# SPDX-License-Identifier: LGPL-2.1-or-later
"""G06 slice 5a: assembly configurations, accuracy, round trip, undo, save and reopen, drift, mutants.

The corpus (configuration_corpus.py) holds cases with closed-form truths. ``evaluate_case`` builds one case
into fresh documents, walks its activation sequence and returns the list of problems it finds; the accuracy,
mutant and probe tests all use it.
"""

import contextlib
import json
import math
import os
import shutil
import tempfile
import unittest
from collections import Counter

import FreeCAD as App

import AssemblyConfiguration as AC
import AssemblyFeature as AF
import Import
import Interference
import Preferences
import ReplaceComponent as RC
from AssemblyTests import configuration_corpus as C
from AssemblyTests import TestAssemblyFeature as TAF

POS_TOL = 1e-6     # mm, brief check 2
ANG_TOL = 1e-9     # rad
VOL_REL = 1e-6


class Tally:
    def __init__(self):
        self.cases = 0
        self.activations = 0
        self.max_pos = 0.0
        self.max_ang = 0.0
        self.placements = 0
        self.suppressed_checked = 0
        self.consumer_checks = Counter()
        self.roundtrips = 0
        self.undo = 0
        self.probes = Counter()


def _reaches(reach, toks):
    """Independent of the module under test: does any path of the joint start with the component's tokens."""
    return any(r[:len(toks)] == toks for r in reach)


def _hit(path, sup):
    return any(path == s or path.startswith(s + ".") for s in sup)


def _pl_err(a, b):
    return (a.Base - b.Base).Length, (a.inverse() * b).Rotation.Angle


def _active_comps(b, cfg):
    sup = cfg.suppressed if cfg else []
    return [c for c in b.comps if not _hit(c.path, sup)]


def expected_keys(b, cfg, default_keys):
    if getattr(b, "existing", False):
        return set(default_keys) - (cfg.removed_keys if cfg else set())
    keys = set()
    for c in _active_comps(b, cfg):
        keys.update(c.keys)
    return keys


def _source_label(c, cfg):
    return (cfg.alt.get(c.path) if cfg else None) or c.source


def bom_truth(b, cfg):
    top, nested = Counter(), {}
    for c in _active_comps(b, cfg):
        if not c.bom:
            continue
        label = _source_label(c, cfg)
        if c.parent is None:
            top[label] += 1
        else:
            nested.setdefault(c.parent, Counter())[label] += 1
    rows = sorted(top.items())
    for parent, cnt in nested.items():
        if not _hit(parent, cfg.suppressed if cfg else []):
            rows += sorted(cnt.items())
    return sorted(rows)


def bom_rows(b):
    bom = b.asm.Document.getObject("BOM")
    if bom is None:
        return None
    rows = []
    for r in range(2, 40):
        name = bom.getContents("B%d" % r).lstrip("'")
        if not name:
            break
        rows.append((name, int(float(bom.getContents("C%d" % r)))))
    return sorted(rows)


def _volume(c, cfg):
    if cfg and c.path in cfg.volume:
        return cfg.volume[c.path]
    return c.box[0] * c.box[1] * c.box[2] if c.box else None


def export_check(b, cfg, tmp):
    """Import.export of the assembly holds exactly the active components: solid count and volume."""
    path = os.path.join(tmp, "out.step")
    Import.export([b.asm], path)
    d = App.newDocument("ExportCheck")
    try:
        Import.insert(path, d.Name)
        solids = [s for o in d.RootObjects if hasattr(o, "Shape") for s in o.Shape.Solids]
    finally:
        App.closeDocument(d.Name)
    exp = [(c, _volume(c, cfg)) for c in _active_comps(b, cfg) if c.box or (cfg and c.path in cfg.volume)]
    want_n = len(exp)
    want_v = sum(v for _c, v in exp)
    got_v = sum(s.Volume for s in solids)
    probs = []
    if len(solids) != want_n:
        probs.append("export has %d solids, want %d" % (len(solids), want_n))
    if abs(got_v - want_v) > max(VOL_REL * want_v, 1e-6):
        probs.append("export volume %.6f, want %.6f" % (got_v, want_v))
    return probs


def analytic_cut(comp, plc, tool_box):
    """Volume of the box comp (at plc, no rotation) inside the tool box ((x0, y0, z0), (x1, y1, z1))."""
    (x0, y0, z0), (x1, y1, z1) = tool_box
    lo = [plc.Base.x, plc.Base.y, plc.Base.z]
    hi = [lo[0] + comp.box[0], lo[1] + comp.box[1], lo[2] + comp.box[2]]
    v = 1.0
    for a, b_, t0, t1 in zip(lo, hi, (x0, y0, z0), (x1, y1, z1)):
        v *= max(0.0, min(b_, t1) - max(a, t0))
    return v


TOOL_BOX = ((10, 10, 11), (60, 30, 23))


