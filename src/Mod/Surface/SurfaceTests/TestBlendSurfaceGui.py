# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI tests of the ``Surface_BlendSurface`` command and task panel (G09 slice 3c).

Run with ``FreeCAD -t TestSurfaceGui`` (needs the GUI binary). Public sources: L. Piegl and W. Tiller, *The
NURBS Book*, 2nd ed., 1997 (Hermite form of Bezier curves); the FreeCAD task dialog API.
"""

import unittest

import FreeCAD
import FreeCADGui
import Part
from PySide import QtWidgets

import SurfaceBlendSurface
from SurfaceTests.TestSurfaceAnalysisGui import _pump
from SurfaceTests.TestBlendSurface import strip

V = FreeCAD.Vector
CMD = "Surface_BlendSurface"


class TestBlendSurfaceGui(unittest.TestCase):
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
        self.doc = FreeCAD.newDocument("G09Blend")
        FreeCADGui.ActiveDocument = FreeCADGui.getDocument(self.doc.Name)
        _pump()
        FreeCADGui.Selection.clearSelection()
        self.fa = self._object("FaceA", strip(0, -5, 0))
        self.fb = self._object("FaceB", strip(3, 5, 10))
        self.ea = self._edge(self.fa, 0)
        self.eb = self._edge(self.fb, 5)
        self.doc.recompute()

    def tearDown(self):
        self._close()
        FreeCADGui.Selection.clearSelection()
        FreeCAD.closeDocument(self.doc.Name)

    # ---- helpers
    @property
    def mw(self):
        return FreeCADGui.getMainWindow()

    def _object(self, name, shape):
        o = self.doc.addObject("Part::Feature", name)
        o.Shape = shape
        return o

    @staticmethod
    def _edge(obj, x):
        for i, e in enumerate(obj.Shape.Edges):
            if abs(e.BoundBox.XMin - x) < 1e-9 and abs(e.BoundBox.XMax - x) < 1e-9:
                return "Edge%d" % (i + 1)

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
        self._w("G09_Blend_%s_Set" % key).click()
        _pump()

    def _fill(self):
        self._set("Edge1", [(self.fa, self.ea)])
        self._set("Edge2", [(self.fb, self.eb)])

    def _status(self):
        return self._w("G09_BlendStatus").text()

    def _features(self):
        return [o for o in self.doc.Objects if o.Name.startswith("BlendSurface")]

    # ---- tests
    def test_command_attributes_and_placement(self):
        info = FreeCADGui.Command.get(CMD).getInfo()
        self.assertEqual(info["menuText"], "Blend surface")
        self.assertEqual(info["pixmap"], "Surface_BlendSurface")
        self.assertTrue(info["toolTip"])
        self.assertTrue(info["statusTip"])
        self.assertFalse(info.get("shortcut"))
        wb = FreeCADGui.getWorkbench("SurfaceWorkbench")
        bar = wb.getToolbarItems()["Surface"]
        self.assertIn(CMD, bar)
        self.assertEqual(bar.index(CMD), bar.index("Surface_TwoRailSweep") + 1)
        self.assertTrue(FreeCADGui.Command.get(CMD).isActive())
        self._open()
        self.assertFalse(FreeCADGui.Command.get(CMD).isActive())

    def test_set_from_selection_needs_exactly_one_edge(self):
        self._open()
        self._set("Edge1", [(self.fa, self.ea), (self.fb, self.eb)])
        self.assertEqual(self._w("G09_Blend_Edge1").text(), "")
        self.assertTrue(self._status().startswith("InvalidInput"), self._status())
        self._set("Edge1", [(self.fa, self.ea)])
        self.assertEqual(self._w("G09_Blend_Edge1").text(), "%s.%s" % (self.fa.Label, self.ea))

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
        self.assertEqual(self.doc.UndoNames[0], "Blend surface")
        self.assertTrue(feats[0].isValid(), feats[0].getStatusString())
        ref = SurfaceBlendSurface.makeBlendSurfaceFeature(self.doc, (self.fa, self.ea), (self.fb, self.eb), "Ref")
        self.doc.recompute()
        s1, s2 = feats[0].Shape.Face1.Surface, ref.Shape.Face1.Surface
        u0, u1, v0, v1 = s1.bounds()
        for i in range(21):
            for j in range(21):
                u, v = u0 + (u1 - u0) * i / 20, v0 + (v1 - v0) * j / 20
                self.assertLess((s1.value(u, v) - s2.value(u, v)).Length, 1e-9)

    def test_continuity_and_size_reach_the_feature(self):
        self._open()
        self._fill()
        self._w("G09_Blend_Edge1_Continuity").setCurrentText("G1")
        self._w("G09_Blend_Edge2_Size").setValue(1.5)
        self._button("OK").click()
        _pump()
        feat = self._features()[0]
        self.assertEqual(feat.Continuity1, "G1")
        self.assertEqual(feat.Continuity2, "G2")
        self.assertAlmostEqual(feat.Size2, 1.5)
        self.assertEqual(feat.Shape.Face1.Surface.VDegree, 4)

    def test_failing_ok_keeps_panel_and_creates_nothing(self):
        self.fb.Placement = FreeCAD.Placement(V(-5, 0, -3), FreeCAD.Rotation())
        self.doc.recompute()
        self._open()
        self._fill()
        undo = self.doc.UndoCount
        count = len(self.doc.Objects)
        self._button("OK").click()
        _pump()
        self.assertTrue(FreeCADGui.Control.activeDialog())
        self.assertTrue(self._status().startswith("DegenerateChord"), self._status())
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

    def test_edge_with_its_face_selected(self):
        box = self._object("Box", Part.makeBox(10, 10, 10))
        self.doc.recompute()
        bs = box.Shape
        top = [i for i, f in enumerate(bs.Faces) if abs(f.BoundBox.ZMin - 10) < 1e-9 and f.BoundBox.ZLength < 1e-9][0]
        edge = [
            i for i, e in enumerate(bs.Edges)
            if abs(e.BoundBox.XMin - 10) < 1e-9 and abs(e.BoundBox.ZMin - 10) < 1e-9 and e.BoundBox.YLength > 9
        ][0]
        self.fb.Shape = strip(3, 14, 24)
        self.eb = self._edge(self.fb, 14)
        self.doc.recompute()
        self._open()
        self._set("Edge1", [(box, "Edge%d" % (edge + 1)), (box, "Face%d" % (top + 1))])
        self.assertEqual(self.mw.findChild(QtWidgets.QLineEdit, "G09_Blend_Edge1").text(), "%s.Edge%d" % (box.Label, edge + 1))
        self._set("Edge2", [(self.fb, self.eb)])
        self._button("OK").click()
        _pump()
        feat = self._features()[0]
        self.assertTrue(feat.isValid(), feat.getStatusString())
        self.assertEqual(list(feat.Edge1[1]), ["Face%d" % (top + 1), "Edge%d" % (edge + 1)])

    def test_edge_without_its_face_on_a_two_face_edge_fails(self):
        box = self._object("Box", Part.makeBox(10, 10, 10))
        self.doc.recompute()
        edge = [
            i for i, e in enumerate(box.Shape.Edges)
            if abs(e.BoundBox.XMin - 10) < 1e-9 and abs(e.BoundBox.ZMin - 10) < 1e-9 and e.BoundBox.YLength > 9
        ][0]
        self._open()
        self._set("Edge1", [(box, "Edge%d" % (edge + 1))])
        self._set("Edge2", [(self.fb, self.eb)])
        self._button("OK").click()
        _pump()
        self.assertTrue(FreeCADGui.Control.activeDialog())
        self.assertTrue(self._status().startswith("AmbiguousFace"), self._status())
        self.assertEqual(self._features(), [])

    def test_edge_on_a_body_in_a_placed_part(self):
        part = self.doc.addObject("App::Part", "Holder")
        part.Placement = FreeCAD.Placement(V(3, 4, 5), FreeCAD.Rotation(V(0, 0, 1), 90))
        part.addObject(self.fb)
        self.doc.recompute()
        self._open()
        self._set("Edge1", [(self.fa, self.ea)])
        self._set("Edge2", [(part, "%s.%s" % (self.fb.Name, self.eb))])
        self.assertTrue(self._w("G09_Blend_Edge2").text().endswith(self.eb))
        self._button("OK").click()
        _pump()
        feat = self._features()[0]
        self.assertTrue(feat.isValid(), feat.getStatusString())
        # face B (x 5..10, y 0..10, z 3) turned 90 degrees about Z and moved by (3, 4, 5)
        self.assertAlmostEqual(feat.Shape.BoundBox.ZMax, 8.0, places=6)

    def test_deleted_input_reports_inputdeleted(self):
        self._open()
        self._fill()
        self.doc.removeObject(self.fb.Name)
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
        self._set("Edge1", [(self.fa, self.ea)])
        self._button("OK").click()
        _pump()
        self.assertTrue(FreeCADGui.Control.activeDialog())
        self.assertTrue(self._status().startswith("InvalidInput"), self._status())

    def test_feature_icon(self):
        feat = SurfaceBlendSurface.makeBlendSurfaceFeature(self.doc, (self.fa, self.ea), (self.fb, self.eb))
        self.doc.recompute()
        self.assertTrue(feat.isValid())
        self.assertEqual(feat.ViewObject.Proxy.getIcon(), ":/icons/Surface_BlendSurface.svg")
