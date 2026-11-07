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


def hole_doc(name="HoleDoc"):
    """A 100 x 20 x 20 mm box with a through hole of radius 4 at (50, 10), fixed at x = 0, pulled at x = 100 (Part::Cut named Body)."""
    doc = FreeCAD.newDocument(name)
    V = FreeCAD.Vector
    box = doc.addObject("Part::Box", "Box")
    box.Length, box.Width, box.Height = 100, 20, 20
    hole = doc.addObject("Part::Cylinder", "Hole")
    hole.Radius, hole.Height = 4, 20
    hole.Placement = FreeCAD.Placement(V(50, 10, 0), FreeCAD.Rotation())
    cut = doc.addObject("Part::Cut", "Body")
    cut.Base, cut.Tool = box, hole
    doc.recompute()
    return doc, cut, hole


def face_at(body, axis, val):
    for i, f in enumerate(body.Shape.Faces, 1):
        b = f.BoundBox
        lo_, hi_ = [(b.XMin, b.XMax), (b.YMin, b.YMax), (b.ZMin, b.ZMax)][axis]
        if abs(lo_ - val) < 1e-6 and abs(hi_ - val) < 1e-6:
            return [[body.Name, f"Face{i}"]]


def hole_spec(body, h0="8 mm", **opts):
    o = {"h0": h0}
    o.update(opts)
    return {"body": body.Name, "material": {"E": "210 GPa", "nu": 0.3, "density": "7850 kg/m^3"},
            "supports": [{"refs": face_at(body, 0, 0), "fixed": "xyz"}],
            "loads": [{"type": "force", "value": "2000 N", "refs": face_at(body, 0, 100), "direction": [0, 0, -1]}], "options": o}


