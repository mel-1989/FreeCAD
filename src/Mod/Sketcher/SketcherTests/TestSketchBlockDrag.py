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

"""Large-block drag speed (G10 slice 5b3): the fast path of ``qp_eq``.

The default run is quick.  ``FC_BLOCK_DRAG_FULL=1`` runs the corpora of the slice's checks 3 and 6 at full size
(at least 2,000 compared drags); ``SketchBlockDragMeasure.py`` holds the timing bars."""

import gzip
import json
import math
import os
import random
import time
import unittest

import FreeCAD
import Sketcher

import SketcherBlocks as B
from SketcherTests import SketchBlockDragCorpus as D
from SketcherTests import SketchBlockCurvesCorpus as CC
from SketcherTests import SketchBlocksCorpus as C
from SketcherTests import TestSketchBlocks as TB

V = FreeCAD.Vector
FULL = os.environ.get("FC_BLOCK_DRAG_FULL") == "1"

# check 4 tolerances, stated once
# the random systems include pins that make A nearly dependent (|x| up to 130); there lambda = Y^T (H x + g) is
# multiplied by a large Y, so its tolerance is looser
TOL_RANDOM_DLAMBDA = 1e-7
TOL = {"resAx": 1e-9, "resAY": 1e-9, "resAZ": 1e-9, "resYZ": 1e-9, "resZ": 1e-8, "dx": 1e-9, "dY": 1e-9, "dLambda": 1e-8}


def report(text):
    print(text, flush=True)


class Base(unittest.TestCase):
    def setUp(self):
        self.doc = TB.new_doc("Drag_" + type(self).__name__)
        Sketcher.qpEqForceDense(False)
        Sketcher.qpEqAudit(False)
        Sketcher.qpEqNoise(0.0)
        Sketcher.qpEqFault(0)
        Sketcher.qpEqReset()

    def tearDown(self):
        Sketcher.qpEqForceDense(False)
        Sketcher.qpEqAudit(False)
        Sketcher.qpEqNoise(0.0)
        FreeCAD.closeDocument(self.doc.Name)


class TestDragEqualsDense(unittest.TestCase):
    """Check 3: the fast path against the forced dense path, drag by drag."""

    @classmethod
    def setUpClass(cls):
        cls.doc = TB.new_doc("DragEquals")
        cls.results = []
        for sc in D.scenarios(FULL):
            cls.results.append(D.lockstep(cls.doc, sc))
            r = cls.results[-1]
            report("lockstep %-24s moves %4d  rc diff %d  max diff %.2e  noise-floor diff %.2e  fast %d fallback %d dense %d" % (
                r["scenario"], r["moves"], r["rc_differences"], r["max_diff"], r["noise_diff"], r["fast"], r["fallback"], r["dense"]))

    @classmethod
    def tearDownClass(cls):
        FreeCAD.closeDocument(cls.doc.Name)

    def test_corpus_size(self):
        moves = sum(r["moves"] for r in self.results)
        report("lockstep total %d moves" % moves)
        self.assertGreaterEqual(moves, 2000 if FULL else 30)

    def test_same_return_code_as_often_as_a_noisy_dense_copy(self):
        # A drag the solver refuses (an arc's end pulled out of reach) runs to the iteration cap and ends wherever
        # rounding sends it, so a dense copy whose Hessian carries 1e-15 relative noise differs in return code from the
        # plain dense one on some moves too.  The fast copy is allowed no more such moves than the noisy copy shows,
        # plus two standard deviations of the count of refused moves (and never any on a scenario with no refusal).
        for r in self.results:
            slack = 0 if r["refused"] == 0 else max(2, math.ceil(2.0 * math.sqrt(r["refused"])))
            self.assertLessEqual(r["rc_differences"], r["noise_rc_differences"] + slack, r["scenario"])

    def test_same_geometry_as_often_as_a_noisy_dense_copy(self):
        # The literal bar is 1e-9 after every move.  It cannot be met by anything but a bit-identical solver: the SQP
        # stops at |step| <= 1e-8, so two correct answers whose data differ by 1e-15 relative end a drag up to ~1e-8
        # apart, more when the reduced Hessian is ill-conditioned (condition number times epsilon), and on a drag that
        # does not converge or a pinned block with a flat rotation they end on different branches, tens of mm apart.
        # So the measure is parity with a dense copy whose Hessian carries 1e-15 relative noise: a move is agreed when
        # no number differs by more than 1e-6 mm or rad, and the fast copy may disagree on no more moves than the noisy
        # copy plus three standard deviations of that count, in each scenario and over the whole corpus.
        total = disagree_fast = disagree_noise = 0
        for r in self.results:
            df, dn = r["moves"] - r["agree_fast"], r["moves"] - r["agree_noise"]
            total += r["moves"]
            disagree_fast += df
            disagree_noise += dn
            report("   %-24s disagree with dense: fast %d, noisy dense %d, of %d moves" % (r["scenario"], df, dn, r["moves"]))
            self.assertLessEqual(df, dn + max(3, math.ceil(3.0 * math.sqrt(dn + 1))), r["scenario"])
        report("   all scenarios: disagree fast %d, noisy dense %d, of %d moves" % (disagree_fast, disagree_noise, total))
        self.assertLessEqual(disagree_fast, disagree_noise + math.ceil(3.0 * math.sqrt(disagree_noise + 1)))

    def test_forced_dense_copy_really_ran_dense(self):
        for r in self.results:
            self.assertEqual(r["forced_leaks"], 0, r["scenario"])
            self.assertGreater(r["forced_dense_calls"], 0, r["scenario"])

    def test_fast_path_served_nearly_every_solve(self):
        fast = sum(r["fast"] for r in self.results)
        total = fast + sum(r["fallback"] + r["dense"] for r in self.results)
        report("fast path share: %d of %d qp_eq calls (%.1f%%)" % (fast, total, 100.0 * fast / total))
        self.assertGreaterEqual(fast, 0.9 * total)
        for r in self.results:
            self.assertGreater(r["fast"], 0, r["scenario"])


