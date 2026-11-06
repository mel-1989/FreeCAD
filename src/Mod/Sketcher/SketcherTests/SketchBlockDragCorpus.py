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

"""Scenarios and helpers for the large-block drag tests (G10 slice 5b3).

A scenario is a sketch holding one or more instances of one block, built from
the slice-5a corpus generators, plus a seeded stream of drags.  ``lockstep``
runs the same drags on two copies of a scenario, one with the fast path of
``qp_eq`` allowed and one with the dense path forced, and reports the
differences.  Used by ``TestSketchBlockDrag``, ``SketchBlockDragMeasure`` and
``SketchBlockDragMutants``."""

import math
import random

import FreeCAD
import Part
import Sketcher

import SketcherBlocks as B
from SketcherTests import SketchBlocksCorpus as C
from SketcherTests import SketchBlockCurvesCorpus as CC

V = FreeCAD.Vector
TWO_PI = 2.0 * math.pi


def members(rng, n, kinds, collinear=False):
    out = [C._member(rng, rng.choice("la" if kinds in ("mixed", "plac") else kinds), collinear)]
    for _ in range(n - 1):
        out.append(C._member(rng, rng.choice("plac" if kinds == "mixed" else kinds), collinear))
    return out


def spec_of(name, n, kinds, seed, collinear=False, long_baseline=False, tiny=0):
    """A block spec of n members of `kinds` (letters of "plac", or "mixed").  `long_baseline` makes
    member 0 a line 30 m long, `tiny` makes that many members about 1e-4 mm across."""
    rng = random.Random(seed)
    mem = members(rng, n, kinds, collinear)
    if long_baseline:
        mem[0] = ("l", [-15000.0, 0.0, 15000.0, 40.0])
    for k in range(tiny):
        i = 1 + (k * 7) % max(1, n - 1)
        x, y = rng.uniform(-30, 30), rng.uniform(-30, 30)
        mem[i] = ("l", [x, y, x + 1e-4, y + 0.5e-4]) if k % 2 == 0 else ("c", [x, y, 1e-4])
    return C.Spec(name, mem)


class Scenario:
    """spec, number of instances, extra constraints, construction members, moves to make."""

    def __init__(self, name, spec=None, instances=1, outside=0, pin=False, construction=0, moves=10, seed=1, curves=0, arc_ends=True, extra=None):
        self.name = name
        self.extra = extra
        self.spec = spec
        self.instances = instances
        self.outside = outside  # distance constraints between points of different instances
        self.pin = pin  # pin one point of instance 0 to the origin frame (two datums)
        self.construction = construction
        self.moves = moves
        self.seed = seed
        self.curves = curves  # n curve members instead of a spec
        self.arc_ends = arc_ends

    def size(self):
        return self.curves if self.curves else len(self.spec.members)


def build(doc, sc):
    """The scenario's sketch and its instance names; deterministic in sc.seed."""
    rng = random.Random(sc.seed * 7919 + 13)
    places = [(rng.uniform(-30, 30), rng.uniform(-30, 30), rng.uniform(-3, 3)) for _ in range(sc.instances)]
    if sc.curves:
        geos = [[CC.ellipse, CC.arc_of_ellipse, CC.spline][k % 3](rng) for k in range(sc.curves)]
        sk = doc.addObject("Sketcher::SketchObject", "S_" + sc.name)
        sk.addGeometry(geos, False)
        B.define(sk, "blk", list(range(sc.curves)))
        sk.delGeometries(list(range(sc.curves)))
        names = [B.insert(sk, "blk", *p) for p in places]
        kp = [(i, 3 if (i % 3) < 2 else 1, 0, 0) for i in range(sc.curves)]
    elif sc.construction:
        # construction members belong to the definition, so the flag is set before the block is defined
        n = len(sc.spec.members)
        sk = doc.addObject("Sketcher::SketchObject", "S_" + sc.name)
        sk.addGeometry(C.geometry(sc.spec), False)
        for k in range(sc.construction):
            sk.toggleConstruction((k * 5) % n)
        B.define(sk, "blk", list(range(n)))
        sk.delGeometries(list(range(n)))
        names = [B.insert(sk, "blk", *p) for p in places]
        kp = C.key_points(sc.spec)
    else:
        sk, names = C.build(doc, sc.spec, places, B)
        kp = C.key_points(sc.spec)
    sk.solve()
    ids = [B.blocks(sk)["instances"][nm]["geo_ids"] for nm in names]
    if sc.instances > 1:
        for k in range(sc.outside):
            i0, i1 = rng.sample(range(len(kp)), 2)
            a, b = kp[i0], kp[i1]
            sk.addConstraint(Sketcher.Constraint("DistanceX", ids[0][a[0]], a[1], ids[1][b[0]], b[1], rng.uniform(-3, 3)))
    if sc.pin:
        i, pos = kp[0][0], kp[0][1]
        sk.addConstraint(Sketcher.Constraint("DistanceX", -1, 1, ids[0][i], pos, 1.0))
        sk.addConstraint(Sketcher.Constraint("DistanceY", -1, 1, ids[0][i], pos, 2.0))
    if sc.extra:
        sc.extra(sk, ids, kp)
    sk.solve()
    return sk, names, kp


