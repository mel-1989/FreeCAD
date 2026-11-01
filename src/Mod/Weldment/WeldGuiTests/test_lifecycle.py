# SPDX-License-Identifier: LGPL-2.1-or-later
"""Slice 6a checks 2, 3 and 5: transactions, editing through the view providers, reopening, commands and icons."""

import os
import tempfile
import unittest
import xml.dom.minidom

import FreeCAD
import FreeCADGui as Gui
import Part

import WeldCommands
import WeldEndCap
import WeldGusset
import WeldMember
import WeldPanels as P
import WeldTrim
import WeldViewProviders as VP

from . import fixture as F
from .test_panels import (BEAD_PROPS, CAP_PROPS, GUSSET_PROPS, MEMBER_PROPS, TRIM_PROPS, Base)

V = FreeCAD.Vector


def member_panel(doc):
    path = F.path_object(doc, "Path", [((0, 0, 0), (1200, 0, 0))])
    return P.MemberPanel(doc, path=[(path, [""])])


def corner_panel(doc):
    a, b = F.joint(doc, 0.3)
    return P.CornerPanel(doc, a=a, b=b, kind="Mitre")


def gusset_panel(doc):
    a, b = F.joint(doc)
    return P.GussetPanel(doc, a=a, b=b)


def endcap_panel(doc):
    a, _b = F.joint(doc)
    return P.EndCapPanel(doc, base=a)


def bead_panel(doc):
    plate, wall, edges = F.bead_world(doc)
    return P.BeadPanel(doc, bodies=[plate, wall], edges=[(wall, e) for e in edges])


#: one panel per feature kind, built on a fresh scene in ``doc`` with valid input
PANEL_MAKERS = {"member": member_panel, "corner": corner_panel, "gusset": gusset_panel, "endcap": endcap_panel,
                "bead": bead_panel}


class TestTransactions(Base):
    def test_cancel_changes_nothing(self):
        for kind, make in PANEL_MAKERS.items():
            with self.subTest(kind):
                F.close_everything()
                self.doc = F.new_doc("Panel")
                FreeCAD.setActiveDocument(self.doc.Name)
                panel = make(self.doc)
                self.doc.recompute()
                before = F.snapshot(self.doc)
                self.assertTrue(panel.reject())
                self.assertEqual(F.snapshot(self.doc), before)

    def test_ok_is_one_transaction_and_one_undo_removes_the_feature(self):
        for kind, make in PANEL_MAKERS.items():
            with self.subTest(kind):
                F.close_everything()
                self.doc = F.new_doc("Panel")
                FreeCAD.setActiveDocument(self.doc.Name)
                panel = make(self.doc)
                self.doc.recompute()
                self.doc.openTransaction("scene")
                self.doc.commitTransaction()
                n0, u0 = len(self.doc.Objects), self.doc.UndoCount
                self.assertTrue(panel.accept(), panel.error_text())
                added = len(self.doc.Objects) - n0
                self.assertEqual(added, 2 if kind == "corner" else 1)
                self.assertEqual(self.doc.UndoCount, u0 + 1)
                self.doc.undo()
                self.assertEqual(len(self.doc.Objects), n0)
                self.assertEqual(self.doc.UndoCount, u0)
                self.doc.redo()
                self.assertEqual(len(self.doc.Objects), n0 + added)

    def test_a_panel_edit_that_breaks_a_dependent_is_refused(self):
        a, b = F.joint(self.doc)
        g = WeldGusset.make_gusset(self.doc, a, 0, "Start", b, 0, "Start", 100.0, 100.0, 8.0)
        self.doc.recompute()
        self.assertNotIn("Invalid", g.State)
        panel = P.MemberPanel(self.doc, obj=b)
        # swap B's path for a one-metre line far from A: the gusset on the joint can no longer be built
        far = F.path_object(self.doc, "Far", [((5000, 0, 0), (5000, 0, 1000))])
        panel.set_path([(far, [""])])
        before = F.snapshot(self.doc)
        with F.capture_output() as cap:
            ok = panel.accept()
        self.assertFalse(ok)
        self.assertIn("Gusset", panel.error_text())
        self.assertEqual(F.snapshot(self.doc), before)
        self.assertNotIn("Traceback", cap.text)
        self.assertEqual(list(b.SegmentLengths), [1000.0])


