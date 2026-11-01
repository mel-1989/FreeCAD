# SPDX-License-Identifier: LGPL-2.1-or-later
"""Gusset feature (cad-fork G07 slice 5b).

A ``Weldment::Gusset`` (Part::FeaturePython) is a flat corner plate between two members that meet
at a joint. The plate is a triangle, or the same triangle with its apex clipped (a chamfer), lying
in the angle between the two members' *inner* side faces:

* the joint plane is spanned by the two member axes (the segments meet at a common path point);
* every member contributes the side face of its section that is square to the joint plane and
  faces the other member (the flange outer face of an I section with its web in the plane, the
  wall of a rectangular tube, the outer face of an angle leg); both faces must lie in the plane
  of the section, so a section turned out of the plane is refused;
* K, the apex, is where the two face lines meet; leg 1 runs from K along member A's face for d1,
  leg 2 along member B's face for d2; the chamfer c clips the apex at c along each leg;
* the plate is extruded square to the joint plane by the thickness t. Placement picks where it sits
  across the plane: Centred (midplane on the members' common centre plane), Inside (flush with the
  -N face of the thinner web or wall, running inward), Face + / Face - (resting against the +N or
  -N face of the web or wall).

Because the plate lies on the faces and not in the members, it has no common volume with either
member. The reported outline size is the minimum-area bounding rectangle of the plate and the cut list
groups plates by congruence of the polygon (angles and sides, up to rotation and reflection).
Anything that would give a wrong solid is a readable ``WeldmentError``: members that do not
meet, members that do not share a plane, a section turned out of the plane, a round tube (no flat
face), a leg that runs past the member's face (checked on the real member solid, so a trimmed or
mitred end counts), a chamfer that eats a leg.

Public sources (no software read): fabricating practice for corner and knee gusset plates (plate
cut from the angle between two members, legs welded along the member faces) as in any steel
detailing text, e.g. AISC *Detailing for Steel Construction*; the area of a triangle, d1 d2 sin(a) / 2.
Clean room: no FrameForge, old Weldment add-on or other weldment workbench source was opened.
"""

import math

import FreeCAD
import Part

from WeldGeom import WeldmentError, attach_view_provider
from WeldMember import segment_specs
from WeldTrim import root_member

V = FreeCAD.Vector

CLASS_NAME = "Weldment::Gusset"
PLACEMENTS = ["Centred", "Inside", "Face +", "Face -"]
DEFAULT_DENSITY = 7850.0   # kg/m^3, structural steel

#: tolerance of the leg-on-face check (mm); the plate edges are exact, this only absorbs the distance query
_ON_FACE = 1e-6
_LEG_SAMPLES = 9


def _unit(v):
    w = V(v)
    w.normalize()
    return w


def _solve3(rows, rhs):
    """Solve a 3x3 linear system by Cramer's rule; rows are three vectors."""
    def det(a, b, c):
        return a.dot(b.cross(c))

    r0, r1, r2 = rows
    d = det(r0, r1, r2)
    if abs(d) < 1e-12:
        raise WeldmentError("the two member faces never meet in the joint plane (members nearly parallel)")
    col = [V(r0.x, r1.x, r2.x), V(r0.y, r1.y, r2.y), V(r0.z, r1.z, r2.z)]
    b = V(*rhs)
    return V(det(b, col[1], col[2]) / d, det(col[0], b, col[2]) / d, det(col[0], col[1], b) / d)


def joint_frame(sa, end_a, sb, end_b):
    """(J, ua, ub, N): joint point, unit axis directions pointing away from it, plane normal."""
    pa = sa.p0 if end_a == "Start" else sa.p1
    pb = sb.p0 if end_b == "Start" else sb.p1
    ua = sa.u if end_a == "Start" else sa.u * -1.0
    ub = sb.u if end_b == "Start" else sb.u * -1.0
    cr = ua.cross(ub)
    if cr.Length < 0.04:
        raise WeldmentError(
            "gusset: the two members are within 2.3 degrees of parallel (or antiparallel) at the joint; "
            "a corner plate needs a real angle between them"
        )
    n = _unit(cr)
    gap = (pa - pb).Length
    if gap > 1e-6:
        off = abs((pa - pb).dot(n))
        why = (
            "the member axes are skew, so the members do not share a plane (%.4g mm apart normal to it)" % off
            if off > 1e-6
            else "the chosen ends are %.4g mm apart" % gap
        )
        raise WeldmentError("gusset: the members do not meet at the joint: " + why)
    return pa, ua, ub, n


