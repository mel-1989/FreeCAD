# SPDX-License-Identifier: LGPL-2.1-or-later
"""Fully Define dialog of the sketch editor (cad-fork G10 slice 4b).  Imported only with the GUI up.

The dialog shows what ``SketcherAutoDefine.propose`` says about the sketch that is open in the editor: the candidate
constraint sets that make it fully defined (each with its scheme, its relations and dimensions with their values, and
the facts measured on a fresh copy), and the schemes that could not close it.  Choosing a candidate lists what it would
add, before anything is applied; Apply runs ``apply_candidate`` in one named transaction and says what was added and
which re-solve gate ran.

It is a non-modal dock widget of the main window, like the diagnosis panel (``SketcherDiagnosisPanel``), whose
machinery it shares: watching the document and the editor, going quiet while another document is in view, never
applying a result that was computed for an earlier state of the sketch, giving the interface its turns while the long
computation runs on the main thread, and putting the user's selection back.  The behaviour follows the public
description of the feature already cited in ``SketcherAutoDefine`` (relations first, then dimensions chosen by
intent); the layout is the plain tree-and-buttons form of FreeCAD's other task panels, and no layout or text comes
from another product's documentation.
"""

import math

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import SketcherAutoDefine as AD
import SketcherDiagnosisPanel as DP
from SketcherDiagnosisPanel import tr

DOCK_NAME = "SketcherFullyDefinePanel"
TREE_NAME = "fullyDefineTree"
STATUS_NAME = "fullyDefineStatus"
APPLY_NAME = "fullyDefineApply"
REFRESH_NAME = "fullyDefineRefresh"
CANCEL_NAME = "fullyDefineCancel"
CLOSE_NAME = "fullyDefineClose"
PROGRESS_NAME = "fullyDefineProgress"
SCHEME_NAME = "fullyDefineScheme"
PREVIEW_NAME = "fullyDefinePreview"
OUTCOME_NAME = "fullyDefineOutcome"
DIAGNOSE_NAME = "fullyDefineDiagnose"

_ROLE = QtCore.Qt.UserRole
_panel = None


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------


def _element(geo, pos=None):
    """A geometry reference as the editor numbers it (1-based), with the point on it when there is one."""
    if geo == -1 and pos == 1:
        return tr("the origin")
    if geo == -1:
        return tr("the x axis")
    if geo == -2:
        return tr("the y axis")
    text = tr("element %d") % (geo + 1) if geo >= 0 else tr("external geometry %d") % (-geo - 2)
    if pos:
        text += " " + {1: tr("start"), 2: tr("end"), 3: tr("centre")}.get(pos, tr("point"))
    return text


def constraint_text(spec):
    """One line for a proposed constraint: its type, what it is on and, for a dimension, its value."""
    t = spec["type"]
    args = list(spec["args"])
    value = None
    if spec["role"] == "dimension":
        value = args.pop()
    n = len(args)
    if n == 1:
        refs = [(args[0], None)]
    elif n == 2:
        refs = [(args[0], None), (args[1], None)] if t in ("Parallel", "Perpendicular", "Equal", "Tangent", "Angle") else [(args[0], args[1])]
    elif n == 3:
        refs = [(args[0], args[1]), (args[2], None)]
    elif n == 4:
        refs = [(args[0], args[1]), (args[2], args[3])]
    else:
        refs = [(args[i], args[i + 1] if i + 1 < n else None) for i in range(0, n, 2)]
    text = "%s: %s" % (t, ", ".join(_element(g, p) for g, p in refs))
    if value is not None:
        if t == "Angle":
            text += " = %.6g %s" % (math.degrees(value), tr("deg"))
        else:
            text += " = %.6g %s" % (value, tr("mm"))
    return text


def gate_text(gate, reason):
    if gate == "full":
        return tr("complete")
    return tr("shortened (%s)") % (reason or gate)


def candidate_text(position, cand):
    # the dimension count comes first: in a narrow dock the end of the row is the part that is cut off
    return tr("Candidate %d: %d dimension(s), %d relation(s), %s scheme") % (
        position + 1,
        cand["dimensions"],
        cand["relations"],
        cand["scheme"],
    )


def move_text(move):
    """The largest point move as the power of ten it stays below.  Every candidate was verified to move nothing by
    more than 1e-6 mm; the digits after that are solver noise that differs from one launch to the next."""
    bound = 10.0 ** math.ceil(math.log10(max(move, 1e-12)))
    return tr("below %s mm") % ("%.0e" % bound)


def facts_text(cand):
    return tr("Measured on a fresh copy: degrees of freedom after: %d; largest point move: %s; verification runs: %d; re-solve check: %s") % (
        cand["dof_after"],
        move_text(cand["max_move"]),
        cand["verification_runs"],
        gate_text(cand["gate"], cand["gate_reason"]),
    )


