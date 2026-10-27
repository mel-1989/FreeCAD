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
  and replays the captured state into it (``transplant``), then verifies the
  F1 dump equals the after-state dump. ``reject`` needs no flattening: it
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

    def _mine(self, doc):
        try:
            return doc is not None and doc.Name == self.turn.doc.Name
        except Exception:
            return False

    def slotOpenTransaction(self, doc, name):
        if self._mine(doc):
            self.turn._events.append(("open", name))

    def slotCommitTransaction(self, doc):
        if self._mine(doc):
            self.turn._events.append(("commit", doc.UndoNames[0] if doc.UndoNames else ""))
            self.turn._rebook()

    def slotAbortTransaction(self, doc):
        if self._mine(doc):
            self.turn._events.append(("abort", ""))
            self.turn._rebook()

    def slotCloseTransaction(self, abort):
        self.turn._rebook()

    def slotBeforeChangeObject(self, obj, prop):
        try:
            if self._mine(obj.Document):
                self.turn._rebook()
        except Exception:
            pass


class AgentTurn:
    """Context manager for one agent turn on ``doc`` (see module docstring)."""

    def __init__(self, doc, label="agent turn", store=None, checks=None, verify=True, geometry_diff=True,
                 tol=geometry.DEFAULT_TOL):
        self.doc = doc
        self.label = label
        self.store = store
        self.checks = checks
        self.verify = verify
        self.geometry_diff = geometry_diff
        self.tol = tol
        self.turn_id = f"{doc.Name}-{next(_COUNTER)}-{secrets.token_hex(3)}"
        self.undo_name = f"Agent turn {self.turn_id}: {label}"
        self.status = "new"
        self._events = []
        self._booked = []
        self._closing = True
        self._obs = None
        self._cs = None
        self.result = None
        self.snapshot = None

    # ------------------------------------------------------------ booking
    def _book(self):
        if App.ActiveDocument is None or App.ActiveDocument.Name != self.doc.Name:
            App.setActiveDocument(self.doc.Name)
        tid = App.setActiveTransaction(self.undo_name)
        self._booked.append(tid)
        return tid

    def _rebook(self):
        if self._closing or self.status != "open":
            return
        try:
            if self.doc.Transacting:
                return
            act = App.getActiveTransaction()
            if App.ActiveDocument is None or App.ActiveDocument.Name != self.doc.Name or act is None:
                self._book()
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
        doc = self.doc
        if self.status != "new":
            raise TurnError("an AgentTurn is used once")
        if doc.Transacting:
            raise TurnError("document is undoing or redoing")
        self._undo_mode = doc.UndoMode
        doc.UndoMode = 1
        self._prev_active = App.ActiveDocument.Name if App.ActiveDocument else None
        if doc.HasPendingTransaction or App.getActiveTransaction() is not None:
            doc.commitTransaction()  # the human's pending edit becomes its own entry
        doc.recompute()  # settle: the before-state is a recompute fixed point
        self.verify_before = self._run_verify() if self.verify else None
        self.before = compare.snapshot_dump(doc)
        self.before_shapes = _shapes(doc) if self.geometry_diff else {}
        if self.store is not None:
            self.snapshot = self.store.save_before(doc, self.turn_id, self.label)
        self.base_undo = doc.UndoCount
        self.base_names = list(doc.UndoNames)
        self.status = "open"
        self._closing = False
        self._obs = _Observer(self)
        App.addDocumentObserver(self._obs)
        self._book()
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.doc.recompute()  # settle inside the turn
                self.verify_after = self._run_verify() if self.verify else None
        finally:
            self._close_recording()
        if exc_type is not None:
            self.verify_after = None
            self.reject()
            return False
        self._count_segments()
        self.after = compare.snapshot_dump(self.doc)
        self.after_shapes = _shapes(self.doc) if self.geometry_diff else {}
        self.status = "proposed"
        self._cs = self._build_change_set()
        return False

    def _close_recording(self):
        self._closing = True
        try:
            if self.doc.HasPendingTransaction or App.getActiveTransaction() is not None:
                self.doc.commitTransaction()
        finally:
            if self._obs is not None:
                App.removeDocumentObserver(self._obs)
                self._obs = None
            self._release_stale()

    def _restore_env(self):
        try:
            self.doc.UndoMode = self._undo_mode
        except Exception:
            pass
        if self._prev_active and self._prev_active in App.listDocuments():
            App.setActiveDocument(self._prev_active)

    def _run_verify(self):
        from .. import verify
        try:
            return verify.run_checks(self.doc, self.checks)
        except Exception as exc:
            return {"status": "error", "ok": False, "error": f"{type(exc).__name__}: {exc}", "results": []}

    # ------------------------------------------------------------ change set
    def _build_change_set(self):
        ch = object_changes(self.before, self.after)
        geo = {}
        if self.geometry_diff:
            names = set(ch["shape_changed"]) | {f["name"] for f in ch["features"]["added"]} | \
                {f["name"] for f in ch["features"]["removed"]}
            for n in sorted(names):
                b, a = self.before_shapes.get(n), self.after_shapes.get(n)
                if b is None and a is None:
                    continue
                try:
                    geo[n] = geometry.shape_diff(b, a, tol=self.tol)
                except Exception as exc:
                    geo[n] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        nested = [e[1] for e in self._events if e[0] == "open" and e[1] != self.undo_name]
        return {
            "schema": SCHEMA,
            "turn": self.turn_id,
            "label": self.label,
            "document": self.doc.Name,
            "features": ch["features"],
            "properties": ch["properties"],
            "expressions": ch["expressions"],
            "structure": ch["structure"],
            "geometry": geo,
            "verify": {"before": self.verify_before, "after": self.verify_after,
                       **verify_delta(self.verify_before, self.verify_after)},
            "transactions": {"segments": self.segments, "nested_opened": nested,
                             "nested_aborts": sum(1 for e in self._events if e[0] == "abort"),
                             "undo_complete": self.undo_complete},
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
    def _count_segments(self):
        """Segments = commits observed on this document during the turn.

        Counted from events, not from ``UndoCount``, because FreeCAD trims
        the oldest entry once ``UndoMaxStackSize`` (20) is reached.
        """
        self.segments = sum(1 for e in self._events if e[0] == "commit")
        self.undo_complete = self.segments <= self.doc.UndoCount
        self.segment_names = list(self.doc.UndoNames[: self.segments])
        self.exit_undo = (self.doc.UndoCount, list(self.doc.UndoNames))

    def _stack_holds_turn(self):
        return self.undo_complete and (self.doc.UndoCount, list(self.doc.UndoNames)) == self.exit_undo

    def reject(self):
        """Undo the whole turn; returns ``{"equal", "method", "differences"}``."""
        if self.status not in ("open", "proposed"):
            raise TurnError(f"cannot reject a turn that is {self.status}")
        if self.status == "open":  # exception inside the body
            self._count_segments()
        method = "undo"
        if self._stack_holds_turn():
            for _ in range(self.segments):
                self.doc.undo()
        elif self.snapshot is not None:
            method = "snapshot"
            self.store.restore(self.doc, self.snapshot["turn"], transaction=None)
        else:
            raise TurnError("the undo stack no longer holds this turn and there is no snapshot")
        self._settle_outside()
        now = compare.snapshot_dump(self.doc)
        diffs = compare.differences(self.before, now)
        if diffs and method == "undo" and self.snapshot is not None:
            method = "undo+snapshot"
            self.store.restore(self.doc, self.snapshot["turn"], transaction=None)
            self._settle_outside()
            diffs = compare.differences(self.before, compare.snapshot_dump(self.doc))
        self.status = "rejected"
        self._restore_env()
        self.result = {"decision": "rejected", "equal": not diffs, "method": method, "differences": diffs}
        return self.result

    def _settle_outside(self):
        n = self.doc.UndoCount
        if App.getActiveTransaction() is None:
            self.doc.recompute()
        if self.doc.UndoCount != n:
            raise TurnError("settling recompute was recorded into a transaction")

    def accept(self, token=None):
        """Keep the turn as exactly one undo entry. Needs a human-minted token."""
        if self.status != "proposed":
            raise TurnError(f"cannot accept a turn that is {self.status}")
        _auth.verify_token(token, self.turn_id, self.digest())
        flattened = False
        if self.segments and (self.segments != 1 or self.segment_names[0] != self.undo_name):
            if not self._stack_holds_turn():
                raise TurnError("the undo stack no longer holds this turn; cannot flatten it")
            self._flatten()
            flattened = True
        self.status = "accepted"
        if self.store is not None and self.snapshot is not None:
            self.store.record(self.snapshot["turn"], status="accepted", undo_name=self.undo_name)
        self._restore_env()
        self.result = {"decision": "accepted", "flattened": flattened,
                       "undo_entries": 1 if self.segments else 0, "detail_drift": getattr(self, "flatten_drift", [])}
        return self.result

    def _flatten(self):
        doc = self.doc
        cap = transplant.capture(doc)
        for _ in range(self.segments):
            doc.undo()
        App.setActiveDocument(doc.Name)
        App.setActiveTransaction(self.undo_name)
        try:
            problems = transplant.apply(doc, cap)
            doc.recompute()
            diffs = compare.differences(self.after, compare.snapshot_dump(doc))
            if diffs:
                _align_touched(doc, self.after)
                diffs = compare.differences(self.after, compare.snapshot_dump(doc))
        finally:
            doc.commitTransaction()
        hard = [d for d in diffs if not _derived(d)]
        if problems or hard:
            raise FlattenError(f"flattened state differs from the turn's after-state: {problems[:5]} {hard[:5]}")
        # Derived, non-property data can differ from the turn's own
        # after-state when that state was not a recompute fixed point: F1's
        # ``detail`` caches (TechDraw edge counts and dimension values of
        # views orphaned by a page delete) and object ``state`` flags of
        # features left broken by the turn (Touched/Invalid after a Body
        # delete). Every property, expression and link is still required to
        # match; this drift is reported in ``accept()["detail_drift"]``.
        self.flatten_drift = diffs
        if not doc.UndoNames or doc.UndoNames[0] != self.undo_name:
            raise FlattenError(f"flatten did not leave one '{self.undo_name}' entry: {doc.UndoNames[:3]}")
        self.segments = 1
        self.segment_names = [self.undo_name]
        self.exit_undo = (doc.UndoCount, list(doc.UndoNames))
