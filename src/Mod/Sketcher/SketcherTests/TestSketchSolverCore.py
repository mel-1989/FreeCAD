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

"""Tests for the solver-core work of G10 slice 3a (deterministic unknown
ordering, degenerate lines, partitioned diagnosis) and 3b (the one-free-DOF
cliff, the minimum norm step, the start value of a reduced slot)."""

import contextlib
import json
import math
import os
import random
import subprocess
import sys
import tempfile
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

    def test_kept_degenerate_line_keeps_its_direction(self):
        """The segment that replaces a collapsed degenerate line points the
        way the line pointed before the solve, not the opposite way.  Every
        solve collapses the line again, so a reversed direction would flip at
        each solve: it is read after the solve that adding the constraint
        runs and after three more."""
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (3, -4)):
            sk = self.doc.addObject("Sketcher::SketchObject", "Dir")
            n = (dx * dx + dy * dy) ** 0.5
            g = sk.addGeometry(Part.LineSegment(V(4, 14, 0), V(4 + 3.6e-15 * dx / n, 14 + 3.6e-15 * dy / n, 0)), False)
            sk.addConstraint(Sketcher.Constraint("Coincident", g, 1, g, 2))
            for step in range(4):
                if step:
                    self.assertEqual(sk.solve(), 0, (dx, dy, step))
                line = sk.Geometry[g]
                ex, ey = line.EndPoint.x - line.StartPoint.x, line.EndPoint.y - line.StartPoint.y
                self.assertGreater(ex * dx + ey * dy, 0.0, (dx, dy, step, ex, ey))
                self.assertGreater(line.length(), 0.0)
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

    def test_driving_constraint_without_unknowns_is_not_dropped(self):
        """A driving constraint between two points of the fixed axes has no
        unknowns, so it is a row of zeros.  A sketch above the partition
        threshold (lowered to 1 here) still reports a conflict through that
        row, as the whole-system path does, and a consistent one stays clean."""

        def build(value):
            doc = FreeCAD.newDocument("NoUnknowns", "NoUnknowns", True, True)
            sk = doc.addObject("Sketcher::SketchObject", "S")
            g = sk.addGeometry(Part.LineSegment(V(1, 1, 0), V(10, 2, 0)), False)
            sk.addConstraint(Sketcher.Constraint("Coincident", g, 1, -1, 1))
            sk.addConstraint(Sketcher.Constraint("Horizontal", g))
            sk.addConstraint(Sketcher.Constraint("Distance", g, 10.0))
            if value is not None:
                sk.addConstraint(Sketcher.Constraint("DistanceX", -1, 1, -1, 2, value))
            rec = (sk.solve(), sk.DoF, sorted(sk.ConflictingConstraints), sorted(sk.RedundantConstraints))
            FreeCAD.closeDocument(doc.Name)
            return rec

        for value in (None, 5.0):
            with partition_threshold(0):
                whole = build(value)
            with partition_threshold(1):
                parts = build(value)
            self.assertEqual(whole, parts, value)
        self.assertEqual(parts[2], [4])
        self.assertLess(parts[0], 0)

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


def _noisy_rects(doc, name, count, drop, seed, plant=None):
    """A grid of `count` rectangles drawn off their place by up to 0.1 mm,
    each with its corners joined by Coincident constraints (so the two ends of
    a joint start apart), `drop` of the constraints left out at random from
    the third rectangle on.  `plant` is None, "conflict" (the width of the
    first rectangle stated as 11.5 as well as 10) or "redundant" (the width of
    the second stated twice)."""
    rng = random.Random(seed)

    def n():
        return rng.uniform(-0.1, 0.1)

    sk = doc.addObject("Sketcher::SketchObject", name)
    geo = []
    cons = []
    protected = 0
    for r in range(count):
        ox, oy = 30.0 * (r % 10), 20.0 * (r // 10)
        corners = [(ox, oy), (ox + 10.0, oy), (ox + 10.0, oy + 6.0), (ox, oy + 6.0)]
        base = len(geo)
        for i in range(4):
            a, b = corners[i], corners[(i + 1) % 4]
            geo.append(Part.LineSegment(V(a[0] + n(), a[1] + n(), 0), V(b[0] + n(), b[1] + n(), 0)))
        mine = [Sketcher.Constraint("Coincident", base + i, 2, base + (i + 1) % 4, 1) for i in range(4)]
        mine += [
            Sketcher.Constraint("Horizontal", base),
            Sketcher.Constraint("Horizontal", base + 2),
            Sketcher.Constraint("Vertical", base + 1),
            Sketcher.Constraint("Vertical", base + 3),
            Sketcher.Constraint("DistanceX", -1, 1, base, 1, ox),
            Sketcher.Constraint("DistanceY", -1, 1, base, 1, oy),
            Sketcher.Constraint("DistanceX", base, 1, base, 2, 10.0),
            Sketcher.Constraint("DistanceY", base + 1, 1, base + 1, 2, 6.0),
        ]
        if r >= 2:
            mine = [c for c in mine if rng.random() >= drop]
        else:
            protected += len(mine)
        cons += mine
    if plant == "conflict":
        cons.append(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 11.5))
    elif plant == "redundant":
        cons.append(Sketcher.Constraint("DistanceX", 4, 1, 4, 2, 10.0))
    sk.addGeometry(geo, False)
    sk.addConstraint(cons)
    return sk


