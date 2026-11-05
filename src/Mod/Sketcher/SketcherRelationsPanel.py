# SPDX-License-Identifier: LGPL-2.1-or-later
"""Relations panel of the sketch editor (cad-fork G10 slice 4c, relation display).  Imported only with the GUI up.

Select an element of the sketch in the editor and the panel lists every constraint that touches it, and for each one
the other elements it ties the selected one to.  Choosing a row highlights that constraint and its elements in the view;
two buttons and a type list hide the constraints that are not on the selection (or not of one type) in the view, and
say so on the panel.  The panel reads the sketch and changes nothing in it: no property, no transaction, no undo entry.

What counts as "on" an element (decided here, stated once):

* A vertex (``Vertex3``, the origin, a centre, an end point) lists the constraints that name exactly that point of that
  element.  A constraint on the whole element (a Horizontal on its line) is not on its end point.
* An edge (``Edge3``, an axis, an external edge) lists the constraints on the element as a whole and on any of its own
  points: a Coincident at its end, its centre's DistanceX.  Each row says which part it reaches (``Edge3 end``).  The
  origin is its own element: the x axis does not list constraints on the origin.
* Coincident chains are shown as the data has them.  A point coincident with another point does not inherit that
  point's other constraints: the Coincident row names the partner point and nothing further is followed.
* A constraint reaches an element through any of its three slots (``First``, ``Second``, ``Third``), so a Symmetric
  line, the point of an AngleViaPoint and both ends of an InternalAlignment are found.  Slots that are unset
  (``GeoUndef``) are not elements.

The view-only filter is ``SketcherGui.setConstraintFilter``: a set of constraint indices held by the edit session, never
a property of the sketch and never saved.  Closing the panel, leaving the editor or any change to the constraints clears
it, so a hidden constraint is never taken for a missing one after the panel is gone.  A change to the sketch that does
not move any constraint (a datum value) keeps the filter once the panel has read the sketch again.

Behaviour follows the public descriptions of constraint filtering and "select associated elements" in FreeCAD's own
documentation of the Sketcher; the layout is the plain list-and-buttons form of FreeCAD's task panels, and no layout,
icon or text comes from another product's documentation.
"""

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import SketcherDiagnosisPanel as DP
from SketcherDiagnosisPanel import tr

try:
    import SketcherGui
except Exception:  # the GUI module is loaded with the Sketcher workbench
    SketcherGui = None

DOCK_NAME = "SketcherRelationsPanel"
TREE_NAME = "relationsTree"
STATUS_NAME = "relationsStatus"
BANNER_NAME = "relationsFilterBanner"
ONLY_NAME = "relationsFilterOnly"
TYPE_NAME = "relationsFilterType"
ALL_NAME = "relationsFilterAll"
CLOSE_NAME = "relationsClose"

GEO_UNDEF = -2000
_ROLE = QtCore.Qt.UserRole
_panel = None


# ---------------------------------------------------------------------------
# Elements and their words
# ---------------------------------------------------------------------------


def element_label(geo, pos=None):
    """An element as the editor names it: ``Edge3``, ``Edge3 end``, ``Origin``, ``X axis``, ``External edge 1``."""
    if geo == -1:
        return tr("Origin") if pos == 1 else tr("X axis")
    if geo == -2:
        return tr("Y axis")
    text = tr("Edge%d") % (geo + 1) if geo >= 0 else tr("External edge %d") % (-geo - 2)
    if pos:
        text += " " + {1: tr("start"), 2: tr("end"), 3: tr("centre")}.get(pos, tr("point"))
    return text


def constraint_label(number, type_, name, value_text):
    """The head of a relation row: editor number, type, name and datum."""
    text = "%d: %s" % (number, type_)
    if name:
        text += " '%s'" % name
    if value_text:
        text += " = " + value_text
    return text


