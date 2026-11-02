# SPDX-License-Identifier: LGPL-2.1-or-later
"""View provider of an assembly feature (cad-fork G06 slice 4b). Imported only with the GUI up.

It gives the feature a tree icon by kind and makes a double click reopen the task panel on it. It does
not touch any part's view provider or ``Visibility``.
"""

import os

import FreeCAD as App
import FreeCADGui as Gui

ICONS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")


def icon_for(kind):
    return os.path.join(ICONS, "Assembly_CreateAssemblyHole.svg" if kind == "hole" else "Assembly_CreateAssemblyCut.svg")


def open_editor(feature):
    """Open the panel on ``feature`` unless another dialog is open. Returns True when the panel was opened."""
    asm = feature.getParentGeoFeatureGroup()
    if asm is None or Gui.Control.activeDialog():
        return False
    from AssemblyFeaturePanel import TaskAssemblyFeature

    dialog = Gui.Control.showDialog(TaskAssemblyFeature(asm, feature=feature))
    if dialog is not None:
        dialog.setAutoCloseOnDeletedDocument(True)
        dialog.setDocumentName(asm.Document.Name)
    return True


class ViewProviderAssemblyFeature:
    def __init__(self, vobj):
        vobj.Proxy = self

    def attach(self, vobj):
        self.Object = vobj.Object

    def getIcon(self):
        return icon_for(getattr(self.Object, "Kind", "cut"))

    def doubleClicked(self, vobj):
        open_editor(vobj.Object)
        return True

    def updateData(self, obj, prop):
        return None

    def onChanged(self, vobj, prop):
        return None

    def dumps(self):
        return None

    def loads(self, state):
        return None


def attach(feature):
    """Give a feature object the view provider (a no-op without the GUI or when it already has one)."""
    vobj = getattr(feature, "ViewObject", None)
    if vobj is not None and not isinstance(getattr(vobj, "Proxy", None), ViewProviderAssemblyFeature):
        ViewProviderAssemblyFeature(vobj)
