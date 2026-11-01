# SPDX-License-Identifier: LGPL-2.1-or-later
"""GUI commands of the corner relief, lightening cutout, lofted bend and flat DXF export (cad-fork G07 slice 6b).

Imported only from InitGui. Each command reads the selection, opens its task panel (``SMShapePanels``) prefilled
from it and leaves the rest to the panel: one transaction on OK, nothing on Cancel, refusals shown as text.

Clean room: written from the FreeCAD Python command conventions and the Weldment commands of this fork; the
implementer never opened the add-on's GPL files.
"""

import os

import FreeCAD
import FreeCADGui as Gui

import SMShapePanels as P

ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Resources", "icons")

#: command name -> (menu text, tooltip, icon name); the order is the toolbar order
COMMANDS = {
    "SheetMetal_CornerRelief": (
        "Corner relief",
        "Select the corner edge where two bends meet, then cut a round, square or obround relief there",
        "SheetMetal_CornerRelief"),
    "SheetMetal_LighteningCutout": (
        "Lightening cutout",
        "Select a planar face of the sheet and a closed profile sketch, then cut the profile through the sheet, alone or as a pattern",
        "SheetMetal_LighteningCutout"),
    "SheetMetal_LoftedBend": (
        "Lofted bend",
        "Select two open profiles on parallel planes, then join them with a faceted sheet-metal transition",
        "SheetMetal_LoftedBend"),
    "SheetMetal_ExportFlatDXF": (
        "Export flat pattern to DXF",
        "Select an Unfold feature, then write its flat pattern with the bend lines to a DXF file",
        "SheetMetal_ExportFlatDXF"),
}
TOOLBAR = list(COMMANDS)


def _doc():
    doc = FreeCAD.ActiveDocument
    if doc is None:
        raise RuntimeError("open a document first")
    return doc


def _open(make):
    """Show the panel ``make()`` builds; a refusal to even open it goes to the report view as one line."""
    if Gui.Control.activeDialog():
        FreeCAD.Console.PrintWarning("SheetMetal: close the open task panel first\n")
        return None
    try:
        return P.show(make())
    except Exception as e:    # noqa: BLE001  (never a traceback in the report view)
        FreeCAD.Console.PrintError("SheetMetal: %s\n" % P.message_of(e))
        return None


class _Command:
    name = ""

    def GetResources(self):
        text, tip, icon = COMMANDS[self.name]
        return {"Pixmap": os.path.join(ICON_DIR, icon + ".svg"), "MenuText": text, "ToolTip": tip}

    def IsActive(self):
        return FreeCAD.ActiveDocument is not None and len(Gui.Selection.getSelectionEx()) > 0


class _CornerRelief(_Command):
    name = "SheetMetal_CornerRelief"

    def Activated(self):
        doc = _doc()
        picked = P.selection_edge()
        _open(lambda: P.CornerReliefPanel(doc, base=picked[0] if picked else None, edge=picked[1] if picked else None))


class _LighteningCutout(_Command):
    name = "SheetMetal_LighteningCutout"

    def Activated(self):
        doc = _doc()
        face = P.selection_face()
        profiles = P.selection_profiles(exclude=face[0] if face else None)
        _open(lambda: P.LighteningCutoutPanel(doc, base=face[0] if face else None, face=face[1] if face else None,
                                              sketch=profiles[0] if profiles else None))


class _LoftedBend(_Command):
    name = "SheetMetal_LoftedBend"

    def Activated(self):
        doc = _doc()
        profiles = P.selection_profiles()
        _open(lambda: P.LoftedBendPanel(doc, profile_a=profiles[0] if len(profiles) > 0 else None,
                                        profile_b=profiles[1] if len(profiles) > 1 else None))


class _ExportFlatDXF(_Command):
    name = "SheetMetal_ExportFlatDXF"

    def IsActive(self):
        if FreeCAD.ActiveDocument is None:
            return False
        sel = Gui.Selection.getSelection()
        return len(sel) == 1 and P.is_unfold(sel[0])

    def Activated(self):
        doc = _doc()
        sel = Gui.Selection.getSelection()
        if len(sel) != 1 or not P.is_unfold(sel[0]):
            FreeCAD.Console.PrintError("SheetMetal: select one Unfold feature to export its flat pattern\n")
            return
        _open(lambda: P.ExportDxfPanel(doc, obj=sel[0]))


for _cls in (_CornerRelief, _LighteningCutout, _LoftedBend, _ExportFlatDXF):
    Gui.addCommand(_cls.name, _cls())
