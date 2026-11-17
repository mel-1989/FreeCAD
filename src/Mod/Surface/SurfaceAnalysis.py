# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *                                                                         *
# *   This program is free software; you can redistribute it and/or modify  *
# *   it under the terms of the GNU Lesser General Public License (LGPL)    *
# *   as published by the Free Software Foundation; either version 2 of     *
# *   the License, or (at your option) any later version.                   *
# *   for detail see the LICENCE text file.                                 *
# *                                                                         *
# *   This program is distributed in the hope that it will be useful,       *
# *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
# *   GNU Library General Public License for more details.                  *
# *                                                                         *
# *   You should have received a copy of the GNU Library General Public     *
# *   License along with this program; if not, write to the Free Software   *
# *   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  *
# *   USA                                                                   *
# *                                                                         *
# ***************************************************************************

"""Surface analysis for B-Rep faces: curvature, draft, deviation, continuity.

A pure-Python layer over the Part bindings (``Surface.curvature``,
``Face.normalAt``, ``Shape.distToShape`` and friends). It never creates
document objects and never modifies the shapes it is given.

Units: distances in mm, angles in degrees, curvatures in 1/mm.

Conventions
-----------
Curvature sign
    Curvature is signed against the face's outward normal, the vector
    ``face.normalAt`` returns. A convex face seen from outside is positive: the
    outer face of a solid sphere of radius R has ``mean == kmax == kmin ==
    +1/R`` and ``gauss == +1/R**2``. OCCT itself signs curvature against the
    bare surface normal and ignores face orientation, so this module flips the
    sign for a forward face and keeps it for a reversed one. ``kmax >= kmin``
    always: the flip swaps the two, and ``dmax`` and ``dmin`` swap with them.
    ``gauss`` does not depend on the normal's side.
Undefined points
    At sphere poles, a cone apex and anywhere the parametrisation collapses, all
    curvature fields are ``nan`` and the directions are ``None``; nothing is
    raised for a (u, v) inside the parameter range. Collapse is detected as a
    parametric speed ratio below ``_DEGENERATE_RATIO`` or a sine of the angle
    between ``Su`` and ``Sv`` below the same value.
Umbilic points
    Where ``kmax == kmin`` the principal directions are arbitrary; whatever
    OCCT returns is passed through (``None`` if it raises, as on a plane).
Sampling
    Maps use the open grid ``u_i = u0 + (i + 0.5) (u1 - u0) / nu`` over
    ``face.ParameterRange`` and keep samples inside the trimmed domain.
Draft
    ``90 - angle(normalAt, pull)`` in degrees, so a face looking along the pull
    direction is +90 and one looking against it is -90.
Edge continuity
    Evaluated at ``samples`` points evenly spaced and strictly inside the edge
    (``first + (i + 1) (last - first) / (samples + 1)``). Four maxima are
    reported: ``gap`` (distance between the two faces' points), ``angle``
    (between the two outward normals, 0 on a smooth shell), ``curvature_jump``
    (relative difference of normal curvature, taken along the edge tangent, the
    across-edge direction and their bisector, each projected into the face's
    tangent plane; floor 1e-3 per mm) and ``g3_jump`` (relative difference of
    the derivative of normal curvature along the direction of travel from the
    first face into the second, along the edge tangent and the across
    direction; floor 1e-3 per mm squared).
G3 measure
    A one-sided, second-order finite difference ``(3 k0 - 4 k1 + k2) / (2 h)``
    on each side, with ``k_i`` the normal curvature after ``i`` steps of
    ``h = 1e-3 * D`` into the face (``D`` the larger bounding-box diagonal of
    the two faces), halved up to six times until two steps stay in the trimmed
    domain. The fixed 3D direction is re-projected into the tangent plane at
    every step, so the quantity is a scalar field and the derivative along the
    path depends only on the path's tangent at the edge. The step was chosen so
    that a cubic profile ``y = x**3`` joined to a plane at ``x = 0`` reads G2
    (the two derivatives are 6 and 0 per mm squared), and surfaces split
    anywhere read G3 (the difference between the two one-sided estimates is
    the truncation error, far below the 0.05 tolerance). If no step fits
    inside a face ``g3_jump`` is ``nan`` and the level stops at G2.

Sources (clean room): do Carmo, *Differential Geometry of Curves and
Surfaces*, for principal curvatures, normal curvature (Euler's formula) and
the Gauss and mean curvature; OCCT's public ``GeomLProp`` documentation for the
numerical curvature evaluation; the generic published definitions of G0 to G3
geometric continuity.
"""

