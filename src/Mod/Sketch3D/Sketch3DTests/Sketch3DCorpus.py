# SPDX-License-Identifier: LGPL-2.1-or-later

"""Seeded corpora and numeric helpers for the Sketch3D tests (G10 slice 6a).

Everything here is built from Python data and numpy, independent of the C++ gradients: the
Jacobians the tests compare against come from central differences of ``Sketch3D._test_rows``
errors, and the generators construct systems whose known solution satisfies every constraint
exactly. Every generator takes a seed and the tests print it on failure."""

import copy
import math
import random

import numpy as np

import Sketch3D


# --------------------------------------------------------------------------------------------
# spec builder
# --------------------------------------------------------------------------------------------


class Builder:
    """Collects points, lines, planes and constraints. ``truth`` holds the known solution
    (when there is one); ``spec()`` gives the plain-data spec started from ``start``."""

    def __init__(self):
        self.truth = []
        self.start = None
        self.fixed = []
        self.lines = []
        self.planes = []
        self.cons = []

    def pt(self, xyz, fixed=False):
        self.truth.append([float(c) for c in xyz])
        self.fixed.append(bool(fixed))
        return len(self.truth) - 1

    def line(self, i, j):
        self.lines.append([i, j])
        return len(self.lines) - 1

    def plane(self, point, normal):
        self.planes.append({"point": list(point), "normal": list(normal)})
        return len(self.planes) - 1

    def con(self, kind, *operands, value=None, tag=None, driving=True):
        c = {"type": kind, "operands": list(operands), "driving": driving}
        if value is not None:
            c["value"] = float(value)
        c["tag"] = len(self.cons) + 1 if tag is None else tag
        self.cons.append(c)
        return len(self.cons) - 1

    def spec(self, coords=None, skip=()):
        pts = coords if coords is not None else (self.start if self.start is not None else self.truth)
        return {
            "points": [{"xyz": list(p), "fixed": f} for p, f in zip(pts, self.fixed)],
            "lines": copy.deepcopy(self.lines),
            "planes": copy.deepcopy(self.planes),
            "constraints": [copy.deepcopy(c) for k, c in enumerate(self.cons) if k not in skip],
        }

    def unknown_indices(self):
        return [3 * i + a for i, f in enumerate(self.fixed) if not f for a in range(3)]


def with_coords(spec, flat):
    """A copy of spec with the coordinates replaced by the flat list (3 per point)."""
    out = copy.deepcopy(spec)
    for i, p in enumerate(out["points"]):
        xyz = [flat[3 * i], flat[3 * i + 1], flat[3 * i + 2]]
        if isinstance(p, dict):
            p["xyz"] = xyz
        else:
            out["points"][i] = xyz
    return out


def flat_coords(spec):
    return [c for p in spec["points"] for c in p["xyz"]]


# --------------------------------------------------------------------------------------------
# numeric Jacobians
# --------------------------------------------------------------------------------------------


def errors(spec):
    return np.array([r["error"] for r in Sketch3D._test_rows(spec)])


def numeric_jacobian(spec, columns, h=1e-6):
    """Central differences of the row errors with respect to the given coordinate indices."""
    base = flat_coords(spec)
    cols = []
    for k in columns:
        up = list(base)
        dn = list(base)
        up[k] += h
        dn[k] -= h
        cols.append((errors(with_coords(spec, up)) - errors(with_coords(spec, dn))) / (2 * h))
    if not cols:
        return np.zeros((len(errors(spec)), 0))
    return np.array(cols).T


def analytic_jacobian(spec, columns):
    rows = Sketch3D._test_rows(spec)
    J = np.zeros((len(rows), len(columns)))
    pos = {c: j for j, c in enumerate(columns)}
    for i, r in enumerate(rows):
        for k, g in r["grad"].items():
            if k in pos:
                J[i, pos[k]] = g
    return J


def rank_of(J, rel=1e-7):
    if J.size == 0:
        return 0
    s = np.linalg.svd(J, compute_uv=False)
    if s.size == 0 or s[0] == 0:
        return 0
    return int(np.sum(s > rel * max(s[0], 1.0)))


def unknown_columns(spec):
    return [3 * i + a for i, p in enumerate(spec["points"]) if not p["fixed"] for a in range(3)]


# --------------------------------------------------------------------------------------------
# geometry helpers (numpy, independent of the C++ code)
# --------------------------------------------------------------------------------------------


def unit(v):
    v = np.asarray(v, float)
    return v / np.linalg.norm(v)


