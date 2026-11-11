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

"""Flattening of a TechDraw SVG page template to page-mm geometry and text, for the layered DXF export (G04 slice 7b).

``flatten_template(path, editable)`` reads the template file with ``xml.etree`` (no Qt), substitutes the editable fields
as ``DrawSVGTemplate::processTemplate`` does, and returns every drawable element in page millimetres, origin lower left,
+Y up, with every transform and the viewBox scale applied.  Shapes come back as ``("poly", points, closed)``,
``("circle", centre, radius)`` or ``("ellipse", centre, major_axis, ratio)``; texts as dictionaries with the string, the
baseline point, the effective height, the anchor, the rotation and the width factor.

Public sources: the SVG 1.1 specification (basic shapes, path data and its elliptical-arc implementation notes, transform
lists, text, ``text-anchor``, ``xml:space``) and TechDraw's own ``DrawSVGTemplate.cpp`` for the editable fields.
"""

import math
import re
import xml.etree.ElementTree as ET

NS = "{http://www.w3.org/2000/svg}"
UNITS = {"mm": 1.0, "cm": 10.0, "in": 25.4, "pt": 25.4 / 72, "pc": 25.4 / 6, "px": 25.4 / 96, "": 1.0}
FLAT_TOL = 0.002  # mm, chord deviation allowed when curves become polylines (the brief allows 0.01)
_NUM = r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?"


def parse_length(s):
    m = re.match(r"\s*(" + _NUM + r")\s*([a-z%]*)", s)
    return float(m.group(1)) * UNITS.get(m.group(2), 1.0)


def mmul(a, b):
    return (
        a[0] * b[0] + a[2] * b[1],
        a[1] * b[0] + a[3] * b[1],
        a[0] * b[2] + a[2] * b[3],
        a[1] * b[2] + a[3] * b[3],
        a[0] * b[4] + a[2] * b[5] + a[4],
        a[1] * b[4] + a[3] * b[5] + a[5],
    )


def parse_transform(s):
    m = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", s or ""):
        a = [float(x) for x in re.split(r"[\s,]+", args.strip()) if x]
        if name == "matrix":
            t = tuple(a)
        elif name == "translate":
            t = (1, 0, 0, 1, a[0], a[1] if len(a) > 1 else 0)
        elif name == "scale":
            t = (a[0], 0, 0, a[1] if len(a) > 1 else a[0], 0, 0)
        elif name == "rotate":
            c, s_ = math.cos(math.radians(a[0])), math.sin(math.radians(a[0]))
            t = (c, s_, -s_, c, 0, 0)
            if len(a) == 3:
                t = mmul(mmul((1, 0, 0, 1, a[1], a[2]), t), (1, 0, 0, 1, -a[1], -a[2]))
        elif name == "skewX":
            t = (1, 0, math.tan(math.radians(a[0])), 1, 0, 0)
        elif name == "skewY":
            t = (1, math.tan(math.radians(a[0])), 0, 1, 0, 0)
        else:
            raise ValueError("unsupported transform %s" % name)
        m = mmul(m, t)
    return m


def apply(m, p):
    return (m[0] * p[0] + m[2] * p[1] + m[4], m[1] * p[0] + m[3] * p[1] + m[5])


def _norm(m):
    return math.sqrt(m[0] * m[0] + m[1] * m[1] + m[2] * m[2] + m[3] * m[3])  # bounds the spectral norm


def _arc_points(p0, rx, ry, phi, fa, fs, p1, scale):
    """Points after ``p0`` of the SVG elliptical arc (implementation notes F.6), chord error at most FLAT_TOL / scale."""
    if rx == 0 or ry == 0:
        return [p1]
    ph = math.radians(phi)
    c, s = math.cos(ph), math.sin(ph)
    dx, dy = (p0[0] - p1[0]) / 2, (p0[1] - p1[1]) / 2
    x1, y1 = c * dx + s * dy, -s * dx + c * dy
    rx, ry = abs(rx), abs(ry)
    lam = x1 * x1 / rx / rx + y1 * y1 / ry / ry
    if lam > 1:
        rx *= math.sqrt(lam)
        ry *= math.sqrt(lam)
    num = rx * rx * ry * ry - rx * rx * y1 * y1 - ry * ry * x1 * x1
    den = rx * rx * y1 * y1 + ry * ry * x1 * x1
    co = math.sqrt(max(0.0, num / den)) * (-1 if fa == fs else 1)
    cxp, cyp = co * rx * y1 / ry, -co * ry * x1 / rx
    cx = c * cxp - s * cyp + (p0[0] + p1[0]) / 2
    cy = s * cxp + c * cyp + (p0[1] + p1[1]) / 2

    def ang(ux, uy, vx, vy):
        return math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)

    t1 = ang(1, 0, (x1 - cxp) / rx, (y1 - cyp) / ry)
    dt = ang((x1 - cxp) / rx, (y1 - cyp) / ry, (-x1 - cxp) / rx, (-y1 - cyp) / ry)
    if not fs and dt > 0:
        dt -= 2 * math.pi
    if fs and dt < 0:
        dt += 2 * math.pi
    radius = max(rx, ry) * scale
    step = 2 * math.acos(max(0.0, 1 - FLAT_TOL / max(radius, 2 * FLAT_TOL)))
    n = max(4, int(abs(dt) / max(step, 1e-3)) + 1)
    out = []
    for i in range(1, n + 1):
        t = t1 + dt * i / n
        x, y = rx * math.cos(t), ry * math.sin(t)
        out.append((c * x - s * y + cx, s * x + c * y + cy))
    out[-1] = p1
    return out


