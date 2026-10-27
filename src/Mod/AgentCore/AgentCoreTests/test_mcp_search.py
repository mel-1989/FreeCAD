# SPDX-License-Identifier: LGPL-2.1-or-later
"""F4: search_parts over a local corpus."""

import os
import tempfile
import unittest

import FreeCAD

from AgentCoreTests.fixtures.mcp import mcp_common as mc
from AgentCore.mcp.search import PartsIndex, tokens


class TestSearch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="mcp_parts_")
        d = cls.tmp.name
        cls.paths = mc.save_fixtures(["box", "pair_overlap", "tube"], d)
        doc = FreeCAD.newDocument("McpWasher")
        c = doc.addObject("Part::Cylinder", "WasherBody")
        c.Label = "M6 washer"
        doc.recompute()
        import Import

        Import.export([c], os.path.join(d, "m6_washer.step"))
        c.Shape.exportBrep(os.path.join(d, "spacer.brep"))
        FreeCAD.closeDocument(doc.Name)
        cls.index = PartsIndex([d])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_tokens(self):
        self.assertIn("washer", tokens("M6WasherBody"))
        self.assertIn("6", tokens("m6_washer"))

    def test_index_formats(self):
        out = self.index.search("", limit=50)
        self.assertEqual(out["indexed"], 5)
        self.assertEqual(sorted({r["format"] for r in out["results"]}), ["BREP", "FCStd", "STEP"])

    def test_search_by_type_label_and_name(self):
        r = self.index.search("tube cylinder")["results"]
        self.assertTrue(r[0]["path"].endswith("tube.FCStd"), r)
        r = self.index.search("washer")["results"]
        self.assertTrue(r[0]["path"].endswith("m6_washer.step"), r)
        r = self.index.search("spacer", formats=["brep"])["results"]
        self.assertEqual(len(r), 1)
        self.assertEqual(self.index.search("zzzz")["results"], [])

    def test_fcstd_objects_read_without_freecad(self):
        self.index.refresh()
        e = next(e for e in self.index.entries if e["path"].endswith("pair_overlap.FCStd"))
        self.assertEqual(sorted(o["name"] for o in e["objects"]), ["A", "B"])
        self.assertTrue(all(o["type"] == "Part::Box" for o in e["objects"]))


if __name__ == "__main__":
    unittest.main()
