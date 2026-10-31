# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run every SheetMetal test module: FreeCADCmd -t TestSheetMetal."""

import TestApp  # noqa: F401  (FreeCAD test harness)

from SMTests.testBendCuts import TestBendCuts  # noqa: F401
from SMTests.testBendTable import TestBendTable, TestBendTableUnfold  # noqa: F401
from SMTests.testConvertSolid import TestConvertSolid  # noqa: F401
from SMTests.testCornerRelief import TestCornerRelief  # noqa: F401
from SMTests.testEdgeFlange import TestEdgeFlange  # noqa: F401
from SMTests.testFlatDxf import TestBendLines, TestDefaultUntouched, TestFlatDxf  # noqa: F401
from SMTests.testFolder import TestFolder  # noqa: F401
from SMTests.testFormingTools import (  # noqa: F401
    TestFormedPartUnfold, TestFormedUnfoldRepairs, TestFormingToolLibrary, TestFormingTools,
    TestRepairs027)
from SMTests.testHem import TestHem  # noqa: F401
from SMTests.testJog import TestJog  # noqa: F401
from SMTests.testKfactor import TestKFactor  # noqa: F401
from SMTests.testLighteningCutout import TestCutoutRefusals, TestLighteningCutout  # noqa: F401
from SMTests.testLoftedBend import (  # noqa: F401
    TestLoftedFlat, TestLoftedGoldens, TestLoftedRefusals, TestLoftedVolume)
from SMTests.testLicence import TestSheetMetalLicence  # noqa: F401
from SMTests.testNewUnfolderK import TestGetKFactor, TestRootFaceIndependence  # noqa: F401
from SMTests.testOracle import TestFlatPatternOracle  # noqa: F401
from SMTests.testSketchedBend import TestSketchedBend  # noqa: F401
from SMTests.testUnfoldFeature import (  # noqa: F401
    TestDefaultIsV1, TestFeaturePathEdgeFlangeAndHem, TestSheetValidation, TestUProfileThickness)
