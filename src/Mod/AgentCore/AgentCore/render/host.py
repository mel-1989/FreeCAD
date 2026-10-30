# SPDX-License-Identifier: LGPL-2.1-or-later
"""Host-side rendering: launch the cad-fork GUI harness runner and return a PNG (no FreeCAD import).

The runner is ``harness/gui/run_gui.py`` of the cad-fork workshop (brief I4). It provides the single-GUI lock,
the isolated user configuration, the timeout kill and the survivor check; this module only builds its command line.
Where to find things (all optional):

- ``CADFORK_RENDER=0``: disable rendering (``render`` then answers ``unavailable``).
- ``CADFORK_GUI_RUNNER``: path of the workshop's ``harness/gui/run_gui.py`` (required for rendering; without it
  ``render`` answers ``unavailable``). The workshop's ``harness/env.sh`` exports it.
- ``CADFORK_GUI_BUILD_DIR``: build directory holding ``bin/FreeCAD`` (default: the parent of the ``FreeCADCmd`` in use).
"""

import json
import os
import shutil
import subprocess
import sys

ENTRY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gui_entry.py")


class RenderUnavailable(Exception):
    """No GUI build, display or runner: the caller reports ``unavailable``."""


class RenderFailed(Exception):
    def __init__(self, message, **details):
        super().__init__(message)
        self.details = details


def _python():
    """A plain Python for the runner: sys.executable, unless this process is FreeCADCmd or FreeCAD itself."""
    if os.path.basename(sys.executable).lower().startswith("python"):
        return sys.executable
    return shutil.which("python3") or "/usr/bin/python3"


def _build_dir(freecadcmd):
    env = os.environ.get("CADFORK_GUI_BUILD_DIR")
    if env:
        return env
    if freecadcmd:
        return os.path.dirname(os.path.dirname(os.path.abspath(freecadcmd)))  # <build>/bin/FreeCADCmd
    return None


def availability(freecadcmd=None):
    """``(runner, build_dir)`` when a GUI render can run here, else raises ``RenderUnavailable`` with the reason."""
    if os.environ.get("CADFORK_RENDER", "").strip() == "0":
        raise RenderUnavailable("rendering is disabled (CADFORK_RENDER=0)")
    if sys.platform != "darwin":
        raise RenderUnavailable("GUI rendering is implemented for the Mac mini's Cocoa display only "
                                f"(platform {sys.platform})")
    runner = os.environ.get("CADFORK_GUI_RUNNER")
    if not runner:
        raise RenderUnavailable("the GUI harness runner is not configured (set CADFORK_GUI_RUNNER to the workshop's "
                                "harness/gui/run_gui.py)")
    if not os.path.isfile(runner):
        raise RenderUnavailable(f"GUI harness runner not found at {runner} (set CADFORK_GUI_RUNNER)")
    bd = _build_dir(freecadcmd)
    if not bd or not os.path.isfile(os.path.join(bd, "bin", "FreeCAD")):
        raise RenderUnavailable(f"no GUI build: {os.path.join(bd or '<build>', 'bin', 'FreeCAD')} is missing")
    return runner, bd


def warnings_for(info, thickness_map):
    """Warnings for a render whose document has nothing to colour (``info`` is the GUI process's outcome)."""
    out = []
    if info.get("shape_objects") == 0:
        out.append("no shapes: the document is empty, the image is blank")
    elif thickness_map and not (info.get("thickness_map") or {}).get("faces_total"):
        out.append("no solids: thickness map empty")
    return out


def render_file(fcstd, out_png, view="iso", width=480, height=360, thickness_map=None, timeout=240.0,
                freecadcmd=None, background="White"):
    """Render a document file (.FCStd, .step/.stp, .brep) to ``out_png`` in the real GUI.

    ``thickness_map`` is None or ``{"min_ok": mm, "samples": n}``. Returns ``{"path", "view", "width", "height",
    "seconds", "thickness_map"}``. Raises ``RenderUnavailable`` or ``RenderFailed``."""
    runner, bd = availability(freecadcmd)
    out_png = os.path.abspath(out_png)
    for stale in (out_png, out_png + ".json"):
        if os.path.exists(stale):
            os.unlink(stale)
    spec = {"file": os.path.abspath(fcstd), "out": out_png, "view": view, "width": int(width), "height": int(height),
            "background": background, "thickness_map": thickness_map}
    cmd = [_python(), "-I", runner, "--build-dir", bd, "script", ENTRY, "--arg", "spec=" + json.dumps(spec),
           "--timeout", str(int(timeout))]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 120, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise RenderFailed("GUI render timed out", timeout=timeout)
    info = {}
    if os.path.exists(out_png + ".json"):
        try:
            with open(out_png + ".json", encoding="utf-8") as fh:
                info = json.load(fh)
        except ValueError:
            pass
        os.unlink(out_png + ".json")
    if p.returncode != 0 or not info.get("ok") or not os.path.exists(out_png):
        raise RenderFailed("GUI render failed", returncode=p.returncode, error=info.get("error"),
                           output=(p.stdout + p.stderr)[-1500:])
    res = {"path": out_png, "view": view, "width": int(width), "height": int(height)}
    if info.get("thickness_map"):
        tm = dict(info["thickness_map"])
        tm.pop("objects", None)
        res["thickness_map"] = tm
        res["thickness_faces"] = info["thickness_map"].get("objects")
    warnings = warnings_for(info, thickness_map)
    if warnings:
        res["warnings"] = warnings
    return res
