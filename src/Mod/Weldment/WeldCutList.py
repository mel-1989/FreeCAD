# SPDX-License-Identifier: LGPL-2.1-or-later
"""Cut list for weldment members (cad-fork G07 slice 5a).

One row per group of identical pieces. Items are identical when family, size, material, cut
length, both end angles and both end types agree (cut length and angles compared after rounding to
1e-4 mm / 1e-4 degree, far below fabrication tolerance and far above the 1e-9 geometry noise).

Definitions
-----------
* **Cut length**: the length of the longest line on the member's surface parallel to its axis,
  the "long point to long point" length that the saw stock must cover (structural steel detailing
  practice, e.g. AISC *Detailing for Steel Construction*; the same figure a mitred tube is
  ordered by). For square ends it is the axial length. It is measured on the finished (trimmed)
  solid, not on the path.
* **End angle**: the angle in degrees between the end face and the plane square to the axis
  (0 = square cut, 45 = the mitre of a 90 degree joint). Ends that are not planar report ``None``
  and the cut length falls back to the axial extent of the solid.
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


def analyse_segment(solid, spec):
    """(cut_length, start_angle, end_angle) of a finished segment solid."""
    start, end = _end_planes(solid, spec)
    u = spec.u
    angles = []
    for pl in (start, end):
        angles.append(None if pl is None else math.degrees(math.acos(min(1.0, abs(pl[1].dot(u))))))
    if start is None or end is None:
        ss = [(v.Point - spec.c0).dot(u) for v in solid.Vertexes]
        return max(ss) - min(ss), angles[0], angles[1]
    (qs, ns, _), (qe, ne, _) = start, end
    nsu, neu = ns.dot(u), ne.dot(u)
    f0 = ((qe - spec.c0).dot(ne)) / neu - ((qs - spec.c0).dot(ns)) / nsu
    g = ns * (1.0 / nsu) - ne * (1.0 / neu)
    best = max(f0 + g.dot(p - spec.c0) for p in _candidates(spec, g))
    return best, angles[0], angles[1]


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
        length, a0, a1 = analyse_segment(solid, spec)
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
                "start_end": types[0],
                "end_end": types[1],
                "mass_per_m": profile.mass_per_m,
            }
        )
    return out


def _angle_key(a):
    return -1.0 if a is None else round(a, 4)


def cut_list(objs):
    """Grouped cut list (list of dicts, deterministic order) for Members and EndTrim chain heads."""
    pieces = []
    for o in objs:
        pieces.extend(segment_pieces(o))
    groups = {}
    for p in pieces:
        key = (
            p["family"],
            p["size"],
            p["material"],
            round(p["cut_length"], 4),
            _angle_key(p["start_angle"]),
            _angle_key(p["end_angle"]),
            p["start_end"],
            p["end_end"],
        )
        g = groups.setdefault(key, {"pieces": []})
        g["pieces"].append(p)
    ordered = sorted(groups, key=lambda k: (k[0], k[1], k[2], -k[3], k[4], k[5], k[6], k[7]))
    rows = []
    for n, key in enumerate(ordered, 1):
        ps = groups[key]["pieces"]
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
        return "%.3f" % (v + 0.0)
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
