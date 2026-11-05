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
    """Independent solver checks, not using SketcherDiagnosis internals.

    Every subset is tested on a brand-new sketch, never on a copy that earlier
    probes have already solved: the solver's conflict detection depends on the
    geometry it starts from.  With ``builder`` the sketch is rebuilt from the
    corpus builder in a new document; without, the untouched user sketch is
    copied afresh."""

    def __init__(self, sketch, builder=None):
        self.previous = _active_name()
        self.builder = builder
        self.source = sketch
        self.doc = FreeCAD.newDocument("TestDiagnosisCheck", "TestDiagnosisCheck", True, True)
        # one persistent copy for the tests that apply fixes to it; the subset
        # checks never touch it
        self.sk = self.doc.copyObject(sketch, True) if builder is None else None
        cons = sketch.Constraints
        self.candidates = [
            i
            for i, c in enumerate(cons)
            if c.IsActive and c.Driving and c.Type != "InternalAlignment"
        ]

    def _fresh(self):
        if self.builder is not None:
            sk = self.builder(self.doc)
            sk.Document.recompute()
            return sk
        return self.doc.copyObject(self.source, True)

    def conflicts_with_only(self, members):
        keep = set(members)
        sk = self._fresh()
        try:
            for i in self.candidates:
                sk.setActive(i, i in keep)
            sk.solve()
            return [i - 1 for i in sk.ConflictingConstraints]
        finally:
            self.doc.removeObject(sk.Name)

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

    def assertNoScratchDocument(self):
        left = [n for n in FreeCAD.listDocuments() if n.startswith("SketchDiagnosisScratch")]
        self.assertEqual(left, [])

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

    def testGeometryDependentConflictsAgainstFreshBruteForce(self):
        """Every subset of the candidates is solved on a sketch rebuilt from the
        corpus builder; the minimal conflicting subsets must equal the reported
        sets exactly (case readers_* once returned [2, 4, 5] and [1, 3, 4, 5])."""
        import itertools

        names = ("readers_expression", "readers_plain", "readers_arcs")
        for name in names:
            case = [c for c in self.cases if c.name == name][0]
            chk = _Check(case.sketch, case.builder)
            try:
                minimal = []
                for k in range(1, len(chk.candidates) + 1):
                    for sub in itertools.combinations(chk.candidates, k):
                        fs = frozenset(sub)
                        if any(m <= fs for m in minimal):
                            continue
                        if chk.conflicts_with_only(fs):
                            minimal.append(fs)
            finally:
                chk.close()
            brute = sorted(sorted(m) for m in minimal)
            self.assertEqual(brute, case.truth["conflict_sets"], name)
            r = self.results[name]
            got = sorted(sorted(cs["members"]) for cs in r["conflict_sets"])
            self.assertEqual(got, brute, name)
            self.assertTrue(r["complete"], name)

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
                            # dof_after is stated only when no conflict is left; None must mean one is
                            or (chk.sk.DoF != fx["dof_after"] if fx["dof_after"] is not None else not left)
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

    # -- fixes that survive, and removals that apply --------------------------
    @staticmethod
    def _settled(sk):
        """What a user sees of a sketch: constraints, expressions, the solver's lists and the DOF."""
        sk.solve()
        return (
            [(c.Type, c.Name, round(c.Value, 9), c.Driving, c.IsActive, c.First, c.Second) for c in sk.Constraints],
            [(str(p).lstrip("."), str(e)) for p, e in sk.ExpressionEngine],
            list(sk.ConflictingConstraints),
            list(sk.RedundantConstraints),
            list(sk.PartiallyRedundantConstraints),
            sk.DoF,
        )

    def _survives_recompute(self, sketch, fx):
        chk = _Check(sketch)
        try:
            SD.apply_fix(chk.sk, fx)
            before = self._settled(chk.sk)
            chk.sk.touch()
            chk.sk.Document.recompute()
            return before == self._settled(chk.sk)
        finally:
            chk.close()

    def testEveryOfferedFixSurvivesARecompute(self):
        """The sketch and its conflict state are the same after a recompute as right after the fix (conflict fixes
        and redundancy removals alike); a fix a recompute would undo is not offered."""
        total = unstable = 0
        for case in self.cases:
            r = self.results[case.name]
            fixes = [f for cs in r["conflict_sets"] for f in cs["fixes"]]
            fixes += [g["fix"] for g in r["redundancy_groups"] if g["fix"]]
            for fx in fixes:
                total += 1
                if not self._survives_recompute(case.sketch, fx):
                    unstable += 1
                    print("UNSTABLE", case.name, fx["description"])
        self.assertGreater(total, 100)
        self.assertEqual(unstable, 0)
        self.__class__.stats["fixes_checked_after_recompute"] = total

    def testExpressionReaderMadeNonDrivingIsNotOffered(self):
        r = self.results["expression_driven_conflict"]
        offered = [(o["op"], o["index"]) for cs in r["conflict_sets"] for f in cs["fixes"] for o in f["ops"]]
        # w (index 0) is read by v's expression: as a reference it takes the measured value and a recompute moves v
        self.assertNotIn(("set_driving", 0), offered)
        self.assertTrue(offered)
        self.assertIn("recompute_changes_result", [n["reason"] for n in r["not_offered"]])

    def testIndexBoundExpressionsDoNotCertifyAShiftedFix(self):
        doc = FreeCAD.newDocument("TestDiagnosisIdx")
        try:
            s = doc.addObject("Sketcher::SketchObject", "S")
            s.addGeometry(Part.LineSegment(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(5, 0, 0)))
            s.addGeometry(Part.LineSegment(FreeCAD.Vector(0, 3, 0), FreeCAD.Vector(5, 3, 0)))
            s.addConstraint(Sketcher.Constraint("Horizontal", 0))
            s.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 5.0))
            s.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 6.0))
            s.addConstraint(Sketcher.Constraint("DistanceX", 1, 1, 1, 2, 5.0))
            s.setExpression("Constraints[3]", ".Constraints[1] * 2")  # reads an unnamed constraint by position
            doc.recompute()
            r = SD.diagnose(s)
            fixes = [f for cs in r["conflict_sets"] for f in cs["fixes"]]
            self.assertTrue(fixes)
            for f in fixes:
                self.assertTrue(self._survives_recompute(s, f), f["description"])
            self.assertTrue(r["not_offered"])  # deleting #2 shifts the position the expression reads
        finally:
            FreeCAD.closeDocument(doc.Name)

    def _two_lines(self, doc, name, redundancy, conflict=True):
        s = doc.addObject("Sketcher::SketchObject", name)
        v = FreeCAD.Vector
        s.addGeometry(Part.LineSegment(v(0, 0, 0), v(10, 0, 0)))
        s.addConstraint(Sketcher.Constraint("Coincident", 0, 1, -1, 1))
        s.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 10.0))
        if conflict:
            s.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 12.0))
        s.addConstraint(Sketcher.Constraint("Horizontal", 0))
        if redundancy:
            s.addGeometry(Part.LineSegment(v(0, 5, 0), v(10, 5, 0)))
            s.addConstraint(Sketcher.Constraint("DistanceX", 1, 1, 1, 2, 7.0))
            s.addConstraint(Sketcher.Constraint("DistanceX", 1, 1, 1, 2, 7.0))
        doc.recompute()
        return s

    def testConflictWithAnUnrelatedRedundancyStillOffersFixes(self):
        doc = FreeCAD.newDocument("TestDiagnosisMixed")
        try:
            s = self._two_lines(doc, "S", redundancy=True)
            r = SD.diagnose(s)
            self.assertEqual(len(r["conflict_sets"]), 1)
            self.assertTrue(r["redundancy_groups"])
            self.assertTrue(r["conflict_sets"][0]["fixes"], "a redundancy that was already there vetoed every fix")
            plain = SD.diagnose(self._two_lines(doc, "P", redundancy=False))
            self.assertEqual(
                len(plain["conflict_sets"][0]["fixes"]), len(r["conflict_sets"][0]["fixes"])
            )
            for fx in r["conflict_sets"][0]["fixes"]:
                chk = _Check(s)
                try:
                    SD.apply_fix(chk.sk, fx)
                    chk.sk.solve()
                    self.assertEqual(list(chk.sk.ConflictingConstraints), [])
                finally:
                    chk.close()
        finally:
            FreeCAD.closeDocument(doc.Name)

    def testRedundancyRemovalsApplyAndRepairARedundantSketch(self):
        offered = 0
        for case in self.cases:
            for g in self.results[case.name]["redundancy_groups"]:
                fx = g["fix"]
                if fx is None:
                    continue
                offered += 1
                self.assertEqual(fx["ops"], [{"op": "delete", "index": g["suggested_removal"]}])
                self.assertTrue(fx["verified"] and fx["applied_to_fresh_copy"])
                json.dumps(fx)
        self.assertGreaterEqual(offered, 8)
        # a sketch with redundancy only is repaired by applying the offered removals one at a time
        doc = FreeCAD.newDocument("TestDiagnosisRepair")
        try:
            s = self._two_lines(doc, "S", redundancy=True, conflict=False)
            dof = None
            for _ in range(4):
                r = SD.diagnose(s)
                dof = r["dof"] if dof is None else dof
                if not r["redundancy_groups"]:
                    break
                fx = r["redundancy_groups"][0]["fix"]
                self.assertIsNotNone(fx, "a redundancy group without an applicable removal")
                SD.apply_fix(s, fx)
            r = SD.diagnose(s)
            self.assertEqual(r["redundancy_groups"], [])
            self.assertEqual(r["conflict_sets"], [])
            self.assertEqual(r["dof"], dof)
        finally:
            FreeCAD.closeDocument(doc.Name)

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

    def testCandidatesAreActiveDrivingConstraints(self):
        for case in self.cases:
            want = [
                i
                for i, c in enumerate(case.sketch.Constraints)
                if c.IsActive and c.Driving and c.Type != "InternalAlignment"
            ]
            self.assertEqual(self.results[case.name]["candidate_constraints"], want, case.name)

    def testSuggestedRemovalIsTheNewestMember(self):
        for case in self.cases:
            for g in self.results[case.name]["redundancy_groups"]:
                self.assertEqual(g["suggested_removal"], max(g["members"]), case.name)

    def testNonConflictingReferenceCasesHaveNoSets(self):
        for case in self.by_class("reference"):
            if not case.truth["conflicting"]:
                r = self.results[case.name]
                self.assertEqual(r["conflict_sets"], [], case.name)
                self.assertEqual(r["status"], "solved", case.name)

    def testSolverLimitsAreReportedHonestly(self):
        for case in self.by_class("limit"):
            r = self.results[case.name]
            if case.name == "parallel_perpendicular":
                # Both constraints hold only for a collapsed line.  The construction solves
                # collapse one line to a few 1e-17 mm; a later solve used to reject the
                # coinciding ends and now keeps the line (G10 slice 3a, check 2), so the case
                # may report a solution instead of a solver failure.
                self.assertIn(r["status"], ("solver_failed", "solved"), case.name)
            else:
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
            self.assertNoScratchDocument()

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
            self.assertNoScratchDocument()
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
        self.assertNoScratchDocument()

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


