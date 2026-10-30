---
tags: [type/reference, for/agent, status/live]
---

# Apply-token trust boundary (F3, F4)

An agent turn reaches the user's model only through `AgentTurn.accept(token)`, and only a human may cause a valid token to exist. This note says which process holds what, what is enforced, and what is not.

## Process roles

| Process | Runs agent code | Holds the apply key | Approve path |
|---|---|---|---|
| Approver (`approver.py` headless, or the GUI change-set panel, `AgentCore/gui/`) | no | yes, the only `ApplyAuthority` | `Approver.approve(turn_id, digest)`, called from the human's own channel (stdin or the GUI); never on the socket |
| Session worker (`mcp/session_worker.py`, FreeCADCmd) | yes, `exec_code` | no | none; it polls the approver for a token the human released and the approver verifies it again on accept |
| MCP server (`mcp/server.py`, plain Python) | no | no | none; it never imports `AgentCore.diff`, refuses every approve-like tool, and passes the approver socket path to workers |

## What is enforced

- The worker is sealed before any agent code runs (`authority.prepare_agent_process`). After that, `ApplyAuthority.install()` raises, and the only verifier is a forwarding closure that asks the approver over its socket. With no approver configured, every token is refused.
- The HMAC key never enters the worker, so no introspection inside the worker can mint a token the approver honours. `test_diff_approver` runs the forgery probes in a sealed FreeCADCmd: `getattr(turn, "acc" + "ept")`, a token built with `object.__new__`, and a key search over every closure cell and module global (the same search forges a token in a process that does hold the key, which is the positive control).
- The approver socket answers `check`, `poll` and `status` only. A token is released by `poll` only for the exact (turn id, change-set digest) a human approved, is spent on its first successful `check`, and cannot be replayed.
- `accept` refuses when the live document no longer equals the reviewed after-state (F1 dump plus geometry signature), before the token is checked, so a refused accept does not spend the token.
- undo, redo and clearUndos inside a turn abort the turn; an undo that reaches into the human's pre-turn history is redone at once.

## Residual in-process limits

Python in one process is not a sandbox, so the worker is not a sandbox either. Agent code in the worker can replace `authority._VERIFIER`, monkeypatch `AgentTurn.accept`, or flip `turn.status`, and make the worker believe a turn was accepted. That fools only the worker's own copy of the document: the approver's record (`status` = `pending`, not `spent`) still says no human approved it, and the session reports a turn decided outside its approval flow, undoing a self-accepted entry when it can. Anything that lands a change in the user's real model (the GUI, a save of the source file) must trust the approver's record, never the worker's `turn.status`.

The worker can also write files directly (`open`, `doc.saveAs`, FreeCAD's C++ exporters) without going through `import_export`. The policy scan and the runtime audit hook flag such access, but they are advisory: a determined agent can hide it (string building, `exec` of runtime strings, C extensions). The real confinement is that the worker holds a copy of the document and the user's model changes only through the approver.

In the GUI, if `exec_code` runs inside the GUI process (`Bridge.start_in_gui`), the agent shares an interpreter with the GUI's authority and can read the key. The GUI must therefore run agent code in a sealed worker and keep only the approver in-process; until that wiring exists, the GUI bridge is not a trust boundary. The change-set panel (`AgentCore/gui/`) keeps its `Approver` in `gui/session.py`'s closure and mints only from the Accept button's slot, bound to the digest the human was shown; that stops a stale or altered change set from being accepted, but it does not make the GUI process a boundary against agent code running inside it.

clearUndos inside a turn cannot be prevented from Python (no observer slot fires, and the C++ method cannot be wrapped). It is detected afterwards, the turn is aborted and the document is restored to its pre-turn state from the before capture, but the human's pre-turn undo entries are gone.

## What is tested on the real F3 path (run 002)

- `test_mcp_real_f3`: the F4 server drives a real `AgentTurn` in a sealed FreeCADCmd worker for the whole loop (propose, approval typed into the separate approver process, the worker's accept, verify). The scripted session replayed twice gives an identical document, change set, verify results and decision. A verify pool worker killed mid-verify errors that call only: the open turn keeps its edit and the same turn still goes through approval and accept. A session worker killed mid-exec returns `session_lost`, respawns from the pre-change checkpoint, and a fresh change then gets approved and accepted. The stub-layer versions in `test_mcp_server` stay for the stub's stored golden and the server's worker plumbing.
- `test_diff_xlink.test_accept_gives_every_document_the_same_transaction_id` observes FreeCAD's own transaction id per document (`getBookedTransactionID`) and the application-level `App.getActiveTransaction()` during the flatten, independently of F3's `shared_transaction` flag.
- `test_diff_xlink.TestXLinkGuiUndo` opens the FreeCAD GUI offscreen, accepts a two-document turn and runs one `Std_Undo`: both documents revert. It runs only with `AGENTCORE_GUI_TESTS=1` (mini only, charter rule 41 forbids GUI windows on the laptop) and is skipped otherwise.
