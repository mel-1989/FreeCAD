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

"""Tests for the solver-core work of G10 slice 3a: deterministic unknown
ordering, degenerate lines, partitioned diagnosis."""

import contextlib
import os
import time
import unittest

import FreeCAD
import Part
import Sketcher

from SketcherTests import SketchDiagnosisCorpus as Corpus
from SketcherTests import SketchSolverBaseline as Baseline

V = FreeCAD.Vector

PARTITION_ENV = "FREECAD_GCS_PARTITION_THRESHOLD"


SPARSE_ENV = "FREECAD_GCS_SPARSE_THRESHOLD"


@contextlib.contextmanager
def sparse_threshold(value):
    """Set the test-only sparse dogleg threshold of every sketch created
    inside the block."""
    old = os.environ.get(SPARSE_ENV)
    os.environ[SPARSE_ENV] = str(value)
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(SPARSE_ENV, None)
        else:
            os.environ[SPARSE_ENV] = old


@contextlib.contextmanager
def partition_threshold(value):
    """Set the test-only partition threshold of every sketch created inside
    the block (a sketch reads it when it is created)."""
    old = os.environ.get(PARTITION_ENV)
    os.environ[PARTITION_ENV] = str(value)
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(PARTITION_ENV, None)
        else:
            os.environ[PARTITION_ENV] = old


def _fourbar(doc, name):
    """A four-bar linkage with one degree of freedom: two valid assemblies
    of the crank and the coupler exist, 1.2 mm apart at the crank end."""
    sk = doc.addObject("Sketcher::SketchObject", name)
    segs = [((0, 0), (60, 0)), ((0, 0), (10, 17)), ((10, 17), (55, 35)), ((55, 35), (60, 0))]
    ids = [sk.addGeometry(Part.LineSegment(V(a[0], a[1], 0), V(b[0], b[1], 0)), False) for a, b in segs]
    g, cr, cp, rk = ids
    sk.addConstraint(Sketcher.Constraint("Coincident", g, 1, cr, 1))
    sk.addConstraint(Sketcher.Constraint("Coincident", cr, 2, cp, 1))
    sk.addConstraint(Sketcher.Constraint("Coincident", cp, 2, rk, 1))
    sk.addConstraint(Sketcher.Constraint("Coincident", rk, 2, g, 2))
    sk.addConstraint(Sketcher.Constraint("Coincident", g, 1, -1, 1))
    sk.addConstraint(Sketcher.Constraint("Horizontal", g))
    sk.addConstraint(Sketcher.Constraint("Distance", g, 60.0))
    sk.addConstraint(Sketcher.Constraint("Distance", cr, 20.0))
    sk.addConstraint(Sketcher.Constraint("Distance", cp, 50.0))
    sk.addConstraint(Sketcher.Constraint("Distance", rk, 45.0))
    return sk, cr


class _Base(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument("TestSketchSolverCore")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)


class TestSketchSolverCore(_Base):
    def test_fourbar_solves_to_the_same_branch_every_time(self):
        """The unknowns of a subsystem keep the order of the system's
        parameter list, not the order of their heap addresses, so the branch
        of a one-DOF mechanism does not depend on the process or the
        document (run 053: two assemblies, 1.2 mm apart, by process)."""
        ends = set()
        for i in range(40):
            sk, cr = _fourbar(self.doc, "FourBar%d" % i)
            self.assertEqual(sk.solve(), 0)
            self.assertEqual(sk.DoF, 1)
            p = sk.getPoint(cr, 2)
            ends.add((round(p.x, 6), round(p.y, 6)))
        self.assertEqual(len(ends), 1, ends)


