# SPDX-License-Identifier: LGPL-2.1-or-later
# Run every F6 refactor op over ONE document, each op on a fresh open, under invariant().
# Runs INSIDE FreeCADCmd (QT_QPA_PLATFORM=offscreen):
#   CADFORK_F6_AGENTCORE=<src/Mod/AgentCore> CADFORK_F6_DOC=<file.FCStd> CADFORK_F6_OUT=<result.json> \
#   FreeCADCmd -c "exec(open(THIS).read())" </dev/null
# Result: {"doc", "load": {"clean", "attempts", "errors"}, "ops": {op: {...}}}. A document whose own
# control run (an empty edit under invariant) fails is "unstable": excluded and named, never counted as a pass.
import json
import os
import sys
import time
import traceback

sys.path.insert(0, os.environ["CADFORK_F6_AGENTCORE"])
import FreeCAD  # noqa: E402

from AgentCore import refactor  # noqa: E402
from AgentCore import state  # noqa: E402
from AgentCore.refactor import geomhash  # noqa: E402

try:
    from AgentCore.diff import AgentTurn, SnapshotStore
    from AgentCore.diff import compare as diff_compare
    from AgentCore.diff import snapshot_dump
except ImportError:  # F3 not in this tree
    AgentTurn = SnapshotStore = diff_compare = snapshot_dump = None

PATH = os.environ["CADFORK_F6_DOC"]
OUT = os.environ["CADFORK_F6_OUT"]
MAX_ATTEMPTS = 6
LIFT_LIMIT = None   # no cap: every candidate literal in every document is lifted
ORIGINAL_TYPES = ("PartDesign::Pad", "PartDesign::Pocket", "PartDesign::Revolution", "PartDesign::Groove")


def open_clean():
    """Open and force-recompute; retry like the golden probe, since a few upstream sketches solve flakily."""
    errors = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        doc = FreeCAD.openDocument(PATH)
        doc.recompute(None, True)
        bad = [o.Name for o in doc.Objects if "Invalid" in o.State or "Error" in o.State]
        if not bad:
            return doc, attempt, []
        errors = bad
        FreeCAD.closeDocument(doc.Name)
        if attempt > 1 and bad == errors:
            pass
    doc = FreeCAD.openDocument(PATH)
    doc.recompute(None, True)
    return doc, MAX_ATTEMPTS, errors


def inject_noops(doc):
    """Append a one-occurrence LinearPattern to bodies that have an Original-capable feature.

    The pattern shape equals its base, so the document's geometry must be unchanged; returns the
    added names, or [] when none could be added without changing geometry (those are removed again).
    """
    base = geomhash.snapshot(doc, max_deviation_bodies=0)
    added = []
    for body in refactor.ops.bodies(doc):
        feats = [f for f in body.Group if f.TypeId in ORIGINAL_TYPES]
        sketches = [f for f in body.Group if f.TypeId == "Sketcher::SketchObject"]
        if not feats or not sketches or body.Tip is None:
            continue
        lp = body.newObject("PartDesign::LinearPattern", "F6Noop")
        lp.Originals = [feats[-1]]
        lp.Direction = (sketches[0], ["H_Axis"])
        lp.Length = 10.0
        lp.Occurrences = 1
        body.Tip = lp
        added.append(lp.Name)
    doc.recompute(None, True)
    cur = geomhash.snapshot(doc, max_deviation_bodies=0)
    ok = not geomhash.compare(base, cur, added=added)
    still = [n for n in added if doc.getObject(n) is not None and not (set(doc.getObject(n).State) & {"Error", "Invalid"})]
    if not ok or len(still) != len(added):
        # roll the injection back by hand: remove the patterns and restore the tips
        for n in added:
            lp = doc.getObject(n)
            if lp is not None:
                b = refactor.ops.body_of(lp)
                prev = lp.BaseFeature
                if b is not None:
                    b.Tip = prev
                    b.removeObject(lp)
                doc.removeObject(n)
        doc.recompute(None, True)
        return [], base
    return added, base


def sabotage(doc):
    """A deliberate geometry change (mutation test at corpus scale): scale the first free Length
    property of a PartDesign or Part feature by 1.01, else nudge the first top-level solid 0.01 mm."""
    for c in refactor.lift_candidates(doc):
        o = doc.getObject(c["object"])
        if c["kind"] == "property" and o.getTypeIdOfProperty(c["property"]) == "App::PropertyLength" \
                and getattr(o, c["property"]).Value > 0 and o.TypeId.startswith(("PartDesign::", "Part::")):
            setattr(o, c["property"], getattr(o, c["property"]).Value * 1.01)
            return {"count": 1, "how": f"{o.Name}.{c['property']} x1.01"}
    from AgentCore.verify import checks
    for o in checks.bodies(doc):
        try:
            o.Placement = FreeCAD.Placement(o.Placement.Base + FreeCAD.Vector(0.01, 0, 0), o.Placement.Rotation)
            return {"count": 1, "how": f"{o.Name} moved 0.01 mm"}
        except Exception:
            continue
    return {"count": 0, "how": "nothing to sabotage"}


