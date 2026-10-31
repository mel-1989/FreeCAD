# SPDX-License-Identifier: LGPL-2.1-or-later
"""G03 slice 4 acceptance checks: the PDM server, driven by separate FreeCADCmd client processes.

One local test stack (PostgreSQL, Forgejo, the PDM service; all on 127.0.0.1, non-default ports, in a temp
directory, torn down afterwards) serves each test class (three classes, three stacks, one per setUpClass). Every client is its own ``FreeCADCmd`` process running
``server_worker.py`` with its own working copy and its own Forgejo token; no test lets two clients share an
interpreter. The suite skips when PostgreSQL, Forgejo or the service venv is missing (see tools/pdm-server/README.md).

The named multi-user scenarios (acceptance check 3):

 1. concurrent_checkout_refusal_names_holder      two users, one document: checkout and check-in refused, holder named
 2. same_user_second_working_copy_refused         a lock belongs to a working copy, not only to a name
 3. release_then_other_user_checks_out            release hands the document to the next user
 4. checkin_releases_lock_next_user_sees_revision  check-in makes A.2 and frees the lock
 5. lock_race_two_clients_20_of_20                exactly one winner in each of 20 trials
 6. lock_race_three_clients                       exactly one winner among three processes, 6 trials
 7. history_across_users                          another user lists revisions, authors and messages
 8. old_revision_opens_read_only_across_users     I3 golden metrics of A.1 from another user's process
 9. stale_checkin_refused                         a stale working copy never overwrites a newer revision
10. where_used_after_move_seen_by_other_user      the server's where-used index follows a move
11. move_refused_while_other_user_holds_referencing_lock
12. lock_survives_client_restart
13. lock_survives_service_restart
14. lock_survives_postgres_crash_restart
15. unauthenticated_requests_refused              no token, bad token, anonymous git
16. token_of_another_user_refused                 a client cannot claim another user's name
17. reader_can_list_history_and_open
18. reader_cannot_check_out
19. reader_cannot_check_in
20. reader_cannot_transition
21. lock_break_admin_only
22. lock_break_recorded_in_audit_log
23. service_outage_during_checkin                 vault stays at the previous revision, lock held, retry works
24. failed_push_keeps_revision_and_lock           injected git fault, as slice 2 but through the server
25. push_without_lock_refused_by_vault_host       a client that skips the library cannot publish unlocked
26. push_while_other_user_holds_lock_refused
27. push_touching_other_document_refused
28. forged_illegal_transition_refused
29. lifecycle_across_users                         release by one user, new revision by another
30. transition_refused_while_other_user_holds_lock
31. non_holder_cannot_release_lock                 library and raw HTTP
32. gui_connect_against_server                     the slice 5 panels' connection layer (PDMGui.state) on a server URL,
                                                   with another user's lock held by a separate process
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.parse

import FreeCAD as App

from PDM import vault as V

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.join(HERE, "server_worker.py")
CMD = os.path.join(App.getHomePath(), "bin", "FreeCADCmd")
ENV = dict(os.environ, QT_QPA_PLATFORM="offscreen", PDM_WORKER_DIR=HERE)
REL = 1e-6


def _find_server_src():
    cands = [os.environ.get("PDM_SERVER_SRC"), os.path.join(os.environ.get("FC_SRC", "/nonexistent"), "tools", "pdm-server")]
    d = HERE
    for _ in range(8):  # the build copy lives in <tree>/build/<preset>/Mod/PDM/..., the source in <tree>/tools
        d = os.path.dirname(d)
        cands.append(os.path.join(d, "tools", "pdm-server"))
    for c in cands:
        if c and os.path.isfile(os.path.join(c, "stack.py")):
            return c
    return None


SERVER_SRC = _find_server_src()
if SERVER_SRC:
    sys.path.insert(0, SERVER_SRC)
    import stack as S
    SKIP = S.missing()
else:
    S = None
    SKIP = "tools/pdm-server not found (set PDM_SERVER_SRC)"


def _spawn(ops, tmp, env):
    fd, path = tempfile.mkstemp(suffix=".json", dir=tmp)
    with os.fdopen(fd, "w") as f:
        json.dump(ops, f)
    return subprocess.Popen([CMD, "-c", f"exec(open({WORKER!r}).read())"], env=dict(ENV, PDM_OPS_FILE=path, **env),
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _collect(proc, timeout=300):
    out, errs = proc.communicate(timeout=timeout)
    for line in out.splitlines():
        if line.startswith("PDMRESULT:"):
            return json.loads(line[len("PDMRESULT:"):])
    raise AssertionError(f"client printed no result\nstdout: {out[-1500:]}\nstderr: {errs[-1500:]}")


def call(m, *a, **k):
    return {"op": "call", "m": m, "a": list(a), "k": k}


@unittest.skipIf(SKIP, SKIP or "")
class ServerCase(unittest.TestCase):
    stack = None
    counter = 0

    @classmethod
    def setUpClass(cls):
        lib = os.path.dirname(os.path.dirname(V.__file__))
        cls.stack = S.Stack(lib)
        try:
            cls.stack.start()
        except BaseException:
            cls.stack.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        if cls.stack:
            cls.stack.stop()

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="pdmc"))  # a symlinked temp path breaks relative link paths
        ServerCase.counter += 1
        self.doc = f"part{ServerCase.counter}"  # unique per test: the stack is shared by the module

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- helpers ------------------------------------------------------------------------------

    def wc(self, user, tag=""):
        return os.path.join(self.tmp, "wc-" + user + tag)

    def spawn(self, user, ops, fresh=False, tag="", fault=None, token=None, extra=None):
        env = self.stack.env_for(user)
        if token is not None:
            env["PDM_TOKEN"] = token
        if fault:
            env[V.FAULT_ENV] = fault
        env.update(extra or {})
        wc = self.wc(user, tag)
        head = ([{"op": "clone", "vault": self.stack.url, "wc": wc, "user": user}] if fresh
                else [{"op": "open", "wc": wc, "user": user}])
        return _spawn(head + ops, self.tmp, env)

    def client(self, user, ops, **kw):
        """Run ``ops`` as ``user`` in a new FreeCADCmd process; returns the op results (the clone/open result dropped)."""
        return _collect(self.spawn(user, ops, **kw))[1:]

    def seed(self, name=None, length=10, user="alice"):
        name = name or self.doc
        r = self.client(user, [{"op": "build_pad", "name": name, "length": length}, call("add_document", name)],
                        fresh=not os.path.isdir(self.wc(user)))
        self.assertTrue(r[1]["ok"], r)
        self.assertEqual(r[1]["value"]["revision"], "A.1")
        return r[0]["metrics"]

    def api(self, method, path, user=None, token=None, body=None):
        return S.http(method, self.stack.url + path, token or (self.stack.tokens[user] if user else None), body)

    def lock_of(self, name=None):
        st, locks = self.api("GET", "/locks", "carol")
        self.assertEqual(st, 200)
        return locks.get(name or self.doc)

    def head(self, name=None):
        """(revision, state) of the document's newest record as the reader carol sees it, from a fresh process."""
        r = self.client("carol", [call("history", name or self.doc)], fresh=True, tag="-head")
        shutil.rmtree(self.wc("carol", "-head"), ignore_errors=True)
        return r[0]["value"][0]["revision"], r[0]["value"][0]["state"]


