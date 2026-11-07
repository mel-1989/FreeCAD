# SPDX-License-Identifier: LGPL-2.1-or-later

"""Speed of Sketch3D.solve (G10 slice 6a, check 9): a grounded chain and a true grid of lines (closed loops, redundant relations) at
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


def grid(unknowns, rng):
    """An m x m grid of axis lines: every row of points joined by lines along X, every column
    by lines along Y, each with a length of 1. The squares are closed loops, so the length and
    direction relations are redundant (and consistent) and the system is rank deficient."""
    m = max(2, int(round((unknowns / 3.0 + 1) ** 0.5)))
    idx = lambda i, j: j * m + i
    pts = [[float(i), float(j), 0.0] for j in range(m) for i in range(m)]
    lines = []
    cons = []
    for j in range(m):
        for i in range(m - 1):
            lines.append([idx(i, j), idx(i + 1, j)])
            cons.append({"type": "along_x", "operands": [len(lines) - 1]})
            cons.append({"type": "length", "operands": [len(lines) - 1], "value": 1.0})
    for i in range(m):
        for j in range(m - 1):
            lines.append([idx(i, j), idx(i, j + 1)])
            cons.append({"type": "along_y", "operands": [len(lines) - 1]})
            cons.append({"type": "length", "operands": [len(lines) - 1], "value": 1.0})
    return pts, lines, cons


NUMBERINGS = ("given", "reverse", "swap", "random1", "random2", "random3")


def renumber(spec, kind, numbering):
    """The same system with its points numbered another way. "given" is the order the generator
    built (a grid in rows, a chain along its length). "reverse" counts the points backwards.
    "swap" numbers a grid by columns instead of rows (for a chain it is the same as given).
    "randomN" shuffles the points, the lines and the constraints with seed N. The point that is
    fixed stays the same point, and every operand follows its line, so the system is the same.
    Returns (spec, perm) with perm[old point] = new point."""
    if numbering == "given":
        return spec, list(range(len(spec["points"])))
    pts, lines, cons = spec["points"], spec["lines"], spec["constraints"]
    n = len(pts)
    perm = list(range(n))  # perm[old] = new
    lorder = list(range(len(lines)))
    corder = list(range(len(cons)))
    if numbering == "reverse":
        perm = [n - 1 - i for i in range(n)]
    elif numbering == "swap":
        m = int(round(n ** 0.5))
        if m * m == n:
            perm = [(i % m) * m + i // m for i in range(n)]
    elif numbering.startswith("random"):
        seed = int(numbering[6:])
        random.Random(seed).shuffle(perm)
        random.Random(seed + 7).shuffle(lorder)
        random.Random(seed + 9).shuffle(corder)
    else:
        raise ValueError(numbering)
    new_pts = [None] * n
    for old, p in enumerate(pts):
        new_pts[perm[old]] = p
    line_new = {old: new for new, old in enumerate(lorder)}
    new_lines = [[perm[lines[old][0]], perm[lines[old][1]]] for old in lorder]
    new_cons = []
    for old in corder:
        c = dict(cons[old])
        c["operands"] = [line_new[o] for o in c["operands"]]
        new_cons.append(c)
    return {"points": new_pts, "lines": new_lines, "constraints": new_cons}, perm


def make_spec(kind, unknowns, numbering="given"):
    spec, perm, n = make_numbered(kind, unknowns, numbering)
    return spec, n


def make_numbered(kind, unknowns, numbering="given"):
    """(spec, perm, unknowns) where perm[old point] is its new index; see renumber."""
    rng = random.Random(unknowns)
    pts, lines, cons = {"chain": chain, "grid": grid}[kind](unknowns, rng)
    # 5% of a typical line length, per coordinate
    size = sum(sum((a - b) ** 2 for a, b in zip(pts[i], pts[j])) ** 0.5 for i, j in lines) / len(lines)
    moved = [
        {"xyz": [c + (0.0 if i == 0 else rng.uniform(-0.05, 0.05) * size) for c in p], "fixed": i == 0}
        for i, p in enumerate(pts)
    ]
    spec = {"points": moved, "lines": lines, "constraints": cons}
    spec, perm = renumber(spec, kind, numbering)
    return spec, perm, 3 * (len(pts) - 1)


def child():
    import Sketch3D

    parts = CASE.split(":")
    kind, size = parts[0], parts[1]
    spec, n = make_spec(kind, int(size), parts[2] if len(parts) > 2 else "given")
    if os.environ.get("SKETCH3D_BENCH_DIAGNOSE") == "0":
        spec["diagnose"] = False
    if os.environ.get("SKETCH3D_BENCH_ALGORITHM"):
        spec["algorithm"] = os.environ["SKETCH3D_BENCH_ALGORITHM"]
    t = time.perf_counter()
    r = Sketch3D.solve(spec)
    dt = time.perf_counter() - t
    print("S3DBENCH " + json.dumps({"kind": kind, "numbering": parts[2] if len(parts) > 2 else "given", "n": n, "ms": 1000 * dt, "iterations": r["iterations"],
                                    "algorithm": r["algorithm"], "code": r["code"], "dof": r["dof"],
                                    "maxres": max(r["residuals"])}))


def parent():
    exe = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "FreeCADCmd")
    if not os.path.exists(exe):
        import FreeCAD

        exe = os.path.join(FreeCAD.getHomePath(), "bin", "FreeCADCmd")

    def median_of_three(kind, size, diagnose, numbering="given"):
        runs = []
        for _ in range(int(os.environ.get("SKETCH3D_BENCH_RUNS", "3"))):
            env = dict(os.environ, SKETCH3D_BENCH_CASE="%s:%d:%s" % (kind, size, numbering), SKETCH3D_BENCH_DIAGNOSE="1" if diagnose else "0")
            out = subprocess.run([exe, os.path.abspath(__file__)], env=env, capture_output=True, text=True, timeout=600).stdout
            line = [l for l in out.splitlines() if l.startswith("S3DBENCH ")]
            runs.append(json.loads(line[0][9:]))
        return statistics.median(r["ms"] for r in runs), runs[0]

    rows = []
    only = os.environ.get("SKETCH3D_BENCH_NUMBERINGS")
    numberings = only.split(",") if only else NUMBERINGS
    for kind in ("chain", "grid"):
        for numbering in numberings:
            if kind == "chain" and numbering == "swap":
                continue  # the same system as "given"
            prev = None
            for size in SIZES:
                med, r = median_of_three(kind, size, True, numbering)
                if numbering == "given" and not os.environ.get("SKETCH3D_BENCH_SKIP_NODIAG"):
                    med_nd, _ = median_of_three(kind, size, False)
                else:
                    med_nd = float("nan")
                growth = "" if prev is None else "x%.1f for x%.1f unknowns" % (med / prev[1], r["n"] / prev[0])
                bar = BARS_MS.get(size)
                verdict = "no bar" if bar is None else ("PASS (< %d ms)" % bar if med < bar and r["code"] == 0 else "MISS (bar %d ms)" % bar)
                rows.append((kind + "/" + numbering, r["n"], med, med_nd, r["iterations"], r["algorithm"], r["code"], r["dof"], r["maxres"], growth, verdict))
                prev = (r["n"], med)
    print("| case | unknowns | median ms | median ms, diagnose false | iterations | algorithm | code | dof | max residual | growth | bar |")
    print("|---|---:|---:|---:|---:|---|---:|---:|---:|---|---|")
    for row in rows:
        print("| %s | %d | %.1f | %.1f | %d | %s | %d | %d | %.1e | %s | %s |" % row)


if CASE:
    child()
elif __name__ == "__main__" or os.environ.get("SKETCH3D_BENCH_PARENT"):
    parent()
