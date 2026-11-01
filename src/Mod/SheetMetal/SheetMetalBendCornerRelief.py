# SPDX-License-Identifier: LGPL-2.1-or-later
"""Bend corner relief: a round, square or obround cut at the corner where two bends meet.

cad-fork G07 slice 4a. Written without reference to the add-on's GPL files (the implementer never
opened SheetMetalCornerReliefCmd.py or any other GPL-headed file): only the public fabrication
knowledge below, OCCT/Part, and the LGPL helpers vendored beside this file (SheetMetalBendSolid
wraps the flat cutter around the bend cylinders).

Public references used
- Oberg, Jones, Horton, Ryffel, "Machinery's Handbook" (Industrial Press), sheet-metal chapter,
  "Bend allowance": BA = (pi / 180) * theta * (R + K * T), the developed length of a bend, which is
  where the flat pattern puts the bend zone.
- Sheet-metal design guides from fabricators (relief practice): a corner relief at the meeting of
  two bends is a round, square or obround cut, at least one thickness wide, centred on the corner
  so that it reaches past the bend tangent lines into both flanges and the plate.

What the parts look like at the corner (measured on an open tray, a plate with two edge flanges on
adjacent edges): the two flanges do not overlap and leave no gap. Each is a strip of the plate edge
(bend, then wall); they touch along one straight edge of length T, the corner edge, where the side
face of one flange meets the side face of the other at the bend tangent lines. The unfolded part
has the plate and the two strips meeting at one point, with the quarter of the plane beyond both
bend lines empty.

Definition. The relief is a shape S drawn on the flat pattern, centred on the corner point C (the
corner edge, in the flat). Flat axes: xi runs along the first flange's outward direction, eta along
the second's, so the plate is xi <= 0, eta <= 0, the first flange xi >= 0, eta <= 0 and the second
flange eta >= 0, xi <= 0. The cut removes S from the plate, from the first flange's bend zone
(0 <= xi <= BA) and wall, and from the second flange's bend zone and wall. In the bent part the
cutter is the flat shape mapped onto the part: planar on the plate, wrapped on the bend cylinders
with developed length BA = theta (R + K T) (the neutral radius R + K T maps the flat to the
cylinder, radial cut walls), planar again on the walls. Consequences, checked by the tests:

- unfolded with the same K-factor, the outline of the relief in the flat pattern is exactly S;
- the removed volume is T * (area of S on the plate and walls + area of S in each bend zone *
  (R + T/2) / (R + K T)); the factor is 1 only for K = 0.5, as the bend is thicker on the outside.

Shapes: ``Round`` (size = diameter), ``Square`` (size = side) and ``Obround`` (size = width, length
= ratio * size), each turned by ``rotation`` degrees in the flat (0: sides along xi and eta; an
obround's long axis lies along xi). The size is absolute (mm) or a multiple of the thickness.
The K-factor must equal the one used for the flat pattern: it decides how far into the bends the
flat shape reaches. Bends must have equal thickness and fold to the same side; their radii and
angles may differ.
"""

import math

import FreeCAD
import Part
from FreeCAD import Vector

import SheetMetalTools

EPS = 1e-6
smElementMapVersion = "sm1."
RELIEF_TYPES = ["Round", "Square", "Obround"]
SIZE_MODES = ["Absolute", "Thickness"]
BIG = 1.0e4
WRAP_POINTS = 240  # samples per curved edge when wrapping it onto a bend cylinder


class ReliefError(Exception):
    """The relief cannot be made at the selected corner with the given parameters."""


def effective_size(size, size_mode, thickness):
    return size * thickness if size_mode == "Thickness" else size


