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
import Part
from FreeCAD import Vector as V

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
    """Unfold through the SMUnfold feature exactly as the command does; returns the feature.

    old=False runs the new unfolder (opt-in), old=True the V1 unfolder, old=None leaves the
    parameter alone (the fork default, V1). The parameter is put back afterwards: removed if it
    was not set before, so no test leaves it persisted.
    """
    params = SheetMetalTools.params
    existed = "UseOldUnfolder" in params.GetBools()
    previous = params.GetBool("UseOldUnfolder", True)
    FreeCAD.setActiveDocument(doc.Name)
    if old is not None:
        params.SetBool("UseOldUnfolder", old)
    try:
        obj = doc.addObject("Part::FeaturePython", "Unfold")
        SheetMetalUnfoldCmd.SMUnfold(obj, base, [face])
        obj.MaterialSheet = material
        obj.KFactor = k
        obj.KFactorStandard = "ansi"
        obj.GenerateSketch = False
        doc.recompute()
    finally:
        if existed:
            params.SetBool("UseOldUnfolder", previous)
        else:
            params.RemBool("UseOldUnfolder")
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
        root = big_planes(shape, case.T)[0]
        NU.check_sheet_consistency(shape, root, case.T)  # the right thickness passes
        with self.assertRaises(NU.RootFaceError) as cm:  # the bends show 2.0, not 2.5
            NU.check_sheet_consistency(shape, root, 2.5, "measured for the test")
        self.assertIn("2.5", str(cm.exception))

    def test_task_panel_lists_bend_tables(self):
        """The material list the panel filters (getSpreadSheetNames) offers bt_ entries."""
        FreeCAD.setActiveDocument(self.doc.Name)
        labels = [o.Label for o in SheetMetalKfactor.getSpreadSheetNames()]
        self.assertIn("bt_mild_steel_ba/mild_steel", labels)
        self.assertIn("bt_mild_steel_gauge_bd/mild_steel", labels)
        # the panel keeps entries with these prefixes (populateMdsList)
        self.assertTrue("bt_mild_steel_ba/mild_steel".startswith(("material_", "csv_", BT.PREFIX)))


def l_profile(thickness=2.0, leg=40.0, inner=None, outer=None, outer_offset=0.0):
    """An L-shaped extrusion of width WIDTH with optional bend fillets (inner radius `inner`,
    outer radius `outer`, or inner + thickness + outer_offset when outer is None and inner set)."""
    t = thickness
    pts = [V(0, 0, 0), V(leg, 0, 0), V(leg, t, 0), V(t, t, 0), V(t, leg, 0), V(0, leg, 0), V(0, 0, 0)]
    solid = Part.Face(Part.makePolygon(pts)).extrude(V(0, 0, WIDTH))

    def find(shape, x, y):
        return [e for e in shape.Edges if abs(e.Length - WIDTH) < 1e-6
                and abs(e.Vertexes[0].Point.x - x) < 1e-9 and abs(e.Vertexes[0].Point.y - y) < 1e-9][0]

    if inner:
        solid = solid.makeFillet(inner, [find(solid, t, t)])
    if outer:
        solid = solid.makeFillet(outer, [find(solid, 0, 0)])
    return solid


def sheet_faces(shape, thickness):
    """Large planar faces of an l_profile (the two faces of each leg), by index."""
    return [i for i, f in enumerate(shape.Faces)
            if f.Surface.TypeId == "Part::GeomPlane" and abs(f.normalAt(0, 0).z) < 0.5
            and f.Area > thickness * WIDTH * 10]


def refused(obj):
    return "Invalid" in obj.State or obj.Shape.isNull()


