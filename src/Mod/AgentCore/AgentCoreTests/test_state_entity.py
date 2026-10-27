# SPDX-License-Identifier: LGPL-2.1-or-later
"""F1: entity IDs, semantic descriptions and per-kind detail sections."""

import os
import sys
import unittest

import FreeCAD as App

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "state")
if FIX not in sys.path:
    sys.path.insert(0, FIX)

import builders  # noqa: E402
from AgentCore import state  # noqa: E402


def by_name(d, name):
    return next(o for o in d["objects"] if o["name"] == name)


class TestStateEntity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = builders.build_partdesign_block("f1_entity")

    @classmethod
    def tearDownClass(cls):
        App.closeDocument(cls.doc.Name)

    def _face(self, obj, pred):
        return builders.face_index(obj.Shape, pred)

    def test_entity_id_uses_element_map(self):
        fil = self.doc.getObject("Fillet")
        for i in range(1, len(fil.Shape.Faces) + 1):
            eid = state.entity_id(fil, "Face%d" % i)
            self.assertTrue(eid.startswith("Fillet:;"), eid)
            self.assertEqual(eid[len("Fillet:;"):], fil.Shape.getElementMappedName("Face%d" % i))
            obj, short = state.resolve_entity(self.doc, eid)
            self.assertIs(obj, fil)
            self.assertEqual(short, "Face%d" % i)
        self.assertEqual(state.entity_id(fil), "Fillet")
        # via a parent path
        self.assertEqual(state.entity_id(self.doc.getObject("Body"), "Fillet.Face2"), state.entity_id(fil, "Face2"))

    def test_entity_id_without_map(self):
        doc = App.newDocument("f1_entity_box")
        try:
            box = doc.addObject("Part::Box", "Box")
            doc.recompute()
            self.assertEqual(state.entity_id(box, "Face3"), "Box:Face3")
            self.assertEqual(state.resolve_entity(doc, "Box:Face3"), (box, "Face3"))
            self.assertEqual(state.describe_entity(box, "Face6")["label"], "planar face, normal +Z, area 100 mm^2, created by Box")
        finally:
            App.closeDocument(doc.Name)

    def test_ids_stable_across_noop_recompute(self):
        fil = self.doc.getObject("Fillet")
        before = [state.entity_id(fil, "Face%d" % i) for i in range(1, len(fil.Shape.Faces) + 1)]
        self.doc.getObject("Pad").touch()
        self.doc.recompute()
        after = [state.entity_id(fil, "Face%d" % i) for i in range(1, len(fil.Shape.Faces) + 1)]
        self.assertEqual(before, after)

    def test_resolve_lost_id(self):
        obj, short = state.resolve_entity(self.doc, "Fillet:;#nonexistent;:H1,F")
        self.assertEqual(short, "")
        self.assertEqual(state.resolve_entity(self.doc, "NoSuchObject:Face1"), (None, ""))

    def test_describe_hole_face(self):
        fil = self.doc.getObject("Fillet")
        hole = self._face(fil, lambda f: f.Surface.TypeId == "Part::GeomCylinder" and abs(f.Surface.Radius - 2.5) < 1e-6)
        d = state.describe_entity(fil, hole)
        self.assertEqual(d["element_type"], "face")
        self.assertEqual(d["geometry"], "Part::GeomCylinder")
        self.assertEqual(d["dimensions"]["diameter"], 5.0)
        self.assertEqual(d["axis"], [0.0, 0.0, 1.0])
        self.assertEqual(d["creator"], "Pocket")
        self.assertEqual(d["label"], "cylindrical face, diameter 5 mm, axis +Z, created by Pocket")
        self.assertIsNotNone(d["history"])

    def test_describe_fillet_and_pad_faces(self):
        fil = self.doc.getObject("Fillet")
        rnd = self._face(fil, lambda f: f.Surface.TypeId == "Part::GeomCylinder" and abs(f.Surface.Radius - 1.0) < 1e-6)
        d = state.describe_entity(fil, rnd)
        self.assertEqual((d["creator"], d["operation"], d["creation"]), ("Fillet", "fillet", "created"))
        self.assertIn("diameter 2 mm, axis +X", d["label"])
        back = self._face(fil, lambda f: f.Surface.TypeId == "Part::GeomPlane" and abs(f.CenterOfMass.y - 20) < 1e-6)
        d = state.describe_entity(fil, back)
        self.assertEqual(d["creator"], "Pad")
        self.assertEqual(d["label"], "planar face, normal +Y, area 180 mm^2, created by Pad")

    def test_describe_edges_vertices_objects(self):
        pad = self.doc.getObject("Pad")
        e = builders.edge_index(pad.Shape, lambda e: abs(e.Length - 30) < 1e-6)
        d = state.describe_entity(pad, e)
        self.assertEqual(d["element_type"], "edge")
        self.assertEqual(d["dimensions"]["length"], 30.0)
        self.assertIn("length 30 mm", d["label"])
        self.assertEqual(d["creator"], "Pad")
        circ = builders.edge_index(self.doc.getObject("Pocket").Shape, lambda e: e.Curve.TypeId == "Part::GeomCircle")
        d = state.describe_entity(self.doc.getObject("Pocket"), circ)
        self.assertIn("circular edge, diameter 5 mm, axis", d["label"])
        d = state.describe_entity(pad, "Vertex1")
        self.assertEqual(d["element_type"], "vertex")
        self.assertTrue(d["label"].startswith("vertex at ("))
        d = state.describe_entity(pad, "")
        self.assertEqual(d["element_type"], "object")
        d = state.describe_entity(pad, "Face999")
        self.assertIn("missing", d["label"])

    def test_sections_partdesign_sheet_varset(self):
        d = state.dump(self.doc)
        body = by_name(d, "Body")
        self.assertEqual(body["kind"], "body")
        self.assertEqual(body["detail"]["tip"], "Fillet")
        self.assertEqual([f["name"] for f in body["detail"]["features"]], ["Sketch", "Pad", "HoleSketch", "Pocket", "Fillet"])
        self.assertEqual(body["detail"]["features"][1]["profile"], "Sketch")
        pad = by_name(d, "Pad")
        self.assertEqual(pad["expressions"], [{"path": "Length", "expression": "Params.thickness"}])
        self.assertIn("Sketch", pad["out_list"])
        self.assertIn("Body", pad["in_list"])
        length = next(p for p in pad["properties"] if p["name"] == "Length")
        self.assertEqual(length["type"], "App::PropertyLength")
        self.assertEqual(length["value"], {"value": 6.0, "unit": "Length"})
        self.assertIn("UpToFace", next(p for p in pad["properties"] if p["name"] == "Type")["enum"])
        self.assertEqual(pad["shape"]["solids"], 1)
        sheet = by_name(d, "Params")["detail"]["cells"]
        b1 = next(c for c in sheet if c["address"] == "B1")
        self.assertEqual((b1["alias"], b1["content"], b1["value"]), ("thickness", "=6 mm", {"value": 6.0, "unit": "Length"}))
        self.assertEqual([c["address"] for c in sheet], ["A1", "B1", "A2", "B2"])
        vs = {v["name"]: v for v in by_name(d, "Vars")["detail"]["variables"]}
        self.assertEqual(set(vs), {"HoleRadius", "Count"})
        self.assertEqual(vs["HoleRadius"]["value"], {"value": 2.5, "unit": "Length"})
        self.assertEqual(vs["Count"]["type"], "App::PropertyInteger")
        hs = by_name(d, "HoleSketch")["detail"]
        self.assertEqual(hs["attachment"]["support"][0]["object"], "Pad")
        self.assertEqual(by_name(d, "HoleSketch")["expressions"][0]["expression"], "Vars.HoleRadius")
        fil = by_name(d, "Fillet")
        base = next(p for p in fil["properties"] if p["name"] == "Base")
        self.assertEqual(base["value"]["object"], "Pocket")
        self.assertEqual(base["type"], "App::PropertyLinkSub")

    def test_sections_sketch_states(self):
        doc = builders.build_sketch_states("f1_entity_sketch")
        try:
            d = state.dump(doc)
            full = by_name(d, "Full")["detail"]
            self.assertEqual((full["solver"]["dof"], full["solver"]["fully_constrained"]), (0, True))
            self.assertEqual(len(full["geometry"]), 4)
            dx = [c for c in full["constraints"] if c["type"] == "DistanceX"]
            self.assertEqual([c["value"] for c in dx], [8.0])
            self.assertTrue(full["dependent_parameters"] == [] or isinstance(full["dependent_parameters"][0], list))
            under = by_name(d, "Under")["detail"]
            self.assertGreater(under["solver"]["dof"], 0)
            self.assertFalse(under["solver"]["fully_constrained"])
            self.assertTrue(under["dependent_parameters"])
            self.assertTrue(any(g["construction"] for g in under["geometry"]))
            self.assertIn("Part::GeomArcOfCircle", [g["type"] for g in under["geometry"]])
            over = by_name(d, "Over")["detail"]
            self.assertTrue(over["solver"]["redundant"] or over["solver"]["partially_redundant"] or over["solver"]["conflicting"])
        finally:
            App.closeDocument(doc.Name)

    def test_sections_techdraw(self):
        doc = builders.build_techdraw("f1_entity_td")
        try:
            d = state.dump(doc)
            page = by_name(d, "Page")
            self.assertEqual(page["kind"], "techdraw_page")
            self.assertEqual(page["detail"]["template_file"], "A4_Landscape_blank.svg")
            self.assertEqual({v["name"] for v in page["detail"]["views"]}, {"TopView", "FrontView", "Dimension"})
            top = by_name(d, "TopView")["detail"]
            self.assertEqual(top["sources"], ["Cut"])
            self.assertEqual(top["direction"], [0.0, 0.0, 1.0])
            self.assertGreater(top["visible_edges"], 0)
            dim = by_name(d, "Dimension")["detail"]
            self.assertEqual(dim["dimension_type"], "DistanceX")
            self.assertTrue(dim["text"])
        finally:
            App.closeDocument(doc.Name)

    def test_sections_assembly(self):
        doc = builders.open_fixture("upstream_assembly_example")
        try:
            d = state.dump(doc)
            asm = [o for o in d["objects"] if o["kind"] == "assembly"]
            self.assertEqual(len(asm), 1)
            joints = [o for o in d["objects"] if o["kind"] == "joint"]
            self.assertGreater(len(joints), 0)
            self.assertEqual(set(asm[0]["detail"]["joints"]), {o["name"] for o in joints})
            j = joints[0]["detail"]
            self.assertTrue(j["joint_type"])
            self.assertIn("object", j["reference1"])
            self.assertTrue(asm[0]["detail"]["grounded"])
        finally:
            App.closeDocument(doc.Name)
