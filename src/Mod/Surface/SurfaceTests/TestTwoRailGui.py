# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI tests of the ``Surface_TwoRailSweep`` command and task panel (G09 slice 3b).

Run with ``FreeCAD -t TestSurfaceGui`` (needs the GUI binary). Public sources: L. Piegl and W. Tiller, *The
NURBS Book*, 2nd ed., 1997 (swept surfaces); the FreeCAD task dialog API.
"""

import unittest

import FreeCAD
import FreeCADGui
import Part
from PySide import QtWidgets

import SurfaceTwoRail
from SurfaceTests.TestSurfaceAnalysisGui import _pump
from SurfaceTests.TestTwoRail import cylinder_rails, half_circle, cone_rails

V = FreeCAD.Vector
CMD = "Surface_TwoRailSweep"


class TestTwoRailGui(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        FreeCADGui.activateWorkbench("SurfaceWorkbench")
        _pump()

    @classmethod
    def tearDownClass(cls):
        # no task dialog may outlive this class: the classes that run after it need the task view free
        if FreeCADGui.Control.activeDialog():
            FreeCADGui.Control.closeDialog()
        _pump()

    def setUp(self):
        self._close()
        self.doc = FreeCAD.newDocument("G09TwoRail")
        FreeCADGui.ActiveDocument = FreeCADGui.getDocument(self.doc.Name)
        _pump()
        FreeCADGui.Selection.clearSelection()
        r1, r2 = cylinder_rails()
        self.prof = self._object("Prof", half_circle())
        self.a = self._object("RailA", r1)
        self.b = self._object("RailB", r2)
        self.doc.recompute()

    def tearDown(self):
        self._close()
        FreeCADGui.Selection.clearSelection()
        FreeCAD.closeDocument(self.doc.Name)

    # ---- helpers
    @property
    def mw(self):
        return FreeCADGui.getMainWindow()

    def _object(self, name, curve):
        o = self.doc.addObject("Part::Feature", name)
        o.Shape = curve.toShape()
        return o

    def _close(self):
        if FreeCADGui.Control.activeDialog():
            for b in self.mw.findChildren(QtWidgets.QPushButton):
                if b.text().replace("&", "") == "Cancel" and b.isVisible():
                    b.click()
                    break
            _pump()
        if FreeCADGui.Control.activeDialog():
            FreeCADGui.Control.closeDialog()
        for _ in range(50):  # the dialog goes at once, but its widgets may take another event-loop turn
            if not FreeCADGui.Control.activeDialog():
                break
            _pump(1)

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

    def _set(self, key, items):
        self._select(items)
        self._w("G09_TwoRail_%s_Set" % key).click()
        _pump()

    def _fill(self):
        self._set("Profile", [(self.prof, "Edge1")])
        self._set("Rail1", [(self.a, "Edge1")])
        self._set("Rail2", [(self.b, "Edge1")])

    def _status(self):
        return self._w("G09_TwoRailStatus").text()

    def _features(self):
        return [o for o in self.doc.Objects if o.Name.startswith("TwoRailSweep")]

    # ---- tests
    def test_command_attributes_and_placement(self):
        info = FreeCADGui.Command.get(CMD).getInfo()
        self.assertEqual(info["menuText"], "Two-rail sweep")
        self.assertEqual(info["pixmap"], "Surface_TwoRailSweep")
        self.assertTrue(info["toolTip"])
        self.assertTrue(info["statusTip"])
        self.assertFalse(info.get("shortcut"))
        wb = FreeCADGui.getWorkbench("SurfaceWorkbench")
        bar = wb.getToolbarItems()["Surface"]
        self.assertIn(CMD, bar)
        self.assertEqual(bar.index(CMD), bar.index("Surface_Gordon") + 1)
        self.assertTrue(FreeCADGui.Command.get(CMD).isActive())
        self._open()
        self.assertFalse(FreeCADGui.Command.get(CMD).isActive())

    def test_set_from_selection_needs_exactly_one_edge(self):
        self._open()
        self._set("Rail1", [(self.a, "Edge1"), (self.b, "Edge1")])
        self.assertEqual(self._w("G09_TwoRail_Rail1").text(), "")
        self.assertTrue(self._status().startswith("InvalidInput"), self._status())
        self._set("Rail1", [(self.a, "Edge1")])
        self.assertEqual(self._w("G09_TwoRail_Rail1").text(), "%s.Edge1" % self.a.Label)

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
        self.assertEqual(self.doc.UndoNames[0], "Two-rail sweep")
        self.assertTrue(feats[0].isValid())
        ref = SurfaceTwoRail.makeTwoRailFeature(self.doc, (self.prof, "Edge1"), (self.a, "Edge1"), (self.b, "Edge1"), "Ref")
        self.doc.recompute()
        s1, s2 = feats[0].Shape.Face1.Surface, ref.Shape.Face1.Surface
        u0, u1, v0, v1 = s1.bounds()
        for i in range(21):
            for j in range(21):
                u, v = u0 + (u1 - u0) * i / 20, v0 + (v1 - v0) * j / 20
                self.assertLess((s1.value(u, v) - s2.value(u, v)).Length, 1e-9)

    def test_scaling_choice_reaches_the_feature(self):
        c1, c2 = cone_rails()
        self.a.Shape, self.b.Shape = c1.toShape(), c2.toShape()
        self.doc.recompute()
        self._open()
        self._fill()
        self._w("G09_TwoRail_Scaling").setCurrentText("Chord")
        self._button("OK").click()
        _pump()
        feat = self._features()[0]
        self.assertEqual(feat.Scaling, "Chord")
        top = max(feat.Shape.Faces[0].Surface.value(u, v).z for u in (0.0, 0.5, 1.0) for v in (0.0, 0.5, 1.0))
        self.assertLess(abs(top - 5.0), 1e-6)

    def test_failing_ok_keeps_panel_and_creates_nothing(self):
        self.prof.Placement = FreeCAD.Placement(V(0, 2, 0), FreeCAD.Rotation())
        self.doc.recompute()
        self._open()
        self._fill()
        undo = self.doc.UndoCount
        count = len(self.doc.Objects)
        self._button("OK").click()
        _pump()
        self.assertTrue(FreeCADGui.Control.activeDialog())
        self.assertTrue(self._status().startswith("ProfileNotOnRails"), self._status())
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

    def test_edge_on_a_body_in_a_placed_part(self):
        part = self.doc.addObject("App::Part", "Holder")
        part.Placement = FreeCAD.Placement(V(3, 4, 5), FreeCAD.Rotation(V(0, 0, 1), 90))
        for o in (self.prof, self.a, self.b):
            part.addObject(o)
        self.doc.recompute()
        self._open()
        for key, obj in (("Profile", self.prof), ("Rail1", self.a), ("Rail2", self.b)):
            self._set(key, [(part, "%s.Edge1" % obj.Name)])
        self.assertTrue(self._w("G09_TwoRail_Rail1").text().endswith("Edge1"))
        self._button("OK").click()
        _pump()
        feat = self._features()[0]
        self.assertTrue(feat.isValid(), feat.getStatusString())
        bb = feat.Shape.BoundBox
        # the half cylinder (x 0..10, y 0..50, z 0..5) rotated 90 degrees about Z, then moved by (3, 4, 5)
        self.assertAlmostEqual(bb.XMin, 3 - 50, places=5)
        self.assertAlmostEqual(bb.XMax, 3, places=5)
        self.assertAlmostEqual(bb.YMin, 4, places=5)
        self.assertAlmostEqual(bb.YMax, 14, places=5)
        self.assertAlmostEqual(bb.ZMax, 10, places=5)

    def test_deleted_input_reports_inputdeleted(self):
        self._open()
        self._fill()
        self.doc.removeObject(self.b.Name)
        undo = self.doc.UndoCount
        count = len(self.doc.Objects)
        self._button("OK").click()
        _pump()
        self.assertTrue(FreeCADGui.Control.activeDialog())
        self.assertTrue(self._status().startswith("InputDeleted"), self._status())
        self.assertEqual(len(self.doc.Objects), count)
        self.assertEqual(self.doc.UndoCount, undo)
        self.assertEqual(self._features(), [])

    def test_unset_field_is_reported(self):
        self._open()
        self._set("Profile", [(self.prof, "Edge1")])
        self._button("OK").click()
        _pump()
        self.assertTrue(FreeCADGui.Control.activeDialog())
        self.assertTrue(self._status().startswith("InvalidInput"), self._status())

    def test_feature_icon(self):
        feat = SurfaceTwoRail.makeTwoRailFeature(self.doc, (self.prof, "Edge1"), (self.a, "Edge1"), (self.b, "Edge1"))
        self.doc.recompute()
        self.assertTrue(feat.isValid())
        self.assertEqual(feat.ViewObject.Proxy.getIcon(), ":/icons/Surface_TwoRailSweep.svg")
