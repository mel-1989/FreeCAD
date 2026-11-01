# SPDX-License-Identifier: LGPL-2.1-or-later
"""Faceted lofted bend: two open planar profiles joined by planar triangles (cad-fork G07 slice 4b).

Clean-room code written for the fork (run 028). There is no lofted bend in the vendored LGPL
subset and none in the add-on; no GPL-headed add-on file was opened. Only the public knowledge
below, OCCT/Part and the LGPL helper modules beside this file were used.

Public sources
- Triangulation development of transition pieces (square-to-round, hoppers, offset transitions):
  sheet-metal pattern-drafting texts, for example the "triangulation" method in Oberg, Jones,
  Horton, Ryffel, "Machinery's Handbook" (Industrial Press), development of surfaces: the
  surface is split into triangles whose three true edge lengths are known; the triangles are
  laid out one after another about their shared edges, each new triangle on the far side of the
  edge it shares with the previous one.
- The mid-surface as the neutral surface of a sharp fold: a press brake fold of a thin sheet with
  no inside radius keeps the sheet's mid-surface (and its true lengths) unstretched, so the
  flat pattern of the sheet is the development of the mid-surface, thickened by T. Folds with a
  finite radius would overlap where the fan hinges of a square-to-round meet at a corner, so
  every hinge here is a sharp fold (radius 0, bend allowance 0).
- A mitred joint of two plates of thickness T is cut on the bisector plane of the two plate
  planes (Wikipedia, "Mitre joint"; any woodworking or sheet-metal fabrication text).

What the feature builds
- Profiles: two open planar chains of straight segments and circular arcs on parallel planes.
  Segment i of A is paired with segment i of B. A pair of lines is a strip of two triangles on the
  shorter diagonal; a line against an arc, or two arcs, are strips of triangles whose fans
  start at the corners (an arc is cut into ``facets`` chords). Profile B can be reversed
  (``alignment``); ``auto`` picks the direction whose ends lie nearest to A's ends.
- Hinges: every edge shared by two triangles is a sharp fold on the mid-surface. The solid is the
  union of one prism per triangle: the triangle thickened along its normal by T (``side`` mid
  is +-T/2, inside the mid-surface to T toward the profiles' centroid, outside the other way),
  mitred on the bisector plane of the two triangle planes at each hinge, and cut square to the
  triangle at the open profile edges. The edge line of a prism at height d moves in the triangle
  plane by -s * d * tan(theta / 2), with theta the angle between the two triangle normals and s
  = +1 when the fold is concave toward +d, -1 when convex, so every prism's cross-section area
  is quadratic in d, its volume has a closed form (Simpson on three sections) and the sum of
  those is the part's volume.
- Flat pattern: the mid-surface triangles unrolled about their hinges (exact development,
  true edge lengths), thickened by T. One bend line per hinge that really folds (dihedral above
  1e-9 rad), UP when the fold is concave toward the viewer, who looks from the inside (the side
  the sides ``inside`` thickens toward). The flat's volume is the mid-surface area times T, so it
  differs from the part's by the mitre's T^2 and T^3 terms; ``flat_volume_offset`` is that
  difference, so ``SheetMetalFlatPattern.flat_problem`` still checks it to 1e-4.
- Refusals (a readable LoftedBendError, shown as an Invalid feature, never a silent wrong
  solid): profiles on non-parallel planes or in one plane, a closed profile, a profile that is
  not one chain, curves other than lines and arcs, different segment counts, a smooth or formed
  method, a flat or degenerate triangle, a triangulation that folds over itself (development or
  prisms overlap, a hinge turning by 170 degrees or more), and a thickness whose mitre inverts a
  facet.
"""

import json
import math

import FreeCAD
import Part
from FreeCAD import Vector

import SheetMetalFlatPattern as FP
import SheetMetalTools

PROFILE_TOL = 1e-6          # mm, point matching and planarity
PARALLEL_TOL = 1e-7         # |n_a x n_b|
FLAT_HINGE = 1e-9           # rad, below this a hinge is not a fold
MAX_TURN = math.radians(170.0)
VOLUME_TOL = 1e-7           # relative, fused solid against the sum of the prisms
MAX_OVERLAP = 0.02          # relative volume the prisms may overlap before it is a fold-over
SIDES = ["mid", "inside", "outside"]
METHODS = ["faceted", "smooth"]
ALIGNMENTS = ["auto", "same", "reversed"]
smElementMapVersion = "sm1."


class LoftedBendError(Exception):
    """A refusal with a message for the user."""


###################################################################################################
# Profiles
###################################################################################################

def _sub(a, b):
    return Vector(a.x - b.x, a.y - b.y, a.z - b.z)


def _curve_kind(edge):
    name = type(edge.Curve).__name__
    if name in ("Line", "LineSegment"):
        return "line"
    if name in ("Circle", "ArcOfCircle"):
        return "arc"
    raise LoftedBendError(
        "a profile edge is a %s; a lofted bend takes straight segments and circular arcs only "
        "(a smooth or formed loft is not supported)" % name)


