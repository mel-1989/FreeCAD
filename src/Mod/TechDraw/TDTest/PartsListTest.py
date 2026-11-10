# SPDX-License-Identifier: LGPL-2.1-or-later

# G04 slice 2a: the associative parts list (PartsList.make), headless.
# Run with: FreeCADCmd -t TestTechDrawApp.PartsListTest   (part of TestTechDrawApp)
#
# Expected rows are written out by hand from the case definitions (assembly structure), never read from the BOM.
# Conventions: ISO 7573 (parts lists, public catalogue listing), ISO 6433 (part references, public listing).

import math
import os
import re
import tempfile
import unittest

import FreeCAD as App

try:
    from AssemblyTests import configuration_corpus as C
except Exception:  # the Assembly module is not built
    C = None

import PartsList as PL

ISO_A3 = os.path.join("ISO", "A3_Landscape_ISO5457_advanced.svg")
DEFAULT = "Default_Template_A4_Landscape.svg"


def cells(view):
    """The drawn text cells of a DrawViewSpreadsheet symbol as rows (grouped by baseline y, ordered by x)."""
    texts = re.findall(r'<text[^>]*\sx="([-\d.]+)"\sy="([-\d.]+)"[^>]*>([^<]*)</text>', view.Symbol)
    rows = {}
    for x, y, t in texts:
        rows.setdefault(float(y), []).append((float(x), t))
    return [[t for _, t in sorted(r)] for _, r in sorted(rows.items())]


def sheet_rows(sheet):
    out = []
    for r in range(1, 200):
        if not sheet.getContents("A%d" % r):
            break
        out.append([str(sheet.get("%s%d" % (c, r))) for c in "ABC"])
    return out


def table_box(view):
    """Drawn table as (x0, y0, x1, y1) in page mm: the symbol's size about the view's X, Y (its centre)."""
    sym = view.Symbol
    w = max(float(x) + float(wd) for x, wd in re.findall(r'<rect x="([-\d.]+)" y="[-\d.]+" width="([-\d.]+)"', sym))
    h = max(float(y) + float(ht) for y, ht in re.findall(r'<rect x="[-\d.]+" y="([-\d.]+)" width="[-\d.]+" height="([-\d.]+)"', sym))
    w, h = w * PL.PX_MM, h * PL.PX_MM
    x, y = view.X.Value, view.Y.Value
    return (x - w / 2, y - h / 2, x + w / 2, y + h / 2)


