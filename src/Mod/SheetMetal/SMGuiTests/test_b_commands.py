# SPDX-License-Identifier: LGPL-2.1-or-later
"""Slice 6b lane B checks 1 and 6: the four commands (``SheetMetal_CornerRelief``, ``SheetMetal_LighteningCutout``,
``SheetMetal_LoftedBend``, ``SheetMetal_ExportFlatDXF``) have a loadable icon and a tooltip, are inactive with an empty
selection, sit in the toolbar after the vendored commands, and open their panel prefilled from the selection; the new
files carry the clean-room markers."""

import os
import re
import unittest

import FreeCAD
import FreeCADGui as Gui

import SMShapeCommands as C
import SMShapePanels as P

from . import fixture_b as F
from .test_b_panels import Base

NAMES = ["SheetMetal_CornerRelief", "SheetMetal_LighteningCutout", "SheetMetal_LoftedBend", "SheetMetal_ExportFlatDXF"]
MOD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEW_FILES = ["SMShapeCommands.py", "SMShapePanels.py", "SMShapeViewProviders.py", "SMGuiTests/fixture_b.py",
             "SMGuiTests/test_b_panels.py", "SMGuiTests/test_b_lifecycle.py", "SMGuiTests/test_b_unfold_dxf.py",
             "SMGuiTests/test_b_commands.py"] + ["Resources/icons/%s.svg" % n for n in NAMES]


class TestCommands(Base):
    def test_commands_have_a_loadable_icon_and_a_tooltip(self):
        from PySide import QtGui

        Gui.activateWorkbench("SMWorkbench")
        registered = set(Gui.listCommands())
        for name in NAMES:
            with self.subTest(name):
                self.assertIn(name, registered)
                info = Gui.Command.get(name).getInfo()
                self.assertTrue(info["menuText"].strip())
                self.assertGreater(len(info["toolTip"].strip()), 20)
                pix = info["pixmap"]
                self.assertTrue(os.path.isfile(pix), pix)
                self.assertFalse(QtGui.QIcon(pix).isNull(), pix)
                self.assertFalse(QtGui.QIcon(pix).pixmap(32, 32).isNull(), pix)

    def test_commands_are_inactive_with_an_empty_selection(self):
        Gui.Selection.clearSelection()
        for name in NAMES:
            with self.subTest(name):
                self.assertFalse(Gui.Command.get(name).isActive())

    def test_commands_are_active_with_a_selection_and_a_document(self):
        t, edge = F.tray(self.doc)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.doc.Name, t.Name, edge)
        for name in NAMES[:3]:
            with self.subTest(name):
                self.assertTrue(Gui.Command.get(name).isActive())

    def test_toolbar_and_menu_list_the_commands_after_the_vendored_ones(self):
        Gui.activateWorkbench("SMWorkbench")
        wb = Gui.getWorkbench("SMWorkbench")
        names = list(wb.getToolbarItems()["Sheet Metal"])
        self.assertEqual(names[names.index("SheetMetal_Forming") + 1:][:4], NAMES)
        for n in NAMES:
            self.assertIn(n, wb.list)
        self.assertEqual(C.TOOLBAR, NAMES)

    def test_corner_relief_command_opens_its_panel_from_the_selection(self):
        t, edge = F.tray(self.doc)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.doc.Name, t.Name, edge)
        Gui.runCommand("SheetMetal_CornerRelief", 0)
        panel = P.current()
        self.assertIsInstance(panel, P.CornerReliefPanel)
        self.assertEqual((panel.base.Name, panel.edge), (t.Name, edge))
        self.assertIn(edge, panel.target.text())

    def test_cutout_command_takes_the_face_and_the_sketch_from_the_selection(self):
        pl, fname, face = F.plate(self.doc)
        sk = F.circle_sketch(self.doc, face, 5.0)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.doc.Name, pl.Name, fname)
        Gui.Selection.addSelection(self.doc.Name, sk.Name)
        Gui.runCommand("SheetMetal_LighteningCutout", 0)
        panel = P.current()
        self.assertIsInstance(panel, P.LighteningCutoutPanel)
        self.assertEqual((panel.base.Name, panel.face, panel.sketch.Name), (pl.Name, fname, sk.Name))

    def test_lofted_command_takes_the_two_profiles_from_the_selection_in_order(self):
        a, b = F.profiles(self.doc, 0)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.doc.Name, b.Name)
        Gui.Selection.addSelection(self.doc.Name, a.Name)
        Gui.runCommand("SheetMetal_LoftedBend", 0)
        panel = P.current()
        self.assertIsInstance(panel, P.LoftedBendPanel)
        self.assertEqual((panel.profile_a.Name, panel.profile_b.Name), (b.Name, a.Name))

    def test_use_selection_button_fills_an_open_panel(self):
        t, edge = F.tray(self.doc)
        panel = P.CornerReliefPanel(self.doc)
        Gui.Selection.clearSelection()
        panel.use_sel.click()
        self.assertIn("Select the corner edge", panel.error_text())
        Gui.Selection.addSelection(self.doc.Name, t.Name, edge)
        panel.use_sel.click()
        self.assertEqual((panel.base.Name, panel.edge), (t.Name, edge))
        self.assertEqual(panel.error_text(), "")


class TestCleanRoom(unittest.TestCase):
    def read(self, rel):
        with open(os.path.join(MOD_DIR, rel), encoding="utf-8") as fh:
            return fh.read()

    def test_new_files_carry_the_licence_line_and_no_gpl_notice(self):
        phrase = "GNU General " + "Public License"
        for rel in NEW_FILES:
            with self.subTest(rel):
                text = self.read(rel)
                self.assertIn("SPDX-License-Identifier: LGPL-2.1-or-later", text[:400])
                self.assertNotIn(phrase, text)

    def test_icons_are_plain_geometry_without_text_elements(self):
        for n in NAMES:
            with self.subTest(n):
                svg = self.read("Resources/icons/%s.svg" % n)
                self.assertNotRegex(svg, r"<text[\s>]")
                self.assertTrue(re.search(r"<(rect|polygon|circle|line|polyline)", svg))
                self.assertIn("cad-fork", svg)

    def test_the_modified_vendored_file_has_a_change_notice(self):
        head = self.read("InitGui.py")[:2500]
        self.assertIn("LGPL-2.1 section 2a, slice 6b lane B", head)

    def test_panel_modules_import_nothing_from_the_left_out_gpl_modules(self):
        for rel in ("SMShapeCommands.py", "SMShapePanels.py", "SMShapeViewProviders.py"):
            text = self.read(rel)
            for bad in ("SheetMetalCornerReliefCmd", "ExtrudedCutout", "SheetMetalCmd", "SheetMetalBaseShapeCmd"):
                self.assertNotIn(bad, text)


if __name__ == "__main__":
    unittest.main()
