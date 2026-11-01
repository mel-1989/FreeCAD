# SPDX-License-Identifier: LGPL-2.1-or-later
"""Weldment geometry kernel: segment frames, prisms, end cuts (cad-fork G07 slice 5a).

Pure geometry, no document objects. Public sources for the behaviour, none of them software:

* A prismatic member is a profile (cross-section) swept along a straight axis; the section is
  centred on its area centroid, so the volume of a prism cut by a plane equals the section area
  times the axial length measured on the centroid line (Pappus / Cavalieri, any mechanics text).
* A mitre joint of two members is cut on the plane that bisects the angle between them: the plane
  whose normal is the difference of the two unit directions pointing away from the joint (plane
  geometry; carpentry and structural fabrication practice).
* A butt joint trims one member to the outer surface of the other (the outer envelope of a
  hollow section, since a fabricator closes a tube end against the wall, not the bore).
* Cut length is the length of the longest line on the member parallel to its axis ("long point to
  long point" measure of structural steel detailing practice, e.g. AISC *Detailing for Steel
  Construction*).

Clean room: no weldment workbench, add-on or CAD vendor documentation was read or copied.
"""

import math

import FreeCAD
import Part

V = FreeCAD.Vector

ANCHORS = (
    "Centre",
    "Top",
    "Bottom",
    "Left",
    "Right",
    "Top Left",
    "Top Right",
    "Bottom Left",
    "Bottom Right",
)

#: A refusal: a trim, extend or build that cannot be done. The message says why.
class WeldmentError(ValueError):
    pass


def anchor_point(bbox, anchor):
    """Local (x, y) of the named anchor on bbox = (xmin, xmax, ymin, ymax)."""
    if anchor not in ANCHORS:
        raise WeldmentError("unknown anchor %r; use one of %s" % (anchor, ", ".join(ANCHORS)))
    xmin, xmax, ymin, ymax = bbox
    xs = {"Left": xmin, "Right": xmax}
    ys = {"Bottom": ymin, "Top": ymax}
    x = (xmin + xmax) / 2.0
    y = (ymin + ymax) / 2.0
    for word in anchor.split():
        if word in xs:
            x = xs[word]
        if word in ys:
            y = ys[word]
    return x, y


def frame_axes(u, rotation_deg):
    """Profile axes (x along width, y along height) for a member of unit axis u.

    The height axis is the global Z projected perpendicular to u (global Y for a vertical
    member); x = y cross u, so x, y, u is right-handed. Rotation turns x toward y about u.
    """
    up = V(0, 0, 1)
    if abs(u.dot(up)) > 1.0 - 1e-9:
        up = V(0, 1, 0)
    yv = up - u * up.dot(u)
    yv.normalize()
    xv = yv.cross(u)
    xv.normalize()
    a = math.radians(rotation_deg)
    c, s = math.cos(a), math.sin(a)
    return xv * c + yv * s, yv * c - xv * s


class SegmentSpec:
    """One straight member segment: profile, axis, frame and the nominal prism."""

    def __init__(self, profile, p0, p1, anchor="Centre", rotation=0.0, mirror=False, index=0):
        self.profile = profile
        self.p0 = V(p0)
        self.p1 = V(p1)
        self.index = index
        axis = self.p1 - self.p0
        self.length = axis.Length
        if self.length < 1e-6:
            raise WeldmentError("segment %d has zero length" % index)
        self.u = V(axis)
        self.u.normalize()
        self.xv, self.yv = frame_axes(self.u, rotation)
        self.mirror = bool(mirror)
        face = profile.face()
        bbox = profile.bbox()
        if self.mirror:
            face = face.mirror(V(0, 0, 0), V(1, 0, 0))
            bbox = (-bbox[1], -bbox[0], bbox[2], bbox[3])
        ax, ay = anchor_point(bbox, anchor)
        face = face.copy()
        face.translate(V(-ax, -ay, 0))
        self.local_face = face
        #: vector from the path point to the section centroid
        self.offset = self.xv * (-ax) + self.yv * (-ay)
        self.c0 = self.p0 + self.offset
        b = (bbox[0] - ax, bbox[1] - ax, bbox[2] - ay, bbox[3] - ay)
        self.radius = max(math.hypot(x, y) for x in b[:2] for y in b[2:])

    # ----- geometry -----------------------------------------------------------------------
    def placement(self, s):
        m = FreeCAD.Matrix(
            self.xv.x, self.yv.x, self.u.x, self.p0.x + s * self.u.x,
            self.xv.y, self.yv.y, self.u.y, self.p0.y + s * self.u.y,
            self.xv.z, self.yv.z, self.u.z, self.p0.z + s * self.u.z,
            0, 0, 0, 1,
        )
        return FreeCAD.Placement(m)

    def section(self, s, outer_only=False):
        face = self.local_face
        if outer_only:
            face = Part.Face(face.OuterWire)
        face = face.copy()
        face.Placement = self.placement(s).multiply(face.Placement)
        return face

    def prism(self, s0, s1, outer_only=False):
        sol = self.section(s0, outer_only).extrude(self.u * (s1 - s0))
        solid = sol.Solids[0] if sol.Solids else sol
        if solid.Volume < 0:
            solid.reverse()
        return solid

    def envelope(self):
        """Nominal outer envelope (bore filled): what a butting member is trimmed to."""
        return self.prism(0.0, self.length, outer_only=True)

    def centre_point(self, s):
        return self.c0 + self.u * s


