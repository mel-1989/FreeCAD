# SPDX-License-Identifier: LGPL-2.1-or-later

# Tests of the in-memory hidden-line cache (TechDraw/App/HlrCache.cpp).  A hit has to give exactly
# what a miss gives, and every term of the cache key has to cause a miss when it changes.
# Public sources: OCCT reference documentation for TopoDS_Shape, TopLoc_Location and
# HLRBRep_Algo; FreeCAD's TechDraw DrawViewPart.cpp and GeometryObject.cpp.

import os
import unittest

import FreeCAD
import Part
import TechDraw

from .TechDrawTestUtilities import createPageWithSVGTemplate

V = FreeCAD.Vector


def stats():
    return TechDraw.hlrCacheStats()


def snapshot(view):
    """Everything a hit must reproduce: edges in order (type, end points, length), vertices in order,
    and the SVG text of the view."""
    out = []
    for kind, edges in (("visible", view.getVisibleEdges()), ("hidden", view.getHiddenEdges())):
        for e in edges:
            p0 = e.firstVertex().Point
            p1 = e.lastVertex().Point
            out.append((kind, e.Curve.TypeId, tuple(p0), tuple(p1), e.Length))
    for kind, verts in (("visible", view.getVisibleVertexes()), ("hidden", view.getHiddenVertexes())):
        for v in verts:
            out.append((kind + "-vertex", tuple(v)))
    out.append(TechDraw.viewPartAsSvg(view))
    return out


