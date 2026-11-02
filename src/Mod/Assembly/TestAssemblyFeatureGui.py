# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run the assembly-feature GUI tests: FreeCAD -t TestAssemblyFeatureGui (needs the GUI binary)."""

from AssemblyTests.TestFeatureGui import TestFeatureGui

# Use the module so that code checkers don't complain (flake8)
True if TestFeatureGui else False