class TestDegenerateLine(_Base):
    """A line drawn degenerate may have both ends land on the same double
    after a solve; it keeps a valid segment instead of invalidating the
    solution.  A line that was not degenerate and collapses stays rejected."""

    def _collapse(self, length):
        sk = self.doc.addObject("Sketcher::SketchObject", "Collapse")
        g = sk.addGeometry(Part.LineSegment(V(4, 14, 0), V(4 + length, 14, 0)), False)
        sk.addConstraint(Sketcher.Constraint("Coincident", g, 1, g, 2))
        return sk, g

    def test_degenerate_line_is_kept(self):
        for length in (3.6e-15, 1e-9, 9.9e-8):
            sk, g = self._collapse(length)
            self.assertEqual(sk.solve(), 0, length)
            line = sk.Geometry[g]
            self.assertGreater(line.length(), 0.0)
            self.assertLess(line.length(), 1e-7)
            self.assertEqual(list(line.StartPoint)[:2], [4.0, 14.0])
            self.doc.removeObject(sk.Name)

    def test_nondegenerate_line_that_collapses_is_rejected(self):
        for length in (2e-7, 1.0):
            sk, g = self._collapse(length)
            self.assertEqual(sk.solve(), -1, length)
            self.assertAlmostEqual(sk.Geometry[g].length(), length, delta=1e-12)
            self.doc.removeObject(sk.Name)


class TestPartitionedDiagnosis(_Base):
    """The diagnosis one connected component at a time gives the same
    results as the diagnosis of the whole system (checks 6 and 7)."""

    def _record_all(self, builder):
        out = {}
        for label, sk in builder():
            out[label] = Baseline.record(sk)
            out[label]["seconds"] = 0.0
        return out

    def test_corpus_matches_whole_system(self):
        """Every synthetic case of slice 1 with the threshold at 1 (all
        partitioned) against threshold 0 (never): the same code, DOF and four
        lists, coordinates within 1e-9 mm."""

        def cases():
            doc = FreeCAD.newDocument("PartCorpus", "PartCorpus", True, True)
            for c in Corpus.build_synthetic(doc):
                yield c.name, c.sketch

        with partition_threshold(0):
            whole = self._record_all(cases)
        with partition_threshold(1):
            parts = self._record_all(cases)
        self.assertEqual(Baseline.compare(whole, parts), [])
        self.assertGreater(len(whole), 60)

    def _planted(self, n, plants, threshold):
        with partition_threshold(threshold):
            doc = FreeCAD.newDocument("Planted", "Planted", True, True)
            sk, info = Corpus.build_scale(doc, "planted", n, chained=False, plants=plants)
            code = sk.solve()
            rec = (code, sk.DoF, sorted(sk.ConflictingConstraints), sorted(sk.RedundantConstraints), sorted(sk.PartiallyRedundantConstraints))
            FreeCAD.closeDocument(doc.Name)
        return rec, info

    def test_planted_conflicts_in_one_two_and_all_components(self):
        """Conflicts planted in one, two and every rectangle of a 1,000
        constraint sketch are reported exactly as the whole-system path does,
        and as the analytic truth (the conflict set of each plant)."""
        for plants_at in ([5], [5, 40], list(range(0, 83, 1))):
            plants = [(m, "conflict") for m in plants_at]
            whole, info = self._planted(1000, plants, 0)
            parts, _ = self._planted(1000, plants, 1)
            self.assertEqual(whole, parts, plants_at)
            self.assertLess(parts[0], 0)
            truth = sorted(i + 1 for p in info["plants"] for i in p["set"])
            self.assertEqual(parts[2], truth, plants_at)

    def test_planted_redundancy(self):
        for plants_at in ([7], [7, 30]):
            plants = [(m, "redundant") for m in plants_at]
            whole, info = self._planted(1000, plants, 0)
            parts, _ = self._planted(1000, plants, 1)
            self.assertEqual(whole, parts, plants_at)
            members = {i + 1 for p in info["plants"] for i in p["set"]}
            self.assertEqual(len(parts[3]), len(plants_at))
            self.assertTrue(set(parts[3]) <= members)

    def test_coupled_strip_above_default_threshold(self):
        """A 2,200 constraint strip (4,400 unknowns, over the default
        threshold of 2,000) is diagnosed by components by default and gives
        DOF 0 with clean lists, as the whole-system path does."""
        with partition_threshold(2000):
            doc = FreeCAD.newDocument("Strip", "Strip", True, True)
            sk, _ = Corpus.build_coupled(doc, "strip", 2200)
            self.assertEqual(sk.solve(), 0)
            self.assertEqual(sk.DoF, 0)
            self.assertEqual(
                (sk.ConflictingConstraints, sk.RedundantConstraints, sk.PartiallyRedundantConstraints, sk.MalformedConstraints),
                ([], [], [], []),
            )
            FreeCAD.closeDocument(doc.Name)


