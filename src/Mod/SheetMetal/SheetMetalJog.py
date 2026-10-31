# SPDX-License-Identifier: LGPL-2.1-or-later
"""Jog: two opposite bends along a line that step a flat sheet-metal wall sideways (an offset).

cad-fork G07 slice 4a. Written without reference to the add-on's GPL files (the implementer never
opened SheetMetalCmd.py, SheetMetalSketch.py, SheetMetalJunction.py or any other GPL-headed
file): only the public fabrication knowledge below, OCCT/Part, and the LGPL helpers vendored beside
this file.

Public references used
- Oberg, Jones, Horton, Ryffel, "Machinery's Handbook" (Industrial Press), sheet-metal chapter,
  "Bend allowance": BA = (pi / 180) * theta * (R + K * T) per bend, theta in degrees, R the inside
  radius, T the thickness, K the neutral-axis factor.
- Wikipedia, "Bending (metalworking)": outside setback tan(theta / 2) * (R + T); manufacturer
  sheet-metal design guides (jog / offset bend, joggle): both bends of a jog share one inside
  radius, a jog needs a straight segment between the bends (here s >= 0, the two arcs may touch
  but not overlap) and the usual practice is a joggle height of at least about two thicknesses.

Geometry (profile in the plane perpendicular to the line; x runs from the fixed side to the moved
side, z up the direction the wall moves, the fixed wall occupying 0 <= z <= T before the jog):

- bend 1 turns the sheet up by theta: inside (top face) radius R, outside (bottom face) R + T;
- a straight slanted segment of length s;
- bend 2 turns the sheet back down by theta: inside (bottom face) radius R, outside R + T;
- the moved wall continues parallel to the fixed one, displaced by d = (2R + T)(1 - cos theta)
  + s sin theta along z, and by X = (2R + T) sin theta + s cos theta along x (jog zone width).

The sketch line is the centre of the jog zone: the fixed flat ends X / 2 before it and the moved
flat starts X / 2 after it (tangent lines). Material beyond the zone on the moved side is the
original sheet translated by d perpendicular to the sheet only, so the part keeps its overall
x-extent and the developed length changes by 2 BA + s - X.

Offset types (the offset value is converted to the displacement d of the moved wall):

- ``outside``: d = offset, the distance between a face of the fixed wall and the same-side face of
  the moved wall;
- ``inside``: d = offset + T, offset is the clear gap between the fixed wall's face on the moved
  side and the moved wall's facing surface;
- ``overall``: d = offset - T, offset is the distance between the far faces (fixed wall's face
  away from the move to the moved wall's face on the far side).

Properties of the feature: baseObject (the sheet face), sketch (one straight line on the face),
offset, offsetType, angle (default 90), radius (0 means 1 x T), kfactor, flip (move against the
face normal instead of along it) and fixedSide (which side of the line stays put, looking down
the face normal from the line start to the end).

The jog needs a plain slab in the zone: the face must be a full-width rectangle across the jog
zone (no holes, notches or flanges there); otherwise a JogError explains it.
"""

import math

import FreeCAD
import Part
from FreeCAD import Vector

import SheetMetalEdgeFlange as EF
import SheetMetalTools

EPS = 1e-6
smElementMapVersion = "sm1."
OFFSET_TYPES = ["outside", "inside", "overall"]
FIXED_SIDES = ["Left", "Right"]


class JogError(Exception):
    """The jog cannot be built with the given face, line or parameters."""


def displacement(offset, offset_type, thickness):
    """Displacement d of the moved wall for the offset value and convention."""
    if offset_type == "outside":
        return offset
    if offset_type == "inside":
        return offset + thickness
    if offset_type == "overall":
        return offset - thickness
    raise JogError("offset type must be one of %s, got %r" % (OFFSET_TYPES, offset_type))


def jog_dimensions(d, angle_deg, radius, thickness):
    """(s, X): straight slanted length and jog zone width for displacement d. s < 0 when the two
    bends do not fit."""
    th = math.radians(angle_deg)
    s = (d - (2.0 * radius + thickness) * (1.0 - math.cos(th))) / math.sin(th)
    x = (2.0 * radius + thickness) * math.sin(th) + s * math.cos(th)
    return s, x


def bend_allowance(angle_deg, radius, thickness, k):
    return EF.bend_allowance(angle_deg, radius, thickness, k)


