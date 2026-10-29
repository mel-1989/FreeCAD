# SPDX-License-Identifier: LGPL-2.1-or-later

# Regression tests for references that no longer resolve (cad-fork G01 slice 5): a stale face name
# in Surface::Filling and an unresolved element in Measure::MeasureAngle are reported as errors
# (formerly a silent wrong result and a crash).

import unittest

import os
import tempfile

import FreeCAD
import Part

try:
    import Sketcher
    import PartDesign  # noqa: F401
except ImportError:  # pragma: no cover
    Sketcher = None

try:
    import Measure
except ImportError:  # pragma: no cover
    Measure = None


def _box_with_filling(face_for_edges, doc):
    box = doc.addObject("Part::Box", "Box")
    doc.recompute()
    shape = box.Shape
    top = shape.Faces[5]
    edge_names = []
    for e in top.OuterWire.OrderedEdges:
        for i, se in enumerate(shape.Edges):
            if se.isSame(e):
                edge_names.append("Edge%d" % (i + 1))
    fill = doc.addObject("Surface::Filling", "Filling")
    fill.BoundaryEdges = [(box, n) for n in edge_names]
    fill.BoundaryFaces = [face_for_edges(shape, n) for n in edge_names]
    fill.BoundaryOrder = [0] * len(edge_names)
    return box, fill


class TestReferenceErrors(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument("RefErrors")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def test_filling_accepts_face_that_carries_the_edge(self):
        def own_face(shape, edge_name):
            e = shape.getElement(edge_name)
            for i, f in enumerate(shape.Faces):
                if i != 5 and any(fe.isSame(e) for fe in f.Edges):
                    return "Face%d" % (i + 1)
        _, fill = _box_with_filling(own_face, self.doc)
        self.doc.recompute()
        self.assertNotIn("Invalid", fill.State)
        self.assertFalse(fill.Shape.isNull())

    def test_filling_rejects_face_without_the_edge(self):
        def opposite_face(shape, edge_name):
            e = shape.getElement(edge_name)
            for i, f in enumerate(shape.Faces):
                if not any(fe.isSame(e) for fe in f.Edges):
                    return "Face%d" % (i + 1)
        _, fill = _box_with_filling(opposite_face, self.doc)
        self.doc.recompute()
        self.assertIn("Invalid", fill.State)

    @unittest.skipIf(Measure is None, "Measure module not available")
    def test_measure_angle_unresolved_element_is_an_error(self):
        box = self.doc.addObject("Part::Box", "Box")
        self.doc.recompute()
        ang = self.doc.addObject("Measure::MeasureAngle", "Angle")
        ang.Element1 = (box, ["Edge1"])
        ang.Element2 = (box, ["Edge99"])
        self.doc.recompute()
        self.assertIn("Invalid", ang.State)


V = FreeCAD.Vector
W, H, L = 40.0, 20.0, 10.0


def _plane_face(shape, normal, point):
    """Name of the planar face with the given normal that contains the point."""
    for i, f in enumerate(shape.Faces):
        if not isinstance(f.Surface, Part.Plane):
            continue
        u0, u1, v0, v1 = f.ParameterRange
        n = f.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
        if abs(abs(n.dot(V(*normal))) - 1) < 1e-9 and f.isInside(V(*point), 1e-6, True):
            return "Face%d" % (i + 1)
    raise LookupError(("face", normal, point))


def _line_edge(shape, direction, point):
    for i, e in enumerate(shape.Edges):
        if not isinstance(e.Curve, Part.Line):
            continue
        m = e.valueAt((e.FirstParameter + e.LastParameter) / 2)
        if abs(e.Curve.Direction.dot(V(*direction))) > 1 - 1e-9 and (m - V(*point)).Length < 1e-6:
            return "Edge%d" % (i + 1)
    raise LookupError(("edge", direction, point))


def _sketch(doc, body, name, z=0.0, after=None):
    sk = doc.addObject("Sketcher::SketchObject", name)
    if after is None:
        body.addObject(sk)
    else:
        body.insertObject(sk, after, True)
    sk.AttachmentSupport = (body.Origin.OriginFeatures[3], [""])
    sk.MapMode = "FlatFace"
    sk.AttachmentOffset = FreeCAD.Placement(V(0, 0, z), FreeCAD.Rotation())
    return sk


def _hole(doc, body, z, after, name="Hole"):
    sk = _sketch(doc, body, name + "Sketch", z=z, after=after)
    sk.addGeometry(Part.Circle(V(-10, 0, 0), V(0, 0, 1), 2.0), False)
    pk = doc.addObject("PartDesign::Pocket", name)
    body.insertObject(pk, sk, True)
    pk.Profile = sk
    pk.Length = 2 * L
    body.Tip = pk
    return pk


def _padded_body(doc):
    body = doc.addObject("PartDesign::Body", "Body")
    sk = _sketch(doc, body, "Sketch")
    pts = [V(-W / 2, -H / 2, 0), V(W / 2, -H / 2, 0), V(W / 2, H / 2, 0), V(-W / 2, H / 2, 0)]
    for i in range(4):
        sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]), False)
    for i in range(4):
        sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
    pad = doc.addObject("PartDesign::Pad", "Pad")
    body.addObject(pad)
    pad.Profile = sk
    pad.Length = L
    doc.recompute()
    return body, pad


