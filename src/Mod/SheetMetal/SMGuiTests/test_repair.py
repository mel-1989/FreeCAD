# SPDX-License-Identifier: LGPL-2.1-or-later
"""Slice 6b repair checks: the cutout copy cap, readable refusals after a picked object was deleted, accepting after
the document was closed, the corner relief reason for an oversized relief, and view providers on features made by
the ``make_*`` functions in a GUI session."""

import time
import unittest

import FreeCAD
import FreeCADGui as Gui

import SheetMetalBendCornerRelief as CR
import SheetMetalConvertSolid as CS
import SheetMetalEdgeFlange as EF
import SheetMetalHemFeature as HM
import SheetMetalJog as JG
import SheetMetalLighteningCutout as LC
import SheetMetalLoftedBend as LB
import SMEdgePanels as EP
import SMEdgeViewProviders as EVP
import SMShapeCommands  # noqa: F401  (registers the commands)
import SMShapePanels as SP
import SMShapeViewProviders as SVP

from . import fixture_a as FA
from . import fixture_b as FB

DELETED = "was deleted; pick it again"


def pick(combo, data):
    combo.setCurrentIndex(combo.findData(data))


class Base(unittest.TestCase):
    def setUp(self):
        self.doc = FB.new_doc("Repair")
        FreeCAD.setActiveDocument(self.doc.Name)

    def tearDown(self):
        FB.close_everything()

    def refused(self, panel, words):
        before = FB.snapshot(self.doc)
        with FB.capture_output() as cap:
            ok = panel.accept()
        self.assertFalse(ok)
        self.assertIn(words, panel.error_text())
        self.assertNotIn("ReferenceError", panel.error_text())
        self.assertNotIn("Traceback", cap.text)
        self.assertEqual(FB.snapshot(self.doc), before)


class TestCutoutCopyCap(Base):
    def cutout_panel(self):
        base, fn, face = FB.plate(self.doc)
        sk = FB.circle_sketch(self.doc, face, 5)
        return SP.LighteningCutoutPanel(self.doc, base=base, face=fn, sketch=sk)

    def test_a_huge_grid_is_refused_at_once_with_the_limit_named(self):
        p = self.cutout_panel()
        pick(p.pattern, "Grid")
        for w in (p.count_x, p.count_y):
            w.setValue(1000)
        p.pitch_x.setValue(0.001)
        p.pitch_y.setValue(0.001)
        t = time.time()
        self.refused(p, "at most %d" % LC.MAX_COPIES)
        self.assertLess(time.time() - t, 5.0)

    def test_the_spin_boxes_stop_at_the_cap(self):
        p = self.cutout_panel()
        self.assertEqual(p.count_x.maximum(), LC.MAX_COPIES)
        self.assertEqual(p.count_y.maximum(), LC.MAX_COPIES)

    def test_the_api_refuses_too_many_copies_before_any_geometry(self):
        n = LC.MAX_COPIES + 1
        with self.assertRaises(LC.CutoutError) as cm:
            LC.pattern_offsets("Linear", n, 1, 1.0, 1.0, FreeCAD.Vector(0, 0, 1))
        self.assertIn("at most", str(cm.exception))
        with self.assertRaises(LC.CutoutError):
            LC.pattern_offsets("Grid", 21, 20, 1.0, 1.0, FreeCAD.Vector(0, 0, 1))
        self.assertEqual(len(LC.pattern_offsets("Grid", 20, 20, 1.0, 1.0, FreeCAD.Vector(0, 0, 1))), 400)


