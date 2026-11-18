# SPDX-License-Identifier: LGPL-2.1-or-later

"""Tests of ``Surface.makeGordon`` and the Gordon surface feature (G09 slice 3a).

Public sources: W. J. Gordon, "Spline-blended surface interpolation through curve networks",
J. Math. Mech. 18(10), 1969; the OCCT 8.0 ``GeomFill_Gordon`` header documentation. The truths are
closed form: a hyperbolic paraboloid z = x*y/10 and a cylinder of radius 10.
"""

import math
import os
import tempfile
import unittest

import FreeCAD
import Part
import Surface
import SurfaceGordon

V = FreeCAD.Vector


def _hypar(x, y):
    return x * y / 10.0


def hypar_network():
    """Four lines in x and four in y on z = x*y/10 over 0..10 (probe network A)."""
    profiles = [
        Part.LineSegment(V(0, 10 * i / 3, _hypar(0, 10 * i / 3)), V(10, 10 * i / 3, _hypar(10, 10 * i / 3)))
        for i in range(4)
    ]
    guides = [
        Part.LineSegment(V(10 * i / 3, 0, _hypar(10 * i / 3, 0)), V(10 * i / 3, 10, _hypar(10 * i / 3, 10)))
        for i in range(4)
    ]
    return profiles, guides


def cylinder_network():
    """Four arcs of radius 10 and four lines on a quarter cylinder about Z (probe network D)."""
    profiles = [
        Part.ArcOfCircle(Part.Circle(V(0, 0, 10 * i), V(0, 0, 1), 10), 0, math.pi / 2) for i in range(4)
    ]
    guides = []
    for i in range(4):
        a = math.pi / 2 * i / 3
        guides.append(Part.LineSegment(V(10 * math.cos(a), 10 * math.sin(a), 0), V(10 * math.cos(a), 10 * math.sin(a), 30)))
    return profiles, guides


def sphere_network():
    """Latitude and meridian arcs of a sphere patch of radius 10 (probe network H)."""
    r = 10.0
    profiles = []
    for i in range(4):
        la = math.radians(10 + 50.0 * i / 3)
        profiles.append(Part.ArcOfCircle(Part.Circle(V(0, 0, r * math.sin(la)), V(0, 0, 1), r * math.cos(la)), 0, math.pi / 2))
    guides = []
    for i in range(4):
        lo = math.pi / 2 * i / 3
        circle = Part.Circle(V(0, 0, 0), V(math.sin(lo), -math.cos(lo), 0), r)
        circle.XAxis = V(math.cos(lo), math.sin(lo), 0)
        guides.append(Part.ArcOfCircle(circle, math.radians(10), math.radians(60)))
    return profiles, guides


def near_miss_network(offset=1e-5):
    profiles = [Part.LineSegment(V(0, 5 * i, 0), V(10, 5 * i, 0)) for i in range(3)]
    guides = [Part.LineSegment(V(5 * i, 0, offset if i == 1 else 0), V(5 * i, 10, offset if i == 1 else 0)) for i in range(3)]
    return profiles, guides


def polyline_network():
    """Three C0 two-segment profiles and three lines (probe network K), profiles as wires."""
    profiles = []
    for i in range(3):
        y = 5.0 * i
        profiles.append(Part.Wire([Part.LineSegment(V(0, y, 0), V(5, y, 3)).toShape(), Part.LineSegment(V(5, y, 3), V(10, y, 0)).toShape()]))
    guides = [Part.LineSegment(V(x, 0, 3 if x == 5.0 else 0), V(x, 10, 3 if x == 5.0 else 0)) for x in (0.0, 5.0, 10.0)]
    return profiles, guides


def curve_of(c):
    return c.toShape() if hasattr(c, "toShape") else c


def max_miss(face, curves, n=51):
    worst = 0.0
    for c in curves:
        edge = curve_of(c)
        if edge.ShapeType == "Wire":
            edges = edge.Edges
        else:
            edges = [edge]
        for e in edges:
            f, l = e.ParameterRange
            for k in range(n):
                p = e.valueAt(f + (l - f) * k / (n - 1))
                worst = max(worst, face.distToShape(Part.Vertex(p))[0])
    return worst


def face_of(result):
    return Part.Face(result["surface"])