# ---- locks ----------------------------------------------------------------------------------------

class TestLocks(ServerCase):
    def test_01_concurrent_checkout_refusal_names_holder(self):
        self.seed()
        r = self.client("alice", [call("checkout", self.doc)])
        self.assertTrue(r[0]["ok"], r)
        self.assertEqual(self.lock_of()["holder"], "alice")
        r = self.client("bob", [call("checkout", self.doc), call("checkin", self.doc, "sneak")], fresh=True)
        for res in r:
            self.assertEqual(res["error"], "LockError", res)
            self.assertEqual(res["holder"], "alice")
            self.assertIn("alice", res["message"])

    def test_02_same_user_second_working_copy_refused(self):
        self.seed()
        self.client("alice", [call("checkout", self.doc)])
        r = self.client("alice", [call("checkout", self.doc)], fresh=True, tag="-2")
        self.assertEqual(r[0]["error"], "LockError", r)
        self.assertEqual(r[0]["holder"], "alice")
        self.assertIn("another working copy", r[0]["message"])

    def test_03_release_then_other_user_checks_out(self):
        self.seed()
        self.client("alice", [call("checkout", self.doc)])
        r = self.client("bob", [call("release_lock", self.doc)], fresh=True)
        self.assertEqual(r[0]["error"], "LockError", r)  # not bob's to release
        self.client("alice", [call("release_lock", self.doc)])
        self.assertIsNone(self.lock_of())
        r = self.client("bob", [call("checkout", self.doc)])
        self.assertTrue(r[0]["ok"], r)
        self.assertEqual(self.lock_of()["holder"], "bob")

    def test_04_checkin_releases_lock_next_user_sees_revision(self):
        m1 = self.seed()
        r = self.client("alice", [call("checkout", self.doc), {"op": "set_pad", "name": self.doc, "length": 12},
                                  call("checkin", self.doc, "longer pad")])
        self.assertEqual(r[2]["value"]["revision"], "A.2")
        self.assertIsNone(self.lock_of())
        r = self.client("bob", [call("checkout", self.doc), {"op": "revision_metrics", "name": self.doc, "revision": "A.2"}],
                        fresh=True)
        self.assertTrue(r[0]["ok"], r)
        self.assertAlmostEqual(r[1]["metrics"]["volume"] / m1["volume"], 1.2, places=6)

    def test_05_lock_race_two_clients_20_of_20(self):
        self.seed()
        sync = os.path.join(self.tmp, "sync")
        os.makedirs(sync)
        race = {"op": "race_loop", "dir": sync, "doc": self.doc, "trials": 20, "n": 2}
        pa = self.spawn("alice", [race], fresh=True, tag="-race")
        pb = self.spawn("bob", [race], fresh=True, tag="-race")
        ra, rb = _collect(pa)[1], _collect(pb)[1]
        self.assertTrue(ra["ok"] and rb["ok"], (ra, rb))
        for t, (a, b) in enumerate(zip(ra["won"], rb["won"])):
            self.assertEqual(a + b, 1, f"trial {t}: alice {a}, bob {b}")
        self.assertEqual(sum(ra["won"]) + sum(rb["won"]), 20)
        self.assertIsNone(self.lock_of())

    def test_06_lock_race_three_clients(self):
        self.seed()
        sync = os.path.join(self.tmp, "sync")
        os.makedirs(sync)
        race = {"op": "race_loop", "dir": sync, "doc": self.doc, "trials": 6, "n": 3}
        procs = [self.spawn(u, [race], fresh=True, tag="-race") for u in ("alice", "bob", "dora")]
        res = [_collect(p)[1] for p in procs]
        for t in range(6):
            self.assertEqual(sum(r["won"][t] for r in res), 1, f"trial {t}: {[r['won'][t] for r in res]}")

    def test_12_lock_survives_client_restart(self):
        self.seed()
        first = self.client("alice", [call("checkout", self.doc)])[0]["ok"]
        self.assertTrue(first)
        sha = self.lock_of()["sha"]
        r = self.client("alice", [call("lock_info", self.doc)])  # a new process, same working copy
        self.assertEqual(r[0]["value"]["holder"], "alice")
        self.assertEqual(r[0]["value"]["sha"], sha)
        self.assertTrue(self.client("alice", [call("checkout", self.doc)])[0]["ok"])  # idempotent for the holder
        self.assertEqual(self.lock_of()["sha"], sha)

    def test_13_lock_survives_service_restart(self):
        self.seed()
        self.client("alice", [call("checkout", self.doc)])
        sha = self.lock_of()["sha"]
        self.stack.restart_service()
        self.assertEqual(self.lock_of()["sha"], sha)
        r = self.client("bob", [call("checkout", self.doc)], fresh=True)
        self.assertEqual(r[0]["error"], "LockError", r)
        self.assertEqual(r[0]["holder"], "alice")

    def test_14_lock_survives_postgres_crash_restart(self):
        self.seed()
        self.client("alice", [call("checkout", self.doc)])
        sha = self.lock_of()["sha"]
        self.stack.restart_pg("immediate")  # a crash: no shutdown checkpoint, recovery from the WAL
        self.assertEqual(self.lock_of()["sha"], sha)
        r = self.client("bob", [call("checkout", self.doc)], fresh=True)
        self.assertEqual(r[0]["error"], "LockError", r)
        self.assertEqual(r[0]["holder"], "alice")

    def test_31_non_holder_cannot_release_lock(self):
        self.seed()
        self.client("alice", [call("checkout", self.doc)])
        lock_id = self.lock_of()["sha"]
        r = self.client("bob", [call("release_lock", self.doc)], fresh=True)
        self.assertEqual(r[0]["error"], "LockError", r)
        self.assertEqual(r[0]["holder"], "alice")
        st, body = self.api("DELETE", f"/locks/{self.doc}?lock_id={lock_id}", "bob")  # and not around the library
        self.assertEqual(st, 403, body)
        self.assertEqual(self.lock_of()["holder"], "alice")


