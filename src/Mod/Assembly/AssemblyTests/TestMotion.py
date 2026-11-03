# SPDX-License-Identifier: LGPL-2.1-or-later
"""G06 slice 6a: motion studies with gravity, forces, springs and dampers (API and closed-form corpus, no GUI).

The corpus (``motion_corpus``) runs every case in a single-document assembly and through ``App::Link`` parts and
compares the stored result with the closed form; the other tests cover the model and round trip (save and reopen,
repeat runs, undo, part documents, JSON), the interplay with slices 1, 3 and 5, the refusals, and the mutants
(planted defects in ``AssemblyMotion``, each of which must make at least one case or probe fail).
"""

import contextlib
import hashlib
import json
import os
import shutil
import tempfile
import unittest
from collections import Counter

import FreeCAD as App

import AssemblyConfiguration as AC
import AssemblyMotion as MO
import AssemblySkeleton as AS
import Preferences
from AssemblyTests import motion_corpus as C

V = App.Vector
P = App.Placement
R = App.Rotation
MODES = ("single", "link")
KINEMATIC = [n for n, f in C.CASES.items() if "slider_crank" in n or "four_bar" in n or "gears" in n
             or "belt" in n or "rack_pinion" in n]


def run_case(name, mode, keep=False):
    return C.evaluate(name, mode, keep=keep)


def failing_rows(rows):
    return [r for r in rows if not r["ok"]]


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


# ------------------------------------------------------------------ probes (each returns a list of problems)

def probe_part_documents_unchanged():
    """A run, dynamic and kinematic, leaves the part documents as they were: signature, shape digests, saved bytes."""
    problems = []
    for name in ("pend_30_L400", "slider_crank_r20_L60"):
        doc, xdoc, tmp = C.open_docs(name, "xdoc")
        try:
            c = C.CASES[name](doc, "link", xdoc)
            xdoc.recompute()
            path = xdoc.FileName
            xdoc.save()
            before_sig, before_hash, before_mtime = MO.signature(xdoc), sha(path), os.path.getmtime(path)
            before_touched = [o.Name for o in xdoc.Objects if "Touched" in o.State]
            MO.run(c.study)
            after = MO.signature(xdoc)
            if after != before_sig:
                changed = sorted(k for k in set(after) | set(before_sig) if after.get(k) != before_sig.get(k))
                problems.append("%s: the part document changed (%s)" % (name, changed[:3]))
            if sha(path) != before_hash or os.path.getmtime(path) != before_mtime:
                problems.append("%s: the part file on disk changed" % name)
            if [o.Name for o in xdoc.Objects if "Touched" in o.State] != before_touched:
                problems.append("%s: the run touched part objects" % name)
        finally:
            C.close_docs(doc, xdoc, directory=tmp)
    return problems


def probe_run_restores_placements():
    """The assembly is back at its pre-run placements after a run (the kinematic mode moves parts while it runs)."""
    problems = []
    for name in ("slider_crank_r20_L60", "gears_r30_r15"):
        for mode in MODES:
            doc, xdoc, tmp = C.open_docs(name, mode)
            try:
                c = C.CASES[name](doc, mode, xdoc)
                comps = MO.live_components(c.rig.asm)
                before = {o.Name: P(o.Placement) for o in comps}
                res = MO.run(c.study)
                moved = max((pl["base"][0] - res["parts"][0]["placements"][0]["base"][0]) for pl in
                            res["parts"][1]["placements"]) if len(res["parts"]) > 1 else 0
                for o in comps:
                    if not o.Placement.isSame(before[o.Name], 0.0):
                        problems.append("%s/%s: %s was left at %s, was %s" % (name, mode, o.Name, o.Placement, before[o.Name]))
                steps = res["t"]
                k = len(steps) // 3
                MO.run(c.study, keep_frame=k)
                for p in res["parts"]:
                    o = doc.getObject(p["name"])
                    want = p["placements"][k]
                    got = o.Placement
                    if (got.Base - V(*want["base"])).Length > 1e-9:
                        problems.append("%s/%s: keep_frame %d did not leave %s at the frame" % (name, mode, k, o.Name))
                        break
            finally:
                C.close_docs(doc, xdoc, directory=tmp)
    return problems


