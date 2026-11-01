# SPDX-License-Identifier: LGPL-2.1-or-later
"""Structural profile tables for the Weldment module.

The tables are CSV files in ``profiles/`` (one or more per family group). Every
row carries its dimensions in mm, the published section area in mm^2, the
published linear mass in kg/m and the URL of the public table it was
transcribed from. ``#`` lines are comments and are skipped by the loader.

Public API (frozen):

    families() -> list[str]
    sizes(family) -> list[str]
    get(family, size) -> Profile            (KeyError with a readable message)

    Profile.family, .size, .kind ("I", "RHS", "CHS", "L"), .dims (dict, mm)
    Profile.published_area (mm^2), .mass_per_m (kg/m), .source (URL)
    Profile.area()  analytic area (mm^2) of the geometry face() builds
    Profile.face()  Part.Face in the XY plane, area centroid at the origin
    Profile.bbox()  (xmin, xmax, ymin, ymax) of face() in the centred frame

Mass note for AISC HSS (``05_aisc_hss.csv``): the face is built on the design wall ``tdes`` while
``mass_kg_m`` is the database's nominal-wall mass, so it is 4.5 to 8 % above area x 7850 kg/m^3.

Section conventions (all lengths in mm):

* ``I``: parallel flanges, root radius ``r`` at the four web-to-flange junctions,
  dims ``h, b, tw, tf, r``; x runs along the width ``b``, y along the height ``h``.
* ``RHS`` (also square): outer corner radius ``ro``, inner ``ri``, dims
  ``h, b, t, ro, ri``.
* ``CHS``: ring, dims ``d, t``.
* ``L``: legs along +x (length ``b``) and +y (length ``h``) before centring, root
  radius ``r1`` in the inner corner, toe radius ``r2`` on the inner-face corner of
  each leg end (EN 10056-1 figure), dims ``h, b, t, r1, r2``. Analytic area is
  ``t(h + b - t) + (1 - pi/4)(r1^2 - 2 r2^2)``.
"""

import csv
import difflib
import math
import os

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profiles")

_COLS = ("family", "size", "kind", "h", "b", "tw", "tf", "r", "t", "ro", "ri", "d",
         "r1", "r2", "area_mm2", "mass_kg_m", "source")
_REQ = {
    "I": ("h", "b", "tw", "tf", "r"),
    "RHS": ("h", "b", "t", "ro", "ri"),
    "CHS": ("d", "t"),
    "L": ("h", "b", "t", "r1", "r2"),
}
_FAMILIES = None   # family -> ordered {size: Profile}


class ProfileTableError(ValueError):
    """A profile CSV is malformed (message names file and line)."""


def _fillet_area(r):
    return (4.0 - math.pi) * r * r / 4.0


