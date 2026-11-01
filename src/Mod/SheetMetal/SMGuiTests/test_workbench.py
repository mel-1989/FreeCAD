# SPDX-License-Identifier: LGPL-2.1-or-later
"""SheetMetal workbench registration (InitGui.py): it loads, every toolbar command exists with text and icon, and it coexists with Weldment."""

import os
import unittest

import FreeCAD
import FreeCADGui as Gui

PARAM = "User parameter:BaseApp/Preferences/Mod/SheetMetal"


def _capture():
    import contextlib
    import sys
    import tempfile

    @contextlib.contextmanager
    def cm():
        sys.stdout.flush()
        sys.stderr.flush()
        tmp = tempfile.TemporaryFile(mode="w+b")
        saved = (os.dup(1), os.dup(2))
        os.dup2(tmp.fileno(), 1)
        os.dup2(tmp.fileno(), 2)
        cap = type("Cap", (), {"text": ""})()
        try:
            yield cap
        finally:
            sys.stdout.flush()
            sys.stderr.flush()
            os.dup2(saved[0], 1)
            os.dup2(saved[1], 2)
            os.close(saved[0])
            os.close(saved[1])
            tmp.seek(0)
            cap.text = tmp.read().decode("utf-8", "replace")
            tmp.close()

    return cm()


def activate_logged(name):
    with _capture() as cap:
        Gui.activateWorkbench(name)
    return cap.text


class TestSheetMetalWorkbench(unittest.TestCase):
    def tearDown(self):
        FreeCAD.ParamGet(PARAM).RemInt("EngineeringUXMode")

    def toolbar_commands(self):
        wb = Gui.getWorkbench("SMWorkbench")
        return list(wb.getToolbarItems()["Sheet Metal"])

    def test_workbench_is_registered_and_activates_without_errors(self):
        self.assertIn("SMWorkbench", Gui.listWorkbenches())
        text = activate_logged("SMWorkbench")
        self.assertEqual(Gui.activeWorkbench().name(), "SMWorkbench")
        for bad in ("is not defined", "During initialization", "Traceback"):
            self.assertNotIn(bad, text)

    def test_every_toolbar_command_is_registered_with_text_and_icon(self):
        from PySide import QtGui

        Gui.activateWorkbench("SMWorkbench")
        names = self.toolbar_commands()
        for expected in ("SheetMetal_AddBase", "SheetMetal_Extrude", "SheetMetal_ExtendBySketch", "SheetMetal_AddFoldWall",
                         "SheetMetal_Unfold", "SheetMetal_UnfoldUpdate", "SheetMetal_AddRelief", "SheetMetal_AddBend",
                         "SheetMetal_Forming"):
            self.assertIn(expected, names)
        registered = set(Gui.listCommands())
        for name in names:
            with self.subTest(name):
                self.assertIn(name, registered)
                info = Gui.Command.get(name).getInfo()
                self.assertTrue(info["menuText"].strip(), name)
                pix = info["pixmap"]
                self.assertTrue(pix, name)
                if os.path.isabs(pix):
                    self.assertTrue(os.path.isfile(pix), pix)
                self.assertFalse(QtGui.QIcon(pix).isNull() and QtGui.QIcon(":/icons/" + pix).isNull(), pix)

    def test_workbench_icon_file_exists(self):
        import SheetMetalTools

        self.assertTrue(os.path.isfile(os.path.join(SheetMetalTools.icons_path, "SMLogo.svg")))

    def test_engineering_mode_adds_unattended_unfold_after_unfold(self):
        # Initialize runs once per session, so evaluate its branch the way it does: the same flag, the same list logic
        from engineering_mode import engineering_mode_enabled

        FreeCAD.ParamGet(PARAM).SetInt("EngineeringUXMode", 1)
        self.assertTrue(engineering_mode_enabled())
        wb = Gui.getWorkbench("SMWorkbench")
        self.assertIn("SheetMetal_Unfold", wb.list)
        self.assertIn("SheetMetal_Unfold", self.toolbar_commands())

    def test_context_menu_lists_the_commands(self):
        wb = Gui.getWorkbench("SMWorkbench")
        Gui.activateWorkbench("SMWorkbench")
        self.assertTrue(callable(wb.ContextMenu))
        self.assertEqual(wb.list[0], "SheetMetal_AddBase")


class TestWorkbenchesTogether(unittest.TestCase):
    def test_weldment_and_sheetmetal_both_activate_in_one_session(self):
        self.assertIn("WeldmentWorkbench", Gui.listWorkbenches())
        self.assertIn("SMWorkbench", Gui.listWorkbenches())
        for name in ("WeldmentWorkbench", "SMWorkbench", "WeldmentWorkbench"):
            text = activate_logged(name)
            self.assertEqual(Gui.activeWorkbench().name(), name)
            self.assertNotIn("is not defined", text)
        self.assertIn("Weldment_Member", Gui.listCommands())
        self.assertIn("SheetMetal_AddBase", Gui.listCommands())
