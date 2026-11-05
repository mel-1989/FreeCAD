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

"""Deterministic sketch corpus for the Sketcher diagnosis tests and benchmark.

``build_corpus(doc)`` builds every case into ``doc`` and returns a list of
``Case`` objects.  Each case carries its analytic truth in ``case.truth``:

``dof``           hand-counted degrees of freedom (None when not hand-counted)
``points``        {(geo, pos): (x, y)} solved coordinates of a fully
                  constrained sketch
``conflict_sets`` list of exact minimal conflict sets (0-based constraint
                  indices), or None when the case is conflicting without a
                  hand-derived set list
``conflicting``   True when the case is over-constrained
``culprit``       index of the planted culprit constraint, or None
``red_groups``    exact redundancy groups, ``red_removals`` number of
                  constraints that must go, ``redundant`` True when the case
                  is redundant
``reference``     indices of non-driving (reference) constraints
``inactive``      indices of inactive constraints

Run as a script (``FreeCADCmd SketchDiagnosisCorpus.py``) it builds the
corpus and prints one line per case.
"""

import math
import os

import FreeCAD
import Part
import Sketcher

V = FreeCAD.Vector
DEG = math.pi / 180.0


class Case:
    def __init__(self, name, cls, sketch, truth, doc=None, real=False, scale=None, builder=None):
        self.name = name
        self.cls = cls
        self.sketch = sketch
        self.truth = truth
        self.doc = doc
        self.real = real
        self.scale = scale
        # optional ``builder(doc) -> sketch`` that rebuilds the sketch from
        # scratch, for oracles that need fresh solver state for every probe
        self.builder = builder

    def __repr__(self):
        return "<Case %s/%s>" % (self.cls, self.name)


class _Sk:
    """Small helper around a SketchObject.  With ``bulk=True`` geometry and
    constraints are buffered and added in one call by ``finish()``, which
    avoids one solver run per addition on large sketches."""

    def __init__(self, doc, name, bulk=False):
        self.doc = doc
        self.o = doc.addObject("Sketcher::SketchObject", name)
        self.bulk = bulk
        self._geo = []
        self._cons = []
        self._ngeo = 0
        self._ncons = 0

    def _add_geo(self, geom, construction):
        if self.bulk:
            self._geo.append(geom)
            self._ngeo += 1
            return self._ngeo - 1
        return self.o.addGeometry(geom, construction)

    def line(self, x1, y1, x2, y2, construction=False):
        return self._add_geo(Part.LineSegment(V(x1, y1, 0), V(x2, y2, 0)), construction)

    def point(self, x, y, construction=False):
        return self._add_geo(Part.Point(V(x, y, 0)), construction)

    def circle(self, x, y, r, construction=False):
        return self._add_geo(Part.Circle(V(x, y, 0), V(0, 0, 1), r), construction)

    def arc(self, x, y, r, a0, a1, construction=False):
        c = Part.Circle(V(x, y, 0), V(0, 0, 1), r)
        return self._add_geo(Part.ArcOfCircle(c, a0, a1), construction)

    def c(self, *args, name=None):
        con = Sketcher.Constraint(*args)
        if self.bulk:
            self._cons.append(con)
            self._ncons += 1
            return self._ncons - 1
        i = self.o.addConstraint(con)
        if name:
            self.o.renameConstraint(i, name)
        return i

    def finish(self):
        if self.bulk:
            if self._geo:
                self.o.addGeometry(self._geo, False)
            if self._cons:
                self.o.addConstraint(self._cons)
            self._geo, self._cons = [], []
        return self.o

    def rect(self, x0, y0, w, h, dx=0.0, dy=0.0):
        """Four lines (bottom, right, top, left) with coincident corners."""
        pts = [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)]
        ids = []
        for i in range(4):
            a, b = pts[i], pts[(i + 1) % 4]
            ids.append(self.line(a[0] + dx, a[1] + dy, b[0], b[1]))
        cons = [self.c("Coincident", ids[i], 2, ids[(i + 1) % 4], 1) for i in range(4)]
        return ids, cons


def _truth(**kw):
    t = {
        "dof": None,
        "points": None,
        "conflict_sets": None,
        "conflicting": False,
        "limit": False,
        "culprit": None,
        "red_groups": None,
        "red_removals": None,
        "redundant": False,
        "reference": [],
        "inactive": [],
    }
    t.update(kw)
    return t


def _settle(doc, sk):
    sk.o.solve()
    return sk


# ---------------------------------------------------------------------------
# A. Fully constrained, known coordinates
# ---------------------------------------------------------------------------