def make_shape_2d(relief_type, size, ratio=2.0, rotation=0.0):
    """The relief shape as a planar face in z = 0 of flat coordinates (xi, eta), centred on the
    origin. size is the diameter, side or obround width in mm."""
    if relief_type == "Round":
        wire = Part.Wire(Part.makeCircle(size / 2.0, Vector(0, 0, 0), Vector(0, 0, 1)))
        face = Part.Face(wire)
    elif relief_type == "Square":
        h = size / 2.0
        face = Part.Face(Part.makePolygon([Vector(-h, -h, 0), Vector(h, -h, 0), Vector(h, h, 0),
                                           Vector(-h, h, 0), Vector(-h, -h, 0)]))
    elif relief_type == "Obround":
        half = (ratio * size - size) / 2.0
        w = size / 2.0
        edges = [Part.LineSegment(Vector(-half, -w, 0), Vector(half, -w, 0)).toShape(),
                 Part.Arc(Vector(half, -w, 0), Vector(half + w, 0, 0), Vector(half, w, 0)).toShape(),
                 Part.LineSegment(Vector(half, w, 0), Vector(-half, w, 0)).toShape(),
                 Part.Arc(Vector(-half, w, 0), Vector(-half - w, 0, 0), Vector(-half, -w, 0)).toShape()]
        face = Part.Face(Part.Wire(edges))
    else:
        raise ReliefError("relief type must be one of %s, got %r" % (RELIEF_TYPES, relief_type))
    if rotation:
        face.rotate(Vector(0, 0, 0), Vector(0, 0, 1), rotation)
    return face


def _check_finite(**values):
    for key, v in values.items():
        if not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ReliefError("%s must be a finite number, got %r" % (key, v))


def corner_frame(base, edge):
    """Read the corner at a straight edge where two bends meet.

    Returns a dict: origin (corner point on the inner plane), up, thickness, strips: two dicts with
    out (flat direction away from the plate), radius, angle (radians). Raises ReliefError."""
    if not isinstance(edge.Curve, Part.Line):
        raise ReliefError("the selected edge is not straight")
    faces = base.ancestorsOfType(edge, Part.Face)
    if len(faces) != 2 or any(not isinstance(f.Surface, Part.Plane) for f in faces):
        raise ReliefError("the selected edge is not a corner edge: it must lie between the side "
                          "faces of two bends")
    v0, v1 = edge.Vertexes[0].Point, edge.Vertexes[-1].Point
    n = v1 - v0
    thickness = n.Length
    n.normalize()
    mid = (v0 + v1) * 0.5
    info = []
    for f in faces:
        m = f.normalAt(*f.Surface.parameter(mid))
        if abs(m.dot(n)) > 1e-6:
            raise ReliefError("the selected edge is not a corner edge of two bends (a side face is "
                              "not perpendicular to it)")
        arcs = [e for e in f.Edges if isinstance(e.Curve, Part.Circle)]
        if len(arcs) < 2:
            raise ReliefError("the selected edge is not a corner of two bends: a side face has no "
                              "bend arcs")
        arcs.sort(key=lambda e: e.Curve.Radius)
        inner, outer = arcs[0], arcs[-1]
        radius = inner.Curve.Radius
        arc_gap = outer.Curve.Radius - radius
        if abs(arc_gap - thickness) > 1e-6 * max(1.0, thickness):
            if arc_gap > 1e-9 and abs(edge.Length - arc_gap) > 1e-6 * max(1.0, arc_gap):
                # bends beyond 90 degrees also have a longer corner edge where the side faces meet
                raise ReliefError("the selected edge is %g mm long but the sheet is %g mm thick: "
                                  "pick the corner edge that is one thickness long (bends beyond "
                                  "90 degrees have a longer edge there as well, which is not "
                                  "supported)" % (edge.Length, arc_gap))
            raise ReliefError("the bend arcs are not one thickness apart")
        info.append(dict(normal=m, inner=inner, radius=radius, center=inner.Curve.Center,
                         angle=abs(inner.LastParameter - inner.FirstParameter)))
    m1, m2 = info[0]["normal"], info[1]["normal"]
    if abs(m1.dot(m2)) > 1e-6:
        raise ReliefError("the two bends do not meet at a right angle in plan; only rectangular "
                          "corners are supported")
    s = [(i["center"] - mid).dot(n) for i in info]
    if s[0] * s[1] <= 0.0:
        raise ReliefError("the two bends fold to opposite sides of the sheet")
    up = n if s[0] > 0.0 else n * -1.0
    origin = mid + up * (thickness / 2.0)
    strips = []
    for i, other in ((0, 1), (1, 0)):
        out = info[other]["normal"]  # strip i bends away from the plate along the other side face's normal
        far = max((v.Point for v in info[i]["inner"].Vertexes), key=lambda p: (p - origin).dot(out))
        if (far - origin).dot(out) <= 1e-9:
            raise ReliefError("the selected corner is open the wrong way: the bends do not leave "
                              "the plate away from the corner")
        # the strip body lies on the side opposite to the side face normal
        strips.append(dict(out=out, radius=info[i]["radius"], angle=info[i]["angle"]))
    return dict(origin=origin, up=up, thickness=thickness, strips=strips)


