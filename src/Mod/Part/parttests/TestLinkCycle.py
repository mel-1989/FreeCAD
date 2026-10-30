# SPDX-License-Identifier: LGPL-2.1-or-later

import unittest

import FreeCAD as App
import Part


class TestLinkGroupCycle(unittest.TestCase):
    """A link group that contains itself, directly or through another group, must not crash.

    The cycle can be set from Python through ElementList. Computing the shape of such a group
    used to recurse until the stack overflowed (SIGSEGV), killing the whole process.
    """

    def setUp(self):
        self.doc = App.newDocument("LinkGroupCycle")
        self.box = self.doc.addObject("Part::Box", "X")
        self.link = self.doc.addObject("App::Link", "LX")
        self.link.LinkedObject = self.box
        self.lg1 = self.doc.addObject("App::LinkGroup", "LG1")
        self.lg2 = self.doc.addObject("App::LinkGroup", "LG2")
        self.compound = self.doc.addObject("Part::Compound", "C")
        self.lg1.ElementList = [self.link]
        self.compound.Links = [self.lg1]
        self.doc.recompute()
        self.assertFalse(self.compound.Shape.isNull())

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def _assert_survives(self):
        # the shape of a cyclic group is empty or an error, never a crash
        try:
            Part.getShape(self.lg1)
        except Exception:
            pass
        self.doc.recompute()

    def test_two_groups_cycle(self):
        self.lg2.ElementList = [self.lg1]
        self.lg1.ElementList = [self.link, self.lg2]
        self._assert_survives()

    def test_self_cycle(self):
        self.lg1.ElementList = [self.link, self.lg1]
        self._assert_survives()

    def test_acyclic_group_keeps_shape(self):
        self.lg2.ElementList = [self.lg1]
        self.compound.Links = [self.lg2]
        self.doc.recompute()
        self.assertFalse(self.compound.Shape.isNull())
        self.assertEqual(len(self.compound.Shape.Solids), 1)
