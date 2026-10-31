# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run every PDM test module: FreeCADCmd -t TestPDM."""

import unittest

from PDMTests import load_tests  # noqa: F401  (unittest protocol)
