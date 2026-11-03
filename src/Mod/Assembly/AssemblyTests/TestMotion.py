# SPDX-License-Identifier: LGPL-2.1-or-later
"""G06 slices 6a and 6c: motion studies with gravity, forces, springs, dampers, contact and joint stops (API and
closed-form corpus, no GUI).

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
import time
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
CONTACT = [n for n in C.CASES if n.startswith(("ball_", "block_", "stop_", "contact_", "suppressed_shelf"))]
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


def ball_over_plate(doc, gap_mm, e=0.7, end=0.02, step=0.002, v_up=None):
    """A steel ball ``gap_mm`` above the grounded plate (negative: sunk into it) with a contact and gravity."""
    rig = C.Rig(doc, "single")
    ball = rig.sph("Ball", 25.0, C.P(C.V(0, 0, gap_mm + 25.0), C.R()))
    rig.slide("Rail", rig.G, ball, (0, 0, gap_mm + 25.0), (0, 0, -1))
    rig.finish()
    study = C.new_study(rig, end, step)
    MO.set_mass(study, ball, density=C.STEEL)
    MO.add_gravity(study)
    MO.add_contact(study, ball, rig.G, e)
    return rig, study, ball


def _run_is_refused(gap_mm):
    doc = App.newDocument("MotionStartRefusal")
    try:
        _rig, study, _ball = ball_over_plate(doc, gap_mm)
        try:
            MO.run(study)
        except MO.MotionRefused:
            return True
        return False
    finally:
        App.closeDocument(doc.Name)


def probe_overlapping_start_is_refused():
    return [] if _run_is_refused(-10.0) else ["a ball sunk 10 mm into the plate was accepted"]


def probe_start_inside_the_band_is_refused():
    return [] if _run_is_refused(0.0005) else ["a ball 0.0005 mm above the plate with a 0.001 mm tolerance was accepted"]


def probe_baumgarte_pulls_a_displaced_lock_back():
    """A locked contact displaced 1e-5 m into the partner at rest must be pushed back with an acceleration of
    a^2 * 1e-5 = 4e-3 m/s^2 along the gap (the documented stabilisation, a = 20 per second, written out here)."""
    doc = App.newDocument("MotionBaumgarte")
    try:
        c = C.CASES["block_rests_on_plate"](doc, "single", None)
        model = MO.Model(c.study)
        el = model.unilaterals[0]
        q0 = model.np.zeros(model.n)
        _gap, _row, data = el.probe(model, q0)
        lk = MO._Lock(el, 0.0, data)
        lk.c0 = el.frozen_row(model, q0, lk)[1]
        q = q0.copy()
        q[0] = 1e-5                                    # down the rail, into the plate
        qdd, _lam = model.locked_accel(q, model.np.zeros(model.n), [lk])
        row = el.frozen_row(model, q, lk)[0]
        got = float(row @ qdd)
        return [] if abs(got - 400.0 * 1e-5) < 1e-6 else ["gap acceleration %.6g, expected 4e-3" % got]
    finally:
        App.closeDocument(doc.Name)


def _bounce_guard(patches=None, case="ball_bounce_e09_step01"):
    """Run a coarse-step bounce case (output step 0.1 s) with ``patches`` (a dict of ``patched`` arguments, or None);
    returns (failing rows, guard record, the exception if the run raised)."""
    try:
        with patched(**(patches or {})):
            rows, _c, res = run_case(case, "single")
        return failing_rows(rows), res["contact"]["guard"], None
    except MO.MotionError as exc:
        return [], {"chunks": 0, "retries": 0}, exc


def probe_guard_plan_needs_no_retries():
    """The guard's plan includes the acceleration term: a ball falling and bouncing at a coarse step is covered by the
    planned chunks with no halving at all (an unplanned chunk that the check rejects counts as a retry)."""
    bad, guard, exc = _bounce_guard()
    problems = ["the run raised: %s" % exc] if exc else []
    if bad:
        problems.append("rows fail: %s" % [r["metric"] for r in bad][:3])
    if guard["retries"] != 0:
        problems.append("%d retries in a run whose plan should be tight" % guard["retries"])
    if guard["chunks"] < 20:
        problems.append("only %d chunks: the guard did not plan" % guard["chunks"])
    return problems


def _oversized_plan():
    """Test hook, not a model of anything real: every planned chunk is made 8 times longer than the plan allows, so the
    check after the chunk must reject it and the halving must bring it back."""
    orig = MO._Sim._plan

    def plan(self, guards, t, y, rhs):
        h, items = orig(self, guards, t, y, rhs)
        return h * 8.0, items
    return plan


def probe_guard_retries_recover():
    """With the planned chunks forced 8 times too long (``_oversized_plan``), the chunk check rejects them, the retry
    counter is above zero, and the halving still gives the closed-form bounce (the case's own rows)."""
    with patched(MO._Sim, _plan=_oversized_plan()):
        bad, guard, exc = _bounce_guard()
    problems = ["the run raised: %s" % exc] if exc else []
    if bad:
        problems.append("rows fail after the retries: %s" % [r["metric"] for r in bad][:3])
    if guard["retries"] < 20:
        problems.append("only %d retries with chunks forced 8 times too long" % guard["retries"])
    return problems


PROBES = {"guard_plan_needs_no_retries": probe_guard_plan_needs_no_retries,
          "guard_retries_recover": probe_guard_retries_recover, "part_documents": probe_part_documents_unchanged, "restores_placements": probe_run_restores_placements,
          "undo": probe_undo, "two_runs": probe_two_runs_identical,
          "overlap_refused": probe_overlapping_start_is_refused, "band_refused": probe_start_inside_the_band_is_refused,
          "baumgarte": probe_baumgarte_pulls_a_displaced_lock_back}


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

    def grid(study, end=None, step=None):
        step, g = real(study, end, step)
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


def _m_slider_coriolis():
    return dict(_coriolis=lambda w_p, a, qdj: 0.0 * a)


def _m_torque_sign():
    return dict(_torque_world=lambda Rd, R0, T, local: -(Rd @ (R0 @ T) if local else T))


def _m_inertia_unrotated():
    return dict(_inertia_to_world=lambda R0, inertia: inertia)


def _m_com_at_origin():
    return dict(Model_com=lambda self, com: 0.0 * com)


def _m_restitution_twice():
    return dict(_ratio=lambda e: e * e)


def _m_restitution_ignored():
    return dict(_ratio=lambda e: 1.0)


def _m_impulse_sign():
    return dict(_impulse_sign=lambda: 1.0)


def _m_tolerance_ignored():
    return dict(_event_value=lambda gap, tol: gap, _MAX_EVENTS=300)      # without the margin the pair chatters


def _m_capture_everything():
    return dict(CAPTURE_SPEED=1e6)


def _m_release_never():
    return dict(_release_value=lambda lam: 1.0)


def _m_release_at_once():
    return dict(_release_value=lambda lam: 0.0, _MAX_EVENTS=300)


def _m_lock_when_tensile():
    return dict(_lock_ok=lambda lam: True)


def _m_stop_wrong_side():
    return dict(_stop_side=lambda side: -1.0 if side == "lower" else 1.0)


def _m_limit_absolute():
    return dict(_q_bound=lambda limit, value0, sgn: sgn * limit)


def _m_limit_ignores_child():
    return dict(_q_bound=lambda limit, value0, sgn: limit - value0)


def _m_energy_flag_true():
    return dict(_contact_conservative=lambda e: True)


def _m_guard_removed():
    return dict(_guard_enabled=lambda: False)


def _m_no_release_on_separation():
    return dict(_separation_value=lambda gap, tol: 1.0)


def _m_no_overlap_refusal():
    return dict(_overlap_limit=lambda small: float("inf"))


def _m_in_band_stays_armed():
    return dict(_unarmed_in_band=lambda: False)


def _m_interval_rule_removed():
    return dict(_should_capture=lambda vn_after, since: vn_after < MO.CAPTURE_SPEED)


def _m_baumgarte_off():
    return dict(BAUMGARTE=0.0)


def _m_slider_sign_ignores_reference():
    return dict(_joint_sign=lambda typ, sgn: 1.0 if typ == "Slider" else sgn)


def _m_speed_bound_part1_only():
    def speed(self, model, q, v):
        np = model.np
        Rd, td, A, O = model.kin(q)
        b, cs = self.b1, self.corners[0]
        base, R = model.pose(b, Rd, td)
        base = base * 1e-3
        return max(float(np.linalg.norm(model.jac(b, R @ c + base, A, O)[0] @ v)) for c in cs) if b.chain else 0.0
    return dict(Contact_speed=speed)


def _m_allow_10x():
    return dict(_allow=lambda el, gap: max(5.0 * el.t_min, 0.8 * (gap - el.tol) * 1e-3))


def _m_chunk_check_never_fails():
    return dict(_chunk_too_long=lambda dt, v0, v1, allow: False)


def _m_accel_term_dropped():
    return dict(_plan_accel=lambda a: 0.0)


_PLAN_CHUNK = MO._plan_chunk


def _m_constant_speed_chunk_30x():
    return dict(_plan_chunk=lambda v0, a, allow: allow / (0.05 * v0) if a <= 1e-12 and v0 > 1e-12 else _PLAN_CHUNK(v0, a, allow))


def _m_no_resettle_after_release():
    orig = MO._Sim._settle
    return dict(Sim__settle=lambda self, el, t, q, qd, mode: qd if mode == "release" else orig(self, el, t, q, qd, mode))


MUTANTS = {
    "gravity_wrong_sign": (_m_gravity_sign, ["fall_vertical", "pend_30_L400"], []),
    "gravity_wrong_axis": (_m_gravity_axis, ["fall_vertical", "pend_30_L400"], []),
    "spring_wrong_sign": (_m_spring_sign, ["spring_mass_k200", "spring_mass_vertical_k500"], []),
    "spring_free_length_ignored": (_m_free_length, ["spring_mass_k200", "spring_mass_vertical_k500"], []),
    "damping_applied_as_stiffness": (_m_damper_stiffness, ["damped_under_z010", "damped_critical"], []),
    "damper_sign_flipped": (_m_damper_sign, ["damped_under_z010", "damped_over_z2"], []),
    "mass_from_volume_without_density": (_m_no_density, ["compound_rod_end", "compound_disc_rim"], []),
    "inertia_about_the_wrong_point": (_m_inertia_origin, ["compound_rod_end", "compound_rod_rotated_part"], []),
    "time_step_doubled_silently": (_m_step_doubled, ["fall_vertical", "pend_30_L400"], []),
    "energy_not_computed": (_m_no_energy, ["pend_30_L400", "spring_mass_k200"], []),
    "suppressed_components_load_still_applied": (
        _m_suppressed_load_applied, ["suppressed_spring_to_pendulum", "suppressed_damper_to_pendulum"], []),
    "results_written_into_part_documents": (_m_write_into_parts, [], ["part_documents"]),
    "placements_not_restored_after_a_run": (_m_not_restored, [], ["restores_placements"]),
    "velocity_products_dropped": (_m_bias_dropped, ["pend_120_L500", "double_pendulum_large"], []),
    "force_frame_ignored": (_m_force_frame, ["force_in_part_frame"], []),
    "rotational_inertia_ignored": (_m_no_rotational_inertia, ["compound_rod_end"], []),
    "slider_coriolis_term_dropped": (_m_slider_coriolis, ["arm_bead_free", "arm_bead_torque_world"], []),
    "torque_sign_flipped": (_m_torque_sign, ["arm_bead_torque_world", "arm_bead_torque_part"], []),
    "inertia_not_rotated_by_the_placement": (_m_inertia_unrotated, ["compound_rod_rotated_part"], []),
    "centre_of_mass_at_the_part_origin": (_m_com_at_origin, ["compound_rod_end", "compound_rod_quarter"], []),
    "restitution_applied_twice": (_m_restitution_twice, ["ball_bounce_e07", "stop_pendulum_e06"], []),
    "restitution_ignored_always_elastic": (_m_restitution_ignored, ["ball_bounce_e05", "ball_bounce_e09", "stop_slider_e05"], []),
    "impulse_on_the_wrong_sign_of_the_normal": (_m_impulse_sign, ["ball_bounce_e07", "stop_slider_e08"], []),
    "contact_tolerance_ignored_penetration_grows": (_m_tolerance_ignored, ["ball_bounce_e07", "ball_bounce_e05"], []),
    "capture_threshold_so_high_it_captures_the_first_bounce": (_m_capture_everything, ["ball_bounce_e09", "ball_bounce_e05"], []),
    "release_never_happens": (_m_release_never, ["contact_released_by_spring"], []),
    "release_happens_at_once": (_m_release_at_once, ["block_rests_on_plate"], []),
    "lock_even_when_the_multiplier_is_tensile": (_m_lock_when_tensile, ["block_lifted_by_force"], []),
    "limit_stop_on_the_wrong_side": (_m_stop_wrong_side, ["stop_pendulum_e1", "stop_slider_e08"], []),
    "limit_mapped_from_the_absolute_value": (_m_limit_absolute, ["stop_pendulum_offset_e1"], []),
    "limit_ignores_which_part_the_coordinate_moves": (_m_limit_ignores_child, ["stop_pendulum_reference2_e1"], []),
    "suppressed_components_contact_still_applied": (_m_suppressed_load_applied, ["suppressed_shelf_contact"], []),
    "energy_flag_left_true_with_restitution_below_one": (_m_energy_flag_true, ["ball_bounce_e05", "stop_pendulum_e06"], []),
    "tunnelling_guard_removed": (_m_guard_removed, ["ball_bounce_e09_step005", "ball_bounce_e09_step01"], []),
    "locked_contact_not_released_on_separation": (_m_no_release_on_separation, ["block_slides_off_the_plate_edge"], []),
    "overlap_refusal_removed": (_m_no_overlap_refusal, [], ["overlap_refused"]),
    "pair_inside_the_band_stays_armed": (_m_in_band_stays_armed, ["ball_touching_start_up_apex_below_tolerance"], []),
    "capture_interval_rule_removed": (_m_interval_rule_removed, ["ball_pressed_captured_by_the_interval_rule"], []),
    "baumgarte_stabilisation_off": (_m_baumgarte_off, [], ["baumgarte"]),
    "slider_stop_ignores_which_part_moves": (_m_slider_sign_ignores_reference, ["stop_slider_ref1_e08"], []),
    "guard_speed_bound_counts_part_1_only": (_m_speed_bound_part1_only, ["two_thin_plates_head_on_coarse_step"], []),
    "guard_allows_ten_times_the_displacement": (_m_allow_10x, ["two_thin_plates_head_on_coarse_step"], []),
    "guard_chunk_check_never_fails": (_m_chunk_check_never_fails, [], ["guard_retries_recover"]),
    "guard_plan_drops_the_acceleration_term": (_m_accel_term_dropped, [], ["guard_plan_needs_no_retries"]),
    "guard_constant_speed_chunk_30_times_too_long": (_m_constant_speed_chunk_30x, ["two_thin_plates_head_on_coarse_step"], []),
    "released_pair_not_re_armed_inside_the_band": (_m_no_resettle_after_release, ["contact_released_in_band_returns_to_rest"], []),
}


@contextlib.contextmanager
def mutated(name):
    spec = MUTANTS[name][0]()
    com = spec.pop("Model_com", None)
    on_sim = {k[len("Sim_"):]: spec.pop(k) for k in [k for k in spec if k.startswith("Sim_")]}      # methods of _Sim and _Contact
    on_contact = {k[len("Contact_"):]: spec.pop(k) for k in [k for k in spec if k.startswith("Contact_")]}
    saved = MO.Model._com
    try:
        if com is not None:
            MO.Model._com = com
        with patched(**spec), patched(MO._Sim, **on_sim), patched(MO._Contact, **on_contact):
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
        wall = {}
        for name in C.CASES:
            for mode in MODES:
                with self.subTest(case=name, mode=mode):
                    t0 = time.time()
                    try:
                        r, c, _res = run_case(name, mode)
                    except Exception:
                        import traceback
                        self.fail("%s/%s crashed: %s" % (name, mode, traceback.format_exc()))
                    wall[(name, mode)] = time.time() - t0
                    rows.extend(r)
                    table[name] = (c.klass, c.integrator, c.step, c.end)
                    bad = failing_rows(r)
                    self.assertEqual(bad, [], "%s/%s" % (name, mode))
        print("\n  contact and stop cases (wall seconds single / link, penetration against the tolerance):")
        for name in CONTACT:
            pen = [r for r in rows if r["case"] == name and r["metric"].startswith("penetration")]
            print("    %-34s %5.1f / %5.1f s   %s" % (name, wall.get((name, "single"), 0.0), wall.get((name, "link"), 0.0),
                                                      pen[0]["info"] if pen else ""))
            for mode in MODES:
                self.assertLess(wall.get((name, mode), 0.0), 60.0, "%s/%s took over a minute" % (name, mode))
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

    def test_misuse_is_refused_clearly(self):
        def fresh(name="pend_30_L400"):
            doc = App.newDocument("MotionMisuse")
            self.addCleanup(lambda d=doc: App.closeDocument(d.Name) if d.Name in App.listDocuments() else None)
            return doc, C.CASES[name](doc, "single", None)

        # a joint whose start is outside an enabled limit is refused, naming the joint (slice 6c lifted the 6a refusal
        # of limits themselves)
        doc, c = fresh()
        doc.getObject("Hinge").EnableAngleMin = True
        doc.getObject("Hinge").AngleMin = 10.0
        with self.assertRaises(MO.MotionRefused) as cm:
            MO.run(c.study)
        self.assertIn("Hinge", str(cm.exception))
        self.assertIn("limit", str(cm.exception))
        # velocities
        doc, c = fresh()
        for text in ('{"Nope": 1}', "{bad", '{"Hinge": NaN}', "[1]", '{"Hinge": "x"}'):
            c.study.Velocities = text
            with self.assertRaises(MO.MotionError, msg=text):
                MO.run(c.study)
        # masses
        doc, c = fresh()
        part = c.rig.parts["Bob"]
        for kw in (dict(density=-1.0), dict(density=0.0), dict(mass=0.0, com=(0, 0, 0)), dict(mass=-1.0),
                   dict(mass=1.0, inertia=[1, 1, 1, 0, 0]), dict(mass=1.0, inertia=[-1, 1, 1, 0, 0, 0]),
                   dict(mass=1.0, inertia=[1, 1, 1, 2, 0, 0]), dict(mass=float("nan"))):
            with self.assertRaises(MO.MotionError, msg=str(kw)):
                MO.set_mass(c.study, part, **kw)
        m = MO.masses(c.study)[0]
        m.Mode = "Manual"
        m.Mass = 0.0
        with self.assertRaises(MO.MotionError):
            MO.run(c.study)
        # the time grid
        for end, step in ((1.0, 0.3), (0.1, 0.5), (1.0, 0.0), (1.0, -1.0), (float("nan"), 0.1), (1.0, float("nan"))):
            doc, c = fresh()
            c.study.TimeEnd, c.study.TimeStep = end, step
            with self.assertRaises(MO.MotionError, msg=str((end, step))):
                MO.run(c.study)
        # a floating part with a mass is refused; a force on a grounded part is reported as not applied
        doc, c = fresh()
        free = c.rig.asm.newObject("Part::Box", "Free")
        MO.set_mass(c.study, free, density=1000.0)
        with self.assertRaises(MO.MotionRefused) as cm:
            MO.run(c.study)
        self.assertIn("Free", str(cm.exception))
        doc, c = fresh()
        f = MO.add_force(c.study, c.rig.G, (0, 0, 0), (1, 0, 0))
        res = MO.run(c.study)
        rec = [x for x in res["loads"] if x["entity"] == MO._entity(f)][0]
        self.assertFalse(rec["applied"])
        self.assertIn("does not move", rec["reason"])
        # a mass whose part was deleted is reported
        doc, c = fresh()
        ghost = c.rig.asm.newObject("Part::Box", "Ghost")
        MO.set_mass(c.study, ghost, density=1000.0)
        doc.removeObject("Ghost")
        res = MO.run(c.study)
        self.assertTrue(any("Ghost" in w for w in res["warnings"]))

    def test_energy_bar_catches_a_small_loss(self):
        doc = App.newDocument("MotionEnergy")
        try:
            c = C.CASES["pend_30_L400"](doc, "single", None)
            MO.add_damper(c.study, c.rig.G, c.rig.L(c.rig.G, (0, 0, 0)), c.rig.parts["Bob"], (0, 0, -400), 0.001)
            res = MO.run(c.study)
            rows = C.Rows()
            c.check(res, rows)
            self.assertTrue(any(r["metric"] == "energy drift" and not r["ok"] for r in rows),
                            [(r["metric"], r["error"]) for r in rows])
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

    def test_a_run_whose_only_moving_part_was_deleted_is_refused(self):
        """Slice 6a LOW, fixed in 6b: the run used to return an empty series with two warnings."""
        for mode in MODES:
            doc = App.newDocument("MotionGone")
            try:
                c = C.CASES["pend_30_L400"](doc, mode, None)
                doc.removeObject("Bob")
                with self.assertRaises(MO.MotionRefused) as cm:
                    MO.run(c.study)
                self.assertIn("Bob", str(cm.exception), mode)
                self.assertIn("no longer exists", str(cm.exception), mode)
                self.assertEqual(c.study.Result, "", "a refused run stores nothing")
                with self.assertRaises(MO.MotionRefused):
                    MO.compute(c.study)
            finally:
                App.closeDocument(doc.Name)

    def test_compute_and_series_accessors_for_the_gui(self):
        """Slice 6b: ``compute`` is a run that writes nothing, ``derived_series`` gives velocities and forces from the
        stored joint coordinates, and ``apply_study`` is one transaction."""
        doc = App.newDocument("MotionAcc")
        try:
            c = C.CASES["spring_mass_k200"](doc, "single", None)
            study = c.study
            sig0, undo0 = MO.signature(doc), doc.UndoCount
            res = MO.compute(study)
            self.assertEqual(MO.signature(doc), sig0, "compute writes nothing")
            self.assertEqual(doc.UndoCount, undo0, "compute opens no transaction")
            self.assertEqual(study.Result, "")
            ran = MO.run(study)
            self.assertEqual(MO.result_text(res), study.Result, "compute and run give the same bytes")
            self.assertEqual(res, ran)
            # overrides of the span and step equal a study that holds them
            res2 = MO.compute(study, 1.0, 0.01)
            MO._assign(study, {"TimeEnd": 1.0, "TimeStep": 0.01})
            self.assertEqual(MO.result_text(res2), MO.result_text(MO.compute(study)))
            self.assertEqual(len(res2["t"]), 101)
            with self.assertRaises(MO.MotionError):
                MO.compute(study, 1.0, 0.3)
            # closed forms: x = 200 + 30 cos(wt) mm, v = -30 w sin(wt) mm/s, spring force 6 cos(wt) N
            w = 200.0 ** 0.5
            t, x, info = MO.series_of(study, res, "position:Block:x")
            self.assertEqual(info["unit"], "mm")
            _t, v, info = MO.series_of(study, res, "velocity:Block:x")
            self.assertEqual(info["unit"], "mm/s")
            _t, f, info = MO.series_of(study, res, "force:Spring")
            self.assertEqual(info["unit"], "N")
            import math
            for series, fn, amp in ((x, lambda s: 200.0 + 30.0 * math.cos(w * s), 30.0),
                                    (v, lambda s: -30.0 * w * math.sin(w * s), 30.0 * w),
                                    (f, lambda s: 6.0 * math.cos(w * s), 6.0)):
                self.assertLess(max(abs(a - fn(s)) for a, s in zip(series, res["t"])) / amp, 1e-5)
            # a damper's force is c times the rate of lengthening: critical damping case
            ids = [q["id"] for q in MO.quantities(res)]
            self.assertIn("force:Spring", ids)
            self.assertNotIn("force:Gravity", ids)
            with self.assertRaises(MO.MotionError):
                MO.series_of(study, res, "force:Nope")
            # apply_study: one transaction for the span, the step and the result; nothing when nothing differs
            u0 = doc.UndoCount
            self.assertTrue(study.Result, "the run above left a result, computed for another span")
            rep = MO.apply_study_report(study, 1.0, 0.01, None)       # which the stored result no longer matches: cleared
            self.assertTrue(rep["changed"] and rep["cleared"])
            self.assertEqual((doc.UndoCount, study.Result), (u0 + 1, ""))
            u0 = doc.UndoCount
            self.assertFalse(MO.apply_study(study, 1.0, 0.01, None))
            self.assertEqual(doc.UndoCount, u0)
            self.assertTrue(MO.apply_study(study, 0.5, 0.01, MO.compute(study, 0.5, 0.01)))
            self.assertEqual(doc.UndoCount, u0 + 1)
            self.assertEqual((study.TimeEnd, study.TimeStep), (0.5, 0.01))
            with self.assertRaises(MO.MotionError):
                MO.apply_study(study, 0.5, 0.3)
            self.assertEqual(doc.UndoCount, u0 + 1)
        finally:
            App.closeDocument(doc.Name)

    def test_damper_force_series(self):
        doc = App.newDocument("MotionDamp")
        try:
            c = C.CASES["damped_critical"](doc, "single", None)
            res = MO.run(c.study)
            _t, f, _i = MO.series_of(c.study, res, "force:Damper")
            _t, v, _i = MO.series_of(c.study, res, "velocity:Block:x")
            cd = 2.0 * (400.0 * 1.0) ** 0.5
            # the damper lengthens at the block's speed (the other end is fixed): force = c v, with v in m/s
            self.assertLess(max(abs(a - cd * b * 1e-3) for a, b in zip(f, v)) / max(abs(x) for x in f), 1e-9)
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

    # -- slice 6c
    def test_first_collision_on_a_contact_case_is_at_or_before_the_first_impact(self):
        doc = App.newDocument("MotionContactHit")
        try:
            c = C.CASES["ball_bounce_e07"](doc, "single", None)
            res = MO.run(c.study)
            first = [e for e in res["contact"]["events"] if e["what"] == "impact"][0]["t"]
            # the stored frames never overlap the plate, so ask for pairs closer than the distance the ball covers in a step
            hit = MO.first_collision(c.study, c.body, c.plate, classes=("interfering", "touching", "close"),
                                     clearance=12.0)
            self.assertIsNotNone(hit)
            print("\n  contact case: first impact at t=%.5f s, first_collision (12 mm clearance) at step %d, t=%.5f s (%s)" % (
                first, hit["step"], hit["time"], hit["class"]))
            self.assertLessEqual(hit["time"], first + 1e-12)
            self.assertGreaterEqual(hit["time"], first - 2 * c.study.TimeStep)
            self.assertTrue(c.body.Placement.isSame(c.body.Placement, 0.0))
        finally:
            App.closeDocument(doc.Name)

    def test_contact_is_an_editable_removable_load_in_one_transaction_each(self):
        doc = App.newDocument("MotionContactEdit")
        try:
            c = C.CASES["ball_bounce_e07"](doc, "single", None)
            res = MO.run(c.study)
            ld = [x for x in MO.loads(c.study) if x.Kind == "Contact"][0]
            self.assertEqual(MO.stale_reason(c.study, res), "")
            n0 = doc.UndoCount
            s0 = MO.signature(doc)
            MO.edit_load(ld, Restitution=0.4)
            self.assertEqual(doc.UndoCount, n0 + 1)
            self.assertNotEqual(MO.stale_reason(c.study, res), "")          # the restitution is part of the inputs
            doc.undo()
            self.assertEqual(MO.signature(doc), s0)
            doc.redo()
            n1 = doc.UndoCount
            MO.remove_load(ld)
            self.assertEqual(doc.UndoCount, n1 + 1)
            doc.undo()
            self.assertEqual(sorted(MO.signature(doc)), sorted(s0))
            n2 = doc.UndoCount
            added = MO.add_contact(c.study, c.body, c.plate, 0.25, tolerance=0.01, name="Second")
            self.assertEqual(doc.UndoCount, n2 + 1)
            self.assertEqual((added.Restitution, added.ContactTolerance), (0.25, 0.01))
            for bad in (dict(restitution=1.5), dict(restitution=-0.1), dict(restitution=float("nan")),
                        dict(restitution=0.5, tolerance=0.0), dict(restitution=0.5, tolerance=-1.0)):
                with self.assertRaises(MO.MotionError, msg=str(bad)):
                    MO.add_contact(c.study, c.body, c.plate, **bad)
            with self.assertRaises(MO.MotionError):
                MO.add_contact(c.study, c.body, c.body, 0.5)
            self.assertEqual(doc.UndoCount, n2 + 1)                          # a refusal leaves no transaction
            e_now, tol_now = ld.Restitution, ld.ContactTolerance
            for bad in (dict(Restitution=1.5), dict(Restitution=-0.1), dict(Restitution=float("nan")),
                        dict(ContactTolerance=0.0), dict(ContactTolerance=-1.0), dict(Restitution=0.5, ContactTolerance=-1.0)):
                with self.assertRaises(MO.MotionError, msg="edit_load %s" % bad):
                    MO.edit_load(ld, **bad)
            self.assertEqual((ld.Restitution, ld.ContactTolerance, doc.UndoCount), (e_now, tol_now, n2 + 1))
            MO.edit_load(ld, Restitution=1.0, ContactTolerance=0.01)             # the limits themselves are accepted
            self.assertEqual((ld.Restitution, ld.ContactTolerance), (1.0, 0.01))
            # an out-of-range value edited in afterwards is refused when the study runs
            ld2 = [x for x in MO.loads(c.study) if x.Name == added.Name][0]
            ld2.Restitution = 2.0
            with self.assertRaises(MO.MotionError):
                MO.run(c.study)
        finally:
            App.closeDocument(doc.Name)

    def test_contact_study_survives_save_and_reopen(self):
        self.assertEqual(save_reopen_problems("ball_bounce_e07"), [])
        self.assertEqual(save_reopen_problems("stop_pendulum_offset_e1", "link"), [])

    def test_studies_without_contact_or_stops_carry_no_contact_record(self):
        for name in ("pend_30_L400", "falling_ball_on_plate"):
            doc = App.newDocument("MotionNoContact")
            try:
                c = C.CASES[name](doc, "single", None)
                res = MO.run(c.study)
                self.assertNotIn("contact", res)
            finally:
                App.closeDocument(doc.Name)

    def test_stops_need_the_start_inside_the_range(self):
        doc = App.newDocument("MotionStopStart")
        try:
            c = C.CASES["stop_slider_e08"](doc, "single", None)
            rail = doc.getObject("Rail")
            rail.EnableLengthMin = True
            rail.LengthMin = 5.0                      # the block starts at distance 0 along the slider
            with self.assertRaises(MO.MotionRefused) as cm:
                MO.run(c.study)
            self.assertIn("Rail", str(cm.exception))
            rail.LengthMin = 0.0                      # exactly at a limit is inside
            MO.run(c.study)
        finally:
            App.closeDocument(doc.Name)

    def test_the_capture_rule_leaves_the_first_three_bounces_alone(self):
        doc = App.newDocument("MotionCapture")
        try:
            c = C.CASES["ball_bounce_e05"](doc, "single", None)
            res = MO.run(c.study)
            ev = res["contact"]["events"]
            caps = [e for e in ev if e["what"] == "capture"]
            imps = [e for e in ev if e["what"] == "impact"]
            self.assertEqual(len(caps), 1)
            before = [e for e in imps if e["t"] < caps[0]["t"]]
            self.assertGreaterEqual(len(before), 4)
            # the post-impact speeds of impacts 1 to 3 are well above the capture speed
            for e in imps[:3]:
                self.assertGreater(e["vn_after"], 3 * MO.CAPTURE_SPEED)
            print("\n  capture: speeds after impacts 1..3 %s m/s against the capture speed %g m/s; locked at impact %d, t=%.4f s" % (
                [round(e["vn_after"], 4) for e in imps[:3]], MO.CAPTURE_SPEED, len(before), caps[0]["t"]))
        finally:
            App.closeDocument(doc.Name)

    # -- run 050 repair
    def test_contact_starts_and_misuse_are_refused_clearly(self):
        for pname in ("overlap_refused", "band_refused", "baumgarte"):
            self.assertEqual(PROBES[pname](), [], pname)
        doc = App.newDocument("MotionStartMsg")
        try:
            _rig, study, _ball = ball_over_plate(doc, -10.0)
            with self.assertRaises(MO.MotionRefused) as cm:
                MO.run(study)
            msg = str(cm.exception)
            for word in ("Contact", "Ball", "G", "overlap"):
                self.assertIn(word, msg)
            rig2_doc = App.newDocument("MotionBandMsg")
            try:
                _r, st2, _b = ball_over_plate(rig2_doc, 0.0005)
                with self.assertRaises(MO.MotionRefused) as cm2:
                    MO.run(st2)
                self.assertIn("inside the contact tolerance", str(cm2.exception))
                self.assertIn("Ball", str(cm2.exception))
            finally:
                App.closeDocument(rig2_doc.Name)
        finally:
            App.closeDocument(doc.Name)

    def test_a_contact_between_parts_that_never_move_is_refused(self):
        doc = App.newDocument("MotionStatic")
        try:
            rig, study, ball = ball_over_plate(doc, 100.0)
            shelf = rig.box("Shelf", 50, 50, 10, C.P(C.V(300, 300, 50), C.R()))
            C.cc.ground(rig.jg, shelf)
            rig.finish()
            n0 = doc.UndoCount
            with self.assertRaises(MO.MotionError) as cm:
                MO.add_contact(study, shelf, rig.G, 0.5)
            self.assertIn("Shelf", str(cm.exception))
            self.assertEqual(doc.UndoCount, n0)
            # a pair that is static without being grounded itself (fixed to ground) is refused when the study runs
            wall = rig.box("Wall", 20, 20, 20, C.P(C.V(-300, -300, 0), C.R()))
            rig.fixed("Weld", rig.G, wall, (-300, -300, 0))
            rig.finish()
            MO.add_contact(study, wall, rig.G, 0.5, name="Static")
            with self.assertRaises(MO.MotionRefused) as cm:
                MO.run(study)
            self.assertIn("Wall", str(cm.exception))
            self.assertIn("never act", str(cm.exception))
        finally:
            App.closeDocument(doc.Name)

    def test_limit_restitution_is_checked_even_without_a_limit_and_the_range_is_quoted_as_entered(self):
        doc = App.newDocument("MotionLimitE")
        try:
            _rig, study, _ball = ball_over_plate(doc, 100.0)
            study.LimitRestitution = 2.0
            with self.assertRaises(MO.MotionError) as cm:
                MO.run(study)
            self.assertIn("LimitRestitution", str(cm.exception))
        finally:
            App.closeDocument(doc.Name)
        doc = App.newDocument("MotionLimitRange")
        try:
            c = C.CASES["stop_slider_e08"](doc, "single", None)
            rail = doc.getObject("Rail")
            rail.EnableLengthMin, rail.LengthMin, rail.LengthMax = True, 60.0, 10.0           # reversed: FreeCAD swaps
            with self.assertRaises(MO.MotionRefused) as cm:
                MO.run(c.study)
            self.assertIn("60 to 10", str(cm.exception))
            self.assertIn("swapped", str(cm.exception))
        finally:
            App.closeDocument(doc.Name)

    def test_the_tunnelling_guard_reports_chunks_and_never_lets_the_ball_through_at_coarse_steps(self):
        for step in (0.05, 0.1, 0.3):
            doc = App.newDocument("MotionCoarse")
            try:
                _rig, study, ball = ball_over_plate(doc, 200.0, e=0.9, end=1.2, step=step)
                res = MO.run(study)
                z = [C.pl_of(d).Base.z for d in C.series(res, "Ball")]
                self.assertGreaterEqual(min(z), 25.0 + MO.CONTACT_TOLERANCE - 1e-9, "step %g" % step)
                guard = res["contact"]["guard"]
                self.assertGreater(guard["chunks"], 10)
                imp = [e for e in res["contact"]["events"] if e["what"] == "impact"]
                self.assertGreaterEqual(len(imp), 3, "step %g" % step)
                print("\n  coarse step %g s: %d impacts, %d chunks, %d retries, lowest ball z %.6f mm" % (
                    step, len(imp), guard["chunks"], guard["retries"], min(z)))
            finally:
                App.closeDocument(doc.Name)

    def test_guard_halving_below_the_floor_raises_a_clear_error_and_leaves_nothing_patched(self):
        """The retry loop halves a rejected chunk until it would be shorter than ``_MIN_CHUNK``, then raises. A real run
        reaches the floor only for a body far faster than the plate is thick, so this test makes the check fail
        for every chunk (a patched ``_Sim._chunk_ok``): the honest content is the floor path, not the physics."""
        n_calls = []

        def never_ok(self, plan, t0, t1, y1):
            n_calls.append(t1 - t0)
            return False
        with patched(MO._Sim, _chunk_ok=never_ok):
            with self.assertRaises(MO.MotionError) as cm:
                run_case("ball_bounce_e09_step01", "single")
        self.assertIn("cannot keep a body from crossing", str(cm.exception))
        self.assertNotIsInstance(cm.exception, MO.MotionRefused)
        self.assertGreater(len(n_calls), 10)                         # it halved repeatedly, not once
        self.assertTrue(all(b < a for a, b in zip(n_calls, n_calls[1:])))       # each retry is a shorter chunk
        rows, _c, res = run_case("ball_bounce_e09_step01", "single")             # unpatched again
        self.assertEqual(failing_rows(rows), [])
        self.assertEqual(res["contact"]["guard"]["retries"], 0)

    def test_a_body_that_goes_into_or_through_its_partner_fails_the_run(self):
        """The check after the run: an in-band release shorter than the solver's step (the coarse-step variant of
        ``contact_released_in_band_returns_to_rest``) lets A sink into the plate, and with the guard switched off the
        coarse-step ball passes through the plate between two output steps. Both runs fail with ``MotionError``, never
        a stored result; the fine-step runs are untouched."""
        name = "contact_released_in_band_coarse_step"
        C.make_release_in_band(name, step=0.002)
        try:
            with self.assertRaises(MO.MotionError) as cm:
                run_case(name, "single")
            self.assertIn("overlap at t =", str(cm.exception))
        finally:
            C.CASES.pop(name, None)
        with patched(_guard_enabled=lambda: False):
            with self.assertRaises(MO.MotionError) as cm:
                run_case("ball_bounce_e09_step01", "single")
        self.assertRegex(str(cm.exception), "changed sides|overlap at t =")
        rows, _c, _res = run_case("ball_bounce_e09_step01", "single")
        self.assertEqual(failing_rows(rows), [])

    def test_guard_retries_are_counted_and_recover_the_closed_form(self):
        self.assertEqual(probe_guard_retries_recover(), [])
        self.assertEqual(probe_guard_plan_needs_no_retries(), [])

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
                        except MO.MotionError:
                            failing.append(name + "(refused)")      # a clear refusal is a justified failure
                    for pname in probes:
                        if PROBES[pname]():
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