def _ends(edge):
    return edge.valueAt(edge.FirstParameter), edge.valueAt(edge.LastParameter)


def read_profile(shape, facets, label):
    """The profile as a list of segments, each a list of points (2 for a line, facets + 1 for an
    arc), following one open chain from its first end to its last."""
    if shape is None or shape.isNull() or not shape.Edges:
        raise LoftedBendError("profile %s has no edges" % label)
    edges = list(shape.Edges)
    kinds = [_curve_kind(e) for e in edges]
    for e in edges:
        a, b = _ends(e)
        if e.isClosed() or (a - b).Length < PROFILE_TOL:
            raise LoftedBendError(
                "profile %s is closed; a lofted bend needs an open profile with a seam gap"
                % label)
    # degree of every end point
    points = []   # [(point, [edge indices])]

    def key(p):
        for rec in points:
            if (rec[0] - p).Length < PROFILE_TOL:
                return rec
        rec = (p, [])
        points.append(rec)
        return rec

    for i, e in enumerate(edges):
        a, b = _ends(e)
        key(a)[1].append(i)
        key(b)[1].append(i)
    if any(len(rec[1]) > 2 for rec in points):
        raise LoftedBendError("profile %s branches; it must be one chain" % label)
    ends = [rec for rec in points if len(rec[1]) == 1]
    if len(ends) == 0:
        raise LoftedBendError(
            "profile %s is closed; a lofted bend needs an open profile with a seam gap" % label)
    if len(ends) != 2:
        raise LoftedBendError("profile %s is not one connected chain" % label)
    # start at the end that is the start of the first edge listed, else its end
    a0, b0 = _ends(edges[0])
    start = a0 if any((rec[0] - a0).Length < PROFILE_TOL and len(rec[1]) == 1
                      for rec in points) else b0
    if not any((rec[0] - start).Length < PROFILE_TOL and len(rec[1]) == 1 for rec in points):
        start = ends[0][0]
    remaining = set(range(len(edges)))
    segments, cur = [], start
    while remaining:
        nxt = None
        for i in sorted(remaining):
            a, b = _ends(edges[i])
            if (a - cur).Length < PROFILE_TOL:
                nxt = (i, False)
                break
            if (b - cur).Length < PROFILE_TOL:
                nxt = (i, True)
                break
        if nxt is None:
            raise LoftedBendError("profile %s is not one connected chain" % label)
        i, rev = nxt
        remaining.discard(i)
        e = edges[i]
        n = 1 if kinds[i] == "line" else int(facets)
        f, l = e.FirstParameter, e.LastParameter
        pts = [e.valueAt(f + (l - f) * k / n) for k in range(n + 1)]
        if rev:
            pts.reverse()
        pts[0] = Vector(cur)
        segments.append({"kind": kinds[i], "pts": pts})
        cur = pts[-1]
    return segments


def _reverse(segments):
    return [{"kind": s["kind"], "pts": list(reversed(s["pts"]))} for s in reversed(segments)]


def _flatten(segments):
    """(points, [(first index, last index) per segment])."""
    pts, spans = [], []
    for s in segments:
        if not pts:
            pts.append(s["pts"][0])
        first = len(pts) - 1
        pts.extend(s["pts"][1:])
        spans.append((first, len(pts) - 1))
    return pts, spans


def _plane_of(points, hint=None):
    """(point, unit normal) of the best plane through points; the hint normal (a sketch
    placement) when the points are collinear."""
    p0 = points[0]
    far = max(points, key=lambda p: (p - p0).Length)
    d = far - p0
    if d.Length < PROFILE_TOL:
        raise LoftedBendError("a profile has zero length")
    best, n = 0.0, None
    for p in points:
        c = d.cross(p - p0)
        if c.Length > best:
            best, n = c.Length, c
    if n is None or best < PROFILE_TOL * d.Length * 10:
        if hint is None:
            raise LoftedBendError(
                "a straight single-line profile does not define its plane; use a sketch")
        n = Vector(hint)
    n = Vector(n)
    n.normalize()
    scale = max(1.0, d.Length)
    for p in points:
        if abs((p - p0).dot(n)) > PROFILE_TOL * scale:
            raise LoftedBendError("a profile is not planar")
    return p0, n


def _sketch_normal(obj):
    if obj is not None and getattr(obj, "TypeId", "").startswith("Sketcher::"):
        return obj.Placement.Rotation.multVec(Vector(0, 0, 1))
    return None


###################################################################################################
# Triangulation
###################################################################################################

