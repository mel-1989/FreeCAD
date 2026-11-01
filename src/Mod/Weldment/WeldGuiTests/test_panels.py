# SPDX-License-Identifier: LGPL-2.1-or-later
"""Slice 6a checks 1 to 4: the Weldment task panels, driven the way a click would (set widgets, accept, reject).

Oracle: the panel's OK must give a feature whose properties equal those of the Python API call with the same
parameters and whose volume matches it within 1e-9 relative; the reference is built in a second document by
calling the API directly (the panel code is never used for an expected value)."""

import unittest

import FreeCAD
import FreeCADGui as Gui
import Part

import Profiles
import WeldBead
import WeldCommands  # noqa: F401  (registers the commands)
import WeldCutList
import WeldEndCap
import WeldGusset
import WeldMember
import WeldPanels as P
import WeldTrim
from WeldGeom import WeldmentError
from WTests import oracle

from . import fixture as F

V = FreeCAD.Vector
REL = 1e-9

MEMBER_PROPS = ["Family", "Size", "Anchor", "Rotation", "Mirror", "SegmentLengths"]
GUSSET_PROPS = ["SegmentA", "EndA", "SegmentB", "EndB", "Leg1", "Leg2", "Chamfer", "Thickness", "PlatePlacement", "AreaMm2",
                "OutlineLengthMm", "OutlineWidthMm", "Signature"]
CAP_PROPS = ["Segment", "End", "Mode", "Thickness", "Recess", "Chamfer", "AreaMm2", "OutlineLengthMm", "OutlineWidthMm"]
BEAD_PROPS = ["SizeMode", "Size", "LegZ", "Throat", "Length", "EdgeLengths", "EdgeVolumes"]
TRIM_PROPS = ["Segment", "End", "Kind", "OtherSub", "OtherSegment", "OtherEnd"]


class Base(unittest.TestCase):
    def setUp(self):
        self.doc = F.new_doc("Panel")
        FreeCAD.setActiveDocument(self.doc.Name)

    def tearDown(self):
        F.close_everything()

    def ref_doc(self):
        doc = F.new_doc("Ref")
        return doc

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

    def open_edit(self, obj):
        gd = Gui.getDocument(obj.Document.Name)
        gd.setEdit(obj.Name)
        panel = P.current()
        self.assertIsNotNone(panel, "setEdit opened no panel for %s" % obj.Name)
        self.assertEqual(panel.obj.Name, obj.Name)
        return panel


# ---------------------------------------------------------------- check 1 and 4: panels

