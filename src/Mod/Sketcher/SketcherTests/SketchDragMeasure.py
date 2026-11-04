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

"""Drag measures of G10 slice 3b: 20 scenarios x 4 step lengths x fresh processes.

Each scenario drags one point along a polyline and back (SketchDragScenarios),
once per step length (0.1, 1, 10, 50 mm), either one move session per call
("python" mode, the SketchObject default) or one session kept alive over the
drag as the sketch editor does ("gui" mode: initTemporaryMove, then one
moveGeometriesTemporary per mouse move, then the release moveGeometries).

Definitions, shared with the run 054 baseline:

* move failure: moveGeometry raised.
* reachable cursor: the target lies within 0.5 mm of a position the dragged
  point held on the outward leg of the frozen 0.1 mm baseline run
  (SketchDragHeld.json.gz).
* lock move: a reachable-cursor move whose gap to the cursor exceeds 1 mm; a
  lock-up is 3 or more consecutive lock moves.
* jump: a successful move whose largest vertex displacement exceeds 5 x the
  distance from the previous dragged-point position to the cursor + 1 mm.
* out-and-back: largest vertex displacement between the start state and the
  state after the cursor returns to the start.
* agree: failures, lock moves, jumps and out-and-back (to 0.1 mm) are the same
  in every process of the cell.

Driver (plain python, spawns FreeCADCmd workers):
    python3 SketchDragMeasure.py drive OUTDIR MODE [S01 S02 ...]
    python3 SketchDragMeasure.py table OUTDIR [BASELINE_CELLS.json]
with MODE gui or python, and env FDM_BIN (FreeCADCmd), FDM_REPS (default 3),
FDM_PAR (default 3), FDM_STEPS (default 0.1,1,10,50).
"""

import concurrent.futures as cf
import gzip
import json
import math
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STEPS = (0.1, 1.0, 10.0, 50.0)


def resample(pts, step):
    out = [pts[0]]
    carry = 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        seg = math.hypot(x1 - x0, y1 - y0)
        d = step - carry
        while d <= seg + 1e-12:
            t = d / seg if seg else 0
            out.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
            d += step
        carry = seg - (d - step)
        if carry >= step - 1e-12:
            carry = 0.0
    if math.hypot(out[-1][0] - pts[-1][0], out[-1][1] - pts[-1][1]) > 1e-9:
        out.append(pts[-1])
    return out


def _scenarios():
    try:
        from SketcherTests import SketchDragScenarios as SC
    except ImportError:
        sys.path.insert(0, HERE)
        import SketchDragScenarios as SC
    return SC.SCENARIOS


def load_held():
    with gzip.open(os.path.join(HERE, "SketchDragHeld.json.gz"), "rt") as f:
        return json.load(f)


# ---------------------------------------------------------------- one cell


def _verts(sk):
    out = []
    for g in range(sk.GeometryCount):
        for p in (1, 2, 3):
            try:
                sk.getPoint(g, p)
                out.append((g, p))
            except Exception:
                pass
    return out


def _pts(sk, verts):
    r = []
    for g, p in verts:
        q = sk.getPoint(g, p)
        r.append((q.x, q.y))
    return r


def _tpts(sk, verts):
    r = []
    for g, p in verts:
        q = sk.getTemporaryPoint(g, p)
        r.append((q.x, q.y))
    return r


