# SPDX-License-Identifier: LGPL-2.1-or-later
"""G06 slice 2a: component replace with joint reference remapping, accuracy, joints, undo, mutants.

The corpus (replace_corpus.py) holds cases with truths given as geometric descriptions. ``run_case``
builds one case in a fresh document, plans, applies, and checks everything the brief's checks 2, 3
and 5 ask; the accuracy, mutant and report tests all iterate the same table.
"""

import contextlib
import json
import math
import os
import shutil
import tempfile
import unittest

import FreeCAD as App

import JointObject
import ReplaceComponent as R
import UtilsAssembly as U
from AssemblyTests import replace_corpus as C

POS_TOL = 1e-6   # brief check 3, mm
ANG_TOL = 1e-9   # brief check 3, rad
RATE_MIN = 0.85  # brief check 2


# ------------------------------------------------------------------ evaluation

def _split(sub):
    head, _, last = sub.rpartition(".")
    return (head + "." if head else ""), last


def element_reports(report, key):
    jname, prop = key.split(".")
    for j in report["joints"]:
        if j["joint"] == jname:
            for r in j["references"]:
                if r["reference"] == prop:
                    return r
    return None


def classify(report, truth, allow_reported=False):
    """Compare a plan report with the truth. Returns (stats, problems).

    stats counts per reference: known (truth is a match), ok, reported (not matched, not wrong),
    wrong (a different element, or a pick among ambiguous candidates), plus the unmatchable and
    ambiguous truths that were reported as expected."""
    stats = {"known": 0, "ok": 0, "reported": 0, "wrong": 0, "unmatchable": 0, "unmatchable_ok": 0,
             "ambiguous": 0, "ambiguous_ok": 0}
    problems = []
    for key, tl in truth.items():
        rep = element_reports(report, key)
        if rep is None:
            problems.append("%s: reference missing from the report" % key)
            stats["known" if all(t[0] == "el" for t in tl) else "unmatchable"] += 1
            continue
        els = rep["elements"]
        if len(els) != len(tl):
            problems.append("%s: %d elements reported, %d expected" % (key, len(els), len(tl)))
            continue
        kinds = {t[0] for t in tl}
        if kinds == {"el"}:
            stats["known"] += 1
            wrong, notmatched = False, False
            for e, t in zip(els, tl):
                if e["status"] == "matched":
                    if e["new_element"] != t[1]:
                        wrong = True
                        problems.append("%s: %s remapped to %s, truth %s" % (key, e["old_element"], e["new_element"], t[1]))
                else:
                    notmatched = True
            if wrong:
                stats["wrong"] += 1
            elif notmatched:
                stats["reported"] += 1
                if not allow_reported:
                    problems.append("%s: not matched automatically (%s)" % (key, [e["reason"] for e in els if e["status"] != "matched"]))
            else:
                stats["ok"] += 1
        else:
            for e, t in zip(els, tl):
                if t[0] == "unmatched":
                    stats["unmatchable"] += 1
                    if e["status"] == "unmatched" and e["reason"]:
                        stats["unmatchable_ok"] += 1
                    else:
                        if e["status"] == "matched":
                            stats["wrong"] += 1
                        problems.append("%s: expected unmatched with a reason, got %s %s" % (key, e["status"], e["new_element"]))
                elif t[0] == "ambiguous":
                    stats["ambiguous"] += 1
                    cand = {c["element"] for c in e["candidates"]}
                    if e["status"] == "ambiguous" and (not t[1] or cand == set(t[1])):
                        stats["ambiguous_ok"] += 1
                    else:
                        if e["status"] == "matched":
                            stats["wrong"] += 1
                        problems.append("%s: expected ambiguous %s, got %s %s %s" % (key, sorted(t[1]), e["status"], e["new_element"], sorted(cand)))
                else:
                    # a matched element in a reference that also holds unmatched ones
                    if e["status"] != "matched" or e["new_element"] != t[1]:
                        problems.append("%s: %s expected %s, got %s %s" % (key, e["old_element"], t[1], e["status"], e["new_element"]))
    return stats, problems


def _sub_with(sub, name):
    head, _, _ = sub.rpartition(".")
    return (head + "." if head else "") + name


def jcs_from_truth(joint, prop, names):
    """Global JCS of a joint reference whose element names are replaced by ``names``."""
    comp, subs = getattr(joint, prop)
    ref = (comp, [_sub_with(s, n) for s, n in zip(subs, names)])
    off = joint.Offset1 if prop == "Reference1" else joint.Offset2
    plc = U.findPlacement(ref, joint.JointType == "Distance") * off
    return U.getJcsGlobalPlc(plc, ref)


def residual(joint, jtype, mprop, names, distance):
    """(mm, rad) by which the joint is violated, computed from the truth elements on the M side."""
    other = "Reference1" if mprop == "Reference2" else "Reference2"
    other_names = [s.rsplit(".", 1)[-1] for s in getattr(joint, other)[1]]
    g = {mprop: jcs_from_truth(joint, mprop, names), other: jcs_from_truth(joint, other, other_names)}
    a, b = g["Reference1"], g["Reference2"]
    dp = b.Base - a.Base
    za = a.Rotation.multVec(App.Vector(0, 0, 1))
    zb = b.Rotation.multVec(App.Vector(0, 0, 1))
    ang_par = math.acos(max(-1.0, min(1.0, abs(za.dot(zb)))))
    if jtype == "revolute":
        return dp.Length, ang_par
    if jtype == "cylindrical":
        return (dp - za * dp.dot(za)).Length, ang_par
    # The turn about the joint axis of a fixed or slider joint is not asserted for a changed
    # geometry: a probe turned a joint frame about its Z by 90 and 180 degrees and the solver
    # left the part where it was; unchanged frames are covered by the "did not move" check.
    if jtype == "fixed":
        return dp.Length, ang_par
    if jtype == "slider":
        return (dp - za * dp.dot(za)).Length, ang_par
    if jtype == "ball":
        return dp.Length, 0.0
    if jtype in ("planar", "distance"):
        return abs(abs(dp.dot(za)) - abs(distance)), ang_par
    return 0.0, 0.0


def placements(objs):
    return [o.Placement for o in objs]


def plc_diff(a, b):
    d = (a.Base - b.Base).Length
    r = a.Rotation.inverted() * b.Rotation
    ang = r.Angle
    ang = min(ang, abs(2 * math.pi - ang))
    return d, ang


def stale_refs(asm, b):
    """Joint references that still reach the replaced component."""
    out = []
    olds = list(b.host_names) if b.mode in ("direct",) else []
    for o in asm.Document.Objects:
        if hasattr(o, "Reference1") and hasattr(o, "JointType"):
            for prop in ("Reference1", "Reference2"):
                ref = getattr(o, prop)
                if ref and ref[0] is not None and ref[0].Name in olds:
                    out.append("%s.%s" % (o.Name, prop))
        if hasattr(o, "ObjectToGround") and o.ObjectToGround is not None and o.ObjectToGround.Name in olds:
            out.append(o.Name + ".ObjectToGround")
    return out


