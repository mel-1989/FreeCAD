# SPDX-License-Identifier: LGPL-2.1-or-later
"""Fillet weld bead feature and weld summary (cad-fork G07 slice 5b).

A ``Weldment::WeldBead`` (Part::FeaturePython) models a fillet weld along straight edges where two
bodies meet. It is a *cosmetic* solid: a compound of one prism per edge, never fused into either
body, so the members' own shapes and volumes are untouched.

Definitions (public)
--------------------
* ISO 2553:2019 (*Welding and allied processes - Symbolic representation on drawings - Welded
  joints*), fillet weld dimensions: ``z`` is the leg length, the side of the largest isosceles
  right triangle that can be inscribed in the weld's cross-section, measured along each fused
  face; ``a`` is the throat thickness, the height of the largest triangle that can be inscribed
  (measured from the root perpendicular to the hypotenuse). For an equal-leg fillet on a 90 degree
  corner ``a = z / sqrt(2)``, hence ``z = a * sqrt(2)``.
* AWS A2.4 (*Standard Symbols for Welding, Brazing, and Nondestructive Examination*): the fillet
  weld "size" is the leg length of the largest right triangle inscribed in the cross-section and
  the effective throat is ``0.707 x size`` for an equal-leg weld on a 90 degree joint; both are
  the same quantities as ISO's ``z`` and ``a``.

Geometry
--------
The cross-section is the right isosceles triangle with the right angle at the weld root (the
edge), legs ``z`` along the two faces. Volume = ``z**2 / 2 * L`` per straight edge of length ``L``.
Prisms are square-ended and unfused (one per edge), so a closed outline of edges is the sum of its
edges' prisms and neighbouring prisms leave the small corner notch a real weld would round off.
Only straight edges and planar faces meeting at exactly 90 degrees are supported (checked to
1e-9 on the face normals); any other dihedral angle is a readable refusal rather than a guess
at the leg convention.

Refusals (``WeldmentError``): an edge that does not lie on a planar face of both bodies, faces that
are coplanar or not perpendicular, a curved edge, a leg that runs past either face, a size that is
not positive, no edges.

Public sources (no software read): ISO 2553 and AWS A2.4 as above; right-triangle prism volume is
elementary. Clean room: no FrameForge, old Weldment add-on or other weldment workbench source was
opened.
"""

import math

import FreeCAD
import Part

from WeldGeom import WeldmentError

V = FreeCAD.Vector

CLASS_NAME = "Weldment::WeldBead"
MODES = ["Leg z", "Throat a"]
SQRT2 = math.sqrt(2.0)
TOL_ON = 1e-6        # an edge lies on a face when its samples are within this distance (mm)
TOL_PERP = 1e-9      # |n1 . n2| bound for perpendicular faces
STEEL_DENSITY = 7850.0   # kg/m^3, default for weld metal mass


def leg_from_throat(a):
    return a * SQRT2


def throat_from_leg(z):
    return z / SQRT2


def _solids(shape):
    return shape.Solids if shape.Solids else [shape]


def _inside(shape, pt, tol=1e-7):
    return any(s.isInside(pt, tol, True) for s in _solids(shape))


def _edges_of(links):
    out = []
    for entry in links:
        obj, subs = entry[0], entry[1]
        if isinstance(subs, str):
            subs = [subs]
        if subs and any(subs):
            for sub in subs:
                if not sub:
                    continue
                if not sub.startswith("Edge"):
                    raise WeldmentError("weld edge %s.%s is not an edge" % (obj.Name, sub))
                out.append(("%s.%s" % (obj.Name, sub), obj.Shape.getElement(sub)))
        else:
            out.extend(("%s.Edge%d" % (obj.Name, i + 1), e) for i, e in enumerate(obj.Shape.Edges))
    return out


def _samples(p0, p1, n=5):
    return [p0 + (p1 - p0) * (i / float(n - 1)) for i in range(n)]


def _on_face(face, pts):
    return all(Part.Vertex(p).distToShape(face)[0] <= TOL_ON for p in pts)


def _face_normal(face, p):
    u, v = face.Surface.parameter(p)
    return face.normalAt(u, v)


