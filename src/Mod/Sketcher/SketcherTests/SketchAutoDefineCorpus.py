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

"""Strip-and-recover corpus for the auto fully-define tests (G10 slice 2).

A case is a fully constrained sketch (DOF 0, no conflict) whose constraints
are the truth.  The oracle strips constraints from a clone of it and asks
``SketcherAutoDefine.propose`` to recover a fully defined sketch from the
geometry that is left.  Three sources, all deterministic:

* ``generated(...)``: 12 parametric families, each built with and without a
  rotation, with seeded non-round sizes.  Each family builder writes the
  relations and the dimensions of a sensible design, so the original
  constraints say what a designer meant.
* ``real_cases(...)``: every non-trivial DOF 0 sketch found in the tree's own
  files, frozen in ``REAL_SKETCHES`` (found by opening every FCStd file under
  ``src/`` and ``data/`` and keeping sketches with at least two geometries,
  at least one constraint, solver result 0 and DOF 0).  Sketches with
  external geometry or an attachment are counted as ordinary ones: the
  external geometry travels with the clone and is ignored by the generator.
* ``slice1_cases(...)``: the fully constrained cases of slice 1's corpus.

``strip(sketch, level, fraction, seed)`` returns a stripped clone and the
indices of the original constraints it kept.  Levels: ``dims`` removes the
driving dimensions only, ``all`` removes every driving constraint that is not
internal alignment, ``partial`` keeps a seeded fraction of the removable
constraints.  Reference (non-driving) constraints stay.
"""

import math
import random
import re

import FreeCAD
import Part
import Sketcher

V = FreeCAD.Vector
DATUM_TYPES = ("Distance", "DistanceX", "DistanceY", "Radius", "Diameter", "Angle", "Weight")
RELATION_TYPES = (
    "Coincident",
    "Horizontal",
    "Vertical",
    "Parallel",
    "Perpendicular",
    "Tangent",
    "Equal",
    "Symmetric",
)

# (file relative to src/ or data/ of the tree, sketch name).  Frozen list.
REAL_SKETCHES = []  # filled in below from _REAL_TABLE


class Case:
    def __init__(self, name, family, sketch, doc, real=False, rotated=False):
        self.name = name
        self.family = family
        self.sketch = sketch
        self.doc = doc
        self.real = real
        self.rotated = rotated

    def __repr__(self):
        return "<Case %s>" % self.name


# ---------------------------------------------------------------------------
# Clone and strip
# ---------------------------------------------------------------------------


def clone(doc, sketch, name="Clone"):
    """Copy a sketch into `doc` through its serialised properties (no links,
    no recompute).  Expressions that stay inside the sketch are copied."""
    n = doc.addObject("Sketcher::SketchObject", name)
    n.Placement = sketch.Placement
    for prop in ("Geometry", "ExternalGeo", "Constraints"):
        n.restorePropertyContent(prop, sketch.dumpPropertyContent(prop))
    for path, expr in sketch.ExpressionEngine:
        try:
            n.setExpression(path.lstrip("."), expr)
        except Exception:
            pass
    n.solve()
    return n


def removable(sketch):
    """Indices of the constraints the strip may remove."""
    return [
        i
        for i, c in enumerate(sketch.Constraints)
        if c.Driving and c.Type != "InternalAlignment" and c.IsActive
    ]


def strip(doc, sketch, level, fraction=0.5, seed=0, name="Stripped"):
    """Return (stripped clone, kept) where `kept` maps the indices of the
    constraints that survive in the clone to their indices in `sketch`."""
    cons = list(sketch.Constraints)
    rem = removable(sketch)
    if level == "dims":
        drop = {i for i in rem if cons[i].Type in DATUM_TYPES}
    elif level == "all":
        drop = set(rem)
    elif level == "partial":
        rng = random.Random("strip-%s-%s" % (seed, len(cons)))
        chosen = rng.sample(rem, int(round(len(rem) * fraction))) if rem else []
        drop = set(rem) - set(chosen)
    else:
        raise ValueError(level)
    n = doc.addObject("Sketcher::SketchObject", name)
    n.Placement = sketch.Placement
    n.restorePropertyContent("Geometry", sketch.dumpPropertyContent("Geometry"))
    n.restorePropertyContent("ExternalGeo", sketch.dumpPropertyContent("ExternalGeo"))
    kept = [i for i in range(len(cons)) if i not in drop]
    n.Constraints = [cons[i] for i in kept]
    remap = {old: new for new, old in enumerate(kept)}
    for path, expr in sketch.ExpressionEngine:
        p = path.lstrip(".")
        m = re.match(r"Constraints\[(\d+)\]$", p)
        if m:
            old = int(m.group(1))
            if old in remap:
                try:
                    n.setExpression("Constraints[%d]" % remap[old], expr)
                except Exception:
                    pass
            continue
        m = re.match(r"Constraints\.(\w+)$", p)
        if m and any(cons[i].Name == m.group(1) for i in remap):
            try:
                n.setExpression(p, expr)
            except Exception:
                pass
    n.solve()
    return n, {new: old for old, new in remap.items()}


