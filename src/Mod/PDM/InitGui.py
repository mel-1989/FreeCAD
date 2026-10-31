# SPDX-License-Identifier: LGPL-2.1-or-later
# PDM GUI (G03 slice 5). The commands are registered globally, so they can be put on any toolbar from
# Tools > Customize, and a small "PDM" workbench shows them as one toolbar and menu. PDMGui is imported only
# here, never from PDM/__init__.py, so FreeCADCmd keeps working without PySide.

import FreeCAD
import FreeCADGui

FreeCAD.__unit_test__ += ["TestPDMGui"]

from PDMGui import commands as _pdm_commands  # noqa: E402

_pdm_commands.register()


class PDMWorkbench(FreeCADGui.Workbench):
    MenuText = "PDM"
    ToolTip = "Vault check-in, revisions, where-used and lifecycle"

    def Initialize(self):
        self.appendToolbar("PDM", _pdm_commands.TOOLBAR)
        self.appendMenu("PDM", _pdm_commands.TOOLBAR)

    def GetClassName(self):
        return "Gui::PythonWorkbench"


FreeCADGui.addWorkbench(PDMWorkbench())