@unittest.skipIf(C is None, "Assembly module not available")
class PartsListTest(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("PartsListTest")
        self.doc.UndoMode = 1

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def page(self, template=DEFAULT):
        page = self.doc.addObject("TechDraw::DrawPage", "Page")
        t = self.doc.addObject("TechDraw::DrawSVGTemplate", "Template")
        t.Template = os.path.join(App.getResourceDir(), "Mod", "TechDraw", "Templates", template)
        page.Template = t
        return page

    def case(self, name):
        b = C.build_case(self.doc, name)
        self.doc.recompute()
        return b

    def assertTable(self, view, rows):
        self.assertEqual(sheet_rows(view.Source), rows)
        self.assertEqual(cells(view), rows)

    # ---- rows, drawn text

    def test_rows_and_drawn_text(self):
        b = self.case("sub_assembly_child")
        view = PL.make(self.page(), b.asm)
        self.doc.recompute()
        self.assertTable(
            view,
            [["Index", "Name", "Quantity"], ["1", "PlateSrc", "1"], ["2", "Sub", "1"], ["2.1", "CubeSrc", "2"]],
        )

    def test_no_apostrophe_in_drawn_text(self):
        b = self.case("sub_assembly_child")
        view = PL.make(self.page(), b.asm)
        self.doc.recompute()
        self.assertNotIn("'", "".join(t for r in cells(view) for t in r))

    def test_link_array_is_counted_in_the_table(self):
        b = self.case("array_element")
        view = PL.make(self.page(), b.asm)
        self.doc.recompute()
        self.assertTable(view, [["Index", "Name", "Quantity"], ["1", "PlateSrc", "1"], ["2", "CubeSrc", "3"]])

    def test_part_used_at_top_level_and_in_a_subassembly(self):
        b = self.case("sub_assembly_child")
        for n in ("T1", "T2"):
            lk = b.asm.newObject("App::Link", n)
            lk.LinkedObject = self.doc.getObject("CubeSrc")
        view = PL.make(self.page(), b.asm)
        self.doc.recompute()
        self.assertTable(
            view,
            [
                ["Index", "Name", "Quantity"],
                ["1", "PlateSrc", "1"],
                ["2", "Sub", "1"],
                ["2.1", "CubeSrc", "2"],
                ["3", "CubeSrc", "2"],
            ],
        )

    # ---- associativity: one plain recompute after each edit

    def test_plain_recompute_follows_edits_and_configurations(self):
        b = self.case("sub_assembly_child")
        view = PL.make(self.page(), b.asm)
        self.doc.recompute()
        head = ["Index", "Name", "Quantity"]
        lk = b.asm.newObject("App::Link", "Extra")
        lk.LinkedObject = self.doc.getObject("PlateSrc")
        self.doc.recompute()
        self.assertTable(view, [head, ["1", "PlateSrc", "2"], ["2", "Sub", "1"], ["2.1", "CubeSrc", "2"]])
        self.doc.getObject("PlateSrc").Label = "Base"
        self.doc.recompute()
        self.assertTable(view, [head, ["1", "Base", "2"], ["2", "Sub", "1"], ["2.1", "CubeSrc", "2"]])
        b.asm.removeObject(lk)
        self.doc.removeObject(lk.Name)
        self.doc.recompute()
        self.assertTable(view, [head, ["1", "Base", "1"], ["2", "Sub", "1"], ["2.1", "CubeSrc", "2"]])
        import AssemblyConfiguration as AC

        AC.create(b.asm, "NoSL2", suppress=["Sub.SL2"])
        AC.activate(b.asm, "NoSL2")
        self.doc.recompute()
        self.assertTable(view, [head, ["1", "Base", "1"], ["2", "Sub", "1"], ["2.1", "CubeSrc", "1"]])
        AC.activate(b.asm, None)
        self.doc.recompute()
        self.assertTable(view, [head, ["1", "Base", "1"], ["2", "Sub", "1"], ["2.1", "CubeSrc", "2"]])

    def test_drawn_table_grows_with_the_assembly(self):
        b = self.case("sub_assembly_child")
        view = PL.make(self.page(ISO_A3), b.asm)
        self.doc.recompute()
        before = table_box(view)
        for i in range(4):
            lk = b.asm.newObject("App::Link", "More%d" % i)
            lk.LinkedObject = self.doc.getObject("PlateSrc") if i == 0 else self.doc.addObject("Part::Box", "Extra%d" % i)
        self.doc.recompute()
        after = table_box(view)
        self.assertGreater(after[3] - after[1], before[3] - before[1])
        self.assertAlmostEqual(after[2], before[2], delta=0.5)  # still anchored at the right
        self.assertAlmostEqual(after[1], before[1], delta=0.5)  # and at the bottom

    # ---- layout

    def test_column_widths_fit_the_text(self):
        b = self.case("sub_assembly_child")
        long = self.doc.getObject("PlateSrc")
        long.Label = "A rather long part name for the fit"
        view = PL.make(self.page(), b.asm)
        self.doc.recompute()
        sheet = view.Source
        for c, col in enumerate("ABC"):
            need = max(PL.text_width_px(row[c]) for row in cells(view)) + 6.0 + 6.0  # the view's own 6 units each side
            w = sheet.getColumnWidth(col)
            self.assertGreaterEqual(w, need, "column %s is narrower than its text" % col)
            self.assertLessEqual(w, need * 1.15 + 6, "column %s is far wider than its text" % col)
        # and the drawn rects carry those widths
        drawn = sorted(set(float(w) for w in re.findall(r'<rect x="[-\d.]+" y="0" width="([-\d.]+)"', view.Symbol)))
        self.assertEqual(drawn, sorted(set(float(sheet.getColumnWidth(c)) for c in "ABC")))

    def test_widths_follow_a_longer_name_after_a_plain_recompute(self):
        b = self.case("sub_assembly_child")
        view = PL.make(self.page(), b.asm)
        self.doc.recompute()
        w0 = view.Source.getColumnWidth("B")
        self.doc.getObject("CubeSrc").Label = "Cube with a much longer label than before"
        self.doc.recompute()
        self.assertGreater(view.Source.getColumnWidth("B"), w0)
        self.assertGreaterEqual(
            view.Source.getColumnWidth("B"),
            PL.text_width_px("Cube with a much longer label than before") + 12.0,
        )

    def test_text_is_at_least_2_5_mm(self):
        b = self.case("sub_assembly_child")
        view = PL.make(self.page(), b.asm)
        size_mm = view.TextSize * PL.PX_MM
        self.assertGreaterEqual(size_mm * 0.72, 2.5)  # a digit is about 0.72 em high in osifont
        self.assertGreaterEqual(float(re.search(r'font-size="([\d.]+)"', view.Symbol).group(1)) * PL.PX_MM * 0.72, 2.5)

    def test_corner_in_a_blank_template(self):
        b = self.case("three_configs")
        page = self.page()
        view = PL.make(page, b.asm)
        self.doc.recompute()
        x0, y0, x1, y1 = table_box(view)
        W, H = page.Template.Width.Value, page.Template.Height.Value
        self.assertAlmostEqual(x1, W - PL.MARGIN_MM, delta=1.0)
        self.assertAlmostEqual(y0, PL.MARGIN_MM, delta=1.0)
        self.assertTrue(x0 > 0 and y1 < H)

    def test_corner_above_the_title_block(self):
        b = self.case("three_configs")
        page = self.page(ISO_A3)
        view = PL.make(page, b.asm)
        self.doc.recompute()
        area, title = PL.page_areas(page)
        self.assertIsNotNone(title)
        x0, y0, x1, y1 = table_box(view)
        self.assertLessEqual(x1, area[2] - PL.MARGIN_MM + 1.0)
        self.assertGreaterEqual(x1, area[2] - PL.MARGIN_MM - 1.0)
        self.assertGreaterEqual(y0, title[3] + PL.MARGIN_MM - 1.0)  # above the title block
        self.assertLessEqual(y0, title[3] + PL.MARGIN_MM + 1.0)  # and as low as it may stand
        self.assertTrue(x0 >= area[0] and y1 <= area[3])

    # ---- persistence, undo, repetition

    def test_save_and_reopen_keep_the_table(self):
        b = self.case("sub_assembly_child")
        view = PL.make(self.page(ISO_A3), b.asm)
        self.doc.recompute()
        name, sym, pos = view.Name, view.Symbol, (view.X.Value, view.Y.Value)
        path = os.path.join(tempfile.mkdtemp(), "pl.FCStd")
        self.doc.saveAs(path)
        App.closeDocument(self.doc.Name)
        self.doc = App.openDocument(path)
        v2 = self.doc.getObject(name)
        self.assertEqual(v2.Symbol, sym)
        self.assertEqual((v2.X.Value, v2.Y.Value), pos)
        self.doc.recompute()
        self.assertEqual(v2.Symbol, sym)
        # and it is still associative after the reopen
        lk = self.doc.getObject("Assembly").newObject("App::Link", "Late")
        lk.LinkedObject = self.doc.getObject("CubeSrc")
        self.doc.recompute()
        self.assertEqual(cells(v2)[-1], ["3", "CubeSrc", "1"])

    def test_make_is_one_transaction_and_undo_leaves_nothing(self):
        b = self.case("sub_assembly_child")
        page = self.page()
        before = {o.Name for o in self.doc.Objects}
        n = self.doc.UndoCount
        PL.make(page, b.asm)
        self.assertEqual(self.doc.UndoCount, n + 1)
        self.doc.undo()
        self.assertEqual({o.Name for o in self.doc.Objects}, before)
        self.assertEqual(len(page.Views), 0)

    def test_make_twice_returns_the_same_view(self):
        b = self.case("three_configs")
        page = self.page()
        v1 = PL.make(page, b.asm)
        v2 = PL.make(page, b.asm)
        self.assertEqual(v1.Name, v2.Name)
        self.assertEqual(len([v for v in page.Views if v.TypeId == "TechDraw::DrawViewSpreadsheet"]), 1)

    def test_uses_the_existing_bom_and_extra_columns(self):
        b = self.case("sub_assembly_child")
        bom = self.doc.getObject("BOM")
        view = PL.make(self.page(), b.asm, columns=[".Label"])
        self.doc.recompute()
        self.assertEqual(view.Source, bom)
        self.assertEqual(list(bom.columnsNames), ["Index", "Name", "Quantity", ".Label"])
        self.assertEqual(view.CellEnd, "D4")

    # ---- several parts lists on a page, a changed template, a table too tall for the sheet

    def second_assembly(self):
        """A second assembly in the same document (the case builder numbers its objects afresh)."""
        before = {o.Name for o in self.doc.Objects}
        b = C.build_case(self.doc, "sub_assembly_child")
        self.doc.recompute()
        self.assertTrue(any(o.Name not in before and o.TypeId == "Assembly::AssemblyObject" for o in self.doc.Objects))
        return b

    def assembly_of(self, view):
        return view.Source.getParentGeoFeatureGroup().getParentGeoFeatureGroup() or view.Source.getParentGeoFeatureGroup()

    def assertNoOverlap(self, boxes):
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                apart = a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]
                self.assertTrue(apart, "tables overlap: %s and %s" % (a, b))

    def test_two_parts_lists_stack_above_each_other(self):
        b1 = self.case("three_configs")
        page = self.page(ISO_A3)
        v1 = PL.make(page, b1.asm)
        b2 = self.second_assembly()
        self.assertNotEqual(b1.asm.Name, b2.asm.Name)
        v2 = PL.make(page, b2.asm)
        self.doc.recompute()
        self.assertNotEqual(v1.Name, v2.Name)
        self.assertGreater(len(sheet_rows(v2.Source)), 1)
        area, title = PL.page_areas(page)
        box1, box2 = table_box(v1), table_box(v2)
        self.assertNoOverlap([box1, box2])
        self.assertGreaterEqual(box2[1], box1[3] + PL.GAP_MM - 1.0)  # the later table stands above the earlier one
        self.assertGreaterEqual(box1[1], title[3] + PL.MARGIN_MM - 1.0)  # both clear of the title block
        for bx in (box1, box2):
            self.assertTrue(bx[0] >= area[0] and bx[2] <= area[2] and bx[3] <= area[3])

    def test_stacked_tables_stay_apart_when_the_lower_one_grows(self):
        b1 = self.case("three_configs")
        page = self.page(ISO_A3)
        v1 = PL.make(page, b1.asm)
        v2 = PL.make(page, self.second_assembly().asm)
        self.doc.recompute()
        h_before = table_box(v1)[3] - table_box(v1)[1]
        for i in range(3):
            src = self.doc.addObject("Part::Box", "GrowSrc%d" % i)
            lk = b1.asm.newObject("App::Link", "Grow%d" % i)
            lk.LinkedObject = src
        self.doc.recompute()
        box1, box2 = table_box(v1), table_box(v2)
        self.assertGreater(box1[3] - box1[1], h_before + 3 * PL.ROW_PX * PL.PX_MM - 1.0)  # the lower table grew
        self.assertNoOverlap([box1, box2])
        self.assertGreaterEqual(box2[1], box1[3] + PL.GAP_MM - 1.0)

    def test_template_change_is_picked_up_on_the_next_recompute(self):
        b = self.case("three_configs")
        page = self.page()  # blank default template: no title block
        view = PL.make(page, b.asm)
        self.doc.recompute()
        self.assertAlmostEqual(table_box(view)[1], PL.MARGIN_MM, delta=1.0)
        page.Template.Template = os.path.join(App.getResourceDir(), "Mod", "TechDraw", "Templates", ISO_A3)
        self.doc.recompute()
        area, title = PL.page_areas(page)
        self.assertIsNotNone(title)
        x0, y0, x1, y1 = table_box(view)
        self.assertGreaterEqual(y0, title[3] + PL.MARGIN_MM - 1.0)
        self.assertAlmostEqual(x1, area[2] - PL.MARGIN_MM, delta=1.0)

    def test_table_taller_than_the_drawing_area_warns(self):
        b = self.case("sub_assembly_child")
        for i in range(40):
            src = self.doc.addObject("Part::Box", "TallSrc%d" % i)
            lk = b.asm.newObject("App::Link", "Tall%d" % i)
            lk.LinkedObject = src
        self.doc.recompute()
        page = self.page()
        seen = []
        real = App.Console.PrintWarning
        App.Console.PrintWarning = lambda msg: seen.append(msg)
        try:
            view = PL.make(page, b.asm)
            self.doc.recompute()
        finally:
            App.Console.PrintWarning = real
        area, _ = PL.page_areas(page)
        self.assertGreater(table_box(view)[3], area[3] - PL.MARGIN_MM)  # it really is too tall
        self.assertTrue(any("PartsList" in m and "does not fit" in m for m in seen), seen)

    def test_a_table_that_fits_does_not_warn(self):
        b = self.case("three_configs")
        seen = []
        real = App.Console.PrintWarning
        App.Console.PrintWarning = lambda msg: seen.append(msg)
        try:
            PL.make(self.page(ISO_A3), b.asm)
            self.doc.recompute()
        finally:
            App.Console.PrintWarning = real
        self.assertFalse([m for m in seen if "PartsList" in m], seen)

    # ---- 2a repairs (run 087 R1 to R3): damage and layout changes heal on the next recompute

    def untouched(self):
        return [o.Name for o in self.doc.Objects if "Touched" in o.State or "Invalid" in o.State]

    def test_r1_deleted_bom_is_rebuilt_by_make(self):
        b = self.case("sub_assembly_child")
        page = self.page(ISO_A3)
        view = PL.make(page, b.asm)
        self.doc.recompute()
        self.doc.removeObject(view.Source.Name)
        self.doc.recompute()
        again = PL.make(page, b.asm)
        self.doc.recompute()
        self.assertEqual(again.Name, view.Name)  # repaired in place, no second table
        self.assertEqual(len(PL.views_on(page)), 1)
        self.assertEqual(sheet_rows(again.Source)[0], ["Index", "Name", "Quantity"])
        self.assertTable(again, sheet_rows(again.Source))
        self.assertEqual(len(sheet_rows(again.Source)), 4)  # header + 3 rows of sub_assembly_child
        self.assertEqual(self.untouched(), [])

    def test_r1_deleted_fit_helper_is_rebuilt_by_refresh(self):
        b = self.case("three_configs")
        page = self.page(ISO_A3)
        view = PL.make(page, b.asm)
        self.doc.recompute()
        self.doc.removeObject(PL.fit_of(view).Name)
        self.doc.recompute()
        PL.refresh(page)
        self.assertEqual(len(PL.views_on(page)), 1)
        self.assertIsNotNone(PL.fit_of(view))
        self.assertTable(view, sheet_rows(view.Source))
        area, title = PL.page_areas(page)
        self.assertAlmostEqual(table_box(view)[2], area[2] - PL.MARGIN_MM, delta=1.0)
        # and it follows the assembly again with a plain recompute
        lk = b.asm.newObject("App::Link", "Extra")
        lk.LinkedObject = self.doc.addObject("Part::Box", "ExtraSrc")
        self.doc.recompute()
        self.assertEqual(len(cells(view)), len(sheet_rows(view.Source)))
        self.assertIn("ExtraSrc", [r[1] for r in cells(view)])

    def test_r1_deleted_bom_and_helper_together(self):
        b = self.case("three_configs")
        page = self.page(ISO_A3)
        view = PL.make(page, b.asm)
        self.doc.recompute()
        bom, fit = view.Source, PL.fit_of(view)
        self.doc.removeObject(fit.Name)
        self.doc.removeObject(bom.Name)
        PL.refresh(page)
        self.assertEqual(len(PL.views_on(page)), 1)
        self.assertTable(view, sheet_rows(view.Source))

    def test_r2_new_template_object_moves_the_table_in_the_same_recompute(self):
        b = self.case("three_configs")
        page = self.page()  # blank default: no title block
        view = PL.make(page, b.asm)
        self.doc.recompute()
        t2 = self.doc.addObject("TechDraw::DrawSVGTemplate", "Template2")
        t2.Template = os.path.join(App.getResourceDir(), "Mod", "TechDraw", "Templates", ISO_A3)
        page.Template = t2
        self.doc.recompute()
        area, title = PL.page_areas(page)
        self.assertIsNotNone(title)
        x0, y0, x1, y1 = table_box(view)
        self.assertGreaterEqual(y0, title[3] + PL.MARGIN_MM - 1.0)
        self.assertAlmostEqual(x1, area[2] - PL.MARGIN_MM, delta=1.0)
        self.assertEqual(self.untouched(), [])

    def test_r3_deleting_the_middle_table_closes_the_gap(self):
        b1 = self.case("three_configs")
        page = self.page(ISO_A3)
        v1 = PL.make(page, b1.asm)
        v2 = PL.make(page, self.second_assembly().asm)
        v3 = PL.make(page, self.second_assembly().asm)
        self.doc.recompute()
        self.assertNoOverlap([table_box(v1), table_box(v2), table_box(v3)])
        high = table_box(v3)[1]
        page.removeView(v2)
        self.doc.removeObject(v2.Name)
        self.doc.recompute()
        box1, box3 = table_box(v1), table_box(v3)
        self.assertLess(table_box(v3)[1], high - 1.0)
        self.assertAlmostEqual(box3[1], box1[3] + PL.GAP_MM, delta=1.0)
        self.assertEqual(self.untouched(), [])
