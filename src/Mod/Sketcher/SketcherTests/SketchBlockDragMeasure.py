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

"""Drag-speed measurement for the fast path of ``qp_eq`` (G10 slice 5b3, checks 2 and 7).

    FreeCADCmd SketchBlockDragMeasure.py

runs each configuration in three child processes (the median of the three is reported) and prints a table.  Per
configuration: seeded moves of a member through ``moveGeometry`` on one instance (or two tied), the mean, median and
worst move in ms, and the share of ``qp_eq`` calls that took the fast path.

    MEASURE_MOVES=100         moves per configuration (default 100)
    MEASURE_ONLY=a,b          only these configuration names
    MEASURE_DENSE=1           also run the forced dense path, 3 moves each (50, 100, 200 members; 400 takes ~25 s a move)
    MEASURE_REFERENCE=<path>  FreeCADCmd of a fork/main build: time the small-sketch drags on both, alternating
                              five times, and print the ratio (check 7: no more than 5% or 0.05 ms slower)

Output also goes to ``block_drag_measure.json`` in the current directory."""

import json
import os
import statistics
import subprocess
import sys
import time

import FreeCAD
import Sketcher

_here = os.path.dirname(os.path.abspath(__file__))
for _p in (_here, os.path.dirname(_here)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from SketcherTests import SketchBlockDragCorpus as D

CONFIGS = [
    ("lines50", lambda: D.Scenario("lines50", D.spec_of("m", 50, "l", 41), seed=1)),
    ("lines100", lambda: D.Scenario("lines100", D.spec_of("m", 100, "l", 42), seed=1)),
    ("lines200", lambda: D.Scenario("lines200", D.spec_of("m", 200, "l", 43), seed=1)),
    ("lines400", lambda: D.Scenario("lines400", D.spec_of("m", 400, "l", 44), seed=1)),
    ("arcs200", lambda: D.Scenario("arcs200", D.spec_of("m", 200, "a", 45), seed=1, arc_ends=False)),
    ("curves100", lambda: D.Scenario("curves100", None, seed=1, curves=100)),
    ("tied2x100", lambda: D.Scenario("tied2x100", D.spec_of("m", 100, "l", 46), instances=2, outside=1, seed=1)),
]


def child(name, moves, dense):
    import random

    doc = FreeCAD.newDocument("M", "M", True, True)
    sc = dict(CONFIGS)[name]()
    sk, names, kp = D.build(doc, sc)
    rng = random.Random(77)
    Sketcher.qpEqForceDense(dense)
    Sketcher.qpEqReset()
    times = []
    for gid, pos, target in D.move_stream(sk, names, kp, rng, moves, sc.arc_ends):
        t = time.perf_counter()
        D.drag(sk, gid, pos, target)
        times.append((time.perf_counter() - t) * 1000.0)
    st = Sketcher.qpEqStats()
    print("CHILD " + json.dumps({"name": name, "dense": dense, "mean": statistics.mean(times), "median": statistics.median(times),
                                 "worst": max(times), "n": len(times), "fast": st["fast"], "fallback": st["fallback"], "dense_calls": st["dense"]}), flush=True)


def small_child():
    doc = FreeCAD.newDocument("S", "S", True, True)
    t = time.perf_counter()
    D.small_digests(doc, count=100)
    print("SMALL %.6f" % (time.perf_counter() - t), flush=True)


def run(binary, env_extra):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", MEASURE_DENSE="0", MEASURE_CHILD="")
    env.update(env_extra)
    out = subprocess.run([binary, os.path.abspath(__file__)], env=env, capture_output=True, text=True).stdout
    return [l for l in out.splitlines() if l.startswith(("CHILD ", "SMALL "))]


def parent():
    moves = os.environ.get("MEASURE_MOVES", "100")
    only = os.environ.get("MEASURE_ONLY")
    binary = os.path.join(FreeCAD.getHomePath(), "bin", "FreeCADCmd")
    rows = []
    for name, _ in CONFIGS:
        if only and name not in only.split(","):
            continue
        runs = [json.loads(l[6:]) for _ in range(3) for l in run(binary, {"MEASURE_CHILD": name, "MEASURE_MOVES": moves})]
        row = {k: statistics.median(r[k] for r in runs) for k in ("mean", "median", "worst")}
        row.update(name=name, moves=int(moves), fast_share=sum(r["fast"] for r in runs) / max(1, sum(r["fast"] + r["fallback"] + r["dense_calls"] for r in runs)),
                   worst_each_process=[round(r["worst"], 1) for r in runs])
        rows.append(row)
        print("%-10s moves %s  mean %7.1f ms  median %7.1f  worst %7.1f  (worst per process %s)  fast share %.3f" % (
            name, moves, row["mean"], row["median"], row["worst"], row["worst_each_process"], row["fast_share"]), flush=True)
    by = {r["name"]: r for r in rows}
    if "lines200" in by and "lines400" in by:
        print("lines 200 -> 400: mean grows %.2f times (bar: under 3)" % (by["lines400"]["mean"] / by["lines200"]["mean"]))
    if os.environ.get("MEASURE_DENSE") == "1":
        for name in ("lines50", "lines100", "lines200"):
            r = [json.loads(l[6:]) for l in run(binary, {"MEASURE_CHILD": name, "MEASURE_MOVES": "3", "MEASURE_DENSE": "1"})][0]
            rows.append(dict(r, name=name + "_dense"))
            print("%-14s dense, 3 moves: mean %.1f ms" % (name, r["mean"]), flush=True)
        d = {r["name"]: r for r in rows}
        if "lines200_dense" in d and "lines100_dense" in d:
            print("dense 100 -> 200 grows %.1f times" % (d["lines200_dense"]["mean"] / d["lines100_dense"]["mean"]))
    ref = os.environ.get("MEASURE_REFERENCE")
    if ref:
        ours, theirs = [], []
        for _ in range(5):
            for binary_, store in ((binary, ours), (ref, theirs)):
                store.append(float(run(binary_, {"MEASURE_CHILD": "small"})[0].split()[1]))
        a, b = statistics.median(ours), statistics.median(theirs)
        per = (a - b) / (100 * 8) * 1000.0
        print("small sketches, 100 sketches x 8 drags: this build %.3f s, reference %.3f s, difference %+.1f%% (%+.4f ms a drag)" % (a, b, 100.0 * (a - b) / b, per))
        rows.append({"name": "small", "this": a, "reference": b})
    with open("block_drag_measure.json", "w") as f:
        json.dump(rows, f, indent=1)


target = os.environ.get("MEASURE_CHILD") or None
if target == "small":
    small_child()
elif target:
    child(target, int(os.environ.get("MEASURE_MOVES", "100")), os.environ.get("MEASURE_DENSE") == "1")
else:
    parent()
