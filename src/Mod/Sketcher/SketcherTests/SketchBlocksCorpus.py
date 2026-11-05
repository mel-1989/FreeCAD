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

"""Corpus for the sketch-block tests (G10 slice 5a, checks 2 to 5).

``corpus()`` returns a deterministic list of block specs, each with analytic
truth: the members' coordinates in the block's own frame and a placement.
``build`` makes a sketch holding one or more instances of one."""

import math
import random

import FreeCAD
import Part
import Sketcher

V = FreeCAD.Vector
TWO_PI = 2.0 * math.pi
SIZES = [1, 2, 3, 4, 5, 8, 13, 21, 34, 55, 89, 144, 200]
MIXES = ["mixed", "lines", "arcs", "circles+lines", "points+lines", "collinear"]


class Spec:
    def __init__(self, name, members):
        self.name = name
        self.members = members  # [(type, numbers)] in the block's own frame, member 0 is a line or arc

    def __repr__(self):
        return "<Block %s (%d members)>" % (self.name, len(self.members))


def _member(rng, kind, collinear):
    c = 0.0 if collinear else None

    def pt():
        return rng.uniform(-40, 40), (0.0 if collinear else rng.uniform(-40, 40))

    if kind == "p":
        x, y = pt()
        return "p", [x, y]
    if kind == "l":
        x, y = pt()
        x2, y2 = pt()
        if abs(x2 - x) + abs(y2 - y) < 1.0:
            x2 += 5.0
        return "l", [x, y, x2, y2]
    x, y = pt()
    r = rng.uniform(0.5, 5.0)
    if kind == "c":
        return "c", [x, y, r]
    a0 = rng.uniform(0.0, TWO_PI)
    return "a", [x, y, r, a0, a0 + rng.uniform(0.3, 5.5)]


