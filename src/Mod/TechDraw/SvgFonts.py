# SPDX-License-Identifier: LGPL-2.1-or-later

# **************************************************************************
#   SVG export with the symbol fonts embedded (G04 slice 1b).               *
#                                                                           *
#   Public sources: the CSS Fonts Module Level 4 (W3C) for @font-face,      *
#   font-family matching and the quoting of family names, RFC 2397 for      *
#   data: URIs, the OpenType specification (Microsoft) for the name, cmap   *
#   and OS/2 tables, and the licence files shipped beside the fonts         *
#   (osifont.license, Y14.5Font.license).                                   *
# **************************************************************************

"""Make an exported TechDraw page draw its text with the right glyphs on a machine that lacks the fonts.

    import SvgFonts
    svg, report = SvgFonts.embed(svg_text, font_dirs=[dir])     # text in, text out
    warning = SvgFonts.embed_file(path, font_dir)               # in place; -> "" or one line for the Report view

Qt writes a page as SVG with font names only.  ``embed`` finds the font families the page's ``<text>`` uses (the CSS cascade over
``font-family`` attributes and ``style`` declarations, with ``font-style``), and for every family that matches a bundled TrueType font
(by the font's own name table) adds a ``@font-face`` with a subset of that font, holding the glyphs the text uses plus the space, as a
``data:font/ttf;base64`` URI in one ``<style>`` inside the file's ``<defs>``.  Family names that are not valid unquoted CSS identifiers
(``Y14.5-FreeCAD``, ``.AppleSystemUIFont``) are quoted, because a browser drops the unquoted declaration.  Nothing else in the file
changes.

The work is done on the text, never on a parsed tree: the stock export of some templates is not namespace-well-formed (an undeclared
``index:`` prefix), so an XML parser would refuse it.

The subset keeps every kept glyph's outline and hinting, the layout tables and the whole name table (so the copyright and licence
strings travel with the font); a comment beside the ``<style>`` names each embedded font and its licence.  Output is deterministic
(no clock is read).  Families the page uses that are not bundled, and codepoints the bundled face lacks, are returned in the report and
never silent.  ``fontTools`` is imported when needed; without it ``embed`` raises ``ImportError`` and ``embed_file`` leaves the file as it is.
"""

import base64
import html
import io
import os
import re

MARKER = "embedded font subsets (SvgFonts)"

# the licence line of each bundled family, from the licence files shipped in Resources/fonts (not legal advice)
LICENCES = {
    "osifont": (
        "GPL-2.0-only WITH Font-exception-2.0 OR GPL-3.0-only WITH Font-exception-2.0 OR LGPL-3.0-only WITH Font-exception-2.0 (osifont.license); "
        "the subset keeps the unaltered glyph outlines, hinting and name table of the font, as the font exception allows for a document"
    ),
    "y14.5-freecad": (
        "free to use and redistribute, Peter Kanold; modifications LGPL-2.0-or-later (Y14.5Font.license); "
        "the subset keeps the unaltered glyph outlines, hinting and name table of the font"
    ),
    "y14.5-2009": (
        "free to use and redistribute, Peter Kanold; modifications LGPL-2.0-or-later (Y14.5Font.license); "
        "the subset keeps the unaltered glyph outlines, hinting and name table of the font"
    ),
}

GENERIC = ("serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui", "ui-serif", "ui-sans-serif", "ui-monospace", "ui-rounded", "emoji", "math", "fangsong")

_IDENT = re.compile(r"^-?(?:[A-Za-z_]|[^\x00-\x7f])(?:[A-Za-z0-9_-]|[^\x00-\x7f])*$")
_TAG = re.compile(r"<(/?)([^\s/>!?][^\s/>]*)((?:[^>\"']|\"[^\"]*\"|'[^']*')*?)(/?)>")
_SKIP = re.compile(r"<!--.*?-->|<!\[CDATA\[.*?\]\]>|<\?.*?\?>|<!DOCTYPE[^>]*>", re.S)
_ATTR = re.compile(r"([^\s=/>]+)(\s*=\s*)(\"[^\"]*\"|'[^']*')")
_DECL = re.compile(r"(font-family|font-style|font-weight)(\s*:\s*)([^;]*)", re.I)

_FACES = {}


# ------------------------------------------------------------------------------------------------
# the bundled faces
# ------------------------------------------------------------------------------------------------


def default_font_dirs():
    try:
        import FreeCAD as App

        return [os.path.join(App.getResourceDir(), "Mod", "TechDraw", "Resources", "fonts")]
    except Exception:
        return []


