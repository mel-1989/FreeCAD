# SPDX-License-Identifier: LGPL-2.1-or-later
"""G08a slice 2: the command palette (Std_CommandPalette), needs FreeCADGui.

Checks 1 and 5 of the brief: coverage of the shipped index, running a command of a workbench that is not the active one
(the workbench is activated first), the one shortcut and its freedom from conflicts, Up/Down/Enter/Escape, a greyed command
that runs nothing and says why, recent commands first on an empty query. measure_latency() is the check-4 measurement; the
workshop's harness/gui/palette_bench.py calls it in a fresh session and prints the numbers."""

import json
import os
import statistics
import time
import unittest

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

from AgentCore.palette import index as pidx
from AgentCore.palette import widget as pw

SHORTCUT = "Ctrl+Shift+K"


def pump(n=4):
    for _ in range(n):
        Gui.updateGui()


def norm(seq):
    return QtGui.QKeySequence(seq).toString(QtGui.QKeySequence.PortableText).lower()


def key(dlg, k, text=""):
    ev = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, k, QtCore.Qt.NoModifier, text)
    QtWidgets.QApplication.sendEvent(dlg.edit, ev)


def type_query(dlg, text):
    dlg.edit.setText(text)     # textChanged -> refill, the same path as typing
    pump(1)


class Recorder:
    """Replaces Gui.runCommand for a while and records the names it is asked to run."""

    def __enter__(self):
        self.calls = []
        self._orig = Gui.runCommand
        Gui.runCommand = lambda name, *a: self.calls.append(name)
        return self

    def __exit__(self, *exc):
        Gui.runCommand = self._orig


def measure_latency(queries, opens=20):
    """Check 4. Returns dict: first open (cold model, shortcut to first paint), median/p95 of `opens` reopens, and the
    keystroke-to-updated-list cost (setText to the refilled list) over every prefix of every query."""
    out = {}
    if pw._dialog is not None:
        pw._dialog.hide()
        pw._dialog = None
    pw.last_open.clear()
    t0 = time.perf_counter()
    Gui.runCommand("Std_CommandPalette")
    deadline = time.perf_counter() + 3
    while "first_paint" not in pw.last_open and time.perf_counter() < deadline:
        pump(1)
    out["first_open_ms"] = round((pw.last_open.get("first_paint", time.perf_counter()) - t0) * 1000, 1)
    out["commands_indexed"] = len(pw._dialog.model.entries)
    times = []
    for _ in range(opens):
        pw._dialog.hide()
        pump(2)
        pw.last_open.clear()
        t0 = time.perf_counter()
        Gui.runCommand("Std_CommandPalette")
        deadline = time.perf_counter() + 2
        while "first_paint" not in pw.last_open and time.perf_counter() < deadline:
            pump(1)
        times.append((pw.last_open.get("first_paint", time.perf_counter()) - t0) * 1000)
    out["open_median_ms"] = round(statistics.median(times), 1)
    out["open_p95_ms"] = round(sorted(times)[min(len(times) - 1, int(round(0.95 * (len(times) - 1))))], 1)
    out["open_samples_ms"] = [round(t, 1) for t in times]
    ks = []
    dlg = pw._dialog
    for q in queries:
        for k in range(1, len(q) + 1):
            t = time.perf_counter()
            dlg.edit.setText(q[:k])
            ks.append((time.perf_counter() - t) * 1000)
    ks.sort()
    out["keystroke_n"] = len(ks)
    out["keystroke_median_ms"] = round(statistics.median(ks), 2)
    out["keystroke_p95_ms"] = round(ks[int(round(0.95 * (len(ks) - 1)))], 2)
    out["keystroke_max_ms"] = round(ks[-1], 2)
    dlg.hide()
    return out


