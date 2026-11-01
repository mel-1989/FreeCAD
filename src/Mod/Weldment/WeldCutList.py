# SPDX-License-Identifier: LGPL-2.1-or-later
"""Cut list for weldment members (cad-fork G07 slice 5a).

One row per group of identical pieces. Items are identical when family, size, material, cut
length, both end angles and both end types agree (compared to 1e-6 mm / 1e-6 degree by clustering after a deterministic sort, far below fabrication
tolerance and far above the 1e-9 geometry noise). The tilt of each end face about the axis, taken in
the profile's own frame, is part of the match: a bevel facing the web and one facing a flange are
different pieces. Two pieces match when a rotation about the axis that maps the section onto itself
(180 degrees for an I or a rectangle, 90 for a square, any for a round tube, none for an angle)
carries one's bevel azimuths onto the other's at both ends at once; reflections are never used
(the opposite hand is a different piece).

Definitions
-----------
* **Cut length**: the length of the longest line on the member's surface parallel to its axis,
  the "long point to long point" length that the saw stock must cover (structural steel detailing
  practice, e.g. AISC *Detailing for Steel Construction*; the same figure a mitred tube is
  ordered by). For square ends it is the axial length. It is measured on the finished (trimmed)
  solid, not on the path.
* **End angle**: the angle in degrees between the end face and the plane square to the axis
  (0 = square cut, 45 = the mitre of a 90 degree joint). Ends that are not planar report ``None``
  and the cut length is the true maximum axial extent of the solid (a curved saddle end, e.g. a tube
  butted to a tube, has its long point between vertices; it is found with an exact distance query).
* **End type**: ``square``, ``mitre``, ``butt``, ``face`` or ``body``.
* **Mass**: ``mass_per_m`` of the profile table times the cut length (the table's linear mass is
  per metre of straight stock, so mass is that of the stock the piece is cut from).

Public sources and clean room: see ``WeldGeom``; no weldment workbench source was read.
"""

import math

import FreeCAD
import Part

from WeldGeom import WeldmentError
from WeldMember import get_profile, segment_specs
from WeldTrim import is_trim, root_member, treatments

V = FreeCAD.Vector

COLUMNS = [
    "item",
    "quantity",
    "family",
    "size",
    "material",
    "cut_length_mm",
    "start_angle_deg",
    "end_angle_deg",
    "start_end",
    "end_end",
    "mass_per_m_kg",
    "mass_each_kg",
    "mass_total_kg",
]


def _end_planes(solid, spec):
    """(start_plane, end_plane) as (point, unit normal, area) or None when an end is not planar."""
    ss = [(v.Point - spec.c0).dot(spec.u) for v in solid.Vertexes]
    s_mid = (min(ss) + max(ss)) / 2.0
    best = {"Start": None, "End": None}
    for f in solid.Faces:
        if f.Surface.TypeId != "Part::GeomPlane":
            continue
        u0, u1, v0, v1 = f.ParameterRange
        n = f.normalAt((u0 + u1) / 2.0, (v0 + v1) / 2.0)
        if abs(n.dot(spec.u)) < 1e-6:
            continue
        com = f.CenterOfMass
        which = "End" if (com - spec.c0).dot(spec.u) > s_mid else "Start"
        if best[which] is None or f.Area > best[which][2]:
            best[which] = (com, n, f.Area)
    return best["Start"], best["End"]


def _candidates(spec, g):
    """Transverse points (global, on the s=0 section plane) where a linear function of gradient g
    peaks: boundary vertices plus the extreme point of every circular boundary edge."""
    face = spec.section(0.0)
    pts = [v.Point for v in face.Vertexes]
    gl = g.Length
    for e in face.Edges:
        c = e.Curve
        if c.TypeId == "Part::GeomCircle" and gl > 1e-15:
            p = c.Center + g * (c.Radius / gl)
            if Part.Vertex(p).distToShape(e)[0] < 1e-7:
                pts.append(p)
        elif c.TypeId not in ("Part::GeomLine", "Part::GeomCircle"):
            pts.extend(e.discretize(Number=2000))
    return pts


