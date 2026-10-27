#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""F6 agent evals: 100 macro tasks and 100 refactor tasks, 5 passes each, with a cheap model as the agent.

    agent_eval.py macro    --worktree <F6 or overlay worktree> --work <scratch dir> [--tasks 100] [--passes 5] [--rounds 3]
    agent_eval.py refactor --worktree <...> --work <scratch dir> --corpus <dir with built scripted .FCStd> [...]

Macro pipeline (per task and pass): the model writes a macro; pyright (AgentCore.refactor.grounding, zero
errors) runs BEFORE execution and a rejected macro is never executed; a clean macro runs headless and its
check expression must hold. A failure (pyright diagnostics, run error or false check) is fed back for up to
``--rounds`` rounds. Reported per round: first-attempt pass and the cumulative pass after each repair.

Refactor pipeline: the model sees a compact listing of one corpus document, the refactor API and a task, and
writes ``edit(doc)``; it runs under refactor.invariant (geometry hash before/after, rollback on violation) and
then the task's check expression runs. Single shot, 5 passes.

Tasks and check expressions come from agent_tasks.py (deterministic, expected values computed from closed-form
geometry, no model involved). A task that no pass ever solves is reported as ``unsolved`` and the rates are
given over all tasks and over tasks solved at least once. Every model call's tokens and cost are summed into the footer.
"""
import argparse
import concurrent.futures
import importlib.util
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import agent_tasks  # noqa: E402
import llm  # noqa: E402

CMD = os.path.expanduser("~/src/cad-fork/freecad/build/release/bin/FreeCADCmd")
METER = llm.Meter()

MACRO_CATEGORIES = [
    "Part primitives (Box, Cylinder, Sphere, Cone, Torus), their properties and Placement",
    "Part booleans (Fuse, Cut, Common, MultiFuse), Part.makeX shape functions, Part::Feature with a Shape",
    "PartDesign: Body, sketch on an origin plane, Pad, Pocket, Revolution, Fillet, Chamfer, LinearPattern",
    "Sketcher geometry and constraints (lines, circles, arcs, polygons; Coincident, Horizontal, Distance, Radius)",
    "Spreadsheet aliases, VarSet properties, setExpression, document properties, labels, groups",
]
MACRO_RULES = (
    "FreeCAD 1.x Python macro, run HEADLESS: never import FreeCADGui, never use ViewObject. Start with "
    "`import FreeCAD` (and `import Part` / `import Sketcher` as needed). Create the document with "
    "`doc = FreeCAD.newDocument(\"T\")` (exactly that name), build the model, end with `doc.recompute()`. "
    "Use only real FreeCAD APIs. Output ONLY one ```python code block.\n"
    "FreeCAD 1.x idioms (pitfalls seen in this environment):\n"
    "- A sketch's attachment property is `AttachmentSupport` (there is no `Support`): `body = doc.addObject('PartDesign::Body','Body'); "
    "sk = body.newObject('Sketcher::SketchObject','Sketch'); sk.AttachmentSupport = [(body.Origin.OriginFeatures[3], '')]; sk.MapMode = 'FlatFace'` "
    "(OriginFeatures: 0 X_Axis, 1 Y_Axis, 2 Z_Axis, 3 XY_Plane, 4 XZ_Plane, 5 YZ_Plane). Create PartDesign features with `body.newObject('PartDesign::Pad','Pad')`, then set `.Profile = sk`, `.Length`.\n"
    "- Add geometry with `sk.addGeometry(Part.LineSegment(App.Vector(x1,y1,0), App.Vector(x2,y2,0)), False)`; "
    "constraints with `sk.addConstraint(Sketcher.Constraint('Coincident', geo1, pos1, geo2, pos2))`; origin point is geometry -1, pos 1; "
    "`Sketcher.Constraint('DistanceX', geo, 1, geo, 2, value)`.\n"
    "- `Part::Fillet` / `Part::Chamfer`: set `.Base = obj` and `.Edges = [(edge_index_1_based, radius, radius), ...]`.\n"
    "- Expressions: `obj.setExpression('Length', 'Sheet.alias')`; spreadsheet cells: `sh.set('A1', '10 mm'); sh.setAlias('A1', 'len'); doc.recompute()`. "
    "VarSet: `vs.addProperty('App::PropertyLength', 'Name', 'Group')`.\n"
    "- Unit symbols (H, m, s, A, V, W, N, K, ...) cannot be alias or property names inside expressions.\n"
    "- Part::Cut/Fuse/Common take `.Base`/`.Tool` (Cut) or `.Shapes = [a, b]` (Fuse/Common)."
)
REFACTOR_API = """\
refactor.relabel(doc, {"Name": "New label", ...})              # set Labels (Name is immutable); returns a dict with "count"
refactor.relabel_all(doc)                                        # apply the house label scheme to every feature
refactor.lift_candidates(doc)                                    # [{"object","property","kind"}] literals that can be lifted
refactor.lift_literal(doc, obj_or_name, "Length", target="VarSet"|"Spreadsheet", container="Parameters", alias=None)
refactor.lift_all(doc, target="VarSet"|"Spreadsheet", container=None, limit=None)   # lift every candidate
refactor.apply_across_bodies(doc, fn)                            # fn(body) on every PartDesign Body; returns dict
refactor.across_all_bodies(doc, target="Spreadsheet")            # parameterise every body into its own container
refactor.find_redundant(doc) / refactor.merge_redundant(doc, dry_run=False)   # conservative removal of proven no-op features
FreeCAD is imported; doc is the open FreeCAD document. Changes are made with plain FreeCAD Python as well
(obj.Label = ..., obj.setExpression(...), doc.addObject(...)). The edit must NOT change the geometry.
Return a dict (e.g. the result of the refactor call) or None; if you delete objects on purpose, return {"removed": [names]}.
"""


PARTDESIGN_PATTERNS = """\
Verified FreeCAD 1.x PartDesign patterns (V = FreeCAD.Vector; adapt the numbers):
  body = doc.addObject('PartDesign::Body', 'Body'); xy = body.Origin.OriginFeatures[3]; xz = body.Origin.OriginFeatures[4]
  def rect(sk, x0, y0, x1, y1):   # closed rectangle, four lines, kept axis-aligned by constraints
      sk.addGeometry([Part.LineSegment(V(x0,y0,0), V(x1,y0,0)), Part.LineSegment(V(x1,y0,0), V(x1,y1,0)),
                      Part.LineSegment(V(x1,y1,0), V(x0,y1,0)), Part.LineSegment(V(x0,y1,0), V(x0,y0,0))], False)
      for i in range(4): sk.addConstraint(Sketcher.Constraint('Coincident', i, 2, (i+1) % 4, 1))
      sk.addConstraint(Sketcher.Constraint('Horizontal', 0)); sk.addConstraint(Sketcher.Constraint('Horizontal', 2))
      sk.addConstraint(Sketcher.Constraint('Vertical', 1)); sk.addConstraint(Sketcher.Constraint('Vertical', 3))
  sk = body.newObject('Sketcher::SketchObject', 'Sketch'); sk.AttachmentSupport = [(xy, '')]; sk.MapMode = 'FlatFace'
  rect(sk, 0, 0, 30, 20)
  sk.addConstraint(Sketcher.Constraint('Coincident', 0, 1, -1, 1))                     # ONLY when that corner really is at (0,0); otherwise
  #   position it with Sketcher.Constraint('DistanceX', -1, 1, 0, 1, x0) and ('DistanceY', -1, 1, 0, 1, y0) (origin point is geometry -1, pos 1)
  sk.addConstraint(Sketcher.Constraint('DistanceX', 0, 1, 0, 2, 30.0)); sk.addConstraint(Sketcher.Constraint('DistanceY', 1, 1, 1, 2, 20.0))
  pad = body.newObject('PartDesign::Pad', 'Pad'); pad.Profile = sk; pad.Length = 10; doc.recompute()
  # A standalone sketch (no Body) needs no attachment: sk = doc.addObject('Sketcher::SketchObject', 'S'); it lies in the XY plane.
  # Pocket: a second sketch attached to the same plane, raised with sk2.AttachmentOffset = App.Placement(V(0,0,10), App.Rotation());
  # sk2.addGeometry(Part.Circle(V(15,10,0), V(0,0,1), 4), False); pk = body.newObject('PartDesign::Pocket','Pocket'); pk.Profile = sk2; pk.Length = 10
  # (a pocket cuts opposite to the sketch normal, i.e. downward from a sketch lifted to the top face).
  # Revolution: sketch on xz = body.Origin.OriginFeatures[4]; rev = body.newObject('PartDesign::Revolution','Rev'); rev.Profile = sk
  #   rev.ReferenceAxis = (sk, ['V_Axis']); rev.Angle = 360   (V_Axis of an XZ sketch is the global Z axis; there is no `Symmetric` property).
  # LinearPattern: lp = body.newObject('PartDesign::LinearPattern','Pattern'); lp.Originals = [pad]; lp.Direction = (sk, ['H_Axis'])
  #   lp.Length = 40 (total length from first to last copy); lp.Occurrences = 3; body.Tip = lp; doc.recompute()