def developed_extra(angle_deg, radius, thickness, k, s):
    """Flat-length change of the jog against a straight sheet of zone width X: 2 BA + s."""
    return 2.0 * bend_allowance(angle_deg, radius, thickness, k) + s


def _check_finite(**values):
    for key, v in values.items():
        if not isinstance(v, (int, float)) or not math.isfinite(v):
            raise JogError("%s must be a finite number, got %r" % (key, v))


def _profile_points(radius, thickness, angle_deg, s):
    """Closed profile as segments in (x, z), x = 0 at the fixed tangent line. Returns the list of
    ('L', a, b) and ('A', a, mid, b) tuples and the end x of the zone."""
    th = math.radians(angle_deg)
    r, t = radius, thickness

    def nrm(a):  # sheet normal after turning through a
        return (-math.sin(a), math.cos(a))

    n1 = nrm(th)
    b0, u0 = (0.0, 0.0), (0.0, t)
    c1 = (0.0, t + r)
    nh = nrm(th / 2.0)
    b1 = (c1[0] - (r + t) * n1[0], c1[1] - (r + t) * n1[1])
    u1 = (c1[0] - r * n1[0], c1[1] - r * n1[1])
    mb1 = (c1[0] - (r + t) * nh[0], c1[1] - (r + t) * nh[1])
    mu1 = (c1[0] - r * nh[0], c1[1] - r * nh[1])
    d1 = (math.cos(th), math.sin(th))
    b2 = (b1[0] + s * d1[0], b1[1] + s * d1[1])
    u2 = (u1[0] + s * d1[0], u1[1] + s * d1[1])
    c2 = (b2[0] - r * n1[0], b2[1] - r * n1[1])
    b3 = (c2[0], c2[1] + r)
    u3 = (c2[0], c2[1] + r + t)
    mb2 = (c2[0] + r * nh[0], c2[1] + r * nh[1])
    mu2 = (c2[0] + (r + t) * nh[0], c2[1] + (r + t) * nh[1])
    segs = [("A", b0, mb1, b1), ("L", b1, b2), ("A", b2, mb2, b3), ("L", b3, u3),
            ("A", u3, mu2, u2), ("L", u2, u1), ("A", u1, mu1, u0), ("L", u0, b0)]
    return segs, b3


def make_jog_solid(x0, y0, width, radius, thickness, angle_deg, s):
    """Jog zone solid in local coordinates: profile in (x, z) at y = y0, extruded `width` along +y,
    the fixed tangent line at x = x0."""
    segs, _end = _profile_points(radius, thickness, angle_deg, s)
    edges = []
    for seg in segs:
        pts = [Vector(x0 + p[0], y0, p[1]) for p in seg[1:]]
        if seg[0] == "L":
            if (pts[0] - pts[1]).Length < 1e-9:
                continue
            edges.append(Part.LineSegment(*pts).toShape())
        else:
            edges.append(Part.Arc(*pts).toShape())
    face = Part.Face(Part.Wire(Part.__sortEdges__(edges)))
    return face.extrude(Vector(0, width, 0))


def line_from_sketch(sketch):
    """The two end points of the single straight line of a sketch (or any shape)."""
    shape = getattr(sketch, "Shape", None)
    if shape is None or shape.isNull() or len(shape.Edges) != 1:
        raise JogError("the sketch must hold exactly one straight line (it has %d edges)"
                       % (0 if shape is None or shape.isNull() else len(shape.Edges)))
    edge = shape.Edges[0]
    if not isinstance(edge.Curve, Part.Line):
        raise JogError("the jog line must be straight")
    return edge.Vertexes[0].Point, edge.Vertexes[-1].Point


def _box(x0, x1, y0, y1, z0, z1):
    return Part.makeBox(x1 - x0, y1 - y0, z1 - z0, Vector(x0, y0, z0))


