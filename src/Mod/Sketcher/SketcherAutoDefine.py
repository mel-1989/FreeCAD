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

"""Automatic "fully define" for sketches.

``propose(sketch)`` answers "what constraints would make this sketch fully
defined" with candidates that the PlaneGCS solver has already accepted.
``apply_candidate(sketch, candidate)`` applies one.  The user's sketch is read
and never changed by ``propose`` (serialised content, undo stack, touched
state and the active document are the same afterwards, also when it raises);
all trial work happens on throwaway copies in a hidden document.  The
behaviour was specified from the public description of the feature in
SOLIDWORKS' documentation ("Fully Define Sketch": relations first, then
dimensions chosen by intent); no SOLIDWORKS binary, file format or UI text was
used.

Method
------
1. Relations that already hold in the drawn geometry are added first, in this
   order: coincident points (endpoint tangency instead where two curves meet
   with a common tangent), points on axes and on curves, horizontal and
   vertical lines and aligned centres, edge tangency, parallel and
   perpendicular lines, equal lengths and radii, symmetry about the axes, a
   line or a point.  The tolerance rule is absolute: a relation is claimed
   only if the geometry satisfies it within ``LENGTH_TOLERANCE`` (1e-7 mm) for
   anything measured in mm and ``ANGLE_TOLERANCE`` (1e-8 rad) for directions,
   so a relation is never claimed across a real gap, and a point that lies on
   the infinite extension of a segment or arc but not on the segment or arc
   itself is not "on" it.  Every relation is tested on the copy: it stays only
   if the solver reports no conflict, no redundancy and no partial redundancy,
   the DOF falls by at least one per constraint and no point moves by more
   than ``MOVE_TOLERANCE``.  Relations the sketch already implies fail the
   redundancy test and are dropped; batches that fail are bisected.
2. Dimensions are added one intent at a time until the DOF is zero: circle
   diameters and arc radii (one per group of equal radii), line lengths (a
   horizontal or vertical line gets a horizontal or vertical distance, one
   length per group of equal lines), arc spans and line angles (one per group
   of parallel lines), then positions from the origin.  Construction geometry
   is dimensioned only after everything else, and only if a relation cannot
   fix it.  The same independence test as for relations applies.
3. Schemes.  ``baseline`` places each connected piece of geometry from the
   origin, ``chain`` places each piece from the previous one, ``symmetric``
   looks for symmetry before every other relation.  Each scheme makes one
   candidate; candidates with identical constraint sets are merged.

Verification (the hard bar).  Every candidate is applied, by the code that
``apply_candidate`` runs, to a brand-new copy of the user's sketch and
re-solved; it is offered only if the copy has DOF 0, no conflicting,
redundant, partially redundant or malformed constraint, every point is within
``MOVE_TOLERANCE`` of the drawn geometry and every existing constraint is
unchanged.  PlaneGCS results depend on the geometry the solver starts from, so
the verification starts from the drawn geometry, not from a copy that earlier
solves have moved.  A candidate that fails is dropped, never offered.  A
scheme that cannot reach DOF 0 is reported in ``unclosed`` with the DOF that
is left; no candidate is made up for it.

Stability.  PlaneGCS is not repeatable on a few ill-conditioned systems (the
same constraints on the same geometry solved in about half the tries in one
process and every time in the next), so a candidate must pass the fresh-copy
verification `STABILITY_RUNS` more times, and ``apply_candidate`` tries up to
`APPLY_ATTEMPTS` times before giving up.

Re-solve guarantee.  A sketch the user saves must keep solving.  On a few
ill-conditioned sets PlaneGCS returns -1 with DOF 0 and nothing flagged,
differently from process to process, and a result reached from a nudged start
can re-solve that way on 30 to 95% of solves after the file is reopened while
every one-off check is green.  So (1) verification starts from the exact drawn
geometry only, with no nudged fallback, (2) every verification pass, repeats
included, and every ``apply_candidate`` result must give `GATE_RUNS` (10)
consecutive clean DOF-0 solves, on the sketch and on a serialised copy, or the
set is dropped or the apply rolled back, and (3) the nudged retry in
``apply_candidate`` only rescues a first attempt that the solver missed, and
its result passes the same gate.  A clean refusal (``AutoDefineError``) is
preferred to an unreliable file.  The gate is bounded, not a proof: a set that
fails in fewer than about 1 solve in 4 can still pass ten solves.  The gate
costs up to 20 solves, so it is skipped by wall clock when a pass is slow
(`STABILITY_BUDGET`; on the loaded mini that is between about 320 and 640
geometries) and the skip is reported, never silent: every candidate carries
``gate`` (``"full"``, ``"repeats_skipped"`` when the gate ran but the repeat
passes did not, or ``"skipped"``) and ``gate_reason``, and the dict
``apply_candidate`` returns carries ``gate`` (``"full"`` or ``"reduced"``),
``gate_runs`` and ``gate_reason``.  ``apply_candidate`` never applies a result
unvetted: when its own apply was too slow for the full gate it asks for
`REDUCED_GATE_RUNS` clean solves of the real sketch instead.  Both functions
take ``stability_budget`` (seconds) so a test or a caller can force or forbid
the skip (``0`` always skips, ``float("inf")`` never does) independently of
machine load.  A line within about 1.7 degrees of an
axis is dimensioned by its extent along that axis, not by its length: with the
other end placed by other constraints a length has a mirror solution only
twice the small extent away, and the solver can land on it after an edit and
back.  While ``propose`` runs, the console's warning and notification
categories are switched off (the solver would name the scratch sketch "Copy"
for every partly redundant trial constraint) and put back in ``finally``.
The switch is process-wide and ``FreeCAD.Console`` has nothing narrower, so a
warning another thread prints meanwhile is lost too (see ``_quiet``).

Sketches it will not close, and why: a plain ellipse or ellipse arc whose
internal geometry is not exposed (its centre and axes have nothing to
dimension; the reason says to expose it), hyperbola and parabola arcs, and
B-splines without exposed poles; the reason names the geometry id and kind.
Stored geometry that does not satisfy the sketch's own constraints is
reported as ``input_inconsistent`` with the advice to solve the sketch first
(it is not solved on the copy: ``apply_candidate`` holds the stored geometry
fixed, so a candidate verified against moved geometry could not be applied).

Existing constraints are never removed or changed (``keep`` lists indices
whose presence and value are verified on top of that).  Reference constraints
and construction geometry stay as they are; external geometry and expressions
are copied with the sketch and survive.

Limits.  The oracle cannot judge whether a chosen dimension is the one a
designer would pick.  External geometry is not used as a target for
relations.  Hyperbolas, parabolas and B-spline curves are placed through their
end points and (for B-splines) their exposed control points only; relations
involving ellipse edges are not detected.  A sketch that already has a
conflict or a redundancy is not worked on.  A sketch built in memory can
behave differently from its serialised copy (slice 1's known LOW), so
``apply_candidate`` checks the result on the real sketch too and rolls back.
"""

import contextlib
import math
import time

import FreeCAD
import Sketcher

from SketcherDiagnosis import (
    _Scratch,
    _Status,
    _discard,
    _geometry_signature,
    _max_move,
)

__all__ = ["propose", "apply_candidate", "AutoDefineError", "SCHEMES"]

