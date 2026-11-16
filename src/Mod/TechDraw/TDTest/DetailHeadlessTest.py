# SPDX-License-Identifier: LGPL-2.1-or-later

# Run 102, R23: headless, a detail view used to compute once and never again, because detailExec
# fell through into its thread branch after the synchronous one and left waitingForDetail set.
# These tests change the base view, the section under it and the detail's own Radius and
# AnchorPoint, and compare each result with a detail built from scratch in the final state.
# Public sources: FreeCAD's TechDraw DrawViewDetail.cpp; Qt QFutureWatcher documentation.

import unittest

import FreeCAD
import Part

from .TechDrawTestUtilities import createPageWithSVGTemplate

V = FreeCAD.Vector

DEFAULTS = dict(direction=V(0, -1, 0), scale=1.0, radius=8.0, anchor=V(0, 0, 0), origin=V(15, 10, 5),
                section=False)


def snapshot(view):
    out = []
    for kind, edges in (("visible", view.getVisibleEdges()), ("hidden", view.getHiddenEdges())):
        for e in edges:
            out.append((kind, e.Curve.TypeId, tuple(e.firstVertex().Point), tuple(e.lastVertex().Point), e.Length))
    return out


def build(name, **kw):
    p = dict(DEFAULTS)
    p.update(kw)
    doc = FreeCAD.newDocument(name)
    shape = Part.makeBox(30, 20, 10)
    shape = shape.makeFillet(2.0, [e for e in shape.Edges if abs(e.Vertexes[0].Z - e.Vertexes[-1].Z) > 5])
    shape = shape.cut(Part.makeCylinder(3, 12, V(15, 10, -1)))
    part = doc.addObject("Part::Feature", "Part")
    part.Shape = shape
    page = createPageWithSVGTemplate(doc)
    page.Scale = 1.0
    view = doc.addObject("TechDraw::DrawViewPart", "View")
    page.addView(view)
    view.Source = [part]
    view.Direction = p["direction"]
    view.ScaleType = "Custom"
    view.Scale = p["scale"]
    base = view
    section = None
    if p["section"]:
        section = doc.addObject("TechDraw::DrawViewSection", "Section")
        page.addView(section)
        section.BaseView = view
        section.Source = [part]
        section.SectionNormal = V(0, 1, 0)
        section.SectionDirection = "Down"
        section.SectionOrigin = p["origin"]
        section.ScaleType = "Custom"
        section.Scale = p["scale"]
        base = section
    detail = doc.addObject("TechDraw::DrawViewDetail", "Detail")
    page.addView(detail)
    detail.BaseView = base
    detail.Source = [part]
    detail.AnchorPoint = p["anchor"]
    detail.Radius = p["radius"]
    detail.ScaleType = "Custom"
    detail.Scale = 2.0
    doc.recompute()
    return doc, view, section, detail


class DetailHeadlessTest(unittest.TestCase):
    def setUp(self):
        self.docs = []

    def tearDown(self):
        for d in self.docs:
            FreeCAD.closeDocument(d.Name)

    def made(self, name, **kw):
        r = build(name, **kw)
        self.docs.append(r[0])
        return r

    def testDetailExists(self):
        doc, view, section, detail = self.made("DetA")
        self.assertGreater(len(detail.getVisibleEdges()), 0)
        self.assertIn("Up-to-date", detail.State)

    def testDetailOfPlainViewFollowsItsBase(self):
        doc, view, section, detail = self.made("DetB")
        first = snapshot(detail)
        view.Direction = V(1, -1, 1)
        doc.recompute()
        moved = snapshot(detail)
        self.assertNotEqual(moved, first)
        _, _, _, ref = self.made("DetB_ref", direction=V(1, -1, 1))
        self.assertEqual(moved, snapshot(ref))
        self.assertIn("Up-to-date", detail.State)

    def testDetailKeepsItsOwnScaleWhenTheBaseScaleChanges(self):
        # a detail has its own Custom scale: the base scale change recomputes it to the same drawing
        doc, view, section, detail = self.made("DetC")
        first = snapshot(detail)
        view.Scale = 0.5
        doc.recompute()
        self.assertEqual(snapshot(detail), first)
        _, _, _, ref = self.made("DetC_ref", scale=0.5)
        self.assertEqual(snapshot(detail), snapshot(ref))

    def testDetailFollowsItsOwnRadiusAndAnchor(self):
        doc, view, section, detail = self.made("DetD")
        first = snapshot(detail)
        detail.Radius = 5.0
        doc.recompute()
        smaller = snapshot(detail)
        self.assertNotEqual(smaller, first)
        _, _, _, ref = self.made("DetD_ref1", radius=5.0)
        self.assertEqual(smaller, snapshot(ref))
        detail.AnchorPoint = V(4, 2, 0)
        doc.recompute()
        shifted = snapshot(detail)
        self.assertNotEqual(shifted, smaller)
        _, _, _, ref = self.made("DetD_ref2", radius=5.0, anchor=V(4, 2, 0))
        self.assertEqual(shifted, snapshot(ref))

    def testDetailOfSectionFollowsItsBaseAndItself(self):
        doc, view, section, detail = self.made("DetE", section=True, radius=12.0)
        first = snapshot(detail)
        self.assertGreater(len(first), 0)
        section.SectionOrigin = V(15, 10, 2)
        doc.recompute()
        moved = snapshot(detail)
        self.assertNotEqual(moved, first)
        _, _, _, ref = self.made("DetE_ref1", section=True, radius=12.0, origin=V(15, 10, 2))
        self.assertEqual(moved, snapshot(ref))
        detail.Radius = 6.0
        doc.recompute()
        smaller = snapshot(detail)
        self.assertNotEqual(smaller, moved)
        _, _, _, ref = self.made("DetE_ref2", section=True, radius=6.0, origin=V(15, 10, 2))
        self.assertEqual(smaller, snapshot(ref))
        detail.AnchorPoint = V(3, 1, 0)
        doc.recompute()
        _, _, _, ref = self.made("DetE_ref3", section=True, radius=6.0, origin=V(15, 10, 2), anchor=V(3, 1, 0))
        self.assertEqual(snapshot(detail), snapshot(ref))


if __name__ == "__main__":
    unittest.main()
