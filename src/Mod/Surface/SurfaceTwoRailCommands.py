# SPDX-License-Identifier: LGPL-2.1-or-later

"""Surface workbench command ``Surface_TwoRailSweep``: sweep a profile along two rails.

The task panel has three fields, Profile, Rail 1 and Rail 2, each with "Set from selection" (one edge
of the current selection, resolved by the path rule of the analysis commands), a Scaling choice and a
status line. OK with a valid input creates one ``SurfaceTwoRail`` feature in one undo transaction
named "Two-rail sweep". OK with a failing input leaves the panel open, creates nothing and puts the
failure message (it starts with the status name) in the status line. Cancel creates nothing and opens
no transaction.

Sources (clean room): FreeCAD's own Python task panels and commands, the Qt documentation, and
L. Piegl and W. Tiller, *The NURBS Book*, 2nd ed., 1997 (swept surfaces).
"""

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import SurfaceTwoRail as STR
from SurfaceGordonCommands import _Relay, selected_edge_entries

__all__ = ["TwoRailPanel", "ViewProviderTwoRail"]

TRANSACTION = "Two-rail sweep"
FIELDS = (("Profile", "Profile"), ("Rail1", "Rail 1"), ("Rail2", "Rail 2"))


def _tr(text):
    return QtCore.QCoreApplication.translate("Surface_TwoRailSweep", text)


class ViewProviderTwoRail:
    """View provider proxy of the two-rail sweep feature; the document opens without it."""

    def __init__(self, vobj):
        vobj.Proxy = self
        self.Object = vobj.Object

    def attach(self, vobj):
        self.Object = vobj.Object

    def getIcon(self):
        return ":/icons/Surface_TwoRailSweep.svg"

    def updateData(self, obj, prop):
        return

    def onChanged(self, vobj, prop):
        return

    def dumps(self):
        return None

    def loads(self, state):
        return None


class TwoRailPanel:
    """Task panel collecting the profile and the two rails of a two-rail sweep."""

    def __init__(self, document=None):
        self.document = document
        self.entries = {key: None for key, _ in FIELDS}
        self.feature = None
        form = QtWidgets.QWidget()
        form.setObjectName("G09_TwoRailPanel")
        form.setWindowTitle(_tr("Two-rail sweep"))
        self.form = form
        layout = QtWidgets.QVBoxLayout(form)
        self._relays = []
        self.fields = {}
        for key, title in FIELDS:
            label = QtWidgets.QLabel(_tr(title + ":"), form)
            label.setObjectName("G09_TwoRail_%s_Label" % key)
            box = QtWidgets.QLineEdit(form)
            box.setObjectName("G09_TwoRail_%s" % key)
            box.setReadOnly(True)
            label.setBuddy(box)
            button = QtWidgets.QPushButton(_tr("Set from selection"), form)
            button.setObjectName("G09_TwoRail_%s_Set" % key)
            relay = _Relay(lambda k=key: self.set_from_selection(k), form)
            self._relays.append(relay)
            button.clicked.connect(relay.fired)
            layout.addWidget(label)
            row = QtWidgets.QHBoxLayout()
            row.addWidget(box)
            row.addWidget(button)
            layout.addLayout(row)
            self.fields[key] = box
            setattr(self, "%s_button" % key.lower(), button)
        scale_label = QtWidgets.QLabel(_tr("Scaling:"), form)
        scale_label.setObjectName("G09_TwoRail_Scaling_Label")
        self.scaling = QtWidgets.QComboBox(form)
        self.scaling.setObjectName("G09_TwoRail_Scaling")
        self.scaling.addItems(["Uniform", "Chord"])
        scale_label.setBuddy(self.scaling)
        layout.addWidget(scale_label)
        layout.addWidget(self.scaling)
        self.status = QtWidgets.QLabel(form)
        self.status.setObjectName("G09_TwoRailStatus")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self._refresh()

    @staticmethod
    def _text(entry):
        obj, sub = entry
        return "%s.%s" % (obj.Label, sub) if sub else obj.Label

    @staticmethod
    def _alive(entry):
        try:
            return entry[0].Document is not None and entry[0].Document.getObject(entry[0].Name) is not None
        except Exception:  # ReferenceError: the object was deleted
            return False

    def set_entry(self, key, entry):
        self.entries[key] = entry
        self.fields[key].setText(self._text(entry))
        self._refresh()

    def set_from_selection(self, key):
        picked = selected_edge_entries()
        if len(picked) != 1:
            self.say(_tr("InvalidInput: select exactly one edge for %s") % key)
            return
        self.set_entry(key, picked[0])

    def _refresh(self):
        done = sum(1 for e in self.entries.values() if e is not None)
        self.say(_tr("%d of 3 curves set") % done)

    def say(self, text):
        self.status.setText(text)

    # ---- task dialog protocol
    def getStandardButtons(self):
        return QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel

    def isAllowedAlterDocument(self):
        return True

    def accept(self):
        doc = self.document
        if doc is None:
            doc = Gui.ActiveDocument.Document if Gui.ActiveDocument is not None else App.ActiveDocument
        if doc is None:
            self.say(_tr("InvalidInput: no document is open"))
            return False
        for key, _title in FIELDS:
            entry = self.entries[key]
            if entry is None:
                self.say(_tr("InvalidInput: %s is not set") % key)
                return False
            if not self._alive(entry):
                self.say(_tr("InputDeleted: the object listed for %s was deleted") % key)
                return False
        try:
            curves = [STR.SG.resolve_curve(*self.entries[key]) for key, _ in FIELDS]
            # the sweep is tried before anything is created, so a failure leaves no transaction
            STR.makeTwoRailSweep(curves[0], curves[1], curves[2], scaling=self.scaling.currentText())
        except Exception as e:
            self.say(str(e) or e.__class__.__name__)
            return False
        doc.openTransaction(TRANSACTION)
        try:
            feature = STR.makeTwoRailFeature(doc, self.entries["Profile"], self.entries["Rail1"], self.entries["Rail2"])
            feature.Scaling = self.scaling.currentText()
            doc.recompute()
            if not feature.isValid():
                raise ValueError("ConstructionFailed: the feature did not recompute")
        except Exception as e:
            doc.abortTransaction()
            self.say(str(e) or e.__class__.__name__)
            return False
        doc.commitTransaction()
        self.feature = feature
        return True

    def reject(self):
        return True


class _TwoRailCommand:
    def GetResources(self):
        return {
            "Pixmap": "Surface_TwoRailSweep",
            "MenuText": QtCore.QT_TRANSLATE_NOOP("Surface_TwoRailSweep", "Two-rail sweep"),
            "ToolTip": QtCore.QT_TRANSLATE_NOOP(
                "Surface_TwoRailSweep",
                "Sweep a profile curve along two rail curves; the profile turns and scales with the chord between the rails",
            ),
            "StatusTip": QtCore.QT_TRANSLATE_NOOP("Surface_TwoRailSweep", "Create a surface from a profile and two rails"),
        }

    def IsActive(self):
        return Gui.ActiveDocument is not None and not Gui.Control.activeDialog()

    def Activated(self):
        Gui.Control.showDialog(TwoRailPanel())


COMMANDS = (("Surface_TwoRailSweep", _TwoRailCommand),)


def register():
    for name, cls in COMMANDS:
        Gui.addCommand(name, cls())


register()
