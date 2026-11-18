# SPDX-License-Identifier: LGPL-2.1-or-later

"""Surface workbench command ``Surface_Gordon``: a Gordon surface from a curve network.

The task panel has a Profiles list and a Guides list, each with "Add selected" (the edges of the
current selection, resolved as the analysis commands resolve them) and "Remove", and a status line.
OK with a valid network creates one ``SurfaceGordon`` feature in one undo transaction named
"Gordon surface". OK with a failing network leaves the panel open, creates nothing and puts the
failure message (it starts with the status name) in the status line. Cancel creates nothing and
opens no transaction.

Sources (clean room): FreeCAD's own Python task panels and commands, the Qt documentation, and
W. J. Gordon, "Spline-blended surface interpolation through curve networks", J. Math. Mech.
18(10), 1969.
"""

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import SurfaceGordon as SGordon
from SurfaceAnalysisCommands import selected_subshapes

__all__ = ["GordonPanel", "ViewProviderGordon", "selected_edge_entries"]

TRANSACTION = "Gordon surface"


def _tr(text):
    return QtCore.QCoreApplication.translate("Surface_Gordon", text)


class ViewProviderGordon:
    """View provider proxy of the Gordon surface feature; the document opens without it."""

    def __init__(self, vobj):
        vobj.Proxy = self
        self.Object = vobj.Object

    def attach(self, vobj):
        self.Object = vobj.Object

    def getIcon(self):
        return ":/icons/Surface_Gordon.svg"

    def updateData(self, obj, prop):
        return

    def onChanged(self, vobj, prop):
        return

    def dumps(self):
        return None

    def loads(self, state):
        return None


# ------------------------------------------------------------------ selection


def selected_edge_entries():
    """The edges of the current selection as ``(object, subname)`` pairs, each by the path rule of
    the analysis commands (the placements of every container on the path apply when the entry is
    resolved). A selected object with no sub-name counts when its shape is one edge or one wire."""
    out = []
    for item in selected_subshapes(("Edge",)):
        out.append((item["object"], item["subname"]))
    try:
        selection = Gui.Selection.getSelectionEx("", 0)
    except Exception:
        selection = []
    for sel in selection:
        if [n for n in sel.SubElementNames if n]:
            continue
        shape = getattr(sel.Object, "Shape", None)
        if isinstance(shape, Part.Shape) and not shape.isNull() and shape.ShapeType in ("Edge", "Wire"):
            out.append((sel.Object, ""))
    return out


# ----------------------------------------------------------------------- panel


class _Relay(QtCore.QObject):
    """Receiver of the panel's button signals (a real QObject slot, as the analysis panel does)."""

    def __init__(self, callback, parent):
        super().__init__(parent)
        self._callback = callback

    @QtCore.Slot()
    def fired(self):
        self._callback()


