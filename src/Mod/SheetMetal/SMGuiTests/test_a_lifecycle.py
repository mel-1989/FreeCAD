# SPDX-License-Identifier: LGPL-2.1-or-later
"""Slice 6b checks 4 and 6 (lane A): transactions, editing through the view providers, commands, icons, AddBase."""

import os
import unittest

import FreeCAD
import FreeCADGui as Gui

import SheetMetalConvertSolid as CS
import SheetMetalEdgeFlange as EF
import SheetMetalHemFeature as HM
import SheetMetalJog as JG
import SMEdgeCommands as C
import SMEdgePanels as P
import SMEdgeViewProviders as VP

from . import fixture_a as F
from .test_a_panels import Base

REL = 1e-9


def flange_panel(doc):
    base = F.plate(doc)
    return P.EdgeFlangePanel(doc, base=base, edges=[F.end_edge(base, "+x")])


def hem_panel(doc):
    base = F.plate(doc)
    return P.HemPanel(doc, base=base, edges=[F.end_edge(base, "+x")])


def jog_panel(doc):
    base = F.plate(doc)
    return P.JogPanel(doc, base=base, face=F.face_with_normal(base, 2, 1), sketch=F.line_sketch(doc, 30.0, 2.0))


def convert_panel(doc):
    src = F.box_source(doc)
    fixed, bends, opens = F.tray(src)
    return P.ConvertSolidPanel(doc, source=src, fixed=fixed, bends=bends, opens=opens)


MAKERS = {"edgeflange": flange_panel, "hem": hem_panel, "jog": jog_panel, "convert": convert_panel}


class TestTransactions(Base):
    def fresh(self, make):
        F.close_everything()
        self.doc = F.new_doc("Panel")
        FreeCAD.setActiveDocument(self.doc.Name)
        panel = make(self.doc)
        self.doc.recompute()
        return panel

    def test_cancel_changes_nothing(self):
        for kind, make in MAKERS.items():
            with self.subTest(kind):
                panel = self.fresh(make)
                before = F.snapshot(self.doc)
                self.assertTrue(panel.reject())
                self.assertEqual(F.snapshot(self.doc), before)

    def test_ok_is_one_transaction_and_one_undo_removes_the_feature(self):
        for kind, make in MAKERS.items():
            with self.subTest(kind):
                panel = self.fresh(make)
                self.doc.openTransaction("scene")
                self.doc.commitTransaction()
                n0, u0 = len(self.doc.Objects), self.doc.UndoCount
                self.assertTrue(panel.accept(), panel.error_text())
                self.assertEqual(len(self.doc.Objects) - n0, 1)
                self.assertEqual(self.doc.UndoCount, u0 + 1)
                self.doc.undo()
                self.assertEqual(len(self.doc.Objects), n0)
                self.assertEqual(self.doc.UndoCount, u0)
                self.doc.redo()
                self.assertEqual(len(self.doc.Objects), n0 + 1)

    def test_a_panel_edit_that_breaks_a_dependent_is_refused(self):
        base = F.plate(self.doc)
        flange = EF.make_edge_flange(self.doc, base, [F.end_edge(base, "+x")], length=15.0, radius=2.0)
        VP.attach(flange)
        tip = F.tip_edge(flange)
        hem = HM.make_hem(self.doc, flange, [tip], length=6.0)
        self.doc.recompute()
        self.assertNotIn("Invalid", hem.State)
        panel = P.EdgeFlangePanel(self.doc, obj=flange)
        panel.angle.setValue(150.0)    # a valid flange, but the hem's edge no longer lies on the end of the sheet
        before = F.snapshot(self.doc)
        with F.capture_output() as cap:
            ok = panel.accept()
        self.assertFalse(ok)
        self.assertIn("Hem", panel.error_text())
        self.assertEqual(F.snapshot(self.doc), before)
        self.assertEqual(flange.angle.Value, 90.0)
        self.assertNotIn("Traceback", cap.text)