def probe_undo():
    """Add, edit and remove of a load are one transaction each and undo restores the signature exactly; so does a
    run (the dynamic one writes the result, the kinematic one creates and removes its helper objects)."""
    problems = []
    doc = App.newDocument("MotionUndo")
    try:
        c = C.CASES["spring_mass_k200"](doc, "single", None)
        study, rig = c.study, c.rig
        doc.recompute()

        def sig():
            return MO.signature(doc)

        def undo_once(label, before):
            n = doc.UndoCount
            doc.undo()
            if doc.UndoCount != n - 1:
                problems.append("%s: undo did not step back exactly one transaction" % label)
            if sig() != before:
                problems.append("%s: undo did not restore the object-and-property signature" % label)

        s0 = sig()
        n0 = doc.UndoCount
        f = MO.add_force(study, rig.parts["Block"], (25, 25, 25), (1, 0, 0))
        if doc.UndoCount != n0 + 1:
            problems.append("add load is not one transaction (%d)" % (doc.UndoCount - n0))
        s1 = sig()
        undo_once("add load", s0)
        doc.redo()
        n1 = doc.UndoCount
        MO.edit_load(f, Value=(2, 0, 0), Frame="Part")
        if doc.UndoCount != n1 + 1:
            problems.append("edit load is not one transaction")
        s2 = sig()
        undo_once("edit load", s1)
        doc.redo()
        n2 = doc.UndoCount
        MO.remove_load(f)
        if doc.UndoCount != n2 + 1:
            problems.append("remove load is not one transaction")
        undo_once("remove load", s2)
        s3 = sig()
        MO.run(study)
        undo_once("dynamic run", s3)
        # the kinematic mode: helper simulation objects appear and disappear inside the run
        for name in ("slider_crank_r20_L60", "gears_r30_r15"):
            d2 = App.newDocument("MotionUndoK")
            try:
                ck = C.CASES[name](d2, "single", None)
                d2.recompute()
                sk = MO.signature(d2)
                MO.run(ck.study)
                if sorted(o.Name for o in d2.Objects if o.Name.startswith("Motion") and o.Name != ck.study.Name):
                    problems.append("%s: the kinematic run left helper objects behind" % name)
                d2.undo()
                if MO.signature(d2) != sk:
                    problems.append("%s: undo of a kinematic run did not restore the signature" % name)
            finally:
                App.closeDocument(d2.Name)
    finally:
        App.closeDocument(doc.Name)
    return problems


def probe_two_runs_identical():
    problems = []
    for name in ("pend_60_L500", "double_pendulum_small", "slider_crank_r20_L60"):
        doc = App.newDocument("MotionTwice")
        try:
            c = C.CASES[name](doc, "single", None)
            MO.run(c.study)
            a = c.study.Result
            MO.run(c.study)
            b = c.study.Result
            if a != b or not a:
                problems.append("%s: two runs differ" % name)
        finally:
            App.closeDocument(doc.Name)
    return problems


PROBES = {"part_documents": probe_part_documents_unchanged, "restores_placements": probe_run_restores_placements,
          "undo": probe_undo, "two_runs": probe_two_runs_identical}