def check_state(b, cfg, default_keys, tally, tmp, with_export=True):
    """The brief's check 2 for the state the assembly is in now (cfg is None for the default)."""
    asm, probs = b.asm, []
    doc = asm.Document
    exp_pl = {}
    for o in AC._components(asm):
        exp_pl[o.Name] = (cfg.pl.get(o.Name) if cfg and o.Name in cfg.pl else b.default_pl[o.Name])
    sup = cfg.suppressed if cfg else []
    removed_names = set()
    for path in sup:
        removed_names.add(path.split(".")[-1])
    for o in AC._components(asm):
        if o.Name in removed_names and o.TypeId != "App::LinkElement":
            continue
        pe, ae = _pl_err(o.Placement, exp_pl[o.Name])
        tally.placements += 1
        tally.max_pos, tally.max_ang = max(tally.max_pos, pe), max(tally.max_ang, ae)
        if pe > POS_TOL or ae > ANG_TOL:
            probs.append("%s placement off by %.3g mm %.3g rad" % (o.Name, pe, ae))
    # suppressed set
    if sorted(AC.suppressed(asm)) != sorted(sup):
        probs.append("suppressed set %s, want %s" % (sorted(AC.suppressed(asm)), sorted(sup)))
    tally.suppressed_checked += 1
    for c in b.comps:
        if c.kind == "array" or (_hit(c.path, sup) and c.path not in sup):
            continue
        if c.obj.TypeId == "App::LinkElement":
            gone = bool(c.obj.Suppressed)
        else:
            gone = c.obj.getParentGeoFeatureGroup() is None
        if gone != (c.path in sup):
            probs.append("%s is %s but should be %s" % (c.path, "suppressed" if gone else "present",
                                                         "suppressed" if c.path in sup else "present"))
    # joints: none deleted, those reaching a suppressed component are suppressed
    for name in b.joint_names:
        if doc.getObject(name) is None:
            probs.append("joint %s was deleted" % name)
    for j, owner, grp, grounded in AC._joints(asm):
        if grounded:
            reach = AC._joint_reach(j, owner, grounded)
            if any(_reaches(reach, c.path.split(".")) for c in b.comps if c.path in sup):
                probs.append("grounded joint %s still grounds a suppressed component" % j.Name)
            continue
        reach = AC._joint_reach(j, owner, grounded)
        touching = any(_reaches(reach, c.path.split(".")) for c in b.comps if _hit(c.path, sup)
                       and c.path in sup)
        if touching and not j.Suppressed:
            probs.append("joint %s reaches a suppressed component and is not suppressed" % j.Name)
    # alternative parts
    if cfg and cfg.alt:
        for path, part in cfg.alt.items():
            link = doc.getObject(path)
            if link.LinkedObject.Name != part:
                probs.append("%s links to %s, want %s" % (path, link.LinkedObject.Name, part))
        if cfg.joint_outcome:
            for jn in b.alt_joints:
                j = doc.getObject(jn)
                want = cfg.joint_outcome == "suppressed"
                if bool(j.Suppressed) != want:
                    probs.append("joint %s Suppressed=%s after the alternative part (%s)" % (jn, j.Suppressed,
                                                                                            cfg.joint_outcome))
    # consumers
    keys = {i.key for i in Interference.resolve(asm).insts}
    if keys != expected_keys(b, cfg, default_keys):
        probs.append("Interference.check sees %s, want %s" % (sorted(keys), sorted(expected_keys(b, cfg, default_keys))))
    tally.consumer_checks["interference"] += 1
    if not getattr(b, "existing", False):
        got = bom_rows(b)
        if got is not None and got != bom_truth(b, cfg):
            probs.append("BOM rows %s, want %s" % (got, bom_truth(b, cfg)))
        tally.consumer_checks["bom"] += 1
        if with_export:
            probs += export_check(b, cfg, tmp)
            tally.consumer_checks["export"] += 1
    if b.features:
        dflt = b.default_cut
        want = (cfg.cut if cfg and cfg.cut is not None else dflt)
        for fname in b.features:
            f = doc.getObject(fname)
            rep = AF.affected(f)
            got = {c["path"] if "path" in c else c["key"] for c in rep["components"]}
            exp = set(want[fname]) if not isinstance(want[fname], set) else want[fname]
            if got != exp:
                probs.append("%s cuts %s, want %s" % (fname, sorted(got), sorted(exp)))
            comp_by = {c.path: c for c in b.comps}
            for rec in rep["components"]:
                p = rec["path"] if "path" in rec else rec["key"]
                c = comp_by[p]
                plc = exp_pl[c.obj.Name]
                want_v = analytic_cut(c, plc, TOOL_BOX)
                if abs(rec["removed_mm3"] - want_v) > max(VOL_REL * want_v, 1e-9):
                    probs.append("%s removed %.6f from %s, want %.6f" % (fname, rec["removed_mm3"], p, want_v))
            # effective_shape of a suppressed component does not exist
            for c in b.comps:
                if c.box and _hit(c.path, sup) and AF.effective_shape(asm, c.path) is not None:
                    probs.append("effective_shape still returns %s" % c.path)
            tally.consumer_checks["features"] += 1
    return probs


def _joint_names(asm):
    return sorted(j.Name for j, _o, _g, _gr in AC._joints(asm))


# --------------------------------------------------------------- evaluation

def evaluate_case(name, tally=None, with_export=True, with_probes=True):
    tally = tally or Tally()
    tmp = tempfile.mkdtemp(prefix="cfgtest")
    doc = App.newDocument("Cfg_" + name.replace(".", "_")[:20])
    docs = [doc]
    problems = []
    try:
        b = C.build_case(doc, name)
        if b.asm.Document != doc:        # the case replaced the document (example file)
            doc = b.asm.Document
            docs = [doc]
        docs += list(b.parts_docs)
        tally.cases += 1
        b.joint_names = _joint_names(b.asm)
        b.alt_joints = [j for j in b.joint_names if j in ("JM", "JB")]
        default_keys = {i.key for i in Interference.resolve(b.asm).insts}
        sig0 = AC.signature(doc)
        for cfg in b.cfgs.values():
            AC.create(b.asm, cfg.name, **cfg.create)
        probs = check_state(b, None, default_keys, tally, tmp, with_export)
        problems += ["default: " + p for p in probs]
        # the consumers (Boolean checks, STEP export) may raise shape tolerances in place; the part documents
        # are compared across each activation itself, before the consumers run
        part_before = [(d.Name, TAF.saved_signature(d)) for d in b.parts_docs]
        seen = {}
        for step in b.sequence:
            cfg = b.cfgs[step] if step else None
            AC.activate(b.asm, step)
            tally.activations += 1
            for (dn, before), pd in zip(part_before, b.parts_docs):
                if TAF.saved_signature(pd) != before:
                    problems.append("%s: part document %s changed by the activation" % (step, dn))
            probs = check_state(b, cfg, default_keys, tally, tmp, with_export)
            part_before = [(d.Name, TAF.saved_signature(d)) for d in b.parts_docs]
            problems += ["%s: %s" % (step or "default", p) for p in probs]
            sig = AC.full_signature(doc)
            if step is None:
                if AC.signature(doc) != sig0:
                    problems.append("default after %s: signature differs from before any configuration" % ",".join(
                        str(s) for s in seen))
                tally.roundtrips += 1
            elif step in seen:
                if sig != seen[step]:
                    problems.append("%s again: signature differs from the first activation" % step)
                tally.roundtrips += 1
            else:
                seen[step] = sig
            st = AC.status(b.asm)
            if st["drift"]:
                problems.append("%s: drift after activation: %s" % (step, st["drift"]))
        if with_probes:
            problems += probes(b, tally, tmp)
        # the source part objects never changed (shape, properties) relative to the default signature
        AC.activate(b.asm, None)
        if AC.signature(doc) != sig0:
            problems.append("final default differs from the signature before any configuration")
    finally:
        for d in docs:
            try:
                App.closeDocument(d.Name)
            except Exception:
                pass
        shutil.rmtree(tmp, ignore_errors=True)
        if hasattr(locals().get("b", None), "tmpdir"):
            shutil.rmtree(b.tmpdir, ignore_errors=True)
    return problems, tally


