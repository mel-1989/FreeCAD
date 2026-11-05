# SPDX-License-Identifier: LGPL-2.1-or-later
"""Fixture of the relations panel's GUI scene (G10 slice 4c): one generated corpus sketch in edit mode, an edge selected,
the panel open with its first relation chosen and the view-only filter on, so the filter notice is on show.

Used by the GUI harness (the snapshot scene, the lint and theme collectors); the suite TestSketcherRelationsPanelGui
builds its own sketches.  Nothing here shows a window on its own: the harness decides what to show."""

import FreeCAD as App
import FreeCADGui as Gui

from SketcherTests import SketchAutoDefineCorpus as Corpus
from SketcherTests.diagnosis_gui_fixture import Rig, grab_png, pump  # noqa: F401  (grab_png is re-exported)

DEFAULT_CASE = "rounded_rect"


def scene_doc(name, case=DEFAULT_CASE):
    """A document holding one generated, fully constrained corpus case."""
    doc = App.newDocument("RelationsScene")
    gen = Corpus.generated(doc, variants=1, families=[case], rotations=(False,))[0]
    doc.recompute()
    return Rig(doc, gen.sketch)


def scene_panel(rig, edge="Edge1"):
    """Edit mode on the sketch, the panel opened with `edge` selected, its first relation chosen and the filter on.  The
    panel is a free-floating dock (not in the main window's layout), so its size is the caller's to fix."""
    import SketcherRelationsPanel as P

    App.setActiveDocument(rig.doc.Name)
    Gui.ActiveDocument = Gui.getDocument(rig.doc.Name)
    Gui.ActiveDocument.setEdit(rig.sketch.Name)
    pump()
    Gui.Selection.clearSelection()
    panel = P.RelationsPanel(rig.sketch, parent=Gui.getMainWindow())
    panel.setFloating(True)
    rig.panel = panel
    Gui.Selection.addSelection(rig.doc.Name, rig.sketch.Name, edge)
    pump()
    panel.filter_to_element()
    if panel.tree.topLevelItemCount():
        panel.tree.setCurrentItem(panel.tree.topLevelItem(0))
    pump()
    return panel
