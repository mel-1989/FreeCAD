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

    def test_verifier_bypass_strings_refused(self):
        """Every bypass the F4 verifier found came back allowed; now each is refused in every mode."""
        for code in ('getattr(turn, "acc"+"ept")(1)',
                     'f=getattr; f(turn,"accept")',
                     'getattr(doc,"commit"+"Transaction")()',
                     'App.closeActiveTransaction()',
                     'App.setActiveTransaction("mine")',
                     'FreeCAD.ActiveDocument.commitTransaction()',
                     'from AgentCore.diff import authority as A; au=A.ApplyAuthority.install()',
                     'import AgentCore.diff.authority',
                     'from AgentCore.diff.authority import verify_token',
                     'import AgentCore.diff.approver as ap',
                     '__import__("AgentCore.diff." + "authority")',
                     'importlib.import_module("AgentCore.diff.authority")',
                     'n = "acc"; m = n + "ept"; getattr(t, m)()',
                     't.__getattribute__("".join(["acc", "ept"]))()',
                     'object.__getattribute__(t, "%sept" % "acc")()',
                     'getattr(t, "{}ept".format("acc"))()',
                     'getattr(doc, "odnu"[::-1])()',
                     'setattr(authority, "_VERIFIER", None)',
                     'vars(t)["accept"]',
                     'A._VERIFIER.__closure__[0].cell_contents',
                     'g = getattr\nh = g\nh(doc, "un" + "do")()'):
            r = policy.scan(code)
            self.assertTrue(r["refused"], code)
            for mode in policy.MODES:
                self.assertFalse(policy.decide(r, mode)[0], (code, mode))

    def test_dynamic_access_flagged_and_strict_refuses(self):
        for code in ("getattr(t, name)()", "f = getattr", "import gc\ngc.get_objects()",
                     "fn.__closure__", "x = vars()", "setattr(o, k, v)"):
            r = policy.scan(code)
            self.assertEqual(r["refused"], [], code)
            cats = {f["category"] for f in r["flags"]}
            self.assertTrue(cats & {"dynamic_attr", "introspection"}, (code, r))
            self.assertTrue(policy.decide(r, "flag")[0], code)
            self.assertFalse(policy.decide(r, "strict")[0], code)

    def test_ordinary_getattr_with_literal_is_clean(self):
        r = policy.scan("v = getattr(doc.getObject('Box'), 'Length')\nresult = 'acc' + 'uracy'")
        self.assertEqual(r["refused"], [])
        self.assertEqual(r["flags"], [])

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
