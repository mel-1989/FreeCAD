# SPDX-License-Identifier: LGPL-2.1-or-later
"""Slice 6b lane B checks 2 and 5: the corner relief, lightening cutout and lofted bend task panels, driven the way a
click would (set widgets, accept).

Oracle: the panel's OK must give a feature whose properties equal those of the Python API call with the same
parameters and whose volume matches it within 1e-9 relative; the reference is built in a second document by calling
the API directly (the panel code is never used for an expected value). A refusal shows text in the panel, keeps it
open and leaves the document, the undo stack and every shape untouched, with no Python traceback anywhere."""

import unittest

import FreeCAD
import FreeCADGui as Gui

import SheetMetalBendCornerRelief as CR
import SheetMetalLighteningCutout as LC
import SheetMetalLoftedBend as LB
import SMShapeCommands  # noqa: F401  (registers the commands)
import SMShapePanels as P

from . import fixture_b as F

V = FreeCAD.Vector
REL = 1e-9

RELIEF_PROPS = ["reliefType", "size", "sizeMode", "ratio", "rotation", "kfactor", "kfromPart", "kUsed",
                "flatVolumeOffset", "bendRadii", "thickness", "sizeUsed"]
CUTOUT_PROPS = ["Pattern", "CountX", "CountY", "PitchX", "PitchY", "Direction", "MinEdgeDistance", "thickness"]
LOFT_PROPS = ["thickness", "side", "facets", "alignment", "method", "invertSides", "triangleCount", "bendCount",
              "flatVolumeOffset", "midSurfaceArea", "facetVolume", "overlapVolume"]


def pick(combo, data):
    i = combo.findData(data)
    assert i >= 0, data
    combo.setCurrentIndex(i)


class Base(unittest.TestCase):
    def setUp(self):
        self.doc = F.new_doc("Panel")
        FreeCAD.setActiveDocument(self.doc.Name)

    def tearDown(self):
        F.close_everything()

    def ref_doc(self):
        doc = F.new_doc("Ref")
        FreeCAD.setActiveDocument(self.doc.Name)
        return doc

    def created(self, before, type_name):
        new = [o for o in self.doc.Objects if o.Name not in before and P.type_name(o) == type_name]
        self.assertEqual(len(new), 1, [o.Name for o in new])
        return new[0]

    def assert_same(self, got, want, names):
        self.assertEqual(F.props(got, names), F.props(want, names))
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


# ---------------------------------------------------------------- corner relief

class TestCornerReliefPanel(Base):
    SETS = [
        dict(T=2.0, r_t=1.0, kind="Round", size=3.0, mode="Absolute", ratio=2.0, rot=0.0, from_part=False, k=0.42),
        dict(T=1.5, r_t=2.0, kind="Obround", size=2.0, mode="Thickness", ratio=3.0, rot=30.0, from_part=True, k=0.4),
    ]

    def test_ok_equals_api_two_parameter_sets(self):
        for i, s in enumerate(self.SETS):
            with self.subTest(set=i):
                t, edge = F.tray(self.doc, s["T"], s["r_t"])
                before = {o.Name for o in self.doc.Objects}
                panel = P.CornerReliefPanel(self.doc, base=t, edge=edge)
                pick(panel.kind, s["kind"])
                panel.size.setValue(s["size"])
                pick(panel.size_mode, s["mode"])
                panel.ratio.setValue(s["ratio"])
                panel.rotation.setValue(s["rot"])
                panel.from_part.setChecked(s["from_part"])
                panel.kfactor.setValue(s["k"])
                self.assertTrue(panel.accept(), panel.error_text())
                got = self.created(before, "SMBendCornerRelief")
                ref = self.ref_doc()
                rt, redge = F.tray(ref, s["T"], s["r_t"])
                want = CR.make_corner_relief(ref, rt, redge, reliefType=s["kind"], size=s["size"], sizeMode=s["mode"],
                                             ratio=s["ratio"], rotation=s["rot"], kfactor=s["k"],
                                             kfromPart=s["from_part"])
                self.assert_same(got, want, RELIEF_PROPS)

    def test_edge_that_is_not_a_two_bend_corner_is_refused(self):
        t, edge = F.tray(self.doc)
        panel = P.CornerReliefPanel(self.doc, base=t, edge=F.other_edge(t.Shape, [edge]))
        self.refuse(panel, "pick the corner edge")

    def test_size_below_the_thickness_is_refused_with_the_reason(self):
        t, edge = F.tray(self.doc, 2.0)
        panel = P.CornerReliefPanel(self.doc, base=t, edge=edge)
        panel.size.setValue(0.5)
        self.refuse(panel, "smaller than the thickness")

    def test_nothing_selected_is_a_readable_refusal(self):
        panel = P.CornerReliefPanel(self.doc)
        self.refuse(panel, "select the sheet-metal solid")

    def test_ratio_is_enabled_for_the_obround_only(self):
        t, edge = F.tray(self.doc)
        panel = P.CornerReliefPanel(self.doc, base=t, edge=edge)
        self.assertFalse(panel.ratio.isEnabled())
        pick(panel.kind, "Obround")
        self.assertTrue(panel.ratio.isEnabled())
        panel.from_part.setChecked(True)
        self.assertFalse(panel.kfactor.isEnabled())