class TestMemberPanel(Base):
    def test_family_and_search_filter_the_size_list(self):
        panel = P.MemberPanel(self.doc)
        fams = Profiles.families()
        self.assertEqual([panel.family.itemText(i) for i in range(panel.family.count())], list(fams))
        for fam in fams[:3]:
            panel.family.setCurrentIndex(panel.family.findData(fam))
            listed = [panel.sizes.item(i).text() for i in range(panel.sizes.count())]
            self.assertEqual(listed, P.sorted_sizes(fam))
            self.assertEqual(sorted(listed), sorted(Profiles.sizes(fam)))
        sizes = Profiles.sizes(panel.family.currentData())
        panel.search.setText(sizes[-1].lower())
        listed = [panel.sizes.item(i).text() for i in range(panel.sizes.count())]
        self.assertIn(sizes[-1], listed)
        self.assertLess(len(listed), len(sizes))
        panel.search.setText("no such section 99999")
        self.assertEqual(panel.sizes.count(), 0)
        with self.assertRaises(WeldmentError):
            panel.values()

    def test_ok_equals_api_two_parameter_sets(self):
        path = F.path_object(self.doc, "Path", [((0, 0, 0), (1500, 0, 0)), ((1500, 0, 0), (1500, 0, 900))])
        ref = self.ref_doc()
        ref_path = F.path_object(ref, "Path", [((0, 0, 0), (1500, 0, 0)), ((1500, 0, 0), (1500, 0, 900))])
        sets = [(F.rhs(0.5), "Centre", 0.0, False), (F.rhs(0.2), "Top Left", 30.0, True)]
        for i, ((fam, size), anchor, rot, mirror) in enumerate(sets):
            with self.subTest(set=i):
                panel = P.MemberPanel(self.doc, path=[(path, [""])])
                panel.set_size(fam, size)
                panel.anchor.setCurrentIndex(panel.anchor.findData(anchor))
                panel.rotation.setValue(rot)
                panel.mirror.setChecked(mirror)
                self.assertTrue(panel.accept())
                got = self.doc.getObject("Member" if i == 0 else "Member00%d" % i)
                want = WeldMember.make_member(ref, fam, size, [(ref_path, [""])], anchor=anchor, rotation=rot, mirror=mirror)
                ref.recompute()
                self.assert_same(got, want, MEMBER_PROPS)

    def test_selection_fills_the_path(self):
        path = F.path_object(self.doc, "Path", [((0, 0, 0), (1000, 0, 0)), ((1000, 0, 0), (1000, 0, 500))])
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.doc.Name, path.Name, "Edge2")
        panel = P.MemberPanel(self.doc)
        panel.use_sel.click()
        self.assertIn("Edge2", panel.path_label.text())
        self.assertIn(path.Label, panel.path_label.text())
        self.assertTrue(panel.accept())
        m = self.doc.getObject("Member")
        self.assertEqual(len(m.SegmentLengths), 1)
        self.assertAlmostEqual(m.SegmentLengths[0], 500.0, places=6)
        Gui.Selection.clearSelection()

    def test_refusal_curved_path(self):
        circ = self.doc.addObject("Part::Feature", "Circle")
        circ.Shape = Part.makeCircle(300.0)
        self.doc.recompute()
        panel = P.MemberPanel(self.doc, path=[(circ, ["Edge1"])])
        self.refuse(panel, "curved")

    def test_refusal_member_path_without_edges(self):
        panel = P.MemberPanel(self.doc, path=[])
        self.refuse(panel, "empty path")


