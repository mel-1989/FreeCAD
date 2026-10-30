# SPDX-License-Identifier: LGPL-2.1-or-later
"""One agent turn = one FreeCAD transaction, with a change set and a human gate.

Usage::

    with AgentTurn(doc, label="add fillet", store=SnapshotStore(dir)) as turn:
        ...agent edits doc...
    cs = turn.change_set()      # what changed, plus F2 verify before/after
    turn.reject()               # or turn.accept(token), token from ApplyAuthority

How the transaction is held. With the C++ guard (``Document.beginAgentTurn``, used
whenever the build has it) the turn is one guarded transaction and nested
transactions are savepoints inside it, see "The C++ guard" below. Without it
(older builds, or ``AgentTurn(..., use_guard=False)``) the Python fallback
described next detects the splits after the fact:

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

**The C++ guard.** ``doc.beginAgentTurn(name)`` books the turn transaction and
``doc.endAgentTurn(commit)`` ends it. While it is active, every nested open
(``doc.openTransaction``, ``App.setActiveTransaction``, ``AutoTransaction``,
GUI ``doCommand``) is a savepoint inside the one transaction: a nested commit
keeps its changes, a nested abort rolls back only the changes since its own
open, an empty close is absorbed, and ``undo``/``redo``/``clearUndos`` are
refused and reported as tampering. The turn then always has one segment (one
undo entry), ``accept`` never flattens a single-document turn, and the
document observer is not installed. The change set's ``transactions`` keys are
filled from the guard's report. ``endAgentTurn`` waits for a recompute of the
document on the async worker, so a turn never commits halfway through one.

``reject`` restores a dump equal to the pre-turn dump (see ``compare`` for
the transient caches excluded; ``result["exact"]`` is the no-tolerance,
byte-level check); if the undo stack no longer holds the turn (another
transaction landed on top, or ``UndoMaxStackSize`` trimmed it) it restores
from the before-state snapshot instead, and it empties the redo stack the
turn left. ``accept`` requires an ``ApplyToken`` minted by the installed
``ApplyAuthority`` and is all or nothing across covered documents.
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


class TurnStale(TurnError):
    """Reject refused: a covered document no longer holds exactly the turn's after-state.

    Nothing was touched and the turn is still ``proposed``. Rewinding would discard
    a change the turn did not make."""


class RejectFailed(TurnError):
    """A step of reject failed after the rewind started. The after-state was
    put back (``restored``) or could not be (the turn is then ``reject_failed``)."""


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


def _undo_limit(doc):
    """Document::UndoMaxStackSize is not exposed to Python: 20 unless the GUI
    set it from the MaxUndoSize preference."""
    try:
        if App.GuiUp:
            return int(App.ParamGet("User parameter:BaseApp/Preferences/Document").GetInt("MaxUndoSize", 20))
    except Exception:
        pass
    return 20


def _geo_signature(doc):
    """Cheap per-object geometry signature: type, topology counts, volume,
    area and bounding box of every non-null ``Shape``. Used by ``accept`` to
    confirm the live document still holds the reviewed geometry."""
    out = {}
    for o in doc.Objects:
        try:
            s = o.Shape if "Shape" in o.PropertiesList else None
            if s is None or s.isNull():
                continue
            bb = s.BoundBox
            box = [bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax] if bb.isValid() else []
            out[o.Name] = (s.ShapeType, len(s.Solids), len(s.Faces), len(s.Edges), len(s.Vertexes),
                           float(s.Volume), float(s.Area), box)
        except Exception as exc:
            out[o.Name] = ("error", type(exc).__name__)
    return out


def _geo_differences(a, b, rel=1e-9):
    diffs = []
    for name in sorted(set(a) | set(b)):
        x, y = a.get(name), b.get(name)
        if x is None or y is None:
            diffs.append(f"{name}: shape {'added' if x is None else 'removed'} since the proposal")
            continue
        if len(x) != len(y) or x[:5] != y[:5]:
            diffs.append(f"{name}: shape topology {x[:5]} -> {y[:5]}")
            continue
        nums_x = list(x[5:7]) + list(x[7])
        nums_y = list(y[5:7]) + list(y[7])
        if len(nums_x) != len(nums_y):
            diffs.append(f"{name}: bounding box validity changed")
            continue
        scale = max([1.0] + [abs(v) for v in nums_x])
        if any(abs(u - v) > rel * scale for u, v in zip(nums_x, nums_y)):
            diffs.append(f"{name}: shape volume/area/bbox changed since the proposal")
    return diffs


def _derived(diff_line, dump, allowed):
    """True when a flatten difference is tolerated derived data.

    Only on objects the turn itself added or modified (``allowed``): their
    ``state`` flags (Touched/Invalid of features the turn left broken) and,
    on TechDraw objects only, F1's ``detail`` caches. Any difference on an
    object the turn did not change, and any property, expression or link
    difference anywhere, is a hard flatten failure.
    """
    parts = diff_line.split(":", 1)[0].split("/")
    if len(parts) < 4 or parts[1] != "objects":
        return False
    try:
        obj = dump["objects"][int(parts[2])]
    except (ValueError, IndexError, KeyError):
        return False
    if obj.get("name") not in allowed:
        return False
    if parts[3] == "state":
        return True
    return parts[3] == "detail" and str(obj.get("type", "")).startswith("TechDraw::")


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

    def slotUndoDocument(self, doc):
        self._history(doc, "undo")

    def slotRedoDocument(self, doc):
        self._history(doc, "redo")

    def _history(self, doc, what):
        """undo/redo inside a turn is tampering: the turn is aborted at its
        exit, and an undo that reached into the human's pre-turn history is
        redone at once, before anything can open a transaction (which would
        empty the redo stack and lose that history for good)."""
        rec = self.turn._rec(doc)
        if rec is None or self.turn.status != "open":
            return
        rec["events"].append((what, ""))
        self.turn._tamper(rec, f"{what}() called inside the turn")
        if self.turn._repairing:
            return
        self.turn._repairing = True
        try:
            while doc.UndoCount < rec["base_undo"] and doc.RedoCount and not doc.Transacting:
                doc.redo()
        except Exception as exc:
            self.turn._tamper(rec, f"could not restore pre-turn history after {what}: {exc}")
        finally:
            self.turn._repairing = False

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
                 tol=geometry.DEFAULT_TOL, docs=None, use_guard=True):
        self.doc = doc
        self.use_guard = bool(use_guard)
        self._guarded = False
        self._guard_failed = False
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
        self._tampered = []      # history tampering seen during the turn (undo, redo, clearUndos)
        self._repairing = False

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

    @property
    def tampered(self):
        """History tampering seen so far. With the C++ guard it is read live from the
        documents, so a caller can check it while the turn is still open."""
        if self._guarded and self.status == "open" and not self._closing:
            for rec in self._recs:
                try:
                    for why in rec["doc"].agentTurnStatus()["tampering"]:
                        self._tamper(rec, why)
                except Exception:
                    pass
        return self._tampered

    def _tamper(self, rec, why):
        rec["tampered"] = True
        msg = f"{rec['name']}: {why}"
        if msg not in self._tampered:
            self._tampered.append(msg)

    def _check_history(self, rec):
        """clearUndos (and a lost pre-turn entry) fire no observer slot; they
        are found by the human's pre-turn undo names no longer sitting under
        the turn's entries."""
        d, base = rec["doc"], rec.get("base_names")
        if base is None:
            return
        segs = sum(1 for e in rec["events"] if e[0] == "commit")
        names = list(d.UndoNames)
        expected = segs + len(base)
        limit = _undo_limit(d)
        if expected <= limit:  # no trimming by UndoMaxStackSize: the stack must be segments + base exactly
            lost = names[len(names) - len(base):] != base if len(names) >= len(base) else True
        else:  # trimmed: only a stack shorter than the limit says history was removed
            lost = len(names) < limit
        if lost:
            self._tamper(rec, "pre-turn undo history was removed inside the turn (clearUndos or undo)")

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
            rec = {"doc": d, "name": d.Name, "undo_mode": d.UndoMode, "events": [], "snapshot": None,
                   "tampered": False}
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
                rec["snapshot"] = self.store.save_before(d, self.turn_id, self.label, dump=rec["before"])
            rec["before_cap"] = transplant.capture(d)
            rec["before_raw"] = compare.raw_state_of_capture(rec["before_cap"])
            rec["base_undo"] = d.UndoCount
            rec["base_names"] = list(d.UndoNames)
        self.snapshot = self._main()["snapshot"]
        self.status = "open"
        self._closing = False
        self._guarded = self.use_guard and all(hasattr(r["doc"], "beginAgentTurn") for r in self._recs)
        if self._guarded:
            begun = []
            try:
                for rec in reversed(self._recs):  # main document last
                    rec["guard_tid"] = rec["doc"].beginAgentTurn(self.undo_name)
                    begun.append(rec)
            except Exception:
                for rec in begun:
                    try:
                        rec["doc"].endAgentTurn(False)
                    except Exception:
                        pass
                self.status = "new"
                raise
        else:
            self._obs = _Observer(self)
            App.addDocumentObserver(self._obs)
            for rec in reversed(self._recs):  # main document last, so it ends up active
                self._book(rec)
        App.setActiveDocument(self.doc.Name)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._guard_failed:
            return False  # already ended and rejected by a refused guarded end
        try:
            if exc_type is None:
                for rec in reversed(self._recs):
                    rec["doc"].recompute()  # settle inside the turn
                self.verify_after = self._run_verify() if self.verify else None
        finally:
            self._close_recording()
        for rec in self._recs:
            self._check_history(rec)
        if exc_type is not None:
            self.verify_after = None
            self.reject()
            return False
        if self.tampered:
            self.verify_after = None
            res = self.reject()
            res["aborted"] = list(self.tampered)
            raise TurnError("agent turn aborted, undo history was touched inside it: " + "; ".join(self.tampered))
        for rec in self._recs:
            self._count_segments(rec)
            rec["after"] = _dump(rec["doc"])
            rec["after_shapes"] = _shapes(rec["doc"]) if self.geometry_diff else {}
            rec["after_geo"] = _geo_signature(rec["doc"])
        self.status = "proposed"
        self._cs = self._build_change_set()
        return False

    def _end_guard(self, commit=True):
        """End the C++ guarded turn of every document and turn its report into the
        records the rest of this module reads (one commit event when the turn
        recorded something, the nested opens and aborts, tampering).

        All or nothing across documents: every covered document is first checked
        to be able to end (worker idle, not locked) and nothing is committed
        unless all can. If one cannot, every turn is aborted instead; if a commit
        still refuses after others committed, those entries are undone again."""
        active = [rec for rec in self._recs if rec["doc"].isAgentTurnActive()]
        blocked = []
        for rec in active:
            ok, why = rec["doc"].canEndAgentTurn(60.0)
            if not ok:
                blocked.append(f"{rec['name']}: {why}")
        done, taken = [], []
        failure = None
        if blocked:
            failure = TurnError("guarded turn could not end, aborted in every document: " + "; ".join(blocked))
            commit = False
        for rec in active:
            d = rec["doc"]
            rep = d.endAgentTurn(commit)
            if rep["refused"]:
                failure = failure or TurnError(f"guarded turn of {rec['name']} could not end: {rep['reason']}")
                if commit:  # raced with the worker after the check: take the others back
                    for prev in done:
                        if prev["guard_report"]["recorded"]:
                            n = prev["doc"].UndoCount
                            try:
                                prev["doc"].undo()
                            except Exception:
                                pass
                            if prev["doc"].UndoCount < n:
                                taken.append(prev)
                    commit = False
                continue
            rec["guard_report"] = rep
            done.append(rec)
            ev = [("open", n) for n in rep["nested_names"]]
            ev += [("abort", "")] * rep["nested_aborts"]
            if rep["recorded"] and rep["committed"]:
                ev.append(("commit", self.undo_name))
            rec["events"] = ev
            for why in rep["tampering"]:
                self._tamper(rec, why)
        if failure is not None:
            for rec in self._recs:  # leave nothing open
                try:
                    if rec["doc"].isAgentTurnActive():
                        rec["doc"].endAgentTurn(False)
                except Exception:
                    pass
            # The turn is over, rejected, in every document: documents that never committed are
            # already restored; the ones taken back were undone above, so reject must not undo them
            # again (that would eat the human's previous entry) and their redo entry is dropped.
            for rec in self._recs:
                if any(rec is t for t in taken):
                    rec["phase"] = "undone"
            self._guard_failed = True
            try:
                self._finish_reject({rec["name"]: "undo" for rec in self._recs}, guard_error=str(failure))
            except Exception:
                self.status = "rejected"
            raise failure

    def _close_recording(self):
        self._closing = True
        if self._guarded:
            try:
                self._end_guard(True)
            finally:
                self._release_stale()
            return
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
                keep = {}
                geo[n] = geometry.shape_diff(b, a, tol=self.tol, keep=keep)
                rec.setdefault("pieces", {})[n] = keep
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

    def overlay_pieces(self):
        """Shapes the GUI overlay draws for the main document: ``[(object, "added"|"removed", shape)]``.

        Added material is green, removed material red. A feature added or removed whole contributes
        its own shape; a modified one the local-boolean pieces of ``geometry.shape_diff``. An object
        that another changed object consumes (a Pad inside its Body, the Base of a Cut) is left
        out, so a result is drawn once.
        """
        main, cs = self._main(), self.change_set()
        geo = cs["geometry"]
        consumed = set()
        for dump in (main["before"], main["after"]):
            for o in dump.get("objects", []):
                if o["name"] in geo:
                    consumed |= {n for n in o.get("out_list", []) if n != o["name"]}
        out = []
        for name in sorted(geo):
            g = geo[name]
            if name in consumed or g.get("status") in (None, "none", "error", "unchanged"):
                continue
            if g["status"] == "added":
                s = main["after_shapes"].get(name)
                if s is not None and s.Faces:
                    out.append((name, "added", s))
            elif g["status"] == "removed":
                s = main["before_shapes"].get(name)
                if s is not None and s.Faces:
                    out.append((name, "removed", s))
            else:
                pieces = main.get("pieces", {}).get(name, {})
                for kind in ("added", "removed"):
                    if pieces.get(kind) is not None:
                        out.append((name, kind, pieces[kind]))
        return out

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
        return not rec.get("tampered") and rec["undo_complete"] and \
            (d.UndoCount, list(d.UndoNames)) == rec["exit_undo"]

    # ------------------------------------------------------------ reject
    def reject(self):
        """Undo the whole turn.

        Returns ``{"equal", "exact", "method", "methods", "settled",
        "differences"}``. ``equal``: the F1 dump matches the pre-turn dump at
        F1's tolerance (1e-9). ``exact``: it matches with no tolerance at all
        and every persistent non-shape property serialises to the same bytes
        (``compare.raw_state``). The turn's undone entries are dropped from
        the redo stack, so a later ``redo()`` cannot bring the rejected
        changes back without an apply token.
        """
        if self._guard_failed and self.status == "rejected":
            return self.result  # the guarded end already rejected the turn; nothing more to undo
        if self.status not in ("open", "proposed"):
            raise TurnError(f"cannot reject a turn that is {self.status}")
        if self.status == "open":  # exception inside the body
            for rec in self._recs:
                self._count_segments(rec)
                self._check_history(rec)
        if self.status == "open":
            methods = {rec["name"]: self._rewind(rec) for rec in self._recs}
            return self._finish_reject(methods)
        stale = self._stale_reasons()
        if stale:  # before anything is touched: rewinding would discard work the turn did not make
            raise TurnStale("reject refused: the document changed after this turn and rewinding would discard "
                            "that work; undo your own edits (or use the snapshot revert, which discards "
                            "later work on purpose) and reject again: " + "; ".join(stale[:6]))
        saved = {rec["name"]: self._save_after(rec) for rec in self._recs}
        methods = {}
        try:
            for rec in self._recs:
                methods[rec["name"]] = self._rewind(rec)
            return self._finish_reject(methods)
        except Exception as exc:
            if self.status == "rejected":  # the failure came after the decision was recorded
                raise
            ok, left = self._restore_after(saved)
            if not ok:
                self.status = "reject_failed"
                self.result = {"decision": "reject_failed", "error": f"{type(exc).__name__}: {exc}",
                               "differences": left}
            raise RejectFailed(f"reject failed ({type(exc).__name__}: {exc}); "
                               + ("the documents were put back to the turn's after-state and the turn is still "
                                  "proposed" if ok else "the documents could not be fully put back: "
                                  + "; ".join(left[:5]))) from exc

    def _rec_differences(self, rec):
        d = rec["doc"]
        prefix = "" if rec is self._main() else f"[{rec['name']}]"
        out = [prefix + x for x in compare.differences(rec["after"], _dump(d))]
        return out + [prefix + x for x in _geo_differences(rec.get("after_geo", {}), _geo_signature(d))]

    def _stale_reasons(self):
        """Why reject would discard something the turn did not make (empty: safe).

        Per covered document either the turn's entries were undone through the
        stack and nothing but them (the human's own Undo; the rewind only drops
        the turn's redo entries), or the document equals the reviewed
        after-state and no foreign transaction sits on the stack."""
        out = []
        for rec in self._recs:
            d, name = rec["doc"], rec["name"]
            if "exit_undo" not in rec:
                continue
            count, names = rec["exit_undo"]
            if self.undo_name in list(d.RedoNames):
                k = count - d.UndoCount
                if 0 < k <= max(rec["segments"], 1) and list(d.UndoNames) == names[k:] and \
                        all(n == self.undo_name for n in list(d.RedoNames)[:k]):
                    continue
                out.append(f"{name}: the undo stack no longer matches the turn")
                continue
            if (d.UndoCount, list(d.UndoNames)) != (count, names):
                out.append(f"{name}: another transaction was recorded after the turn "
                           f"(undo stack now {list(d.UndoNames)[:3]})")
            # not work to protect: a Touched flag left by the human's own Undo, and the document's
            # own label or file name (Save As renames it while a turn waits)
            drift = [x for x in self._rec_differences(rec)
                     if not ("/state" in x.split(":", 1)[0] and "Touched" in x)
                     and not x.lstrip("[").split("]", 1)[-1].startswith("/document/")]
            if drift:
                out.append(f"{name}: no longer equals the turn's after-state ({drift[0]})")
        return out

    def _save_after(self, rec):
        d = rec["doc"]
        return {"rec": rec, "cap": transplant.capture(d), "dump": _dump(d), "undo": (d.UndoCount, list(d.UndoNames)),
                "redo": (d.RedoCount, list(d.RedoNames))}

    def _restore_after(self, saved):
        """Put every document back to what it was when reject began (redo of the undone entries, else the capture); (ok, leftover differences)."""
        left = []
        for name, sv in saved.items():
            rec, d = sv["rec"], sv["rec"]["doc"]
            try:
                with _Active(d):
                    if d.HasPendingTransaction or App.getActiveTransaction() is not None:
                        d.commitTransaction()
                    if (d.UndoCount, list(d.UndoNames)) != sv["undo"] and d.UndoCount < sv["undo"][0] \
                            and d.RedoCount >= sv["undo"][0] - d.UndoCount:
                        for _ in range(sv["undo"][0] - d.UndoCount):   # the entries the rewind undid come back
                            d.redo()
                    if compare.differences(sv["dump"], _dump(d)):
                        transplant.apply(d, sv["cap"], recompute=False)
                        transplant.align_touched(d, sv["dump"])
                    if (d.UndoCount, list(d.UndoNames)) != sv["undo"]:
                        # the turn's undo entry could not come back: a later reject uses the capture
                        # (and the check above must accept this stack as the turn's own)
                        rec["exit_undo"] = (d.UndoCount, list(d.UndoNames))
                        rec["undo_complete"] = False
            except Exception as exc:
                left.append(f"{name}: restore raised {type(exc).__name__}: {exc}")
            left += [f"[{name}]{x}" for x in compare.differences(sv["dump"], _dump(d))]
        return not left, left

    def _rewind(self, rec):
        """Bring one document back to its before-state; returns the method."""
        d, phase = rec["doc"], rec.get("phase")
        if phase == "committed" and d.UndoNames and d.UndoNames[0] == self.undo_name:
            d.undo()  # the one flattened entry
            return "undo"
        if phase == "undone":  # flatten undid the segments, nothing was replayed
            return "undo"
        if phase is None and self._stack_holds(rec):
            for _ in range(rec["segments"]):
                d.undo()
            return "undo"
        if phase is None:
            # The stack cannot be trusted as a whole, but the turn's own entries
            # still on top of it are undone, so none is left for a later undo or
            # redo to replay; the snapshot or capture restore does the rest.
            while d.UndoNames and d.UndoNames[0] == self.undo_name and not d.Transacting:
                n = d.UndoCount
                d.undo()
                if d.UndoCount >= n:
                    break
        if rec["snapshot"] is not None:
            self.store.restore(d, self.turn_id, transaction=None, document=d.Name)
            return "snapshot"
        if rec.get("before_cap") is not None:
            self._restore_capture(rec)
            return "capture"
        raise TurnError(f"the undo stack of {d.Name} no longer holds this turn and there is no snapshot")

    def _restore_capture(self, rec):
        """Replay the before-state captured on entry (no snapshot file needed).

        Runs outside any transaction, so nothing lands on the undo stack."""
        d = rec["doc"]
        with _Active(d):
            if d.HasPendingTransaction or App.getActiveTransaction() is not None:
                d.commitTransaction()
            n = d.UndoCount
            problems = transplant.apply(d, rec["before_cap"], recompute=False)
            transplant.align_touched(d, rec["before"])
        if d.UndoCount != n:
            raise TurnError(f"capture restore of {d.Name} was recorded into a transaction")
        rec["capture_problems"] = problems
        return problems

    def _exact(self, rec):
        d = rec["doc"]
        return not compare.differences(rec["before"], _dump(d), rel_tol=0.0, abs_tol=0.0) and \
            not compare.raw_differences(rec["before_raw"], compare.raw_state(d))

    def _finish_reject(self, methods, **extra):
        for rec in self._recs:
            # A snapshot or capture restore undid nothing, so the redo stack is
            # the human's -- unless the turn's own entries sit on it (undone
            # inside the turn or after the proposal): then redo would bring
            # the rejected change back without a token, so it is emptied.
            on_redo = self.undo_name in list(rec["doc"].RedoNames)
            if on_redo or methods[rec["name"]] not in ("snapshot", "capture"):
                self._drop_redos(rec, force=on_redo)
        diffs, settled, exact = [], {}, {}
        for rec in self._recs:
            d, name = rec["doc"], rec["name"]
            # Undo restores every property; only Touched flags are left set.
            # Clearing them, rather than recomputing, keeps solver outputs
            # (Assembly placements) bit for bit as they were.
            # Undo restores every property; what it leaves behind is Touched
            # flags. When that is the only difference, the flags are cleared
            # instead of recomputing, which keeps solver outputs (Assembly
            # placements) bit for bit. Anything else (Invalid flags after a
            # Body delete is undone) gets the settling recompute.
            gaps = compare.differences(rec["before"], _dump(d), rel_tol=0.0, abs_tol=0.0)
            only_touched = all("/state" in x.split(":", 1)[0] and "Touched" in x for x in gaps)
            if only_touched and not compare.raw_differences(rec["before_raw"], compare.raw_state(d)):
                transplant.align_touched(d, rec["before"])
                settled[name] = False
            else:
                self._settle_outside(d)
                settled[name] = True
            dd = compare.differences(rec["before"], _dump(d))
            if dd and methods[name] == "undo" and rec["snapshot"] is not None:
                methods[name] = "undo+snapshot"
                self.store.restore(d, self.turn_id, transaction=None, document=name)
                self._settle_outside(d)
                settled[name] = True
                dd = compare.differences(rec["before"], _dump(d))
            if dd and rec.get("before_cap") is not None and not str(methods[name]).endswith("capture"):
                methods[name] = f"{methods[name]}+capture"
                self._restore_capture(rec)
                if compare.differences(rec["before"], _dump(d), rel_tol=0.0, abs_tol=0.0):
                    self._settle_outside(d)
                    settled[name] = True
                dd = compare.differences(rec["before"], _dump(d))
            exact[name] = self._exact(rec)
            prefix = "" if rec is self._main() else f"[{name}]"
            diffs += [prefix + x for x in dd]
        self.status = "rejected"
        self._restore_env()
        self.result = {"decision": "rejected", "equal": not diffs, "exact": all(exact.values()),
                       "exact_documents": exact, "method": methods[self.doc.Name], "methods": methods,
                       "settled": settled, "differences": diffs, **extra}
        return self.result

    def _drop_redos(self, rec, force=False):
        """Empty the redo stack the rejected turn left behind.

        FreeCAD has no Python call that clears only the redo stack, but opening
        a transaction does (``Document::_openTransaction``). So a throw-away
        object is added inside a transaction that is then aborted: the abort
        rolls the add back and no undo entry is left.
        """
        d = rec["doc"]
        if not d.RedoCount or not (force or rec.get("segments") or rec.get("phase")):
            return  # a turn that recorded nothing leaves the human's redo stack alone
        n = (d.UndoCount, list(d.UndoNames))
        with _Active(d):
            d.openTransaction("AgentCore: drop rejected redo")
            try:
                d.addObject("App::FeaturePython", "AgentCoreDropRedo")
            finally:
                d.abortTransaction()
        if d.RedoCount or (d.UndoCount, list(d.UndoNames)) != n or d.getObject("AgentCoreDropRedo") is not None:
            raise TurnError(f"could not clear the redo stack of {d.Name}")

    def _settle_outside(self, doc):
        n = doc.UndoCount
        with _Active(doc):
            if App.getActiveTransaction() is None:
                doc.recompute()
        if doc.UndoCount != n:
            raise TurnError("settling recompute was recorded into a transaction")

    # ------------------------------------------------------------ accept
    def accept(self, token=None):
        """Keep the turn as one undo entry per document, all sharing one
        FreeCAD transaction id (so the GUI's undo reverts every document of a
        cross-document turn together). Needs a human-minted token.

        Accept is all or nothing: if flattening fails in any document, every
        document is rolled back to its before-state, the turn ends
        ``rejected`` (``result["accept_error"]`` says why) and the error is
        re-raised.
        """
        if self.status != "proposed":
            raise TurnError(f"cannot accept a turn that is {self.status}")
        drifted = self.live_differences()
        if drifted:  # checked before the token, so a refused accept does not spend it
            raise TurnError("the document no longer equals the reviewed after-state; accept refused: "
                            + "; ".join(drifted[:8]))
        _auth.verify_token(token, self.turn_id, self.digest())
        live = [rec for rec in self._recs if rec["segments"]]
        if len(live) > 1 or any(rec["segments"] != 1 or rec["segment_names"][0] != self.undo_name for rec in live):
            todo = live  # several documents are always re-recorded, so they share one transaction id
        else:
            todo = []
        for rec in todo:  # check every document before touching any
            if not self._stack_holds(rec):
                raise TurnError(f"the undo stack of {rec['name']} no longer holds this turn; cannot flatten it")
        drift, tid, shared = [], None, len(live) <= 1
        if todo:
            try:
                drift, tid, shared = self._flatten(todo)
            except Exception as exc:
                self._abort_accept(exc)
                raise
        self.status = "accepted"
        if self.store is not None:
            for rec in self._recs:
                if rec["snapshot"] is not None:
                    self.store.record(self.turn_id, document=rec["name"], status="accepted",
                                      undo_name=self.undo_name if rec["segments"] else None)
        self._restore_env()
        self.result = {"decision": "accepted", "flattened": self._main() in todo,
                       "flattened_documents": sorted(r["name"] for r in todo),
                       "undo_entries": 1 if self._main()["segments"] else 0,
                       "undo_entries_by_document": {r["name"]: 1 if r["segments"] else 0 for r in self._recs},
                       "transaction_id": tid, "shared_transaction": shared, "detail_drift": drift}
        return self.result

    def live_differences(self):
        """Differences between every covered document now and the after-state
        the human reviewed (F1 dump at F1's tolerance plus the geometry
        signature). Empty means the reviewed change set is what accept keeps."""
        out = []
        for rec in self._recs:
            out += self._rec_differences(rec)
        return out

    def _abort_accept(self, exc):
        methods = {}
        for rec in self._recs:
            try:
                methods[rec["name"]] = self._rewind(rec)
            except Exception as inner:
                methods[rec["name"]] = f"failed: {inner}"
        self._finish_reject(methods, accept_error=f"{type(exc).__name__}: {exc}")

    def _flatten(self, todo):
        """Re-record the turn in ``todo``'s documents as one transaction."""
        caps = {rec["name"]: transplant.capture(rec["doc"]) for rec in todo}
        for rec in todo:
            rec["phase"] = "undoing"
            for _ in range(rec["segments"]):
                rec["doc"].undo()
            rec["phase"] = "undone"
        problems, booked = {}, {}
        # With no active document, setActiveTransaction opens an application
        # wide transaction: every document changed while it is open records
        # into it under the same id (a per-document booking gives each
        # document its own id, and an unbooked document records nothing).
        App.setActiveDocument("")
        tid = App.setActiveTransaction(self.undo_name)
        try:
            # No recompute: every output property is in the capture, and
            # solvers (Assembly, Sketcher) re-run from a restored state may
            # not land on the same solution. Touched flags are set to match.
            # Linked documents first, so links into them resolve.
            for rec in reversed(todo):
                d = rec["doc"]
                with _Active(d):
                    problems[rec["name"]] = transplant.apply(d, caps[rec["name"]], recompute=False)
                    transplant.align_touched(d, rec["after"])
                booked[rec["name"]] = d.getBookedTransactionID()
        finally:
            try:
                App.closeActiveTransaction(False, tid)
            except Exception:
                pass
            for rec in todo:
                d = rec["doc"]
                with _Active(d):
                    if d.HasPendingTransaction:
                        d.commitTransaction()
                if d.UndoNames and d.UndoNames[0] == self.undo_name and d.UndoCount > rec["base_undo"]:
                    rec["phase"] = "committed"  # holds the new entry; a document the replay never reached stays "undone"
        drift = []
        for rec in todo:
            d, name = rec["doc"], rec["name"]
            diffs = compare.differences(rec["after"], _dump(d))
            ch = object_changes(rec["before"], rec["after"])
            allowed = set(ch["features"]["modified"]) | {f["name"] for f in ch["features"]["added"]}
            hard = [x for x in diffs if not _derived(x, rec["after"], allowed)]
            if problems.get(name) or hard:
                raise FlattenError(f"flattened state of {name} differs from the turn's after-state: "
                                   f"{problems.get(name, [])[:5]} {hard[:5]}")
            # Derived, non-property data of objects the turn changed can
            # differ from the turn's own after-state when that state was not a
            # recompute fixed point: TechDraw ``detail`` caches (edge counts
            # and dimension values of views orphaned by a page delete) and
            # ``state`` flags of features left broken by the turn
            # (Touched/Invalid after a Body delete). It is reported in
            # ``accept()["detail_drift"]``.
            if not d.UndoNames or d.UndoNames[0] != self.undo_name:
                raise FlattenError(f"flatten did not leave one '{self.undo_name}' entry in {name}: {d.UndoNames[:3]}")
            rec["segments"] = 1
            rec["segment_names"] = [self.undo_name]
            rec["exit_undo"] = (d.UndoCount, list(d.UndoNames))
            prefix = "" if rec is self._main() else f"[{name}]"
            drift += [prefix + x for x in diffs]
        ids = set(booked.values())
        return drift, tid, len(ids) == 1 and 0 not in ids
