# SPDX-License-Identifier: LGPL-2.1-or-later
"""View providers of the corner relief, lightening cutout and lofted bend features (cad-fork G07 slice 6b).

One small proxy class per feature type. ``setEdit`` opens the feature's task panel prefilled with the object's
current properties (``SMShapePanels``); the panel applies its changes on OK in one transaction and leaves the
properties alone on Cancel. The proxies keep no state, so a saved document restores them from the class name
alone; documents saved before this slice have no proxy (the features were created headlessly or with the plain
view provider) and open unchanged, only without the panel.

Clean room: written from the FreeCAD Python view provider API and the Weldment view providers of this fork; the
implementer never opened the add-on's GPL files.
"""

import os

import FreeCAD
import FreeCADGui as Gui

ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Resources", "icons")


def icon_path(name):
    return os.path.join(ICON_DIR, name + ".svg")


class _Base:
    icon = "SheetMetal_CornerRelief"
    panel = "relief"

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
            return None    # other edit modes stay with the default provider
        import SMShapePanels

        obj = vobj.Object
        if Gui.Control.activeDialog():
            FreeCAD.Console.PrintWarning("Close the open task panel before editing %s\n" % obj.Label)
            return False
        SMShapePanels.show(SMShapePanels.PANELS[self.panel](doc=obj.Document, obj=obj))
        return True

    def unsetEdit(self, vobj, mode=0):
        if mode != 0:
            return None
        import SMShapePanels

        panel = SMShapePanels.current()
        if panel is not None and not panel.done:
            panel.cancelled()
            Gui.Control.closeDialog()
        return True

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


class CornerReliefVP(_Base):
    icon, panel = "SheetMetal_CornerRelief", "relief"


class LighteningCutoutVP(_Base):
    icon, panel = "SheetMetal_LighteningCutout", "cutout"


class LoftedBendVP(_Base):
    icon, panel = "SheetMetal_LoftedBend", "lofted"


#: object proxy class name -> view provider class
BY_TYPE = {"SMBendCornerRelief": CornerReliefVP, "SMLighteningCutout": LighteningCutoutVP,
           "SMLoftedBend": LoftedBendVP}


def attach(obj):
    """Set the matching view provider proxy on a new feature (only with the GUI up)."""
    cls = BY_TYPE.get(type(obj.Proxy).__name__)
    if cls is not None and obj.ViewObject is not None and not isinstance(obj.ViewObject.Proxy, cls):
        obj.ViewObject.Proxy = cls()
