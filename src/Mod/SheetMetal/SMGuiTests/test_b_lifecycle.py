# SPDX-License-Identifier: LGPL-2.1-or-later
"""Slice 6b lane B check 4: Cancel, undo and edit for the corner relief, lightening cutout and lofted bend.

Cancel changes nothing (object count, undo stack, volumes); OK is one transaction that one Undo removes; double-click
(``setEdit``) reopens the panel prefilled, and OK after a change recomputes to the API's volume; closing the dialog
directly leaves edit mode; accepting a panel whose object was deleted is a readable refusal. Documents saved before the
panels existed (no view provider proxy) open unchanged."""

import os
import tempfile
import unittest

import FreeCAD
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import SheetMetalBendCornerRelief as CR
import SheetMetalLighteningCutout as LC
import SheetMetalLoftedBend as LB
import SMShapePanels as P
import SMShapeViewProviders as VP

from . import fixture_b as F
from .test_b_panels import CUTOUT_PROPS, LOFT_PROPS, RELIEF_PROPS, Base, pick

REL = 1e-9


def pump():
    for _ in range(4):
        QtWidgets.QApplication.processEvents()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)


class Lifecycle:
    """Mixin: the tests of one feature kind; ``K`` describes it."""

    K = None

    def open_edit(self, obj):
        gd = Gui.getDocument(obj.Document.Name)
        gd.setEdit(obj.Name)
        panel = P.current()
        self.assertIsNotNone(panel, "setEdit opened no panel for %s" % obj.Name)
        self.assertEqual(panel.obj.Name, obj.Name)
        return panel

    def make_api(self, doc, which):
        obj = self.K.api(doc, which)
        VP.attach(obj)
        return obj

    def new_panel(self):
        return self.K.panel(self.doc)

    def test_cancel_changes_nothing(self):
        panel = self.new_panel()
        self.K.fill(panel, "A")
        before = F.snapshot(self.doc)
        self.assertTrue(panel.reject())
        self.assertEqual(F.snapshot(self.doc), before)

    def test_ok_is_one_transaction_and_one_undo_removes_it(self):
        panel = self.new_panel()
        self.K.fill(panel, "A")
        before = F.snapshot(self.doc)
        self.assertTrue(panel.accept(), panel.error_text())
        self.assertEqual(self.doc.UndoCount, before[1] + 1)
        self.assertGreater(len(self.doc.Objects), len(before[0]))
        self.doc.undo()
        after = F.snapshot(self.doc)
        self.assertEqual((after[0], after[1], after[3]), (before[0], before[1], before[3]))
        self.doc.redo()
        self.assertEqual(self.doc.UndoCount, before[1] + 1)

    def test_a_created_feature_has_the_view_provider_with_icon_and_panel(self):
        panel = self.new_panel()
        self.K.fill(panel, "A")
        before = {o.Name for o in self.doc.Objects}
        self.assertTrue(panel.accept())
        obj = self.created(before, self.K.type_name)
        proxy = obj.ViewObject.Proxy
        self.assertIs(type(proxy), self.K.vp)
        self.assertTrue(os.path.isfile(proxy.getIcon()))
        self.assertIs(P.PANELS[proxy.panel], self.K.panel_cls)

    def test_edit_is_prefilled_and_ok_equals_the_api(self):
        obj = self.make_api(self.doc, "A")
        ref = self.ref_doc()
        want_a = self.K.api(ref, "A")
        panel = self.open_edit(obj)
        self.assertEqual(self.K.read(panel), self.K.params["A"])
        self.K.fill(panel, "B")
        before = F.snapshot(self.doc)
        self.assertTrue(panel.accept(), panel.error_text())
        self.assertEqual(self.doc.UndoCount, before[1] + 1)
        self.assertIsNone(Gui.getDocument(self.doc.Name).getInEdit())
        want_b = self.K.api(self.ref_doc(), "B")
        self.assertEqual(F.props(obj, self.K.props), F.props(want_b, self.K.props))
        self.assertNotEqual(F.props(obj, self.K.props), F.props(want_a, self.K.props))
        self.assertLessEqual(abs(obj.Shape.Volume - want_b.Shape.Volume), REL * want_b.Shape.Volume)
        self.doc.undo()
        self.assertEqual(F.props(obj, self.K.props), F.props(want_a, self.K.props))

    def test_edit_cancel_leaves_properties_and_shape_alone(self):
        obj = self.make_api(self.doc, "A")
        ref = self.K.api(self.ref_doc(), "A")
        panel = self.open_edit(obj)
        self.K.fill(panel, "B")
        before = F.snapshot(self.doc)
        self.assertTrue(panel.reject())
        self.assertEqual(F.snapshot(self.doc), before)
        self.assertEqual(F.props(obj, self.K.props), F.props(ref, self.K.props))
        self.assertIsNone(Gui.getDocument(self.doc.Name).getInEdit())

    def test_edit_refusal_keeps_the_panel_open_and_the_feature_unchanged(self):
        obj = self.make_api(self.doc, "A")
        ref = self.K.api(self.ref_doc(), "A")
        panel = self.open_edit(obj)
        self.K.fill(panel, "bad")
        self.refuse(panel, self.K.bad_words)
        self.assertEqual(F.props(obj, self.K.props), F.props(ref, self.K.props))
        self.assertIsNotNone(Gui.getDocument(self.doc.Name).getInEdit())

    def test_closing_the_dialog_directly_leaves_edit_mode(self):
        obj = self.make_api(self.doc, "A")
        self.open_edit(obj)
        gd = Gui.getDocument(self.doc.Name)
        self.assertIsNotNone(gd.getInEdit())
        Gui.Control.closeDialog()
        pump()
        self.assertIsNone(gd.getInEdit())

    def test_accepting_after_the_object_was_deleted_is_a_readable_refusal(self):
        obj = self.make_api(self.doc, "A")
        panel = P.show(self.K.panel_cls(doc=self.doc, obj=obj))    # open, but not through edit mode
        self.doc.removeObject(obj.Name)
        with F.capture_output() as cap:
            ok = panel.accept()
        self.assertFalse(ok)
        self.assertIn("no longer exists", panel.error_text())
        self.assertNotIn("Traceback", cap.text)
        self.assertNotIn("ReferenceError", panel.error_text())

    def test_an_old_document_without_view_provider_proxies_opens_unchanged(self):
        obj = self.K.api(self.doc, "A")
        obj.ViewObject.Proxy = 0    # as saved before slice 6b: the make_* functions now attach a provider with the GUI up
        volume, oname = obj.Shape.Volume, obj.Name
        path = os.path.join(tempfile.mkdtemp(prefix="smgui-"), "old.FCStd")
        self.doc.saveAs(path)
        name = self.doc.Name
        FreeCAD.closeDocument(name)
        doc = FreeCAD.openDocument(path)
        doc.recompute()
        back = doc.getObject(oname)
        self.assertLessEqual(abs(back.Shape.Volume - volume), REL * volume)
        self.assertNotIn("Invalid", back.State)
        self.assertNotIsInstance(back.ViewObject.Proxy, self.K.vp)
        FreeCAD.closeDocument(doc.Name)
        self.doc = F.new_doc("Panel2")


