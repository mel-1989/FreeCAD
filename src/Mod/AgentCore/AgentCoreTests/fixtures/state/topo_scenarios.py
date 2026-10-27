# SPDX-License-Identifier: LGPL-2.1-or-later
"""Toponaming edit scripts for AgentCore.state.track_references.

Each scenario builds a model, names one tracked reference (the target), makes
an upstream edit and recomputes. The oracle is geometric and independent of
the tracker: ``pred(element)`` says whether an element is the one the
reference meant. A run passes when every reference ends ``kept``,
``remapped`` or ``lost``, a ``kept`` target still satisfies ``pred``, a
``remapped`` target's new element satisfies ``pred``, and the outcome is in
``expect``. A tracker that reports ``kept`` while FreeCAD silently moved the
reference onto another element fails the ``pred`` check.
"""

import FreeCAD as App
import Part
import Sketcher

from builders import V, edge_index, face_index, polygon, rect_sketch, top_face

ANY = {"kept", "remapped", "lost"}
TOL = 1e-6


def _plane_normal(f):
    u0, u1, v0, v1 = f.ParameterRange
    return f.normalAt((u0 + u1) / 2, (v0 + v1) / 2)


def is_plane(f, n=None):
    if f.Surface.TypeId != "Part::GeomPlane":
        return False
    return n is None or (_plane_normal(f) - n).Length < 1e-6


# --- base models --------------------------------------------------------------

def base_pad(doc, w=30, h=20, length=6):
    body = doc.addObject("PartDesign::Body", "Body")
    sk = rect_sketch(body, "Sketch", w, h)
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Profile = sk
    pad.Length = length
    doc.recompute()
    return body, sk, pad


def build_attach():
    """HoleSketch attached to the Pad's end cap; Pocket through all."""
    doc = App.newDocument("topo_attach")
    body, sk, pad = base_pad(doc)
    hs = body.newObject("Sketcher::SketchObject", "HoleSketch")
    hs.AttachmentSupport = (pad, [top_face(pad.Shape)])
    hs.MapMode = "FlatFace"
    hs.addGeometry(Part.Circle(V(10, 10, 0), V(0, 0, 1), 2.5))
    pocket = body.newObject("PartDesign::Pocket", "Pocket")
    pocket.Profile = hs
    pocket.Type = 1
    doc.recompute()
    return doc, ("HoleSketch", "AttachmentSupport", 0)


def pred_end_cap(doc, f):
    """The extrusion end cap: planar, normal along the extrusion and lying at
    the pad's far extent from the sketch plane."""
    if not is_plane(f):
        return False
    n = _plane_normal(f)
    if abs(abs(n.z) - 1) > 1e-6:
        return False
    bb = doc.getObject("Pad").Shape.BoundBox
    far = bb.ZMax if abs(bb.ZMax) >= abs(bb.ZMin) else bb.ZMin
    return abs(f.CenterOfMass.z - far) < TOL and n.z * far > 0


def build_fillet():
    doc, _ = build_attach()
    pocket = doc.getObject("Pocket")
    edge = edge_index(pocket.Shape, lambda e: e.Curve.TypeId == "Part::GeomLine" and abs(e.CenterOfMass.y) < TOL
                      and abs(e.CenterOfMass.z - pocket.Shape.BoundBox.ZMax) < TOL)
    fil = doc.getObject("Body").newObject("PartDesign::Fillet", "Fillet")
    fil.Base = (pocket, [edge])
    fil.Radius = 1.0
    doc.recompute()
    return doc, ("Fillet", "Base", 0)


def pred_front_top_edge(doc, e):
    """The straight top edge of the y=0 side."""
    if e.Curve.TypeId not in ("Part::GeomLine", "Part::GeomLineSegment"):
        return False
    d = e.Vertexes[-1].Point - e.Vertexes[0].Point
    zmax = doc.getObject("Pocket").Shape.BoundBox.ZMax
    return abs(d.y) < TOL and abs(d.z) < TOL and abs(e.CenterOfMass.y) < TOL and abs(e.CenterOfMass.z - zmax) < TOL


def _holder(doc, ptype="App::PropertyLinkSub"):
    h = doc.addObject("App::FeaturePython", "Holder")
    h.addProperty(ptype, "Ref", "Refs")
    return h


def build_cut():
    """Part::Cut of a cylinder from a box; Holder.Ref -> the bore face."""
    doc = App.newDocument("topo_cut")
    box = doc.addObject("Part::Box", "Box")
    box.Length, box.Width, box.Height = 40, 25, 10
    cyl = doc.addObject("Part::Cylinder", "Cyl")
    cyl.Radius, cyl.Height = 4, 30
    cyl.Placement.Base = V(20, 12.5, -5)
    cut = doc.addObject("Part::Cut", "Cut")
    cut.Base, cut.Tool = box, cyl
    doc.recompute()
    h = _holder(doc)
    h.Ref = (cut, [face_index(cut.Shape, lambda f: f.Surface.TypeId == "Part::GeomCylinder")])
    doc.recompute()
    return doc, ("Holder", "Ref", 0)


