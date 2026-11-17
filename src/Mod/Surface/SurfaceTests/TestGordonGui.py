# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI tests of the ``Surface_Gordon`` command and task panel (G09 slice 3a).

Run with ``FreeCAD -t TestSurfaceGui`` (needs the GUI binary). Public sources: W. J. Gordon, "Spline-blended
surface interpolation through curve networks", J. Math. Mech. 18(10), 1969; the FreeCAD task dialog API.
"""

import unittest

import FreeCAD
import FreeCADGui
import Part
from PySide import QtWidgets

import SurfaceGordon
from SurfaceTests.TestGordon import cylinder_network, hypar_network
from SurfaceTests.TestSurfaceAnalysisGui import _pump

V = FreeCAD.Vector
CMD = "Surface_Gordon"


class TestGordonGui(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        FreeCADGui.activateWorkbench("SurfaceWorkbench")
        _pump()

    def setUp(self):
        self.doc = FreeCAD.newDocument("G09Gordon")
        FreeCADGui.ActiveDocument = FreeCADGui.getDocument(self.doc.Name)
        _pump()
        FreeCADGui.Selection.clearSelection()
        profiles, guides = hypar_network()
        self.p = self._objects("P", profiles)
        self.g = self._objects("G", guides)
        self.doc.recompute()

    def tearDown(self):
        self._close()
        FreeCADGui.Selection.clearSelection()
        FreeCAD.closeDocument(self.doc.Name)

    # ---- helpers
    @property
    def mw(self):
        return FreeCADGui.getMainWindow()

    def _objects(self, name, curves):
        out = []
        for i, c in enumerate(curves):
            o = self.doc.addObject("Part::Feature", "%s%d" % (name, i))
            o.Shape = c.toShape()
            out.append(o)
        return out

    def _close(self):
        if FreeCADGui.Control.activeDialog():
            self._button("Cancel").click()
            _pump()

    def _button(self, text):
        for b in self.mw.findChildren(QtWidgets.QPushButton):
            if b.text().replace("&", "") == text and b.isVisible():
                return b
        self.fail("no %s button" % text)

    def _w(self, name):
        w = self.mw.findChild(QtWidgets.QWidget, name)
        self.assertIsNotNone(w, name)
        return w

    def _select(self, items):
        FreeCADGui.Selection.clearSelection()
        for obj, sub in items:
            FreeCADGui.Selection.addSelection(self.doc.Name, obj.Name, sub)
        _pump()

    def _open(self):
        FreeCADGui.Selection.clearSelection()
        FreeCADGui.runCommand(CMD)
        _pump()
        self.assertTrue(FreeCADGui.Control.activeDialog())

    def _add(self, which, items):
        self._select(items)
        self._w("G09_Gordon%s_Add" % which).click()
        _pump()

    def _fill(self):
        self._add("Profiles", [(o, "Edge1") for o in self.p])
        self._add("Guides", [(o, "Edge1") for o in self.g])

    def _status(self):
        return self._w("G09_GordonStatus").text()

    def _features(self):
        return [o for o in self.doc.Objects if o.Name.startswith("GordonSurface")]

    # ---- tests
    def test_command_attributes_and_placement(self):
        info = FreeCADGui.Command.get(CMD).getInfo()
        self.assertEqual(info["menuText"], "Gordon surface")
        self.assertEqual(info["pixmap"], "Surface_Gordon")
        self.assertTrue(info["toolTip"])
        self.assertTrue(info["statusTip"])
        self.assertFalse(info.get("shortcut"))
        wb = FreeCADGui.getWorkbench("SurfaceWorkbench")
        self.assertIn(CMD, wb.getToolbarItems()["Surface"])
        self.assertTrue(any(CMD in cmds for cmds in wb.getToolbarItems().values()))
        self.assertTrue(FreeCADGui.Command.get(CMD).isActive())
        self._open()
        self.assertFalse(FreeCADGui.Command.get(CMD).isActive())

    def test_add_and_remove(self):
        self._open()
        self._add("Profiles", [(self.p[0], "Edge1"), (self.p[1], "Edge1")])
        self.assertEqual(self._w("G09_GordonProfiles").count(), 2)
        self._add("Profiles", [(self.p[0], "Edge1")])  # no duplicates
        self.assertEqual(self._w("G09_GordonProfiles").count(), 2)
        self._add("Guides", [(self.g[0], "Edge1")])
        self.assertEqual(self._w("G09_GordonGuides").count(), 1)
        self.assertIn("2 profile", self._status())
        lw = self._w("G09_GordonProfiles")
        lw.item(0).setSelected(True)
        self._w("G09_GordonProfiles_Remove").click()
        _pump()
        self.assertEqual(lw.count(), 1)
        self.assertEqual(lw.item(0).text(), "%s.Edge1" % self.p[1].Label)

    def test_ok_creates_one_feature_in_one_transaction(self):
        self._open()
        self._fill()
        undo = self.doc.UndoCount
        self._button("OK").click()
        _pump()
        self.assertFalse(FreeCADGui.Control.activeDialog())
        feats = self._features()
        self.assertEqual(len(feats), 1)
        self.assertEqual(self.doc.UndoCount, undo + 1)
        self.assertEqual(self.doc.UndoNames[0], "Gordon surface")
        self.assertTrue(feats[0].isValid())
        ref = SurfaceGordon.makeGordonFeature(self.doc, [(o, "Edge1") for o in self.p], [(o, "Edge1") for o in self.g], "Ref")
        self.doc.recompute()
        s1, s2 = feats[0].Shape.Face1.Surface, ref.Shape.Face1.Surface
        u0, u1, v0, v1 = s1.bounds()
        for i in range(21):
            for j in range(21):
                u, v = u0 + (u1 - u0) * i / 20, v0 + (v1 - v0) * j / 20
                self.assertLess((s1.value(u, v) - s2.value(u, v)).Length, 1e-9)

    def test_failing_ok_keeps_panel_and_creates_nothing(self):
        self.g[1].Placement = FreeCAD.Placement(V(0, 0, 40), FreeCAD.Rotation())
        self.g[1].recompute()
        self.doc.recompute()
        self._open()
        self._fill()
        undo = self.doc.UndoCount
        count = len(self.doc.Objects)
        self._button("OK").click()
        _pump()
        self.assertTrue(FreeCADGui.Control.activeDialog())
        self.assertTrue(self._status().startswith("IntersectionFailed"), self._status())
        self.assertEqual(len(self.doc.Objects), count)
        self.assertEqual(self.doc.UndoCount, undo)

    def test_cancel_creates_nothing(self):
        self._open()
        self._fill()
        undo = self.doc.UndoCount
        count = len(self.doc.Objects)
        self._button("Cancel").click()
        _pump()
        self.assertFalse(FreeCADGui.Control.activeDialog())
        self.assertEqual(len(self.doc.Objects), count)
        self.assertEqual(self.doc.UndoCount, undo)
        self.assertFalse(self.doc.HasPendingTransaction)

    def test_edges_on_a_body_in_a_placed_part(self):
        profiles, guides = cylinder_network()
        part = self.doc.addObject("App::Part", "Holder")
        part.Placement = FreeCAD.Placement(V(10, 20, 30), FreeCAD.Rotation(V(0, 0, 1), 90))
        objs = self._objects("E", profiles + guides)
        for o in objs:
            part.addObject(o)
        self.doc.recompute()
        self._open()
        FreeCADGui.Selection.clearSelection()
        for o in objs[:4]:
            FreeCADGui.Selection.addSelection(self.doc.Name, part.Name, "%s.Edge1" % o.Name)
        _pump()
        self._w("G09_GordonProfiles_Add").click()
        FreeCADGui.Selection.clearSelection()
        for o in objs[4:]:
            FreeCADGui.Selection.addSelection(self.doc.Name, part.Name, "%s.Edge1" % o.Name)
        _pump()
        self._w("G09_GordonGuides_Add").click()
        _pump()
        self.assertEqual(self._w("G09_GordonProfiles").count(), 4)
        self.assertEqual(self._w("G09_GordonGuides").count(), 4)
        self._button("OK").click()
        _pump()
        feat = self._features()[0]
        self.assertTrue(feat.isValid(), feat.getStatusString())
        bb = feat.Shape.BoundBox
        # the quarter cylinder (x, y in 0..10) rotated 90 degrees about Z and moved by (10, 20, 30)
        self.assertAlmostEqual(bb.XMin, 10 - 10, places=5)
        self.assertAlmostEqual(bb.XMax, 10, places=5)
        self.assertAlmostEqual(bb.YMin, 20, places=5)
        self.assertAlmostEqual(bb.ZMin, 30, places=5)
        self.assertAlmostEqual(bb.ZMax, 60, places=5)

    def test_whole_edge_object_selection(self):
        line = self.doc.addObject("Part::Feature", "Whole")
        line.Shape = Part.LineSegment(V(0, 0, 0), V(1, 0, 0)).toShape()
        self._open()
        self._select([(line, "")])
        self._w("G09_GordonProfiles_Add").click()
        _pump()
        self.assertEqual(self._w("G09_GordonProfiles").count(), 1)

    def test_feature_survives_without_view_provider_proxy(self):
        feat = SurfaceGordon.makeGordonFeature(self.doc, [(o, "Edge1") for o in self.p], [(o, "Edge1") for o in self.g])
        self.doc.recompute()
        self.assertTrue(feat.isValid())
        self.assertGreater(feat.Shape.Area, 100)
        self.assertEqual(feat.ViewObject.Proxy.getIcon(), ":/icons/Surface_Gordon.svg")