LENGTH_TOLERANCE = 1e-7
ANGLE_TOLERANCE = 1e-8
MOVE_TOLERANCE = 1e-6
APPLY_ATTEMPTS = 1  # the nudged retry is gone: the -1 at DOF 0 was a degenerate line the solver rejected (G10 slice 3a)
STABILITY_RUNS = 3  # extra fresh-copy passes a candidate must survive
STABILITY_BUDGET = 0.3  # seconds; slower sketches skip the repeats and the full re-solve gate (see "The re-solve gate" in the module docstring)
REDUCED_GATE_RUNS = 3  # solves of the real sketch ``apply_candidate`` asks for when its own apply was too slow for the full gate
GATE_RUNS = 10  # consecutive clean solves a result must give (on the sketch and on a serialised copy)
NUDGE = 1e-7  # mm; size of the start offset of a retried solve (well under MOVE_TOLERANCE)
NEAR_AXIS = 0.03  # a line within about 1.7 degrees of an axis is dimensioned by its extent
SCHEMES = ("baseline", "chain", "symmetric")
_MAX_BATCH = 400
_DATUM_TYPES = {"Distance", "DistanceX", "DistanceY", "Radius", "Diameter", "Angle", "Weight"}
_GEOMETRIC = {
    "Coincident",
    "Horizontal",
    "Vertical",
    "Parallel",
    "Perpendicular",
    "Tangent",
    "Equal",
    "Symmetric",
    "PointOnObject",
}


class AutoDefineError(RuntimeError):
    """Raised when a candidate cannot be applied or the input is unusable."""


class _Timeout(Exception):
    pass


# ---------------------------------------------------------------------------
# Geometry model
# ---------------------------------------------------------------------------


class _Geo:
    """One geometry element reduced to what relation detection needs."""

    __slots__ = ("i", "kind", "construction", "pts", "c", "r", "a", "d", "length", "internal", "tid")

    def __init__(self, i, kind, construction):
        self.i = i
        self.kind = kind
        self.construction = construction
        self.pts = {}  # pos -> (x, y)
        self.c = None  # centre
        self.r = 0.0
        self.a = None  # line start
        self.d = None  # unit direction of a line
        self.length = 0.0
        self.internal = False


def _xy(v):
    return (float(v.x), float(v.y))


def _model(sk):
    internal = {c.First for c in sk.Constraints if c.Type == "InternalAlignment"}
    out = []
    for i, g in enumerate(sk.Geometry):
        t = g.TypeId
        construction = bool(sk.getConstruction(i))
        if t == "Part::GeomLineSegment":
            m = _Geo(i, "line", construction)
            m.a = _xy(g.StartPoint)
            b = _xy(g.EndPoint)
            m.pts = {1: m.a, 2: b}
            dx, dy = b[0] - m.a[0], b[1] - m.a[1]
            m.length = math.hypot(dx, dy)
            if m.length > 0:
                m.d = (dx / m.length, dy / m.length)
        elif t == "Part::GeomPoint":
            m = _Geo(i, "point", construction)
            m.pts = {1: (float(g.X), float(g.Y))}
        elif t == "Part::GeomCircle":
            m = _Geo(i, "circle", construction)
            m.c = _xy(g.Center)
            m.r = float(g.Radius)
            m.pts = {3: m.c}
        elif t == "Part::GeomArcOfCircle":
            m = _Geo(i, "arc", construction)
            m.c = _xy(g.Center)
            m.r = float(g.Radius)
            m.pts = {1: _xy(g.StartPoint), 2: _xy(g.EndPoint), 3: m.c}
        elif t == "Part::GeomEllipse":
            m = _Geo(i, "ellipse", construction)
            m.c = _xy(g.Center)
            m.pts = {3: m.c}
        elif t == "Part::GeomArcOfEllipse":
            m = _Geo(i, "ellipse_arc", construction)
            m.c = _xy(g.Center)
            m.pts = {1: _xy(g.StartPoint), 2: _xy(g.EndPoint), 3: m.c}
        elif t == "Part::GeomBSplineCurve":
            m = _Geo(i, "bspline", construction)
            m.pts = {1: _xy(g.StartPoint), 2: _xy(g.EndPoint)}
        else:
            m = _Geo(i, "other", construction)
        m.internal = i in internal
        m.tid = t
        out.append(m)
    return out


def _dist(p, q):
    return math.hypot(p[0] - q[0], p[1] - q[1])


def _arc_span(m):
    a0 = math.atan2(m.pts[1][1] - m.c[1], m.pts[1][0] - m.c[0])
    a1 = math.atan2(m.pts[2][1] - m.c[1], m.pts[2][0] - m.c[0])
    return a0, (a1 - a0) % (2 * math.pi)


def _in_arc(m, p):
    if m.kind != "arc":
        return True
    a0, span = _arc_span(m)
    t = (math.atan2(p[1] - m.c[1], p[0] - m.c[0]) - a0) % (2 * math.pi)
    slack = LENGTH_TOLERANCE / max(m.r, 1e-9)
    return t <= span + slack or t >= 2 * math.pi - slack


def _tangent_at(m, p):
    """Unit tangent of line, circle or arc `m` at the point `p` on it."""
    if m.kind == "line":
        return m.d
    if m.kind in ("circle", "arc"):
        rx, ry = p[0] - m.c[0], p[1] - m.c[1]
        n = math.hypot(rx, ry)
        if n > 0:
            return (-ry / n, rx / n)
    return None


def _parallel(u, v):
    return u is not None and v is not None and abs(u[0] * v[1] - u[1] * v[0]) <= ANGLE_TOLERANCE


class _Grid:
    """Points hashed on a grid so near neighbours are found without a
    quadratic scan."""

    def __init__(self, points, cell=1e-3):
        self.cell = cell
        self.cells = {}
        for k, p in enumerate(points):
            self.cells.setdefault((round(p[0] / cell), round(p[1] / cell)), []).append(k)
        self.points = points

    def near(self, p, tol=LENGTH_TOLERANCE):
        cx, cy = round(p[0] / self.cell), round(p[1] / self.cell)
        out = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for k in self.cells.get((cx + dx, cy + dy), ()):
                    if _dist(self.points[k], p) <= tol:
                        out.append(k)
        return out


# ---------------------------------------------------------------------------
# Relation detection
# ---------------------------------------------------------------------------


def _clusters(items):
    """Group (geo, pos, point) triples that coincide.  Returns clusters of
    size two or more, each sorted by (geo, pos)."""
    grid = _Grid([p for _, _, p in items])
    parent = list(range(len(items)))

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for k, (_, _, p) in enumerate(items):
        for j in grid.near(p):
            a, b = find(k), find(j)
            if a != b:
                parent[max(a, b)] = min(a, b)
    groups = {}
    for k in range(len(items)):
        groups.setdefault(find(k), []).append(items[k][:2])
    return [sorted(g) for g in groups.values() if len(g) > 1]


