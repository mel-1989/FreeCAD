# SPDX-License-Identifier: LGPL-2.1-or-later
"""Bend-line labels for the flat pattern, drawn as seven-segment style strokes.

Written for cad-fork from scratch (no code from the add-on's GPL text helper). It
turns a short string of digits, '.', '-' and '^' (drawn as a small degree square)
into line edges lying in the plane with normal +Z. Labels are plain Part edges, so
they need no font file and work headless.
"""

import FreeCAD
import Part
from FreeCAD import Vector

# Glyph cell: width 0.6, height 1.0, origin at the lower left.
_W = 0.6
_H = 1.0
_M = _H / 2.0
_PTS = {
    "a": ((0.0, _H), (_W, _H)),
    "b": ((_W, _H), (_W, _M)),
    "c": ((_W, _M), (_W, 0.0)),
    "d": ((0.0, 0.0), (_W, 0.0)),
    "e": ((0.0, 0.0), (0.0, _M)),
    "f": ((0.0, _M), (0.0, _H)),
    "g": ((0.0, _M), (_W, _M)),
}
_DIGITS = {
    "0": "abcdef",
    "1": "bc",
    "2": "abged",
    "3": "abgcd",
    "4": "fgbc",
    "5": "afgcd",
    "6": "afgedc",
    "7": "abc",
    "8": "abcdefg",
    "9": "abcdfg",
}
_ADVANCE = 0.9


def _glyph_segments(ch):
    """Return a list of ((x0, y0), (x1, y1)) strokes for a character in its own cell."""
    if ch in _DIGITS:
        return [_PTS[s] for s in _DIGITS[ch]]
    if ch == "-":
        return [_PTS["g"]]
    if ch == ".":
        return [((_W / 2.0, 0.0), (_W / 2.0, 0.08 * _H))]
    if ch == "^":
        # degree mark: small square in the top right of the cell
        x0, y0, x1, y1 = 0.15, 0.7, 0.45, 1.0
        return [((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))]
    return []


def generate_label(text, position, height, direction):
    """Build a compound of line edges spelling `text`.

    position: lower left corner of the first glyph; height: glyph height;
    direction: reading direction (the text baseline), projected into the XY plane.
    """
    xdir = Vector(direction.x, direction.y, 0)
    if xdir.Length < 1e-9:
        xdir = Vector(1, 0, 0)
    xdir.normalize()
    ydir = Vector(0, 0, 1).cross(xdir)
    edges = []
    cursor = 0.0
    for ch in text:
        for (p0, p1) in _glyph_segments(ch):
            a = position + xdir * ((cursor + p0[0]) * height) + ydir * (p0[1] * height)
            b = position + xdir * ((cursor + p1[0]) * height) + ydir * (p1[1] * height)
            if (a - b).Length > 1e-9:
                edges.append(Part.makeLine(a, b))
        cursor += _ADVANCE
    return Part.makeCompound(edges)
