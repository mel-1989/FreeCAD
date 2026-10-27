# SPDX-License-Identifier: LGPL-2.1-or-later
"""Shared helpers for the F2 verify tests (not collected: name does not start with test_)."""

import os
import sys

import FreeCAD

_FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "verify")
if _FIX not in sys.path:
    sys.path.insert(0, _FIX)

import builders  # noqa: E402

TRUTHS = builders.TRUTHS


class Fixture:
    """Context manager: build a fixture doc and close it afterwards."""

    def __init__(self, name):
        self.name = name

    def __enter__(self):
        self.doc = builders.build(self.name)
        return self.doc

    def __exit__(self, *exc):
        FreeCAD.closeDocument(self.doc.Name)
