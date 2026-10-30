<!-- SPDX-License-Identifier: LGPL-2.1-or-later -->
# AgentCore.diff (F3)

Transactional agent turns: one FreeCAD transaction per agent turn, a change set for the human, reject, accept with a human-only token, and before-state snapshots for multi-turn revert. Pure Python on FreeCAD's own transaction system; it runs under `FreeCADCmd` with `QT_QPA_PLATFORM=offscreen`.

## Entry points

| name | what it does |
|---|---|
| `AgentTurn(doc, label=..., store=None, checks=None, verify=True, docs=None)` | context manager for one turn; `.change_set()`, `.reject()`, `.accept(token)` |
| `ApplyAuthority.install()` | human side only: creates the process-wide authority; `.mint(turn)` returns a one-shot `ApplyToken` |
| `SnapshotStore(root)` | before-state `.FCStd` copies per turn and document; `.revert(doc, turn_id)` |
| `shape_diff(before, after)` | face-level geometry diff of two shapes |
| `snapshot_dump(doc)`, `differences(a, b)` | canonical F1 dump and its comparison, as used for reject equality |

## Usage

```python
from AgentCore.diff import AgentTurn, ApplyAuthority, SnapshotStore

authority = ApplyAuthority.install()          # GUI side; never passed to the MCP layer
store = SnapshotStore("/path/to/snapshots")

with AgentTurn(doc, label="enlarge hole", store=store) as turn:
    doc.Hole.Radius = 4                        # agent edits, any GUI-style transactions inside
cs = turn.change_set()                         # shown to the human

turn.accept(authority.mint(turn))              # or: turn.reject()
store.revert(doc, turn.turn_id)                # later: back to the state before this turn
```

## How one turn stays one transaction

With the C++ guard (`doc.beginAgentTurn(name)` / `doc.endAgentTurn(commit)` / `doc.isAgentTurnActive()` / `doc.agentTurnStatus()`, `src/App/AgentTurnGuard.*`, used automatically when the build has it) the split never happens: the turn is one transaction on each covered document, every nested route (`doc.openTransaction`, `App.setActiveTransaction`, `AutoTransaction`, GUI `doCommand`) is a savepoint inside it, a nested commit keeps its changes, a nested abort rolls back only the changes since its own open, empty closes are absorbed, and `undo`/`redo`/`clearUndos` are refused and reported as tampering. `endAgentTurn` waits for a recompute of the document on the async worker and folds the savepoint segments into one composite undo entry. The change set then has `transactions.segments == 1` and `accept` needs no flatten for a single-document turn. `AgentTurn(..., use_guard=False)`, or a build without the guard, uses the Python fallback described next.

### Python fallback (no C++ guard)

