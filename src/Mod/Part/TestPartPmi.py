# SPDX-License-Identifier: LGPL-2.1-or-later

# **************************************************************************
#   Tests for the PMI document objects (G12 slice 1b-i): Part::PmiDimension, *
#   Part::PmiTolerance, Part::PmiDatum and Part::PmiGroup.                   *
# **************************************************************************

import os
import tempfile
import unittest

import FreeCAD as App


class TestPartPmi(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("PartPmi")
        self.box = self.doc.addObject("Part::Box", "Box")
        self.doc.recompute()

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def _tol(self):
        tol = self.doc.addObject("Part::PmiTolerance", "Flat")
        tol.PmiType = "Flatness"
        tol.Value = 0.1
        tol.Modifiers = ["FREE_STATE"]
        tol.DatumReferences = ["A", "B(MAXIMUM_MATERIAL_REQUIREMENT)"]
        tol.StepFaceIds = [11, 12]
        tol.RawAttributes = {"tol.type": "Flatness", "x": "y"}
        tol.KernelLoss = ["lost: example reason", "corrected: another"]
        tol.StepEntityName = "flatness"
        tol.References = [(self.box, ["Face1"])]
        return tol

    def test_create_and_set(self):
        tol = self._tol()
        dim = self.doc.addObject("Part::PmiDimension", "Dim")
        dim.PmiType = "Size_Diameter"
        dim.Value = 10.0
        dim.HasBounds = True
        dim.BoundsKind = "plus_minus"
        dim.UpperBound = 0.1
        dim.LowerBound = -0.2
        dat = self.doc.addObject("Part::PmiDatum", "Dat")
        dat.Letter = "A"
        dat.Position = 1
        tol.Datums = [dat]
        grp = self.doc.addObject("Part::PmiGroup", "PMI")
        grp.addObjects([tol, dim, dat])
        self.doc.recompute()
        self.assertEqual([o.Name for o in grp.Group], ["Flat", "Dim", "Dat"])
        self.assertTrue(tol.isDerivedFrom("App::DocumentObject"))
        self.assertFalse(hasattr(tol, "Shape"))
        for o in self.doc.Objects:
            self.assertNotIn("Invalid", o.State)

    def test_save_and_reload(self):
        tol = self._tol()
        d = tempfile.mkdtemp(prefix="pmi")
        path = os.path.join(d, "t.FCStd")
        self.doc.saveAs(path)
        name = self.doc.Name
        App.closeDocument(name)
        self.doc = App.openDocument(path)
        tol = self.doc.getObject("Flat")
        self.assertEqual(tol.PmiType, "Flatness")
        self.assertAlmostEqual(tol.Value, 0.1)
        self.assertEqual(list(tol.Modifiers), ["FREE_STATE"])
        self.assertEqual(list(tol.DatumReferences), ["A", "B(MAXIMUM_MATERIAL_REQUIREMENT)"])
        self.assertEqual(list(tol.StepFaceIds), [11, 12])
        self.assertEqual(dict(tol.RawAttributes)["x"], "y")
        self.assertEqual(list(tol.KernelLoss), ["lost: example reason", "corrected: another"])
        self.assertEqual(tol.StepEntityName, "flatness")
        self.assertEqual(tol.References[0][0].Name, "Box")
        self.assertEqual(list(tol.References[0][1]), ["Face1"])
        self.doc.recompute()
        os.remove(path)
        os.rmdir(d)

    def test_face_link_survives_recompute_and_edit(self):
        tol = self._tol()
        area = self.box.Shape.getElement("Face1").Area
        self.doc.recompute()
        self.assertEqual(list(tol.References[0][1]), ["Face1"])
        self.box.Length = 25.0
        self.box.Height = 30.0
        self.doc.recompute()
        sub = tol.References[0][1][0]
        self.assertEqual(sub, "Face1")
        face = tol.References[0][0].Shape.getElement(sub)
        self.assertEqual(face.ShapeType, "Face")
        # Face1 of a box is the x = 0 face: width times height
        self.assertAlmostEqual(face.Area, self.box.Width.Value * 30.0, places=6)
        self.assertNotAlmostEqual(face.Area, area, places=3)
