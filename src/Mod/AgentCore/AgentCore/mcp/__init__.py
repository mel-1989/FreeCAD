# SPDX-License-Identifier: LGPL-2.1-or-later
"""AgentCore.mcp (F4): thin MCP server over F1 (state), F2 (verify) and F3 (diff).

- ``server``         MCP stdio server (pure stdlib, never imports FreeCAD); ``python -m AgentCore.mcp``
- ``workers``        FreeCADCmd session workers, F2 pool loader
- ``bridge``         token-authenticated line-JSON bridge with main-thread marshalling
- ``session``        FreeCAD-side tool bodies for one document (runs inside FreeCAD)
- ``session_worker`` FreeCADCmd entry for a headless session
- ``policy``         exec_code confinement and flagging (not a sandbox)
- ``apidocs``        FreeCAD API reference generated from the .pyi stubs
- ``search``         local parts-corpus search
- ``cookbook``       the cookbook resource

Only ``session`` and ``session_worker`` import FreeCAD, and only when used.
"""

from .server import PROTOCOL_VERSIONS, SERVER_VERSION, TOOL_NAMES, TOOL_SCHEMA  # noqa: F401