class TestSheetValidation(unittest.TestCase):
    """Run 026 second verifier: counterbores, STEP-level radius offsets, sharp / single-fillet
    bends and roots on end faces must never give a silently wrong flat."""

    def setUp(self):
        self.doc = FreeCAD.newDocument("SMValidate")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def _add(self, name, shape):
        obj = self.doc.addObject("Part::Feature", name)
        obj.Shape = shape
        self.doc.recompute()
        return obj

    @staticmethod
    def _counterbores(shape, xs, y, t_top, r_thru=3.0, r_cb=4.0, depth=1.0, z_top=None):
        for x in xs:
            shape = shape.cut(Part.makeCylinder(r_thru, 10, V(x, y, -5), V(0, 0, 1)))
            shape = shape.cut(Part.makeCylinder(r_cb, depth, V(x, y, (t_top if z_top is None else z_top) - depth),
                                                V(0, 0, 1)))
        return shape

    def test_counterbores_do_not_change_the_thickness_of_a_bent_part(self):
        case = Case("L_cb", 2.0, 1.5, 0.5, [40.0, 40.0], [90.0])
        base = build_base(self.doc, case)
        shape = base.Shape
        for r_cb in (4.0, 3.5):  # radius differences 1.0 and 0.5 against the sheet's 2.0
            cb = shape
            for x in (12.0, 24.0):
                cb = cb.cut(Part.makeCylinder(3.0, 10, V(x, -18, -5), V(0, 0, 1)))
                cb = cb.cut(Part.makeCylinder(r_cb, 1.0, V(x, -18, -2.0), V(0, 0, 1)))
            self.assertGreaterEqual(len([f for f in cb.Faces if f.Surface.TypeId == "Part::GeomCylinder"]), 6)
            obj = self._add("Lcb%g" % r_cb, cb)
            roots = big_planes(cb, 2.0)
            self.assertGreaterEqual(len(roots), 4)
            for i in roots:
                with self.subTest(r_cb=r_cb, face=i + 1):
                    self.assertAlmostEqual(NU.EstimateThickness.using_best_method(cb, i), 2.0, 6)
                    feat = feature_unfold(self.doc, obj, "Face%d" % (i + 1), k=0.5)
                    self.assertFalse(refused(feat), "counterbored L refused")
                    # K = 0.5: the flat is as long as the developed length of the plain part
                    length = measure(feat.Shape, 2.0, WIDTH)[1]
                    self.assertAlmostEqual(length, case.developed_length(), delta=0.5)

    def test_counterbores_on_a_bendless_plate_do_not_change_the_thickness(self):
        plate = Part.makeBox(60, WIDTH, 2.0, V(0, 0, 0))
        for x in (15.0, 40.0):
            plate = plate.cut(Part.makeCylinder(3.0, 10, V(x, 18, -5), V(0, 0, 1)))
            plate = plate.cut(Part.makeCylinder(4.0, 1.0, V(x, 18, 1.0), V(0, 0, 1)))
        obj = self._add("plate_cb", plate)
        big = [i for i, f in enumerate(plate.Faces) if f.Surface.TypeId == "Part::GeomPlane"
               and abs(f.normalAt(0, 0).z) > 0.9 and f.Area > 500]
        self.assertEqual(len(big), 2)
        for i in big:
            self.assertAlmostEqual(NU.EstimateThickness.using_best_method(plate, i), 2.0, 6)
            feat = feature_unfold(self.doc, obj, "Face%d" % (i + 1), k=0.5)
            self.assertFalse(refused(feat))
            self.assertAlmostEqual(feat.Shape.BoundBox.XLength, 60.0, 3)

    def test_step_level_radius_offset_is_accepted(self):
        """Outer radius R + T + 2e-5 (STEP rounding): one tolerance for radius and centre."""
        for offset in (2e-5, 8e-5):
            with self.subTest(offset=offset):
                shape = l_profile(2.0, inner=3.0)
                outer_edge = [e for e in shape.Edges if abs(e.Length - WIDTH) < 1e-6
                              and abs(e.Vertexes[0].Point.x) < 1e-9 and abs(e.Vertexes[0].Point.y) < 1e-9][0]
                shape = shape.makeFillet(5.0 + offset, [outer_edge])
                obj = self._add("Loff%g" % offset, shape)
                for i in sheet_faces(shape, 2.0):
                    feat = feature_unfold(self.doc, obj, "Face%d" % (i + 1), k=0.5)
                    self.assertFalse(refused(feat), "offset %g refused" % offset)
                    self.assertAlmostEqual(feat.Shape.Volume / shape.Volume, 1.0, delta=1e-3)

    def test_radius_offset_beyond_tolerance_falls_back_to_the_old_unfolder(self):
        """An outer radius off by more than 1e-4 (imported STEP) is flattened by V1, exactly."""
        for offset in (1e-3, 5e-3):
            with self.subTest(offset=offset):
                shape = l_profile(2.0, inner=3.0)
                outer_edge = [e for e in shape.Edges if abs(e.Length - WIDTH) < 1e-6
                              and abs(e.Vertexes[0].Point.x) < 1e-9 and abs(e.Vertexes[0].Point.y) < 1e-9][0]
                shape = shape.makeFillet(5.0 + offset, [outer_edge])
                root = sheet_faces(shape, 2.0)[0]
                t = NU.EstimateThickness.using_best_method(shape, root)
                with self.assertRaises(NU.SheetNeedsV1Error) as cm:
                    NU.check_sheet_consistency(shape, root, t, "measured through the root face")
                msg = str(cm.exception)
                self.assertIn("measured through the root face", msg)
                self.assertIn("radius", msg)
                self.assertIn("Face", msg)
                obj = self._add("Loff%g" % offset, shape)
                for i in sheet_faces(shape, 2.0):
                    feat = feature_unfold(self.doc, obj, "Face%d" % (i + 1), k=0.5)
                    self.assertFalse(refused(feat), "offset %g refused" % offset)
                    self.assertAlmostEqual(feat.Shape.Volume / shape.Volume, 1.0, delta=1e-3)

    def test_all_sharp_bend_falls_back_to_the_old_unfolder(self):
        shape = l_profile(2.0)
        obj = self._add("Lsharp", shape)
        roots = sheet_faces(shape, 2.0)
        self.assertEqual(len(roots), 4)
        with self.assertRaises(NU.SheetNeedsV1Error):
            NU.check_sheet_consistency(shape, roots[0], 2.0)
        for i in roots:
            with self.subTest(face=i + 1):
                feat = feature_unfold(self.doc, obj, "Face%d" % (i + 1), k=0.5)
                self.assertFalse(refused(feat))
                self.assertAlmostEqual(feat.Shape.Volume / shape.Volume, 1.0, delta=1e-3)

    def test_single_cylinder_bends_are_refused_not_flattened_wrongly(self):
        for name, shape in (("outer_only", l_profile(2.0, outer=2.0)), ("inner_only", l_profile(2.0, inner=3.0))):
            obj = self._add(name, shape)
            for i in sheet_faces(shape, 2.0):
                with self.subTest(part=name, face=i + 1):
                    feat = feature_unfold(self.doc, obj, "Face%d" % (i + 1), k=0.5)
                    self.assertTrue(refused(feat), "%s Face%d gave a flat" % (name, i + 1))
        with self.assertRaises(NU.SheetConsistencyError) as cm:
            shape = l_profile(2.0, outer=2.0)
            NU.check_sheet_consistency(shape, sheet_faces(shape, 2.0)[0], 2.0)
        self.assertIn("no concentric bend face", str(cm.exception))

    def test_round_bend_with_both_fillets_is_fine(self):
        shape = l_profile(2.0, inner=3.0, outer=5.0)
        obj = self._add("Lboth", shape)
        for i in sheet_faces(shape, 2.0):
            feat = feature_unfold(self.doc, obj, "Face%d" % (i + 1), k=0.5)
            self.assertFalse(refused(feat))
            self.assertAlmostEqual(feat.Shape.Volume / shape.Volume, 1.0, delta=1e-3)

    def test_root_on_an_end_face_or_side_wall_is_refused(self):
        case = Case("L_root", 2.0, 1.5, 0.5, [40.0, 40.0], [90.0])
        base = build_base(self.doc, case)
        shape = base.Shape
        walls = [i for i, f in enumerate(shape.Faces) if f.Surface.TypeId == "Part::GeomPlane"
                 and i not in big_planes(shape, 2.0)]
        self.assertGreaterEqual(len(walls), 4)  # two end faces and two side walls
        for i in walls:
            with self.subTest(face=i + 1):
                if HAVE_NEW:
                    with self.assertRaises(NU.RootFaceError) as cm:
                        NU.check_sheet_consistency(shape, i, NU.EstimateThickness.using_best_method(shape, i))
                    self.assertIn("large faces of the sheet", str(cm.exception))
                feat = feature_unfold(self.doc, base, "Face%d" % (i + 1), k=0.5)
                self.assertTrue(refused(feat), "root on a wall gave a flat")


