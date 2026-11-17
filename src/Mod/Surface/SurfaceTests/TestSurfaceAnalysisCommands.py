# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI tests of the Surface analysis commands, task panel and colour legend (slice 2b).

Run with ``FreeCAD -t TestSurfaceGui`` (needs the GUI binary).
"""

import math
import time
import unittest

import FreeCAD
import FreeCADGui
import Part
from PySide import QtGui, QtWidgets

import SurfaceAnalysisGui as SG
from SurfaceTests.TestSurfaceAnalysisGui import _colors, _pump, _ramp

V = FreeCAD.Vector

CURVATURE = "Surface_CurvatureAnalysis"
DRAFT = "Surface_DraftAnalysis"
ZEBRA = "Surface_ZebraAnalysis"
CLEAR = "Surface_ClearAnalysis"
ALL = (CURVATURE, DRAFT, ZEBRA, CLEAR)


def _names(ufs):
    return [ufs.getChild(i).getName().getString() for i in range(ufs.getNumChildren())]


def _find_all(root, type_name):
    from pivy import coin

    sa = coin.SoSearchAction()
    sa.setType(coin.SoType.fromName(type_name))
    sa.setInterest(coin.SoSearchAction.ALL)
    sa.apply(root)
    return [p.getTail() for p in sa.getPaths()]


def _tex(node):
    tc = _find_all(node, "SoTextureCoordinate2")[0]
    return [tuple(x) for x in tc.point.getValues(0)][: tc.point.getNum()]


def _texts(node):
    return [str(t.string[0]) for t in _find_all(node, "SoText2")]


class TestSurfaceAnalysisCommands(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        FreeCADGui.activateWorkbench("SurfaceWorkbench")
        _pump()

    def setUp(self):
        self.doc = FreeCAD.newDocument("G09Cmd")
        self.sphere = self.doc.addObject("Part::Feature", "Sphere")
        self.sphere.Shape = Part.makeSphere(10)
        box = Part.makeBox(20, 20, 10).cut(Part.makeCylinder(4, 10, V(10, 10, 0)))
        self.box = self.doc.addObject("Part::Feature", "Box")
        self.box.Shape = box
        self.doc.recompute()
        FreeCADGui.ActiveDocument = FreeCADGui.getDocument(self.doc.Name)
        _pump()
        self.view = FreeCADGui.ActiveDocument.ActiveView
        self.assertIsNotNone(self.view, "no 3D view")
        self.ufs = SG._find_selection_node(self.view)
        self.base = self.ufs.getNumChildren()
        FreeCADGui.Selection.clearSelection()

    def tearDown(self):
        self._close_panel()
        FreeCADGui.Selection.clearSelection()
        if self.view is not None:
            SG.clear(self.view)
        FreeCAD.closeDocument(self.doc.Name)

    # ---- helpers
    @property
    def mw(self):
        return FreeCADGui.getMainWindow()

    def _close_panel(self):
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

    def _open(self, command, selection):
        FreeCADGui.Selection.clearSelection()
        for obj, sub in selection:
            FreeCADGui.Selection.addSelection(self.doc.Name, obj.Name, sub)
        _pump()
        FreeCADGui.runCommand(command)
        _pump()
        self.assertTrue(FreeCADGui.Control.activeDialog())

    def _overlays(self):
        return [n for n in _names(self.ufs) if n.startswith("G09_OVERLAY_")]

    def _legends(self):
        return [n for n in _names(self.ufs) if n.startswith("G09_LEGEND_")]

    def _panel_overlay(self):
        for i in range(self.ufs.getNumChildren()):
            ch = self.ufs.getChild(i)
            if ch.getName().getString().startswith("G09_OVERLAY_"):
                return ch
        return None

    def _assert_same_as_api(self, handle):
        node = self._panel_overlay()
        self.assertIsNotNone(node)
        self.assertEqual(node.getName().getString(), "G09_OVERLAY_" + handle.kind)
        got, want = _colors(node), _colors(handle.node)
        self.assertEqual(len(got), len(want))
        for a, b in zip(got, want):
            for k in range(3):
                self.assertAlmostEqual(a[k], b[k], delta=1e-9)

    # ---- check 1: commands
    def test_commands_exist_with_resources_and_placement(self):
        for name in ALL:
            cmd = FreeCADGui.Command.get(name)
            self.assertIsNotNone(cmd, name)
            res = cmd.getInfo()
            self.assertTrue(res["menuText"], name)
            self.assertTrue(res["toolTip"], name)
            self.assertTrue(res["statusTip"], name)
            self.assertTrue(res["pixmap"], name)
            self.assertFalse(res["shortcut"], name)
            self.assertFalse(QtGui.QIcon(":/icons/" + name + ".svg").isNull(), name)
        tbs = [t for t in self.mw.findChildren(QtWidgets.QToolBar) if t.objectName() == "Surface analysis"]
        self.assertEqual(len(tbs), 1)
        in_tb = [a.objectName() for a in tbs[0].actions()]
        self.assertEqual([n for n in in_tb if n in ALL], list(ALL))
        surface = None
        for act in self.mw.menuBar().actions():
            if act.text().replace("&", "") == "Surface":
                surface = act.menu()
        self.assertIsNotNone(surface)
        sub = [a.menu() for a in surface.actions() if a.menu() and a.text().replace("&", "") == "Analysis"]
        self.assertEqual(len(sub), 1)
        self.assertEqual([a.objectName() for a in sub[0].actions() if a.objectName() in ALL], list(ALL))

    def test_active_rules_in_four_contexts(self):
        def active(name):
            return FreeCADGui.Command.get(name).isActive()

        # a document, empty selection
        for name in (CURVATURE, DRAFT, ZEBRA):
            self.assertFalse(active(name), name)
        self.assertTrue(active(CLEAR))
        # a shape selected, and a face of one
        FreeCADGui.Selection.addSelection(self.doc.Name, self.sphere.Name)
        for name in (CURVATURE, DRAFT, ZEBRA):
            self.assertTrue(active(name), name)
        FreeCADGui.Selection.clearSelection()
        FreeCADGui.Selection.addSelection(self.doc.Name, self.box.Name, "Face1")
        self.assertTrue(active(CURVATURE))
        # a task dialog open
        FreeCADGui.runCommand(CURVATURE)
        _pump()
        for name in (CURVATURE, DRAFT, ZEBRA):
            self.assertFalse(active(name), name)
        self._close_panel()
        # no document
        FreeCAD.closeDocument(self.doc.Name)
        _pump()
        self.view = None
        for name in ALL:
            self.assertFalse(active(name), name)
        self.doc = FreeCAD.newDocument("G09Cmd")  # tearDown closes it

    # ---- check 2: panel to overlay
    def test_curvature_panel_matches_api_for_every_kind_and_range(self):
        face = self.box.Shape.Faces[1]
        for obj, sub, target in ((self.sphere, None, self.sphere), (self.box, "Face2", face)):
            self._open(CURVATURE, [(obj, sub or "")])
            kind = self._w("G09_CurvatureKind")
            self.assertEqual(self._overlays(), ["G09_OVERLAY_curvature"])
            for i, key in enumerate(("gauss", "mean", "kmax", "kmin")):
                kind.setCurrentIndex(i)
                _pump(2)
                self.assertEqual(len(self._overlays()), 1)
                h = SG.show_curvature(target, key, view=self.view)
                self._assert_same_as_api(h)
                SG.remove(h)
            auto = self._w("G09_AutoRange")
            self.assertTrue(auto.isChecked())
            self.assertFalse(self._w("G09_RangeLow").isEnabled())
            auto.setChecked(False)
            _pump(2)
            self.assertTrue(self._w("G09_RangeLow").isEnabled())
            self.assertTrue(self._w("G09_RangeHigh").isEnabled())
            self._w("G09_RangeLow").setValue(-0.05)
            self._w("G09_RangeHigh").setValue(0.02)
            _pump(2)
            h = SG.show_curvature(target, "kmin", range=(-0.05, 0.02), view=self.view)
            self.assertEqual(h.range, (-0.05, 0.02))
            self._assert_same_as_api(h)
            SG.remove(h)
            self.assertEqual(len(self._overlays()), 1)
            self._close_panel()
            self.assertEqual(self.ufs.getNumChildren(), self.base)

    def test_draft_panel_matches_api(self):
        self._open(DRAFT, [(self.box, "Face3")])
        pull = self._w("G09_DraftPull")
        spin = self._w("G09_DraftMinAngle")
        target = self.box.Shape.Faces[2]
        self.assertEqual(pull.currentText(), "+Z")
        self.assertAlmostEqual(float(spin.property("rawValue")), 1.0)
        self.assertEqual(self.ufs.getChild(self.ufs.getNumChildren() - 2).getName().getString(), "G09_OVERLAY_draft")
        directions = {"+X": (1, 0, 0), "-X": (-1, 0, 0), "+Y": (0, 1, 0), "-Y": (0, -1, 0), "+Z": (0, 0, 1), "-Z": (0, 0, -1)}
        for i in range(pull.count()):
            pull.setCurrentIndex(i)
            _pump(2)
            h = SG.show_draft(target, directions[pull.itemText(i)], 1.0, view=self.view)
            self._assert_same_as_api(h)
            SG.remove(h)
        for deg in (0.0, 3.0, 45.0):
            spin.setProperty("rawValue", deg)
            _pump(2)
            h = SG.show_draft(target, (0, 0, -1), deg, view=self.view)
            self._assert_same_as_api(h)
            SG.remove(h)
            self.assertEqual(len(self._overlays()), 1)

    def test_zebra_panel_matches_api(self):
        self._open(ZEBRA, [(self.sphere, "")])
        direction = self._w("G09_ZebraDirection")
        bands = self._w("G09_ZebraBands")
        self.assertEqual([bands.itemText(i) for i in range(bands.count())], ["4", "8", "16", "32", "64"])
        self.assertEqual(bands.currentText(), "16")
        for dname, d in (("X", (1, 0, 0)), ("Y", (0, 1, 0)), ("Z", (0, 0, 1))):
            direction.setCurrentIndex(direction.findText(dname))
            for i in range(bands.count()):
                bands.setCurrentIndex(i)
                _pump(1)
                self.assertEqual(len(self._overlays()), 1)
                h = SG.show_zebra(self.sphere, d, int(bands.itemText(i)), view=self.view)
                node = self._panel_overlay()
                self.assertEqual(node.getName().getString(), "G09_OVERLAY_zebra")
                a = _tex(node)
                b = _tex(h.node)
                self.assertEqual(len(a), len(b))
                self.assertEqual(a, b)
                SG.remove(h)

    def test_type_selector_set_by_command_and_one_overlay(self):
        self._open(DRAFT, [(self.sphere, "")])
        self.assertEqual(self._w("G09_AnalysisType").currentText(), "Draft")
        self.assertEqual(self._overlays(), ["G09_OVERLAY_draft"])
        self._w("G09_AnalysisType").setCurrentIndex(0)
        _pump(2)
        self.assertEqual(self._overlays(), ["G09_OVERLAY_curvature"])
        self.assertEqual(self._legends(), ["G09_LEGEND_curvature"])
        self._w("G09_AnalysisType").setCurrentIndex(2)
        _pump(2)
        self.assertEqual(self._overlays(), ["G09_OVERLAY_zebra"])
        self.assertEqual(self._legends(), [])

    def test_every_input_has_a_label_buddy(self):
        self._open(CURVATURE, [(self.sphere, "")])
        panel = self._w("G09_AnalysisPanel")
        buddies = {lbl.buddy() for lbl in panel.findChildren(QtWidgets.QLabel) if lbl.buddy() is not None}
        for name in (
            "G09_AnalysisType", "G09_CurvatureKind", "G09_RangeLow", "G09_RangeHigh",
            "G09_DraftPull", "G09_DraftMinAngle", "G09_ZebraDirection", "G09_ZebraBands",
        ):
            self.assertIn(self._w(name), buddies, name)
        self.assertEqual([b.text() for b in panel.findChildren(QtWidgets.QPushButton)], [])

    # ---- check 3: OK, Cancel, side effects
    def test_ok_keeps_overlay_and_legend_cancel_removes_them(self):
        self.doc.openTransaction("probe")
        self.doc.commitTransaction()
        undo, objs = self.doc.UndoCount, len(self.doc.Objects)
        props = {o.Name: {p: str(getattr(FreeCADGui.getDocument(self.doc.Name).getObject(o.Name), p)) for p in ("Visibility", "ShapeColor", "Transparency")} for o in self.doc.Objects}
        modified = FreeCADGui.getDocument(self.doc.Name).Modified
        for command in (CURVATURE, DRAFT):
            self._open(command, [(self.sphere, ""), (self.box, "")])
            self.assertEqual(len(self._overlays()), 1)
            self._button("OK").click()
            _pump()
            self.assertFalse(FreeCADGui.Control.activeDialog())
            self.assertEqual(len(self._overlays()), 1)
            self.assertEqual(len(self._legends()), 1)
            self.assertEqual(self.ufs.getNumChildren(), self.base + 2)
            SG.clear(self.view)
            self.assertEqual(self.ufs.getNumChildren(), self.base)
            self._open(command, [(self.sphere, "")])
            self._button("Cancel").click()
            _pump()
            self.assertFalse(FreeCADGui.Control.activeDialog())
            self.assertEqual(self.ufs.getNumChildren(), self.base)
        self.assertEqual(self.doc.UndoCount, undo)
        self.assertEqual(len(self.doc.Objects), objs)
        self.assertEqual(FreeCADGui.getDocument(self.doc.Name).Modified, modified)
        now = {o.Name: {p: str(getattr(FreeCADGui.getDocument(self.doc.Name).getObject(o.Name), p)) for p in ("Visibility", "ShapeColor", "Transparency")} for o in self.doc.Objects}
        self.assertEqual(now, props)

    def test_clear_command(self):
        h1 = SG.show_curvature(self.sphere, view=self.view)
        h2 = SG.show_zebra(self.sphere, view=self.view)
        self.assertEqual(self.ufs.getNumChildren(), self.base + 3)
        FreeCADGui.runCommand(CLEAR)
        _pump()
        self.assertEqual(self.ufs.getNumChildren(), self.base)
        SG.remove(h1)
        SG.remove(h2)

    # ---- check 5: legend lifecycle and labels
    def test_legend_lifecycle_and_labels(self):
        h = SG.show_curvature(self.sphere, "kmax", range=(-1.0, 3.0), view=self.view)
        self.assertEqual(self.ufs.getNumChildren(), self.base + 2)
        self.assertEqual(h.legend.getName().getString(), "G09_LEGEND_curvature")
        self.assertEqual(
            _texts(h.legend)[:3], ["%.3g" % -1.0, "%.3g" % 1.0, "%.3g" % 3.0]
        )
        self.assertIn("Max principal curvature", _texts(h.legend))
        SG.remove(h)
        self.assertEqual(self.ufs.getNumChildren(), self.base)
        SG.remove(h)  # second call: no-op
        self.assertEqual(self.ufs.getNumChildren(), self.base)

        a = SG.show_curvature(self.sphere, view=self.view)
        lo, hi = a.range
        self.assertEqual(_texts(a.legend)[:3], ["%.3g" % lo, "%.3g" % ((lo + hi) / 2), "%.3g" % hi])
        b = SG.show_draft(self.sphere, (0, 0, 1), 2.5, view=self.view)
        self.assertEqual(b.legend.getName().getString(), "G09_LEGEND_draft")
        texts = _texts(b.legend)
        for s in ("Positive", "Negative", "Insufficient"):
            self.assertIn(s, texts)
        self.assertTrue(any("2.5" in t for t in texts))
        self.assertEqual(self.ufs.getNumChildren(), self.base + 4)
        SG.clear(self.view)
        self.assertEqual(self.ufs.getNumChildren(), self.base)

    def test_legend_off_and_zebra_has_none(self):
        h = SG.show_curvature(self.sphere, view=self.view, legend=False)
        self.assertIsNone(h.legend)
        d = SG.show_draft(self.sphere, (0, 0, 1), view=self.view, legend=False)
        self.assertIsNone(d.legend)
        z = SG.show_zebra(self.sphere, view=self.view)
        self.assertIsNone(z.legend)
        self.assertEqual(self.ufs.getNumChildren(), self.base + 3)
        self.assertEqual(self._legends(), [])
        SG.clear(self.view)

    def test_legend_is_screen_space_unlit_unpickable(self):
        from pivy import coin

        h = SG.show_curvature(self.sphere, view=self.view)
        self.assertEqual(len(_find_all(h.legend, "SoOrthographicCamera")), 1)
        pick = _find_all(h.legend, "SoPickStyle")[0]
        self.assertEqual(pick.style.getValue(), coin.SoPickStyle.UNPICKABLE)
        light = _find_all(h.legend, "SoLightModel")[0]
        self.assertEqual(light.model.getValue(), coin.SoLightModel.BASE_COLOR)
        # the strip: five stops, low at the bottom, in the frozen box
        strip = _find_all(h.legend, "SoIndexedFaceSet")[0]
        coords = _find_all(h.legend, "SoCoordinate3")[0]
        pts = [tuple(p.getValue()) for p in coords.point.getValues(0)][: coords.point.getNum()]
        ys = sorted({round(p[1], 9) for p in pts})
        self.assertAlmostEqual(ys[0], 0.10)
        self.assertAlmostEqual(ys[-1], 0.90)
        self.assertGreaterEqual(min(p[0] for p in pts), 0.88)
        self.assertLessEqual(max(p[0] for p in pts) - min(p[0] for p in pts), 0.12)
        mat = _find_all(h.legend, "SoMaterial")[0]
        cols = [tuple(c.getValue()) for c in mat.diffuseColor.getValues(0)][: mat.diffuseColor.getNum()]
        for k in range(5):
            self.assertEqual(cols[2 * k], _ramp(k / 4.0))
        self.assertGreater(strip.coordIndex.getNum(), 0)
        SG.remove(h)
