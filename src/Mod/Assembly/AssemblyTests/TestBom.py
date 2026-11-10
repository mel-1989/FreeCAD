# SPDX-License-Identifier: LGPL-2.1-or-later
"""G04 slice 2a: the three fixes to the stock Assembly::BomObject (sibling merge, link arrays, staleness) and the
column widths it keeps.

Quantities follow the stock definition (ISO 7573 parts-list conventions, public catalogue listing): one row per
distinct target at a level, a sub-assembly's children counted per parent instance.
"""

import unittest

import FreeCAD as App

from AssemblyTests import configuration_corpus as C


def _rows(bom):
    """The BOM's rows as they stand: nothing is recomputed here, the caller does one plain doc.recompute()."""
    out = []
    for r in range(2, 60):
        idx = bom.get("A%d" % r) if bom.getContents("A%d" % r) else None
        if idx is None:
            break
        out.append((str(idx), bom.get("B%d" % r), int(bom.get("C%d" % r))))
    return out


def plain_first(doc, bom):
    return _plain(doc, bom)


def _plain(doc, bom):
    doc.recompute()
    return _rows(bom)


class TestBom(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("BomTest")

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def _scene(self):
        """GL, then a sub-assembly holding two links to CubeSrc, then (optionally) more links to CubeSrc."""
        b = C._sub_scene(self.doc)
        self.cube = self.doc.getObject("CubeSrc")
        return b

    def test_sibling_merge_does_not_enter_earlier_subassembly(self):
        b = self._scene()
        lk = b.asm.newObject("App::Link", "Top1")
        lk.LinkedObject = self.cube
        lk2 = b.asm.newObject("App::Link", "Top2")
        lk2.LinkedObject = self.cube
        self.assertEqual(
            _plain(self.doc, self.doc.getObject("BOM")),
            [("1", "PlateSrc", 1), ("2", "Sub", 1), ("2.1", "CubeSrc", 2), ("3", "CubeSrc", 2)],
        )

    def test_link_array_elements_are_counted(self):
        b = C.build_case(self.doc, "array_element")
        self.assertEqual(
            _plain(self.doc, self.doc.getObject("BOM")), [("1", "PlateSrc", 1), ("2", "CubeSrc", 3)]
        )
        # a link next to the array merges into the same row
        extra = b.asm.newObject("App::Link", "Extra")
        extra.LinkedObject = self.doc.getObject("CubeSrc")
        self.assertEqual(
            _plain(self.doc, self.doc.getObject("BOM")), [("1", "PlateSrc", 1), ("2", "CubeSrc", 4)]
        )

    def test_suppressed_array_element_is_not_counted(self):
        b = C.build_case(self.doc, "array_element")
        b.arr.ElementList[1].Suppressed = True
        self.assertEqual(
            _plain(self.doc, self.doc.getObject("BOM")), [("1", "PlateSrc", 1), ("2", "CubeSrc", 2)]
        )

    def test_plain_recompute_follows_the_assembly(self):
        b = self._scene()
        bom = self.doc.getObject("BOM")
        base = [("1", "PlateSrc", 1), ("2", "Sub", 1), ("2.1", "CubeSrc", 2)]
        self.assertEqual(plain_first(self.doc, bom), base)

        def plain():
            return _plain(self.doc, bom)

        # add a component
        lk = b.asm.newObject("App::Link", "Top1")
        lk.LinkedObject = self.cube
        self.assertEqual(plain(), base + [("3", "CubeSrc", 1)])
        # relabel the source part
        self.cube.Label = "Renamed"
        self.assertEqual(plain()[2:], [("2.1", "Renamed", 2), ("3", "Renamed", 1)])
        # remove a component
        b.asm.removeObject(lk)
        self.doc.removeObject(lk.Name)
        self.assertEqual(plain(), [("1", "PlateSrc", 1), ("2", "Sub", 1), ("2.1", "Renamed", 2)])

    def test_untouched_assembly_does_not_recompute_the_bom(self):
        self._scene()
        bom = self.doc.getObject("BOM")
        self.doc.recompute()
        self.assertFalse("Touched" in bom.State)
        self.assertEqual(bom.MustExecute, 0)

    def test_column_widths_survive_regeneration(self):
        self._scene()
        bom = self.doc.getObject("BOM")
        self.doc.recompute()
        bom.setColumnWidth("A", 40)
        bom.setColumnWidth("B", 200)
        bom.touch()
        self.doc.recompute()
        self.assertEqual((bom.getColumnWidth("A"), bom.getColumnWidth("B")), (40, 200))

