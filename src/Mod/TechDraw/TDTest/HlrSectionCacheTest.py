# SPDX-License-Identifier: LGPL-2.1-or-later

# Tests of the hidden-line cache for plain section views (slice 8c, rulings 134 to 137).  A hit has
# to give exactly what a miss gives, and every term of the key has to cause a miss when it changes.
# Public sources: OCCT reference documentation for BRepAlgoAPI_Cut, BinTools and HLRBRep_Algo;
# FreeCAD's TechDraw DrawViewSection.cpp.

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import FreeCAD
import Part
import TechDraw
from PySide import QtCore

from . import HlrSectionScenario as scenario

V = FreeCAD.Vector

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "TestTemplate.svg")


def stats():
    return TechDraw.hlrCacheStats()


def snap(view):
    return scenario.edges_of(view)


class HlrSectionCacheTest(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("CADFORK_HLR_CACHE", "CADFORK_HLR_CACHE_MB")}
        for k in self._env:
            os.environ.pop(k, None)
        TechDraw.clearHlrCache()
        self.doc = FreeCAD.newDocument("HlrSecDoc")
        self.blk, self.link = scenario.build_model(self.doc)
        self.page, self.base, self.sec, self.det, self.sos = scenario.build_page(
            self.doc, self.blk, self.link, TEMPLATE)
        self.doc.recompute()
        self.dbg = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/TechDraw/debug")
        self.gen = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/TechDraw/General")
        self._prefs = {
            "HandleFaces": self.gen.GetBool("HandleFaces", True),
            "IdentifyVoids": self.gen.GetBool("IdentifyVoids", True),
            "NewFaceFinder": self.gen.GetBool("NewFaceFinder", True),
            "CheckShapesBeforeUse": self.gen.GetBool("CheckShapesBeforeUse", False),
            "ScrubCount": self.gen.GetInt("ScrubCount", 1),
            "FaceFinderVersion": self.gen.GetInt("FaceFinderVersion", -1),
        }
        self._crazy = self.dbg.GetBool("allowCrazyEdge", False)

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for k in ("HandleFaces", "IdentifyVoids", "NewFaceFinder", "CheckShapesBeforeUse"):
            self.gen.SetBool(k, self._prefs[k])
        self.gen.SetInt("ScrubCount", self._prefs["ScrubCount"])
        self.gen.SetInt("FaceFinderVersion", self._prefs["FaceFinderVersion"])
        self.dbg.SetBool("allowCrazyEdge", self._crazy)
        if "HlrSecDoc" in FreeCAD.listDocuments():
            FreeCAD.closeDocument("HlrSecDoc")
        TechDraw.clearHlrCache()

    # ---- helpers -----------------------------------------------------------------------------

    def run_only_section(self, change=None):
        """Change something, then recompute only the section (touch), and return its hits and misses."""
        before = stats()
        if change:
            change()
        self.sec.touch()
        self.doc.recompute()
        after = stats()
        return after["hits"] - before["hits"], after["misses"] - before["misses"]

    def fresh(self, view):
        """What a miss gives for the present state (the cache is emptied first)."""
        TechDraw.clearHlrCache()
        view.touch()
        self.doc.recompute()
        return snap(view)

    def off(self, view):
        old = os.environ.get("CADFORK_HLR_CACHE")
        os.environ["CADFORK_HLR_CACHE"] = "0"
        try:
            view.touch()
            self.doc.recompute()
            return snap(view)
        finally:
            if old is None:
                del os.environ["CADFORK_HLR_CACHE"]
            else:
                os.environ["CADFORK_HLR_CACHE"] = old

    # ---- hits ------------------------------------------------------------------------------------

    def testSectionCountsAHitAndEqualsAMiss(self):
        first = snap(self.sec)
        self.assertTrue(first[0])
        hits, misses = self.run_only_section()
        self.assertEqual((hits, misses), (1, 0))
        self.assertEqual(snap(self.sec), first)
        self.assertEqual(self.fresh(self.sec), first)

    def testMoveUndoRedoHit(self):
        self.doc.UndoMode = 1
        first = snap(self.sec)
        self.doc.openTransaction("move")
        self.sec.X = self.sec.X.Value + 10
        self.doc.recompute()
        self.doc.commitTransaction()
        self.assertEqual(snap(self.sec), first)
        for step in (lambda: self.doc.undo(), lambda: self.doc.redo()):
            hits, misses = self.run_only_section(step)
            self.assertEqual((hits, misses), (1, 0))
            self.assertEqual(snap(self.sec), first)

    def testHatchAndScaleBackAndSameValueEditHit(self):
        first = snap(self.sec)
        hits, misses = self.run_only_section(lambda: setattr(self.sec, "HatchScale", 2.0))
        self.assertEqual((hits, misses), (1, 0))
        hits, misses = self.run_only_section(lambda: setattr(self.sec, "CutSurfaceDisplay", "Hide"))
        self.assertEqual((hits, misses), (1, 0))
        hits, misses = self.run_only_section(lambda: setattr(self.sec, "Scale", 0.5))
        self.assertEqual(hits, 0)
        self.assertGreaterEqual(misses, 1)
        hits, misses = self.run_only_section(lambda: setattr(self.sec, "Scale", 1.0))
        self.assertEqual((hits, misses), (1, 0))
        self.assertEqual(snap(self.sec), first)

    def testLinkTouchAndBaseMoveHit(self):
        first = snap(self.sec)
        before = stats()
        self.link.touch()
        self.doc.recompute()
        after = stats()
        self.assertGreaterEqual(after["hits"] - before["hits"], 2)  # the base view and the section
        self.assertEqual(after["misses"], before["misses"])
        self.assertEqual(snap(self.sec), first)
        before = stats()
        self.blk.touch()  # same-value edit
        self.doc.recompute()
        after = stats()
        self.assertGreaterEqual(after["hits"] - before["hits"], 2)
        self.assertEqual(after["misses"], before["misses"])
        before = stats()
        self.base.X = self.base.X.Value + 20
        self.doc.recompute()
        self.sec.touch()
        self.doc.recompute()
        after = stats()
        self.assertGreaterEqual(after["hits"] - before["hits"], 1)
        self.assertEqual(after["misses"], before["misses"])
        self.assertEqual(snap(self.sec), first)

    def testDependentsFollowAHit(self):
        # the detail of the section reads the cut shape; a hit must still hand it over
        first = snap(self.det)
        self.assertTrue(first[0])
        hits, misses = self.run_only_section()
        self.assertEqual((hits, misses), (1, 0))
        self.assertEqual(snap(self.det), first)
        self.assertEqual(self.fresh(self.det), first)
        self.sec.SectionOrigin = V(15, 10, 2)
        self.doc.recompute()
        moved = snap(self.det)
        self.assertNotEqual(moved, first)
        hits, misses = self.run_only_section()
        self.assertEqual((hits, misses), (1, 0))
        self.assertEqual(snap(self.det), moved)
        self.assertEqual(self.fresh(self.det), moved)

    def testSectionOfASectionRecomputesOnceAfterAHit(self):
        # R25 (ruling 142): the busy flag of a section is cleared on the main thread when its cut is
        # handled, never on the cut's worker thread.  Headless the cut runs synchronously, and
        # waitingForCut is not exposed to Python, so what this reaches is the result: after a hit
        # of the parent, its section of a section (uncached) is up to date, is not touched again
        # by a second recompute, and still follows a later change of the parent.
        first = snap(self.sos)
        self.assertTrue(first[0])
        hits, misses = self.run_only_section()
        self.assertEqual((hits, misses), (1, 0))
        self.assertNotIn("Touched", self.sos.State)
        self.assertEqual(snap(self.sos), first)
        self.assertEqual(self.doc.recompute(), 0)
        self.assertEqual(snap(self.sos), first)
        self.assertEqual(self.fresh(self.sos), first)
        self.sec.FuseBeforeCut = True
        self.doc.recompute()
        moved = snap(self.sos)
        hits, misses = self.run_only_section()
        self.assertEqual((hits, misses), (1, 0))
        self.assertNotIn("Touched", self.sos.State)
        self.assertEqual(snap(self.sos), moved)
        self.assertEqual(self.fresh(self.sos), moved)

    def testCosmeticsDoNotLeak(self):
        first = snap(self.sec)
        n = len(self.sec.getVisibleEdges())
        self.sec.makeCosmeticLine(V(0, 0, 0), V(5, 5, 0))
        hits, misses = self.run_only_section()
        self.assertEqual((hits, misses), (1, 0))
        self.assertEqual(len(self.sec.getVisibleEdges()), n + 1)
        self.sec.clearCosmeticEdges()
        hits, misses = self.run_only_section()
        self.assertEqual((hits, misses), (1, 0))
        self.assertEqual(snap(self.sec), first)

    def testSectionFacesAndCutShapeEqualAMiss(self):
        # the debug dumps of the cut shape, the prepared shape and the final section faces
        d = tempfile.mkdtemp(prefix="hlr8c_dump_")
        old = os.getcwd()
        os.chdir(d)
        self.dbg.SetBool("debugSection", True)
        try:
            def dump():
                for n in scenario.DUMPS:
                    if os.path.exists(n):
                        os.remove(n)
                self.sec.touch()
                self.doc.recompute()
                return scenario.dumps_of(d)

            TechDraw.clearHlrCache()
            miss = dump()
            self.assertIn("DVSCutShape.brep", miss)
            self.assertIn("DVSFinalSectionFaces.brep", miss)
            before = stats()["hits"]
            hit = dump()
            self.assertEqual(stats()["hits"] - before, 1)
            self.assertEqual(hit, miss)
            self.assertGreater(miss["DVSFinalSectionFaces.brep"][2], 0)  # there are section faces
        finally:
            self.dbg.SetBool("debugSection", False)
            os.chdir(old)
            shutil.rmtree(d, ignore_errors=True)

    def testFacesAreReusedWithTheirSectionFaces(self):
        # hits that take cached faces still carry the section faces of the miss
        d = tempfile.mkdtemp(prefix="hlr8c_dump_")
        old = os.getcwd()
        os.chdir(d)
        self.dbg.SetBool("debugSection", True)
        try:
            for n in scenario.DUMPS:
                if os.path.exists(n):
                    os.remove(n)
            TechDraw.clearHlrCache()
            self.sec.touch()
            self.doc.recompute()
            miss = scenario.dumps_of(d)
            for i in range(3):
                self.sec.touch()
                self.doc.recompute()
                self.assertEqual(scenario.dumps_of(d), miss)
        finally:
            self.dbg.SetBool("debugSection", False)
            os.chdir(old)
            shutil.rmtree(d, ignore_errors=True)

    # ---- every key term causes a miss ---------------------------------------------------------------

    def check_term(self, label, change, restore=None):
        hits, misses = self.run_only_section(change)
        self.assertEqual(hits, 0, label)
        self.assertGreaterEqual(misses, 1, label)
        changed = snap(self.sec)
        self.assertEqual(changed, self.off(self.sec), label)  # never the old result
        if restore:
            restore()
            self.sec.touch()
            self.doc.recompute()

    def testEveryKeyTermMisses(self):
        s = self.sec
        terms = [
            ("Scale", lambda: setattr(s, "Scale", 0.5), lambda: setattr(s, "Scale", 1.0)),
            ("Rotation", lambda: setattr(s, "Rotation", 30.0), lambda: setattr(s, "Rotation", 0.0)),
            ("SectionOrigin", lambda: setattr(s, "SectionOrigin", V(15.001, 10, 5)),
             lambda: setattr(s, "SectionOrigin", V(15, 10, 5))),
            ("SectionNormal", lambda: setattr(s, "SectionNormal", V(0.2, 1, 0)),
             lambda: setattr(s, "SectionNormal", V(0, 1, 0))),
            ("XDirection", lambda: setattr(s, "XDirection", V(0.2, 0, 1)),
             lambda: setattr(s, "XDirection", V(1, 0, 0))),
            ("SectionDirection", lambda: setattr(s, "SectionDirection", "Up"),
             lambda: setattr(s, "SectionDirection", "Down")),
            ("FuseBeforeCut", lambda: setattr(s, "FuseBeforeCut", True), lambda: setattr(s, "FuseBeforeCut", False)),
            ("TrimAfterCut", lambda: setattr(s, "TrimAfterCut", True), lambda: setattr(s, "TrimAfterCut", False)),
            ("IsoCount", lambda: setattr(s, "IsoCount", 2), lambda: setattr(s, "IsoCount", 0)),
            ("Perspective", lambda: setattr(s, "Perspective", True), lambda: setattr(s, "Perspective", False)),
            ("Focus", lambda: setattr(s, "Focus", 55.0), lambda: setattr(s, "Focus", 100.0)),
            ("ScrubCount", lambda: setattr(s, "ScrubCount", 3), lambda: setattr(s, "ScrubCount", 1)),
        ]
        for flag in ("SmoothVisible", "SeamVisible", "IsoVisible", "HardHidden", "SmoothHidden",
                     "SeamHidden", "IsoHidden"):
            old = getattr(s, flag)
            terms.append(("flag " + flag, lambda f=flag, o=old: setattr(s, f, not o),
                          lambda f=flag, o=old: setattr(s, f, o)))
        for label, change, restore in terms:
            with self.subTest(term=label):
                TechDraw.clearHlrCache()
                self.doc.recompute()
                self.run_only_section()  # fills the entry of the unchanged state
                self.check_term(label, change, restore)

    def testSourceTermsMiss(self):
        for label, change in (
            ("link placement 0.001", lambda: setattr(self.link, "Placement", FreeCAD.Placement(
                V(50.001, 0, 0), FreeCAD.Rotation(V(0, 0, 1), 30)))),
            ("real edit", lambda: setattr(self.blk, "Shape", self.blk.Shape.cut(Part.makeCylinder(2, 12, V(6, 6, -1))))),
        ):
            with self.subTest(term=label):
                self.run_only_section()
                before = stats()
                change()
                self.doc.recompute()
                self.sec.touch()
                self.doc.recompute()
                after = stats()
                self.assertGreaterEqual(after["misses"] - before["misses"], 1, label)
                self.assertEqual(snap(self.sec), self.off(self.sec), label)

    def testSourceListChangeMisses(self):
        self.run_only_section()
        extra = self.doc.addObject("Part::Feature", "Extra")
        extra.Shape = Part.makeBox(4, 4, 4, V(10, 8, 3))
        self.base.Source = [self.blk, self.link, extra]
        self.doc.recompute()
        self.sec.touch()
        self.doc.recompute()
        self.assertEqual(snap(self.sec), self.off(self.sec))
        self.base.Source = [self.blk, self.link]
        self.doc.recompute()
        self.sec.touch()
        self.doc.recompute()
        self.assertEqual(snap(self.sec), self.off(self.sec))

    def testPreferenceTermsMiss(self):
        prefs = [
            ("HandleFaces", lambda: self.gen.SetBool("HandleFaces", not self._prefs["HandleFaces"])),
            ("IdentifyVoids", lambda: self.gen.SetBool("IdentifyVoids", not self._prefs["IdentifyVoids"])),
            ("NewFaceFinder", lambda: self.gen.SetBool("NewFaceFinder", not self._prefs["NewFaceFinder"])),
            ("CheckShapesBeforeUse", lambda: self.gen.SetBool("CheckShapesBeforeUse", not self._prefs["CheckShapesBeforeUse"])),
            ("ScrubCount", lambda: self.gen.SetInt("ScrubCount", self._prefs["ScrubCount"] + 1)),
            ("FaceFinderVersion", lambda: self.gen.SetInt("FaceFinderVersion", 1)),
            ("allowCrazyEdge", lambda: self.dbg.SetBool("allowCrazyEdge", not self._crazy)),
        ]
        for label, change in prefs:
            with self.subTest(pref=label):
                TechDraw.clearHlrCache()
                self.run_only_section()
                self.setUp_restore()
                hits, misses = self.run_only_section(change)
                self.assertEqual(hits, 0, label)
                self.assertGreaterEqual(misses, 1, label)
                self.assertEqual(snap(self.sec), self.off(self.sec), label)
                self.setUp_restore()

    def setUp_restore(self):
        for k in ("HandleFaces", "IdentifyVoids", "NewFaceFinder", "CheckShapesBeforeUse"):
            self.gen.SetBool(k, self._prefs[k])
        self.gen.SetInt("ScrubCount", self._prefs["ScrubCount"])
        self.gen.SetInt("FaceFinderVersion", self._prefs["FaceFinderVersion"])
        self.dbg.SetBool("allowCrazyEdge", self._crazy)

    # ---- exclusions (ruling 134) --------------------------------------------------------------------

    def assertNoSectionHit(self, view):
        before = stats()
        view.touch()
        self.doc.recompute()
        view.X = view.X.Value + 3
        self.doc.recompute()
        after = stats()
        self.assertEqual(after["hits"], before["hits"])
        self.assertEqual(after["misses"], before["misses"])
        self.assertEqual(snap(view), self.off(view))

    def testOffSwitch(self):
        os.environ["CADFORK_HLR_CACHE"] = "0"
        try:
            self.assertNoSectionHit(self.sec)
        finally:
            del os.environ["CADFORK_HLR_CACHE"]

    def testSectionOfASectionAndOfADetailNeverHit(self):
        self.assertNoSectionHit(self.sos)
        sod = self.doc.addObject("TechDraw::DrawViewSection", "SecOfDet")
        self.page.addView(sod)
        sod.BaseView = self.det
        sod.Source = [self.blk, self.link]
        sod.SectionNormal = V(1, 0, 0)
        sod.SectionDirection = "Right"
        sod.SectionOrigin = V(0, 0, 0)
        sod.ScaleType = "Custom"
        sod.Scale = 1.0
        self.doc.recompute()
        self.assertNoSectionHit(sod)

    def testAutomaticScaleNeverHits(self):
        self.sec.ScaleType = "Automatic"
        self.doc.recompute()
        self.assertNoSectionHit(self.sec)

    def testCoarseViewNeverHits(self):
        self.sec.CoarseView = True
        self.doc.recompute()
        self.assertNoSectionHit(self.sec)

    def testExplodedShapeHookNeverHits(self):
        class Exploder:
            def __init__(self, obj):
                obj.Proxy = self

            def execute(self, obj):
                pass

            def getExplodedShape(self, obj):
                return Part.makeBox(20, 20, 20, V(0, 0, 0))

        feat = self.doc.addObject("Part::FeaturePython", "Exploded")
        Exploder(feat)
        feat.Shape = Part.makeBox(20, 20, 20)
        base = self.doc.addObject("TechDraw::DrawViewPart", "ExBase")
        self.page.addView(base)
        base.Source = [feat]
        base.ScaleType = "Custom"
        base.Scale = 1.0
        sec = self.doc.addObject("TechDraw::DrawViewSection", "ExSec")
        self.page.addView(sec)
        sec.BaseView = base
        sec.Source = [feat]
        sec.SectionNormal = V(0, 1, 0)
        sec.SectionDirection = "Down"
        sec.SectionOrigin = V(10, 10, 10)
        sec.ScaleType = "Custom"
        sec.Scale = 1.0
        self.doc.recompute()
        before = stats()
        sec.touch()
        self.doc.recompute()
        sec.X = sec.X.Value + 3
        self.doc.recompute()
        after = stats()
        # the base view is excluded too, so nothing at all is counted
        self.assertEqual(after["hits"], before["hits"])

    def testComplexSectionsNeverHit(self):
        profile = self.doc.addObject("Part::Feature", "Profile")
        profile.Shape = Part.makePolygon([V(5, -5, 5), V(25, -5, 5), V(25, 25, 5)])
        for strategy in ("Offset", "Aligned"):
            with self.subTest(strategy=strategy):
                cs = self.doc.addObject("TechDraw::DrawComplexSection", "Cs" + strategy)
                self.page.addView(cs)
                cs.BaseView = self.base
                cs.Source = [self.blk, self.link]
                cs.CuttingToolWireObject = profile
                cs.ProjectionStrategy = strategy
                cs.SectionNormal = V(0, 0, 1)
                cs.SectionDirection = "Down"
                cs.SectionOrigin = V(15, 10, 5)
                cs.ScaleType = "Custom"
                cs.Scale = 1.0
                self.doc.recompute()
                before = stats()
                cs.touch()
                self.doc.recompute()
                after = stats()
                self.assertEqual(after["hits"], before["hits"])
                # an Aligned section runs its second cut in a thread that nothing waits for
                # headless; let it end before the document goes
                QtCore.QThreadPool.globalInstance().waitForDone()

    # ---- bounds ---------------------------------------------------------------------------------------

    def testBoundedByTheCap(self):
        os.environ["CADFORK_HLR_CACHE_MB"] = "0.001"
        for scale in (0.9, 0.8, 0.7):
            self.sec.Scale = scale
            self.sec.touch()
            self.doc.recompute()
        s = stats()
        self.assertGreater(s["evictions"], 0)
        capped = snap(self.sec)
        del os.environ["CADFORK_HLR_CACHE_MB"]
        self.assertEqual(self.fresh(self.sec), capped)

    def testClosingDocumentAndDeletingTheViewDropEntries(self):
        entries = stats()["entries"]
        self.assertGreaterEqual(entries, 2)
        self.page.removeView(self.sos)
        self.doc.removeObject(self.sos.Name)
        self.page.removeView(self.sec)
        # the detail depends on the section: remove it first
        self.doc.recompute()
        FreeCAD.closeDocument("HlrSecDoc")
        self.assertEqual(stats()["entries"], 0)
        self.assertEqual(stats()["bytes"], 0)

    # ---- the whole sequence against cache off in another process ---------------------------------------

    def testScenarioEqualsCacheOffInAnotherProcess(self):
        FreeCAD.closeDocument("HlrSecDoc")
        TechDraw.clearHlrCache()
        on = scenario.run(TEMPLATE)
        exe = os.path.join(FreeCAD.getHomePath(), "bin", "FreeCADCmd")
        if not os.path.exists(exe):
            self.skipTest("no FreeCADCmd next to the home path")
        scratch = tempfile.mkdtemp(prefix="hlr8c_off_")
        try:
            env = dict(os.environ)
            env.update({
                "CADFORK_HLR_CACHE": "0",
                "FREECAD_USER_HOME": os.path.join(scratch, "home"),
                "SCENARIO_OUT": os.path.join(scratch, "off.json"),
                "SCENARIO_TEMPLATE": TEMPLATE,
                "QT_QPA_PLATFORM": "offscreen",
            })
            os.makedirs(env["FREECAD_USER_HOME"])
            r = subprocess.run([exe, os.path.join(HERE, "HlrSectionScenario.py")], env=env,
                               cwd=scratch, capture_output=True, text=True, timeout=900)
            self.assertTrue(os.path.exists(env["SCENARIO_OUT"]), r.stdout[-2000:] + r.stderr[-2000:])
            off = json.load(open(env["SCENARIO_OUT"]))
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        on = json.loads(json.dumps(on))
        self.assertEqual([s[0] for s in on], [s[0] for s in off])
        for a, b in zip(on, off):
            self.assertEqual(a[1], b[1], a[0])
        # the cache did its work in this process and none in the other
        self.assertEqual(sum(s[2][0] for s in off), 0)
        by_label = {s[0]: s[2] for s in on}
        for label in ("undo", "redo", "scale back", "origin back", "normal back", "fuse back",
                      "trim back", "hatch", "cut surface display", "base moved", "link touch",
                      "same-value edit", "link moved back", "cosmetics cleared"):
            self.assertGreaterEqual(by_label[label][0], 1, label)


if __name__ == "__main__":
    unittest.main()