def _planar(face2d, origin, out_a, out_b, up, shift):
    """Map a face in flat coordinates (a, b) to 3D: the inner plane shifted by `shift` along up.
    The frame may be left-handed; the face normal is then flipped, which callers fix."""
    mat = FreeCAD.Matrix(out_a.x, out_b.x, up.x, origin.x + up.x * shift,
                         out_a.y, out_b.y, up.y, origin.y + up.y * shift,
                         out_a.z, out_b.z, up.z, origin.z + up.z * shift, 0, 0, 0, 1)
    shape = face2d.copy()
    shape.transformShape(mat, True)
    return shape


def _faces_of(shape):
    return [f for f in shape.Faces] if shape is not None and not shape.isNull() else []


def _region(shape2d, a0, a1, b0, b1):
    """shape2d clipped to the box a0 <= a <= a1, b0 <= b <= b1 (flat coordinates)."""
    box = Part.Face(Part.makePolygon([Vector(a0, b0, 0), Vector(a1, b0, 0), Vector(a1, b1, 0),
                                      Vector(a0, b1, 0), Vector(a0, b0, 0)]))
    return _faces_of(shape2d.common(box))


def _prism_from_face(face3d, direction, depth):
    return face3d.extrude(direction * depth)


def wrap_point(a, b, center, out_a, out_b, up, r_wrap, rho):
    """Flat point (a, b) on the plane r_wrap from the bend axis, wrapped onto the cylinder: the
    developed coordinate a runs along the neutral radius rho, so the turn is a / rho."""
    phi = a / rho
    return center + (up * (-math.cos(phi)) + out_a * math.sin(phi)) * r_wrap + out_b * b


def _wrapped_edges(face2d, center, out_a, out_b, up, r_wrap, rho):
    """The outer wire of the flat face wrapped onto the cylinder of radius r_wrap. Curves other
    than lines along the axis or around it are sampled at the same parameters at every radius, so
    that ruled surfaces between two radii are exactly radial."""
    def w(p):
        return wrap_point(p.x, p.y, center, out_a, out_b, up, r_wrap, rho)

    edges = []
    for e in face2d.OuterWire.Edges:
        p0, p1 = e.Vertexes[0].Point, e.Vertexes[-1].Point
        if isinstance(e.Curve, Part.Line):
            if abs(p1.x - p0.x) < 1e-9:  # a generator line of the cylinder
                edges.append(Part.makeLine(w(p0), w(p1)))
                continue
            if abs(p1.y - p0.y) < 1e-9:  # a circular arc about the axis
                edges.append(Part.Arc(w(p0), w((p0 + p1) * 0.5), w(p1)).toShape())
                continue
        pts = [w(p) for p in e.discretize(Number=WRAP_POINTS)]
        curve = Part.BSplineCurve()
        curve.interpolate(Points=pts, Parameters=[float(i) for i in range(len(pts))])
        edges.append(curve.toShape())
    return Part.Wire(Part.__sortEdges__(edges))


def bend_cutter(face2d, center, out_a, out_b, up, r_in, rho, depth):
    """Wrap the flat face (coordinates (a, b)) onto the cylinders of radius r_in and r_in + depth
    about the axis through `center` along out_b and join them with radial walls: the solid of
    all points whose flat image (turn * rho, along the axis) lies in the face."""
    r_out = r_in + depth
    wires = [_wrapped_edges(face2d, center, out_a, out_b, up, r, rho) for r in (r_in, r_out)]
    faces = []
    for wire, r in zip(wires, (r_in, r_out)):
        cyl = Part.makeCylinder(r, 100.0, center - out_b * 50.0, out_b, 360)
        cap = Part.Face(cyl.Face1.Surface, wire)
        cap.validate()
        faces.append(cap)
    walls = []
    for e_in in wires[0].Edges:
        mid = e_in.valueAt((e_in.FirstParameter + e_in.LastParameter) / 2.0)
        # the matching outer edge: the one whose midpoint lies on the radial line through mid
        radial = mid - (center + out_b * (mid - center).dot(out_b))
        radial.normalize()
        target = mid + radial * depth
        e_out = min(wires[1].Edges, key=lambda e: e.distToShape(Part.Vertex(target))[0])
        walls.append(Part.makeRuledSurface(e_in, e_out))
    shell = Part.Shell(faces + walls)
    shell.sewShape()
    solid = Part.Solid(shell)
    solid.fix(1e-7, 1e-7, 1e-7)
    if solid.Volume < 0.0:
        solid.reverse()
    return solid