def _max_along(solid, point, d):
    """max over the solid of d . (x - point), d a unit vector: the exact extreme of a linear
    functional, taken as the distance from a far plane normal to d to the solid (the nearest solid
    point to that plane is the extreme point, wherever it lies on a curved end)."""
    bb = solid.BoundBox
    big = 4.0 * bb.DiagonalLength + 4.0 * (bb.Center - point).Length + 1000.0
    plane = Part.makePlane(4.0 * big, 4.0 * big, V(0, 0, 0), d)
    plane.translate(point + d * big - plane.CenterOfMass)
    return big - plane.distToShape(solid)[0]


def _ray_extent(solid, spec, p):
    """(smin, smax) of the solid along the line through the transverse point p, or None."""
    far = spec.length * 4.0 + 4.0 * solid.BoundBox.DiagonalLength + 1000.0
    hit = solid.common(Part.makeLine(p - spec.u * far, p + spec.u * far))
    ss = [(v.Point - spec.c0).dot(spec.u) for e in hit.Edges for v in e.Vertexes]
    return (min(ss), max(ss)) if ss else None


def _sampled_length(solid, spec):
    """Longest axial line on a solid whose both ends are curved: dense samples along every
    section edge, then golden-section refinement of the best few, to about 1e-9 mm."""
    def f(e, t):
        r = _ray_extent(solid, spec, e.valueAt(t))
        return -1.0 if r is None else r[1] - r[0]
    cands = []
    for e in spec.section(0.0).Edges:
        t0, t1 = e.FirstParameter, e.LastParameter
        n = 48
        ts = [t0 + (t1 - t0) * i / n for i in range(n + 1)]
        vals = [f(e, t) for t in ts]
        for i, v in enumerate(vals):
            if (i == 0 or v >= vals[i - 1]) and (i == n or v >= vals[i + 1]):
                cands.append((v, e, ts[max(0, i - 1)], ts[min(n, i + 1)]))
    cands.sort(key=lambda c: -c[0])
    best = max((c[0] for c in cands), default=0.0)
    gr = (math.sqrt(5.0) - 1.0) / 2.0
    for v, e, lo, hi in cands[:4]:
        c, d = hi - gr * (hi - lo), lo + gr * (hi - lo)
        fc, fd = f(e, c), f(e, d)
        for _ in range(45):
            if fc > fd:
                hi, d, fd = d, c, fc
                c = hi - gr * (hi - lo)
                fc = f(e, c)
            else:
                lo, c, fc = c, d, fd
                d = lo + gr * (hi - lo)
                fd = f(e, d)
        best = max(best, fc, fd)
    return best


def _azimuth(n, spec):
    """Direction of the tilt of an end-face normal about the axis, in the profile's own frame:
    unit (x, y) of the transverse part of n, or None for a square end."""
    nx, ny = n.dot(spec.xv), n.dot(spec.yv)
    h = math.hypot(nx, ny)
    if h < 1e-8:
        return None
    return (nx / h, ny / h)


def analyse_full(solid, spec):
    """Cut length, end angles and end-face azimuths of a finished segment solid.

    Planar ends: exact (maximum of an affine function over the section outline). A curved (saddle)
    end has no plane: the length is the true maximum of the axial extent, found as the extreme of
    a linear functional over the solid (one curved end) or by dense sampling with refinement (two
    curved ends); angle and azimuth are None.
    """
    start, end = _end_planes(solid, spec)
    u = spec.u
    angles, azim = [], []
    for pl in (start, end):
        if pl is None:
            angles.append(None)
            azim.append(None)
        else:
            angles.append(math.degrees(math.acos(min(1.0, abs(pl[1].dot(u))))))
            n = pl[1] if pl[1].dot(u) >= 0 else pl[1] * -1.0
            azim.append(_azimuth(n, spec) if angles[-1] > 1e-6 else None)
    if start is not None and end is not None:
        (qs, ns, _), (qe, ne, _) = start, end
        nsu, neu = ns.dot(u), ne.dot(u)
        f0 = ((qe - spec.c0).dot(ne)) / neu - ((qs - spec.c0).dot(ns)) / nsu
        g = ns * (1.0 / nsu) - ne * (1.0 / neu)
        length = max(f0 + g.dot(p - spec.c0) for p in _candidates(spec, g))
    elif start is not None:
        qs, ns, _ = start
        if ns.dot(u) < 0:
            ns = ns * -1.0
        length = _max_along(solid, qs, ns) / ns.dot(u)
    elif end is not None:
        qe, ne, _ = end
        if ne.dot(u) < 0:
            ne = ne * -1.0
        length = _max_along(solid, qe, ne * -1.0) / ne.dot(u)
    else:
        length = _sampled_length(solid, spec)
    return length, angles[0], angles[1], azim[0], azim[1]


