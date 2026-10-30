# SPDX-License-Identifier: LGPL-2.1-or-later
"""GUI-process side of rendering: thickness-map colouring and PNG capture.

Import only inside the FreeCAD GUI (it needs ``FreeCADGui`` and view providers).
"""

import time

import FreeCAD as App
import FreeCADGui as Gui

from AgentCore.render import ramp
from AgentCore.verify import thickness

VIEWS = {"iso": "viewIsometric", "front": "viewFront", "top": "viewTop", "right": "viewRight",
         "left": "viewLeft", "rear": "viewRear", "bottom": "viewBottom"}


def pump(n=3, pause=0.0):
    for _ in range(n):
        Gui.updateGui()
        if pause:
            time.sleep(pause)


def _solid_objects(doc):
    out = []
    for o in doc.Objects:
        vo = getattr(o, "ViewObject", None)
        sh = getattr(o, "Shape", None)
        if vo is None or sh is None or sh.isNull() or not sh.Solids or not vo.Visibility:
            continue
        out.append(o)
    return out


def apply_thickness_map(doc, min_ok, samples=5, objects=None, time_budget_s=None):
    """Colour every face of every visible solid by its thickness (see ``ramp`` for the colour rule).

    Returns a summary: ``min``/``max`` thickness over all coloured faces, ``min_ok``, the number of faces below
    ``min_ok``, faces without a measurement, and per-object face thicknesses under ``objects``."""
    objs = [doc.getObject(n) if isinstance(n, str) else n for n in objects] if objects else _solid_objects(doc)
    maps = []
    for o in objs:
        faces, res = thickness.thickness_map(o.Shape, samples=samples, time_budget_s=time_budget_s)
        maps.append((o, faces, res))
    known = [t for _, faces, _ in maps for t in faces if t is not None]
    t_max = max(known) if known else None
    summary = {"min_ok": min_ok, "min": min(known) if known else None, "max": t_max,
               "faces_total": sum(len(f) for _, f, _ in maps), "faces_below_min_ok": 0, "faces_unmeasured": 0,
               "complete": all(r.get("complete", True) for _, _, r in maps), "objects": {}}
    for o, faces, res in maps:
        cols = ramp.colours(faces, min_ok, t_max)
        # this version's DiffuseColor alpha is opacity (1.0 = opaque; 0.0 renders the face invisible)
        o.ViewObject.DiffuseColor = [(r, g, b, 1.0) for r, g, b in cols]
        summary["faces_below_min_ok"] += sum(1 for t in faces if t is not None and t <= min_ok)
        summary["faces_unmeasured"] += sum(1 for t in faces if t is None)
        summary["objects"][o.Name] = {"faces": faces, "kernel": res.get("kernel")}
    pump(4)
    return summary


def activate(doc):
    App.setActiveDocument(doc.Name)
    Gui.ActiveDocument = Gui.getDocument(doc.Name)
    pump()
    view = Gui.ActiveDocument.ActiveView
    view.setCameraType("Orthographic")
    return view


def save_png(view, path, view_name="iso", width=480, height=360, background="White", camera=None):
    """Orient the view, fit (or apply the stored ``camera``), and save a PNG with a fixed background."""
    if view_name not in VIEWS:
        raise ValueError(f"unknown view {view_name!r}; use one of {sorted(VIEWS)}")
    getattr(view, VIEWS[view_name])()
    pump()
    if camera:
        view.setCamera(camera)
    else:
        view.fitAll()
    pump()
    pump(8, 0.1)
    if camera:
        view.setCamera(camera)
        pump(4, 0.1)
    view.saveImage(path, int(width), int(height), background)
    return view.getCamera()
