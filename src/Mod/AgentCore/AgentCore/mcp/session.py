# SPDX-License-Identifier: LGPL-2.1-or-later
"""FreeCAD-side tool bodies for one document (runs inside FreeCAD).

A ``Session`` owns one open document and at most one agent change (an F3
``AgentTurn``). The MCP server reaches it through ``bridge.Bridge`` in a
``FreeCADCmd`` session worker; tests drive it in-process.

Methods exposed over the bridge are listed in ``Session.METHODS``. There is no
accept or approve method: nothing that arrives over the bridge can apply a
proposal. The apply key lives only in the human-side approver process
(``AgentCore/diff/approver.py``, or the GUI). A sealed worker polls that
approver for the proposal's (turn id, digest) and accepts only with the token
the approver hands out after a human approved exactly that change set; the
approver verifies it again on accept (see ``AgentCore/diff/SECURITY.md``).
"""

import contextlib
import importlib
import io
import json
import os
import shutil
import signal
import tempfile
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


def _is_same_file(path, protected):
    """True if ``path`` is any of ``protected`` by identity, not by spelling.

    ``os.path.samefile`` compares device and inode, so it catches a case
    variant on a case-insensitive volume, a symlink and a hardlink; the
    realpath/casefold comparison covers a target that does not exist yet.
    """
    for p in protected:
        if not p:
            continue
        try:
            if os.path.exists(path) and os.path.exists(p) and os.path.samefile(path, p):
                return True
        except OSError:
            return True  # cannot tell: refuse
        if os.path.realpath(path) == os.path.realpath(p):
            return True
    return False


