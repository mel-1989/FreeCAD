#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Driver for the F6 corpus run (plain python3, not inside FreeCAD).

    corpus_run.py --worktree <F6 worktree> --out <work dir> [--results results/corpus.json] [--jobs 4]

Builds the scripted documents and variants (corpus_build.py), adds every upstream .FCStd listed in the
control repo's manifest-upstream.txt that exists, runs corpus_worker.py once per document (one FreeCADCmd
process each, QT_QPA_PLATFORM=offscreen, 15 min cap), and writes one JSON with per-document, per-op
results and a summary. Documents whose control run is not clean are excluded and named.
"""
import argparse
import concurrent.futures
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser(os.environ.get("CADFORK_HOME", "~/src/cad-fork"))
CTL = os.path.expanduser(os.environ.get("CADFORK_CTL", "~/_agents/repos/app development/cad-fork"))
CMD = os.path.join(HOME, "freecad/build/release/bin/FreeCADCmd")
OPS = ("relabel", "lift_varset", "lift_sheet", "across_bodies", "merge", "reject")


def fc(script_env, script, timeout):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", **script_env)
    return subprocess.run([CMD, "-c", f"exec(open({script!r}).read())"], env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=timeout)


def run_doc(args):
    path, out = args
    try:
        p = fc({"CADFORK_F6_AGENTCORE": ARGS.agentcore, "CADFORK_F6_DOC": path, "CADFORK_F6_OUT": out},
               os.path.join(HERE, "corpus_worker.py"), 900)
        if os.path.exists(out):
            res = json.load(open(out))
        else:
            res = {"doc": path, "ops": {}}
        res["returncode"] = p.returncode
        if "F6_CORPUS_DOC_DONE" not in p.stdout:
            res["incomplete"] = (p.stdout + p.stderr)[-500:]
        return res
    except subprocess.TimeoutExpired:
        res = json.load(open(out)) if os.path.exists(out) else {"doc": path, "ops": {}}
        res["incomplete"] = "timeout"
        return res


def main():
    global ARGS
    ap = argparse.ArgumentParser()
    ap.add_argument("--worktree", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--results", default=os.path.join(HERE, "results", "corpus.json"))
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--extra-manifest", default=os.path.join(HERE, "results", "freecad_library_manifest.json"),
                    help="JSON list of {id, path, sha256} public FreeCAD-library files downloaded to --extra-dir")
    ap.add_argument("--extra-dir", default="", help="directory holding <id>.FCStd for each manifest entry")
    ap.add_argument("--only", default="", help="substring filter on document name (debugging)")
    ARGS = ap.parse_args()
    ARGS.agentcore = os.path.join(os.path.abspath(ARGS.worktree), "src/Mod/AgentCore")
    out = os.path.abspath(ARGS.out)
    os.makedirs(out, exist_ok=True)
    scripted = os.path.join(out, "scripted")
    if not os.path.exists(os.path.join(scripted, "built.json")):
        p = fc({"CADFORK_F6_MODELS": os.path.join(CTL, "fixtures/golden/models"), "CADFORK_F6_OUT": scripted},
               os.path.join(HERE, "corpus_build.py"), 900)
        if "F6_CORPUS_BUILT" not in p.stdout:
            sys.exit("corpus build failed:\n" + p.stdout[-800:] + p.stderr[-800:])
    built = json.load(open(os.path.join(scripted, "built.json")))
    docs = {n: v["path"] for n, v in built["built"].items()}
    missing = []
    for line in open(os.path.join(CTL, "fixtures/golden/manifest-upstream.txt")):
        rel = line.split("#", 1)[0].strip()
        if rel.lower().endswith(".fcstd"):
            full = os.path.join(HOME, "freecad", rel)
            (docs.__setitem__(rel, full) if os.path.exists(full) else missing.append(rel))
    if ARGS.extra_dir:
        for e in json.load(open(ARGS.extra_manifest)):
            full = os.path.join(ARGS.extra_dir, e["id"] + ".FCStd")
            if os.path.exists(full):
                docs["freecad-library/" + e["path"]] = full
            else:
                missing.append("freecad-library/" + e["path"])
    docs = {k: v for k, v in docs.items() if ARGS.only in k}
    jobs = [(path, os.path.join(out, "res_" + name.replace("/", "__") + ".json")) for name, path in docs.items()]
    for _, o in jobs:
        if os.path.exists(o):
            os.remove(o)
    results = {}
    with concurrent.futures.ThreadPoolExecutor(ARGS.jobs) as ex:
        for name, res in zip(list(docs), ex.map(run_doc, jobs)):
            results[name] = res
            print("done", name, {o: r.get("status") for o, r in res["ops"].items()}, flush=True)
    summary = summarise(results)
    summary["dropped_variants"] = built["dropped"]
    summary["missing_upstream"] = missing
    os.makedirs(os.path.dirname(ARGS.results), exist_ok=True)
    with open(ARGS.results, "w") as fh:
        json.dump({"summary": summary, "documents": results}, fh, indent=1, sort_keys=True)
    print(json.dumps(summary, indent=1, sort_keys=True))


def summarise(results):
    excluded, incomplete = {}, {}
    per_op = {op: {"run": 0, "pass": 0, "effective_docs": 0, "effective_changes": 0, "fail": [], "crash": []} for op in OPS}
    clean_docs = 0
    for name, res in results.items():
        if res.get("incomplete"):
            incomplete[name] = res["incomplete"]
        ctrl = res["ops"].get("control", {})
        if ctrl.get("status") != "pass":
            excluded[name] = ctrl.get("status") or "no result"
            continue
        clean_docs += 1
        for op in OPS:
            r = res["ops"].get(op)
            if r is None:
                incomplete.setdefault(name, f"{op} missing")
                continue
            s = per_op[op]
            if r.get("status") == "skipped":
                s.setdefault("skipped", 0)
                s["skipped"] += 1
                continue
            s["run"] += 1
            if r.get("status") == "pass":
                s["pass"] += 1
            elif r.get("status") == "crash":
                s["crash"].append(name)
            else:
                s["fail"].append({"doc": name, "status": r.get("status"), "violations": r.get("violations")})
            if not r.get("effective"):
                s.setdefault("zero_effect_docs", 0)
                s["zero_effect_docs"] += 1
            if r.get("effective"):
                s["effective_docs"] += 1
                s["effective_changes"] += r["effective"]
            if op == "merge" and r.get("injected"):
                s["effective_docs"] += 0 if r.get("effective") else 0
    sab = {"applicable": 0, "detected": 0, "missed": []}
    for name, res in results.items():
        r = res["ops"].get("sabotage")
        if r and res["ops"].get("control", {}).get("status") == "pass" and r.get("effective"):
            sab["applicable"] += 1
            if r.get("status") == "fail":
                sab["detected"] += 1
            else:
                sab["missed"].append(name)
    rb = {"baseline_fail_too": [], "f6_only_fail": []}
    for name, res in results.items():
        if res["ops"].get("control", {}).get("status") != "pass":
            continue
        rj, bl = res["ops"].get("reject"), res["ops"].get("reject_baseline")
        if rj and rj.get("status") == "fail":
            (rb["baseline_fail_too"] if bl and bl.get("status") == "fail" else rb["f6_only_fail"]).append(name)
    per_op["reject"]["attribution"] = rb
    inv = [s for op, s in per_op.items() if op != "reject"]     # the F3 reject check is not an invariance check
    total_run = sum(s["run"] for s in inv)
    total_pass = sum(s["pass"] for s in inv)
    return {"documents_total": len(results), "documents_clean": clean_docs, "excluded_unstable": excluded,
            "incomplete": incomplete, "per_op": per_op, "sabotage": sab, "invariance_runs": total_run, "invariance_passes": total_pass,
            "pass_rate": (total_pass / total_run) if total_run else None}


if __name__ == "__main__":
    main()
