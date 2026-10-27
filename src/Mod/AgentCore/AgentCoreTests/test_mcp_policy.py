# SPDX-License-Identifier: LGPL-2.1-or-later
"""F4: exec_code policy (static scan and decisions). Pure Python."""

import unittest

from AgentCore.mcp import policy


class TestPolicy(unittest.TestCase):
    def test_plain_edit_is_clean(self):
        r = policy.scan("b = doc.addObject('Part::Box', 'Box')\nb.Length = 3\ndoc.recompute()\nresult = b.Shape.Volume")
        self.assertEqual(r["flags"], [])
        self.assertEqual(r["refused"], [])
        self.assertEqual(policy.decide(r, "strict"), (True, ""))

    def test_file_subprocess_network_flagged(self):
        r = policy.scan("import os, subprocess\nimport socket\nopen('/etc/passwd').read()\nos.system('ls')")
        cats = {f["category"] for f in r["flags"]}
        self.assertTrue({"file", "subprocess", "network"} <= cats, r)
        self.assertTrue(policy.decide(r, "flag")[0])
        ok, why = policy.decide(r, "strict")
        self.assertFalse(ok)
        self.assertIn("strict", why)

    def test_approval_refused_in_every_mode(self):
        for code in ("turn.accept('x')", "from AgentCore.diff import accept", "t.apply_token = 'x'",
                     "print(APPLY_TOKEN)", "x.approve()"):
            r = policy.scan(code)
            self.assertTrue(r["refused"], code)
            for mode in policy.MODES:
                self.assertFalse(policy.decide(r, mode)[0], (code, mode))

    def test_transaction_control_refused(self):
        for code in ("doc.commitTransaction()", "doc.abortTransaction()", "doc.undo()"):
            self.assertFalse(policy.decide(policy.scan(code))[0], code)

    def test_syntax_error(self):
        r = policy.scan("def (")
        self.assertIsNotNone(r["syntax_error"])
        self.assertFalse(policy.decide(r)[0])

    def test_bad_mode(self):
        with self.assertRaises(ValueError):
            policy.decide(policy.scan("x=1"), "lenient")

    def test_runtime_audit_records_writes_only_while_active(self):
        import os
        import tempfile

        a = policy.RuntimeAudit.instance()
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "f.txt")
            open(p, "w").close()  # inactive: not recorded
            a.start()
            with open(p, "w") as fh:
                fh.write("x")
            ev = a.stop()
            open(p).close()
        self.assertEqual([e["category"] for e in ev], ["file"])
        self.assertIn("f.txt", ev[0]["detail"])
        self.assertEqual(a.events, ev)


if __name__ == "__main__":
    unittest.main()