class TestCornerPanel(Base):
    JOINTS = {"Mitre": dict(frac=0.3), "ButtA": dict(frac=0.3, frac_b=0.8, b_span=True),
              "ButtB": dict(frac=0.8, frac_b=0.3, a_span=True)}

    def corner_pair(self, kind):
        a, b = F.joint(self.doc, **self.JOINTS[kind])
        panel = P.CornerPanel(self.doc, a=a, b=b, kind=kind)
        if kind == "ButtB":
            self.assertEqual(panel.end_b.currentData(), "Start")
        else:
            self.assertEqual(panel.end_a.currentData(), "Start")
        return a, b, panel

    def test_modes_equal_api(self):
        for kind, api in (("Mitre", "Mitre"), ("ButtA", "Butt"), ("ButtB", "ButtB")):
            with self.subTest(kind):
                F.close_everything()
                self.doc = F.new_doc("Panel")
                FreeCAD.setActiveDocument(self.doc.Name)
                a, b, panel = self.corner_pair(kind)
                n0 = len(self.doc.Objects)
                self.assertTrue(panel.accept())
                ref = self.ref_doc()
                ra, rb = F.joint(ref, **self.JOINTS[kind])
                WeldTrim.make_corner(ref, ra, 0, panel.end_a.currentData(), rb, 0, panel.end_b.currentData(), api)
                ref.recompute()
                new = [o for o in self.doc.Objects[n0:] if WeldTrim.is_trim(o)]
                want = [o for o in ref.Objects if WeldTrim.is_trim(o)]
                self.assertEqual(len(new), len(want))
                self.assertEqual(len(new), 2 if kind == "Mitre" else 1)
                for g, w in zip(new, want):
                    self.assert_same(g, w, TRIM_PROPS)

    def test_trim_to_face_equals_api(self):
        a, _b = F.joint(self.doc, 0.3)
        wall = self.doc.addObject("Part::Feature", "Wall")
        wall.Shape = Part.makeBox(20, 600, 600, V(1300, -300, -300))
        self.doc.recompute()
        face = F.face_with_normal(wall, 0, -1)
        panel = P.CornerPanel(self.doc, a=a, face=(wall, face), kind="Face")
        self.assertEqual(panel.end_a.currentData(), "End")
        self.assertTrue(panel.accept())
        got = self.doc.getObject("FaceTrim")
        ref = self.ref_doc()
        ra, _ = F.joint(ref, 0.3)
        rwall = ref.addObject("Part::Feature", "Wall")
        rwall.Shape = Part.makeBox(20, 600, 600, V(1300, -300, -300))
        ref.recompute()
        want = WeldTrim.make_trim(ref, ra, 0, "End", "Face", rwall, face)
        ref.recompute()
        self.assert_same(got, want, TRIM_PROPS)

    def test_refusal_face_the_member_does_not_reach(self):
        a, _b = F.joint(self.doc, 0.3)
        wall = self.doc.addObject("Part::Feature", "Wall")
        wall.Shape = Part.makeBox(20, 500, 500, V(3000, 5000, 5000))
        self.doc.recompute()
        panel = P.CornerPanel(self.doc, a=a, face=(wall, F.face_with_normal(wall, 0, -1)), kind="Face")
        self.refuse(panel, "outside the face")

    def test_refusal_needs_two_members_or_a_face(self):
        a, _b = F.joint(self.doc, 0.3)
        self.refuse(P.CornerPanel(self.doc, a=a, kind="Mitre"), "two members")
        self.refuse(P.CornerPanel(self.doc, a=a, kind="Face"), "planar face")

    def test_refusal_butt_end_not_at_the_joint(self):
        a, b = F.joint(self.doc, 0.3)
        panel = P.CornerPanel(self.doc, a=a, b=b, kind="ButtA")
        panel.end_a.setCurrentIndex(panel.end_a.findData("End"))     # A's far end cannot butt against B
        self.refuse(panel, "")
        self.assertTrue(panel.error_text())


class TestGussetPanel(Base):
    SETS = [dict(leg1=100.0, leg2=100.0, t=8.0, chamfer=0.0, placement="Centred"),
            dict(leg1=150.0, leg2=120.0, t=10.0, chamfer=15.0, placement="Face +")]

    def test_ok_equals_api_two_parameter_sets(self):
        a, b = F.joint(self.doc)
        ref = self.ref_doc()
        ra, rb = F.joint(ref)
        for i, s in enumerate(self.SETS):
            with self.subTest(set=i):
                panel = P.GussetPanel(self.doc, a=a, b=b)
                panel.shape.setCurrentIndex(panel.shape.findData("Chamfered" if s["chamfer"] else "Triangular"))
                panel.leg1.setValue(s["leg1"])
                panel.leg2.setValue(s["leg2"])
                panel.thickness.setValue(s["t"])
                panel.chamfer.setValue(s["chamfer"] or 15.0)
                panel.placement.setCurrentIndex(panel.placement.findData(s["placement"]))
                self.assertTrue(panel.accept())
                got = [o for o in self.doc.Objects if o.Name.startswith("Gusset")][-1]
                want = WeldGusset.make_gusset(ref, ra, 0, "Start", rb, 0, "Start", s["leg1"], s["leg2"], s["t"],
                                              s["chamfer"], s["placement"])
                ref.recompute()
                self.assert_same(got, want, GUSSET_PROPS)

    def test_triangular_ignores_the_chamfer_field(self):
        a, b = F.joint(self.doc)
        panel = P.GussetPanel(self.doc, a=a, b=b)
        panel.chamfer.setValue(40.0)
        self.assertFalse(panel.chamfer.isEnabled())
        self.assertEqual(panel.values()["chamfer"], 0.0)

    def test_refusal_leg_past_the_member_face(self):
        a, b = F.joint(self.doc)
        panel = P.GussetPanel(self.doc, a=a, b=b)
        panel.leg1.setValue(1400.0)
        self.refuse(panel, "runs past the face")

    def test_refusal_without_members(self):
        self.refuse(P.GussetPanel(self.doc), "two members")