def numbers(sk):
    """Every number of every geometry as (value, is_angle) pairs; angles compare modulo a turn."""
    out = []

    def add(vals, angles=()):
        out.extend((v, False) for v in vals)
        out.extend((v, True) for v in angles)

    for g in sk.Geometry:
        ti = g.TypeId
        if ti == "Part::GeomPoint":
            add([g.X, g.Y])
        elif ti == "Part::GeomLineSegment":
            add([g.StartPoint.x, g.StartPoint.y, g.EndPoint.x, g.EndPoint.y])
        elif ti == "Part::GeomCircle":
            add([g.Center.x, g.Center.y, g.Radius])
        elif ti == "Part::GeomArcOfCircle":
            add([g.Center.x, g.Center.y, g.Radius], [g.FirstParameter, g.LastParameter])
        elif ti == "Part::GeomEllipse":
            add([g.Center.x, g.Center.y, g.MajorRadius, g.MinorRadius], [g.AngleXU])
        elif ti == "Part::GeomArcOfEllipse":
            add([g.Center.x, g.Center.y, g.MajorRadius, g.MinorRadius], [g.AngleXU, g.FirstParameter, g.LastParameter])
        elif ti == "Part::GeomBSplineCurve":
            add([c for q in g.getPoles() for c in (q.x, q.y)] + list(g.getWeights()))
    return out


def difference(a, b):
    """Largest difference between two `numbers` lists (angles modulo a turn); inf when the shapes differ."""
    if len(a) != len(b):
        return math.inf
    worst = 0.0
    for (x, ang), (y, _) in zip(a, b):
        d = abs(math.remainder(x - y, TWO_PI)) if ang else abs(x - y)
        if not d <= worst:  # also catches nan
            worst = d
    return worst


def move_stream(sk, names, kp, rng, count, arc_ends=True):
    """Yield (geo id, pos, target) for `count` seeded drags, targets relative to the current geometry of sk."""
    for _ in range(count):
        name = rng.choice(names)
        ids = B.blocks(sk)["instances"][name]["geo_ids"]
        i, pos, _, _ = rng.choice(kp)
        if not arc_ends and pos in (1, 2) and sk.Geometry[ids[i]].TypeId == "Part::GeomArcOfCircle":
            pos = 3
        try:
            pt = sk.getPoint(ids[i], pos)
        except Exception:
            continue
        yield ids[i], pos, V(pt.x + rng.uniform(-8, 8), pt.y + rng.uniform(-8, 8), 0)


def drag(sk, gid, pos, target):
    try:
        rc = sk.moveGeometry(gid, pos, target, False)
    except ValueError:
        rc = -1
    return 0 if rc is None else rc


NOISE = 1e-15  # relative noise on the data H of the forced dense solve of the third copy
AGREE = 1e-6  # two copies of a move agree when no number differs by more than this (mm or rad)