class ReliefKind:
    type_name, props, vp, panel_cls = "SMBendCornerRelief", RELIEF_PROPS, VP.CornerReliefVP, P.CornerReliefPanel
    bad_words = "smaller than the thickness"
    params = {
        "A": dict(reliefType="Round", size=3.0, sizeMode="Absolute", ratio=2.0, rotation=0.0, kfromPart=False, kfactor=0.42),
        "B": dict(reliefType="Square", size=5.0, sizeMode="Absolute", ratio=2.0, rotation=45.0, kfromPart=False, kfactor=0.42),
        "bad": dict(reliefType="Round", size=0.5, sizeMode="Absolute", ratio=2.0, rotation=0.0, kfromPart=False, kfactor=0.42),
    }

    @staticmethod
    def api(doc, which):
        t, edge = F.tray(doc)
        return CR.make_corner_relief(doc, t, edge, **ReliefKind.params[which])

    @staticmethod
    def panel(doc):
        t, edge = F.tray(doc)
        return P.CornerReliefPanel(doc, base=t, edge=edge)

    @staticmethod
    def fill(panel, which):
        p = ReliefKind.params[which]
        pick(panel.kind, p["reliefType"])
        panel.size.setValue(p["size"])
        pick(panel.size_mode, p["sizeMode"])
        panel.ratio.setValue(p["ratio"])
        panel.rotation.setValue(p["rotation"])
        panel.from_part.setChecked(p["kfromPart"])
        panel.kfactor.setValue(p["kfactor"])

    @staticmethod
    def read(panel):
        return dict(reliefType=panel.kind.currentData(), size=panel.size.value(), sizeMode=panel.size_mode.currentData(),
                    ratio=panel.ratio.value(), rotation=panel.rotation.value(), kfromPart=panel.from_part.isChecked(),
                    kfactor=panel.kfactor.value())