def build_jog(base_shape, face_name, p0, p1, offset, offset_type="outside", angle=90.0,
              radius=0.0, flip=False, fixed_side="Left"):
    """Pure geometry: put a jog into the flat sheet-metal wall carrying face `face_name` of
    `base_shape` along the line p0 to p1 (points on the face). radius 0 means 1 x T. Returns one
    valid solid; raises JogError with a readable message on bad input."""
    _check_finite(offset=offset, angle=angle, radius=radius)
    for name, p in (("line start", p0), ("line end", p1)):
        _check_finite(**{name + " x": p.x, name + " y": p.y, name + " z": p.z})
    if offset_type not in OFFSET_TYPES:
        raise JogError("offset type must be one of %s, got %r" % (OFFSET_TYPES, offset_type))
    if fixed_side not in FIXED_SIDES:
        raise JogError("fixed side must be 'Left' or 'Right'")
    if not 0.0 < angle <= 90.0:
        raise JogError("jog angle must be greater than 0 and at most 90 degrees, got %g" % angle)
    if radius < 0.0:
        raise JogError("bend radius cannot be negative, got %g" % radius)
    face = base_shape.getElement(SheetMetalTools.getElementFromTNP(face_name))
    if not isinstance(face.Surface, Part.Plane):
        raise JogError("the jog face must be planar")
    thk = EF.sheet_thickness_of(base_shape)
    if thk is None:
        raise JogError("the base is not a sheet-metal solid (no wall thickness found)")
    r = radius if radius > 0.0 else thk
    d = displacement(offset, offset_type, thk)
    if d <= EPS:
        raise JogError("the offset gives a displacement of %g mm; it must be positive "
                       "(overall offsets must exceed the thickness %g)" % (d, thk))
    s, xw = jog_dimensions(d, angle, r, thk)
    if s < -EPS:
        raise JogError("the offset is too small for two bends of radius %g, angle %g and thickness %g "
                       "to fit: the displacement must be at least %g mm (it is %g)"
                       % (r, angle, thk, (2.0 * r + thk) * (1.0 - math.cos(math.radians(angle))), d))
    s = max(s, 0.0)
    # frame on the face
    along = p1 - p0
    if along.Length < EPS:
        raise JogError("the jog line has zero length")
    along.normalize()
    mid = (p0 + p1) * 0.5
    n = face.normalAt(*face.Surface.parameter(mid))
    plane_dist = lambda p: abs((p - face.CenterOfMass).dot(n))  # noqa: E731
    if plane_dist(p0) > 1e-6 or plane_dist(p1) > 1e-6 or abs(along.dot(n)) > 1e-6:
        raise JogError("the jog line does not lie on the selected face")
    if not face.isInside(mid, 1e-6, True):
        raise JogError("the jog line does not lie on the selected face (its middle is outside "
                       "the face)")
    e = n * (-1.0 if flip else 1.0)
    m = n.cross(along)  # points to the left of the line seen along -n
    u = m * (-1.0 if fixed_side == "Left" else 1.0)
    lvec = e.cross(u)  # right-handed (u, l, e)
    origin = mid - n * thk if not flip else mid
    mat = FreeCAD.Matrix(u.x, lvec.x, e.x, origin.x, u.y, lvec.y, e.y, origin.y,
                         u.z, lvec.z, e.z, origin.z, 0, 0, 0, 1)
    place = FreeCAD.Placement(mat)
    inv = place.inverse()
    local = base_shape.copy()
    local.transformShape(inv.toMatrix(), True)

    def lc(p):
        return inv.multVec(p)

    face_l = [lc(v.Point) for v in face.Vertexes]
    ymin, ymax = min(v.y for v in face_l), max(v.y for v in face_l)
    wface = ymax - ymin
    bb = local.BoundBox
    big = 10.0 * max(bb.XLength, bb.YLength, bb.ZLength) + 10.0
    x1, x4 = -xw / 2.0, xw / 2.0
    # the zone must be a plain full-width slab of the sheet
    slab = local.common(_box(x1, x4, ymin - 1.0, ymax + 1.0, -1.0, thk + 1.0))
    want = thk * xw * wface
    if slab.isNull() or abs(slab.Volume - want) > 1e-6 * want:
        raise JogError("the jog zone (%g mm wide around the line) is not a plain rectangular strip "
                       "of the sheet: it has %g mm3 of material where %g is expected (holes, "
                       "notches, bends or a non-rectangular face in the zone, or the line is too "
                       "near an edge)" % (xw, 0.0 if slab.isNull() else slab.Volume, want))
    fixed = local.common(_box(-big, x1, -big, big, -big, big))
    moved = local.common(_box(x4, big, -big, big, -big, big))
    if fixed.isNull() or fixed.Volume < EPS or moved.isNull() or moved.Volume < EPS:
        raise JogError("the jog zone leaves no flat sheet on one side of the line; move the line "
                       "or reduce the offset, angle or radius")
    moved.translate(Vector(0, 0, d))
    zone = make_jog_solid(x1, ymin, wface, r, thk, angle, s)
    expected = fixed.Volume + zone.Volume + moved.Volume
    fused = fixed.fuse([zone, moved]).removeSplitter()
    if not fused.isValid() or len(fused.Solids) != 1:
        raise JogError("the jog does not fuse into one valid solid")
    if abs(fused.Volume - expected) > 1e-6 * expected:
        raise JogError("the jog collides with the part (volume overlap %g mm3)"
                       % (expected - fused.Volume))
    result = fused.Solids[0] if fused.ShapeType != "Solid" else fused
    result = result.copy()
    result.transformShape(place.toMatrix(), True)
    return result