class HlrCacheTest(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("CADFORK_HLR_CACHE", "CADFORK_HLR_CACHE_MB")}
        for k in self._env:
            os.environ.pop(k, None)
        TechDraw.clearHlrCache()
        self.doc = FreeCAD.newDocument("HlrCacheDoc")
        base = Part.makeBox(30, 20, 10)
        base = base.makeFillet(2.0, [e for e in base.Edges if abs(e.Vertexes[0].Z - e.Vertexes[-1].Z) > 5])
        base = base.cut(Part.makeCylinder(3, 12, V(15, 10, -1)))
        self.blk = self.doc.addObject("Part::Feature", "Blk")
        self.blk.Shape = base
        self.link = self.doc.addObject("App::Link", "Lnk")
        self.link.LinkedObject = self.blk
        self.link.Placement = FreeCAD.Placement(V(50, 0, 0), FreeCAD.Rotation(V(0, 0, 1), 30))
        self.page = createPageWithSVGTemplate(self.doc)
        self.page.Scale = 1.0
        self.view = self.doc.addObject("TechDraw::DrawViewPart", "View")
        self.page.addView(self.view)
        self.view.Source = [self.blk, self.link]
        self.view.Direction = V(1, -1, 1)
        self.view.ScaleType = "Custom"
        self.view.Scale = 1.0
        self.doc.recompute()

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if "HlrCacheDoc" in FreeCAD.listDocuments():
            FreeCAD.closeDocument("HlrCacheDoc")
        TechDraw.clearHlrCache()

    def fresh(self, view=None):
        """The result of a miss for the current state of the view."""
        view = view or self.view
        TechDraw.clearHlrCache()
        view.touch()
        self.doc.recompute()
        return snapshot(view)

    def recompute_counting(self, change):
        before = stats()
        change()
        self.doc.recompute()
        after = stats()
        return after["hits"] - before["hits"], after["misses"] - before["misses"]

    # ---- stats and clear -------------------------------------------------------------------

    def testStatsAndClear(self):
        s = stats()
        for key in ("hits", "misses", "entries", "bytes"):
            self.assertIn(key, s)
        self.assertGreaterEqual(s["entries"], 1)
        self.assertGreater(s["bytes"], 0)
        TechDraw.clearHlrCache()
        s = stats()
        self.assertEqual(s["entries"], 0)
        self.assertEqual(s["bytes"], 0)

    # ---- hits ------------------------------------------------------------------------------

    def testHitAfterMoveEqualsMiss(self):
        first = snapshot(self.view)
        self.assertTrue(first[0])
        hits, misses = self.recompute_counting(lambda: setattr(self.view, "X", self.view.X.Value + 12))
        self.assertGreaterEqual(hits, 1)
        self.assertEqual(misses, 0)
        moved = snapshot(self.view)
        undo_hits, undo_misses = self.recompute_counting(lambda: setattr(self.view, "X", self.view.X.Value - 12))
        self.assertGreaterEqual(undo_hits, 1)
        self.assertEqual(undo_misses, 0)
        self.assertEqual(moved, first)
        self.assertEqual(snapshot(self.view), first)
        # the same state computed without any cache entry
        self.assertEqual(self.fresh(), first)

    def testHitOnTouchOfSources(self):
        first = snapshot(self.view)
        hits, misses = self.recompute_counting(lambda: self.link.touch())
        self.assertGreaterEqual(hits, 1)
        self.assertEqual(misses, 0)
        self.assertEqual(snapshot(self.view), first)
        # a same-value edit of the source object: it recomputes, the content is the same
        hits, misses = self.recompute_counting(lambda: self.blk.touch())
        self.assertGreaterEqual(hits, 1)
        self.assertEqual(misses, 0)
        self.assertEqual(snapshot(self.view), first)

    def testScaleChangeAndBackHits(self):
        first = snapshot(self.view)
        hits, misses = self.recompute_counting(lambda: setattr(self.view, "Scale", 0.5))
        self.assertEqual(hits, 0)
        self.assertGreaterEqual(misses, 1)
        half = snapshot(self.view)
        hits, misses = self.recompute_counting(lambda: setattr(self.view, "Scale", 1.0))
        self.assertGreaterEqual(hits, 1)
        self.assertEqual(misses, 0)
        self.assertEqual(snapshot(self.view), first)
        hits, misses = self.recompute_counting(lambda: setattr(self.view, "Scale", 0.5))
        self.assertGreaterEqual(hits, 1)
        self.assertEqual(snapshot(self.view), half)

    # ---- misses: every key term -------------------------------------------------------------

    def check_miss(self, name, change):
        before = snapshot(self.view)
        hits, misses = self.recompute_counting(change)
        self.assertEqual(hits, 0, name + " must not hit")
        self.assertGreaterEqual(misses, 1, name + " must miss")
        after = snapshot(self.view)
        self.assertEqual(after, self.fresh(), name + " gave a stale result")
        return before, after

    def testMissOnScale(self):
        self.check_miss("Scale", lambda: setattr(self.view, "Scale", 0.7))

    def testMissOnRotation(self):
        self.check_miss("Rotation", lambda: setattr(self.view, "Rotation", 20.0))

    def testMissOnDirection(self):
        self.check_miss("Direction", lambda: setattr(self.view, "Direction", V(0, -1, 1)))

    def testMissOnXDirection(self):
        self.view.Direction = V(0, -1, 0)
        self.view.XDirection = V(1, 0, 0)
        self.doc.recompute()
        self.check_miss("XDirection", lambda: setattr(self.view, "XDirection", V(-1, 0, 0)))

    def testMissOnClassFlags(self):
        for flag in ("SmoothVisible", "SeamVisible", "IsoVisible", "HardHidden", "SmoothHidden",
                     "SeamHidden", "IsoHidden"):
            with self.subTest(flag=flag):
                self.view.IsoCount = 2
                self.doc.recompute()
                self.check_miss(flag, lambda f=flag: setattr(self.view, f, not getattr(self.view, f)))

    def testMissOnIsoCount(self):
        self.view.IsoVisible = True
        self.doc.recompute()
        self.check_miss("IsoCount", lambda: setattr(self.view, "IsoCount", 3))

    def testMissOnPerspectiveAndFocus(self):
        self.check_miss("Perspective", lambda: setattr(self.view, "Perspective", True))
        self.check_miss("Focus", lambda: setattr(self.view, "Focus", 250.0))

    def testMissOnLinkPlacement(self):
        def move():
            p = self.link.Placement
            self.link.Placement = FreeCAD.Placement(p.Base + V(0.001, 0, 0), p.Rotation)
        self.check_miss("link placement by 0.001", move)

    def testMissOnRealEdit(self):
        def edit():
            shape = Part.makeBox(30, 20, 10.5)
            self.blk.Shape = shape
        before, after = self.check_miss("real edit", edit)
        self.assertNotEqual(before, after)

    def testMissOnSourceListChange(self):
        self.check_miss("Source", lambda: setattr(self.view, "Source", [self.blk]))
        self.check_miss("Source back", lambda: setattr(self.view, "Source", [self.blk, self.link]))

    # ---- cosmetics must never reach the cached data ----------------------------------------

    def testCosmeticDoesNotLeak(self):
        first = snapshot(self.view)
        n = len(self.view.getVisibleEdges())
        hits, misses = self.recompute_counting(
            lambda: self.view.makeCosmeticLine(V(0, 0, 0), V(5, 5, 0)))
        self.assertGreaterEqual(hits, 1)
        self.assertEqual(len(self.view.getVisibleEdges()), n + 1)
        self.view.clearCosmeticEdges()
        hits, misses = self.recompute_counting(lambda: self.view.touch())
        self.assertGreaterEqual(hits, 1)
        self.assertEqual(misses, 0)
        self.assertEqual(len(self.view.getVisibleEdges()), n)
        self.assertEqual(snapshot(self.view), first)
        # a cosmetic vertex too
        nv = len(self.view.getVisibleVertexes())
        tag = self.view.makeCosmeticVertex(V(1, 2, 0))
        self.doc.recompute()
        self.assertEqual(len(self.view.getVisibleVertexes()), nv + 1)
        self.view.removeCosmeticVertex(tag)
        self.doc.recompute()
        self.assertEqual(len(self.view.getVisibleVertexes()), nv)
        self.assertEqual(snapshot(self.view), first)

    # ---- switches, exclusions -----------------------------------------------------------------

    def testOffSwitch(self):
        os.environ["CADFORK_HLR_CACHE"] = "0"
        try:
            self.assertFalse(stats()["enabled"])
            before = stats()
            self.view.touch()
            self.doc.recompute()
            self.view.X = self.view.X.Value + 5
            self.doc.recompute()
            after = stats()
            self.assertEqual(after["hits"], before["hits"])
            self.assertEqual(after["misses"], before["misses"])
            off = snapshot(self.view)
        finally:
            del os.environ["CADFORK_HLR_CACHE"]
        self.assertEqual(self.fresh(), off)

    def testSectionNeverUsesTheCache(self):
        section = self.doc.addObject("TechDraw::DrawViewSection", "Section")
        self.page.addView(section)
        section.BaseView = self.view
        section.Source = [self.blk, self.link]
        section.SectionNormal = V(0, 1, 0)
        section.SectionDirection = "Down"
        section.SectionOrigin = V(15, 10, 5)
        section.ScaleType = "Custom"
        section.Scale = 1.0
        self.doc.recompute()
        before = stats()
        section.touch()
        self.doc.recompute()
        section.Scale = 0.5
        self.doc.recompute()
        after = stats()
        self.assertEqual(after["hits"], before["hits"])
        self.assertEqual(after["misses"], before["misses"])
        self.assertEqual(after["entries"], before["entries"])

    def testCoarseViewNeverUsesTheCache(self):
        self.view.CoarseView = True
        self.doc.recompute()
        before = stats()
        self.view.touch()
        self.doc.recompute()
        self.view.X = self.view.X.Value + 3
        self.doc.recompute()
        after = stats()
        self.assertEqual(after["hits"], before["hits"])
        self.assertEqual(after["misses"], before["misses"])

    def testExplodedShapeHookNeverUsesTheCache(self):
        class Exploder:
            def __init__(self, obj):
                obj.Proxy = self

            def execute(self, obj):
                pass

            def getExplodedShape(self, obj):
                return Part.makeBox(5, 6, 7)

        feat = self.doc.addObject("Part::FeaturePython", "Exploded")
        Exploder(feat)
        feat.Shape = Part.makeBox(5, 6, 7)
        view = self.doc.addObject("TechDraw::DrawViewPart", "ViewExploded")
        self.page.addView(view)
        view.Source = [feat]
        view.ScaleType = "Custom"
        view.Scale = 1.0
        self.doc.recompute()
        before = stats()
        view.touch()
        self.doc.recompute()
        view.X = view.X.Value + 3
        self.doc.recompute()
        after = stats()
        self.assertEqual(after["hits"], before["hits"])
        self.assertEqual(after["misses"], before["misses"])

    # ---- bounds -----------------------------------------------------------------------------------

    def testByteCapEvictsAndResultsStayEqual(self):
        os.environ["CADFORK_HLR_CACHE_MB"] = "0.001"
        for scale in (0.9, 0.8, 0.7, 0.6):
            self.view.Scale = scale
            self.doc.recompute()
        s = stats()
        self.assertEqual(s["entries"], 1)
        self.assertGreater(s["evictions"], 0)
        capped = snapshot(self.view)
        del os.environ["CADFORK_HLR_CACHE_MB"]
        self.assertEqual(self.fresh(), capped)

    def testClosingDocumentDropsItsEntries(self):
        self.assertGreaterEqual(stats()["entries"], 1)
        FreeCAD.closeDocument("HlrCacheDoc")
        self.assertEqual(stats()["entries"], 0)
        self.assertEqual(stats()["bytes"], 0)

    def testDeletingAViewDropsItsEntries(self):
        other = self.doc.addObject("TechDraw::DrawViewPart", "Other")
        self.page.addView(other)
        other.Source = [self.blk]
        other.Direction = V(0, -1, 0)
        other.ScaleType = "Custom"
        other.Scale = 1.0
        self.doc.recompute()
        entries = stats()["entries"]
        self.page.removeView(other)
        self.doc.removeObject(other.Name)
        self.doc.recompute()
        self.assertLess(stats()["entries"], entries)

    # ---- projection group items -----------------------------------------------------------------

    def testDeletingAViewDropsItsEntriesWithUndoOn(self):
        self.doc.UndoMode = 1
        other = self.doc.addObject("TechDraw::DrawViewPart", "Other")
        self.page.addView(other)
        other.Source = [self.blk]
        other.Direction = V(0, -1, 0)
        other.ScaleType = "Custom"
        other.Scale = 1.0
        self.doc.recompute()
        entries = stats()["entries"]
        self.doc.openTransaction("delete view")
        self.page.removeView(other)
        self.doc.removeObject(other.Name)
        self.doc.commitTransaction()
        self.doc.recompute()
        self.assertLess(stats()["entries"], entries)

    def testRealUndoAndRedoHit(self):
        self.doc.UndoMode = 1
        first = snapshot(self.view)
        self.doc.openTransaction("move view")
        self.view.X = self.view.X.Value + 10
        self.doc.recompute()
        self.doc.commitTransaction()
        moved = snapshot(self.view)
        self.assertEqual(moved, first)
        hits, misses = self.recompute_counting(lambda: self.doc.undo())
        self.assertGreaterEqual(hits, 1)
        self.assertEqual(misses, 0)
        self.assertEqual(snapshot(self.view), first)
        hits, misses = self.recompute_counting(lambda: self.doc.redo())
        self.assertGreaterEqual(hits, 1)
        self.assertEqual(misses, 0)
        self.assertEqual(snapshot(self.view), first)

    def testProjectionGroupItemsHit(self):
        group = self.doc.addObject("TechDraw::DrawProjGroup", "Group")
        self.page.addView(group)
        group.Source = [self.blk, self.link]
        group.ScaleType = "Custom"
        group.Scale = 1.0
        group.addProjection("Front")
        group.addProjection("Top")
        self.doc.recompute()
        items = list(group.Views)
        first = [snapshot(i) for i in items]
        before = stats()
        group.X = group.X.Value + 20
        self.doc.recompute()
        for item in items:
            item.touch()
        self.doc.recompute()
        after = stats()
        self.assertGreaterEqual(after["hits"] - before["hits"], len(items))
        self.assertEqual(after["misses"], before["misses"])
        self.assertEqual([snapshot(i) for i in items], first)


if __name__ == "__main__":
    unittest.main()
