# SPDX-License-Identifier: LGPL-2.1-or-later
"""Fast unit suite for femstudy (G11 slice 1a, run 067): unit parsing, refusal codes, spec errors, one solved bar, the loud missing-binary failure and
the stale flag. Registered as the suite TestFemStudyApp. Nothing here skips on a missing binary: without gmsh and ccx on PATH the solved-bar test fails,
which is the point (brief check 8)."""

import os
import unittest

import FreeCAD
import Part

import femstudy
from femstudy.spec import Refusal, StudySpecError, parse_quantity, resolve_material

PSI_MPA = 0.006894757293168
LBF_N = 4.4482216152605


def bar_spec(doc, load="4 kN", E="70 GPa", nu=0.0, supports=True, loads=True, h0="12 mm", body=None):
    """A 60 x 10 x 10 mm bar along x, fixed at x = 0, pulled at x = 60; closed form delta = P L / (A E)."""
    if body is None:
        body = doc.addObject("Part::Feature", "Body")
        body.Shape = Part.makeBox(60, 10, 10)
        doc.recompute()

    def face(x):
        for i, f in enumerate(body.Shape.Faces, 1):
            b = f.BoundBox
            if b.XLength < 1e-6 and abs(b.XMin - x) < 1e-6:
                return [[body.Name, f"Face{i}"]]

    return {"body": body.Name, "material": {"E": E, "nu": nu},
            "supports": [{"refs": face(0), "fixed": "xyz"}] if supports else [],
            "loads": [{"type": "force", "value": load, "refs": face(60), "direction": [1, 0, 0]}] if loads else [],
            "options": {"h0": h0}}


class TestStudyUnits(unittest.TestCase):
    def test_quantities_agree(self):
        mpa = parse_quantity("210 GPa", "pressure", "E")
        self.assertAlmostEqual(mpa, 210000.0, places=6)
        for s in ("210000 MPa", "2.1e11 Pa", f"{210000.0 / PSI_MPA:.12g} psi"):
            self.assertLess(abs(parse_quantity(s, "pressure", "E") - mpa) / mpa, 1e-9, s)
        n = parse_quantity("1000 N", "force", "P")
        for s in ("1 kN", "1000000 mN", f"{1000.0 / LBF_N:.12g} lbf"):
            self.assertLess(abs(parse_quantity(s, "force", "P") - n) / n, 1e-9, s)

    def test_unit_refusal_codes(self):
        for value, dim, code in (("210000", "pressure", "unit_missing"), (210000, "pressure", "unit_missing"), ("210 N", "pressure", "unit_dimension"),
                                 ("5 kip", "force", "unit_unknown"), ("5 furlong", "force", "unit_unknown"), ("5 mm", "force", "unit_dimension")):
            with self.assertRaises(Refusal, msg=repr(value)) as cm:
                parse_quantity(value, dim, "x")
            self.assertEqual(cm.exception.code, code, repr(value))

    def test_material_refusals(self):
        with self.assertRaises(Refusal) as cm:
            resolve_material({"E": "210 GPa"})
        self.assertEqual(cm.exception.code, "material_incomplete")
        with self.assertRaises(Refusal) as cm:
            resolve_material({"E": "210 GPa", "nu": 0.5})
        self.assertEqual(cm.exception.code, "poisson_range")

    def test_spec_errors_raise(self):
        doc = FreeCAD.newDocument("StudySpecErr")
        good = bar_spec(doc)
        bad = []
        s = dict(good)
        s["colour"] = "red"
        bad.append(s)
        s = dict(good)
        del s["loads"]
        bad.append(s)
        s = dict(good)
        s["material"] = {"E": "70 GPa", "nu": "abc"}
        bad.append(s)
        s = dict(good)
        s["supports"] = [{"refs": good["supports"][0]["refs"], "fixed": "xq"}]
        bad.append(s)
        s = dict(good)
        s["loads"] = [dict(good["loads"][0], type="torque")]
        bad.append(s)
        s = dict(good)
        s["options"] = {"h0": "10 mm", "levels": "three"}
        bad.append(s)
        for b in bad:
            with self.assertRaises(StudySpecError):
                femstudy.define_static(doc, b)
        FreeCAD.closeDocument(doc.Name)


