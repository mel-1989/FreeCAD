# SPDX-License-Identifier: LGPL-2.1-or-later
"""Minimum wall thickness against analytic plates, tubes and shells (tolerance in tolerances.json)."""

import unittest

from AgentCore import verify
from AgentCore.verify import thickness, tolerances
from AgentCoreTests._verify_common import TRUTHS, Fixture

CASES = ["plate", "plate_thin", "tube", "shell", "sphere_shell", "box"]


def measure(name, samples=5):
    with Fixture(name) as doc:
        r = verify.run_checks(doc, [{"check": "thickness", "samples": samples}])["results"][0]
        assert r["status"] == "ok", r
        return r["details"]


class TestThickness(unittest.TestCase):
    def test_analytic_within_recorded_tolerance(self):
        tol = tolerances.load()
        for name in CASES:
            truth = TRUTHS[name]["thickness_min"]
            got = measure(name)
            self.assertTrue(got["complete"])
            err = abs(got["min_thickness_mm"] - truth)
            allowed = max(tol["thickness_abs_mm"], tol["thickness_rel"] * truth)
            self.assertLessEqual(err, allowed, f"{name}: truth {truth} got {got['min_thickness_mm']} err {err}")

    def test_entity_points_to_a_face(self):
        with Fixture("plate") as doc:
            r = verify.run_checks(doc, [{"check": "thickness", "min_required": 5.0}])["results"][0]
            self.assertFalse(r["ok"])
            self.assertRegex(r["entities"][0], r"^Box:Face\d+$")

    def test_kernel_is_replaceable(self):
        calls = []

        def fake(shape, samples=5, time_budget_s=None):
            calls.append(1)
            return {"min": 123.0, "point": [0, 0, 0], "face": 1, "rays": 0, "complete": True, "kernel": "fake"}

        thickness.set_kernel(fake)
        try:
            self.assertEqual(measure("plate")["min_thickness_mm"], 123.0)
        finally:
            thickness.set_kernel(None)
        self.assertTrue(calls)
        self.assertAlmostEqual(measure("plate")["min_thickness_mm"], 2.0, places=3)

    def test_time_budget_truncates(self):
        with Fixture("shell") as doc:
            r = verify.run_checks(doc, [{"check": "thickness", "time_budget_s": 0.0}])["results"][0]
            self.assertFalse(r["details"]["complete"])