class TestGordonApi(unittest.TestCase):
    def test_result_keys_and_type(self):
        r = Surface.makeGordon(*hypar_network())
        self.assertEqual(
            sorted(r), ["approximate", "max_contact_gap", "max_guide_deviation", "max_profile_deviation", "surface"]
        )
        self.assertEqual(r["surface"].TypeId if hasattr(r["surface"], "TypeId") else "BSplineSurface", "Part::GeomBSplineSurface")
        self.assertIs(r["approximate"], False)

    def test_hypar_exact(self):
        profiles, guides = hypar_network()
        r = Surface.makeGordon(profiles, guides, tolerance=1e-6)
        face = face_of(r)
        self.assertLess(max_miss(face, profiles + guides), 1e-7)
        worst = 0.0
        for i in range(21):
            for j in range(21):
                x, y = 10.0 * i / 20, 10.0 * j / 20
                worst = max(worst, face.distToShape(Part.Vertex(V(x, y, _hypar(x, y))))[0])
        self.assertLess(worst, 1e-7)

    def test_cylinder_radius(self):
        profiles, guides = cylinder_network()
        r = Surface.makeGordon(profiles, guides, tolerance=1e-6)
        surf = r["surface"]
        u0, u1, v0, v1 = surf.bounds()
        worst = 0.0
        for i in range(1, 20):
            for j in range(1, 20):
                p = surf.value(u0 + (u1 - u0) * i / 20, v0 + (v1 - v0) * j / 20)
                worst = max(worst, abs(math.hypot(p.x, p.y) - 10.0))
        self.assertLess(worst, 1e-6)
        self.assertLess(max_miss(face_of(r), profiles + guides), 1e-7)

    def test_polyline_wires_as_profiles(self):
        profiles, guides = polyline_network()
        r = Surface.makeGordon(profiles, guides, tolerance=1e-6)
        self.assertIs(r["approximate"], False)
        self.assertLess(max_miss(face_of(r), profiles + guides), 1e-7)

    def test_order_and_reversal_do_not_matter(self):
        profiles, guides = hypar_network()
        edges_p = [p.toShape() for p in profiles]
        edges_g = [g.toShape() for g in guides]
        edges_g[1].reverse()
        shuffled_p = [edges_p[i] for i in (2, 0, 3, 1)]
        shuffled_g = [edges_g[i] for i in (3, 1, 0, 2)]
        r = Surface.makeGordon(shuffled_p, shuffled_g, tolerance=1e-6)
        self.assertLess(max_miss(face_of(r), profiles + guides), 1e-7)

    def test_edges_carry_their_placement(self):
        profiles, guides = cylinder_network()
        move = FreeCAD.Placement(V(100, -50, 7), FreeCAD.Rotation(V(1, 1, 0), 33))
        edges = []
        for c in profiles + guides:
            e = c.toShape()
            e.Placement = move.multiply(e.Placement)
            edges.append(e)
        r = Surface.makeGordon(edges[:4], edges[4:], tolerance=1e-6)
        self.assertLess(max_miss(face_of(r), edges), 1e-7)

    def test_edge_range_is_honoured(self):
        """An edge trimmed from a longer curve contributes only its own range."""
        full = [Part.LineSegment(V(-20, 5 * i, 0), V(30, 5 * i, 0)).toShape() for i in range(3)]
        trimmed = []
        for e in full:
            c = e.Curve
            trimmed.append(Part.Edge(c, 20.0, 30.0))
        guides = [Part.LineSegment(V(0 + 5 * i, 0, 0), V(0 + 5 * i, 10, 0)) for i in range(3)]
        r = Surface.makeGordon(trimmed, guides, tolerance=1e-6)
        b = face_of(r).BoundBox
        self.assertAlmostEqual(b.XMin, 0.0, places=6)
        self.assertAlmostEqual(b.XMax, 10.0, places=6)

    def test_deviation_rule(self):
        profiles, guides = sphere_network()
        with self.assertRaises(ValueError) as cm:
            Surface.makeGordon(profiles, guides, tolerance=1e-6)
        self.assertTrue(str(cm.exception).startswith("DeviationExceeded"), str(cm.exception))
        r = Surface.makeGordon(profiles, guides, tolerance=1e-6, approximate=True)
        self.assertIs(r["approximate"], True)
        miss = max_miss(face_of(r), profiles + guides)
        reported = max(r["max_profile_deviation"], r["max_guide_deviation"])
        self.assertGreater(reported, 1e-3)
        self.assertLess(abs(reported - miss), max(0.1 * miss, 1e-6))

    def test_near_miss_follows_tolerance(self):
        profiles, guides = near_miss_network()
        with self.assertRaises(ValueError) as cm:
            Surface.makeGordon(profiles, guides, tolerance=1e-6)
        self.assertTrue(str(cm.exception).startswith("IntersectionFailed"), str(cm.exception))
        r = Surface.makeGordon(profiles, guides, tolerance=1e-4)
        self.assertLess(max_miss(face_of(r), profiles + guides), 1e-4)
        self.assertIs(r["approximate"], False)

    def test_input_errors(self):
        profiles, guides = hypar_network()
        with self.assertRaises(ValueError) as cm:
            Surface.makeGordon(profiles[:1], guides)
        self.assertTrue(str(cm.exception).startswith("InvalidInput"))
        disjoint = [Part.LineSegment(V(0, 0, 50 + i), V(10, 0, 50 + i)) for i in range(4)]
        with self.assertRaises(ValueError) as cm:
            Surface.makeGordon(disjoint, guides)
        self.assertTrue(str(cm.exception).startswith("IntersectionFailed"))
        with self.assertRaises(ValueError):
            Surface.makeGordon([Part.Line()] + profiles[1:], guides)
        with self.assertRaises(TypeError):
            Surface.makeGordon(["curve"] + profiles[1:], guides)
        with self.assertRaises(TypeError):
            Surface.makeGordon(profiles, 3)
        for bad in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError, msg=str(bad)):
                Surface.makeGordon(profiles, guides, tolerance=bad)