class TestStudyRun(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument("StudyRun")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def test_refusals_solve_nothing(self):
        d = self.doc
        for kwargs, code in (({"supports": False}, "no_support"), ({"loads": False}, "no_load"), ({"E": "70000"}, "unit_missing"),
                             ({"E": "70 N"}, "unit_dimension"), ({"nu": 0.5}, "poisson_range"), ({"load": "5 kip"}, "unit_unknown")):
            r = femstudy.run_static(d, bar_spec(d, body=d.getObject("Body"), **kwargs))
            self.assertEqual((r["verdict"], r["code"]), ("refused", code), kwargs)
            self.assertTrue(r["reason"])
            self.assertEqual(r["levels"], [])
        gone = bar_spec(d, body=d.getObject("Body"))
        gone["body"] = "Nope"
        self.assertEqual(femstudy.run_static(d, gone)["code"], "body_invalid")

    def test_rigid_body_refused(self):
        d = self.doc
        spec = bar_spec(d)
        spec["supports"][0]["fixed"] = "x"
        r = femstudy.run_static(d, spec)
        self.assertEqual((r["verdict"], r["code"]), ("refused", "rigid_body_motion"))

    def test_solved_bar_is_converged_and_exact(self):
        d = self.doc
        r = femstudy.run_static(d, bar_spec(d))
        self.assertEqual((r["verdict"], r["code"]), ("converged", "ok"), r["reason"])
        self.assertEqual(r["units"], {"length": "mm", "force": "N", "stress": "MPa", "energy": "mJ"})
        self.assertEqual(len(r["levels"]), 3)
        truth = 4000.0 * 60.0 / (100.0 * 70000.0)  # delta = P L / (A E)
        lv = r["levels"][-1]
        self.assertLess(abs(lv["max_displacement"] - truth) / truth, 1e-5)
        self.assertLess(abs(lv["max_von_mises"] - 40.0) / 40.0, 2e-5)
        for lv in r["levels"]:
            miss = sum((a + b) ** 2 for a, b in zip(lv["reaction_sum"], lv["applied_sum"])) ** 0.5 / 4000.0
            self.assertLess(miss, 1e-3)
        self.assertIsNotNone(d.getObject(r["levels"][-1]["result"]))

    def test_missing_binaries_fail_loudly(self):
        d = self.doc
        old = os.environ["PATH"]
        os.environ["PATH"] = "/usr/bin:/bin"
        try:
            r = femstudy.run_static(d, bar_spec(d))
        finally:
            os.environ["PATH"] = old
        self.assertEqual(r["verdict"], "failed")
        self.assertIn(r["code"], ("mesher_missing", "solver_missing"))
        self.assertTrue(r["reason"])

    def test_element_cap_stops_refinement(self):
        d = self.doc
        spec = bar_spec(d)
        spec["options"]["max_elements"] = 150
        r = femstudy.run_static(d, spec)
        self.assertEqual((r["verdict"], r["code"]), ("unconverged", "element_cap"), r["reason"])

    def test_stale_flag(self):
        d = self.doc
        r = femstudy.run_static(d, bar_spec(d))
        name = r["analysis"]
        self.assertFalse(femstudy.is_stale(d, name))
        self.assertFalse(femstudy.stored_result(d, name)["stale"])
        an = d.getObject(name)
        import json

        s = json.loads(an.StudySpec)
        s["loads"][0]["value"] = "5 kN"
        an.StudySpec = json.dumps(s)
        self.assertTrue(femstudy.is_stale(d, name))
        self.assertTrue(femstudy.stored_result(d, name)["stale"])
        r2 = femstudy.run_static(d, name)
        self.assertFalse(r2["stale"])
        self.assertFalse(femstudy.is_stale(d, name))

    def test_stale_survives_save_and_reopen(self):
        import tempfile

        d = self.doc
        r = femstudy.run_static(d, bar_spec(d))
        name = r["analysis"]
        path = os.path.join(tempfile.mkdtemp(prefix="study_save_"), "s.FCStd")
        d.saveAs(path)
        FreeCAD.closeDocument(d.Name)
        d2 = FreeCAD.openDocument(path)
        self.doc = d2
        self.assertFalse(femstudy.is_stale(d2, name))
        stored = femstudy.stored_result(d2, name)
        self.assertEqual(stored["verdict"], "converged")
        d2.getObject("Body").Placement = FreeCAD.Placement(FreeCAD.Vector(1, 0, 0), FreeCAD.Rotation())
        d2.recompute()
        self.assertTrue(femstudy.is_stale(d2, name))
        self.assertTrue(femstudy.stored_result(d2, name)["stale"])
