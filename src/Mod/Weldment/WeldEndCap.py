# SPDX-License-Identifier: LGPL-2.1-or-later
"""End cap feature (cad-fork G07 slice 5b).

A ``Weldment::EndCap`` (Part::FeaturePython) closes the end of one member segment that has a closed
(hollow) section: rectangular, square or circular. The end may be square-cut or mitred (any planar
end face). Two modes:

* ``Outset``: a plate of the section's *outer* outline resting on the end face, outside the member,
  of thickness t measured square to the end plane (the plate's side walls continue the member's
  walls, so it matches the outer outline exactly);
* ``Inset``: a plate of the section's *bore* outline set inside the tube, its outer face a distance
  ``Recess`` (>= 0, measured square to the end plane) behind the end face; it fits the bore and
  therefore lies within the outer outline.

``Chamfer`` clips the four corners of a rectangular outline by c along each side (a straight cut
from the point c along one side to the point c along the next). A chamfer smaller than the
section's corner radius would leave the rounded corner untouched, so it is refused (use 0 to keep
the rounded corners); a round tube has no corners and takes no chamfer. I sections and angles are
open sections and are refused: a cap needs a closed section.

Volume (analytic): outline area x t / |cos(a)|, with a the angle between the end face normal and the
axis (the plate is the cylinder or prism of the outline between two parallel planes t apart).
Outline areas: rectangle b h - (4 - pi) r^2 (rounded), b h - 2 c^2 (chamfered, c >= r), circle
pi d^2 / 4; the bore uses the inner dimensions.

Public sources (no software read): fabrication practice for capping hollow structural sections with a
plate welded to the tube end (cap plates, closure plates) as in any steel detailing text; area of a
rounded rectangle and a circle.
Clean room: no FrameForge, old Weldment add-on or other weldment workbench source was opened.
"""

import math

import FreeCAD
import Part

from WeldCutList import _end_planes
from WeldGeom import WeldmentError
from WeldGusset import DEFAULT_DENSITY, outline_size
from WeldMember import segment_specs
from WeldTrim import root_member

V = FreeCAD.Vector

CLASS_NAME = "Weldment::EndCap"
MODES = ["Outset", "Inset"]


def outline_dims(profile, mode):
    """(kind, width, height, corner_radius) of the outline the cap follows."""
    d = profile.dims
    if profile.kind == "RHS":
        if mode == "Outset":
            return "RHS", d["b"], d["h"], d["ro"]
        return "RHS", d["b"] - 2 * d["t"], d["h"] - 2 * d["t"], d["ri"]
    if profile.kind == "CHS":
        dia = d["d"] if mode == "Outset" else d["d"] - 2 * d["t"]
        return "CHS", dia, dia, 0.0
    raise WeldmentError(
        "end cap: %s %s is an open section (%s); a cap needs a closed rectangular, square or circular "
        "hollow section" % (profile.family, profile.size, "I section" if profile.kind == "I" else "angle")
    )


def outline_area(kind, w, h, r, chamfer):
    """Analytic area of the cap outline."""
    if kind == "CHS":
        return math.pi * w * w / 4.0
    if chamfer:
        return w * h - 2.0 * chamfer * chamfer
    return w * h - (4.0 - math.pi) * r * r


def check_chamfer(kind, w, h, r, chamfer):
    if chamfer < 0:
        raise WeldmentError("end cap: Chamfer must not be negative (got %.6g)" % chamfer)
    if not chamfer:
        return
    if kind == "CHS":
        raise WeldmentError("end cap: a circular cap has no corners to chamfer; set Chamfer to 0")
    if chamfer < r - 1e-12:
        raise WeldmentError(
            "end cap: the chamfer (%.6g) is smaller than the section's corner radius (%.6g), so it would "
            "leave the rounded corner; use 0 or at least %.6g" % (chamfer, r, r)
        )
    if chamfer >= min(w, h) / 2.0:
        raise WeldmentError(
            "end cap: the chamfer (%.6g) must be less than half the shorter side (%.6g)" % (chamfer, min(w, h) / 2.0)
        )


