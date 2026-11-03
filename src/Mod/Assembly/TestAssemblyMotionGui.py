# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run the dynamic-analysis GUI tests: FreeCAD -t TestAssemblyMotionGui (needs the GUI binary)."""

from AssemblyTests.TestMotionGui import TestMotionGui, TestMotionCommands, TestMotionPanel

# Use the module so that code checkers don't complain (flake8)
True if TestMotionGui and TestMotionCommands and TestMotionPanel else False
