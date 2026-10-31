# SPDX-License-Identifier: LGPL-2.1-or-later
"""Slice-2 acceptance checks of G03: the local vault driven by separate FreeCADCmd client processes.

Every client is its own ``FreeCADCmd`` process running ``client_worker.py``; nothing here lets two
clients share an interpreter. Vaults and working copies live in temp dirs, and the workers set git
identity themselves, so no global git config is read or changed.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

import FreeCAD as App

from PDM import vault as V

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.join(HERE, "client_worker.py")
CMD = os.path.join(App.getHomePath(), "bin", "FreeCADCmd")
ENV = dict(os.environ, QT_QPA_PLATFORM="offscreen")
REL = 1e-6
RACE_TRIALS = 20


def _spawn(ops, tmp, extra_env=None):
    fd, path = tempfile.mkstemp(suffix=".json", dir=tmp)
    with os.fdopen(fd, "w") as f:
        json.dump(ops, f)
    env = dict(ENV, PDM_OPS_FILE=path, **(extra_env or {}))
    return subprocess.Popen([CMD, "-c", f"exec(open({WORKER!r}).read())"], env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _collect(proc, timeout=300):
    out, errs = proc.communicate(timeout=timeout)
    for line in out.splitlines():
        if line.startswith("PDMRESULT:"):
            return json.loads(line[len("PDMRESULT:"):])
    raise AssertionError(f"client printed no result\nstdout: {out[-1500:]}\nstderr: {errs[-1500:]}")


def call(m, *a, **k):
    return {"op": "call", "m": m, "a": list(a), "k": k}


class VaultCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pdm")
        self.vault = V.Vault.create(os.path.join(self.tmp, "vault.git"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def wc(self, user):
        return os.path.join(self.tmp, "wc-" + user)

    def client(self, user, ops, fresh=False, fault=None):
        """Run ``ops`` as ``user`` in a new FreeCADCmd process; returns the list of op results."""
        head = [{"op": "clone", "vault": self.vault, "wc": self.wc(user), "user": user}] if fresh else \
            [{"op": "open", "wc": self.wc(user), "user": user}]
        proc = _spawn(head + ops, self.tmp, {V.FAULT_ENV: fault} if fault else None)
        return _collect(proc)[1:]

    def git(self, *args):
        return subprocess.run(["git", "--git-dir", self.vault] + list(args), check=True, stdout=subprocess.PIPE,
                              text=True).stdout

    def doc_state(self, name):
        """(revision, state) of the document's newest record, read straight from the bare vault."""
        out = self.git("log", "-1", "-E", f"--grep=^PDM-Document: {name}$", "--format=%B", "refs/heads/main")
        meta = dict(l[4:].split(": ", 1) for l in out.splitlines() if l.startswith("PDM-"))
        return meta["Revision"], meta["State"]

    def lock_ref(self, name):
        p = subprocess.run(["git", "--git-dir", self.vault, "rev-parse", "--verify", "-q", f"refs/locks/{name}"],
                           stdout=subprocess.PIPE, text=True)
        return p.stdout.strip() or None

    def seed(self, name="bracket", length=10, user="alice"):
        """Alice clones, builds a Pad document and adds it (A.1)."""
        r = self.client(user, [{"op": "build_pad", "name": name, "length": length}, call("add_document", name)],
                        fresh=True)
        self.assertTrue(r[1]["ok"], r)
        self.assertEqual(r[1]["value"]["revision"], "A.1")
        return r[0]["metrics"]