# ---------------------------------------------------------------- lightening cutout

class TestLighteningCutoutPanel(Base):
    def test_ok_equals_api_two_parameter_sets(self):
        sets = [dict(T=2.0, r=5.0, pattern="None", cx=1, cy=1, px=10.0, py=10.0, d=(1, 0, 0), m=0.0),
                dict(T=3.0, r=4.0, pattern="Grid", cx=3, cy=2, px=16.0, py=14.0, d=(1, 0, 0), m=0.0),
                dict(T=2.0, r=3.0, pattern="Linear", cx=3, cy=1, px=10.0, py=10.0, d=(0, 1, 0), m=4.0)]
        for i, s in enumerate(sets):
            with self.subTest(set=i):
                pl, fname, face = F.plate(self.doc, s["T"])
                sk = F.circle_sketch(self.doc, face, s["r"])
                before = {o.Name for o in self.doc.Objects}
                panel = P.LighteningCutoutPanel(self.doc, base=pl, face=fname, sketch=sk)
                pick(panel.pattern, s["pattern"])
                panel.count_x.setValue(s["cx"])
                panel.count_y.setValue(s["cy"])
                panel.pitch_x.setValue(s["px"])
                panel.pitch_y.setValue(s["py"])
                panel.dir_x.setValue(s["d"][0])
                panel.dir_y.setValue(s["d"][1])
                panel.dir_z.setValue(s["d"][2])
                panel.min_edge.setValue(s["m"])
                self.assertTrue(panel.accept(), panel.error_text())
                got = self.created(before, "SMLighteningCutout")
                ref = self.ref_doc()
                rpl, rname, rface = F.plate(ref, s["T"])
                rsk = F.circle_sketch(ref, rface, s["r"])
                want = LC.make_lightening_cutout(ref, rpl, rname, rsk, Pattern=s["pattern"], CountX=s["cx"],
                                                 CountY=s["cy"], PitchX=s["px"], PitchY=s["py"],
                                                 Direction=V(*s["d"]), MinEdgeDistance=s["m"])
                self.assert_same(got, want, CUTOUT_PROPS)

    def test_cutout_that_crosses_a_bend_is_refused(self):
        base, fname, face = F.bent_part(self.doc)
        sk = F.sketch_near_bend(self.doc, base, face, gap=-2.0, r=6.0)
        panel = P.LighteningCutoutPanel(self.doc, base=base, face=fname, sketch=sk)
        self.refuse(panel, "reaches beyond")

    def test_too_little_clearance_is_refused(self):
        pl, fname, face = F.plate(self.doc)
        sk = F.circle_sketch(self.doc, face, 5.0, du=-30.0)    # 10 mm from the short edge
        panel = P.LighteningCutoutPanel(self.doc, base=pl, face=fname, sketch=sk)
        panel.min_edge.setValue(20.0)
        self.refuse(panel, "below the minimum")

    def test_overlapping_copies_are_refused(self):
        pl, fname, face = F.plate(self.doc)
        sk = F.circle_sketch(self.doc, face, 5.0, du=-20.0)
        panel = P.LighteningCutoutPanel(self.doc, base=pl, face=fname, sketch=sk)
        pick(panel.pattern, "Linear")
        panel.count_x.setValue(3)
        panel.pitch_x.setValue(6.0)
        self.refuse(panel, "overlap")

    def test_missing_sketch_is_a_readable_refusal(self):
        pl, fname, face = F.plate(self.doc)
        panel = P.LighteningCutoutPanel(self.doc, base=pl, face=fname)
        self.refuse(panel, "closed profile sketch")

    def test_pattern_controls_follow_the_pattern(self):
        pl, fname, face = F.plate(self.doc)
        panel = P.LighteningCutoutPanel(self.doc, base=pl, face=fname)
        self.assertFalse(panel.count_x.isEnabled())
        pick(panel.pattern, "Linear")
        self.assertTrue(panel.count_x.isEnabled())
        self.assertFalse(panel.count_y.isEnabled())
        pick(panel.pattern, "Grid")
        self.assertTrue(panel.count_y.isEnabled())