def random_unit(rng):
    while True:
        v = np.array([rng.gauss(0, 1) for _ in range(3)])
        if np.linalg.norm(v) > 1e-3:
            return unit(v)


def perp_to(rng, v):
    v = unit(v)
    while True:
        w = random_unit(rng)
        w = w - np.dot(w, v) * v
        if np.linalg.norm(w) > 0.2:
            return unit(w)


def dist_line(p, a, b):
    d = b - a
    return float(np.linalg.norm(np.cross(p - a, d)) / np.linalg.norm(d))


# --------------------------------------------------------------------------------------------
# random systems with a known solution
# --------------------------------------------------------------------------------------------

OPS = [
    "free_line", "line_between", "dist_pp", "length", "on_line", "on_plane", "plane_dist",
    "pl_dist", "midpoint", "coincident", "along", "parallel", "perp", "par_plane",
    "normal_plane", "angle", "equal_len",
]
PLANAR_OPS = [
    "free_line", "line_between", "dist_pp", "length", "on_line", "pl_dist", "midpoint",
    "coincident", "along", "parallel", "perp", "equal_len",
]


def grow(seed, n_ops, box=10.0, ops=None, planar=False, max_points=10**9):
    """A consistent system built by construction: every constraint holds at ``truth`` to
    rounding. No start is set. The seed fully determines the system."""
    rng = random.Random(seed)
    b = Builder()

    def P():
        return [rng.uniform(-box, box) for _ in range(2)] + ([0.0] if planar else [rng.uniform(-box, box)])

    def ru():
        v = random_unit(rng)
        if planar:
            v = unit([v[0], v[1], 0.0]) if abs(v[0]) + abs(v[1]) > 1e-3 else np.array([1.0, 0.0, 0.0])
        return v

    def perp(v):
        return unit(np.cross([0.0, 0.0, 1.0], v)) if planar else perp_to(rng, v)

    for _ in range(3):
        b.pt(P())
    b.line(0, 1)
    b.plane(P(), list(random_unit(rng)))
    ops = ops or (PLANAR_OPS if planar else OPS)
    T = lambda i: np.array(b.truth[i])
    for _ in range(n_ops):
        if len(b.truth) >= max_points:
            break
        op = rng.choice(ops)
        nl = len(b.lines)
        npt = len(b.truth)
        if op == "free_line":
            i, j = b.pt(P()), b.pt(P())
            b.line(i, j)
        elif op == "line_between":
            i, j = rng.sample(range(npt), 2)
            b.line(i, j)
        elif op == "dist_pp":
            i, j = rng.sample(range(npt), 2)
            b.con("distance_pp", i, j, value=np.linalg.norm(T(i) - T(j)))
        elif op == "length":
            li = rng.randrange(nl)
            a, c = b.lines[li]
            b.con("length", li, value=np.linalg.norm(T(a) - T(c)))
        elif op == "on_line":
            li = rng.randrange(nl)
            a, c = b.lines[li]
            t = rng.uniform(-0.5, 1.5)
            q = b.pt(T(a) + t * (T(c) - T(a)))
            b.con("point_on_line", q, li)
        elif op == "on_plane":
            pl = b.planes[rng.randrange(len(b.planes))]
            n = unit(pl["normal"])
            x = np.array(P())
            x = x - np.dot(x - np.array(pl["point"]), n) * n
            q = b.pt(x)
            b.con("point_on_plane", q, b.planes.index(pl))
        elif op == "plane_dist":
            k = rng.randrange(len(b.planes))
            pl = b.planes[k]
            n = unit(pl["normal"])
            i = rng.randrange(npt)
            b.con("distance_pplane", i, k, value=abs(np.dot(T(i) - np.array(pl["point"]), n)))
        elif op == "pl_dist":
            li = rng.randrange(nl)
            a, c = b.lines[li]
            i = rng.randrange(npt)
            if i in (a, c) or np.linalg.norm(T(a) - T(c)) < 1e-3:
                continue
            b.con("distance_pl", i, li, value=dist_line(T(i), T(a), T(c)))
        elif op == "midpoint":
            li = rng.randrange(nl)
            a, c = b.lines[li]
            q = b.pt(0.5 * (T(a) + T(c)))
            b.con("midpoint", q, li)
        elif op == "coincident":
            i = rng.randrange(npt)
            q = b.pt(T(i))
            b.con("coincident", q, i)
        elif op == "along":
            k = rng.randrange(2 if planar else 3)
            e = np.zeros(3)
            e[k] = 1.0
            a = rng.randrange(npt)
            q = b.pt(T(a) + rng.uniform(-box, box) * e)
            li = b.line(a, q)
            b.con(["along_x", "along_y", "along_z"][k], li)
        elif op in ("parallel", "perp", "angle", "equal_len", "par_plane", "normal_plane"):
            li = rng.randrange(nl)
            a, c = b.lines[li]
            v = T(c) - T(a)
            if np.linalg.norm(v) < 1e-3:
                continue
            s = rng.uniform(0.4, 2.0) * rng.choice([1, -1])
            cpt = P()
            if op == "parallel":
                w = s * v
            elif op == "perp":
                w = perp(v) * np.linalg.norm(v) * abs(s)
            elif op == "angle":
                th = rng.uniform(0.2, math.pi - 0.2)
                w = (math.cos(th) * unit(v) + math.sin(th) * perp(v)) * np.linalg.norm(v) * abs(s)
            elif op == "equal_len":
                w = ru() * np.linalg.norm(v)
            else:
                k = rng.randrange(len(b.planes))
                n = unit(b.planes[k]["normal"])
                w = (perp_to(rng, n) * rng.uniform(1, 5)) if op == "par_plane" else n * s * 3
            q0 = b.pt(cpt)
            q1 = b.pt(np.array(cpt) + w)
            nli = b.line(q0, q1)
            if op == "parallel":
                b.con("parallel", li, nli)
            elif op == "perp":
                b.con("perpendicular", li, nli)
            elif op == "angle":
                cosv = np.dot(v, w) / (np.linalg.norm(v) * np.linalg.norm(w))
                b.con("angle", li, nli, value=math.acos(max(-1.0, min(1.0, cosv))))
            elif op == "equal_len":
                b.con("equal_length", li, nli)
            elif op == "par_plane":
                b.con("parallel_plane", nli, k)
            else:
                b.con("normal_plane", nli, k)
    return b


