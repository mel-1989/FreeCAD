# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI tests of the ``Surface_MatchSurface`` command and task panel (G09 slice 5b).

Run with ``FreeCAD -t TestSurfaceGui`` (needs the GUI binary). Public sources: the FreeCAD task dialog API,
the Qt documentation, the Coin3D scene-graph API and L. Piegl and W. Tiller, *The NURBS Book*, 2nd ed.,
1997. The truths are ``SurfaceMatch.matchSurface`` and ``makeMatchSurfaceFeature``, called directly on
the same faces.
"""

import statistics
import time
import unittest

import FreeCAD
import FreeCADGui
import Part
from PySide import QtCore, QtWidgets

import SurfaceAnalysisGui as SAG
import SurfaceMatch as SM
from SurfaceTests.TestMatchSurfaceFeature import Scene, same_surface
from SurfaceTests.TestSurfaceAnalysisGui import _pump

V = FreeCAD.Vector
CMD = "Surface_MatchSurface"


class TestMatchSurfaceGui(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        FreeCADGui.activateWorkbench("SurfaceWorkbench")
        _pump()

    @classmethod
    def tearDownClass(cls):
        if FreeCADGui.Control.activeDialog():
            FreeCADGui.Control.closeDialog()
        _pump()

    def setUp(self):
        self._close()
        self.doc = FreeCAD.newDocument("G09Match")
        FreeCADGui.ActiveDocument = FreeCADGui.getDocument(self.doc.Name)
        _pump()
        FreeCADGui.Selection.clearSelection()
        self.scene = Scene(self.doc)
        self.src, self.box = self.scene.src, self.scene.box
        self.view = FreeCADGui.ActiveDocument.ActiveView
        self.view.viewIsometric()
        self.view.fitAll()
        _pump()

    def tearDown(self):
        self._close()
        FreeCADGui.Selection.clearSelection()
        FreeCAD.closeDocument(self.doc.Name)

    # ---- helpers
    @property
    def mw(self):
        return FreeCADGui.getMainWindow()

    def _close(self):
        if FreeCADGui.Control.activeDialog():
            for b in self.mw.findChildren(QtWidgets.QPushButton):
                if b.text().replace("&", "") == "Cancel" and b.isVisible():
                    b.click()
                    break
            _pump()
        if FreeCADGui.Control.activeDialog():
            FreeCADGui.Control.closeDialog()
        for _ in range(50):
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
        self._w("G09_Match_%s_Set" % key).click()
        _pump()

    def _fill(self):
        self._set("Source", [(self.src, "Face1")])
        self._set("Target", [(self.box, self.scene.top), (self.box, self.scene.edge)])

    def _status(self):
        return self._w("G09_MatchStatus").text()

    def _features(self):
        return [o for o in self.doc.Objects if o.Name.startswith("MatchSurface")]

    def _ufs(self):
        return SAG._find_selection_node(self.view)

    def _g09_nodes(self):
        ufs = self._ufs()
        return [ufs.getChild(i).getName().getString() for i in range(ufs.getNumChildren()) if ufs.getChild(i).getName().getString().startswith("G09_")]

    def _preview_face(self):
        """The tessellated preview's bounding box: (min z, max z) over the vertices of ``G09_MATCH``."""
        ufs = self._ufs()
        for i in range(ufs.getNumChildren()):
            if ufs.getChild(i).getName().getString() == "G09_MATCH":
                return ufs.getChild(i)
        return None

    def _reference(self, k=2, side="V0", level=5, approximate=False):
        return SM.build_match((self.src, ["Face1"]), side, self.scene.target, k, 1e-4, level, approximate)

    def _tree_double_click(self, obj):
        from PySide6 import QtTest

        tree = [w for w in self.mw.findChildren(QtWidgets.QTreeWidget) if w.metaObject().className() == "Gui::TreeWidget"][0]
        _pump(20)
        item = tree.findItems(obj.Label, QtCore.Qt.MatchRecursive)[0]
        tree.scrollToItem(item)
        QtTest.QTest.mouseDClick(tree.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, tree.visualItemRect(item).center())
        _pump(10)

    # ---- tests
    def test_command_attributes_and_placement(self):
        info = FreeCADGui.Command.get(CMD).getInfo()
        self.assertEqual(info["menuText"], "Match surface")
        self.assertEqual(info["pixmap"], "Surface_MatchSurface")
        self.assertTrue(info["toolTip"])
        self.assertTrue(info["statusTip"])
        self.assertFalse(info.get("shortcut"))
        wb = FreeCADGui.getWorkbench("SurfaceWorkbench")
        bar = wb.getToolbarItems()["Surface"]
        self.assertIn(CMD, bar)
        self.assertEqual(bar.index(CMD), bar.index("Surface_EditPoles") + 1)
        self.assertTrue(FreeCADGui.Command.get(CMD).isActive())
        self._open()
        self.assertFalse(FreeCADGui.Command.get(CMD).isActive())

    def test_set_from_selection_takes_the_face_the_side_and_the_target(self):
        self._open()
        far = [i for i, e in enumerate(self.src.Shape.Edges) if e.CenterOfMass.y > 20][0]
        self._set("Source", [(self.src, "Face1"), (self.src, "Edge%d" % (far + 1))])
        self.assertEqual(self._w("G09_Match_Source").text(), "%s.Face1" % self.src.Label)
        self.assertEqual(self._w("G09_Match_Side").currentText(), "V1")
        self._set("Source", [(self.src, "Face1"), (self.src, "Face1")])
        near = [i for i, e in enumerate(self.src.Shape.Edges) if abs(e.CenterOfMass.y - 2) < 1e-6][0]
        self._set("Source", [(self.src, "Face1"), (self.src, "Edge%d" % (near + 1))])
        self.assertEqual(self._w("G09_Match_Side").currentText(), "V0")
        self._set("Target", [(self.box, self.scene.edge)])
        self.assertEqual(self._w("G09_Match_Target").text(), "%s.%s" % (self.box.Label, self.scene.edge))
        self.assertTrue(self._status().startswith("AmbiguousFace"), self._status())  # an edge of two faces
        self._set("Target", [(self.box, self.scene.top), (self.box, self.scene.edge)])
        self.assertTrue(self._status().startswith("Level"), self._status())
        self._set("Source", [(self.src, "Face1"), (self.box, "Face1")])
        self.assertTrue(self._status().startswith("InvalidInput"), self._status())

    def test_the_preview_follows_every_change_and_hides_the_source(self):
        self._open()
        self._fill()
        self.assertFalse(self.src.Visibility)
        self.assertEqual(self._g09_nodes().count("G09_MATCH"), 1)
        for k, name in ((0, "G0"), (1, "G1"), (2, "G2")):
            self._w("G09_Match_Continuity").setCurrentText(name)
            _pump(2)
            ref = self._reference(k=k)
            self.assertIn("Level %d," % ref["level"], self._status())
            self.assertEqual(self._g09_nodes().count("G09_MATCH"), 1)
            node = self._preview_face()
            coords = node.getChild(1).getChild(2).point  # the face's SoCoordinate3
            pts = [V(*p.getValue()) if hasattr(p, "getValue") else V(p[0], p[1], p[2]) for p in coords.getValues()]
            self.assertGreater(len(pts), 20)
            for p in pts[:: max(1, len(pts) // 25)]:
                self.assertLess(ref["face"].distToShape(Part.Vertex(p))[0], 1e-4)
        self.assertEqual(self._node_names(self._preview_face()), ["G09_MATCH_FACE", "G09_MATCH_SIDE"])

    @staticmethod
    def _node_names(node):
        return [node.getChild(i).getName().getString() for i in range(node.getNumChildren()) if node.getChild(i).getName().getString()]

    def test_the_level_choice_stops_at_six(self):
        self._open()
        spin = self._w("G09_Match_MaxLevel")
        self.assertEqual((spin.minimum(), spin.maximum()), (0, 6))
        self._fill()
        spin.setValue(0)
        _pump(2)
        self.assertIn("Level 0,", self._status())

    def test_ok_creates_one_feature_in_one_transaction_and_leaves_the_source_hidden(self):
        self._open()
        self._fill()
        self._w("G09_Match_Continuity").setCurrentText("G1")
        undo = self.doc.UndoCount
        self._button("OK").click()
        _pump()
        self.assertFalse(FreeCADGui.Control.activeDialog())
        feats = self._features()
        self.assertEqual(len(feats), 1)
        self.assertEqual(self.doc.UndoCount, undo + 1)
        self.assertEqual(self.doc.UndoNames[0], "Match surface")
        self.assertTrue(feats[0].isValid(), feats[0].getStatusString())
        self.assertFalse(self.src.Visibility)
        self.assertEqual(self._g09_nodes(), [])
        ref = SM.makeMatchSurfaceFeature(self.doc, (self.src, "Face1"), "V0", self.scene.target, "Ref")
        ref.Continuity = "G1"
        self.doc.recompute()
        self.assertTrue(same_surface(feats[0].Shape.Faces[0].Surface, ref.Shape.Faces[0].Surface))
        self.assertEqual(feats[0].Side, "V0")
        self.assertEqual(feats[0].Continuity, "G1")

    def test_cancel_changes_nothing_and_leaves_no_node(self):
        self._open()
        self._fill()
        self.assertFalse(self.src.Visibility)
        undo = self.doc.UndoCount
        count = len(self.doc.Objects)
        self._button("Cancel").click()
        _pump()
        self.assertFalse(FreeCADGui.Control.activeDialog())
        self.assertEqual(self.doc.UndoCount, undo)
        self.assertEqual(len(self.doc.Objects), count)
        self.assertTrue(self.src.Visibility)
        self.assertEqual(self._g09_nodes(), [])

    def test_a_failing_ok_keeps_the_panel_and_changes_nothing(self):
        self._open()
        self._fill()
        self._w("G09_Match_Side").setCurrentText("V1")  # the far side does not meet the box edge
        _pump(2)
        self.assertFalse(self._status().startswith("Level"), self._status())
        undo = self.doc.UndoCount
        count = len(self.doc.Objects)
        self._button("OK").click()
        _pump()
        self.assertTrue(FreeCADGui.Control.activeDialog())
        self.assertEqual(len(self.doc.Objects), count)
        self.assertEqual(self.doc.UndoCount, undo)
        self.assertTrue(self._status()[:1].isupper())

    def test_a_double_click_re_edits_a_feature_in_one_undo_step(self):
        feat = SM.makeMatchSurfaceFeature(self.doc, (self.src, "Face1"), "V0", self.scene.target)
        self.src.Visibility = False
        self.doc.recompute()
        _pump(10)
        undo = self.doc.UndoCount
        self._tree_double_click(feat)
        self.assertTrue(FreeCADGui.Control.activeDialog())
        self.assertEqual(self.doc.UndoCount, undo)
        self.assertEqual(self._w("G09_Match_Continuity").currentText(), "G2")
        self.assertEqual(self._w("G09_Match_MaxLevel").value(), 5)
        self.assertFalse(feat.Visibility)
        self._w("G09_Match_Continuity").setCurrentText("G0")
        self._w("G09_Match_MaxLevel").setValue(3)
        _pump(2)
        self._button("OK").click()
        _pump()
        self.assertFalse(FreeCADGui.Control.activeDialog())
        self.assertEqual(self.doc.UndoCount, undo + 1)
        self.assertEqual(self.doc.UndoNames[0], "Match surface")
        self.assertEqual(len(self._features()), 1)
        self.assertEqual((feat.Continuity, feat.MaxLevel), ("G0", 3))
        self.assertTrue(feat.isValid(), feat.getStatusString())
        self.assertTrue(feat.Visibility)
        self.assertFalse(self.src.Visibility)
        self.assertEqual(self._g09_nodes(), [])

    def test_a_re_edit_shows_and_keeps_a_stored_level_above_six(self):
        feat = SM.makeMatchSurfaceFeature(self.doc, (self.src, "Face1"), "V0", self.scene.target)
        feat.MaxLevel = 8
        self.doc.recompute()
        _pump(10)
        panel = FreeCADGui.getDocument(self.doc.Name).getObject(feat.Name).Proxy
        panel.doubleClicked(feat.ViewObject)
        _pump()
        spin = self._w("G09_Match_MaxLevel")
        self.assertEqual(spin.value(), 8)
        self._w("G09_Match_Continuity").setCurrentText("G1")
        self._button("OK").click()
        _pump()
        self.assertEqual(feat.MaxLevel, 8)

    def test_cancelling_a_re_edit_restores_the_visibility(self):
        feat = SM.makeMatchSurfaceFeature(self.doc, (self.src, "Face1"), "V0", self.scene.target)
        self.doc.recompute()
        _pump(10)
        undo = self.doc.UndoCount
        feat.ViewObject.Proxy.doubleClicked(feat.ViewObject)
        _pump()
        self._w("G09_Match_Continuity").setCurrentText("G0")
        self._button("Cancel").click()
        _pump()
        self.assertEqual(feat.Continuity, "G2")
        self.assertEqual(self.doc.UndoCount, undo)
        self.assertTrue(self.src.Visibility)
        self.assertTrue(feat.Visibility)
        self.assertEqual(self._g09_nodes(), [])

    def test_live_latency_of_a_change(self):
        self._open()
        self._fill()
        times = []
        for k in range(20):
            t0 = time.perf_counter()
            self._w("G09_Match_Continuity").setCurrentIndex(k % 3)
            times.append(time.perf_counter() - t0)
        FreeCAD.Console.PrintMessage("G09 match preview latency: median %.3f s, worst %.3f s\n" % (statistics.median(times), max(times)))
        self.assertLess(statistics.median(times), 0.5)
        self.assertLess(max(times), 2.0)
