# SPDX-License-Identifier: LGPL-2.1-or-later
"""Assembly interference and clearance (G06 slice 1a): a JSON-returning Python API.

``check(assembly, ...)`` walks every solid of an Assembly (through ``App::Link``, link
arrays, nested parts and sub-assemblies, in the placement FreeCAD resolves for each one)
and classifies each pair of visible solids as ``interfering`` (exact Boolean common above
the volume tolerance), ``touching`` (common volume at or below it and minimum distance
within the contact tolerance) or ``close`` (distance below the requested clearance).

The pairwise primitives (bounding-box test, OCCT ``BRepAlgoAPI_Common``, OCCT
``BRepExtrema_DistShapeShape`` through ``Shape.distToShape``) are F2's, in
``AgentCore.verify.checks``; this module adds the assembly walk, the broad phase over
cached boxes, the touching class, the ignore rules and the clearance sets.

Behaviour sources, all public: OCCT Boolean operations and BRepExtrema documentation
(dev.opencascade.org); FreeCAD's App::Link documentation (wiki.freecad.org/App_Link),
whose ``Part.getShape(..., transform=True)`` resolves the placement of a sub-object path.
Hidden parts follow the object ``Visibility`` property of each object on the path.

Which shapes are checked: only shapes that contain at least one solid (a solid, or a
compound/compsolid holding solids). Every other visible shape (face, wire, edge, open or
closed shell, sketch geometry) is listed in ``skipped_non_solid`` with its shape type; a closed
shell is not a solid and is listed, not checked. A visible leaf that cannot be resolved (a link
whose target is gone, a Part::Feature with a null or invalid shape, a failed placement lookup)
is an entry in ``errors`` with kind ``unresolved`` and a reason; it is never dropped silently.
Hidden leaves are not resolved and only counted (``hidden_skipped``: hidden leaves that hold
solids). Objects that carry no geometry at all (empty groups, origins) are ignored.

Limits (slice 1a): the broad phase is a sweep along X followed by a box test, quadratic in
the worst case; large assemblies are G05's work. The check does not solve joints: it reads
the placements the assembly currently holds.
"""

import json
import re
import time

import FreeCAD as App
import Part

from AgentCore.verify import checks as _core
from AgentCore.verify.entities import entity
from AgentCore.verify.tolerances import resolve as _resolve_tol

SCHEMA = "cadfork.assembly.interference/1"
CLEARANCE_PROP = "ClearanceSets"

_SKIP_TYPES = (
    "Assembly::JointGroup",
    "Assembly::ViewGroup",
    "Assembly::BomGroup",
    "Assembly::SimulationGroup",
    "Assembly::SnapshotGroup",
    "App::Origin",
)
_CLASS_ORDER = {"interfering": 0, "touching": 1, "close": 2}


class Instance:
    """One solid occurrence in the assembly (a leaf of the walk)."""

    __slots__ = ("key", "labels", "objs", "component", "hidden", "shape", "box", "idx")

    def __init__(self, key, labels, objs, component, hidden):
        self.key = key
        self.labels = labels
        self.objs = objs
        self.component = component
        self.hidden = hidden
        self.shape = None
        self.box = None
        self.idx = -1

    @property
    def label(self):
        return self.labels[-1]

    def describe(self, root):
        comp_obj = root.getSubObject(self.component + ".", 1)
        return {
            "label": self.label,
            "link_path": list(self.labels),
            "path": self.key,
            "entity": entity(comp_obj) if comp_obj is not None else self.key,
        }


# ------------------------------------------------------------------ walking

_LINK_TYPES = ("App::Link", "App::LinkElement", "Assembly::AssemblyLink")


class _Unresolved(Exception):
    """A visible leaf whose shape cannot be obtained (the message is the reason)."""


def _is_suppressed(obj):
    return obj.TypeId == "App::LinkElement" and bool(getattr(obj, "Suppressed", False))


def _is_array_element(parent, sub):
    """True for a child of a link array whose elements are not shown as LinkElement objects."""
    return (parent.TypeId == "App::Link" and parent.ElementCount > 0 and not parent.ShowElement
            and re.fullmatch(r"\d+\.", sub) is not None)


