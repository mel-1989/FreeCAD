# SPDX-License-Identifier: LGPL-2.1-or-later
"""Skeleton corpus kit (G06 slice 3a): deterministic skeletons and subscribers with closed-form truths.

The skeleton is a PartDesign Body "Skel": a rectangle sketch from (0, 0) to (W, H) padded by L (W = 40, H = 20,
L = 10 unless a case says otherwise), plus a datum plane on the +X face, a datum line along the (+X, +Y) vertical
edge and a datum point on that edge's top vertex. Elements are picked by geometry (normal, midpoint, point), never
by name. The truth of every published item is a function of (W, H, L) and of what a case inserts:

| item | geometry |
|---|---|
| face | the top face: area W*H (minus 9*pi with the hole), centre (W/2, H/2, L) |
| edge | the +Y top edge: length W, midpoint (W/2, H, L) |
| vertex | the (+X, +Y) top corner (W, H, L) |
| sketch | the profile: total length 2(W + H), box centre (W/2, H/2, 0) |
| plane | the datum on the +X face: normal +X, offset W |
| line | the datum along the (+X, +Y) vertical edge: direction +Z through (W, H) |
| point | the datum point (W, H, L) |
"""

import math
import os
import tempfile

import FreeCAD as App
import Part
import Sketcher

import AssemblySkeleton as AS

V = App.Vector
W0, H0, L0 = 40.0, 20.0, 10.0
KEYS = ("face", "edge", "vertex", "sketch", "plane", "line", "point")
TOL = 1e-9


# ------------------------------------------------------------------ picking by geometry

def pick_face(shape, normal, point, tol=1e-6):
    n = V(*normal)
    for i, f in enumerate(shape.Faces):
        if type(f.Surface).__name__ == "Plane" and (f.normalAt(0, 0) - n).Length < tol \
                and abs((f.CenterOfMass - V(*point)).dot(n)) < tol:
            return "Face%d" % (i + 1)
    raise LookupError("no face %s through %s" % (normal, point))


def pick_edge(shape, direction, point, tol=1e-6):
    d = V(*direction)
    for i, e in enumerate(shape.Edges):
        if type(e.Curve).__name__ != "Line" or abs(e.Curve.Direction.dot(d)) < 1 - 1e-9:
            continue
        mid = (e.Vertexes[0].Point + e.Vertexes[1].Point) * 0.5
        if (mid - V(*point)).Length < tol:
            return "Edge%d" % (i + 1)
    raise LookupError("no edge %s through %s" % (direction, point))


def pick_vertex(shape, point, tol=1e-6):
    for i, v in enumerate(shape.Vertexes):
        if (v.Point - V(*point)).Length < tol:
            return "Vertex%d" % (i + 1)
    raise LookupError("no vertex at %s" % (point,))


# ------------------------------------------------------------------ the skeleton

def _plane(body, role):
    for f in body.Origin.OriginFeatures:
        if f.Role == role:
            return f
    raise KeyError(role)


def rect_sketch(doc, body, name, w, h, order=0, plane="XY_Plane", offset=0.0):
    sk = body.newObject("Sketcher::SketchObject", name)
    sk.AttachmentSupport = (_plane(body, plane), [""])
    sk.MapMode = "FlatFace"
    if offset:
        sk.AttachmentOffset = App.Placement(V(0, 0, offset), App.Rotation())
    fill_rect(sk, w, h, order)
    return sk


def fill_rect(sk, w, h, order=0):
    pts = [V(0, 0, 0), V(w, 0, 0), V(w, h, 0), V(0, h, 0)]
    segs = [(pts[i], pts[(i + 1) % 4]) for i in range(4)]
    if order:
        segs = [(b, a) for a, b in reversed(segs)]
    for a, b in segs:
        sk.addGeometry(Part.LineSegment(a, b), False)
    for i in range(4):
        sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
    sk.addConstraint(Sketcher.Constraint("Coincident", 0, 1, -1, 1))          # segment 0 starts at the origin in both orders
    horiz, vert = ((0, 2), (1, 3)) if not order else ((1, 3), (0, 2))
    for g in horiz:
        sk.addConstraint(Sketcher.Constraint("Horizontal", g))
    for g in vert:
        sk.addConstraint(Sketcher.Constraint("Vertical", g))
    i = sk.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, w) if not order else
                         Sketcher.Constraint("DistanceX", 1, 1, 1, 2, w))
    sk.renameConstraint(i, "Width")
    i = sk.addConstraint(Sketcher.Constraint("DistanceY", 1, 1, 1, 2, h) if not order else
                         Sketcher.Constraint("DistanceY", 0, 1, 0, 2, h))
    sk.renameConstraint(i, "Height")