class TestEditThroughViewProviders(Base):
    def test_every_feature_has_a_view_provider(self):
        for kind, make in PANEL_MAKERS.items():
            if kind == "member":
                continue
            with self.subTest(kind):
                F.close_everything()
                self.doc = F.new_doc("Panel")
                FreeCAD.setActiveDocument(self.doc.Name)
                panel = make(self.doc)
                self.doc.recompute()
                self.assertTrue(panel.accept(), panel.error_text())
                obj = self.doc.Objects[-1]
                self.assertIn(type(obj.ViewObject.Proxy).__name__, [c.__name__ for c in VP.BY_TYPE.values()])
                self.assertTrue(os.path.isfile(obj.ViewObject.Proxy.getIcon()))

    def test_member_edit_prefills_and_ok_equals_api(self):
        fam, size = F.rhs(0.5)
        path = F.path_object(self.doc, "Path", [((0, 0, 0), (1200, 0, 0))])
        m = WeldMember.make_member(self.doc, fam, size, [(path, [""])], anchor="Top", rotation=15.0, mirror=True)
        self.doc.recompute()
        panel = self.open_edit(m)
        self.assertIsInstance(panel, P.MemberPanel)
        self.assertEqual((panel.family.currentData(), panel.size_value()), (fam, size))
        self.assertEqual(panel.anchor.currentData(), "Top")
        self.assertEqual(panel.rotation.value(), 15.0)
        self.assertTrue(panel.mirror.isChecked())
        self.assertIn(path.Label, panel.path_label.text())
        fam2, size2 = F.rhs(0.8)
        panel.set_size(fam2, size2)
        panel.rotation.setValue(40.0)
        self.assertTrue(panel.accept(), panel.error_text())
        Gui.Control.closeDialog()
        ref = self.ref_doc()
        rpath = F.path_object(ref, "Path", [((0, 0, 0), (1200, 0, 0))])
        want = WeldMember.make_member(ref, fam2, size2, [(rpath, [""])], anchor="Top", rotation=40.0, mirror=True)
        ref.recompute()
        self.assert_same(m, want, MEMBER_PROPS)

    def test_corner_edit_switches_mitre_to_butt(self):
        a, b = F.joint(self.doc, 0.3, frac_b=0.8, b_span=True)    # B is larger and runs through, so A seats on it
        t = WeldTrim.make_trim(self.doc, a, 0, "Start", "Mitre", b, "", 0, "Start")
        self.doc.recompute()
        panel = self.open_edit(t)
        self.assertIsInstance(panel, P.CornerPanel)
        self.assertEqual(panel.kind.currentData(), "Mitre")
        self.assertEqual(panel.end_a.currentData(), "Start")
        panel.kind.setCurrentIndex(panel.kind.findData("ButtA"))
        self.assertTrue(panel.accept(), panel.error_text())
        Gui.Control.closeDialog()
        ref = self.ref_doc()
        ra, rb = F.joint(ref, 0.3, frac_b=0.8, b_span=True)
        want = WeldTrim.make_trim(ref, ra, 0, "Start", "Butt", rb, "", 0, "Start")
        ref.recompute()
        self.assertEqual(t.Kind, "Butt")
        self.assert_same(t, want, TRIM_PROPS)

    def test_gusset_edit_prefills_ok_equals_api_and_cancel_restores(self):
        a, b = F.joint(self.doc)
        g = WeldGusset.make_gusset(self.doc, a, 0, "Start", b, 0, "Start", 120.0, 90.0, 6.0, 10.0, "Inside")
        self.doc.recompute()
        old = F.props(g, GUSSET_PROPS)
        vol0 = g.Shape.Volume
        panel = self.open_edit(g)
        self.assertEqual((panel.leg1.value(), panel.leg2.value(), panel.thickness.value(), panel.chamfer.value()),
                         (120.0, 90.0, 6.0, 10.0))
        self.assertEqual(panel.shape.currentData(), "Chamfered")
        self.assertEqual(panel.placement.currentData(), "Inside")
        # Cancel after typing restores everything
        panel.leg1.setValue(160.0)
        u0 = self.doc.UndoCount
        self.assertTrue(panel.reject())
        Gui.Control.closeDialog()
        self.assertEqual(F.props(g, GUSSET_PROPS), old)
        self.assertEqual(g.Shape.Volume, vol0)
        self.assertEqual(self.doc.UndoCount, u0)
        # OK with a new leg equals the API
        panel = self.open_edit(g)
        panel.leg1.setValue(160.0)
        self.assertTrue(panel.accept(), panel.error_text())
        Gui.Control.closeDialog()
        ref = self.ref_doc()
        ra, rb = F.joint(ref)
        want = WeldGusset.make_gusset(ref, ra, 0, "Start", rb, 0, "Start", 160.0, 90.0, 6.0, 10.0, "Inside")
        ref.recompute()
        self.assert_same(g, want, GUSSET_PROPS)

    def test_gusset_edit_refusal_keeps_old_values(self):
        a, b = F.joint(self.doc)
        g = WeldGusset.make_gusset(self.doc, a, 0, "Start", b, 0, "Start", 100.0, 100.0, 8.0)
        self.doc.recompute()
        old, vol0 = F.props(g, GUSSET_PROPS), g.Shape.Volume
        panel = self.open_edit(g)
        panel.leg1.setValue(1400.0)
        with F.capture_output() as cap:
            self.assertFalse(panel.accept())
        self.assertIn("runs past the face", panel.error_text())
        self.assertNotIn("Traceback", cap.text)
        self.assertTrue(Gui.Control.activeDialog())
        Gui.Control.closeDialog()
        self.assertEqual(F.props(g, GUSSET_PROPS), old)
        self.assertEqual(g.Shape.Volume, vol0)
        self.assertNotIn("Invalid", g.State)

    def test_endcap_edit_prefills_and_ok_equals_api(self):
        a, _b = F.joint(self.doc)
        cap = WeldEndCap.make_end_cap(self.doc, a, 0, "End", 8.0, "Inset", 3.0, 0.0)
        self.doc.recompute()
        panel = self.open_edit(cap)
        self.assertEqual((panel.mode.currentData(), panel.thickness.value(), panel.recess.value(), panel.end.currentData()),
                         ("Inset", 8.0, 3.0, "End"))
        panel.thickness.setValue(10.0)
        panel.recess.setValue(5.0)
        self.assertTrue(panel.accept(), panel.error_text())
        Gui.Control.closeDialog()
        ref = self.ref_doc()
        ra, _ = F.joint(ref)
        want = WeldEndCap.make_end_cap(ref, ra, 0, "End", 10.0, "Inset", 5.0, 0.0)
        ref.recompute()
        self.assert_same(cap, want, CAP_PROPS)

    def test_bead_edit_prefills_and_ok_equals_api(self):
        import WeldBead

        plate, wall, edges = F.bead_world(self.doc)
        bead = WeldBead.make_bead(self.doc, plate, wall, [(wall, e) for e in edges], 5.0)
        self.doc.recompute()
        panel = self.open_edit(bead)
        self.assertEqual((panel.size.value(), panel.mode.currentData(), panel.edge_list.count()), (5.0, "Leg z", 2))
        self.assertEqual(panel.b1_label.text(), plate.Label)
        panel.mode.setCurrentIndex(panel.mode.findData("Throat a"))
        panel.size.setValue(3.5)
        self.assertTrue(panel.accept(), panel.error_text())
        Gui.Control.closeDialog()
        ref = self.ref_doc()
        rplate, rwall, redges = F.bead_world(ref)
        want = WeldBead.make_bead(ref, rplate, rwall, [(rwall, e) for e in redges], 3.5, "Throat a")
        ref.recompute()
        self.assert_same(bead, want, BEAD_PROPS)

    def test_double_click_opens_the_panel(self):
        a, b = F.joint(self.doc)
        g = WeldGusset.make_gusset(self.doc, a, 0, "Start", b, 0, "Start", 100.0, 100.0, 8.0)
        self.doc.recompute()
        self.assertTrue(g.ViewObject.Proxy.doubleClicked(g.ViewObject))
        self.assertIsInstance(P.current(), P.GussetPanel)
        Gui.Control.closeDialog()


