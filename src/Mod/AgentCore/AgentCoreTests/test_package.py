# SPDX-License-Identifier: LGPL-2.1-or-later
import unittest

import AgentCore


class TestPackage(unittest.TestCase):
    def test_version(self):
        self.assertRegex(AgentCore.__version__, r"^\d+\.\d+\.\d+$")
