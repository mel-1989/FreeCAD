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

import functools

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


def build_attach(w=30, h=20, length=6, hx=10, hy=10, r=2.5):
    """HoleSketch attached to the Pad's end cap; Pocket through all."""
    doc = App.newDocument("topo_attach")
    body, sk, pad = base_pad(doc, w, h, length)
    hs = body.newObject("Sketcher::SketchObject", "HoleSketch")
    hs.AttachmentSupport = (pad, [top_face(pad.Shape)])
    hs.MapMode = "FlatFace"
    hs.addGeometry(Part.Circle(V(hx, hy, 0), V(0, 0, 1), r))
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


def build_fillet(**kw):
    """build_attach plus a Fillet on the front (y=0) top edge of the Pocket."""
    doc, _ = build_attach(**kw)
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


def build_cut(L=40, W=25, H=10, R=4):
    """Part::Cut of a cylinder from a box; Holder.Ref -> the bore face."""
    doc = App.newDocument("topo_cut")
    box = doc.addObject("Part::Box", "Box")
    box.Length, box.Width, box.Height = L, W, H
    cyl = doc.addObject("Part::Cylinder", "Cyl")
    cyl.Radius, cyl.Height = R, H + 20
    cyl.Placement.Base = V(L / 2, W / 2, -10)
    cut = doc.addObject("Part::Cut", "Cut")
    cut.Base, cut.Tool = box, cyl
    doc.recompute()
    h = _holder(doc)
    h.Ref = (cut, [face_index(cut.Shape, lambda f: f.Surface.TypeId == "Part::GeomCylinder")])
    doc.recompute()
    return doc, ("Holder", "Ref", 0)


def pred_bore(doc, f):
    return f.Surface.TypeId == "Part::GeomCylinder"


def build_box(L=10, W=8, H=6):
    """Holder.Ref -> top face of a Part::Box (no element map: signature path)."""
    doc = App.newDocument("topo_box")
    box = doc.addObject("Part::Box", "Box")
    box.Length, box.Width, box.Height = L, W, H
    doc.recompute()
    h = _holder(doc)
    h.Ref = (box, [top_face(box.Shape)])
    doc.recompute()
    return doc, ("Holder", "Ref", 0)


def pred_box_top(doc, f):
    return is_plane(f, V(0, 0, 1)) and abs(f.CenterOfMass.z - doc.getObject("Box").Shape.BoundBox.ZMax) < TOL


def build_sides(w=30, h=20, length=6):
    """Holder.Ref (LinkSubList) -> the +X and -Y side faces of a Pad."""
    doc = App.newDocument("topo_sides")
    body, sk, pad = base_pad(doc, w, h, length)
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


_SIDES = {
    "bottom": lambda g: abs(g.StartPoint.y) < TOL and abs(g.EndPoint.y) < TOL,
    "right": lambda g: abs(g.StartPoint.x - g.EndPoint.x) < TOL and g.StartPoint.x > TOL,
    "top": lambda g: abs(g.StartPoint.y - g.EndPoint.y) < TOL and g.StartPoint.y > TOL,
}


def split_line(which):
    """Replace one rectangle side by two collinear halves (adds a vertex)."""
    def edit(doc):
        sk = doc.getObject("Sketch")
        i = _line_at(sk, _SIDES[which])
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
        i = _line_at(sk, _SIDES[which])
        g = sk.Geometry[i]
        a, b = g.StartPoint, g.EndPoint
        m = (a + b) * 0.5 + {"right": V(-3, 0, 0), "bottom": V(0, 3, 0), "top": V(0, -3, 0)}[which]
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
    box = doc.getObject("Box")
    t.Length, t.Width, t.Height = 6, 6, box.Height.Value + 20
    t.Placement.Base = V(box.Length.Value / 2 - 3, box.Width.Value / 2 - 3, -10)
    doc.getObject("Cut").Tool = t


def rotate_box(doc):
    b = doc.getObject("Box")
    b.Placement = App.Placement(V(0, 0, 0), App.Rotation(V(1, 0, 0), 180))




# --- more model families -------------------------------------------------------
# Every builder takes its dimensions as keyword arguments, so each scenario
# below runs on its own model (family + parameters); model_key() names it.