# ---- revisions, integrity -------------------------------------------------------------------------

class TestRevisions(ServerCase):
    def test_07_history_across_users(self):
        self.seed()
        self.client("alice", [call("checkout", self.doc), {"op": "set_pad", "name": self.doc, "length": 12},
                              call("checkin", self.doc, "longer pad")])
        r = self.client("bob", [call("history", self.doc)], fresh=True)
        hist = r[0]["value"]
        self.assertEqual([h["revision"] for h in hist], ["A.2", "A.1"])
        self.assertEqual([h["author"] for h in hist], ["alice", "alice"])
        self.assertEqual(hist[0]["message"], "longer pad")

    def test_08_old_revision_opens_read_only_across_users(self):
        m1 = self.seed(length=10)
        self.client("alice", [call("checkout", self.doc), {"op": "set_pad", "name": self.doc, "length": 25},
                              call("checkin", self.doc, "much longer")])
        r = self.client("bob", [{"op": "revision_metrics", "name": self.doc, "revision": "A.1"},
                                {"op": "revision_metrics", "name": self.doc, "revision": "A.2"}], fresh=True)
        old, new = r[0]["metrics"], r[1]["metrics"]
        self.assertAlmostEqual(old["volume"], m1["volume"], delta=REL * m1["volume"])
        self.assertAlmostEqual(new["volume"] / old["volume"], 2.5, places=6)
        self.assertFalse(os.access(os.path.join(r[0]["metrics"]["filename"], "Document.xml"), os.W_OK))

    def test_09_stale_checkin_refused(self):
        self.seed()
        self.client("bob", [call("checkout", self.doc), {"op": "set_pad", "name": self.doc, "length": 99},
                            call("release_lock", self.doc)], fresh=True)
        r = self.client("alice", [call("checkout", self.doc), {"op": "set_pad", "name": self.doc, "length": 12},
                                  call("checkin", self.doc, "alice A.2")])
        self.assertEqual(r[2]["value"]["revision"], "A.2")
        r = self.client("bob", [call("lock", self.doc), call("checkin", self.doc, "bob stale")])
        self.assertTrue(r[0]["ok"], r)
        self.assertEqual(r[1]["error"], "StaleRevisionError", r)
        self.assertEqual(self.head(), ("A.2", "WIP"))

    def test_10_where_used_after_move_seen_by_other_user(self):
        part, asm = self.doc, self.doc + "asm"
        self.seed(part)
        r = self.client("alice", [{"op": "build_link", "name": asm, "targets": [part]}, call("add_document", asm)])
        self.assertTrue(r[1]["ok"], r)
        r = self.client("bob", [call("where_used", part)], fresh=True)
        self.assertEqual([u["document"] for u in r[0]["value"]], [asm])
        new = part + "b"
        r = self.client("alice", [call("move", part, new)])
        self.assertEqual(r[0]["value"]["relinked"][0]["document"], asm, r)
        r = self.client("bob", [call("where_used", new), call("where_used", part),
                                call("checkout", asm), call("checkout", new), {"op": "link_check", "name": asm}])
        self.assertEqual([u["document"] for u in r[0]["value"]], [asm])
        self.assertEqual(r[1]["error"], "DocumentError", r)  # the old name is gone
        link = r[4]["links"][0]
        self.assertTrue(link["resolved"] and link["valid"], r[4])
        self.assertEqual(link["target_file"], new + V.DOC_SUFFIX)

    def test_11_move_refused_while_other_user_holds_referencing_lock(self):
        part, asm = self.doc, self.doc + "asm"
        self.seed(part)
        self.client("alice", [{"op": "build_link", "name": asm, "targets": [part]}, call("add_document", asm)])
        self.assertTrue(self.client("bob", [call("checkout", asm)], fresh=True)[0]["ok"])
        r = self.client("alice", [call("move", part, part + "b")])
        self.assertEqual(r[0]["error"], "LockError", r)
        self.assertEqual(r[0]["holder"], "bob")
        r = self.client("carol", [call("documents")], fresh=True)
        self.assertIn(part, r[0]["value"])
        self.assertNotIn(part + "b", r[0]["value"])
        self.assertIsNone(self.lock_of(part))

    def test_29_lifecycle_across_users(self):
        self.seed()
        r = self.client("alice", [call("transition", self.doc, "Released")])
        self.assertEqual(r[0]["error"], "LifecycleError", r)  # WIP -> Released is not in the table
        r = self.client("alice", [call("transition", self.doc, "InReview"), call("transition", self.doc, "Released")])
        self.assertEqual(r[1]["value"]["state"], "Released", r)
        r = self.client("bob", [call("checkout", self.doc), call("checkout", self.doc, new_revision=True),
                                {"op": "set_pad", "name": self.doc, "length": 15},
                                call("checkin", self.doc, "next revision")], fresh=True)
        self.assertEqual(r[0]["error"], "LifecycleError", r)
        self.assertEqual(r[3]["value"]["revision"], "B.1", r)

    def test_30_transition_refused_while_other_user_holds_lock(self):
        self.seed()
        self.client("alice", [call("checkout", self.doc)])
        r = self.client("bob", [call("transition", self.doc, "InReview")], fresh=True)
        self.assertEqual(r[0]["error"], "LockError", r)
        self.assertEqual(r[0]["holder"], "alice")
        self.assertEqual(self.head(), ("A.1", "WIP"))

    def test_24_failed_push_keeps_revision_and_lock(self):
        self.seed()
        self.client("alice", [call("checkout", self.doc), {"op": "set_pad", "name": self.doc, "length": 14}])
        sha = self.lock_of()["sha"]
        for point in ("before_push", "after_commit"):
            r = self.client("alice", [call("checkin", self.doc, "doomed")], fault=point)
            self.assertEqual(r[0]["error"], "GitError", r)
            self.assertEqual(self.head(), ("A.1", "WIP"))
            self.assertEqual(self.lock_of()["sha"], sha)
        r = self.client("alice", [call("checkin", self.doc, "retry")])
        self.assertEqual(r[0]["value"]["revision"], "A.2")
        self.assertIsNone(self.lock_of())

    def test_23_service_outage_during_checkin(self):
        self.seed()
        self.client("alice", [call("checkout", self.doc), {"op": "set_pad", "name": self.doc, "length": 14}])
        sha = self.lock_of()["sha"]
        ready, go = os.path.join(self.tmp, "ready"), os.path.join(self.tmp, "go")
        proc = self.spawn("alice", [{"op": "outage_checkin", "name": self.doc, "message": "doomed", "ready": ready, "go": go}])
        for _ in range(600):  # the client has built its commit and stands just before the push
            if os.path.exists(ready):
                break
            subprocess.run(["sleep", "0.1"])
        self.assertTrue(os.path.exists(ready))
        self.stack.stop_service()
        open(go, "w").close()
        r = _collect(proc)[1]
        self.assertEqual(r["error"], "ServerUnavailable", r)
        self.stack.start_service()
        self.assertEqual(self.head(), ("A.1", "WIP"))
        self.assertEqual(self.lock_of()["sha"], sha)
        r = self.client("alice", [call("checkin", self.doc, "after the outage")])  # the working copy kept the edit
        self.assertEqual(r[0]["value"]["revision"], "A.2", r)