def _strip(pa, pb, a0, a1, b0, b1, tris):
    """Append the triangles between A[a0..a1] and B[b0..b1]; indices into the shared point list
    (A points first). Triangles are (A_i, A_i+1, B_j) or (A_i, B_j+1, B_j), all of one sense, and
    each shares an edge with the one before it."""
    m, n = a1 - a0, b1 - b0
    if m == n:
        for k in range(m):
            A0, A1, B0, B1 = a0 + k, a0 + k + 1, b0 + k, b0 + k + 1
            if (pa[A0] - pb[B1]).Length <= (pa[A1] - pb[B0]).Length:
                tris.append((A0, B1, B0))
                tris.append((A0, A1, B1))
            else:
                tris.append((A0, A1, B0))
                tris.append((A1, B1, B0))
        return
    i, j = 0, 0
    while i < m or j < n:
        if j >= n or (i < m and (i + 0.5) / m <= (j + 0.5) / n):
            tris.append((a0 + i, a0 + i + 1, b0 + j))
            i += 1
        else:
            tris.append((a0 + i, b0 + j + 1, b0 + j))
            j += 1


def build_mesh(segs_a, segs_b):
    """(points, number of A points, triangles) for two segment lists of equal length."""
    pa, spans_a = _flatten(segs_a)
    pb, spans_b = _flatten(segs_b)
    na = len(pa)
    points = list(pa) + list(pb)
    tris = []
    for (a0, a1), (b0, b1) in zip(spans_a, spans_b):
        _strip(points, points, a0, a1, na + b0, na + b1, tris)
    return points, na, tris


###################################################################################################
# Mid-surface analysis
###################################################################################################

###################################################################################################
# Triangle-triangle intersection (pure Python, tolerance eps)
###################################################################################################

def _v_sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _v_dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _v_cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _seg_hits_tri(p, q, tri, eps):
    """Segment pq against a triangle that is not coplanar with it; True when they touch."""
    e1, e2 = _v_sub(tri[1], tri[0]), _v_sub(tri[2], tri[0])
    nrm = _v_cross(e1, e2)
    ln = math.sqrt(_v_dot(nrm, nrm))
    nrm = (nrm[0] / ln, nrm[1] / ln, nrm[2] / ln)
    dp, dq = _v_dot(_v_sub(p, tri[0]), nrm), _v_dot(_v_sub(q, tri[0]), nrm)
    if (dp > eps and dq > eps) or (dp < -eps and dq < -eps):
        return False
    den = dp - dq
    t = 0.5 if abs(den) < 1e-300 else min(1.0, max(0.0, dp / den))
    x = (p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1]), p[2] + t * (q[2] - p[2]))
    # barycentric test of the crossing point, tolerance eps in length
    for k in range(3):
        a, b = tri[k], tri[(k + 1) % 3]
        edge = _v_sub(b, a)
        inward = _v_cross(nrm, edge)          # points to the inside for a ccw triangle
        c = _v_sub(tri[(k + 2) % 3], a)
        sign = 1.0 if _v_dot(inward, c) > 0 else -1.0
        il = math.sqrt(_v_dot(inward, inward))
        if sign * _v_dot(inward, _v_sub(x, a)) / il < -eps:
            return False
    return True


def _coplanar_overlap(a, b, eps, strict):
    """2D separating-axis test of two coplanar triangles (strict: touching does not count)."""
    nrm = _v_cross(_v_sub(a[1], a[0]), _v_sub(a[2], a[0]))
    ln = math.sqrt(_v_dot(nrm, nrm))
    nrm = (nrm[0] / ln, nrm[1] / ln, nrm[2] / ln)
    for tri in (a, b):
        for k in range(3):
            ax = _v_cross(nrm, _v_sub(tri[(k + 1) % 3], tri[k]))
            al = math.sqrt(_v_dot(ax, ax))
            if al == 0:
                continue
            pa = [_v_dot(q, ax) / al for q in a]
            pb = [_v_dot(q, ax) / al for q in b]
            tol = eps if strict else -eps
            if max(pa) <= min(pb) + tol or max(pb) <= min(pa) + tol:
                return False
    return True


def _tris_cross(a, b, sa, sb, eps):
    """True when triangles a and b (3 points each) intersect anywhere but at the vertices they
    share (sa, sb flag the shared corners; none or one corner is shared)."""
    def plane_dist(tri, ref):
        n = _v_cross(_v_sub(ref[1], ref[0]), _v_sub(ref[2], ref[0]))
        ln = math.sqrt(_v_dot(n, n))
        return [_v_dot(_v_sub(q, ref[0]), n) / ln for q in tri]

    if all(abs(d) <= eps for d in plane_dist(b, a)):
        return _coplanar_overlap(a, b, eps, any(sa))
    if any(sa):
        # the intersection is a segment out of the shared vertex; it ends on the opposite edge
        ia = sa.index(True)
        ib = sb.index(True)
        pa, qa = a[(ia + 1) % 3], a[(ia + 2) % 3]
        pb, qb = b[(ib + 1) % 3], b[(ib + 2) % 3]
        return _seg_hits_tri(pa, qa, b, eps) or _seg_hits_tri(pb, qb, a, eps)
    return (any(_seg_hits_tri(a[k], a[(k + 1) % 3], b, eps) for k in range(3))
            or any(_seg_hits_tri(b[k], b[(k + 1) % 3], a, eps) for k in range(3)))


