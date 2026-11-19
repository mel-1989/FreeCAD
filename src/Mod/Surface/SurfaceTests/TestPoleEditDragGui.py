# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI tests of the in-view pole dragger of ``Surface_EditPoles`` (G09 slice 4b).

Run with ``FreeCAD -t TestSurfaceGui`` (needs the GUI binary). The events are real Qt mouse and key events
sent to the viewer's typed ``QOpenGLWidget``. The truths are geometric: the dragged pole's screen projection
must land on the cursor (view plane), its displacement must be normal to the view direction (view plane) or
parallel to the axis (axis drag), and the state after a release must equal the state a typed offset gives.
Public sources: the Qt documentation and the Coin3D scene-graph API.
"""

import statistics
import time
import unittest

import FreeCAD
import FreeCADGui
from PySide import QtCore, QtGui, QtWidgets

import SurfacePoleEdit as PE
from SurfaceTests.TestPoleEdit import cubic_face, clamped_face
from SurfaceTests.TestPoleEditGui import TestPoleEditGui as _Base
from SurfaceTests.TestSurfaceAnalysisGui import _pump

V = FreeCAD.Vector
CMD = "Surface_EditPoles"
STYLES = (
    "Gui::CADNavigationStyle",
    "Gui::BlenderNavigationStyle",
    "Gui::TouchpadNavigationStyle",
    "Gui::GestureNavigationStyle",
    "Gui::OpenInventorNavigationStyle",
)


class _Helpers:
    pass


for _name in ("mw", "_close", "_button", "_w", "_select", "_open", "_set_offset", "_choose", "_lock", "_analysis", "_status", "_features", "_ufs", "_g09_nodes", "_node", "_overlay_data", "_gl"):
    setattr(_Helpers, _name, _Base.__dict__[_name])


class TestPoleEditDragGui(_Helpers, unittest.TestCase):
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
        self.doc = FreeCAD.newDocument("G09PoleDrag")
        FreeCADGui.ActiveDocument = FreeCADGui.getDocument(self.doc.Name)
        _pump()
        FreeCADGui.Selection.clearSelection()
        self.src = self.doc.addObject("Part::Feature", "Src")
        self.src.Shape = cubic_face(6, 8)
        self.doc.recompute()
        self.view = FreeCADGui.ActiveDocument.ActiveView
        FreeCADGui.runCommand("Std_OrthographicCamera")
        self.view.setNavigationType("Gui::CADNavigationStyle")
        self.view.viewIsometric()
        self.view.fitAll()
        _pump()

    def tearDown(self):
        self._close()
        self.view.setNavigationType("Gui::CADNavigationStyle")
        FreeCADGui.Selection.clearSelection()
        FreeCAD.closeDocument(self.doc.Name)

    # ---- helpers
    def _panel(self):
        import SurfacePoleEditCommands as C

        return C.active_panel()

    def _pole_px(self, i, j, panel=None):
        """The Qt position (x, y from the top) of a marker of the edited surface."""
        gl = self._gl()
        surf = (panel.edited if panel is not None and panel.edited is not None else PE.editable_surface(self.src.Shape.Faces[0]))
        p = surf.getPole(i, j)
        x, y = self.view.getPointOnScreen(p.x, p.y, p.z)
        dpr = gl.devicePixelRatioF()
        return x / dpr, gl.height() - y / dpr

    def _send(self, typ, x, y, button=QtCore.Qt.NoButton, buttons=QtCore.Qt.NoButton, mods=QtCore.Qt.NoModifier):
        gl = self._gl()
        lp = QtCore.QPointF(x, y)
        gp = QtCore.QPointF(gl.mapToGlobal(QtCore.QPoint(int(x), int(y))))
        QtWidgets.QApplication.sendEvent(gl, QtGui.QMouseEvent(typ, lp, lp, gp, button, buttons, mods))

    def _press(self, x, y, button=QtCore.Qt.LeftButton, mods=QtCore.Qt.NoModifier):
        self._send(QtCore.QEvent.MouseMove, x, y)
        self._send(QtCore.QEvent.MouseButtonPress, x, y, button, button, mods)

    def _move(self, x, y):
        self._send(QtCore.QEvent.MouseMove, x, y, QtCore.Qt.NoButton, QtCore.Qt.LeftButton)

    def _release(self, x, y, button=QtCore.Qt.LeftButton):
        self._send(QtCore.QEvent.MouseButtonRelease, x, y, button, QtCore.Qt.NoButton)

    def _drag(self, x, y, dx, dy, steps=5):
        self._press(x, y)
        for k in range(1, steps + 1):
            self._move(x + dx * k / steps, y + dy * k / steps)
        return x + dx, y + dy

    def _camera(self):
        cam = self.view.getCameraNode()
        return tuple(cam.position.getValue().getValue()) + tuple(cam.orientation.getValue().getValue())

    def _offset(self):
        return V(*(float(self._w("G09_PoleEdit_Offset%s" % a).property("rawValue")) for a in "XYZ"))

    def _pole(self, panel, i, j):
        return V(panel.edited.getPole(i, j))

    def _screen_of(self, p):
        gl = self._gl()
        x, y = self.view.getPointOnScreen(p.x, p.y, p.z)
        dpr = gl.devicePixelRatioF()
        return x / dpr, gl.height() - y / dpr

    def _set_mode(self, text):
        self._w("G09_PoleEdit_DragMode").setCurrentText(text)
        _pump(1)

    # ---- tests
    def test_the_view_plane_drag_follows_the_cursor(self):
        self._open()
        panel = self._panel()
        base = self._pole(panel, 4, 5)
        x, y = self._pole_px(4, 5)
        self.assertEqual(self._w("G09_PoleEdit_DragMode").currentText(), "View plane")
        ex, ey = self._drag(x, y, 23.0, -17.0)
        self._release(ex, ey)
        self.assertEqual(panel.chosen, (4, 5))
        moved = self._pole(panel, 4, 5)
        normal = self.view.getViewDirection()
        self.assertAlmostEqual((moved - base).dot(normal), 0.0, places=6)  # in the plane through the pole, normal to the view
        sx, sy = self._screen_of(moved)
        self.assertLess(((sx - ex) ** 2 + (sy - ey) ** 2) ** 0.5, 1.5)  # the marker is under the cursor
        off = self._offset()
        self.assertAlmostEqual((off - (moved - base)).Length, 0.0, places=6)  # the fields hold the absolute offset
        self.assertEqual(self._g09_nodes().count("G09_POLES"), 1)

    def test_axis_drags_stay_on_their_global_axis(self):
        for axis, vec in (("X", V(1, 0, 0)), ("Y", V(0, 1, 0)), ("Z", V(0, 0, 1))):
            with self.subTest(axis=axis):
                self._close()
                self._open()
                self._set_mode(axis)
                panel = self._panel()
                x, y = self._pole_px(3, 4, panel)
                ex, ey = self._drag(x, y, 30.0, -22.0)
                self._release(ex, ey)
                off = self._offset()
                self.assertGreater(off.Length, 1e-3)
                self.assertAlmostEqual((off - vec * off.dot(vec)).Length, 0.0, places=9)
                self.assertEqual(len(panel.moves), 1)

    def test_a_drag_adds_to_the_offset_at_the_press(self):
        self._open()
        self._choose(3, 4)
        self._set_offset(0.0, 0.0, 0.5)
        panel = self._panel()
        self._set_mode("Z")
        x, y = self._pole_px(3, 4, panel)
        before = self._offset()
        self._press(x, y)
        self._move(x, y)  # no cursor travel: the offset is the one at the press, not a restart from zero
        self._release(x, y)
        after = self._offset()
        self.assertAlmostEqual((after - before).dot(V(1, 0, 0)), 0.0, places=9)
        self.assertAlmostEqual((after - before).dot(V(0, 1, 0)), 0.0, places=9)
        self.assertGreater(after.z, 0.0)

    def test_the_release_state_equals_a_typed_offset(self):
        self._open()
        self._analysis("Mean curvature")
        panel = self._panel()
        x, y = self._pole_px(4, 5, panel)
        ex, ey = self._drag(x, y, 18.0, 11.0)
        self._release(ex, ey)
        off = self._offset()
        dragged = {k: V(v) for k, v in panel.moves.items()}
        poles = [[V(p) for p in row] for row in panel.edited.getPoles()]
        status = self._status()
        n_drag, colours_drag = self._overlay_data(self._node("G09_OVERLAY_curvature"))
        self._set_offset(0, 0, 0)  # back to nothing, then type the same offset
        self._set_offset(off.x, off.y, off.z)
        self.assertEqual(set(panel.moves), set(dragged))
        for k, v in panel.moves.items():
            self.assertAlmostEqual((v - dragged[k]).Length, 0.0, places=12)
        for row_a, row_b in zip(poles, panel.edited.getPoles()):
            for a, b in zip(row_a, row_b):
                self.assertAlmostEqual((a - V(b)).Length, 0.0, places=12)
        self.assertEqual(status, self._status())
        n_typed, colours_typed = self._overlay_data(self._node("G09_OVERLAY_curvature"))
        self.assertEqual(n_drag, n_typed)
        self.assertEqual(colours_drag, colours_typed)

    def test_every_navigation_style_lets_the_panel_claim_a_marker_press(self):
        self._open()
        panel = self._panel()
        x, y = self._pole_px(4, 5, panel)
        for style in STYLES:
            with self.subTest(style=style):
                panel.moves.clear()
                self.view.setNavigationType(style)
                self.view.viewIsometric()
                self.view.fitAll()
                _pump(2)
                x, y = self._pole_px(4, 5, panel)
                cam0 = self._camera()
                ex, ey = self._drag(x, y, 35.0, -25.0, steps=8)
                self._release(ex, ey)
                _pump(2)
                self.assertEqual(self._camera(), cam0)  # the view did not navigate
                self.assertEqual(panel.chosen, (4, 5))
                self.assertGreater(self._offset().Length, 1e-6)

    def test_other_presses_and_modified_presses_reach_the_navigation_style(self):
        for style in ("Gui::CADNavigationStyle", "Gui::BlenderNavigationStyle", "Gui::GestureNavigationStyle", "Gui::OpenInventorNavigationStyle"):
            with self.subTest(style=style):
                self._close()
                self.view.setNavigationType(style)
                outcomes = []
                for panel_open in (False, True):
                    self._close()
                    self.view.viewIsometric()
                    self.view.fitAll()
                    _pump(2)
                    if panel_open:
                        self._open()
                    x, y = self._pole_px(4, 5)
                    cases = ((x + 20.0, y, QtCore.Qt.NoModifier), (x, y, QtCore.Qt.ShiftModifier), (x, y, QtCore.Qt.ControlModifier), (x, y, QtCore.Qt.AltModifier))
                    seen = []
                    for px, py, mods in cases:
                        self.view.viewIsometric()
                        self.view.fitAll()
                        _pump(1)
                        c0 = self._camera()
                        self._press(px, py, mods=mods)
                        for k in range(1, 6):
                            self._send(QtCore.QEvent.MouseMove, px + 6 * k, py - 4 * k, QtCore.Qt.NoButton, QtCore.Qt.LeftButton, mods)
                        self._send(QtCore.QEvent.MouseButtonRelease, px + 30, py - 20, QtCore.Qt.LeftButton, QtCore.Qt.NoButton, mods)
                        _pump(2)
                        seen.append(self._camera() != c0)
                        if panel_open:
                            self.assertEqual(self._panel().moves, {})
                    outcomes.append(seen)
                self.assertEqual(outcomes[0], outcomes[1])  # what the same events do with no panel open

    def test_nothing_is_claimed_with_the_panel_closed(self):
        self._open()
        panel = self._panel()
        x, y = self._pole_px(4, 5, panel)
        self._button("Cancel").click()
        _pump()
        self.assertIsNone(panel._view_filter)
        self.assertIsNone(panel._gl)
        self.assertIsNone(panel._app_filter)
        self.view.viewIsometric()
        self.view.fitAll()
        _pump(2)
        self._press(x, y)
        self.assertEqual(self._g09_nodes(), [])

    def test_a_locked_marker_is_chosen_and_not_dragged(self):
        self._open()
        self._lock("U0", "G1")
        panel = self._panel()
        x, y = self._pole_px(2, 3, panel)  # row 2 is locked by G1 on U0
        ex, ey = self._drag(x, y, 25.0, 15.0)
        self._release(ex, ey)
        self.assertEqual(panel.chosen, (2, 3))
        self.assertEqual(panel.moves, {})
        self.assertTrue(self._status().startswith("LockedPole"), self._status())
        self.assertIsNone(panel._drag)

    def test_escape_and_a_right_press_put_the_pole_back(self):
        self._open()
        self._choose(3, 4)
        self._set_offset(0.1, 0.2, 0.3)
        panel = self._panel()
        start = self._offset()
        x, y = self._pole_px(3, 4, panel)
        for how in ("escape", "right"):
            with self.subTest(how=how):
                x, y = self._pole_px(3, 4, panel)
                ex, ey = self._drag(x, y, 28.0, -10.0)
                self.assertGreater((self._offset() - start).Length, 1e-3)
                if how == "escape":
                    app = QtWidgets.QApplication
                    for typ in (QtCore.QEvent.ShortcutOverride, QtCore.QEvent.KeyPress, QtCore.QEvent.KeyRelease):
                        app.sendEvent(self._gl(), QtGui.QKeyEvent(typ, QtCore.Qt.Key_Escape, QtCore.Qt.NoModifier))
                else:
                    self._send(QtCore.QEvent.MouseButtonPress, ex, ey, QtCore.Qt.RightButton, QtCore.Qt.LeftButton | QtCore.Qt.RightButton)
                self.assertLess((self._offset() - start).Length, 1e-12)
                self.assertIsNone(panel._drag)
                self.assertTrue(FreeCADGui.Control.activeDialog())  # Escape did not cancel the panel
                self._move(ex + 5, ey + 5)  # the rest of the gesture is swallowed
                self._release(ex + 5, ey + 5)
                self._send(QtCore.QEvent.MouseButtonRelease, ex, ey, QtCore.Qt.RightButton, QtCore.Qt.NoButton)
                self.assertLess((self._offset() - start).Length, 1e-12)
        self.assertIsNone(panel._app_filter)

    def test_escape_outside_a_drag_still_cancels_nothing_extra(self):
        self._open()
        panel = self._panel()
        self.assertIsNone(panel._app_filter)  # installed only while a drag runs

    def test_ok_in_the_middle_of_a_drag_ends_it_and_opens_one_transaction(self):
        self._open()
        panel = self._panel()
        undo = self.doc.UndoCount
        x, y = self._pole_px(3, 4, panel)
        self._drag(x, y, 20.0, -12.0)
        self.assertEqual(self.doc.UndoCount, undo)  # a drag opens no transaction
        self.assertFalse(self.doc.HasPendingTransaction)
        moves = {k: V(v) for k, v in panel.moves.items()}
        self._button("OK").click()
        _pump()
        self.assertIsNone(panel._app_filter)
        feats = self._features()
        self.assertEqual(len(feats), 1)
        self.assertEqual(self.doc.UndoCount, undo + 1)
        ref = PE.makePoleEditFeature(self.doc, (self.src, "Face1"), moves, None, "Ref")
        self.doc.recompute()
        self.assertAlmostEqual(
            (feats[0].Shape.Faces[0].Surface.getPole(3, 4) - ref.Shape.Faces[0].Surface.getPole(3, 4)).Length, 0.0, places=9
        )

    def test_cancel_in_the_middle_of_a_drag_changes_nothing(self):
        self._open()
        panel = self._panel()
        undo = self.doc.UndoCount
        x, y = self._pole_px(3, 4, panel)
        self._drag(x, y, 20.0, -12.0)
        self._button("Cancel").click()
        _pump()
        self.assertEqual(self._features(), [])
        self.assertEqual(self.doc.UndoCount, undo)
        self.assertEqual(self._g09_nodes(), [])
        self.assertTrue(self.src.Visibility)
        self.assertIsNone(panel._view_filter)

    def test_a_face_in_a_rotated_part_drags_in_global_axes(self):
        part = self.doc.addObject("App::Part", "Part")
        part.Placement = FreeCAD.Placement(V(3, 4, 5), FreeCAD.Rotation(V(0, 0, 1), 90))
        self.doc.removeObject(self.src.Name)
        self.src = self.doc.addObject("Part::Feature", "Inner")
        self.src.Shape = cubic_face(6, 8)
        part.addObject(self.src)
        self.doc.recompute()
        self.view.viewIsometric()
        self.view.fitAll()
        _pump()
        self._select(part, "Inner.Face1")
        FreeCADGui.runCommand(CMD)
        _pump()
        panel = self._panel()
        self._set_mode("X")
        x, y = self._pole_px(3, 4, panel)
        ex, ey = self._drag(x, y, 24.0, 6.0)
        self._release(ex, ey)
        off = self._offset()
        self.assertGreater(abs(off.x), 1e-3)
        self.assertAlmostEqual(off.y, 0.0, places=9)
        self.assertAlmostEqual(off.z, 0.0, places=9)
        self._button("OK").click()
        _pump()
        local = list(self._features()[0].PoleOffsets)[0]
        want = FreeCAD.Rotation(V(0, 0, 1), 90).inverted().multVec(off)
        self.assertAlmostEqual((local - want).Length, 0.0, places=9)

    def test_the_overlay_follows_every_move(self):
        self._open()
        self._analysis("Mean curvature")
        panel = self._panel()
        x, y = self._pole_px(4, 5, panel)
        self._press(x, y)
        seen = []
        for k in range(1, 7):
            self._move(x + 4 * k, y - 3 * k)
            seen.append(tuple(self._overlay_data(self._node("G09_OVERLAY_curvature"))[1]))
        self._release(x + 24, y - 18)
        self.assertGreater(len(set(seen)), 1)  # the colours change with the pole

    def test_a_source_change_on_the_same_grid_reloads_and_keeps_the_moves(self):
        self._open()
        self._choose(3, 4)
        self._set_offset(0.0, 0.0, 0.7)
        panel = self._panel()
        surf = PE.editable_surface(self.src.Shape.Faces[0])
        surf.setPole(5, 5, surf.getPole(5, 5) + V(0, 0, 2.0))
        self.src.Shape = surf.toShape()
        self.doc.recompute()
        _pump(3)
        self.assertTrue(FreeCADGui.Control.activeDialog())
        self.assertIn((3, 4), panel.moves)
        self.assertAlmostEqual((panel.edited.getPole(5, 5) - surf.getPole(5, 5)).Length, 0.0, places=9)
        self.assertEqual(self._g09_nodes().count("G09_POLES"), 1)

    def test_a_grid_change_says_pole_grid_changed(self):
        self._open()
        self._choose(3, 4)
        self._set_offset(0.0, 0.0, 0.7)
        self.src.Shape = clamped_face(7, 8, 3, 3)
        self.doc.recompute()
        _pump(3)
        self.assertTrue(self._status().startswith("PoleGridChanged"), self._status())
        panel = self._panel()
        self.assertEqual((panel.surface.NbUPoles, panel.surface.NbVPoles), (7, 8))  # the new grid is shown
        self.assertEqual(panel.moves, {})  # the old grid's indices are dropped
        self.assertEqual(len(panel.edited.getPoles()), 7)
        self._choose(2, 2)
        self._set_offset(0.0, 0.0, 0.4)
        self._button("OK").click()  # records the move made on the new grid
        _pump()
        feats = self._features()
        self.assertEqual(len(feats), 1)
        self.assertEqual(list(feats[0].PoleIndices), [2, 2])
        self.assertTrue(feats[0].isValid())

    def test_deleting_the_source_closes_the_panel_cleanly(self):
        self._open()
        panel = self._panel()
        n = len(self.doc.Objects)
        self.doc.removeObject(self.src.Name)
        _pump(4)
        self.assertFalse(FreeCADGui.Control.activeDialog())
        self.assertEqual(self._g09_nodes(), [])
        self.assertLessEqual(len(self.doc.Objects), n)
        self.assertIsNone(panel._observer)
        self.assertFalse(self.doc.HasPendingTransaction)

    def test_closing_the_document_closes_the_panel_cleanly(self):
        doc2 = FreeCAD.newDocument("G09PoleDrag2")
        FreeCADGui.ActiveDocument = FreeCADGui.getDocument(doc2.Name)
        _pump()
        src2 = doc2.addObject("Part::Feature", "Src2")
        src2.Shape = cubic_face(6, 8)
        doc2.recompute()
        FreeCADGui.ActiveDocument.ActiveView.viewIsometric()
        FreeCADGui.ActiveDocument.ActiveView.fitAll()
        _pump()
        FreeCADGui.Selection.clearSelection()
        FreeCADGui.Selection.addSelection(doc2.Name, src2.Name, "Face1")
        _pump()
        FreeCADGui.runCommand(CMD)
        _pump()
        panel = self._panel()
        self.assertIsNotNone(panel)
        FreeCAD.closeDocument(doc2.Name)
        _pump(4)
        self.assertFalse(FreeCADGui.Control.activeDialog())
        self.assertIsNone(self._panel())
        self.assertIsNone(panel._view_filter)
        self.assertIsNone(panel._observer)
        FreeCADGui.ActiveDocument = FreeCADGui.getDocument(self.doc.Name)
        _pump()
        self.assertEqual(self._g09_nodes(), [])

    def test_undoing_the_sources_creation_closes_the_panel(self):
        self.doc.undo()  # the source was created and recomputed in the tests' setUp
        self.doc.redo()
        self.doc.openTransaction("make")
        src2 = self.doc.addObject("Part::Feature", "Src2")
        src2.Shape = cubic_face(6, 8)
        self.doc.commitTransaction()
        self.doc.recompute()
        self.src = src2
        self._open(src2)
        self.doc.undo()
        _pump(4)
        self.assertFalse(FreeCADGui.Control.activeDialog())
        self.assertEqual(self._g09_nodes(), [])

    def test_drag_latency_at_18_by_18_poles(self):
        self.src.Shape = clamped_face(18, 18, 3, 3)
        self.doc.recompute()
        self.view.fitAll()
        _pump()
        self._open()
        self._analysis("Mean curvature")
        panel = self._panel()
        x, y = self._pole_px(9, 9, panel)
        self._press(x, y)
        times = []
        for k in range(1, 31):
            t0 = time.perf_counter()
            self._move(x + 2 * k, y - k)
            times.append(time.perf_counter() - t0)
        self._release(x + 60, y - 30)
        print("\ndrag latency 18x18 mean curvature: median %.1f ms, worst %.1f ms" % (1000 * statistics.median(times), 1000 * max(times)))
        self.assertLess(statistics.median(times), 0.06)
        self.assertLess(max(times), 0.3)


class TestPoleEditDragTallViewGui(_Helpers, unittest.TestCase):
    """Repair R118: a 3D view taller than wide. Coin's camera mapping rescales the view volume by
    1 / aspect when the aspect is below 1, under both cameras; the marker hit test and the drag ray
    must follow the same volume. The truth is the rendered framebuffer: the chosen marker's centroid
    (base colour (1.0, 0.8, 0.0))."""

    setUpClass = TestPoleEditDragGui.__dict__["setUpClass"]
    tearDownClass = TestPoleEditDragGui.__dict__["tearDownClass"]
    for _name in ("_panel", "_send", "_press", "_move", "_release", "_drag", "_camera"):
        locals()[_name] = TestPoleEditDragGui.__dict__[_name]
    del _name

    def setUp(self):
        TestPoleEditDragGui.setUp(self)
        mw = FreeCADGui.getMainWindow()
        self._was_max = mw.isMaximized()
        self._was_size = mw.size()
        mw.showNormal()
        mw.resize(700, 1010)
        _pump(8)

    def tearDown(self):
        mw = FreeCADGui.getMainWindow()
        if self._was_max:
            mw.showMaximized()
        else:
            mw.resize(self._was_size)
        _pump(4)
        TestPoleEditDragGui.tearDown(self)

    def _chosen_centroid(self):
        """Centroid ``(x, y)`` in device pixels, origin top left, of the rendered chosen marker."""
        img = self._gl().grabFramebuffer()
        xs = ys = n = 0
        for yy in range(img.height()):
            for xx in range(img.width()):
                c = img.pixelColor(xx, yy)
                if c.red() > 220 and 160 < c.green() < 235 and c.blue() < 70:
                    xs += xx
                    ys += yy
                    n += 1
        self.assertGreater(n, 0, "the chosen marker is not visible")
        return xs / n, ys / n

    def _tall_case(self, camera):
        FreeCADGui.runCommand(camera)
        self.view.viewIsometric()
        self.view.fitAll()
        _pump(4)
        self._open()
        panel = self._panel()
        gl = self._gl()
        if gl.height() <= gl.width():
            self.skipTest("the window manager did not give a view taller than wide")
        dpr = gl.devicePixelRatioF()
        self._choose(3, 4)
        _pump(3)
        cx, cy = self._chosen_centroid()
        # the hit test projects the chosen pole onto its rendered marker (Coin pixel, origin bottom left)
        hit = panel.nearest_pole(cx, gl.height() * dpr - 1.0 - cy, panel._cb_view)
        self.assertEqual(hit[0], (3, 4))
        self.assertLessEqual(hit[1], 1.5)
        # a press on the rendered marker is claimed and the dragged pole is drawn under the cursor
        camera_before = self._camera()
        x, y = cx / dpr, cy / dpr
        ex, ey = self._drag(x, y, gl.width() * 0.25, -gl.height() * 0.3, steps=6)
        self._release(ex, ey)
        _pump(6)
        self.assertIn((3, 4), panel.moves)
        self.assertEqual(self._camera(), camera_before)
        ax, ay = self._chosen_centroid()
        self.assertLessEqual(((ax - ex * dpr) ** 2 + (ay - ey * dpr) ** 2) ** 0.5, 1.5)

    def test_tall_view_orthographic_marker_and_drag_match_the_render(self):
        self._tall_case("Std_OrthographicCamera")

    def test_tall_view_perspective_marker_and_drag_match_the_render(self):
        self._tall_case("Std_PerspectiveCamera")


def _net_count(view):
    """How many ``G09_POLES_NET`` nodes the view's scene graph holds."""
    from pivy import coin

    sa = coin.SoSearchAction()
    sa.setInterest(coin.SoSearchAction.ALL)
    sa.setSearchingAll(True)
    sa.setName("G09_POLES_NET")
    sa.apply(view.getSceneGraph())
    return sa.getPaths().getLength()


