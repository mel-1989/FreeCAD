# SPDX-License-Identifier: LGPL-2.1-or-later
"""G08a slice 4b: every icon a SheetMetal .ui panel names resolves to an SVG file in Resources/icons (the panels reach them
through the "Icons:" search path), and every icon file of the module is SVG."""

import os
import re
import unittest

import SheetMetalTools


class TestPanelIcons(unittest.TestCase):
    def test_ui_icon_references_resolve_to_svg(self):
        seen = 0
        for f in sorted(os.listdir(SheetMetalTools.panels_path)):
            if not f.endswith(".ui"):
                continue
            text = open(os.path.join(SheetMetalTools.panels_path, f), encoding="utf-8").read()
            for name in re.findall(r"Icons:([A-Za-z0-9_\-.]+)", text):
                seen += 1
                with self.subTest(panel=f, icon=name):
                    self.assertTrue(name.endswith(".svg"), "not an SVG: " + name)
                    self.assertTrue(os.path.isfile(os.path.join(SheetMetalTools.icons_path, name)), "missing: " + name)
        self.assertGreater(seen, 0)

    def test_every_icon_file_is_svg(self):
        files = os.listdir(SheetMetalTools.icons_path)
        self.assertTrue(files)
        self.assertEqual([f for f in files if not f.endswith(".svg")], [])