def build_skeleton(doc, name="Skel", L=L0, w=W0, h=H0):
    body = doc.addObject("PartDesign::Body", name)
    sk = rect_sketch(doc, body, "Sketch", w, h)
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Profile = sk
    pad.Length = L
    doc.recompute()
    sh = pad.Shape
    d = body.newObject("PartDesign::Plane", "DPlane")
    d.AttachmentSupport = [(pad, pick_face(sh, (1, 0, 0), (w, h / 2, L / 2)))]
    d.MapMode = "FlatFace"
    dl = body.newObject("PartDesign::Line", "DLine")
    dl.AttachmentSupport = [(pad, pick_vertex(sh, (w, h, 0))), (pad, pick_vertex(sh, (w, h, L)))]
    dl.MapMode = "TwoPointLine"
    dp = body.newObject("PartDesign::Point", "DPoint")
    dp.AttachmentSupport = [(pad, pick_vertex(sh, (w, h, L)))]
    dp.MapMode = "Vertex"
    doc.recompute()
    return body


def settip(body):
    for o in reversed(body.Group):
        if o.isDerivedFrom("PartDesign::Feature") and o.TypeId in (
                "PartDesign::Pad", "PartDesign::Pocket", "PartDesign::Fillet"):
            body.Tip = o
            return


def insert_after_pad(doc, body, typ, name):
    o = doc.addObject(typ, name)
    body.insertObject(o, doc.getObject("Pad"), True)
    return o


def insert_hole(doc, L=L0):
    """A through hole (r = 3, 9*pi of area) in the middle of the top, inserted after the Pad: renumbers faces and edges."""
    body = doc.getObject("Skel")
    sk = insert_after_pad(doc, body, "Sketcher::SketchObject", "HoleSketch")
    sk.AttachmentSupport = (_plane(body, "XY_Plane"), [""])
    sk.MapMode = "FlatFace"
    sk.AttachmentOffset = App.Placement(V(0, 0, L), App.Rotation())
    sk.addGeometry(Part.Circle(V(W0 / 2, H0 / 2, 0), V(0, 0, 1), 3.0), False)
    pk = insert_after_pad(doc, body, "PartDesign::Pocket", "Hole")
    pk.Profile = sk
    pk.Type = 1                     # through all
    settip(body)
    doc.recompute()


def insert_vertical_fillet(doc, r=3.0):
    """Fillet the four vertical edges, inserted after the Pad: the corner vertices and the vertical edges vanish."""
    body = doc.getObject("Skel")
    pad = doc.getObject("Pad")
    L = pad.Length.Value
    edges = [pick_edge(pad.Shape, (0, 0, 1), (x, y, L / 2)) for x in (0, W0) for y in (0, H0)]
    f = insert_after_pad(doc, body, "PartDesign::Fillet", "VFillet")
    f.Base = (pad, edges)
    f.Radius = r
    settip(body)
    doc.recompute()


def cut_off_plus_x(doc):
    """A pocket that removes the +X side strip: the +X face and the vertical (+X, +Y) edge are gone."""
    body = doc.getObject("Skel")
    L = doc.getObject("Pad").Length.Value
    sk = insert_after_pad(doc, body, "Sketcher::SketchObject", "CutSketch")
    sk.AttachmentSupport = (_plane(body, "XY_Plane"), [""])
    sk.MapMode = "FlatFace"
    sk.AttachmentOffset = App.Placement(V(0, 0, L), App.Rotation())
    pts = [V(W0 - 4, -1, 0), V(W0 + 1, -1, 0), V(W0 + 1, H0 + 1, 0), V(W0 - 4, H0 + 1, 0)]
    for i in range(4):
        sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]), False)
    for i in range(4):
        sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
    pk = insert_after_pad(doc, body, "PartDesign::Pocket", "Cut")
    pk.Profile = sk
    pk.Type = 1
    settip(body)
    doc.recompute()


def set_size(doc, w=None, h=None):
    sk = doc.getObject("Sketch")
    if w is not None:
        sk.setDatum("Width", App.Units.Quantity("%g mm" % w))
    if h is not None:
        sk.setDatum("Height", App.Units.Quantity("%g mm" % h))
    doc.recompute()


def redraw_profile(doc, w=W0, h=H0, order=1):
    sk = doc.getObject("Sketch")
    sk.deleteAllGeometry()
    fill_rect(sk, w, h, order)
    doc.recompute()


# ------------------------------------------------------------------ what is published

def target_of(body, key, L=L0, w=W0, h=H0):
    """(target object, sub) of the skeleton element behind ``key``, picked by geometry in the current shape."""
    doc = body.Document
    sh = body.Shape
    if key == "face":
        return body, pick_face(sh, (0, 0, 1), (w / 2, h / 2, L))
    if key == "edge":
        return body, pick_edge(sh, (1, 0, 0), (w / 2, h, L))
    if key == "vertex":
        return body, pick_vertex(sh, (w, h, L))
    return doc.getObject({"sketch": "Sketch", "plane": "DPlane", "line": "DLine", "point": "DPoint"}[key]), ""


