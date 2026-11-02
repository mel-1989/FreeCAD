# SPDX-License-Identifier: LGPL-2.1-or-later
"""View provider of an assembly configuration (cad-fork G06 slice 5b). Imported only with the GUI up.

It gives the configuration a tree icon, makes a double click open the Configurations panel, and guards the
tree's Delete command: deleting the active configuration while an overridden property was edited (drift)
would throw those edits away, so the user is asked first. The guard covers the GUI delete path only (the
tree command calls ``onDelete`` before it removes the object); ``doc.removeObject`` from a script does not
go through a view provider, and there ``AssemblyConfiguration.unsetupObject`` restores the defaults and
prints a warning as before. ``AssemblyConfiguration.delete`` refuses to delete an active configuration.
"""

import os

import FreeCAD as App
import FreeCADGui as Gui

ICONS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")


def ask_discard_and_delete(cfg, drift):
    """Ask whether to delete the active configuration and discard the edits made while it was active.

    Replaceable: tests set ``ViewProviderAssemblyConfiguration.ask_discard_and_delete`` to a function with the
    same signature, so no modal dialog opens in an unattended run. Returns True to delete."""
    from PySide import QtWidgets

    items = "\n".join("%s: %s" % (d["target"], d["property"]) for d in drift)
    box = QtWidgets.QMessageBox(QtWidgets.QApplication.activeWindow())
    box.setIcon(QtWidgets.QMessageBox.Warning)
    box.setWindowTitle(App.Qt.translate("Assembly", "Delete active configuration"))
    box.setText(App.Qt.translate(
        "Assembly", "The active configuration \"%s\" has edits that were not captured. Deleting it throws them away."
    ) % cfg.Label)
    box.setInformativeText(App.Qt.translate(
        "Assembly", "Cancel and use Capture or Discard in the Configurations panel to keep or drop them on purpose.\n\n%s"
    ) % items)
    delete = box.addButton(App.Qt.translate("Assembly", "Discard and delete"), QtWidgets.QMessageBox.DestructiveRole)
    box.addButton(QtWidgets.QMessageBox.Cancel)
    box.setDefaultButton(QtWidgets.QMessageBox.Cancel)
    box.exec_()
    return box.clickedButton() is delete


def open_editor(cfg):
    """Open the Configurations panel unless another dialog is open. Returns True when it was opened."""
    asm = cfg.getParentGeoFeatureGroup()
    if asm is None or Gui.Control.activeDialog():
        return False
    from CommandConfigurations import open_panel

    open_panel(asm)
    return True


class ViewProviderAssemblyConfiguration:
    def __init__(self, vobj):
        vobj.Proxy = self

    def attach(self, vobj):
        self.Object = vobj.Object

    def getIcon(self):
        return os.path.join(ICONS, "Assembly_Configurations.svg")

    def doubleClicked(self, vobj):
        open_editor(vobj.Object)
        return True

    def onDelete(self, vobj, subelements):
        """False refuses the delete. Only the active configuration with drift is guarded."""
        import AssemblyConfiguration as AC

        cfg = vobj.Object
        try:
            asm = cfg.getParentGeoFeatureGroup()
            drift = AC._drift(asm, cfg) if asm is not None and cfg.Active else []
        except Exception:
            drift = []
        if not drift:
            return True
        return bool(ask_discard_and_delete(cfg, drift))

    def updateData(self, obj, prop):
        return None

    def onChanged(self, vobj, prop):
        return None

    def dumps(self):
        return None

    def loads(self, state):
        return None


def attach(cfg):
    """Give a configuration object the view provider (a no-op without the GUI or when it already has one)."""
    vobj = getattr(cfg, "ViewObject", None)
    if vobj is not None and not isinstance(getattr(vobj, "Proxy", None), ViewProviderAssemblyConfiguration):
        ViewProviderAssemblyConfiguration(vobj)
