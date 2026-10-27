# SPDX-License-Identifier: LGPL-2.1-or-later
"""F6 refactor ops over three scripted golden models (skipped when the control repo's models are absent).

The full corpus run (about 200 documents, real and scripted) is fixtures/refactor/corpus_run.py; this is its small in-process cousin.
"""

import importlib.util
import os
import sys
import unittest

import FreeCAD

from AgentCore import refactor

CTL = os.path.expanduser(os.environ.get("CADFORK_CTL", "~/_agents/repos/app development/cad-fork"))
MODELS = os.path.join(CTL, "fixtures", "golden", "models")
NAMES = ("m04_fillet", "m10_linear_pattern", "m13_bracket")


def _load(name):
    if MODELS not in sys.path:
        sys.path.insert(0, MODELS)
    spec = importlib.util.spec_from_file_location(name, os.path.join(MODELS, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ALLOW_MISSING = os.environ.get("CADFORK_ALLOW_MISSING_CORPUS") == "1"


class TestCorpusPresent(unittest.TestCase):
    def test_control_repo_corpus_exists(self):
        # A missing corpus must never read as a pass: only CADFORK_ALLOW_MISSING_CORPUS=1 may skip it.
        if os.path.isdir(MODELS):
            return
        if ALLOW_MISSING:
            self.skipTest("control repo golden models not found (CADFORK_ALLOW_MISSING_CORPUS=1)")
        self.fail(f"control repo corpus not found at {MODELS}; set CADFORK_CTL, or CADFORK_ALLOW_MISSING_CORPUS=1 to skip")


@unittest.skipUnless(os.path.isdir(MODELS), "control repo golden models not found (reported by TestCorpusPresent)")
class TestScriptedModels(unittest.TestCase):
    def build(self, name):
        helpers, mod = _load("_helpers"), _load(name)
        doc = FreeCAD.newDocument(name)
        mod.build(doc, dict(mod.PARAMS), helpers)
        doc.recompute(None, True)
        return doc

    def test_every_op_is_invariant(self):
        ops = {
            "relabel": refactor.relabel_all,
            "lift_varset": lambda d: refactor.lift_all(d, target="VarSet"),
            "lift_sheet": lambda d: refactor.lift_all(d, target="Spreadsheet"),
            "across_bodies": refactor.across_all_bodies,
            "merge": refactor.merge_redundant,
        }
        for name in NAMES:
            for op, fn in ops.items():
                doc = self.build(name)
                try:
                    r = refactor.invariant(doc, fn)
                    self.assertTrue(r["ok"], (name, op, r["violations"], r["error"]))
                finally:
                    FreeCAD.closeDocument(doc.Name)


if __name__ == "__main__":
    unittest.main()
