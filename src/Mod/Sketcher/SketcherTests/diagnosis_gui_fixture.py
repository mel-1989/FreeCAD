# SPDX-License-Identifier: LGPL-2.1-or-later
"""Fixture of the diagnosis panel's GUI scenes (G10 slice 4a): one corpus sketch in edit mode with the panel open.

Used by the GUI harness (the snapshot scene, the lint and theme collectors); the suite TestSketcherDiagnosisPanelGui
builds its own rig.  Nothing here shows a window on its own: the harness decides what to show."""

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtWidgets

import SketcherDiagnosis as SD
from SketcherTests import SketchDiagnosisCorpus as Corpus

DEFAULT_CASE = "two_overlapping_conflicts"


def pump(n=4):
    for _ in range(n):
        QtWidgets.QApplication.processEvents()
    Gui.updateGui()


class Rig:
    def __init__(self, doc, sketch):
        self.doc = doc
        self.sketch = sketch
        self.panel = None

    def close(self):
        if self.panel is not None:
            try:
                self.panel.close()
            except RuntimeError:
                pass
            self.panel = None
        pump()
        gdoc = Gui.getDocument(self.doc.Name) if self.doc.Name in App.listDocuments() else None
        if gdoc is not None and gdoc.getInEdit() is not None:
            gdoc.resetEdit()
        pump()
        if self.doc.Name in App.listDocuments():
            App.closeDocument(self.doc.Name)


def scene_doc(name, case=DEFAULT_CASE):
    """A document holding only the corpus case `case`."""
    doc = App.newDocument("DiagnosisScene")
    cases = Corpus.build_synthetic(doc)
    keep = [c for c in cases if c.name == case][0]
    for c in cases:
        if c is not keep:
            doc.removeObject(c.sketch.Name)
    doc.recompute()
    return Rig(doc, keep.sketch)


def scene_panel(rig, choose_first_fix=True):
    """Edit mode on the sketch, the panel opened and diagnosed, the first fix of the first conflict chosen.  The
    panel is a free-floating dock (not in the main window's layout), so its size is the caller's to fix."""
    import SketcherDiagnosisPanel as P

    App.setActiveDocument(rig.doc.Name)
    Gui.ActiveDocument = Gui.getDocument(rig.doc.Name)
    Gui.ActiveDocument.setEdit(rig.sketch.Name)
    pump()
    panel = P.DiagnosisPanel(rig.sketch, parent=Gui.getMainWindow(), auto_run=False)
    panel.setFloating(True)
    panel.diagnose_now()
    rig.panel = panel
    if choose_first_fix and panel.result and panel.result["conflict_sets"]:
        top = panel.tree.topLevelItem(0)
        panel.tree.setCurrentItem(top.child(len(panel.result["conflict_sets"][0]["members"])))
    pump()
    return panel


def grab_png(panel, path, size):
    """Save the panel's form as a PNG at a fixed size and style, with no focus state."""
    w = panel.widget()
    w.setStyle(QtWidgets.QStyleFactory.create("Fusion"))
    w.setParent(None)
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
