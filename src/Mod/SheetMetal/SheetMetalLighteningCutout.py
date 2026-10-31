# SPDX-License-Identifier: LGPL-2.1-or-later
"""Lightening cutout: a closed sketch profile cut through the sheet, alone or as a pattern.

cad-fork G07 slice 4a. Written without reference to any GPL file of the add-on (the add-on's
extruded-cutout feature was never opened): only plain OCCT (Part), the vendored LGPL helpers
beside this file, and the public fabrication guidance below.

The profile is a closed planar sketch lying in the plane of a flat sheet face. It is extruded
through the thickness T (normal to the face, into the material) and cut. A pattern repeats the
profile: "None", "Linear" (CountX copies PitchX apart along a direction in the face) or "Grid"
(CountX by CountY copies, PitchX and PitchY apart). Nothing is cut wrong silently: the profile
must be closed, not self-intersecting, in the plane of the face and wholly inside the face
(a profile that reaches a bend, the sheet edge or an existing opening is refused, not cut
across the bend), copies must not overlap, and the removed volume must come out as
(profile area) x T x (number of copies) within 1e-6 relative, or the cut is refused.

Public references: fabrication design guides for hole-to-edge and hole-to-bend distance, for
example the fabcon.com sheet-metal DFM guide: a hole or slot at least 2T from a sheared edge, and
at least 2T + R from a bend (2.5T + R for round holes and short slots and 4T + R for long slots
in stricter guides, R the inside bend radius). ``design_guide_clearance`` returns these values;
``MinEdgeDistance`` (default 0, no rule) makes the feature refuse a profile closer to the face
boundary than the number given.
"""

import FreeCAD
import Part
from FreeCAD import Vector as V

import SheetMetalTools

EPS = 1e-9
PLANE_TOL = 1e-6  # mm: how far the profile may sit off the face plane
VOL_TOL = 1e-6  # relative
smElementMapVersion = "sm1."


class CutoutError(Exception):
    """The cutout cannot be made; the message says why."""


def design_guide_clearance(thickness, radius=0.0):
    """Minimum distances from a hole or slot, from published sheet-metal design guides:
    to a sheared edge 2T; to a bend 2T + R (round hole), 2.5T + R (stricter guides), 4T + R
    (long slot)."""
    t = thickness
    return {"edge": 2.0 * t, "bend": 2.0 * t + radius, "bend_strict": 2.5 * t + radius,
            "bend_slot": 4.0 * t + radius}


def _profile_faces(profile):
    """One planar Part.Face per closed wire of `profile` (a sketch shape or any shape with
    wires), after the checks that make the profile usable."""
    wires = list(profile.Wires) if profile.ShapeType != "Wire" else [profile]
    if not wires:
        raise CutoutError("the profile has no wires: draw a closed profile in the sketch")
    faces = []
    for i, wire in enumerate(wires, start=1):
        tag = "profile wire %d" % i if len(wires) > 1 else "the profile"
        if not wire.isClosed():
            raise CutoutError("%s is open: close it (every end point must meet another)" % tag)
        try:
            face = Part.Face(wire)
        except Exception:
            raise CutoutError("%s is not planar or cannot make a face" % tag)
        if not face.isValid():
            raise CutoutError("%s intersects itself" % tag)
        if face.Area < 1e-9:
            raise CutoutError("%s encloses no area" % tag)
        faces.append(face)
    for i in range(len(faces)):
        for j in range(i + 1, len(faces)):
            if faces[i].BoundBox.intersect(faces[j].BoundBox) and \
                    faces[i].common(faces[j]).Area > 1e-9:
                raise CutoutError("profile wires %d and %d overlap or are nested: use separate "
                                  "closed outlines that do not touch" % (i + 1, j + 1))
    return faces


def _in_plane(face, sheet_face):
    p0 = sheet_face.CenterOfMass
    n = sheet_face.normalAt(0, 0)
    n.normalize()
    pn = face.normalAt(0, 0)
    if abs(abs(pn.dot(n)) - 1.0) > 1e-9:
        return False
    return all(abs((v.Point - p0).dot(n)) <= PLANE_TOL for v in face.Vertexes) and \
        abs((face.CenterOfMass - p0).dot(n)) <= PLANE_TOL


