# SPDX-License-Identifier: LGPL-2.1-or-later
"""Assembly_CheckInterference (cad-fork G06 slice 1b): list the interfering, touching and close pairs of an assembly.

The command opens the task panel of ``InterferencePanel``; the check itself is ``Interference.check`` (slice 1a).
Nothing is added to the document. The icon is an SVG installed next to this file (the compiled icon resource
would need a rebuild of the GUI library), referenced by path as the other fork modules do.
"""

import os

import FreeCAD as App
import FreeCADGui as Gui
from PySide.QtCore import QT_TRANSLATE_NOOP

import UtilsAssembly

ICON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons", "Assembly_CheckInterference.svg")


def target_assembly():
    """The active assembly, else the single selected assembly of the active document, else None."""
    assembly = UtilsAssembly.activeAssembly()
    if assembly is not None:
        return assembly
    sel = Gui.Selection.getSelection()
    if len(sel) == 1 and sel[0].isDerivedFrom("Assembly::AssemblyObject"):
        return sel[0]
    return None


class CommandCheckInterference:
    def GetResources(self):
        return {
            "Pixmap": ICON,
            "MenuText": QT_TRANSLATE_NOOP("Assembly_CheckInterference", "Check interference"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "Assembly_CheckInterference",
                "<p>Lists the parts of the assembly that overlap, touch, or sit closer than a chosen gap. "
                "Select a row to see both parts and the shared volume in the 3D view.</p>"
                "<p>Nothing is added to the document.</p>",
            ),
            "StatusTip": QT_TRANSLATE_NOOP(
                "Assembly_CheckInterference", "List overlapping, touching and too-close parts of the assembly"
            ),
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        return App.ActiveDocument is not None and not Gui.Control.activeDialog() and target_assembly() is not None

    def Activated(self):
        assembly = target_assembly()
        if assembly is None or Gui.Control.activeDialog():   # Gui.runCommand ignores IsActive: never build a second, hidden panel
            return
        from InterferencePanel import TaskCheckInterference

        dialog = Gui.Control.showDialog(TaskCheckInterference(assembly))
        if dialog is not None:
            dialog.setAutoCloseOnDeletedDocument(True)
            dialog.setDocumentName(assembly.Document.Name)


Gui.addCommand("Assembly_CheckInterference", CommandCheckInterference())
