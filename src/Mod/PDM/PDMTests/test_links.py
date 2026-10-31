# SPDX-License-Identifier: LGPL-2.1-or-later
"""Slice-3 acceptance checks of G03: where-used and link relocation (move) in the local vault.

Two layers. ``TestLinkLogic`` drives the vault with small synthetic folder-format documents (no FreeCAD document is
opened) to pin the scan, the textual rewrite, the refusals and the atomicity. ``TestMoveWithFreeCAD`` builds real
documents with ``App::Link`` and friends and drives separate ``FreeCADCmd`` client processes, so the scan is compared
with FreeCAD's own link graph and moved links are reopened in a fresh process. Checks 3 and 4 of the brief (open
documents, moves on disk) are in ``AgentCoreTests/test_folder_format.py::TestFolderLinkRelocation``.
"""

import os
import re
import shutil
import subprocess
import tempfile
import unittest

from PDM import vault as V

from PDMTests import test_vault as TV
from PDMTests.test_vault import call

XML = """<?xml version='1.0' encoding='utf-8'?>
<Document SchemaVersion="4">
  <Properties Count="1" TransientCount="0">
    <Property name="Comment" type="App::PropertyString"><String value=""/></Property>
  </Properties>
  <Objects Count="1"><Object type="App::Link" name="L" id="1"/></Objects>
  <ObjectData Count="1">
    <Object name="L">
      <Properties Count="2" TransientCount="0">
%s
      </Properties>
    </Object>
  </ObjectData>
</Document>
"""


def synth_xml(links):
    """``links`` is a list of (property name, XLink file attribute or None for a name-only element)."""
    props = []
    for i, (prop, f) in enumerate(links):
        el = f'<XLink name="X{i}"/>' if f is None else f'<XLink file="{f}" stamp="" name="Box" sub="Face1"/>'
        props.append(f'        <Property name="{prop}" type="App::PropertyXLinkSubList"><XLinkSubList count="1">'
                     f'{el}</XLinkSubList></Property>')
    return XML % "\n".join(props)


