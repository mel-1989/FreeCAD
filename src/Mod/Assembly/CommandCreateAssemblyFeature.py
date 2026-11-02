# SPDX-License-Identifier: LGPL-2.1-or-later
"""Assembly_CreateAssemblyCut, Assembly_CreateAssemblyHole, Assembly_EditAssemblyFeature (cad-fork G06 slice 4b).

The commands open the task panel of ``AssemblyFeaturePanel``; the feature itself is made through the slice 4a
API (``AssemblyFeature``). Icons are SVG files installed next to this file, referenced by path as the other fork
modules do.
"""

import os

import FreeCAD as App
import FreeCADGui as Gui
from PySide.QtCore import QT_TRANSLATE_NOOP

import AssemblyFeature as AF
from CommandCheckInterference import target_assembly

ICONS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")


def selected_feature():
    """The single selected assembly feature, else None."""
    sel = Gui.Selection.getSelection()
    if len(sel) == 1 and AF._is_feature(sel[0]):
        return sel[0]
    return None


def _open(assembly, kind=None, feature=None):
    if Gui.Control.activeDialog():   # Gui.runCommand ignores IsActive: never build a second, hidden panel
        return
    from AssemblyFeaturePanel import TaskAssemblyFeature

    dialog = Gui.Control.showDialog(TaskAssemblyFeature(assembly, kind=kind or "cut", feature=feature))
    if dialog is not None:
        dialog.setAutoCloseOnDeletedDocument(True)
        dialog.setDocumentName(assembly.Document.Name)


class CommandCreateAssemblyCut:
    def GetResources(self):
        return {
            "Pixmap": os.path.join(ICONS, "Assembly_CreateAssemblyCut.svg"),
            "MenuText": QT_TRANSLATE_NOOP("Assembly_CreateAssemblyCut", "Assembly cut"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "Assembly_CreateAssemblyCut",
                "<p>Cuts the parts of the assembly with a profile pushed through them or with a solid. "
                "The cut belongs to the assembly: the parts themselves are not changed.</p>"
                "<p>Choose which parts it cuts, and see them before you confirm.</p>",
            ),
            "StatusTip": QT_TRANSLATE_NOOP(
                "Assembly_CreateAssemblyCut", "Cut the parts of the assembly without changing the parts"
            ),
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        return App.ActiveDocument is not None and not Gui.Control.activeDialog() and target_assembly() is not None

    def Activated(self):
        assembly = target_assembly()
        if assembly is not None:
            _open(assembly, "cut")


class CommandCreateAssemblyHole:
    def GetResources(self):
        return {
            "Pixmap": os.path.join(ICONS, "Assembly_CreateAssemblyHole.svg"),
            "MenuText": QT_TRANSLATE_NOOP("Assembly_CreateAssemblyHole", "Assembly hole"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "Assembly_CreateAssemblyHole",
                "<p>Drills a hole through the parts of the assembly, to a depth or all the way, "
                "with an optional counterbore. The hole belongs to the assembly: the parts themselves are not changed.</p>"
                "<p>Choose which parts it drills, and see them before you confirm.</p>",
            ),
            "StatusTip": QT_TRANSLATE_NOOP(
                "Assembly_CreateAssemblyHole", "Drill a hole through parts of the assembly without changing the parts"
            ),
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        return App.ActiveDocument is not None and not Gui.Control.activeDialog() and target_assembly() is not None

    def Activated(self):
        assembly = target_assembly()
        if assembly is not None:
            _open(assembly, "hole")


class CommandEditAssemblyFeature:
    def GetResources(self):
        return {
            "Pixmap": os.path.join(ICONS, "Assembly_EditAssemblyFeature.svg"),
            "MenuText": QT_TRANSLATE_NOOP("Assembly_EditAssemblyFeature", "Edit assembly cut or hole"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "Assembly_EditAssemblyFeature",
                "<p>Reopens the selected assembly cut or hole so you can change its tool, size or the parts it cuts.</p>"
                "<p>Cancel puts everything back as it was.</p>",
            ),
            "StatusTip": QT_TRANSLATE_NOOP(
                "Assembly_EditAssemblyFeature", "Change the selected assembly cut or hole"
            ),
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        return App.ActiveDocument is not None and not Gui.Control.activeDialog() and selected_feature() is not None

    def Activated(self):
        feat = selected_feature()
        if feat is not None and feat.getParentGeoFeatureGroup() is not None:
            _open(feat.getParentGeoFeatureGroup(), feature=feat)


Gui.addCommand("Assembly_CreateAssemblyCut", CommandCreateAssemblyCut())
Gui.addCommand("Assembly_CreateAssemblyHole", CommandCreateAssemblyHole())
Gui.addCommand("Assembly_EditAssemblyFeature", CommandEditAssemblyFeature())
