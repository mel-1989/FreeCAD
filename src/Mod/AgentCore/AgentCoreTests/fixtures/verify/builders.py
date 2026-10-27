# SPDX-License-Identifier: LGPL-2.1-or-later
"""Build the analytic verify fixtures as FreeCAD documents (run inside FreeCADCmd)."""

import json
import math
import os

import FreeCAD
import Part

HERE = os.path.dirname(os.path.abspath(__file__))
TRUTHS = json.load(open(os.path.join(HERE, "truths.json"), encoding="utf-8"))["fixtures"]


def _box(doc, name, dims, pos=(0, 0, 0)):
    b = doc.addObject("Part::Box", name)
    b.Length, b.Width, b.Height = dims
    b.Placement.Base = FreeCAD.Vector(*pos)
    return b


def _material(name):
    import Materials

    for m in Materials.MaterialManager().Materials.values():
        if m.Name == name:
            return m
    raise KeyError(name)


def build(name):
    """Return a recomputed document named after the fixture."""
    t = TRUTHS[name]
    kind = t["kind"]
    doc = FreeCAD.newDocument(name)
    if kind == "box":
        _box(doc, "Box", t["dims"])
    elif kind == "tube":
        o = doc.addObject("Part::Cylinder", "Outer")
        o.Radius, o.Height = t["ro"], t["h"]
        i = doc.addObject("Part::Cylinder", "Inner")
        i.Radius, i.Height = t["ri"], t["h"]
        doc.addObject("Part::Cut", "Tube").Base = o
        doc.Tube.Tool = i
    elif kind == "shell":
        s, w = t["outer"], t["t"]
        o = _box(doc, "Outer", (s, s, s))
        i = _box(doc, "Inner", (s - 2 * w,) * 3, (w, w, w))
        doc.addObject("Part::Cut", "Shell").Base = o
        doc.Shell.Tool = i
    elif kind == "sphere_shell":
        o = doc.addObject("Part::Sphere", "Outer")
        o.Radius = t["ro"]
        i = doc.addObject("Part::Sphere", "Inner")
        i.Radius = t["ri"]
        doc.addObject("Part::Cut", "SphereShell").Base = o
        doc.SphereShell.Tool = i
    elif kind == "pair":
        _box(doc, "A", (t["size"],) * 3)
        _box(doc, "B", (t["size"],) * 3, (t["dx"], 0, 0))
    elif kind == "material_box":
        b = _box(doc, "Box", t["dims"])
        b.ShapeMaterial = _material(t["material"])
    elif kind == "bad_recompute":
        b = _box(doc, "Box", (10, 10, 10))
        doc.addObject("Part::Cut", "Broken").Base = b  # no valid Tool: recompute error
    elif kind == "bad_shape":
        bx = Part.makeBox(10, 10, 10)
        bad = Part.Solid(Part.Shell(bx.Faces[:5]))  # open shell as solid: invalid
        doc.addObject("Part::Feature", "Open").Shape = bad
    else:
        raise ValueError(kind)
    doc.recompute()
    return doc


def tube_volume(t):
    return math.pi * (t["ro"] ** 2 - t["ri"] ** 2) * t["h"]


def save(name, directory):
    doc = build(name)
    path = os.path.join(directory, name + ".FCStd")
    doc.saveAs(path)
    FreeCAD.closeDocument(doc.Name)
    return path