def _plain_sketch(doc, name, pts, body=None, plane=3):
    """Unconstrained closed polygon sketch, in ``body`` when given."""
    if body is not None:
        sk = body.newObject("Sketcher::SketchObject", name)
        sk.AttachmentSupport = (body.Origin.OriginFeatures[plane], [""])
        sk.MapMode = "FlatFace"
    else:
        sk = doc.addObject("Sketcher::SketchObject", name)
    polygon(sk, pts, constrain=False)
    return sk


def _line_dir(e):
    d = e.Vertexes[-1].Point - e.Vertexes[0].Point
    return d * (1.0 / d.Length) if d.Length > 0 else d


def is_line(e, axis=None):
    if e.Curve.TypeId not in ("Part::GeomLine", "Part::GeomLineSegment"):
        return False
    if axis is None:
        return True
    d = _line_dir(e)
    return abs(abs(d.x) - abs(axis.x)) < 1e-6 and abs(abs(d.y) - abs(axis.y)) < 1e-6 and abs(abs(d.z) - abs(axis.z)) < 1e-6


def build_pdchamfer(w=30, h=20, length=6, size=1.0):
    """PartDesign Chamfer on the Pad's right top edge (x=w, z=length)."""
    doc = App.newDocument("topo_chamfer")
    body, sk, pad = base_pad(doc, w, h, length)
    edge = edge_index(pad.Shape, lambda e: pred_right_top_edge(doc, e))
    ch = body.newObject("PartDesign::Chamfer", "Chamfer")
    ch.Base = (pad, [edge])
    ch.Size = size
    doc.recompute()
    return doc, ("Chamfer", "Base", 0)


def _far_z(doc):
    """z of the Pad's end cap: its extent farthest from the sketch plane."""
    bb = doc.getObject("Pad").Shape.BoundBox
    return bb.ZMax if abs(bb.ZMax) >= abs(bb.ZMin) else bb.ZMin


def pred_right_top_edge(doc, e):
    """The end cap's right edge (x = XMax, on the cap)."""
    bb = doc.getObject("Pad").Shape.BoundBox
    c = e.CenterOfMass
    return is_line(e, V(0, 1, 0)) and abs(c.x - bb.XMax) < TOL and abs(c.z - _far_z(doc)) < TOL


def build_fuse(base=(20, 10, 5), tower=(5, 10, 15), tx=0):
    """Part::Fuse of a base slab and a tower; Holder.Ref -> the tower's top."""
    doc = App.newDocument("topo_fuse")
    a = doc.addObject("Part::Box", "Slab")
    a.Length, a.Width, a.Height = base
    t = doc.addObject("Part::Box", "Tower")
    t.Length, t.Width, t.Height = tower
    t.Placement.Base = V(tx, 0, 0)
    fu = doc.addObject("Part::MultiFuse", "Fusion")
    fu.Shapes = [a, t]
    fu.Refine = True
    doc.recompute()
    h = _holder(doc)
    h.Ref = (fu, [face_index(fu.Shape, lambda f: pred_tower_top(doc, f))])
    doc.recompute()
    return doc, ("Holder", "Ref", 0)


def pred_tower_top(doc, f):
    """The tower's top: +Z plane at the tower's height, inside its footprint."""
    t = doc.getObject("Tower")
    s = doc.getObject("Slab")
    if not is_plane(f, V(0, 0, 1)) or t.Height.Value <= s.Height.Value + TOL:
        return False
    tb = t.Shape.BoundBox
    c = f.CenterOfMass
    area = t.Length.Value * t.Width.Value
    return abs(c.z - tb.ZMax) < TOL and tb.XMin - TOL <= c.x <= tb.XMax + TOL and abs(f.Area - area) < 1e-6 * max(1, area)