def corpus(count=48, seed=5001):
    """At least 40 blocks: sizes 1 to 200, six mixes, collinear members included."""
    rng = random.Random(seed)
    out = []
    for i in range(count):
        n = SIZES[i % len(SIZES)]
        mix = MIXES[(i // 2) % len(MIXES)]
        collinear = mix == "collinear"
        kinds = {
            "mixed": "plac",
            "lines": "l",
            "arcs": "a",
            "circles+lines": "cl",
            "points+lines": "pl",
            "collinear": "plac",
        }[mix]
        members = [_member(rng, rng.choice("la" if mix != "lines" and mix != "arcs" else kinds), collinear)]
        for _ in range(n - 1):
            members.append(_member(rng, rng.choice(kinds), collinear))
        out.append(Spec("B%02d_%s_%d" % (i, mix.replace("+", "_"), n), members))
    return out


def geometry(spec, X=0.0, Y=0.0, th=0.0):
    """The members of `spec` as Part geometry at a placement (analytic)."""
    c, s = math.cos(th), math.sin(th)

    def tr(x, y):
        return X + x * c - y * s, Y + x * s + y * c

    geos = []
    for t, g in spec.members:
        if t == "p":
            x, y = tr(g[0], g[1])
            geos.append(Part.Point(V(x, y, 0)))
        elif t == "l":
            x, y = tr(g[0], g[1])
            x2, y2 = tr(g[2], g[3])
            geos.append(Part.LineSegment(V(x, y, 0), V(x2, y2, 0)))
        elif t == "c":
            x, y = tr(g[0], g[1])
            geos.append(Part.Circle(V(x, y, 0), V(0, 0, 1), g[2]))
        else:
            x, y = tr(g[0], g[1])
            geos.append(Part.ArcOfCircle(Part.Circle(V(x, y, 0), V(0, 0, 1), g[2]), g[3] + th, g[4] + th))
    return geos


def key_points(spec):
    """[(member index, PointPos, local x, local y)] for every point a constraint can name."""
    out = []
    for i, (t, g) in enumerate(spec.members):
        if t == "p":
            out.append((i, 1, g[0], g[1]))
        elif t == "l":
            out.append((i, 1, g[0], g[1]))
            out.append((i, 2, g[2], g[3]))
        elif t == "c":
            out.append((i, 3, g[0], g[1]))
        else:
            out.append((i, 3, g[0], g[1]))
            out.append((i, 1, g[0] + g[2] * math.cos(g[3]), g[1] + g[2] * math.sin(g[3])))
            out.append((i, 2, g[0] + g[2] * math.cos(g[4]), g[1] + g[2] * math.sin(g[4])))
    return out


def build(doc, spec, placements, blocks):
    """A sketch with one instance per placement (x, y, angle) of the block.

    The block's frame has its origin at the first point of member 0, so the
    analytic world position of the member geometry at placement (x, y, th) is
    `geometry(spec, ...)` shifted by that point.  Returns (sketch, [instance names])."""
    sk = doc.addObject("Sketcher::SketchObject", "S_" + spec.name)
    sk.addGeometry(geometry(spec), False)
    blocks.define(sk, "blk", list(range(len(spec.members))))
    sk.delGeometries(list(range(len(spec.members))))
    names = [blocks.insert(sk, "blk", *p) for p in placements]
    return sk, names


def origin_of(spec):
    """The block frame's origin in the spec's coordinates (its first point)."""
    t, g = spec.members[0]
    return g[0], g[1]


def world_of(spec, X, Y, th):
    """Analytic world numbers of every member for an instance placed at (X, Y, th)."""
    ox, oy = origin_of(spec)
    shifted = Spec(spec.name, [])
    for t, g in spec.members:
        g = list(g)
        g[0] -= ox
        g[1] -= oy
        if t == "l":
            g[2] -= ox
            g[3] -= oy
        shifted.members.append((t, g))
    out = []
    c, s = math.cos(th), math.sin(th)
    for t, g in shifted.members:
        w = list(g)
        w[0], w[1] = X + g[0] * c - g[1] * s, Y + g[0] * s + g[1] * c
        if t == "l":
            w[2], w[3] = X + g[2] * c - g[3] * s, Y + g[2] * s + g[3] * c
        elif t == "a":
            w[3], w[4] = g[3] + th, g[4] + th
        out.append((t, w))
    return out


def member_numbers(geo):
    """(type, numbers) of a sketch geometry, as the corpus stores them."""
    ti = geo.TypeId
    if ti == "Part::GeomPoint":
        return "p", [geo.X, geo.Y]
    if ti == "Part::GeomLineSegment":
        return "l", [geo.StartPoint.x, geo.StartPoint.y, geo.EndPoint.x, geo.EndPoint.y]
    if ti == "Part::GeomCircle":
        return "c", [geo.Center.x, geo.Center.y, geo.Radius]
    return "a", [geo.Center.x, geo.Center.y, geo.Radius, geo.FirstParameter, geo.LastParameter]


def deviation(spec, X, Y, th, geos):
    """Largest difference between sketch geometry `geos` (member order) and the analytic
    placement: lengths in mm, arc angles mod a turn in rad (returned as a pair)."""
    mm, rad = 0.0, 0.0
    for (t, want), geo in zip(world_of(spec, X, Y, th), geos):
        t2, have = member_numbers(geo)
        if t != t2:
            return math.inf, math.inf
        n = 3 if t == "a" else len(want)
        for a, b in zip(want[:n], have[:n]):
            mm = max(mm, abs(a - b))
        if t == "a":
            for a, b in zip(want[3:], have[3:]):
                rad = max(rad, abs(math.remainder(a - b, TWO_PI)))
    return mm, rad


def placement_constraints(sk, spec, inst_geo_ids, X, Y, th, ):
    """Constrain an instance to (X, Y, th) by coordinates of two distinct points; returns
    the number of constraints added (3: the first point's x and y, and one coordinate of a
    second point, chosen where it is not degenerate)."""
    kp = key_points(spec)
    ox, oy = origin_of(spec)
    i0, p0, lx0, ly0 = kp[0]
    world = lambda lx, ly: (
        X + (lx - ox) * math.cos(th) - (ly - oy) * math.sin(th),
        Y + (lx - ox) * math.sin(th) + (ly - oy) * math.cos(th),
    )
    wx, wy = world(lx0, ly0)
    for i, p, lx, ly in kp[1:]:
        if math.hypot(lx - lx0, ly - ly0) > 1e-3:
            w2 = world(lx, ly)
            break
    g0, g1 = inst_geo_ids[i0], inst_geo_ids[i]
    sk.addConstraint(Sketcher.Constraint("DistanceX", -1, 1, g0, p0, wx))
    sk.addConstraint(Sketcher.Constraint("DistanceY", -1, 1, g0, p0, wy))
    # the second point's x when it lies more above/below the first than beside it, else its y: the
    # constrained coordinate then moves fastest with the angle, and the two angles that satisfy it
    # lie far apart, so the solver cannot settle on the wrong one from a seed a little off
    if abs(w2[1] - wy) >= abs(w2[0] - wx):
        sk.addConstraint(Sketcher.Constraint("DistanceX", -1, 1, g1, p, w2[0]))
    else:
        sk.addConstraint(Sketcher.Constraint("DistanceY", -1, 1, g1, p, w2[1]))
    return 3
