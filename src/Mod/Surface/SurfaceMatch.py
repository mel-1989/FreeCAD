# SPDX-License-Identifier: LGPL-2.1-or-later

"""G2 match of a B-spline surface side to a target face's edge: ``matchSurface`` (G09 slice 5a).

``matchSurface(face, side, target_face, target_edge, continuity=2, ...)`` moves the poles beside one of
the four sides of a B-spline face so that the side lands on the target edge (G0), meets the target
face's tangent plane (G1) or its normal curvature (G2), changing only the first ``continuity + 1`` pole
rows beside the side and by the least change that meets the constraints. Where the target edge cannot be
met on the source's own knots, the along-side knots are refined (midpoint insertion, level by level) and
the fit repeated; the loop stops at the first level that meets the thresholds.

Public sources. B-spline basis functions and their derivatives, knot insertion and degree elevation
(L. Piegl and W. Tiller, *The NURBS Book*, 2nd ed., 1997, chapters 2, 3 and 5; the end-point property
of a clamped knot vector gives the derivatives across a boundary from the first rows only, and the
rational surface derivatives follow from the quotient rule of chapter 4); the second fundamental form
and the normal curvature of a surface (M. P. do Carmo, *Differential Geometry of Curves and Surfaces*,
1976, chapter 3); the linkage-curve theorem (A. Pegna and F. Wolter, 1992): across a common G1
boundary, G2 holds when the normal curvatures agree in one direction transverse to it; the continuity
measure of ``SurfaceAnalysis.edge_continuity`` (slice 1). Nothing is copied from another CAD system.

The surface is linear in its poles once the weights are fixed, so each row is a linear least-change
problem. Row 0 minimises the weighted squared distance of the side to arc-length paired target points
(the two ends weighted 100) plus 1e-6 times the squared change. Rows 1 and 2 are the least change that
meets ``N_t . S_v = 0`` and ``N_t . S_vv = kappa_t |S_v|^2`` at the sample points, by a penalty-weighted
and damped least-squares solve (plain minimum-norm least squares moves poles by tens of millimetres and
fails). Weights never change.

Sides: ``U0``, ``U1``, ``V0``, ``V1`` (u or v at its first or last bound), as in ``SurfacePoleEdit``.
"""

import math

import FreeCAD as App
import Part
import numpy as np

import SurfaceAnalysis as SA
import SurfacePoleEdit as PE

__all__ = ["matchSurface", "SIDES"]

SIDES = PE.SIDES

_ANGLE_LIMIT = 0.05  # degrees
_JUMP_LIMIT = 0.01
_SAMPLES = 41
_END_WEIGHT = 100.0
_DAMP0 = 1e-6
_PENALTY = 1e5
_STRETCH_LOW, _STRETCH_HIGH = 0.2, 4.0
_WEIGHT_TOL = 1e-12


# ------------------------------------------------------------------------- B-spline basis (own)


def _find_span(n, p, u, knots):
    """Knot span of ``u`` (``n`` is the number of poles minus 1), Piegl and Tiller algorithm A2.1."""
    if u >= knots[n + 1]:
        return n
    if u <= knots[p]:
        return p
    lo, hi = p, n + 1
    mid = (lo + hi) // 2
    while u < knots[mid] or u >= knots[mid + 1]:
        if u < knots[mid]:
            hi = mid
        else:
            lo = mid
        mid = (lo + hi) // 2
    return mid


