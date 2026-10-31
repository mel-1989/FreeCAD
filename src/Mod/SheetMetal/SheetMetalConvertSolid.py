# SPDX-License-Identifier: LGPL-2.1-or-later
"""Convert a prismatic solid into a sheet-metal part: walls on the kept faces, bends on the chosen
edges, rips on every other edge where two kept faces meet.

cad-fork G07 slice 2c. Written without opening the add-on's GPL SheetMetalFromSolid.py (or any
other GPL-headed add-on file): only the public fabrication knowledge below, OCCT/Part, and the
LGPL helpers vendored beside this file.

Public references used
- Oberg, Jones, Horton, Ryffel, "Machinery's Handbook" (Industrial Press), sheet-metal chapter,
  "Bend allowance": BA = (pi / 180) * theta * (R + K * T), theta the turn angle in degrees, R the
  inside radius, T the thickness, K the neutral-axis factor.
- Wikipedia, "Bending (metalworking)", "Bend allowance" and "Outside setback": the setback from the
  mould-line intersection ("virtual sharp") to the bend tangent line is tan(theta / 2) times the
  radius of the surface the mould lines belong to: R + T on the outside, R on the inside.
- General CAD user guides on "convert to sheet metal" (fixed face, bend edges, rip edges, rip gap,
  thickness direction): the picked faces become walls, the picked edges become bends, every other
  edge between two walls is ripped open by the rip gap so the part can be flattened.

Model
- The source solid is the mould-line envelope. Kept faces are all faces except the open faces.
- Thickness inward: the source faces are the outer surfaces and the walls grow into the solid; the
  outer dimensions equal the source. Thickness outward: the source faces are the inner surfaces
  and the walls grow away from the solid; the outer dimensions are the source plus T on every kept
  side.
- A wall is its source face, shrunk edge by edge to the bend tangent line (bend edge), by the rip
  setback (rip edge) or not at all (edge shared with an open face), extruded by T.
- A bend is the annular sector of radii R and R + T between the two tangent lines, as long as the
  two walls overlap along the edge. Its volume is theta * T * (R + T / 2) * length.
- Rip: both walls stop short of the corner, each at T + gap (inward) or gap (outward) from the
  edge line, so the end face of each wall stands the gap away from the neighbouring wall. The gap
  must be greater than zero (a zero gap would weld the corner shut and the part could not unfold);
  rips are supported between perpendicular faces only.
- The bend edges must connect all kept faces without a loop, otherwise the part cannot be one
  solid that flattens. Kept faces must be planar, convex, straight-edged, with square ends at
  every bend edge.
"""

import math

import FreeCAD
import Part
from FreeCAD import Vector

import SheetMetalTools

EPS = 1e-7
ANG = 1e-6
smElementMapVersion = "sm1."


class ConvertSolidError(Exception):
    """The solid cannot be converted with the given selection and parameters."""


def _dedupe(points):
    out = []
    for p in points:
        if not out or (p - out[-1]).Length > EPS:
            out.append(p)
    if len(out) > 1 and (out[0] - out[-1]).Length <= EPS:
        out.pop()
    return out


def _clip(poly, q, m, delta):
    """Keep the part of the convex polygon with m . (p - q) >= delta (Sutherland-Hodgman)."""
    out = []
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        da, db = m.dot(a - q) - delta, m.dot(b - q) - delta
        if da >= -EPS:
            out.append(a)
        if (da > EPS and db < -EPS) or (da < -EPS and db > EPS):
            out.append(a + (b - a) * (da / (da - db)))
    return _dedupe(out)


def _area(poly, normal):
    total = Vector()
    for i in range(len(poly)):
        total = total + poly[i].cross(poly[(i + 1) % len(poly)])
    return 0.5 * total.dot(normal)


