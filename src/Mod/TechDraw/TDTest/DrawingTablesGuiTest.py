# SPDX-License-Identifier: LGPL-2.1-or-later

# G04 slice 9: the commands for the parts list, hole, bend and revision tables and sheet sets, driven through the real GUI (selection,
# Gui.runCommand, the revision panel's widgets by object name, the task dialog's OK and Cancel buttons).
# Run with: FreeCAD -t TestTechDrawGui.DrawingTablesGuiTest
# Public sources: FreeCAD's Gui.Selection, Gui.Control and Gui.Command Python APIs; the expected objects are made by the table modules'
# own APIs on a second, identical document.

import datetime
import os
import time
import unittest

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtGui, QtWidgets

import BendTable as BT
import HoleTable as HT
import PartsList as PL
import RevisionTable as RT
import SheetSet as SS

from TDTest.DimChainTest import Plate
from TDTest.DimSetGuiTest import pump, press

try:
    from AssemblyTests import configuration_corpus as C
except Exception:  # the Assembly module is not built
    C = None

NAMES = {
    "parts": "TechDraw_PartsListInsert",
    "hole": "TechDraw_HoleTableInsert",
    "bend": "TechDraw_BendTableInsert",
    "rev": "TechDraw_RevisionAdd",
    "set": "TechDraw_SheetSetMake",
    "add": "TechDraw_SheetSetAddSheet",
}
A4 = os.path.join("ISO", "A4_Landscape_ISO5457_minimal.svg")
TPL = os.path.join(App.getResourceDir(), "Mod", "TechDraw", "Templates")


def widget(cls, name):
    return Gui.getMainWindow().findChild(cls, name)


def snapshot(doc):
    """Everything a table command can make, comparable between two documents built the same way: type, name, place, sheet cells."""
    out = {}
    for o in doc.Objects:
        item = [o.TypeId]
        if o.TypeId.startswith("TechDraw::DrawView") and hasattr(o, "X"):
            item += [round(o.X.Value, 3), round(o.Y.Value, 3)]
        if o.TypeId == "Spreadsheet::Sheet":
            cells = []
            for r in range(1, 40):
                row = [o.getContents("%s%d" % (c, r)) for c in "ABCDEF"]
                if any(row):
                    cells.append(row)
            item.append(cells)
        if o.TypeId == "TechDraw::DrawPage" and o.Template is not None:
            item.append(sorted(o.Template.EditableTexts.items()))
        out[o.Name] = repr(item).replace(doc.Label, "DOC")  # the title block shows the document's label
    return out


class Warnings:
    def __enter__(self):
        self.got = []
        self.old = App.Console.PrintWarning
        App.Console.PrintWarning = lambda text: self.got.append(text)
        return self.got

    def __exit__(self, *a):
        App.Console.PrintWarning = self.old


