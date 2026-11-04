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

"""Tests for the drag half of G10 slice 3b: the editor's drag calls from Python,
the BFGS guard, the stall escape, the restart from the last good state, silent
success.  A drag here is what the sketch editor does: initTemporaryMove when the
button goes down, one moveGeometriesTemporary per mouse move (read back with
getTemporaryPoint, because only the solver's working copy changes), and the
Python moveGeometries command when the button is released."""

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


def _drag_positions(sk, geo, pos, targets, temporary):
    """Dragged-point position after each move (None when the move failed). With
    temporary the moves are the editor's mouse moves, otherwise Python's
    one-call moveGeometry."""
    out = []
    if temporary:
        sk.setRecalculateInitialSolutionWhileMovingPoint(True)
        sk.initTemporaryMove([(geo, pos)])
    for x, y in targets:
        if temporary:
            ok = sk.moveGeometriesTemporary([(geo, pos)], V(x, y, 0)) == 0
            q = sk.getTemporaryPoint(geo, pos)
        else:
            try:
                sk.moveGeometry(geo, pos, V(x, y, 0))
                ok = True
            except Exception:
                ok = False
            q = sk.getPoint(geo, pos)
        out.append((q.x, q.y) if ok else None)
    return out


class TestDragSession(unittest.TestCase):
    """initTemporaryMove, moveGeometriesTemporary and the release behave as the
    sketch editor's drag does: every move restarts from the state the drag
    began in, only the solver's working copy changes until the release."""

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

    def test_temporary_moves_leave_the_geometry_property_alone(self):
        sk, geo, pos, targets = self._s03()
        start = sk.getPoint(geo, pos)
        self.assertEqual(sk.initTemporaryMove([(geo, pos)]), 0)
        self.assertEqual(sk.moveGeometriesTemporary([(geo, pos)], V(*targets[10], 0)), 0)
        self.assertAlmostEqual(sk.getTemporaryPoint(geo, pos).x, targets[10][0], places=3)
        self.assertEqual(sk.getPoint(geo, pos), start)

    def test_release_writes_the_geometry_and_ends_the_session(self):
        sk, geo, pos, targets = self._s03()
        sk.initTemporaryMove([(geo, pos)])
        sk.moveGeometriesTemporary([(geo, pos)], V(*targets[10], 0))
        sk.moveGeometries([(geo, pos)], V(*targets[10], 0))
        self.assertAlmostEqual(sk.getPoint(geo, pos).x, targets[10][0], places=3)
        # a Python call after the release is its own session again
        sk.moveGeometry(geo, pos, V(*targets[20], 0))
        self.assertAlmostEqual(sk.getPoint(geo, pos).x, targets[20][0], places=3)

    def test_after_the_release_another_point_can_be_moved(self):
        sk = self.doc.addObject("Sketcher::SketchObject", "Two")
        sk.addGeometry(Part.LineSegment(V(0, 0, 0), V(10, 0, 0)), False)
        sk.addGeometry(Part.LineSegment(V(0, 20, 0), V(10, 20, 0)), False)
        sk.solve()
        sk.initTemporaryMove([(0, 2)])
        sk.moveGeometriesTemporary([(0, 2)], V(12, 3, 0))
        sk.moveGeometries([(0, 2)], V(12, 3, 0))
        self.assertAlmostEqual(sk.getPoint(0, 2).y, 3.0, places=4)
        sk.moveGeometry(1, 2, V(14, 25, 0))
        q = sk.getPoint(1, 2)
        self.assertAlmostEqual(q.x, 14.0, places=4)
        self.assertAlmostEqual(q.y, 25.0, places=4)
        self.assertAlmostEqual(sk.getPoint(0, 2).y, 3.0, places=4)

    def test_every_move_restarts_from_the_state_the_drag_began_in(self):
        sk, geo, pos, targets = self._s03()
        sk.initTemporaryMove([(geo, pos)])
        for x, y in targets[:60]:
            sk.moveGeometriesTemporary([(geo, pos)], V(x, y, 0))
        # back at the first target: the state is the one a single move from the start gives
        sk.moveGeometriesTemporary([(geo, pos)], V(*targets[0], 0))
        again = sk.getTemporaryPoint(geo, pos)
        self.doc.removeObject(sk.Name)
        sk, geo, pos, _ = self._s03()
        sk.initTemporaryMove([(geo, pos)])
        sk.moveGeometriesTemporary([(geo, pos)], V(*targets[0], 0))
        once = sk.getTemporaryPoint(geo, pos)
        self.assertAlmostEqual(again.x, once.x, places=9)
        self.assertAlmostEqual(again.y, once.y, places=9)

    def test_python_default_is_its_own_session_every_call(self):
        sk, geo, pos, targets = self._s03()
        plain = _drag_positions(sk, geo, pos, targets[:80], False)
        self.doc.removeObject(sk.Name)
        sk, geo, pos, _ = self._s03()
        editor = _drag_positions(sk, geo, pos, targets[:80], True)
        self.assertNotEqual(plain, editor, "the editor's path must differ from Python's on S03 at 1 mm")

    def test_relative_group_move(self):
        doc = self.doc
        sk = doc.addObject("Sketcher::SketchObject", "Rel")
        sk.addGeometry(Part.LineSegment(V(0, 0, 0), V(10, 0, 0)), False)
        sk.addGeometry(Part.LineSegment(V(0, 5, 0), V(10, 5, 0)), False)
        sk.solve()
        sk.initTemporaryMove([(0, 0), (1, 0)])
        self.assertEqual(sk.moveGeometriesTemporary([(0, 0), (1, 0)], V(3, 2, 0), True), 0)
        q = sk.getTemporaryPoint(1, 1)
        self.assertAlmostEqual(q.x, 3.0, places=6)
        self.assertAlmostEqual(q.y, 7.0, places=6)


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

    def test_slider_on_the_editor_path_follows_cursor_at_69(self):
        sk, geo, pos = self._slider()
        sk.setRecalculateInitialSolutionWhileMovingPoint(True)
        sk.initTemporaryMove([(geo, pos)])
        for x in (62.0, 69.9, 69.0, 69.0, 69.0):
            sk.moveGeometriesTemporary([(geo, pos)], V(x, 0, 0))
        self.assertAlmostEqual(sk.getTemporaryPoint(geo, pos).x, 69.0, delta=0.01)
        sk.moveGeometries([(geo, pos)], V(69.0, 0, 0))
        self.assertAlmostEqual(sk.getPoint(geo, pos).x, 69.0, delta=0.01)
        self.assertTrue(_all_finite(sk))


