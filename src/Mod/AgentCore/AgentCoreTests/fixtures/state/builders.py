# SPDX-License-Identifier: LGPL-2.1-or-later
"""Script-built fixture documents for the AgentCore.state tests."""

import os

import FreeCAD as App
import Part
import Sketcher

V = App.Vector

from AgentCore.paths import source_root  # noqa: E402

REPO = source_root()
UPSTREAM = {
    "upstream_partdesign_example": "data/examples/PartDesignExample.FCStd",
    "upstream_padtest": "data/tests/PadTest.fcstd",
    "upstream_pockettest": "data/tests/PocketTest.fcstd",
    "upstream_crank": "data/tests/Crank.fcstd",
    "upstream_assembly_example": "data/examples/AssemblyExample.FCStd",
    "upstream_draft_test_objects": "data/examples/draft_test_objects.FCStd",
}


def polygon(sk, pts, constrain=True):
    n = len(pts)
    first = len(sk.Geometry)
    for i in range(n):
        sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % n]))
    if constrain:
        for i in range(n):
            sk.addConstraint(Sketcher.Constraint("Coincident", first + i, 2, first + (i + 1) % n, 1))
    return list(range(first, first + n))


def rect_sketch(body, name, w, h, support=None, constrain=True):
    sk = body.newObject("Sketcher::SketchObject", name)
    sk.AttachmentSupport = support if support else (body.Origin.OriginFeatures[3], [""])
    sk.MapMode = "FlatFace"
    g = polygon(sk, [V(0, 0, 0), V(w, 0, 0), V(w, h, 0), V(0, h, 0)], constrain)
    if constrain:
        sk.addConstraint(Sketcher.Constraint("Horizontal", g[0]))
        sk.addConstraint(Sketcher.Constraint("Horizontal", g[2]))
        sk.addConstraint(Sketcher.Constraint("Vertical", g[1]))
        sk.addConstraint(Sketcher.Constraint("Vertical", g[3]))
        sk.addConstraint(Sketcher.Constraint("Coincident", g[0], 1, -1, 1))
        sk.addConstraint(Sketcher.Constraint("DistanceX", g[0], 1, g[0], 2, w))
        sk.addConstraint(Sketcher.Constraint("DistanceY", g[1], 1, g[1], 2, h))
        sk.renameConstraint(len(sk.Constraints) - 2, "Width")
        sk.renameConstraint(len(sk.Constraints) - 1, "Height")
    return sk


def face_index(shape, pred):
    for i, f in enumerate(shape.Faces):
        if pred(f):
            return "Face%d" % (i + 1)
    raise LookupError("no face matches")


def edge_index(shape, pred):
    for i, e in enumerate(shape.Edges):
        if pred(e):
            return "Edge%d" % (i + 1)
    raise LookupError("no edge matches")


def top_face(shape):
    z = shape.BoundBox.ZMax
    return face_index(shape, lambda f: f.Surface.TypeId == "Part::GeomPlane" and abs(f.CenterOfMass.z - z) < 1e-6)


def build_partdesign_block(name="f1_pd_block"):
    """Body: constrained rect sketch, Pad driven by a spreadsheet alias, a
    pocket sketch attached to the top face, a hole-like pocket and a fillet;
    plus a VarSet driving the pocket radius."""
    doc = App.newDocument(name)
    sheet = doc.addObject("Spreadsheet::Sheet", "Params")
    sheet.set("A1", "Thickness")
    sheet.set("B1", "=6 mm")
    sheet.setAlias("B1", "thickness")
    sheet.set("A2", "Ratio")
    sheet.set("B2", "0.25")
    vs = doc.addObject("App::VarSet", "Vars")
    vs.addProperty("App::PropertyLength", "HoleRadius", "Dims")
    vs.HoleRadius = 2.5
    vs.addProperty("App::PropertyInteger", "Count", "Dims")
    vs.Count = 3
    body = doc.addObject("PartDesign::Body", "Body")
    sk = rect_sketch(body, "Sketch", 30, 20)
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Profile = sk
    pad.Length = 6
    pad.setExpression("Length", "Params.thickness")
    doc.recompute()
    top = top_face(pad.Shape)
    sk2 = body.newObject("Sketcher::SketchObject", "HoleSketch")
    sk2.AttachmentSupport = (pad, [top])
    sk2.MapMode = "FlatFace"
    sk2.addGeometry(Part.Circle(V(10, 10, 0), V(0, 0, 1), 2.5))
    sk2.addConstraint(Sketcher.Constraint("Radius", 0, 2.5))
    sk2.setExpression("Constraints[0]", "Vars.HoleRadius")
    pocket = body.newObject("PartDesign::Pocket", "Pocket")
    pocket.Profile = sk2
    pocket.Type = 1  # through all
    doc.recompute()
    edge = edge_index(pocket.Shape, lambda e: e.Curve.TypeId == "Part::GeomLine" and abs(e.CenterOfMass.z - 6) < 1e-6 and abs(e.CenterOfMass.y) < 1e-6)
    fil = body.newObject("PartDesign::Fillet", "Fillet")
    fil.Base = (pocket, [edge])
    fil.Radius = 1.0
    doc.recompute()
    return doc