class DrawingTablesGuiTest(unittest.TestCase):
    def setUp(self):
        Gui.activateWorkbench("TechDrawWorkbench")  # registers the commands (InitGui imports TechDrawTools)
        self.docs = []
        self.doc = self.newdoc("TablesGui")
        self.p = Plate(self.doc)
        HT.wait_for_geometry(self.p.view)
        pump(0.2)
        Gui.Selection.clearSelection()

    def tearDown(self):
        Gui.Selection.clearSelection()
        if Gui.Control.activeDialog():
            Gui.Control.closeDialog()
            pump()
        for d in self.docs:
            try:
                App.closeDocument(d.Name)
            except Exception:
                pass

    def newdoc(self, name):
        d = App.newDocument(name)
        d.UndoMode = 1
        App.setActiveDocument(d.Name)
        self.docs.append(d)
        return d

    def select(self, *objs, doc=None):
        Gui.Selection.clearSelection()
        for o in objs:
            Gui.Selection.addSelection((doc or self.doc).Name, o.Name)

    def press_cmd(self, key, *objs, doc=None):
        """Select ``objs``, run the command; -> the warning lines it wrote."""
        doc = doc or self.doc
        App.setActiveDocument(doc.Name)
        self.select(*objs, doc=doc)
        with Warnings() as got:
            Gui.runCommand(NAMES[key])
            pump(0.1)
        return got

    def depth(self, doc=None):
        return len((doc or self.doc).UndoNames)

    def add_page(self, doc=None, name="Page"):
        doc = doc or self.doc
        page = doc.addObject("TechDraw::DrawPage", name)
        t = doc.addObject("TechDraw::DrawSVGTemplate", "Template")
        t.Template = os.path.join(TPL, A4)
        page.Template = t
        doc.recompute()
        return page

    def one_line(self, got, key):
        self.assertEqual(len(got), 1, got)
        self.assertTrue(got[0].startswith(NAMES[key] + ": "), got)
        self.assertEqual(got[0].count("\n"), 1, got)
        self.assertTrue(got[0].endswith("\n"))
        self.assertNotIn("Traceback", got[0])

    def assembly_world(self, doc):
        b = C.build_case(doc, "sub_assembly_child")
        doc.recompute()
        page = self.add_page(doc)
        return b, page

    # ---- wiring

    def test_commands_are_wired(self):
        for key, name in NAMES.items():
            info = Gui.Command.get(name).getInfo()
            self.assertTrue(info["menuText"] and info["toolTip"] and info["statusTip"], name)
            self.assertNotEqual(info["menuText"], info["toolTip"])
            self.assertFalse(Gui.Command.get(name).getShortcut(), name)
            self.assertFalse(QtGui.QIcon(":/icons/" + info["pixmap"]).isNull() and QtGui.QIcon(info["pixmap"]).isNull(), name)
        pump(0.3)
        mw = Gui.getMainWindow()
        bars = [b for b in mw.findChildren(QtWidgets.QToolBar) if b.objectName() == "TechDraw Tables"]
        self.assertTrue(bars)
        self.assertEqual(len(bars[0].actions()), 6)
        menus = [m for m in mw.findChildren(QtWidgets.QMenu) if m.title().replace("&", "") == "Tables and sheets"]
        self.assertTrue(menus)
        self.assertEqual(len([a for a in menus[0].actions() if not a.isSeparator()]), 6)

    def test_activity(self):
        for k in ("parts", "hole", "bend", "rev", "set"):
            self.assertTrue(Gui.Command.get(NAMES[k]).isActive(), k)
        self.assertFalse(Gui.Command.get(NAMES["add"]).isActive())
        SS.make(self.doc)
        self.assertTrue(Gui.Command.get(NAMES["add"]).isActive())
        other = self.newdoc("NoPage")
        for k in NAMES:
            self.assertFalse(Gui.Command.get(NAMES[k]).isActive(), k)
        App.setActiveDocument(self.doc.Name)

    def test_a_second_press_while_one_runs_is_ignored(self):
        import TechDrawTools.CommandDrawingTables as CD

        CD._busy = True
        try:
            self.assertFalse(Gui.Command.get(NAMES["hole"]).isActive())
            n = len(self.doc.Objects)
            got = self.press_cmd("hole", self.p.view)
            self.assertEqual((got, len(self.doc.Objects)), ([], n))
        finally:
            CD._busy = False

    # ---- equivalence with the API, undo, repeat

    def check_equivalent(self, label, key, make_selection, api, expect_selected=True, setup=None, repeat=True):
        """Run the command in one document and the API in an identical second one; compare, then undo and repeat."""
        docs = []
        for tag in ("Cmd", "Api"):
            d = self.newdoc(label + tag)
            docs.append((d, setup(d)))
        (da, wa), (db, wb) = docs
        n0, u0 = len(da.Objects), self.depth(da)
        with Warnings() as got:
            App.setActiveDocument(da.Name)
            self.select(*make_selection(wa), doc=da)
            Gui.runCommand(NAMES[key])
            pump(0.1)
        self.assertEqual(got, [], label)
        api(wb, db)
        pump(0.1)
        self.assertEqual(snapshot(da), snapshot(db), label)
        self.assertEqual(self.depth(da), u0 + 1, label + ": one undo step")
        if expect_selected:
            self.assertEqual(len(Gui.Selection.getSelection()), 1, label)
        if not repeat:  # adding a sheet is not idempotent: the second press adds a second sheet
            self.assertEqual(self.press_cmd(key, *make_selection(wa), doc=da), [], label)
            self.assertEqual(self.depth(da), u0 + 2, label)
            return
        # repeat: nothing created, no undo step, one "already has" line
        n1 = len(da.Objects)
        got = self.press_cmd(key, *make_selection(wa), doc=da)
        self.assertEqual((len(da.Objects), self.depth(da)), (n1, u0 + 1), label + ": repeat")
        self.one_line(got, key)
        self.assertIn("already", got[0], label)
        # undo restores the objects
        da.undo()
        da.recompute()
        self.assertEqual(len(da.Objects), n0, label + ": undo")

    def test_hole_table_equals_api(self):
        def setup(d):
            return Plate(d)

        self.check_equivalent("hole", "hole", lambda w: [w.view], lambda w, d: HT.make(w.page, w.view), setup=setup)

    def test_hole_table_on_a_projection_group_item(self):
        """A view inside a projection group is on the group's page (verifier MEDIUM 1, run 099): the page's Views list only the group."""

        def setup(d):
            w = Plate(d)
            grp = d.addObject("TechDraw::DrawProjGroup", "ProjGroup")
            w.page.addView(grp)
            grp.Source = [w.top]
            grp.addProjection("Front")
            d.recompute()
            w.item = next(o for o in grp.Views if o.Type == "Front")
            return w

        self.check_equivalent("holegrp", "hole", lambda w: [w.item], lambda w, d: HT.make(w.page, w.item), setup=setup)

    def test_bend_table_equals_api(self):
        from TDTest.BendTableTest import Built

        self.check_equivalent("bend", "bend", lambda w: [w.view], lambda w, d: BT.make(w.page, w.view), setup=lambda d: Built("g01", doc=d, table=False))

    @unittest.skipIf(C is None, "Assembly module not available")
    def test_parts_list_from_assembly_equals_api(self):
        self.check_equivalent(
            "pl", "parts", lambda w: [w[0].asm], lambda w, d: PL.make(w[1], w[0].asm), setup=lambda d: self.assembly_world(d)
        )

    @unittest.skipIf(C is None, "Assembly module not available")
    def test_parts_list_from_assembly_view_equals_api(self):
        def setup(d):
            b, page = self.assembly_world(d)
            v = d.addObject("TechDraw::DrawViewPart", "AsmView")
            page.addView(v)
            v.Source = [b.asm]
            d.recompute()
            return b, page, v

        self.check_equivalent("plv", "parts", lambda w: [w[2]], lambda w, d: PL.make(w[1], w[0].asm), setup=setup)

    def test_sheet_set_from_two_selected_pages_equals_api(self):
        def setup(d):
            return [self.add_page(d, "P1"), self.add_page(d, "P2"), self.add_page(d, "P3")]

        self.check_equivalent("ss2", "set", lambda w: [w[2], w[0]], lambda w, d: SS.make(d, pages=[w[2], w[0]]), setup=setup)

    def test_sheet_set_from_no_selection_equals_api(self):
        def setup(d):
            return [self.add_page(d, "P1"), self.add_page(d, "P2")]

        self.check_equivalent("ss0", "set", lambda w: [], lambda w, d: SS.make(d), setup=setup, expect_selected=False)

    def test_a_second_sheet_set_press_with_the_set_selected_says_it_already_has_its_pages(self):
        d = self.newdoc("SetTwice")
        pages = [self.add_page(d, "P1"), self.add_page(d, "P2")]
        self.assertEqual(self.press_cmd("set", *pages, doc=d), [])
        sset = SS.drivers(d)[0]
        self.assertEqual([o.Name for o in Gui.Selection.getSelection()], [sset.Name])
        n, u = len(d.Objects), self.depth(d)
        got = []
        for _ in range(2):  # the driver stays selected, so the press can repeat
            got = self.press_cmd("set", sset, doc=d)
            self.one_line(got, "set")
            self.assertIn("%s already has its pages; it is selected" % sset.Label, got[0])
            self.assertEqual((len(d.Objects), self.depth(d)), (n, u))

    def test_add_sheet_equals_api(self):
        def setup(d):
            pages = [self.add_page(d, "P1"), self.add_page(d, "P2")]
            return pages, SS.make(d)

        self.check_equivalent("add", "add", lambda w: [w[0][0]], lambda w, d: SS.add_sheet(w[1]), setup=setup, expect_selected=False, repeat=False)
        # the set driver itself works as the input too
        d = self.newdoc("AddByDriver")
        pages, sset = [self.add_page(d, "P1")], None
        sset = SS.make(d)
        n = len(pages[0].Document.Objects)
        got = self.press_cmd("add", sset, doc=d)
        self.assertEqual(got, [])
        self.assertEqual(len(sset.Pages), 2)
        self.assertEqual(Gui.Selection.getSelection()[0].Name, sset.Pages[1].Name)

    def test_success_leaves_the_new_table_selected(self):
        self.assertEqual(self.press_cmd("hole", self.p.view), [])
        sel = Gui.Selection.getSelection()
        self.assertEqual(len(sel), 1)
        self.assertIn(sel[0].Name, [v.Name for v in self.p.page.Views])
        self.assertNotEqual(sel[0].Name, self.p.view.Name)

    # ---- bad selections

    def test_bad_selections_give_one_warning_line_and_create_nothing(self):
        p = self.p
        box = self.doc.addObject("Part::Box", "Bx")
        v2 = self.doc.addObject("TechDraw::DrawViewPart", "View2")
        p.page.addView(v2)
        v2.Source = [p.top]
        loose = self.doc.addObject("TechDraw::DrawViewPart", "Loose")
        loose.Source = [p.top]
        self.doc.recompute()
        HT.wait_for_geometry(v2)
        page2 = self.add_page(self.doc, "Second")
        SS.make(self.doc, pages=[p.page])
        cases = [
            ("hole", []),
            ("hole", [box]),
            ("hole", [p.page]),
            ("hole", [p.view, v2]),
            ("hole", [loose]),
            ("bend", []),
            ("bend", [p.view]),
            ("bend", [p.page]),
            ("parts", []),
            ("parts", [p.view]),
            ("parts", [box]),
            ("parts", [p.view, v2]),
            ("set", [p.page]),
            ("set", [box]),
            ("add", []),
            ("add", [page2]),
            ("add", [box]),
            ("rev", [box]),
            ("rev", [p.page, page2]),
        ]
        for key, sel in cases:
            n0, u0 = len(self.doc.Objects), self.depth()
            got = self.press_cmd(key, *sel)
            label = "%s %s" % (key, [o.Name for o in sel])
            self.one_line(got, key)
            self.assertEqual((len(self.doc.Objects), self.depth()), (n0, u0), label)
            self.assertFalse(Gui.Control.activeDialog(), label)

    def test_no_page_commands_are_inactive_and_say_why_when_forced(self):
        import TechDrawTools.CommandDrawingTables as CD

        d = self.newdoc("Empty")
        d.addObject("Part::Box", "Bx")
        classes = dict(
            parts=CD.CommandPartsListInsert, hole=CD.CommandHoleTableInsert, bend=CD.CommandBendTableInsert,
            rev=CD.CommandRevisionAdd, set=CD.CommandSheetSetMake, add=CD.CommandSheetSetAddSheet,
        )
        for key, cls in classes.items():
            # Gui.runCommand does nothing for an inactive command; calling Activated by hand still gives one line
            self.assertEqual(self.press_cmd(key, doc=d), [], key)
            with Warnings() as got:
                cls().Activated()
            self.one_line(got, key)
        self.assertEqual((len(d.Objects), self.depth(d)), (1, 0))

    def test_a_page_without_a_template_gives_one_warning_line(self):
        d = self.newdoc("Bare")
        bare = d.addObject("TechDraw::DrawPage", "Bare")
        got = self.press_cmd("rev", bare, doc=d)
        self.one_line(got, "rev")
        self.assertEqual(len(d.Objects), 1)

    # ---- revision panel

    def open_panel(self, page=None):
        got = self.press_cmd("rev", *([page] if page is not None else [self.p.page]))
        self.assertEqual(got, [])
        self.assertIsNotNone(widget(QtWidgets.QLineEdit, "revRev"))
        return widget

    def fill(self, rev="", description="", date=None, approved="", zone=""):
        for name, val in (("revRev", rev), ("revDescription", description), ("revApproved", approved), ("revZone", zone)):
            widget(QtWidgets.QLineEdit, name).setText(val)
        if date is not None:
            widget(QtWidgets.QLineEdit, "revDate").setText(date)

    def message(self):
        return widget(QtWidgets.QLabel, "revMessage").text()

    def test_revision_panel_widgets_and_defaults(self):
        self.open_panel()
        for name in ("revRev", "revDescription", "revDate", "revApproved", "revZone"):
            self.assertIsNotNone(widget(QtWidgets.QLineEdit, name), name)
        self.assertEqual(widget(QtWidgets.QLineEdit, "revDate").text(), datetime.date.today().isoformat())
        self.assertEqual(self.message(), "")
        press(QtWidgets.QDialogButtonBox.Cancel)

    def test_revision_invalid_entries_show_one_sentence_and_change_nothing(self):
        self.open_panel()
        n0, u0 = len(self.doc.Objects), self.depth()
        for rev, desc in (("", "Hole moved"), ("A", ""), ("=1+1", "x"), ("+A", "x"), ("-A", "x"), ("@A", "x")):
            self.fill(rev, desc)
            press(QtWidgets.QDialogButtonBox.Ok)
            self.assertTrue(self.message(), (rev, desc))
            self.assertNotIn("\n", self.message())
            self.assertTrue(Gui.Control.activeDialog(), (rev, desc))
            self.assertEqual((len(self.doc.Objects), self.depth()), (n0, u0), (rev, desc))
        press(QtWidgets.QDialogButtonBox.Cancel)
        self.assertFalse(Gui.Control.activeDialog())

    def test_revision_ok_is_one_undo_step_and_fills_the_template(self):
        page = self.p.page
        u0 = self.depth()
        self.open_panel()
        self.fill("B", "Hole moved", "2026-03-04", "MS", "B3")
        press(QtWidgets.QDialogButtonBox.Ok)
        self.assertFalse(Gui.Control.activeDialog())
        self.assertEqual(self.depth(), u0 + 1)
        rows = RT.read_rows(RT.drivers_on(page)[0].Sheet)
        self.assertEqual(rows[-1], ["B3", "B", "Hole moved", "2026-03-04", "MS"])
        self.assertEqual(page.Template.EditableTexts["revision_index"], "B")
        self.assertEqual(page.Template.EditableTexts["date_of_issue"], "2026-03-04")
        self.assertEqual(Gui.Selection.getSelection()[0].Name, RT.view_of(page).Name)
        self.doc.undo()
        self.doc.recompute()
        self.assertEqual(RT.drivers_on(page), [])

    def test_revision_equals_api_with_and_without_zone(self):
        for zone in ("", "C2"):
            da = self.newdoc("RevCmd" + zone)
            db = self.newdoc("RevApi" + zone)
            pa, pb = self.add_page(da), self.add_page(db)
            App.setActiveDocument(da.Name)
            self.select(pa, doc=da)
            Gui.runCommand(NAMES["rev"])
            pump(0.1)
            self.fill("A", "First release", "2026-01-02", "MS", zone)
            press(QtWidgets.QDialogButtonBox.Ok)
            RT.add(pb, "A", "First release", "2026-01-02", "MS", zone)
            self.assertEqual(snapshot(da), snapshot(db), zone)

    def test_revision_cancel_changes_nothing(self):
        n0, u0 = len(self.doc.Objects), self.depth()
        self.open_panel()
        self.fill("B", "Hole moved")
        press(QtWidgets.QDialogButtonBox.Cancel)
        self.assertFalse(Gui.Control.activeDialog())
        self.assertEqual((len(self.doc.Objects), self.depth()), (n0, u0))
        self.assertEqual(RT.drivers_on(self.p.page), [])

    def test_revision_page_resolution(self):
        # nothing selected, one page: that page; a view selected: its page
        got = self.press_cmd("rev")
        self.assertEqual(got, [])
        self.assertIsNotNone(widget(QtWidgets.QLineEdit, "revRev"))
        press(QtWidgets.QDialogButtonBox.Cancel)
        got = self.press_cmd("rev", self.p.view)
        self.assertEqual(got, [])
        self.fill("A", "x")
        press(QtWidgets.QDialogButtonBox.Ok)
        self.assertEqual(len(RT.drivers_on(self.p.page)), 1)
        # two pages, nothing selected and none open: one warning line
        self.add_page(self.doc, "Second")
        got = self.press_cmd("rev")
        if not Gui.Control.activeDialog():
            self.one_line(got, "rev")
        else:
            press(QtWidgets.QDialogButtonBox.Cancel)