def build_extrude(w=12, h=8, length=5):
    """Part::Extrusion of a standalone sketch; Holder.Ref -> the end cap."""
    doc = App.newDocument("topo_extrude")
    sk = _plain_sketch(doc, "Sketch", [V(0, 0, 0), V(w, 0, 0), V(w, h, 0), V(0, h, 0)])
    ex = doc.addObject("Part::Extrusion", "Extrude")
    ex.Base = sk
    ex.DirMode = "Normal"
    ex.LengthFwd = length
    ex.Solid = True
    doc.recompute()
    h_ = _holder(doc)
    h_.Ref = (ex, [face_index(ex.Shape, lambda f: pred_extrude_cap(doc, f))])
    doc.recompute()
    return doc, ("Holder", "Ref", 0)


def pred_extrude_cap(doc, f):
    """The far cap of the extrusion: the +Z or -Z plane farthest from z=0."""
    bb = doc.getObject("Extrude").Shape.BoundBox
    far = bb.ZMax if abs(bb.ZMax) >= abs(bb.ZMin) else bb.ZMin
    return is_plane(f) and abs(abs(_plane_normal(f).z) - 1) < 1e-6 and abs(f.CenterOfMass.z - far) < TOL and abs(far) > TOL


def set_sketch_corner(dx, dy, sketch="Sketch"):
    """Move the sketch's (w, h) corner by (dx, dy) on unconstrained polygons."""
    def edit(doc):
        sk = doc.getObject(sketch)
        geo = sk.Geometry
        far = max((g.EndPoint for g in geo), key=lambda p: p.x + p.y)
        new = []
        for g in geo:
            a, b = g.StartPoint, g.EndPoint
            if (a - far).Length < TOL:
                a = a + V(dx, dy, 0)
            if (b - far).Length < TOL:
                b = b + V(dx, dy, 0)
            new.append(Part.LineSegment(a, b))
        sk.deleteAllGeometry()
        for g in new:
            sk.addGeometry(g)
    return edit


def build_revolve(r_in=5, r_out=10, height=8, angle=360):
    """PartDesign Revolution of an offset rectangle about the sketch V axis;
    Holder.Ref -> the outer cylindrical face."""
    doc = App.newDocument("topo_revolve")
    body = doc.addObject("PartDesign::Body", "Body")
    sk = _plain_sketch(doc, "Sketch", [V(r_in, 0, 0), V(r_out, 0, 0), V(r_out, height, 0), V(r_in, height, 0)], body=body)
    rv = body.newObject("PartDesign::Revolution", "Revolution")
    rv.Profile = sk
    rv.ReferenceAxis = (sk, ["V_Axis"])
    rv.Angle = angle
    doc.recompute()
    h = _holder(doc)
    h.Ref = (rv, [face_index(rv.Shape, lambda f: pred_outer_cyl(doc, f))])
    doc.recompute()
    return doc, ("Holder", "Ref", 0)


def pred_outer_cyl(doc, f):
    """The outer cylindrical wall: cylinder of the largest radius in the part."""
    s = doc.getObject("Revolution").Shape
    radii = [x.Surface.Radius for x in s.Faces if x.Surface.TypeId == "Part::GeomCylinder"]
    return f.Surface.TypeId == "Part::GeomCylinder" and bool(radii) and abs(f.Surface.Radius - max(radii)) < TOL


def set_revolve_outer(r):
    def edit(doc):
        sk = doc.getObject("Sketch")
        new = []
        for g in sk.Geometry:
            a, b = g.StartPoint, g.EndPoint
            xmax = max(x.EndPoint.x for x in sk.Geometry)
            a = V(r, a.y, 0) if abs(a.x - xmax) < TOL else a
            b = V(r, b.y, 0) if abs(b.x - xmax) < TOL else b
            new.append(Part.LineSegment(a, b))
        sk.deleteAllGeometry()
        for g in new:
            sk.addGeometry(g)
    return edit


def build_datum(w=30, h=20, length=6):
    """A PartDesign datum plane attached to the Pad's back (+Y) face."""
    doc = App.newDocument("topo_datum")
    body, sk, pad = base_pad(doc, w, h, length)
    dp = body.newObject("PartDesign::Plane", "DatumPlane")
    dp.AttachmentSupport = (pad, [face_index(pad.Shape, lambda f: pred_back_face(doc, f))])
    dp.MapMode = "FlatFace"
    doc.recompute()
    return doc, ("DatumPlane", "AttachmentSupport", 0)


