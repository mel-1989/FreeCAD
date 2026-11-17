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

"""Design-intent dimension proposals for ``SketcherAutoDefine`` (F11 slice 1).

``SketcherAutoDefine`` adds relations first and then asks this module for an
ordered list of dimension specs; it tries them in order and keeps each one the
solver accepts, so the order is the choice.  This module decides the order.

The behaviour comes from general drafting practice (size an outline once with
its overall width and height, give holes a diameter and a position from the
datum, size a feature from its neighbour rather than from the origin, give an
inclined edge an angle against the edge it meets, carry dimensions on
construction lines) and from the sketches in this repository's own test data.
No other CAD product's documentation, binaries or file formats were used.

Every proposal is a typed item ``(kind, spec)``.  ``rank`` is the one place
that turns the typed pool into an order, and each scheme is a table of
priorities per kind, so the three intent schemes differ in what they dimension
first and not only in a tie-break:

``intent``
    sizes, dimensions carried by construction lines, the overall width and
    height of the outline with its position from the origin, angles against a
    neighbouring line, offsets between neighbouring features, line lengths.
``intent_offset``
    sizes, construction-line dimensions, then offsets between neighbouring
    features before the overall size (each feature is placed from the nearest
    one already placed), with the other end of each tie broken the other way.
``intent_origin``
    sizes, then every feature placed from the origin, then the overall size,
    angles and lengths.

The proposals never read the original constraints of a sketch that was stripped
for a test; they are computed from the geometry and from the relations that are
on the sketch now.
"""

import math

INTENT_SCHEMES = ("intent", "intent_offset", "intent_origin")

_TOL = 1e-7
NEAR_AXIS = 0.03

# Priority of each proposal kind per scheme (lower first).  A kind that is
# missing from a table goes last, in generation order.
PRIORITY = {
    "intent": {
        "size": 0,
        "carrier": 1,
        "gap": 1.5,
        "extent": 2,
        "extent_origin": 3,
        "angle_nb": 4,
        "offset": 5,
        "length": 6,
        "angle_axis": 7,
        "arcspan": 8,
        "origin": 9,
    },
    "intent_offset": {
        "size": 0,
        "carrier": 1,
        "gap": 1.5,
        "offset": 2,
        "extent": 3,
        "extent_origin": 4,
        "angle_nb": 5,
        "length": 6,
        "angle_axis": 7,
        "arcspan": 8,
        "origin": 9,
    },
    "intent_origin": {
        "size": 0,
        "origin": 1,
        "carrier": 2,
        "extent": 3,
        "angle_nb": 4,
        "length": 5,
        "offset": 6,
        "extent_origin": 7,
        "angle_axis": 8,
        "arcspan": 9,
    },
}


def rank(pool, scheme):
    """Order the typed proposals of `pool` for `scheme`: by the scheme's
    priority of the kind, stable within a kind."""
    table = PRIORITY[scheme]
    last = len(table)
    return [item for _, item in sorted(((table.get(k, last), (k, s)) for k, s in pool), key=lambda t: t[0])]


def _signed(kind, g1, p1, g2, p2, value):
    if value < 0:
        return (kind, g2, p2, g1, p1, -value)
    return (kind, g1, p1, g2, p2, value)


def _drawn(model):
    return [m for m in model if not m.construction and m.pts and not m.internal]


def _scale(model):
    xs = [v for m in model for p in m.pts.values() for v in p]
    return max(1.0, max((abs(v) for v in xs), default=1.0))


def _is_hv(m):
    (x1, y1), (x2, y2) = m.pts[1], m.pts[2]
    return abs(y2 - y1) <= _TOL or abs(x2 - x1) <= _TOL


def _is_line(m):
    return m.kind == "line" and m.d is not None and m.length > 10 * _TOL


