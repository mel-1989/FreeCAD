# SPDX-License-Identifier: LGPL-2.1-or-later
"""F4: API reference generated from the .pyi stubs."""

import json
import os
import tempfile
import unittest

from AgentCore.mcp import apidocs


class TestApiDocs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = apidocs.default_stub_root()
        if cls.src is None:
            raise unittest.SkipTest("no FreeCAD src/ tree beside the package")
        with open(apidocs.PACKAGED, encoding="utf-8") as fh:
            cls.packaged = json.load(fh)

    def test_parse_document_object_stub(self):
        classes = apidocs.parse_stub(os.path.join(self.src, "App", "DocumentObject.pyi"), "App")
        c = next(c for c in classes if c["name"] == "DocumentObject")
        self.assertEqual(c["bases"], ["ExtensionContainer"])
        methods = {m["name"]: m for m in c["methods"]}
        for name in ("recompute", "getSubObject", "touch", "isValid"):
            self.assertIn(name, methods)
        attrs = {a["name"]: a for a in c["attributes"]}
        self.assertTrue(attrs["OutList"]["readonly"])
        self.assertIn("links to", attrs["OutList"]["doc"])

    def test_packaged_index_is_current_with_stub_tree(self):
        self.assertEqual(self.packaged["schema"], apidocs.API_SCHEMA)
        self.assertEqual(self.packaged["source_hash"], apidocs.tree_hash(self.src),
                         "api_docs.json is stale: run python -m AgentCore.mcp.apidocs")
        self.assertEqual(self.packaged["errors"], [])
        self.assertEqual(self.packaged["stub_files"], len(apidocs.stub_files(self.src)))
        self.assertGreater(self.packaged["class_count"], 300)
        self.assertGreater(self.packaged["method_count"], 2000)

    def test_generate_is_deterministic_and_cacheable(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = os.path.join(d, "a.json"), os.path.join(d, "b.json")
            apidocs.write(apidocs.generate(self.src), a)
            apidocs.write(apidocs.generate(self.src), b)
            with open(a, "rb") as fa, open(b, "rb") as fb, open(apidocs.PACKAGED, "rb") as fp:
                da = fa.read()
                self.assertEqual(da, fb.read())
                self.assertEqual(da, fp.read())

    def test_markdown(self):
        idx = apidocs.index_markdown(self.packaged)
        self.assertIn("cadfork://api/Mod.Part.App/TopoShape", idx)
        md = apidocs.class_markdown(self.packaged["classes"]["Mod.Part.App/TopoShape"])
        self.assertIn("exportBrep", md)


if __name__ == "__main__":
    unittest.main()
