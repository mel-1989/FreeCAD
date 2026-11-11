# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *   Copyright (c) 2026 the FreeCAD contributors                           *
# *                                                                         *
# *   This file is part of FreeCAD.                                         *
# *                                                                         *
# *   FreeCAD is free software: you can redistribute it and/or modify it    *
# *   under the terms of the GNU Lesser General Public License as           *
# *   published by the Free Software Foundation, either version 2.1 of the  *
# *   License, or (at your option) any later version.                       *
# *                                                                         *
# *   FreeCAD is distributed in the hope that it will be useful, but        *
# *   WITHOUT ANY WARRANTY; without even the implied warranty of            *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU     *
# *   Lesser General Public License for more details.                       *
# *                                                                         *
# *   You should have received a copy of the GNU Lesser General Public      *
# *   License along with FreeCAD.  If not, see                              *
# *   <https://www.gnu.org/licenses/>.                                      *
# *                                                                         *
# ***************************************************************************

"""Layered DXF export of a TechDraw page (G04 slice 7).

``write(page, path, standard=None)`` writes a DXF R2013 file with ezdxf: role layers (VISIBLE, HIDDEN, CENTER, COSMETIC,
DIMENSIONS, TEXT), line types computed from the drawing standard's CSV files in ``Mod/TechDraw/LineGroup`` and the line
group's widths, each edge once, DIMENSION entities with rendered blocks, MTEXT for annotations and UTF-8 text.  It returns a
report; ``report["skipped"]`` names every page object that the file cannot carry yet.

``export_page(page, path)`` is what the GUI routes call: the layered writer when the preference ``LayeredDxf``
(User parameter Mod/TechDraw/General, default true) is on and ezdxf imports, else the stock ``TechDraw.writeDXFPage`` with a
warning.  The stock writer and ``TechDraw.writeDXFView`` are not changed.

Public sources: the AutoCAD DXF Reference (group codes, linetype and MTEXT formatting codes, DIMENSION entity types), the
documentation of ezdxf 1.4.2, the line-type CSV files shipped in ``Mod/TechDraw/LineGroup`` (ISO 128-20, ANSI Y14.2M, ASME
Y14.2 element and line definitions, ``LineGroup.csv`` widths) and FreeCAD's stock writer ``TechDraw.writeDXFPage``
(``App/AppTechDrawPy.cpp``) for the page placement of views and dimensions.
"""

import codecs
import html.parser
import math
import os
import re

import FreeCAD as App

LAYERS = ("VISIBLE", "HIDDEN", "CENTER", "COSMETIC", "DIMENSIONS", "TEXT")
TEXT_STYLE = "TECHDRAW"
DIM_STYLE = "TECHDRAW"
VALID_LINEWEIGHTS = (0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60, 70, 80, 90, 100, 106, 120, 140, 158, 200, 211)
HIDDEN_LINE = 2  # the hidden line's number in every standard's LineDef file
DUP_TOL = 1e-6
SPLINE_TOL = 0.002  # discretisation deflection, well inside the 0.01 mm the brief allows
SMOOTH_NOTE = "smooth, seam and iso edges are not separable in Python and are written to VISIBLE or HIDDEN with the other edges"


# ---------------------------------------------------------------------------------------------------------------------
# preferences and the standard's data files
# ---------------------------------------------------------------------------------------------------------------------


def _group(name):
    return App.ParamGet("User parameter:BaseApp/Preferences/Mod/TechDraw/" + name)


def layered_enabled():
    return _group("General").GetBool("LayeredDxf", True)


def _resource_dir():
    return os.path.join(App.getResourceDir(), "Mod", "TechDraw", "LineGroup")


def standard_files():
    """-> the standards' file stems, sorted as ``LineGenerator::getAvailableLineStandards`` lists them: the names of the
    ``*.LineDef.csv`` files in ``Mod/TechDraw/LineGroup`` without that suffix (index = the ``LineStandard`` preference)."""
    suffix = ".LineDef.csv"
    try:
        names = os.listdir(_resource_dir())
    except OSError:
        names = []
    return sorted(n[: -len(suffix)] for n in names if n.endswith(suffix))


def standard_body(stem):
    """"ISO.128.20.1996" -> "ISO": the body name that TechDraw tests (``ANSI`` is the one non-proportional standard)."""
    return stem.split(".")[0].upper()


def standard_names():
    """-> {body name: file stem} of the standards found."""
    return {standard_body(s): s for s in standard_files()}


def line_standard():
    """-> the body name (``"ANSI"``, ``"ASME"``, ``"ISO"``) of the standard at the ``LineStandard`` preference index into
    ``standard_files()``; index 1 (ASME) when unset or out of range (ruling 84)."""
    stems = standard_files()
    index = _group("Standards").GetInt("LineStandard", 1)
    if not 0 <= index < len(stems):
        index = 1
    return standard_body(stems[index])


def center_line_number():
    """TechDraw's number for a new centre line: the ``LineStyleCenter`` preference (default 4) plus one (Preferences.cpp)."""
    return _group("Decorations").GetInt("LineStyleCenter", 4) + 1