class TestLinkLogic(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="pdml"))
        self.vpath = V.Vault.create(os.path.join(self.tmp, "vault.git"))
        self.a = V.Vault.clone(self.vpath, os.path.join(self.tmp, "wc-a"), "alice")
        self.b = V.Vault.clone(self.vpath, os.path.join(self.tmp, "wc-b"), "bob")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def head(self):
        return subprocess.run(["git", "--git-dir", self.vpath, "rev-parse", "refs/heads/main"], check=True,
                              stdout=subprocess.PIPE, text=True).stdout.strip()

    def add(self, name, links=()):
        d = self.a.doc_path(name)
        os.makedirs(d)
        with open(os.path.join(d, "Document.xml"), "w", encoding="utf-8") as f:
            f.write(synth_xml(links))
        with open(os.path.join(d, "Shape.brp"), "w") as f:
            f.write("BREP " + name + "\n")
        return self.a.add_document(name)

    def blob(self, name, path="Document.xml", ref="refs/heads/main"):
        return subprocess.run(["git", "--git-dir", self.vpath, "show", f"{ref}:{name}.FCStd.d/{path}"], check=True,
                              stdout=subprocess.PIPE).stdout.decode()

    def locks(self):
        return subprocess.run(["git", "--git-dir", self.vpath, "for-each-ref", "--format=%(refname)", "refs/locks/"],
                              check=True, stdout=subprocess.PIPE, text=True).stdout.split()

    def test_owns_lock(self):
        self.add("base")
        self.assertFalse(self.a.owns_lock(None))
        self.a.lock("base")
        self.assertTrue(self.a.owns_lock(self.a.lock_info("base")))
        self.assertFalse(self.b.owns_lock(self.b.lock_info("base")))   # another user's lock

    def test_scan_forms(self):
        self.add("base")
        self.add("other")
        self.add("asm", [("Support", "base.FCStd.d"), ("LinkedObject", "./base.FCStd.d"), ("Upper", "BASE.FCStd.d"),
                         ("Other", "other.FCStd.d"), ("Url", "https://example.org/base.FCStd.d"),
                         ("Empty", ""), ("Local", None), ("Zip", "base.FCStd")])
        used = self.a.where_used("base")
        self.assertEqual(sorted(u["property"] for u in used), ["LinkedObject", "Support", "Upper"])
        self.assertEqual({u["document"] for u in used}, {"asm"})
        self.assertEqual([u["property"] for u in self.a.where_used("other")], ["Other"])
        self.assertEqual(self.a.where_used("asm"), [])
        with self.assertRaises(V.DocumentError):
            self.a.where_used("nothere")
        sus = []
        head = self.a._head()
        self.a._where_used_at(head, "base", sus)
        self.assertEqual(sus, [])

    def test_move_rewrites_only_the_link_attributes(self):
        self.add("base")
        self.add("other")
        self.add("asm", [("Support", "base.FCStd.d"), ("LinkedObject", "./base.FCStd.d"), ("Other", "other.FCStd.d"),
                         ("Url", "https://example.org/base.FCStd.d")])
        before = self.blob("asm")
        r = self.a.move("base", "base2")
        after = self.blob("asm")
        self.assertEqual(after, before.replace('file="base.FCStd.d"', 'file="base2.FCStd.d"')
                         .replace('file="./base.FCStd.d"', 'file="base2.FCStd.d"'))
        self.assertIn("https://example.org/base.FCStd.d", after)
        self.assertIn("other.FCStd.d", after)
        self.assertNotIn('"base.FCStd.d"', after)
        self.assertEqual(self.blob("base2", "Shape.brp"), "BREP base\n")
        names = subprocess.run(["git", "--git-dir", self.vpath, "ls-tree", "--name-only", "refs/heads/main"],
                               check=True, stdout=subprocess.PIPE, text=True).stdout.split()
        self.assertEqual(sorted(names), ["asm.FCStd.d", "base2.FCStd.d", "other.FCStd.d"])
        self.assertEqual(r["revision"], "A.1")
        self.assertEqual([x["document"] for x in r["relinked"]], ["asm"])
        self.assertEqual(r["relinked"][0]["revision"], "A.2")
        # one vault update, one commit per document, with trailers
        log = subprocess.run(["git", "--git-dir", self.vpath, "log", "-3", "--format=%B%x00", "refs/heads/main"],
                             check=True, stdout=subprocess.PIPE, text=True).stdout.split("\x00")
        self.assertIn("PDM-Event: relink", log[0])
        self.assertIn("PDM-Document: asm", log[0])
        self.assertIn("PDM-Relink: base -> base2", log[0])
        self.assertIn("PDM-Event: move", log[1])
        self.assertIn("PDM-Document: base2", log[1])
        self.assertIn("PDM-Moved-From: base", log[1])
        # no link anywhere names the old location; the scan agrees; locks are gone
        self.assertEqual(self.a._where_used_at(self.a._head(), "base"), [])
        self.assertEqual([u["property"] for u in self.a.where_used("base2")], ["Support", "LinkedObject"])
        self.assertEqual(self.locks(), [])
        # the history of base2 includes base's, the moved document is listed, the old name is not
        hist = self.a.history("base2")
        self.assertEqual([(h["event"], h["revision"]) for h in hist], [("move", "A.1"), ("add", "A.1")])
        self.assertEqual(sorted(self.a.documents()), ["asm", "base2", "other"])
        # base2 can be checked out and checked in as A.2 (state and label carried across the move)
        self.b.checkout("base2")
        with open(os.path.join(self.b.doc_path("base2"), "Shape.brp"), "a") as f:
            f.write("more\n")
        self.assertEqual(self.b.checkin("base2", "edit")["revision"], "A.2")
        self.assertTrue(os.path.isdir(self.b.export_revision("base2", "A.1")))

    def test_refused_when_another_client_holds_a_lock(self):
        self.add("base")
        self.add("asm1", [("L", "base.FCStd.d")])
        self.add("asm2", [("L", "base.FCStd.d")])
        for held in ("asm2", "base"):
            self.b.lock(held)
            head = self.head()
            with self.assertRaises(V.LockError) as cm:
                self.a.move("base", "base2")
            self.assertEqual(cm.exception.holder, "bob")
            self.assertEqual(self.head(), head, "vault changed by a refused move")
            self.assertEqual(self.locks(), [f"refs/locks/{held}"], "refused move left a lock behind")
            self.b.release_lock(held)

    def test_injected_failures_leave_vault_and_locks_unchanged(self):
        self.add("base")
        self.add("asm", [("L", "base.FCStd.d")])
        head = self.head()
        for point in ("before_push", "after_commit"):
            os.environ[V.FAULT_ENV] = point
            try:
                with self.assertRaises(V.GitError):
                    self.a.move("base", "base2")
            finally:
                del os.environ[V.FAULT_ENV]
            self.assertEqual(self.head(), head)
            self.assertEqual(self.locks(), [])
        self.assertEqual(self.a.move("base", "base2")["relinked"][0]["document"], "asm")  # and it still works

    def test_lifecycle_and_name_refusals(self):
        self.add("base")
        self.add("rev", [("L", "base.FCStd.d")])
        self.add("rel", [("L", "base.FCStd.d")])
        self.add("obs", [("L", "base.FCStd.d")])
        self.add("BASE2")
        head = self.head()
        for bad, exc in (("base2", V.DocumentError), ("base", V.DocumentError)):
            with self.assertRaises(exc):
                self.a.move("base", bad)  # case-insensitive clash with BASE2; same name
        with self.assertRaises(V.DocumentError):
            self.a.move("nothere", "x")
        self.a.transition("rev", "InReview")
        with self.assertRaises(V.LifecycleError):
            self.a.move("base", "moved")
        self.a.transition("rev", "WIP")
        self.a.transition("obs", "Obsolete")
        with self.assertRaises(V.LifecycleError):
            self.a.move("base", "moved")
        self.a.transition("rel", "InReview")
        self.a.transition("rel", "Released")
        self.assertEqual(self.locks(), [])
        self.assertNotEqual(self.head(), head)  # only the transitions above changed it

    def test_released_referrer_gets_next_letter(self):
        self.add("base")
        self.add("rel", [("L", "base.FCStd.d")])
        self.a.transition("rel", "InReview")
        self.a.transition("rel", "Released")
        r = self.a.move("base", "moved")
        self.assertEqual(r["relinked"][0]["revision"], "B.1")
        self.assertEqual(self.a.status("rel")["state"], "WIP")
        self.assertIn("moved.FCStd.d", self.blob("rel"))

    def test_local_edits_and_odd_links_refuse(self):
        self.add("base")
        self.add("asm", [("L", "base.FCStd.d")])
        self.a.checkout("asm")
        with open(os.path.join(self.a.doc_path("asm"), "Shape.brp"), "a") as f:
            f.write("local edit\n")
        head = self.head()
        with self.assertRaises(V.DocumentError):
            self.a.move("base", "moved")
        self.assertEqual(self.head(), head)
        self.assertEqual(self.locks(), ["refs/locks/asm"])  # the lock I held before the call is kept
        self.a.release_lock("asm")
        # a link that names base's folder through another directory cannot be re-pointed: refused, not skipped
        self.add("odd", [("L", "../elsewhere/base.FCStd.d")])
        head = self.head()
        with self.assertRaises(V.DocumentError) as cm:
            self.a.move("base", "moved")
        self.assertIn("odd", str(cm.exception))
        self.assertEqual(self.head(), head)
        self.assertEqual(self.locks(), [])

    def tree_names(self):
        return subprocess.run(["git", "--git-dir", self.vpath, "ls-tree", "--name-only", "refs/heads/main"], check=True,
                              stdout=subprocess.PIPE, text=True).stdout.split()

    def add_raw(self, name, xml):
        d = self.a.doc_path(name)
        os.makedirs(d)
        with open(os.path.join(d, "Document.xml"), "w", encoding="utf-8") as f:
            f.write(xml)
        return self.a.add_document(name)

    def test_unreadable_documents_are_skipped_and_reported(self):
        # MEDIUM 3 and 4: one bad document must not break where_used or move vault-wide
        bomb = ('<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]>'
                '<Document>&b;</Document>')
        self.add("base")
        self.add("asm", [("L", "base.FCStd.d")])
        self.add_raw("bomb", bomb)
        self.add_raw("broken", "<Document><unclosed>")
        used = self.a.where_used("base")
        self.assertEqual([u["document"] for u in used], ["asm"])
        self.assertEqual(sorted(u["document"] for u in used.unreadable), ["bomb", "broken"])
        # an unreadable document might link to base in a form the scan cannot see: refused by default
        head = self.head()
        with self.assertRaises(V.DocumentError) as cm:
            self.a.move("base", "base2")
        self.assertIn("allow_unreadable", str(cm.exception))
        self.assertEqual(self.head(), head)
        self.assertEqual(self.locks(), [])
        r = self.a.move("base", "base2", allow_unreadable=True)  # neither mentions base: proceeds when allowed
        self.assertEqual(sorted(r["unreadable"]), ["bomb", "broken"])
        self.assertEqual([x["document"] for x in r["relinked"]], ["asm"])
        self.assertIn("base2.FCStd.d", self.blob("asm"))
        # an unreadable document that mentions the moved name might link to it: refuse, change nothing
        self.add_raw("hostile", bomb.replace("<Document>", "<Document><!-- base2.FCStd.d -->"))
        head = self.head()
        with self.assertRaises(V.DocumentError) as cm:
            self.a.move("base2", "base3", allow_unreadable=True)
        self.assertIn("hostile", str(cm.exception))
        self.assertEqual(self.head(), head)
        self.assertEqual(self.locks(), [])

    def test_self_link_is_rewritten(self):
        # MEDIUM 5
        self.add("selfy", [("L", "selfy.FCStd.d")])
        self.add("asm", [("L", "selfy.FCStd.d")])
        self.a.move("selfy", "selfy2")
        self.assertIn('file="selfy2.FCStd.d"', self.blob("selfy2"))
        self.assertNotIn('"selfy.FCStd.d"', self.blob("selfy2"))
        self.assertEqual(self.a._where_used_at(self.a._head(), "selfy"), [])

    def test_odd_link_spellings_refuse_clearly(self):
        # LOW 6: numeric character reference or single-quoted attribute
        for tag, xml, expect in (
                ("ref", synth_xml([("L", "base&#46;FCStd.d")]), "numeric character reference"),
                ("quote", synth_xml([("L", "base.FCStd.d")]).replace('file="base.FCStd.d"', "file='base.FCStd.d'"),
                 "single-quoted")):
            self.add("base" + tag)
            self.add_raw(tag, xml.replace("base", "base" + tag))
            head = self.head()
            with self.assertRaises(V.DocumentError) as cm:
                self.a.move("base" + tag, "moved" + tag)
            self.assertIn(expect, str(cm.exception))
            self.assertEqual(self.head(), head)
            self.assertEqual(self.locks(), [])

    def test_old_names_are_refused_after_move(self):
        # HIGH 1 (single process); the multi-client version is in TestMoveWithFreeCAD
        self.add("old")
        self.a.move("old", "mid")
        self.a.move("mid", "new")
        head = self.head()
        for gone, to in (("old", "mid"), ("mid", "new"), ("never", None)):
            for fn in (lambda n: self.b.lock(n), lambda n: self.b.checkout(n), lambda n: self.b.checkin(n, "x"),
                       lambda n: self.b.transition(n, "InReview")):
                with self.assertRaises(V.DocumentError) as cm:
                    fn(gone)
                if to:
                    self.assertIn(f"moved to {to!r}" if gone == "mid" else "moved to", str(cm.exception))
        with self.assertRaises(V.DocumentError) as cm:
            self.b.lock("old")
        self.assertIn("'new'", str(cm.exception))  # the chain old -> mid -> new ends at the document that exists
        self.assertEqual(self.head(), head)
        self.assertEqual(self.locks(), [])
        self.assertEqual(self.tree_names(), ["new.FCStd.d"])
        self.assertEqual(sorted(self.b.documents()), ["new"])
        with self.assertRaises(V.DocumentError):
            self.b.where_used("old")

    def test_untracked_old_folder_survives_move(self):
        # LOW 10: a local folder named like the old document that is not a clean checkout is not deleted
        self.add("old")
        mine = self.b.doc_path("old")
        os.makedirs(mine)
        with open(os.path.join(mine, "notes.txt"), "w") as f:
            f.write("keep me\n")
        r = self.b.move("old", "new")
        self.assertTrue(os.path.isfile(os.path.join(mine, "notes.txt")))
        self.assertTrue(any("untracked" in w for w in r["warnings"]), r)
        # a clean checkout is dropped as before
        self.add("old2")
        self.b.checkout("old2")
        self.b.release_lock("old2")
        r = self.b.move("old2", "new2")
        self.assertFalse(os.path.exists(self.b.doc_path("old2")))
        self.assertEqual(r["warnings"], [])


