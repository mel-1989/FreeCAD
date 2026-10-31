# SPDX-License-Identifier: LGPL-2.1-or-later
"""Oracle tests for the clean-room edge flange (G07 slice 2).

Expected values are computed from the case parameters with fabrication trigonometry only:
- bend volume  = theta * T * (R + T/2) * W   (annular sector area times width)
- wall volume  = s * T * W, s = L - setback, setback = tan(theta/2) * (R + T) for a length to the
                 outer mould line, tan(theta/2) * R for the inner one
- wall end     = the end plane lies L along the wall direction from the mould-line intersection
- flat length  = plate length + BA + s, BA = theta * (R + K*T)  (Machinery's Handbook)
"""

import math
import unittest

import FreeCAD
import Part
from FreeCAD import Vector as V

import SheetMetalEdgeFlange as EF
from SMTests.testOracle import old_unfold

PL, W = 40.0, 30.0


def _case(name, T, r_t, angle, L, measure="outer", flip=False, gap=0.0, edges=("+x",), k=0.42):
    return dict(name=name, T=T, R=r_t * T, angle=angle, L=L, measure=measure, flip=flip, gap=gap,
                edges=edges, k=k)


CASES = [
    _case("c01_30_T1_R.5T", 1.0, 0.5, 30.0, 15.0),
    _case("c02_45_T2_R1T", 2.0, 1.0, 45.0, 20.0),
    _case("c03_60_T3_R3T", 3.0, 3.0, 60.0, 25.0),
    _case("c04_90_T1_R3T_inner", 1.0, 3.0, 90.0, 12.0, measure="inner"),
    _case("c05_90_T2_R2T", 2.0, 2.0, 90.0, 15.0, k=0.5),
    _case("c06_120_T3_R.5T", 3.0, 0.5, 120.0, 20.0, k=0.33),
    _case("c07_135_T2_R2T", 2.0, 2.0, 135.0, 25.0),
    _case("c08_150_T1_R1T", 1.0, 1.0, 150.0, 30.0),
    _case("c09_90_T3_R3T_inner", 3.0, 3.0, 90.0, 30.0, measure="inner"),
    _case("c10_90_T2_R1T_flip", 2.0, 1.0, 90.0, 18.0, flip=True),
    _case("c11_75_T2_R1.5T_gap2", 2.0, 1.5, 75.0, 22.0, gap=2.0),
    _case("c12_90_T1.5_R1T_opposite", 1.5, 1.0, 90.0, 14.0, edges=("+x", "-x")),
    _case("c13_70_T2_R1T_adjacent", 2.0, 1.0, 70.0, 16.0, edges=("+x", "+y")),
    _case("c14_40_T3_R2T_inner_flip", 3.0, 2.0, 40.0, 20.0, measure="inner", flip=True),
]


def make_box(doc, T, name="Plate"):
    box = doc.addObject("Part::Box", name)
    box.Length, box.Width, box.Height = PL, W, T
    doc.recompute()
    return box


def find_edge(box, which, top=True):
    """Name of the straight edge on the `which` end face, on the top (z = T) or bottom face."""
    sh = box.Shape
    z = sh.BoundBox.ZMax if top else 0.0
    for i, e in enumerate(sh.Edges):
        a, b = e.Vertexes[0].Point, e.Vertexes[-1].Point
        if abs(a.z - z) > 1e-9 or abs(b.z - z) > 1e-9:
            continue
        if which == "+x" and abs(a.x - PL) < 1e-9 and abs(b.x - PL) < 1e-9:
            return "Edge%d" % (i + 1)
        if which == "-x" and abs(a.x) < 1e-9 and abs(b.x) < 1e-9:
            return "Edge%d" % (i + 1)
        if which == "+y" and abs(a.y - W) < 1e-9 and abs(b.y - W) < 1e-9:
            return "Edge%d" % (i + 1)
    raise AssertionError("edge %s not found" % which)


