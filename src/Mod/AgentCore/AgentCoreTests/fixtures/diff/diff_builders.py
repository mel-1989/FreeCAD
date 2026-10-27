# SPDX-License-Identifier: LGPL-2.1-or-later
"""F3 fixtures: scripted agent turns, a holed block, an XLink document pair."""

import os
import sys

import FreeCAD as App

V = App.Vector
HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FIX = os.path.join(os.path.dirname(HERE), "state")
if STATE_FIX not in sys.path:
    sys.path.insert(0, STATE_FIX)

import builders as state_builders  # noqa: E402  (F1's fixture corpus)

F1_FIXTURES = state_builders.ALL
EDIT_TYPES = ("App::PropertyLength", "App::PropertyDistance", "App::PropertyFloat")


def open_f1_fixture(key):
    doc = state_builders.open_fixture(key)
    doc.UndoMode = 1
    return doc


def _editable_number(doc):
    """(object, property) of a plain numeric input that no expression drives."""
    for o in doc.Objects:
        bound = {str(p) for p, _ in getattr(o, "ExpressionEngine", [])}
        for p in o.PropertiesList:
            if p in bound or p.startswith("_"):
                continue
            try:
                if o.getTypeIdOfProperty(p) not in EDIT_TYPES:
                    continue
                st = set(o.getPropertyStatus(p))
                if st & {2, 3, 7, 24, 26, 27, "ReadOnly", "Hidden", "Output"}:
                    continue
                if o.getGroupOfProperty(p) in ("Base", ""):
                    continue
                return o, p
            except Exception:
                continue
    return None, None


def _leaf(doc, exclude):
    for o in reversed(doc.Objects):
        if o.Name in exclude or o.InList:
            continue
        return o
    return None


def scripted_turn(doc, nested=True):
    """Add, edit and delete features, with one GUI-style nested transaction.

    Returns a dict naming what was done (for assertions).
    """
    done = {}
    obj, prop = _editable_number(doc)
    if obj is not None:
        v = getattr(obj, prop)
        v = v.Value if hasattr(v, "Value") else float(v)
        setattr(obj, prop, v * 1.1 + 0.5)
        done["edited"] = f"{obj.Name}.{prop}"
    first = doc.Objects[0] if doc.Objects else None
    if first is not None:
        first.Label = first.Label + " (agent)"
        done["relabelled"] = first.Name
    if nested:  # what a GUI command does through doCommand
        doc.openTransaction("Create box")
    box = doc.addObject("Part::Box", "AgentBox")
    box.Length = 7
    box.Placement.Base = V(100, 0, 0)
    if nested:
        doc.commitTransaction()
    cyl = doc.addObject("Part::Cylinder", "AgentCyl")
    cyl.setExpression("Height", "AgentBox.Length * 2")
    done["added"] = [box.Name, cyl.Name]
    leaf = _leaf(doc, set(done["added"]))
    if leaf is not None:
        done["deleted"] = leaf.Name
        doc.removeObject(leaf.Name)
    doc.recompute()
    return done


def build_holed_block(name="f3_holed_block", radius=3.0):
    """Part::Cut of a 40x30x10 box and a vertical cylinder of ``radius``."""
    doc = App.newDocument(name)
    doc.UndoMode = 1
    box = doc.addObject("Part::Box", "Block")
    box.Length, box.Width, box.Height = 40, 30, 10
    cyl = doc.addObject("Part::Cylinder", "Hole")
    cyl.Radius, cyl.Height = radius, 20
    cyl.Placement.Base = V(10, 10, -5)
    cut = doc.addObject("Part::Cut", "Cut")
    cut.Base, cut.Tool = box, cyl
    doc.recompute()
    return doc


def build_xlink_pair(folder, prefix="f3x"):
    """Two saved documents: ``<prefix>_src`` has a box and a cylinder,
    ``<prefix>_asm`` holds App::Link objects (XLinks) to them. Both are saved
    first because an XLink to an unsaved document is refused."""
    os.makedirs(folder, exist_ok=True)
    src = App.newDocument(f"{prefix}_src")
    src.UndoMode = 1
    box = src.addObject("Part::Box", "SrcBox")
    box.Length = 12
    cyl = src.addObject("Part::Cylinder", "SrcCyl")
    cyl.Radius = 2
    src.recompute()
    src.saveAs(os.path.join(folder, f"{prefix}_src.FCStd"))
    asm = App.newDocument(f"{prefix}_asm")
    asm.UndoMode = 1
    asm.saveAs(os.path.join(folder, f"{prefix}_asm.FCStd"))
    lk = asm.addObject("App::Link", "LinkBox")
    lk.LinkedObject = box
    lk.Placement.Base = V(0, 50, 0)
    asm.recompute()
    asm.save()
    src.save()
    return src, asm


def close(*docs):
    for d in docs:
        try:
            if d is not None and d.Name in App.listDocuments():
                App.closeDocument(d.Name)
        except Exception:
            pass