import math

import FreeCAD
import Part

__all__ = [
    "curvature",
    "curvature_map",
    "draft_angle",
    "draft_map",
    "deviation",
    "edge_continuity",
]

_NAN = float("nan")
_DEGENERATE_RATIO = 1e-7
_DEFAULT_TOL = {"gap": 1e-4, "angle": 0.05, "curvature": 0.01, "g3": 0.05}
_CURV_FLOOR = 1e-3
_G3_STEP_FRACTION = 1e-3


def _is_nan(x):
    return x != x


def _require_face(face):
    if not isinstance(face, Part.Shape) or face.ShapeType != "Face":
        raise TypeError("a Part.Face is required")


class _FaceCtx:
    """A face with its bare surface and the sign needed for outward curvature."""

    def __init__(self, face):
        _require_face(face)
        self.face = face
        self.surf = face.Surface
        # OCCT signs against the bare surface normal, normalAt flips on a
        # reversed face. Outward-positive therefore negates a forward face's
        # values and keeps a reversed face's.
        self.reversed = face.Orientation == "Reversed"
        self.sign = 1.0 if self.reversed else -1.0

    def degenerate(self, u, v):
        try:
            su, sv = self.face.derivative1At(u, v)
        except Exception:
            return True
        lu, lv = su.Length, sv.Length
        top, low = max(lu, lv), min(lu, lv)
        if top <= 0.0 or low < _DEGENERATE_RATIO * top:
            return True
        return su.cross(sv).Length < _DEGENERATE_RATIO * lu * lv

    def eval(self, u, v):
        """Curvature dict at (u, v); curvature fields are nan where undefined."""
        out = {
            "gauss": _NAN,
            "mean": _NAN,
            "kmax": _NAN,
            "kmin": _NAN,
            "dmax": None,
            "dmin": None,
            "point": None,
            "normal": None,
        }
        try:
            out["point"] = self.face.valueAt(u, v)
        except Exception:
            pass
        try:
            out["normal"] = self.face.normalAt(u, v)
        except Exception:
            pass
        if self.degenerate(u, v):
            return out
        surf = self.surf
        try:
            k_hi = surf.curvature(u, v, "Max")
            k_lo = surf.curvature(u, v, "Min")
            gauss = surf.curvature(u, v, "Gauss")
            mean = surf.curvature(u, v, "Mean")
        except Exception:
            return out
        try:
            d_hi, d_lo = surf.curvatureDirections(u, v)
        except Exception:
            d_hi = d_lo = None
        s = self.sign
        out["gauss"] = gauss
        out["mean"] = s * mean + 0.0
        if s < 0:
            out["kmax"], out["kmin"] = 0.0 - k_lo, 0.0 - k_hi
            out["dmax"], out["dmin"] = d_lo, d_hi
        else:
            out["kmax"], out["kmin"] = k_hi, k_lo
            out["dmax"], out["dmin"] = d_hi, d_lo
        return out

    def normal_curvatures(self, u, v, dirs):
        """Normal curvature along each 3D direction, projected to the tangent plane.

        Euler's formula: k(d) = kmax (d.dmax)^2 + kmin (d.dmin)^2.
        """
        c = self.eval(u, v)
        n = c["normal"]
        if n is None or _is_nan(c["kmax"]):
            return [_NAN] * len(dirs)
        out = []
        for d in dirs:
            dp = d - n * d.dot(n)
            length = dp.Length
            if length < 1e-12:
                out.append(_NAN)
                continue
            dp = dp * (1.0 / length)
            if c["dmax"] is None or c["dmin"] is None:
                if abs(c["kmax"] - c["kmin"]) <= 1e-9 * max(1.0, abs(c["kmax"])):
                    out.append(c["kmax"])
                else:
                    out.append(_NAN)
                continue
            out.append(
                c["kmax"] * dp.dot(c["dmax"]) ** 2 + c["kmin"] * dp.dot(c["dmin"]) ** 2
            )
        return out

    def uv_direction(self, u, v, w):
        """(a, b) with a*Su + b*Sv equal to w projected into the tangent plane."""
        su, sv = self.face.derivative1At(u, v)
        e, f, g = su.dot(su), su.dot(sv), sv.dot(sv)
        r1, r2 = w.dot(su), w.dot(sv)
        det = e * g - f * f
        if det <= 0.0:
            return None
        return ((r1 * g - r2 * f) / det, (e * r2 - f * r1) / det)