class TestSketchDiagnosisProgress(unittest.TestCase):
    """The progress callback of ``diagnose`` (G10 slice 4a, check 7): it is called, it can cancel and it changes nothing
    else."""

    @classmethod
    def setUpClass(cls):
        cls.doc = FreeCAD.newDocument("DiagnosisProgress")
        sk = cls.doc.addObject("Sketcher::SketchObject", "S")
        sk.addGeometry(Part.LineSegment(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(10, 0, 0)))
        sk.addConstraint(Sketcher.Constraint("Horizontal", 0))
        sk.addConstraint(Sketcher.Constraint("Distance", 0, 10.0))
        sk.addConstraint(Sketcher.Constraint("Distance", 0, 12.0))
        cls.doc.recompute()
        cls.sk = sk

    @classmethod
    def tearDownClass(cls):
        FreeCAD.closeDocument(cls.doc.Name)

    def testResultIsTheSameWithAndWithoutACallback(self):
        calls = []
        a = SD.diagnose(self.sk)
        b = SD.diagnose(self.sk, progress=lambda info: calls.append(info["phase"]) or False)
        for r in (a, b):
            for k in ("seconds", "solves", "copies"):
                r.pop(k)
        self.assertEqual(a, b)
        self.assertGreater(len(calls), 5)
        self.assertIn("solve", calls)

    def testCancelRaisesAndLeavesNothingBehind(self):
        before = _signature(self.sk)
        n = []

        def stop(info):
            n.append(1)
            return len(n) == 4

        with self.assertRaises(SD.DiagnosisCancelled):
            SD.diagnose(self.sk, progress=stop)
        self.assertEqual(len(n), 4)
        self.assertEqual(_signature(self.sk), before)
        self.assertFalse([d for d in FreeCAD.listDocuments() if "Scratch" in d])

    def testACallbackThatRaisesPropagatesAndLeavesNothingBehind(self):
        before = _signature(self.sk)

        def boom(info):
            raise ValueError("planted")

        with self.assertRaises(ValueError):
            SD.diagnose(self.sk, progress=boom)
        self.assertEqual(_signature(self.sk), before)
        self.assertFalse([d for d in FreeCAD.listDocuments() if "Scratch" in d])
        SD.diagnose(self.sk)  # the hook is gone: a later call without a callback works


