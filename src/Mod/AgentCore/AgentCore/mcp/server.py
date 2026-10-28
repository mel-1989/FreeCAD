# SPDX-License-Identifier: LGPL-2.1-or-later
"""cadfork-mcp: a thin MCP stdio server over AgentCore F1 (state), F2 (verify), F3 (diff).

Pure stdlib; it never imports FreeCAD. JSON-RPC 2.0, one message per line on
stdin/stdout (MCP stdio transport), initialize handshake at protocol revisions
2024-11-05 through 2025-11-25. ``server/discover`` (the 2026 stateless era) is
answered with METHOD_NOT_FOUND, so modern clients fall back to the handshake.

Every open document lives in its own long-lived FreeCADCmd session worker
(``workers.SessionWorker``), reached through a token-authenticated bridge;
``verify`` saves a snapshot and runs F2's ``verify.Pool`` (one isolated
FreeCADCmd per check). A worker that dies costs one error result, never the
server.

Tool surface (schema ``agentcore.mcp/1``): list_documents, open_document,
get_state, describe_entity, get_selection, exec_code, verify, begin_change,
propose_change, render, import_export, search_parts. There is no approve tool:
only the FreeCAD GUI holds F3's apply token.

The tool set follows the patterns of neka-nat/freecad-mcp and
spkane/freecad-addon-robust-mcp-server (both MIT); no code is copied from them.
"""

import json
import os
import shutil
import sys
import threading
import traceback

from . import apidocs, cookbook, policy
from .bridge import BridgeError
from .search import PartsIndex
from .workers import AGENTCORE_PARENT, SessionWorker, WorkerStartError, find_freecadcmd, load_pool, short_tempdir

SERVER_NAME = "cadfork-mcp"
SERVER_VERSION = "0.1.0"
TOOL_SCHEMA = "agentcore.mcp/1"
PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
LATEST_PROTOCOL = PROTOCOL_VERSIONS[-1]

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = -32700, -32600, -32601, -32602, -32603
RESOURCE_NOT_FOUND = -32002

APPROVAL_TOOLS = {"approve_change", "accept_change", "apply_change", "approve_proposal", "accept_proposal",
                  "approve", "accept", "apply"}
APPROVAL_REFUSAL = ("approval is human-only: only the FreeCAD GUI holds F3's apply token, so no MCP client "
                    "(including this server) can accept a proposal. Ask the user to review it in the GUI.")

INSTRUCTIONS = (
    "cadfork-mcp drives headless FreeCAD (FreeCADCmd) workers. Loop: open_document, get_state, begin_change, "
    "exec_code, verify, propose_change. A human accepts proposals in the FreeCAD GUI; you cannot. "
    "Read cadfork://cookbook first; the FreeCAD API reference is under cadfork://api/index."
)

_DOC = {"type": "string", "description": "Document handle from open_document; defaults to the most recent one."}


def _tool(name, title, description, props=None, required=(), read_only=False, destructive=False, open_world=False):
    return {
        "name": name, "title": title, "description": description,
        "inputSchema": {"type": "object", "properties": props or {}, "required": list(required),
                        "additionalProperties": False},
        "annotations": {"title": title, "readOnlyHint": read_only, "destructiveHint": destructive,
                        "idempotentHint": read_only, "openWorldHint": open_world},
    }


