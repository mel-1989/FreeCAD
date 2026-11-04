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
