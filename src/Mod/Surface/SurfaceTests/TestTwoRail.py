# SPDX-License-Identifier: LGPL-2.1-or-later

"""Tests of ``SurfaceTwoRail`` (the two-rail sweep, G09 slice 3b) and of the deleted-input report of the
Gordon and two-rail features (R114a).

Public sources: L. Piegl and W. Tiller, *The NURBS Book*, 2nd ed., 1997 (swept surfaces); W. J. Gordon,
"Spline-blended surface interpolation through curve networks", J. Math. Mech. 18(10), 1969. The truths
are closed form: a cylinder, cones, a torus and a helicoid.
"""

import math
import os
import tempfile
import unittest

import FreeCAD
import Part
import Surface
import SurfaceGordon
import SurfaceTwoRail
from SurfaceTests.TestGordon import cylinder_network

V = FreeCAD.Vector
Z = V(0, 0, 1)


def line(a, b):
    return Part.LineSegment(a, b)


def half_circle():
    """Half circle of radius 5 in the xz plane from (0,0,0) over (5,0,5) to (10,0,0)."""
    return Part.Arc(V(0, 0, 0), V(5, 0, 5), V(10, 0, 0))


def cylinder_rails():
    return line(V(0, 0, 0), V(0, 50, 0)), line(V(10, 0, 0), V(10, 50, 0))


def cone_rails():
    return line(V(0, 0, 0), V(-5, 50, 0)), line(V(10, 0, 0), V(15, 50, 0))


def torus_parts():
    outer = Part.ArcOfCircle(Part.Circle(V(0, 0, 0), Z, 20), 0, math.pi / 2)
    inner = Part.ArcOfCircle(Part.Circle(V(0, 0, 0), Z, 10), 0, math.pi / 2)
    return Part.Arc(V(20, 0, 0), V(15, 0, 5), V(10, 0, 0)), outer, inner


def cylinder_truth(p):
    return abs(math.hypot(p.x - 5, p.z) - 5)


def cone_truth(p):
    return abs(math.hypot(p.x - 5, p.z) - (5 + 0.1 * p.y))


def elliptic_cone_truth(p):
    return abs(math.hypot((p.x - 5) / (5 + 0.1 * p.y), p.z / 5.0)) - 1.0


def torus_truth(p):
    return abs(math.hypot(math.hypot(p.x, p.y) - 15, p.z) - 5)


def helicoid_truth(p):
    zz = 10 * math.atan2(p.y, p.x) / (2 * math.pi)
    k = round((p.z - zz) / 10)
    return abs(p.z - zz - 10 * k) * math.cos(math.atan(10 / (2 * math.pi * math.hypot(p.x, p.y))))


def grid_max(surface, truth, n=21):
    u0, u1, v0, v1 = surface.bounds()
    return max(
        abs(truth(surface.value(u0 + (u1 - u0) * i / (n - 1), v0 + (v1 - v0) * j / (n - 1)))) for i in range(n) for j in range(n)
    )


def rail_miss(face, curves, n=51):
    worst = 0.0
    for c in curves:
        e = c.toShape() if hasattr(c, "toShape") else c
        f, l = e.FirstParameter, e.LastParameter
        for k in range(n):
            worst = max(worst, face.distToShape(Part.Vertex(e.valueAt(f + (l - f) * k / (n - 1))))[0])
    return worst


def sweep(profile, r1, r2, **kw):
    return SurfaceTwoRail.makeTwoRailSweep(profile, r1, r2, **kw)


def face_of(result):
    return Part.Face(result["surface"])


