# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run the interference GUI tests: FreeCAD -t TestAssemblyInterferenceGui (needs the GUI binary)."""

from AssemblyTests.TestInterferenceGui import TestInterferenceGui

# Use the module so that code checkers don't complain (flake8)
True if TestInterferenceGui else False