def _face_frame(solid, face, face_name):
    """(points ccw about the outward normal, outward normal, [(edge index, in-plane inward
    direction)]) of a kept face; refuses anything that is not a plain convex planar polygon."""
    if not isinstance(face.Surface, Part.Plane):
        raise ConvertSolidError("%s is not planar (%s); only planar faces can be converted"
                                % (face_name, type(face.Surface).__name__))
    if len(face.Wires) != 1:
        raise ConvertSolidError("%s has a hole; faces with holes are not supported" % face_name)
    edges = face.OuterWire.OrderedEdges
    for e in edges:
        if not isinstance(e.Curve, Part.Line):
            raise ConvertSolidError("%s has a curved edge; only straight-edged faces can be converted"
                                    % face_name)
    pts = _dedupe([v.Point for v in face.OuterWire.OrderedVertexes])
    u0, v0 = face.Surface.parameter(pts[0])
    n = face.normalAt(u0, v0)
    n.normalize()
    if _area(pts, n) < 0.0:
        pts.reverse()
    count = len(pts)
    for i in range(count):
        a, b, c = pts[i], pts[(i + 1) % count], pts[(i + 2) % count]
        if (b - a).cross(c - b).dot(n) < -1e-9:
            raise ConvertSolidError("%s is not convex; only convex faces can be converted" % face_name)
    return pts, n


def _edge_key(p, q):
    def r(v):
        return (round(v.x, 5), round(v.y, 5), round(v.z, 5))
    return frozenset((r(p), r(q)))


def _tree_check(kept, bends, names):
    parent = {f: f for f in kept}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b, _e in bends:
        ra, rb = find(a), find(b)
        if ra == rb:
            raise ConvertSolidError("the bend edges form a closed loop through %s; leave one of "
                                    "them unselected so it is ripped open" % names[a])
        parent[ra] = rb
    roots = {find(f) for f in kept}
    if len(roots) > 1:
        groups = {}
        for f in kept:
            groups.setdefault(find(f), []).append(names[f])
        raise ConvertSolidError("the kept faces are not all joined by bend edges: %s are separate "
                                "groups; select bend edges that connect them"
                                % " | ".join(", ".join(g) for g in groups.values()))


def _bend_solid(v0, u, lo, hi, mA, nA, nB, theta, radius, thickness, inward):
    """Annular-sector solid of the bend between two walls along the line v0 + u * t, t in [lo, hi].
    mA: in-plane direction into face A from the edge, nA / nB: outward normals of the two faces."""
    t = math.tan(theta / 2.0)
    r_src = radius + thickness if inward else radius
    corner = v0 + u * lo
    centre = corner + mA * (r_src * t) - nA * r_src
    bis = nA + nB
    bis.normalize()

    def on(normal, r):
        return centre + normal * r

    pa_i, pb_i = on(nA, radius), on(nB, radius)
    pa_o, pb_o = on(nA, radius + thickness), on(nB, radius + thickness)
    mid_i, mid_o = on(bis, radius), on(bis, radius + thickness)
    edges = [Part.Arc(pa_i, mid_i, pb_i).toShape(),
             Part.LineSegment(pb_i, pb_o).toShape(),
             Part.Arc(pb_o, mid_o, pa_o).toShape(),
             Part.LineSegment(pa_o, pa_i).toShape()]
    face = Part.Face(Part.Wire(Part.__sortEdges__(edges)))
    return face.extrude(u * (hi - lo))


