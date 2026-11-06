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
        it = self.panel.tree.invisibleRootItem()
        stack = [it.child(i) for i in range(it.childCount())]
        while stack:
            item = stack.pop()
            d = item.data(0, QtCore.Qt.UserRole) or {}
            if d.get("kind") == kind and d.get("name") == name:
                self.panel.tree.setCurrentItem(item)
                item.setSelected(True)
                self.settle()
                return item
            stack += [item.child(i) for i in range(item.childCount())]
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

    def click_places(self, sk, target):
        gl = self.viewport()
        self.place_mode()
        n = len(B.blocks(sk)["instances"])
        sel_before = _selected_names()
        undo = sk.Document.UndoCount
        self.click_at(gl, self.pixel_of(sk, *target))
        self.assertEqual(len(B.blocks(sk)["instances"]), n + 1)
        self.assertEqual(sk.Document.UndoCount, undo + 1)
        self.assertEqual(_selected_names(), sel_before)
        self.assertNoMode()
        g = sk.Geometry[B.blocks(sk)["instances"][_latest(sk)]["geo_ids"][0]]
        # within one pixel: the view's own projection of the placed origin against the clicked point
        view = Gui.getDocument(sk.Document.Name).ActiveView
        gp = sk.getGlobalPlacement()
        hx, hy = view.getPointOnViewport(gp.multVec(V(g.StartPoint.x, g.StartPoint.y, 0)))
        tx, ty = view.getPointOnViewport(gp.multVec(V(target[0], target[1], 0)))
        self.assertLessEqual(max(abs(hx - tx), abs(hy - ty)), 1, ("pixels", (hx, hy), (tx, ty)))
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
                doc = self.new_doc("PanelClick%s%d" % (label, top))
                sk = make(doc)
                self.open_panel(sk)
                self.frame(sk, top)
                self.choose("def", "T")
                _widget("blocksAngle").setText("0 deg")
                self.click_places(sk, (3.5, 2.25))
                self.click_places(sk, (-6.0, 4.0))
                self.close_panel()
                Gui.ActiveDocument.resetEdit()
                _pump(50)

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