class Mesh:
    """Triangles of the mid-surface with their hinges, normals and the flat development."""

    def __init__(self, points, na, tris, side, thickness, invert=False):
        self.points = points
        self.na = na
        self.tris = tris
        self.side = side
        self.T = float(thickness)
        self._normals(invert)
        self._hinges()
        self._check_triangles()
        self._check_self_intersection()
        self._development()

    # -- normals, inside orientation
    def _normals(self, invert):
        P = self.points
        raw, areas, cents = [], [], []
        for a, b, c in self.tris:
            n = (P[b] - P[a]).cross(P[c] - P[a])
            raw.append(n)
            areas.append(0.5 * n.Length)
            cents.append((P[a] + P[b] + P[c]) * (1.0 / 3.0))
        self.areas = areas
        centroid = Vector(0, 0, 0)
        for p in P:
            centroid = centroid + p
        centroid = centroid * (1.0 / len(P))
        score = sum(ar * (Vector(n).normalize().dot(centroid - cen)) if ar > 0 else 0.0
                    for ar, n, cen in zip(areas, raw, cents))
        total = sum(areas)
        size = max(max((p - centroid).Length for p in P), 1.0)
        sigma = 1.0
        ambiguous = abs(score) < 1e-9 * total * size
        if ambiguous:
            if self.side != "mid" and not invert:
                raise LoftedBendError(
                    "the profiles' centroid lies in the sheet, so inside and outside cannot be "
                    "told apart; use side mid or tick invert")
        elif score < 0:
            sigma = -1.0
        if invert:
            sigma = -sigma
        self.sigma = sigma
        self.normals = []
        for n in raw:
            u = Vector(n)
            u.normalize()
            self.normals.append(u * sigma)

    # -- hinges
    def _hinges(self):
        edge_tris = {}
        for k, t in enumerate(self.tris):
            for j in range(3):
                e = tuple(sorted((t[j], t[(j + 1) % 3])))
                edge_tris.setdefault(e, []).append(k)
        self.edge_tris = edge_tris
        P = self.points
        # per (triangle, edge) the shift coefficient c: the edge moves outward by c * d
        self.shift = {}
        self.hinges = []   # dict(a, b, k, l, theta, up)
        for e, ks in edge_tris.items():
            if len(ks) > 2:
                raise LoftedBendError("the triangulation has an edge shared by three triangles")
            if len(ks) == 1:
                self.shift[(ks[0], e)] = 0.0
                continue
            k, l = sorted(ks)
            nk, nl = self.normals[k], self.normals[l]
            theta = math.atan2(nk.cross(nl).Length, nk.dot(nl))
            if theta > MAX_TURN:
                raise LoftedBendError(
                    "the triangulation folds over itself: two neighbouring facets turn by "
                    "%.1f degrees at one hinge; check the alignment of the profiles"
                    % math.degrees(theta))
            other = [v for v in self.tris[l] if v not in e][0]
            h = (P[other] - P[e[0]]).dot(nk)
            up = h > 0
            if theta < FLAT_HINGE:
                coef = 0.0
            else:
                coef = (-1.0 if up else 1.0) * math.tan(theta / 2.0)
            self.shift[(k, e)] = coef
            self.shift[(l, e)] = coef
            if theta >= FLAT_HINGE:
                self.hinges.append({"a": e[0], "b": e[1], "k": k, "l": l, "theta": theta,
                                    "up": up})
        self.hinges.sort(key=lambda h: (h["k"], h["a"], h["b"]))

    # -- per triangle offset geometry
    def _check_self_intersection(self):
        """Refuse a mid-surface whose non-adjacent triangles intersect (a twisted loft)."""
        pts = [(p.x, p.y, p.z) for p in self.points]
        tris = self.tris
        scale = max(max(abs(c) for p in pts for c in p), 1.0)
        eps = 1e-9 * scale
        boxes = []
        for t in tris:
            ps = [pts[v] for v in t]
            boxes.append(([min(q[i] for q in ps) - eps for i in range(3)],
                          [max(q[i] for q in ps) + eps for i in range(3)]))
        n = len(tris)
        for i in range(n):
            lo_i, hi_i = boxes[i]
            si = set(tris[i])
            for j in range(i + 1, n):
                lo_j, hi_j = boxes[j]
                if (lo_i[0] > hi_j[0] or lo_j[0] > hi_i[0] or lo_i[1] > hi_j[1]
                        or lo_j[1] > hi_i[1] or lo_i[2] > hi_j[2] or lo_j[2] > hi_i[2]):
                    continue
                shared = si & set(tris[j])
                if len(shared) >= 2:
                    continue
                if _tris_cross([pts[v] for v in tris[i]], [pts[v] for v in tris[j]],
                               [v in shared for v in tris[i]], [v in shared for v in tris[j]],
                               eps):
                    raise LoftedBendError(
                        "the mid-surface intersects itself: facets %d and %d cross each other "
                        "(the profiles are twisted against each other); change the start point "
                        "of one profile or the alignment (forward or reversed) so that the "
                        "profiles run the same way round" % (i, j))

    def _check_triangles(self):
        P = self.points
        scale = max((p - P[0]).Length for p in P)
        self.tri_geo = []
        for k, t in enumerate(self.tris):
            if self.areas[k] < 1e-9 * scale * scale:
                raise LoftedBendError(
                    "the triangulation has a flat (zero area) triangle; move the profiles' "
                    "start points or change the number of facets")
            pts = [P[v] for v in t]
            n = self.normals[k]
            a = _sub(pts[1], pts[0])
            a.normalize()
            b = n.cross(a)
            ms = []
            for j in range(3):
                d = _sub(pts[(j + 1) % 3], pts[j])
                m = d.cross(n)
                m.normalize()
                if m.dot(_sub(pts[(j + 2) % 3], pts[j])) > 0:
                    m = m * -1.0
                ms.append((m.dot(a), m.dot(b)))
            # vertex j lies on edges j-1 and j: solve m_{j-1}.u = c_{j-1} d, m_j.u = c_j d
            coefs = []
            for j in range(3):
                e = tuple(sorted((t[j], t[(j + 1) % 3])))
                coefs.append(self.shift[(k, e)])
            offs = []
            for j in range(3):
                (a1, b1), (a2, b2) = ms[(j - 1) % 3], ms[j]
                c1, c2 = coefs[(j - 1) % 3], coefs[j]
                det = a1 * b2 - a2 * b1
                if abs(det) < 1e-12:
                    raise LoftedBendError("a facet is degenerate")
                x = (c1 * b2 - c2 * b1) / det
                y = (a1 * c2 - a2 * c1) / det
                offs.append(a * x + b * y)
            self.tri_geo.append((pts, n, offs))
        lo, hi = self.heights()
        h = hi - lo
        msg = ("the thickness %.3g mm is too large for facet %d: its mitre inverts the facet; "
               "reduce the thickness or the fold angles")
        for k in range(len(self.tris)):
            a_lo, a_hi = self.section_area(k, lo), self.section_area(k, hi)
            a_mid = self.section_area(k, 0.5 * (lo + hi))
            floor = 1e-9 * self.areas[k]
            if min(a_lo, a_mid, a_hi) <= floor:
                raise LoftedBendError(msg % (self.T, k))
            q2 = (a_lo - 2.0 * a_mid + a_hi) * 4.0 / (h * h)   # second derivative of A(d)
            if q2 > 0:
                q1 = (a_hi - a_lo) / h
                d_star = 0.5 * (lo + hi) - q1 / q2
                if lo < d_star < hi and self.section_area(k, d_star) <= floor:
                    raise LoftedBendError(msg % (self.T, k))

    def heights(self):
        if self.side == "mid":
            return -0.5 * self.T, 0.5 * self.T
        if self.side == "inside":
            return 0.0, self.T
        return -self.T, 0.0

    def vertices_at(self, k, d):
        pts, n, offs = self.tri_geo[k]
        return [pts[j] + n * d + offs[j] * d for j in range(3)]

    def section_area(self, k, d):
        q = self.vertices_at(k, d)
        n = (q[1] - q[0]).cross(q[2] - q[0])
        return 0.5 * n.dot(self._tri_n(k))

    def _tri_n(self, k):
        pts, n, _ = self.tri_geo[k]
        raw = (pts[1] - pts[0]).cross(pts[2] - pts[0])
        raw.normalize()
        return raw

    def prism_volume(self, k):
        lo, hi = self.heights()
        mid = 0.5 * (lo + hi)
        return (hi - lo) / 6.0 * (self.section_area(k, lo) + 4.0 * self.section_area(k, mid)
                                  + self.section_area(k, hi))

    def volume(self):
        return sum(self.prism_volume(k) for k in range(len(self.tris)))

    def mid_area(self):
        return sum(self.areas)

    # -- development
    def _development(self):
        P = self.points
        tris = self.tris

        def dist(a, b):
            return (P[a] - P[b]).Length

        flat = {}
        t0 = tris[0]
        d01, d02, d12 = dist(t0[0], t0[1]), dist(t0[0], t0[2]), dist(t0[1], t0[2])
        x = (d01 * d01 + d02 * d02 - d12 * d12) / (2.0 * d01)
        y = math.sqrt(max(0.0, d02 * d02 - x * x))
        flat[t0[0]] = (0.0, 0.0)
        flat[t0[1]] = (d01, 0.0)
        flat[t0[2]] = (x, y)
        for k in range(1, len(tris)):
            prev, cur = set(tris[k - 1]), tris[k]
            shared = [v for v in cur if v in prev]
            new = [v for v in cur if v not in prev]
            if len(shared) != 2 or len(new) != 1:
                raise LoftedBendError("the triangulation is not a strip of triangles")
            u, v = shared
            w = new[0]
            third_prev = [q for q in tris[k - 1] if q not in shared][0]
            pu, pv = flat[u], flat[v]
            ex, ey = pv[0] - pu[0], pv[1] - pu[1]
            L = math.hypot(ex, ey)
            ex, ey = ex / L, ey / L
            du, dv = dist(u, w), dist(v, w)
            xx = (du * du + L * L - dv * dv) / (2.0 * L)
            yy = math.sqrt(max(0.0, du * du - xx * xx))
            pp = flat[third_prev]
            side = ex * (pp[1] - pu[1]) - ey * (pp[0] - pu[0])
            sgn = -1.0 if side > 0 else 1.0
            # perpendicular (-ey, ex) is on the left of the edge
            flat[w] = (pu[0] + ex * xx + sgn * (-ey) * yy, pu[1] + ey * xx + sgn * ex * yy)
        if self.sigma < 0:
            flat = {v: (-p[0], p[1]) for v, p in flat.items()}
        self.flat = flat
        self._check_flat_overlap()

    def _check_flat_overlap(self):
        tri2 = [[self.flat[v] for v in t] for t in self.tris]
        scale = max(max(abs(c) for p in pts for c in p) for pts in tri2)
        eps = 1e-9 * max(scale, 1.0)

        def axes(pts):
            for j in range(3):
                x0, y0 = pts[j]
                x1, y1 = pts[(j + 1) % 3]
                yield (y0 - y1, x1 - x0)

        def separated(a, b):
            for ax in list(axes(a)) + list(axes(b)):
                ln = math.hypot(*ax)
                if ln == 0:
                    continue
                pa = [(p[0] * ax[0] + p[1] * ax[1]) / ln for p in a]
                pb = [(p[0] * ax[0] + p[1] * ax[1]) / ln for p in b]
                if max(pa) <= min(pb) + eps or max(pb) <= min(pa) + eps:
                    return True
            return False

        n = len(tri2)
        for i in range(n):
            for j in range(i + 1, n):
                if not separated(tri2[i], tri2[j]):
                    raise LoftedBendError(
                        "the triangulation folds over itself: facets %d and %d overlap when "
                        "developed into the plane (a flat pattern cannot be made); check the "
                        "alignment of the profiles" % (i, j))

    # -- flat outline
    def outline_ids(self):
        na = self.na
        n = len(self.points)
        return list(range(na)) + list(range(n - 1, na - 1, -1))

    def flat_points(self):
        return [Vector(self.flat[v][0], self.flat[v][1], 0.0) for v in self.outline_ids()]

    def bends(self):
        out = []
        for h in self.hinges:
            a = self.flat[h["a"]]
            b = self.flat[h["b"]]
            out.append(FP.FlatBend("UP" if h["up"] else "DOWN", math.degrees(h["theta"]),
                                   0.0, 0.0, 0.0, self.T,
                                   [(Vector(a[0], a[1], 0.0), Vector(b[0], b[1], 0.0))]))
        return out