def analyse_segment(solid, spec):
    """(cut_length, start_angle, end_angle) of a finished segment solid."""
    r = analyse_full(solid, spec)
    return r[:3]


def segment_pieces(obj):
    """One dict per segment of a Member or EndTrim chain, ungrouped."""
    root = root_member(obj)
    specs = segment_specs(root)
    solids = obj.Shape.Solids
    if len(solids) != len(specs):
        raise WeldmentError("%s: shape has %d solids for %d segments; recompute first" % (obj.Name, len(solids), len(specs)))
    treat = treatments(obj) if is_trim(obj) else {}
    profile = get_profile(root.Family, root.Size)
    out = []
    for i, (spec, solid) in enumerate(zip(specs, solids)):
        length, a0, a1, z0, z1 = analyse_full(solid, spec)
        types = []
        for e in ("Start", "End"):
            t = treat.get((i, e))
            types.append("square" if t is None else t.Kind.lower())
        out.append(
            {
                "mark": "%s.%d" % (root.Name, i),
                "family": profile.family,
                "size": profile.size,
                "material": root.Material,
                "cut_length": length,
                "start_angle": a0,
                "end_angle": a1,
                "rotations": _rotations(profile),
                "start_azimuth": z0,
                "end_azimuth": z1,
                # curved ends carry no plane to compare, so the solid's volume stands in for their shape
                "volume": solid.Volume if (a0 is None or a1 is None) else None,
                "start_end": types[0],
                "end_end": types[1],
                "mass_per_m": profile.mass_per_m,
            }
        )
    return out


#: tolerances for treating two pieces as the same: far below any fabrication tolerance, far above
#: the 1e-9 geometry noise
TOL_LEN = 1e-6
TOL_ANG = 1e-6


def _sort_key(p):
    return (
        p["family"], p["size"], p["material"], p["start_end"], p["end_end"],
        -p["cut_length"],
        -1.0 if p["start_angle"] is None else p["start_angle"],
        -1.0 if p["end_angle"] is None else p["end_angle"],
        p["mark"],
    )


def _near(a, b, tol):
    return (a is None and b is None) or (a is not None and b is not None and abs(a - b) <= tol)


TOL_AZ = 1e-5   # on a unit vector: the transverse part of a nearly square end carries relative noise

_SYM = {}


def _rotations(profile):
    """Rotations about the axis (degrees) that map the section onto itself: [0], [0, 180],
    [0, 90, 180, 270], or None for a circle (every rotation). Mirror symmetry is not used: a
    reflected piece is the opposite hand, not the same piece."""
    key = (profile.family, profile.size)
    if key not in _SYM:
        if profile.kind == "CHS":
            _SYM[key] = None
        else:
            face = profile.face()
            found = [0.0]
            for deg in (90.0, 180.0, 270.0):
                r = face.copy()
                r.rotate(V(0, 0, 0), V(0, 0, 1), deg)
                if abs(face.common(r).Area - face.Area) <= 1e-9 * face.Area:
                    found.append(deg)
            _SYM[key] = found
    return _SYM[key]


def _rot(a, deg):
    if a is None:
        return None
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return (a[0] * c - a[1] * s, a[0] * s + a[1] * c)


def _az_pair_near(pa, qa):
    for a, b in zip(pa, qa):
        if (a is None) != (b is None):
            return False
        if a is not None and math.hypot(a[0] - b[0], a[1] - b[1]) > TOL_AZ:
            return False
    return True


