# SPDX-License-Identifier: LGPL-2.1-or-later
"""exec_code policy: confinement and flagging, honestly not a sandbox.

``exec_code`` runs arbitrary Python inside the session worker's FreeCAD process.
This module can (a) refuse code that tries to approve a change (only the GUI
holds F3's apply token), (b) flag file, subprocess, network and dynamic-code
access found by a static AST scan, and (c) record matching runtime audit events
(PEP 578 audit hooks) while the code runs. Anything determined to escape will
escape: ``exec`` of a string built at runtime, C extensions and FreeCAD's own
C++ file I/O are invisible to both scans. The real confinement is structural:
edits happen inside the change (one F3 transaction) of a worker process that
holds a copy of the document, and nothing reaches the user's model until a
human accepts the proposal in the GUI.

Pure stdlib; imported by the MCP server (no FreeCAD) and by the worker.
"""

import ast
import sys
import threading

POLICY_VERSION = "agentcore.mcp.policy/1"
MODES = ("flag", "strict")

FLAG_MODULES = {
    "os": "file", "shutil": "file", "pathlib": "file", "tempfile": "file", "glob": "file", "io": "file",
    "subprocess": "subprocess", "multiprocessing": "subprocess", "pty": "subprocess",
    "socket": "network", "urllib": "network", "http": "network", "requests": "network", "ftplib": "network",
    "smtplib": "network", "ssl": "network", "asyncio": "network",
    "ctypes": "native", "cffi": "native", "importlib": "dynamic", "signal": "process", "threading": "process",
}
FLAG_CALLS = {
    "open": "file", "exec": "dynamic", "eval": "dynamic", "compile": "dynamic", "__import__": "dynamic",
    "system": "subprocess", "popen": "subprocess", "Popen": "subprocess", "spawn": "subprocess",
    "remove": "file", "unlink": "file", "rmtree": "file", "rename": "file",
    "saveAs": "file", "save": "file", "saveCopy": "file", "openDocument": "file", "closeDocument": "document",
    "exportBrep": "file", "exportStep": "file", "export": "file", "insert": "file", "read": "file",
    "newDocument": "document", "setActiveDocument": "document",
}
# Names whose use means "try to approve": refused, never just flagged.
APPROVAL_ATTRS = {"accept", "approve", "apply_token", "APPLY_TOKEN", "_apply_token"}
TRANSACTION_ATTRS = {"commitTransaction", "abortTransaction", "openTransaction", "undo", "redo", "clearUndos"}


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def scan(code):
    """Static scan. Returns ``{"flags": [...], "refused": [...], "syntax_error": str|None}``.

    Each flag is ``{"category", "what", "line"}``. ``refused`` entries block execution
    in every mode (approval attempts, direct transaction control).
    """
    out = {"flags": [], "refused": [], "syntax_error": None}
    try:
        tree = ast.parse(code, "<exec_code>", "exec")
    except SyntaxError as e:
        out["syntax_error"] = f"{e.msg} (line {e.lineno})"
        return out
    seen = set()

    def flag(cat, what, line):
        key = (cat, what, line)
        if key not in seen:
            seen.add(key)
            out["flags"].append({"category": cat, "what": what, "line": line})

    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Import):
            for a in node.names:
                top = a.name.split(".")[0]
                if top in FLAG_MODULES:
                    flag(FLAG_MODULES[top], "import " + a.name, line)
        elif isinstance(node, ast.ImportFrom):
            top = (node.module or "").split(".")[0]
            if top in FLAG_MODULES:
                flag(FLAG_MODULES[top], "from " + (node.module or "") + " import", line)
            if (node.module or "").startswith("AgentCore.diff"):
                for a in node.names:
                    if a.name in APPROVAL_ATTRS:
                        out["refused"].append({"what": "import " + a.name, "line": line,
                                               "reason": "approval is human-only"})
        elif isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else "")
            if name in APPROVAL_ATTRS:
                out["refused"].append({"what": _dotted(fn) or name, "line": line,
                                       "reason": "approval is human-only: only the FreeCAD GUI holds the apply token"})
            elif name in TRANSACTION_ATTRS:
                out["refused"].append({"what": _dotted(fn) or name, "line": line,
                                       "reason": "the change owns the transaction; use begin_change/propose_change"})
            elif name in FLAG_CALLS:
                flag(FLAG_CALLS[name], (_dotted(fn) or name) + "()", line)
        elif isinstance(node, ast.Attribute) and node.attr in APPROVAL_ATTRS and not isinstance(node.ctx, ast.Load):
            out["refused"].append({"what": _dotted(node), "line": line, "reason": "approval is human-only"})
        elif isinstance(node, ast.Name) and node.id in ("apply_token", "APPLY_TOKEN", "_apply_token"):
            out["refused"].append({"what": node.id, "line": line, "reason": "approval is human-only"})
    out["flags"].sort(key=lambda f: (f["line"], f["category"], f["what"]))
    return out


def decide(report, mode="flag"):
    """Return ``(allowed, reason)`` for a scan report under ``mode``."""
    if mode not in MODES:
        raise ValueError(f"unknown policy mode {mode!r}; use one of {MODES}")
    if report.get("syntax_error"):
        return False, "syntax error: " + report["syntax_error"]
    if report["refused"]:
        return False, "; ".join(f"{r['what']} (line {r['line']}): {r['reason']}" for r in report["refused"])
    if mode == "strict" and report["flags"]:
        cats = sorted({f["category"] for f in report["flags"]})
        return False, "strict policy refuses flagged access: " + ", ".join(cats)
    return True, ""


AUDIT_EVENTS = {
    "open": "file", "os.remove": "file", "os.rename": "file", "os.rmdir": "file", "os.mkdir": "file",
    "shutil.rmtree": "file", "shutil.copyfile": "file", "shutil.move": "file", "os.chmod": "file",
    "subprocess.Popen": "subprocess", "os.system": "subprocess", "os.exec": "subprocess",
    "os.posix_spawn": "subprocess", "os.spawn": "subprocess", "os.fork": "subprocess",
    "socket.connect": "network", "socket.bind": "network", "socket.getaddrinfo": "network",
    "urllib.Request": "network", "ctypes.dlopen": "native",
}
# exec/compile audit events are not recorded: FreeCAD and the exec_code wrapper raise them
# constantly; the static scan flags dynamic code instead.


class RuntimeAudit:
    """PEP 578 audit hook that records flagged events only while ``active``.

    Audit hooks cannot be removed once installed, so one instance is installed per
    process and toggled. Reads of Python source and bytecode (imports) are ignored.
    """

    _installed = None

    def __init__(self, limit=200):
        self.limit = limit
        self.events = []
        self._active = False
        self._owner = None

    @classmethod
    def instance(cls):
        if cls._installed is None:
            cls._installed = cls()
            sys.addaudithook(cls._installed._hook)
        return cls._installed

    def start(self):
        self.events = []
        self._owner = threading.get_ident()
        self._active = True

    def stop(self):
        self._active = False
        return list(self.events)

    def _hook(self, event, args):
        if not self._active or threading.get_ident() != self._owner:
            return
        cat = AUDIT_EVENTS.get(event)
        if cat is None:
            return
        detail = ""
        if event == "open":
            path, mode = (args + (None, None))[:2]
            path = str(path)
            if path.endswith((".py", ".pyc", ".so", ".pyd", ".pth")) or "__pycache__" in path:
                return
            detail = f"{path} mode={mode}"
        elif args:
            detail = repr(args[0])[:200]
        if len(self.events) < self.limit:
            self.events.append({"category": cat, "event": event, "detail": detail})