def _fully_constrained(doc, out):
    def add(name, sk, **truth):
        out.append(Case(name, "full", sk.o, _truth(dof=0, **truth), doc))

    # A1 triangle from three sides (3-4-5)
    s = _Sk(doc, "tri345")
    a = s.line(0.1, 0.1, 3.2, -0.1)
    b = s.line(3.2, -0.1, 0.2, 3.7)
    c = s.line(0.2, 3.7, 0.1, 0.1)
    s.c("Coincident", a, 2, b, 1)
    s.c("Coincident", b, 2, c, 1)
    s.c("Coincident", c, 2, a, 1)
    s.c("DistanceX", -1, 1, a, 1, 0.0)
    s.c("DistanceY", -1, 1, a, 1, 0.0)
    s.c("Horizontal", a)
    s.c("Distance", a, 3.0)
    s.c("Distance", b, 5.0)
    s.c("Distance", c, 4.0)
    add("triangle_3_4_5", s, points={(a, 1): (0, 0), (a, 2): (3, 0), (b, 2): (0, 4)})

    # A2 tangent circle chain
    s = _Sk(doc, "tangent_chain")
    c1 = s.circle(0.1, -0.1, 2.1)
    c2 = s.circle(3.2, 0.2, 0.9)
    c3 = s.circle(4.3, -0.1, 0.6)
    s.c("DistanceX", -1, 1, c1, 3, 0.0)
    s.c("DistanceY", -1, 1, c1, 3, 0.0)
    s.c("Radius", c1, 2.0)
    s.c("Radius", c2, 1.0)
    s.c("Radius", c3, 0.5)
    s.c("Tangent", c1, c2)
    s.c("Tangent", c2, c3)
    s.c("PointOnObject", c2, 3, -1)
    s.c("PointOnObject", c3, 3, -1)
    add("tangent_circle_chain", s, points={(c1, 3): (0, 0), (c2, 3): (3, 0), (c3, 3): (4.5, 0)})

    # A3 slot: two arcs and two lines, centre distance 10, radius 2
    s = _Sk(doc, "slot")
    al = s.arc(0.1, 0.0, 2.0, math.pi / 2, 3 * math.pi / 2)
    ar = s.arc(10.2, 0.1, 2.0, -math.pi / 2, math.pi / 2)
    lt = s.line(0.0, 2.0, 10.2, 2.1)
    lb = s.line(10.2, -1.9, 0.0, -2.0)
    # arc points: pos 1 start, 2 end, 3 centre
    s.c("Tangent", al, 2, lb, 2)
    s.c("Tangent", lb, 1, ar, 1)
    s.c("Tangent", ar, 2, lt, 2)
    s.c("Tangent", lt, 1, al, 1)
    s.c("DistanceX", -1, 1, al, 3, 0.0)
    s.c("DistanceY", -1, 1, al, 3, 0.0)
    s.c("Radius", al, 2.0)
    s.c("Equal", al, ar)
    s.c("DistanceX", al, 3, ar, 3, 10.0)
    s.c("Horizontal", al, 3, ar, 3)
    add(
        "slot",
        s,
        points={(al, 3): (0, 0), (ar, 3): (10, 0), (lt, 1): (0, 2), (lt, 2): (10, 2)},
    )

    # A4 regular hexagon from one dimension (circumradius 5)
    s = _Sk(doc, "hexagon")
    r = 5.0
    vs = [(r * math.cos(k * math.pi / 3), r * math.sin(k * math.pi / 3)) for k in range(6)]
    lines = []
    for k in range(6):
        p, q = vs[k], vs[(k + 1) % 6]
        lines.append(s.line(p[0] + 0.15, p[1] - 0.1, q[0] - 0.1, q[1] + 0.1))
    cc = s.circle(0.1, 0.1, 5.2, True)
    for k in range(6):
        s.c("Coincident", lines[k], 2, lines[(k + 1) % 6], 1)
    s.c("Coincident", cc, 3, -1, 1)
    s.c("Radius", cc, r)
    for k in range(6):
        s.c("PointOnObject", lines[k], 1, cc)
    for k in range(5):
        s.c("Equal", lines[k], lines[k + 1])
    s.c("PointOnObject", lines[0], 1, -1)
    add("hexagon_from_radius", s, points={(lines[k], 1): vs[k] for k in range(6)})

    # A5 rectangle 10 x 5 at the origin
    s = _Sk(doc, "rect10x5")
    ids, _ = s.rect(0, 0, 10, 5, 0.1, -0.1)
    for i in (0, 2):
        s.c("Horizontal", ids[i])
    for i in (1, 3):
        s.c("Vertical", ids[i])
    s.c("DistanceX", ids[0], 1, ids[0], 2, 10.0)
    s.c("DistanceY", ids[1], 1, ids[1], 2, 5.0)
    s.c("DistanceX", -1, 1, ids[0], 1, 0.0)
    s.c("DistanceY", -1, 1, ids[0], 1, 0.0)
    add("rectangle_10x5", s, points={(ids[0], 1): (0, 0), (ids[2], 1): (10, 5), (ids[3], 1): (0, 5)})

    # A6 ellipse with axis lengths 8 and 4
    s = _Sk(doc, "ellipse")
    e = s.o.addGeometry(Part.Ellipse(V(0.1, 0.1, 0), 4.2, 1.9), False)
    s.o.exposeInternalGeometry(e)
    facades = s.o.GeometryFacadeList
    major = minor = None
    for i, g in enumerate(facades):
        t = str(g.InternalType)
        if t == "EllipseMajorDiameter":
            major = i
        elif t == "EllipseMinorDiameter":
            minor = i
    s.c("DistanceX", -1, 1, e, 3, 0.0)
    s.c("DistanceY", -1, 1, e, 3, 0.0)
    s.c("Horizontal", major)
    s.c("Distance", major, 8.0)
    s.c("Distance", minor, 4.0)
    add("ellipse_8x4", s, points={(e, 3): (0, 0)})

    # A7 quarter arc
    s = _Sk(doc, "quarter_arc")
    a = s.arc(0.1, -0.1, 3.2, 0.05, math.pi / 2 - 0.05)
    s.c("DistanceX", -1, 1, a, 3, 0.0)
    s.c("DistanceY", -1, 1, a, 3, 0.0)
    s.c("Radius", a, 3.0)
    s.c("PointOnObject", a, 1, -1)
    s.c("DistanceY", -1, 1, a, 2, 3.0)
    add("quarter_arc", s, points={(a, 1): (3, 0), (a, 2): (0, 3), (a, 3): (0, 0)})

    # A8 B-spline: four poles with position fixed (exposing the internal geometry adds the weight constraints)
    s = _Sk(doc, "bspline")
    poles = [V(0.1, 0, 0), V(2, 3.2, 0), V(5.1, 2.9, 0), V(7, 0.1, 0)]
    bs = Part.BSplineCurve()
    bs.buildFromPolesMultsKnots(poles, [4, 4], [0.0, 1.0], False, 3)
    b = s.o.addGeometry(bs, False)
    s.o.exposeInternalGeometry(b)
    pole_circles = [
        i
        for i, g in enumerate(s.o.GeometryFacadeList)
        if str(g.InternalType) == "BSplineControlPoint"
    ]
    want = [(0, 0), (2, 3), (5, 3), (7, 0)]
    for pc, (x, y) in zip(pole_circles, want):
        s.c("DistanceX", -1, 1, pc, 3, float(x))
        s.c("DistanceY", -1, 1, pc, 3, float(y))
    pts = {(pc, 3): w for pc, w in zip(pole_circles, want)}
    add("bspline_four_poles", s, points=pts)

    # A9 L-shaped profile
    s = _Sk(doc, "lshape")
    P = [(0, 0), (6, 0), (6, 2), (2, 2), (2, 5), (0, 5)]
    ls = []
    for k in range(6):
        p, q = P[k], P[(k + 1) % 6]
        ls.append(s.line(p[0] + 0.1, p[1] - 0.1, q[0] - 0.1, q[1] + 0.1))
    for k in range(6):
        s.c("Coincident", ls[k], 2, ls[(k + 1) % 6], 1)
    for k in range(6):
        s.c("Horizontal" if k % 2 == 0 else "Vertical", ls[k])
    s.c("DistanceX", -1, 1, ls[0], 1, 0.0)
    s.c("DistanceY", -1, 1, ls[0], 1, 0.0)
    s.c("Distance", ls[0], 6.0)
    s.c("Distance", ls[1], 2.0)
    s.c("Distance", ls[2], 4.0)
    s.c("Distance", ls[3], 3.0)
    add("l_shape", s, points={(ls[k], 1): P[k] for k in range(6)})

    # A10 square by Equal
    s = _Sk(doc, "square_equal")
    ids, _ = s.rect(0, 0, 4, 4, 0.1, 0.05)
    for i in (0, 2):
        s.c("Horizontal", ids[i])
    for i in (1, 3):
        s.c("Vertical", ids[i])
    s.c("Equal", ids[0], ids[1])
    s.c("Distance", ids[0], 4.0)
    s.c("DistanceX", -1, 1, ids[0], 1, 0.0)
    s.c("DistanceY", -1, 1, ids[0], 1, 0.0)
    add("square_by_equal", s, points={(ids[2], 1): (4, 4), (ids[3], 1): (0, 4)})

    # A11 circle centred in a rectangle by a symmetric constraint
    s = _Sk(doc, "circle_in_rect")
    ids, _ = s.rect(0, 0, 10, 5, 0.1, 0.0)
    for i in (0, 2):
        s.c("Horizontal", ids[i])
    for i in (1, 3):
        s.c("Vertical", ids[i])
    s.c("DistanceX", ids[0], 1, ids[0], 2, 10.0)
    s.c("DistanceY", ids[1], 1, ids[1], 2, 5.0)
    s.c("DistanceX", -1, 1, ids[0], 1, 0.0)
    s.c("DistanceY", -1, 1, ids[0], 1, 0.0)
    ci = s.circle(4.8, 2.6, 0.9)
    s.c("Symmetric", ids[0], 1, ids[2], 1, ci, 3)
    s.c("Radius", ci, 1.0)
    add("circle_centred_in_rectangle", s, points={(ci, 3): (5, 2.5)})

    # A12 two lines at 30 degrees
    s = _Sk(doc, "angle30")
    l0 = s.line(0.1, 0.1, 4.1, 0.0)
    l1 = s.line(0.1, 0.1, 2.5, 1.4)
    s.c("Coincident", l0, 1, l1, 1)
    s.c("DistanceX", -1, 1, l0, 1, 0.0)
    s.c("DistanceY", -1, 1, l0, 1, 0.0)
    s.c("Horizontal", l0)
    s.c("Distance", l0, 4.0)
    s.c("Distance", l1, 3.0)
    s.c("Angle", l0, 1, l1, 1, 30 * DEG)
    add("angle_30", s, points={(l1, 2): (3 * math.cos(30 * DEG), 3 * math.sin(30 * DEG))})