def probes(b, tally, tmp):
    """Undo and redo, drift and a user edit that survives a switch, on this case."""
    problems, doc, asm = [], b.asm.Document, b.asm
    first = b.sequence[0]
    AC.activate(asm, None)
    before = AC.full_signature(doc)
    AC.activate(asm, first)
    after = AC.full_signature(doc)
    doc.undo()
    if AC.full_signature(doc) != before:
        problems.append("undo of the activation of %s does not restore the signature" % first)
    if AC.status(asm)["active"] is not None:
        problems.append("undo of the activation leaves %s active" % AC.status(asm)["active"])
    doc.redo()
    if AC.full_signature(doc) != after:
        problems.append("redo of the activation of %s differs" % first)
    tally.undo += 1
    # A -> B undo restores A exactly
    others = [s for s in b.cfgs if s != first]
    if others:
        sig_a = AC.full_signature(doc)
        AC.activate(asm, others[0])
        doc.undo()
        if AC.full_signature(doc) != sig_a:
            problems.append("undo of %s -> %s does not restore %s exactly" % (first, others[0], first))
        tally.undo += 1
    # drift
    cfg = b.cfgs[first]
    ov = cfg.create
    if ov.get("joints"):
        jn = sorted(ov["joints"])[0]
        prop = sorted(ov["joints"][jn])[0]
        j = doc.getObject(jn)
        old = getattr(j, prop)
        if prop.startswith("Offset"):
            j.Offset1 = App.Placement(old.Base + App.Vector(0, 0, 1), old.Rotation)
        else:
            setattr(j, prop, float(getattr(old, "Value", old)) + 1.0)
        st = AC.status(asm)
        if not [d for d in st["drift"] if d["target"] == jn and d["property"] == prop]:
            problems.append("an edit of %s.%s while %s is active is not reported as drift" % (jn, prop, first))
        sig_edit = AC.full_signature(doc)
        try:
            AC.activate(asm, None)
            problems.append("switching away with drift was not refused")
        except AC.ConfigurationDriftError:
            pass
        if AC.full_signature(doc) != sig_edit:
            problems.append("a refused switch changed the document")
        AC.discard(asm)
        if AC.status(asm)["drift"]:
            problems.append("discard left drift")
        if AC.full_signature(doc) != after:
            problems.append("discard did not restore the configuration's state")
        setattr(j, prop, old) if not prop.startswith("Offset") else None
        # capture: edit again, capture, switch, come back
        if prop.startswith("Offset"):
            j.Offset1 = App.Placement(old.Base + App.Vector(0, 0, 1), old.Rotation)
        else:
            setattr(j, prop, float(getattr(old, "Value", old)) + 2.0)
        AC.capture(asm)
        if AC.status(asm)["drift"]:
            problems.append("capture left drift")
        captured = _no_placements(AC.full_signature(doc))
        pl_captured = {o.Name: App.Placement(o.Placement) for o in AC._components(asm)}
        AC.activate(asm, None)
        AC.activate(asm, first)
        if _no_placements(AC.full_signature(doc)) != captured:
            problems.append("captured value is not what the configuration writes after a switch")
        for o in AC._components(asm):
            pe, ae = _pl_err(o.Placement, pl_captured[o.Name])
            if pe > POS_TOL or ae > 1e-7:
                problems.append("captured configuration places %s %.3g mm off after a switch" % (o.Name, pe))
        tally.probes["drift"] += 1
    return problems


def _no_placements(sig):
    """The signature without placement values and the stored JSON of configurations (a capture is compared to
    a fresh solve, which may differ in the last digits)."""
    return {n: dict(rec, props={p: v for p, v in rec["props"].items()
                                if not (isinstance(v, str) and v.startswith("P(")) and p not in ("Defaults", "Applied")})
            for n, rec in sig.items()}


def check_cases(names, **kw):
    tally = Tally()
    failing = {}
    for name in names:
        try:
            probs, _ = evaluate_case(name, tally, **kw)
        except Exception as exc:
            probs = ["crash: %r" % exc]
        if probs:
            failing[name] = probs
    return failing, tally


# ------------------------------------------------------------------ mutants

@contextlib.contextmanager
def patched(**fns):
    saved = {k: getattr(AC, k) for k in fns}
    try:
        for k, v in fns.items():
            setattr(AC, k, v)
        yield
    finally:
        for k, v in saved.items():
            setattr(AC, k, v)


def _wrap_restore(edit):
    real = AC._restore

    def restore(assembly, cfg):
        if cfg.Defaults:
            d = json.loads(cfg.Defaults)
            edit(assembly, cfg, d)
            cfg.Defaults = json.dumps(d, sort_keys=True)
        return real(assembly, cfg)
    return restore


def _m_no_joint_suppress():
    return dict(_suppress_joint=lambda state, j, reason: None)


def _m_hide_only():
    def remove(state, comp):
        state.defaults["components"][comp.path] = {"kind": "group", "container": comp.container.Name,
                                                   "name": comp.obj.Name,
                                                   "index": list(comp.container.Group).index(comp.obj)}
        comp.obj.Visibility = False
    return dict(_remove_from_group=remove)


def _m_no_consumer_refresh():
    return dict(_refresh_consumers=lambda assembly: None)


def _m_grounded_kept():
    real = AC._touches
    return dict(_touches=lambda reach, toks: real(reach, toks) and not (len(reach) == 1))


def _m_backwards():
    def edit(assembly, cfg, d):
        doc = assembly.Document
        for lname, rec in list(d["links"].items()):
            link = doc.getObject(lname)
            old = doc.getObject(rec["linked"])
            try:
                rep, ctx = RC._plan(assembly, link, old)
                for rf in ctx["refs"]:
                    if rf.status == "matched":
                        RC._apply_ref(rf, {}, ctx["new_tokens"])
                link.LinkedObject = ctx["new_top"]
            except Exception:
                pass
            d["links"].pop(lname)
            d["placements"].pop(lname, None)   # the matcher cannot give the old placement back verbatim
        for jname in list(d["joints"]):
            if jname == "JM":   # the matcher run backwards neither un-suppresses a joint nor gives a reference back
                d["joints"].pop(jname)
    return dict(_restore=_wrap_restore(edit))


