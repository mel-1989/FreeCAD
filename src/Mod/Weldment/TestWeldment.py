# SPDX-License-Identifier: LGPL-2.1-or-later
"""Weldment test suite entry point (FreeCAD.__unit_test__ lists "TestWeldment")."""

from WTests.testCutList import TestCutList  # noqa: F401
from WTests.testEndCap import TestEndCap, TestEndCapRefusals  # noqa: F401
from WTests.testGusset import TestGusset, TestGussetRefusals  # noqa: F401
from WTests.testPlateCutList import TestPlateCutList  # noqa: F401
from WTests.testLicence import TestWeldmentLicence  # noqa: F401
from WTests.testMember import TestMember  # noqa: F401
from WTests.testRepair import (  # noqa: F401
    TestBodySeating,
    TestCsvAndMessages,
    TestCurvedOverhang,
    TestGrouping,
    TestHssDocumented,
    TestReversedTwins,
    TestSaddleLength,
    TestSeating,
)
from WTests.testProfiles import *  # noqa: F401,F403
from WTests.testTrim import TestButtFaceBody, TestMitre, TestRefusals  # noqa: F401
