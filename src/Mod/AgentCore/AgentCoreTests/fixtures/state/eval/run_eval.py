#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run the AgentCore.state agent-usability eval against OpenRouter.

Stdlib only; runs outside FreeCAD. For each of the 40 tasks in truth.json it
gives a model the fixture's state dump (the stored golden, which
test_state_golden proves equal to a fresh ``state.dump``) and the question,
asks for ``{"answer": ...}``, grades it with grade.py, and repeats for
``--passes`` passes. Writes results.json with every call's model, tokens and
cost, and prints the summary.

    python3 -I run_eval.py [--model deepseek/deepseek-v4-flash] [--passes 3]

The API key is read from $OPENROUTER_API_KEY or the workspace .env
(--env, default ~/_agents/anth/.env; keys OPEN_ROUTER_API, OPENROUTER_API_KEY
or OPENROUTER_API) and is never printed. OPENROUTER_BASE_URL is honoured.
"""

import argparse
import concurrent.futures as cf
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from grade import grade  # noqa: E402

GOLDEN = os.path.join(os.path.dirname(HERE), "golden")
SYSTEM = ("You are a CAD agent working on a FreeCAD document. You are given the document's state as JSON "
          "(schema agentcore.state/1: objects with name, label, type, kind, in_list/out_list, typed properties, "
          "expressions, shape summaries and per-kind detail sections). Answer the question using only this JSON. "
          "Reply with a single JSON object {\"answer\": <value>} and nothing else. Use a JSON number for numbers, "
          "true/false for yes/no questions, a string for names, and a JSON list for lists.")


def _env(path):
    vals = {}
    try:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip().strip("'\"")
    except OSError:
        pass
    return vals


def _key_and_base(env_path):
    file_env = _env(env_path)
    get = lambda k: os.environ.get(k) or file_env.get(k)  # noqa: E731
    key = next((get(k) for k in ("OPENROUTER_API_KEY", "OPEN_ROUTER_API", "OPENROUTER_API") if get(k)), None)
    if not key:
        sys.exit("no OpenRouter key found")
    return key, (get("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1").rstrip("/")


def _parse(text):
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("{"):] if "{" in t else t
    try:
        start, end = t.find("{"), t.rfind("}")
        return json.loads(t[start:end + 1])["answer"]
    except (ValueError, KeyError, TypeError):
        return text.strip()


def ask(key, base, model, dump_text, question, temperature):
    body = {"model": model, "temperature": temperature, "max_tokens": 4000, "usage": {"include": True},
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": "STATE:\n" + dump_text + "\n\nQUESTION: " + question}]}
    req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    for attempt in range(4):
        try:
            t0 = time.time()
            with urllib.request.urlopen(req, timeout=180) as r:
                d = json.load(r)
            u = d.get("usage") or {}
            text = d["choices"][0]["message"].get("content") or ""
            return {"text": text, "answer": _parse(text), "model": d.get("model", model),
                    "tokens_in": u.get("prompt_tokens"), "tokens_out": u.get("completion_tokens"),
                    "cost_usd": u.get("cost"), "latency_s": round(time.time() - t0, 2)}
        except (urllib.error.URLError, KeyError, ValueError, TimeoutError) as e:
            err = str(e)[:200]
            time.sleep(2 * (attempt + 1))
    return {"text": "", "answer": None, "model": model, "error": err, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="deepseek/deepseek-v4-flash")
    ap.add_argument("--passes", type=int, default=3)
    ap.add_argument("--temperature", type=float, default=0.5)
    ap.add_argument("--env", default=os.path.expanduser("~/_agents/anth/.env"))
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--out", default=os.path.join(HERE, "results.json"))
    a = ap.parse_args()
    key, base = _key_and_base(a.env)
    tasks = json.load(open(os.path.join(HERE, "truth.json"), encoding="utf-8"))
    dumps = {}
    for t in tasks:
        if t["fixture"] not in dumps:
            with open(os.path.join(GOLDEN, t["fixture"] + ".json"), encoding="utf-8") as f:
                dumps[t["fixture"]] = json.dumps(json.load(f), separators=(",", ":"), sort_keys=True)
    jobs = [(p, t) for p in range(a.passes) for t in tasks]
    results = [None] * len(jobs)
    with cf.ThreadPoolExecutor(a.jobs) as ex:
        futs = {ex.submit(ask, key, base, a.model, dumps[t["fixture"]], t["question"], a.temperature): i
                for i, (p, t) in enumerate(jobs)}
        for f in cf.as_completed(futs):
            i = futs[f]
            p, t = jobs[i]
            r = f.result()
            results[i] = dict(task=t["id"], fixture=t["fixture"], kind=t["kind"], truth=t["truth"], pass_index=p,
                              correct=grade(t["kind"], t["truth"], r["answer"]), **r)
    per_pass = [sum(r["correct"] for r in results if r["pass_index"] == p) for p in range(a.passes)]
    all_three = sum(all(r["correct"] for r in results if r["task"] == t["id"]) for t in tasks)
    summary = {
        "model": a.model, "passes": a.passes, "tasks": len(tasks), "temperature": a.temperature,
        "correct_per_pass": per_pass, "accuracy_per_pass": [round(c / len(tasks), 4) for c in per_pass],
        "accuracy_overall": round(sum(per_pass) / (len(tasks) * a.passes), 4),
        "tasks_correct_in_all_passes": all_three,
        "tokens_in": sum(r["tokens_in"] or 0 for r in results), "tokens_out": sum(r["tokens_out"] or 0 for r in results),
        "cost_usd": round(sum(r["cost_usd"] or 0 for r in results), 6),
        "errors": sum(1 for r in results if r.get("error")),
        "dump_chars": {k: len(v) for k, v in sorted(dumps.items())},
    }
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "results": results}, f, indent=1, sort_keys=True)
        f.write("\n")
    print(json.dumps(summary, indent=1))
    for r in results:
        if not r["correct"]:
            print("WRONG pass%d %s truth=%r answer=%r" % (r["pass_index"], r["task"], r["truth"], r["answer"]))


if __name__ == "__main__":
    main()
