# SPDX-License-Identifier: LGPL-2.1-or-later
"""Forming-tool library: parametric punch solids applied through the vendored forming path.

cad-fork G07 slice 4a. Written without reference to any GPL file of the add-on: the tool
geometry is plain OCCT (Part) built from named parameters held in ``FormingTools/tools.json``
(name, kind, parameters with default, unit and minimum, public source). The vendored
``SheetMetalFormingCmd.makeforming`` does the forming itself.

How the vendored forming path works (read from the LGPL file SheetMetalFormingCmd.py): the
punch solid is laid with its selected face on the sheet face and the body going into the sheet
(opposite the face normal). The punch volume is cut from the sheet, and a shell made of all
punch faces except the selected ones, offset outwards by the sheet thickness T, is added where
it lies outside the sheet. So the formed wall is T thick wherever the punch protrudes below
the sheet, and the punch depth must be at least T (``apply_tool`` refuses a smaller one).
Faces named as extra "shear" faces get no wall: they are the open (sheared) sides of a louver
or lance, the open bottom of an extruded or countersunk hole.

Tool frame: top face in the plane z = 0 with outward normal +z, body below (z < 0), footprint
centred on the origin.

Expected volumes (``expected_volume_change``) are computed without OCCT booleans or offsets,
from the tool's 2D section: the wall is the path of the tool surface offset by T with
intersection joins (plain 2D line intersections), clipped to the part below the sheet bottom,
and revolved (Pappus) or extruded; the ribbed stadium has a closed form.

Frames: ``offset`` (OffsetX, OffsetY) moves the tool in the plane of the face along the face's
own axes ``u`` (global X projected into the face, global Y if X is normal to it) and ``v`` = n x u
(n the outward face normal): for a face normal to +z these are the global x and y. The tool's own
orientation about the normal is the minimal rotation of the vendored path, plus ``angle``.

Checks that stop a silently wrong result: the tool footprint must lie inside the face with at
least ``min_edge_distance`` to its boundary; the stub of a countersunk hole must reach the
corner of the offset cone (``min_countersink_stub``), below which the vendored path falls back to
an alternate way that returns a wrong solid; and the volume change must equal
``expected_volume_change`` within 1e-6 relative, or the forming is refused.

Sources: see the ``source`` field of each tool in ``FormingTools/tools.json``. The default
proportions are rule-of-thumb starting points, not checked against a published source; only the
countersink angles are tied to standards (82 degrees for inch flat-head machine screws, ASME
B18.6.3; 90 degrees for ISO 10642 and ISO 15065).
"""

import json
import math
import os

import FreeCAD
import Part
from FreeCAD import Vector as V

import SheetMetalFormingCmd as FC
import SheetMetalTools

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "FormingTools", "tools.json")
EPS = 1e-9


class FormingToolError(Exception):
    """A forming tool cannot be built or applied with the given parameters."""


# --- data and loader -----------------------------------------------------------------------
_CACHE = None


def _load():
    global _CACHE
    if _CACHE is None:
        with open(DATA_FILE, encoding="utf-8") as fh:
            _CACHE = {t["name"]: t for t in json.load(fh)["tools"]}
    return _CACHE


def list_tools():
    """Names of the tools in the library, in file order."""
    return list(_load())


def tool_info(name):
    """The data record of a tool: name, kind, description, parameters, source."""
    try:
        return _load()[name]
    except KeyError:
        raise FormingToolError("unknown forming tool %r; available: %s" % (name, ", ".join(list_tools())))


def _params(name, given):
    info = tool_info(name)
    spec = info["parameters"]
    unknown = sorted(set(given) - set(spec))
    if unknown:
        raise FormingToolError("tool %r has no parameter(s) %s; it has %s" % (name, unknown, sorted(spec)))
    out = {}
    for key, rec in spec.items():
        val = float(given.get(key, rec["default"]))
        if val < rec["min"] - EPS:
            raise FormingToolError("%s of tool %r must be at least %g %s, got %g"
                                   % (key, name, rec["min"], rec["unit"], val))
        if "max" in rec and val > rec["max"] + EPS:
            raise FormingToolError("%s of tool %r must be at most %g %s, got %g"
                                   % (key, name, rec["max"], rec["unit"], val))
        out[key] = val
    return out


