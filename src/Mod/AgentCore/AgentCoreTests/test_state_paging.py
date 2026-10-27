# SPDX-License-Identifier: LGPL-2.1-or-later
"""F1: paged reads concatenate to the unpaged read."""

import os
import sys
import unittest

import FreeCAD as App

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "state")
if FIX not in sys.path:
    sys.path.insert(0, FIX)

import builders  # noqa: E402
from AgentCore import state  # noqa: E402


class TestStatePaging(unittest.TestCase):
    def test_paged_equals_unpaged(self):
        for key in builders.ALL:
            doc = builders.open_fixture(key)
            try:
                full = state.dump(doc)
                for size in (1, 3, 7, 50, 1000):
                    with self.subTest(fixture=key, page_size=size):
                        pages = state.dump_all_pages(doc, size)
                        objs = [o for p in pages for o in p["objects"]]
                        self.assertEqual(objs, full["objects"])
                        self.assertEqual(len(pages), pages[0]["paging"]["pages"])
                        for i, p in enumerate(pages):
                            self.assertEqual(p["paging"]["page"], i)
                            self.assertEqual(p["paging"]["total"], len(full["objects"]))
                            self.assertLessEqual(len(p["objects"]), size)
                            for k in ("schema", "generator", "document"):
                                self.assertEqual(p[k], full[k])
            finally:
                App.closeDocument(doc.Name)

    def test_bounds(self):
        doc = builders.build_sketch_states("f1_paging_bounds")
        try:
            self.assertEqual(state.dump(doc, page=99, page_size=2)["objects"], [])
            self.assertEqual(state.dump(doc, page=0)["paging"]["page_size"], 50)
            with self.assertRaises(ValueError):
                state.dump(doc, page=-1, page_size=2)
            with self.assertRaises(ValueError):
                state.dump(doc, page=0, page_size=0)
        finally:
            App.closeDocument(doc.Name)
