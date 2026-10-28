# SPDX-License-Identifier: LGPL-2.1-or-later
"""F4: export_file never overwrites the session's own source file, however the target is spelled."""

import hashlib
import os
import shutil
import tempfile
import unittest

from AgentCoreTests.fixtures.mcp import mcp_common as mc
from AgentCore.mcp import session as sess
from AgentCore.mcp.bridge import BridgeError


def _sha(p):
    with open(p, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class TestExportNeverOverwritesSource(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="acexp-")
        self.src = mc.save_fixtures(["box"], self.d)["box"]
        os.environ[sess.DIFF_MODULE_ENV] = ""
        self.s = sess.Session()
        self.s.m_open(path=self.src)
        self.s.doc.getObject("Box").Length = 123
        self.s.doc.recompute()
        self.before = _sha(self.src)

    def tearDown(self):
        self.s.m_close()
        os.environ.pop(sess.DIFF_MODULE_ENV, None)
        shutil.rmtree(self.d, ignore_errors=True)

    def _refused(self, target):
        with self.assertRaises(BridgeError) as cm:
            self.s.m_export_file(target, overwrite=True)
        self.assertEqual(cm.exception.code, "refused_target")
        self.assertEqual(_sha(self.src), self.before, "source file was modified")

    def test_hardlink_to_source(self):
        hl = os.path.join(self.d, "hardlink.FCStd")
        os.link(self.src, hl)
        self._refused(hl)

    def test_symlink_to_source(self):
        sl = os.path.join(self.d, "sl.FCStd")
        os.symlink(self.src, sl)
        self._refused(sl)

    def test_case_variant_of_source(self):
        variant = os.path.join(os.path.dirname(self.src), os.path.basename(self.src).swapcase())
        if not os.path.exists(variant):
            self.skipTest("case-sensitive filesystem: the variant is a different file")
        self._refused(variant)

    def test_unrelated_target_still_exports(self):
        r = self.s.m_export_file(os.path.join(self.d, "other.FCStd"))
        self.assertTrue(os.path.exists(os.path.join(self.d, "other.FCStd")), r)
        self.assertEqual(_sha(self.src), self.before)


if __name__ == "__main__":
    unittest.main()
