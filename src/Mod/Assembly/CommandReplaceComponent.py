# SPDX-License-Identifier: LGPL-2.1-or-later
"""Assembly_ReplaceComponent (cad-fork G06 slice 2b): put another part in place of a component, with a preview.

The command opens the task panel of ``ReplacePanel``; the remap itself is ``ReplaceComponent.plan`` / ``apply``
(slice 2a). Nothing changes in the document until OK. The icon is an SVG installed next to this file, referenced
by path like the one of Check interference.
"""

import os

import FreeCAD as App
import FreeCADGui as Gui
from PySide.QtCore import QT_TRANSLATE_NOOP

import UtilsAssembly

ICON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons", "Assembly_ReplaceComponent.svg")
ICON_REPAIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons", "Assembly_RepairJointReference.svg")


def _assembly_of(obj):
    """The assembly that holds ``obj`` (itself when it is one), else None."""
    seen = set()
    while obj is not None and obj.Name not in seen:
        seen.add(obj.Name)
        if obj.isDerivedFrom("Assembly::AssemblyObject"):
            return obj
        parent = obj.getParentGeoFeatureGroup()
        if parent is None:
            parent = next((p for p in obj.InList if p.isDerivedFrom("Assembly::AssemblyObject")), None)
        obj = parent
    return None


def target():
    """(assembly, component) for the panel: the active assembly, else the assembly that holds the selection; the
    component is the selected one (the top level child of the assembly that contains the selection) or None."""
    assembly = UtilsAssembly.activeAssembly()
    comp = None
    for sel in Gui.Selection.getSelectionEx("*", 0):
        found = _assembly_of(sel.Object)
        if found is None or (assembly is not None and found != assembly):
            continue
        assembly = found
        if sel.Object == assembly:
            subs = [s for s in sel.SubElementNames if s]
            comp = assembly.Document.getObject(subs[0].split(".")[0]) if subs else None
        else:
            comp = sel.Object
            while comp is not None and comp not in assembly.Group:
                comp = comp.getParentGeoFeatureGroup()
        if comp is not None:
            break
    if assembly is None:
        return None, None
    return assembly, comp if comp is not None and comp in assembly.Group else None


class CommandReplaceComponent:
    def GetResources(self):
        return {
            "Pixmap": ICON,
            "MenuText": QT_TRANSLATE_NOOP("Assembly_ReplaceComponent", "Replace component"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "Assembly_ReplaceComponent",
                "<p>Puts another part in place of the selected component and shows, before anything changes, "
                "which joint references carry over to the new part, which are ambiguous and which cannot be matched.</p>"
                "<p>The new part can be an object of this document, a file or a revision from the PDM vault. "
                "Nothing changes until you press OK, and OK is one undo step.</p>",
            ),
            "StatusTip": QT_TRANSLATE_NOOP(
                "Assembly_ReplaceComponent", "Replace a component by another part and remap its joints, with a preview"
            ),
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        return App.ActiveDocument is not None and not Gui.Control.activeDialog() and target()[0] is not None

    def Activated(self):
        assembly, comp = target()
        if assembly is None or Gui.Control.activeDialog():   # Gui.runCommand ignores IsActive: never build a second, hidden panel
            return
        from ReplacePanel import TaskReplaceComponent

        dialog = Gui.Control.showDialog(TaskReplaceComponent(assembly, old=comp))
        if dialog is not None:
            dialog.setAutoCloseOnDeletedDocument(True)
            dialog.setDocumentName(assembly.Document.Name)


Gui.addCommand("Assembly_ReplaceComponent", CommandReplaceComponent())


def _broken_joint_selected():
    """(assembly, joint) when the selection is one joint that a replace left broken."""
    import ReplaceComponent as RC

    sel = Gui.Selection.getSelection()
    if len(sel) == 1 and RC._is_joint(sel[0]) and RC.is_broken(sel[0]):
        return _assembly_of(sel[0]) or UtilsAssembly.activeAssembly(), sel[0]
    return None, None


class CommandRepairJointReference:
    def GetResources(self):
        return {
            "Pixmap": ICON_REPAIR,
            "MenuText": QT_TRANSLATE_NOOP("Assembly_RepairJointReference", "Repair joint reference"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "Assembly_RepairJointReference",
                "<p>Picks the new element for a joint reference that Replace component could not match, "
                "and puts the joint back to work.</p>",
            ),
            "StatusTip": QT_TRANSLATE_NOOP(
                "Assembly_RepairJointReference", "Choose the new element for a broken joint reference"
            ),
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        return App.ActiveDocument is not None and not Gui.Control.activeDialog() and _broken_joint_selected()[1] is not None

    def Activated(self):
        assembly, joint = _broken_joint_selected()
        if assembly is None or Gui.Control.activeDialog():
            return
        from ReplacePanel import TaskRepairJoint

        dialog = Gui.Control.showDialog(TaskRepairJoint(assembly, joint))
        if dialog is not None:
            dialog.setAutoCloseOnDeletedDocument(True)
            dialog.setDocumentName(assembly.Document.Name)


Gui.addCommand("Assembly_RepairJointReference", CommandRepairJointReference())