def run_cell(name, builder, step, session):
    """Trace one scenario at one step length. session=True drives the sketch the
    way the editor's mouse drag does (initTemporaryMove, one moveGeometriesTemporary
    per mouse move, read from the solver's working copy, then the release, which
    is the Python moveGeometries command on the live session); False is the
    python mode, one moveGeometry call (its own session) per move."""
    import FreeCAD

    V = FreeCAD.Vector
    doc = FreeCAD.newDocument("D_" + name)
    try:
        sk, (geo, pos), wp, spec = builder(doc)
        sk.solve()
        verts = _verts(sk)
        st = sk.getPoint(geo, pos)
        wp = [(st.x, st.y)] + list(wp[1:])
        path = resample(wp + wp[-2::-1], step)
        nout = len(resample(wp, step))
        P0 = _pts(sk, verts)
        prev = P0
        i = verts.index((geo, pos))
        tr = []
        t_all = time.perf_counter()
        if session:
            # the editor sets this from its preference (default true) when a sketch is opened
            sk.setRecalculateInitialSolutionWhileMovingPoint(True)
            sk.initTemporaryMove([(geo, pos)])
        for k, (x, y) in enumerate(path[1:], 1):
            t0 = time.perf_counter()
            if session:
                ok = sk.moveGeometriesTemporary([(geo, pos)], V(x, y, 0), False) == 0
            else:
                try:
                    sk.moveGeometry(geo, pos, V(x, y, 0), False)
                    ok = True
                except Exception:
                    ok = False
            dt = time.perf_counter() - t0
            P = _tpts(sk, verts) if session else _pts(sk, verts)
            gap = math.hypot(P[i][0] - x, P[i][1] - y)
            dmax = max(math.hypot(a[0] - b[0], a[1] - b[1]) for a, b in zip(P, prev))
            dpt = math.hypot(P[i][0] - prev[i][0], P[i][1] - prev[i][1])
            tr.append([k, x, y, P[i][0], P[i][1], int(ok), gap, dmax, dpt, dt])
            prev = P
        hys = max(math.hypot(a[0] - b[0], a[1] - b[1]) for a, b in zip(prev, P0))
        rel = None
        if session:
            # release: the editor sends moveGeometries(...) for the last cursor position;
            # a failure aborts the command and the sketch keeps its drag-start state
            x, y = path[-1]
            t0 = time.perf_counter()
            try:
                sk.moveGeometries([(geo, pos)], V(x, y, 0), False)
                rok = True
            except Exception:
                rok = False
            rdt = time.perf_counter() - t0
            R = _pts(sk, verts)
            rel = {
                "ok": int(rok),
                "gap": math.hypot(R[i][0] - x, R[i][1] - y),
                "dmax_temp": max(math.hypot(a[0] - b[0], a[1] - b[1]) for a, b in zip(R, prev)),
                "hys": max(math.hypot(a[0] - b[0], a[1] - b[1]) for a, b in zip(R, P0)),
                "dt": rdt,
            }
        return {
            "scenario": name,
            "step": step,
            "session": bool(session),
            "dof": sk.DoF,
            "n": len(path) - 1,
            "nout": nout,
            "verts": verts,
            "P0": P0,
            "Pend": prev,
            "hys": hys,
            "rel": rel,
            "trace": tr,
            "total_s": time.perf_counter() - t_all,
        }
    finally:
        FreeCAD.closeDocument(doc.Name)


# ---------------------------------------------------------------- metrics


def _reachable_fn(held_pts):
    pts = held_pts
    cache = {}

    def reach(x, y):
        if not pts:
            return False
        key = (round(x, 6), round(y, 6))
        if key not in cache:
            cache[key] = min(math.hypot(x - a, y - b) for a, b in pts) < 0.5
        return cache[key]

    return reach


def cell_stats(r, held):
    """Summarise one traced cell (one process)."""
    tr = r["trace"]
    if not tr:
        return {"empty": True}
    reach = _reachable_fn(held.get(r["scenario"], []))
    nout = r["nout"]
    fails = sum(1 for t in tr if not t[5])
    gapmax = 0.0
    lockmoves = 0
    run = 0
    maxrun = 0
    nreach = 0
    jumps = 0
    prev = None
    lockups_out = 0
    lockups_ret = 0
    run_start = 0
    times = []

    def close_run(end_k):
        nonlocal lockups_out, lockups_ret
        if run >= 3:
            if run_start < nout:
                lockups_out += 1
            else:
                lockups_ret += 1

    for t in tr:
        k, x, y, gx, gy, ok, gap, dmax, dpt, dt = t
        times.append(dt)
        if reach(x, y):
            nreach += 1
            gapmax = max(gapmax, gap)
            if gap > 1.0:
                lockmoves += 1
                if run == 0:
                    run_start = k
                run += 1
                maxrun = max(maxrun, run)
            else:
                close_run(k)
                run = 0
        else:
            close_run(k)
            run = 0
        if ok and prev is not None:
            dcur = math.hypot(x - prev[0], y - prev[1])
            if dmax > 5 * dcur + 1.0:
                jumps += 1
        if ok or prev is None:
            prev = (gx, gy)
    close_run(len(tr))
    ts = sorted(times)
    return {
        "fails": fails,
        "lockmoves": lockmoves,
        "maxrun": maxrun,
        "lockups_out": lockups_out,
        "lockups_ret": lockups_ret,
        "nreach": nreach,
        "n": len(tr),
        "gapreach": round(gapmax, 3),
        "jumps": jumps,
        "hys": round(r["hys"], 3),
        "rel": r.get("rel") and {k: round(v, 3) for k, v in r["rel"].items()},
        "ms_mean": round(1e3 * sum(times) / len(times), 4),
        "ms_p99": round(1e3 * ts[int(0.99 * (len(ts) - 1))], 4),
    }


