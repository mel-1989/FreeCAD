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
"""

import json
import os
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


def check_source(source, *, ignore_rules=(), timeout=120):
    """Run pyright on ``source``. ``{"ok", "errors": [...], "warnings", "seconds"}``.

    ``ok`` means zero error-severity diagnostics outside ``ignore_rules``. Each error is
    ``{"line", "rule", "message"}`` (1-based line). ``{"ok": False, "unavailable": True}`` when
    pyright cannot be found, so a missing tool never reads as a pass.
    """
    exe, py = find_pyright()
    if exe is None:
        return {"ok": False, "unavailable": True, "errors": [], "warnings": 0, "seconds": 0.0}
    with tempfile.TemporaryDirectory(prefix="agentcore-pyright-") as tmp:
        path = os.path.join(tmp, "macro_under_check.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(source)
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
