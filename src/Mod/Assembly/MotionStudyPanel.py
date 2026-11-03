# SPDX-License-Identifier: LGPL-2.1-or-later
"""Task panel of a dynamic analysis (cad-fork G06 slice 6b). Imported only with the GUI up.

The panel is opened on one dynamic analysis (``AssemblyMotion``). It shows

- the time span and the output step (a span that is not a whole number of steps is refused with a message in the panel,
  exactly as the API refuses it);
- the loads and masses of the study, one row each;
- a Run button, playback of the result in the 3D view (play, pause, step back, step forward and a frame slider) and a plot of
  one quantity over time: a part's position component, the velocity of its base point, or the force of a spring or damper.

Transaction model. The panel stages everything. Run computes the result in memory (``AssemblyMotion.compute``: no object is
written, no transaction is opened) and the panel keeps it; playback sets the placements of the parts for display only and
puts the pre-run placements back when it stops, when the panel closes and before every run (the document's modified flag is
put back as well, and no undo step is made). OK applies the edited span and step and stores the panel's result in the study
as one undo transaction. Cancel, closing the panel and closing the document discard the staged edits and the result and
leave the placements, the undo and redo stacks and the modified flag as they were. No part document is ever touched.

The questions the panel can ask (a report when OK cannot be applied) are module-level functions that tests replace, so no
modal dialog opens on the test path.

Behaviour sources: the stock Simulation panel's timer-driven playback (``CommandCreateSimulation``) for the idea of a
frame slider and a play timer, Qt widget and QPainter documentation. Wording and layout are this project's own.
"""

import math

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

import AssemblyMotion as MO

translate = App.Qt.translate

PLAY_INTERVAL_MS = 40


# ----------------------------------------------------------------- questions

def report_problems(parent, text):
    """Tell the user that OK could not be applied. Tests replace this."""
    QtWidgets.QMessageBox.warning(parent, translate("Assembly", "Dynamic analysis"), text)


# ----------------------------------------------------------------- the plot

def nice_ticks(lo, hi, target=5):
    """Round tick values covering [lo, hi] (at most about ``target`` of them)."""
    if not (math.isfinite(lo) and math.isfinite(hi)):
        return [0.0, 1.0]
    if hi - lo <= 0:
        pad = abs(lo) * 0.1 or 1.0
        lo, hi = lo - pad, hi + pad
    raw = (hi - lo) / max(1, target)
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        step = m * mag
        if step >= raw:
            break
    first = math.ceil(lo / step - 1e-9) * step
    out = []
    v = first
    while v <= hi + step * 1e-9:
        out.append(0.0 if abs(v) < step * 1e-9 else v)
        v += step
    return out