def pred_back_face(doc, f):
    return is_plane(f, V(0, 1, 0)) and abs(f.CenterOfMass.y - doc.getObject("Pad").Shape.BoundBox.YMax) < TOL


def build_extgeo(w=30, h=20, length=6):
    """A second sketch whose external geometry is the Pad's back top edge."""
    doc = App.newDocument("topo_extgeo")
    body, sk, pad = base_pad(doc, w, h, length)
    sk2 = body.newObject("Sketcher::SketchObject", "Sketch2")
    sk2.AttachmentSupport = (body.Origin.OriginFeatures[3], [""])
    sk2.MapMode = "FlatFace"
    doc.recompute()
    sk2.addExternal("Pad", edge_index(pad.Shape, lambda e: pred_back_top_edge(doc, e)))
    doc.recompute()
    return doc, ("Sketch2", "ExternalGeometry", 0)


def pred_back_top_edge(doc, e):
    bb = doc.getObject("Pad").Shape.BoundBox
    c = e.CenterOfMass
    return is_line(e, V(1, 0, 0)) and abs(c.y - bb.YMax) < TOL and abs(c.z - bb.ZMax) < TOL


def build_cylinder(r=5, height=10, angle=360):
    """Holder.Ref -> top face of a Part::Cylinder."""
    doc = App.newDocument("topo_cyl")
    c = doc.addObject("Part::Cylinder", "Cylinder")
    c.Radius, c.Height, c.Angle = r, height, angle
    doc.recompute()
    h = _holder(doc)
    h.Ref = (c, [face_index(c.Shape, lambda f: pred_cyl_top(doc, f))])
    doc.recompute()
    return doc, ("Holder", "Ref", 0)


def pred_cyl_top(doc, f):
    return is_plane(f, V(0, 0, 1)) and abs(f.CenterOfMass.z - doc.getObject("Cylinder").Shape.BoundBox.ZMax) < TOL


def rotate_obj(name, axis, angle):
    def edit(doc):
        doc.getObject(name).Placement = App.Placement(V(0, 0, 0), App.Rotation(axis, angle))
    return edit


def build_common(size=20, height=10, r=8):
    """Part::Common of a box and a centred cylinder; Holder.Ref -> the
    cylindrical wall."""
    doc = App.newDocument("topo_common")
    b = doc.addObject("Part::Box", "Box")
    b.Length, b.Width, b.Height = size, size, height
    c = doc.addObject("Part::Cylinder", "Cyl")
    c.Radius, c.Height = r, height + 10
    c.Placement.Base = V(size / 2, size / 2, -5)
    cm = doc.addObject("Part::Common", "Common")
    cm.Base, cm.Tool = b, c
    doc.recompute()
    h = _holder(doc)
    h.Ref = (cm, [face_index(cm.Shape, lambda f: pred_common_wall(doc, f))])
    doc.recompute()
    return doc, ("Holder", "Ref", 0)


def pred_common_wall(doc, f):
    return f.Surface.TypeId == "Part::GeomCylinder" and abs(f.Surface.Radius - doc.getObject("Cyl").Radius.Value) < TOL


def build_pocket_floor(w=30, h=20, length=8, depth=3):
    """A blind rectangular Pocket in the Pad's end cap; Holder.Ref -> floor."""
    doc = App.newDocument("topo_floor")
    body, sk, pad = base_pad(doc, w, h, length)
    ps = body.newObject("Sketcher::SketchObject", "PocketSketch")
    ps.AttachmentSupport = (pad, [top_face(pad.Shape)])
    ps.MapMode = "FlatFace"
    polygon(ps, [V(5, 5, 0), V(15, 5, 0), V(15, 12, 0), V(5, 12, 0)], constrain=False)
    pk = body.newObject("PartDesign::Pocket", "Pocket")
    pk.Profile = ps
    pk.Length = depth
    doc.recompute()
    h_ = _holder(doc)
    h_.Ref = (pk, [face_index(pk.Shape, lambda f: pred_pocket_floor(doc, f))])
    doc.recompute()
    return doc, ("Holder", "Ref", 0)


