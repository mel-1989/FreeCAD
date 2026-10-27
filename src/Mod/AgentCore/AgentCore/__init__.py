# SPDX-License-Identifier: LGPL-2.1-or-later
"""AgentCore: the cad-fork AI-agent layer.

Sub-packages (each owned by one work item, see cad-fork briefs/phase1):

- ``state``    F1, agent-readable model state: versioned JSON, stable entity IDs
- ``verify``   F2, headless verify checks and an isolated FreeCADCmd worker pool
- ``diff``     F3, transactions, change sets and before-state snapshots
- ``mcp``      F4, thin MCP server over the above
- ``refactor`` F6, geometry-invariant refactors and macro scaffolds

Everything here must run under FreeCADCmd with QT_QPA_PLATFORM=offscreen.
"""

__version__ = "0.1.0"
