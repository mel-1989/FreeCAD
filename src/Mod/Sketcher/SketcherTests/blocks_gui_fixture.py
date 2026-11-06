# SPDX-License-Identifier: LGPL-2.1-or-later
"""Fixture of the sketch blocks panel's GUI scene (G10 slice 5c): a sketch with a three-member block 'Slot' placed three
times and one plain line, in edit mode, the panel open with the second instance chosen so its members are selected.

Used by the GUI harness (the snapshot scene, the lint and theme collectors); the suite TestSketcherBlocksPanelGui builds
its own sketches.  Nothing here shows a window on its own: the harness decides what to show."""

import FreeCAD as App
import FreeCADGui as Gui
import Part

import SketcherBlocks as B
from SketcherTests.diagnosis_gui_fixture import Rig, grab_png, pump  # noqa: F401  (grab_png is re-exported)

V = App.Vector


def scene_doc(name="BlocksScene"):
    """A document with one sketch: block 'Slot' (two lines and a circle) placed three times, and a plain line."""
    doc = App.newDocument("BlocksScene")
    sk = doc.addObject("Sketcher::SketchObject", "Sketch")
    sk.addGeometry(
        [
            Part.LineSegment(V(0, 0, 0), V(20, 0, 0)),
            Part.LineSegment(V(20, 0, 0), V(20, 6, 0)),
            Part.Circle(V(6, 3, 0), V(0, 0, 1), 2.0),
        ],
        False,
    )
    B.define(sk, "Slot", [0, 1, 2])
    sk.delGeometries([0, 1, 2])
    B.insert(sk, "Slot", 0.0, 0.0, 0.0)
    B.insert(sk, "Slot", 30.0, 5.0, 0.4)
    B.insert(sk, "Slot", 10.0, 25.0, -0.3)
    sk.addGeometry(Part.LineSegment(V(-10, -8, 0), V(50, -8, 0)), False)
    doc.recompute()
    return Rig(doc, sk)


def scene_panel(rig, instance="Slot_2"):
    """Edit mode on the sketch, the panel opened, `instance` chosen.  The panel is a free-floating dock (not in the main
    window's layout), so its size is the caller's to fix."""
    import SketcherBlocksPanel as P

    App.setActiveDocument(rig.doc.Name)
    Gui.ActiveDocument = Gui.getDocument(rig.doc.Name)
    Gui.ActiveDocument.setEdit(rig.sketch.Name)
    pump()
    Gui.Selection.clearSelection()
    panel = P.BlocksPanel(rig.sketch, parent=Gui.getMainWindow())
    panel.setFloating(True)
    rig.panel = panel
    panel._choose_row("inst", instance)
    pump()
    panel.select_instance(instance)
    pump()
    return panel