# ---------------------------------------------------------------------------
# Generated families
# ---------------------------------------------------------------------------


class _B:
    """Builds one generated sketch in a local frame that is rotated by `rot`
    and shifted by `off`.  ``hv`` writes Horizontal or Vertical when the frame
    is not rotated and an angle or a parallel/perpendicular relation when it
    is, so a rotated copy has the same design intent."""

    def __init__(self, doc, name, rot, off):
        self.doc = doc
        self.o = doc.addObject("Sketcher::SketchObject", name)
        self.rot = rot
        self.off = off
        self.geo = []
        self.cons = []
        self.ref = None
        self.ctor = []

    def T(self, u, v):
        c, s = math.cos(self.rot), math.sin(self.rot)
        return (self.off[0] + c * u - s * v, self.off[1] + s * u + c * v)

    def _add(self, g, construction):
        self.geo.append(g)
        self.ctor.append(construction)
        return len(self.geo) - 1

    def line(self, u1, v1, u2, v2, construction=False):
        (x1, y1), (x2, y2) = self.T(u1, v1), self.T(u2, v2)
        return self._add(Part.LineSegment(V(x1, y1, 0), V(x2, y2, 0)), construction)

    def circle(self, u, v, r, construction=False):
        x, y = self.T(u, v)
        return self._add(Part.Circle(V(x, y, 0), V(0, 0, 1), r), construction)

    def arc(self, u, v, r, a0, a1, construction=False):
        x, y = self.T(u, v)
        c = Part.Circle(V(x, y, 0), V(0, 0, 1), r)
        return self._add(Part.ArcOfCircle(c, a0 + self.rot, a1 + self.rot), construction)

    def c(self, *args):
        self.cons.append(Sketcher.Constraint(*args))
        return len(self.cons) - 1

    # geometry readers for dimension values ------------------------------
    def pt(self, g, pos):
        o = self.geo[g]
        if pos == 1:
            p = o.StartPoint
        elif pos == 2:
            p = o.EndPoint
        else:
            p = o.Center
        return (p.x, p.y)

    def length(self, g):
        a, b = self.pt(g, 1), self.pt(g, 2)
        return math.hypot(b[0] - a[0], b[1] - a[1])

    def direction(self, g):
        a, b = self.pt(g, 1), self.pt(g, 2)
        return math.atan2(b[1] - a[1], b[0] - a[0])

    # design-intent helpers -------------------------------------------------
    def hv(self, g, kind):
        if self.rot == 0.0:
            self.c("Horizontal" if kind == "H" else "Vertical", g)
        elif self.ref is None:
            self.ref = (g, kind)
            self.c("Angle", g, self.direction(g))
        elif self.ref[1] == kind:
            self.c("Parallel", g, self.ref[0])
        else:
            self.c("Perpendicular", g, self.ref[0])

    def align(self, p, q, kind):
        """Align two points horizontally or vertically."""
        if self.rot == 0.0:
            self.c("Horizontal" if kind == "H" else "Vertical", p[0], p[1], q[0], q[1])
            return
        a, b = self.pt(*p), self.pt(*q)
        g = self._add(Part.LineSegment(V(a[0], a[1], 0), V(b[0], b[1], 0)), True)
        self.c("Coincident", g, 1, p[0], p[1])
        self.c("Coincident", g, 2, q[0], q[1])
        self.hv(g, kind)

    def length_dim(self, g, kind):
        a, b = self.pt(g, 1), self.pt(g, 2)
        if self.rot == 0.0 and kind == "H":
            self.c("DistanceX", g, 1, g, 2, b[0] - a[0]) if b[0] > a[0] else self.c("DistanceX", g, 2, g, 1, a[0] - b[0])
        elif self.rot == 0.0 and kind == "V":
            self.c("DistanceY", g, 1, g, 2, b[1] - a[1]) if b[1] > a[1] else self.c("DistanceY", g, 2, g, 1, a[1] - b[1])
        else:
            self.c("Distance", g, self.length(g))

    def place(self, g, pos):
        x, y = self.pt(g, pos)
        self.c("DistanceX", g, pos, x)
        self.c("DistanceY", g, pos, y)

    def finish(self):
        self.o.addGeometry(self.geo, False)
        for i, flag in enumerate(self.ctor):
            if flag:
                self.o.setConstruction(i, True)
        self.o.addConstraint(self.cons)
        self.o.solve()
        return self.o


def _poly_rect(b, x0, y0, w, h):
    """Four lines (bottom, right, top, left) with coincident corners and
    horizontal/vertical relations."""
    pts = [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)]
    ids = [b.line(*pts[i], *pts[(i + 1) % 4]) for i in range(4)]
    for i in range(4):
        b.c("Coincident", ids[i], 2, ids[(i + 1) % 4], 1)
    for i, k in enumerate("HVHV"):
        b.hv(ids[i], k)
    return ids