def element_of_selection_name(sketch, sub, vertex_map=None):
    """(geo, pos) for a selection sub-element name of the editor: ``(geo, None)`` for an edge, ``(geo, pos)`` for a point;
    None for anything else (a constraint, a face, an unknown name)."""
    if sub == "RootPoint":
        return (-1, 1)
    if sub == "H_Axis":
        return (-1, None)
    if sub == "V_Axis":
        return (-2, None)
    for prefix, kind in (("ExternalEdge", "ext"), ("Edge", "edge"), ("Vertex", "vertex")):
        if sub.startswith(prefix) and sub[len(prefix):].isdigit():
            n = int(sub[len(prefix):])
            if n < 1:
                return None
            if kind == "ext":
                return (-n - 2, None)
            if kind == "edge":
                return (n - 1, None) if n <= sketch.GeometryCount else None
            try:
                geo, pos = sketch.getGeoVertexIndex(n - 1)
            except Exception:
                return None
            return (geo, pos)
    return None


def selection_names_of_element(sketch, geo, pos, vertex_index):
    """The editor's selection name for an element, or None when it has none (a point the editor cannot name)."""
    if pos:
        if geo == -1 and pos == 1:
            return "RootPoint"
        n = vertex_index.get((geo, pos))
        return "Vertex%d" % (n + 1) if n is not None else None
    if geo == -1:
        return "H_Axis"
    if geo == -2:
        return "V_Axis"
    if geo >= 0:
        return "Edge%d" % (geo + 1)
    return "ExternalEdge%d" % (-geo - 2)


# ---------------------------------------------------------------------------
# The index of the constraints
# ---------------------------------------------------------------------------


_UNITLESS = ("Weight", "SnellsLaw")


class Relations:
    """One pass over ``sketch.Constraints``, then dictionary lookups.

    ``refs[i]`` is the tuple of ``(geo, pos)`` the constraint i names, in slot order, with unset slots left out.
    ``by_geo`` maps an element to the constraints naming it or any of its points, ``by_point`` maps one point."""

    def __init__(self, sketch):
        cons = sketch.Constraints
        self.count = len(cons)
        self.geometry_count = sketch.GeometryCount
        self.types = []
        self.names = []
        self.values = []
        self.refs = []
        by_geo = {}
        by_point = {}
        by_type = {}
        for i, c in enumerate(cons):
            first, fpos, second, spos, third, tpos = c.First, c.FirstPos, c.Second, c.SecondPos, c.Third, c.ThirdPos
            refs = []
            if first != GEO_UNDEF:
                refs.append((first, fpos))
            if second != GEO_UNDEF:
                refs.append((second, spos))
            if third != GEO_UNDEF:
                refs.append((third, tpos))
            t = c.Type
            self.types.append(t)
            self.names.append(c.Name)
            self.values.append(c.Value)
            self.refs.append(tuple(refs))
            by_type.setdefault(t, []).append(i)
            seen_geo = set()
            seen_point = set()
            for geo, pos in refs:
                if pos:
                    if (geo, pos) not in seen_point:
                        seen_point.add((geo, pos))
                        by_point.setdefault((geo, pos), []).append(i)
                # an element lists the constraints on any of its own points, except that the origin is its own element
                if geo == -1 and pos:
                    continue
                if geo not in seen_geo:
                    seen_geo.add(geo)
                    by_geo.setdefault(geo, []).append(i)
        self.by_geo = by_geo
        self.by_point = by_point
        self.by_type = by_type
        self.refs_signature = tuple(self.refs)

    def ids_on(self, geo, pos):
        """Constraint indices on the element: the point itself when `pos`, else the element and its points."""
        if pos:
            return self.by_point.get((geo, pos), [])
        return self.by_geo.get(geo, [])

    @staticmethod
    def matches(source_geo, source_pos, geo, pos):
        if source_pos:
            return geo == source_geo and pos == source_pos
        if geo != source_geo:
            return False
        return not (geo == -1 and pos)  # the origin is not part of the x axis

    def row(self, i, source_geo, source_pos):
        """(on, tied) for constraint i: the labels of the slots that are the source (with the part they reach) and the
        distinct other elements it names, in slot order."""
        on = []
        tied = []
        for geo, pos in self.refs[i]:
            label = element_label(geo, pos)
            if self.matches(source_geo, source_pos, geo, pos):
                if label not in on:
                    on.append(label)
            elif label not in tied:
                tied.append(label)
        return on, tied