class PlotWidget(QtWidgets.QWidget):
    """A line plot of one series over time, drawn with QPainter: axes with ticks and labels, the curve and a marker at the
    frame being shown. It holds the data it draws (``t``, ``y``), so a test can compare it with the series it claims."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Assembly_MotionPlot")
        self.setAccessibleName(translate("Assembly", "Plot of the chosen quantity over time"))
        self.setMinimumHeight(150)
        self.t, self.y = [], []
        self.title, self.unit = "", ""
        self.marker = None

    def set_data(self, t, y, title, unit):
        self.t, self.y, self.title, self.unit = list(t), list(y), title, unit
        self.update()

    def set_marker(self, t):
        self.marker = t
        self.update()

    def clear(self):
        self.set_data([], [], "", "")

    @staticmethod
    def fmt(v):
        if v == 0:
            return "0"
        a = abs(v)
        return ("%.3g" % v) if (a >= 1e5 or a < 1e-2) else ("%.4g" % v)

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, False)
        pal = self.palette()
        fg, grid = pal.color(QtGui.QPalette.WindowText), pal.color(QtGui.QPalette.Mid)
        p.fillRect(self.rect(), pal.color(QtGui.QPalette.Base))
        fm = p.fontMetrics()
        head = (self.title + (" [%s]" % self.unit if self.unit else "")) if self.title else \
            translate("Assembly", "Nothing to plot yet: run the analysis")
        p.setPen(fg)
        p.drawText(QtCore.QRect(4, 2, self.width() - 8, fm.height()), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, head)
        if not self.t:
            p.end()
            return
        t0, t1 = self.t[0], self.t[-1]
        lo, hi = min(self.y), max(self.y)
        if hi - lo <= 0:
            pad = abs(lo) * 0.1 or 1.0
            lo, hi = lo - pad, hi + pad
        yt = nice_ticks(lo, hi)
        lo, hi = min(lo, yt[0]), max(hi, yt[-1])
        labw = max(fm.horizontalAdvance(self.fmt(v)) for v in yt) + 8
        left, right, top, bottom = labw + 4, 10, fm.height() + 8, fm.height() * 2 + 6
        w, h = self.width() - left - right, self.height() - top - bottom
        if w < 10 or h < 10:
            p.end()
            return

        def X(t):
            return left + (t - t0) / (t1 - t0 or 1.0) * w

        def Y(v):
            return top + (hi - v) / (hi - lo or 1.0) * h

        for v in yt:
            p.setPen(grid)
            p.drawLine(int(left), int(Y(v)), int(left + w), int(Y(v)))
            p.setPen(fg)
            s = self.fmt(v)
            p.drawText(QtCore.QRect(0, int(Y(v)) - fm.height() // 2, int(left) - 4, fm.height()),
                       QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, s)
        for v in nice_ticks(t0, t1):
            p.setPen(grid)
            p.drawLine(int(X(v)), int(top), int(X(v)), int(top + h))
            p.setPen(fg)
            s = self.fmt(v)
            p.drawText(QtCore.QRect(int(X(v)) - 30, int(top + h) + 2, 60, fm.height()), QtCore.Qt.AlignCenter, s)
        p.drawRect(int(left), int(top), int(w), int(h))
        p.drawText(QtCore.QRect(int(left), self.height() - fm.height() - 2, int(w), fm.height()), QtCore.Qt.AlignCenter,
                   translate("Assembly", "time [s]"))
        dark = pal.color(QtGui.QPalette.Base).lightness() < 128
        pen = QtGui.QPen(QtGui.QColor("#6cb2ff") if dark else QtGui.QColor("#1d5a96"))      # the curve reads on either theme
        pen.setWidth(2)
        p.setPen(pen)
        pts = [QtCore.QPointF(X(a), Y(b)) for a, b in zip(self.t, self.y)]
        if len(pts) > 1:
            p.drawPolyline(pts)
        else:
            p.drawPoint(pts[0])
        if self.marker is not None and t0 <= self.marker <= t1:
            p.setPen(QtGui.QPen(fg, 1, QtCore.Qt.DashLine))
            p.drawLine(int(X(self.marker)), int(top), int(X(self.marker)), int(top + h))
        p.end()


# ----------------------------------------------------------------- the panel

_current = None                 # the open panel, until it is accepted, rejected or its document is closed


def panel_open():
    """True while a dynamic-analysis panel is open."""
    return _current is not None and not _current.done


def _text(x):
    """A field's text for a number: shortest form that reads back to the same value."""
    return repr(float(x))


def _number(text):
    """float of a field's text, or None."""
    try:
        v = float(str(text).replace(",", ".").strip())
    except ValueError:
        return None
    return v if math.isfinite(v) else None


class _SaveGuard:
    """Document observer: while a frame is shown, the placements in the document are display placements. A save, save as or
    save a copy of the assembly's document must write the pre-run placements, so playback stops and the placements are put
    back just before the document is written, and the frame is shown again once the save is over."""

    def __init__(self, panel):
        self.panel = panel
        self.frame = None

    def slotStartSaveDocument(self, doc, filename):
        p = self.panel
        if p.done or doc.Name != p.document_name or p._saved is None:
            return
        self.frame = p.frame
        p.stop()

    def slotFinishSaveDocument(self, doc, filename):
        p, k = self.panel, self.frame
        self.frame = None
        if k is not None and not p.done and doc.Name == p.document_name:
            QtCore.QTimer.singleShot(0, lambda: (not p.done) and p.result is not None and p.show_frame(k))


