# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *   Copyright (c) 2013 Yorik van Havre <yorik@uncreated.net>              *
# *   Copyright (c) 2019 Eliud Cabrera Castillo <e.cabrera-castillo@tum.de> *
# *   Copyright (c) 2025 FreeCAD Project Association                        *
# *                                                                         *
# *   This file is part of the FreeCAD CAx development system.              *
# *                                                                         *
# *   This program is free software; you can redistribute it and/or modify  *
# *   it under the terms of the GNU Lesser General Public License (LGPL)    *
# *   as published by the Free Software Foundation; either version 2 of     *
# *   the License, or (at your option) any later version.                   *
# *   for detail see the LICENCE text file.                                 *
# *                                                                         *
# *   FreeCAD is distributed in the hope that it will be useful,            *
# *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
# *   GNU Library General Public License for more details.                  *
# *                                                                         *
# *   You should have received a copy of the GNU Library General Public     *
# *   License along with FreeCAD; if not, write to the Free Software        *
# *   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  *
# *   USA                                                                   *
# *                                                                         *
# ***************************************************************************
"""Unit tests for the Draft Workbench, DWG import and export through outside converters.

The converters are small shell scripts written into a temporary folder at test
time, so these tests need no LibreDWG, ODA or QCAD. The tests that run the real
LibreDWG programs skip when dwg2dxf is not installed.
"""

## @package test_dwg
# \ingroup drafttests
# \brief Unit tests for the Draft Workbench, DWG import and export tests.

## \addtogroup drafttests
# @{

import os
import shutil
import stat
import tempfile
import time
import unittest

import FreeCAD as App
import Draft
import importDWG
from draftutils import params as real_params

MINIMAL_DXF = (
    "0\nSECTION\n2\nENTITIES\n0\nLINE\n8\n0\n10\n0.0\n20\n0.0\n30\n0.0\n"
    "11\n10.0\n21\n5.0\n31\n0.0\n0\nENDSEC\n0\nEOF\n"
)

# Shell fragments for the fake converters. $out is the file after -o.
PARSE = """#!/bin/sh
PATH=/usr/bin:/bin
out=""
while [ $# -gt 0 ]; do
  case "$1" in -o) out="$2"; shift;; esac
  shift
done
"""
WRITE_DXF = 'cat > "$out" <<\'DXF_END\'\n' + MINIMAL_DXF + "DXF_END\n"
FAKE_DWG2DXF = {
    "ok": PARSE + WRITE_DXF + "exit 0\n",
    "rc1_nothing": PARSE + 'echo "fake: cannot read file" >&2\nexit 1\n',
    "rc1_partial": PARSE
    + 'printf "0\\nSECTION\\n2\\nENTITIES\\n0\\nLINE\\n" > "$out"\n'
    + 'echo "fake: died half way" >&2\nexit 1\n',
    "rc0_nothing": PARSE + 'echo "fake: said nothing" >&2\nexit 0\n',
    "rc0_empty": PARSE + ': > "$out"\necho "fake: wrote nothing" >&2\nexit 0\n',
    "sleep": PARSE + "sleep 600\nexit 0\n",
    "slow_ok": PARSE + "sleep 1\n" + WRITE_DXF + "exit 0\n",
}
FAKE_DXF2DWG = {
    "ok": PARSE + 'printf "AC1015fakeDWGbytes" > "$out"\nexit 0\n',
    "rc1": PARSE + 'echo "ERROR: READ ERROR 0x1000" >&2\nexit 1\n',
    "rc1_partial": PARSE + 'printf "AC1015" > "$out"\necho "fake: half" >&2\nexit 1\n',
    "rc0_nothing": PARSE + "exit 0\n",
    "rc0_empty": PARSE + ': > "$out"\nexit 0\n',
}


class _Params:
    """Stand-in for draftutils.params that overrides a few entries and writes none."""

    def __init__(self, **overrides):
        self.overrides = overrides

    def get_param(self, entry, *args, **kwargs):
        if entry in self.overrides:
            return self.overrides[entry]
        return real_params.get_param(entry, *args, **kwargs)