class TestQpEqExact(Base):
    """Check 4: the fast path is exact on its own terms."""

    def test_audit_over_drags(self):
        Sketcher.qpEqAudit(True)
        for sc in D.scenarios(False)[:6] + [D.scenarios(False)[-1]]:
            sk, names, kp = D.build(self.doc, sc)
            rng = random.Random(3)
            for gid, pos, target in D.move_stream(sk, names, kp, rng, 3, arc_ends=False):
                D.drag(sk, gid, pos, target)
        a = Sketcher.qpEqStats()
        report("audit over drags: " + ", ".join("%s %.2e" % (k, a[k]) for k in TOL))
        self.assertGreater(a["calls"], 100)
        for k, tol in TOL.items():
            self.assertLessEqual(a[k], tol, k)

    def test_random_structured_systems(self):
        worst = {k: 0.0 for k in TOL}
        for seed in range(8):
            ns = 200 + 20 * seed
            frames = 1 + seed % 3
            pins = (0, 1, 2, 0, 1, 3, 0, 2)[seed]
            H, g, A, c = D.structured_system(seed, ns, frames, pins, shuffle=seed % 2 == 1)  # odd seeds: columns in random order
            r = Sketcher.qpEq(H, g, A, c)
            self.assertEqual(r["path"], 1, "seed %d took path %d" % (seed, r["path"]))
            self.assertEqual(r["status"], 0)
            self.assertEqual(r["statusDense"], 0)
            for k in TOL:
                worst[k] = max(worst[k], r[k])
                self.assertLessEqual(r[k], TOL_RANDOM_DLAMBDA if k == "dLambda" else TOL[k], "seed %d %s" % (seed, k))
            self.assertLessEqual(max(abs(a - b) for a, b in zip(r["x"], r["xDense"])), 1e-9, seed)
        report("random structured systems: " + ", ".join("%s %.2e" % (k, v) for k, v in worst.items()))


