# SPDX-License-Identifier: LGPL-2.1-or-later
"""Cut list for weldment members (cad-fork G07 slice 5a).

One row per group of identical pieces. Items are identical when family, size, material, cut
length, both end angles and both end types agree (compared to 1e-6 mm / 1e-6 degree by clustering after a deterministic sort, far below fabrication
tolerance and far above the 1e-9 geometry noise). The tilt of each end face about the axis, taken in
the profile's own frame, is part of the match: a bevel facing the web and one facing a flange are
different pieces. Two pieces match when a rotation about the axis that maps the section onto itself
(180 degrees for an I or a rectangle, 90 for a square, any for a round tube, none for an angle)
carries one's bevel azimuths onto the other's at both ends at once; reflections are never used
(the opposite hand is a different piece). A member modelled from the other end (path reversed) is the same piece:
its start is compared with the other's end, through the reflection that reversing the axis imposes on the section
plane, and only when that reflection is a symmetry of the section (an unequal angle reversed is the opposite hand).

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

import WeldBead
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
                "profile": profile,
                "circle": profile.kind == "CHS",
                "mirror": bool(root.Mirror),
                "c0": spec.c0,
                "xv": spec.xv,
                "yv": spec.yv,
                "u": spec.u,
                "solid": solid,
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


#: relative volume agreement for pieces with a curved (saddle) end: the same saddle cut from the two
#: ends differs by boolean noise of about 3e-5, a fabrication-irrelevant film
TOL_VOL = 1e-4
TOL_AZ = 1e-5   # on a unit vector: the transverse part of a nearly square end carries relative noise

_FACES = {}
_MAPS = {}

# the eight multiples of 45 degrees cover every symmetry axis of the table sections (I, rectangle,
# square, equal angle); a circle is handled from the bevel azimuths themselves
_THETAS = tuple(45.0 * k for k in range(8))


def _eff_face(profile, mirror):
    """Section face in the profile's centred frame as the member sees it (mirrored if asked)."""
    key = (profile.family, profile.size, bool(mirror))
    if key not in _FACES:
        face = profile.face()
        if mirror:
            face = face.mirror(V(0, 0, 0), V(1, 0, 0))
        _FACES[key] = face
    return _FACES[key]


def _lin(m, a):
    if a is None:
        return None
    return (m[0] * a[0] + m[1] * a[1], m[2] * a[0] + m[3] * a[1])