def _ders_basis(span, u, p, nd, knots):
    """Derivatives up to order ``nd`` of the ``p + 1`` nonzero basis functions (algorithm A2.3):
    ``ders[k][j]`` is the k-th derivative of ``N_{span - p + j}``."""
    ndu = np.zeros((p + 1, p + 1))
    ndu[0, 0] = 1.0
    left = np.zeros(p + 1)
    right = np.zeros(p + 1)
    for j in range(1, p + 1):
        left[j] = u - knots[span + 1 - j]
        right[j] = knots[span + j] - u
        saved = 0.0
        for r in range(j):
            ndu[j, r] = right[r + 1] + left[j - r]
            temp = ndu[r, j - 1] / ndu[j, r]
            ndu[r, j] = saved + right[r + 1] * temp
            saved = left[j - r] * temp
        ndu[j, j] = saved
    ders = np.zeros((nd + 1, p + 1))
    for j in range(p + 1):
        ders[0, j] = ndu[j, p]
    a = np.zeros((2, p + 1))
    for r in range(p + 1):
        s1, s2 = 0, 1
        a[0, 0] = 1.0
        for k in range(1, nd + 1):
            d = 0.0
            rk, pk = r - k, p - k
            if r >= k:
                a[s2, 0] = a[s1, 0] / ndu[pk + 1, rk]
                d = a[s2, 0] * ndu[rk, pk]
            j1 = 1 if rk >= -1 else -rk
            j2 = k - 1 if r - 1 <= pk else p - r
            for j in range(j1, j2 + 1):
                a[s2, j] = (a[s1, j] - a[s1, j - 1]) / ndu[pk + 1, rk + j]
                d += a[s2, j] * ndu[rk + j, pk]
            if r <= pk:
                a[s2, k] = -a[s1, k - 1] / ndu[pk + 1, r]
                d += a[s2, k] * ndu[r, pk]
            ders[k, r] = d
            s1, s2 = s2, s1
    r = p
    for k in range(1, nd + 1):
        ders[k] *= r
        r *= p - k
    return ders


def _basis_row(u, count, p, knots):
    span = _find_span(count - 1, p, u, knots)
    d = _ders_basis(span, u, p, 0, knots)
    out = np.zeros(count)
    out[span - p : span + 1] = d[0]
    return out


# ------------------------------------------------------------------------- small helpers


def _finite_positive(x):
    return not isinstance(x, bool) and isinstance(x, (int, float)) and math.isfinite(x) and x > 0


def _arr(v):
    return np.array([v.x, v.y, v.z])


def _vec(a):
    return App.Vector(float(a[0]), float(a[1]), float(a[2]))


def _points(edge, count=11):
    f, l = edge.FirstParameter, edge.LastParameter
    return [edge.valueAt(f + (l - f) * k / (count - 1.0)) for k in range(count)]


def _dist(p, shape):
    return Part.Vertex(p).distToShape(shape)[0]


def _side_iso(surface, side):
    u0, u1, v0, v1 = surface.bounds()
    return {"U0": lambda: surface.uIso(u0), "U1": lambda: surface.uIso(u1), "V0": lambda: surface.vIso(v0), "V1": lambda: surface.vIso(v1)}[side]().toShape()


def _side_uv(surface, side, t):
    u0, u1, v0, v1 = surface.bounds()
    return {"V0": (t, v0), "V1": (t, v1), "U0": (u0, t), "U1": (u1, t)}[side]


def _cross_derivative(surface, side, u, v):
    """The derivative across the side, pointing from the side into the surface."""
    if side[0] == "V":
        d = surface.getDN(u, v, 0, 1)
    else:
        d = surface.getDN(u, v, 1, 0)
    return d * -1.0 if side[1] == "1" else d


def _is_straight(edge, tolerance):
    a, b = edge.valueAt(edge.FirstParameter), edge.valueAt(edge.LastParameter)
    chord = b - a
    if chord.Length <= 1e-12:
        return False
    c = chord * (1.0 / chord.Length)
    for p in _points(edge, 11):
        w = p - a
        if (w - c * w.dot(c)).Length > tolerance:
            return False
    return True


def _face_normal(face, point):
    u, v = face.Surface.parameter(point)
    n = face.normalAt(u, v)
    n.normalize()
    return n


# ------------------------------------------------------------------------- target frames


class _TargetFrame:
    """The target face at a point of its edge: the unit normal (``face.normalAt``'s orientation) and the
    normal curvature in any tangent direction against that normal."""

    def __init__(self, face, point):
        surf = face.Surface
        u, v = surf.parameter(point)
        n = face.normalAt(u, v)
        n.normalize()
        self.su, self.sv = surf.getDN(u, v, 1, 0), surf.getDN(u, v, 0, 1)
        self.suu, self.suv, self.svv = surf.getDN(u, v, 2, 0), surf.getDN(u, v, 1, 1), surf.getDN(u, v, 0, 2)
        nn = self.su.cross(self.sv)
        nn.normalize()
        self.N = nn * -1.0 if nn.dot(n) < 0 else nn

    def curvature(self, w):
        return _normal_curvature(self.su, self.sv, self.suu, self.suv, self.svv, self.N, w)


