# SPDX-License-Identifier: LGPL-2.1-or-later
"""Entering edit mode and adding a reference dimension (G10 slice 4c, rendering half).

Entering edit mode used to write the whole Constraints list back to the sketch even when no constraint changed
visibility, which touched the sketch and redrew it; the add sequence of a reference dimension used to set the sketch up
more often than it needed.  These tests hold what the editor must still do: a sketch that is entered and left without a
change stays untouched, the rectangle is still fully constrained afterwards, and the add sequence leaves the same
geometry and degrees of freedom as a further solve of the same sketch.
"""

import FreeCAD
import Part
import Sketcher

from SketcherTests.GuiTestCase import SketcherGuiTestCase

V = FreeCAD.Vector


def build_rectangle(doc, name):
    sk = doc.addObject("Sketcher::SketchObject", name)
    pts = [V(0, 0, 0), V(40, 0, 0), V(40, 20, 0), V(0, 20, 0)]
    for i in range(4):
        sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]), False)
    for i in range(4):
        sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
    sk.addConstraint(Sketcher.Constraint("Horizontal", 0))
    sk.addConstraint(Sketcher.Constraint("Horizontal", 2))
    sk.addConstraint(Sketcher.Constraint("Vertical", 1))
    sk.addConstraint(Sketcher.Constraint("Vertical", 3))
    sk.addConstraint(Sketcher.Constraint("Coincident", 0, 1, -1, 1))
    sk.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 40.0))
    sk.addConstraint(Sketcher.Constraint("DistanceY", 1, 1, 1, 2, 20.0))
    return sk


class TestSketchEditEntryGui(SketcherGuiTestCase):
    def setUp(self):
        super().setUp()
        self.doc = FreeCAD.newDocument("EditEntry")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)
        super().tearDown()

    def test_enter_and_leave_without_change_leaves_the_sketch_untouched(self):
        sk = build_rectangle(self.doc, "Rect")
        self.doc.recompute()
        self.assertFalse(sk.isTouched())
        geometry = [(g.StartPoint, g.EndPoint) for g in sk.Geometry]
        import FreeCADGui

        FreeCADGui.ActiveDocument = FreeCADGui.getDocument(self.doc.Name)
        FreeCADGui.ActiveDocument.setEdit(sk.Name)
        self.assertFalse(sk.isTouched(), "entering edit mode rewrote the constraints of an unchanged sketch")
        FreeCADGui.ActiveDocument.resetEdit()
        self.assertEqual(sk.solve(), 0)
        self.assertEqual([(g.StartPoint, g.EndPoint) for g in sk.Geometry], geometry)

    def test_reference_dimension_sequence_matches_a_further_solve(self):
        sk = build_rectangle(self.doc, "Rect")
        self.doc.recompute()
        idx = sk.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 1, 1, 40.0))
        sk.setDriving(idx, False)
        self.doc.recompute()
        after_sequence = [(g.StartPoint, g.EndPoint) for g in sk.Geometry]
        dof_sequence = sk.solve()
        self.assertEqual(dof_sequence, 0)
        for _ in range(2):
            sk.solve()
        self.assertEqual([(g.StartPoint, g.EndPoint) for g in sk.Geometry], after_sequence)
        self.assertEqual(sk.ConstraintCount, 14)
        self.assertFalse(sk.Constraints[idx].Driving)