def build_sketch_states(name="f1_sketch_states"):
    """Three standalone sketches: fully constrained, under-constrained and
    over-constrained (redundant), plus construction geometry and an arc."""
    doc = App.newDocument(name)
    full = doc.addObject("Sketcher::SketchObject", "Full")
    g = polygon(full, [V(0, 0, 0), V(8, 0, 0), V(8, 5, 0), V(0, 5, 0)])
    full.addConstraint(Sketcher.Constraint("Horizontal", g[0]))
    full.addConstraint(Sketcher.Constraint("Horizontal", g[2]))
    full.addConstraint(Sketcher.Constraint("Vertical", g[1]))
    full.addConstraint(Sketcher.Constraint("Vertical", g[3]))
    full.addConstraint(Sketcher.Constraint("Coincident", g[0], 1, -1, 1))
    full.addConstraint(Sketcher.Constraint("DistanceX", g[0], 1, g[0], 2, 8))
    full.addConstraint(Sketcher.Constraint("DistanceY", g[1], 1, g[1], 2, 5))
    under = doc.addObject("Sketcher::SketchObject", "Under")
    polygon(under, [V(0, 0, 0), V(6, 0, 0), V(3, 4, 0)])
    under.addGeometry(Part.Circle(V(3, 1.5, 0), V(0, 0, 1), 0.8), True)
    under.addGeometry(Part.ArcOfCircle(Part.Circle(V(10, 0, 0), V(0, 0, 1), 2), 0.0, 1.5))
    over = doc.addObject("Sketcher::SketchObject", "Over")
    over.addGeometry(Part.LineSegment(V(0, 0, 0), V(4, 0, 0)))
    over.addConstraint(Sketcher.Constraint("Horizontal", 0))
    over.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 4))
    over.addConstraint(Sketcher.Constraint("Distance", 0, 4))
    over.addConstraint(Sketcher.Constraint("Horizontal", 0))
    doc.recompute()
    return doc


def build_techdraw(name="f1_techdraw"):
    """A box with a cylinder cut, a TechDraw page with a template, two views
    and a distance dimension."""
    import TechDraw  # noqa: F401

    doc = App.newDocument(name)
    box = doc.addObject("Part::Box", "Box")
    box.Length, box.Width, box.Height = 40, 25, 10
    cyl = doc.addObject("Part::Cylinder", "Cyl")
    cyl.Radius = 4
    cyl.Height = 30
    cyl.Placement.Base = V(20, 12.5, -5)
    cut = doc.addObject("Part::Cut", "Cut")
    cut.Base, cut.Tool = box, cyl
    page = doc.addObject("TechDraw::DrawPage", "Page")
    tmpl = doc.addObject("TechDraw::DrawSVGTemplate", "Template")
    tmpl.Template = os.path.join(App.getResourceDir(), "Mod", "TechDraw", "Templates", "ISO", "A4_Landscape_blank.svg")
    page.Template = tmpl
    top = doc.addObject("TechDraw::DrawViewPart", "TopView")
    page.addView(top)
    top.Source = [cut]
    top.Direction = V(0, 0, 1)
    top.X, top.Y = 100, 150
    front = doc.addObject("TechDraw::DrawViewPart", "FrontView")
    page.addView(front)
    front.Source = [cut]
    front.Direction = V(0, -1, 0)
    front.X, front.Y = 100, 80
    doc.recompute()
    dim = doc.addObject("TechDraw::DrawViewDimension", "Dimension")
    dim.Type = "DistanceX"
    edges = top.getVisibleEdges()
    idx = max(range(len(edges)), key=lambda i: edges[i].Length)
    dim.References2D = [(top, "Edge%d" % idx)]
    page.addView(dim)
    doc.recompute()
    return doc


BUILT = {
    "built_partdesign_block": build_partdesign_block,
    "built_sketch_states": build_sketch_states,
    "built_techdraw": build_techdraw,
}


def open_fixture(key):
    """Open (upstream, no recompute) or build a fixture; returns the document."""
    if key in BUILT:
        return BUILT[key]()
    return App.openDocument(os.path.join(REPO, UPSTREAM[key]))


ALL = sorted(list(BUILT) + list(UPSTREAM))