def _detect(model, existing):
    """Relation candidates by phase.  Each entry is a constraint spec (the
    arguments of ``Sketcher.Constraint``)."""
    geos = {m.i: m for m in model}
    ph = {k: [] for k in ("coincident", "onobject", "hv", "tangent", "parallel", "equal", "symmetric", "symmetric_axes")}
    covered = set()  # frozenset({geoA, geoB}) pairs that already carry a tangency

    # -- coincident points and endpoint tangency ----------------------------
    items = [(-1, 1, (0.0, 0.0))]
    for m in model:
        if m.kind == "point" or m.kind in ("line", "arc", "ellipse_arc", "bspline"):
            for pos, p in m.pts.items():
                items.append((m.i, pos, p))
        elif m.kind in ("circle", "ellipse"):
            items.append((m.i, 3, m.c))
    point_of = {(g, p): xy for g, p, xy in items}
    clusters = _clusters(items)
    for cl in clusters:
        # tangent links first, so a three-way meeting keeps the tangency
        tangent_links = []
        for a in range(len(cl)):
            for b in range(a + 1, len(cl)):
                (ga, pa), (gb, pb) = cl[a], cl[b]
                if ga < 0 or gb < 0 or ga == gb:
                    continue
                A, B = geos[ga], geos[gb]
                curved = A.kind in ("arc", "circle") or B.kind in ("arc", "circle")
                if not curved or pa == 3 or pb == 3:
                    continue
                if _parallel(_tangent_at(A, point_of[(ga, pa)]), _tangent_at(B, point_of[(gb, pb)])):
                    tangent_links.append((a, b))
        parent = list(range(len(cl)))

        def find(k):
            while parent[k] != k:
                k = parent[k]
            return k

        for a, b in tangent_links:
            if find(a) != find(b):
                parent[find(b)] = find(a)
                (ga, pa), (gb, pb) = cl[a], cl[b]
                ph["coincident"].append(("Tangent", ga, pa, gb, pb))
                covered.add(frozenset((ga, gb)))
        for b in range(1, len(cl)):
            if find(b) != find(0):
                parent[find(b)] = find(0)
                (ga, pa), (gb, pb) = cl[0], cl[b]
                if ga == gb:
                    continue
                ph["coincident"].append(("Coincident", ga, pa, gb, pb))
    clustered = {}
    for cl in clusters:
        for g, p in cl:
            clustered[(g, p)] = cl[0]

    reps = [(g, p, xy) for g, p, xy in items if g >= 0 and clustered.get((g, p), (g, p)) == (g, p)]

    # -- edge tangency -------------------------------------------------------
    curves = [m for m in model if m.kind in ("circle", "arc")]
    for A in curves:
        for B in model:
            if B.i == A.i or B.kind not in ("line", "circle", "arc"):
                continue
            if B.kind in ("circle", "arc") and B.i < A.i:
                continue
            if frozenset((A.i, B.i)) in covered:
                continue
            contact = None
            if B.kind == "line":
                if B.d is None:
                    continue
                s = (A.c[0] - B.a[0]) * B.d[0] + (A.c[1] - B.a[1]) * B.d[1]
                foot = (B.a[0] + s * B.d[0], B.a[1] + s * B.d[1])
                if abs(_dist(foot, A.c) - A.r) <= LENGTH_TOLERANCE and -LENGTH_TOLERANCE <= s <= B.length + LENGTH_TOLERANCE:
                    contact = foot
            else:
                d = _dist(A.c, B.c)
                if d > LENGTH_TOLERANCE:
                    ux, uy = (B.c[0] - A.c[0]) / d, (B.c[1] - A.c[1]) / d
                    if abs(d - (A.r + B.r)) <= LENGTH_TOLERANCE:
                        contact = (A.c[0] + ux * A.r, A.c[1] + uy * A.r)
                    elif abs(d - abs(A.r - B.r)) <= LENGTH_TOLERANCE:
                        sg = 1.0 if A.r > B.r else -1.0
                        contact = (A.c[0] + sg * ux * A.r, A.c[1] + sg * uy * A.r)
            if contact is None or not _in_arc(A, contact) or not _in_arc(B, contact):
                continue
            # a contact at an end point of one of them becomes end-to-edge
            spec = None
            for X, Y in ((B, A), (A, B)):
                for pos in (1, 2):
                    if pos in X.pts and _dist(X.pts[pos], contact) <= LENGTH_TOLERANCE and X.kind in ("line", "arc"):
                        spec = ("Tangent", X.i, pos, Y.i)
                        break
                if spec:
                    break
            ph["tangent"].append(spec or ("Tangent", A.i, B.i))
            covered.add(frozenset((A.i, B.i)))

    # -- points on axes and on curves ---------------------------------------
    for g, p, (x, y) in reps:
        if abs(y) <= LENGTH_TOLERANCE:
            ph["onobject"].append(("PointOnObject", g, p, -1))
        if abs(x) <= LENGTH_TOLERANCE:
            ph["onobject"].append(("PointOnObject", g, p, -2))
    own = {}
    for cl in clusters:
        for g, _ in cl:
            own.setdefault(cl[0], set()).add(g)
    for g, p, xy in reps:
        mates = own.get((g, p), {g}) | {g}
        for C in model:
            if C.i in mates or C.kind not in ("line", "circle", "arc"):
                continue
            if frozenset((g, C.i)) in covered:
                continue
            if C.kind == "line":
                if C.d is None:
                    continue
                s = (xy[0] - C.a[0]) * C.d[0] + (xy[1] - C.a[1]) * C.d[1]
                perp = abs((xy[0] - C.a[0]) * C.d[1] - (xy[1] - C.a[1]) * C.d[0])
                ok = perp <= LENGTH_TOLERANCE and -LENGTH_TOLERANCE <= s <= C.length + LENGTH_TOLERANCE
            else:
                ok = abs(_dist(xy, C.c) - C.r) <= LENGTH_TOLERANCE and _in_arc(C, xy)
            if ok:
                ph["onobject"].append(("PointOnObject", g, p, C.i))

    # -- horizontal, vertical, aligned centres --------------------------------
    for m in model:
        if m.kind == "line" and m.length > 10 * LENGTH_TOLERANCE:
            (x1, y1), (x2, y2) = m.pts[1], m.pts[2]
            if abs(y2 - y1) <= LENGTH_TOLERANCE:
                ph["hv"].append(("Horizontal", m.i))
            elif abs(x2 - x1) <= LENGTH_TOLERANCE:
                ph["hv"].append(("Vertical", m.i))
    centres = [(m.i, 3 if m.kind != "point" else 1, m.pts[3 if m.kind != "point" else 1]) for m in model if m.kind in ("circle", "arc", "point", "ellipse")]
    for axis, name in ((1, "Horizontal"), (0, "Vertical")):
        ordered = sorted(centres, key=lambda t: t[2][axis])
        for a, b in zip(ordered, ordered[1:]):
            if abs(a[2][axis] - b[2][axis]) <= LENGTH_TOLERANCE and a[0] != b[0]:
                ph["hv"].append((name, a[0], a[1], b[0], b[1]))

    # -- parallel and perpendicular ----------------------------------------------
    hv_lines = {s[1] for s in ph["hv"] if len(s) == 2}
    lines = [m for m in model if m.kind == "line" and m.d is not None and m.i not in hv_lines and m.length > 10 * LENGTH_TOLERANCE]
    groups = []
    for m in lines:
        for grp in groups:
            if _parallel(grp[0].d, m.d):
                grp.append(m)
                break
        else:
            groups.append([m])
    for grp in groups:
        for a, b in zip(grp, grp[1:]):
            ph["parallel"].append(("Parallel", a.i, b.i))
    for a in range(len(groups)):
        for b in range(a + 1, len(groups)):
            u, v = groups[a][0].d, groups[b][0].d
            if abs(u[0] * v[0] + u[1] * v[1]) <= ANGLE_TOLERANCE:
                ph["parallel"].append(("Perpendicular", groups[a][0].i, groups[b][0].i))

    # -- equal lengths and radii ---------------------------------------------------
    def chain_equal(items):
        items = sorted(items)
        for (v1, i1), (v2, i2) in zip(items, items[1:]):
            if abs(v1 - v2) <= LENGTH_TOLERANCE:
                ph["equal"].append(("Equal", i1, i2))

    chain_equal([(m.length, m.i) for m in model if m.kind == "line" and m.length > 10 * LENGTH_TOLERANCE and not m.internal])
    chain_equal([(m.r, m.i) for m in model if m.kind in ("circle", "arc")])

    # -- symmetry ---------------------------------------------------------------------
    grid = _Grid([xy for _, _, xy in reps])
    free = [(g, p, xy) for g, p, xy in reps]

    def mirror_pairs(reflect, line_geo, bucket):
        found = []
        for k, (g, p, xy) in enumerate(free):
            q = reflect(xy)
            if _dist(q, xy) <= LENGTH_TOLERANCE:
                continue
            for j in grid.near(q):
                if j > k:
                    g2, p2, _ = free[j]
                    if g2 != g or p2 != p:
                        found.append(("Symmetric", g, p, g2, p2))
        ph[bucket].extend(f + (line_geo,) if not isinstance(line_geo, tuple) else f + line_geo for f in found)

    # about drawn helpers first (construction lines, centres, points), then about
    # the axes, the origin and drawn lines, which a centred shape satisfies by chance
    for m in model:
        if m.kind == "line" and m.d is not None and m.length > 10 * LENGTH_TOLERANCE and (m.construction or len(free) <= 150):
            a, u = m.a, m.d

            def refl(q, a=a, u=u):
                s = (q[0] - a[0]) * u[0] + (q[1] - a[1]) * u[1]
                f = (a[0] + s * u[0], a[1] + s * u[1])
                return (2 * f[0] - q[0], 2 * f[1] - q[1])

            mirror_pairs(refl, m.i, "symmetric" if m.construction else "symmetric_axes")
    if len(free) <= 150:
        centres_pts = [(g, p, xy) for g, p, xy in free if geos[g].kind in ("circle", "arc", "point")]
        for og, op, o in centres_pts:
            def refl(q, o=o):
                return (2 * o[0] - q[0], 2 * o[1] - q[1])

            mirror_pairs(refl, (og, op), "symmetric")
        mirror_pairs(lambda q: (-q[0], -q[1]), (-1, 1), "symmetric_axes")
    mirror_pairs(lambda q: (q[0], -q[1]), -1, "symmetric_axes")
    mirror_pairs(lambda q: (-q[0], q[1]), -2, "symmetric_axes")
    # existing constraints are not proposed again
    seen = {_key(c) for c in existing}
    for k in ph:
        ph[k] = [s for s in ph[k] if _spec_key(s) not in seen]
    return ph


