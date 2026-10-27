# SPDX-License-Identifier: LGPL-2.1-or-later
"""Grading for the AgentCore.state eval (stdlib only, no FreeCAD)."""

import json
import re

REL_TOL = 1e-3


def _num(x):
    if isinstance(x, bool):
        raise ValueError("bool is not a number")
    if isinstance(x, (int, float)):
        return float(x)
    m = re.search(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", str(x).replace(",", ""))
    if not m:
        raise ValueError("no number in %r" % (x,))
    return float(m.group(0))


def _norm(s):
    return re.sub(r"\s+", " ", str(s)).strip().strip("'\"`").lower()


def grade(kind, truth, answer):
    """True when ``answer`` matches ``truth`` for the given kind."""
    try:
        if kind == "number":
            a, t = _num(answer), float(truth)
            return abs(a - t) <= max(1e-6, REL_TOL * abs(t))
        if kind == "int":
            return int(round(_num(answer))) == int(truth) and abs(_num(answer) - round(_num(answer))) < 1e-9
        if kind == "bool":
            if isinstance(answer, bool):
                return answer == truth
            return _norm(answer) in ({"true", "yes"} if truth else {"false", "no"})
        if kind == "string":
            return _norm(answer) == _norm(truth)
        if kind == "set":
            if isinstance(answer, str):
                try:
                    answer = json.loads(answer)
                except ValueError:
                    answer = [x for x in re.split(r"[,\s]+", answer) if x]
            if not isinstance(answer, (list, tuple)):
                return False
            if truth and all(isinstance(t, int) for t in truth):
                return sorted(int(_num(a)) for a in answer) == sorted(truth)
            return sorted(_norm(a) for a in answer) == sorted(_norm(t) for t in truth)
    except (ValueError, TypeError):
        return False
    raise ValueError("unknown kind %r" % kind)