def _top_filling(doc, body, z):
    """Filling on the four edges of the top face (z), G1 to the top face on the +Y edge."""
    sh = body.Shape
    b = H / 2
    edges = [
        _line_edge(sh, (1, 0, 0), (0, b, z)),
        _line_edge(sh, (0, 0, 1), (-W / 2, b, z / 2)),
        _line_edge(sh, (1, 0, 0), (0, b, 0)),
        _line_edge(sh, (0, 0, 1), (W / 2, b, z / 2)),
    ]
    fill = doc.addObject("Surface::Filling", "Fill")
    fill.BoundaryEdges = [(body, [e]) for e in edges]
    fill.BoundaryFaces = [_plane_face(sh, (0, 0, 1), (5, 5, z)), "", "", ""]
    fill.BoundaryOrder = [1, 0, 0, 0]
    doc.recompute()
    return fill


@unittest.skipIf(Sketcher is None, "Sketcher or PartDesign not available")
class TestFillingFollowsSupportFace(unittest.TestCase):
    """A G1 support face that keeps its plane and outline is the same face after an edit that
    renumbers it, even when the link layer cannot rescue its mapped name (D6 same-element ruling)."""

    def setUp(self):
        self.doc = FreeCAD.newDocument("FillFollow")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def _assert_follows_top(self, fill, body, z):
        self.assertNotIn("Invalid", fill.State, fill.getStatusString())
        top = _plane_face(body.Shape, (0, 0, 1), (5, 5, z))
        self.assertEqual(fill.BoundaryFaces[0], top)
        # the +Y face also contains the boundary edge; it must never be taken for the support
        self.assertNotEqual(fill.BoundaryFaces[0], _plane_face(body.Shape, (0, 1, 0), (5, H / 2, 5)))

    def test_hole_present_first_then_pad_grows(self):
        body, pad = _padded_body(self.doc)
        _hole(self.doc, body, L, pad)
        self.doc.recompute()
        fill = _top_filling(self.doc, body, L)
        self.assertNotIn("Invalid", fill.State)
        pad.Length = 14.0
        self.doc.recompute()
        self._assert_follows_top(fill, body, 14.0)

    def test_pad_grows_after_hole_is_inserted(self):
        body, pad = _padded_body(self.doc)
        fill = _top_filling(self.doc, body, L)
        _hole(self.doc, body, L, pad)
        self.doc.recompute()
        pad.Length = 14.0
        self.doc.recompute()
        self._assert_follows_top(fill, body, 14.0)

    def test_bottom_face_keeps_outline_when_hole_is_suppressed(self):
        body, pad = _padded_body(self.doc)
        hole = _hole(self.doc, body, L, pad)
        self.doc.recompute()
        sh = body.Shape
        b = H / 2
        fill = self.doc.addObject("Surface::Filling", "Fill")
        fill.BoundaryEdges = [
            (body, [_line_edge(sh, (1, 0, 0), (0, b, L))]),
            (body, [_line_edge(sh, (0, 0, 1), (-W / 2, b, L / 2))]),
            (body, [_line_edge(sh, (1, 0, 0), (0, b, 0))]),
            (body, [_line_edge(sh, (0, 0, 1), (W / 2, b, L / 2))]),
        ]
        fill.BoundaryFaces = ["", "", _plane_face(sh, (0, 0, -1), (5, 5, 0)), ""]
        fill.BoundaryOrder = [0, 0, 1, 0]
        self.doc.recompute()
        self.assertNotIn("Invalid", fill.State)
        hole.Suppressed = True
        self.doc.recompute()
        pad.Length = 12.0
        self.doc.recompute()
        self.assertNotIn("Invalid", fill.State, fill.getStatusString())
        self.assertEqual(fill.BoundaryFaces[2], _plane_face(body.Shape, (0, 0, -1), (5, 5, 0)))

    def test_mirror_is_seeded_on_open(self):
        # a file from stock FreeCAD has no mirror: it must exist before the first edit
        body, pad = _padded_body(self.doc)
        fill = _top_filling(self.doc, body, L)
        fill.BoundaryFaceLinks = []
        fill.BoundaryFaceSig = []
        fill.purgeTouched()  # as saved by stock FreeCAD: up to date, nothing else touched
        path = os.path.join(tempfile.mkdtemp(), "stock.FCStd")
        self.doc.saveAs(path)
        FreeCAD.closeDocument(self.doc.Name)
        self.doc = FreeCAD.openDocument(path)
        body, pad = self.doc.getObject("Body"), self.doc.getObject("Pad")
        fill = self.doc.getObject("Fill")
        self.assertEqual([s for _, ss in fill.BoundaryFaceLinks for s in ss], ["Face6", "", "", ""])
        _hole(self.doc, body, L, pad)
        self.doc.recompute()
        pad.Length = 14.0
        self.doc.recompute()
        self._assert_follows_top(fill, body, 14.0)