def member_side(spec, n, toward, label):
    """The side face of a member used by the gusset.

    Returns (w, const, n_centre, wall): the face lies on the plane w . X = const (w in the joint
    plane, square to the axis, pointing out of the member through that face), n_centre is the N
    coordinate of the section's centre plane, wall the web or wall thickness.
    """
    kind = spec.profile.kind
    if kind == "CHS":
        raise WeldmentError(
            "gusset: %s is a circular hollow section, which has no flat side face for a plate to lie on" % label
        )
    bb = spec.local_face.BoundBox
    if abs(spec.xv.dot(n)) < 1e-7:
        w, nax, wlo, whi, nlo, nhi = spec.xv, spec.yv, bb.XMin, bb.XMax, bb.YMin, bb.YMax
    elif abs(spec.yv.dot(n)) < 1e-7:
        w, nax, wlo, whi, nlo, nhi = spec.yv, spec.xv, bb.YMin, bb.YMax, bb.XMin, bb.XMax
    else:
        raise WeldmentError(
            "gusset: the section of %s is turned out of the joint plane (neither of its axes lies in it); "
            "set Rotation so one section axis lies in the plane of the two members" % label
        )
    s = w.dot(toward)
    const = w.dot(spec.p0) + (whi if s > 0 else wlo)
    n_centre = n.dot(spec.p0) + nax.dot(n) * (nlo + nhi) / 2.0
    d = spec.profile.dims
    wall = d["tw"] if kind == "I" else d["t"]
    return (w if s > 0 else w * -1.0), const if s > 0 else -const, n_centre, wall


def plate_polygon(sa, end_a, sb, end_b, d1, d2, chamfer, thickness, placement, label_a="member A", label_b="member B"):
    """The plate: dict with the 3D outline points, N, lower N level and the analytic area."""
    for name, v in (("Leg1", d1), ("Leg2", d2), ("Thickness", thickness)):
        if not v > 0:
            raise WeldmentError("gusset: %s must be positive (got %.6g)" % (name, v))
    if chamfer < 0:
        raise WeldmentError("gusset: Chamfer must not be negative (got %.6g)" % chamfer)
    if chamfer and chamfer >= min(d1, d2):
        raise WeldmentError(
            "gusset: the chamfer (%.6g) must be shorter than both legs (%.6g, %.6g)" % (chamfer, d1, d2)
        )
    if placement not in PLACEMENTS:
        raise WeldmentError("gusset: unknown placement %r; use one of %s" % (placement, ", ".join(PLACEMENTS)))
    j, ua, ub, n = joint_frame(sa, end_a, sb, end_b)
    wa, ca, na, ta = member_side(sa, n, ub, label_a)
    wb, cb, nb, tb = member_side(sb, n, ua, label_b)
    if abs(na - nb) > 1e-6:
        raise WeldmentError(
            "gusset: %s and %s do not share a plane: their section centre planes are %.4g mm apart "
            "(check the anchors and rotations)" % (label_a, label_b, abs(na - nb))
        )
    k = _solve3([wa, wb, n], [ca, cb, n.dot(j)])
    wall = min(ta, tb)
    n_lo = {
        "Centred": na - thickness / 2.0,
        "Inside": na - wall / 2.0,
        "Face +": na + wall / 2.0,
        "Face -": na - wall / 2.0 - thickness,
    }[placement]
    k = k + n * (n_lo - n.dot(k))
    if chamfer:
        pts = [k + ua * chamfer, k + ua * d1, k + ub * d2, k + ub * chamfer]
    else:
        pts = [k, k + ua * d1, k + ub * d2]
    sin_t = ua.cross(ub).Length
    area = 0.5 * d1 * d2 * sin_t - 0.5 * chamfer * chamfer * sin_t
    return {"points": pts, "N": n, "n_lo": n_lo, "area": area, "ua": ua, "ub": ub, "K": k, "thickness": thickness}


def _check_leg(solid, a, b, thickness, n, label, which):
    """Refuse when the leg a-b (and its copy across the thickness) leaves the member's surface."""
    for lvl in (0.0, thickness / 2.0, thickness):
        for i in range(_LEG_SAMPLES):
            p = a + (b - a) * (i / float(_LEG_SAMPLES - 1)) + n * lvl
            gap = Part.Vertex(p).distToShape(solid)[0]
            if gap > _ON_FACE:
                raise WeldmentError(
                    "gusset: %s runs past the face of %s: the point %.1f mm along the leg (%.4g, %.4g, %.4g) is "
                    "%.4g mm off the member; shorten the leg, make the plate thinner or place it on the face"
                    % (which, label, (p - a - n * lvl).Length, p.x, p.y, p.z, gap)
                )


