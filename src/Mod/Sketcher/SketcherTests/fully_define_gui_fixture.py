# SPDX-License-Identifier: LGPL-2.1-or-later
"""Fixture of the Fully Define dialog's GUI scenes (G10 slice 4b): one stripped corpus sketch in edit mode with the
dialog open and the first candidate chosen.

Used by the GUI harness (the snapshot scene, the lint and theme collectors); the suite TestSketcherFullyDefinePanelGui
builds its own rig.  Nothing here shows a window on its own: the harness decides what to show."""

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtWidgets

from SketcherTests import SketchAutoDefineCorpus as Corpus
from SketcherTests.diagnosis_gui_fixture import Rig, grab_png, pump  # noqa: F401  (grab_png is re-exported)

DEFAULT_CASE = "rounded_rect"
DEFAULT_LEVEL = "all"


def scene_doc(name, case=DEFAULT_CASE, level=DEFAULT_LEVEL):
    """A document holding one generated corpus case stripped at `level`."""
    doc = App.newDocument("FullyDefineScene")
    gen = Corpus.generated(doc, variants=1, families=[case], rotations=(False,))[0]
    stripped, _ = Corpus.strip(doc, gen.sketch, level, 0.5, 0, name="Stripped")
    doc.removeObject(gen.sketch.Name)
    doc.recompute()
    return Rig(doc, stripped)


def scene_panel(rig, choose_first=True):
    """Edit mode on the sketch, the dialog opened and run, the first candidate chosen.  The dialog is a
    free-floating dock (not in the main window's layout), so its size is the caller's to fix."""
    import SketcherFullyDefinePanel as P

    App.setActiveDocument(rig.doc.Name)
    Gui.ActiveDocument = Gui.getDocument(rig.doc.Name)
    Gui.ActiveDocument.setEdit(rig.sketch.Name)
    pump()
    panel = P.FullyDefinePanel(rig.sketch, parent=Gui.getMainWindow(), auto_run=False)
    panel.setFloating(True)
    panel.diagnose_now()
    rig.panel = panel
    if choose_first and panel.result and panel.result["candidates"]:
        panel.tree.setCurrentItem(panel.tree.topLevelItem(0))
    pump()
    return panel
