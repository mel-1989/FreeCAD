# SPDX-License-Identifier: LGPL-2.1-or-later
"""exec_code policy: confinement and flagging, honestly not a sandbox.

``exec_code`` runs arbitrary Python inside the session worker's FreeCAD process.
This module can (a) refuse code that tries to approve a change or take over
the change's transaction (only the human-side approver holds the apply key),
(b) flag file, subprocess, network, dynamic-code and introspection access
found by a static AST scan, and (c) record matching runtime audit events
(PEP 578 audit hooks) while the code runs. Anything determined to escape will
escape: ``exec`` of a string built at runtime, C extensions and FreeCAD's own
C++ file I/O are invisible to both scans. The real confinement is structural:
edits happen inside the change (one F3 transaction) of a worker process that
holds a copy of the document and never the apply key, and nothing reaches
the user's model until a human approves the proposal in the approver. The
scan is advisory; that process boundary is the guarantee
(``AgentCore/diff/SECURITY.md``).

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
# F3's authority internals: naming any of them is an approval attempt.
AUTHORITY_NAMES = {"ApplyAuthority", "ApplyToken", "_VERIFIER", "verify_token", "mint", "mint_for",
                   "prepare_agent_process", "_SEALED", "from_wire", "_make_core", "Approver", "ApproverServer"}
TRANSACTION_ATTRS = {"commitTransaction", "abortTransaction", "openTransaction", "undo", "redo", "clearUndos",
                     "closeActiveTransaction", "setActiveTransaction"}
SENSITIVE = APPROVAL_ATTRS | AUTHORITY_NAMES | TRANSACTION_ATTRS
REFUSED_MODULES = ("AgentCore.diff.authority", "AgentCore.diff.approver")
DYNAMIC_ATTR_FUNCS = {"getattr", "setattr", "delattr", "hasattr"}
DYNAMIC_ATTR_METHODS = {"__getattribute__", "__getattr__", "__setattr__", "__delattr__"}
ALIASABLE = DYNAMIC_ATTR_FUNCS | {"vars", "globals", "locals", "__import__", "eval", "exec", "compile"}
INTROSPECTION_ATTRS = {"__closure__", "__globals__", "__code__", "__dict__", "__subclasses__", "__builtins__",
                       "cell_contents", "__self__", "__func__", "__wrapped__", "f_globals", "f_locals", "modules"}
INTROSPECTION_MODULES = {"gc", "inspect", "sys", "builtins", "types"}


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _const_int(x):
    if isinstance(x, ast.Constant) and isinstance(x.value, int):
        return x.value
    if isinstance(x, ast.UnaryOp) and isinstance(x.op, ast.USub) and isinstance(x.operand, ast.Constant) \
            and isinstance(x.operand.value, int):
        return -x.operand.value
    raise ValueError


def _fold(node, env):
    """The string a node evaluates to when that is knowable statically:
    literals, names bound to such strings, ``+`` and ``*`` on them, f-strings
    of literals, ``sep.join([...])``, ``%`` and ``.format`` of literals, and
    slicing or reversing. ``None`` otherwise."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return env.get(node.id)
    if isinstance(node, ast.BinOp):
        a, b = _fold(node.left, env), _fold(node.right, env)
        if isinstance(node.op, ast.Add) and a is not None and b is not None:
            return a + b
        if isinstance(node.op, ast.Mult) and a is not None:
            try:
                n = _const_int(node.right)
            except ValueError:
                return None
            return a * n if 0 <= n < 64 else None
        if isinstance(node.op, ast.Mod) and a is not None:
            vals = node.right.elts if isinstance(node.right, ast.Tuple) else [node.right]
            folded = [_fold(v, env) for v in vals]
            if all(f is not None for f in folded):
                try:
                    return a % tuple(folded)
                except (TypeError, ValueError):
                    return None
        return None
    if isinstance(node, ast.JoinedStr):
        out = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                out.append(v.value)
            elif isinstance(v, ast.FormattedValue) and not v.format_spec:
                f = _fold(v.value, env)
                if f is None:
                    return None
                out.append(f)
            else:
                return None
        return "".join(out)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        base = _fold(node.func.value, env)
        if base is None:
            return None
        if node.func.attr == "join" and len(node.args) == 1 and isinstance(node.args[0], (ast.List, ast.Tuple)):
            parts = [_fold(e, env) for e in node.args[0].elts]
            return base.join(parts) if all(p is not None for p in parts) else None
        if node.func.attr == "format" and not node.keywords:
            args = [_fold(a, env) for a in node.args]
            if all(a is not None for a in args):
                try:
                    return base.format(*args)
                except (IndexError, KeyError, ValueError):
                    return None
        if node.func.attr in ("lower", "upper", "strip") and not node.args:
            return getattr(base, node.func.attr)()
        return None
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
        base = _fold(node.value, env)
        if base is None:
            return None
        try:
            idx = [None if x is None else _const_int(x) for x in (node.slice.lower, node.slice.upper, node.slice.step)]
            return base[slice(*idx)]
        except ValueError:
            return None
    return None


