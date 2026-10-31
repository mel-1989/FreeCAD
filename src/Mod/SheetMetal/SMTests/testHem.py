# SPDX-License-Identifier: LGPL-2.1-or-later
"""Oracle tests for the clean-room hem (G07 slice 2).

Expected values are computed from the case parameters with fabrication geometry only:
- bend volume   = pi * T * (R + T/2) * W      (half annulus area times width)
- return leg    = T * L * W
- leg outer face at height 2R + T above the base face the hem folds towards (outward normal)
- leg end plane L back from the bend tangent plane (x = PL - L for the +x edge)
- flat length   = plate + pi * (R + K*T) + L   (Machinery's Handbook, bend allowance)
R is gap/2 for an open hem and 0.1*T for a closed one.
"""

import math
import unittest
from SMTests.quiet import expected_recompute_errors

import FreeCAD
import Part

import SheetMetalHemFeature as HF
from SMTests.testEdgeFlange import PL, W, find_edge, make_box
from SMTests.testOracle import old_unfold


def _case(name, T, hem_type, gap, L, flip=False, end_gap=0.0, edges=("+x",), k=0.42):
    R = 0.1 * T if hem_type == "Closed" else gap / 2.0
    return dict(name=name, T=T, type=hem_type, gap=gap, L=L, flip=flip, end_gap=end_gap,
                edges=edges, k=k, R=R)


CASES = [
    _case("h01_closed_T1", 1.0, "Closed", 0.0, 5.0),
    _case("h02_closed_T2", 2.0, "Closed", 0.0, 10.0),
    _case("h03_closed_T3_L20", 3.0, "Closed", 0.0, 20.0, k=0.33),
    _case("h04_open_T1_g.5T", 1.0, "Open", 0.5, 8.0),
    _case("h05_open_T2_g1T", 2.0, "Open", 2.0, 12.0),
    _case("h06_open_T3_g2T", 3.0, "Open", 6.0, 15.0, k=0.5),
    _case("h07_open_T2_g1.5T_flip", 2.0, "Open", 3.0, 10.0, flip=True),
    _case("h08_closed_T2_flip", 2.0, "Closed", 0.0, 6.0, flip=True),
    _case("h09_open_T1.5_endgap", 1.5, "Open", 1.5, 9.0, end_gap=3.0),
    _case("h10_open_T2_two_edges", 2.0, "Open", 4.0, 14.0, edges=("+x", "-x")),
    _case("h11_closed_T1_two_edges", 1.0, "Closed", 0.0, 7.0, edges=("+x", "-x")),
]

OUT = {"+x": 1.0, "-x": -1.0}