def _walk(root):
    """Every geometry-candidate leaf below ``root`` as an Instance (hidden ones flagged)."""
    out = []

    def rec(path, objs, labels, hidden):
        parent = objs[-1] if objs else root
        for sub in parent.getSubObjects():
            cpath = path + sub
            child = root.getSubObject(cpath, 1)
            if child is None or child.TypeId in _SKIP_TYPES or _is_suppressed(child):
                continue
            if _is_array_element(parent, sub):
                # ShowElement=False: the child is the linked source; visibility follows the array
                # (already in ``hidden``) and the element's own flag, never the source object.
                idx = int(sub[:-1])
                label = "%s[%d]" % (parent.Label, idx)
                chidden = hidden or parent.isElementVisible(sub[:-1]) == 0
            else:
                label = child.Label
                chidden = hidden or (getattr(child, "Visibility", True) is False)
            cobjs = objs + [child]
            clabels = labels + [label]
            if not child.isDerivedFrom("Part::Feature") and child.getSubObjects():
                rec(cpath, cobjs, clabels, chidden)
                continue
            key = cpath[:-1] if cpath.endswith(".") else cpath
            out.append(Instance(key, clabels, cobjs, _component_key(key, cobjs), chidden))

    rec("", [], [], False)
    return out


def _component_key(key, objs):
    """The part this leaf belongs to: its enclosing App::Part or plain Link, else itself.

    A plain link to a sub-assembly is not one part: the sub-assembly's own parts stay separate,
    exactly as when the sub-assembly is contained, so its internal pairs are reported once, in
    the assembly that reaches it (F2 does not check such a sub-assembly as a top level).
    """
    if len(objs) >= 2:
        par = objs[-2]
        if par.TypeId == "App::Part" or (par.TypeId == "App::Link" and par.ElementCount == 0
                                         and par.getLinkedObject(True).TypeId != "Assembly::AssemblyObject"):
            return key.rsplit(".", 1)[0]
    return key


def _is_hidden(inst):
    return inst.hidden


def _empty_by_design(lo):
    """An empty sketch, or a PartDesign Body with no features yet: shapeless without anything being wrong."""
    return lo.TypeId.startswith("Sketcher::") or (lo.TypeId == "PartDesign::Body" and getattr(lo, "Tip", None) is None)


def _null_reason(lo):
    state = list(getattr(lo, "State", []))
    if "Invalid" in state or "Error" in state:
        return "null shape (%s failed to recompute)" % lo.Label
    if "Touched" in state:
        return "null shape (%s has not been recomputed)" % lo.Label
    return "null shape (%s)" % lo.Label


def _leaf_kind(inst):
    """("geometry" | "none" | "error", reason) for a leaf, from the objects alone (no placement)."""
    obj = inst.objs[-1]
    try:
        lo = obj.getLinkedObject(True)
    except Exception as exc:
        return "error", "link cannot be resolved: %s" % exc
    is_link = obj.TypeId in _LINK_TYPES
    if lo is None or (is_link and lo.TypeId in _LINK_TYPES):
        return "error", "link target is missing"
    if not hasattr(lo, "Shape"):
        if is_link and not lo.getSubObjects():
            return "error", "link target %s has no shape" % lo.Label
        return "none", ""
    shp = lo.Shape
    if shp.isNull():
        if _empty_by_design(lo):
            return "geometry", ""  # listed as a non-solid, not an error
        return "error", _null_reason(lo)
    return "geometry", ""


def _raw_has_solids(inst):
    lo = inst.objs[-1].getLinkedObject(True)
    shp = getattr(lo, "Shape", None)
    return shp is not None and not shp.isNull() and bool(shp.Solids)


def _instance_shape(root, inst):
    """The leaf's shape in the assembly's global frame; raises _Unresolved when it has none."""
    try:
        shp = Part.getShape(root, inst.key + ".", needSubElement=False, transform=True)
    except Exception as exc:
        raise _Unresolved("shape lookup failed: %s" % exc)
    if shp is None or shp.isNull():
        if _empty_by_design(inst.objs[-1].getLinkedObject(True)):
            return Part.Shape()
        raise _Unresolved("shape is null")
    if not shp.isValid():
        raise _Unresolved("shape is invalid")
    return shp


def _note_error(errors, root, inst, reason):
    errors.append({"kind": "unresolved", **inst.describe(root), "reason": reason})


class Resolved:
    """Result of resolving every leaf: solids to check, and everything that was set aside."""

    def __init__(self):
        self.insts = []
        self.errors = []
        self.bad = []       # (Instance, reason) for each entry in errors
        self.skipped = []   # visible leaves without solids
        self.hidden = 0     # hidden leaves holding solids


