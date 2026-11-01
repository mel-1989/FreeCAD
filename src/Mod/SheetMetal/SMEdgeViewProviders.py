# SPDX-License-Identifier: LGPL-2.1-or-later
"""View providers of the edge flange, hem, convert-from-solid and jog features (cad-fork G07 slice 6b, lane A).

One small proxy class per feature type. ``setEdit`` opens the feature's panel (``SMEdgePanels``) prefilled with
the object's current properties; the panel applies its changes on OK in one transaction and leaves the properties
alone on Cancel. The proxies keep no state, so a saved document restores them from the class name alone; documents
saved before this slice have no proxy at all and open unchanged, only without the panel.

Clean room: written from the FreeCAD Python view provider API and the Weldment view providers of this fork.
"""

import os

import FreeCAD
import FreeCADGui as Gui

import SheetMetalTools


def icon_path(name):
    return os.path.join(SheetMetalTools.icons_path, name + ".svg")


class _Base:
    icon = "SheetMetal_EdgeFlange"
    panel = "edgeflange"

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
        import SMEdgePanels

        obj = vobj.Object
        if Gui.Control.activeDialog():
            FreeCAD.Console.PrintWarning("Close the open task panel before editing %s\n" % obj.Label)
            return False
        SMEdgePanels.show(SMEdgePanels.PANELS[self.panel](doc=obj.Document, obj=obj))
        return True

    def unsetEdit(self, vobj, mode=0):
        if mode != 0:
            return None
        import SMEdgePanels

        panel = SMEdgePanels.current()
        if panel is not None and not panel.done:
            panel.cancelled()
            Gui.Control.closeDialog()
        return True

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


class EdgeFlangeVP(_Base):
    icon, panel = "SheetMetal_EdgeFlange", "edgeflange"


class HemVP(_Base):
    icon, panel = "SheetMetal_Hem", "hem"


class ConvertSolidVP(_Base):
    icon, panel = "SheetMetal_ConvertSolid", "convert"


class JogVP(_Base):
    icon, panel = "SheetMetal_Jog", "jog"


#: object proxy class name -> view provider class
BY_TYPE = {"SMEdgeFlange": EdgeFlangeVP, "SMHem": HemVP, "SMConvertSolid": ConvertSolidVP, "SMJog": JogVP}


def attach(obj):
    """Set the matching view provider proxy on a new feature (only with the GUI up)."""
    cls = BY_TYPE.get(type(obj.Proxy).__name__)
    if cls is not None and obj.ViewObject is not None:
        obj.ViewObject.Proxy = cls()