def _spec_key(spec):
    t = spec[0]
    if t in ("Coincident", "Horizontal", "Vertical") and len(spec) == 5:
        return (t, frozenset({(spec[1], spec[2]), (spec[3], spec[4])}))
    if t in ("Parallel", "Perpendicular", "Equal"):
        return (t, frozenset(spec[1:3]))
    if t in ("Tangent",) and len(spec) == 3:
        return (t, frozenset(spec[1:3]))
    return (t, tuple(spec[1:]))


def _key(c):
    t = c.Type
    pa, pb = (c.First, c.FirstPos), (c.Second, c.SecondPos)
    if t in ("Coincident", "Horizontal", "Vertical") and c.Second != -2000:
        return (t, frozenset({pa, pb}))
    if t in ("Parallel", "Perpendicular", "Equal") or (t == "Tangent" and c.FirstPos == 0 and c.SecondPos == 0):
        return (t, frozenset((c.First, c.Second)))
    args = [c.First]
    if c.FirstPos:
        args.append(c.FirstPos)
    if c.Second != -2000:
        args.append(c.Second)
        if c.SecondPos:
            args.append(c.SecondPos)
    return (t, tuple(args))


# ---------------------------------------------------------------------------
# Dimension proposals
# ---------------------------------------------------------------------------


def _union_find(n):
    parent = list(range(n))

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    def union(a, b):
        a, b = find(a), find(b)
        if a != b:
            parent[max(a, b)] = min(a, b)

    return find, union


def _dimension_proposals(model, accepted, scheme):
    """Ordered dimension specs.  `accepted` are the relation specs already
    on the copy, used to skip dimensions the relations make redundant."""
    n = len(model)
    f_eq, u_eq = _union_find(n)
    f_dir, u_dir = _union_find(n)
    f_pos, u_pos = _union_find(n)
    for s in accepted:
        t = s[0]
        if t == "Equal":
            u_eq(s[1], s[2])
        elif t in ("Parallel", "Perpendicular"):
            u_dir(s[1], s[2])
        if t in ("Coincident", "Tangent", "Symmetric", "PointOnObject"):
            ids = (s[1], s[2]) if (t == "Tangent" and len(s) == 3) else (s[1], s[3])
            if ids[0] >= 0 and ids[1] >= 0:
                u_pos(ids[0], ids[1])

    seen_eq, seen_dir, seen_piece = set(), set(), set()
    anchors_all = {}

    def sizes_of(ms):
        out = []
        for m in ms:
            if m.kind == "circle":
                root = f_eq(m.i)
                if root not in seen_eq:
                    seen_eq.add(root)
                    out.append(("Diameter", m.i, 2 * m.r))
            elif m.kind == "arc":
                root = f_eq(m.i)
                if root not in seen_eq:
                    seen_eq.add(root)
                    out.append(("Radius", m.i, m.r))
                out.append(("Angle", m.i, _arc_span(m)[1]))
            elif m.kind == "line" and m.d is not None and m.length > 10 * LENGTH_TOLERANCE:
                root = ("L", f_eq(m.i))
                if root not in seen_eq:
                    seen_eq.add(root)
                    (x1, y1), (x2, y2) = m.pts[1], m.pts[2]
                    if abs(y2 - y1) <= LENGTH_TOLERANCE:
                        out.append(_signed("DistanceX", m.i, 1, m.i, 2, x2 - x1))
                    elif abs(x2 - x1) <= LENGTH_TOLERANCE:
                        out.append(_signed("DistanceY", m.i, 1, m.i, 2, y2 - y1))
                    elif min(abs(x2 - x1), abs(y2 - y1)) < NEAR_AXIS * m.length:
                        # A length on a line this close to an axis has a mirror solution only
                        # 2*|small extent| away, and the solver can land on it after an edit
                        # and back.  The dominant extent is well conditioned; the angle
                        # proposal fixes the direction.
                        if abs(x2 - x1) >= abs(y2 - y1):
                            out.append(_signed("DistanceX", m.i, 1, m.i, 2, x2 - x1))
                        else:
                            out.append(_signed("DistanceY", m.i, 1, m.i, 2, y2 - y1))
                    else:
                        out.append(("Distance", m.i, m.length))
        return out

    def angles_of(ms):
        out = []
        for m in ms:
            if m.kind == "line" and m.d is not None and m.length > 10 * LENGTH_TOLERANCE:
                (x1, y1), (x2, y2) = m.pts[1], m.pts[2]
                if abs(y2 - y1) <= LENGTH_TOLERANCE or abs(x2 - x1) <= LENGTH_TOLERANCE:
                    continue
                root = f_dir(m.i)
                if root not in seen_dir:
                    seen_dir.add(root)
                    out.append(("Angle", m.i, math.atan2(y2 - y1, x2 - x1)))
        return out

    def positions_of(ms):
        anchors = []
        for m in ms:
            if not m.pts:
                continue
            root = f_pos(m.i)
            if root in seen_piece:
                continue
            seen_piece.add(root)
            pos = 3 if 3 in m.pts else min(m.pts)
            anchors.append((m.i, pos, m.pts[pos]))
        out = []
        if scheme == "chain":
            prev = anchors_all.get("prev")
            for g, p, xy in anchors:
                if prev is None:
                    out.append(("DistanceX", g, p, xy[0]))
                    out.append(("DistanceY", g, p, xy[1]))
                else:
                    out.append(_signed("DistanceX", prev[0], prev[1], g, p, xy[0] - prev[2][0]))
                    out.append(_signed("DistanceY", prev[0], prev[1], g, p, xy[1] - prev[2][1]))
                prev = (g, p, xy)
            anchors_all["prev"] = prev
        for g, p, xy in anchors:
            out.append(("DistanceX", g, p, xy[0]))
            out.append(("DistanceY", g, p, xy[1]))
        return out

    def fallback(ms):
        out = []
        for m in ms:
            for pos in sorted(m.pts, key=lambda k: (k != 3, k)):
                xy = m.pts[pos]
                out.append(("DistanceX", m.i, pos, xy[0]))
                out.append(("DistanceY", m.i, pos, xy[1]))
        return out

    drawn = [m for m in model if not m.construction]
    helper = [m for m in model if m.construction]
    out = sizes_of(drawn) + angles_of(drawn) + positions_of(drawn)
    out += sizes_of(helper) + angles_of(helper) + positions_of(helper)
    out += fallback(drawn) + fallback(helper)
    return out


def _signed(kind, g1, p1, g2, p2, value):
    if value < 0:
        return (kind, g2, p2, g1, p1, -value)
    return (kind, g1, p1, g2, p2, value)


def _make(spec):
    return Sketcher.Constraint(*spec)


def _is_datum(spec):
    return spec[0] in _DATUM_TYPES


def _text(spec):
    t = spec[0]
    if t in _DATUM_TYPES:
        v = spec[-1]
        if t == "Angle":
            v = math.degrees(v)
            return "%s %s = %.6g deg" % (t, _targets(spec[1:-1]), v)
        return "%s %s = %.6g" % (t, _targets(spec[1:-1]), v)
    return "%s %s" % (t, _targets(spec[1:]))


