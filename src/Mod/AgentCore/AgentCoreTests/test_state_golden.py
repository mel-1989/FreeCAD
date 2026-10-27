# SPDX-License-Identifier: LGPL-2.1-or-later
"""F1: golden state snapshots of fixture documents.

Compared as JSON equality modulo ordering (lists of named records are
compared as sets keyed by name) with a 1e-6 relative float tolerance. Set
AGENTCORE_UPDATE_GOLDEN=1 to rewrite the snapshots.
"""

import json
import math
import os
import sys
import unittest

import FreeCAD as App

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "state")
GOLDEN = os.path.join(FIX, "golden")
if FIX not in sys.path:
    sys.path.insert(0, FIX)

import builders  # noqa: E402
from AgentCore import state  # noqa: E402

IGNORE_TOP = {"generator"}


def canonical(x):
    if isinstance(x, dict):
        return {k: canonical(v) for k, v in sorted(x.items())}
    if isinstance(x, list):
        items = [canonical(v) for v in x]
        if items and all(isinstance(v, dict) and "name" in v for v in items):
            return sorted(items, key=lambda v: str(v["name"]))
        return items
    return x


def diff(a, b, path="", out=None):
    out = [] if out is None else out
    if len(out) > 20:
        return out
    if isinstance(a, bool) or isinstance(b, bool) or not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
        if type(a) is not type(b):
            out.append(f"{path}: {a!r} != {b!r}")
            return out
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{path}/{k}: missing on one side")
            else:
                diff(a[k], b[k], f"{path}/{k}", out)
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append(f"{path}: length {len(a)} != {len(b)}")
        else:
            for i, (u, w) in enumerate(zip(a, b)):
                diff(u, w, f"{path}/{i}", out)
    elif isinstance(a, (int, float)) and not isinstance(a, bool):
        if not math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-6):
            out.append(f"{path}: {a} != {b}")
    elif a != b:
        out.append(f"{path}: {a!r} != {b!r}")
    return out


def snapshot(doc):
    d = state.dump(doc)
    return canonical({k: v for k, v in d.items() if k not in IGNORE_TOP})


class TestStateGolden(unittest.TestCase):
    def test_built_fixtures_are_deterministic(self):
        for key in builders.BUILT:
            with self.subTest(fixture=key):
                a_doc = builders.open_fixture(key)
                a = snapshot(a_doc)
                App.closeDocument(a_doc.Name)
                b_doc = builders.open_fixture(key)
                b = snapshot(b_doc)
                App.closeDocument(b_doc.Name)
                self.assertEqual(diff(a, b), [])

    def test_golden_snapshots(self):
        update = os.environ.get("AGENTCORE_UPDATE_GOLDEN") == "1"
        self.assertGreaterEqual(len(builders.ALL), 6)
        for key in builders.ALL:
            with self.subTest(fixture=key):
                doc = builders.open_fixture(key)
                try:
                    snap = snapshot(doc)
                finally:
                    App.closeDocument(doc.Name)
                path = os.path.join(GOLDEN, key + ".json")
                if update or not os.path.exists(path):
                    if not update:
                        self.fail("golden missing: %s (run with AGENTCORE_UPDATE_GOLDEN=1)" % path)
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(snap, f, indent=1, sort_keys=True)
                        f.write("\n")
                    continue
                with open(path, encoding="utf-8") as f:
                    gold = json.load(f)
                problems = diff(gold, json.loads(json.dumps(snap)))
                self.assertEqual(problems, [], key)