def strip_cutters(shape2d, origin, out_a, out_b, up, radius, angle, thickness, k, delta):
    """Cutters for one flange. Flat coordinates (a, b): a along the flange's outward direction,
    b along the other flange's; the flange lies at b <= 0. Returns solids."""
    ba = angle * (radius + k * thickness)
    rho = radius + k * thickness
    cutters = []
    # bend zone, wrapped
    center = origin + up * radius
    for f in _region(shape2d, 0.0, ba, -BIG, 0.0):
        cutters.append(bend_cutter(f, center, out_a, out_b, up, radius - delta, rho,
                                   thickness + 2.0 * delta))
    # wall, planar
    th = angle
    d = out_a * math.cos(th) + up * math.sin(th)
    n_in = out_a * (-math.sin(th)) + up * math.cos(th)
    start = origin + out_a * (radius * math.sin(th)) + up * (radius * (1.0 - math.cos(th))) \
        + n_in * delta
    for f in _region(shape2d, ba, BIG, -BIG, 0.0):
        face = Part.Face(f.OuterWire)
        shifted = face.copy()
        shifted.translate(Vector(-ba, 0, 0))
        mapped = _planar(shifted, Vector(0, 0, 0), d, out_b, n_in, 0.0)
        mapped.translate(start)
        if mapped.normalAt(0, 0).dot(n_in) < 0.0:
            mapped.reverse()
        cutters.append(mapped.extrude(n_in * (-(thickness + 2.0 * delta))))
    return cutters


def bend_zone_cut(shape2d, radius, angle, thickness, k):
    """(area, lost) of `shape2d` (flat coordinates) in the bend strip of one flange: its area
    inside the strip, mm2, and the length of bend line the unfolder no longer records, mm. The
    unfolder records every separate piece of bend face that is left as a bend line along the
    piece's own middle (halfway across its extent in the bend direction), so `lost` is the
    length of the strip's side from the plate edge outwards minus the lengths of those lines."""
    ba = angle * (radius + k * thickness)
    area = sum(f.Area for f in _region(shape2d, 0.0, ba, -BIG, 0.0))
    box_b = -(shape2d.BoundBox.DiagonalLength + 1.0)
    strip = Part.Face(Part.makePolygon([Vector(0, box_b, 0), Vector(ba, box_b, 0), Vector(ba, 0, 0),
                                        Vector(0, 0, 0), Vector(0, box_b, 0)]))
    recorded = 0.0
    for piece in strip.cut(shape2d).Faces:
        mid = 0.5 * (piece.BoundBox.XMin + piece.BoundBox.XMax)
        line = Part.makeLine(Vector(mid, box_b - 1.0, 0), Vector(mid, 1.0, 0))
        recorded += sum(e.Length for e in line.common(piece).Edges)
    return area, -box_b - recorded


def flat_volume_offset(cuts, radii, angles, thickness, k):
    """Change a relief makes to the part's flat-minus-part volume beyond what
    ``SheetMetalFlatPattern.expected_bend_volume_change`` already gives, mm3. `cuts` are the
    (area, lost) pairs of `bend_zone_cut` for the two bends, `radii` and `angles` (radians)
    those bends' inside radii and angles. In a bend zone, flat minus part is T*A*(1 - x) over
    the material that is left, with x = (R + T/2)/(R + K*T) (the flat area wraps onto the
    mid-thickness cylinder), where A is the area of left material. The bends-only formula
    counts BA * (full width - lost) as that area, so the missing term is
    T*(1 - x)*(BA*lost - cut area) per bend."""
    total = 0.0
    for (area, lost), r, th in zip(cuts, radii, angles):
        rho = r + k * thickness
        total += thickness * (1.0 - (r + 0.5 * thickness) / rho) * (th * rho * lost - area)
    return total


