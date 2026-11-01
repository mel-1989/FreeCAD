# SPDX-License-Identifier: LGPL-2.1-or-later
"""Slice 6b checks 2 and 5 (lane A): the edge flange, hem, convert-from-solid and jog panels, driven the way a click would.

Oracle: the panel's OK must give a feature whose properties equal those of the Python API call with the same
parameters and whose volume matches it within 1e-9 relative; the reference is built in a second document by calling
the API directly (the panel code is never used for an expected value). A refusal shows as text in the panel with the
document and undo stack untouched and no traceback in fd 1, fd 2 or the report view."""

import unittest

import FreeCAD
import FreeCADGui as Gui

import SheetMetalConvertSolid as CS
import SheetMetalEdgeFlange as EF
import SheetMetalHemFeature as HM
import SheetMetalJog as JG
import SMEdgeCommands  # noqa: F401  (registers the commands)
import SMEdgePanels as P

from . import fixture_a as F

REL = 1e-9

FLANGE_PROPS = ["length", "angle", "radius", "kfactor", "gap", "flip", "measure", "thickness", "bendAllowance", "baseObject"]
HEM_PROPS = ["hemType", "gap", "length", "kfactor", "flip", "endGap", "thickness", "radius", "bendAllowance", "baseObject"]
JOG_PROPS = ["offset", "offsetType", "angle", "radius", "kfactor", "flip", "fixedSide", "thickness", "radiusUsed",
             "displacement", "bendAllowance", "baseObject"]
CONVERT_PROPS = ["thickness", "radius", "kfactor", "ripGap", "thicknessSide", "bendCount", "baseObject", "bendEdges", "openFaces"]


def renamed(want, got, names):
    """props() of both with the base-object link normalised to the sub names (the documents differ in object names only)."""
    a, b = F.props(got, names), F.props(want, names)
    for d in (a, b):
        for k in ("baseObject", "bendEdges", "openFaces"):
            if k in d:
                d[k] = d[k][1]
    return a, b


class Base(unittest.TestCase):
    def setUp(self):
        self.doc = F.new_doc("Panel")
        FreeCAD.setActiveDocument(self.doc.Name)

    def tearDown(self):
        F.close_everything()

    def ref_doc(self):
        return F.new_doc("Ref")

    def assert_same(self, got, want, names):
        a, b = renamed(want, got, names)
        self.assertEqual(a, b)
        vg, vw = got.Shape.Volume, want.Shape.Volume
        self.assertGreater(vw, 0.0)
        self.assertLessEqual(abs(vg - vw), REL * vw, "volume %r vs %r" % (vg, vw))

    def refuse(self, panel, words):
        """OK must be refused: text in the panel, panel kept open, document untouched, no traceback anywhere."""
        before = F.snapshot(self.doc)
        rv_before = F.report_view_text()
        with F.capture_output() as cap:
            ok = panel.accept()
        self.assertFalse(ok)
        self.assertFalse(panel.done)
        self.assertIn(words, panel.error_text())
        self.assertFalse(panel.error.isHidden())
        self.assertEqual(F.snapshot(self.doc), before)
        self.assertNotIn("Traceback", cap.text)
        rv = F.report_view_text()
        if rv is not None:
            self.assertNotIn("Traceback", rv[len(rv_before or ""):])

    def edge_panel(self, cls, T=2.0, which="+x"):
        base = F.plate(self.doc, T)
        return base, cls(self.doc, base=base, edges=[F.end_edge(base, which)])


class TestEdgeFlangePanel(Base):
    def test_ok_equals_api_two_parameter_sets(self):
        base = F.plate(self.doc, 2.0)
        ref = self.ref_doc()
        rbase = F.plate(ref, 2.0)
        sets = [dict(length=15.0, angle=90.0, radius=2.0, kfactor=0.4, gap=0.0, flip=False, measure="outer", edge="+x"),
                dict(length=22.0, angle=60.0, radius=3.0, kfactor=0.33, gap=2.0, flip=True, measure="inner", edge="-x")]
        for i, s in enumerate(sets):
            with self.subTest(set=i):
                edge = s.pop("edge")
                panel = P.EdgeFlangePanel(self.doc, base=base, edges=[F.end_edge(base, edge)])
                panel.length.setValue(s["length"])
                panel.angle.setValue(s["angle"])
                panel.radius.setValue(s["radius"])
                panel.kfactor.setValue(s["kfactor"])
                panel.gap.setValue(s["gap"])
                panel.flip.setChecked(s["flip"])
                panel.measure.setCurrentIndex(panel.measure.findData(s["measure"]))
                self.assertTrue(panel.accept(), panel.error_text())
                got = self.doc.getObject("EdgeFlange" if i == 0 else "EdgeFlange00%d" % i)
                want = EF.make_edge_flange(ref, rbase, [F.end_edge(rbase, edge)], **s)
                ref.recompute()
                self.assert_same(got, want, FLANGE_PROPS)
                rbase = F.plate(ref, 2.0, "Plate%d" % (i + 1))

    def test_selection_button_takes_base_and_edges(self):
        base = F.plate(self.doc)
        panel = P.EdgeFlangePanel(self.doc)
        self.assertFalse(panel.take_selection())    # nothing selected: a readable message, not an exception
        self.assertIn("select", panel.error_text())
        names = [F.end_edge(base, "+x"), F.end_edge(base, "-x")]
        F.select(base, *names)
        self.assertTrue(panel.take_selection())
        self.assertIs(panel.base, base)
        self.assertEqual(panel.edges, names)
        for n in names:
            self.assertIn(n, panel.sel_label.text())
        self.assertIn(base.Label, panel.sel_label.text())

    def test_refuses_a_curved_edge(self):
        cyl = F.cylinder(self.doc)
        panel = P.EdgeFlangePanel(self.doc, base=cyl, edges=[F.circle_edge(cyl)])
        self.refuse(panel, "not straight")

    def test_refuses_without_a_selection_and_with_a_flange_shorter_than_its_setback(self):
        self.refuse(P.EdgeFlangePanel(self.doc), "select the base part")
        _base, panel = self.edge_panel(P.EdgeFlangePanel)
        panel.length.setValue(1.0)
        panel.radius.setValue(10.0)
        self.refuse(panel, "shorter than the bend setback")