def resolve(assembly, include_hidden=False, apply_features=True):
    res = Resolved()
    for inst in _walk(assembly):
        kind, why = _leaf_kind(inst)
        if kind == "none":
            continue
        if _is_hidden(inst) and not include_hidden:
            if kind == "geometry" and _raw_has_solids(inst):
                res.hidden += 1
            continue
        if kind == "error":
            _note_error(res.errors, assembly, inst, why)
            res.bad.append((inst, why))
            continue
        try:
            shp = _instance_shape(assembly, inst)
            if shp is None:
                raise _Unresolved("no shape returned")
        except _Unresolved as exc:
            _note_error(res.errors, assembly, inst, str(exc))
            res.bad.append((inst, str(exc)))
            continue
        if shp.isNull() or not shp.Solids:
            rec = inst.describe(assembly)
            rec["shape_type"] = shp.ShapeType if not shp.isNull() else "Empty"
            res.skipped.append(rec)
            continue
        inst.shape = shp
        inst.box = App.BoundBox(shp.BoundBox)
        inst.idx = len(res.insts)
        res.insts.append(inst)
    if apply_features:
        _apply_assembly_features(assembly, res)
    return res


def _apply_assembly_features(assembly, res):
    """Swap in the shape an active assembly feature (G06 slice 4a) leaves of a component.

    A component a feature removes entirely is listed in ``skipped_non_solid`` as ``Empty``.
    """
    if not any(o.TypeId == "App::FeaturePython" and getattr(o, "Kind", None) in ("cut", "hole")
               and not o.Suppressed for o in assembly.Group):
        return
    import AssemblyFeature
    eff = AssemblyFeature.apply_to_instances(assembly, res.insts)
    if not eff:
        return
    kept = []
    for inst in res.insts:
        shp = eff.get(inst.key)
        if shp is not None:
            if shp.isNull() or not shp.Solids:
                rec = inst.describe(assembly)
                rec["shape_type"] = "Empty"
                res.skipped.append(rec)
                continue
            inst.shape = shp
            inst.box = App.BoundBox(shp.BoundBox)
        kept.append(inst)
    for n, inst in enumerate(kept):
        inst.idx = n
    res.insts = kept


def instances(assembly, include_hidden=False):
    """Solid instances with shapes and cached bounding boxes (hidden ones only on request)."""
    return resolve(assembly, include_hidden).insts


# ------------------------------------------------------------- ignore rules

def _matches(inst, sel):
    sel = sel.rstrip(".")
    return sel in (inst.key, inst.label) or inst.key.startswith(sel + ".")


def _ignored(a, b, ignore, patterns):
    for rule in ignore:
        if isinstance(rule, str):
            if _matches(a, rule) or _matches(b, rule):
                return True
        elif (_matches(a, rule[0]) and _matches(b, rule[1])) or (
            _matches(a, rule[1]) and _matches(b, rule[0])
        ):
            return True
    for rx in patterns:
        if rx.search(a.label) or rx.search(b.label):
            return True
    return False


def _same_part(a, b):
    return a.component == b.component


# ------------------------------------------------------------ broad phase

def _broad_phase(insts, pairs_ok, margin):
    """Candidate index pairs whose boxes, enlarged by ``margin``, overlap (sweep along X)."""
    order = sorted(range(len(insts)), key=lambda i: insts[i].box.XMin)
    cand = []
    for oi, i in enumerate(order):
        bi = insts[i].box
        reach = bi.XMax + margin
        for j in order[oi + 1:]:
            bj = insts[j].box
            if bj.XMin > reach:
                break
            lo, hi = (i, j) if i < j else (j, i)
            if pairs_ok(insts[lo], insts[hi]) and _core.bbox_overlap(bi, bj, margin):
                cand.append((lo, hi))
    cand.sort()
    return cand


# ------------------------------------------------------------- exact stage

def _classify(a, b, clearance, contact, vol_tol):
    """(class or None, volume, distance, points) for one candidate pair."""
    vol = 0.0
    if _core.bbox_overlap(a.box, b.box, contact):
        vol = _core.common_volume(a.shape, b.shape)
        if vol > vol_tol:
            return "interfering", vol, 0.0, None
    dist, pts, _info = _core.min_distance(a.shape, b.shape)
    pts_out = None
    if pts:
        pts_out = [[float(p.x), float(p.y), float(p.z)] for p in pts[0]]
    if dist <= contact:
        return "touching", vol, dist, pts_out
    if dist < clearance:
        return "close", vol, dist, pts_out
    return None, vol, dist, pts_out