def _m_unmatched_picked():
    real = RC._plan

    def plan(assembly, old, new, *a, **k):
        rep, ctx = real(assembly, old, new, *a, **k)
        for rf in ctx["refs"]:
            for s in rf.result:
                if s["status"] != "matched":
                    s["new_element"] = (s["candidates"][0]["element"] if s["candidates"] else s["old_element"])
                    s["status"] = "matched"
            rf.status = "matched"
        return rep, ctx
    return dict(_rc_plan=plan)


def _m_joint_deleted():
    def sup(state, j, reason):
        j.Document.removeObject(j.Name)
    return dict(_suppress_joint=sup)


def _m_no_resolve():
    return dict(_solve=lambda assembly: 0)


def _m_drift_ignored():
    return dict(_drift=lambda assembly, cfg: [])


def _m_placements_not_restored():
    return dict(_restore=_wrap_restore(lambda a, c, d: d.__setitem__("placements", {})))


def _m_group_index_lost():
    real = AC._remove_from_group

    def remove(state, comp):
        real(state, comp)
        state.defaults["components"][comp.path]["index"] = 10 ** 6
    return dict(_remove_from_group=remove)


def _m_feature_not_restored():
    return dict(_restore=_wrap_restore(lambda a, c, d: d.__setitem__("features", {})))


def _m_element_not_restored():
    def edit(a, c, d):
        d["components"] = {k: v for k, v in d["components"].items() if v["kind"] != "element"}
    return dict(_restore=_wrap_restore(edit))


def _m_user_edit_lost():
    def edit(a, c, d):
        ap = json.loads(c.Applied)
        ap["placements"] = {}
        c.Applied = json.dumps(ap)
    return dict(_restore=_wrap_restore(edit))


def _m_expression_allowed():
    return dict(_refuse_expression=lambda obj, prop: None)


def _m_joint_values_not_restored():
    def edit(a, c, d):
        for rec in d["joints"].values():
            for k in ("Distance", "Distance2", "Angle", "Offset1", "Offset2"):
                rec.pop(k, None)
    return dict(_restore=_wrap_restore(edit))


def _m_joint_suppression_not_restored():
    def edit(a, c, d):
        for rec in d["joints"].values():
            rec.pop("Suppressed", None)
    return dict(_restore=_wrap_restore(edit))


def _m_linked_object_not_restored():
    return dict(_restore=_wrap_restore(lambda a, c, d: d.__setitem__("links", {})))


def _m_delete_active_orphans():
    return dict(_on_delete=lambda cfg: None)


def _m_restore_crashes_on_deleted():
    real = AC._restore

    def restore(assembly, cfg):
        if cfg.Defaults:
            for rec in json.loads(cfg.Defaults)["components"].values():
                if assembly.Document.getObject(rec["name"]) is None:
                    raise ValueError("PropertyLinkList: invalid document object")
        return real(assembly, cfg)
    return dict(_restore=restore)


def _m_joint_snapshot_everything():
    real = AC._snapshot_joint
    return dict(_snapshot_joint=lambda state, j, grp, grounded, props=(): real(
        state, j, grp, grounded, AC._JOINT_SNAPSHOT_PROPS))


def _m_suppressed_placement_not_tracked():
    return dict(_placement_map=lambda assembly, defaults: {
        o.Name: AC._pl_json(o.Placement, exact=False) for o in AC._components(assembly)})


def _m_suppressed_stays_visible():
    real = AC._remove_from_group

    def remove(state, comp):
        real(state, comp)
        comp.obj.Visibility = True
    return dict(_remove_from_group=remove)


def _m_proxy_required():
    return dict(_is_config=lambda obj: isinstance(getattr(obj, "Proxy", None), AC.AssemblyConfiguration))


def _m_validate_against_live_group():
    return dict(_default_group=lambda assembly, container: list(container.Group))


MUTANTS = {
    "delete_active_leaves_components_suppressed": _m_delete_active_orphans,
    "restore_crashes_on_deleted_object": _m_restore_crashes_on_deleted,
    "restore_reverts_whole_joint": _m_joint_snapshot_everything,
    "suppressed_placement_not_tracked": _m_suppressed_placement_not_tracked,
    "suppressed_component_stays_visible": _m_suppressed_stays_visible,
    "configuration_needs_proxy": _m_proxy_required,
    "copy_validates_live_group": _m_validate_against_live_group,
    "solve_joints_of_suppressed_kept": _m_no_joint_suppress,
    "suppress_by_hiding_only": _m_hide_only,
    "bom_and_features_not_refreshed": _m_no_consumer_refresh,
    "grounded_joint_of_suppressed_kept": _m_grounded_kept,
    "restore_by_matcher_backwards": _m_backwards,
    "unmatched_reference_picked": _m_unmatched_picked,
    "joint_deleted_instead_of_suppressed": _m_joint_deleted,
    "joint_values_without_resolve": _m_no_resolve,
    "drift_dropped_on_switch": _m_drift_ignored,
    "placements_not_restored": _m_placements_not_restored,
    "group_index_lost_on_restore": _m_group_index_lost,
    "feature_suppression_not_restored": _m_feature_not_restored,
    "array_element_flag_not_restored": _m_element_not_restored,
    "user_edit_overwritten_on_switch": _m_user_edit_lost,
    "expression_override_allowed": _m_expression_allowed,
    "joint_values_not_restored": _m_joint_values_not_restored,
    "joint_suppression_not_restored": _m_joint_suppression_not_restored,
    "alternative_part_not_restored": _m_linked_object_not_restored,
}


@contextlib.contextmanager
def mutated(name):
    spec = MUTANTS[name]()
    rc_plan = spec.pop("_rc_plan", None)
    saved_plan = RC._plan
    try:
        if rc_plan is not None:
            RC._plan = rc_plan
        with patched(**spec):
            yield
    finally:
        RC._plan = saved_plan