def lockstep(doc, sc, moves=None, on_move=None, noise_copy=True):
    """Run sc's seeded drags on a fast copy, a dense-forced copy and (noise_copy) a dense-forced copy whose
    answers carry rounding-sized noise.  Returns a dict: moves, rc differences, the largest difference of
    any number after any move between fast and dense (max_diff) and between noisy-dense and dense
    (noise_diff, the solver's own sensitivity to rounding, since it stops at |step| <= 1e-8), and the
    fast/fallback/dense counts of the fast copy."""
    fast, names, kp = build(doc, sc)
    clone = lambda tag: Scenario(sc.name + tag, sc.spec, sc.instances, sc.outside, sc.pin, sc.construction, sc.moves, sc.seed, sc.curves, sc.arc_ends, sc.extra)
    dense, _, _ = build(doc, clone("_d"))
    noisy = build(doc, clone("_n"))[0] if noise_copy else None
    rng = random.Random(sc.seed * 104729 + 7)
    res = {"scenario": sc.name, "members": sc.size(), "moves": 0, "rc_differences": 0, "max_diff": 0.0, "noise_diff": 0.0, "noise_rc_differences": 0, "agree_fast": 0, "agree_noise": 0, "fast": 0, "fallback": 0, "dense": 0, "refused": 0, "forced_leaks": 0, "forced_dense_calls": 0}
    for gid, pos, target in move_stream(fast, names, kp, rng, moves if moves is not None else sc.moves, sc.arc_ends):
        Sketcher.qpEqForceDense(False)
        Sketcher.qpEqReset()
        rc_f = drag(fast, gid, pos, target)
        st = Sketcher.qpEqStats()
        Sketcher.qpEqForceDense(True)
        Sketcher.qpEqReset()
        rc_d = drag(dense, gid, pos, target)
        sd = Sketcher.qpEqStats()
        res["forced_leaks"] += sd["fast"] + sd["fallback"]  # a forced-dense drag must never try the fast path
        res["forced_dense_calls"] += sd["dense"]
        if noisy is not None:
            Sketcher.qpEqForceDense(True)
            Sketcher.qpEqNoise(NOISE)
            rc_n = drag(noisy, gid, pos, target)
            Sketcher.qpEqNoise(0.0)
            res["noise_rc_differences"] += rc_n != rc_d
            dn = difference(numbers(noisy), numbers(dense))
            res["noise_diff"] = max(res["noise_diff"], dn)
            res["agree_noise"] += dn <= AGREE
        Sketcher.qpEqForceDense(False)
        res["fast"] += st["fast"]
        res["fallback"] += st["fallback"]
        res["dense"] += st["dense"]
        res["moves"] += 1
        res["rc_differences"] += rc_f != rc_d
        res["refused"] += rc_f != 0
        df = difference(numbers(fast), numbers(dense))
        res["max_diff"] = max(res["max_diff"], df)
        res["agree_fast"] += df <= AGREE
        if on_move:
            on_move(fast, dense, rc_f, rc_d)
    doc.removeObject(fast.Name)
    doc.removeObject(dense.Name)
    if noisy is not None:
        doc.removeObject(noisy.Name)
    return res


def scenarios(full=False):
    """The check-3 corpus.  Quick form is a handful of moves each; full form is at least 2,000 moves."""
    k = 1 if full else 0
    out = []

    def add(name, spec, moves_quick, moves_full, **kw):
        out.append(Scenario(name, spec, moves=moves_full if full else moves_quick, **kw))

    add("lines55", spec_of("l55", 55, "l", 11), 4, 300)
    add("mixed55", spec_of("m55", 55, "plac", 12), 4, 150)
    add("arcs55", spec_of("a55", 55, "a", 13), 3, 40)
    add("circles_lines80", spec_of("cl80", 80, "cl", 14), 3, 150)
    add("points_lines70", spec_of("pl70", 70, "pl", 15), 3, 150)
    add("collinear60", spec_of("co60", 60, "plac", 16, collinear=True), 3, 120)
    add("mixed89", spec_of("m89", 89, "plac", 17), 2, 60)
    add("lines89_two_instances", spec_of("l89", 89, "l", 18), 3, 80, instances=2)
    add("mixed55_tied", spec_of("m55t", 55, "plac", 19), 4, 120, instances=2, outside=1)
    add("mixed55_outside_pin", spec_of("m55p", 55, "plac", 20), 4, 120, instances=2, outside=2, pin=True)
    add("lines60_pin", spec_of("l60p", 60, "l", 21), 3, 200, pin=True)
    add("mixed60_construction", spec_of("m60c", 60, "plac", 22), 3, 150, construction=12)
    add("long_baseline60", spec_of("lb60", 60, "plac", 23, long_baseline=True), 3, 150)
    add("tiny_members60", spec_of("tm60", 60, "plac", 24, tiny=12), 3, 200)
    add("curves40", None, 2, 60, curves=40)
    if full:
        add("lines144", spec_of("l144", 144, "l", 25), 0, 12)
        add("mixed144", spec_of("m144", 144, "plac", 26), 0, 12)
        add("lines200", spec_of("l200", 200, "l", 27), 0, 6)
        add("mixed200", spec_of("m200", 200, "plac", 28), 0, 6)
    return out


# ---------------------------------------------------------------------------------------------
# systems for qp_eq itself (checks 4 and 5)


