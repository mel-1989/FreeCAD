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

"""Tests for the drag half of G10 slice 3b: the move-session binding, the
BFGS guard, the stall escape, silent success."""

import math
import time
import unittest

import FreeCAD
import Part
import Sketcher

from SketcherTests import SketchDragMeasure as Measure
from SketcherTests import SketchDragScenarios as Scen

V = FreeCAD.Vector


def _scenario(prefix):
    return [(n, f) for n, f in Scen.SCENARIOS if n.startswith(prefix + "_")][0]


def _drag_positions(sk, geo, pos, targets, session):
    """Dragged-point position after each move (None when the move raised)."""
    out = []
    for x, y in targets:
        try:
            sk.moveGeometry(geo, pos, V(x, y, 0), False, session)
            q = sk.getPoint(geo, pos)
            out.append((q.x, q.y))
        except Exception:
            out.append(None)
    if session:
        sk.endMoveSession()
    return out


class TestDragSession(unittest.TestCase):
    """moveGeometry(..., keep_session) keeps one move session across calls as
    the sketch editor does; the default resets it every call."""

    def setUp(self):
        self.doc = FreeCAD.newDocument("DragSession")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def _s03(self):
        name, builder = _scenario("S03")
        sk, (geo, pos), wp, spec = builder(self.doc)
        sk.solve()
        st = sk.getPoint(geo, pos)
        wp = [(st.x, st.y)] + list(wp[1:])
        return sk, geo, pos, Measure.resample(wp, 1.0)[1:]

    def test_session_differs_from_default(self):
        sk, geo, pos, targets = self._s03()
        targets = targets[:80]
        plain = _drag_positions(sk, geo, pos, targets, False)
        self.doc.removeObject(sk.Name)
        sk, geo, pos, _ = self._s03()
        kept = _drag_positions(sk, geo, pos, targets, True)
        self.assertNotEqual(plain, kept, "a kept session must change the results on S03 at 1 mm")

    def test_default_is_unchanged_by_the_flag_being_false(self):
        sk, geo, pos, targets = self._s03()
        a = _drag_positions(sk, geo, pos, targets[:30], False)
        self.doc.removeObject(sk.Name)
        sk, geo, pos, _ = self._s03()
        b = []
        for x, y in targets[:30]:
            sk.moveGeometry(geo, pos, V(x, y, 0), False, False)
            q = sk.getPoint(geo, pos)
            b.append((q.x, q.y))
        self.assertEqual(a, b)

    def test_end_session_then_new_session(self):
        sk, geo, pos, targets = self._s03()
        sk.moveGeometry(geo, pos, V(*targets[0], 0), False, True)
        sk.endMoveSession()
        # a default call after the end of a session works and resets nothing kept
        sk.moveGeometry(geo, pos, V(*targets[1], 0))
        q = sk.getPoint(geo, pos)
        self.assertAlmostEqual(q.x, targets[1][0], places=3)
        sk.endMoveSession()  # ending twice is harmless

    def test_moveGeometries_accepts_the_flag(self):
        sk, geo, pos, targets = self._s03()
        sk.moveGeometries([(geo, pos)], V(*targets[0], 0), False, True)
        sk.endMoveSession()


def _all_finite(sk):
    for g in sk.Geometry:
        for name in ("StartPoint", "EndPoint", "Center"):
            if hasattr(g, name):
                v = getattr(g, name)
                if not all(map(math.isfinite, (v.x, v.y, v.z))):
                    return False
    return True


class TestDragBfgsGuard(unittest.TestCase):
    """The drag SQP's BFGS update is cautious and its penalty parameter stays
    finite (check 8)."""

    def setUp(self):
        self.doc = FreeCAD.newDocument("DragGuard")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def _slider(self):
        name, builder = _scenario("S08")
        sk, (geo, pos), wp, spec = builder(self.doc)
        sk.solve()
        return sk, geo, pos

    def test_slider_at_69_9_follows_cursor_at_69(self):
        sk, geo, pos = self._slider()
        for x in (62.0, 69.9):
            sk.moveGeometry(geo, pos, V(x, 0, 0))
        for _ in range(4):
            sk.moveGeometry(geo, pos, V(69.0, 0, 0))
            q = sk.getPoint(geo, pos)
            self.assertAlmostEqual(q.x, 69.0, delta=0.01)
            self.assertTrue(_all_finite(sk))

    def test_slider_in_a_kept_session_follows_cursor_at_69(self):
        sk, geo, pos = self._slider()
        for x in (62.0, 69.9, 69.0, 69.0, 69.0):
            sk.moveGeometry(geo, pos, V(x, 0, 0), False, True)
        sk.endMoveSession()
        self.assertAlmostEqual(sk.getPoint(geo, pos).x, 69.0, delta=0.01)
        self.assertTrue(_all_finite(sk))


