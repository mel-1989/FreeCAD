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

"""Balloon and leader-line geometry for the layered DXF export (G04 slice 7b).

The functions here turn the properties of a ``TechDraw::DrawViewBalloon`` or ``TechDraw::DrawLeaderLine`` (and the view
provider's font, size and line width) into page-millimetre primitives, the way TechDraw's own scene items draw them:
``("line", p, q)``, ``("poly", points, closed)`` with optional bulges, ``("circle", centre, radius)`` and
``("solid", points)``.  Page coordinates are millimetres with the origin at the lower left and +Y up.

Public sources: FreeCAD's ``QGIViewBalloon.cpp`` (bubble shapes, leader and arrow placement), ``QGILeaderLine.cpp`` and
``DrawLeaderLine.cpp`` (start point, waypoint transform, horizontal last segment, line trimming), ``QGIArrow.cpp``
(end symbols), ``Preferences.cpp`` (defaults); fontTools for the glyph advances of the bundled font files.
"""

import math
import os
import re
import xml.etree.ElementTree as ET
import zipfile

import FreeCAD as App

ARROW_NAMES = ("Filled arrow", "Open arrow", "Tick", "Dot", "Open circle", "Fork", "Filled triangle", "None")
UNIT = 0.1  # mm per scene unit
FONT_FILES = {
    "osifont": "osifont-lgpl3fe.ttf",
    "osifont-lgpl3fe": "osifont-lgpl3fe.ttf",
    "osifont-italic": "osifont-italic.ttf",
    "y14.5-2018": "Y14.5-2018.ttf",
    "y14.5-freecad": "Y14.5-FreeCAD.ttf",
}
_FONT_CACHE = {}


def _val(x):
    return float(getattr(x, "Value", x))


def _group(name):
    return App.ParamGet("User parameter:BaseApp/Preferences/Mod/TechDraw/" + name)


# ---------------------------------------------------------------------------------------------------------------------
# view-provider properties
# ---------------------------------------------------------------------------------------------------------------------


def default_format(thin):
    """The defaults of ``ViewProviderBalloon`` / ``ViewProviderLeader`` (headless, no saved GUI document)."""
    return {
        "Font": _group("Labels").GetString("LabelFont", "osifont") or "osifont",
        "Fontsize": _group("Dimensions").GetFloat("FontSize", 5.0),
        "LineWidth": thin,
        "LineVisible": True,
        "Color": None,
        "source": "defaults",
    }


class GuiFormats:
    """The view-provider formats of one document: from the live view objects in a GUI session, else from the
    ``GuiDocument.xml`` stored in the saved file, else the defaults."""

    def __init__(self, doc, thin):
        self.doc = doc
        self.thin = thin
        self.saved = None

    def _load(self):
        if self.saved is not None:
            return self.saved
        self.saved = {}
        path = getattr(self.doc, "FileName", "") or ""
        if path and os.path.isfile(path):
            try:
                with zipfile.ZipFile(path) as z:
                    root = ET.fromstring(z.read("GuiDocument.xml"))
            except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError):
                return self.saved
            for vp in root.iter("ViewProvider"):
                props = {}
                for p in vp.iter("Property"):
                    child = p[0] if len(p) else None
                    if child is None:
                        continue
                    v = child.get("value")
                    name = p.get("name")
                    if name in ("Font",):
                        props[name] = v
                    elif name in ("Fontsize", "LineWidth"):
                        props[name] = float(v)
                    elif name == "LineVisible":
                        props[name] = v == "true"
                    elif name == "Color":
                        packed = int(v) & 0xFFFFFFFF
                        props[name] = ((packed >> 24) / 255.0, ((packed >> 16) & 255) / 255.0, ((packed >> 8) & 255) / 255.0, 1.0)
                self.saved[vp.get("name")] = props
        return self.saved

    def get(self, obj):
        fmt = default_format(self.thin)
        vo = getattr(obj, "ViewObject", None) if getattr(App, "GuiUp", False) else None
        if vo is not None:
            for key in ("Font", "Fontsize", "LineWidth", "LineVisible", "Color"):
                if hasattr(vo, key):
                    v = getattr(vo, key)
                    fmt[key] = tuple(v) if key == "Color" else (_val(v) if key in ("Fontsize", "LineWidth") else v)
            fmt["source"] = "view object"
            return fmt
        saved = self._load().get(obj.Name)
        if saved:
            fmt.update(saved)
            fmt["source"] = "GuiDocument.xml"
        return fmt


# ---------------------------------------------------------------------------------------------------------------------
# text metrics
# ---------------------------------------------------------------------------------------------------------------------