def _normal_curvature(su, sv, suu, suv, svv, n, w):
    """II(w, w) / I(w, w) of the surface with these derivatives, against the unit normal ``n``, for the
    tangent-plane projection of ``w``."""
    e, f, g = su.dot(su), su.dot(sv), sv.dot(sv)
    det = e * g - f * f
    if det <= 0.0:
        return 0.0
    r1, r2 = w.dot(su), w.dot(sv)
    a = (r1 * g - r2 * f) / det
    b = (r2 * e - r1 * f) / det
    i2 = n.dot(suu * (a * a) + suv * (2 * a * b) + svv * (b * b))
    i1 = a * a * e + 2 * a * b * f + b * b * g
    return i2 / i1 if i1 > 0.0 else 0.0


def _into_target(face, point, d, scale):
    """``d`` or ``-d``, whichever points from the edge point into the target face."""
    surf = face.Surface
    for h in (1e-3 * scale, 1e-4 * scale, 1e-2 * scale):
        inside = []
        for s in (1.0, -1.0):
            q = point + d * (s * h)
            u, v = surf.parameter(q)
            inside.append(bool(face.isPartOfDomain(u, v)))
        if inside[0] != inside[1]:
            return d if inside[0] else d * -1.0
    return d if (face.CenterOfMass - point).dot(d) >= 0 else d * -1.0


# ------------------------------------------------------------------------- canonical form


class _Canon:
    """The surface with the side turned into the cross direction's first bound and the along direction
    first: poles ``P[i][r]`` (``i`` along, ``r`` rows from the side), weights ``W`` and knot vectors."""


def _read(surface, side):
    nu, nv = surface.NbUPoles, surface.NbVPoles
    pl, wl = surface.getPoles(), surface.getWeights()
    P = np.array([[_arr(pl[i][j]) for j in range(nv)] for i in range(nu)])
    W = np.array([[wl[i][j] for j in range(nv)] for i in range(nu)])
    ku, kv = np.array(surface.UKnotSequence), np.array(surface.VKnotSequence)
    pu, pv = surface.UDegree, surface.VDegree
    if side[0] == "U":
        P, W = P.transpose(1, 0, 2), W.T
        ku, kv, pu, pv = kv, ku, pv, pu
    if side[1] == "1":
        P, W = P[:, ::-1], W[:, ::-1]
        kv = -kv[::-1]
    c = _Canon()
    c.P, c.W, c.ka, c.kc, c.pa, c.pc = P.copy(), W.copy(), ku, kv, pu, pv
    return c


def _from_canon(side, P):
    if side[1] == "1":
        P = P[:, ::-1]
    if side[0] == "U":
        P = P.transpose(1, 0, 2)
    return P


def _with_poles(surface, side, P, rows):
    """A copy of ``surface`` whose poles are ``P`` (canonical form); only the first ``rows`` rows are set."""
    out = surface.copy()
    full = _from_canon(side, P)
    nu, nv = full.shape[0], full.shape[1]
    for r in range(rows):
        if side[0] == "V":
            idx = [(i, (r if side[1] == "0" else nv - 1 - r)) for i in range(nu)]
        else:
            idx = [((r if side[1] == "0" else nu - 1 - r), j) for j in range(nv)]
        for i, j in idx:
            out.setPole(i + 1, j + 1, _vec(full[i, j]))
    return out


def _refine(surface, side):
    """Insert the midpoint of every along-direction knot span."""
    out = surface.copy()
    if side[0] == "V":
        kn = out.getUKnots()
        mids = [(kn[i] + kn[i + 1]) / 2.0 for i in range(len(kn) - 1)]
        out.insertUKnots(mids, [1] * len(mids), 1e-12)
    else:
        kn = out.getVKnots()
        mids = [(kn[i] + kn[i + 1]) / 2.0 for i in range(len(kn) - 1)]
        out.insertVKnots(mids, [1] * len(mids), 1e-12)
    return out


# ------------------------------------------------------------------------- the fit


