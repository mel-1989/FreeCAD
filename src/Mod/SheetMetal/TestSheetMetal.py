# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run every SheetMetal test module: FreeCADCmd -t TestSheetMetal."""

import TestApp  # noqa: F401  (FreeCAD test harness)

from SMTests.testBendCuts import TestBendCuts  # noqa: F401
from SMTests.testBendTable import TestBendTable, TestBendTableUnfold  # noqa: F401
from SMTests.testConvertSolid import TestConvertSolid  # noqa: F401
from SMTests.testEdgeFlange import TestEdgeFlange  # noqa: F401
from SMTests.testFolder import TestFolder  # noqa: F401
from SMTests.testHem import TestHem  # noqa: F401
from SMTests.testKfactor import TestKFactor  # noqa: F401
from SMTests.testLicence import TestSheetMetalLicence  # noqa: F401
from SMTests.testNewUnfolderK import TestGetKFactor, TestRootFaceIndependence  # noqa: F401
from SMTests.testOracle import TestFlatPatternOracle  # noqa: F401
from SMTests.testUnfoldFeature import (  # noqa: F401
    TestFeaturePathEdgeFlangeAndHem, TestSheetValidation, TestUProfileThickness)