# ---------------------------------------------------------------------------
# Watching the document, the selection and the editor
# ---------------------------------------------------------------------------


class _DocWatch:
    def __init__(self, panel):
        self.panel = panel

    def _mine(self, obj):
        try:
            return obj is not None and obj.Document.Name == self.panel.doc_name and obj.Name == self.panel.sketch_name
        except Exception:
            return False

    def slotChangedObject(self, obj, prop):
        if self._mine(obj):
            self.panel.on_property(prop)

    def slotUndoDocument(self, doc):
        if doc.Name == self.panel.doc_name:
            self.panel.reset(tr("Undo changed the document."))

    def slotRedoDocument(self, doc):
        if doc.Name == self.panel.doc_name:
            self.panel.reset(tr("Redo changed the document."))

    def slotDeletedObject(self, obj):
        if self._mine(obj):
            self.panel.invalidate(tr("The sketch was deleted."), close=True)

    def slotDeletedDocument(self, doc):
        if doc.Name == self.panel.doc_name:
            self.panel.invalidate(tr("The document was closed."), close=True)


class _SelWatch:
    """Selection events: the list follows the user's selection, and ignores what the panel itself selects."""

    def __init__(self, panel):
        self.panel = panel

    def _event(self, *_):
        self.panel.on_selection()

    addSelection = removeSelection = setSelection = clearSelection = _event


# ---------------------------------------------------------------------------
# The panel
# ---------------------------------------------------------------------------