###################################################################################################
# Shapes
###################################################################################################

def _polygon_face(pts):
    return Part.Face(Part.makePolygon(list(pts) + [pts[0]]))


def prism_solid(vb, vt):
    faces = [_polygon_face(vb), _polygon_face(vt)]
    for j in range(3):
        k = (j + 1) % 3
        faces.append(_polygon_face([vb[j], vb[k], vt[k], vt[j]]))
    solid = Part.makeSolid(Part.makeShell(faces))
    if solid.Volume < 0:
        solid.reverse()
    return solid


def build_solid(mesh):
    lo, hi = mesh.heights()
    prisms = [prism_solid(mesh.vertices_at(k, lo), mesh.vertices_at(k, hi))
              for k in range(len(mesh.tris))]
    fused = prisms[0].multiFuse(prisms[1:], 1e-7) if len(prisms) > 1 else prisms[0]
    fused = fused.removeSplitter()
    solids = fused.Solids
    if len(solids) != 1:
        raise LoftedBendError(
            "the thickened facets do not join into one solid (%d pieces); the thickness may be "
            "too large for the corners, or the triangulation folds over itself" % len(solids))
    solid = solids[0]
    expected = mesh.volume()
    overlap = expected - solid.Volume
    if overlap < -VOLUME_TOL * expected or overlap > MAX_OVERLAP * expected:
        raise LoftedBendError(
            "the thickened facets overlap each other too much (volume %.6f against %.6f mm3 for "
            "the sum of the facets); reduce the thickness or change the profiles"
            % (solid.Volume, expected))
    if not solid.isValid():
        raise LoftedBendError("the thickened facets give an invalid solid")
    return solid