TOOLS = [
    _tool("list_documents", "List documents", "Open documents (one FreeCADCmd worker each) with their change state.",
          read_only=True),
    _tool("open_document", "Open document",
          "Open a .FCStd, .step/.stp or .brep file, or create an empty document with new_name. Returns a handle.",
          {"path": {"type": "string"}, "new_name": {"type": "string"}}),
    _tool("get_state", "Get state", "F1 versioned JSON state of the document (agentcore.state/1), paged by objects.",
          {"doc": _DOC, "page": {"type": "integer", "minimum": 0}, "page_size": {"type": "integer", "minimum": 1}},
          read_only=True),
    _tool("describe_entity", "Describe entity",
          "Type, key dimensions, axis and creating feature of an object or element, by stable ID such as 'Pad001:Face3'.",
          {"doc": _DOC, "entity": {"type": "string"}}, required=("entity",), read_only=True),
    _tool("get_selection", "Get selection",
          "The user's GUI selection as entity IDs. Headless sessions have no GUI, so this is empty with a note.",
          {"doc": _DOC}, read_only=True),
    _tool("exec_code", "Execute Python",
          "Run Python inside the open change (begin_change first) with `doc` and `App` predefined; assign `result` to "
          "return JSON. Timeout-limited; file, subprocess and network access are flagged (confinement, not a sandbox).",
          {"doc": _DOC, "code": {"type": "string"}, "timeout": {"type": "number", "minimum": 0.1, "maximum": 600},
           "recompute": {"type": "boolean"}}, required=("code",), destructive=True),
    _tool("verify", "Verify",
          "F2 headless checks on a snapshot of the document, each in an isolated FreeCADCmd worker. Checks: recompute, "
          "validity, interference, mass, thickness ({check, min_required}) and others; default set when omitted.",
          {"doc": _DOC, "checks": {"type": "array", "items": {"anyOf": [{"type": "string"}, {"type": "object"}]}},
           "timeout": {"type": "number", "minimum": 1, "maximum": 1800}}, read_only=True),
    _tool("begin_change", "Begin change",
          "Open an F3 change (one transaction) for exec_code and import edits. discard_pending rejects an open or "
          "proposed change first.", {"doc": _DOC, "discard_pending": {"type": "boolean"}}),
    _tool("propose_change", "Propose change",
          "Freeze the open change and return its F3 change set for human approval in the GUI. Cannot approve.",
          {"doc": _DOC, "summary": {"type": "string"}}),
    _tool("render", "Render", "Image of the model. Unavailable until I4 (GUI rendering in a VM or CI).",
          {"doc": _DOC, "view": {"type": "string"}}, read_only=True),
    _tool("import_export", "Import or export",
          "Export the document or objects to .step/.stp, .brep or .FCStd, or import .step/.stp/.brep into the open change. "
          "Export is refused while a change is open or awaiting approval (pre_turn=true exports the state before it) "
          "and never replaces an existing file unless overwrite=true.",
          {"doc": _DOC, "action": {"type": "string", "enum": ["import", "export"]}, "path": {"type": "string"},
           "objects": {"type": "array", "items": {"type": "string"}},
           "overwrite": {"type": "boolean", "description": "export: replace an existing file (default false)"},
           "pre_turn": {"type": "boolean", "description": "export: during a change, export the state before it"}},
          required=("action", "path")),
    _tool("search_parts", "Search parts",
          "Search the local parts corpus (FCStd, STEP, BREP under the configured roots) by words in names, labels, types.",
          {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100},
           "formats": {"type": "array", "items": {"type": "string"}}}, required=("query",), read_only=True),
]
TOOL_NAMES = [t["name"] for t in TOOLS]


class ToolError(Exception):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.payload = {"error": code, "message": message, **details}


class DocHandle:
    def __init__(self, hid, source, new_name):
        self.id = hid
        self.source = source
        self.new_name = new_name
        self.worker = None
        self.checkpoint = None  # last committed state (pre-change while a change is open, post-accept after): the respawn source
        self.checkpoint_path = None  # where the worker refreshes it on an accept
        self.lost = None        # set when the worker died; reported once on respawn