def _az_match(p, q, rotations):
    """True when turning piece p about its axis by a symmetry rotation of its section lines the
    bevel azimuths of both ends up with q's."""
    pa = (p["start_azimuth"], p["end_azimuth"])
    qa = (q["start_azimuth"], q["end_azimuth"])
    if rotations is None:
        ref = next((i for i in (0, 1) if pa[i] is not None), None)
        if ref is None:
            return _az_pair_near(pa, qa)
        if qa[ref] is None:
            return False
        phi = math.degrees(math.atan2(qa[ref][1], qa[ref][0]) - math.atan2(pa[ref][1], pa[ref][0]))
        rotations = [phi]
    return any(_az_pair_near(tuple(_rot(a, deg) for a in pa), qa) for deg in rotations)


def _same(p, q):
    """Identical pieces: same stock and treatments, and lengths, end angles, bevel azimuths about
    the axis (in the profile's own frame, not folded for section symmetry) agree to tolerance."""
    for k in ("family", "size", "material", "start_end", "end_end"):
        if p[k] != q[k]:
            return False
    if not _near(p["cut_length"], q["cut_length"], TOL_LEN):
        return False
    for k in ("start_angle", "end_angle"):
        if not _near(p[k], q[k], TOL_ANG):
            return False
    if not _az_match(p, q, p["rotations"]):
        return False
    pv, qv = p["volume"], q["volume"]
    return (pv is None and qv is None) or (
        pv is not None and qv is not None and abs(pv - qv) <= 1e-9 * max(pv, qv)
    )


def cut_list(objs):
    """Grouped cut list (list of dicts, deterministic order) for Members and EndTrim chain heads.

    Pieces are sorted by value, then each joins the first group whose first member matches it to
    tolerance (a clustering, not a rounding: values straddling a rounding boundary still merge).
    """
    pieces = []
    for o in objs:
        pieces.extend(segment_pieces(o))
    pieces.sort(key=_sort_key)
    groups = []
    for p in pieces:
        for g in groups:
            if _same(g[0], p):
                g.append(p)
                break
        else:
            groups.append([p])
    ordered = sorted(
        groups,
        key=lambda g: (g[0]["family"], g[0]["size"], g[0]["material"], -g[0]["cut_length"],
                       -1.0 if g[0]["start_angle"] is None else g[0]["start_angle"],
                       -1.0 if g[0]["end_angle"] is None else g[0]["end_angle"],
                       g[0]["start_end"], g[0]["end_end"], g[0]["mark"]),
    )
    rows = []
    for n, ps in enumerate(ordered, 1):
        p = ps[0]
        each = p["mass_per_m"] * p["cut_length"] / 1000.0
        rows.append(
            {
                "item": n,
                "quantity": len(ps),
                "family": p["family"],
                "size": p["size"],
                "material": p["material"],
                "cut_length_mm": p["cut_length"],
                "start_angle_deg": p["start_angle"],
                "end_angle_deg": p["end_angle"],
                "start_end": p["start_end"],
                "end_end": p["end_end"],
                "mass_per_m_kg": p["mass_per_m"],
                "mass_each_kg": each,
                "mass_total_kg": each * len(ps),
                "marks": sorted(q["mark"] for q in ps),
            }
        )
    return rows


def _fmt(col, v):
    if v is None:
        return ""
    if col in ("cut_length_mm", "start_angle_deg", "end_angle_deg"):
        return "%.6f" % (v + 0.0)
    if col.startswith("mass"):
        return "%.4f" % v
    return str(v)


def to_csv(rows):
    """CSV text (LF line ends, fixed decimals) of cut_list rows."""
    lines = [",".join(COLUMNS)]
    for r in rows:
        cells = [_fmt(c, r[c]) for c in COLUMNS]
        cells = ['"%s"' % c if "," in c else c for c in cells]
        lines.append(",".join(cells))
    return "\n".join(lines) + "\n"


def export_csv(objs, path):
    text = to_csv(cut_list(objs))
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return text