def _segments(second_diff, scale, factor):
    """Subdivisions so that a Bezier curve's chord error stays within FLAT_TOL (error <= factor * |P''| / n^2)."""
    return max(1, int(math.ceil(math.sqrt(factor * second_diff * scale / FLAT_TOL))))


def path_polylines(d, scale=1.0):
    """-> [(points, closed)] for SVG path data ``d`` in user units; ``scale`` (user unit -> mm) sizes the curve subdivision."""
    toks = re.findall(r"[A-Za-z]|" + _NUM, d)
    i = 0
    out = []
    cur = None
    pos = start = (0.0, 0.0)
    cmd = None
    last_ctl = None  # (x, y, kind) of the previous curve's last control point

    def num():
        nonlocal i
        v = float(toks[i])
        i += 1
        return v

    def flush(closed):
        nonlocal cur
        if cur is not None and len(cur) > 1:
            out.append((cur, closed))
        cur = None

    while i < len(toks):
        if re.match("[A-Za-z]", toks[i]):
            cmd = toks[i]
            i += 1
        if cmd is None:
            raise ValueError("path data does not start with a command")
        rel, c = cmd.islower(), cmd.upper()
        if c == "Z":
            if cur is not None:
                flush(True)
            pos = start
            last_ctl = None
            continue
        if c == "M":
            x, y = num(), num()
            if rel:
                x, y = x + pos[0], y + pos[1]
            flush(False)
            pos = start = (x, y)
            cur = [pos]
            cmd = "l" if rel else "L"
            last_ctl = None
            continue
        if cur is None:
            cur = [pos]
        if c == "L":
            x, y = num(), num()
            if rel:
                x, y = x + pos[0], y + pos[1]
            pos = (x, y)
            cur.append(pos)
            last_ctl = None
        elif c == "H":
            x = num()
            pos = (x + (pos[0] if rel else 0), pos[1])
            cur.append(pos)
            last_ctl = None
        elif c == "V":
            y = num()
            pos = (pos[0], y + (pos[1] if rel else 0))
            cur.append(pos)
            last_ctl = None
        elif c in "CS":
            if c == "C":
                p1 = (num(), num())
            p2 = (num(), num())
            p3 = (num(), num())
            if rel:
                if c == "C":
                    p1 = (p1[0] + pos[0], p1[1] + pos[1])
                p2 = (p2[0] + pos[0], p2[1] + pos[1])
                p3 = (p3[0] + pos[0], p3[1] + pos[1])
            if c == "S":
                p1 = (2 * pos[0] - last_ctl[0], 2 * pos[1] - last_ctl[1]) if last_ctl and last_ctl[2] == "c" else pos
            p0 = pos
            m2 = 6 * max(
                math.hypot(p0[0] - 2 * p1[0] + p2[0], p0[1] - 2 * p1[1] + p2[1]),
                math.hypot(p1[0] - 2 * p2[0] + p3[0], p1[1] - 2 * p2[1] + p3[1]),
            )
            n = _segments(m2, scale, 1 / 8)
            for k in range(1, n + 1):
                t = k / n
                u = 1 - t
                cur.append(
                    (
                        u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0],
                        u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1],
                    )
                )
            last_ctl = (p2[0], p2[1], "c")
            pos = p3
        elif c in "QT":
            if c == "Q":
                p1 = (num(), num())
            p3 = (num(), num())
            if rel:
                if c == "Q":
                    p1 = (p1[0] + pos[0], p1[1] + pos[1])
                p3 = (p3[0] + pos[0], p3[1] + pos[1])
            if c == "T":
                p1 = (2 * pos[0] - last_ctl[0], 2 * pos[1] - last_ctl[1]) if last_ctl and last_ctl[2] == "q" else pos
            p0 = pos
            m2 = 2 * math.hypot(p0[0] - 2 * p1[0] + p3[0], p0[1] - 2 * p1[1] + p3[1])
            n = _segments(m2, scale, 1 / 8)
            for k in range(1, n + 1):
                t = k / n
                u = 1 - t
                cur.append((u * u * p0[0] + 2 * u * t * p1[0] + t * t * p3[0], u * u * p0[1] + 2 * u * t * p1[1] + t * t * p3[1]))
            last_ctl = (p1[0], p1[1], "q")
            pos = p3
        elif c == "A":
            rx, ry, ph = num(), num(), num()
            fa, fs = int(num()), int(num())
            x, y = num(), num()
            if rel:
                x, y = x + pos[0], y + pos[1]
            cur.extend(_arc_points(pos, rx, ry, ph, fa, fs, (x, y), scale))
            pos = (x, y)
            last_ctl = None
        else:
            raise ValueError("unsupported path command %s" % cmd)
    flush(False)
    return out