def build_corner_relief(base_shape, edge_name, relief_type="Round", size=3.0, size_mode="Absolute",
                        k_factor=0.4, ratio=2.0, rotation=0.0, info=None):
    """Pure geometry: see ``_build_corner_relief``. A failure inside the geometry kernel (a relief far larger than
    the part leaves it nothing to cut) is re-raised as a ReliefError that says so."""
    try:
        return _build_corner_relief(base_shape, edge_name, relief_type, size, size_mode, k_factor, ratio, rotation, info)
    except ReliefError:
        raise
    except (FreeCAD.Base.FreeCADError, RuntimeError, ValueError) as exc:
        raise ReliefError("the relief of size %g could not be cut: it is probably larger than the part or its "
                          "bend (%s)" % (size, exc)) from exc


def _build_corner_relief(base_shape, edge_name, relief_type, size, size_mode, k_factor, ratio, rotation, info):
    """Pure geometry: cut the relief at the corner edge `edge_name` of `base_shape`. Returns one
    valid solid; raises ReliefError with a readable message on bad input. When `info` is a dict
    it receives "flat_volume_offset" (see `flat_volume_offset`) and "radii" (the two bends'
    inside radii)."""
    _check_finite(size=size, k_factor=k_factor, ratio=ratio, rotation=rotation)
    if relief_type not in RELIEF_TYPES:
        raise ReliefError("relief type must be one of %s, got %r" % (RELIEF_TYPES, relief_type))
    if size_mode not in SIZE_MODES:
        raise ReliefError("size mode must be one of %s, got %r" % (SIZE_MODES, size_mode))
    if size <= 0.0:
        raise ReliefError("relief size must be greater than zero, got %g" % size)
    if relief_type == "Obround" and ratio <= 1.0:
        raise ReliefError("obround length ratio must be greater than 1, got %g" % ratio)
    if not 0.0 <= k_factor <= 1.0:
        raise ReliefError("K-factor must be between 0 and 1, got %g" % k_factor)
    edge = base_shape.getElement(SheetMetalTools.getElementFromTNP(edge_name))
    fr = corner_frame(base_shape, edge)
    thk = fr["thickness"]
    s_mm = effective_size(size, size_mode, thk)
    if s_mm < thk - EPS:
        raise ReliefError("relief size %g is smaller than the thickness %g (design guides ask for "
                          "at least one thickness)" % (s_mm, thk))
    st1, st2 = fr["strips"]
    delta = 0.25 * min(thk, st1["radius"], st2["radius"])
    shape2d = make_shape_2d(relief_type, s_mm, ratio, rotation)
    up, origin = fr["up"], fr["origin"]
    out1, out2 = st1["out"], st2["out"]
    cutters = []
    # plate: xi <= 0, eta <= 0
    for f in _region(shape2d, -BIG, 0.0, -BIG, 0.0):
        face3 = _planar(Part.Face(f.OuterWire), origin, out1, out2, up, delta)
        if face3.normalAt(0, 0).dot(up) < 0.0:
            face3.reverse()
        cutters.append(face3.extrude(up * (-(thk + 2.0 * delta))))
    cutters += strip_cutters(shape2d, origin, out1, out2, up, st1["radius"], st1["angle"], thk,
                             k_factor, delta)
    mirrored = shape2d.mirror(Vector(0, 0, 0), Vector(1, -1, 0))
    if info is not None:
        cuts = [bend_zone_cut(shape2d, st1["radius"], st1["angle"], thk, k_factor),
                bend_zone_cut(mirrored, st2["radius"], st2["angle"], thk, k_factor)]
        radii = [st1["radius"], st2["radius"]]
        info["radii"] = radii
        info["flat_volume_offset"] = flat_volume_offset(
            cuts, radii, [st1["angle"], st2["angle"]], thk, k_factor)
    cutters += strip_cutters(mirrored, origin, out2, out1, up, st2["radius"], st2["angle"], thk,
                             k_factor, delta)
    if not cutters:
        raise ReliefError("the relief does not touch the part")
    result = base_shape
    for c in cutters:
        if not c.isValid():
            raise ReliefError("the relief cutter is not valid (the size is probably too large for the "
                              "bend)")
        result = result.cut(c)
    result = result.removeSplitter()
    if not result.isValid() or len(result.Solids) != 1:
        raise ReliefError("the relief leaves more than one solid or an invalid one (is it larger than "
                          "the corner?)")
    if result.Volume >= base_shape.Volume - 1e-9:
        raise ReliefError("the relief removes no material")
    return result.Solids[0] if result.ShapeType != "Solid" else result