class TestReopen(Base):
    def roundtrip(self, doc, path):
        doc.saveAs(path)
        FreeCAD.closeDocument(doc.Name)
        return FreeCAD.openDocument(path)

    def test_new_documents_keep_their_view_providers(self):
        a, b = F.joint(self.doc)
        WeldGusset.make_gusset(self.doc, a, 0, "Start", b, 0, "Start", 100.0, 100.0, 8.0)
        self.doc.recompute()
        vols = {o.Name: o.Shape.Volume for o in self.doc.Objects if hasattr(o, "Shape")}
        with tempfile.TemporaryDirectory() as d:
            doc = self.roundtrip(self.doc, os.path.join(d, "new.FCStd"))
            doc.recompute()
            g = doc.getObject("Gusset")
            self.assertEqual(type(g.ViewObject.Proxy).__name__, "GussetVP")
            self.assertEqual({o.Name: o.Shape.Volume for o in doc.Objects if hasattr(o, "Shape")}, vols)
            self.assertTrue(g.ViewObject.Proxy.doubleClicked(g.ViewObject))
            self.assertEqual(P.current().obj.Name, "Gusset")
            Gui.Control.closeDialog()

    def test_documents_saved_without_a_view_provider_still_open(self):
        a, b = F.joint(self.doc)
        g = WeldGusset.make_gusset(self.doc, a, 0, "Start", b, 0, "Start", 100.0, 100.0, 8.0)
        for o in (a, b, g):
            o.ViewObject.Proxy = 0         # how every release before slice 6a saved these features
        self.doc.recompute()
        vols = {o.Name: o.Shape.Volume for o in self.doc.Objects if hasattr(o, "Shape")}
        with tempfile.TemporaryDirectory() as d:
            doc = self.roundtrip(self.doc, os.path.join(d, "old.FCStd"))
            doc.recompute()
            g = doc.getObject("Gusset")
            self.assertNotIn("Invalid", g.State)
            self.assertFalse(type(g.ViewObject.Proxy).__name__.endswith("VP"))
            self.assertEqual({o.Name: o.Shape.Volume for o in doc.Objects if hasattr(o, "Shape")}, vols)
            # the default provider handles a double-click without error
            Gui.getDocument(doc.Name).setEdit("Gusset")
            Gui.getDocument(doc.Name).resetEdit()


