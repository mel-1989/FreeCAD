# SPDX-License-Identifier: LGPL-2.1-or-later
"""G06 slice 4a: assembly-level features, accuracy, parts untouched, follows the assembly, mutants.

The corpus (feature_corpus.py) holds cases with analytic truths. Every case is built into fresh
documents; the accuracy, mutant and deletion tests all evaluate the same table.
"""

import contextlib
import hashlib
import json
import os
import re
import shutil
import tempfile
import unittest
import zipfile

import FreeCAD as App
import Part

import AssemblyFeature as AF
import Interference
from AssemblyTests import feature_corpus as C

REL = 1e-6      # brief check 2: volume relative tolerance
ABS_VOL = 1e-9  # brief check 2: volume absolute tolerance, mm^3
PI = C.PI


def _vol_ok(got, want):
    return abs(got - want) <= max(REL * abs(want), ABS_VOL)


def brep_hash(shape):
    """Fingerprint of a shape's geometry: counts, exact floats of volume, area, mass centre, box,
    placement and every vertex. (The BREP text of a copy differs from the original's, so it is no hash.)"""
    if shape is None or shape.isNull():
        return "null"
    parts = [shape.ShapeType, len(shape.Solids), len(shape.Faces), len(shape.Edges), len(shape.Vertexes),
             float(shape.Volume).hex(), float(shape.Area).hex()]
    for sol in shape.Solids:
        com = sol.CenterOfMass
        parts += [float(com.x).hex(), float(com.y).hex(), float(com.z).hex()]
    bb = shape.BoundBox
    parts += [float(x).hex() for x in (bb.XMin, bb.XMax, bb.YMin, bb.YMax, bb.ZMin, bb.ZMax)]
    parts += [float(x).hex() for x in shape.Placement.Matrix.A]
    parts += sorted(tuple(float(c).hex() for c in (v.X, v.Y, v.Z)) for v in shape.Vertexes)
    return hashlib.sha256(repr(parts).encode()).hexdigest()


def raw_map(asm):
    return {i.key: i for i in Interference.resolve(asm, include_hidden=True, apply_features=False).insts}


# ------------------------------------------------------------ part signatures

def _norm(v):
    if isinstance(v, App.DocumentObject):
        return "obj:" + v.Name
    if isinstance(v, (list, tuple)):
        return [_norm(x) for x in v]
    if isinstance(v, Part.Shape):
        return "shape:" + brep_hash(v)
    if isinstance(v, dict):
        return {str(k): _norm(x) for k, x in v.items()}
    return re.sub(r"0x[0-9a-fA-F]+", "0x", str(v))


def _is_assembly_side(o):
    if o.TypeId == "Assembly::AssemblyObject" or AF._is_feature(o) or o.TypeId == "Assembly::JointGroup":
        return True
    return any(p.TypeId == "Assembly::JointGroup" for p in o.InList)


def part_snapshot(ctx):
    """Property, state and shape signature of every object that is not the assembly, a feature or a joint."""
    snap = {}
    for d in ctx.docs:
        for o in d.Objects:
            if _is_assembly_side(o):
                continue
            props = {}
            for p in o.PropertiesList:
                if p in ("Proxy", "Shape") or p.startswith("_"):
                    continue  # underscore names are FreeCAD's own transient caches (_Part_ShapeCache, _ChildCache)
                try:
                    props[p] = _norm(getattr(o, p))
                except Exception:
                    props[p] = "unreadable"
            snap[d.Name + ":" + o.Name] = {
                "props": props, "touched": "Touched" in o.State,
                "shape": brep_hash(o.Shape) if hasattr(o, "Shape") else None}
    return snap