def check_after_apply(b, rep, before, plan=None, reported_ok=False):
    """Checks 3 and 5 (joints, references, kept objects) for an applied case; returns problems."""
    problems = []
    asm = b.asm
    doc = asm.Document
    if getattr(b, "host_names", None) is None:
        b.host_names = [o.Name for o in b.hosts]
        b.moving_names = [o.Name for o in b.moving]
    if not rep.get("applied"):
        problems.append("not applied: %s" % rep.get("reason"))
        return problems
    if b.mode == "direct":
        problems += ["stale reference to the old part: %s" % s for s in stale_refs(asm, b)]
        for hn in b.host_names:
            h = doc.getObject(hn)
            if h is None and not b.apply_kwargs.get("delete_old"):
                problems.append("old object %s deleted without delete_old" % hn)
            elif h is not None and U.getObject((h, [""])) is not None and h.getParentGeoFeatureGroup() is not None:
                problems.append("old object %s is still inside the assembly" % hn)
    else:
        for h in b.hosts:
            host = h
            if host.TypeId == "App::LinkElement":
                continue
        new_leaf = b.new if b.new.TypeId != "App::Link" else b.new.LinkedObject
        for h in rep["hosts"]:
            lk = doc.getObject(h)
            if lk is not None and lk.TypeId == "App::Link" and lk.LinkedObject != new_leaf:
                problems.append("%s still links to %s" % (h, lk.LinkedObject.Name))
    # references equal the truth, joints that need to be broken are marked and kept
    plan_broken = set()
    if reported_ok and plan is not None:
        plan_broken = {j["joint"] for j in plan["joints"] if j["status"] != "matched"}
    for key, tl in b.truth.items():
        jname, prop = key.split(".")
        j = doc.getObject(jname)
        if j is None:
            problems.append("%s: joint deleted" % jname)
            continue
        ref = getattr(j, prop)
        names = [s.rsplit(".", 1)[-1] for s in ref[1]]
        if jname in plan_broken:
            if not getattr(j, "Suppressed", False) or not any("?" in x for x in ref[1]):
                problems.append("%s: joint the plan could not match is not marked broken" % jname)
            if not R.is_broken(j):
                problems.append("%s: broken joint has no ReplaceBroken marker" % jname)
        elif all(t[0] == "el" for t in tl):
            want = [t[1] for t in tl]
            if names != want:
                problems.append("%s: reference %s, truth %s" % (key, names, want))
            pref = before.get("prefixes", {}).get(key)
            if pref is not None and [_split(x)[0] for x in ref[1]] != pref:
                problems.append("%s: path to the part changed from %s to %s" % (key, pref, [_split(x)[0] for x in ref[1]]))
            if getattr(j, "Suppressed", False):
                problems.append("%s: suppressed although every reference matched" % jname)
            if R.is_broken(j):
                problems.append("%s: marked broken although every reference matched" % jname)
        else:
            if not getattr(j, "Suppressed", False):
                problems.append("%s: broken joint not suppressed" % jname)
            if not any("?" in s for s in ref[1]):
                problems.append("%s: broken joint reference not marked: %s" % (key, list(ref[1])))
            if not R.is_broken(j):
                problems.append("%s: broken joint has no ReplaceBroken marker" % jname)
    # delete_old: what is still used stays and says why, what nobody uses goes
    kept = {k["name"]: k for k in rep.get("kept_old", [])}
    for name in b.expect_kept:
        if doc.getObject(name) is None:
            problems.append("%s was deleted although something else still uses it" % name)
        if name not in kept or not kept[name]["users"]:
            problems.append("%s kept but kept_old does not say why: %s" % (name, rep.get("kept_old")))
        if name in rep.get("deleted_old", []):
            problems.append("%s listed as deleted although it was kept" % name)
    for name in b.expect_deleted:
        if doc.getObject(name) is not None:
            problems.append("%s should have been deleted (nothing else uses it)" % name)
        if name not in rep.get("deleted_old", []):
            problems.append("%s not listed in deleted_old: %s" % (name, rep.get("deleted_old")))
    for lk in doc.Objects:
        if lk.TypeId == "App::Link" and lk.Name == "OtherUse" and lk.LinkedObject is None:
            problems.append("OtherUse lost its link")
    # the rest solve: every fully matched joint is satisfied to the brief's tolerance
    worst_mm = worst_rad = 0.0
    for (j, jtype) in b.joints:
        mprop = None
        for k in b.truth:
            if k.startswith(j.Name + "."):
                mprop = k.split(".")[1]
                tl = b.truth[k]
        if mprop is None or not all(t[0] == "el" for t in tl) or j.Name in plan_broken:
            continue
        mm, rad = residual(j, jtype, mprop, [t[1] for t in tl], float(j.Distance.Value) if jtype in ("distance", "planar") else 0.0)
        worst_mm, worst_rad = max(worst_mm, mm), max(worst_rad, rad)
        if mm > POS_TOL or rad > ANG_TOL:
            problems.append("%s (%s): residual %.3g mm, %.3g rad" % (j.Name, jtype, mm, rad))
    if b.still:
        for cname, plc0 in zip(b.moving_names, before["placements"]):
            comp = doc.getObject(cname)
            cur = comp
            if b.mode == "direct":
                cur = doc.getObject(rep["new_components"][cname]) if cname in rep.get("new_components", {}) else comp
            al = rep["alignment"].get("placement")
            t = App.Placement(App.Vector(*al["base"]), App.Rotation(*al["rotation"])) if al else App.Placement()
            # the component's placement composed with the alignment is where the old frame sits now
            d, ang = plc_diff(cur.Placement * t.inverse(), plc0)
            if d > POS_TOL or ang > ANG_TOL:
                problems.append("%s moved by %.3g mm, %.3g rad although every referenced frame is unchanged" % (cname, d, ang))
    rep["_worst"] = (worst_mm, worst_rad)
    return problems


RESULTS = {}


