# SPDX-License-Identifier: LGPL-2.1-or-later
"""G10 slice 5c, the GUI for sketch blocks: the blocks panel driven through real widgets and real Qt mouse events.

Run with the GUI binary: ``FreeCAD -t TestSketcherBlocksPanelGui``.  The checks are the numbered acceptance checks of
slice 5c in briefs/phase1/G10-sketcher.md: 1 (the list equals the API), 2 (define), 3 (insert by number), 4 (insert by
click), 5 (selection both ways), 6 (explode and delete), 7 to 9 (edit in place, the second half of this file),
10 (no effect on other work), 11 (speed), 12 (reachable and clean); 13 is the mutant script SketchBlocksPanelMutants.py.
What the panel must show is computed here from ``SketcherBlocks.blocks()`` and from the sketch's own tables; the
widgets are read back, never the panel's attributes, except where a test names the attribute it spies on.
"""

import ast
import math
import os
import re
import statistics
import time

import FreeCAD
import FreeCADGui as Gui
import Part
import Sketcher
from PySide import QtCore, QtGui, QtWidgets

import SketcherBlocks as B
from SketcherTests import SketchBlocksCorpus as C
from SketcherTests.GuiTestCase import SketcherGuiTestCase
from SketcherTests.TestSketcherDiagnosisPanelGui import _content, _editing, _find, _pump, _rows, _selected_names, _wait

V = FreeCAD.Vector
COMMAND = "Sketcher_Blocks"
SPEED_BAR = 0.1  # seconds, check 11


def _snapshot(sk):
    """Everything a refused action must leave alone."""
    doc = sk.Document
    return {
        "content": _content(sk),
        "block": sk.BlockData,
        "undo": doc.UndoCount,
        "redo": doc.RedoCount,
        "names": list(doc.UndoNames),
        "state": tuple(sk.State),
        "touched": doc.isTouched(),
    }


def _widget(name, cls=QtWidgets.QWidget):
    found = _find(name, cls)
    return found[0] if found else None


def _tree_rows(panel):
    """[(definition, member count, instance count, [(instance, member count)])] read back from the widgets."""
    out = []
    for top_text, kids in _rows(panel.tree):
        m = re.match(r"(.*): (\d+) member\(s\), (\d+) instance\(s\)$", top_text)
        insts = []
        for kid_text, _ in kids:
            k = re.match(r"(.*): (\d+) member\(s\)$", kid_text)
            insts.append((k.group(1), int(k.group(2))))
        out.append((m.group(1), int(m.group(2)), int(m.group(3)), insts))
    return out


def _expected_rows(sk):
    info = B.blocks(sk)
    out = []
    for dname, members in info["definitions"].items():
        insts = [(n, len(i["geo_ids"])) for n, i in info["instances"].items() if i["definition"] == dname]
        out.append((dname, len(members), len(insts), insts))
    return out


def _latest(sk):
    info = B.blocks(sk)["instances"]
    return max(info, key=lambda n: int(n.rsplit("_", 1)[1]))