def _as_dirs(font_dirs):
    if font_dirs is None:
        return default_font_dirs()
    if isinstance(font_dirs, (str, os.PathLike)):
        font_dirs = [font_dirs]
    dirs = [str(d) for d in font_dirs]
    return dirs + [d for d in default_font_dirs() if d not in dirs]


def _name(font, ids):
    for i in ids:
        for rec in font["name"].names:
            if rec.nameID == i and rec.isUnicode():
                try:
                    return rec.toUnicode()
                except Exception:
                    continue
        for rec in font["name"].names:
            if rec.nameID == i:
                try:
                    return rec.toUnicode()
                except Exception:
                    continue
    return ""


def _describe(path):
    """-> dict(path, names, italic, cmap) of the TrueType file, by its own name table; cached on path and mtime."""
    from fontTools.ttLib import TTFont

    key = (path, os.path.getmtime(path))
    hit = _FACES.get(key)
    if hit is not None:
        return hit
    f = TTFont(path, lazy=True, recalcTimestamp=False)
    try:
        names = []
        for ids in ((1,), (16,)):
            n = _name(f, ids)
            if n and n.lower() not in names:
                names.append(n.lower())
        sub = _name(f, (17, 2)).lower()
        italic = "italic" in sub or "oblique" in sub
        if "OS/2" in f and f["OS/2"].fsSelection & 1:
            italic = True
        if "head" in f and f["head"].macStyle & 2:
            italic = True
        d = {"path": path, "family": _name(f, (16, 1)), "names": names, "italic": italic, "cmap": set(f.getBestCmap() or {}), "file": os.path.basename(path)}
    finally:
        f.close()
    _FACES[key] = d
    return d


def bundled_faces(font_dirs=None):
    """-> {family name (lower case): {False: face, True: face}} over the ``.ttf`` files of the font directories, first found wins."""
    out = {}
    for d in _as_dirs(font_dirs):
        try:
            files = sorted(os.listdir(d))
        except OSError:
            continue
        for fn in files:
            if not fn.lower().endswith(".ttf"):
                continue
            try:
                face = _describe(os.path.join(d, fn))
            except Exception:
                continue
            for n in face["names"]:
                out.setdefault(n, {}).setdefault(face["italic"], face)
    return out


# ------------------------------------------------------------------------------------------------
# families and styles
# ------------------------------------------------------------------------------------------------


def split_families(value):
    """-> [family names without quotes] of a CSS ``font-family`` value (commas outside quotes)."""
    out, cur, q = [], "", None
    for ch in value:
        if q:
            if ch == q:
                q = None
            else:
                cur += ch
        elif ch in "'\"":
            q = ch
        elif ch == ",":
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    out.append(cur.strip())
    return [re.sub(r"\s+", " ", n) for n in out if n]


def needs_quotes(name):
    """True when ``name`` is not a sequence of CSS identifiers (so it cannot stand unquoted in ``font-family``)."""
    return not all(_IDENT.match(tok) for tok in name.split(" "))


def quote_families(value):
    """-> ``value`` with each family that cannot stand unquoted put in single quotes, else ``value`` itself (same bytes)."""
    parts = _split_keep(value)
    changed = False
    out = []
    for raw in parts:
        s = raw.strip()
        if s and s[0] not in "'\"" and needs_quotes(re.sub(r"\s+", " ", s)):
            raw = raw.replace(s, "'" + s.replace("'", "\\'") + "'", 1)
            changed = True
        out.append(raw)
    return ",".join(out) if changed else value


def _split_keep(value):
    parts, cur, q = [], "", None
    for ch in value:
        if q:
            cur += ch
            if ch == q:
                q = None
        elif ch in "'\"":
            q = ch
            cur += ch
        elif ch == ",":
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return parts


def _esc_attr(value, delim):
    v = value.replace("&", "&amp;").replace("<", "&lt;")
    return v.replace(delim, "&quot;" if delim == '"' else "&apos;")


def _props(attrs, parent):
    """The inherited font properties of an element: (family value, italic) over the attributes then the ``style`` declarations."""
    fam, ital = parent
    style = None
    for m in _ATTR.finditer(attrs):
        k, v = m.group(1), html.unescape(m.group(3)[1:-1])
        if k == "font-family":
            fam = v
        elif k == "font-style":
            ital = v.strip().lower() in ("italic", "oblique") or v.strip().lower().startswith("oblique")
        elif k == "style":
            style = v
    if style:
        for m in _DECL.finditer(style):
            k, v = m.group(1).lower(), m.group(3).strip()
            v = re.sub(r"\s*!important\s*$", "", v)
            if k == "font-family":
                fam = v
            elif k == "font-style":
                ital = v.lower() == "italic" or v.lower().startswith("oblique")
    return fam, ital


