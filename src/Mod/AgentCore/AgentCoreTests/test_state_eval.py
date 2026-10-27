# SPDX-License-Identifier: LGPL-2.1-or-later
"""F1: agent-usability eval, checked offline.

fixtures/state/eval/ holds 40 query tasks (tasks.py), their ground truth
computed from the FreeCAD documents (truth.json, written by prepare.py), the
grader (grade.py), the OpenRouter runner (run_eval.py) and the stored run
(results.json: 40 tasks x 3 passes). No network here: this test proves the
stored truth still matches the documents, re-grades every stored answer, and
holds the run to the proposed bar (the brief sets none): at least 90% correct
in every pass and at least 95% over all passes.
"""

import json
import os
import sys
import unittest

EVAL = os.path.join(os.path.dirname(__file__), "fixtures", "state", "eval")
FIX = os.path.dirname(EVAL)
for p in (FIX, EVAL):
    if p not in sys.path:
        sys.path.insert(0, p)

import grade  # noqa: E402
import prepare  # noqa: E402
import tasks  # noqa: E402

MIN_PASS_ACCURACY = 0.90
MIN_OVERALL_ACCURACY = 0.95


def _load(name):
    with open(os.path.join(EVAL, name), encoding="utf-8") as f:
        return json.load(f)


class TestStateEval(unittest.TestCase):
    def test_forty_tasks_over_six_fixtures(self):
        self.assertEqual(len(tasks.TASKS), 40)
        self.assertGreaterEqual(len({t["fixture"] for t in tasks.TASKS}), 6)

    def test_truth_regenerates_from_documents(self):
        fresh = prepare.compute_truth()
        stored = _load("truth.json")
        self.assertEqual([t["id"] for t in fresh], [t["id"] for t in stored])
        for f, s in zip(fresh, stored):
            self.assertEqual(f["question"], s["question"], f["id"])
            # the stored truth must grade as correct against a fresh computation
            self.assertTrue(grade.grade(f["kind"], f["truth"], s["truth"]), (f["id"], f["truth"], s["truth"]))

    def test_grader(self):
        g = grade.grade
        self.assertTrue(g("number", 3475.75222, "3475.75 mm^3"))
        self.assertFalse(g("number", 3475.75222, 3400))
        self.assertTrue(g("int", 14, 14))
        self.assertFalse(g("int", 14, 14.5))
        self.assertTrue(g("bool", False, "false"))
        self.assertFalse(g("bool", False, True))
        self.assertTrue(g("set", ["Body", "Pocket"], ["pocket", "Body"]))
        self.assertFalse(g("set", ["Body", "Pocket"], ["Body"]))
        self.assertTrue(g("set", [3, 4], [4, 3]))
        self.assertFalse(g("string", "Fillet", ""))
        self.assertFalse(g("number", 6.0, None))

    def test_stored_run_meets_bar(self):
        run = _load("results.json")
        s, res = run["summary"], run["results"]
        truth = {t["id"]: t for t in _load("truth.json")}
        self.assertEqual(s["tasks"], 40)
        self.assertGreaterEqual(s["passes"], 3)
        self.assertEqual(len(res), s["tasks"] * s["passes"])
        self.assertEqual(s["errors"], 0)
        seen = set()
        for r in res:
            self.assertEqual(r["truth"], truth[r["task"]]["truth"], r["task"])
            self.assertEqual(grade.grade(r["kind"], r["truth"], r["answer"]), r["correct"], r)
            self.assertIn("cost_usd", r)
            self.assertIn("tokens_in", r)
            seen.add((r["task"], r["pass_index"]))
        self.assertEqual(len(seen), len(res))
        per_pass = [sum(r["correct"] for r in res if r["pass_index"] == p) / s["tasks"] for p in range(s["passes"])]
        self.assertEqual([round(x, 4) for x in per_pass], s["accuracy_per_pass"])
        for acc in per_pass:
            self.assertGreaterEqual(acc, MIN_PASS_ACCURACY)
        self.assertGreaterEqual(sum(per_pass) / len(per_pass), MIN_OVERALL_ACCURACY)
