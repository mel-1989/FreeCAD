# AgentCore interface contract (v0)

Sub-packages are built in parallel; these are the entry points each one promises the others. Change a signature only together with its callers.

| Package | Entry point | Returns |
|---|---|---|
| `AgentCore.state` | `SCHEMA_VERSION: str` | e.g. `"agentcore.state/1"` |
| `AgentCore.state` | `dump(doc, page=None, page_size=None) -> dict` | versioned document state; paged reads concatenate to the unpaged read |
| `AgentCore.state` | `entity_id(obj, subname="") -> str` | stable ID such as `"Pad001:Face3"`, built on the element map where one exists |
| `AgentCore.state` | `describe_entity(obj, subname) -> dict` | type, key dimensions, axis, creator feature, human-readable `label` |
| `AgentCore.state` | `track_references(doc) -> ReferenceTracker` | records remapped and lost references across a recompute |
| `AgentCore.verify` | `run_checks(doc, checks=None, **opts) -> dict` | JSON result per check: `{"check", "ok", "entities": [entity ids], "details"}` |
| `AgentCore.verify` | `Pool(size, timeout).run(fcstd_path, checks) -> dict` | same result shape, or `{"status": "crash" or "timeout", ...}` |
| `AgentCore.diff` | `AgentTurn(doc)` context manager; `.change_set()`, `.reject()`, `.accept(token)` | change set dict; reject restores the pre-turn dump |
| `AgentCore.refactor` | `invariant(doc, fn) -> dict` | geometry hash before and after, pass or fail |

Tests live in `AgentCoreTests/test_<package>_*.py` and run with `FreeCADCmd -t TestAgentCore`.