class TestEditThroughViewProviders(Base):
    def test_every_feature_gets_its_view_provider_and_icon(self):
        for kind, make in MAKERS.items():
            with self.subTest(kind):
                F.close_everything()
                self.doc = F.new_doc("Panel")
                FreeCAD.setActiveDocument(self.doc.Name)
                panel = make(self.doc)
                self.assertTrue(panel.accept(), panel.error_text())
                obj = self.doc.Objects[-1]
                proxy = obj.ViewObject.Proxy
                self.assertEqual(type(proxy).__name__, {"edgeflange": "EdgeFlangeVP", "hem": "HemVP", "jog": "JogVP",
                                                       "convert": "ConvertSolidVP"}[kind])
                self.assertTrue(os.path.isfile(proxy.getIcon()), proxy.getIcon())

    def open_edit(self, obj):
        Gui.getDocument(obj.Document.Name).setEdit(obj.Name)
        panel = P.current()
        self.assertIsNotNone(panel, "setEdit opened no panel for %s" % obj.Name)
        self.assertEqual(panel.obj.Name, obj.Name)
        return panel

    def test_edge_flange_edit_is_prefilled_and_recomputes(self):
        panel = flange_panel(self.doc)
        panel.length.setValue(18.0)
        panel.angle.setValue(75.0)
        panel.radius.setValue(3.0)
        panel.flip.setChecked(True)
        self.assertTrue(panel.accept(), panel.error_text())
        obj = self.doc.getObject("EdgeFlange")
        ep = self.open_edit(obj)
        self.assertIsInstance(ep, P.EdgeFlangePanel)
        self.assertEqual((ep.length.value(), ep.angle.value(), ep.radius.value(), ep.flip.isChecked()), (18.0, 75.0, 3.0, True))
        self.assertIn("Edge", ep.sel_label.text())
        ep.length.setValue(25.0)
        ep.flip.setChecked(False)
        self.assertTrue(ep.accept(), ep.error_text())
        ref = F.new_doc("Ref")
        rb = F.plate(ref)
        want = EF.make_edge_flange(ref, rb, [F.end_edge(rb, "+x")], length=25.0, angle=75.0, radius=3.0)
        self.assertLessEqual(abs(obj.Shape.Volume - want.Shape.Volume), REL * want.Shape.Volume)
        self.assertEqual(obj.length.Value, 25.0)

    def test_hem_edit_is_prefilled_and_recomputes(self):
        panel = hem_panel(self.doc)
        panel.hem_type.setCurrentIndex(panel.hem_type.findData("Open"))
        panel.gap.setValue(5.0)
        panel.length.setValue(14.0)
        self.assertTrue(panel.accept(), panel.error_text())
        obj = self.doc.getObject("Hem")
        ep = self.open_edit(obj)
        self.assertEqual((ep.hem_type.currentData(), ep.gap.value(), ep.length.value()), ("Open", 5.0, 14.0))
        ep.length.setValue(9.0)
        self.assertTrue(ep.accept(), ep.error_text())
        ref = F.new_doc("Ref")
        rb = F.plate(ref)
        want = HM.make_hem(ref, rb, [F.end_edge(rb, "+x")], hemType="Open", gap=5.0, length=9.0)
        self.assertLessEqual(abs(obj.Shape.Volume - want.Shape.Volume), REL * want.Shape.Volume)

    def test_jog_edit_is_prefilled_and_recomputes(self):
        panel = jog_panel(self.doc)
        panel.offset.setValue(7.0)
        panel.angle.setValue(60.0)
        self.assertTrue(panel.accept(), panel.error_text())
        obj = self.doc.getObject("Jog")
        ep = self.open_edit(obj)
        self.assertEqual((ep.offset.value(), ep.angle.value()), (7.0, 60.0))
        self.assertIn(obj.sketch.Label, ep.sketch_label.text())
        ep.offset.setValue(9.0)
        self.assertTrue(ep.accept(), ep.error_text())
        ref = F.new_doc("Ref")
        rb = F.plate(ref)
        want = JG.make_jog(ref, rb, F.face_with_normal(rb, 2, 1), F.line_sketch(ref, 30.0, 2.0), offset=9.0, angle=60.0)
        self.assertLessEqual(abs(obj.Shape.Volume - want.Shape.Volume), REL * want.Shape.Volume)

    def test_convert_edit_is_prefilled_and_recomputes(self):
        panel = convert_panel(self.doc)
        panel.thickness.setValue(1.5)
        panel.radius.setValue(1.5)
        self.assertTrue(panel.accept(), panel.error_text())
        obj = self.doc.getObject("ConvertSolid")
        ep = self.open_edit(obj)
        self.assertEqual(ep._items(ep.fixed), list(obj.baseObject[1]))
        self.assertEqual(ep._items(ep.bends), list(obj.bendEdges[1]))
        self.assertEqual(ep._items(ep.opens), list(obj.openFaces[1]))
        self.assertEqual((ep.thickness.value(), ep.radius.value()), (1.5, 1.5))
        ep.thickness.setValue(2.0)
        ep.radius.setValue(2.0)
        self.assertTrue(ep.accept(), ep.error_text())
        ref = F.new_doc("Ref")
        rs = F.box_source(ref)
        f, b, o = F.tray(rs)
        want = CS.make_convert_solid(ref, rs, f, b, o, thickness=2.0, radius=2.0)
        self.assertLessEqual(abs(obj.Shape.Volume - want.Shape.Volume), REL * want.Shape.Volume)

    def test_cancelling_an_edit_leaves_the_properties_and_edit_mode(self):
        panel = flange_panel(self.doc)
        self.assertTrue(panel.accept())
        obj = self.doc.getObject("EdgeFlange")
        vol, length = obj.Shape.Volume, obj.length.Value
        ep = self.open_edit(obj)
        ep.length.setValue(40.0)
        before = F.snapshot(self.doc)
        self.assertTrue(ep.reject())
        self.assertEqual(F.snapshot(self.doc), before)
        self.assertEqual((obj.length.Value, obj.Shape.Volume), (length, vol))
        self.assertIsNone(Gui.getDocument(self.doc.Name).getInEdit())

    def test_closing_the_dialog_directly_leaves_edit_mode(self):
        panel = hem_panel(self.doc)
        self.assertTrue(panel.accept())
        obj = self.doc.getObject("Hem")
        self.open_edit(obj)
        self.assertIsNotNone(Gui.getDocument(self.doc.Name).getInEdit())
        Gui.Control.closeDialog()
        for _ in range(3):
            from PySide import QtWidgets

            QtWidgets.QApplication.processEvents()
        self.assertIsNone(Gui.getDocument(self.doc.Name).getInEdit())

    def test_accepting_a_panel_whose_object_was_deleted_is_a_readable_refusal(self):
        panel = jog_panel(self.doc)
        self.assertTrue(panel.accept())
        obj = self.doc.getObject("Jog")
        ep = P.JogPanel(self.doc, obj=obj)    # not shown: removing an object under edit closes its dialog by itself
        ep.offset.setValue(8.0)
        self.doc.removeObject("Jog")
        before = F.snapshot(self.doc)
        with F.capture_output() as cap:
            ok = ep.accept()
        self.assertFalse(ok)
        self.assertIn("deleted", ep.error_text())
        self.assertEqual(F.snapshot(self.doc), before)
        self.assertNotIn("Traceback", cap.text)
        self.assertNotIn("ReferenceError", ep.error_text())