def outline_size(shape, n, e1):
    """(long, short) side of the bounding rectangle of the planar face of ``shape`` square to n,
    measured in the frame (e1 projected square to n, n x e1)."""
    f1 = e1 - n * e1.dot(n)
    f1.normalize()
    f2 = n.cross(f1)
    m = FreeCAD.Matrix(f1.x, f2.x, n.x, 0, f1.y, f2.y, n.y, 0, f1.z, f2.z, n.z, 0, 0, 0, 0, 1)
    pl = FreeCAD.Placement(m)
    flat = shape.copy()
    flat.transformShape(pl.inverse().toMatrix())
    try:
        bb = flat.optimalBoundingBox(True, False)
    except Exception:
        bb = flat.BoundBox
    a, b = bb.XLength, bb.YLength
    return max(a, b), min(a, b)


def min_area_rect(points, n, e1):
    """(long, short) side of the minimum-area bounding rectangle of the polygon ``points`` (3D, in a
    plane square to n); one side of that rectangle lies along a polygon side, so each side direction
    is tried. Equal areas (any triangle) are broken by the smaller long side. Independent of the
    plate's orientation and of the order of the corners, so congruent plates report the same size."""
    f1 = e1 - n * e1.dot(n)
    f1.normalize()
    f2 = n.cross(f1)
    xy = [(p.dot(f1), p.dot(f2)) for p in points]
    best = None
    for i in range(len(xy)):
        ax, ay = xy[(i + 1) % len(xy)][0] - xy[i][0], xy[(i + 1) % len(xy)][1] - xy[i][1]
        h = math.hypot(ax, ay)
        ax, ay = ax / h, ay / h
        us = [x * ax + y * ay for x, y in xy]
        vs = [-x * ay + y * ax for x, y in xy]
        w, d = max(us) - min(us), max(vs) - min(vs)
        # minimum area; sides of a triangle give equal areas, so a tie goes to the smaller long side
        # (what makes the answer independent of the order the corners are listed in)
        area, lng = w * d, max(w, d)
        if best is None or area < best[0] * (1.0 - 1e-9) or (area <= best[0] * (1.0 + 1e-9) and lng < best[1] - 1e-9):
            best = (area, lng, min(w, d))
    return best[1], best[2]


def polygon_signature(points):
    """Flat list [interior angle (rad), side length to the next vertex, ...] of a convex polygon, in
    the order of ``points``: the data that decides whether two plates are congruent."""
    n = len(points)
    out = []
    for i in range(n):
        a, b, c = points[i - 1], points[i], points[(i + 1) % n]
        u, v = a - b, c - b
        out.append(math.atan2(u.cross(v).Length, u.dot(v)))
        out.append((c - b).Length)
    return out


def congruent(sa, sb, tol_len=1e-6, tol_ang=1e-7):
    """True when two polygon signatures describe congruent polygons: the cyclic sequences of
    (angle, side) agree up to rotation of the start and reversal of the direction (a reflection)."""
    if len(sa) != len(sb):
        return False
    n = len(sa) // 2
    a = [(sa[2 * i], sa[2 * i + 1]) for i in range(n)]
    b = [(sb[2 * i], sb[2 * i + 1]) for i in range(n)]
    # reversed reading: the angle at vertex i followed by the side that precedes it
    rb = [(b[i][0], b[i - 1][1]) for i in range(n)][::-1]
    for seq in (b, rb):
        for r in range(n):
            if all(abs(a[i][0] - seq[(i + r) % n][0]) <= tol_ang and abs(a[i][1] - seq[(i + r) % n][1]) <= tol_len
                   for i in range(n)):
                return True
    return False