def build_from_solid(solid, fixed_face, bend_edges, open_faces, thickness, radius, rip_gap,
                     inward=True):
    """Pure geometry. Faces and edges are 0-based indices into solid.Faces / solid.Edges.

    Returns (shape, info) with the sheet-metal solid and a dict with the bend list. Raises
    ConvertSolidError with a readable message when the selection or the numbers do not work."""
    if thickness <= 0.0:
        raise ConvertSolidError("thickness must be greater than zero, got %g" % thickness)
    if radius <= 0.0:
        raise ConvertSolidError("inside bend radius must be greater than zero (sharp bends are not "
                                "supported), got %g" % radius)
    if rip_gap <= 0.0:
        raise ConvertSolidError("rip gap must be greater than zero, got %g (a zero gap welds the "
                                "corner shut and the part could not be flattened)" % rip_gap)
    faces, edges = solid.Faces, solid.Edges
    names = ["Face%d" % (i + 1) for i in range(len(faces))]
    ename = lambda i: "Edge%d" % (i + 1)  # noqa: E731
    opens = set(open_faces)
    kept = [i for i in range(len(faces)) if i not in opens]
    if not kept:
        raise ConvertSolidError("every face is open; nothing to convert")
    if fixed_face not in kept:
        raise ConvertSolidError("the fixed face %s must be one of the kept faces (it is %s)"
                                % (names[fixed_face] if 0 <= fixed_face < len(faces) else "?",
                                   "open" if fixed_face in opens else "not a face of the solid"))
    edge_by_key = {_edge_key(e.Vertexes[0].Point, e.Vertexes[-1].Point): i for i, e in enumerate(edges)}
    owners = {}
    for fi, f in enumerate(faces):
        for e in f.Edges:
            idx = edge_by_key.get(_edge_key(e.Vertexes[0].Point, e.Vertexes[-1].Point))
            if idx is not None:
                owners.setdefault(idx, []).append(fi)

    frames = {}
    for fi in kept:
        pts, n = _face_frame(solid, faces[fi], names[fi])
        frames[fi] = (pts, n)

    # edge classification
    bend_set = set(bend_edges)
    for ei in bend_set:
        if not 0 <= ei < len(edges):
            raise ConvertSolidError("bend edge index %d is not an edge of the solid" % ei)
        fs = owners.get(ei, [])
        if len(fs) != 2 or any(f not in frames for f in fs):
            raise ConvertSolidError("bend edge %s is not shared by two kept faces (it borders %s)"
                                    % (ename(ei), ", ".join(names[f] + (" (open)" if f in opens else "")
                                                            for f in fs) or "no face"))
    info_edges = {}  # edge index -> dict(faces, theta, kind)
    for ei, fs in owners.items():
        if len(fs) == 2 and fs[0] in frames and fs[1] in frames:
            a, b = fs
            seg = edges[ei]
            p0, p1 = seg.Vertexes[0].Point, seg.Vertexes[-1].Point
            ia = _inward_dir(frames[a], p0, p1)
            nb = frames[b][1]
            theta = math.atan2(-nb.dot(ia), nb.dot(frames[a][1]))
            kind = "bend" if ei in bend_set else "rip"
            if not ANG < theta < math.pi - ANG:
                raise ConvertSolidError("%s and %s meet at edge %s with %s dihedral; only convex "
                                        "edges can be bent or ripped"
                                        % (names[a], names[b], ename(ei),
                                           "a concave" if theta < 0 else "a flat"))
            if kind == "rip" and abs(theta - math.pi / 2.0) > 1e-4:
                raise ConvertSolidError("edge %s between %s and %s is not square (%.2f degrees); only "
                                        "square corners can be ripped, select it as a bend edge"
                                        % (ename(ei), names[a], names[b], math.degrees(theta)))
            info_edges[ei] = dict(faces=(a, b), theta=theta, kind=kind, p0=p0, p1=p1)
    bends = [(v["faces"][0], v["faces"][1], ei) for ei, v in info_edges.items() if v["kind"] == "bend"]
    _tree_check(kept, bends, names)

    r_src = radius + thickness if inward else radius
    rip_setback = thickness + rip_gap if inward else rip_gap

    # walls: clip each face to its setbacks
    walls, polys = [], {}
    for fi in kept:
        pts, n = frames[fi]
        poly = list(pts)
        constraints = []  # (edge index or None, q, m, delta)
        count = len(pts)
        for i in range(count):
            a, b = pts[i], pts[(i + 1) % count]
            m = n.cross((b - a).normalize())
            ei = edge_by_key.get(_edge_key(a, b))
            info = info_edges.get(ei)
            if info is None:
                delta = 0.0
            elif info["kind"] == "bend":
                delta = r_src * math.tan(info["theta"] / 2.0)
            else:
                delta = rip_setback
            constraints.append((ei, a, m, delta))
            if delta > 0.0:
                poly = _clip(poly, a, m, delta)
                if len(poly) < 3:
                    break
        if len(poly) < 3 or _area(poly, n) < 1e-9:
            raise ConvertSolidError("%s is too small for the bend radius %g, thickness %g and rip gap "
                                    "%g: nothing is left of the wall" % (names[fi], radius, thickness, rip_gap))
        polys[fi] = (poly, n, constraints)
        outline = Part.makePolygon(poly + [poly[0]])
        wall = Part.Face(outline).extrude(n * (-thickness if inward else thickness))
        walls.append(wall)

    # bends: along the overlap of the two trimmed walls
    bend_solids, bend_info = [], []
    for a, b, ei in bends:
        info = info_edges[ei]
        v0, v1 = info["p0"], info["p1"]
        u = v1 - v0
        u.normalize()
        theta = info["theta"]
        ranges = []
        for fi in (a, b):
            poly, n, constraints = polys[fi]
            cons = [c for c in constraints if c[0] == ei][0]
            _ei, q, m, delta = cons
            on = [k for k, p in enumerate(poly) if abs(m.dot(p - q) - delta) < 1e-6]
            seg = None
            for k in range(len(poly)):
                k2 = (k + 1) % len(poly)
                if k in on and k2 in on and (poly[k2] - poly[k]).Length > 1e-6:
                    seg = (k, k2)
            if seg is None:
                raise ConvertSolidError("the bend radius %g leaves no flat on %s next to bend edge %s; "
                                        "reduce the radius or the thickness" % (radius, names[fi], ename(ei)))
            k, k2 = seg
            for (i0, i1) in ((k - 1, k), (k2, (k2 + 1) % len(poly))):
                d = poly[i1 % len(poly)] - poly[i0 % len(poly)]
                if d.Length > EPS and abs(d.dot(u)) / d.Length > 1e-6:
                    raise ConvertSolidError("%s is not square to bend edge %s; faces with slanted ends "
                                            "at a bend are not supported" % (names[fi], ename(ei)))
            ts = sorted([(poly[k] - v0).dot(u), (poly[k2] - v0).dot(u)])
            ranges.append(ts)
        lo, hi = max(r[0] for r in ranges), min(r[1] for r in ranges)
        if hi - lo <= 1e-6:
            raise ConvertSolidError("bend edge %s has no length left after the rip setbacks of the "
                                    "neighbouring edges; reduce the rip gap or the radius" % ename(ei))
        mA = polys[a][2]
        mA = [c for c in mA if c[0] == ei][0][2]
        bend_solids.append(_bend_solid(v0, u, lo, hi, mA, frames[a][1], frames[b][1], theta, radius,
                                       thickness, inward))
        bend_info.append(dict(edge=ename(ei), faces=(names[a], names[b]), angle=math.degrees(theta),
                              length=hi - lo))

    parts = walls + bend_solids
    expected = sum(p.Volume for p in parts)
    fused = parts[0] if len(parts) == 1 else parts[0].multiFuse(parts[1:])
    fused = fused.removeSplitter()
    solids = fused.Solids
    if not fused.isValid() or len(solids) != 1:
        raise ConvertSolidError("the walls and bends do not join into one valid solid (got %d "
                                "solids); check the thickness and radius against the solid's size"
                                % len(solids))
    if abs(fused.Volume - expected) > 1e-6 * expected:
        raise ConvertSolidError("walls overlap each other (%.6g mm3 of overlap): the thickness %g, "
                                "radius %g or rip gap %g is too large for this solid"
                                % (expected - fused.Volume, thickness, radius, rip_gap))
    return solids[0], dict(bends=bend_info, walls=len(walls))


