# SPDX-License-Identifier: LGPL-2.1-or-later
"""Scenes and helpers shared by the Configurations panel GUI tests and the harness screenshot scene (G06 slice 5b).

The document is the 5a corpus rig (a ground plate with the cubes A to D and a slider C, an assembly cut ``Cut1``)
with three configurations made through the 5a API: ``Light`` (suppresses LB, moves the slider to 90 mm), ``Short``
(suppresses LA and LD) and ``NoCut`` (suppresses the cut). ``scene_doc`` returns it with ``Light`` active and one
overridden joint value edited since (drift), the state the panel shows in its screenshot.
"""

import json
import re

import FreeCAD as App

import AssemblyConfiguration as AC
from AssemblyTests import configuration_corpus as C
from AssemblyTests import feature_gui_fixture as FG

_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
SLIDER = "JCd"
SLIDER_FAR = 90.0


class Rig:
    """The document, its assembly and the names the tests use."""

    def __init__(self, doc, built):
        self.doc = doc
        self.asm = built.asm
        self.built = built

    def close(self):
        if self.doc.Name in App.listDocuments():
            App.closeDocument(self.doc.Name)


def build(name="CfgGui", configurations=True):
    doc = App.newDocument(name)
    b = C.rig(doc)
    C.rig_feature(b)
    C.finish(b)
    if configurations:
        AC.create(b.asm, "Light", suppress=["LB"], joints={SLIDER: {"Distance": SLIDER_FAR}})
        AC.create(b.asm, "Short", suppress=["LA", "LD"])
        AC.create(b.asm, "NoCut", features={"Cut1": True})
    return Rig(doc, b)


def edit_slider(rig, value=80.0):
    """Edit the overridden joint value the way a user does (a drift of the active configuration)."""
    rig.doc.getObject(SLIDER).Distance = value
    rig.doc.recompute()


def scene_doc(name, case=None):
    rig = build("CfgScene")
    AC.activate(rig.asm, "Light")
    edit_slider(rig, 80.0)
    return rig


def scene_panel(rig):
    """The panel a harness scene shows: opened on the assembly, ``Light`` selected. Nothing is shown yet."""
    from ConfigurationsPanel import TaskConfigurations

    panel = TaskConfigurations(rig.asm)
    panel.select("Light")
    return panel


grab_png = FG.grab_png


def signature(rig):
    """Everything the 5a corpus compares: every object and property, configuration objects included."""
    sig = json.loads(json.dumps(AC.full_signature(rig.doc), default=str))
    for rec in sig.values():
        for k in [k for k, v in rec["props"].items() if k in ("Id", "Uid") or (isinstance(v, str) and _GUID.match(v))]:
            rec["props"].pop(k)            # GUIDs differ between two documents
    return sig