def saved_signature(doc):
    """Content of the saved part document, member by member, without the save-time stamps."""
    if doc is None:
        return None
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "part.FCStd")
        doc.saveCopy(path)
        out = {}
        with zipfile.ZipFile(path) as z:
            for n in sorted(z.namelist()):
                data = z.read(n)
                if n == "Document.xml":
                    data = re.sub(rb'(CreationDate|LastModifiedDate)"[^>]*>\s*<String value="[^"]*"',
                                  rb'\1"', data)
                out[n] = hashlib.sha256(data).hexdigest()
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def diff_snap(a, b):
    out = []
    for k in sorted(set(a) | set(b)):
        if k not in a or k not in b:
            out.append("object %s appeared or vanished" % k)
        elif a[k] != b[k]:
            what = [p for p in set(a[k]["props"]) | set(b[k]["props"]) if a[k]["props"].get(p) != b[k]["props"].get(p)]
            if a[k]["touched"] != b[k]["touched"]:
                what.append("Touched")
            if a[k]["shape"] != b[k]["shape"]:
                what.append("Shape")
            out.append("%s changed: %s" % (k, what))
    return out


# --------------------------------------------------------------- evaluation

class Tally:
    def __init__(self):
        self.max_rel = 0.0
        self.max_abs = 0.0
        self.cut_checked = 0
        self.false_cuts = 0
        self.missed_cuts = 0
        self.unchanged_checked = 0
        self.untouched_checks = 0
        self.saved_checks = 0
        self.phases = 0


def eval_phase(ctx, truth, tally):
    problems = []
    tally.phases += 1
    pre, pre_bytes = part_snapshot(ctx), saved_signature(ctx._src)
    rep = AF.report(ctx.asm)
    if json.loads(json.dumps(rep)) != rep:
        problems.append("report not JSON-stable")
    raws = raw_map(ctx.asm)
    comps = {c["path"]: c for c in rep["components"]}
    if set(comps) != set(raws):
        problems.append("components %s vs raw %s" % (sorted(comps), sorted(raws)))
    for path in truth["removed"]:
        if path not in raws:
            problems.append("truth path %s is not a component" % path)
    if rep["errors"]:
        problems.append("errors %s" % rep["errors"])
    for path, inst in raws.items():
        c = comps.get(path)
        if c is None:
            continue
        eff = AF.effective_shape(ctx.asm, path)
        want = truth["removed"].get(path)
        if want is not None and want > 0:
            tally.cut_checked += 1
            if not c["cut"]:
                tally.missed_cuts += 1
                problems.append("%s: not cut, want %r removed" % (path, want))
                continue
            tally.max_rel = max(tally.max_rel, abs(c["removed_mm3"] - want) / want)
            tally.max_abs = max(tally.max_abs, abs(c["removed_mm3"] - want))
            if not _vol_ok(c["removed_mm3"], want):
                problems.append("%s: removed %r want %r" % (path, c["removed_mm3"], want))
            if eff is None or not _vol_ok(inst.shape.Volume - eff.Volume, want):
                problems.append("%s: shape lost %r want %r" % (path, inst.shape.Volume - (eff.Volume if eff else 0), want))
            if eff is not None and not eff.isNull() and eff.Solids:
                if not eff.isValid():
                    problems.append("%s: effective shape invalid" % path)
                # where it sits: what is left lies inside the original, in assembly placement
                inside = eff.common(inst.shape).Volume
                if abs(inside - eff.Volume) > max(REL * eff.Volume, ABS_VOL):
                    problems.append("%s: effective shape strays outside the component (%r of %r inside)"
                                    % (path, inside, eff.Volume))
        else:
            tally.unchanged_checked += 1
            if c["cut"] or c["removed_mm3"] != 0:
                tally.false_cuts += 1
                problems.append("%s: false cut (removed %r)" % (path, c["removed_mm3"]))
            if brep_hash(eff) != brep_hash(inst.shape):
                problems.append("%s: shape changed though no cut is due" % path)
            if eff is not None and eff.Volume != inst.shape.Volume:
                problems.append("%s: volume changed though no cut is due" % path)
    for fname, per in truth["per"].items():
        f = ctx.asm.getObject(fname)
        got = {r["path"]: r["removed_mm3"] for r in AF.affected(f)["components"]}
        if set(got) != set(per) or any(not _vol_ok(got[k], per[k]) for k in per if k in got):
            problems.append("feature %s removed %s want %s" % (fname, got, per))
    post, post_bytes = part_snapshot(ctx), saved_signature(ctx._src)
    ctx.doc.recompute()
    post2 = part_snapshot(ctx)
    tally.untouched_checks += 1
    problems += ["parts: " + p for p in diff_snap(pre, post)]
    problems += ["parts after recompute: " + p for p in diff_snap(pre, post2)]
    if pre_bytes is not None:
        tally.saved_checks += 1
        if pre_bytes != post_bytes:
            problems.append("saved part document differs")
    return problems


