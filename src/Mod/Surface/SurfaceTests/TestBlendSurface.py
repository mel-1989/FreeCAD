# SPDX-License-Identifier: LGPL-2.1-or-later

"""Tests of ``SurfaceBlendSurface`` (the G2 blend surface, G09 slice 3c) and of the cross-document copy of
the features that keep a deleted-input record (R115).

Public sources: the Hermite form of Bezier curves (L. Piegl and W. Tiller, *The NURBS Book*, 2nd ed.,
1997, chapters 1 and 2); the normal curvature of a surface (M. do Carmo, *Differential Geometry of Curves
and Surfaces*, 1976, chapter 3). The truths are closed form: planes, a step between parallel strips (a
quintic), a quarter cylinder and a sphere patch.
"""

import math
import os
import tempfile
import unittest

import FreeCAD
import Part
import SurfaceBlendSurface as SBS
import SurfaceGordon
import SurfaceTwoRail
from SurfaceTests.TestGordon import cylinder_network
from SurfaceTests.TestTwoRail import cylinder_rails, half_circle

V = FreeCAD.Vector


def strip(z0, x0, x1, y1=10.0):
    return Part.makePlane(x1 - x0, y1, V(x0, 0, z0))


def xedge(face, x):
    return [e for e in face.Edges if abs(e.BoundBox.XMin - x) < 1e-9 and abs(e.BoundBox.XMax - x) < 1e-9][0]


def step(size1=1.0, size2=1.0, **kw):
    f1, f2 = strip(0, -5, 0), strip(3, 5, 10)
    return f1, xedge(f1, 0), f2, xedge(f2, 5), SBS.makeBlendSurface(f1, xedge(f1, 0), f2, xedge(f2, 5), size1=size1, size2=size2, **kw)


def sphere_cylinder():
    sph = Part.makeSphere(10, V(0, 0, 0), V(0, 0, 1), 0, 90, 90).Faces[0]
    e1 = [e for e in sph.Edges if not e.Degenerated and e.Curve.TypeId.endswith("Circle") and abs(e.BoundBox.ZMax) < 1e-6][0]
    cyl = Part.makeCylinder(14, 10, V(0, 0, -15), V(0, 0, 1), 90).Faces[0]
    e2 = [e for e in cyl.Edges if not e.Degenerated and e.Curve.TypeId.endswith("Circle") and abs(e.BoundBox.ZMax + 5) < 1e-6][0]
    return sph, e1, cyl, e2


def freeform_plane():
    pts = [[V(i * 2.5, j * 2.5, 1.5 * math.sin(i * 0.7) * math.cos(j * 0.5)) for j in range(5)] for i in range(5)]
    bs = Part.BSplineSurface()
    bs.interpolate(pts)
    ff = bs.toShape()
    e1 = [e for e in ff.Edges if abs(e.BoundBox.XMin - 10) < 1e-6 and e.BoundBox.YLength > 9][0]
    pl = Part.makePlane(10, 10, V(14, 0, 0))
    e2 = [e for e in pl.Edges if abs(e.BoundBox.XMax - 14) < 1e-9 and abs(e.BoundBox.XMin - 14) < 1e-9][0]
    return ff, e1, pl, e2


def cylinder_plane():
    cyl = Part.makeCylinder(5, 10, V(0, 0, 0), V(0, 1, 0), 90)
    cf = [f for f in cyl.Faces if f.Surface.TypeId.endswith("Cylinder")][0]
    e1 = [e for e in cf.Edges if e.BoundBox.YLength > 9 and abs(e.BoundBox.XMin - 5) < 1e-9][0]
    pl = Part.makePlane(12, 10, V(8, 0, -2))
    e2 = [e for e in pl.Edges if abs(e.BoundBox.XMin - 8) < 1e-9 and abs(e.BoundBox.XMax - 8) < 1e-9][0]
    return cf, e1, pl, e2


def grid(surface, n=21):
    u0, u1, v0, v1 = surface.bounds()
    return [[surface.value(u0 + (u1 - u0) * i / (n - 1.0), v0 + (v1 - v0) * j / (n - 1.0)) for j in range(n)] for i in range(n)]