# the cases that exercise the extra probes of this suite (expression, user edit)
def expression_problems():
    doc = App.newDocument("CfgExpr")
    try:
        b = C.build_case(doc, "slider_d25")
        j = doc.getObject("Dist")
        j.setExpression("Distance", "40 mm")
        try:
            AC.create(b.asm, "Bad", joints={"Dist": {"Distance": 25.0}})
        except AC.ConfigurationError as exc:
            if "40 mm" not in str(exc) or "expression" not in str(exc):
                return ["the refusal does not name the expression: %s" % exc]
            return []
        return ["an override of an expression-driven property was accepted"]
    finally:
        App.closeDocument(doc.Name)


def user_edit_problems():
    """Edits to properties no configuration overrides survive switches: a label, a joint's label and the
    placement of a component no joint reaches (the solver rewrites the placement of a jointed one at every
    recompute, so a manual edit of it is not a durable edit in FreeCAD)."""
    doc = App.newDocument("CfgEdit")
    problems = []
    try:
        b = C.build_case(doc, "three_configs")
        free = b.asm.newObject("Part::Box", "Free")
        doc.recompute()
        for cfg in b.cfgs.values():
            AC.create(b.asm, cfg.name, **cfg.create)
        AC.activate(b.asm, "Slide")
        moved = App.Placement(App.Vector(7, 8, 9), App.Rotation(App.Vector(0, 0, 1), 30))
        free.Placement = moved
        doc.getObject("LA").Label = "renamed by the user"
        doc.getObject("JA").Label = "joint renamed"
        for step in (None, "NoB", "NoAD", "Slide", None):
            AC.activate(b.asm, step)
            if not free.Placement.isSame(moved, 1e-9):
                problems.append("the placement of an unjointed component the user moved was lost at %s" % step)
            if doc.getObject("LA").Label != "renamed by the user" or doc.getObject("JA").Label != "joint renamed":
                problems.append("a label edit was lost at %s" % step)
    finally:
        App.closeDocument(doc.Name)
    return problems


# ------------------------------------------------------------------ repair 1 scenarios

def _fresh(name):
    doc = App.newDocument(name)
    b = C.rig(doc)
    doc.recompute()
    return doc, b.asm


def _plc(x, y, z):
    return App.Placement(App.Vector(x, y, z), App.Rotation())


def delete_active_problems():
    """Deleting the active configuration puts the assembly back to its defaults (finding 1)."""
    doc, asm = _fresh("RepDelete")
    problems = []
    try:
        sig0 = AC.signature(doc)
        AC.create(asm, "S", suppress=["LB", "LA"])
        AC.activate(asm, "S")
        doc.removeObject(AC.active(asm).Name)
        if AC.signature(doc) != sig0:
            problems.append("deleting the active configuration did not restore the default signature")
        if doc.LB not in asm.Group or doc.LA not in asm.Group or doc.JB.Suppressed:
            problems.append("a suppressed component or joint stayed suppressed after its configuration was deleted")
        if not doc.LB.Visibility:
            problems.append("a restored component stayed hidden")
    finally:
        App.closeDocument(doc.Name)
    return problems


def deleted_object_problems():
    """A suppressed object the user deleted while its configuration was active: the restore finishes."""
    doc, asm = _fresh("RepGone")
    problems = []
    try:
        AC.create(asm, "S", suppress=["LB", "LA"])
        AC.activate(asm, "S")
        doc.removeObject("LB")
        try:
            rep = AC.activate(asm, None)
        except Exception as exc:
            return ["activate(None) raised %s: %s" % (type(exc).__name__, exc)]
        json.dumps(rep)
        if AC.status(asm)["active"] is not None:
            problems.append("the assembly is stuck in the configuration")
        if doc.LA not in asm.Group or doc.JA.Suppressed:
            problems.append("the other suppressed component was not restored")
        if not any(sk["name"] == "LB" for sk in rep.get("restore_skipped", [])):
            problems.append("the deleted object is not reported: %s" % rep.get("restore_skipped"))
    finally:
        App.closeDocument(doc.Name)
    return problems


def user_edit_kept_problems():
    """Edits to properties a configuration does not override survive a switch (finding 3)."""
    doc, asm = _fresh("RepKeep")
    problems = []
    try:
        C.src_box(doc, "Alt", 10, 10, 12)
        fl = asm.newObject("App::Link", "FL")      # no joint reaches it: nothing but the user places it
        fl.LinkedObject = doc.CubeSrc
        fl.Placement = _plc(60, 60, 30)
        doc.recompute()
        AC.create(asm, "S", suppress=["LB", "FL"], joints={"JCd": {"Distance": 70.0}})
        AC.create(asm, "Al", alternatives={"FL": "Alt"})
        AC.activate(asm, "S")
        jb_pl = _plc(9, 9, 9)
        doc.JB.Placement1 = jb_pl
        ref = (doc.GL, ["Face2"])
        doc.JB.Reference1 = ref
        fl_pl = _plc(3, 4, 5)
        fl.Placement = fl_pl
        doc.JCd.Placement1 = _plc(1, 2, 3)
        doc.GL.Placement = _plc(0, 0, -1)
        if AC.status(asm)["drift"]:
            problems.append("an edit of a property no configuration overrides is reported as drift: %s"
                            % AC.status(asm)["drift"])
        AC.activate(asm, None)
        if not fl.Placement.isSame(fl_pl, 1e-9):
            problems.append("the Placement of a suppressed component was reverted")
        AC.activate(asm, "S")
        fl.Placement = _plc(8, 8, 8)
        AC.activate(asm, "Al")
        la_pl = _plc(11, 12, 13)
        fl.Placement = la_pl
        AC.activate(asm, None)
        if not doc.JB.Placement1.isSame(jb_pl, 1e-9):
            problems.append("Placement1 of a joint a configuration suppressed was reverted")
        if doc.JB.Reference1[1] != ["Face2"]:
            problems.append("Reference1 of a joint a configuration suppressed was reverted")
        if not fl.Placement.isSame(la_pl, 1e-9):
            problems.append("the Placement of an alternative component was reverted")
        if not doc.JCd.Placement1.isSame(_plc(1, 2, 3), 1e-9):
            problems.append("Placement1 of a joint whose only override is Distance was reverted")
        if not doc.GL.Placement.isSame(_plc(0, 0, -1), 1e-9):
            problems.append("the Placement of a grounded component was reverted")
    finally:
        App.closeDocument(doc.Name)
    return problems


