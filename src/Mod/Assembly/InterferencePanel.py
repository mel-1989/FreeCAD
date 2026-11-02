# SPDX-License-Identifier: LGPL-2.1-or-later
"""Task panel of the Assembly_CheckInterference command (cad-fork G06 slice 1b). Imported only with the GUI up.

The panel runs ``Interference.check`` (and ``check_clearance_sets``) on one assembly and lists the
interfering, touching and close pairs. Selecting a row draws a highlight in the 3D view: both parts
as coloured outlines and, for an interference, the common solid filled in red; for a touching or
close pair, the shortest line between the two parts.

The highlight is temporary Coin geometry added under the view's selection root (the node that holds the view providers, so it shows in the live view and in captures). It is not a
document object, so the check never creates objects, opens transactions, touches the document or
changes a view provider; closing the panel (OK, Close, Escape, or the dialog being destroyed from
outside) removes it. It does not depend on Gui.Selection (the selection highlight can be switched off).

Behaviour sources: OCCT Boolean and BRepExtrema operations through ``Interference`` (G06 slice 1a);
Coin3D scene-graph nodes from the public Coin documentation. Wording and layout are this project's own.
"""

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets
from pivy import coin

import Interference as I

translate = App.Qt.translate

#: highlight colours (RGB 0..1): part A, part B, common solid, closest-point line
COL_A = (0.95, 0.50, 0.00)
COL_B = (0.00, 0.40, 0.90)
COL_COMMON = (0.90, 0.05, 0.05)
COL_COMMON_EDGE = (0.45, 0.00, 0.00)
COL_GAP = (0.80, 0.00, 0.70)

CLASS_TEXT = {"interfering": "Interference", "touching": "Touching", "close": "Too close"}
PAIR_HEADERS = ["Kind", "Part A", "Part B", "Common volume (mm³)", "Distance (mm)"]
SET_HEADERS = ["Clearance set", "Part A", "Part B", "Distance (mm)", "Required (mm)"]
NODE_NAME = "AssemblyInterferenceHighlight"
NODE_COMMON = NODE_NAME + "Common"
NODE_OUTLINE_A = NODE_NAME + "OutlineA"
NODE_OUTLINE_B = NODE_NAME + "OutlineB"
NODE_TINT_A = NODE_NAME + "TintA"
NODE_TINT_B = NODE_NAME + "TintB"
#: parts with this many edges or fewer (sphere, cylinder, cone) also get a tinted face overlay
FEW_EDGES = 3


def part_text(desc):
    """Part label with its link path, as the API reports it: 'Sub / Link / Box'."""
    path = desc.get("link_path") or [desc.get("label", "")]
    return " / ".join(path)


# --------------------------------------------------------------------------- highlight

