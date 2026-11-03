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

"""The oracle of the auto fully-define tests.

It judges candidates from the geometry and from the original constraints.  It
shares nothing with the generator's detection code: relations are re-evaluated
from the solved coordinates by the functions below, candidates are applied and
perturbed through the public sketch API only.

* ``check_applied``: the hard bar of check 2 on a sketch a candidate was applied to.
* ``relation_report``: which of the original geometric relations hold in the
  drawn geometry and which of them survive every +-2% dimension change.
* ``edit_report``: how many +-2% dimension changes solve with DOF 0 and restore.
* ``medium_report``: the fraction of the original's dimension kinds and
  targets that a candidate recovers.
"""

import math

import FreeCAD

TOL = 1e-6
DIR_TOL = 1e-7
DATUMS = ("Distance", "DistanceX", "DistanceY", "Radius", "Diameter", "Angle", "Weight")
RELATIONS = ("Coincident", "Horizontal", "Vertical", "Parallel", "Perpendicular", "Tangent", "Equal", "Symmetric")


# ---------------------------------------------------------------------------
# Geometry reader
# ---------------------------------------------------------------------------


def coordinates(sk):
    """Flat list of numbers describing the solved geometry."""
    out = []
    for g in sk.Geometry:
        t = g.TypeId
        if t == "Part::GeomLineSegment":
            out += [g.StartPoint.x, g.StartPoint.y, g.EndPoint.x, g.EndPoint.y]
        elif t == "Part::GeomPoint":
            out += [g.X, g.Y]
        elif t == "Part::GeomCircle":
            out += [g.Center.x, g.Center.y, g.Radius]
        elif t == "Part::GeomArcOfCircle":
            out += [g.Center.x, g.Center.y, g.Radius, g.StartPoint.x, g.StartPoint.y, g.EndPoint.x, g.EndPoint.y]
        elif t in ("Part::GeomEllipse", "Part::GeomArcOfEllipse"):
            out += [g.Center.x, g.Center.y, g.MajorRadius, g.MinorRadius, g.XAxis.x, g.XAxis.y]
        elif t == "Part::GeomBSplineCurve":
            for p in g.getPoles():
                out += [p.x, p.y]
        else:
            out += [0.0]
    return out


def moved(a, b):
    if len(a) != len(b):
        return float("inf")
    return max([abs(x - y) for x, y in zip(a, b)] + [0.0])


class Geo:
    """Snapshot of the geometry used to evaluate relations."""

    def __init__(self, sk):
        self.g = list(sk.Geometry)

    def point(self, gid, pos):
        if gid == -1 or gid == -2:
            return (0.0, 0.0)
        if gid < 0:
            return None
        g = self.g[gid]
        t = g.TypeId
        if t == "Part::GeomLineSegment":
            p = g.StartPoint if pos == 1 else g.EndPoint
        elif t == "Part::GeomPoint":
            return (g.X, g.Y)
        elif t in ("Part::GeomCircle", "Part::GeomEllipse"):
            p = g.Center
        elif t in ("Part::GeomArcOfCircle", "Part::GeomArcOfEllipse"):
            p = g.StartPoint if pos == 1 else g.EndPoint if pos == 2 else g.Center
        elif t == "Part::GeomBSplineCurve":
            p = g.StartPoint if pos == 1 else g.EndPoint
        else:
            return None
        return (p.x, p.y)

    def direction(self, gid):
        if gid == -1:
            return (1.0, 0.0)
        if gid == -2:
            return (0.0, 1.0)
        if gid < 0:
            return None
        g = self.g[gid]
        if g.TypeId != "Part::GeomLineSegment":
            return None
        dx, dy = g.EndPoint.x - g.StartPoint.x, g.EndPoint.y - g.StartPoint.y
        n = math.hypot(dx, dy)
        return (dx / n, dy / n) if n > 0 else None

    def line_origin(self, gid):
        if gid == -1 or gid == -2:
            return (0.0, 0.0)
        g = self.g[gid]
        return (g.StartPoint.x, g.StartPoint.y)

    def kind(self, gid):
        if gid in (-1, -2):
            return "line"
        if gid < 0:
            return None
        t = self.g[gid].TypeId
        return {"Part::GeomLineSegment": "line", "Part::GeomCircle": "circle", "Part::GeomArcOfCircle": "circle", "Part::GeomPoint": "point"}.get(t, "other")

    def radius(self, gid):
        return self.g[gid].Radius

    def length(self, gid):
        g = self.g[gid]
        return math.hypot(g.EndPoint.x - g.StartPoint.x, g.EndPoint.y - g.StartPoint.y)

    def tangent_at(self, gid, p):
        k = self.kind(gid)
        if k == "line":
            return self.direction(gid)
        if k == "circle":
            c = self.point(gid, 3)
            rx, ry = p[0] - c[0], p[1] - c[1]
            n = math.hypot(rx, ry)
            return (-ry / n, rx / n)
        return None