class TestStudyRepair(unittest.TestCase):
    """Run-067 second repair round: one regression per verifier finding. Everything here but the hanging bar stops before any mesh."""

    def setUp(self):
        self.doc, self.body, self.hole = hole_doc()

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def hash_of(self, doc, name):
        import json
        from femstudy import model, stale

        an = doc.getObject(name)
        live, _ = model.read_live(doc, an)
        return stale.input_hash(doc, json.loads(an.StudySpec), live)

    def test_hash_sees_a_moved_hole_and_is_stable(self):
        import tempfile

        d = self.doc
        name = femstudy.define_static(d, hole_spec(self.body))
        h0 = self.hash_of(d, name)
        d.recompute()
        self.body.Shape.tessellate(0.1)  # a triangulation must not change the hash
        self.assertEqual(self.hash_of(d, name), h0)
        for f in self.body.Shape.Faces:
            f.CenterOfMass, f.BoundBox
        self.assertEqual(self.hash_of(d, name), h0)
        for x, y in ((8, 10), (50, 6)):  # the verifier's two edits: same volume, same bounding box, same referenced faces
            self.hole.Placement = FreeCAD.Placement(FreeCAD.Vector(x, y, 0), FreeCAD.Rotation())
            d.recompute()
            self.assertNotEqual(self.hash_of(d, name), h0, (x, y))
            self.assertTrue(femstudy.is_stale(d, name))
        self.hole.Placement = FreeCAD.Placement(FreeCAD.Vector(50, 10, 0), FreeCAD.Rotation())
        d.recompute()
        self.assertEqual(self.hash_of(d, name), h0)
        path = os.path.join(tempfile.mkdtemp(prefix="study_hash_"), "h.FCStd")
        d.saveAs(path)
        FreeCAD.closeDocument(d.Name)
        self.doc = d2 = FreeCAD.openDocument(path)
        self.assertEqual(self.hash_of(d2, name), h0)

    def test_hash_of_the_old_form_reads_stale(self):
        import hashlib
        import json

        d = self.doc
        name = femstudy.define_static(d, hole_spec(self.body))
        an = d.getObject(name)
        an.InputHash = hashlib.sha256(json.dumps({"spec": json.loads(an.StudySpec), "body": [1.0], "refs": [], "material": {}}, sort_keys=True).encode()).hexdigest()
        self.assertTrue(femstudy.is_stale(d, name))

    def test_nonfinite_numbers_are_spec_errors(self):
        nan, inf = float("nan"), float("inf")
        for path, val in ((("loads", 0, "direction"), [nan, 0, -1]), (("loads", 0, "direction"), [inf, 0, 0]), (("material", "nu"), nan),
                          (("options", "ratio"), inf), (("options", "target"), nan), (("options", "h0"), nan), (("loads", 0, "value"), nan)):
            s = hole_spec(self.body)
            t = s
            for k in path[:-1]:
                t = t[k]
            t[path[-1]] = val
            with self.assertRaises(StudySpecError, msg=str(path)):
                femstudy.define_static(self.doc, s)

    def test_checks_fail_closed_on_nan(self):
        from femstudy import checks

        nan = float("nan")
        self.assertIsNotNone(checks.check_equilibrium([nan, 0, 0], [1000.0, 0, 0]))
        self.assertIsNotNone(checks.check_equilibrium([0, 0, 0], [nan, 0, 0]))
        self.assertIsNotNone(checks.check_displacement(nan, self.body.Shape.BoundBox))
        self.assertIsNotNone(checks.check_yield(nan, 250.0))
        self.assertIsNone(checks.check_equilibrium([0, 0, 0], [0, 0, 0], 2000.0))  # cancelling loads: no division by the zero resultant
        self.assertIsNotNone(checks.check_equilibrium([5.0, 0, 0], [0, 0, 0], 2000.0))

    def test_null_loads_are_refused_and_cancelling_loads_are_not(self):
        from femstudy import checks
        from femstudy.spec import parse_spec

        d, b = self.doc, self.body
        s = hole_spec(b)
        s["loads"][0]["value"] = "0 N"
        r = femstudy.run_static(d, s)
        self.assertEqual((r["verdict"], r["code"], r["levels"]), ("refused", "no_load", []))
        for ref in ([[b.Name, "Vertex1"]], [[b.Name, "Edge1"]]):
            s = hole_spec(b)
            s["loads"] = [{"type": "pressure", "value": "5 MPa", "refs": ref}]
            r = femstudy.run_static(d, s)
            self.assertEqual((r["verdict"], r["code"]), ("refused", "no_load"), ref)
        s = hole_spec(b)
        s["loads"].append(dict(s["loads"][0], direction=[0, 0, 1]))
        total = checks.check_loads(d, parse_spec(s), [2000.0, 2000.0], 1.0)
        self.assertEqual(total, 4000.0)

    def test_reason_claims_only_what_was_tested_and_two_new_warnings(self):
        from femstudy import convergence

        def lv(n, u, vm, h):
            return {"elements": n, "max_displacement": u, "energy": u, "max_von_mises": vm, "h": h}

        levels = [lv(100, 1.0, 100.0, 8.0), lv(800, 1.0, 120.0, 4.0), lv(6400, 1.0, 144.0, 2.0)]
        a = convergence.assess(levels, 0.01)
        v, c, why = convergence.verdict(levels, 0.01, False, a)
        self.assertEqual((v, c), ("converged", "ok"))
        self.assertNotIn("stable under refinement", why)
        self.assertIn("+20.0%", why)
        codes = [w["code"] for w in convergence.stress_warnings(levels, 1.5)]
        self.assertEqual(codes, ["stress_not_settled", "stress_under_resolved"])
        self.assertEqual([w["code"] for w in convergence.stress_warnings(levels, 3.0)], ["stress_not_settled"])
        calm = [lv(100, 1.0, 100.0, 8.0), lv(800, 1.0, 102.0, 4.0), lv(6400, 1.0, 103.0, 2.0)]
        self.assertEqual(convergence.stress_warnings(calm, None), [])

    def test_no_raw_exceptions_on_odd_specs(self):
        d, b = self.doc, self.body
        for sub in ("", "Face1.5", "face1", "Face0"):
            s = hole_spec(b)
            s["loads"][0]["refs"] = [[b.Name, sub]]
            with self.assertRaises(StudySpecError, msg=repr(sub)):
                femstudy.run_static(d, s)
        s = hole_spec(b)
        s["loads"][0]["refs"] = [[b.Name, "Face99"]]
        self.assertEqual(femstudy.run_static(d, s)["code"], "reference_lost")
        s = hole_spec(b, h0="1e9 mm")
        r = femstudy.run_static(d, s)
        self.assertEqual((r["verdict"], r["code"]), ("refused", "h0_range"))
        s = hole_spec(b)
        s["name"] = "Body"  # an existing object that is not an Analysis
        with self.assertRaises(StudySpecError):
            femstudy.run_static(d, s)
        with self.assertRaises(StudySpecError):
            femstudy.run_static(d, "Body")
        with self.assertRaises(StudySpecError):
            femstudy.run_static(d, 42)

    def test_element_cap_is_checked_before_meshing(self):
        import time

        t0 = time.time()
        r = femstudy.run_static(self.doc, hole_spec(self.body, h0="0.2 mm"))
        self.assertEqual((r["verdict"], r["code"], r["levels"]), ("refused", "element_cap", []))
        self.assertLess(time.time() - t0, 10.0)
        self.assertIn("nothing was meshed", r["reason"])

    def test_option_bounds(self):
        inf = float("inf")
        for opt in ({"ratio": 1.4}, {"ratio": 1.1}, {"ratio": inf}, {"target": 10}, {"target": 0.21}, {"target": 0}, {"target": -0.1}, {"levels": 1}, {"levels": 0}):
            with self.assertRaises(StudySpecError, msg=str(opt)):
                femstudy.define_static(self.doc, hole_spec(self.body, **opt))
        femstudy.define_static(self.doc, hole_spec(self.body, ratio=1.5, target=0.2, levels=2))

    def test_more_than_one_solid_is_refused(self):
        d = self.doc
        V = FreeCAD.Vector
        two = d.addObject("Part::Feature", "Two")
        two.Shape = Part.makeCompound([Part.makeBox(60, 10, 10), Part.makeBox(60, 10, 10, V(0, 30, 0))])
        d.recompute()
        s = hole_spec(self.body)
        s["body"] = "Two"
        s["supports"][0]["refs"] = [["Two", "Face1"]]
        s["loads"][0]["refs"] = [["Two", "Face2"]]
        r = femstudy.run_static(d, s)
        self.assertEqual((r["verdict"], r["code"]), ("refused", "not_single_solid"), r["reason"])

    def test_poisson_range(self):
        for nu in (-0.99, -0.5, 0.0, 0.49):
            self.assertEqual(resolve_material({"E": "210 GPa", "nu": nu})["nu"], nu)
        for nu in (-1.0, -1.5, 0.5, 0.7):
            with self.assertRaises(Refusal, msg=str(nu)) as cm:
                resolve_material({"E": "210 GPa", "nu": nu})
            self.assertEqual(cm.exception.code, "poisson_range")

    def test_hanging_bar_gravity_reaction_and_tip(self):
        d = FreeCAD.newDocument("Hang")
        try:
            body = d.addObject("Part::Feature", "Body")
            body.Shape = Part.makeBox(10, 10, 100)
            d.recompute()
            spec = {"body": "Body", "material": {"E": "210 GPa", "nu": 0.0, "density": "7850 kg/m^3"},
                    "supports": [{"refs": face_at(body, 2, 100), "fixed": "xyz"}], "loads": [{"type": "gravity", "value": "9.81 m/s^2", "direction": [0, 0, -1]}],
                    "options": {"h0": "12 mm"}}
            r = femstudy.run_static(d, spec)
            self.assertEqual((r["verdict"], r["code"]), ("converged", "ok"), r["reason"])
            weight = 7850e-12 * 9810.0 * 10 * 10 * 100  # N
            lv = r["levels"][-1]
            self.assertLess(abs(lv["reaction_sum"][2] - weight) / weight, 1e-4)
            tip = 7850e-12 * 9810.0 * 100.0**2 / (2 * 210000.0)
            self.assertLess(abs(lv["max_displacement"] - tip) / tip, 1e-4)
        finally:
            FreeCAD.closeDocument(d.Name)