def run_case(name, checks=("plan", "apply", "undo")):
    """Build, plan, apply, undo/redo one case in a fresh document; returns a result dict."""
    doc = App.newDocument("Rc_" + name)
    res = {"name": name, "problems": [], "stats": None, "klass": C.CASES[name].klass}
    try:
        b = C.build_case(doc, name)
        asm = b.asm
        before_sig = R.signature(doc)
        b.host_names = [o.Name for o in b.hosts]
        b.moving_names = [o.Name for o in b.moving]
        before = {"placements": [App.Placement(o.Placement) for o in b.moving],
                  "prefixes": {k: [_split(x)[0] for x in getattr(doc.getObject(k.split(".")[0]), k.split(".")[1])[1]]
                               for k in b.truth}}
        rep = R.plan(asm, b.old, b.new, **b.kwargs)
        json.dumps(rep)
        res["plan"] = rep
        if R.signature(doc) != before_sig:
            res["problems"].append("plan changed the document")
        stats, probs = classify(rep, b.truth, C.CASES[name].opts.get("reported_ok", False))
        # a reference that matches only because the caller passed an alignment is reported (needs alignment), not
        # matched: the default plan (no alignment) is what counts for matched, the hinted plan must give the truth
        stats["hinted_ok"] = stats["ok"]
        stats["needs_alignment"] = 0
        if "alignment" in b.kwargs:
            known = {k: tl for k, tl in b.truth.items() if all(t[0] == "el" for t in tl)}
            rep_d = R.plan(asm, b.old, b.new, **{k: v for k, v in b.kwargs.items() if k != "alignment"})
            st_d, pr_d = classify(rep_d, known, allow_reported=True)
            stats["wrong"] += st_d["wrong"]
            res["problems"] += [x for x in pr_d if "remapped to" in x]
            stats["ok"] = st_d["ok"]
            stats["reported"] = st_d["reported"]
            stats["needs_alignment"] = st_d["reported"]
            res["default_plan"] = rep_d
        # D13: a known reference that the default plan did not match and whose readings of the alignment land on
        # different places is ambiguous by geometry. The plan itself says which (alignment.readings_differ), so a
        # future case cannot leave the denominator by being listed here
        dflt = res.get("default_plan", rep)
        differ = {d["reference"] for d in dflt["alignment"].get("readings_differ", [])}
        stats["geometry_ambiguous"] = 0
        stats["reported_undecidable"] = 0
        for jr in dflt["joints"]:
            for rr in jr["references"]:
                key = "%s.%s" % (jr["joint"], rr["reference"])
                tl = b.truth.get(key)
                if not tl or not all(t[0] == "el" for t in tl) or rr["status"] == "matched":
                    continue
                if any(e["status"] == "matched" and e["new_element"] != t[1] for e, t in zip(rr["elements"], tl)):
                    continue  # a silent pick: already counted wrong
                if key in differ:
                    stats["geometry_ambiguous"] += 1
                    res.setdefault("geometry_ambiguous", []).append(key)
                else:
                    stats["reported_undecidable"] += 1
        res["stats"] = stats
        res["problems"] += probs
        if C.CASES[name].opts.get("renumbers"):
            diff = [k for k, tl in b.truth.items() for t, o in zip(tl, b.old_names.get(k, [])) if t[0] == "el" and t[1] != o]
            if not diff:
                res["problems"].append("case is meant to renumber but no referenced element changed its name")
        if "apply" in checks:
            rep2 = R.apply(asm, b.old, b.new, **dict(b.kwargs, **b.apply_kwargs))
            json.dumps(rep2)
            res["apply"] = rep2
            res["problems"] += check_after_apply(b, rep2, before, rep, C.CASES[name].opts.get("reported_ok", False))
            after_sig = R.signature(doc)
            if "undo" in checks and rep2.get("applied"):
                doc.undo()
                if R.signature(doc) != before_sig:
                    res["problems"].append("undo did not restore the document signature")
                doc.redo()
                if R.signature(doc) != after_sig:
                    res["problems"].append("redo did not reproduce the applied state")
    except Exception as exc:
        import traceback
        res["problems"].append("exception: %s" % traceback.format_exc()[-600:])
    finally:
        for d in list(getattr(locals().get("b", None), "extra_docs", []) or []):
            App.closeDocument(d.Name)
        App.closeDocument(doc.Name)
    return res


# ------------------------------------------------- broken joints: marker, findPlacement, repair

def broken_machinery():
    """What a joint a replace could not remap must do for the user; returns the problems found.

    The marker is visible and persistent, findPlacement refuses a "?" reference instead of answering the
    identity, unsuppressing without a repair is refused with an error, and repair() validates the
    element, sets it, clears the marker and lets the joint solve."""
    problems = []
    doc = App.newDocument("Rc_broken")
    try:
        b = C.build_case(doc, "sym_two_holes_cylindrical")
        plan = R.plan(b.asm, b.old, b.new)
        cands = [c["element"] for c in plan["joints"][0]["references"][0]["elements"][0]["candidates"]]
        R.apply(b.asm, b.old, b.new)
        j = doc.getObject("Cylindrical")
        if not R.is_broken(j) or "candidates" not in j.ReplaceBroken:
            problems.append("no ReplaceBroken text naming the candidates: %r" % getattr(j, "ReplaceBroken", None))
        if not j.Suppressed:
            problems.append("broken joint not suppressed")
        try:
            plc = U.findPlacement(j.Reference2)
            problems.append("findPlacement answered %s for a broken reference" % plc)
        except U.BrokenReferenceError:
            pass
        n_log = len(JointObject.BrokenUnsuppressLog)
        j.Suppressed = False
        if not j.Suppressed or len(JointObject.BrokenUnsuppressLog) != n_log + 1:
            problems.append("unsuppressing a broken joint was not refused with an error")
        try:
            R.repair(j, "Reference2", "Face999")
            problems.append("repair accepted an element the part does not have")
        except R.ReplaceError:
            pass
        try:
            R.repair(j, "Reference2", "Edge1")
            problems.append("repair accepted an edge where the reference held a face")
        except R.ReplaceError:
            pass
        if not R.is_broken(j):
            problems.append("a refused repair cleared the marker")
        try:
            R.repair(doc.getObject("Grounded"), "Reference1", "Face1")
            problems.append("repair accepted a joint that is not broken")
        except Exception:
            pass
        out = R.repair(j, "Reference2", cands[1])
        if R.is_broken(j) or j.Suppressed or any("?" in x for x in j.Reference2[1]) or out["still_broken"]:
            problems.append("repair did not leave a sound joint: %s %s %s" % (j.ReplaceBroken, j.Suppressed, list(j.Reference2[1])))
        if [x.rsplit(".", 1)[-1] for x in j.Reference2[1]] != [cands[1]] * 2:
            problems.append("repair set %s" % list(j.Reference2[1]))
        if "Invalid" in j.State:
            problems.append("repaired joint is invalid: %s" % j.State)
        if b.asm.solve() != 0:
            problems.append("assembly does not solve after the repair")
        doc.undo()
        if not R.is_broken(j) or not j.Suppressed:
            problems.append("undo of the repair did not bring the broken state back")
    except Exception:
        import traceback
        problems.append("exception: " + traceback.format_exc()[-500:])
    finally:
        App.closeDocument(doc.Name)
    return problems


def suppression_and_offset_machinery():
    """A joint the user had suppressed stays suppressed through break and repair; editing the offset of a
    broken joint is a console message, not a traceback (updateJCSPlacements, which raises, is not called)."""
    problems = []
    for was in (True, False):
        doc = App.newDocument("Rc_supp")
        try:
            b = C.build_case(doc, "sym_two_holes_cylindrical")
            plan = R.plan(b.asm, b.old, b.new)
            cands = [c["element"] for c in plan["joints"][0]["references"][0]["elements"][0]["candidates"]]
            j = doc.getObject("Cylindrical")
            j.Suppressed = was
            R.apply(b.asm, b.old, b.new)
            if not (R.is_broken(j) and j.Suppressed):
                problems.append("joint (suppressed=%s) not broken and suppressed after the replace" % was)
            calls = []
            orig = JointObject.Joint.updateJCSPlacements
            JointObject.Joint.updateJCSPlacements = lambda self, joint: calls.append(1) or orig(self, joint)
            try:
                want = App.Placement(App.Vector(1, 2, 3), App.Rotation())
                j.Offset1 = want
            finally:
                JointObject.Joint.updateJCSPlacements = orig
            if calls:
                problems.append("an Offset edit on a broken joint went on to the placement update (a traceback)")
            if not j.Offset1.isSame(want, 1e-9):
                problems.append("the offset edit on a broken joint was not kept")
            R.repair(j, "Reference2", cands[1])
            if R.is_broken(j) or j.Suppressed != was:
                problems.append("repair left suppressed=%s for a joint that was suppressed=%s" % (j.Suppressed, was))
            doc.undo()
            if not (R.is_broken(j) and j.Suppressed):
                problems.append("undo of the repair did not bring the broken state back (was=%s)" % was)
        except Exception:
            import traceback
            problems.append("exception: " + traceback.format_exc()[-500:])
        finally:
            App.closeDocument(doc.Name)
    return problems


