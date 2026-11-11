# SPDX-License-Identifier: LGPL-2.1-or-later

# G04 slice 6: sheet sets and title-block propagation (SheetSet.make, add_sheet, remove_sheet, move_sheet, unpin), headless.
# Run with: FreeCADCmd -t TestTechDrawApp.SheetSetTest   (part of TestTechDrawApp)
#
# Expected values are written out by hand from the corpus (which key each template family uses for which field, the sheet order the
# test built, the densities of the materials it chose), never read from TitleBlockKeys.json or SheetSet's own helpers.
# Public sources: the bundled templates' field names (ISO 7200 data fields), FreeCAD's autofill code for the scale text.

import datetime
import os
import tempfile
import time
import types
import unittest

import FreeCAD as App
import Part  # noqa: F401

import RevisionTable as RT
import SheetSet as SS

TPL = os.path.join(App.getResourceDir(), "Mod", "TechDraw", "Templates")

# family -> template, combined sheet key and its text, split keys, then the key each canonical field has (from reading the SVGs)
FAM = {
    "iso": dict(t="ISO/A4_Landscape_ISO5457_minimal.svg", sheet=("sheet_number", "%d / %d"), title="title", dn="drawing_number", author="creator",
                checker="approval_person", date="date_of_issue", company="legal_owner_1", material="part_material", doctype="document_type",
                tol="general_tolerances", rev="revision_index", scale="scale"),
    "de": dict(t="ISO/localized/de/A4_Landscape_ISO7200_DE.svg", sheet=("page_number", "%d/%d"), title="drawing_title", dn="Sachnummer",
               author="author_name", checker="Genehmiger", date="fc-date", company="owner_1", doctype="document_type", scale="fc-sc"),
    "gost": dict(t="ISO/localized/ru/Leading/Landscape_A4_NotInGOST.svg", split=("Лист", "Листов"), title="Название", dn="Номер", author="Разработал",
                 date="Дата_разработки", company="Организация1", material="Материал1", weight="Масса", scale="Масштаб"),
    "cn": dict(t="ISO/localized/zh-CN/landscape/A4_Landscape_CN_CLIP.svg", split=("OF_SHEET", "TOTAL_SHEETS"), title="PARTNAME", dn="PART_CODE_NAME",
               author="AUTHOR_NAME", date="DATE-1", company="COMPANYNAME", material="MATERIALMARK", weight="WEIGHT", scale="scale", rev="VERSION"),
    "ansi": dict(t="ASME/ANSIA_Landscape.svg", sheet=("Sheet", "%d / %d"), title="DrawingTitle1", dn="drawing_number", author="DrawnBy",
                 checker="CheckedBy", company="CompanyName", weight="Weight", scale="scale", rev="revision_index"),
    "arch": dict(t="ASME/Arch_A_Landscape.svg", sheet=("SheetNumber", "%d / %d"), title="Title", dn="drawing_number", author="AuthorName",
                 date="CreationDate", company="Company_name", weight="Weight", scale="scale", checker="SupervisorName"),
    "fi": dict(t="ISO/localized/fi/A4_Landscape_SFS_ISO.svg", sheet=("FC-Page", "%d / %d"), title="FC-Title", dn="FC-DrawingNumber", author="FC-Author",
               checker="FC-Checker", date="FC-Date", company="FC-Organisation", material="FC-Material", weight="FC-Mass", doctype="FC-DocumentType",
               scale="FC-Scale", rev="FC-Revision"),
    "none": dict(t="ASME/ANSIA_Landscape_blank.svg"),
}
STEEL, AL7075, COPPER, IRON = (
    ("90bbd8ef-8623-4d78-b3bf-e0bdb9b74dd3", "Steel-Generic", 7.9e-06),
    ("9e53ebad-3e8a-464e-8c6d-1540a08ce5a6", "Aluminum-7075-T6", 2.81e-06),
    ("6c03899d-496e-4c60-a41b-3d66f2337fb9", "Copper-Generic", 8.96e-06),
    ("1826c364-d26a-43fb-8f61-288281236836", "Iron-Generic", 7.874e-06),
)