def _canon(v):
    for c in (v.x, v.y, v.z):
        if abs(c) > 1e-9:
            return v * -1 if c < 0 else v
    return v


def truth(key, L=L0, w=W0, h=H0, hole=False, fillet=0.0):
    """Closed-form signature of the geometry a subscriber of ``key`` must hold, as a tuple of numbers."""
    if key == "face":
        area = w * h - (9 * math.pi if hole else 0.0) - (4 * fillet * fillet * (1 - math.pi / 4) if fillet else 0.0)
        return ("face", area, w / 2, h / 2, L)
    if key == "edge":
        return ("edge", w - (2 * fillet if fillet else 0.0), w / 2, h, L)
    if key == "vertex":
        return ("vertex", w, h, L)
    if key == "sketch":
        return ("sketch", 2 * (w + h), w / 2, h / 2, 0.0)
    if key == "plane":
        return ("plane", 1.0, 0.0, 0.0, w)
    if key == "line":
        return ("line", 0.0, 0.0, 1.0, w, h, 0.0)
    if key == "point":
        return ("point", w, h, L)
    raise KeyError(key)


def observe_shape(shape, key):
    """The same signature, read from the shape a binder (or any object) holds."""
    if key == "face":
        f = shape.Faces[0]
        c = f.CenterOfMass
        return ("face", f.Area, c.x, c.y, c.z)
    if key == "edge":
        e = shape.Edges[0]
        c = (e.Vertexes[0].Point + e.Vertexes[1].Point) * 0.5
        return ("edge", e.Length, c.x, c.y, c.z)
    if key in ("vertex", "point"):
        p = shape.Vertexes[0].Point
        return (key, p.x, p.y, p.z)
    if key == "sketch":
        c = shape.BoundBox.Center
        return ("sketch", sum(e.Length for e in shape.Edges), c.x, c.y, c.z)
    if key == "plane":
        s = shape.Faces[0].Surface
        n = _canon(s.Axis)
        return ("plane", n.x, n.y, n.z, n.dot(s.Position))
    if key == "line":
        c = shape.Edges[0].Curve
        d = _canon(c.Direction)
        p = c.Location - d * d.dot(c.Location)
        return ("line", d.x, d.y, d.z, p.x, p.y, p.z)
    raise KeyError(key)


def same(a, b, tol=TOL):
    return a is not None and b is not None and a[0] == b[0] and len(a) == len(b) \
        and all(abs(x - y) <= tol * max(1.0, abs(y)) for x, y in zip(a[1:], b[1:]))


# ------------------------------------------------------------------ worlds

def tmpdir():
    """A scratch directory with symlinks resolved (macOS /tmp and /var are links; a stored link path must not depend on them)."""
    return os.path.realpath(tempfile.mkdtemp(prefix="skeleton_"))


class World:
    """A skeleton document and a subscriber document (the same one unless ``xdoc``)."""

    def __init__(self, xdoc=False, L=L0, tag="W"):
        self.xdoc = xdoc
        self.dir = None
        self.names = []
        self.sdoc = App.newDocument(tag + "Skel")
        self.body = build_skeleton(self.sdoc, "Skel", L)
        if xdoc:
            self.dir = tmpdir()
            self.sdoc.saveAs(os.path.join(self.dir, "skeleton.FCStd"))
            self.doc = App.newDocument(tag + "Sub")
            self.doc.saveAs(os.path.join(self.dir, "subscriber.FCStd"))
        else:
            self.doc = self.sdoc
        self.names = [self.doc.Name, self.sdoc.Name]
        self.holder = self.doc.addObject("PartDesign::Body", "Sub")
        self.binders = {}

    @property
    def L(self):
        return self.sdoc.getObject("Pad").Length.Value

    def publish(self, key, name=None, **geom):
        tgt, sub = target_of(self.body, key, **geom)
        return AS.publish(self.body, name or key, tgt, sub)

    def subscribe(self, key, name=None, holder=None):
        item = AS.find_item(self.body, name or key)
        rec = AS.subscribe(holder or self.holder, item, object_name="S_" + (name or key))
        b = self.doc.getObject(rec["object"])
        self.binders[name or key] = b
        return b

    def both(self, *keys):
        for k in keys:
            self.publish(k)
            self.subscribe(k)
        return self

    def recompute(self):
        self.sdoc.recompute()
        if self.doc is not self.sdoc:
            self.doc.recompute()

    def set_length(self, L):
        self.sdoc.getObject("Pad").Length = L
        self.recompute()

    def save(self):
        if self.xdoc:
            self.sdoc.save()
        self.doc.save()

    def close(self):
        for d in set(self.names) | {"skeleton", "subscriber"}:
            if d in App.listDocuments():
                App.closeDocument(d)
        if self.dir:
            import shutil
            shutil.rmtree(self.dir, ignore_errors=True)