def _grid(face, nu, nv):
    """Open-grid (u, v) samples inside the trimmed domain."""
    nu, nv = int(nu), int(nv)
    if nu < 1 or nv < 1:
        raise ValueError("nu and nv must be at least 1")
    u0, u1, v0, v1 = face.ParameterRange
    du, dv = (u1 - u0) / nu, (v1 - v0) / nv
    out = []
    for i in range(nu):
        u = u0 + (i + 0.5) * du
        for j in range(nv):
            v = v0 + (j + 0.5) * dv
            if face.isPartOfDomain(u, v):
                out.append((u, v))
    return out


def curvature(face, u, v):
    """Curvature of ``face`` at parameters (u, v), outward-positive.

    Returns a dict with ``gauss``, ``mean``, ``kmax``, ``kmin`` (1/mm),
    ``dmax``, ``dmin`` (unit principal directions or ``None``), ``point`` and
    ``normal`` (``face.normalAt``). See the module docstring for the sign
    convention and for undefined points (all curvature fields ``nan``).
    """
    return _FaceCtx(face).eval(u, v)


def curvature_map(face, nu=20, nv=20):
    """Curvature on the open ``nu`` x ``nv`` grid, trimmed samples dropped.

    Each item is the dict of :func:`curvature` plus ``u`` and ``v``.
    """
    ctx = _FaceCtx(face)
    out = []
    for u, v in _grid(face, nu, nv):
        c = ctx.eval(u, v)
        c["u"], c["v"] = u, v
        out.append(c)
    return out


def _unit(pull):
    p = FreeCAD.Vector(pull)
    if p.Length == 0.0:
        raise ValueError("pull direction must not be zero")
    return p * (1.0 / p.Length)


def _draft_from_normal(n, p):
    return math.degrees(math.asin(max(-1.0, min(1.0, n.dot(p)))))


def _classify(angle, min_draft):
    if angle >= min_draft:
        return "positive"
    if angle <= -min_draft:
        return "negative"
    return "insufficient"


def draft_angle(face, u, v, pull):
    """Draft angle in degrees at (u, v): 90 minus the angle between normal and pull."""
    _require_face(face)
    return _draft_from_normal(face.normalAt(u, v), _unit(pull))


def draft_map(face, pull, nu=20, nv=20, min_draft=1.0):
    """Draft on the open grid: dicts with ``u``, ``v``, ``point``, ``angle``, ``cls``.

    ``cls`` is ``positive`` (angle >= min_draft), ``negative`` (angle <=
    -min_draft) or ``insufficient``. Where the normal cannot be evaluated the
    angle is ``nan`` and ``cls`` is ``insufficient``.
    """
    _require_face(face)
    p = _unit(pull)
    out = []
    for u, v in _grid(face, nu, nv):
        try:
            angle = _draft_from_normal(face.normalAt(u, v), p)
        except Exception:
            angle = _NAN
        cls = "insufficient" if _is_nan(angle) else _classify(angle, min_draft)
        out.append(
            {"u": u, "v": v, "point": face.valueAt(u, v), "angle": angle, "cls": cls}
        )
    return out


def _point_distance(point, shape):
    return Part.Vertex(point).distToShape(shape)[0]


def deviation(face, target, nu=20, nv=20):
    """Distance statistics between ``face`` and ``target``.

    ``target`` is a ``Part.Shape``: the distance runs from each
    :func:`curvature_map` sample point of ``face`` to the target. Or it is a
    list of ``Vector``: the distance runs from each point to ``face``.
    Returns ``min``, ``max``, ``mean``, ``rms`` and ``count``.
    """
    _require_face(face)
    if isinstance(target, Part.Shape):
        points = [face.valueAt(u, v) for u, v in _grid(face, nu, nv)]
        dists = [_point_distance(p, target) for p in points]
    else:
        pts = []
        for p in target:
            pts.append(p.Point if isinstance(p, Part.Vertex) else FreeCAD.Vector(p))
        dists = [_point_distance(p, face) for p in pts]
    if not dists:
        raise ValueError("no points to measure")
    n = len(dists)
    return {
        "min": min(dists),
        "max": max(dists),
        "mean": sum(dists) / n,
        "rms": math.sqrt(sum(d * d for d in dists) / n),
        "count": n,
    }


def _same_edge(a, b):
    return a.isSame(b)


def _contains_edge(face, edge):
    return any(_same_edge(e, edge) for e in face.Edges)


