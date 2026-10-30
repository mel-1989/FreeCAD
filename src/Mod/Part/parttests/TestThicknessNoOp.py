# SPDX-License-Identifier: LGPL-2.1-or-later

import unittest

import FreeCAD as App
import Part


def _filleted_box():
    box = Part.makeBox(10, 10, 10)
    shape = box.makeFillet(2.0, box.Edges)
    top = [f for f in shape.Faces if f.BoundBox.ZMin > 9.99 and f.BoundBox.XMin > 1.9]
    return shape, top


def _top_face_name(shape, top):
    for i, face in enumerate(shape.Faces):
        if face.isSame(top[0]):
            return "Face%d" % (i + 1)
    raise AssertionError("top face not found")


class TestThicknessNoOp(unittest.TestCase):
    """A thickness that OCCT cannot compute must fail, not hand back the unshelled solid.

    BRepOffsetAPI_MakeThickSolid reports success for a box with all edges filleted but returns
    the input solid unchanged (volume 907.705), which then looked like a valid shell.
    """

    def setUp(self):
        self.doc = App.newDocument("ThicknessNoOp")

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def test_filleted_box_inward_raises(self):
        shape, top = _filleted_box()
        for value in (-1.5, -2.0, -2.2):
            with self.subTest(value=value):
                with self.assertRaises(Exception):
                    shape.makeThickness(top, value, 1e-4, False, False, 0, 2)

    def test_filleted_box_outward_raises(self):
        shape, top = _filleted_box()
        with self.assertRaises(Exception):
            shape.makeThickness(top, 1.0, 1e-4, False, False, 0, 2)

    def test_filleted_box_feature_is_in_error(self):
        shape, top = _filleted_box()
        base = self.doc.addObject("Part::Feature", "Base")
        base.Shape = shape
        for value in (1.5, 2.0, 2.2):
            with self.subTest(value=value):
                thick = self.doc.addObject("Part::Thickness", "Thick")
                thick.Faces = (base, [_top_face_name(shape, top)])
                thick.Value = value
                thick.Mode = 0
                thick.Join = 1
                thick.Intersection = True
                thick.SelfIntersection = False
                self.doc.recompute()
                self.assertIn("Invalid", thick.State)

    def test_sphere_and_torus_outward_raise(self):
        # OCCT returns these inverted (volume -Vin); still a no-op
        for shape in (Part.makeSphere(8), Part.makeTorus(10, 3)):
            with self.subTest(shape=shape.Faces[0].Surface.TypeId):
                with self.assertRaises(Exception):
                    shape.makeThickness([shape.Faces[0]], 1, 1e-7, False, False, 0, 0)

    def test_cylinder_outward(self):
        cyl = Part.makeCylinder(5, 10)
        top = [f for f in cyl.Faces if f.BoundBox.ZMin > 9.99]
        shell = cyl.makeThickness(top, 1, 1e-4, False, False, 0, 2)
        self.assertTrue(shell.isValid())
        self.assertAlmostEqual(shell.Volume, 458.6725, places=3)

    def test_plain_box_inward(self):
        box = Part.makeBox(10, 10, 10)
        top = [f for f in box.Faces if f.BoundBox.ZMin > 9.99]
        shell = box.makeThickness(top, -1, 1e-4, False, False, 0, 2)
        self.assertTrue(shell.isValid())
        self.assertAlmostEqual(shell.Volume, 424.0, places=6)

    def test_plain_box_outward(self):
        box = Part.makeBox(10, 10, 10)
        top = [f for f in box.Faces if f.BoundBox.ZMin > 9.99]
        shell = box.makeThickness(top, 1, 1e-4, False, False, 0, 2)
        self.assertTrue(shell.isValid())
        self.assertAlmostEqual(shell.Volume, 584.0, places=6)

    def test_partly_filleted_box_arc_join(self):
        # OCCT solves this one (arc join), so it must still succeed
        box = Part.makeBox(10, 10, 10)
        shape = box.makeFillet(2.0, [e for e in box.Edges if e.BoundBox.ZMax < 1])
        top = [f for f in shape.Faces if f.BoundBox.ZMin > 9.99]
        shell = shape.makeThickness(top, -1, 1e-4, False, False, 0, 0)
        self.assertTrue(shell.isValid())
        self.assertLess(shell.Volume, shape.Volume - 100)

    def test_cylinder_shell(self):
        cyl = Part.makeCylinder(5, 10)
        top = [f for f in cyl.Faces if f.BoundBox.ZMin > 9.99]
        shell = cyl.makeThickness(top, -1, 1e-4, False, False, 0, 2)
        self.assertTrue(shell.isValid())
        self.assertAlmostEqual(shell.Volume, 3.141592653589793 * (25 * 10 - 16 * 9), places=3)

    def test_part_feature_box_ok(self):
        box = self.doc.addObject("Part::Box", "Box")
        thick = self.doc.addObject("Part::Thickness", "Thick")
        thick.Faces = (box, ["Face6"])
        thick.Value = -1
        thick.Mode = 0
        thick.Join = 1
        self.doc.recompute()
        self.assertNotIn("Invalid", thick.State)
        self.assertAlmostEqual(thick.Shape.Volume, 424.0, places=6)

    def test_partdesign_thickness_on_pad(self):
        body = self.doc.addObject("PartDesign::Body", "Body")
        box = body.newObject("PartDesign::AdditiveBox", "Box")
        box.Length = box.Width = box.Height = 10
        self.doc.recompute()
        top = [i + 1 for i, f in enumerate(box.Shape.Faces) if f.BoundBox.ZMin > 9.99][0]
        thick = body.newObject("PartDesign::Thickness", "Thick")
        thick.Base = (box, ["Face%d" % top])
        thick.Value = 1.0
        thick.Reversed = True
        self.doc.recompute()
        self.assertNotIn("Invalid", thick.State)
        self.assertAlmostEqual(thick.Shape.Volume, 424.0, places=6)
