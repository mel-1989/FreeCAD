# SPDX-License-Identifier: LGPL-2.1-or-later
"""Bend lines and DXF export of a SheetMetal flat pattern (cad-fork G07 slice 3b).

Clean-room code written for the fork against the public DXF R12 file format (Autodesk's DXF
reference: the HEADER, TABLES and ENTITIES sections with LINE, ARC, CIRCLE and TEXT entities).
It reads data the V1 unfolder already computes (the fold lines of every bend and the bend
node's direction, angle, inside radius and allowance) and writes the flat pattern with no
sketch, view object or GUI, so it runs headlessly.

Conventions
-----------
* The sketch plane is the plane of the selected root face; the viewer looks at it from the
  side the root-face normal points to. A bend is UP when its flange folds toward the viewer
  (the centre of curvature is on the viewer's side) and DOWN when it folds away.
* A bend line is drawn at the bend centreline: the middle of the developed bend zone, at
  BA / 2 from where the bend starts, with BA = angle * (inside radius + K * thickness).
* 2D frame: x is the global X axis projected on the sketch plane (global Y when X is
  perpendicular to it), y = normal x x, so a view from the normal side is not mirrored. The
  frame's origin is moved so that the outline's bounding box starts at (0, 0).
* DXF layers: OUTLINE (outer wire), INNER (cutouts), BEND_UP, BEND_DOWN and NOTES (one TEXT
  per bend, for example ``UP 90° R2 BA 3.456``; the degree sign is written as ``%%d``).
* Output is deterministic: entities are sorted within each layer, coordinates are written with
  nine decimals and there are no handles, dates or timestamps.
"""

import json
import math

import FreeCAD
import Part
from FreeCAD import Vector

LAYERS = (
    # name, ACI colour, linetype
    ("0", 7, "CONTINUOUS"),
    ("OUTLINE", 7, "CONTINUOUS"),
    ("INNER", 3, "CONTINUOUS"),
    ("BEND_UP", 5, "DASHED"),
    ("BEND_DOWN", 1, "DASHED"),
    ("NOTES", 2, "CONTINUOUS"),
)

ROUND = 5  # decimals of the sort keys
DISCRETIZE_DEFLECTION = 0.01  # mm, curves other than lines, circles and arcs


###################################################################################################
# Bend data
###################################################################################################

def bend_note(direction, angle_deg, inside_radius, allowance):
    """The bend note of one bend, for example ``UP 90° R2 BA 3.456``."""
    return "%s %g° R%g BA %.3f" % (
        direction.upper(), round(angle_deg, 2), round(inside_radius, 3), allowance)


class FlatBend:
    """One bend on the flat pattern.

    direction     "UP" or "DOWN" (see the module docstring)
    angle         bend angle in degrees (always positive)
    inside_radius inside bend radius, mm
    allowance     bend allowance BA used for this bend, mm
    k_factor      the (ANSI) K-factor that gives that allowance
    thickness     sheet thickness, mm
    segments      list of (start, end) FreeCAD vectors, the bend centreline on the flat; one
                  segment unless a cutout interrupts the bend
    """

    def __init__(self, direction, angle, inside_radius, allowance, k_factor, thickness, segments):
        self.direction = direction
        self.angle = angle
        self.inside_radius = inside_radius
        self.allowance = allowance
        self.k_factor = k_factor
        self.thickness = thickness
        self.segments = [(Vector(a), Vector(b)) for a, b in segments]

    @classmethod
    def from_record(cls, rec):
        """Build from a dict produced by SheetMetalUnfolder.getUnfold(bend_out=...)."""
        return cls(rec["direction"], math.degrees(rec["angle"]), rec["inside_radius"],
                   rec["allowance"], rec["k_factor"], rec["thickness"], rec["segments"])

    @property
    def note(self):
        return bend_note(self.direction, self.angle, self.inside_radius, self.allowance)

    @property
    def start(self):
        return self._longest()[0]

    @property
    def end(self):
        return self._longest()[1]

    @property
    def length(self):
        """Total length of the bend line segments."""
        return sum((b - a).Length for a, b in self.segments)

    def _longest(self):
        return max(self.segments, key=lambda s: (s[1] - s[0]).Length)

    def transformed(self, placement):
        return FlatBend(self.direction, self.angle, self.inside_radius, self.allowance,
                        self.k_factor, self.thickness,
                        [(placement.multVec(a), placement.multVec(b)) for a, b in self.segments])

    def to_dict(self):
        return {
            "direction": self.direction, "angle": self.angle,
            "inside_radius": self.inside_radius, "allowance": self.allowance,
            "k_factor": self.k_factor, "thickness": self.thickness,
            "segments": [[list(a), list(b)] for a, b in self.segments],
        }

    @classmethod
    def from_dict(cls, d):
        return cls(d["direction"], d["angle"], d["inside_radius"], d["allowance"], d["k_factor"],
                   d["thickness"], [(Vector(*a), Vector(*b)) for a, b in d["segments"]])

    def __repr__(self):
        return "<FlatBend %s>" % self.note