"""


def load_grounding(worktree):
    path = os.path.join(os.path.abspath(worktree), "src/Mod/AgentCore/AgentCore/refactor/grounding.py")
    spec = importlib.util.spec_from_file_location("f6_grounding", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ask(system, user, **kw):
    r = llm.chat([{"role": "system", "content": system}, {"role": "user", "content": user}], **kw)
    METER.add(r)
    return r["text"]


def code_of(text):
    m = re.search(r"```(?:python|py)?\s*\n(.*?)```", text, re.S)
    return (m.group(1) if m else text).strip() + "\n"


def parse_tasks(text, prefix):
    out = []
    for blk in re.split(r"^###\s*", text, flags=re.M)[1:]:
        m = re.match(r"([A-Za-z0-9_]+)\s*\n(.*)", blk, re.S)
        if not m:
            continue
        body = m.group(2)
        t = re.search(r"^TASK:\s*(.+?)\s*^CHECK:\s*(.+?)\s*(?=^###|\Z)", body, re.S | re.M)
        if t:
            check = t.group(2).strip().strip("`").strip()
            out.append({"id": f"{prefix}_{m.group(1)}", "task": " ".join(t.group(1).split()), "check": check})
    return out


def run_worker(mode, items, ac, work, tag, timeout=1800):
    inp, outp = os.path.join(work, f"{tag}.in.json"), os.path.join(work, f"{tag}.out.json")
    json.dump(items, open(inp, "w"))
    if os.path.exists(outp):
        os.remove(outp)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", CADFORK_F6_AGENTCORE=ac, CADFORK_F6_MODE=mode,
               CADFORK_F6_IN=inp, CADFORK_F6_OUT=outp)
    p = subprocess.run([CMD, "-c", "exec(open(%r).read())" % os.path.join(HERE, "agent_eval_worker.py")], env=env,
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)
    res = json.load(open(outp)) if os.path.exists(outp) else {}
    if "F6_AGENT_WORKER_DONE" not in p.stdout:
        res["_incomplete"] = (p.stdout + p.stderr)[-300:]
    return res


def run_chunks(mode, items, ac, work, tag, chunk=25, jobs=3):
    chunks = [items[i:i + chunk] for i in range(0, len(items), chunk)]
    out = {}
    with concurrent.futures.ThreadPoolExecutor(jobs) as ex:
        for i, res in enumerate(ex.map(lambda a: run_worker(mode, a[1], ac, work, f"{tag}{a[0]}"), enumerate(chunks))):
            inc = res.pop("_incomplete", None)
            out.update(res)
            if inc:
                print(f"chunk {i} incomplete: {inc!r}", flush=True)
    return out


def pmap(fn, seq, workers=8):
    with concurrent.futures.ThreadPoolExecutor(workers) as ex:
        return list(ex.map(fn, seq))


# ----------------------------------------------------------------------------------------------- macro

def gen_macro_tasks(n, work):
    tasks = agent_tasks.macro_tasks(n)
    json.dump(tasks, open(os.path.join(work, "macro_tasks.json"), "w"), indent=1)
    return tasks


def macro_prompt(t, prev=None, feedback=None):
    base = (f"Task: {t['task']}\nA test will evaluate this expression after your macro runs (it must be true):\n"
            f"    {t['check']}\n")
    if prev is None:
        return base
    return (base + f"\nYour previous macro:\n```python\n{prev}```\nIt failed before or during its test with:\n{feedback}\n"
            f"Fix it. Output the complete corrected macro in one ```python block.")


def macro_eval(a):
    work = os.path.abspath(a.work)
    os.makedirs(work, exist_ok=True)
    ac = os.path.join(os.path.abspath(a.worktree), "src/Mod/AgentCore")
    grounding = load_grounding(a.worktree)
    tasks = gen_macro_tasks(a.tasks, work)
    print("macro tasks:", len(tasks), flush=True)
    state = {(t["id"], p): {"id": t["id"], "pass": p, "code": None, "history": [], "done": False}
             for t in tasks for p in range(a.passes)}
    by_id = {t["id"]: t for t in tasks}
    for rnd in range(1, a.rounds + 1):
        todo = [s for s in state.values() if not s["done"]]
        if not todo:
            break

        def gen(s):
            t = by_id[s["id"]]
            if rnd == 1:
                text = ask(MACRO_RULES + PARTDESIGN_PATTERNS, macro_prompt(t), max_tokens=2000, temperature=0.7)
            else:
                text = ask(MACRO_RULES + PARTDESIGN_PATTERNS, macro_prompt(t, s["code"], s["feedback"]), max_tokens=2000, temperature=0.4)
            s["code"] = code_of(text)
            s["pyright"] = grounding.check_source(s["code"])
            return s
        pmap(gen, todo, 8)
        runnable = [s for s in todo if s["pyright"]["ok"]]
        items = [{"id": f"{s['id']}#{s['pass']}", "macro": s["code"], "check": by_id[s["id"]]["check"]} for s in runnable]
        res = run_chunks("macro", items, ac, work, f"macro_r{rnd}_") if items else {}
        for s in todo:
            key = f"{s['id']}#{s['pass']}"
            if not s["pyright"]["ok"]:
                status = "pyright_rejected"
                s["feedback"] = "pyright (static check, the macro was not run) reported:\n" + "\n".join(
                    f"line {e['line']}: {e['message']}" for e in s["pyright"]["errors"][:6])
            else:
                r = res.get(key, {})
                if r.get("check"):
                    status = "pass"
                elif r.get("error"):
                    status = "run_error"
                    s["feedback"] = "runtime error:\n" + r["error"]
                else:
                    status = "check_false"
                    s["feedback"] = "the macro ran but the test expression evaluated false"
            s["history"].append(status)
            if status == "pass":
                s["done"] = True
        print(f"round {rnd}: {sum(1 for s in state.values() if s['done'])}/{len(state)} passing", flush=True)
    return summarise_macro(tasks, state, a)


def summarise_macro(tasks, state, a):
    n = len(state)
    by_check = {t['id']: t['check'] for t in tasks}
    cum = []
    for r in range(1, a.rounds + 1):
        cum.append(sum(1 for s in state.values() if "pass" in s["history"][:r]))
    first = Counter_of([s["history"][0] for s in state.values()])
    solved_tasks = {t["id"] for t in tasks if any("pass" in state[(t["id"], p)]["history"] for p in range(a.passes))}
    solved_runs = [s for s in state.values() if s["id"] in solved_tasks]
    rounds_to_pass = Counter_of([s["history"].index("pass") + 1 for s in state.values() if "pass" in s["history"]])
    pyright_ok_first = sum(1 for s in state.values() if s["history"][0] != "pyright_rejected")
    pyright_clean_then_pass_first = first.get("pass", 0)
    # the brief's condition: every macro that RUNS has passed pyright (true by construction, the gate precedes
    # execution); and its headless test: report as pass rates, not as a vacuous claim
    summary = {
        "model": METER.model, "tasks": len(tasks), "passes": a.passes, "runs": n, "max_repair_rounds": a.rounds,
        "first_attempt_status": first, "first_attempt_pyright_clean": pyright_ok_first,
        "first_attempt_pass": pyright_clean_then_pass_first, "first_attempt_pass_rate": round(pyright_clean_then_pass_first / n, 4),
        "cumulative_pass_after_round": cum, "cumulative_pass_rate_after_round": [round(c / n, 4) for c in cum],
        "final_pass": cum[-1], "final_pass_rate": round(cum[-1] / n, 4),
        "tasks_solved_at_least_once": len(solved_tasks), "tasks_unsolved": sorted(set(t["id"] for t in tasks) - solved_tasks),
        "final_pass_rate_over_solvable_tasks": round(sum(1 for s in solved_runs if "pass" in s["history"]) / max(1, len(solved_runs)), 4),
        "rounds_needed_when_passing": rounds_to_pass,
        "executed_without_pyright_clean": 0,
        "final_status_counts": Counter_of([("pass" if "pass" in s["history"] else s["history"][-1]) for s in state.values()]),
        "footer": METER.footer(),
    }
    return summary, [{"id": s["id"], "pass": s["pass"], "history": s["history"], "last_feedback": s.get("feedback"), "check": by_check[s["id"]], "final_code": s["code"]} for s in state.values()]


def Counter_of(seq):
    d = {}
    for x in seq:
        d[x] = d.get(x, 0) + 1
    return d


# ------------------------------------------------------------------------------------------- refactor

def refactor_eval(a):
    work = os.path.abspath(a.work)
    os.makedirs(work, exist_ok=True)
    ac = os.path.join(os.path.abspath(a.worktree), "src/Mod/AgentCore")
    docs = sorted(f for f in os.listdir(a.corpus) if re.fullmatch(r"m\d\d_[a-z_0-9]+\.FCStd", f) and "_x" not in f)
    docs = [os.path.join(os.path.abspath(a.corpus), f) for f in docs]
    summ_res = run_worker("summaries", [{"id": os.path.basename(d), "doc": d} for d in docs], ac, work, "summ")
    summaries = {k: v for k, v in summ_res.items() if isinstance(v, str)}
    tasks = agent_tasks.refactor_tasks({os.path.basename(d): summaries[os.path.basename(d)] for d in docs}, a.tasks)
    for t in tasks:
        t["doc"] = os.path.join(os.path.abspath(a.corpus), t["doc_name"])
    json.dump(tasks, open(os.path.join(work, "refactor_tasks.json"), "w"), indent=1)
    print("refactor tasks:", len(tasks), flush=True)
    system = ("You write FreeCAD Python for a geometry-preserving refactor. Output ONLY one ```python block that defines "
              "`def edit(doc):` and nothing else executed at import. API available as `refactor` and `FreeCAD`:\n" + REFACTOR_API)
    runs = [(t, p) for t in tasks for p in range(a.passes)]

    def gen_script(tp):
        t, p = tp
        user = f"Document objects:\n{summaries[t['doc_name']]}\n\nTask: {t['task']}\nA test will check (must be true afterwards): {t['check']}\n"
        return code_of(ask(system, user, max_tokens=2000, temperature=0.7))
    scripts = pmap(gen_script, runs, 8)
    items = [{"id": f"{t['id']}#{p}", "doc": t["doc"], "script": sc, "check": t["check"]} for (t, p), sc in zip(runs, scripts)]
    res = run_chunks("refactor", items, ac, work, "refactor_", chunk=25, jobs=3)
    noop_items = [{"id": t["id"], "doc": t["doc"], "script": "def edit(doc):\n    return None\n", "check": t["check"]} for t in tasks]
    noop = run_chunks("refactor", noop_items, ac, work, "refactor_noop_", chunk=25, jobs=3)
    vacuous = sorted(k for k, v in noop.items() if v.get("check"))
    rows = []
    for (t, p), it in zip(runs, items):
        r = res.get(it["id"], {"status": "no_result"})
        outcome = ("pass" if r.get("ok") and r.get("check") else
                   "invariant_ok_check_false" if r.get("ok") else
                   "geometry_violation_rolled_back" if r.get("status") == "fail" else
                   "script_error" if r.get("status") in ("error", "timeout") else r.get("status", "no_result"))
        rows.append({"id": it["id"], "task": t["task"], "outcome": outcome, "invariant_status": r.get("status"),
                     "restored_identical": r.get("restored_identical"), "check": r.get("check"),
                     "error": (r.get("error") or "")[-200:], "violations": r.get("violations"), "script": it["script"]})
    n = len(rows)
    counts = Counter_of([r["outcome"] for r in rows])
    solved = {t["id"] for t in tasks if any(r["outcome"] == "pass" for r in rows if r["id"].rsplit("#", 1)[0] == t["id"])}
    rb = [r for r in rows if r["outcome"] == "geometry_violation_rolled_back"]
    summary = {
        "model": METER.model, "tasks": len(tasks), "passes": a.passes, "runs": n, "outcomes": counts,
        "pass": counts.get("pass", 0), "pass_rate": round(counts.get("pass", 0) / n, 4),
        "tasks_solved_at_least_once": len(solved), "tasks_unsolved": sorted(set(t["id"] for t in tasks) - solved),
        "geometry_changing_edits_caught": len(rb),
        "geometry_changing_edits_restored_identical": sum(1 for r in rb if r["restored_identical"]),
        "invariance_held_on_every_edit_that_ran": sum(1 for r in rows if r["invariant_status"] == "pass"),
        "edits_that_ran_to_the_oracle": sum(1 for r in rows if r["invariant_status"] in ("pass", "fail")),
        "checks_already_true_with_a_no_op_edit": vacuous,
        "footer": METER.footer(),
    }
    return summary, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["macro", "refactor"])
    ap.add_argument("--worktree", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--corpus", default="")
    ap.add_argument("--tasks", type=int, default=100)
    ap.add_argument("--passes", type=int, default=5)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    summary, rows = macro_eval(a) if a.kind == "macro" else refactor_eval(a)
    out = a.out or os.path.join(HERE, "results", f"{a.kind}_agent_eval.json")
    json.dump({"summary": summary, "rows": rows}, open(out, "w"), indent=1)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
