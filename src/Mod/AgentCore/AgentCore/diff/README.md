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

- On entry each covered document is settled (one `recompute()` outside any transaction), F2 checks run, the F1 dump is taken, shapes are kept for the geometry diff, an optional snapshot is written, and the turn transaction is booked with `FreeCAD.setActiveTransaction`.
- Nested GUI-style code (`doc.openTransaction`/`commitTransaction`, `App.setActiveTransaction`/`closeActiveTransaction`, `doc.abortTransaction`) splits FreeCAD's transaction. These C++ methods cannot be intercepted from Python, so a document observer (`slotCommitTransaction`, `slotAbortTransaction`, `slotCloseTransaction`, `slotBeforeChangeObject`) detects every split and re-books at once; no change goes unrecorded. The turn's changes then sit in K consecutive undo entries (segments). A nested abort rolls back only its own segment.
- `accept` flattens K segments into exactly one undo entry: it captures the after-state with `dumpPropertyContent`, undoes the K segments, opens one transaction, replays the capture with `restorePropertyContent` (no recompute, outputs are replayed too) and checks the F1 dump against the after-state dump.
- `reject` undoes the K segments. When the stack no longer holds the turn (another transaction on top, or `UndoMaxStackSize` 20 trimmed it), it restores the snapshot instead.
- Documents joined by XLinks are covered automatically, each with its own booked transaction, dumps, and reject or flatten, because FreeCAD records changes only in a document that has a transaction booked.
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

- Reject equality compares F1 dumps (`AgentCore.state.dump`): every object, property value, expression, link list, state flag and shape summary.
- Left out: `generator`; the transient caches `_ElementMapVersion` and `_Part_ShapeCache`, which FreeCAD fills lazily and never saves; the `volume` of a shape with no solids, which OCCT reports as noise.

## Known limits

- There is no C++ transaction guard yet (class D, out of this wave), so nested splits are detected and flattened after the fact rather than prevented.
- Token secrecy is process-level: code in the same interpreter can reach the secret through `gc`. The guarantee is that the MCP layer never receives a token or the authority.
- The flatten tolerates drift in derived, non-property data only: F1 `detail` caches of TechDraw views orphaned by a page delete and object `state` flags of features a turn left broken. It is reported in `accept()["detail_drift"]`.
- Snapshot revert loses Sketcher `dependent_parameters` solver history for an over-constrained sketch, exactly as a plain save and reopen does.
- `reject` leaves the turn's segments on the redo stack.
- The GUI task panel, pivy overlay (needs I4) and C++ transaction introspection are not built yet.

Tests: `AgentCoreTests/test_diff_*.py`, run with `harness/agentcore_test.sh <worktree>`.