# --- 2D helpers for the expected volumes ---------------------------------------------------
def _area_centroid(poly):
    a = cx = 0.0
    for (x0, z0), (x1, z1) in zip(poly, poly[1:] + poly[:1]):
        cr = x0 * z1 - x1 * z0
        a += cr
        cx += (x0 + x1) * cr
    a /= 2.0
    return abs(a), (cx / (6.0 * a) if abs(a) > EPS else 0.0)


def _clip(poly, keep_below, level):
    """Sutherland-Hodgman against the half plane z <= level (keep_below) or z >= level."""
    def inside(p):
        return p[1] <= level + EPS if keep_below else p[1] >= level - EPS
    out = []
    for p, q in zip(poly, poly[1:] + poly[:1]):
        pin, qin = inside(p), inside(q)
        if pin:
            out.append(p)
        if pin != qin:
            t = (level - p[1]) / (q[1] - p[1])
            out.append((p[0] + t * (q[0] - p[0]), level))
        elif False:
            pass
    return out


def _inside(poly, pt):
    """Even-odd point in polygon test."""
    x, z = pt
    hit = False
    for (x0, z0), (x1, z1) in zip(poly, poly[1:] + poly[:1]):
        if (z0 > z) != (z1 > z) and x < x0 + (z - z0) * (x1 - x0) / (z1 - z0):
            hit = not hit
    return hit


def _offset_path(path, section, thk):
    """Offset an open polyline that is part of the boundary of the closed polygon `section`
    outwards (away from the polygon) by thk, intersection joins, flush (perpendicular) ends.
    Returns the offset points."""
    eps = 1e-7 * max(1.0, max(abs(c) for p in section for c in p))
    lines = []
    for p, q in zip(path, path[1:]):
        dx, dz = q[0] - p[0], q[1] - p[1]
        ln = math.hypot(dx, dz)
        n = (dz / ln, -dx / ln)
        mid = ((p[0] + q[0]) / 2.0, (p[1] + q[1]) / 2.0)
        if _inside(section, (mid[0] + eps * n[0], mid[1] + eps * n[1])):
            n = (-n[0], -n[1])
        lines.append((p, (dx / ln, dz / ln), n))
    pts = [(path[0][0] + thk * lines[0][2][0], path[0][1] + thk * lines[0][2][1])]
    for (p1, d1, n1), (p2, d2, n2) in zip(lines, lines[1:]):
        a = (p1[0] + thk * n1[0], p1[1] + thk * n1[1])
        b = (p2[0] + thk * n2[0], p2[1] + thk * n2[1])
        den = d1[0] * d2[1] - d1[1] * d2[0]
        if abs(den) < 1e-12:  # collinear
            pts.append(b)
            continue
        s = ((b[0] - a[0]) * d2[1] - (b[1] - a[1]) * d2[0]) / den
        pts.append((a[0] + s * d1[0], a[1] + s * d1[1]))
    end, (_p, _d, n) = path[-1], lines[-1]
    pts.append((end[0] + thk * n[0], end[1] + thk * n[1]))
    return pts


def _revolved(poly):
    a, cx = _area_centroid(poly)
    return 2.0 * math.pi * a * cx


def _prism(poly, width):
    return _area_centroid(poly)[0] * width


def _stadium_area(w, ln):
    return w * (ln - w) + math.pi * w * w / 4.0


