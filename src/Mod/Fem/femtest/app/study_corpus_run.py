# SPDX-License-Identifier: LGPL-2.1-or-later
"""Driver for the G11 corpus (run 067). One FreeCADCmd process per case, one JSON line per case, resumable, per-case timeout.

  python3 -I study_corpus_run.py --mode baseline --cases A,B --out results.jsonl
  python3 -I study_corpus_run.py --mode study --cases all --out results.jsonl
  python3 -I study_oracle.py results.jsonl          # judge a study run

Modes: `baseline` runs the stock pipeline (study_baseline.py) at the corpus's finest h (three levels for group H) and records raw numbers;
`study` calls femstudy.define_static / run_static (SPEC.md). Scratch, mesh, .frd and .dat files live under SCRATCH (default
~/src/cad-fork/scratch/r067/), never /tmp, and are deleted after each case. Environment: FC_SRC (worktree, default the G11 one),
FC_BUILD_DIR (default $FC_SRC/build/release). The pixi bin of the worktree is put first on PATH here, because harness/env.sh does not.
The same file is the worker: with CADFORK_STUDY_WORKER=1 it runs one case inside FreeCADCmd and writes the record to CADFORK_STUDY_OUT.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
FC_SRC = os.environ.get("FC_SRC", os.path.join(HOME, "src/cad-fork/worktrees/G11"))
FC_BUILD = os.environ.get("FC_BUILD_DIR", os.path.join(FC_SRC, "build/release"))
SCRATCH = os.environ.get("SCRATCH", os.path.join(HOME, "src/cad-fork/scratch/r067"))


# ------------------------------------------------------------------------------------------------ worker (inside FreeCADCmd)
def worker():
    import traceback

    sys.path.insert(0, HERE)
    import FreeCAD
    import study_corpus
    import study_oracle

    case_id, mode, wd, out_path = os.environ["CADFORK_CASE"], os.environ["CADFORK_MODE"], os.environ["CADFORK_WD"], os.environ["CADFORK_STUDY_OUT"]
    case = next(c for c in study_corpus.all_cases() if c.id == case_id)
    rec = {"case": case_id, "mode": mode, "h0": case.h0, "results": [], "probes": [], "error": None}
    t0 = time.time()
    try:
        doc = FreeCAD.newDocument("corpus")
        ctx = case.build(doc) if mode == "study" else {}
        edit = ctx.get("edit")
        if mode == "baseline":
            import study_baseline

            sc = float(os.environ.get("CADFORK_HSCALE", "1"))
            levels = [case.h0 * sc, case.h0 * sc / 2, case.h0 * sc / 4] if (case.group == "H" or os.environ.get("CADFORK_LEVELS3") == "1") else [case.h0 * sc / 4]
            lv = []
            if case.group == "G":  # four spellings of one problem, finest level each
                for k in range(4):
                    d = FreeCAD.newDocument(f"g{k}")
                    c = case.build(d)
                    one = study_baseline.run_stock(d, c["variants"][k], levels[0], os.path.join(wd, f"G{k}"))
                    rec["results"].append({"verdict": None, "units": study_oracle.UNITS, "levels": [one], "stock": True})
                rec["error"] = next((r["levels"][0]["error"] for r in rec["results"] if r["levels"][0].get("error")), None)
                levels = []
            for k, h in enumerate(levels):
                d = FreeCAD.newDocument(f"lv{k}")  # a fresh geometry per level keeps the stock objects of the previous level out of the way
                c = case.build(d)
                one = study_baseline.run_stock(d, c["spec"], h, os.path.join(wd, f"L{k}"), edit=c.get("edit"))
                lv.append(one)
                rec["probes"].append(study_oracle.probe_result(case, d, one["result"], h) if one.get("result") else {})
            if lv:
                rec["results"] = [{"verdict": None, "units": study_oracle.UNITS, "levels": lv, "stock": True}]
                rec["error"] = next((l["error"] for l in lv if l.get("error")), None)
        else:
            import femstudy

            runs = ctx.get("variants") or [ctx["spec"]]
            for spec in runs:
                if edit:
                    name = femstudy.define_static(doc, spec)
                    edit(doc)
                    doc.recompute()
                    res = femstudy.run_static(doc, name)
                else:
                    res = femstudy.run_static(doc, spec)
                rec["results"].append(res)
                if case.truth.get("sigma"):
                    rec["probes"] = [study_oracle.probe_result(case, doc, lv["result"], lv["h"]) for lv in res.get("levels", []) if lv.get("result")]
    except Exception as ex:  # noqa: BLE001
        rec["error"] = f"{type(ex).__name__}: {str(ex)[:300]}"
        rec["trace"] = traceback.format_exc()[-800:]
    rec["seconds"] = round(time.time() - t0, 2)
    with open(out_path, "w") as f:
        json.dump(rec, f, default=str)
    os._exit(0)  # skip FreeCAD's slow interpreter teardown


# ------------------------------------------------------------------------------------------------ orchestrator
def run_case(case, mode, timeout):
    wd = os.path.join(SCRATCH, "work", case.id)
    shutil.rmtree(wd, ignore_errors=True)
    os.makedirs(wd, exist_ok=True)
    tmp = os.path.join(SCRATCH, "tmp")
    os.makedirs(tmp, exist_ok=True)
    out_path = os.path.join(wd, "record.json")
    env = dict(os.environ)
    pixi = os.path.join(FC_SRC, ".pixi/envs/default/bin")
    env["PATH"] = pixi + ":" + env["PATH"]
    env.update({"CADFORK_STUDY_WORKER": "1", "CADFORK_CASE": case.id, "CADFORK_MODE": mode, "CADFORK_WD": wd, "CADFORK_STUDY_OUT": out_path,
                "TMPDIR": tmp, "QT_QPA_PLATFORM": "offscreen", "PYTHONPATH": ""})
    # group I9 runs with the CalculiX binary missing: a PATH holding gmsh only
    if case.id == "I09":
        only = os.path.join(wd, "pathbin")
        os.makedirs(only)
        os.symlink(os.path.join(pixi, "gmsh"), os.path.join(only, "gmsh"))
        env["PATH"] = only + ":/usr/bin:/bin"
    t0 = time.time()
    p = subprocess.Popen([os.path.join(FC_BUILD, "bin/FreeCADCmd"), os.path.abspath(__file__)], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         start_new_session=True, text=True)
    try:
        so, _ = p.communicate(timeout=timeout)
        rc = p.returncode
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        so, _ = p.communicate()
        rc = "timeout"
    rec = None
    if os.path.exists(out_path):
        try:
            rec = json.load(open(out_path))
        except Exception:  # noqa: BLE001
            rec = None
    if rec is None:
        rec = {"case": case.id, "mode": mode, "results": [], "probes": [], "crash": f"rc={rc}", "tail": (so or "")[-400:]}
    rec["wall_seconds"] = round(time.time() - t0, 2)
    shutil.rmtree(wd, ignore_errors=True)  # .frd, .dat, .inp, mesh files
    return rec


def main(argv):
    import argparse

    sys.path.insert(0, HERE)
    import study_corpus

    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["baseline", "study"], required=True)
    ap.add_argument("--cases", default="all", help="comma list of groups and case ids, or all")
    ap.add_argument("--out", required=True)
    ap.add_argument("--timeout", type=int, default=0, help="seconds per case; 0 means the case's own")
    ap.add_argument("--hscale", type=float, default=1.0, help="baseline only: multiply every h by this (mesh studies)")
    ap.add_argument("--redo", action="store_true", help="ignore existing lines")
    a = ap.parse_args(argv)
    cases = study_corpus.select(None if a.cases == "all" else a.cases)
    done = set()
    if os.path.exists(a.out) and not a.redo:
        for ln in open(a.out):
            if ln.strip():
                r = json.loads(ln)
                if r.get("mode") == a.mode:
                    done.add(r["case"])
    os.environ["CADFORK_HSCALE"] = str(a.hscale)
    for c in cases:
        if c.id in done:
            continue
        rec = run_case(c, a.mode, a.timeout or c.timeout_s * (4 if c.variants_n > 1 else 1))
        with open(a.out, "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        print(f"{c.id} {rec.get('wall_seconds')}s {rec.get('error') or rec.get('crash') or 'ok'}", flush=True)


if os.environ.get("CADFORK_STUDY_WORKER") == "1":
    worker()
elif __name__ == "__main__":
    main(sys.argv[1:])