def pred_pocket_floor(doc, f):
    """+Z plane below the top, lying inside the pocket outline."""
    pad = doc.getObject("Pad")
    pk = doc.getObject("Pocket")
    if pk.Type != "Length":
        return False
    z = pad.Shape.BoundBox.ZMax - pk.Length.Value
    c = f.CenterOfMass
    return is_plane(f, V(0, 0, 1)) and abs(c.z - z) < TOL and 5 - TOL <= c.x <= 15 + TOL and 5 - TOL <= c.y <= 12 + TOL


def build_boss(w=30, h=20, length=6, r=4, boss=5):
    """A cylindrical boss padded from a circle on the base Pad's end cap;
    Holder.Ref -> the boss's top disc."""
    doc = App.newDocument("topo_boss")
    body, sk, pad = base_pad(doc, w, h, length)
    cs = body.newObject("Sketcher::SketchObject", "BossSketch")
    cs.AttachmentSupport = (pad, [top_face(pad.Shape)])
    cs.MapMode = "FlatFace"
    cs.addGeometry(Part.Circle(V(w / 2, h / 2, 0), V(0, 0, 1), r))
    p2 = body.newObject("PartDesign::Pad", "Boss")
    p2.Profile = cs
    p2.Length = boss
    doc.recompute()
    h_ = _holder(doc)
    h_.Ref = (p2, [face_index(p2.Shape, lambda f: pred_boss_top(doc, f))])
    doc.recompute()
    return doc, ("Holder", "Ref", 0)


def pred_boss_top(doc, f):
    """The disc at the very top: +Z plane at ZMax bounded by one circle."""
    bb = doc.getObject("Boss").Shape.BoundBox
    return (is_plane(f, V(0, 0, 1)) and abs(f.CenterOfMass.z - bb.ZMax) < TOL and len(f.Edges) == 1
            and f.Edges[0].Curve.TypeId == "Part::GeomCircle")


def set_circle_radius(sketch, r):
    def edit(doc):
        sk = doc.getObject(sketch)
        g = sk.Geometry[0]
        sk.delGeometry(0)
        sk.addGeometry(Part.Circle(g.Center, V(0, 0, 1), r))
    return edit


def build_mirror(L=10, W=6, H=4, normal=(1, 0, 0)):
    """Part::Mirroring of a box; Holder.Ref -> the mirrored part's outer face
    on the far side of the mirror plane."""
    doc = App.newDocument("topo_mirror")
    b = doc.addObject("Part::Box", "Box")
    b.Length, b.Width, b.Height = L, W, H
    m = doc.addObject("Part::Mirroring", "Mirror")
    m.Source = b
    m.Normal = V(*normal)
    doc.recompute()
    h = _holder(doc)
    h.Ref = (m, [face_index(m.Shape, lambda f: pred_mirror_far(doc, f))])
    doc.recompute()
    return doc, ("Holder", "Ref", 0)


def pred_mirror_far(doc, f):
    """The image of the box's +X face under the current mirror."""
    box = doc.getObject("Box")
    m = doc.getObject("Mirror")
    src = next(x for x in box.Shape.Faces if is_plane(x, V(1, 0, 0)))
    img = src.mirror(m.Base, m.Normal)
    n, m_ = _plane_normal(img), _plane_normal(f) if is_plane(f) else None
    return m_ is not None and abs(abs(n.dot(m_)) - 1) < 1e-6 and (f.CenterOfMass - img.CenterOfMass).Length < TOL


def build_fillet2(w=30, h=20, length=6, radius=1.0):
    """PartDesign Fillet on two edges of a Pad; the target is the second
    entry of the LinkSub (the left top edge, x=0)."""
    doc = App.newDocument("topo_fillet2")
    body, sk, pad = base_pad(doc, w, h, length)
    e1 = edge_index(pad.Shape, lambda e: pred_right_top_edge(doc, e))
    e2 = edge_index(pad.Shape, lambda e: pred_left_top_edge(doc, e))
    fl = body.newObject("PartDesign::Fillet", "Fillet")
    fl.Base = (pad, [e1, e2])
    fl.Radius = radius
    doc.recompute()
    return doc, ("Fillet", "Base", 1)


