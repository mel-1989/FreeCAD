#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""F6 macro eval: model-written macros through three stages, reported separately.

    macro_eval.py --worktree <F6 worktree> [--tasks macro_tasks/*.raw.json] [--out results/macro_eval.json]

Stage 1 parse: the raw model output must be JSON (a broken array is split per object; objects that still
do not parse are counted as parse failures). Stage 2 pyright: AgentCore.refactor.grounding, zero errors.
Stage 3 headless: the macro is exec'd under FreeCADCmd (QT_QPA_PLATFORM=offscreen) and its `check`
expression must be true. Stage 3 runs for every parsed macro, not only the pyright-clean ones, so the
table shows what pyright caught and what it let through. The in-FreeCAD half is macro_eval_worker.py.
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CMD = os.path.expanduser("~/src/cad-fork/freecad/build/release/bin/FreeCADCmd")


def parse_raw(text):
    try:
        return json.loads(text), []
    except ValueError:
        pass
    tasks, bad = [], []
    for chunk in re.split(r"\n\},?\n(?=\{)", text.strip().lstrip("[").rstrip("]").strip()):
        chunk = chunk.strip().rstrip(",")
        if not chunk.endswith("}"):
            chunk += "\n}"
        try:
            tasks.append(json.loads(chunk))
        except ValueError as exc:
            m = re.search(r'"id":\s*"([^"]+)"', chunk)
            bad.append({"id": m.group(1) if m else "?", "error": str(exc)})
    return tasks, bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--worktree")
    ap.add_argument("--tasks", nargs="*", default=sorted(glob.glob(os.path.join(HERE, "macro_tasks", "*.raw.json"))))
    ap.add_argument("--out", default=os.path.join(HERE, "results", "macro_eval.json"))
    a = ap.parse_args()
    ac = os.path.join(os.path.abspath(a.worktree), "src/Mod/AgentCore")
    import importlib.util
    spec = importlib.util.spec_from_file_location("f6_grounding", os.path.join(ac, "AgentCore/refactor/grounding.py"))
    grounding = importlib.util.module_from_spec(spec)   # pure Python, no FreeCAD import needed
    spec.loader.exec_module(grounding)

    tasks, parse_fail = [], []
    for f in a.tasks:
        t, b = parse_raw(open(f, encoding="utf-8").read())
        tasks += t
        parse_fail += b
    for t in tasks:
        t["pyright"] = grounding.check_source(t["macro"])
    tmp_in, tmp_out = a.out + ".in.json", a.out + ".run.json"
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(tasks, open(tmp_in, "w"))
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", CADFORK_F6_AGENTCORE=ac, CADFORK_F6_IN=tmp_in, CADFORK_F6_OUT=tmp_out)
    run = subprocess.run([CMD, "-c", "exec(open(%r).read())" % os.path.join(HERE, "macro_eval_worker.py")], env=env, stdin=subprocess.DEVNULL,
                         capture_output=True, text=True, timeout=600)
    ran = json.load(open(tmp_out))
    os.remove(tmp_in)
    os.remove(tmp_out)
    rows = []
    for t in tasks:
        r = ran.get(t["id"], {})
        rows.append({"id": t["id"], "task": t["task"], "pyright_ok": t["pyright"]["ok"],
                     "pyright_errors": t["pyright"]["errors"], "ran": r.get("ran"), "check": r.get("check"),
                     "run_error": r.get("error")})
    n = len(tasks) + len(parse_fail)
    pyr = [r for r in rows if r["pyright_ok"]]
    summary = {
        "generated": n, "parsed": len(tasks), "parse_failures": parse_fail,
        "pyright_clean": len(pyr), "pyright_clean_and_test_pass": sum(1 for r in pyr if r["check"]),
        "headless_test_pass_all_parsed": sum(1 for r in rows if r["check"]),
        "pyright_rejected": len(rows) - len(pyr),
        "pyright_rejected_but_test_would_pass": sum(1 for r in rows if not r["pyright_ok"] and r["check"]),
        "pyright_clean_but_test_fail": [r["id"] for r in pyr if not r["check"]],
    }
    json.dump({"summary": summary, "rows": rows}, open(a.out, "w"), indent=1)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