def _targets(args):
    return ",".join(str(a) for a in args)


class _Axes:
    """Which coordinates are already tied together by the constraints on the
    copy, from horizontal and vertical lines, coincident points, points on
    the axes and horizontal or vertical distances.  A distance between two
    coordinates that are already tied is redundant, so it is not tried; this
    spares the solver one trial per redundant dimension (a rectangle's
    opposite sides, for instance).  It only predicts: the solver decides, and
    anything skipped is tried again if the sketch is still not fully defined
    afterwards."""

    def __init__(self):
        self.parent = {"x": {}, "y": {}}

    def copy(self):
        c = _Axes()
        c.parent = {k: dict(v) for k, v in self.parent.items()}
        return c

    def find(self, axis, key):
        par = self.parent[axis]
        par.setdefault(key, key)
        while par[key] != key:
            par[key] = par[par[key]]
            key = par[key]
        return key

    def union(self, axis, a, b):
        ra, rb = self.find(axis, a), self.find(axis, b)
        if ra != rb:
            self.parent[axis][rb] = ra

    def same(self, axis, a, b):
        return self.find(axis, a) == self.find(axis, b)

    @staticmethod
    def point(g, p):
        return "o" if g in (-1, -2) else (g, p)

    def add(self, spec):
        t = spec[0]
        P = self.point
        if t in ("Coincident",) or (t == "Tangent" and len(spec) == 5):
            for ax in "xy":
                self.union(ax, P(spec[1], spec[2]), P(spec[3], spec[4]))
        elif t == "Vertical" or t == "Horizontal":
            ax = "x" if t == "Vertical" else "y"
            if len(spec) == 2:
                self.union(ax, (spec[1], 1), (spec[1], 2))
            else:
                self.union(ax, P(spec[1], spec[2]), P(spec[3], spec[4]))
        elif t == "PointOnObject" and spec[3] in (-1, -2):
            self.union("y" if spec[3] == -1 else "x", P(spec[1], spec[2]), "o")
        elif t in ("DistanceX", "DistanceY"):
            ax = "x" if t == "DistanceX" else "y"
            if len(spec) == 4:
                self.union(ax, P(spec[1], spec[2]), "o")
            else:
                self.union(ax, P(spec[1], spec[2]), P(spec[3], spec[4]))

    def redundant(self, spec, lines=None):
        t = spec[0]
        P = self.point
        if t in ("DistanceX", "DistanceY"):
            ax = "x" if t == "DistanceX" else "y"
            if len(spec) == 4:
                return self.same(ax, P(spec[1], spec[2]), "o")
            return self.same(ax, P(spec[1], spec[2]), P(spec[3], spec[4]))
        if t in ("Horizontal", "Vertical") and len(spec) == 5:
            return self.same("y" if t == "Horizontal" else "x", P(spec[1], spec[2]), P(spec[3], spec[4]))
        if t == "PointOnObject" and spec[3] in (-1, -2):
            return self.same("y" if spec[3] == -1 else "x", P(spec[1], spec[2]), "o")
        if t == "Equal" and lines is not None:
            a, b = lines.get(spec[1]), lines.get(spec[2])
            if a and b and a[0] == b[0]:
                ax = a[0]
                other = "y" if ax == "x" else "x"
                if not (
                    self.same(other, (spec[1], 1), (spec[1], 2))
                    and self.same(other, (spec[2], 1), (spec[2], 2))
                ):
                    return False
                ca = {self.find(ax, (spec[1], 1)), self.find(ax, (spec[1], 2))}
                cb = {self.find(ax, (spec[2], 1)), self.find(ax, (spec[2], 2))}
                return ca == cb and len(ca) == 2
        return False


def _spec_of(c):
    """The part of an existing constraint that `_Axes` understands."""
    t = c.Type
    if t in ("Coincident",) and c.Second != -2000:
        return (t, c.First, c.FirstPos, c.Second, c.SecondPos)
    if t in ("Horizontal", "Vertical"):
        if c.Second == -2000:
            return (t, c.First)
        return (t, c.First, c.FirstPos, c.Second, c.SecondPos)
    if t == "PointOnObject":
        return (t, c.First, c.FirstPos, c.Second)
    if t in ("DistanceX", "DistanceY") and c.Driving and c.IsActive:
        if c.Second == -2000:
            return (t, c.First, c.FirstPos, 0.0)
        return (t, c.First, c.FirstPos, c.Second, c.SecondPos, 0.0)
    return None


def _equal_specs(existing):
    """Existing Equal constraints in the form `_dimension_proposals` reads, so
    that it does not propose a size for the second of two lines (or arcs) the
    sketch already makes equal: next to a near-axis extent such a size is not
    exactly redundant, the solver counts it as independent, and the pair is
    ill conditioned."""
    return [("Equal", c.First, c.Second) for c in existing if c.Type == "Equal" and c.Driving and c.IsActive]


# ---------------------------------------------------------------------------
# Building, verifying and applying
# ---------------------------------------------------------------------------


def _clean(st):
    return st.code == 0 and not (st.conflicts or st.redundant or st.partial or st.malformed)


def _constraint_row(c):
    return (c.Type, c.Name, c.First, c.FirstPos, c.Second, c.SecondPos, c.Third, c.ThirdPos, float(c.Value), bool(c.Driving), bool(c.IsActive))


def _nudged(geometry, size, seed=1):
    """`geometry` with every item translated by a small deterministic offset
    (a fixed linear congruential sequence, so it does not depend on the
    process).  PlaneGCS sometimes returns "no convergence" (-1) with DOF 0 and
    nothing flagged when the start already satisfies every constraint to
    rounding, and converges from the same start moved by 1e-9 or more (run 052,
    dovetail); the constraints are not what is wrong, so a retried solve starts
    from this.  Whatever comes out is checked against the drawn geometry like
    any other result.  Items that cannot be translated are left as they are."""
    if not size:
        return list(geometry)
    state = seed * 2654435761 % 2**32
    out = []
    for g in geometry:
        try:
            g = g.copy()
            state = (state * 1664525 + 1013904223) % 2**32
            dx = (state / 2**32 * 2 - 1) * size
            state = (state * 1664525 + 1013904223) % 2**32
            dy = (state / 2**32 * 2 - 1) * size
            g.translate(FreeCAD.Vector(dx, dy, 0))
        except Exception:
            pass
        out.append(g)
    return out


def _stable(sk, runs=GATE_RUNS):
    """`runs` consecutive solves of `sk`, each clean with DOF 0.  PlaneGCS can
    return -1 at DOF 0 with nothing flagged, differently from process to process,
    on a few ill-conditioned sets whose first solves are fine; a sketch that
    does that after the user saves and reopens it is worse than no offer."""
    for _ in range(runs):
        st = _Status(sk)
        if not (_clean(st) and st.dof == 0):
            return False
    return True


def _serialised_stable(sk, runs=GATE_RUNS):
    """`_stable` on a copy rebuilt from the serialised geometry and constraints
    of `sk` in a hidden document, the nearest thing to saving and reopening."""
    scratch = _Scratch(sk)
    with contextlib.closing(scratch):
        return _stable(scratch.copy(), runs)


def _apply_batches(sk, specs, batches, start_geometry):
    """Add `specs` to `sk` in the batches the builder accepted them in.  The
    drawn geometry is put back before each batch because the solver result
    depends on where it starts.  Used for throwaway copies and the real
    sketch alike."""
    pos = 0
    for size in batches:
        sk.Geometry = start_geometry
        sk.addConstraint([_make(s) for s in specs[pos : pos + size]])
        pos += size
    return _Status(sk)


