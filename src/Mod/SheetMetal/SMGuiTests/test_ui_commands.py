# SPDX-License-Identifier: LGPL-2.1-or-later
"""G08a slice 3: the UI lint's command guarantee for SheetMetal, kept outside the workshop (needs FreeCADGui).

Every SheetMetal command has an icon that resolves, a MenuText, a ToolTip different from the MenuText, a StatusTip
chosen by its author (the raw datum, not the ToolTip fallback the action builds) and a default shortcut that no
other registered command shares. The rules are the same as harness/gui/lint.py's CMD_* rules."""

import os
import unittest

import FreeCADGui as Gui
from PySide import QtGui

PREFIX = "SheetMetal_"
WORKBENCHES = ["SMWorkbench", "SketcherWorkbench", "AssemblyWorkbench"]


def _norm(text):
    return text.replace("&", "").rstrip(".").rstrip("\u2026").strip().lower()


def _seq(cmd):
    s = cmd.getShortcut() or ""
    return QtGui.QKeySequence(s).toString(QtGui.QKeySequence.PortableText) if s else ""


class TestCommandGuarantee(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for wb in WORKBENCHES:
            try:
                Gui.activateWorkbench(wb)
            except Exception:    # noqa: BLE001  (a workbench missing from a build only weakens the collision sweep)
                pass
        cls.names = sorted(n for n in Gui.listCommands() if n.startswith(PREFIX))
        # A command gets its action, and so its default shortcut, only once a toolbar holds it; SheetMetal commands
        # no toolbar places (UnattendedUnfold unless its preference is set) would read an empty shortcut and hide a
        # collision, so a scratch workbench places them, as harness/gui/lint_gui.py does for every command.
        orphans = [n for n in cls.names if not Gui.Command.get(n).getAction()]
        if orphans:
            class _Sweep(Gui.Workbench):
                MenuText = "SheetMetal lint sweep"

                def Initialize(self):
                    self.appendToolbar("SheetMetal lint sweep", orphans)

                def GetClassName(self):
                    return "Gui::PythonWorkbench"

            Gui.addWorkbench(_Sweep())
            Gui.activateWorkbench("_Sweep")
            Gui.activateWorkbench("SMWorkbench")

    def test_the_module_registers_commands(self):
        self.assertGreaterEqual(len(self.names), 18, self.names)

    def test_icon_menu_text_tool_tip_and_status_tip(self):
        placeholder = Gui.getIcon("__lint_missing_icon__").pixmap(32, 32).toImage()
        for name in self.names:
            with self.subTest(name):
                info = Gui.Command.get(name).getInfo()
                pix = info.get("pixmap", "")
                self.assertTrue(pix, "no icon")
                if os.path.isabs(pix):
                    self.assertTrue(os.path.isfile(pix), pix)
                icon = Gui.getIcon(pix)
                self.assertFalse(icon is None or icon.isNull(), pix)
                self.assertNotEqual(icon.pixmap(32, 32).toImage(), placeholder, "icon did not resolve: " + pix)
                menu, tip, status = info["menuText"], info["toolTip"], info["statusTip"]
                self.assertTrue(menu.strip(), "no MenuText")
                self.assertNotEqual(_norm(menu), _norm(name))
                self.assertTrue(tip.strip(), "no ToolTip")
                self.assertNotEqual(_norm(tip), _norm(menu), "ToolTip repeats the MenuText")
                self.assertTrue(status.strip(), "no StatusTip")

    def test_default_shortcuts_do_not_collide(self):
        by_seq = {}
        for name in Gui.listCommands():
            seq = _seq(Gui.Command.get(name))
            if seq:
                by_seq.setdefault(seq, []).append(name)
        for name in self.names:
            seq = _seq(Gui.Command.get(name))
            if seq:
                self.assertEqual(by_seq[seq], [name], "shortcut %s is shared" % seq)
