# SPDX-License-Identifier: LGPL-2.1-or-later
"""F1: every fixture dump validates against state/schema.json."""

import copy
import json
import os
import sys
import unittest

import FreeCAD as App

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "state")
if FIX not in sys.path:
    sys.path.insert(0, FIX)

import builders  # noqa: E402
from AgentCore import state  # noqa: E402
from AgentCore.state import validate as v  # noqa: E402


class TestStateSchema(unittest.TestCase):
    def test_schema_file(self):
        s = state.load_schema()
        self.assertEqual(s["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(s["properties"]["schema"]["const"], state.SCHEMA_VERSION)
        self.assertEqual(state.SCHEMA_VERSION, "agentcore.state/1")
        # every $ref resolves
        refs = []

        def walk(x):
            if isinstance(x, dict):
                if "$ref" in x:
                    refs.append(x["$ref"])
                for y in x.values():
                    walk(y)
            elif isinstance(x, list):
                for y in x:
                    walk(y)
        walk(s)
        self.assertTrue(refs)
        for r in refs:
            self.assertIn(r.split("/")[-1], s["$defs"], r)

    def test_validator_rejects(self):
        doc = builders.build_sketch_states("f1_schema_neg")
        try:
            d = state.dump(doc)
            self.assertEqual(state.validate_dump(d), [])
            bad = copy.deepcopy(d)
            bad["schema"] = "agentcore.state/0"
            self.assertTrue(state.validate_dump(bad))
            bad = copy.deepcopy(d)
            del bad["objects"][0]["kind"]
            self.assertTrue(any("kind" in e for e in state.validate_dump(bad)))
            bad = copy.deepcopy(d)
            bad["objects"][0]["detail"]["solver"]["dof"] = "three"
            self.assertTrue(state.validate_dump(bad))
            bad = copy.deepcopy(d)
            bad["paging"]["extra"] = 1
            self.assertTrue(state.validate_dump(bad))
        finally:
            App.closeDocument(doc.Name)
        self.assertTrue(v.validate(True, {"type": "integer"}))
        self.assertFalse(v.validate(3, {"type": "number"}))

    def test_every_fixture_validates(self):
        try:
            import jsonschema  # noqa: F401
        except ImportError:
            jsonschema = None
        for key in builders.ALL:
            with self.subTest(fixture=key):
                doc = builders.open_fixture(key)
                try:
                    d = state.dump(doc)
                    json.dumps(d)  # pure JSON
                    errs = state.validate_dump(d)
                    self.assertEqual(errs, [], "%s: %s" % (key, errs[:5]))
                    for p in state.dump_all_pages(doc, 5):
                        self.assertEqual(state.validate_dump(p), [])
                    if jsonschema is not None:
                        jsonschema.validate(d, state.load_schema())
                finally:
                    App.closeDocument(doc.Name)