def _d(p, q):
    return math.hypot(p[0] - q[0], p[1] - q[1])


def _cross(u, v):
    return u[0] * v[1] - u[1] * v[0]


def _line_dist(G, gid, p):
    d = G.direction(gid)
    o = G.line_origin(gid)
    return abs(_cross(d, (p[0] - o[0], p[1] - o[1])))


def _on_edge(G, gid, p):
    k = G.kind(gid)
    if k == "line":
        return _line_dist(G, gid, p) <= TOL
    if k == "circle":
        return abs(_d(p, G.point(gid, 3)) - G.radius(gid)) <= TOL
    return None


def holds(G, c):
    """True/False for an original relation in geometry G, None when the
    oracle cannot evaluate it (external geometry, unsupported kinds)."""
    t = c.Type
    ids = [x for x in (c.First, c.Second, c.Third) if x != -2000]
    if any(i < -2 for i in ids):
        return None
    try:
        if t == "Coincident":
            p, q = G.point(c.First, c.FirstPos), G.point(c.Second, c.SecondPos)
            return None if p is None or q is None else _d(p, q) <= TOL
        if t in ("Horizontal", "Vertical"):
            ax = 1 if t == "Horizontal" else 0
            if c.Second == -2000:
                a, b = G.point(c.First, 1), G.point(c.First, 2)
            else:
                a, b = G.point(c.First, c.FirstPos), G.point(c.Second, c.SecondPos)
            return None if a is None or b is None else abs(a[ax] - b[ax]) <= TOL
        if t in ("Parallel", "Perpendicular"):
            u, v = G.direction(c.First), G.direction(c.Second)
            if u is None or v is None:
                return None
            return abs(_cross(u, v)) <= DIR_TOL if t == "Parallel" else abs(u[0] * v[0] + u[1] * v[1]) <= DIR_TOL
        if t == "Equal":
            k1, k2 = G.kind(c.First), G.kind(c.Second)
            if k1 == "line" and k2 == "line":
                return abs(G.length(c.First) - G.length(c.Second)) <= TOL
            if k1 == "circle" and k2 == "circle":
                return abs(G.radius(c.First) - G.radius(c.Second)) <= TOL
            return None
        if t == "Tangent":
            a, b = c.First, c.Second
            if c.FirstPos and c.SecondPos:
                p, q = G.point(a, c.FirstPos), G.point(b, c.SecondPos)
                ta, tb = G.tangent_at(a, p), G.tangent_at(b, q)
                if None in (p, q, ta, tb):
                    return None
                return _d(p, q) <= TOL and abs(_cross(ta, tb)) <= DIR_TOL
            if c.FirstPos:
                p = G.point(a, c.FirstPos)
                ta, tb = G.tangent_at(a, p), G.tangent_at(b, p)
                on = _on_edge(G, b, p)
                if None in (ta, tb, on):
                    return None
                return on and abs(_cross(ta, tb)) <= DIR_TOL
            ka, kb = G.kind(a), G.kind(b)
            if ka == "line" and kb == "circle":
                a, b, ka, kb = b, a, kb, ka
            if ka == "circle" and kb == "line":
                return abs(_line_dist(G, b, G.point(a, 3)) - G.radius(a)) <= TOL
            if ka == "circle" and kb == "circle":
                d = _d(G.point(a, 3), G.point(b, 3))
                r1, r2 = G.radius(a), G.radius(b)
                return abs(d - (r1 + r2)) <= TOL or abs(d - abs(r1 - r2)) <= TOL
            return None
        if t == "Symmetric":
            p, q = G.point(c.First, c.FirstPos), G.point(c.Second, c.SecondPos)
            if p is None or q is None:
                return None
            if c.ThirdPos:
                o = G.point(c.Third, c.ThirdPos)
                return _d(((p[0] + q[0]) / 2, (p[1] + q[1]) / 2), o) <= TOL
            mid = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
            d = G.direction(c.Third)
            if d is None:
                return None
            return _line_dist(G, c.Third, mid) <= TOL and abs(d[0] * (q[0] - p[0]) + d[1] * (q[1] - p[1])) <= TOL
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# Check 2: the hard bar, judged on the sketch a candidate was applied to
# ---------------------------------------------------------------------------


def row(c):
    return (c.Type, c.Name, c.First, c.FirstPos, c.Second, c.SecondPos, c.Third, c.ThirdPos, float(c.Value), bool(c.Driving), bool(c.IsActive))