def remap_edit_drift_problems():
    """A joint reference the alternative-part remap rewrote and the user then edited is drift."""
    doc, asm = _fresh("RepRemap")
    problems = []
    try:
        C.src_box(doc, "Alt", 10, 10, 12)
        doc.recompute()
        AC.create(asm, "Al", alternatives={"LA": "Alt"})
        AC.activate(asm, "Al")
        refs = json.loads(AC.active(asm).Applied)["joint_refs"]
        if not refs:
            return ["the remap recorded no joint references"]
        jname = sorted(refs)[0]
        prop = sorted(p for p in refs[jname] if p.startswith("Placement"))[0]
        setattr(getattr(doc, jname), prop, _plc(5, 6, 7))
        drift = AC.status(asm)["drift"]
        if not [d for d in drift if d["target"] == jname and d["property"] == prop]:
            problems.append("an edit of %s.%s, rewritten by the remap, is not drift: %s" % (jname, prop, drift))
        try:
            AC.activate(asm, None)
            problems.append("switching away from an edited remapped reference was not refused")
        except AC.ConfigurationDriftError:
            pass
        AC.discard(asm)
        if AC.status(asm)["drift"]:
            problems.append("discard left drift on the remapped reference")
        AC.activate(asm, None)
    finally:
        App.closeDocument(doc.Name)
    return problems


def lost_proxy_problems():
    """Configuration objects are recognised by their properties when the proxy is gone (finding 4)."""
    doc, asm = _fresh("RepProxy")
    problems = []
    try:
        AC.create(asm, "S", suppress=["LB"])
        AC.create(asm, "T", joints={"JCd": {"Distance": 70.0}})
        AC.activate(asm, "S")
        for o in list(asm.Group):
            if o.TypeId == "App::FeaturePython":
                o.Proxy = None
        if AC.status(asm)["configurations"] != ["S", "T"] or AC.status(asm)["active"] != "S":
            problems.append("configurations without a proxy are not recognised: %s" % AC.status(asm))
        AC.activate(asm, "T")
        if doc.LB not in asm.Group:
            problems.append("activation through a recovered configuration did not restore the defaults")
        if not isinstance(AC.active(asm).Proxy, AC.AssemblyConfiguration):
            problems.append("the proxy was not re-attached")
    finally:
        App.closeDocument(doc.Name)
    return problems


def copy_active_problems():
    doc, asm = _fresh("RepCopy")
    problems = []
    try:
        AC.create(asm, "S", suppress=["LB"])
        AC.activate(asm, "S")
        try:
            AC.copy(asm, "S", "S2")
        except AC.ConfigurationError as exc:
            return ["copy of the active configuration failed: %s" % exc]
        cp = [c for c in AC.configurations(asm) if c.Label == "S2"][0]
        if json.loads(cp.Overrides)["suppress"] != ["LB"]:
            problems.append("the copy lost the suppression")
        AC.activate(asm, "S2")
        if AC.suppressed(asm) != ["LB"]:
            problems.append("the copy does not suppress LB")
    finally:
        App.closeDocument(doc.Name)
    return problems


def visibility_problems():
    """A suppressed component is hidden; its Visibility comes back verbatim (finding 6)."""
    doc, asm = _fresh("RepVis")
    problems = []
    try:
        doc.LA.Visibility = False
        AC.create(asm, "S", suppress=["LA", "LB"])
        AC.activate(asm, "S")
        if doc.LB.Visibility or doc.LA.Visibility:
            problems.append("a suppressed component is still visible")
        AC.activate(asm, None)
        if not doc.LB.Visibility:
            problems.append("a visible component stayed hidden after the restore")
        if doc.LA.Visibility:
            problems.append("a hidden component became visible after the restore")
    finally:
        App.closeDocument(doc.Name)
    return problems


def expression_after_problems():
    """An expression added to an overridden property after the configuration was made (finding 7)."""
    doc, asm = _fresh("RepExpr")
    problems = []
    try:
        AC.create(asm, "T", joints={"JCd": {"Distance": 70.0}})
        doc.JCd.setExpression("Distance", "40 mm + 5 mm")
        sig = AC.full_signature(doc)
        try:
            AC.activate(asm, "T")
            problems.append("activation with an expression on an overridden property was accepted")
        except AC.ConfigurationError as exc:
            if "40 mm + 5 mm" not in str(exc):
                problems.append("the refusal does not name the expression: %s" % exc)
        if AC.full_signature(doc) != sig or AC.active(asm) is not None:
            problems.append("a refused activation changed the document")
        doc.JCd.setExpression("Distance", None)
        AC.activate(asm, "T")
        doc.JCd.setExpression("Distance", "70 mm")
        if not [d for d in AC.status(asm)["drift"] if d["kind"] == "expression"]:
            problems.append("an expression set while active is not reported as drift")
        try:
            AC.activate(asm, None)
            problems.append("switching away with an expression drift was accepted")
        except AC.ConfigurationDriftError:
            pass
        AC.discard(asm)
        if AC.status(asm)["drift"] or doc.JCd.ExpressionEngine:
            problems.append("discard did not clear the expression")
        AC.activate(asm, None)
    finally:
        App.closeDocument(doc.Name)
    return problems


def stale_active_problems():
    doc, asm = _fresh("RepStale")
    problems = []
    try:
        AC.create(asm, "S", suppress=["LB"])
        AC.create(asm, "T", suppress=["LA"])
        AC.activate(asm, "S")
        t = [c for c in AC.configurations(asm) if c.Label == "T"][0]
        t.Active = True
        st = AC.status(asm)
        if st["active"] != "S" or not st["warnings"]:
            problems.append("a hand-set Active flag is not reported: %s" % st)
        AC.activate(asm, None)
        if t.Active or AC.status(asm)["warnings"]:
            problems.append("the stale Active flag survived a switch")
    finally:
        App.closeDocument(doc.Name)
    return problems