class TestDeletedTargets(Base):
    def test_cutout_sketch_deleted(self):
        base, fn, face = FB.plate(self.doc)
        sk = FB.circle_sketch(self.doc, face, 5)
        p = SP.LighteningCutoutPanel(self.doc, base=base, face=fn, sketch=sk)
        self.doc.removeObject(sk.Name)
        self.refused(p, "The selected sketch " + DELETED)

    def test_corner_relief_base_deleted(self):
        t, e = FB.tray(self.doc)
        p = SP.CornerReliefPanel(self.doc, base=t, edge=e)
        self.doc.removeObject(t.Name)
        self.refused(p, "The selected part " + DELETED)

    def test_lofted_bend_profile_deleted(self):
        a, b = FB.profiles(self.doc, 0)
        p = SP.LoftedBendPanel(self.doc, profile_a=a, profile_b=b)
        self.doc.removeObject(b.Name)
        self.refused(p, "The selected profile B " + DELETED)

    def test_dxf_unfold_deleted(self):
        _base, unfold = FB.unfold_of(self.doc)
        p = SP.ExportDxfPanel(self.doc, obj=unfold, path="/tmp/never-written.dxf")
        self.doc.removeObject(unfold.Name)
        self.refused(p, "deleted")

    def test_jog_sketch_deleted(self):
        base = FA.plate(self.doc)
        sk = FA.line_sketch(self.doc, 30.0, 2.0)
        p = EP.JogPanel(self.doc, base=base, face=FA.face_with_normal(base, 2, 1), sketch=sk)
        self.doc.removeObject(sk.Name)
        self.refused(p, "The selected sketch " + DELETED)

    def test_edge_flange_base_deleted(self):
        base = FA.plate(self.doc)
        p = EP.EdgeFlangePanel(self.doc, base=base, edges=[FA.end_edge(base, "+x")])
        self.doc.removeObject(base.Name)
        self.refused(p, "The selected part " + DELETED)

    def test_convert_solid_source_deleted(self):
        src = FA.box_source(self.doc)
        fixed, bends, opens = FA.tray(src)
        p = EP.ConvertSolidPanel(self.doc, source=src, fixed=fixed, bends=bends, opens=opens)
        self.doc.removeObject(src.Name)
        self.refused(p, DELETED)


class TestDocumentClosed(Base):
    def test_accepting_after_the_document_was_closed_is_a_quiet_no_op(self):
        base = FA.plate(self.doc)
        ep = EP.EdgeFlangePanel(self.doc, base=base, edges=[FA.end_edge(base, "+x")])
        t, e = FB.tray(self.doc)
        sp = SP.CornerReliefPanel(self.doc, base=t, edge=e)
        FreeCAD.closeDocument(self.doc.Name)
        with FB.capture_output() as cap:
            self.assertTrue(ep.accept())
            self.assertTrue(sp.accept())
        self.assertNotIn("Traceback", cap.text)


class TestOversizedRelief(Base):
    def test_a_relief_larger_than_the_part_names_the_reason(self):
        t, e = FB.tray(self.doc)
        p = SP.CornerReliefPanel(self.doc, base=t, edge=e)
        p.size.setValue(1e5)
        self.refused(p, "larger than")
        with self.assertRaises(CR.ReliefError):
            CR.build_corner_relief(t.Shape.copy(), e, "Round", 1e5)


class TestMakeFunctionsAttachViewProviders(Base):
    def test_every_make_function_gives_its_feature_a_view_provider(self):
        doc = self.doc
        plate = FA.plate(doc)
        flange = EF.make_edge_flange(doc, plate, [FA.end_edge(plate, "+x")], length=10.0, angle=90.0, radius=2.0)
        plate2 = FA.plate(doc, name="Plate2")
        hem = HM.make_hem(doc, plate2, [FA.end_edge(plate2, "+x")])
        plate3 = FA.plate(doc, name="Plate3")
        jog = JG.make_jog(doc, plate3, FA.face_with_normal(plate3, 2, 1), FA.line_sketch(doc, 30.0, 2.0))
        src = FA.box_source(doc)
        fixed, bends, opens = FA.tray(src)
        conv = CS.make_convert_solid(doc, src, fixed, bends, opens, thickness=2.0)
        t, e = FB.tray(doc)
        relief = CR.make_corner_relief(doc, t, e, size=4.0)
        base, fn, face = FB.plate(doc)
        cut = LC.make_lightening_cutout(doc, base, fn, FB.circle_sketch(doc, face, 5))
        a, b = FB.profiles(doc, 0)
        loft = LB.make_lofted_bend(doc, a, b)
        want = [(flange, EVP.EdgeFlangeVP), (hem, EVP.HemVP), (jog, EVP.JogVP), (conv, EVP.ConvertSolidVP),
                (relief, SVP.CornerReliefVP), (cut, SVP.LighteningCutoutVP), (loft, SVP.LoftedBendVP)]
        for obj, cls in want:
            self.assertIs(type(obj.ViewObject.Proxy), cls, obj.Name)
