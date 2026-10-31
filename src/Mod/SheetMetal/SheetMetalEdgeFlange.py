# SPDX-License-Identifier: LGPL-2.1-or-later
"""Edge flange: a bend and a straight wall added to a straight edge of a sheet-metal part.

cad-fork G07 slice 2. Written without reference to the add-on's GPL SheetMetalCmd.py: only the
public fabrication formulas below, OCCT/Part, and the LGPL helpers vendored beside this file.

Public references used
- Oberg, Jones, Horton, Ryffel, "Machinery's Handbook" (Industrial Press), sheet-metal chapter,
  "Bend allowance": BA = (pi / 180) * theta * (R + K * T), theta the bend angle in degrees, R the
  inside radius, T the thickness, K the neutral-axis factor (ANSI: fraction of T from the inside).
- Wikipedia, "Bending (metalworking)", sections "Bend allowance" and "Outside setback": the outside
  setback is OSSB = tan(theta / 2) * (R + T) and the inside setback tan(theta / 2) * R, the
  distances from the bend tangent line to the mould-line intersection ("virtual sharp"), with
  theta the angle the sheet is turned through (90 deg for a right-angle flange).

Geometry (2D profile in the plane perpendicular to the edge, x away from the part, z up the side
of the bend, the selected edge at the origin on the face the bend folds towards):

- inner arc: centre (0, R), radius R, from (0, 0) through the turn angle theta;
- outer arc: same centre, radius R + T, from (0, -T);
- wall direction d = (cos theta, sin theta), straight length s from the arc end, ended square;
- the flange length L is measured to the outer mould lines (s = L - (R + T) tan(theta / 2)) or to
  the inner ones (s = L - R tan(theta / 2)).

The profile is extruded along the edge and fused with the base. Bend and wall are one solid whose
bend faces are cylinders of radius R and R + T tangent to the base and the wall, which is what the
vendored unfolders detect. Developed length of the result: plate + BA + s.
"""

import math

import FreeCAD
import Part
from FreeCAD import Vector

import SheetMetalTools

EPS = 1e-6
smElementMapVersion = "sm1."


class EdgeFlangeError(Exception):
    """The flange cannot be built on the given edge with the given parameters."""


def straight_length(length, angle_deg, radius, thickness, measure="outer"):
    """Straight wall length s for a flange length measured to the outer or inner mould line."""
    t2 = math.tan(math.radians(angle_deg) / 2.0)
    setback = (radius + thickness) * t2 if measure == "outer" else radius * t2
    return length - setback


def bend_allowance(angle_deg, radius, thickness, k):
    return math.radians(angle_deg) * (radius + k * thickness)


def _profile(radius, thickness, angle_deg, s):
    """Closed wire in (x, z) points for bend plus wall; returns edges as ('L'|'A', points)."""
    th = math.radians(angle_deg)
    c = (0.0, radius)
    phi = th - math.pi / 2.0
    phm = th / 2.0 - math.pi / 2.0
    d = (math.cos(th), math.sin(th))

    def pt(r, a):
        return (c[0] + r * math.cos(a), c[1] + r * math.sin(a))

    a_i, a_o = (0.0, 0.0), (0.0, -thickness)
    b_i, b_o = pt(radius, phi), pt(radius + thickness, phi)
    m_i, m_o = pt(radius, phm), pt(radius + thickness, phm)
    e_i = (b_i[0] + s * d[0], b_i[1] + s * d[1])
    e_o = (b_o[0] + s * d[0], b_o[1] + s * d[1])
    return [("L", a_o, a_i), ("A", a_i, m_i, b_i), ("L", b_i, e_i), ("L", e_i, e_o),
            ("L", e_o, b_o), ("A", b_o, m_o, a_o)]


def make_flange_solid(origin, along, width, out, up, radius, thickness, angle_deg, s):
    """Bend plus wall solid. origin: point on the edge (start of the flange), along: unit vector
    of the edge, out: unit vector pointing away from the part, up: unit vector towards the side
    the flange folds to."""
    def p3(p):
        return origin + out * p[0] + up * p[1]

    edges = []
    for seg in _profile(radius, thickness, angle_deg, s):
        pts = [p3(p) for p in seg[1:]]
        edges.append(Part.LineSegment(*pts).toShape() if seg[0] == "L" else Part.Arc(*pts).toShape())
    face = Part.Face(Part.Wire(Part.__sortEdges__(edges)))
    return face.extrude(along * width)


def _edge_frame(base, edge, thickness_hint=None):
    """(point0, along, length, out, up_top, thickness) for a straight edge between a sheet face
    and the end face of the sheet."""
    if not isinstance(edge.Curve, Part.Line):
        raise EdgeFlangeError("the selected edge is not straight")
    faces = base.ancestorsOfType(edge, Part.Face)
    if len(faces) != 2 or any(not isinstance(f.Surface, Part.Plane) for f in faces):
        raise EdgeFlangeError("the selected edge must lie between two planar faces")
    v0, v1 = edge.Vertexes[0].Point, edge.Vertexes[-1].Point
    along = (v1 - v0)
    length = along.Length
    along.normalize()

    def extent(f):
        return max(((v.Point - v0) - along * ((v.Point - v0).dot(along))).Length for v in f.Vertexes)

    end_face, sheet_face = sorted(faces, key=extent)
    thickness = extent(end_face)
    n_end = end_face.normalAt(*end_face.Surface.parameter(v0))
    n_sheet = sheet_face.normalAt(*sheet_face.Surface.parameter(v0))
    if abs(n_end.dot(n_sheet)) > 1e-6:
        raise EdgeFlangeError("the faces at the selected edge are not perpendicular")
    return v0, along, length, n_end, n_sheet, thickness