def lost_feature_proxy_problems():
    """Assembly features are recognised by their properties when the proxy is gone (a stable re-save drops it)."""
    import AssemblyFeature as AF
    doc, asm = _fresh("RepFeatProxy")
    problems = []
    try:
        C.rig_feature(C.Built(doc, asm))
        doc.recompute()
        cut = AF.effective_shape(asm, "LA").Volume
        AC.create(asm, "NoCut", features={"Cut1": True})
        AC.create(asm, "S", suppress=["LB"])
        for o in list(asm.Group):
            if o.TypeId == "App::FeaturePython" and "Spec" in o.PropertiesList:
                o.Proxy = None
        if [f.Name for f in AF.features(asm)] != ["Cut1"]:
            return ["a feature without a proxy is not recognised: %s" % [f.Name for f in AF.features(asm)]]
        if not isinstance(doc.Cut1.Proxy, AF.AssemblyFeature):
            problems.append("the feature proxy was not re-attached")
        doc.recompute()
        if abs(AF.effective_shape(asm, "LA").Volume - cut) > 1e-6:
            problems.append("the recovered feature no longer cuts")
        AC.activate(asm, "NoCut")
        if abs(AF.effective_shape(asm, "LA").Volume - 1000.0) > 1e-6:
            problems.append("a configuration did not suppress the recovered feature")
        AC.activate(asm, "S")
        if doc.Cut1.Suppressed or abs(AF.effective_shape(asm, "LA").Volume - cut) > 1e-6:
            problems.append("switching away did not restore the recovered feature")
    except Exception as exc:
        problems.append("%s: %s" % (type(exc).__name__, exc))
    finally:
        App.closeDocument(doc.Name)
    return problems


REPAIR_PROBES = (delete_active_problems, deleted_object_problems, user_edit_kept_problems,
                 remap_edit_drift_problems, lost_proxy_problems, copy_active_problems, visibility_problems,
                 expression_after_problems, stale_active_problems, lost_feature_proxy_problems)


def save_reopen_problems(name):
    tmp = tempfile.mkdtemp(prefix="cfgsave")
    problems = []
    doc = App.newDocument("CfgSave")
    try:
        b = C.build_case(doc, name)
        sig0 = AC.signature(doc)
        for cfg in b.cfgs.values():
            AC.create(b.asm, cfg.name, **cfg.create)
        first = b.sequence[0]
        AC.activate(b.asm, first)
        sig_active = AC.full_signature(doc)
        names = AC.status(b.asm)["configurations"]
        path = os.path.join(tmp, "asm.FCStd")
        doc.saveAs(path)
        App.closeDocument(doc.Name)
        doc2 = App.openDocument(path)
        asm2 = [o for o in doc2.Objects if o.TypeId == "Assembly::AssemblyObject" and o.getParentGeoFeatureGroup() is None][0]
        st = AC.status(asm2)
        if st["configurations"] != names or st["active"] != first:
            problems.append("reopen: configurations %s active %s, want %s active %s" % (
                st["configurations"], st["active"], names, first))
        if AC.full_signature(doc2) != sig_active:
            problems.append("reopen: signature differs from the saved one")
        AC.activate(asm2, None)
        if AC.signature(doc2) != sig0:
            problems.append("reopen: the default does not restore the pre-configuration signature")
        AC.activate(asm2, first)
        if AC.full_signature(doc2) != sig_active:
            problems.append("reopen: activating again gives a different signature than before saving")
        App.closeDocument(doc2.Name)
        doc = None
    finally:
        if doc is not None:
            try:
                App.closeDocument(doc.Name)
            except Exception:
                pass
        shutil.rmtree(tmp, ignore_errors=True)
    return problems


def drift_problems():
    doc = App.newDocument("CfgDrift")
    problems = []
    try:
        b = C.build_case(doc, "slider_d25")
        AC.create(b.asm, "D25", **b.cfgs["D25"].create)
        AC.activate(b.asm, "D25")
        p = doc.getObject("P")
        j = doc.getObject("Dist")
        j.Distance = 40.0
        st = AC.status(b.asm)
        if len(st["drift"]) != 1 or st["drift"][0]["property"] != "Distance":
            problems.append("drift not reported: %s" % st["drift"])
        try:
            AC.activate(b.asm, None)
            problems.append("a switch with drift was accepted")
        except AC.ConfigurationDriftError:
            pass
        AC.capture(b.asm)
        AC.activate(b.asm, None)
        if abs(p.Placement.Base.x - 60.0) > POS_TOL:
            problems.append("default after capture: P at %g" % p.Placement.Base.x)
        AC.activate(b.asm, "D25")
        if abs(p.Placement.Base.x - 40.0) > POS_TOL:
            problems.append("captured configuration puts P at %g, want 40" % p.Placement.Base.x)
        j.Distance = 55.0
        AC.discard(b.asm)
        if abs(AC._num(j.Distance) - 40.0) > 1e-9 or abs(p.Placement.Base.x - 40.0) > POS_TOL:
            problems.append("discard did not write the configuration back")
        AC.activate(b.asm, None)
    finally:
        App.closeDocument(doc.Name)
    return problems


# -------------------------------------------------------------------- tests

