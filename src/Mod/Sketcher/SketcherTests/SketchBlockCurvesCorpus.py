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
# *   Lesser General Public License for more details.                       *
# *                                                                         *
# *   You should have received a copy of the GNU Lesser General Public      *
# *   License along with FreeCAD. If not, see                               *
# *   <https://www.gnu.org/licenses/>.                                      *
# *                                                                         *

"""A seeded corpus of blocks holding ellipses, arcs of ellipse and B-splines (G10 slice 5b2), with the analytic
truth for them: where every sampled point of every curve must be for an instance placed at (X, Y, angle).

A `Spec` holds Part geometry in the sketch's own coordinates.  The block frame's origin is the first point of the first
member (the centre of an ellipse, the first pole of a spline), as `SketcherBlocks.define` takes it, and the truth is
computed here from the geometry, not from the block code."""

import math
import random

import FreeCAD
import Part

import SketcherBlocks as B

V = FreeCAD.Vector
TWO_PI = 2.0 * math.pi
SAMPLES = 40


class Spec:
    def __init__(self, name, geos):
        self.name = name
        self.geos = geos

    def __repr__(self):
        return "<Curves %s (%d members)>" % (self.name, len(self.geos))


def ellipse(rng):
    a = rng.uniform(4.0, 12.0)
    e = Part.Ellipse(V(rng.uniform(-30, 30), rng.uniform(-30, 30), 0), a, a * rng.uniform(0.3, 0.85))
    e.AngleXU = rng.uniform(-math.pi, math.pi)
    return e


def arc_of_ellipse(rng):
    a0 = rng.uniform(-3.0, 3.0)
    return Part.ArcOfEllipse(ellipse(rng), a0, a0 + rng.uniform(0.6, 5.5))


def spline(rng, periodic=False, degree=None, rational=None, interior=None, one_unit_weight=False):
    """A B-spline with random poles, clamped when open; `interior` sets the largest interior multiplicity."""
    d = degree or rng.randint(2, 5)
    interior = interior or 1
    for _ in range(100):
        m = rng.randint(5, 9) if periodic else rng.randint(3, 6)
        mults = [rng.randint(1, min(interior, d)) for _ in range(m)]
        if periodic:
            mults[-1] = mults[0]
            n = sum(mults) - mults[0]
        else:
            mults[0] = mults[-1] = d + 1
            n = sum(mults) - d - 1
        if n < (d + 1 if periodic else max(d + 1, 3)) or n > 14:
            continue
        knots = sorted(set(round(rng.uniform(0.0, 10.0), 3) for _ in range(m)))
        if len(knots) != m:
            continue
        poles = [V(rng.uniform(-25, 25), rng.uniform(-25, 25), 0) for _ in range(n)]
        if rational is None:
            rational = rng.random() < 0.5
        weights = [round(rng.uniform(1.5, 3.0), 3) for _ in range(n)] if rational else [1.0] * n
        if one_unit_weight and rational:
            weights[rng.randrange(n)] = 1.0  # exactly one weight of 1.0: the case addBSpline's start-value tweak is for
        b = Part.BSplineCurve()
        try:
            b.buildFromPolesMultsKnots(poles, mults, knots, periodic, d, weights)
        except Exception:
            continue
        return b
    raise RuntimeError("no spline")


def line(rng):
    x, y = rng.uniform(-30, 30), rng.uniform(-30, 30)
    return Part.LineSegment(V(x, y, 0), V(x + rng.uniform(3, 9), y + rng.uniform(-9, 9), 0))


def arc(rng):
    a0 = rng.uniform(0, TWO_PI)
    return Part.ArcOfCircle(Part.Circle(V(rng.uniform(-30, 30), rng.uniform(-30, 30), 0), V(0, 0, 1), rng.uniform(2, 6)), a0, a0 + rng.uniform(0.5, 5))


