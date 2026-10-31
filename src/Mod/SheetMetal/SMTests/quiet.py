# SPDX-License-Identifier: LGPL-2.1-or-later
"""Silence FreeCAD's console error output around a recompute that is *meant* to fail.

A Python feature whose ``execute`` raises is reported by FreeCAD with the full traceback on the
error console, even when the test provokes the refusal on purpose (a bad angle, a root face on a
wall). The refusal itself is still asserted by the tests (the object's ``State`` carries
``Invalid``); only the console noise is dropped, and only inside the ``with`` block. Anything
raised outside it, or an unexpected pass, is still visible.
"""

import contextlib

import FreeCAD


@contextlib.contextmanager
def expected_recompute_errors():
    was = FreeCAD.Console.GetStatus("Console", "Err")
    FreeCAD.Console.SetStatus("Console", "Err", False)
    try:
        yield
    finally:
        FreeCAD.Console.SetStatus("Console", "Err", was)