def _planar_faces_with(shape, pts):
    return [f for f in shape.Faces if isinstance(f.Surface, Part.Plane) and _on_face(f, pts)]


def bead_prism(name, edge, body1, body2, z):
    """(prism solid, edge length) for one edge, or WeldmentError."""
    p0 = edge.valueAt(edge.FirstParameter)
    p1 = edge.valueAt(edge.LastParameter)
    length = (p1 - p0).Length
    if abs(edge.Length - length) > 1e-7 * max(1.0, length):
        raise WeldmentError("weld edge %s is curved; beads follow straight edges only" % name)
    if length < 1e-6:
        raise WeldmentError("weld edge %s has zero length" % name)
    t = (p1 - p0)
    t.normalize()
    pts = _samples(p0, p1)
    f1s = _planar_faces_with(body1, pts)
    f2s = _planar_faces_with(body2, pts)
    if not f1s or not f2s:
        raise WeldmentError(
            "weld edge %s does not lie on a planar face of %s: it must lie on both bodies (within %g mm)"
            % (name, "both bodies" if not f1s and not f2s else ("the first body" if not f1s else "the second body"),
               TOL_ON)
        )
    mid = (p0 + p1) * 0.5
    angles = []
    perpendicular = False
    eps = min(1e-3, z * 0.01)
    for f1 in f1s:
        n1 = _face_normal(f1, mid)
        for f2 in f2s:
            n2 = _face_normal(f2, mid)
            dot = n1.dot(n2)
            if abs(dot) > TOL_PERP:
                angles.append(math.degrees(math.acos(max(-1.0, min(1.0, dot)))))
                continue
            perpendicular = True
            for s1 in (1.0, -1.0):
                d1 = n1.cross(t) * s1
                d1.normalize()
                if not f1.isInside(mid + d1 * eps, 1e-6, True) or _inside(body2, mid + d1 * eps):
                    continue
                for s2 in (1.0, -1.0):
                    d2 = n2.cross(t) * s2
                    d2.normalize()
                    if not f2.isInside(mid + d2 * eps, 1e-6, True) or _inside(body1, mid + d2 * eps):
                        continue
                    ends = [(f1, d1), (f2, d2)]
                    for q in (p0, mid, p1):
                        for f, d in ends:
                            if not f.isInside(q + d * z, 1e-6, True):
                                raise WeldmentError(
                                    "weld edge %s: a leg of %g mm runs past the end of the face it lies on; "
                                    "use a smaller size" % (name, z)
                                )
                    tri = Part.makePolygon([p0, p0 + d1 * z, p0 + d2 * z, p0])
                    solid = Part.Face(tri).extrude(p1 - p0)
                    solid = Part.Solid(solid.Shells[0]) if solid.ShapeType != "Solid" else solid
                    if solid.Volume < 0:
                        solid.reverse()
                    centre = solid.CenterOfMass
                    if _inside(body1, centre) or _inside(body2, centre):
                        continue
                    return solid, length
    if angles and not perpendicular:
        raise WeldmentError(
            "weld edge %s: the faces meet at %.6g degrees; only 90 degree fillet corners are supported"
            % (name, min(angles, key=lambda a: abs(a - 90.0)))
        )
    raise WeldmentError(
        "weld edge %s lies on both bodies but no pair of their faces forms a free corner for a fillet "
        "(coplanar faces, or the corner is filled with material)" % name
    )