DEDICATED = {"broken_machinery": broken_machinery, "suppression_and_offset": suppression_and_offset_machinery}


# ----------------------------------------------------------------- mutants
# Each installer patches a hook of ReplaceComponent; the case named next to it must fail.

def _mut_index_only():
    def f(o_name, mo, mn, pool, claims=None):
        if o_name in mn.els:
            return {"status": "matched", "new": o_name, "candidates": [], "basis": "index", "cost": 0.0, "reason": ""}
        return {"status": "unmatched", "new": None, "candidates": [], "basis": "", "cost": None, "reason": "no such index"}
    R._match_element = f


def _mut_ignore_alignment():
    orig = R._choose_alignment

    def f(old_shape, new_shape, forced=None):
        import FreeCAD as A
        return A.Placement(), {"method": "identity", "overlap": None, "candidates": 1}, new_shape.copy()
    R._choose_alignment = f


def _mut_pick_first_ambiguous():
    orig = R._decide

    def f(o, pool, mo, mn, table=None):
        r = orig(o, pool, mo, mn, table)
        if r["status"] == "ambiguous" and r["candidates"]:
            r = dict(r, status="matched", new=r["candidates"][0][0], candidates=[], reason="")
        return r
    R._decide = f


def _mut_drop_link_placement():
    R._new_link_placement = lambda old_obj, plc: plc


def _mut_stale_reference():
    orig = R._write_ref

    def f(joint, prop, comp, subs):
        old_ref = getattr(joint, prop)
        orig(joint, prop, old_ref[0], subs)
    R._write_ref = f


def _mut_delete_unmatched_joint():
    def f(doc, joint, reason=""):
        doc.removeObject(joint.Name)
    R._break_joint = f


def _mut_skip_array_elements():
    orig = R._collect_refs

    def f(assembly, hosts):
        return [r for r in orig(assembly, hosts) if r.comp.TypeId != "App::LinkElement"]
    R._collect_refs = f


def _mut_only_reference1():
    orig = R._collect_refs

    def f(assembly, hosts):
        return [r for r in orig(assembly, hosts) if r.prop == "Reference1"]
    R._collect_refs = f


def _mut_break_noop():
    R._break_joint = lambda doc, joint, reason="": None


def _mut_delete_always():
    orig = R.apply

    def f(assembly, old, new, choices=None, delete_old=False, alignment=None):
        return orig(assembly, old, new, choices=choices, delete_old=True, alignment=alignment)
    R.apply = f


def _mut_guess_unmatched():
    # an unmatched element is "remapped" to the element with the same index: a silent retarget
    orig = R._decide

    def f(o, pool, mo, mn, table=None):
        r = orig(o, pool, mo, mn, table)
        if r["status"] == "unmatched" and o.name in pool:
            r = dict(r, status="matched", new=o.name, reason="", basis="index")
        return r
    R._decide = f


def _mut_subpath_dropped():
    def f(rf, new_host, new_mids, elements):
        return (new_host if (new_host is not None and rf.chain_index == 0) else rf.comp), list(elements)
    R._new_ref_value = f


def _mut_cheapest_even_if_far():
    # accept any same-type candidate: no neighbour check, no radius limit
    R.ADJ_MIN = 0.0
    R.RADIUS_ADAPT = 10.0
    R.SIZE_RATIO_MAX = 1e9


class _TableNoExact(R._Table):
    """Rivals that are exact twins are not seen: a removed hole is matched to the hole that stayed."""

    def rivals(self, n_name):
        return [r for r in R._Table.rivals(self, n_name) if r[0] > 0.0]


class _TableOff(R._Table):
    """No one-to-one rule at all: every old element takes the best candidate whoever else wants it."""

    def verdict(self, o_name, n_name, cost):
        return ("free",)


def _mut_claims_ignored():
    R._claims = lambda mo, mn, kind: _TableNoExact(mo, mn, kind)


def _mut_one_to_one_off():
    R._claims = lambda mo, mn, kind: _TableOff(mo, mn, kind)


def _mut_move_and_resize_allowed():
    R.MOVE_TOL = 1e9


def _mut_alignment_check_off():
    R._alignment_conflict = lambda *a, **k: None


def _mut_near_check_off():
    R._near_conflict = lambda *a, **k: []


def _mut_symmetry_rule_off():
    R._symmetry_verdict = lambda *a, **k: None


def _mut_agreement_check_blind():
    # the comparison of two readings finds no difference: every ambiguous alignment "agrees" and the part stands as placed
    R._readings_differ = lambda first, second, both=False, tol=None: []


def _mut_near_twin_shortcut():
    # the old shortcut: a turned reading that recognises no old face identity does not is dropped without asking where
    # the references land (a new part symmetric under the turn recognises the same faces turned and as placed)
    orig = R._near_conflict
    R._near_conflict = lambda mo, new_shape, comp, probe=None: orig(mo, new_shape, comp, None)


def _mut_resize_exemption_off():
    R._same_topology = lambda *a, **k: False


def _mut_suppression_forgotten():
    orig = R._break_joint

    def f(doc, joint, reason=""):
        orig(doc, joint, reason)
        setattr(joint, R.WAS_SUPPRESSED_PROP, False)
    R._break_joint = f


def _mut_delete_ignores_users():
    R._outside_users = lambda members: []


def _mut_body_features_orphaned():
    R._deletion_set = lambda obj: [obj]


def _mut_findplacement_identity():
    orig = U.findPlacement

    def f(ref, ignoreVertex=False):
        if U.hasBrokenMarker(ref):
            return App.Placement()
        return orig(ref, ignoreVertex)
    U.findPlacement = f


def _mut_unsuppress_guard_removed():
    orig = JointObject.Joint.onChanged

    def f(self, joint, prop):
        if prop == "Suppressed":
            return None
        return orig(self, joint, prop)
    JointObject.Joint.onChanged = f


def _mut_marker_not_set():
    def f(doc, joint, reason=""):
        if hasattr(joint, "Suppressed"):
            joint.Suppressed = True
    R._break_joint = f


def _mut_repair_leaves_marker():
    orig = R.repair

    def f(joint, reference, elements):
        out = orig(joint, reference, elements)
        setattr(joint, R.BROKEN_PROP, "stale")
        return out
    R.repair = f


def _mut_repair_unchecked():
    R._check_element = lambda *a, **k: None