def _length_spec(m):
    """The length of line `m`: a horizontal or vertical distance for a line
    near an axis (the dominant extent is well conditioned), else a distance."""
    (x1, y1), (x2, y2) = m.pts[1], m.pts[2]
    dx, dy = x2 - x1, y2 - y1
    if abs(dy) <= _TOL or (min(abs(dx), abs(dy)) < NEAR_AXIS * m.length and abs(dx) >= abs(dy)):
        return _signed("DistanceX", m.i, 1, m.i, 2, dx)
    if abs(dx) <= _TOL or min(abs(dx), abs(dy)) < NEAR_AXIS * m.length:
        return _signed("DistanceY", m.i, 1, m.i, 2, dy)
    return ("Distance", m.i, m.length)


def _extent_points(model, axis, scale):
    """The overall extent of the drawn outline along `axis` (0 for x, 1 for
    y): (lo, hi, lo points, hi points), or None when a circle or arc reaches
    beyond the points that can be dimensioned (then no point pair carries the
    overall size)."""
    pts = []
    for m in _drawn(model):
        for p, xy in m.pts.items():
            if p == 3 and m.kind in ("circle", "arc", "ellipse", "ellipse_arc"):
                continue
            if m.kind == "point" or m.kind in ("line", "arc"):
                pts.append((m.i, p, xy))
    if len(pts) < 2:
        return None
    lo = min(xy[axis] for _, _, xy in pts)
    hi = max(xy[axis] for _, _, xy in pts)
    tol = 1e-6 * scale
    for m in _drawn(model):
        if m.kind in ("circle", "arc", "ellipse", "ellipse_arc"):
            if m.kind == "circle" or m.kind == "arc":
                if m.c[axis] - m.r < lo - tol or m.c[axis] + m.r > hi + tol:
                    # an arc may not reach its full circle; only a circle or an
                    # arc that actually passes the extreme point matters, and
                    # the arc's own end points were counted already
                    if m.kind == "circle" or _arc_reaches(m, axis, lo, hi, tol):
                        return None
            else:
                return None
    if hi - lo <= 10 * tol:
        return None
    lop = [(g, p, xy) for g, p, xy in pts if xy[axis] <= lo + tol]
    hip = [(g, p, xy) for g, p, xy in pts if xy[axis] >= hi - tol]
    return lo, hi, lop, hip


def _arc_reaches(m, axis, lo, hi, tol):
    """True if the arc passes beyond lo or hi along `axis` (a quadrant point
    of the arc lies outside the end-point extent)."""
    a0 = math.atan2(m.pts[1][1] - m.c[1], m.pts[1][0] - m.c[0])
    a1 = math.atan2(m.pts[2][1] - m.c[1], m.pts[2][0] - m.c[0])
    span = (a1 - a0) % (2 * math.pi)
    for k in range(4):
        ang = k * math.pi / 2
        if (ang - a0) % (2 * math.pi) <= span:
            q = (m.c[0] + m.r * math.cos(ang), m.c[1] + m.r * math.sin(ang))
            if q[axis] < lo - tol or q[axis] > hi + tol:
                return True
    return False


def _extent_proposals(model, flip):
    """Overall width and height of the outline.  `flip` swaps which of the
    equally good end points and edges is used."""
    out = []
    scale = _scale(model)
    for axis, kind in ((0, "DistanceX"), (1, "DistanceY")):
        ext = _extent_points(model, axis, scale)
        if ext is None:
            continue
        lo, hi, lop, hip = ext
        other = 1 - axis
        # a single edge that spans the whole extent carries the size
        span = [m for m in _drawn(model) if _is_line(m) and _is_hv(m) and abs(m.pts[1][other] - m.pts[2][other]) <= _TOL and min(m.pts[1][axis], m.pts[2][axis]) <= lo + 1e-6 * scale and max(m.pts[1][axis], m.pts[2][axis]) >= hi - 1e-6 * scale]
        if span:
            span.sort(key=lambda m: (m.pts[1][other], m.i))
            m = span[0] if flip else span[-1]
            out.append(("extent", _signed(kind, m.i, 1, m.i, 2, m.pts[2][axis] - m.pts[1][axis])))
            low = [(g, p, xy) for g, p, xy in lop if g == m.i]
            low = low[0] if low else lop[0]
        else:
            lop.sort(key=lambda t: (_kind_order(model, t[0]), t[0], t[1]))
            hip.sort(key=lambda t: (_kind_order(model, t[0]), t[0], t[1]))
            a = lop[-1] if flip else lop[0]
            b = hip[-1] if flip else hip[0]
            if a[0] == b[0] and a[1] == b[1]:
                continue
            out.append(("extent", (kind, a[0], a[1], b[0], b[1], hi - lo)))
            low = a
        out.append(("extent_origin", (kind, low[0], low[1], low[2][axis])))
    return out