class TestBlendSurfaceApi(unittest.TestCase):
    def test_coplanar_blend_is_the_plane(self):
        f1, f2 = strip(0, -5, 0), strip(0, 5, 10)
        r = SBS.makeBlendSurface(f1, xedge(f1, 0), f2, xedge(f2, 5))
        self.assertFalse(r["approximate"])
        self.assertEqual(r["sections"], 5)
        for row in grid(r["surface"]):
            for p in row:
                self.assertLess(abs(p.z), 1e-9)

    def test_g0_blend_between_skew_edges_is_the_ruled_surface(self):
        f1 = Part.Face(Part.makePolygon([V(0, 0, 0), V(10, 0, 0), V(10, -5, 0), V(0, -5, 0), V(0, 0, 0)]))
        f2 = Part.Face(Part.makePolygon([V(0, 5, 3), V(10, 8, 5), V(10, 8, 10), V(0, 5, 10), V(0, 5, 3)]))
        e1 = [e for e in f1.Edges if abs(e.BoundBox.YMax) < 1e-9 and abs(e.BoundBox.YMin) < 1e-9][0]
        e2 = [e for e in f2.Edges if e.BoundBox.ZLength < 2.5 and e.BoundBox.XLength > 9][0]
        r = SBS.makeBlendSurface(f1, e1, f2, e2, continuity1=0, continuity2=0)
        ruled = Part.makeRuledSurface(Part.Edge(Part.LineSegment(e1.valueAt(e1.FirstParameter), e1.valueAt(e1.LastParameter))),
                                      Part.Edge(Part.LineSegment(e2.valueAt(e2.FirstParameter), e2.valueAt(e2.LastParameter)))).Face1.Surface
        self.assertEqual(r["sections"], 5)
        self.assertFalse(r["approximate"])
        a, b = grid(r["surface"]), grid(ruled)
        for i in range(21):
            for j in range(21):
                self.assertLess((a[i][j] - b[i][j]).Length, 1e-9)

    def test_step_is_the_quintic_of_the_frozen_rule(self):
        _f1, _e1, _f2, _e2, r = step()
        c = math.sqrt(25.0 + 9.0)
        a, b, x = V(0, 0, 0), V(5, 0, 3), V(1, 0, 0)
        poles = [a, a + x * (c / 5), a + x * (2 * c / 5), b - x * (2 * c / 5), b - x * (c / 5), b]
        self.assertEqual(r["sections"], 5)
        self.assertEqual(r["surface"].VDegree, 5)
        surf = r["surface"]
        for iy in range(21):
            y = 10.0 * iy / 20.0
            for jv in range(21):
                t = jv / 20.0
                p = V(0, y, 0)
                for k, pole in enumerate(poles):
                    p = p + pole * (math.comb(5, k) * t**k * (1 - t) ** (5 - k))
                q = surf.value(iy / 20.0, t)
                self.assertLess((p - q).Length, 1e-9)

    def test_continuity_g2_on_sphere_to_cylinder(self):
        sph, e1, cyl, e2 = sphere_cylinder()
        r = SBS.makeBlendSurface(sph, e1, cyl, e2)
        self.assertEqual(r["sections"], 17)
        self.assertFalse(r["approximate"])
        for side in ("side1", "side2"):
            self.assertLessEqual(r[side]["gap"], 1e-4)
            self.assertLessEqual(r[side]["angle"], 0.05)
            self.assertLessEqual(r[side]["curvature_jump"], 0.01)
        self.assertTrue(r["face"].isValid())

    def test_freeform_to_plane(self):
        ff, e1, pl, e2 = freeform_plane()
        r = SBS.makeBlendSurface(ff, e1, pl, e2)
        self.assertFalse(r["approximate"])
        self.assertLessEqual(r["side1"]["curvature_jump"], 0.01)
        self.assertLessEqual(r["side2"]["gap"], 1e-9)

    def test_deviation_rule_and_approximate(self):
        sph, e1, cyl, e2 = sphere_cylinder()
        with self.assertRaises(ValueError) as cm:
            SBS.makeBlendSurface(sph, e1, cyl, e2, max_sections=9)
        self.assertTrue(str(cm.exception).startswith("DeviationExceeded"), str(cm.exception))
        r = SBS.makeBlendSurface(sph, e1, cyl, e2, max_sections=9, approximate=True)
        self.assertTrue(r["approximate"])
        self.assertEqual(r["sections"], 9)

    def test_mixed_continuity_degree_and_sizes(self):
        cf, e1, pl, e2 = cylinder_plane()
        r = SBS.makeBlendSurface(cf, e1, pl, e2, continuity1=2, continuity2=1)
        self.assertEqual(r["surface"].VDegree, 4)
        self.assertFalse(r["approximate"])
        self.assertLessEqual(r["side1"]["curvature_jump"], 0.01)
        self.assertLessEqual(r["side2"]["angle"], 0.05)
        g1 = SBS.makeBlendSurface(cf, e1, pl, e2, continuity1=1, continuity2=0)
        self.assertEqual(g1["surface"].VDegree, 2)

    def test_edge_two_reversed_and_face_two_reversed(self):
        f1 = strip(0, -5, 0)
        f2 = Part.Face(Part.makePolygon([V(5, 10, 3), V(5, 0, 3), V(10, 0, 3), V(10, 10, 3), V(5, 10, 3)]))
        e1, e2 = xedge(f1, 0), xedge(f2, 5)
        r = SBS.makeBlendSurface(f1, e1, f2, e2)
        self.assertTrue(r["edge2_reversed"])
        self.assertFalse(r["approximate"])
        _a, _b, _c, _d, plain = step()
        self.assertFalse(plain["edge2_reversed"])
        a, b = grid(r["surface"]), grid(plain["surface"])
        for i in range(21):
            for j in range(21):
                self.assertLess((a[i][j] - b[i][j]).Length, 1e-9)
        g2 = f2.copy()
        g2.reverse()
        r2 = SBS.makeBlendSurface(f1, e1, g2, xedge(g2, 5))
        self.assertFalse(r2["approximate"])
        self.assertLessEqual(r2["side2"]["angle"], 0.05)
        c = grid(r2["surface"])
        for i in range(21):
            for j in range(21):
                self.assertLess((a[i][j] - c[i][j]).Length, 1e-9)

    def test_refusals(self):
        f1, f2 = strip(0, -5, 0), strip(3, 5, 10)
        e1, e2 = xedge(f1, 0), xedge(f2, 5)
        cases = [
            (dict(tolerance=0), "InvalidTolerance"),
            (dict(tolerance=-1), "InvalidTolerance"),
            (dict(tolerance=float("nan")), "InvalidTolerance"),
            (dict(tolerance=float("inf")), "InvalidTolerance"),
            (dict(continuity1=3), "InvalidContinuity"),
            (dict(continuity2=-1), "InvalidContinuity"),
            (dict(size1=0), "InvalidSize"),
            (dict(size2=-1), "InvalidSize"),
            (dict(size1=float("nan")), "InvalidSize"),
            (dict(max_sections=1), "InvalidMaxSections"),
        ]
        for kw, name in cases:
            with self.assertRaises(ValueError, msg=str(kw)) as cm:
                SBS.makeBlendSurface(f1, e1, f2, e2, **kw)
            self.assertTrue(str(cm.exception).startswith(name), "%s: %s" % (kw, cm.exception))
        with self.assertRaises(ValueError) as cm:
            SBS.makeBlendSurface(f1, xedge(f2, 5), f2, e2)
        self.assertTrue(str(cm.exception).startswith("EdgeNotOnFace"))
        with self.assertRaises(TypeError):
            SBS.makeBlendSurface(e1, e1, f2, e2)
        with self.assertRaises(TypeError):
            SBS.makeBlendSurface(f1, f1, f2, e2)

    def test_degenerate_closed_and_touching_edges(self):
        sph = Part.makeSphere(10).Faces[0]
        pole = [e for e in sph.Edges if e.Degenerated][0]
        f2 = strip(3, 5, 10)
        with self.assertRaises(ValueError) as cm:
            SBS.makeBlendSurface(sph, pole, f2, xedge(f2, 5))
        self.assertTrue(str(cm.exception).startswith("DegenerateEdge"), str(cm.exception))
        cyl = Part.makeCylinder(10, 10).Faces[0]
        circle = [e for e in cyl.Edges if e.isClosed() and e.Curve.TypeId.endswith("Circle")][0]
        with self.assertRaises(ValueError) as cm:
            SBS.makeBlendSurface(cyl, circle, f2, xedge(f2, 5))
        self.assertTrue(str(cm.exception).startswith("ClosedEdge"), str(cm.exception))
        g1, g2 = strip(0, -5, 0), strip(0, 0, 5)
        with self.assertRaises(ValueError) as cm:
            SBS.makeBlendSurface(g1, xedge(g1, 0), g2, xedge(g2, 0))
        self.assertTrue(str(cm.exception).startswith("DegenerateChord"), str(cm.exception))
        # two edges that touch at one end
        h1 = Part.Face(Part.makePolygon([V(0, 0, 0), V(10, 0, 0), V(10, -5, 0), V(0, -5, 0), V(0, 0, 0)]))
        h2 = Part.Face(Part.makePolygon([V(0, 0, 0), V(10, 5, 0), V(10, 5, 5), V(0, 0, 5), V(0, 0, 0)]))
        a = [e for e in h1.Edges if abs(e.BoundBox.YMax) < 1e-9 and abs(e.BoundBox.YMin) < 1e-9][0]
        b = [e for e in h2.Edges if e.BoundBox.ZLength < 1e-9 and e.BoundBox.XLength > 9][0]
        with self.assertRaises(ValueError) as cm:
            SBS.makeBlendSurface(h1, a, h2, b)
        self.assertTrue(str(cm.exception).startswith("DegenerateChord"), str(cm.exception))