# ---------------------------------------------------------------------------
# Repairs after the independent verification of slice 4a (run 057)
# ---------------------------------------------------------------------------

# Random small sketches the verifier fuzzed: (lines, [(type, a, b, pos a, pos b, value)]).  Seeds 5 and 155 are
# sketches where a fix certified on the copy changes after a recompute; 79 and 98 are conflicted sketches with
# a redundancy removal.
_FUZZ = {
    5: (3, [("Ang", 2, 2, 1, 2, 10.0), ("Perp", 2, 0, 1, 2, 5.0), ("DX", 0, 1, 1, 1, 10.0), ("H", 2, 0, 2, 1, 7.0), ("DY", 0, 0, 2, 1, 5.0), ("Par", 0, 0, 1, 1, 5.0)]),
    155: (3, [("DY", 2, 1, 2, 1, 10.0), ("Coin", 0, 2, 1, 1, 5.0), ("Par", 0, 2, 1, 1, 5.0), ("Dist", 2, 0, 1, 2, 7.0), ("Perp", 0, 2, 1, 1, 10.0), ("V", 1, 1, 1, 2, 10.0), ("DY", 2, 1, 1, 1, 7.0)]),
    79: (2, [("DX", 0, 0, 2, 1, 5.0), ("V", 1, 0, 1, 2, 5.0), ("Ang", 1, 0, 2, 2, 7.0), ("Dist", 0, 1, 2, 2, 5.0), ("H", 1, 1, 1, 1, 10.0), ("DX", 0, 0, 1, 2, 5.0), ("Eq", 1, 0, 2, 2, 10.0)]),
    98: (3, [("H", 1, 1, 2, 2, 5.0), ("Ang", 2, 2, 2, 1, 5.0), ("DY", 0, 2, 2, 1, 10.0), ("H", 1, 1, 1, 2, 7.0), ("Perp", 2, 0, 1, 1, 7.0), ("Eq", 2, 2, 1, 1, 5.0), ("Ang", 1, 2, 1, 2, 5.0), ("Par", 1, 1, 2, 1, 5.0)]),
}


