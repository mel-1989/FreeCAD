# SPDX-License-Identifier: LGPL-2.1-or-later

# R085: PMI leaders measured in the rendered QGraphicsScene, with the GUI up.
# Run with: FreeCAD -t TestTechDrawGui   (needs a QApplication for the views' hidden-line threads)
#
# The headless check (PmiDrawingTest.anchorMisses) reads the leader end through PmiDrawing.anchorEnd and so shares its sign
# convention for DrawLeaderLine.WayPoints; run 083 shipped y-up waypoints that way.  TechDraw stores WayPoints y inverted (page
# millimetres, y down; the leader's X, Y are y up): see DrawLeaderLine.cpp lines 347 to 352 (WayPoints.setValues(inverted)),
# lines 385 to 391 (getTransformedWayPoints) and QGILeaderLine.cpp line 278 (setPosition(.., -y)).  This test therefore uses nothing
# from PmiDrawing: it takes the end of the leader
# line item and the edge items of the parent view from the scene, in page millimetres, and requires them to meet.

import math
import time
import unittest

import FreeCAD as App
import Part

import PmiAuthoring as PA
import PmiDrawing as PD

from PySide import QtCore, QtWidgets

import FreeCADGui as Gui
import TechDrawGui

from TDTest.PmiDrawingTest import face_named, make_page, planar_face, cyl_face

UT = 65536
T_EDGE, T_PRIM = 19, 25
TOL_MM = 0.5
X, Y, Z = App.Vector(1, 0, 0), App.Vector(0, 1, 0), App.Vector(0, 0, 1)


def pump(sec):
    t = time.time()
    while time.time() - t < sec:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.01)


def kind(it):
    return it.type() - UT


def descend(it):
    for c in it.childItems():
        yield c
        if c.data(0) != "QGIV":
            yield from descend(c)


def seg_dist(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = dx * dx + dy * dy
    t = 0.0 if n == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / n))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


class PmiDrawingSceneTest(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("PmiSceneTest")
        # an asymmetric part: a long thin box and a small cylinder far off the box's centreline, so a y mirror moves every anchor
        self.box = self.doc.addObject("Part::Box", "Box")
        self.box.Length, self.box.Width, self.box.Height = 60, 20, 10
        self.cyl = self.doc.addObject("Part::Cylinder", "Cyl")
        self.cyl.Radius, self.cyl.Height = 4, 10
        self.cyl.Placement.Base = App.Vector(75, 5, 0)
        self.doc.recompute()
        self.page = make_page(self.doc)
        Gui.ActiveDocument = Gui.getDocument(self.doc.Name)

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def scene(self):
        Gui.getDocument(self.doc.Name).getObject(self.page.Name).doubleClicked()
        sc = TechDrawGui.getSceneForPage(self.page)
        self.assertIsNotNone(sc)
        return sc

    def settle_scene(self, sc, names):
        t0 = time.time()
        last, stable = None, 0
        while time.time() - t0 < 60:
            pump(0.1)
            items = sc.items()
            have = {i.data(1) for i in items if i.data(0) == "QGIV"}
            if not names <= have:
                last, stable = None, 0
                continue
            sig = tuple(sorted((i.data(1), round(i.sceneBoundingRect().x(), 2), round(i.sceneBoundingRect().y(), 2)) for i in items if i.data(0) == "QGIV"))
            stable = stable + 1 if sig == last else 0
            last = sig
            if stable >= 6:
                return
        self.fail("the scene did not settle")

    def pmi(self):
        PA.addDatum("A", (self.box, [planar_face(self.box, -Z, 0)]))
        PA.addDatum("B", (self.box, [planar_face(self.box, -X, 0)]))
        top = (self.box, [planar_face(self.box, Z, 10)])
        hole = (self.cyl, [cyl_face(self.cyl)])
        PA.addTolerance("Flatness", 0.05, top)
        PA.addTolerance("Position", 0.25, hole, datums=["A", "B"], zone_shape="Diameter")
        PA.addTolerance("Perpendicularity", 0.1, (self.box, [planar_face(self.box, X, 60)]), datums=["A"])
        PD.project(self.page, [self.box, self.cyl])
        self.doc.recompute()
        PD.settle(self.page)

    def test_every_leader_ends_on_a_drawn_edge(self):
        self.pmi()
        self.check_leaders_on_edges()

    def test_refresh_puts_a_displaced_leader_back_on_its_edge(self):
        self.pmi()
        for ld in self.doc.Objects:
            if ld.TypeId == "TechDraw::DrawLeaderLine" and "PmiRole" in ld.PropertiesList and ld.PmiRole == "leader":
                ld.WayPoints = [App.Vector(0, 0, 0), App.Vector(3, 4, 0)]
                ld.X = ld.X.Value + 5
        PD.refresh(self.page)
        self.doc.recompute()
        PD.settle(self.page)
        self.check_leaders_on_edges()

    def check_leaders_on_edges(self):
        leaders = [o for o in self.doc.Objects if o.TypeId == "TechDraw::DrawLeaderLine" and "PmiRole" in o.PropertiesList and o.PmiRole == "leader"]
        self.assertGreaterEqual(len(leaders), 4)
        names = {o.Name for o in leaders} | {v.Name for g in self.page.Views if g.TypeId == "TechDraw::DrawProjGroup" for v in g.Views}
        sc = self.scene()
        self.settle_scene(sc, names)
        items = sc.items()
        by_name = {i.data(1): i for i in items if i.data(0) == "QGIV"}
        grp = [v for v in self.page.Views if v.TypeId == "TechDraw::DrawProjGroup"][0]
        self.assertGreater(abs(grp.X.Value), 1.0)
        rez = by_name[grp.Name].pos().x() / grp.X.Value  # scene units per page millimetre; the scene's y is down

        def pt(p):
            return (p.x() / rez, -p.y() / rez)

        def polys(item):
            return [[pt(poly.at(i)) for i in range(poly.count())] for poly in item.mapToScene(item.path()).toSubpathPolygons()]

        def edges_of(view_item):
            out = []
            for c in descend(view_item):
                if kind(c) == T_EDGE and c.isVisible() and c.path().elementCount() > 0 and c.pen().style() == QtCore.Qt.SolidLine:
                    out.extend(polys(c))
            return out

        far = []
        for ld in leaders:
            it = by_name[ld.Name]
            pl = [p for c in it.childItems() if c.data(0) != "QGIV" and kind(c) == T_PRIM and c.isVisible() and c.path().elementCount() > 1 for p in polys(c)]
            self.assertTrue(pl, ld.Name)
            ends = (pl[0][0], pl[-1][-1])
            self.assertGreater(math.hypot(ends[0][0] - ends[1][0], ends[0][1] - ends[1][1]), 1.0, ld.Name)
            edges = edges_of(by_name[ld.LeaderParent.Name])
            self.assertTrue(edges, ld.LeaderParent.Name)
            # one end is at the annotation (10 mm and more from the geometry), the other at the anchor: the nearer to an edge is the anchor
            d = min(seg_dist(end, pl2[i], pl2[i + 1]) for end in ends for pl2 in edges for i in range(len(pl2) - 1))
            if d > TOL_MM:
                far.append((ld.Name, ld.PmiSource.Name, round(d, 2)))
        self.assertEqual(far, [], "leader ends further than %s mm from every drawn edge of their view" % TOL_MM)