class TestBlendSurfaceUsable(unittest.TestCase):
    def check_usable(self, r, size_offset=True):
        face = r["face"]
        self.assertTrue(face.isValid())
        if size_offset:
            self.assertTrue(face.makeOffsetShape(1.0, 1e-6).isValid())
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "f.step")
            face.exportStep(path)
            back = Part.read(path)
            self.assertLess(abs(back.Area - face.Area) / face.Area, 1e-6)

    def test_step(self):
        f1, e1, f2, e2, r = step()
        self.check_usable(r)
        self.assertFalse(r["backtracks"])
        self.assertAlmostEqual(r["face"].BoundBox.XMin, 0.0, places=9)
        self.assertAlmostEqual(r["face"].BoundBox.XMax, 5.0, places=9)

    def test_sphere_to_cylinder(self):
        sph, e1, cyl, e2 = sphere_cylinder()
        self.check_usable(SBS.makeBlendSurface(sph, e1, cyl, e2))

    def test_backtracking_step_is_flagged(self):
        # the frozen flag is C'(t) . (B - A) < 0: at size 2 the 5 mm by 3 mm step stays just above zero
        # (x already doubles back), at size 3 it goes negative
        self.assertFalse(step(size1=2.0, size2=2.0)[4]["backtracks"])
        r = step(size1=3.0, size2=3.0)[4]
        self.assertTrue(r["backtracks"])
        self.assertFalse(r["approximate"])


