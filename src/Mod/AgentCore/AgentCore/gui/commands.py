# SPDX-License-Identifier: LGPL-2.1-or-later
"""GUI commands of the AgentCore review panel."""


class ReviewPending:
    def GetResources(self):
        return {"MenuText": "Review pending agent changes",
                "ToolTip": "Reopen the change-set panel for the newest agent turn that has no decision yet"}

    def IsActive(self):
        from . import panel
        return bool(panel.pending())

    def Activated(self):
        from . import panel
        panel.review_pending()


def register():
    import FreeCADGui
    FreeCADGui.addCommand("AgentCore_ReviewPending", ReviewPending())
