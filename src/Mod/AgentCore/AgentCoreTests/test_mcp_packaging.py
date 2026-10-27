# SPDX-License-Identifier: LGPL-2.1-or-later
"""F4: the uvx entry point builds, runs, and its workers import the packaged AgentCore."""

import json
import os
import shutil
import subprocess
import unittest

from AgentCoreTests.fixtures.mcp import mcp_common as mc

from AgentCore.paths import agentcore_source_dir  # noqa: E402

PKG = os.path.join(agentcore_source_dir(), "mcp-server")


class TestPackaging(unittest.TestCase):
    def setUp(self):
        self.uvx = shutil.which("uvx")
        if not self.uvx:
            self.skipTest("uvx not installed")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "VIRTUAL_ENV", "CONDA"))}

    def run_uvx(self, args, inp=None, cwd=None):
        # --refresh-package: rebuild from the current tree (uv keys local builds on pyproject, not on AgentCore)
        return subprocess.run([self.uvx, "--refresh-package", "cadfork-mcp", "--from", PKG, "cadfork-mcp"] + args, input=inp, capture_output=True,
                              text=True, timeout=600, env=self.env, cwd=cwd,
                              stdin=None if inp is not None else subprocess.DEVNULL)

    def test_entry_point(self):
        p = self.run_uvx(["--list-tools"])
        self.assertEqual(p.returncode, 0, p.stderr[-3000:])
        self.assertEqual(len(p.stdout.split()), 12)

    def test_packaged_server_drives_a_worker(self):
        with mc.tmpdir("mcp_pkg_") as d:
            path = mc.save_fixtures(["box"], d)["box"]
            msgs = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "open_document", "arguments": {"path": path}}},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "verify", "arguments": {"checks": ["validity"]}}},
                {"jsonrpc": "2.0", "id": 4, "method": "resources/read", "params": {"uri": "cadfork://api/App/Document"}},
            ]
            p = self.run_uvx(["--freecadcmd", mc.freecadcmd()], "".join(json.dumps(m) + "\n" for m in msgs), cwd=d)
        resps = {r["id"]: r for r in map(json.loads, p.stdout.splitlines())}
        self.assertEqual(sorted(resps), [1, 2, 3, 4], p.stderr[-3000:])
        opened = resps[2]["result"]["structuredContent"]
        self.assertFalse(resps[2]["result"]["isError"], opened)
        self.assertNotIn(mc.AGENTCORE_SRC, json.dumps(opened))
        self.assertTrue(resps[3]["result"]["structuredContent"]["ok"], resps[3])
        self.assertIn("Document", resps[4]["result"]["contents"][0]["text"])


if __name__ == "__main__":
    unittest.main()