def save_reopen_problems(name="spring_mass_k200", mode="single"):
    """Save, close, reopen: the objects and the result are as saved, and running again gives the same bytes."""
    problems = []
    tmp = tempfile.mkdtemp(prefix="motionsave")
    doc = App.newDocument("MotionSave")
    try:
        c = C.CASES[name](doc, mode, None)
        part = [p for n, p in c.rig.parts.items() if n != "G"][0]
        MO.add_force(c.study, part, (1, 2, 3), (0.5, 0, 0))
        n_loads, n_masses = len(MO.loads(c.study)), len(MO.masses(c.study))
        MO.run(c.study)
        mine = [c.study.Name] + [o.Name for o in c.study.Group]
        sig0 = MO.signature(doc)
        res0 = c.study.Result
        path = os.path.join(tmp, "asm.FCStd")
        doc.saveAs(path)
        App.closeDocument(doc.Name)
        doc2 = App.openDocument(path)
        asm2 = [o for o in doc2.Objects if o.TypeId == "Assembly::AssemblyObject"][0]
        st = MO.studies(asm2)
        if len(st) != 1:
            problems.append("reopen: %d studies" % len(st))
            return problems
        s2 = st[0]
        if s2.Result != res0:
            problems.append("reopen: the stored result is not bit-identical")
        if len(MO.loads(s2)) != n_loads:
            problems.append("reopen: %d loads, want %d" % (len(MO.loads(s2)), n_loads))
        if len(MO.masses(s2)) != n_masses:
            problems.append("reopen: %d masses, want %d" % (len(MO.masses(s2)), n_masses))
        sig1 = MO.signature(doc2)
        # the study, its loads and masses come back exactly; the placements of the parts and the joints are the
        # core's own and the file keeps 14 significant digits of them (the run is immune, see MO._q)
        diff = sorted(k for k in mine if sig0.get(k) != sig1.get(k))
        if diff:
            problems.append("reopen: signature differs for %s" % diff[:4])
        MO.run(s2)
        if s2.Result != res0:
            problems.append("reopen: running again does not give the same bytes")
        App.closeDocument(doc2.Name)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        if "MotionSave" in App.listDocuments():
            App.closeDocument("MotionSave")
    return problems


# ------------------------------------------------------------------ mutants

@contextlib.contextmanager
def patched(target=MO, **fns):
    saved = {k: getattr(target, k) for k in fns}
    try:
        for k, v in fns.items():
            setattr(target, k, v)
        yield
    finally:
        for k, v in saved.items():
            setattr(target, k, v)


def _np():
    import numpy
    return numpy


def _m_gravity_sign():
    return dict(_gravity_vector=lambda ld, np: -np.array(MO._vec(ld.Value)))


def _m_gravity_axis():
    return dict(_gravity_vector=lambda ld, np: np.array([MO._vec(ld.Value)[2], 0.0, 0.0]))


def _m_spring_sign():
    return dict(_spring_force=lambda k, L, L0: -k * (L - L0))


def _m_free_length():
    return dict(_free_length=lambda ld: 0.0)


def _m_damper_stiffness():
    def route(model, b1, x1, b2, x2, c):
        L = float(_np().linalg.norm(x2 - x1))
        model.springs.append((b1, x1, b2, x2, c, L))
    return dict(_route_damper=route)


def _m_damper_sign():
    return dict(_damper_force=lambda c, vr: -c * vr)


def _m_no_density():
    return dict(_density_of=lambda m: 1.0)


def _m_inertia_origin():
    real = MO._shape_inertia

    def about_origin(shape):
        inertia, com = real(shape)
        v, c = shape.Volume, _np().array([com.x, com.y, com.z])
        I = _np().array(inertia) + v * ((c @ c) * _np().eye(3) - _np().outer(c, c))
        return I.tolist(), com
    return dict(_shape_inertia=about_origin)


def _m_step_doubled():
    real = MO._grid

    def grid(study):
        step, g = real(study)
        return step, [2 * t for t in g]
    return dict(_grid=grid)


def _m_no_energy():
    return dict(_energy=lambda ke, pe, pes: (0.0, 0.0, 0.0))


def _m_suppressed_load_applied():
    return dict(_is_live=lambda model, obj: True)


def _m_write_into_parts():
    real = MO._write_series

    def write(study, text):
        real(study, text)
        asm = MO.assembly_of(study)
        for o in MO.live_components(asm):
            src = o.getLinkedObject(True)
            if src is not None and src is not o and "MotionResult" not in src.PropertiesList:
                src.addProperty("App::PropertyString", "MotionResult", "Motion", "")
                src.MotionResult = text[:20]
    return dict(_write_series=write)