def structured_system(seed, ns=200, frames=1, pins=0):
    """The Jacobian of `frames` block instances over `ns` member parameters, as lists for Sketcher.qpEq:
    each row holds one member parameter and up to two frame columns (X or Y, and the angle), like
    ConstraintBlockPlacement; `pins` extra rows hold one or two member parameters (outside constraints).
    Columns are the member parameters first, then X, Y, a of every frame.  H is a positive definite
    BFGS-like matrix.  Returns (H, g, A, c) as lists, A as a list of rows."""
    rng = random.Random(seed)
    n = ns + 3 * frames
    rows = []
    for j in range(ns):
        row = {j: rng.choice((-1, 1)) * rng.uniform(0.5, 2.0)}
        f = j % frames
        t = (j // frames) % 3
        if t == 0:
            row[ns + 3 * f] = 1.0
            row[ns + 3 * f + 2] = rng.uniform(-50, 50)
        elif t == 1:
            row[ns + 3 * f + 1] = 1.0
            row[ns + 3 * f + 2] = rng.uniform(-50, 50)
        else:
            row[ns + 3 * f + 2] = 1.0
        rows.append(row)
    for _ in range(pins):
        row = {}
        for _ in range(rng.choice((1, 2))):
            row[rng.randrange(ns)] = rng.choice((-1, 1)) * rng.uniform(0.5, 2.0)
        rows.append(row)
    A = [[row.get(j, 0.0) for j in range(n)] for row in rows]
    u = [[rng.uniform(-1, 1) for _ in range(n)] for _ in range(3)]
    d = [rng.uniform(0.5, 2.0) for _ in range(n)]
    H = [[(d[i] if i == j else 0.0) + 0.2 * sum(u[k][i] * u[k][j] for k in range(3)) for j in range(n)] for i in range(n)]
    g = [rng.uniform(-1, 1) for _ in range(n)]
    c = [rng.uniform(-1, 1) for _ in rows]
    return H, g, A, c


# ---------------------------------------------------------------------------------------------
# small sketches (check 7): below the size threshold the dense path runs exactly as before


def small_sketch(doc, rng):
    """A closed polygon of 5 to 24 lines with coincidences, horizontals, verticals, equal lengths and
    two datums, and sometimes a circle on a corner.  Fewer than 100 parameters.  Returns (sketch, n lines)."""
    sk = doc.addObject("Sketcher::SketchObject", "Small")
    n = rng.randint(5, 24)
    pts = [(rng.uniform(-40, 40), rng.uniform(-40, 40)) for _ in range(n)]
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        sk.addGeometry(Part.LineSegment(V(a[0], a[1], 0), V(b[0], b[1], 0)), False)
    for i in range(n):
        sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % n, 1))
    for i in range(0, n, 3):
        sk.addConstraint(Sketcher.Constraint("Horizontal" if (i // 3) % 2 == 0 else "Vertical", i))
    for _ in range(rng.randint(0, 3)):
        a, b = rng.sample(range(n), 2)
        sk.addConstraint(Sketcher.Constraint("Equal", a, b))
    if rng.random() < 0.5:
        sk.addGeometry(Part.Circle(V(pts[0][0], pts[0][1], 0), V(0, 0, 1), rng.uniform(1, 5)), False)
        sk.addConstraint(Sketcher.Constraint("Coincident", n, 3, 0, 1))
        sk.addConstraint(Sketcher.Constraint("Radius", n, rng.uniform(1, 5)))
    sk.addConstraint(Sketcher.Constraint("DistanceX", -1, 1, 0, 1, pts[0][0]))
    sk.addConstraint(Sketcher.Constraint("DistanceY", -1, 1, 0, 1, pts[0][1]))
    sk.solve()
    return sk, n


def small_digests(doc, count=216, seed=640, drags=8):
    """Per sketch: for each of `drags` seeded drags, the return code and a hash of the exact bits of
    every geometry number afterwards.  Deterministic in `seed`; independent of the block code."""
    import hashlib
    import struct

    rng = random.Random(seed)
    out = []
    for _ in range(count):
        sk, n = small_sketch(doc, rng)
        parts = []
        for _ in range(drags):
            i = rng.randrange(n)
            pos = rng.choice((1, 2))
            pt = sk.getPoint(i, pos)
            try:
                rc = sk.moveGeometry(i, pos, V(pt.x + rng.uniform(-5, 5), pt.y + rng.uniform(-5, 5), 0), False)
            except ValueError:
                rc = -1
            vals = [v for v, _ in numbers(sk)]
            parts.append("%d:%s" % (-1 if rc is None else rc, hashlib.sha1(struct.pack("<%dd" % len(vals), *vals)).hexdigest()[:10]))
        out.append(" ".join(parts))
        doc.removeObject(sk.Name)
    return out
