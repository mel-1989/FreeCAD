# SPDX-License-Identifier: LGPL-2.1-or-later

# G04 slice 1b: SVG export with the symbol fonts embedded (SvgFonts.embed, SvgFonts.embed_file), headless, on small synthetic SVG strings.
# Run with: FreeCADCmd -t TestTechDrawApp.SvgFontsTest   (part of TestTechDrawApp)
#
# Public sources: CSS Fonts Module (W3C) for font-family quoting and @font-face, RFC 2397 for data: URIs, the OpenType specification
# for cmap and name tables.  The faces are decoded here from the data URIs with fontTools and compared with the original files.
# The preference EmbedFonts and the C++ hook are exercised by the GUI smoke script (harness), not here.

import base64
import io
import os
import re
import sys
import tempfile
import unittest

import FreeCAD as App

import SvgFonts as SF

FONTS = os.path.join(App.getResourceDir(), "Mod", "TechDraw", "Resources", "fonts")
HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n<svg xmlns="http://www.w3.org/2000/svg" xmlns:freecad="http://www.freecad.org/wiki/index.php?title=Svg_Namespace" width="297mm" height="210mm" viewBox="0 0 2970 2100">\n<defs/>\n'
TAIL = "\n</svg>\n"
FACE = re.compile(r"@font-face\{font-family:'([^']*)';font-style:(\w+);font-weight:normal;src:url\(data:font/ttf;base64,([A-Za-z0-9+/=]*)\)")


def svg(body, head=HEAD):
    return head + body + TAIL


def faces_of(text):
    """-> {(family, style): TTFont} decoded from the data URIs of ``text``."""
    from fontTools.ttLib import TTFont

    return {(m.group(1), m.group(2)): TTFont(io.BytesIO(base64.b64decode(m.group(3)))) for m in FACE.finditer(text)}


def original(fn):
    from fontTools.ttLib import TTFont

    return TTFont(os.path.join(FONTS, fn))


def texts(t):
    return re.findall(r"<(?:text|tspan)\b[^>]*>([^<]*)", t)


def strip_injection(t):
    """The output without the comment and <style> that were added (and the <defs> restored)."""
    t = re.sub(r"<defs>\s*<!-- embedded font subsets.*?</style></defs>", "<defs/>", t, flags=re.S)
    return t


