# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *   Copyright (c) 2026 The FreeCAD project association AISBL              *
# *                                                                         *
# *   This file is part of FreeCAD.                                         *
# *                                                                         *
# *   FreeCAD is free software: you can redistribute it and/or modify it    *
# *   under the terms of the GNU Lesser General Public License as           *
# *   published by the Free Software Foundation, either version 2.1 of the  *
# *   License, or (at your option) any later version.                       *
# *                                                                         *
# *   FreeCAD is distributed in the hope that it will be useful, but        *
# *   WITHOUT ANY WARRANTY; without even the implied warranty of            *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU     *
# *   Lesser General Public License for more details.                      *
# *                                                                         *
# *   You should have received a copy of the GNU Lesser General Public      *
# *   License along with FreeCAD. If not, see                               *
# *   <https://www.gnu.org/licenses/>.                                      *
# *                                                                         *
# ***************************************************************************

"""Tests for SketcherDiagnosis (G10 slice 1, acceptance checks 1 to 7)."""

import io
import json
import time
import unittest
import zipfile

import FreeCAD
import Part
import Sketcher

import SketcherDiagnosis as SD
from SketcherTests import SketchDiagnosisCorpus as Corpus

MOVE_TOLERANCE = 1e-9
CULPRIT_RATE_MIN = 0.80
PLANTED_TIME_LIMIT = 20.0


def _scratch_copy(sketch):
    """A copy of `sketch` in a hidden document, for the tests' own checks."""
    doc = FreeCAD.newDocument("TestDiagnosisCheck", "TestDiagnosisCheck", True, True)
    return doc, doc.copyObject(sketch, True)


def _close(doc, previous):
    FreeCAD.closeDocument(doc.Name)
    if previous is not None and previous in FreeCAD.listDocuments():
        FreeCAD.setActiveDocument(previous)


def _active_name():
    return FreeCAD.ActiveDocument.Name if FreeCAD.ActiveDocument is not None else None


class _Check:
    """Independent solver checks on a copy, not using SketcherDiagnosis internals."""

    def __init__(self, sketch):
        self.previous = _active_name()
        self.doc, self.sk = _scratch_copy(sketch)
        cons = self.sk.Constraints
        self.candidates = [
            i
            for i, c in enumerate(cons)
            if c.IsActive and c.Driving and c.Type != "InternalAlignment"
        ]

    def conflicts_with_only(self, members):
        keep = set(members)
        for i in self.candidates:
            self.sk.setActive(i, i in keep)
        self.sk.solve()
        return [i - 1 for i in self.sk.ConflictingConstraints]

    def close(self):
        _close(self.doc, self.previous)


def _content(sketch):
    """The sketch's serialised members; the zip wrapper carries timestamps."""
    z = zipfile.ZipFile(io.BytesIO(bytes(sketch.dumpContent())))
    return {n: z.read(n) for n in z.namelist()}


def _signature(sketch):
    doc = sketch.Document
    return {
        "content": _content(sketch),
        "undo": doc.UndoCount,
        "redo": doc.RedoCount,
        "states": [(o.Name, tuple(o.State)) for o in doc.Objects],
        "documents": sorted(FreeCAD.listDocuments().keys()),
        "active": _active_name(),
        "constraints": [
            (c.Type, c.Name, c.First, c.FirstPos, c.Second, c.SecondPos, c.Value, c.Driving)
            for c in sketch.Constraints
        ],
        "geometry": [str(g) for g in sketch.Geometry],
        "expressions": list(sketch.ExpressionEngine),
    }


