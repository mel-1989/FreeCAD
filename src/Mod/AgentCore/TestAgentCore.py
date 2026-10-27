# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run every AgentCore test module: FreeCADCmd -t TestAgentCore."""

import unittest

from AgentCoreTests import load_tests  # noqa: F401  (unittest protocol)