class TestGordonUsable(unittest.TestCase):
    def check(self, make, offset=True):
        face = face_of(Surface.makeGordon(*make(), tolerance=1e-6, approximate=True))
        self.assertTrue(face.isValid())
        self.assertGreater(face.Area, 1.0)
        if offset:
            self.assertTrue(face.makeOffsetShape(1, 1e-6).isValid())
        solid = face.extrude(V(0, 0, 1))
        self.assertTrue(solid.isValid())
        bb = solid.BoundBox
        box = Part.makeBox(bb.XLength / 2, bb.YLength * 2, bb.ZLength * 4, V(bb.XMin + bb.XLength / 4, bb.YMin - bb.YLength / 2, bb.ZMin - bb.ZLength))
        cut = box.cut(solid)
        self.assertTrue(cut.isValid())
        self.assertLess(cut.Volume, box.Volume)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "g.step")
            face.exportStep(path)
            back = Part.read(path)
            self.assertLess(abs(back.Area - face.Area) / face.Area, 1e-6)

    def test_hypar_face(self):
        self.check(hypar_network)

    def test_cylinder_face(self):
        self.check(cylinder_network)

    def test_polyline_face(self):
        # a single face with a C0 crease: OCCT's offset fails on it (measured, run 113), the rest works
        self.check(polyline_network, offset=False)


