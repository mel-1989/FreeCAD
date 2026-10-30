# SPDX-License-Identifier: LGPL-2.1-or-later
"""Script run inside the FreeCAD GUI by ``harness/gui/run_gui.py script`` (through ``gui_wrap.py``).

``PARAMS["spec"]`` is a JSON object: ``file`` (.FCStd, .step/.stp or .brep), ``out`` (PNG path), ``view``,
``width``, ``height``, ``background`` and optionally ``thickness_map`` (``{"min_ok": mm, "samples": n}``).
It writes ``<out>.json`` with the outcome (``ok``, ``error``, the thickness summary)."""
import json
import os
import traceback

import FreeCAD as App

spec = json.loads(PARAMS["spec"])  # noqa: F821  (injected by gui_wrap.py)
result = {"ok": False, "error": None}
try:
    from AgentCore.render import gui as render_gui

    path = spec["file"]
    ext = os.path.splitext(path)[1].lower()
    if ext == ".fcstd":
        doc = App.openDocument(path)
    elif ext in (".step", ".stp", ".brep", ".brp"):
        import Part

        doc = App.newDocument("render")
        Part.insert(path, doc.Name)
        doc.recompute()
    else:
        raise ValueError(f"unsupported file type {ext!r}")
    result["shape_objects"] = sum(1 for o in doc.Objects if getattr(o, "Shape", None) is not None
                                  and not o.Shape.isNull())
    view = render_gui.activate(doc)
    tm = spec.get("thickness_map")
    if tm:
        result["thickness_map"] = render_gui.apply_thickness_map(doc, float(tm["min_ok"]), int(tm.get("samples", 5)))
    result["camera"] = render_gui.save_png(view, spec["out"], spec.get("view", "iso"), spec.get("width", 480),
                                           spec.get("height", 360), spec.get("background", "White"))
    result["ok"] = os.path.exists(spec["out"])
    if not result["ok"]:
        result["error"] = "saveImage wrote no file"
except BaseException:
    result["error"] = traceback.format_exc()
    print(result["error"])
finally:
    with open(spec["out"] + ".json", "w") as f:
        json.dump(result, f)
if not result["ok"]:
    raise RuntimeError(result["error"])
