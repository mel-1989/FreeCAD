# SPDX-License-Identifier: LGPL-2.1-or-later

"""Tests of the ``SurfaceMatch.MatchSurface`` feature (G09 slice 5b) and of repair R120.

Public sources: L. Piegl and W. Tiller, *The NURBS Book*, 2nd ed., 1997 (B-spline surfaces and knot
insertion); M. P. do Carmo, *Differential Geometry of Curves and Surfaces*, 1976, chapter 3; A. Pegna and
F. Wolter, 1992 (linkage curves). The reference for every equality is ``matchSurface`` itself, called on
the faces the test resolved with ``Part.getShape``.
"""

import math
import os
import tempfile
import unittest

import FreeCAD
import Part
import SurfaceBlendSurface as SBS
import SurfaceMatch as SM
import SurfacePoleEdit as PE
from SurfaceTests.TestSurfaceMatch import net_surface, pick_edge, poly_face

V = FreeCAD.Vector


def same_surface(a, b, tol=1e-9, n=21):
    u0, u1, v0, v1 = a.bounds()
    for i in range(n):
        for j in range(n):
            u, v = u0 + (u1 - u0) * i / (n - 1.0), v0 + (v1 - v0) * j / (n - 1.0)
            if (a.value(u, v) - b.value(u, v)).Length > tol:
                return False
    return True


def rising_net(x0=0.0, x1=40.0, nu=6):
    """A bicubic net rising from y = 2 beside the top edge (y = 0, z = 20) of the box of ``scene``."""
    P = [[V(x0 + (x1 - x0) * i / (nu - 1.0), 2 + 7.5 * j, 20 + 1.5 * j + 2 * math.sin(1.7 * i + 0.9 * j + 0.5) * (j > 0)) for j in range(5)] for i in range(nu)]
    return net_surface(P)


def face_name(obj, pred):
    for i, f in enumerate(obj.Shape.Faces):
        if pred(f):
            return "Face%d" % (i + 1)


def edge_name(obj, edge):
    for i, e in enumerate(obj.Shape.Edges):
        if e.isSame(edge):
            return "Edge%d" % (i + 1)


class Scene:
    """A box (top at z = 20, edge y = 0) and a B-spline source rising beside it."""

    def __init__(self, doc):
        self.doc = doc
        self.box = doc.addObject("Part::Feature", "Box")
        self.box.Shape = Part.makeBox(40, 30, 20, V(0, -30, 0))
        self.src = doc.addObject("Part::Feature", "Src")
        self.src.Shape = rising_net().toShape()
        doc.recompute()
        top = [f for f in self.box.Shape.Faces if abs(f.CenterOfMass.z - 20) < 1e-6][0]
        self.top = face_name(self.box, lambda f: f.isSame(top))
        self.edge = edge_name(self.box, pick_edge(top, V(20, 0, 20)))
        self.target = (self.box, [self.top, self.edge])

    def make(self, side="V0", k=2, **props):
        feat = SM.makeMatchSurfaceFeature(self.doc, (self.src, "Face1"), side, self.target)
        feat.Continuity = "G%d" % k
        for key, value in props.items():
            setattr(feat, key, value)
        self.doc.recompute()
        return feat

    def reference(self, feat):
        face = PE.resolve_face(feat.Source)
        tface, tedge = SBS.resolve_edge_face(feat.Target)
        return SM.matchSurface(face, feat.Side, tface, tedge, continuity=int(feat.Continuity[1]), tolerance=feat.Tolerance, max_level=feat.MaxLevel, approximate=feat.AllowApproximation)