def outline_face(spec, mode, chamfer):
    """The cap outline as a planar face on the s = 0 section plane of spec."""
    lf = spec.local_face
    if mode == "Outset":
        wire = lf.OuterWire
    else:
        inner = [w for w in lf.Wires if not w.isSame(lf.OuterWire)]
        if not inner:
            raise WeldmentError("end cap: the section has no bore")
        wire = inner[0]
    if chamfer:
        bb = wire.BoundBox
        x0, x1, y0, y1, c = bb.XMin, bb.XMax, bb.YMin, bb.YMax, chamfer
        pts = [(x0 + c, y0), (x1 - c, y0), (x1, y0 + c), (x1, y1 - c), (x1 - c, y1), (x0 + c, y1), (x0, y1 - c), (x0, y0 + c)]
        wire = Part.makePolygon([V(x, y, 0) for x, y in pts] + [V(pts[0][0], pts[0][1], 0)])
    face = Part.Face(wire)
    face.Placement = spec.placement(0.0).multiply(face.Placement)
    return face


def build_cap(spec, solid, which, mode, thickness, recess, chamfer):
    """(plate solid, area, outline plane normal, reference point) for one member end."""
    if not thickness > 0:
        raise WeldmentError("end cap: Thickness must be positive (got %.6g)" % thickness)
    if recess < 0:
        raise WeldmentError("end cap: Recess must not be negative (got %.6g)" % recess)
    if mode not in MODES:
        raise WeldmentError("end cap: unknown mode %r; use Outset or Inset" % mode)
    kind, w, h, r = outline_dims(spec.profile, mode)
    check_chamfer(kind, w, h, r, chamfer)
    if mode == "Inset" and min(w, h) <= 2 * chamfer:
        raise WeldmentError("end cap: the chamfer does not fit the bore")
    planes = _end_planes(solid, spec)
    pl = planes[1] if which == "End" else planes[0]
    if pl is None:
        raise WeldmentError(
            "end cap: the %s of the member is not a planar face (a saddle or curved cut); a cap needs a "
            "square-cut or mitred end" % which
        )
    q, n, _ = pl
    sgn = 1.0 if which == "End" else -1.0
    if n.dot(spec.u) * sgn < 0:
        n = n * -1.0      # outward: away from the member
    cosa = abs(n.dot(spec.u))
    if mode == "Inset":
        depth = max((q - v.Point).dot(n) for v in solid.Vertexes)
        if recess + thickness > depth + 1e-9:
            raise WeldmentError(
                "end cap: recess %.6g + thickness %.6g = %.6g mm reaches past the other end of the member "
                "(%.6g mm deep from this end); reduce the recess or the thickness"
                % (recess, thickness, recess + thickness, depth)
            )
    s_q = (q - spec.c0).dot(n) / n.dot(spec.u)
    margin = (thickness + recess + 2.0 * spec.radius) / cosa + 1.0
    face = outline_face(spec, mode, chamfer)
    face.translate(spec.u * (s_q - margin))
    prism = face.extrude(spec.u * (2.0 * margin))
    prism = prism.Solids[0] if prism.Solids else prism
    size = 4.0 * (2.0 * margin + 2.0 * spec.radius + 1000.0)
    z0 = 0.0 if mode == "Outset" else -(recess + thickness)
    slab = Part.makeBox(size, size, thickness, V(-size / 2.0, -size / 2.0, z0), V(0, 0, 1))
    slab.Placement = FreeCAD.Placement(q, FreeCAD.Rotation(V(0, 0, 1), n))
    plate = prism.common(slab)
    pieces = plate.Solids
    if len(pieces) != 1:
        raise WeldmentError("end cap: the plate did not build as one solid (%d pieces)" % len(pieces))
    plate = pieces[0]
    if plate.Volume < 0:
        plate.reverse()
    area = outline_area(kind, w, h, r, chamfer)
    expect = area * thickness / cosa
    if abs(plate.Volume - expect) > 1e-9 * expect:
        raise WeldmentError("end cap: plate volume %.9g differs from the analytic %.9g" % (plate.Volume, expect))
    common = plate.common(solid).Volume
    if common > 1e-9 * solid.Volume:
        raise WeldmentError("end cap: the plate overlaps the member (common volume %.6g mm^3)" % common)
    ref = q if mode == "Outset" else q - n * (recess + thickness)
    return plate, area / cosa, n, ref


