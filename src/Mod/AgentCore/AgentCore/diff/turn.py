# SPDX-License-Identifier: LGPL-2.1-or-later
"""One agent turn = one FreeCAD transaction, with a change set and a human gate.

Usage::

    with AgentTurn(doc, label="add fillet", store=SnapshotStore(dir)) as turn:
        ...agent edits doc...
    cs = turn.change_set()      # what changed, plus F2 verify before/after
    turn.reject()               # or turn.accept(token), token from ApplyAuthority

How the transaction is held (headless, Python only, no C++ guard yet):

- On entry the document is *settled* (one ``recompute()`` outside any
  transaction, so the before-state is a fixed point), F2's checks run, the
  F1 dump is taken, the shapes are kept for the geometry diff, an optional
  before-state ``.FCStd`` copy is written, and the turn transaction is
  booked with ``FreeCAD.setActiveTransaction`` on the document made active.
- **Nested transactions.** Code that behaves like a GUI command
  (``doc.openTransaction``/``commitTransaction``, ``App.setActiveTransaction``/
  ``closeActiveTransaction``, ``doc.abortTransaction``) splits FreeCAD's
  transaction: an open commits the turn's current part, a commit ends
  recording. These C++ methods cannot be intercepted from Python, so the turn
  *detects* every split with a document observer (``slotCommitTransaction``,
  ``slotAbortTransaction``, ``slotCloseTransaction``, ``slotBeforeChangeObject``)
  and immediately re-books the turn transaction, so no change goes unrecorded.
  The turn's changes then sit in K consecutive undo entries ("segments"). A
  nested abort keeps its meaning: it rolls back only the nested segment.
- **Flattening.** ``accept`` turns K segments into exactly one undo entry:
  it captures the after-state with FreeCAD's per-property serialisation,
  undoes the K segments (back to the before-state), opens one transaction
  and replays the captured state into it (``transplant``, no recompute: the
  outputs are replayed too), then verifies the F1 dump equals the after-state
  dump. ``reject`` needs no flattening: it
  undoes the K segments.
- On exit the document is settled inside the turn (``recompute()``), F2 runs
  again, the last segment is committed and the observer removed. The turn
  is then *proposed*: nothing more is recorded into it.

``reject`` restores a dump equal to the pre-turn dump (see ``compare`` for
the one transient cache excluded); if the undo stack no longer holds the
turn (another transaction landed on top, or ``UndoMaxStackSize`` trimmed it)
it restores from the before-state snapshot instead. ``accept`` requires an
``ApplyToken`` minted by the installed ``ApplyAuthority``.
"""

import itertools
import os
import secrets

import FreeCAD as App

from . import authority as _auth
from . import compare, geometry, transplant
from .changeset import object_changes, verify_delta

SCHEMA = "agentcore.diff/1"
_COUNTER = itertools.count(1)


class TurnError(RuntimeError):
    pass


class FlattenError(TurnError):
    pass


def _shapes(doc):
    out = {}
    for o in doc.Objects:
        try:
            s = o.Shape if "Shape" in o.PropertiesList else None
            if s is not None and not s.isNull():
                out[o.Name] = s.copy()
        except Exception:
            pass
    return out


def _derived(diff_line):
    path = diff_line.split(":", 1)[0]
    parts = path.split("/")
    return "/detail/" in path or (len(parts) > 3 and parts[1] == "objects" and parts[3] == "state")


def _align_touched(doc, dump):
    """Give objects the Touched flag they had in ``dump``."""
    for o in dump.get("objects", []):
        obj = doc.getObject(o["name"])
        if obj is None:
            continue
        want, have = "Touched" in o["state"], "Touched" in [str(s) for s in obj.State]
        try:
            if want and not have:
                obj.touch()
            elif have and not want:
                obj.purgeTouched()
        except Exception:
            pass