class TaskMotionStudy:
    """The dynamic-analysis panel of ``study``."""

    def __init__(self, study):
        if Gui.Control.activeDialog() or panel_open():
            raise RuntimeError("another task dialog is already open")
        self.study = study
        self.doc = study.Document
        self.document_name = self.doc.Name
        self.study_name = study.Name
        self.asm = MO.assembly_of(study)
        self.done = False
        self.result = MO.result(study)           # the stored result, or the one this panel computed (staged)
        self.stale = ""                          # why the stored result is not shown (it no longer matches the study)
        if self.result is not None:
            self.stale = MO.stale_reason(study, self.result)
            if self.stale:
                self.result = None
        self.fresh = False                       # True when self.result was computed by this panel and not stored yet
        self.run_key = None                      # (end, step) the staged result was computed for
        self.derived = {}
        self.series_error = ""
        self.timer = QtCore.QTimer()
        self.timer.setInterval(PLAY_INTERVAL_MS)
        self.timer.timeout.connect(self._tick)
        self._saved = None                       # pre-run placements while a frame is shown
        self._was_modified = None
        self.frame = 0
        self._busy = False
        self._build()
        self._load_result_views()
        if self.stale:
            self._say(translate("Assembly", "The stored result is out of date and is not shown: %s. Run the analysis again; "
                                "OK removes it.") % self.stale)
        self._observer = _SaveGuard(self)
        App.addDocumentObserver(self._observer)
        global _current
        _current = self

    # ---- construction

    def _build(self):
        self.form = QtWidgets.QWidget()
        self.form.setObjectName("Assembly_TaskMotionStudy")
        self.form.setWindowTitle(translate("Assembly", "Dynamic analysis"))
        self.form.destroyed.connect(self._form_gone)
        lay = QtWidgets.QVBoxLayout(self.form)
        lay.setContentsMargins(6, 6, 6, 6)

        head = QtWidgets.QLabel(translate("Assembly", "%s (%s mode)") % (self.study.Label, self.study.Mode))
        head.setObjectName("headLabel")
        lay.addWidget(head)

        grid = QtWidgets.QGridLayout()
        self.span = QtWidgets.QLineEdit(_text(self.study.TimeEnd))
        self.span.setObjectName("spanEdit")
        self.span.setAccessibleName(translate("Assembly", "Time span in seconds"))
        self.step = QtWidgets.QLineEdit(_text(self.study.TimeStep))
        self.step.setObjectName("stepEdit")
        self.step.setAccessibleName(translate("Assembly", "Output step in seconds"))
        for r, (text, w) in enumerate(((translate("Assembly", "Time span (s)"), self.span),
                                       (translate("Assembly", "Output step (s)"), self.step))):
            lab = QtWidgets.QLabel(text)
            lab.setBuddy(w)
            grid.addWidget(lab, r, 0)
            grid.addWidget(w, r, 1)
        lay.addLayout(grid)
        self.span.textChanged.connect(self._fields_changed)
        self.step.textChanged.connect(self._fields_changed)

        self.message = QtWidgets.QLabel("")
        self.message.setObjectName("messageLabel")
        self.message.setWordWrap(True)
        self.message.setAccessibleName(translate("Assembly", "Message"))
        self.message.hide()
        lay.addWidget(self.message)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setObjectName("loadTree")
        self.tree.setAccessibleName(translate("Assembly", "Loads and masses of the analysis"))
        self.tree.setHeaderLabels([translate("Assembly", "Kind"), translate("Assembly", "Name"),
                                   translate("Assembly", "Detail")])
        self.tree.setRootIsDecorated(False)
        self.tree.setMinimumHeight(90)
        lay.addWidget(self.tree)

        self.run_button = QtWidgets.QPushButton(translate("Assembly", "Run"))
        self.run_button.setObjectName("runButton")
        self.run_button.setToolTip(translate("Assembly", "Integrate the motion over the time span"))
        self.run_button.clicked.connect(lambda *_: self.run())
        lay.addWidget(self.run_button)

        row = QtWidgets.QHBoxLayout()
        self.back_button = QtWidgets.QPushButton(translate("Assembly", "Back"))
        self.back_button.setObjectName("backButton")
        self.back_button.setToolTip(translate("Assembly", "Show the previous frame"))
        self.play_button = QtWidgets.QPushButton(translate("Assembly", "Play"))
        self.play_button.setObjectName("playButton")
        self.play_button.setCheckable(True)
        self.play_button.setToolTip(translate("Assembly", "Play the result in the 3D view or pause it"))
        self.forward_button = QtWidgets.QPushButton(translate("Assembly", "Next"))
        self.forward_button.setObjectName("forwardButton")
        self.forward_button.setToolTip(translate("Assembly", "Show the next frame"))
        for b in (self.back_button, self.play_button, self.forward_button):
            row.addWidget(b)
        lay.addLayout(row)
        self.back_button.clicked.connect(lambda *_: self.step_back())
        self.forward_button.clicked.connect(lambda *_: self.step_forward())
        self.play_button.toggled.connect(self._play_toggled)

        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setObjectName("frameSlider")
        self.slider.setAccessibleName(translate("Assembly", "Frame shown in the 3D view"))
        self.slider.valueChanged.connect(self._slider_moved)
        lay.addWidget(self.slider)
        self.frame_label = QtWidgets.QLabel("")
        self.frame_label.setObjectName("frameLabel")
        lay.addWidget(self.frame_label)

        self.quantity = QtWidgets.QComboBox()
        self.quantity.setObjectName("quantityCombo")
        self.quantity.setAccessibleName(translate("Assembly", "Quantity to plot"))
        qlab = QtWidgets.QLabel(translate("Assembly", "Plot"))
        qlab.setBuddy(self.quantity)
        qrow = QtWidgets.QHBoxLayout()
        qrow.addWidget(qlab)
        qrow.addWidget(self.quantity, 1)
        lay.addLayout(qrow)
        self.quantity.currentIndexChanged.connect(lambda *_: self._replot())
        self.plot = PlotWidget()
        lay.addWidget(self.plot, 1)

    # ---- the loads list

    def refresh_rows(self):
        self.tree.clear()
        for ld in MO.loads(self.study):
            self.tree.addTopLevelItem(QtWidgets.QTreeWidgetItem([ld.Kind, ld.Label, self._load_detail(ld)]))
        for m in MO.masses(self.study):
            self.tree.addTopLevelItem(QtWidgets.QTreeWidgetItem([translate("Assembly", "Mass"), m.Component,
                                                                 self._mass_detail(m)]))
        for i in range(3):
            self.tree.resizeColumnToContents(i)

    @staticmethod
    def _load_detail(ld):
        k = ld.Kind
        if k == "Gravity":
            return "(%g, %g, %g) m/s²" % tuple(ld.Value)
        if k == "Force":
            return "%s at %s, (%g, %g, %g) N" % (ld.Part1, "(%g, %g, %g) mm" % tuple(ld.Point1), *tuple(ld.Value))
        if k == "Torque":
            return "%s, (%g, %g, %g) N·m" % (ld.Part1, *tuple(ld.Value))
        if k == "Spring":
            return "%s to %s, %g N/m, free length %g mm" % (ld.Part1, ld.Part2, ld.Stiffness, ld.FreeLength)
        return "%s to %s, %g N·s/m" % (ld.Part1, ld.Part2, ld.Damping)

    @staticmethod
    def _mass_detail(m):
        return ("density %g kg/m³" % m.Density) if m.Mode == "Density" else ("%g kg" % m.Mass)

    def rows(self):
        """The rows of the loads list as (kind, name, detail) tuples."""
        out = []
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            out.append(tuple(it.text(c) for c in range(3)))
        return out

    # ---- messages

    def _say(self, text):
        self.message.setText(text)
        self.message.setVisible(bool(text))
        if text:
            App.Console.PrintMessage("Dynamic analysis: %s\n" % text)

    def _fields_changed(self, *_):
        if self._busy:
            return
        end, step = self._values()
        if end is None or step is None:
            self._say(translate("Assembly", "The time span and the step must be numbers."))
            return
        try:
            MO.check_grid(end, step)
        except MO.MotionError as exc:
            self._say(str(exc))
            return
        self._say("")

    def _values(self):
        return _number(self.span.text()), _number(self.step.text())

    # ---- running

    def span_and_step(self):
        """(end, step) from the fields, or None with the refusal shown in the panel."""
        end, step = self._values()
        if end is None or step is None:
            self._say(translate("Assembly", "The time span and the step must be numbers."))
            return None
        try:
            MO.check_grid(end, step)
        except MO.MotionError as exc:
            self._say(str(exc))
            return None
        return end, step

    def run(self):
        """Run the analysis on the fields' span and step. The result stays in the panel until OK. Returns the result dict,
        or None when the run was refused (the reason is shown in the panel)."""
        vals = self.span_and_step()
        if vals is None:
            return None
        self.stop()
        try:
            res = MO.compute(self.study, *vals)
        except MO.MotionError as exc:           # MotionRefused is one
            self._say(str(exc))
            return None
        self._restore_modified_flag()
        self.result, self.fresh, self.run_key = res, True, (MO._q(vals[0]), MO._q(vals[1]))
        self._say("; ".join(res.get("warnings", [])))
        self._load_result_views()
        return res

    def result_json(self):
        """The result the panel holds as the string a run stores (``None`` before the first run)."""
        return None if self.result is None else MO.result_text(self.result)

    def _gdoc(self):
        return Gui.getDocument(self.document_name) if App.listDocuments().get(self.document_name) is not None else None

    def _restore_modified_flag(self):
        g = self._gdoc()
        if g is not None and self._was_modified is False:
            g.Modified = False

    def _load_result_views(self):
        self.refresh_rows()
        self.derived, self.series_error = {}, ""
        res = self.result
        self.quantity.blockSignals(True)
        keep = self.quantity.currentData()
        self.quantity.clear()
        if res is not None:
            try:
                self.derived = MO.derived_series(self.study, res)
            except MO.MotionError as exc:
                self.series_error = str(exc)
                self._say(translate("Assembly", "Velocities and forces cannot be plotted: %s") % exc)
            for q in MO.quantities(res):
                if q["kind"] == "position" or q["id"] in self.derived:
                    self.quantity.addItem("%s [%s]" % (q["label"], q["unit"]), q["id"])
            i = self.quantity.findData(keep)
            self.quantity.setCurrentIndex(i if i >= 0 else 0)
        self.quantity.blockSignals(False)
        n = len(res["t"]) - 1 if res else 0
        self.slider.blockSignals(True)
        self.slider.setRange(0, n)
        self.slider.setValue(0)
        self.slider.blockSignals(False)
        self.frame = 0
        for w in (self.play_button, self.back_button, self.forward_button, self.slider):
            w.setEnabled(bool(res))
        self._frame_text()
        self._replot()

    # ---- the plot

    def quantities(self):
        """Ids of the quantities the plot offers."""
        return [self.quantity.itemData(i) for i in range(self.quantity.count())]

    def choose(self, quantity):
        """Plot the quantity with this id (see ``AssemblyMotion.quantities``)."""
        i = self.quantity.findData(quantity)
        if i < 0:
            raise KeyError(quantity)
        self.quantity.setCurrentIndex(i)

    def _replot(self):
        q = self.quantity.currentData()
        if self.result is None or q is None:
            self.plot.clear()
            return
        t, y, info = MO.series_of(self.study, self.result, q, self.derived)
        self.plot.set_data(t, y, info["label"], info["unit"])
        self.plot.set_marker(t[self.frame] if self.frame < len(t) else None)

    # ---- playback (display only)

    def _begin_display(self):
        if self._saved is None:
            g = self._gdoc()
            self._was_modified = None if g is None else bool(g.Modified)
            self._saved = MO._save_placements(self.asm)

    def show_frame(self, k):
        """Show frame ``k`` of the result in the 3D view: placements are set for display only (no undo step, the
        modified flag is put back); ``stop`` or closing the panel restores the pre-run placements."""
        res = self.result
        if res is None or not 0 <= k < len(res["t"]):
            return
        self._begin_display()
        doc = self.doc
        for p in res["parts"]:
            o = doc.getObject(p["name"])
            if o is None:
                continue
            pl = p["placements"][k]
            o.Placement = App.Placement(App.Vector(*pl["base"]), App.Rotation(*pl["rotation"]))
        self._restore_modified_flag()
        self.frame = k
        if self.slider.value() != k:
            self.slider.blockSignals(True)
            self.slider.setValue(k)
            self.slider.blockSignals(False)
        self._frame_text()
        self.plot.set_marker(res["t"][k])
        Gui.updateGui()

    def _frame_text(self):
        res = self.result
        if res is None:
            self.frame_label.setText(translate("Assembly", "No result yet"))
        else:
            self.frame_label.setText(translate("Assembly", "Frame %d of %d, t = %.4g s")
                                     % (self.frame, len(res["t"]) - 1, res["t"][self.frame]))

    def _slider_moved(self, k):
        self.show_frame(k)

    def step_forward(self):
        self.pause()
        if self.result is not None:
            self.show_frame(min(self.frame + 1, len(self.result["t"]) - 1))

    def step_back(self):
        self.pause()
        if self.result is not None:
            self.show_frame(max(self.frame - 1, 0))

    def play(self):
        if self.result is not None:
            self.play_button.setChecked(True)

    def pause(self):
        self.play_button.setChecked(False)

    def _play_toggled(self, on):
        self.play_button.setText(translate("Assembly", "Pause") if on else translate("Assembly", "Play"))
        if on:
            if self.result is None:
                self.play_button.setChecked(False)
                return
            if self.frame >= len(self.result["t"]) - 1:
                self.show_frame(0)
            self.timer.start()
        else:
            self.timer.stop()

    @property
    def playing(self):
        return self.timer.isActive()

    def _tick(self):
        """One timer step: advance by the number of output steps that make up about one timer interval of real time."""
        res = self.result
        if res is None:
            self.pause()
            return
        stride = max(1, round(PLAY_INTERVAL_MS * 1e-3 / res["step"]))
        k = self.frame + stride
        last = len(res["t"]) - 1
        if k >= last:
            self.show_frame(last)
            self.pause()
        else:
            self.show_frame(k)

    def stop(self):
        """Stop playing and put the pre-run placements back."""
        self.timer.stop()
        if self.play_button.isChecked():
            self.play_button.blockSignals(True)
            self.play_button.setChecked(False)
            self.play_button.blockSignals(False)
            self.play_button.setText(translate("Assembly", "Play"))
        if self._saved is not None:
            if self._gdoc() is not None:
                MO._restore_placements(self.asm, self._saved)
                self._restore_modified_flag()
            self._saved = None
            self._was_modified = None

    # ---- task dialog protocol

    def _release(self):
        obs = getattr(self, "_observer", None)
        if obs is not None:
            self._observer = None
            try:
                App.removeDocumentObserver(obs)
            except Exception:                   # noqa: BLE001
                pass
        global _current
        if _current is self:
            _current = None
        self.done = True

    def _form_gone(self, *_):
        """Gui.Control.closeDialog() calls neither accept nor reject, nor does closing the document: nothing was changed, so
        only the display has to be put back."""
        try:
            self.timer.stop()
            if self._saved is not None and App.listDocuments().get(self.document_name) is not None:
                self.stop()
        except Exception:                       # noqa: BLE001
            pass
        self._release()

    def getStandardButtons(self):
        std = QtWidgets.QDialogButtonBox.StandardButton
        ok, cancel = std.Ok, std.Cancel
        return int(getattr(ok, "value", ok)) | int(getattr(cancel, "value", cancel))

    def accept(self):
        """Apply the edited span and step, and store the result of this panel's run, as one undo transaction."""
        if self.done:
            return True
        vals = self.span_and_step()
        if vals is None:
            return False                        # the refusal is shown in the panel; OK does nothing until it is fixed
        self.stop()
        keep = self.result if self.fresh and self.run_key == (MO._q(vals[0]), MO._q(vals[1])) else None
        dropped = ""
        if keep is not None:
            gone = [p["name"] for p in keep["parts"] if self.doc.getObject(p["name"]) is None]
            if gone:
                keep = None
                dropped = translate("Assembly", "The result was not stored: it names parts that no longer exist (%s).") \
                    % ", ".join(gone)
        try:
            rep = MO.apply_study_report(self.study, vals[0], vals[1], keep)
        except MO.MotionError as exc:
            report_problems(self.form, str(exc))
            self._say(str(exc))
            return False
        if rep["cleared"]:
            dropped = (dropped + " " if dropped else "") + translate(
                "Assembly", "The stored result was removed because %s.") % rep["cleared"]
        if dropped:
            self._say(dropped)
            report_problems(self.form, dropped)
        self._release()
        return True

    def reject(self):
        self.stop()
        self._release()
        return True

    def clicked(self, button):
        return None

    def isAllowedAlterDocument(self):
        return True

    def isAllowedAlterView(self):
        return True

    def isAllowedAlterSelection(self):
        return True