# ---------------------------------------------------------------------------
# B. Under-constrained, hand-counted DOF
# ---------------------------------------------------------------------------


def _under_constrained(doc, out):
    def add(name, sk, dof, **kw):
        out.append(Case(name, "under", sk.o, _truth(dof=dof, **kw), doc))

    s = _Sk(doc, "free_line")
    s.line(0, 0, 3, 1)
    add("free_line", s, 4)

    s = _Sk(doc, "free_circle")
    s.circle(1, 1, 2)
    add("free_circle", s, 3)

    s = _Sk(doc, "free_arc")
    s.arc(0, 0, 2, 0.2, 2.0)
    add("free_arc", s, 5)

    s = _Sk(doc, "horizontal_line")
    a = s.line(0, 0, 3, 0.1)
    s.c("Horizontal", a)
    add("horizontal_line", s, 3)

    s = _Sk(doc, "two_joined_lines")
    a = s.line(0, 0, 3, 0)
    b = s.line(3, 0, 3, 3)
    s.c("Coincident", a, 2, b, 1)
    add("two_joined_lines", s, 6)

    s = _Sk(doc, "loose_quad")
    ids, _ = s.rect(0, 0, 4, 3)
    add("loose_quad", s, 8)

    s = _Sk(doc, "quad_hv")
    ids, _ = s.rect(0, 0, 4, 3)
    for i in (0, 2):
        s.c("Horizontal", ids[i])
    for i in (1, 3):
        s.c("Vertical", ids[i])
    add("quad_horizontal_vertical", s, 4)

    s = _Sk(doc, "rect_dims")
    ids, _ = s.rect(0, 0, 4, 3)
    for i in (0, 2):
        s.c("Horizontal", ids[i])
    for i in (1, 3):
        s.c("Vertical", ids[i])
    s.c("DistanceX", ids[0], 1, ids[0], 2, 4.0)
    s.c("DistanceY", ids[1], 1, ids[1], 2, 3.0)
    add("rectangle_floating", s, 2)

    s = _Sk(doc, "circle_origin")
    c = s.circle(0.1, 0.1, 2)
    s.c("Coincident", c, 3, -1, 1)
    add("circle_at_origin", s, 1)

    s = _Sk(doc, "concentric")
    a = s.circle(0, 0, 2)
    b = s.circle(0.1, 0.1, 1)
    s.c("Coincident", a, 3, b, 3)
    add("concentric_circles", s, 4)

    s = _Sk(doc, "point")
    s.point(1, 2)
    add("free_point", s, 2)

    s = _Sk(doc, "construction_pair")
    a = s.line(0, 0, 4, 0)
    b = s.line(0, 1, 4, 1.2, True)
    s.c("Parallel", a, b)
    add("construction_parallel", s, 7)

    s = _Sk(doc, "ellipse_free")
    e = s.o.addGeometry(Part.Ellipse(V(0, 0, 0), 4, 2), False)
    s.o.exposeInternalGeometry(e)
    add("free_ellipse", s, 5)

    s = _Sk(doc, "tri_two_sides")
    a = s.line(0, 0, 3, 0)
    b = s.line(3, 0, 0, 4)
    c = s.line(0, 4, 0, 0)
    s.c("Coincident", a, 2, b, 1)
    s.c("Coincident", b, 2, c, 1)
    s.c("Coincident", c, 2, a, 1)
    s.c("Distance", a, 3.0)
    s.c("Distance", c, 4.0)
    add("triangle_two_sides", s, 4)


# ---------------------------------------------------------------------------
# C. Over-constrained, planted conflicts
# ---------------------------------------------------------------------------