class TestCommandPalette(unittest.TestCase):
    def setUp(self):
        for name in list(App.listDocuments()):
            App.closeDocument(name)
        App.ParamGet(pw.PARAM).SetString("Recent", "")
        if pw._dialog is not None:
            pw._dialog.hide()

    def view_doc(self):
        """A document with a 3D view: the view commands are active only then."""
        doc = App.newDocument("PaletteView")
        doc.addObject("Part::Box", "Box")
        doc.recompute()
        pump()
        return doc

    def tearDown(self):
        if pw._dialog is not None:
            pw._dialog.hide()
        Gui.Control.closeDialog()
        for name in list(App.listDocuments()):
            App.closeDocument(name)

    # -- check 1
    def test_index_coverage(self):
        entries, labels = pidx.load_shipped()
        names = [e["name"] for e in entries]
        self.assertEqual(len(names), len(set(names)))
        self.assertGreaterEqual(len(entries), 1100)
        self.assertIn("Std_CommandPalette", names)
        by_wb = {}
        for e in entries:
            by_wb.setdefault(e["wb"], []).append(e["name"])
        for wb in ("PartDesignWorkbench", "SketcherWorkbench", "SMWorkbench", "AssemblyWorkbench", "TechDrawWorkbench", "BIMWorkbench",
                   "CAMWorkbench", "FemWorkbench", "DraftWorkbench"):
            self.assertTrue(by_wb.get(wb), wb)
            self.assertIn(wb, labels)
        # everything registered right now is in the shipped index (the freshness check in palette_index.py covers the rest)
        missing = [n for n in Gui.listCommands() if n not in set(names) and n.split("_", 1)[0] in pidx.WB_BY_PREFIX]
        self.assertEqual(missing, [])

    def test_run_from_neutral_workbench(self):
        """From the blank workbench, run one command each of PartDesign, Sketcher, SheetMetal and Assembly through the palette."""
        Gui.activateWorkbench("NoneWorkbench")
        doc = App.newDocument("PaletteRun")
        m = pw.Model()
        m.refresh()

        def run(name):
            e = m.entries[m.by_name[name]]
            self.assertNotEqual(pw.current_workbench(), e["wb"], name)
            self.assertTrue(pw.run_entry(e, m), name)
            pump()
            self.assertEqual(pw.current_workbench(), e["wb"], name)

        run("PartDesign_Body")
        self.assertEqual([o.TypeId for o in doc.Objects if o.TypeId == "PartDesign::Body"], ["PartDesign::Body"])
        import Part
        sk = doc.addObject("Sketcher::SketchObject", "Sketch")
        sk.addGeometry([Part.LineSegment(App.Vector(0, 0, 0), App.Vector(10, 0, 0)), Part.LineSegment(App.Vector(10, 0, 0), App.Vector(10, 10, 0)),
                        Part.LineSegment(App.Vector(10, 10, 0), App.Vector(0, 10, 0)), Part.LineSegment(App.Vector(0, 10, 0), App.Vector(0, 0, 0))])
        doc.recompute()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(doc.Name, sk.Name)
        Gui.activateWorkbench("NoneWorkbench")
        run("Sketcher_EditSketch")
        self.assertIsNotNone(Gui.ActiveDocument.getInEdit())
        Gui.ActiveDocument.resetEdit()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(doc.Name, sk.Name)
        Gui.activateWorkbench("NoneWorkbench")
        n_before = len(doc.Objects)
        run("SheetMetal_AddBase")         # the sketch is selected: opens its panel and adds the base feature
        self.assertGreater(len(doc.Objects), n_before)
        Gui.Control.closeDialog()
        Gui.activateWorkbench("NoneWorkbench")
        run("Assembly_CreateAssembly")
        self.assertTrue(doc.findObjects("Assembly::AssemblyObject"))

    # -- check 5
    def test_shortcut_registered_and_free(self):
        cmd = Gui.Command.get("Std_CommandPalette")
        self.assertEqual(norm(cmd.getShortcut()), norm(SHORTCUT))
        entries, _ = pidx.load_shipped()
        mine = norm(SHORTCUT)

        def clash(k):    # the same sequence, or one that starts with the other (a chord "Ctrl+Shift+K, X" shadows it)
            k = norm(k)
            return k == mine or k.startswith(mine + ",") or mine.startswith(k + ",")

        clashes = [e["name"] for e in entries if e["name"] != "Std_CommandPalette" and e.get("key") and clash(e["key"])]
        live = [n for n in Gui.Command.listByShortcut(SHORTCUT) if n != "Std_CommandPalette"]
        self.assertEqual(clashes, [])
        self.assertEqual(live, [])
        self.assertTrue(any("key" in e for e in entries), "the index records default shortcuts")

    def test_in_tools_menu(self):
        Gui.activateWorkbench("PartDesignWorkbench")   # the blank workbench has no Tools menu
        pump()
        mw = Gui.getMainWindow()
        found = []
        for act in mw.menuBar().actions():
            if act.menu():
                for a in act.menu().actions():
                    if a.objectName() == "Std_CommandPalette":
                        found.append(act.text())
        self.assertTrue(any("Tools" in t for t in found), found)

    def test_command_opens_palette(self):
        Gui.runCommand("Std_CommandPalette")
        pump()
        self.assertTrue(pw._dialog.isVisible())
        self.assertTrue(pw._dialog.edit.hasFocus() or pw._dialog.edit.isVisible())

    def test_keys_up_down_enter_escape(self):
        self.view_doc()
        dlg = pw.open_palette("fit")
        pump()
        self.assertGreaterEqual(dlg.list.count(), 3)
        self.assertEqual(dlg.list.currentRow(), 0)
        key(dlg, QtCore.Qt.Key_Down)
        key(dlg, QtCore.Qt.Key_Down)
        self.assertEqual(dlg.list.currentRow(), 2)
        key(dlg, QtCore.Qt.Key_Up)
        self.assertEqual(dlg.list.currentRow(), 1)
        with Recorder() as rec:
            key(dlg, QtCore.Qt.Key_Escape)
            self.assertFalse(dlg.isVisible())
            self.assertEqual(rec.calls, [])
            dlg = pw.open_palette("fit")
            pump()
            key(dlg, QtCore.Qt.Key_Down)
            want = dlg.shown[1]["name"]
            self.assertIsNone(pw.inactive_reason(dlg.shown[1]))
            key(dlg, QtCore.Qt.Key_Return)
            self.assertFalse(dlg.isVisible())
        self.assertEqual(rec.calls, [want])

    def test_enter_runs_selected_command(self):
        self.view_doc()
        dlg = pw.open_palette("fit all")
        pump()
        self.assertEqual(dlg.shown[0]["name"], "Std_ViewFitAll")
        Gui.activateWorkbench("NoneWorkbench")
        with Recorder() as rec:
            key(dlg, QtCore.Qt.Key_Return)
        self.assertEqual(rec.calls, ["Std_ViewFitAll"])

    def test_inactive_command_is_grey_and_runs_nothing(self):
        dlg = pw.open_palette("horizontal constraint")
        pump()
        row = next(i for i, e in enumerate(dlg.shown) if e["name"] == "Sketcher_ConstrainHorizontal")
        self.assertTrue(pw.inactive_reason(dlg.shown[row]))
        self.assertTrue(dlg.list.item(row).data(QtCore.Qt.UserRole + 1))
        color = dlg.list.item(row).foreground().color()
        self.assertNotEqual(color, dlg.palette().color(QtGui.QPalette.Text))      # dimmed, still readable
        self.assertGreater(color.alpha(), 0)
        dlg.list.setCurrentRow(row)
        Gui.getMainWindow().statusBar().clearMessage()
        with Recorder() as rec:
            key(dlg, QtCore.Qt.Key_Return)
        self.assertEqual(rec.calls, [])
        self.assertIn("not available", Gui.getMainWindow().statusBar().currentMessage())

    def test_recent_commands_first_on_empty_query(self):
        self.view_doc()
        with Recorder():
            for name in ("Std_ViewFitAll", "Std_ViewIsometric", "Std_ViewTop"):
                dlg = pw.open_palette("")
                pw.run_entry(dlg.model.entries[dlg.model.by_name[name]], dlg.model)
        dlg = pw.open_palette("")
        pump()
        self.assertEqual([e["name"] for e in dlg.shown[:3]], ["Std_ViewTop", "Std_ViewIsometric", "Std_ViewFitAll"])

    def test_latency_smoke(self):
        r = measure_latency(["fillet", "new sketch", "bend sheet metal"], opens=5)
        self.assertLess(r["open_p95_ms"], 300)
        self.assertLess(r["keystroke_p95_ms"], 30)
