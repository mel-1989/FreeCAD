# SPDX-License-Identifier: LGPL-2.1-or-later
"""AgentCore test package. Each test_*.py module here is collected."""

import importlib
import pkgutil
import unittest


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for mod in sorted(m.name for m in pkgutil.iter_modules(__path__) if m.name.startswith("test_")):
        suite.addTests(loader.loadTestsFromModule(importlib.import_module(f"{__name__}.{mod}")))
    return suite