# ---------------------------------------------------------------- lofted bend

class TestLoftedBendPanel(Base):
    SETS = [dict(case=0, T=2.0, side="mid", facets=4, alignment="auto", invert=False),
            dict(case=2, T=1.0, side="outside", facets=6, alignment="auto", invert=True),
            dict(case=1, T=1.5, side="inside", facets=4, alignment="same", invert=False)]

    def test_ok_equals_api_parameter_sets(self):
        for i, s in enumerate(self.SETS):
            with self.subTest(set=i):
                a, b = F.profiles(self.doc, s["case"])
                before = {o.Name for o in self.doc.Objects}
                panel = P.LoftedBendPanel(self.doc, profile_a=a, profile_b=b)
                panel.thickness.setValue(s["T"])
                pick(panel.side, s["side"])
                panel.facets.setValue(s["facets"])
                pick(panel.alignment, s["alignment"])
                panel.invert.setChecked(s["invert"])
                self.assertTrue(panel.accept(), panel.error_text())
                got = self.created(before, "SMLoftedBend")
                ref = self.ref_doc()
                ra, rb = F.profiles(ref, s["case"])
                want = LB.make_lofted_bend(ref, ra, rb, thickness=s["T"], side=s["side"], facets=s["facets"],
                                           alignment=s["alignment"], invertSides=s["invert"])
                self.assert_same(got, want, LOFT_PROPS)

    def test_profiles_on_non_parallel_planes_are_refused(self):
        a, b = F.tilted_profile(self.doc)
        panel = P.LoftedBendPanel(self.doc, profile_a=a, profile_b=b)
        self.refuse(panel, "not parallel")

    def test_smooth_method_is_refused(self):
        a, b = F.profiles(self.doc, 0)
        panel = P.LoftedBendPanel(self.doc, profile_a=a, profile_b=b)
        pick(panel.method, "smooth")
        self.refuse(panel, "not supported")

    def test_zero_thickness_is_refused(self):
        a, b = F.profiles(self.doc, 0)
        panel = P.LoftedBendPanel(self.doc, profile_a=a, profile_b=b)
        panel.thickness.setValue(0.0)
        self.refuse(panel, "thickness")

    def test_same_profile_twice_and_nothing_selected_are_refused(self):
        a, _b = F.profiles(self.doc, 0)
        self.refuse(P.LoftedBendPanel(self.doc, profile_a=a, profile_b=a), "two different objects")
        self.refuse(P.LoftedBendPanel(self.doc), "select the two profiles")


if __name__ == "__main__":
    unittest.main()