class _Build:
    """Incremental construction of one candidate on a throwaway copy."""

    def __init__(self, scratch, deadline):
        self.sk = scratch.copy()
        self.deadline = deadline
        self.start = list(self.sk.Geometry)
        self.sig0 = _geometry_signature(self.sk)
        self.base_rows = [_constraint_row(c) for c in self.sk.Constraints]
        self.status = _Status(self.sk)
        self.dof = self.status.dof
        self.lines = {}
        self.base_axes = _Axes()
        for c in self.sk.Constraints:
            sp = _spec_of(c)
            if sp:
                self.base_axes.add(sp)
        self.axes = self.base_axes.copy()
        self.specs = []
        self.batches = []
        self.dirty = False
        self.solves = 0

    @property
    def usable(self):
        return _clean(self.status)

    def _accept(self, st, specs):
        self.dof = st.dof
        self.status = st
        self.specs.extend(specs)
        self.batches.append(len(specs))
        for sp in specs:
            self.axes.add(sp)

    def _remove_new(self, n0):
        if len(self.sk.Constraints) > n0:
            self.sk.delConstraints(list(range(n0, len(self.sk.Constraints))), False, True)
        self.dirty = True

    def try_add(self, specs):
        """Add `specs` together; accept all or none."""
        if time.time() > self.deadline:
            raise _Timeout()
        n0 = len(self.sk.Constraints)
        if self.dirty:
            self.sk.Geometry = self.start
            self.dirty = False
        try:
            self.sk.addConstraint([_make(s) for s in specs])
            st = _Status(self.sk)
        except Exception:
            st = None
        self.solves += 1
        if st is not None and self._good(st, specs):
            self._accept(st, specs)
            return True
        self._remove_new(n0)
        return False

    def _good(self, st, specs):
        return (
            _clean(st)
            and st.dof <= self.dof - len(specs)
            and _max_move(self.sig0, _geometry_signature(self.sk)) <= MOVE_TOLERANCE
        )

    def add_chunk(self, specs):
        """Add `specs` together, then drop the newest constraint the solver
        flags (a conflict first, then redundancy, then partial redundancy) and
        solve again, until the rest is clean.  One at a time keeps one member
        of every redundant group.  The solver sometimes blames only earlier
        constraints; then the newest of the batch goes, which may be an
        independent one, and every constraint dropped that way is tried again
        on its own afterwards.  What is accepted must pass the same test as a
        single trial; otherwise the chunk is bisected."""
        if not specs:
            return
        if time.time() > self.deadline:
            raise _Timeout()
        n0 = len(self.sk.Constraints)
        if self.dirty:
            self.sk.Geometry = self.start
            self.dirty = False
        live = list(specs)
        uncertain = []
        failed = False
        try:
            self.sk.addConstraint([_make(s) for s in specs])
            st = _Status(self.sk)
            self.solves += 1
            while live:
                flagged = st.conflicts + st.redundant + st.partial
                if not flagged:
                    break
                fresh = [i for i in flagged if i >= n0]
                pick = max(fresh) if fresh else len(self.sk.Constraints) - 1
                self.sk.delConstraints([pick], False, True)
                dropped = live.pop(pick - n0)
                if not fresh:
                    uncertain.append(dropped)
                st = _Status(self.sk)
                self.solves += 1
                if time.time() > self.deadline:
                    raise _Timeout()
        except _Timeout:
            self._remove_new(n0)
            raise
        except Exception:
            failed = True
        if not live and not failed:
            self._remove_new(n0)
        elif not failed and self._good(st, live):
            self._accept(st, live)
        else:
            self._remove_new(n0)
            if len(specs) > 1:
                mid = len(specs) // 2
                self.add_chunk_safe(specs[:mid])
                self.add_chunk_safe(specs[mid:])
            return
        for d in uncertain:
            if self.dof > 0:
                self.add_chunk_safe([d])

    def add_chunk_safe(self, specs):
        """Bisection fallback: all-or-nothing trials."""
        if not specs:
            return
        if self.try_add(specs) or len(specs) == 1:
            return
        mid = len(specs) // 2
        self.add_chunk_safe(specs[:mid])
        self.add_chunk_safe(specs[mid:])

    def add_relations(self, specs):
        specs = [sp for sp in specs if not self.axes.redundant(sp, self.lines)]
        for i in range(0, len(specs), _MAX_BATCH):
            self.add_chunk(specs[i : i + _MAX_BATCH])

    def free_geometry(self):
        """Ids of the geometry that still has a free parameter, from the
        solver's own list; None when the solver does not say."""
        try:
            if self.dirty:
                self.sk.Geometry = self.start
                self.dirty = False
            self.sk.solve()
            free = {g for g, _ in self.sk.getGeometryWithDependentParameters()}
        except Exception:
            return None
        return free or None

    @staticmethod
    def _targets(spec):
        t = spec[0]
        if t in ("DistanceX", "DistanceY"):
            return {spec[1]} | ({spec[3]} if len(spec) == 6 else set())
        return {spec[1]}

    def add_dimensions(self, specs):
        """Try the proposals in order, a chunk of at most `dof` at a time.
        Only proposals on geometry that still has a free parameter are
        tried; what the solver rejects is redundant for good (constraints are
        only ever added), so it is not tried again.  What the axis predictor
        skipped is tried last, in case the prediction was wrong."""
        pending = list(specs)
        deferred = []
        for use_predictor in (True, False):
            if not use_predictor:
                pending, deferred = deferred, []
            while pending and self.dof > 0:
                size = max(1, min(_MAX_BATCH, self.dof))
                free = self.free_geometry()
                ax = self.axes.copy()
                chunk, rest = [], []
                for k, sp in enumerate(pending):
                    if len(chunk) >= size:
                        rest.extend(pending[k:])
                        break
                    if free is not None and not (self._targets(sp) & free):
                        continue  # nothing free on it: redundant
                    if use_predictor and ax.redundant(sp):
                        deferred.append(sp)
                        continue
                    if use_predictor:
                        ax.add(sp)
                    chunk.append(sp)
                pending = rest
                if not chunk:
                    break
                self.add_chunk(chunk)


def _build_scheme(scratch, model, existing, scheme, deadline, cache=None):
    """Build one candidate.  `cache` maps a relation order to the relations
    that an earlier scheme with that order accepted; they are added in one
    batch instead of being searched for again (the first trial must pass the
    same test as any other, otherwise the search runs normally)."""
    b = _Build(scratch, deadline)
    if not b.usable:
        return b, "input"
    rel = _detect(model, existing)
    for m in model:
        if m.kind == "line" and m.d is not None:
            if abs(m.pts[2][1] - m.pts[1][1]) <= LENGTH_TOLERANCE:
                b.lines[m.i] = ("x",)
            elif abs(m.pts[2][0] - m.pts[1][0]) <= LENGTH_TOLERANCE:
                b.lines[m.i] = ("y",)
    order = ["coincident", "symmetric", "onobject", "hv", "tangent", "parallel", "equal", "symmetric_axes"]
    if scheme == "symmetric":
        order = ["coincident", "symmetric", "symmetric_axes", "onobject", "hv", "tangent", "parallel", "equal"]
    key = tuple(order)
    try:
        seeded = cache is not None and key in cache and cache[key] and b.try_add(cache[key])
        if not seeded:
            for phase in order:
                if b.dof == 0:
                    break
                b.add_relations(rel[phase])
        if cache is not None and key not in cache:
            cache[key] = [sp for sp in b.specs if not _is_datum(sp)]
        if b.dof > 0:
            dims = _dimension_proposals(model, _equal_specs(existing) + b.specs, scheme)
            b.add_dimensions(dims)
    except _Timeout:
        return b, "time"
    return b, None


def _candidate_key(specs):
    return frozenset(_spec_key(s) for s in specs)