class GordonPanel:
    """Task panel collecting the profile and guide curves of a Gordon surface."""

    def __init__(self, document=None):
        self.document = document
        self.profiles = []
        self.guides = []
        self._deleted = 0  # inputs found deleted since the last OK
        self.feature = None
        form = QtWidgets.QWidget()
        form.setObjectName("G09_GordonPanel")
        form.setWindowTitle(_tr("Gordon surface"))
        self.form = form
        layout = QtWidgets.QVBoxLayout(form)
        self._relays = []
        self.profile_list = self._section(layout, "Profiles", "G09_GordonProfiles", self.add_profiles, self.remove_profiles)
        self.guide_list = self._section(layout, "Guides", "G09_GordonGuides", self.add_guides, self.remove_guides)
        self.status = QtWidgets.QLabel(form)
        self.status.setObjectName("G09_GordonStatus")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self._refresh()

    def _section(self, layout, title, name, add, remove):
        form = self.form
        label = QtWidgets.QLabel(_tr(title + ":"), form)
        label.setObjectName(name + "_Label")
        box = QtWidgets.QListWidget(form)
        box.setObjectName(name)
        box.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        label.setBuddy(box)
        layout.addWidget(label)
        layout.addWidget(box)
        row = QtWidgets.QHBoxLayout()
        for text, suffix, slot in (("Add selected", "Add", add), ("Remove", "Remove", remove)):
            button = QtWidgets.QPushButton(_tr(text), form)
            button.setObjectName(name + "_" + suffix)
            relay = _Relay(slot, form)
            self._relays.append(relay)
            button.clicked.connect(relay.fired)
            row.addWidget(button)
            setattr(self, "%s_%s_button" % (name.replace("G09_Gordon", "").lower(), suffix.lower()), button)
        layout.addLayout(row)
        return box

    # ---- list handling
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

    def _prune(self):
        """Drop the entries whose object was deleted; returns how many went."""
        gone = 0
        for entries, widget in ((self.profiles, self.profile_list), (self.guides, self.guide_list)):
            for row in reversed(range(len(entries))):
                if not self._alive(entries[row]):
                    del entries[row]
                    widget.takeItem(row)
                    gone += 1
        self._deleted += gone
        return gone

    def _same(self, a, b):
        return a[0] is b[0] and a[1] == b[1] or (a[0].Name == b[0].Name and a[1] == b[1])

    def _add(self, entries, listwidget, selected=None):
        self._prune()
        for entry in selected if selected is not None else selected_edge_entries():
            if any(self._same(entry, e) for e in entries):
                continue
            entries.append(entry)
            listwidget.addItem(self._text(entry))
        self._refresh()

    def add_profiles(self):
        self._add(self.profiles, self.profile_list)

    def add_guides(self):
        self._add(self.guides, self.guide_list)

    def _remove(self, entries, listwidget):
        rows = sorted({listwidget.row(i) for i in listwidget.selectedItems()}, reverse=True)
        for row in rows:
            listwidget.takeItem(row)
            del entries[row]
        self._refresh()

    def remove_profiles(self):
        self._remove(self.profiles, self.profile_list)

    def remove_guides(self):
        self._remove(self.guides, self.guide_list)

    def _refresh(self):
        self.say(_tr("%d profile(s), %d guide(s)") % (len(self.profiles), len(self.guides)))

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
        self._prune()
        gone, self._deleted = self._deleted, 0
        if gone:
            self.say(_tr("InputDeleted: %d listed input(s) were deleted and are removed from the lists") % gone)
            return False
        try:
            # the network is tried before anything is created, so a failure leaves no transaction
            SGordon.build_gordon(self.profiles, self.guides)
        except Exception as e:  # ValueError from the checks and from OCCT's status, ReferenceError for deleted objects
            self.say(str(e) or e.__class__.__name__)
            return False
        doc.openTransaction(TRANSACTION)
        try:
            feature = SGordon.makeGordonFeature(doc, self.profiles, self.guides)
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


# -------------------------------------------------------------------- command


class _GordonCommand:
    def GetResources(self):
        return {
            "Pixmap": "Surface_Gordon",
            "MenuText": QtCore.QT_TRANSLATE_NOOP("Surface_Gordon", "Gordon surface"),
            "ToolTip": QtCore.QT_TRANSLATE_NOOP(
                "Surface_Gordon",
                "Create one B-spline surface through a network of profile and guide curves that cross each other",
            ),
            "StatusTip": QtCore.QT_TRANSLATE_NOOP(
                "Surface_Gordon", "Create a Gordon surface from profile and guide curves"
            ),
        }

    def IsActive(self):
        return Gui.ActiveDocument is not None and not Gui.Control.activeDialog()

    def Activated(self):
        panel = GordonPanel()
        # edges already selected when the command starts are not added: the user adds them to the
        # list they belong to
        Gui.Control.showDialog(panel)


COMMANDS = (("Surface_Gordon", _GordonCommand),)


def register():
    for name, cls in COMMANDS:
        Gui.addCommand(name, cls())


register()
