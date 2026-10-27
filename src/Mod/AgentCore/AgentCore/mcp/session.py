# SPDX-License-Identifier: LGPL-2.1-or-later
"""FreeCAD-side tool bodies for one document (runs inside FreeCAD).

A ``Session`` owns one open document and at most one agent change (an F3
``AgentTurn``). The MCP server reaches it through ``bridge.Bridge`` in a
``FreeCADCmd`` session worker; tests drive it in-process.

Methods exposed over the bridge are listed in ``Session.METHODS``. There is no
accept or approve method: only the GUI holds F3's apply token, so nothing that
arrives over the bridge can apply a proposal.
"""

import contextlib
import importlib
import io
import json
import os
import signal
import threading
import traceback

import FreeCAD as App

from . import policy
from .bridge import BridgeError

DIFF_MODULE_ENV = "CADFORK_MCP_DIFF_MODULE"  # test hook: module providing AgentTurn
APPROVAL_METHODS = {"accept", "approve", "apply", "accept_change", "approve_change", "apply_change"}
STEP_EXT = (".step", ".stp")
BREP_EXT = (".brep", ".brp")
FCSTD_EXT = (".fcstd",)


class ExecTimeout(BaseException):
    """Raised in the main thread by SIGALRM when exec_code overruns its timeout."""


def resolve_agent_turn():
    """Return F3's ``AgentTurn`` class, imported at call time (never at import).

    Raises ``BridgeError("diff_unavailable")`` while AgentCore.diff lacks it.
    """
    modname = os.environ.get(DIFF_MODULE_ENV) or "AgentCore.diff"
    try:
        mod = importlib.import_module(modname)
    except Exception as e:
        raise BridgeError("diff_unavailable", f"cannot import {modname}: {e}")
    cls = getattr(mod, "AgentTurn", None)
    if cls is None:
        raise BridgeError("diff_unavailable",
                          f"{modname} has no AgentTurn yet (F3 not merged): begin_change, exec_code edits "
                          "and propose_change are unavailable; read-only tools and verify still work")
    return cls


def _jsonable(x, depth=0):
    if depth > 6:
        return repr(x)[:200]
    if x is None or isinstance(x, (bool, int, float, str)):
        return x
    if isinstance(x, dict):
        return {str(k): _jsonable(v, depth + 1) for k, v in x.items()}
    if isinstance(x, (list, tuple, set)):
        return [_jsonable(v, depth + 1) for v in x]
    for attr in ("x", "y", "z"):
        if not hasattr(x, attr):
            break
    else:
        try:
            return [float(x.x), float(x.y), float(x.z)]
        except Exception:
            pass
    if hasattr(x, "Value") and hasattr(x, "Unit"):
        return float(x.Value)
    if hasattr(x, "Name") and hasattr(x, "TypeId"):
        return {"object": x.Name, "type": x.TypeId}
    return repr(x)[:500]


