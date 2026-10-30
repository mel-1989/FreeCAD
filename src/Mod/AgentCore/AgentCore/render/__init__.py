# SPDX-License-Identifier: LGPL-2.1-or-later
"""Rendering for F2 (thickness map) and F4 (the MCP ``render`` tool).

Headless FreeCADCmd has no view providers, so a picture needs the real GUI. The split:

- ``ramp``: the thickness colour ramp (pure Python, no FreeCAD).
- ``gui``: runs inside the FreeCAD GUI process; colours faces and saves PNGs (``View3DInventor.saveImage``).
- ``gui_entry``: the script the GUI harness runner executes (``harness/gui/run_gui.py script``).
- ``host``: runs outside FreeCAD (the MCP server, tests); launches the runner and returns the PNG path.
"""