def _conflicting(doc, out):
    def add(name, sk, sets, culprit, **kw):
        out.append(
            Case(
                name,
                "conflict",
                sk.o,
                _truth(conflicting=True, conflict_sets=sets, culprit=culprit, **kw),
                doc,
            )
        )

    # C1 single conflict
    s = _Sk(doc, "c_single")
    a = s.line(0, 0, 5, 0)
    s.c("Horizontal", a)
    s.c("DistanceX", -1, 1, a, 1, 0.0)
    s.c("DistanceY", -1, 1, a, 1, 0.0)
    s.c("DistanceX", a, 1, a, 2, 5.0)
    s.c("DistanceX", a, 1, a, 2, 6.0)
    add("single_conflict", s, [[3, 4]], 4)

    # C2 two independent conflicts
    s = _Sk(doc, "c_independent")
    a = s.line(0, 0, 5, 0)
    b = s.line(0, 4, 0, 7)
    s.c("DistanceX", a, 1, a, 2, 5.0)
    s.c("DistanceY", b, 1, b, 2, 3.0)
    s.c("DistanceX", a, 1, a, 2, 6.0)
    s.c("DistanceY", b, 1, b, 2, 4.0)
    add("two_independent_conflicts", s, [[0, 2], [1, 3]], None)

    # C3 two overlapping conflicts sharing the middle constraint
    s = _Sk(doc, "c_overlap")
    a = s.line(0, 0, 5, 0)
    s.c("DistanceX", a, 1, a, 2, 5.0)
    s.c("DistanceX", a, 1, a, 2, 6.0)
    s.c("DistanceX", a, 1, a, 2, 5.0)
    add("two_overlapping_conflicts", s, [[0, 1], [1, 2]], 1)

    # C4 three pairwise conflicting values
    s = _Sk(doc, "c_triple")
    a = s.line(0, 0, 5, 0)
    s.c("DistanceX", a, 1, a, 2, 5.0)
    s.c("DistanceX", a, 1, a, 2, 6.0)
    s.c("DistanceX", a, 1, a, 2, 7.0)
    add("three_pairwise_conflicts", s, [[0, 1], [0, 2], [1, 2]], None)

    # C5 conflict through an expression-driven datum
    s = _Sk(doc, "c_expr")
    a = s.line(0, 0, 5, 0)
    s.c("DistanceX", a, 1, a, 2, 5.0, name="w")
    s.c("DistanceX", a, 1, a, 2, 5.0, name="v")
    s.o.setExpression("Constraints.v", "Constraints.w * 2")
    doc.recompute()
    add("expression_driven_conflict", s, [[0, 1]], 1)

    # C6 conflict against external geometry
    s = _Sk(doc, "c_external")
    box = doc.addObject("Part::Box", "c_external_box")
    doc.recompute()
    edge = None
    for i, e in enumerate(box.Shape.Edges):
        ps = sorted((tuple(round(c, 6) for c in v.Point) for v in e.Vertexes))
        if ps == [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0)]:
            edge = "Edge%d" % (i + 1)
    s.o.addExternal(box.Name, edge)
    a = s.line(0.05, 0.05, 9.9, 0.0)
    s.c("Coincident", a, 1, -3, 1)
    s.c("Coincident", a, 2, -3, 2)
    s.c("Distance", a, 12.0)
    add("external_geometry_conflict", s, [[0, 1, 2]], 2)

    # C7 conflict inside a rectangle (needs the corners and both vertical sides)
    s = _Sk(doc, "c_rect")
    ids, _ = s.rect(0, 0, 10, 5)
    s.c("Horizontal", ids[0])
    s.c("Horizontal", ids[2])
    s.c("Vertical", ids[1])
    s.c("Vertical", ids[3])
    s.c("DistanceX", ids[0], 1, ids[0], 2, 10.0)
    s.c("DistanceX", ids[2], 2, ids[2], 1, 11.0)
    add("rectangle_width_conflict", s, [[0, 1, 2, 3, 6, 7, 8, 9]], 9)

    # C8 two different angles between the same lines
    s = _Sk(doc, "c_angle")
    a = s.line(0, 0, 4, 0)
    b = s.line(0, 0, 3.4641, 2)
    s.c("Coincident", a, 1, b, 1)
    s.c("Angle", a, 1, b, 1, 30 * DEG)
    s.c("Angle", a, 1, b, 1, 45 * DEG)
    add("angle_conflict", s, [[1, 2]], 2)

    # C9 equal circles with different radii
    s = _Sk(doc, "c_radius")
    a = s.circle(0, 0, 2)
    b = s.circle(6, 0, 2)
    s.c("Radius", a, 2.0)
    s.c("Equal", a, b)
    s.c("Radius", b, 3.0)
    add("equal_radius_conflict", s, [[0, 1, 2]], 2)

    # C10 parallel and perpendicular
    s = _Sk(doc, "c_par_perp")
    a = s.line(0, 0, 4, 0)
    b = s.line(0, 2, 4, 2)
    s.c("Parallel", a, b)
    s.c("Angle", a, b, 0.5)
    add("parallel_angle_conflict", s, [[0, 1]], 1)

    # C11 horizontal line with a vertical extent
    s = _Sk(doc, "c_hdy")
    a = s.line(0, 0, 4, 0)
    s.c("Horizontal", a)
    s.c("DistanceY", a, 1, a, 2, 3.0)
    add("horizontal_with_height_conflict", s, [[0, 1]], 1)

    # C12 tangent circles pinned at the wrong centre distance
    s = _Sk(doc, "c_tangent")
    c1 = s.circle(0, 0, 2)
    c2 = s.circle(3, 0, 1)
    s.c("DistanceX", -1, 1, c1, 3, 0.0)
    s.c("DistanceY", -1, 1, c1, 3, 0.0)
    s.c("Radius", c1, 2.0)
    s.c("Radius", c2, 1.0)
    s.c("PointOnObject", c2, 3, -1)
    s.c("Tangent", c1, c2)
    s.c("DistanceX", -1, 1, c2, 3, 5.0)
    add("tangent_distance_conflict", s, None, 6)

    # C13 conflict in the middle of the constraint list (benign constraints follow)
    s = _Sk(doc, "c_middle")
    ids, _ = s.rect(0, 0, 10, 5)
    s.c("Horizontal", ids[0])
    s.c("Horizontal", ids[2])
    s.c("Vertical", ids[1])
    s.c("Vertical", ids[3])
    s.c("DistanceX", ids[0], 1, ids[0], 2, 10.0)
    s.c("DistanceX", ids[2], 2, ids[2], 1, 11.0)
    s.c("DistanceY", ids[1], 1, ids[1], 2, 5.0)
    s.c("DistanceX", -1, 1, ids[0], 1, 0.0)
    s.c("DistanceY", -1, 1, ids[0], 1, 0.0)
    add("conflict_followed_by_benign", s, [[0, 1, 2, 3, 6, 7, 8, 9]], 9)

    # C14 culprit is the earlier constraint, geometry drawn at the culprit's value
    s = _Sk(doc, "c_first")
    a = s.line(0, 0, 6, 0)
    s.c("DistanceX", a, 1, a, 2, 5.0)
    s.c("Horizontal", a)
    s.c("DistanceX", a, 1, a, 2, 6.0)
    add("culprit_drawn_late", s, [[0, 2]], 2)

    # C15 drawing satisfies neither value (the error signal is weak).  Added in
    # one batch so the solver never moved the drawing to satisfy the first value.
    s = _Sk(doc, "c_neutral", bulk=True)
    a = s.line(0, 0, 5.5, 0)
    s.c("DistanceX", a, 1, a, 2, 5.0)
    s.c("Horizontal", a)
    s.c("DistanceX", a, 1, a, 2, 6.0)
    s.finish()
    add("neutral_drawing_conflict", s, [[0, 2]], 2)

    # C15b the same for a rectangle, drawn 10.5 wide against widths 10 and 11
    s = _Sk(doc, "c_neutral_rect", bulk=True)
    ids, _ = s.rect(0, 0, 10.5, 5)
    s.c("Horizontal", ids[0])
    s.c("Horizontal", ids[2])
    s.c("Vertical", ids[1])
    s.c("Vertical", ids[3])
    s.c("DistanceX", ids[0], 1, ids[0], 2, 10.0)
    s.c("DistanceX", ids[2], 2, ids[2], 1, 11.0)
    s.finish()
    add("neutral_drawing_rectangle", s, [[0, 1, 2, 3, 6, 7, 8, 9]], 9)

    # C15c batch-added rectangle drawn at the first width: the error signal points at the culprit
    s = _Sk(doc, "c_bulk_rect", bulk=True)
    ids, _ = s.rect(0, 0, 10, 5)
    s.c("Horizontal", ids[0])
    s.c("Horizontal", ids[2])
    s.c("Vertical", ids[1])
    s.c("Vertical", ids[3])
    s.c("DistanceX", ids[0], 1, ids[0], 2, 10.0)
    s.c("DistanceX", ids[2], 2, ids[2], 1, 12.0)
    s.finish()
    add("batch_added_rectangle_conflict", s, [[0, 1, 2, 3, 6, 7, 8, 9]], 9)

    # C15d one odd value against two equal ones: the best fix removes the odd
    # value and leaves two equal dimensions, which are then redundant
    s = _Sk(doc, "c_odd_one", bulk=True)
    a = s.line(0, 0, 6, 0)
    s.c("DistanceX", a, 1, a, 2, 5.0)
    s.c("DistanceX", a, 1, a, 2, 6.0)
    s.c("DistanceX", a, 1, a, 2, 6.0)
    s.finish()
    add("odd_value_against_two_equal", s, [[0, 1], [0, 2]], 0)

    # C16 overlapping conflicts with unrelated geometry between them
    s = _Sk(doc, "c_overlap2")
    a = s.line(0, 0, 5, 0)
    b = s.line(0, 3, 4, 3)
    s.c("DistanceX", a, 1, a, 2, 5.0)
    s.c("DistanceX", b, 1, b, 2, 4.0)
    s.c("DistanceX", a, 1, a, 2, 6.0)
    s.c("DistanceX", b, 1, b, 2, 4.0)
    s.c("DistanceX", a, 1, a, 2, 5.0)
    add("overlap_with_bystanders", s, [[0, 2], [2, 4]], 2)