def _string_env(tree):
    """Names assigned a statically known string anywhere in the code (flat:
    a conservative over-approximation, which only ever adds refusals), and
    names bound to getattr-like builtins (``f = getattr``)."""
    env, aliases = {}, {}
    for _ in range(3):  # a few rounds resolve chains such as a = "acc"; b = a + "ept"
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            val = _fold(node.value, env)
            for t in node.targets:
                if not isinstance(t, ast.Name):
                    continue
                if val is not None:
                    env[t.id] = val
                if isinstance(node.value, ast.Name) and (node.value.id in ALIASABLE or node.value.id in aliases):
                    aliases[t.id] = aliases.get(node.value.id, node.value.id)
                elif isinstance(node.value, ast.Attribute) and node.value.attr in ALIASABLE:
                    aliases[t.id] = node.value.attr
    return env, aliases


def scan(code):
    """Static scan. Returns ``{"flags": [...], "refused": [...], "syntax_error": str|None}``.

    Each flag is ``{"category", "what", "line"}``. ``refused`` entries block execution
    in every mode: approval attempts (direct, through getattr-like access with a
    statically known name, by naming F3's authority internals, or by importing
    ``AgentCore.diff.authority``/``approver``), and direct transaction control
    (undo/redo/clearUndos, open/commit/abort, App's set/closeActiveTransaction),
    including names built by string concatenation, f-strings, ``join``, ``%`` or
    ``format``. Dynamic attribute access with a name the scan cannot resolve,
    aliases of getattr-like builtins and introspection (closures, globals, gc)
    are flagged ("dynamic_attr", "introspection") and so refused in strict mode.

    The scan is advisory. Python cannot be confined by reading its source:
    ``exec`` of a string built at runtime, data read from a file, or C
    extensions all get past it. The hard guarantee against self-approval is
    that the worker running this code never holds the apply key (see
    ``AgentCore/diff/SECURITY.md``).
    """
    out = {"flags": [], "refused": [], "syntax_error": None}
    try:
        tree = ast.parse(code, "<exec_code>", "exec")
    except SyntaxError as e:
        out["syntax_error"] = f"{e.msg} (line {e.lineno})"
        return out
    seen, seen_r = set(), set()
    env, aliases = _string_env(tree)
    called = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}

    def flag(cat, what, line):
        key = (cat, what, line)
        if key not in seen:
            seen.add(key)
            out["flags"].append({"category": cat, "what": what, "line": line})

    def refuse(what, line, reason):
        key = (what, line)
        if key not in seen_r:
            seen_r.add(key)
            out["refused"].append({"what": what, "line": line, "reason": reason})

    def why(name):
        if name in TRANSACTION_ATTRS:
            return "the change owns the transaction; use begin_change/propose_change"
        return "approval is human-only: only the human-side approver holds the apply key"

    def refused_module(m):
        return any(m == r or m.startswith(r + ".") for r in REFUSED_MODULES)

    def check_module(mod, line, names=()):
        for m in [mod] + [f"{mod}.{n}" for n in names]:
            if refused_module(m):
                refuse("import " + m, line, "importing F3's apply authority is an approval attempt")
        if mod.startswith("AgentCore.diff"):
            for n in names:
                if n in APPROVAL_ATTRS or n in AUTHORITY_NAMES:
                    refuse("import " + n, line, "approval is human-only")
        top = mod.split(".")[0]
        if top in FLAG_MODULES:
            flag(FLAG_MODULES[top], ("import " if not names else "from ") + mod + ("" if not names else " import"),
                 line)
        if top in INTROSPECTION_MODULES:
            flag("introspection", "import " + mod, line)

    def check_string(s, what, line, names=SENSITIVE):
        if s is None:
            return False
        if s in names:
            refuse(f"{what} -> {s!r}", line, why(s))
            return True
        if refused_module(s):
            refuse(f"{what} -> {s!r}", line, "importing F3's apply authority is an approval attempt")
            return True
        return False

    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Import):
            for a in node.names:
                check_module(a.name, line)
        elif isinstance(node, ast.ImportFrom):
            check_module(node.module or "", line, [a.name for a in node.names])
        elif isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else "")
            real = aliases.get(name, name) if isinstance(fn, ast.Name) else name
            if real in DYNAMIC_ATTR_FUNCS or real in DYNAMIC_ATTR_METHODS:
                # getattr(obj, name) and object.__getattribute__(obj, name): args[1]; obj.__getattribute__(name): args[0]
                idx = 1 if (real in DYNAMIC_ATTR_FUNCS or len(node.args) >= 2) else 0
                arg = node.args[idx] if len(node.args) > idx else None
                s = _fold(arg, env) if arg is not None else None
                if s is None:
                    flag("dynamic_attr", f"{real}() with a computed attribute name", line)
                else:
                    check_string(s, f"{real}()", line)
            elif real in ("__import__", "import_module"):
                s = _fold(node.args[0], env) if node.args else None
                if not check_string(s, f"{real}()", line):
                    flag("dynamic", f"{real}()", line)
            if name in APPROVAL_ATTRS or name in AUTHORITY_NAMES or name in TRANSACTION_ATTRS:
                refuse(_dotted(fn) or name, line, why(name))
            elif name in FLAG_CALLS:
                flag(FLAG_CALLS[name], (_dotted(fn) or name) + "()", line)
            if real in ("vars", "globals", "locals"):
                flag("introspection", f"{name}()", line)
            for a in list(node.args) + [k.value for k in node.keywords]:
                # an approval or authority name passed as data (f = getattr; f(turn, "accept"))
                check_string(_fold(a, env), "string argument", line, APPROVAL_ATTRS | AUTHORITY_NAMES)
        elif isinstance(node, ast.Subscript):
            if not isinstance(node.slice, ast.Slice):
                check_string(_fold(node.slice, env), "subscript", line)
        elif isinstance(node, ast.Attribute):
            if node.attr in AUTHORITY_NAMES:
                refuse(_dotted(node), line, why(node.attr))
            elif node.attr in APPROVAL_ATTRS and not isinstance(node.ctx, ast.Load):
                refuse(_dotted(node), line, "approval is human-only")
            if node.attr in INTROSPECTION_ATTRS:
                flag("introspection", _dotted(node) or node.attr, line)
            if node.attr in ALIASABLE and id(node) not in called:
                flag("dynamic_attr", f"{_dotted(node)} used as a value", line)
        elif isinstance(node, ast.Name):
            if node.id in ("apply_token", "APPLY_TOKEN", "_apply_token") or node.id in AUTHORITY_NAMES:
                refuse(node.id, line, "approval is human-only")
            elif node.id in ALIASABLE and isinstance(node.ctx, ast.Load) and id(node) not in called:
                flag("dynamic_attr", f"{node.id} used as a value (alias)", line)
        elif isinstance(node, (ast.BinOp, ast.JoinedStr)):
            check_string(_fold(node, env), "built string", line)
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