MUTANTS = {
    "match_by_element_index_only": (_mut_index_only, "renumber_revolute"),
    "alignment_ignored_placement_and_origin": (_mut_ignore_alignment, "origin_rot_x_fixed"),
    "first_ambiguous_candidate_picked": (_mut_pick_first_ambiguous, "sym_two_holes_revolute"),
    "link_placement_dropped": (_mut_drop_link_placement, "direct_part_revolute_renumbered"),
    "stale_reference_to_old_part": (_mut_stale_reference, "direct_part_fixed"),
    "unmatched_joint_deleted": (_mut_delete_unmatched_joint, "removed_hole_revolute"),
    "array_elements_skipped": (_mut_skip_array_elements, "array_elements_revolute"),
    "only_reference1_remapped": (_mut_only_reference1, "link_with_rotation_revolute"),
    "broken_joint_left_active": (_mut_break_noop, "removed_hole_revolute"),
    "old_part_deleted_unasked": (_mut_delete_always, "direct_part_fixed"),
    "unmatched_guessed_by_index": (_mut_guess_unmatched, "removed_hole_revolute"),
    "subassembly_path_dropped": (_mut_subpath_dropped, "sub_revolute_renumbered"),
    "exact_twin_claims_ignored": (_mut_claims_ignored, "two_holes_first_removed_cylindrical"),
    "one_to_one_assignment_off": (_mut_one_to_one_off, "d1_both_holes_referenced_first_gone_second_shifted"),
    "moved_and_resized_allowed": (_mut_move_and_resize_allowed, "c2_hole_gone_wider_hole_nearby"),
    "alignment_conflict_check_off": (_mut_alignment_check_off, "e3_bar_turned_about_z_boss_top_and_end"),
    "near_twin_alignment_check_off": (_mut_near_check_off, "n1_bracket_turned_and_gained_a_small_hole"),
    "symmetry_rule_off": (_mut_symmetry_rule_off, "n20_square_plate_turned_a_quarter_and_gained_an_offcentre_hole"),
    "resize_exemption_off": (_mut_resize_exemption_off, "core_resize_cube_to_flat_plate"),
    "agreement_check_treats_differing_as_agreeing": (_mut_agreement_check_blind, "n5_symmetric_plate_turned_and_gained_an_offcentre_hole"),
    "near_twin_shortcut_drops_a_reading_unasked": (_mut_near_twin_shortcut, "n17_plate_with_two_holes_replaced_by_the_plain_plate_turned_about_y"),
    "user_suppression_forgotten_by_repair": (_mut_suppression_forgotten, "@suppression_and_offset"),
    "delete_old_ignores_other_users": (_mut_delete_ignores_users, "t6_delete_old_direct_part_used_by_another_assembly"),
    "body_features_orphaned": (_mut_body_features_orphaned, "t10_delete_old_body_nobody_else_uses"),
    "marker_not_set_on_broken_joint": (_mut_marker_not_set, "removed_hole_revolute"),
    "findplacement_identity_on_broken_ref": (_mut_findplacement_identity, "@broken_machinery"),
    "unsuppress_of_broken_joint_unguarded": (_mut_unsuppress_guard_removed, "@broken_machinery"),
    "repair_leaves_marker": (_mut_repair_leaves_marker, "@broken_machinery"),
    "repair_accepts_any_element": (_mut_repair_unchecked, "@broken_machinery"),
    "neighbour_and_radius_checks_off": (_mut_cheapest_even_if_far, "a1_step_removed_lower_face_two_mm_away"),
}


@contextlib.contextmanager
def mutated(name):
    saved = {k: getattr(R, k) for k in ("_match_element", "_choose_alignment", "_decide", "_new_link_placement",
                                        "_write_ref", "_break_joint", "_collect_refs", "_solve", "_new_ref_value", "apply",
                                        "ADJ_MIN", "RADIUS_ADAPT", "SIZE_RATIO_MAX", "_claims", "MOVE_TOL",
                                        "_alignment_conflict", "_near_conflict", "_readings_differ", "_symmetry_verdict", "_same_topology", "_outside_users", "_deletion_set", "repair",
                                        "_check_element")}
    saved_u = U.findPlacement
    saved_j = JointObject.Joint.onChanged
    try:
        MUTANTS[name][0]()
        yield
    finally:
        for k, v in saved.items():
            setattr(R, k, v)
        U.findPlacement = saved_u
        JointObject.Joint.onChanged = saved_j


# -------------------------------------------------------------------- tests

