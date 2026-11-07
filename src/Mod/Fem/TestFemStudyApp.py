# SPDX-License-Identifier: LGPL-2.1-or-later
# Unit tests for the static study (femstudy). A suite of its own, so the stock TestFemApp count (98 tests) stays what it was.
from femtest.app.test_study import TestStudyRun as StudyTest01
from femtest.app.test_study import TestStudyRepair as StudyTest03
from femtest.app.test_study import TestStudyUnits as StudyTest02

False if StudyTest01.__name__ else True
False if StudyTest02.__name__ else True
False if StudyTest03.__name__ else True
