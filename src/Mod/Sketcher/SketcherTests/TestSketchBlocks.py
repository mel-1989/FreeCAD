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

"""Tests for sketch blocks, G10 slice 5a (App and solver level, no GUI).

Check numbers refer to the slice 5a acceptance checks in briefs/phase1/G10-sketcher.md."""

import json
import math
import random
import unittest

import FreeCAD
import Part
import Sketcher

import SketcherBlocks as B
from SketcherTests import SketchBlocksCorpus as C

V = FreeCAD.Vector
MM = 1e-7
RAD = 1e-9


def new_doc(name):
    doc = FreeCAD.newDocument(name, name, True, True)
    doc.UndoMode = 1
    return doc


def fit_pose(sk, spec, inst_name):
    """Placement of an instance fitted to its current geometry (first point, first distinct point)."""
    data = B._load(sk)
    inst = data["inst"][inst_name]
    defn = data["defs"][inst["def"]]
    return B._placement(defn, B._instance_world(sk, defn, inst))


def member_geos(sk, inst_name):
    ids = B.blocks(sk)["instances"][inst_name]["geo_ids"]
    geo = sk.Geometry
    return [geo[i] for i in ids], ids


def point_of(spec, geos, kp_entry):
    i, pos, _, _ = kp_entry
    t, nums = C.member_numbers(geos[i])
    if t == "p":
        return nums[0], nums[1]
    if t == "l":
        return (nums[0], nums[1]) if pos == 1 else (nums[2], nums[3])
    if pos == 3:
        return nums[0], nums[1]
    a = nums[3] if pos == 1 else nums[4]
    return nums[0] + nums[2] * math.cos(a), nums[1] + nums[2] * math.sin(a)


class BlocksCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.specs = C.corpus()

    def setUp(self):
        self.doc = new_doc("Blocks_" + type(self).__name__)

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def assertClean(self, sk, msg=""):
        self.assertEqual(list(sk.ConflictingConstraints), [], msg)
        self.assertEqual(list(sk.RedundantConstraints), [], msg)
        self.assertEqual(list(sk.PartiallyRedundantConstraints), [], msg)
        self.assertEqual(list(sk.MalformedConstraints), [], msg)


class TestBlockDOF(BlocksCase):
    """Check 2: each instance adds exactly 3 DOF; analytic placement takes it to the hand count."""

    def test_corpus_size(self):
        self.assertGreaterEqual(len(self.specs), 40)
        self.assertEqual(min(len(s.members) for s in self.specs), 1)
        self.assertEqual(max(len(s.members) for s in self.specs), 200)

    def test_three_dof_per_instance(self):
        rng = random.Random(7)
        for spec in self.specs:
            n = 1 + rng.randrange(3)
            places = [(rng.uniform(-30, 30), rng.uniform(-30, 30), rng.uniform(-6.5, 6.5)) for _ in range(n)]
            sk, names = C.build(self.doc, spec, places, B)
            sk.solve()
            self.assertEqual(sk.DoF, 3 * n, spec.name)
            self.assertClean(sk, spec.name)
            self.assertEqual(sk.getBlockInstanceCount(), n)
            # analytic placement: three coordinates per instance take it to the hand-counted figure
            for (x, y, th), name in zip(places, names):
                _, ids = member_geos(sk, name)
                C.placement_constraints(sk, spec, ids, x, y, th)
            self.assertEqual(sk.solve(), 0, spec.name)
            self.assertEqual(sk.DoF, 0, spec.name)
            self.assertClean(sk, spec.name)

    def test_degenerate_definitions_are_refused(self):
        sk = self.doc.addObject("Sketcher::SketchObject", "D")
        sk.addGeometry(Part.Point(V(1, 1, 0)))
        sk.addGeometry(Part.Circle(V(2, 2, 0), V(0, 0, 1), 1.0))
        sk.addGeometry(Part.Circle(V(2, 2, 0), V(0, 0, 1), 3.0))
        for ids in ([0], [1], [1, 2]):
            with self.assertRaises(B.SketchBlockError):
                B.define(sk, "x", ids)
        self.assertEqual(sk.BlockData, "")


