# SPDX-License-Identifier: LGPL-2.1-or-later
"""G06 slice 6b: the dynamic-analysis commands and panel.

Run with the GUI binary: ``FreeCAD -t TestAssemblyMotionGui``. The documents come from ``motion_gui_fixture`` (cases of the 6a
corpus, each with a closed-form truth). The panel's run is compared with the API's run on a twin document (bit-identical JSON),
playback and Cancel with the object-and-property signature, the undo and redo counts, the placements and the modified flag, and
the plot with closed forms and with the API's own series. No test opens a modal dialog: the questions are replaced by scripted
answers and an unexpected question fails the test.

The panel checks are module-level functions returning a list of problems (empty when all is well). The tests assert them
empty, and ``test_mutants_are_caught`` plants defects in the panel and the API accessors and requires at least one check to
report each of them.
"""

import contextlib
import math
import os
import shutil
import tempfile
import unittest

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtGui, QtWidgets

import AssemblyMotion as MO
import CommandMotion as CM
import MotionStudyPanel as MP
from AssemblyTests import motion_gui_fixture as F

V = App.Vector


# ------------------------------------------------------------------ scaffolding

class Prompts:
    """Scripted answers for the questions the GUI asks; an unexpected question fails the test."""

    def __init__(self):
        self.asked = []
        self.values = []            # answers to CommandMotion.ask_values, in order (a dict, or None to cancel)
        self.reported = []

    def ask_values(self, parent, title, fields):
        self.asked.append((title, [f[0] for f in fields], [f[2] for f in fields]))
        if not self.values:
            raise AssertionError("unexpected question: %s" % title)
        return self.values.pop(0)

    def report(self, parent, text):
        self.reported.append(text)

    def report_problems(self, parent, text):
        self.reported.append(text)


def activate(doc):
    App.setActiveDocument(doc.Name)
    Gui.ActiveDocument = Gui.getDocument(doc.Name)
    Gui.updateGui()


class Env:
    """A fixture document made current, with the questions scripted; ``close`` leaves nothing open."""

    def __init__(self, kind="damped", mode="single", host=None, directory=None):
        self.prompts = Prompts()
        self._saved = (CM.ask_values, CM.report, MP.report_problems)
        CM.ask_values, CM.report, MP.report_problems = self.prompts.ask_values, self.prompts.report, self.prompts.report_problems
        self.rig = F.build(kind, mode, host=host, directory=directory)
        self.doc, self.asm, self.study = self.rig.doc, self.rig.asm, self.rig.study
        activate(self.doc)
        self.gdoc = Gui.getDocument(self.doc.Name)
        self.p = None

    def panel(self):
        self.p = MP.TaskMotionStudy(self.study)
        Gui.Control.showDialog(self.p)
        Gui.updateGui()
        return self.p

    def finish(self, accept=False):
        """Close the panel the way its buttons do, then the dialog."""
        p = self.p
        if p is not None and not p.done:
            (p.accept if accept else p.reject)()
        if Gui.Control.activeDialog():
            Gui.Control.closeDialog()
        Gui.updateGui()

    def snap(self):
        return F.world_snapshot(list(App.listDocuments().values()))

    def close(self):
        CM.ask_values, CM.report, MP.report_problems = self._saved
        MP._current = None
        if Gui.Control.activeDialog():
            Gui.Control.closeDialog()
        Gui.Selection.clearSelection()
        for n in list(App.listDocuments()):
            App.closeDocument(n)


@contextlib.contextmanager
def env(kind="damped", mode="single", host=None, directory=None):
    e = Env(kind, mode, host, directory)
    try:
        yield e
    finally:
        e.close()


def corner(l, w, h):
    """The element name of the (l, w, h) corner of a Part::Box of that size (from the topology of a fresh box, not assumed)."""
    import Part
    for i, v in enumerate(Part.makeBox(l, w, h).Vertexes):
        if (abs(v.X - l) + abs(v.Y - w) + abs(v.Z - h)) < 1e-9:
            return "Vertex%d" % (i + 1)
    raise AssertionError("no such corner")


BLOCK, GROUND, BOB = corner(50, 50, 50), corner(400, 400, 10), corner(20, 20, 20)


def pump():
    for _ in range(3):
        QtWidgets.QApplication.processEvents()
    Gui.updateGui()


def twin_result(kind, mode="single", end=None, step=None):
    """The API's own run of a twin document: (result text, result dict)."""
    rig = F.build(kind, mode, "MotionTwin")
    try:
        if end is not None:
            MO._assign(rig.study, {"TimeEnd": end, "TimeStep": step})
        res = MO.run(rig.study)
        return rig.study.Result, res
    finally:
        rig.close()
        for n in list(App.listDocuments()):
            activate(App.getDocument(n))


def same(a, b, tol=0.0):
    if len(a) != len(b):
        return False
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def placement_tuple(d):
    pl = App.Placement(App.Vector(*d["base"]), App.Rotation(*d["rotation"]))
    return (tuple(pl.Base), tuple(pl.Rotation.Q))


def close_enough(a, b, tol=1e-9):
    return all(abs(x - y) <= tol for x, y in zip(sum((list(t) for t in a), []), sum((list(t) for t in b), [])))


# ------------------------------------------------------------------ panel checks (each returns a list of problems)

def check_run_equals_api():
    bad = []
    for kind in ("pendulum", "damped", "force"):
        for mode in ("single", "link"):
            want_text, _want = twin_result(kind, mode)
            with env(kind, mode) as e:
                snap0 = e.snap()
                p = e.panel()
                res = p.run()
                if res is None:
                    bad.append("%s/%s: run refused: %s" % (kind, mode, p.message.text()))
                elif p.result_json() != want_text:
                    bad.append("%s/%s: the panel's result is not the API's bytes" % (kind, mode))
                if e.snap() != snap0:
                    bad.append("%s/%s: Run changed the document (signature, undo count or placements)" % (kind, mode))
                e.finish()
    return bad