# ----- end cuts ----------------------------------------------------------------------------
class PlaneCut:
    """Keep the side of the plane (point q) that n_keep points into."""

    def __init__(self, kind, q, n_keep, s_hit, info=""):
        self.kind, self.q, self.n_keep, self.s_hit, self.info = kind, V(q), V(n_keep), s_hit, info
        self.n_keep.normalize()


class BodyCut:
    """Trim to the surface of a solid; the member is extended to it first."""

    def __init__(self, kind, solid, s_enter, s_exit, info=""):
        self.kind, self.solid, self.s_enter, self.s_exit, self.info = kind, solid, s_enter, s_exit, info


def plane_cut(spec, which, kind, q, n_keep, info=""):
    """Resolve a planar cut at the Start or End of spec. n_keep may be given in either sense."""
    n = V(n_keep)
    if n.Length < 1e-12:
        raise WeldmentError("%s: the cutting plane has no normal" % kind)
    n.normalize()
    un = spec.u.dot(n)
    if abs(un) < 0.02:
        raise WeldmentError(
            "%s at the %s of segment %d: the member axis is within 1.2 degrees of the cutting plane, "
            "so it never reaches it" % (kind, which, spec.index)
        )
    want = -1.0 if which == "End" else 1.0
    if un * want < 0:
        n = n * -1.0
        un = -un
    s_hit = (V(q) - spec.c0).dot(n) / un
    return PlaneCut(kind, q, n, s_hit, info)


def _half_space(cut, size):
    box = Part.makeBox(size, size, size, V(-size / 2, -size / 2, 0), V(0, 0, 1))
    box.Placement = FreeCAD.Placement(cut.q, FreeCAD.Rotation(V(0, 0, 1), cut.n_keep * -1.0))
    return box


def _target(kind):
    return "the other member" if kind == "butt" else "the target body"


#: how far inside the outline the seating probes sit (relative to the section radius)
_INSET = 1e-6


def outline_points(spec, per_edge=40):
    """Points on the outer outline of the section at s = 0, pulled 1e-6 of the section radius
    towards the centroid so a section flush with its seat is not refused on rounding noise."""
    pts = []
    for e in spec.section(0.0, outer_only=True).OuterWire.Edges:
        pts.extend(e.discretize(Number=per_edge))
    return [spec.c0 + (p - spec.c0) * (1.0 - _INSET) for p in pts]


def check_seated(spec, which, body, what):
    """Refuse when the section overhangs ``body``: some point of its outline, followed along the
    axis, never meets the body (the rest of the section would hang beside it)."""
    far = spec.length + 4.0 * (body.BoundBox.DiagonalLength + spec.length) + 1000.0
    sgn = 1.0 if which == "End" else -1.0
    s0 = 0.0 if which == "End" else spec.length
    for p in outline_points(spec):
        a = p + spec.u * s0
        b = a + spec.u * (sgn * far)
        if not body.common(Part.makeLine(a, b)).Edges:
            raise WeldmentError(
                "%s at the %s of segment %d: the section is not fully seated on %s; its outline overhangs "
                "the other member (point %.4g, %.4g, %.4g has nothing to meet). Choose a mitre or "
                "trim to a face of the member that spans the whole section"
                % (what, which, spec.index, "the other member" if what == "butt" else "the body",
                   p.x, p.y, p.z)
            )


def check_on_face(spec, which, face, q, n, what):
    """Refuse when the section, projected along the axis onto the plane of ``face``, leaves it."""
    s0 = 0.0 if which == "End" else spec.length
    un = spec.u.dot(n)
    for p in outline_points(spec):
        a = p + spec.u * s0
        hit = a + spec.u * ((q - a).dot(n) / un)
        gap = Part.Vertex(hit).distToShape(face)[0]
        if gap > 1e-6:
            raise WeldmentError(
                "%s at the %s of segment %d: the section does not lie within the face (its outline "
                "projects %.4g mm outside it), so the member would overhang; use a face at least as "
                "large as the section" % (what, which, spec.index, gap)
            )