- On entry each covered document is settled (one `recompute()` outside any transaction), F2 checks run, the F1 dump is taken, shapes are kept for the geometry diff, an optional snapshot is written, and the turn transaction is booked with `FreeCAD.setActiveTransaction`.
- Nested GUI-style code (`doc.openTransaction`/`commitTransaction`, `App.setActiveTransaction`/`closeActiveTransaction`, `doc.abortTransaction`) splits FreeCAD's transaction. These C++ methods cannot be intercepted from Python, so a document observer (`slotCommitTransaction`, `slotAbortTransaction`, `slotCloseTransaction`, `slotBeforeChangeObject`) detects every split and re-books at once; no change goes unrecorded. The turn's changes then sit in K consecutive undo entries (segments). A nested abort rolls back only its own segment.
- `accept` flattens K segments into exactly one undo entry: it captures the after-state with `dumpPropertyContent`, undoes the K segments, opens one transaction, replays the capture with `restorePropertyContent` (no recompute, outputs are replayed too) and checks the F1 dump against the after-state dump.
- `accept` is all or nothing. Every covered document's stack is checked before any is touched; if the replay or its check fails in any document, every document is rolled back to its before-state, the turn ends `rejected` with `result["accept_error"]`, and the error is re-raised.
- `reject` undoes the K segments. When the stack no longer holds the turn (another transaction on top, or `UndoMaxStackSize` 20 trimmed it), it restores the snapshot instead. When undo left only Touched flags behind, it clears them rather than recomputing, so solver outputs come back bit for bit; otherwise (Invalid flags after an undone Body delete) it runs one settling recompute.
- `reject` then empties the redo stack the turn left (a transaction is opened, a throw-away object added, and the transaction aborted, which is the only Python route to FreeCAD's `_clearRedos`). Without this, `doc.redo()` would bring the rejected changes back with no apply token. A turn that recorded nothing leaves the human's redo stack alone.
- Documents joined by XLinks are covered automatically, each with its own booked transaction and dumps during the turn. On accept every document that recorded something is re-recorded inside one application-wide transaction, so each keeps one undo entry and all of them share one transaction id (`result["shared_transaction"]`). FreeCAD's GUI undo (`Gui::Document::checkTransactionID`) reverts entries that share an id together; headless, `doc.undo()` is still per document.
- An exception inside the `with` block rejects the turn and re-raises.

## Change set fields

| key | meaning |
|---|---|
| `schema` | `agentcore.diff/1` |
| `turn`, `label`, `document` | turn id, label, main document name |
| `features` | `{added, removed, modified}` |
| `properties` | list of `{object, property, change, before, after, type}` |
| `expressions` | list of `{object, path, before, after}` |
| `structure` | changes of type, kind, proxy, in/out lists and F1 detail sections |
| `geometry` | per object: face match counts, modified/added/removed faces, max deviation, local-boolean `region` with added and removed volume |
| `linked_documents` | the same layers for XLinked documents that changed |
| `covered_documents` | documents the turn booked |
| `verify` | F2 `run_checks` before and after, plus `regressions` and `fixes` |
| `transactions` | `{segments, nested_opened, nested_aborts, undo_complete}` |
| `snapshot` | the before-state snapshot entry, or null |

The token is bound to the change set's digest (everything except `verify`), so a change set altered after the human saw it cannot be accepted.

## Equality rules

- Reject equality compares F1 dumps (`AgentCore.state.dump`): every object, property value, expression, link list, state flag and shape summary. `result["equal"]` uses F1's tolerance (`math.isclose`, rel and abs 1e-9).
- `result["exact"]` is the strict check: the dump compared with no tolerance, and every persistent non-shape property serialised by FreeCAD (`compare.raw_state`, zip members of `dumpPropertyContent` without the zip header's timestamp) byte-equal to the pre-turn bytes. Shape properties are judged by F1's shape summary only, because their serialisation embeds element-map hasher indices that differ between two recomputes of the same model. The reject corpus test asserts `exact` on every F1 fixture whose reject needed no settling recompute (8 of 9, including the Assembly example), and a zero-tolerance F1 dump match on all 9. The ninth, `upstream_partdesign_example`, deletes a Body: undoing that leaves Invalid flags that only a recompute clears, and the sketch solver then re-solves `Sketch006` with last-digit noise (about 2e-16 in its `Geometry` bytes) on some runs.
- Left out: `generator`; the transient caches `_ElementMapVersion` and `_Part_ShapeCache`, which FreeCAD fills lazily and never saves; the `volume` of a shape with no solids, which OCCT reports as noise.

## Known limits

- The C++ guard prevents the split on one document. A transaction group that spans several XLinked documents is still re-recorded on `accept` so the documents share one transaction id; that flatten is unchanged.
- Token secrecy is process-level, and the boundary is the approver process, not this module. In a process that holds an `ApplyAuthority` the key is readable through closures or `gc` (a test forges a token that way), so agent code never runs in one: the F4 session worker is sealed with `authority.prepare_agent_process()`, which refuses `install()` and forwards every token check to the approver (`approver.py`), whose key the worker never receives. See `SECURITY.md` for the residual in-process limits.
- The flatten tolerates drift in derived, non-property data only, and only on objects the turn added or modified: their `state` flags (features a turn left broken) and, on TechDraw objects, F1 `detail` caches (views orphaned by a page delete). Drift on any object the turn did not change is a hard failure. It is reported in `accept()["detail_drift"]`.
- Snapshot revert loses Sketcher `dependent_parameters` solver history for an over-constrained sketch, exactly as a plain save and reopen does. Snapshots store the turn's before-dump beside the `.FCStd`; a restore recomputes only when the replayed state falls short of it, so Assembly placements are not re-solved (a re-solve from a restored state could land on another solution).
- Headless, a cross-document accepted turn needs one `doc.undo()` per document; the shared transaction id is what lets the GUI undo them in one step.
- The remote (F4 worker) review has no 3D overlay: the shapes stay in the worker, and shipping them to the GUI is not built. C++ transaction introspection is not built yet.

## Change-set panel and overlay (`AgentCore/gui/`, GUI only)

`AgentCore.gui` is imported only from `InitGui.py` (never from `AgentCore/__init__.py`), so `FreeCADCmd` and the headless suites never load `FreeCADGui`, PySide or pivy.

```python
from AgentCore import gui
gui.review(turn)                                   # a proposed AgentTurn in this process
gui.review_remote(turn_id, fetch_change_set)       # a turn in an F4 worker; fetch_change_set() returns its current change set
```

| file | role |
|---|---|
| `panel.py` | PySide task dialog (`Gui.Control.showDialog`): a tree of the change set in groups (features added, removed, modified, properties before to after, expressions, geometry per object, verification regressions and fixes, transactions) and Accept and Reject buttons. Selecting a row selects that object in the document. The standard Close button decides nothing: the turn stays `proposed` and the overlay goes |
| `overlay.py` | pivy overlay under the active view's scene graph: added material translucent green, removed material translucent red, flat shaded with a polygon offset and opaque edges, so `saveImage` renders it. Removed on accept, reject and close |
| `backends.py` | `LocalTurnBackend` (in-process turn) and `RemoteTurnBackend` (F4 worker) behind one interface |
| `session.py` | the GUI process's one `Approver`, created on the first press and never handed to the panel or the backends |
| `model.py` | rows of the panel from a change set (no Qt) |

- **Overlay pieces** come from `AgentTurn.overlay_pieces()`: the local-boolean added and removed material of `geometry.shape_diff(..., keep=...)` for a modified object, the whole after shape for an added one, the whole before shape for a removed one. An object another changed object consumes (a Pad in its Body, the Base of a Cut) is not drawn, so a result appears once. No whole-solid boolean is run for the overlay.
- **Local accept**: `Approver.approve(turn_id, shown_digest)` returns the token fields, `turn.accept` verifies them. The token is bound to the digest the human was shown, not to the turn's current one: a change set altered after the panel opened is refused (`StaleChangeSet` before any token exists, `TokenRefused` if one did), and a live document that moved is refused by `accept` before the token is spent. A refusal shows in the panel, which stays open and reviewable, and leaves nothing approved at the approver.
- **Remote accept**: Accept refetches the worker's change set, refuses if its digest differs, then calls `Approver.approve(turn_id, digest)`, which the worker polls. Reject calls `Approver.deny`. The panel never touches the worker's document.
- **Who can mint**: tokens exist only inside `Approver.approve`, called from a backend's `accept`, called from the Accept button's slot. Neither the panel nor a backend holds an `ApplyAuthority` or a token. As `SECURITY.md` says, inside the GUI process this is convention, not a boundary: agent code must run in a sealed worker, never in the GUI process.

Tests: `AgentCoreTests/test_diff_*.py`, run with `harness/agentcore_test.sh <worktree>`; the GUI suite is `FreeCAD -t TestAgentCoreGui` (`AgentCoreGuiTests/`, registered from `InitGui.py`, mini only), and its screenshot baselines are the `f3_*` scenes of `fixtures/gui/scenes.json` (`harness/gui/run.sh snap`).
