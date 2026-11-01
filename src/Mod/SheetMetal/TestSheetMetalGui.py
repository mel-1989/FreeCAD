# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run the SheetMetal GUI tests: FreeCAD -t TestSheetMetalGui (needs the GUI binary; Qt's offscreen platform is enough)."""

import unittest

from SMGuiTests import load_tests  # noqa: F401  (unittest protocol)