class TestMatchSurfaceFeature(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument("G09MatchFeature")
        self.scene = Scene(self.doc)

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def check_equal(self, feat):
        self.assertTrue(feat.isValid(), feat.getStatusString())
        ref = self.scene.reference(feat)
        self.assertTrue(same_surface(feat.Shape.Faces[0].Surface, ref["surface"]))
        self.assertEqual(feat.Level, ref["level"])
        self.assertEqual(feat.PolesAlong, ref["poles_along"])
        self.assertEqual(feat.DegreeAlong, ref["degree_along"])
        self.assertEqual(feat.IsApproximate, ref["approximate"])
        self.assertAlmostEqual(feat.MaxGap, ref["gap"], delta=1e-12)
        self.assertAlmostEqual(feat.MaxMove, ref["max_move"], delta=1e-9)
        self.assertEqual([w for w in feat.Warnings if w != "AdjacentSideShared"], ref["warnings"])

    def test_defaults_and_properties(self):
        feat = self.scene.make()
        self.assertEqual(feat.Continuity, "G2")
        self.assertEqual(feat.Side, "V0")
        self.assertEqual(feat.MaxLevel, 5)
        self.assertEqual(feat.Tolerance, 1e-4)
        self.assertFalse(feat.AllowApproximation)
        self.assertEqual(feat.Side, "V0")
        for name in ("Level", "PolesAlong", "DegreeAlong", "IsApproximate", "MaxGap", "MaxAngle", "MaxCurvatureJump", "MaxMove", "Warnings"):
            self.assertIn("ReadOnly", feat.getPropertyStatus(name), name)
        self.assertEqual(feat.Shape.ShapeType, "Face")
        with self.assertRaises(ValueError):
            SM.makeMatchSurfaceFeature(self.doc, (self.scene.src, "Face1"), "W0", self.scene.target)

    def test_equals_the_api_for_each_continuity(self):
        for k in (0, 1, 2):
            feat = self.scene.make(k=k)
            self.check_equal(feat)
            self.assertGreater(feat.MaxMove, 0.0)

    def test_target_edge_with_and_without_its_face(self):
        plane = self.doc.addObject("Part::Feature", "Plane")
        plane.Shape = poly_face([V(0, 0, 20), V(40, 0, 20), V(40, -30, 20), V(0, -30, 20)])
        self.doc.recompute()
        e = edge_name(plane, pick_edge(plane.Shape.Faces[0], V(20, 0, 20)))
        bare = SM.makeMatchSurfaceFeature(self.doc, (self.scene.src, "Face1"), "V0", (plane, e))
        full = SM.makeMatchSurfaceFeature(self.doc, (self.scene.src, "Face1"), "V0", (plane, ["Face1", e]))
        self.doc.recompute()
        self.assertTrue(bare.isValid(), bare.getStatusString())
        self.assertTrue(same_surface(bare.Shape.Faces[0].Surface, full.Shape.Faces[0].Surface))
        edge_only = SM.makeMatchSurfaceFeature(self.doc, (self.scene.src, "Face1"), "V0", (self.scene.box, self.scene.edge))
        self.doc.recompute()
        self.assertFalse(edge_only.isValid())
        self.assertTrue(edge_only.getStatusString().startswith("AmbiguousFace"), edge_only.getStatusString())

    def test_each_side_matches_its_own_side(self):
        # a source whose V1 side is the one beside the box edge: the same net, reversed in v
        poles = rising_net().getPoles()
        P = [col[::-1] for col in poles]
        self.scene.src.Shape = net_surface(P).toShape()
        self.doc.recompute()
        feat = self.scene.make(side="V1")
        self.check_equal(feat)
        side0 = self.scene.make(side="V0")  # V0 is the far side: not on the box edge
        self.assertFalse(side0.isValid())

    def test_follows_its_inputs(self):
        feat = self.scene.make()
        self.scene.src.Placement = FreeCAD.Placement(V(3, 0, 0), FreeCAD.Rotation())
        self.doc.recompute()
        self.check_equal(feat)
        self.scene.box.Placement = FreeCAD.Placement(V(0, 0, 0.5), FreeCAD.Rotation())
        self.doc.recompute()
        self.assertTrue(feat.isValid(), feat.getStatusString())
        feat.Continuity = "G1"
        feat.MaxLevel = 2
        self.doc.recompute()
        self.check_equal(feat)
        feat.AllowApproximation = True
        self.doc.recompute()
        self.check_equal(feat)

    def test_global_placement_of_a_container(self):
        part = self.doc.addObject("App::Part", "Part")
        part.addObject(self.scene.src)
        part.Placement = FreeCAD.Placement(V(0, 0, 0), FreeCAD.Rotation(V(0, 0, 1), 0))
        self.doc.recompute()
        feat = SM.makeMatchSurfaceFeature(self.doc, (part, "Src.Face1"), "V0", self.scene.target)
        self.doc.recompute()
        self.check_equal(feat)
        self.assertAlmostEqual(feat.Shape.BoundBox.ZMin, 20.0, delta=0.5)

    def test_failures_keep_the_last_shape(self):
        feat = self.scene.make()
        good = feat.Shape.copy()
        feat.Tolerance = 0.0
        self.doc.recompute()
        self.assertFalse(feat.isValid())
        self.assertTrue(feat.getStatusString().startswith("InvalidTolerance"), feat.getStatusString())
        self.assertAlmostEqual(feat.Shape.Area, good.Area, delta=1e-9)

    def test_a_trimmed_source_is_refused(self):
        trimmed = self.doc.addObject("Part::Feature", "Trimmed")
        s = rising_net()
        trimmed.Shape = s.toShape().common(Part.makeBox(20, 40, 40, V(0, 0, 0)))
        self.doc.recompute()
        feat = SM.makeMatchSurfaceFeature(self.doc, (trimmed, "Face1"), "V0", self.scene.target)
        self.doc.recompute()
        self.assertFalse(feat.isValid())
        self.assertTrue(feat.getStatusString().startswith("TrimmedFace"), feat.getStatusString())

    def test_a_deleted_input_is_named(self):
        feat = self.scene.make()
        label = self.scene.box.Label
        self.doc.removeObject(self.scene.box.Name)
        self.doc.recompute()
        self.assertFalse(feat.isValid())
        self.assertIn("InputDeleted: %s " % label, feat.getStatusString())

    def test_save_and_reopen(self):
        feat = self.scene.make(k=1)
        shape = feat.Shape.copy()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "m.FCStd")
            self.doc.saveAs(path)
            other = FreeCAD.openDocument(path)
            try:
                back = other.getObject(feat.Name)
                self.assertTrue(same_surface(back.Shape.Faces[0].Surface, shape.Faces[0].Surface))
                other.recompute()
                self.assertTrue(back.isValid(), back.getStatusString())
                self.assertTrue(same_surface(back.Shape.Faces[0].Surface, shape.Faces[0].Surface))
                self.assertEqual(back.Continuity, "G1")
            finally:
                FreeCAD.closeDocument(other.Name)
            self.doc = FreeCAD.newDocument("G09MatchFeature")
            self.scene = Scene(self.doc)

    def test_a_copy_in_another_document_recomputes(self):
        feat = self.scene.make()
        dst = FreeCAD.newDocument("G09MatchCopy")
        try:
            dst.copyObject(feat, True)
            dst.recompute()
            copy = [o for o in dst.Objects if o.TypeId == feat.TypeId][-1]
            self.assertTrue(copy.isValid(), copy.getStatusString())
            self.assertTrue(same_surface(copy.Shape.Faces[0].Surface, feat.Shape.Faces[0].Surface))
        finally:
            FreeCAD.closeDocument(dst.Name)