class RelationsPanel(QtWidgets.QDockWidget):
    def __init__(self, sketch, parent=None):
        super().__init__(tr("Sketch relations"), parent or Gui.getMainWindow())
        self.setObjectName(DOCK_NAME)
        self.sketch = sketch
        self.doc_name = sketch.Document.Name
        self.sketch_name = sketch.Name
        self.state = "ready"  # ready, closed
        self.index = None
        self.source = None  # (geo, pos or None) of the element the list was built for
        self.filter_spec = None  # None, ("element", geo, pos) or ("type", name)
        self.filter_count = 0
        self._resume_filter = None  # a filter to put back when the sketch is read again after a change that moved nothing
        self._owned = []  # selection names the panel added for the chosen row
        self._own_change = False
        self._vertex_index = None
        self._dormant = False
        self._closing = False
        self._resync_pending = False
        self._build()
        self._dw = _DocWatch(self)
        self._gw = DP._GuiWatch(self)
        self._sw = _SelWatch(self)
        App.addDocumentObserver(self._dw)
        Gui.addDocumentObserver(self._gw)
        Gui.Selection.addObserver(self._sw)
        self.on_selection()

    # -- widgets -----------------------------------------------------------
    def _build(self):
        body = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(body)
        self.status = QtWidgets.QLabel(tr("Select an element of the sketch."))
        self.status.setObjectName(STATUS_NAME)
        self.status.setWordWrap(True)
        self.status.setAccessibleName(tr("Relations status"))
        lay.addWidget(self.status)
        self.banner = QtWidgets.QLabel("")
        self.banner.setObjectName(BANNER_NAME)
        self.banner.setWordWrap(True)
        self.banner.setAccessibleName(tr("Filter notice"))
        self.banner.setStyleSheet(
            "QLabel { background: palette(highlight); color: palette(highlighted-text); font-weight: bold; padding: 3px; }"
        )
        self.banner.hide()
        lay.addWidget(self.banner)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setObjectName(TREE_NAME)
        self.tree.setHeaderHidden(True)
        self.tree.setWordWrap(True)
        self.tree.setTextElideMode(QtCore.Qt.ElideNone)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.tree.setAccessibleName(tr("Constraints on the selected element"))
        self.tree.setToolTip(tr("Choose a constraint to highlight it and its elements in the view"))
        self.tree.setMinimumHeight(160)
        self.tree.setStyleSheet(
            "QTreeWidget::item:selected, QTreeWidget::item:selected:!active "
            "{ background: palette(highlight); color: palette(highlighted-text); }"
        )
        self.tree.itemSelectionChanged.connect(self._on_choice)
        lay.addWidget(self.tree)
        row = QtWidgets.QHBoxLayout()
        self.only_button = QtWidgets.QPushButton(tr("Show only these"))
        self.only_button.setObjectName(ONLY_NAME)
        self.only_button.setToolTip(tr("Hides the other constraints in the view. The sketch is not changed"))
        self.only_button.setAccessibleName(tr("Show only these constraints"))
        self.only_button.setEnabled(False)
        self.only_button.clicked.connect(self.filter_to_element)
        self.all_button = QtWidgets.QPushButton(tr("Show all"))
        self.all_button.setObjectName(ALL_NAME)
        self.all_button.setToolTip(tr("Shows every constraint in the view again"))
        self.all_button.setAccessibleName(tr("Show all constraints"))
        self.all_button.setEnabled(False)
        self.all_button.clicked.connect(self.clear_filter)
        row.addWidget(self.only_button)
        row.addWidget(self.all_button)
        lay.addLayout(row)
        trow = QtWidgets.QHBoxLayout()
        label = QtWidgets.QLabel(tr("Only type"))
        self.type_box = QtWidgets.QComboBox()
        self.type_box.setObjectName(TYPE_NAME)
        self.type_box.setAccessibleName(tr("Only type"))
        self.type_box.setToolTip(tr("Hides the constraints of other types in the view. The sketch is not changed"))
        label.setBuddy(self.type_box)
        trow.addWidget(label)
        trow.addWidget(self.type_box, 1)
        lay.addLayout(trow)
        self._fill_types()
        self.type_box.activated.connect(self._on_type)
        self.close_button = QtWidgets.QPushButton(tr("Close"))
        self.close_button.setObjectName(CLOSE_NAME)
        self.close_button.setToolTip(tr("Closes the panel, shows every constraint again and leaves the sketch as it is"))
        self.close_button.setAccessibleName(tr("Close panel"))
        self.close_button.clicked.connect(self.close)
        lay.addWidget(self.close_button)
        self.setWidget(body)
        self.setAllowedAreas(QtCore.Qt.LeftDockWidgetArea | QtCore.Qt.RightDockWidgetArea)
        self.setFeatures(QtWidgets.QDockWidget.DockWidgetClosable | QtWidgets.QDockWidget.DockWidgetMovable)

    def _fill_types(self):
        self.type_box.blockSignals(True)
        self.type_box.clear()
        self.type_box.addItem(tr("All types"), None)
        if self.index is not None:
            for t in sorted(self.index.by_type):
                self.type_box.addItem("%s (%d)" % (t, len(self.index.by_type[t])), t)
        self.type_box.blockSignals(False)

    # -- reading the sketch -----------------------------------------------------
    def _ensure_index(self):
        if self.index is None:
            self.index = Relations(self.sketch)
            self._vertex_index = None
            self._fill_types()
        return self.index

    def vertex_index(self):
        """{(geo, pos): vertex number (0-based)} for the editor's ``VertexN`` names, built when first needed."""
        if self._vertex_index is None:
            m = {}
            i = 0
            while True:
                try:
                    geo, pos = self.sketch.getGeoVertexIndex(i)
                except Exception:
                    break
                if geo == GEO_UNDEF and pos == 0:
                    break
                m[(geo, pos)] = i
                i += 1
            self._vertex_index = m
        return self._vertex_index

    def _alive(self):
        return self.state != "closed" and not self._closing

    def _user_elements(self):
        """The elements the user has selected in this sketch, in order, as (geo, pos)."""
        out = []
        try:
            for s in Gui.Selection.getSelectionEx(self.doc_name):
                if s.ObjectName != self.sketch_name:
                    continue
                for sub in s.SubElementNames:
                    el = element_of_selection_name(self.sketch, sub)
                    if el is not None and el not in out:
                        out.append(el)
        except Exception:
            pass
        return out

    def _selected_names(self):
        names = set()
        try:
            for s in Gui.Selection.getSelectionEx(self.doc_name):
                if s.ObjectName == self.sketch_name:
                    names.update(s.SubElementNames)
        except Exception:
            pass
        return names

    # -- following the selection ---------------------------------------------------
    def on_selection(self):
        if not self._alive() or self._own_change or self._dormant:
            return
        if not self._editing_here():
            return
        try:
            els = self._user_elements()
        except Exception:
            els = []
        if self._resync_pending:
            return
        if els and els[0] == self.source and self.index is not None and self._rows_current:
            return
        if els:
            self.show_element(*els[0])
        else:
            self._show_none()

    def _show_none(self):
        self._drop_focus()
        self.source = None
        self._rows_current = False
        self._empty_tree()
        self.only_button.setEnabled(False)
        self.status.setText(tr("Select an element of the sketch."))

    _rows_current = False

    def _empty_tree(self):
        """Remove every row without telling anyone.  The current item goes first: while a row is current, clear() updates
        it once per removed row and takes time that grows with the square of the row count (2,500 rows: 47 s)."""
        self.tree.blockSignals(True)
        try:
            self.tree.setCurrentItem(None)
            self.tree.clear()
        finally:
            self.tree.blockSignals(False)

    def show_element(self, geo, pos=None):
        """List the relations of an element (also what a selection does)."""
        self._drop_focus()
        idx = self._ensure_index()
        self.source = (geo, pos)
        ids = idx.ids_on(geo, pos)
        self._empty_tree()
        self.tree.blockSignals(True)
        for i in ids:
            on, tied = idx.row(i, geo, pos)
            head = constraint_label(i + 1, idx.types[i], idx.names[i], self._value_text(i))
            if on:
                head += " — " + ", ".join(on)
            top = QtWidgets.QTreeWidgetItem(self.tree, [head])
            top.setData(0, _ROLE, {"kind": "constraint", "index": i})
            for label in tied:
                child = QtWidgets.QTreeWidgetItem(top, [label])
                child.setData(0, _ROLE, {"kind": "tied", "index": i})
            top.setExpanded(True)
        self.tree.blockSignals(False)
        self._rows_current = True
        n = len(ids)
        what = element_label(geo, pos)
        self.status.setText(
            tr("%s: no constraint is on it.") % what if n == 0 else tr("%s: %d constraint(s) are on it.") % (what, n)
        )
        self.only_button.setEnabled(n > 0 and self._alive())

    def _value_text(self, i):
        idx = self.index
        import SketcherDiagnosis as SD

        t = idx.types[i]
        if t in SD._DATUM_TYPES:
            number = "%.6g" % SD._value_in_unit(idx.values[i], t)
            unit = "deg" if t == "Angle" else ("" if t in _UNITLESS else "mm")  # a weight or a Snell ratio has no unit
            return number + (" " + unit if unit else "")
        return ""

    # -- the view-only filter ----------------------------------------------------------
    def _set_view_filter(self, ids):
        if SketcherGui is None:
            return False
        return bool(SketcherGui.setConstraintFilter(None if ids is None else list(ids)))

    def filter_to_element(self, *_):
        """Show only the constraints on the listed element."""
        if self.source is None or not self._alive() or not self._editing_here():
            return False
        idx = self._ensure_index()
        ids = list(idx.ids_on(*self.source))
        return self._apply_filter(("element",) + tuple(self.source), ids)

    def filter_to_type(self, type_name):
        if not self._alive() or not self._editing_here():
            return False
        idx = self._ensure_index()
        if type_name is None:
            return self.clear_filter()
        return self._apply_filter(("type", type_name), list(idx.by_type.get(type_name, [])))

    def _apply_filter(self, spec, ids):
        self._drop_focus()
        if not self._set_view_filter(ids):
            return False
        self.filter_spec = spec
        self.filter_count = len(ids)
        self._show_banner()
        return True

    def clear_filter(self, *_):
        self._set_view_filter(None)
        self.filter_spec = None
        self.filter_count = 0
        self._resume_filter = None
        self._show_banner()
        return True

    def _on_type(self, position):
        self.filter_to_type(self.type_box.itemData(position))

    def _show_banner(self):
        on = self.filter_spec is not None
        total = self.index.count if self.index is not None else 0
        if on:
            what = (
                tr("on %s") % element_label(self.filter_spec[1], self.filter_spec[2])
                if self.filter_spec[0] == "element"
                else tr("of type %s") % self.filter_spec[1]
            )
            self.banner.setText(
                tr("Filter on: the view shows %d of %d constraint(s), those %s. The others are hidden, not removed.")
                % (self.filter_count, total, what)
            )
        self.banner.setVisible(on)
        self.all_button.setEnabled(on)
        if not on:
            self.type_box.blockSignals(True)
            self.type_box.setCurrentIndex(0)
            self.type_box.blockSignals(False)
        else:
            self.type_box.blockSignals(True)
            at = self.type_box.findData(self.filter_spec[1]) if self.filter_spec[0] == "type" else 0
            self.type_box.setCurrentIndex(max(at, 0))
            self.type_box.blockSignals(False)

    # -- focus: highlighting a constraint and its elements -----------------------------------
    def _on_choice(self):
        item = self.tree.selectedItems()[0] if self.tree.selectedItems() else None
        data = (item.data(0, _ROLE) or {}) if item is not None else {}
        if item is None or "index" not in data:
            self._drop_focus()
            return
        self.focus(data["index"])

    def focus(self, i):
        """Highlight constraint i and its elements by adding them to the selection (the user's own selection stays)."""
        self._drop_focus()
        if not self._alive() or not self._editing_here():
            return False
        idx = self.index
        if idx is None or not 0 <= i < idx.count or i >= len(self.sketch.Constraints):
            return False
        names = [DP.constraint_name(i)]
        vix = self.vertex_index()
        for geo, pos in idx.refs[i]:
            n = selection_names_of_element(self.sketch, geo, pos, vix)
            if n is not None and n not in names:
                names.append(n)
        have = self._selected_names()
        self._own_change = True
        try:
            for n in names:
                if n in have:
                    continue
                Gui.Selection.addSelection(self.doc_name, self.sketch_name, n)
                self._owned.append(n)
        finally:
            self._own_change = False
        if self.filter_spec is not None and i not in self._filter_ids():
            self.status.setText(tr("That constraint is hidden by the filter. Show all to see it in the view."))
        return True

    def _filter_ids(self):
        try:
            return SketcherGui.getConstraintFilter() or []
        except Exception:
            return []

    def _drop_focus(self):
        """Remove what the panel added to the selection, and nothing else."""
        names, self._owned = self._owned, []
        self._own_change = True
        try:
            for n in names:
                try:
                    Gui.Selection.removeSelection(self.doc_name, self.sketch_name, n)
                except Exception:
                    pass
        finally:
            self._own_change = False

    # -- the editor and the document ------------------------------------------------------------
    def _edit_now(self, active=None):
        try:
            if self.doc_name not in App.listDocuments() or App.getDocument(self.doc_name).getObject(self.sketch_name) is None:
                return "gone"
        except Exception:
            return "gone"
        return DP.edit_state(self.sketch, active)

    def _editing_here(self):
        self.sync_activation()
        return not self._dormant and self._alive()

    def sync_activation(self, active=None):
        if not self._alive():
            return
        state = self._edit_now(active)
        if state == "inactive":
            if not self._dormant:
                self._dormant = True
                self.tree.setEnabled(False)
                self.only_button.setEnabled(False)
                self.type_box.setEnabled(False)
        elif state == "gone":
            self.invalidate(tr("The sketch is no longer being edited."), close=True)
        elif self._dormant:
            self._dormant = False
            self.tree.setEnabled(True)
            self.type_box.setEnabled(True)
            self.on_selection()

    def on_property(self, prop):
        """A property of the sketch changed.  Constraints and the element lists matter; a drag's geometry moves do not."""
        if not self._alive():
            return
        if prop == "Constraints":
            self.reset(tr("The constraints changed."), keep_filter=True)
        elif prop == "Geometry":
            if self.index is not None and self.sketch.GeometryCount != self.index.geometry_count:
                self.reset(tr("The elements changed."))
        elif prop == "ExternalGeo":
            self.reset(tr("The external geometry changed."))

    def reset(self, reason, keep_filter=False):
        """What the panel shows may be out of date: clear the list, the highlight and the filter at once, then read the
        sketch again.  The filter comes back only if nothing it names has moved."""
        if not self._alive():
            return
        self._drop_focus()
        if not keep_filter and (self.filter_spec is not None or self._resume_filter is not None):
            self._filter_dropped = True  # _resync says so once the status line is rebuilt
        if keep_filter and self.filter_spec is not None:
            self._resume_filter = self.filter_spec
        elif not keep_filter:
            self._resume_filter = None
        self._set_view_filter(None)
        self.filter_spec = None
        self.filter_count = 0
        self._show_banner()
        if self.index is not None:
            self._signature_before = self.index.refs_signature
        self.index = None
        self.source = None
        self._rows_current = False
        self._empty_tree()
        self.only_button.setEnabled(False)
        self.status.setText(reason)
        if not self._resync_pending:
            self._resync_pending = True
            QtCore.QTimer.singleShot(0, self._resync)

    _signature_before = None
    _filter_dropped = False

    def _resync(self):
        self._resync_pending = False
        if not self._alive():
            return
        resume, self._resume_filter = self._resume_filter, None
        dropped, self._filter_dropped = self._filter_dropped, False
        if not self._editing_here():
            return
        try:
            idx = self._ensure_index()
        except Exception:
            return
        self.on_selection()
        if resume is not None and idx.refs_signature == self._signature_before:
            ids = idx.by_type.get(resume[1], []) if resume[0] == "type" else idx.ids_on(resume[1], resume[2])
            self._apply_filter(resume, list(ids))
        elif resume is not None or dropped:
            self.status.setText(self.status.text() + " " + tr("The filter was cleared because the sketch changed."))

    def invalidate(self, reason, close=False):
        if not self._alive():
            return
        self.status.setText(reason)
        if close:
            self._closing = True
            QtCore.QTimer.singleShot(0, self.close)

    # -- closing ------------------------------------------------------------------------------------
    def closeEvent(self, event):
        self.state = "closed"
        for fn, name in ((App.removeDocumentObserver, "_dw"), (Gui.removeDocumentObserver, "_gw"), (Gui.Selection.removeObserver, "_sw")):
            try:
                fn(getattr(self, name))
            except Exception:
                pass
        try:
            self._drop_focus()
        except Exception:
            pass
        try:
            self._set_view_filter(None)
        except Exception:
            pass
        global _panel
        if _panel is self:
            _panel = None
        super().closeEvent(event)
        self.deleteLater()


def current_panel():
    return _panel


def open_panel(sketch=None):
    """Open the panel for the sketch in the editor (or `sketch`).  A panel that is open is reused."""
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
    panel = RelationsPanel(sketch, mw)
    mw.addDockWidget(QtCore.Qt.RightDockWidgetArea, panel)
    panel.show()
    _panel = panel
    return panel