class TestCommandsAndIcons(unittest.TestCase):
    def test_every_command_has_a_loadable_icon_and_a_tooltip(self):
        from PySide import QtGui

        self.assertEqual(len(WeldCommands.COMMANDS), 9)
        for name in WeldCommands.COMMANDS:
            with self.subTest(name):
                cmd = Gui.Command.get(name)
                self.assertIsNotNone(cmd, name)
                info = cmd.getInfo()
                self.assertTrue(info["toolTip"].strip(), name)
                self.assertTrue(info["menuText"].strip(), name)
                path = WeldCommands.COMMANDS[name][2]
                svg = os.path.join(WeldCommands.ICON_DIR, path + ".svg")
                self.assertTrue(os.path.isfile(svg), svg)
                icon = QtGui.QIcon(svg)
                self.assertFalse(icon.isNull(), svg)
                self.assertFalse(icon.pixmap(32, 32).isNull(), svg)
                self.assertTrue(info["pixmap"], name)

    def test_icons_are_original_svg_with_the_licence_line(self):
        names = sorted(f for f in os.listdir(WeldCommands.ICON_DIR) if f.endswith(".svg"))
        self.assertEqual(len(names), 10)     # nine commands and the workbench
        self.assertIn("Weldment_Workbench.svg", names)
        for n in names:
            with open(os.path.join(WeldCommands.ICON_DIR, n), encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn("SPDX-License-Identifier: LGPL-2.1-or-later", text, n)
            xml.dom.minidom.parseString(text.encode("utf-8"))
        self.assertEqual(len(set(os.path.getsize(os.path.join(WeldCommands.ICON_DIR, n)) for n in names)), len(names))

    def test_toolbar_and_menu_list_every_command_and_the_corner_modes(self):
        self.assertEqual(sorted(WeldCommands.TOOLBAR), sorted(WeldCommands.COMMANDS))
        menu = [m for m in WeldCommands.MENU if m != "Separator"]
        self.assertEqual(sorted(menu), sorted(WeldCommands.COMMANDS))
        for corner in ("Weldment_Mitre", "Weldment_ButtA", "Weldment_ButtB", "Weldment_TrimToFace"):
            self.assertIn(corner, menu)
        for six in ("Weldment_Member", "Weldment_Gusset", "Weldment_EndCap", "Weldment_WeldBead", "Weldment_CutList"):
            self.assertIn(six, WeldCommands.TOOLBAR)

    def test_workbench_is_registered_with_an_icon(self):
        wb = Gui.getWorkbench("WeldmentWorkbench")
        self.assertIsNotNone(wb)
        self.assertTrue(os.path.isfile(VP.icon_path("Weldment_Workbench")))
        self.assertEqual(wb.MenuText, "Weldment")
        self.assertTrue(os.path.samefile(wb.Icon, VP.icon_path("Weldment_Workbench")))


class TestCommandsOpenPanels(Base):
    def test_commands_open_prefilled_panels_from_the_selection(self):
        a, b = F.joint(self.doc, 0.3)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.doc.Name, a.Name)
        Gui.Selection.addSelection(self.doc.Name, b.Name)
        for cmd, cls in (("Weldment_Mitre", P.CornerPanel), ("Weldment_Gusset", P.GussetPanel)):
            with self.subTest(cmd):
                Gui.runCommand(cmd, 0)
                panel = P.current()
                self.assertIsInstance(panel, cls)
                self.assertEqual(panel.a.Name, a.Name)
                self.assertEqual(panel.b.Name, b.Name)
                Gui.Control.closeDialog()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.doc.Name, a.Name)
        Gui.runCommand("Weldment_EndCap", 0)
        self.assertIsInstance(P.current(), P.EndCapPanel)
        Gui.Control.closeDialog()
        Gui.runCommand("Weldment_CutList", 0)
        self.assertIsInstance(P.current(), P.CutListPanel)
        Gui.Control.closeDialog()
        Gui.Selection.clearSelection()
