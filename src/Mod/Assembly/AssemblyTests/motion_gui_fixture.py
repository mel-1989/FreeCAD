# SPDX-License-Identifier: LGPL-2.1-or-later
"""Scenes and helpers shared by the dynamic-analysis GUI tests and the harness screenshot scene (G06 slice 6b).

The documents are built from cases of ``motion_corpus`` (slice 6a), so they have a closed-form truth: ``pendulum`` (the
30 degree simple pendulum, gravity), ``spring`` (an undamped 1 kg block on a 200 N/m spring, x = 200 + 30 cos(wt) mm), ``damped`` (a 1 kg block on a rail between a spring and a damper, zeta 0.3) and ``force``
(a constant force on a slider). The span of the pendulum is cut to 2 s at 0.01 s so a GUI run takes a moment; the other two keep
the span their corpus case chose. Each is built in ``single`` form (the parts sit in the assembly) or ``link`` form
(``App::Link`` components whose sources are outside the assembly), and the document is never run here: the study has no result
until a panel or the API runs it.
"""

import FreeCAD as App

import AssemblyMotion as MO
from AssemblyTests import feature_gui_fixture as FG
from AssemblyTests import motion_corpus as MC

KINDS = ("pendulum", "damped", "force", "spring")


class Rig:
    """The document and the names the tests use."""

    def __init__(self, kind, case):
        self.kind = kind
        self.case = case
        self.rig = case.rig
        self.doc = case.rig.doc
        self.asm = case.rig.asm
        self.study = case.study
        self.parts = case.rig.parts

    def close(self):
        if self.doc.Name in App.listDocuments():
            App.closeDocument(self.doc.Name)


def _builder(kind):
    if kind == "pendulum":
        return MC.CASES["pend_30_L400"]
    if kind == "damped":
        fn = MC.make_damped("_gui_damped", 0.3)
        MC.CASES.pop("_gui_damped")           # a GUI fixture is not a corpus case
        return fn
    if kind == "force":
        fn = MC.make_force("_gui_force", 0.0, (10.0, 0.0, 0.0), "World")
        MC.CASES.pop("_gui_force")
        return fn
    if kind == "spring":
        return MC.CASES["spring_mass_k200"]       # undamped 1 kg block on a 200 N/m spring: x = 200 + 30 cos(wt) mm
    raise KeyError(kind)


def build(kind="pendulum", mode="single", name="MotionGui", host=None, directory=None):
    doc = App.newDocument(name)
    if directory:                          # links into another document need both documents saved
        import os
        doc.saveAs(os.path.join(directory, name + ".FCStd"))
    case = _builder(kind)(doc, mode, host)
    if kind == "pendulum":
        MO._assign(case.study, {"TimeEnd": 2.0, "TimeStep": 0.01})
    doc.recompute()
    return Rig(kind, case)


def scene_doc(name, case=None):
    return build("damped", "single", "MotionScene")


def scene_panel(rig):
    """The panel a harness scene shows: opened on the damped-oscillator study, run, the spring's force plotted, the frame
    a third of the way in. Nothing is shown yet and no timer runs."""
    from MotionStudyPanel import TaskMotionStudy

    panel = TaskMotionStudy(rig.study)
    panel.run()
    panel.choose("force:Spring")
    panel.show_frame((len(panel.result["t"]) - 1) // 3)
    return panel


grab_png = FG.grab_png


def placements(asm):
    """Name -> placement of every component, as exact tuples."""
    return {o.Name: (tuple(o.Placement.Base), tuple(o.Placement.Rotation.Q)) for o in MO.live_components(asm)}


def world_snapshot(docs):
    """Everything Cancel must leave alone, per open document: the object-and-property signature (shape hashes included), the
    undo and redo counts and the placements of the components of every assembly."""
    out = {}
    for d in docs:
        asms = [o for o in d.Objects if o.isDerivedFrom("Assembly::AssemblyObject")]
        out[d.Name] = {"signature": MO.signature(d), "undo": d.UndoCount, "redo": d.RedoCount,
                       "placements": {a.Name: placements(a) for a in asms}}
    return out
