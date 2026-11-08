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

    def test_exit_zero_when_only_ok_and_changed(self):
        done, _ = self.run_batch("to=iges", "out=" + self.out, self.box, self.cyl)
        self.assertEqual(done.returncode, 0, done.stdout)


if __name__ == "__main__":
    unittest.main()