class TestFillingRescue(unittest.TestCase):
    """The string fallback when the hidden mirror lost its face ("?FaceN"): the face the visible
    string names is accepted only when it is provably the face the Filling was built on. Other
    faces are never searched."""

    def setUp(self):
        self.doc = FreeCAD.newDocument("FillRescue")
        self.box = self.doc.addObject("Part::Box", "Box")
        self.doc.recompute()
        sh = self.box.Shape
        self.top = _plane_face(sh, (0, 0, 1), (5, 5, 10))
        self.py = _plane_face(sh, (0, 1, 0), (5, 10, 5))
        self.edge = _line_edge(sh, (1, 0, 0), (5, 10, 10))  # shared by the top and the +Y face
        top = sh.getElement(self.top)
        edges = []
        for e in top.OuterWire.OrderedEdges:
            edges.append("Edge%d" % (1 + [i for i, x in enumerate(sh.Edges) if x.isSame(e)][0]))
        self.edges = edges
        self.idx = edges.index(self.edge)
        self.fill = self.doc.addObject("Surface::Filling", "Fill")
        self.fill.BoundaryEdges = [(self.box, [e]) for e in edges]
        faces = [""] * len(edges)
        faces[self.idx] = self.top
        self.fill.BoundaryFaces = faces
        self.fill.BoundaryOrder = [1 if i == self.idx else 0 for i in range(len(edges))]
        self.doc.recompute()
        self.assertNotIn("Invalid", self.fill.State)

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def _lose_face(self, name=None):
        subs = [[""] if i != self.idx else ["?" + (name or self.top)] for i in range(len(self.edges))]
        self.fill.BoundaryFaceLinks = [(self.box, s) for s in subs]
        self.fill.touch()
        self.doc.recompute()

    def test_lost_face_is_recovered_through_the_string_face(self):
        self._lose_face()
        self.assertNotIn("Invalid", self.fill.State, self.fill.getStatusString())
        self.assertEqual(self.fill.BoundaryFaces[self.idx], self.top)
        self.assertEqual(self.fill.BoundaryFaceLinks[0][1][self.idx], self.top)

    def test_other_faces_are_never_searched(self):
        # the string names the +Y face, the stored signature is the top face's: the top face is
        # adjacent to the edge and matches the signature, but it is not what the string says
        sig = list(self.fill.BoundaryFaceSig)
        faces = list(self.fill.BoundaryFaces)
        faces[self.idx] = self.py
        self.fill.BoundaryFaces = faces
        self.doc.recompute()
        self.fill.BoundaryFaceSig = sig
        self._lose_face(self.py)
        self.assertIn("Invalid", self.fill.State)
        self.assertEqual(self.fill.BoundaryFaces[self.idx], self.py)

    def test_changed_normal_is_not_accepted(self):
        # the support face is recorded as facing down; neither face at the edge does
        sig = list(self.fill.BoundaryFaceSig)
        t, x, y, z, n = sig[self.idx].split()
        sig[self.idx] = " ".join((t, x, y, str(-float(z)), n))
        self.fill.BoundaryFaceSig = sig
        self._lose_face()
        self.assertIn("Invalid", self.fill.State)

    def test_changed_surface_type_is_not_accepted(self):
        sig = list(self.fill.BoundaryFaceSig)
        sig[self.idx] = "1" + sig[self.idx][1:]  # not a plane
        self.fill.BoundaryFaceSig = sig
        self._lose_face()
        self.assertIn("Invalid", self.fill.State)

    def test_trimmed_outline_is_not_accepted(self):
        sig = list(self.fill.BoundaryFaceSig)
        sig[self.idx] = sig[self.idx].rsplit(" ", 1)[0] + " 5"  # recorded with a five-edge outline
        self.fill.BoundaryFaceSig = sig
        self._lose_face()
        self.assertIn("Invalid", self.fill.State)

    def test_file_without_signature_keeps_the_error(self):
        self.fill.BoundaryFaceSig = []
        self._lose_face()
        # the signature is rewritten only when a face resolves; nothing to check the string with
        self.assertIn("Invalid", self.fill.State)

    def test_error_rewrites_neither_string_nor_mirror(self):
        sig = list(self.fill.BoundaryFaceSig)
        sig[self.idx] = "1" + sig[self.idx][1:]
        self.fill.BoundaryFaceSig = sig
        self._lose_face()
        self.assertIn("Invalid", self.fill.State)
        self.assertEqual(self.fill.BoundaryFaces[self.idx], self.top)
        self.assertEqual(self.fill.BoundaryFaceLinks[0][1][self.idx], "?" + self.top)

    def test_reopened_lost_face_stays_lost_when_the_string_is_stale(self):
        sig = list(self.fill.BoundaryFaceSig)
        sig[self.idx] = "1" + sig[self.idx][1:]  # the string's face no longer matches
        self.fill.BoundaryFaceSig = sig
        self._lose_face()
        self.assertIn("Invalid", self.fill.State)
        path = os.path.join(tempfile.mkdtemp(), "lost.FCStd")
        self.doc.saveAs(path)
        FreeCAD.closeDocument(self.doc.Name)
        self.doc = FreeCAD.openDocument(path)
        fill = self.doc.getObject("Fill")
        self.doc.recompute()
        self.assertIn("Invalid", fill.State)
        self.assertEqual(fill.BoundaryFaceLinks[0][1][self.idx], "?" + self.top)