def font_file(family):
    """The bundled font file for a font family name, or None."""
    name = (family or "").strip().lower()
    base = FONT_FILES.get(name) or FONT_FILES.get(re.sub(r"\.ttf$", "", name))
    if base is None:
        return None
    path = os.path.join(App.getResourceDir(), "Mod", "TechDraw", "Resources", "fonts", base)
    return path if os.path.isfile(path) else None


def _load_font(path):
    if path not in _FONT_CACHE:
        try:
            from fontTools.ttLib import TTFont

            f = TTFont(path)
            _FONT_CACHE[path] = {
                "upem": f["head"].unitsPerEm,
                "cmap": f.getBestCmap(),
                "hmtx": f["hmtx"],
                "ascent": f["hhea"].ascent,
                "descent": f["hhea"].descent,
                "cap": f["OS/2"].sCapHeight,
            }
        except Exception:
            _FONT_CACHE[path] = None
    return _FONT_CACHE[path]


def text_box(text, family, fontsize_mm):
    """-> (W, H, approximate): the width and height in scene units of a balloon's text box in ``family`` at ``fontsize_mm``,
    as ``QGIViewBalloon`` gets them from Qt (advance plus 8, ceil(ascent + descent) plus 8 at the pixel size that gives
    the requested cap height).  ``approximate`` is true when the font file is not one that ships with FreeCAD or is missing
    glyphs, or fontTools is absent."""
    path = font_file(family)
    approx = path is None
    font = _load_font(path or font_file("osifont") or "")
    s = fontsize_mm * 10.0
    if font is None:  # fixed metrics: 0.55 em per character, line height 1.66 em at the osifont pixel size
        px = int(s * 1.34)
        return len(text) * 0.55 * px + 8, 1.66 * px + 8, True
    cap_px = font["cap"] * int(s) / font["upem"]
    px = int(s * (s / cap_px))
    advance = 0.0
    for ch in text:
        gid = font["cmap"].get(ord(ch))
        if gid is None:
            approx = True
            gid = ".notdef"
        advance += font["hmtx"][gid][0] * px / font["upem"]
    height = math.ceil((font["ascent"] - font["descent"]) * px / font["upem"])
    return advance + 8, height + 8, approx


# ---------------------------------------------------------------------------------------------------------------------
# end symbols
# ---------------------------------------------------------------------------------------------------------------------


def arrow_prims(style, size, tip, direction):
    """Primitives of end symbol ``style`` (index into ARROW_NAMES) of ``size`` mm with its tip at ``tip``; ``direction`` is
    the page direction the symbol points to.  The shapes are those of ``QGIArrow::draw`` (directed mode)."""
    if style == 7 or size <= 0:
        return []
    dx, dy = direction[0], -direction[1]  # Qt coordinates (y down), as the C++ formulas use
    n = math.hypot(dx, dy)
    dx, dy = (dx / n, dy / n) if n > 0 else (1.0, 0.0)
    neg = (-dx, -dy)
    perp = (-neg[1], neg[0])

    def q(v):
        return (tip[0] + v[0], tip[1] - v[1])

    def comb(a, va, b=None, vb=0.0):
        return (a[0] * va + (b[0] * vb if b else 0.0), a[1] * va + (b[1] * vb if b else 0.0))

    if style == 0:
        return [("solid", [q((0, 0)), q(comb(neg, size, perp, size / 6)), q(comb(neg, size, perp, -size / 6))])]
    if style == 1:
        return [("poly", [q(comb(neg, size, perp, size / 3)), q((0, 0)), q(comb(neg, size, perp, -size / 3))], False)]
    if style == 2:
        half = size / 2
        return [("line", q(comb(neg, half, perp, -half)), q(comb((dx, dy), half, perp, half)))]
    if style in (3, 4):
        return [("circle", tuple(tip), size / 4)]
    if style == 5:
        half = size / 2
        fperp = (-dy, dx)
        return [("poly", [q(comb((dx, dy), half, fperp, half)), q((0, 0)), q(comb((dx, dy), half, fperp, -half))], False)]
    if style == 6:
        return [("solid", [q(comb(neg, size)), q(comb(perp, size / 2)), q(comb(perp, -size / 2))])]
    return []


def overlap_adjust(style, size):
    """``QGIArrow::getOverlapAdjust``: how far a line is pulled back from the tip so it hides behind the symbol."""
    return size * {0: 0.5, 1: 0.1, 2: 0.0, 3: 0.0, 4: 0.25, 5: 0.0, 6: 1.0}.get(style, 0.0)


