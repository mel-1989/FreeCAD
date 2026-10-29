# SPDX-License-Identifier: LGPL-2.1-or-later

# Tests for DimensionAutoCorrect phase 2 and for dimensions that report broken references.
# Model: a 40 x 20 x 10 pad in a PartDesign body, a top-view DrawViewPart at scale 1 and dimensions on the top +Y edge
# (3D reference and 2D-only reference).  Edits insert or remove features upstream of the pad's edges.

import os
import tempfile
import FreeCAD
import Part
import Sketcher
import unittest
from FreeCAD import Vector as V
from .TechDrawTestUtilities import createPageWithSVGTemplate

W, H, L = 40.0, 20.0, 10.0


def _broken(dim):
    return "Invalid" in dim.State or "Error" in dim.State


def _line_edge(shape, direction, point, tol=1e-6):
    """Name of the shape edge that is a line along `direction` through `point` (its midpoint)."""
    d = V(*direction)
    for i, e in enumerate(shape.Edges):
        if type(e.Curve).__name__ != "Line" or abs(e.Curve.Direction.dot(d)) < 1 - 1e-9:
            continue
        mid = e.valueAt((e.FirstParameter + e.LastParameter) / 2)
        if (mid - V(*point)).Length < tol:
            return "Edge%d" % (i + 1)
    raise LookupError("no edge at %s" % (point,))


def _view_line(view, direction, point, tol=1e-3):
    d = V(*direction)
    i = 0
    while True:
        try:
            e = view.getEdgeByIndex(i)
        except Exception:
            raise LookupError("no view edge at %s" % (point,))
        if type(e.Curve).__name__ == "Line" and abs(e.Curve.Direction.dot(d)) > 1 - 1e-9:
            mid = e.valueAt((e.FirstParameter + e.LastParameter) / 2)
            if (mid - V(*point)).Length < tol:
                return "Edge%d" % i
        i += 1


