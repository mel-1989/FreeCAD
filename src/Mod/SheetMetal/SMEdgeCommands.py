# SPDX-License-Identifier: LGPL-2.1-or-later
"""GUI commands for the edge flange, hem, convert-from-solid and jog features (cad-fork G07 slice 6b, lane A).

Imported only from InitGui. Each command reads the selection, opens its task panel (``SMEdgePanels``) prefilled
from it and leaves the rest to the panel: one transaction on OK, nothing on Cancel, refusals shown as text.

Clean room: written from the FreeCAD Python command API and the Weldment commands of this fork.
"""

import FreeCAD
import FreeCADGui as Gui

import SMEdgePanels as P
import SMEdgeViewProviders as VP

#: command name -> (menu text, tooltip, icon name); the order is the toolbar order
COMMANDS = {
    "SheetMetal_EdgeFlange": ("Edge flange", "Select the straight end edges of a sheet-metal part (Ctrl-click for several), then add a bend and a straight wall to them", "SheetMetal_EdgeFlange"),
    "SheetMetal_Hem": ("Hem", "Select the straight end edges of a sheet-metal part, then fold the sheet back on itself with a closed or open hem", "SheetMetal_Hem"),
    "SheetMetal_ConvertSolid": ("Convert solid to sheet metal", "Select a solid, then pick its fixed face, the edges that become bends and the faces left open", "SheetMetal_ConvertSolid"),
    "SheetMetal_Jog": ("Jog", "Select a planar face of a sheet-metal part and, with Ctrl, a sketch holding one straight line on it, then offset the sheet beyond the line", "SheetMetal_Jog"),
}
TOOLBAR = list(COMMANDS)


def _open(make):
    """Show the panel ``make()`` builds; a refusal to even open it goes to the report view as one line."""
    if Gui.Control.activeDialog():
        FreeCAD.Console.PrintWarning("SheetMetal: close the open task panel first\n")
        return None
    try:
        return P.show(make())
    except Exception as e:    # noqa: BLE001  (never a traceback in the report view)
        FreeCAD.Console.PrintError("SheetMetal: %s\n" % (e.args[0] if e.args else e))
        return None


class _Command:
    name = ""

    def GetResources(self):
        text, tip, icon = COMMANDS[self.name]
        return {"Pixmap": VP.icon_path(icon), "MenuText": text, "ToolTip": tip}

    def IsActive(self):
        return FreeCAD.ActiveDocument is not None and bool(Gui.Selection.getSelectionEx())


class _EdgeCommand(_Command):
    def IsActive(self):
        return FreeCAD.ActiveDocument is not None and P.has_sub("Edge")

    def panel(self, doc, base, edges):
        raise NotImplementedError

    def Activated(self):
        doc = FreeCAD.ActiveDocument
        picks = P.selected("Edge")
        base = picks[0][0] if picks else None
        edges = picks[0][1] if picks else []
        _open(lambda: self.panel(doc, base, edges))


class _EdgeFlange(_EdgeCommand):
    name = "SheetMetal_EdgeFlange"

    def panel(self, doc, base, edges):
        return P.EdgeFlangePanel(doc, base=base, edges=edges)


class _Hem(_EdgeCommand):
    name = "SheetMetal_Hem"

    def panel(self, doc, base, edges):
        return P.HemPanel(doc, base=base, edges=edges)


class _ConvertSolid(_Command):
    name = "SheetMetal_ConvertSolid"

    def Activated(self):
        doc = FreeCAD.ActiveDocument
        sel = Gui.Selection.getSelectionEx()
        src = sel[0].Object if sel else None
        faces = [n for n in sel[0].SubElementNames if n.startswith("Face")] if sel else []
        edges = [n for n in sel[0].SubElementNames if n.startswith("Edge")] if sel else []
        _open(lambda: P.ConvertSolidPanel(doc, source=src, fixed=faces[0] if len(faces) == 1 else None, bends=edges))


class _Jog(_Command):
    name = "SheetMetal_Jog"

    def IsActive(self):
        return FreeCAD.ActiveDocument is not None and P.has_sub("Face")

    def Activated(self):
        doc = FreeCAD.ActiveDocument
        panel = P.JogPanel(doc)
        panel.take_selection()    # fills what the selection gives; the panel says what is missing
        panel.show_error("")
        _open(lambda: panel)


for _cls in (_EdgeFlange, _Hem, _ConvertSolid, _Jog):
    Gui.addCommand(_cls.name, _cls())