def _ellipse_entity(centre, a, b):
    """The ellipse with centre and conjugate semi-diameters ``a``, ``b`` (page mm) as ("circle" | "ellipse", ...)."""
    # principal axes of the linear map [a b]: eigen-decomposition of M M^T
    p, q, r = a[0] * a[0] + b[0] * b[0], a[0] * a[1] + b[0] * b[1], a[1] * a[1] + b[1] * b[1]
    mean, diff = (p + r) / 2, math.hypot((p - r) / 2, q)
    s0, s1 = math.sqrt(max(mean + diff, 0.0)), math.sqrt(max(mean - diff, 0.0))
    if s0 == 0:
        return None
    if s1 / s0 > 1 - 1e-9:
        return ("circle", centre, s0)
    theta = 0.5 * math.atan2(2 * q, p - r)
    return ("ellipse", centre, (s0 * math.cos(theta), s0 * math.sin(theta)), s1 / s0)


class Flat:
    """The result of ``flatten_template``."""

    def __init__(self, width, height):
        self.width, self.height = width, height
        self.shapes = []  # ("poly", pts, closed) | ("circle", c, r) | ("ellipse", c, major, ratio)
        self.texts = []
        self.filled = 0  # shapes with a fill, written as outlines
        self.unsupported = []


def _prop(el, key):
    m = re.search(r"(?:^|;)\s*" + key + r"\s*:\s*([^;]+)", el.get("style") or "")
    return (m.group(1).strip() if m else None) or el.get(key)


def _fontsize(v):
    m = re.match(r"\s*(" + _NUM + r")", v or "")
    return float(m.group(1)) if m else None


def _visible_fill(v):
    return v is None or v.strip().lower() not in ("none", "transparent")


def _clean_text(s, preserve):
    if preserve:
        return s.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return re.sub(r"\s+", " ", s).strip()


def load_template_root(path, editable=None):
    """Parse the template file; the editable fields (``text[@freecad:editable]``) take their ``editable[name]`` string in
    every ``tspan``, as ``DrawSVGTemplate::processTemplate`` does (it matches the literal attribute name
    ``freecad:editable``, so a template whose fields carry another prefix keeps its defaults)."""
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    raw = re.sub(r"(?<![\w:])freecad:editable\s*=", "fcEditable=", raw)
    raw = re.sub(r"(?<![\w:])([A-Za-z_][\w.-]*):(editable|autofill)\s*=", r"\1_\2=", raw)  # an unbound prefix is not parseable
    root = ET.fromstring(raw)
    editable = editable or {}
    for text in root.iter(NS + "text"):
        name = text.get("fcEditable")
        if name and name in editable:
            for tspan in text.findall(NS + "tspan"):
                for child in list(tspan):
                    tspan.remove(child)
                tspan.text = str(editable[name])
                tspan.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    return root