def _child_cases():
    """The sketches the child processes of the determinism test solve; the
    result of each is every coordinate and every list."""
    doc = FreeCAD.newDocument("Child")
    out = {}
    fb, _ = _fourbar(doc, "fourbar")
    out["fourbar"] = Baseline.record(fb)
    for label, drop, plant in (
        ("drop_5", 0.05, None),
        ("drop_15", 0.15, None),
        ("drop_40", 0.4, None),
        ("conflict", 0.1, "conflict"),
        ("redundant", 0.1, "redundant"),
        ("both", 0.1, "conflict"),
    ):
        sk = _noisy_rects(doc, label, 40, drop, 7, plant)
        if label == "both":
            sk.addConstraint(Sketcher.Constraint("DistanceX", 4, 1, 4, 2, 10.0))
        out[label] = Baseline.record(sk)
    for rec in out.values():
        rec["seconds"] = 0.0
    return out


def _round_all(x, places=9):
    if isinstance(x, float):
        return round(x, places) + 0.0
    if isinstance(x, dict):
        return {k: _round_all(v, places) for k, v in x.items()}
    if isinstance(x, list):
        return [_round_all(v, places) for v in x]
    return x


class TestStartValues(_Base):
    """Check 3 of slice 3b: the start value of a reduced slot is the kept
    parameter's, not whichever entry of an address-ordered map came last."""

    def test_coincident_pairs_start_from_the_first_point(self):
        """Forty pairs of points joined by Coincident, the first of each
        pair drawn on one side of a fixed point and the second on the other,
        with the pair held at distance 5 from that point.  The pair is off its
        circle at the start, so the solver moves it from its start value, and
        it must move it from the kept (first) point's: the pair ends on the
        first point's side.  Before the fix the start value was the first or
        the second point's, depending on where the heap put the
        parameters."""
        count = 40
        sk = self.doc.addObject("Sketcher::SketchObject", "Pairs")
        geo = []
        for i in range(count):
            geo.append(Part.Point(V(10.0 * i + 3.5, 4.5, 0)))
            geo.append(Part.Point(V(10.0 * i - 4.5, 3.5, 0)))
            geo.append(Part.Point(V(10.0 * i, 0.0, 0)))
        sk.addGeometry(geo, False)
        cons = []
        for i in range(count):
            cons.append(Sketcher.Constraint("Coincident", 3 * i, 1, 3 * i + 1, 1))
            cons.append(Sketcher.Constraint("DistanceX", -1, 1, 3 * i + 2, 1, 10.0 * i))
            cons.append(Sketcher.Constraint("DistanceY", -1, 1, 3 * i + 2, 1, 0.0))
            cons.append(Sketcher.Constraint("Distance", 3 * i + 2, 1, 3 * i, 1, 5.0))
        sk.addConstraint(cons)
        self.assertEqual(sk.solve(), 0)
        for i in range(count):
            a, b = sk.getPoint(3 * i, 1), sk.getPoint(3 * i + 1, 1)
            self.assertAlmostEqual(a.x, b.x, places=7)
            self.assertAlmostEqual(math.hypot(a.x - 10.0 * i, a.y), 5.0, places=7)
            self.assertGreater(a.x, 10.0 * i + 1.0, i)

    def test_twelve_fresh_processes_agree(self):
        """The four-bar, three under-constrained grids of rectangles with
        coincident corners (5, 15 and 40 percent of the constraints left out),
        and three conflicting or redundant ones give identical coordinates and
        lists in 12 fresh processes (this settles `conflictingMap`, a
        pointer-keyed map: the popularity contest ends on the sketcher
        constraint's tag, so its order cannot reach the result)."""
        binary = os.path.join(FreeCAD.getHomePath(), "bin", "FreeCADCmd")
        if not os.path.exists(binary):
            self.skipTest("FreeCADCmd is not next to the home path")
        with tempfile.TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "child.py")
            with open(script, "w") as fh:
                fh.write(
                    "import json, os, sys\n"
                    "from SketcherTests import TestSketchSolverCore as T\n"
                    "res = T._round_all(T._child_cases())\n"
                    "with open(os.environ['CHILD_OUT'], 'w') as fh:\n"
                    "    json.dump(res, fh)\n"
                    "sys.stdout.flush()\n"
                    "os._exit(0)\n"
                )
            results = []
            for batch in range(3):
                procs = []
                for k in range(4):
                    out = os.path.join(tmp, "out_%d_%d.json" % (batch, k))
                    env = dict(os.environ, CHILD_OUT=out)
                    procs.append((out, subprocess.Popen([binary, script], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)))
                for out, proc in procs:
                    proc.wait(timeout=300)
                    with open(out) as fh:
                        results.append(json.load(fh))
        self.assertEqual(len(results), 12)
        for label in results[0]:
            for other in results[1:]:
                self.assertEqual(results[0][label], other[label], label)