def sewn_pair():
    """Two faces split from one parent surface and sewn along their shared edge (a shell)."""
    P = [[V(80 * i / 8.0, 2 + 7.5 * j, 20 + 1.5 * j + 2 * math.sin(1.1 * i + 0.9 * j + 0.5) * (j > 0)) for j in range(5)] for i in range(9)]
    par = net_surface(P)
    a, b = par.copy(), par.copy()
    a.segment(0, 0.5, 0, 1)
    b.segment(0.5, 1, 0, 1)
    shell = Part.makeShell([a.toShape(), b.toShape()]).copy()
    shell.sewShape()
    return shell


class TestAdjacentSideWarning(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument("G09MatchWarn")
        self.plane = self.doc.addObject("Part::Feature", "Plane")
        self.plane.Shape = poly_face([V(0, 0, 20), V(40, 0, 20), V(40, -30, 20), V(0, -30, 20)])
        self.edge = edge_name(self.plane, pick_edge(self.plane.Shape.Faces[0], V(20, 0, 20)))
        self.shell = self.doc.addObject("Part::Feature", "Shell")
        self.shell.Shape = sewn_pair()
        self.doc.recompute()

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def feature(self, source):
        feat = SM.makeMatchSurfaceFeature(self.doc, source, "V0", (self.plane, [ "Face1", self.edge]))
        self.doc.recompute()
        self.assertTrue(feat.isValid(), feat.getStatusString())
        return feat

    def split_objects(self):
        """The two faces as two separate objects."""
        objs = []
        for i, f in enumerate(self.shell.Shape.Faces):
            o = self.doc.addObject("Part::Feature", "Half%d" % (i + 1))
            o.Shape = f.copy()
            objs.append(o)
        self.doc.recompute()
        return objs

    def test_a_neighbour_in_the_same_object(self):
        self.assertIn("AdjacentSideShared", self.feature((self.shell, "Face1")).Warnings)

    def test_a_lone_face_has_none(self):
        a, b = self.split_objects()
        self.doc.removeObject(self.shell.Name)
        b.Visibility = False
        self.assertNotIn("AdjacentSideShared", self.feature((a, "Face1")).Warnings)

    def test_a_visible_neighbour_object_counts_and_a_hidden_one_does_not(self):
        a, b = self.split_objects()
        self.doc.removeObject(self.shell.Name)
        a.Visibility = False
        b.Visibility = True
        self.assertIn("AdjacentSideShared", self.feature((a, "Face1")).Warnings)
        b.Visibility = False
        self.doc.recompute()
        feat = self.doc.getObjectsByLabel("MatchSurface")[0]
        feat.touch()
        self.doc.recompute()
        self.assertNotIn("AdjacentSideShared", feat.Warnings)

    def test_the_warning_uses_global_coordinates(self):
        a, b = self.split_objects()
        self.doc.removeObject(self.shell.Name)
        part = self.doc.addObject("App::Part", "Part")
        part.addObject(a)
        part.addObject(b)
        part.Placement = FreeCAD.Placement(V(0, 0, 0), FreeCAD.Rotation())
        self.doc.recompute()
        feat = self.feature((part, "Half1.Face1"))
        self.assertIn("AdjacentSideShared", feat.Warnings)
        # moving one half away in its own frame breaks the sharing
        b.Placement = FreeCAD.Placement(V(0, 0, 10), FreeCAD.Rotation())
        self.doc.recompute()
        self.assertNotIn("AdjacentSideShared", feat.Warnings)
        self.assertTrue(feat.isValid(), feat.getStatusString())


class TestRepairR120(unittest.TestCase):
    def test_approximate_must_be_a_bool(self):
        s = Scene(FreeCAD.newDocument("G09R120"))
        try:
            face = PE.resolve_face((s.src, ["Face1"]))
            tface, tedge = SBS.resolve_edge_face(s.target)
            for bad in (1, 0, "yes", None, 1.0):
                with self.assertRaises(TypeError, msg=repr(bad)):
                    SM.matchSurface(face, "V0", tface, tedge, approximate=bad)
            SM.matchSurface(face, "V0", tface, tedge, approximate=False)
        finally:
            FreeCAD.closeDocument(s.doc.Name)