class Session:
    METHODS = ("ping", "open", "info", "state", "describe", "selection", "exec", "begin", "propose",
               "snapshot", "import_file", "export_file", "default_checks", "close", "shutdown")

    def __init__(self, turn_factory=None, policy_mode="flag"):
        self.doc = None
        self.source = None
        self.turn = None
        self.turn_state = None  # None | "open" | "proposed"
        self.turn_no = 0
        self.proposal = None
        self._turn_factory = turn_factory
        self.policy_mode = policy_mode
        self._audit = policy.RuntimeAudit.instance()

    # -- dispatch
    def handle(self, method, params):
        if method in APPROVAL_METHODS:
            raise BridgeError("approval_refused",
                              "approval is human-only: the FreeCAD GUI holds F3's apply token and no bridge "
                              "method can accept a proposal")
        if method not in self.METHODS:
            raise BridgeError("unknown_method", f"unknown bridge method {method!r}")
        return getattr(self, "m_" + method)(**params)

    def _need_doc(self):
        if self.doc is None:
            raise BridgeError("no_document", "no document is open in this session")
        return self.doc

    # -- documents
    def m_ping(self):
        return {"pong": True, "pid": os.getpid(), "freecad": ".".join(App.Version()[:3])}

    def m_open(self, path=None, name=None):
        if self.doc is not None:
            raise BridgeError("document_open", "this session already holds a document")
        if path:
            path = os.path.abspath(os.path.expanduser(path))
            if not os.path.exists(path):
                raise BridgeError("not_found", f"no such file: {path}")
            low = path.lower()
            if low.endswith(FCSTD_EXT):
                self.doc = App.openDocument(path)
            elif low.endswith(STEP_EXT + BREP_EXT):
                self.doc = App.newDocument(name or _safe_name(os.path.splitext(os.path.basename(path))[0]))
                self._import_into(path)
                self.doc.recompute()
            else:
                raise BridgeError("unsupported_format", f"cannot open {path}: use .FCStd, .step/.stp or .brep")
            self.source = path
        else:
            self.doc = App.newDocument(_safe_name(name or "Unnamed"))
        return self.m_info()

    def m_info(self):
        doc = self._need_doc()
        return {"name": doc.Name, "label": doc.Label, "file": doc.FileName or None, "source": self.source,
                "object_count": len(doc.Objects), "change": self.turn_state,
                "proposal_id": (self.proposal or {}).get("proposal_id")}

    def m_close(self):
        if self.doc is not None:
            if self.turn is not None:
                self._reject_quietly()
            App.closeDocument(self.doc.Name)
            self.doc = None
        return {"closed": True}

    def m_shutdown(self):
        return self.m_close()

    # -- reading
    def m_state(self, page=None, page_size=None):
        from AgentCore import state

        try:
            return state.dump(self._need_doc(), page, page_size)
        except ValueError as e:
            raise BridgeError("bad_argument", str(e))

    def m_describe(self, entity):
        from AgentCore import state

        doc = self._need_doc()
        obj, sub = state.resolve_entity(doc, entity)
        if obj is None:
            raise BridgeError("not_found", f"no object for entity {entity!r}")
        if ":" in entity and not sub:
            raise BridgeError("not_found", f"element of {entity!r} no longer exists")
        try:
            d = state.describe_entity(obj, sub)
        except Exception as e:
            raise BridgeError("describe_failed", f"{type(e).__name__}: {e}")
        return {"entity": entity, "resolved": state.entity_id(obj, sub), **_jsonable(d)}

    def m_selection(self):
        if not App.GuiUp:
            return {"selection": [], "headless": True,
                    "note": "No GUI in this session (FreeCADCmd), so there is no selection. Refer to "
                            "entities by ID from get_state instead, e.g. 'Box:Face3'."}
        import FreeCADGui as Gui  # only reached inside the GUI bridge

        from AgentCore import state

        sel = []
        for s in Gui.Selection.getSelectionEx(self._need_doc().Name):
            subs = list(s.SubElementNames) or [""]
            for sub in subs:
                sel.append(state.entity_id(s.Object, sub))
        return {"selection": sel, "headless": False}

    def m_default_checks(self):
        from AgentCore import verify

        return {"checks": list(verify.DEFAULT_CHECKS)}

    # -- changes
    def m_begin(self, discard_pending=False):
        doc = self._need_doc()
        if self.turn is not None:
            if not discard_pending:
                raise BridgeError("change_pending",
                                  f"change {self.turn_no} is {self.turn_state}; pass discard_pending=true to reject it first")
            self._reject_quietly()
        cls = self._turn_factory or resolve_agent_turn()
        turn = cls(doc)
        entered = turn.__enter__()
        self.turn = entered if entered is not None else turn
        self.turn_no += 1
        self.turn_state = "open"
        self.proposal = None
        return {"change": self.turn_no, "state": "open"}

    def _reject_quietly(self):
        try:
            self.turn.reject()
        finally:
            self.turn = None
            self.turn_state = None
            self.proposal = None

    def m_propose(self, summary=""):
        self._need_doc()
        if self.turn is None:
            raise BridgeError("no_change", "no open change: call begin_change first")
        if self.turn_state == "proposed":
            return self.proposal
        cs = self.turn.change_set()
        self.turn_state = "proposed"
        self.proposal = {
            "proposal_id": f"change-{self.turn_no}",
            "status": "pending_human_approval",
            "summary": summary,
            "change_set": _jsonable(cs),
            "apply": "Only a human can accept this, in the FreeCAD GUI change-set panel (F3 holds the apply "
                     "token there). The MCP server cannot approve it. Call begin_change with "
                     "discard_pending=true to withdraw it.",
        }
        return self.proposal

    def m_exec(self, code, timeout=30.0, recompute=True):
        doc = self._need_doc()
        if self.turn is None:
            raise BridgeError("no_change", "exec_code runs inside a change: call begin_change first")
        if self.turn_state == "proposed":
            raise BridgeError("change_proposed", "the change is proposed and frozen; begin a new change to edit")
        report = policy.scan(code)
        ok, reason = policy.decide(report, self.policy_mode)
        if not ok:
            raise BridgeError("policy_refused", reason, policy={"mode": self.policy_mode, **report})
        ns = {"__name__": "__agent__", "App": App, "FreeCAD": App, "doc": doc, "result": None}
        out, err = io.StringIO(), io.StringIO()
        timeout = float(timeout)
        use_alarm = threading.current_thread() is threading.main_thread() and hasattr(signal, "setitimer")
        prev = None
        status, tb = "ok", None
        compiled = compile(code, "<exec_code>", "exec")

        def _alarm(signum, frame):
            raise ExecTimeout()

        if use_alarm:
            prev = signal.signal(signal.SIGALRM, _alarm)
            signal.setitimer(signal.ITIMER_REAL, timeout)
        self._audit.start()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                exec(compiled, ns)
                if recompute:
                    doc.recompute()
        except ExecTimeout:
            status, tb = "timeout", f"exec_code exceeded {timeout}s and was interrupted"
        except Exception:
            status, tb = "error", traceback.format_exc(limit=6)
        finally:
            events = self._audit.stop()
            if use_alarm:
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGALRM, prev)
        invalid = []
        for o in doc.Objects:
            try:
                if "Invalid" in o.State or "Error" in o.State:
                    invalid.append(o.Name)
            except Exception:
                pass
        res = {"status": status, "ok": status == "ok", "stdout": out.getvalue()[-8000:],
               "stderr": err.getvalue()[-4000:], "result": _jsonable(ns.get("result")),
               "invalid_objects": sorted(invalid), "object_count": len(doc.Objects),
               "policy": {"version": policy.POLICY_VERSION, "mode": self.policy_mode,
                          "static_flags": report["flags"], "runtime_events": events,
                          "note": "flags are advisory: exec_code is confined to the change, not sandboxed"}}
        if tb:
            res["error"] = tb
        return res

    # -- files
    def m_snapshot(self, path):
        doc = self._need_doc()
        doc.saveCopy(path)
        return {"path": path}

    def _import_into(self, path):
        low = path.lower()
        if low.endswith(STEP_EXT):
            import Import

            Import.insert(path, self.doc.Name)
        elif low.endswith(BREP_EXT):
            import Part

            shape = Part.Shape()
            shape.read(path)
            obj = self.doc.addObject("Part::Feature", _safe_name(os.path.splitext(os.path.basename(path))[0]))
            obj.Shape = shape
        else:
            raise BridgeError("unsupported_format", "import takes .step/.stp or .brep; open .FCStd with open_document")

    def m_import_file(self, path):
        doc = self._need_doc()
        if self.turn is None or self.turn_state != "open":
            raise BridgeError("no_change", "import edits the document: call begin_change first")
        path = os.path.abspath(os.path.expanduser(path))
        if not os.path.exists(path):
            raise BridgeError("not_found", f"no such file: {path}")
        before = {o.Name for o in doc.Objects}
        self._import_into(path)
        doc.recompute()
        return {"imported": sorted(o.Name for o in doc.Objects if o.Name not in before), "path": path}

    def m_export_file(self, path, objects=None):
        doc = self._need_doc()
        path = os.path.abspath(os.path.expanduser(path))
        low = path.lower()
        if low.endswith(FCSTD_EXT):
            doc.saveCopy(path)
            return {"path": path, "format": "FCStd", "objects": [o.Name for o in doc.Objects]}
        if objects:
            objs = []
            for n in objects:
                o = doc.getObject(n)
                if o is None:
                    raise BridgeError("not_found", f"no object {n!r}")
                objs.append(o)
        else:
            objs = [o for o in doc.Objects if not o.InList and hasattr(o, "Shape") and not o.Shape.isNull()]
        if not objs:
            raise BridgeError("nothing_to_export", "no objects with a shape to export")
        if low.endswith(STEP_EXT):
            import Import

            Import.export(objs, path)
            fmt = "STEP"
        elif low.endswith(BREP_EXT):
            import Part

            shapes = [o.Shape for o in objs]
            (shapes[0] if len(shapes) == 1 else Part.makeCompound(shapes)).exportBrep(path)
            fmt = "BREP"
        else:
            raise BridgeError("unsupported_format", "export to .step/.stp, .brep or .FCStd")
        return {"path": path, "format": fmt, "objects": [o.Name for o in objs], "bytes": os.path.getsize(path)}


def _safe_name(s):
    out = "".join(c if c.isalnum() or c == "_" else "_" for c in s) or "Doc"
    return out if not out[0].isdigit() else "D" + out


def dumps(obj):
    return json.dumps(obj, default=str, sort_keys=True)