class SMJog:
    def __init__(self, obj, selobj, face_names, sketch):
        """Add a jog along the line of `sketch` on the face `face_names` of `selobj`."""
        self.addVerifyProperties(obj)
        obj.addProperty("App::PropertyLinkSub", "baseObject", "Parameters",
                        "Base object and face").baseObject = (selobj, face_names)
        obj.addProperty("App::PropertyLink", "sketch", "Parameters",
                        "Sketch holding one straight line on the face").sketch = sketch
        obj.Proxy = self

    def addVerifyProperties(self, obj):
        tr = FreeCAD.Qt.translate
        SheetMetalTools.smAddLengthProperty(obj, "offset", tr("App::Property", "Jog offset"), 10.0)
        SheetMetalTools.smAddEnumProperty(obj, "offsetType", "Offset convention",
                                          OFFSET_TYPES, "outside")
        SheetMetalTools.smAddAngleProperty(obj, "angle", tr("App::Property", "Jog angle, 0 to 90"), 90.0)
        SheetMetalTools.smAddLengthProperty(obj, "radius", tr("App::Property", "Inside bend radius (0 = 1 x thickness)"), 0.0)
        SheetMetalTools.smAddFloatProperty(obj, "kfactor", tr("App::Property", "K-factor (ANSI)"), 0.4)
        SheetMetalTools.smAddBoolProperty(obj, "flip", tr("App::Property", "Move against the face normal"), False)
        SheetMetalTools.smAddEnumProperty(obj, "fixedSide", "Side of the line that stays put",
                                          FIXED_SIDES, "Left")
        SheetMetalTools.smAddLengthProperty(obj, "thickness", tr("App::Property", "Thickness (from the base)"), 0.0)
        obj.setEditorMode("thickness", 1)
        SheetMetalTools.smAddLengthProperty(obj, "radiusUsed", tr("App::Property", "Inside bend radius used"), 0.0)
        obj.setEditorMode("radiusUsed", 1)
        SheetMetalTools.smAddLengthProperty(obj, "displacement", tr("App::Property", "Displacement of the moved wall"), 0.0)
        obj.setEditorMode("displacement", 1)
        SheetMetalTools.smAddLengthProperty(obj, "bendAllowance", tr("App::Property", "Bend allowance per bend (from K-factor)"), 0.0)
        obj.setEditorMode("bendAllowance", 1)

    def getElementMapVersion(self, _fp, ver, _prop, restored):
        return smElementMapVersion + ver if not restored else None

    def execute(self, fp):
        self.addVerifyProperties(fp)
        base = fp.baseObject[0].Shape.copy()
        try:
            p0, p1 = line_from_sketch(fp.sketch)
            if not fp.baseObject[1]:
                raise JogError("no face selected")
            thk = EF.sheet_thickness_of(base)
            if thk is None:
                raise JogError("the base is not a sheet-metal solid (no wall thickness found)")
            shape = build_jog(base, fp.baseObject[1][0], p0, p1, fp.offset.Value, str(fp.offsetType),
                              fp.angle.Value, fp.radius.Value, fp.flip, str(fp.fixedSide))
        except JogError as exc:
            raise FreeCAD.Base.FreeCADError("Jog: %s" % exc)
        r = fp.radius.Value if fp.radius.Value > 0.0 else thk
        fp.thickness = thk
        fp.radiusUsed = r
        fp.displacement = displacement(fp.offset.Value, str(fp.offsetType), thk)
        fp.bendAllowance = bend_allowance(fp.angle.Value, r, thk, fp.kfactor)
        fp.Shape = shape


def make_jog(doc, base_obj, face_name, sketch, name="Jog", **props):
    """Create a recomputed jog feature; props set the feature's properties."""
    obj = doc.addObject("Part::FeaturePython", name)
    SMJog(obj, base_obj, [face_name], sketch)
    for key, value in props.items():
        setattr(obj, key, value)
    doc.recompute()
    return obj