def _plate_holes(b, rng):
    w, h = rng.uniform(55, 95), rng.uniform(38, 70)
    ids = _poly_rect(b, rng.uniform(-5, 8), rng.uniform(-4, 6), w, h)
    b.length_dim(ids[0], "H")
    b.length_dim(ids[1], "V")
    b.place(ids[0], 1)
    r = rng.uniform(2.1, 4.9)
    hx, hy = rng.uniform(9, 17), rng.uniform(8, 14)
    dx, dy = rng.uniform(20, w - 2 * hx - 3), rng.uniform(12, h - 2 * hy - 2)
    u0, v0 = b.pt(ids[0], 1)
    o = b.rot
    # hole centres in the local frame of the rectangle
    lx0, ly0 = _local(b, ids[0])
    c = [b.circle(lx0 + hx + i * dx, ly0 + hy + j * dy, r) for j in range(2) for i in range(2)]
    b.align((c[0], 3), (c[1], 3), "H")
    b.align((c[0], 3), (c[2], 3), "V")
    b.align((c[2], 3), (c[3], 3), "H")
    b.align((c[1], 3), (c[3], 3), "V")
    for k in (1, 2, 3):
        b.c("Equal", c[0], c[k])
    b.c("Diameter", c[0], 2 * r)
    b.c("Distance", c[0], 3, ids[3], _pl(b, c[0], ids[3]))
    b.c("Distance", c[0], 3, ids[0], _pl(b, c[0], ids[0]))
    b.c("Distance", c[0], 3, c[1], _pp(b, c[0], c[1]))
    b.c("Distance", c[0], 3, c[2], _pp(b, c[0], c[2]))


def _local(b, line0):
    """Local-frame start point of the first rectangle line (the line was
    created from local coordinates that were rotated and shifted)."""
    x, y = b.pt(line0, 1)
    c, s = math.cos(-b.rot), math.sin(-b.rot)
    x, y = x - b.off[0], y - b.off[1]
    return (c * x - s * y, s * x + c * y)


def _pp(b, g1, g2):
    p, q = b.pt(g1, 3), b.pt(g2, 3)
    return math.hypot(p[0] - q[0], p[1] - q[1])


def _pl(b, g, line):
    p, a, q = b.pt(g, 3), b.pt(line, 1), b.pt(line, 2)
    dx, dy = q[0] - a[0], q[1] - a[1]
    n = math.hypot(dx, dy)
    return abs((p[0] - a[0]) * dy - (p[1] - a[1]) * dx) / n


def _slot_plate(b, rng):
    r = rng.uniform(3.1, 8.7)
    ls = rng.uniform(18, 42)
    cx, cy = rng.uniform(-6, 9), rng.uniform(-5, 7)
    l0 = b.line(cx, cy - r, cx + ls, cy - r)
    ar = b.arc(cx + ls, cy, r, -math.pi / 2, math.pi / 2)
    l1 = b.line(cx + ls, cy + r, cx, cy + r)
    al = b.arc(cx, cy, r, math.pi / 2, 3 * math.pi / 2)
    b.c("Tangent", l0, 2, ar, 1)
    b.c("Tangent", ar, 2, l1, 1)
    b.c("Tangent", l1, 2, al, 1)
    b.c("Tangent", al, 2, l0, 1)
    b.hv(l0, "H")
    b.hv(l1, "H")
    b.c("Radius", ar, r)
    b.c("Distance", al, 3, ar, 3, ls)
    b.place(al, 3)
    # a pair of equal holes beside the slot
    hr = rng.uniform(1.2, 2.8)
    hx, hy = cx + rng.uniform(4, ls - 4), cy + r + rng.uniform(9, 14)
    h1 = b.circle(hx, hy, hr)
    h2 = b.circle(hx + rng.uniform(9, 16), hy + rng.uniform(-2, 2.5), hr)
    b.c("Equal", h1, h2)
    b.c("Diameter", h1, 2 * hr)
    b.c("Distance", h1, 3, l1, _pl(b, h1, l1))
    b.c("Distance", h1, 3, ar, 3, _pp(b, h1, ar))
    b.c("Distance", h2, 3, h1, 3, _pp(b, h2, h1))
    # the second hole is placed by its distance to the first and its height
    b.c("Distance", h2, 3, l1, _pl(b, h2, l1))


def _polygon(b, rng):
    n = rng.randint(3, 8)
    big_r = rng.uniform(9, 31)
    phi = rng.uniform(0.05, 1.0)
    cx, cy = rng.uniform(-8, 10), rng.uniform(-7, 9)
    verts = [(cx + big_r * math.cos(phi + 2 * math.pi * k / n), cy + big_r * math.sin(phi + 2 * math.pi * k / n)) for k in range(n)]
    ids = [b.line(*verts[k], *verts[(k + 1) % n]) for k in range(n)]
    circ = b.circle(cx, cy, big_r, construction=True)
    for k in range(n):
        b.c("Coincident", ids[k], 2, ids[(k + 1) % n], 1)
    for k in range(1, n):
        b.c("Equal", ids[0], ids[k]) if k == 1 else b.c("Equal", ids[k - 1], ids[k])
    for k in range(n):
        b.c("PointOnObject", ids[k], 1, circ)
    b.c("Diameter", circ, 2 * big_r)
    b.place(circ, 3)
    b.c("Angle", ids[0], b.direction(ids[0]))