class Server:
    def __init__(self, freecadcmd=None, parts_path=None, pool_size=2, verify_timeout=120.0, exec_policy="flag",
                 worker_env=None, worker_start_timeout=90.0, approver_socket=None):
        if exec_policy not in policy.MODES:
            raise ValueError(f"exec policy must be one of {policy.MODES}")
        self.freecadcmd = find_freecadcmd(freecadcmd)
        self.parts = PartsIndex(parts_path or [p for p in os.environ.get("CADFORK_PARTS_PATH", "").split(os.pathsep) if p])
        self.pool_size = pool_size
        self.verify_timeout = verify_timeout
        self.exec_policy = exec_policy
        self.worker_env = dict(worker_env or {})
        if approver_socket:  # only a path: the server holds no key and no approve path
            self.worker_env["CADFORK_MCP_APPROVER_SOCKET"] = os.path.abspath(approver_socket)
        self.worker_start_timeout = worker_start_timeout
        self.docs = {}
        self.order = []
        self._n = 0
        self._pool = None
        self._tmp = short_tempdir("acmcp-srv-")
        self._api = None
        self.protocol_version = None
        self.initialized = False
        self._lock = threading.RLock()

    # -- lifecycle
    @property
    def pool(self):
        if self._pool is None:
            self._pool = load_pool().Pool(size=self.pool_size, timeout=self.verify_timeout, freecadcmd=self.freecadcmd)
        return self._pool

    def close(self):
        for h in list(self.docs.values()):
            if h.worker is not None:
                h.worker.shutdown()
        self.docs.clear()
        if self._pool is not None:
            self._pool.close()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def worker_pids(self):
        return {hid: h.worker.pid for hid, h in self.docs.items() if h.worker is not None and h.worker.alive()}

    # -- JSON-RPC
    def handle(self, msg):
        """Handle one decoded JSON-RPC message; return the response dict or None for notifications."""
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return _err(msg.get("id") if isinstance(msg, dict) else None, INVALID_REQUEST, "not a JSON-RPC 2.0 message")
        method = msg.get("method")
        rid = msg.get("id")
        if method is None:
            return None  # a response to a server request; this server sends none
        if "id" not in msg:
            return None  # notification (initialized, cancelled, ...)
        params = msg.get("params") or {}
        try:
            if method == "initialize":
                return _ok(rid, self._initialize(params))
            if method == "ping":
                return _ok(rid, {})
            if method == "tools/list":
                return _ok(rid, {"tools": TOOLS})
            if method == "tools/call":
                return _ok(rid, self._call_tool(params.get("name"), params.get("arguments") or {}))
            if method == "resources/list":
                return _ok(rid, {"resources": self._resource_list()})
            if method == "resources/templates/list":
                return _ok(rid, {"resourceTemplates": [{
                    "uriTemplate": apidocs.URI_PREFIX + "{module}/{class}", "name": "freecad-api-class",
                    "title": "FreeCAD API class", "mimeType": "text/markdown",
                    "description": "One class of the FreeCAD Python API, e.g. cadfork://api/App/DocumentObject"}]})
            if method == "resources/read":
                return self._resource_read(rid, params.get("uri"))
            if method == "prompts/list":
                return _ok(rid, {"prompts": []})
            if method == "logging/setLevel":
                return _ok(rid, {})
            return _err(rid, METHOD_NOT_FOUND, f"Method not found: {method}")
        except Exception as e:
            return _err(rid, INTERNAL_ERROR, f"{type(e).__name__}: {e}", traceback.format_exc(limit=6))

    def _initialize(self, params):
        asked = params.get("protocolVersion")
        self.protocol_version = asked if asked in PROTOCOL_VERSIONS else LATEST_PROTOCOL
        self.initialized = True
        return {
            "protocolVersion": self.protocol_version,
            "capabilities": {"tools": {"listChanged": False}, "resources": {"subscribe": False, "listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "title": "cadfork FreeCAD agent server", "version": SERVER_VERSION},
            "instructions": INSTRUCTIONS,
        }

    def serve_stdio(self, inp=None, out=None):
        inp = inp or sys.stdin.buffer
        out = out or sys.stdout.buffer
        for line in inp:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                resp = _err(None, PARSE_ERROR, "parse error")
            else:
                if isinstance(msg, list):
                    resps = [r for r in (self.handle(m) for m in msg) if r is not None]
                    resp = resps or None
                else:
                    resp = self.handle(msg)
            if resp is not None:
                out.write((json.dumps(resp, default=str) + "\n").encode("utf-8"))
                out.flush()

    # -- tools
    def _call_tool(self, name, args):
        if name in APPROVAL_TOOLS:
            return _result({"error": "approval_refused", "message": APPROVAL_REFUSAL}, True)
        fn = getattr(self, "t_" + str(name), None) if name in TOOL_NAMES else None
        if fn is None:
            return _result({"error": "unknown_tool", "message": f"unknown tool {name!r}", "tools": TOOL_NAMES}, True)
        if not isinstance(args, dict):
            return _result({"error": "bad_argument", "message": "arguments must be an object"}, True)
        allowed = set(next(t for t in TOOLS if t["name"] == name)["inputSchema"]["properties"])
        extra = sorted(set(args) - allowed)
        if extra:
            msg = f"unknown argument(s) {extra} for {name}"
            if any(k in ("approve", "accept", "apply", "token", "apply_token") for k in extra):
                return _result({"error": "approval_refused", "message": APPROVAL_REFUSAL}, True)
            return _result({"error": "bad_argument", "message": msg}, True)
        with self._lock:
            try:
                out = fn(**args)
                is_error = bool(out.pop("_is_error", False))
                return _result(out, is_error)
            except ToolError as e:
                return _result(e.payload, True)
            except TypeError as e:
                return _result({"error": "bad_argument", "message": str(e)}, True)

    def _handle(self, doc):
        if not self.docs:
            raise ToolError("no_document", "no document is open: call open_document first")
        hid = doc or self.order[-1]
        h = self.docs.get(hid)
        if h is None:
            raise ToolError("not_found", f"no document handle {hid!r}", documents=sorted(self.docs))
        return h

    def _spawn(self, h, source):
        env = dict(self.worker_env or {})
        if h.checkpoint_path is None:
            h.checkpoint_path = os.path.join(self._tmp, f"{h.id}-checkpoint.FCStd")
        env["CADFORK_MCP_CHECKPOINT"] = h.checkpoint_path  # the worker rewrites it atomically on every accept
        w = SessionWorker(self.freecadcmd, extra_env=env, policy_mode=self.exec_policy,
                          start_timeout=self.worker_start_timeout)
        try:
            if source:
                w.call("open", {"path": source}, timeout=300)
            else:
                w.call("open", {"name": h.new_name}, timeout=60)
        except Exception:
            w.shutdown()
            raise
        h.worker = w

    def _worker(self, h):
        """The handle's live worker, respawned from the checkpoint or source if it died."""
        if h.worker is not None and h.worker.alive():
            return h.worker
        if h.worker is not None:
            h.worker.kill()
        src = h.checkpoint or h.source
        try:
            self._spawn(h, src)
        except (WorkerStartError, BridgeError, OSError, ConnectionError, TimeoutError) as e:
            raise ToolError("session_lost", f"could not respawn the session worker: {e}")
        # The checkpoint stays: the respawned document holds exactly that state, so a second crash
        # must restore it too (clearing it fell back to the original source and lost accepted work).
        if h.lost is None:
            h.lost = {"respawned_from": src or "empty document"}
        return h.worker

    def _call(self, h, method, params=None, timeout=120.0):
        w = self._worker(h)
        try:
            res = w.call(method, params, timeout=timeout)
        except BridgeError as e:
            raise ToolError(e.code, e.message, **e.details)
        except (ConnectionError, TimeoutError, OSError) as e:
            hard_timeout = isinstance(e, TimeoutError)
            w.kill()
            info = w.exit_info()
            src = h.checkpoint or h.source
            h.lost = {"respawned_from": src or "empty document", "after": method, "worker_exit": info}
            raise ToolError("timeout" if hard_timeout else "session_lost",
                            ("the session worker did not return in time and was killed; " if hard_timeout else
                             "the session worker died; ") +
                            "the next call respawns it from the pre-change checkpoint (any open change is gone)",
                            worker_exit=info, method=method, log_tail=w.log_tail()[-1500:])
        if h.lost is not None and isinstance(res, dict):
            res = dict(res, recovered=h.lost)
            h.lost = None
        return res

    def t_list_documents(self):
        docs = []
        for hid in self.order:
            h = self.docs[hid]
            alive = h.worker is not None and h.worker.alive()
            entry = {"doc": hid, "source": h.source, "alive": alive, "pid": h.worker.pid if alive else None}
            if alive:
                try:
                    entry.update(h.worker.call("info", timeout=10))
                except Exception as e:
                    entry["info_error"] = str(e)
            docs.append(entry)
        return {"schema": TOOL_SCHEMA, "documents": docs}

    def t_open_document(self, path=None, new_name=None):
        if not path and not new_name:
            raise ToolError("bad_argument", "give path (to open a file) or new_name (for an empty document)")
        if path:
            path = os.path.abspath(os.path.expanduser(path))
            if not os.path.exists(path):
                raise ToolError("not_found", f"no such file: {path}")
        self._n += 1
        base = os.path.splitext(os.path.basename(path))[0] if path else new_name
        hid = "".join(c if c.isalnum() or c in "-_" else "_" for c in base)[:40] + f"-{self._n}"
        h = DocHandle(hid, path, new_name)
        try:
            self._spawn(h, path)
        except BridgeError as e:
            raise ToolError(e.code, e.message, **e.details)
        except WorkerStartError as e:
            raise ToolError("worker_start_failed", str(e))
        self.docs[hid] = h
        self.order.append(hid)
        info = h.worker.call("info", timeout=10)
        return {"schema": TOOL_SCHEMA, "doc": hid, "pid": h.worker.pid, **info}

    def t_get_state(self, doc=None, page=None, page_size=None):
        h = self._handle(doc)
        if page is not None and page_size is None:
            page_size = 50
        return {"doc": h.id, **self._call(h, "state", {"page": page, "page_size": page_size})}

    def t_describe_entity(self, entity, doc=None):
        h = self._handle(doc)
        return {"doc": h.id, **self._call(h, "describe", {"entity": entity})}

    def t_get_selection(self, doc=None):
        if not self.docs:
            return {"selection": [], "headless": True, "note": "no document is open and no GUI is attached"}
        h = self._handle(doc)
        return {"doc": h.id, **self._call(h, "selection")}

    def t_exec_code(self, code, doc=None, timeout=30.0, recompute=True):
        h = self._handle(doc)
        report = policy.scan(code)
        ok, reason = policy.decide(report, self.exec_policy)
        if not ok:  # refuse before touching the worker
            raise ToolError("policy_refused", reason, policy={"mode": self.exec_policy, **report})
        timeout = float(timeout)
        res = self._call(h, "exec", {"code": code, "timeout": timeout, "recompute": bool(recompute)},
                         timeout=timeout + 15.0)
        return {"doc": h.id, "_is_error": not res.get("ok"), **res}

    def t_verify(self, doc=None, checks=None, timeout=None):
        h = self._handle(doc)
        if checks is None:
            checks = self._call(h, "default_checks")["checks"]
        if not isinstance(checks, list) or not checks:
            raise ToolError("bad_argument", "checks must be a non-empty list")
        snap = os.path.join(self._tmp, f"{h.id}-verify.FCStd")
        self._call(h, "snapshot", {"path": snap})
        try:
            specs = checks
            if timeout is not None:
                specs = [dict({"check": c} if isinstance(c, str) else c, timeout=float(timeout)) for c in checks]
            out = self.pool.run(snap, specs)
        finally:
            try:
                os.unlink(snap)
            except OSError:
                pass
        out.pop("path", None)
        res = {"doc": h.id, "schema": TOOL_SCHEMA, **out}
        if out["status"] != "ok":
            bad = [r["check"] for r in out["results"] if r.get("status") != "ok"]
            res.update(error="verify_" + out["status"], _is_error=True,
                       message=f"verify worker {out['status']} in check(s) {bad}; other checks completed and the "
                               "server keeps serving")
        return res

    def t_begin_change(self, doc=None, discard_pending=False):
        h = self._handle(doc)
        if self._call(h, "info", {}).get("change") is not None:
            # A change is open or proposed: the document holds unapproved edits, so it must never become
            # the checkpoint. Without discard_pending the worker refuses (change_pending); with it the
            # change is rejected and the existing checkpoint already holds the last committed state.
            res = self._call(h, "begin", {"discard_pending": bool(discard_pending)})
            return {"doc": h.id, **res, "checkpoint": "pre-change snapshot kept for crash recovery"}
        if h.checkpoint_path is None:
            h.checkpoint_path = os.path.join(self._tmp, f"{h.id}-checkpoint.FCStd")
        ck = h.checkpoint_path
        tmp = ck[:-len(".FCStd")] + ".new.FCStd"  # not ck + ".tmp": saveCopy uses that name itself
        self._call(h, "snapshot", {"path": tmp})
        os.replace(tmp, ck)  # atomic: a crash never leaves a torn checkpoint
        h.checkpoint = ck
        res = self._call(h, "begin", {"discard_pending": bool(discard_pending)})
        return {"doc": h.id, **res, "checkpoint": "pre-change snapshot kept for crash recovery"}

    def t_propose_change(self, doc=None, summary=""):
        h = self._handle(doc)
        return {"doc": h.id, **self._call(h, "propose", {"summary": summary})}

    def t_render(self, doc=None, view=None):
        return {"error": "unavailable", "_is_error": True,
                "message": "render is unavailable until I4: headless FreeCADCmd has no 3D view, and GUI rendering "
                           "runs only in a Linux VM or CI. Use get_state, describe_entity and verify instead."}

    def t_import_export(self, action, path, doc=None, objects=None, overwrite=False, pre_turn=False):
        h = self._handle(doc)
        if action == "export":
            return {"doc": h.id, **self._call(h, "export_file", {"path": path, "objects": objects,
                                                                  "overwrite": bool(overwrite),
                                                                  "pre_turn": bool(pre_turn)}, timeout=600)}
        if action == "import":
            if overwrite or pre_turn:
                raise ToolError("bad_argument", "overwrite and pre_turn apply to export only")
            if objects:
                raise ToolError("bad_argument", "objects applies to export only")
            return {"doc": h.id, **self._call(h, "import_file", {"path": path}, timeout=600)}
        raise ToolError("bad_argument", "action must be 'import' or 'export'")

    def t_search_parts(self, query, limit=10, formats=None):
        if not self.parts.roots:
            return {"query": query, "roots": [], "indexed": 0, "results": [],
                    "note": "no parts roots configured: pass --parts-path or set CADFORK_PARTS_PATH"}
        return self.parts.search(query, limit=limit, formats=formats)

    # -- resources
    @property
    def api(self):
        if self._api is None:
            self._api = apidocs.load() or {"classes": {}, "class_count": 0, "method_count": 0, "stub_files": 0,
                                           "source_hash": "none"}
        return self._api

    def _static_resources(self):
        return {
            "cadfork://cookbook": ("Cookbook", "text/markdown", "Recipes for the turn loop, paging, export and errors.",
                                   lambda: cookbook.COOKBOOK),
            "cadfork://api/index": ("FreeCAD API index", "text/markdown",
                                    "Every class from the .pyi binding stubs with its resource URI.",
                                    lambda: apidocs.index_markdown(self.api)),
            "cadfork://state/schema": ("State schema", "application/json", "JSON schema of get_state (agentcore.state/1).",
                                       lambda: _read(os.path.join(AGENTCORE_PARENT, "AgentCore", "state", "schema.json"))),
        }

    def _resource_list(self):
        out = [{"uri": u, "name": u.split("//", 1)[1], "title": t, "mimeType": m, "description": d}
               for u, (t, m, d, _) in self._static_resources().items()]
        for key, c in self.api["classes"].items():
            out.append({"uri": apidocs.URI_PREFIX + key, "name": key, "title": c["name"], "mimeType": "text/markdown",
                        "description": (c["doc"] or f"{c['name']} ({c['module']})")[:200]})
        return out

    def _resource_read(self, rid, uri):
        static = self._static_resources()
        if uri in static:
            _, mime, _, fn = static[uri]
            return _ok(rid, {"contents": [{"uri": uri, "mimeType": mime, "text": fn()}]})
        if isinstance(uri, str) and uri.startswith(apidocs.URI_PREFIX):
            c = self.api["classes"].get(uri[len(apidocs.URI_PREFIX):])
            if c is not None:
                return _ok(rid, {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": apidocs.class_markdown(c)}]})
        return _err(rid, RESOURCE_NOT_FOUND, f"Resource not found: {uri}", {"uri": uri})


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError as e:
        return json.dumps({"error": str(e)})


def _ok(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _err(rid, code, message, data=None):
    e = {"code": code, "message": message}
    if data is not None:
        e["data"] = data
    return {"jsonrpc": "2.0", "id": rid, "error": e}


def _result(payload, is_error=False):
    payload = dict(payload)
    payload.setdefault("schema", TOOL_SCHEMA)
    return {"content": [{"type": "text", "text": json.dumps(payload, default=str, sort_keys=True)}],
            "structuredContent": json.loads(json.dumps(payload, default=str)), "isError": bool(is_error)}


def main(argv=None):
    import argparse

    ap = argparse.ArgumentParser(prog="cadfork-mcp", description="Thin MCP stdio server over headless FreeCAD (AgentCore).")
    ap.add_argument("--freecadcmd", help="path to FreeCADCmd (default: CADFORK_FREECADCMD, then PATH)")
    ap.add_argument("--parts-path", action="append", default=None, help="parts corpus root; repeatable")
    ap.add_argument("--pool-size", type=int, default=2, help="parallel verify workers")
    ap.add_argument("--verify-timeout", type=float, default=120.0, help="seconds per verify check")
    ap.add_argument("--exec-policy", choices=policy.MODES, default="flag",
                    help="flag: run exec_code and flag file/process/network access; strict: refuse flagged code")
    ap.add_argument("--approver-socket", help="socket of the human-side approver process (AgentCore/diff/approver.py) "
                                              "that session workers ask before accepting a proposal")
    ap.add_argument("--version", action="store_true")
    ap.add_argument("--list-tools", action="store_true", help="print the tool names and exit")
    a = ap.parse_args(argv)
    if a.version:
        print(f"{SERVER_NAME} {SERVER_VERSION} (tools {TOOL_SCHEMA}, MCP {', '.join(PROTOCOL_VERSIONS)})")
        return 0
    if a.list_tools:
        print("\n".join(TOOL_NAMES))
        return 0
    try:
        srv = Server(freecadcmd=a.freecadcmd, parts_path=a.parts_path, pool_size=a.pool_size,
                     verify_timeout=a.verify_timeout, exec_policy=a.exec_policy, approver_socket=a.approver_socket)
    except FileNotFoundError as e:
        print(f"cadfork-mcp: {e}", file=sys.stderr)
        return 2
    try:
        srv.serve_stdio()
    finally:
        srv.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