class Gusset:
    plate_kind = "Gusset"

    def __init__(self, obj):
        obj.Proxy = self
        obj.addProperty("App::PropertyLink", "MemberA", "Joint", "First member (Member or EndTrim chain head)")
        obj.addProperty("App::PropertyInteger", "SegmentA", "Joint", "Segment index of the first member")
        obj.addProperty("App::PropertyEnumeration", "EndA", "Joint", "Joint end of that segment")
        obj.EndA = ["Start", "End"]
        obj.EndA = "Start"
        obj.addProperty("App::PropertyLink", "MemberB", "Joint", "Second member")
        obj.addProperty("App::PropertyInteger", "SegmentB", "Joint", "Segment index of the second member")
        obj.addProperty("App::PropertyEnumeration", "EndB", "Joint", "Joint end of that segment")
        obj.EndB = ["Start", "End"]
        obj.EndB = "Start"
        obj.addProperty("App::PropertyLength", "Leg1", "Plate", "Leg along member A's face (mm)")
        obj.addProperty("App::PropertyLength", "Leg2", "Plate", "Leg along member B's face (mm)")
        obj.addProperty("App::PropertyLength", "Chamfer", "Plate", "Apex clip along each leg (0 = sharp triangle)")
        obj.addProperty("App::PropertyLength", "Thickness", "Plate", "Plate thickness")
        obj.addProperty("App::PropertyEnumeration", "PlatePlacement", "Plate", "Where the plate sits across the joint plane")
        obj.PlatePlacement = PLACEMENTS
        obj.PlatePlacement = "Centred"
        obj.addProperty("App::PropertyFloat", "Density", "Cut list", "Plate density, kg/m^3 (7850 = structural steel)")
        obj.Density = DEFAULT_DENSITY
        obj.addProperty("App::PropertyString", "Material", "Cut list", "Material for the cut list")
        obj.Material = "Structural steel"
        obj.addProperty("App::PropertyFloat", "AreaMm2", "Result", "Plate outline area (mm^2)")
        obj.addProperty("App::PropertyFloat", "OutlineLengthMm", "Result", "Long side of the bounding rectangle (mm)")
        obj.addProperty("App::PropertyFloat", "OutlineWidthMm", "Result", "Short side of the bounding rectangle (mm)")
        obj.addProperty("App::PropertyFloatList", "Signature", "Result", "Angle and side of each plate corner (cut-list congruence)")
        for name in ("AreaMm2", "OutlineLengthMm", "OutlineWidthMm", "Signature"):
            obj.setEditorMode(name, 1)

    def _end(self, obj, which):
        member = getattr(obj, "Member" + which)
        seg = getattr(obj, "Segment" + which)
        if member is None:
            raise WeldmentError("%s: Member%s is not set" % (obj.Name, which))
        specs = segment_specs(root_member(member))
        if not 0 <= seg < len(specs):
            raise WeldmentError("%s: Segment%s %d is out of range (0..%d)" % (obj.Name, which, seg, len(specs) - 1))
        solids = member.Shape.Solids
        if len(solids) != len(specs):
            raise WeldmentError("%s: %s has %d solids for %d segments; recompute it first"
                                % (obj.Name, member.Name, len(solids), len(specs)))
        return member, specs[seg], solids[seg]

    def execute(self, obj):
        ma, sa, sola = self._end(obj, "A")
        mb, sb, solb = self._end(obj, "B")
        if sola.isSame(solb) and obj.EndA == obj.EndB:
            raise WeldmentError("%s: both ends name the same member end" % obj.Name)
        d1, d2 = obj.Leg1.Value, obj.Leg2.Value
        t = obj.Thickness.Value
        data = plate_polygon(sa, obj.EndA, sb, obj.EndB, d1, d2, obj.Chamfer.Value, t, obj.PlatePlacement,
                             ma.Name, mb.Name)
        pts, n = data["points"], data["N"]
        # legs lie on the members' faces from the first clipped point to the leg end
        if obj.Chamfer.Value:
            la, lb = (pts[0], pts[1]), (pts[3], pts[2])
        else:
            la, lb = (pts[0], pts[1]), (pts[0], pts[2])
        _check_leg(sola, la[0], la[1], t, n, ma.Name, "leg 1")
        _check_leg(solb, lb[0], lb[1], t, n, mb.Name, "leg 2")
        face = Part.Face(Part.makePolygon(pts + [pts[0]]))
        plate = face.extrude(n * t)
        solid = plate.Solids[0] if plate.Solids else plate
        if solid.Volume < 0:
            solid.reverse()
        expect = data["area"] * t
        if abs(solid.Volume - expect) > 1e-9 * expect:
            raise WeldmentError("%s: plate volume %.9g differs from area x thickness %.9g" % (obj.Name, solid.Volume, expect))
        for label, member_solid in ((ma.Name, sola), (mb.Name, solb)):
            common = solid.common(member_solid).Volume
            if common > 1e-9 * member_solid.Volume:
                raise WeldmentError("%s: the plate overlaps %s (common volume %.6g mm^3)" % (obj.Name, label, common))
        obj.AreaMm2 = data["area"]
        obj.OutlineLengthMm, obj.OutlineWidthMm = min_area_rect(pts, n, data["ua"])
        obj.Signature = polygon_signature(pts)
        obj.Shape = solid

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


def make_gusset(doc, member_a, seg_a, end_a, member_b, seg_b, end_b, leg1, leg2, thickness,
                chamfer=0.0, placement="Centred", name="Gusset"):
    """Create a Gusset at the joint of member_a's end_a and member_b's end_b."""
    obj = doc.addObject("Part::FeaturePython", name)
    Gusset(obj)
    obj.MemberA, obj.SegmentA, obj.EndA = member_a, seg_a, end_a
    obj.MemberB, obj.SegmentB, obj.EndB = member_b, seg_b, end_b
    obj.Leg1, obj.Leg2, obj.Thickness, obj.Chamfer = leg1, leg2, thickness, chamfer
    obj.PlatePlacement = placement
    attach_view_provider(obj)
    return obj
