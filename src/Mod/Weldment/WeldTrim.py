# SPDX-License-Identifier: LGPL-2.1-or-later
"""Corner treatment and trim features (cad-fork G07 slice 5a).

A ``Weldment::EndTrim`` (Part::FeaturePython) treats one end of one member segment and passes the
other segments through. Features chain: ``Base`` is a Member or another EndTrim, so a member with
both ends treated is Member -> EndTrim -> EndTrim, and every feature stays a pure function of its
inputs (no dependency cycle between the two members of a joint: a mitre or butt refers to the
*other member's path and nominal body*, never to its trimmed result).

Kinds:

* ``Mitre``: cut on the plane that bisects the joint angle (the other member is named by
  Other, OtherSegment, OtherEnd).
* ``Butt``: extend or trim to the outer envelope of the other member's segment body.
* ``Face``: extend or trim to a planar face (Other, OtherSub = "FaceN").
* ``Body``: extend or trim to the solid of Other (the whole shape, or its solid OtherSegment).

A member whose axis cannot reach the face or body (parallel, behind the member, outside the
face, no intersection) is refused: the feature raises ``WeldmentError``, the object goes invalid
and ``getStatusString()`` carries the reason.

Public sources and clean room: see ``WeldGeom``; no weldment workbench source was read.
"""

import FreeCAD
import Part

from WeldGeom import (
    WeldmentError,
    body_cut,
    build_segment,
    mitre_cut,
    plane_cut,
)
from WeldMember import segment_specs

V = FreeCAD.Vector
KINDS = ["Mitre", "Butt", "Face", "Body"]


def is_trim(obj):
    return getattr(obj, "Proxy", None).__class__.__name__ == "EndTrim"


def root_member(obj):
    while is_trim(obj):
        if obj.Base is None:
            raise WeldmentError("%s has no Base" % obj.Name)
        obj = obj.Base
    return obj


def chain(obj):
    """EndTrim objects from the head (closest to the Member) to obj itself."""
    out = []
    while is_trim(obj):
        out.append(obj)
        obj = obj.Base
    out.reverse()
    return out


def treatments(obj):
    """{(segment, 'Start'|'End'): EndTrim} for a Member or EndTrim chain; duplicates refused."""
    found = {}
    for t in chain(obj):
        key = (t.Segment, t.End)
        if key in found:
            raise WeldmentError(
                "%s: the %s of segment %d is already treated by %s"
                % (t.Name, t.End, t.Segment, found[key].Name)
            )
        found[key] = t
    return found


def _planar(face, what):
    if face.Surface.TypeId != "Part::GeomPlane":
        raise WeldmentError("%s is not planar (%s); a planar face is needed" % (what, face.Surface.TypeId))
    u0, u1, v0, v1 = face.ParameterRange
    n = face.normalAt((u0 + u1) / 2.0, (v0 + v1) / 2.0)
    return face.CenterOfMass, n


def resolve(trim, spec):
    """PlaneCut or BodyCut for one EndTrim against the segment spec it treats."""
    which, kind = trim.End, trim.Kind
    other = trim.Other
    if kind in ("Mitre", "Butt"):
        if other is None:
            raise WeldmentError("%s: %s needs Other (the member it meets)" % (trim.Name, kind))
        other_specs = segment_specs(root_member(other))
        if not 0 <= trim.OtherSegment < len(other_specs):
            raise WeldmentError("%s: OtherSegment %d is out of range" % (trim.Name, trim.OtherSegment))
        o = other_specs[trim.OtherSegment]
        if kind == "Mitre":
            cut = mitre_cut(spec, which, o, trim.OtherEnd)
        else:
            cut = body_cut(spec, which, "butt", o.envelope())
    elif kind == "Face":
        if other is None or not trim.OtherSub.startswith("Face"):
            raise WeldmentError("%s: Face needs Other and OtherSub = 'FaceN'" % trim.Name)
        face = other.Shape.getElement(trim.OtherSub)
        q, n = _planar(face, "%s.%s" % (other.Name, trim.OtherSub))
        cut = plane_cut(spec, "End" if which == "End" else "Start", "face", q, n)
        hit = spec.centre_point(cut.s_hit)
        gap = Part.Vertex(hit).distToShape(face)[0]
        if gap > 1e-6:
            raise WeldmentError(
                "%s: the member axis meets the plane of %s.%s %.4g mm outside the face; the path does "
                "not reach it" % (trim.Name, other.Name, trim.OtherSub, gap)
            )
    elif kind == "Body":
        if other is None:
            raise WeldmentError("%s: Body needs Other" % trim.Name)
        shape = other.Shape
        if trim.OtherSegment >= 0:
            if trim.OtherSegment >= len(shape.Solids):
                raise WeldmentError("%s: OtherSegment %d is out of range" % (trim.Name, trim.OtherSegment))
            shape = shape.Solids[trim.OtherSegment]
        cut = body_cut(spec, which, "body", shape)
    else:
        raise WeldmentError("%s: unknown kind %r" % (trim.Name, kind))
    if hasattr(cut, "s_hit"):
        if which == "End" and cut.s_hit <= 1e-9:
            raise WeldmentError(
                "%s: the %s plane lies behind the start of segment %d (at %.6g mm along the axis)"
                % (trim.Name, kind, spec.index, cut.s_hit)
            )
        if which == "Start" and cut.s_hit >= spec.length - 1e-9:
            raise WeldmentError(
                "%s: the %s plane lies beyond the end of segment %d (at %.6g mm along the axis)"
                % (trim.Name, kind, spec.index, cut.s_hit)
            )
    return cut


