# SPDX-License-Identifier: LGPL-2.1-or-later
"""G03 slice 6a, checks 1, 2 and 5 at library level: ECO workflow, roles, two-person rule, atomic release.

These run in-process against a throwaway bare vault (the library needs no FreeCAD document for them: a document is a
folder with a ``Document.xml``). The multi-user scenarios against the server are in PDMServerTests/test_eco_server.py.
"""

import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from PDM import eco
from PDM import vault as V


class EcoCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pdmeco")
        self.vault = V.Vault.create(os.path.join(self.tmp, "vault.git"))
        self.users = {}

    def tearDown(self):
        os.environ.pop(V.FAULT_ENV, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def u(self, name):
        if name not in self.users:
            self.users[name] = V.Vault.clone(self.vault, os.path.join(self.tmp, "wc-" + name), name)
        return self.users[name]

    def add(self, name, user="alice"):
        v = self.u(user)
        os.makedirs(v.doc_path(name))
        with open(os.path.join(v.doc_path(name), "Document.xml"), "w") as f:
            f.write("<Document/>")
        v.add_document(name)

    def head(self):
        return subprocess.run(["git", "--git-dir", self.vault, "rev-parse", "main"], check=True,
                              stdout=subprocess.PIPE, text=True).stdout.strip()

    def states(self):
        return {n: (r["revision"], r["state"]) for n, r in self.u("alice").documents().items()}

    def approved(self, docs=("p1", "p2")):
        for d in docs:
            self.add(d)
        self.u("alice").set_role("bob", "approver")
        e = self.u("alice").eco_create("change", "because", {d: "A.1" for d in docs})
        self.u("alice").eco_submit(e["number"])
        self.u("bob").eco_approve(e["number"], "ok")
        return e["number"]


class TestWorkflow(EcoCase):
    def test_workflow_history_who_when_why(self):
        n = self.approved()
        rec = self.u("alice").eco_release(n, "ship it")
        self.assertEqual(rec["state"], "Released")
        h = self.u("carol").eco_history(n)
        self.assertEqual([(x["action"], x["from"], x["to"], x["actor"]) for x in h],
                         [("create", None, "Draft", "alice"), ("submit", "Draft", "Submitted", "alice"),
                          ("approve", "Submitted", "Approved", "bob"), ("release", "Approved", "Released", "alice")])
        self.assertTrue(all(x["at"] and x["role"] for x in h))
        self.assertEqual([x["comment"] for x in h][2:], ["ok", "ship it"])
        self.assertEqual(self.u("carol").eco_get(n)["author"], "alice")
        self.assertEqual(self.u("carol").eco_list("Released")[0]["number"], n)

    def test_release_uses_lifecycle_and_next_checkin_bumps_the_letter(self):
        n = self.approved(("p1",))
        self.u("alice").eco_release(n)
        self.assertEqual(self.states()["p1"], ("A.1", "Released"))
        a = self.u("alice")
        a.checkout("p1", new_revision=True)
        with open(os.path.join(a.doc_path("p1"), "Document.xml"), "a") as f:
            f.write("<!-- next -->")
        self.assertEqual(a.checkin("p1", "next letter")["revision"], "B.1")

    def test_rejection_returns_to_draft_with_the_reviewers_comment(self):
        self.add("p1")
        a = self.u("alice")
        a.set_role("bob", "approver")
        n = a.eco_create("t", "r", {"p1": "A.1"})["number"]
        a.eco_submit(n)
        with self.assertRaises(eco.EcoError):
            self.u("bob").eco_reject(n, "")  # a rejection says why
        rec = self.u("bob").eco_reject(n, "tolerance missing")
        self.assertEqual(rec["state"], "Draft")
        self.assertEqual(rec["last_rejection"]["comment"], "tolerance missing")
        self.assertEqual([(x["to"]) for x in rec["history"][-2:]], ["Rejected", "Draft"])
        a.eco_submit(n)
        self.assertEqual(a.eco_get(n)["state"], "Submitted")

    def test_release_is_one_push_with_every_document_or_none(self):
        n = self.approved(("p1", "p2", "p3"))
        before = self.head()
        os.environ[V.FAULT_ENV] = "eco_between"  # the second document's commit cannot be built
        with self.assertRaises(V.GitError):
            self.u("alice").eco_release(n)
        del os.environ[V.FAULT_ENV]
        self.assertEqual(self.head(), before)
        self.assertEqual(set(s for _, s in self.states().values()), {"WIP"})
        self.assertEqual(self.u("alice").eco_get(n)["state"], "Approved")
        os.environ[V.FAULT_ENV] = "before_push"
        with self.assertRaises(V.GitError):
            self.u("alice").eco_release(n)
        del os.environ[V.FAULT_ENV]
        self.assertEqual(self.head(), before)
        self.u("alice").eco_release(n)
        self.assertEqual(set(s for _, s in self.states().values()), {"Released"})
        log = subprocess.run(["git", "--git-dir", self.vault, "log", "--format=%H", f"{before}..main"], check=True,
                             stdout=subprocess.PIPE, text=True).stdout.split()
        self.assertEqual(len(log), 7)  # the ECO record, then WIP -> InReview -> Released for each of three documents

    def test_release_refused_for_a_locked_or_moved_on_document_changes_nothing(self):
        n = self.approved()
        a, b = self.u("alice"), self.u("bob")
        b.checkout("p2")
        before = self.head()
        with self.assertRaises(V.LockError):
            a.eco_release(n)
        self.assertEqual(self.head(), before)
        with open(os.path.join(b.doc_path("p2"), "Document.xml"), "a") as f:
            f.write("<!-- moved on -->")
        b.checkin("p2", "A.2 appears after approval")
        before = self.head()
        with self.assertRaises(eco.EcoError) as cm:
            a.eco_release(n)
        self.assertIn("A.2", str(cm.exception))
        self.assertEqual(self.head(), before)
        self.assertEqual(self.states()["p1"], ("A.1", "WIP"))

    def test_release_takes_a_wip_document_through_in_review(self):
        n = self.approved(("p1", "p2"))
        self.u("alice").transition("p2", "InReview")
        self.u("alice").eco_release(n)
        self.assertEqual(self.states(), {"p1": ("A.1", "Released"), "p2": ("A.1", "Released")})
        self.assertEqual([r["state"] for r in self.u("alice").history("p1")][:3], ["Released", "InReview", "WIP"])

    def test_document_in_an_open_eco_cannot_be_released_directly(self):
        self.add("p1")
        self.add("p2")
        a = self.u("alice")
        n = a.eco_create("t", "r", {"p1": "A.1"})["number"]
        a.transition("p1", "InReview")
        with self.assertRaises(V.LifecycleError) as cm:
            a.transition("p1", "Released")
        self.assertIn(n, str(cm.exception))
        a.transition("p2", "InReview")
        self.assertEqual(a.transition("p2", "Released")["state"], "Released")  # not in an ECO: slice 2 as before

    def test_a_document_belongs_to_one_open_eco(self):
        self.add("p1")
        a = self.u("alice")
        a.eco_create("one", "r", {"p1": "A.1"})
        with self.assertRaises(eco.EcoError) as cm:
            a.eco_create("two", "r", {"p1": "A.1"})
        self.assertIn("ECO-0001", str(cm.exception))
        with self.assertRaises(eco.EcoError):
            a.eco_create("three", "r", {"nope": "A.1"})
        with self.assertRaises(eco.EcoError):
            a.eco_create("four", "r", {})

    def test_numbers_are_sequential_and_concurrent_creators_never_clash(self):
        for d in ("p1", "p2"):
            self.add(d)
        a, b = self.u("alice"), self.u("bob")
        n1 = a.eco_create("one", "r", {"p1": "A.1"})["number"]
        n2 = b.eco_create("two", "r", {"p2": "A.1"})["number"]
        self.assertEqual((n1, n2), ("ECO-0001", "ECO-0002"))


class TestRoles(EcoCase):
    def test_engineer_creates_and_submits_but_cannot_approve(self):
        self.add("p1")
        a = self.u("alice")
        n = a.eco_create("t", "r", {"p1": "A.1"})["number"]
        a.eco_submit(n)
        with self.assertRaises(eco.AccessError) as cm:
            a.eco_approve(n)
        self.assertIn("approver", str(cm.exception))
        self.assertEqual(a.eco_get(n)["state"], "Submitted")

    def test_two_person_rule_approver_cannot_approve_own_but_an_admin_can_approve_anothers(self):
        self.add("p1")
        a = self.u("alice")
        a.set_role("alice", "admin")
        a.set_role("bob", "approver")
        a.set_role("dora", "admin")
        b = self.u("bob")
        n = b.eco_create("t", "r", {"p1": "A.1"})["number"]
        b.eco_submit(n)
        with self.assertRaises(eco.AccessError) as cm:
            b.eco_approve(n)  # an approver cannot approve what they authored
        self.assertIn("two-person", str(cm.exception))
        with self.assertRaises(eco.AccessError):
            b.eco_approve(n, "I insist", override=True)  # an approver has no override
        d = self.u("dora")
        self.assertEqual(d.eco_approve(n, "checked")["state"], "Approved")  # an admin approving another's: normal
        self.assertFalse(d.eco_get(n)["history"][-1]["override"])

    def test_admin_self_approval_needs_explicit_override_with_a_reason(self):
        self.add("p1")
        a = self.u("alice")
        a.set_role("dora", "admin")
        d = self.u("dora")
        n = d.eco_create("mine", "r", {"p1": "A.1"})["number"]
        d.eco_submit(n)
        with self.assertRaises(eco.AccessError):
            d.eco_approve(n)
        with self.assertRaises(eco.EcoError):
            d.eco_approve(n, "", override=True)
        rec = d.eco_approve(n, "emergency fix, no second approver on shift", override=True)
        last = rec["history"][-1]
        self.assertTrue(last["override"])
        self.assertEqual((last["actor"], last["role"]), ("dora", "admin"))
        self.assertEqual(rec["state"], "Approved")

    def test_viewer_reads_but_changes_nothing(self):
        self.add("p1")
        a = self.u("alice")
        a.set_role("carol", "viewer")
        n = a.eco_create("t", "r", {"p1": "A.1"})["number"]
        c = self.u("carol")
        self.assertEqual(c.role(), "viewer")
        self.assertEqual(c.eco_get(n)["number"], n)
        self.assertEqual(len(c.eco_list()), 1)
        for call in (lambda: c.eco_create("x", "y", {"p1": "A.1"}), lambda: c.eco_submit(n),
                     lambda: c.eco_approve(n), lambda: c.eco_release(n), lambda: c.set_role("carol", "admin")):
            with self.assertRaises(eco.AccessError):
                call()

    def test_roles_file_is_admin_only_after_it_exists_and_aliases_map(self):
        a, b = self.u("alice"), self.u("bob")
        self.assertEqual(a.role(), "engineer")  # no entry: the library-only default
        a.set_role("alice", "admin")  # bootstrap: the first writer of the file
        with self.assertRaises(eco.AccessError):
            b.set_role("bob", "approver")
        a.set_role("bob", "approver")
        self.assertEqual((a.roles(), b.role()), ({"alice": "admin", "bob": "approver"}, "approver"))
        self.assertEqual([eco.effective_role(base, {}, "x") for base in ("reader", "writer", "admin", None)],
                         ["viewer", "engineer", "admin", None])
        self.assertEqual(eco.effective_role("writer", {"x": "approver"}, "x"), "approver")
        self.assertEqual(eco.effective_role("writer", {"x": "admin"}, "x"), "engineer")  # the host decides who is admin
        self.assertEqual(eco.effective_role("reader", {"x": "approver"}, "x"), "viewer")  # and nobody is raised over a reader


class TestPolicyReplay(EcoCase):
    """``verify_change`` is what the server's pre-receive policy runs: the pushed record must equal the rules' result."""

    def setUp(self):
        super().setUp()
        self.add("p1")
        self.rec = self.u("alice").eco_create("t", "r", {"p1": "A.1"})
        self.sub = self.u("alice").eco_submit(self.rec["number"])

    def test_the_rules_own_result_passes(self):
        bad, act, _e = eco.verify_change(self.rec, self.sub, "alice", "engineer")
        self.assertEqual((bad, act), (None, "submit"))

    def test_forged_records_are_refused(self):
        forged = copy.deepcopy(self.sub)
        forged["state"] = "Approved"
        forged["history"].append({"seq": 3, "action": "approve", "from": "Submitted", "to": "Approved", "actor": "alice",
                                  "role": "approver", "at": "2099-01-01T00:00:00+00:00", "override": False, "comment": ""})
        bad, _a, _e = eco.verify_change(self.sub, forged, "alice", "engineer")
        self.assertIn("approver", bad or "")  # alice is an engineer; a record claiming otherwise is not what the rules produce
        again = copy.deepcopy(self.sub)
        again["history"] = again["history"][:1]
        self.assertIsNotNone(eco.verify_change(self.sub, again, "alice", "engineer")[0])  # history is append-only
        tamper = copy.deepcopy(self.sub)
        tamper["title"] = "other"
        tamper["history"].append(dict(self.sub["history"][-1], seq=3, action="withdraw", comment="x", **{}))
        self.assertIsNotNone(eco.verify_change(self.sub, tamper, "alice", "engineer")[0])
        author = copy.deepcopy(self.rec)
        author["author"] = "mallory"
        self.assertIsNotNone(eco.verify_change(None, author, "alice", "engineer")[0])

    def test_record_is_plain_sorted_json_in_the_vault(self):
        out = subprocess.run(["git", "--git-dir", self.vault, "cat-file", "-p", f"main:plm/eco/{self.rec['number']}.json"],
                             check=True, stdout=subprocess.PIPE, text=True).stdout
        self.assertEqual(json.loads(out)["state"], "Submitted")
        self.assertEqual(out, json.dumps(json.loads(out), indent=2, sort_keys=True) + "\n")


class TestReview031(EcoCase):
    """Repairs after the independent verifier of run 031 (half E)."""

    def raw_eco_commit(self, v, number, rec, event="eco"):
        v._fetch()
        return eco._put(v, v._head(), f"{eco.ECO_DIR}{number}.json", eco._dump(rec), "forged", event,
                        [("Eco", number), ("Eco-Action", "x")])

    def test_next_number_is_one_above_the_top_and_survives_a_squatted_top(self):
        self.assertEqual(eco.next_number({}), "ECO-0001")
        self.assertEqual(eco.next_number({"ECO-0007": 1, "ECO-0002": 1}), "ECO-0008")
        self.assertEqual(eco.next_number({"ECO-99999999": 1}), "ECO-0001")
        self.assertEqual(eco.next_number({"ECO-99999999": 1, "ECO-0001": 1}), "ECO-0002")

    def test_verify_change_demands_exactly_the_next_number_for_a_create(self):
        rec = eco.apply_action(None, "create", "alice", "engineer", "", eco._now(), False,
                               {"number": "ECO-99999999", "title": "t", "reason": "r",
                                "documents": [{"name": "p1", "revision": "A.1"}]})
        why, _a, _e = eco.verify_change(None, rec, "alice", "engineer", {"ECO-0001": {}})
        self.assertIn("next free number ECO-0002", why)
        rec["number"] = "ECO-0002"
        rec["history"] = [dict(rec["history"][0])]
        self.assertIsNone(eco.verify_change(None, rec, "alice", "engineer", {"ECO-0001": {}})[0])

    def test_verify_change_refuses_a_timestamp_ahead_of_the_clock(self):
        rec = eco.apply_action(None, "create", "alice", "engineer", "", "2999-01-01T00:00:00+00:00", False,
                               {"number": "ECO-0001", "title": "t", "reason": "r",
                                "documents": [{"name": "p1", "revision": "A.1"}]})
        self.assertIn("ahead of the server clock", eco.verify_change(None, rec, "alice", "engineer", {}, eco.latest_at())[0])
        self.assertIsNone(eco.verify_change(None, rec, "alice", "engineer", {})[0])  # no clock given: not judged

    def test_a_forged_future_entry_jams_a_local_vault_with_a_clear_error_but_slight_skew_does_not(self):
        import datetime
        self.add("p1")
        a = self.u("alice")
        n = a.eco_create("t", "r", {"p1": "A.1"})["number"]
        a._fetch()
        rec = copy.deepcopy(eco.load(a, a._head(), n))
        soon = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=120)).isoformat(timespec="seconds")
        rec["history"][-1]["at"] = soon  # the author's clock ran two minutes fast
        self.assertTrue(a._push_main(self.raw_eco_commit(a, n, rec)))
        self.assertEqual(a.eco_submit(n)["history"][-1]["at"], soon)  # never goes backwards: max(now, last)
        a._fetch()
        rec = copy.deepcopy(eco.load(a, a._head(), n))
        rec["history"][-1]["at"] = "2999-01-01T00:00:00+00:00"
        self.assertTrue(a._push_main(self.raw_eco_commit(a, n, rec)))
        with self.assertRaises(eco.EcoError) as cm:
            a.eco_withdraw(n, "no")
        self.assertIn("ahead of this machine's clock", str(cm.exception))

    def test_moving_a_document_in_an_open_eco_is_refused_and_a_released_one_may_move(self):
        self.add("p1")
        self.add("p2")
        a = self.u("alice")
        n = a.eco_create("t", "r", {"p1": "A.1"})["number"]
        before = self.head()
        with self.assertRaises(V.LifecycleError) as cm:
            a.move("p1", "p1b")
        self.assertIn(n, str(cm.exception))
        self.assertIn("edit the ECO to drop the document, or release it", str(cm.exception))
        self.assertEqual(self.head(), before)
        self.assertEqual(a.move("p2", "p2b")["new"], "p2b")  # not in an ECO

    def test_a_create_that_loses_the_number_race_to_the_server_retries_once_then_says_so(self):
        from PDM import remote
        self.add("p1")
        self.add("p2")
        a = self.u("alice")
        real, calls = a._push_main, []

        def lose(sha, times):
            calls.append(sha)
            if len(calls) <= times:
                raise remote.PermissionDenied("the vault refused the push: a new ECO must take the next free number ECO-0002")
            return real(sha)
        a._push_main = lambda sha: lose(sha, 1)
        self.assertEqual(a.eco_create("t", "r", {"p1": "A.1"})["number"], "ECO-0001")
        self.assertEqual(len(calls), 2)  # one retry succeeded
        calls.clear()
        a._push_main = lambda sha: lose(sha, 9)
        with self.assertRaises(eco.EcoError) as cm:
            a.eco_create("t2", "r", {"p2": "A.1"})
        self.assertIn("lost the race", str(cm.exception))
        self.assertEqual(len(calls), 2)

    def test_an_eco_naming_a_document_that_is_no_longer_in_the_tree_is_refused(self):
        self.add("p1")
        a = self.u("alice")
        self.assertEqual(a.move("p1", "p1b")["new"], "p1b")
        rec = {"number": "ECO-0001", "documents": [{"name": "p1", "revision": "A.1"}]}
        docs = {"p1": {"revision": "A.1", "state": "WIP"}}  # the moved-away name still has records
        eco.check_context("create", rec, docs, {}, {})  # without the tree it passes: the gap the verifier found
        with self.assertRaises(eco.EcoError):
            eco.check_context("create", rec, docs, {}, {}, present={"p1b"})
        with self.assertRaises(eco.EcoError):
            a.eco_create("t", "r", {"p1": "A.1"})

    def test_set_role_to_the_role_already_held_pushes_nothing(self):
        a = self.u("alice")
        a.set_role("alice", "admin")  # bootstrap: the first writer of the file
        before = self.head()
        self.assertEqual(a.set_role("alice", "admin"), {"alice": "admin"})
        self.assertEqual(a.set_role("carol", None), {"alice": "admin"})  # removing an entry that is not there
        self.assertEqual(self.head(), before)

    def test_a_record_the_hook_would_not_forward_is_refused_before_pushing(self):
        self.add("p1")
        a = self.u("alice")
        a.set_role("alice", "admin")
        n = a.eco_create("t", "r", {"p1": "A.1"})["number"]
        before = self.head()
        old = eco.MAX_PLM_BYTES
        eco.MAX_PLM_BYTES = 300
        try:
            with self.assertRaises(eco.EcoError) as cm:
                a.eco_submit(n)
            self.assertIn("300 bytes", str(cm.exception))
            eco.MAX_PLM_BYTES = 20
            with self.assertRaises(eco.EcoError):
                a.set_role("bob", "approver")
        finally:
            eco.MAX_PLM_BYTES = old
        self.assertEqual(self.head(), before)
        self.assertEqual(a.eco_submit(n)["state"], "Submitted")