class TestMoveWithFreeCAD(TV.VaultCase):
    """Acceptance checks 1 and 2 with real FreeCAD documents and separate client processes."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="pdm"))  # /var is a symlink on macOS, see the README
        self.vault = V.Vault.create(os.path.join(self.tmp, "vault.git"))

    def git(self, *args):
        # utf-8 explicitly: the embedded interpreter's locale is ASCII and Document.xml holds non-ASCII text
        return subprocess.run(["git", "--git-dir", self.vault] + list(args), check=True,
                              stdout=subprocess.PIPE).stdout.decode("utf-8", "replace")

    def test_where_used_matches_freecad_and_move_relinks(self):
        docs = ["base", "part2", "solo", "asm1", "asm2"]
        r = self.client("alice", [
            {"op": "build_pad", "name": "base", "length": 10}, {"op": "build_pad", "name": "part2", "length": 7},
            {"op": "build_pad", "name": "solo", "length": 5},
            call("add_document", "base"), call("add_document", "part2"), call("add_document", "solo"),
            {"op": "build_link", "name": "asm1", "targets": ["base"]},
            {"op": "build_link", "name": "asm2", "targets": ["base", "part2"]},
            call("add_document", "asm1"), call("add_document", "asm2")], fresh=True)
        self.assertEqual([x.get("ok") for x in r], [True] * 10, r)
        base_metrics = r[0]["metrics"]
        # Bob, a separate process: FreeCAD's own link graph, and a checkout that will block the move
        r = self.client("bob", [{"op": "fc_graph", "names": docs}, call("checkout", "asm2")], fresh=True)
        graph = r[0]
        self.assertTrue(graph["ok"], graph)
        v = V.Vault.open(self.wc("alice"), "alice")
        for d in docs:
            mine = sorted({u["document"] for u in v.where_used(d)})
            self.assertEqual(mine, graph["used"][d], f"where_used({d}) disagrees with FreeCAD getLinksTo: {graph}")
        # the forward direction: what each document depends on, from FreeCAD's dependency graph
        for d in docs:
            forward = sorted(n for n in docs if d in {u["document"] for u in v.where_used(n)})
            self.assertEqual(forward, sorted(graph["deps"][d]), f"dependencies of {d}")
        self.assertEqual(graph["deps"]["asm2"], ["base", "part2"])
        self.assertEqual(graph["deps"]["asm1"], ["base"])
        self.assertEqual(sorted(u["document"] for u in v.where_used("part2")), ["asm2"])
        self.assertEqual(v.where_used("solo"), [])
        # Refused with nothing changed: bob holds asm2
        head = self.git("rev-parse", "refs/heads/main").strip()
        r = self.client("alice", [call("move", "base", "base2")])
        self.assertEqual(r[0]["error"], "LockError", r)
        self.assertEqual(r[0]["holder"], "bob")
        self.assertEqual(self.git("rev-parse", "refs/heads/main").strip(), head)
        self.assertEqual([self.lock_ref(n) is not None for n in ("base", "asm1", "asm2")], [False, False, True])
        self.client("bob", [call("release_lock", "asm2")])
        # The move, one process; then the vault state and a fresh client's reopen
        r = self.client("alice", [call("move", "base", "base2")])
        self.assertTrue(r[0]["ok"], r)
        self.assertEqual(sorted(x["document"] for x in r[0]["value"]["relinked"]), ["asm1", "asm2"])
        self.assertEqual([self.lock_ref(n) for n in ("base", "base2", "asm1", "asm2")], [None] * 4)
        xml = {n: self.git("show", f"refs/heads/main:{n}.FCStd.d/Document.xml") for n in ("asm1", "asm2")}
        for n, text in xml.items():
            self.assertNotIn("base.FCStd.d", text.replace("base2.FCStd.d", ""), n)
            self.assertIn("base2.FCStd.d", text)
        diff = self.git("diff", head, "refs/heads/main", "--", "asm1.FCStd.d")
        changed = [l for l in diff.splitlines() if l[:1] in "+-" and l[:3] not in ("+++", "---")]
        self.assertEqual(len(changed), 2, diff)  # one link line out, one in
        self.assertEqual(sorted(re.findall(r"file=\"([^\"]+)\"", " ".join(changed))), ["base.FCStd.d", "base2.FCStd.d"])
        r = self.client("carol", [call("checkout", "base2"), call("checkout", "part2"), call("checkout", "asm1"),
                                  call("checkout", "asm2"),
                                  {"op": "link_check", "name": "asm1"}, {"op": "link_check", "name": "asm2"},
                                  {"op": "fc_graph", "names": ["base2", "part2", "solo", "asm1", "asm2"]}], fresh=True)
        self.assertEqual([x.get("ok") for x in r], [True] * 7, r)
        for lc, n in ((r[4], 1), (r[5], 2)):
            self.assertEqual(len(lc["links"]), n)
            for link in lc["links"]:
                self.assertTrue(link["resolved"] and link["valid"], lc)
            first = lc["links"][0]
            self.assertEqual(first["target_file"], "base2.FCStd.d", lc)
            for k, ref in base_metrics.items():
                got = first["metrics"][k]
                if isinstance(ref, list):
                    for g, e in zip(got, ref):
                        self.assertAlmostEqual(g, e, delta=abs(e) * TV.REL)
                else:
                    self.assertAlmostEqual(got, ref, delta=abs(ref) * TV.REL)
        self.assertEqual(r[6]["used"]["base2"], ["asm1", "asm2"])
        self.assertEqual(sorted(u["document"] for u in V.Vault.open(self.wc("carol"), "carol").where_used("base2")),
                         ["asm1", "asm2"])

    def test_old_names_dead_after_move_across_clients(self):
        """HIGH 1 and MEDIUM 2 with separate client processes: bob holds a stale clone of the pre-move vault."""
        r = self.client("alice", [{"op": "build_pad", "name": "old", "length": 10}, call("add_document", "old")],
                        fresh=True)
        self.assertTrue(all(x.get("ok") for x in r), r)
        old_metrics = r[0]["metrics"]
        r = self.client("bob", [call("checkout", "old"), {"op": "set_pad", "name": "old", "length": 12},
                                call("checkin", "old", "bob edit")], fresh=True)
        self.assertTrue(all(x.get("ok") for x in r), r)
        r = self.client("alice", [call("move", "old", "mid"), call("move", "mid", "new")])
        self.assertTrue(all(x.get("ok") for x in r), r)
        head = self.git("rev-parse", "refs/heads/main").strip()
        r = self.client("bob", [call("lock", "old"), call("checkout", "old", discard_local=True),
                                call("checkin", "old", "fork it"), call("transition", "old", "InReview"),
                                call("lock", "mid"), call("checkout", "mid"), call("checkin", "mid", "x")])
        self.assertEqual([x.get("error") for x in r], ["DocumentError"] * 7, r)
        self.assertIn("'new'", r[0]["message"])
        self.assertEqual(self.git("rev-parse", "refs/heads/main").strip(), head)
        self.assertEqual(self.git("ls-tree", "--name-only", "refs/heads/main").split(), ["new.FCStd.d"])
        self.assertEqual(self.git("for-each-ref", "refs/locks/").strip(), "")
        # MEDIUM 2: a pre-move revision of the moved document opens (its folder carries the name it had then)
        r = self.client("bob", [{"op": "revision_metrics", "name": "new", "revision": "A.1"}])
        self.assertTrue(r[0].get("ok"), r)
        for k in ("volume", "area"):
            self.assertAlmostEqual(r[0]["metrics"][k], old_metrics[k], delta=abs(old_metrics[k]) * TV.REL)
        self.assertTrue(r[0]["metrics"]["filename"].rstrip("/").endswith("old.FCStd.d"), r)


if __name__ == "__main__":
    unittest.main()