class TestDefaultIsV1(unittest.TestCase):
    """The fork default is the V1 unfolder; the new one is opt-in (UseOldUnfolder = False)."""

    def setUp(self):
        self.doc = FreeCAD.newDocument("SMDefault")
        self.had = "UseOldUnfolder" in SheetMetalTools.params.GetBools()

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)
        # the helper must not leave the parameter persisted
        self.assertEqual("UseOldUnfolder" in SheetMetalTools.params.GetBools(), self.had)

    def test_default_uses_v1(self):
        from unittest import mock

        if not self.had:
            self.assertTrue(SheetMetalTools.use_old_unfolder())
        case = Case("def", 2.0, 1.5, 0.4, [25.0, 30.0, 25.0], [90.0, 90.0])
        base = build_base(self.doc, case)
        face = "Face%d" % (big_planes(base.Shape, case.T)[0] + 1)
        with mock.patch.object(SheetMetalUnfoldCmd.SMUnfold, "newUnfolder", side_effect=AssertionError("new unfolder ran")), \
                mock.patch.object(SheetMetalUnfoldCmd.SMUnfold, "oldUnfolder",
                                  autospec=True, side_effect=SheetMetalUnfoldCmd.SMUnfold.oldUnfolder) as old_fn:
            if self.had:
                self.skipTest("UseOldUnfolder is set in this profile")
            obj = feature_unfold(self.doc, base, face, k=case.k, old=None)
        self.assertEqual(old_fn.call_count, 1)
        self.assertFalse(refused(obj))
        self.assertAlmostEqual(measure(obj.Shape, case.T, WIDTH)[1], case.developed_length(), delta=LEN_TOL)

    def test_bend_table_on_the_default_path(self):
        case = Case("deft", 2.0, 1.5, 0.4, [25.0, 30.0, 25.0], [90.0, 90.0])
        base = build_base(self.doc, case)
        lookup = BT.lookup_from_ref("bt_mild_steel_ba")
        expected = case.straight_total() + 2 * lookup.allowance(case.T, case.R, 90.0)
        for i in big_planes(base.Shape, case.T):
            obj = feature_unfold(self.doc, base, "Face%d" % (i + 1), "bt_mild_steel_ba", old=None)
            self.assertFalse(refused(obj))
            self.assertAlmostEqual(measure(obj.Shape, case.T, WIDTH)[1], expected, delta=LEN_TOL)

    def test_new_path_falls_back_on_conical_faces(self):
        """A countersink (cone) makes the opt-in new unfolder fall back to V1 with a warning."""
        shape = l_profile(2.0, inner=3.0, outer=5.0)
        shape = shape.cut(Part.makeCone(1.0, 5.0, 3.0, V(20, -1, 20), V(0, 1, 0)))
        obj = self.doc.addObject("Part::Feature", "Lcs")
        obj.Shape = shape
        self.doc.recompute()
        root = sheet_faces(shape, 2.0)[0]
        with self.assertRaises(NU.SheetNeedsV1Error) as cm:
            NU.check_sheet_consistency(shape, root, 2.0)
        self.assertIn("cone", str(cm.exception))


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
