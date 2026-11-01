# SPDX-License-Identifier: LGPL-2.1-or-later
"""Independent oracles for the weldment tests. Nothing here calls the feature code (WeldGeom,
WeldMember, WeldTrim, WeldCutList); it re-derives placement and plane arithmetic by hand from the
documented conventions, using only the profile tables."""

import math

import FreeCAD
import Part

import Profiles

V = FreeCAD.Vector


def unit(v):
    w = V(v)
    w.normalize()
    return w


def axes(u, rot_deg=0.0):
    """Profile x, y axes: y is global Z (global Y if vertical) made perpendicular to u; x = up cross u."""
    up = V(0, 0, 1) if abs(u.z) < 1 - 1e-9 else V(0, 1, 0)
    x = unit(up.cross(u))
    y = u.cross(x)
    a = math.radians(rot_deg)
    return x * math.cos(a) + y * math.sin(a), y * math.cos(a) - x * math.sin(a)


def anchor_xy(profile, anchor, mirror=False):
    x0, x1, y0, y1 = profile.bbox()
    if mirror:
        x0, x1 = -x1, -x0
    x = {"Left": x0, "Right": x1}
    y = {"Bottom": y0, "Top": y1}
    ax, ay = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    for w in anchor.split():
        ax = x.get(w, ax)
        ay = y.get(w, ay)
    return ax, ay


def centroid_offset(profile, u, anchor="Centre", rot_deg=0.0, mirror=False):
    """Vector from the path point to the section centroid."""
    xv, yv = axes(u, rot_deg)
    ax, ay = anchor_xy(profile, anchor, mirror)
    return xv * (-ax) + yv * (-ay)


def by_outline(family):
    """Sizes of a family ordered by outline (bounding box) area, then by name."""
    def key(size):
        x0, x1, y0, y1 = Profiles.get(family, size).bbox()
        return ((x1 - x0) * (y1 - y0), size)

    return sorted(Profiles.sizes(family), key=key)


def kind_profile(kind, frac=0.5):
    """A profile of the given kind at relative position frac (0 smallest, 1 largest outline) in the
    first family that has that kind."""
    for fam in Profiles.families():
        if Profiles.get(fam, Profiles.sizes(fam)[0]).kind == kind:
            sizes = by_outline(fam)
            return Profiles.get(fam, sizes[int(round(frac * (len(sizes) - 1)))])
    raise AssertionError("no family of kind %s" % kind)


def plane_param(c0, u, q, n):
    """Parameter s where the line c0 + s u meets the plane through q with normal n."""
    return n.dot(q - c0) / n.dot(u)


def long_point_length(profile, p0, p1, anchor, rot_deg, mirror, plane_start, plane_end):
    """Cut length by brute force: for every outline point of the placed section, the length of the
    line through it parallel to the axis between the two end planes; plane_* = (q, n), or None for a
    square end at p0 / p1. A circular section is handled through the linear length function's gradient."""
    u = unit(p1 - p0)
    xv, yv = axes(u, rot_deg)
    ax, ay = anchor_xy(profile, anchor, mirror)

    def line_length(x, y):
        if mirror:
            x = -x
        pt = p0 + xv * (x - ax) + yv * (y - ay)
        t0 = (p0 - pt).dot(u) if plane_start is None else plane_param(pt, u, *plane_start)
        t1 = (p1 - pt).dot(u) if plane_end is None else plane_param(pt, u, *plane_end)
        return t1 - t0

    # a linear function peaks at a vertex of the outline or at the extreme point of a circular arc
    g = (
        (line_length(1, 0) - line_length(-1, 0)) / 2.0,
        (line_length(0, 1) - line_length(0, -1)) / 2.0,
    )
    gn = math.hypot(*g)
    cands = []
    for e in profile.face().OuterWire.Edges:
        cands.extend((v.Point.x, v.Point.y) for v in e.Vertexes)
        if e.Curve.TypeId == "Part::GeomCircle" and gn > 0:
            c, r = e.Curve.Center, e.Curve.Radius
            px, py = c.x + r * g[0] / gn, c.y + r * g[1] / gn
            if Part.Vertex(V(px, py, 0)).distToShape(e)[0] < 1e-9:
                cands.append((px, py))
    return max(line_length(x, y) for (x, y) in cands)