def unclosed_text(entry):
    scheme = entry.get("scheme")
    who = tr("The %s scheme") % scheme if scheme else tr("The sketch")
    if entry.get("out_of_time"):
        return tr("%s stopped because the time ran out, with %d degree(s) of freedom still free. This does not mean it cannot be closed.") % (
            who,
            entry["remaining_dof"],
        )
    if scheme is None:
        return tr("%s cannot be worked on yet: %s.") % (who, entry["reason"])
    return tr("%s could not reach zero degrees of freedom (%d left): %s.") % (who, entry["remaining_dof"], entry["reason"])


def summary_text(r):
    """The status line for a result."""
    status = r["status"]
    if status == "already_defined":
        return tr("The sketch is already fully defined: there is nothing to add.")
    if status == "input_inconsistent":
        return tr(
            "This sketch has a conflict or redundancy, or its stored geometry does not match its constraints, so it cannot be fully defined yet. Diagnose it first."
        )
    if status == "cannot_close":
        if r.get("out_of_time"):
            return tr(
                "The search ran out of time before it found a way to fully define this sketch. That does not mean there is none: use Propose again, or constrain part of the sketch by hand first."
            )
        return tr("No tested way to fully define this sketch was found. The reasons are listed below.")
    n = len(r["candidates"])
    text = tr("Found %d candidate(s) that fully define the sketch (degrees of freedom now: %d).") % (n, r["dof"])
    if r.get("out_of_time"):
        text += " " + tr("A scheme ran out of time, so the list may be incomplete.")
    return text


def apply_outcome_text(cand, info):
    """What an Apply did, with the re-solve gate that ran."""
    text = tr("Added %d constraint(s) (%d relation(s), %d dimension(s)) from the %s scheme. Degrees of freedom now: %d.") % (
        info["added"],
        cand["relations"],
        cand["dimensions"],
        cand["scheme"],
        info["dof"],
    )
    if info["gate"] == "full":
        text += " " + tr("The result passed the full re-solve check (%d solves of the sketch and of a serialised copy).") % info["gate_runs"]
    else:
        text += " " + tr("The re-solve check was reduced to %d solve(s) of the sketch only, because %s.") % (
            info["gate_runs"],
            info["gate_reason"],
        )
    return text


# ---------------------------------------------------------------------------
# The dialog
# ---------------------------------------------------------------------------


