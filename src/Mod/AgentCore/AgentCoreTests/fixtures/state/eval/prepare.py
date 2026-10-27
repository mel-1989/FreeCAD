# SPDX-License-Identifier: LGPL-2.1-or-later
"""Compute the eval's ground truth inside FreeCAD and write truth.json.

Run in a FreeCAD process (FreeCADCmd, or a Python that imports FreeCAD):
    exec(open(".../eval/prepare.py").read())
The truth comes from the FreeCAD API on each opened fixture document, never
from a state dump.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.environ["AGENTCORE_EVAL_DIR"]
sys.path[:0] = [HERE, os.path.dirname(HERE)]

import FreeCAD as App  # noqa: E402

import builders  # noqa: E402
import tasks  # noqa: E402


def compute_truth():
    out = {}
    by_fixture = {}
    for t in tasks.TASKS:
        by_fixture.setdefault(t["fixture"], []).append(t)
    for fixture, ts in sorted(by_fixture.items()):
        doc = builders.open_fixture(fixture)
        try:
            for t in ts:
                v = t["truth"](doc)
                if isinstance(v, float):
                    v = round(v, 6)
                out[t["id"]] = {"fixture": fixture, "question": t["question"], "kind": t["kind"], "truth": v}
        finally:
            App.closeDocument(doc.Name)
    return [dict(id=k, **out[k]) for k in [t["id"] for t in tasks.TASKS]]


if __name__ in ("__main__", "builtins") or os.environ.get("AGENTCORE_EVAL_WRITE") == "1":
    with open(os.path.join(HERE, "truth.json"), "w", encoding="utf-8") as f:
        json.dump(compute_truth(), f, indent=1)
        f.write("\n")