# --- the tool object -----------------------------------------------------------------------
class FormingTool:
    """A punch solid plus what the forming path needs to know about it.

    shape: the punch solid; top_face: name of the face that sits on the sheet face; shear_faces:
    names of the faces that get no wall; params: the parameters used; kind, name, depth.
    """

    def __init__(self, name, kind, params, shape, top_face, shear_faces, path, section, revolved,
                 width=None):
        self.name, self.kind, self.params = name, kind, params
        self.shape, self.top_face, self.shear_faces = shape, top_face, shear_faces
        self.depth = params["depth"] if "depth" in params else -min(p[1] for p in section)
        self._path, self._section, self._revolved, self._width = path, section, revolved, width

    @property
    def spec(self):
        """JSON of the tool (name and its library parameters), stored on a forming feature."""
        keys = tool_info(self.name)["parameters"]
        return json.dumps({"name": self.name, "params": {k: self.params[k] for k in keys}},
                          sort_keys=True)

    @property
    def volume(self):
        """Volume of the punch solid, as OCCT measures it."""
        return self.shape.Volume

    def analytic_volume(self):
        if self.kind == "rib":
            return _stadium_area(self.params["width"], self.params["length"]) * self.params["depth"]
        if self._revolved:
            return _revolved(self._section)
        return _prism(self._section, self._width)

    def expected_volume_change(self, thickness):
        """Predicted volume change of a plate of thickness T after forming (wall added below the
        plate minus punch volume removed from the plate), by independent 2D / closed-form maths."""
        t, h = thickness, self.depth
        if h < t - EPS:
            raise FormingToolError("tool depth %g is smaller than the sheet thickness %g" % (h, t))
        if self.kind == "rib":
            w, ln = self.params["width"], self.params["length"]
            wall = _stadium_area(w + 2 * t, ln + 2 * t) * h - _stadium_area(w, ln) * (h - t)
            return wall - _stadium_area(w, ln) * t
        off = _offset_path(self._path, self._section, t)
        wall_poly = _clip(self._path + off[::-1], True, -t)
        pocket_poly = _clip(self._section, False, -t)
        if self._revolved:
            return _revolved(wall_poly) - _revolved(pocket_poly)
        return _prism(wall_poly, self._width) - _prism(pocket_poly, self._width)


def _face_name(shape, pred):
    names = ["Face%d" % (i + 1) for i, f in enumerate(shape.Faces) if pred(f)]
    return names


def _planar_at(z, nz):
    def pred(f):
        return (f.Surface.TypeId == "Part::GeomPlane" and abs(f.CenterOfMass.z - z) < 1e-9
                and f.normalAt(0, 0).z * nz > 0.9)
    return pred


def _revolve(points):
    poly = Part.makePolygon([V(r, 0, z) for r, z in points] + [V(points[0][0], 0, points[0][1])])
    return Part.Face(poly).revolve(V(0, 0, 0), V(0, 0, 1), 360)


def _prism_xz(points, width):
    poly = Part.makePolygon([V(x, -width / 2.0, z) for x, z in points]
                            + [V(points[0][0], -width / 2.0, points[0][1])])
    return Part.Face(poly).extrude(V(0, width, 0))


def _stadium_prism(w, ln, depth):
    r, c = w / 2.0, (ln - w) / 2.0
    e = [Part.LineSegment(V(-c, -r, 0), V(c, -r, 0)),
         Part.Arc(V(c, -r, 0), V(c + r, 0, 0), V(c, r, 0)),
         Part.LineSegment(V(c, r, 0), V(-c, r, 0)),
         Part.Arc(V(-c, r, 0), V(-c - r, 0, 0), V(-c, -r, 0))]
    return Part.Face(Part.Wire([x.toShape() for x in e])).extrude(V(0, 0, -depth))


