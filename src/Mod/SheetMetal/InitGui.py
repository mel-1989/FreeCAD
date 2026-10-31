# SPDX-License-Identifier: LGPL-2.1-or-later
# SheetMetal workbench. Only the commands of the vendored LGPL files are registered; the edge flange,
# hem, convert-from-solid, corner relief, junction, sketch and cutout commands arrive in later slices.

import os

import FreeCAD

import SheetMetalTools
from engineering_mode import engineering_mode_enabled

Gui = FreeCAD.Gui
SMWBPath = SheetMetalTools.mod_path
SMIconPath = SheetMetalTools.icons_path


class SMWorkbench(Workbench):
    MenuText = FreeCAD.Qt.translate("SheetMetal", "Sheet Metal")
    ToolTip = FreeCAD.Qt.translate(
        "SheetMetal",
        "Sheet Metal workbench allows for designing and unfolding sheet metal parts",
    )
    Icon = os.path.join(SMIconPath, "SMLogo.svg")

    def Initialize(self):
        """Execute when FreeCAD starts."""
        import SheetMetalBaseCmd
        import SheetMetalBend
        import SheetMetalExtendCmd
        import SheetMetalFoldCmd
        import SheetMetalFormingCmd
        import SheetMetalRelief
        import SheetMetalUnfoldCmd
        import SheetMetalUnfolder

        self.list = [
            "SheetMetal_AddBase",
            "SheetMetal_Extrude",
            "SheetMetal_ExtendBySketch",
            "SheetMetal_AddFoldWall",
            "SheetMetal_Unfold",
            "SheetMetal_UnfoldUpdate",
            "SheetMetal_AddRelief",
            "SheetMetal_AddBend",
            "SheetMetal_Forming",
        ]
        if engineering_mode_enabled():
            self.list.insert(self.list.index("SheetMetal_Unfold") + 1, "SheetMetal_UnattendedUnfold")

        self.appendToolbar(FreeCAD.Qt.translate("SheetMetal", "Sheet Metal"), self.list)
        self.appendMenu(FreeCAD.Qt.translate("SheetMetal", "&Sheet Metal"), self.list)
        Gui.addIconPath(SMIconPath)

    def Activated(self):
        return

    def Deactivated(self):
        return

    def ContextMenu(self, recipient):
        self.appendContextMenu(FreeCAD.Qt.translate("SheetMetal", "Sheet Metal"), self.list)

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(SMWorkbench())