class Profile:
    def __init__(self, family, size, kind, dims, published_area, mass_per_m, source):
        self.family = family
        self.size = size
        self.kind = kind
        self.dims = dict(dims)
        self.published_area = published_area
        self.mass_per_m = mass_per_m
        self.source = source
        self._face = None

    def __repr__(self):
        return "<Profile %s %s>" % (self.family, self.size)

    # ------------------------------------------------------------- analytic area
    def area(self):
        d = self.dims
        k = self.kind
        if k == "I":
            return (2.0 * d["b"] * d["tf"] + (d["h"] - 2.0 * d["tf"]) * d["tw"]
                    + 4.0 * _fillet_area(d["r"]))
        if k == "RHS":
            outer = d["h"] * d["b"] - 4.0 * _fillet_area(d["ro"])
            inner = ((d["h"] - 2 * d["t"]) * (d["b"] - 2 * d["t"])
                     - 4.0 * _fillet_area(d["ri"]))
            return outer - inner
        if k == "CHS":
            di = d["d"] - 2.0 * d["t"]
            return math.pi / 4.0 * (d["d"] ** 2 - di ** 2)
        if k == "L":
            t = d["t"]
            return (t * (d["h"] + d["b"] - t) + _fillet_area(d["r1"])
                    - 2.0 * _fillet_area(d["r2"]))
        raise KeyError("unknown profile kind %r" % k)

    # ------------------------------------------------------------------ geometry
    def face(self):
        if self._face is None:
            self._face = self._build_face()
        return self._face.copy()

    def bbox(self):
        bb = self._build_face_cached().BoundBox
        return (bb.XMin, bb.XMax, bb.YMin, bb.YMax)

    def _build_face_cached(self):
        if self._face is None:
            self._face = self._build_face()
        return self._face

    def _build_face(self):
        import FreeCAD
        import Part
        d = self.dims
        k = self.kind
        if k == "I":
            h, b, tw, tf, r = d["h"], d["b"], d["tw"], d["tf"], d["r"]
            if h - 2 * tf <= 2 * r or (b - tw) / 2.0 <= r:
                raise ValueError("%s %s: root radius too large for the web/flange" %
                                 (self.family, self.size))
            x0, y0 = b / 2.0, h / 2.0
            w = tw / 2.0
            ih = y0 - tf
            pts = [(-x0, -y0), (x0, -y0), (x0, -y0 + tf), (w, -ih), (w, ih),
                   (x0, y0 - tf), (x0, y0), (-x0, y0), (-x0, y0 - tf), (-w, ih),
                   (-w, -ih), (-x0, -y0 + tf)]
            rad = [0, 0, 0, r, r, 0, 0, 0, 0, r, r, 0]
            outer = _rounded_wire(pts, rad)
            face = Part.Face(outer)
        elif k == "RHS":
            h, b, t, ro, ri = d["h"], d["b"], d["t"], d["ro"], d["ri"]
            o = _rounded_wire([(-b / 2, -h / 2), (b / 2, -h / 2), (b / 2, h / 2),
                               (-b / 2, h / 2)], [ro] * 4)
            bi, hi = b / 2 - t, h / 2 - t
            i = _rounded_wire([(-bi, -hi), (bi, -hi), (bi, hi), (-bi, hi)], [ri] * 4)
            face = Part.Face(o).cut(Part.Face(i)).Faces[0]
        elif k == "CHS":
            D, t = d["d"], d["t"]
            z = FreeCAD.Vector(0, 0, 1)
            o = Part.Wire(Part.makeCircle(D / 2.0, FreeCAD.Vector(0, 0, 0), z))
            i = Part.Wire(Part.makeCircle(D / 2.0 - t, FreeCAD.Vector(0, 0, 0), z))
            face = Part.Face(o).cut(Part.Face(i)).Faces[0]
        elif k == "L":
            h, b, t, r1, r2 = d["h"], d["b"], d["t"], d["r1"], d["r2"]
            pts = [(0, 0), (b, 0), (b, t), (t, t), (t, h), (0, h)]
            w = _rounded_wire(pts, [0, 0, r2, r1, r2, 0])
            face = Part.Face(w)
        else:
            raise KeyError("unknown profile kind %r" % k)
        if not face.isValid():
            raise ValueError("%s %s: invalid cross-section face" % (self.family, self.size))
        # area centroid to the origin
        com = face.CenterOfMass
        face.translate(FreeCAD.Vector(-com.x, -com.y, -com.z))
        return face


def _rounded_wire(pts, radii):
    """Closed wire through the polygon ``pts`` with fillet radius ``radii[i]`` at vertex i."""
    import FreeCAD
    import Part
    V = FreeCAD.Vector
    n = len(pts)
    segs = []   # list of (start, end) for the corner-to-corner pieces, arcs in between
    corners = []
    for i in range(n):
        p0, p1, p2 = pts[i - 1], pts[i], pts[(i + 1) % n]
        r = radii[i]
        if r <= 0:
            corners.append((p1, None, p1))
            continue
        ax, ay = p0[0] - p1[0], p0[1] - p1[1]
        bx, by = p2[0] - p1[0], p2[1] - p1[1]
        la, lb = math.hypot(ax, ay), math.hypot(bx, by)
        ax, ay, bx, by = ax / la, ay / la, bx / lb, by / lb
        cos_t = max(-1.0, min(1.0, ax * bx + ay * by))
        th = math.acos(cos_t)               # angle between the edges at the vertex
        tl = r / math.tan(th / 2.0)         # tangent length
        if tl > min(la, lb) * (1 + 1e-12):
            raise ValueError("fillet radius %g too large for the adjacent edges" % r)
        s = (p1[0] + ax * tl, p1[1] + ay * tl)
        e = (p1[0] + bx * tl, p1[1] + by * tl)
        mx, my = ax + bx, ay + by
        lm = math.hypot(mx, my)
        mx, my = mx / lm, my / lm
        dc = r / math.sin(th / 2.0)         # vertex to arc centre
        cx, cy = p1[0] + mx * dc, p1[1] + my * dc
        m = (cx - mx * r, cy - my * r)      # arc midpoint, on the vertex side
        corners.append((s, m, e))
    edges = []
    for i in range(n):
        s, m, e = corners[i]
        if m is not None:
            edges.append(Part.Arc(V(s[0], s[1], 0), V(m[0], m[1], 0), V(e[0], e[1], 0)).toShape())
        nxt = corners[(i + 1) % n]
        a, c = e, nxt[0]
        if math.hypot(a[0] - c[0], a[1] - c[1]) > 1e-12:
            edges.append(Part.LineSegment(V(a[0], a[1], 0), V(c[0], c[1], 0)).toShape())
    return Part.Wire(edges)