class EndTrim:
    def __init__(self, obj):
        obj.Proxy = self
        obj.addProperty("App::PropertyLink", "Base", "Trim", "Member or EndTrim this feature treats")
        obj.addProperty("App::PropertyInteger", "Segment", "Trim", "Segment index within the member")
        obj.addProperty("App::PropertyEnumeration", "End", "Trim", "Which end of the segment")
        obj.End = ["Start", "End"]
        obj.End = "End"
        obj.addProperty("App::PropertyEnumeration", "Kind", "Trim", "Corner treatment")
        obj.Kind = KINDS
        obj.Kind = "Mitre"
        obj.addProperty("App::PropertyLink", "Other", "Trim", "The member, face owner or body trimmed to")
        obj.addProperty("App::PropertyString", "OtherSub", "Trim", "Face name on Other (Face kind)")
        obj.addProperty("App::PropertyInteger", "OtherSegment", "Trim", "Segment of Other (-1 = whole shape)")
        obj.OtherSegment = 0
        obj.addProperty("App::PropertyEnumeration", "OtherEnd", "Trim", "End of Other's segment (Mitre)")
        obj.OtherEnd = ["Start", "End"]
        obj.OtherEnd = "Start"

    def execute(self, obj):
        root = root_member(obj)
        specs = segment_specs(root)
        todo = treatments(obj)
        for (seg, _end) in todo:
            if not 0 <= seg < len(specs):
                raise WeldmentError("%s: Segment %d is out of range (0..%d)" % (obj.Name, seg, len(specs) - 1))
        solids = []
        for i, spec in enumerate(specs):
            ends = {e: (resolve(todo[(i, e)], spec) if (i, e) in todo else None) for e in ("Start", "End")}
            if ends["Start"] is None and ends["End"] is None:
                solids.append(spec.prism(0.0, spec.length))
            else:
                solids.append(build_segment(spec, ends["Start"], ends["End"]))
        obj.Shape = Part.Compound(solids)

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


def make_trim(doc, base, segment, end, kind, other=None, other_sub="", other_segment=0, other_end="Start",
              name="EndTrim"):
    obj = doc.addObject("Part::FeaturePython", name)
    EndTrim(obj)
    obj.Base = base
    obj.Segment = segment
    obj.End = end
    obj.Kind = kind
    obj.Other = other
    obj.OtherSub = other_sub
    obj.OtherSegment = other_segment
    obj.OtherEnd = other_end
    if FreeCAD.GuiUp:
        obj.ViewObject.Proxy = 0
    return obj


def make_corner(doc, a, seg_a, end_a, b, seg_b, end_b, kind="Mitre"):
    """Treat the joint of two member segments; returns (new head for a, new head for b).

    Mitre treats both members; Butt trims member a to the envelope of b (a butts to b); ButtB
    trims b to a. a and b may be Members or EndTrim chains: the new features chain onto them.
    """
    if kind == "Mitre":
        ta = make_trim(doc, a, seg_a, end_a, "Mitre", root_member(b), "", seg_b, end_b, "MitreA")
        # two ends of one chain (a is b): the second feature chains onto the first
        tb = make_trim(doc, ta if b is a else b, seg_b, end_b, "Mitre", root_member(a), "", seg_a, end_a, "MitreB")
        return ta, tb
    if kind == "Butt":
        return make_trim(doc, a, seg_a, end_a, "Butt", root_member(b), "", seg_b, end_b, "ButtA"), b
    if kind == "ButtB":
        return a, make_trim(doc, b, seg_b, end_b, "Butt", root_member(a), "", seg_a, end_a, "ButtB")
    raise WeldmentError("unknown corner kind %r; use Mitre, Butt or ButtB" % kind)