class TestTwoRailApi(unittest.TestCase):
    def test_result_keys(self):
        r1, r2 = cylinder_rails()
        r = sweep(half_circle(), r1, r2)
        self.assertEqual(set(r), {"surface", "approximate", "sections", "max_rail_deviation", "max_section_deviation", "rails_reversed"})
        self.assertIsInstance(r["surface"], Part.BSplineSurface)
        self.assertIs(r["approximate"], False)
        self.assertEqual(r["rails_reversed"], (False, False))

    def test_cylinder_closed_form(self):
        r1, r2 = cylinder_rails()
        r = sweep(half_circle(), r1, r2)
        self.assertEqual(r["sections"], 5)
        self.assertLess(grid_max(r["surface"], cylinder_truth), 1e-7)
        self.assertLess(rail_miss(face_of(r), [r1, r2]), 1e-7)

    def test_cone_uniform_and_reversed_rail(self):
        c1, c2 = cone_rails()
        r = sweep(half_circle(), c1, c2)
        self.assertLess(grid_max(r["surface"], cone_truth), 1e-7)
        back = sweep(half_circle(), c1, line(V(15, 50, 0), V(10, 0, 0)))
        self.assertEqual(back["rails_reversed"], (False, True))
        self.assertLess(grid_max(back["surface"], cone_truth), 1e-7)

    def test_profile_reversed_and_rails_swapped(self):
        r1, r2 = cylinder_rails()
        flipped = Part.Arc(V(10, 0, 0), V(5, 0, 5), V(0, 0, 0))
        self.assertLess(grid_max(sweep(flipped, r1, r2)["surface"], cylinder_truth), 1e-7)
        self.assertLess(grid_max(sweep(half_circle(), r2, r1)["surface"], cylinder_truth), 1e-7)

    def test_chord_scaling_keeps_section_height(self):
        c1, c2 = cone_rails()
        r = sweep(half_circle(), c1, c2, scaling="Chord")
        self.assertLess(grid_max(r["surface"], elliptic_cone_truth), 1e-7)
        top = max(r["surface"].value(u, v).z for u in (0.0, 0.5, 1.0) for v in (0.0, 0.5, 1.0))
        self.assertLess(abs(top - 5.0), 1e-6)

    def test_torus_adapts_the_station_count(self):
        profile, outer, inner = torus_parts()
        r = sweep(profile, outer, inner)
        self.assertEqual(r["sections"], 9)
        self.assertLessEqual(r["max_section_deviation"], 1e-4)
        self.assertLess(grid_max(r["surface"], torus_truth), 1e-4)
        self.assertLess(rail_miss(face_of(r), [outer, inner]), 1e-7)

    def test_deviation_rule(self):
        profile, outer, inner = torus_parts()
        with self.assertRaises(ValueError) as cm:
            sweep(profile, outer, inner, max_sections=5)
        self.assertTrue(str(cm.exception).startswith("DeviationExceeded"))
        r = sweep(profile, outer, inner, max_sections=5, approximate=True)
        self.assertIs(r["approximate"], True)
        self.assertEqual(r["sections"], 5)
        self.assertGreater(r["max_section_deviation"], 1e-4)
        self.assertLess(grid_max(r["surface"], torus_truth), 5e-3)
        ok = sweep(profile, outer, inner, max_sections=9)
        self.assertIs(ok["approximate"], False)

    def test_helicoid_needs_the_nonrational_fallback(self):
        h1 = Part.makeHelix(10, 20, 5).Edges[0]
        h2 = Part.makeHelix(10, 20, 15).Edges[0]
        r = sweep(line(V(5, 0, 0), V(15, 0, 0)), h1, h2)
        self.assertIs(r["approximate"], False)
        self.assertLess(r["max_rail_deviation"], 1e-4)
        self.assertLess(rail_miss(face_of(r), [h1, h2]), 1e-4)
        self.assertLess(abs(rail_miss(face_of(r), [h1, h2], 51) - r["max_rail_deviation"]), max(1e-7, 0.1 * r["max_rail_deviation"]))
        self.assertLess(grid_max(r["surface"], helicoid_truth), 1e-4)

    def test_arc_length_correspondence_ignores_parametrisation(self):
        spline = Part.BSplineCurve()
        spline.interpolate([V(0, y * y / 50.0, 0) for y in range(0, 51, 5)])
        _, r2 = cylinder_rails()
        r = sweep(half_circle(), spline, r2)
        self.assertLess(grid_max(r["surface"], cylinder_truth), 1e-7)

    def test_wire_profile_with_a_corner(self):
        r1, r2 = cylinder_rails()
        prof = Part.Wire(
            [line(V(0, 0, 0), V(0, 0, 5)).toShape(), line(V(0, 0, 5), V(10, 0, 5)).toShape(), line(V(10, 0, 5), V(10, 0, 0)).toShape()]
        )
        r = sweep(prof, r1, r2)
        self.assertLess(rail_miss(face_of(r), [r1, r2]), 1e-7)
        self.assertLess(abs(face_of(r).BoundBox.ZMax - 5.0), 1e-9)

    def test_placement_rule_between_stations(self):
        """Sections placed by an independent implementation of the frozen rule lie on the surface."""
        r1 = Part.BSplineCurve()
        r1.interpolate([V(0.4 * math.sin(i), 4.0 * i, 0.5 * math.cos(0.7 * i)) for i in range(9)])
        r2 = Part.BSplineCurve()
        r2.interpolate([V(12 + 0.5 * math.sin(0.8 * i), 4.0 * i, 0.3 * math.sin(i)) for i in range(9)])
        a, b = r1.value(r1.FirstParameter), r2.value(r2.FirstParameter)
        mid = (a + b) * 0.5 + V(0, 0, 6)
        profile = Part.Arc(a, mid, b)

        def walk(curve):
            f, l = curve.FirstParameter, curve.LastParameter
            ts = [f + (l - f) * k / 20000.0 for k in range(20001)]
            ps = [curve.value(t) for t in ts]
            acc = [0.0]
            for p, q in zip(ps, ps[1:]):
                acc.append(acc[-1] + (q - p).Length)

            def at(s):
                target = s * acc[-1]
                lo, hi = 0, len(acc) - 1
                while hi - lo > 1:
                    m = (lo + hi) // 2
                    lo, hi = (m, hi) if acc[m] <= target else (lo, m)
                t = ts[lo] + (ts[hi] - ts[lo]) * (target - acc[lo]) / (acc[hi] - acc[lo])
                tan = curve.tangent(t)[0]
                tan.normalize()
                return curve.value(t), tan

            return at

        w1, w2 = walk(r1), walk(r2)

        def frame(s):
            p1, t1 = w1(s)
            p2, t2 = w2(s)
            x = p2 - p1
            length = x.Length
            x.normalize()
            t = t1 + t2
            z = t - x * t.dot(x)
            z.normalize()
            return p1, x, z.cross(x), z, length

        for scaling in ("Uniform", "Chord"):
            res = sweep(profile, r1, r2, scaling=scaling, tolerance=1e-3)
            face = face_of(res)
            self.assertLessEqual(res["sections"], 65)
            o0, x0, y0, z0, l0 = frame(0.0)
            worst = 0.0
            for j in range(8):
                s = (j + 0.37) / 8.0
                o, x, y, z, length = frame(s)
                k = length / l0
                for q in range(21):
                    p = profile.value(profile.FirstParameter + (profile.LastParameter - profile.FirstParameter) * q / 20.0) - o0
                    lx, ly, lz = p.dot(x0), p.dot(y0), p.dot(z0)
                    sy, sz = (k, k) if scaling == "Uniform" else (1.0, 1.0)
                    pt = o + x * (k * lx) + y * (sy * ly) + z * (sz * lz)
                    worst = max(worst, face.distToShape(Part.Vertex(pt))[0])
            self.assertLess(worst, 1e-3 + 1e-9, scaling)
            self.assertLessEqual(res["max_section_deviation"], 1e-3)

    def test_input_errors(self):
        r1, r2 = cylinder_rails()
        prof = half_circle()

        def start(*args, **kw):
            with self.assertRaises((ValueError, TypeError)) as cm:
                sweep(*args, **kw)
            return str(cm.exception)

        self.assertTrue(start(Part.Arc(V(0, 1, 0), V(5, 0, 5), V(10, 0, 0)), r1, r2).startswith("ProfileNotOnRails"))
        closed = Part.ArcOfCircle(Part.Circle(V(0, 0, 0), Z, 5), 0, 2 * math.pi)
        self.assertTrue(start(prof, closed, r2).startswith("ClosedRail"))
        corner = Part.Wire([line(V(0, 0, 0), V(0, 25, 0)).toShape(), line(V(0, 25, 0), V(-5, 50, 0)).toShape()])
        self.assertTrue(start(prof, corner, r2).startswith("RailNotSmooth"))
        self.assertTrue(start(prof, line(V(0, 0, 0), V(20, 50, 0)), line(V(10, 0, 0), V(-10, 50, 0))).startswith("DegenerateChord"))
        self.assertTrue(start(prof, r1, r2, scaling="Bogus").startswith("InvalidScaling"))
        for bad in (0, -1, float("nan"), float("inf")):
            self.assertTrue(start(prof, r1, r2, tolerance=bad).startswith("InvalidTolerance"), bad)
        self.assertTrue(start(prof, r1, r2, max_sections=1).startswith("InvalidMaxSections"))
        self.assertTrue(start(prof, Part.Line(), r2).startswith("InvalidInput"))
        self.assertTrue(start(prof, 5, r2).startswith("TypeError"))

    def test_wire_rails_that_are_smooth(self):
        a = Part.ArcOfCircle(Part.Circle(V(0, 0, 0), Z, 20), 0, math.pi / 4).toShape()
        b = Part.ArcOfCircle(Part.Circle(V(0, 0, 0), Z, 20), math.pi / 4, math.pi / 2).toShape()
        outer = Part.Wire([a, b])
        profile, _o, inner = torus_parts()
        r = sweep(profile, outer, inner, max_sections=17)
        self.assertLess(grid_max(r["surface"], torus_truth), 1e-4)


