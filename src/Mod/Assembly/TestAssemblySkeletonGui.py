# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run the skeleton publish and subscribe GUI tests: FreeCAD -t TestAssemblySkeletonGui (needs the GUI binary)."""

from AssemblyTests.TestSkeletonGui import TestSkeletonGui, TestSkeletonCommands, TestSkeletonStagedIdentity, TestSkeletonRepairs

# Use the module so that code checkers don't complain (flake8)
True if TestSkeletonGui and TestSkeletonCommands and TestSkeletonStagedIdentity and TestSkeletonRepairs else False
