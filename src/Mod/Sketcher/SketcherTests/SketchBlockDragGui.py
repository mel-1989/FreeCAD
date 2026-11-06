# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *   Copyright (c) 2026 The FreeCAD project association AISBL              *
# *                                                                         *
# *   This file is part of FreeCAD.                                         *
# *                                                                         *
# *   FreeCAD is free software: you can redistribute it and/or modify it    *
# *   under the terms of the GNU Lesser General Public License as           *
# *   published by the Free Software Foundation, either version 2.1 of the  *
# *   License, or (at your option) any later version.                       *
# *                                                                         *
# *   FreeCAD is distributed in the hope that it will be useful, but        *
# *   WITHOUT ANY WARRANTY; without even the implied warranty of            *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU     *
# *   Lesser General Public License for more details.                      *
# *                                                                         *
# *   You should have received a copy of the GNU Lesser General Public      *
# *   License along with FreeCAD. If not, see                               *
# *   <https://www.gnu.org/licenses/>.                                      *
# *                                                                         *
# ***************************************************************************

"""Real-editor drag of a 200-member block (G10 slice 5b3, check 8).  Runs inside the GUI:

    python3 -I <cad-fork>/harness/gui/run_gui.py script SketchBlockDragGui.py --timeout 900 --out <dir>

It opens a sketch of one 200-line block instance in the editor, presses on a line end with real Qt mouse events on the
3D view, moves the pointer in small steps and times each step from the event to the end of the event-loop turns that
follow it (so the redraw is included).  The same is done for 200 plain lines of one chain (stock sketcher, no block).
No bar.  The numbers are written to ``block_drag_gui.json`` in the output folder and printed as ``GUIDRAG`` lines."""

import json
import os
import random
import statistics
import sys
import time

import FreeCAD
import FreeCADGui as Gui
import Part
import Sketcher
from PySide6 import QtCore, QtGui, QtWidgets

_here = os.path.dirname(os.path.abspath(__file__))
for _p in (_here, os.path.dirname(_here)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import SketcherBlocks as B
from SketcherTests import SketchBlockDragCorpus as D
import SketcherBlocksPanel as P

V = FreeCAD.Vector


def pump(n=2):
    for _ in range(n):
        QtWidgets.QApplication.processEvents()


def send(gl, typ, pos, button, buttons=QtCore.Qt.NoButton):
    gp = QtCore.QPointF(gl.mapToGlobal(pos.toPoint()))
    QtWidgets.QApplication.sendEvent(gl, QtGui.QMouseEvent(typ, pos, pos, gp, button, buttons, QtCore.Qt.NoModifier))


def pixel_of(sk, x, y, gl):
    view = Gui.getDocument(sk.Document.Name).ActiveView
    px, py = view.getPointOnViewport(sk.getGlobalPlacement().multVec(V(x, y, 0)))
    h = view.getSize()[1]
    return QtCore.QPointF(px / gl.devicePixelRatioF(), (h - 1 - py) / gl.devicePixelRatioF())


def measure(label, sk, geo, pos, steps=40):
    gdoc = Gui.getDocument(sk.Document.Name)
    Gui.ActiveDocument = gdoc
    gdoc.setEdit(sk.Name)
    pump(30)
    view = gdoc.ActiveView
    view.viewTop()
    view.fitAll()
    pump(30)
    gl = P.find_viewport()
    pt = sk.getPoint(geo, pos)
    start = pixel_of(sk, pt.x, pt.y, gl)
    send(gl, QtCore.QEvent.MouseMove, start + QtCore.QPointF(40, 40), QtCore.Qt.NoButton)
    pump(10)
    send(gl, QtCore.QEvent.MouseMove, start, QtCore.Qt.NoButton)
    pump(10)
    send(gl, QtCore.QEvent.MouseButtonPress, start, QtCore.Qt.LeftButton, QtCore.Qt.LeftButton)
    pump(5)
    before = [sk.getPoint(geo, pos).x, sk.getPoint(geo, pos).y]
    times = []
    for k in range(1, steps + 1):
        p = start + QtCore.QPointF(0.8 * k, 0.5 * k)
        t = time.perf_counter()
        send(gl, QtCore.QEvent.MouseMove, p, QtCore.Qt.NoButton, QtCore.Qt.LeftButton)
        pump(3)
        times.append((time.perf_counter() - t) * 1000.0)
    send(gl, QtCore.QEvent.MouseButtonRelease, p, QtCore.Qt.LeftButton, QtCore.Qt.NoButton)
    pump(20)
    after = sk.getPoint(geo, pos)
    moved = ((after.x - before[0]) ** 2 + (after.y - before[1]) ** 2) ** 0.5
    gdoc.resetEdit()
    pump(20)
    row = {"label": label, "steps": steps, "mean_ms": statistics.mean(times), "median_ms": statistics.median(times), "worst_ms": max(times), "first_ms": times[0], "point_moved_mm": moved}
    print("GUIDRAG " + json.dumps(row), flush=True)
    return row


params = json.loads(open(os.environ["GUI_RUN_PARAMS"]).read()) if os.environ.get("GUI_RUN_PARAMS") else {}
out = []
doc = FreeCAD.newDocument("GuiDrag")
rng = random.Random(5)
spec = D.spec_of("g", 200, "l", 8)
sk, names = D.C.build(doc, spec, [(0.0, 0.0, 0.0)], B)
sk.solve()
ids = B.blocks(sk)["instances"][names[0]]["geo_ids"]
out.append(measure("block200 fast path", sk, ids[100], 1))
import Sketcher as _S
_S.qpEqForceDense(True)
out.append(measure("block200 dense path (fork/main behaviour), 8 steps", sk, ids[100], 1, steps=8))
_S.qpEqForceDense(False)
doc2 = FreeCAD.newDocument("GuiStock")
chain = doc2.addObject("Sketcher::SketchObject", "Chain")
pts = [(i * 1.0, 0.5 * ((-1) ** i)) for i in range(201)]
chain.addGeometry([Part.LineSegment(V(pts[i][0], pts[i][1], 0), V(pts[i + 1][0], pts[i + 1][1], 0)) for i in range(200)], False)
for i in range(199):
    chain.addConstraint(Sketcher.Constraint("Coincident", i, 2, i + 1, 1))
chain.solve()
out.append(measure("stock: chain of 200 lines, no block", chain, 100, 1))
dest = os.environ.get("GUI_RUN_OUT") or params.get("out") or "."
with open(os.path.join(dest, "block_drag_gui.json"), "w") as f:
    json.dump(out, f, indent=1)
