# SPDX-License-Identifier: LGPL-2.1-or-later
from SketcherTests.TestSketcherBlocksPanelGui import (
    TestBlocksPanelActions,
    TestBlocksPanelClick,
    TestBlocksPanelContents,
    TestBlocksPanelEditLeaving,
    TestBlocksPanelEditResults,
    TestBlocksPanelEditUndo,
    TestBlocksPanelOthers,
    TestBlocksPanelReachable,
    TestBlocksPanelSelection,
    TestBlocksPanelSpeed,
)

# Use the module so that code checkers don't complain (flake8)
True if (
    TestBlocksPanelActions
    and TestBlocksPanelClick
    and TestBlocksPanelContents
    and TestBlocksPanelEditLeaving
    and TestBlocksPanelEditResults
    and TestBlocksPanelEditUndo
    and TestBlocksPanelOthers
    and TestBlocksPanelReachable
    and TestBlocksPanelSelection
    and TestBlocksPanelSpeed
) else False