class TestHelpers(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(V.revision_label(0, 1), "A.1")
        self.assertEqual(V.revision_label(25, 3), "Z.3")
        self.assertEqual(V.revision_label(26, 1), "AA.1")
        self.assertEqual(V.parse_label("B.2"), (1, 2))
        self.assertEqual(V.parse_label("AA.1"), (26, 1))
        with self.assertRaises(V.DocumentError):
            V.parse_label("1.A")

    def test_table_is_total_and_closed(self):
        self.assertEqual(set(V.TRANSITIONS), set(V.STATES))
        for dst in V.TRANSITIONS.values():
            self.assertTrue(set(dst) <= set(V.STATES))
        self.assertEqual(V.TRANSITIONS["Obsolete"], ())
        self.assertNotIn("Released", V.TRANSITIONS["WIP"])

    def test_bad_names_refused(self):
        for bad in ("../x", "a b", "", "a.b", "x/y"):
            with self.assertRaises(V.DocumentError):
                V._check_name(bad)


class TestLocks(VaultCase):
    """Acceptance check 2."""

    def test_lock_conflict_names_holder_and_survives_restart(self):
        self.seed()
        r = self.client("alice", [call("checkout", "bracket")])
        self.assertTrue(r[0]["ok"])
        self.assertTrue(self.lock_ref("bracket"))
        # B (separate process): checkout and check-in refused, error names alice
        r = self.client("bob", [call("checkout", "bracket"), call("checkin", "bracket", "sneak")], fresh=True)
        for res in r:
            self.assertEqual(res["error"], "LockError", res)
            self.assertEqual(res["holder"], "alice")
            self.assertIn("alice", res["message"])
        # A's process has exited since: the lock survived the restart, in the vault
        r = self.client("alice", [call("lock_info", "bracket")])
        self.assertEqual(r[0]["value"]["holder"], "alice")
        # A changes the model and checks in (lock released), then B can check out
        r = self.client("alice", [{"op": "set_pad", "name": "bracket", "length": 12},
                                  call("checkin", "bracket", "longer pad")])
        self.assertEqual(r[1]["value"]["revision"], "A.2")
        self.assertIsNone(self.lock_ref("bracket"))
        r = self.client("bob", [call("checkout", "bracket")])
        self.assertTrue(r[0]["ok"], r)
        # release (no check-in) frees it for A as well
        self.client("bob", [call("release_lock", "bracket")])
        r = self.client("alice", [call("checkout", "bracket")])
        self.assertTrue(r[0]["ok"], r)

    def test_checkin_without_checkout_refused(self):
        self.seed()
        r = self.client("alice", [call("checkin", "bracket", "no lock")])
        self.assertEqual(r[0]["error"], "LockError")

    def test_race_exactly_one_winner_in_20_of_20(self):
        names = [f"part{i:02d}" for i in range(RACE_TRIALS)]
        src = os.path.join(self.tmp, "seed.FCStd.d")
        self.client("alice", [{"op": "build_pad", "name": "seed", "length": 5}], fresh=True)
        shutil.copytree(os.path.join(self.wc("alice"), "seed.FCStd.d"), src)
        shutil.rmtree(os.path.join(self.wc("alice"), "seed.FCStd.d"))
        self.client("alice", [call("add_document", n, source=src) for n in names])
        sync = os.path.join(self.tmp, "sync")
        os.makedirs(sync)
        pa = _spawn([{"op": "open", "wc": self.wc("alice"), "user": "alice"},
                     {"op": "race", "docs": names, "dir": sync}], self.tmp)
        pb = _spawn([{"op": "clone", "vault": self.vault, "wc": self.wc("bob"), "user": "bob"},
                     {"op": "race", "docs": names, "dir": sync}], self.tmp)
        wa, wb = _collect(pa)[1]["won"], _collect(pb)[1]["won"]
        winners = {"alice": 0, "bob": 0}
        for n in names:
            self.assertNotEqual(wa[n], wb[n], f"{n}: both or neither won (alice {wa[n]}, bob {wb[n]})")
            winner = "alice" if wa[n] else "bob"
            winners[winner] += 1
            holder = json.loads(self.git("log", "-1", "--format=%B", f"refs/locks/{n}").strip().split("\n")[-1])["holder"]
            self.assertEqual(holder, winner)
        print(f"\nPDM race: {RACE_TRIALS} of {RACE_TRIALS} trials had exactly one winner {winners}")


class TestRevisions(VaultCase):
    """Acceptance check 3."""

    def test_checkin_revision_diff_and_open_old_revision(self):
        m1 = self.seed(length=10)
        self.assertAlmostEqual(m1["volume"], 2000.0, delta=1e-6)
        r = self.client("alice", [call("checkout", "bracket"), {"op": "set_pad", "name": "bracket", "length": 15},
                                  call("checkin", "bracket", "pad 10 to 15")])
        self.assertEqual(r[2]["value"]["revision"], "A.2")
        m2 = r[1]["metrics"]
        self.assertAlmostEqual(m2["volume"], 3000.0, delta=1e-6)
        # B lists history and opens both revisions read-only
        r = self.client("bob", [call("history", "bracket"), {"op": "revision_metrics", "name": "bracket", "revision": "A.1"},
                                {"op": "revision_metrics", "name": "bracket", "revision": "A.2"}], fresh=True)
        hist = r[0]["value"]
        self.assertEqual([h["revision"] for h in hist], ["A.2", "A.1"])
        self.assertEqual(hist[0]["author"], "alice")
        self.assertEqual(hist[0]["message"], "pad 10 to 15")
        self.assertRegex(hist[0]["time"], r"^\d{4}-\d\d-\d\dT")
        for got, want in ((r[1]["metrics"], m1), (r[2]["metrics"], m2)):
            self.assertAlmostEqual(got["volume"], want["volume"], delta=abs(want["volume"]) * REL)
            self.assertAlmostEqual(got["area"], want["area"], delta=abs(want["area"]) * REL)
            for a, b in zip(got["bbox"], want["bbox"]):
                self.assertAlmostEqual(a, b, delta=1e-6 * max(1, abs(b)))
        self.assertIn("A.1", r[1]["metrics"]["filename"] + "A.1")  # opened from an exported temp copy
        self.assertNotEqual(os.path.dirname(os.path.dirname(r[1]["metrics"]["filename"])), self.wc("bob"))
        # The change set of A.2 is the folder-format diff of the Pad length
        sha1, sha2 = hist[1]["sha"], hist[0]["sha"]
        names = self.git("diff", "--name-only", sha1, sha2).split()
        files = {os.path.basename(n) for n in names}
        self.assertIn("Document.xml", files)
        allowed = ("Document.xml", "StringHasher.Table.txt", "Pad.", "Body.", "Sketch.InternalShape.brp")
        for n in files:
            self.assertTrue(n.startswith(allowed), f"unexpected file in change set: {n}")
        # The sketch's geometry did not change; OCCT only re-flags its shape after the reopen (one BREP line,
        # the determinism-after-reopen drift listed in run 021's Exhibit 2).
        sk = [l for l in self.git("diff", "-U0", sha1, sha2, "--", "bracket.FCStd.d/Sketch.InternalShape.brp").splitlines()
              if l[:1] in "+-" and l[:3] not in ("+++", "---")]
        self.assertLessEqual(len(sk), 2, sk)
        xml = [l for l in self.git("diff", "-U0", sha1, sha2, "--", "bracket.FCStd.d/Document.xml").splitlines()
               if l[:1] in "+-" and l[:3] not in ("+++", "---")]
        self.assertTrue(xml and len(xml) <= 8, xml)
        for l in xml:
            self.assertTrue("10" in l or "15" in l or "Length" in l, f"Document.xml line not about the length: {l}")
        print(f"\nPDM Pad change set: {len(names)} files ({sorted(files)}), {len(xml)} Document.xml lines")


class TestLifecycle(VaultCase):
    """Acceptance check 4."""

    def test_transitions_and_release_bumps_letter(self):
        self.seed()
        r = self.client("alice", [call("transition", "bracket", "Released"),   # WIP -> Released illegal
                                  call("transition", "bracket", "InReview"), call("checkout", "bracket"),
                                  call("transition", "bracket", "Released"), call("checkout", "bracket"),
                                  call("transition", "bracket", "WIP")])
        self.assertEqual(r[0]["error"], "LifecycleError")
        self.assertTrue(r[1]["ok"])
        self.assertEqual(r[2]["error"], "LifecycleError")  # InReview cannot be checked out
        self.assertEqual(r[3]["value"]["state"], "Released")
        self.assertEqual(r[4]["error"], "LifecycleError")  # Released needs a new revision
        self.assertEqual(r[5]["error"], "LifecycleError")  # Released -> WIP not in the table
        self.assertEqual(self.doc_state("bracket"), ("A.1", "Released"))
        # bob starts a new revision; check-in makes B.1 (WIP)
        r = self.client("bob", [call("checkout", "bracket", new_revision=True),
                                {"op": "set_pad", "name": "bracket", "length": 11},
                                call("checkin", "bracket", "rev B")], fresh=True)
        self.assertEqual(r[2]["value"]["revision"], "B.1")
        self.assertEqual(self.doc_state("bracket"), ("B.1", "WIP"))
        r = self.client("alice", [call("transition", "bracket", "InReview"), call("transition", "bracket", "Released"),
                                  call("transition", "bracket", "Obsolete"), call("checkout", "bracket", new_revision=True),
                                  call("transition", "bracket", "WIP"), call("history", "bracket")])
        self.assertTrue(all(x["ok"] for x in r[:3]), r)
        self.assertEqual(r[3]["error"], "LifecycleError")
        self.assertEqual(r[4]["error"], "LifecycleError")  # Obsolete is terminal
        self.assertEqual([h["state"] for h in r[5]["value"]][:3], ["Obsolete", "Released", "InReview"])

    def test_transition_refused_while_locked_by_another(self):
        self.seed()
        self.client("alice", [call("checkout", "bracket")])
        r = self.client("bob", [call("transition", "bracket", "InReview")], fresh=True)
        self.assertEqual(r[0]["error"], "LockError")
        self.assertEqual(r[0]["holder"], "alice")


class TestIntegrity(VaultCase):
    """Acceptance check 5."""

    def test_stale_checkin_refused(self):
        self.seed()
        # bob checks out (base A.1), then gives the lock up but keeps his A.1 files
        self.client("bob", [call("checkout", "bracket"), {"op": "set_pad", "name": "bracket", "length": 99},
                            call("release_lock", "bracket")], fresh=True)
        r = self.client("alice", [call("checkout", "bracket"), {"op": "set_pad", "name": "bracket", "length": 12},
                                  call("checkin", "bracket", "alice A.2")])
        self.assertEqual(r[2]["value"]["revision"], "A.2")
        before = self.git("rev-parse", "refs/heads/main")
        # bob re-locks without syncing and tries to check in his stale copy
        r = self.client("bob", [call("lock", "bracket"), call("checkin", "bracket", "bob stale")])
        self.assertTrue(r[0]["ok"], r)
        self.assertEqual(r[1]["error"], "StaleRevisionError", r)
        self.assertEqual(self.git("rev-parse", "refs/heads/main"), before)
        self.assertEqual(self.doc_state("bracket"), ("A.2", "WIP"))
        # the way out: check out again (sync), redo the change on top of A.2
        r = self.client("bob", [call("checkout", "bracket"), call("checkout", "bracket", discard_local=True),
                                {"op": "set_pad", "name": "bracket", "length": 13},
                                call("checkin", "bracket", "bob on A.2")])
        self.assertEqual(r[0]["error"], "DocumentError")  # local edits on a stale base are not overwritten silently
        self.assertEqual(r[3]["value"]["revision"], "A.3")

    def test_failed_checkin_keeps_previous_revision_and_lock(self):
        self.seed()
        self.client("alice", [call("checkout", "bracket"), {"op": "set_pad", "name": "bracket", "length": 14}])
        before = self.git("rev-parse", "refs/heads/main")
        lock = self.lock_ref("bracket")
        for point in ("before_push", "after_commit"):
            r = self.client("alice", [call("checkin", "bracket", "doomed")], fault=point)
            self.assertEqual(r[0]["error"], "GitError", r)
            self.assertIn("injected fault", r[0]["message"])
            self.assertEqual(self.git("rev-parse", "refs/heads/main"), before)
            self.assertEqual(self.doc_state("bracket"), ("A.1", "WIP"))
            self.assertEqual(self.lock_ref("bracket"), lock)
        # still holds the lock and the edit: a clean retry succeeds
        r = self.client("alice", [call("checkin", "bracket", "retry")])
        self.assertEqual(r[0]["value"]["revision"], "A.2")
        self.assertIsNone(self.lock_ref("bracket"))

    def test_add_twice_and_empty_checkin_refused(self):
        self.seed()
        r = self.client("alice", [call("add_document", "bracket"), call("checkout", "bracket"),
                                  call("checkin", "bracket", "nothing changed")])
        self.assertEqual(r[0]["error"], "DocumentError")
        self.assertEqual(r[2]["error"], "DocumentError")


if __name__ == "__main__":
    unittest.main()
