# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run the PDM GUI tests: FreeCAD -t TestPDMGui (mini only, it needs a GUI)."""

import unittest

from PDMGuiTests import load_tests  # noqa: F401  (unittest protocol)