class BlocksGuiCase(SketcherGuiTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Gui.activateWorkbench("SketcherWorkbench")
        _pump(50)

    def setUp(self):
        super().setUp()
        self.panel = None
        self._docs = []

    def tearDown(self):
        try:
            self.close_panel()
            gdoc = Gui.ActiveDocument
            if gdoc is not None and gdoc.getInEdit() is not None:
                gdoc.resetEdit()
            _pump(30)
            Gui.Selection.clearSelection()
            for name in self._docs:
                if name in FreeCAD.listDocuments():
                    FreeCAD.closeDocument(name)
        finally:
            super().tearDown()

    # -- helpers -------------------------------------------------------------
    def new_doc(self, name):
        doc = FreeCAD.newDocument(name)
        doc.UndoMode = 1
        self._docs.append(doc.Name)
        return doc

    def line_block_sketch(self, doc, name="Sk", placements=((0, 0, 0), (5, 5, 0.5)), placement=None):
        sk = doc.addObject("Sketcher::SketchObject", name)
        if placement is not None:
            sk.Placement = placement
        sk.addGeometry([Part.LineSegment(V(0, 0, 0), V(4, 0, 0)), Part.LineSegment(V(4, 0, 0), V(4, 3, 0)), Part.Circle(V(1, 1.5, 0), V(0, 0, 1), 0.5)], False)
        B.define(sk, "T", [0, 1, 2])
        sk.delGeometries([0, 1, 2])
        for p in placements:
            B.insert(sk, "T", *p)
        doc.recompute()
        return sk

    def edit(self, sk):
        Gui.ActiveDocument = Gui.getDocument(sk.Document.Name)
        Gui.ActiveDocument.setEdit(sk.Name)
        _pump(60)
        self.assertEqual(_editing(), (sk.Document.Name, sk.Name))

    def open_panel(self, sk):
        self.edit(sk)
        Gui.runCommand(COMMAND)
        _pump(30)
        import SketcherBlocksPanel as P

        self.P = P
        self.panel = P.current_panel()
        self.assertIsNotNone(self.panel)
        self.assertEqual(self.panel.sketch_name, sk.Name)
        return self.panel

    def close_panel(self):
        p, self.panel = self.panel, None
        if p is not None:
            try:
                p.close()
            except RuntimeError:
                pass
            _pump(30)

    def settle(self):
        _pump()
        _pump()

    def choose(self, kind, name):
        """Choose the row the user would click: found by the words on it, a definition at the top and an instance
        under it."""
        root = self.panel.tree.invisibleRootItem()
        tops = [root.child(i) for i in range(root.childCount())]
        rows = tops if kind == "def" else [t.child(j) for t in tops for j in range(t.childCount())]
        for item in rows:
            if item.text(0).startswith(name + ":"):
                self.panel.tree.setCurrentItem(item)
                item.setSelected(True)
                self.settle()
                return item
        self.fail("no %s row %s" % (kind, name))

    def select(self, sk, *subs):
        Gui.Selection.clearSelection()
        for s in subs:
            Gui.Selection.addSelection(sk.Document.Name, sk.Name, s)
        self.settle()

    def click_button(self, name):
        b = _widget(name, QtWidgets.QPushButton)
        self.assertIsNotNone(b, name)
        b.click()
        self.settle()

    def status(self):
        return _widget("blocksStatus").text()

    def assertSameSketch(self, sk, snap):
        """The sketch as it was: constraints and block data exactly, geometry to 1e-9 (the editor solves again after an
        undo, which can move a coordinate by 1e-14)."""
        now = _content(sk)
        self.assertEqual(now["Constraints"], snap["content"]["Constraints"])
        self.assertEqual(sk.BlockData, snap["block"])
        a = re.findall(r'(?:Start|End|Center|Radius|Angle|Pos|Axis)\w*="(-?[0-9.eE+-]+)"', b"".join(now["Geometry"].values()).decode())
        b = re.findall(r'(?:Start|End|Center|Radius|Angle|Pos|Axis)\w*="(-?[0-9.eE+-]+)"', b"".join(snap["content"]["Geometry"].values()).decode())
        self.assertEqual(len(a), len(b))
        self.assertTrue(a)
        for x, y in zip(a, b):
            self.assertAlmostEqual(float(x), float(y), delta=1e-9)

    def assertPanelEqualsApi(self, sk, why=""):
        self.assertEqual(_tree_rows(self.panel), _expected_rows(sk), why)

    def viewport(self):
        return self.P.find_viewport()

    def send_mouse(self, gl, typ, pos, button, buttons=QtCore.Qt.NoButton):
        gp = QtCore.QPointF(gl.mapToGlobal(pos.toPoint()))
        ev = QtGui.QMouseEvent(typ, pos, pos, gp, button, buttons, QtCore.Qt.NoModifier)
        QtWidgets.QApplication.sendEvent(gl, ev)

    def click_at(self, gl, pos, button=QtCore.Qt.LeftButton):
        # the editor selects what the pointer is over: come from elsewhere so that it is seen to arrive
        self.send_mouse(gl, QtCore.QEvent.MouseMove, pos + QtCore.QPointF(60, 60), QtCore.Qt.NoButton)
        _pump(30)
        self.send_mouse(gl, QtCore.QEvent.MouseMove, pos, QtCore.Qt.NoButton)
        _pump(30)
        self.send_mouse(gl, QtCore.QEvent.MouseButtonPress, pos, button, button)
        self.send_mouse(gl, QtCore.QEvent.MouseButtonRelease, pos, button, QtCore.Qt.NoButton)
        self.settle()

    def pixel_of(self, sk, x, y):
        """Qt position in the viewport of the sketch-frame point (x, y), from the view's own projection."""
        view = Gui.getDocument(sk.Document.Name).ActiveView
        gl = self.viewport()
        w = sk.getGlobalPlacement().multVec(V(x, y, 0))
        px, py = view.getPointOnViewport(w)
        h = view.getSize()[1]
        return QtCore.QPointF(px / gl.devicePixelRatioF(), (h - 1 - py) / gl.devicePixelRatioF())

    def camera(self, sk):
        return Gui.getDocument(sk.Document.Name).ActiveView.getCamera()

    def frame(self, sk, top=True):
        v = Gui.getDocument(sk.Document.Name).ActiveView
        v.viewTop() if top else v.viewIsometric()
        v.fitAll()
        _pump(80)


# ---------------------------------------------------------------------------------------------------------------------
# Check 1: the list equals the API
# ---------------------------------------------------------------------------------------------------------------------


class TestBlocksPanelContents(BlocksGuiCase):
    def test_contents_equal_the_api_on_corpus_sketches_and_an_empty_one(self):
        specs = C.corpus()[:10]
        self.assertEqual(len(specs), 10)
        for k, spec in enumerate(specs):
            doc = self.new_doc("PanelC%d" % k)
            sk, names = C.build(doc, spec, [(0.0, 0.0, 0.0), (3.0, 2.0, 0.7)], B)
            self.open_panel(sk)
            self.assertPanelEqualsApi(sk, spec.name)
            rows = _tree_rows(self.panel)
            self.assertEqual(rows[0][2], 2)
            self.assertEqual([n for n, _ in rows[0][3]], names)
            self.assertEqual(rows[0][1], len(spec.members))
            self.close_panel()
            Gui.ActiveDocument.resetEdit()
            _pump(30)
        doc = self.new_doc("PanelEmpty")
        sk = doc.addObject("Sketcher::SketchObject", "E")
        self.open_panel(sk)
        self.assertEqual(_tree_rows(self.panel), [])
        self.assertFalse(_widget("blocksExplode").isEnabled())
        self.assertFalse(_widget("blocksInsert").isEnabled())

    def test_malformed_block_data_shows_a_sentence_and_offers_no_action(self):
        sk = self.line_block_sketch(self.new_doc("PanelBad"))
        self.open_panel(sk)
        self.choose("inst", "T_1")
        sk.BlockData = "{not json"
        self.settle()
        text = self.status()
        self.assertNotIn("Traceback", text)
        self.assertNotIn("File ", text)
        self.assertTrue(text.endswith("."), text)
        self.assertEqual(_tree_rows(self.panel), [])
        for name in ("blocksDefine", "blocksInsert", "blocksPlace", "blocksExplode", "blocksDelete", "blocksEdit"):
            self.assertFalse(_widget(name).isEnabled(), name)
        sk.BlockData = '{"v":1,"defs":{"T":{"members":5}},"inst":{},"n":0}'
        self.settle()
        self.assertNotIn("Traceback", self.status())
        self.assertFalse(_widget("blocksDefine").isEnabled())

    def test_every_source_of_change_reaches_the_panel_in_two_turns(self):
        sk = self.line_block_sketch(self.new_doc("PanelSources"))
        doc = sk.Document
        self.open_panel(sk)
        self.assertPanelEqualsApi(sk, "start")
        B.insert(sk, "T", 20, 20, 0)  # macro
        self.settle()
        self.assertPanelEqualsApi(sk, "macro insert")
        doc.undo()
        self.settle()
        self.assertPanelEqualsApi(sk, "undo")
        self.assertEqual(len(B.blocks(sk)["instances"]), 2)
        doc.redo()
        self.settle()
        self.assertPanelEqualsApi(sk, "redo")
        self.assertEqual(len(B.blocks(sk)["instances"]), 3)
        # a stock tool deleting a member (the fit rule explodes that instance)
        sk.delGeometry(B.blocks(sk)["instances"]["T_1"]["geo_ids"][2])
        self.settle()
        self.assertPanelEqualsApi(sk, "stock delete")
        self.assertNotIn("T_1", [n for r in _tree_rows(self.panel) for n, _ in r[3]])
        # a member moved outside the fit tolerance (fit-rule explosion with the same number of geometries)
        g2 = B.blocks(sk)["instances"]["T_2"]["geo_ids"][0]
        geo = sk.Geometry
        geo[g2].StartPoint = V(50, 50, 0)
        sk.Geometry = geo
        self.settle()
        self.assertPanelEqualsApi(sk, "fit-rule explosion")
        B.explode(sk, "T_3")
        self.settle()
        self.assertPanelEqualsApi(sk, "explode")
        B.delete_definition(sk, "T")
        self.settle()
        self.assertPanelEqualsApi(sk, "definition deleted")

    def test_a_stale_row_refuses_with_a_sentence_and_changes_nothing(self):
        sk = self.line_block_sketch(self.new_doc("PanelStale"))
        self.open_panel(sk)
        self.choose("inst", "T_2")
        B.delete_instance(sk, "T_2")  # a macro, and no event turn yet: the row is still on show
        before = _snapshot(sk)
        for button in ("blocksExplode", "blocksDelete"):
            sk.Document.undo()
            self.settle()
            self.choose("inst", "T_2")
            B.delete_instance(sk, "T_2")
            before = _snapshot(sk)
            b = _widget(button)
            self.assertTrue(b.isEnabled(), button + " is still on the stale row")
            b.click()
            self.assertEqual(_snapshot(sk), before, button)
            self.assertIn("no block instance", self.status())
        self.settle()
        self.assertPanelEqualsApi(sk, "stale row gone")
        self.assertFalse(_widget("blocksExplode").isEnabled())
        self.choose("def", "T")
        B.explode(sk, "T_1")
        B.delete_definition(sk, "T")
        before = _snapshot(sk)
        _widget("blocksInsert").click()
        self.assertIn("no block called", self.status())
        self.assertEqual(_snapshot(sk), before)


# ---------------------------------------------------------------------------------------------------------------------
# Checks 2, 3 and 6: define, insert by number, explode and delete
# ---------------------------------------------------------------------------------------------------------------------


class TestBlocksPanelActions(BlocksGuiCase):
    def plain_sketch(self, name):
        doc = self.new_doc(name)
        sk = doc.addObject("Sketcher::SketchObject", "P")
        sk.addGeometry([Part.LineSegment(V(0, 0, 0), V(4, 0, 0)), Part.LineSegment(V(4, 0, 0), V(4, 3, 0)), Part.Circle(V(1, 1.5, 0), V(0, 0, 1), 0.5), Part.Point(V(2, 2, 0))], False)
        sk.addConstraint(Sketcher.Constraint("Horizontal", 0))
        doc.recompute()
        return sk

    def twin(self, sk):
        return sk.Document.copyObject(sk)

    def vertex_of_point(self, sk, geo):
        i = 0
        while True:
            g, p = sk.getGeoVertexIndex(i)
            if (g, p) == (geo, 1):
                return i
            i += 1

    def test_define_gives_the_apis_result_in_one_undo_entry(self):
        sk = self.plain_sketch("PanelDef")
        twin = self.twin(sk)
        self.open_panel(sk)
        count = sk.Document.UndoCount
        _widget("blocksName").setText("Mine")
        self.select(sk, "Edge3", "Edge1", "Vertex%d" % (self.vertex_of_point(sk, 3) + 1))  # the order decides the members
        self.click_button("blocksDefine")
        self.assertEqual(sk.Document.UndoCount, count + 1)
        self.assertEqual(sk.Document.UndoNames[0], "Define block")
        B.define(twin, "Mine", [2, 0, 3])
        self.assertEqual(sk.BlockData, twin.BlockData)
        self.assertEqual(_content(sk)["Geometry"], _content(twin)["Geometry"])
        self.assertIn("plain geometry", self.status())
        self.assertPanelEqualsApi(sk)
        self.assertEqual(len(sk.Geometry), 4)  # nothing was added or removed: it stays plain geometry

    def test_every_refusal_of_define_shows_the_sentence_and_changes_nothing(self):
        sk = self.plain_sketch("PanelDefRefuse")
        B.define(sk, "Taken", [0, 1])
        B.insert(sk, "Taken", 20, 20, 0)
        inst_geo = B.blocks(sk)["instances"]["Taken_1"]["geo_ids"][0]
        sk.addGeometry(Part.Ellipse(V(30, 30, 0), 3.0, 1.0), False)
        ell = len(sk.Geometry) - 1
        self.open_panel(sk)
        cases = [
            ("empty selection", "Fresh", [], None),
            ("duplicate name", "Taken", ["Edge3", "Edge4"], "already exists"),
            ("member of an instance", "X1", ["Edge%d" % (inst_geo + 1)], "already belongs"),
            ("unsupported member", "X2", ["Edge%d" % (ell + 1), "Edge3"], "cannot be part"),
            ("degenerate set", "X3", ["Edge3"], "two distinct points"),
            ("origin", "X4", ["RootPoint"], "origin"),
            ("axis", "X5", ["H_Axis"], "origin"),
            ("constraint", "X6", ["Constraint1"], "constraint"),
            ("empty name", "", ["Edge1", "Edge2"], "name"),
            ("spaces name", "   ", ["Edge1", "Edge2"], "name"),
        ]
        for label, name, subs, word in cases:
            before = _snapshot(sk)
            _widget("blocksName").setText(name)
            self.select(sk, *subs)
            sel_before = _selected_names()
            self.click_button("blocksDefine")
            text = self.status()
            self.assertEqual(_snapshot(sk), before, label)
            self.assertTrue(text.endswith("."), (label, text))
            self.assertNotIn("Traceback", text)
            self.assertNotIn("Defined", text, label)
            if word:
                self.assertIn(word, text, label)
            self.assertEqual(_selected_names(), sel_before, label)
        twin = self.twin(sk)
        with self.assertRaises(B.SketchBlockError) as cm:
            B.define(twin, "Taken", [2, 3])
        _widget("blocksName").setText("Taken")
        self.select(sk, "Edge3", "Edge4")
        self.click_button("blocksDefine")
        said = str(cm.exception)
        self.assertEqual(self.status(), said[0].upper() + said[1:] + ("" if said.endswith(".") else "."))

    def test_insert_by_number_lands_on_analytic_coordinates(self):
        sk = self.line_block_sketch(self.new_doc("PanelNum"), placements=())
        self.open_panel(sk)
        self.choose("def", "T")
        count = sk.Document.UndoCount
        for x, y, a, ex, ey, ea in [
            ("25.4 mm", "1 in", "90 deg", 25.4, 25.4, math.pi / 2),
            ("2*3 mm", "-7", "30", 6.0, -7.0, math.radians(30)),
            ("1 in + 2 mm", "0.5 mm", "1 rad", 27.4, 0.5, 1.0),
        ]:
            _widget("blocksX").setText(x)
            _widget("blocksY").setText(y)
            _widget("blocksAngle").setText(a)
            self.click_button("blocksInsert")
            g = sk.Geometry[B.blocks(sk)["instances"][_latest(sk)]["geo_ids"][0]]
            self.assertAlmostEqual(g.StartPoint.x, ex, delta=1e-7)
            self.assertAlmostEqual(g.StartPoint.y, ey, delta=1e-7)
            self.assertAlmostEqual(g.EndPoint.x, ex + 4 * math.cos(ea), delta=1e-7)
            self.assertAlmostEqual(g.EndPoint.y, ey + 4 * math.sin(ea), delta=1e-7)
            count += 1
            self.assertEqual(sk.Document.UndoCount, count)
            self.assertEqual(sk.Document.UndoNames[0], "Insert block")
        self.assertPanelEqualsApi(sk)

    def test_an_insert_through_the_panel_solves_once(self):
        """Check 3's one solve, counted: every solve() the API asks of the sketch during a panel insert goes through a proxy.  (addGeometry
        and setConstruction do not solve inside the C++ sketch, as SketchObjectGeometry.cpp shows, and the panel only reads afterwards, so
        the calls through the proxy are all of them; a solve made inside the C++ code cannot be counted from Python.)"""
        sk = self.line_block_sketch(self.new_doc("PanelSolves"), placements=())
        self.open_panel(sk)
        self.choose("def", "T")

        class Counting:
            def __init__(self, inner):
                object.__setattr__(self, "_inner", inner)
                object.__setattr__(self, "solves", 0)

            def solve(self):
                object.__setattr__(self, "solves", self.solves + 1)
                return self._inner.solve()

            def __getattr__(self, name):
                return getattr(self._inner, name)

            def __setattr__(self, name, value):
                setattr(self._inner, name, value)

        seen = []
        real = B.insert

        def counted(sketch, *args, **kw):
            proxy = Counting(sketch)
            try:
                return real(proxy, *args, **kw)
            finally:
                seen.append(proxy.solves)

        B.insert = counted
        try:
            self.click_button("blocksInsert")
        finally:
            B.insert = real
        self.assertEqual(len(B.blocks(sk)["instances"]), 1)
        self.assertEqual(seen, [1])

    def test_a_bad_value_refuses_with_a_sentence_and_changes_nothing(self):
        sk = self.line_block_sketch(self.new_doc("PanelNumBad"), placements=())
        self.open_panel(sk)
        self.choose("def", "T")
        before = _snapshot(sk)
        for field, text in [("blocksX", "abc"), ("blocksX", ""), ("blocksY", "5 kg"), ("blocksAngle", "3 mm"), ("blocksAngle", "1/0 deg"), ("blocksX", "10 deg")]:
            for name, good in (("blocksX", "0 mm"), ("blocksY", "0 mm"), ("blocksAngle", "0 deg")):
                _widget(name).setText(good)
            _widget(field).setText(text)
            self.click_button("blocksInsert")
            self.assertEqual(_snapshot(sk), before, (field, text))
            self.assertTrue(self.status().endswith("."), self.status())
            self.assertNotIn("Inserted", self.status())

    def test_explode_gives_the_apis_result_and_delete_removes_exactly_the_instance(self):
        sk = self.line_block_sketch(self.new_doc("PanelExp"), placements=((0, 0, 0), (6, 0, 0.2), (12, 0, -0.4)))
        g = B.blocks(sk)["instances"]
        sk.addConstraint(Sketcher.Constraint("Coincident", g["T_1"]["geo_ids"][1], 2, g["T_2"]["geo_ids"][0], 1))
        sk.renameConstraint(len(sk.Constraints) - 1, "tie12")
        sk.addConstraint(Sketcher.Constraint("Horizontal", g["T_3"]["geo_ids"][0]))
        sk.renameConstraint(len(sk.Constraints) - 1, "hor3")
        sk.solve()
        twin = self.twin(sk)
        self.open_panel(sk)
        count = sk.Document.UndoCount
        self.choose("inst", "T_3")
        self.click_button("blocksExplode")
        self.assertEqual(sk.Document.UndoCount, count + 1)
        B.explode(twin, "T_3")
        self.assertEqual(_content(sk)["Geometry"], _content(twin)["Geometry"])
        self.assertEqual(sk.BlockData, twin.BlockData)
        self.assertPanelEqualsApi(sk)
        before_t1 = [C.member_numbers(sk.Geometry[i]) for i in B.blocks(sk)["instances"]["T_1"]["geo_ids"]]
        snap = _snapshot(sk)
        self.choose("inst", "T_2")
        self.click_button("blocksDelete")
        self.assertIn("tie12", self.status())
        self.assertEqual(sk.Document.UndoCount, snap["undo"] + 1)
        self.assertNotIn("T_2", B.blocks(sk)["instances"])
        self.assertEqual([c.Name for c in sk.Constraints if c.Name], ["hor3"])
        after_t1 = [C.member_numbers(sk.Geometry[i]) for i in B.blocks(sk)["instances"]["T_1"]["geo_ids"]]
        for (_, a), (_, b) in zip(before_t1, after_t1):
            for x, y in zip(a, b):
                self.assertAlmostEqual(x, y, delta=1e-9)
        self.assertPanelEqualsApi(sk)
        after = _snapshot(sk)
        sk.Document.undo()
        self.settle()
        self.assertSameSketch(sk, snap)
        sk.Document.redo()
        self.settle()
        self.assertSameSketch(sk, after)
        self.assertPanelEqualsApi(sk)

    def test_deleting_a_definition_is_refused_while_it_has_instances(self):
        sk = self.line_block_sketch(self.new_doc("PanelDelDef"))
        self.open_panel(sk)
        self.choose("def", "T")
        snap = _snapshot(sk)
        self.click_button("blocksDelete")
        self.assertIn("2 instances", self.status())
        self.assertEqual(_snapshot(sk), snap)
        for n in ("T_1", "T_2"):
            self.choose("inst", n)
            self.click_button("blocksExplode")
        self.choose("def", "T")
        count = sk.Document.UndoCount
        self.click_button("blocksDelete")
        self.assertEqual(sk.Document.UndoCount, count + 1)
        self.assertEqual(B.blocks(sk)["definitions"], {})
        self.assertPanelEqualsApi(sk)
        sk.Document.undo()
        self.settle()
        self.assertEqual(list(B.blocks(sk)["definitions"]), ["T"])
        self.assertPanelEqualsApi(sk)


# ---------------------------------------------------------------------------------------------------------------------
# Check 4: insert by click
# ---------------------------------------------------------------------------------------------------------------------


class TestBlocksPanelClick(BlocksGuiCase):
    def prepare(self, sk):
        self.open_panel(sk)
        self.choose("def", "T")
        _widget("blocksAngle").setText("0 deg")
        return self.viewport()

    def place_mode(self):
        self.choose("def", "T")
        _widget("blocksPlace").click()
        self.settle()
        self.assertEqual(self.panel._placing, "T")

    def assertNoMode(self):
        self.assertIsNone(self.panel._placing if self.panel else None)
        self.assertEqual(self.P.active_filter_count(), 0)

    def stock_point_at(self, sk, pos):
        """The oracle for a click: the stock Create point tool, given the same real press and release at the same widget
        pixel, creates a point; that point's sketch coordinates are what a click means.  The point is removed again."""
        gl = self.viewport()
        n0, c0 = len(sk.Geometry), len(sk.Constraints)
        Gui.runCommand("Sketcher_CreatePoint")
        _pump(120)
        self.click_at(gl, pos)
        made = [sk.Geometry[i] for i in range(n0, len(sk.Geometry))]
        self.assertEqual([g.TypeId for g in made], ["Part::GeomPoint"], "the stock tool made no point at that pixel")
        snapped = len(sk.Constraints) != c0  # near an axis or a line the tool adds a constraint and moves the point
        point = (made[0].X, made[0].Y)
        for t in (QtCore.QEvent.KeyPress, QtCore.QEvent.KeyRelease):  # the tool is continuous; one Escape leaves it (a second would leave the editor)
            QtWidgets.QApplication.sendEvent(gl, QtGui.QKeyEvent(t, QtCore.Qt.Key_Escape, QtCore.Qt.NoModifier))
        _pump(60)
        self.assertEqual(_editing(), (sk.Document.Name, sk.Name))
        sk.Document.openTransaction("remove oracle point")
        sk.delGeometries(list(range(n0, len(sk.Geometry))))
        sk.Document.commitTransaction()
        self.settle()
        self.assertEqual((len(sk.Geometry), len(sk.Constraints)), (n0, c0))
        return None if snapped else point

    def click_places(self, sk, fraction):
        """One click at the widget pixel `fraction` (of the viewport's width and height) places the instance where the
        stock Create point tool puts a point for the same pixel, at the device pixel ratio the process runs at."""
        gl = self.viewport()
        pos = QtCore.QPointF(round(gl.width() * fraction[0]), round(gl.height() * fraction[1]))
        want = self.stock_point_at(sk, pos)
        for nudge in range(1, 6):  # a pixel next to an axis or a line is snapped by the stock tool: move on over empty space
            if want is not None:
                break
            pos = QtCore.QPointF(pos.x() + 37, pos.y() - 23)
            want = self.stock_point_at(sk, pos)
        self.assertIsNotNone(want, ("no pixel over empty space", self.kind))
        self.place_mode()
        n = len(B.blocks(sk)["instances"])
        sel_before = _selected_names()
        undo = sk.Document.UndoCount
        self.click_at(gl, pos)
        self.assertEqual(len(B.blocks(sk)["instances"]), n + 1)
        self.assertEqual(sk.Document.UndoCount, undo + 1)
        self.assertEqual(_selected_names(), sel_before)
        self.assertNoMode()
        g = sk.Geometry[B.blocks(sk)["instances"][_latest(sk)]["geo_ids"][0]]
        # the stock tool's own mapping is the truth; a device pixel is far more than this (about 0.005 mm at these zooms)
        self.assertAlmostEqual(g.StartPoint.x, want[0], delta=1e-4, msg=("x", self.kind, pos.x(), pos.y(), gl.devicePixelRatioF()))
        self.assertAlmostEqual(g.StartPoint.y, want[1], delta=1e-4, msg=("y", self.kind, pos.x(), pos.y(), gl.devicePixelRatioF()))
        return g.StartPoint.x, g.StartPoint.y

    def body_sketch(self, doc):
        body = doc.addObject("PartDesign::Body", "Body")
        body.Placement = FreeCAD.Placement(V(7, 8, 9), FreeCAD.Rotation(V(0, 1, 1), 50))
        sk = body.newObject("Sketcher::SketchObject", "A")
        xy = [f for f in body.Origin.OriginFeatures if f.Role == "XY_Plane"][0]
        sk.AttachmentSupport = (xy, [""])
        sk.MapMode = "FlatFace"
        sk.AttachmentOffset = FreeCAD.Placement(V(2, 3, 4), FreeCAD.Rotation(V(1, 0, 0), 20))
        doc.recompute()
        sk.addGeometry([Part.LineSegment(V(0, 0, 0), V(4, 0, 0)), Part.LineSegment(V(4, 0, 0), V(4, 3, 0))], False)
        B.define(sk, "T", [0, 1])
        sk.delGeometries([0, 1])
        doc.recompute()
        return sk

    def test_one_click_places_at_that_point_on_every_kind_of_sketch(self):
        kinds = (
            ("xy", lambda doc: self.line_block_sketch(doc, "A", placements=((0, 0, 0),))),
            ("rotated", lambda doc: self.line_block_sketch(doc, "A", placements=((0, 0, 0),), placement=FreeCAD.Placement(V(10, -5, 3), FreeCAD.Rotation(V(1, 2, 3), 35)))),
            ("body", self.body_sketch),
        )
        for label, make in kinds:
            for top in (True, False):
                self.kind = (label, "top" if top else "iso")
                doc = self.new_doc("PanelClick%s%d" % (label, top))
                sk = make(doc)
                self.open_panel(sk)
                self.frame(sk, top)
                self.choose("def", "T")
                _widget("blocksAngle").setText("0 deg")
                self.click_places(sk, (0.62, 0.37))
                self.click_places(sk, (0.27, 0.68))
                self.close_panel()
                Gui.ActiveDocument.resetEdit()
                _pump(50)

    def test_the_click_places_with_the_angle_field(self):
        self.kind = ("xy", "angle")
        doc = self.new_doc("PanelClickAngle")
        sk = self.line_block_sketch(doc, "A", placements=((0, 0, 0),))
        gl = self.prepare(sk)
        self.frame(sk)
        _widget("blocksAngle").setText("90 deg")
        x, y = self.click_places(sk, (0.62, 0.37))
        pose = B.instance_placement(sk, _latest(sk))
        self.assertAlmostEqual(pose[2], math.pi / 2, delta=1e-7)
        self.assertAlmostEqual(pose[0], x, delta=1e-7)

    def test_every_way_out_of_the_mode_changes_nothing_and_frees_the_next_click(self):
        doc = self.new_doc("PanelCancel")
        sk = self.line_block_sketch(doc, "A", placements=((0, 0, 0),))
        gl = self.prepare(sk)
        self.frame(sk)
        gids = B.blocks(sk)["instances"]["T_1"]["geo_ids"]  # two lines and a circle at the origin, unturned
        spots = [((2.0, 0.0), "Edge%d" % (gids[0] + 1)), ((4.0, 1.5), "Edge%d" % (gids[1] + 1)), ((1.5, 1.5), "Edge%d" % (gids[2] + 1)), ((2.0, 0.0), "Edge%d" % (gids[0] + 1))]
        Gui.Selection.clearSelection()
        snap = _snapshot(sk)

        def esc():
            for t in (QtCore.QEvent.KeyPress, QtCore.QEvent.KeyRelease):
                QtWidgets.QApplication.sendEvent(gl, QtGui.QKeyEvent(t, QtCore.Qt.Key_Escape, QtCore.Qt.NoModifier))

        def freed(k, label):
            """The next click selects what a click selects with no panel going on: the edge under it, nothing more."""
            (x, y), expected = spots[k]
            self.click_at(gl, self.pixel_of(sk, x, y))
            self.assertEqual(_selected_names(), [expected], label + ": the next click selects as without the panel")
            Gui.Selection.clearSelection()
            self.send_mouse(gl, QtCore.QEvent.MouseMove, QtCore.QPointF(5, 5), QtCore.Qt.NoButton)
            _pump(30)
            self.assertEqual(sk.Document.UndoCount, snap["undo"], label)

        on_line = self.pixel_of(sk, 4.0, 1.5)
        self.place_mode()
        esc()
        self.settle()
        self.assertNoMode()
        self.assertEqual(_snapshot(sk), snap, "escape")
        freed(0, "escape")
        self.place_mode()
        self.click_at(gl, on_line, QtCore.Qt.RightButton)
        self.assertNoMode()
        self.assertEqual(_snapshot(sk), snap, "right click")
        freed(1, "right click")
        self.place_mode()
        self.close_panel()
        self.assertEqual(self.P.active_filter_count(), 0)
        self.assertEqual(_snapshot(sk), snap, "close")
        freed(2, "closing the panel")
        self.open_panel(sk)
        self.choose("def", "T")
        self.place_mode()
        Gui.ActiveDocument.resetEdit()
        _wait(lambda: self.P.current_panel() is None)
        self.assertEqual(self.P.active_filter_count(), 0)
        self.assertEqual(_content(sk), snap["content"])  # (the editor's own open and close make undo entries)
        self.assertEqual(sk.BlockData, snap["block"])
        self.panel = None

    def test_the_click_does_not_also_select_and_navigation_still_works(self):
        doc = self.new_doc("PanelMode")
        sk = self.line_block_sketch(doc, "A", placements=((0, 0, 0),))
        gl = self.prepare(sk)
        self.frame(sk)
        spot = self.pixel_of(sk, 4.0, 1.5)  # exactly on an existing line
        def spin():
            wheel = QtGui.QWheelEvent(QtCore.QPointF(spot), QtCore.QPointF(gl.mapToGlobal(spot.toPoint())), QtCore.QPoint(0, 0), QtCore.QPoint(0, 120), QtCore.Qt.NoButton, QtCore.Qt.NoModifier, QtCore.Qt.NoScrollPhase, False)
            QtWidgets.QApplication.sendEvent(gl.parentWidget(), wheel)  # the wheel goes to the view widget itself
            _pump(100)

        cam = self.camera(sk)
        spin()
        self.assertNotEqual(self.camera(sk), cam, "control: the wheel zooms with no placement going on")
        spin()
        self.place_mode()
        cam = self.camera(sk)
        spin()
        self.assertNotEqual(self.camera(sk), cam, "the wheel still zooms during the placement")
        self.assertEqual(self.panel._placing, "T")
        taken = []
        real = self.panel.on_view_event
        self.panel.on_view_event = lambda ev: taken.append(real(ev)) or taken[-1]
        self.send_mouse(gl, QtCore.QEvent.MouseButtonPress, spot, QtCore.Qt.MiddleButton, QtCore.Qt.MiddleButton)
        self.send_mouse(gl, QtCore.QEvent.MouseMove, spot + QtCore.QPointF(10, 10), QtCore.Qt.NoButton, QtCore.Qt.MiddleButton)
        self.send_mouse(gl, QtCore.QEvent.MouseButtonRelease, spot + QtCore.QPointF(10, 10), QtCore.Qt.MiddleButton, QtCore.Qt.NoButton)
        self.assertTrue(taken and not any(taken), taken)
        self.assertEqual(self.panel._placing, "T")
        self.panel.on_view_event = real
        spot = self.pixel_of(sk, 4.0, 1.5)  # the wheel moved the picture
        old = _content(sk)["Geometry"]
        old_members = [C.member_numbers(sk.Geometry[i]) for i in B.blocks(sk)["instances"]["T_1"]["geo_ids"]]
        Gui.Selection.clearSelection()
        self.click_at(gl, spot)
        self.assertEqual(_selected_names(), [], "the placing click also selected")
        self.assertNoMode()
        self.assertEqual(sorted(B.blocks(sk)["instances"]), ["T_1", "T_2"])
        now = [C.member_numbers(sk.Geometry[i]) for i in B.blocks(sk)["instances"]["T_1"]["geo_ids"]]
        for (_, a), (_, b) in zip(old_members, now):
            for x, y in zip(a, b):
                self.assertAlmostEqual(x, y, delta=1e-9)
        self.assertNotEqual(old, _content(sk)["Geometry"])

    def test_a_stock_tool_started_during_the_mode_ends_it(self):
        doc = self.new_doc("PanelTool")
        sk = self.line_block_sketch(doc, "A", placements=((0, 0, 0),))
        gl = self.prepare(sk)
        self.frame(sk)
        self.place_mode()
        Gui.runCommand("Sketcher_CreateLine")
        _pump(150)
        self.assertNoMode()
        self.assertEqual(len(B.blocks(sk)["instances"]), 1)
        Gui.runCommand("Sketcher_CreateLine")  # leaves the tool again
        _pump(50)


# ---------------------------------------------------------------------------------------------------------------------
# Check 5: selection both ways
# ---------------------------------------------------------------------------------------------------------------------


class TestBlocksPanelSelection(BlocksGuiCase):
    def mixed_sketch(self, doc):
        sk = doc.addObject("Sketcher::SketchObject", "M")
        sk.addGeometry([Part.LineSegment(V(0, 0, 0), V(4, 0, 0)), Part.Point(V(1, 2, 0)), Part.ArcOfCircle(Part.Circle(V(2, 1, 0), V(0, 0, 1), 1.0), 0.3, 2.0), Part.Circle(V(3, 3, 0), V(0, 0, 1), 0.4)], False)
        B.define(sk, "M", [0, 1, 2, 3])
        sk.delGeometries([0, 1, 2, 3])
        for p in ((0, 0, 0), (10, 0, 0.3), (20, 5, 1.0)):
            B.insert(sk, "M", *p)
        sk.addGeometry(Part.LineSegment(V(-5, -5, 0), V(30, -5, 0)), False)
        doc.recompute()
        return sk

    def expected_names(self, sk, inst):
        """The editor's names for an instance's members, written out here from the sketch's own vertex table."""
        names = []
        vertex = {}
        i = 0
        while True:
            g, p = sk.getGeoVertexIndex(i)
            if g == -2000:
                break
            vertex[(g, p)] = i
            i += 1
        for gid in B.blocks(sk)["instances"][inst]["geo_ids"]:
            if sk.Geometry[gid].TypeId == "Part::GeomPoint":
                names.append("Vertex%d" % (vertex[(gid, 1)] + 1))
            else:
                names.append("Edge%d" % (gid + 1))
        return sorted(names)

    def test_choosing_a_row_selects_exactly_its_members_with_the_right_names(self):
        sk = self.mixed_sketch(self.new_doc("PanelSel"))
        self.open_panel(sk)
        snap = _snapshot(sk)
        for inst in ("M_1", "M_2", "M_3"):
            self.choose("inst", inst)
            self.assertEqual(sorted(_selected_names()), self.expected_names(sk, inst), inst)
        self.assertEqual(_snapshot(sk), snap, "selecting touched the document")
        # a GeoId shift: delete the first instance, then choose the second again
        self.choose("inst", "M_1")
        self.click_button("blocksDelete")
        Gui.Selection.clearSelection()  # the names of the deleted members mean other geometry now
        self.settle()
        self.choose("inst", "M_2")
        self.assertEqual(sorted(_selected_names()), self.expected_names(sk, "M_2"))
        self.assertNotIn("M_1", B.blocks(sk)["instances"])
        # the last row chosen still selects members only of its own instance
        for n in _selected_names():
            self.assertTrue(n.startswith(("Edge", "Vertex")))

    def test_selecting_a_member_in_the_view_chooses_its_row_and_plain_geometry_clears_it(self):
        sk = self.mixed_sketch(self.new_doc("PanelSelBack"))
        self.open_panel(sk)
        snap = _snapshot(sk)
        self.choose("def", "M")
        for inst in ("M_3", "M_1"):
            gid = B.blocks(sk)["instances"][inst]["geo_ids"][2]
            self.select(sk, "Edge%d" % (gid + 1))
            item = self.panel.tree.selectedItems()
            self.assertEqual(len(item), 1, inst)
            self.assertTrue(item[0].text(0).startswith(inst + ":"), item[0].text(0))
        plain = len(sk.Geometry) - 1
        self.select(sk, "Edge%d" % (plain + 1))
        self.assertEqual(self.panel.tree.selectedItems(), [])
        self.assertEqual(_snapshot(sk), snap)

    def test_a_selection_the_sketch_no_longer_has_is_not_reselected(self):
        sk = self.mixed_sketch(self.new_doc("PanelSelGone"))
        self.open_panel(sk)
        self.choose("inst", "M_2")
        Gui.Selection.clearSelection()
        B.delete_instance(sk, "M_2")  # no turn yet: the row still names it
        self.panel._on_choice()
        self.assertEqual(_selected_names(), [])
        self.assertFalse(self.panel.select_instance("M_2"))
        self.settle()
        self.assertEqual(_selected_names(), [])
        self.assertPanelEqualsApi(sk)


# ---------------------------------------------------------------------------------------------------------------------
# Check 10: no effect on other work
# ---------------------------------------------------------------------------------------------------------------------


class TestBlocksPanelOthers(BlocksGuiCase):
    def test_nothing_remains_after_close_and_fifty_openings_leave_nothing_behind(self):
        sk = self.line_block_sketch(self.new_doc("PanelFifty"))
        self.edit(sk)
        import SketcherBlocksPanel as P

        def counts():
            QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
            return (
                len(sk.Document.Objects),
                len([w for w in QtWidgets.QApplication.allWidgets() if isinstance(w, QtWidgets.QDockWidget)]),
                len([w for w in QtWidgets.QApplication.allWidgets() if w.objectName().startswith("blocks")]),
                P.active_filter_count(),
                sk.Document.UndoCount,
            )

        Gui.runCommand(COMMAND)
        _pump(30)
        P.current_panel().close()
        _pump(50)
        base = counts()
        for _ in range(50):
            Gui.runCommand(COMMAND)
            _pump()
            p = P.current_panel()
            p.start_place() if p.tree.topLevelItemCount() and p._choose_row("def", "T") else None
            p.close()
            _pump()
        _pump(80)
        self.assertEqual(counts(), base)
        # no observer remains: every entry point of the panel is spied on
        Gui.runCommand(COMMAND)
        _pump(30)
        p = P.current_panel()
        calls = []
        for n in ("on_selection", "on_property", "on_undo_redo", "invalidate", "schedule", "data_changed", "on_view_event", "on_cursor_change"):
            setattr(p, n, lambda *a, _n=n, **k: calls.append(_n))
        doc = sk.Document
        Gui.Selection.addSelection(doc.Name, sk.Name, "Edge1")
        B.insert(sk, "T", 30, 30, 0)
        doc.undo()
        _pump(40)
        self.assertTrue({"on_selection", "on_property", "on_undo_redo"} <= set(calls), calls)
        p.close()
        _pump(40)
        calls.clear()
        Gui.Selection.addSelection(doc.Name, sk.Name, "Edge2")
        Gui.Selection.clearSelection()
        B.insert(sk, "T", 30, 30, 0)
        doc.undo()
        Gui.ActiveDocument.resetEdit()
        _pump(40)
        self.assertEqual(calls, [])

    def test_the_other_docks_work_with_the_panel_open_and_it_follows_their_fixes(self):
        import SketcherDiagnosisPanel as DPm
        import SketcherFullyDefinePanel as FPm
        import SketcherRelationsPanel as RPm

        doc = self.new_doc("PanelOthers")
        sk = self.line_block_sketch(doc, "S", placements=((0, 0, 0), (8, 0, 0.3)))
        a = sk.addGeometry(Part.LineSegment(V(-10, -10, 0), V(-2, -10, 0)), False)
        b = sk.addGeometry(Part.LineSegment(V(-2, -10, 0), V(-2, -3, 0)), False)
        sk.addConstraint(Sketcher.Constraint("Horizontal", a))
        sk.addConstraint(Sketcher.Constraint("Horizontal", a))  # a redundancy for the diagnosis to fix
        sk.addConstraint(Sketcher.Constraint("Coincident", a, 2, b, 1))
        sk.solve()
        self.open_panel(sk)
        snap_blocks = B.blocks(sk)
        # diagnosis
        Gui.runCommand("Sketcher_DiagnoseSketch")
        _pump(50)
        dp = DPm.current_panel()
        self.assertTrue(_wait(lambda: dp.state in ("ready", "failed", "closed")))
        self.assertEqual(dp.state, "ready", dp.status.text())
        fix = None
        stack = [dp.tree.topLevelItem(k) for k in range(dp.tree.topLevelItemCount())]
        while stack and fix is None:
            it = stack.pop(0)
            if (it.data(0, QtCore.Qt.UserRole) or {}).get("kind") in ("fix", "removal"):
                fix = it
            stack += [it.child(j) for j in range(it.childCount())]
        self.assertIsNotNone(fix, "the diagnosis offered no fix")
        dp.tree.setCurrentItem(fix)
        _pump()
        n_before = len(sk.Constraints)
        self.assertTrue(dp.apply_chosen())
        self.settle()
        self.assertLess(len(sk.Constraints), n_before)
        self.assertEqual(B.blocks(sk), snap_blocks)
        self.assertPanelEqualsApi(sk, "after a diagnosis fix")
        dp.close()
        _pump(50)
        # fully define
        Gui.runCommand("Sketcher_FullyDefine")
        _pump(50)
        fp = FPm.current_panel()
        self.assertTrue(_wait(lambda: fp.state in ("ready", "failed", "closed")))
        self.assertEqual(fp.state, "ready", fp.status.text())
        cand = None
        for k in range(fp.tree.topLevelItemCount()):
            if (fp.tree.topLevelItem(k).data(0, QtCore.Qt.UserRole) or {}).get("kind") == "candidate":
                cand = fp.tree.topLevelItem(k)
                break
        self.assertIsNotNone(cand, "Fully Define offered no candidate")
        fp.tree.setCurrentItem(cand)
        _pump()
        dof = sk.DoF
        self.assertTrue(fp.apply_chosen())
        self.settle()
        self.assertLess(sk.DoF, dof)
        self.assertEqual(B.blocks(sk), snap_blocks)
        self.assertPanelEqualsApi(sk, "after Fully Define")
        fp.close()
        _pump(50)
        # relations, with the panel still open
        Gui.runCommand("Sketcher_Relations")
        _pump(50)
        rp = RPm.current_panel()
        self.assertIsNotNone(rp)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(doc.Name, sk.Name, "Edge%d" % (a + 1))
        self.settle()
        self.assertGreater(rp.tree.topLevelItemCount(), 0)
        self.assertIs(self.P.current_panel(), self.panel)
        self.assertEqual(self.panel.state, "ready")
        self.assertPanelEqualsApi(sk, "with the relations dock")
        # the solver messages box of the edit task is still there
        self.assertIsNotNone(Gui.Control.activeDialog() or [w for w in QtWidgets.QApplication.allWidgets() if "TaskSketcherMessages" in w.metaObject().className()])
        rp.close()
        _pump(50)


# ---------------------------------------------------------------------------------------------------------------------
# Check 11: speed
# ---------------------------------------------------------------------------------------------------------------------


def _median(f, n=5):
    xs = []
    for _ in range(n):
        t = time.perf_counter()
        f()
        xs.append(time.perf_counter() - t)
    return statistics.median(xs)


class TestBlocksPanelSpeed(BlocksGuiCase):
    def big(self, name, instances=200, members=10):
        doc = self.new_doc(name)
        sk = doc.addObject("Sketcher::SketchObject", "Big")
        sk.addGeometry([Part.LineSegment(V(i, 0, 0), V(i + 1, 0.5 * ((-1) ** i), 0)) for i in range(members)], False)
        B.define(sk, "L", list(range(members)))
        sk.delGeometries(list(range(members)))
        t = time.perf_counter()
        for k in range(instances):
            B.insert(sk, "L", (k % 20) * 14.0, (k // 20) * 4.0, 0.0)
        self.build_seconds = time.perf_counter() - t
        doc.recompute()
        return sk

    def test_open_refresh_select_and_drag_at_200_by_10(self):
        sk = self.big("PanelSpeed")
        self.edit(sk)
        import SketcherBlocksPanel as P

        def open_close():
            p = P.BlocksPanel(sk, Gui.getMainWindow())
            p.close()

        t_open = _median(open_close)
        Gui.runCommand(COMMAND)
        _pump(50)
        self.panel = P.current_panel()
        self.assertEqual(len(B.blocks(sk)["instances"]), 200)
        self.assertPanelEqualsApi(sk)
        t_refresh = _median(self.panel.refresh)
        names = ["L_%d" % k for k in (1, 50, 100, 150, 200)]
        rows = iter(names)

        def select():
            self.choose("inst", next(rows))

        # choosing includes the settle turns of the helper; time the selection itself
        def select_only():
            n = next(rows2)
            item = [i for i in self._items() if (i.data(0, QtCore.Qt.UserRole) or {}).get("name") == n][0]
            self.panel.tree.setCurrentItem(item)
            item.setSelected(True)

        rows2 = iter(names)
        t_select = _median(select_only)
        # a refresh after an insert (the insert itself timed apart)
        t0 = time.perf_counter()
        B.insert(sk, "L", 300, 300, 0)
        t_api = time.perf_counter() - t0
        t = time.perf_counter()
        self.panel.refresh()
        t_after_insert = time.perf_counter() - t
        # the same insert through the panel
        self.choose("def", "L")
        _widget("blocksX").setText("320 mm")
        _widget("blocksY").setText("320 mm")
        t0 = time.perf_counter()
        _widget("blocksInsert").click()
        t_panel_insert = time.perf_counter() - t0
        print(
            "BLOCKS PANEL 200x10: open %.0f ms, refresh %.0f ms, refresh after insert %.0f ms, select row %.0f ms; insert via API %.0f ms, via panel %.0f ms (build of 200 inserts %.1f s)"
            % (1000 * t_open, 1000 * t_refresh, 1000 * t_after_insert, 1000 * t_select, 1000 * t_api, 1000 * t_panel_insert, self.build_seconds)
        )
        for label, v in (("open", t_open), ("refresh", t_refresh), ("refresh after insert", t_after_insert), ("select", t_select)):
            self.assertLess(v, SPEED_BAR, label)
        # a drag move: the panel must not refresh on every move
        g = B.blocks(sk)["instances"]["L_7"]["geo_ids"][0]
        pos = sk.Geometry[g].StartPoint
        moves = 40

        def drag():
            for k in range(moves):
                sk.moveGeometry(g, 1, V(pos.x + 0.01 * (k + 1), pos.y + 0.005 * (k + 1), 0), False)
            sk.moveGeometry(g, 1, V(pos.x, pos.y, 0), False)

        refreshes = []
        real = self.panel.refresh
        self.panel.refresh = lambda *a, **k: refreshes.append(1) or real(*a, **k)
        times_open, times_closed = [], []
        for rep in range(3):
            t = time.perf_counter()
            drag()
            times_open.append((time.perf_counter() - t) / (moves + 1))
            _pump()
            _pump()
        self.assertEqual(refreshes, [], "a drag refreshed the panel")
        self.panel.refresh = real
        self.close_panel()
        _pump(50)
        for rep in range(3):
            t = time.perf_counter()
            drag()
            times_closed.append((time.perf_counter() - t) / (moves + 1))
        extra = statistics.median(times_open) - statistics.median(times_closed)
        print("BLOCKS PANEL drag move: open %.3f ms, closed %.3f ms, extra %.3f ms" % (1000 * statistics.median(times_open), 1000 * statistics.median(times_closed), 1000 * extra))
        self.assertLess(extra, 0.001)

    def test_finish_with_200_instances_is_reported(self):
        sk = self.big("PanelSpeedFinish", instances=200, members=10)
        self.open_panel(sk)
        self.choose("inst", "L_1")
        t0 = time.perf_counter()
        _widget("blocksEdit").click()
        t_start = time.perf_counter() - t0
        self.assertIsNotNone(self.panel.session)
        sk.Document.openTransaction("Drag")
        sk.moveGeometry(B.blocks(sk)["instances"] and 0, 2, V(0.4, 0.3, 0), False)
        sk.Document.commitTransaction()
        self.settle()
        t0 = time.perf_counter()
        _widget("blocksFinish").click()
        t_finish = time.perf_counter() - t0
        self.assertIsNone(self.panel.session, self.status())
        self.assertEqual(len(B.blocks(sk)["instances"]), 200, self.status())
        print("BLOCKS PANEL Finish on 200 instances of 10: start %.2f s, finish %.2f s; %s" % (t_start, t_finish, self.status()))

    def _items(self):
        out = []
        root = self.panel.tree.invisibleRootItem()
        stack = [root.child(i) for i in range(root.childCount())]
        while stack:
            it = stack.pop()
            out.append(it)
            stack += [it.child(i) for i in range(it.childCount())]
        return out


# ---------------------------------------------------------------------------------------------------------------------
# Check 12: reachable and clean
# ---------------------------------------------------------------------------------------------------------------------


class TestBlocksPanelReachable(BlocksGuiCase):
    def test_the_command_is_registered_active_only_in_the_editor_with_an_icon(self):
        self.assertIn(COMMAND, Gui.listCommands())
        cmd = Gui.Command.get(COMMAND)
        doc = self.new_doc("PanelCmd")
        sk = self.line_block_sketch(doc)
        Gui.ActiveDocument = Gui.getDocument(doc.Name)
        if Gui.ActiveDocument.getInEdit() is not None:
            Gui.ActiveDocument.resetEdit()
        _pump(30)
        self.assertFalse(cmd.isActive())
        self.edit(sk)
        self.assertTrue(cmd.isActive())
        actions = [a for a in Gui.getMainWindow().findChildren(QtGui.QAction) if a.objectName() == COMMAND]
        self.assertTrue(actions)
        self.assertFalse(actions[0].icon().isNull(), "the command has no icon")
        self.assertTrue(actions[0].text())

    def test_the_command_is_in_the_sketch_menu_and_the_palette_index(self):
        found = []

        def walk(menu, path):
            for a in menu.actions():
                if a.menu() is not None:
                    walk(a.menu(), path + [a.text()])
                elif a.objectName() == COMMAND:
                    found.append(path)

        for a in Gui.getMainWindow().menuBar().actions():
            if a.menu() is not None:
                walk(a.menu(), [a.text()])
        self.assertTrue(any("Sketch" in p[0].replace("&", "") for p in found), found)
        import json

        import AgentCore.palette as pal

        path = os.path.join(os.path.dirname(pal.__file__), "commands_index.json")
        names = {c["name"]: c for c in json.load(open(path))["commands"]}
        self.assertIn(COMMAND, names)
        self.assertEqual(names[COMMAND]["wb"], "SketcherWorkbench")

    def test_every_visible_string_goes_through_tr(self):
        """No literal text is handed to a label, button, tooltip or status line without tr(); a number-only or empty
        string and a unit default are allowed."""
        import SketcherBlocksPanel as P

        tree = ast.parse(open(P.__file__).read())
        sinks = {"QLabel", "QPushButton", "setText", "setToolTip", "setAccessibleName", "setPlaceholderText", "setWindowTitle", "addRow", "QDockWidget"}
        bad = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                if name in sinks:
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            if arg.value.strip() and not re.fullmatch(r"[0-9.\- ]*( mm| deg)?", arg.value):
                                bad.append((node.lineno, arg.value))
        self.assertEqual(bad, [])


# ---------------------------------------------------------------------------------------------------------------------
# Checks 7, 8 and 9: edit in place
# ---------------------------------------------------------------------------------------------------------------------

TOL = 1e-7


def _numbers_of(sk):
    return [C.member_numbers(g) for g in sk.Geometry]


def _cons_of(sk):
    return [(c.Type, c.First, c.FirstPos, c.Second, c.SecondPos, c.Third, c.ThirdPos, c.Name, round(c.Value, 9)) for c in sk.Constraints]


class EditCase(BlocksGuiCase):
    """Block W (a line, a half circle and a small circle) placed three times; W_1 is tied to the origin."""

    def scene(self, name, bare=False):
        doc = self.new_doc(name)
        sk = doc.addObject("Sketcher::SketchObject", "W")
        sk.addGeometry(
            [
                Part.LineSegment(V(0, 0, 0), V(5, 0, 0)),
                Part.ArcOfCircle(Part.Circle(V(2.5, 0, 0), V(0, 0, 1), 2.5), 0.0, math.pi),
                Part.Circle(V(2.5, 1.2, 0), V(0, 0, 1), 0.4),
            ],
            False,
        )
        B.define(sk, "W", [0, 1, 2])
        sk.delGeometries([0, 1, 2])
        for p in ((0, 0, 0), (12, 6, 0.4), (-8, 9, -0.6)):
            B.insert(sk, "W", *p)
        info = B.blocks(sk)["instances"]
        g1, g2 = info["W_1"]["geo_ids"], info["W_2"]["geo_ids"]
        P = sk.addGeometry(Part.LineSegment(V(-20, -3, 0), V(30, -3, 0)), False)
        Q = sk.addGeometry(Part.Point(V(2.5, 1.2, 0)), False)
        for c, name_ in () if bare else (
            (Sketcher.Constraint("Coincident", g1[0], 1, -1, 1), "orig"),
            (Sketcher.Constraint("Horizontal", g1[0]), "horiz"),
            (Sketcher.Constraint("Horizontal", P), "Ph"),
            (Sketcher.Constraint("PointOnObject", g2[0], 1, P), "onP"),
            (Sketcher.Constraint("Coincident", g1[2], 3, Q, 1), "cq"),
        ):
            sk.renameConstraint(sk.addConstraint(c), name_)
        sk.solve()
        doc.recompute()
        return sk

    def poses(self, sk):
        return {n: B.instance_placement(sk, n) for n in B.blocks(sk)["instances"]}

    def stock(self, sk, label, fn):
        """One stock operation: what a tool does, a transaction around some changes."""
        sk.Document.openTransaction(label)
        try:
            return fn()
        finally:
            sk.Document.commitTransaction()
            self.settle()

    def finish_accepting(self):
        """Finish as a user does when it names what it will undo and the user accepts: a second press."""
        self.click_button("blocksFinish")
        if self.panel.session is not None and "Press Finish again" in self.status():
            self.click_button("blocksFinish")

    def begin(self, sk, inst="W_1"):
        self.choose("inst", inst)
        self.click_button("blocksEdit")
        self.assertIsNotNone(self.panel.session, self.status())
        return self.panel.session

    def gids(self, sk, inst):
        return B.blocks(sk)["instances"][inst]["geo_ids"]

    def local_geo(self, sk, gid, pose):
        """A member's geometry in the frame of `pose`, by plain math written here (no call into the API's helpers)."""
        X, Y, th = pose
        g = sk.Geometry[gid]
        c, s = math.cos(-th), math.sin(-th)

        def pt(p):
            return V((p.x - X) * c - (p.y - Y) * s, (p.x - X) * s + (p.y - Y) * c, 0)

        if g.TypeId == "Part::GeomLineSegment":
            return Part.LineSegment(pt(g.StartPoint), pt(g.EndPoint))
        if g.TypeId == "Part::GeomCircle":
            return Part.Circle(pt(g.Center), V(0, 0, 1), g.Radius)
        if g.TypeId == "Part::GeomPoint":
            return Part.Point(pt(V(g.X, g.Y, 0)))
        a0, a1 = g.FirstParameter, g.LastParameter
        return Part.ArcOfCircle(Part.Circle(pt(g.Center), V(0, 0, 1), g.Radius), a0 - th, a1 - th)

    def run_case(self, label, ops, expect_removed=(), inst="W_1", bare=False):
        """The session `ops` on W_1, then Finish; compared with update_definition called directly on a twin with the
        changes the test reads from the session's end state."""
        sk = self.scene("PanelEdit" + label, bare)
        doc = sk.Document
        twin = doc.copyObject(sk)
        self.open_panel(sk)
        before_poses = self.poses(sk)
        known = {sk.getGeometryId(i) for i in range(len(sk.Geometry))}
        member_ids = {k: sk.getGeometryId(g) for k, g in zip(("m0000", "m0001", "m0002"), B.blocks(sk)["instances"][inst]["geo_ids"])}
        self.begin(sk, inst)
        pose = before_poses[inst]
        ops(sk)
        idmap = {sk.getGeometryId(i): i for i in range(len(sk.Geometry))}
        changes = {}
        for k, gid in member_ids.items():
            if gid not in idmap:
                changes[k] = None
            else:
                changes[k] = (self.local_geo(sk, idmap[gid], pose), bool(sk.getConstruction(idmap[gid])))
        new = [i for i in range(len(sk.Geometry)) if sk.getGeometryId(i) not in known]
        for n, i in enumerate(new):
            changes["n%04d" % (n + 1)] = (self.local_geo(sk, i, pose), bool(sk.getConstruction(i)))
        self.finish_accepting()
        self.assertIsNone(self.panel.session, "Finish did not finish: " + self.status())
        text = self.status()
        ref = B.update_definition(twin, "W", changes)
        self.assertEqual(sorted(ref["removed_constraints"]), sorted(expect_removed))
        for r in expect_removed:
            self.assertIn(r, text)
        after_poses = self.poses(sk)
        self.assertEqual(sorted(after_poses), ["W_1", "W_2", "W_3"])
        for n in after_poses:
            for a, b in zip(after_poses[n], before_poses[n]):
                self.assertAlmostEqual(a, b, delta=TOL, msg=n)
        self.assertEqual(len(sk.Geometry), len(twin.Geometry))
        for (ta, na), (tb, nb) in zip(_numbers_of(sk), _numbers_of(twin)):
            self.assertEqual(ta, tb)
            for x, y in zip(na, nb):
                self.assertAlmostEqual(x, y, delta=TOL)
        self.assertEqual(_cons_of(sk), _cons_of(twin))
        a, b = B._load(sk), B._load(twin)
        self.assertEqual(sorted(a["inst"]), sorted(b["inst"]))
        key = lambda m: (m["t"], m["c"], [round(x, 5) for x in m["g"]])
        ma, mb = sorted(a["defs"]["W"]["members"].values(), key=key), sorted(b["defs"]["W"]["members"].values(), key=key)
        self.assertEqual(len(ma), len(mb))
        for x, y in zip(ma, mb):
            self.assertEqual((x["t"], x["c"]), (y["t"], y["c"]))
            for u, v in zip(x["g"], y["g"]):
                self.assertAlmostEqual(u, v, delta=1e-9)
        for i in range(len(sk.Geometry)):
            self.assertEqual(bool(sk.getConstruction(i)), bool(twin.getConstruction(i)))
        self.assertEqual(sk.DoF, twin.DoF)
        newdef = a["defs"]["W"]["members"]
        for n, inst in a["inst"].items():
            world = B._instance_world(sk, a["defs"]["W"], inst)
            X, Y, th = after_poses[n]
            for k, nums in world.items():
                want = B._to_world(newdef[k]["t"], newdef[k]["g"], X, Y, th)
                cut = 3 if newdef[k]["t"] == "a" else len(want)
                for x, y in zip(nums[:cut], want[:cut]):
                    self.assertAlmostEqual(x, y, delta=TOL, msg=(n, k))
        return sk, text


class TestBlocksPanelEditResults(EditCase):
    def test_a_removed_first_member_shifts_the_ids_and_is_read_by_persistent_id(self):
        def ops(sk):
            self.stock(sk, "Delete", lambda: sk.delGeometry(0))  # the line: every id after it moves down

        sk, text = self.run_case("RemovedFirst", ops, expect_removed=["horiz", "onP", "orig"])
        self.assertIn("1 removed", text)

    def test_a_session_that_turns_the_first_member_keeps_the_start_placement(self):
        def ops(sk):
            self.stock(sk, "Drag", lambda: sk.moveGeometry(6, 2, V(-3.0, 8.0, 0), False))  # the end of W_3's line

        sk, text = self.run_case("Turned", ops, inst="W_3", bare=True)
        self.assertIn("1 changed", text)

    def test_a_moved_member(self):
        def ops(sk):
            self.stock(sk, "Drag", lambda: sk.moveGeometry(0, 2, V(7.0, 0, 0), False))  # the end of the line

        sk, text = self.run_case("Moved", ops)
        self.assertIn("1 changed", text)

    def test_an_arc_changed_member(self):
        def ops(sk):
            self.stock(sk, "Drag", lambda: sk.moveGeometry(1, 1, V(0.5, 1.5, 0), False))  # the arc's start point

            def datum():
                i = sk.addConstraint(Sketcher.Constraint("Radius", 1, 2.0))
                sk.setDatum(i, 3.1)

            self.stock(sk, "Radius", datum)

        sk, text = self.run_case("Arc", ops)
        self.assertIn("1 changed", text)
        self.assertIn("added during the edit were not kept", text)

    def test_an_added_member(self):
        def ops(sk):
            self.stock(sk, "Line", lambda: sk.addGeometry(Part.LineSegment(V(1.0, 1.0, 0), V(2.0, 3.0, 0)), False))

        sk, text = self.run_case("Added", ops)
        self.assertIn("1 added", text)
        for n in ("W_1", "W_2", "W_3"):
            self.assertEqual(len(B.blocks(sk)["instances"][n]["geo_ids"]), 4)

    def test_two_added_members_and_a_construction_flip(self):
        def ops(sk):
            self.stock(sk, "Lines", lambda: sk.addGeometry([Part.LineSegment(V(1.0, 1.0, 0), V(2.0, 3.0, 0)), Part.Point(V(4.0, 2.0, 0))], False))
            self.stock(sk, "Construction", lambda: sk.setConstruction(2, True))

        sk, text = self.run_case("Added2", ops)
        self.assertIn("2 added", text)
        for n in ("W_1", "W_2", "W_3"):
            ids = B.blocks(sk)["instances"][n]["geo_ids"]
            self.assertTrue(sk.getConstruction(ids[2]), n)

    def test_a_removed_member_returns_the_constraints_on_it_by_name(self):
        def ops(sk):
            self.stock(sk, "Delete", lambda: sk.delGeometry(2))  # the small circle, tied to Q by cq

        sk, text = self.run_case("Removed", ops, expect_removed=["cq"])
        self.assertIn("1 removed", text)
        for n in ("W_1", "W_2", "W_3"):
            self.assertEqual(len(B.blocks(sk)["instances"][n]["geo_ids"]), 2)

    def test_the_constraint_to_plain_geometry_on_a_surviving_member_survives(self):
        def ops(sk):
            self.stock(sk, "Drag", lambda: sk.moveGeometry(0, 2, V(6.0, 0, 0), False))

        sk, text = self.run_case("Survive", ops)
        onp = [c for c in sk.Constraints if c.Name == "onP"][0]
        self.assertEqual(onp.Type, "PointOnObject")
        self.assertEqual(B.blocks(sk)["instances"]["W_2"]["geo_ids"][0], onp.First)

    def test_a_session_that_changed_nothing_is_a_no_op(self):
        sk = self.scene("PanelEditNone")
        self.open_panel(sk)
        before = _snapshot(sk)
        self.begin(sk)
        self.click_button("blocksFinish")
        self.assertIsNone(self.panel.session)
        self.assertSameSketch(sk, before)
        self.assertEqual(sk.Document.UndoCount, before["undo"])
        self.assertIn("nothing had changed", self.status())


class TestBlocksPanelEditUndo(EditCase):
    def many_ops(self, sk):
        n = len(sk.Geometry)
        self.stock(sk, "Drag", lambda: sk.moveGeometry(0, 2, V(7.0, 0, 0), False))
        self.stock(sk, "Line", lambda: sk.addGeometry(Part.LineSegment(V(1.0, 1.0, 0), V(2.0, 3.0, 0)), False))
        self.stock(sk, "Point", lambda: sk.addGeometry(Part.Point(V(4.0, 2.0, 0)), False))
        self.stock(sk, "Construction", lambda: sk.setConstruction(n + 1, True))
        self.stock(sk, "Drag again", lambda: sk.moveGeometry(1, 1, V(0.5, 1.5, 0), False))
        self.stock(sk, "Delete", lambda: sk.delGeometry(2))

    def test_finish_is_one_undo_entry_and_redo_gives_the_result(self):
        sk = self.scene("PanelUndoF")
        self.open_panel(sk)
        before = _snapshot(sk)
        self.begin(sk)
        self.many_ops(sk)
        self.assertGreater(sk.Document.UndoCount, before["undo"] + 3)
        self.click_button("blocksFinish")
        self.assertIsNone(self.panel.session, self.status())
        after = _snapshot(sk)
        self.assertEqual(after["undo"], before["undo"] + 1)
        self.assertEqual(sk.Document.UndoNames[0], "Edit block definition")
        self.assertEqual(sk.Document.RedoCount, 0)
        sk.Document.undo()
        self.settle()
        self.assertSameSketch(sk, before)
        self.assertEqual(sk.Document.UndoCount, before["undo"])
        sk.Document.redo()
        self.settle()
        self.assertSameSketch(sk, after)
        self.assertEqual(sk.Document.UndoCount, before["undo"] + 1)
        self.assertPanelEqualsApi(sk)

    def test_cancel_restores_the_sketch_and_the_depth_with_redo_empty(self):
        sk = self.scene("PanelUndoC")
        self.open_panel(sk)
        before = _snapshot(sk)
        self.begin(sk)
        self.many_ops(sk)
        self.click_button("blocksCancel")
        self.assertIsNone(self.panel.session, self.status())
        self.assertSameSketch(sk, before)
        self.assertEqual(sk.Document.UndoCount, before["undo"])
        self.assertEqual(sk.Document.RedoCount, 0)
        self.assertEqual(sk.Document.UndoNames, before["names"])
        self.assertPanelEqualsApi(sk)
        self.assertIn("W_1", B.blocks(sk)["instances"])

    def test_an_empty_session_leaves_depth_and_redo_alone_on_both_ends(self):
        sk = self.scene("PanelUndoE")
        self.open_panel(sk)
        before = _snapshot(sk)
        for button in ("blocksCancel", "blocksFinish"):
            self.begin(sk)
            self.click_button(button)
            self.assertSameSketch(sk, before)
            self.assertEqual(sk.Document.UndoCount, before["undo"], button)
            self.assertEqual(sk.Document.RedoCount, 0, button)

    def test_a_refused_finish_stays_in_the_session_with_its_work_intact(self):
        sk = self.scene("PanelUndoR")
        g3 = self.gids(sk, "W_3")
        sk.addConstraint(Sketcher.Constraint("Distance", g3[0], 5.0))  # redundant while rigid; a longer line cannot hold
        sk.solve()
        self.open_panel(sk)
        before = _snapshot(sk)
        self.begin(sk)
        self.stock(sk, "Drag", lambda: sk.moveGeometry(0, 2, V(7.0, 0, 0), False))
        mid = _snapshot(sk)
        geo_mid = _numbers_of(sk)
        self.click_button("blocksFinish")
        self.assertIsNotNone(self.panel.session, "a refused Finish ended the session")
        self.assertIn("cannot be solved", self.status())
        self.assertEqual(sk.Document.UndoCount, mid["undo"])
        self.assertEqual(_numbers_of(sk), geo_mid)
        self.assertTrue(_widget("blocksFinish").isVisibleTo(self.panel.widget()))
        self.click_button("blocksCancel")
        self.assertIsNone(self.panel.session)
        self.assertSameSketch(sk, before)
        self.assertEqual(sk.Document.UndoCount, before["undo"])


class TestBlocksPanelEditRepairs(EditCase):
    """Repairs after the verifier's round: what Finish undoes is named first, a rigid move is the instance's, the undo
    depth is counted from the session's start, and the stack's limits are met with a sentence."""

    def plain_line(self, sk):
        return [c.First for c in sk.Constraints if c.Name == "Ph"][0]

    def constraint_names(self, sk):
        return [c.Name for c in sk.Constraints]

    def with_stack_limit(self, n):
        """Documents made while this is in force keep at most `n` undo entries (the limit is read when a document is made)."""
        grp = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Document")
        old = grp.GetInt("MaxUndoSize", 20)
        grp.SetInt("MaxUndoSize", n)
        self.addCleanup(grp.SetInt, "MaxUndoSize", old)

    # -- item 1 --------------------------------------------------------------------------------------------------------
    def test_edits_outside_the_block_are_named_before_finish_and_undone_by_the_second_press(self):
        sk = self.scene("RepOutside")
        self.open_panel(sk)
        P = self.plain_line(sk)
        start = _numbers_of(sk)
        start_cons = _cons_of(sk)
        self.begin(sk)
        self.stock(sk, "move plain", lambda: sk.moveGeometry(P, 1, V(-25.0, -3.0, 0), False))
        self.stock(sk, "del Ph", lambda: sk.delConstraint(self.constraint_names(sk).index("Ph")))
        moved = _numbers_of(sk)
        depth = sk.Document.UndoCount
        self.click_button("blocksFinish")
        self.assertIsNotNone(self.panel.session, "the first Finish went on: " + self.status())
        text = self.status()
        self.assertRegex(text, r"^Finish will undo \d+ moved elements? \(Edge\d+.*\) and 1 deleted constraint \('Ph'\) that are not part of the block\. Press Finish again to accept, or Cancel\.$")
        self.assertEqual(_numbers_of(sk), moved, "the warning changed the sketch")
        self.assertEqual(sk.Document.UndoCount, depth)
        self.assertNotIn("Ph", self.constraint_names(sk))
        self.click_button("blocksFinish")
        self.assertIsNone(self.panel.session, self.status())
        after = self.status()
        self.assertNotIn("nothing had changed", after)
        self.assertIn("Undone because they are not part of the block", after)
        self.assertIn("1 deleted constraint ('Ph')", after)
        self.assertRegex(after, r"moved elements? \(Edge")
        self.assertEqual(_cons_of(sk), start_cons, "the deleted constraint is back")
        for (ta, na), (tb, nb) in zip(_numbers_of(sk), start):
            for x, y in zip(na, nb):
                self.assertAlmostEqual(x, y, delta=TOL)

    def test_a_further_change_after_the_warning_asks_again_and_cancel_still_restores(self):
        sk = self.scene("RepRearm")
        self.open_panel(sk)
        P = self.plain_line(sk)
        before = _snapshot(sk)
        self.begin(sk)
        self.stock(sk, "move plain", lambda: sk.moveGeometry(P, 1, V(-25.0, -3.0, 0), False))
        self.click_button("blocksFinish")
        self.assertIn("Press Finish again", self.status())
        self.stock(sk, "move plain again", lambda: sk.moveGeometry(P, 2, V(40.0, -3.0, 0), False))
        self.click_button("blocksFinish")
        self.assertIsNotNone(self.panel.session, "a change after the warning went through without asking again")
        self.assertIn("Press Finish again", self.status())
        self.click_button("blocksFinish")
        self.assertIsNone(self.panel.session, self.status())
        # and a session that is warned and then cancelled
        sk2 = self.scene("RepRearm2")
        self.close_panel()
        Gui.ActiveDocument.resetEdit()
        _pump(40)
        self.open_panel(sk2)
        before = _snapshot(sk2)
        self.begin(sk2)
        self.stock(sk2, "move plain", lambda: sk2.moveGeometry(self.plain_line(sk2), 1, V(-25.0, -3.0, 0), False))
        self.click_button("blocksFinish")
        self.assertIn("Press Finish again", self.status())
        self.click_button("blocksCancel")
        self.assertIsNone(self.panel.session)
        self.assertSameSketch(sk2, before)

    def test_nothing_had_changed_is_said_only_when_nothing_changed(self):
        sk = self.scene("RepNothing")
        self.open_panel(sk)
        self.begin(sk)
        self.click_button("blocksFinish")
        self.assertIsNone(self.panel.session)
        self.assertIn("nothing had changed", self.status())
        # a constraint added and no geometry changed: the constraint is undone, and the status says so
        self.begin(sk)
        n = len(sk.Constraints)
        self.stock(sk, "Dist", lambda: sk.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 5.0)))
        self.assertEqual(len(sk.Constraints), n + 1)
        self.click_button("blocksFinish")
        self.assertIsNone(self.panel.session, self.status())
        self.assertNotIn("nothing had changed", self.status())
        self.assertIn("1 constraint(s) added during the edit were not kept", self.status())
        self.assertEqual(len(sk.Constraints), n)

    # -- item 2 --------------------------------------------------------------------------------------------------------
    def test_members_moved_together_do_not_change_the_block_or_the_siblings(self):
        sk = self.scene("RepRigid", bare=True)
        self.open_panel(sk)
        before = _snapshot(sk)
        poses = self.poses(sk)
        ids = self.gids(sk, "W_1")
        self.begin(sk)
        self.stock(sk, "drag all", lambda: sk.moveGeometries([(i, 0) for i in ids], V(2.0, 1.0, 0), True))
        self.click_button("blocksFinish")
        self.assertIsNone(self.panel.session, self.status())
        self.assertIn("The members moved together, which moves the instance rather than changing the block, so the move was not kept.", self.status())
        self.assertNotIn("changed,", self.status())
        for n, pose in self.poses(sk).items():
            for a, b in zip(pose, poses[n]):
                self.assertAlmostEqual(a, b, delta=TOL, msg=n)
        self.assertSameSketch(sk, before)

    def test_a_rigid_move_with_a_new_member_gives_only_the_new_member(self):
        sk = self.scene("RepRigidAdd", bare=True)
        self.open_panel(sk)
        poses = self.poses(sk)
        ids = self.gids(sk, "W_1")
        self.begin(sk)
        self.stock(sk, "drag all", lambda: sk.moveGeometries([(i, 0) for i in ids], V(2.0, 1.0, 0), True))
        self.stock(sk, "Line", lambda: sk.addGeometry(Part.LineSegment(V(3.0, 3.0, 0), V(4.0, 5.0, 0)), False))
        self.click_button("blocksFinish")
        self.assertIsNone(self.panel.session, self.status())
        self.assertIn("0 changed, 1 added, 0 removed", self.status())
        self.assertIn("moved together", self.status())
        for n, pose in self.poses(sk).items():
            for a, b in zip(pose, poses[n]):
                self.assertAlmostEqual(a, b, delta=TOL, msg=n)
        for n in ("W_1", "W_2", "W_3"):
            self.assertEqual(len(self.gids(sk, n)), 4)

    # -- item 4 --------------------------------------------------------------------------------------------------------
    def test_the_panel_follows_document_activation_and_comes_back_with_its_status(self):
        sk = self.scene("RepActA")
        other = self.new_doc("RepActB")
        other.addObject("Sketcher::SketchObject", "O")
        other.recompute()
        self.open_panel(sk)
        self.assertTrue(hasattr(self.panel, "sync_activation"))
        status = self.status()
        Gui.setActiveDocument(other.Name)
        _pump(40)
        self.assertTrue(self.panel._dormant)
        self.assertFalse(_widget("blocksDefine").isEnabled())
        self.assertNotIn("not being edited", self.status())
        self.assertNotEqual(self.status(), status)
        Gui.setActiveDocument(sk.Document.Name)
        _pump(40)
        self.assertFalse(self.panel._dormant)
        self.assertEqual(self.status(), status)
        self.assertNotIn("not being edited", self.status())
        self.assertTrue(_widget("blocksName").isEnabled())

    # -- item 5 --------------------------------------------------------------------------------------------------------
    def test_cancel_and_finish_count_from_the_start_when_a_later_entry_has_the_sessions_name(self):
        for how in ("cancel", "finish"):
            sk = self.scene("RepName" + how, bare=True)
            self.open_panel(sk)
            before = _snapshot(sk)
            self.begin(sk)
            self.stock(sk, "mv", lambda: sk.moveGeometry(0, 2, V(7.0, 0, 0), False))
            self.stock(sk, "Edit block W_1", lambda: sk.moveGeometry(1, 1, V(0.5, 1.5, 0), False))
            self.stock(sk, "mv2", lambda: sk.moveGeometry(0, 1, V(0.0, 0.5, 0), False))
            self.click_button("blocksCancel" if how == "cancel" else "blocksFinish")
            self.assertIsNone(self.panel.session, self.status())
            if how == "cancel":
                self.assertSameSketch(sk, before)
                self.assertEqual(sk.Document.UndoNames, before["names"])
            else:
                self.assertEqual(sk.Document.UndoCount, before["undo"] + 1)
                self.assertEqual(sk.Document.UndoNames[0], "Edit block definition")
                self.assertEqual(sorted(B.blocks(sk)["instances"]), ["W_1", "W_2", "W_3"])
            self.close_panel()
            Gui.ActiveDocument.resetEdit()
            _pump(40)

    # -- item 8c and 8d ------------------------------------------------------------------------------------------------------
    def test_a_stack_that_is_already_full_at_the_start_does_not_matter(self):
        self.with_stack_limit(6)
        sk = self.scene("RepFull", bare=True)
        for i in range(9):  # the stack holds six entries and is full when the session begins
            sk.Document.openTransaction("filler%d" % i)
            sk.Placement = FreeCAD.Placement(V(0, 0, i), FreeCAD.Rotation())
            sk.Document.commitTransaction()
        self.open_panel(sk)
        self.assertEqual(sk.Document.UndoCount, 6)
        before = _snapshot(sk)
        self.begin(sk)
        self.stock(sk, "Drag", lambda: sk.moveGeometry(0, 2, V(7.0, 0, 0), False))
        self.stock(sk, "Line", lambda: sk.addGeometry(Part.LineSegment(V(1.0, 1.0, 0), V(2.0, 3.0, 0)), False))
        self.click_button("blocksFinish")
        self.assertIsNone(self.panel.session, self.status())
        self.assertIn("1 changed, 1 added", self.status())
        self.assertEqual(sk.Document.UndoNames[0], "Edit block definition")
        rest = list(sk.Document.UndoNames[1:])
        self.assertEqual(rest, before["names"][: len(rest)])  # what the stack keeps below is the newest of what it held (a full stack loses its oldest entries to the session's)

    def test_a_session_longer_than_the_undo_stack_ends_with_a_message_and_keeps_the_edits(self):
        import SketcherBlocksPanel as P

        self.with_stack_limit(6)
        sk = self.scene("RepLong", bare=True)
        self.open_panel(sk)
        n0 = len(P.messages)
        self.begin(sk)
        for i in range(8):  # more steps than the stack keeps: the explosion falls off its far end
            self.stock(sk, "op%d" % i, lambda i=i: sk.moveGeometry(0, 2, V(5.0 + 0.1 * i, 0, 0), False))
            if i == 0:
                self.assertNotIn("undo steps the document keeps", self.panel.session_label.text())
            if i == 3:
                self.assertIn("used 5 of the 6 undo steps the document keeps", self.panel.session_label.text())
        self.assertEqual(sk.Document.UndoCount, 6)
        end = _numbers_of(sk)
        stack = list(sk.Document.UndoNames)
        self.click_button("blocksFinish")
        self.assertIsNone(self.panel.session, self.status())
        self.assertEqual(len(P.messages), n0 + 1)
        self.assertIn("undo", P.messages[-1])
        self.assertEqual(list(sk.Document.UndoNames), stack, "the session's steps were undone or the stack was written to: nothing may touch it")
        self.assertEqual(sk.Document.RedoCount, 0)
        self.assertEqual(_numbers_of(sk), end, "ending the session changed the sketch")
        self.assertEqual(sorted(B.blocks(sk)["instances"]), ["W_2", "W_3"])

    def test_undo_mode_zero_changes_nothing_here_and_a_stack_of_nothing_refuses_the_edit(self):
        sk = self.scene("RepMode0", bare=True)
        sk.Document.UndoMode = 0
        self.assertEqual(sk.Document.UndoMode, 1, "UndoMode is not settable in this build: undo cannot be switched off")
        self.open_panel(sk)
        self.begin(sk)
        self.stock(sk, "Drag", lambda: sk.moveGeometry(0, 2, V(7.0, 0, 0), False))
        self.click_button("blocksFinish")
        self.assertIsNone(self.panel.session, self.status())
        self.assertIn("1 changed", self.status())
        self.close_panel()
        Gui.ActiveDocument.resetEdit()
        _pump(40)
        # the one way to have no undo history: a limit of nothing on the stack
        self.with_stack_limit(0)
        sk = self.scene("RepStackZero", bare=True)
        self.open_panel(sk)
        before = _snapshot(sk)
        self.choose("inst", "W_1")
        self.click_button("blocksEdit")
        self.assertIsNone(self.panel.session)
        self.assertIn("undo history", self.status())
        self.assertSameSketch(sk, before)
        self.assertEqual(sorted(B.blocks(sk)["instances"]), ["W_1", "W_2", "W_3"])

    def test_an_operation_left_open_refuses_the_start_and_the_finish(self):
        """(Called straight, in one turn of the event loop: the application closes an open transaction when it goes idle.)"""
        sk = self.scene("RepOpen", bare=True)
        self.open_panel(sk)
        doc = sk.Document
        before = _snapshot(sk)
        self.choose("inst", "W_1")
        doc.openTransaction("macro")
        sk.addGeometry(Part.LineSegment(V(50, 50, 0), V(60, 60, 0)), False)  # (a transaction is pending once something has changed in it)
        self.assertTrue(doc.HasPendingTransaction)
        self.assertFalse(self.panel.start_session())
        self.assertIsNone(self.panel.session)
        self.assertIn("operation open", self.status())
        doc.commitTransaction()
        self.settle()
        doc.undo()
        self.settle()
        self.assertEqual(_content(sk), before["content"])
        self.begin(sk)
        self.stock(sk, "Drag", lambda: sk.moveGeometry(0, 2, V(7.0, 0, 0), False))
        doc.openTransaction("macro again")
        sk.addGeometry(Part.LineSegment(V(50, 50, 0), V(60, 60, 0)), False)
        self.assertFalse(self.panel.finish_session())
        self.assertIsNotNone(self.panel.session)
        self.assertIn("operation open", self.status())
        self.assertFalse(self.panel.cancel_session())
        self.assertIsNotNone(self.panel.session)
        doc.commitTransaction()
        self.settle()
        self.click_button("blocksFinish")
        self.assertIsNone(self.panel.session, self.status())
        self.assertIn("1 changed, 1 added", self.status())

    # -- item 8a and 8b: stock operations that reach beyond the members ----------------------------------------------------------
    def assertInstancesFollow(self, sk, poses):
        """Every instance sits at the placement it had at the start and its members are the definition's, to 1e-7 mm."""
        data = B._load(sk)
        for n, pose in self.poses(sk).items():
            for a, b in zip(pose, poses[n]):
                self.assertAlmostEqual(a, b, delta=TOL, msg=n)
        for n, inst in data["inst"].items():
            defn = data["defs"][inst["def"]]
            X, Y, th = poses[n]
            for k, nums in B._instance_world(sk, defn, inst).items():
                want = B._to_world(defn["members"][k]["t"], defn["members"][k]["g"], X, Y, th)
                cut = 3 if defn["members"][k]["t"] == "a" else len(want)
                for x, y in zip(nums[:cut], want[:cut]):
                    self.assertAlmostEqual(x, y, delta=TOL, msg=(n, k))

    def test_a_constraint_to_plain_geometry_that_moves_a_member_leaves_placements_and_the_plain_geometry_exact(self):
        sk = self.scene("RepCPlain", bare=True)
        self.open_panel(sk)
        plain = [i for i in range(len(sk.Geometry)) if sk.getGeometryId(i) not in {g for n in B._load(sk)["inst"].values() for g in n["m"].values()}][0]
        g1 = self.gids(sk, "W_1")
        poses, start = self.poses(sk), _numbers_of(sk)
        self.begin(sk)
        self.stock(sk, "Coincident", lambda: sk.addConstraint(Sketcher.Constraint("Coincident", g1[0], 2, plain, 1)))
        self.assertNotEqual(_numbers_of(sk)[plain], start[plain], "control: the constraint moves the plain line")
        self.click_button("blocksFinish")
        self.assertIn("Press Finish again", self.status())
        self.assertRegex(self.status(), r"1 moved element \(Edge%d\)" % (plain + 1))
        self.click_button("blocksFinish")
        self.assertIsNone(self.panel.session, self.status())
        self.assertInstancesFollow(sk, poses)
        self.assertEqual(len(sk.Constraints), 0)
        for (ta, na), (tb, nb) in zip(_numbers_of(sk), start):
            for x, y in zip(na, nb):
                self.assertAlmostEqual(x, y, delta=TOL)

    def test_a_constraint_to_a_sibling_that_moves_the_session_and_the_sibling_leaves_every_placement_exact(self):
        sk = self.scene("RepCSibling", bare=True)
        self.open_panel(sk)
        g1, g2 = self.gids(sk, "W_1"), self.gids(sk, "W_2")
        poses = self.poses(sk)
        self.begin(sk)
        self.stock(sk, "Coincident", lambda: sk.addConstraint(Sketcher.Constraint("Coincident", g1[0], 2, g2[0], 1)))
        moved = self.poses(sk)
        self.assertNotEqual(moved["W_2"], poses["W_2"], "control: the constraint moves the sibling")
        self.click_button("blocksFinish")
        self.assertRegex(self.status(), r"3 moved elements \(Edge%d, Edge%d, Edge%d\)" % tuple(g + 1 for g in g2))
        self.click_button("blocksFinish")
        self.assertIsNone(self.panel.session, self.status())
        self.assertInstancesFollow(sk, poses)
        self.assertEqual(len(sk.Constraints), 0)

    def test_stock_split_on_a_member_gives_a_shorter_member_and_a_new_one_and_no_constraint_moves(self):
        sk = self.scene("RepSplit", bare=False)
        self.open_panel(sk)
        poses, cons = self.poses(sk), _cons_of(sk)
        member = self.gids(sk, "W_1")[0]
        self.begin(sk)
        self.stock(sk, "Split", lambda: sk.split(member, V(2.0, 0.0, 0)))
        self.assertGreater(len(sk.Constraints), len(cons), "control: the tool adds its own constraint")
        self.finish_accepting()
        self.assertIsNone(self.panel.session, self.status())
        self.assertIn("1 changed, 1 added, 0 removed", self.status())
        self.assertRegex(self.status(), r"\d+ constraint\(s\) added during the edit were not kept")
        self.assertInstancesFollow(sk, poses)
        self.assertEqual(_cons_of(sk), cons, "a constraint was retargeted or dropped")
        for n in ("W_1", "W_2", "W_3"):
            ids = self.gids(sk, n)
            self.assertEqual(len(ids), 4)
            lines = sorted(_numbers_of(sk)[i][1] for i in ids if _numbers_of(sk)[i][0] == "l")
            self.assertEqual(len(lines), 2)
            self.assertAlmostEqual(math.hypot(lines[0][2] - lines[0][0], lines[0][3] - lines[0][1]) + math.hypot(lines[1][2] - lines[1][0], lines[1][3] - lines[1][1]), 5.0, delta=TOL, msg=n)

    def test_stock_trim_in_the_middle_of_a_member_keeps_the_first_piece_and_adds_the_second(self):
        sk = self.scene("RepTrim", bare=False)
        self.open_panel(sk)
        poses, cons = self.poses(sk), _cons_of(sk)
        member = self.gids(sk, "W_1")[0]
        self.begin(sk)
        self.stock(sk, "crossings", lambda: sk.addGeometry([Part.LineSegment(V(1, -1, 0), V(1, 1, 0)), Part.LineSegment(V(3, -1, 0), V(3, 1, 0))], False))
        self.stock(sk, "Trim", lambda: sk.trim(member, V(2.0, 0.0, 0)))
        self.finish_accepting()
        self.assertIsNone(self.panel.session, self.status())
        self.assertIn("1 changed, 3 added, 0 removed", self.status())
        self.assertInstancesFollow(sk, poses)
        self.assertEqual(_cons_of(sk), cons, "a constraint was retargeted or dropped")

    def test_stock_fillet_at_a_corner_of_a_member_gives_a_shorter_member_a_new_line_and_an_arc(self):
        sk = self.scene("RepFillet", bare=False)
        self.open_panel(sk)
        poses, cons = self.poses(sk), _cons_of(sk)
        member = self.gids(sk, "W_1")[0]
        self.begin(sk)
        self.stock(sk, "Line", lambda: sk.addGeometry(Part.LineSegment(V(5, 0, 0), V(5, 4, 0)), False))
        new = len(sk.Geometry) - 1
        self.stock(sk, "Corner", lambda: sk.addConstraint(Sketcher.Constraint("Coincident", member, 2, new, 1)))
        self.stock(sk, "Fillet", lambda: sk.fillet(member, 2, 0.5, True, False))
        self.finish_accepting()
        self.assertIsNone(self.panel.session, self.status())
        self.assertIn("1 changed, 2 added, 0 removed", self.status())
        self.assertInstancesFollow(sk, poses)
        self.assertEqual(_cons_of(sk), cons, "a constraint was retargeted or dropped")
        for n in ("W_1", "W_2", "W_3"):
            self.assertEqual(len(self.gids(sk, n)), 5)

    # -- item 6 ------------------------------------------------------------------------------------------------------
    def test_finish_does_nothing_when_the_sketch_is_no_longer_in_the_editor_even_if_no_event_arrived(self):
        import SketcherBlocksPanel as P

        n0 = len(P.messages)
        sk = self.scene("RepNoEdit")
        self.open_panel(sk)
        self.begin(sk)
        self.stock(sk, "Drag", lambda: sk.moveGeometry(0, 2, V(7.0, 0, 0), False))
        panel = self.panel
        FreeCAD.removeDocumentObserver(panel._dw)  # nobody tells the panel that the editor is gone
        Gui.removeDocumentObserver(panel._gw)
        Gui.ActiveDocument.resetEdit()
        _pump(40)
        before = _snapshot(sk)
        self.assertFalse(panel.finish_session())
        self.assertEqual(_snapshot(sk), before, "Finish acted on a sketch that is not in the editor")
        self.assertEqual(sorted(B.blocks(sk)["instances"]), ["W_2", "W_3"], "the rollback ran")
        self.assertEqual(len(P.messages), n0 + 1)
        self.assertIn("no longer being edited", P.messages[-1])  # said for that reason, not for another that happens to end the session too
        self.panel = None


class TestBlocksPanelEditLeaving(EditCase):
    def started(self, name):
        sk = self.scene(name)
        self.open_panel(sk)
        self.begin(sk)
        self.stock(sk, "Drag", lambda: sk.moveGeometry(0, 2, V(7.0, 0, 0), False))
        return sk

    def assertFits(self, sk, n):
        """Every instance left fits its definition: none was exploded behind the test's back, and the solver is fine."""
        self.assertEqual(len(B.blocks(sk)["instances"]), n)
        self.assertEqual(sk.getBlockInstanceCount(), n)
        self.assertEqual(sk.solve(), 0)

    def assertOneMessage(self, count_before):
        import SketcherBlocksPanel as P

        self.assertEqual(len(P.messages), count_before + 1, P.messages[count_before:])
        return P.messages[-1]

    def test_leaving_the_editor_keeps_the_edits_with_the_instance_exploded(self):
        import SketcherBlocksPanel as P

        n0 = len(P.messages)
        sk = self.started("PanelLeave")
        moved = sk.Geometry[0].EndPoint.x
        Gui.ActiveDocument.resetEdit()
        self.assertTrue(_wait(lambda: P.current_panel() is None))
        self.assertIn("exploded", self.assertOneMessage(n0))
        self.assertFits(sk, 2)
        self.assertAlmostEqual(sk.Geometry[0].EndPoint.x, moved, delta=1e-9)
        self.panel = None

    def test_undoing_past_the_start_ends_the_session_with_the_instance_whole(self):
        import SketcherBlocksPanel as P

        n0 = len(P.messages)
        sk = self.started("PanelUndoPast")
        sk.Document.undo()  # undoing one of the session's own steps is the user's right; the session goes on
        self.settle()
        self.assertIsNotNone(self.panel.session)
        self.stock(sk, "Drag", lambda: sk.moveGeometry(0, 2, V(6.0, 0, 0), False))
        for _ in range(2):  # the drag, then the explosion itself: past the start
            sk.Document.undo()
        self.assertTrue(_wait(lambda: self.panel.session is None))
        self.assertIn("undo", self.assertOneMessage(n0))
        self.assertFits(sk, 3)
        self.assertEqual(sorted(B.blocks(sk)["instances"]), ["W_1", "W_2", "W_3"])
        self.assertTrue(_wait(lambda: not _widget("blocksFinish").isVisibleTo(self.panel.widget())))

    def test_closing_the_document_and_deleting_the_sketch_do_not_crash_or_hang(self):
        import SketcherBlocksPanel as P

        n0 = len(P.messages)
        sk = self.started("PanelCloseDoc")
        FreeCAD.closeDocument(sk.Document.Name)
        self.assertTrue(_wait(lambda: P.current_panel() is None))
        self.assertEqual(len(P.messages), n0 + 1)
        self.panel = None
        n1 = len(P.messages)
        sk = self.started("PanelDelSketch")
        sk.Document.removeObject(sk.Name)
        self.assertTrue(_wait(lambda: P.current_panel() is None))
        self.assertEqual(len(P.messages), n1 + 1)
        self.panel = None

    def test_a_second_session_is_refused(self):
        sk = self.started("PanelSecond")
        depth = sk.Document.UndoCount
        self.assertFalse(_widget("blocksEdit").isEnabled())
        self.assertFalse(self.panel.start_session())
        self.assertIn("already", self.status())
        self.assertEqual(sk.Document.UndoCount, depth)
        self.assertIsNotNone(self.panel.session)
        self.click_button("blocksCancel")

    def test_a_macro_changing_the_block_data_ends_the_session(self):
        import SketcherBlocksPanel as P

        n0 = len(P.messages)
        sk = self.started("PanelMacro")
        moved = sk.Geometry[0].EndPoint.x
        B.define(sk, "Other", [len(sk.Geometry) - 2, len(sk.Geometry) - 1])  # P and Q
        self.assertTrue(_wait(lambda: self.panel.session is None))
        self.assertIn("block data", self.assertOneMessage(n0))
        self.assertFits(sk, 2)
        self.assertAlmostEqual(sk.Geometry[0].EndPoint.x, moved, delta=1e-9)

    def test_a_fit_rule_explosion_of_a_sibling_ends_the_session(self):
        import SketcherBlocksPanel as P

        n0 = len(P.messages)
        sk = self.started("PanelSibling")
        g2 = self.gids(sk, "W_2")[0]
        geo = sk.Geometry
        geo[g2].StartPoint = V(40, 40, 0)
        sk.Geometry = geo
        self.assertTrue(_wait(lambda: self.panel.session is None))
        self.assertOneMessage(n0)
        self.assertFits(sk, 1)

    def test_save_and_reopen_during_the_session_gives_a_valid_file_with_the_instance_exploded(self):
        import tempfile

        sk = self.started("PanelSave")
        moved = sk.Geometry[0].EndPoint.x
        path = os.path.join(tempfile.mkdtemp(prefix="blocks-save-"), "s.FCStd")
        sk.Document.saveAs(path)
        _pump(50)
        FreeCAD.closeDocument(sk.Document.Name)
        self.assertTrue(_wait(lambda: self.P.current_panel() is None))
        self.panel = None
        doc = FreeCAD.openDocument(path)
        self._docs.append(doc.Name)
        sk2 = doc.getObject("W")
        self.assertEqual(sorted(B.blocks(sk2)["instances"]), ["W_2", "W_3"])
        self.assertEqual(sk2.getBlockInstanceCount(), 2)
        self.assertAlmostEqual(sk2.Geometry[0].EndPoint.x, moved, delta=1e-9)
        self.assertEqual(sk2.solve(), 0)

    def test_a_result_that_cannot_be_read_at_finish_is_refused_and_the_session_goes_on(self):
        sk = self.started("PanelUnread")
        self.stock(sk, "Ellipse", lambda: sk.addGeometry(Part.Ellipse(V(30, 30, 0), 3.0, 1.0), False))
        depth = sk.Document.UndoCount
        self.click_button("blocksFinish")
        self.assertIsNotNone(self.panel.session)
        self.assertTrue(self.status().endswith("."))
        self.assertNotIn("Traceback", self.status())
        self.assertEqual(sk.Document.UndoCount, depth)
        self.click_button("blocksCancel")
        self.assertIsNone(self.panel.session)
        self.assertFits(sk, 3)


class TestBlocksPanelOutsideTheSession(EditCase):
    """Run 061, second verifier: Finish and Cancel roll the whole document back by undo, so everything outside the block
    that differs from the session's start is named first, in the sketch and out of it; a break message stays on screen."""

    def plain_line(self, sk):
        return [c.First for c in sk.Constraints if c.Name == "Ph"][0]

    def member_move(self, sk):
        self.stock(sk, "mv member", lambda: sk.moveGeometry(0, 2, V(6.0, 0.4, 0), False))

    def started_with(self, name, extra=None):
        """Scene W with a Part::Box, optionally more objects made by `extra(doc)`, a session on W_1 with one member moved."""
        sk = self.scene(name)
        doc = sk.Document
        box = doc.addObject("Part::Box", "Box")
        if extra:
            extra(doc)
        doc.recompute()
        self.open_panel(sk)
        self.begin(sk)
        self.member_move(sk)
        return sk, box

    def press_twice(self, button, label, expect):
        """The first press names `expect` and changes nothing; the second goes on and ends the session."""
        self.click_button(button)
        self.assertIsNotNone(self.panel.session, label + ": the first press went on: " + self.status())
        self.assertIn(expect, self.status(), label)
        self.assertIn("not part of the block", self.status(), label)
        self.click_button(button)
        self.assertIsNone(self.panel.session, label + ": " + self.status())

    # -- item 1 and 2 -----------------------------------------------------------------------------------------------------
    def test_other_objects_are_named_before_finish_and_before_cancel(self):
        for n, button in enumerate(("blocksFinish", "blocksCancel")):
            self.close_panel()
            if Gui.ActiveDocument is not None:
                Gui.ActiveDocument.resetEdit()
                _pump(40)
            sk = self.scene("OutsideObjs%d" % n)
            doc = sk.Document
            box = doc.addObject("Part::Box", "Box")
            s2 = doc.addObject("Sketcher::SketchObject", "S2")
            s2.addGeometry(Part.LineSegment(V(0, 0, 0), V(10, 0, 0)), False)
            gone = doc.addObject("Part::Cylinder", "Gone")
            gone.Label = "Old cylinder"
            doc.recompute()
            self.open_panel(sk)
            self.begin(sk)
            self.member_move(sk)
            self.stock(sk, "S2", lambda: s2.addGeometry(Part.LineSegment(V(0, 5, 0), V(10, 5, 0)), False))
            self.stock(sk, "length", lambda: setattr(box, "Length", 33.0))
            self.stock(sk, "add", lambda: doc.addObject("Part::Sphere", "Ball"))
            self.stock(sk, "del", lambda: doc.removeObject("Gone"))
            self.click_button(button)
            self.assertIsNotNone(self.panel.session, self.status())
            text = self.status()
            for part in ("S2 (Geometry)", "Box (Length)", "1 added object (Ball)", "1 deleted object (Old cylinder)"):
                self.assertIn(part, text, button)
            self.assertEqual(box.Length.Value, 33.0, "the warning changed the document")
            self.assertEqual(len(s2.Geometry), 2)
            self.click_button(button)
            self.assertIsNone(self.panel.session, self.status())
            self.assertEqual(box.Length.Value, 10.0)
            self.assertEqual(len(s2.Geometry), 1)
            self.assertIsNone(doc.getObject("Ball"))
            self.assertIsNotNone(doc.getObject("Gone"))
            self.assertIn("Undone because they are not part of the block", self.status())

    def test_how_another_object_looks_is_named_before_finish_and_before_cancel(self):
        for n, button in enumerate(("blocksFinish", "blocksCancel")):
            self.close_panel()
            if Gui.ActiveDocument is not None:
                Gui.ActiveDocument.resetEdit()
                _pump(40)
            sk = self.scene("OutsideLooks%d" % n)
            doc = sk.Document
            box = doc.addObject("Part::Box", "Box")
            doc.recompute()
            self.open_panel(sk)
            self.begin(sk)
            self.member_move(sk)
            self.stock(sk, "colour", lambda: setattr(box.ViewObject, "ShapeColor", (1.0, 0.0, 0.0)))
            self.stock(sk, "hide", lambda: setattr(box.ViewObject, "Visibility", False))
            self.click_button(button)
            self.assertIsNotNone(self.panel.session, self.status())
            self.assertIn("view ShapeColor", self.status(), button)
            self.assertIn("Visibility", self.status(), button)
            self.click_button(button)
            self.assertIsNone(self.panel.session, self.status())

    def test_a_session_with_only_member_changes_on_a_shown_box_gives_no_warning(self):
        sk = self.scene("OutsideLooksQuiet")
        doc = sk.Document
        doc.addObject("Part::Box", "Box")
        doc.recompute()
        self.open_panel(sk)
        self.begin(sk)
        self.member_move(sk)
        doc.recompute()
        self.click_button("blocksCancel")
        self.assertIsNone(self.panel.session, self.status())

    def test_a_cancel_with_only_member_changes_is_one_press_and_restores_the_sketch(self):
        sk, box = self.started_with("OutsideCancelPlain")
        before = _snapshot(sk)
        self.click_button("blocksCancel")
        self.assertIsNone(self.panel.session, self.status())
        self.assertNotIn("Cancel will undo", self.status())

    # -- the rule for dependents: a Pad over the sketch ---------------------------------------------------------------------
    def pad_scene(self, name):
        doc = self.new_doc(name)
        body = doc.addObject("PartDesign::Body", "Body")
        sk = body.newObject("Sketcher::SketchObject", "R")
        pts = [(0, 0), (4, 0), (4, 3), (0, 3)]
        for i in range(4):
            a, b = pts[i], pts[(i + 1) % 4]
            sk.addGeometry(Part.LineSegment(V(a[0], a[1], 0), V(b[0], b[1], 0)), False)
        B.define(sk, "R", [0, 1, 2, 3])
        sk.delGeometries([0, 1, 2, 3])
        B.insert(sk, "R", 0, 0, 0)
        B.insert(sk, "R", 10, 0, 0)
        doc.recompute()
        pad = body.newObject("PartDesign::Pad", "Pad")
        pad.Profile = sk
        pad.Length = 5.0
        doc.recompute()
        self.assertAlmostEqual(pad.Shape.Volume, 120.0, delta=1e-6)
        return sk, pad

    def test_a_recompute_of_a_dependent_is_not_a_change_but_an_edit_of_its_input_is(self):
        sk, pad = self.pad_scene("OutsidePad")
        self.open_panel(sk)
        self.begin(sk, "R_1")
        self.stock(sk, "add construction", lambda: sk.addGeometry(Part.LineSegment(V(-5, -5, 0), V(-1, -5, 0)), True))

        def recompute():
            sk.touch()
            sk.Document.recompute()

        self.stock(sk, "recompute", recompute)
        pad.addProperty("App::PropertyInteger", "Computed", "Test", "a result a recompute writes")
        pad.setPropertyStatus("Computed", "Output")
        self.assertIn("Output", pad.getPropertyStatus("Computed"))
        self.stock(sk, "output property", lambda: setattr(pad, "Computed", 7))
        self.assertIn(pad, sk.InListRecursive)
        self.click_button("blocksFinish")
        self.assertIsNone(self.panel.session, "a recompute of the Pad over the sketch raised a warning: " + self.status())
        self.assertNotIn("not part of the block", self.status())
        # the other direction: the Pad's own input
        self.begin(sk, "R_1")
        self.stock(sk, "add construction", lambda: sk.addGeometry(Part.LineSegment(V(-5, -5, 0), V(-1, -5, 0)), True))
        self.stock(sk, "pad length", lambda: setattr(pad, "Length", 9.0))
        self.stock(sk, "recompute", recompute)
        self.click_button("blocksFinish")
        self.assertIsNotNone(self.panel.session, self.status())
        self.assertIn("Pad (Length)", self.status())
        self.assertNotIn("Shape", self.status())
        self.assertEqual(pad.Length.Value, 9.0)
        self.click_button("blocksFinish")
        self.assertIsNone(self.panel.session, self.status())
        self.assertEqual(pad.Length.Value, 5.0)

    def test_a_recompute_in_a_session_on_an_untouched_document_raises_no_warning(self):
        sk, box = self.started_with("OutsideRecompute")
        sk.Document.recompute()
        box.touch()
        self.stock(sk, "recompute", sk.Document.recompute)
        time.sleep(2.2)  # a serialised property carries the second it was written in; the same content must not read as a change
        self.click_button("blocksFinish")
        self.assertIsNone(self.panel.session, self.status())
        self.assertNotIn("not part of the block", self.status())

    def test_the_whole_document_is_read_only_at_start_finish_and_cancel(self):
        import SketcherBlocksPanel as P

        sk = self.scene("OutsideCost")
        doc = sk.Document
        for i in range(200):
            doc.addObject("Part::Box", "B%d" % i)
        doc.recompute()
        self.open_panel(sk)
        calls = []
        orig = B._document_state
        B._document_state = lambda s: (calls.append(1), orig(s))[1]
        self.addCleanup(setattr, B, "_document_state", orig)
        t0 = time.perf_counter()
        self.begin(sk)
        t_start = time.perf_counter() - t0
        n_start = len(calls)
        for i in range(5):
            self.stock(sk, "mv %d" % i, lambda i=i: sk.moveGeometry(0, 2, V(6.0 + i, 0.4, 0), False))
        self.assertEqual(len(calls), n_start, "the document was read again during the session")
        t0 = time.perf_counter()
        self.click_button("blocksFinish")
        t_finish = time.perf_counter() - t0
        print("OUTSIDE-COST 200 objects: start %.0f ms, finish %.0f ms, document passes %d" % (t_start * 1000, t_finish * 1000, len(calls)))
        self.assertEqual(len(calls), n_start + 1)

    # -- item 3: the sketch's whole state ----------------------------------------------------------------------------------
    def test_every_other_part_of_the_sketch_is_named_when_it_changes(self):
        sk = self.scene("OutsideSketch")
        doc = sk.Document
        box = doc.addObject("Part::Box", "Box")
        P = self.plain_line(sk)
        names = [c.Name for c in sk.Constraints]
        datum = sk.addConstraint(Sketcher.Constraint("DistanceX", P, 1, P, 2, 50.0))
        sk.renameConstraint(datum, "len")
        sk.solve()
        doc.recompute()
        self.open_panel(sk)
        cases = [
            ("rename", lambda: sk.renameConstraint(names.index("Ph"), "Ph2"), "renamed constraint ('Ph')"),
            ("construction", lambda: sk.toggleConstruction(P), "construction setting changed"),
            ("expression", lambda: sk.setExpression("Constraints.len", "30 mm + 20 mm"), "ExpressionEngine: Constraints.len"),
            ("active", lambda: sk.setActive(names.index("horiz"), False), "changed active, driving or virtual-space setting ('horiz')"),
            ("driving", lambda: sk.setDriving(datum, False), "changed active, driving or virtual-space setting ('len')"),
            ("virtual", lambda: sk.setVirtualSpace(names.index("onP"), True), "changed active, driving or virtual-space setting ('onP')"),
            ("placement", lambda: setattr(sk, "Placement", FreeCAD.Placement(V(1, 2, 3), FreeCAD.Rotation())), "Placement"),
            ("external", lambda: sk.addExternal("Box", "Edge1"), "ExternalGeometry"),
            ("offset", lambda: setattr(sk, "AttachmentOffset", FreeCAD.Placement(V(0, 0, 2), FreeCAD.Rotation())), "AttachmentOffset"),
        ]
        for label, op, expect in cases:
            self.begin(sk)
            self.member_move(sk)
            self.stock(sk, label, op)
            self.press_twice("blocksFinish", label, expect)
            self.assertNotIn("nothing had changed", self.status(), label)
            self.assertIn("Undone because they are not part of the block", self.status(), label)
        self.assertEqual([c.Name for c in sk.Constraints], names + ["len"])
        self.assertEqual(sk.ExpressionEngine, [])
        self.assertFalse(sk.getConstruction(P))
        self.assertEqual(sk.Placement.Base, V(0, 0, 0))
        self.assertEqual(len(sk.ExternalGeometry), 0)

    # -- item 5 --------------------------------------------------------------------------------------------------------

    # -- item 5 -----------------------------------------------------------------------------------------------------------
    def test_a_change_between_two_presses_that_nets_to_zero_does_not_ask_again(self):
        sk, box = self.started_with("OutsideNet")
        self.stock(sk, "length", lambda: setattr(box, "Length", 33.0))
        self.click_button("blocksFinish")
        self.assertIn("Press Finish again", self.status())
        self.stock(sk, "width", lambda: setattr(box, "Width", 7.0))
        self.stock(sk, "width back", lambda: setattr(box, "Width", 10.0))
        self.click_button("blocksFinish")
        self.assertIsNone(self.panel.session, "a change that netted to zero asked again: " + self.status())
        self.assertEqual(box.Length.Value, 10.0)

    def test_a_real_change_between_two_presses_asks_again(self):
        sk, box = self.started_with("OutsideReask")
        self.stock(sk, "length", lambda: setattr(box, "Length", 33.0))
        self.click_button("blocksFinish")
        self.stock(sk, "width", lambda: setattr(box, "Width", 7.0))
        self.click_button("blocksFinish")
        self.assertIsNotNone(self.panel.session)
        self.assertIn("Box (Length, Width)", self.status())

    # -- item 4: a break message stays ------------------------------------------------------------------------------------
    def assertMessageStays(self, text_part):
        self.settle()
        self.assertIn(text_part, self.status())
        self.settle()
        _pump(80)
        self.assertIn(text_part, self.status(), "a refresh overwrote the message")
        before = self.status()
        self.choose("inst", "W_2")  # the user does something in the panel
        self.assertNotEqual(self.status(), before)
        self.assertNotIn(text_part, self.status())

    def test_the_message_of_a_broken_session_stays_until_the_next_action(self):
        # undone past the start
        sk = self.scene("StayUndo")
        self.open_panel(sk)
        self.begin(sk)
        self.stock(sk, "Drag", lambda: sk.moveGeometry(0, 2, V(6.0, 0, 0), False))
        for _ in range(2):
            sk.Document.undo()
        self.assertTrue(_wait(lambda: self.panel.session is None))
        self.assertMessageStays("undo steps of the edit are gone")
        # a macro changing the block data
        self.close_panel()
        Gui.ActiveDocument.resetEdit()
        sk = self.scene("StayMacro")
        self.open_panel(sk)
        self.begin(sk)
        self.stock(sk, "Drag", lambda: sk.moveGeometry(0, 2, V(6.0, 0, 0), False))
        B.define(sk, "Other", [len(sk.Geometry) - 2, len(sk.Geometry) - 1])
        self.assertTrue(_wait(lambda: self.panel.session is None))
        self.assertMessageStays("block data changed")
        # a fit-rule explosion of a sibling
        self.close_panel()
        Gui.ActiveDocument.resetEdit()
        sk = self.scene("StaySibling")
        self.open_panel(sk)
        self.begin(sk)
        g2 = self.gids(sk, "W_2")[0]
        geo = sk.Geometry
        geo[g2].StartPoint = V(40, 40, 0)
        sk.Geometry = geo
        self.assertTrue(_wait(lambda: self.panel.session is None))
        self.settle()
        self.assertIn("The edit of W_1 ended", self.status())
        self.settle()
        self.assertIn("The edit of W_1 ended", self.status(), "a refresh overwrote the message")

    def test_the_message_of_a_session_longer_than_the_stack_stays(self):
        grp = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Document")
        old = grp.GetInt("MaxUndoSize", 20)
        grp.SetInt("MaxUndoSize", 6)
        self.addCleanup(grp.SetInt, "MaxUndoSize", old)
        sk = self.scene("StayLong", bare=True)
        self.open_panel(sk)
        self.begin(sk)
        for i in range(8):
            self.stock(sk, "op%d" % i, lambda i=i: sk.moveGeometry(0, 2, V(5.0 + 0.1 * i, 0, 0), False))
        self.click_button("blocksFinish")
        self.assertIsNone(self.panel.session)
        self.assertMessageStays("undo steps of the edit are gone")

    def test_leaving_the_editor_and_deleting_the_sketch_leave_the_message_in_the_log(self):
        import SketcherBlocksPanel as P

        n0 = len(P.messages)
        sk = self.scene("StayLeave")
        self.open_panel(sk)
        self.begin(sk)
        panel = self.panel
        Gui.ActiveDocument.resetEdit()
        self.assertTrue(_wait(lambda: P.current_panel() is None))
        self.assertEqual(len(P.messages), n0 + 1)
        self.assertIn("ended", P.messages[-1])
        self.panel = None