def pred_bore(doc, f):
    return f.Surface.TypeId == "Part::GeomCylinder"


def build_box():
    """Holder.Ref -> top face of a Part::Box (no element map: signature path)."""
    doc = App.newDocument("topo_box")
    box = doc.addObject("Part::Box", "Box")
    box.Length, box.Width, box.Height = 10, 8, 6
    doc.recompute()
    h = _holder(doc)
    h.Ref = (box, [top_face(box.Shape)])
    doc.recompute()
    return doc, ("Holder", "Ref", 0)


def pred_box_top(doc, f):
    return is_plane(f, V(0, 0, 1)) and abs(f.CenterOfMass.z - doc.getObject("Box").Shape.BoundBox.ZMax) < TOL


def build_sides():
    """Holder.Ref (LinkSubList) -> the +X and -Y side faces of a Pad."""
    doc = App.newDocument("topo_sides")
    body, sk, pad = base_pad(doc)
    h = _holder(doc, "App::PropertyLinkSubList")
    s = pad.Shape
    px = face_index(s, lambda f: is_plane(f, V(1, 0, 0)))
    my = face_index(s, lambda f: is_plane(f, V(0, -1, 0)))
    h.Ref = [(pad, [px, my])]
    doc.recompute()
    return doc, ("Holder", "Ref", 0)


def pred_plus_x(doc, f):
    return is_plane(f, V(1, 0, 0)) and abs(f.CenterOfMass.x - doc.getObject("Pad").Shape.BoundBox.XMax) < TOL


# --- edits ------------------------------------------------------------------------

def set_pad(**kw):
    def edit(doc):
        for k, v in kw.items():
            setattr(doc.getObject("Pad"), k, v)
    return edit


def set_datum(name, value, sketch="Sketch"):
    def edit(doc):
        doc.getObject(sketch).setDatum(name, App.Units.Quantity(value, App.Units.Length))
    return edit


def _line_at(sk, pred):
    for i, g in enumerate(sk.Geometry):
        if g.TypeId == "Part::GeomLineSegment" and pred(g):
            return i
    raise LookupError("no line")


def split_line(which):
    """Replace one rectangle side by two collinear halves (adds a vertex)."""
    preds = {
        "bottom": lambda g: abs(g.StartPoint.y) < TOL and abs(g.EndPoint.y) < TOL,
        "right": lambda g: abs(g.StartPoint.x - g.EndPoint.x) < TOL and g.StartPoint.x > TOL,
    }

    def edit(doc):
        sk = doc.getObject("Sketch")
        i = _line_at(sk, preds[which])
        g = sk.Geometry[i]
        a, b = g.StartPoint, g.EndPoint
        m = (a + b) * 0.5
        sk.delGeometry(i)
        sk.addGeometry(Part.LineSegment(a, m))
        sk.addGeometry(Part.LineSegment(m, b))
    return edit


def notch_line(which):
    """Replace one side by a V notch (two non-collinear segments)."""
    def edit(doc):
        sk = doc.getObject("Sketch")
        i = _line_at(sk, lambda g: abs(g.StartPoint.x - g.EndPoint.x) < TOL and g.StartPoint.x > TOL) if which == "right" else \
            _line_at(sk, lambda g: abs(g.StartPoint.y) < TOL and abs(g.EndPoint.y) < TOL)
        g = sk.Geometry[i]
        a, b = g.StartPoint, g.EndPoint
        m = (a + b) * 0.5 + (V(-3, 0, 0) if which == "right" else V(0, 3, 0))
        sk.delGeometry(i)
        sk.addGeometry(Part.LineSegment(a, m))
        sk.addGeometry(Part.LineSegment(m, b))
    return edit


def arc_side(doc):
    """Replace the right side with an outward arc: the +X plane disappears."""
    sk = doc.getObject("Sketch")
    i = _line_at(sk, lambda g: abs(g.StartPoint.x - g.EndPoint.x) < TOL and g.StartPoint.x > TOL)
    g = sk.Geometry[i]
    a, b = g.StartPoint, g.EndPoint
    c = (a + b) * 0.5
    r = (b - a).Length / 2
    sk.delGeometry(i)
    import math
    sk.addGeometry(Part.ArcOfCircle(Part.Circle(c, V(0, 0, 1), r), -math.pi / 2, math.pi / 2))


def triangle_profile(doc):
    """Redraw the pad sketch as a triangle from scratch."""
    sk = doc.getObject("Sketch")
    sk.deleteAllGeometry()
    polygon(sk, [V(0, 0, 0), V(30, 0, 0), V(15, 20, 0)])