def arrow_size():
    return _group("Dimensions").GetFloat("ArrowSize", 3.5)


# ---------------------------------------------------------------------------------------------------------------------
# balloons
# ---------------------------------------------------------------------------------------------------------------------


def _bubble(shape, c, W, H, s):
    """-> (prims, offset_left_right, offset_up_down) of the bubble ``shape`` centred at page point ``c``, in mm.
    ``W``, ``H`` are the text box in scene units (``QGIViewBalloon::updateBalloon``)."""
    u = UNIT
    cx, cy = c
    if shape == "Circular":
        r = math.hypot(H / 2, W / 2) * s
        return [("circle", c, r * u)], r * u, 0.0
    if shape == "None":
        return [], (W / 2 + 20) * u, 0.0
    if shape == "Rectangle":
        h = H * s + 40 * s
        hw, hh = W / 2 * u, h / 2 * u
        return [("poly", [(cx - hw, cy + hh), (cx + hw, cy + hh), (cx + hw, cy - hh), (cx - hw, cy - hh)], True)], hw, 0.0
    if shape == "Triangle":
        r = math.hypot(H / 2, W / 2) * s + 30
        pts = [(cx + r * u * math.cos(a), cy - r * u * math.sin(a)) for a in (-math.pi / 2 + k * 2 * math.pi / 3 for k in range(3))]
        return [("poly", pts, True)], math.tan(math.radians(30)) * r * u, 0.0
    if shape == "Hexagon":
        r = math.hypot(H / 2, W / 2) * s + 10
        pts = [(cx + r * u * math.cos(a), cy - r * u * math.sin(a)) for a in (-2 * math.pi / 3 + k * math.pi / 3 for k in range(6))]
        return [("poly", pts, True)], r * u, 0.0
    if shape == "Square":
        m = max(W * s + 20, H * s + 10) * u
        return [("poly", [(cx - m / 2, cy + m / 2), (cx + m / 2, cy + m / 2), (cx + m / 2, cy - m / 2), (cx - m / 2, cy - m / 2)], True)], m / 2, 0.0
    if shape == "Inspection":
        w, h = (W * s + 20) * u, (H * s + 10) * u
        x0, yt, yb = cx - w / 2, cy + h / 2, cy - h / 2
        pts = [(x0, yt, 0.0), (x0 + w, yt, -1.0), (x0 + w, yb, 0.0), (x0, yb, -1.0)]
        return [("poly", pts, True)], w / 2 + h / 2, 0.0
    if shape == "Line":
        w, h = (W * s + 10) * u, (H * s + 5) * u
        return [("line", (cx - w / 2, cy - h / 2), (cx + w / 2, cy - h / 2))], w / 2, h / 2
    return [], (W / 2 + 20) * u, 0.0


def balloon_geometry(balloon, source_pos, fmt):
    """-> (prims, text_centre, approximate_reason or None, info) for a ``DrawViewBalloon`` whose source view is at page
    position ``source_pos``; ``fmt`` is the view provider's format (``GuiDocument`` / defaults)."""
    src = balloon.SourceView
    sc = _val(src.Scale)
    rot = math.radians(_val(getattr(src, "Rotation", 0.0)))
    c = (source_pos[0] + _val(balloon.X) * sc, source_pos[1] + _val(balloon.Y) * sc)
    ox, oy = _val(balloon.OriginX), _val(balloon.OriginY)
    tip = (
        source_pos[0] + (ox * math.cos(rot) - oy * math.sin(rot)) * sc,
        source_pos[1] + (ox * math.sin(rot) + oy * math.cos(rot)) * sc,
    )
    text = balloon.Text
    W, H, approx = text_box(text, fmt["Font"], fmt["Fontsize"])
    shape = balloon.BubbleShape
    prims, off_lr, off_ud = _bubble(shape, c, W, H, _val(balloon.ShapeScale))
    right = c[0] < tip[0]
    off_lr = off_lr if right else -off_lr
    start = (c[0] + off_lr, c[1] - off_ud)
    kink_len = _val(balloon.KinkLength)
    if abs(kink_len) < 1e-9 and shape != "Line":
        kink = start
    else:
        kink = (start[0] + (kink_len if right else -kink_len), start[1])
    style = ARROW_NAMES.index(balloon.EndType)
    size = _val(balloon.EndTypeScale) * arrow_size()
    end = tip
    arrow = []
    if style != 7:
        d = (tip[0] - kink[0], tip[1] - kink[1]) if abs(kink_len) > 1e-9 else (tip[0] - start[0], tip[1] - start[1])
        if style == 6 and _group("Decorations").GetBool("PyramidOrtho", True):
            a = math.degrees(math.atan2(-d[1], d[0]))  # Qt angle, y down
            a = a + 360.0 if a < 0 else a
            a = 90.0 if 45 < a < 135 else 180.0 if 135 < a < 225 else 270.0 if 225 < a < 315 else 0.0
            adj = overlap_adjust(style, size)
            end = (tip[0] - adj * math.cos(math.radians(a)), tip[1] + adj * math.sin(math.radians(a)))
            d = (math.cos(math.radians(a)), -math.sin(math.radians(a)))
        arrow = arrow_prims(style, size, tip, d)
    line = [start]
    for p in (kink, end):
        if math.hypot(p[0] - line[-1][0], p[1] - line[-1][1]) > 1e-9:
            line.append(p)
    line_prims = [("poly", line, False)] if len(line) > 1 else []
    reason = None
    if approx:
        reason = "font %r is not a bundled FreeCAD font or lacks glyphs; the bubble size is estimated" % fmt["Font"]
    visible = fmt["LineVisible"]
    if visible:
        prims = prims + line_prims + arrow
    else:  # QGIViewBalloon hides the leader and the symbol (the bubble stays)
        prims = prims
    return prims, c, reason, {"shape": shape, "box": (W, H)}