def _fuzz_build(doc, nl, cs, name="S"):
    V = FreeCAD.Vector
    C = Sketcher.Constraint
    s = doc.addObject("Sketcher::SketchObject", name)
    for i in range(nl):
        s.addGeometry(Part.LineSegment(V(0, 3 * i, 0), V(8 + i, 3 * i + (1 if i % 2 else 0), 0)))
    for t, a, b, pa, pb, v in cs:
        try:
            if t == "H":
                s.addConstraint(C("Horizontal", a))
            elif t == "V":
                s.addConstraint(C("Vertical", a))
            elif t == "Par" and a != b:
                s.addConstraint(C("Parallel", a, b))
            elif t == "Perp" and a != b:
                s.addConstraint(C("Perpendicular", a, b))
            elif t == "Eq" and a != b:
                s.addConstraint(C("Equal", a, b))
            elif t == "DX":
                s.addConstraint(C("DistanceX", a, 1, a, 2, v))
            elif t == "DY":
                s.addConstraint(C("DistanceY", a, 1, a, 2, v))
            elif t == "Dist":
                s.addConstraint(C("Distance", a, v))
            elif t == "Coin" and a != b:
                s.addConstraint(C("Coincident", a, pa, b, pb))
            elif t == "Ang":
                s.addConstraint(C("Angle", a, 0.5))
        except Exception:
            pass
    doc.recompute()
    return s