class TestEndCapPanel(Base):
    SETS = [dict(end="End", mode="Outset", t=8.0, recess=0.0, chamfer=0.0),
            dict(end="Start", mode="Inset", t=6.0, recess=4.0, chamfer=0.0)]

    def test_ok_equals_api_two_parameter_sets(self):
        a, _b = F.joint(self.doc)
        ref = self.ref_doc()
        ra, _ = F.joint(ref)
        for i, s in enumerate(self.SETS):
            with self.subTest(set=i):
                panel = P.EndCapPanel(self.doc, base=a)
                panel.end.setCurrentIndex(panel.end.findData(s["end"]))
                panel.mode.setCurrentIndex(panel.mode.findData(s["mode"]))
                panel.thickness.setValue(s["t"])
                panel.recess.setValue(s["recess"])
                panel.chamfer.setValue(s["chamfer"])
                self.assertTrue(panel.accept())
                got = [o for o in self.doc.Objects if o.Name.startswith("EndCap")][-1]
                want = WeldEndCap.make_end_cap(ref, ra, 0, s["end"], s["t"], s["mode"], s["recess"], s["chamfer"])
                ref.recompute()
                self.assert_same(got, want, CAP_PROPS)

    def test_recess_is_for_inset_only(self):
        a, _b = F.joint(self.doc)
        panel = P.EndCapPanel(self.doc, base=a)
        panel.recess.setValue(5.0)
        self.assertFalse(panel.recess.isEnabled())
        self.assertEqual(panel.values()["recess"], 0.0)

    def test_refusal_i_section_has_no_cap(self):
        prof = oracle.kind_profile("I", 0.5)
        path = F.path_object(self.doc, "P", [((0, 0, 0), (1000, 0, 0))])
        m = WeldMember.make_member(self.doc, prof.family, prof.size, [(path, [""])], "Beam")
        self.doc.recompute()
        self.refuse(P.EndCapPanel(self.doc, base=m), "open section")

    def test_refusal_no_member(self):
        self.refuse(P.EndCapPanel(self.doc), "member")


class TestBeadPanel(Base):
    SETS = [dict(mode="Leg z", size=5.0), dict(mode="Throat a", size=4.0)]

    def test_ok_equals_api_two_parameter_sets(self):
        plate, wall, edges = F.bead_world(self.doc)
        ref = self.ref_doc()
        rplate, rwall, redges = F.bead_world(ref)
        for i, s in enumerate(self.SETS):
            with self.subTest(set=i):
                panel = P.BeadPanel(self.doc, bodies=[plate, wall], edges=[(wall, e) for e in edges])
                panel.mode.setCurrentIndex(panel.mode.findData(s["mode"]))
                panel.size.setValue(s["size"])
                self.assertEqual(panel.edge_list.count(), 2)
                self.assertTrue(panel.accept())
                got = [o for o in self.doc.Objects if o.Name.startswith("WeldBead")][-1]
                want = WeldBead.make_bead(ref, rplate, rwall, [(rwall, e) for e in redges], s["size"], s["mode"])
                ref.recompute()
                self.assert_same(got, want, BEAD_PROPS)

    def test_hint_shows_the_other_measure(self):
        plate, wall, edges = F.bead_world(self.doc)
        panel = P.BeadPanel(self.doc, bodies=[plate, wall], edges=[(wall, edges[0])])
        panel.size.setValue(5.0)
        self.assertIn("%.3f" % WeldBead.throat_from_leg(5.0), panel.hint.text())

    def test_refusal_edge_not_on_both_bodies(self):
        plate, wall, _edges = F.bead_world(self.doc)
        top = [("Edge%d" % (i + 1)) for i, e in enumerate(wall.Shape.Edges)
               if abs(e.valueAt(e.FirstParameter).z - 100) < 1e-9 and abs(e.valueAt(e.LastParameter).z - 100) < 1e-9
               and abs(e.Length - 200) < 1e-9][0]
        self.refuse(P.BeadPanel(self.doc, bodies=[plate, wall], edges=[(wall, top)]), "does not lie on")

    def test_refusal_nothing_selected(self):
        plate, wall, _e = F.bead_world(self.doc)
        self.refuse(P.BeadPanel(self.doc, bodies=[plate, wall]), "edges")
        self.refuse(P.BeadPanel(self.doc), "two bodies")