class TestSolverCliff(_Base):
    """Checks 1 and 2 of slice 3b: a sketch of thousands of unknowns with a
    few free degrees of freedom is solved quickly and by the minimum norm
    step."""

    def test_one_free_dof_strip_of_1500_points(self):
        """3,000 constraints on 1,500 points with the last one removed: one
        degree of freedom.  Stock took 32 s (SparseQR fill on a wide
        Jacobian, a cubic Gauss-Jordan in the diagnosis)."""
        sk, _ = Corpus.build_coupled(self.doc, "cliff", 3000, omit=1)
        t0 = time.perf_counter()
        self.assertEqual(sk.solve(), 0)
        elapsed = time.perf_counter() - t0
        self.assertEqual(sk.DoF, 1)
        self.assertEqual(sorted(sk.getGeometryWithDependentParameters()), [(1499, 1)])
        self.assertEqual(len(sk.ConflictingConstraints) + len(sk.RedundantConstraints), 0)
        self.assertLess(elapsed, 5.0)
        for i in range(2, 1500):
            p, q, r = (sk.getPoint(j, 1) for j in (i - 2, i - 1, i))
            self.assertAlmostEqual(math.hypot(q.x - r.x, q.y - r.y), 1.0, places=7)
            if i < 1499:
                self.assertAlmostEqual(math.hypot(p.x - r.x, p.y - r.y), 1.0, places=7)

    def test_datum_edit_on_a_wide_strip_stays_in_the_sparse_dogleg(self):
        """Editing a distance on a 1,050 point strip with the last constraint
        removed (2,100 unknowns, 2,099 equations) asks for a minimum norm step
        of norm 170 or more, which the trust region covers in 108 to 182
        iterations, over the old limit of 100.  The sparse DogLeg used to give
        up there and the dense Levenberg-Marquardt, BFGS and SQP fallbacks took
        400 seconds; the limit now extends while the residual halves per window.
        The fallbacks cost minutes, so the time bound is the check that none ran."""
        sk, _ = Corpus.build_coupled(self.doc, "wide", 2100, omit=1)
        self.assertEqual(sk.solve(), 0)
        self.assertEqual(sk.DoF, 1)
        distances = [i for i, c in enumerate(sk.Constraints) if c.Type == "Distance"]
        index = distances[350]
        for step in (0.005, 0.02, 0.5):
            target = sk.Constraints[index].Value + step
            sk.setDatum(index, FreeCAD.Units.Quantity(target, "mm"))
            t0 = time.perf_counter()
            self.assertEqual(sk.solve(), 0, step)
            elapsed = time.perf_counter() - t0
            self.assertLess(elapsed, 10.0, step)
            self.assertEqual(sk.DoF, 1, step)
            self.assertEqual(len(sk.ConflictingConstraints) + len(sk.RedundantConstraints), 0, step)
            for i in distances:
                c = sk.Constraints[i]
                p, q = sk.getPoint(c.First, 1), sk.getPoint(c.Second, 1)
                want = target if i == index else c.Value
                self.assertAlmostEqual(math.hypot(p.x - q.x, p.y - q.y), want, places=7, msg=(step, i))

    def test_dependent_parameters_of_a_strip_with_free_points(self):
        """The diagnosis of a strip with 1, 3 or 5 constraints removed names
        the last 1, 2 or 3 points: the null space basis from back
        substitution on the triangle (only the columns beyond the rank are
        read)."""
        for omit, expected in ((1, [(499, 1)]), (3, [(498, 1), (499, 1)]), (5, [(497, 1), (498, 1), (499, 1)])):
            with partition_threshold(1):
                doc = FreeCAD.newDocument("Dep%d" % omit, "Dep", True, True)
                sk, _ = Corpus.build_coupled(doc, "dep", 1000, omit=omit)
                self.assertEqual(sk.solve(), 0)
                self.assertEqual(sk.DoF, omit)
                self.assertEqual(sorted(sk.getGeometryWithDependentParameters()), expected, omit)
                FreeCAD.closeDocument(doc.Name)

    def test_dependent_parameters_of_a_strip_with_a_hinge(self):
        """One constraint removed from the middle of a 1,000 constraint strip
        leaves a hinge: every point after it can swing, so the dependent
        parameters are those points.  The raw triangle names only about seven
        of them; the rest come from the back substitution."""
        for index in (500, 255, 30):
            with partition_threshold(1):
                doc = FreeCAD.newDocument("Hinge%d" % index, "Hinge", True, True)
                sk, _ = Corpus.build_coupled(doc, "hinge", 1000)
                sk.delConstraint(index)
                self.assertEqual(sk.solve(), 0)
                self.assertEqual(sk.DoF, 1)
                self.assertEqual(sorted(sk.getGeometryWithDependentParameters()), [(g, 1) for g in range(index // 2, 500)], index)
                FreeCAD.closeDocument(doc.Name)

    def _start(self, count):
        h = math.sqrt(3.0) / 2.0
        return [
            (0.5 * i + 0.03 * math.sin(1.7 * i), h * (i % 2) + 0.03 * math.cos(2.3 * i)) for i in range(count)
        ]

    def test_minimum_norm_step_moves_no_farther_than_the_dense_step(self):
        """An under-constrained connected strip (1,000 constraints, one
        removed) solved by the sparse step (threshold 1) against the dense
        one (threshold 0): the same code, DOF and lists, every driving
        distance met, and the displacement from the start no larger than the
        dense step's plus 1 percent."""
        start = self._start(500)

        def solved(threshold):
            with sparse_threshold(threshold), partition_threshold(0):
                doc = FreeCAD.newDocument("MinNorm%d" % threshold, "MinNorm", True, True)
                sk, _ = Corpus.build_coupled(doc, "mn", 1000, omit=1)
                rec = Baseline.record(sk)
                pts = [(sk.getPoint(i, 1).x, sk.getPoint(i, 1).y) for i in range(500)]
                worst = 0.0
                for i in range(2, 500):
                    q, r = pts[i - 1], pts[i]
                    worst = max(worst, abs(math.hypot(q[0] - r[0], q[1] - r[1]) - 1.0))
                    if i < 499:
                        p = pts[i - 2]
                        worst = max(worst, abs(math.hypot(p[0] - r[0], p[1] - r[1]) - 1.0))
                disp = math.sqrt(sum((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 for a, b in zip(pts, start)))
                FreeCAD.closeDocument(doc.Name)
            return rec, worst, disp

        dense, dense_resid, dense_disp = solved(0)
        sparse, sparse_resid, sparse_disp = solved(1)
        for key in ("code", "dof", "conflicting", "redundant", "partial", "malformed", "dependent"):
            self.assertEqual(dense[key], sparse[key], key)
        self.assertLess(dense_resid, 1e-8)
        self.assertLess(sparse_resid, 1e-8)
        self.assertLessEqual(sparse_disp, dense_disp * 1.01 + 1e-12)
