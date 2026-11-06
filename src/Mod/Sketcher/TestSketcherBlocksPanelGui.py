# SPDX-License-Identifier: LGPL-2.1-or-later
from SketcherTests.TestSketcherBlocksPanelGui import (
    TestBlocksPanelActions,
    TestBlocksPanelClick,
    TestBlocksPanelContents,
    TestBlocksPanelCurves,
    TestBlocksPanelEditLeaving,
    TestBlocksPanelEditRepairs,
    TestBlocksPanelEditResults,
    TestBlocksPanelEditUndo,
    TestBlocksPanelLibrary,
    TestBlocksPanelOthers,
    TestBlocksPanelOutsideTheSession,
    TestBlocksPanelReachable,
    TestBlocksPanelSelection,
    TestBlocksPanelSpeed,
)

# Use the module so that code checkers don't complain (flake8)
True if (
    TestBlocksPanelActions
    and TestBlocksPanelClick
    and TestBlocksPanelContents
    and TestBlocksPanelCurves
    and TestBlocksPanelEditLeaving
    and TestBlocksPanelEditRepairs
    and TestBlocksPanelEditResults
    and TestBlocksPanelEditUndo
    and TestBlocksPanelLibrary
    and TestBlocksPanelOthers
    and TestBlocksPanelOutsideTheSession
    and TestBlocksPanelReachable
    and TestBlocksPanelSelection
    and TestBlocksPanelSpeed
) else False
