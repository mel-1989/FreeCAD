# SPDX-License-Identifier: LGPL-2.1-or-later
"""G06 slice 2b: the Assembly_ReplaceComponent command and its task panel.

Run with the GUI binary: ``FreeCAD -t TestAssemblyReplaceComponentGui``. The data-safety tests are the important ones:
opening the panel, picking sources, previewing, changing the alignment, picking candidates and cancelling must leave the
document (objects, properties, view providers, undo stack, touched flags, saved file) exactly as it was, and OK must give
exactly what ``ReplaceComponent.apply`` gives for the same choices, in one undo step.
"""

import os
import re
import tempfile
import time
import unittest

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

import ReplaceComponent as R
from AssemblyTests import replace_corpus as C
from AssemblyTests import replace_gui_fixture as F


def pump(seconds=0.2):
    t = time.time()
    while time.time() - t < seconds:
        Gui.updateGui()
        time.sleep(0.02)


def vp_signature(doc):
    """Every view-provider property of every object, as text."""
    out = {}
    for o in doc.Objects:
        vo = getattr(o, "ViewObject", None)
        if vo is None:
            continue
        props = {}
        for p in vo.PropertiesList:
            if p == "Proxy":
                continue
            try:
                props[p] = str(getattr(vo, p))
            except Exception:
                props[p] = "?"
        out[o.Name] = props
    return out


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def norm(x):
    """A signature with random ids (uuids of joints and links) blanked, for comparing two separately built documents."""
    if isinstance(x, dict):
        return {k: norm(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [norm(v) for v in x]
    return _UUID.sub("<uuid>", x) if isinstance(x, str) else x


def full_state(doc):
    gdoc = Gui.getDocument(doc.Name)
    # reading the assembly's Shape or some of its view properties creates a cache property on it, so read them first
    shapes = {o.Name: round(o.Shape.Volume, 6) for o in doc.Objects
              if hasattr(o, "Shape") and not o.Shape.isNull() and o.Shape.Solids}
    vp = vp_signature(doc)
    return {
        "sig": R.signature(doc),
        "order": [o.Name for o in doc.Objects],
        "groups": {o.Name: [x.Name for x in getattr(o, "Group", [])] for o in doc.Objects},
        "vp": vp,
        "touched": sorted(o.Name for o in doc.Objects if "Touched" in o.State),
        "doc_touched": doc.isTouched(),
        "modified": gdoc.Modified,
        "undo": (doc.UndoCount, doc.RedoCount, list(getattr(doc, "UndoNames", [])), list(getattr(doc, "RedoNames", []))),
        "date": doc.LastModifiedDate,
        "docs": sorted(App.listDocuments()),
        "shapes": shapes,
    }


def state_diff(a, b, path=""):
    """Where two nested states differ, as short strings (so a failure names the property, not 30 kB of dict)."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b), key=str):
            if k not in a:
                out.append("%s/%s only after" % (path, k))
            elif k not in b:
                out.append("%s/%s only before" % (path, k))
            else:
                out += state_diff(a[k], b[k], "%s/%s" % (path, k))
        return out
    return [] if a == b else ["%s: %.80r -> %.80r" % (path, a, b)]


def saved_signature(path):
    doc = App.openDocument(path, True)
    try:
        return sorted((o.Name, o.TypeId, tuple(o.PropertiesList),
                       round(o.Shape.Volume, 6) if hasattr(o, "Shape") and not o.Shape.isNull() else None)
                      for o in doc.Objects)
    finally:
        App.closeDocument(doc.Name)


def click_candidate(panel, row, nth, pick):
    """Pick entry ``pick`` (0 is the placeholder) in the nth combo of the row, as a click does."""
    combo = panel.candidate_combos(row)[nth]
    combo.setCurrentIndex(pick)
    combo.activated.emit(pick)


def row_of(panel, joint_label):
    return [i for i in range(panel.table.rowCount()) if panel.table.item(i, 0).text() == joint_label][0]


class TestReplaceComponentGui(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="replace-gui-")
        self.doc = None
        self.build("mixed")

    def build(self, case, name="RcGui"):
        if self.doc is not None and self.doc.Name in App.listDocuments():
            App.closeDocument(self.doc.Name)
        self.doc, self.b = F.scene_doc(name, case)
        self.asm = self.b.asm
        App.setActiveDocument(self.doc.Name)
        Gui.ActiveDocument = Gui.getDocument(self.doc.Name)
        Gui.updateGui()

    def tearDown(self):
        if Gui.Control.activeDialog():
            Gui.Control.closeDialog()
        pump(0.1)
        for n in list(App.listDocuments()):
            try:
                App.closeDocument(n)
            except NameError:   # already closed with a document it belonged to
                pass

    def panel(self, **kw):
        return F.scene_panel(self.b, kw)

    def open_panel(self, **kw):
        p = self.panel(**kw)
        Gui.Control.showDialog(p)
        Gui.updateGui()
        return p

    def new_part_file(self, name="newpart.FCStd", holes=((10, 6, 3), (30, 6, 3))):
        """A saved document holding one bracket that has the two equal holes; returns the path."""
        d = App.newDocument("RcFile")
        o = d.addObject("Part::Feature", "FileBracket")
        o.Shape = C.bracket(holes=holes)
        d.recompute()
        path = os.path.join(self.tmp, name)
        d.saveAs(path)
        App.closeDocument(d.Name)
        return path

    # ------------------------------------------------------------------ the table shows what the API reports

    def test_table_has_one_row_per_reference_with_the_plan_statuses(self):
        p = self.panel()
        rep = R.plan(self.asm, self.b.hosts[0], self.b.new)
        refs = [(j, r) for j in rep["joints"] for r in j["references"]]
        self.assertEqual(p.table.rowCount(), len(refs))
        self.assertEqual([p.table.horizontalHeaderItem(i).text() for i in range(6)],
                         ["Joint", "Reference", "Old element", "Status", "New element or reason", "Joint afterwards"])
        names = {p.table.item(i, 0).text() for i in range(p.table.rowCount())}
        self.assertEqual(names, {"Rev", "Plan", "Slide"})
        plan_row, slide, rev = row_of(p, "Plan"), row_of(p, "Slide"), row_of(p, "Rev")
        self.assertEqual(p.table.item(plan_row, 3).text(), "Matched")
        self.assertEqual(p.table.item(plan_row, 5).text(), "Remapped")
        self.assertEqual(p.table.item(plan_row, 4).text(), "Face5")
        self.assertEqual(p.table.item(slide, 3).text(), "Unmatched")
        self.assertIn("no candidate", p.cell_text(slide, 4))
        self.assertEqual(p.table.item(slide, 5).text(), "Broken: suppressed, marked")
        self.assertEqual(p.table.item(rev, 3).text(), "Ambiguous")
        self.assertEqual(p.table.item(rev, 2).text(), "Face7 + Edge1")
        self.assertEqual(p.table.item(rev, 5).text(), "Broken: suppressed, marked")
        combos = p.candidate_combos(rev)
        self.assertEqual(len(combos), 2, "one combo per ambiguous element")
        elements = [e for j in rep["joints"] if j["label"] == "Rev" for r in j["references"] for e in r["elements"]]
        for combo, e in zip(combos, elements):
            names = [combo.itemData(i) for i in range(1, combo.count())]
            self.assertEqual(names, [c["element"] for c in e["candidates"]])
            self.assertEqual(combo.currentIndex(), 0)
        self.assertIn("3 joint references: 1 matched, 1 ambiguous, 1 unmatched", p.summary.text())
        self.assertIn("2 joints will be kept suppressed and marked", p.summary.text())
        self.assertFalse(p.delete_old.isChecked(), "delete the old part is off by default")
        self.assertTrue(p.ready)

    def test_choosing_a_candidate_replans_and_can_be_changed(self):
        p = self.panel()
        rep0 = R.plan(self.asm, self.b.hosts[0], self.b.new)
        click_candidate(p, row_of(p, "Rev"), 0, 2)
        rev = row_of(p, "Rev")
        # the face is settled; the edge of the reference lies on that face, so it follows
        self.assertEqual(p.table.item(rev, 3).text(), "Matched")
        self.assertEqual(p.table.item(rev, 5).text(), "Remapped")
        self.assertEqual(p.report["references"], {"matched": 2, "ambiguous": 0, "unmatched": 1})
        # the choice stays changeable: the combo is still there with the pick selected
        combos = p.candidate_combos(rev)
        self.assertEqual(len(combos), 1)
        self.assertEqual(combos[0].currentIndex(), 2)
        click_candidate(p, rev, 0, 1)
        self.assertEqual(p.candidate_combos(rev)[0].currentIndex(), 1)
        self.assertEqual(p.report["references"], {"matched": 2, "ambiguous": 0, "unmatched": 1})
        # taking the choice back makes the reference ambiguous again
        click_candidate(p, rev, 0, 0)
        self.assertEqual(p.report["references"], rep0["references"])
        self.assertEqual(p.choices, {})
        self.assertEqual(len(p.candidate_combos(rev)), 2)

    # ------------------------------------------------------------------ the alignment chooser

    def test_ambiguous_alignment_lists_candidates_and_choosing_replans(self):
        self.build("align")
        p = self.panel()
        rep = R.plan(self.asm, self.b.hosts[0], self.b.new)
        self.assertEqual(rep["alignment"]["method"], "ambiguous")
        self.assertFalse(p.align_row.isHidden())
        cands = rep["alignment"]["candidates"]
        self.assertEqual(p.align_combo.count(), len(cands) + 1)
        for i, c in enumerate(cands):
            text = p.align_combo.itemText(i + 1)
            n = c["counts"]
            self.assertIn(c["label"], text)
            self.assertIn("%d matched, %d ambiguous, %d unmatched" % (n["matched"], n["ambiguous"], n["unmatched"]), text)
        self.assertFalse(p.ready, "OK waits for the alignment")
        self.assertIn("ambiguous", p.summary.text())
        # every reference is ambiguous without element candidates until the alignment is picked
        self.assertEqual([c for r in range(p.table.rowCount()) for c in p.candidate_combos(r)], [])
        for k in range(len(cands)):
            p.align_combo.setCurrentIndex(k + 1)
            p.align_combo.activated.emit(k + 1)
            self.assertEqual(p.report["references"], cands[k]["counts"], cands[k]["label"])
            self.assertEqual(p.report["alignment"]["method"], "given")
            self.assertTrue(p.ready)
            self.assertFalse(p.align_row.isHidden(), "the chooser stays so the reading can be changed")
            self.assertEqual(p.align_combo.currentIndex(), k + 1)
        p.align_combo.setCurrentIndex(0)
        p.align_combo.activated.emit(0)
        self.assertFalse(p.ready)

    def test_ok_is_refused_while_the_alignment_is_ambiguous(self):
        self.build("align")
        p = self.open_panel()
        before = full_state(self.doc)
        self.assertFalse(p.accept())
        self.assertFalse(p.error.isHidden())
        self.assertEqual(state_diff(before, full_state(self.doc)), [])

    # ------------------------------------------------------------------ data safety

    def test_cancel_after_the_whole_dance_changes_nothing(self):
        path = os.path.join(self.tmp, "assembly.FCStd")
        self.doc.saveAs(path)
        self.doc.recompute()
        self.doc.purgeTouched()
        Gui.getDocument(self.doc.Name).Modified = False
        self.doc.saveCopy(os.path.join(self.tmp, "before.FCStd"))
        before = full_state(self.doc)
        before_file = open(path, "rb").read()
        fpath = self.new_part_file()
        docs_before = sorted(App.listDocuments())

        p = self.open_panel()
        click_candidate(p, row_of(p, "Rev"), 0, 1)                  # a candidate pick on an object of the document
        p.set_source_file(fpath)                                    # a file: a hidden temporary document
        self.assertEqual(len(p.temp), 1)
        self.assertIn(p.temp[0][0], App.listDocuments())
        self.assertIsNotNone(p.report)
        click_candidate(p, row_of(p, "Rev"), 0, 2)
        p.set_source_object(self.b.new)
        p.set_source_file(fpath)
        p.delete_old.setChecked(True)
        p.set_source_kind(2)                                        # the vault: none connected
        pump(0.3)
        self.assertTrue(p.pdm_note.text())
        p.set_source_kind(0)
        p.set_source_object(self.b.new)
        self.assertTrue(Gui.Control.activeDialog())
        p.reject()
        Gui.Control.closeDialog()
        pump(0.3)

        self.assertEqual(state_diff(before, full_state(self.doc)), [])
        self.assertFalse(self.doc.isTouched())
        self.assertFalse(Gui.getDocument(self.doc.Name).Modified)
        self.assertEqual(sorted(App.listDocuments()), docs_before, "the hidden document is closed again")
        self.doc.saveCopy(os.path.join(self.tmp, "after.FCStd"))
        self.assertEqual(saved_signature(os.path.join(self.tmp, "after.FCStd")),
                         saved_signature(os.path.join(self.tmp, "before.FCStd")))
        self.assertEqual(open(path, "rb").read(), before_file, "the saved file is untouched")

    def test_cancel_after_the_alignment_dance_changes_nothing(self):
        self.build("align")
        self.doc.recompute()
        self.doc.purgeTouched()
        Gui.getDocument(self.doc.Name).Modified = False
        before = full_state(self.doc)
        p = self.open_panel()
        n = p.align_combo.count() - 1
        for k in list(range(n)) + [0]:
            p.align_combo.setCurrentIndex(k + 1)
            p.align_combo.activated.emit(k + 1)
            for r in range(p.table.rowCount()):
                combos = p.candidate_combos(r)
                if combos and combos[0].count() > 1:
                    click_candidate(p, r, 0, 1)
                    break
        p.reject()
        Gui.Control.closeDialog()
        pump(0.3)
        self.assertEqual(state_diff(before, full_state(self.doc)), [])

    def test_closing_the_dialog_without_cancel_still_closes_the_hidden_document(self):
        fpath = self.new_part_file()
        docs = sorted(App.listDocuments())
        p = self.open_panel()
        p.set_source_file(fpath)
        self.assertEqual(len(App.listDocuments()), len(docs) + 1)
        Gui.Control.closeDialog()
        pump(0.5)
        self.assertEqual(sorted(App.listDocuments()), docs)

    # ------------------------------------------------------------------ OK equals apply, in one undo step

    def run_both(self, case, drive, direct, name="RcOk"):
        """Build the case twice under the same names; drive the panel on the first, call the API on the second."""
        self.build(case, name)
        self.doc.recompute()
        sig_before = R.signature(self.doc)
        undo_before = self.doc.UndoCount
        p = self.open_panel()
        drive(p)
        self.assertTrue(p.accept(), p.error.text())
        Gui.Control.closeDialog()
        pump(0.2)
        via_panel = R.signature(self.doc)
        self.assertEqual(self.doc.UndoCount, undo_before + 1, "OK is one undo step")
        self.assertNotEqual(via_panel, sig_before)
        report = p.applied
        self.doc.undo()
        self.assertEqual(R.signature(self.doc), sig_before, "one undo restores the document")
        self.assertEqual(self.doc.UndoCount, undo_before)
        self.doc.redo()
        self.assertEqual(R.signature(self.doc), via_panel)
        self.build(case, name)
        direct()
        self.assertEqual(norm(R.signature(self.doc)), norm(via_panel), "OK gave what apply gives")
        return report

    def test_ok_equals_apply_for_an_object_with_candidate_picks(self):
        picks = {}

        def drive(p):
            click_candidate(p, row_of(p, "Rev"), 0, 2)
            picks.update(p.choices)

        def direct():
            R.apply(self.asm, self.b.hosts[0], self.b.new, choices=picks)

        rep = self.run_both("mixed", drive, direct)
        self.assertTrue(picks)
        self.assertEqual(rep["references"], {"matched": 2, "ambiguous": 0, "unmatched": 1})
        self.assertTrue(self.doc.getObject("Slide").Suppressed)
        self.assertTrue(R.is_broken(self.doc.getObject("Slide")))

    def test_ok_equals_apply_with_an_alignment_picked(self):
        chosen = {}

        def drive(p):
            p.align_combo.setCurrentIndex(2)
            p.align_combo.activated.emit(2)
            chosen["plc"] = p.alignment_plc

        def direct():
            R.apply(self.asm, self.b.hosts[0], self.b.new, alignment=chosen["plc"])

        rep = self.run_both("align", drive, direct)
        self.assertEqual(rep["alignment"]["method"], "given")

    def test_ok_equals_apply_for_a_file_and_is_one_undo_step(self):
        fpath = self.new_part_file()
        choices = {}

        def drive(p):
            self.assertTrue(p.set_source_file(fpath))
            click_candidate(p, row_of(p, "Rev"), 0, 1)
            choices.update(p.choices)

        def direct():
            fdoc = App.openDocument(fpath, True, True)
            try:
                R.apply(self.asm, self.b.hosts[0], fdoc.getObject("FileBracket"), choices=choices, import_foreign=True)
            finally:
                App.closeDocument(fdoc.Name)

        rep = self.run_both("mixed", drive, direct)
        self.assertTrue(rep["imported"])
        self.assertEqual([d for d in App.listDocuments() if d.startswith("RcFile")], [], "the hidden document is closed")
        # the import belongs to the one step: undo takes the copy away with the replacement
        self.doc.undo()
        self.assertIsNone(self.doc.getObject("FileBracket"))

    def test_delete_old_checkbox_is_passed_to_apply(self):
        p = self.open_panel()
        p.delete_old.setChecked(True)
        self.assertTrue(p.accept(), p.error.text())
        Gui.Control.closeDialog()
        self.assertIsNone(self.doc.getObject("Old"), "the old part is gone with the box ticked")

    def test_old_part_stays_without_the_checkbox(self):
        p = self.open_panel()
        self.assertTrue(p.accept(), p.error.text())
        Gui.Control.closeDialog()
        self.assertIsNotNone(self.doc.getObject("Old"))
        self.assertEqual(p.applied["deleted_old"], [])

    def test_apply_error_keeps_the_panel_open_and_the_document_unchanged(self):
        p = self.open_panel()
        before = full_state(self.doc)
        orig = R.apply

        def boom(*a, **k):
            raise R.ReplaceError("boom")
        R.apply = boom
        try:
            self.assertFalse(p.accept())
        finally:
            R.apply = orig
        self.assertIn("boom", p.error.text())
        self.assertEqual(state_diff(before, full_state(self.doc)), [])
        self.assertTrue(Gui.Control.activeDialog())

    # ------------------------------------------------------------------ the vault

    def test_vault_not_connected_says_so_at_once(self):
        from PDMGui import state

        state.disconnect()
        p = self.open_panel()
        t = time.time()
        p.set_source_kind(2)
        self.assertLess(time.time() - t, 3.0)
        self.assertIn("No PDM vault", p.pdm_note.text())
        self.assertTrue(p.form.isEnabled())

    def test_a_vault_that_hangs_gives_a_message_within_the_timeout(self):
        import ReplacePanel as P
        from PDMGui import state

        class Hanging:
            def documents(self):
                time.sleep(20)
                return {}

        old_vault, old_timeout = state._vault, P.PDM_TIMEOUT
        state._vault = Hanging()
        P.PDM_TIMEOUT = 1.0
        try:
            p = self.open_panel()
            t = time.time()
            p.set_source_kind(2)
            took = time.time() - t
        finally:
            state._vault, P.PDM_TIMEOUT = old_vault, old_timeout
        self.assertLess(took, 4.0)
        self.assertIn("did not answer", p.pdm_note.text())
        self.assertTrue(p.form.isEnabled())

    def test_a_revision_from_the_vault_is_previewed_and_cleaned_up(self):
        from PDMGui import state
        from PDMGuiTests import fixture

        world = fixture.make_world(os.path.join(self.tmp, "vault"), "basic")
        state.connect(world.vault_path, world.alice.workdir, "alice", save=False)
        try:
            p = self.open_panel()
            p.set_source_kind(2)
            self.assertTrue(p.pdm_docs, p.pdm_note.text())
            self.assertIn("bracket", p.pdm_docs)
            self.assertTrue(p.set_pdm_document("bracket"))
            revs = [p.pdm_rev.itemText(i) for i in range(1, p.pdm_rev.count())]
            self.assertTrue(revs)
            before = full_state(self.doc)
            self.assertTrue(p.set_pdm_revision("bracket", revs[0]))
            self.assertTrue(p.temp and p.temp[0][1])
            self.assertIsNotNone(p.new_obj)
            self.assertIsNotNone(p.report, p.error.text())
            self.assertEqual(state_diff(before["sig"], full_state(self.doc)["sig"]), [], "previewing a revision changes nothing")
            export = p.temp[0][1]
            self.assertTrue(os.path.isdir(export))
            p.reject()
            Gui.Control.closeDialog()
            pump(0.2)
            self.assertFalse(os.path.exists(export), "the exported revision is removed")
            self.assertEqual(state_diff(before, full_state(self.doc)), [])
        finally:
            state.disconnect()

    def test_ok_with_a_vault_revision_imports_the_part_in_one_undo_step(self):
        from PDMGui import state
        from PDMGuiTests import fixture

        world = fixture.make_world(os.path.join(self.tmp, "vault"), "basic")
        state.connect(world.vault_path, world.alice.workdir, "alice", save=False)
        try:
            p = self.open_panel()
            p.set_source_kind(2)
            p.set_pdm_document("bracket")
            rev = p.pdm_rev.itemText(1)
            self.assertTrue(p.set_pdm_revision("bracket", rev))
            if not p.ready:
                p.choose_alignment(0)
            self.assertTrue(p.ready, p.error.text() or p.summary.text())
            before = R.signature(self.doc)
            undo = self.doc.UndoCount
            self.assertTrue(p.accept(), p.error.text())
            Gui.Control.closeDialog()
            pump(0.3)
            self.assertTrue(p.applied["imported"])
            self.assertEqual(self.doc.UndoCount, undo + 1)
            self.assertEqual([d for d in App.listDocuments() if d == "bracket"], [], "the revision document is closed")
            link = self.doc.getObject("ML")
            self.assertIs(link.LinkedObject.Document, self.doc, "the link points into the assembly's own document")
            self.doc.undo()
            self.assertEqual(R.signature(self.doc), before)
        finally:
            state.disconnect()

    # ------------------------------------------------------------------ keys

    def test_return_in_the_path_field_loads_and_never_closes_the_panel(self):
        fpath = self.new_part_file()
        p = self.open_panel()
        p.set_source_kind(1)
        before = full_state(self.doc)
        p.file_edit.setText(fpath)
        QtWidgets.QApplication.sendEvent(p.file_edit, QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Return, QtCore.Qt.NoModifier))
        pump(0.3)
        self.assertTrue(Gui.Control.activeDialog())
        self.assertIsNotNone(p.new_obj, "Return loaded the file")
        self.assertEqual(R.signature(self.doc), before["sig"])

    def test_return_in_a_candidate_combo_does_nothing(self):
        p = self.open_panel()
        before = full_state(self.doc)
        combo = p.candidate_combos(row_of(p, "Rev"))[0]
        combo.setFocus()
        QtWidgets.QApplication.sendEvent(combo, QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Return, QtCore.Qt.NoModifier))
        pump(0.3)
        self.assertTrue(Gui.Control.activeDialog())
        self.assertEqual(state_diff(before, full_state(self.doc)), [])

    # ------------------------------------------------------------------ the command

    def test_command_is_registered_and_placed(self):
        info = Gui.Command.get("Assembly_ReplaceComponent").getInfo()
        for k in ("menuText", "toolTip", "statusTip", "pixmap"):
            self.assertTrue(info[k], k)
        self.assertTrue(os.path.isfile(info["pixmap"]), info["pixmap"])
        self.assertFalse(QtGui.QIcon(info["pixmap"]).isNull())
        self.assertEqual(Gui.Command.get("Assembly_ReplaceComponent").getShortcut(), "")
        Gui.activateWorkbench("AssemblyWorkbench")
        Gui.updateGui()
        acts = [a for a in Gui.getMainWindow().findChildren(QtGui.QAction) if a.objectName() == "Assembly_ReplaceComponent"]
        self.assertTrue(acts, "no QAction: the command is in no toolbar or menu")
        self.assertTrue(any(isinstance(w, QtWidgets.QToolBar) for a in acts for w in a.associatedObjects()), "not in a toolbar")
        self.assertTrue(any(isinstance(w, QtWidgets.QMenu) for a in acts for w in a.associatedObjects()), "not in a menu")

    def captured(self):
        import ReplacePanel as P

        made = []
        orig = P.TaskReplaceComponent.__init__

        def spy(this, *a, **k):
            made.append(this)
            orig(this, *a, **k)
        P.TaskReplaceComponent.__init__ = spy
        self.addCleanup(setattr, P.TaskReplaceComponent, "__init__", orig)
        return made

    def test_command_preselects_the_selected_component_and_runs_once(self):
        made = self.captured()
        Gui.activateWorkbench("AssemblyWorkbench")
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.doc.Name, "ML")
        Gui.runCommand("Assembly_ReplaceComponent")
        Gui.updateGui()
        self.assertTrue(Gui.Control.activeDialog())
        self.assertEqual(len(made), 1)
        self.assertEqual(made[0].old, self.doc.getObject("ML"))
        Gui.runCommand("Assembly_ReplaceComponent")
        Gui.updateGui()
        self.assertEqual(len(made), 1, "a second panel was built while one was open")

    def test_command_active_only_with_an_assembly_and_not_while_open(self):
        Gui.activateWorkbench("AssemblyWorkbench")
        Gui.Selection.clearSelection()
        cmd = Gui.Command.get("Assembly_ReplaceComponent")
        act = [a for a in Gui.getMainWindow().findChildren(QtGui.QAction) if a.objectName() == "Assembly_ReplaceComponent"][0]
        pump(0.7)
        self.assertFalse(act.isEnabled(), "enabled with no assembly selected")
        Gui.Selection.addSelection(self.doc.Name, "ML")
        pump(0.7)
        self.assertTrue(act.isEnabled(), "disabled with a component selected")
        cmd.run()
        Gui.updateGui()
        self.assertTrue(Gui.Control.activeDialog())
        pump(0.7)
        self.assertFalse(act.isEnabled(), "enabled while its own panel is open")

    def test_panel_can_pick_the_old_component_itself(self):
        p = F.scene_panel(self.b)
        p.old_combo.setCurrentIndex(-1)
        p._old_changed()
        self.assertIsNone(p.report)
        self.assertIn("Choose the component", p.summary.text())
        p.old_combo.setCurrentIndex(p.components.index(self.b.hosts[0]))
        p._old_changed()
        self.assertIsNotNone(p.report)

    # ------------------------------------------------------------------ repairing a broken reference

    def replaced(self):
        """The mixed scene after OK: Rev and Slide are left broken."""
        p = self.open_panel()
        self.assertTrue(p.accept(), p.error.text())
        Gui.Control.closeDialog()
        pump(0.1)
        return p

    def test_repair_panel_lists_broken_joints_and_repairs_one(self):
        from ReplacePanel import TaskRepairJoint

        self.replaced()
        slide = self.doc.getObject("Slide")
        self.assertTrue(R.is_broken(slide) and slide.Suppressed)
        before = R.signature(self.doc)
        p = TaskRepairJoint(self.asm, slide)
        self.assertEqual({j.Name for j in p.joints}, {"Rev", "Slide"})
        self.assertEqual(p.joint, slide)
        self.assertEqual(p.reference, "Reference2")
        self.assertTrue(p.why.text())
        self.assertEqual(len([c for c in p.element_combos if c is not None]), 2)
        # Cancel changes nothing, OK without a choice is refused
        self.assertFalse(p.accept())
        self.assertEqual(R.signature(self.doc), before)
        top = C.one(C.local(self.b.new), C.pl((0, 0, 1), 10))
        for i in range(2):
            p.set_element(i, top)
        self.assertTrue(p.accept(), p.error.text())
        self.assertFalse(R.is_broken(slide))
        self.assertFalse(slide.Suppressed)
        self.assertEqual([s.lstrip("?") for s in slide.Reference2[1]], [top, top])
        self.assertTrue(all("?" not in s for s in slide.Reference2[1]))
        self.assertEqual({j.Name for j in TaskRepairJoint(self.asm).joints}, {"Rev"})
        self.doc.undo()
        self.assertEqual(R.signature(self.doc), before, "repair is one undo step")

    def test_repair_offers_only_elements_of_the_same_kind(self):
        from ReplacePanel import TaskRepairJoint

        self.replaced()
        slide = self.doc.getObject("Slide")
        before = R.signature(self.doc)
        p = TaskRepairJoint(self.asm, slide)
        combo = p.element_combos[0]
        self.assertTrue(all(combo.itemText(i).startswith("Face") for i in range(1, combo.count())))
        self.assertEqual(R.signature(self.doc), before)

    def test_repair_command_needs_a_broken_joint_selected(self):
        self.replaced()
        Gui.activateWorkbench("AssemblyWorkbench")
        cmd = Gui.Command.get("Assembly_RepairJointReference")
        info = cmd.getInfo()
        for k in ("menuText", "toolTip", "statusTip", "pixmap"):
            self.assertTrue(info[k], k)
        self.assertTrue(os.path.isfile(info["pixmap"]))
        act = [a for a in Gui.getMainWindow().findChildren(QtGui.QAction) if a.objectName() == "Assembly_RepairJointReference"][0]
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.doc.Name, "Plan")
        pump(0.7)
        self.assertFalse(act.isEnabled(), "enabled for a sound joint")
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.doc.Name, "Slide")
        pump(0.7)
        self.assertTrue(act.isEnabled(), "disabled for a broken joint")