def _rounded_rect(b, rng):
    w, h, r = rng.uniform(40, 80), rng.uniform(26, 50), rng.uniform(3.2, 9.5)
    x0, y0 = rng.uniform(-6, 8), rng.uniform(-6, 8)
    hp = math.pi / 2
    bottom = b.line(x0 + r, y0, x0 + w - r, y0)
    abr = b.arc(x0 + w - r, y0 + r, r, -hp, 0.0)
    right = b.line(x0 + w, y0 + r, x0 + w, y0 + h - r)
    atr = b.arc(x0 + w - r, y0 + h - r, r, 0.0, hp)
    top = b.line(x0 + w - r, y0 + h, x0 + r, y0 + h)
    atl = b.arc(x0 + r, y0 + h - r, r, hp, 2 * hp)
    left = b.line(x0, y0 + h - r, x0, y0 + r)
    abl = b.arc(x0 + r, y0 + r, r, 2 * hp, 3 * hp)
    ring = [bottom, abr, right, atr, top, atl, left, abl]
    for k in range(8):
        a, c = ring[k], ring[(k + 1) % 8]
        b.c("Tangent", a, 2, c, 1)
    for g, k in ((bottom, "H"), (right, "V"), (top, "H"), (left, "V")):
        b.hv(g, k)
    b.c("Equal", abr, atr)
    b.c("Equal", atr, atl)
    b.c("Equal", atl, abl)
    b.c("Radius", abr, r)
    b.length_dim(bottom, "H")
    b.length_dim(right, "V")
    b.place(bottom, 1)


def _tangent_circles(b, rng):
    r1, r2, r3 = rng.uniform(4.2, 11), rng.uniform(4.2, 11), rng.uniform(3.1, 9)
    a = rng.uniform(8, 16)
    bx = a + 2 * math.sqrt(r1 * r2)
    c1, c2 = (a, r1), (bx, r2)
    d13, d23 = r1 + r3, r2 + r3
    d12 = math.hypot(c2[0] - c1[0], c2[1] - c1[1])
    t = (d13 * d13 - d23 * d23 + d12 * d12) / (2 * d12)
    hh = math.sqrt(max(d13 * d13 - t * t, 0.0))
    ux, uy = (c2[0] - c1[0]) / d12, (c2[1] - c1[1]) / d12
    c3 = (c1[0] + t * ux - hh * uy, c1[1] + t * uy + hh * ux)
    if c3[1] < max(r1, r2):
        c3 = (c1[0] + t * ux + hh * uy, c1[1] + t * uy - hh * ux)
    line = b.line(0.0, 0.0, bx + r2 + rng.uniform(3, 9), 0.0)
    k1, k2, k3 = b.circle(*c1, r1), b.circle(*c2, r2), b.circle(*c3, r3)
    b.c("Tangent", k1, line)
    b.c("Tangent", k2, line)
    b.c("Tangent", k1, k2)
    b.c("Tangent", k3, k1)
    b.c("Tangent", k3, k2)
    b.hv(line, "H")
    b.c("Diameter", k1, 2 * r1)
    b.c("Diameter", k2, 2 * r2)
    b.c("Diameter", k3, 2 * r3)
    b.length_dim(line, "H")
    b.place(line, 1)
    b.c("Distance", line, 1, k1, 3, _pp2(b, line, k1))


def _pp2(b, line, circ):
    p, q = b.pt(line, 1), b.pt(circ, 3)
    return math.hypot(p[0] - q[0], p[1] - q[1])


def _ellipse_plate(b, rng):
    a, bb = rng.uniform(14, 30), rng.uniform(6, 13)
    cx, cy = rng.uniform(-5, 8), rng.uniform(-5, 8)
    phi = rng.uniform(0.0, 0.9)
    x, y = b.T(cx, cy)
    ang = b.rot + phi
    s1 = V(x + a * math.cos(ang), y + a * math.sin(ang), 0)
    s2 = V(x - bb * math.sin(ang), y + bb * math.cos(ang), 0)
    e = b._add(Part.Ellipse(s1, s2, V(x, y, 0)), False)
    hole = b.circle(cx, cy, rng.uniform(1.5, 4.5))
    b.o.addGeometry(b.geo, False)
    for i, flag in enumerate(b.ctor):
        if flag:
            b.o.setConstruction(i, True)
    b.o.exposeInternalGeometry(e)
    n0 = len(b.geo)
    geoms = b.o.Geometry
    major = minor = None
    for i in range(n0, len(geoms)):
        g = geoms[i]
        if g.TypeId == "Part::GeomLineSegment":
            ln = g.length() if callable(getattr(g, "length", None)) else g.Length
            if major is None or ln > major[1]:
                if major is not None:
                    minor = major
                major = (i, ln)
            else:
                minor = (i, ln)
    b.o.addConstraint(b.cons)
    b.cons = []
    cons = []
    cons.append(Sketcher.Constraint("Coincident", hole, 3, e, 3))
    cons.append(Sketcher.Constraint("Diameter", hole, 2 * geoms[hole].Radius))
    cons.append(Sketcher.Constraint("DistanceX", e, 3, geoms[e].Center.x))
    cons.append(Sketcher.Constraint("DistanceY", e, 3, geoms[e].Center.y))
    cons.append(Sketcher.Constraint("Distance", major[0], major[1]))
    cons.append(Sketcher.Constraint("Distance", minor[0], minor[1]))
    mg = geoms[major[0]]
    cons.append(Sketcher.Constraint("Angle", major[0], math.atan2(mg.EndPoint.y - mg.StartPoint.y, mg.EndPoint.x - mg.StartPoint.x)))
    b.o.addConstraint(cons)
    b.o.solve()
    b.done = True