def evaluate_case(name, tally=None, with_delete=True):
    tally = tally or Tally()
    ctx, truth = C.build_case(name)
    try:
        problems = []
        base_snap, base_bytes = part_snapshot(ctx), saved_signature(ctx._src)
        base_raw = {k: brep_hash(v.shape) for k, v in raw_map(ctx.asm).items()}
        problems += eval_phase(ctx, truth, tally)
        for action, t2 in truth["then"]:
            action(ctx)
            ctx.doc.recompute()
            problems += eval_phase(ctx, t2, tally)
        if with_delete:
            for f in list(ctx.features):
                ctx.doc.removeObject(f.Name)
            ctx.doc.recompute()
            raws = raw_map(ctx.asm)
            if not truth["then"]:
                for path, inst in raws.items():
                    eff = AF.effective_shape(ctx.asm, path)
                    if brep_hash(eff) != brep_hash(inst.shape) or base_raw.get(path) != brep_hash(inst.shape):
                        problems.append("%s: deleting the features did not restore the shape" % path)
                problems += ["after delete: " + p for p in diff_snap(base_snap, part_snapshot(ctx))]
                if base_bytes is not None and base_bytes != saved_signature(ctx._src):
                    problems.append("saved part document differs after delete")
        return problems, truth
    finally:
        ctx.close()


# ------------------------------------------------------------------ pin test

def build_pin_assembly(pin_radius, hole=True, hole_d=10.0):
    doc = App.newDocument("PinHole")
    asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
    plate = asm.newObject("Part::Box", "Plate")
    plate.Length, plate.Width, plate.Height = 40, 40, 10
    pin = asm.newObject("Part::Cylinder", "Pin")
    pin.Radius, pin.Height = pin_radius, 30
    pin.Placement = App.Placement(App.Vector(20, 20, -10), App.Rotation())
    doc.recompute()
    f = AF.make_hole(asm, (20, 20, 20), (0, 0, -1), hole_d, through=True, scope=["Plate"]) if hole else None
    doc.recompute()
    return doc, asm, f


def pin_problems():
    """A pin through an assembly hole interferes only where the hole does not clear it."""
    out = []
    for r_pin, want_class, want in ((4.0, "close", 1.0), (5.1, "interfering", PI * (5.1 ** 2 - 25.0) * 10.0)):
        doc, asm, f = build_pin_assembly(r_pin)
        try:
            rep = Interference.check(asm, clearance=2.0)
            pairs = [(p["class"], p["volume_mm3"], p["distance_mm"]) for p in rep["pairs"]]
            if len(pairs) != 1 or pairs[0][0] != want_class:
                out.append("pin r=%s with hole: %s" % (r_pin, pairs))
            elif want_class == "interfering" and not _vol_ok(pairs[0][1], want):
                out.append("pin r=%s ring volume %r want %r" % (r_pin, pairs[0][1], want))
            elif want_class == "close" and abs(pairs[0][2] - want) > 1e-6:
                out.append("pin r=%s clearance %r want %r" % (r_pin, pairs[0][2], want))
            AF.set_suppressed(f, True)
            doc.recompute()
            rep2 = Interference.check(asm, clearance=2.0)
            p2 = [(p["class"], p["volume_mm3"]) for p in rep2["pairs"]]
            full = PI * r_pin ** 2 * 10.0
            if len(p2) != 1 or p2[0][0] != "interfering" or not _vol_ok(p2[0][1], full):
                out.append("pin r=%s suppressed hole: %s want interfering %r" % (r_pin, p2, full))
        finally:
            App.closeDocument(doc.Name)
    return out


