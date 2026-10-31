# SPDX-License-Identifier: LGPL-2.1-or-later
"""Oracle tests for the clean-room jog (G07 slice 4a, check 3).

Expected values come from the case parameters with fabrication geometry only (Machinery's
Handbook bend allowance, annular-sector areas):
- displacement d from the offset convention: outside d = off, inside d = off + T, overall d = off - T
- straight slanted length s = (d - (2R + T)(1 - cos th)) / sin th, jog zone width X = (2R + T) sin th
  + s cos th
- volume = plate - X W T + W T (2 th (R + T/2) + s)
- moved wall: its faces sit exactly d from the fixed wall's, its extent is plate length - X/2 - line
- flat length = plate - X + s + 2 BA, BA = th (R + K T)
Exactly 60 degree bends are avoided in the unfold cases: the vendored V1 unfolder flattens some
shapes with 60 degree bends only partly (run 026); the jog says so on recompute instead.
"""

import math
import unittest

import FreeCAD
import Part
from FreeCAD import Vector as V

import SheetMetalJog as JG
import SheetMetalUnfoldCmd as UC
from SMTests.testEdgeFlange import PL, W, make_box
from SMTests.testOracle import unfold_target_face
from SMTests.testUnfoldFeature import feature_unfold

K = 0.42


def _case(name, T, r_t, angle, off_t, kind, xc=20.0, flip=False, side="Left", k=K):
    return dict(name=name, T=T, R=r_t * T, angle=angle, off=off_t * T, kind=kind, xc=xc, flip=flip,
                side=side, k=k)


CASES = [
    _case("j01_30_T1_R.5T_out2T", 1.0, 0.5, 30.0, 2.0, "outside"),
    _case("j02_45_T2_R1T_out4T", 2.0, 1.0, 45.0, 4.0, "outside"),
    _case("j03_90_T3_R2T_out10T", 3.0, 2.0, 90.0, 10.0, "outside", k=0.33),
    _case("j04_75_T2_R1.5T_in3T", 2.0, 1.5, 75.0, 3.0, "inside"),
    _case("j05_90_T1_R1T_all6T", 1.0, 1.0, 90.0, 6.0, "overall"),
    _case("j06_30_T2_R2T_all3T", 2.0, 2.0, 30.0, 3.0, "overall"),
    _case("j07_45_T3_R.5T_in5T", 3.0, 0.5, 45.0, 5.0, "inside", k=0.5),
    _case("j08_90_T1.5_R1T_out4T_flip", 1.5, 1.0, 90.0, 4.0, "outside", flip=True),
    _case("j09_80_T2_R1T_out6T_fixR_offc", 2.0, 1.0, 80.0, 6.0, "outside", xc=12.0, side="Right"),
    _case("j10_50_T1_R2T_in8T", 1.0, 2.0, 50.0, 8.0, "inside", xc=28.0),
]


def derived(c):
    T, R, th = c["T"], c["R"], math.radians(c["angle"])
    d = {"outside": c["off"], "inside": c["off"] + T, "overall": c["off"] - T}[c["kind"]]
    s = (d - (2 * R + T) * (1 - math.cos(th))) / math.sin(th)
    X = (2 * R + T) * math.sin(th) + s * math.cos(th)
    ba = th * (R + c["k"] * T)
    return d, s, X, ba


def top_face(box):
    return next("Face%d" % (i + 1) for i, f in enumerate(box.Shape.Faces)
                if abs(f.normalAt(0, 0).z - 1.0) < 1e-9)


def line_obj(doc, xc, z, name="JogLine", y0=0.0, y1=W):
    obj = doc.addObject("Part::Feature", name)
    obj.Shape = Part.makeLine(V(xc, y0, z), V(xc, y1, z))
    return obj