# ------------------------------------------------------------------------ loader
def _num(v, path, ln, col):
    try:
        x = float(v)
    except ValueError:
        raise ProfileTableError("%s:%d: column %r is not a number: %r" % (path, ln, col, v))
    if not math.isfinite(x) or x < 0:
        raise ProfileTableError("%s:%d: column %r must be a non-negative number: %r" %
                                (path, ln, col, v))
    return x


def _read_file(path, out):
    name = os.path.basename(path)
    with open(path, newline="", encoding="utf-8") as fh:
        lines = [(n, ln) for n, ln in enumerate(fh, 1) if ln.strip() and not ln.startswith("#")]
    if not lines:
        raise ProfileTableError("%s: no header row" % name)
    rows = csv.reader(ln for _, ln in lines)
    header = next(rows)
    missing = [c for c in _COLS if c not in header]
    if missing:
        raise ProfileTableError("%s: header lacks column(s) %s" % (name, ", ".join(missing)))
    for (lineno, _), row in zip(lines[1:], rows):
        if len(row) != len(header):
            raise ProfileTableError("%s:%d: expected %d fields, found %d" %
                                    (name, lineno, len(header), len(row)))
        rec = dict(zip(header, row))
        kind = rec["kind"].strip()
        if kind not in _REQ:
            raise ProfileTableError("%s:%d: unknown kind %r (use I, RHS, CHS or L)" %
                                    (name, lineno, kind))
        dims = {}
        for c in _REQ[kind]:
            if rec[c].strip() == "":
                raise ProfileTableError("%s:%d: %s %s needs column %r" %
                                        (name, lineno, rec["family"], rec["size"], c))
            dims[c] = _num(rec[c], name, lineno, c)
        area = _num(rec["area_mm2"], name, lineno, "area_mm2")
        mass = _num(rec["mass_kg_m"], name, lineno, "mass_kg_m")
        if not rec["source"].strip().startswith("http"):
            raise ProfileTableError("%s:%d: %s %s has no source URL" %
                                    (name, lineno, rec["family"], rec["size"]))
        fam, size = rec["family"].strip(), rec["size"].strip()
        table = out.setdefault(fam, {})
        if size in table:
            raise ProfileTableError("%s:%d: duplicate size %r in family %r" %
                                    (name, lineno, size, fam))
        table[size] = Profile(fam, size, kind, dims, area, mass, rec["source"].strip())


def _load():
    global _FAMILIES
    if _FAMILIES is None:
        out = {}
        if not os.path.isdir(_DIR):
            raise ProfileTableError("profile table directory not found: %s" % _DIR)
        for fn in sorted(os.listdir(_DIR)):
            if fn.endswith(".csv"):
                _read_file(os.path.join(_DIR, fn), out)
        _FAMILIES = out
    return _FAMILIES


def families():
    return list(_load().keys())


def sizes(family):
    tab = _load()
    if family not in tab:
        raise KeyError("unknown profile family %r; known families: %s" %
                       (family, ", ".join(tab)))
    return list(tab[family].keys())


def get(family, size):
    tab = _load()
    if family not in tab:
        raise KeyError("unknown profile family %r; known families: %s" %
                       (family, ", ".join(tab)))
    if size not in tab[family]:
        names = list(tab[family])
        near = difflib.get_close_matches(str(size), names, n=5, cutoff=0.0)
        raise KeyError("unknown size %r in family %r (%d sizes); nearest: %s" %
                       (size, family, len(names), ", ".join(near)))
    return tab[family][size]