def _bspline(b, rng):
    n = rng.randint(4, 6)
    x = 0.0
    poles = []
    for k in range(n):
        x += rng.uniform(6, 14)
        poles.append(b.T(x, rng.uniform(-9, 9)))
    cur = Part.BSplineCurve()
    cur.buildFromPoles([V(p[0], p[1], 0) for p in poles], False, 3)
    i = b._add(cur, False)
    b.o.addGeometry(b.geo, False)
    b.o.exposeInternalGeometry(i)
    geoms = b.o.Geometry
    cons = []
    for k in range(i + 1, len(geoms)):
        g = geoms[k]
        if g.TypeId == "Part::GeomCircle":
            cons.append(Sketcher.Constraint("DistanceX", k, 3, g.Center.x))
            cons.append(Sketcher.Constraint("DistanceY", k, 3, g.Center.y))
    b.o.addConstraint(cons)
    b.o.solve()
    b.done = True


def _mirrored_house(b, rng):
    w, h1, h2 = rng.uniform(26, 52), rng.uniform(14, 30), rng.uniform(9, 22)
    ox = rng.uniform(-4, 7)
    y0 = rng.uniform(-4, 5)
    hw = w / 2
    base = b.line(ox - hw, y0, ox + hw, y0)
    wr = b.line(ox + hw, y0, ox + hw, y0 + h1)
    rr = b.line(ox + hw, y0 + h1, ox, y0 + h1 + h2)
    rl = b.line(ox, y0 + h1 + h2, ox - hw, y0 + h1)
    wl = b.line(ox - hw, y0 + h1, ox - hw, y0)
    ring = [base, wr, rr, rl, wl]
    for k in range(5):
        b.c("Coincident", ring[k], 2, ring[(k + 1) % 5], 1)
    b.hv(base, "H")
    b.hv(wr, "V")
    axis = b.line(ox, y0, ox, y0 + h1 + h2, construction=True)
    b.c("Coincident", axis, 2, rr, 2)
    b.c("PointOnObject", axis, 1, base)
    b.c("Symmetric", base, 1, base, 2, axis)
    b.c("Symmetric", wr, 2, wl, 1, axis)
    b.length_dim(base, "H")
    b.length_dim(wr, "V")
    b.c("Distance", axis, b.length(axis))
    b.place(axis, 1)


def _stepped_bracket(b, rng):
    n_steps = rng.randint(1, 3)
    w = [rng.uniform(14, 26)]
    hts = [rng.uniform(10, 20)]
    for _ in range(n_steps):
        w.append(w[-1] + rng.uniform(8, 18))
        hts.append(hts[-1] + rng.uniform(7, 15))
    # staircase: bottom edge to the right, steps up, back along the top-left
    pts = [(0.0, 0.0), (w[-1], 0.0)]
    for k in range(n_steps, -1, -1):
        pass
    xs = [w[-1]] + [w[k] for k in range(n_steps - 1, -1, -1)]
    ys = [0.0] + [hts[k] for k in range(n_steps)]
    pts = [(0.0, 0.0)]
    pts.append((w[-1], 0.0))
    prev = (w[-1], 0.0)
    for k in range(n_steps):
        pts.append((prev[0], hts[k]))
        pts.append((w[n_steps - 1 - k], hts[k]))
        prev = (w[n_steps - 1 - k], hts[k])
    pts.append((prev[0], hts[-1]))
    pts.append((0.0, hts[-1]))
    n = len(pts)
    ox, oy = rng.uniform(-6, 6), rng.uniform(-5, 5)
    pts = [(p[0] + ox, p[1] + oy) for p in pts]
    ids = [b.line(*pts[k], *pts[(k + 1) % n]) for k in range(n)]
    for k in range(n):
        b.c("Coincident", ids[k], 2, ids[(k + 1) % n], 1)
    kinds = []
    for k in range(n):
        p, q = pts[k], pts[(k + 1) % n]
        kinds.append("H" if abs(p[1] - q[1]) < 1e-9 else "V")
        b.hv(ids[k], kinds[-1])
    for k in range(n - 2):
        b.length_dim(ids[k], kinds[k])
    b.place(ids[0], 1)
    hr = rng.uniform(1.4, 3.3)
    hx, hy = pts[0][0] + 5.5 + rng.uniform(0, 2), pts[0][1] + 4.5 + rng.uniform(0, 2)
    lx, ly = _local(b, ids[0])
    hole = b.circle(lx + 5.5 + (hx - pts[0][0] - 5.5), ly + 4.5 + (hy - pts[0][1] - 4.5), hr)
    b.c("Diameter", hole, 2 * hr)
    b.c("Distance", hole, 3, ids[0], _pl(b, hole, ids[0]))
    b.c("Distance", hole, 3, ids[-1], _pl(b, hole, ids[-1]))


