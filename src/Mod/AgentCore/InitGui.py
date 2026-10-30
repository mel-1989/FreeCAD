# SPDX-License-Identifier: LGPL-2.1-or-later
# AgentCore GUI side: the change-set review panel (F3). The package AgentCore.gui is imported only
# on demand, never from AgentCore/__init__.py, so FreeCADCmd keeps working without FreeCADGui.

import FreeCAD

FreeCAD.__unit_test__ += ["TestAgentCoreGui"]

# No workbench: the command is reachable as FreeCADGui.runCommand("AgentCore_ReviewPending") and from
# Tools > Customize (Commands), where it can be put on any toolbar or menu.
from AgentCore.gui import commands as _agentcore_commands  # noqa: E402

_agentcore_commands.register()