class TestTwoRailUsable(unittest.TestCase):
    def check_usable(self, face, offset=True):
        self.assertTrue(face.isValid())
        if offset:
            self.assertTrue(face.makeOffsetShape(1.0, 1e-6).isValid())
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "f.step")
            face.exportStep(path)
            back = Part.read(path)
            self.assertLess(abs(back.Area - face.Area) / face.Area, 1e-6)

    # A half circle is two conic arcs, so its parametrisation has a C0 knot at the join; OCCT's offset
    # refuses the Gordon surface built from such sections (a quarter circle offsets), so the offset leg is
    # checked on a quarter-circle profile and the other legs on the half circles.
    def test_cylinder(self):
        r1, r2 = cylinder_rails()
        self.check_usable(face_of(sweep(half_circle(), r1, r2)), offset=False)

    def test_quarter_cylinder_offsets(self):
        quarter = Part.Arc(V(0, 0, 0), V(5 - 5 * math.cos(math.pi / 4), 0, 5 * math.sin(math.pi / 4)), V(5, 0, 5))
        self.check_usable(face_of(sweep(quarter, line(V(0, 0, 0), V(0, 50, 0)), line(V(5, 0, 5), V(5, 50, 5)))))

    def test_torus(self):
        profile, outer, inner = torus_parts()
        self.check_usable(face_of(sweep(profile, outer, inner)), offset=False)

    def test_cut_a_box_with_the_extruded_face(self):
        c1, c2 = cone_rails()
        face = face_of(sweep(half_circle(), c1, c2))
        solid = face.extrude(V(0, 0, 1))
        self.assertTrue(solid.isValid())
        box = Part.makeBox(30, 60, 10, V(-10, -5, -3))
        cut = box.cut(Part.makeSolid(solid) if solid.ShapeType == "Shell" else solid)
        self.assertTrue(cut.isValid())


