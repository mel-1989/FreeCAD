# SPDX-License-Identifier: LGPL-2.1-or-later
"""The short cookbook served as the ``cadfork://cookbook`` resource.

First draft by deepseek/deepseek-v4-flash (via the OpenRouter relay), corrected
and rewritten against the real tool schemas.
"""

COOKBOOK = r"""# cadfork-mcp cookbook

Every tool returns JSON in `structuredContent` (and the same JSON as text). Errors come back as a tool result with `isError: true` and an `error` code; they are never exceptions. Most tools take `doc`, the handle `open_document` returned; it defaults to the most recently opened document.

## The turn loop

Read, open a change, edit, verify, propose. A human accepts or rejects the proposal in the FreeCAD GUI; the agent cannot approve it.

```json
{"tool": "open_document", "arguments": {"path": "/models/bracket.FCStd"}}
{"tool": "get_state", "arguments": {"page": 0, "page_size": 50}}
{"tool": "begin_change", "arguments": {}}
{"tool": "exec_code", "arguments": {"code": "doc.getObject('Box').Length = 40"}}
{"tool": "verify", "arguments": {"checks": ["recompute", "validity", "interference", "mass"]}}
{"tool": "propose_change", "arguments": {"summary": "Lengthen Box to 40 mm"}}
```

## exec_code: add, cut, measure

`doc` (the document) and `App` (the FreeCAD module) are predefined. Assign `result` to return a JSON value. The document is recomputed after the code unless `recompute` is false.

```python
box = doc.addObject("Part::Box", "Box")
box.Length, box.Width, box.Height = 20, 10, 5
cyl = doc.addObject("Part::Cylinder", "Hole")
cyl.Radius, cyl.Height = 2, 5
cyl.Placement.Base = App.Vector(10, 5, 0)
cut = doc.addObject("Part::Cut", "Plate")
cut.Base, cut.Tool = box, cyl
doc.recompute()
result = {"volume": cut.Shape.Volume, "valid": cut.Shape.isValid()}
```

Each call gets a fresh namespace. The result also lists `invalid_objects` after the recompute; check it before proposing.

## Parametric edits

Change properties rather than rebuilding geometry, so references and the change set stay small.

```python
doc.getObject("Box").Length = 40
spreadsheet = doc.getObject("Spreadsheet")
if spreadsheet:
    spreadsheet.set("A1", "12")
```

## Large documents: paging

`get_state` pages `objects` in document order; every other key is the same on every page. Read `paging.pages` from the first page and iterate.

```json
{"tool": "get_state", "arguments": {"page": 0, "page_size": 50}}
{"tool": "get_state", "arguments": {"page": 1, "page_size": 50}}
```

Use `describe_entity` with an ID from the state, such as `Pad001:Face3`, for the type, key dimensions, axis and creating feature of one face, edge or vertex.

## Import and export

Export needs no change, and while a change is open or awaiting approval it is refused, because the document then holds edits no human approved: pass `pre_turn: true` to export the state before the change. An existing file is replaced only with `overwrite: true`, and the document's own source file never. Import edits the document, so it needs a change.

```json
{"tool": "import_export", "arguments": {"action": "export", "path": "/tmp/bracket.step"}}
{"tool": "import_export", "arguments": {"action": "export", "path": "/tmp/bracket.brep", "objects": ["Plate"]}}
{"tool": "import_export", "arguments": {"action": "import", "path": "/parts/m6_washer.step"}}
```

Find candidate parts first with `search_parts` (`{"query": "washer m6"}`).

## Errors and what to do

| error | meaning | next step |
|---|---|---|
| `session_lost` | the FreeCADCmd worker for the document died (crash or kill) | call again: the worker respawns from the pre-change checkpoint, and the open change is gone, so begin a new one |
| `timeout` | exec_code ran past its timeout and was interrupted; if it could not be interrupted the worker was killed (`session_lost`) | simplify the code or raise `timeout` |
| `diff_unavailable` | AgentCore.diff (F3) is not installed, so changes cannot be opened | read-only tools and verify still work |
| `policy_refused` | the code tried to approve a change, control the transaction, or (strict mode) touch files, processes or the network | edit inside the change only |
| `change_pending` | a change is already open or proposed | pass `discard_pending: true` to begin_change to reject it |
| `change_aborted` | exec_code undid, redid or cleared history inside the change, or closed or decided the turn itself; the change was rejected | begin a new change and edit without touching undo or the turn |
| `file_exists` / `refused_target` | export would replace an existing file, or the document's own source file | pick a new path, or pass `overwrite: true` for a file you mean to replace |
| `crash` or `timeout` in verify | one verify worker died; the other checks still ran | read `results`; the server keeps serving |

## Policy

File, subprocess, network and dynamic-code access are flagged in the exec_code result's `policy` field (a static scan plus Python audit events). This is confinement, not a sandbox: exec_code is arbitrary Python in a worker process that holds a copy of the document, and nothing reaches the user's model until a human accepts the proposal.

`render` is unavailable until the I4 GUI test harness exists; `get_selection` is empty in headless sessions.
"""
