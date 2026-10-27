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
from AgentCore.refactor import geomhash  # noqa: E402

PATH = os.environ["CADFORK_F6_DOC"]
OUT = os.environ["CADFORK_F6_OUT"]
MAX_ATTEMPTS = 6
LIFT_LIMIT = 40
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


def run_op(op):
    doc, attempts, errors = open_clean()
    t0 = time.time()
    entry = {"attempts": attempts}
    try:
        if errors:
            entry.update({"status": "unclean", "errors": errors})
            return entry
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
for op in ("control", "relabel", "lift_varset", "lift_sheet", "across_bodies", "merge", "sabotage"):
    result["ops"][op] = run_op(op)
    with open(OUT, "w") as fh:
        json.dump(result, fh, indent=1, sort_keys=True)
print("F6_CORPUS_DOC_DONE", PATH)
