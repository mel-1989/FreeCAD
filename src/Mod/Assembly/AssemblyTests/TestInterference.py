# SPDX-License-Identifier: LGPL-2.1-or-later
"""G06 slice 1a: interference and clearance accuracy, mutants, speed, clearance sets.

The corpus (interference_corpus.py) holds cases with analytic truths. One document per case
is built once; accuracy, mutation and speed tests all iterate the same table.
"""

import contextlib
import json
import os
import tempfile
import unittest

import FreeCAD as App

import Interference
from AssemblyTests import interference_corpus as C

REL = 1e-6      # brief check 2: volume relative tolerance
ABS_VOL = 1e-9  # brief check 2: volume absolute tolerance, mm^3
ABS_DIST = 1e-6  # brief check 2: distance tolerance, mm


def _vol_ok(got, want):
    return abs(got - want) <= max(REL * abs(want), ABS_VOL)


def evaluate(rep, truth):
    """Compare a report with a truth; returns (problems, worst_volume_error, worst_distance_error)."""
    problems, wv, wd = [], 0.0, 0.0
    got = {"interfering": {}, "touching": set(), "close": {}}
    for p in rep["pairs"]:
        k = frozenset((p["a"]["path"], p["b"]["path"]))
        if p["class"] == "touching":
            got["touching"].add(k)
        else:
            got[p["class"]][k] = p["volume_mm3"] if p["class"] == "interfering" else p["distance_mm"]
    bad = [e for e in rep["errors"] if e.get("kind") != "unresolved"]
    if bad:
        problems.append("errors: %s" % bad)
    unres = {e["path"] for e in rep["errors"] if e.get("kind") == "unresolved"}
    if unres != truth["errors"]:
        problems.append("unresolved got %s want %s" % (sorted(unres), sorted(truth["errors"])))
    skipped = {e["path"] for e in rep["skipped_non_solid"]}
    if skipped != truth["skipped"]:
        problems.append("skipped_non_solid got %s want %s" % (sorted(skipped), sorted(truth["skipped"])))
    for cls in ("interfering", "close"):
        miss = set(truth[cls]) - set(got[cls])
        extra = set(got[cls]) - set(truth[cls])
        if miss or extra:
            problems.append("%s missing %s extra %s" % (cls, sorted(map(sorted, miss)), sorted(map(sorted, extra))))
        for k in set(truth[cls]) & set(got[cls]):
            want, have = truth[cls][k], got[cls][k]
            if cls == "interfering":
                wv = max(wv, abs(have - want) / max(abs(want), 1e-300) if abs(have - want) > ABS_VOL else 0.0)
                if not _vol_ok(have, want):
                    problems.append("volume %s: %r vs %r" % (sorted(k), have, want))
            else:
                wd = max(wd, abs(have - want))
                if abs(have - want) > ABS_DIST:
                    problems.append("distance %s: %r vs %r" % (sorted(k), have, want))
    if got["touching"] != truth["touching"]:
        problems.append("touching got %s want %s" % (sorted(map(sorted, got["touching"])),
                                                     sorted(map(sorted, truth["touching"]))))
    return problems, wv, wd


# ----------------------------------------------------------------- mutants

def _raw_shape(inst):
    lo = inst.objs[-1].getLinkedObject(True)
    shp = getattr(lo, "Shape", None)
    return shp.copy() if shp is not None and not shp.isNull() and shp.Solids else None


def _mut_broad_skips_first(I):
    orig = I._broad_phase
    I._broad_phase = lambda insts, ok, m: orig(insts, ok, m)[1:]


def _mut_link_placement_ignored(I):
    I._instance_shape = lambda root, inst: _raw_shape(inst)


def _mut_placement_applied_twice(I):
    orig = I._instance_shape

    def f(root, inst):
        s = orig(root, inst)
        if s is not None and len(inst.objs) >= 2:
            par = inst.objs[-2]
            if par.TypeId in ("App::Part", "Assembly::AssemblyObject"):
                s = s.copy()
                s.transformShape(par.Placement.toMatrix())
        return s
    I._instance_shape = f