@unittest.skipIf(os.name != "posix", "the fake converters are shell scripts")
class DraftDWG(unittest.TestCase):
    """Test reading and writing of DWG files with Draft."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dwgtest_")
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        self.tempdir = os.path.join(self.tmp, "tmpdir")  # tempfile's folder during a test
        os.makedirs(self.tempdir)
        self.dwg = os.path.join(self.tmp, "in.dwg")
        with open(self.dwg, "wb") as f:
            f.write(b"AC1015not a real dwg")
        self.saved = (importDWG.params, tempfile.tempdir, importDWG._libredwg_fixed_dirs)
        self.saved_path = os.environ.get("PATH")
        self.warnings = []
        self.saved_warn = importDWG.FCC.PrintWarning
        importDWG.FCC.PrintWarning = self.warnings.append
        importDWG._libredwg_fixed_dirs = lambda: []
        os.environ["PATH"] = "/nonexistent-dir"
        tempfile.tempdir = self.tempdir
        self.setParams()
        self.doc = App.newDocument("DWGTest")

    def tearDown(self):
        for name in list(App.listDocuments()):
            App.closeDocument(name)
        importDWG.params, tempfile.tempdir, importDWG._libredwg_fixed_dirs = self.saved
        importDWG.FCC.PrintWarning = self.saved_warn
        if self.saved_path is None:
            del os.environ["PATH"]
        else:
            os.environ["PATH"] = self.saved_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def setParams(self, **overrides):
        base = dict(DWGConversion=1, TeighaFileConverter="", DWGConversionTimeout=30)
        base.update(overrides)
        importDWG.params = _Params(**base)

    def fake(self, name, script, folder=None):
        path = os.path.join(folder or self.bin, name)
        with open(path, "w") as f:
            f.write(script)
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        return path

    def install(self, to_dxf=None, to_dwg=None, **overrides):
        """Write fake converters and point the preference at them."""
        pref = ""
        if to_dxf:
            pref = self.fake("dwg2dxf", FAKE_DWG2DXF[to_dxf])
        if to_dwg:
            self.fake("dxf2dwg", FAKE_DXF2DWG[to_dwg])
            pref = pref or os.path.join(self.bin, "dxf2dwg")
        self.setParams(TeighaFileConverter=pref, **overrides)

    def tempEntries(self):
        return sorted(os.listdir(self.tempdir))

    def shapes(self):
        return [Draft.make_line(App.Vector(0, 0, 0), App.Vector(10, 5, 0))]

    def calls(self):
        out = os.path.join(self.tmp, "out.dwg")
        return [
            lambda: importDWG.insert(self.dwg, self.doc.Name),
            lambda: importDWG.open(self.dwg),
            lambda: importDWG.export(self.shapes(), out),
        ]

    # ---- check 1: discovery

    def test_preference_wins_over_path(self):
        other = os.path.join(self.tmp, "otherbin")
        os.makedirs(other)
        self.fake("dwg2dxf", FAKE_DWG2DXF["ok"], other)
        self.fake("dwg2dxf", FAKE_DWG2DXF["ok"])
        os.environ["PATH"] = other
        self.setParams(TeighaFileConverter=os.path.join(self.bin, "dwg2dxf"))
        self.assertEqual(importDWG.get_libredwg_converter("dwg2dxf"), os.path.join(self.bin, "dwg2dxf"))
        self.assertEqual(self.warnings, [])

    def test_missing_preference_falls_through_with_one_warning(self):
        self.fake("dwg2dxf", FAKE_DWG2DXF["ok"])
        os.environ["PATH"] = self.bin
        self.setParams(TeighaFileConverter=os.path.join(self.tmp, "gone", "dwg2dxf"))
        self.assertEqual(importDWG.get_libredwg_converter("dwg2dxf"), os.path.join(self.bin, "dwg2dxf"))
        self.assertEqual(len(self.warnings), 1)

    def test_fixed_places_are_searched_after_path(self):
        self.fake("dwg2dxf", FAKE_DWG2DXF["ok"])
        self.assertIsNone(importDWG.get_libredwg_converter("dwg2dxf"))
        importDWG._libredwg_fixed_dirs = lambda: [self.bin]
        self.assertEqual(importDWG.get_libredwg_converter("dwg2dxf"), os.path.join(self.bin, "dwg2dxf"))
        # a Finder-style PATH does not matter, and insert works end to end
        os.environ["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
        importDWG.insert(self.dwg, self.doc.Name)
        self.assertGreater(len(self.doc.Objects), 0)

    def test_no_converter_raises(self):
        for call in self.calls():
            with self.assertRaises(RuntimeError) as cm:
                call()
            self.assertIn("No suitable external DWG converter", str(cm.exception))

    # ---- check 3: loud failure

    def test_failures_raise_once_with_decided_text(self):
        cases = {
            "rc1_nothing": ["dwg2dxf returned 1", "fake: cannot read file"],
            "rc1_partial": ["dwg2dxf returned 1", "fake: died half way"],
            "rc0_nothing": ["returned 0 but wrote no output file", "fake: said nothing"],
            "rc0_empty": ["returned 0 but wrote an empty output file", "fake: wrote nothing"],
        }
        for case, needles in cases.items():
            self.install(to_dxf=case)
            for call in self.calls()[:2]:
                before = len(self.doc.Objects)
                docs = set(App.listDocuments())
                with self.assertRaises(RuntimeError) as cm:
                    call()
                for needle in needles:
                    self.assertIn(needle, str(cm.exception), case)
                self.assertEqual(len(self.doc.Objects), before, case + ": partial DXF was imported")
                self.assertEqual(set(App.listDocuments()), docs, case)
        self.assertEqual(self.tempEntries(), [])

    def test_convert_functions_return_none(self):
        self.install(to_dxf="rc1_nothing", to_dwg="rc1")
        self.assertIsNone(importDWG.convertToDxf(self.dwg))
        self.assertIsNone(importDWG.convertToDwg(self.dwg, os.path.join(self.tmp, "x.dwg")))

    def test_export_failures_raise_and_never_return_missing_path(self):
        out = os.path.join(self.tmp, "out.dwg")
        for case, needle in {
            "rc1": "READ ERROR 0x1000",
            "rc1_partial": "fake: half",
            "rc0_nothing": "wrote no output file",
            "rc0_empty": "wrote an empty output file",
        }.items():
            self.install(to_dwg=case)
            with self.assertRaises(RuntimeError) as cm:
                importDWG.export(self.shapes(), out)
            self.assertIn("dxf2dwg", str(cm.exception), case)
            self.assertIn(needle, str(cm.exception), case)
            self.assertFalse(os.path.exists(out), case)

    def test_failed_export_keeps_existing_file(self):
        out = os.path.join(self.tmp, "out.dwg")
        with open(out, "wb") as f:
            f.write(b"previous")
        self.install(to_dwg="rc1_partial")
        with self.assertRaises(RuntimeError):
            importDWG.export(self.shapes(), out)
        with open(out, "rb") as f:
            self.assertEqual(f.read(), b"previous")

    def test_message_has_no_spaces_advice(self):
        self.install(to_dxf="rc1_nothing")
        with self.assertRaises(RuntimeError) as cm:
            importDWG.insert(self.dwg, self.doc.Name)
        self.assertNotIn("without spaces", str(cm.exception))

    # ---- check 4: timeout

    def test_timeout_kills_the_converter(self):
        self.install(to_dxf="sleep", DWGConversionTimeout=2)
        t0 = time.time()
        with self.assertRaises(RuntimeError) as cm:
            importDWG.insert(self.dwg, self.doc.Name)
        self.assertLess(time.time() - t0, 4.0)
        self.assertIn("timed out after 2 s", str(cm.exception))
        self.assertIn("dwg2dxf", str(cm.exception))
        self.assertEqual(self.tempEntries(), [])

    def test_timeout_zero_means_none(self):
        self.install(to_dxf="slow_ok", DWGConversionTimeout=0)
        importDWG.insert(self.dwg, self.doc.Name)
        self.assertGreater(len(self.doc.Objects), 0)

    def test_timeout_default_is_300(self):
        self.assertEqual(real_params.get_param("DWGConversionTimeout", ret_default=True), 300)

    # ---- check 5: temporary files

    def test_no_temp_entries_left(self):
        out = os.path.join(self.tmp, "out.dwg")
        self.install(to_dxf="ok", to_dwg="ok")
        for _ in range(4):
            importDWG.insert(self.dwg, self.doc.Name)
            importDWG.open(self.dwg)
        self.install(to_dxf="rc1_nothing", to_dwg="rc1")
        for _ in range(4):
            for call in self.calls():
                with self.assertRaises(RuntimeError):
                    call()
        self.install(to_dxf="ok", to_dwg="ok")
        for _ in range(2):
            self.assertEqual(importDWG.export(self.shapes(), out), out)
            self.assertTrue(os.path.getsize(out) > 0)
        self.assertEqual(self.tempEntries(), [])

    # ---- check 7: export

    def test_export_returns_path_of_written_file(self):
        out = os.path.join(self.tmp, "out.dwg")
        self.install(to_dwg="ok")
        self.assertEqual(importDWG.export(self.shapes(), out), out)
        self.assertTrue(os.path.getsize(out) > 0)

    def test_export_into_read_only_folder_raises(self):
        ro = os.path.join(self.tmp, "ro")
        os.makedirs(ro)
        os.chmod(ro, 0o555)
        try:
            if os.access(ro, os.W_OK):
                self.skipTest("running as a user who can write to read-only folders")
            self.install(to_dwg="ok")
            with self.assertRaises(RuntimeError) as cm:
                importDWG.export(self.shapes(), os.path.join(ro, "out.dwg"))
            self.assertIn("could not be moved", str(cm.exception))
        finally:
            os.chmod(ro, 0o755)

    @unittest.skipUnless(
        shutil.which("dxf2dwg") or os.path.exists("/opt/homebrew/bin/dxf2dwg"),
        "LibreDWG is not installed",
    )
    def test_real_libredwg_export(self):
        """LibreDWG 0.14 cannot read FreeCAD's DXF: the failure names dxf2dwg and leaves no file."""
        self.setParams(TeighaFileConverter="")
        os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
        out = os.path.join(self.tmp, "real.dwg")
        try:
            importDWG.export(self.shapes(), out)
        except RuntimeError as exc:
            self.assertIn("dxf2dwg", str(exc))
            self.assertFalse(os.path.exists(out))
        else:  # a newer LibreDWG that reads the DXF
            self.assertTrue(os.path.getsize(out) > 0)


## @}
