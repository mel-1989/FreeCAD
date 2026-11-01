# SPDX-License-Identifier: LGPL-2.1-or-later
"""View providers of the Weldment features (cad-fork G07 slice 6a): icon, and a task panel on double-click.

One small proxy class per feature type. ``setEdit`` opens the feature's panel prefilled with the object's
current properties (``WeldPanels``); the panel applies its changes on OK in one transaction and leaves the
properties alone on Cancel. The proxies keep no state, so a saved document restores them from the class name
alone; documents saved before this slice have no proxy at all (they were saved with ``Proxy = 0``) and open
unchanged, only without the panel.

Clean room: written from the FreeCAD Python view provider API; no weldment workbench source was read.
"""

import os

import FreeCAD
import FreeCADGui as Gui

ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")


def icon_path(name):
    return os.path.join(ICON_DIR, name + ".svg")


class _Base:
    icon = "Weldment_Member"
    panel = "member"

    def __init__(self, vobj=None):
        if vobj is not None:
            vobj.Proxy = self

    def attach(self, vobj):
        return

    def getIcon(self):
        return icon_path(self.icon)

    def doubleClicked(self, vobj):
        Gui.getDocument(vobj.Object.Document.Name).setEdit(vobj.Object.Name, 0)
        return True

    def setEdit(self, vobj, mode=0):
        if mode != 0:
            return None    # other edit modes (transform, cutting) stay with the default provider
        import WeldPanels

        obj = vobj.Object
        if Gui.Control.activeDialog():
            FreeCAD.Console.PrintWarning("Close the open task panel before editing %s\n" % obj.Label)
            return False
        WeldPanels.show(WeldPanels.PANELS[self.panel](doc=obj.Document, obj=obj))
        return True

    def unsetEdit(self, vobj, mode=0):
        if mode != 0:
            return None
        import WeldPanels

        panel = WeldPanels.current()
        if panel is not None and not panel.done:
            panel.cancelled()
            Gui.Control.closeDialog()
        return True

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


class MemberVP(_Base):
    icon, panel = "Weldment_Member", "member"


class EndTrimVP(_Base):
    icon, panel = "Weldment_Mitre", "corner"


class GussetVP(_Base):
    icon, panel = "Weldment_Gusset", "gusset"


class EndCapVP(_Base):
    icon, panel = "Weldment_EndCap", "endcap"


class WeldBeadVP(_Base):
    icon, panel = "Weldment_WeldBead", "bead"


#: object proxy class name -> view provider class
BY_TYPE = {"Member": MemberVP, "EndTrim": EndTrimVP, "Gusset": GussetVP, "EndCap": EndCapVP, "WeldBead": WeldBeadVP}


def attach(obj):
    """Set the matching view provider proxy on a new feature (only with the GUI up)."""
    cls = BY_TYPE.get(type(obj.Proxy).__name__)
    obj.ViewObject.Proxy = cls() if cls else 0
