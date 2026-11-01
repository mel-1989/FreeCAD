# SPDX-License-Identifier: LGPL-2.1-or-later
"""GUI commands of the Weldment workbench (cad-fork G07 slices 5a, 5b, 6a). Imported only from InitGui.

Each command reads the selection, opens its task panel (``WeldPanels``) prefilled from it and leaves the
rest to the panel: one transaction on OK, nothing on Cancel, refusals shown as text in the panel.
"""

import os

import FreeCAD
import FreeCADGui as Gui

import WeldPanels as P
import WeldTrim

ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")

#: command name -> (menu text, tooltip, icon name); the order is the toolbar order
COMMANDS = {
    "Weldment_Member": ("Structural member", "Sweep a profile from the tables along the selected straight edges or a sketch", "Weldment_Member"),
    "Weldment_Mitre": ("Mitre joint", "Select two members that meet at a point, then mitre both ends on the bisector", "Weldment_Mitre"),
    "Weldment_ButtA": ("Butt joint, A trimmed", "Select member A, then member B: A is trimmed to B's outer surface", "Weldment_ButtA"),
    "Weldment_ButtB": ("Butt joint, B trimmed", "Select member A, then member B: B is trimmed to A's outer surface", "Weldment_ButtB"),
    "Weldment_TrimToFace": ("Trim to face", "Select a member and a planar face: the member's nearer end is extended or trimmed to the face", "Weldment_TrimToFace"),
    "Weldment_Gusset": ("Gusset", "Select two members that meet at a point, then add a triangular or chamfered corner plate", "Weldment_Gusset"),
    "Weldment_EndCap": ("End cap", "Select a member, then add a cap plate on or in its end", "Weldment_EndCap"),
    "Weldment_WeldBead": ("Weld bead", "Select two bodies and the straight edges where they meet (Ctrl-click the edges), then add a fillet weld bead", "Weldment_WeldBead"),
    "Weldment_CutList": ("Cut list", "List the selected members, plates and welds grouped, and export the list as CSV", "Weldment_CutList"),
}
TOOLBAR = list(COMMANDS)
CORNER_COMMANDS = ["Weldment_Mitre", "Weldment_ButtA", "Weldment_ButtB", "Weldment_TrimToFace"]
MENU = ["Weldment_Member", "Separator"] + CORNER_COMMANDS + ["Separator", "Weldment_Gusset", "Weldment_EndCap",
                                                              "Weldment_WeldBead", "Separator", "Weldment_CutList"]


def _doc():
    doc = FreeCAD.ActiveDocument
    if doc is None:
        raise RuntimeError("open a document first")
    return doc


def _open(make):
    """Show the panel ``make()`` builds; a refusal to even open it goes to the report view as one line."""
    if Gui.Control.activeDialog():
        FreeCAD.Console.PrintWarning("Weldment: close the open task panel first\n")
        return None
    try:
        return P.show(make())
    except Exception as e:    # noqa: BLE001  (never a traceback in the report view)
        FreeCAD.Console.PrintError("Weldment: %s\n" % (e.args[0] if e.args else e))
        return None


class _Command:
    name = ""

    def GetResources(self):
        text, tip, icon = COMMANDS[self.name]
        return {"Pixmap": os.path.join(ICON_DIR, icon + ".svg"), "MenuText": text, "ToolTip": tip}

    def IsActive(self):
        return FreeCAD.ActiveDocument is not None


class _Member(_Command):
    name = "Weldment_Member"

    def Activated(self):
        doc = _doc()
        _open(lambda: P.MemberPanel(doc, path=P.selection_path()))


class _Corner(_Command):
    kind = "Mitre"

    def IsActive(self):
        if FreeCAD.ActiveDocument is None:
            return False
        sel = Gui.Selection.getSelection()
        if self.kind == "Face":
            return len(sel) >= 1 and P.is_chain(sel[0])
        return len(sel) == 2

    def Activated(self):
        doc = _doc()
        sel = P.selection_chains()
        if self.kind == "Face":
            _open(lambda: P.CornerPanel(doc, a=sel[0] if sel else None, face=P.selection_face(), kind="Face"))
            return
        a = sel[0] if len(sel) > 0 else None
        b = sel[1] if len(sel) > 1 else None
        _open(lambda: P.CornerPanel(doc, a=a, b=b, kind=self.kind))


class _Mitre(_Corner):
    name, kind = "Weldment_Mitre", "Mitre"


class _ButtA(_Corner):
    name, kind = "Weldment_ButtA", "ButtA"


class _ButtB(_Corner):
    name, kind = "Weldment_ButtB", "ButtB"


class _TrimToFace(_Corner):
    name, kind = "Weldment_TrimToFace", "Face"


class _Gusset(_Command):
    name = "Weldment_Gusset"

    def IsActive(self):
        return FreeCAD.ActiveDocument is not None and len(Gui.Selection.getSelection()) == 2

    def Activated(self):
        doc = _doc()
        sel = P.selection_chains()
        _open(lambda: P.GussetPanel(doc, a=sel[0] if len(sel) > 0 else None, b=sel[1] if len(sel) > 1 else None))


class _EndCap(_Command):
    name = "Weldment_EndCap"

    def IsActive(self):
        return FreeCAD.ActiveDocument is not None and len(Gui.Selection.getSelection()) == 1

    def Activated(self):
        doc = _doc()
        sels = Gui.Selection.getSelectionEx()
        base = sels[0].Object if sels else None
        end = "End"
        if base is not None and sels[0].PickedPoints:
            try:
                end = P.end_near(base, sels[0].PickedPoints[0])
            except Exception:    # noqa: BLE001
                pass
        _open(lambda: P.EndCapPanel(doc, base=base if base is not None and P.is_chain(base) else None, end=end))


class _WeldBead(_Command):
    name = "Weldment_WeldBead"

    def IsActive(self):
        return FreeCAD.ActiveDocument is not None and len(Gui.Selection.getSelectionEx()) >= 2

    def Activated(self):
        doc = _doc()
        bodies, edges = P.selection_bead()
        _open(lambda: P.BeadPanel(doc, bodies=bodies, edges=edges))


class _CutList(_Command):
    name = "Weldment_CutList"

    def Activated(self):
        doc = _doc()
        _open(lambda: P.CutListPanel(doc, objects=Gui.Selection.getSelection()))


for _cls in (_Member, _Mitre, _ButtA, _ButtB, _TrimToFace, _Gusset, _EndCap, _WeldBead, _CutList):
    Gui.addCommand(_cls.name, _cls())
