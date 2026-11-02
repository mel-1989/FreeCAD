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
    """The outermost assembly that holds ``obj`` (itself when it is one), else None."""
    seen = set()
    found = None
    while obj is not None and obj.Name not in seen:
        seen.add(obj.Name)
        if obj.isDerivedFrom("Assembly::AssemblyObject"):
            found = obj
        parent = obj.getParentGeoFeatureGroup()
        if parent is None:
            parent = next((p for p in obj.InList if p.isDerivedFrom("Assembly::AssemblyObject")), None)
        obj = parent
    return found


def target():
    """(assembly, component) for the panel: the active assembly, else the assembly that holds the selection; the
    component is the nearest entry of the panel's component list that holds the selection (a part inside a
    sub-assembly, an array element) or None."""
    import ReplaceComponent
    from ReplacePanel import component_for

    assembly = UtilsAssembly.activeAssembly()
    comp = None
    for sel in Gui.Selection.getSelectionEx("*", 0):
        found = _assembly_of(sel.Object)
        if found is not None and assembly is not None and found != assembly and ReplaceComponent._in_tree(assembly, sel.Object):
            found = assembly   # a sub-assembly that is the active one: its own parts are the components
        if found is None or (assembly is not None and found != assembly):
            continue
        assembly = found
        if sel.Object == assembly:
            comp = None
            for sub in [x for x in sel.SubElementNames if x]:
                # the deepest object of the sub-name path that the panel offers (Sub.ML. gives ML, not Sub)
                for name in reversed([n for n in sub.split(".") if n]):
                    comp = component_for(assembly, assembly.Document.getObject(name))
                    if comp is not None:
                        break
                if comp is not None:
                    break
        else:
            comp = component_for(assembly, sel.Object)
        if comp is not None:
            break
    if assembly is None:
        return None, None
    return assembly, comp


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
