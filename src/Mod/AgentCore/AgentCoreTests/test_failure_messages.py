# SPDX-License-Identifier: LGPL-2.1-or-later
"""G08a slice 1: readable recompute-failure messages (App/FailureMessages.cpp).

Recomputes the corpus of failing features (workshop folder fixtures/messages/, at least 40 cases
from at least 8 modules) in a FreeCADCmd child with a log file and reads back what the report view
would show (the Err/Wrn/Msg lines) and what only the log holds (the Log lines). The change is
display only, so the stored status, State and isValid must equal the manifest's, which was taken
before the catalogue existed.
"""

import json
import os
import re
import subprocess
import tempfile
import unittest

import FreeCAD

CTL = os.environ.get("CADFORK_CTL") or os.path.join(
    os.path.expanduser("~"), "_agents", "repos", "app development", "cad-fork"
)
CORPUS = os.path.join(CTL, "fixtures", "messages")

TRACEBACK = re.compile(r"Traceback \(most recent call last\)|^\s+File \".*\", line \d+", re.M)
INTERNAL = re.compile(
    r"\b(BRep\w*_\w+|Standard_\w+|StdFail_\w+|TopoDS_\w+|NCollection_\w+|gp_\w+|OCC\w*|NotDone|"
    r"TopoShape\w*|Base::\w+Exception|Part\.OCCError)\b|command not done|\w+\.cpp\(\d+\)|<Exception>|<class '"
)
PREFIX = re.compile(r"^(Err|Wrn|Msg|Log|Critical): (.*)$")
REPORT_LEVELS = ("Err", "Wrn", "Msg", "Critical")


def run_corpus():
    """case id -> dict(result fields, report=[lines], log=[lines])."""
    if not os.path.isdir(CORPUS):
        raise AssertionError("corpus missing: %s (set CADFORK_CTL to the cad-fork workshop)" % CORPUS)
    exe = os.path.join(FreeCAD.getHomePath(), "bin", "FreeCADCmd")
    with tempfile.TemporaryDirectory() as tmp:
        log, res = os.path.join(tmp, "fc.log"), os.path.join(tmp, "res.json")
        env = dict(os.environ, G08A_OUT=res, G08A_DIR=CORPUS, QT_QPA_PLATFORM="offscreen")
        subprocess.run([exe, "--log-file", log, os.path.join(CORPUS, "driver.py")], env=env,
                       capture_output=True, encoding="utf-8", errors="replace", timeout=600, check=False)
        with open(res, encoding="utf-8") as fh:
            results = json.load(fh)
        with open(log, encoding="utf-8-sig") as fh:
            text = fh.read()
    logs, cur, level = {}, None, None
    for line in text.splitlines():
        m = PREFIX.match(line)
        if m:
            level, body = m.groups()
            if level == "Log" and body.startswith("@@BEGIN "):
                cur = body[8:].strip()
                logs[cur] = []
                continue
            if level == "Log" and body.startswith("@@END "):
                cur = None
                continue
        else:
            body = line
        if cur is not None and level:
            logs[cur].append((level, body))
    out = {}
    for cid, rec in results.items():
        lines = logs.get(cid, [])
        out[cid] = dict(rec,
                        report=[b for lv, b in lines if lv in REPORT_LEVELS],
                        log=[b for lv, b in lines if lv == "Log"])
    return out


