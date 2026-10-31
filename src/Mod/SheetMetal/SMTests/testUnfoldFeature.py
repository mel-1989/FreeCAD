# SPDX-License-Identifier: LGPL-2.1-or-later
"""The default Unfold feature path (SMUnfold on a document object) on U-profiles, an edge flange
and a hem (cad-fork run 026 verifier findings).

Run 026's verifier found that a symmetric U (legs 25 / 30 / 25, T = 2, inside R = 3) unfolded from
an outer leg face gave a flat of 118.95 or 39.59 mm against the correct 79.94 mm, with no message:
the thickness estimator measured the web width (30 or 32 mm) instead of 2 mm. The thickness now
comes from the concentric bend cylinders, and the unfold refuses when the thickness does not
match them. Formula and sources as in testOracle.py (Machinery's Handbook bend allowance).
"""

import math
import unittest

import FreeCAD

import SheetMetalBendTable as BT
import SheetMetalEdgeFlange as EF
import SheetMetalHemFeature as HF
import SheetMetalKfactor
import SheetMetalNewUnfolder as NU
import SheetMetalTools
import SheetMetalUnfoldCmd
from SMTests import testEdgeFlange as TEF
from SMTests import testHem as THM
from SMTests.testOracle import HAVE_NEW, LEN_TOL, WIDTH, Case, build_base, measure, unfold_target_face


def feature_unfold(doc, base, face, material="_manual", k=0.4, old=False):
    """Unfold through the SMUnfold feature exactly as the command does; returns the feature."""
    FreeCAD.setActiveDocument(doc.Name)
    SheetMetalTools.params.SetBool("UseOldUnfolder", old)
    try:
        obj = doc.addObject("Part::FeaturePython", "Unfold")
        SheetMetalUnfoldCmd.SMUnfold(obj, base, [face])
        obj.MaterialSheet = material
        obj.KFactor = k
        obj.KFactorStandard = "ansi"
        obj.GenerateSketch = False
        doc.recompute()
    finally:
        SheetMetalTools.params.SetBool("UseOldUnfolder", False)
    return obj


def big_planes(shape, thickness):
    """Planar faces of the legs and web (not the thin end faces, not the side walls)."""
    out = []
    for i, f in enumerate(shape.Faces):
        if f.Surface.TypeId == "Part::GeomPlane" and abs(f.normalAt(0, 0).y) < 0.5 \
                and f.Area > thickness * WIDTH * 1.01:
            out.append(i)
    return out


