# SPDX-License-Identifier: LGPL-2.1-or-later
"""F3: transaction introspection agrees with the F1 dump diff on the scripted corpus turns."""

import os
import sys
import tempfile
import unittest

import FreeCAD as App

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "diff")
if FIX not in sys.path:
    sys.path.insert(0, FIX)

import diff_builders as db  # noqa: E402
from AgentCore.diff import AgentTurn, ApplyAuthority, differences, snapshot_dump  # noqa: E402
from AgentCore.diff.changeset import object_changes  # noqa: E402
from AgentCore.diff.compare import TRANSIENT_CACHES  # noqa: E402
from AgentCore.diff.introspect import summary, turn_changes  # noqa: E402

HAVE_API = hasattr(App.Document, "getAgentTurnChanges")

# Property types that the F1 dump reports under the object's "shape" or "detail" record rather
# than as a property (shapes, sketch geometry and constraints).
F1_ELSEWHERE = ("Shape", "GeometryList", "ConstraintList")


def explained(rec):
    """Why F1's property list may lack a property the transaction records changed, or None."""
    if rec["name"] in TRANSIENT_CACHES:
        return "transient cache"
    if any(k in rec["type"] for k in F1_ELSEWHERE) or rec["name"] in ("Geometry", "ExternalGeo", "Constraints"):
        return "F1 reports shapes and sketch geometry under shape/detail"
    if rec["change"] == "modified" and not differences(rec["old"], rec["new"], rel_tol=1e-6, abs_tol=1e-6):
        return "change below F1's rounding"
    return None