class TestCutListPanel(Base):
    def scene(self):
        a, b = F.joint(self.doc)
        cap = WeldEndCap.make_end_cap(self.doc, a, 0, "End", 8.0)
        gus = WeldGusset.make_gusset(self.doc, a, 0, "Start", b, 0, "Start", 100.0, 100.0, 8.0)
        plate, wall, edges = F.bead_world(self.doc)
        bead = WeldBead.make_bead(self.doc, plate, wall, [(wall, e) for e in edges], 5.0)
        self.doc.recompute()
        return [a, b, cap, gus, bead]

    def test_tables_list_members_plates_and_welds(self):
        objs = self.scene()
        panel = P.CutListPanel(self.doc, objects=objs)
        rows = WeldCutList.cut_list(objs)
        self.assertEqual(panel.rows, rows)
        members = [r for r in rows if "type" not in r and r.get("kind") != "weld"]
        plates = [r for r in rows if "type" in r]
        welds = [r for r in rows if r.get("kind") == "weld"]
        self.assertTrue(members and plates and welds)
        for key, want in (("members", members), ("plates", plates), ("welds", welds)):
            table = panel.sections[key][1]
            self.assertEqual(table.rowCount(), len(want), key)
            self.assertFalse(table.isHidden(), key)
        self.assertEqual(panel.sections["members"][1].item(0, 2).text(), members[0]["family"])
        self.assertEqual(panel.sections["plates"][1].item(0, 2).text(), plates[0]["type"])

    def test_csv_export_is_exactly_to_csv(self):
        import os
        import tempfile

        objs = self.scene()
        panel = P.CutListPanel(self.doc, objects=objs)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "out.csv")
            panel.ask_path = lambda: path
            panel.export_button.click()
            with open(path, "rb") as fh:
                got = fh.read()
            ref = os.path.join(d, "ref.csv")
            WeldCutList.export_csv(objs, ref)
            with open(ref, "rb") as fh:
                want = fh.read()
        self.assertEqual(got, want)
        self.assertEqual(got.decode("utf-8"), WeldCutList.to_csv(WeldCutList.cut_list(objs)))

    def test_empty_selection_lists_the_whole_document(self):
        objs = self.scene()
        panel = P.CutListPanel(self.doc)
        self.assertEqual(panel.rows, WeldCutList.cut_list(P.default_cut_objects(self.doc)))
        names = {o.Name for o in P.default_cut_objects(self.doc)}
        self.assertTrue({o.Name for o in objs} <= names)

    def test_refusal_invalid_feature_is_text_and_no_export(self):
        objs = self.scene()
        objs[3].Leg1 = 1400.0
        self.doc.recompute()
        before = F.snapshot(self.doc)
        with F.capture_output():
            panel = P.CutListPanel(self.doc, objects=objs)
        self.assertEqual(panel.rows, [])
        self.assertIn("invalid", panel.error_text())
        self.assertFalse(panel.error.isHidden())
        self.assertFalse(panel.export_button.isEnabled())
        self.assertEqual(F.snapshot(self.doc), before)