def _kind_order(model, g):
    return 0 if model[g].kind == "line" else 1


def _centres(model):
    return [m for m in _drawn(model) if m.kind in ("circle", "arc", "ellipse", "ellipse_arc") and 3 in m.pts]


def _anchor(m):
    """The point of `m` that places it: the centre of a curve, else the start
    of a line or the point itself."""
    if 3 in m.pts:
        return 3, m.pts[3]
    p = min(m.pts)
    return p, m.pts[p]


def _nested_ids(model):
    """Ids of the line elements in closed-up pieces that sit inside another
    piece's bounding box close to its edges (a wall, a frame, an offset
    loop): such a piece is sized and placed from the piece around it."""
    els = [m for m in _drawn(model) if m.kind == 'line' and m.d is not None]
    parent = {m.i: m.i for m in els}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    scale = _scale(model)
    tol = 1e-6 * scale
    for a in els:
        for b in els:
            if a.i < b.i and any(abs(p[0] - q[0]) <= tol and abs(p[1] - q[1]) <= tol for p in a.pts.values() for q in b.pts.values()):
                parent[find(b.i)] = find(a.i)
    pieces = {}
    for m in els:
        pieces.setdefault(find(m.i), []).append(m)
    box = {}
    for r, ms in pieces.items():
        xs = [p[0] for m in ms for p in m.pts.values()]
        ys = [p[1] for m in ms for p in m.pts.values()]
        box[r] = (min(xs), min(ys), max(xs), max(ys))
    out = set()
    for r, (x0, y0, x1, y1) in box.items():
        for q, (X0, Y0, X1, Y1) in box.items():
            if q == r:
                continue
            margin = min(x0 - X0, y0 - Y0, X1 - x1, Y1 - y1)
            if margin <= tol:
                continue
            if max(x0 - X0, y0 - Y0, X1 - x1, Y1 - y1) <= 0.25 * min(X1 - X0, Y1 - Y0):
                out.update(m.i for m in pieces[r])
                break
    return out


def _offset_proposals(model, flip, curves, nested_only=False):
    """Place each feature from the nearest feature already placed: the
    feature's anchor points are tied to the closest point of a placed
    element by a horizontal and a vertical distance (a zero offset is an
    alignment, which a relation already gives)."""
    els = [m for m in _drawn(model) if m.pts]
    if len(els) < 2:
        return []
    movers = set(m.i for m in els if curves or m.kind in ("line", "point"))
    if nested_only:
        nested = _nested_ids(model)
        movers &= nested
        if not movers:
            return []
    scale = _scale(model)
    out = []
    # start from the element holding the lowest-left point; the rest follow
    # nearest first
    if nested_only:
        placed = [m for m in els if m.i not in movers]
        left = [m for m in els if m.i in movers]
    else:
        start = min(els, key=lambda m: (min((xy[0], xy[1]) for xy in m.pts.values()), m.i))
        placed = [start]
        left = [m for m in els if m is not start]

    def pts_of(m):
        return [(m.i, p, xy) for p, xy in sorted(m.pts.items())]

    def gap(m):
        best = None
        for g, p, xy in pts_of(m):
            for h, q, zy in (t for e in placed for t in pts_of(e)):
                d = math.hypot(xy[0] - zy[0], xy[1] - zy[1])
                key = (round(d, 9), (-h if flip else h), q, g, p)
                if best is None or key < best[0]:
                    best = (key, (g, p, xy), (h, q, zy))
        return best

    left = [m for m in left if m.i in movers]
    while left:
        cand = [(gap(m), m) for m in left]
        (key, mine, theirs), m = min(cand, key=lambda t: (t[0][0][0], t[1].i))
        left.remove(m)
        placed.append(m)
        done = set()
        g, p, xy = mine
        h, q, zy = theirs
        if g == h:
            continue
        for ax, kind in ((0, "DistanceX"), (1, "DistanceY")):
            v = xy[ax] - zy[ax]
            if abs(v) <= 1e-6 * scale:
                continue
            out.append(("offset", _signed(kind, h, q, g, p, v)))
        # the other end points of a line, against the same neighbour
        if m.kind == "line":
            other = 2 if p == 1 else 1
            oxy = m.pts[other]
            for ax, kind in ((0, "DistanceX"), (1, "DistanceY")):
                v = oxy[ax] - zy[ax]
                if abs(v) <= 1e-6 * scale:
                    continue
                out.append(("offset", _signed(kind, h, q, g, other, v)))
    return out