def _inward_dir(frame, p0, p1):
    """In-plane direction from the edge p0-p1 into the face described by frame (points, normal)."""
    pts, n = frame
    d = p1 - p0
    d.normalize()
    m = n.cross(d)
    centre = Vector()
    for p in pts:
        centre = centre + p
    centre = centre * (1.0 / len(pts))
    if m.dot(centre - p0) < 0.0:
        m = m * -1.0
    return m


def _to_solid_indices(shape, solid, names, kind):
    pool = solid.Faces if kind == "Face" else solid.Edges
    out = []
    for name in names:
        short = SheetMetalTools.getElementFromTNP(name)
        if not short.startswith(kind):
            raise ConvertSolidError("%s is not a %s" % (name, kind.lower()))
        try:
            sub = shape.getElement(short)
        except Exception:
            raise ConvertSolidError("%s does not exist on the source solid" % name)
        hits = [i for i, s in enumerate(pool) if s.isSame(sub)]
        if not hits:
            raise ConvertSolidError("%s does not belong to the source solid" % name)
        out.append(hits[0])
    return out


def source_solid(shape):
    if shape.ShapeType == "Solid":
        return shape
    if len(shape.Solids) == 1:
        return shape.Solids[0]
    raise ConvertSolidError("the source must be exactly one solid (it has %d)" % len(shape.Solids))