def _fuzz_spec(seed):
    import random

    rng = random.Random(seed)
    nl = rng.choice([2, 3])
    cs = []
    for _ in range(rng.randint(4, 8)):
        t = rng.choice(["H", "V", "Par", "Perp", "Eq", "DX", "DY", "DX", "DY", "Dist", "Coin", "Ang"])
        a = rng.randrange(nl)
        b = rng.randrange(nl)
        v = rng.choice([5.0, 7.0, 10.0])
        pa = rng.choice([1, 2])
        pb = rng.choice([1, 2])
        cs.append((t, a, b, pa, pb, v))
    return nl, cs


def _real_state(s):
    code = s.solve()
    return {
        "code": code,
        "conf": sorted(i - 1 for i in s.ConflictingConstraints),
        "red": sorted(i - 1 for i in list(s.RedundantConstraints) + list(s.PartiallyRedundantConstraints)),
        "dof": s.DoF,
    }


def _all_fixes(r):
    out = [("c%d.%d" % (k, j), f) for k, cs in enumerate(r["conflict_sets"]) for j, f in enumerate(cs["fixes"])]
    out += [("r%d" % k, g["fix"]) for k, g in enumerate(r["redundancy_groups"]) if g.get("fix")]
    return out


def _shifted(i, ops):
    """The index a constraint has after the fix's deletions."""
    return i - sum(1 for o in ops if o["op"] == "delete" and o["index"] < i)