def pred_left_top_edge(doc, e):
    bb = doc.getObject("Pad").Shape.BoundBox
    c = e.CenterOfMass
    return is_line(e, V(0, 1, 0)) and abs(c.x - bb.XMin) < TOL and abs(c.z - bb.ZMax) < TOL


def build_sides_back(w=30, h=20, length=6):
    """Holder.Ref (LinkSubList) -> [+X face, -Y face, +Y face] of a Pad;
    the target is entry 2 (the back face)."""
    doc = App.newDocument("topo_sides3")
    body, sk, pad = base_pad(doc, w, h, length)
    h_ = _holder(doc, "App::PropertyLinkSubList")
    s = pad.Shape
    h_.Ref = [(pad, [face_index(s, lambda f: is_plane(f, V(1, 0, 0))), face_index(s, lambda f: is_plane(f, V(0, -1, 0))),
                     face_index(s, lambda f: pred_back_face(doc, f))])]
    doc.recompute()
    return doc, ("Holder", "Ref", 2)


def scale_pad_sketch(sx, sy):
    """Scale an unconstrained-rect (rect_sketch) pad profile via its datums."""
    def edit(doc):
        sk = doc.getObject("Sketch")
        sk.setDatum("Width", App.Units.Quantity(sk.getDatum("Width").Value * sx, App.Units.Length))
        sk.setDatum("Height", App.Units.Quantity(sk.getDatum("Height").Value * sy, App.Units.Length))
    return edit


# --- scenario table ----------------------------------------------------------------

def model_key(sc):
    """(family, sorted parameters): the model a scenario runs on."""
    b = sc[1]
    fam = getattr(b, "func", b).__name__
    return fam, tuple(sorted(getattr(b, "keywords", {}).items()))


def S(name, build, params, edit, pred, expect):
    return (name, functools.partial(build, **params), edit, pred, expect)