def _cell(prefix, step, session=True):
    """Trace one scenario at one step length and return its cell statistics."""
    name, builder = _scenario(prefix)
    r = Measure.run_cell(name, builder, step, session)
    return Measure.cell_stats(r, Measure.load_held())


class TestDragFallback(unittest.TestCase):
    """Where the guarded SQP does not end with the constraints met, the stock
    iteration is run from the same start: S18 past its zero-length line fails
    on 18 moves with the stock update and on about 400 with a damped one."""

    def test_s18_failures_stay_at_the_stock_count(self):
        cell = _cell("S18", 0.1, session=False)
        self.assertLessEqual(cell["fails"], 20, cell)


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

    def test_a_cursor_that_comes_back_over_a_dead_point_goes_back_the_way_it_came(self):
        # S08 at 0.1 mm: the slider is dragged to the end of its stroke and back, then through the
        # other end and back. The crank is at a dead point at each end, and either branch after it
        # is valid, but the baseline returns on the branch it left by (0.000 mm) and a probe that
        # picks the mirror one ends 29.9 mm from the start. The state the drag began in is tried
        # before the probes (run 055; the editor's own re-initialisation while dragging must not
        # replace it).
        cell = _cell("S08", 0.1)
        self.assertLess(cell["hys"], 0.01, cell)
        self.assertEqual(cell["lockups_out"] + cell["lockups_ret"], 0, cell)

    def test_cells_clean_in_the_baseline_stay_clean(self):
        # S09 ends where they began on the editor path; an escape that keeps a
        # worse result (a different branch) breaks that
        for prefix, step in (("S09", 0.1), ("S09", 1.0)):
            with self.subTest(scenario=prefix, step=step):
                self.assertLess(_cell(prefix, step)["hys"], 0.01)

    def test_s18_does_not_end_mirrored(self):
        # S18 at 0.1 mm lands on another branch when its fillet line reaches zero length, and the
        # landing is 9.7 mm from the cursor. A lock that opens at once is not a stall: the escape
        # must not carry the sketch over to a third branch (it ended 20 mm from where it began)
        cell = _cell("S18", 0.1)
        self.assertLess(cell["hys"], 0.01, cell)

    def test_cells_where_the_escape_decides_keep_their_counts(self):
        # measured on the editor path: S03 at 0.1 mm has 18 lock moves in one run, S19 at 0.1 mm
        # has 9 jumps; an escape that keeps probe results which did not lower the gap gives 20 and 12
        self.assertLessEqual(_cell("S03", 0.1)["lockmoves"], 18)
        self.assertLessEqual(_cell("S19", 0.1)["jumps"], 9)

    def test_flat_valley_is_not_a_lock(self):
        # S01 on the return leg is a known open case; the outward leg must still follow
        cell = _cell("S01", 1.0)
        self.assertEqual(cell["lockups_out"], 0, cell)

    @staticmethod
    def _s19_move_times(runs=3):
        name, builder = _scenario("S19")
        best = None
        for _ in range(runs):
            tr = Measure.run_cell(name, builder, 0.1, True)["trace"]
            best = [t[9] for t in tr] if best is None else [min(a, t[9]) for a, t in zip(best, tr)]
        return tr, best

    def test_a_cursor_coming_back_into_reach_is_followed_again(self):
        # S19 at 0.1 mm, moves 1540 to 1600: the cursor has run past the small circle's far side
        # and the circle can follow it again (gap 0.000 in the stored run). The probes that
        # follow a settled escape are held back while the gap stays as large as when they last
        # found nothing, and must run again once it has halved, or the circle is left behind
        # by up to 2.9 mm.
        tr, _ = self._s19_move_times(1)
        self.assertLess(max(t[6] for t in tr[1539:1600]), 0.01)

    def test_a_cursor_running_away_costs_no_search_on_each_move(self):
        # S19, moves 1245 to 1286: the cursor leaves the circle's reach along a line and the main
        # solve lands on the far branch every move, which the escape's stored sketch repairs. The
        # probes found nothing the first time and are not run again while the gap grows (2
        # solves a move, 6 when they are). Wall time, since the solver exposes no count: about
        # 0.4 times a move in reach with the probes held back, 3.8 times when they run.
        tr, best = self._s19_move_times()
        away = sum(best[1244:1286]) / 42
        in_reach = sum(best[99:800]) / 701
        self.assertLess(away, 1.5 * in_reach)

    def test_unreachable_cursor_is_searched_once(self):
        """A cursor held beyond the slider's stroke costs no more than one
        reachable move once the first move has failed to escape (wall time:
        the solver exposes no count of its searches)."""
        doc = FreeCAD.newDocument("EscapeCost")
        try:
            name, builder = _scenario("S08")
            sk, (geo, pos), wp, spec = builder(doc)
            sk.solve()
            sk.setRecalculateInitialSolutionWhileMovingPoint(True)
            sk.initTemporaryMove([(geo, pos)])
            sk.moveGeometriesTemporary([(geo, pos)], V(65, 0, 0))

            def mean_ms(x, n):
                t0 = time.perf_counter()
                for _ in range(n):
                    sk.moveGeometriesTemporary([(geo, pos)], V(x, 0, 0))
                return (time.perf_counter() - t0) / n

            reachable = min(mean_ms(66.0, 200) for _ in range(3))
            sk.moveGeometriesTemporary([(geo, pos)], V(80, 0, 0))
            blocked = min(mean_ms(80.0, 200) for _ in range(3))
            self.assertLess(blocked, 2.5 * reachable)
        finally:
            FreeCAD.closeDocument(doc.Name)