class Doc(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument("HemTest")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def feature(self, c):
        box = make_box(self.doc, c["T"])
        names = [find_edge(box, e) for e in c["edges"]]
        hem = HF.make_hem(self.doc, box, names, hemType=c["type"], gap=c["gap"] or 1.0,
                          length=c["L"], kfactor=c["k"], flip=c["flip"], endGap=c["end_gap"])
        return box, hem


class TestHem(Doc):
    def test_oracle_cases(self):
        self.assertGreaterEqual(len(CASES), 8)
        for c in CASES:
            with self.subTest(case=c["name"]):
                box, hem = self.feature(c)
                sh = hem.Shape
                self.assertTrue(sh.isValid())
                self.assertEqual(len(sh.Solids), 1)
                T, R, L = c["T"], c["R"], c["L"]
                width = W - 2 * c["end_gap"]
                n = len(c["edges"])
                vol = PL * W * T + n * (math.pi * T * (R + T / 2.0) * width + T * L * width)
                self.assertAlmostEqual(sh.Volume / vol - 1.0, 0.0, delta=1e-6, msg="volume")
                self.assertAlmostEqual(hem.radius.Value, R, 9)
                cyl = sorted(round(f.Surface.Radius, 9) for f in sh.Faces
                             if f.Surface.TypeId == "Part::GeomCylinder")
                self.assertEqual(cyl, sorted([round(R, 9), round(R + T, 9)] * n))
                # return leg outer face: plane (outward normal), area L * width
                # (base top face is at z = T; a flipped hem folds under the bottom face, z = 0)
                zleg = -(2 * R + T) if c["flip"] else 2 * T + 2 * R
                sgn = -1.0 if c["flip"] else 1.0
                legs = [f for f in sh.Faces if f.Surface.TypeId == "Part::GeomPlane"
                        and abs(f.normalAt(0, 0).z - sgn) < 1e-9
                        and abs(f.CenterOfMass.z - zleg) < 1e-6]
                self.assertEqual(len(legs), n, "return leg outer face position")
                for f in legs:
                    self.assertAlmostEqual(f.Area, L * width, delta=1e-6 * L * width)
                # leg end plane: L back from the bend tangent plane
                for e in c["edges"]:
                    xend = PL - L if e == "+x" else L
                    ends = [f for f in sh.Faces if f.Surface.TypeId == "Part::GeomPlane"
                            and abs(abs(f.normalAt(0, 0).x) - 1.0) < 1e-9
                            and abs(f.CenterOfMass.x - xend) < 1e-6]
                    self.assertGreaterEqual(len(ends), 1, "leg end plane for %s" % e)
                    self.assertAlmostEqual(ends[0].Area, T * width, delta=1e-6 * T * width)

    def test_flat_length_through_unfolder(self):
        for c in CASES:
            with self.subTest(case=c["name"]):
                box, hem = self.feature(c)
                flat, err = old_unfold(hem, {1: c["k"]})
                self.assertEqual(err, 0)
                T, L = c["T"], c["L"]
                n = len(c["edges"])
                ba = math.pi * (c["R"] + c["k"] * T)
                width = W - 2 * c["end_gap"]
                if c["end_gap"] == 0.0:
                    self.assertAlmostEqual(flat.Volume / (T * W), PL + n * (ba + L), delta=1e-3)
                else:
                    top = max(f.Area for f in flat.Faces if f.Surface.TypeId == "Part::GeomPlane")
                    self.assertAlmostEqual(top, PL * W + width * (ba + L), delta=1e-3)

    def test_recompute_after_base_change(self):
        c = _case("recompute", 2.0, "Open", 3.0, 10.0)
        box, hem = self.feature(c)
        box.Length = 55.0
        self.doc.recompute()
        self.assertNotIn("Invalid", hem.State)
        T, R, L = 2.0, 1.5, 10.0
        vol = 55.0 * W * T + math.pi * T * (R + T / 2) * W + T * L * W
        self.assertAlmostEqual(hem.Shape.Volume / vol - 1.0, 0.0, delta=1e-6)
        bb = hem.Shape.optimalBoundingBox(True, False)
        self.assertAlmostEqual(bb.XMax, 55.0 + R + T, delta=1e-6)  # bend outer extent
        self.assertAlmostEqual(bb.ZMax, 2 * T + 2 * R, delta=1e-6)

    def test_property_change_recomputes(self):
        c = _case("props", 2.0, "Open", 3.0, 10.0)
        box, hem = self.feature(c)
        hem.hemType = "Closed"
        self.doc.recompute()
        R = 0.2
        vol = PL * W * 2 + math.pi * 2 * (R + 1) * W + 2 * 10 * W
        self.assertAlmostEqual(hem.Shape.Volume / vol - 1.0, 0.0, delta=1e-6)

    def test_refusals(self):
        box = make_box(self.doc, 2.0)
        e = find_edge(box, "+x")
        bad = [dict(length=0.0), dict(length=-3.0), dict(length=1.0),  # shorter than T
               dict(hem_type="Open", gap=0.0), dict(hem_type="Open", gap=-1.0),
               dict(hem_type="Teardrop"), dict(end_gap=W / 2), dict(end_gap=-1.0)]
        for kw in bad:
            args = dict(hem_type="Closed", gap=2.0, length=10.0)
            args.update(kw)
            with self.assertRaises(HF.HemError, msg=str(kw)):
                HF.build_hem(box.Shape, [e], args["hem_type"], args["gap"], args["length"],
                             0.4, False, args.get("end_gap", 0.0))
        with self.assertRaises(HF.HemError):
            HF.build_hem(box.Shape, [], "Closed", 1.0, 10.0)
        cyl = self.doc.addObject("Part::Cylinder", "Cyl")
        cyl.Radius, cyl.Height = 20.0, 2.0
        self.doc.recompute()
        circ = next("Edge%d" % (i + 1) for i, ed in enumerate(cyl.Shape.Edges)
                    if isinstance(ed.Curve, Part.Circle))
        with self.assertRaises(HF.HemError):
            HF.build_hem(cyl.Shape, [circ], "Closed", 1.0, 10.0)
        obj = self.doc.addObject("Part::FeaturePython", "Bad")
        HF.SMHem(obj, box, [e])
        obj.length = 0.0
        with expected_recompute_errors():
            self.doc.recompute()
        self.assertIn("Invalid", obj.State)
        obj.length = 10.0
        self.doc.recompute()
        self.assertNotIn("Invalid", obj.State)
        self.assertTrue(obj.Shape.isValid())


if __name__ == "__main__":
    unittest.main()
