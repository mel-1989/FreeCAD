# SPDX-License-Identifier: LGPL-2.1-or-later
"""Static grounding (F6): run pyright against ``freecad-stubs`` before a macro executes.

An invented module-level function (``Part.makeBoxx``) or method on a typed object
(``doc.addObjectt``) is an ``reportAttributeAccessIssue`` error in pyright, so the macro is
rejected before ``exec``. Limits, measured in ``test_refactor_grounding.py``: attributes read
or written on a generic ``DocumentObject`` (``obj.Lenght = 4``) are dynamic in FreeCAD and
cannot be checked statically, and a real API that needs the GUI still passes pyright and is
only caught by the headless run.

The pyright binary comes from ``$CADFORK_PYRIGHT``, else the pixi environment of the main
clone (``~/src/cad-fork/freecad/.pixi/envs/default/bin/pyright``), else ``PATH``. It runs
offline (no node download) in that environment.

Why the stubs are patched: the generated ``freecad-stubs`` write ``import Part as PartModule`` and
then ``import PartModule.Geom2d``, which rebinds ``PartModule`` to a module that does not exist, so
every class that derives from ``PartModule.Shape`` has an unknown base and pyright accepts any
attribute on it (``Part.makeBox(1, 2, 3).Volumee`` passed). ``patched_stub_path`` writes a corrected copy
of each affected stub package (``import Part.Geom2d``, ``Part.Geom2d.X``) into a cache directory
and points pyright's ``stubPath`` at it, with ``reportAttributeAccessIssue`` as an error. Pyright
still cannot type everything, so ``introspect_source`` is a second check that resolves attribute
chains on values built from literal-argument FreeCAD constructors against ``dir()`` of the real objects.
"""

import ast
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile

_PIXI = os.path.expanduser("~/src/cad-fork/freecad/.pixi/envs/default")


class GroundingError(Exception):
    """Raised by ``grounded_exec`` when pyright rejects the source."""

    def __init__(self, result):
        self.result = result
        first = result["errors"][0] if result["errors"] else {}
        super().__init__(f"pyright rejected the macro ({len(result['errors'])} errors): "
                         f"line {first.get('line')}: {first.get('message')}")


def find_pyright():
    """``(pyright path, python path)`` or ``(None, None)`` when pyright is not installed."""
    exe = os.environ.get("CADFORK_PYRIGHT")
    if exe:
        if not os.path.exists(exe):
            return None, None
    else:
        cand = os.path.join(_PIXI, "bin", "pyright")
        exe = cand if os.path.exists(cand) else shutil.which("pyright")
    if not exe:
        return None, None
    py = os.environ.get("CADFORK_PYRIGHT_PYTHON") or os.path.join(os.path.dirname(exe), "python")
    return exe, py if os.path.exists(py) else None


_BROKEN_IMPORT = re.compile(r"^import (\w+)Module\.(\w+)[ \t]*$", re.M)


def _site_packages(exe):
    root = os.path.dirname(os.path.dirname(os.path.realpath(exe)))
    return sorted(glob.glob(os.path.join(root, "lib", "python3*", "site-packages")))


def patched_stub_path(exe):
    """A directory of corrected copies of the stub packages whose ``import XModule.sub`` is broken.

    Returns ``None`` when there is nothing to patch. Built once per stubs state, atomically, in the
    system temp directory.
    """
    packages = []
    for sp in _site_packages(exe):
        packages += sorted(glob.glob(os.path.join(sp, "*-stubs")))
    if not packages:
        return None
    key = hashlib.sha1("|".join(f"{d}:{os.stat(d).st_mtime_ns}" for d in packages).encode() + b"v1").hexdigest()[:12]
    final = os.path.join(tempfile.gettempdir(), f"agentcore-stubs-{key}")
    if os.path.isdir(final):
        return final
    building = tempfile.mkdtemp(prefix="agentcore-stubs-build-")
    patched_any = False
    for pkg in packages:
        name = os.path.basename(pkg)[: -len("-stubs")]
        for root, _, files in os.walk(pkg):
            for fname in files:
                if not fname.endswith(".pyi"):
                    continue
                with open(os.path.join(root, fname), encoding="utf-8") as fh:
                    text = fh.read()
                for mod, sub in set(_BROKEN_IMPORT.findall(text)):
                    text = text.replace(f"import {mod}Module.{sub}", f"import {mod}.{sub}")
                    text = text.replace(f"{mod}Module.{sub}", f"{mod}.{sub}")
                    patched_any = True
                dest = os.path.join(building, name, os.path.relpath(os.path.join(root, fname), pkg))
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "w", encoding="utf-8") as fh:
                    fh.write(text)
    if not patched_any:
        shutil.rmtree(building, ignore_errors=True)
        return None
    try:
        os.rename(building, final)
    except OSError:                       # another process won the race
        shutil.rmtree(building, ignore_errors=True)
    return final


# Constructors whose result is safe to build while checking (pure, no document, no GUI) and whose
# arguments must all be literals.
_SAFE_CONSTRUCTORS = {
    "Part": ("make", "Vertex", "Point", "LineSegment", "Line", "Circle", "Ellipse", "Plane", "Cylinder", "Cone",
             "Sphere", "Face", "Wire", "Edge", "Shell", "Solid"),
    "FreeCAD": ("Vector", "Placement", "Rotation", "Matrix", "BoundBox"),
}


