# SPDX-License-Identifier: LGPL-2.1-or-later
# In-FreeCAD half of agent_eval.py. Run with FreeCADCmd (QT_QPA_PLATFORM=offscreen, stdin </dev/null):
#   CADFORK_F6_AGENTCORE=<src/Mod/AgentCore> CADFORK_F6_MODE=macro|refactor|summaries \
#   CADFORK_F6_IN=<items.json> CADFORK_F6_OUT=<out.json> FreeCADCmd -c "exec(open(THIS).read())" </dev/null
# macro:     each item {"id","macro","check"}: exec the macro as __main__, then eval the check against document "T".
# refactor:  each item {"id","doc","script","check"}: open the document, exec the script (defines edit(doc)),
#            run refactor.invariant(doc, edit), then eval the check. Every item has a 60 s alarm.
# summaries: each item {"id","doc"}: a compact object listing for the prompt.
# reference: each item {"id","doc"}: what the reference refactor API achieves on the document (counts of
#            literals it lifts, expressions it leaves in each body), used to size the task checks.
import json
import os
import signal
import sys
import traceback

sys.path.insert(0, os.environ["CADFORK_F6_AGENTCORE"])
import FreeCAD  # noqa: E402

MODE = os.environ["CADFORK_F6_MODE"]
items = json.load(open(os.environ["CADFORK_F6_IN"]))
out = {}


class Timeout(BaseException):
    pass


def _alarm(signum, frame):
    raise Timeout()


signal.signal(signal.SIGALRM, _alarm)


def close_all():
    for n in list(FreeCAD.listDocuments()):
        try:
            FreeCAD.closeDocument(n)
        except Exception:
            pass


def run_macro(t):
    r = {"ran": False, "check": None, "error": None}
    try:
        signal.alarm(30)
        ns = {"__name__": "__main__"}
        exec(compile(t["macro"], t["id"], "exec"), ns)
        r["ran"] = True
        doc = FreeCAD.getDocument("T")
        r["check"] = bool(eval(t["check"], {"FreeCAD": FreeCAD, "doc": doc, "App": FreeCAD}))
    except Timeout:
        r["error"] = "timeout after 30 s"
    except BaseException:
        r["error"] = traceback.format_exc(limit=3)[-500:]
    finally:
        signal.alarm(0)
        close_all()
    return r


def run_refactor(t):
    from AgentCore import refactor
    r = {"status": None, "ok": None, "check": None, "error": None, "violations": [], "restored_identical": None}
    doc = None
    try:
        signal.alarm(120)
        doc = FreeCAD.openDocument(t["doc"])
        doc.recompute(None, True)
        ns = {"__name__": "f6_agent_edit", "FreeCAD": FreeCAD, "refactor": refactor, "App": FreeCAD}
        exec(compile(t["script"], t["id"], "exec"), ns)
        res = refactor.invariant(doc, ns["edit"])
        r.update({"status": res["status"], "ok": res["ok"], "violations": res["violations"][:3],
                  "error": (res["error"] or "")[-500:] or None, "rolled_back": res["rolled_back"],
                  "restored_identical": res["restored_identical"]})
        rr = res["result"]
        r["effective"] = int(rr.get("count", 0)) if isinstance(rr, dict) and isinstance(rr.get("count", 0), int) else None
        try:
            r["check"] = bool(eval(t["check"], {"FreeCAD": FreeCAD, "doc": doc, "App": FreeCAD, "refactor": refactor, "ref": t.get("ref") or {}}))
        except Exception:
            r["check"] = False
            r["check_error"] = traceback.format_exc(limit=2)[-300:]
    except Timeout:
        r.update({"status": "timeout", "ok": False, "error": "timeout after 120 s"})
    except BaseException:
        r.update({"status": "error", "ok": False, "error": traceback.format_exc(limit=4)[-500:]})
    finally:
        signal.alarm(0)
        close_all()
    return r


def summary(t):
    doc = FreeCAD.openDocument(t["doc"])
    doc.recompute(None, True)
    lines = []
    for o in doc.Objects:
        extra = []
        for p in ("Length", "Width", "Height", "Radius", "Occurrences", "Angle", "Type", "Profile", "BaseFeature", "Support", "Shapes"):
            if p in o.PropertiesList:
                v = getattr(o, p)
                if isinstance(v, (list, tuple)):
                    v = [getattr(x, "Name", x) for x in v]
                v = getattr(v, "Name", v)
                extra.append(f"{p}={v}")
        if o.TypeId == "Sketcher::SketchObject":
            extra.append("constraints=" + ",".join(f"{c.Name}:{c.Type}" for c in o.Constraints if c.Name))
        lines.append(f"{o.Name} [{o.TypeId}] label={o.Label!r} " + " ".join(extra))
    FreeCAD.closeDocument(doc.Name)
    return "\n".join(lines)


def reference(t):
    """Run the reference operations on fresh copies of the document (never saved)."""
    from AgentCore import refactor
    out = {}
    for key, target in (("lift_varset", "VarSet"), ("lift_sheet", "Spreadsheet")):
        doc = FreeCAD.openDocument(t["doc"])
        doc.recompute(None, True)
        out[key] = refactor.lift_all(doc, target=target)["count"]
        FreeCAD.closeDocument(doc.Name)
    doc = FreeCAD.openDocument(t["doc"])
    doc.recompute(None, True)
    refactor.across_all_bodies(doc)
    containers = [c.Name for c in doc.Objects if c.TypeId in ("Spreadsheet::Sheet", "App::VarSet")]
    out["bodies"] = {b.Name: sum(1 for f in b.Group for _p, e in f.ExpressionEngine if any(c in e for c in containers))
                     for b in doc.Objects if b.TypeId == "PartDesign::Body"}
    FreeCAD.closeDocument(doc.Name)
    return out


fn = {"macro": run_macro, "refactor": run_refactor, "summaries": summary, "reference": reference}[MODE]
for t in items:
    try:
        out[t["id"]] = fn(t)
    except BaseException:
        out[t["id"]] = {"error": traceback.format_exc(limit=3)[-400:]}
    with open(os.environ["CADFORK_F6_OUT"], "w") as fh:
        json.dump(out, fh)
print("F6_AGENT_WORKER_DONE")