def signature(c):
    return (c["fails"], c["lockmoves"], c["jumps"], round(c["hys"], 1))


def aggregate(cells):
    """cells: {"scenario|step": [per-process stats]} -> totals."""
    tot = {"cells": 0, "fails": 0, "lockmoves": 0, "lockups": 0, "jumps": 0,
           "oab_cells": 0, "agree_cells": 0, "gap_cells": 0}
    for reps in cells.values():
        if "empty" in reps[0]:
            continue
        tot["cells"] += 1
        c = reps[0]
        tot["fails"] += c["fails"]
        tot["lockmoves"] += c["lockmoves"]
        tot["lockups"] += c["lockups_out"] + c["lockups_ret"]
        tot["jumps"] += c["jumps"]
        tot["oab_cells"] += 1 if c["hys"] > 1.0 else 0
        tot["gap_cells"] += 1 if c["gapreach"] > 1.0 else 0
        tot["agree_cells"] += 1 if len({signature(x) for x in reps}) == 1 else 0
    return tot


def compare(base, now):
    """Check 6 of the slice 3b brief: returns a list of violations."""
    bad = []
    tb, tn = aggregate(base), aggregate(now)
    if tn["fails"] > tb["fails"]:
        bad.append("total failures %d > baseline %d" % (tn["fails"], tb["fails"]))
    if tn["jumps"] > tb["jumps"]:
        bad.append("jumps %d > baseline %d" % (tn["jumps"], tb["jumps"]))
    if tn["agree_cells"] < tb["agree_cells"]:
        bad.append("agreeing cells %d < baseline %d" % (tn["agree_cells"], tb["agree_cells"]))
    for key, reps in base.items():
        if "empty" in reps[0] or key not in now:
            continue
        b, n = reps[0], now[key][0]
        if b["fails"] == 0 and n["fails"] > 0:
            bad.append("%s: failures 0 -> %d" % (key, n["fails"]))
        if b["hys"] < 0.01 and n["hys"] >= 0.01:
            bad.append("%s: out-and-back %.3f -> %.3f" % (key, b["hys"], n["hys"]))
    return bad


# ---------------------------------------------------------------- worker


def _worker():
    pref = os.environ["FDM_SCEN"]
    session = os.environ.get("FDM_MODE", "gui") == "gui"
    steps = [float(s) for s in os.environ.get("FDM_STEPS", "0.1,1,10,50").split(",")]
    held = load_held()
    name, builder = [(n, f) for n, f in _scenarios() if n.startswith(pref + "_")][0]
    res = []
    for st in steps:
        try:
            r = run_cell(name, builder, st, session)
            c = cell_stats(r, held)
            c["scenario"] = name
            c["step"] = st
            c["dof"] = r["dof"]
            c["nout"] = r["nout"]
            c["total_s"] = r["total_s"]
            if os.environ.get("FDM_TRACE"):
                c["trace"] = r["trace"]
            res.append(c)
        except Exception:
            import traceback

            res.append({"scenario": name, "step": st, "error": traceback.format_exc()})
    with open(os.environ["FDM_OUT"], "w") as f:
        json.dump(res, f)