def _m_not_restored():
    return dict(_restore_placements=lambda assembly, saved: None)


def _m_bias_dropped():
    return dict(_scale_bias=lambda h: 0.0 * h)


def _m_force_frame():
    return dict(_force_world=lambda Rd, R0, F, local: F)


def _m_no_rotational_inertia():
    return dict(_inertia_world=lambda Rd, I0: 0.0 * I0)


def _m_com_at_origin():
    return dict(Model_com=lambda self, com: 0.0 * com)


MUTANTS = {
    "gravity_wrong_sign": (_m_gravity_sign, ["fall_vertical", "pend_30_L400"], []),
    "gravity_wrong_axis": (_m_gravity_axis, ["fall_vertical", "pend_30_L400"], []),
    "spring_wrong_sign": (_m_spring_sign, ["spring_mass_k200", "spring_mass_vertical_k500"], []),
    "spring_free_length_ignored": (_m_free_length, ["spring_mass_k200", "spring_mass_vertical_k500"], []),
    "damping_applied_as_stiffness": (_m_damper_stiffness, ["damped_under_z010", "damped_critical"], []),
    "damper_sign_flipped": (_m_damper_sign, ["damped_under_z010", "damped_over_z2"], []),
    "mass_from_volume_without_density": (_m_no_density, ["compound_rod_end", "compound_disc_rim"], []),
    "inertia_about_the_wrong_point": (_m_inertia_origin, ["compound_rod_end", "compound_disc_rim"], []),
    "time_step_doubled_silently": (_m_step_doubled, ["fall_vertical", "pend_30_L400"], []),
    "energy_not_computed": (_m_no_energy, ["pend_30_L400", "spring_mass_k200"], []),
    "suppressed_components_load_still_applied": (
        _m_suppressed_load_applied, ["suppressed_spring_to_pendulum", "suppressed_damper_to_pendulum"], []),
    "results_written_into_part_documents": (_m_write_into_parts, [], ["part_documents"]),
    "placements_not_restored_after_a_run": (_m_not_restored, [], ["restores_placements"]),
    "velocity_products_dropped": (_m_bias_dropped, ["pend_120_L500", "double_pendulum_large"], []),
    "force_frame_ignored": (_m_force_frame, ["force_in_part_frame"], []),
    "rotational_inertia_ignored": (_m_no_rotational_inertia, ["compound_rod_end"], []),
    "centre_of_mass_at_the_part_origin": (_m_com_at_origin, ["compound_rod_end", "compound_rod_quarter"], []),
}


@contextlib.contextmanager
def mutated(name):
    spec = MUTANTS[name][0]()
    com = spec.pop("Model_com", None)
    saved = MO.Model._com
    try:
        if com is not None:
            MO.Model._com = com
        with patched(**spec):
            yield
    finally:
        MO.Model._com = saved


# ------------------------------------------------------------------ the tests

