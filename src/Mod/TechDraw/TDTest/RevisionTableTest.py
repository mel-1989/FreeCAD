# SPDX-License-Identifier: LGPL-2.1-or-later

# G04 slice 3a: the revision table (RevisionTable.make / add / from_pdm), headless.
# Run with: FreeCADCmd -t TestTechDrawApp.RevisionTableTest   (part of TestTechDrawApp)
#
# Conventions: ASME Y14.35 catalogue listings and a public NDIA training deck on it (columns revision, description, date,
# approval, zone); ISO 7200 revision index and date of issue fields as carried by the bundled templates.

import os
import re
import tempfile
import time
import unittest

import FreeCAD as App

import HoleTable as HT
import PartsList as PL
import RevisionTable as RT

T = os.path.join(App.getResourceDir(), "Mod", "TechDraw", "Templates")


def cells(view):
    texts = re.findall(r'<text[^>]*\sx="([-\d.]+)"\sy="([-\d.]+)"[^>]*>([^<]*)</text>', view.Symbol)
    rows = {}
    for x, y, t in texts:
        rows.setdefault(float(y), []).append((float(x), t))
    return [[t for _, t in sorted(r)] for _, r in sorted(rows.items())]


class FakeVault:
    """Anything with ``history(name)`` newest first, as PDM.vault.Vault gives it."""

    def __init__(self, recs):
        self.recs = recs

    def history(self, name):
        return list(self.recs)