class Session:
    METHODS = ("ping", "open", "info", "state", "describe", "selection", "exec", "begin", "propose",
               "snapshot", "import_file", "export_file", "default_checks", "close", "shutdown")

    def __init__(self, turn_factory=None, policy_mode="flag", approver_socket=None):
        self.doc = None
        self.source = None
        self.turn = None
        self.turn_state = None  # None | "open" | "proposed"
        self.turn_no = 0
        self.proposal = None
        self.last_decision = None  # {"change", "decision", ...} of the last turn that ended
        self._turn_factory = turn_factory
        self.policy_mode = policy_mode
        self.approver_socket = approver_socket
        self._audit = policy.RuntimeAudit.instance()
        self._snapdir = None

    # -- turn bookkeeping
    def _refresh_checkpoint(self):
        """Persist the just-committed document as the server's crash-recovery checkpoint: write a
        temp file, then rename over the old one, so a crash never sees a stale or torn checkpoint."""
        path = os.environ.get("CADFORK_MCP_CHECKPOINT")
        if not path or self.doc is None:
            return None
        tmp = path[:-len(".FCStd")] + ".new.FCStd"  # not path + ".tmp": saveCopy uses that name itself
        try:
            self.doc.saveCopy(tmp)
            os.replace(tmp, path)
        except Exception as exc:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return f"{type(exc).__name__}: {exc}"
        return None

    def _end_turn(self, decision, **extra):
        if decision == "accepted":  # every commit path (approver accept, accept seen via _sync) lands here
            err = self._refresh_checkpoint()
            if err:
                extra["checkpoint_error"] = err
        self.last_decision = {"change": self.turn_no, "decision": decision, **extra}
        self.turn = None
        self.turn_state = None
        self.proposal = None

    def _sync(self):
        """Reconcile with the turn's own status: code run by the agent (or a
        human in the GUI) may have ended the turn without going through us."""
        if self.turn is None:
            return
        st = getattr(self.turn, "status", None)
        if st in ("accepted", "rejected"):
            self._end_turn(st, outside_session=True,
                           note="the turn was decided outside the session's approval flow")
        elif st == "proposed" and self.turn_state == "open":
            self.turn_state = "proposed_outside"

    def poll_approval(self):
        """Ask the approver whether a human approved the pending proposal; if
        so, accept with the approver's token. Called from the worker's loop."""
        if self.turn is None or self.turn_state != "proposed" or not self.approver_socket:
            return None
        ids = self.proposal or {}
        if not ids.get("turn_id") or not ids.get("digest"):
            return None
        from AgentCore.diff import approver as _approver
        try:
            resp = _approver.request(self.approver_socket, {"op": "poll", "turn_id": ids["turn_id"],
                                                            "digest": ids["digest"]}, timeout=0.5)
        except (OSError, ValueError):
            return None
        tok = resp.get("token") if resp.get("ok") else None
        if not tok:
            return None
        from AgentCore.diff.authority import ApplyToken
        try:
            res = self.turn.accept(ApplyToken.from_wire(tok))
        except Exception as exc:
            self.proposal["accept_error"] = f"{type(exc).__name__}: {exc}"
            self._sync()
            return None
        self._end_turn("accepted", result=_jsonable(res), proposal_id=ids.get("proposal_id"))
        return res

    # -- dispatch
    def handle(self, method, params):
        self._sync()
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
                "proposal_id": (self.proposal or {}).get("proposal_id"), "last_decision": self.last_decision}

    def m_close(self):
        if self.doc is not None:
            if self.turn is not None:
                self._reject_quietly()
            App.closeDocument(self.doc.Name)
            self.doc = None
        if self._snapdir:
            shutil.rmtree(self._snapdir, ignore_errors=True)
            self._snapdir = None
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
        turn = cls(doc, **self._turn_kwargs(cls))
        entered = turn.__enter__()
        self.turn = entered if entered is not None else turn
        self.turn_no += 1
        self.turn_state = "open"
        self.proposal = None
        return {"change": self.turn_no, "state": "open"}

    def _turn_kwargs(self, cls):
        """A before-state snapshot store for F3's AgentTurn (reject and the
        pre-turn export use it); stand-ins that take no ``store`` get none."""
        import inspect
        try:
            if "store" not in inspect.signature(cls).parameters:
                return {}
        except (TypeError, ValueError):
            return {}
        from AgentCore.diff import SnapshotStore
        if self._snapdir is None:
            self._snapdir = tempfile.mkdtemp(prefix="acmcp-snap-")
        return {"store": SnapshotStore(self._snapdir)}

    def _before_snapshot(self):
        snap = getattr(self.turn, "snapshot", None)
        return snap.get("path") if isinstance(snap, dict) else None

    def _reject_quietly(self):
        """Reject the current change. An open turn is closed through its context exit with an
        exception, which F3 answers by rejecting; a proposed one gets ``reject()``."""
        try:
            if self.turn_state == "open":
                exc = RuntimeError("change discarded by the agent")
                self.turn.__exit__(type(exc), exc, None)
            if getattr(self.turn, "status", None) not in ("rejected", "accepted"):
                self.turn.reject()
        finally:
            self._end_turn("rejected")

    def m_propose(self, summary=""):
        self._need_doc()
        if self.turn is None:
            raise BridgeError("no_change", "no open change: call begin_change first")
        if self.turn_state == "proposed":
            return self.proposal
        if self.turn_state == "proposed_outside":
            self._reject_quietly()
            raise BridgeError("change_aborted", "the change was closed by code inside exec_code, not by "
                              "propose_change; it was rejected")
        try:
            self.turn.__exit__(None, None, None)  # F3: settle, verify after, close recording, build the change set
        except Exception as exc:
            if getattr(self.turn, "status", None) not in ("rejected", "accepted"):
                self._reject_quietly()
            else:
                self._end_turn(self.turn.status)
            raise BridgeError("change_aborted", f"{type(exc).__name__}: {exc}")
        self.turn_state = "proposed"
        cs = self.turn.change_set()
        ids = {}
        if callable(getattr(self.turn, "digest", None)):
            ids = {"turn_id": self.turn.turn_id, "digest": self.turn.digest()}
        self.proposal = {
            "proposal_id": f"change-{self.turn_no}",
            "status": "pending_human_approval",
            "summary": summary,
            **ids,
            "change_set": _jsonable(cs),
            "apply": "Only a human can accept this, in the FreeCAD GUI change-set panel or the approver "
                     "process, which hold the apply key. The MCP server cannot approve it. Call "
                     "begin_change with discard_pending=true to withdraw it.",
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
        aborted = self._check_turn_after_exec()
        if aborted:
            res["status"], res["ok"] = "change_aborted", False
            res["change_aborted"] = aborted
        return res

    def _check_turn_after_exec(self):
        """Abort the change when the agent's code tampered with it: undo/redo/
        clearUndos inside the turn (F3 records it), or closed/decided the turn
        itself instead of going through propose_change."""
        t = self.turn
        if t is None:
            return None
        st = getattr(t, "status", "open")
        why = list(getattr(t, "tampered", None) or [])
        if st != "open":
            why.append(f"the turn was moved to {st!r} by code inside exec_code")
        if not why:
            return None
        try:
            if st == "open":
                exc = RuntimeError("change aborted: " + "; ".join(why))
                t.__exit__(type(exc), exc, None)
            elif st == "proposed":
                t.reject()
            elif st == "accepted":  # accepted without the approver: take the entry back off the model
                d = getattr(t, "doc", None)
                if d is not None and list(d.UndoNames)[:1] == [getattr(t, "undo_name", None)]:
                    d.undo()
                    why.append("the self-accepted entry was undone")
        except Exception as exc:
            why.append(f"reject failed: {type(exc).__name__}: {exc}")
        self._end_turn(getattr(t, "status", "rejected"), aborted=why)
        return why

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

    def m_export_file(self, path, objects=None, overwrite=False, pre_turn=False):
        """Export the document (or objects) to STEP, BREP or FCStd.

        - While a change is open or proposed, the live document holds edits no
          human approved, so export is refused unless ``pre_turn=True``, which
          exports the before-state of the change instead (from its snapshot).
        - An existing file is never overwritten unless ``overwrite=True``, and
          the session's own source file never (save the model in the GUI).
        - After writing, the file is checked to exist and to have changed.
        """
        doc = self._need_doc()
        path = os.path.abspath(os.path.expanduser(path))
        low = path.lower()
        if not low.endswith(FCSTD_EXT + STEP_EXT + BREP_EXT):
            raise BridgeError("unsupported_format", "export to .step/.stp, .brep or .FCStd")
        if _is_same_file(path, (self.source, doc.FileName)):
            raise BridgeError("refused_target", f"{path} is the document's own source file; export elsewhere")
        if os.path.exists(path) and not overwrite:
            raise BridgeError("file_exists", f"{path} exists; pass overwrite=true to replace it")
        if os.path.isdir(path):
            raise BridgeError("refused_target", f"{path} is a directory")
        src_doc, temp_doc = doc, None
        if self.turn is not None:
            if not pre_turn:
                raise BridgeError("change_pending",
                                  f"change {self.turn_no} is {self.turn_state} and not approved: export would write "
                                  "unapproved edits. Pass pre_turn=true to export the state before the change, or "
                                  "export after the change is accepted or withdrawn")
            snap = self._before_snapshot()
            if not snap or not os.path.exists(snap):
                raise BridgeError("pre_turn_unavailable", "this change has no before-state snapshot to export from")
            temp_doc = App.openDocument(snap, hidden=True)
            src_doc = temp_doc
        try:
            before = _stat_sig(path)
            res = self._export_from(src_doc, path, low, objects)
        finally:
            if temp_doc is not None:
                App.closeDocument(temp_doc.Name)
                App.setActiveDocument(doc.Name)
        after = _stat_sig(path)
        if after is None or after == before:
            raise BridgeError("export_failed", f"nothing was written to {path}")
        res.update({"bytes": after[0], "pre_turn": bool(self.turn is not None and pre_turn),
                    "overwrote": before is not None})
        return res

    def _export_from(self, doc, path, low, objects):
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
        return {"path": path, "format": fmt, "objects": [o.Name for o in objs]}


def _stat_sig(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    import hashlib
    with open(path, "rb") as fh:
        return (st.st_size, st.st_mtime_ns, hashlib.sha256(fh.read()).hexdigest())


def _safe_name(s):
    out = "".join(c if c.isalnum() or c == "_" else "_" for c in s) or "Doc"
    return out if not out[0].isdigit() else "D" + out


def dumps(obj):
    return json.dumps(obj, default=str, sort_keys=True)
