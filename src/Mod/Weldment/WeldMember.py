# SPDX-License-Identifier: LGPL-2.1-or-later
"""Structural member feature (cad-fork G07 slice 5a).

A ``Weldment::Member`` (Part::FeaturePython) sweeps a rolled or hollow section from the profile
tables (``Profiles``) along straight path segments: one solid per segment, gathered in a compound.
The path is a list of straight edges (from a sketch, a wire or any Part object, as
``App::PropertyLinkSubList``); a link without sub-elements contributes all its edges. The section
is placed with its chosen anchor point (one of nine points of its bounding box) on the path, turned
about the path by ``Rotation`` and optionally mirrored.

Frame convention: profile y (height) points along global Z projected perpendicular to the segment
(global Y for a vertical segment), profile x = y cross axis; ``Rotation`` turns x toward y.

Public sources (no software read): section geometry from the published standards through the
profile tables; prism, centroid and Pappus facts from any mechanics text. Clean room: no weldment
workbench or add-on source was read.
"""

import FreeCAD
import Part

import Profiles
from WeldGeom import ANCHORS, SegmentSpec, WeldmentError

V = FreeCAD.Vector

CLASS_NAME = "Weldment::Member"


def line_segments(path):
    """Directed straight segments (p0, p1) from a Path value: [(object, [sub names]), ...]."""
    segs = []
    for entry in path:
        obj, subs = entry[0], entry[1]
        if isinstance(subs, str):
            subs = [subs]
        shape = obj.Shape
        edges = []
        if subs and any(subs):
            for sub in subs:
                if not sub:
                    continue
                if not sub.startswith("Edge"):
                    raise WeldmentError("path link %s.%s is not an edge" % (obj.Name, sub))
                edges.append((sub, shape.getElement(sub)))
        else:
            edges = [("Edge%d" % (i + 1), e) for i, e in enumerate(shape.Edges)]
        for name, e in edges:
            try:
                elen = e.Length
            except Exception:    # a degenerate edge cannot even report its length
                elen = 0.0
            if elen < 1e-6:
                raise WeldmentError(
                    "path edge %s.%s has zero length (%.3g mm); a member needs a path segment longer than "
                    "1e-6 mm" % (obj.Name, name, elen)
                )
            p0 = e.valueAt(e.FirstParameter)
            p1 = e.valueAt(e.LastParameter)
            chord = (p1 - p0).Length
            if abs(e.Length - chord) > 1e-7 * max(1.0, chord):
                raise WeldmentError(
                    "path edge %s.%s is curved; members follow straight segments only" % (obj.Name, name)
                )
            segs.append((p0, p1))
    return segs


def get_profile(family, size):
    try:
        return Profiles.get(family, size)
    except KeyError as e:
        msg = e.args[0] if e.args else "%r %r" % (family, size)
        if "known families" not in msg:
            msg += "; families: " + ", ".join(Profiles.families())
        raise WeldmentError("unknown profile (%s)" % msg)


def segment_specs(obj):
    """SegmentSpec list for a Member object (raises WeldmentError on bad input)."""
    profile = get_profile(obj.Family, obj.Size)
    segs = line_segments(obj.Path)
    if not segs:
        raise WeldmentError("%s has an empty path; link a sketch or edges" % obj.Name)
    return [
        SegmentSpec(profile, p0, p1, obj.Anchor, obj.Rotation.getValueAs("deg").Value, obj.Mirror, i)
        for i, (p0, p1) in enumerate(segs)
    ]


class Member:
    def __init__(self, obj):
        obj.Proxy = self
        obj.addProperty("App::PropertyString", "Family", "Profile", "Profile table family, e.g. EN RHS")
        obj.addProperty("App::PropertyString", "Size", "Profile", "Size within the family")
        obj.addProperty("App::PropertyLinkSubList", "Path", "Path", "Straight edges the member follows")
        obj.addProperty("App::PropertyEnumeration", "Anchor", "Placement", "Profile point placed on the path")
        obj.Anchor = list(ANCHORS)
        obj.Anchor = "Centre"
        obj.addProperty("App::PropertyAngle", "Rotation", "Placement", "Turn about the path")
        obj.addProperty("App::PropertyBool", "Mirror", "Placement", "Mirror the profile about its height axis")
        obj.addProperty("App::PropertyString", "Material", "Profile", "Material for the cut list")
        obj.Material = "Structural steel"
        obj.addProperty("App::PropertyFloatList", "SegmentLengths", "Result", "Axis length of each segment (mm)")
        obj.setEditorMode("SegmentLengths", 1)

    def execute(self, obj):
        specs = segment_specs(obj)
        obj.SegmentLengths = [s.length for s in specs]
        obj.Shape = Part.Compound([s.prism(0.0, s.length) for s in specs])

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


def make_member(doc, family, size, path, name="Member", anchor="Centre", rotation=0.0, mirror=False):
    """Create a Member. path: a list of (object, [sub names]) or objects (all their edges)."""
    obj = doc.addObject("Part::FeaturePython", name)
    Member(obj)
    obj.Family, obj.Size = family, size
    obj.Path = [(p[0], list(p[1]) or [""]) if isinstance(p, (tuple, list)) else (p, [""]) for p in path]
    obj.Anchor = anchor
    obj.Rotation = rotation
    obj.Mirror = mirror
    if FreeCAD.GuiUp:
        obj.ViewObject.Proxy = 0
    return obj
