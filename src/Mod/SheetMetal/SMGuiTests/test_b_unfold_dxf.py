# SPDX-License-Identifier: LGPL-2.1-or-later
"""Slice 6b lane B check 3: bend tables in the Unfold panel and the flat DXF export command.

The Unfold panel (``SheetMetalUnfoldCmd.SMUnfoldTaskPanel``) lists every shipped bend table, BA and BD, in its material
list, and choosing one gives the same flat (area within 1e-9 relative, same ``BendData``) as setting ``MaterialSheet`` to
that table's reference through Python. ``SheetMetal_ExportFlatDXF`` writes a file byte-identical to
``SheetMetalUnfoldCmd.export_flat_dxf`` with the same options; its refusals appear as text in the panel."""

import os
import tempfile
import unittest

import FreeCAD
import FreeCADGui as Gui

import SheetMetalBendTable as BT
import SheetMetalUnfoldCmd as UC
import SMShapeCommands  # noqa: F401  (registers the commands)
import SMShapePanels as P
from SMTests.testBendTable import GAUGE_PARTS, UNFOLD_PARTS
from SMTests.testOracle import Case, build_base, unfold_target_face
from SMTests.testUnfoldFeature import feature_unfold

from . import fixture_b as F
from .test_b_panels import Base

REL = 1e-9


def part_for(ref):
    """The (name, thickness, radius, segments, angles) of a part inside the range of the table ``ref`` names."""
    return GAUGE_PARTS[0] if "gauge" in ref else UNFOLD_PARTS[0]


def case_of(part):
    name, t, r, segs, angles = part
    return Case(name, t, r / t, 0.5, segs, angles)


class TestUnfoldPanelBendTables(Base):
    def test_the_material_list_offers_every_shipped_table_ba_and_bd(self):
        refs = BT.list_refs()
        self.assertTrue(any("_ba" in r for r in refs), refs)
        self.assertTrue(any("_bd" in r for r in refs), refs)
        _base, unfold = F.unfold_of(self.doc)
        panel = UC.SMUnfoldTaskPanel(unfold)
        items = [panel.form.availableMds.itemText(i) for i in range(panel.form.availableMds.count())]
        for ref in refs:
            self.assertIn(ref, items)
        for fname in BT.list_table_files():
            table = os.path.splitext(fname)[0]
            self.assertTrue(any(i.startswith(BT.PREFIX + table + "/") for i in items), table)

    def test_choosing_a_table_equals_setting_material_sheet_in_python(self):
        for ref in BT.list_refs():
            with self.subTest(ref=ref):
                case = case_of(part_for(ref))
                want_doc = self.ref_doc()
                base = build_base(want_doc, case)
                want = feature_unfold(want_doc, base, unfold_target_face(base.Shape), material=ref, old=None)
                FreeCAD.setActiveDocument(self.doc.Name)
                base2 = build_base(self.doc, case)
                got = feature_unfold(self.doc, base2, unfold_target_face(base2.Shape), material="_manual", k=0.5,
                                     old=None)
                self.assertNotEqual(got.MaterialSheet, ref)
                panel = UC.SMUnfoldTaskPanel(got)
                combo = panel.form.availableMds
                combo.setCurrentIndex(combo.findText(ref))
                self.doc.recompute()
                self.assertEqual(got.MaterialSheet, ref)
                self.assertNotIn("Invalid", got.State)
                self.assertGreater(want.Shape.Area, 0.0)
                self.assertLessEqual(abs(got.Shape.Area - want.Shape.Area), REL * want.Shape.Area)
                self.assertEqual(got.BendData, want.BendData)
                self.assertTrue(got.BendData)
                self.doc.removeObject(got.Name)


