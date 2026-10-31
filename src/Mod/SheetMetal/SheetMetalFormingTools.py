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

Public references: fabrication design guides for the feature families (louver, lance, dimple,
stiffening rib or bead, extruded hole, countersunk hole), for example the fabcon.com
sheet-metal DFM guide (dimple, hole and bend distances); ISO 10642 and ASME B18.6.3 for the
90 degree countersink. Default proportions are rule-of-thumb starting points, not normative.
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


def _offset_path(path, inside_pt, thk):
    """Offset an open polyline outwards (away from inside_pt) by thk, intersection joins,
    flush (perpendicular) ends. Returns the offset points."""
    lines = []
    for p, q in zip(path, path[1:]):
        dx, dz = q[0] - p[0], q[1] - p[1]
        ln = math.hypot(dx, dz)
        n = (dz / ln, -dx / ln)
        mid = ((p[0] + q[0]) / 2.0 - inside_pt[0], (p[1] + q[1]) / 2.0 - inside_pt[1])
        if n[0] * mid[0] + n[1] * mid[1] < 0:
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
        cen = _area_centroid(self._section)
        inside = (cen[1], sum(p[1] for p in self._section) / len(self._section))
        off = _offset_path(self._path, inside, t)
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
def _single_solid(shape):
    if not shape.Solids:
        raise FormingToolError("forming produced no solid")
    if len(shape.Solids) != 1:
        raise FormingToolError("forming produced %d solids, expected 1" % len(shape.Solids))
    out = shape.Solids[0]
    if not out.isValid():
        raise FormingToolError("forming produced an invalid solid")
    return out


def apply_tool(base_shape, face_name, tool, offset=(0.0, 0.0), angle=0.0):
    """Form `tool` into the planar sheet face `face_name` of `base_shape` through the vendored
    makeforming. offset is the (x, y) shift of the tool from the face centre in global axes
    (for a face normal to z), angle the rotation about the face normal in degrees. Returns one
    valid solid. Refuses a tool shallower than the sheet thickness or a non-planar face."""
    face = base_shape.getElement(SheetMetalTools.getElementFromTNP(face_name))
    if face.Surface.TypeId != "Part::GeomPlane":
        raise FormingToolError("the forming face must be planar")
    thk = SheetMetalTools.smGetThickness(base_shape, face)
    if thk <= 0:
        raise FormingToolError("cannot read the sheet thickness at the selected face")
    if tool.depth < thk - EPS:
        raise FormingToolError("tool depth %g is smaller than the sheet thickness %g: the formed "
                               "wall would be thinner than the sheet" % (tool.depth, thk))
    faces = [tool.shape.getElement(n) for n in [tool.top_face] + list(tool.shear_faces)]
    point = V(offset[0], offset[1], 0)
    try:
        formed = FC.makeforming(tool.shape, base_shape, face, thk, faces, point, angle)
    except Exception as exc:  # OCCT or makeforming failure: say so readably
        raise FormingToolError("forming with %s failed: %s" % (tool.name, exc))
    return _single_solid(formed)


def make_forming_feature(doc, base, face_name, tool, offset=(0.0, 0.0), angle=0.0):
    """Document feature: a Part::Feature holding the punch plus a vendored SMBendWall
    (``Part::FeaturePython``) that forms it into `face_name` of `base`. Returns the feature."""
    tool_obj = doc.addObject("Part::Feature", "FormTool_" + tool.name)
    tool_obj.Shape = tool.shape
    feat = doc.addObject("Part::FeaturePython", "WallForming")
    FC.SMBendWall(feat, base, [face_name], tool_obj, [tool.top_face] + list(tool.shear_faces))
    feat.OffsetX = offset[0]
    feat.OffsetY = offset[1]
    feat.angle = angle
    doc.recompute()
    return feat