def _gap_proposals(model):
    """Two pieces of drawn geometry whose nearest end points are close but
    not joined (a deliberate small gap or step): the gap is dimensioned
    along each axis it has."""
    scale = _scale(model)
    tol = 1e-6 * scale
    els = [m for m in _drawn(model) if m.kind in ("line", "arc")]
    if len(els) < 2 or len(els) > 60:
        return []
    parent = {m.i: m.i for m in els}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    ends = [(m.i, p, xy) for m in els for p, xy in sorted(m.pts.items()) if p != 3]
    for a in ends:
        for b in ends:
            if a[0] < b[0] and abs(a[2][0] - b[2][0]) <= tol and abs(a[2][1] - b[2][1]) <= tol:
                parent[find(b[0])] = find(a[0])
    xs = [xy[0] for _, _, xy in ends]
    ys = [xy[1] for _, _, xy in ends]
    diag = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
    best = {}
    for a in ends:
        for b in ends:
            ra, rb = find(a[0]), find(b[0])
            if ra >= rb:
                continue
            d = math.hypot(a[2][0] - b[2][0], a[2][1] - b[2][1])
            if d <= tol or d > 0.02 * diag:
                continue
            key = (ra, rb)
            cand = (d, a[0], a[1], b[0], b[1], a[2], b[2])
            if key not in best or cand[:5] < best[key][:5]:
                best[key] = cand
    out = []
    for key in sorted(best):
        d, g1, p1, g2, p2, a, b = best[key]
        for ax, kind in ((0, "DistanceX"), (1, "DistanceY")):
            v = b[ax] - a[ax]
            if abs(v) > tol:
                out.append(("gap", _signed(kind, g1, p1, g2, p2, v)))
    return out


def _angle_value(a, pa, b, pb):
    """The value FreeCAD's four-argument angle constraint takes for lines `a`
    and `b` at the end points `pa`, `pb`: the counter-clockwise angle from the
    direction of `a` away from its point to the direction of `b` away from its
    point."""

    def direction(m, p):
        (x1, y1), (x2, y2) = m.pts[1], m.pts[2]
        return (x2 - x1, y2 - y1) if p == 1 else (x1 - x2, y1 - y2)

    d1, d2 = direction(a, pa), direction(b, pb)
    v = math.atan2(d1[0] * d2[1] - d1[1] * d2[0], d1[0] * d2[0] + d1[1] * d2[1])
    return v


