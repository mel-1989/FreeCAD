# SPDX-License-Identifier: LGPL-2.1-or-later
"""F2 verify checks: analytic truths and a seeded mutant per check (mutation score 100%)."""

import re
import unittest

import FreeCAD
import Part

from AgentCore import verify
from AgentCoreTests._verify_common import TRUTHS, Fixture, builders


def one(doc, spec):
    res = verify.run_checks(doc, [spec])["results"][0]
    assert res["status"] == "ok", res
    return res


class TestChecksAnalytic(unittest.TestCase):
    def test_box_clean_defaults_pass(self):
        with Fixture("box") as doc:
            out = verify.run_checks(doc)
            self.assertTrue(out["ok"], out)
            self.assertEqual([r["check"] for r in out["results"]], verify.DEFAULT_CHECKS)
            for r in out["results"]:
                self.assertEqual(set(["check", "ok", "status", "entities", "details"]), set(r))

    def test_bbox_and_mass_truth(self):
        t = TRUTHS["box"]
        with Fixture("box") as doc:
            r = one(doc, {"check": "bbox", "expect": {"Box": {"size": t["bbox_size"], "min": [0, 0, 0]}}})
            self.assertTrue(r["ok"], r)
            r = one(doc, {"check": "mass", "default_density": 1000.0,
                          "expect": {"Box": {"volume_mm3": t["volume"], "area_mm2": t["area"], "com": t["com"],
                                             "mass_kg": t["volume"] * 1000e-9}}})
            self.assertTrue(r["ok"], r)

    def test_tube_and_shell_volume(self):
        with Fixture("tube") as doc:
            v = one(doc, "mass")["details"]["bodies"]["Tube"]["volume_mm3"]
            self.assertAlmostEqual(v, builders.tube_volume(TRUTHS["tube"]), delta=1e-6 * v)
        with Fixture("shell") as doc:
            v = one(doc, "mass")["details"]["bodies"]["Shell"]["volume_mm3"]
            self.assertAlmostEqual(v, TRUTHS["shell"]["volume"], delta=1e-6 * v)

    def test_interference_truth(self):
        with Fixture("pair_overlap") as doc:
            r = one(doc, "interference")
            self.assertFalse(r["ok"])
            self.assertAlmostEqual(r["details"]["interferences"][0]["volume_mm3"], TRUTHS["pair_overlap"]["interference_volume"], places=6)
            self.assertEqual(r["entities"], ["A", "B"])
        with Fixture("pair_gap2") as doc:
            self.assertTrue(one(doc, "interference")["ok"])

    def test_clearance_truth_and_entities(self):
        with Fixture("pair_gap2") as doc:
            r = one(doc, {"check": "clearance", "min": 1.0})
            self.assertTrue(r["ok"], r)
            self.assertAlmostEqual(r["details"]["pairs"][0]["distance_mm"], 2.0, places=6)
            ents = r["details"]["pairs"][0]["entities"]
            self.assertTrue(re.match(r"A:(Face|Edge|Vertex)\d+$", ents[0]) and re.match(r"B:(Face|Edge|Vertex)\d+$", ents[1]), ents)
            r = one(doc, {"check": "clearance", "min": 3.0})
            self.assertFalse(r["ok"])
            self.assertTrue(any(e.startswith("A:") for e in r["entities"]))

    def test_material_density_mass(self):
        for name in ("steel_box", "aluminium_box"):
            t = TRUTHS[name]
            with Fixture(name) as doc:
                r = one(doc, {"check": "mass", "expect": {"Box": {"mass_kg": t["mass_kg"]}}})
                self.assertTrue(r["ok"], r)
                self.assertTrue(r["details"]["bodies"]["Box"]["density_source"].startswith("material:"))

    def test_tolerances_not_hardcoded(self):
        from AgentCore.verify import tolerances

        tol = tolerances.load()
        for k in ("linear_mm", "volume_rel", "interference_volume_mm3", "thickness_abs_mm"):
            self.assertIn(k, tol)

    def test_unknown_check_and_exception_are_structured(self):
        with Fixture("box") as doc:
            r = verify.run_checks(doc, ["nope", "_selftest_raise"])["results"]
            self.assertEqual([x["status"] for x in r], ["error", "error"])
            self.assertFalse(any(x["ok"] for x in r))


# ---- seeded mutants: (check spec, clean fixture, mutate(doc), expected entity in result)

def _m_bbox(doc):
    doc.Box.Height = 31


def _m_interference(doc):
    doc.B.Placement.Base = FreeCAD.Vector(5, 0, 0)


def _m_clearance(doc):
    doc.B.Placement.Base = FreeCAD.Vector(10.5, 0, 0)


def _m_mass(doc):
    doc.Box.ShapeMaterial = builders._material("Aluminum-6061-T6")


def _m_thickness(doc):
    doc.Box.Height = 1.0


def _m_recompute(doc):
    doc.addObject("Part::Cut", "Broken").Base = doc.Box


def _m_validity(doc):
    bx = Part.makeBox(10, 10, 10)
    doc.addObject("Part::Feature", "Open").Shape = Part.Solid(Part.Shell(bx.Faces[:5]))


MUTANTS = [
    ("recompute", {"check": "recompute"}, "box", _m_recompute, "Broken"),
    ("validity", {"check": "validity"}, "box", _m_validity, "Open"),
    ("bbox", {"check": "bbox", "expect": {"Box": {"size": [10, 20, 30]}}}, "box", _m_bbox, "Box"),
    ("interference", {"check": "interference"}, "pair_gap2", _m_interference, "A"),
    ("clearance", {"check": "clearance", "min": 1.0}, "pair_gap2", _m_clearance, "A:"),
    ("mass", {"check": "mass", "expect": {"Box": {"mass_kg": 0.0078}}}, "steel_box", _m_mass, "Box"),
    ("thickness", {"check": "thickness", "min_required": 1.5}, "plate", _m_thickness, "Box:Face"),
]


class TestMutationScore(unittest.TestCase):
    def test_every_check_catches_its_mutant_and_passes_clean(self):
        caught, passed_clean = 0, 0
        for check, spec, fixture, mutate, ent in MUTANTS:
            with Fixture(fixture) as doc:
                clean = one(doc, spec)
                self.assertTrue(clean["ok"], f"{check}: clean part must pass: {clean}")
                passed_clean += 1
                mutate(doc)
                doc.recompute() if check != "recompute" else None
                dirty = one(doc, spec)
                self.assertFalse(dirty["ok"], f"{check}: mutant escaped: {dirty}")
                self.assertTrue(any(e.startswith(ent) for e in dirty["entities"]), (check, dirty["entities"]))
                caught += 1
        score = caught / len(MUTANTS)
        self.assertEqual(score, 1.0)
        self.assertEqual(passed_clean, len(MUTANTS))
        # every shipped real check has a mutant
        real = {c for c in verify.CHECKS if not c.startswith("_")}
        self.assertEqual(real, {m[0] for m in MUTANTS})