def _correspondence(src_edge, tgt_edge, m):
    """Parameters of ``m`` arc-length-paired points on the source side and on the target edge; the target
    edge is taken reversed by slice 3c's rule."""
    a0, a1 = src_edge.valueAt(src_edge.FirstParameter), src_edge.valueAt(src_edge.LastParameter)
    b0, b1 = tgt_edge.valueAt(tgt_edge.FirstParameter), tgt_edge.valueAt(tgt_edge.LastParameter)
    rev = (a0 - b1).Length + (a1 - b0).Length < (a0 - b0).Length + (a1 - b1).Length
    ls, lt = src_edge.Length, tgt_edge.Length
    ts, tt = [], []
    for f in np.linspace(0.0, 1.0, m):
        ts.append(src_edge.getParameterByLength(f * ls))
        tt.append(tgt_edge.getParameterByLength(((1.0 - f) if rev else f) * lt))
    return np.array(ts), np.array(tt)


def _solve(M, r):
    """The least change ``dx`` with ``M dx = r``: the constraint rows scaled to unit size, weighted by the
    penalty, against identity damping."""
    scale = float(np.abs(M).max())
    if scale <= 0.0:
        return np.zeros(M.shape[1])
    A = np.vstack([_PENALTY * (M / scale), np.eye(M.shape[1])])
    b = np.concatenate([_PENALTY * (r / scale), np.zeros(M.shape[1])])
    return np.linalg.lstsq(A, b, rcond=1e-10)[0]


def _fit(ref, side, k, tface, tedge):
    """One fit on ``ref`` (the degree-raised, refined original): the surface with rows 0 to k moved."""
    d = _read(ref, side)
    n, mc = d.P.shape[0], d.P.shape[1]
    q = d.pc
    m = 5 * n
    ts, tt = _correspondence(_side_iso(ref, side), tedge, m)
    N = np.array([_basis_row(t, n, d.pa, d.ka) for t in ts])
    cb = _ders_basis(_find_span(mc - 1, q, d.kc[q], d.kc), d.kc[q], q, 2, d.kc)
    rows = min(3, q + 1)
    b1, b2 = np.zeros(3), np.zeros(3)
    b1[:rows], b2[:rows] = cb[1][:rows], cb[2][:rows]
    nr = min(3, mc)
    wt = N * d.W[:, 0][None, :]
    w0 = wt.sum(1)
    C = [N * d.W[:, r][None, :] / w0[:, None] for r in range(nr)]
    c = [C[r].sum(1) for r in range(nr)]
    frames = [_TargetFrame(tface, tedge.valueAt(t)) for t in tt]
    T = np.array([_arr(tedge.valueAt(t)) for t in tt])
    Nt = np.array([_arr(f.N) for f in frames])

    P = d.P.copy()
    weight = np.ones(m)
    weight[0] = weight[-1] = _END_WEIGHT
    A = np.vstack([weight[:, None] * C[0], math.sqrt(_DAMP0) * np.eye(n)])
    for dim in range(3):
        b = np.concatenate([weight * T[:, dim], math.sqrt(_DAMP0) * d.P[:, 0, dim]])
        P[:, 0, dim] = np.linalg.lstsq(A, b, rcond=None)[0]

    def derivs():
        S = C[0] @ P[:, 0]
        Sv = sum(b1[r] * (C[r] @ P[:, r] - c[r][:, None] * S) for r in range(rows))
        Svv = sum(b2[r] * (C[r] @ P[:, r] - c[r][:, None] * S) for r in range(rows)) - 2.0 * (sum(b1[r] * c[r] for r in range(rows)))[:, None] * Sv
        return Sv, Svv

    def operator(r, b):
        M = np.zeros((m, 3 * n))
        for dim in range(3):
            M[:, dim::3] = b * C[r] * Nt[:, dim][:, None]
        return M

    if k >= 1:
        Sv, _ = derivs()
        dx = _solve(operator(1, b1[1]), -np.einsum("ij,ij->i", Nt, Sv))
        P[:, 1, :] += dx.reshape(n, 3)
    if k >= 2:
        Sv, Svv = derivs()
        kappa = np.array([f.curvature(_vec(Sv[j])) for j, f in enumerate(frames)])
        res = kappa * np.einsum("ij,ij->i", Sv, Sv) - np.einsum("ij,ij->i", Nt, Svv)
        dx = _solve(operator(2, b2[2]), res)
        P[:, 2, :] += dx.reshape(n, 3)
    return _with_poles(ref, side, P, k + 1), d.P