class TestBlendSurfaceFeature(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument("BlendFeature")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def obj(self, name, shape):
        o = self.doc.addObject("Part::Feature", name)
        o.Shape = shape
        o.Label = name + "Label"
        return o

    def step_objects(self):
        self.a = self.obj("FaceA", strip(0, -5, 0))
        self.b = self.obj("FaceB", strip(3, 5, 10))
        self.doc.recompute()

    def feature(self):
        self.step_objects()
        names = [("Edge%d" % (i + 1)) for i, e in enumerate(self.a.Shape.Edges) if abs(e.BoundBox.XMin) < 1e-9 and abs(e.BoundBox.XMax) < 1e-9]
        nb = [("Edge%d" % (i + 1)) for i, e in enumerate(self.b.Shape.Edges) if abs(e.BoundBox.XMin - 5) < 1e-9 and abs(e.BoundBox.XMax - 5) < 1e-9]
        feat = SBS.makeBlendSurfaceFeature(self.doc, (self.a, names[0]), (self.b, nb[0]))
        self.doc.recompute()
        return feat

    def test_feature_equals_the_api(self):
        feat = self.feature()
        self.assertTrue(feat.isValid(), feat.getStatusString())
        ref = step()[4]
        a, b = grid(feat.Shape.Face1.Surface), grid(ref["surface"])
        for i in range(21):
            for j in range(21):
                self.assertLess((a[i][j] - b[i][j]).Length, 1e-9)
        self.assertEqual(feat.Sections, 5)
        self.assertFalse(feat.IsApproximate)
        self.assertLess(feat.MaxGap, 1e-9)
        self.assertEqual(feat.Continuity1, "G2")

    def test_edit_and_property_changes_recompute(self):
        feat = self.feature()
        area = feat.Shape.Area
        self.b.Placement = FreeCAD.Placement(V(0, 0, 2), FreeCAD.Rotation())
        self.doc.recompute()
        self.assertTrue(feat.isValid(), feat.getStatusString())
        self.assertNotAlmostEqual(feat.Shape.Area, area, places=3)
        a2 = feat.Shape.Area
        feat.Continuity1 = "G0"
        feat.Size2 = 1.5
        self.doc.recompute()
        self.assertTrue(feat.isValid(), feat.getStatusString())
        self.assertNotAlmostEqual(feat.Shape.Area, a2, places=6)

    def test_save_and_reopen(self):
        feat = self.feature()
        area = feat.Shape.Area
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "b.FCStd")
            self.doc.saveAs(path)
            other = FreeCAD.openDocument(path)
            try:
                back = other.getObject(feat.Name)
                self.assertAlmostEqual(back.Shape.Area, area, places=9)
                other.recompute()
                self.assertTrue(back.isValid(), back.getStatusString())
                self.assertAlmostEqual(back.Shape.Area, area, places=9)
            finally:
                FreeCAD.closeDocument(other.Name)
            self.doc = FreeCAD.newDocument("BlendFeature")

    def test_face_sub_and_ambiguous_face(self):
        box = self.obj("Box", Part.makeBox(10, 10, 10))
        pl = self.obj("Plane", strip(3, 14, 24))
        pe = [("Edge%d" % (i + 1)) for i, e in enumerate(pl.Shape.Edges) if abs(e.BoundBox.XMin - 14) < 1e-9 and abs(e.BoundBox.XMax - 14) < 1e-9][0]
        bs = box.Shape
        idx = [i for i, e in enumerate(bs.Edges) if abs(e.BoundBox.XMin - 10) < 1e-9 and abs(e.BoundBox.XMax - 10) < 1e-9 and abs(e.BoundBox.ZMin - 10) < 1e-9 and abs(e.BoundBox.ZMax - 10) < 1e-9][0]
        top = [i for i, f in enumerate(bs.Faces) if abs(f.BoundBox.ZMin - 10) < 1e-9 and f.BoundBox.ZLength < 1e-9][0]
        feat = SBS.makeBlendSurfaceFeature(self.doc, (box, "Edge%d" % (idx + 1)), (pl, pe))
        self.doc.recompute()
        self.assertFalse(feat.isValid())
        self.assertIn("AmbiguousFace", feat.getStatusString())
        feat.Edge1 = (box, ["Face%d" % (top + 1), "Edge%d" % (idx + 1)])
        self.doc.recompute()
        self.assertTrue(feat.isValid(), feat.getStatusString())
        wrong = [i for i, f in enumerate(bs.Faces) if abs(f.BoundBox.XMin) < 1e-9 and f.BoundBox.XLength < 1e-9][0]
        feat.Edge1 = (box, ["Face%d" % (wrong + 1), "Edge%d" % (idx + 1)])
        self.doc.recompute()
        self.assertFalse(feat.isValid())
        self.assertIn("EdgeNotOnFace", feat.getStatusString())

    def test_touching_edges_keep_the_last_shape(self):
        feat = self.feature()
        area = feat.Shape.Area
        self.b.Placement = FreeCAD.Placement(V(-5, 0, -3), FreeCAD.Rotation())
        self.doc.recompute()
        self.assertFalse(feat.isValid())
        self.assertIn("DegenerateChord", feat.getStatusString())
        self.assertAlmostEqual(feat.Shape.Area, area, places=9)

    def test_edge_in_a_placed_part(self):
        self.step_objects()
        part = self.doc.addObject("App::Part", "Holder")
        part.Placement = FreeCAD.Placement(V(3, 4, 5), FreeCAD.Rotation(V(0, 0, 1), 90))
        part.addObject(self.b)
        self.doc.recompute()
        ea = [("Edge%d" % (i + 1)) for i, e in enumerate(self.a.Shape.Edges) if abs(e.BoundBox.XMin) < 1e-9 and abs(e.BoundBox.XMax) < 1e-9][0]
        # face B sits at x 5..10, y 0..10, z 3 in its own frame; the part rotates it by 90 degrees about Z and moves it
        placed = Part.getShape(part, "%s.Face1" % self.b.Name, needSubElement=True)
        self.assertAlmostEqual(placed.BoundBox.ZMin, 8.0, places=9)
        eb = "%s.Edge%d" % (self.b.Name, [i for i, e in enumerate(self.b.Shape.Edges) if abs(e.BoundBox.XMin - 5) < 1e-9 and abs(e.BoundBox.XMax - 5) < 1e-9][0] + 1)
        feat = SBS.makeBlendSurfaceFeature(self.doc, (self.a, ea), (part, eb))
        self.doc.recompute()
        self.assertTrue(feat.isValid(), feat.getStatusString())
        bb = feat.Shape.BoundBox
        self.assertAlmostEqual(bb.ZMax, 8.0, places=6)

    def test_deleted_input_and_undo(self):
        self.doc.UndoMode = 1
        feat = self.feature()
        area = feat.Shape.Area
        self.doc.openTransaction("delete")
        self.doc.removeObject(self.b.Name)
        self.doc.commitTransaction()
        self.doc.recompute()
        self.assertFalse(feat.isValid())
        self.assertIn("InputDeleted", feat.getStatusString())
        self.assertIn("FaceBLabel", feat.getStatusString())
        self.assertAlmostEqual(feat.Shape.Area, area, places=9)
        self.doc.undo()
        feat.touch()
        self.doc.recompute()
        self.assertTrue(feat.isValid(), feat.getStatusString())
        self.assertAlmostEqual(feat.Shape.Area, area, places=9)


