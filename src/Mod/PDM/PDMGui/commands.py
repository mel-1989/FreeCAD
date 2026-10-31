# SPDX-License-Identifier: LGPL-2.1-or-later
"""PDM commands. Each ``IsActive`` reads only local state (the settings, the active document's path and the
cached lock mirror), so refreshing the toolbar never touches the network or runs a long git command."""

import FreeCADGui as Gui

from . import dialogs, panels, state


def _name():
    return state.active_name()


class _Base:
    text = ""
    tip = ""

    def GetResources(self):
        return {"MenuText": self.text, "ToolTip": self.tip}

    def IsActive(self):
        return state.current_vault() is not None and self.applies()

    def applies(self):
        return _name() is not None


class Connect(_Base):
    text = "Connect to vault"
    tip = "Choose the vault and your working copy"

    def IsActive(self):
        return True

    def Activated(self):
        panels.show_panel("connect")


class Documents(_Base):
    text = "Vault documents"
    tip = "List the documents in the vault; open one or check it out"

    def applies(self):
        return True

    def Activated(self):
        panels.show_panel("documents")


class CheckOut(_Base):
    text = "Check out"
    tip = "Lock the active document for editing (starts the next revision of a Released one)"

    def applies(self):
        n = _name()
        return n is not None and not state.lock_state(n)["mine"]

    def Activated(self):
        n = _name()
        w = panels.show_panel("revisions", n)
        w.do_checkout()


class CheckIn(_Base):
    text = "Check in"
    tip = "Save the active document and check it in as a new revision"

    def applies(self):
        n = _name()
        return n is not None and state.lock_state(n)["mine"]

    def Activated(self):
        panels.show_panel("checkin", _name())


class Revisions(_Base):
    text = "Revisions"
    tip = "Browse the revisions of the active document and open one read-only"

    def Activated(self):
        panels.show_panel("revisions", _name())


class WhereUsed(_Base):
    text = "Where used"
    tip = "List the vault documents that link to the active document"

    def Activated(self):
        panels.show_panel("whereused", _name())


class Lifecycle(_Base):
    text = "Lifecycle"
    tip = "Show the allowed lifecycle transitions of the active document and apply one"

    def Activated(self):
        panels.show_panel("lifecycle", _name())


COMMANDS = {
    "PDM_Connect": Connect(), "PDM_Documents": Documents(), "PDM_CheckOut": CheckOut(), "PDM_CheckIn": CheckIn(),
    "PDM_Revisions": Revisions(), "PDM_WhereUsed": WhereUsed(), "PDM_Lifecycle": Lifecycle(),
}
TOOLBAR = ["PDM_Connect", "PDM_Documents", "PDM_CheckOut", "PDM_CheckIn", "PDM_Revisions", "PDM_WhereUsed",
           "PDM_Lifecycle"]


def register():
    for name, cmd in COMMANDS.items():
        Gui.addCommand(name, cmd)