class EndCap:
    plate_kind = "End cap"

    def __init__(self, obj):
        obj.Proxy = self
        obj.addProperty("App::PropertyLink", "Base", "Cap", "Member or EndTrim chain head the cap closes")
        obj.addProperty("App::PropertyInteger", "Segment", "Cap", "Segment index within the member")
        obj.addProperty("App::PropertyEnumeration", "End", "Cap", "Which end of the segment")
        obj.End = ["Start", "End"]
        obj.End = "End"
        obj.addProperty("App::PropertyEnumeration", "Mode", "Cap", "Outset: on the end face; Inset: inside the bore")
        obj.Mode = MODES
        obj.Mode = "Outset"
        obj.addProperty("App::PropertyLength", "Thickness", "Cap", "Plate thickness, square to the end plane")
        obj.addProperty("App::PropertyLength", "Recess", "Cap", "Inset only: depth of the plate's outer face behind the end face")
        obj.addProperty("App::PropertyLength", "Chamfer", "Cap", "Corner clip of a rectangular outline (0 = none)")
        obj.addProperty("App::PropertyFloat", "Density", "Cut list", "Plate density, kg/m^3 (7850 = structural steel)")
        obj.Density = DEFAULT_DENSITY
        obj.addProperty("App::PropertyString", "Material", "Cut list", "Material for the cut list")
        obj.Material = "Structural steel"
        obj.addProperty("App::PropertyFloat", "AreaMm2", "Result", "Plate outline area in the end plane (mm^2)")
        obj.addProperty("App::PropertyFloat", "OutlineLengthMm", "Result", "Long side of the bounding rectangle (mm)")
        obj.addProperty("App::PropertyFloat", "OutlineWidthMm", "Result", "Short side of the bounding rectangle (mm)")
        for name in ("AreaMm2", "OutlineLengthMm", "OutlineWidthMm"):
            obj.setEditorMode(name, 1)

    def execute(self, obj):
        if obj.Base is None:
            raise WeldmentError("%s has no Base" % obj.Name)
        specs = segment_specs(root_member(obj.Base))
        if not 0 <= obj.Segment < len(specs):
            raise WeldmentError("%s: Segment %d is out of range (0..%d)" % (obj.Name, obj.Segment, len(specs) - 1))
        solids = obj.Base.Shape.Solids
        if len(solids) != len(specs):
            raise WeldmentError("%s: %s has %d solids for %d segments; recompute it first"
                                % (obj.Name, obj.Base.Name, len(solids), len(specs)))
        spec = specs[obj.Segment]
        plate, area, n, ref = build_cap(spec, solids[obj.Segment], obj.End, obj.Mode, obj.Thickness.Value,
                                        obj.Recess.Value, obj.Chamfer.Value)
        obj.AreaMm2 = area
        face = None
        for f in plate.Faces:
            if f.Surface.TypeId == "Part::GeomPlane" and abs((f.CenterOfMass - ref).dot(n)) < 1e-6:
                face = f
                break
        lng, wid = outline_size(face, n, spec.xv) if face is not None else (0.0, 0.0)
        obj.OutlineLengthMm, obj.OutlineWidthMm = lng, wid
        obj.Shape = plate

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


def make_end_cap(doc, base, segment, end, thickness, mode="Outset", recess=0.0, chamfer=0.0, name="EndCap"):
    obj = doc.addObject("Part::FeaturePython", name)
    EndCap(obj)
    obj.Base, obj.Segment, obj.End = base, segment, end
    obj.Thickness, obj.Mode, obj.Recess, obj.Chamfer = thickness, mode, recess, chamfer
    if FreeCAD.GuiUp:
        obj.ViewObject.Proxy = 0
    return obj