class TestFillingSeam(unittest.TestCase):
    """Two coplanar faces meet at the boundary edge (an unrefined fusion): the face is ambiguous
    once the link layer loses it, so the Filling errors rather than pick one (D6: no new silent
    retarget against fork/main, which keeps the string's face)."""

    def setUp(self):
        self.doc = FreeCAD.newDocument("FillSeam")
        a = self.doc.addObject("Part::Box", "A")
        b = self.doc.addObject("Part::Box", "B")
        b.Placement = FreeCAD.Placement(V(10, 0, 0), FreeCAD.Rotation())
        fuse = self.doc.addObject("Part::MultiFuse", "Fuse")
        fuse.Shapes = [a, b]
        fuse.Refine = False
        self.doc.recompute()
        self.fuse = fuse
        sh = fuse.Shape
        self.tl = _plane_face(sh, (0, 0, 1), (5, 5, 10))
        self.tr = _plane_face(sh, (0, 0, 1), (15, 5, 10))
        self.assertNotEqual(self.tl, self.tr)
        self.seam = _line_edge(sh, (0, 1, 0), (10, 5, 10))
        tl = sh.getElement(self.tl)
        edges = []
        for e in tl.OuterWire.OrderedEdges:
            edges.append("Edge%d" % (1 + [i for i, x in enumerate(sh.Edges) if x.isSame(e)][0]))
        self.edges = edges
        self.idx = edges.index(self.seam)
        self.fill = self.doc.addObject("Surface::Filling", "Fill")
        self.fill.BoundaryEdges = [(fuse, [e]) for e in edges]
        faces = [""] * len(edges)
        faces[self.idx] = self.tl
        self.fill.BoundaryFaces = faces
        self.fill.BoundaryOrder = [1 if i == self.idx else 0 for i in range(len(edges))]
        self.doc.recompute()
        self.assertNotIn("Invalid", self.fill.State, self.fill.getStatusString())

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def test_coplanar_neighbour_makes_a_lost_face_an_error(self):
        subs = [[""] if i != self.idx else ["?" + self.tl] for i in range(len(self.edges))]
        self.fill.BoundaryFaceLinks = [(self.fuse, s) for s in subs]
        self.fill.touch()
        self.doc.recompute()
        self.assertIn("Invalid", self.fill.State)
        self.assertEqual(self.fill.BoundaryFaces[self.idx], self.tl)