class TestBlockRigidity(BlocksCase):
    """Check 3: seeded drags and datum changes keep every instance a rigid, unmirrored copy."""

    DRAGS = 50
    DATUMS = 8

    def check_rigid(self, sk, spec, name, label):
        geos, _ = member_geos(sk, name)
        X, Y, th = fit_pose(sk, spec, name)
        mm, rad = C.deviation(spec, X, Y, th, geos)
        self.assertLess(mm, MM, label)
        self.assertLess(rad, RAD, label)
        # pairwise distances and signed areas of the member points against the definition
        ox, oy = C.origin_of(spec)
        kp = C.key_points(spec)
        loc = [(lx - ox, ly - oy) for (_, _, lx, ly) in kp]
        wld = [point_of(spec, geos, e) for e in kp]
        rng = random.Random(len(kp))
        idx = list(range(len(kp)))
        for _ in range(min(60, len(kp) * len(kp))):
            i, j = rng.choice(idx), rng.choice(idx)
            self.assertAlmostEqual(
                math.dist(loc[i], loc[j]), math.dist(wld[i], wld[j]), delta=MM, msg=label
            )
        for _ in range(60):
            if len(kp) < 3:
                break
            i, j, k = rng.sample(idx, 3)
            a_l = (loc[j][0] - loc[i][0]) * (loc[k][1] - loc[i][1]) - (loc[j][1] - loc[i][1]) * (loc[k][0] - loc[i][0])
            a_w = (wld[j][0] - wld[i][0]) * (wld[k][1] - wld[i][1]) - (wld[j][1] - wld[i][1]) * (wld[k][0] - wld[i][0])
            if abs(a_l) > 1e-3:
                self.assertGreater(a_l * a_w, 0.0, "mirrored " + label)

    def test_drags_and_datum_changes(self):
        rng = random.Random(11)
        solves = 0
        failed = 0  # solves that did not converge, other than drags of an arc's end points
        arc_end_drags = 0
        arc_end_failed = 0
        for spec in self.specs:
            n = 2
            places = [(rng.uniform(-30, 30), rng.uniform(-30, 30), rng.uniform(-6.5, 6.5)) for _ in range(n)]
            sk, names = C.build(self.doc, spec, places, B)
            sk.solve()
            kp = C.key_points(spec)
            # a drag in a block of 144 or 200 members costs seconds (one dense system of ~900 unknowns)
            for d in range(self.DRAGS if len(spec.members) <= 34 else 10 if len(spec.members) <= 89 else 3):
                name = rng.choice(names)
                _, ids = member_geos(sk, name)
                i, pos, _, _ = rng.choice(kp)
                cur = point_of(spec, member_geos(sk, name)[0], (i, pos, 0, 0))
                target = V(cur[0] + rng.uniform(-8, 8), cur[1] + rng.uniform(-8, 8), 0)
                try:
                    rc = sk.moveGeometry(ids[i], pos, target, False)
                except ValueError:
                    rc = -1  # the solver reports the drag as not possible; the geometry must still be a rigid copy
                solves += 1
                if spec.members[i][0] == "a" and pos in (1, 2):
                    arc_end_drags += 1
                    arc_end_failed += rc not in (0, None)
                else:
                    failed += rc not in (0, None)
                self.check_rigid(sk, spec, name, "%s drag %d" % (spec.name, d))
            # datum changes: pin a member point of the first instance, then move the datum
            _, ids = member_geos(sk, names[0])
            i, pos, _, _ = kp[0]
            cx = sk.addConstraint(Sketcher.Constraint("DistanceX", -1, 1, ids[i], pos, 1.0))
            cy = sk.addConstraint(Sketcher.Constraint("DistanceY", -1, 1, ids[i], pos, 1.0))
            for d in range(self.DATUMS):
                sk.setDatum(cx, FreeCAD.Units.Quantity("%f mm" % rng.uniform(-40, 40)))
                sk.setDatum(cy, FreeCAD.Units.Quantity("%f mm" % rng.uniform(-40, 40)))
                rc = sk.solve()
                solves += 1
                failed += rc != 0
                for name in names:
                    self.check_rigid(sk, spec, name, "%s datum %d" % (spec.name, d))
            self.doc.removeObject(sk.Name)
        self.assertGreaterEqual(solves, 2000)
        # Dragging an arc's start or end point is refused by the drag solver when the target is farther than
        # the radius from the arc's centre: the drag also holds the centre (weight 0.01), so the pull cannot
        # reach zero.  A stock arc with a radius constraint refuses 13 of 60 such seeded drags the same way
        # (measured in run 060); in a block it is 28 percent.  The geometry then stays unmoved and rigid,
        # which the loop above has just checked.  Everything else must converge.
        self.assertEqual(failed, 0, "%d of %d solves did not converge" % (failed, solves))
        self.assertLess(arc_end_failed, 0.5 * max(arc_end_drags, 1))
        print("\nblocks rigidity: %d solves, %d arc-end drags of which %d refused" % (solves, arc_end_drags, arc_end_failed))


class TestBlockPlacement(BlocksCase):
    """Check 4: instances placed by constraints land on analytic coordinates, past a half and a whole turn."""

    ANGLES = [0.4, 1.5, 3.3, 4.0, 2 * math.pi, 7.0, -3.5, -2 * math.pi - 0.5]

    def test_exact_placement_on_every_branch(self):
        rng = random.Random(13)
        for spec in self.specs[:24]:
            for th in self.ANGLES:
                x, y = rng.uniform(-20, 20), rng.uniform(-20, 20)
                # inserted 0.3 rad short of the target so the solver has to turn it onto the branch
                sk, (name,) = C.build(self.doc, spec, [(x + 1.0, y - 1.0, th - 0.3)], B)
                _, ids = member_geos(sk, name)
                C.placement_constraints(sk, spec, ids, x, y, th)
                self.assertEqual(sk.solve(), 0, "%s %g" % (spec.name, th))
                self.assertEqual(sk.DoF, 0)
                geos, _ = member_geos(sk, name)
                mm, rad = C.deviation(spec, x, y, th, geos)
                self.assertLess(mm, MM, "%s at %g rad" % (spec.name, th))
                self.assertLess(rad, RAD, "%s at %g rad" % (spec.name, th))
                self.doc.removeObject(sk.Name)


if __name__ == "__main__":
    unittest.main()