def _verify(scratch, specs, batches, keep, stability_budget=None):
    """Apply to a brand-new copy and re-solve, first in one batch and, if that
    does not pass, in the batches the builder used.  A plan that passes is
    applied to `STABILITY_RUNS` further fresh copies: PlaneGCS gives different
    answers from run to run on a few ill-conditioned systems (the same
    constraints on the same geometry solved 15 times out of 30 in one process
    and 30 out of 30 in the next), and a candidate that passes once but not
    always must not be offered.  The repeats are skipped when the whole first
    pass (the batch apply, the solves and the re-solve gate, which is itself up
    to 20 solves) took longer than `stability_budget` seconds (large sketches),
    and the gate inside a pass is skipped when its batch apply alone took longer
    than that.  Either skip is recorded in the facts as ``gate`` (``"full"``,
    ``"repeats_skipped"`` or ``"skipped"``) with a ``gate_reason``.  Returns the facts measured,
    including the batches that passed, or None when the hard bar is not met."""
    budget = STABILITY_BUDGET if stability_budget is None else stability_budget
    for trial in ([len(specs)], list(batches)) if len(batches) > 1 else (list(batches),):
        t0 = time.time()
        facts = _verify_pass(scratch, specs, trial, keep, budget)
        if facts is None:
            continue
        elapsed = time.time() - t0
        repeats = STABILITY_RUNS if elapsed <= budget else 0
        if any(_verify_once(scratch, specs, trial, keep, facts["nudge"], budget) is None for _ in range(repeats)):
            continue
        facts["batches"] = trial
        facts["runs"] = 1 + repeats
        reasons = []
        gate_ran = facts.pop("gate_ran")
        if not gate_ran:
            reasons.append("the re-solve gate (%d solves on the sketch and on a serialised copy) was not run because applying the constraints took %.2f s, over the %.2f s stability budget" % (GATE_RUNS, facts.pop("apply_seconds"), budget))
        else:
            facts.pop("apply_seconds")
        if not repeats:
            reasons.append("the %d repeat passes on fresh copies were not run because the first pass took %.2f s, over the %.2f s stability budget" % (STABILITY_RUNS, elapsed, budget))
        facts["gate"] = "skipped" if not gate_ran else ("repeats_skipped" if reasons else "full")
        facts["gate_reason"] = "; ".join(reasons) or None
        return facts
    return None


def _verify_pass(scratch, specs, batches, keep, budget=STABILITY_BUDGET):
    """The first verification pass, from the drawn geometry exactly.  There is no
    nudged fallback here: a plan that only solves from a start moved by `NUDGE`
    is ill-conditioned, its result is the kind that later re-solves with -1 after
    the user saves and reopens, and offering such plans also made the one
    sensitive dovetail plan far more common (run 052).  ``apply_candidate``
    still retries from a nudged start, but its result has to pass the re-solve
    gate too."""
    facts = _verify_once(scratch, specs, batches, keep, 0.0, budget)
    if facts is not None:
        facts["nudge"] = 0.0
    return facts


def _verify_once(scratch, specs, batches, keep, nudge=0.0, budget=STABILITY_BUDGET):
    fresh = scratch.copy()
    try:
        start = list(fresh.Geometry)
        sig0 = _geometry_signature(fresh)
        rows0 = [_constraint_row(c) for c in fresh.Constraints]
        t0 = time.time()
        st = _apply_batches(fresh, specs, batches, _nudged(start, nudge))
        spent = time.time() - t0
        cheap = spent <= budget
        rows = [_constraint_row(c) for c in fresh.Constraints]
        if len(rows) != len(rows0) + len(specs) or rows[: len(rows0)] != rows0:
            return None
        if not _clean(st) or st.dof != 0:
            return None
        move = _max_move(sig0, _geometry_signature(fresh))
        if move > MOVE_TOLERANCE:
            return None
        again = _Status(fresh)  # a second solve must agree
        if not _clean(again) or again.dof != 0:
            return None
        if any(k >= len(rows0) or rows[k] != rows0[k] for k in keep):
            return None
        if cheap and not (_stable(fresh) and _serialised_stable(fresh)):
            return None  # the re-solve gate; a nudged start rarely passes it
        return {"max_move": move, "dof": st.dof, "gate_ran": cheap, "apply_seconds": spent}
    finally:
        _discard(fresh)


def _describe(scheme, specs, batches, facts):
    cons = []
    for s in specs:
        cons.append(
            {
                "type": s[0],
                "args": list(s[1:]),
                "role": "dimension" if _is_datum(s) else "relation",
                "text": _text(s),
            }
        )
    return {
        "scheme": scheme,
        "constraints": cons,
        "relations": sum(1 for s in specs if not _is_datum(s)),
        "dimensions": sum(1 for s in specs if _is_datum(s)),
        "batches": list(facts["batches"]),
        "dof_after": facts["dof"],
        "verification_runs": facts["runs"],
        "max_move": facts["max_move"],
        "verified": True,
        "gate": facts["gate"],
        "gate_reason": facts["gate_reason"],
    }


def _schemes(prefer, max_candidates):
    if prefer is None:
        names = list(SCHEMES)
    elif isinstance(prefer, str):
        names = [prefer] + [s for s in SCHEMES if s != prefer]
    else:
        names = list(prefer) + [s for s in SCHEMES if s not in prefer]
    for n in names:
        if n not in SCHEMES:
            raise AutoDefineError("unknown scheme %r (one of %s)" % (n, ", ".join(SCHEMES)))
    return names


_UNSUPPORTED = {
    "ellipse": "ellipse: its centre and axes can be dimensioned only through its internal geometry, which this sketch has not exposed (expose it with exposeInternalGeometry on the ellipse, then call propose again)",
    "ellipse_arc": "ellipse arc: its centre and axes can be dimensioned only through its internal geometry, which this sketch has not exposed (expose it with exposeInternalGeometry on the arc, then call propose again)",
    "bspline": "B-spline: its poles are not exposed as points, so nothing can dimension its shape (expose its control points or use another curve)",
    "other": "hyperbola, parabola or other curve: no relation or dimension of the supported kinds fixes its shape",
}
_UNSUPPORTED_ID = {"Part::GeomArcOfHyperbola": "hyperbola arc", "Part::GeomArcOfParabola": "parabola arc"}


def _unclosed_reason(b, model):
    """Why the DOF the builder could not remove is left: the geometry kinds
    that still have a free parameter and that the generator cannot dimension,
    each with the geometry id, or the generic statement when it is something
    else."""
    free = b.free_geometry()
    parts = []
    if free:
        for m in model:
            if m.i in free and m.kind in _UNSUPPORTED:
                what = _UNSUPPORTED_ID.get(m.tid)
                text = _UNSUPPORTED[m.kind]
                if what:
                    text = "%s: no relation or dimension of the supported kinds fixes its shape" % what
                parts.append("geometry %d is an %s" % (m.i, text) if text[0] in "aeiou" else "geometry %d is a %s" % (m.i, text))
    if parts:
        return "no relation or dimension of the supported kinds fixes the rest; " + "; ".join(parts)
    return "no relation or dimension of the supported kinds fixes the rest"


@contextlib.contextmanager
def _quiet():
    """Silence the console's warning and notification categories while the
    scratch copies are solved: the solver reports every partly redundant
    trial constraint by naming the scratch sketch ("Copy"), which the user
    never made.  The categories of every registered observer are put back in
    ``finally``, so nothing changes after ``propose`` returns or raises.

    Known limit: the switch is process-wide, so a warning or notification
    printed by another thread (or by a callback) while ``propose`` runs is
    lost too (6 of 36 in a measured run).  ``Err``, ``Critical``, ``Msg`` and
    ``Log`` are untouched.  There is no narrower mechanism without C++ changes:
    ``FreeCAD.Console`` exposes only ``SetStatus``/``GetStatus``/``GetObservers``,
    which flip a per-observer flag read by every thread; there is no per-thread
    or per-sender filter, no capture or scoped-suppression call, and a Python
    object cannot be registered as an observer.  Callers that need every
    warning should not run ``propose`` while another thread prints them."""
    saved = []
    try:
        for name in FreeCAD.Console.GetObservers():
            for kind in ("Wrn", "Notification"):
                was = FreeCAD.Console.GetStatus(name, kind)
                if was:
                    saved.append((name, kind))
                    FreeCAD.Console.SetStatus(name, kind, False)
        yield
    finally:
        for name, kind in saved:
            FreeCAD.Console.SetStatus(name, kind, True)


