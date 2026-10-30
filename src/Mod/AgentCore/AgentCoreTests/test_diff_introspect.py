# SPDX-License-Identifier: LGPL-2.1-or-later
"""F3: transaction introspection agrees with the F1 dump diff on the scripted corpus turns."""

import os
import sys
import tempfile
import threading
import time
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

    def test_keyword_arguments_and_bad_timeouts(self):
        self.doc.beginAgentTurn("t")
        self.box.Length = 25
        self.assertEqual(turn_changes(self.doc, json_values=False)["objects"][0]["name"], "Box")
        self.doc.getAgentTurnChanges(timeout=5.0)
        for bad in (float("nan"), -1.0, 1e300, float("inf")):
            with self.assertRaises(ValueError):
                self.doc.getAgentTurnChanges(True, bad)

    def test_equal_json_is_flagged_only_for_shapes(self):
        # A Length change below JSON's 1e-6 rounding, and one element of a long list, are real changes.
        holder = self.doc.addObject("App::FeaturePython", "Holder")
        holder.addProperty("App::PropertyFloatList", "Big", "g", "")
        holder.Big = [0.0] * 3000
        self.doc.recompute()
        self.doc.beginAgentTurn("t")
        self.box.Length = 10.0000001
        holder.Big = [0.0] * 2999 + [1.0]
        ch = self.doc.getAgentTurnChanges()
        props = summary(ch)["properties"]
        self.assertIn(("Box", "Length"), props)
        self.assertIn(("Holder", "Big"), props)
        for o in ch["objects"]:
            for p in o["properties"]:
                if p["name"] in ("Length", "Big"):
                    self.assertFalse(p["maybe_unchanged"], p["name"])

    def test_no_deadlock_when_another_thread_sets_properties_during_a_big_report(self):
        objs = []
        for i in range(300):
            o = self.doc.addObject("App::FeaturePython", "O%d" % i)
            o.addProperty("App::PropertyLength", "L")
            o.addProperty("App::PropertyPlacement", "P")
            objs.append(o)
        self.doc.recompute()
        self.doc.beginAgentTurn("t")
        for i, o in enumerate(objs):
            o.L = i + 1
            o.P = App.Placement(App.Vector(i, 1, 2), App.Rotation(10, 20, 30))
        done = threading.Event()
        # A hang would stop the whole run: the watchdog ends the process loudly instead.
        watchdog = threading.Timer(90, lambda: (print("DEADLOCK in introspection test", flush=True), os._exit(3)))
        watchdog.start()
        old = sys.getswitchinterval()
        sys.setswitchinterval(1e-5)
        calls = [0]

        def reader():
            while not done.is_set():
                self.doc.getAgentTurnChanges()
                calls[0] += 1

        t = threading.Thread(target=reader)
        t.start()
        try:
            end = time.time() + 3
            k = 0
            while time.time() < end:
                objs[k % 300].L = 1000 + k
                if k % 50 == 0:
                    self.doc.getAgentTurnChanges()
                k += 1
        finally:
            done.set()
            t.join()
            sys.setswitchinterval(old)
            watchdog.cancel()
        self.assertGreater(calls[0], 0)
        self.assertEqual(len(self.doc.getAgentTurnChanges()["objects"]), 300)

    def test_closing_the_document_during_the_call_is_an_error_not_a_crash(self):
        errors = {}
        old = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)
        watchdog = threading.Timer(90, lambda: (print("HANG in close test", flush=True), os._exit(3)))
        watchdog.start()
        try:
            for rnd in range(15):
                d = App.newDocument("introspect_close%d" % rnd)
                o = d.addObject("App::FeaturePython", "O")
                o.addProperty("App::PropertyLength", "L")
                d.recompute()
                d.beginAgentTurn("t")
                o.L = 3
                go = [True]

                def worker():
                    while go[0]:
                        try:
                            d.getAgentTurnChanges(False)
                        except (RuntimeError, ReferenceError) as e:
                            errors[type(e).__name__] = errors.get(type(e).__name__, 0) + 1
                            if isinstance(e, ReferenceError):
                                return

                t = threading.Thread(target=worker)
                t.start()
                time.sleep(0.01)
                App.closeDocument(d.Name)
                go[0] = False
                t.join()
        finally:
            sys.setswitchinterval(old)
            watchdog.cancel()


class _Proxy:
    """A FeaturePython proxy whose onChanged runs Python, as real ones do."""

    def __init__(self, obj):
        obj.Proxy = self

    def onChanged(self, obj, prop):
        sum(range(100))

    def execute(self, obj):
        pass

    def dumps(self):
        return {"n": 0}

    def loads(self, state):
        pass


@unittest.skipUnless(HAVE_API, "built without doc.getAgentTurnChanges")
class TestIntrospectThreads(unittest.TestCase):
    """The GIL must never be held while waiting for the turn mutex (watchdog ends a hang loudly)."""

    def setUp(self):
        self.doc = App.newDocument("introspect_threads")
        self.old_interval = sys.getswitchinterval()
        sys.setswitchinterval(1e-5)
        self.watchdog = threading.Timer(120, lambda: (print("DEADLOCK in thread test", flush=True), os._exit(3)))
        self.watchdog.start()

    def tearDown(self):
        self.watchdog.cancel()
        sys.setswitchinterval(self.old_interval)
        if self.doc.isAgentTurnActive():
            self.doc.endAgentTurn(False)
        App.closeDocument(self.doc.Name)

    def _objects(self, n, python_objects):
        objs = []
        for i in range(n):
            o = self.doc.addObject("App::FeaturePython", "O%d" % i)
            _Proxy(o)
            o.addProperty("App::PropertyLength", "L")
            if python_objects:
                o.addProperty("App::PropertyPythonObject", "PO")
                o.PO = {"i": i}
            objs.append(o)
        self.doc.recompute()
        return objs

    def _hammer(self, reader, writer, seconds=3):
        done = threading.Event()
        calls = [0]

        def run():
            while not done.is_set():
                reader()
                calls[0] += 1

        t = threading.Thread(target=run)
        t.start()
        try:
            end = time.time() + seconds
            k = 0
            while time.time() < end:
                writer(k)
                k += 1
        finally:
            done.set()
            t.join()
        self.assertGreater(calls[0], 0)

    def test_python_object_properties_do_not_deadlock_a_concurrent_setter(self):
        objs = self._objects(300, True)
        self.doc.beginAgentTurn("t")
        for i, o in enumerate(objs):
            o.PO = {"i": i, "big": list(range(50))}
            o.L = i + 1

        def writer(k):
            o = objs[k % 300]
            o.L = 1000 + k
            o.PO = {"k": k}

        self._hammer(lambda: self.doc.getAgentTurnChanges(), writer)
        self._hammer(lambda: self.doc.getAgentTurnChanges(False), writer, 1)

    def test_nested_aborts_do_not_deadlock_readers_or_setters(self):
        objs = self._objects(100, False)
        self.doc.beginAgentTurn("t")
        objs[0].L = 5
        for mode in ("json", "raw", "set"):
            def reader(mode=mode):
                if mode == "set":
                    objs[1].L = 7
                else:
                    self.doc.getAgentTurnChanges(mode == "json")

            def writer(k):
                self.doc.openTransaction("n")
                for o in objs[2:]:
                    o.L = 100 + k
                self.doc.abortTransaction()

            self._hammer(reader, writer, 2)


if __name__ == "__main__":
    unittest.main()