@unittest.skipIf(Sketcher is None, "Sketcher or PartDesign not available")
class TestFillingInsertThenDelete(unittest.TestCase):
    """A feature inserted across the support breaks the Filling; deleting it brings the Filling
    back on the same face (the error must not rewrite the face string or the mirror)."""

    def setUp(self):
        self.doc = FreeCAD.newDocument("FillInsDel")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def test_insert_then_delete_recovers(self):
        body, pad = _padded_body(self.doc)
        fill = _top_filling(self.doc, body, L)
        self.assertNotIn("Invalid", fill.State)
        before = list(fill.BoundaryFaces)
        sk = _sketch(self.doc, body, "SlotSketch", z=L, after=pad)
        y0 = -5.0
        pts = [V(-100, y0 - 1.2, 0), V(100, y0 - 1.2, 0), V(100, y0 + 1.2, 0), V(-100, y0 + 1.2, 0)]
        for i in range(4):
            sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]), False)
        for i in range(4):
            sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
        slot = self.doc.addObject("PartDesign::Pocket", "SlotPocket")
        body.insertObject(slot, sk, True)
        slot.Profile = sk
        slot.Length = 5.0
        body.Tip = slot
        self.doc.recompute()
        self.assertIn("Invalid", fill.State)  # the slot cuts the boundary edges across the top
        body.removeObject(slot)
        self.doc.removeObject(slot.Name)
        body.removeObject(sk)
        self.doc.removeObject(sk.Name)
        self.doc.recompute()
        self.assertNotIn("Invalid", fill.State, fill.getStatusString())
        self.assertEqual(list(fill.BoundaryFaces), before)


class TestSectionsSewingReferences(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument("SecRefs")
        self.b1 = self.doc.addObject("Part::Box", "B1")
        self.b2 = self.doc.addObject("Part::Box", "B2")
        self.b2.Placement = FreeCAD.Placement(V(0, 0, 20), FreeCAD.Rotation())
        self.doc.recompute()

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def _sections(self, extra):
        s = self.doc.addObject("Surface::Sections", "Sec")
        s.NSections = [(self.b1, ["Edge1"]), (self.b2, ["Edge1"])] + extra
        self.doc.recompute()
        return s

    def test_valid_non_edge_subshape_is_still_skipped(self):
        control = self._sections([])
        self.assertNotIn("Invalid", control.State, control.getStatusString())
        with_vertex = self._sections([(self.b1, ["Vertex1"])])
        self.assertNotIn("Invalid", with_vertex.State, with_vertex.getStatusString())

    def test_unresolved_subshape_is_an_error(self):
        s = self._sections([(self.b1, ["Edge99"])])
        self.assertIn("Invalid", s.State)

    def test_sewing_unresolved_subshape_is_an_error(self):
        sew = self.doc.addObject("Surface::Sewing", "Sew")
        sew.ShapeList = [(self.b1, ["Face1", "Face99"])]
        self.doc.recompute()
        self.assertIn("Invalid", sew.State)
