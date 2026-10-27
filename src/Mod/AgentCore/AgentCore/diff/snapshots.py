# SPDX-License-Identifier: LGPL-2.1-or-later
"""Before-state ``.FCStd`` snapshots and multi-turn revert.

Each turn opened with ``store=`` writes ``<root>/<seq>-<turn>/<DocName>.FCStd``
with ``doc.saveCopy`` (the live document's FileName is untouched) before its
transaction opens, and an entry in ``<root>/index.json``.

``revert(doc, turn_id)`` brings the document back to the state before that
turn, undoing every later accepted turn too:

- **undo** when the top of the undo stack is exactly the single entries of
  those accepted turns, newest first (FreeCAD's own undo, cheapest and exact);
- **snapshot** otherwise (another edit landed on top, the stack was trimmed
  at ``UndoMaxStackSize``, or the document was reopened): the snapshot is
  opened as a hidden temporary document, captured property by property and
  replayed into the live document inside one transaction named
  ``Revert to before <turn>``, so the revert itself is one undo entry.
"""

import json
import os
import time

import FreeCAD as App

from . import transplant


class SnapshotStore:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)
        self.index_path = os.path.join(self.root, "index.json")
        self.entries = []
        if os.path.exists(self.index_path):
            with open(self.index_path, encoding="utf-8") as f:
                self.entries = json.load(f)

    def _save_index(self):
        tmp = self.index_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.entries, f, indent=1, sort_keys=True)
        os.replace(tmp, self.index_path)

    def entry(self, turn_id):
        for e in self.entries:
            if e["turn"] == turn_id:
                return e
        raise KeyError(turn_id)

    def save_before(self, doc, turn_id, label=""):
        seq = len(self.entries) + 1
        folder = os.path.join(self.root, f"{seq:04d}-{turn_id}")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{doc.Name}.FCStd")
        doc.saveCopy(path)
        e = {"turn": turn_id, "seq": seq, "document": doc.Name, "label": label, "path": path,
             "status": "open", "undo_name": None, "time": time.time()}
        self.entries.append(e)
        self._save_index()
        return dict(e)

    def record(self, turn_id, **fields):
        self.entry(turn_id).update(fields)
        self._save_index()

    # ----------------------------------------------------------- restore
    def capture_snapshot(self, turn_id):
        """Capture of a snapshot, read from a hidden temporary document."""
        e = self.entry(turn_id)
        prev = App.ActiveDocument.Name if App.ActiveDocument else None
        tmp = App.openDocument(e["path"], True, True)
        try:
            return transplant.capture(tmp)
        finally:
            App.closeDocument(tmp.Name)
            if prev and prev in App.listDocuments():
                App.setActiveDocument(prev)

    def restore(self, doc, turn_id, transaction=None):
        """Replay the before-state of ``turn_id`` into ``doc``.

        ``transaction``: an undo-entry name to record the restore under, or
        None to apply it outside any transaction (used by reject).
        """
        cap = self.capture_snapshot(turn_id)
        if transaction:
            App.setActiveDocument(doc.Name)
            App.setActiveTransaction(transaction)
        try:
            problems = transplant.apply(doc, cap)
            doc.recompute()
        finally:
            if transaction:
                doc.commitTransaction()
        if problems:
            raise transplant.TransplantError("; ".join(problems[:10]))
        return problems

    def revert(self, doc, turn_id):
        """Return ``doc`` to the state before ``turn_id``. Returns the method used."""
        start = self.entry(turn_id)
        later = [e for e in self.entries if e["document"] == doc.Name and e["seq"] >= start["seq"]
                 and e["status"] == "accepted"]
        names = [e["undo_name"] for e in sorted(later, key=lambda e: -e["seq"])]
        method = "snapshot"
        if names and list(doc.UndoNames[: len(names)]) == names:
            for _ in names:
                doc.undo()
            if App.getActiveTransaction() is None:
                doc.recompute()
            method = "undo"
        else:
            self.restore(doc, turn_id, transaction=f"Revert to before {turn_id}")
        for e in later:
            e["status"] = "reverted"
        self._save_index()
        return method
