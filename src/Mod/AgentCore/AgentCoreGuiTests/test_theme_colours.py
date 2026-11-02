# SPDX-License-Identifier: LGPL-2.1-or-later
"""G08a slice 4b: the change-set panel's coloured labels keep WCAG AA text contrast whatever the theme (needs FreeCADGui)."""

import re
import unittest

from AgentCore.gui import overlay, panel


def _lin(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _ratio(bg_css):
    bg = [int(x) for x in re.search(r"background-color: rgb\((\d+),(\d+),(\d+)\)", bg_css).groups()]
    fg = (255, 255, 255) if "color: white" in bg_css else (0, 0, 0)
    lum = lambda c: 0.2126 * _lin(c[0]) + 0.7152 * _lin(c[1]) + 0.0722 * _lin(c[2])
    hi, lo = max(lum(bg), lum(fg)), min(lum(bg), lum(fg))
    return (hi + 0.05) / (lo + 0.05)


class TestSwatchContrast(unittest.TestCase):
    def test_overlay_colours_reach_4_5_to_1(self):
        for kind, color in overlay.COLORS.items():
            with self.subTest(kind=kind):
                self.assertGreaterEqual(_ratio(panel._swatch_css(color)), 4.5)

    def test_light_colour_gets_black_text_and_dark_colour_white(self):
        self.assertIn("color: black", panel._swatch_css((0.1, 0.75, 0.2)))
        self.assertIn("color: white", panel._swatch_css((0.1, 0.1, 0.5)))