# ------------------------------------------------------------------------- measuring and the fold rule


def _closest_on_edge(edge, p):
    """(distance, closest point, parameter) of ``p`` against ``edge``."""
    dist, pts, info = edge.distToShape(Part.Vertex(p))
    return dist, pts[0][0], info[0][2]


def _inspect(new, ref, side, tface, tedge, size):
    """At the 41 parameters (j + 0.37) / 41 of the new side's arc length: gap to the target edge, normal
    angle, relative normal-curvature jump, the fold flags and the stretch."""
    edge = _side_iso(new, side)
    length = edge.Length
    scale = tface.BoundBox.DiagonalLength
    floor = 0.1 / size
    gap = angle = jump = 0.0
    fold_dot = nflip = foldback = None
    ratios = []
    for j in range(_SAMPLES):
        t = edge.getParameterByLength((j + 0.37) / _SAMPLES * length)
        u, v = _side_uv(new, side, t)
        p = new.value(u, v)
        su, sv = new.getDN(u, v, 1, 0), new.getDN(u, v, 0, 1)
        n = su.cross(sv)
        n.normalize()
        svc = _cross_derivative(new, side, u, v)
        ref_sv = _cross_derivative(ref, side, u, v)
        ref_n = ref.getDN(u, v, 1, 0).cross(ref.getDN(u, v, 0, 1))
        ref_n.normalize()
        dist, q, tp = _closest_on_edge(tedge, p)
        frame = _TargetFrame(tface, q)
        tg = tedge.tangentAt(tp)
        tg.normalize()
        d_in = frame.N.cross(tg)
        d_in.normalize()
        d_in = _into_target(tface, q, d_in, scale)

        ratios.append(svc.Length / ref_sv.Length)
        fd = svc.dot(ref_sv) / ref_sv.dot(ref_sv)
        fold_dot = fd if fold_dot is None else min(fold_dot, fd)
        nf = n.dot(ref_n)
        nflip = nf if nflip is None else min(nflip, nf)
        fb = svc.dot(d_in)
        foldback = fb if foldback is None else max(foldback, fb)

        gap = max(gap, dist)
        s = 1.0 if n.dot(frame.N) >= 0 else -1.0
        angle = max(angle, math.degrees(math.atan2(n.cross(frame.N).Length, n.dot(frame.N) * s)))
        suu, suv, svv = new.getDN(u, v, 2, 0), new.getDN(u, v, 1, 1), new.getDN(u, v, 0, 2)
        w_cross = svc * (1.0 / svc.Length)
        bis = w_cross + tg
        dirs = [w_cross, tg]
        if bis.Length > 1e-12:
            dirs.append(bis * (1.0 / bis.Length))
        for w in dirs:
            ks = _normal_curvature(su, sv, suu, suv, svv, frame.N, w)
            kt = frame.curvature(w)
            jump = max(jump, abs(ks - kt) / max(abs(ks), abs(kt), floor))
    return {
        "gap": gap,
        "angle": angle,
        "jump": jump,
        "fold_dot": fold_dot,
        "nflip": nflip,
        "foldback": foldback,
        "stretch": (min(ratios), max(ratios)),
    }


def _larger(a, b):
    if b != b:  # edge_continuity could not judge this one; the own check stands alone
        return a
    return max(a, b)


def _passes(values, k, tolerance):
    ok = values["gap"] <= tolerance
    if k >= 1:
        ok = ok and values["angle"] <= _ANGLE_LIMIT
    if k >= 2:
        ok = ok and values["jump"] <= _JUMP_LIMIT
    return ok


# ------------------------------------------------------------------------- validation


def _resolve_side(side, face, surface, tolerance):
    """The side name for ``side`` (a name or an edge of the face)."""
    if isinstance(side, str):
        return side
    best, best_d = None, None
    for name in SIDES:
        try:
            iso = _side_iso(surface, name)
        except Exception:
            continue
        if iso.Length <= 1e-9:
            continue
        dmax = max(_dist(p, iso) for p in _points(side))
        if best_d is None or dmax < best_d:
            best, best_d = name, dmax
    if best is not None:
        same = any(e.isSame(side) for e in face.Edges)
        if best_d <= tolerance or (same and best_d <= max(tolerance, 1e-6)):
            return best
    raise ValueError("InvalidSide: the edge is not one of the face's four sides")