class SvgFontsTest(unittest.TestCase):
    def test_quoting_only_where_css_needs_it(self):
        body = (
            '<g font-family=".AppleSystemUIFont"><text font-family="Y14.5-FreeCAD" x="1">&#xb8;</text>'
            '<text style="fill:red;font-family:Y14.5-2009;font-size:3" x="2">a</text>'
            '<text font-family="Open Sans" x="3">b</text><text font-family="sans-serif">c</text>'
            "<text font-family=\"'Y14.5-FreeCAD'\">d</text><text font-family=\"Times New Roman, Y14.5-X, serif\">e</text></g>"
        )
        out, rep = SF.embed(svg(body), [FONTS])
        self.assertIn('<g font-family="\'.AppleSystemUIFont\'">', out)
        self.assertIn('font-family="\'Y14.5-FreeCAD\'" x="1"', out)
        self.assertIn("fill:red;font-family:'Y14.5-2009';font-size:3", out)
        self.assertIn('<text font-family="Open Sans" x="3">', out)
        self.assertIn('<text font-family="sans-serif">c', out)
        self.assertIn("font-family=\"'Y14.5-FreeCAD'\">d", out)  # already quoted: untouched, not double quoted
        self.assertIn("font-family=\"Times New Roman, 'Y14.5-X', serif\"", out)
        self.assertNotIn("''", out.split("<defs>")[0] + out.split("</defs>")[-1])
        self.assertEqual(rep["quoted"], 4)

    def test_family_match_by_internal_name_case_and_style(self):
        from fontTools.ttLib import TTFont  # noqa: F401

        body = (
            '<text font-family="osifont" x="0">A</text>'
            '<text font-family="OSIFONT" font-style="italic" x="0">B</text>'
            '<g style="font-family:osifont;font-style:italic"><text x="0"><tspan>C</tspan></text></g>'
            '<text font-family="Y14.5-2009" x="0">&#xb8;</text>'
            '<text font-family="Foo, Y14.5-FreeCAD" x="0">&#xbf;</text>'
            '<text font-family="Y14.5-2018" x="0">&#xbf;</text>'
        )
        out, rep = SF.embed(svg(body), [FONTS])
        fs = faces_of(out)
        self.assertEqual(sorted(fs), [("Y14.5-2009", "normal"), ("Y14.5-FreeCAD", "normal"), ("osifont", "italic"), ("osifont", "normal")])
        it = fs[("osifont", "italic")]
        self.assertEqual(sorted(c for c in it.getBestCmap() if c > 0x20), [ord("B"), ord("C")])
        up = fs[("osifont", "normal")]
        self.assertEqual(sorted(c for c in up.getBestCmap() if c > 0x20), [ord("A")])
        self.assertEqual(it["name"].getDebugName(2), "Italic")
        self.assertEqual(fs[("Y14.5-2009", "normal")]["name"].getDebugName(1), "Y14.5-2009")
        self.assertEqual(rep["non_bundled"], ["Y14.5-2018"])  # a family no bundled font calls by that name is named, not guessed; "Foo" is only a fallback ahead of a bundled one
        self.assertEqual(sorted((e["family"], e["style"]) for e in rep["embedded"]), sorted(fs))

    def test_inheritance_and_style_over_attribute(self):
        body = '<g font-family="Y14.5-FreeCAD"><g><text x="0" y="0">&#xb8;<tspan font-family="osifont">Q</tspan>&#xbf;</text></g></g>'
        out, rep = SF.embed(svg(body), [FONTS])
        fs = faces_of(out)
        self.assertEqual(sorted(c for c in fs[("Y14.5-FreeCAD", "normal")].getBestCmap() if c > 0x20), [0xB8, 0xBF])
        self.assertEqual(sorted(c for c in fs[("osifont", "normal")].getBestCmap() if c > 0x20), [ord("Q")])
        # text outside <text> is not text: a <title> and a <style> mention do not pull glyphs in
        out2, _ = SF.embed(svg('<title>Z</title><g font-family="osifont"><text>A</text></g>'), [FONTS])
        self.assertEqual(sorted(c for c in faces_of(out2)[("osifont", "normal")].getBestCmap() if c > 0x20), [ord("A")])

    def test_every_used_codepoint_has_a_glyph_and_outlines_and_names_are_the_originals(self):
        used = [0xB2, 0xB8, 0xBC, 0xBF, 0xCC, 0xD8, 0xDB]
        sym = "".join("&#x%x;" % c for c in used)
        body = '<text font-family="Y14.5-FreeCAD">%s</text><text font-family="osifont">&#xd8; &#x2300; &#xb1; 0.05 Ab</text>' % sym
        out, rep = SF.embed(svg(body), [FONTS])
        fs = faces_of(out)
        y = fs[("Y14.5-FreeCAD", "normal")]
        o = original("Y14.5-FreeCAD.ttf")
        cm, ocm = y.getBestCmap(), o.getBestCmap()
        for c in used:
            self.assertIn(c, cm)
            self.assertEqual(self.outline(y, cm[c]), self.outline(o, ocm[c]), "U+%04X" % c)
        self.assertIn(0x20, cm)
        self.assertNotIn(0xB9, cm)  # not used: not carried
        self.assertEqual(self.names(y), self.names(o))
        self.assertEqual(rep["missing"], {})
        osi = fs[("osifont", "normal")]
        oo = original("osifont-lgpl3fe.ttf")
        for ch in "Ø⌀± 0.05Ab":
            self.assertIn(ord(ch), osi.getBestCmap(), repr(ch))
            self.assertEqual(self.outline(osi, osi.getBestCmap()[ord(ch)]), self.outline(oo, oo.getBestCmap()[ord(ch)]))
        self.assertEqual(self.names(osi), self.names(oo))
        for tag in ("GSUB", "GPOS", "fpgm", "prep", "cvt "):  # shaping and hinting tables carried when the original has them
            if tag in oo:
                self.assertIn(tag, osi, tag)

    @staticmethod
    def outline(font, gname):
        from fontTools.pens.recordingPen import RecordingPen

        pen = RecordingPen()
        font.getGlyphSet()[gname].draw(pen)
        g = font["glyf"][gname]
        prog = bytes(g.program.getBytecode()) if hasattr(g, "program") and g.program else b""
        return (pen.value, prog, font["hmtx"][gname])

    @staticmethod
    def names(font):
        return sorted((n.nameID, n.platformID, n.platEncID, n.langID, n.toUnicode()) for n in font["name"].names)

    def test_codepoints_the_face_lacks_are_reported_not_hidden(self):
        out, rep = SF.embed(svg('<text font-family="osifont">A\u4e2d\u3042</text>'), [FONTS])
        self.assertEqual(rep["missing"], {"osifont": [0x3042, 0x4E2D]})
        self.assertIn("U+4E2D", rep["warning"])
        self.assertIn("U+3042", rep["warning"])
        self.assertIn("osifont", rep["warning"])
        self.assertNotIn(0x4E2D, faces_of(out)[("osifont", "normal")].getBestCmap())

    def test_non_bundled_families_are_named_in_report_and_warning_and_not_embedded(self):
        body = (
            '<g font-family=".AppleSystemUIFont"><text>x</text></g><text font-family="Arial">y</text><text font-family="sans-serif">z</text>'
            '<text font-family="Open Sans, Arial">w</text><text font-family="osifont">v</text>'
        )
        out, rep = SF.embed(svg(body), [FONTS])
        self.assertEqual(rep["non_bundled"], [".AppleSystemUIFont", "Arial", "Open Sans", "sans-serif"])
        for n in rep["non_bundled"]:
            self.assertIn(n, rep["warning"])
        self.assertEqual(list(faces_of(out)), [("osifont", "normal")])
        _, quiet = SF.embed(svg('<text font-family="osifont">v</text>'), [FONTS])
        self.assertEqual(quiet["warning"], "")
        self.assertEqual(quiet["non_bundled"], [])

    def test_text_and_every_other_byte_unchanged(self):
        body = (
            '<g font-family=".AppleSystemUIFont" fill="#000"><text font-family="Y14.5-FreeCAD" freecad:editable="K" x="1.5" y="2">&#xb8;&#xbf;</text>'
            '<text font-family="osifont" x="3">&#xd8;12 &amp; &lt;x&gt;<tspan x="4">± 0.05</tspan></text></g>'
            '<path d="M0 0 L1 1" stroke="#fff"/>'
        )
        src = svg(body)
        out, _ = SF.embed(src, [FONTS])
        self.assertEqual(texts(out), texts(src))
        back = strip_injection(out).replace("'Y14.5-FreeCAD'", "Y14.5-FreeCAD").replace("'.AppleSystemUIFont'", ".AppleSystemUIFont")
        self.assertEqual(back, src)

    def test_stock_export_that_is_not_well_formed_is_accepted(self):
        head = HEAD.replace("<defs/>", "<defs/>")
        body = '<text font-family="osifont" index:editable="TITLE" x="1">Tö</text>'  # undeclared prefix, as the DE template writes
        out, rep = SF.embed(svg(body, head), [FONTS])
        self.assertIn('index:editable="TITLE"', out)
        self.assertIn(("osifont", "normal"), faces_of(out))

    def test_one_style_in_the_existing_defs_or_a_new_one(self):
        out, _ = SF.embed(svg('<text font-family="osifont">a</text>'), [FONTS])
        self.assertEqual(out.count("<style"), 1)
        self.assertEqual(out.count("<defs"), 1)
        self.assertTrue(out.index("<defs>") < out.index("<style") < out.index("</defs>"))
        head = HEAD.replace("<defs/>", '<defs><linearGradient id="g"/></defs>')
        out2, _ = SF.embed(svg('<text font-family="osifont">a</text>', head), [FONTS])
        self.assertEqual(out2.count("<style"), 1)
        self.assertTrue(out2.index("<defs>") < out2.index("<style") < out2.index("<linearGradient"))
        out3, _ = SF.embed(svg('<text font-family="osifont">a</text>', HEAD.replace("<defs/>\n", "")), [FONTS])
        self.assertEqual((out3.count("<defs>"), out3.count("<style")), (1, 1))

    def test_deterministic_and_idempotent(self):
        src = svg('<text font-family="osifont" font-style="italic">AØ</text><text font-family="Y14.5-FreeCAD">&#xb8;</text>')
        a, _ = SF.embed(src, [FONTS])
        b, _ = SF.embed(src, [FONTS])
        self.assertEqual(a, b)
        c, rep = SF.embed(a, [FONTS])
        self.assertEqual(c, a)
        self.assertEqual(rep["embedded"], [])
        # no clock is read: the subset's head.modified is the source font's
        self.assertEqual(faces_of(a)[("osifont", "italic")]["head"].modified, original("osifont-italic.ttf")["head"].modified)

    def test_licence_comment_names_each_font_and_its_licence(self):
        out, _ = SF.embed(svg('<text font-family="osifont">a</text><text font-family="Y14.5-FreeCAD">&#xb8;</text>'), [FONTS])
        m = re.search(r"<!-- (embedded font subsets.*?)-->", out, re.S)
        self.assertTrue(m)
        c = m.group(1)
        self.assertIn("osifont-lgpl3fe.ttf", c)
        self.assertIn("Font-exception-2.0", c)
        self.assertIn("Y14.5-FreeCAD.ttf", c)
        self.assertIn("LGPL-2.0-or-later", c)
        self.assertNotIn("--", c)

    def test_fonttools_missing_leaves_the_stock_file_and_says_so(self):
        src = svg('<text font-family="Y14.5-FreeCAD">&#xb8;</text>')
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "p.svg")
            with open(p, "w", encoding="utf-8", newline="") as fh:
                fh.write(src)
            saved = {k: sys.modules.get(k) for k in ("fontTools", "fontTools.subset", "fontTools.ttLib")}
            try:
                for k in saved:
                    sys.modules[k] = None  # an import of it now raises ImportError
                with self.assertRaises(ImportError):
                    SF.embed(src, [FONTS])
                w = SF.embed_file(p, FONTS)
            finally:
                for k, v in saved.items():
                    if v is None:
                        sys.modules.pop(k, None)
                    else:
                        sys.modules[k] = v
            self.assertIn("fontTools", w)
            with open(p, encoding="utf-8", newline="") as fh:
                self.assertEqual(fh.read(), src)

    def test_embed_file_in_place_keeps_line_ends_and_never_raises(self):
        src = svg('<text font-family="osifont">a</text><text font-family="Arial">b</text>').replace("\n", "\r\n")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "p.svg")
            with open(p, "w", encoding="utf-8", newline="") as fh:
                fh.write(src)
            w = SF.embed_file(p, FONTS)
            self.assertIn("Arial", w)
            with open(p, "rb") as fh:
                data = fh.read().decode("utf-8")
            self.assertIn("@font-face", data)
            self.assertEqual(data.count("\n"), data.count("\r\n"))
            self.assertIn(("osifont", "normal"), faces_of(data))
            # a missing file, a file with no <svg>, a bad font directory: a message, never an exception
            self.assertTrue(SF.embed_file(os.path.join(d, "nope.svg"), FONTS))
            q = os.path.join(d, "q.svg")
            with open(q, "w") as fh:
                fh.write("<g><text font-family='osifont'>a</text></g>")
            self.assertTrue(SF.embed_file(q, FONTS))
            with open(q) as fh:
                self.assertEqual(fh.read(), "<g><text font-family='osifont'>a</text></g>")
            self.assertEqual(SF.embed_file(p, os.path.join(d, "no-such-dir")), "")  # already embedded: nothing to do

    def test_font_dirs_are_searched_first(self):
        import shutil

        with tempfile.TemporaryDirectory() as d:
            shutil.copy(os.path.join(FONTS, "Y14.5-FreeCAD.ttf"), os.path.join(d, "Mine.ttf"))
            out, rep = SF.embed(svg('<text font-family="Y14.5-FreeCAD">&#xb8;</text>'), [d])
            self.assertEqual([e["file"] for e in rep["embedded"]], ["Mine.ttf"])
