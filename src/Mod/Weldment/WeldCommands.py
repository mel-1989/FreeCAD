# SPDX-License-Identifier: LGPL-2.1-or-later
"""GUI commands of the Weldment workbench (cad-fork G07 slice 5a). Imported only from InitGui."""

import FreeCAD
import FreeCADGui as Gui

import Profiles
import WeldCutList
import WeldMember
import WeldTrim


def _doc():
    doc = FreeCAD.ActiveDocument
    if doc is None:
        raise RuntimeError("open a document first")
    return doc


class _Member:
    def GetResources(self):
        return {"MenuText": "Structural member", "ToolTip": "Sweep a profile along the selected straight edges"}

    def IsActive(self):
        return FreeCAD.ActiveDocument is not None

    def Activated(self):
        doc = _doc()
        path = []
        for sel in Gui.Selection.getSelectionEx():
            path.append((sel.Object, list(sel.SubElementNames) or [""]))
        fam = "EN SHS" if "EN SHS" in Profiles.families() else Profiles.families()[0]
        doc.openTransaction("Structural member")
        WeldMember.make_member(doc, fam, Profiles.sizes(fam)[0], path)
        doc.commitTransaction()
        doc.recompute()


class _Mitre:
    def GetResources(self):
        return {"MenuText": "Mitre joint", "ToolTip": "Select two members that meet at a point, then mitre their ends"}

    def IsActive(self):
        return len(Gui.Selection.getSelection()) == 2

    def Activated(self):
        doc = _doc()
        a, b = Gui.Selection.getSelection()
        pa = WeldMember.segment_specs(WeldTrim.root_member(a))[0]
        pb = WeldMember.segment_specs(WeldTrim.root_member(b))[0]
        best = min(
            ((x, y, (px - py).Length) for x, px in (("Start", pa.p0), ("End", pa.p1)) for y, py in (("Start", pb.p0), ("End", pb.p1))),
            key=lambda t: t[2],
        )
        doc.openTransaction("Mitre joint")
        WeldTrim.make_corner(doc, a, 0, best[0], b, 0, best[1], "Mitre")
        doc.commitTransaction()
        doc.recompute()


class _CutList:
    def GetResources(self):
        return {"MenuText": "Cut list", "ToolTip": "Print the cut list of the selected members to the report view"}

    def IsActive(self):
        return bool(Gui.Selection.getSelection())

    def Activated(self):
        FreeCAD.Console.PrintMessage(WeldCutList.to_csv(WeldCutList.cut_list(Gui.Selection.getSelection())))


Gui.addCommand("Weldment_Member", _Member())
Gui.addCommand("Weldment_Mitre", _Mitre())
Gui.addCommand("Weldment_CutList", _CutList())