class TestCrossDocumentCopy(unittest.TestCase):
    """R115 F1 and F2: a copied or merged feature re-records its inputs against its own document."""

    def setUp(self):
        self.src = FreeCAD.newDocument("CopySrc")
        self.dst = FreeCAD.newDocument("CopyDst")

    def tearDown(self):
        for d in (self.src, self.dst):
            FreeCAD.closeDocument(d.Name)

    def objs(self, doc, name, curves):
        out = []
        for i, c in enumerate(curves):
            o = doc.addObject("Part::Feature", "%s%d" % (name, i))
            o.Shape = c.toShape()
            o.Label = "%sLabel%d" % (name, i)
            out.append(o)
        return out

    def gordon(self):
        profiles, guides = cylinder_network()
        p, g = self.objs(self.src, "P", profiles), self.objs(self.src, "G", guides)
        feat = SurfaceGordon.makeGordonFeature(self.src, [(o, "Edge1") for o in p], [(o, "Edge1") for o in g])
        self.src.recompute()
        return feat, p

    def two_rail(self):
        r1, r2 = cylinder_rails()
        o = self.objs(self.src, "T", [half_circle(), r1, r2])
        feat = SurfaceTwoRail.makeTwoRailFeature(self.src, (o[0], "Edge1"), (o[1], "Edge1"), (o[2], "Edge1"))
        self.src.recompute()
        return feat, o

    def blend(self):
        a, b = [self.src.addObject("Part::Feature", "F%d" % i) for i in range(2)]
        a.Shape, b.Shape = strip(0, -5, 0), strip(3, 5, 10)
        a.Label, b.Label = "FLabel0", "FLabel1"
        ea = [("Edge%d" % (i + 1)) for i, e in enumerate(a.Shape.Edges) if abs(e.BoundBox.XMin) < 1e-9 and abs(e.BoundBox.XMax) < 1e-9][0]
        eb = [("Edge%d" % (i + 1)) for i, e in enumerate(b.Shape.Edges) if abs(e.BoundBox.XMin - 5) < 1e-9 and abs(e.BoundBox.XMax - 5) < 1e-9][0]
        feat = SBS.makeBlendSurfaceFeature(self.src, (a, ea), (b, eb))
        self.src.recompute()
        return feat, [a, b]

    def clash(self, src_objs):
        """Objects in the destination with the source's names, so the copies get renamed."""
        for o in src_objs:
            x = self.dst.addObject("Part::Feature", o.Name)
            x.Shape = Part.makeBox(1, 1, 1)

    def copied(self, feat):
        self.dst.copyObject(feat, True)
        self.dst.recompute()
        return [o for o in self.dst.Objects if o.TypeId == feat.TypeId][-1]

    def check_copy(self, make, clash):
        feat, inputs = make()
        area = feat.Shape.Area
        if clash:
            self.clash(inputs)
        copy = self.copied(feat)
        self.assertTrue(copy.isValid(), copy.getStatusString())
        self.assertAlmostEqual(copy.Shape.Area, area, places=9)
        # a delete in the destination names the right input
        victim = [o for o in copy.OutList if o.TypeId == "Part::Feature"][1]
        label = victim.Label
        self.dst.removeObject(victim.Name)
        self.dst.recompute()
        self.assertFalse(copy.isValid())
        self.assertIn("InputDeleted: %s " % label, copy.getStatusString())

    def test_gordon_copy(self):
        self.check_copy(self.gordon, False)

    def test_gordon_copy_with_name_clashes(self):
        self.check_copy(self.gordon, True)

    def test_two_rail_copy(self):
        self.check_copy(self.two_rail, False)

    def test_blend_copy(self):
        self.check_copy(self.blend, False)

    def test_blend_copy_with_name_clashes(self):
        self.check_copy(self.blend, True)

    def test_gordon_merge_names_the_deleted_input(self):
        feat, p = self.gordon()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "src.FCStd")
            self.src.saveAs(path)
            self.dst.mergeProject(path)
        merged = [o for o in self.dst.Objects if o.TypeId == feat.TypeId][0]
        self.dst.recompute()
        self.assertTrue(merged.isValid(), merged.getStatusString())
        victim = self.dst.getObject(p[1].Name)
        label = victim.Label
        self.dst.removeObject(victim.Name)
        self.dst.recompute()
        self.assertFalse(merged.isValid())
        self.assertIn("InputDeleted: %s " % label, merged.getStatusString())

    def test_blend_merge_names_the_deleted_input(self):
        feat, inputs = self.blend()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "src.FCStd")
            self.src.saveAs(path)
            self.dst.mergeProject(path)
        merged = [o for o in self.dst.Objects if o.TypeId == feat.TypeId][0]
        self.dst.recompute()
        self.assertTrue(merged.isValid(), merged.getStatusString())
        victim = self.dst.getObject(inputs[1].Name)
        label = victim.Label
        self.dst.removeObject(victim.Name)
        self.dst.recompute()
        self.assertFalse(merged.isValid())
        self.assertIn("InputDeleted: %s " % label, merged.getStatusString())