class WeldBead:
    def __init__(self, obj):
        obj.Proxy = self
        obj.addProperty("App::PropertyLink", "Body1", "Weld", "First body the weld joins")
        obj.addProperty("App::PropertyLink", "Body2", "Weld", "Second body the weld joins")
        obj.addProperty("App::PropertyLinkSubList", "Edges", "Weld", "Straight edges where the bodies meet")
        obj.addProperty("App::PropertyEnumeration", "SizeMode", "Weld", "Whether Size is the leg z or the throat a")
        obj.SizeMode = MODES
        obj.SizeMode = "Leg z"
        obj.addProperty("App::PropertyLength", "Size", "Weld", "Leg z or throat a, per SizeMode (ISO 2553, AWS A2.4)")
        obj.Size = 5.0
        obj.addProperty("App::PropertyLength", "LegZ", "Result", "Leg length z (mm)")
        obj.addProperty("App::PropertyLength", "Throat", "Result", "Throat thickness a = z / sqrt 2 (mm)")
        obj.addProperty("App::PropertyLength", "Length", "Result", "Total weld length (mm)")
        obj.addProperty("App::PropertyFloatList", "EdgeLengths", "Result", "Length of each weld edge (mm)")
        for p in ("LegZ", "Throat", "Length", "EdgeLengths"):
            obj.setEditorMode(p, 1)

    def execute(self, obj):
        if obj.Body1 is None or obj.Body2 is None:
            raise WeldmentError("%s needs both bodies linked" % obj.Name)
        size = obj.Size.Value
        if not size > 0.0:
            raise WeldmentError("%s: weld size must be positive, got %g mm" % (obj.Name, size))
        z = size if obj.SizeMode == "Leg z" else leg_from_throat(size)
        edges = _edges_of(obj.Edges)
        if not edges:
            raise WeldmentError("%s has no edges; link the edges to weld" % obj.Name)
        solids, lengths = [], []
        for name, e in edges:
            s, l = bead_prism(name, e, obj.Body1.Shape, obj.Body2.Shape, z)
            solids.append(s)
            lengths.append(l)
        obj.LegZ = z
        obj.Throat = throat_from_leg(z)
        obj.EdgeLengths = lengths
        obj.Length = sum(lengths)
        obj.Shape = Part.Compound(solids)

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


def make_bead(doc, body1, body2, edges, size, mode="Leg z", name="WeldBead"):
    """Create a bead. edges: a list of (object, [sub names]) or (object, 'EdgeN') tuples."""
    obj = doc.addObject("Part::FeaturePython", name)
    WeldBead(obj)
    obj.Body1, obj.Body2 = body1, body2
    obj.Edges = [(e[0], [e[1]] if isinstance(e[1], str) else list(e[1])) for e in edges]
    obj.SizeMode = mode
    obj.Size = size
    if FreeCAD.GuiUp:
        obj.ViewObject.Proxy = 0
    return obj


def is_bead(obj):
    return obj.TypeId == "Part::FeaturePython" and "LegZ" in obj.PropertiesList and "EdgeLengths" in obj.PropertiesList


def beads_in(doc_or_objects):
    objs = doc_or_objects.Objects if hasattr(doc_or_objects, "Objects") else list(doc_or_objects)
    return [o for o in objs if is_bead(o)]


def weld_summary(doc_or_objects, density_kg_m3=STEEL_DENSITY):
    """Weld summary rows sorted by leg size: one dict per distinct leg z.

    Keys: ``leg_z_mm``, ``throat_a_mm``, ``total_length_mm`` (sum over all beads of that size),
    ``bead_count`` (bead objects), ``edge_count`` (weld edges), ``volume_mm3`` and ``mass_kg``
    (weld metal at ``density_kg_m3``). Sizes within 1e-6 mm are one size. Deterministic.
    """
    groups = []
    for b in sorted(beads_in(doc_or_objects), key=lambda o: (o.LegZ.Value, o.Name)):
        z = b.LegZ.Value
        for g in groups:
            if abs(g["leg_z_mm"] - z) <= 1e-6:
                break
        else:
            g = {"leg_z_mm": z, "throat_a_mm": throat_from_leg(z), "total_length_mm": 0.0,
                 "bead_count": 0, "edge_count": 0, "volume_mm3": 0.0}
            groups.append(g)
        g["total_length_mm"] += sum(b.EdgeLengths)
        g["bead_count"] += 1
        g["edge_count"] += len(b.EdgeLengths)
        g["volume_mm3"] += z * z / 2.0 * sum(b.EdgeLengths)
    for g in groups:
        g["mass_kg"] = g["volume_mm3"] * 1e-9 * density_kg_m3
    return sorted(groups, key=lambda g: g["leg_z_mm"])