def new_profile(doc):
    """Point the Pad at a brand-new circular sketch."""
    body = doc.getObject("Body")
    sk = body.newObject("Sketcher::SketchObject", "Disc")
    sk.AttachmentSupport = (body.Origin.OriginFeatures[3], [""])
    sk.MapMode = "FlatFace"
    sk.addGeometry(Part.Circle(V(15, 10, 0), V(0, 0, 1), 9))
    doc.getObject("Pad").Profile = sk


def move_hole(doc):
    hs = doc.getObject("HoleSketch")
    g = hs.Geometry[0]
    hs.delGeometry(0)
    hs.addGeometry(Part.Circle(V(12, 8, 0), V(0, 0, 1), g.Radius))


def hole_radius(doc):
    hs = doc.getObject("HoleSketch")
    g = hs.Geometry[0]
    hs.delGeometry(0)
    hs.addGeometry(Part.Circle(g.Center, V(0, 0, 1), 4.0))


def pocket_blind(doc):
    p = doc.getObject("Pocket")
    p.Type = 0
    p.Length = 2


def set_attr(name, **kw):
    def edit(doc):
        o = doc.getObject(name)
        for k, v in kw.items():
            if k == "Base":
                o.Placement.Base = v
            else:
                setattr(o, k, v)
    return edit


def swap_tool_box(doc):
    t = doc.addObject("Part::Box", "ToolBox")
    t.Length, t.Width, t.Height = 6, 6, 30
    t.Placement.Base = V(17, 9.5, -5)
    doc.getObject("Cut").Tool = t


def rotate_box(doc):
    b = doc.getObject("Box")
    b.Placement = App.Placement(V(0, 0, 0), App.Rotation(V(1, 0, 0), 180))


SCENARIOS = [
    # name, build, edit, pred, expected outcomes for the target.
    # attach_pad_two_sides is a real FreeCAD silent retarget on b4fcc8e: the
    # element map keeps the end cap's name on the new -Z cap and FreeCAD moves
    # HoleSketch onto it; the tracker must report it as a remap.
    ("attach_pad_longer", build_attach, set_pad(Length=10), pred_end_cap, {"kept"}),
    ("attach_sketch_wider", build_attach, set_datum("Width", 40), pred_end_cap, {"kept"}),
    ("attach_pad_reversed", build_attach, set_pad(Reversed=True), pred_end_cap, ANY),
    ("attach_pad_two_sides", build_attach, set_pad(SideType="Two sides", Length2=3), pred_end_cap, {"remapped"}),
    ("attach_pad_symmetric", build_attach, set_pad(SideType="Symmetric"), pred_end_cap, ANY),
    ("attach_split_bottom_line", build_attach, split_line("bottom"), pred_end_cap, {"remapped", "lost"}),
    ("attach_triangle_profile", build_attach, triangle_profile, pred_end_cap, ANY),
    ("attach_new_profile", build_attach, new_profile, pred_end_cap, ANY),
    ("fillet_pad_longer", build_fillet, set_pad(Length=9), pred_front_top_edge, {"kept"}),
    ("fillet_sketch_wider", build_fillet, set_datum("Width", 36), pred_front_top_edge, ANY),
    ("fillet_hole_radius", build_fillet, hole_radius, pred_front_top_edge, {"kept"}),
    ("fillet_move_hole", build_fillet, move_hole, pred_front_top_edge, {"kept"}),
    ("fillet_split_bottom_line", build_fillet, split_line("bottom"), pred_front_top_edge, ANY),
    ("fillet_pocket_blind", build_fillet, pocket_blind, pred_front_top_edge, ANY),
    ("fillet_notch_bottom_line", build_fillet, notch_line("bottom"), pred_front_top_edge, ANY),
    ("cut_bore_radius", build_cut, set_attr("Cyl", Radius=6), pred_bore, {"kept", "remapped"}),
    ("cut_box_longer", build_cut, set_attr("Box", Length=60), pred_bore, {"kept", "remapped"}),
    ("cut_tool_outside", build_cut, set_attr("Cyl", Base=V(200, 200, -5)), pred_bore, {"lost"}),
    ("cut_tool_box", build_cut, swap_tool_box, pred_bore, {"lost"}),
    ("box_taller", build_box, set_attr("Box", Height=9), pred_box_top, {"kept"}),
    ("box_longer", build_box, set_attr("Box", Length=25), pred_box_top, {"kept"}),
    ("box_flipped", build_box, rotate_box, pred_box_top, {"remapped", "lost"}),
    ("sides_sketch_wider", build_sides, set_datum("Width", 45), pred_plus_x, {"kept", "remapped"}),
    ("sides_split_right_line", build_sides, split_line("right"), pred_plus_x, ANY),
    ("sides_notch_right_line", build_sides, notch_line("right"), pred_plus_x, {"lost", "remapped"}),
    ("sides_arc_right_side", build_sides, arc_side, pred_plus_x, {"lost"}),
]