class TestHemPanel(Base):
    def test_ok_equals_api_two_parameter_sets(self):
        base = F.plate(self.doc, 2.0)
        ref = self.ref_doc()
        rbase = F.plate(ref, 2.0)
        sets = [dict(hemType="Closed", gap=2.0, length=10.0, kfactor=0.4, flip=False, endGap=0.0, edge="+x"),
                dict(hemType="Open", gap=4.0, length=12.0, kfactor=0.33, flip=True, endGap=3.0, edge="-x")]
        for i, s in enumerate(sets):
            with self.subTest(set=i):
                edge = s.pop("edge")
                panel = P.HemPanel(self.doc, base=base, edges=[F.end_edge(base, edge)])
                panel.hem_type.setCurrentIndex(panel.hem_type.findData(s["hemType"]))
                panel.gap.setValue(s["gap"])
                panel.length.setValue(s["length"])
                panel.kfactor.setValue(s["kfactor"])
                panel.flip.setChecked(s["flip"])
                panel.end_gap.setValue(s["endGap"])
                self.assertTrue(panel.accept(), panel.error_text())
                got = self.doc.getObject("Hem" if i == 0 else "Hem00%d" % i)
                want = HM.make_hem(ref, rbase, [F.end_edge(rbase, edge)], **s)
                ref.recompute()
                self.assert_same(got, want, HEM_PROPS)
                rbase = F.plate(ref, 2.0, "Plate%d" % (i + 1))

    def test_gap_field_only_matters_for_an_open_hem(self):
        _base, panel = self.edge_panel(P.HemPanel)
        self.assertFalse(panel.gap.isEnabled())
        panel.hem_type.setCurrentIndex(panel.hem_type.findData("Open"))
        self.assertTrue(panel.gap.isEnabled())

    def test_refuses_an_open_hem_with_gap_zero(self):
        _base, panel = self.edge_panel(P.HemPanel)
        panel.hem_type.setCurrentIndex(panel.hem_type.findData("Open"))
        panel.gap.setValue(0.0)
        self.refuse(panel, "gap must be greater than zero")


class TestJogPanel(Base):
    def make_world(self):
        base = F.plate(self.doc, 2.0)
        line = F.line_sketch(self.doc, 30.0, 2.0)
        return base, line, F.face_with_normal(base, 2, 1)

    def test_ok_equals_api_two_parameter_sets(self):
        base, line, face = self.make_world()
        ref = self.ref_doc()
        rbase = F.plate(ref, 2.0)
        rline = F.line_sketch(ref, 30.0, 2.0)
        sets = [dict(offset=8.0, offsetType="outside", angle=90.0, radius=0.0, kfactor=0.4, flip=False, fixedSide="Left"),
                dict(offset=8.0, offsetType="inside", angle=45.0, radius=2.0, kfactor=0.33, flip=True, fixedSide="Right")]
        for i, s in enumerate(sets):
            with self.subTest(set=i):
                panel = P.JogPanel(self.doc, base=base, face=face, sketch=line)
                panel.offset.setValue(s["offset"])
                panel.offset_type.setCurrentIndex(panel.offset_type.findData(s["offsetType"]))
                panel.angle.setValue(s["angle"])
                panel.radius.setValue(s["radius"])
                panel.kfactor.setValue(s["kfactor"])
                panel.flip.setChecked(s["flip"])
                panel.fixed_side.setCurrentIndex(panel.fixed_side.findData(s["fixedSide"]))
                self.assertTrue(panel.accept(), panel.error_text())
                got = self.doc.getObject("Jog" if i == 0 else "Jog00%d" % i)
                rface = F.face_with_normal(rbase, 2, 1)
                want = JG.make_jog(ref, rbase, rface, rline, **s)
                ref.recompute()
                self.assert_same(got, want, JOG_PROPS)
                rbase = F.plate(ref, 2.0, "Plate%d" % (i + 1))

    def test_selection_button_takes_the_face_and_the_sketch(self):
        base, line, face = self.make_world()
        panel = P.JogPanel(self.doc)
        F.select(base, face)
        self.assertFalse(panel.take_selection())    # the sketch is missing
        self.assertIn("sketch", panel.error_text())
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.doc.Name, base.Name, face)
        Gui.Selection.addSelection(self.doc.Name, line.Name)
        self.assertTrue(panel.take_selection(), panel.error_text())
        self.assertIs(panel.base, base)
        self.assertEqual(panel.face, face)
        self.assertIs(panel.sketch, line)
        self.assertIn(face, panel.face_label.text())
        self.assertIn(line.Label, panel.sketch_label.text())

    def test_refuses_an_offset_too_small_for_two_bends(self):
        base, line, face = self.make_world()
        panel = P.JogPanel(self.doc, base=base, face=face, sketch=line)
        panel.offset.setValue(2.5)    # displacement 2.5 mm: two bends of R = T = 2 mm need more
        panel.radius.setValue(3.0)
        self.refuse(panel, "too small for two bends")

    def test_refuses_a_missing_sketch_and_a_line_off_the_face(self):
        base, line, face = self.make_world()
        self.refuse(P.JogPanel(self.doc, base=base, face=face), "select the sketch")
        off = F.line_sketch(self.doc, 30.0, 12.0, "OffLine")    # 10 mm above the face
        self.refuse(P.JogPanel(self.doc, base=base, face=face, sketch=off), "does not lie on the selected face")


