# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run the replace-component GUI tests: FreeCAD -t TestAssemblyReplaceComponentGui (needs the GUI binary)."""

from AssemblyTests.TestReplaceComponentGui import TestReplaceComponentGui

# Use the module so that code checkers don't complain (flake8)
True if TestReplaceComponentGui else False