# Conflicts whose detection depends on the drawn geometry: the same set of
# constraints answers differently on a geometry the solver has already moved.
# Truths are exact minimal conflict sets from a brute force over all subsets,
# each subset solved on a brand-new sketch.


ARCS_TRUTH = [[0, 1, 2]]


def build_readers(doc, expression=True):
    """Two horizontal lines l and m; DistanceX l = 10, DistanceX m = 20 (the
    expression ``w * 2`` when ``expression``), Distance l = 12, Equal l m.
    Constraints: 0 Horizontal l, 1 Horizontal m, 2 DistanceX l, 3 DistanceX m,
    4 Distance l, 5 Equal.  Truth: {0,1,2,3,5} (10 and 20 on equal lines),
    {0,1,3,4,5} (12 and 20 on equal lines) and {2,4}, which the solver flags
    because the drawn line is exactly horizontal, so that a distance of 12
    against a horizontal extent of 10 has a singular Jacobian there."""
    s = _Sk(doc, "c_readers" if expression else "c_readers_plain")
    l = s.line(0, 0, 10, 0)
    m = s.line(0, 3, 10, 3)
    s.c("Horizontal", l)
    s.c("Horizontal", m)
    s.c("DistanceX", l, 1, l, 2, 10.0, name="w")
    s.c("DistanceX", m, 1, m, 2, 20.0, name="w2")
    s.c("Distance", l, 12.0, name="len")
    s.c("Equal", l, m)
    if expression:
        s.o.setExpression("Constraints.w2", "Constraints.w * 2")
    return s.o


def build_readers_arcs(doc):
    """Two arcs with radii 5 and 4 made Equal, plus a coincident centre and a
    tangency.  Constraints: 0 Radius a, 1 Radius b, 2 Equal, 3 Coincident,
    4 Tangent.  Truth: {0,1,2} (5, 4 and equal radii); the tangency and the
    coincidence are not part of any conflict."""
    s = _Sk(doc, "c_readers_arcs")
    a = s.arc(0, 0, 5, 0.2, 2.0)
    b = s.arc(20, 0, 4, 0.2, 2.0)
    s.c("Radius", a, 5.0)
    s.c("Radius", b, 4.0)
    s.c("Equal", a, b)
    s.c("Coincident", a, 3, -1, 1)
    s.c("Tangent", a, b)
    return s.o


def _geometry_dependent(doc, out):
    for name, builder, sets in (
        ("readers_expression", lambda d: build_readers(d, True), [[0, 1, 2, 3, 5], [0, 1, 3, 4, 5], [2, 4]]),
        ("readers_plain", lambda d: build_readers(d, False), [[0, 1, 2, 3, 5], [0, 1, 3, 4, 5], [2, 4]]),
        ("readers_arcs", build_readers_arcs, ARCS_TRUTH),
    ):
        out.append(
            Case(
                name,
                "conflict",
                builder(doc),
                _truth(conflicting=True, conflict_sets=sets, culprit=None),
                doc,
                builder=builder,
            )
        )


# ---------------------------------------------------------------------------
# D. Consistent but redundant
# ---------------------------------------------------------------------------


def _redundant(doc, out):
    def add(name, sk, groups, removals, dof, **kw):
        out.append(
            Case(
                name,
                "redundant",
                sk.o,
                _truth(redundant=True, red_groups=groups, red_removals=removals, dof=dof, **kw),
                doc,
            )
        )

    s = _Sk(doc, "r_dup")
    a = s.line(0, 0, 5, 0)
    s.c("Horizontal", a)
    s.c("DistanceX", -1, 1, a, 1, 0.0)
    s.c("DistanceY", -1, 1, a, 1, 0.0)
    s.c("DistanceX", a, 1, a, 2, 5.0)
    s.c("DistanceX", a, 1, a, 2, 5.0)
    add("duplicate_dimension", s, [[3, 4]], 1, 0)

    s = _Sk(doc, "r_par")
    a = s.line(0, 0, 4, 0)
    b = s.line(0, 2, 4, 2)
    s.c("Horizontal", a)
    s.c("Horizontal", b)
    s.c("Parallel", a, b)
    add("parallel_of_horizontals", s, [[0, 1, 2]], 1, 6)

    s = _Sk(doc, "r_rect")
    ids, _ = s.rect(0, 0, 10, 5)
    s.c("Horizontal", ids[0])
    s.c("Horizontal", ids[2])
    s.c("Vertical", ids[1])
    s.c("Vertical", ids[3])
    s.c("DistanceX", ids[0], 1, ids[0], 2, 10.0)
    s.c("DistanceX", ids[2], 2, ids[2], 1, 10.0)
    add("rectangle_repeated_width", s, [[0, 1, 2, 3, 6, 7, 8, 9]], 1, 3)

    s = _Sk(doc, "r_radius")
    a = s.circle(0, 0, 2)
    b = s.circle(6, 0, 2)
    s.c("Radius", a, 2.0)
    s.c("Equal", a, b)
    s.c("Radius", b, 2.0)
    add("equal_and_repeated_radius", s, [[0, 1, 2]], 1, 4)

    s = _Sk(doc, "r_hdy")
    a = s.line(0, 0, 4, 0)
    s.c("Horizontal", a)
    s.c("DistanceY", a, 1, a, 2, 0.0)
    add("horizontal_and_zero_height", s, [[0, 1]], 1, 3)

    s = _Sk(doc, "r_ext")
    box = doc.addObject("Part::Box", "r_ext_box")
    doc.recompute()
    edge = None
    for i, e in enumerate(box.Shape.Edges):
        ps = sorted((tuple(round(c, 6) for c in v.Point) for v in e.Vertexes))
        if ps == [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0)]:
            edge = "Edge%d" % (i + 1)
    s.o.addExternal(box.Name, edge)
    a = s.line(0.05, 0.05, 9.9, 0.0)
    s.c("Coincident", a, 1, -3, 1)
    s.c("Coincident", a, 2, -3, 2)
    s.c("Distance", a, 10.0)
    add("external_pinned_length", s, [[0, 1, 2]], 1, 0)

    s = _Sk(doc, "r_triple")
    a = s.line(0, 0, 5, 0)
    s.c("Horizontal", a)
    s.c("DistanceX", -1, 1, a, 1, 0.0)
    s.c("DistanceY", -1, 1, a, 1, 0.0)
    s.c("DistanceX", a, 1, a, 2, 5.0)
    s.c("DistanceX", a, 1, a, 2, 5.0)
    s.c("DistanceX", a, 1, a, 2, 5.0)
    add("triple_dimension", s, None, 2, 0)

    s = _Sk(doc, "r_tri")
    a = s.line(0, 0, 3, 0)
    b = s.line(3, 0, 0, 4)
    c = s.line(0, 4, 0, 0)
    s.c("Coincident", a, 2, b, 1)
    s.c("Coincident", b, 2, c, 1)
    s.c("Coincident", c, 2, a, 1)
    s.c("DistanceX", -1, 1, a, 1, 0.0)
    s.c("DistanceY", -1, 1, a, 1, 0.0)
    s.c("Horizontal", a)
    s.c("Distance", a, 3.0)
    s.c("Distance", b, 5.0)
    s.c("Distance", c, 4.0)
    s.c("Perpendicular", a, c)
    add("triangle_with_right_angle", s, None, 1, 0)