def bends_to_json(bends, normal):
    return json.dumps({"version": 1, "normal": list(normal),
                       "bends": [b.to_dict() for b in bends]}, sort_keys=True)


def bends_from_json(text):
    """(bends, normal) from the JSON stored on the Unfold object; ([], None) when empty."""
    if not text:
        return [], None
    data = json.loads(text)
    return [FlatBend.from_dict(b) for b in data["bends"]], Vector(*data["normal"])


###################################################################################################
# 2D frame and geometry
###################################################################################################

class Frame2D:
    """Maps 3D points on the flat pattern to 2D DXF coordinates."""

    def __init__(self, normal, origin=None, ex=None):
        n = Vector(normal)
        n.normalize()
        if ex is None:
            ex = Vector(1, 0, 0)
            if abs(ex.dot(n)) > 1.0 - 1e-6:
                ex = Vector(0, 1, 0)
        ex = ex - n * ex.dot(n)
        ex.normalize()
        self.normal, self.ex = n, ex
        self.ey = n.cross(ex)
        self.origin = Vector(0, 0, 0) if origin is None else Vector(origin)
        self.shift = (0.0, 0.0)

    def point(self, p):
        d = Vector(p) - self.origin
        return (d.dot(self.ex) + self.shift[0], d.dot(self.ey) + self.shift[1])


def _planar_faces(shape, normal):
    faces = [f for f in shape.Faces
             if f.Surface.TypeId == "Part::GeomPlane" and f.normalAt(0, 0).dot(normal) > 1 - 1e-5]
    if not faces:
        raise ValueError("The flat pattern has no face with the sketch normal.")
    if len(faces) > 1:
        try:
            fused = faces[0].fuse(faces[1:]).removeSplitter()
            if fused.Faces:
                faces = list(fused.Faces)
        except Exception:  # keep the separate faces
            pass
    return faces


def _edge_entity(edge, frame):
    """List of 2D entities for one edge: ("LINE", p0, p1), ("ARC", c, r, a0, a1) in degrees
    counter-clockwise, ("CIRCLE", c, r)."""
    curve = edge.Curve
    if isinstance(curve, Part.Line):
        return [("LINE", frame.point(edge.Vertexes[0].Point), frame.point(edge.Vertexes[-1].Point))]
    if isinstance(curve, Part.Circle):
        c = frame.point(curve.Center)
        r = curve.Radius
        if edge.isClosed() or abs((edge.LastParameter - edge.FirstParameter) - 2 * math.pi) < 1e-9:
            return [("CIRCLE", c, r)]
        p0 = frame.point(edge.valueAt(edge.FirstParameter))
        p1 = frame.point(edge.valueAt(edge.LastParameter))
        pm = frame.point(edge.valueAt(0.5 * (edge.FirstParameter + edge.LastParameter)))

        def ang(p):
            return math.atan2(p[1] - c[1], p[0] - c[0]) % (2 * math.pi)

        a0, a1, am = ang(p0), ang(p1), ang(pm)
        if (am - a0) % (2 * math.pi) > (a1 - a0) % (2 * math.pi):  # clockwise: swap the ends
            a0, a1 = a1, a0
        return [("ARC", c, r, math.degrees(a0), math.degrees(a1))]
    pts = edge.discretize(Deflection=DISCRETIZE_DEFLECTION)
    return [("LINE", frame.point(a), frame.point(b)) for a, b in zip(pts, pts[1:])]