class TestUProfileThickness(unittest.TestCase):
    CASES = [
        Case("u_25_30_25", 2.0, 1.5, 0.4, [25.0, 30.0, 25.0], [90.0, 90.0]),
        Case("u_25_40_25", 2.0, 1.5, 0.4, [25.0, 40.0, 25.0], [90.0, 90.0]),
        Case("u_25_30_30", 2.0, 1.5, 0.4, [25.0, 30.0, 30.0], [90.0, 90.0]),  # unequal legs were fine
    ]

    def setUp(self):
        self.doc = FreeCAD.newDocument("SMUProfile")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def test_estimated_thickness_is_the_sheet_thickness_for_every_root_face(self):
        for case in self.CASES:
            base = build_base(self.doc, case)
            faces = big_planes(base.Shape, case.T)
            self.assertEqual(len(faces), 6, case.name)
            for i in faces:
                with self.subTest(case=case.name, face=i + 1):
                    self.assertAlmostEqual(NU.EstimateThickness.using_best_method(base.Shape, i), case.T, 9)

    def test_default_unfold_feature_gives_the_right_flat_from_every_face(self):
        for case in self.CASES:
            base = build_base(self.doc, case)
            expected = case.developed_length()
            for i in big_planes(base.Shape, case.T):
                with self.subTest(case=case.name, face="Face%d" % (i + 1)):
                    obj = feature_unfold(self.doc, base, "Face%d" % (i + 1), k=case.k)
                    self.assertNotIn("Invalid", obj.State)
                    self.assertFalse(obj.Shape.isNull())
                    _v, flat_len, _vol = measure(obj.Shape, case.T, WIDTH)
                    self.assertAlmostEqual(flat_len, expected, delta=LEN_TOL)

    def test_bend_table_through_the_feature_on_the_u(self):
        case = Case("u_table", 2.0, 1.5, 0.4, [25.0, 30.0, 25.0], [90.0, 90.0])
        base = build_base(self.doc, case)
        lookup = BT.lookup_from_ref("bt_mild_steel_ba")
        expected = case.straight_total() + 2 * lookup.allowance(case.T, case.R, 90.0)
        for i in big_planes(base.Shape, case.T):
            with self.subTest(face="Face%d" % (i + 1)):
                obj = feature_unfold(self.doc, base, "Face%d" % (i + 1), "bt_mild_steel_ba")
                self.assertAlmostEqual(measure(obj.Shape, case.T, WIDTH)[1], expected, delta=LEN_TOL)

    @unittest.skipUnless(HAVE_NEW, "networkx is not installed; the new unfolder needs it")
    def test_inconsistent_thickness_is_refused_with_a_readable_error(self):
        case = self.CASES[0]
        base = build_base(self.doc, case)
        shape = base.Shape
        graph = NU.build_graph_of_tangent_faces(shape, big_planes(shape, case.T)[0])
        faces = list(shape.Faces)
        NU.check_thickness_against_bends(faces, graph, case.T)  # the right thickness passes
        with self.assertRaises(RuntimeError) as cm:
            NU.check_thickness_against_bends(faces, graph, 30.0)
        msg = str(cm.exception)
        self.assertIn("thickness 30", msg)
        self.assertIn("no concentric bend face", msg)

    def test_task_panel_lists_bend_tables(self):
        """The material list the panel filters (getSpreadSheetNames) offers bt_ entries."""
        FreeCAD.setActiveDocument(self.doc.Name)
        labels = [o.Label for o in SheetMetalKfactor.getSpreadSheetNames()]
        self.assertIn("bt_mild_steel_ba/mild_steel", labels)
        self.assertIn("bt_mild_steel_gauge_bd/mild_steel", labels)
        # the panel keeps entries with these prefixes (populateMdsList)
        self.assertTrue("bt_mild_steel_ba/mild_steel".startswith(("material_", "csv_", BT.PREFIX)))


class TestFeaturePathEdgeFlangeAndHem(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument("SMFeaturePath")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def test_edge_flange_through_the_default_feature(self):
        c = next(c for c in TEF.CASES if c["edges"] == ("+x",) and c["gap"] == 0.0 and c["measure"] == "outer")
        box = TEF.make_box(self.doc, c["T"])
        ff = EF.make_edge_flange(self.doc, box, [TEF.find_edge(box, e) for e in c["edges"]], length=c["L"],
                                 angle=c["angle"], radius=c["R"], kfactor=c["k"], gap=c["gap"],
                                 flip=c["flip"], measure=c["measure"])
        s = TEF.expected(c, "+x")[0]
        expected, _ = TEF.flat_expected(c, TEF.PL, s)
        for old in (False, True):
            with self.subTest(old_unfolder=old):
                obj = feature_unfold(self.doc, ff, unfold_target_face(ff.Shape), k=c["k"], old=old)
                self.assertNotIn("Invalid", obj.State)
                self.assertAlmostEqual(obj.Shape.Volume / (c["T"] * TEF.W), expected, delta=1e-3)

    def test_hem_through_the_default_feature(self):
        c = next(c for c in THM.CASES if c["type"] == "Closed" and c["end_gap"] == 0.0 and c["edges"] == ("+x",)
                 and not c["flip"])
        box = TEF.make_box(self.doc, c["T"])
        hem = HF.make_hem(self.doc, box, [TEF.find_edge(box, e) for e in c["edges"]], hemType=c["type"],
                          gap=c["gap"] or 1.0, length=c["L"], kfactor=c["k"], flip=c["flip"],
                          endGap=c["end_gap"])
        expected = TEF.PL + math.pi * (c["R"] + c["k"] * c["T"]) + c["L"]
        for old in (False, True):
            with self.subTest(old_unfolder=old):
                obj = feature_unfold(self.doc, hem, unfold_target_face(hem.Shape), k=c["k"], old=old)
                self.assertNotIn("Invalid", obj.State)
                self.assertAlmostEqual(obj.Shape.Volume / (c["T"] * TEF.W), expected, delta=1e-3)


if __name__ == "__main__":
    unittest.main()