# ---------------------------------------------------------------------------
# E. Construction, reference and inactive constraints
# ---------------------------------------------------------------------------


def _reference_construction(doc, out):
    # E1 reference dimension next to a driving one: no conflict
    s = _Sk(doc, "e_ref")
    a = s.line(0, 0, 5, 0)
    s.c("Horizontal", a)
    s.c("DistanceX", -1, 1, a, 1, 0.0)
    s.c("DistanceY", -1, 1, a, 1, 0.0)
    s.c("DistanceX", a, 1, a, 2, 5.0)
    r = s.c("DistanceX", a, 1, a, 2, 6.0)
    s.o.setDriving(r, False)
    out.append(Case("reference_dimension", "reference", s.o, _truth(dof=0, reference=[r]), doc))

    # E2 conflict on construction geometry
    s = _Sk(doc, "e_cons")
    a = s.line(0, 0, 5, 0, True)
    s.c("DistanceX", a, 1, a, 2, 5.0)
    s.c("DistanceX", a, 1, a, 2, 6.0)
    out.append(
        Case(
            "construction_line_conflict",
            "reference",
            s.o,
            _truth(conflicting=True, conflict_sets=[[0, 1]], culprit=1),
            doc,
        )
    )

    # E3 inactive constraint pair: no conflict, the inactive ones are ignored
    s = _Sk(doc, "e_inactive")
    a = s.line(0, 0, 5, 0)
    s.c("Horizontal", a)
    s.c("DistanceX", -1, 1, a, 1, 0.0)
    s.c("DistanceY", -1, 1, a, 1, 0.0)
    s.c("DistanceX", a, 1, a, 2, 5.0)
    i = s.c("DistanceX", a, 1, a, 2, 6.0)
    s.o.setActive(i, False)
    out.append(Case("inactive_constraint", "reference", s.o, _truth(dof=0, inactive=[i]), doc))

    # E4 reference constraint beside a real conflict
    s = _Sk(doc, "e_ref_conflict")
    a = s.line(0, 0, 5, 0)
    s.c("DistanceX", a, 1, a, 2, 5.0)
    r = s.c("DistanceY", a, 1, a, 2, 0.0)
    s.o.setDriving(r, False)
    s.c("DistanceX", a, 1, a, 2, 6.0)
    out.append(
        Case(
            "reference_beside_conflict",
            "reference",
            s.o,
            _truth(conflicting=True, conflict_sets=[[0, 2]], culprit=2, reference=[r]),
            doc,
        )
    )

    # E5 construction circle plus a driven point: DOF counts construction geometry
    s = _Sk(doc, "e_cons_dof")
    c = s.circle(0, 0, 3, True)
    p = s.point(3, 0)
    s.c("PointOnObject", p, 1, c)
    out.append(Case("construction_circle_point", "reference", s.o, _truth(dof=4), doc))

    # E6 inactive plus conflict elsewhere
    s = _Sk(doc, "e_inactive_conflict")
    a = s.line(0, 0, 5, 0)
    s.c("DistanceX", a, 1, a, 2, 5.0)
    s.c("DistanceX", a, 1, a, 2, 6.0)
    i = s.c("DistanceX", a, 1, a, 2, 7.0)
    s.o.setActive(i, False)
    out.append(
        Case(
            "inactive_beside_conflict",
            "reference",
            s.o,
            _truth(conflicting=True, conflict_sets=[[0, 1]], culprit=1, inactive=[i]),
            doc,
        )
    )


# ---------------------------------------------------------------------------
# L. Known limits: the solver fails without naming a conflict
# ---------------------------------------------------------------------------


def _limits(doc, out):
    def add(name, sk):
        out.append(Case(name, "limit", sk.o, _truth(limit=True), doc))

    # infeasible but full rank: no side of 10 closes a 3-5 triangle
    s = _Sk(doc, "l_triangle")
    a = s.line(0, 0, 3, 0)
    b = s.line(3, 0, 0, 4)
    c = s.line(0, 4, 0, 0)
    s.c("Coincident", a, 2, b, 1)
    s.c("Coincident", b, 2, c, 1)
    s.c("Coincident", c, 2, a, 1)
    s.c("Distance", a, 3.0)
    s.c("Distance", b, 5.0)
    s.c("Distance", c, 10.0)
    add("triangle_3_5_10", s)

    # parallel and perpendicular lines: the solver fails and names nothing
    s = _Sk(doc, "l_par_perp")
    a = s.line(0, 0, 4, 0)
    b = s.line(0, 2, 4, 2)
    s.c("Parallel", a, b)
    s.c("Perpendicular", a, b)
    add("parallel_perpendicular", s)


# ---------------------------------------------------------------------------
# F. Real sketches from FreeCAD's own data
# ---------------------------------------------------------------------------

# (label, relative candidates, sketch name)
REAL_SKETCHES = [
    ("partdesign_example_sketch", "examples/PartDesignExample.FCStd", "Sketch"),
    ("partdesign_example_sketch001", "examples/PartDesignExample.FCStd", "Sketch001"),
    ("partdesign_example_sketch002", "examples/PartDesignExample.FCStd", "Sketch002"),
    ("partdesign_example_sketch003", "examples/PartDesignExample.FCStd", "Sketch003"),
    ("partdesign_example_sketch004", "examples/PartDesignExample.FCStd", "Sketch004"),
    ("bim_example_sketch007", "examples/BIMExample.FCStd", "Sketch007"),
    ("tangent_mode_sketch001", "Mod/Part/parttests/TestTangentMode3-0.21.FCStd", "Sketch001"),
    ("tangent_mode_sketch002", "Mod/Part/parttests/TestTangentMode3-0.21.FCStd", "Sketch002"),
    (
        "carbon_copy_sketch",
        "Mod/Sketcher/SketcherTests/TestSketchCarbonCopyReverseMapping.FCStd",
        "Sketch",
    ),
    (
        "carbon_copy_sketch001",
        "Mod/Sketcher/SketcherTests/TestSketchCarbonCopyReverseMapping.FCStd",
        "Sketch001",
    ),
    (
        "cam_truncated_pocket_sketch019",
        "Mod/CAM/CAMTests/Fixtures/test_28534_truncated_pocket.FCStd",
        "Sketch019",
    ),
    ("cam_drilling_sketch005", "Mod/CAM/CAMTests/Drilling_1.FCStd", "Sketch005"),
    ("arch_detail_sketch", "examples/ArchDetail.FCStd", "Sketch"),
]