class SMConvertSolid:
    def __init__(self, obj, selobj, fixed_face, bend_edges, open_faces):
        """Convert `selobj`'s solid: `fixed_face` (e.g. 'Face1'), `bend_edges` and `open_faces`
        are lists of sub-element names of the source."""
        self.addVerifyProperties(obj)
        obj.addProperty("App::PropertyLinkSub", "baseObject", "Parameters",
                        "Source solid and its fixed face").baseObject = (selobj, [fixed_face])
        obj.addProperty("App::PropertyLinkSub", "bendEdges", "Parameters",
                        "Edges of the source solid that become bends").bendEdges = (selobj, list(bend_edges))
        obj.addProperty("App::PropertyLinkSub", "openFaces", "Parameters",
                        "Faces of the source solid that get no wall").openFaces = (selobj, list(open_faces))
        obj.Proxy = self

    def addVerifyProperties(self, obj):
        tr = FreeCAD.Qt.translate
        SheetMetalTools.smAddLengthProperty(obj, "thickness", tr("App::Property", "Sheet thickness"), 1.0)
        SheetMetalTools.smAddLengthProperty(obj, "radius", tr("App::Property", "Inside bend radius"), 1.0)
        SheetMetalTools.smAddFloatProperty(obj, "kfactor", tr("App::Property", "K-factor (ANSI)"), 0.4)
        SheetMetalTools.smAddLengthProperty(obj, "ripGap", tr("App::Property", "Gap left at ripped corners"), 1.0)
        SheetMetalTools.smAddEnumProperty(obj, "thicknessSide", "Thickness direction",
                                          ["Inward", "Outward"], "Inward")
        SheetMetalTools.smAddIntProperty(obj, "bendCount", tr("App::Property", "Number of bends made"), 0)
        obj.setEditorMode("bendCount", 1)

    def getElementMapVersion(self, _fp, ver, _prop, restored):
        return smElementMapVersion + ver if not restored else None

    def execute(self, fp):
        self.addVerifyProperties(fp)
        src = fp.baseObject[0]
        try:
            shape = src.Shape
            solid = source_solid(shape)
            fixed = _to_solid_indices(shape, solid, fp.baseObject[1], "Face")
            if len(fixed) != 1:
                raise ConvertSolidError("select exactly one fixed face")
            bends = _to_solid_indices(shape, solid, fp.bendEdges[1], "Edge")
            opens = _to_solid_indices(shape, solid, fp.openFaces[1], "Face")
            result, info = build_from_solid(solid, fixed[0], bends, opens, fp.thickness.Value,
                                            fp.radius.Value, fp.ripGap.Value,
                                            str(fp.thicknessSide) == "Inward")
        except ConvertSolidError as exc:
            raise FreeCAD.Base.FreeCADError("Convert to sheet metal: %s" % exc)
        fp.bendCount = len(info["bends"])
        fp.Shape = result


def make_convert_solid(doc, src_obj, fixed_face, bend_edges, open_faces=(), name="ConvertSolid",
                       **props):
    """Create a recomputed convert-from-solid feature; props set the feature's properties."""
    obj = doc.addObject("Part::FeaturePython", name)
    SMConvertSolid(obj, src_obj, fixed_face, list(bend_edges), list(open_faces))
    for key, value in props.items():
        setattr(obj, key, value)
    doc.recompute()
    return obj