def introspect_source(source):
    """Check attribute chains on real FreeCAD objects against ``dir()``; errors in pyright's format.

    Handles names bound at module level to a safe constructor call with literal arguments
    (``s = Part.makeBox(1, 2, 3)``) and chains that start at such a call or name. Anything else is
    left to pyright. Returns a list of ``{"line", "rule": "introspection", "message"}``; empty when
    FreeCAD cannot be imported.
    """
    try:
        import FreeCAD
        import Part
    except ImportError:
        return []
    modules = {"FreeCAD": FreeCAD, "App": FreeCAD, "Part": Part}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    aliases = {}                          # local module alias -> real module name
    for node in tree.body:
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name in modules:
                    aliases[a.asname or a.name] = "FreeCAD" if a.name == "App" else a.name
    env, errors = {}, []

    def literal_args(call):
        try:
            return ([ast.literal_eval(a) for a in call.args],
                    {k.arg: ast.literal_eval(k.value) for k in call.keywords if k.arg})
        except (ValueError, TypeError, SyntaxError):
            return None

    def resolve(node):
        """The real object ``node`` evaluates to, or ``None`` when unknown. Records invented attributes."""
        if isinstance(node, ast.Name):
            return env.get(node.id)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            mod = aliases.get(node.func.value.id)
            attr = node.func.attr
            if mod and any(attr.startswith("make") if p == "make" else attr == p for p in _SAFE_CONSTRUCTORS.get(mod, ())):
                args = literal_args(node)
                if args is not None and hasattr(modules[mod], attr):
                    try:
                        return getattr(modules[mod], attr)(*args[0], **args[1])
                    except Exception:
                        return None
            return None
        if isinstance(node, ast.Attribute):
            base = resolve(node.value)
            if base is None:
                return None
            if not hasattr(base, node.attr):
                errors.append({"line": node.lineno, "rule": "introspection",
                               "message": f'"{node.attr}" is not an attribute of {type(base).__module__}.{type(base).__name__}; '
                                          f'it has: {", ".join(sorted(n for n in dir(base) if not n.startswith("_"))[:12])} ...'})
                return None
            try:
                return getattr(base, node.attr)
            except Exception:
                return None
        return None

    stores = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            stores[node.id] = stores.get(node.id, 0) + 1
        elif isinstance(node, ast.arg):
            stores[node.arg] = stores.get(node.arg, 0) + 1         # a parameter shadows a module-level name

    def chains(stmt):
        """Attribute nodes in ``stmt`` outside any function, lambda or class body."""
        stack = [stmt]
        while stack:
            n = stack.pop()
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                continue
            if isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Load):
                yield n
            stack.extend(ast.iter_child_nodes(n))

    for stmt in tree.body:
        for attr in chains(stmt):
            resolve(attr)
        if (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name)
                and stores.get(stmt.targets[0].id) == 1 and isinstance(stmt.value, (ast.Call, ast.Name, ast.Attribute))):
            val = resolve(stmt.value)
            if val is not None:
                env[stmt.targets[0].id] = val
    unique = {(e["line"], e["message"]): e for e in errors}
    return sorted(unique.values(), key=lambda e: e["line"])


def check_source(source, *, ignore_rules=(), timeout=120):
    """Run pyright on ``source``. ``{"ok", "errors": [...], "warnings", "seconds"}``.

    ``ok`` means zero error-severity diagnostics outside ``ignore_rules``. Each error is
    ``{"line", "rule", "message"}`` (1-based line). ``{"ok": False, "unavailable": True}`` when
    pyright cannot be found, so a missing tool never reads as a pass.
    """
    exe, py = find_pyright()
    if exe is None:
        return {"ok": False, "unavailable": True, "errors": [], "warnings": 0, "seconds": 0.0}
    stubs = patched_stub_path(exe)
    with tempfile.TemporaryDirectory(prefix="agentcore-pyright-") as tmp:
        path = os.path.join(tmp, "macro_under_check.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(source)
        config = {"reportAttributeAccessIssue": "error"}
        if stubs:
            config["stubPath"] = stubs
        with open(os.path.join(tmp, "pyrightconfig.json"), "w", encoding="utf-8") as fh:
            json.dump(config, fh)
        cmd = [exe, "--outputjson", path]
        if py:
            cmd[1:1] = ["--pythonpath", py]
        proc = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    try:
        report = json.loads(proc.stdout)
    except ValueError:
        return {"ok": False, "errors": [{"line": 0, "rule": "pyright", "message": (proc.stdout + proc.stderr)[-400:]}],
                "warnings": 0, "seconds": 0.0}
    errors, warnings = [], 0
    for d in report.get("generalDiagnostics", []):
        if d["severity"] == "error":
            if d.get("rule") in ignore_rules:
                continue
            errors.append({"line": d["range"]["start"]["line"] + 1, "rule": d.get("rule", ""), "message": d["message"]})
        elif d["severity"] == "warning":
            warnings += 1
    errors += [e for e in introspect_source(source) if e["rule"] not in ignore_rules]
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "seconds": float(report.get("summary", {}).get("timeInSec", 0.0))}


def grounded_exec(source, namespace=None, *, ignore_rules=()):
    """Reject ``source`` with ``GroundingError`` if pyright finds an error, else ``exec`` it."""
    res = check_source(source, ignore_rules=ignore_rules)
    if not res["ok"]:
        raise GroundingError(res)
    ns = namespace if namespace is not None else {"__name__": "__main__"}
    exec(compile(source, "<grounded macro>", "exec"), ns)
    return ns