def _drop_elements(which):
    def install(I):
        orig = I._walk

        def f(root):
            keep = []
            for inst in orig(root):
                par = inst.objs[-2] if len(inst.objs) >= 2 else None
                if par is not None and par.TypeId == "App::Link" and par.ElementCount > 0:
                    idx = int(inst.key.rsplit(".", 1)[1])
                    if idx == (par.ElementCount - 1 if which == "last" else 0):
                        continue
                keep.append(inst)
            return keep
        I._walk = f
    return install


def _mut_errors_swallowed(I):
    I._note_error = lambda *a, **k: None


def _mut_hidden_included(I):
    I._is_hidden = lambda inst: False


def _mut_hidden_leaf_only(I):
    I._is_hidden = lambda inst: getattr(inst.objs[-1], "Visibility", True) is False


def _mut_touching_as_interfering(I):
    orig = I._classify

    def f(*a, **k):
        r = orig(*a, **k)
        return ("interfering",) + tuple(r[1:]) if r[0] == "touching" else r
    I._classify = f


def _mut_same_part_not_ignored(I):
    I._same_part = lambda a, b: False


def _mut_ignore_dropped(I):
    I._ignored = lambda a, b, ignore, patterns: False


def _mut_bbox_distance(I):
    def dist(sa, sb):
        ba, bb = sa.BoundBox, sb.BoundBox
        dx = max(ba.XMin - bb.XMax, bb.XMin - ba.XMax, 0.0)
        dy = max(ba.YMin - bb.YMax, bb.YMin - ba.YMax, 0.0)
        dz = max(ba.ZMin - bb.ZMax, bb.ZMin - ba.ZMax, 0.0)
        return max(dx, dy, dz), None, None
    I._core = _Shim(I._core, min_distance=dist)


def _mut_margin_ignores_clearance(I):
    orig = I._broad_phase
    I._broad_phase = lambda insts, ok, m: orig(insts, ok, 0.0)


def _mut_volume_from_bbox(I):
    def vol(sa, sb):
        a, b = sa.BoundBox, sb.BoundBox
        dx = min(a.XMax, b.XMax) - max(a.XMin, b.XMin)
        dy = min(a.YMax, b.YMax) - max(a.YMin, b.YMin)
        dz = min(a.ZMax, b.ZMax) - max(a.ZMin, b.ZMin)
        return max(dx, 0) * max(dy, 0) * max(dz, 0)
    I._core = _Shim(I._core, common_volume=vol)


def _mut_common_skipped(I):
    I._core = _Shim(I._core, common_volume=lambda sa, sb: 0.0)


def _mut_instances_deduplicated_by_source(I):
    orig = I._walk

    def f(root):
        seen, keep = set(), []
        for inst in orig(root):
            src = inst.objs[-1].getLinkedObject(True).Name
            if src not in seen:
                seen.add(src)
                keep.append(inst)
        return keep
    I._walk = f


def _mut_contact_tol_zero(I):
    orig = I._resolve_tol
    I._resolve_tol = lambda p: {**orig(p), "contact_mm": 0.0}


def _mut_clear_pairs_reported(I):
    orig = I._classify
    I._classify = lambda a, b, cl, ct, vt: (orig(a, b, cl, ct, vt)[0] or "close",) + tuple(orig(a, b, cl, ct, vt)[1:])


class _Shim:
    """The F2 core module with some primitives replaced."""

    def __init__(self, base, **over):
        self._base, self._over = base, over

    def __getattr__(self, name):
        return self._over[name] if name in self._over else getattr(self._base, name)