def _cell(prefix, step, session=True):
    """Trace one scenario at one step length and return its cell statistics."""
    name, builder = _scenario(prefix)
    r = Measure.run_cell(name, builder, step, session)
    return Measure.cell_stats(r, Measure.load_held())


class TestDragEscape(unittest.TestCase):
    """The drag escape: a cursor that comes back within reach of a mechanism
    stopped at a dead point is followed again, and a cursor that stays out of
    reach is not searched for on every move (checks 6, 7 and 10)."""

    def test_dead_point_locks_are_released(self):
        for prefix, step in (("S06", 1.0), ("S06", 0.1), ("S08", 1.0), ("S08", 0.1), ("S19", 0.1)):
            with self.subTest(scenario=prefix, step=step):
                cell = _cell(prefix, step)
                self.assertEqual(cell["lockups_out"] + cell["lockups_ret"], 0, cell)
                self.assertEqual(cell["fails"], 0, cell)

    def test_out_and_back_returns_to_the_start(self):
        for prefix, step in (("S06", 1.0), ("S06", 0.1)):
            with self.subTest(scenario=prefix, step=step):
                self.assertLess(_cell(prefix, step)["hys"], 0.01)

    def test_flat_valley_is_not_a_lock(self):
        # S01 on the return leg is a known open case; the outward leg must still follow
        cell = _cell("S01", 1.0)
        self.assertEqual(cell["lockups_out"], 0, cell)

    def test_unreachable_cursor_is_searched_once(self):
        """A cursor held beyond the slider's stroke costs no more than one
        reachable move once the first move has failed to escape."""
        doc = FreeCAD.newDocument("EscapeCost")
        try:
            name, builder = _scenario("S08")
            sk, (geo, pos), wp, spec = builder(doc)
            sk.solve()
            sk.moveGeometry(geo, pos, V(65, 0, 0), False, True)

            def mean_ms(x, n):
                t0 = time.perf_counter()
                for _ in range(n):
                    sk.moveGeometry(geo, pos, V(x, 0, 0), False, True)
                return (time.perf_counter() - t0) / n

            reachable = min(mean_ms(66.0, 200), mean_ms(66.0, 200))
            sk.moveGeometry(geo, pos, V(80, 0, 0), False, True)
            blocked = min(mean_ms(80.0, 200), mean_ms(80.0, 200))
            sk.endMoveSession()
            self.assertLess(blocked, 3.0 * reachable + 0.02)
        finally:
            FreeCAD.closeDocument(doc.Name)


class TestDragSilentSuccess(unittest.TestCase):
    """A move that ends with its constraints unmet never reports success, and a
    failed move leaves every parameter unchanged (check 9)."""

    def setUp(self):
        self.doc = FreeCAD.newDocument("DragSilent")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def _holds(self, sk):
        """The sketch satisfies its constraints: a fresh copy solves in place."""
        copy = self.doc.addObject("Sketcher::SketchObject", "Copy")
        copy.addGeometry(sk.Geometry, False)
        copy.addConstraint(list(sk.Constraints))
        verts = Measure._verts(sk)
        before = Measure._pts(copy, verts)
        ok = copy.solve() == 0
        after = Measure._pts(copy, verts)
        self.doc.removeObject(copy.Name)
        shift = max(math.hypot(a[0] - b[0], a[1] - b[1]) for a, b in zip(before, after))
        return ok and shift < 1e-6

    def _drive(self, prefix, step, session, first, last):
        name, builder = _scenario(prefix)
        sk, (geo, pos), wp, spec = builder(self.doc)
        sk.solve()
        st = sk.getPoint(geo, pos)
        wp = [(st.x, st.y)] + list(wp[1:])
        path = Measure.resample(wp + wp[-2::-1], step)
        verts = Measure._verts(sk)
        failed = succeeded = 0
        for k, (x, y) in enumerate(path[1:], 1):
            before = Measure._pts(sk, verts)
            try:
                sk.moveGeometry(geo, pos, V(x, y, 0), False, session)
                ok = True
            except Exception:
                ok = False
            after = Measure._pts(sk, verts)
            if not ok:
                failed += 1
                self.assertEqual(before, after, "a failed move changed the sketch at move %d" % k)
            elif first <= k <= last and k % 3 == 0:
                succeeded += 1
                self.assertTrue(self._holds(sk), "move %d reported success with a constraint unmet" % k)
            if k > last:
                break
        if session:
            sk.endMoveSession()
        return failed, succeeded

    def test_s18_past_its_zero_length_line(self):
        for session in (False, True):
            failed, succeeded = self._drive("S18", 0.1, session, 2120, 2200)
            self.assertGreater(failed, 0)
            self.assertGreater(succeeded, 0)

    def test_s03_across_the_axis_in_a_session(self):
        failed, succeeded = self._drive("S03", 1.0, True, 1, 120)
        self.assertGreater(failed, 0)
        self.assertGreater(succeeded, 0)
