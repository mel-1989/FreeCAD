# SPDX-License-Identifier: LGPL-2.1-or-later
# Weldment workbench (cad-fork G07 slice 5a): structural member, corner trim, cut list.

import FreeCAD
import FreeCADGui as Gui


class WeldmentWorkbench(Gui.Workbench):
    MenuText = "Weldment"
    ToolTip = "Structural members from profile tables, corner trims and cut lists"

    def Initialize(self):
        import WeldCommands  # noqa: F401  (registers the commands)

        self.appendToolbar("Weldment", ["Weldment_Member", "Weldment_Mitre", "Weldment_CutList"])
        self.appendMenu("Weldment", ["Weldment_Member", "Weldment_Mitre", "Weldment_CutList"])

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(WeldmentWorkbench())