def corpus(count=48, seed=6301):
    """At least 40 blocks: ellipses, arcs of ellipse, polynomial and rational, open and periodic B-splines of degrees 2
    to 5, interior multiplicities, mixed with the kinds of slice 5a."""
    rng = random.Random(seed)
    out = []
    for i in range(count):
        k = i % 12
        if k == 0:
            geos, tag = [ellipse(rng)], "ellipse"
        elif k == 1:
            geos, tag = [arc_of_ellipse(rng)], "arc_of_ellipse"
        elif k == 2:
            geos, tag = [spline(rng, False, rational=False)], "spline_poly"
        elif k == 3:
            geos, tag = [spline(rng, False, rational=True, one_unit_weight=True)], "spline_rational"
        elif k == 4:
            geos, tag = [spline(rng, True, rational=False)], "spline_periodic"
        elif k == 5:
            geos, tag = [spline(rng, True, rational=True)], "spline_periodic_rational"
        elif k == 6:
            geos, tag = [spline(rng, False, interior=3)], "spline_multiplicity"
        elif k == 7:
            geos, tag = [ellipse(rng), line(rng), arc(rng)], "ellipse+line+arc"
        elif k == 8:
            geos, tag = [arc_of_ellipse(rng), spline(rng, False), Part.Circle(V(rng.uniform(-30, 30), rng.uniform(-30, 30), 0), V(0, 0, 1), 3.0)], "aoe+spline+circle"
        elif k == 9:
            geos, tag = [line(rng), ellipse(rng), arc_of_ellipse(rng)], "line+ellipse+aoe"
        elif k == 10:
            geos, tag = [spline(rng, True, interior=2), spline(rng, False, rational=True), ellipse(rng)], "two_splines+ellipse"
        else:
            geos, tag = [spline(rng, False, degree=5, rational=True), arc_of_ellipse(rng)], "degree5+aoe"
        out.append(Spec("C%02d_%s" % (i, tag), geos))
    return out


def origin_of(geo):
    """The first point of a member, the block frame's origin when it is the first member."""
    t = geo.TypeId
    if t == "Part::GeomBSplineCurve":
        p = geo.getPoles()[0]
        return p.x, p.y
    if t in ("Part::GeomEllipse", "Part::GeomArcOfEllipse", "Part::GeomCircle", "Part::GeomArcOfCircle"):
        return geo.Center.x, geo.Center.y
    if t == "Part::GeomPoint":
        return geo.X, geo.Y
    return geo.StartPoint.x, geo.StartPoint.y


def samples(geo, n=SAMPLES):
    """n points along a curve, by the fraction of its parameter range, as (x, y)."""
    if geo.TypeId == "Part::GeomPoint":
        return [(geo.X, geo.Y)]
    if geo.TypeId == "Part::GeomEllipse":
        a, b = 0.0, TWO_PI
    else:
        a, b = geo.FirstParameter, geo.LastParameter
        if b <= a:
            b += TWO_PI
    out = []
    for k in range(n):
        p = geo.value(a + (b - a) * k / (n - 1))
        out.append((p.x, p.y))
    return out


def place(pt, origin, X, Y, th):
    dx, dy = pt[0] - origin[0], pt[1] - origin[1]
    c, s = math.cos(th), math.sin(th)
    return X + dx * c - dy * s, Y + dx * s + dy * c


def weights_of(geo):
    return [float(w) for w in geo.getWeights()] if geo.TypeId == "Part::GeomBSplineCurve" else []


def deviation(spec, X, Y, th, inst_geos):
    """(largest distance in mm between the sampled points of an instance's curves and the analytic placement,
    largest weight difference)."""
    origin = origin_of(spec.geos[0])
    mm, dw = 0.0, 0.0
    for src, got in zip(spec.geos, inst_geos):
        if src.TypeId == "Part::GeomCircle":  # a whole circle has no orientation: its points are not matched by parameter
            c = place((src.Center.x, src.Center.y), origin, X, Y, th)
            mm = max(mm, math.hypot(c[0] - got.Center.x, c[1] - got.Center.y), abs(src.Radius - got.Radius))
            continue
        want = [place(p, origin, X, Y, th) for p in samples(src)]
        have = samples(got)
        for a, b in zip(want, have):
            mm = max(mm, math.hypot(a[0] - b[0], a[1] - b[1]))
        ws = weights_of(src)
        factor = B._weight_scale(ws)  # a definition holds no lone weight of 1.0: its weights are those times a power of 2
        for a, b in zip(ws, weights_of(got)):
            dw = max(dw, abs(a * factor - b))
    return mm, dw
