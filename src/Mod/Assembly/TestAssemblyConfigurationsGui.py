# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run the assembly-configurations GUI tests: FreeCAD -t TestAssemblyConfigurationsGui (needs the GUI binary)."""

from AssemblyTests.TestConfigurationsGui import TestConfigurationsGui, TestConfigurationViewProvider

# Use the module so that code checkers don't complain (flake8)
True if TestConfigurationsGui and TestConfigurationViewProvider else False
