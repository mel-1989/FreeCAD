# SPDX-License-Identifier: LGPL-2.1-or-later
"""G03 slice 5: the PDM commands and panels, driven programmatically (needs FreeCADGui; mini only).

Every test builds a throwaway vault in a temp dir (``fixture.make_world``), connects the GUI as alice and
drives the panels the way a click would (button.click(), itemDoubleClicked). Dialogs are non-modal and logged
in ``dialogs.shown``. Bob is a second client: a second Vault in this process (own working copy, own lock
identity) and, for the lock conflict, a separate FreeCADCmd process running the slice 2 ``client_worker.py``."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtWidgets

from PDM import vault as V
from PDMGui import commands, dialogs, panels, state
from PDMTests import test_vault as TV

from . import fixture

ALL = ["PDM_Connect", "PDM_Documents", "PDM_CheckOut", "PDM_CheckIn", "PDM_Revisions", "PDM_WhereUsed",
       "PDM_Lifecycle"]


def pump(n=4):
    for _ in range(n):
        Gui.updateGui()


def column(table, c):
    return [table.item(r, c).text() for r in range(table.rowCount())]


def select(table, text, col=0):
    for r in range(table.rowCount()):
        if table.item(r, col).text() == text:
            table.setCurrentCell(r, col)
            return r
    raise AssertionError(f"{text!r} not in column {col}: {column(table, col)}")


class PanelCase(unittest.TestCase):
    scenario = "basic"

    def setUp(self):
        dialogs.clear()
        self.tmp = tempfile.mkdtemp(prefix="pdmgui")
        self.world = fixture.make_world(self.tmp, self.scenario, fixed_time=True)
        self.va, self.vb = self.world.alice, self.world.bob
        state.connect(self.world.vault_path, self.va.workdir, "alice", save=False)

    def tearDown(self):
        panels.close_all()
        dialogs.clear()
        state.cleanup_exports()
        state.disconnect()
        for d in list(App.listDocuments().values()):
            App.closeDocument(d.Name)
        pump()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def vault_state(self, name):
        return self.va.status(name)

    def errors(self):
        return [m for m in dialogs.shown if m[0] == "error"]

    def head(self):
        return subprocess.run(["git", "--git-dir", self.world.vault_path, "rev-parse", "refs/heads/main"], check=True,
                              stdout=subprocess.PIPE, text=True).stdout.strip()


class TestCommands(PanelCase):
    def test_registered_and_is_active_follow_the_document(self):
        def active(n):
            return Gui.Command.get(n).isActive()
        for n in ALL:
            self.assertIsNotNone(Gui.Command.get(n), n)
        state.disconnect()
        s = state.settings()
        state.save_settings("", "", "")
        try:
            self.assertTrue(active("PDM_Connect"))
            self.assertFalse(any(active(n) for n in ALL[1:]), "nothing applies before connecting")
        finally:
            state.save_settings(s["vault"], s["workdir"], s["user"])
        state.connect(self.world.vault_path, self.va.workdir, "alice", save=False)
        self.assertTrue(active("PDM_Documents"))
        self.assertFalse(any(active(n) for n in ALL[2:]), "no active document: per-document commands are off")
        # an unrelated document (not in the working copy) leaves them off
        other = App.newDocument("scratch")
        self.assertFalse(any(active(n) for n in ALL[2:]))
        App.closeDocument(other.Name)
        doc = App.openDocument(self.va.doc_path("plate"))
        App.setActiveDocument(doc.Name)
        self.assertTrue(active("PDM_CheckOut") and active("PDM_Revisions") and active("PDM_WhereUsed")
                        and active("PDM_Lifecycle"))
        self.assertFalse(active("PDM_CheckIn"), "not checked out: nothing to check in")
        state.checkout("plate")
        state.forget_locks()
        self.assertTrue(active("PDM_CheckIn"))
        self.assertFalse(active("PDM_CheckOut"), "already checked out by this client")

    def test_workbench_and_toolbar(self):
        self.assertIn("PDMWorkbench", Gui.listWorkbenches())
        self.assertEqual(commands.TOOLBAR, ALL)

    def test_settings_roundtrip(self):
        s = state.settings()
        try:
            state.save_settings("/v.git", "/wc", "carol")
            self.assertEqual(state.settings(), {"vault": "/v.git", "workdir": "/wc", "user": "carol"})
            self.assertEqual(App.ParamGet(state.PARAM_PATH).GetString("User"), "carol")
        finally:
            state.save_settings(s["vault"], s["workdir"], s["user"])

    def test_connect_panel_clones_and_connects(self):
        state.disconnect()
        w = panels.show_panel("connect")
        w.vault_edit.setText(self.world.vault_path)
        w.wc_edit.setText(os.path.join(self.tmp, "wc-carol"))
        w.user_edit.setText("carol")
        s = state.settings()
        try:
            w.connect_btn.click()
            self.assertEqual(state.current_vault().user, "carol")
            self.assertIn("Connected as carol", w.status.text())
            self.assertEqual(sorted(state.current_vault().documents()), ["assembly", "bracket", "plate"])
            w.user_edit.setText("bad user!")
            w.connect_btn.click()
            self.assertTrue(self.errors(), "a bad user name is reported, not raised")
        finally:
            state.save_settings(s["vault"], s["workdir"], s["user"])


class TestCheckInOut(PanelCase):
    def test_checkout_edit_checkin_from_the_gui(self):
        docs = panels.show_panel("documents")
        select(docs.table, "plate")
        docs.btn_checkout.click()
        self.assertEqual(self.errors(), [])
        doc = state.open_doc_for("plate")
        self.assertIsNotNone(doc, "check-out opens the document from the working copy")
        self.assertEqual(os.path.realpath(doc.FileName.rstrip("/")), os.path.realpath(self.va.doc_path("plate")))
        row = column(docs.table, 3)[column(docs.table, 0).index("plate")]
        self.assertEqual(row, "alice (you)", "the lock is shown with its holder")
        self.assertEqual(self.vault_state("plate")["lock"]["holder"], "alice")
        # edit, then check in with a message
        App.setActiveDocument(doc.Name)
        doc.getObject("Box").Length = 77
        doc.recompute()
        ci = panels.show_panel("checkin", "plate")
        self.assertIn("checked out by you", ci.status.text())
        ci.btn.click()
        self.assertTrue(self.errors(), "an empty message is refused")
        self.assertEqual(self.vault_state("plate")["revision"], "A.1")
        dialogs.clear()
        ci.msg.setPlainText("Widen the plate to 77 mm")
        ci.btn.click()
        self.assertEqual(self.errors(), [], dialogs.shown)
        st = self.vault_state("plate")
        self.assertEqual((st["revision"], st["state"], st["lock"]), ("A.2", "WIP", None))
        top = self.va.history("plate")[0]
        self.assertEqual((top["message"], top["author"]), ("Widen the plate to 77 mm", "alice"))
        # the vault's content is the edit
        verify = self.vb.export_revision("plate", "A.2", dest=os.path.join(self.tmp, "x"))
        d2 = App.openDocument(verify)
        self.assertAlmostEqual(d2.getObject("Box").Length.Value, 77)
        # the revision browser lists it
        rv = panels.show_panel("revisions", "plate")
        self.assertEqual(column(rv.table, 0)[0], "A.2")
        self.assertEqual(column(rv.table, 4)[0], "Widen the plate to 77 mm")

    def test_lock_conflict_from_another_process(self):
        proc = TV._spawn([{"op": "clone", "vault": self.world.vault_path, "wc": os.path.join(self.tmp, "wc-bob2"),
                           "user": "bob"}, TV.call("checkout", "plate")], self.tmp)
        res = TV._collect(proc)
        self.assertTrue(res[1]["ok"], res)
        before = self.head()
        xml = os.path.join(self.va.doc_path("plate"), "Document.xml")
        with open(xml, "rb") as f:
            xml_before = f.read()
        docs = panels.show_panel("documents")
        select(docs.table, "plate")
        docs.btn_checkout.click()
        errs = self.errors()
        self.assertEqual(len(errs), 1, dialogs.shown)
        self.assertIn("bob", errs[0][2])
        self.assertIn("checked out by", errs[0][2])
        self.assertIsNone(state.open_doc_for("plate"), "a refused check-out leaves the document closed")
        with open(xml, "rb") as f:
            self.assertEqual(f.read(), xml_before, "the working copy was not touched")
        self.assertEqual(self.head(), before)
        docs.refresh()
        self.assertEqual(column(docs.table, 3)[column(docs.table, 0).index("plate")], "bob")

    def test_stale_checkin_shows_the_vault_error_and_changes_nothing(self):
        self.va.checkout("plate")
        fixture.edit_box(self.va, "plate", 70)        # alice's edit on A.1
        self.va.release_lock("plate")                  # she gives the lock up, keeping her files
        self.vb.checkout("plate")
        fixture.edit_box(self.vb, "plate", 55)
        self.vb.checkin("plate", "Bob's change")       # vault is at A.2
        self.va.lock("plate")                          # alice re-locks without syncing
        before = self.head()
        ci = panels.show_panel("checkin", "plate")
        ci.msg.setPlainText("Alice on a stale base")
        ci.btn.click()
        errs = self.errors()
        self.assertEqual(len(errs), 1, dialogs.shown)
        self.assertIn("based on A.1", errs[0][2])      # the library's own message, unchanged
        self.assertIn("vault head for it is A.2", errs[0][2])
        self.assertEqual(self.head(), before, "the vault did not change")
        self.assertEqual(self.vault_state("plate")["revision"], "A.2")
        self.assertEqual(self.vault_state("plate")["lock"]["holder"], "alice", "the lock is still held")
        self.assertEqual(ci.msg.toPlainText(), "Alice on a stale base", "her message is kept for the retry")

    def test_dialogs_never_block(self):
        dialogs.error("t", "x")
        self.assertEqual(len(dialogs.boxes), 1)
        self.assertFalse(dialogs.boxes[0].isModal())
        dialogs.clear()
        self.assertEqual(dialogs.boxes, [])


class TestRepairRound(PanelCase):
    """Verifier findings M1 to M4 and the low ones of run 023."""

    def length(self, doc):
        return doc.getObject("Box").Length.Value

    def bob_revises(self, name, length, message):
        fixture.revise(self.vb, name, length, message)

    def test_m1_open_shows_the_vault_head_not_the_stale_working_copy(self):
        self.bob_revises("plate", 99, "Bob: 99 mm")          # vault head A.2; alice's working copy still A.1
        self.assertAlmostEqual(self.length(App.openDocument(self.va.doc_path("plate"))), 60)
        for d in list(App.listDocuments().values()):
            App.closeDocument(d.Name)
        docs = panels.show_panel("documents")
        select(docs.table, "plate")
        docs.btn_open.click()
        self.assertEqual(self.errors(), [], dialogs.shown)
        doc = [d for d in App.listDocuments().values()][0]
        self.assertAlmostEqual(self.length(doc), 99, msg="the head revision, not the working copy")
        self.assertNotEqual(os.path.realpath(doc.FileName.rstrip("/")), os.path.realpath(self.va.doc_path("plate")))

    def test_m2_unlocked_open_is_read_only_and_leaves_no_dead_end(self):
        doc = state.open_head("plate")
        self.assertFalse(os.access(os.path.join(doc.FileName, "Document.xml"), os.W_OK))
        self.assertFalse(self.va.has_changes("plate"))
        App.closeDocument(doc.Name)
        state.checkout("plate")                                # still checks out cleanly afterwards
        self.assertEqual(self.vault_state("plate")["lock"]["holder"], "alice")

    def test_m2_discard_local_edits_and_check_out_is_confirmed(self):
        self.va.checkout("plate")
        fixture.edit_box(self.va, "plate", 70)                 # local edits in the working copy
        self.va.release_lock("plate")
        docs = panels.show_panel("documents")
        select(docs.table, "plate")
        docs.btn_checkout.click()
        self.assertEqual(len(self.errors()), 1)
        self.assertIn("discard_local", self.errors()[0][2], "the library names the way out")
        dialogs.clear()
        dialogs.handler = lambda kind, title, text: False      # the user answers No
        try:
            docs.btn_discard.click()
            self.assertEqual([m[0] for m in dialogs.shown], ["confirm"])
            self.assertIsNone(self.vault_state("plate")["lock"])
            self.assertTrue(self.va.has_changes("plate"))
            dialogs.handler = lambda kind, title, text: True   # the user answers Yes
            docs.btn_discard.click()
        finally:
            dialogs.handler = None
        self.assertEqual(self.errors(), [], dialogs.shown)
        self.assertEqual(self.vault_state("plate")["lock"]["holder"], "alice")
        self.assertFalse(self.va.has_changes("plate"))
        self.assertAlmostEqual(self.length(state.open_doc_for("plate")), 60)

    def test_m2_confirmation_box_without_handler_is_non_modal_and_runs_on_yes(self):
        self.va.checkout("plate")
        fixture.edit_box(self.va, "plate", 70)
        self.va.release_lock("plate")
        docs = panels.show_panel("documents")
        select(docs.table, "plate")
        docs.btn_discard.click()
        self.assertEqual(len(dialogs.boxes), 1)
        box = dialogs.boxes[0]
        self.assertFalse(box.isModal())
        self.assertTrue(self.va.has_changes("plate"), "nothing happens before the answer")
        box.button(QtWidgets.QMessageBox.Yes).click()
        self.assertFalse(self.va.has_changes("plate"))

    def test_m3_checkin_clears_modified_so_a_second_checkout_works(self):
        doc = state.checkout("plate")
        doc.getObject("Box").Length = 71
        doc.recompute()
        self.assertTrue(state.is_modified(doc))
        rec = state.checkin("plate", "71 mm")
        self.assertTrue(rec["saved"])
        self.assertFalse(state.is_modified(doc), "the GUI document's Modified flag is clear after a check-in")
        again = state.checkout("plate")                         # no 'unsaved changes' refusal
        self.assertIsNotNone(again)
        self.assertEqual(self.vault_state("plate")["lock"]["holder"], "alice")

    def test_m4_checkin_without_edits_makes_no_revision_and_edits_say_they_saved(self):
        doc = state.checkout("plate")
        ci = panels.show_panel("checkin", "plate")
        ci.msg.setPlainText("nothing changed")
        ci.btn.click()
        self.assertEqual(len(self.errors()), 1, dialogs.shown)
        self.assertEqual(self.vault_state("plate")["revision"], "A.1")
        self.assertFalse(state.is_modified(doc), "the check-in did not save an unmodified document")
        dialogs.clear()
        doc.getObject("Box").Length = 72
        doc.recompute()
        ci.btn.click()
        self.assertEqual(self.errors(), [], dialogs.shown)
        info = [m for m in dialogs.shown if m[0] == "info"][0][2]
        self.assertIn("A.2", info)
        self.assertIn("saved", info)
        # a second round with no edits: still no new revision
        dialogs.clear()
        state.checkout("plate")
        ci.msg.setPlainText("again, no edits")
        ci.btn.click()
        self.assertEqual(len(self.errors()), 1)
        self.assertEqual(self.vault_state("plate")["revision"], "A.2")
        self.assertEqual(len(self.va.history("plate")), 2)

    def test_l6_lifecycle_refresh_leaves_no_stale_buttons_or_stretches(self):
        lp = panels.show_panel("lifecycle", "plate")
        for _ in range(3):
            lp.refresh()
        lp.buttons["InReview"].click()
        pump()
        self.assertEqual(lp.buttons_box.count(), len(lp.buttons) + 1, "buttons plus one stretch")
        shown = [b for b in lp.findChildren(QtWidgets.QPushButton)]
        self.assertEqual(sorted(b.text() for b in shown), sorted(b.text() for b in lp.buttons.values()))

    def test_l7_revisions_checkout_is_labelled_as_the_latest(self):
        rv = panels.show_panel("revisions", "plate")
        self.assertIn("latest", rv.btn_checkout.text())


class TestRevisions(PanelCase):
    def test_history_table_open_readonly_and_released_checkout(self):
        fixture.revise(self.va, "plate", 61, "Plate: 61 mm")
        rv = panels.show_panel("revisions", "plate")
        hist = self.va.history("plate")
        self.assertEqual(rv.table.rowCount(), len(hist))
        self.assertEqual([rv.table.horizontalHeaderItem(c).text() for c in range(5)],
                         ["Revision", "State", "Author", "Time", "Message"])
        self.assertEqual(column(rv.table, 0), [h["revision"] for h in hist])
        self.assertEqual(column(rv.table, 2), [h["author"] for h in hist])
        self.assertEqual(column(rv.table, 4), [h["message"] for h in hist])
        # open A.1 read-only in a new document
        select(rv.table, "A.1")
        doc = rv.do_open()
        self.assertIsNotNone(doc)
        self.assertAlmostEqual(doc.getObject("Box").Length.Value, 60)
        self.assertNotEqual(os.path.realpath(doc.FileName.rstrip("/")), os.path.realpath(self.va.doc_path("plate")))
        self.assertFalse(os.access(os.path.join(doc.FileName, "Document.xml"), os.W_OK), "files are read-only on disk")
        # a Released row cannot be checked out without the new-revision choice
        rb = panels.show_panel("revisions", "bracket")
        self.assertEqual(self.vault_state("bracket")["state"], "Released")
        rb.btn_checkout.click()
        errs = self.errors()
        self.assertEqual(len(errs), 1, dialogs.shown)
        self.assertIn("Released", errs[0][2])
        self.assertIsNone(self.vault_state("bracket")["lock"])
        self.assertIsNone(state.open_doc_for("bracket"))
        dialogs.clear()
        rb.next_rev.setChecked(True)
        rb.btn_checkout.click()
        self.assertEqual(self.errors(), [], dialogs.shown)
        self.assertEqual(self.vault_state("bracket")["lock"]["holder"], "alice")
        self.assertIsNotNone(state.open_doc_for("bracket"))
        self.assertIn("checked out by you", rb.status.text())


class TestWhereUsedAndLifecycle(PanelCase):
    def test_where_used_lists_and_double_click_opens(self):
        w = panels.show_panel("whereused", "bracket")
        self.assertEqual(column(w.table, 0), ["assembly"])
        self.assertEqual(w.table.item(0, 2).text(), "LinkedObject")
        self.assertEqual(self.va.where_used("bracket")[0]["document"], "assembly")
        w.table.setCurrentCell(0, 0)
        n_before = len(App.listDocuments())
        w.table.itemDoubleClicked.emit(w.table.item(0, 0))
        self.assertGreater(len(App.listDocuments()), n_before, "double-click opens the linking document")
        doc = [d for d in App.listDocuments().values() if d.FileName.rstrip("/").endswith("assembly.FCStd.d")][0]
        pw = panels.show_panel("whereused", "assembly")
        self.assertIn("No document links", pw.status.text())

    def test_lifecycle_offers_only_allowed_transitions_and_applies_one(self):
        lp = panels.show_panel("lifecycle", "plate")
        self.assertEqual(sorted(lp.buttons), ["InReview", "Obsolete"])
        self.assertEqual(sorted(lp.buttons), sorted(V.TRANSITIONS["WIP"]))
        lp.msg.setText("Ready for review")
        lp.buttons["InReview"].click()
        self.assertEqual(self.errors(), [], dialogs.shown)
        st = self.vault_state("plate")
        self.assertEqual(st["state"], "InReview")
        self.assertEqual(sorted(lp.buttons), sorted(V.TRANSITIONS["InReview"]))
        self.assertEqual(self.va.history("plate")[0]["message"], "Ready for review")
        # a Released document can only go to Obsolete
        lb = panels.show_panel("lifecycle", "bracket")
        self.assertEqual(list(lb.buttons), ["Obsolete"])
        lb.buttons["Obsolete"].click()
        self.assertEqual(self.vault_state("bracket")["state"], "Obsolete")
        self.assertEqual(lb.buttons, {})
        self.assertIn("no further transitions", lb.status.text())

    def test_transition_refused_while_locked_names_holder(self):
        self.vb.checkout("plate")
        lp = panels.show_panel("lifecycle", "plate")
        lp.buttons["InReview"].click()
        errs = self.errors()
        self.assertEqual(len(errs), 1, dialogs.shown)
        self.assertIn("bob", errs[0][2])
        self.assertEqual(self.vault_state("plate")["state"], "WIP")


class TestHeadlessImport(unittest.TestCase):
    def test_pdm_imports_without_pyside_or_gui_package(self):
        code = ("import sys\n"
                "before = {m for m in sys.modules if m.startswith('PySide')}\n"
                "import PDM, PDM.vault\n"
                "after = {m for m in sys.modules if m.startswith('PySide')}\n"
                "assert after == before, sorted(after - before)\n"
                "assert 'PDMGui' not in sys.modules\n"
                "print('HEADLESS-OK')\n")
        cmd = os.path.join(App.getHomePath(), "bin", "FreeCADCmd")
        p = subprocess.run([cmd, "-c", code], env=dict(os.environ, QT_QPA_PLATFORM="offscreen"),
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                           timeout=120)
        self.assertIn("HEADLESS-OK", p.stdout + p.stderr)
        src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for f in ("Init.py", os.path.join("PDM", "__init__.py"), os.path.join("PDM", "vault.py")):
            with open(os.path.join(src, f)) as fh:
                self.assertNotIn("PySide", fh.read(), f)