class DimensionRescueTest(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument("TDRescueTest")
        self.body = self.doc.addObject("PartDesign::Body", "Body")
        self.sketch = self.body.newObject("Sketcher::SketchObject", "Sketch")
        self.sketch.AttachmentSupport = (self.body.Origin.OriginFeatures[3], [""])
        self.sketch.MapMode = "FlatFace"
        pts = [V(-W / 2, -H / 2, 0), V(W / 2, -H / 2, 0), V(W / 2, H / 2, 0), V(-W / 2, H / 2, 0)]
        for i in range(4):
            self.sketch.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]), False)
        for i in range(4):
            self.sketch.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
        self.pad = self.body.newObject("PartDesign::Pad", "Pad")
        self.pad.Profile = self.sketch
        self.pad.Length = L
        # a through hole after the pad, so that features inserted after the pad really are upstream of the tip
        self.holeSketch = self.body.newObject("Sketcher::SketchObject", "HoleSketch")
        self.holeSketch.AttachmentSupport = (self.body.Origin.OriginFeatures[3], [""])
        self.holeSketch.MapMode = "FlatFace"
        self.holeSketch.AttachmentOffset = FreeCAD.Placement(V(0, 0, L), FreeCAD.Rotation())
        self.holeSketch.setExpression("AttachmentOffset.Base.z", "Pad.Length")
        self.holeSketch.addGeometry(Part.Circle(V(-10, 0, 0), V(0, 0, 1), 2.0), False)
        self.hole = self.body.newObject("PartDesign::Pocket", "HolePocket")
        self.hole.Profile = self.holeSketch
        self.hole.Length = 2 * L
        self.doc.recompute()

        self.page = createPageWithSVGTemplate(self.doc)
        self.page.Scale = 1.0
        self.view = self.doc.addObject("TechDraw::DrawViewPart", "View")
        self.page.addView(self.view)
        self.view.Source = [self.body]
        self.view.Direction = V(0, 0, 1)
        self.view.X = 100
        self.view.Y = 100
        self.doc.recompute()

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    # -- helpers
    def dim3d(self, name="Dim3d"):
        edge = _line_edge(self.body.Shape, (1, 0, 0), (0, H / 2, L))
        dim = self.doc.addObject("TechDraw::DrawViewDimension", name)
        self.page.addView(dim)
        dim.Type = "Distance"
        dim.References2D = [(self.view, "")]
        dim.References3D = [(self.body, edge)]
        self.doc.recompute()
        self.assertFalse(_broken(dim))
        self.assertAlmostEqual(dim.getRawValue(), W, places=3)
        return dim

    def dim2d(self, name="Dim2d"):
        edge = _view_line(self.view, (1, 0, 0), (0, H / 2, 0))
        dim = self.doc.addObject("TechDraw::DrawViewDimension", name)
        self.page.addView(dim)
        dim.Type = "Distance"
        dim.References2D = [(self.view, edge)]
        self.doc.recompute()
        self.assertFalse(_broken(dim))
        self.assertAlmostEqual(dim.getRawValue(), W, places=3)
        return dim

    def insertDressUp(self, kind):
        """Fillet or chamfer (3 mm) of the four vertical edges right after the pad, inside a transaction."""
        edges = [
            _line_edge(self.pad.Shape, (0, 0, 1), (sx * W / 2, sy * H / 2, L / 2))
            for sx in (-1, 1)
            for sy in (-1, 1)
        ]
        self.doc.openTransaction("insert " + kind)
        feat = self.doc.addObject("PartDesign::" + kind, kind)
        self.body.insertObject(feat, self.pad, True)
        feat.Base = (self.pad, edges)
        if kind == "Fillet":
            feat.Radius = 3.0
        else:
            feat.Size = 3.0
        self.doc.recompute()
        self.doc.commitTransaction()
        return feat

    def insertFillet(self):
        """Fillet the four vertical edges (r = 3) right after the pad: the referenced top edge shortens from 40 to 34."""
        edges = [
            _line_edge(self.pad.Shape, (0, 0, 1), (sx * W / 2, sy * H / 2, L / 2))
            for sx in (-1, 1)
            for sy in (-1, 1)
        ]
        fillet = self.doc.addObject("PartDesign::Fillet", "Fillet")
        self.body.insertObject(fillet, self.pad, True)
        fillet.Base = (self.pad, edges)
        fillet.Radius = 3.0
        self.doc.recompute()
        return fillet

    def insertNotch(self, x0=-3.0, x1=3.0):
        """A through pocket across the top +Y edge (by default the middle: the edge splits into two pieces)."""
        sk = self.doc.addObject("Sketcher::SketchObject", "NotchSketch")
        self.body.insertObject(sk, self.pad, True)
        sk.AttachmentSupport = (self.body.Origin.OriginFeatures[3], [""])
        sk.MapMode = "FlatFace"
        sk.AttachmentOffset = FreeCAD.Placement(V(0, 0, L), FreeCAD.Rotation())
        y0, y1 = H / 2 - 4, H / 2 + 1
        pts = [V(x0, y0, 0), V(x1, y0, 0), V(x1, y1, 0), V(x0, y1, 0)]
        for i in range(4):
            sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]), False)
        for i in range(4):
            sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
        pocket = self.doc.addObject("PartDesign::Pocket", "NotchPocket")
        self.body.insertObject(pocket, sk, True)
        pocket.Profile = sk
        pocket.Length = 2 * L
        self.doc.recompute()
        return pocket

    # -- tests
    def testParametricEditStaysQuiet(self):
        """Lengthening the pad moves no top edge in the view; both dimensions stay valid and keep their value."""
        d3, d2 = self.dim3d(), self.dim2d()
        self.pad.Length = 18.0
        self.doc.recompute()
        for dim in (d3, d2):
            self.assertFalse(_broken(dim), dim.Name)
            self.assertAlmostEqual(dim.getRawValue(), W, places=3)

    def testFilletRescuesCollinear3dEdge(self):
        """The 3D reference lost its element ('?'), the shortened collinear top edge is the only candidate."""
        dim = self.dim3d()
        self.insertFillet()
        self.assertFalse(_broken(dim))
        self.assertAlmostEqual(dim.getRawValue(), W - 6.0, places=3)
        self.assertFalse(dim.References3D[0][1][0].startswith("?"))

    def testFilletRescuesCollinear2dEdge(self):
        """The 2D index now names another edge; the unique collinear overlapping view edge replaces it."""
        dim = self.dim2d()
        self.insertFillet()
        self.assertFalse(_broken(dim))
        self.assertAlmostEqual(dim.getRawValue(), W - 6.0, places=3)

    def testSplitEdgeIsReportedBroken3d(self):
        """The edge is split in two: two collinear candidates, so no guess; the dimension reports the break."""
        dim = self.dim3d()
        self.insertNotch()
        self.assertTrue(_broken(dim), "dimension still %s with value %s" % (dim.State, dim.getRawValue()))

    def testSplitEdgeIsReportedBroken2d(self):
        dim = self.dim2d()
        self.insertNotch()
        self.assertTrue(_broken(dim), "dimension still %s with value %s" % (dim.State, dim.getRawValue()))

    def testMissing3dElementIsNotUpToDate(self):
        """A 3D edge that is gone and has no stand-in must not stay Up-to-date with a stale value."""
        circle = None
        for i, e in enumerate(self.body.Shape.Edges):
            if type(e.Curve).__name__ == "Circle" and abs(e.Curve.Radius - 2.0) < 1e-6 and abs(e.Curve.Center.z - L) < 1e-6:
                circle = "Edge%d" % (i + 1)
        self.assertIsNotNone(circle)
        dim = self.doc.addObject("TechDraw::DrawViewDimension", "DimHole")
        self.page.addView(dim)
        dim.Type = "Radius"
        dim.References2D = [(self.view, "")]
        dim.References3D = [(self.body, circle)]
        self.doc.recompute()
        self.assertFalse(_broken(dim))
        self.assertAlmostEqual(dim.getRawValue(), 2.0, places=3)
        self.hole.Suppressed = True
        self.doc.recompute()
        self.assertTrue(_broken(dim), "dimension still %s with value %s" % (dim.State, dim.getRawValue()))

    def _undoCase(self, kind):
        self.doc.UndoMode = 1
        dim = self.dim2d()
        self.insertDressUp(kind)
        self.assertFalse(_broken(dim))
        self.assertAlmostEqual(dim.getRawValue(), W - 6.0, places=3)
        self.doc.undo()
        self.doc.recompute()
        self.assertFalse(_broken(dim), dim.State)
        self.assertAlmostEqual(dim.getRawValue(), W, places=3)
        self.doc.recompute()
        self.assertFalse(_broken(dim), dim.State)
        self.assertAlmostEqual(dim.getRawValue(), W, places=3)
        self.doc.redo()
        self.doc.recompute()
        self.assertFalse(_broken(dim), dim.State)
        self.assertAlmostEqual(dim.getRawValue(), W - 6.0, places=3)

    def testUndoInsertedFillet2d(self):
        """Undo restores References2D together with SavedGeometry; the dimension is the base's 40 and Up-to-date."""
        self._undoCase("Fillet")

    def testUndoInsertedChamfer2d(self):
        self._undoCase("Chamfer")

    def testShortenedAtOneEndIsReported3d(self):
        """The top edge loses its +X end: a different extent and midpoint, so it is a different element."""
        dim = self.dim3d()
        self.insertNotch(x0=16.0, x1=21.0)
        self.assertTrue(_broken(dim), "dimension still %s with value %s" % (dim.State, dim.getRawValue()))

    def testFragmentMergedBackIsReported3d(self):
        """A dimension on one fragment of a split edge; the split goes away and the whole edge is not that fragment."""
        notch = self.insertNotch()
        edge = _line_edge(self.body.Shape, (1, 0, 0), (-11.5, H / 2, L))
        dim = self.doc.addObject("TechDraw::DrawViewDimension", "DimFrag")
        self.page.addView(dim)
        dim.Type = "Distance"
        dim.References2D = [(self.view, "")]
        dim.References3D = [(self.body, edge)]
        self.doc.recompute()
        self.assertFalse(_broken(dim))
        self.assertAlmostEqual(dim.getRawValue(), 17.0, places=3)
        notch.Suppressed = True
        self.doc.recompute()
        self.assertTrue(_broken(dim), "dimension still %s with value %s" % (dim.State, dim.getRawValue()))

    def testUnmarkedStaleSavedGeometryIsIgnored(self):
        """A file whose SavedGeometry the fork did not write (no trusted marker) behaves like stock on reopen,
        even if the saved shape is stale; the saved geometry is then refreshed and marked."""
        dim = self.dim2d()
        self.assertTrue(dim.SavedGeometryTrusted)
        dim.SavedGeometry = [Part.makeLine(V(-20, -10, 0), V(-20, 10, 0))]  # a stale, different edge
        dim.SavedGeometryTrusted = False
        path = os.path.join(tempfile.mkdtemp(prefix="tdrescue"), "stale.FCStd")
        self.doc.saveAs(path)
        FreeCAD.closeDocument(self.doc.Name)
        self.doc = FreeCAD.openDocument(path)
        dim = self.doc.getObject("Dim2d")
        for o in self.doc.Objects:
            o.touch()
        self.doc.recompute()
        self.assertFalse(_broken(dim), dim.State)
        self.assertAlmostEqual(dim.getRawValue(), W, places=3)
        self.assertTrue(dim.SavedGeometryTrusted)
        self.assertAlmostEqual(dim.SavedGeometry[0].Length, W, places=3)

    def testInsertThenDeleteFilletReturns(self):
        """A rescued 2D reference follows the edge back when the fillet is deleted again."""
        dim = self.dim2d()
        fillet = self.insertFillet()
        self.assertAlmostEqual(dim.getRawValue(), W - 6.0, places=3)
        self.body.removeObject(fillet)
        self.doc.removeObject(fillet.Name)
        self.doc.recompute()
        self.assertFalse(_broken(dim))
        self.assertAlmostEqual(dim.getRawValue(), W, places=3)


if __name__ == "__main__":
    unittest.main()
