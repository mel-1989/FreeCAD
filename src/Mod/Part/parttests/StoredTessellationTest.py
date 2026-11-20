# SPDX-License-Identifier: LGPL-2.1-or-later

"""Tests for the triangulation stored with a Part shape (G05 slice 3a): the shape is saved as
BinTools with its triangles when the preference SaveTessellation is on, as before when it is off,
and a shape without triangles is saved as before in either case."""

import os
import shutil
import tempfile
import unittest
import zipfile

import FreeCAD
import Part

PREF = "User parameter:BaseApp/Preferences/Mod/Part/General"


def _entries(path):
    with zipfile.ZipFile(path) as z:
        return {i.filename: i.file_size for i in z.infolist()}


class StoredTessellationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.grp = FreeCAD.ParamGet(PREF)
        self.had = "SaveTessellation" in self.grp.GetBools()
        self.old = self.grp.GetBool("SaveTessellation", True)
        self.doc = FreeCAD.newDocument("StoredTessellationTest")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)
        if self.had:
            self.grp.SetBool("SaveTessellation", self.old)
        else:
            self.grp.RemBool("SaveTessellation")
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _meshed_object(self):
        obj = self.doc.addObject("Part::Feature", "Meshed")
        shape = Part.makeCylinder(5, 20)
        shape.tessellate(0.05)  # leaves the triangulation on the faces of the shared TShape
        obj.Shape = shape
        return obj

    def _save(self, name):
        path = os.path.join(self.tmp, name)
        self.doc.saveAs(path)
        return path

    def testRoundTripWithTriangles(self):
        self.grp.SetBool("SaveTessellation", True)
        obj = self._meshed_object()
        volume, area, faces = obj.Shape.Volume, obj.Shape.Area, len(obj.Shape.Faces)
        path = self._save("with.FCStd")
        names = _entries(path)
        self.assertTrue(any(n.endswith(".bin") for n in names), names)
        self.assertFalse(any(n.endswith(".brp") for n in names), names)
        FreeCAD.closeDocument(self.doc.Name)
        self.doc = FreeCAD.openDocument(path)
        back = self.doc.getObject("Meshed").Shape
        self.assertTrue(back.isValid())
        self.assertEqual(len(back.Faces), faces)
        self.assertAlmostEqual(back.Volume, volume, places=6)
        self.assertAlmostEqual(back.Area, area, places=6)

    def testPreferenceOffWritesNoTriangles(self):
        self._meshed_object()
        self.grp.SetBool("SaveTessellation", True)
        on = self._save("on.FCStd")
        self.grp.SetBool("SaveTessellation", False)
        off = self._save("off.FCStd")
        names = _entries(off)
        self.assertTrue(any(n.endswith(".brp") for n in names), names)
        self.assertFalse(any(n.endswith(".bin") for n in names), names)
        with zipfile.ZipFile(off) as z:
            brp = [n for n in names if n.endswith(".brp")][0]
            data = z.read(brp)
        self.assertIn(b"CASCADE Topology", data[:40])
        self.assertIn(b"Triangulations 0", data)
        self.assertGreater(
            max(s for n, s in _entries(on).items() if n.endswith(".bin")), names[brp]
        )

    def testShapeWithoutTrianglesIsWrittenAsBefore(self):
        self.grp.SetBool("SaveTessellation", True)
        obj = self.doc.addObject("Part::Feature", "Plain")
        obj.Shape = Part.makeBox(1, 2, 3)
        names = _entries(self._save("plain.FCStd"))
        self.assertTrue(any(n.endswith(".brp") for n in names), names)
        self.assertFalse(any(n.endswith(".bin") for n in names), names)
