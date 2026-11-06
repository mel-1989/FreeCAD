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


def scene_doc(name="BlocksScene", curves=False):
    """A document with one sketch: block 'Slot' (two lines and a circle) placed three times, and a plain line.  With
    `curves` it also holds block 'Curves' (an ellipse, an arc of ellipse and a B-spline) placed once, turned, with the
    pole circles of the spline exposed (slice 5b2's scene)."""
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
    if curves:
        _add_curves(sk)
    doc.recompute()
    return Rig(doc, sk)


def _add_curves(sk):
    ell = Part.Ellipse(V(0, 0, 0), 9.0, 4.0)
    arc = Part.ArcOfEllipse(Part.Ellipse(V(14, 3, 0), 6.0, 2.5), 0.4, 4.0)
    spl = Part.BSplineCurve()
    spl.buildFromPolesMultsKnots([V(-6, 10, 0), V(-2, 16, 0), V(4, 8, 0), V(9, 15, 0), V(14, 10, 0)], [3, 2, 3], [0, 1, 2], False, 2, [1, 2, 1.5, 1, 1])
    n = len(sk.Geometry)
    sk.addGeometry([ell, arc, spl], False)
    B.define(sk, "Curves", [n, n + 1, n + 2])
    sk.delGeometries([n, n + 1, n + 2])
    name = B.insert(sk, "Curves", 20.0, 14.0, 0.6)
    sk.exposeInternalGeometry(B.blocks(sk)["instances"][name]["geo_ids"][2])


def view_doc(name="BlocksCurvesView"):
    """A document with one sketch for the 3D view's picture (slice 5b2): block 'Curves' (an ellipse, an arc of ellipse and a
    B-spline) placed once, turned by 0.6 rad, with the pole circles of the spline exposed.  The caller puts the sketch in edit mode."""
    doc = App.newDocument(name)
    sk = doc.addObject("Sketcher::SketchObject", "Sketch")
    _add_curves(sk)
    doc.recompute()
    return Rig(doc, sk)


def _scene_library(sketch):
    """A library at a fixed path, one good block and one bad file: the panel prints its folder, so a folder named for
    the launch would make every stored picture differ."""
    import os
    import shutil

    import FreeCAD

    import SketcherBlocks as B

    folder = "/tmp/fc-sketch-block-scene"
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder)
    with open(os.path.join(folder, "Damaged.json"), "w", encoding="utf-8") as fh:
        fh.write("not a block")
    B.library_save(sketch, "Slot", folder=folder)
    if "Curves" in B.blocks(sketch)["definitions"]:
        B.library_save(sketch, "Curves", folder=folder)
    FreeCAD.ParamGet(B.LIBRARY_PARAM).SetString("LibraryFolder", folder)
    return folder


def scene_panel(rig, instance="Slot_2"):
    """Edit mode on the sketch, the panel opened, `instance` chosen.  The panel is a free-floating dock (not in the main
    window's layout), so its size is the caller's to fix."""
    import SketcherBlocksPanel as P

    App.setActiveDocument(rig.doc.Name)
    Gui.ActiveDocument = Gui.getDocument(rig.doc.Name)
    Gui.ActiveDocument.setEdit(rig.sketch.Name)
    pump()
    _scene_library(rig.sketch)
    Gui.Selection.clearSelection()
    panel = P.BlocksPanel(rig.sketch, parent=Gui.getMainWindow())
    panel.setFloating(True)
    rig.panel = panel
    panel._choose_row("inst", instance)
    pump()
    panel.select_instance(instance)
    pump()
    return panel