def _parallelogram(b, rng):
    l0, l1 = rng.uniform(32, 60), rng.uniform(16, 30)
    th = rng.uniform(0.9, 1.9)
    p0 = (rng.uniform(-5, 8), rng.uniform(-5, 8))
    p1 = (p0[0] + l0, p0[1])
    p3 = (p0[0] + l1 * math.cos(th), p0[1] + l1 * math.sin(th))
    p2 = (p1[0] + p3[0] - p0[0], p1[1] + p3[1] - p0[1])
    pts = [p0, p1, p2, p3]
    ids = [b.line(*pts[k], *pts[(k + 1) % 4]) for k in range(4)]
    for k in range(4):
        b.c("Coincident", ids[k], 2, ids[(k + 1) % 4], 1)
    b.hv(ids[0], "H")
    b.c("Parallel", ids[0], ids[2])
    b.c("Parallel", ids[1], ids[3])
    b.length_dim(ids[0], "H")
    b.c("Distance", ids[1], b.length(ids[1]))
    d0, d1 = b.direction(ids[0]), b.direction(ids[3])
    b.c("Angle", ids[0], ids[3], math.atan2(math.sin(d1 - d0), math.cos(d1 - d0)))
    b.place(ids[0], 1)
    # a diagonal construction line with its mid point on the centre hole
    hr = rng.uniform(2.0, 4.8)
    cxu = (pts[0][0] + pts[2][0]) / 2
    cyu = (pts[0][1] + pts[2][1]) / 2
    lx0, ly0 = _local(b, ids[0])
    hole = b.circle(lx0 + (cxu - p0[0]), ly0 + (cyu - p0[1]), hr)
    diag = b.line(*[lx0, ly0], *[lx0 + (pts[2][0] - p0[0]), ly0 + (pts[2][1] - p0[1])], construction=True)
    b.c("Coincident", diag, 1, ids[0], 1)
    b.c("Coincident", diag, 2, ids[1], 2)
    b.c("Diameter", hole, 2 * hr)
    b.c("Symmetric", ids[0], 1, ids[2], 1, hole, 3)


def _flange(b, rng):
    cx, cy = rng.uniform(-6, 8), rng.uniform(-6, 8)
    ro, ri = rng.uniform(31, 48), rng.uniform(10, 17)
    rb, rh = rng.uniform(19, ro - 8), rng.uniform(2.2, 4.8)
    th = rng.uniform(0.1, 1.3)
    outer = b.circle(cx, cy, ro)
    inner = b.circle(cx, cy, ri)
    pos = [(cx + rb * math.cos(th + k * math.pi / 2), cy + rb * math.sin(th + k * math.pi / 2)) for k in range(4)]
    holes = [b.circle(*b_local(b, p), rh) for p in pos]
    la = b.line(*b_local(b, pos[0]), *b_local(b, pos[2]), construction=True)
    lb = b.line(*b_local(b, pos[1]), *b_local(b, pos[3]), construction=True)
    b.c("Coincident", inner, 3, outer, 3)
    b.c("Coincident", la, 1, holes[0], 3)
    b.c("Coincident", la, 2, holes[2], 3)
    b.c("Coincident", lb, 1, holes[1], 3)
    b.c("Coincident", lb, 2, holes[3], 3)
    b.c("Symmetric", holes[0], 3, holes[2], 3, outer, 3)
    b.c("Symmetric", holes[1], 3, holes[3], 3, outer, 3)
    b.c("Perpendicular", la, lb)
    b.c("Equal", la, lb)
    for k in (1, 2, 3):
        b.c("Equal", holes[0], holes[k])
    b.c("Diameter", holes[0], 2 * rh)
    b.c("Diameter", outer, 2 * ro)
    b.c("Diameter", inner, 2 * ri)
    b.c("Distance", la, 2 * rb)
    b.c("Angle", la, b.direction(la))
    b.place(outer, 3)


def b_local(b, p):
    c, s = math.cos(-b.rot), math.sin(-b.rot)
    x, y = p[0] - b.off[0], p[1] - b.off[1]
    return (c * x - s * y, s * x + c * y)


