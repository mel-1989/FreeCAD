# SPDX-License-Identifier: LGPL-2.1-or-later
"""python -m AgentCore.mcp: run the cadfork MCP stdio server."""

import sys

from .server import main

sys.exit(main())