class RevisionTableTest(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("RevisionTableTest")
        self.doc.UndoMode = 1

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def page(self, template):
        page = self.doc.addObject("TechDraw::DrawPage", "Page")
        t = self.doc.addObject("TechDraw::DrawSVGTemplate", "Template")
        t.Template = os.path.join(T, template)
        page.Template = t
        self.doc.recompute()
        return page

    def untouched(self):
        return [o.Name for o in self.doc.Objects if "Touched" in o.State or "Invalid" in o.State]

    def rows(self, page):
        return RT.read_rows(RT.drivers_on(page)[0].Sheet)

    def test_add_rows_template_fields_and_drawn_text(self):
        for tpl, keys in (
            (os.path.join("ISO", "A4_Landscape_ISO5457_minimal.svg"), ("revision_index", "date_of_issue")),
            (os.path.join("ISO", "titleblocks", "ISO7200_titleblock_3_advanced.svg"), ("revision_index", "date_of_issue")),
            (os.path.join("ASME", "ANSIA_Landscape.svg"), ("revision_index",)),
        ):
            page = self.page(tpl)
            if not os.path.exists(os.path.join(T, tpl)):
                continue
            RT.make(page)
            RT.add(page, "A", "First release", "2026-01-02", "MS")
            RT.add(page, "B", "Hole moved", "2026-02-03", "MS", zone="B3")
            view = RT.add(page, "C", "Fillet added", "2026-03-04", "AB")
            texts = page.Template.EditableTexts
            self.assertEqual(texts["revision_index"], "C", tpl)
            if "date_of_issue" in keys:
                self.assertEqual(texts["date_of_issue"], "2026-03-04", tpl)
            want = [
                ["ZONE", "REV", "DESCRIPTION", "DATE", "APPROVED"],
                ["", "A", "First release", "2026-01-02", "MS"],
                ["B3", "B", "Hole moved", "2026-02-03", "MS"],
                ["", "C", "Fillet added", "2026-03-04", "AB"],
            ]
            self.assertEqual(self.rows(page), want, tpl)
            self.assertEqual([r for r in cells(view) if r], want, tpl)
            self.assertEqual(self.untouched(), [], tpl)
            if RT.warning_of(page):  # a title-block-only template is a page smaller than the table
                continue
            area, title = PL.page_areas(page)
            b = HT.sheet_view_box(view)
            self.assertTrue(b[0] >= area[0] and b[2] <= area[2] and b[1] >= area[1] and b[3] <= area[3], tpl)
            self.assertGreater(0.5 * (b[0] + b[2]), 0.5 * (area[0] + area[2]), "top right")
            self.assertGreater(0.5 * (b[1] + b[3]), 0.5 * (area[1] + area[3]), "top right")
            if title:
                self.assertFalse(HT.overlap(b, tuple(title)))

    def test_blank_template_is_left_untouched(self):
        page = self.page(os.path.join("ISO", "A4_Landscape_blank.svg"))
        before = dict(page.Template.EditableTexts)
        RT.make(page)
        RT.add(page, "A", "First", "2026-01-02", "MS")
        self.assertEqual(dict(page.Template.EditableTexts), before)

    def test_editing_a_rev_in_the_sheet_updates_the_template(self):
        page = self.page(os.path.join("ISO", "A4_Landscape_ISO5457_minimal.svg"))
        RT.add(page, "A", "First", "2026-01-02", "MS")
        RT.add(page, "B", "Second", "2026-02-03", "MS")
        sheet = RT.drivers_on(page)[0].Sheet
        sheet.set("B3", "'D")
        sheet.set("D3", "'2026-09-09")
        self.doc.recompute()
        self.assertEqual(page.Template.EditableTexts["revision_index"], "D")
        self.assertEqual(page.Template.EditableTexts["date_of_issue"], "2026-09-09")
        self.assertEqual(self.untouched(), [])

    def test_from_pdm_adds_released_revisions_once(self):
        page = self.page(os.path.join("ISO", "A4_Landscape_ISO5457_minimal.svg"))
        recs = [
            {"revision": "B.2", "released": False, "message": "wip", "time": "2026-05-01T10:00:00", "author": "ann"},
            {"revision": "B.1", "released": True, "message": "Second release\nlonger text", "time": "2026-04-01T10:00:00", "author": "bob"},
            {"revision": "A.1", "released": True, "message": "First release", "time": "2026-03-01T10:00:00", "author": "ann"},
            {"revision": "A.1", "released": True, "message": "older record of A.1", "time": "2026-02-01T10:00:00", "author": "ann"},
        ]
        RT.add(page, "X", "by hand", "2026-01-01", "MS")
        RT.from_pdm(page, "bracket", FakeVault(recs))
        RT.from_pdm(page, "bracket", FakeVault(recs))
        self.assertEqual(
            self.rows(page)[1:],
            [["", "X", "by hand", "2026-01-01", "MS"], ["", "A.1", "First release", "2026-03-01", "ann"], ["", "B.1", "Second release", "2026-04-01", "bob"]],
        )
        self.assertEqual(page.Template.EditableTexts["revision_index"], "B.1")

    def test_undo_and_persistence(self):
        page = self.page(os.path.join("ISO", "A4_Landscape_ISO5457_minimal.svg"))
        before = {o.Name for o in self.doc.Objects}
        RT.add(page, "A", "First", "2026-01-02", "MS")
        self.doc.undo()
        self.assertEqual({o.Name for o in self.doc.Objects}, before)
        self.doc.redo()
        RT.add(page, "B", "Second", "2026-02-03", "MS")
        path = os.path.join(tempfile.mkdtemp(), "rev.FCStd")
        self.doc.saveAs(path)
        App.closeDocument(self.doc.Name)
        self.doc = App.openDocument(path)
        page = [o for o in self.doc.Objects if o.TypeId == "TechDraw::DrawPage"][0]
        self.doc.recompute()
        self.assertEqual(len(self.rows(page)), 3)
        sheet = RT.drivers_on(page)[0].Sheet
        sheet.set("B3", "'Z")
        self.doc.recompute()
        self.assertEqual(page.Template.EditableTexts["revision_index"], "Z")

    def test_moved_table_keeps_its_place_and_add_is_fast(self):
        page = self.page(os.path.join("ISO", "A4_Landscape_ISO5457_minimal.svg"))
        v = RT.make(page)
        v.X, v.Y = 150.0, 100.0
        t0 = time.time()
        RT.add(page, "A", "First", "2026-01-02", "MS")
        self.assertLess(time.time() - t0, 0.5)
        self.assertAlmostEqual(v.X.Value, 150.0, delta=1e-6)
        self.assertAlmostEqual(v.Y.Value, 100.0, delta=1e-6)