class TestTwoRailFeature(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument("TwoRailTest")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def obj(self, name, curve):
        o = self.doc.addObject("Part::Feature", name)
        o.Shape = curve.toShape()
        return o

    def feature(self, profile=None, r1=None, r2=None):
        profile = profile or half_circle()
        if r1 is None:
            r1, r2 = cylinder_rails()
        self.p, self.a, self.b = self.obj("Prof", profile), self.obj("RailA", r1), self.obj("RailB", r2)
        feat = SurfaceTwoRail.makeTwoRailFeature(self.doc, (self.p, "Edge1"), (self.a, "Edge1"), (self.b, "Edge1"))
        self.doc.recompute()
        return feat

    @staticmethod
    def grid(face, n=21):
        s = face.Surface
        u0, u1, v0, v1 = s.bounds()
        return [s.value(u0 + (u1 - u0) * i / (n - 1), v0 + (v1 - v0) * j / (n - 1)) for i in range(n) for j in range(n)]

    def test_feature_equals_api(self):
        feat = self.feature()
        self.assertTrue(feat.isValid(), feat.getStatusString())
        self.assertEqual(len(feat.Shape.Faces), 1)
        r1, r2 = cylinder_rails()
        api = face_of(sweep(half_circle(), r1, r2))
        for a, b in zip(self.grid(feat.Shape.Faces[0]), self.grid(api)):
            self.assertLess((a - b).Length, 1e-9)
        self.assertEqual(feat.Sections, 5)
        self.assertIs(feat.IsApproximate, False)
        self.assertEqual(feat.Scaling, "Uniform")
        self.assertEqual(feat.Tolerance, 1e-4)

    def test_feature_follows_edit_and_scaling(self):
        c1, c2 = cone_rails()
        feat = self.feature(r1=c1, r2=c2)
        area = feat.Shape.Area
        self.b.Placement = FreeCAD.Placement(V(0, 3, 0), FreeCAD.Rotation())
        self.doc.recompute()
        self.assertFalse(feat.isValid())  # the profile no longer reaches rail B
        self.b.Placement = FreeCAD.Placement()
        self.doc.recompute()
        self.assertTrue(feat.isValid())
        self.assertAlmostEqual(feat.Shape.Area, area, places=6)
        feat.Scaling = "Chord"
        self.doc.recompute()
        self.assertTrue(feat.isValid(), feat.getStatusString())
        top = max(feat.Shape.Faces[0].Surface.value(u, v).z for u in (0.0, 0.5, 1.0) for v in (0.0, 0.5, 1.0))
        self.assertLess(abs(top - 5.0), 1e-6)
        self.assertGreater(abs(feat.Shape.Area - area), 1e-3)

    def test_rail_in_a_placed_part(self):
        r1, r2 = cylinder_rails()
        part = self.doc.addObject("App::Part", "Holder")
        part.Placement = FreeCAD.Placement(V(3, 4, 5), FreeCAD.Rotation(V(0, 0, 1), 90))
        prof = self.obj("Prof", half_circle())
        prof.Placement = part.Placement
        left = self.obj("RailA", r1)
        right = self.obj("RailB", r2)
        part.addObject(left)
        feat = SurfaceTwoRail.makeTwoRailFeature(self.doc, (prof, "Edge1"), (part, "RailA.Edge1"), (right, "Edge1"))
        right.Placement = part.Placement
        self.doc.recompute()
        self.assertTrue(feat.isValid(), feat.getStatusString())
        e1 = left.Shape.copy()
        e1.Placement = part.Placement.multiply(left.Placement)
        e2 = right.Shape.copy()
        e2.Placement = part.Placement
        p = prof.Shape.copy()
        p.Placement = part.Placement
        api = face_of(sweep(p, e1, e2))
        for a, b in zip(self.grid(feat.Shape.Faces[0]), self.grid(api)):
            self.assertLess((a - b).Length, 1e-9)

    def test_save_and_reopen(self):
        feat = self.feature()
        area = feat.Shape.Area
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.FCStd")
            self.doc.saveAs(path)
            other = FreeCAD.openDocument(path)
            try:
                back = other.getObject(feat.Name)
                self.assertAlmostEqual(back.Shape.Area, area, places=9)
                other.recompute()
                self.assertTrue(back.isValid())
                self.assertAlmostEqual(back.Shape.Area, area, places=9)
                self.assertEqual(back.Scaling, "Uniform")
            finally:
                FreeCAD.closeDocument(other.Name)
            self.doc = FreeCAD.newDocument("TwoRailTest")

    def test_off_rail_profile_is_invalid_and_keeps_the_shape(self):
        feat = self.feature()
        area = feat.Shape.Area
        self.p.Placement = FreeCAD.Placement(V(0, 2, 0), FreeCAD.Rotation())
        self.doc.recompute()
        self.assertFalse(feat.isValid())
        self.assertIn("ProfileNotOnRails", feat.getStatusString())
        self.assertAlmostEqual(feat.Shape.Area, area, places=9)

    def test_feature_has_link_dependencies(self):
        feat = self.feature()
        self.assertEqual({o.Name for o in feat.OutList}, {self.p.Name, self.a.Name, self.b.Name})


class TestDeletedInputs(unittest.TestCase):
    """R114a: a deleted input is reported as ``InputDeleted``, not dropped from the links."""

    def setUp(self):
        self.doc = FreeCAD.newDocument("DeletedInputs")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def objs(self, name, curves):
        out = []
        for i, c in enumerate(curves):
            o = self.doc.addObject("Part::Feature", "%s%d" % (name, i))
            o.Shape = c.toShape()
            o.Label = "%sLabel%d" % (name, i)
            out.append(o)
        return out

    def gordon(self):
        profiles, guides = cylinder_network()
        self.p, self.g = self.objs("P", profiles), self.objs("G", guides)
        feat = SurfaceGordon.makeGordonFeature(self.doc, [(o, "Edge1") for o in self.p], [(o, "Edge1") for o in self.g])
        self.doc.recompute()
        return feat

    def test_gordon_delete_then_undo(self):
        feat = self.gordon()
        self.assertTrue(feat.isValid(), feat.getStatusString())
        area = feat.Shape.Area
        self.doc.openTransaction("delete")
        self.doc.removeObject(self.p[1].Name)
        self.doc.commitTransaction()
        self.doc.recompute()
        self.assertFalse(feat.isValid())
        self.assertTrue(feat.getStatusString().find("InputDeleted") >= 0, feat.getStatusString())
        self.assertIn("PLabel1", feat.getStatusString())
        self.assertAlmostEqual(feat.Shape.Area, area, places=9)
        self.doc.undo()
        feat.touch()
        self.doc.recompute()
        self.assertTrue(feat.isValid(), feat.getStatusString())
        self.assertEqual(len(feat.Profiles), 4)
        self.assertAlmostEqual(feat.Shape.Area, area, places=9)

    def test_deliberate_shorter_list_recomputes(self):
        feat = self.gordon()
        feat.Profiles = [(self.p[i], ["Edge1"]) for i in (0, 2, 3)]
        feat.Guides = [(self.g[0], ["Edge1"]), (self.g[3], ["Edge1"])]
        self.doc.recompute()
        self.assertTrue(feat.isValid(), feat.getStatusString())
        self.assertNotIn("InputDeleted", feat.getStatusString())

    def test_two_rail_delete_then_undo(self):
        r1, r2 = cylinder_rails()
        o = self.objs("T", [half_circle(), r1, r2])
        feat = SurfaceTwoRail.makeTwoRailFeature(self.doc, (o[0], "Edge1"), (o[1], "Edge1"), (o[2], "Edge1"))
        self.doc.recompute()
        area = feat.Shape.Area
        self.doc.openTransaction("delete")
        self.doc.removeObject(o[1].Name)
        self.doc.commitTransaction()
        self.doc.recompute()
        self.assertFalse(feat.isValid())
        self.assertIn("InputDeleted", feat.getStatusString())
        self.assertIn("TLabel1", feat.getStatusString())
        self.assertAlmostEqual(feat.Shape.Area, area, places=9)
        self.doc.undo()
        feat.touch()
        self.doc.recompute()
        self.assertTrue(feat.isValid(), feat.getStatusString())
        self.assertAlmostEqual(feat.Shape.Area, area, places=9)

    def test_two_rail_relink_after_delete_recovers(self):
        r1, r2 = cylinder_rails()
        o = self.objs("T", [half_circle(), r1, r2])
        spare = self.objs("S", [line(V(10, 0, 0), V(10, 50, 0))])[0]
        feat = SurfaceTwoRail.makeTwoRailFeature(self.doc, (o[0], "Edge1"), (o[1], "Edge1"), (o[2], "Edge1"))
        self.doc.recompute()
        area = feat.Shape.Area
        self.doc.removeObject(o[2].Name)
        self.doc.recompute()
        self.assertIn("InputDeleted", feat.getStatusString())
        feat.Rail2 = (spare, ["Edge1"])
        self.doc.recompute()
        self.assertTrue(feat.isValid(), feat.getStatusString())
        self.assertAlmostEqual(feat.Shape.Area, area, places=6)

    def test_gordon_relink_after_delete_recovers(self):
        feat = self.gordon()
        area = feat.Shape.Area
        spare = self.objs("X", [Part.ArcOfCircle(Part.Circle(V(0, 0, 20), Z, 10), 0, math.pi / 2)])[0]
        self.doc.removeObject(self.p[2].Name)
        self.doc.recompute()
        self.assertIn("InputDeleted", feat.getStatusString())
        feat.Profiles = [(self.p[0], ["Edge1"]), (self.p[1], ["Edge1"]), (spare, ["Edge1"]), (self.p[3], ["Edge1"])]
        self.doc.recompute()
        self.assertTrue(feat.isValid(), feat.getStatusString())
        self.assertNotIn("InputDeleted", feat.getStatusString())
        self.assertAlmostEqual(feat.Shape.Area, area, places=6)

    def test_tolerance_is_validated_first(self):
        for bad in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError) as cm:
                SurfaceTwoRail.makeTwoRailSweep(5, 6, 7, tolerance=bad)
            self.assertTrue(str(cm.exception).startswith("InvalidTolerance"), bad)

    def test_file_without_the_guard_still_opens(self):
        """A file saved before R114a has no ``InputNames``: it opens and recomputes with the same shape."""
        feat = self.gordon()
        area = feat.Shape.Area
        feat.removeProperty("InputNames")
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "old.FCStd")
            self.doc.saveAs(path)
            other = FreeCAD.openDocument(path)
            try:
                back = other.getObject(feat.Name)
                self.assertIn("InputNames", back.PropertiesList)
                other.recompute()
                back.touch()
                other.recompute()
                self.assertTrue(back.isValid(), back.getStatusString())
                self.assertAlmostEqual(back.Shape.Area, area, places=9)
            finally:
                FreeCAD.closeDocument(other.Name)
            self.doc = FreeCAD.newDocument("DeletedInputs")
