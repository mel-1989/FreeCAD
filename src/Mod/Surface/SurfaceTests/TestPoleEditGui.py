# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI tests of the ``Surface_EditPoles`` command and task panel (G09 slice 4a).

Run with ``FreeCAD -t TestSurfaceGui`` (needs the GUI binary). Public sources: the FreeCAD task dialog API,
the Qt documentation and the Coin3D scene-graph API. The truths are the App-level functions of
``SurfacePoleEdit`` and slice 2's overlays, called directly on the same face.
"""

import statistics
import time
import unittest

import FreeCAD
import FreeCADGui
import Part
from PySide import QtCore, QtGui, QtWidgets

import SurfaceAnalysisGui as SAG
import SurfacePoleEdit as PE
from SurfaceTests.TestPoleEdit import cubic_face, clamped_face, same_surface
from SurfaceTests.TestSurfaceAnalysisGui import _pump

V = FreeCAD.Vector
CMD = "Surface_EditPoles"


class TestPoleEditGui(unittest.TestCase):
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
        self.doc = FreeCAD.newDocument("G09PoleEdit")
        FreeCADGui.ActiveDocument = FreeCADGui.getDocument(self.doc.Name)
        _pump()
        FreeCADGui.Selection.clearSelection()
        self.src = self.doc.addObject("Part::Feature", "Src")
        self.src.Shape = cubic_face(6, 8)
        self.doc.recompute()
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

    def _select(self, obj=None, sub="Face1"):
        FreeCADGui.Selection.clearSelection()
        FreeCADGui.Selection.addSelection(self.doc.Name, (obj or self.src).Name, sub)
        _pump()

    def _open(self, obj=None, sub="Face1"):
        self._select(obj, sub)
        FreeCADGui.runCommand(CMD)
        _pump()
        self.assertTrue(FreeCADGui.Control.activeDialog())

    def _set_offset(self, x, y, z):
        for axis, val in zip("XYZ", (x, y, z)):
            self._w("G09_PoleEdit_Offset%s" % axis).setProperty("rawValue", float(val))
        _pump(2)

    def _choose(self, i, j):
        self._w("G09_PoleEdit_U").setValue(i)
        self._w("G09_PoleEdit_V").setValue(j)
        _pump(2)

    def _lock(self, side, text):
        self._w("G09_PoleEdit_Lock%s" % side).setCurrentText(text)
        _pump(2)

    def _analysis(self, text):
        self._w("G09_PoleEdit_Analysis").setCurrentText(text)
        _pump(2)

    def _status(self):
        return self._w("G09_PoleEditStatus").text()

    def _features(self):
        return [o for o in self.doc.Objects if o.Name.startswith("PoleEdit")]

    def _ufs(self):
        return SAG._find_selection_node(self.view)

    def _g09_nodes(self):
        ufs = self._ufs()
        return [ufs.getChild(i).getName().getString() for i in range(ufs.getNumChildren()) if ufs.getChild(i).getName().getString().startswith("G09_")]

    def _node(self, name):
        ufs = self._ufs()
        for i in range(ufs.getNumChildren()):
            if ufs.getChild(i).getName().getString() == name:
                return ufs.getChild(i)
        return None

    @staticmethod
    def _overlay_data(node):
        """(vertex count, colours) of an overlay separator."""
        n, colors = 0, []
        for i in range(node.getNumChildren()):
            ch = node.getChild(i)
            t = ch.getTypeId().getName().getString()
            if t == "Coordinate3":
                n = ch.point.getNum()
            elif t == "Material":
                colors = [tuple(c) for c in ch.diffuseColor.getValues(0)]
        return n, colors

    def _expected_overlay(self, face, kind):
        if kind in ("mean", "gauss"):
            h = SAG.show_curvature(face, kind=kind, view=self.view)
        elif kind == "zebra":
            h = SAG.show_zebra(face, view=self.view)
        else:
            h = SAG.show_isophotes(face, view=self.view)
        try:
            return self._overlay_data(h.node)
        finally:
            SAG.remove(h)

    def _gl(self):
        for g in self.mw.findChildren(QtWidgets.QWidget):
            if g.metaObject().className() == "QOpenGLWidget" and g.parentWidget() is not None and g.parentWidget().metaObject().className() == "Gui::View3DInventorViewer" and g.isVisible():
                return g
        self.fail("no 3D view widget")

    def _click(self, x, y):
        """A real click at pixel ``(x, y)`` (origin bottom left, as ``getPointOnScreen`` gives it)."""
        gl = self._gl()
        qy = gl.height() - y
        lp = QtCore.QPointF(x, qy)
        gp = QtCore.QPointF(gl.mapToGlobal(QtCore.QPoint(int(x), int(qy))))
        N, L = QtCore.Qt.NoButton, QtCore.Qt.LeftButton

        def ev(typ, button, buttons):
            return QtGui.QMouseEvent(typ, lp, lp, gp, button, buttons, QtCore.Qt.NoModifier)

        app = QtWidgets.QApplication
        app.sendEvent(gl, ev(QtCore.QEvent.MouseMove, N, N))
        _pump(2)
        app.sendEvent(gl, ev(QtCore.QEvent.MouseButtonPress, L, L))
        _pump(2)
        app.sendEvent(gl, ev(QtCore.QEvent.MouseButtonRelease, L, N))
        _pump(3)

    # ---- tests
    def test_command_attributes_and_placement(self):
        info = FreeCADGui.Command.get(CMD).getInfo()
        self.assertEqual(info["menuText"], "Edit control points")
        self.assertEqual(info["pixmap"], "Surface_EditPoles")
        self.assertTrue(info["toolTip"])
        self.assertTrue(info["statusTip"])
        self.assertFalse(info.get("shortcut"))
        bar = FreeCADGui.getWorkbench("SurfaceWorkbench").getToolbarItems()["Surface"]
        self.assertEqual(bar.index(CMD), bar.index("Surface_BlendSurface") + 1)
        FreeCADGui.Selection.clearSelection()
        self.assertFalse(FreeCADGui.Command.get(CMD).isActive())
        self._select()
        self.assertTrue(FreeCADGui.Command.get(CMD).isActive())
        self._open()
        self.assertFalse(FreeCADGui.Command.get(CMD).isActive())

    def test_the_panel_draws_the_net_and_hides_the_source(self):
        self._open()
        self.assertFalse(self.src.Visibility)
        names = self._g09_nodes()
        self.assertEqual(names.count("G09_POLES"), 1)
        net = self._node("G09_POLES")
        children = [net.getChild(i).getName().getString() for i in range(net.getNumChildren())]
        for part in ("G09_POLES_NET", "G09_POLES_FREE", "G09_POLES_LOCKED", "G09_POLES_CHOSEN"):
            self.assertIn(part, children)
        self.assertEqual(self._w("G09_PoleEdit_U").maximum(), 6)
        self.assertEqual(self._w("G09_PoleEdit_V").maximum(), 8)

    def test_cancel_changes_nothing_and_leaves_no_node(self):
        undo, count = self.doc.UndoCount, len(self.doc.Objects)
        self._open()
        self._set_offset(0, 0, 1)
        self._analysis("Mean curvature")
        self.assertTrue(any(n.startswith("G09_OVERLAY") for n in self._g09_nodes()))
        self._button("Cancel").click()
        _pump()
        self.assertFalse(FreeCADGui.Control.activeDialog())
        self.assertEqual(self.doc.UndoCount, undo)
        self.assertEqual(len(self.doc.Objects), count)
        self.assertTrue(self.src.Visibility)
        self.assertEqual(self._g09_nodes(), [])

    def test_ok_creates_one_feature_in_one_transaction(self):
        self._open()
        self._choose(3, 4)
        self._set_offset(0.2, -0.3, 0.8)
        self._lock("U0", "G1")
        undo = self.doc.UndoCount
        self._button("OK").click()
        _pump()
        self.assertFalse(FreeCADGui.Control.activeDialog())
        feats = self._features()
        self.assertEqual(len(feats), 1)
        self.assertEqual(self.doc.UndoCount, undo + 1)
        self.assertEqual(self.doc.UndoNames[0], "Edit control points")
        self.assertTrue(feats[0].isValid(), feats[0].getStatusString())
        ref = PE.makePoleEditFeature(self.doc, (self.src, "Face1"), {(3, 4): V(0.2, -0.3, 0.8)}, {"U0": 1}, "Ref")
        self.doc.recompute()
        self.assertTrue(same_surface(feats[0].Shape.Faces[0].Surface, ref.Shape.Faces[0].Surface))
        self.assertEqual(feats[0].LockU0, "G1")
        self.assertEqual(self._g09_nodes(), [])
        self.assertFalse(self.src.Visibility)
        self.doc.removeObject(ref.Name)
        self.doc.undo()
        self.assertEqual(self._features(), [])
        self.assertTrue(self.src.Visibility)

    def test_the_offset_is_absolute_and_reset_clears_it(self):
        self._open()
        self._choose(3, 4)
        self._set_offset(0, 0, 1)
        self._set_offset(0, 0, 2)  # absolute: 2, not 3
        self.assertIn("1 pole(s) moved", self._status())
        self._button("Reset pole").click()
        _pump(2)
        self.assertIn("0 pole(s) moved", self._status())
        self.assertAlmostEqual(self._w("G09_PoleEdit_OffsetZ").property("rawValue"), 0.0)
        self._set_offset(0, 0, 2)
        self._choose(3, 5)
        self.assertAlmostEqual(self._w("G09_PoleEdit_OffsetZ").property("rawValue"), 0.0)
        self._choose(3, 4)
        self.assertAlmostEqual(self._w("G09_PoleEdit_OffsetZ").property("rawValue"), 2.0)

    def test_a_locked_pole_is_refused_and_a_failing_ok_changes_nothing(self):
        self._open()
        self._lock("U0", "G1")
        self._choose(2, 3)
        self._set_offset(0, 0, 1)
        self.assertTrue(self._status().startswith("LockedPole"), self._status())
        undo, count = self.doc.UndoCount, len(self.doc.Objects)
        self._button("OK").click()
        _pump()
        self.assertTrue(FreeCADGui.Control.activeDialog())
        self.assertTrue(self._status().startswith("LockedPole"), self._status())
        self.assertEqual(self.doc.UndoCount, undo)
        self.assertEqual(len(self.doc.Objects), count)
        self._lock("U0", "Free")
        self.assertIn("1 pole(s) moved", self._status())
        self._button("OK").click()
        _pump()
        self.assertEqual(len(self._features()), 1)

    def test_locked_markers_are_drawn_apart(self):
        self._open()
        net = self._node("G09_POLES")

        def count(name):
            for i in range(net.getNumChildren()):
                if net.getChild(i).getName().getString() == name:
                    for j in range(net.getChild(i).getNumChildren()):
                        ch = net.getChild(i).getChild(j)
                        if ch.getTypeId().getName().getString() == "Coordinate3":
                            return ch.point.getNum()
            return None

        self.assertEqual(count("G09_POLES_LOCKED"), 0)
        self.assertEqual(count("G09_POLES_FREE"), 6 * 8 - 1)
        self._lock("U0", "G1")
        net = self._node("G09_POLES")
        self.assertEqual(count("G09_POLES_LOCKED"), 2 * 8 - 1)  # rows 1 and 2, less the chosen pole (1, 1)
        self.assertEqual(count("G09_POLES_CHOSEN"), 1)

    def test_the_analysis_overlay_equals_slice_2_on_the_edited_face(self):
        self._open()
        self._choose(3, 4)
        self._set_offset(0.2, -0.3, 0.8)
        face = PE.edit_poles(self.src.Shape.Faces[0], {(3, 4): V(0.2, -0.3, 0.8)})["face"]
        before = None
        for text, kind, prefix in (
            ("Mean curvature", "mean", "G09_OVERLAY_curvature"),
            ("Gaussian curvature", "gauss", "G09_OVERLAY_curvature"),
            ("Zebra", "zebra", "G09_OVERLAY_zebra"),
            ("Isophotes", "isophotes", "G09_OVERLAY_isophote"),
        ):
            self._analysis(text)
            node = self._node(prefix)
            self.assertIsNotNone(node, text)
            n, colors = self._overlay_data(node)
            en, ecolors = self._expected_overlay(face, kind)
            self.assertEqual(n, en, text)
            self.assertEqual(len(colors), len(ecolors), text)
            for a, b in zip(colors, ecolors):
                for x, y in zip(a, b):
                    self.assertAlmostEqual(x, y, delta=1e-6, msg=text)
            self.assertEqual([x for x in self._g09_nodes() if x.startswith("G09_OVERLAY")], [prefix])
            if kind == "mean":
                before = colors
        self._analysis("None")
        self.assertEqual([x for x in self._g09_nodes() if x.startswith("G09_OVERLAY")], [])
        self._analysis("Mean curvature")
        self._set_offset(0, 0, 3)
        _n, after = self._overlay_data(self._node("G09_OVERLAY_curvature"))
        self.assertNotEqual(before, after)  # a change redraws the overlay

    def test_clicking_a_marker_chooses_the_nearest_pole(self):
        self._open()
        surf = PE.editable_surface(self.src.Shape.Faces[0])
        screen = {(i, j): self.view.getPointOnScreen(*tuple(surf.getPole(i, j))) for i in range(1, 7) for j in range(1, 9)}
        gl = self._gl()
        want = (4, 5)
        x, y = screen[want]
        others = min(((x - sx) ** 2 + (y - sy) ** 2) ** 0.5 for k, (sx, sy) in screen.items() if k != want)
        self.assertGreater(others, 6.0, "poles overlap on screen; the view is too small for this test")
        self.assertTrue(0 <= x < gl.width() and 0 <= y < gl.height())
        self._click(x + 2, y - 1)
        self.assertEqual((self._w("G09_PoleEdit_U").value(), self._w("G09_PoleEdit_V").value()), want)
        want = (2, 7)
        x, y = screen[want]
        self._click(x, y)
        self.assertEqual((self._w("G09_PoleEdit_U").value(), self._w("G09_PoleEdit_V").value()), want)
        self._click(1, 1)  # more than 8 px from every pole: no change
        self.assertEqual((self._w("G09_PoleEdit_U").value(), self._w("G09_PoleEdit_V").value()), want)

    def test_a_face_inside_a_placed_part_takes_global_offsets(self):
        part = self.doc.addObject("App::Part", "Part")
        part.Placement = FreeCAD.Placement(V(3, 4, 5), FreeCAD.Rotation(V(0, 0, 1), 90))
        self.doc.removeObject(self.src.Name)
        self.src = self.doc.addObject("Part::Feature", "Inner")
        self.src.Shape = cubic_face(6, 8)
        part.addObject(self.src)
        self.doc.recompute()
        self._select(part, "Inner.Face1")
        self.assertTrue(FreeCADGui.Command.get(CMD).isActive())
        FreeCADGui.runCommand(CMD)
        _pump()
        self._choose(3, 4)
        self._set_offset(1, 0, 0)  # one unit along global X
        self._button("OK").click()
        _pump()
        feat = self._features()[0]
        self.assertTrue(feat.isValid(), feat.getStatusString())
        local = list(feat.PoleOffsets)[0]
        self.assertAlmostEqual((local - V(0, -1, 0)).Length, 0.0, places=9)  # the part is turned 90 degrees about Z
        placed = Part.getShape(part, "Inner.Face1", needSubElement=True)
        surf = PE.editable_surface(placed)
        base = PE.editable_surface(feat.Shape.Faces[0])
        self.assertAlmostEqual((base.getPole(3, 4) - surf.getPole(3, 4) - V(1, 0, 0)).Length, 0.0, places=9)

    def _tree_double_click(self, obj):
        from PySide6 import QtTest

        tree = [w for w in self.mw.findChildren(QtWidgets.QTreeWidget) if w.metaObject().className() == "Gui::TreeWidget"][0]
        _pump(20)
        item = tree.findItems(obj.Label, QtCore.Qt.MatchRecursive)[0]
        tree.scrollToItem(item)
        QtTest.QTest.mouseDClick(tree.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, tree.visualItemRect(item).center())
        _pump(10)

    def test_a_double_click_re_edits_a_feature_in_one_undo_step(self):
        feat = PE.makePoleEditFeature(self.doc, (self.src, "Face1"), {(3, 4): V(0, 0, 1)}, {"V0": 0})
        self.doc.recompute()
        _pump(10)
        undo = self.doc.UndoCount
        self._tree_double_click(feat)  # a real double click on the tree row
        self.assertTrue(FreeCADGui.Control.activeDialog())
        self.assertEqual(self.doc.UndoCount, undo)
        self.assertEqual(self._w("G09_PoleEdit_LockV0").currentText(), "G0")
        self._choose(3, 4)
        self.assertAlmostEqual(self._w("G09_PoleEdit_OffsetZ").property("rawValue"), 1.0)
        self.assertFalse(feat.Visibility)
        self._set_offset(0, 0, 2.5)
        self._lock("V0", "G1")
        self._button("OK").click()
        _pump()
        self.assertFalse(FreeCADGui.Control.activeDialog())
        self.assertEqual(self.doc.UndoCount, undo + 1)
        self.assertEqual(self.doc.UndoNames[0], "Edit control points")
        self.assertEqual(len(self._features()), 1)
        self.assertEqual(feat.LockV0, "G1")
        self.assertAlmostEqual(list(feat.PoleOffsets)[0].z, 2.5)
        self.assertTrue(feat.Visibility)
        ref = PE.edit_poles(self.src.Shape.Faces[0], {(3, 4): V(0, 0, 2.5)}, {"V0": 1})["surface"]
        self.assertTrue(same_surface(feat.Shape.Faces[0].Surface, ref))
        self.assertEqual(self._g09_nodes(), [])

    def test_a_re_edit_on_a_changed_grid_loads_no_moves_and_ok_replaces_the_edit(self):
        feat = PE.makePoleEditFeature(self.doc, (self.src, "Face1"), {(3, 4): V(0, 0, 1)})
        self.doc.recompute()
        self.src.Shape = cubic_face(7, 8)
        self.doc.recompute()
        self.assertFalse(feat.isValid())
        old_sig, old_idx = feat.GridSignature, list(feat.PoleIndices)
        _pump(10)
        undo = self.doc.UndoCount
        self._tree_double_click(feat)
        self.assertTrue(FreeCADGui.Control.activeDialog())
        self.assertTrue(self._status().startswith("PoleGridChanged"), self._status())
        self.assertIn("replaces", self._status())
        self.assertEqual(self._w("G09_PoleEdit_U").maximum(), 7)
        self._choose(3, 4)
        self.assertAlmostEqual(self._w("G09_PoleEdit_OffsetZ").property("rawValue"), 0.0)
        self._set_offset(0, 0, 2)
        self.assertTrue(self._status().startswith("PoleGridChanged"), self._status())
        self._button("OK").click()
        _pump()
        self.assertEqual(self.doc.UndoCount, undo + 1)
        self.assertTrue(feat.isValid(), feat.getStatusString())
        self.assertNotEqual(feat.GridSignature, old_sig)
        self.assertAlmostEqual(list(feat.PoleOffsets)[0].z, 2.0)
        self.doc.undo()
        self.doc.recompute()
        self.assertEqual(feat.GridSignature, old_sig)
        self.assertEqual(list(feat.PoleIndices), old_idx)
        self.assertAlmostEqual(list(feat.PoleOffsets)[0].z, 1.0)
        self.assertFalse(feat.isValid())
        self.assertIn("PoleGridChanged", feat.getStatusString())

    def test_cancelling_a_re_edit_restores_the_feature(self):
        feat = PE.makePoleEditFeature(self.doc, (self.src, "Face1"), {(3, 4): V(0, 0, 1)})
        self.doc.recompute()
        undo = self.doc.UndoCount
        self._tree_double_click(feat)
        self.assertFalse(feat.Visibility)
        self._choose(3, 4)
        self._set_offset(0, 0, 5)
        self._button("Cancel").click()
        _pump()
        self.assertEqual(self.doc.UndoCount, undo)
        self.assertTrue(feat.Visibility)
        self.assertAlmostEqual(list(feat.PoleOffsets)[0].z, 1.0)
        self.assertEqual(self._g09_nodes(), [])

    def test_a_reopened_document_keeps_the_double_click(self):
        import os
        import tempfile

        feat = PE.makePoleEditFeature(self.doc, (self.src, "Face1"), {(3, 4): V(0, 0, 1)})
        self.doc.recompute()
        name = feat.Name
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p.FCStd")
            self.doc.saveAs(path)
            FreeCAD.closeDocument(self.doc.Name)
            self.doc = FreeCAD.openDocument(path)
            _pump(10)
            FreeCADGui.ActiveDocument = FreeCADGui.getDocument(self.doc.Name)
            back = self.doc.getObject(name)
            self.assertIsNotNone(back.ViewObject.Proxy)
            self.assertTrue(back.ViewObject.Proxy.doubleClicked(back.ViewObject))
            _pump()
            self.assertTrue(FreeCADGui.Control.activeDialog())
            self._choose(3, 4)
            self.assertAlmostEqual(self._w("G09_PoleEdit_OffsetZ").property("rawValue"), 1.0)
            self._button("Cancel").click()
            _pump()
            self.doc.saveAs(path)  # the saved file stays free of the proxy
            FreeCAD.closeDocument(self.doc.Name)
            self.doc = FreeCAD.newDocument("G09PoleEdit")

    def test_setting_the_edit_mode_opens_the_panel_without_an_undo_step(self):
        feat = PE.makePoleEditFeature(self.doc, (self.src, "Face1"), {(3, 4): V(0, 0, 1)})
        self.doc.recompute()
        undo = self.doc.UndoCount
        FreeCADGui.ActiveDocument.setEdit(feat.Name)
        _pump()
        self.assertTrue(FreeCADGui.Control.activeDialog())
        self._button("Cancel").click()
        _pump()
        self.assertIsNone(FreeCADGui.ActiveDocument.getInEdit())
        self.assertEqual(self.doc.UndoCount, undo)

    def test_a_trimmed_face_opens_a_refusing_panel(self):
        box = Part.makeBox(10, 10, 10).cut(Part.makeCylinder(2, 20, V(5, 5, -5)))
        holed = [i + 1 for i, f in enumerate(box.Faces) if len(f.Wires) == 2][0]
        self.src.Shape = box
        self.doc.recompute()
        self._open(sub="Face%d" % holed)
        self.assertTrue(self._status().startswith("TrimmedFace"), self._status())
        count = len(self.doc.Objects)
        self._button("OK").click()
        _pump()
        self.assertTrue(FreeCADGui.Control.activeDialog())
        self.assertEqual(len(self.doc.Objects), count)

    def test_live_latency_of_an_offset_change(self):
        self.src.Shape = clamped_face(10, 10, 3, 3)
        self.doc.recompute()
        self._open()
        self._analysis("Mean curvature")
        times = []
        for k in range(10):
            self._choose(3 + k % 4, 4)
            t0 = time.perf_counter()
            self._w("G09_PoleEdit_OffsetZ").setProperty("rawValue", 0.1 * (k + 1))
            times.append(time.perf_counter() - t0)
        self.assertLess(statistics.median(times), 0.2)
        self.assertLess(max(times), 1.0)