class TestGordonFeature(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument("GordonTest")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def lines(self, name, curves):
        out = []
        for i, c in enumerate(curves):
            o = self.doc.addObject("Part::Feature", "%s%d" % (name, i))
            o.Shape = c.toShape()
            out.append(o)
        return out

    def grid(self, face, n=21):
        s = face.Surface
        u0, u1, v0, v1 = s.bounds()
        return [s.value(u0 + (u1 - u0) * i / (n - 1), v0 + (v1 - v0) * j / (n - 1)) for i in range(n) for j in range(n)]

    def test_feature_equals_api(self):
        profiles, guides = hypar_network()
        p = self.lines("P", profiles)
        g = self.lines("G", guides)
        feat = SurfaceGordon.makeGordonFeature(self.doc, p, g)
        self.doc.recompute()
        self.assertTrue(feat.isValid())
        self.assertEqual(len(feat.Shape.Faces), 1)
        api = face_of(Surface.makeGordon(profiles, guides, tolerance=1e-4))
        for a, b in zip(self.grid(feat.Shape.Faces[0]), self.grid(api)):
            self.assertLess((a - b).Length, 1e-9)
        self.assertIs(feat.IsApproximate, False)
        self.assertLess(feat.MaxDeviation, 1e-6)
        self.assertEqual(feat.Tolerance, 1e-4)

    def test_feature_follows_edit(self):
        profiles, guides = hypar_network()
        p = self.lines("P", profiles)
        g = self.lines("G", guides)
        feat = SurfaceGordon.makeGordonFeature(self.doc, p, g)
        self.doc.recompute()
        before = feat.Shape.Area
        # lift the whole network by 5 mm: the face moves with it
        for o in p + g:
            o.Placement = FreeCAD.Placement(V(0, 0, 5), FreeCAD.Rotation())
        self.doc.recompute()
        self.assertAlmostEqual(feat.Shape.BoundBox.ZMax - 5, 10.0, places=6)
        self.assertAlmostEqual(feat.Shape.Area, before, places=6)

    def test_feature_sub_elements_in_placed_part(self):
        profiles, guides = cylinder_network()
        part = self.doc.addObject("App::Part", "Holder")
        part.Placement = FreeCAD.Placement(V(10, 20, 30), FreeCAD.Rotation(V(0, 0, 1), 90))
        objs = self.lines("E", profiles + guides)
        for o in objs:
            part.addObject(o)
        feat = SurfaceGordon.makeGordonFeature(
            self.doc,
            [(part, "E%d.Edge1" % i) for i in range(4)],
            [(part, "E%d.Edge1" % i) for i in range(4, 8)],
        )
        self.doc.recompute()
        self.assertTrue(feat.isValid(), feat.getStatusString())
        placed = [part.Placement.multiply(o.Placement).toMatrix() for o in objs]
        self.assertTrue(placed)
        edges = []
        for o in objs:
            e = o.Shape.copy()
            e.Placement = part.Placement.multiply(o.Placement)
            edges.append(e)
        api = face_of(Surface.makeGordon(edges[:4], edges[4:], tolerance=1e-4))
        for a, b in zip(self.grid(feat.Shape.Faces[0]), self.grid(api)):
            self.assertLess((a - b).Length, 1e-9)

    def test_save_and_reopen(self):
        profiles, guides = hypar_network()
        feat = SurfaceGordon.makeGordonFeature(self.doc, self.lines("P", profiles), self.lines("G", guides))
        self.doc.recompute()
        area = feat.Shape.Area
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "g.FCStd")
            self.doc.saveAs(path)
            other = FreeCAD.openDocument(path)
            try:
                back = other.getObject(feat.Name)
                self.assertAlmostEqual(back.Shape.Area, area, places=9)
                self.assertEqual(len(back.Profiles), 4)
                self.assertEqual(len(back.Guides), 4)
                other.recompute()
                self.assertTrue(back.isValid())
                self.assertAlmostEqual(back.Shape.Area, area, places=9)
            finally:
                FreeCAD.closeDocument(other.Name)
            self.doc = FreeCAD.newDocument("GordonTest")

    def test_failing_network_keeps_last_shape(self):
        profiles, guides = hypar_network()
        p = self.lines("P", profiles)
        g = self.lines("G", guides)
        feat = SurfaceGordon.makeGordonFeature(self.doc, p, g)
        self.doc.recompute()
        area = feat.Shape.Area
        g[1].Placement = FreeCAD.Placement(V(0, 0, 40), FreeCAD.Rotation())
        self.doc.recompute()
        self.assertFalse(feat.isValid())
        self.assertIn("IntersectionFailed", feat.getStatusString())
        self.assertAlmostEqual(feat.Shape.Area, area, places=9)

    def test_whole_object_entries_must_be_one_curve(self):
        profiles, guides = hypar_network()
        compound = self.doc.addObject("Part::Feature", "Many")
        compound.Shape = Part.makeCompound([c.toShape() for c in profiles])
        feat = SurfaceGordon.makeGordonFeature(self.doc, [compound], self.lines("G", guides))
        self.doc.recompute()
        self.assertFalse(feat.isValid())
        self.assertIn("InvalidInput", feat.getStatusString())

    def test_allow_approximation_property(self):
        profiles, guides = sphere_network()
        feat = SurfaceGordon.makeGordonFeature(self.doc, self.lines("P", profiles), self.lines("G", guides))
        feat.Tolerance = 1e-6
        self.doc.recompute()
        self.assertFalse(feat.isValid())
        self.assertIn("DeviationExceeded", feat.getStatusString())
        feat.AllowApproximation = True
        self.doc.recompute()
        self.assertTrue(feat.isValid())
        self.assertIs(feat.IsApproximate, True)
        self.assertGreater(feat.MaxDeviation, 1e-3)