# ------------------------------------------------------------------ mutants

def _mut_scope_ignored(AFm, I):
    AFm._in_scope = lambda feat, inst: True


def _mut_link_placement_ignored(AFm, I):
    def raw(root, inst):
        lo = inst.objs[-1].getLinkedObject(True)
        return lo.Shape.copy()
    I._instance_shape = raw


def _mut_cut_written_to_source(AFm, I):
    def publish(inst, shape):
        lo = inst.objs[-1].getLinkedObject(True)
        lo.Shape = shape
    AFm._publish = publish


def _alike(select):
    def install(AFm, I):
        memo_rv, memo_cut = {}, {}
        orig_rv, orig_cut = AFm._removed_volume, AFm._cut_one

        def src_key(inst):
            return inst.objs[-1].getLinkedObject(True).Name

        def rv(inst, cur, tool, tol):
            if not select(inst):
                return orig_rv(inst, cur, tool, tol)
            k = src_key(inst)
            if k not in memo_rv:
                memo_rv[k] = orig_rv(inst, cur, tool, tol)
            return memo_rv[k]

        def cut(inst, cur, tool):
            if not select(inst):
                return orig_cut(inst, cur, tool)
            k = src_key(inst)
            if k not in memo_cut:
                res = orig_cut(inst, cur, tool)
                memo_cut[k] = res.transformed(cur.Placement.Matrix.inverse())
            return memo_cut[k].transformed(cur.Placement.Matrix)

        AFm._removed_volume, AFm._cut_one = rv, cut
    return install


def _is_array_element(inst):
    return len(inst.objs) >= 1 and re.search(r"\.\d+$", inst.key) is not None and inst.key.startswith("Arr")


def _mut_hidden_not_cut(AFm, I):
    AFm._hidden_ok = lambda inst: not inst.hidden


def _mut_suppression_ignored(AFm, I):
    AFm._active_features = lambda asm: AFm.features(asm)


def _mut_recompute_does_not_recut(AFm, I):
    AFm.AssemblyFeature.execute = lambda self, obj: None
    AFm._signature = lambda *a, **k: "constant"


def _mut_through_all_short(AFm, I):
    AFm._through_length = lambda start, boxes: 6.0


def _mut_counterbore_ignored(AFm, I):
    orig = AFm._hole_tool

    def f(spec, length):
        spec = dict(spec, cbore_diameter=None, cbore_depth=None)
        return orig(spec, length)
    AFm._hole_tool = f


def _mut_sequence_not_cumulative(AFm, I):
    AFm._input_shape = lambda cur, base, key: base[key]


def _mut_volume_from_bbox(AFm, I):
    def rv(inst, cur, tool, tol):
        a, b = cur.BoundBox, tool.BoundBox
        if not a.intersect(b):
            return 0.0
        dx = max(0.0, min(a.XMax, b.XMax) - max(a.XMin, b.XMin))
        dy = max(0.0, min(a.YMax, b.YMax) - max(a.YMin, b.YMin))
        dz = max(0.0, min(a.ZMax, b.ZMax) - max(a.ZMin, b.ZMin))
        return dx * dy * dz
    AFm._removed_volume = rv


def _mut_touching_counts_as_cut(AFm, I):
    orig = AFm._removed_volume

    def rv(inst, cur, tool, tol):
        v = orig(inst, cur, tool, tol)
        return v if v > tol else (1.0 if cur.BoundBox.intersect(tool.BoundBox) else 0.0)
    AFm._removed_volume = rv


