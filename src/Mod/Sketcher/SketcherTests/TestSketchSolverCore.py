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

import unittest

import FreeCAD
import Part
import Sketcher

V = FreeCAD.Vector


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


class TestSketchSolverCore(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument("TestSketchSolverCore")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def test_fourbar_solves_to_the_same_branch_every_time(self):
        """The unknowns of a subsystem keep the order of the system's
        parameter list, not the order of their heap addresses, so the branch
        of a one-DOF mechanism does not depend on the process or the
        document (run 053: two assemblies, 1.2 mm apart, by process)."""
        ends = set()
        for i in range(12):
            sk, cr = _fourbar(self.doc, "FourBar%d" % i)
            self.assertEqual(sk.solve(), 0)
            self.assertEqual(sk.DoF, 1)
            p = sk.getPoint(cr, 2)
            ends.add((round(p.x, 6), round(p.y, 6)))
        self.assertEqual(len(ends), 1, ends)