class TestFailureMessages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(CORPUS, "manifest.json"), encoding="utf-8") as fh:
            cls.manifest = json.load(fh)["cases"]
        cls.rows = run_corpus()

    def test_corpus_is_big_enough(self):
        self.assertGreaterEqual(len(self.manifest), 40)
        self.assertGreaterEqual(len({m["module"] for m in self.manifest.values()}), 8)
        self.assertEqual(set(self.manifest), set(self.rows), "driver and manifest list different cases")

    def test_every_case_fails_and_builds(self):
        for cid, row in self.rows.items():
            with self.subTest(case=cid):
                self.assertNotIn("build_error", row, row.get("build_error"))
                self.assertFalse(row["valid"], "the case no longer fails")

    def test_report_view_is_one_readable_sentence_naming_the_label(self):
        for cid, row in self.rows.items():
            with self.subTest(case=cid):
                text = "\n".join(row["report"])
                self.assertIsNone(TRACEBACK.search(text), text)
                self.assertIsNone(INTERNAL.search(text), text)
                label = self.manifest[cid]["label"]
                named = [l for l in row["report"] if "'%s'" % label in l]
                self.assertEqual(len(named), 1, "expected exactly one line naming %r in %r" % (label, row["report"]))
                self.assertTrue(named[0].rstrip().endswith("."), named[0])

    def test_catalogue_keys(self):
        for cid, row in self.rows.items():
            with self.subTest(case=cid):
                keys = re.findall(r"\(message key (\S+)\)", "\n".join(row["log"]))
                self.assertEqual(keys, [self.manifest[cid]["key"]])

    def test_results_unchanged_by_the_catalogue(self):
        for cid, row in self.rows.items():
            with self.subTest(case=cid):
                want = self.manifest[cid]
                self.assertEqual(row["status"], want["stored_status"])
                self.assertEqual(row["state"], want["state"])
                self.assertEqual(row["valid"], want["valid"])

    def test_raw_text_and_tracebacks_stay_in_the_log(self):
        for cid, row in self.rows.items():
            with self.subTest(case=cid):
                log = "\n".join(row["log"])
                first = self.manifest[cid]["stored_status"].strip().splitlines()[0]
                self.assertIn(first, log, "the raw error text is missing from the log")
        # a scripted feature that raises: the traceback is written to the log, not to the report view
        for cid in ("py_value_error", "py_attribute_error", "weld_empty_path", "sm_hem_wrong_edge"):
            with self.subTest(case=cid):
                log = "\n".join(self.rows[cid]["log"])
                self.assertRegex(log, r"Traceback \(most recent call last\)")
                self.assertIn("Python exception in", log)

    def test_unknown_error_falls_back_to_its_own_message_and_the_label(self):
        generic = [c for c, m in self.manifest.items() if m["key"] == "generic"]
        self.assertGreaterEqual(len(generic), 3)
        for cid in generic:
            with self.subTest(case=cid):
                line = [l for l in self.rows[cid]["report"] if "'%s'" % self.manifest[cid]["label"] in l][0]
                own = self.manifest[cid]["stored_status"].strip().splitlines()[0].rstrip(".")
                # the detail is cut at 160 bytes on a UTF-8 boundary, so compare the start of a long message
                self.assertIn(own[:40], line)
                self.assertIn("failed to recompute", line)
                self.assertNotIn("\ufffd", line)


class TestCatalogueEdgeCases(unittest.TestCase):
    """Things the corpus cannot show: a silent document, an empty message, repeated recompute."""

    def test_quiet_when_nothing_fails(self):
        doc = FreeCAD.newDocument("G08aOK")
        self.addCleanup(FreeCAD.closeDocument, doc.Name)
        box = doc.addObject("Part::Box", "Box")
        self.assertTrue(doc.recompute() >= 0)
        self.assertEqual(box.getStatusString(), "Valid")

    def test_stored_status_is_raw_after_a_recompute(self):
        doc = FreeCAD.newDocument("G08aRaw")
        self.addCleanup(FreeCAD.closeDocument, doc.Name)
        box = doc.addObject("Part::Box", "Box")
        fillet = doc.addObject("Part::Fillet", "Fillet")
        fillet.Base = box
        fillet.Edges = [(1, 50.0, 50.0)]
        doc.recompute()
        doc.recompute()
        self.assertEqual(fillet.getStatusString(), "BRep_API: command not done")
        self.assertIn("Invalid", fillet.State)