def _mut_local_frame_skipped(AFm, I):
    AFm._to_local = lambda shape, matrix: shape


def _mut_assembly_placement_ignored(AFm, I):
    AFm._asm_matrix = lambda assembly: App.Matrix()


def _mut_interference_reads_raw(AFm, I):
    I._apply_assembly_features = lambda assembly, res: None


def _mut_cuts_not_in_assembly_order(AFm, I):
    orig = AFm.features

    def f(assembly, active_only=False):
        return list(reversed(orig(assembly, active_only)))
    AFm.features = f


# name -> (installer, case that must catch it)
MUTANTS = {
    "scope_ignored": (_mut_scope_ignored, "scope_excludes_one"),
    "link_placement_not_applied": (_mut_link_placement_ignored, "link_placed_and_turned"),
    "cut_written_to_source_part": (_mut_cut_written_to_source, "box_two_components"),
    "link_array_elements_cut_alike": (_alike(_is_array_element), "array_elements_cut_independently"),
    "links_of_one_source_cut_alike": (_alike(lambda inst: True), "link_two_instances_cut_both"),
    "hidden_parts_not_cut": (_mut_hidden_not_cut, "hidden_component_is_cut"),
    "suppression_not_restoring": (_mut_suppression_ignored, "second_feature_suppressed"),
    "recompute_does_not_recut": (_mut_recompute_does_not_recut, "follow_placement_edit"),
    "through_all_too_short": (_mut_through_all_short, "hole_through_stack"),
    "counterbore_ignored": (_mut_counterbore_ignored, "hole_counterbore"),
    "sequence_not_cumulative": (_mut_sequence_not_cumulative, "two_cuts_overlapping"),
    "removed_volume_from_bounding_boxes": (_mut_volume_from_bbox, "hole_blind"),
    "face_contact_counted_as_cut": (_mut_touching_counts_as_cut, "tool_touches_face_only"),
    "local_frame_back_transform_skipped": (_mut_local_frame_skipped, "link_placed_and_turned"),
    "assembly_placement_not_applied_to_tool": (_mut_assembly_placement_ignored, "assembly_placement_applied_to_tool"),
    "interference_reads_raw_shapes": (_mut_interference_reads_raw, "pin_through_hole"),
    "features_applied_out_of_order": (_mut_cuts_not_in_assembly_order, "two_cuts_overlapping"),
}


@contextlib.contextmanager
def mutated(name):
    saved_af = {k: getattr(AF, k) for k in dir(AF) if k.startswith("_") and callable(getattr(AF, k))
                and not k.startswith("__")}
    saved_af["features"] = AF.features
    saved_i = {k: getattr(Interference, k) for k in ("_instance_shape", "_apply_assembly_features")}
    saved_exec = AF.AssemblyFeature.execute
    try:
        MUTANTS[name][0](AF, Interference)
        yield
    finally:
        for k, v in saved_af.items():
            setattr(AF, k, v)
        for k, v in saved_i.items():
            setattr(Interference, k, v)
        AF.AssemblyFeature.execute = saved_exec


# -------------------------------------------------------------------- tests

