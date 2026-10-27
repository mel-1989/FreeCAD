# SPDX-License-Identifier: LGPL-2.1-or-later
"""Load the tolerance policy from tolerances.json (default merged with per-OS overrides)."""

import json
import os
import platform

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tolerances.json")
_cache = None


def load(path=None):
    """Return the effective tolerance dict for this OS."""
    global _cache
    if path is None and _cache is not None:
        return dict(_cache)
    with open(path or _PATH, encoding="utf-8") as fh:
        raw = json.load(fh)
    tol = dict(raw.get("default", {}))
    tol.update(raw.get("per_os", {}).get(platform.system(), {}))
    if path is None:
        _cache = tol
    return dict(tol)


def resolve(params=None):
    """Tolerances overlaid with any per-call ``tol`` dict in a check's params."""
    tol = load()
    if params and isinstance(params.get("tol"), dict):
        tol.update(params["tol"])
    return tol