def _normalise_ignore(ignore):
    out = []
    for r in ignore or []:
        if isinstance(r, str):
            out.append(r)
            continue
        r = tuple(r)
        if len(r) != 2 or not all(isinstance(x, str) for x in r):
            raise ValueError("ignore entries are a selector string or a pair of two selector strings, got %r" % (r,))
        out.append(r)
    return out


def check(assembly, contact_tol=None, clearance=0.0, ignore=None, ignore_names=None,
          include_hidden=False, volume_tol=None):
    """Interference / touching / clearance report for one assembly, JSON-serialisable.

    ``contact_tol`` (mm, default tolerances.json ``contact_mm``): pairs whose common volume
    is at most ``volume_tol`` (default ``interference_volume_mm3``) and whose minimum
    distance is at most this are ``touching``. ``clearance`` (mm): pairs closer than this
    but not touching are ``close``. ``ignore``: part selectors (a string excludes that part
    from every pair) or [a, b] selector pairs; a selector is an instance path, a label, or
    a path prefix. ``ignore_names``: regular expressions on labels (e.g. fastener threads).
    Pairs of solids inside one part (an App::Part or one plain link) are never reported.
    """
    t0 = time.perf_counter()
    tol = _resolve_tol(None)
    contact = tol["contact_mm"] if contact_tol is None else float(contact_tol)
    vol_tol = tol["interference_volume_mm3"] if volume_tol is None else float(volume_tol)
    clearance = float(clearance or 0.0)
    ignore = _normalise_ignore(ignore)
    patterns = [re.compile(p) for p in (ignore_names or [])]

    res = resolve(assembly, include_hidden=include_hidden)
    insts = res.insts
    t1 = time.perf_counter()

    n = len(insts)
    pairs_total = n * (n - 1) // 2
    skipped = {"same_part": 0, "ignored": 0}

    def pairs_ok(a, b):
        return not _same_part(a, b) and not _ignored(a, b, ignore, patterns)

    margin = clearance + contact
    cand = _broad_phase(insts, pairs_ok, margin)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = insts[i], insts[j]
            if _same_part(a, b):
                skipped["same_part"] += 1
            elif _ignored(a, b, ignore, patterns):
                skipped["ignored"] += 1
    t2 = time.perf_counter()

    reports, errors, clear = [], list(res.errors), 0
    for i, j in cand:
        a, b = insts[i], insts[j]
        try:
            cls, vol, dist, pts = _classify(a, b, clearance, contact, vol_tol)
        except Exception as exc:  # OCCT failures are reported, never hidden
            errors.append({"kind": "pair", "a": a.key, "b": b.key, "error": str(exc)})
            continue
        if cls is None:
            clear += 1
            continue
        reports.append({
            "class": cls,
            "a": a.describe(assembly),
            "b": b.describe(assembly),
            "volume_mm3": float(vol) if cls in ("interfering", "touching") else 0.0,
            "distance_mm": float(dist),
            "points": pts,
        })
    reports.sort(key=lambda r: (_CLASS_ORDER[r["class"]], r["a"]["path"], r["b"]["path"]))
    t3 = time.perf_counter()

    considered = pairs_total - skipped["same_part"] - skipped["ignored"]
    discard = 1.0 - len(cand) / considered if considered > 0 else 1.0
    return {
        "schema": SCHEMA,
        "assembly": {"name": assembly.Name, "label": assembly.Label},
        "params": {"contact_tol_mm": contact, "volume_tol_mm3": vol_tol, "clearance_mm": clearance,
                   "include_hidden": bool(include_hidden), "ignore": [list(r) if not isinstance(r, str) else r
                                                                   for r in ignore],
                   "ignore_names": list(ignore_names or [])},
        "pairs": reports,
        "errors": errors,
        "skipped_non_solid": res.skipped,
        "stats": {
            "instances": n,
            "unresolved": len(res.errors),
            "non_solid_skipped": len(res.skipped),
            "hidden_skipped": 0 if include_hidden else res.hidden,
            "pairs_total": pairs_total,
            "pairs_same_part": skipped["same_part"],
            "pairs_ignored": skipped["ignored"],
            "pairs_considered": considered,
            "pairs_after_broad_phase": len(cand),
            "exact_checks": len(cand),
            "broad_phase_discard": discard,
            "pairs_clear_after_exact": clear,
            "counts": {c: sum(1 for r in reports if r["class"] == c) for c in _CLASS_ORDER},
            "timings_s": {"walk_and_shapes": t1 - t0, "broad_phase": t2 - t1,
                          "exact": t3 - t2, "total": t3 - t0},
        },
    }


