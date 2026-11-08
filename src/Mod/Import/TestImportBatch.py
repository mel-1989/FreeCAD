# SPDX-License-Identifier: LGPL-2.1-or-later

# **************************************************************************
#   Tests for the batch translation command translate.py (G13 slice 7).   *
#   The command runs one child FreeCADCmd per input (Python subprocess).   *
# **************************************************************************

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

import FreeCAD as App
import Part

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "translate.py")
FAULT = "FREECAD_TRANSLATE_TEST_FAULT"


def _binary():
    candidate = os.path.join(App.getHomePath(), "bin", "FreeCADCmd")
    return candidate if os.path.isfile(candidate) else sys.executable


class BatchTranslateCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="g13batch")
        self.out = os.path.join(self.dir, "out")
        self.box = os.path.join(self.dir, "box.brep")
        Part.makeBox(10, 20, 30).exportBrep(self.box)
        self.cyl = os.path.join(self.dir, "cyl.brep")
        Part.makeCylinder(5, 7).exportBrep(self.cyl)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_batch(self, *args, fault=None, limit=120):
        env = dict(os.environ)
        env.pop(FAULT, None)
        if fault:
            env[FAULT] = fault
        start = time.monotonic()
        done = subprocess.run([_binary(), SCRIPT, "--pass", *args], env=env, capture_output=True, text=True,
                              timeout=limit)
        return done, time.monotonic() - start

    def report(self, name="report.json"):
        with open(os.path.join(self.dir, name), encoding="utf-8") as handle:
            return json.load(handle)

    # -- interface

    def test_help_exits_zero_and_names_the_options(self):
        done, _ = self.run_batch("--help")
        self.assertEqual(done.returncode, 0)
        for word in ("to=", "out=", "timeout=", "report="):
            self.assertIn(word, done.stdout)

    def test_usage_errors_exit_two(self):
        for args in (["out=" + self.out, self.box],               # no to=
                     ["to=nope", self.box],                 # unknown format
                     ["to=step"],                           # no input
                     ["to=step", os.path.join(self.dir, "missing.step")],
                     ["to=step", "timeout=-1", self.box],
                     ["to=step", "timeout=abc", self.box],      # a value that is not a number
                     ["to=step", "bogus=1", self.box]):
            done, _ = self.run_batch(*args)
            self.assertEqual(done.returncode, 2, args)
            self.assertEqual(os.path.exists(self.out), False, args)

    def test_one_line_per_file_summary_and_report(self):
        report = os.path.join(self.dir, "report.json")
        done, _ = self.run_batch("to=step", "out=" + self.out, "report=" + report, self.box, self.cyl)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        lines = [ln for ln in done.stdout.splitlines() if ln.startswith(("ok", "changed", "failed", "timeout"))]
        self.assertEqual(len(lines), 2)
        self.assertIn("2 files: 2 ok, 0 changed, 0 failed, 0 timeout", done.stdout)
        data = self.report()
        self.assertEqual(data["to"], "step")
        self.assertEqual(data["summary"]["files"], 2)
        by_input = {os.path.basename(e["input"]): e for e in data["files"]}
        entry = by_input["box.brep"]
        for key in ("input", "output", "status", "seconds", "solids_in", "volume_in", "area_in",
                    "solids_out", "volume_out", "area_out", "messages"):
            self.assertIn(key, entry)
        self.assertEqual(entry["status"], "ok")
        self.assertEqual(entry["solids_in"], 1)
        self.assertEqual(entry["solids_out"], 1)
        self.assertAlmostEqual(entry["volume_in"], 6000.0, places=6)
        self.assertAlmostEqual(entry["volume_out"], 6000.0, places=3)
        self.assertAlmostEqual(entry["area_in"], 2 * (200 + 300 + 600), places=6)
        self.assertTrue(os.path.getsize(entry["output"]) > 0)
        self.assertEqual(by_input["cyl.brep"]["solids_out"], 1)

    def test_folders_and_globs_are_expanded(self):
        done, _ = self.run_batch("to=fcstd", "out=" + self.out, self.dir)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(sorted(os.listdir(self.out)), ["box.FCStd", "cyl.FCStd"])
        done, _ = self.run_batch("to=stl", "out=" + self.out, os.path.join(self.dir, "b*.brep"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("box.stl", os.listdir(self.out))
        self.assertIn("1 files: 1 ok", done.stdout)

    def test_every_target_format_is_written(self):
        for fmt, ext in (("step", "step"), ("iges", "iges"), ("brep", "brep"), ("stl", "stl"), ("obj", "obj"),
                         ("3mf", "3mf"), ("dxf", "dxf"), ("fcstd", "FCStd")):
            done, _ = self.run_batch("to=" + fmt, "out=" + self.out, self.box)
            self.assertEqual(done.returncode, 0, (fmt, done.stdout, done.stderr))
            path = os.path.join(self.out, "box." + ext)
            self.assertTrue(os.path.isfile(path) and os.path.getsize(path) > 0, fmt)

    def test_brep_round_trip_is_exact_and_status_follows_the_figures(self):
        report = os.path.join(self.dir, "report.json")
        for fmt in ("brep", "iges", "step"):
            done, _ = self.run_batch("to=" + fmt, "out=" + self.out, "report=" + report, self.box)
            self.assertEqual(done.returncode, 0, done.stdout)
            entry = self.report()["files"][0]
            self.assertIn(entry["status"], ("ok", "changed"))
            tolerance = 1e-9 if fmt == "brep" else 1e-6
            same = (entry["solids_in"] == entry["solids_out"]
                    and abs(entry["volume_in"] - entry["volume_out"]) <= tolerance * entry["volume_in"]
                    and abs(entry["area_in"] - entry["area_out"]) <= tolerance * entry["area_in"])
            self.assertEqual(entry["status"], "ok" if same else "changed", fmt)
            if entry["status"] == "changed":
                self.assertTrue(any("read back" in m for m in entry["messages"]))
        self.assertEqual(entry["status"], "ok")  # the last pass is step, and a box survives it

    def test_output_never_overwrites_the_input(self):
        done, _ = self.run_batch("to=brep", "out=" + self.dir, self.box)
        self.assertEqual(done.returncode, 1)
        self.assertIn("overwrite", done.stdout)
        self.assertTrue(os.path.getsize(self.box) > 0)

    def test_tolerances_are_the_decided_ones(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("translate_under_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # without --pass on the command line it only defines names
        self.assertEqual(module.DEFAULT_TOLERANCE, 1e-6)
        self.assertEqual(module.TOLERANCE["brep"], 1e-9)
        self.assertFalse(module._differs(1000.0, 1000.0005, module.DEFAULT_TOLERANCE))
        self.assertTrue(module._differs(1000.0, 1000.002, module.DEFAULT_TOLERANCE))
        self.assertTrue(module._differs(1000.0, 1000.0005, module.TOLERANCE["brep"]))

    # -- failures lose one file only

    def test_unreadable_file_fails_alone(self):
        bad = os.path.join(self.dir, "bad.step")
        with open(bad, "w") as handle:
            handle.write("this is not a STEP file\n")
        report = os.path.join(self.dir, "report.json")
        done, _ = self.run_batch("to=brep", "out=" + self.out, "report=" + report, bad, self.box)
        self.assertEqual(done.returncode, 1, done.stdout)
        status = {os.path.basename(e["input"]): e["status"] for e in self.report()["files"]}
        self.assertEqual(status["bad.step"], "failed")
        failed = [e for e in self.report()["files"] if e["input"] == bad][0]
        self.assertFalse(any("reported success" in m for m in failed["messages"]))  # the child itself said failed
        self.assertEqual(status["box.brep"], "ok")
        self.assertFalse(os.path.exists(os.path.join(self.out, "bad.brep")))

    def test_crashing_child_fails_that_file_and_the_batch_goes_on(self):
        report = os.path.join(self.dir, "report.json")
        done, _ = self.run_batch("to=step", "out=" + self.out, "report=" + report, self.box, self.cyl,
                                 fault="crash:box")
        self.assertEqual(done.returncode, 1, done.stdout)
        entries = {os.path.basename(e["input"]): e for e in self.report()["files"]}
        self.assertEqual(entries["box.brep"]["status"], "failed")
        self.assertTrue(any("signal" in m or "code" in m for m in entries["box.brep"]["messages"]))
        self.assertEqual(entries["cyl.brep"]["status"], "ok")
        self.assertIn("1 ok, 0 changed, 1 failed, 0 timeout", done.stdout)

    def test_hanging_child_times_out_and_the_batch_goes_on(self):
        report = os.path.join(self.dir, "report.json")
        done, seconds = self.run_batch("to=step", "out=" + self.out, "report=" + report, "timeout=4",
                                       self.box, self.cyl, fault="hang:box")
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertLess(seconds, 60)
        entries = {os.path.basename(e["input"]): e for e in self.report()["files"]}
        self.assertEqual(entries["box.brep"]["status"], "timeout")
        self.assertEqual(entries["cyl.brep"]["status"], "ok")
        self.assertIn("0 changed, 0 failed, 1 timeout", done.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.out, "box.step")))

    def test_failed_file_never_removes_an_existing_output(self):
        os.makedirs(self.out)
        keep = os.path.join(self.out, "bad.step")
        with open(keep, "w") as handle:
            handle.write("KEEP\n")
        bad = os.path.join(self.dir, "bad.iges")
        with open(bad, "w") as handle:
            handle.write("garbage\n")
        done, _ = self.run_batch("to=step", "out=" + self.out, bad)
        self.assertEqual(done.returncode, 1, done.stdout)
        with open(keep) as handle:
            self.assertEqual(handle.read(), "KEEP\n")
        self.assertEqual(sorted(os.listdir(self.out)), ["bad.step"])  # no scratch folder left either

    def test_timed_out_file_never_removes_an_existing_output(self):
        os.makedirs(self.out)
        keep = os.path.join(self.out, "box.step")
        with open(keep, "w") as handle:
            handle.write("KEEP\n")
        done, _ = self.run_batch("to=step", "out=" + self.out, "timeout=4", self.box, fault="hang:box")
        self.assertEqual(done.returncode, 1, done.stdout)
        with open(keep) as handle:
            self.assertEqual(handle.read(), "KEEP\n")
        self.assertEqual(sorted(os.listdir(self.out)), ["box.step"])

    def test_child_that_dies_after_writing_leaves_an_existing_output_alone(self):
        os.makedirs(self.out)
        keep = os.path.join(self.out, "box.step")
        with open(keep, "w") as handle:
            handle.write("KEEP\n")
        done, _ = self.run_batch("to=step", "out=" + self.out, self.box, fault="dirty:box")
        self.assertEqual(done.returncode, 1, done.stdout)
        with open(keep) as handle:
            self.assertEqual(handle.read(), "KEEP\n")
        self.assertEqual(sorted(os.listdir(self.out)), ["box.step"])

    def test_child_that_ignores_sigterm_is_killed_after_the_grace_period(self):
        report = os.path.join(self.dir, "report.json")
        done, seconds = self.run_batch("to=step", "out=" + self.out, "report=" + report, "timeout=4",
                                       self.box, self.cyl, fault="deaf:box")
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertLess(seconds, 60)
        entries = {os.path.basename(e["input"]): e for e in self.report()["files"]}
        self.assertEqual(entries["box.brep"]["status"], "timeout")
        self.assertEqual(entries["cyl.brep"]["status"], "ok")

    def test_successful_conversion_replaces_a_file_that_is_not_an_input(self):
        os.makedirs(self.out)
        old = os.path.join(self.out, "box.step")
        with open(old, "w") as handle:
            handle.write("OLD\n")
        done, _ = self.run_batch("to=step", "out=" + self.out, self.box)
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertGreater(os.path.getsize(old), 10)
        with open(old) as handle:
            self.assertNotEqual(handle.read(), "OLD\n")
        self.assertEqual(sorted(os.listdir(self.out)), ["box.step"])

    def test_output_never_overwrites_another_input_of_the_batch(self):
        part = os.path.join(self.dir, "part.step")
        Part.makeSphere(4).exportStep(part)
        with open(part, "rb") as handle:
            original = handle.read()
        sibling = os.path.join(self.dir, "part.brep")
        Part.makeBox(1, 2, 3).exportBrep(sibling)
        report = os.path.join(self.dir, "report.json")
        done, _ = self.run_batch("to=step", "out=" + self.dir, "report=" + report, sibling, part)
        with open(part, "rb") as handle:
            self.assertEqual(handle.read(), original)
        entries = {os.path.basename(e["input"]): e for e in self.report()["files"]}
        self.assertEqual(entries["part.brep"]["status"], "ok", done.stdout)
        self.assertNotEqual(os.path.realpath(entries["part.brep"]["output"]), os.path.realpath(part))
        self.assertTrue(os.path.getsize(entries["part.brep"]["output"]) > 0)
        self.assertIn("_2", os.path.basename(entries["part.brep"]["output"]))

    def test_failed_and_timed_out_entries_have_no_output(self):
        bad = os.path.join(self.dir, "bad.step")
        with open(bad, "w") as handle:
            handle.write("nope\n")
        report = os.path.join(self.dir, "report.json")
        self.run_batch("to=brep", "out=" + self.out, "report=" + report, "timeout=4", bad, self.box, self.cyl,
                       fault="hang:cyl")
        entries = {os.path.basename(e["input"]): e for e in self.report()["files"]}
        self.assertIsNone(entries["bad.step"]["output"])
        self.assertEqual(entries["cyl.brep"]["status"], "timeout")
        self.assertIsNone(entries["cyl.brep"]["output"])
        self.assertTrue(entries["box.brep"]["output"].endswith("box.brep"))

    # -- internal failures and odd arguments exit with a code, not 0

    def test_report_in_a_missing_folder_is_a_usage_error(self):
        done, _ = self.run_batch("to=step", "out=" + self.out, "report=/nonexistent-dir/r.json", self.box)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertFalse(os.path.exists(self.out))

    def test_non_finite_timeouts_are_usage_errors(self):
        for value in ("nan", "inf", "-inf"):
            done, _ = self.run_batch("to=step", "out=" + self.out, "timeout=" + value, self.box)
            self.assertEqual(done.returncode, 2, value)

    def test_report_below_a_file_is_a_usage_error(self):
        blocker = os.path.join(self.dir, "blocker")
        with open(blocker, "w") as handle:
            handle.write("x")
        done, _ = self.run_batch("to=step", "out=" + self.out, "report=" + os.path.join(blocker, "r.json"), self.box)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)

    def test_internal_error_exits_one_with_a_message(self):
        done, _ = self.run_batch("to=step", "out=" + self.out, self.box, fault="internal:test")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("internal error", done.stderr)

    def test_non_ascii_paths_under_the_c_locale(self):
        folder = os.path.join(self.dir, "caf\u00e9 \u6a21\u578b")
        os.makedirs(folder)
        source = os.path.join(folder, "b\u00f6x.brep")
        shutil.copy(self.box, source)
        env = dict(os.environ, LC_ALL="C", LANG="C")
        env.pop("PYTHONUTF8", None)
        env.pop("PYTHONIOENCODING", None)
        out = os.path.join(self.dir, "ut\u00e9")
        done = subprocess.run([_binary(), SCRIPT, "--pass", "to=step", "out=" + out, source], env=env,
                              capture_output=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("1 files: 1 ok", done.stdout.decode("ascii", "replace"))
        self.assertTrue(os.path.isfile(os.path.join(out, "b\u00f6x.step")))

    # -- a timed-out child takes its DWG converter with it

    def test_timeout_leaves_no_converter_process_and_no_temp_folder(self):
        marker = "sleep 7%d" % (os.getpid() % 100000 + 100000)
        fakes = os.path.join(self.dir, "fakebin")
        os.makedirs(fakes)
        fake = os.path.join(fakes, "dwg2dxf")
        with open(fake, "w") as handle:
            handle.write("#!/bin/sh\n%s\n" % marker)
        os.chmod(fake, 0o755)
        dwg = os.path.join(self.dir, "x.dwg")
        with open(dwg, "wb") as handle:
            handle.write(b"AC1015 not a dwg")
        scratch = os.path.join(self.dir, "tmp")
        os.makedirs(scratch)
        env = dict(os.environ, PATH=fakes + os.pathsep + os.environ.get("PATH", ""), TMPDIR=scratch)
        env.pop(FAULT, None)
        done = subprocess.run([_binary(), SCRIPT, "--pass", "to=step", "out=" + self.out, "timeout=6", dwg],
                              env=env, capture_output=True, text=True, timeout=120)
        time.sleep(0.5)
        left = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True).stdout.split()
        try:
            self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
            self.assertIn("timeout", done.stdout)
            self.assertEqual(left, [], "converter survived the batch timeout")
            self.assertEqual(os.listdir(scratch), [], "temporary folders left behind")
        finally:
            for pid in left:
                try:
                    os.kill(int(pid), 9)
                except OSError:
                    pass

    def test_exit_zero_when_only_ok_and_changed(self):
        done, _ = self.run_batch("to=iges", "out=" + self.out, self.box, self.cyl)
        self.assertEqual(done.returncode, 0, done.stdout)


    # -- second verifier's findings (run 073)

    def test_names_that_differ_only_in_case_never_share_an_output(self):
        upper = os.path.join(self.dir, "X.brep")
        Part.makeBox(1, 1, 1).exportBrep(upper)
        lower_dir = os.path.join(self.dir, "lower")
        os.makedirs(lower_dir)
        lower = os.path.join(lower_dir, "x.brep")
        Part.makeBox(2, 2, 2).exportBrep(lower)
        report = os.path.join(self.dir, "report.json")
        done, _ = self.run_batch("to=step", "out=" + self.out, "report=" + report, upper, lower)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        outputs = [e["output"] for e in self.report()["files"]]
        self.assertEqual(len({o.casefold() for o in outputs}), 2, outputs)
        self.assertEqual(len(os.listdir(self.out)), 2, os.listdir(self.out))

    def test_folder_at_the_output_fails_that_file_only(self):
        os.makedirs(os.path.join(self.out, "box.step"))
        report = os.path.join(self.dir, "report.json")
        done, _ = self.run_batch("to=step", "out=" + self.out, "report=" + report, self.box, self.cyl)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        entries = {os.path.basename(e["input"]): e for e in self.report()["files"]}
        self.assertEqual(entries["box.brep"]["status"], "failed")
        self.assertEqual(entries["cyl.brep"]["status"], "ok")
        self.assertNotIn("internal error", done.stderr)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores folder permissions")
    def test_read_only_out_fails_each_file_and_still_reports(self):
        os.makedirs(self.out)
        os.chmod(self.out, 0o555)
        try:
            report = os.path.join(self.dir, "report.json")
            done, _ = self.run_batch("to=step", "out=" + self.out, "report=" + report, self.box, self.cyl)
            self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
            self.assertNotIn("internal error", done.stderr)
            self.assertEqual([e["status"] for e in self.report()["files"]], ["failed", "failed"])
        finally:
            os.chmod(self.out, 0o755)

    def test_sigterm_to_the_batch_stops_its_child(self):
        env = dict(os.environ, **{FAULT: "hang:box"})
        process = subprocess.Popen([_binary(), SCRIPT, "--pass", "to=step", "out=" + self.out, "timeout=0",
                                    self.box], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not (os.path.isdir(self.out) and any(
                n.startswith(".translate-partial-") for n in os.listdir(self.out))):
            time.sleep(0.2)
        time.sleep(2.0)  # let the child reach its hang
        process.terminate()
        process.communicate(timeout=60)
        time.sleep(0.5)
        left = subprocess.run(["pgrep", "-f", "child " + self.box], capture_output=True, text=True).stdout.split()
        try:
            self.assertNotEqual(process.returncode, 0)
            self.assertEqual(left, [], "the child outlived the batch")
            self.assertEqual(os.listdir(self.out), [], "scratch folder left in the output folder")
        finally:
            for pid in left:
                try:
                    os.kill(int(pid), 9)
                except OSError:
                    pass


if __name__ == "__main__":
    unittest.main()