def expect(fam, i, n, **v):
    """The keys the family's template must show for sheet ``i`` of ``n``, from the corpus values ``v`` (canonical name -> text)."""
    f = FAM[fam]
    out = {}
    if "sheet" in f:
        out[f["sheet"][0]] = f["sheet"][1] % (i, n)
    if "split" in f:
        out[f["split"][0]] = str(i)
        out[f["split"][1]] = str(n)
    for c in ("title", "dn", "author", "checker", "date", "company", "material", "weight", "doctype", "tol", "rev", "scale"):
        if c in f and v.get(c) is not None:
            out[f[c]] = v[c]
    return out


def weight_text(dens, size):
    return "%.3f kg" % (size[0] * size[1] * size[2] * dens)


class Corpus:
    """One document of the corpus: a source box, pages of the given families, and the set."""

    def __init__(self, fams, source=True, material=STEEL, size=(100, 50, 10), fields=None, labels=None, title="Bracket"):
        self.doc = App.newDocument("SheetSetTest")
        self.doc.UndoMode = 1
        self.doc.Label = title
        self.doc.CreatedBy = "Max"
        self.doc.Company = "ACME"
        self.box = None
        if source:
            import Materials

            self.box = self.doc.addObject("Part::Box", "Box")
            self.box.Length, self.box.Width, self.box.Height = size
            if material is not None:  # None: the object's default material, named "Default"
                self.box.ShapeMaterial = Materials.MaterialManager().getMaterial(material[0])
            self.doc.recompute()
        self.fam_of = {}
        self.pages = [self.page(f, labels[i] if labels else None) for i, f in enumerate(fams)]
        self.doc.recompute()
        self.before = {p.Name: dict(p.Template.EditableTexts) for p in self.pages}  # as stock left them
        self.set = SS.make(self.doc, pages=self.pages, source=self.box, fields=fields)

    def page(self, fam, label=None):
        page = self.doc.addObject("TechDraw::DrawPage", "Page")
        t = self.doc.addObject("TechDraw::DrawSVGTemplate", "Template")
        page.Template = t
        t.Template = os.path.join(TPL, FAM[fam]["t"])
        if label:
            page.Label = label
        self.fam_of[page.Name] = fam
        return page

    def texts(self, page):
        return dict(page.Template.EditableTexts)

    def untouched(self):
        return [o.Name for o in self.doc.Objects if "Touched" in o.State or "Invalid" in o.State]

    def close(self):
        App.closeDocument(self.doc.Name)