class TestConfigurations(unittest.TestCase):

    def setUp(self):
        self.pref = Preferences.preferences()
        self._old = self.pref.GetBool("SolveInJointCreation", True)
        self.pref.SetBool("SolveInJointCreation", False)

    def tearDown(self):
        self.pref.SetBool("SolveInJointCreation", self._old)

    def test_corpus_shape(self):
        counts = Counter(k for k, _f in C.CASES.values())
        print("\n  configuration corpus: %d cases %s" % (len(C.CASES), dict(counts)))
        self.assertGreaterEqual(len(C.CASES), 30)
        for klass in ("joint_value", "suppress", "alternative", "feature", "link", "combined", "workbench_tests"):
            self.assertIn(klass, counts)
        self.assertGreaterEqual(counts["workbench_tests"], 3)

    def test_accuracy_all_cases(self):
        tally = Tally()
        failures = {}
        for name in C.CASES:
            with self.subTest(case=name):
                try:
                    problems, _ = evaluate_case(name, tally)
                except Exception as exc:
                    import traceback
                    problems = ["crash: %s" % traceback.format_exc()]
                if problems:
                    failures[name] = problems
                self.assertEqual(problems, [], name)
        print("\n  %d cases, %d activations: worst placement error %.3g mm and %.3g rad over %d placements; "
              "suppressed sets checked %d; consumers %s; round trips %d; undo checks %d; drift probes %d; "
              "failures %d" % (tally.cases, tally.activations, tally.max_pos, tally.max_ang, tally.placements,
                               tally.suppressed_checked, dict(tally.consumer_checks), tally.roundtrips,
                               tally.undo, tally.probes["drift"], len(failures)))

    def test_expression_driven_override_refused(self):
        self.assertEqual(expression_problems(), [])

    def test_user_edits_survive_switches(self):
        self.assertEqual(user_edit_problems(), [])

    def test_drift_capture_and_discard(self):
        self.assertEqual(drift_problems(), [])

    def test_save_and_reopen(self):
        for name in ("suppress_LA", "alt_resized", "slider_d25", "combined_unused"):
            if name not in C.CASES:
                continue
            with self.subTest(case=name):
                self.assertEqual(save_reopen_problems(name), [], name)
        self.assertEqual(save_reopen_problems("three_configs"), [])
        self.assertEqual(save_reopen_problems("feature_and_component"), [])

    def test_unknown_targets_refused(self):
        doc = App.newDocument("CfgBad")
        try:
            b = C.build_case(doc, "suppress_LA")
            for kw in (dict(suppress=["Nope"]), dict(joints={"Nope": {"Distance": 1.0}}),
                       dict(joints={"JA": {"Colour": 1.0}}), dict(features={"Nope": True}),
                       dict(alternatives={"LA": "Nope"})):
                with self.assertRaises(AC.ConfigurationError):
                    AC.create(b.asm, "x", **kw)
            with self.assertRaises(AC.ConfigurationError):
                AC.activate(b.asm, "missing")
            AC.create(b.asm, "ok", suppress=["LA"])
            with self.assertRaises(AC.ConfigurationError):
                AC.create(b.asm, "ok", suppress=["LB"])
            json.dumps(AC.activate(b.asm, "ok"))
            json.dumps(AC.status(b.asm))
        finally:
            App.closeDocument(doc.Name)

    def test_names_are_checked_the_same_by_create_copy_and_rename(self):
        doc = App.newDocument("CfgNames")
        try:
            b = C.build_case(doc, "suppress_LA")
            AC.create(b.asm, "ok", suppress=["LA"])
            before = [c.Label for c in AC.configurations(b.asm)]
            bad = ("", "   ", "\t", "Default", "default", " DEFAULT ", None, 7,
                   "ok", " ok ", "LB")      # empty, reserved, not text, taken by a configuration, by another object
            for name in bad:
                with self.subTest(name=name):
                    with self.assertRaises(AC.ConfigurationError):
                        AC.create(b.asm, name)
                    with self.assertRaises(AC.ConfigurationError):
                        AC.copy(b.asm, "ok", name)
                    AC.create(b.asm, "other")
                    try:
                        with self.assertRaises(AC.ConfigurationError):
                            AC.rename(b.asm, "other", name)
                        self.assertEqual(AC._find(b.asm, "other").Label, "other")
                    finally:
                        doc.removeObject(AC._find(b.asm, "other").Name)
            self.assertEqual([c.Label for c in AC.configurations(b.asm)], before)
            # the name is stored trimmed; "/" is an ordinary character and is kept as typed
            AC.create(b.asm, "  a/b  ", suppress=["LA"])
            self.assertEqual(AC._find(b.asm, "a/b").Label, "a/b")
            AC.rename(b.asm, "a/b", " c/d ")
            self.assertEqual(AC._find(b.asm, "c/d").Label, "c/d")
            AC.rename(b.asm, "c/d", "c/d")      # to itself: nothing to do
        finally:
            App.closeDocument(doc.Name)

    def test_report_is_json_and_cites_entities(self):
        doc = App.newDocument("CfgRep")
        try:
            b = C.build_case(doc, "alt_unmatched")
            AC.create(b.asm, "Alt", **b.cfgs["Alt"].create)
            rep = AC.activate(b.asm, "Alt")
            json.dumps(rep)
            alt = rep["alternatives"][0]
            self.assertTrue(alt["entity"] and alt["part_entity"])
            self.assertEqual(alt["joints"][0]["outcome"], "suppressed")
            self.assertEqual(rep["suppressed_joints"][0]["joint"], "JM")
            self.assertTrue(rep["suppressed_joints"][0]["entity"])
        finally:
            App.closeDocument(doc.Name)

    def test_mechanism_truth_against_a_plain_build(self):
        """The closed forms agree with a plain build of the mechanism at the target value, no configuration."""
        for theta in (30.0, 90.0):
            doc = App.newDocument("CfgPlain")
            try:
                b = C.slider_crank_case(doc, 0.0)
                doc.getObject("CrankAng").Angle = theta
                doc.recompute()
                b.asm.solve()
                for n, plc in C.crank_truth(theta).items():
                    pe, ae = _pl_err(doc.getObject(n).Placement, plc)
                    self.assertLess(pe, POS_TOL)
                    self.assertLess(ae, ANG_TOL)
            finally:
                App.closeDocument(doc.Name)

    def test_repair_1_regressions(self):
        for fn in REPAIR_PROBES:
            with self.subTest(scenario=fn.__name__):
                self.assertEqual(fn(), [])

    def test_mutants_are_caught(self):
        self.assertGreaterEqual(len(MUTANTS), 12)
        probe_cases = list(C.CASES)
        table = []
        for mname in MUTANTS:
            with self.subTest(mutant=mname):
                failing = []
                with mutated(mname):
                    for name in probe_cases:
                        try:
                            problems, _ = evaluate_case(name, with_export=False)
                        except Exception as exc:
                            problems = [repr(exc)]
                        if problems:
                            failing.append(name)
                    for label, fn in (("expression", expression_problems), ("user_edit", user_edit_problems),
                                      ("drift", drift_problems)) + tuple(
                            (f.__name__, f) for f in REPAIR_PROBES):
                        try:
                            if fn():
                                failing.append(label)
                        except Exception:
                            failing.append(label)
                table.append((mname, failing))
                self.assertTrue(failing, "mutant %s survived" % mname)
        print("\n  mutants: %d planted, %d caught (failing cases)" % (len(table), sum(1 for _m, f in table if f)))
        for mname, failing in table:
            print("    %-40s %3d  %s" % (mname, len(failing), ", ".join(failing[:3])))
        for name in probe_cases:
            self.assertEqual(evaluate_case(name, with_export=False)[0], [], name)