def _base_of(obj):
    """The feature below `obj` in its base chain, or None."""
    for name in ("baseObject", "BaseObject", "Base"):
        if name in getattr(obj, "PropertiesList", []):
            value = getattr(obj, name)
            if isinstance(value, (tuple, list)):
                value = value[0] if value else None
            if value is not None and hasattr(value, "PropertiesList"):
                return value
    return None


def part_kfactor(obj):
    """The K-factor (ANSI) of the nearest feature at or below `obj` that has one (an edge
    flange, a jog, a hem), or None when the chain has none."""
    seen = set()
    while obj is not None and id(obj) not in seen:
        seen.add(id(obj))
        if "kfactor" in obj.PropertiesList and "kfromPart" not in obj.PropertiesList:
            return float(obj.kfactor)
        obj = _base_of(obj)
    return None


def relief_volume_offset(unfold_feature):
    """Sum of `flatVolumeOffset` over the corner reliefs in the unfold's base chain, mm3."""
    total, seen = 0.0, set()
    obj = _base_of(unfold_feature)
    while obj is not None and id(obj) not in seen:
        seen.add(id(obj))
        if "flatVolumeOffset" in obj.PropertiesList:
            total += float(obj.flatVolumeOffset)
        obj = _base_of(obj)
    return total


def _k_message(obj, k_relief, k_flat):
    return ("%s was cut with K-factor %g but the flat pattern uses %g: the relief and the "
            "unfolded part differ by up to %.2f mm per bend; set the relief to take K from "
            "the part or change the unfold's K-factor"
            % (obj.Label, k_relief, k_flat,
               abs(k_relief - k_flat) * 3.1416 / 2.0 * float(obj.thickness)))


# A bend table's K, recorded on the flat's bends, is back-computed from its 6-decimal allowance and
# differs from the listed K by up to about 3e-7; a K difference of 1e-6 moves a relief outline by
# about theta * T * 1e-6 (under 1e-5 mm for T up to 3 mm), far inside the 1e-3 mm flat-pattern tolerance.
K_TOLERANCE = 1e-6


def relief_k_mismatch(unfold_feature, bends=None):
    """A readable message when a corner relief under an Unfold feature was built with a
    K-factor other than the one the flat pattern uses (more than K_TOLERANCE apart), else None.
    Walks the unfold's base chain. With `bends` (the FlatBend records of the flat) each relief's
    `kUsed` is compared with the K recorded on the bends whose inside radius is one of the
    relief's bend radii (within 1e-6), which covers manual, DIN and table unfolds alike; without
    them the Unfold's own K-factor is used and a table unfold is not checked."""
    if bends is None:
        if "KFactor" not in unfold_feature.PropertiesList:
            return None
        if str(getattr(unfold_feature, "MaterialSheet", "_manual")) != "_manual":
            return None
        k_unfold = float(unfold_feature.KFactor)
        if str(getattr(unfold_feature, "KFactorStandard", "ansi")) == "din":
            k_unfold /= 2.0
    seen = set()
    obj = _base_of(unfold_feature)
    while obj is not None and id(obj) not in seen:
        seen.add(id(obj))
        if "kUsed" in obj.PropertiesList:
            k_relief = float(obj.kUsed)
            if bends is None:
                if abs(k_relief - k_unfold) > K_TOLERANCE:
                    return _k_message(obj, k_relief, k_unfold)
            else:
                radii = list(getattr(obj, "bendRadii", []))
                for b in bends:
                    if any(abs(b.inside_radius - r) < 1e-6 for r in radii) \
                            and abs(b.k_factor - k_relief) > K_TOLERANCE:
                        return _k_message(obj, k_relief, b.k_factor)
        obj = _base_of(obj)
    return None


