# SPDX-License-Identifier: LGPL-2.1-or-later
# Weldment workbench (cad-fork G07 slice 5a/5b): structural member, corner trim, gusset, end cap, cut list.

import FreeCAD
import FreeCADGui as Gui

FreeCAD.__unit_test__ += ["TestWeldmentGui"]


class WeldmentWorkbench(Gui.Workbench):
    MenuText = "Weldment"
    ToolTip = "Structural members from profile tables, corner trims and cut lists"
    # InitGui.py is exec()d, so a class body cannot see names imported at module level: import inside the expression
    Icon = __import__("WeldViewProviders").icon_path("Weldment_Workbench")

    def Initialize(self):
        import WeldCommands  # noqa: F401  (registers the commands)

        self.appendToolbar("Weldment", WeldCommands.TOOLBAR)
        self.appendMenu("Weldment", WeldCommands.MENU)

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(WeldmentWorkbench())
