# SPDX-License-Identifier: LGPL-2.1-or-later
"""F4: conformance with the official MCP Python SDK client.

Runs fixtures/mcp/mcp_sdk_client.py with `uv run --no-project --with mcp` (outside
FreeCAD); the client spawns the server over stdio, initializes (auto and legacy
modes), lists tools, calls every tool, and lists and reads resources. Skips
cleanly when uv is not installed. CADFORK_MCP_SDK_OFFLINE=1 adds --offline;
CADFORK_MCP_SDK_REPORT=<path> writes the client's summary there.
"""

import json
import os
import shutil
import subprocess
import unittest

from AgentCoreTests.fixtures.mcp import mcp_common as mc

CLIENT = os.path.join(mc.MCP_FIX, "mcp_sdk_client.py")


class TestSdkConformance(unittest.TestCase):
    def test_official_sdk_client(self):
        uv = shutil.which("uv")
        if not uv:
            self.skipTest("uv not installed")
        with mc.tmpdir("mcp_sdk_") as d:
            fixture = mc.save_fixtures(["box"], d)["box"]
            parts = os.path.join(d, "parts")
            os.makedirs(parts)
            mc.save_fixtures(["box", "tube"], parts)
            cfg = {"freecadcmd": mc.freecadcmd(), "fixture": fixture, "parts": parts, "out": d,
                   "env": {"PYTHONPATH": mc.AGENTCORE_SRC, "QT_QPA_PLATFORM": "offscreen",
                           "CADFORK_MCP_DIFF_MODULE": "mcp_stub_diff", "CADFORK_MCP_PYTHONPATH": mc.MCP_FIX,
                           "HOME": os.environ.get("HOME", ""), "PATH": os.environ.get("PATH", "")}}
            cfgp = os.path.join(d, "cfg.json")
            with open(cfgp, "w", encoding="utf-8") as fh:
                json.dump(cfg, fh)
            shutil.copy(CLIENT, os.path.join(d, "client_test.py"))
            cmd = [uv, "run", "--no-project", "--with", "mcp", "python", "client_test.py", cfgp]
            if os.environ.get("CADFORK_MCP_SDK_OFFLINE") == "1":
                cmd.insert(2, "--offline")
            env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "VIRTUAL_ENV", "CONDA"))}
            p = subprocess.run(cmd, cwd=d, capture_output=True, text=True, timeout=600, env=env,
                               stdin=subprocess.DEVNULL)
            line = next((x for x in p.stdout.splitlines() if x.startswith("MCP_SDK_SUMMARY ")), None)
            self.assertIsNotNone(line, f"rc={p.returncode}\nstdout={p.stdout[-3000:]}\nstderr={p.stderr[-5000:]}")
            summary = json.loads(line.split(" ", 1)[1])
            for x in ("auto", "legacy"):
                self.assertTrue(os.path.getsize(os.path.join(d, f"sdk-{x}.step")) > 100)
        if os.environ.get("CADFORK_MCP_SDK_REPORT"):
            with open(os.environ["CADFORK_MCP_SDK_REPORT"], "w", encoding="utf-8") as fh:
                json.dump(summary, fh, indent=1, sort_keys=True)
        self.assertEqual([s["mode"] for s in summary["sessions"]], ["auto", "legacy"])
        expect_error = {"approve_change": "approval_refused", "render": "unavailable"}
        for s in summary["sessions"]:
            self.assertEqual(s["errors"], [], s)
            self.assertEqual(s["protocol_version"], "2025-11-25")
            self.assertEqual(s["server"], "cadfork-mcp")
            self.assertEqual(s["tools"], [
                "list_documents", "open_document", "get_state", "describe_entity", "get_selection", "exec_code",
                "verify", "begin_change", "propose_change", "render", "import_export", "search_parts"])
            self.assertTrue(set(s["tools"]) <= set(s["calls"]), "every tool called")
            for name, calls in s["calls"].items():
                for c in calls:
                    if name in expect_error:
                        self.assertTrue(c["is_error"], (name, c))
                        self.assertEqual(c["error"], expect_error[name])
                    else:
                        self.assertFalse(c["is_error"], (name, c))
            self.assertEqual(s["state_schema"], "agentcore.state/1")
            self.assertEqual(s["exec_result"], 15.0)
            self.assertTrue(s["verify_ok"])
            self.assertEqual(s["proposal_status"], "pending_human_approval")
            self.assertGreater(s["resource_count"], 300)
            self.assertEqual(s["templates"], ["cadfork://api/{module}/{class}"])
            self.assertTrue(all(n > 100 for n in s["reads"].values()), s["reads"])
            self.assertEqual(s["missing_resource"], "MCPError")


if __name__ == "__main__":
    unittest.main()