def _entity_bounds(ent):
    kind = ent[0]
    if kind == "LINE":
        xs, ys = [ent[1][0], ent[2][0]], [ent[1][1], ent[2][1]]
    elif kind == "CIRCLE":
        (cx, cy), r = ent[1], ent[2]
        xs, ys = [cx - r, cx + r], [cy - r, cy + r]
    else:
        (cx, cy), r, a0, a1 = ent[1], ent[2], ent[3], ent[4]
        pts = [(a0 % 360.0), (a1 % 360.0)]
        span = (a1 - a0) % 360.0
        for cardinal in (0.0, 90.0, 180.0, 270.0):
            if (cardinal - a0) % 360.0 <= span:
                pts.append(cardinal)
        xs = [cx + r * math.cos(math.radians(a)) for a in pts]
        ys = [cy + r * math.sin(math.radians(a)) for a in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _clean(x):
    x = round(x, 9)
    return 0.0 if x == 0 else x


def _canonical(ent):
    """Entity in canonical form (rounded to nine decimals, lines with the lower end first)."""
    kind = ent[0]
    if kind == "LINE":
        a = (_clean(ent[1][0]), _clean(ent[1][1]))
        b = (_clean(ent[2][0]), _clean(ent[2][1]))
        a, b = sorted([a, b], key=lambda p: (round(p[0], ROUND), round(p[1], ROUND)))
        return ("LINE", a, b)
    if kind == "CIRCLE":
        return ("CIRCLE", (_clean(ent[1][0]), _clean(ent[1][1])), _clean(ent[2]))
    return ("ARC", (_clean(ent[1][0]), _clean(ent[1][1])), _clean(ent[2]),
            _clean(ent[3] % 360.0), _clean(ent[4] % 360.0))


def _sort_key(ent):
    def rnd(v):
        if isinstance(v, tuple):
            return tuple(rnd(x) for x in v)
        return round(v, ROUND) if isinstance(v, float) else v
    return tuple(rnd(v) for v in ent)


###################################################################################################
# DXF R12 writer
###################################################################################################

def _num(x):
    return "%.9f" % _clean(x)


class _Dxf:
    def __init__(self):
        self.lines = []

    def add(self, code, value):
        self.lines.append("%3d" % code)
        self.lines.append(str(value))

    def text(self):
        return "\n".join(self.lines) + "\n"


def _write_dxf(layers_entities, texts, extents):
    d = _Dxf()
    d.add(0, "SECTION")
    d.add(2, "HEADER")
    d.add(9, "$ACADVER")
    d.add(1, "AC1009")
    d.add(9, "$EXTMIN")
    d.add(10, _num(extents[0]))
    d.add(20, _num(extents[1]))
    d.add(30, _num(0.0))
    d.add(9, "$EXTMAX")
    d.add(10, _num(extents[2]))
    d.add(20, _num(extents[3]))
    d.add(30, _num(0.0))
    d.add(0, "ENDSEC")

    d.add(0, "SECTION")
    d.add(2, "TABLES")
    d.add(0, "TABLE")
    d.add(2, "LTYPE")
    d.add(70, 2)
    d.add(0, "LTYPE")
    d.add(2, "CONTINUOUS")
    d.add(70, 0)
    d.add(3, "Solid line")
    d.add(72, 65)
    d.add(73, 0)
    d.add(40, "0.0")
    d.add(0, "LTYPE")
    d.add(2, "DASHED")
    d.add(70, 0)
    d.add(3, "Dashed line")
    d.add(72, 65)
    d.add(73, 2)
    d.add(40, "6.0")
    d.add(49, "4.0")
    d.add(49, "-2.0")
    d.add(0, "ENDTAB")
    d.add(0, "TABLE")
    d.add(2, "LAYER")
    d.add(70, len(LAYERS))
    for name, colour, linetype in LAYERS:
        d.add(0, "LAYER")
        d.add(2, name)
        d.add(70, 0)
        d.add(62, colour)
        d.add(6, linetype)
    d.add(0, "ENDTAB")
    d.add(0, "ENDSEC")

    d.add(0, "SECTION")
    d.add(2, "ENTITIES")
    for layer, _c, _l in LAYERS:
        for ent in layers_entities.get(layer, []):
            kind = ent[0]
            d.add(0, kind)
            d.add(8, layer)
            if kind == "LINE":
                d.add(10, _num(ent[1][0]))
                d.add(20, _num(ent[1][1]))
                d.add(30, _num(0.0))
                d.add(11, _num(ent[2][0]))
                d.add(21, _num(ent[2][1]))
                d.add(31, _num(0.0))
            else:
                d.add(10, _num(ent[1][0]))
                d.add(20, _num(ent[1][1]))
                d.add(30, _num(0.0))
                d.add(40, _num(ent[2]))
                if kind == "ARC":
                    d.add(50, _num(ent[3]))
                    d.add(51, _num(ent[4]))
        if layer == "NOTES":
            for (x, y), height, rotation, string in texts:
                d.add(0, "TEXT")
                d.add(8, "NOTES")
                d.add(10, _num(x))
                d.add(20, _num(y))
                d.add(30, _num(0.0))
                d.add(40, _num(height))
                d.add(1, string.replace("°", "%%d"))
                d.add(50, _num(rotation))
                d.add(72, 1)
                d.add(11, _num(x))
                d.add(21, _num(y))
                d.add(31, _num(0.0))
                d.add(73, 2)
    d.add(0, "ENDSEC")
    d.add(0, "EOF")
    return d.text()


def flat_dxf_text(shape, normal, bends, text_height=2.5, notes=True):
    """DXF R12 text of a flat pattern.

    shape   the unfolded solid (SMUnfold's Shape); normal the sketch-plane normal; bends a
    list of FlatBend in the same coordinates as `shape`.
    """
    n = Vector(normal)
    n.normalize()
    frame = Frame2D(n)
    faces = _planar_faces(shape, n)
    outline, inner = [], []
    for face in faces:
        for wire in face.Wires:
            target = outline if wire.isSame(face.OuterWire) else inner
            for edge in wire.Edges:
                target.extend(_edge_entity(edge, frame))
    if not outline:
        raise ValueError("The flat pattern has no outline.")
    bounds = [_entity_bounds(e) for e in outline]
    frame.shift = (-min(b[0] for b in bounds), -min(b[1] for b in bounds))
    # Re-derive in the shifted frame (the shift is a pure translation of every entity).
    sx, sy = frame.shift

    def moved(ent):
        kind = ent[0]
        if kind == "LINE":
            return (kind, (ent[1][0] + sx, ent[1][1] + sy), (ent[2][0] + sx, ent[2][1] + sy))
        return (kind, (ent[1][0] + sx, ent[1][1] + sy)) + tuple(ent[2:])

    layers = {"OUTLINE": [moved(e) for e in outline], "INNER": [moved(e) for e in inner]}
    texts = []
    for bend in bends:
        layer = "BEND_UP" if bend.direction.upper() == "UP" else "BEND_DOWN"
        for a, b in bend.segments:
            pa, pb = frame.point(a), frame.point(b)
            if math.hypot(pb[0] - pa[0], pb[1] - pa[1]) > 1e-9:
                layers.setdefault(layer, []).append(("LINE", pa, pb))
        if notes and bend.segments:
            a, b = bend._longest()
            pa, pb = frame.point(a), frame.point(b)
            dx, dy = pb[0] - pa[0], pb[1] - pa[1]
            length = math.hypot(dx, dy)
            if length > 1e-9:
                ux, uy = dx / length, dy / length
                rotation = math.degrees(math.atan2(uy, ux))
                if rotation > 90.0 + 1e-9 or rotation <= -90.0 + 1e-9:
                    rotation = (rotation + 180.0) % 360.0
                    if rotation > 180.0:
                        rotation -= 360.0
                    ux, uy = -ux, -uy
                mid = ((pa[0] + pb[0]) / 2.0, (pa[1] + pb[1]) / 2.0)
                pos = (mid[0] - uy * text_height, mid[1] + ux * text_height)
                texts.append((pos, text_height, rotation, bend.note))
    for layer in list(layers):
        layers[layer] = sorted((_canonical(e) for e in layers[layer]), key=_sort_key)
    texts.sort(key=lambda t: (round(t[0][0], ROUND), round(t[0][1], ROUND), t[3]))

    all_ents = [e for ents in layers.values() for e in ents]
    all_bounds = [_entity_bounds(e) for e in all_ents]
    extents = (min(b[0] for b in all_bounds), min(b[1] for b in all_bounds),
               max(b[2] for b in all_bounds), max(b[3] for b in all_bounds))
    return _write_dxf(layers, texts if notes else [], extents)


def write_flat_dxf(path, shape, normal, bends, text_height=2.5, notes=True):
    text = flat_dxf_text(shape, normal, bends, text_height, notes)
    with open(path, "w", encoding="ascii", newline="\n") as fh:
        fh.write(text)
    return path


###################################################################################################
# Reading a DXF back (R12 group-code pairs); used by the tests and by anyone checking an export
###################################################################################################

def read_dxf_entities(path):
    """Entities of the ENTITIES section as dicts: type, layer, and the group codes by number
    (floats for numeric codes, strings otherwise)."""
    with open(path, encoding="ascii") as fh:
        raw = fh.read().split("\n")
    pairs = []
    for i in range(0, len(raw) - 1, 2):
        pairs.append((int(raw[i].strip()), raw[i + 1].strip()))
    ents, current, in_entities = [], None, False
    for k, (code, value) in enumerate(pairs):
        if code == 0 and value == "SECTION":
            in_entities = pairs[k + 1] == (2, "ENTITIES")
            continue
        if code == 0 and value == "ENDSEC":
            in_entities = False
        if not in_entities:
            continue
        if code == 0:
            current = {"type": value, "codes": {}}
            ents.append(current)
        elif current is not None:
            if code == 8:
                current["layer"] = value
            elif code in (1, 2, 3, 6):
                current["codes"][code] = value
            else:
                current["codes"][code] = float(value)
    return ents