class TestSubsystemStatus(_Base):
    """System::solve reports a failure of any component, not only of the last
    one (run 053: a failing component followed by a solvable one passed as
    solved and its points were moved off their fixed coordinates)."""

    def _build(self, name, infeasible_first):
        sk = self.doc.addObject("Sketcher::SketchObject", name)

        def infeasible():
            # a point 1 mm from two points that are 10 mm apart: no solution
            a = sk.addGeometry(Part.Point(V(0, 0, 0)), False)
            b = sk.addGeometry(Part.Point(V(10, 0, 0)), False)
            p = sk.addGeometry(Part.Point(V(5, 0.5, 0)), False)
            for g, x in ((a, 0.0), (b, 10.0)):
                sk.addConstraint(Sketcher.Constraint("DistanceX", -1, 1, g, 1, x))
                sk.addConstraint(Sketcher.Constraint("DistanceY", -1, 1, g, 1, 0.0))
            sk.addConstraint(Sketcher.Constraint("Distance", a, 1, p, 1, 1.0))
            sk.addConstraint(Sketcher.Constraint("Distance", b, 1, p, 1, 1.0))

        def solvable():
            c = sk.addGeometry(Part.Point(V(20, 20.1, 0)), False)
            sk.addConstraint(Sketcher.Constraint("DistanceX", -1, 1, c, 1, 20.0))
            sk.addConstraint(Sketcher.Constraint("DistanceY", -1, 1, c, 1, 20.0))

        for part in ((infeasible, solvable) if infeasible_first else (solvable, infeasible)):
            part()
        return sk

    def test_failure_of_an_earlier_component_is_reported(self):
        for first in (True, False):
            sk = self._build("Sub%d" % first, first)
            self.assertEqual(sk.solve(), -1, first)
            self.assertEqual(sk.ConflictingConstraints, [])
            # the failed solution is not applied: the fixed points stay where they are
            fixed = [g for g in sk.Geometry if abs(g.Y) < 1e-9 and abs(g.X) in (0.0, 10.0)]
            self.assertEqual(len(fixed), 2, first)


