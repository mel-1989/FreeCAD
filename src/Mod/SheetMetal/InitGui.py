########################################################################
#
#  InitGui.py
#
#  Copyright 2015 Shai Seger <shaise at gmail dot com>
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 2 of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU Lesser General Public
#  License along with this program; if not, write to the Free Software
#  Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
#  MA 02110-1301, USA.
#
#
########################################################################
#
#  Adapted for cad-fork core (2026-10-31) from shaise/FreeCAD_SheetMetal cbf6595.
#  Modified for cad-fork on 2026-11-01 (LGPL-2.1 section 2a): names are imported inside the class body and methods, because FreeCAD exec()s InitGui.py with separate globals and locals; registers TestSheetMetalGui.
#  Modified for cad-fork on 2026-11-01 (LGPL-2.1 section 2a, slice 6b lane B): appends the corner relief, lightening cutout, lofted bend and flat DXF export commands after the vendored ones.
# SPDX-License-Identifier: LGPL-2.1-or-later
# SheetMetal workbench. Only the commands of the vendored LGPL files are registered; the edge flange,
# hem, convert-from-solid, corner relief, junction, sketch and cutout commands arrive in later slices.

import FreeCAD
import FreeCADGui as Gui

FreeCAD.__unit_test__ += ["TestSheetMetalGui"]


class SMWorkbench(Workbench):
    MenuText = FreeCAD.Qt.translate("SheetMetal", "Sheet Metal")
    ToolTip = FreeCAD.Qt.translate(
        "SheetMetal",
        "Sheet Metal workbench allows for designing and unfolding sheet metal parts",
    )
    # InitGui.py is exec()d with separate globals and locals, so module-level names are invisible in a class
    # body and its methods: import what each one needs inside it.
    Icon = __import__("os").path.join(__import__("SheetMetalTools").icons_path, "SMLogo.svg")

    def Initialize(self):
        """Execute when FreeCAD starts."""
        import SheetMetalBaseCmd
        import SheetMetalTools
        from engineering_mode import engineering_mode_enabled
        import SheetMetalBend
        import SheetMetalExtendCmd
        import SheetMetalFoldCmd
        import SheetMetalFormingCmd
        import SheetMetalRelief
        import SheetMetalUnfoldCmd
        import SheetMetalUnfolder
        import SMShapeCommands

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
        self.list += SMShapeCommands.TOOLBAR
        if engineering_mode_enabled():
            self.list.insert(self.list.index("SheetMetal_Unfold") + 1, "SheetMetal_UnattendedUnfold")

        self.appendToolbar(FreeCAD.Qt.translate("SheetMetal", "Sheet Metal"), self.list)
        self.appendMenu(FreeCAD.Qt.translate("SheetMetal", "&Sheet Metal"), self.list)
        Gui.addIconPath(SheetMetalTools.icons_path)

    def Activated(self):
        return

    def Deactivated(self):
        return

    def ContextMenu(self, recipient):
        self.appendContextMenu(FreeCAD.Qt.translate("SheetMetal", "Sheet Metal"), self.list)

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(SMWorkbench())