def build_edge_flange(base_shape, edge_names, length, angle, radius, k_factor=0.4, gap=0.0,
                      flip=False, measure="outer"):
    """Pure geometry: fuse an edge flange onto each named straight edge of `base_shape`.

    angle in degrees, 0 < angle < 180 (the turn angle, 90 for a right angle). Returns the fused
    shape (one valid solid). Raises EdgeFlangeError with a readable message on bad input."""
    if not 0.0 < angle < 180.0:
        raise EdgeFlangeError("angle must be strictly between 0 and 180 degrees, got %g" % angle)
    if radius <= 0.0:
        raise EdgeFlangeError("bend radius must be greater than zero (sharp bends are not supported)")
    if measure not in ("outer", "inner"):
        raise EdgeFlangeError("measure must be 'outer' or 'inner'")
    if not edge_names:
        raise EdgeFlangeError("no edge selected")
    result = base_shape
    for name in edge_names:
        edge = base_shape.getElement(SheetMetalTools.getElementFromTNP(name))
        p0, along, w_edge, out, n_sheet, thk = _edge_frame(base_shape, edge)
        width = w_edge - 2.0 * gap
        if width <= EPS:
            raise EdgeFlangeError("gap leaves no flange width")
        s = straight_length(length, angle, radius, thk, measure)
        if s <= EPS:
            raise EdgeFlangeError("flange length %g is shorter than the bend setback; increase it"
                                  % length)
        up = n_sheet * (-1.0 if flip else 1.0)
        origin = p0 + along * gap
        if flip:  # fold towards the other face: the origin moves to the opposite edge of the end
            origin = origin - n_sheet * thk
        flange = make_flange_solid(origin, along, width, out, up, radius, thk, angle, s)
        expected = result.Volume + flange.Volume
        fused = result.fuse(flange).removeSplitter()
        if not fused.isValid() or len(fused.Solids) != 1:
            raise EdgeFlangeError("the flange does not fuse into one valid solid")
        if abs(fused.Volume - expected) > 1e-6 * expected:
            raise EdgeFlangeError("the flange collides with the part (volume overlap %g mm3)"
                                  % (expected - fused.Volume))
        result = Part.Solid(fused.Solids[0]) if fused.ShapeType != "Solid" else fused
    return result


class SMEdgeFlange:
    def __init__(self, obj, selobj, sel_elements):
        """Add an edge flange to the edge(s) `sel_elements` of `selobj`."""
        self.addVerifyProperties(obj)
        obj.addProperty("App::PropertyLinkSub", "baseObject", "Parameters",
                        "Base object and edge(s)").baseObject = (selobj, sel_elements)
        obj.Proxy = self

    def addVerifyProperties(self, obj):
        tr = FreeCAD.Qt.translate
        SheetMetalTools.smAddLengthProperty(obj, "length", tr("App::Property", "Flange length"), 10.0)
        SheetMetalTools.smAddAngleProperty(obj, "angle", tr("App::Property", "Bend angle, 0 to 180 exclusive"), 90.0)
        SheetMetalTools.smAddLengthProperty(obj, "radius", tr("App::Property", "Inside bend radius"), 1.0)
        SheetMetalTools.smAddFloatProperty(obj, "kfactor", tr("App::Property", "K-factor (ANSI)"), 0.4)
        SheetMetalTools.smAddLengthProperty(obj, "gap", tr("App::Property", "Setback from each end of the edge"), 0.0)
        SheetMetalTools.smAddBoolProperty(obj, "flip", tr("App::Property", "Fold towards the other face"), False)
        SheetMetalTools.smAddEnumProperty(obj, "measure", "Length measured to the mould line",
                                          ["outer", "inner"], "outer")
        SheetMetalTools.smAddLengthProperty(obj, "thickness", tr("App::Property", "Thickness (from the base)"), 0.0)
        obj.setEditorMode("thickness", 1)  # read-only
        SheetMetalTools.smAddLengthProperty(obj, "bendAllowance", tr("App::Property", "Bend allowance (from K-factor)"), 0.0)
        obj.setEditorMode("bendAllowance", 1)

    def getElementMapVersion(self, _fp, ver, _prop, restored):
        return smElementMapVersion + ver if not restored else None

    def execute(self, fp):
        self.addVerifyProperties(fp)
        base = fp.baseObject[0].Shape.copy()
        try:
            shape = build_edge_flange(base, fp.baseObject[1], fp.length.Value, fp.angle.Value,
                                      fp.radius.Value, fp.kfactor, fp.gap.Value, fp.flip,
                                      str(fp.measure))
        except EdgeFlangeError as exc:
            raise FreeCAD.Base.FreeCADError("Edge flange: %s" % exc)
        edge = base.getElement(SheetMetalTools.getElementFromTNP(fp.baseObject[1][0]))
        thk = _edge_frame(base, edge)[5]
        fp.thickness = thk
        fp.bendAllowance = bend_allowance(fp.angle.Value, fp.radius.Value, thk, fp.kfactor)
        fp.Shape = shape


def make_edge_flange(doc, base_obj, edge_names, name="EdgeFlange", **props):
    """Create a recomputed edge flange feature; props set the feature's properties."""
    obj = doc.addObject("Part::FeaturePython", name)
    SMEdgeFlange(obj, base_obj, list(edge_names))
    for key, value in props.items():
        setattr(obj, key, value)
    doc.recompute()
    return obj
