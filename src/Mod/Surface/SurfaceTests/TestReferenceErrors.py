# SPDX-License-Identifier: LGPL-2.1-or-later

# Regression tests for references that no longer resolve (cad-fork G01 slice 5): a stale face name
# in Surface::Filling and an unresolved element in Measure::MeasureAngle are reported as errors
# (formerly a silent wrong result and a crash).

import unittest

import FreeCAD

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