class TestSketchDiagnosis(unittest.TestCase):
    """The corpus is built once and diagnosed once; the tests share the results."""

    cases = None
    results = None
    seconds = None
    extra_docs = None
    doc = None
    stats = {}

    @classmethod
    def setUpClass(cls):
        cls.previous = _active_name()
        cls.doc = FreeCAD.newDocument("SketchDiagnosisCorpus")
        cls.cases, cls.extra_docs = Corpus.build_corpus(
            cls.doc, scale_sizes=(10, 100, 1000), real=True
        )
        cls.results = {}
        cls.seconds = {}
        for case in cls.cases:
            t0 = time.time()
            cls.results[case.name] = SD.diagnose(case.sketch)
            cls.seconds[case.name] = time.time() - t0

    @classmethod
    def tearDownClass(cls):
        names = [cls.doc.Name] + [d.Name for d in cls.extra_docs.values()]
        for case in cls.cases:
            if case.doc is not None and case.doc.Name not in names:
                names.append(case.doc.Name)
        for n in names:
            if n in FreeCAD.listDocuments():
                FreeCAD.closeDocument(n)
        if cls.previous is not None and cls.previous in FreeCAD.listDocuments():
            FreeCAD.setActiveDocument(cls.previous)

    def by_class(self, *classes):
        return [c for c in self.cases if c.cls in classes]

    # -- check 1 ----------------------------------------------------------
    def testCorpusShape(self):
        names = [c.name for c in self.cases]
        self.assertEqual(len(names), len(set(names)), "case names must be unique")
        self.assertGreaterEqual(len(self.cases), 60)
        counts = {}
        for c in self.cases:
            counts[c.cls] = counts.get(c.cls, 0) + 1
        self.assertGreaterEqual(counts.get("real", 0), 10)
        for cls in ("full", "under", "conflict", "redundant", "reference", "scale", "scale_planted"):
            self.assertGreater(counts.get(cls, 0), 0, cls)
        # the specific items check 1 names
        for need in (
            "triangle_3_4_5",
            "tangent_circle_chain",
            "slot",
            "hexagon_from_radius",
            "ellipse_8x4",
            "quarter_arc",
            "bspline_four_poles",
            "single_conflict",
            "two_independent_conflicts",
            "two_overlapping_conflicts",
            "expression_driven_conflict",
            "external_geometry_conflict",
            "duplicate_dimension",
            "construction_line_conflict",
            "reference_dimension",
            "scale_10",
            "scale_100",
            "scale_1000",
            "scale_1000_planted",
        ):
            self.assertIn(need, names)
        self.__class__.stats["case_counts"] = counts

    def testSolvedCoordinates(self):
        checked = 0
        for case in self.by_class("full"):
            sk = case.sketch
            self.assertEqual(sk.solve(), 0, case.name)
            for (geo, pos), (x, y) in case.truth["points"].items():
                p = sk.getPoint(geo, pos)
                self.assertAlmostEqual(p.x, x, 3, "%s (%d,%d)" % (case.name, geo, pos))
                self.assertAlmostEqual(p.y, y, 3, "%s (%d,%d)" % (case.name, geo, pos))
                checked += 1
        self.assertGreater(checked, 25)

    # -- check 2 ----------------------------------------------------------
    def testDof(self):
        agree = total = 0
        for case in self.cases:
            want = case.truth["dof"]
            if want is None or case.truth["conflicting"]:
                continue
            total += 1
            got = self.results[case.name]["dof"]
            self.assertEqual(got, want, "DOF of %s" % case.name)
            agree += 1
        self.assertEqual(agree, total)
        self.assertGreaterEqual(total, 35)
        self.__class__.stats["dof_agree"] = "%d/%d" % (agree, total)

    def testRealSketchDofMatchesDirectSolve(self):
        for case in self.by_class("real"):
            sk = case.sketch
            sk.solve()
            r = self.results[case.name]
            self.assertEqual(r["dof"], sk.DoF, case.name)
            self.assertEqual(r["conflict_sets"], [], case.name)

    # -- check 3 ----------------------------------------------------------
    def conflicting_cases(self):
        return self.by_class("conflict", "reference", "scale_planted", "real_planted")

    def testConflictSetsAreMinimal(self):
        checked = sets_total = 0
        for case in self.conflicting_cases():
            if not case.truth["conflicting"]:
                continue
            r = self.results[case.name]
            self.assertTrue(r["conflict_sets"], "no conflict set for %s" % case.name)
            self.assertTrue(r["complete"], case.name)
            chk = _Check(case.sketch)
            try:
                for cs in r["conflict_sets"]:
                    members = cs["members"]
                    sets_total += 1
                    self.assertTrue(
                        chk.conflicts_with_only(members), "set not conflicting: %s" % case.name
                    )
                    for m in members:
                        rest = [x for x in members if x != m]
                        self.assertFalse(
                            chk.conflicts_with_only(rest),
                            "set of %s not minimal: %d droppable" % (case.name, m),
                        )
            finally:
                chk.close()
            checked += 1
        self.assertGreater(checked, 25)
        self.__class__.stats["minimal_sets"] = "%d sets in %d cases" % (sets_total, checked)

    def testKnownConflictSetsAreExact(self):
        exact = 0
        for case in self.conflicting_cases():
            want = case.truth["conflict_sets"]
            if want is None:
                continue
            got = sorted(sorted(cs["members"]) for cs in self.results[case.name]["conflict_sets"])
            self.assertEqual(got, sorted(sorted(w) for w in want), case.name)
            exact += 1
        self.assertGreaterEqual(exact, 15)
        self.__class__.stats["exact_sets"] = exact

    def testPlantedRealConflictsContainAnchorAndPlanted(self):
        planted = self.by_class("real_planted")
        self.assertGreaterEqual(len(planted), 10)
        for case in planted:
            r = self.results[case.name]
            hit = [
                cs
                for cs in r["conflict_sets"]
                if case.truth["culprit"] in cs["members"] and case.truth["anchor"] in cs["members"]
            ]
            self.assertTrue(hit, case.name)

    def testTopFixOfEverySetClearsTheSketch(self):
        for case in self.conflicting_cases():
            if not case.truth["conflicting"]:
                continue
            r = self.results[case.name]
            plan = r.get("plan")
            self.assertIsNotNone(plan, case.name)
            self.assertTrue(plan["clean"], case.name)
            chk = _Check(case.sketch)
            try:
                SD.apply_fix(chk.sk, plan)
                chk.sk.solve()
                self.assertEqual(chk.sk.ConflictingConstraints, [], case.name)
                deleted = sorted(o["index"] for o in plan["ops"] if o["op"] == "delete")
                keep = [i for i in range(case.sketch.ConstraintCount) if i not in deleted]
                red = {
                    keep[i - 1]
                    for i in list(chk.sk.RedundantConstraints)
                    + list(chk.sk.PartiallyRedundantConstraints)
                }
                self.assertFalse(red - set(r["redundancy_members"]), case.name)
                self.assertEqual(chk.sk.DoF, plan["dof_after"], case.name)
            finally:
                chk.close()

    # -- check 4 ----------------------------------------------------------
    def testEveryOfferedFixWorksOnTheRealSketch(self):
        offered = failed = 0
        for case in self.conflicting_cases():
            if not case.truth["conflicting"]:
                continue
            r = self.results[case.name]
            for cs in r["conflict_sets"]:
                self.assertTrue(cs["fixes"], "no fix for a set of %s" % case.name)
                for fx in cs["fixes"]:
                    offered += 1
                    chk = _Check(case.sketch)
                    try:
                        ok = True
                        try:
                            SD.apply_fix(chk.sk, fx)
                            chk.sk.Document.recompute()
                            chk.sk.solve()
                        except Exception:
                            ok = False
                        unresolved = set()
                        for k, other in enumerate(r["conflict_sets"]):
                            if k not in fx["resolves"]:
                                unresolved |= set(other["members"])
                        deleted = sorted(o["index"] for o in fx["ops"] if o["op"] == "delete")
                        keep = [i for i in range(case.sketch.ConstraintCount) if i not in deleted]
                        left = {keep[i - 1] for i in chk.sk.ConflictingConstraints}
                        red = {
                            keep[i - 1]
                            for i in list(chk.sk.RedundantConstraints)
                            + list(chk.sk.PartiallyRedundantConstraints)
                        }
                        broken = (
                            ok
                            and not left
                            and not red
                            and "Invalid" in chk.sk.State
                        )
                        if (
                            not ok
                            or broken
                            or left - unresolved
                            or red - set(r["redundancy_members"])
                            or chk.sk.DoF != fx["dof_after"]
                        ):
                            failed += 1
                            self.fail_detail = (case.name, fx["description"])
                    finally:
                        chk.close()
        self.assertGreater(offered, 60)
        self.assertEqual(failed, 0, "%d of %d offered fixes failed" % (failed, offered))
        self.__class__.stats["fixes_offered"] = offered
        self.__class__.stats["fixes_failed"] = failed

    def testAlternativesResolveTheirSet(self):
        for case in self.conflicting_cases():
            r = self.results[case.name]
            for cs in r["conflict_sets"]:
                for fx in cs["alternatives"]:
                    self.assertTrue(fx["leaves_redundancy"], case.name)
                    chk = _Check(case.sketch)
                    try:
                        SD.apply_fix(chk.sk, fx)
                        chk.sk.solve()
                        left = set(i - 1 for i in chk.sk.ConflictingConstraints)
                        self.assertFalse(left & set(cs["members"]), case.name)
                    finally:
                        chk.close()

    def testCulpritRate(self):
        labelled = hit = 0
        misses = []
        for case in self.conflicting_cases():
            culprit = case.truth["culprit"]
            if culprit is None:
                continue
            r = self.results[case.name]
            labelled += 1
            top = None
            for cs in r["conflict_sets"]:
                if culprit in cs["members"] and cs["fixes"]:
                    top = cs["fixes"][0]
                    break
            if top is not None and any(o["index"] == culprit for o in top["ops"]):
                hit += 1
            else:
                misses.append(case.name)
        self.assertGreaterEqual(labelled, 25)
        rate = hit / float(labelled)
        self.__class__.stats["culprit_rate"] = "%d/%d = %.3f; misses %s" % (
            hit,
            labelled,
            rate,
            misses,
        )
        self.assertGreaterEqual(rate, CULPRIT_RATE_MIN, misses)

    # -- check 5 ----------------------------------------------------------
    def testRedundancyGroups(self):
        cases = [c for c in self.cases if c.truth["redundant"]]
        self.assertGreaterEqual(len(cases), 6)
        for case in cases:
            r = self.results[case.name]
            groups = r["redundancy_groups"]
            self.assertTrue(groups, case.name)
            self.assertEqual(len(groups), case.truth["red_removals"], case.name)
            want = case.truth["red_groups"]
            if want is not None:
                self.assertEqual(
                    sorted(sorted(g["members"]) for g in groups),
                    sorted(sorted(w) for w in want),
                    case.name,
                )
            self.assertEqual(r["status"], "redundant", case.name)
            base = [_pos(case.sketch)]
            for g in groups:
                self.assertIn(g["suggested_removal"], g["members"])
            # removing one suggestion at a time keeps DOF and positions
            for g in groups:
                chk = _Check(case.sketch)
                try:
                    chk.sk.solve()
                    before = _pos(chk.sk)
                    dof = chk.sk.DoF
                    chk.sk.delConstraint(g["suggested_removal"])
                    chk.sk.solve()
                    self.assertEqual(chk.sk.DoF, dof, case.name)
                    self.assertLessEqual(_dist(before, _pos(chk.sk)), MOVE_TOLERANCE, case.name)
                finally:
                    chk.close()
            # removing all suggestions leaves no redundancy
            chk = _Check(case.sketch)
            try:
                chk.sk.solve()
                dof = chk.sk.DoF
                for i in sorted((g["suggested_removal"] for g in groups), reverse=True):
                    chk.sk.delConstraint(i)
                chk.sk.solve()
                self.assertEqual(chk.sk.RedundantConstraints, [], case.name)
                self.assertEqual(chk.sk.PartiallyRedundantConstraints, [], case.name)
                self.assertEqual(chk.sk.DoF, dof, case.name)
            finally:
                chk.close()
            del base

    def testNoRedundancyReportedOnCleanSketches(self):
        for case in self.cases:
            if case.cls in ("full", "under", "real") and case.name != "bspline_four_poles":
                r = self.results[case.name]
                if r["status"] == "solved":
                    self.assertEqual(r["redundancy_groups"], [], case.name)

    # -- reference, inactive, construction, limits ------------------------
    def testReferenceAndInactiveAreRecognised(self):
        for case in self.by_class("reference"):
            r = self.results[case.name]
            self.assertEqual(r["reference_constraints"], case.truth["reference"], case.name)
            self.assertEqual(r["inactive_constraints"], case.truth["inactive"], case.name)
            for cs in r["conflict_sets"]:
                for m in cs["members"]:
                    self.assertNotIn(m, case.truth["reference"], case.name)
                    self.assertNotIn(m, case.truth["inactive"], case.name)

    def testNonConflictingReferenceCasesHaveNoSets(self):
        for case in self.by_class("reference"):
            if not case.truth["conflicting"]:
                r = self.results[case.name]
                self.assertEqual(r["conflict_sets"], [], case.name)
                self.assertEqual(r["status"], "solved", case.name)

    def testSolverLimitsAreReportedHonestly(self):
        for case in self.by_class("limit"):
            r = self.results[case.name]
            self.assertEqual(r["status"], "solver_failed", case.name)
            self.assertEqual(r["conflict_sets"], [], case.name)

    # -- check 6 ----------------------------------------------------------
    def sideeffect_cases(self):
        names = [
            "single_conflict",
            "two_overlapping_conflicts",
            "expression_driven_conflict",
            "external_geometry_conflict",
            "duplicate_dimension",
            "rectangle_10x5",
            "reference_beside_conflict",
            "partdesign_example_sketch001",
            "tangent_mode_sketch002_planted",
            "scale_100",
        ]
        by_name = {c.name: c for c in self.cases}
        return [by_name[n] for n in names]

    def testDiagnosisLeavesTheDocumentUntouched(self):
        for case in self.sideeffect_cases():
            before = _signature(case.sketch)
            SD.diagnose(case.sketch)
            self.assertEqual(before, _signature(case.sketch), case.name)

    def testDiagnosisLeavesUndoStackAndTouchedState(self):
        doc = FreeCAD.newDocument("SideEffectUndo")
        try:
            doc.UndoMode = 1
            doc.openTransaction("build")
            sk = doc.addObject("Sketcher::SketchObject", "S")
            sk.addGeometry(Part.LineSegment(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(5, 0, 0)), False)
            sk.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 5.0))
            sk.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 6.0))
            doc.commitTransaction()
            doc.recompute()
            doc.openTransaction("pending")
            sk.touch()
            before = _signature(sk)
            r = SD.diagnose(sk)
            self.assertTrue(r["conflict_sets"])
            self.assertEqual(before, _signature(sk))
            self.assertGreaterEqual(doc.UndoCount, 1)
            doc.commitTransaction()
        finally:
            FreeCAD.closeDocument(doc.Name)
            if self.previous is not None and self.previous in FreeCAD.listDocuments():
                FreeCAD.setActiveDocument(self.previous)

    def testDiagnosisLeavesNoTraceWhenItRaises(self):
        case = [c for c in self.cases if c.name == "two_overlapping_conflicts"][0]
        original = SD._enumerate_conflict_sets

        def boom(*args, **kwargs):
            raise RuntimeError("injected failure")

        before = _signature(case.sketch)
        SD._enumerate_conflict_sets = boom
        try:
            with self.assertRaises(RuntimeError):
                SD.diagnose(case.sketch)
        finally:
            SD._enumerate_conflict_sets = original
        self.assertEqual(before, _signature(case.sketch))

    def testDiagnoseRejectsOtherObjects(self):
        box = self.doc.addObject("Part::Box", "NotASketch")
        before = sorted(FreeCAD.listDocuments().keys())
        with self.assertRaises(SD.DiagnosisError):
            SD.diagnose(box)
        self.assertEqual(before, sorted(FreeCAD.listDocuments().keys()))
        self.doc.removeObject(box.Name)

    # -- check 7 ----------------------------------------------------------
    def testScaleSeriesIsWellConstrained(self):
        for n in (10, 100, 1000):
            case = [c for c in self.cases if c.name == "scale_%d" % n][0]
            self.assertEqual(case.sketch.ConstraintCount, n)
            r = self.results[case.name]
            self.assertEqual(r["dof"], 0)
            self.assertEqual(r["status"], "solved")

    def testScale5000IsWellConstrained(self):
        doc = FreeCAD.newDocument("Scale5000Check")
        try:
            sk, _ = Corpus.build_scale(doc, "s5000", 5000)
            self.assertEqual(sk.ConstraintCount, 5000)
            self.assertEqual(sk.solve(), 0)
            self.assertEqual(sk.DoF, 0)
        finally:
            FreeCAD.closeDocument(doc.Name)
            if self.previous is not None and self.previous in FreeCAD.listDocuments():
                FreeCAD.setActiveDocument(self.previous)

    def testPlantedThousandConstraintConflict(self):
        case = [c for c in self.cases if c.name == "scale_1000_planted"][0]
        t0 = time.time()
        r = SD.diagnose(case.sketch)
        seconds = time.time() - t0
        self.assertLess(seconds, PLANTED_TIME_LIMIT)
        self.assertEqual(
            [cs["members"] for cs in r["conflict_sets"]], case.truth["conflict_sets"]
        )
        top = r["conflict_sets"][0]["fixes"][0]
        self.assertTrue(any(o["index"] == case.truth["culprit"] for o in top["ops"]))
        self.__class__.stats["planted_seconds"] = seconds

    def testResultIsJsonSerialisable(self):
        for name in ("two_overlapping_conflicts", "duplicate_dimension", "scale_100"):
            json.dumps(self.results[name])


def _pos(sketch):
    return SD._geometry_signature(sketch)


def _dist(a, b):
    return SD._max_move(a, b)