def flat_shapes(mesh):
    """(flat solid, compound of flat triangles, compound of mid-surface triangles)."""
    pts = mesh.flat_points()
    face = _polygon_face(pts)
    solid = face.extrude(Vector(0, 0, mesh.T))
    if solid.ShapeType != "Solid":
        solid = solid.Solids[0]
    flat_tris = Part.Compound([
        _polygon_face([Vector(mesh.flat[v][0], mesh.flat[v][1], 0.0) for v in t])
        for t in mesh.tris])
    mid = Part.Compound([_polygon_face([mesh.points[v] for v in t]) for t in mesh.tris])
    return solid, flat_tris, mid


###################################################################################################
# Feature
###################################################################################################

def prepare(shape_a, shape_b, facets, alignment, hint_a=None, hint_b=None):
    """(points, na, triangles) for two profile shapes, or a LoftedBendError."""
    if int(facets) < 1:
        raise LoftedBendError("facets per curved segment must be at least 1")
    seg_a = read_profile(shape_a, facets, "A")
    seg_b = read_profile(shape_b, facets, "B")
    pa, _ = _flatten(seg_a)
    pb, _ = _flatten(seg_b)
    p_a, n_a = _plane_of(pa, hint_a)
    p_b, n_b = _plane_of(pb, hint_b)
    if n_a.cross(n_b).Length > PARALLEL_TOL:
        raise LoftedBendError(
            "the profiles lie on planes that are not parallel (normals %.4f degrees apart)"
            % math.degrees(math.asin(min(1.0, n_a.cross(n_b).Length))))
    gap = abs((p_b - p_a).dot(n_a))
    if gap < PROFILE_TOL * 10:
        raise LoftedBendError("the profiles lie in one plane; they need a distance between them")
    if len(seg_a) != len(seg_b):
        raise LoftedBendError(
            "profile A has %d segments and profile B has %d; a lofted bend pairs segment i of A "
            "with segment i of B, so split a profile so that the counts agree"
            % (len(seg_a), len(seg_b)))
    if alignment not in ALIGNMENTS:
        raise LoftedBendError("alignment must be one of %s" % ALIGNMENTS)
    if alignment == "reversed":
        seg_b = _reverse(seg_b)
    elif alignment == "auto":
        def proj(p):
            return p - n_a * ((p - p_a).dot(n_a))

        def cost(sb):
            q, _ = _flatten(sb)
            return (proj(q[0]) - pa[0]).Length + (proj(q[-1]) - pa[-1]).Length

        rb = _reverse(seg_b)
        if cost(rb) < cost(seg_b) - 1e-9:
            seg_b = rb
    # a line and an arc, or arc and arc, need the pairing to cut the same number of facets
    return build_mesh(seg_a, seg_b)