# ---------------------------------------------------------------------------------------------------------------------
# leaders
# ---------------------------------------------------------------------------------------------------------------------


def leader_points(leader, parent_pos):
    """-> the leader's page points (mm), before trimming: start point plus the transformed waypoints
    (``DrawLeaderLine::getTransformedWayPoints``, ``QGILeaderLine::getAttachPoint``)."""
    parent = leader.LeaderParent
    if parent is not None:
        sc = _val(parent.Scale)
        rot = math.radians(_val(getattr(parent, "Rotation", 0.0)))
        ax, ay = _val(leader.X) * sc, _val(leader.Y) * sc
        start = (parent_pos[0] + ax * math.cos(rot) - ay * math.sin(rot), parent_pos[1] + ax * math.sin(rot) + ay * math.cos(rot))
    else:
        sc, rot = 1.0, 0.0
        start = (_val(leader.X), _val(leader.Y))
    scale = sc if leader.Scalable else 1.0
    rotate = rot if leader.RotatesWithParent else 0.0
    deltas = []
    for p in leader.WayPoints:
        x, y = p.x * scale, -p.y * scale  # conventional (y up) deltas
        deltas.append((x * math.cos(rotate) - y * math.sin(rotate), x * math.sin(rotate) + y * math.cos(rotate)))
    if leader.AutoHorizontal and len(deltas) > 1:
        # DrawLeaderLine::horizLastSegment: the last segment becomes horizontal with the same length; its direction is the
        # sign of the x component of the segment rotated (once more, whatever RotatesWithParent says) by the parent's rotation
        last, pen = deltas[-1], deltas[-2]
        sx, sy = last[0] - pen[0], last[1] - pen[1]
        length = math.hypot(sx, sy)
        rx = sx * math.cos(rot) - sy * math.sin(rot)
        deltas[-1] = (pen[0] + (length if rx >= 0 else -length), pen[1])
    return [(start[0] + d[0], start[1] + d[1]) for d in deltas]


def leader_geometry(leader, parent_pos):
    """-> prims of a ``DrawLeaderLine``: the trimmed polyline and its start and end symbols."""
    pts = leader_points(leader, parent_pos)
    if len(pts) < 2:
        return []
    size = arrow_size()
    s_style, e_style = ARROW_NAMES.index(leader.StartSymbol), ARROW_NAMES.index(leader.EndSymbol)
    prims = []
    s_dir = (pts[0][0] - pts[1][0], pts[0][1] - pts[1][1])
    e_dir = (pts[-1][0] - pts[-2][0], pts[-1][1] - pts[-2][1])
    if s_style != 7:
        prims += arrow_prims(s_style, size, pts[0], s_dir)
    if e_style != 7:
        prims += arrow_prims(e_style, size, pts[-1], e_dir)
    line = list(pts)
    for index, nb, style in ((0, 1, s_style), (-1, -2, e_style)):
        if style == 7:
            continue
        vx, vy = pts[nb][0] - pts[index][0], pts[nb][1] - pts[index][1]
        n = math.hypot(vx, vy)
        if n > 0:
            adj = overlap_adjust(style, size) * UNIT  # QGILeaderLine adds the adjustment in scene units (0.1 mm), unscaled
            line[index] = (pts[index][0] + vx / n * adj, pts[index][1] + vy / n * adj)
    return [("poly", line, False)] + prims