class Highlight:
    """Temporary scene-graph geometry for one selected pair; ``clear`` removes every node it added."""

    def __init__(self, document_name):
        self.document_name = document_name
        self.sg = None
        self.node = None

    def _scene_graph(self):
        gdoc = Gui.getDocument(self.document_name) if self.document_name in App.listDocuments() else None
        view = gdoc.ActiveView if gdoc is not None else None
        if view is None or not hasattr(view, "getSceneGraph"):
            return None
        sg = view.getSceneGraph()
        # The picture and print paths render only the view providers' root (the selection root), the lights and the
        # camera, not the whole scene graph: the highlight goes under the selection root so every capture shows it.
        for i in range(sg.getNumChildren()):
            child = sg.getChild(i)
            if child.getTypeId().getName().getString().endswith("SoFCUnifiedSelection"):
                return child
        return sg

    @staticmethod
    def _deflection(*shapes):
        diag = max((s.BoundBox.DiagonalLength for s in shapes if s is not None and not s.isNull()), default=1.0)
        return max(diag * 0.002, 1e-4)

    @staticmethod
    def _line_node(shape, color, width, defl, name=""):
        """Polylines of every edge of ``shape``."""
        sep = coin.SoSeparator()
        mat = coin.SoMaterial()
        mat.diffuseColor = color
        mat.emissiveColor = color
        style = coin.SoDrawStyle()
        style.lineWidth = width
        light = coin.SoLightModel()
        light.model = coin.SoLightModel.BASE_COLOR
        pts, counts = [], []
        for edge in shape.Edges:
            try:
                p = edge.discretize(Deflection=defl)
            except Exception:  # an edge OCCT cannot discretise is left out of the outline
                continue
            if len(p) >= 2:
                pts.extend((v.x, v.y, v.z) for v in p)
                counts.append(len(p))
        coords = coin.SoCoordinate3()
        coords.point.setValues(0, len(pts), pts)
        sep.setName(name)
        lines = coin.SoLineSet()
        lines.numVertices.setValues(0, len(counts), counts)
        for n in (mat, style, light, coords, lines):
            sep.addChild(n)
        return sep

    @staticmethod
    def _solid_node(shape, color, transparency, defl, name=""):
        """Shaded faces of ``shape`` (tessellated)."""
        verts, tris = shape.tessellate(defl)
        sep = coin.SoSeparator()
        sep.setName(name)
        mat = coin.SoMaterial()
        mat.diffuseColor = color
        mat.transparency = transparency
        hints = coin.SoShapeHints()
        hints.vertexOrdering = coin.SoShapeHints.UNKNOWN_ORDERING   # two-sided lighting, whatever the face orientation
        hints.creaseAngle = 0.6
        coords = coin.SoCoordinate3()
        coords.point.setValues(0, len(verts), [(v.x, v.y, v.z) for v in verts])
        faces = coin.SoIndexedFaceSet()
        idx = []
        for t in tris:
            idx.extend((t[0], t[1], t[2], -1))
        faces.coordIndex.setValues(0, len(idx), idx)
        for n in (mat, hints, coords, faces):
            sep.addChild(n)
        return sep

    @staticmethod
    def _gap_node(p, q, color):
        sep = coin.SoSeparator()
        mat = coin.SoMaterial()
        mat.diffuseColor = color
        mat.emissiveColor = color
        style = coin.SoDrawStyle()
        style.lineWidth = 3
        style.pointSize = 9
        light = coin.SoLightModel()
        light.model = coin.SoLightModel.BASE_COLOR
        coords = coin.SoCoordinate3()
        coords.point.setValues(0, 2, [tuple(p), tuple(q)])
        line = coin.SoLineSet()
        line.numVertices.setValues(0, 1, [2])
        pts = coin.SoPointSet()
        pts.numPoints = 2
        for n in (mat, style, light, coords, line, pts):
            sep.addChild(n)
        return sep

    def show(self, shape_a, shape_b, common=None, gap=None):
        """Replace the highlight: outlines of both parts, the ``common`` solid if given, the ``gap`` segment (p, q) if given."""
        self.clear()
        sg = self._scene_graph()
        if sg is None:
            return False
        defl = self._deflection(shape_a, shape_b)
        root = coin.SoSeparator()
        root.setName(NODE_NAME)
        pick = coin.SoPickStyle()
        pick.style = coin.SoPickStyle.UNPICKABLE   # clicks go through to the parts
        root.addChild(pick)
        # The highlight is drawn after the document and ignores the depth buffer, so it stays visible inside the parts.
        depth = coin.SoDepthBuffer()
        depth.function = coin.SoDepthBuffer.ALWAYS
        root.addChild(depth)
        if common is not None and not common.isNull() and common.Solids:
            root.addChild(self._solid_node(common, COL_COMMON, 0.15, defl, NODE_COMMON))
            root.addChild(self._line_node(common, COL_COMMON_EDGE, 2, defl))
        for shape, color, name, tint in ((shape_a, COL_A, NODE_OUTLINE_A, NODE_TINT_A), (shape_b, COL_B, NODE_OUTLINE_B, NODE_TINT_B)):
            if len(shape.Edges) <= FEW_EDGES:
                # a sphere's only visible edge is its seam: a faint tinted copy of the part shows where it is
                root.addChild(self._solid_node(shape, color, 0.8, defl, tint))
            root.addChild(self._line_node(shape, color, 2, defl, name))
        if gap is not None:
            root.addChild(self._gap_node(gap[0], gap[1], COL_GAP))
        sg.addChild(root)
        self.sg, self.node = sg, root
        return True

    def clear(self):
        if self.sg is not None and self.node is not None:
            try:
                self.sg.removeChild(self.node)
            except Exception:  # the view is already gone: nothing left to remove
                pass
        self.sg = self.node = None

    @property
    def active(self):
        return self.node is not None


