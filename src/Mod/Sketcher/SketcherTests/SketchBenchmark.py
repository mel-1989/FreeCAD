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

"""Solver benchmark for the Sketcher: solve rate, solve time, DOF, diagnosis
time and drag stability.

Run it with ``FreeCADCmd SketchBenchmark.py [output.json]``; the output path
can also come from the environment variable ``SKETCH_BENCH_OUT``.  Without
either the JSON goes to ``sketch_benchmark.json`` in the current directory.

Per corpus case and per scale step it records: solve success (the solver's
return code), the median of five solves, DOF and the time of
``SketcherDiagnosis.diagnose``.  Scripted drags move chosen points along
paths with ``moveGeometry`` and count flips measured with orientation
invariants: the side of each arc's centre relative to its chord (the sign of
the arc's sweep), the side of each tangency, and the signed area of each
closed loop.  The drag numbers are a baseline for later solver work, not a
pass bar.
"""

import json
import math
import os
import statistics
import sys
import time

import FreeCAD
import Part
import Sketcher

_here = os.path.dirname(os.path.abspath(__file__))
for _p in (_here, os.path.dirname(_here), os.path.join(FreeCAD.getHomePath(), "Mod", "Sketcher")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import SketcherDiagnosis as SD
from SketcherTests import SketchDiagnosisCorpus as Corpus

V = FreeCAD.Vector


def _median_solve(sketch, repeats=5):
    times, code = [], None
    for _ in range(repeats):
        t0 = time.perf_counter()
        code = sketch.solve()
        times.append(time.perf_counter() - t0)
    return code, statistics.median(times)


def bench_case(case):
    sk = case.sketch
    code, med = _median_solve(sk)
    t0 = time.perf_counter()
    result = SD.diagnose(sk)
    diag = time.perf_counter() - t0
    return {
        "name": case.name,
        "class": case.cls,
        "constraints": sk.ConstraintCount,
        "geometry": sk.GeometryCount,
        "solve_code": code,
        "solve_success": code == 0,
        "solve_median_s": med,
        "dof": sk.DoF,
        "diagnosis_s": diag,
        "diagnosis_status": result["status"],
        "conflict_sets": len(result["conflict_sets"]),
        "redundancy_groups": len(result["redundancy_groups"]),
        "diagnosis_complete": result["complete"],
    }


# ---------------------------------------------------------------------------
# Drag scenarios
# ---------------------------------------------------------------------------


def _pt(sk, geo, pos):
    p = sk.getPoint(geo, pos)
    return p.x, p.y


def _cross(ax, ay, bx, by):
    return ax * by - ay * bx


def _sign(x, eps=1e-9):
    return 0 if abs(x) < eps else (1 if x > 0 else -1)


def _arc_sides(sk, arcs):
    out = []
    for a in arcs:
        sx, sy = _pt(sk, a, 1)
        ex, ey = _pt(sk, a, 2)
        cx, cy = _pt(sk, a, 3)
        out.append(_sign(_cross(ex - sx, ey - sy, cx - sx, cy - sy)))
    return out


def _tangent_sides(sk, tangents):
    """For circle-circle tangency: +1 external, -1 internal.  For line-circle:
    the side of the circle centre relative to the line."""
    out = []
    geo = sk.Geometry
    for (a, b) in tangents:
        ga, gb = geo[a], geo[b]
        if hasattr(ga, "Radius") and hasattr(gb, "Radius"):
            ax, ay = _pt(sk, a, 3)
            bx, by = _pt(sk, b, 3)
            d = math.hypot(ax - bx, ay - by)
            ext = abs(d - (ga.Radius + gb.Radius))
            inn = abs(d - abs(ga.Radius - gb.Radius))
            out.append(1 if ext <= inn else -1)
        else:
            line, circ = (a, b) if hasattr(gb, "Radius") else (b, a)
            sx, sy = _pt(sk, line, 1)
            ex, ey = _pt(sk, line, 2)
            cx, cy = _pt(sk, circ, 3)
            out.append(_sign(_cross(ex - sx, ey - sy, cx - sx, cy - sy)))
    return out


def _loop_areas(sk, loops):
    out = []
    for loop in loops:
        pts = [_pt(sk, g, p) for g, p in loop]
        a = 0.0
        for i in range(len(pts)):
            x1, y1 = pts[i]
            x2, y2 = pts[(i + 1) % len(pts)]
            a += x1 * y2 - x2 * y1
        out.append(_sign(a / 2.0, 1e-9))
    return out


def _invariants(sk, spec):
    return {
        "arcs": _arc_sides(sk, spec.get("arcs", [])),
        "tangents": _tangent_sides(sk, spec.get("tangents", [])),
        "loops": _loop_areas(sk, spec.get("loops", [])),
    }


def _count_flips(base, now):
    n = {"arc": 0, "tangent": 0, "loop": 0}
    for key, name in (("arcs", "arc"), ("tangents", "tangent"), ("loops", "loop")):
        for b, c in zip(base[key], now[key]):
            if c != b:
                n[name] += 1
    return n


def _scenario_triangle(doc):
    """Base pinned at the origin, one side of length 3, the third side free:
    the apex is free on a circle and is dragged below the base line and back."""
    s = Corpus._Sk(doc, "d_triangle")
    a = s.line(0, 0, 3, 0)
    b = s.line(3, 0, 1.5, 2.6)
    c = s.line(1.5, 2.6, 0, 0)
    s.c("Coincident", a, 2, b, 1)
    s.c("Coincident", b, 2, c, 1)
    s.c("Coincident", c, 2, a, 1)
    s.c("DistanceX", -1, 1, a, 1, 0.0)
    s.c("DistanceY", -1, 1, a, 1, 0.0)
    s.c("Horizontal", a)
    s.c("Distance", a, 3.0)
    s.c("Distance", c, 3.0)
    path = [(3.0 * math.cos(math.radians(60 - 10 * i)), 3.0 * math.sin(math.radians(60 - 10 * i))) for i in range(0, 16)]
    path += [(3.0 * math.cos(math.radians(-90 + 10 * i)), 3.0 * math.sin(math.radians(-90 + 10 * i))) for i in range(0, 16)]
    spec = {"loops": [[(a, 1), (b, 1), (c, 1)]]}
    return s.o, (b, 2), path, spec


def _scenario_fourbar(doc):
    s = Corpus._Sk(doc, "d_fourbar")
    # ground (0,0)-(6,0), crank 2, coupler 5, rocker 4.5
    g = s.line(0, 0, 6, 0)
    cr = s.line(0, 0, 1.0, 1.7)
    cp = s.line(1.0, 1.7, 5.5, 3.5)
    rk = s.line(5.5, 3.5, 6, 0)
    s.c("Coincident", g, 1, cr, 1)
    s.c("Coincident", cr, 2, cp, 1)
    s.c("Coincident", cp, 2, rk, 1)
    s.c("Coincident", rk, 2, g, 2)
    s.c("DistanceX", -1, 1, g, 1, 0.0)
    s.c("DistanceY", -1, 1, g, 1, 0.0)
    s.c("Horizontal", g)
    s.c("Distance", g, 6.0)
    s.c("Distance", cr, 2.0)
    s.c("Distance", cp, 5.0)
    s.c("Distance", rk, 4.5)
    path = [(2.0 * math.cos(math.radians(a)), 2.0 * math.sin(math.radians(a))) for a in range(60, 421, 10)]
    spec = {"loops": [[(g, 1), (g, 2), (cp, 2), (cp, 1)]]}
    return s.o, (cr, 2), path, spec


def _scenario_fourbar_rocker(doc):
    """A four-bar that cannot turn fully (lengths 6, 3, 4, 4): the crank is
    dragged past its dead point."""
    s = Corpus._Sk(doc, "d_fourbar2")
    g = s.line(0, 0, 6, 0)
    cr = s.line(0, 0, 1.5, 2.6)
    cp = s.line(1.5, 2.6, 4.5, 2.9)
    rk = s.line(4.5, 2.9, 6, 0)
    s.c("Coincident", g, 1, cr, 1)
    s.c("Coincident", cr, 2, cp, 1)
    s.c("Coincident", cp, 2, rk, 1)
    s.c("Coincident", rk, 2, g, 2)
    s.c("DistanceX", -1, 1, g, 1, 0.0)
    s.c("DistanceY", -1, 1, g, 1, 0.0)
    s.c("Horizontal", g)
    s.c("Distance", g, 6.0)
    s.c("Distance", cr, 3.0)
    s.c("Distance", cp, 4.0)
    s.c("Distance", rk, 4.0)
    path = [(3.0 * math.cos(math.radians(a)), 3.0 * math.sin(math.radians(a))) for a in range(60, -181, -8)]
    spec = {"loops": [[(g, 1), (g, 2), (cp, 2), (cp, 1)]]}
    return s.o, (cr, 2), path, spec


def _scenario_slot(doc):
    s = Corpus._Sk(doc, "d_slot")
    al = s.arc(0.0, 0.0, 2.0, math.pi / 2, 3 * math.pi / 2)
    ar = s.arc(10.0, 0.0, 2.0, -math.pi / 2, math.pi / 2)
    lt = s.line(0.0, 2.0, 10.0, 2.0)
    lb = s.line(10.0, -2.0, 0.0, -2.0)
    s.c("Tangent", al, 2, lb, 2)
    s.c("Tangent", lb, 1, ar, 1)
    s.c("Tangent", ar, 2, lt, 2)
    s.c("Tangent", lt, 1, al, 1)
    s.c("DistanceX", -1, 1, al, 3, 0.0)
    s.c("DistanceY", -1, 1, al, 3, 0.0)
    s.c("Radius", al, 2.0)
    s.c("Equal", al, ar)
    s.c("Horizontal", al, 3, ar, 3)
    # drag the right centre along x through and past the left centre
    path = [(10.0 - 0.8 * i, 0.0) for i in range(0, 26)]
    spec = {"arcs": [al, ar], "loops": [[(lt, 1), (lt, 2), (lb, 1), (lb, 2)]]}
    return s.o, (ar, 3), path, spec


def _scenario_tangent_chain(doc):
    s = Corpus._Sk(doc, "d_tangent")
    c1 = s.circle(0, 0, 2)
    c2 = s.circle(3, 0, 1)
    c3 = s.circle(4.5, 0, 0.5)
    s.c("DistanceX", -1, 1, c1, 3, 0.0)
    s.c("DistanceY", -1, 1, c1, 3, 0.0)
    s.c("Radius", c1, 2.0)
    s.c("Radius", c2, 1.0)
    s.c("Radius", c3, 0.5)
    s.c("Tangent", c1, c2)
    s.c("Tangent", c2, c3)
    # drag the small circle round the middle one, then through the large one
    path = [
        (3 + 1.5 * math.cos(math.radians(a)), 1.5 * math.sin(math.radians(a))) for a in range(0, 361, 15)
    ]
    path += [(4.5 - 0.5 * i, 0.0) for i in range(0, 14)]
    spec = {"tangents": [(c1, c2), (c2, c3)]}
    return s.o, (c3, 3), path, spec


def _scenario_rectangle(doc):
    s = Corpus._Sk(doc, "d_rect")
    ids, _ = s.rect(0, 0, 10, 5)
    s.c("Horizontal", ids[0])
    s.c("Horizontal", ids[2])
    s.c("Vertical", ids[1])
    s.c("Vertical", ids[3])
    s.c("DistanceX", -1, 1, ids[0], 1, 0.0)
    s.c("DistanceY", -1, 1, ids[0], 1, 0.0)
    # drag the top-right corner across the origin corner and back
    path = [(10.0 - 1.2 * i, 5.0 - 0.6 * i) for i in range(0, 20)]
    path += [(-12.0 + 1.2 * i, -6.0 + 0.6 * i) for i in range(0, 20)]
    spec = {"loops": [[(ids[0], 1), (ids[1], 1), (ids[2], 1), (ids[3], 1)]]}
    return s.o, (ids[1], 2), path, spec


def _scenario_chain(doc):
    """Eight chained rectangles; the last one loses its two dimensions and its
    top-right corner is dragged across the opposite corner."""
    sk, info = Corpus.build_scale(doc, "d_chain", 96)
    n = sk.GeometryCount // 4
    last = (n - 1) * 4
    unit = (n - 1) * 12
    sk.delConstraint(unit + 9)  # DistanceY of the right side
    sk.delConstraint(unit + 8)  # DistanceX of the bottom side
    sk.solve()
    loops = [[(r * 4, 1), (r * 4 + 1, 1), (r * 4 + 2, 1), (r * 4 + 3, 1)] for r in range(n)]
    x0 = 12.0 * (n - 1)
    path = [(x0 + 10.0 - 1.1 * i, 5.0 - 0.6 * i) for i in range(0, 20)]
    return sk, (last + 1, 2), path, {"loops": loops}


SCENARIOS = [
    ("triangle_apex_through_base", _scenario_triangle),
    ("fourbar_crank_turn", _scenario_fourbar),
    ("fourbar_rocker_past_dead_point", _scenario_fourbar_rocker),
    ("slot_centre_through_centre", _scenario_slot),
    ("tangent_chain_small_circle", _scenario_tangent_chain),
    ("rectangle_corner_through_corner", _scenario_rectangle),
    ("chain_of_rectangles_corner", _scenario_chain),
]


def run_drag(name, builder):
    doc = FreeCAD.newDocument("Drag_" + name)
    try:
        sk, (geo, pos), path, spec = builder(doc)
        code = sk.solve()
        dof0 = sk.DoF
        base = _invariants(sk, spec)
        max_gap = 0.0
        times, failures = [], 0
        flips = {"arc": 0, "tangent": 0, "loop": 0}
        flipped_steps = 0
        last = base
        for (x, y) in path:
            t0 = time.perf_counter()
            try:
                rc = sk.moveGeometry(geo, pos, V(x, y, 0), False)
            except Exception:
                rc = -1
            times.append(time.perf_counter() - t0)
            if rc not in (0, None):
                failures += 1
            px, py = _pt(sk, geo, pos)
            max_gap = max(max_gap, math.hypot(px - x, py - y))
            now = _invariants(sk, spec)
            step = _count_flips(last, now)
            if any(step.values()):
                flipped_steps += 1
            for k in flips:
                flips[k] += step[k]
            last = now
        fx, fy = _pt(sk, geo, pos)
        tx, ty = path[-1]
        return {
            "name": name,
            "initial_dof": dof0,
            "initial_solve_code": code,
            "final_point": [fx, fy],
            "final_target": [tx, ty],
            "final_distance_to_target": math.hypot(fx - tx, fy - ty),
            "max_distance_to_target": max_gap,
            "steps": len(path),
            "move_failures": failures,
            "flip_events": flips,
            "steps_with_a_flip": flipped_steps,
            "net_changes_from_start": _count_flips(base, last),
            "move_median_s": statistics.median(times),
            "move_max_s": max(times),
            "move_total_s": sum(times),
        }
    finally:
        FreeCAD.closeDocument(doc.Name)


def main(out_path=None):
    out_path = (
        out_path
        or os.environ.get("SKETCH_BENCH_OUT")
        or (sys.argv[-1] if sys.argv[-1].endswith(".json") else "sketch_benchmark.json")
    )
    previous = FreeCAD.ActiveDocument.Name if FreeCAD.ActiveDocument is not None else None
    doc = FreeCAD.newDocument("SketchBenchmarkCorpus")
    cases, extra = Corpus.build_corpus(doc, scale_sizes=(10, 100, 1000, 5000))
    report = {
        "freecad": FreeCAD.Version()[:3] + [FreeCAD.Version()[3]],
        "cases": [],
        "scale": [],
        "drags": [],
    }
    skip_cases = os.environ.get("SKETCH_BENCH_ONLY") == "drags"
    for case in ([] if skip_cases else cases):
        row = bench_case(case)
        (report["scale"] if case.cls in ("scale", "scale_planted") else report["cases"]).append(row)
        print(
            "%-10s %-34s n=%5d solve=%s %.4fs dof=%s diag=%.3fs %s"
            % (
                row["class"],
                row["name"],
                row["constraints"],
                row["solve_code"],
                row["solve_median_s"],
                row["dof"],
                row["diagnosis_s"],
                row["diagnosis_status"],
            )
        )
    for name, builder in SCENARIOS:
        row = run_drag(name, builder)
        report["drags"].append(row)
        print("drag %-34s %s" % (name, json.dumps({k: row[k] for k in ("initial_dof", "steps", "move_failures", "flip_events", "max_distance_to_target", "move_median_s")})))
    with open(out_path, "w") as fh:
        json.dump(report, fh, indent=1)
    print("wrote", out_path)
    for d in [doc] + list(extra.values()):
        if d.Name in FreeCAD.listDocuments():
            FreeCAD.closeDocument(d.Name)
    if previous is not None and previous in FreeCAD.listDocuments():
        FreeCAD.setActiveDocument(previous)


# FreeCADCmd runs a script under its file name rather than "__main__".
if __name__ in ("__main__", os.path.splitext(os.path.basename(__file__))[0]):
    if os.environ.get("SKETCH_BENCH_NO_RUN") != "1":
        main()