def find_data_file(relative):
    """Find an FCStd shipped with the build or the source tree.  The
    environment variable ``AUTODEFINE_SOURCE_ROOT`` names a source checkout to
    search before the build tree."""
    home = FreeCAD.getHomePath()
    here = os.path.dirname(os.path.abspath(__file__))
    roots = []
    source = os.environ.get("AUTODEFINE_SOURCE_ROOT")  # a source checkout, searched first: a build tree does not install every test file
    if source:
        roots += [source, os.path.join(source, "data"), os.path.join(source, "src")]
    roots += [
        home,
        os.path.join(home, "share"),
        os.path.join(home, "data"),
        FreeCAD.getResourceDir(),
        os.path.abspath(os.path.join(here, "..", "..", "..", "..")),
        os.path.abspath(os.path.join(here, "..", "..", "..", "..", "data")),
        os.path.abspath(os.path.join(here, "..", "..", "..", "..", "src")),
        os.path.abspath(os.path.join(here, "..", "..", "..")),
    ]
    rel = relative
    alt = relative[len("examples/") :] if relative.startswith("examples/") else None
    for root in roots:
        for r in (rel, "examples/" + alt if alt else None, alt):
            if r and os.path.exists(os.path.join(root, r)):
                return os.path.join(root, r)
    return None


def _plant_duplicate(sketch):
    """Add a second datum of the same kind with a different value to a real
    sketch.  Returns (anchor_index, planted_index) or None."""
    cons = sketch.Constraints
    for i, c in enumerate(cons):
        if c.Type not in ("DistanceX", "DistanceY", "Distance", "Radius", "Diameter"):
            continue
        if not c.Driving or not c.IsActive or c.Value <= 1e-6:
            continue
        bound = [p.lstrip(".") for p, _ in sketch.ExpressionEngine]
        if "Constraints[%d]" % i in bound or (c.Name and "Constraints." + c.Name in bound):
            continue
        v = float(c.Value) * 1.25 + 1.0
        try:
            if c.Type in ("Radius", "Diameter"):
                new = Sketcher.Constraint(c.Type, c.First, v)
            elif c.Second == -2000:
                if c.FirstPos == 0:
                    new = Sketcher.Constraint(c.Type, c.First, v)
                else:
                    new = Sketcher.Constraint(c.Type, c.First, c.FirstPos, v)
            elif c.SecondPos == 0:
                new = Sketcher.Constraint(c.Type, c.First, c.FirstPos, c.Second, v)
            else:
                new = Sketcher.Constraint(c.Type, c.First, c.FirstPos, c.Second, c.SecondPos, v)
        except Exception:
            continue
        j = sketch.addConstraint(new)
        if sketch.solve() < 0 and sketch.ConflictingConstraints:
            return i, j
        sketch.delConstraint(j)
    return None


def load_real(doc_cache=None):
    """Open the real documents and return [(label, sketch)] for the listed sketches."""
    doc_cache = doc_cache if doc_cache is not None else {}
    out = []
    for label, rel, name in REAL_SKETCHES:
        path = find_data_file(rel)
        if path is None:
            out.append((label, None, rel))
            continue
        if path not in doc_cache:
            doc_cache[path] = FreeCAD.openDocument(path, True)
        d = doc_cache[path]
        out.append((label, d.getObject(name), rel))
    return out


def _real(out, doc_cache):
    for label, sk, rel in load_real(doc_cache):
        if sk is None:
            raise FileNotFoundError("corpus data file not found: " + rel)
        sk.solve()
        out.append(
            Case(
                label,
                "real",
                sk,
                _truth(dof=None, conflicting=False),
                sk.Document,
                real=True,
            )
        )
    # planted variants live in the same documents, as copies of the sketches
    for label, sk, rel in load_real(doc_cache):
        d = sk.Document
        twin = d.copyObject(sk, True)
        planted = _plant_duplicate(twin)
        if planted is None:
            d.removeObject(twin.Name)
            continue
        anchor, new = planted
        out.append(
            Case(
                label + "_planted",
                "real_planted",
                twin,
                _truth(conflicting=True, culprit=new, anchor=anchor),
                d,
                real=True,
            )
        )


# ---------------------------------------------------------------------------
# G. Scale series
# ---------------------------------------------------------------------------


def build_scale(doc, name, n, planted=False, chained=True, plants=()):
    """A chain of constrained rectangles plus fillers, exactly `n` constraints
    (`n + 1` with a planted conflict), well constrained with DOF 0.

    With ``chained=False`` every rectangle is placed from the origin on its
    own, so the sketch falls apart into independent components (the
    "decoupled" series); the chain is one long connected system (although the
    horizontal and vertical constraints never meet, so PlaneGCS splits it in
    two: the x and the y unknowns).  ``plants`` is a list of ``(rect, kind)``
    with kind ``"conflict"`` (a second, different top width) or
    ``"redundant"`` (the bottom width stated twice); each adds one constraint
    and is described in ``info["plants"]``.

    Returns (sketch, info) where info holds the planted index and the known
    minimal conflict set."""
    s = _Sk(doc, name, bulk=True)
    k = n // 12
    rest = n - 12 * k
    if rest == 1 and k > 0:
        k -= 1
        rest = 13
    info = {"rects": k, "planted": None, "conflict_set": None}
    prev_bottom = None
    units = []
    for r in range(k):
        x = r * 12.0
        start = s._ncons
        ids, _ = s.rect(x, 0, 10, 5, 0.05, -0.04)
        b, rt, t, l = ids
        s.c("Horizontal", b)
        s.c("Horizontal", t)
        s.c("Vertical", rt)
        s.c("Vertical", l)
        s.c("DistanceX", b, 1, b, 2, 10.0)
        s.c("DistanceY", rt, 1, rt, 2, 5.0)
        if prev_bottom is None or not chained:
            s.c("DistanceX", -1, 1, b, 1, 0.0 if chained else x)
            s.c("DistanceY", -1, 1, b, 1, 0.0)
        else:
            s.c("DistanceX", prev_bottom, 2, b, 1, 2.0)
            s.c("DistanceY", prev_bottom, 2, b, 1, 0.0)
        prev_bottom = b
        units.append((start, ids))
    # fillers: circles (3 constraints) and points (2 constraints), each DOF 0
    fx = 0.0
    while rest > 0:
        if rest % 2 == 1:
            c = s.circle(fx + 0.1, -10.1, 1.1)
            s.c("DistanceX", -1, 1, c, 3, fx)
            s.c("DistanceY", -1, 1, c, 3, -10.0)
            s.c("Radius", c, 1.0)
            rest -= 3
        else:
            p = s.point(fx + 0.1, -20.1)
            s.c("DistanceX", -1, 1, p, 1, fx)
            s.c("DistanceY", -1, 1, p, 1, -20.0)
            rest -= 2
        fx += 3.0
    info["plants"] = []
    for m, kind in plants:
        start, ids = units[m]
        if kind == "conflict":
            j = s.c("DistanceX", ids[2], 2, ids[2], 1, 11.0)
            members = sorted([start + o for o in (0, 1, 2, 3, 6, 7, 8)] + [j])
        else:
            j = s.c("DistanceX", ids[0], 1, ids[0], 2, 10.0)
            members = sorted([start + 8, j])
        info["plants"].append({"rect": m, "kind": kind, "index": j, "set": members})
    if planted and k > 0:
        m = k // 2
        start, ids = units[m]
        j = s.c("DistanceX", ids[2], 2, ids[2], 1, 11.0)
        # corners 0-3, vertical sides at unit offsets 6 and 7, bottom width at 8, planted
        info["planted"] = j
        info["conflict_set"] = sorted([start + o for o in (0, 1, 2, 3, 6, 7, 8)] + [j])
    s.finish()
    return s.o, info