# ---------------------------------------------------------------- driver


def drive(outdir, mode, scens):
    binpath = os.environ.get("FDM_BIN")
    if not binpath:
        sys.exit("set FDM_BIN to the FreeCADCmd binary")
    outdir = os.path.abspath(outdir)
    os.makedirs(outdir, exist_ok=True)
    reps = int(os.environ.get("FDM_REPS", "3"))
    steps = os.environ.get("FDM_STEPS", "0.1,1,10,50")
    if not scens:
        scens = ["S%02d" % i for i in range(1, 21)]

    def one(a):
        s, r = a
        out = "%s/%s_rep%d.json" % (outdir, s, r)
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", FDM_WORKER="1", FDM_SCEN=s,
                   FDM_OUT=out, FDM_MODE=mode, FDM_STEPS=steps)
        p = subprocess.run(["nice", "-n", "10", binpath, os.path.join(HERE, "SketchDragMeasure.py")],
                           env=env, capture_output=True, text=True,
                           timeout=int(os.environ.get("FDM_TMO", "900")))
        with open("%s/%s_rep%d.log" % (outdir, s, r), "w") as f:
            f.write(p.stdout[-2000:] + p.stderr[-2000:])
        return s, r, os.path.exists(out)

    jobs = [(s, r) for s in scens for r in range(1, reps + 1)]
    with cf.ThreadPoolExecutor(int(os.environ.get("FDM_PAR", "3"))) as ex:
        missing = [j for j in ex.map(one, jobs) if not j[2]]
    print("missing:", missing)


def load_cells(outdir):
    import glob

    cells = {}
    for f in sorted(glob.glob(outdir.rstrip("/") + "/S*_rep*.json")):
        for c in json.load(open(f)):
            key = "%s|%g" % (c["scenario"], c["step"])
            cells.setdefault(key, []).append(c)
    return cells


def _fmt(vals):
    s = [str(v) for v in vals]
    return s[0] if len(set(s)) == 1 else "/".join(s)


def table(outdir, base_path=None):
    cells = load_cells(outdir)
    json.dump(cells, open(outdir.rstrip("/") + "/cells.json", "w"))
    base = json.load(open(base_path)) if base_path else None
    head = "| scenario | step | fails | lock moves (max run) / reachable | lock-ups out/ret | worst reach gap | jumps | out-and-back | ms mean | agree |"
    L = [head, "|---|---:|---:|---|---|---:|---:|---:|---:|---|"]
    for key, reps in cells.items():
        sc, st = key.split("|")
        if "empty" in reps[0]:
            L.append("| %s | %s | no moves | | | | | | | |" % (sc, st))
            continue
        if "error" in reps[0]:
            L.append("| %s | %s | ERROR | | | | | | | |" % (sc, st))
            continue
        agree = "yes" if len({signature(c) for c in reps}) == 1 else "NO"
        L.append("| %s | %s | %s | %s (%s) / %d | %s/%s | %s | %s | %s | %s | %s |" % (
            sc, st, _fmt([c["fails"] for c in reps]), _fmt([c["lockmoves"] for c in reps]),
            _fmt([c["maxrun"] for c in reps]), reps[0]["nreach"],
            _fmt([c["lockups_out"] for c in reps]), _fmt([c["lockups_ret"] for c in reps]),
            _fmt([c["gapreach"] for c in reps]), _fmt([c["jumps"] for c in reps]),
            _fmt([c["hys"] for c in reps]), reps[0]["ms_mean"], agree))
    t = aggregate(cells)
    L.append("")
    L.append("Totals: %s" % json.dumps(t))
    if base:
        L.append("Against baseline: %s" % (compare(base, cells) or "no violation of check 6"))
    open(outdir.rstrip("/") + "/table.md", "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if os.environ.get("FDM_WORKER"):
    _worker()
elif __name__ == "__main__" and len(sys.argv) > 2:
    if sys.argv[1] == "drive":
        drive(sys.argv[2], sys.argv[3], sys.argv[4:])
    elif sys.argv[1] == "table":
        table(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