class TestSolverScale(_Base):
    """Speed and equivalence of the large-sketch paths (checks 4, 5, 6)."""

    def test_decoupled_5000_solves_fast(self):
        """5,000 constraints in about 400 independent components: the
        diagnosis works per component (stock: 9.8 s)."""
        sk, _ = Corpus.build_scale(self.doc, "dec5000", 5000, chained=False)
        t0 = time.perf_counter()
        self.assertEqual(sk.solve(), 0)
        elapsed = time.perf_counter() - t0
        self.assertEqual(sk.DoF, 0)
        self.assertLess(elapsed, 2.0)

    def _strip_lists(self, n, plants, partition, sparse, omit=0):
        with partition_threshold(partition), sparse_threshold(sparse):
            doc = FreeCAD.newDocument("Strip", "Strip", True, True)
            sk, _ = Corpus.build_coupled(doc, "strip", n, plants=plants, omit=omit)
            rec = Baseline.record(sk)
            rec["seconds"] = 0.0
            FreeCAD.closeDocument(doc.Name)
        return rec

    def test_strip_diagnosis_with_conflict_and_redundancy(self):
        """A 1,000 constraint strip (one component of 1,000 unknowns, sparse
        QR) with a conflict and a redundancy planted gives the lists of the
        whole-system path."""
        plants = [(450, "conflict"), (150, "redundant")]
        whole = self._strip_lists(1000, plants, 0, 0)
        parts = self._strip_lists(1000, plants, 1, 0)
        self.assertEqual(whole["conflicting"], parts["conflicting"])
        self.assertEqual(whole["redundant"], parts["redundant"])
        self.assertEqual(whole["partial"], parts["partial"])
        self.assertEqual(whole["dof"], parts["dof"])
        self.assertTrue(parts["conflicting"])
        self.assertEqual(whole["dependent"], parts["dependent"])

    def test_strip_with_one_plant_or_missing_constraints(self):
        """One conflict, one redundancy, or three constraints missing (so
        points are free and the dependent parameters are found by the sparse
        QR): the partitioned lists, DOF and dependent geometry are the
        whole-system ones."""
        for plants, omit in (([(450, "conflict")], 0), ([(450, "redundant")], 0), ([], 3)):
            whole = self._strip_lists(1000, plants, 0, 0, omit)
            parts = self._strip_lists(1000, plants, 1, 0, omit)
            for key in ("code", "dof", "conflicting", "redundant", "partial", "malformed", "dependent"):
                self.assertEqual(whole[key], parts[key], (plants, omit, key))
            if plants:
                self.assertTrue(parts["conflicting"] or parts["redundant"])
            else:
                self.assertEqual(parts["dof"], 3)
                self.assertTrue(parts["dependent"])

    def test_coupled_strip_solves_from_a_perturbed_start_quickly(self):
        """2,400 constraints, one connected system, drawn off its place: the
        sparse dogleg step solves it (the dense step takes tens of seconds)."""
        sk, _ = Corpus.build_coupled(self.doc, "perturbed", 2400)
        t0 = time.perf_counter()
        self.assertEqual(sk.solve(), 0)
        self.assertLess(time.perf_counter() - t0, 8.0)
        self.assertEqual(sk.DoF, 0)

    def test_sparse_dogleg_matches_dense(self):
        """The sparse dogleg step (threshold lowered to 1) reaches the dense
        solution on a strip drawn off its place, and on every fully
        constrained synthetic case of slice 1."""
        dense = self._strip_lists(600, (), 0, 0)
        sparse = self._strip_lists(600, (), 0, 1)
        self.assertEqual(Baseline.compare({"strip": dense}, {"strip": sparse}), [])

        def full_cases():
            doc = FreeCAD.newDocument("SparseFull", "SparseFull", True, True)
            for c in Corpus.build_synthetic(doc):
                if c.cls == "full":
                    yield c.name, c.sketch

        with sparse_threshold(0):
            dense_all = {k: Baseline.record(v) for k, v in full_cases()}
        with sparse_threshold(1):
            sparse_all = {k: Baseline.record(v) for k, v in full_cases()}
        for rec in list(dense_all.values()) + list(sparse_all.values()):
            rec["seconds"] = 0.0
        self.assertGreater(len(dense_all), 10)
        # The two steps stop at different points inside the solver's convergence band: on the
        # quarter arc (a poorly conditioned fully constrained case) they end 2.8e-6 mm apart.
        self.assertEqual(Baseline.compare(dense_all, sparse_all, tol=1e-5), [])


class TestSqpUnknownOrder(_Base):
    def testDrillingSketchFirstSolveDoesNotDependOnTheProcess(self):
        # The SQP step ordered its unknowns by address, which differs from process to process: on this
        # sketch (DOF 21, no flagged constraints, DogLeg, LM and BFGS all fail from the stored geometry)
        # the first solve gave -1 in 11 of 40 fresh processes and 0 in the rest.  A single process only
        # catches that about one time in four, so this is a regression guard, not a proof.
        path = os.path.join(FreeCAD.getHomePath(), "Mod", "CAM", "CAMTests", "Drilling_1.FCStd")
        if not os.path.exists(path):
            self.skipTest("Drilling_1.FCStd is not installed")
        doc = FreeCAD.openDocument(path)
        try:
            sketch = doc.getObject("Sketch")
            self.assertEqual(sketch.solve(), 0)
            self.assertEqual(sketch.DoF, 21)
            self.assertEqual(len(sketch.ConflictingConstraints), 0)
        finally:
            FreeCAD.closeDocument(doc.Name)
