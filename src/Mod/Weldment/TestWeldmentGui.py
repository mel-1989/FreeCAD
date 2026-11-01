# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run the Weldment GUI tests: FreeCAD -t TestWeldmentGui (needs the GUI binary; Qt's offscreen platform is enough)."""

import unittest

from WeldGuiTests import load_tests  # noqa: F401  (unittest protocol)