SCENARIOS = [
    # name, family, model parameters, edit, oracle predicate, documented verdict.
    # attach_pad_two_sides is a real FreeCAD silent retarget on b4fcc8e: the
    # element map keeps the end cap's name on the new -Z cap and FreeCAD moves
    # HoleSketch onto it; the tracker must report it as a remap.
    S("attach_pad_longer", build_attach, dict(w=30), set_pad(Length=10), pred_end_cap, "kept"),
    S("attach_sketch_wider", build_attach, dict(w=31), set_datum("Width", 40), pred_end_cap, "kept"),
    S("attach_pad_reversed", build_attach, dict(w=32), set_pad(Reversed=True), pred_end_cap, "kept"),
    S("attach_pad_two_sides", build_attach, dict(w=33), set_pad(SideType="Two sides", Length2=3), pred_end_cap, "remapped"),
    S("attach_pad_symmetric", build_attach, dict(w=34), set_pad(SideType="Symmetric"), pred_end_cap, "kept"),
    S("attach_split_bottom_line", build_attach, dict(w=35), split_line("bottom"), pred_end_cap, "remapped"),
    S("attach_triangle_profile", build_attach, dict(w=36), triangle_profile, pred_end_cap, "remapped"),
    S("attach_new_profile", build_attach, dict(w=37), new_profile, pred_end_cap, "remapped"),
    S("attach_sketch_taller", build_attach, dict(w=30, h=24), set_datum("Height", 30), pred_end_cap, "kept"),
    S("attach_notch_top_line", build_attach, dict(w=30, h=22), notch_line("top"), pred_end_cap, "remapped"),
    S("fillet_pad_longer", build_fillet, dict(w=30), set_pad(Length=9), pred_front_top_edge, "kept"),
    S("fillet_sketch_wider", build_fillet, dict(w=31), set_datum("Width", 36), pred_front_top_edge, "kept"),
    S("fillet_hole_radius", build_fillet, dict(w=32), hole_radius, pred_front_top_edge, "kept"),
    S("fillet_move_hole", build_fillet, dict(w=33), move_hole, pred_front_top_edge, "kept"),
    S("fillet_split_bottom_line", build_fillet, dict(w=34), split_line("bottom"), pred_front_top_edge, "remapped"),
    S("fillet_pocket_blind", build_fillet, dict(w=35), pocket_blind, pred_front_top_edge, "kept"),
    S("fillet_notch_bottom_line", build_fillet, dict(w=36), notch_line("bottom"), pred_front_top_edge, "lost"),
    S("cut_bore_radius", build_cut, dict(L=40), set_attr("Cyl", Radius=6), pred_bore, "kept"),
    S("cut_box_longer", build_cut, dict(L=41), set_attr("Box", Length=60), pred_bore, "kept"),
    S("cut_tool_outside", build_cut, dict(L=42), set_attr("Cyl", Base=V(200, 200, -5)), pred_bore, "lost"),
    S("cut_tool_box", build_cut, dict(L=43), swap_tool_box, pred_bore, "lost"),
    S("cut_box_thicker", build_cut, dict(L=44, H=12), set_attr("Box", Height=20), pred_bore, "kept"),
    S("box_taller", build_box, dict(L=10), set_attr("Box", Height=9), pred_box_top, "kept"),
    S("box_longer", build_box, dict(L=11), set_attr("Box", Length=25), pred_box_top, "kept"),
    S("box_flipped", build_box, dict(L=12), rotate_box, pred_box_top, "remapped"),
    S("box_lifted", build_box, dict(L=13), set_attr("Box", Base=V(0, 0, 5)), pred_box_top, "kept"),
    S("sides_sketch_wider", build_sides, dict(w=30), set_datum("Width", 45), pred_plus_x, "kept"),
    S("sides_split_right_line", build_sides, dict(w=31), split_line("right"), pred_plus_x, "remapped"),
    S("sides_notch_right_line", build_sides, dict(w=32), notch_line("right"), pred_plus_x, "lost"),
    S("sides_arc_right_side", build_sides, dict(w=33), arc_side, pred_plus_x, "lost"),
    S("chamfer_pad_longer", build_pdchamfer, dict(w=30), set_pad(Length=10), pred_right_top_edge, "kept"),
    S("chamfer_sketch_wider", build_pdchamfer, dict(w=31), set_datum("Width", 40), pred_right_top_edge, "kept"),
    S("chamfer_split_right_line", build_pdchamfer, dict(w=32), split_line("right"), pred_right_top_edge, "remapped"),
    S("chamfer_pad_reversed", build_pdchamfer, dict(w=33), set_pad(Reversed=True), pred_right_top_edge, "kept"),
    S("fuse_tower_taller", build_fuse, dict(tx=0), set_attr("Tower", Height=25), pred_tower_top, "kept"),
    S("fuse_tower_moved", build_fuse, dict(tx=1), set_attr("Tower", Base=V(8, 0, 0)), pred_tower_top, "kept"),
    S("fuse_tower_sunk", build_fuse, dict(tx=2), set_attr("Tower", Height=3), pred_tower_top, "lost"),
    S("fuse_slab_longer", build_fuse, dict(tx=3), set_attr("Slab", Length=30), pred_tower_top, "kept"),
    S("extrude_longer", build_extrude, dict(w=12), set_attr("Extrude", LengthFwd=9), pred_extrude_cap, "kept"),
    S("extrude_reversed", build_extrude, dict(w=13), set_attr("Extrude", Reversed=True), pred_extrude_cap, "kept"),
    S("extrude_symmetric", build_extrude, dict(w=14), set_attr("Extrude", Symmetric=True), pred_extrude_cap, "kept"),
    S("extrude_profile_wider", build_extrude, dict(w=15), set_sketch_corner(4, 2), pred_extrude_cap, "remapped"),
    S("revolve_half", build_revolve, dict(r_out=10), set_attr("Revolution", Angle=180), pred_outer_cyl, "remapped"),
    S("revolve_outer_radius", build_revolve, dict(r_out=11), set_revolve_outer(14), pred_outer_cyl, "lost*"),
    S("revolve_taller", build_revolve, dict(r_out=12, height=6), set_sketch_corner(0, 4), pred_outer_cyl, "remapped"),
    S("revolve_reversed", build_revolve, dict(r_out=13, angle=270), set_attr("Revolution", Reversed=True), pred_outer_cyl, "kept"),
    S("datum_sketch_taller", build_datum, dict(w=30), set_datum("Height", 26), pred_back_face, "kept"),
    S("datum_pad_longer", build_datum, dict(w=31), set_pad(Length=12), pred_back_face, "kept"),
    S("datum_split_top_line", build_datum, dict(w=32), split_line("top"), pred_back_face, "remapped"),
    S("datum_notch_top_line", build_datum, dict(w=33), notch_line("top"), pred_back_face, "lost"),
    S("extgeo_pad_longer", build_extgeo, dict(w=30), set_pad(Length=11), pred_back_top_edge, "kept"),
    S("extgeo_sketch_taller", build_extgeo, dict(w=31), set_datum("Height", 25), pred_back_top_edge, "kept"),
    S("extgeo_split_top_line", build_extgeo, dict(w=32), split_line("top"), pred_back_top_edge, "remapped"),
    S("cylinder_radius", build_cylinder, dict(r=5), set_attr("Cylinder", Radius=8), pred_cyl_top, "kept"),
    S("cylinder_taller", build_cylinder, dict(r=6), set_attr("Cylinder", Height=20), pred_cyl_top, "kept"),
    S("cylinder_flipped", build_cylinder, dict(r=7), rotate_obj("Cylinder", V(1, 0, 0), 180), pred_cyl_top, "remapped"),
    S("cylinder_partial", build_cylinder, dict(r=8), set_attr("Cylinder", Angle=180), pred_cyl_top, "remapped"),
    S("common_radius", build_common, dict(r=8), set_attr("Cyl", Radius=9), pred_common_wall, "kept"),
    S("common_box_taller", build_common, dict(r=7), set_attr("Box", Height=14), pred_common_wall, "kept"),
    S("common_tool_away", build_common, dict(r=6), set_attr("Cyl", Base=V(100, 100, -5)), pred_common_wall, "lost"),
    S("floor_deeper", build_pocket_floor, dict(depth=3), set_attr("Pocket", Length=5), pred_pocket_floor, "kept"),
    S("floor_pad_longer", build_pocket_floor, dict(depth=2), set_pad(Length=12), pred_pocket_floor, "kept"),
    S("floor_through_all", build_pocket_floor, dict(depth=4), set_attr("Pocket", Type="ThroughAll"), pred_pocket_floor, "lost"),
    S("boss_taller", build_boss, dict(boss=5), set_attr("Boss", Length=9), pred_boss_top, "kept"),
    S("boss_radius", build_boss, dict(boss=6), set_circle_radius("BossSketch", 6), pred_boss_top, "remapped"),
    S("boss_base_longer", build_boss, dict(boss=7), set_pad(Length=10), pred_boss_top, "kept"),
    S("boss_sunk", build_boss, dict(boss=4), set_attr("Boss", Reversed=True), pred_boss_top, "lost"),
    S("mirror_box_longer", build_mirror, dict(L=10), set_attr("Box", Length=16), pred_mirror_far, "kept"),
    S("mirror_box_wider", build_mirror, dict(L=11), set_attr("Box", Width=9), pred_mirror_far, "kept"),
    S("mirror_plane_y", build_mirror, dict(L=12), set_attr("Mirror", Normal=V(0, 1, 0)), pred_mirror_far, "kept"),
    S("fillet2_pad_longer", build_fillet2, dict(w=30), set_pad(Length=10), pred_left_top_edge, "kept"),
    S("fillet2_sketch_wider", build_fillet2, dict(w=31), set_datum("Width", 40), pred_left_top_edge, "kept"),
    S("fillet2_split_bottom_line", build_fillet2, dict(w=32), split_line("bottom"), pred_left_top_edge, "remapped"),
    S("sides3_sketch_taller", build_sides_back, dict(w=30), set_datum("Height", 28), pred_back_face, "kept"),
    S("sides3_scaled", build_sides_back, dict(w=31), scale_pad_sketch(1.5, 0.5), pred_back_face, "kept"),
    S("sides3_notch_top_line", build_sides_back, dict(w=32), notch_line("top"), pred_back_face, "lost"),
]