def _turn_and_reject(doc, body, entry):
    """Run ``body(doc)`` inside one AgentTurn (with F3's snapshot store as the fallback), reject the turn, and
    record whether F3's own equality, the F1/F3 canonical dump and the geometry snapshot all say "identical"."""
    import tempfile
    for _ in range(2):          # reach FreeCAD's recompute fixed point first (LinearPattern Spacings settle on the 2nd pass)
        doc.recompute(None, True)
    before_geo = geomhash.snapshot(doc)
    with tempfile.TemporaryDirectory(prefix="f6-reject-") as tmp:
        store = SnapshotStore(tmp)          # F3's before-state copy: the designed fallback when undo alone is not exact
        with AgentTurn(doc, verify=False, geometry_diff=False, store=store) as turn:
            before_state = snapshot_dump(doc)   # the document as the turn saw it at entry
            body(doc)
            doc.recompute(None, True)
        cs = turn.change_set()
        res = turn.reject()
    state_diff = diff_compare.differences(before_state, snapshot_dump(doc))     # F3's canonical dump, 1e-9 tolerance
    geo_diff = geomhash.compare(before_geo, geomhash.snapshot(doc))
    ok = res.get("equal", False) and not state_diff and not geo_diff
    entry.update({"status": "pass" if ok else "fail", "ok": ok, "method": res.get("method"),
                  "turn_equal": res.get("equal"), "state_differences": state_diff[:5],
                  "geometry_violations": geo_diff[:5], "turn_differences": res.get("differences", [])[:5],
                  "change_set_nonempty": bool(cs)})
    return entry


def run_reject(doc, entry):
    """F6 + F3: relabel, lift and merge inside one AgentTurn, then turn.reject()."""
    if AgentTurn is None:
        entry.update({"status": "skipped", "ok": None, "error": "AgentCore.diff.AgentTurn absent"})
        return entry
    added, _ = inject_noops(doc)
    entry["injected"] = added

    def body(d):
        refactor.relabel_all(d)
        refactor.lift_all(d, target="VarSet", limit=LIFT_LIMIT)
        refactor.merge_redundant(d)
    return _turn_and_reject(doc, body, entry)


def run_reject_baseline(doc, entry):
    """No F6 code at all: the turn does the same kinds of edit with plain FreeCAD calls (re-assign, to the same
    value, the first size property of every PartDesign feature; remove the injected no-op patterns the way a
    merge would, rewiring BaseFeature and Tip by hand). If this also fails to reject cleanly the cause is F3 or
    FreeCAD, not a refactor."""
    if AgentTurn is None:
        entry.update({"status": "skipped", "ok": None})
        return entry
    added, _ = inject_noops(doc)       # same pre-turn document as the reject run
    entry["injected"] = added

    def body(d):
        for o in d.Objects:
            if o.TypeId.startswith("PartDesign::") and o.TypeId != "PartDesign::Body" and o.Name not in added:
                for p in ("Length", "Angle", "Radius"):
                    if p in o.PropertiesList and not o.ExpressionEngine:
                        setattr(o, p, getattr(o, p).Value)
                        break
        for n in added:
            lp = d.getObject(n)
            if lp is None:
                continue
            b, prev = refactor.ops.body_of(lp), lp.BaseFeature
            if b is not None:
                b.Tip = prev
                b.removeObject(lp)
            d.removeObject(n)
    return _turn_and_reject(doc, body, entry)


def run_op(op):
    doc, attempts, errors = open_clean()
    t0 = time.time()
    entry = {"attempts": attempts}
    try:
        if errors:
            entry.update({"status": "unclean", "errors": errors})
            return entry
        if op == "reject":
            return run_reject(doc, entry)
        if op == "reject_baseline":
            return run_reject_baseline(doc, entry)
        if op == "control":
            fn = lambda d: None
        elif op == "relabel":
            fn = refactor.relabel_all
        elif op == "lift_varset":
            fn = lambda d: refactor.lift_all(d, target="VarSet", limit=LIFT_LIMIT)
        elif op == "lift_sheet":
            fn = lambda d: refactor.lift_all(d, target="Spreadsheet", limit=LIFT_LIMIT)
        elif op == "across_bodies":
            fn = lambda d: refactor.across_all_bodies(d)
        elif op == "merge":
            added, base = inject_noops(doc)
            entry["injected"] = added
            fn = lambda d: refactor.merge_redundant(d)
        elif op == "sabotage":
            fn = sabotage
        else:
            raise KeyError(op)
        res = refactor.invariant(doc, fn)
        r = res["result"] or {}
        entry.update({
            "status": res["status"], "ok": res["ok"], "violations": res["violations"][:5],
            "error": res["error"], "rolled_back": res["rolled_back"], "restored_identical": res["restored_identical"],
            "max_deviation": res["max_deviation"], "hash_before": res["before"]["hash"], "hash_after": res["after"]["hash"],
            "shape_objects": res["before"]["shape_objects"], "bodies": len(res["before"]["bodies"]),
            "effective": int(r.get("count", 0)) if isinstance(r, dict) else 0,
            "removed": list(r.get("removed", [])) if isinstance(r, dict) else [],
        })
        if isinstance(r, dict) and "skipped" in r:
            entry["skipped"] = len(r["skipped"]) if isinstance(r["skipped"], (list, dict)) else 0
        if op == "merge":
            if entry["injected"]:
                end = geomhash.snapshot(doc, max_deviation_bodies=40)
                entry["pre_existing_removed"] = [n for n in entry["removed"] if n not in entry["injected"]]
                entry["end_to_end"] = not geomhash.compare(base, end, removed=entry["removed"])
                entry["ok"] = entry["ok"] and entry["end_to_end"]
                entry["status"] = "pass" if entry["ok"] else "fail"
    except Exception:
        entry.update({"status": "crash", "ok": False, "error": traceback.format_exc(limit=6)})
    finally:
        entry["seconds"] = round(time.time() - t0, 2)
        FreeCAD.closeDocument(doc.Name)
    return entry


result = {"doc": PATH, "ops": {}}
for op in ("control", "relabel", "lift_varset", "lift_sheet", "across_bodies", "merge", "reject", "reject_baseline", "sabotage"):
    result["ops"][op] = run_op(op)
    with open(OUT, "w") as fh:
        json.dump(result, fh, indent=1, sort_keys=True)
print("F6_CORPUS_DOC_DONE", PATH)