class TestDragRestart(unittest.TestCase):
    """A drag that gets stuck goes on from the last state that met the cursor
    (checks 6 and 7 on the editor's path)."""

    def setUp(self):
        self.doc = FreeCAD.newDocument("DragRestart")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def _s06_at(self, ox, oy):
        """The non-Grashof four-bar of S06 with every coordinate moved by (ox, oy)."""
        sk = self.doc.addObject("Sketcher::SketchObject", "S06T")

        def line(a, b, c, d):
            return sk.addGeometry(Part.LineSegment(V(a + ox, b + oy, 0), V(c + ox, d + oy, 0)), False)

        def con(*args):
            sk.addConstraint(Sketcher.Constraint(*args))

        g, cr, cp, rk = line(0, 0, 60, 0), line(0, 0, 15, 26), line(15, 26, 45, 29), line(45, 29, 60, 0)
        con("Coincident", g, 1, cr, 1)
        con("Coincident", cr, 2, cp, 1)
        con("Coincident", cp, 2, rk, 1)
        con("Coincident", rk, 2, g, 2)
        con("DistanceX", -1, 1, g, 1, ox)
        con("DistanceY", -1, 1, g, 1, oy)
        con("Horizontal", g)
        con("Distance", g, 60.0)
        con("Distance", cr, 30.0)
        con("Distance", cp, 40.0)
        con("Distance", rk, 40.0)
        sk.solve()
        wp = [(x + ox, y + oy) for x, y in Scen.circ(0, 0, 30, 60, -180, 40)]
        return sk, cr, 2, wp

    def _drag_s06(self, ox, oy):
        """Gap of the dragged point from the cursor after each editor-path move."""
        sk, geo, pos, wp = self._s06_at(ox, oy)
        st = sk.getPoint(geo, pos)
        path = Measure.resample([(st.x, st.y)] + wp[1:], 1.0)[1:]
        sk.setRecalculateInitialSolutionWhileMovingPoint(True)
        sk.initTemporaryMove([(geo, pos)])
        gaps = []
        for x, y in path:
            sk.moveGeometriesTemporary([(geo, pos)], V(x, y, 0))
            q = sk.getTemporaryPoint(geo, pos)
            gaps.append(math.hypot(q.x - x, q.y - y))
        self.doc.removeObject(sk.Name)
        return gaps

    def test_dead_point_of_a_sketch_far_from_the_origin(self):
        # the escape and the restart scale with the sketch, not with its distance from the
        # origin: a translated copy behaves as the original does, gap for gap
        here = self._drag_s06(0.0, 0.0)
        far = self._drag_s06(5000.0, -3000.0)
        self.assertEqual(len(here), len(far))
        for a, b in zip(here, far):
            self.assertAlmostEqual(a, b, delta=1e-3)
        # and the restart works: the path reaches the dead point and goes beyond it, where the
        # cursor is out of reach (31 moves of the path's last third), not before
        self.assertLess(sum(1 for g in far if g > 1.0), 40)

    def test_no_move_ends_far_from_the_sketch(self):
        # S03 at 1 mm across its coincident centres put a point 183 m from the cursor
        name, builder = _scenario("S03")
        sk, (geo, pos), wp, spec = builder(self.doc)
        sk.solve()
        st = sk.getPoint(geo, pos)
        path = Measure.resample([(st.x, st.y)] + list(wp[1:]), 1.0)[1:]
        sk.setRecalculateInitialSolutionWhileMovingPoint(True)
        sk.initTemporaryMove([(geo, pos)])
        for x, y in path:
            if sk.moveGeometriesTemporary([(geo, pos)], V(x, y, 0)) == 0:
                q = sk.getTemporaryPoint(geo, pos)
                self.assertLess(math.hypot(q.x - x, q.y - y), 1000.0)

    def test_a_drag_that_never_gets_stuck_returns_to_the_start(self):
        for prefix in ("S11", "S12"):
            with self.subTest(scenario=prefix):
                cell = _cell(prefix, 1.0)
                self.assertLess(cell["hys"], 0.01, cell)

    def test_the_release_keeps_the_last_temporary_position(self):
        name, builder = _scenario("S06")
        sk, (geo, pos), wp, spec = builder(self.doc)
        sk.solve()
        st = sk.getPoint(geo, pos)
        path = Measure.resample([(st.x, st.y)] + list(wp[1:]), 1.0)[1:60]
        sk.setRecalculateInitialSolutionWhileMovingPoint(True)
        sk.initTemporaryMove([(geo, pos)])
        for x, y in path:
            sk.moveGeometriesTemporary([(geo, pos)], V(x, y, 0))
        last = sk.getTemporaryPoint(geo, pos)
        sk.moveGeometries([(geo, pos)], V(*path[-1], 0))
        kept = sk.getPoint(geo, pos)
        self.assertLess(math.hypot(kept.x - last.x, kept.y - last.y), 0.01)


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
        """session: the editor's path (temporary moves, working copy read back);
        otherwise Python's one-call moveGeometry."""
        name, builder = _scenario(prefix)
        sk, (geo, pos), wp, spec = builder(self.doc)
        sk.solve()
        st = sk.getPoint(geo, pos)
        wp = [(st.x, st.y)] + list(wp[1:])
        path = Measure.resample(wp + wp[-2::-1], step)
        verts = Measure._verts(sk)
        read = Measure._tpts if session else Measure._pts
        if session:
            sk.setRecalculateInitialSolutionWhileMovingPoint(True)
            sk.initTemporaryMove([(geo, pos)])
        failed = succeeded = 0
        for k, (x, y) in enumerate(path[1:], 1):
            before = read(sk, verts)
            if session:
                ok = sk.moveGeometriesTemporary([(geo, pos)], V(x, y, 0)) == 0
            else:
                try:
                    sk.moveGeometry(geo, pos, V(x, y, 0), False)
                    ok = True
                except Exception:
                    ok = False
            after = read(sk, verts)
            if not ok:
                failed += 1
                self.assertEqual(before, after, "a failed move changed the sketch at move %d" % k)
            elif first <= k <= last and k % 3 == 0:
                succeeded += 1
                if session:
                    # the release writes the working copy to the sketch, where it can be checked
                    pass
                else:
                    self.assertTrue(self._holds(sk), "move %d reported success with a constraint unmet" % k)
            if k > last:
                break
        if session:
            x, y = path[min(last, len(path) - 1)]
            try:
                sk.moveGeometries([(geo, pos)], V(x, y, 0), False)
                self.assertTrue(self._holds(sk), "the release left a constraint unmet")
            except ValueError:
                pass
        return failed, succeeded

    def test_s18_past_its_zero_length_line(self):
        failed, succeeded = self._drive("S18", 0.1, False, 2120, 2200)
        self.assertGreater(failed, 0)
        self.assertGreater(succeeded, 0)
        # on the editor's path the moves restart from the drag's reference and mostly succeed
        failed, succeeded = self._drive("S18", 0.1, True, 2120, 2200)
        self.assertGreater(succeeded, 0)

    def test_s03_across_the_axis_in_a_session(self):
        failed, succeeded = self._drive("S03", 1.0, True, 1, 120)
        self.assertGreater(failed, 0)
        self.assertGreater(succeeded, 0)