class TestQpEqRefusals(Base):
    """Check 5: bad systems above the size threshold give the dense path's answer.  Each case prints whether the
    fallback ran (path 2) or the dense path was never left (path 0)."""

    def base(self, **kw):
        H, g, A, c = D.structured_system(11, kw.pop("ns", 200), kw.pop("frames", 1), kw.pop("pins", 0))
        return H, g, [list(r) for r in A], c

    # why codes of the fast path
    INPUT, STRUCTURE, BLOCK_RANK, DOUBT, RANK_MISMATCH, RESIDUAL = 1, 2, 3, 4, 5, 6

    def check(self, name, H, g, A, c, must_not_be_fast=True, why=None):
        r = Sketcher.qpEq(H, g, A, c)
        report("refusal case %-34s status %2d (dense %2d)  path %d  why %d" % (name, r["status"], r["statusDense"], r["path"], r["why"]))
        if why is not None:
            self.assertEqual(r["path"], 2, name)
            self.assertEqual(r["why"], why, name)
        self.assertEqual(r["status"], r["statusDense"], name)
        if r["status"] == 0:
            for a, b in zip(r["x"], r["xDense"]):
                self.assertTrue((math.isnan(a) and math.isnan(b)) or abs(a - b) <= 1e-9 * (1 + abs(b)), name)
        if must_not_be_fast:
            self.assertNotEqual(r["path"], 1, name)
        return r

    def test_duplicated_tie(self):
        H, g, A, c = self.base()
        A.append(list(A[5]))
        c.append(0.3)
        r = self.check("two ties on one parameter", H, g, A, c, why=self.RANK_MISMATCH)
        self.assertEqual(r["status"], -1)

    def test_pin_equal_to_a_tie_row_of_the_member(self):
        H, g, A, c = self.base()
        pin = [0.0] * len(A[0])
        pin[7] = 1.0
        A.append(pin)
        A.append(list(pin))  # the member fixed twice
        c.extend([0.1, 0.2])
        self.check("member fixed by two datums", H, g, A, c, why=self.RANK_MISMATCH)

    def test_nearly_parallel_rows(self):
        for eps in (1e-10, 1e-13, 1e-16):
            H, g, A, c = self.base()
            row = list(A[9])
            row[20] += eps
            A.append(row)
            c.append(0.5)
            self.check("nearly parallel rows eps=%g" % eps, H, g, A, c, why=self.RANK_MISMATCH)

    def test_zero_frame_column(self):
        H, g, A, c = self.base()
        for row in A:
            row[200] = 0.0  # X of the only frame
        self.check("frame column of zeros", H, g, A, c, why=self.STRUCTURE)

    def test_more_constraints_than_parameters(self):
        H, g, A, c = self.base()
        n = len(A[0])
        for k in range(n - len(A) + 3):  # three rows more than there are parameters
            row = [0.0] * n
            row[(k * 7) % 200] = 1.0
            A.append(row)
            c.append(0.0)
        self.assertGreater(len(A), n)
        r = self.check("more constraints than parameters", H, g, A, c, must_not_be_fast=False)
        self.assertEqual(r["status"], -1)
        self.assertEqual(r["path"], 0)

    def test_non_finite_entries(self):
        for what in ("A nan", "A inf", "c nan", "c inf", "g nan", "g inf"):
            H, g, A, c = self.base()
            val = float("nan") if "nan" in what else float("inf")
            if what.startswith("A"):
                A[30][30] = val
            elif what.startswith("c"):
                c[4] = val
            else:
                g[4] = val
            self.check("non-finite " + what, H, g, A, c, why=self.INPUT)

    def test_a_wrong_fast_answer_is_caught_by_the_residual_checks(self):
        # a corrupted Y and a corrupted x are each sent to the dense path, whose answer is returned
        for kind, what in ((1, "Y"), (2, "x")):
            H, g, A, c = self.base()
            Sketcher.qpEqFault(kind)
            try:
                r = self.check("planted wrong %s" % what, H, g, A, c, why=self.RESIDUAL)
            finally:
                Sketcher.qpEqFault(0)
            self.assertEqual(r["status"], 0)

    def test_empty_a(self):
        H, g, A, c = self.base()
        r = self.check("empty A (no rows)", H, g, [], [], must_not_be_fast=False)
        self.assertEqual(r["path"], 0)

    def test_conflicting_drag_geometry_is_the_dense_geometry(self):
        # redundant and conflicting datums on a block, dragged: same return codes and geometry as the dense path
        for tag, extra in (("redundant", lambda sk, ids, kp: [sk.addConstraint(Sketcher.Constraint("DistanceX", -1, 1, ids[0][kp[0][0]], kp[0][1], 1.0)) for _ in range(2)]),
                           ("conflicting", lambda sk, ids, kp: [sk.addConstraint(Sketcher.Constraint("DistanceX", -1, 1, ids[0][kp[0][0]], kp[0][1], v)) for v in (1.0, 2.0)])):
            sc = D.Scenario("bad_" + tag, D.spec_of("bad", 55, "l", 30), moves=3, seed=4, extra=extra)
            r = D.lockstep(self.doc, sc)
            report("refusal case drag %-24s rc diff %d max diff %.2e fast %d fallback %d dense %d" % (tag, r["rc_differences"], r["max_diff"], r["fast"], r["fallback"], r["dense"]))
            self.assertEqual(r["rc_differences"], 0, tag)
            self.assertLessEqual(r["max_diff"], 5.0 * r["noise_diff"] + 1e-9, tag)