def build_coupled(doc, name, n, plants=(), omit=0):
    """One connected system of exactly `n` constraints (n even, at least 6)
    with DOF 0, in which x and y are coupled everywhere: a strip of equilateral
    triangles made of points, every point after the second held at distance 1
    from the two before it.  The first point is placed from the origin, the
    second by one distance and one horizontal offset.  Each point is drawn a
    little off its place so that a solve has to iterate.

    ``plants`` is a list of ``(point, kind)``: kind ``"conflict"`` states the
    distance to the point two before as 2 instead of 1, ``"redundant"``
    repeats the distance to the point before.  Each adds one constraint.
    ``omit`` leaves out the last `omit` constraints of the strip, which gives
    the sketch that many degrees of freedom.

    Returns (sketch, info) with the number of points."""
    assert n % 2 == 0 and n >= 6
    s = _Sk(doc, name, bulk=True)
    count = n // 2
    h = math.sqrt(3.0) / 2.0
    pts = []
    for i in range(count):
        x = 0.5 * i + 0.03 * math.sin(1.7 * i)
        y = h * (i % 2) + 0.03 * math.cos(2.3 * i)
        pts.append(s.point(x, y))
    s.c("DistanceX", -1, 1, pts[0], 1, 0.0)
    s.c("DistanceY", -1, 1, pts[0], 1, 0.0)
    s.c("Distance", pts[0], 1, pts[1], 1, 1.0)
    s.c("DistanceX", pts[0], 1, pts[1], 1, 0.5)
    for i in range(2, count):
        s.c("Distance", pts[i - 1], 1, pts[i], 1, 1.0)
        s.c("Distance", pts[i - 2], 1, pts[i], 1, 1.0)
    if omit:
        del s._cons[-omit:]
        s._ncons -= omit
    for k, kind in plants:
        if kind == "conflict":
            s.c("Distance", pts[k - 2], 1, pts[k], 1, 2.0)
        else:
            s.c("Distance", pts[k - 1], 1, pts[k], 1, 1.0)
    s.finish()
    return s.o, {"points": count}


def _scale(doc_factory, out, sizes):
    for n in sizes:
        d = doc_factory("Scale%d" % n)
        sk, _ = build_scale(d, "scale_%d" % n, n)
        out.append(Case("scale_%d" % n, "scale", sk, _truth(dof=0), d, scale=n))
    n = 1000
    d = doc_factory("Scale1000Planted")
    sk, info = build_scale(d, "scale_1000_planted", n, planted=True)
    out.append(
        Case(
            "scale_1000_planted",
            "scale_planted",
            sk,
            _truth(
                conflicting=True,
                conflict_sets=[info["conflict_set"]],
                culprit=info["planted"],
            ),
            d,
            scale=n,
        )
    )


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def build_synthetic(doc):
    out = []
    _fully_constrained(doc, out)
    _under_constrained(doc, out)
    _conflicting(doc, out)
    _geometry_dependent(doc, out)
    _redundant(doc, out)
    _reference_construction(doc, out)
    _limits(doc, out)
    doc.recompute()
    return out


def build_corpus(doc, doc_factory=None, scale_sizes=(10, 100, 1000, 5000), real=True):
    """Build the whole corpus.  `doc` holds the synthetic cases; scale cases
    get their own documents from `doc_factory(name)`; real sketches are opened
    from the data files.  Returns (cases, extra_docs) so the caller can close
    the extra documents."""
    extra = {}
    cases = build_synthetic(doc)
    if doc_factory is None:
        doc_factory = FreeCAD.newDocument
    scale_cases = []
    _scale(doc_factory, scale_cases, scale_sizes)
    cases += scale_cases
    if real:
        _real(cases, extra)
    return cases, extra


def repair_equal_distances(doc, name="S", n=2, expr=False):
    """Two lines; line 0 carries `n + 1` equal Distance constraints (so every pair is redundant: overlapping groups)
    and line 1 a Distance c, optionally bound to read the last b constraint."""
    V = FreeCAD.Vector
    C = Sketcher.Constraint
    s = doc.addObject("Sketcher::SketchObject", name)
    s.addGeometry(Part.LineSegment(V(0, 0, 0), V(10, 0, 0)))
    s.addGeometry(Part.LineSegment(V(0, 5, 0), V(7, 5, 0)))
    s.addConstraint(C("Horizontal", 0))
    s.addConstraint(C("Horizontal", 1))
    for label in ["a"] + ["b%d" % k for k in range(n)]:
        s.renameConstraint(s.addConstraint(C("Distance", 0, 10.0)), label)
    s.renameConstraint(s.addConstraint(C("Distance", 1, 7.0)), "c")
    if expr:
        s.setExpression("Constraints.c", "Constraints.b%d + 0 mm" % (n - 1))
    doc.recompute()
    return s


def repair_reader_pair(doc, expr, name_of_reader="Sk2"):
    """A conflicted sketch Sk (w, w2 conflict; v) and a second sketch whose constraint k reads `expr` of it."""
    V = FreeCAD.Vector
    C = Sketcher.Constraint
    s = doc.addObject("Sketcher::SketchObject", "Sk")
    s.addGeometry(Part.LineSegment(V(0, 0, 0), V(10, 0, 0)))
    s.addGeometry(Part.LineSegment(V(0, 5, 0), V(10, 5, 0)))
    s.addConstraint(C("Horizontal", 0))
    s.addConstraint(C("Horizontal", 1))
    s.renameConstraint(s.addConstraint(C("DistanceX", 0, 1, 0, 2, 10.0)), "w")
    s.renameConstraint(s.addConstraint(C("DistanceX", 0, 1, 0, 2, 12.0)), "w2")
    s.renameConstraint(s.addConstraint(C("DistanceX", 1, 1, 1, 2, 20.0)), "v")
    s2 = doc.addObject("Sketcher::SketchObject", name_of_reader)
    s2.addGeometry(Part.LineSegment(V(0, 0, 0), V(5, 0, 0)))
    s2.addConstraint(C("DistanceX", 0, 1, 0, 2, 5.0))
    s2.renameConstraint(0, "k")
    s2.setExpression("Constraints.k", expr)
    doc.recompute()
    return s, s2