class TestMotion(unittest.TestCase):

    def setUp(self):
        self.pref = Preferences.preferences()
        self._old = self.pref.GetBool("SolveInJointCreation", True)
        self.pref.SetBool("SolveInJointCreation", False)

    def tearDown(self):
        self.pref.SetBool("SolveInJointCreation", self._old)

    # -- check 1 and 2
    def test_corpus_shape(self):
        counts = Counter(c.split("/")[0] for c in self._classes())
        print("\n  motion corpus: %d cases %s, each in %s" % (len(C.CASES), dict(counts), MODES))
        self.assertGreaterEqual(len(C.CASES) - 1, 30)          # without the falling ball, which is check 4
        for klass in ("kinematics", "dynamics", "interplay"):
            self.assertIn(klass, counts)
        self.assertGreaterEqual(len(KINEMATIC), 8)

    def _classes(self):
        out = []
        for name in C.CASES:
            doc = App.newDocument("Shape")
            try:
                out.append(C.CASES[name](doc, "single", None).klass)
            finally:
                App.closeDocument(doc.Name)
        return out

    def test_accuracy_all_cases(self):
        rows = C.Rows()
        table = {}
        for name in C.CASES:
            for mode in MODES:
                with self.subTest(case=name, mode=mode):
                    try:
                        r, c, _res = run_case(name, mode)
                    except Exception:
                        import traceback
                        self.fail("%s/%s crashed: %s" % (name, mode, traceback.format_exc()))
                    rows.extend(r)
                    table[name] = (c.klass, c.integrator, c.step, c.end)
                    bad = failing_rows(r)
                    self.assertEqual(bad, [], "%s/%s" % (name, mode))
        print("\n  %d cases x %d modes, %d checks, worst margin below:" % (len(table), len(MODES), len(rows)))
        worst = {}
        for r in rows:
            key = r["case"]
            if r["bar"] > 0:
                worst[key] = max(worst.get(key, 0.0), r["error"] / r["bar"])
        for name, (klass, integ, step, end) in table.items():
            print("    %-30s %-28s %-12s step %-10.4g end %-8.4g worst error/bar %.2g" % (
                name, klass, integ, step, end, worst.get(name, 0.0)))

    def test_cases_through_a_linked_part_document(self):
        for name in ("pend_60_L500", "spring_mass_k800", "damped_under_z010", "slider_crank_r20_L60", "gears_r30_r15"):
            with self.subTest(case=name):
                rows, _c, _r = run_case(name, "xdoc")
                self.assertEqual(failing_rows(rows), [], name)

    # -- check 3
    def test_save_and_reopen_bit_identical(self):
        self.assertEqual(save_reopen_problems(), [])
        self.assertEqual(save_reopen_problems("compound_rod_end"), [])
        self.assertEqual(save_reopen_problems("pend_120_L500", "link"), [])
        self.assertEqual(save_reopen_problems("slider_crank_r35_L90"), [])

    def test_two_runs_bit_identical(self):
        self.assertEqual(probe_two_runs_identical(), [])

    def test_undo_is_exact_and_each_edit_is_one_transaction(self):
        self.assertEqual(probe_undo(), [])

    def test_part_documents_unchanged(self):
        self.assertEqual(probe_part_documents_unchanged(), [])

    def test_run_restores_the_assembly(self):
        self.assertEqual(probe_run_restores_placements(), [])

    def test_existing_simulation_objects_untouched(self):
        doc = App.newDocument("MotionSim")
        try:
            c = C.CASES["slider_crank_r20_L60"](doc, "single", None)
            asm = c.rig.asm
            sim = asm.newObject("App::FeaturePython", "Sim")
            sim.addExtension("App::GroupExtensionPython")
            for n, t, v in (("aTimeStart", "App::PropertyTime", 0.0), ("bTimeEnd", "App::PropertyTime", 1.0),
                            ("cTimeStepOutput", "App::PropertyTime", 0.1), ("fGlobalErrorTolerance", "App::PropertyFloat", 1e-6)):
                sim.addProperty(t, n, "Simulation", "")
                setattr(sim, n, v)
            doc.recompute()
            before = MO.signature(doc)["Sim"]
            for name in ("slider_crank_r20_L60",):
                MO.run(c.study)
            self.assertEqual(MO.signature(doc)["Sim"], before)
            self.assertEqual(asm.generateSimulation(sim), 0)        # the plain Simulation still runs
        finally:
            App.closeDocument(doc.Name)

    def test_result_is_json_with_entity_ids(self):
        doc = App.newDocument("MotionJson")
        try:
            c = C.CASES["spring_mass_k200"](doc, "single", None)
            MO.run(c.study)
            res = MO.to_json(c.study)
            res2 = json.loads(json.dumps(res))
            self.assertEqual(res, res2)
            self.assertEqual(res["schema"], MO.SCHEMA)
            block = c.rig.parts["Block"]
            self.assertEqual([p["entity"] for p in res["parts"]], [MO._entity(block)])
            self.assertEqual(sorted(x["entity"] for x in res["loads"]),
                             sorted(MO._entity(o) for o in MO.loads(c.study)))
            self.assertTrue(all(x["entity"] for x in res["joints"]))
            self.assertEqual(len(res["parts"][0]["placements"]), len(res["t"]))
            self.assertEqual(res["integrator"], "DOP853")
        finally:
            App.closeDocument(doc.Name)

    # -- the refusals
    def test_unsupported_mechanisms_are_refused_not_guessed(self):
        doc = App.newDocument("MotionRefuse")
        try:
            c = C.CASES["slider_crank_r20_L60"](doc, "single", None)
            c.study.Mode = "Dynamic"
            for nm, blk in c.rig.parts.items():
                if nm != "G":
                    MO.set_mass(c.study, blk, density=C.STEEL)
            with self.assertRaises(MO.MotionRefused) as cm:
                MO.run(c.study)
            self.assertIn("closed loop", str(cm.exception))
        finally:
            App.closeDocument(doc.Name)
        doc = App.newDocument("MotionRefuse2")
        try:
            c = C.CASES["gears_r30_r15"](doc, "single", None)
            c.study.Mode = "Dynamic"
            for nm, blk in c.rig.parts.items():
                if nm != "G":
                    MO.set_mass(c.study, blk, density=C.STEEL)
            with self.assertRaises(MO.MotionRefused) as cm:
                MO.run(c.study)
            self.assertIn("Gears", str(cm.exception))
            self.assertEqual(c.study.Result, "")
        finally:
            App.closeDocument(doc.Name)
        doc = App.newDocument("MotionRefuse3")
        try:
            c = C.CASES["pend_30_L400"](doc, "single", None)
            for m in MO.masses(c.study):
                MO.remove_mass(m)
            with self.assertRaises(MO.MotionError) as cm:
                MO.run(c.study)
            self.assertIn("no mass", str(cm.exception))
        finally:
            App.closeDocument(doc.Name)

    # -- check 4
    def test_falling_ball_first_collision_within_one_step(self):
        doc = App.newDocument("MotionBall")
        try:
            c = C.CASES["falling_ball_on_plate"](doc, "single", None)
            MO.run(c.study)
            hit = MO.first_collision(c.study, c.ball, c.plate, classes=("interfering", "touching"))
            self.assertIsNotNone(hit)
            step = c.study.TimeStep
            print("\n  falling ball: contact at t=%.5f s analytic, first colliding step %d at t=%.5f s (step %g s, %s)" % (
                c.contact_time, hit["step"], hit["time"], step, hit["class"]))
            self.assertLessEqual(abs(hit["time"] - c.contact_time), step)
            self.assertGreaterEqual(hit["time"], c.contact_time - 1e-12)
            # the assembly is back where it started
            self.assertTrue(c.ball.Placement.isSame(P(V(0, 0, 425), R()), 0.0))
            # a pair that never meets gives None
            self.assertIsNone(MO.first_collision(c.study, c.ball, c.rig.G, classes=("interfering",),
                                                 ignore=[[c.ball.Name, c.rig.G.Name]]))
        finally:
            App.closeDocument(doc.Name)

    def test_suppressed_component_and_its_load_are_left_out(self):
        for name in ("suppressed_spring_to_pendulum", "suppressed_damper_to_pendulum"):
            for mode in MODES:
                rows, _c, res = run_case(name, mode)
                self.assertEqual(failing_rows(rows), [], "%s/%s" % (name, mode))
        # and restored when the configuration is left: the load acts again
        doc = App.newDocument("MotionSupp")
        try:
            c = C.CASES["suppressed_spring_to_pendulum"](doc, "single", None)
            res1 = MO.run(c.study)
            AC.activate(c.rig.asm, None)
            res2 = MO.run(c.study)
            self.assertFalse(res1["loads"][1]["applied"] if res1["loads"][1]["kind"] == "Spring" else True)
            sp = [x for x in res2["loads"] if x["kind"] == "Spring"][0]
            self.assertTrue(sp["applied"])
            self.assertNotEqual(res1["joints"][0]["q"][-1], res2["joints"][0]["q"][-1])
            # suppressing the part took nothing else out of the assembly
            AC.activate(c.rig.asm, "NoAnchor")
            names = [o.Name for o in c.rig.asm.Group]
            for want in ("Bob", "Hinge", "MotionStudy", "Mass", "Gravity", "Spring"):
                self.assertIn(want, names)
        finally:
            App.closeDocument(doc.Name)

    def test_skeleton_subscriber_moves_with_its_part(self):
        from AssemblyTests import skeleton_corpus as K
        w = K.World(False)
        try:
            asm = w.doc.addObject("Assembly::AssemblyObject", "Assembly")
            jg = asm.newObject("Assembly::JointGroup", "Joints")
            ground = asm.newObject("Part::Box", "G")
            ground.Length, ground.Width, ground.Height = 100, 100, 10
            C.cc.ground(jg, ground)
            body = asm.newObject("PartDesign::Body", "Comp")
            w.publish("face")
            rec = AS.subscribe(body, AS.find_item(w.body, "face"), object_name="Sub_face")
            binder = w.doc.getObject(rec["object"])
            w.doc.recompute()
            pivot = V(0, 0, 200)
            body.Placement = P(V(0, 0, 0), R())
            fa = ground.Placement.inverse().multiply(C.frame_at(tuple(pivot), (0, 1, 0)))
            fb = body.Placement.inverse().multiply(C.frame_at(tuple(pivot), (0, 1, 0)))
            C.cc.jt(jg, "Hinge", "Revolute", ground, fa, body, fb)
            w.doc.recompute()
            study = MO.create_study(asm, time_end=0.5, step=0.05)
            MO.set_mass(study, body, mass=1.0, com=(60, 0, 100), inertia=[0, 0, 0, 0, 0, 0])
            MO.add_gravity(study)
            before_digest = AS.shape_digest(binder.Shape)
            before_state = AS.describe_subscription(binder)["state"]
            res = MO.run(study, keep_frame=6)
            w.doc.recompute()
            moved = body.Placement
            self.assertGreater((moved.Base - V(0, 0, 0)).Length + abs(moved.Rotation.Angle), 1e-3)
            kept = AS.shape_digest(binder.Shape)
            self.assertNotEqual(kept, before_digest)        # it did move with the part
            # the subscriber follows the part exactly as it does for any other placement of that part
            MO.apply_frame(study, 0)
            w.doc.recompute()
            self.assertEqual(AS.shape_digest(binder.Shape), before_digest)
            self.assertEqual(AS.describe_subscription(binder)["state"], before_state)
            ref = P(V(*res["parts"][0]["placements"][6]["base"]), R(*res["parts"][0]["placements"][6]["rotation"]))
            body.Placement = ref
            w.doc.recompute()
            self.assertEqual(AS.shape_digest(binder.Shape), kept)
            self.assertEqual(AS.describe_subscription(binder)["state"], before_state)
        finally:
            w.close()

    # -- check 5
    def test_mutants_are_caught(self):
        self.assertGreaterEqual(len(MUTANTS), 12)
        table = []
        for mname, (_spec, cases, probes) in MUTANTS.items():
            with self.subTest(mutant=mname):
                failing = []
                with mutated(mname):
                    for name in cases:
                        try:
                            rows, _c, _r = run_case(name, "single")
                            if failing_rows(rows):
                                failing.append(name)
                        except Exception:
                            failing.append(name)
                    for pname in probes:
                        try:
                            if PROBES[pname]():
                                failing.append("probe:" + pname)
                        except Exception:
                            failing.append("probe:" + pname)
                table.append((mname, failing))
                self.assertTrue(failing, "mutant %s survived" % mname)
        for name in ("pend_30_L400", "fall_vertical", "compound_rod_end"):
            rows, _c, _r = run_case(name, "single")
            self.assertEqual(failing_rows(rows), [], name)           # nothing stays patched
        print("\n  mutants: %d planted, %d caught" % (len(table), sum(1 for _m, f in table if f)))
        for mname, failing in table:
            print("    %-44s %s" % (mname, ", ".join(failing)))


if __name__ == "__main__":
    unittest.main()