def analyse(shape_a, shape_b, thickness, side="mid", facets=4, alignment="auto", invert=False,
            method="faceted", hint_a=None, hint_b=None):
    """The Mesh of a lofted bend (triangles, hinges, development) without building shapes."""
    if method != "faceted":
        raise LoftedBendError(
            "the %s method (a smooth, formed loft) is not supported; use the faceted method"
            % method)
    if not (thickness > 0 and math.isfinite(thickness)):
        raise LoftedBendError("the thickness must be a finite number above 0")
    if side not in SIDES:
        raise LoftedBendError("side must be one of %s" % SIDES)
    points, na, tris = prepare(shape_a, shape_b, facets, alignment, hint_a, hint_b)
    return Mesh(points, na, tris, side, thickness, invert)


def make_all(mesh):
    """(solid, flat solid, flat triangles, mid-surface triangles, bends, flat volume offset,
    overlap volume)."""
    solid = build_solid(mesh)
    flat, flat_tris, mid = flat_shapes(mesh)
    bends = mesh.bends()
    # flat volume - part volume = the mitre terms (closed form, T^2 and T^3) + the overlap of the
    # prisms at the fans, which is measured on the solid
    overlap = mesh.volume() - solid.Volume
    offset = (mesh.mid_area() * mesh.T - mesh.volume() + overlap) \
        - FP.expected_bend_volume_change(bends)
    return solid, flat, flat_tris, mid, bends, offset, overlap