class TestSketchDiagnosisRepairs(unittest.TestCase):
    def setUp(self):
        self.previous = _active_name()
        self.docs = []

    def tearDown(self):
        for n in self.docs:
            if n in FreeCAD.listDocuments():
                FreeCAD.closeDocument(n)
        if self.previous is not None and self.previous in FreeCAD.listDocuments():
            FreeCAD.setActiveDocument(self.previous)

    def newdoc(self, name):
        d = FreeCAD.newDocument(name)
        self.docs.append(d.Name)
        return d

    def three_equal_distances(self, doc, name="S", expr=False, n=2):
        return Corpus.repair_equal_distances(doc, name, n, expr)

    # M1 -----------------------------------------------------------------
    def testOverlappingRedundancyGroupsReachACleanSketchByApplyingRepeatedly(self):
        doc = self.newdoc("RepairTriple")
        s = self.three_equal_distances(doc)
        r = SD.diagnose(s)
        self.assertEqual(sorted(len(g["members"]) for g in r["redundancy_groups"]), [2, 2])
        self.assertTrue(all(g["fix"] for g in r["redundancy_groups"]), [g.get("no_fix_reason") for g in r["redundancy_groups"]])
        dof = r["dof"]
        doc.clearUndos()
        steps = 0
        while True:
            r = SD.diagnose(s)
            groups = [g for g in r["redundancy_groups"]]
            if not groups:
                break
            self.assertLess(steps, 4, "applying removals does not converge")
            self.assertTrue(groups[0]["fix"], groups[0].get("no_fix_reason"))
            before = len(s.Constraints)
            doc.openTransaction("Apply sketch fix")
            SD.apply_fix(s, groups[0]["fix"])
            doc.commitTransaction()
            doc.recompute()
            self.assertEqual(len(s.Constraints), before - 1)
            steps += 1
        self.assertEqual(steps, 2)
        self.assertEqual(doc.UndoCount, 2)  # one undo entry per Apply
        self.assertEqual(r["conflict_sets"], [])
        self.assertEqual(r["dof"], dof)
        self.assertEqual(_real_state(s)["red"], [])

    def testARemovalThatCannotBeOfferedStatesWhy(self):
        doc = self.newdoc("RepairNamedExpr")
        s = self.three_equal_distances(doc, expr=True, n=1)  # c reads b0, the member the search would remove
        r = SD.diagnose(s)
        no = [g for g in r["redundancy_groups"] if not g["fix"]]
        self.assertTrue(no, "the removal of a constraint another expression reads was offered")
        for g in no:
            self.assertTrue(g["no_fix_reason"])
            self.assertIsInstance(g["no_fix_text"], str)
            self.assertGreater(len(g["no_fix_text"]), 10)
        json.dumps(r)

    # M2 -----------------------------------------------------------------
    def testNoFixIsCertifiedThatARecomputeChangesOnASketchWithoutExpressions(self):
        offered = dropped = 0
        for seed in (5, 155):  # two sketches with no expressions at all
            doc = self.newdoc("RepairRecompute%d" % seed)
            nl, cs = _FUZZ[seed]
            s = _fuzz_build(doc, nl, cs)
            self.assertFalse(s.ExpressionEngine)
            r = SD.diagnose(s)
            dropped += len([n for n in r["not_offered"] if n["reason"] in ("recompute_changes_result", "result_not_reproduced")])
            for name, fx in _all_fixes(r):
                offered += 1
                d2 = self.newdoc("RepairRecomputeFix")
                s2 = _fuzz_build(d2, nl, cs)
                SD.apply_fix(s2, fx)
                before = _real_state(s2)
                s2.touch()
                d2.recompute()
                self.assertEqual(before, _real_state(s2), (seed, name, fx["description"]))
        self.assertGreaterEqual(dropped, 2, "the fixes the verifier found are not reported as dropped")

    def testFuzzedFixesAreStableUnderARecompute(self):
        unstable = total = 0
        for seed in range(60):
            nl, cs = _fuzz_spec(seed)
            doc = self.newdoc("RepairFuzz%d" % seed)
            s = _fuzz_build(doc, nl, cs)
            st = _real_state(s)
            if not (st["conf"] or st["red"]):
                continue
            r = SD.diagnose(s)
            for name, fx in _all_fixes(r):
                d2 = self.newdoc("RepairFuzzFix")
                s2 = _fuzz_build(d2, nl, cs)
                SD.apply_fix(s2, fx)
                a = _real_state(s2)
                d2.recompute()
                total += 1
                if a != _real_state(s2):
                    unstable += 1
        self.assertGreater(total, 20)
        self.assertEqual(unstable, 0)

    # L1 -----------------------------------------------------------------
    def testStatedConflictsAndDofEqualTheRealOnes(self):
        stated_none = checked = 0
        for seed in [79, 98] + list(range(60)):
            nl, cs = _FUZZ[seed] if seed in _FUZZ and seed > 59 else _fuzz_spec(seed)
            doc = self.newdoc("RepairTruth%d" % seed)
            s = _fuzz_build(doc, nl, cs)
            st = _real_state(s)
            if not (st["conf"] or st["red"]):
                continue
            r = SD.diagnose(s)
            for name, fx in _all_fixes(r):
                d2 = self.newdoc("RepairTruthFix")
                s2 = _fuzz_build(d2, nl, cs)
                SD.apply_fix(s2, fx)
                d2.recompute()
                real = _real_state(s2)
                with self.subTest(seed=seed, fix=name, text=fx["description"]):
                    checked += 1
                    self.assertEqual(real["conf"], sorted(_shifted(i, fx["ops"]) for i in fx["remaining_conflicts"]))
                    if fx["remaining_conflicts"]:
                        stated_none += 1
                        self.assertIsNone(fx["dof_after"])
                    else:
                        self.assertEqual(real["dof"], fx["dof_after"])
        self.assertGreater(checked, 20)
        self.assertGreater(stated_none, 0, "no conflicted sketch with a removal in the sample")

    def testRemovalOnAConflictedSketchStatesTheConflictThatRemains(self):
        for seed in (79, 98):
            nl, cs = _FUZZ[seed]
            doc = self.newdoc("RepairConflicted%d" % seed)
            s = _fuzz_build(doc, nl, cs)
            r = SD.diagnose(s)
            self.assertTrue(r["conflict_sets"])
            removals = [g["fix"] for g in r["redundancy_groups"] if g.get("fix")]
            self.assertTrue(removals, seed)
            for fx in removals:
                self.assertTrue(fx["remaining_conflicts"], seed)
                self.assertIsNone(fx["dof_after"], seed)

    # L2 -----------------------------------------------------------------
    def other_sketch_reads(self, doc, expr, name_of_reader="Sk2"):
        return Corpus.repair_reader_pair(doc, expr, name_of_reader)

    def testAFixThatBreaksAnotherObjectsExpressionIsNotOffered(self):
        # (what the other sketch reads, ops that would break it)
        cases = (
            ("Sk.Constraints.w2 + 1 mm", lambda o: o["index"] == 3),  # by name: any change of w2
            ("Sk.Constraints[4] + 1 mm", lambda o: o["index"] == 4 or (o["op"] == "delete" and o["index"] < 4)),  # by position
        )
        for expr, breaks in cases:
            doc = self.newdoc("RepairOther")
            s, s2 = self.other_sketch_reads(doc, expr)
            self.assertTrue(s2.isValid())
            r = SD.diagnose(s)
            fixes = [f for cs in r["conflict_sets"] for f in cs["fixes"]]
            self.assertTrue(fixes, "nothing is offered at all: " + str(r["not_offered"]))
            for f in fixes:
                self.assertFalse([o for o in f["ops"] if breaks(o)], (expr, f["description"]))
                # and applied for real the reader stays valid
                d2 = self.newdoc("RepairOtherApply")
                s_b, s2_b = self.other_sketch_reads(d2, expr)
                SD.apply_fix(s_b, f)
                d2.recompute()
                self.assertTrue(s2_b.isValid(), (expr, f["description"]))
                self.assertEqual(len(s2_b.ExpressionEngine), 1, (expr, f["description"]))
            listed = [n for n in r["not_offered"] if n["reason"] == "read_by_another_object"]
            self.assertTrue(listed, (expr, r["not_offered"]))
            self.assertTrue(all(n["reason_text"] for n in listed))

    def testADatumBoundOutsideTheSketchIsNotTreatedAsFree(self):
        doc = self.newdoc("RepairSheet")
        V = FreeCAD.Vector
        C = Sketcher.Constraint
        s = doc.addObject("Sketcher::SketchObject", "Sk")
        s.addGeometry(Part.LineSegment(V(0, 0, 0), V(10, 0, 0)))
        s.addConstraint(C("Horizontal", 0))
        s.renameConstraint(s.addConstraint(C("DistanceX", 0, 1, 0, 2, 10.0)), "w")
        s.renameConstraint(s.addConstraint(C("DistanceX", 0, 1, 0, 2, 12.0)), "w2")
        sh = doc.addObject("Spreadsheet::Sheet", "Sheet")
        sh.set("B1", "10")
        doc.recompute()
        s.setExpression("Constraints.w", "Sheet.B1")
        doc.recompute()
        r = SD.diagnose(s)
        for cs in r["conflict_sets"]:
            for f in cs["fixes"] + cs["alternatives"]:
                for o in f["ops"]:
                    self.assertNotEqual((o["op"], o["index"]), ("set_datum", 1))
        reasons = [(n["ops"][0]["op"], n["ops"][0]["index"], n["reason"]) for n in r["not_offered"]]
        self.assertIn(("set_datum", 1, "bound_outside_sketch"), reasons)
