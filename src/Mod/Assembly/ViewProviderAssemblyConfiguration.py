# SPDX-License-Identifier: LGPL-2.1-or-later
"""View provider of an assembly configuration (cad-fork G06 slice 5b). Imported only with the GUI up.

It gives the configuration a tree icon, makes a double click open the Configurations panel, and guards the
tree's Delete command: deleting the active configuration while an overridden property was edited (drift)
would throw those edits away, so the user is asked first. The guard covers the GUI delete path only (the
tree command calls ``onDelete`` before it removes the object); ``doc.removeObject`` from a script does not
go through a view provider, and there ``AssemblyConfiguration.unsetupObject`` restores the defaults and
prints a warning as before. ``AssemblyConfiguration.delete`` refuses to delete an active configuration.

Several configurations selected for one Delete are one decision: when the selection holds the active
configuration with drift, the user is asked once for the whole set, and a refusal keeps every selected
configuration, not only the active one (the tree command asks each object in turn, so the answer is kept in
``_batch`` until each selected configuration has been asked about).
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


_batch = {}     # configuration Name -> the answer of the one question asked for the selection being deleted


def _selected_configurations(cfg):
    """The configurations of cfg's document that are selected right now (cfg itself included)."""
    import AssemblyConfiguration as AC

    out = {cfg.Name: cfg}
    try:
        for o in Gui.Selection.getSelection(cfg.Document.Name):
            if AC._is_config(o):
                out[o.Name] = o
    except Exception:
        pass
    return out


def _clear_batch():
    _batch.clear()


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
        if cfg.Name in _batch:                      # decided with the rest of the selection
            return _batch.pop(cfg.Name)
        selected = _selected_configurations(cfg)
        guarded = None
        for c in selected.values():
            try:
                asm = c.getParentGeoFeatureGroup()
                drift = AC._drift(asm, c) if asm is not None and c.Active else []
            except Exception:
                drift = []
            if drift:
                guarded = (c, drift)
                break
        if guarded is None:
            return True
        answer = bool(ask_discard_and_delete(*guarded))
        for name in selected:
            if name != cfg.Name:
                _batch[name] = answer
        if _batch:
            from PySide import QtCore

            QtCore.QTimer.singleShot(0, _clear_batch)   # a stale answer must not outlive the command
        return answer

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
