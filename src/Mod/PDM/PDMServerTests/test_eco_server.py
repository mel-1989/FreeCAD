# SPDX-License-Identifier: LGPL-2.1-or-later
"""G03 slice 6a acceptance checks 1 and 2: ECO workflow and access control against the PDM server.

Same method as slice 4 (test_server.py, whose helpers this reuses): one throwaway PostgreSQL + Forgejo + service stack
on 127.0.0.1 with non-default ports in a temp directory, and every client its own ``FreeCADCmd`` process running
``server_worker.py`` with its own working copy and Forgejo token. Users: alice (Forgejo write, no vault grant: engineer),
bob (write, granted approver by dora), carol (read: viewer), dora (admin). The suite skips when the stack is missing.

The named multi-user scenarios (acceptance check 2 asks for at least 12):

 1. eco_workflow_across_users                      alice creates and submits, bob approves, alice releases; history over REST
 2. engineer_cannot_approve                        library and a forged record that claims approver rights
 3. self_approval_refused                          bob cannot approve his own ECO, by library or forged push
 4. admin_override_recorded_in_history_and_audit   dora approves her own ECO only with an explicit, explained override
 5. rejection_returns_to_draft_with_comment        the reviewer's comment stays on the record; resubmit works
 6. viewer_refused_on_every_write                  carol: library, locks, transition, forged push; reads still work
 7. release_racing_checkin_20_of_20                release against a check-in of an affected document: exactly one wins
 8. service_restart_between_approve_and_release    the ECO lives in git, not in the service
 9. fault_mid_release_leaves_vault_unchanged       injected fault between two documents' commits, then a clean release
10. release_refused_when_one_document_is_locked    nothing is released
11. release_refused_after_a_checkin_moved_the_target
12. direct_release_of_a_document_in_an_open_eco_refused   library and a forged transition push
13. incomplete_release_push_refused_by_the_hook    the ECO commit with only one of its documents' transitions
14. roles_change_is_admin_only                     library, forged roles commit, REST /roles and /access
15. document_in_two_open_ecos_refused
16. concurrent_creators_get_distinct_numbers
17. approve_and_reject_racing_exactly_one_wins     bob approves while dora rejects, 3 trials; /audit holds only the step that landed
18. forged_create_must_take_the_next_number        ECO-99999999 refused; the next create still works
19. forged_future_timestamp_refused                 an entry dated 2999 is refused; the ECO keeps moving
20. move_of_a_document_in_an_open_eco_refused      library and a client that skips the library's check
"""

import os
import unittest

from PDMServerTests.test_server import SKIP, ServerCase, _collect, call


def eco_by(doc, m, *a, **k):
    return {"op": "eco_call", "doc": doc, "m": m, "a": list(a), "k": k}


