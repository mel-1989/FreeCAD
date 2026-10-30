# SPDX-License-Identifier: LGPL-2.1-or-later
"""F4: MCP JSON-RPC layer, tool listing and resources (no FreeCAD in the server)."""

import json
import os
import shutil
import subprocess
import sys
import unittest

from AgentCoreTests.fixtures.mcp import mcp_common as mc
from AgentCore.mcp import server as srv


def rpc(s, method, params=None, rid=1):
    msg = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        msg["params"] = params
    return s.handle(msg)


class TestProtocol(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = mc.make_server(stub=False)

    @classmethod
    def tearDownClass(cls):
        cls.s.close()

    def test_initialize_negotiates_version(self):
        for asked, got in (("2025-06-18", "2025-06-18"), ("2025-11-25", "2025-11-25"), ("2024-11-05", "2024-11-05"),
                           ("2026-07-28", "2025-11-25"), ("1999-01-01", "2025-11-25")):
            r = rpc(self.s, "initialize", {"protocolVersion": asked, "capabilities": {},
                                           "clientInfo": {"name": "t", "version": "0"}})["result"]
            self.assertEqual(r["protocolVersion"], got)
            self.assertIn("tools", r["capabilities"])
            self.assertIn("resources", r["capabilities"])
            self.assertEqual(r["serverInfo"]["name"], "cadfork-mcp")

    def test_discover_is_method_not_found_so_modern_clients_fall_back(self):
        r = rpc(self.s, "server/discover", {})
        self.assertEqual(r["error"]["code"], srv.METHOD_NOT_FOUND)

    def test_notifications_get_no_response(self):
        self.assertIsNone(self.s.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertIsNone(self.s.handle({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 3}}))

    def test_bad_messages(self):
        self.assertEqual(self.s.handle({"id": 1, "method": "ping"})["error"]["code"], srv.INVALID_REQUEST)
        self.assertEqual(rpc(self.s, "ping")["result"], {})
        self.assertEqual(rpc(self.s, "nope/nope")["error"]["code"], srv.METHOD_NOT_FOUND)

    def test_tool_list(self):
        tools = rpc(self.s, "tools/list")["result"]["tools"]
        names = [t["name"] for t in tools]
        self.assertEqual(names, ["list_documents", "open_document", "get_state", "describe_entity", "get_selection",
                                 "exec_code", "verify", "begin_change", "propose_change", "render", "import_export",
                                 "search_parts"])
        for t in tools:
            self.assertEqual(t["inputSchema"]["type"], "object")
            self.assertNotIn("outputSchema", t)
            self.assertFalse(any(w in t["name"] for w in ("approve", "accept", "apply")))

    def test_approve_tool_calls_are_rejected(self):
        for name in ("approve_change", "accept_change", "apply_change", "approve"):
            res = rpc(self.s, "tools/call", {"name": name, "arguments": {"proposal_id": "change-1"}})["result"]
            self.assertTrue(res["isError"], name)
            self.assertEqual(res["structuredContent"]["error"], "approval_refused")
        res = rpc(self.s, "tools/call", {"name": "propose_change", "arguments": {"approve": True}})["result"]
        self.assertTrue(res["isError"])
        self.assertEqual(res["structuredContent"]["error"], "approval_refused")

    def test_unknown_tool_and_args(self):
        res = rpc(self.s, "tools/call", {"name": "frobnicate", "arguments": {}})["result"]
        self.assertTrue(res["isError"])
        res = rpc(self.s, "tools/call", {"name": "get_state", "arguments": {"bogus": 1}})["result"]
        self.assertEqual(res["structuredContent"]["error"], "bad_argument")

    def test_tools_without_documents(self):
        out, err = mc.call(self.s, "get_state")
        self.assertTrue(err)
        self.assertEqual(out["error"], "no_document")
        out, err = mc.call(self.s, "render")
        self.assertTrue(err)
        self.assertIn(out["error"], ("no_document", "unavailable"))
        out, err = mc.call(self.s, "get_selection")
        self.assertFalse(err)
        self.assertEqual(out["selection"], [])
        out, err = mc.call(self.s, "list_documents")
        self.assertEqual(out["documents"], [])
        out, err = mc.call(self.s, "search_parts", query="box")
        self.assertFalse(err)
        self.assertIn("no parts roots", out["note"])

    def test_resources(self):
        res = rpc(self.s, "resources/list")["result"]["resources"]
        uris = {r["uri"] for r in res}
        self.assertTrue({"cadfork://cookbook", "cadfork://api/index", "cadfork://state/schema",
                         "cadfork://api/App/DocumentObject", "cadfork://api/Mod.Part.App/TopoShape"} <= uris)
        self.assertGreater(len(res), 300)
        cb = rpc(self.s, "resources/read", {"uri": "cadfork://cookbook"})["result"]["contents"][0]
        self.assertTrue(cb["text"].startswith("# cadfork-mcp cookbook"))
        do = rpc(self.s, "resources/read", {"uri": "cadfork://api/App/DocumentObject"})["result"]["contents"][0]["text"]
        self.assertIn("recompute", do)
        self.assertIn("getSubObject", do)
        schema = rpc(self.s, "resources/read", {"uri": "cadfork://state/schema"})["result"]["contents"][0]["text"]
        json.loads(schema)
        self.assertEqual(rpc(self.s, "resources/read", {"uri": "cadfork://api/No/Such"})["error"]["code"],
                         srv.RESOURCE_NOT_FOUND)
        tpl = rpc(self.s, "resources/templates/list")["result"]["resourceTemplates"]
        self.assertEqual(tpl[0]["uriTemplate"], "cadfork://api/{module}/{class}")

    def test_server_module_does_not_import_freecad(self):
        py = shutil.which("python3")
        if not py:
            self.skipTest("no python3 on PATH")
        code = ("import sys; sys.path.insert(0, %r); import AgentCore.mcp.server, AgentCore.mcp.workers; "
                "print('FreeCAD' in sys.modules)" % mc.AGENTCORE_SRC)
        out = subprocess.run([py, "-I", "-c", code], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        self.assertEqual(out.stdout.strip(), "False", out.stderr)

    def test_stdio_roundtrip_in_plain_python(self):
        py = shutil.which("python3")
        if not py:
            self.skipTest("no python3 on PATH")
        lines = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "render", "arguments": {}}},
        ]
        inp = "".join(json.dumps(m) + "\n" for m in lines) + "not json\n"
        env = dict(os.environ, PYTHONPATH=mc.AGENTCORE_SRC)
        out = subprocess.run([py, "-m", "AgentCore.mcp", "--freecadcmd", mc.freecadcmd()], input=inp,
                             capture_output=True, text=True, timeout=60, env=env)
        resps = [json.loads(x) for x in out.stdout.splitlines()]
        self.assertEqual([r.get("id") for r in resps], [1, 2, 3, None], out.stderr)
        self.assertEqual(resps[0]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(len(resps[1]["result"]["tools"]), 12)
        self.assertTrue(resps[2]["result"]["isError"])
        self.assertEqual(resps[3]["error"]["code"], srv.PARSE_ERROR)


if __name__ == "__main__":
    unittest.main()