def check_playback():
    bad = []
    tmp = tempfile.mkdtemp(prefix="motiongui")
    try:
        for mode in ("single", "link"):
            host = None
            if mode == "link":                   # the linked parts live in another document, which must not change
                host = App.newDocument("MotionParts")
                host.saveAs(os.path.join(tmp, "parts.FCStd"))
            with env("damped", mode, host, tmp if host else None) as e:
                e.gdoc.Modified = False
                before = F.placements(e.asm)
                host_sig = MO.signature(host) if host else None
                sig0, undo0, redo0 = MO.signature(e.doc), e.doc.UndoCount, e.doc.RedoCount
                p = e.panel()
                res = p.run()
                n = len(res["t"]) - 1
                for k in (0, n // 3, n // 2, n):
                    p.show_frame(k)
                    for part in res["parts"]:
                        got = F.placements(e.asm)[part["name"]]
                        if not close_enough(got, placement_tuple(part["placements"][k])):
                            bad.append("%s: frame %d shows %s at %s" % (mode, k, part["name"], got))
                if F.placements(e.asm) == before:
                    bad.append("%s: playback did not move the parts" % mode)
                p.show_frame(n // 4)
                stride = max(1, round(MP.PLAY_INTERVAL_MS * 1e-3 / res["step"]))
                for _ in range(4):
                    p._tick()
                if p.frame != n // 4 + 4 * stride:
                    bad.append("%s: four timer ticks reach frame %d, not %d" % (mode, p.frame, n // 4 + 4 * stride))
                p.play()
                if not p.playing:
                    bad.append("%s: play does not start the timer" % mode)
                p.pause()
                if p.playing:
                    bad.append("%s: pause does not stop the timer" % mode)
                at = p.frame
                p.step_forward()
                if p.frame != at + 1:
                    bad.append("%s: step forward goes from %d to %d" % (mode, at, p.frame))
                p.step_back()
                if p.frame != at:
                    bad.append("%s: step back goes to %d, not %d" % (mode, p.frame, at))
                if e.doc.UndoCount != undo0 or e.doc.RedoCount != redo0:
                    bad.append("%s: playback made an undo step" % mode)
                if e.gdoc.Modified:
                    bad.append("%s: playback marked the document modified" % mode)
                p.stop()
                if F.placements(e.asm) != before:
                    bad.append("%s: Stop did not put the pre-run placements back" % mode)
                if e.gdoc.Modified:
                    bad.append("%s: the document is modified after Stop" % mode)
                p.show_frame(n // 2)
                e.finish()
                if F.placements(e.asm) != before:
                    bad.append("%s: Cancel did not put the pre-run placements back" % mode)
                if MO.signature(e.doc) != sig0 or (e.doc.UndoCount, e.doc.RedoCount) != (undo0, redo0):
                    bad.append("%s: playback and Cancel changed the document" % mode)
                if host is not None and MO.signature(host) != host_sig:
                    bad.append("%s: the part document changed" % mode)
                if e.gdoc.Modified:
                    bad.append("%s: Cancel left the document modified" % mode)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return bad


def check_cancel_matrix():
    bad = []
    for variant in ("plain", "run", "run_and_play", "edited_fields", "unrelated_edit", "after_undo", "after_redo"):
        with env("damped") as e:
            e.gdoc.Modified = False
            span0 = (e.study.TimeEnd, e.study.TimeStep)
            if variant == "after_undo":
                MO.add_gravity(e.study)
            if variant == "after_redo":
                MO.add_gravity(e.study)
                e.doc.undo()
            want0 = e.snap()
            p = e.panel()
            if variant in ("run", "run_and_play"):
                p.run()
            if variant == "run_and_play":
                p.show_frame(5)
                p.play()
                p._tick()
            if variant == "edited_fields":
                p.span.setText("0.5")
                p.step.setText("0.01")
                p.run()
            if variant == "unrelated_edit":
                e.rig.parts["Block"].Label = "Renamed while the panel is open"
            if variant == "after_undo":
                e.doc.undo()
            if variant == "after_redo":
                e.doc.redo()
            # Cancel leaves the document as the user's own edits made it: the state before the panel for the first four
            # variants, the state after the edit, undo or redo for the others
            want = e.snap() if variant in ("unrelated_edit", "after_undo", "after_redo") else want0
            flag = False if variant in ("plain", "run", "run_and_play", "edited_fields") else e.gdoc.Modified
            e.finish()
            if e.snap() != want:
                bad.append("%s: Cancel changed the signature, the undo or redo counts or the placements" % variant)
            if e.gdoc.Modified != flag:
                bad.append("%s: Cancel changed the modified flag" % variant)
            if variant in ("plain", "run", "run_and_play", "edited_fields", "unrelated_edit") and e.study.Result:
                bad.append("%s: the study holds a result after Cancel" % variant)
            if variant in ("plain", "run", "run_and_play", "edited_fields") and (e.study.TimeEnd, e.study.TimeStep) != span0:
                bad.append("%s: Cancel changed the span or the step" % variant)
    return bad


def check_ok_transaction():
    bad = []
    with env("damped") as e:
        sig0 = MO.signature(e.doc)
        u0 = e.doc.UndoCount
        e.panel()
        e.finish(accept=True)
        if e.doc.UndoCount != u0 or MO.signature(e.doc) != sig0:
            bad.append("OK without an edit changed the document")
    want_text, _ = twin_result("damped", "single", 0.5, 0.01)
    with env("damped") as e:
        sig0, u0 = MO.signature(e.doc), e.doc.UndoCount
        p = e.panel()
        p.span.setText("0.5")
        p.step.setText("0.01")
        p.run()
        e.finish(accept=True)
        if e.doc.UndoCount != u0 + 1:
            bad.append("OK made %d undo steps, not 1" % (e.doc.UndoCount - u0))
        if (e.study.TimeEnd, e.study.TimeStep) != (0.5, 0.01):
            bad.append("OK did not set the span and step")
        if e.study.Result != want_text:
            bad.append("OK did not store the API's bytes")
        e.doc.undo()
        if MO.signature(e.doc) != sig0:
            bad.append("undoing OK did not restore the signature")
    with env("damped") as e:
        u0 = e.doc.UndoCount
        p = e.panel()
        p.span.setText("0.5")
        e.finish(accept=True)
        if e.doc.UndoCount != u0 + 1 or e.study.TimeEnd != 0.5 or e.study.Result:
            bad.append("OK with an edited span and no run: %d steps, span %g" % (e.doc.UndoCount - u0, e.study.TimeEnd))
    with env("damped") as e:              # a result computed for other fields is not stored
        p = e.panel()
        p.run()
        p.span.setText("0.5")
        e.finish(accept=True)
        if e.study.Result:
            bad.append("OK stored a result that was computed for another span")
    return bad


def check_refusals():
    bad = []
    with env("damped") as e:
        sig0 = e.snap()
        p = e.panel()
        p.span.setText("1.0")
        p.step.setText("0.3")
        if p.message.isHidden() or "whole number" not in p.message.text():
            bad.append("no visible refusal for a span that is not a whole number of steps: %r" % p.message.text())
        if p.run() is not None or p.result is not None:
            bad.append("a run was made on a span that is not a whole number of steps")
        if p.accept() is not False or p.done:
            bad.append("OK closed the panel on a refused span")
        p.span.setText("one")
        if p.message.isHidden():
            bad.append("no message for a span that is not a number")
        p.span.setText("0.6")
        p.step.setText("0.01")
        if not p.message.isHidden():
            bad.append("the message stays after the fields are fixed: %r" % p.message.text())
        e.finish()
        if e.snap() != sig0:
            bad.append("refusals changed the document")
    return bad


def check_plot_truth():
    bad = []
    with env("spring") as e:
        p = e.panel()
        res = p.run()
        w = math.sqrt(200.0)
        t = res["t"]
        truth = {
            "position:Block:x": [200.0 + 30.0 * math.cos(w * s) for s in t],
            "velocity:Block:x": [-30.0 * w * math.sin(w * s) for s in t],
            "force:Spring": [6.0 * math.cos(w * s) for s in t],
            "position:Block:y": [0.0] * len(t),
        }
        scale = {"position:Block:x": 30.0, "velocity:Block:x": 30.0 * w, "force:Spring": 6.0, "position:Block:y": 1.0}
        for q, want in truth.items():
            if q not in p.quantities():
                bad.append("the plot does not offer %s" % q)
                continue
            p.choose(q)
            if not same(p.plot.t, t):
                bad.append("%s: the time axis is not the result's" % q)
            err = max(abs(a - b) for a, b in zip(p.plot.y, want)) / scale[q]
            if err > 1e-5:
                bad.append("%s: the plot differs from the closed form by %.3g of its amplitude" % (q, err))
        e.finish()
    # the position series of a pendulum are the API's own placements
    _text, want = twin_result("pendulum")
    with env("pendulum") as e:
        p = e.panel()
        p.run()
        for q, i in (("position:Bob:x", 0), ("position:Bob:z", 2)):
            p.choose(q)
            api = [d["base"][i] for d in want["parts"][0]["placements"]]
            if p.plot.y != api:
                bad.append("%s: the plot is not the API's series" % q)
        e.finish()
    return bad


def check_deleted_part():
    bad = []
    with env("pendulum") as e:
        e.doc.removeObject("Bob")
        sig0 = e.snap()
        p = e.panel()
        if p.run() is not None or p.result is not None:
            bad.append("a run was made after the only moving part was deleted")
        if p.message.isHidden() or "no longer exists" not in p.message.text():
            bad.append("no visible refusal: %r" % p.message.text())
        e.finish()
        if e.snap() != sig0:
            bad.append("the refused run changed the document")
    return bad


def check_close_restores():
    bad = []
    with env("damped") as e:
        before = F.placements(e.asm)
        p = e.panel()
        p.run()
        p.show_frame(40)
        if F.placements(e.asm) == before:
            bad.append("the frame was not shown")
        Gui.Control.closeDialog()               # neither accept nor reject is called
        pump()
        if F.placements(e.asm) != before:
            bad.append("closing the dialog left the frame in the 3D view")
        if MP.panel_open():
            bad.append("the panel is still registered after the dialog closed")
    return bad


CHECKS = {
    "run_equals_api": check_run_equals_api,
    "playback": check_playback,
    "cancel_matrix": check_cancel_matrix,
    "ok_transaction": check_ok_transaction,
    "refusals": check_refusals,
    "plot_truth": check_plot_truth,
    "deleted_part": check_deleted_part,
    "close_restores": check_close_restores,
}


# ------------------------------------------------------------------ mutants

@contextlib.contextmanager
def patched(obj, name, value):
    old = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, old)


def _mut_cancel_applies():
    def reject(self):
        self.accept()
        self.stop()
        self._release()
        return True
    return patched(MP.TaskMotionStudy, "reject", reject)


def _mut_no_restore():
    def stop(self):
        self.timer.stop()
        self._saved = None
        self._was_modified = None
    return patched(MP.TaskMotionStudy, "stop", stop)


def _mut_wrong_series():
    orig = MO.series_of

    def wrong(study, res, quantity, derived=None):
        return orig(study, res, quantity.replace(":x", ":y") if quantity.startswith("position") else quantity, derived)
    return patched(MO, "series_of", wrong)


def _mut_force_sign():
    orig = MO.derived_series

    def wrong(study, res):
        d = orig(study, res)
        for k in [k for k in d if k.startswith("force:")]:
            d[k] = [-v for v in d[k]]
        return d
    return patched(MO, "derived_series", wrong)


def _mut_flag_left_modified():
    return patched(MP.TaskMotionStudy, "_restore_modified_flag", lambda self: None)


def _mut_run_writes_document():
    def run(self):
        vals = self.span_and_step()
        if vals is None:
            return None
        res = MO.run(self.study)
        self.result, self.fresh, self.run_key = res, True, (MO._q(vals[0]), MO._q(vals[1]))
        self._load_result_views()
        return res
    return patched(MP.TaskMotionStudy, "run", run)


def _mut_ok_two_transactions():
    orig = MO.apply_study_report

    def two(study, time_end=None, step=None, result=None, title="x"):
        a = orig(study, time_end, step, None)
        b = orig(study, None, None, result)
        return {"changed": a["changed"] or b["changed"], "cleared": a["cleared"]}
    return patched(MO, "apply_study_report", two)


def _mut_wrong_frame():
    orig = MP.TaskMotionStudy.show_frame

    def show(self, k):
        orig(self, min(k + 1, len(self.result["t"]) - 1) if self.result else k)
    return patched(MP.TaskMotionStudy, "show_frame", show)


def _mut_no_whole_step_check():
    return patched(MO, "check_grid", lambda end, step: (step, 1))


def _mut_playback_adds_undo():
    orig = MP.TaskMotionStudy.show_frame

    def show(self, k):
        self.doc.openTransaction("Show frame")
        orig(self, k)
        self.doc.commitTransaction()
    return patched(MP.TaskMotionStudy, "show_frame", show)


MUTANTS = {
    "Cancel applies the edits": _mut_cancel_applies,
    "Stop does not restore the pre-run placements": _mut_no_restore,
    "plot reads the wrong series (y for x)": _mut_wrong_series,
    "spring force plotted with the wrong sign": _mut_force_sign,
    "playback leaves the document modified": _mut_flag_left_modified,
    "Run stores the result in the document": _mut_run_writes_document,
    "OK is two transactions": _mut_ok_two_transactions,
    "playback shows the next frame": _mut_wrong_frame,
    "a span that is not a whole number of steps is accepted": _mut_no_whole_step_check,
    "playback makes an undo step": _mut_playback_adds_undo,
}


# ------------------------------------------------------------------ tests

class TestMotionPanel(unittest.TestCase):
    """The panel, one check at a time."""

    def run_check(self, name):
        self.assertEqual(CHECKS[name](), [])

    def test_panel_run_equals_api_run_bit_identical(self):
        self.run_check("run_equals_api")

    def test_playback_invariants(self):
        self.run_check("playback")

    def test_cancel_matrix(self):
        self.run_check("cancel_matrix")

    def test_ok_is_one_transaction(self):
        self.run_check("ok_transaction")

    def test_span_and_step_refusals_are_visible(self):
        self.run_check("refusals")

    def test_plot_equals_the_series_it_claims(self):
        self.run_check("plot_truth")

    def test_run_after_the_only_moving_part_was_deleted_is_refused(self):
        self.run_check("deleted_part")

    def test_closing_the_dialog_restores_the_view(self):
        self.run_check("close_restores")

    def test_plot_widget_draws_the_curve(self):
        with env("spring") as e:
            p = e.panel()
            p.run()
            p.plot.resize(400, 200)
            img = p.plot.grab().toImage()
            colours = {img.pixel(x, y) for x in range(0, img.width(), 3) for y in range(0, img.height(), 3)}
            self.assertGreater(len(colours), 3, "a blank plot")
            p.plot.clear()
            self.assertNotEqual(img, p.plot.grab().toImage())
            e.finish()

    def test_nice_ticks(self):
        self.assertEqual(MP.nice_ticks(0.0, 10.0), [0.0, 2.0, 4.0, 6.0, 8.0, 10.0])
        t = MP.nice_ticks(-0.031, 0.031)
        self.assertLessEqual(len(t), 8)
        self.assertEqual(t, sorted(t))
        t = MP.nice_ticks(5.0, 5.0)
        self.assertTrue(t[0] < 5.0 < t[-1])

    def test_mutants_are_caught(self):
        self.assertGreaterEqual(len(MUTANTS), 3)
        table = []
        for name, factory in MUTANTS.items():
            with self.subTest(mutant=name):
                failing = []
                with factory():
                    for cname, check in CHECKS.items():
                        try:
                            if check():
                                failing.append(cname)
                        except Exception as exc:        # noqa: BLE001  a crash is a justified failure
                            failing.append("%s(%s)" % (cname, type(exc).__name__))
                            for n in list(App.listDocuments()):
                                App.closeDocument(n)
                            MP._current = None
                            if Gui.Control.activeDialog():
                                Gui.Control.closeDialog()
                table.append((name, failing))
                self.assertTrue(failing, "mutant survived: %s" % name)
        for cname, check in CHECKS.items():
            self.assertEqual(check(), [], "nothing stays patched: %s" % cname)
        print("\n  panel mutants: %d planted, %d caught" % (len(table), sum(1 for _n, f in table if f)))
        for name, failing in table:
            print("    %-58s %s" % (name, ", ".join(failing)))


class TestMotionGui(unittest.TestCase):
    """The scene the harness photographs."""

    def test_scene_panel_is_deterministic(self):
        shots = []
        for _ in range(2):
            rig = F.scene_doc("motion_panel")
            try:
                activate(rig.doc)
                panel = F.scene_panel(rig)
                path = os.path.join(tempfile.mkdtemp(prefix="motionshot"), "p.png")
                F.grab_png(panel, path, (480, 640))
                with open(path, "rb") as fh:
                    shots.append(fh.read())
                panel.reject()
                panel.form.deleteLater()
                shutil.rmtree(os.path.dirname(path), ignore_errors=True)
            finally:
                rig.close()
        self.assertEqual(shots[0], shots[1])
        self.assertGreater(len(shots[0]), 5000)


class TestMotionCommands(unittest.TestCase):
    """The eight commands (the contact one from slice 6c): registration, when they are active, what they add."""

    NAMES = ("Assembly_CreateMotionStudy", "Assembly_EditMotionStudy", "Assembly_AddMotionGravity", "Assembly_AddMotionForce",
             "Assembly_AddMotionTorque", "Assembly_AddMotionSpring", "Assembly_AddMotionDamper", "Assembly_AddMotionContact")

    def setUp(self):
        self.e = None

    def tearDown(self):
        if self.e is not None:
            self.e.close()
        else:
            if Gui.Control.activeDialog():
                Gui.Control.closeDialog()
            for n in list(App.listDocuments()):
                App.closeDocument(n)

    def load(self, kind="damped", mode="single", host=None):
        if self.e is not None:
            self.e.close()
        self.e = Env(kind, mode, host)
        self.doc, self.asm = self.e.doc, self.e.asm
        Gui.Selection.clearSelection()
        return self.e

    @staticmethod
    def cmd(name):
        return Gui.Command.get(name)

    def select(self, *paths):
        Gui.Selection.clearSelection()
        for obj, sub in paths:
            Gui.Selection.addSelection(self.doc.Name, obj, sub)

    def active(self):
        return [n for n in self.NAMES if self.cmd(n).isActive()]

    def test_commands_are_registered_with_icons_and_placed(self):
        Gui.activateWorkbench("AssemblyWorkbench")
        Gui.updateGui()
        texts = set()
        for name in self.NAMES:
            info = self.cmd(name).getInfo()
            for k in ("menuText", "toolTip", "statusTip", "pixmap"):
                self.assertTrue(info[k], "%s %s" % (name, k))
            self.assertTrue(os.path.isfile(info["pixmap"]), info["pixmap"])
            self.assertFalse(QtGui.QIcon(info["pixmap"]).isNull())
            self.assertEqual(self.cmd(name).getShortcut(), "")
            self.assertNotEqual(info["toolTip"], info["menuText"])
            self.assertNotIn(info["menuText"], texts)
            texts.add(info["menuText"])
            for k in ("menuText", "toolTip", "statusTip"):
                self.assertNotIn("motion study", info[k].lower(), "%s uses another product's wording" % name)
            acts = [a for a in Gui.getMainWindow().findChildren(QtGui.QAction) if a.objectName() == name]
            self.assertTrue(acts, name)
            self.assertTrue(any(isinstance(w, QtWidgets.QMenu) for a in acts for w in a.associatedObjects()), name)
            self.assertTrue(any(isinstance(w, QtWidgets.QToolBar) for a in acts for w in a.associatedObjects()), name)
        icons = {self.cmd(n).getInfo()["pixmap"] for n in self.NAMES}
        self.assertEqual(len(icons), len(self.NAMES), "each command has its own icon")

    def test_is_active_matrix(self):
        Gui.activateWorkbench("AssemblyWorkbench")
        e = self.load()
        base = ["Assembly_CreateMotionStudy", "Assembly_EditMotionStudy", "Assembly_AddMotionGravity"]
        self.assertEqual(self.active(), [], "no selection and no assembly being edited")
        self.select(("Assembly", ""))
        self.assertEqual(self.active(), base)
        self.select(("Assembly", "Block." + BLOCK))
        self.assertEqual(self.active(), base + ["Assembly_AddMotionForce", "Assembly_AddMotionTorque"])
        self.select(("Assembly", "G." + GROUND), ("Assembly", "Block." + BLOCK))
        self.assertEqual(self.active(), base + ["Assembly_AddMotionSpring", "Assembly_AddMotionDamper", "Assembly_AddMotionContact"])
        self.select(("Assembly", "Block." + BLOCK), ("Assembly", "Block.Vertex1"))
        self.assertNotIn("Assembly_AddMotionSpring", self.active(), "two points on one part")
        self.assertNotIn("Assembly_AddMotionContact", self.active(), "two points on one part")
        self.select(("Assembly", "G." + GROUND), ("Assembly", "Block." + BLOCK))
        MO.add_gravity(e.study)                                     # gravity is added once
        self.assertNotIn("Assembly_AddMotionGravity", self.active())
        # an open task dialog silences every command, and so does an open panel
        class Dummy:
            def __init__(self):
                self.form = QtWidgets.QWidget()

            def getStandardButtons(self):
                return 0

        Gui.Control.showDialog(Dummy())
        self.assertEqual(self.active(), [], "another dialog is open")
        Gui.Control.closeDialog()
        e.panel()
        self.assertEqual(self.active(), [], "the panel is open")
        e.finish()
        self.assertNotEqual(self.active(), [])
        # the study is also found through the selection of one of its loads
        self.select(("Gravity", ""))
        self.assertIn("Assembly_EditMotionStudy", self.active())
        # two studies and none selected: a load has nowhere to go
        MO.create_study(e.asm, name="Second")
        self.select(("Assembly", "Block." + BLOCK))
        self.assertNotIn("Assembly_AddMotionForce", self.active())
        self.assertNotIn("Assembly_EditMotionStudy", self.active())

    def test_create_makes_one_study_and_opens_the_panel_and_edit_reopens_it(self):
        Gui.activateWorkbench("AssemblyWorkbench")
        e = self.load()
        u0 = e.doc.UndoCount
        self.select(("Assembly", ""))
        Gui.runCommand("Assembly_CreateMotionStudy")
        Gui.updateGui()
        self.assertEqual(len(MO.studies(e.asm)), 2)
        self.assertEqual(e.doc.UndoCount, u0 + 1, "one transaction")
        self.assertTrue(MP.panel_open())
        study = MO.studies(e.asm)[-1]
        self.assertEqual(study.Name, "DynamicAnalysis")
        self.assertEqual((study.TimeEnd, study.TimeStep, study.Mode), (1.0, 0.01, "Dynamic"))
        Gui.Control.closeDialog()
        pump()
        self.assertFalse(MP.panel_open())
        self.select((study.Name, ""))
        Gui.runCommand("Assembly_EditMotionStudy")
        Gui.updateGui()
        self.assertTrue(MP.panel_open())
        self.assertEqual(e.doc.UndoCount, u0 + 1, "opening the panel changes nothing")
        self.assertEqual(MP._current.study_name, study.Name)

    def _undo_one(self, e, add):
        u0, sig0 = e.doc.UndoCount, MO.signature(e.doc)
        n0 = len(MO.loads(e.study))
        add()
        self.assertEqual(e.doc.UndoCount, u0 + 1, "one undo transaction")
        self.assertEqual(len(MO.loads(e.study)), n0 + 1)
        load = MO.loads(e.study)[-1]
        values = (load.Kind, load.Part1, load.Part2, tuple(load.Point1), tuple(load.Point2), tuple(load.Value),
                  load.Stiffness, load.Damping, load.FreeLength)
        e.doc.undo()
        self.assertEqual(MO.signature(e.doc), sig0, "undo restores the signature exactly")
        e.doc.redo()
        self.assertEqual(MO.loads(e.study)[-1].Name, load.Name)
        return values

    def test_each_add_is_one_transaction_and_places_the_point_in_the_part_frame(self):
        Gui.activateWorkbench("AssemblyWorkbench")
        for mode in ("single", "link"):
            e = self.load("damped", mode)
            self.assertEqual(len(MO.loads(e.study)), 2)                      # the fixture's spring and damper
            self.select(("Assembly", ""))
            g = self._undo_one(e, lambda: Gui.runCommand("Assembly_AddMotionGravity"))
            self.assertEqual((g[0], g[5]), ("Gravity", (0.0, 0.0, -MO.G0)))
            self.select(("Assembly", "Block." + BLOCK))
            e.prompts.values = [{"x": 3.0, "y": 0.0, "z": -4.0}]
            f = self._undo_one(e, lambda: Gui.runCommand("Assembly_AddMotionForce"))
            self.assertEqual((f[0], f[1], f[3], f[5]), ("Force", "Block", (50.0, 50.0, 50.0), (3.0, 0.0, -4.0)), mode)
            e.prompts.values = [{"x": 0.0, "y": 0.0, "z": 2.5}]
            t = self._undo_one(e, lambda: Gui.runCommand("Assembly_AddMotionTorque"))
            self.assertEqual((t[0], t[1], t[5]), ("Torque", "Block", (0.0, 0.0, 2.5)))
            self.select(("Assembly", "G." + GROUND), ("Assembly", "Block." + BLOCK))
            e.prompts.values = [{"k": 750.0, "l0": 123.0}]
            s = self._undo_one(e, lambda: Gui.runCommand("Assembly_AddMotionSpring"))
            self.assertEqual((s[0], s[1], s[2], s[3], s[4], s[6], s[8]),
                             ("Spring", "G", "Block", (400.0, 400.0, 10.0), (50.0, 50.0, 50.0), 750.0, 123.0), mode)
            want_gap = (e.rig.parts["G"].Placement.multVec(V(400, 400, 10))
                        - e.rig.parts["Block"].Placement.multVec(V(50, 50, 50))).Length
            self.assertAlmostEqual(e.prompts.asked[-1][2][1], want_gap, places=5)    # the proposed free length
            e.prompts.values = [{"c": 4.5}]
            d = self._undo_one(e, lambda: Gui.runCommand("Assembly_AddMotionDamper"))
            self.assertEqual((d[0], d[1], d[2], d[7]), ("Damper", "G", "Block", 4.5))
            u0 = e.doc.UndoCount                                            # cancelling the question adds nothing
            e.prompts.values = [None]
            Gui.runCommand("Assembly_AddMotionDamper")
            self.assertEqual(e.doc.UndoCount, u0)

    def test_the_contact_command_adds_one_contact_in_one_transaction_on_plain_and_linked_parts(self):
        Gui.activateWorkbench("AssemblyWorkbench")
        for mode in ("single", "link"):
            e = self.load("damped", mode)
            self.select(("Assembly", "G." + GROUND), ("Assembly", "Block." + BLOCK))
            e.prompts.values = [{"e": 0.6, "tol": 0.02}]
            c = self._undo_one(e, lambda: Gui.runCommand("Assembly_AddMotionContact"))
            self.assertEqual(c[:3], ("Contact", "G", "Block"), mode)
            ld = MO.loads(e.study)[-1]
            self.assertEqual((ld.Restitution, ld.ContactTolerance), (0.6, 0.02))
            u0 = e.doc.UndoCount                                            # cancelling the question adds nothing
            e.prompts.values = [None]
            Gui.runCommand("Assembly_AddMotionContact")
            self.assertEqual(e.doc.UndoCount, u0)
            e.prompts.values = [{"e": 1.5, "tol": 0.02}]                    # an impossible restitution is reported, not added
            n0 = len(MO.loads(e.study))
            Gui.runCommand("Assembly_AddMotionContact")
            self.assertEqual((e.doc.UndoCount, len(MO.loads(e.study))), (u0, n0))
            self.assertTrue(e.prompts.reported and "restitution" in e.prompts.reported[-1], e.prompts.reported)
        # the texts use this project's words
        info = self.cmd("Assembly_AddMotionContact").getInfo()
        for text in (info["menuText"], info["toolTip"], info["statusTip"]):
            self.assertNotIn("motion study", text.lower())

    def test_the_contact_command_is_inactive_for_two_grounded_parts(self):
        Gui.activateWorkbench("AssemblyWorkbench")
        e = self.load("damped", "single")
        rig = e.rig.rig
        shelf = rig.box("Shelf", 50, 50, 10, F.MC.P(F.MC.V(300, 300, 50), F.MC.R()))
        F.MC.cc.ground(rig.jg, shelf)
        rig.finish()
        self.select(("Assembly", "G." + GROUND), ("Assembly", "Shelf." + corner(50, 50, 10)))
        self.assertFalse(self.cmd("Assembly_AddMotionContact").isActive())
        self.select(("Assembly", "G." + GROUND), ("Assembly", "Block." + BLOCK))
        self.assertTrue(self.cmd("Assembly_AddMotionContact").isActive())

    def test_a_contact_is_listed_and_does_not_break_run_plots_or_playback(self):
        Gui.activateWorkbench("AssemblyWorkbench")
        for mode in ("single", "link"):
            e = self.load("pendulum", mode)
            MO.add_contact(e.study, e.rig.parts["Bob"], e.rig.parts["G"], 0.7)
            p = e.panel()
            rows = p.rows()
            self.assertTrue(any(r[0] == "Contact" and "restitution 0.7" in r[2] and "Bob" in r[2] and "G" in r[2]
                                for r in rows), rows)
            res = p.run()
            self.assertIn("contact", res)
            p.show_frame(len(res["t"]) // 2)
            e.finish()
            ids = [q["id"] for q in MO.quantities(res)]
            self.assertTrue(any(i.startswith("velocity:") for i in ids))
            derived = MO.derived_series(e.study, res)
            for i in ids:
                t, y, info = MO.series_of(e.study, res, i, derived)
                self.assertEqual(len(t), len(y))
            self.assertEqual(MO.stale_reason(e.study, res), "")

    def test_the_recovery_hold_nests(self):
        e = self.load("pendulum")
        MP.hold_recovery(e.doc, True)
        MP.hold_recovery(e.doc, True)
        self.assertFalse(e.doc.canWriteRecoverySnapshot())
        MP.hold_recovery(e.doc, False)
        self.assertFalse(e.doc.canWriteRecoverySnapshot(), "one release must not undo the other hold")
        MP.hold_recovery(e.doc, False)
        self.assertTrue(e.doc.canWriteRecoverySnapshot())
        MP.hold_recovery(e.doc, False)                               # an unmatched release does nothing
        self.assertTrue(e.doc.canWriteRecoverySnapshot())

    def test_a_load_added_by_the_command_runs_like_one_added_by_the_api(self):
        Gui.activateWorkbench("AssemblyWorkbench")
        e = self.load("pendulum", "link")
        twin = F.build("pendulum", "link", "MotionTwin2")
        try:
            activate(e.doc)
            self.select(("Assembly", "Bob." + BOB))
            e.prompts.values = [{"x": 1.0, "y": 0.0, "z": 0.0}]
            Gui.runCommand("Assembly_AddMotionForce")
            MO.add_force(twin.study, twin.parts["Bob"], (20, 20, 20), (1.0, 0.0, 0.0))
            MO.run(e.study)
            MO.run(twin.study)
            self.assertEqual(e.study.Result, twin.study.Result)
        finally:
            twin.close()
            activate(e.doc)

    def test_points_through_a_link_and_a_sub_assembly(self):
        e = self.load("damped", "link")
        sub = e.asm.newObject("Assembly::AssemblyObject", "Sub")        # a sub-assembly offset in the outer assembly
        inner = sub.newObject("Part::Box", "Inner")
        inner.Length = inner.Width = inner.Height = 10
        sub.Placement = App.Placement(V(100, 0, 0), App.Rotation())
        e.doc.recompute()
        self.select(("Assembly", "Sub.Inner." + corner(10, 10, 10)))
        picks = CM.points(e.asm)
        self.assertEqual([p.comp.Name for p in picks], ["Inner"])
        self.assertAlmostEqual((picks[0].local - V(10, 10, 10)).Length, 0.0, places=6)
        self.select(("Block", BLOCK))                                 # an App::Link part, selected on its own
        picks = CM.points(e.asm)
        self.assertEqual([(p.comp.Name, tuple(round(c, 6) for c in p.local)) for p in picks], [("Block", (50.0, 50.0, 50.0))])
        Gui.Selection.clearSelection()                                    # a face: the click is used
        Gui.Selection.addSelection(e.doc.Name, "Assembly", "Block.Face1", 230.0, 25.0, 10.0)
        picks = CM.points(e.asm)
        self.assertAlmostEqual((picks[0].local - V(0, 25, 10)).Length, 0.0, places=6)
        self.select(("Assembly", "Block.Face1"))                          # a face without one: its centre
        picks = CM.points(e.asm)
        self.assertAlmostEqual((picks[0].local - V(0, 25, 25)).Length, 0.0, places=6)
        self.select(("Assembly", ""))                                     # the assembly itself is no pick
        self.assertEqual(CM.picked_components(e.asm), [])


class TestMotionRepairs(unittest.TestCase):
    """Run 049 repair 1: the verifier's findings, one test each."""

    @staticmethod
    def sub_rig(e):
        for ld in list(MO.loads(e.study)):
            e.doc.removeObject(ld.Name)
        sub = e.asm.newObject("Assembly::AssemblyObject", "Sub")
        inner = sub.newObject("Part::Box", "Inner")
        inner.Length = inner.Width = inner.Height = 10
        sub.Placement = App.Placement(V(100, 0, 0), App.Rotation())
        e.doc.recompute()
        return inner

    def test_save_while_a_frame_is_shown_writes_the_pre_run_placements(self):
        d = tempfile.mkdtemp()
        try:
            with env("damped", "single", None, d) as e:
                p0 = F.placements(e.asm)
                p = e.panel()
                p.run()
                for how in ("saveAs", "save", "saveCopy"):
                    p.show_frame(60)
                    shown = F.placements(e.asm)
                    self.assertNotEqual(shown, p0)
                    f = os.path.join(d, how + ".FCStd")
                    if how == "saveAs":
                        e.doc.saveAs(f)
                    elif how == "save":
                        e.doc.save()
                    else:
                        e.doc.saveCopy(f)
                    pump()
                    if how == "save":
                        f = e.doc.FileName
                    g = os.path.join(d, "read_" + how + ".FCStd")         # the saved file, opened apart from the live document
                    shutil.copy(f, g)
                    d2 = App.openDocument(g)
                    try:
                        asm = [o for o in d2.Objects if o.isDerivedFrom("Assembly::AssemblyObject")][0]
                        self.assertEqual(F.placements(asm), p0, how + " wrote display placements")
                    finally:
                        App.closeDocument(d2.Name)
                    # the frame is back in the view afterwards
                    for _ in range(20):
                        pump()
                    self.assertEqual(F.placements(e.asm), shown, how + " did not show the frame again")
                e.finish()
                self.assertEqual(F.placements(e.asm), p0)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_loads_and_masses_inside_a_sub_assembly_are_refused(self):
        with env("damped", "link") as e:
            inner = self.sub_rig(e)
            blk = e.rig.parts["Block"]
            MO.add_spring(e.study, blk, V(0, 0, 0), inner, V(0, 0, 0), 100.0, 50.0)
            with self.assertRaises(MO.MotionRefused) as cm:
                MO.compute(e.study)
            self.assertIn("sub-assembly", str(cm.exception))
            self.assertTrue(str(cm.exception).startswith("dynamic analysis refused"), str(cm.exception))
            with self.assertRaises(MO.MotionRefused):
                MO.run(e.study)
            self.assertEqual(e.study.Result, "")
        with env("damped", "link") as e:
            inner = self.sub_rig(e)
            MO.set_mass(e.study, inner, density=7800.0)
            with self.assertRaises(MO.MotionRefused):
                MO.compute(e.study)
        with env("damped", "link") as e:                    # the command refuses the pick with a message
            self.sub_rig(e)
            Gui.activateWorkbench("AssemblyWorkbench")
            Gui.Selection.clearSelection()
            for sub in ("Block.Vertex1", "Sub.Inner.Vertex1"):
                Gui.Selection.addSelection(e.doc.Name, "Assembly", sub)
            n = len(MO.loads(e.study))
            u = e.doc.UndoCount
            e.prompts.values = [{"k": 100.0, "l0": 50.0}]
            Gui.runCommand("Assembly_AddMotionSpring")
            self.assertEqual(len(MO.loads(e.study)), n)
            self.assertEqual(e.doc.UndoCount, u)
            self.assertTrue(e.prompts.reported and "sub-assembly" in e.prompts.reported[-1], e.prompts.reported)
            Gui.Selection.clearSelection()

    def test_ok_clears_a_result_that_no_longer_matches_the_span_and_step(self):
        with env("damped") as e:
            p = e.panel()
            p.run()
            e.finish(True)
            self.assertTrue(e.study.Result)
            u = e.doc.UndoCount
            p = e.panel()
            p.span.setText(repr(MO._q(e.study.TimeStep) * (round(e.study.TimeEnd / e.study.TimeStep) // 2)))
            e.finish(True)
            self.assertEqual(e.doc.UndoCount, u + 1, "one transaction")
            self.assertEqual(e.study.Result, "", "a result for the old span was kept")
            self.assertTrue(e.prompts.reported and "removed" in e.prompts.reported[-1], e.prompts.reported)
        with env("damped") as e:                            # the step edited, no run at the new value
            p = e.panel()
            p.run()
            e.finish(True)
            p = e.panel()
            p.step.setText(repr(MO._q(e.study.TimeStep) * 1.5))
            e.finish(True)
            self.assertEqual(e.study.Result, "")
        with env("damped") as e:                            # a panel opening on a stale stored result does not show it
            p = e.panel()
            p.run()
            e.finish(True)
            e.doc.openTransaction("edit")
            e.study.TimeEnd = e.study.TimeStep * (round(e.study.TimeEnd / e.study.TimeStep) // 2)
            e.doc.commitTransaction()
            p = e.panel()
            self.assertIsNone(p.result)
            self.assertTrue(p.stale and p.message.isVisible() or p.message.text())
            self.assertEqual(p.quantities(), [])
            self.assertFalse(p.play_button.isEnabled())
            self.assertEqual(p.plot.t, [])
            e.finish(False)
            self.assertTrue(e.study.Result, "Cancel changes nothing")
        with env("damped") as e:                            # loads edited since the run
            p = e.panel()
            p.run()
            e.finish(True)
            ld = [x for x in MO.loads(e.study) if x.Kind == "Spring"][0]
            MO.edit_load(ld, Stiffness=ld.Stiffness * 2)
            p = e.panel()
            self.assertIsNone(p.result)
            self.assertIn("edited", p.stale)
            e.finish(True)
            self.assertEqual(e.study.Result, "")
        with env("damped") as e:                            # an unchanged study keeps its result
            p = e.panel()
            p.run()
            e.finish(True)
            p = e.panel()
            self.assertIsNotNone(p.result)
            e.finish(True)
            self.assertTrue(e.study.Result)

    def test_velocity_and_force_do_not_depend_on_the_live_placements(self):
        with env("spring") as e:
            p = e.panel()
            p.run()
            e.finish(True)
            blk = e.rig.parts["Block"]
            blk.Placement = App.Placement(blk.Placement.Base + V(30, 0, 0), blk.Placement.Rotation)
            with self.assertRaises(MO.MotionError):
                MO.derived_series(e.study, MO.result(e.study))
            p = e.panel()                       # the move makes the stored result stale, so it is not shown at all
            self.assertIsNone(p.result)
            self.assertIn("edited", p.stale)
            self.assertEqual(p.quantities(), [])
            e.finish(False)

    def test_ok_after_a_part_was_deleted_does_not_store_a_result_naming_it(self):
        with env("damped") as e:
            p = e.panel()
            p.run()
            e.doc.removeObject(e.rig.parts["Block"].Name)
            r = p.accept()
            self.assertTrue(r)
            self.assertEqual(e.study.Result, "")
            self.assertTrue(e.prompts.reported and "no longer exist" in e.prompts.reported[-1], e.prompts.reported)
            if Gui.Control.activeDialog():
                Gui.Control.closeDialog()
        with env("damped") as e:                            # the API refuses as well
            p = e.panel()
            p.run()
            res = p.result
            e.finish(False)
            e.doc.removeObject(e.rig.parts["Block"].Name)
            with self.assertRaises(MO.MotionError):
                MO.apply_study(e.study, None, None, res)

    def test_user_visible_strings_say_dynamic_analysis(self):
        with env("damped") as e:
            study = MO.create_study(e.asm, "Study2", time_end=0.1, step=0.01)
            MO.add_gravity(study)
            MO.set_mass(study, e.rig.parts["Block"], density=1000.0)
            MO.edit_load(MO.loads(study)[0], Value=(0, 0, -5.0))
            MO.run(e.rig.study)
            MO.apply_frame(e.rig.study, 1)
            MO.remove_load(MO.loads(study)[0])
            MO.remove_mass(MO.masses(study)[0])
            p = e.panel()
            p.span.setText(repr(MO._q(e.study.TimeStep) * (round(e.study.TimeEnd / e.study.TimeStep) // 2)))
            e.finish(True)
            names = list(e.doc.UndoNames)
            self.assertGreaterEqual(len(names), 8, names)
            for n in names:
                self.assertNotIn("motion", n.lower(), n)
                self.assertIn("dynamic analysis", n.lower(), n)
            inner = self.sub_rig(e)
            MO.add_spring(e.study, e.rig.parts["Block"], V(0, 0, 0), inner, V(0, 0, 0), 100.0, 50.0)
            with self.assertRaises(MO.MotionRefused) as cm:
                MO.compute(e.study)
            self.assertNotIn("motion", str(cm.exception).lower())


    # ---- run 049 repair 2

    def test_recovery_writes_never_carry_display_placements(self):
        d = tempfile.mkdtemp()
        try:
            with env("damped", "single", None, d) as e:
                p0 = F.placements(e.asm)
                p = e.panel()
                p.run()
                self.assertTrue(e.doc.canWriteRecoverySnapshot())
                p.show_frame(60)
                shown = F.placements(e.asm)
                self.assertNotEqual(shown, p0)
                self.assertFalse(e.doc.canWriteRecoverySnapshot(), "a recovery write is possible while a frame is shown")
                with self.assertRaises(Exception):
                    App.writeRecoverySnapshotToTransientDir(e.doc)
                self.assertEqual(F.placements(e.asm), shown)         # the refusal left the display alone
                # a save while the hold is on: the save guard stops the display, writes, shows the frame again
                e.doc.saveAs(os.path.join(d, "s.FCStd"))
                for _ in range(20):
                    pump()
                self.assertFalse(e.doc.canWriteRecoverySnapshot(), "the hold was lost after a save")
                # after the display ends the snapshot is written and holds the pre-run placements
                p.stop()
                self.assertTrue(e.doc.canWriteRecoverySnapshot())
                self.assertTrue(App.writeRecoverySnapshotToTransientDir(e.doc))
                files = [os.path.join(r, x) for r, _ds, fs in os.walk(e.doc.TransientDir) for x in fs if x.endswith(".fcstd")]
                self.assertEqual(len(files), 1, files)
                g = os.path.join(d, "rec.FCStd")
                shutil.copy(files[0], g)
                d2 = App.openDocument(g)
                try:
                    asm = [o for o in d2.Objects if o.isDerivedFrom("Assembly::AssemblyObject")][0]
                    self.assertEqual(F.placements(asm), p0)
                finally:
                    App.closeDocument(d2.Name)
                    activate(e.doc)
                # every way the display ends releases the hold
                e.finish()
                for how in ("reject", "accept", "close"):
                    p = e.panel()
                    p.run()
                    p.show_frame(30)
                    self.assertFalse(e.doc.canWriteRecoverySnapshot(), how)
                    if how == "close":
                        Gui.Control.closeDialog()
                    else:
                        getattr(p, how)()
                    e.finish()
                    self.assertTrue(e.doc.canWriteRecoverySnapshot(), how)
                    self.assertEqual(F.placements(e.asm), p0, how)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_recovery_hold_adds_no_undo_step_and_leaves_modified_alone(self):
        with env("damped") as e:
            u, r, m = e.doc.UndoCount, e.doc.RedoCount, e.gdoc.Modified
            p = e.panel()
            p.run()
            p.show_frame(40)
            p.play()
            pump()
            e.finish()
            self.assertEqual((e.doc.UndoCount, e.doc.RedoCount, e.gdoc.Modified), (u, r, m))
            self.assertTrue(e.doc.canWriteRecoverySnapshot())

    def stored_run(self, e):
        p = e.panel()
        p.run()
        e.finish(True)
        self.assertTrue(e.study.Result)
        self.assertEqual(MO.stale_reason(e.study, MO.result(e.study)), "")

    def reopen(self, e, d, name="ro.FCStd"):
        sname, f = e.study.Name, os.path.join(d, name)
        e.doc.saveAs(f)
        for n in list(App.listDocuments()):
            App.closeDocument(n)
        d2 = App.openDocument(f)
        activate(d2)
        asm = [o for o in d2.Objects if o.isDerivedFrom("Assembly::AssemblyObject")][0]
        return d2, [o for o in asm.Group if o.Name == sname][0]

    def test_a_joint_shape_or_initial_placement_edit_makes_the_result_stale(self):
        def edit_joint(e):
            j = e.doc.getObject("Rail")
            j.Distance = float(j.Distance.Value) + 7.0

        def edit_shape(e):
            b = e.rig.parts["Block"]
            b.Length = b.Length * 1.5

        def move_part(e):
            b = e.rig.parts["Block"]
            b.Placement = App.Placement(b.Placement.Base + V(0, 0, 3.0), b.Placement.Rotation)

        # a moved part is not recomputed: the solver would put it back where its joints hold it, and a run reads the placement
        for label, edit, recompute in (("joint distance", edit_joint, True), ("block size", edit_shape, True),
                                       ("initial placement", move_part, False)):
            with env("damped") as e:
                self.stored_run(e)
                e.doc.openTransaction("edit")
                edit(e)
                e.doc.commitTransaction()
                if recompute:
                    e.doc.recompute()
                why = MO.stale_reason(e.study, MO.result(e.study))
                self.assertNotEqual(why, "", label + " left the old result current")
                p = e.panel()
                self.assertIsNone(p.result, label)
                self.assertTrue(p.stale, label)
                e.finish()
                # undo brings the old result back as current
                e.doc.undo()
                if recompute:
                    e.doc.recompute()
                self.assertEqual(MO.stale_reason(e.study, MO.result(e.study)), "", label + " after undo")
                e.doc.redo()
                if recompute:
                    e.doc.recompute()
                self.assertNotEqual(MO.stale_reason(e.study, MO.result(e.study)), "", label + " after redo")

    def test_the_result_stays_current_through_save_reopen_recompute_and_unrelated_edits(self):
        for kind in ("damped", "pendulum", "spring", "force"):
            d = tempfile.mkdtemp()
            try:
                with env(kind, "single", None, d) as e:
                    self.stored_run(e)
                    cur = lambda st: MO.stale_reason(st, MO.result(st))      # noqa: E731
                    e.doc.recompute()
                    self.assertEqual(cur(e.study), "", kind + " recompute")
                    e.doc.openTransaction("u")
                    list(e.rig.parts.values())[1].Label = "Renamed"
                    e.doc.commitTransaction()
                    self.assertEqual(cur(e.study), "", kind + " unrelated edit")
                    e.doc.undo()
                    e.doc.redo()
                    self.assertEqual(cur(e.study), "", kind + " undo redo")
                    d2, st = self.reopen(e, d)
                    self.assertEqual(cur(st), "", kind + " reopen")
                    d2.recompute()
                    self.assertEqual(cur(st), "", kind + " reopen recompute")
                    sname = st.Name
                    d2.saveAs(os.path.join(d, "ro2.FCStd"))
                    for n in list(App.listDocuments()):
                        App.closeDocument(n)
                    d3 = App.openDocument(os.path.join(d, "ro2.FCStd"))
                    asm = [o for o in d3.Objects if o.isDerivedFrom("Assembly::AssemblyObject")][0]
                    st3 = [o for o in asm.Group if o.Name == sname][0]
                    self.assertEqual(cur(st3), "", kind + " second reopen")
                    for n in list(App.listDocuments()):
                        App.closeDocument(n)
            finally:
                shutil.rmtree(d, ignore_errors=True)

    def test_unquantised_settings_do_not_flip_stale_after_a_reopen(self):
        d = tempfile.mkdtemp()
        try:
            with env("damped", "single", None, d) as e:
                e.doc.openTransaction("raw")
                e.study.Rtol = 1e-8 / 3                 # as the property editor assigns it: 17 digits
                e.study.Atol = 1e-10 / 7
                MO.masses(e.study)[0].Density = 7850.0 / 7
                sp = [x for x in MO.loads(e.study) if x.Kind == "Spring"][0]
                sp.Stiffness = 400.0 / 3
                sp.Point1 = V(0, 25 / 3, 25)
                e.doc.commitTransaction()
                self.stored_run(e)
                d2, st = self.reopen(e, d)
                self.assertEqual(MO.stale_reason(st, MO.result(st)), "")
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