# --------------------------------------------------------------------------- panel

class _ReturnCloses(QtCore.QObject):
    """Return pressed in the panel's inputs or tables closes the Close-only panel, as the Close button would
    (a Close button is a reject-role button, so the task view has no default button to click)."""

    def __init__(self, panel, widgets):
        super().__init__(panel.form)
        self.panel = panel
        for w in widgets:
            w.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.KeyPress and event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            QtCore.QTimer.singleShot(0, self.panel.close_from_key)
            return True
        return False


class TaskCheckInterference:
    """Lists the interfering, touching and close pairs of ``assembly`` and highlights the selected one."""

    title = "Check interference"

    def __init__(self, assembly, clearance=1.0, include_hidden=False, run=True):
        self.assembly = assembly
        self.document_name = assembly.Document.Name
        self.done = False
        self.report = None
        self.sets_report = None
        self.shapes = {}
        self.pair_rows = []
        self.set_rows = []
        self.highlight = Highlight(self.document_name)

        self.form = QtWidgets.QWidget()
        self.form.setObjectName("Assembly_TaskCheckInterference")
        self.form.setWindowTitle(translate("Assembly", self.title))
        lay = QtWidgets.QVBoxLayout(self.form)

        fields = QtWidgets.QFormLayout()
        self.clearance = QtWidgets.QDoubleSpinBox()
        self.clearance.setObjectName("clearance")
        self.clearance.setDecimals(3)
        self.clearance.setRange(0.0, 1.0e6)
        self.clearance.setValue(float(clearance))
        self.clearance.setSuffix(" mm")
        self.clearance.setKeyboardTracking(False)
        self.clearance.setToolTip(translate("Assembly", "Pairs closer than this, but not touching, are listed as too close"))
        fields.addRow(translate("Assembly", "Smallest allowed gap"), self.clearance)
        self.hidden = QtWidgets.QCheckBox(translate("Assembly", "Include hidden parts"))
        self.hidden.setObjectName("include_hidden")
        self.hidden.setChecked(bool(include_hidden))
        fields.addRow("", self.hidden)
        lay.addLayout(fields)

        self.run_button = QtWidgets.QPushButton(translate("Assembly", "Check again"))
        self.run_button.setObjectName("check_again")
        self.run_button.clicked.connect(self.run)
        lay.addWidget(self.run_button)

        self.summary = QtWidgets.QLabel()
        self.summary.setObjectName("summary")
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)

        self.error = QtWidgets.QLabel()
        self.error.setObjectName("error")
        self.error.setWordWrap(True)
        self.error.setStyleSheet("background-color: #b00020; color: white; padding: 2px 6px;")   # own background: readable on any theme
        self.error.hide()
        lay.addWidget(self.error)

        self.notice = QtWidgets.QLabel()
        self.notice.setObjectName("notice")
        self.notice.setWordWrap(True)
        self.notice.setStyleSheet("background-color: #fff1b8; color: #3a2f00; padding: 2px 6px;")   # informational, own colours: readable on any theme
        self.notice.hide()
        lay.addWidget(self.notice)

        self.pairs = self._table("pairs", PAIR_HEADERS)
        lay.addWidget(self.pairs)

        self.sets_head = QtWidgets.QLabel(translate("Assembly", "Clearance sets that are not met"))
        self.sets_head.setObjectName("sets_head")
        f = self.sets_head.font()
        f.setBold(True)
        self.sets_head.setFont(f)
        self.sets = self._table("sets", SET_HEADERS)
        lay.addWidget(self.sets_head)
        lay.addWidget(self.sets)

        self.details = QtWidgets.QLabel()
        self.details.setObjectName("details")
        self.details.setWordWrap(True)
        lay.addWidget(self.details)

        self.pairs.itemSelectionChanged.connect(lambda: self._row_selected(self.pairs, self.sets))
        self.sets.itemSelectionChanged.connect(lambda: self._row_selected(self.sets, self.pairs))
        self._return_closes = _ReturnCloses(self, [self.clearance, self.pairs, self.sets])
        # Gui.Control.closeDialog() calls neither accept nor reject: take the highlight down when the form goes
        self.form.destroyed.connect(self.highlight.clear)
        if run:
            self.run()

    @staticmethod
    def _table(name, headers):
        t = QtWidgets.QTableWidget(0, len(headers))
        t.setObjectName(name)
        t.setHorizontalHeaderLabels([translate("Assembly", h) for h in headers])
        t.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        t.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        t.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        t.verticalHeader().setVisible(False)
        t.horizontalHeader().setStretchLastSection(True)
        t.setMinimumHeight(90)
        return t

    # ---- running the check

    def show_error(self, text):
        self.error.setText(text)
        self.error.setVisible(bool(text))

    def run(self, *_args):
        """Run both checks (no document change) and refill the tables."""
        self.highlight.clear()
        self.show_error("")
        self.details.setText("")
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            res = I.resolve(self.assembly, include_hidden=True)
            self.shapes = {inst.key: inst.shape for inst in res.insts}
            self.report = I.check(self.assembly, clearance=self.clearance.value(),
                                  include_hidden=self.hidden.isChecked())
            self.sets_report = I.check_clearance_sets(self.assembly)
        except Exception as exc:  # noqa: BLE001  (a panel never raises into the Qt event loop)
            self.report = self.sets_report = None
            self.show_error("%s: %s" % (type(exc).__name__, exc))
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self._fill()

    def _fill(self):
        self.pair_rows = list(self.report["pairs"]) if self.report else []
        self.set_rows = list(self.sets_report["violations"]) if self.sets_report else []
        self._fill_table(self.pairs, [self._pair_cells(r) for r in self.pair_rows])
        self._fill_table(self.sets, [self._set_cells(r) for r in self.set_rows])
        has_sets = bool(self.sets_report and self.sets_report["sets"])
        self.sets_head.setVisible(has_sets)
        self.sets.setVisible(has_sets)
        problems = []
        if self.report:
            problems += ["%s: %s" % (e.get("label") or e.get("path") or e.get("a"), e.get("reason") or e.get("error"))
                         for e in self.report["errors"]]
        if self.sets_report:
            problems += [str(u.get("error")) for u in self.sets_report["unresolved"]]
        if problems:
            self.show_error(translate("Assembly", "Could not check:") + " " + "; ".join(problems))
        self.notice.setText(self.notice_text())
        self.notice.setVisible(bool(self.notice.text()))
        self.summary.setText(self.summary_text())

    def notice_text(self):
        """Parts that were found but hold no solid, so they could not be tested (the API's ``skipped_non_solid``)."""
        recs = []
        for rep in (self.report, self.sets_report):
            for rec in (rep or {}).get("skipped_non_solid", []):
                if all(part_text(rec) != part_text(r) for r in recs):
                    recs.append(rec)
        if not recs:
            return ""
        names = ", ".join("%s (%s)" % (part_text(r), r.get("shape_type", "no solid")) for r in recs)
        return translate("Assembly", "Not tested, because they hold no solid: %s. Only solids are checked.") % names

    def summary_text(self):
        if not self.report:
            return translate("Assembly", "The check did not run.")
        c = self.report["stats"]["counts"]
        text = translate("Assembly", "%d interfering, %d touching, %d too close; %d parts checked.") % (
            c["interfering"], c["touching"], c["close"], self.report["stats"]["instances"])
        if self.sets_report and self.sets_report["sets"]:
            text += " " + translate("Assembly", "%d clearance set violations.") % len(self.sets_report["violations"])
        skipped = self.report["stats"]["hidden_skipped"]
        if skipped:
            text += " " + translate("Assembly", "%d hidden parts left out.") % skipped
        return text

    @staticmethod
    def _pair_cells(r):
        cls = r["class"]
        vol = "%.3f" % r["volume_mm3"] if cls == "interfering" else "-"
        dist = "-" if cls == "interfering" else "%.3f" % r["distance_mm"]
        return [CLASS_TEXT[cls], part_text(r["a"]), part_text(r["b"]), vol, dist]

    @staticmethod
    def _set_cells(r):
        return [r["set"], part_text(r["a"]), part_text(r["b"]), "%.3f" % r["distance_mm"], "%.3f" % r["required_mm"]]

    @staticmethod
    def _fill_table(table, rows):
        table.blockSignals(True)
        table.clearSelection()
        table.setRowCount(len(rows))
        for i, cells in enumerate(rows):
            for j, text in enumerate(cells):
                item = QtWidgets.QTableWidgetItem(text)
                item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
                if j >= 3:
                    item.setTextAlignment(int(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter))
                table.setItem(i, j, item)
        table.resizeColumnsToContents()
        table.blockSignals(False)

    # ---- selecting a row

    def select_pair(self, row):
        """Select row ``row`` of the pair table (what a click does)."""
        self.pairs.selectRow(row)

    def select_violation(self, row):
        self.sets.selectRow(row)

    def _row_selected(self, table, other):
        rows = sorted({i.row() for i in table.selectedItems()})
        if not rows:
            self.highlight.clear()
            self.details.setText("")
            return
        other.blockSignals(True)
        other.clearSelection()
        other.blockSignals(False)
        row = rows[0]
        try:
            if table is self.pairs:
                self._show_pair(self.pair_rows[row])
            else:
                self._show_violation(self.set_rows[row])
        except Exception as exc:  # noqa: BLE001
            self.highlight.clear()
            self.details.setText(translate("Assembly", "Could not draw the highlight: %s") % exc)

    def _shapes_of(self, rec):
        a, b = self.shapes.get(rec["a"]["path"]), self.shapes.get(rec["b"]["path"])
        if a is None or b is None:
            raise RuntimeError("the shapes of %s and %s are not available" % (part_text(rec["a"]), part_text(rec["b"])))
        return a, b

    def _show_pair(self, rec):
        a, b = self._shapes_of(rec)
        names = "%s / %s" % (part_text(rec["a"]), part_text(rec["b"]))
        if rec["class"] == "interfering":
            common = a.common(b)
            shown = self.highlight.show(a, b, common=common)
            text = translate("Assembly", "%s share %.3f mm³") % (names, common.Volume)
            self.details.setText(text + (translate("Assembly", " (red in the 3D view).") if shown else "."))
            return
        pts = rec.get("points")
        gap = (App.Vector(*pts[0]), App.Vector(*pts[1])) if pts else None
        shown = self.highlight.show(a, b, gap=gap)
        line = translate("Assembly", ", magenta line") if shown else ""
        if rec["class"] == "touching":
            self.details.setText(translate("Assembly", "%s touch (closest distance %.3f mm%s).") % (names, rec["distance_mm"], line))
        else:
            self.details.setText(translate("Assembly", "%s are %.3f mm apart%s.") % (names, rec["distance_mm"], translate("Assembly", " (magenta line)") if shown else ""))

    def _show_violation(self, rec):
        a, b = self._shapes_of(rec)
        dist, pts, _info = a.distToShape(b)
        gap = (pts[0][0], pts[0][1]) if pts else None
        shown = self.highlight.show(a, b, gap=gap)
        self.details.setText(translate("Assembly", "%s / %s are %.3f mm apart; set \"%s\" requires %.3f mm%s.") % (
            part_text(rec["a"]), part_text(rec["b"]), dist, rec["set"], rec["required_mm"],
            translate("Assembly", " (magenta line)") if shown else ""))

    # ---- task dialog protocol

    def getStandardButtons(self):
        close = QtWidgets.QDialogButtonBox.StandardButton.Close
        return int(getattr(close, "value", close))

    def close_from_key(self):
        if Gui.Control.activeDialog() and self.reject():
            Gui.Control.closeDialog()

    def accept(self):
        self.highlight.clear()
        self.done = True
        return True

    def reject(self):
        self.highlight.clear()
        self.done = True
        return True

    def clicked(self, button):
        return None

    def isAllowedAlterDocument(self):
        return False

    def isAllowedAlterView(self):
        return True

    def isAllowedAlterSelection(self):
        return True