class TestExportFlatDxf(Base):
    def setUp(self):
        super().setUp()
        self.dir = tempfile.mkdtemp(prefix="smdxf-")
        self.base, self.unfold = F.unfold_of(self.doc)

    def path(self, name):
        return os.path.join(self.dir, name)

    def test_the_file_is_byte_identical_to_export_flat_dxf(self):
        for i, (notes, height) in enumerate([(True, 2.5), (False, 2.5), (True, 3.5), (False, 1.25)]):
            with self.subTest(notes=notes, height=height):
                panel = P.ExportDxfPanel(self.doc, obj=self.unfold, path=self.path("panel%d.dxf" % i))
                panel.notes.setChecked(notes)
                panel.text_height.setValue(height)
                self.assertTrue(panel.accept(), panel.error_text())
                UC.export_flat_dxf(self.unfold, self.path("api%d.dxf" % i), text_height=height, notes=notes)
                with open(self.path("panel%d.dxf" % i), "rb") as a, open(self.path("api%d.dxf" % i), "rb") as b:
                    got, want = a.read(), b.read()
                self.assertGreater(len(got), 200)
                self.assertEqual(got, want)

    def test_the_bend_notes_option_changes_the_file(self):
        outs = {}
        for notes in (True, False):
            panel = P.ExportDxfPanel(self.doc, obj=self.unfold, path=self.path("n%s.dxf" % notes))
            panel.notes.setChecked(notes)
            self.assertTrue(panel.accept(), panel.error_text())
            with open(self.path("n%s.dxf" % notes), "rb") as fh:
                outs[notes] = fh.read()
        self.assertNotEqual(outs[True], outs[False])
        self.assertIn(b"NOTES", outs[True])
        self.assertIn(b"BEND_UP", outs[False])

    def test_browse_uses_the_file_dialog_a_test_can_bypass(self):
        panel = P.ExportDxfPanel(self.doc, obj=self.unfold, path="")
        panel.ask_path = lambda: self.path("browsed.dxf")
        panel.browse.click()
        self.assertEqual(panel.path.text(), self.path("browsed.dxf"))
        self.assertTrue(panel.accept())
        self.assertTrue(os.path.isfile(self.path("browsed.dxf")))
        panel.ask_path = lambda: ""
        panel.browse.click()
        self.assertEqual(panel.path.text(), self.path("browsed.dxf"))

    def refuse(self, panel, words, path=None):
        before = F.snapshot(self.doc)
        with F.capture_output() as cap:
            ok = panel.accept()
        self.assertFalse(ok)
        self.assertFalse(panel.done)
        self.assertIn(words, panel.error_text())
        self.assertEqual(F.snapshot(self.doc), before)
        self.assertNotIn("Traceback", cap.text)
        if path:
            self.assertFalse(os.path.exists(path))

    def test_a_non_unfold_selection_is_refused(self):
        out = self.path("base.dxf")
        self.refuse(P.ExportDxfPanel(self.doc, obj=self.base, path=out), "not an Unfold feature", out)

    def test_no_bend_data_bad_text_height_and_unwritable_path_are_refused(self):
        out = self.path("x.dxf")
        panel = P.ExportDxfPanel(self.doc, obj=self.unfold, path=out)
        panel.text_height.setValue(0.0)
        self.refuse(panel, "text_height", out)
        panel = P.ExportDxfPanel(self.doc, obj=self.unfold, path="")
        self.refuse(panel, "choose a file name")
        panel = P.ExportDxfPanel(self.doc, obj=self.unfold, path=self.path("no/such/dir/x.dxf"))
        self.refuse(panel, "could not write")
        saved = self.unfold.BendData
        self.unfold.BendData = ""
        self.refuse(P.ExportDxfPanel(self.doc, obj=self.unfold, path=out), "no bend data", out)
        self.unfold.BendData = saved

    def test_the_command_is_active_on_an_unfold_feature_only(self):
        cmd = Gui.Command.get("SheetMetal_ExportFlatDXF")
        Gui.Selection.clearSelection()
        self.assertFalse(cmd.isActive())
        Gui.Selection.addSelection(self.doc.Name, self.base.Name)
        self.assertFalse(cmd.isActive())
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.doc.Name, self.unfold.Name)
        self.assertTrue(cmd.isActive())

    def test_the_command_opens_the_panel_and_refuses_a_non_unfold_selection_quietly(self):
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.doc.Name, self.unfold.Name)
        Gui.runCommand("SheetMetal_ExportFlatDXF", 0)
        panel = P.current()
        self.assertIsInstance(panel, P.ExportDxfPanel)
        self.assertEqual(panel.obj.Name, self.unfold.Name)
        panel.reject()
        Gui.Control.closeDialog()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.doc.Name, self.base.Name)
        with F.capture_output() as cap:
            import SMShapeCommands

            Gui.Command.get("SheetMetal_ExportFlatDXF")    # exists
            SMShapeCommands._ExportFlatDXF().Activated()
        self.assertNotIn("Traceback", cap.text)
        self.assertIsNone(P.current())


if __name__ == "__main__":
    unittest.main()