def fix_some(b, rng, count=1):
    """Ground a few points by flag."""
    for i in rng.sample(range(len(b.truth)), min(count, len(b.truth))):
        b.fixed[i] = True


def complete(b, rng, max_add=400):
    """Add fixed-coordinate constraints at the truth until the system is rigid (rank of the
    analytic Jacobian equals the number of unknowns). Returns True when it got there."""
    b.start = None
    for _ in range(max_add):
        spec = b.spec()
        cols = unknown_columns(spec)
        if not cols:
            return True
        J = analytic_jacobian(spec, cols)
        if rank_of(J) >= len(cols):
            return True
        # the coordinate with the largest share of the null space
        _, s, vt = np.linalg.svd(J, full_matrices=True)
        r = rank_of(J)
        null = vt[r:]
        share = np.sum(null ** 2, axis=0)
        nullity = len(cols) - r
        # several at once while the null space is large; the next pass re-checks the rank
        for k in np.argsort(-share)[: max(1, nullity // 3)]:
            coord = cols[int(k)]
            b.con("fixed_coordinate", coord // 3, coord % 3, value=b.truth[coord // 3][coord % 3])
    return False


def complete_by_grounding(b, max_add=400):
    """Ground points by flag (most under-determined first) until the analytic Jacobian is of
    full column rank. Used for planar systems, where fixed_coordinate would not survive a
    rotation of the plane."""
    for _ in range(max_add):
        spec = b.spec(b.truth)
        cols = unknown_columns(spec)
        if not cols:
            return True
        J = analytic_jacobian(spec, cols)
        r = rank_of(J)
        if r >= len(cols):
            return True
        _, _, vt = np.linalg.svd(J, full_matrices=True)
        share = np.sum(vt[r:] ** 2, axis=0)
        per_point = {}
        for k, c in enumerate(cols):
            per_point[c // 3] = per_point.get(c // 3, 0.0) + share[k]
        b.fixed[max(per_point, key=per_point.get)] = True
    return False


def perturb(b, rng, frac, planar=False):
    """Start = truth moved by up to frac of the system's size, per coordinate. The size is the
    RMS distance of the points from their centroid. Fixed points stay where they are."""
    pts = np.array(b.truth)
    size = max(1e-9, float(np.sqrt(np.mean(np.sum((pts - pts.mean(axis=0)) ** 2, axis=1)))))
    b.start = [
        [c + (0.0 if (b.fixed[i] or (planar and a == 2)) else rng.uniform(-frac, frac) * size) for a, c in enumerate(p)]
        for i, p in enumerate(b.truth)
    ]
    return b
