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
ISOPHOTE = "Surface_IsophoteAnalysis"
COMB = "Surface_CurvatureComb"
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

    # ---- repair R112: nested selection, placement, deleted target, failed showDialog
    def _placed(self, x, deg):
        return FreeCAD.Placement(V(x, 0, 0), FreeCAD.Rotation(V(0, 0, 1), deg))

    def _nested_box(self):
        part = self.doc.addObject("App::Part", "Part")
        part.Placement = self._placed(100, 90)
        box = self.doc.addObject("Part::Box", "NBox")
        part.addObject(box)
        box.Placement = self._placed(5, 0)
        self.doc.recompute()
        return part, box

    def _overlay_points(self, node):
        coords = _find_all(node, "SoCoordinate3")[0]
        return [V(*coords.point[i].getValue()) for i in range(coords.point.getNum())]

    def test_nested_face_selection_gives_that_face_only(self):
        part, box = self._nested_box()
        FreeCADGui.Selection.clearSelection()
        FreeCADGui.Selection.addSelection(self.doc.Name, part.Name, "NBox.Face6")
        _pump()
        import SurfaceAnalysisCommands as SC

        targets = SC.selected_targets()
        truth = Part.getShape(part, "NBox.Face6", needSubElement=True)
        self.assertEqual(len(targets), 1)
        self.assertIsInstance(targets[0], Part.Face)
        self.assertLess((targets[0].CenterOfMass - truth.CenterOfMass).Length, 1e-6)
        self.assertTrue(SC._has_analysable_selection())
        FreeCADGui.runCommand(CURVATURE)
        _pump()
        node = self._panel_overlay()
        for p in self._overlay_points(node):
            self.assertLess(Part.Vertex(p).distToShape(truth)[0], 1e-4)

    def test_nested_object_path_gives_placed_object_and_edges_are_ignored(self):
        part, box = self._nested_box()
        import SurfaceAnalysisCommands as SC

        FreeCADGui.Selection.clearSelection()
        FreeCADGui.Selection.addSelection(self.doc.Name, part.Name, "NBox.")
        got = SC.selected_targets()
        self.assertEqual(len(got), 1)
        truth = Part.getShape(part, "NBox.", needSubElement=False)
        self.assertLess((got[0].CenterOfMass - truth.CenterOfMass).Length, 1e-6)
        FreeCADGui.Selection.clearSelection()
        FreeCADGui.Selection.addSelection(self.doc.Name, part.Name, "NBox.Edge1")
        self.assertEqual(SC.selected_targets(), [])
        self.assertFalse(SC._has_analysable_selection())
        self.assertEqual([i["type"] for i in SC.selected_subshapes(("Edge",))], ["Edge"])

    def test_api_on_nested_objects_draws_where_they_are_shown(self):
        part, box = self._nested_box()
        inner = self.doc.addObject("App::Part", "Inner")
        inner.Placement = self._placed(0, 90)
        outer = self.doc.addObject("App::Part", "Outer")
        outer.Placement = self._placed(50, 90)
        outer.addObject(inner)
        leaf = self.doc.addObject("Part::Box", "Leaf")
        inner.addObject(leaf)
        leaf.Placement = self._placed(3, 0)
        self.doc.recompute()
        cases = ((box, part, "NBox."), (leaf, outer, "Inner.Leaf."))
        for obj, top, path in cases:
            truth = Part.getShape(top, path, needSubElement=False)
            for fn, kw in (("show_curvature", {}), ("show_draft", {"pull": (0, 0, 1)}), ("show_zebra", {})):
                h = getattr(SG, fn)(obj, view=self.view, **kw)
                for v in h.vertices:
                    self.assertLess(Part.Vertex(v["point"]).distToShape(truth)[0], 1e-6, (obj.Name, fn))
                SG.remove(h)

    def test_api_on_body_pad_and_link_in_placed_part(self):
        part = self.doc.addObject("App::Part", "PartB")
        part.Placement = self._placed(100, 90)
        body = self.doc.addObject("PartDesign::Body", "Body")
        part.addObject(body)
        body.Placement = self._placed(0, 0)
        body.Placement.Base = V(0, 20, 0)
        sk = body.newObject("Sketcher::SketchObject", "Sk")
        sk.AttachmentSupport = [(body.Origin.OriginFeatures[3], "")]
        sk.MapMode = "FlatFace"
        import Sketcher  # noqa: F401

        pts = ((0, 0), (10, 0), (10, 10), (0, 10))
        for i in range(4):
            a, b = pts[i], pts[(i + 1) % 4]
            sk.addGeometry(Part.LineSegment(V(a[0], a[1], 0), V(b[0], b[1], 0)))
        pad = body.newObject("PartDesign::Pad", "Pad")
        pad.Profile = sk
        pad.Length = 10
        src = self.doc.addObject("Part::Box", "LinkSrc")
        link = self.doc.addObject("App::Link", "Lnk")
        link.LinkedObject = src
        link.Placement = self._placed(0, 90)
        part.addObject(link)
        self.doc.recompute()
        for obj, path in ((pad, "Body.Pad."), (body, "Body."), (link, "Lnk.")):
            truth = Part.getShape(part, path, needSubElement=False)
            self.assertFalse(truth.isNull(), path)
            h = SG.show_curvature(obj, view=self.view)
            for v in h.vertices:
                self.assertLess(Part.Vertex(v["point"]).distToShape(truth)[0], 1e-6, path)
            SG.remove(h)

    def test_unnested_object_keeps_its_own_shape(self):
        self.box.Placement = self._placed(30, 30)
        self.assertIsNone(SG.parent_placement(self.box))
        self.assertTrue(SG.placed_shape(self.box).isSame(self.box.Shape))
        h = SG.show_zebra(self.box, view=self.view)
        for v in h.vertices:
            self.assertLess(Part.Vertex(v["point"]).distToShape(self.box.Shape)[0], 1e-6)
        SG.remove(h)

    def test_deleted_target_drops_overlay_and_panel_still_closes(self):
        for button in ("OK", "Cancel"):
            part, box = self._nested_box()
            FreeCADGui.Selection.clearSelection()
            FreeCADGui.Selection.addSelection(self.doc.Name, part.Name, "NBox.Face6")
            FreeCADGui.runCommand(CURVATURE)
            _pump()
            status = self._w("G09_AnalysisStatus")
            old = status.text()
            self.assertIn("target", old)
            self.assertIsNotNone(self._panel_overlay())
            self.doc.removeObject(part.Name)
            self.doc.removeObject("NBox")
            _pump()
            self.assertEqual(self._overlays(), [])
            self.assertEqual(self._legends(), [])
            self.assertNotEqual(status.text(), old)
            self.assertIn("deleted", status.text())
            self._w("G09_AnalysisType").setCurrentIndex(1)  # a later refresh must not raise or draw
            _pump()
            self.assertEqual(self._overlays(), [])
            self._button(button).click()
            _pump()
            self.assertFalse(FreeCADGui.Control.activeDialog())
            self.assertEqual(self.ufs.getNumChildren(), self.base)

    def test_failed_show_dialog_leaves_nothing_behind(self):
        FreeCADGui.Selection.addSelection(self.doc.Name, self.sphere.Name, "")
        before = self.ufs.getNumChildren()
        import types

        import SurfaceAnalysisCommands as SC

        def boom(panel):
            raise RuntimeError("no dialog")

        real = SC.Gui
        # the Control object cannot be patched, so the module's own FreeCADGui name gets a stand-in
        class _Gui:
            Control = types.SimpleNamespace(showDialog=staticmethod(boom), activeDialog=real.Control.activeDialog)

            def __getattr__(self, name):
                return getattr(real, name)

        SC.Gui = _Gui()
        try:
            with self.assertRaises(RuntimeError):
                SC._CurvatureCommand().Activated()
        finally:
            SC.Gui = real
        self.assertEqual(self.ufs.getNumChildren(), before)
        self.assertEqual(self._legends(), [])

    # ---------------------------------------------------------------- slice 2c

    def _all_points(self, node):
        out = []
        for coords in _find_all(node, "SoCoordinate3"):
            out.extend(tuple(coords.point[i].getValue()) for i in range(coords.point.getNum()))
        return out

    def test_new_commands_exist_placed_and_follow_active_rules(self):
        new = (ISOPHOTE, COMB)
        for name in new:
            res = FreeCADGui.Command.get(name).getInfo()
            self.assertTrue(res["menuText"] and res["toolTip"] and res["statusTip"] and res["pixmap"], name)
            self.assertFalse(res["shortcut"], name)
            self.assertFalse(QtGui.QIcon(":/icons/" + name + ".svg").isNull(), name)
        self.assertEqual(FreeCADGui.Command.get(ISOPHOTE).getInfo()["menuText"], "Isophote lines")
        self.assertEqual(FreeCADGui.Command.get(COMB).getInfo()["menuText"], "Curvature combs")
        tb = [t for t in self.mw.findChildren(QtWidgets.QToolBar) if t.objectName() == "Surface analysis"][0]
        in_tb = [a.objectName() for a in tb.actions() if a.objectName() in ALL + new]
        self.assertEqual(in_tb, [CURVATURE, DRAFT, ZEBRA, ISOPHOTE, COMB, CLEAR])
        surface = [a.menu() for a in self.mw.menuBar().actions() if a.text().replace("&", "") == "Surface"][0]
        sub = [a.menu() for a in surface.actions() if a.menu() and a.text().replace("&", "") == "Analysis"][0]
        self.assertEqual([a.objectName() for a in sub.actions() if a.objectName() in ALL + new],
                         [CURVATURE, DRAFT, ZEBRA, ISOPHOTE, COMB, CLEAR])

        def active(name):
            return FreeCADGui.Command.get(name).isActive()

        for name in new:
            self.assertFalse(active(name), name)
        FreeCADGui.Selection.addSelection(self.doc.Name, self.box.Name, "Edge1")
        self.assertTrue(active(COMB))
        self.assertFalse(active(ISOPHOTE))  # an edge alone is nothing for isophotes
        FreeCADGui.Selection.clearSelection()
        FreeCADGui.Selection.addSelection(self.doc.Name, self.sphere.Name)
        self.assertTrue(active(ISOPHOTE) and active(COMB))
        FreeCADGui.runCommand(ISOPHOTE)
        _pump()
        self.assertFalse(active(ISOPHOTE) or active(COMB))

    def test_panel_type_selector_holds_five_types_and_new_inputs_have_labels(self):
        self._open(COMB, [(self.box, "")])
        sel = self._w("G09_AnalysisType")
        self.assertEqual([sel.itemText(i) for i in range(sel.count())],
                         ["Curvature", "Draft", "Zebra stripes", "Isophote lines", "Curvature combs"])
        self.assertEqual(sel.currentText(), "Curvature combs")
        panel = self._w("G09_AnalysisPanel")
        buddies = {lbl.buddy(): lbl.text() for lbl in panel.findChildren(QtWidgets.QLabel) if lbl.buddy() is not None}
        for name, text in (("G09_IsophoteDirection", "Direction:"), ("G09_IsophoteCount", "Count:"),
                           ("G09_CombScale", "Scale:"), ("G09_CombSamples", "Samples:")):
            self.assertEqual(buddies.get(self._w(name)), text, name)
        self.assertEqual(self._w("G09_CombAutoScale").text(), "Automatic scale")
        self.assertTrue(self._w("G09_CombAutoScale").isChecked())
        self.assertFalse(self._w("G09_CombScale").isEnabled())

    def test_isophote_panel_matches_api(self):
        self._open(ISOPHOTE, [(self.sphere, "")])
        direction = self._w("G09_IsophoteDirection")
        count = self._w("G09_IsophoteCount")
        self.assertEqual([count.itemText(i) for i in range(count.count())], ["4", "6", "8", "12", "18"])
        self.assertEqual((direction.currentText(), count.currentText()), ("Z", "8"))
        self.assertEqual(self._overlays(), ["G09_OVERLAY_isophote"])
        for dname, d in (("X", (1, 0, 0)), ("Z", (0, 0, 1))):
            direction.setCurrentIndex(direction.findText(dname))
            for i in range(count.count()):
                count.setCurrentIndex(i)
                _pump(1)
                self.assertEqual(self._overlays(), ["G09_OVERLAY_isophote"])
                h = SG.show_isophotes(self.sphere, d, count=int(count.itemText(i)), view=self.view)
                a, b = self._all_points(self._panel_overlay()), self._all_points(h.node)
                self.assertEqual(len(a), len(b))
                for p, q in zip(a, b):
                    self.assertLess(max(abs(x - y) for x, y in zip(p, q)), 1e-9)
                SG.remove(h)

    def test_comb_panel_matches_api_and_scale_rules(self):
        edge = self.box.Shape.Edges[0]
        self._open(COMB, [(self.box, "Edge1")])
        self.assertEqual(self._overlays(), ["G09_OVERLAY_comb"])
        samples = self._w("G09_CombSamples")
        self.assertEqual([samples.itemText(i) for i in range(samples.count())], ["9", "17", "33", "65"])
        for i in range(samples.count()):
            samples.setCurrentIndex(i)
            _pump(1)
            self.assertEqual(len(self._overlays()), 1)
            h = SG.show_combs([edge], samples=int(samples.itemText(i)), view=self.view)
            self.assertEqual(self._all_points(self._panel_overlay()), self._all_points(h.node))
            SG.remove(h)
        auto = self._w("G09_CombAutoScale")
        spin = self._w("G09_CombScale")
        auto.setChecked(False)
        _pump(2)
        self.assertTrue(spin.isEnabled())
        spin.setValue(2.5)
        _pump(2)
        h = SG.show_combs([edge], scale=2.5, samples=int(samples.currentText()), view=self.view)
        self.assertEqual(self._all_points(self._panel_overlay()), self._all_points(h.node))
        SG.remove(h)
        auto.setChecked(True)
        _pump(2)
        self.assertFalse(spin.isEnabled())

    def test_comb_targets_edges_faces_objects_and_placed_edges(self):
        import SurfaceAnalysisCommands as SC

        def edge_count(selection):
            FreeCADGui.Selection.clearSelection()
            for obj, sub in selection:
                FreeCADGui.Selection.addSelection(self.doc.Name, obj.Name, sub)
            _pump()
            panel = SC.AnalysisPanel(SC.COMB)
            n = len(panel.edge_targets)
            panel.reject()
            return n

        self.assertEqual(edge_count([(self.box, "Edge1"), (self.box, "Edge2")]), 2)
        self.assertEqual(edge_count([(self.box, "Face2")]), len(self.box.Shape.Faces[1].Edges))
        self.assertEqual(edge_count([(self.box, "")]), len(self.box.Shape.Edges))
        part, nbox = self._nested_box()
        FreeCADGui.Selection.clearSelection()
        FreeCADGui.Selection.addSelection(self.doc.Name, part.Name, "NBox.Edge1")
        _pump()
        panel = SC.AnalysisPanel(SC.COMB)
        truth = Part.getShape(part, "NBox.Edge1", needSubElement=True)
        self.assertEqual(len(panel.edge_targets), 1)
        self.assertLess((panel.edge_targets[0].CenterOfMass - truth.CenterOfMass).Length, 1e-6)
        panel.reject()

    def test_new_panel_types_ok_cancel_and_no_side_effects(self):
        self.doc.openTransaction("probe")
        self.doc.commitTransaction()
        undo, objs = self.doc.UndoCount, len(self.doc.Objects)
        modified = FreeCADGui.getDocument(self.doc.Name).Modified
        for command, name in ((ISOPHOTE, "isophote"), (COMB, "comb")):
            self._open(command, [(self.box, "")])
            self.assertEqual(self._overlays(), ["G09_OVERLAY_" + name])
            self._button("OK").click()
            _pump()
            self.assertFalse(FreeCADGui.Control.activeDialog())
            self.assertEqual(self._overlays(), ["G09_OVERLAY_" + name])
            self.assertEqual(self._legends(), [])
            SG.clear(self.view)
            self._open(command, [(self.box, "")])
            self._button("Cancel").click()
            _pump()
            self.assertEqual(self.ufs.getNumChildren(), self.base)
        self.assertEqual((self.doc.UndoCount, len(self.doc.Objects)), (undo, objs))
        self.assertEqual(FreeCADGui.getDocument(self.doc.Name).Modified, modified)