class TestAssemblyFeature(unittest.TestCase):

    def test_corpus_shape(self):
        counts = {}
        for c in C.CASES.values():
            counts[c.klass] = counts.get(c.klass, 0) + 1
        print("\n  feature corpus: %d cases %s" % (len(C.CASES), counts))
        self.assertGreaterEqual(len(C.CASES), 30)
        for klass in ("box", "extrude", "hole", "miss", "link", "array", "nested", "scope", "hidden",
                      "sequence", "follow"):
            self.assertIn(klass, counts)

    def test_accuracy_all_cases(self):
        tally = Tally()
        failures = {}
        for name in C.CASES:
            with self.subTest(case=name):
                problems, _ = evaluate_case(name, tally)
                if problems:
                    failures[name] = problems
                self.assertEqual(problems, [], name)
        print("\n  %d cases, %d phases: worst relative removed-volume error %.3g (absolute %.3g mm3); %d cut components checked, "
              "%d missed, %d false cuts; %d unchanged components bit-identical; parts untouched checked %d "
              "times, saved part-document bytes %d times; failures %d"
              % (len(C.CASES), tally.phases, tally.max_rel, tally.max_abs, tally.cut_checked, tally.missed_cuts,
                 tally.false_cuts, tally.unchanged_checked, tally.untouched_checks, tally.saved_checks,
                 len(failures)))
        by_class = {}
        for c in C.CASES.values():
            by_class[c.klass] = by_class.get(c.klass, 0) + 1
        print("  cases by class: %s" % by_class)

    def test_saved_signature_is_stable(self):
        ctx, _ = C.build_case("link_placed_and_turned")
        try:
            a, b = saved_signature(ctx.src), saved_signature(ctx.src)
            self.assertEqual(a, b)
            self.assertIn("Document.xml", a)
        finally:
            ctx.close()

    def test_report_names_labels_and_link_paths(self):
        ctx, _ = C.build_case("link_to_part_with_two_solids")
        try:
            rep = AF.report(ctx.asm)
            comps = {c["path"]: c for c in rep["components"]}
            self.assertEqual(comps["LP.b1"]["link_path"], ["LP", "b1"])
            self.assertEqual(comps["LP.b1"]["label"], "b1")
            f = ctx.features[0]
            aff = AF.affected(f)
            self.assertEqual({c["path"] for c in aff["components"]}, {"LP.b1", "LP.b2"})
            self.assertEqual(aff["feature"]["kind"], "cut")
            self.assertEqual(json.loads(json.dumps(aff)), aff)
        finally:
            ctx.close()

    def test_effective_shape_accepts_label_object_and_instance(self):
        ctx, _ = C.build_case("box_corner_cut")
        try:
            a = ctx.asm.getObject("A")
            v = 1000.0 - 60.0
            self.assertTrue(_vol_ok(AF.effective_shape(ctx.asm, "A").Volume, v))
            self.assertTrue(_vol_ok(AF.effective_shape(ctx.asm, a).Volume, v))
            self.assertTrue(_vol_ok(AF.effective_shape(ctx.asm, raw_map(ctx.asm)["A"]).Volume, v))
            self.assertIsNone(AF.effective_shape(ctx.asm, "NoSuchPart"))
        finally:
            ctx.close()

    def test_effective_shape_is_a_copy(self):
        ctx, _ = C.build_case("box_corner_cut")
        try:
            e = AF.effective_shape(ctx.asm, "A")
            h = brep_hash(AF.effective_shape(ctx.asm, "A"))
            e.translate(App.Vector(5, 5, 5))
            self.assertEqual(brep_hash(AF.effective_shape(ctx.asm, "A")), h)
        finally:
            ctx.close()

    def test_placement_edit_reruns_the_feature(self):
        ctx, _ = C.build_case("follow_placement_edit")
        try:
            f = ctx.features[0]
            n0 = f.Proxy.executions
            ctx.doc.recompute()
            self.assertEqual(f.Proxy.executions, n0)   # nothing moved: the feature is not re-run
            ctx.asm.getObject("A").Placement = C.plc(6, 0, 0)
            ctx.doc.recompute()
            self.assertGreater(f.Proxy.executions, n0)  # a placement edit re-runs the feature
        finally:
            ctx.close()

    def test_follows_a_move_without_recompute(self):
        ctx, _ = C.build_case("follow_placement_edit")
        try:
            a = ctx.asm.getObject("A")
            self.assertTrue(_vol_ok(AF.effective_shape(ctx.asm, "A").Volume, 200.0))
            a.Placement = C.plc(6, 0, 0)
            self.assertTrue(_vol_ok(AF.effective_shape(ctx.asm, "A").Volume, 800.0))
        finally:
            ctx.close()

    def test_suppress_and_delete_restore_original(self):
        ctx, _ = C.build_case("hole_through_stack")
        try:
            raw = {k: brep_hash(v.shape) for k, v in raw_map(ctx.asm).items()}
            f = ctx.features[0]
            self.assertNotEqual(brep_hash(AF.effective_shape(ctx.asm, "P1")), raw["P1"])
            AF.set_suppressed(f, True)
            for k in raw:
                self.assertEqual(brep_hash(AF.effective_shape(ctx.asm, k)), raw[k])
            self.assertEqual(AF.affected(f)["components"], [])
            AF.set_suppressed(f, False)
            self.assertNotEqual(brep_hash(AF.effective_shape(ctx.asm, "P1")), raw["P1"])
            ctx.doc.removeObject(f.Name)
            for k in raw:
                self.assertEqual(brep_hash(AF.effective_shape(ctx.asm, k)), raw[k])
        finally:
            ctx.close()

    def test_undo_and_redo_are_exact(self):
        doc = App.newDocument("UndoFeat")
        doc.UndoMode = 1
        try:
            asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
            for i, n in enumerate(("P1", "P2")):
                b = asm.newObject("Part::Box", n)
                b.Length, b.Width, b.Height = 20, 20, 5
                b.Placement = C.plc(0, 0, 6 * i)
            doc.recompute()
            raw = {k: brep_hash(v.shape) for k, v in raw_map(asm).items()}
            sig0 = part_snapshot(type("X", (), {"docs": [doc]})())
            doc.openTransaction("hole")
            AF.make_hole(asm, (10, 10, 20), (0, 0, -1), 4, through=True)
            doc.commitTransaction()
            doc.recompute()
            cut = {k: brep_hash(AF.effective_shape(asm, k)) for k in raw}
            self.assertTrue(all(cut[k] != raw[k] for k in raw))
            doc.undo()
            doc.recompute()
            self.assertEqual([o.Name for o in asm.Group], ["P1", "P2"])
            self.assertEqual({k: brep_hash(AF.effective_shape(asm, k)) for k in raw}, raw)
            self.assertEqual(diff_snap(sig0, part_snapshot(type("X", (), {"docs": [doc]})())), [])
            doc.redo()
            doc.recompute()
            self.assertEqual({k: brep_hash(AF.effective_shape(asm, k)) for k in raw}, cut)
            # undo and redo of a placement edit with the feature in place re-cuts where the part sits
            moved = {}
            doc.openTransaction("move")
            asm.getObject("P1").Placement = C.plc(5, 0, 0)
            doc.commitTransaction()
            doc.recompute()
            moved = brep_hash(AF.effective_shape(asm, "P1"))
            self.assertNotEqual(moved, cut["P1"])
            doc.undo()
            doc.recompute()
            self.assertEqual(brep_hash(AF.effective_shape(asm, "P1")), cut["P1"])
            doc.redo()
            doc.recompute()
            self.assertEqual(brep_hash(AF.effective_shape(asm, "P1")), moved)
        finally:
            App.closeDocument(doc.Name)

    def test_feature_survives_save_and_reopen(self):
        ctx, _ = C.build_case("hole_counterbore")
        tmp = tempfile.mkdtemp()
        try:
            want = brep_hash(AF.effective_shape(ctx.asm, "Plate"))
            path = os.path.join(tmp, "asm.FCStd")
            ctx.doc.saveAs(path)
            name = ctx.doc.Name
            App.closeDocument(name)
            doc2 = App.openDocument(path)
            asm2 = doc2.getObject("Assembly")
            f = AF.features(asm2)[0]
            self.assertEqual(f.Kind, "hole")
            self.assertEqual(brep_hash(AF.effective_shape(asm2, "Plate")), want)
            self.assertTrue(_vol_ok(1000.0 * 4 - AF.effective_shape(asm2, "Plate").Volume, PI * 16 * 3 + PI * 4 * 7))
            App.closeDocument(doc2.Name)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_pin_through_assembly_hole(self):
        self.assertEqual(pin_problems(), [])

    def test_interference_without_features_unchanged_path(self):
        doc = App.newDocument("NoFeat")
        try:
            asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
            a = asm.newObject("Part::Box", "A")
            b = asm.newObject("Part::Box", "B")
            b.Placement = C.plc(8, 0, 0)
            doc.recompute()
            rep = Interference.check(asm)
            self.assertEqual(len(rep["pairs"]), 1)
            self.assertTrue(_vol_ok(rep["pairs"][0]["volume_mm3"], 200.0))
            self.assertIsNotNone(a)
        finally:
            App.closeDocument(doc.Name)

    def test_cut_component_listed_when_swallowed(self):
        ctx, _ = C.build_case("box_tool_swallows_part")
        try:
            rep = Interference.check(ctx.asm)
            self.assertEqual(rep["stats"]["instances"], 0)
            self.assertEqual([s["shape_type"] for s in rep["skipped_non_solid"]], ["Empty"])
            self.assertEqual(AF.effective_shape(ctx.asm, "A").Volume, 0.0)
        finally:
            ctx.close()

    def test_invalid_arguments(self):
        doc = App.newDocument("Args")
        try:
            asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
            with self.assertRaises(ValueError):
                AF.extrude_tool((0, 0, 1))
            with self.assertRaises(ValueError):
                AF.make_hole(asm, (0, 0, 0), (0, 0, -1), 4)
            with self.assertRaises(ValueError):
                AF.make_hole(asm, (0, 0, 0), (0, 0, -1), 4, depth=5, cbore_diameter=3, cbore_depth=1)
            with self.assertRaises(ValueError):
                AF.make_hole(asm, (0, 0, 0), (0, 0, -1), 4, depth=5, cbore_diameter=6)
            with self.assertRaises(ValueError):
                AF.make_cut(asm, AF.solid_tool())
        finally:
            App.closeDocument(doc.Name)

    def test_two_runs_identical(self):
        reps = []
        for _ in range(2):
            ctx, _t = C.build_case("hole_through_stack")
            try:
                reps.append(json.dumps(AF.report(ctx.asm), sort_keys=True))
            finally:
                ctx.close()
        self.assertEqual(reps[0], reps[1])

    def test_mutants_are_caught(self):
        self.assertGreaterEqual(len(MUTANTS), 10)
        table = []
        for mname, (_inst, catcher) in MUTANTS.items():
            with self.subTest(mutant=mname):
                failing = []
                with mutated(mname):
                    for name in C.CASES:
                        try:
                            problems, _ = evaluate_case(name, with_delete=False)
                        except Exception as exc:  # a crash also counts as a failing case
                            problems = [repr(exc)]
                        if problems:
                            failing.append(name)
                    try:
                        if pin_problems():
                            failing.append("pin_through_hole")
                    except Exception:
                        failing.append("pin_through_hole")
                table.append((mname, catcher, failing))
                self.assertTrue(failing, "mutant %s survived" % mname)
                self.assertIn(catcher, failing, "mutant %s not caught by %s" % (mname, catcher))
        print("\n  mutants: %d planted, %d caught (catching case / total failing cases)"
              % (len(table), sum(1 for _m, _c, f in table if f)))
        for mname, catcher, failing in table:
            print("    %-40s %-36s %d" % (mname, catcher, len(failing)))
        # the unmutated corpus still passes after every patch was restored
        for name in C.CASES:
            self.assertEqual(evaluate_case(name, with_delete=False)[0], [], name)
        self.assertEqual(pin_problems(), [])
