# SPDX-License-Identifier: LGPL-2.1-or-later
"""Scenes and helpers shared by the interference GUI tests and the harness screenshot scenes (G06 slice 1b).

The document is always built from a case of ``interference_corpus`` (default ``mixed_all_classes``: one
interference, one touching pair, one close pair and one violated clearance set), never from a hand-made file.
"""

import FreeCAD as App
import FreeCADGui as Gui

from AssemblyTests import interference_corpus as C

DEFAULT_CASE = "mixed_all_classes"


def scene_doc(name, case=DEFAULT_CASE):
    """A fresh document holding the corpus case; returns (doc, assembly, truth)."""
    doc = App.newDocument(name)
    asm, truth = C.build_case(doc, case)
    if App.GuiUp:
        for obj in asm.Group:
            obj.ViewObject.Visibility = True
    return doc, asm, truth


def scene_panel(asm, params=None, case=DEFAULT_CASE):
    """The panel an ``interference_panel`` scene shows: clearance from the case (or ``params``), one row selected if
    ``params["select"]`` names one. Returns the panel; nothing is shown yet."""
    from InterferencePanel import TaskCheckInterference

    p = dict(params or {})
    clearance = float(p.get("clearance", C.CASES[case].params.get("clearance", 1.0)))
    panel = TaskCheckInterference(asm, clearance=clearance, include_hidden=bool(p.get("include_hidden", False)))
    sel = p.get("select")
    if sel is not None:
        table, row = sel
        (panel.select_pair if table == "pairs" else panel.select_violation)(int(row))
    return panel


def grab_png(panel, path, size):
    """Save the panel's form as a PNG at a fixed size and style, with no focus state."""
    from PySide import QtWidgets

    w = panel.form
    w.setStyle(QtWidgets.QStyleFactory.create("Fusion"))
    w.setFixedSize(*size)
    w.ensurePolished()
    w.layout().activate()
    w.show()
    for _ in range(4):
        QtWidgets.QApplication.processEvents()
    img = w.grab().toImage()
    img.save(path)
    w.hide()
    return img.width(), img.height()
