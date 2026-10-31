# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run the PDM server suite (G03 slice 4): FreeCADCmd -t TestPDMServer. Skips when PostgreSQL, Forgejo or the
service venv is missing."""

import unittest

from PDMServerTests import load_tests  # noqa: F401  (unittest protocol)