def _angle_neighbour_proposals(model, flip):
    """An inclined line is given the angle against a line it meets (a
    horizontal or vertical one first), measured at the shared point."""
    out = []
    lines = [m for m in _drawn(model) if _is_line(m)]
    scale = _scale(model)
    tol = 1e-6 * scale
    for m in lines:
        if _is_hv(m):
            continue
        best = None
        for n in lines:
            if n is m:
                continue
            for pm in (1, 2):
                for pn in (1, 2):
                    a, b = m.pts[pm], n.pts[pn]
                    if abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol:
                        v = _angle_value(m, pm, n, pn)
                        if abs(math.sin(v)) < 1e-3:
                            continue
                        key = (0 if _is_hv(n) else 1, (-n.i if flip else n.i), pm, pn)
                        if best is None or key < best[0]:
                            best = (key, n, pm, pn, v)
        if best is None:
            continue
        _, n, pm, pn, v = best
        if v < 0:
            out.append(("angle_nb", ("Angle", n.i, pn, m.i, pm, -v)))
        else:
            out.append(("angle_nb", ("Angle", m.i, pm, n.i, pn, v)))
    return out


def _angle_axis_proposals(model, accepted):
    """One angle against the x axis for each group of parallel inclined lines."""
    parent = list(range(len(model)))

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for s in accepted:
        if s[0] in ("Parallel", "Perpendicular"):
            a, b = find(s[1]), find(s[2])
            if a != b:
                parent[max(a, b)] = min(a, b)
    seen, out = set(), []
    for m in _drawn(model):
        if _is_line(m) and not _is_hv(m):
            root = find(m.i)
            if root not in seen:
                seen.add(root)
                (x1, y1), (x2, y2) = m.pts[1], m.pts[2]
                out.append(("angle_axis", ("Angle", m.i, math.atan2(y2 - y1, x2 - x1))))
    return out


def proposals(model, scheme, accepted=()):
    """The ranked dimension specs for an intent scheme.  `accepted` are the
    relation specs already on the copy; of a group of equal elements only one
    is sized."""
    flip = scheme == "intent_offset"
    parent = list(range(len(model)))

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for s in accepted:
        if s[0] == "Equal":
            a, b = find(s[1]), find(s[2])
            if a != b:
                parent[max(a, b)] = min(a, b)
    sized = set()

    def first(m):
        root = find(m.i)
        if root in sized:
            return False
        sized.add(root)
        return True

    pool = []
    drawn = _drawn(model)
    for m in model:
        if m.kind == "circle" and first(m):
            pool.append(("size", ("Diameter", m.i, 2 * m.r)))
        elif m.kind == "arc" and first(m):
            pool.append(("size", ("Radius", m.i, m.r)))
    # construction lines carry dimensions
    for m in model:
        if m.construction and _is_line(m) and first(m):
            pool.append(("carrier", _length_spec(m)))
    if scheme != "intent":
        pool += _extent_proposals(model, flip)
    if scheme == "intent":
        pool += _angle_axis_proposals(model, accepted)
    else:
        pool += _angle_neighbour_proposals(model, flip)
    pool += _gap_proposals(model)
    pool += _offset_proposals(model, flip, scheme != "intent", scheme == "intent")
    # remaining line lengths, longest first
    for m in sorted((m for m in drawn if _is_line(m)), key=lambda m: m.i):
        if first(m):
            pool.append(("length", _length_spec(m)))
    # origin placements of every feature anchor
    for m in drawn:
        p, xy = _anchor(m)
        pool.append(("origin", ("DistanceX", m.i, p, xy[0])))
        pool.append(("origin", ("DistanceY", m.i, p, xy[1])))
    seen = set()
    out = []
    import os
    drop = set(os.environ.get('INTENT_DROP', '').split(','))
    for _kind, spec in rank(pool, scheme):
        if _kind in drop:
            continue
        key = _norm_key(spec)
        if key in seen:
            continue
        seen.add(key)
        out.append(spec)
    return out


def _norm_key(spec):
    t = spec[0]
    if t in ("DistanceX", "DistanceY") and len(spec) == 6:
        return (t, frozenset({(spec[1], spec[2]), (spec[3], spec[4])}))
    if t in ("Distance", "Angle") and len(spec) == 6:
        return (t, frozenset({(spec[1], spec[2]), (spec[3], spec[4])}))
    return (t, tuple(spec[1:-1]))