class TestCommands(Base):
    NAMES = ["SheetMetal_EdgeFlange", "SheetMetal_Hem", "SheetMetal_ConvertSolid", "SheetMetal_Jog"]

    def test_icons_tooltips_and_text(self):
        from PySide import QtGui

        registered = set(Gui.listCommands())
        for name in self.NAMES:
            with self.subTest(name):
                self.assertIn(name, registered)
                info = Gui.Command.get(name).getInfo()
                self.assertTrue(info["menuText"].strip())
                self.assertTrue(info["toolTip"].strip())
                pix = info["pixmap"]
                self.assertTrue(os.path.isfile(pix), pix)
                self.assertFalse(QtGui.QIcon(pix).isNull(), pix)
                with open(pix, encoding="utf-8") as fh:
                    svg = fh.read()
                self.assertIn("SPDX-License-Identifier: LGPL-2.1-or-later", svg)
                self.assertNotIn("<text", svg)

    def test_inactive_with_an_empty_selection(self):
        Gui.Selection.clearSelection()
        for name in self.NAMES:
            with self.subTest(name):
                self.assertFalse(Gui.Command.get(name).isActive())

    def test_active_with_the_right_selection(self):
        base = F.plate(self.doc)
        F.select(base, F.end_edge(base, "+x"))
        self.assertTrue(Gui.Command.get("SheetMetal_EdgeFlange").isActive())
        self.assertTrue(Gui.Command.get("SheetMetal_Hem").isActive())
        self.assertTrue(Gui.Command.get("SheetMetal_ConvertSolid").isActive())
        self.assertFalse(Gui.Command.get("SheetMetal_Jog").isActive())    # an edge is not a face
        F.select(base, F.face_with_normal(base, 2, 1))
        self.assertTrue(Gui.Command.get("SheetMetal_Jog").isActive())
        self.assertFalse(Gui.Command.get("SheetMetal_EdgeFlange").isActive())

    def test_commands_open_panels_filled_from_the_selection(self):
        base = F.plate(self.doc)
        edge = F.end_edge(base, "+x")
        for name, cls in (("SheetMetal_EdgeFlange", P.EdgeFlangePanel), ("SheetMetal_Hem", P.HemPanel)):
            with self.subTest(name):
                F.select(base, edge)
                Gui.runCommand(name)
                panel = P.current()
                self.assertIsInstance(panel, cls)
                self.assertEqual((panel.base, panel.edges), (base, [edge]))
                Gui.Control.closeDialog()
        fixed, bends, _opens = F.tray(F.box_source(self.doc))
        src = self.doc.getObject("Src")
        F.select(src, fixed)
        Gui.runCommand("SheetMetal_ConvertSolid")
        panel = P.current()
        self.assertIsInstance(panel, P.ConvertSolidPanel)
        self.assertEqual(panel._items(panel.fixed), [fixed])
        Gui.Control.closeDialog()
        line = F.line_sketch(self.doc, 30.0, 2.0)
        face = F.face_with_normal(base, 2, 1)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.doc.Name, base.Name, face)
        Gui.Selection.addSelection(self.doc.Name, line.Name)
        Gui.runCommand("SheetMetal_Jog")
        panel = P.current()
        self.assertIsInstance(panel, P.JogPanel)
        self.assertEqual((panel.face, panel.sketch), (face, line))
        self.assertEqual(panel.error_text(), "")

    def test_toolbar_lists_the_four_after_the_vendored_commands(self):
        Gui.activateWorkbench("SMWorkbench")
        names = list(Gui.getWorkbench("SMWorkbench").getToolbarItems()["Sheet Metal"])
        self.assertEqual(C.TOOLBAR, self.NAMES)
        for n in self.NAMES:
            self.assertIn(n, names)
        self.assertGreater(names.index("SheetMetal_EdgeFlange"), names.index("SheetMetal_Forming"))
        self.assertEqual(names[names.index("SheetMetal_EdgeFlange"):][:4], self.NAMES)