def _check_target_edge(face, edge, tolerance):
    if edge.isNull() or getattr(edge, "Degenerated", False) or edge.Length <= tolerance:
        raise ValueError("DegenerateEdge: the target edge is degenerate")
    a, b = edge.valueAt(edge.FirstParameter), edge.valueAt(edge.LastParameter)
    if edge.isClosed() or (a - b).Length <= tolerance:
        raise ValueError("ClosedEdge: the target edge is closed")
    if any(e.isSame(edge) for e in face.Edges):
        return
    if not all(_dist(p, face) <= tolerance for p in _points(edge)):
        raise ValueError("EdgeNotOnFace: the target edge is not an edge of the target face")


def _oriented_like(new_surface, ref_surface, source_face):
    """A face on ``new_surface`` with the source face's orientation."""
    face = new_surface.toShape()
    u0, u1, v0, v1 = ref_surface.bounds()
    um, vm = (u0 + u1) / 2.0, (v0 + v1) / 2.0
    p = ref_surface.value(um, vm)
    n_ref = ref_surface.getDN(um, vm, 1, 0).cross(ref_surface.getDN(um, vm, 0, 1))
    n_ref.normalize()
    if n_ref.dot(_face_normal(source_face, p)) < 0:
        face = face.copy()
        face.reverse()
    return face


# ------------------------------------------------------------------------- the API