# ---------------------------------------------------------- clearance sets
# Stored on the assembly as a dynamic App::PropertyStringList "ClearanceSets", one JSON
# string per set: {"name", "min_mm", "pairs": [[selectorA, selectorB], ...]}. Dynamic
# properties of C++ objects are written to and restored from the .FCStd file.

def _prop(assembly, create):
    if CLEARANCE_PROP not in assembly.PropertiesList:
        if not create:
            return None
        assembly.addProperty("App::PropertyStringList", CLEARANCE_PROP, "Interference",
                             "Named clearance sets (JSON), see Assembly/Interference.py")
    return getattr(assembly, CLEARANCE_PROP)


def get_clearance_sets(assembly):
    raw = _prop(assembly, False) or []
    return [json.loads(s) for s in raw]


def set_clearance_set(assembly, name, min_mm, pairs):
    """Create or replace the set ``name``: every listed pair must be at least ``min_mm`` apart."""
    sets = [s for s in get_clearance_sets(assembly) if s["name"] != name]
    sets.append({"name": str(name), "min_mm": float(min_mm),
                 "pairs": [[str(a), str(b)] for a, b in pairs]})
    _prop(assembly, True)
    setattr(assembly, CLEARANCE_PROP, [json.dumps(s, sort_keys=True) for s in sets])
    return sets[-1]


def remove_clearance_set(assembly, name):
    sets = get_clearance_sets(assembly)
    keep = [s for s in sets if s["name"] != name]
    if _prop(assembly, False) is not None:
        setattr(assembly, CLEARANCE_PROP, [json.dumps(s, sort_keys=True) for s in keep])
    return len(keep) != len(sets)


def check_clearance_sets(assembly, sets=None):
    """Distance of every pair of every set against its required minimum, JSON-serialisable.

    Selectors resolve against all solids including hidden ones (an explicit pair is
    honoured). A selector naming several solids (a part with many) uses the closest combination.
    """
    t0 = time.perf_counter()
    slack = _resolve_tol(None)["clearance_abs_mm"]
    res = resolve(assembly, include_hidden=True)
    insts = res.insts
    sets = get_clearance_sets(assembly) if sets is None else sets
    out_sets, violations, unresolved = [], [], []
    for st in sets:
        rows = []
        for sel_a, sel_b in st["pairs"]:
            ma = [i for i in insts if _matches(i, sel_a)]
            mb = [i for i in insts if _matches(i, sel_b)]
            if not ma or not mb:
                miss = sel_a if not ma else sel_b
                why = [i.label + ": " + r for i, r in res.bad if _matches(i, miss)]
                rec = {"set": st["name"], "a": sel_a, "b": sel_b,
                       "error": ("selector matches an unresolved part (" + "; ".join(why) + "): " + miss) if why
                       else "selector matches no solid: " + miss}
                unresolved.append(rec)
                rows.append(rec)
                continue
            best = None
            for x in ma:
                for y in mb:
                    if x is y:
                        continue
                    d, _p, _i = _core.min_distance(x.shape, y.shape)
                    if best is None or d < best[0]:
                        best = (d, x, y)
            if best is None:
                rec = {"set": st["name"], "a": sel_a, "b": sel_b, "error": "selectors name the same solid"}
                unresolved.append(rec)
                rows.append(rec)
                continue
            d, x, y = best
            rec = {"set": st["name"], "required_mm": st["min_mm"], "distance_mm": float(d),
                   "ok": d >= st["min_mm"] - slack,
                   "a": x.describe(assembly), "b": y.describe(assembly)}
            rows.append(rec)
            if not rec["ok"]:
                violations.append(rec)
        out_sets.append({"name": st["name"], "min_mm": st["min_mm"], "pairs": rows})
    return {
        "schema": SCHEMA, "assembly": {"name": assembly.Name, "label": assembly.Label},
        "sets": out_sets, "violations": violations, "unresolved": unresolved,
        "errors": res.errors, "skipped_non_solid": res.skipped,
        "ok": not violations and not unresolved,
        "stats": {"sets": len(out_sets), "pairs": sum(len(s["pairs"]) for s in out_sets),
                  "time_s": time.perf_counter() - t0},
    }