class TestAddBaseInInactiveBody(Base):
    def test_reports_in_the_report_view_instead_of_a_modal_warning(self):
        import SheetMetalBaseCmd  # noqa: F401  (registers SheetMetal_AddBase)

        body = self.doc.addObject("PartDesign::Body", "Body")
        sk = body.newObject("Sketcher::SketchObject", "Sketch")
        import Part

        sk.addGeometry(Part.LineSegment(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(20, 0, 0)), False)
        self.doc.recompute()
        Gui.ActiveDocument.ActiveView.setActiveObject("pdbody", None)
        F.select(sk)
        before = F.snapshot(self.doc)
        shown = []
        from PySide import QtWidgets

        orig = QtWidgets.QMessageBox.exec_
        QtWidgets.QMessageBox.exec_ = lambda self_: shown.append(self_.text()) or 0    # a modal box would hang the run
        try:
            with F.capture_output() as cap:
                Gui.runCommand("SheetMetal_AddBase")
        finally:
            QtWidgets.QMessageBox.exec_ = orig
        self.assertEqual(shown, [])
        self.assertIn("does not belong to the active Body", cap.text + (F.report_view_text() or ""))
        self.assertEqual(F.snapshot(self.doc), before)
        self.assertNotIn("Traceback", cap.text)


class TestCleanRoom(unittest.TestCase):
    FILES = ["SMEdgeCommands.py", "SMEdgePanels.py", "SMEdgeViewProviders.py", "SMGuiTests/fixture_a.py",
             "SMGuiTests/test_a_panels.py", "SMGuiTests/test_a_lifecycle.py"]

    def test_new_files_carry_the_spdx_line_and_no_gpl_notice(self):
        mod = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        gpl = "GNU General " + "Public License"
        for rel in self.FILES + ["Resources/icons/SheetMetal_%s.svg" % n for n in ("EdgeFlange", "Hem", "ConvertSolid", "Jog")]:
            with self.subTest(rel):
                with open(os.path.join(mod, rel), encoding="utf-8") as fh:
                    text = fh.read()
                self.assertIn("SPDX-License-Identifier: LGPL-2.1-or-later", text)
                self.assertNotIn(gpl, text)


class TestScenes(Base):
    def test_scene_part_is_deterministic_and_every_panel_scene_grabs(self):
        import tempfile

        objs = F.scene_part(self.doc)
        for role in ("flange", "hem", "jog", "conv"):
            self.assertNotIn("Invalid", objs[role].State, role)
            self.assertTrue(objs[role].Shape.isValid(), role)
        vols = {r: round(objs[r].Shape.Volume, 6) for r in ("flange", "hem", "jog", "conv")}
        other = F.new_doc("Again")
        again = F.scene_part(other)
        self.assertEqual(vols, {r: round(again[r].Shape.Volume, 6) for r in vols})
        with tempfile.TemporaryDirectory() as tmp:
            for kind in ("edgeflange", "hem", "jog", "convert"):
                with self.subTest(kind):
                    panel = F.scene_panel(self.doc, objs, {"panel": kind})
                    size = F.grab_png(panel, os.path.join(tmp, kind + ".png"), (360, 520))
                    self.assertEqual(size, (360, 520))
                    self.assertTrue(os.path.getsize(os.path.join(tmp, kind + ".png")) > 1000)
                    self.assertTrue(panel.reject())