class CutoutKind:
    type_name, props, vp, panel_cls = "SMLighteningCutout", CUTOUT_PROPS, VP.LighteningCutoutVP, P.LighteningCutoutPanel
    bad_words = "overlap"
    params = {
        "A": dict(Pattern="None", CountX=1, CountY=1, PitchX=10.0, PitchY=10.0, MinEdgeDistance=0.0),
        "B": dict(Pattern="Grid", CountX=2, CountY=2, PitchX=15.0, PitchY=15.0, MinEdgeDistance=3.0),
        "bad": dict(Pattern="Linear", CountX=3, CountY=1, PitchX=6.0, PitchY=10.0, MinEdgeDistance=0.0),
    }

    @staticmethod
    def api(doc, which):
        pl, fname, face = F.plate(doc)
        sk = F.circle_sketch(doc, face, 5.0)
        return LC.make_lightening_cutout(doc, pl, fname, sk, **CutoutKind.params[which])

    @staticmethod
    def panel(doc):
        pl, fname, face = F.plate(doc)
        return P.LighteningCutoutPanel(doc, base=pl, face=fname, sketch=F.circle_sketch(doc, face, 5.0))

    @staticmethod
    def fill(panel, which):
        p = CutoutKind.params[which]
        pick(panel.pattern, p["Pattern"])
        panel.count_x.setValue(p["CountX"])
        panel.count_y.setValue(p["CountY"])
        panel.pitch_x.setValue(p["PitchX"])
        panel.pitch_y.setValue(p["PitchY"])
        panel.min_edge.setValue(p["MinEdgeDistance"])

    @staticmethod
    def read(panel):
        return dict(Pattern=panel.pattern.currentData(), CountX=panel.count_x.value(), CountY=panel.count_y.value(),
                    PitchX=panel.pitch_x.value(), PitchY=panel.pitch_y.value(), MinEdgeDistance=panel.min_edge.value())


class LoftKind:
    type_name, props, vp, panel_cls = "SMLoftedBend", LOFT_PROPS, VP.LoftedBendVP, P.LoftedBendPanel
    bad_words = "thickness"
    params = {
        "A": dict(thickness=2.0, side="mid", facets=4, alignment="auto", method="faceted", invertSides=False),
        "B": dict(thickness=1.5, side="inside", facets=6, alignment="auto", method="faceted", invertSides=False),
        "bad": dict(thickness=0.0, side="mid", facets=4, alignment="auto", method="faceted", invertSides=False),
    }

    @staticmethod
    def api(doc, which):
        a, b = F.profiles(doc, 3)
        return LB.make_lofted_bend(doc, a, b, **LoftKind.params[which])

    @staticmethod
    def panel(doc):
        a, b = F.profiles(doc, 3)
        return P.LoftedBendPanel(doc, profile_a=a, profile_b=b)

    @staticmethod
    def fill(panel, which):
        p = LoftKind.params[which]
        panel.thickness.setValue(p["thickness"])
        pick(panel.side, p["side"])
        panel.facets.setValue(p["facets"])
        pick(panel.alignment, p["alignment"])
        pick(panel.method, p["method"])
        panel.invert.setChecked(p["invertSides"])

    @staticmethod
    def read(panel):
        return dict(thickness=panel.thickness.value(), side=panel.side.currentData(), facets=panel.facets.value(),
                    alignment=panel.alignment.currentData(), method=panel.method.currentData(),
                    invertSides=panel.invert.isChecked())


class TestCornerReliefLifecycle(Lifecycle, Base):
    K = ReliefKind


class TestLighteningCutoutLifecycle(Lifecycle, Base):
    K = CutoutKind


class TestLoftedBendLifecycle(Lifecycle, Base):
    K = LoftKind


if __name__ == "__main__":
    unittest.main()