def make_tool(name, **params):
    """Build the named tool from its parameters (defaults from the data file) -> FormingTool."""
    info = tool_info(name)
    p = _params(name, params)
    kind = info["kind"]
    shear = []
    width = None
    if kind == "dimple":
        r1, r2, h = p["top_diameter"] / 2, p["bottom_diameter"] / 2, p["depth"]
        if r2 >= r1:
            raise FormingToolError("dimple bottom_diameter must be smaller than top_diameter")
        if r2 <= 0:
            raise FormingToolError("dimple bottom_diameter must be greater than zero: a pointed "
                                   "cone cannot be offset by the forming path (measured in run 027)")
        path = [(r1, 0.0), (r2, -h), (0.0, -h)]
        section = [(0.0, 0.0)] + path
        shape, rev = _revolve(section), True
    elif kind == "extruded_hole":
        r, h = p["hole_diameter"] / 2, p["depth"]
        path, section = [(r, 0.0), (r, -h)], [(0.0, 0.0), (r, 0.0), (r, -h), (0.0, -h)]
        shape, rev = _revolve(section), True
    elif kind == "countersunk_hole":
        rh, rc, ang = p["hole_diameter"] / 2, p["countersink_diameter"] / 2, p["angle"]
        if rc <= rh:
            raise FormingToolError("countersink_diameter must be larger than hole_diameter")
        if ang >= 180.0:
            raise FormingToolError("countersink angle must be below 180 degrees")
        d = (rc - rh) / math.tan(math.radians(ang / 2.0))
        if p["stub"] <= 0:
            raise FormingToolError("stub must be greater than zero (the hole needs a cylinder to open it)")
        h = d + p["stub"]
        p["depth"] = h
        path = [(rc, 0.0), (rh, -d), (rh, -h)]
        section = [(0.0, 0.0)] + path + [(0.0, -h)]
        shape, rev = _revolve(section), True
    elif kind == "rib":
        w, ln, h = p["width"], p["length"], p["depth"]
        if ln <= w:
            raise FormingToolError("rib length must be larger than its width")
        shape, rev, path, section = _stadium_prism(w, ln, h), False, None, None
        section = [(-ln / 2, 0.0), (ln / 2, 0.0), (ln / 2, -h), (-ln / 2, -h)]
    elif kind == "louver":
        ln, width, h, s = p["length"], p["width"], p["depth"], p["slope_inset"]
        if 2 * s >= ln:
            raise FormingToolError("louver slope_inset must be less than half the length")
        path = [(-ln / 2, 0.0), (-ln / 2 + s, -h), (ln / 2 - s, -h), (ln / 2, 0.0)]
        section, rev = path, False
        shape = _prism_xz(section, width)
        shear = _face_name(shape, lambda f: abs(abs(f.CenterOfMass.y) - width / 2) < 1e-9
                           and f.Surface.TypeId == "Part::GeomPlane")
    elif kind == "lance":
        ln, width, h = p["length"], p["width"], p["depth"]
        path = [(-ln / 2, 0.0), (ln / 2, -h)]
        section, rev = [(-ln / 2, 0.0), (ln / 2, 0.0), (ln / 2, -h)], False
        shape = _prism_xz(section, width)
        slope = max((f for f in shape.Faces), key=lambda f: f.Area)
        top = _face_name(shape, _planar_at(0.0, 1))
        shear = [n for n in ["Face%d" % (i + 1) for i in range(len(shape.Faces))]
                 if n not in top and not shape.getElement(n).isSame(slope)]
    else:
        raise FormingToolError("unknown tool kind %r" % kind)
    if kind in ("extruded_hole", "countersunk_hole"):
        shear = _face_name(shape, _planar_at(-p["depth"], -1))
    top = _face_name(shape, _planar_at(0.0, 1))
    if len(top) != 1:
        raise FormingToolError("tool %r has no unique top face (internal error)" % name)
    shape = shape.removeSplitter() if hasattr(shape, "removeSplitter") else shape
    # removeSplitter may renumber faces: find them again on the final shape.
    top = _face_name(shape, _planar_at(0.0, 1))
    if kind in ("extruded_hole", "countersunk_hole"):
        shear = _face_name(shape, _planar_at(-p["depth"], -1))
    elif kind == "louver":
        shear = _face_name(shape, lambda f: abs(abs(f.CenterOfMass.y) - width / 2) < 1e-9
                           and f.Surface.TypeId == "Part::GeomPlane")
    elif kind == "lance":
        slope = max((f for f in shape.Faces), key=lambda f: f.Area)
        shear = [n for n in ["Face%d" % (i + 1) for i in range(len(shape.Faces))]
                 if n not in top and not shape.getElement(n).isSame(slope)]
    if not shape.isValid() or shape.Volume <= 0:
        raise FormingToolError("tool %r with %s is not a valid solid" % (name, p))
    return FormingTool(name, kind, p, shape, top[0], shear, path, section, rev, width)