def build_segment(spec, start=None, end=None):
    """The segment solid with an optional PlaneCut/BodyCut at each end (None keeps it square)."""
    L = spec.length
    s_a, s_b = 0.0, L
    for which, cut in (("Start", start), ("End", end)):
        if cut is None:
            continue
        if isinstance(cut, PlaneCut):
            ext = spec.radius / abs(spec.u.dot(cut.n_keep)) + 1.0
            if which == "End":
                s_b = max(L, cut.s_hit) + ext
            else:
                s_a = min(0.0, cut.s_hit) - ext
        else:
            reach = min(8.0 * spec.radius, abs(cut.s_exit - cut.s_enter))
            if which == "End":
                s_b = max(L, cut.s_enter + reach)
            else:
                s_a = min(0.0, cut.s_enter - reach)
    if start is not None and end is not None:
        if isinstance(start, PlaneCut) and isinstance(end, PlaneCut) and end.s_hit - start.s_hit <= 1e-9:
            raise WeldmentError(
                "segment %d: the two end cuts cross each other (start at %.6g, end at %.6g along the axis)"
                % (spec.index, start.s_hit, end.s_hit)
            )
    solid = spec.prism(s_a, s_b)
    cutters = []
    size = 4.0 * (abs(s_b - s_a) + 2 * spec.radius + 1000.0)
    for cut in (start, end):
        if cut is None:
            continue
        cutters.append(_half_space(cut, size) if isinstance(cut, PlaneCut) else cut.solid)
    for c in cutters:
        solid = solid.cut(c)
    pieces = solid.Solids
    if not pieces:
        raise WeldmentError("segment %d: the end cuts remove the whole member" % spec.index)
    if len(pieces) > 1:
        # keep the piece holding the nominal midpoint (the rest lies beyond a body that was passed through)
        mid = Part.Vertex(spec.centre_point(spec.length / 2.0))
        pieces.sort(key=lambda p: mid.distToShape(p)[0])
        solid = pieces[0]
    else:
        solid = pieces[0]
    if not solid.isValid() or solid.Volume <= 0:
        raise WeldmentError("segment %d: the trimmed solid is not valid" % spec.index)
    for cut in (start, end):
        if isinstance(cut, BodyCut):
            common = solid.common(cut.solid).Volume
            if common > 1e-9 * solid.Volume:
                raise WeldmentError("segment %d: trimmed member still overlaps %s" % (spec.index, _target(cut.kind)))
            if solid.distToShape(cut.solid)[0] > 1e-6:
                raise WeldmentError(
                    "segment %d: the trimmed member does not reach %s (gap %.4g mm)"
                    % (spec.index, _target(cut.kind), solid.distToShape(cut.solid)[0])
                )
    return solid


def body_cut(spec, which, kind, body, info=""):
    """Resolve a trim to a solid: where the centroid line enters and leaves it."""
    far = spec.length + 4.0 * (body.BoundBox.DiagonalLength + spec.length) + 1000.0
    if which == "End":
        a = spec.centre_point(0.0)
        b = spec.centre_point(far)
    else:
        a = spec.centre_point(spec.length)
        b = spec.centre_point(spec.length - far)
    line = Part.makeLine(a, b)
    hit = body.common(line)
    if not hit.Edges:
        raise WeldmentError(
            "%s at the %s of segment %d: the member axis never reaches the body; nothing to trim or "
            "extend to" % (kind, which, spec.index)
        )
    ss = []
    for e in hit.Edges:
        for v in e.Vertexes:
            ss.append((v.Point - spec.c0).dot(spec.u))
    s_enter, s_exit = min(ss), max(ss)
    if which == "Start":
        # looking back along -u: entering is the larger s
        s_enter, s_exit = max(ss), min(ss)
    return BodyCut(kind, body, s_enter, s_exit, info)


def mitre_cut(spec, which, other, other_which):
    """Mitre cut of spec's end against the end of another segment meeting at the same point."""
    p = spec.p0 if which == "Start" else spec.p1
    po = other.p0 if other_which == "Start" else other.p1
    if (p - po).Length > 1e-6:
        raise WeldmentError(
            "mitre at the %s of segment %d: the other member's %s is %.6g mm away, so the paths do not meet"
            % (which, spec.index, other_which, (p - po).Length)
        )
    ua = spec.u * (1.0 if which == "Start" else -1.0)
    ub = other.u * (1.0 if other_which == "Start" else -1.0)
    n = ua - ub
    if n.Length < 0.04:
        raise WeldmentError(
            "mitre at the %s of segment %d: the two members fold back on each other (joint angle under "
            "2.3 degrees)" % (which, spec.index)
        )
    return plane_cut(spec, which, "mitre", p, n)


def joint_angle(ua, ub):
    """Angle between the two members at a joint, from unit vectors pointing away from it."""
    return math.acos(max(-1.0, min(1.0, ua.dot(ub))))