class TestConvertSolidPanel(Base):
    def test_ok_equals_api_two_parameter_sets(self):
        src = F.box_source(self.doc)
        ref = self.ref_doc()
        rsrc = F.box_source(ref)
        sets = [(F.u_channel, dict(thickness=1.0, radius=1.0, kfactor=0.42, ripGap=1.0, thicknessSide="Inward")),
                (F.tray, dict(thickness=2.0, radius=2.0, kfactor=0.33, ripGap=1.5, thicknessSide="Outward"))]
        for i, (sel, s) in enumerate(sets):
            with self.subTest(set=i):
                fixed, bends, opens = sel(src)
                panel = P.ConvertSolidPanel(self.doc, source=src, fixed=fixed, bends=bends, opens=opens)
                panel.thickness.setValue(s["thickness"])
                panel.radius.setValue(s["radius"])
                panel.kfactor.setValue(s["kfactor"])
                panel.rip_gap.setValue(s["ripGap"])
                panel.side.setCurrentIndex(panel.side.findData(s["thicknessSide"]))
                self.assertTrue(panel.accept(), panel.error_text())
                got = self.doc.getObject("ConvertSolid" if i == 0 else "ConvertSolid00%d" % i)
                rf, rb, ro = sel(rsrc)
                want = CS.make_convert_solid(ref, rsrc, rf, rb, ro, **s)
                ref.recompute()
                self.assert_same(got, want, CONVERT_PROPS)

    def test_the_three_lists_fill_from_the_selection(self):
        src = F.box_source(self.doc)
        fixed, bends, opens = F.tray(src)
        panel = P.ConvertSolidPanel(self.doc)
        self.assertFalse(panel.take(panel.fixed, "Face", True))    # nothing selected
        self.assertIn("select a face", panel.error_text())
        F.select(src, fixed)
        self.assertTrue(panel.take(panel.fixed, "Face", True))
        self.assertEqual(panel._items(panel.fixed), [fixed])
        F.select(src, *bends)
        self.assertTrue(panel.take(panel.bends, "Edge", False))
        self.assertTrue(panel.take(panel.bends, "Edge", False))    # adding the same edges again lists them once
        self.assertEqual(panel._items(panel.bends), bends)
        F.select(src, *opens)
        self.assertTrue(panel.take(panel.opens, "Face", False))
        self.assertEqual(panel._items(panel.opens), opens)
        self.assertIs(panel.source, src)
        self.assertEqual(panel.source_label.text(), src.Label)
        panel.bends.setCurrentRow(0)
        panel.remove(panel.bends)
        self.assertEqual(panel._items(panel.bends), bends[1:])
        self.refuse(panel, "bend edges")    # a side is cut off from the bottom
        F.select(src, bends[0])
        self.assertTrue(panel.take(panel.bends, "Edge", False))
        self.assertTrue(panel.accept(), panel.error_text())

    def test_a_second_solid_is_refused_in_the_lists(self):
        a, b = F.box_source(self.doc, name="A"), F.box_source(self.doc, name="B", at=(100.0, 0.0, 0.0))
        panel = P.ConvertSolidPanel(self.doc, source=a)
        F.select(b, F.face_with_normal(b, 2, -1))
        self.assertFalse(panel.take(panel.fixed, "Face", True))
        self.assertIn("not to", panel.error_text())

    def test_refuses_a_curved_fixed_face_and_a_missing_fixed_face(self):
        cyl = F.cylinder(self.doc)
        panel = P.ConvertSolidPanel(self.doc, source=cyl, fixed=F.curved_face(cyl))
        self.refuse(panel, "planar")
        src = F.box_source(self.doc)
        self.refuse(P.ConvertSolidPanel(self.doc, source=src), "fixed face")