class _Observer:
    """Re-books the turn transaction whenever nested code closes it."""

    def __init__(self, turn):
        self.turn = turn

    def slotOpenTransaction(self, doc, name):
        rec = self.turn._rec(doc)
        if rec is not None:
            rec["events"].append(("open", name))

    def slotCommitTransaction(self, doc):
        rec = self.turn._rec(doc)
        if rec is not None:
            rec["events"].append(("commit", doc.UndoNames[0] if doc.UndoNames else ""))
            self.turn._rebook(rec)

    def slotAbortTransaction(self, doc):
        rec = self.turn._rec(doc)
        if rec is not None:
            rec["events"].append(("abort", ""))
            self.turn._rebook(rec)

    def slotCloseTransaction(self, abort):
        self.turn._rebook(self.turn._recs[0])

    def slotBeforeChangeObject(self, obj, prop):
        try:
            rec = self.turn._rec(obj.Document)
        except Exception:
            rec = None
        if rec is not None:
            self.turn._rebook(rec)


def _linked_docs(doc):
    """Documents connected to ``doc`` through XLinks, in either direction."""
    seen, todo = {doc.Name: doc}, [doc]
    while todo:
        d = todo.pop()
        for other in list(getattr(d, "OutList", []) or []) + list(getattr(d, "InList", []) or []):
            if other.Name not in seen:
                seen[other.Name] = other
                todo.append(other)
    return [d for n, d in seen.items() if n != doc.Name]


class _Active:
    """Temporarily make ``doc`` the active document (F1 normalises paths
    against the active document, and booking goes to the active document)."""

    def __init__(self, doc):
        self.doc = doc

    def __enter__(self):
        self.prev = App.ActiveDocument.Name if App.ActiveDocument else None
        if self.prev != self.doc.Name:
            App.setActiveDocument(self.doc.Name)

    def __exit__(self, *exc):
        if self.prev and self.prev != self.doc.Name and self.prev in App.listDocuments():
            App.setActiveDocument(self.prev)
        return False


def _dump(doc):
    with _Active(doc):
        return compare.snapshot_dump(doc)