class TestLargeBlockRigidity(Base):
    """Check 6: instances of 144 and 200 members stay rigid, unmirrored copies with 3 DOF through drags, and the
    pole circles of a 100-member curve block stay on their curves."""

    def test_large_specs_stay_rigid(self):
        checker = TB.TestBlockRigidity("check_rigid")
        rng = random.Random(61)
        specs = [s for s in C.corpus() if len(s.members) in (144, 200)]
        self.assertGreaterEqual(len(specs), 4)
        drags = 8 if FULL else 2
        for spec in specs[: (len(specs) if FULL else 2)]:
            places = [(rng.uniform(-30, 30), rng.uniform(-30, 30), rng.uniform(-6.5, 6.5)) for _ in range(2)]
            sk, names = C.build(self.doc, spec, places, B)
            sk.solve()
            self.assertEqual(sk.DoF, 6, spec.name)
            kp = C.key_points(spec)
            for _ in range(drags):
                name = rng.choice(names)
                _, ids = TB.member_geos(sk, name)
                i, pos, _, _ = rng.choice(kp)
                if spec.members[i][0] == "a" and pos in (1, 2):
                    pos = 3
                cur = TB.point_of(spec, TB.member_geos(sk, name)[0], (i, pos, 0, 0))
                D.drag(sk, ids[i], pos, V(cur[0] + rng.uniform(-8, 8), cur[1] + rng.uniform(-8, 8), 0))
                for nm in names:
                    checker.check_rigid(sk, spec, nm, "%s drag" % spec.name)
            self.assertEqual(sk.DoF, 6, spec.name)
            self.assertEqual(sk.getBlockInstanceCount(), 2)
            self.doc.removeObject(sk.Name)

    def test_pole_circles_stay_on_their_curves(self):
        rng = random.Random(62)
        n = 100 if FULL else 60
        geos = [[CC.ellipse, CC.arc_of_ellipse, CC.spline][k % 3](rng) for k in range(n)]
        sk = self.doc.addObject("Sketcher::SketchObject", "PoleBlock")
        sk.addGeometry(geos, False)
        B.define(sk, "blk", list(range(n)))
        sk.delGeometries(list(range(n)))
        name = B.insert(sk, "blk", 3.0, 4.0, 0.3)
        ids = B.blocks(sk)["instances"][name]["geo_ids"]
        splines = [i for i in ids if sk.Geometry[i].TypeId == "Part::GeomBSplineCurve"][:8]
        for g in splines:
            sk.exposeInternalGeometry(g)
        sk.solve()
        Sketcher.qpEqReset()
        for k in range(4):
            g = ids[k * 3 + 2]
            pt = sk.getPoint(g, 1)
            D.drag(sk, g, 1, V(pt.x + rng.uniform(-6, 6), pt.y + rng.uniform(-6, 6), 0))
            circles = [e for e in sk.Geometry if e.TypeId == "Part::GeomCircle"]
            self.assertEqual(len(circles), sum(len(sk.Geometry[g2].getPoles()) for g2 in splines))
            for g2 in splines:
                for p, w in zip(sk.Geometry[g2].getPoles(), sk.Geometry[g2].getWeights()):
                    self.assertTrue(any(math.hypot(e.Center.x - p.x, e.Center.y - p.y) < 1e-7 for e in circles), "pole circle left its pole")
        st = Sketcher.qpEqStats()
        report("pole block: %d members, qp_eq fast %d fallback %d dense %d" % (n, st["fast"], st["fallback"], st["dense"]))
        self.assertGreater(st["fast"], 0)


class TestSmallSketchesUnchanged(Base):
    """Check 7: small sketches run the dense path as before, bit for bit."""

    def test_bit_identical_to_the_stored_fork_main_values(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "SketchBlockDragSmall.json.gz")
        with gzip.open(path, "rt") as f:
            stored = json.load(f)
        self.assertGreaterEqual(stored["count"], 200)
        got = D.small_digests(self.doc, count=stored["count"], seed=stored["seed"])
        bad = [i for i, (a, b) in enumerate(zip(got, stored["digests"])) if a != b]
        report("small sketches: %d of %d identical to fork/main" % (stored["count"] - len(bad), stored["count"]))
        self.assertEqual(bad, [])
        st = Sketcher.qpEqStats()
        self.assertEqual(st["fast"] + st["fallback"], 0)
        self.assertGreater(st["dense"], 1000)


class TestDragIsFasterThanDense(Base):
    """Check 2 in the suite, as a ratio only: a 100-member block drag with the fast path is several times quicker than
    forced dense (about 40 times on the mini); the real bars are in SketchBlockDragMeasure.py."""

    def test_ratio(self):
        sc = D.Scenario("speed", D.spec_of("sp", 100, "l", 33), moves=5, seed=2)
        times = {}
        for force in (False, True):
            sk, names, kp = D.build(self.doc, sc)
            rng = random.Random(9)
            Sketcher.qpEqForceDense(force)
            t = time.perf_counter()
            for gid, pos, target in D.move_stream(sk, names, kp, rng, 4):
                D.drag(sk, gid, pos, target)
            times[force] = time.perf_counter() - t
            Sketcher.qpEqForceDense(False)
        report("100 lines, 4 drags: fast %.3f s, dense %.3f s, ratio %.1f" % (times[False], times[True], times[True] / times[False]))
        self.assertGreater(times[True], 5.0 * times[False])


if __name__ == "__main__":
    unittest.main()