# ---- access ---------------------------------------------------------------------------------------

class TestAccess(ServerCase):
    def test_15_unauthenticated_requests_refused(self):
        self.seed()
        for path in ("/locks", "/whoami", "/config", f"/where-used/{self.doc}", "/audit"):
            self.assertEqual(self.api("GET", path)[0], 401, path)
        self.assertEqual(self.api("POST", f"/locks/{self.doc}", body={})[0], 401)
        self.assertEqual(self.api("GET", "/whoami", token="not-a-token")[0], 401)
        self.assertEqual(self.api("POST", "/internal/pre-receive", body={"pusher": "alice", "updates": []})[0], 401)
        # a client process without a token, and with a bad one, cannot even clone
        for token in ("", "garbage"):
            res = _collect(self.spawn("alice", [], fresh=True, token=token))
            self.assertIn("AuthError", res[0].get("crash", ""), res)
        p = subprocess.run(["git", "ls-remote", self.stack.git_url], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           env=dict(os.environ, GIT_TERMINAL_PROMPT="0", HOME=self.tmp))
        self.assertNotEqual(p.returncode, 0)  # the vault host itself refuses anonymous reads

    def test_16_token_of_another_user_refused(self):
        self.seed()
        res = _collect(self.spawn("alice", [], fresh=True, tag="-x", token=self.stack.tokens["bob"]))
        self.assertIn("AuthError", res[0].get("crash", ""), res)
        self.assertIn("bob", res[0]["crash"])

    def test_17_reader_can_list_history_and_open(self):
        m1 = self.seed()
        self.client("alice", [call("checkout", self.doc)])
        r = self.client("carol", [call("documents"), call("history", self.doc), call("locks"), call("where_used", self.doc),
                                  {"op": "revision_metrics", "name": self.doc, "revision": "A.1"}, {"op": "whoami"}],
                        fresh=True)
        self.assertIn(self.doc, r[0]["value"])
        self.assertEqual(r[1]["value"][0]["revision"], "A.1")
        self.assertEqual(r[2]["value"][self.doc]["holder"], "alice")
        self.assertEqual(r[3]["value"], [])
        self.assertAlmostEqual(r[4]["metrics"]["volume"], m1["volume"], delta=REL * m1["volume"])
        self.assertEqual(r[5]["value"], {"user": "carol", "role": "reader"})

    def test_18_reader_cannot_check_out(self):
        self.seed()
        r = self.client("carol", [call("checkout", self.doc)], fresh=True)
        self.assertEqual(r[0]["error"], "PermissionDenied", r)
        self.assertIsNone(self.lock_of())
        self.assertFalse(os.path.isdir(os.path.join(self.wc("carol"), self.doc + V.DOC_SUFFIX)))
        self.assertEqual(self.api("POST", f"/locks/{self.doc}", "carol", body={})[0], 403)

    def test_19_reader_cannot_check_in(self):
        self.seed()
        r = self.client("carol", [call("checkin", self.doc, "no"),
                                  {"op": "raw_push", "name": self.doc, "event": "checkin", "revision": "A.2",
                                   "materialize": True, "append": "<!-- carol -->"}], fresh=True)
        self.assertEqual(r[0]["error"], "LockError", r)  # the library: not checked out
        self.assertEqual(r[1]["error"], "PermissionDenied", r)  # around the library: the vault host refuses
        self.assertEqual(self.head(), ("A.1", "WIP"))

    def test_20_reader_cannot_transition(self):
        self.seed()
        r = self.client("carol", [call("transition", self.doc, "InReview")], fresh=True)
        self.assertEqual(r[0]["error"], "PermissionDenied", r)
        self.assertEqual(self.head(), ("A.1", "WIP"))
        r = self.client("alice", [call("transition", self.doc, "InReview")])  # a writer can
        self.assertEqual(r[0]["value"]["state"], "InReview", r)

    def test_21_lock_break_admin_only(self):
        self.seed()
        self.client("alice", [call("checkout", self.doc)])
        r = self.client("bob", [call("break_lock", self.doc)], fresh=True)
        self.assertEqual(r[0]["error"], "PermissionDenied", r)
        self.assertEqual(self.api("POST", f"/locks/{self.doc}/break", "carol")[0], 403)
        self.assertEqual(self.lock_of()["holder"], "alice")
        r = self.client("dora", [call("break_lock", self.doc)], fresh=True)
        self.assertEqual(r[0]["value"]["holder"], "alice", r)
        self.assertIsNone(self.lock_of())
        self.assertTrue(self.client("bob", [call("checkout", self.doc)])[0]["ok"])

    def test_22_lock_break_recorded_in_audit_log(self):
        self.seed()
        self.client("alice", [call("checkout", self.doc)])
        self.client("dora", [call("break_lock", self.doc)], fresh=True)
        st, rows = self.api("GET", "/audit", "dora")
        self.assertEqual(st, 200)
        brk = [r for r in rows if r["action"] == "break" and r["document"] == self.doc]
        self.assertEqual(len(brk), 1, rows[:5])
        self.assertEqual(brk[0]["actor"], "dora")
        self.assertEqual(brk[0]["detail"]["former_holder"], "alice")
        self.assertEqual(self.api("GET", "/audit", "bob")[0], 403)  # the log is for admins

    def test_25_push_without_lock_refused_by_vault_host(self):
        self.seed()
        r = self.client("bob", [{"op": "raw_push", "name": self.doc, "event": "checkin", "revision": "A.2",
                                 "materialize": True, "append": "<!-- sneak -->"}], fresh=True)
        self.assertEqual(r[0].get("error"), "PermissionDenied", r)
        self.assertIn("the lock on", r[0]["message"])
        self.assertEqual(self.head(), ("A.1", "WIP"))

    def test_26_push_while_other_user_holds_lock_refused(self):
        self.seed()
        self.client("alice", [call("checkout", self.doc)])
        r = self.client("bob", [{"op": "raw_push", "name": self.doc, "event": "checkin", "revision": "A.2",
                                 "materialize": True, "append": "<!-- sneak -->"}], fresh=True)
        self.assertEqual(r[0]["error"], "PermissionDenied", r)
        self.assertIn("held by alice", r[0]["message"])
        self.assertEqual(self.head(), ("A.1", "WIP"))

    def test_27_push_touching_other_document_refused(self):
        other = self.doc + "other"
        self.seed()
        self.seed(other)
        self.client("alice", [call("checkout", self.doc), call("checkout", other)])
        # alice owns both locks but then releases the second: a check-in of the first may not carry the second's folder
        self.client("alice", [call("release_lock", other)])
        r = self.client("alice", [{"op": "raw_push", "name": other, "event": "checkin", "revision": "A.2",
                                   "materialize": True, "append": "<!-- x -->", "message": "unlocked"}])
        self.assertEqual(r[0]["error"], "PermissionDenied", r)  # no lock on `other`
        r = self.client("alice", [{"op": "raw_push", "name": self.doc, "event": "checkin", "revision": "A.2",
                                   "append": "<!-- y -->", "also_touch": other}])
        self.assertEqual(r[0]["error"], "PermissionDenied", r)
        self.assertIn("outside what that event may touch", r[0]["message"])
        self.assertEqual(self.head(), ("A.1", "WIP"))
        self.assertEqual(self.head(other), ("A.1", "WIP"))

    def test_28_forged_illegal_transition_refused(self):
        self.seed()
        r = self.client("alice", [{"op": "raw_push", "name": self.doc, "event": "transition", "revision": "A.1",
                                   "state": "Released", "with_dir": False}])
        self.assertEqual(r[0]["error"], "PermissionDenied", r)
        self.assertIn("illegal transition WIP -> Released", r[0]["message"])
        self.assertEqual(self.head(), ("A.1", "WIP"))

    def test_32_gui_connect_against_server(self):
        from PDMGui import state  # the slice 5 connection layer; it imports no PySide
        self.seed()
        self.client("bob", [call("checkout", self.doc)], fresh=True)  # bob, in his own process, holds the lock
        saved = {k: os.environ.get(k) for k in ("PDM_SERVER_URL", "PDM_TOKEN")}
        os.environ["PDM_TOKEN"] = self.stack.tokens["alice"]
        os.environ.pop("PDM_SERVER_URL", None)  # the URL alone, as typed in the Connect dialog, must be enough
        try:
            v = state.connect(self.stack.url + "/", self.wc("alice", "-gui"), "alice", save=False)
            self.assertEqual(type(v).__name__, "RemoteVault")
            self.assertIn(self.doc, v.documents())
            with self.assertRaises(V.LockError) as cm:
                v.checkout(self.doc)
            self.assertEqual(cm.exception.holder, "bob")
            self.assertEqual(state.connect(self.stack.url, self.wc("alice", "-gui"), "alice", save=False).user, "alice")
            self.assertEqual(v.lock_info(self.doc)["holder"], "bob")
        finally:
            state.disconnect()
            for k, val in saved.items():
                if val is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = val


if __name__ == "__main__":
    unittest.main()