def propose(sketch, keep=None, prefer=None, max_candidates=3, time_budget=120.0, stability_budget=None):
    """Propose constraint sets that make `sketch` fully defined.

    Returns a JSON-serialisable dict: ``dof`` (before), ``status`` (``ok``,
    ``already_defined``, ``cannot_close`` or ``input_inconsistent``),
    ``candidates`` (each with its ``constraints``, ``scheme``, ``verified`` and
    the facts measured on a fresh copy) and ``unclosed`` (for every scheme that
    could not reach DOF 0: the DOF that was left and why).  `keep` is a list of
    constraint indices whose presence is verified; existing constraints are
    never changed whether listed or not.  `prefer` is a scheme name or a list
    of names tried first (``baseline``, ``chain``, ``symmetric``).
    `stability_budget` (seconds, default `STABILITY_BUDGET`) is the longest a
    verification step may take before the repeat passes and the re-solve gate
    are skipped; each candidate's ``gate`` says whether that happened."""
    if getattr(sketch, "TypeId", None) != "Sketcher::SketchObject":
        raise AutoDefineError("propose() needs a Sketcher::SketchObject")
    if isinstance(max_candidates, bool) or not isinstance(max_candidates, int) or max_candidates < 1:
        raise AutoDefineError("max_candidates must be a whole number of at least 1")
    names = _schemes(prefer, max_candidates)
    keep = sorted(set(keep or ()))
    ncons = len(sketch.Constraints)
    if any((not isinstance(k, int)) or k < 0 or k >= ncons for k in keep):
        raise AutoDefineError("keep refers to a constraint that does not exist")
    with _quiet():
        return _propose(sketch, names, keep, max_candidates, time_budget, stability_budget)


def _propose(sketch, names, keep, max_candidates, time_budget, stability_budget=None):
    deadline = time.time() + time_budget
    scratch = _Scratch(sketch)
    try:
        probe = scratch.copy()
        stored = _geometry_signature(probe)
        st = _Status(probe)
        drift = _max_move(stored, _geometry_signature(probe))
        _discard(probe)
        result = {"dof": st.dof, "status": "ok", "candidates": [], "unclosed": []}
        if not _clean(st):
            result["status"] = "input_inconsistent"
            result["unclosed"].append({"scheme": None, "remaining_dof": st.dof, "reason": "the sketch already has a conflict, redundancy or malformed constraint"})
            return result
        if st.dof == 0:
            result["status"] = "already_defined"
            return result
        if drift > MOVE_TOLERANCE:
            # Not solved on the copy first: a candidate would be verified against
            # geometry the user's sketch does not hold, and ``apply_candidate``
            # holds the stored geometry fixed.
            result["status"] = "input_inconsistent"
            result["unclosed"].append(
                {
                    "scheme": None,
                    "remaining_dof": st.dof,
                    "reason": "the stored geometry does not satisfy the sketch's own constraints (solving moves it by %.3g mm): solve the sketch first, then call propose again" % drift,
                }
            )
            return result
        base = scratch.copy()
        try:
            model = _model(base)
            existing = list(base.Constraints)
        finally:
            _discard(base)
        seen = set()
        cache = {}
        for scheme in names:
            if len(result["candidates"]) >= max_candidates:
                break
            b, why = _build_scheme(scratch, model, existing, scheme, deadline, cache)
            try:
                if why == "input":
                    continue
                if b.dof > 0:
                    result["unclosed"].append(
                        {
                            "scheme": scheme,
                            "remaining_dof": b.dof,
                            "reason": "time budget exhausted" if why == "time" else _unclosed_reason(b, model),
                        }
                    )
                    continue
                key = _candidate_key(b.specs)
                if key in seen:
                    continue
                facts = _verify(scratch, b.specs, b.batches, keep, stability_budget)
                if facts is None:
                    result["unclosed"].append({"scheme": scheme, "remaining_dof": b.dof, "reason": "verification on a fresh copy failed; candidate dropped"})
                    continue
                seen.add(key)
                result["candidates"].append(_describe(scheme, b.specs, b.batches, facts))
            finally:
                _discard(b.sk)
        if not result["candidates"]:
            result["status"] = "cannot_close"
        return result
    finally:
        scratch.close()


def apply_candidate(sketch, candidate, stability_budget=None):
    """Apply a candidate returned by ``propose`` to the real sketch.  The
    result is checked on the real sketch; if it is not clean with DOF 0 the
    constraints are removed again and the drawn geometry is put back.  The
    solver is not repeatable on a few ill-conditioned systems, so a result
    that is not clean is tried up to `APPLY_ATTEMPTS` times (the retries start
    from the drawn geometry moved by `NUDGE`; the result must still be clean,
    DOF 0, solve again cleanly and be within `MOVE_TOLERANCE` of the
    drawn geometry) before ``AutoDefineError`` is raised (and the sketch is
    left as it was).  Every result, from an exact or a nudged start, must also
    pass the re-solve gate before it is accepted: `GATE_RUNS` consecutive clean
    solves with DOF 0 of the sketch and of a copy rebuilt from its serialised
    content.  A result that fails it is rolled back like any other miss.  The
    full gate costs up to 20 solves, so when applying the constraints took
    longer than `stability_budget` seconds (default `STABILITY_BUDGET`; large
    sketches) the gate is reduced, not skipped: `REDUCED_GATE_RUNS` clean DOF 0
    solves of the real sketch, with no serialised copy.  A reduced gate catches
    only a result that fails most solves.  Returns a dict: ``dof``, ``added``,
    ``gate`` (``"full"`` or ``"reduced"``), ``gate_runs`` (solves asked for
    on the sketch) and ``gate_reason`` (None for a full gate)."""
    budget = STABILITY_BUDGET if stability_budget is None else stability_budget
    specs = [tuple([c["type"]] + list(c["args"])) for c in candidate["constraints"]]
    batches = candidate.get("batches") or [len(specs)]
    n0 = len(sketch.Constraints)
    start = list(sketch.Geometry)
    sig0 = _geometry_signature(sketch)

    def roll_back():
        if len(sketch.Constraints) > n0:
            sketch.delConstraints(list(range(n0, len(sketch.Constraints))), False, True)
        sketch.Geometry = start

    for attempt in range(APPLY_ATTEMPTS):
        cheap, spent = True, 0.0
        try:
            t0 = time.time()
            st = _apply_batches(sketch, specs, batches, _nudged(start, NUDGE if attempt else 0.0, attempt))
            spent = time.time() - t0
            cheap = spent <= budget
            if _clean(st):
                again = _Status(sketch)  # a second solve must agree
                st = st if _clean(again) else again
            ok = _clean(st) and st.dof == 0 and _max_move(sig0, _geometry_signature(sketch)) <= MOVE_TOLERANCE
            if ok and cheap:  # the re-solve gate, on the sketch and on its serialised copy
                ok = _stable(sketch) and _serialised_stable(sketch)
            elif ok:  # too slow for 20 solves: a reduced gate on the real sketch, never none
                ok = _stable(sketch, REDUCED_GATE_RUNS)
        except Exception as exc:
            roll_back()
            raise AutoDefineError("the candidate could not be applied: %s" % exc)
        if ok:
            if cheap:
                return {"dof": st.dof, "added": len(specs), "gate": "full", "gate_runs": GATE_RUNS, "gate_reason": None}
            reason = "applying the constraints took %.2f s, over the %.2f s stability budget, so the %d-solve gate on the sketch and a serialised copy was cut to %d solves of the sketch" % (spent, budget, GATE_RUNS, REDUCED_GATE_RUNS)
            return {"dof": st.dof, "added": len(specs), "gate": "reduced", "gate_runs": REDUCED_GATE_RUNS, "gate_reason": reason}
        roll_back()
    raise AutoDefineError("the candidate did not solve cleanly on this sketch; it was rolled back")
