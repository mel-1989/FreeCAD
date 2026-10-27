# SPDX-License-Identifier: LGPL-2.1-or-later
"""AgentCore.verify (F2): headless deterministic verify checks.

- ``run_checks(doc, checks=None, **opts)`` runs checks in-process.
- ``Pool(size, timeout).run(fcstd_path, checks)`` runs each check in an isolated
  FreeCADCmd worker with a timeout and crash capture.

A check spec is a name (``"validity"``) or ``{"check": name, **params}``.
"""

from .checks import CHECKS, DEFAULT_CHECKS, run_one
from .pool import Pool, find_freecadcmd


def run_checks(doc, checks=None, **opts):
    """Run checks on an open document; returns ``{"status", "ok", "results": [...]}``.

    Each result is ``{"check", "ok", "status", "entities": [ids], "details"}``.
    ``opts`` are merged into every check's params (e.g. ``objects=[...]``).
    """
    specs = DEFAULT_CHECKS if checks is None else checks
    results = []
    for spec in specs:
        if isinstance(spec, str):
            spec = {"check": spec}
        spec = {**opts, **spec}
        results.append(run_one(doc, spec))
    return {"status": "ok", "ok": all(r["ok"] for r in results), "results": results}


__all__ = ["run_checks", "Pool", "find_freecadcmd", "CHECKS", "DEFAULT_CHECKS"]