# name -> (installer, case that must catch it)
MUTANTS = {
    "broad_phase_skips_a_pair": (_mut_broad_skips_first, "box_overlap_depth_1"),
    "link_placement_ignored": (_mut_link_placement_ignored, "link_offset_overlap"),
    "array_last_element_missed": (_drop_elements("last"), "array_last_element_only"),
    "array_first_element_missed": (_drop_elements("first"), "array_first_element_only"),
    "unresolved_parts_swallowed": (_mut_errors_swallowed, "link_deleted_target"),
    "hidden_part_included": (_mut_hidden_included, "hidden_box"),
    "hidden_parent_not_inherited": (_mut_hidden_leaf_only, "hidden_part_hides_children"),
    "subassembly_placement_applied_twice": (_mut_placement_applied_twice, "nested_part_translated"),
    "touching_counted_as_interfering": (_mut_touching_as_interfering, "touch_box_face_full"),
    "same_part_pairs_not_ignored": (_mut_same_part_not_ignored, "link_to_part_same_part_ignored"),
    "user_ignore_list_dropped": (_mut_ignore_dropped, "ignore_listed_pair"),
    "clearance_uses_bbox_gap": (_mut_bbox_distance, "near_box_diagonal"),
    "broad_phase_ignores_clearance_margin": (_mut_margin_ignores_clearance, "near_box_gap_1"),
    "volume_from_bounding_boxes": (_mut_volume_from_bbox, "box_rot45_octagon"),
    "exact_common_skipped": (_mut_common_skipped, "box_contained"),
    "instances_of_one_part_deduplicated": (_mut_instances_deduplicated_by_source, "link_two_instances_of_one_part"),
    "contact_tolerance_zero": (_mut_contact_tol_zero, "touch_sph_sph_tangent"),
    "clear_pairs_reported": (_mut_clear_pairs_reported, "near_sph_diagonal_clear"),
}


@contextlib.contextmanager
def mutated(name):
    saved = {k: getattr(Interference, k) for k in
             ("_broad_phase", "_instance_shape", "_walk", "_is_hidden", "_classify", "_same_part",
              "_ignored", "_core", "_resolve_tol", "_note_error")}
    try:
        MUTANTS[name][0](Interference)
        yield
    finally:
        for k, v in saved.items():
            setattr(Interference, k, v)


# -------------------------------------------------------------------- tests

