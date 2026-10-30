# SPDX-License-Identifier: LGPL-2.1-or-later
"""Thickness colour ramp (pure Python, no FreeCAD import).

A face whose measured thickness ``t`` is at or below ``min_ok`` is pure red ``(1, 0, 0)``. Above it the colour
moves linearly in RGB to pure blue ``(0, 0, 1)`` at ``t_max`` (the largest thickness in the picture), so the
red channel is ``1 - f`` and the blue channel is ``f`` with ``f = (t - min_ok) / (t_max - min_ok)`` clamped to
[0, 1]. A face none of whose rays hit anything (``None``) is mid grey ``(0.6, 0.6, 0.6)``. Faces of a body that
is not a solid are grey too.
"""

RED = (1.0, 0.0, 0.0)
BLUE = (0.0, 0.0, 1.0)
GREY = (0.6, 0.6, 0.6)


def colour(t, min_ok, t_max):
    """RGB tuple (floats 0..1) for thickness ``t`` (None = no measurement)."""
    if t is None:
        return GREY
    if t <= min_ok:
        return RED
    if t_max is None or t_max <= min_ok:
        return BLUE
    f = min(1.0, max(0.0, (t - min_ok) / (t_max - min_ok)))
    return (1.0 - f, 0.0, f)


def colours(values, min_ok, t_max=None):
    """Colours for a list of per-face thicknesses; ``t_max`` defaults to the largest value."""
    if t_max is None:
        known = [v for v in values if v is not None]
        t_max = max(known) if known else None
    return [colour(v, min_ok, t_max) for v in values]