def _wrap_uv(face, surf, u, v):
    """Shift u and v by whole periods so they sit nearest the face's range."""
    u0, u1, v0, v1 = face.ParameterRange
    try:
        if surf.isUPeriodic():
            per = surf.UPeriod
            per = per() if callable(per) else per
            u += per * round(((u0 + u1) * 0.5 - u) / per)
        if surf.isVPeriodic():
            per = surf.VPeriod
            per = per() if callable(per) else per
            v += per * round(((v0 + v1) * 0.5 - v) / per)
    except Exception:
        pass
    return u, v


def _locate(ctx, edge, tparam, point, has_edge):
    """(u, v, point on face) of the face at an edge sample."""
    face = ctx.face
    if has_edge:
        try:
            c2, first, last = face.curveOnSurface(edge)
            uv = c2.value(tparam)
            u, v = uv.x, uv.y
            p = face.valueAt(u, v)
            if (p - point).Length <= 1e-4 * max(1.0, face.BoundBox.DiagonalLength):
                return u, v, p
        except Exception:
            pass
    dist, sols, _info = Part.Vertex(point).distToShape(face)
    q = sols[0][1]
    u, v = ctx.surf.parameter(q)
    u, v = _wrap_uv(face, ctx.surf, u, v)
    return u, v, face.valueAt(u, v)


def _inward(ctx, u, v, w, h0):
    """(a, b, h): uv direction of unit w pointing into the face, and a step that fits."""
    ab = ctx.uv_direction(u, v, w)
    if ab is None:
        return None
    a, b = ab
    h = h0
    for _ in range(7):
        for sgn in (1.0, -1.0):
            if all(
                ctx.face.isPartOfDomain(u + sgn * k * h * a, v + sgn * k * h * b)
                for k in (1, 2)
            ):
                return sgn * a, sgn * b, h
        h *= 0.5
    return None


def _rel(x, y, floor):
    if _is_nan(x) or _is_nan(y):
        return _NAN
    return abs(x - y) / max(abs(x), abs(y), floor)


def _nanmax(values):
    best = 0.0
    for x in values:
        if _is_nan(x):
            return _NAN
        best = max(best, x)
    return best


def _faces_of(shape, edge, faces):
    """(ctxA, ctxB, indices) for the edge; raises ValueError per the contract."""
    if faces is not None:
        pair = list(faces)
        if len(pair) != 2:
            raise ValueError("faces must be a pair")
        ctxs, idx = [], []
        for f in pair:
            if isinstance(f, int):
                if shape is None:
                    raise ValueError("an index needs a shape")
                idx.append(f)
                f = shape.Faces[f - 1]
            else:
                idx.append(None)
            ctxs.append(_FaceCtx(f))
        return ctxs[0], ctxs[1], (None if all(i is None for i in idx) else tuple(idx))
    if shape is None or not isinstance(shape, Part.Shape):
        raise ValueError("edge is not in shape")
    ancestors = shape.ancestorsOfType(edge, Part.Face)
    if not ancestors:
        raise ValueError("edge is not in shape")
    distinct = []
    for f in ancestors:
        if not any(f.isSame(g) for g in distinct):
            distinct.append(f)
    if len(distinct) == 1:
        f = distinct[0]
        count = sum(1 for e in f.OuterWire.OrderedEdges if _same_edge(e, edge))
        for w in f.Wires:
            count = max(count, sum(1 for e in w.OrderedEdges if _same_edge(e, edge)))
        if count >= 2:
            raise ValueError("edge is a seam: one face lies on both sides")
        raise ValueError("edge has only one adjacent face")
    if len(distinct) != 2:
        raise ValueError("edge must have exactly two adjacent faces")
    all_faces = shape.Faces
    idx = []
    for f in distinct:
        idx.append(next(i + 1 for i, g in enumerate(all_faces) if g.isSame(f)))
    return _FaceCtx(distinct[0]), _FaceCtx(distinct[1]), tuple(idx)