class SheetSetTest(unittest.TestCase):
    def setUp(self):
        self.made = []

    def tearDown(self):
        for m in self.made:
            try:
                m.close()
            except Exception:
                pass

    def corpus(self, *a, **k):
        m = Corpus(*a, **k)
        self.made.append(m)
        return m

    def add(self, m, fam, **k):
        page = SS.add_sheet(m.set, **k)
        m.fam_of[page.Name] = fam
        return page

    def assertSheets(self, m, vals, msg=""):
        """Every sheet of the set shows exactly the corpus value for each key its family has, in set order, and nothing is Touched."""
        pages = list(m.set.Pages)
        for i, page in enumerate(pages, start=1):
            fam = m.fam_of[page.Name]
            want = expect(fam, i, len(pages), **vals(page))
            got = m.texts(page)
            for k, v in want.items():
                self.assertEqual(got.get(k), v, "%s sheet %d (%s) key %s" % (msg, i, fam, k))
        self.assertEqual(m.untouched(), [], msg)

    # -- check 70: numbering ----------------------------------------------------------------

    def test_numbering_over_families_and_edit_sequences(self):
        none = lambda page: {}
        for fam in ("iso", "de", "gost", "cn", "ansi", "arch", "fi"):
            m = self.corpus([fam] * 2, labels=["Page 10", "Page 2"])  # labels that collate out of order
            self.assertSheets(m, none, fam + " start")
            p3 = self.add(m, fam, label="Page 3")
            self.assertSheets(m, none, fam + " add")
            p4 = self.add(m, fam, label="Alpha")
            self.assertSheets(m, none, fam + " add 2")
            SS.move_sheet(m.set, p4, 0)  # the set's order rules, not the labels' collation
            self.assertEqual([p.Label for p in m.set.Pages][0], "Alpha")
            self.assertSheets(m, none, fam + " move")
            SS.move_sheet(m.set, m.pages[0], 3)
            self.assertEqual(m.set.Pages[3], m.pages[0])
            self.assertSheets(m, none, fam + " move 2")
            SS.remove_sheet(m.set, m.pages[1])
            self.assertEqual(len(m.set.Pages), 3)
            self.assertSheets(m, none, fam + " remove")
            SS.remove_sheet(m.set, p3)
            SS.remove_sheet(m.set, p4)
            self.assertEqual(len(m.set.Pages), 1)
            self.assertSheets(m, none, fam + " down to one")

    def test_numbering_follows_pages_order_not_label_collation(self):
        m = self.corpus(["iso"] * 3, labels=["Page 2", "Page 10", "Alpha"])
        self.assertEqual([m.texts(p)["sheet_number"] for p in m.pages], ["1 / 3", "2 / 3", "3 / 3"])  # stock says 3/3, 2/3, 1/3 by collation

    def test_default_format_is_kept_when_it_reads_number_separator_number(self):
        for default, want in (
            ("1/1", "2/5"),
            ("1 / 1", "2 / 5"),
            ("X / Y", "2 / 5"),
            ("Sheet 1 of 1", "Sheet 2 of 5"),
            ("1 von 3 Blatt", "2 von 5 Blatt"),
            ("1 de 1", "2 de 5"),
            ("1 di 1", "2 di 5"),
            ("1 OF 1", "2 OF 5"),
            ("SHEET", "2 / 5"),
            ("Sheet n of m", "2 / 5"),
        ):
            self.assertEqual(SS.sheet_text(default, 2, 5), want, default)

    def test_template_without_sheet_keys_does_not_stop_the_numbering(self):
        m = self.corpus(["none", "iso", "gost"])
        self.assertEqual(m.texts(m.pages[1])["sheet_number"], "2 / 3")
        self.assertEqual((m.texts(m.pages[2])["Лист"], m.texts(m.pages[2])["Листов"]), ("3", "3"))

    # -- check 71: shared fields -------------------------------------------------------------

    def test_shared_fields_from_defaults_and_fields_and_follow_edits(self):
        m = self.corpus(["iso", "fi", "arch"], fields={"drawing_number": "DN-100", "subtitle": "Sub", "checker": "J. Doe", "document_type": "Part"})
        today = datetime.date.today().isoformat()
        v = lambda page: {"title": "Bracket", "author": "Max", "company": "ACME", "dn": "DN-100", "checker": "J. Doe", "doctype": "Part", "date": today}
        self.assertSheets(m, v, "defaults")
        self.assertEqual(m.texts(m.pages[2])["Subtitle"], "Sub")
        m.doc.Label, m.doc.CreatedBy, m.doc.Company = "Bracket B", "Ann", "Globex"
        m.doc.recompute()
        v2 = lambda page: {"title": "Bracket B", "author": "Ann", "company": "Globex", "dn": "DN-100", "checker": "J. Doe", "doctype": "Part", "date": today}
        self.assertSheets(m, v2, "doc edit")
        m.set.Fields = dict(m.set.Fields, title="Own title", drawing_number="DN-200", author="Zed", company="Initech", document_type="Assembly")
        m.doc.recompute()
        v3 = lambda page: {"title": "Own title", "author": "Zed", "company": "Initech", "dn": "DN-200", "checker": "J. Doe", "doctype": "Assembly",
                           "date": today}
        self.assertSheets(m, v3, "Fields edit")
        m.doc.Label = "Other"  # Fields are set: the document's Label no longer matters
        m.doc.recompute()
        self.assertSheets(m, v3, "label after Fields")

    def test_drawing_number_from_the_source_part_number(self):
        m = self.corpus(["iso", "ansi"])
        self.assertNotIn("PN-7", m.texts(m.pages[0]).values())
        m.box.addProperty("App::PropertyString", "PartNumber", "Drawing")
        m.box.PartNumber = "PN-7"
        m.doc.recompute()
        self.assertEqual([m.texts(p)["drawing_number"] for p in m.pages], ["PN-7", "PN-7"])
        m.set.Fields = {"drawing_number": "FIELD-1"}
        m.doc.recompute()
        self.assertEqual([m.texts(p)["drawing_number"] for p in m.pages], ["FIELD-1", "FIELD-1"])

    def test_date_is_iso_stored_once_and_never_the_clock_at_a_recompute(self):
        real = SS.datetime

        class Day(datetime.date):
            day = datetime.date(2031, 3, 4)

            @classmethod
            def today(cls):
                return cls.day

        SS.datetime = types.SimpleNamespace(date=Day)
        try:
            m = self.corpus(["iso", "fi"])
            self.assertEqual([m.texts(m.pages[0])["date_of_issue"], m.texts(m.pages[1])["FC-Date"]], ["2031-03-04", "2031-03-04"])
            Day.day = datetime.date(2032, 9, 9)  # the clock moves on; nothing may read it again
            m.doc.Label = "Bracket 2"
            m.doc.recompute()
            self.add(m, "fi")
            m.doc.recompute()
            for p in m.set.Pages:
                self.assertEqual(SS.fields_of(m.set, p)["date"], "2031-03-04")
            # saved and reopened on another day: still the day it was made
            path = os.path.join(tempfile.mkdtemp(), "d.FCStd")
            m.doc.saveAs(path)
            App.closeDocument(m.doc.Name)
            doc = App.openDocument(path)
            m.doc = doc
            doc.recompute()
            s = SS.drivers(doc)[0]
            self.assertEqual(SS.fields_of(s, s.Pages[0])["date"], "2031-03-04")
        finally:
            SS.datetime = real

    # -- check 72: model fields --------------------------------------------------------------

    def test_material_and_weight_on_four_materials_and_three_sizes_then_edits(self):
        for mat in (STEEL, AL7075, COPPER, IRON):
            for size in ((100, 50, 11), (20, 20, 20), (333, 7, 41)):  # no size lands on a rounding tie at the third decimal
                m = self.corpus(["cn", "gost"], material=mat, size=size)
                w = weight_text(mat[2], size)
                self.assertSheets(m, lambda page: {"material": mat[1], "weight": w}, "%s %s" % (mat[1], size))
                m.close()
                self.made.pop()
        m = self.corpus(["cn", "gost"], material=STEEL, size=(100, 50, 10))
        import Materials

        m.box.ShapeMaterial = Materials.MaterialManager().getMaterial(COPPER[0])
        m.doc.recompute()
        self.assertSheets(m, lambda p: {"material": COPPER[1], "weight": weight_text(COPPER[2], (100, 50, 10))}, "material edit")
        m.box.Height = 30
        m.doc.recompute()
        self.assertSheets(m, lambda p: {"material": COPPER[1], "weight": weight_text(COPPER[2], (100, 50, 30))}, "size edit")

    def test_no_source_or_default_material_leaves_the_fields_alone(self):
        m = self.corpus(["cn"], source=False)
        t = m.texts(m.pages[0])
        self.assertEqual((t["WEIGHT"], t["MATERIALMARK"]), ("重量", "(材料标记)"))
        m2 = self.corpus(["cn"])
        self.assertEqual(m2.texts(m2.pages[0])["MATERIALMARK"], STEEL[1])
        import Materials

        m2.box.ShapeMaterial = Materials.Material()  # no material: nothing new is written, the old text is not blanked
        m2.doc.recompute()
        self.assertEqual(m2.texts(m2.pages[0])["MATERIALMARK"], STEEL[1])
        self.assertEqual(m2.untouched(), [])

    def test_chinese_craftsman_cell_is_not_the_author(self):
        m = self.corpus(["cn", "cn"])
        for p in m.pages:
            self.assertEqual(m.texts(p)["CRAFTS_NAME"], m.before[p.Name]["CRAFTS_NAME"])
            self.assertEqual(m.texts(p)["AUTHOR_NAME"], "Max")

    def test_default_material_is_no_material(self):
        m = self.corpus(["cn", "gost", "iso"], material=None)
        self.assertEqual(m.box.ShapeMaterial.Name, "Default")
        for p in m.pages:
            t = m.texts(p)
            for k in ("MATERIALMARK", "WEIGHT", "Материал1", "Масса", "part_material"):
                if k in t:
                    self.assertEqual(t[k], m.before[p.Name][k], k)

    # -- check 73: scale ---------------------------------------------------------------------

    def view_on(self, m, page, scale):
        v = m.doc.addObject("TechDraw::DrawViewPart", "View")
        page.addView(v)
        v.Source = [m.box]
        v.ScaleType = "Custom"
        v.Scale = scale
        m.doc.recompute()
        return v

    def test_scale_per_sheet_from_the_first_view_else_the_page(self):
        m = self.corpus(["iso", "gost", "ansi"])
        self.assertEqual([m.texts(m.pages[0])["scale"], m.texts(m.pages[1])["Масштаб"], m.texts(m.pages[2])["scale"]], ["1 : 1"] * 3)
        v = self.view_on(m, m.pages[0], 0.5)
        m.doc.recompute()
        self.assertEqual(m.texts(m.pages[0])["scale"], "1 : 2")
        self.assertEqual(m.texts(m.pages[1])["Масштаб"], "1 : 1")  # another sheet is its own
        v.Scale = 2.0
        m.doc.recompute()
        self.assertEqual(m.texts(m.pages[0])["scale"], "2 : 1")
        v.Scale = 0.3
        m.doc.recompute()
        self.assertEqual(m.texts(m.pages[0])["scale"], "3 : 10")
        v.Scale = 2.5
        m.doc.recompute()
        self.assertEqual(m.texts(m.pages[0])["scale"], "5 : 2")
        m.pages[2].Scale = 0.25  # a sheet with no view: the page's scale
        m.doc.recompute()
        self.assertEqual(m.texts(m.pages[2])["scale"], "1 : 4")
        self.view_on(m, m.pages[0], 5.0)  # the first view in Views order decides, not the last or the largest
        self.assertEqual(m.texts(m.pages[0])["scale"], "5 : 2")
        self.assertEqual(m.untouched(), [])

    def test_scale_text_matches_stock_nearest_fraction(self):
        for v, want in ((1.0, "1 : 1"), (0.5, "1 : 2"), (2.0, "2 : 1"), (0.2, "1 : 5"), (0.3, "3 : 10"), (0.02, "1 : 50"), (10.0, "10 : 1"),
                        (0.125, "1 : 8"), (0.75, "3 : 4"), (1.5, "3 : 2")):
            self.assertEqual(SS.scale_text(v), want, v)

    # -- check 74: revision ------------------------------------------------------------------

    def test_revision_follows_the_first_sheet_with_a_table(self):
        m = self.corpus(["iso", "iso", "cn", "ansi"])
        RT.make(m.pages[0])
        RT.add(m.pages[0], "B", "Hole moved", "2026-11-10", "MS")
        m.doc.recompute()
        self.assertEqual(m.texts(m.pages[1])["revision_index"], "B")
        self.assertEqual(m.texts(m.pages[2])["VERSION"], "B")
        self.assertEqual(m.texts(m.pages[3])["revision_index"], "B")
        RT.add(m.pages[0], "C", "Slot added", "2026-11-11", "MS")
        m.doc.recompute()
        self.assertEqual([m.texts(p)["revision_index"] for p in (m.pages[0], m.pages[1], m.pages[3])], ["C", "C", "C"])
        self.assertEqual(m.texts(m.pages[2])["VERSION"], "C")
        # sheet 1's own keys are what RevisionTable alone writes
        alone = App.newDocument("RevAlone")
        try:
            page = alone.addObject("TechDraw::DrawPage", "Page")
            t = alone.addObject("TechDraw::DrawSVGTemplate", "Template")
            page.Template = t
            t.Template = os.path.join(TPL, FAM["iso"]["t"])
            RT.make(page)
            RT.add(page, "B", "Hole moved", "2026-11-10", "MS")
            RT.add(page, "C", "Slot added", "2026-11-11", "MS")
            for k in ("revision_index", "date_of_issue"):
                self.assertEqual(m.texts(m.pages[0])[k], dict(t.EditableTexts)[k], k)
            self.assertEqual(m.texts(m.pages[0])["date_of_issue"], "2026-11-11")
        finally:
            App.closeDocument(alone.Name)
        self.assertEqual(m.untouched(), [])

    def test_two_tables_the_first_sheet_in_set_order_feeds_the_others(self):
        m = self.corpus(["iso", "iso", "iso", "cn"])
        for page, rev in ((m.pages[2], "Y"), (m.pages[0], "X")):
            RT.make(page)
            RT.add(page, rev, "d", "2026-02-02", "MS")
        m.doc.recompute()
        self.assertEqual([m.texts(p)["revision_index"] for p in m.pages[:3]], ["X", "X", "Y"])  # sheet 3 keeps its own table's revision
        self.assertEqual(m.texts(m.pages[3])["VERSION"], "X")
        SS.move_sheet(m.set, m.pages[2], 0)  # now the table of sheet "Y" comes first
        self.assertEqual([m.texts(p)["revision_index"] for p in m.pages[:3]], ["X", "Y", "Y"])  # X keeps its own table's; sheet 2 now follows Y
        self.assertEqual(m.texts(m.pages[3])["VERSION"], "Y")

    def test_revision_table_on_a_later_sheet_still_owns_its_keys(self):
        m = self.corpus(["iso", "iso"])
        RT.make(m.pages[1])
        RT.add(m.pages[1], "B", "x", "2026-01-02", "MS")
        m.doc.recompute()
        self.assertEqual([m.texts(p)["revision_index"] for p in m.pages], ["B", "B"])
        self.assertEqual(m.texts(m.pages[1])["date_of_issue"], "2026-01-02")
        self.assertNotEqual(m.texts(m.pages[0])["date_of_issue"], "2026-01-02")  # the set's date, not another sheet's issue date

    # -- check 75: user edits ----------------------------------------------------------------

    def type_into(self, page, **kv):
        d = dict(page.Template.EditableTexts)
        d.update(kv)
        page.Template.EditableTexts = d

    def test_shared_field_typed_on_one_sheet_reaches_all(self):
        m = self.corpus(["iso", "iso", "ansi", "gost"])
        self.type_into(m.pages[1], title="Typed on two")
        m.doc.recompute()
        self.assertEqual([m.texts(m.pages[0])["title"], m.texts(m.pages[1])["title"], m.texts(m.pages[2])["DrawingTitle1"],
                          m.texts(m.pages[3])["Название"]], ["Typed on two"] * 4)
        self.assertEqual(m.set.Fields["title"], "Typed on two")
        m.doc.Label = "Renamed"  # Fields now rule
        m.doc.recompute()
        self.assertEqual(m.texts(m.pages[0])["title"], "Typed on two")
        self.assertEqual(m.untouched(), [])

    def test_per_sheet_field_typed_is_pinned_listed_and_unpin_restores(self):
        m = self.corpus(["iso", "gost"])
        self.type_into(m.pages[1], Лист="7")
        self.type_into(m.pages[0], sheet_number="Z 1/2")
        m.doc.recompute()
        self.assertEqual(m.texts(m.pages[1])["Лист"], "7")
        self.assertEqual(m.texts(m.pages[0])["sheet_number"], "Z 1/2")
        w = SS.warning_of(m.set)
        self.assertIn(m.pages[1].Label, w)
        self.assertIn("sheet_number", w)
        self.assertIn(m.pages[0].Label, w)
        self.add(m, "gost")  # still kept after the set changes
        self.assertEqual(m.texts(m.pages[1])["Лист"], "7")
        self.assertEqual(m.texts(m.pages[1])["Листов"], "3")  # only the pinned key is kept; the total still follows
        SS.unpin(m.set, m.pages[1], "sheet_number")
        self.assertEqual(m.texts(m.pages[1])["Лист"], "2")
        self.assertNotIn(m.pages[1].Label, SS.warning_of(m.set))
        self.assertEqual(m.texts(m.pages[0])["sheet_number"], "Z 1/2")  # the other pin stays
        SS.unpin(m.set, m.pages[0], "sheet")
        self.assertEqual(m.texts(m.pages[0])["sheet_number"], "1 / 3")
        self.assertEqual(SS.warning_of(m.set), "")
        self.type_into(m.pages[0], scale="1 : 7")  # a pinned scale
        m.doc.recompute()
        self.assertEqual(m.texts(m.pages[0])["scale"], "1 : 7")
        self.assertIn("scale", SS.warning_of(m.set))

    def test_conflicting_edits_resolve_to_the_earlier_sheet_with_a_warning(self):
        m = self.corpus(["iso", "iso", "iso"])
        self.type_into(m.pages[2], title="Late")
        self.type_into(m.pages[1], title="Early")
        m.doc.recompute()
        self.assertEqual([m.texts(p)["title"] for p in m.pages], ["Early"] * 3)
        w = SS.warning_of(m.set)
        self.assertIn(m.pages[2].Label, w)
        self.assertIn("title", w)
        self.type_into(m.pages[0], creator="Same")  # the same value on two sheets is no conflict
        self.type_into(m.pages[2], creator="Same")
        m.doc.recompute()
        self.assertEqual([m.texts(p)["creator"] for p in m.pages], ["Same"] * 3)
        self.assertNotIn("creator", SS.warning_of(m.set))

    def test_edit_of_a_field_nobody_wrote_is_adopted_and_conflicts_name_the_key(self):
        # document_type and general_tolerances have no value in the set, so the driver never wrote them: a typed value is still an edit
        m = self.corpus(["iso"] * 4)
        self.assertEqual(m.set.State, ["Up-to-date"])  # the driver's own bookkeeping must not hide FreeCAD's State
        self.type_into(m.pages[1], document_type="Assembly")
        m.doc.recompute()
        self.assertEqual([m.texts(p)["document_type"] for p in m.pages], ["Assembly"] * 4)
        self.assertEqual(m.set.Fields["document_type"], "Assembly")
        self.type_into(m.pages[3], general_tolerances="ISO 2768-c")
        self.type_into(m.pages[2], general_tolerances="ISO 2768-f")
        m.doc.recompute()
        self.assertEqual([m.texts(p)["general_tolerances"] for p in m.pages], ["ISO 2768-f"] * 4)
        w = SS.warning_of(m.set)
        self.assertIn(m.pages[3].Label, w)
        self.assertIn("general_tolerances", w)
        # GOST checker typed on the last sheet
        g = self.corpus(["gost", "gost", "gost"])
        self.type_into(g.pages[2], Утвердил="С. Иванов")
        g.doc.recompute()
        self.assertEqual([g.texts(p)["Утвердил"] for p in g.pages], ["С. Иванов"] * 3)

    def test_a_template_reset_is_not_a_user_edit(self):
        m = self.corpus(["iso", "iso"])
        t = m.pages[1].Template
        t.Template = os.path.join(TPL, FAM["ansi"]["t"])  # stock autofill re-runs and replaces every field
        t.Template = os.path.join(TPL, FAM["iso"]["t"])
        m.doc.recompute()
        self.assertNotIn("date", m.set.Fields)
        self.assertEqual(m.texts(m.pages[1])["date_of_issue"], m.texts(m.pages[0])["date_of_issue"])
        self.assertEqual(m.texts(m.pages[1])["sheet_number"], "2 / 2")

    # -- check 76: never silent ----------------------------------------------------------------

    def test_templates_without_mapped_keys_are_left_byte_equal_and_named(self):
        m = self.corpus(["none", "iso"])
        blank = m.pages[0]
        self.assertEqual(m.texts(blank), {})
        self.assertIn(blank.Label, SS.warning_of(m.set))
        self.assertEqual(m.texts(m.pages[1])["sheet_number"], "2 / 2")

    def test_excluded_key_is_named_and_kept(self):
        m = self.corpus(["fi", "fi"])
        for p in m.pages:
            # stock autofill wrote '1 / 1' over the paper size when the template was set; the set leaves that key exactly as it found it
            self.assertEqual(m.texts(p)["FC-Sheet"], m.before[p.Name]["FC-Sheet"])
        self.add(m, "fi")
        for p in m.pages:
            self.assertEqual(m.texts(p)["FC-Sheet"], m.before[p.Name]["FC-Sheet"])
        self.assertEqual([m.texts(p)["FC-Page"] for p in m.pages], ["1 / 3", "2 / 3"])
        self.assertIn("FC-Sheet", SS.warning_of(m.set))
        self.assertIn(m.pages[0].Label, SS.warning_of(m.set))

    def test_a_page_in_no_set_is_never_written(self):
        m = self.corpus(["iso"])
        lone = m.page("iso")
        m.doc.recompute()
        before = dict(lone.Template.EditableTexts)
        m.doc.Label = "Changed"
        self.add(m, "iso")
        m.doc.recompute()
        self.assertEqual(dict(lone.Template.EditableTexts), before)

    # -- check 78: persistence and undo ------------------------------------------------------------

    def snapshot(self, m):
        return [(p.Name, dict(p.Template.EditableTexts)) for p in m.set.Pages]

    def test_save_reopen_then_add_renumbers(self):
        m = self.corpus(["iso", "gost"], fields={"title": "Kept"})
        path = os.path.join(tempfile.mkdtemp(), "s.FCStd")
        m.doc.saveAs(path)
        before = self.snapshot(m)
        App.closeDocument(m.doc.Name)
        doc = App.openDocument(path)
        m.doc = doc
        s = SS.drivers(doc)[0]
        self.assertEqual([(p.Name, dict(p.Template.EditableTexts)) for p in s.Pages], before)
        self.assertEqual(dict(s.Fields), {"title": "Kept"})
        doc.recompute()
        self.assertEqual([o.Name for o in doc.Objects if "Touched" in o.State], [])
        SS.add_sheet(s)
        self.assertEqual(dict(s.Pages[0].Template.EditableTexts)["sheet_number"], "1 / 3")
        self.assertEqual(dict(s.Pages[1].Template.EditableTexts)["Листов"], "3")
        self.assertEqual(dict(s.Pages[2].Template.EditableTexts)["Листов"], "3")
        self.assertEqual(dict(s.Pages[2].Template.EditableTexts)["Лист"], "3")

    def test_each_operation_is_one_undo_step_and_undo_restores_the_keys(self):
        m = self.corpus(["iso", "iso"])
        doc = m.doc
        for name, op in (
            ("add", lambda: SS.add_sheet(m.set)),
            ("move", lambda: SS.move_sheet(m.set, m.set.Pages[2], 0)),
            ("remove", lambda: SS.remove_sheet(m.set, m.set.Pages[1])),
        ):
            before = self.snapshot(m)
            n = doc.UndoCount
            op()
            self.assertEqual(doc.UndoCount, n + 1, name)
            doc.undo()
            self.assertEqual(doc.UndoCount, n, name)
            self.assertEqual(self.snapshot(m), before, name)
            doc.redo()
        m2 = Corpus.__new__(Corpus)  # make is one step too
        m2.doc = App.newDocument("MakeUndo")
        self.made.append(m2)
        m2.doc.UndoMode = 1
        m2.fam_of = {}
        m2.pages = [m2.page("iso")]
        m2.doc.recompute()
        n = m2.doc.UndoCount
        before = dict(m2.pages[0].Template.EditableTexts)
        SS.make(m2.doc, pages=m2.pages)
        self.assertEqual(m2.doc.UndoCount, n + 1)
        m2.doc.undo()
        self.assertEqual(SS.drivers(m2.doc), [])
        self.assertEqual(dict(m2.pages[0].Template.EditableTexts), before)

    # -- check 79: speed ---------------------------------------------------------------------------

    def test_twenty_sheets_recompute_under_a_second(self):
        m = self.corpus(["iso"] * 20)
        m.doc.Label = "Changed"
        t0 = time.time()
        m.doc.recompute()
        self.assertLess(time.time() - t0, 1.0)
        self.assertEqual(m.texts(m.pages[19])["sheet_number"], "20 / 20")
        self.assertEqual(m.texts(m.pages[7])["title"], "Changed")
        t0 = time.time()
        m.doc.recompute()
        self.assertLess(time.time() - t0, 1.0)

    # -- API details ---------------------------------------------------------------------------------

    def test_fields_of_and_template_default_for_add_sheet(self):
        m = self.corpus(["gost", "iso"])
        p = self.add(m, "iso")
        self.assertEqual(p.Template.Template, m.pages[1].Template.Template)  # the last sheet's file
        f = SS.fields_of(m.set, m.pages[0])
        self.assertEqual((f["sheet_number"], f["sheet_total"], f["title"]), ("1", "3", "Bracket"))
        p2 = self.add(m, "ansi", template=FAM["ansi"]["t"], label="Z")
        self.assertEqual(p2.Label, "Z")
        self.assertEqual(SS.fields_of(m.set, p2)["sheet"], "4 / 4")


if __name__ == "__main__":
    unittest.main()