@unittest.skipUnless(HAVE_API, "built without doc.getAgentTurnChanges")
class TestIntrospectAgreesWithDumpDiff(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.auth = ApplyAuthority.install()

    @classmethod
    def tearDownClass(cls):
        cls.auth.uninstall()

    def test_scripted_turns_on_every_f1_fixture(self):
        unexplained = {}
        rules = {}
        for key in db.F1_FIXTURES:
            doc = db.open_f1_fixture(key)
            try:
                with AgentTurn(doc, label=f"scripted {key}", verify=False) as t:
                    before = snapshot_dump(doc)  # inside the turn: entering it settles the document
                    db.scripted_turn(doc)
                    changes = turn_changes(doc)
                    f1 = object_changes(before, snapshot_dump(doc))
                    t.reject()
                new = summary(changes)
                f1_added = {f["name"] for f in f1["features"]["added"]}
                f1_removed = {f["name"] for f in f1["features"]["removed"]}
                self.assertEqual(new["added"], f1_added, key)
                self.assertEqual(new["removed"], f1_removed, key)
                f1_pairs = {(p["object"], p["property"]): p for p in f1["properties"]
                            if p["object"] not in f1_added | f1_removed}
                # Everything F1 sees changed, the transaction records too (property for property).
                self.assertEqual(sorted(set(f1_pairs) - set(new["properties"])), [], key)
                for pair, rec in new["properties"].items():
                    if pair in f1_pairs:
                        # The old and new values agree with F1's before and after dumps.
                        self.assertEqual(differences(rec["old"], f1_pairs[pair]["before"], rel_tol=1e-9, abs_tol=1e-9),
                                         [], f"{key} {pair} old")
                        self.assertEqual(differences(rec["new"], f1_pairs[pair]["after"], rel_tol=1e-9, abs_tol=1e-9),
                                         [], f"{key} {pair} new")
                    else:
                        why = explained(rec)
                        if why is None:
                            unexplained.setdefault(key, []).append(pair)
                        else:
                            rules[why] = rules.get(why, 0) + 1
            finally:
                App.closeDocument(doc.Name)
        self.assertEqual(unexplained, {})
        print("introspect agreement, extra pairs by rule:", rules)


@unittest.skipUnless(HAVE_API, "built without doc.getAgentTurnChanges")
class TestIntrospectApi(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("introspect_api")
        self.doc.UndoMode = 1
        self.box = self.doc.addObject("Part::Box", "Box")
        self.doc.recompute()

    def tearDown(self):
        if self.doc.isAgentTurnActive():
            self.doc.endAgentTurn(False)
        App.closeDocument(self.doc.Name)

    def test_outside_a_turn_raises(self):
        with self.assertRaises(RuntimeError):
            self.doc.getAgentTurnChanges()

    def test_empty_turn_is_empty(self):
        self.doc.beginAgentTurn("t")
        self.assertEqual(self.doc.getAgentTurnChanges()["objects"], [])

    def test_values_are_json_safe_with_units_on_both_sides(self):
        import json
        self.doc.beginAgentTurn("t")
        self.box.Length = 25
        ch = self.doc.getAgentTurnChanges()
        json.dumps(ch)
        rec = summary(ch)["properties"][("Box", "Length")]
        self.assertEqual(rec["old"], {"value": 10.0, "unit": "Length"})
        self.assertEqual(rec["new"], {"value": 25.0, "unit": "Length"})

    def test_restored_property_is_dropped_and_nested_abort_is_absent(self):
        self.doc.beginAgentTurn("t")
        self.box.Length = 25
        self.box.Length = 10
        self.doc.openTransaction("nested")
        self.box.Width = 99
        self.doc.abortTransaction()
        self.assertEqual(summary(self.doc.getAgentTurnChanges())["properties"], {})

    def test_added_removed_and_replaced_objects(self):
        self.doc.beginAgentTurn("t")
        self.doc.removeObject("Box")
        self.doc.openTransaction("nested")
        self.doc.addObject("Part::Box", "Box")
        self.doc.commitTransaction()
        objs = {(o["name"], o["status"]): o for o in self.doc.getAgentTurnChanges()["objects"]}
        self.assertTrue(objs[("Box", "removed")]["replaced"])
        self.assertTrue(objs[("Box", "added")]["replaced"])

    def test_link_list_to_an_object_removed_in_the_turn_keeps_its_name(self):
        a = self.doc.addObject("Part::Box", "A")
        holder = self.doc.addObject("App::FeaturePython", "Holder")
        for kind, prop in (("App::PropertyLinkList", "Ls"), ("App::PropertyLinkSubList", "Lsub"),
                           ("App::PropertyLinkListChild", "Lc")):
            holder.addProperty(kind, prop, "g", "")
        holder.Ls = [a, self.box, a]
        holder.Lsub = [(a, ["Edge1"]), (self.box, ["Edge2"])]
        holder.Lc = [a, self.box]
        self.doc.recompute()
        self.doc.beginAgentTurn("t")
        self.doc.removeObject("A")
        props = {p["name"]: p for o in self.doc.getAgentTurnChanges()["objects"]
                 if o["name"] == "Holder" for p in o["properties"]}
        self.assertEqual(props["Ls"]["old"], [{"object": "A"}, {"object": "Box"}, {"object": "A"}])
        self.assertEqual(props["Ls"]["new"], [{"object": "Box"}])
        self.assertEqual(props["Lsub"]["old"], [{"object": "A", "subs": ["Edge1"]},
                                                {"object": "Box", "subs": ["Edge2"]}])
        self.assertEqual(props["Lc"]["old"], [{"object": "A"}, {"object": "Box"}])

    def test_clearing_a_cross_document_link_is_reported_with_its_target(self):
        other = App.newDocument("introspect_other")
        tmp = tempfile.TemporaryDirectory()
        other.saveAs(os.path.join(tmp.name, "other.FCStd"))
        self.doc.saveAs(os.path.join(tmp.name, "main.FCStd"))
        try:
            target = other.addObject("Part::Box", "Target")
            holder = self.doc.addObject("App::FeaturePython", "Holder")
            holder.addProperty("App::PropertyXLink", "X", "g", "")
            holder.addProperty("App::PropertyXLinkList", "XL", "g", "")
            holder.X = target
            holder.XL = [target]
            self.doc.recompute()
            self.doc.beginAgentTurn("t")
            holder.X = None
            holder.XL = []
            ch = self.doc.getAgentTurnChanges()
            props = {p["name"]: p for o in ch["objects"] if o["name"] == "Holder" for p in o["properties"]}
            want = {"object": "Target", "document": "introspect_other"}
            self.assertEqual(props["X"]["old"], want)
            self.assertIsNone(props["X"]["new"])
            self.assertFalse(props["X"]["maybe_unchanged"])
            self.assertEqual(props["XL"]["old"], [want])
            self.assertEqual(props["XL"]["new"], [])
            self.assertIn(("Holder", "X"), summary(ch)["properties"])
            self.assertIn(("Holder", "XL"), summary(ch)["properties"])
        finally:
            if self.doc.isAgentTurnActive():
                self.doc.endAgentTurn(False)
            App.closeDocument("introspect_other")
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