class FullyDefinePanel(DP.DiagnosisPanel):
    SCHEMES = (None,) + tuple(AD.SCHEMES)

    def __init__(self, sketch, parent=None, auto_run=True):
        super().__init__(sketch, parent, auto_run=auto_run)
        self.setObjectName(DOCK_NAME)
        self.setWindowTitle(tr("Fully define sketch"))

    # -- widgets -----------------------------------------------------------
    def _build(self):
        body = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(body)
        self.status = QtWidgets.QLabel(tr("Not proposed yet."))
        self.status.setObjectName(STATUS_NAME)
        self.status.setWordWrap(True)
        self.status.setAccessibleName(tr("Fully define status"))
        lay.addWidget(self.status)
        row = QtWidgets.QHBoxLayout()
        label = QtWidgets.QLabel(tr("Preferred scheme"))
        self.scheme_box = QtWidgets.QComboBox()
        self.scheme_box.setObjectName(SCHEME_NAME)
        self.scheme_box.setAccessibleName(tr("Preferred scheme"))
        self.scheme_box.setToolTip(
            tr("The scheme listed first; the others follow. Baseline places each piece from the origin, chain places each piece from the previous one, symmetric looks for symmetry first")
        )
        for name in self.SCHEMES:
            self.scheme_box.addItem(tr("Automatic") if name is None else name, name)
        label.setBuddy(self.scheme_box)
        row.addWidget(label)
        row.addWidget(self.scheme_box, 1)
        lay.addLayout(row)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setObjectName(PROGRESS_NAME)
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setAccessibleName(tr("Search progress"))
        self.progress.hide()
        lay.addWidget(self.progress)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setObjectName(TREE_NAME)
        self.tree.setHeaderHidden(True)
        self.tree.setWordWrap(True)
        self.tree.setTextElideMode(QtCore.Qt.ElideNone)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.tree.setAccessibleName(tr("Candidates"))
        self.tree.setToolTip(tr("Choose a candidate to see what it would add to the sketch"))
        self.tree.setMinimumHeight(160)
        # the chosen row is drawn in the plain highlight colour whether or not the dock has the focus
        self.tree.setStyleSheet(
            "QTreeWidget::item:selected, QTreeWidget::item:selected:!active "
            "{ background: palette(highlight); color: palette(highlighted-text); }"
        )
        self.tree.itemSelectionChanged.connect(self._on_choice)
        lay.addWidget(self.tree)
        self.preview_label = QtWidgets.QLabel("")
        self.preview_label.setObjectName(PREVIEW_NAME + "Label")
        self.preview_label.setWordWrap(True)
        self.preview_label.hide()
        lay.addWidget(self.preview_label)
        self.preview = QtWidgets.QListWidget()
        self.preview.setObjectName(PREVIEW_NAME)
        self.preview.setAccessibleName(tr("What the chosen candidate would add"))
        self.preview.setMinimumHeight(100)
        self.preview.hide()
        lay.addWidget(self.preview)
        self.outcome = QtWidgets.QLabel("")
        self.outcome.setObjectName(OUTCOME_NAME)
        self.outcome.setWordWrap(True)
        self.outcome.setAccessibleName(tr("Result of the last Apply"))
        self.outcome.hide()
        lay.addWidget(self.outcome)
        buttons = QtWidgets.QHBoxLayout()
        self.apply_button = QtWidgets.QPushButton(tr("Apply candidate"))
        self.apply_button.setObjectName(APPLY_NAME)
        self.apply_button.setToolTip(tr("Adds the chosen candidate's constraints to the sketch as one undoable step"))
        self.apply_button.setAccessibleName(tr("Apply candidate"))
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self.apply_chosen)
        self.refresh_button = QtWidgets.QPushButton(tr("Propose again"))
        self.refresh_button.setObjectName(REFRESH_NAME)
        self.refresh_button.setToolTip(tr("Searches again on the sketch as it is now, with the chosen scheme first"))
        self.refresh_button.setAccessibleName(tr("Propose again"))
        self.refresh_button.clicked.connect(self.diagnose_now)
        self.diagnose_button = QtWidgets.QPushButton(tr("Open diagnosis"))
        self.diagnose_button.setObjectName(DIAGNOSE_NAME)
        self.diagnose_button.setToolTip(tr("Opens the diagnosis panel for this sketch to find the conflicting or redundant constraints"))
        self.diagnose_button.setAccessibleName(tr("Open diagnosis"))
        self.diagnose_button.clicked.connect(self.open_diagnosis)
        self.diagnose_button.hide()
        self.cancel_button = QtWidgets.QPushButton(tr("Stop"))
        self.cancel_button.setObjectName(CANCEL_NAME)
        self.cancel_button.setToolTip(tr("Stops the search that is running"))
        self.cancel_button.setAccessibleName(tr("Cancel search"))
        self.cancel_button.clicked.connect(self.request_cancel)
        self.cancel_button.hide()
        self.close_button = QtWidgets.QPushButton(tr("Close"))
        self.close_button.setObjectName(CLOSE_NAME)
        self.close_button.setToolTip(tr("Closes the dialog and leaves the sketch as it is"))
        self.close_button.setAccessibleName(tr("Close dialog"))
        self.close_button.clicked.connect(self.close)
        for b in (self.apply_button, self.refresh_button, self.diagnose_button, self.cancel_button, self.close_button):
            buttons.addWidget(b)
        lay.addLayout(buttons)
        self.setWidget(body)
        self.setAllowedAreas(QtCore.Qt.LeftDockWidgetArea | QtCore.Qt.RightDockWidgetArea)
        self.setFeatures(QtWidgets.QDockWidget.DockWidgetClosable | QtWidgets.QDockWidget.DockWidgetMovable)

    # -- the shared machinery's words and computation -----------------------
    def _words(self):
        return {
            "away": tr("This sketch's document is not the active one. Switch back to it to apply a candidate or propose again."),
            "running": tr("Searching..."),
            "cancelled": tr("Search stopped."),
            "changed_again": tr("The sketch changed; searching again."),
            "failed": tr("The search failed: %s"),
            "keeps_changing": tr("The sketch keeps changing while it is searched. Use Propose again."),
            "again": tr("Searching again."),
        }

    def preferred_scheme(self):
        """The scheme the user put first, or None for automatic."""
        return self.scheme_box.currentData()

    def _compute(self):
        # Existing constraints are never changed by ``propose`` (it checks every one of them, listed or not), so
        # there is nothing for the user to choose: the dialog asks for all of them to be kept.
        keep = list(range(len(self.sketch.Constraints)))
        return AD.propose(self.sketch, keep=keep, prefer=self.preferred_scheme(), progress=self._on_progress)

    def _set_running(self, running):
        super()._set_running(running)
        self.scheme_box.setEnabled(not running)
        if running:
            self.diagnose_button.hide()

    def _forget(self):
        global _panel
        if _panel is self:
            _panel = None

    # -- showing the result ---------------------------------------------------
    def _clear_tree(self):
        """A new search starts: nothing of the last result stays on screen, except what an Apply just reported."""
        self._clear_preview()
        if not getattr(self, "_keep_outcome", False):
            self.outcome.hide()
            self.outcome.setText("")
        super()._clear_tree()

    def _clear_preview(self):
        self.preview.clear()
        self.preview.hide()
        self.preview_label.setText("")
        self.preview_label.hide()

    def _show(self, r):
        self.tree.blockSignals(True)
        self.tree.clear()
        for k, cand in enumerate(r["candidates"]):
            top = self._item(self.tree, candidate_text(k, cand), {"kind": "candidate", "candidate": k})
            for c in cand["constraints"]:
                self._item(top, constraint_text(c), {"kind": "constraint", "candidate": k})
            self._item(top, facts_text(cand), {"kind": "facts", "candidate": k})
        for entry in r["unclosed"]:
            self._item(self.tree, unclosed_text(entry), {"kind": "unclosed"})
        self.tree.blockSignals(False)
        text = summary_text(r)
        self.diagnose_button.setVisible(r["status"] == "input_inconsistent")
        if self._dormant:  # the result came in while the user was looking at another document
            self._status_when_active = text
            self.tree.setEnabled(False)
        else:
            self.status.setText(text)
        self.apply_button.setEnabled(False)

    # -- choosing a candidate shows what it would add --------------------------
    def chosen_candidate(self):
        item = self._chosen()
        if item is None or self.result is None:
            return None
        data = item.data(0, _ROLE) or {}
        if "candidate" not in data:
            return None
        return self.result["candidates"][data["candidate"]]

    chosen_fix = chosen_candidate

    def chosen_action(self):
        return self.chosen_candidate()

    def _on_choice(self):
        cand = self.chosen_candidate()
        self._clear_preview()
        if cand is None:
            self.apply_button.setEnabled(False)
            return
        self.preview_label.setText(
            tr("Applying this candidate would add %d constraint(s) to the sketch:") % len(cand["constraints"])
        )
        self.preview_label.show()
        for c in cand["constraints"]:
            self.preview.addItem(constraint_text(c))
        self.preview.show()
        self.apply_button.setEnabled(self.state == "ready")

    # -- applying -----------------------------------------------------------------
    def _say(self, text):
        self.outcome.setText(text)
        self.outcome.show()

    def apply_chosen(self, *_):
        """Apply the chosen candidate in one named transaction, if the sketch is still the one it was computed for."""
        cand = self.chosen_candidate()
        if cand is None or self.state != "ready":
            return False
        if not self._editing_here():
            return False
        if self._fingerprint() != self.fingerprint:
            self.invalidate(tr("The sketch changed."))
            return False
        doc = self.sketch.Document
        self._applying = True
        name = tr("Fully define sketch")
        info = None
        refused = None
        try:
            doc.openTransaction(name)
            try:
                info = AD.apply_candidate(self.sketch, cand)
            except Exception as exc:
                doc.abortTransaction()
                refused = exc
            else:
                doc.commitTransaction()
        finally:
            self._applying = False
        if refused is not None:
            self._say(
                tr("This candidate could not be applied: %s The sketch is exactly as it was, and you can try another candidate.")
                % (str(refused).rstrip(".") + ".")
            )
            self.apply_button.setEnabled(self.chosen_candidate() is not None)
            return False
        self._keep_outcome = True
        try:
            self._say(apply_outcome_text(cand, info))
            self._after_change()
        finally:
            self._keep_outcome = False
        return True

    def open_diagnosis(self, *_):
        """Hand over to the diagnosis panel for this sketch."""
        sketch = self.sketch
        self.close()
        DP.open_panel(sketch)


def current_panel():
    return _panel


def open_panel(sketch=None, auto_run=True):
    """Open the dialog for the sketch in the editor (or `sketch`).  A dialog that is open is reused."""
    global _panel
    if sketch is None:
        sketch = DP.editing_sketch()
    if sketch is None:
        return None
    if _panel is not None and _panel.state != "closed":
        if _panel.sketch_name == sketch.Name and _panel.doc_name == sketch.Document.Name:
            _panel.show()
            _panel.raise_()
            return _panel
        _panel.close()
    mw = Gui.getMainWindow()
    panel = FullyDefinePanel(sketch, mw, auto_run=auto_run)
    mw.addDockWidget(QtCore.Qt.RightDockWidgetArea, panel)
    panel.show()
    _panel = panel
    return panel
