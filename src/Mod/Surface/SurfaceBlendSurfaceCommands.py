# SPDX-License-Identifier: LGPL-2.1-or-later

"""Surface workbench command ``Surface_BlendSurface``: a G0, G1 or G2 blend between two face edges.

The task panel has two fields, Edge 1 and Edge 2, each with "Set from selection" (one edge of the current
selection, by the path rule of the analysis commands, plus a face of the same object when one is selected
too), a continuity choice (G0, G1, G2) and a size per side, and a status line. OK with a valid input
creates one ``BlendSurface`` feature in one undo transaction named "Blend surface". OK with a failing
input leaves the panel open, creates nothing and puts the failure message (it starts with the status
name) in the status line. Cancel creates nothing and opens no transaction.

Sources (clean room): FreeCAD's own Python task panels and commands, the Qt documentation, and
L. Piegl and W. Tiller, *The NURBS Book*, 2nd ed., 1997 (Hermite form of Bezier curves).
"""

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import SurfaceBlendSurface as SBS
from SurfaceAnalysisCommands import selected_subshapes
from SurfaceGordonCommands import _Relay

__all__ = ["BlendPanel", "ViewProviderBlend"]

TRANSACTION = "Blend surface"
FIELDS = (("Edge1", "Edge 1"), ("Edge2", "Edge 2"))


def _tr(text):
    return QtCore.QCoreApplication.translate("Surface_BlendSurface", text)


class ViewProviderBlend:
    """View provider proxy of the blend surface feature; the document opens without it."""

    def __init__(self, vobj):
        vobj.Proxy = self
        self.Object = vobj.Object

    def attach(self, vobj):
        self.Object = vobj.Object

    def getIcon(self):
        return ":/icons/Surface_BlendSurface.svg"

    def updateData(self, obj, prop):
        return

    def onChanged(self, vobj, prop):
        return

    def dumps(self):
        return None

    def loads(self, state):
        return None


def selected_edge_with_face():
    """``(object, [subnames])`` for the one selected edge, with a selected face of the same object when
    there is one; ``None`` unless exactly one edge is selected."""
    edges = selected_subshapes(("Edge",))
    if len(edges) != 1:
        return None
    edge = edges[0]
    subs = [edge["subname"]]
    for face in selected_subshapes(("Face",)):
        if face["object"] is edge["object"]:
            subs.insert(0, face["subname"])
            break
    return edge["object"], subs


class BlendPanel:
    """Task panel collecting the two edges, continuities and sizes of a blend surface."""

    def __init__(self, document=None):
        self.document = document
        self.entries = {key: None for key, _ in FIELDS}
        self.feature = None
        form = QtWidgets.QWidget()
        form.setObjectName("G09_BlendPanel")
        form.setWindowTitle(_tr("Blend surface"))
        self.form = form
        layout = QtWidgets.QVBoxLayout(form)
        self._relays = []
        self.fields, self.continuity, self.size = {}, {}, {}
        for key, title in FIELDS:
            label = QtWidgets.QLabel(_tr(title + ":"), form)
            label.setObjectName("G09_Blend_%s_Label" % key)
            box = QtWidgets.QLineEdit(form)
            box.setObjectName("G09_Blend_%s" % key)
            box.setReadOnly(True)
            label.setBuddy(box)
            button = QtWidgets.QPushButton(_tr("Set from selection"), form)
            button.setObjectName("G09_Blend_%s_Set" % key)
            relay = _Relay(lambda k=key: self.set_from_selection(k), form)
            self._relays.append(relay)
            button.clicked.connect(relay.fired)
            layout.addWidget(label)
            row = QtWidgets.QHBoxLayout()
            row.addWidget(box)
            row.addWidget(button)
            layout.addLayout(row)
            combo_label = QtWidgets.QLabel(_tr("Continuity %s:") % title[-1], form)
            combo_label.setObjectName("G09_Blend_%s_Continuity_Label" % key)
            combo = QtWidgets.QComboBox(form)
            combo.setObjectName("G09_Blend_%s_Continuity" % key)
            combo.addItems(list(SBS._CONTINUITY))
            combo.setCurrentText("G2")
            combo_label.setBuddy(combo)
            size_label = QtWidgets.QLabel(_tr("Size %s:") % title[-1], form)
            size_label.setObjectName("G09_Blend_%s_Size_Label" % key)
            size = QtWidgets.QDoubleSpinBox(form)
            size.setObjectName("G09_Blend_%s_Size" % key)
            size.setDecimals(3)
            size.setRange(0.001, 100.0)
            size.setSingleStep(0.1)
            size.setSuffix(_tr(" \u00d7 chord"))
            size.setValue(1.0)
            size_label.setBuddy(size)
            row2 = QtWidgets.QHBoxLayout()
            for w in (combo_label, combo, size_label, size):
                row2.addWidget(w)
            layout.addLayout(row2)
            self.fields[key], self.continuity[key], self.size[key] = box, combo, size
        self.status = QtWidgets.QLabel(form)
        self.status.setObjectName("G09_BlendStatus")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self._refresh()

    @staticmethod
    def _text(entry):
        obj, subs = entry
        return "%s.%s" % (obj.Label, subs[-1]) if subs else obj.Label

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
        picked = selected_edge_with_face()
        if picked is None:
            self.say(_tr("InvalidInput: select exactly one edge for %s") % key)
            return
        self.set_entry(key, picked)

    def _refresh(self):
        done = sum(1 for e in self.entries.values() if e is not None)
        self.say(_tr("%d of 2 edges set") % done)

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
        c1, c2 = (self.continuity[k].currentText() for k, _ in FIELDS)
        s1, s2 = (self.size[k].value() for k, _ in FIELDS)
        try:
            (f1, e1), (f2, e2) = (SBS.resolve_edge_face(self.entries[k]) for k, _ in FIELDS)
            # the blend is tried before anything is created, so a failure leaves no transaction
            SBS.makeBlendSurface(
                f1, e1, f2, e2, SBS._CONTINUITY.index(c1), SBS._CONTINUITY.index(c2), size1=s1, size2=s2
            )
        except Exception as e:
            self.say(str(e) or e.__class__.__name__)
            return False
        doc.openTransaction(TRANSACTION)
        try:
            feature = SBS.makeBlendSurfaceFeature(doc, self.entries["Edge1"], self.entries["Edge2"])
            feature.Continuity1, feature.Continuity2 = c1, c2
            feature.Size1, feature.Size2 = s1, s2
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


class _BlendCommand:
    def GetResources(self):
        return {
            "Pixmap": "Surface_BlendSurface",
            "MenuText": QtCore.QT_TRANSLATE_NOOP("Surface_BlendSurface", "Blend surface"),
            "ToolTip": QtCore.QT_TRANSLATE_NOOP(
                "Surface_BlendSurface",
                "Bridge the gap between an edge of one face and an edge of another with a surface that meets each face with G0, G1 or G2 continuity",
            ),
            "StatusTip": QtCore.QT_TRANSLATE_NOOP("Surface_BlendSurface", "Create a blend surface between two face edges"),
        }

    def IsActive(self):
        return Gui.ActiveDocument is not None and not Gui.Control.activeDialog()

    def Activated(self):
        Gui.Control.showDialog(BlendPanel())


COMMANDS = (("Surface_BlendSurface", _BlendCommand),)


def register():
    for name, cls in COMMANDS:
        Gui.addCommand(name, cls())


register()
