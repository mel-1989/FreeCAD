# SPDX-License-Identifier: LGPL-2.1-or-later
"""F3/F4 trust boundary: the apply key lives only in the approver process.

- A sealed agent process (what the F4 session worker is) refuses
  ``ApplyAuthority.install()`` and cannot mint a token the approver honours:
  the getattr, closure/gc key search and ``object.__new__`` forgery probes
  all fail. Replacing the verifier in-process fools only that process; the
  approver's record stays ``pending`` (the documented residual).
- The legitimate path: a human approves (turn id, digest) on the approver's
  own channel, the worker polls, accepts, and the approver records it spent.
- The MCP server process never loads the authority or approver modules.
"""

import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from AgentCoreTests.fixtures.mcp import mcp_common as mc

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)                                   # src/Mod/AgentCore
APPROVER = os.path.join(SRC, "AgentCore", "diff", "approver.py")
PROBE = os.path.join(HERE, "fixtures", "diff", "sealed_probe.py")


def python3():
    exe = shutil.which("python3")
    if not exe:
        raise unittest.SkipTest("python3 not on PATH")
    return exe


class Approver:
    """The human side, run as its own process; the test types on its stdin."""

    def __init__(self, directory):
        self.socket = os.path.join(directory, "approver.sock")
        self.proc = subprocess.Popen([python3(), "-I", APPROVER, "--socket", self.socket], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        line = self.proc.stdout.readline()
        if not line.startswith("CADFORK_APPROVER_READY"):
            self.proc.kill()
            raise RuntimeError(f"approver did not start: {line!r}")

    def say(self, line):
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()
        return self.proc.stdout.readline().strip()

    def close(self):
        try:
            self.proc.stdin.write("quit\n")
            self.proc.stdin.flush()
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()


def short_dir(prefix):
    from AgentCore.mcp.workers import short_tempdir
    return short_tempdir(prefix)


class TestSealedWorker(unittest.TestCase):
    def _run_probe(self, with_approver):
        d = short_dir("acapp-")
        approver = Approver(d) if with_approver else None
        try:
            env = dict(os.environ, QT_QPA_PLATFORM="offscreen", AGENTCORE_SRC=SRC, PROBE_OUT=d, PROBE_FILE=PROBE,
                       PROBE_APPROVER=approver.socket if approver else "")
            proc = subprocess.Popen([mc.freecadcmd(), "-c", f"exec(open({PROBE!r}).read())"], env=env,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            approved = None
            if approver:
                ids_path = os.path.join(d, "ids.json")
                deadline = time.time() + 120
                while not os.path.exists(ids_path) and proc.poll() is None and time.time() < deadline:
                    time.sleep(0.05)
                if os.path.exists(ids_path):
                    time.sleep(0.05)
                    with open(ids_path) as fh:
                        ids = json.load(fh)
                    approved = approver.say(f"approve {ids['turn_id']} {ids['digest']}")
            out, _ = proc.communicate(timeout=180)
            with open(os.path.join(d, "result.json")) as fh:
                res = json.load(fh)
            self.assertNotIn("crash", res, res.get("crash"))
            return res, approved
        finally:
            if approver:
                approver.close()
            shutil.rmtree(d, ignore_errors=True)

    def test_forgery_probes_fail_and_human_approval_applies(self):
        res, approved = self._run_probe(with_approver=True)
        p = res["probes"]
        self.assertIn("refused: RuntimeError", p["install"])
        self.assertIn("agent code", p["install"])
        for name in ("object_new", "getattr_concat", "closure_gc_key"):
            self.assertTrue(p[name].startswith("refused: TokenRefused"), (name, p[name]))
        self.assertNotIn("bytes", res["closure_cells"])         # no key material in the verifier's closure
        self.assertEqual(res["status_after_probes"], "proposed")
        # residual: an in-process verifier swap fools the worker, not the approver
        self.assertEqual(res["residual_local_accept"], "accepted")
        self.assertEqual(res["residual_approver_status"], "pending")
        # legitimate path
        self.assertTrue(approved.startswith("APPROVED"), approved)
        self.assertTrue(res["polled"])
        self.assertEqual(res["legit_decision"], "accepted")
        self.assertEqual(res["length_after"], 77.0)
        self.assertEqual(res["approver_status_after"], "spent")
        self.assertTrue(res["replay"].startswith("refused"), res["replay"])

    def test_sealed_without_approver_refuses_every_token(self):
        res, _ = self._run_probe(with_approver=False)
        p = res["probes"]
        self.assertIn("refused: RuntimeError", p["install"])
        for name in ("object_new", "getattr_concat"):
            self.assertIn("no approver connected", p[name], (name, p[name]))
        self.assertTrue(p["closure_gc_key"].startswith("refused: TokenRefused"), p["closure_gc_key"])
        self.assertEqual(res["status_after_probes"], "proposed")


class TestKeySearchControl(unittest.TestCase):
    """Positive control for the key-search probe: in a process that holds the
    authority (the GUI or a test, never a worker) the same search finds the
    key and forges a token. That is the in-process limit SECURITY.md states,
    and it shows the sealed probe would have found a key had there been one."""

    def test_key_search_forges_where_the_key_lives(self):
        sys.path.insert(0, os.path.join(HERE, "fixtures", "diff"))
        import diff_builders as db
        import forge
        from AgentCore.diff import AgentTurn, ApplyAuthority, authority
        auth = ApplyAuthority.install()
        doc = db.build_holed_block("f3_forge_control")
        try:
            with AgentTurn(doc, label="control", verify=False) as t:
                doc.Block.Length = 50
            ok, n = forge.try_forge(t, authority)
            self.assertTrue(ok, n)
            self.assertEqual(t.status, "accepted")
        finally:
            auth.uninstall()
            db.close(doc)


class TestApproverChannel(unittest.TestCase):
    def test_socket_has_no_mint_or_approve_op(self):
        d = short_dir("acapp-")
        a = Approver(d)
        try:
            from AgentCore.diff import approver as ap
            for op in ("approve", "mint", "accept", "apply"):
                r = ap.request(a.socket, {"op": op, "turn_id": "t", "digest": "d"})
                self.assertFalse(r["ok"])
                self.assertIn("not available", r["reason"])
            self.assertIsNone(ap.request(a.socket, {"op": "poll", "turn_id": "t", "digest": "d"})["token"])
            self.assertEqual(ap.request(a.socket, {"op": "status", "turn_id": "t", "digest": "d"})["status"], "pending")
            self.assertTrue(a.say("approve t d").startswith("APPROVED"))
            tok = ap.request(a.socket, {"op": "poll", "turn_id": "t", "digest": "d"})["token"]
            self.assertFalse(ap.request(a.socket, {"op": "check", "turn_id": "t", "digest": "other",
                                                   "token": tok})["ok"])
            self.assertTrue(ap.request(a.socket, {"op": "check", "turn_id": "t", "digest": "d", "token": tok})["ok"])
            r = ap.request(a.socket, {"op": "check", "turn_id": "t", "digest": "d", "token": tok})
            self.assertIn("already spent", r["reason"])
            self.assertEqual(ap.request(a.socket, {"op": "status", "turn_id": "t", "digest": "d"})["status"], "spent")
            self.assertTrue(a.say("approve t d").startswith("APPROVED"))   # re-approval after a failed accept
            tok2 = ap.request(a.socket, {"op": "poll", "turn_id": "t", "digest": "d"})["token"]
            self.assertNotEqual(tok2["nonce"], tok["nonce"])
            self.assertTrue(ap.request(a.socket, {"op": "check", "turn_id": "t", "digest": "d", "token": tok2})["ok"])
            self.assertFalse(ap.request(a.socket, {"op": "check", "turn_id": "t", "digest": "d", "token": tok})["ok"])
            self.assertEqual(oct(os.stat(a.socket).st_mode & 0o777), "0o600")
        finally:
            a.close()
            shutil.rmtree(d, ignore_errors=True)

    def test_mcp_server_process_never_loads_the_authority(self):
        """A full tool round in a plain-Python server process: no AgentCore.diff module is ever imported,
        and the source of every module the server loads has no approve path."""
        d = tempfile.mkdtemp(prefix="acapp-srv-")
        try:
            fixture = mc.save_fixtures(["box"], d)["box"]
            code = f"""
import json, sys
sys.path.insert(0, {SRC!r})
from AgentCore.mcp.server import Server
srv = Server(freecadcmd={mc.freecadcmd()!r})
def call(name, **a):
    r = srv.handle({{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {{"name": name, "arguments": a}}}})
    return r["result"]["structuredContent"], r["result"]["isError"]
try:
    doc = call("open_document", path={fixture!r})[0]["doc"]
    call("begin_change", doc=doc)
    call("exec_code", doc=doc, code="doc.getObject('Box').Length = 15")
    prop, _ = call("propose_change", doc=doc)
    ap, ap_err = call("approve_change", proposal_id=prop.get("proposal_id"))
    mods = sorted(m for m in sys.modules if m.startswith("AgentCore"))
    print("SERVER_PROBE " + json.dumps({{"mods": mods, "approve_error": ap_err, "approve": ap,
                                        "status": prop.get("status")}}))
finally:
    srv.close()
"""
            env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
            p = subprocess.run([python3(), "-I", "-c", code], capture_output=True, text=True, timeout=300,
                               env=dict(env, QT_QPA_PLATFORM="offscreen"), stdin=subprocess.DEVNULL)
            line = next((x for x in p.stdout.splitlines() if x.startswith("SERVER_PROBE ")), None)
            self.assertIsNotNone(line, p.stdout[-2000:] + p.stderr[-3000:])
            r = json.loads(line.split(" ", 1)[1])
            self.assertEqual(r["status"], "pending_human_approval")
            self.assertTrue(r["approve_error"])
            self.assertEqual(r["approve"]["error"], "approval_refused")
            self.assertFalse([m for m in r["mods"] if m.startswith("AgentCore.diff")], r["mods"])
            for m in r["mods"]:
                path = os.path.join(SRC, *m.split(".")) + ".py"
                if not os.path.exists(path):
                    path = os.path.join(SRC, *m.split("."), "__init__.py")
                with open(path, encoding="utf-8") as fh:
                    tree = ast.parse(fh.read())
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        self.assertFalse([a.name for a in node.names if "diff" in a.name], (m, ast.dump(node)))
                    elif isinstance(node, ast.ImportFrom):
                        mod = ("." * node.level) + (node.module or "")
                        self.assertNotIn("diff", mod, (m, mod))
                        self.assertFalse({a.name for a in node.names} & {"authority", "approver"}, m)
                    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                        self.assertNotIn(node.func.attr, {"mint", "mint_for", "approve", "install"}, (m, node.lineno))
                        if node.func.attr == "accept":             # socket.accept() takes no token
                            self.assertFalse(node.args or node.keywords, (m, node.lineno))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_deny_revokes_a_token_already_polled(self):
        d = short_dir("acapp-")
        a = Approver(d)
        try:
            from AgentCore.diff import approver as ap
            q = {"turn_id": "t", "digest": "d"}
            self.assertTrue(a.say("approve t d").startswith("APPROVED"))
            tok = ap.request(a.socket, dict(q, op="poll"))["token"]
            self.assertTrue(a.say("deny t d").startswith("DENIED"))
            r = ap.request(a.socket, dict(q, op="check", token=tok))
            self.assertFalse(r["ok"])
            self.assertIn("revoked", r["reason"])
            self.assertEqual(ap.request(a.socket, dict(q, op="status"))["status"], "denied")
            # a later approval mints a fresh token; the pre-deny one stays dead
            self.assertTrue(a.say("approve t d").startswith("APPROVED"))
            tok2 = ap.request(a.socket, dict(q, op="poll"))["token"]
            self.assertFalse(ap.request(a.socket, dict(q, op="check", token=tok))["ok"])
            self.assertTrue(ap.request(a.socket, dict(q, op="check", token=tok2))["ok"])
        finally:
            a.close()
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
