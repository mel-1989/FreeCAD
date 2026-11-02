# SPDX-License-Identifier: LGPL-2.1-or-later
"""Assembly_Configurations (cad-fork G06 slice 5b): open the Configurations panel of the assembly.

The command opens the task panel of ``ConfigurationsPanel``; every operation in it goes through the slice 5a
API (``AssemblyConfiguration``). The icon is an SVG installed next to this file (the compiled icon resource
would need a rebuild of the GUI library), referenced by path as the other fork modules do.
"""

import os

import FreeCAD as App
import FreeCADGui as Gui
from PySide.QtCore import QT_TRANSLATE_NOOP

from CommandCheckInterference import target_assembly

ICON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons", "Assembly_Configurations.svg")


def open_panel(assembly):
    """Open the panel on ``assembly`` unless another task dialog is open. Returns the panel or None."""
    if Gui.Control.activeDialog():   # Gui.runCommand ignores IsActive: never build a second, hidden panel
        return None
    from ConfigurationsPanel import TaskConfigurations

    panel = TaskConfigurations(assembly)
    dialog = Gui.Control.showDialog(panel)
    if dialog is not None:
        dialog.setAutoCloseOnDeletedDocument(True)
        dialog.setDocumentName(assembly.Document.Name)
    return panel


class CommandConfigurations:
    def GetResources(self):
        return {
            "Pixmap": ICON,
            "MenuText": QT_TRANSLATE_NOOP("Assembly_Configurations", "Configurations"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "Assembly_Configurations",
                "<p>Lists the configurations of the assembly: named sets of suppressed parts, alternative parts and "
                "joint values. Switch between them, add, rename, copy or delete one, and see what each one changes.</p>"
                "<p>Before a switch, edits you made to the active configuration are shown so you can keep or drop them.</p>",
            ),
            "StatusTip": QT_TRANSLATE_NOOP(
                "Assembly_Configurations", "Switch and manage the configurations of the assembly"
            ),
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        return App.ActiveDocument is not None and not Gui.Control.activeDialog() and target_assembly() is not None

    def Activated(self):
        assembly = target_assembly()
        if assembly is not None:
            open_panel(assembly)


Gui.addCommand("Assembly_Configurations", CommandConfigurations())