class SMLoftedBend:
    def __init__(self, obj, profile_a, profile_b):
        self.addVerifyProperties(obj)
        obj.profileA = profile_a
        obj.profileB = profile_b
        obj.Proxy = self

    def addVerifyProperties(self, obj):
        tr = FreeCAD.Qt.translate
        for name, tip in (("profileA", "First open planar profile (sketch or wire)"),
                          ("profileB", "Second open planar profile, on a parallel plane")):
            if name not in obj.PropertiesList:
                obj.addProperty("App::PropertyLink", name, "Parameters", tip)
        SheetMetalTools.smAddLengthProperty(obj, "thickness", tr("App::Property", "Sheet thickness T"), 2.0)
        SheetMetalTools.smAddEnumProperty(
            obj, "side", "Which way the thickness goes from the mid-surface: mid is +-T/2, inside "
            "T toward the profiles' centroid, outside T away from it", SIDES, "mid")
        SheetMetalTools.smAddIntProperty(obj, "facets", tr("App::Property", "Facets per curved segment"), 4)
        SheetMetalTools.smAddEnumProperty(
            obj, "alignment", "Direction of profile B against A (auto: ends nearest to A's)",
            ALIGNMENTS, "auto")
        SheetMetalTools.smAddEnumProperty(obj, "method", "faceted (press brake); smooth is refused",
                                          METHODS, "faceted")
        SheetMetalTools.smAddBoolProperty(obj, "invertSides", tr("App::Property", "Swap inside and outside"), False)
        SheetMetalTools.smAddIntProperty(obj, "triangleCount", tr("App::Property", "Facets built"), 0)
        obj.setEditorMode("triangleCount", 1)
        SheetMetalTools.smAddIntProperty(obj, "bendCount", tr("App::Property", "Hinges that fold"), 0)
        obj.setEditorMode("bendCount", 1)
        SheetMetalTools.smAddFloatProperty(obj, "flatVolumeOffset", tr("App::Property", "Exact (flat volume - part volume) minus the bends' expected change, mm3"), 0.0)
        obj.setEditorMode("flatVolumeOffset", 1)
        SheetMetalTools.smAddFloatProperty(obj, "midSurfaceArea", tr("App::Property", "Area of the mid-surface, mm2"), 0.0)
        obj.setEditorMode("midSurfaceArea", 1)
        SheetMetalTools.smAddFloatProperty(obj, "facetVolume", tr("App::Property", "Sum of the facet prisms' volumes (closed form), mm3"), 0.0)
        obj.setEditorMode("facetVolume", 1)
        SheetMetalTools.smAddFloatProperty(obj, "overlapVolume", tr("App::Property", "Volume where facet prisms overlap at the corner fans (counted once), mm3"), 0.0)
        obj.setEditorMode("overlapVolume", 1)
        for name in ("flatPattern", "flatTriangles", "midSurface"):
            if name not in obj.PropertiesList:
                obj.addProperty("Part::PropertyPartShape", name, "Result", "Result shape")
                obj.setEditorMode(name, 2)
        if "flatBendData" not in obj.PropertiesList:
            obj.addProperty("App::PropertyString", "flatBendData", "Result", "Flat pattern bends")
            obj.setEditorMode("flatBendData", 2)

    def getElementMapVersion(self, _fp, ver, _prop, restored):
        return smElementMapVersion + ver if not restored else None

    def execute(self, fp):
        self.addVerifyProperties(fp)
        if fp.profileA is None or fp.profileB is None:
            raise FreeCAD.Base.FreeCADError("Lofted bend: both profiles are needed")
        try:
            mesh = analyse(
                fp.profileA.Shape, fp.profileB.Shape, fp.thickness.Value, str(fp.side),
                int(fp.facets), str(fp.alignment), bool(fp.invertSides), str(fp.method),
                _sketch_normal(fp.profileA), _sketch_normal(fp.profileB))
            solid, flat, flat_tris, mid, bends, offset, overlap = make_all(mesh)
        except LoftedBendError as exc:
            raise FreeCAD.Base.FreeCADError("Lofted bend: %s" % exc)
        fp.triangleCount = len(mesh.tris)
        fp.bendCount = len(bends)
        fp.flatVolumeOffset = offset
        fp.midSurfaceArea = mesh.mid_area()
        fp.facetVolume = mesh.volume()
        fp.overlapVolume = overlap
        fp.flatPattern = flat
        fp.flatTriangles = flat_tris
        fp.midSurface = mid
        fp.flatBendData = FP.bends_to_json(bends, (0.0, 0.0, 1.0))
        fp.Shape = solid


def make_lofted_bend(doc, profile_a, profile_b, name="LoftedBend", **props):
    """Create a recomputed lofted bend; props set the feature's properties."""
    obj = doc.addObject("Part::FeaturePython", name)
    SMLoftedBend(obj, profile_a, profile_b)
    for key, value in props.items():
        setattr(obj, key, value)
    doc.recompute()
    SheetMetalTools.attach_view_provider(obj)
    return obj


###################################################################################################
# Flat pattern for the Unfold feature
###################################################################################################

def lofted_source(obj):
    """The lofted bend feature itself when `obj` is one, else None."""
    if obj is not None and type(getattr(obj, "Proxy", None)).__name__ == "SMLoftedBend":
        return obj
    return None


def in_chain(obj):
    """True when a lofted bend sits anywhere in the dependency chain of `obj`."""
    seen, stack = set(), [obj]
    while stack:
        o = stack.pop()
        if o is None or o.Name in seen:
            continue
        seen.add(o.Name)
        if lofted_source(o) is not None:
            return True
        stack.extend(o.OutList)
    return False


def lofted_flat(lofted):
    """(flat solid, bends, normal, problem, volume offset) of a lofted bend feature, the flat
    checked by flat_problem with the feature's exact volume offset."""
    if lofted.flatPattern.isNull():
        raise FreeCAD.Base.FreeCADError("Lofted bend: recompute the lofted bend first")
    shape = lofted.flatPattern.copy()
    bends = [FP.FlatBend.from_dict(d) for d in json.loads(lofted.flatBendData)["bends"]]
    normal = Vector(0, 0, 1)
    thickness = lofted.thickness.Value
    problem = FP.flat_problem(shape, normal, bends, thickness, len(bends), lofted.Shape.Volume,
                              (), float(lofted.flatVolumeOffset))
    return shape, bends, normal, problem