class TestInterference(unittest.TestCase):
    docs = {}
    truths = {}
    summary = {}

    @classmethod
    def setUpClass(cls):
        for name in C.CASES:
            doc = App.newDocument("Itf_" + name)
            cls.docs[name], cls.truths[name] = C.build_case(doc, name)

    @classmethod
    def tearDownClass(cls):
        for asm in cls.docs.values():
            App.closeDocument(asm.Document.Name)
        cls.docs.clear()

    def run_case(self, name, **override):
        params = {**C.CASES[name].params, **override}
        return Interference.check(self.docs[name], **params)

    def test_corpus_shape(self):
        counts = {}
        for c in C.CASES.values():
            counts[c.klass] = counts.get(c.klass, 0) + 1
        print("\n  corpus: %d cases %s" % (len(C.CASES), counts))
        self.assertGreaterEqual(len(C.CASES), 40)
        self.assertGreaterEqual(sum(1 for c in C.CASES.values() if c.own), 5)
        for klass in ("interfering", "touching", "near_miss", "link", "array", "nested", "joint",
                      "hidden", "ignore", "own", "big"):
            self.assertIn(klass, counts)
        big = self.run_case("big_grid_225")
        self.assertGreaterEqual(big["stats"]["instances"], 200)

    def test_accuracy_all_cases(self):
        worst_v = worst_d = 0.0
        for name in C.CASES:
            with self.subTest(case=name):
                rep = self.run_case(name)
                problems, wv, wd = evaluate(rep, self.truths[name])
                worst_v, worst_d = max(worst_v, wv), max(worst_d, wd)
                self.assertEqual(problems, [], name)
        print("\n  worst relative volume error %.3g, worst distance error %.3g mm over %d cases"
              % (worst_v, worst_d, len(C.CASES)))

    def test_report_is_json_and_names_labels(self):
        rep = self.run_case("link_offset_overlap")
        rep2 = json.loads(json.dumps(rep))
        self.assertEqual(rep, rep2)
        pair = rep["pairs"][0]
        self.assertEqual(pair["class"], "interfering")
        self.assertEqual({pair["a"]["label"], pair["b"]["label"]}, {"A", "LB"})
        self.assertEqual(pair["b"]["link_path"], ["LB"])
        nested = self.run_case("nested_part_in_part")["pairs"][0]
        self.assertEqual(nested["b"]["link_path"], ["Outer", "Inner", "Box"])
        for key in ("pairs_total", "pairs_after_broad_phase", "exact_checks", "timings_s"):
            self.assertIn(key, rep["stats"])

    def test_array_elements_named_distinctly(self):
        for name in ("array_noshow_hidden_source", "array_noshow_visible_source"):
            rep = self.run_case(name)
            self.assertEqual(rep["stats"]["instances"], 3, name)
            labels = {p[s]["label"] for p in rep["pairs"] for s in ("a", "b")}
            self.assertEqual(labels, {"Arr[0]", "Arr[1]", "Arr[2]"}, name)
        hid = self.run_case("array_noshow_hidden_element")
        self.assertEqual(hid["stats"]["hidden_skipped"], 1)
        self.assertEqual(self.run_case("array_noshow_array_hidden")["stats"]["hidden_skipped"], 3)

    def test_unresolved_and_non_solid_reporting(self):
        rep = self.run_case("link_deleted_target")
        err = [e for e in rep["errors"] if e["kind"] == "unresolved"]
        self.assertEqual([(e["label"], e["path"]) for e in err], [("Broken", "Broken")])
        self.assertTrue(err[0]["reason"])
        self.assertEqual(rep["stats"]["unresolved"], 1)
        rep = self.run_case("face_wire_shell_sketch_listed")
        types = {e["label"]: e["shape_type"] for e in rep["skipped_non_solid"]}
        self.assertEqual(types, {"Face": "Face", "Wire": "Wire", "Shell": "Shell", "Sk": "Empty"})
        self.assertEqual(rep["stats"]["hidden_skipped"], 0)   # the hidden face is not a hidden solid
        self.assertEqual(rep["errors"], [])
        self.assertEqual(self.run_case("hidden_box")["stats"]["hidden_skipped"], 1)
        json.dumps(rep)

    def test_empty_body_is_not_an_error(self):
        # a Body with no features yet is shapeless by design: listed as a non-solid, never an unresolved error
        doc = App.newDocument("Itf_empty_body")
        try:
            asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
            asm.addObject(doc.addObject("PartDesign::Body", "Body"))
            box = doc.addObject("Part::Box", "Box")
            asm.addObject(box)
            doc.recompute()
            rep = Interference.check(asm)
            self.assertEqual(rep["errors"], [])
            self.assertEqual([(e["label"], e["shape_type"]) for e in rep["skipped_non_solid"]], [("Body", "Empty")])
            asm.addObject(doc.addObject("Part::Box", "Fresh"))   # added, not recomputed: reported with a plain reason
            err = Interference.check(asm)["errors"]
            self.assertEqual([e["label"] for e in err], ["Fresh"])
            self.assertIn("not been recomputed", err[0]["reason"])
        finally:
            App.closeDocument(doc.Name)

    def test_verify_fails_on_unresolved_parts(self):
        from AgentCore import verify
        for name in ("link_deleted_target", "null_shape_part"):
            res = verify.run_checks(self.docs[name].Document, [{"check": "assembly_interference"}])["results"][0]
            self.assertEqual(res["status"], "ok", res)
            self.assertFalse(res["ok"], name)
            self.assertTrue(any("error" in v for v in res["details"]["violations"]), name)
        # a non-solid shape is listed but does not fail the check
        res = verify.run_checks(self.docs["face_wire_shell_sketch_listed"].Document,
                                [{"check": "assembly_interference"}])["results"][0]
        self.assertTrue(res["ok"], res)
        # a clearance-set selector naming an unresolved part is reported, not ok
        asm = self.docs["link_deleted_target"]
        rep = Interference.check_clearance_sets(asm, sets=[{"name": "s", "min_mm": 1.0, "pairs": [["Broken", "A"]]}])
        self.assertFalse(rep["ok"])
        self.assertIn("unresolved", rep["unresolved"][0]["error"])
        self.assertEqual(len(rep["errors"]), 1)

    def test_subassembly_through_plain_link_counted_once(self):
        from AgentCore import verify
        from AgentCore.verify import checks
        doc = App.newDocument("Itf_sublink")
        try:
            sub = doc.addObject("Assembly::AssemblyObject", "Sub")
            C.box(sub, "a")
            C.box(sub, "b", pos=(5, 0, 0))
            top = doc.addObject("Assembly::AssemblyObject", "Top")
            top.newObject("App::Link", "PL").LinkedObject = sub
            doc.recompute()
            self.assertEqual([o.Name for o in checks._top_assemblies(doc)], ["Top"])
            res = verify.run_checks(doc, [{"check": "assembly_interference"}])["results"][0]
            self.assertFalse(res["ok"])
            self.assertEqual(len(res["details"]["violations"]), 1)
            self.assertEqual(list(res["details"]["assemblies"]), ["Top"])
            # a stray link outside any assembly does not hide the sub-assembly
            doc2 = App.newDocument("Itf_sublink2")
            sub2 = doc2.addObject("Assembly::AssemblyObject", "Sub")
            doc2.addObject("App::Link", "Stray").LinkedObject = sub2
            doc2.recompute()
            self.assertEqual([o.Name for o in checks._top_assemblies(doc2)], ["Sub"])
            App.closeDocument(doc2.Name)
        finally:
            App.closeDocument(doc.Name)

    def test_ignore_entries_validated(self):
        asm = self.docs["ignore_listed_pair"]
        for bad in (["A", "B", "C"], ["A"], [[]], [("A", 3)]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                Interference.check(asm, ignore=[bad])
        Interference.check(asm, ignore=["A", ("A", "B"), ["A", "B"]])

    def test_mutants_are_caught(self):
        self.assertGreaterEqual(len(MUTANTS), 10)
        small = [n for n, c in C.CASES.items() if not c.big]
        table = []
        for mname, (_inst, catcher) in MUTANTS.items():
            with self.subTest(mutant=mname), mutated(mname):
                failing = []
                for name in small:
                    try:
                        rep = self.run_case(name)
                        problems, _, _ = evaluate(rep, self.truths[name])
                    except Exception as exc:  # a crash also counts as a failing case
                        problems = [repr(exc)]
                    if problems:
                        failing.append(name)
                table.append((mname, catcher, failing))
                self.assertTrue(failing, "mutant %s survived" % mname)
                self.assertIn(catcher, failing, "mutant %s not caught by %s" % (mname, catcher))
        print("\n  mutants (%d), each: catching case / total failing cases" % len(table))
        for mname, catcher, failing in table:
            print("    %-40s %-34s %d" % (mname, catcher, len(failing)))
        # the unmutated corpus still passes after all patches were restored
        for name in small:
            self.assertEqual(evaluate(self.run_case(name), self.truths[name])[0], [], name)

    def test_speed_big_case(self):
        rep = self.run_case("big_grid_225")
        st = rep["stats"]
        print("\n  big case: %s" % json.dumps(st, sort_keys=True))
        self.assertLess(st["timings_s"]["total"], 30.0)
        self.assertGreaterEqual(st["broad_phase_discard"], 0.90)
        self.assertGreaterEqual(st["instances"], 200)
        self.assertEqual(evaluate(rep, self.truths["big_grid_225"])[0], [])

    def test_clearance_sets_survive_save_and_reopen(self):
        asm = self.docs["near_box_gap_1"]
        doc = asm.Document
        Interference.set_clearance_set(asm, "gearbox", 3.0, [("A", "B")])
        Interference.set_clearance_set(asm, "spare", 0.5, [("A", "B")])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sets.FCStd")
            doc.saveAs(path)
            name = doc.Name
            App.closeDocument(name)
            doc2 = App.openDocument(path)
            try:
                asm2 = doc2.getObject("Assembly")
                sets = Interference.get_clearance_sets(asm2)
                self.assertEqual([s["name"] for s in sets], ["gearbox", "spare"])
                rep = Interference.check_clearance_sets(asm2)
                self.assertFalse(rep["ok"])
                self.assertEqual(len(rep["violations"]), 1)
                v = rep["violations"][0]
                self.assertEqual(v["set"], "gearbox")
                self.assertAlmostEqual(v["distance_mm"], 1.0, delta=1e-6)
                self.assertEqual(v["required_mm"], 3.0)
                self.assertEqual({v["a"]["label"], v["b"]["label"]}, {"A", "B"})
                json.dumps(rep)
                # F2 verify loop cites the entities
                from AgentCore import verify
                out = verify.run_checks(doc2, [{"check": "clearance_sets"}])
                res = out["results"][0]
                self.assertEqual(res["status"], "ok")
                self.assertFalse(res["ok"])
                self.assertEqual(res["entities"], sorted({v["a"]["entity"], v["b"]["entity"]}))
                self.assertEqual(len(res["entities"]), 2)
                json.dumps(out)
                # removing the violated set makes the loop pass
                self.assertTrue(Interference.remove_clearance_set(asm2, "gearbox"))
                self.assertTrue(verify.run_checks(doc2, [{"check": "clearance_sets"}])["results"][0]["ok"])
                # an isolated worker process opens the saved file and reports the same
                doc2.saveAs(os.path.join(tmp, "sets2.FCStd"))
            finally:
                App.closeDocument(doc2.Name)
        # rebuild the closed case so later tests (and repeated runs) still find a document
        d = App.newDocument("Itf_near_box_gap_1")
        self.docs["near_box_gap_1"], self.truths["near_box_gap_1"] = C.build_case(d, "near_box_gap_1")

    def test_clearance_sets_in_isolated_worker(self):
        from AgentCore import verify
        asm = self.docs["near_box_gap_1e-2"]
        Interference.set_clearance_set(asm, "tight", 0.05, [("A", "B")])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "w.FCStd")
            asm.Document.saveAs(path)
            with verify.Pool(size=1, timeout=120) as pool:
                out = pool.run(path, [{"check": "clearance_sets"},
                                      {"check": "assembly_interference", "clearance": 0.05}])
        r_sets, r_itf = out["results"]
        self.assertEqual(r_sets["status"], "ok", r_sets)
        self.assertFalse(r_sets["ok"])
        self.assertEqual(len(r_sets["entities"]), 2)
        self.assertEqual(r_itf["status"], "ok", r_itf)
        self.assertFalse(r_itf["ok"])  # the 0.01 mm gap is below the 0.05 clearance

    def test_verify_assembly_interference(self):
        from AgentCore import verify
        doc = self.docs["box_overlap_depth_1"].Document
        res = verify.run_checks(doc, [{"check": "assembly_interference"}])["results"][0]
        self.assertEqual(res["status"], "ok", res)
        self.assertFalse(res["ok"])
        self.assertEqual(len(res["entities"]), 2)
        clean = self.docs["touch_box_face_full"].Document
        res = verify.run_checks(clean, [{"check": "assembly_interference"}])["results"][0]
        self.assertTrue(res["ok"], res)
        res = verify.run_checks(clean, [{"check": "assembly_interference", "fail_on_touching": True}])["results"][0]
        self.assertFalse(res["ok"])

    def test_two_runs_identical(self):
        a = self.run_case("array_link_overlap")
        b = self.run_case("array_link_overlap")
        for r in (a, b):
            r["stats"].pop("timings_s")
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