# frame of each edge on a plate: (out, along, edge length, point on the edge at its start, top z)
def frames(T):
    return {
        "+x": (V(1, 0, 0), V(0, 1, 0), W, V(PL, 0, T)),
        "-x": (V(-1, 0, 0), V(0, 1, 0), W, V(0, 0, T)),
        "+y": (V(0, 1, 0), V(1, 0, 0), PL, V(0, W, T)),
    }


def expected(c, edge):
    """(s, flange width, origin, out, up, d, far-plane offset, outer corner point) from trigonometry."""
    T, R, L, th = c["T"], c["R"], c["L"], math.radians(c["angle"])
    t2 = math.tan(th / 2.0)
    out, along, ln, p0 = frames(T)[edge]
    up = V(0, 0, -1) if c["flip"] else V(0, 0, 1)
    origin = p0 + along * c["gap"] - (V(0, 0, T) if c["flip"] else V(0, 0, 0))
    d = out * math.cos(th) + up * math.sin(th)
    # mould-line intersections ("virtual sharps"): inner at (R*t2, 0), outer at ((R+T)*t2, -T)
    sharp_in = origin + out * (R * t2)
    sharp_out = origin + out * ((R + T) * t2) - up * T
    if c["measure"] == "outer":
        s = L - (R + T) * t2
        end_out = sharp_out + d * L
    else:
        s = L - R * t2
        end_out = sharp_out + d * (s + (R + T) * t2)  # the same plane: s past the outer tangent point
        end_in = sharp_in + d * L
        assert abs(d.dot(end_out) - d.dot(end_in)) < 1e-9
    return s, ln - 2.0 * c["gap"], origin, out, up, d, d.dot(end_out), end_out, along


def flat_expected(c, plate_len, s):
    ba = math.radians(c["angle"]) * (c["R"] + c["k"] * c["T"])
    return plate_len + ba + s, ba