class SMBendCornerRelief:
    def __init__(self, obj, selobj, sel_elements):
        """Cut a relief at the corner edge `sel_elements` of `selobj`."""
        self.addVerifyProperties(obj)
        obj.addProperty("App::PropertyLinkSub", "baseObject", "Parameters",
                        "Base object and corner edge").baseObject = (selobj, sel_elements)
        obj.Proxy = self

    def addVerifyProperties(self, obj):
        tr = FreeCAD.Qt.translate
        SheetMetalTools.smAddEnumProperty(obj, "reliefType", "Relief shape", RELIEF_TYPES, "Round")
        SheetMetalTools.smAddFloatProperty(obj, "size", tr("App::Property", "Diameter, side or obround width"), 3.0)
        SheetMetalTools.smAddEnumProperty(obj, "sizeMode", "Size is in mm or a multiple of T", SIZE_MODES, "Absolute")
        SheetMetalTools.smAddFloatProperty(obj, "ratio", tr("App::Property", "Obround length / width"), 2.0)
        SheetMetalTools.smAddAngleProperty(obj, "rotation", tr("App::Property", "Rotation of the shape in the flat pattern"), 0.0)
        SheetMetalTools.smAddFloatProperty(obj, "kfactor", tr("App::Property", "K-factor of the flat pattern (ANSI)"), 0.4)
        SheetMetalTools.smAddBoolProperty(obj, "kfromPart", tr("App::Property", "Take the K-factor from the bends of the part (the nearest feature below with a K-factor); off uses kfactor"), True)
        SheetMetalTools.smAddFloatProperty(obj, "kUsed", tr("App::Property", "K-factor the relief was built with"), 0.4)
        obj.setEditorMode("kUsed", 1)
        SheetMetalTools.smAddFloatProperty(obj, "flatVolumeOffset", tr("App::Property", "Change this cut makes to (flat volume - part volume), mm3"), 0.0)
        obj.setEditorMode("flatVolumeOffset", 1)
        if "bendRadii" not in obj.PropertiesList:
            obj.addProperty("App::PropertyFloatList", "bendRadii", "Parameters", "Inside radii of the two bends the relief cuts")
            obj.setEditorMode("bendRadii", 1)
        SheetMetalTools.smAddLengthProperty(obj, "thickness", tr("App::Property", "Thickness (from the base)"), 0.0)
        obj.setEditorMode("thickness", 1)
        SheetMetalTools.smAddLengthProperty(obj, "sizeUsed", tr("App::Property", "Size used in mm"), 0.0)
        obj.setEditorMode("sizeUsed", 1)

    def getElementMapVersion(self, _fp, ver, _prop, restored):
        return smElementMapVersion + ver if not restored else None

    def execute(self, fp):
        self.addVerifyProperties(fp)
        base = fp.baseObject[0].Shape.copy()
        k_used = part_kfactor(fp.baseObject[0]) if fp.kfromPart else None
        if k_used is None:
            k_used = fp.kfactor
        info = {}
        try:
            shape = build_corner_relief(base, fp.baseObject[1][0], str(fp.reliefType), fp.size,
                                        str(fp.sizeMode), k_used, fp.ratio, fp.rotation.Value,
                                        info)
            edge = base.getElement(SheetMetalTools.getElementFromTNP(fp.baseObject[1][0]))
            thk = edge.Length
        except ReliefError as exc:
            raise FreeCAD.Base.FreeCADError("Corner relief: %s" % exc)
        fp.kUsed = k_used
        fp.flatVolumeOffset = info["flat_volume_offset"]
        fp.bendRadii = info["radii"]
        fp.thickness = thk
        fp.sizeUsed = effective_size(fp.size, str(fp.sizeMode), thk)
        fp.Shape = shape


def make_corner_relief(doc, base_obj, edge_name, name="CornerRelief", **props):
    """Create a recomputed corner relief feature; props set the feature's properties."""
    obj = doc.addObject("Part::FeaturePython", name)
    SMBendCornerRelief(obj, base_obj, [edge_name])
    for key, value in props.items():
        setattr(obj, key, value)
    doc.recompute()
    SheetMetalTools.attach_view_provider(obj)
    return obj
