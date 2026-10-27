# cadfork-mcp

A thin MCP server over the cad-fork AgentCore layer: F1 (agent-readable state), F2 (headless verify) and F3 (agent changes with human approval). It speaks MCP over stdio (JSON-RPC 2.0, initialize handshake at revisions 2024-11-05 to 2025-11-25) and drives headless `FreeCADCmd` worker processes. It never starts the FreeCAD GUI and does not import FreeCAD itself.

## Run

```bash
uvx --from <freecad>/src/Mod/AgentCore/mcp-server cadfork-mcp --freecadcmd <path to FreeCADCmd> --parts-path <parts dir>
```

Without `uvx`, from a source tree: `PYTHONPATH=<freecad>/src/Mod/AgentCore python3 -m AgentCore.mcp --freecadcmd ...`. Options: `--pool-size` (parallel verify workers, default 2), `--verify-timeout` (seconds per check, default 120), `--exec-policy flag|strict`, `--list-tools`, `--version`. `CADFORK_FREECADCMD` and `CADFORK_PARTS_PATH` (os.pathsep list) work in place of the flags. Client configurations for Claude Code, Cursor and Codex are in `examples/`.

## Tools

| tool | what it does |
|---|---|
| `list_documents` | open documents, one FreeCADCmd worker each, with their change state |
| `open_document` | open .FCStd, .step/.stp or .brep, or create an empty document |
| `get_state` | F1 state dump (`agentcore.state/1`), paged by objects |
| `describe_entity` | type, dimensions, axis and creating feature of an entity such as `Pad001:Face3` |
| `get_selection` | the GUI selection; empty with a note when headless |
| `exec_code` | Python inside the open change, with a timeout and policy flags |
| `verify` | F2 checks on a snapshot, each in an isolated FreeCADCmd worker |
| `begin_change` | open an F3 change (one transaction) |
| `propose_change` | return the F3 change set for human approval |
| `render` | unavailable until I4 |
| `import_export` | STEP, BREP and FCStd export; STEP and BREP import into the change |
| `search_parts` | search the local parts corpus |

There is no approve tool. Only the FreeCAD GUI holds F3's apply token, so a client (or this server) cannot accept its own proposal; calls named `approve_change`, `accept_change` and the like return `approval_refused`.

Resources: `cadfork://cookbook`, `cadfork://state/schema`, `cadfork://api/index` and one `cadfork://api/<module>/<Class>` page per class of the FreeCAD Python API, generated from the `.pyi` binding stubs by `python -m AgentCore.mcp.apidocs` (output committed as `AgentCore/mcp/api_docs.json`).

## Process model

Each open document lives in its own long-lived `FreeCADCmd` session worker. The server talks to it over a private Unix socket (mode 0600) with a per-worker random token passed in the environment; the worker's socket threads queue every request for its main thread, which owns FreeCAD. `verify` saves a snapshot and runs F2's `verify.Pool`, one process per check. A worker that crashes, is killed or overruns a hard timeout costs one error result (`session_lost`, `timeout`, `verify_crash`); the next call respawns the session from the pre-change checkpoint.

## exec_code policy: confinement, not a sandbox

`exec_code` is arbitrary Python in the worker process. It only runs inside an open change, so edits land in one F3 transaction on the worker's copy of the document, and nothing reaches the user's model until a human accepts. Code that calls `accept`/`approve`, touches an apply token or controls the transaction directly is refused. File, subprocess, network, native and dynamic-code access is flagged in the result's `policy` field from a static scan and from Python audit events; `--exec-policy strict` refuses flagged code instead. Code built at runtime, C extensions and FreeCAD's own C++ file I/O can evade both scans; treat the flags as advice to the reviewer.

## Credits

The tool set follows the patterns of neka-nat/freecad-mcp and spkane/freecad-addon-robust-mcp-server (both MIT). No code is copied from either.