def check_applied(sk, rows_before, coords_before):
    """Problems found on a sketch after a candidate was applied (empty when
    the hard bar holds): DOF 0, no conflicting, redundant, partially
    redundant or malformed constraint, existing constraints unchanged, every
    point within 1e-6 mm of the geometry before."""
    problems = []
    code = sk.solve()
    if code != 0:
        problems.append("solve code %d" % code)
    if sk.DoF != 0:
        problems.append("DoF %d" % sk.DoF)
    for name in ("ConflictingConstraints", "RedundantConstraints", "PartiallyRedundantConstraints", "MalformedConstraints"):
        if list(getattr(sk, name)):
            problems.append("%s %s" % (name, list(getattr(sk, name))))
    rows = [row(c) for c in sk.Constraints]
    if rows[: len(rows_before)] != rows_before:
        problems.append("existing constraints changed")
    m = moved(coords_before, coordinates(sk))
    if m > TOL:
        problems.append("moved %.3g" % m)
    return problems


# ---------------------------------------------------------------------------
# Checks 4 and 5: relations that survive, edits that solve
# ---------------------------------------------------------------------------


def _driving_datums(sk):
    out = []
    for i, c in enumerate(sk.Constraints):
        if c.Type in DATUMS and c.Driving and c.IsActive and abs(c.Value) > 1e-9:
            if any(p.lstrip(".") in ("Constraints[%d]" % i, "Constraints." + c.Name) for p, _ in sk.ExpressionEngine):
                continue
            out.append(i)
    return out


def perturb(sk, indices, relations=None, factor=0.02):
    """Change every datum in `indices` by +-`factor` in turn.  Returns
    (changes, solved, restored, held) where `held[k]` is True while relation
    k (a constraint of the original sketch) held in every solved state."""
    base = coordinates(sk)
    changes = solved = restored = 0
    held = {k: True for k in (relations or {})}
    seen_solved = False
    for i in indices:
        value = float(sk.Constraints[i].Value)
        for sign in (1, -1):
            changes += 1
            ok = False
            try:
                sk.setDatum(i, value * (1 + sign * factor))
                ok = sk.solve() == 0 and sk.DoF == 0 and not sk.ConflictingConstraints and not sk.RedundantConstraints
            except Exception:
                ok = False
            if ok:
                solved += 1
                seen_solved = True
                G = Geo(sk)
                for k, con in (relations or {}).items():
                    if held[k] and holds(G, con) is False:
                        held[k] = False
            try:
                sk.setDatum(i, value)
                sk.solve()
                if moved(base, coordinates(sk)) <= TOL and sk.DoF == 0:
                    restored += 1
            except Exception:
                pass
    if not seen_solved:
        held = {k: False for k in held}
    return changes, solved, restored, held


def original_relations(original):
    """Relations of the original sketch that hold in its geometry:
    {index: constraint}, grouped by type."""
    G = Geo(original)
    out = {}
    for i, c in enumerate(original.Constraints):
        if c.Type in RELATIONS and c.Driving and c.IsActive and holds(G, c) is True:
            out[i] = c
    return out


# ---------------------------------------------------------------------------
# Medium oracle
# ---------------------------------------------------------------------------


def datum_signature(c):
    """(kind, targets): kind in size, length, position, angle; targets the
    geometry ids involved.  Radius and diameter are the same size."""
    t = c.Type
    ids = frozenset(x for x in (c.First, c.Second) if x not in (-2000, -1, -2))
    if t in ("Radius", "Diameter"):
        return ("size", frozenset((c.First,)))
    if t == "Angle":
        return ("angle", ids)
    if t in ("Distance", "DistanceX", "DistanceY"):
        origin = c.Second in (-2000, -1, -2) or c.First in (-1, -2)
        if origin:
            return ("position", ids)
        if c.First == c.Second:
            return ("length", ids)
        return ("distance", ids)
    return ("other", ids)


def medium_report(original_rows, candidate_cons):
    """Fraction of the original's dimension kinds and (kind, target) pairs that
    appear in the candidate.  `original_rows` are Constraint objects,
    `candidate_cons` Constraint objects of the sketch the candidate was
    applied to (new ones only)."""
    o = [datum_signature(c) for c in original_rows if c.Type in DATUMS and c.Driving]
    n = [datum_signature(c) for c in candidate_cons if c.Type in DATUMS and c.Driving]
    if not o:
        return None
    kinds_o, kinds_n = {}, {}
    for k, _ in o:
        kinds_o[k] = kinds_o.get(k, 0) + 1
    for k, _ in n:
        kinds_n[k] = kinds_n.get(k, 0) + 1
    kind_hit = sum(min(v, kinds_n.get(k, 0)) for k, v in kinds_o.items())
    pool = list(n)
    target_hit = 0
    for sig in o:
        if sig in pool:
            pool.remove(sig)
            target_hit += 1
    return {"datums": len(o), "kind_hits": kind_hit, "target_hits": target_hit}