# --- applying a tool -----------------------------------------------------------------------
VOLUME_TOL = 1e-6  # relative, formed volume change against expected_volume_change


def min_countersink_stub(angle, thickness):
    """Shortest stub of a countersunk hole that the vendored forming path handles in sheet of
    thickness T. The shell of the cone is offset outwards by T; its intersection with the offset
    of the cylinder lies T * tan(angle / 4) below the cone-to-stub corner (half angle a/2: the
    offset cone is T / cos(a/2) away radially, the corner (T / cos(a/2) - T) / tan(a/2) lower).
    A shorter stub ends above that corner, the offset fails and the vendored code falls back
    to an alternate way that returns a valid but wrong solid (measured in run 027)."""
    return thickness * math.tan(math.radians(angle) / 4.0)


def _single_solid(shape):
    if not shape.Solids:
        raise FormingToolError("forming produced no solid")
    if len(shape.Solids) != 1:
        raise FormingToolError("forming produced %d solids, expected 1" % len(shape.Solids))
    out = shape.Solids[0]
    if not out.isValid():
        raise FormingToolError("forming produced an invalid solid")
    return out


def check_tool_fits_sheet(tool, thickness):
    """Refusals that depend on the sheet thickness alone."""
    if tool.depth < thickness - EPS:
        raise FormingToolError("tool depth %g is smaller than the sheet thickness %g: the formed "
                               "wall would be thinner than the sheet" % (tool.depth, thickness))
    if tool.kind == "countersunk_hole":
        need = min_countersink_stub(tool.params["angle"], thickness)
        if tool.params["stub"] < need - EPS:
            raise FormingToolError(
                "countersunk_hole stub %g is shorter than %.4g (sheet thickness %g x tan(angle/4)): "
                "the vendored forming path would fall back and return a wrong solid; lengthen "
                "the stub" % (tool.params["stub"], need, thickness))


def check_placement(tool, base_face, thickness, point, angle, min_edge_distance=0.0):
    """Refuse when the tool footprint (its top face, placed like the vendored path places it) is
    not wholly inside `base_face`, or is closer to the face boundary than `min_edge_distance`
    (the same rule as the lightening cutout's MinEdgeDistance). The formed wall reaches up to
    about T beyond the footprint at the sheet bottom; an overreach shows as a volume mismatch."""
    check_tool_fits_sheet(tool, thickness)
    top = tool.shape.getElement(tool.top_face)
    foot = top.copy()
    FC.transform_tool(foot, base_face, top, point, angle)
    inside = base_face.common(foot).Area
    if abs(inside - foot.Area) > 1e-6 * foot.Area:
        raise FormingToolError(
            "the %s footprint is not inside the selected face (%.4g of %.4g mm2 inside): move it "
            "with OffsetX and OffsetY so it lies on the flat wall, clear of the sheet edge, a "
            "bend and any opening" % (tool.name, inside, foot.Area))
    if min_edge_distance > 0:
        dist = foot.OuterWire.distToShape(Part.Compound(list(base_face.Wires)))[0]
        if dist < min_edge_distance - 1e-9:
            raise FormingToolError("the %s footprint is %.4g mm from the face boundary (edge, bend "
                                   "or opening), below the minimum of %.4g mm"
                                   % (tool.name, dist, min_edge_distance))