def _thickness(base, point, normal):
    """Sheet thickness T at `point` (on the face): length of the material along -normal."""
    line = Part.makeLine(point, point - normal * 1.0e4)
    inside = base.common(line)
    segs = [e for e in inside.Edges if e.distToShape(Part.Vertex(point))[0] < 1e-6]
    if not segs:
        raise CutoutError("cannot measure the sheet thickness below the profile")
    return min(segs, key=lambda e: e.distToShape(Part.Vertex(point))[0]).Length


def pattern_offsets(pattern, count_x, count_y, pitch_x, pitch_y, normal, direction=V(1, 0, 0)):
    """Translation vectors of the pattern copies in the face plane (the first is zero)."""
    if pattern not in ("None", "Linear", "Grid"):
        raise CutoutError("pattern must be None, Linear or Grid, got %r" % pattern)
    if pattern == "None":
        return [V(0, 0, 0)]
    n = V(normal)
    n.normalize()
    u = V(direction) - n * V(direction).dot(n)
    if u.Length < 1e-9:
        raise CutoutError("the pattern direction is normal to the face; give a direction in the face")
    u.normalize()
    w = n.cross(u)
    ny = count_y if pattern == "Grid" else 1
    if count_x < 1 or ny < 1:
        raise CutoutError("pattern counts must be at least 1")
    if (count_x > 1 and pitch_x <= EPS) or (ny > 1 and pitch_y <= EPS):
        raise CutoutError("pattern pitch must be greater than zero when the count is above 1")
    return [u * (i * pitch_x) + w * (j * pitch_y) for j in range(ny) for i in range(count_x)]


def build_cutout(base_shape, face_name, profile, pattern="None", count_x=1, count_y=1,
                 pitch_x=0.0, pitch_y=0.0, direction=V(1, 0, 0), min_edge_distance=0.0):
    """Cut `profile` (a shape with closed wires in the plane of the face) through the sheet from
    the planar face `face_name` of `base_shape`; returns (solid, info) with info keys: copies,
    area (one copy), thickness, removed (volume). Raises CutoutError, never cuts wrongly."""
    face = base_shape.getElement(SheetMetalTools.getElementFromTNP(face_name))
    if face.Surface.TypeId != "Part::GeomPlane":
        raise CutoutError("the cutout face must be planar (a bend or curved face cannot take a "
                          "lightening cutout)")
    normal = face.normalAt(0, 0)
    normal.normalize()
    faces = _profile_faces(profile)
    for pf in faces:
        if not _in_plane(pf, face):
            raise CutoutError("the profile is not in the plane of the selected face (it must lie "
                              "within %g mm of it and be parallel to it)" % PLANE_TOL)
    offsets = pattern_offsets(pattern, count_x, count_y, pitch_x, pitch_y, normal, direction)
    thickness = _thickness(base_shape, faces[0].CenterOfMass, normal)
    if thickness < 1e-6:
        raise CutoutError("the sheet thickness under the profile is zero")
    copies = []  # (label, face)
    for k, off in enumerate(offsets):
        for pf in faces:
            moved = pf.copy()
            moved.translate(off)
            copies.append(("copy %d" % (k + 1) if len(offsets) > 1 else "profile", moved))
    boundary = Part.Compound(list(face.Wires))
    for label, cf in copies:
        common = face.common(cf)
        if abs(common.Area - cf.Area) > 1e-6 * cf.Area:
            raise CutoutError(
                "%s reaches beyond the selected face (a bend, the sheet edge or an existing opening, "
                "%.4g of %.4g mm2 inside): cutting across a bend is not supported, move or shrink the "
                "profile so it lies inside the flat wall" % (label, common.Area, cf.Area))
        if min_edge_distance > 0:
            dist = cf.OuterWire.distToShape(boundary)[0]
            if dist < min_edge_distance - 1e-9:
                raise CutoutError("%s is %.4g mm from the face boundary (bend, edge or opening), "
                                  "below the minimum of %.4g mm" % (label, dist, min_edge_distance))
    for i in range(len(copies)):
        for j in range(i + 1, len(copies)):
            a, b = copies[i][1], copies[j][1]
            if a.BoundBox.intersect(b.BoundBox) and a.common(b).Area > 1e-9:
                raise CutoutError("%s and %s overlap: increase the pitch" % (copies[i][0], copies[j][0]))
    area = sum(cf.Area for _l, cf in copies)
    tools = [cf.extrude(-normal * thickness) for _l, cf in copies]
    cut = base_shape.cut(Part.Compound(tools))
    removed = base_shape.Volume - cut.Volume
    want = area * thickness
    if abs(removed - want) > VOL_TOL * want:
        raise CutoutError("the cut removed %.6g mm3 instead of %.6g (area x thickness): the profile "
                          "does not cut cleanly through the sheet here" % (removed, want))
    if not cut.Solids or len(cut.Solids) != 1 or not cut.isValid():
        raise CutoutError("the cut does not give one valid solid")
    return cut.Solids[0], dict(copies=len(copies), area=area, thickness=thickness, removed=removed)