class AgentTurn:
    """Context manager for one agent turn on ``doc`` (see module docstring).

    ``docs``: further documents the turn covers. By default every document
    linked to ``doc`` through XLinks (either direction) is covered: each gets
    its own booked transaction, before and after dumps and its own reject or
    flatten, because FreeCAD records a change only in a document that has a
    transaction booked.
    """

    def __init__(self, doc, label="agent turn", store=None, checks=None, verify=True, geometry_diff=True,
                 tol=geometry.DEFAULT_TOL, docs=None):
        self.doc = doc
        self.label = label
        self.store = store
        self.checks = checks
        self.verify = verify
        self.geometry_diff = geometry_diff
        self.tol = tol
        self.extra_docs = docs
        self.turn_id = f"{doc.Name}-{next(_COUNTER)}-{secrets.token_hex(3)}"
        self.undo_name = f"Agent turn {self.turn_id}: {label}"
        self.status = "new"
        self._recs = []
        self._booked = []
        self._closing = True
        self._obs = None
        self._cs = None
        self.result = None
        self.snapshot = None

    # ------------------------------------------------------------ records
    def _rec(self, doc):
        try:
            name = doc.Name
        except Exception:
            return None
        for r in self._recs:
            if r["name"] == name:
                return r
        return None

    def _main(self):
        return self._recs[0]

    # convenience views of the main document's record
    before = property(lambda self: self._main()["before"])
    after = property(lambda self: self._main()["after"])
    before_shapes = property(lambda self: self._main()["before_shapes"])
    after_shapes = property(lambda self: self._main()["after_shapes"])
    base_undo = property(lambda self: self._main()["base_undo"])
    base_names = property(lambda self: self._main()["base_names"])
    segments = property(lambda self: self._main()["segments"])
    _events = property(lambda self: self._main()["events"])

    # ------------------------------------------------------------ booking
    def _book(self, rec):
        with _Active(rec["doc"]):
            tid = App.setActiveTransaction(self.undo_name)
        self._booked.append(tid)
        return tid

    def _rebook(self, rec):
        if self._closing or self.status != "open":
            return
        try:
            d = rec["doc"]
            if d.Transacting:
                return
            if rec is self._main():
                act = App.getActiveTransaction()
                if App.ActiveDocument is None or App.ActiveDocument.Name != d.Name or act is None:
                    if App.ActiveDocument is None or App.ActiveDocument.Name != d.Name:
                        App.setActiveDocument(d.Name)
                    if App.getActiveTransaction() is None:
                        self._booked.append(App.setActiveTransaction(self.undo_name))
            elif not d.HasPendingTransaction:
                # Re-booking a booked but still empty document only swaps its id.
                self._book(rec)
        except Exception:
            pass

    def _release_stale(self):
        """Close transaction descriptions we booked that no document holds."""
        for tid in self._booked:
            try:
                App.closeActiveTransaction(False, tid)
            except Exception:
                pass
        self._booked = []

    # ------------------------------------------------------------ enter/exit
    def __enter__(self):
        if self.status != "new":
            raise TurnError("an AgentTurn is used once")
        docs = [self.doc] + [d for d in (self.extra_docs if self.extra_docs is not None else _linked_docs(self.doc))
                             if d.Name != self.doc.Name]
        self._prev_active = App.ActiveDocument.Name if App.ActiveDocument else None
        for d in docs:
            if d.Transacting:
                raise TurnError(f"document {d.Name} is undoing or redoing")
        for d in docs:
            rec = {"doc": d, "name": d.Name, "undo_mode": d.UndoMode, "events": [], "snapshot": None}
            d.UndoMode = 1
            with _Active(d):
                if d.HasPendingTransaction or App.getActiveTransaction() is not None:
                    d.commitTransaction()  # the human's pending edit becomes its own entry
            d.recompute()  # settle: the before-state is a recompute fixed point
            self._recs.append(rec)
        self.verify_before = self._run_verify() if self.verify else None
        for rec in self._recs:
            d = rec["doc"]
            rec["before"] = _dump(d)
            rec["before_shapes"] = _shapes(d) if self.geometry_diff else {}
            if self.store is not None:
                rec["snapshot"] = self.store.save_before(d, self.turn_id, self.label)
            rec["base_undo"] = d.UndoCount
            rec["base_names"] = list(d.UndoNames)
        self.snapshot = self._main()["snapshot"]
        self.status = "open"
        self._closing = False
        self._obs = _Observer(self)
        App.addDocumentObserver(self._obs)
        for rec in reversed(self._recs):  # main document last, so it ends up active
            self._book(rec)
        App.setActiveDocument(self.doc.Name)
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                for rec in reversed(self._recs):
                    rec["doc"].recompute()  # settle inside the turn
                self.verify_after = self._run_verify() if self.verify else None
        finally:
            self._close_recording()
        if exc_type is not None:
            self.verify_after = None
            self.reject()
            return False
        for rec in self._recs:
            self._count_segments(rec)
            rec["after"] = _dump(rec["doc"])
            rec["after_shapes"] = _shapes(rec["doc"]) if self.geometry_diff else {}
        self.status = "proposed"
        self._cs = self._build_change_set()
        return False

    def _close_recording(self):
        self._closing = True
        try:
            for rec in self._recs:
                d = rec["doc"]
                with _Active(d):
                    if d.HasPendingTransaction or App.getActiveTransaction() is not None:
                        d.commitTransaction()
        finally:
            if self._obs is not None:
                App.removeDocumentObserver(self._obs)
                self._obs = None
            self._release_stale()

    def _restore_env(self):
        for rec in self._recs:
            try:
                rec["doc"].UndoMode = rec["undo_mode"]
            except Exception:
                pass
        if self._prev_active and self._prev_active in App.listDocuments():
            App.setActiveDocument(self._prev_active)

    def _run_verify(self):
        from .. import verify
        try:
            with _Active(self.doc):
                return verify.run_checks(self.doc, self.checks)
        except Exception as exc:
            return {"status": "error", "ok": False, "error": f"{type(exc).__name__}: {exc}", "results": []}

    # ------------------------------------------------------------ change set
    def _geometry(self, rec, ch):
        geo = {}
        if not self.geometry_diff:
            return geo
        names = set(ch["shape_changed"]) | {f["name"] for f in ch["features"]["added"]} | \
            {f["name"] for f in ch["features"]["removed"]}
        for n in sorted(names):
            b, a = rec["before_shapes"].get(n), rec["after_shapes"].get(n)
            if b is None and a is None:
                continue
            try:
                geo[n] = geometry.shape_diff(b, a, tol=self.tol)
            except Exception as exc:
                geo[n] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        return geo

    def _tx_info(self, rec):
        return {"segments": rec["segments"],
                "nested_opened": [e[1] for e in rec["events"] if e[0] == "open" and e[1] != self.undo_name],
                "nested_aborts": sum(1 for e in rec["events"] if e[0] == "abort"),
                "undo_complete": rec["undo_complete"]}

    def _build_change_set(self):
        main = self._main()
        ch = object_changes(main["before"], main["after"])
        linked = {}
        for rec in self._recs[1:]:
            c = object_changes(rec["before"], rec["after"])
            if c["features"]["added"] or c["features"]["removed"] or c["features"]["modified"]:
                linked[rec["name"]] = {"features": c["features"], "properties": c["properties"],
                                       "expressions": c["expressions"], "structure": c["structure"],
                                       "geometry": self._geometry(rec, c), "transactions": self._tx_info(rec)}
        return {
            "schema": SCHEMA,
            "turn": self.turn_id,
            "label": self.label,
            "document": self.doc.Name,
            "features": ch["features"],
            "properties": ch["properties"],
            "expressions": ch["expressions"],
            "structure": ch["structure"],
            "geometry": self._geometry(main, ch),
            "linked_documents": linked,
            "covered_documents": [r["name"] for r in self._recs],
            "verify": {"before": self.verify_before, "after": self.verify_after,
                       **verify_delta(self.verify_before, self.verify_after)},
            "transactions": self._tx_info(main),
            "snapshot": self.snapshot,
        }

    def change_set(self):
        if self._cs is None:
            raise TurnError(f"no change set while the turn is {self.status}")
        return self._cs

    def digest(self):
        cs = dict(self.change_set())
        cs.pop("verify", None)  # timing-free fields only
        return compare.digest(cs)

    # ------------------------------------------------------------ decisions
    def _count_segments(self, rec):
        """Segments = commits observed on this document during the turn.

        Counted from events, not from ``UndoCount``, because FreeCAD trims
        the oldest entry once ``UndoMaxStackSize`` (20) is reached.
        """
        d = rec["doc"]
        rec["segments"] = sum(1 for e in rec["events"] if e[0] == "commit")
        rec["undo_complete"] = rec["segments"] <= d.UndoCount
        rec["segment_names"] = list(d.UndoNames[: rec["segments"]])
        rec["exit_undo"] = (d.UndoCount, list(d.UndoNames))

    @staticmethod
    def _stack_holds(rec):
        d = rec["doc"]
        return rec["undo_complete"] and (d.UndoCount, list(d.UndoNames)) == rec["exit_undo"]

    def reject(self):
        """Undo the whole turn; returns ``{"equal", "method", "differences"}``."""
        if self.status not in ("open", "proposed"):
            raise TurnError(f"cannot reject a turn that is {self.status}")
        if self.status == "open":  # exception inside the body
            for rec in self._recs:
                self._count_segments(rec)
        methods, diffs = {}, []
        for rec in self._recs:
            d = rec["doc"]
            method = "undo"
            if self._stack_holds(rec):
                for _ in range(rec["segments"]):
                    d.undo()
            elif rec["snapshot"] is not None:
                method = "snapshot"
                self.store.restore(d, self.turn_id, transaction=None, document=d.Name)
            else:
                raise TurnError(f"the undo stack of {d.Name} no longer holds this turn and there is no snapshot")
            methods[rec["name"]] = method
        for rec in self._recs:
            self._settle_outside(rec["doc"])
        for rec in self._recs:
            dd = compare.differences(rec["before"], _dump(rec["doc"]))
            if dd and methods[rec["name"]] == "undo" and rec["snapshot"] is not None:
                methods[rec["name"]] = "undo+snapshot"
                self.store.restore(rec["doc"], self.turn_id, transaction=None, document=rec["name"])
                self._settle_outside(rec["doc"])
                dd = compare.differences(rec["before"], _dump(rec["doc"]))
            prefix = "" if rec is self._main() else f"[{rec['name']}]"
            diffs += [prefix + x for x in dd]
        self.status = "rejected"
        self._restore_env()
        self.result = {"decision": "rejected", "equal": not diffs, "method": methods[self.doc.Name],
                       "methods": methods, "differences": diffs}
        return self.result

    def _settle_outside(self, doc):
        n = doc.UndoCount
        with _Active(doc):
            if App.getActiveTransaction() is None:
                doc.recompute()
        if doc.UndoCount != n:
            raise TurnError("settling recompute was recorded into a transaction")

    def accept(self, token=None):
        """Keep the turn as exactly one undo entry per document. Needs a human-minted token."""
        if self.status != "proposed":
            raise TurnError(f"cannot accept a turn that is {self.status}")
        _auth.verify_token(token, self.turn_id, self.digest())
        flattened, drift = {}, []
        for rec in self._recs:
            if rec["segments"] and (rec["segments"] != 1 or rec["segment_names"][0] != self.undo_name):
                if not self._stack_holds(rec):
                    raise TurnError(f"the undo stack of {rec['name']} no longer holds this turn; cannot flatten it")
                drift += self._flatten(rec)
                flattened[rec["name"]] = True
        self.status = "accepted"
        if self.store is not None:
            for rec in self._recs:
                if rec["snapshot"] is not None:
                    self.store.record(self.turn_id, document=rec["name"], status="accepted",
                                      undo_name=self.undo_name if rec["segments"] else None)
        self._restore_env()
        self.result = {"decision": "accepted", "flattened": bool(flattened.get(self.doc.Name)),
                       "flattened_documents": sorted(flattened),
                       "undo_entries": 1 if self._main()["segments"] else 0, "detail_drift": drift}
        return self.result

    def _flatten(self, rec):
        doc = rec["doc"]
        cap = transplant.capture(doc)
        for _ in range(rec["segments"]):
            doc.undo()
        with _Active(doc):
            App.setActiveTransaction(self.undo_name)
            try:
                # No recompute: every output property is in the capture, and
                # solvers (Assembly, Sketcher) re-run from a restored state may
                # not land on the same solution. Touched flags are set to match.
                problems = transplant.apply(doc, cap, recompute=False)
                _align_touched(doc, rec["after"])
                diffs = compare.differences(rec["after"], compare.snapshot_dump(doc))
            finally:
                doc.commitTransaction()
        hard = [d for d in diffs if not _derived(d)]
        if problems or hard:
            raise FlattenError(f"flattened state of {doc.Name} differs from the turn's after-state: "
                               f"{problems[:5]} {hard[:5]}")
        # Derived, non-property data can differ from the turn's own
        # after-state when that state was not a recompute fixed point: F1's
        # ``detail`` caches (TechDraw edge counts and dimension values of
        # views orphaned by a page delete) and object ``state`` flags of
        # features left broken by the turn (Touched/Invalid after a Body
        # delete). Every property, expression and link is still required to
        # match; this drift is reported in ``accept()["detail_drift"]``.
        if not doc.UndoNames or doc.UndoNames[0] != self.undo_name:
            raise FlattenError(f"flatten did not leave one '{self.undo_name}' entry: {doc.UndoNames[:3]}")
        rec["segments"] = 1
        rec["segment_names"] = [self.undo_name]
        rec["exit_undo"] = (doc.UndoCount, list(doc.UndoNames))
        return diffs