def edge_continuity(shape, edge, samples=11, faces=None, tol=None):
    """Continuity across an edge between two faces.

    Returns a dict ``gap`` (mm), ``angle`` (degrees), ``curvature_jump``,
    ``g3_jump`` (relative, see the module docstring; maxima over the samples),
    ``level`` (``none``, ``G0``, ``G1``, ``G2`` or ``G3``) and ``faces`` (1-based
    indices into ``shape.Faces``, or ``None`` when ``faces`` was given as
    faces). ``tol`` overrides any of ``gap`` (1e-4 mm), ``angle`` (0.05 deg),
    ``curvature`` (0.01) and ``g3`` (0.05).

    Raises ``ValueError`` when the edge is not in the shape, has one adjacent
    face, has more than two, or is a seam (one face on both sides).
    """
    limits = dict(_DEFAULT_TOL)
    if tol:
        limits.update(tol)
    samples = int(samples)
    if samples < 1:
        raise ValueError("samples must be at least 1")
    ca, cb, indices = _faces_of(shape, edge, faces)
    has_a, has_b = _contains_edge(ca.face, edge), _contains_edge(cb.face, edge)
    first, last = edge.FirstParameter, edge.LastParameter
    h0 = _G3_STEP_FRACTION * max(
        ca.face.BoundBox.DiagonalLength, cb.face.BoundBox.DiagonalLength
    )

    gaps, angles, jumps, g3s = [], [], [], []
    for i in range(samples):
        tp = first + (i + 1) * (last - first) / (samples + 1)
        p = edge.valueAt(tp)
        t = edge.tangentAt(tp)
        t = t * (1.0 / t.Length)
        ua, va, pa = _locate(ca, edge, tp, p, has_a)
        ub, vb, pb = _locate(cb, edge, tp, p, has_b)
        gaps.append((pa - pb).Length)
        na, nb = ca.face.normalAt(ua, va), cb.face.normalAt(ub, vb)
        angles.append(math.degrees(math.atan2(na.cross(nb).Length, na.dot(nb))))

        # Direction of travel from face A into face B, found from inward steps.
        wa = na.cross(t)
        wb = nb.cross(t)
        step_a = _inward(ca, ua, va, wa, h0) if wa.Length > 1e-12 else None
        step_b = _inward(cb, ub, vb, wb, h0) if wb.Length > 1e-12 else None
        if step_a is not None and step_b is not None:
            su, sv = ca.face.derivative1At(ua, va)
            dir_a = su * step_a[0] + sv * step_a[1]
            dir_a = dir_a * (1.0 / dir_a.Length)
            su, sv = cb.face.derivative1At(ub, vb)
            dir_b = su * step_b[0] + sv * step_b[1]
            dir_b = dir_b * (1.0 / dir_b.Length)
            travel = dir_b - dir_a
            travel = travel * (1.0 / travel.Length) if travel.Length > 1e-12 else dir_b
        else:
            travel = wa * (1.0 / wa.Length) if wa.Length > 1e-12 else None
        if travel is None:
            jumps.append(_NAN)
            g3s.append(_NAN)
            continue
        # Make the across direction perpendicular to t (it is, up to rounding).
        travel = travel - t * travel.dot(t)
        travel = travel * (1.0 / travel.Length)
        diag = (t + travel) * (1.0 / (t + travel).Length)
        dirs = [t, travel, diag]
        ka = ca.normal_curvatures(ua, va, dirs)
        kb = cb.normal_curvatures(ub, vb, dirs)
        jumps.append(_nanmax(_rel(x, y, _CURV_FLOOR) for x, y in zip(ka, kb)))

        if step_a is None or step_b is None:
            g3s.append(_NAN)
            continue
        deriv = []
        for ctx, (u, v), (a, b, h), k0, sgn in (
            (ca, (ua, va), step_a, ka, -1.0),
            (cb, (ub, vb), step_b, kb, 1.0),
        ):
            # steps stay at least h inside; share the smaller h across both faces
            deriv.append((ctx, u, v, a, b, h, k0, sgn))
        h = min(deriv[0][5], deriv[1][5])
        sides = []
        for ctx, u, v, a, b, _h, k0, sgn in deriv[:2]:
            k1 = ctx.normal_curvatures(u + h * a, v + h * b, dirs[:2])
            k2 = ctx.normal_curvatures(u + 2 * h * a, v + 2 * h * b, dirs[:2])
            # derivative with respect to travel s: A lies at s < 0, B at s > 0
            sides.append(
                [
                    sgn * (-3.0 * k0[j] + 4.0 * k1[j] - k2[j]) / (2.0 * h)
                    for j in range(2)
                ]
            )
        g3s.append(
            _nanmax(_rel(sides[0][j], sides[1][j], _CURV_FLOOR) for j in range(2))
        )

    gap = max(gaps)
    angle = max(angles)
    jump = _nanmax(jumps)
    g3 = _nanmax(g3s)
    if gap > limits["gap"]:
        level = "none"
    else:
        level = "G0"
        if angle <= limits["angle"]:
            level = "G1"
            if not _is_nan(jump) and jump <= limits["curvature"]:
                level = "G2"
                if not _is_nan(g3) and g3 <= limits["g3"]:
                    level = "G3"
    return {
        "gap": gap,
        "angle": angle,
        "curvature_jump": jump,
        "g3_jump": g3,
        "level": level,
        "faces": indices,
    }