def _csv_rows(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line[0] in "#;":
                continue
            rows.append([c.strip() for c in line.split(",")])
    return rows


def load_line_group():
    """-> dict(name, thin, graphic, thick, extra) in mm for the Decorations/LineGroup preference."""
    index = _group("Decorations").GetInt("LineGroup", 3)
    default = os.path.join(_resource_dir(), "LineGroup.csv")
    path = _group("Files").GetString("LineGroupFile", default) or default
    if not os.path.isfile(path):
        path = default
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line[0] == ";":
                continue
            cells = [c.strip() for c in line.split(",")]
            if len(cells) >= 5:
                rows.append(cells)
    if not 0 <= index < len(rows):
        index = min(3, len(rows) - 1)
    r = rows[index]
    return {"name": r[0].lstrip("*"), "thin": float(r[1]), "graphic": float(r[2]), "thick": float(r[3]), "extra": float(r[4])}


def load_standard(std):
    """-> (elements {name: length}, lines {number: (name, [element names] | None | "noline")}) from the CSV files."""
    base = os.path.join(_resource_dir(), standard_names()[std])
    elements = {}
    for row in _csv_rows(base + ".ElementDef.csv"):
        elements[row[0]] = float(row[1])
    lines = {}
    for row in _csv_rows(base + ".LineDef.csv"):
        spec = row[2:]
        if spec == ["n/a"]:
            els = None
        elif spec == ["noline"]:
            els = "noline"
        else:
            els = spec
        lines[int(row[0])] = (row[1], els)
    return elements, lines


def _clean(x):
    return round(x + 0.0, 9)


def line_pattern(std, number, width):
    """The DXF dash pattern ``[total, e1, e2, ...]`` of line ``number`` under ``std`` at pen ``width`` mm, or None (continuous).

    Dash and long dash are positive, gap and space negative, a dot a zero-length element; ISO and ASME lengths are the CSV's
    pen-width numbers times ``width``, ANSI lengths are the CSV's mm (``LineGenerator::getLinePen``, ruling 85)."""
    elements, lines = load_standard(std)
    if number not in lines:
        return None
    _name, els = lines[number]
    if els is None or els == "noline":
        return None
    out = []
    for name in els:
        length = elements[name] * (1.0 if std == "ANSI" else width)
        if name == "Dot":
            out.append(0.0)
        elif name in ("Gap", "Space"):
            out.append(_clean(-length))
        else:
            out.append(_clean(length))
    return [_clean(sum(abs(v) for v in out))] + out


def lineweight(mm):
    """Nearest valid DXF lineweight (hundredths of mm) for ``mm``."""
    hundredths = mm * 100.0
    return min(VALID_LINEWEIGHTS, key=lambda v: (abs(v - hundredths), v))


class _Types:
    """Line types of one export, created on demand in the document and named for standard, line number and width."""

    def __init__(self, doc, std, thin):
        self.doc, self.std, self.thin = doc, std, thin
        self.elements, self.lines = load_standard(std)
        self.noline = set()

    def name(self, number, width):
        """Name of the (created) LTYPE for line ``number`` at ``width``; "CONTINUOUS" when continuous; None for "noline"."""
        if number not in self.lines:
            return None
        _n, els = self.lines[number]
        if els is None:
            return "CONTINUOUS"
        if els == "noline":
            return None
        if self.std == "ANSI":
            name = "%s_%02d" % (self.std, number)
        else:
            name = "%s_%02d_%s" % (self.std, number, ("%.3f" % width).rstrip("0").rstrip("."))
        if name not in self.doc.linetypes:
            self.doc.linetypes.add(
                name, pattern=line_pattern(self.std, number, width), description="%s %s" % (self.std, self.lines[number][0])
            )
        return name


# ---------------------------------------------------------------------------------------------------------------------
# page walk
# ---------------------------------------------------------------------------------------------------------------------


def _is(o, type_id):
    return o.isDerivedFrom(type_id)


def _val(x):
    return float(getattr(x, "Value", x))


def walk(page):
    """-> (views, group_of): every view below the page once, in page order (containers before their children), and
    {view name: its projection group}."""
    views, group_of, seen = [], {}, set()

    def add(o, group=None):
        if o.Name in seen:
            return
        seen.add(o.Name)
        views.append(o)
        if group is not None:
            group_of[o.Name] = group
        if o.TypeId == "TechDraw::DrawProjGroup":
            for it in o.Views:
                add(it, o)
        elif _is(o, "TechDraw::DrawViewCollection") or o.TypeId == "TechDraw::DrawViewClip":
            for it in getattr(o, "Views", []):
                add(it)

    for o in page.Views:
        add(o)
    return views, group_of


def page_position(view, group_of):
    """The page position of ``view`` as stock ``writeDXFPage`` places it: its X, Y plus its projection group's."""
    x, y = _val(view.X), _val(view.Y)
    g = group_of.get(view.Name)
    if g is not None:
        x += _val(g.X)
        y += _val(g.Y)
    return x, y


# ---------------------------------------------------------------------------------------------------------------------
# edges
# ---------------------------------------------------------------------------------------------------------------------


def _pt(v, off):
    return (v.x + off[0], v.y + off[1])


def _angle(c, p):
    return math.degrees(math.atan2(p[1] - c[1], p[0] - c[0])) % 360.0


def edge_to_geom(edge, off):
    """-> ("L", p, q) | ("C", c, r) | ("A", c, r, a0, a1, s, e) | ("E", c, major, ratio, t0, t1, s, e) | ("S", points) | None."""
    if edge.Length < 1e-9:
        return None
    curve = edge.Curve
    kind = type(curve).__name__
    first, last = edge.FirstParameter, edge.LastParameter
    s, e = _pt(edge.valueAt(first), off), _pt(edge.valueAt(last), off)
    if kind in ("Line", "LineSegment"):
        return ("L", s, e)
    closed = math.hypot(s[0] - e[0], s[1] - e[1]) < 1e-7 and edge.Length > 1e-6
    if kind == "Circle":
        c = _pt(curve.Center, off)
        r = curve.Radius
        if closed:
            return ("C", c, r)
        m = _pt(edge.valueAt(0.5 * (first + last)), off)
        a0, a1, am = _angle(c, s), _angle(c, e), _angle(c, m)
        if (am - a0) % 360.0 > (a1 - a0) % 360.0:  # the edge runs clockwise: swap the ends
            a0, a1, s, e = a1, a0, e, s
        return ("A", c, r, a0, a1, s, e)
    if kind == "Ellipse":
        c = _pt(curve.Center, off)
        xa = curve.XAxis
        xa.normalize()
        major = (xa.x * curve.MajorRadius, xa.y * curve.MajorRadius)
        ratio = curve.MinorRadius / curve.MajorRadius
        if closed:
            return ("E", c, major, ratio, 0.0, 2.0 * math.pi, s, s)
        t0, t1 = first, last
        if curve.Axis.z < 0:  # the local frame is mirrored: parameters run the other way in the DXF frame
            t0, t1 = -last, -first
            s, e = e, s
        while t1 <= t0:
            t1 += 2.0 * math.pi
        return ("E", c, major, ratio, t0, t1, s, e)
    pts = [_pt(p, off) for p in edge.discretize(Deflection=SPLINE_TOL)]
    if len(pts) < 2:
        return None
    return ("S", pts)


def _key_points(g):
    if g[0] == "L":
        return [g[1], g[2]]
    if g[0] == "S":
        pts = g[1]
        return [pts[0], pts[len(pts) // 2], pts[-1]]
    if g[0] == "C":
        return [g[1], (g[2], 0.0)]
    if g[0] == "A":
        return [g[1], (g[2], 0.0), g[5], g[6]]
    return [g[1], g[2], (g[3], 0.0), g[6], g[7]]


def _same(a, b, tol=DUP_TOL):
    if a[0] != b[0]:
        return False
    pa, pb = _key_points(a), _key_points(b)
    if a[0] in ("L", "S"):
        orders = (pb, pb[::-1])
    else:
        orders = (pb,)
    for q in orders:
        if all(abs(u[0] - v[0]) <= tol and abs(u[1] - v[1]) <= tol for u, v in zip(pa, q)):
            return True
    return False


class _Dedup:
    """Geometry duplicates within 1e-6 mm: a hash grid of the key points' bounding-box centres, 3x3 cells searched."""

    CELL = 0.01

    def __init__(self):
        self.cells = {}
        self.dropped = 0

    def _cell(self, g):
        pts = _key_points(g)
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return (g[0], int(round((min(xs) + max(xs)) * 0.5 / self.CELL)), int(round((min(ys) + max(ys)) * 0.5 / self.CELL)))

    def add(self, g):
        """True when ``g`` is new (and remembered), False for a duplicate."""
        k, cx, cy = self._cell(g)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for other in self.cells.get((k, cx + dx, cy + dy), ()):
                    if _same(g, other):
                        self.dropped += 1
                        return False
        self.cells.setdefault((k, cx, cy), []).append(g)
        return True


def _add_geom(msp, g, attribs):
    k = g[0]
    if k == "L":
        return msp.add_line(g[1], g[2], dxfattribs=attribs)
    if k == "C":
        return msp.add_circle(g[1], g[2], dxfattribs=attribs)
    if k == "A":
        return msp.add_arc(g[1], g[2], g[3], g[4], dxfattribs=attribs)
    if k == "E":
        return msp.add_ellipse(g[1], major_axis=g[2], ratio=g[3], start_param=g[4], end_param=g[5], dxfattribs=attribs)
    pts = g[1]
    if len(pts) < 4:
        return msp.add_line(pts[0], pts[-1], dxfattribs=attribs) if len(pts) == 2 else msp.add_spline(fit_points=pts, dxfattribs=attribs)
    from ezdxf.math import global_bspline_interpolation

    bs = global_bspline_interpolation(pts, degree=3)
    return msp.add_open_spline(control_points=list(bs.control_points), degree=3, knots=list(bs.knots()), dxfattribs=attribs)


def _rgb(color):
    """True colour (int) of a (r, g, b, a) float tuple, or None for black (the layer colour)."""
    r, g, b = (int(round(c * 255)) for c in color[:3])
    if (r, g, b) == (0, 0, 0):
        return None
    return (r << 16) | (g << 8) | b


# ---------------------------------------------------------------------------------------------------------------------
# text
# ---------------------------------------------------------------------------------------------------------------------


def mtext_escape(s):
    return s.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def annotation_lines(view):
    """The lines of a DrawViewAnnotation as the GUI draws them (legacy ``\\x`` escapes decoded as the C++ does)."""
    out = []
    for line in view.Text:
        if "\\x" in line:
            try:
                line = codecs.decode(line.encode("utf-8"), "unicode_escape")
            except Exception:
                pass
        out.append(line)
    return out


_WS = re.compile(r"[ \t\r\n\f]+")


class _BodyParser(html.parser.HTMLParser):
    """Collects the paragraphs of the ``body`` of a Qt rich-text document: each ``<p>`` a paragraph, each ``<br>`` a break."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.paras = []  # list of lists of text runs, "\n" marks a break
        self.cur = None
        self.skip = 0
        self.in_body = False
        self.has_body = False

    def _para(self):
        if self.cur is None:
            self.cur = []
            self.paras.append(self.cur)
        return self.cur

    def handle_starttag(self, tag, attrs):
        if tag in ("head", "style", "script", "title"):
            self.skip += 1
        elif tag == "body":
            self.in_body = True
        elif tag in ("p", "tr"):
            self.cur = []
            self.paras.append(self.cur)
        elif tag in ("td", "th"):
            pass  # ruling 81: other tags are removed and add nothing (cells of one row join as written)
        elif tag == "br":
            self._para().append("\n")

    def handle_startendtag(self, tag, attrs):
        if tag in ("br", "p"):
            self.handle_starttag(tag, attrs)
            if tag == "p":
                self.cur = None

    def handle_endtag(self, tag):
        if tag in ("head", "style", "script", "title"):
            self.skip = max(0, self.skip - 1)
        elif tag in ("p", "tr"):
            self.cur = None

    def handle_data(self, data):
        if self.skip:
            return
        if self.cur is None and not data.strip(" \t\r\n\f"):
            return  # white space between block elements makes no paragraph
        self._para().append(data)


def rich_text_to_mtext(anno_text):
    """The MTEXT string of a DrawRichAnno's HTML (ruling 81): body only, each paragraph and each break a ``\\P``, other tags
    removed, entities decoded, ASCII white-space runs collapsed to one space and trimmed at paragraph ends, U+00A0 kept."""
    p = _BodyParser()
    p.feed(anno_text)
    p.close()
    paras = []
    for runs in p.paras:
        parts, buf = [], []
        for r in runs:
            if r == "\n":
                parts.append("".join(buf))
                buf = []
            else:
                buf.append(r)
        parts.append("".join(buf))
        parts = [_WS.sub(" ", t) for t in parts]
        parts[0] = parts[0].lstrip(" ")
        parts[-1] = parts[-1].rstrip(" ")
        paras.append("\\P".join(mtext_escape(t) for t in parts))
    return "\\P".join(paras)


def left_aligned(text):
    """``text`` (MTEXT, lines joined by \\P) with a left-alignment paragraph code when it has more than one line."""
    if "\\P" not in text:
        return text
    return "\\pxql;" + text


def _tolerance_value(spec, value):
    """The GUI's tolerance string for ``value`` under FormatSpec ``spec`` (``%+.2w``: printf with trailing zeros cut)."""
    m = re.match(r"^(.*?)(%[+\- ]?[0-9]*\.?[0-9]*[aefgwrAEFGWR])(.*)$", spec, re.S)
    if not m:
        return spec
    pre, f, post = m.groups()
    conv = f[-1].lower()
    if conv == "w":
        s = (f[:-1] + "f") % value
        if "." in s:
            s = s.rstrip("0")
            if s.endswith("."):
                s = s[:-1]
    elif conv == "r":
        rounder = float(f[1:-1].lstrip("+- ") or 1)
        decimals = len(re.sub(r"0+$", "", f[1:-1].split(".")[1])) if "." in f[1:-1] else 0
        s = ("%." + str(decimals) + "f") % (round(value / rounder) * rounder)
    else:
        s = f % value
    return pre + s + post


def dimension_text(dim):
    """-> (text, exact_frame): ``getText()`` plus, for unequal or one-sided tolerances, an MTEXT stack ``\\S+a^-b;``."""
    text = dim.getText()
    exact = bool(getattr(dim, "TheoreticalExact", False))
    if exact or getattr(dim, "Arbitrary", False) or getattr(dim, "EqualTolerance", True):
        return text, exact
    arb = getattr(dim, "ArbitraryTolerances", False)
    over, under = _val(dim.OverTolerance), _val(dim.UnderTolerance)
    if not arb and over == 0.0 and under == 0.0:
        return text, exact
    if arb:
        o, u = dim.FormatSpecOverTolerance, dim.FormatSpecUnderTolerance
    else:
        o = _tolerance_value(dim.FormatSpecOverTolerance, over)
        u = _tolerance_value(dim.FormatSpecUnderTolerance, under)
    # QGIDatumLabel::setToleranceString: a zero side loses its sign (the first character becomes a space)
    if under == 0.0 and u[:1] in ("+", "-"):
        u = " " + u[1:]
    if over == 0.0 and o[:1] in ("+", "-"):
        o = " " + o[1:]
    return text + "\\S" + mtext_escape(o).replace("^", "\\^") + "^" + mtext_escape(u).replace("^", "\\^") + ";", exact


# ---------------------------------------------------------------------------------------------------------------------
# the writer
# ---------------------------------------------------------------------------------------------------------------------


class _Run:
    """State of one export."""

    def __init__(self, page, std):
        self.page = page
        self.std = std
        self.group = load_line_group()
        self.thin = self.group["thin"]
        self.thick = self.group["thick"]
        self.skipped = []
        self.notes = [SMOOTH_NOTE]
        self.counts = {}
        self.dups = 0
        self.edges = {"VISIBLE": 0, "HIDDEN": 0, "COSMETIC": 0, "CENTER": 0}

    def skip(self, obj, reason):
        self.skipped.append({"name": obj.Name, "label": obj.Label, "type": obj.TypeId, "reason": reason})

    def count(self, entity):
        k = entity.dxftype()
        self.counts[k] = self.counts.get(k, 0) + 1


def _new_document(run):
    import ezdxf

    doc = ezdxf.new("R2013", setup=False, units=4)
    h = doc.header
    h["$MEASUREMENT"] = 1
    h["$LTSCALE"] = 1.0
    h["$CELTSCALE"] = 1.0
    h["$LWDISPLAY"] = 1
    types = _Types(doc, run.std, run.thin)
    hidden = types.name(HIDDEN_LINE, run.thin) or "CONTINUOUS"
    center = types.name(center_line_number(), run.thin) or "CONTINUOUS"
    spec = {
        "VISIBLE": ("CONTINUOUS", run.thick),
        "HIDDEN": (hidden, run.thin),
        "CENTER": (center, run.thin),
        "COSMETIC": ("CONTINUOUS", run.thin),
        "DIMENSIONS": ("CONTINUOUS", run.thin),
        "TEXT": ("CONTINUOUS", run.thin),
    }
    for name in LAYERS:
        lt, w = spec[name]
        doc.layers.add(name, color=7, linetype=lt, lineweight=lineweight(w))
    doc.styles.add(TEXT_STYLE, font="osifont.ttf")
    doc.dimstyles.new(
        DIM_STYLE,
        dxfattribs={"dimtxt": 3.5, "dimasz": 3.5, "dimexe": 1.5, "dimexo": 1.0, "dimgap": 1.0, "dimtad": 1, "dimtxsty": TEXT_STYLE},
    )
    return doc, types


def _view_edges(run, msp, types, view, off, dedupe):
    """Visible, hidden, cosmetic and centre-line edges of a DrawViewPart."""
    vis = list(view.getVisibleEdges(True))
    hid = list(view.getHiddenEdges(True))
    if not vis and not hid:  # never silent: a view with no source shape or an empty projection draws nothing
        run.skip(view, "view has no edges to write (no source shape or an empty projection)")
        return
    cosmetic = list(view.CosmeticEdges)
    centers = list(view.CenterLines)
    n_cos = len([c for c in cosmetic if c.Format.get("visible", True)])
    n_cen = len([c for c in centers if c.Format.get("visible", True)])
    n_geo = max(0, len(vis) - n_cos - n_cen)
    layers = {"VISIBLE": vis[:n_geo], "HIDDEN": hid}
    for layer, edges in layers.items():
        dd = dedupe.setdefault(layer, _Dedup())
        for e in edges:
            g = edge_to_geom(e, off)
            if g is None or not dd.add(g):
                continue
            run.count(_add_geom(msp, g, {"layer": layer}))
            run.edges[layer] += 1
    tail = vis[n_geo:]
    items = [("COSMETIC", c) for c in cosmetic if c.Format.get("visible", True)] + [("CENTER", c) for c in centers if c.Format.get("visible", True)]
    for (layer, obj), e in zip(items, tail):
        g = edge_to_geom(e, off)
        if g is None:
            continue
        fmt = obj.Format
        w = float(fmt.get("weight", run.thin)) or run.thin
        attribs = {"layer": layer, "lineweight": lineweight(w)}
        if layer == "COSMETIC":
            number = int(fmt.get("lineNumber", 1))
            if number not in types.lines:
                run.notes.append("%s: cosmetic edge line number %d is not in the %s table, written continuous" % (view.Name, number, run.std))
                lt = "CONTINUOUS"
            else:
                lt = types.name(number, w)
            if lt is None:
                run.skip(view, "cosmetic edge on %s line %d draws no line" % (run.std, number))
                continue
            attribs["linetype"] = lt
        elif abs(w - run.thin) > 1e-9:
            lt = types.name(center_line_number(), w)
            if lt:
                attribs["linetype"] = lt
        rgb = _rgb(fmt.get("color", (0, 0, 0, 1)))
        if rgb is not None:
            attribs["true_color"] = rgb
        dd = dedupe.setdefault(layer, _Dedup())
        if not dd.add(g):
            continue
        run.count(_add_geom(msp, g, attribs))
        run.edges[layer] += 1


def _flip(p, off):
    """A view-coordinate point (Y down, scaled) in page mm."""
    return (p.x + off[0], -p.y + off[1])


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def _dim_entity(run, msp, dim, parent_off, text, text_loc, attribs, override):
    t = dim.Type
    if t in ("Distance", "DistanceX", "DistanceY"):
        p = dim.getLinearPoints()
        p1, p2 = _flip(p[0], parent_off), _flip(p[1], parent_off)
        if math.hypot(*_sub(p1, p2)) < 1e-9:
            raise ValueError("the measured points coincide")
        if t == "Distance":
            d = _sub(p2, p1)
            n = math.hypot(*d)
            normal = (-d[1] / n, d[0] / n)
            distance = (text_loc[0] - p1[0]) * normal[0] + (text_loc[1] - p1[1]) * normal[1]
            ov = msp.add_aligned_dim(p1, p2, distance, text=text, dimstyle=DIM_STYLE, override=override, dxfattribs=attribs)
            ov.set_location(text_loc)  # ruling 80: group 11 is the dimension's page position, as for the other types
            ov.aligned = True
            return ov
        angle = 0 if t == "DistanceX" else 90
        return msp.add_linear_dim(
            text_loc, p1, p2, location=text_loc, text=text, angle=angle, dimstyle=DIM_STYLE, override=override, dxfattribs=attribs
        )
    if t in ("Radius", "Diameter"):
        pts = dim.getArcPoints()
        c = _flip(pts[0], parent_off)
        r = math.hypot(*_sub(_flip(pts[1], parent_off), c))
        if r < 1e-9:
            raise ValueError("zero radius")
        d = _sub(text_loc, c)
        n = math.hypot(*d)
        d = (d[0] / n, d[1] / n) if n > 1e-9 else (1.0, 0.0)
        ang = math.degrees(math.atan2(d[1], d[0]))
        make = msp.add_radius_dim if t == "Radius" else msp.add_diameter_dim
        return make(c, radius=r, angle=ang, location=text_loc, text=text, dimstyle=DIM_STYLE, override=override, dxfattribs=attribs)
    if t in ("Angle", "Angle3Pt"):
        pts = dim.getAnglePoints()
        a, b, v = (_flip(p, parent_off) for p in pts)
        if t == "Angle":
            return msp.add_angular_dim_2l(
                text_loc, (v, a), (v, b), location=text_loc, text=text, dimstyle=DIM_STYLE, override=override, dxfattribs=attribs
            )
        return msp.add_angular_dim_3p(text_loc, v, a, b, location=text_loc, text=text, dimstyle=DIM_STYLE, override=override, dxfattribs=attribs)
    raise ValueError("unsupported dimension type " + str(t))


SUPPORTED_DIMS = ("Distance", "DistanceX", "DistanceY", "Radius", "Diameter", "Angle", "Angle3Pt")


def _write_dimension(run, msp, dim, group_of):
    refs = dim.References2D
    parent = refs[0][0] if refs else None
    if parent is None:
        run.skip(dim, "dimension without a 2D reference view")
        return
    if dim.Type not in SUPPORTED_DIMS:
        run.skip(dim, "dimension type %s is not written" % dim.Type)
        return
    if not dim.isValid():
        run.skip(dim, "dimension is not valid (references no longer match)")
        return
    off = page_position(parent, group_of)
    text, exact = dimension_text(dim)
    text_loc = (_val(dim.X) + off[0], _val(dim.Y) + off[1])
    override = {}
    if hasattr(dim, "Fontsize") and _val(dim.Fontsize) > 0:
        override["dimtxt"] = _val(dim.Fontsize)
    if hasattr(dim, "Arrowsize") and _val(dim.Arrowsize) > 0:
        override["dimasz"] = _val(dim.Arrowsize)
    before = len(msp)
    try:
        ov = _dim_entity(run, msp, dim, off, text, text_loc, {"layer": "DIMENSIONS"}, override)
        ov.render()
        _mtext_direction(msp.doc.blocks.get(ov.dimension.dxf.geometry))
        if getattr(ov, "aligned", False):  # ezdxf builds an aligned dimension as type 0 plus the line's angle; the type is the DXF one
            ov.dimension.dxf.dimtype = (ov.dimension.dxf.dimtype & ~7) | 1
    except Exception as ex:  # a dimension ezdxf cannot build is named, not lost
        for e in list(msp)[before:]:
            msp.delete_entity(e)
        run.skip(dim, "dimension not written: %s" % ex)
        return
    run.count(ov.dimension)
    if exact:
        run.skip(dim, "theoretically exact frame around the dimension is not drawn (the dimension is written)")


def _text_height(v, default=3.5):
    h = _val(v)
    return h if h > 0 else default


def _write_annotation(run, msp, view):
    from ezdxf.enums import MTextEntityAlignment

    lines = [mtext_escape(s) for s in annotation_lines(view)]
    if not lines:
        return
    text = left_aligned("\\P".join(lines))
    attribs = {"layer": "TEXT", "style": TEXT_STYLE, "char_height": _text_height(view.TextSize)}
    angle = _val(view.Rotation)
    if abs(angle) > 1e-12:
        # rotation as the text direction (group 11), not group 50: both are valid DXF, and libredwg rejects group 50 on MTEXT
        rad = math.radians(angle)
        attribs["text_direction"] = (math.cos(rad), math.sin(rad), 0.0)
    rgb = _rgb(tuple(view.TextColor))
    if rgb is not None:
        attribs["true_color"] = rgb
    mt = msp.add_mtext(text, dxfattribs=attribs)
    mt.set_location((_val(view.X), _val(view.Y)), attachment_point=int(MTextEntityAlignment.MIDDLE_CENTER))
    width = _val(view.MaxWidth) if hasattr(view, "MaxWidth") else -1.0
    if width > 0:
        mt.dxf.width = width
    run.count(mt)


def _is_pmi_frame(view):
    """A PmiDrawing annotation: a non-empty ``PmiRole`` or a ``<table>`` frame in the HTML (ruling 82)."""
    if "PmiRole" in view.PropertiesList and view.PmiRole:
        return True
    return re.search(r"<table[\s>/]", view.AnnoText, re.I) is not None


def _write_rich(run, msp, view, group_of, view_names):
    if _is_pmi_frame(view):
        run.skip(view, "PMI frame not drawn (its text is written)")
    text = rich_text_to_mtext(view.AnnoText)
    if not text:
        return
    x, y = _val(view.X), _val(view.Y)
    parent = getattr(view, "AnnoParent", None)
    if parent is not None:
        px, py = page_position(parent, group_of)
        x, y = x + px, y + py
    centred = bool(view.OriginCentered)
    attach = 5 if centred else 1
    mt = msp.add_mtext(left_aligned(text), dxfattribs={"layer": "TEXT", "style": TEXT_STYLE, "char_height": 3.5})
    mt.set_location((x, y), attachment_point=attach)
    run.count(mt)
    if getattr(view, "ShowFrame", False):
        run.skip(view, "rich annotation frame is not drawn (the text is written)")


SKIP_REASONS = (
    ("TechDraw::DrawViewBalloon", "balloon (bubble and leader) is not written"),
    ("TechDraw::DrawLeaderLine", "leader line is not written"),
    ("TechDraw::DrawViewSpreadsheet", "table or parts list (spreadsheet view) is not written"),
    ("TechDraw::DrawWeldSymbol", "weld symbol is not written"),
    ("TechDraw::DrawViewClip", "clip group is not written"),
    ("TechDraw::DrawViewSymbol", "symbol view is not written"),
    ("TechDraw::DrawViewImage", "image view is not written"),
)


def _mtext_direction(block):
    """Rotated MTEXT inside a rendered dimension block carries its rotation as a text direction (group 11), not group 50,
    as the annotations do: both are valid DXF, and libredwg's dxf2dwg rejects group 50 on MTEXT (run 095 verifier)."""
    if block is None:
        return
    for e in block:
        if e.dxftype() == "MTEXT" and e.dxf.hasattr("rotation"):
            rad = math.radians(e.dxf.rotation)
            e.dxf.discard("rotation")
            if abs(rad) > 1e-12:
                e.dxf.text_direction = (math.cos(rad), math.sin(rad), 0.0)


_ANG3P_16 = re.compile(r"(AcDb3PointAngularDimension\n(?: 1[345]\n[^\n]*\n 2[345]\n[^\n]*\n 3[345]\n[^\n]*\n)+) 16\n[^\n]*\n 26\n[^\n]*\n 36\n[^\n]*\n")


def _drop_3p_arc_point(path):
    """The AutoCAD DXF Reference gives a 3-point angular dimension (AcDb3PointAngularDimension) the groups 13, 14 and 15,
    and the arc point 16 only to the 2-line angular one; ezdxf always writes 16 on both, and libredwg's dxf2dwg rejects
    it on the 3-point one (run 095). The arc's location stays in group 10."""
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    new = _ANG3P_16.sub(r"\1", text)
    if new != text:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(new)


def _page_size(page):
    """The sheet's width and height in mm, or None for a page without a usable template."""
    try:
        return _val(page.PageWidth), _val(page.PageHeight)
    except Exception:
        return None


def _classify(run, msp, types, views, group_of):
    dedupe_by_view = {}
    names = {v.Name for v in views}
    for v in views:
        tid = v.TypeId
        if tid == "TechDraw::DrawProjGroup":
            continue
        if _is(v, "TechDraw::DrawViewPart"):
            off = page_position(v, group_of)
            _view_edges(run, msp, types, v, off, dedupe_by_view.setdefault(v.Name, {}))
            if _is(v, "TechDraw::DrawViewSection"):
                run.skip(v, "section line symbol and cut-face fill are not written")
            elif _is(v, "TechDraw::DrawViewDetail"):
                run.skip(v, "detail highlight circle and label are not written")
            if hasattr(v, "CosmeticVertexes") and len(v.CosmeticVertexes):
                run.skip(v, "cosmetic vertices are not written")
        elif _is(v, "TechDraw::DrawViewDimension"):
            _write_dimension(run, msp, v, group_of)
        elif _is(v, "TechDraw::DrawViewAnnotation"):
            _write_annotation(run, msp, v)
        elif _is(v, "TechDraw::DrawRichAnno"):
            _write_rich(run, msp, v, group_of, names)
        else:
            for t, why in SKIP_REASONS:
                if _is(v, t):
                    run.skip(v, why)
                    break
            else:
                run.skip(v, "object type %s is not written" % tid)
    page = run.page
    tpl = getattr(page, "Template", None)
    if tpl is not None:
        run.skip(tpl, "template and title block are not written")
    for o in page.Document.Objects:
        if o.TypeId in ("TechDraw::DrawHatch", "TechDraw::DrawGeomHatch"):
            src = o.Source
            owner = src[0] if isinstance(src, tuple) else None
            if owner is not None and owner.Name in names:
                run.skip(o, "hatch is not written")
    run.dups = sum(d.dropped for dd in dedupe_by_view.values() for d in dd.values())


EXTENTS_MARGIN = 1.0  # mm


def _extents(doc, msp, page):
    from ezdxf import bbox

    box = bbox.BoundingBox()
    for e in msp:
        if e.dxftype() == "DIMENSION":  # its drawn extent is the rendered block's
            block = doc.blocks.get(e.dxf.geometry)
            part = bbox.extents(block) if block is not None else bbox.BoundingBox()
        else:
            part = bbox.extents([e])
        if part.has_data:
            box.extend([part.extmin, part.extmax])
    if box.has_data:
        lo, hi = box.extmin, box.extmax
        # 1 mm beyond the drawn box: LibreOffice's DXF import clips a line lying exactly on $EXTMAX (run 095, a
        # rectangle's right edge vanished); the extents still enclose the drawing, which is all the DXF Reference asks
        lo = (lo.x - EXTENTS_MARGIN, lo.y - EXTENTS_MARGIN, 0.0)
        hi = (hi.x + EXTENTS_MARGIN, hi.y + EXTENTS_MARGIN, 0.0)
    else:
        size = _page_size(page) or (0.0, 0.0)
        lo, hi = (0.0, 0.0, 0.0), (size[0], size[1], 0.0)
    if lo == (0.0, 0.0, 0.0):  # ezdxf treats an extmin of exactly (0, 0, 0) as unset
        lo = (-1e-6, -1e-6, 0.0)
    msp.reset_extents(lo, hi)
    size = _page_size(page)
    if size is not None:
        msp.dxf.limmin = (0.0, 0.0)
        msp.dxf.limmax = size


def _warn(msg):
    App.Console.PrintWarning(msg + "\n")


def write(page, path, standard=None):
    """Write ``page`` as a layered DXF R2013 file at ``path``; returns the report (a dict, ``report["skipped"]`` the list of
    objects not written, each with ``name`` and ``reason``)."""
    import ezdxf

    std = str(standard or line_standard()).upper()
    if std not in standard_names():
        raise ValueError("standard must be one of %s, not %r" % (", ".join(sorted(standard_names())), standard))
    run = _Run(page, std)
    # A file must be a function of the page alone: fixed dates, GUIDs and metadata, restored afterwards so no ezdxf global
    # outlives this call.
    previous = ezdxf.options.write_fixed_meta_data_for_testing
    ezdxf.options.write_fixed_meta_data_for_testing = True
    try:
        doc, types = _new_document(run)
        msp = doc.modelspace()
        views, group_of = walk(page)
        _classify(run, msp, types, views, group_of)
        _extents(doc, msp, page)
        _order_classes(doc)
        tmp = str(path) + ".tmp"
        doc.saveas(tmp)
        _drop_3p_arc_point(tmp)
        os.replace(tmp, str(path))
    finally:
        ezdxf.options.write_fixed_meta_data_for_testing = previous
    report = {
        "path": str(path),
        "standard": std,
        "line_group": run.group["name"],
        "layers": list(LAYERS),
        "entities": dict(run.counts),
        "edges": dict(run.edges),
        "duplicates_dropped": run.dups,
        "skipped": run.skipped,
        "notes": run.notes,
    }
    if run.skipped:
        seen, items = set(), []
        for s in run.skipped:
            if s["name"] not in seen:
                seen.add(s["name"])
                items.append("%s (%s)" % (s["name"], "; ".join(x["reason"] for x in run.skipped if x["name"] == s["name"])))
        _warn("DXF export of %s: not written to %s: %s" % (page.Name, os.path.basename(str(path)), ", ".join(items)))
    return report


def _order_classes(doc):
    """Register the CLASSES entries now and sort them, so the section does not depend on the process's hash seed."""
    from collections import OrderedDict

    doc.classes.add_required_classes(doc.dxfversion)
    doc.classes.classes = OrderedDict(sorted(doc.classes.classes.items(), key=lambda kv: kv[0]))


def export_page(page, path):
    """The routine the GUI calls: layered when the ``LayeredDxf`` preference is on and ezdxf imports, else the stock writer."""
    import TechDraw

    why = None
    if not layered_enabled():
        why = "the preference LayeredDxf is off"
    else:
        try:
            import ezdxf  # noqa: F401
        except ImportError:
            why = "ezdxf is not installed"
    if why is None:
        return write(page, path)
    _warn("DXF export of %s: %s, so the stock writer was used (no line types or layers by role, hidden edges as visible)" % (page.Name, why))
    TechDraw.writeDXFPage(page, path)
    return None