def _rotm(deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return (c, -s, s, c)


def _mul(m, n):
    return (m[0] * n[0] + m[1] * n[2], m[0] * n[1] + m[1] * n[3],
            m[2] * n[0] + m[3] * n[2], m[2] * n[1] + m[3] * n[3])


_FLIP = (-1.0, 0.0, 0.0, 1.0)   # the section-plane map of reversing a member: x -> -x, y -> y


def _maps_onto(fp, fq, m, key):
    """True when the linear map m (rotation or reflection) carries section face fp onto fq."""
    k = (key, tuple(round(v, 9) for v in m))
    if k not in _MAPS:
        g = fp.transformGeometry(FreeCAD.Matrix(m[0], m[1], 0, 0, m[2], m[3], 0, 0, 0, 0, 1, 0, 0, 0, 0, 1))
        _MAPS[k] = abs(g.common(fq).Area - fq.Area) <= 1e-9 * fq.Area and abs(g.Area - fq.Area) <= 1e-9 * fq.Area
    return _MAPS[k]


def _az_pair_near(pa, qa):
    for a, b in zip(pa, qa):
        if (a is None) != (b is None):
            return False
        if a is not None and math.hypot(a[0] - b[0], a[1] - b[1]) > TOL_AZ:
            return False
    return True


def _candidate_maps(p, q, reverse):
    """Section-plane linear maps N that could carry p's end-face azimuths onto q's ends.

    Forward: N is a rotation R with R(section p) = section q. Reversed (p modelled from the other
    end): the rigid motion that turns p end for end acts on the section plane as M = R * diag(-1, 1)
    (a reflection, because the axis flips) and the azimuth, which is read with the normal turned to
    point along +axis, maps by -M; so N = -M with M(section p) = section q. A circle accepts every
    rotation or reflection: N is then fixed by one bevel azimuth.
    """
    pa = (p["start_azimuth"], p["end_azimuth"])
    qa = (q["end_azimuth"], q["start_azimuth"]) if reverse else (q["start_azimuth"], q["end_azimuth"])
    if p["circle"]:
        ref = next((i for i in (0, 1) if pa[i] is not None), None)
        if ref is None or qa[ref] is None:
            if reverse:
                # no bevel azimuth to fix the reflection (curved ends): try the axes at multiples of
                # 45 degrees and let the solid comparison decide; a saddle whose symmetry plane lies
                # at another angle stays a separate row (never a wrong merge)
                return [tuple(-v for v in _mul(_rotm(th), _FLIP)) for th in _THETAS]
            return [(1.0, 0.0, 0.0, 1.0)]
        phi_p = math.atan2(pa[ref][1], pa[ref][0])
        phi_q = math.atan2(qa[ref][1], qa[ref][0])
        if not reverse:
            return [_rotm(math.degrees(phi_q - phi_p))]
        # reflection about the bisector line: angle -> phi_p + phi_q - angle
        c, s = math.cos(phi_p + phi_q), math.sin(phi_p + phi_q)
        return [(c, s, s, -c)]
    fp, fq = _eff_face(p["profile"], p["mirror"]), _eff_face(q["profile"], q["mirror"])
    key = (p["profile"].family, p["profile"].size, p["mirror"], q["mirror"], reverse)
    out = []
    for th in _THETAS:
        r = _rotm(th)
        if reverse:
            m = _mul(r, _FLIP)
            if _maps_onto(fp, fq, m, key):
                out.append(tuple(-v for v in m))
        elif _maps_onto(fp, fq, r, key):
            out.append(r)
    return out


def _az_match(p, q, reverse=False):
    """True when some symmetry of the section lines up the bevel azimuths of both ends of p with q's
    (p's start with q's end and vice versa when ``reverse``)."""
    pa = (p["start_azimuth"], p["end_azimuth"])
    qa = (q["end_azimuth"], q["start_azimuth"]) if reverse else (q["start_azimuth"], q["end_azimuth"])
    return any(_az_pair_near(tuple(_lin(n, a) for a in pa), qa) for n in _candidate_maps(p, q, reverse))


def _shape_matches(p, q, reverse):
    """Exact check for pieces with curved ends: carry p's solid by the rigid motion implied by the
    section map and the axis reversal onto q's frame and compare by common volume."""
    sp, sq = p["solid"], q["solid"]
    cp, cq = sp.CenterOfMass, sq.CenterOfMass
    fp = (p["xv"], p["yv"], p["u"])
    fq = (q["xv"], q["yv"], q["u"])
    lp = [(cp - p["c0"]).dot(v) for v in fp]
    lq = [(cq - q["c0"]).dot(v) for v in fq]
    p_cands = _candidate_maps(p, q, reverse)
    for n in p_cands:
        # forward: (a, b, s) -> (N (a, b), s + d); reversed: (a, b, s) -> (-N (a, b), d - s)
        m = n if not reverse else tuple(-v for v in n)
        ab = _lin(m, (lp[0], lp[1]))
        if math.hypot(ab[0] - lq[0], ab[1] - lq[1]) > 1e-4 * max(1.0, sp.BoundBox.DiagonalLength):
            continue
        sgn = -1.0 if reverse else 1.0
        d = lq[2] - sgn * lp[2]
        # image of p-local basis vectors in the global frame of q
        def img(a, b, s):
            x, y = _lin(m, (a, b))
            return fq[0] * x + fq[1] * y + fq[2] * (sgn * s)
        e1, e2, e3 = img(1, 0, 0), img(0, 1, 0), img(0, 0, 1)
        # global linear part L = [e1 e2 e3] * [xp yp up]^T
        cols = []
        for g in (V(1, 0, 0), V(0, 1, 0), V(0, 0, 1)):
            cols.append(e1 * g.dot(fp[0]) + e2 * g.dot(fp[1]) + e3 * g.dot(fp[2]))
        t = q["c0"] + fq[2] * d
        mat = FreeCAD.Matrix(cols[0].x, cols[1].x, cols[2].x, 0, cols[0].y, cols[1].y, cols[2].y, 0,
                             cols[0].z, cols[1].z, cols[2].z, 0, 0, 0, 0, 1)
        moved = sp.copy()
        # x_q = t + L (x_p - c0_p)
        moved.translate(p["c0"] * -1.0)
        moved.transformShape(mat)   # a proper rotation: exact, surfaces stay analytic
        moved.translate(t)
        vol = sq.Volume
        if abs(moved.common(sq).Volume - vol) <= TOL_VOL * vol and abs(moved.Volume - vol) <= TOL_VOL * vol:
            return True
    return False


def _same(p, q, reverse=False):
    """Identical pieces: same stock and treatments, and lengths, end angles, bevel azimuths about
    the axis (in the profile's own frame, up to the symmetries of the section) agree to tolerance.
    ``reverse`` compares p's start with q's end and p's end with q's start (a member modelled from
    the other end)."""
    for k in ("family", "size", "material"):
        if p[k] != q[k]:
            return False
    ks, ke = ("end", "start") if reverse else ("start", "end")
    if p["start_end"] != q[ks + "_end"] or p["end_end"] != q[ke + "_end"]:
        return False
    if not _near(p["cut_length"], q["cut_length"], TOL_LEN):
        return False
    if not _near(p["start_angle"], q[ks + "_angle"], TOL_ANG) or not _near(p["end_angle"], q[ke + "_angle"], TOL_ANG):
        return False
    if not _az_match(p, q, reverse):
        return False
    pv, qv = p["volume"], q["volume"]
    if not ((pv is None and qv is None) or (
            pv is not None and qv is not None and abs(pv - qv) <= TOL_VOL * max(pv, qv))):
        return False
    if pv is not None and reverse and not _shape_matches(p, q, reverse):
        return False
    return True


#: Plate items (gussets and end caps): columns of the second CSV section, written only when plates exist.
PLATE_COLUMNS = [
    "item",
    "quantity",
    "type",
    "thickness_mm",
    "length_mm",
    "width_mm",
    "area_mm2",
    "material",
    "density_kg_m3",
    "mass_each_kg",
    "mass_total_kg",
]
#: Weld summary section of the CSV, written only when weld beads exist (one row per leg size).
WELD_COLUMNS = ["leg_z_mm", "throat_a_mm", "bead_count", "total_length_mm", "mass_kg"]
#: tolerance for two plates being identical: thickness and outline sizes (mm) and area (relative)
TOL_PLATE = 1e-6


def is_plate(obj):
    return getattr(getattr(obj, "Proxy", None), "plate_kind", None) is not None


def plate_piece(obj):
    """One plate item from a Gusset or EndCap object (recomputed shape required)."""
    t = obj.Thickness.Value
    vol = obj.Shape.Volume
    if vol <= 0 or t <= 0:
        raise WeldmentError("%s: no plate solid; recompute first" % obj.Name)
    return {
        "mark": obj.Name,
        "type": obj.Proxy.plate_kind,
        "thickness": t,
        "length": obj.OutlineLengthMm,
        "width": obj.OutlineWidthMm,
        "area": obj.AreaMm2,
        "material": obj.Material,
        "density": obj.Density,
        "volume": vol,
    }


def _plate_sort_key(p):
    return (p["type"], p["material"], p["density"], p["thickness"], -p["length"], -p["width"], -p["area"], p["mark"])


def _plate_same(p, q):
    if p["type"] != q["type"] or p["material"] != q["material"] or p["density"] != q["density"]:
        return False
    return (
        _near(p["thickness"], q["thickness"], TOL_PLATE)
        and _near(p["length"], q["length"], TOL_PLATE)
        and _near(p["width"], q["width"], TOL_PLATE)
        and abs(p["area"] - q["area"]) <= TOL_PLATE * max(p["area"], q["area"])
    )


def plate_list(objs, first_item=1):
    """Grouped plate items (list of dicts, deterministic order) for Gusset and EndCap objects.

    Mass: plate volume x density (kg/m^3) / 1e9 (mm^3 to m^3); a Density property of 7850 is structural steel.
    """
    pieces = sorted((plate_piece(o) for o in objs if is_plate(o)), key=_plate_sort_key)
    groups = []
    for p in pieces:
        for g in groups:
            if _plate_same(g[0], p):
                g.append(p)
                break
        else:
            groups.append([p])
    rows = []
    for n, ps in enumerate(sorted(groups, key=lambda g: _plate_sort_key(g[0])), first_item):
        p = ps[0]
        each = p["volume"] * p["density"] / 1e9
        rows.append(
            {
                "item": n,
                "quantity": len(ps),
                "kind": "plate",
                "type": p["type"],
                "thickness_mm": p["thickness"],
                "length_mm": p["length"],
                "width_mm": p["width"],
                "area_mm2": p["area"],
                "material": p["material"],
                "density_kg_m3": p["density"],
                "mass_each_kg": each,
                "mass_total_kg": each * len(ps),
                "marks": sorted(q["mark"] for q in ps),
            }
        )
    return rows


def weld_list(beads, first_item=1):
    """Weld summary rows (kind "weld", ascending leg size) for WeldBead objects; weld metal at 7850 kg/m^3."""
    rows = []
    for n, w in enumerate(WeldBead.weld_summary(beads), first_item):
        rows.append({"item": n, "kind": "weld", **{k: w[k] for k in WELD_COLUMNS}})
    return rows


def cut_list(objs):
    """Grouped cut list (list of dicts, deterministic order) for Members and EndTrim chain heads.

    Gusset and EndCap objects among ``objs`` follow the member rows as plate items (rows with a
    ``type`` and ``kind == "plate"``; see ``plate_list``) and WeldBead objects as weld rows
    (``kind == "weld"``; see ``weld_list``); a list with neither is exactly the member rows.

    Pieces are sorted by value, then each joins the first group whose first member matches it to
    tolerance (a clustering, not a rounding: values straddling a rounding boundary still merge).
    """
    objs = list(objs)
    plates = [o for o in objs if is_plate(o)]
    beads = [o for o in objs if WeldBead.is_bead(o)]
    pieces = []
    for o in objs:
        if not is_plate(o) and not WeldBead.is_bead(o):
            pieces.extend(segment_pieces(o))
    pieces.sort(key=_sort_key)
    groups = []
    for p in pieces:
        for g in groups:
            if _same(g[0], p) or _same(g[0], p, reverse=True):
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
    rows = rows + plate_list(plates, len(rows) + 1)
    return rows + weld_list(beads, len(rows) + 1)


def _fmt(col, v):
    if v is None:
        return ""
    if col in ("cut_length_mm", "start_angle_deg", "end_angle_deg"):
        return "%.6f" % (v + 0.0)
    if col.startswith("mass"):
        return "%.4f" % v
    if col in ("thickness_mm", "length_mm", "width_mm"):
        return "%.6f" % v
    if col == "area_mm2":
        return "%.4f" % v
    if col in ("leg_z_mm", "throat_a_mm", "total_length_mm"):
        return "%.6f" % v
    if col == "density_kg_m3":
        return "%.1f" % v
    return str(v)


def to_csv(rows):
    """CSV text (LF line ends, fixed decimals) of cut_list rows. Plate rows, when present, follow the
    member rows as a second section: a blank line, then their own header."""
    lines = [",".join(COLUMNS)]

    def emit(cols, rs):
        for r in rs:
            cells = [_fmt(c, r[c]) for c in cols]
            cells = ['"%s"' % c if "," in c else c for c in cells]
            lines.append(",".join(cells))

    emit(COLUMNS, [r for r in rows if "type" not in r and r.get("kind") != "weld"])
    plates = [r for r in rows if "type" in r]
    welds = [r for r in rows if r.get("kind") == "weld"]
    if plates:
        lines.append("")
        lines.append(",".join(PLATE_COLUMNS))
        emit(PLATE_COLUMNS, plates)
    if welds:
        lines.append("")
        lines.append(",".join(WELD_COLUMNS))
        emit(WELD_COLUMNS, welds)
    return "\n".join(lines) + "\n"


def export_csv(objs, path):
    text = to_csv(cut_list(objs))
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return text