def _arch(b, rng):
    w, h = rng.uniform(26, 56), rng.uniform(14, 36)
    x0, y0 = rng.uniform(-6, 7), rng.uniform(-5, 7)
    r = w / 2
    bottom = b.line(x0, y0, x0 + w, y0)
    right = b.line(x0 + w, y0, x0 + w, y0 + h)
    top = b.arc(x0 + r, y0 + h, r, 0.0, math.pi)
    left = b.line(x0, y0 + h, x0, y0)
    b.c("Coincident", bottom, 2, right, 1)
    b.c("Tangent", right, 2, top, 1)
    b.c("Tangent", top, 2, left, 1)
    b.c("Coincident", left, 2, bottom, 1)
    b.hv(bottom, "H")
    b.hv(right, "V")
    b.hv(left, "V")
    b.length_dim(bottom, "H")
    b.length_dim(right, "V")
    b.place(bottom, 1)
    # a window: a small circle on the arch's axis of symmetry, drawn on a construction line
    hr = rng.uniform(2.1, min(5.5, r - 3))
    axis = b.line(x0 + r, y0, x0 + r, y0 + h, construction=True)
    wnd = b.circle(x0 + r, y0 + h * rng.uniform(0.35, 0.7), hr)
    b.c("PointOnObject", axis, 1, bottom)
    b.c("Coincident", axis, 2, top, 3)
    b.hv(axis, "V")
    b.c("PointOnObject", wnd, 3, axis)
    b.c("Diameter", wnd, 2 * hr)
    b.c("Distance", wnd, 3, bottom, _pl(b, wnd, bottom))


FAMILIES = {
    "plate_holes": _plate_holes,
    "slot_plate": _slot_plate,
    "polygon": _polygon,
    "rounded_rect": _rounded_rect,
    "tangent_circles": _tangent_circles,
    "ellipse_plate": _ellipse_plate,
    "bspline": _bspline,
    "mirrored_house": _mirrored_house,
    "stepped_bracket": _stepped_bracket,
    "parallelogram": _parallelogram,
    "flange": _flange,
    "arch": _arch,
}


def build_generated(doc, family, rotated, variant):
    rng = random.Random("autodefine-%s-%d-%d" % (family, rotated, variant))
    rot = rng.uniform(0.15, 1.3) if rotated else 0.0
    off = (rng.uniform(-8, 12), rng.uniform(-8, 12)) if family in ("ellipse_plate", "bspline") else (0.0, 0.0)
    b = _B(doc, "%s_%s_%d" % (family, "rot" if rotated else "std", variant), rot, off)
    b.done = False
    FAMILIES[family](b, rng)
    if b.done:
        return b.o
    return b.finish()


def generated(doc, variants=9, families=None, rotations=(False, True), first=0):
    """Build the generated cases.  Returns Case objects whose sketches are
    verified to be fully constrained (an assertion error names a family whose
    builder is wrong)."""
    out = []
    for family in families or FAMILIES:
        for rotated in rotations:
            for v in range(first, first + variants):
                sk = build_generated(doc, family, rotated, v)
                assert sk.solve() == 0 and sk.DoF == 0, (sk.Name, sk.solve(), sk.DoF, sk.ConflictingConstraints, sk.RedundantConstraints)
                out.append(Case(sk.Name, family, sk, doc, real=False, rotated=bool(rotated)))
    return out