def _rewrite_attrs(attrs):
    """-> ``attrs`` with the ``font-family`` attribute and ``style`` declarations quoted where needed; every other byte as it was."""

    def attr(m):
        k, eq, q = m.group(1), m.group(2), m.group(3)
        if k not in ("font-family", "style"):
            return m.group(0)
        delim = q[0]
        val = html.unescape(q[1:-1])
        if k == "font-family":
            new = quote_families(val)
        else:
            new = _DECL.sub(lambda d: d.group(0) if d.group(1).lower() != "font-family" else d.group(1) + d.group(2) + quote_families(d.group(3)), val)
        if new == val:
            return m.group(0)
        return k + eq + delim + _esc_attr(new, delim) + delim

    return _ATTR.sub(attr, attrs)


def scan(svg_text):
    """-> (runs, rewritten, quoted): ``runs`` maps (font-family value or None, italic) to the set of codepoints the ``<text>`` content
    uses; ``rewritten`` is ``svg_text`` with the unquotable family names quoted; ``quoted`` counts the tags changed."""
    runs = {}
    out = []
    quoted = 0
    pos = 0
    stack = [((None, False), "")]  # (inherited props, tag name)
    text_depth = 0
    n = len(svg_text)
    i = 0

    def feed(seg):
        if text_depth > 0 and seg:
            cps = {ord(c) for c in html.unescape(seg) if not c.isspace()}
            if cps:
                runs.setdefault(stack[-1][0], set()).update(cps)

    while i < n:
        j = svg_text.find("<", i)
        if j < 0:
            feed(svg_text[i:])
            break
        feed(svg_text[i:j])
        m = _SKIP.match(svg_text, j)
        if m:
            i = m.end()
            continue
        m = _TAG.match(svg_text, j)
        if not m:
            i = j + 1
            continue
        closing, name, attrs, selfclose = m.group(1), m.group(2), m.group(3), m.group(4)
        local = name.rsplit(":", 1)[-1]
        if closing:
            for k in range(len(stack) - 1, 0, -1):
                if stack[k][1] == name:
                    if local == "text":
                        text_depth = 0
                    del stack[k:]
                    break
        else:
            new_attrs = _rewrite_attrs(attrs)
            if new_attrs != attrs:
                out.append(svg_text[pos:j])
                out.append("<" + name + new_attrs + selfclose + ">")
                pos = m.end()
                quoted += 1
            if not selfclose:
                stack.append((_props(attrs, stack[-1][0]), name))
                if local == "text":
                    text_depth = 1
        i = m.end()
    out.append(svg_text[pos:])
    return runs, "".join(out), quoted


# ------------------------------------------------------------------------------------------------
# subsetting and injection
# ------------------------------------------------------------------------------------------------


def subset_font(path, codepoints):
    """-> TTF bytes holding ``codepoints`` (and the space) of the font at ``path``; outlines, hinting and the name table are kept."""
    from fontTools import subset
    from fontTools.ttLib import TTFont

    opts = subset.Options()
    opts.layout_features = ["*"]
    opts.notdef_outline = True
    opts.hinting = True
    opts.legacy_kern = True
    opts.name_IDs = ["*"]
    opts.name_languages = ["*"]
    opts.name_legacy = True
    opts.glyph_names = False
    opts.recalc_timestamp = False
    opts.recalc_bounds = False
    opts.drop_tables = sorted(set(opts.drop_tables) | {"FFTM"})
    font = TTFont(path, recalcTimestamp=False)
    try:
        sub = subset.Subsetter(opts)
        sub.populate(unicodes=sorted(codepoints))
        sub.subset(font)
        buf = io.BytesIO()
        font.flavor = None
        font.save(buf)
        return buf.getvalue()
    finally:
        font.close()


def _generic(name):
    return name.lower() in GENERIC


def plan(runs, faces):
    """-> (wanted {(face path, italic): set of codepoints}, non_bundled [names], missing {family: [codepoints]})."""
    wanted, non_bundled, missing = {}, set(), {}
    for (fam, ital), cps in runs.items():
        names = split_families(fam) if fam else []
        hit = None
        for nm in names:
            fs = faces.get(nm.lower())
            if fs:
                hit = fs.get(ital) or fs.get(False) or next(iter(fs.values()))
                break
        if hit is None:
            non_bundled.update(names)
            continue
        key = (hit["path"], hit["italic"])
        wanted.setdefault(key, {"face": hit, "cps": set()})["cps"].update(cps)
    for key, w in wanted.items():
        gone = sorted(c for c in w["cps"] if c not in w["face"]["cmap"])
        if gone:
            missing.setdefault(w["face"]["family"], set()).update(gone)
    return wanted, sorted(non_bundled, key=str.lower), {k: sorted(v) for k, v in sorted(missing.items())}