class TestReplaceComponent(unittest.TestCase):
    results = {}

    @classmethod
    def setUpClass(cls):
        for name in C.CASES:
            cls.results[name] = run_case(name)

    def test_corpus_shape(self):
        counts = {}
        for c in C.CASES.values():
            counts[c.klass] = counts.get(c.klass, 0) + 1
        print("\n  corpus: %d cases %s" % (len(C.CASES), dict(sorted(counts.items()))))
        self.assertGreaterEqual(len(C.CASES) + 1, 30)
        for klass in ("joint_type", "renumber", "resize", "hole_moved", "face_removed", "mirrored", "family",
                      "ambiguous", "origin", "link", "direct", "array", "subassembly", "workbench_tests",
                      "one_to_one", "alignment", "delete_safety"):
            self.assertIn(klass, counts)
        types = set()
        for name, r in self.results.items():
            pass
        for name in C.CASES:
            doc = App.newDocument("Rc_shape")
            try:
                b = C.build_case(doc, name)
                types.update(t for _j, t in b.joints)
            finally:
                App.closeDocument(doc.Name)
        for t in ("fixed", "revolute", "cylindrical", "slider", "planar", "ball", "distance"):
            self.assertIn(t, types, t)

    def test_plan_accuracy_all_cases(self):
        total = {}
        per_class = {}
        failures = []
        for name, r in self.results.items():
            st = r["stats"]
            if st is None:
                failures.append((name, r["problems"]))
                continue
            for k, v in st.items():
                total[k] = total.get(k, 0) + v
                pc = per_class.setdefault(r["klass"], {})
                pc[k] = pc.get(k, 0) + v
        print("\n  per class (known refs: matched automatically / reported (needs alignment) / matched with the alignment given / wrong), unmatchable and ambiguous truths ok/total:")
        for k, v in sorted(per_class.items()) + [("TOTAL", total)]:
            print("    %-16s known %2d  automatic %2d  reported %2d  with hint %2d  wrong %d   unmatchable %d/%d   ambiguous %d/%d"
                  % (k, v["known"], v["ok"], v["reported"], v["hinted_ok"], v["wrong"], v["unmatchable_ok"],
                     v["unmatchable"], v["ambiguous_ok"], v["ambiguous"]))
        print("    matched automatically / known: %d/%d = %.1f%%;  matched with the alignment given / known: %d/%d = %.1f%%;  "
              "needing an alignment: %d;  wrong: %d" % (
                  total["ok"], total["known"], 100.0 * total["ok"] / total["known"], total["hinted_ok"], total["known"],
                  100.0 * total["hinted_ok"] / total["known"], total["needs_alignment"], total["wrong"]))
        print("    cases whose known references need an alignment to match (%d references): %s" % (
            total["needs_alignment"], ", ".join(sorted(n for n, r in self.results.items()
                                                       if r["stats"] and r["stats"]["needs_alignment"]))))
        print("    other known references reported, not matched, with no alignment hint: %s" % ", ".join(
            "%s(%d)" % (n, r["stats"]["reported"]) for n, r in sorted(self.results.items())
            if r["stats"] and r["stats"]["reported"] and not r["stats"]["needs_alignment"]))
        den = total["known"] - total["geometry_ambiguous"]
        print("    D13: references whose readings land on different places, ambiguous by geometry and out of the denominator "
              "(%d): %s" % (total["geometry_ambiguous"], ", ".join(sorted(
                  "%s:%s" % (n, k) for n, r in self.results.items() for k in r.get("geometry_ambiguous", [])))))
        print("    matched automatically / (known - ambiguous by geometry): %d/%d = %.1f%% (old count %d/%d = %.1f%%);  "
              "reported although the readings agree: %d;  wrong: %d" % (
                  total["ok"], den, 100.0 * total["ok"] / den, total["ok"], total["known"],
                  100.0 * total["ok"] / total["known"], total["reported_undecidable"], total["wrong"]))
        self.assertEqual(failures, [])
        self.assertEqual(total["wrong"], 0, "silent wrong remaps")
        self.assertEqual(total["geometry_ambiguous"], 11, "the references D13 names")
        self.assertGreaterEqual(total["ok"] / den, RATE_MIN,
                                "brief check 2 under D13: %d of %d references the geometry can decide matched automatically (%.1f%%)" % (
                                    total["ok"], den, 100.0 * total["ok"] / den))
        self.assertEqual(total["unmatchable_ok"], total["unmatchable"])
        self.assertEqual(total["ambiguous_ok"], total["ambiguous"])

    def test_each_case_plan_apply_undo(self):
        for name, r in self.results.items():
            with self.subTest(case=name):
                self.assertEqual(r["problems"], [], name)

    def test_joint_residuals_reported(self):
        worst = [r["apply"].get("_worst", (0, 0)) for r in self.results.values() if "apply" in r]
        mm = max(w[0] for w in worst)
        rad = max(w[1] for w in worst)
        print("\n  worst joint residual after replace: %.3g mm, %.3g rad over %d cases" % (mm, rad, len(worst)))
        self.assertLessEqual(mm, POS_TOL)
        self.assertLessEqual(rad, ANG_TOL)

    def test_report_is_json_with_entities(self):
        rep = self.results["renumber_revolute"]["plan"]
        self.assertEqual(rep, json.loads(json.dumps(rep)))
        self.assertEqual(rep["schema"], R.SCHEMA)
        el = rep["joints"][0]["references"][0]["elements"][0]
        self.assertTrue(el["old_entity"].startswith("Old:"))
        self.assertTrue(el["new_entity"].startswith("New:"))
        self.assertIn(el["basis"], ("exact", "adapted"))
        amb = self.results["sym_two_holes_revolute"]["plan"]["joints"][0]["references"][0]["elements"][0]
        self.assertEqual(amb["status"], "ambiguous")
        self.assertEqual(len(amb["candidates"]), 2)
        self.assertTrue(all(c["entity"].startswith("New:") for c in amb["candidates"]))
        bad = self.results["removed_hole_revolute"]["plan"]["joints"][0]["references"][0]["elements"][0]
        self.assertEqual(bad["status"], "unmatched")
        self.assertTrue(bad["reason"])
        self.assertEqual(self.results["removed_hole_revolute"]["apply"]["deleted_old"], [])

    def test_ambiguous_choice_and_broken_default(self):
        doc = App.newDocument("Rc_choice")
        try:
            b = C.build_case(doc, "sym_two_holes_cylindrical")
            plan = R.plan(b.asm, b.old, b.new)
            cands = [c["element"] for c in plan["joints"][0]["references"][0]["elements"][0]["candidates"]]
            self.assertEqual(len(cands), 2)
            with self.assertRaises(ValueError):
                R.plan(b.asm, b.old, b.new, choices={"Cylindrical.Reference2": "Face999"})
            rep = R.plan(b.asm, b.old, b.new, choices={"Cylindrical.Reference2": cands[1]})
            e = rep["joints"][0]["references"][0]["elements"][0]
            self.assertEqual((e["status"], e["new_element"], e["basis"]), ("matched", cands[1], "user"))
            rep = R.apply(b.asm, b.old, b.new, choices={"Cylindrical.Reference2": cands[1]})
            j = doc.getObject("Cylindrical")
            self.assertEqual(j.Reference2[1][0], cands[1])
            self.assertFalse(j.Suppressed)
            doc.undo()
            rep = R.apply(b.asm, b.old, b.new)
            self.assertTrue(j.Suppressed)
            self.assertTrue(j.Reference2[1][0].startswith("?"))
            self.assertIsNotNone(doc.getObject("Cylindrical"))
        finally:
            App.closeDocument(doc.Name)

    def test_delete_old_only_on_request(self):
        for name, gone in (("direct_part_fixed", True), ("link_source_given_cylindrical", True)):
            with self.subTest(case=name):
                doc = App.newDocument("Rc_del")
                try:
                    b = C.build_case(doc, name)
                    old_name = b.old.Name if b.mode == "direct" else b.old.Name
                    leaf = b.old if b.mode in ("direct", "source") else b.old.LinkedObject
                    lname = leaf.Name
                    R.apply(b.asm, b.old, b.new)
                    self.assertIsNotNone(doc.getObject(lname), "kept without delete_old")
                    doc.undo()
                    rep = R.apply(b.asm, b.old, b.new, delete_old=True)
                    self.assertEqual(doc.getObject(lname) is None, gone)
                    self.assertTrue(rep["deleted_old"])
                finally:
                    App.closeDocument(doc.Name)

    def test_save_and_reopen_keeps_remapped_references(self):
        for name in ("renumber_revolute", "direct_part_revolute_renumbered", "array_elements_revolute",
                     "sub_revolute_renumbered", "hole_moved_revolute", "link_two_links_one_source"):
            with self.subTest(case=name):
                doc = App.newDocument("Rc_save")
                tmp = tempfile.mkdtemp(prefix="rc")
                try:
                    b = C.build_case(doc, name)
                    R.apply(b.asm, b.old, b.new, **b.kwargs)
                    want = {k: [t[1] for t in v] for k, v in b.truth.items()}
                    path = os.path.join(tmp, "a.FCStd")
                    doc.saveAs(path)
                    App.closeDocument(doc.Name)
                    doc2 = App.openDocument(path)
                    try:
                        doc2.recompute()
                        for key, names in want.items():
                            j = doc2.getObject(key.split(".")[0])
                            ref = getattr(j, key.split(".")[1])
                            self.assertEqual([s.rsplit(".", 1)[-1] for s in ref[1]], names)
                            self.assertFalse(j.Suppressed)
                            self.assertNotIn("Invalid", j.State)
                        asm2 = doc2.getObject("Assembly")
                        self.assertEqual(asm2.solve(), 0)
                    finally:
                        App.closeDocument(doc2.Name)
                    doc = None
                finally:
                    if doc is not None:
                        App.closeDocument(doc.Name)
                    shutil.rmtree(tmp, ignore_errors=True)

    def test_workbench_example_cases(self):
        path = C.example_path()
        if path is None:
            self.skipTest("data/examples/AssemblyExample.FCStd not found from this tree")
        for part in C.EXAMPLE_PARTS:
            with self.subTest(part=part):
                doc = App.newDocument("Rc_ex")
                b = None
                try:
                    b = C.build_example_case(doc, part)
                    self.assertTrue(b.truth, part)
                    rep = R.plan(b.asm, b.old, b.new)
                    stats, probs = classify(rep, b.truth)
                    print("\n  example %s: %s" % (part, stats))
                    self.assertEqual(probs, [])
                    self.assertEqual(stats["wrong"], 0)
                    sig = R.signature(b.doc)
                    before = [App.Placement(o.Placement) for o in b.moving]
                    rep2 = R.apply(b.asm, b.old, b.new)
                    problems = check_after_apply(b, rep2, {"placements": before})
                    self.assertEqual(problems, [])
                    b.doc.undo()
                    self.assertEqual(R.signature(b.doc), sig)
                finally:
                    if b is not None:
                        App.closeDocument(b.doc.Name)
                    App.closeDocument(doc.Name)

    def test_pdm_vault_revision(self):
        tmp = tempfile.mkdtemp(prefix="rc")
        doc = App.newDocument("Rc_pdm")
        b = None
        try:
            try:
                b = C.build_pdm_case(doc, tmp)
            except Exception as exc:
                self.skipTest("PDM vault not usable here: %s" % exc)
            if b is None:
                self.skipTest("PDM module or git missing")
            rep = R.plan(b.asm, b.old, b.new, **b.kwargs)
            stats, probs = classify(rep, b.truth)
            self.assertEqual(probs, [])
            self.assertEqual((stats["ok"], stats["wrong"]), (1, 0))
            rep2 = R.apply(b.asm, b.old, b.new, **b.kwargs)
            self.assertEqual(check_after_apply(b, rep2, {"placements": []}), [])
            self.assertEqual(b.old.LinkedObject.Document.Name, b.new.Document.Name)
        finally:
            for d in getattr(b, "extra_docs", []) or []:
                App.closeDocument(d.Name)
            App.closeDocument(doc.Name)
            shutil.rmtree(tmp, ignore_errors=True)

    def test_mutants_are_caught(self):
        self.assertGreaterEqual(len(MUTANTS), 8)
        self.assertEqual(broken_machinery(), [], "the unmutated broken-joint machinery must pass")
        table = []
        for mname, (_inst, catcher) in MUTANTS.items():
            with self.subTest(mutant=mname), mutated(mname):
                failing = []
                if catcher.startswith("@"):
                    if DEDICATED[catcher[1:]]():
                        failing.append(catcher)
                else:
                    order = [catcher] + [n for n in C.CASES if n != catcher]
                    for name in order:
                        res = run_case(name)
                        if res["problems"]:
                            failing.append(name)
                        if name == catcher and not failing:
                            break
                table.append((mname, catcher, failing))
                self.assertTrue(failing, "mutant %s survived" % mname)
                self.assertIn(catcher, failing, "mutant %s not caught by %s" % (mname, catcher))
        print("\n  mutants (%d): mutant / catching case / cases failing (of %d)" % (len(table), len(C.CASES)))
        for mname, catcher, failing in table:
            print("    %-42s %-52s %d" % (mname, catcher, len(failing)))
        for name in ("renumber_revolute", "direct_part_fixed"):
            self.assertEqual(run_case(name)["problems"], [], name)

    def test_broken_joint_marker_findplacement_unsuppress_and_repair(self):
        self.assertEqual(broken_machinery(), [])

    def test_user_suppression_survives_break_and_repair_and_offset_edit_is_quiet(self):
        self.assertEqual(suppression_and_offset_machinery(), [])

    def test_ambiguous_alignment_is_resolved_by_giving_one(self):
        doc = App.newDocument("Rc_align")
        try:
            b = C.build_case(doc, "e3_bar_turned_about_z_boss_top_and_end")
            rep = R.plan(b.asm, b.old, b.new)
            self.assertEqual(rep["alignment"]["method"], "ambiguous")
            alts = rep["alignment"]["alternatives"]
            self.assertTrue(alts)
            statuses = {e["status"] for j in rep["joints"] for r in j["references"] for e in r["elements"]}
            self.assertEqual(statuses, {"ambiguous"})
            # as placed: the boss moved and the +x end stays; turned: the boss stays and the other end is the +x end
            ident = R.plan(b.asm, b.old, b.new, alignment=App.Placement())
            self.assertEqual(ident["alignment"]["method"], "given")
            turned = R.plan(b.asm, b.old, b.new,
                            alignment=App.Placement(App.Vector(*alts[0]["base"]), App.Rotation(*alts[0]["rotation"])))
            for r in (ident, turned):
                self.assertEqual({e["status"] for j in r["joints"] for rr in j["references"] for e in rr["elements"]},
                                 {"matched"})
            ends = lambda r: [e["new_element"] for j in r["joints"] if j["joint"] == "J1"
                              for rr in j["references"] for e in rr["elements"]][0]
            self.assertNotEqual(ends(ident), ends(turned), "the two readings put the +x end on different faces")
            self.assertEqual(ends(ident), C.one(C.local(b.new), C.pl((1, 0, 0), 100)))
        finally:
            App.closeDocument(doc.Name)

    def test_alignment_candidates_contract(self):
        def counts_of(r):
            return r["references"]

        def plc_of(d):
            return App.Placement(App.Vector(*d["base"]), App.Rotation(*d["rotation"]))

        for name, turned_label in (("n1_bracket_turned_and_gained_a_small_hole", "turned 180\u00b0 about Y"),
                                   ("n2_bracket_turned_and_gained_a_boss", "turned 180\u00b0 about Y"),
                                   ("n5_symmetric_plate_turned_and_gained_an_offcentre_hole", "turned 180\u00b0 about Y"),
                                   ("n6_symmetric_plate_turned_about_y_edge_only_reference", "turned 180\u00b0 about Y"),
                                   ("n7_symmetric_plate_turned_about_x_edge_only_reference", "turned 180\u00b0 about X"),
                                   ("n8_symmetric_plate_turned_vertex_only_reference", "turned 180\u00b0 about Y"),
                                   ("n11_bracket_turned_and_lost_a_chamfered_end", "turned 178\u00b0 about Y"),   # an inertia alignment, not exact
                                   ("n17_plate_with_two_holes_replaced_by_the_plain_plate_turned_about_y", "turned 180\u00b0 about Y"),
                                   ("n18_plate_with_two_holes_replaced_by_the_plain_plate_turned_about_x", "turned 180\u00b0 about X"),
                                   ("e3_bar_turned_about_z_boss_top_and_end", "turned 180\u00b0 about Z")):
            with self.subTest(case=name):
                doc = App.newDocument("Rc_cand")
                try:
                    b = C.build_case(doc, name)
                    rep = R.plan(b.asm, b.old, b.new)
                    json.dumps(rep)
                    al = rep["alignment"]
                    self.assertEqual(al["method"], "ambiguous")
                    cands = al["candidates"]
                    self.assertEqual(cands[0]["label"], "as placed")
                    self.assertEqual(cands[0]["placement"], R._plc_json(App.Placement()))
                    self.assertIn(turned_label, [c["label"] for c in cands])
                    self.assertGreaterEqual(len(cands), 2)
                    for c in cands:
                        self.assertEqual(set(c), {"label", "placement", "counts", "error"})
                        self.assertEqual(set(c["counts"]), {"matched", "ambiguous", "unmatched"})
                        again = R.plan(b.asm, b.old, b.new, alignment=plc_of(c["placement"]))
                        self.assertEqual(again["alignment"]["method"], "given")
                        self.assertEqual(again["alignment"]["candidates"], [])
                        self.assertEqual(counts_of(again), c["counts"], c["label"])
                    # the readings differ: the turned one resolves what as placed does not (or resolves it differently)
                    news = lambda r: [e["new_element"] for j in r["joints"] for rr in j["references"] for e in rr["elements"]]
                    self.assertNotEqual(news(R.plan(b.asm, b.old, b.new, alignment=plc_of(cands[0]["placement"]))),
                                        news(R.plan(b.asm, b.old, b.new, alignment=plc_of(cands[1]["placement"]))))
                    # apply with the turned candidate reproduces its counts too
                    done = R.apply(b.asm, b.old, b.new, alignment=plc_of(cands[1]["placement"]))
                    self.assertEqual(counts_of(done), cands[1]["counts"])
                    # a given alignment is never reported as ambiguous
                    self.assertEqual(done["alignment"]["method"], "given")
                finally:
                    App.closeDocument(doc.Name)

    def test_readings_differ_rules(self):
        V = App.Vector
        a = {"r": ("matched", "Face7", V(30, 6, 5))}
        self.assertEqual(R._readings_differ(a, {"r": ("matched", "Face7", V(30, 6, 5))}), [])
        # the same name at another place differs only when a tolerance is asked for
        far = {"r": ("matched", "Face7", V(10, 6, 5))}
        self.assertEqual(R._readings_differ(a, far), [])
        self.assertEqual(len(R._readings_differ(a, far, tol=1.0)), 1)
        self.assertEqual(R._readings_differ(a, {"r": ("matched", "Face7", V(30.5, 6, 5))}, tol=1.0), [])
        # another element differs
        self.assertEqual(len(R._readings_differ(a, {"r": ("matched", "Face8", V(30, 6, 5))})), 1)
        # matched against not matched differs, unless only references both readings match count
        gone = {"r": ("unmatched", None, None)}
        self.assertEqual(len(R._readings_differ(a, gone)), 1)
        self.assertEqual(R._readings_differ(a, gone, both=True), [])
        self.assertEqual(len(R._readings_differ(a, {"r": ("matched", "Face8", V(30, 6, 5))}, both=True)), 1)
        # neither matched: nothing to tell apart
        self.assertEqual(R._readings_differ(gone, {"r": ("ambiguous", None, None)}), [])

    def test_unambiguous_alignment_has_no_candidates(self):
        for name, method in (("n3_control_plain_revision_gained_a_small_hole", "identity"),
                             ("n4_control_plain_revision_gained_a_boss", "identity"),
                             ("n13_control_symmetric_plate_gained_a_hole_reference_is_the_centre_hole", "identity"),
                             ("n14_control_asymmetric_bracket_gained_a_tiny_hole_far_away", "identity"),
                             ("n15_control_symmetric_plate_resized_keeping_the_symmetry", "identity"),   # a resize: as placed (was inertia before the resize exemption)
                             ("n16_control_symmetric_plate_made_thicker", "identity"),
                             ("n19_control_plate_with_two_holes_replaced_by_the_plain_plate_reference_is_the_centre_hole", "identity")):
            with self.subTest(case=name):
                doc = App.newDocument("Rc_ctl")
                try:
                    b = C.build_case(doc, name)
                    rep = R.plan(b.asm, b.old, b.new)
                    self.assertEqual(rep["alignment"]["method"], method)
                    self.assertEqual(rep["alignment"]["candidates"], [])
                    self.assertEqual(rep["references"]["matched"], 1)
                finally:
                    App.closeDocument(doc.Name)

    def test_choices_dict_shape_and_candidate_basis(self):
        doc = App.newDocument("Rc_choices")
        try:
            b = C.build_case(doc, "sym_two_holes_revolute")
            plan = R.plan(b.asm, b.old, b.new)
            ref = plan["joints"][0]["references"][0]
            amb = [e for e in ref["elements"] if e["status"] == "ambiguous"]
            self.assertTrue(amb)
            cands = amb[0]["candidates"]
            self.assertGreaterEqual(len(cands), 2)
            self.assertEqual(set(cands[0]), {"element", "entity", "cost", "basis"})
            key = "%s.%s" % (plan["joints"][0]["joint"], ref["reference"])
            pick = {amb[0]["old_element"]: cands[1]["element"]}
            rep = R.plan(b.asm, b.old, b.new, choices={key: pick})
            e = [x for x in element_reports(rep, key)["elements"] if x["old_element"] == amb[0]["old_element"]][0]
            self.assertEqual((e["status"], e["new_element"], e["basis"]), ("matched", cands[1]["element"], "user"))
            # a choice for a reference that is not ambiguous is ignored
            self.assertEqual(R.plan(b.asm, b.old, b.new, choices={"Nope.Reference1": "Face1"})["references"],
                             plan["references"])
            rep = R.apply(b.asm, b.old, b.new, choices={key: pick})
            j = doc.getObject(plan["joints"][0]["joint"])
            self.assertIn(cands[1]["element"], getattr(j, ref["reference"])[1][0])
        finally:
            App.closeDocument(doc.Name)

    def test_delete_old_keeps_an_object_linked_from_another_document(self):
        tmp = tempfile.mkdtemp(prefix="rc")
        doc = App.newDocument("Rc_xdoc")
        other = App.newDocument("Rc_xdoc2")
        try:
            b = C.build_case(doc, "t9_delete_old_direct_part_nobody_else_uses")
            doc.saveAs(os.path.join(tmp, "a.FCStd"))
            other.saveAs(os.path.join(tmp, "b.FCStd"))
            lk = other.addObject("App::Link", "ExternalUse")
            lk.LinkedObject = b.old
            rep = R.apply(b.asm, b.old, b.new, delete_old=True)
            self.assertIsNotNone(doc.getObject("Old"), "an object linked from another document was deleted")
            self.assertEqual([k["name"] for k in rep["kept_old"]], ["Old"])
            self.assertTrue(any("Rc_xdoc2:ExternalUse" in u for u in rep["kept_old"][0]["users"]), rep["kept_old"])
            self.assertEqual(rep["deleted_old"], [])
            self.assertEqual(lk.LinkedObject.Name, "Old")
        finally:
            App.closeDocument(other.Name)
            App.closeDocument(doc.Name)
            shutil.rmtree(tmp, ignore_errors=True)

    def test_importing_a_part_from_another_document_leaves_that_document_as_it_was(self):
        # Document.copyObject marks the objects it copies from with a hidden _ObjectUUID that the next save of their document
        # would write; a part taken from a document the user has open is used as it is
        doc = App.newDocument("Rc_imp")
        other = App.newDocument("Rc_imp_src")
        try:
            b = C.build_case(doc, "renumber_revolute")
            src = other.addObject("Part::Feature", "SrcPart")
            src.Shape = b.new.Shape
            other.recompute()
            before = (R.signature(other), other.isTouched(), other.UndoCount)
            rep = R.apply(b.asm, b.old, src, import_foreign=True)
            self.assertTrue(rep["imported"], rep)
            self.assertNotIn("_ObjectUUID", src.PropertiesList)
            self.assertEqual((R.signature(other), other.isTouched(), other.UndoCount), before)
            # a source object that carried the mark already keeps it
            src2 = other.addObject("Part::Feature", "SrcPart2")
            src2.Shape = b.new.Shape
            src2.addProperty("App::PropertyUUID", "_ObjectUUID", "Base", "", 0, False, True)
            R._forget_copy_marks(other, {"SrcPart2"})
            self.assertIn("_ObjectUUID", src2.PropertiesList)
        finally:
            App.closeDocument(other.Name)
            App.closeDocument(doc.Name)

    def test_two_runs_identical(self):
        a = run_case("resize_slider", checks=("plan",))["plan"]
        b = run_case("resize_slider", checks=("plan",))["plan"]
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