@unittest.skipIf(SKIP, SKIP or "")
class TestEco(ServerCase):
    granted = False

    def setUp(self):
        super().setUp()
        if not TestEco.granted:
            r = self.client("dora", [call("set_role", "bob", "approver")], fresh=True)
            self.assertEqual(r[0]["value"], {"bob": "approver"}, r)
            TestEco.granted = True

    # ---- helpers ------------------------------------------------------------------------------

    def run_as(self, user, ops, **kw):
        return self.client(user, ops, fresh=not os.path.isdir(self.wc(user)), **kw)

    def spawn_as(self, user, ops, **kw):
        return self.spawn(user, ops, fresh=not os.path.isdir(self.wc(user)), **kw)

    def mkdoc(self, name=None, user="alice"):
        name = name or self.doc
        r = self.run_as(user, [{"op": "plain_doc", "name": name, "xml": "<Document/>"}])
        self.assertEqual(r[0]["value"]["revision"], "A.1", r)
        return name

    def new_eco(self, docs, user="alice", submit=False, approve_by=None, title="change", reason="reason"):
        """Create (and optionally submit and approve) an ECO over ``docs``; returns its number."""
        r = self.run_as(user, [call("eco_create", title, reason, {d: "A.1" for d in docs})])
        self.assertTrue(r[0]["ok"], r)
        n = r[0]["value"]["number"]
        if submit or approve_by:
            r = self.run_as(user, [call("eco_submit", n)])
            self.assertTrue(r[0]["ok"], r)
        if approve_by:
            r = self.run_as(approve_by, [call("eco_approve", n, "ok")])
            self.assertTrue(r[0]["ok"], r)
        return n

    def eco(self, n):
        st, rec = self.api("GET", f"/ecos/{n}", "carol")
        self.assertEqual(st, 200, rec)
        return rec

    def states(self, *docs):
        r = self.run_as("carol", [call("documents")])
        d = r[0]["value"]
        return {n: (d[n]["revision"], d[n]["state"]) for n in docs}

    # ---- scenarios ----------------------------------------------------------------------------

    def test_01_eco_workflow_across_users(self):
        d1, d2 = self.mkdoc(), self.mkdoc(self.doc + "b")
        n = self.new_eco([d1, d2], submit=True)
        self.assertEqual(self.eco(n)["state"], "Submitted")
        self.assertTrue(self.run_as("bob", [call("eco_approve", n, "looks right")])[0]["ok"])
        self.assertEqual(self.states(d1, d2), {d1: ("A.1", "WIP"), d2: ("A.1", "WIP")})  # approval releases nothing
        r = self.run_as("alice", [call("eco_release", n, "go")])
        self.assertEqual(r[0]["value"]["state"], "Released", r)
        self.assertEqual(self.states(d1, d2), {d1: ("A.1", "Released"), d2: ("A.1", "Released")})
        st, hist = self.api("GET", f"/ecos/{n}/history", "carol")
        self.assertEqual(st, 200)
        self.assertEqual([(h["action"], h["actor"], h["role"]) for h in hist],
                         [("create", "alice", "engineer"), ("submit", "alice", "engineer"),
                          ("approve", "bob", "approver"), ("release", "alice", "engineer")])
        self.assertTrue(all(h["at"] for h in hist))
        self.assertEqual(hist[2]["comment"], "looks right")
        self.assertIn(n, [e["number"] for e in self.api("GET", "/ecos?state=Released", "carol")[1]])
        lib = self.run_as("carol", [call("eco_history", n)])[0]["value"]  # the same history from Python
        self.assertEqual(lib, hist)
        self.assertEqual(self.api("GET", f"/ecos/{n}", None)[0], 401)

    def test_02_engineer_cannot_approve(self):
        d = self.mkdoc()
        n = self.new_eco([d], submit=True)
        r = self.run_as("alice", [call("eco_approve", n)])
        self.assertEqual(r[0]["error"], "AccessError", r)
        self.assertIn("approver", r[0]["message"])
        # a client that skips the library and claims approver rights in the record: the vault host replays the rules
        r = self.run_as("alice", [{"op": "eco_forge", "number": n, "action": "approve", "state": "Approved",
                                   "role": "approver"}])
        self.assertEqual(r[0]["error"], "PermissionDenied", r)
        self.assertIn("approver", r[0]["message"])
        self.assertEqual(self.eco(n)["state"], "Submitted")

    def test_03_self_approval_refused(self):
        d = self.mkdoc()
        n = self.new_eco([d], user="bob", submit=True)
        r = self.run_as("bob", [call("eco_approve", n)])
        self.assertEqual(r[0]["error"], "AccessError", r)
        self.assertIn("two-person", r[0]["message"])
        r = self.run_as("bob", [{"op": "eco_forge", "number": n, "action": "approve", "state": "Approved",
                                 "role": "approver"}])
        self.assertEqual(r[0]["error"], "PermissionDenied", r)
        self.assertIn("two-person", r[0]["message"])
        r = self.run_as("bob", [{"op": "eco_forge", "number": n, "action": "approve", "state": "Approved",
                                 "role": "approver", "entry": {"override": True, "comment": "trust me"}}])
        self.assertEqual(r[0]["error"], "PermissionDenied", r)  # an approver has no override
        self.assertEqual(self.eco(n)["state"], "Submitted")
        self.assertTrue(self.run_as("dora", [call("eco_approve", n)])[0]["ok"])  # someone else can

    def test_04_admin_override_recorded_in_history_and_audit(self):
        d = self.mkdoc()
        n = self.new_eco([d], user="dora", submit=True)
        r = self.run_as("dora", [call("eco_approve", n), call("eco_approve", n, ""  , override=True),
                                 call("eco_approve", n, "sole approver on shift, change is urgent", override=True)])
        self.assertEqual(r[0]["error"], "AccessError", r)  # no override requested: the two-person rule holds for admins
        self.assertEqual(r[1]["error"], "EcoError", r)  # an override must say why
        self.assertEqual(r[2]["value"]["state"], "Approved", r)
        last = self.eco(n)["history"][-1]
        self.assertEqual((last["actor"], last["role"], last["override"]), ("dora", "admin", True))
        self.assertEqual(last["comment"], "sole approver on shift, change is urgent")
        st, rows = self.api("GET", "/audit", "dora")
        ov = [x for x in rows if x["action"] == "eco-override" and x["document"] == n]
        self.assertEqual(len(ov), 1, rows[:6])
        self.assertEqual((ov[0]["actor"], ov[0]["detail"]["action"]), ("dora", "approve"))
        self.assertEqual(self.api("GET", "/audit", "bob")[0], 403)

    def test_05_rejection_returns_to_draft_with_comment(self):
        d = self.mkdoc()
        n = self.new_eco([d], submit=True)
        r = self.run_as("bob", [call("eco_reject", n, "")])
        self.assertEqual(r[0]["error"], "EcoError", r)  # a rejection says why
        r = self.run_as("bob", [call("eco_reject", n, "drawing lacks the tolerance")])
        self.assertEqual(r[0]["value"]["state"], "Draft", r)
        rec = self.eco(n)
        self.assertEqual(rec["state"], "Draft")
        self.assertEqual(rec["last_rejection"], dict(rec["last_rejection"], by="bob", comment="drawing lacks the tolerance"))
        self.assertEqual([h["to"] for h in rec["history"][-2:]], ["Rejected", "Draft"])
        self.assertTrue(self.run_as("alice", [call("eco_submit", n)])[0]["ok"])
        self.assertEqual(self.eco(n)["state"], "Submitted")

    def test_06_viewer_refused_on_every_write(self):
        d = self.mkdoc()
        n = self.new_eco([d], submit=True)
        ops = [call("eco_create", "t", "r", {d: "A.1"}), call("eco_submit", n), call("eco_approve", n),
               call("eco_reject", n, "no"), call("eco_withdraw", n, "no"), call("eco_release", n),
               call("set_role", "carol", "admin"), call("checkout", d), call("transition", d, "InReview"),
               {"op": "eco_forge", "number": n, "action": "approve", "state": "Approved", "role": "approver"},
               call("eco_get", n), call("eco_list"), call("role")]
        r = self.run_as("carol", ops)
        for res in r[:10]:
            self.assertIn(res.get("error"), ("AccessError", "PermissionDenied"), res)
        self.assertEqual(r[10]["value"]["number"], n)  # reads are fine
        self.assertIn(n, [e["number"] for e in r[11]["value"]])
        self.assertEqual(r[12]["value"], "viewer")
        self.assertEqual(self.api("POST", f"/locks/{d}", "carol", body={})[0], 403)
        self.assertEqual(self.api("GET", "/ecos", "carol")[0], 200)
        self.assertEqual(self.api("GET", "/access", "carol")[1], {"user": "carol", "base": "reader", "role": "viewer"})
        self.assertEqual(self.eco(n)["state"], "Submitted")
        self.assertEqual(self.states(d), {d: ("A.1", "WIP")})
        self.assertIsNone(self.lock_of(d))

    def test_07_release_racing_checkin_20_of_20(self):
        trials = 20
        docs = [f"{self.doc}r{t}" for t in range(trials)]
        setup = []
        for t, d in enumerate(docs):
            setup += [{"op": "plain_doc", "name": d, "xml": "<Document/>"},
                      call("eco_create", f"race {t}", "trial", {d: "A.1"}), eco_by(d, "eco_submit")]
        r = self.run_as("alice", setup)
        self.assertTrue(all(x.get("ok") for x in r), [x for x in r if not x.get("ok")][:2])
        r = self.run_as("bob", [eco_by(d, "eco_approve", "ok") for d in docs])
        self.assertTrue(all(x.get("ok") for x in r), [x for x in r if not x.get("ok")][:2])
        sync = os.path.join(self.tmp, "sync")
        os.makedirs(sync)
        bar = lambda t: {"op": "barrier", "dir": sync, "tag": f"t{t}", "n": 2}  # noqa: E731
        pa = self.spawn_as("alice", [x for t, d in enumerate(docs) for x in (bar(t), eco_by(d, "eco_release", "race"))])
        pb = self.spawn_as("bob", [x for t, d in enumerate(docs) for x in (
            bar(t), call("checkout", d), {"op": "append_xml", "name": d, "text": "<!-- edit -->"},
            call("checkin", d, "edit"))])
        ra, rb = _collect(pa)[1:], _collect(pb)[1:]
        self.assertEqual((len(ra), len(rb)), (2 * trials, 4 * trials), (ra[-1:], rb[-1:]))
        wins = {"release": 0, "checkin": 0}
        for t, d in enumerate(docs):
            rel, chk = ra[2 * t + 1], rb[4 * t + 3]
            released, checked_in = "error" not in rel, "error" not in chk
            self.assertNotEqual(released, checked_in, f"trial {t}: release {rel}, checkin {chk}")
            wins["release" if released else "checkin"] += 1
        self.assertEqual(sum(wins.values()), trials)
        print(f"\n  release vs check-in, {trials} trials, exactly one winner each: {wins}")
        doc_states = self.states(*docs)
        ecos = {e["number"]: e for e in self.api("GET", "/ecos", "carol")[1]}
        for t, d in enumerate(docs):
            released = "error" not in ra[2 * t + 1]
            ecos_of = [e for e in ecos.values() if [x["name"] for x in e["documents"]] == [d]]
            self.assertEqual(len(ecos_of), 1)
            if released:
                self.assertEqual((doc_states[d], ecos_of[0]["state"]), (("A.1", "Released"), "Released"), t)
            else:
                self.assertEqual((doc_states[d], ecos_of[0]["state"]), (("A.2", "WIP"), "Approved"), t)
                self.assertEqual(ra[2 * t + 1]["error"] in ("EcoError", "LockError", "PermissionDenied"), True, ra[2 * t + 1])
        self.assertEqual(self.api("GET", "/locks", "carol")[1].keys() & set(docs), set())

    def test_08_service_restart_between_approve_and_release(self):
        d = self.mkdoc()
        n = self.new_eco([d], approve_by="bob")
        self.stack.restart_service()
        self.assertEqual(self.eco(n)["state"], "Approved")
        r = self.run_as("alice", [call("eco_release", n)])
        self.assertEqual(r[0]["value"]["state"], "Released", r)
        self.assertEqual(self.states(d), {d: ("A.1", "Released")})
        self.assertEqual([h["action"] for h in self.eco(n)["history"]], ["create", "submit", "approve", "release"])

    def test_09_fault_mid_release_leaves_vault_unchanged(self):
        d1, d2, d3 = self.mkdoc(), self.mkdoc(self.doc + "b"), self.mkdoc(self.doc + "c")
        n = self.new_eco([d1, d2, d3], approve_by="bob")
        before = self.states(d1, d2, d3)
        for fault in ("eco_between", "before_push"):
            r = self.run_as("alice", [call("eco_release", n)], fault=fault)
            self.assertEqual(r[0]["error"], "GitError", (fault, r))
            self.assertEqual(self.states(d1, d2, d3), before, fault)
            self.assertEqual(self.eco(n)["state"], "Approved", fault)
        r = self.run_as("alice", [call("eco_release", n)])
        self.assertEqual(r[0]["value"]["state"], "Released", r)
        self.assertEqual(set(self.states(d1, d2, d3).values()), {("A.1", "Released")})

    def test_10_release_refused_when_one_document_is_locked(self):
        d1, d2 = self.mkdoc(), self.mkdoc(self.doc + "b")
        n = self.new_eco([d1, d2], approve_by="bob")
        self.assertTrue(self.run_as("bob", [call("checkout", d2)])[0]["ok"])
        r = self.run_as("alice", [call("eco_release", n)])
        self.assertEqual(r[0]["error"], "LockError", r)
        self.assertEqual(r[0]["holder"], "bob")
        self.assertEqual(self.states(d1, d2), {d1: ("A.1", "WIP"), d2: ("A.1", "WIP")})
        self.assertEqual(self.eco(n)["state"], "Approved")
        self.run_as("bob", [call("release_lock", d2)])
        self.assertEqual(self.run_as("alice", [call("eco_release", n)])[0]["value"]["state"], "Released")

    def test_11_release_refused_after_a_checkin_moved_the_target(self):
        d1, d2 = self.mkdoc(), self.mkdoc(self.doc + "b")
        n = self.new_eco([d1, d2], approve_by="bob")
        r = self.run_as("bob", [call("checkout", d2), {"op": "append_xml", "name": d2, "text": "<!-- later -->"},
                                call("checkin", d2, "after approval")])
        self.assertEqual(r[2]["value"]["revision"], "A.2", r)
        r = self.run_as("alice", [call("eco_release", n)])
        self.assertEqual(r[0]["error"], "EcoError", r)
        self.assertIn("A.2", r[0]["message"])
        self.assertEqual(self.states(d1, d2), {d1: ("A.1", "WIP"), d2: ("A.2", "WIP")})

    def test_12_direct_release_of_a_document_in_an_open_eco_refused(self):
        d = self.mkdoc()
        n = self.new_eco([d], submit=True)  # a Draft holds nothing
        self.assertTrue(self.run_as("alice", [call("transition", d, "InReview")])[0]["ok"])
        r = self.run_as("alice", [call("transition", d, "Released")])
        self.assertEqual(r[0]["error"], "LifecycleError", r)
        self.assertIn(n, r[0]["message"])
        r = self.run_as("alice", [{"op": "bare_release", "name": d}])  # around the library
        self.assertEqual(r[0]["error"], "PermissionDenied", r)
        self.assertIn(f"open {n}", r[0]["message"])
        self.assertEqual(self.states(d), {d: ("A.1", "InReview")})
        other = self.mkdoc(self.doc + "o")  # a document in no ECO keeps the slice 2 behaviour
        self.run_as("alice", [call("transition", other, "InReview")])
        self.assertEqual(self.run_as("alice", [call("transition", other, "Released")])[0]["value"]["state"], "Released")

    def test_13_incomplete_release_push_refused_by_the_hook(self):
        d1, d2 = self.mkdoc(), self.mkdoc(self.doc + "b")
        n = self.new_eco([d1, d2], approve_by="bob")
        r = self.run_as("alice", [{"op": "eco_forge", "number": n, "action": "release", "state": "Released",
                                   "role": "engineer", "partial_release": True}])
        self.assertEqual(r[0]["error"], "PermissionDenied", r)
        self.assertIn("all of its documents or nothing", r[0]["message"])
        self.assertEqual(self.states(d1, d2), {d1: ("A.1", "WIP"), d2: ("A.1", "WIP")})
        self.assertEqual(self.eco(n)["state"], "Approved")

    def test_14_roles_change_is_admin_only(self):
        r = self.run_as("bob", [call("set_role", "bob", "admin"), {"op": "eco_forge", "roles": {"bob": "admin"},
                                                                   "user": "bob", "role": "admin"}])
        self.assertEqual(r[0]["error"], "AccessError", r)
        self.assertEqual(r[1]["error"], "PermissionDenied", r)
        self.assertIn("needs admin", r[1]["message"])
        st, grants = self.api("GET", "/roles", "carol")
        self.assertEqual((st, grants["bob"]), (200, "approver"))
        self.assertEqual(self.api("GET", "/access", "bob")[1]["role"], "approver")
        self.assertEqual(self.api("GET", "/access", "alice")[1]["role"], "engineer")  # writer, no grant
        self.assertEqual(self.api("GET", "/access", "dora")[1]["role"], "admin")
        self.assertEqual(self.api("GET", "/whoami", "carol")[1], {"user": "carol", "role": "reader"})  # unchanged

    def test_15_document_in_two_open_ecos_refused(self):
        d = self.mkdoc()
        n = self.new_eco([d], submit=True)
        r = self.run_as("bob", [call("eco_create", "second", "r", {d: "A.1"})])  # held while n is Submitted or Approved
        self.assertEqual(r[0]["error"], "EcoError", r)
        self.assertIn(n, r[0]["message"])

    def test_16_concurrent_creators_get_distinct_numbers(self):
        docs = [self.mkdoc(f"{self.doc}c{i}") for i in range(8)]
        a, b = docs[:4], docs[4:]
        pa = self.spawn_as("alice", [call("eco_create", f"a{d}", "r", {d: "A.1"}) for d in a])
        pb = self.spawn_as("bob", [call("eco_create", f"b{d}", "r", {d: "A.1"}) for d in b])
        ra, rb = _collect(pa)[1:], _collect(pb)[1:]
        self.assertTrue(all("value" in x for x in ra + rb), (ra, rb))
        nums = [x["value"]["number"] for x in ra + rb]
        self.assertEqual(len(set(nums)), 8, (ra, rb))
        all_nums = [e["number"] for e in self.api("GET", "/ecos", "carol")[1]]
        self.assertEqual(len(all_nums), len(set(all_nums)))

    def test_17_approve_and_reject_racing_exactly_one_wins(self):
        trials = 3
        docs = [f"{self.doc}x{t}" for t in range(trials)]
        setup = []
        for t, d in enumerate(docs):
            setup += [{"op": "plain_doc", "name": d, "xml": "<Document/>"},
                      call("eco_create", f"dec {t}", "trial", {d: "A.1"}), eco_by(d, "eco_submit")]
        self.assertTrue(all(x.get("ok") for x in self.run_as("alice", setup)))
        sync = os.path.join(self.tmp, "sync")
        os.makedirs(sync)
        bar = lambda t: {"op": "barrier", "dir": sync, "tag": f"t{t}", "n": 2}  # noqa: E731
        pb = self.spawn_as("bob", [x for t, d in enumerate(docs) for x in (bar(t), eco_by(d, "eco_approve", "yes"))])
        pd = self.spawn_as("dora", [x for t, d in enumerate(docs) for x in (bar(t), eco_by(d, "eco_reject", "no"))])
        rd = _collect(pd)
        self.assertNotIn("crash", rd[0], rd)
        rb, rd = _collect(pb)[1:], rd[1:]
        self.assertEqual((len(rb), len(rd)), (2 * trials, 2 * trials), (rb[-1:], rd[-1:]))
        by_doc = {}
        for e in self.api("GET", "/ecos", "carol")[1]:
            by_doc[e["documents"][0]["name"]] = e["number"]
        for t, d in enumerate(docs):
            ok_b, ok_d = "error" not in rb[2 * t + 1], "error" not in rd[2 * t + 1]
            self.assertNotEqual(ok_b, ok_d, f"trial {t}: approve {rb[2 * t + 1]}, reject {rd[2 * t + 1]}")
            self.assertEqual(self.eco(by_doc[d])["state"], "Approved" if ok_b else "Draft", t)
            # the audit log holds the step that landed and nothing of the one that lost the race
            _st, rows = self.api("GET", "/audit", "dora")
            steps = sorted(x["detail"]["action"] for x in rows
                           if x["document"] == by_doc[d] and x["detail"].get("action") in ("approve", "reject"))
            self.assertEqual(steps, ["approve" if ok_b else "reject"], (t, steps))

    def test_18_forged_create_must_take_the_next_number(self):
        d = self.mkdoc()
        r = self.run_as("alice", [{"op": "eco_forge", "create": {"number": "ECO-99999999", "documents": [d]}}])
        self.assertEqual(r[0]["error"], "EcoNumberTaken", r)
        self.assertIn("next free number", r[0]["message"])
        r = self.run_as("alice", [call("eco_create", "after the squat", "r", {d: "A.1"})])
        self.assertTrue(r[0]["ok"], r)
        self.assertNotEqual(r[0]["value"]["number"], "ECO-99999999")

    def test_19_forged_future_timestamp_refused(self):
        d = self.mkdoc()
        n = self.new_eco([d])
        r = self.run_as("alice", [{"op": "eco_forge", "number": n, "action": "submit", "state": "Submitted",
                                   "entry": {"at": "2999-01-01T00:00:00+00:00"}}])
        self.assertEqual(r[0]["error"], "PermissionDenied", r)
        self.assertIn("ahead of the server clock", r[0]["message"])
        r = self.run_as("alice", [call("eco_submit", n)])
        self.assertTrue(r[0]["ok"], r)
        self.assertEqual(self.run_as("bob", [call("eco_approve", n)])[0]["value"]["state"], "Approved")

    def test_20_move_of_a_document_in_an_open_eco_refused(self):
        d = self.mkdoc()
        n = self.new_eco([d], submit=True)
        r = self.run_as("alice", [call("move", d, d + "m"), {"op": "move_unguarded", "old": d, "new": d + "m"}])
        self.assertEqual(r[0]["error"], "LifecycleError", r)
        self.assertIn(n, r[0]["message"])
        self.assertEqual(r[1]["error"], "PermissionDenied", r)  # the vault host refuses what the library would not
        self.assertIn(f"open {n}", r[1]["message"])
        self.assertEqual(self.states(d), {d: ("A.1", "WIP")})

    def test_21_withdrawn_or_rejected_eco_no_longer_holds_its_documents(self):
        d1, d2 = self.mkdoc(), self.mkdoc(self.doc + "w")
        n1 = self.new_eco([d1], submit=True)
        self.assertTrue(self.run_as("alice", [call("transition", d1, "InReview")])[0]["ok"])
        r = self.run_as("alice", [call("transition", d1, "Released")])
        self.assertEqual(r[0]["error"], "LifecycleError", r)  # held while Submitted
        self.assertEqual(self.run_as("alice", [call("eco_withdraw", n1, "not yet")])[0]["value"]["state"], "Draft")
        r = self.run_as("alice", [{"op": "bare_release", "name": d1}])  # the policy replays eco.open_docs: no hold left
        self.assertTrue(r[0]["ok"], r)
        self.assertEqual(self.states(d1), {d1: ("A.1", "Released")})
        n2 = self.new_eco([d2], submit=True)
        r = self.run_as("alice", [call("move", d2, d2 + "m")])
        self.assertEqual(r[0]["error"], "LifecycleError", r)
        self.assertEqual(self.run_as("bob", [call("eco_reject", n2, "no")])[0]["value"]["state"], "Draft")
        r = self.run_as("alice", [call("move", d2, d2 + "m")])
        self.assertTrue(r[0]["ok"], r)