class SMLighteningCutout:
    def __init__(self, obj, selobj, sel_elements, sketch):
        self.addVerifyProperties(obj)
        obj.addProperty("App::PropertyLinkSub", "baseObject", "Parameters",
                        "Base object and the sheet face").baseObject = (selobj, sel_elements)
        obj.addProperty("App::PropertyLink", "Sketch", "Parameters", "Closed profile sketch").Sketch = sketch
        obj.Proxy = self

    def addVerifyProperties(self, obj):
        tr = FreeCAD.Qt.translate
        SheetMetalTools.smAddEnumProperty(obj, "Pattern", tr("App::Property", "Pattern of the profile"),
                                          ["None", "Linear", "Grid"], "None")
        SheetMetalTools.smAddIntProperty(obj, "CountX", tr("App::Property", "Copies along the direction"), 1)
        SheetMetalTools.smAddIntProperty(obj, "CountY", tr("App::Property", "Copies across (Grid)"), 1)
        SheetMetalTools.smAddLengthProperty(obj, "PitchX", tr("App::Property", "Pitch along the direction"), 10.0)
        SheetMetalTools.smAddLengthProperty(obj, "PitchY", tr("App::Property", "Pitch across (Grid)"), 10.0)
        SheetMetalTools.smAddProperty(obj, "App::PropertyVector", "Direction",
                                      tr("App::Property", "Pattern direction (projected into the face)"),
                                      V(1, 0, 0))
        SheetMetalTools.smAddLengthProperty(obj, "MinEdgeDistance",
                                            tr("App::Property", "Minimum distance from the face boundary (0 = none)"), 0.0)
        SheetMetalTools.smAddLengthProperty(obj, "thickness", tr("App::Property", "Thickness (from the base)"), 0.0)
        obj.setEditorMode("thickness", 1)

    def getElementMapVersion(self, _fp, ver, _prop, restored):
        return smElementMapVersion + ver if not restored else None

    def execute(self, fp):
        self.addVerifyProperties(fp)
        base = fp.baseObject[0].Shape.copy()
        try:
            shape, info = build_cutout(
                base, fp.baseObject[1][0], fp.Sketch.Shape, str(fp.Pattern), fp.CountX, fp.CountY,
                fp.PitchX.Value, fp.PitchY.Value, V(fp.Direction), fp.MinEdgeDistance.Value)
        except CutoutError as exc:
            raise FreeCAD.Base.FreeCADError("Lightening cutout: %s" % exc)
        fp.thickness = info["thickness"]
        fp.Shape = shape
        SheetMetalTools.smHideObjects(fp.baseObject[0], fp.Sketch)


def make_lightening_cutout(doc, base_obj, face_name, sketch, name="LighteningCutout", **props):
    """Create a recomputed cutout feature; props set the feature's properties (Pattern, CountX,
    CountY, PitchX, PitchY, Direction, MinEdgeDistance)."""
    obj = doc.addObject("Part::FeaturePython", name)
    SMLighteningCutout(obj, base_obj, [face_name], sketch)
    for key, value in props.items():
        setattr(obj, key, value)
    doc.recompute()
    return obj