class Doc(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument("JogTest")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def feature(self, c):
        box = make_box(self.doc, c["T"])
        ln = line_obj(self.doc, c["xc"], c["T"])
        jog = JG.make_jog(self.doc, box, top_face(box), ln, offset=c["off"], offsetType=c["kind"],
                          angle=c["angle"], radius=c["R"], kfactor=c["k"], flip=c["flip"],
                          fixedSide=c["side"])
        return box, jog


class TestJog(Doc):
    def test_oracle_cases(self):
        self.assertGreaterEqual(len(CASES), 8)
        for c in CASES:
            with self.subTest(case=c["name"]):
                box, jog = self.feature(c)
                self.assertNotIn("Invalid", jog.State)
                sh = jog.Shape
                self.assertTrue(sh.isValid())
                self.assertEqual(len(sh.Solids), 1)
                T, R, th = c["T"], c["R"], math.radians(c["angle"])
                d, s, X, ba = derived(c)
                self.assertGreaterEqual(s, 0.0)
                vol = W * T * (PL - X + 2 * th * (R + T / 2.0) + s)
                self.assertAlmostEqual(sh.Volume / vol - 1.0, 0.0, delta=1e-6, msg="volume")
                self.assertAlmostEqual(jog.displacement.Value, d, 9)
                sgn = -1.0 if c["flip"] else 1.0
                right = c["side"] == "Right"
                # moved wall: faces with the face normal along z, at the shifted heights
                moved_len = (c["xc"] - X / 2.0) if right else (PL - c["xc"] - X / 2.0)
                fixed_len = (PL - c["xc"] - X / 2.0) if right else (c["xc"] - X / 2.0)
                for fixed, ln, zs in ((False, moved_len, (d * sgn, d * sgn + T)),
                                      (True, fixed_len, (0.0, T))):
                    for z, nz in ((zs[1], 1.0), (zs[0], -1.0)):
                        faces = [f for f in sh.Faces if f.Surface.TypeId == "Part::GeomPlane"
                                 and abs(f.normalAt(0, 0).z - nz) < 1e-9
                                 and abs(f.CenterOfMass.z - z) < 1e-6]
                        self.assertEqual(len(faces), 1, "%s wall face z=%g n=%g" % (fixed, z, nz))
                        self.assertAlmostEqual(faces[0].Area, ln * W, delta=1e-6 * ln * W)
                        self.assertAlmostEqual(faces[0].CenterOfMass.z, z, delta=1e-6)
                bb = sh.optimalBoundingBox(True, False)
                self.assertAlmostEqual(bb.ZMax if not c["flip"] else bb.ZMin,
                                       (T + d) if not c["flip"] else -d, delta=1e-6)
                cyl = sorted(round(f.Surface.Radius, 9) for f in sh.Faces
                             if f.Surface.TypeId == "Part::GeomCylinder")
                self.assertEqual(cyl, sorted([round(R, 9), round(R + T, 9)] * 2))

    def test_flat_length_and_bend_lines(self):
        for c in CASES:
            with self.subTest(case=c["name"]):
                box, jog = self.feature(c)
                T = c["T"]
                d, s, X, ba = derived(c)
                feat = feature_unfold(self.doc, jog, unfold_target_face(jog.Shape), k=c["k"], old=None)
                self.assertNotIn("Invalid", feat.State)
                flat = feat.Shape
                length = PL - X + s + 2 * ba
                self.assertAlmostEqual(flat.Volume / (T * W), length, delta=1e-3, msg="flat length")
                planes = [f for f in flat.Faces if f.Surface.TypeId == "Part::GeomPlane"]
                big = [f for f in planes if f.Area > 0.5 * max(g.Area for g in planes)]
                self.assertEqual(len(big), 2, "one flat face (top and bottom)")
                for f in big:
                    self.assertAlmostEqual(f.Area, length * W, delta=1e-3 * W)
                bends = UC.get_bend_lines(feat)
                self.assertEqual(sorted(b.direction for b in bends), ["DOWN", "UP"])
                for b in bends:
                    self.assertAlmostEqual(b.angle, c["angle"], delta=1e-6)
                    self.assertAlmostEqual(b.inside_radius, c["R"], delta=1e-9)
                    self.assertAlmostEqual(b.allowance, ba, delta=1e-6)

    def test_recompute_after_base_and_property_change(self):
        c = CASES[1]
        box, jog = self.feature(c)
        box.Length = 55.0
        self.doc.recompute()
        self.assertNotIn("Invalid", jog.State)
        T, R, th = c["T"], c["R"], math.radians(c["angle"])
        d, s, X, ba = derived(c)
        vol = W * T * (55.0 - X + 2 * th * (R + T / 2.0) + s)
        self.assertAlmostEqual(jog.Shape.Volume / vol - 1.0, 0.0, delta=1e-6)
        jog.offset = 6 * T
        self.doc.recompute()
        c2 = dict(c, off=6 * T)
        d, s, X, ba = derived(c2)
        vol = W * T * (55.0 - X + 2 * th * (R + T / 2.0) + s)
        self.assertAlmostEqual(jog.Shape.Volume / vol - 1.0, 0.0, delta=1e-6)

    def test_default_radius_is_one_thickness(self):
        box = make_box(self.doc, 2.0)
        jog = JG.make_jog(self.doc, box, top_face(box), line_obj(self.doc, 20.0, 2.0), offset=8.0)
        self.assertAlmostEqual(jog.radiusUsed.Value, 2.0, 9)
        self.assertAlmostEqual(jog.angle.Value, 90.0, 9)

    def test_sketch_object_line(self):
        try:
            import Sketcher
        except ImportError:
            self.skipTest("Sketcher is not available")
        box = make_box(self.doc, 2.0)
        sk = self.doc.addObject("Sketcher::SketchObject", "JogSketch")
        sk.Placement = FreeCAD.Placement(V(0, 0, 2.0), FreeCAD.Rotation())
        sk.addGeometry(Part.LineSegment(V(20, 0, 0), V(20, W, 0)))
        self.doc.recompute()
        jog = JG.make_jog(self.doc, box, top_face(box), sk, offset=8.0, angle=45.0, radius=2.0)
        self.assertNotIn("Invalid", jog.State)
        self.assertTrue(jog.Shape.isValid())

    def test_refusals(self):
        box = make_box(self.doc, 2.0)
        face = top_face(box)
        a, b = V(20, 0, 2), V(20, W, 2)
        base = dict(offset=10.0, offset_type="outside", angle=90.0, radius=2.0)

        def build(p0=a, p1=b, **kw):
            args = dict(base)
            args.update(kw)
            return JG.build_jog(box.Shape, face, p0, p1, **args)

        cases = [
            ("too small", dict(offset=4.0), "too small"),
            ("small at 90 outside 2T", dict(offset=4.0, radius=2.0), "too small"),
            ("overall below T", dict(offset=1.0, offset_type="overall"), "positive"),
            ("nan offset", dict(offset=float("nan")), "finite"),
            ("inf offset", dict(offset=float("inf")), "finite"),
            ("nan angle", dict(angle=float("nan")), "finite"),
            ("angle 0", dict(angle=0.0), "angle"),
            ("angle 120", dict(angle=120.0), "angle"),
            ("negative radius", dict(radius=-1.0), "radius"),
            ("bad type", dict(offset_type="middle"), "offset type"),
            ("bad side", dict(fixed_side="Up"), "fixed side"),
            ("zero offset", dict(offset=0.0), "positive"),
            ("line off the face", dict(p0=V(20, 0, 3), p1=V(20, W, 3)), "does not lie"),
            ("line far outside", dict(p0=V(80, 0, 2), p1=V(80, W, 2)), "does not lie"),
            ("zero length", dict(p0=a, p1=a), "zero length"),
            ("nan point", dict(p0=V(float("nan"), 0, 2)), "finite"),
            ("line tilted out of the face", dict(p0=V(20, 0, 2), p1=V(20, W, 4)), "does not lie"),
            ("zone leaves no flat", dict(p0=V(3, 0, 2), p1=V(3, W, 2)), "jog zone"),
            ("zone beyond the end", dict(p0=V(39, 0, 2), p1=V(39, W, 2)), "jog zone"),
        ]
        for name, kw, text in cases:
            with self.subTest(case=name):
                with self.assertRaises(JG.JogError) as cm:
                    build(**kw)
                self.assertIn(text, str(cm.exception))
        # a feature with a refusal is Invalid, then recovers
        ln = line_obj(self.doc, 20.0, 2.0)
        jog = JG.make_jog(self.doc, box, face, ln, offset=4.0, angle=90.0, radius=2.0)
        self.assertIn("Invalid", jog.State)
        jog.offset = 10.0
        self.doc.recompute()
        self.assertNotIn("Invalid", jog.State)
        self.assertTrue(jog.Shape.isValid())

    def test_not_a_sheet_and_not_a_line(self):
        cyl = self.doc.addObject("Part::Cylinder", "Cyl")
        self.doc.recompute()
        with self.assertRaises(JG.JogError):
            JG.build_jog(cyl.Shape, "Face1", V(0, 0, 0), V(1, 0, 0), 5.0)
        arc = self.doc.addObject("Part::Feature", "Arc")
        arc.Shape = Part.ArcOfCircle(Part.Circle(V(0, 0, 2), V(0, 0, 1), 5.0), 0.0, 1.5).toShape()
        with self.assertRaises(JG.JogError):
            JG.line_from_sketch(arc)
        two = self.doc.addObject("Part::Feature", "Two")
        two.Shape = Part.makePolygon([V(0, 0, 0), V(1, 0, 0), V(1, 1, 0)])
        with self.assertRaises(JG.JogError):
            JG.line_from_sketch(two)


if __name__ == "__main__":
    unittest.main()
