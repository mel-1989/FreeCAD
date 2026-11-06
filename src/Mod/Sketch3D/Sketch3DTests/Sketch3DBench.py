# SPDX-License-Identifier: LGPL-2.1-or-later

"""Speed of Sketch3D.solve (G10 slice 6a, check 9): a grounded chain and a comb of lines at
about 300, 1000 and 3000 unknowns, the median of three processes.

    FreeCADCmd Sketch3DBench.py            # parent: runs every case three times, prints the table
    SKETCH3D_BENCH_CASE=chain:1000 FreeCADCmd Sketch3DBench.py   # child: one solve, one JSON line

The start is the exact solution moved by up to 5% of the mean line length per coordinate. The time is the whole call, including parsing the spec."""

import json
import os
import random
import statistics
import subprocess
import sys
import time

CASE = os.environ.get("SKETCH3D_BENCH_CASE")
SIZES = [300, 1000, 3000]
BARS_MS = {1000: 200.0, 3000: 1000.0}


def chain(unknowns, rng):
    n = max(1, unknowns // 3 - 1)  # lines
    pts = [[0.0, 0.0, 0.0]]
    cons = []
    lines = []
    for k in range(n):
        axis = k % 3
        length = 1.0 + (k % 5)
        sign = 1.0 if (k // 3) % 2 == 0 else -1.0
        p = list(pts[-1])
        p[axis] += sign * length
        pts.append(p)
        lines.append([k, k + 1])
        cons.append({"type": ["along_x", "along_y", "along_z"][axis], "operands": [k]})
        cons.append({"type": "length", "operands": [k], "value": length})
    return pts, lines, cons


def comb(unknowns, rng):
    m = max(2, int(round((unknowns / 3.0 + 1) ** 0.5)))
    idx = lambda i, j: j * m + i
    pts = [[float(i), float(j), 0.0] for j in range(m) for i in range(m)]
    lines = []
    cons = []
    for j in range(m - 1):
        lines.append([idx(0, j), idx(0, j + 1)])
        cons.append({"type": "along_y", "operands": [len(lines) - 1]})
        cons.append({"type": "length", "operands": [len(lines) - 1], "value": 1.0})
    for j in range(m):
        for i in range(m - 1):
            lines.append([idx(i, j), idx(i + 1, j)])
            cons.append({"type": "along_x", "operands": [len(lines) - 1]})
            cons.append({"type": "length", "operands": [len(lines) - 1], "value": 1.0})
    return pts, lines, cons


def make_spec(kind, unknowns):
    rng = random.Random(unknowns)
    pts, lines, cons = {"chain": chain, "comb": comb}[kind](unknowns, rng)
    # 5% of a typical line length, per coordinate
    size = sum(sum((a - b) ** 2 for a, b in zip(pts[i], pts[j])) ** 0.5 for i, j in lines) / len(lines)
    moved = [
        {"xyz": [c + (0.0 if i == 0 else rng.uniform(-0.05, 0.05) * size) for c in p], "fixed": i == 0}
        for i, p in enumerate(pts)
    ]
    return {"points": moved, "lines": lines, "constraints": cons}, 3 * (len(pts) - 1)


def child():
    import Sketch3D

    kind, size = CASE.split(":")
    spec, n = make_spec(kind, int(size))
    if os.environ.get("SKETCH3D_BENCH_DIAGNOSE") == "0":
        spec["diagnose"] = False
    if os.environ.get("SKETCH3D_BENCH_ALGORITHM"):
        spec["algorithm"] = os.environ["SKETCH3D_BENCH_ALGORITHM"]
    t = time.perf_counter()
    r = Sketch3D.solve(spec)
    dt = time.perf_counter() - t
    print("S3DBENCH " + json.dumps({"kind": kind, "n": n, "ms": 1000 * dt, "iterations": r["iterations"],
                                    "algorithm": r["algorithm"], "code": r["code"], "dof": r["dof"],
                                    "maxres": max(r["residuals"])}))


def parent():
    exe = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "FreeCADCmd")
    if not os.path.exists(exe):
        import FreeCAD

        exe = os.path.join(FreeCAD.getHomePath(), "bin", "FreeCADCmd")

    def median_of_three(kind, size, diagnose):
        runs = []
        for _ in range(3):
            env = dict(os.environ, SKETCH3D_BENCH_CASE="%s:%d" % (kind, size), SKETCH3D_BENCH_DIAGNOSE="1" if diagnose else "0")
            out = subprocess.run([exe, os.path.abspath(__file__)], env=env, capture_output=True, text=True, timeout=600).stdout
            line = [l for l in out.splitlines() if l.startswith("S3DBENCH ")]
            runs.append(json.loads(line[0][9:]))
        return statistics.median(r["ms"] for r in runs), runs[0]

    rows = []
    for kind in ("chain", "comb"):
        prev = None
        for size in SIZES:
            med, r = median_of_three(kind, size, True)
            med_nd, _ = median_of_three(kind, size, False)
            growth = "" if prev is None else "x%.1f for x%.1f unknowns" % (med / prev[1], r["n"] / prev[0])
            rows.append((kind, r["n"], med, med_nd, r["iterations"], r["algorithm"], r["code"], r["dof"], r["maxres"], growth))
            prev = (r["n"], med)
    print("| case | unknowns | median ms | median ms, diagnose false | iterations | algorithm | code | dof | max residual | growth |")
    print("|---|---:|---:|---:|---:|---|---:|---:|---:|---|")
    for row in rows:
        print("| %s | %d | %.1f | %.1f | %d | %s | %d | %d | %.1e | %s |" % row)


if CASE:
    child()
elif __name__ == "__main__" or os.environ.get("SKETCH3D_BENCH_PARENT"):
    parent()
