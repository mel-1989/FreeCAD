# SPDX-License-Identifier: LGPL-2.1-or-later
"""F3: only a token minted by the human-side ApplyAuthority can accept a turn."""

import os
import sys
import unittest

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "diff")
if FIX not in sys.path:
    sys.path.insert(0, FIX)

import diff_builders as db  # noqa: E402
from AgentCore.diff import AgentTurn, ApplyAuthority, ApplyToken, TokenRefused  # noqa: E402
from AgentCore.diff import authority as auth_mod  # noqa: E402


class TestApplyAuthority(unittest.TestCase):
    def setUp(self):
        self.auth = ApplyAuthority.install()
        self.doc = db.build_holed_block("f3_auth")

    def tearDown(self):
        self.auth.uninstall()
        db.close(self.doc)

    def _turn(self, length=50):
        with AgentTurn(self.doc, label="auth", verify=False) as t:
            self.doc.Block.Length = length
        return t

    def _refused(self, t, token, msg):
        before = (self.doc.UndoCount, list(self.doc.UndoNames))
        with self.assertRaises(TokenRefused) as cm:
            t.accept(token)
        self.assertIn(msg, str(cm.exception))
        self.assertEqual(t.status, "proposed")              # still pending, nothing applied
        self.assertEqual((self.doc.UndoCount, list(self.doc.UndoNames)), before)

    def test_valid_token_accepts(self):
        t = self._turn()
        self.assertEqual(t.accept(self.auth.mint(t))["decision"], "accepted")

    def test_missing_token_refused(self):
        t = self._turn()
        self._refused(t, None, "no apply token")
        self.assertTrue(t.reject()["equal"])

    def test_wrong_object_refused(self):
        t = self._turn()
        self._refused(t, "yes, apply it", "not an ApplyToken")
        self._refused(t, {"turn_id": t.turn_id}, "not an ApplyToken")

    def test_constructed_token_impossible(self):
        t = self._turn()
        with self.assertRaises(TypeError):
            ApplyToken(t.turn_id, t.digest(), "n", "m")
        with self.assertRaises(TypeError):
            ApplyAuthority()

    def test_forged_token_bypassing_constructor_refused(self):
        t = self._turn()
        forged = object.__new__(ApplyToken)
        forged.turn_id, forged.digest, forged.nonce, forged.mac = t.turn_id, t.digest(), "00" * 16, "ab" * 32
        self._refused(t, forged, "signature is invalid")
        real = self.auth.mint(t)
        tampered = object.__new__(ApplyToken)
        tampered.turn_id, tampered.digest, tampered.nonce, tampered.mac = real.turn_id, real.digest, "11" * 16, real.mac
        self._refused(t, tampered, "signature is invalid")
        half = object.__new__(ApplyToken)               # slots left unset
        self._refused(t, half, "malformed")

    def test_token_from_another_authority_refused(self):
        t = self._turn()
        self.auth.uninstall()
        other = ApplyAuthority.install()
        foreign = other.mint(t)
        other.uninstall()
        self.auth = ApplyAuthority.install()
        self._refused(t, foreign, "signature is invalid")

    def test_token_for_another_turn_refused(self):
        t1 = self._turn(50)
        tok1 = self.auth.mint(t1)
        t1.reject()
        t2 = self._turn(60)
        self._refused(t2, tok1, "is for turn")

    def test_token_for_another_change_set_refused(self):
        t = self._turn()
        tok = self.auth.mint(t)
        t._cs = dict(t._cs, label="edited after the human looked")   # change set changed after minting
        self._refused(t, tok, "different change set")

    def test_replayed_token_refused(self):
        t = self._turn()
        tok = self.auth.mint(t)
        t.accept(tok)
        self.doc.undo()
        self.doc.redo()                                   # the live state equals the reviewed one again
        self.doc.recompute()
        t.status = "proposed"                             # pretend the turn is pending again
        with self.assertRaises(TokenRefused) as cm:
            t.accept(tok)
        self.assertIn("already spent", str(cm.exception))

    def test_no_authority_installed_refuses_everything(self):
        t = self._turn()
        tok = self.auth.mint(t)
        self.auth.uninstall()
        try:
            self._refused(t, tok, "no ApplyAuthority installed")
        finally:
            self.auth = ApplyAuthority.install()

    def test_single_install_and_holder_only_uninstall(self):
        with self.assertRaises(RuntimeError):
            ApplyAuthority.install()
        self.assertTrue(auth_mod.installed())

    def test_token_does_not_serialise(self):
        import pickle
        t = self._turn()
        with self.assertRaises(TypeError):
            pickle.dumps(self.auth.mint(t))

    def test_no_authority_object_reachable_from_module(self):
        """No ApplyAuthority instance is reachable by attribute access from the
        module, which keeps only a verify-only function. This is not "nothing
        can mint": in a process that holds the authority, the key is readable
        through closures (``test_diff_approver.TestKeySearchControl`` forges a
        token that way). Agent code therefore never runs in such a process:
        workers are sealed and the key lives in the approver (SECURITY.md)."""
        import types
        seen, todo = set(), [(auth_mod, 0)]
        while todo:
            obj, depth = todo.pop()
            if id(obj) in seen or depth > 3:
                continue
            seen.add(id(obj))
            self.assertNotIsInstance(obj, ApplyAuthority)
            if isinstance(obj, (types.ModuleType, type)) or hasattr(obj, "__dict__"):
                for k, v in list(vars(obj).items()):
                    if isinstance(v, types.ModuleType) and v is not auth_mod:
                        continue
                    todo.append((v, depth + 1))
        self.assertFalse(hasattr(auth_mod, "_INSTALLED"))
        self.assertFalse(hasattr(auth_mod, "_SENTINEL"))
        self.assertFalse(hasattr(auth_mod._VERIFIER, "mint"))
        t = self._turn()
        self._refused(t, None, "no apply token")              # the verifier only verifies
        self.assertTrue(t.reject()["equal"])

    def test_authority_does_not_serialise(self):
        import pickle
        with self.assertRaises(TypeError):
            pickle.dumps(self.auth)

    def test_turn_api_never_exposes_the_authority(self):
        t = self._turn()
        public = {k: getattr(t, k) for k in dir(t) if not k.startswith("__")}
        self.assertFalse(any(isinstance(v, ApplyAuthority) for v in public.values()))
        self.assertFalse(any(isinstance(v, ApplyAuthority) for v in vars(t).values()))


if __name__ == "__main__":
    unittest.main()