def matchSurface(face, side, target_face, target_edge, continuity=2, tolerance=1e-4, max_level=5, approximate=False):
    """Move the poles beside ``side`` of the B-spline ``face`` so that the side meets ``target_edge`` of
    ``target_face`` with the chosen ``continuity`` (0, 1 or 2).

    Returns a dict ``surface`` (``Part.BSplineSurface``), ``face`` (a ``Part.Face`` on it, oriented like
    ``face``), ``level``, ``poles_along``, ``degree_along``, ``approximate``, ``gap`` (mm), ``angle``
    (degrees), ``curvature_jump``, ``stretch`` (min and max of ``|S'_v| / |S_v|`` across the side),
    ``max_move`` (mm, the largest pole displacement against the refined original) and ``warnings``.

    Raises ``TypeError`` for a wrong argument type and ``ValueError`` whose message starts with
    ``InvalidTolerance``, ``InvalidContinuity``, ``InvalidMaxLevel``, ``InvalidSide``, ``TrimmedFace`` (and
    the other refusals of ``SurfacePoleEdit.editable_surface``), ``PeriodicSurface``, ``InsufficientRows``,
    ``DegenerateEdge``, ``ClosedEdge``, ``EdgeNotOnFace``, ``RationalG2``, ``FoldedResult`` or
    ``DeviationExceeded`` (unless ``approximate`` is true, which returns the last level instead).
    """
    for item, kind, name in ((face, Part.Face, "face"), (target_face, Part.Face, "target_face"), (target_edge, Part.Edge, "target_edge")):
        if not isinstance(item, kind):
            raise TypeError("TypeError: %s must be a Part.%s" % (name, kind.__name__))
    if not _finite_positive(tolerance):
        raise ValueError("InvalidTolerance: tolerance must be finite and greater than 0")
    if isinstance(continuity, bool) or not isinstance(continuity, int) or continuity not in (0, 1, 2):
        raise ValueError("InvalidContinuity: continuity must be 0, 1 or 2, not %r" % (continuity,))
    if isinstance(max_level, bool) or not isinstance(max_level, int) or not 0 <= max_level <= 8:
        raise ValueError("InvalidMaxLevel: max_level must be an integer from 0 to 8, not %r" % (max_level,))
    if not ((isinstance(side, str) and side in SIDES) or isinstance(side, Part.Edge)):
        raise ValueError("InvalidSide: side must be one of U0, U1, V0, V1 or an edge of the face, not %r" % (side,))
    k = continuity

    source = PE.editable_surface(face)
    side = _resolve_side(side, face, source, tolerance)
    cross_periodic = source.isVPeriodic() if side[0] == "V" else source.isUPeriodic()
    along_periodic = source.isUPeriodic() if side[0] == "V" else source.isVPeriodic()
    if cross_periodic or along_periodic:
        raise ValueError("PeriodicSurface: the surface is periodic across or along the side")
    canon = _read(source, side)
    mc, q = canon.P.shape[1], canon.pc
    if mc < k + 2 or q < k or canon.kc[0] != canon.kc[q]:
        raise ValueError(
            "InsufficientRows: the cross direction has %d poles at degree %d (continuity %d needs at least %d poles, degree %d and a clamped end)"
            % (mc, q, k, k + 2, k)
        )
    _check_target_edge(target_face, target_edge, tolerance)
    if k == 2:
        for r in range(3):
            w = canon.W[:, r]
            if np.abs(w - w[0]).max() > _WEIGHT_TOL * np.abs(w).max():
                raise ValueError("RationalG2: the weights of the three rows beside the side are not constant along it")

    ref0 = source
    if k >= 1 and not _is_straight(target_edge, tolerance):
        along_degree = canon.pa
        if along_degree < 5:
            ref0 = source.copy()
            if side[0] == "V":
                ref0.increaseDegree(5, ref0.VDegree)
            else:
                ref0.increaseDegree(ref0.UDegree, 5)

    box = App.BoundBox(face.BoundBox)
    box.add(target_face.BoundBox)
    size = box.DiagonalLength
    mid = target_edge.valueAt((target_edge.FirstParameter + target_edge.LastParameter) / 2.0)

    ref = ref0
    last = None
    for level in range(max_level + 1):
        if level > 0:
            ref = _refine(ref, side)
        new, _ = _fit(ref, side, k, target_face, target_edge)
        seen = _inspect(new, ref, side, target_face, target_edge, size)
        if seen["fold_dot"] <= 0.0 or seen["nflip"] < 0.0 or seen["foldback"] > 0.0:
            raise ValueError(
                "FoldedResult: at level %d the matched surface folds (cross-derivative ratio %.3g, normal dot %.3g, direction into the target %.3g)"
                % (level, seen["fold_dot"], seen["nflip"], seen["foldback"])
            )
        result_face = _oriented_like(new, ref, face)
        measured = result_face
        if _face_normal(measured, mid).dot(_face_normal(target_face, mid)) < 0:
            measured = result_face.copy()
            measured.reverse()
        r = SA.edge_continuity(None, target_edge, samples=_SAMPLES, faces=(measured, target_face))
        values = {
            "gap": _larger(seen["gap"], r["gap"]),
            "angle": _larger(seen["angle"], r["angle"]),
            "jump": _larger(seen["jump"], r["curvature_jump"]),
        }
        last = (level, new, ref, result_face, seen, values)
        if _passes(values, k, tolerance):
            break
    level, new, ref, result_face, seen, values = last
    met = _passes(values, k, tolerance)
    if not met and not approximate:
        raise ValueError(
            "DeviationExceeded: after level %d (%d poles along) the worst values are gap %.3g mm (limit %.3g), angle %.3g deg (limit %g), curvature jump %.3g (limit %g)"
            % (level, new.NbUPoles if side[0] == "V" else new.NbVPoles, values["gap"], tolerance, values["angle"], _ANGLE_LIMIT, values["jump"], _JUMP_LIMIT)
        )
    stretch = seen["stretch"]
    warnings = []
    if stretch[0] < _STRETCH_LOW or stretch[1] > _STRETCH_HIGH:
        warnings.append("StretchWarning")
    old_poles, new_poles = ref.getPoles(), new.getPoles()
    move = max((new_poles[i][j] - old_poles[i][j]).Length for i in range(len(old_poles)) for j in range(len(old_poles[0])))
    if side[0] == "V":
        poles_along, degree_along = new.NbUPoles, new.UDegree
    else:
        poles_along, degree_along = new.NbVPoles, new.VDegree
    return {
        "surface": new,
        "face": result_face,
        "level": level,
        "poles_along": poles_along,
        "degree_along": degree_along,
        "approximate": not met,
        "gap": values["gap"],
        "angle": values["angle"],
        "curvature_jump": values["jump"],
        "stretch": stretch,
        "max_move": move,
        "warnings": warnings,
    }