class Doc(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument("EdgeFlangeTest")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def feature(self, c, T=None):
        box = make_box(self.doc, c["T"] if T is None else T)
        names = [find_edge(box, e) for e in c["edges"]]
        ff = EF.make_edge_flange(self.doc, box, names, length=c["L"], angle=c["angle"], radius=c["R"],
                                 kfactor=c["k"], gap=c["gap"], flip=c["flip"], measure=c["measure"])
        return box, ff

    def unfold(self, ff, k):
        flat, err = old_unfold(ff, {1: k})
        self.assertEqual(err, 0)
        return flat


class TestEdgeFlange(Doc):
    def test_module_imports_without_pyside(self):
        import sys
        self.assertFalse([m for m in sys.modules if m.startswith("PySide")])

    def test_oracle_cases(self):
        self.assertGreaterEqual(len(CASES), 12)
        for c in CASES:
            with self.subTest(case=c["name"]):
                box, ff = self.feature(c)
                sh = ff.Shape
                self.assertTrue(sh.isValid(), c["name"])
                self.assertEqual(len(sh.Solids), 1)
                T, R, th = c["T"], c["R"], math.radians(c["angle"])
                vol = PL * W * T
                for e in c["edges"]:
                    s, width, origin, out, up, d, plane, end_out, along = expected(c, e)
                    vol += th * T * (R + T / 2.0) * width + s * T * width
                self.assertAlmostEqual(sh.Volume / vol - 1.0, 0.0, delta=1e-6, msg="volume")
                self.assertAlmostEqual(ff.thickness.Value, T, 9)
                # bend faces: cylinders R and R+T per flange
                cyl = sorted(round(f.Surface.Radius, 9) for f in sh.Faces
                             if f.Surface.TypeId == "Part::GeomCylinder")
                self.assertEqual(cyl, sorted([round(R, 9), round(R + T, 9)] * len(c["edges"])))
                for e in c["edges"]:
                    s, width, origin, out, up, d, plane, end_out, along = expected(c, e)
                    # far face: planar, normal d, centroid on the expected plane
                    ends = [f for f in sh.Faces if f.Surface.TypeId == "Part::GeomPlane"
                            and abs(abs(f.normalAt(0, 0).dot(d)) - 1.0) < 1e-9
                            and abs(f.CenterOfMass.dot(d) - plane) < 1e-6]
                    self.assertGreaterEqual(len(ends), 1, "far face plane off by more than 1e-6 mm")
                    # and an outer corner of a far face is where the mould lines put it
                    corners = [v.Point for f in ends for v in f.Vertexes]
                    near = min((p - end_out).Length for p in corners)
                    self.assertLess(near, 1e-6, "far face outer corner")

    def test_flat_length_through_unfolder(self):
        for c in CASES:
            with self.subTest(case=c["name"]):
                box, ff = self.feature(c)
                flat = self.unfold(ff, c["k"])
                T = c["T"]
                if c["edges"] == ("+x",):
                    s = expected(c, "+x")[0]
                    exp, _ = flat_expected(c, PL, s)
                    width = W - 2 * c["gap"]
                    # the plate is W wide and the flange W - 2 gap: use the plate strip's flat area
                    top = max((f.Area for f in flat.Faces if f.Surface.TypeId == "Part::GeomPlane"))
                    ba = math.radians(c["angle"]) * (c["R"] + c["k"] * T)
                    area = PL * W + width * (ba + s)
                    self.assertAlmostEqual(top, area, delta=1e-3 * 1.0 + 1e-6 * area, msg="flat area")
                    if c["gap"] == 0.0:
                        length = flat.Volume / (T * W)
                        self.assertAlmostEqual(length, exp, delta=1e-3, msg="flat length")
                elif c["edges"] == ("+x", "-x"):
                    s = expected(c, "+x")[0]
                    ba = math.radians(c["angle"]) * (c["R"] + c["k"] * T)
                    self.assertAlmostEqual(flat.Volume / (T * W), PL + 2 * (ba + s), delta=1e-3)
                else:
                    s1 = expected(c, "+x")[0]
                    s2 = expected(c, "+y")[0]
                    ba = math.radians(c["angle"]) * (c["R"] + c["k"] * T)
                    top = max((f.Area for f in flat.Faces if f.Surface.TypeId == "Part::GeomPlane"))
                    area = PL * W + W * (ba + s1) + PL * (ba + s2)
                    self.assertAlmostEqual(top, area, delta=1e-3)

    def test_second_flange_on_the_wall_unfolds(self):
        c = _case("chain", 2.0, 1.0, 90.0, 15.0)
        box, ff = self.feature(c)
        s1 = expected(c, "+x")[0]
        # the wall's end face is the planar face with normal +z at the greatest height
        sh = ff.Shape
        top_z = sh.BoundBox.ZMax
        name = None
        for i, e in enumerate(sh.Edges):
            a, b = e.Vertexes[0].Point, e.Vertexes[-1].Point
            if abs(a.z - top_z) < 1e-9 and abs(b.z - top_z) < 1e-9 and abs(a.y - b.y) > 1.0 \
                    and abs(a.x - (PL + c["R"] + c["T"])) < 1e-6 and abs(b.x - a.x) < 1e-9:
                name = "Edge%d" % (i + 1)
        self.assertIsNotNone(name)
        ff2 = EF.make_edge_flange(self.doc, ff, [name], name="Second", length=10.0, angle=90.0,
                                  radius=1.0, kfactor=0.42, measure="outer")
        self.assertTrue(ff2.Shape.isValid())
        self.assertEqual(len(ff2.Shape.Solids), 1)
        flat = self.unfold(ff2, 0.42)
        s2 = 10.0 - (1.0 + 2.0) * 1.0
        ba1 = math.radians(90.0) * (c["R"] + 0.42 * 2.0)
        ba2 = math.radians(90.0) * (1.0 + 0.42 * 2.0)
        self.assertAlmostEqual(flat.Volume / (2.0 * W), PL + ba1 + ba2 + s1 + s2, delta=1e-3)

    def test_recompute_after_base_change(self):
        c = _case("recompute", 2.0, 1.0, 90.0, 15.0)
        box, ff = self.feature(c)
        v0 = ff.Shape.Volume
        box.Length = 55.0
        self.doc.recompute()
        self.assertFalse(ff.State and "Invalid" in ff.State)
        s = expected(c, "+x")[0]
        R = c["R"]
        wall_bend = math.radians(90.0) * 2.0 * (R + 1.0) * W + s * 2.0 * W
        self.assertAlmostEqual(ff.Shape.Volume / (55.0 * W * 2.0 + wall_bend) - 1.0, 0.0, delta=1e-6)
        self.assertGreater(ff.Shape.Volume, v0)
        self.assertEqual(len(ff.Shape.Solids), 1)
        # the wall followed the moved end: outer face at x = 55 + R + T, end plane at z = L (outer mould)
        bb = ff.Shape.optimalBoundingBox(True, False)
        self.assertAlmostEqual(bb.XMax, 55.0 + R + 2.0, delta=1e-6)
        self.assertAlmostEqual(bb.ZMax, 15.0, delta=1e-6)

    def test_vertical_end_edge_is_not_a_sheet_edge(self):
        """Run 025 LOW: the edge between an end face and a side face of a plate was refused with the
        setback message; it must say the edge is not an edge of a sheet face."""
        box = make_box(self.doc, 3.0)
        sh = box.Shape
        vertical = [("Edge%d" % (i + 1)) for i, e in enumerate(sh.Edges)
                    if abs(e.Vertexes[0].Point.z - e.Vertexes[-1].Point.z) > 1e-9]
        self.assertEqual(len(vertical), 4)
        for name in vertical:
            with self.assertRaisesRegex(EF.EdgeFlangeError, "not an edge of a sheet face"):
                EF.build_edge_flange(sh, [name], 15.0, 90.0, 2.0)
        # the same message through the hem frame
        import SheetMetalHemFeature as HF
        with self.assertRaisesRegex(HF.HemError, "not an edge of a sheet face"):
            HF.build_hem(sh, [vertical[0]], "Closed", length=10.0)
        # a real end edge still works, and the thickness helper reads T from the plate
        self.assertAlmostEqual(EF.sheet_thickness_of(sh), 3.0, 9)
        self.assertTrue(EF.build_edge_flange(sh, [find_edge(box, "+x")], 15.0, 90.0, 2.0).isValid())

    def test_refusals(self):
        box = make_box(self.doc, 2.0)
        flat_edge = find_edge(box, "+x")
        for ang in (0.0, 180.0, -5.0, 200.0):
            with self.assertRaises(EF.EdgeFlangeError, msg="angle %g" % ang):
                EF.build_edge_flange(box.Shape, [flat_edge], 15.0, ang, 2.0)
        with self.assertRaises(EF.EdgeFlangeError):  # zero radius (sharp bends unsupported)
            EF.build_edge_flange(box.Shape, [flat_edge], 15.0, 90.0, 0.0)
        with self.assertRaises(EF.EdgeFlangeError):  # too short for the setback
            EF.build_edge_flange(box.Shape, [flat_edge], 2.0, 90.0, 2.0)
        with self.assertRaises(EF.EdgeFlangeError):  # gap eats the width
            EF.build_edge_flange(box.Shape, [flat_edge], 15.0, 90.0, 2.0, gap=W / 2)
        # curved edge
        cyl = self.doc.addObject("Part::Cylinder", "Cyl")
        cyl.Radius, cyl.Height = 20.0, 2.0
        self.doc.recompute()
        circ = next("Edge%d" % (i + 1) for i, e in enumerate(cyl.Shape.Edges)
                    if isinstance(e.Curve, Part.Circle))
        with self.assertRaises(EF.EdgeFlangeError):
            EF.build_edge_flange(cyl.Shape, [circ], 15.0, 90.0, 2.0)
        # through the feature: a recompute error, no crash, and the document survives
        obj = self.doc.addObject("Part::FeaturePython", "Bad")
        EF.SMEdgeFlange(obj, box, [flat_edge])
        obj.angle = 180.0
        self.doc.recompute()
        self.assertIn("Invalid", obj.State)
        obj.angle = 90.0
        self.doc.recompute()
        self.assertNotIn("Invalid", obj.State)
        self.assertTrue(obj.Shape.isValid())


if __name__ == "__main__":
    unittest.main()
