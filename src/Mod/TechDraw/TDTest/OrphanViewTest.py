# SPDX-License-Identifier: LGPL-2.1-or-later

# Repair R21 (G04 slice 10): a view that is on no page used to crash the process in findParentPage() and in
# DrawViewClip.addView().  Source: FreeCAD's own DrawViewPyImp.cpp and DrawViewClip.cpp (the null page was never checked).

import unittest

import FreeCAD as App

from .TechDrawTestUtilities import createPageWithSVGTemplate


class OrphanViewTest(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("TDOrphan")
        self.doc.addObject("Part::Box", "Box")
        self.doc.recompute()

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def orphan(self, name="OrphanView"):
        """A part view made with addObject and never added to a page."""
        v = self.doc.addObject("TechDraw::DrawViewPart", name)
        v.Source = [self.doc.Box]
        v.Direction = App.Vector(0, 0, 1)
        self.doc.recompute()
        return v

    def test_find_parent_page_on_a_view_on_no_page_is_none(self):
        orphan = self.orphan()
        self.assertEqual(list(orphan.InList), [])
        self.assertIsNone(orphan.findParentPage())

    def test_find_parent_page_on_a_view_on_a_page_still_answers(self):
        page = createPageWithSVGTemplate(self.doc)
        v = self.orphan("OnPage")
        page.addView(v)
        self.doc.recompute()
        self.assertEqual(v.findParentPage().Name, page.Name)

    def test_a_clip_on_no_page_accepts_a_view(self):
        orphan = self.orphan()
        clip = self.doc.addObject("TechDraw::DrawViewClip", "OrphanClip")
        self.doc.recompute()
        self.assertIsNone(clip.findParentPage())
        clip.addView(orphan)
        self.assertEqual([o.Name for o in clip.Views], [orphan.Name])
        self.doc.recompute()

    def test_a_clip_on_a_page_still_takes_a_view(self):
        page = createPageWithSVGTemplate(self.doc)
        clip = self.doc.addObject("TechDraw::DrawViewClip", "PageClip")
        page.addView(clip)
        v = self.orphan("Inside")
        self.doc.recompute()
        clip.addView(v)
        self.assertEqual([o.Name for o in clip.Views], [v.Name])

    def test_a_view_on_no_page_recomputes_with_every_scale_type(self):
        orphan = self.orphan()
        for scale_type in ("Automatic", "Custom", "Page"):
            orphan.ScaleType = scale_type
            orphan.Scale = 2.0
            orphan.touch()
            self.doc.recompute()
            self.assertIsNone(orphan.findParentPage(), scale_type)

    def test_a_projection_group_on_no_page_and_its_item_do_not_crash(self):
        grp = self.doc.addObject("TechDraw::DrawProjGroup", "OrphanGroup")
        self.doc.recompute()
        item = grp.addProjection("Front")  # the console says "DPG is not on a page"; the process must survive
        self.assertIsNone(item.findParentPage())
        grp.touch()
        self.doc.recompute()

    def test_dimension_and_leader_helpers_refuse_a_view_on_no_page(self):
        # Run 100 verifier finding: makeExtentDim, makeDistanceDim and makeLeader dereferenced the null page.
        import TechDraw
        orphan = self.orphan()
        before = len(self.doc.Objects)
        calls = (
            lambda: TechDraw.makeExtentDim(orphan, [], 0),
            lambda: TechDraw.makeDistanceDim(orphan, "Distance", App.Vector(0, 0, 0), App.Vector(5, 5, 0)),
            lambda: TechDraw.makeDistanceDim3d(orphan, "Distance", App.Vector(0, 0, 0), App.Vector(5, 5, 0)),
            lambda: TechDraw.makeLeader(orphan, [App.Vector(0, 0, 0), App.Vector(10, 10, 0)], 0, 0),
        )
        for call in calls:
            with self.assertRaisesRegex(RuntimeError, "is on no page"):
                call()
        self.assertEqual(len(self.doc.Objects), before)