def check_result(tool, before, after, thickness):
    """Refuse unless `after` is one valid solid whose volume change against `before` is the
    expected one within VOLUME_TOL relative to the larger of the expected change and the tool
    volume (so that a change near zero is not judged against rounding noise)."""
    solid = _single_solid(after)
    want = tool.expected_volume_change(thickness)
    got = solid.Volume - before.Volume
    if abs(got - want) > VOLUME_TOL * max(abs(want), tool.volume):
        raise FormingToolError(
            "forming with %s changed the volume by %.6g mm3 instead of the expected %.6g: the "
            "formed wall probably reaches the sheet edge, a bend or an opening, or the vendored "
            "forming path failed; move the tool or change its parameters" % (tool.name, got, want))
    return solid


def _tool_from_json(spec):
    data = json.loads(spec)
    return make_tool(data["name"], **data["params"])


def check_placement_json(spec, base, base_face, tool_face, thickness, point, angle,
                         min_edge_distance=0.0):
    """check_placement for the LibraryTool property of a forming feature."""
    check_placement(_tool_from_json(spec), base_face, thickness, point, angle, min_edge_distance)


def check_result_json(spec, before, after, thickness):
    check_result(_tool_from_json(spec), before, after, thickness)


def apply_tool(base_shape, face_name, tool, offset=(0.0, 0.0), angle=0.0, min_edge_distance=0.0):
    """Form `tool` into the planar sheet face `face_name` of `base_shape` through the vendored
    makeforming. offset is the (u, v) shift of the tool from the face centre along the face's own
    axes (see the module docstring; for a face normal to +z the global x and y), angle the
    rotation about the face normal in degrees. Returns one valid solid. Refuses a non-planar
    face, a tool shallower than the sheet thickness, a footprint not inside the face (or closer
    than min_edge_distance to its boundary), a countersink stub that is too short, and any
    result whose volume change is not the expected one."""
    face = base_shape.getElement(SheetMetalTools.getElementFromTNP(face_name))
    if face.Surface.TypeId != "Part::GeomPlane":
        raise FormingToolError("the forming face must be planar")
    thk = SheetMetalTools.smGetThickness(base_shape, face)
    if thk <= 0:
        raise FormingToolError("cannot read the sheet thickness at the selected face")
    point = FC.face_offset(face, offset[0], offset[1])
    check_placement(tool, face, thk, point, angle, min_edge_distance)
    faces = [tool.shape.getElement(n) for n in [tool.top_face] + list(tool.shear_faces)]
    try:
        formed = FC.makeforming(tool.shape, base_shape, face, thk, faces, point, angle)
    except Exception as exc:  # OCCT or makeforming failure: say so readably
        raise FormingToolError("forming with %s failed: %s" % (tool.name, exc))
    return check_result(tool, base_shape, formed, thk)


def make_forming_feature(doc, base, face_name, tool, offset=(0.0, 0.0), angle=0.0,
                         min_edge_distance=0.0):
    """Document feature: a Part::Feature holding the punch plus a vendored SMBendWall
    (``Part::FeaturePython``) that forms it into `face_name` of `base`; the feature carries the
    library tool in its LibraryTool property, so recompute applies the same checks as
    ``apply_tool`` (a refusal fails the recompute with the message). Returns the feature."""
    tool_obj = doc.addObject("Part::Feature", "FormTool_" + tool.name)
    tool_obj.Shape = tool.shape
    feat = doc.addObject("Part::FeaturePython", "WallForming")
    FC.SMBendWall(feat, base, [face_name], tool_obj, [tool.top_face] + list(tool.shear_faces))
    feat.addProperty("App::PropertyString", "LibraryTool", "Parameters",
                     "Forming-tool library tool (name and parameters)").LibraryTool = tool.spec
    feat.OffsetX = offset[0]
    feat.OffsetY = offset[1]
    feat.angle = angle
    feat.MinEdgeDistance = min_edge_distance
    doc.recompute()
    return feat
