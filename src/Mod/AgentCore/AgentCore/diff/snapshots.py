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

from . import compare, transplant


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

    def entry(self, turn_id, document=None):
        for e in self.entries:
            if e["turn"] == turn_id and (document is None or e["document"] == document):
                return e
        raise KeyError((turn_id, document))

    def save_before(self, doc, turn_id, label="", dump=None):
        """Write the before-state copy; ``dump`` (the turn's canonical F1
        dump) is stored beside it so a restore can tell whether it needs a
        recompute at all."""
        seq = len(self.entries) + 1
        folder = os.path.join(self.root, f"{seq:04d}-{turn_id}")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{doc.Name}.FCStd")
        doc.saveCopy(path)
        dump_path = None
        if dump is not None:
            dump_path = os.path.join(folder, f"{doc.Name}.dump.json")
            with open(dump_path, "w", encoding="utf-8") as f:
                json.dump(dump, f, sort_keys=True)
        e = {"turn": turn_id, "seq": seq, "document": doc.Name, "label": label, "path": path,
             "dump": dump_path, "status": "open", "undo_name": None, "time": time.time()}
        self.entries.append(e)
        self._save_index()
        return dict(e)

    def record(self, turn_id, document=None, **fields):
        self.entry(turn_id, document).update(fields)
        self._save_index()

    # ----------------------------------------------------------- restore
    def capture_snapshot(self, turn_id, document=None):
        """Capture of a snapshot, read from a hidden temporary document.

        Documents the snapshot pulls in through XLinks are closed again.
        """
        e = self.entry(turn_id, document)
        prev = App.ActiveDocument.Name if App.ActiveDocument else None
        open_before = set(App.listDocuments())
        tmp = App.openDocument(e["path"], True, True)
        try:
            return transplant.capture(tmp)
        finally:
            for name in list(App.listDocuments()):
                if name not in open_before:
                    App.closeDocument(name)
            if prev and prev in App.listDocuments():
                App.setActiveDocument(prev)

    def _dump_of(self, turn_id, document):
        p = self.entry(turn_id, document).get("dump")
        if not p or not os.path.exists(p):
            return None
        with open(p, encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _dump(doc):
        prev = App.ActiveDocument.Name if App.ActiveDocument else None
        App.setActiveDocument(doc.Name)
        try:
            return compare.snapshot_dump(doc)
        finally:
            if prev and prev != doc.Name and prev in App.listDocuments():
                App.setActiveDocument(prev)

    def restore(self, doc, turn_id, transaction=None, document=None):
        """Replay the before-state of ``turn_id`` into ``doc``.

        ``transaction``: an undo-entry name to record the restore under, or
        None to apply it outside any transaction (used by reject).
        """
        cap = self.capture_snapshot(turn_id, document or doc.Name)
        if transaction:
            App.setActiveDocument(doc.Name)
            App.setActiveTransaction(transaction)
        try:
            changed = set()
            problems = transplant.apply(doc, cap, changed=changed, recompute=False)
            # Every output property is in the file, so a recompute is needed
            # only where the replay leaves the state short of the saved dump
            # (solver details of re-created sketches are not saved). Skipping
            # it otherwise keeps solver outputs (Assembly placements) exactly
            # as saved: a solver re-run from a restored state can land on a
            # different solution.
            ref = self._dump_of(turn_id, document or doc.Name)
            if ref is not None:
                transplant.align_touched(doc, ref)
            if ref is None or compare.differences(ref, self._dump(doc)):
                for name in sorted(changed):
                    o = doc.getObject(name)
                    if o is not None:
                        o.touch()
                doc.recompute()
        finally:
            if transaction:
                doc.commitTransaction()
        if problems:
            raise transplant.TransplantError("; ".join(problems[:10]))
        return problems

    def revert(self, doc, turn_id):
        """Return ``doc`` to the state before ``turn_id``. Returns the method used."""
        start = self.entry(turn_id, doc.Name)
        later = [e for e in self.entries if e["document"] == doc.Name and e["seq"] >= start["seq"]
                 and e["status"] == "accepted" and e["undo_name"]]
        names = [e["undo_name"] for e in sorted(later, key=lambda e: -e["seq"])]
        method = "snapshot"
        if names and list(doc.UndoNames[: len(names)]) == names:
            for _ in names:
                doc.undo()
            if App.getActiveTransaction() is None:
                doc.recompute()
            method = "undo"
        else:
            self.restore(doc, turn_id, transaction=f"Revert to before {turn_id}", document=doc.Name)
        for e in later:
            e["status"] = "reverted"
        self._save_index()
        return method