def scale_sketch(doc, n_geometry, name=None, seed=1):
    """A geometry-only sketch with about `n_geometry` elements for timing: a
    grid of cells, each a rectangle of four lines and a hole, all sizes
    different (seeded).  Nothing is constrained, so this is the stripped
    state; the relations to find are the coincident corners, the horizontal
    and vertical sides and the points on lines."""
    rng = random.Random("scale-%d-%d" % (n_geometry, seed))
    o = doc.addObject("Sketcher::SketchObject", name or "Scale%d" % n_geometry)
    cells = max(1, n_geometry // 5)
    cols = max(1, int(math.sqrt(cells)))
    geo = []
    for k in range(cells):
        cx, cy = (k % cols) * 70.0 + rng.uniform(-3, 3), (k // cols) * 55.0 + rng.uniform(-3, 3)
        w, h, r = rng.uniform(30, 60), rng.uniform(25, 45), rng.uniform(2, 6)
        pts = [(cx, cy), (cx + w, cy), (cx + w, cy + h), (cx, cy + h)]
        for i in range(4):
            a, b = pts[i], pts[(i + 1) % 4]
            geo.append(Part.LineSegment(V(a[0], a[1], 0), V(b[0], b[1], 0)))
        geo.append(Part.Circle(V(cx + w * rng.uniform(0.3, 0.7), cy + h * rng.uniform(0.3, 0.7), 0), V(0, 0, 1), r))
    o.addGeometry(geo, False)
    return o


# ---------------------------------------------------------------------------
# Real sketches
# ---------------------------------------------------------------------------


def real_cases(doc_cache=None, names=None):
    """Open the frozen real sketches.  Returns (cases, missing)."""
    from SketcherTests import SketchDiagnosisCorpus as Slice1

    doc_cache = doc_cache if doc_cache is not None else {}
    out, missing = [], []
    for rel, name in REAL_SKETCHES:
        label = "%s:%s" % (rel, name)
        if names is not None and label not in names:
            continue
        path = Slice1.find_data_file(rel)
        if path is None:
            missing.append(label)
            continue
        if path not in doc_cache:
            doc_cache[path] = FreeCAD.openDocument(path, True)
        sk = doc_cache[path].getObject(name)
        if sk is None:
            missing.append(label)
            continue
        out.append(Case(label, "real", sk, doc_cache[path], real=True))
    return out, missing


def slice1_cases(doc):
    """The fully constrained cases of slice 1's corpus."""
    from SketcherTests import SketchDiagnosisCorpus as Slice1

    cases = []
    Slice1._fully_constrained(doc, cases)
    return [Case("s1_" + c.name, "slice1", c.sketch, doc) for c in cases]


_REAL_TABLE = """
examples/BIMExample.FCStd Sketch
examples/BIMExample.FCStd Sketch001
examples/BIMExample.FCStd Sketch002
examples/BIMExample.FCStd Sketch003
examples/BIMExample.FCStd Sketch004
examples/BIMExample.FCStd Sketch005
examples/BIMExample.FCStd Sketch006
examples/BIMExample.FCStd Sketch007
examples/BIMExample.FCStd Sketch008
examples/BIMExample.FCStd Sketch009
examples/BIMExample.FCStd Sketch010
examples/BIMExample.FCStd Sketch011
examples/PartDesignExample.FCStd Sketch
examples/PartDesignExample.FCStd Sketch001
examples/PartDesignExample.FCStd Sketch002
examples/PartDesignExample.FCStd Sketch003
examples/PartDesignExample.FCStd Sketch004
examples/PartDesignExample.FCStd Sketch005
examples/PartDesignExample.FCStd Sketch006
tests/PadTest.fcstd Sketch
tests/PocketTest.fcstd Sketch
Mod/BIM/bimtests/test_structure_column_flip_v1_0_v1_1.FCStd Sketch
Mod/BIM/bimtests/test_structure_column_flip_v1_0_v1_1.FCStd Sketch001
Mod/BIM/bimtests/test_structure_column_flip_v1_0_v1_1.FCStd Sketch002
Mod/BIM/bimtests/test_structure_column_flip_v1_0_v1_1.FCStd Sketch003
Mod/CAM/CAMTests/Fixtures/test_17748_cam_profile.FCStd Sketch
Mod/CAM/CAMTests/Fixtures/test_28534_truncated_pocket.FCStd Sketch019
Mod/CAM/CAMTests/Tools/Shape/test-path-tool-bit-shape-00.fcstd Sketch
Mod/CAM/CAMTests/test_geomop.fcstd Sketch
Mod/CAM/CAMTests/test_geomop.fcstd Sketch002
Mod/CAM/CAMTests/test_geomop.fcstd Sketch003
Mod/CAM/CAMTests/test_geomop.fcstd Sketch005
Mod/CAM/CAMTests/test_geomop.fcstd Sketch006
Mod/CAM/CAMTests/test_geomop.fcstd Sketch007
Mod/CAM/CAMTests/test_holes00.fcstd Sketch
Mod/CAM/CAMTests/test_profile.fcstd Sketch
Mod/CAM/DemoParts/hole_puzzle.fcstd Sketch
Mod/CAM/DemoParts/hole_puzzle.fcstd Sketch001
Mod/CAM/DemoParts/hole_puzzle.fcstd Sketch003
Mod/CAM/DemoParts/hole_puzzle.fcstd Sketch004
Mod/CAM/DemoParts/motor_mount_inch.fcstd Sketch001
Mod/CAM/DemoParts/motor_mount_inch.fcstd Sketch002
Mod/CAM/Tools/Shape/ballend.fcstd Sketch
Mod/CAM/Tools/Shape/bullnose.fcstd Sketch
Mod/CAM/Tools/Shape/chamfer.fcstd Sketch
Mod/CAM/Tools/Shape/dovetail.fcstd Sketch
Mod/CAM/Tools/Shape/drill.fcstd Sketch
Mod/CAM/Tools/Shape/endmill.fcstd Sketch
Mod/CAM/Tools/Shape/probe.fcstd Sketch
Mod/CAM/Tools/Shape/radius.fcstd Sketch
Mod/CAM/Tools/Shape/reamer.fcstd Sketch
Mod/CAM/Tools/Shape/slittingsaw.fcstd Sketch
Mod/CAM/Tools/Shape/tap.fcstd Sketch
Mod/CAM/Tools/Shape/taperedballnose.fcstd Sketch
Mod/CAM/Tools/Shape/thread-mill.fcstd Sketch
Mod/CAM/Tools/Shape/v-bit.fcstd Sketch
Mod/Part/parttests/TestTangentMode3-0.21.FCStd Sketch001
Mod/Part/parttests/TestTangentMode3-0.21.FCStd Sketch002
Mod/Part/parttests/TestTangentMode3-0.21.FCStd Sketch003
Mod/Sketcher/SketcherTests/TestSketchCarbonCopyReverseMapping.FCStd Sketch
Mod/Sketcher/SketcherTests/TestSketchCarbonCopyReverseMapping.FCStd Sketch001
"""
for _line in _REAL_TABLE.strip().splitlines():
    if _line.strip():
        _rel, _name = _line.rsplit(" ", 1)
        REAL_SKETCHES.append((_rel, _name))