def flatten_root(root):
    w, h = parse_length(root.get("width", "0")), parse_length(root.get("height", "0"))
    vb = root.get("viewBox")
    if vb:
        v = [float(x) for x in vb.replace(",", " ").split()]
        sx, sy = w / v[2], h / v[3]
        base = (sx, 0.0, 0.0, sy, -v[0] * sx, -v[1] * sy)
    else:
        base = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    flat = Flat(w, h)

    def page(m, p):
        x, y = apply(m, p)
        return (x, h - y)

    def walk(e, m, inh):
        k = e.tag.replace(NS, "")
        if k in ("defs", "metadata", "title", "desc", "style", "namedview"):
            return
        if "}" in k:
            return
        if e.get("transform"):
            m = mmul(m, parse_transform(e.get("transform")))
        inh = dict(inh)
        for key in ("stroke", "fill", "stroke-width", "font-size", "text-anchor", "display"):
            v = _prop(e, key)
            if v is not None:
                inh[key] = v
        if inh.get("display", "").strip() == "none":
            return
        scale = _norm(m) / math.sqrt(2) if k != "svg" else 1.0
        stroked = _visible_fill(inh.get("stroke")) and inh.get("stroke") is not None
        filled = _visible_fill(inh.get("fill"))  # the SVG default fill is black
        visible = stroked or filled

        def num(a, default=0.0):
            try:
                return float(re.match(_NUM, (e.get(a) or "").strip()).group(0))
            except (AttributeError, ValueError):
                return default

        def add_poly(pts, closed):
            if not visible or len(pts) < 2:
                return
            flat.shapes.append(("poly", [page(m, p) for p in pts], closed))
            if filled and not stroked:
                flat.filled += 1

        if k == "rect":
            x, y, wd, ht = num("x"), num("y"), num("width"), num("height")
            if wd > 0 and ht > 0:
                add_poly([(x, y), (x + wd, y), (x + wd, y + ht), (x, y + ht)], True)
        elif k == "line":
            add_poly([(num("x1"), num("y1")), (num("x2"), num("y2"))], False)
        elif k in ("polyline", "polygon"):
            nums = [float(x) for x in re.findall(_NUM, e.get("points", ""))]
            pts = list(zip(nums[::2], nums[1::2]))
            add_poly(pts, k == "polygon")
        elif k in ("circle", "ellipse"):
            rx = num("r") if k == "circle" else num("rx")
            ry = rx if k == "circle" else num("ry")
            if visible and rx > 0 and ry > 0:
                c = page(m, (num("cx"), num("cy")))
                a = (m[0] * rx, -m[1] * rx)  # the x semi-axis in page mm (y flipped)
                b = (m[2] * ry, -m[3] * ry)
                ent = _ellipse_entity(c, a, b)
                if ent is not None:
                    flat.shapes.append(ent)
                    if filled and not stroked:
                        flat.filled += 1
        elif k == "path":
            if visible:
                for pts, closed in path_polylines(e.get("d", ""), scale):
                    add_poly(pts, closed)
        elif k == "text":
            _text(e, m, inh)
            return
        elif k in ("image", "use", "foreignObject"):
            flat.unsupported.append(k)
        for c in e:
            walk(c, m, inh)

    def _text(e, m, inh):
        sx, sy = math.hypot(m[0], m[1]), math.hypot(m[2], m[3])
        rot = -math.degrees(math.atan2(m[1], m[0]))
        preserve0 = e.get("{http://www.w3.org/XML/1998/namespace}space") == "preserve"

        def emit(string, x, y, el, inh_el, preserve):
            s = _clean_text(string, preserve)
            if not s:
                return
            fs = _fontsize(_prop(el, "font-size") or inh_el.get("font-size")) or 12.0
            anchor = (_prop(el, "text-anchor") or inh_el.get("text-anchor") or "start").strip()
            px, py = page(m, (x, y))
            flat.texts.append(
                {
                    "text": s,
                    "x": px,
                    "y": py,
                    "height": fs * sy,
                    "anchor": anchor if anchor in ("start", "middle", "end") else "start",
                    "rotation": rot,
                    "width_factor": sx / sy if sy else 1.0,
                }
            )

        def first(v, default):
            if v:
                n = re.findall(_NUM, v)
                if n:
                    return float(n[0])
            return default

        def rec(el, x, y, inh_el, preserve):
            x, y = first(el.get("x"), x), first(el.get("y"), y)
            inh_el = dict(inh_el)
            for key in ("font-size", "text-anchor"):
                v = _prop(el, key)
                if v is not None:
                    inh_el[key] = v
            preserve = preserve or el.get("{http://www.w3.org/XML/1998/namespace}space") == "preserve"
            if el.text:
                emit(el.text, x, y, el, inh_el, preserve)
            for c in el:
                if c.tag.replace(NS, "") == "tspan":
                    rec(c, x, y, inh_el, preserve)
                if c.tail:
                    emit(c.tail, x, y, el, inh_el, preserve)

        rec(e, 0.0, 0.0, inh, preserve0)

    walk(root, base, {})
    return flat


def flatten_template(path, editable=None):
    """-> ``Flat`` for the template file at ``path`` (raises ``ET.ParseError``, ``OSError`` or ``ValueError``)."""
    return flatten_root(load_template_root(path, editable))