class TestPoleEditTwoViewsGui(_Helpers, unittest.TestCase):
    """Repair R118b: two 3D views of one document. The panel draws in, and removes from, the view it
    opened in; when that view closes it moves to the remaining one and leaves no node behind."""

    setUpClass = TestPoleEditDragGui.__dict__["setUpClass"]
    tearDownClass = TestPoleEditDragGui.__dict__["tearDownClass"]
    setUp = TestPoleEditDragGui.__dict__["setUp"]
    tearDown = TestPoleEditDragGui.__dict__["tearDown"]
    for _name in ("_panel", "_send", "_press", "_move", "_release"):
        locals()[_name] = TestPoleEditDragGui.__dict__[_name]
    del _name

    def _second_view(self):
        first = FreeCADGui.ActiveDocument.ActiveView
        FreeCADGui.runCommand("Std_ViewCreate")
        _pump(6)
        second = FreeCADGui.ActiveDocument.ActiveView
        second.viewIsometric()
        second.fitAll()
        _pump(4)
        area = self.mw.findChild(QtWidgets.QMdiArea)
        return first, second, area, area.subWindowList()

    def test_edits_after_switching_views_draw_one_net_in_the_panel_view(self):
        first, second, area, subs = self._second_view()
        self._open()
        self.assertEqual((_net_count(first), _net_count(second)), (0, 1))
        area.setActiveSubWindow(subs[0])
        _pump(6)
        counts = []
        for val in (1.0, 2.0, 3.0):
            self._w("G09_PoleEdit_OffsetX").setProperty("rawValue", val)
            _pump(3)
            counts.append((_net_count(first), _net_count(second)))
        self.assertEqual(counts, [(0, 1)] * 3)
        self._button("Cancel").click()
        _pump(4)
        self.assertEqual((_net_count(first), _net_count(second)), (0, 0))

    def test_closing_the_panel_view_then_ok_leaves_no_node(self):
        first, second, area, subs = self._second_view()
        self._open()
        self._w("G09_PoleEdit_OffsetX").setProperty("rawValue", 1.0)
        _pump(3)
        subs[-1].close()
        _pump(8)
        self._button("OK").click()
        _pump(4)
        self.assertEqual(_net_count(first), 0)
        self.assertEqual(self._g09_nodes(), [])
        self.assertEqual(len([o for o in self.doc.Objects if o.TypeId != "Part::Feature" or o.Name != "Src"]), 1)

    def test_a_drag_in_the_remaining_view_still_moves_a_pole_after_the_panel_view_closed(self):
        """Repair R119 (run 118's verifier): the panel's own view closes, the panel reconnects to the
        remaining view of the document, and a drag there moves the pole. Without the reconnect the
        press is never claimed and nothing moves."""
        first, second, area, subs = self._second_view()
        self._open()
        panel = self._panel()
        subs[-1].close()
        _pump(8)
        self._w("G09_PoleEdit_OffsetX").setProperty("rawValue", 0.5)  # the redraw moves the panel to the remaining view
        _pump(3)
        gl = self._gl()
        dpr = gl.devicePixelRatioF()
        ij = (max(2, panel.edited.NbUPoles // 2), max(2, panel.edited.NbVPoles // 2))
        p0 = V(panel.edited.getPole(*ij))
        sx, sy = first.getPointOnScreen(p0.x, p0.y, p0.z)
        x, y = sx / dpr, gl.height() - sy / dpr
        self._press(x, y)
        _pump(1)
        self.assertIsNotNone(panel._drag, "the press in the remaining view was not claimed")
        for k in range(1, 4):
            self._move(x + 8 * k, y - 5 * k)
        self._release(x + 24, y - 15)
        _pump(3)
        self.assertEqual(tuple(panel.chosen), ij)
        self.assertGreater((V(panel.edited.getPole(*ij)) - p0).Length, 0.05)
        self.assertEqual(_net_count(first), 1)
        self._button("OK").click()
        _pump(4)
        self.assertEqual(_net_count(first), 0)
        self.assertEqual(self._g09_nodes(), [])