def _css_name(name):
    return "'" + name.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _comment_text(s):
    return s.replace("--", "- -")


def embed(svg_text, font_dirs=None):
    """-> (svg text with the fonts embedded and families quoted, report).

    ``report``: ``embedded`` (one dict per face: family, style, file, codepoints, bytes), ``non_bundled`` (family names the text uses that
    no bundled font carries), ``missing`` (family to the codepoints the bundled face lacks), ``quoted`` (number of family values quoted),
    ``warning`` (one line, or empty).  Raises ``ImportError`` when fontTools is missing."""
    import fontTools.subset  # noqa: F401  (fail early and before any change)

    report = {"embedded": [], "non_bundled": [], "missing": {}, "quoted": 0, "warning": ""}
    if MARKER in svg_text:
        report["warning"] = ""
        return svg_text, report
    faces = bundled_faces(font_dirs)
    runs, text, report["quoted"] = scan(svg_text)
    wanted, non_bundled, missing = plan(runs, faces)
    report["non_bundled"] = non_bundled
    report["missing"] = missing
    css, notes = [], []
    for key in sorted(wanted, key=lambda k: (wanted[k]["face"]["family"].lower(), k[1], k[0])):
        w = wanted[key]
        face = w["face"]
        have = {c for c in w["cps"] if c in face["cmap"]}
        if 0x20 in face["cmap"]:
            have.add(0x20)
        data = subset_font(face["path"], have)
        style = "italic" if face["italic"] else "normal"
        css.append(
            "@font-face{font-family:%s;font-style:%s;font-weight:normal;src:url(data:font/ttf;base64,%s) format('truetype');}"
            % (_css_name(face["family"]), style, base64.b64encode(data).decode("ascii"))
        )
        lic = LICENCES.get(face["family"].lower(), "see the name table of the font")
        notes.append("%s %s (%s), %d glyph codepoints, licence: %s" % (face["family"], style, face["file"], len(have), lic))
        report["embedded"].append({"family": face["family"], "style": style, "file": face["file"], "codepoints": len(have), "bytes": len(data)})
    if css:
        nl = "\r\n" if "\r\n" in text else "\n"  # the file's own line ends
        block = "<!-- %s:%s%s%s-->%s<style type=\"text/css\">%s%s%s</style>" % (MARKER, nl, _comment_text(nl.join(notes)), nl, nl, nl, nl.join(css), nl)
        text = _inject(text, block)
    bits = []
    if non_bundled:
        bits.append("fonts not bundled, left to the viewer: " + ", ".join(non_bundled))
    if missing:
        bits.append("codepoints missing from the bundled font: " + "; ".join("%s %s" % (f, " ".join("U+%04X" % c for c in cps)) for f, cps in missing.items()))
    report["warning"] = " | ".join(bits)
    return text, report


_DEFS = re.compile(r"<defs(?:\s[^>]*?)?(/?)>")
_SVG = re.compile(r"<svg(?:\s(?:[^>\"']|\"[^\"]*\"|'[^']*')*)?>")


def _inject(text, block):
    m = _DEFS.search(text)
    if m:
        if m.group(1):  # <defs/> or <defs a="b"/>
            open_tag = m.group(0)[:-2].rstrip() + ">"
            return text[: m.start()] + open_tag + block + "</defs>" + text[m.end() :]
        return text[: m.end()] + block + text[m.end() :]
    s = _SVG.search(text)
    if not s:
        raise ValueError("no <svg> element to put the fonts in")
    return text[: s.end()] + "<defs>" + block + "</defs>" + text[s.end() :]


def embed_file(path, font_dir=None):
    """Embed in place (bytes of line ends kept); -> "" or one warning line for the Report view.  Never raises: on any failure the
    file stays as it was and the line says why."""
    try:
        try:
            import fontTools  # noqa: F401
        except ImportError:
            return "fontTools is not installed: the fonts are not embedded and the file is written as the stock export"
        with open(path, encoding="utf-8", newline="") as fh:
            text = fh.read()
        new, report = embed(text, font_dir)
        if new != text:
            # write beside the file and rename, so a failure mid-write never truncates the export
            tmp = path + ".fonts.tmp"
            try:
                with open(tmp, "w", encoding="utf-8", newline="") as fh:
                    fh.write(new)
                os.replace(tmp, path)
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
        return report["warning"]
    except Exception as e:  # the export must never fail because of the fonts
        return "fonts not embedded (%s: %s)" % (type(e).__name__, e)
