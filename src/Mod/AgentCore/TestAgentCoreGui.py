# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run the AgentCore GUI tests: FreeCAD -t TestAgentCoreGui (mini only, it needs a GUI)."""

import unittest

from AgentCoreGuiTests import load_tests  # noqa: F401  (unittest protocol)
