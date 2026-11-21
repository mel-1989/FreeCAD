# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI tests for reusing stored triangles on open (G05 slice 3a): a document saved by the GUI opens
without meshing, and every change that makes the stored triangles untrustworthy falls back to the
clean and mesh of before. PartGui.tessellationCacheStats() reports which path each object took."""

import os
import shutil
import tempfile
import unittest
import zipfile

import FreeCAD
import FreeCADGui
import Part
import PartGui

PREF = "User parameter:BaseApp/Preferences/Mod/Part/General"
NAMES = ["Box", "Cyl", "Sph"]


def _make(doc):
    doc.addObject("Part::Box", "Box")
    doc.addObject("Part::Cylinder", "Cyl")
    doc.addObject("Part::Sphere", "Sph")
    doc.recompute()


def _stats(doc):
    s = PartGui.tessellationCacheStats()
    prefix = doc.Name + "."
    return (
        sorted(n[len(prefix) :] for n in s["used"] if n.startswith(prefix)),
        sorted(n[len(prefix) :] for n in s["meshed"] if n.startswith(prefix)),
    )


class StoredTessellationGuiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.grp = FreeCAD.ParamGet(PREF)
        self.had = "SaveTessellation" in self.grp.GetBools()
        self.old = self.grp.GetBool("SaveTessellation", True)
        self.grp.SetBool("SaveTessellation", True)
        self.doc = FreeCAD.newDocument("StoredTessGui")
        _make(self.doc)
        self.path = os.path.join(self.tmp, "t.FCStd")
        self.doc.saveAs(self.path)
        FreeCAD.closeDocument(self.doc.Name)
        self.doc = None

    def tearDown(self):
        if self.doc is not None:
            FreeCAD.closeDocument(self.doc.Name)
        if self.had:
            self.grp.SetBool("SaveTessellation", self.old)
        else:
            self.grp.RemBool("SaveTessellation")
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _open(self, path=None):
        PartGui.resetTessellationCacheStats()
        self.doc = FreeCAD.openDocument(path or self.path)
        return self.doc

    def _arrays(self, name):
        from pivy import coin

        vp = self.doc.getObject(name).ViewObject
        sa = coin.SoSearchAction()
        sa.setType(coin.SoCoordinate3.getClassTypeId())
        sa.setInterest(coin.SoSearchAction.FIRST)
        sa.apply(vp.RootNode)
        pts = sa.getPath().getTail().point.getValues()
        return [tuple(p) for p in pts]

    def testSkipOnOpen(self):
        doc = self._open()
        used, meshed = _stats(doc)
        self.assertEqual(used, sorted(NAMES))
        self.assertEqual(meshed, [])

    def testSkippedMeshEqualsFreshMesh(self):
        doc = self._open()
        self.assertEqual(_stats(doc), (sorted(NAMES), []))
        stored = {n: self._arrays(n) for n in NAMES}
        # force the clean and mesh of the same shapes: another deviation, then back
        for n in NAMES:
            vp = doc.getObject(n).ViewObject
            vp.Deviation = 0.9
            vp.Deviation = 0.2
        used, meshed = _stats(doc)
        self.assertEqual(meshed.count("Box"), 2)
        for n in NAMES:
            self.assertEqual(stored[n], self._arrays(n))

    def testFallbackOnParameterChange(self):
        doc = self._open()
        PartGui.resetTessellationCacheStats()
        doc.getObject("Cyl").ViewObject.Deviation = 0.9
        self.assertEqual(_stats(doc), ([], ["Cyl"]))
        PartGui.resetTessellationCacheStats()
        doc.getObject("Box").ViewObject.AngularDeflection = 10
        self.assertEqual(_stats(doc), ([], ["Box"]))

    def testFallbackOnRecompute(self):
        doc = self._open()
        PartGui.resetTessellationCacheStats()
        doc.getObject("Sph").Radius = 7
        doc.recompute()
        used, meshed = _stats(doc)
        self.assertEqual(meshed, ["Sph"])
        self.assertEqual(used, [])

    def testPreferenceOffKeepsKeyOutOfGuiDocument(self):
        self.grp.SetBool("SaveTessellation", False)
        doc = FreeCAD.newDocument("StoredTessOff")
        try:
            _make(doc)
            path = os.path.join(self.tmp, "off.FCStd")
            doc.saveAs(path)
        finally:
            FreeCAD.closeDocument(doc.Name)
        with zipfile.ZipFile(path) as z:
            self.assertNotIn(b"TessellationKey", z.read("GuiDocument.xml"))
            self.assertFalse(any(n.endswith(".bin") for n in z.namelist()))
        with zipfile.ZipFile(self.path) as z:
            self.assertIn(b"TessellationKey", z.read("GuiDocument.xml"))

    def testFileWithoutTrianglesIsMeshed(self):
        # a stale key (kept) with the triangles of the file removed: never skipped
        with zipfile.ZipFile(self.path) as zin:
            gui = zin.read("GuiDocument.xml")
        plain = os.path.join(self.tmp, "stripped.FCStd")
        self.grp.SetBool("SaveTessellation", False)
        doc = FreeCAD.openDocument(self.path)
        doc.saveAs(plain)
        FreeCAD.closeDocument(doc.Name)
        with zipfile.ZipFile(plain) as zin, zipfile.ZipFile(
            os.path.join(self.tmp, "mixed.FCStd"), "w"
        ) as zout:
            for item in zin.infolist():
                data = gui if item.filename == "GuiDocument.xml" else zin.read(item.filename)
                zout.writestr(item.filename, data)
        self.doc = self._open(os.path.join(self.tmp, "mixed.FCStd"))
        self.assertEqual(_stats(self.doc), ([], sorted(NAMES)))

    def testRequestedDeflectionIsStocks(self):
        # a shape already carrying a triangulation gets the deflection stock computes: bounds
        # taken with the triangulation (tighter than the B-spline pole hull here), so the same
        # surface meshes finer than a bare copy of it (162 against 97 points on fork/main)
        from pivy import coin

        V = FreeCAD.Vector
        poles = [[V(10 * i, 10 * j, 80 if i in (1, 2) and j in (1, 2) else 0) for j in range(4)] for i in range(4)]
        mults = [4, 4]
        knots = [0.0, 1.0]

        def points(name, triangulated):
            surface = Part.BSplineSurface()
            surface.buildFromPolesMultsKnots(poles, mults, mults, knots, knots, False, False, 3, 3)
            shape = surface.toShape()
            if triangulated:
                shape.tessellate(0.1)
            doc = FreeCAD.newDocument("StoredTessDefl" + name)
            try:
                obj = doc.addObject("Part::Feature", name)
                obj.ViewObject.Deviation = 2.0
                obj.ViewObject.AngularDeflection = 180
                obj.ViewObject.Visibility = True
                obj.Shape = shape
                doc.recompute()
                sa = coin.SoSearchAction()
                sa.setType(coin.SoCoordinate3.getClassTypeId())
                sa.setInterest(coin.SoSearchAction.FIRST)
                sa.apply(obj.ViewObject.RootNode)
                return sa.getPath().getTail().point.getNum()
            finally:
                FreeCAD.closeDocument(doc.Name)

        self.assertEqual(points("Tri", True), 162)
        self.assertEqual(points("Bare", False), 97)

    def testFacelessShapeIsNotDeferred(self):
        # A shape without faces (a wire, like a sketch) is vacuously "complete"; it must mesh during
        # the restore as stock does, not wait for the key in GuiDocument.xml, or a Body's origin
        # is sized before the sketch is drawn. "A" carries triangles with a stale key, so it is
        # deferred and meshed afresh after the key is read: "W" must be meshed before it.
        import re

        doc = FreeCAD.newDocument("StoredTessWire")
        try:
            doc.addObject("Part::Feature", "A").Shape = Part.makeBox(5, 5, 5)
            V = FreeCAD.Vector
            wire = Part.makePolygon([V(0, 0, 0), V(40, 0, 0), V(40, 30, 0), V(0, 0, 0)])
            doc.addObject("Part::Feature", "W").Shape = wire
            doc.recompute()
            path = os.path.join(self.tmp, "wire.FCStd")
            doc.saveAs(path)
        finally:
            FreeCAD.closeDocument(doc.Name)
        stale = os.path.join(self.tmp, "wire-stale.FCStd")
        with zipfile.ZipFile(path) as zin, zipfile.ZipFile(stale, "w") as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == "GuiDocument.xml":
                    data, n = re.subn(
                        rb'(name="TessellationKey".*?value=")[^"]*"', rb'\1stale"', data, flags=re.S
                    )
                    self.assertGreaterEqual(n, 1)
                zout.writestr(item.filename, data)
        self.doc = self._open(stale)
        prefix = self.doc.Name + "."
        meshed = [n[len(prefix) :] for n in PartGui.tessellationCacheStats()["meshed"] if n.startswith(prefix)]
        self.assertEqual(meshed, ["W", "A"])
