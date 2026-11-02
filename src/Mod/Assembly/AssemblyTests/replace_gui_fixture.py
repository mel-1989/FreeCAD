# SPDX-License-Identifier: LGPL-2.1-or-later
"""Scenes and helpers shared by the replace-component GUI tests and the harness screenshot scene (G06 slice 2b).

Documents are built from ``replace_corpus`` (the slice 2a corpus) or, for the mixed scene, from its ``scene`` builder with
three joints on one bracket: a revolute on the hole (the new part has two equal holes: ambiguous), a planar on the underside
(matched) and a slider on the +x end face (the new part has a sloped end instead: unmatched).
"""

import FreeCAD as App

from AssemblyTests import replace_corpus as C

DEFAULT_CASE = "mixed"
ALIGN_CASE = "n1_bracket_turned_and_gained_a_small_hole"   # a turned bracket that also gained a small hole: ambiguous alignment

def _mixed(doc):
    old = C.bracket(holes=((20, 6, 3),), bosses=((2, 5, 4, 10, 4),))
    new = C.bracket(holes=((10, 6, 3), (30, 6, 3)))
    js = [C.jspec("revolute", (C.cy((20, 6, 0), (0, 0, 1), 3), C.ci((20, 6, 10), (0, 0, 1), 3)), C.UN, name="Rev"),
          C.jspec("planar", C.BR["planar"], C.BR["planar"], name="Plan"),
          C.jspec("slider", C.pl((0, 0, 1), 14), C.UN, name="Slide")]
    return C.scene(doc, old, new, js)


def scene_doc(name, case=DEFAULT_CASE):
    """A fresh document holding the case; returns (doc, built)."""
    doc = App.newDocument(name)
    if case == DEFAULT_CASE:
        doc.UndoMode = 1
        b = _mixed(doc)
        doc.recompute()
    else:
        b = C.build_case(doc, ALIGN_CASE if case == "align" else case)
    if App.GuiUp:
        for obj in doc.Objects:
            if obj.Name in ("GL", "ML") or obj.Name.startswith("Assembly"):
                if getattr(obj, "ViewObject", None):
                    obj.ViewObject.Visibility = True
    return doc, b


def scene_panel(built, params=None):
    """The panel a ``replace_panel`` scene shows: the case's old component, its new part as an object of the document;
    ``params["alignment"]`` (an index into the candidates) settles an ambiguous alignment. Nothing is shown yet."""
    from ReplacePanel import TaskReplaceComponent

    p = dict(params or {})
    panel = TaskReplaceComponent(built.asm, old=built.hosts[0] if built.mode != "source" else None, new=built.new)
    if p.get("alignment") is not None:
        panel.choose_alignment(int(p["alignment"]))
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
