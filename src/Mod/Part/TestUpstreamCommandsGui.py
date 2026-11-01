# SPDX-License-Identifier: LGPL-2.1-or-later
"""G08a slice 4a: the UI lint's command guarantee for Part, PartDesign and Sketcher, kept outside the workshop (needs FreeCADGui).

Every command of the three workbenches has an icon (its Pixmap resolves, or a group command shows its active child's icon on its
QAction), a MenuText that is not the command name, a ToolTip that is not the MenuText and a StatusTip chosen by its author (the raw
datum, not the ToolTip fallback the action builds). The rules are the same as harness/gui/lint.py's CMD_* rules in the workshop; the
commands listed in WAIVED are the ones the lint's waiver file also lists, each with the reason.

Run: FreeCAD -t TestUpstreamCommandsGui (needs the GUI binary)."""

import os
import unittest

import FreeCADGui as Gui

PREFIXES = ("Part_", "PartDesign_", "Sketcher_")
WORKBENCHES = ("PartWorkbench", "PartDesignWorkbench", "SketcherWorkbench")

#: command -> {rule: reason}; the lint reports no key for the command and rule, and so does this test
WAIVED = {
    "Part_PickCurveNet": {"icon": "developer test command whose icon name Test1 does not exist; no art is drawn for it"},
    "PartDesign_DuplicateSelection": {"icon": "no icon of its own in the module; the Clone icon belongs to another command"},
    "Sketcher_ChangeDimensionConstraint": {"icon": "context-menu entry with no icon in the module's resources"},
    "Sketcher_Grid": {"icon": "toggle shown by the sketcher status-bar widget; no icon in the module's resources"},
    "Sketcher_RenderingOrder": {"icon": "menu entry with no icon in the module's resources"},
    "Sketcher_Snap": {"icon": "toggle shown by the sketcher status-bar widget; no icon in the module's resources"},
}


def _norm(text):
    return text.replace("&", "").rstrip(".").rstrip("…").strip().lower()


class TestUpstreamCommandGuarantee(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for wb in WORKBENCHES:
            Gui.activateWorkbench(wb)
        cls.names = sorted(n for n in Gui.listCommands() if n.startswith(PREFIXES))
        # A group command has no Pixmap datum; the icon a user sees is the one on its action, which exists once a toolbar holds it.
        orphans = [n for n in cls.names if not Gui.Command.get(n).getAction()]
        if orphans:
            class _Sweep(Gui.Workbench):
                MenuText = "Upstream lint sweep"

                def Initialize(self):
                    self.appendToolbar("Upstream lint sweep", orphans)

                def GetClassName(self):
                    return "Gui::PythonWorkbench"

            Gui.addWorkbench(_Sweep())
            Gui.activateWorkbench("_Sweep")
            Gui.activateWorkbench("PartDesignWorkbench")
        cls.placeholder = Gui.getIcon("__lint_missing_icon__").pixmap(32, 32).toImage()

    def _icon_resolves(self, pixmap):
        if not pixmap:
            return False
        if os.path.isabs(pixmap) and not os.path.isfile(pixmap):
            return False
        icon = Gui.getIcon(pixmap)
        return icon is not None and not icon.isNull() and icon.pixmap(32, 32).toImage() != self.placeholder

    def _action_icon_is_real(self, name):
        from PySide import QtGui
        for a in Gui.getMainWindow().findChildren(QtGui.QAction):
            if a.objectName() == name:
                return not a.icon().isNull() and a.icon().pixmap(32, 32).toImage() != self.placeholder
        return False

    def test_the_three_workbenches_register_commands(self):
        self.assertGreaterEqual(len([n for n in self.names if n.startswith("Part_")]), 40)
        self.assertGreaterEqual(len([n for n in self.names if n.startswith("PartDesign_")]), 40)
        self.assertGreaterEqual(len([n for n in self.names if n.startswith("Sketcher_")]), 90)

    def test_icon_menu_text_tool_tip_and_status_tip(self):
        for name in self.names:
            waived = WAIVED.get(name, {})
            with self.subTest(name):
                info = Gui.Command.get(name).getInfo()
                if "icon" not in waived:
                    self.assertTrue(self._icon_resolves(info.get("pixmap", "")) or self._action_icon_is_real(name), "no icon")
                menu, tip, status = info["menuText"], info["toolTip"], info["statusTip"]
                if "menu" not in waived:
                    self.assertTrue(menu.strip(), "no MenuText")
                    self.assertNotEqual(_norm(menu), _norm(name), "MenuText is the command name")
                if "tooltip" not in waived:
                    self.assertTrue(tip.strip(), "no ToolTip")
                    self.assertNotEqual(_norm(tip), _norm(menu), "ToolTip repeats the MenuText")
                if "status" not in waived:
                    self.assertTrue(status.strip(), "no StatusTip")

    def test_waivers_are_still_needed(self):
        for name in WAIVED:
            self.assertIn(name, self.names, "waived command no longer exists: " + name)
