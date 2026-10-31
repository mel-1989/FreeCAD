# SPDX-License-Identifier: LGPL-2.1-or-later
"""Bend allowance and bend deduction tables for SheetMetal (cad-fork G07 slice 3a).

A bend table is a CSV file in the ``BendTables`` folder next to this module. Lines starting
with ``#`` are comments (each shipped file cites its source there). The header row names the
columns:

    material,gauge,thickness_mm,inside_radius_mm,angle_deg,<value column>

where ``<value column>`` is ``bend_allowance_mm`` (a BA table) or ``bend_deduction_mm`` (a BD
table). ``gauge`` may be empty; when set it is an alias for the row's thickness, so a part can
be keyed by "16ga" instead of 1.519 mm. The bend angle is the angle the flange turns through
(90 for an ordinary L-bend), the radius is the INSIDE radius.

Definitions (public: Oberg, Jones, Horton, Ryffel, "Machinery's Handbook", Industrial Press,
sheet-metal chapter; the same formulas are in Wikipedia, "Bending (metalworking)"):

    BA   = pi / 180 * theta * (R + K * T)           bend allowance, flat length of the bend
    OSSB = tan(theta / 2) * (R + T)                 outside setback
    BD   = 2 * OSSB - BA                            bend deduction

so a BA table and a BD table describe the same bends and convert into each other exactly.

K-factor of the shipped tables: 0.38 (R < T), 0.43 (T <= R <= 3T), 0.50 (R > 3T) for mild steel, air bending
(Wikipedia "Bending (metalworking)", generic K-factors table, medium steel; Durma Press, "K Factor in
Press Brake Bending"; a rule of thumb, see the CSV headers).

Lookup rules:

* A value at a listed (thickness, radius, angle) point is returned exactly as stored.
* Between listed points the lookup is linear in the stated variable: linear in thickness
  between two listed thicknesses, and bilinear in radius and angle on the grid of one
  thickness. The stored quantity (BA or BD) is the one interpolated.
* Outside the listed range of any variable the lookup raises ``BendTableRangeError`` (a
  ``ValueError``) with a readable message; it never extrapolates.
"""

import bisect
import csv
import math
import os

TABLE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "BendTables")

BA_COLUMN = "bend_allowance_mm"
BD_COLUMN = "bend_deduction_mm"
KEY_COLUMNS = ["material", "gauge", "thickness_mm", "inside_radius_mm", "angle_deg"]
EPS = 1e-9  # two keys closer than this are the same listed point


class BendTableError(ValueError):
    """A bend table file is malformed or names something that does not exist."""


class BendTableRangeError(BendTableError):
    """A lookup asked for a point outside the range the table lists."""


def get_bend_table_directory():
    return TABLE_DIR


def outside_setback(thickness, radius, angle_deg):
    """OSSB = tan(theta / 2) * (R + T), the setback from the mould-line apex to the tangent line."""
    if not 0.0 < angle_deg < 180.0:
        raise BendTableError(
            "outside setback is defined for bend angles between 0 and 180 degrees, got %.9g" % angle_deg
        )
    return math.tan(math.radians(angle_deg) / 2.0) * (radius + thickness)


def ba_to_bd(ba, thickness, radius, angle_deg):
    """BD = 2 * OSSB - BA."""
    return 2.0 * outside_setback(thickness, radius, angle_deg) - ba


def bd_to_ba(bd, thickness, radius, angle_deg):
    """BA = 2 * OSSB - BD (the same relation, it is its own inverse)."""
    return 2.0 * outside_setback(thickness, radius, angle_deg) - bd


def bend_allowance_from_k(thickness, radius, angle_deg, k_factor):
    """BA = pi / 180 * theta * (R + K * T)."""
    return math.radians(angle_deg) * (radius + k_factor * thickness)


def _interp_1d(axis, x, label, unit=""):
    """Bracket x on the sorted list `axis`: (i, j, t) with value = (1 - t) * v[i] + t * v[j].

    Exact hits return t = 0 so a listed point comes back unchanged. Outside the axis raises.
    """
    lo, hi = axis[0], axis[-1]
    if x < lo - EPS or x > hi + EPS:
        raise BendTableRangeError(
            "%s %.9g%s is outside the table range %.9g to %.9g%s; the table is not extrapolated"
            % (label, x, unit, lo, hi, unit)
        )
    pos = bisect.bisect_left(axis, x - EPS)
    if abs(axis[pos] - x) <= EPS:
        return pos, pos, 0.0
    i, j = pos - 1, pos
    return i, j, (x - axis[i]) / (axis[j] - axis[i])


class BendTable:
    """One parsed table file: kind "BA" or "BD" and the rows for one or more materials."""

    def __init__(self, name, kind, rows, comments=()):
        if kind not in ("BA", "BD"):
            raise BendTableError("bend table kind must be BA or BD, got %r" % (kind,))
        self.name = name
        self.kind = kind
        self.comments = list(comments)
        # material -> thickness -> (radii, angles, {(radius, angle): value})
        self._grids = {}
        self._gauges = {}  # material -> gauge name -> thickness
        self._build(rows)

    # -- construction -------------------------------------------------------------------

    @classmethod
    def from_file(cls, path):
        name = os.path.splitext(os.path.basename(path))[0]
        with open(path, newline="", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        comments = [ln.lstrip("#").strip() for ln in lines if ln.startswith("#")]
        body = [ln for ln in lines if ln.strip() and not ln.startswith("#")]
        if not body:
            raise BendTableError("bend table %s has no header row" % name)
        reader = csv.DictReader(body)
        header = reader.fieldnames or []
        missing = [c for c in KEY_COLUMNS if c not in header]
        if missing:
            raise BendTableError("bend table %s lacks the column(s) %s" % (name, ", ".join(missing)))
        value_cols = [c for c in (BA_COLUMN, BD_COLUMN) if c in header]
        if len(value_cols) != 1:
            raise BendTableError(
                "bend table %s needs exactly one of the columns %s or %s" % (name, BA_COLUMN, BD_COLUMN)
            )
        kind = "BA" if value_cols[0] == BA_COLUMN else "BD"
        rows = []
        for n, rec in enumerate(reader, start=1):
            try:
                rows.append((
                    rec["material"].strip(),
                    (rec["gauge"] or "").strip(),
                    float(rec["thickness_mm"]),
                    float(rec["inside_radius_mm"]),
                    float(rec["angle_deg"]),
                    float(rec[value_cols[0]]),
                ))
            except (TypeError, ValueError, AttributeError) as exc:
                raise BendTableError("bend table %s, data row %d is not valid: %s" % (name, n, exc))
        return cls(name, kind, rows, comments)

    def _build(self, rows):
        if not rows:
            raise BendTableError("bend table %s has no data rows" % self.name)
        raw = {}
        for material, gauge, thickness, radius, angle, value in rows:
            if not material:
                raise BendTableError("bend table %s has a row without a material" % self.name)
            if thickness <= 0 or radius < 0 or not 0.0 < angle <= 180.0:
                raise BendTableError(
                    "bend table %s: impossible row thickness=%.9g radius=%.9g angle=%.9g"
                    % (self.name, thickness, radius, angle)
                )
            cell = raw.setdefault(material, {}).setdefault(thickness, {})
            if (radius, angle) in cell:
                raise BendTableError(
                    "bend table %s lists %s T=%.9g R=%.9g angle=%.9g twice" % (self.name, material, thickness, radius, angle)
                )
            cell[(radius, angle)] = value
            if gauge:
                known = self._gauges.setdefault(material, {})
                if known.get(gauge, thickness) != thickness:
                    raise BendTableError("bend table %s: gauge %s has two thicknesses" % (self.name, gauge))
                known[gauge] = thickness
        for material, by_t in raw.items():
            self._grids[material] = {}
            for thickness, cell in by_t.items():
                radii = sorted({r for r, _ in cell})
                angles = sorted({a for _, a in cell})
                if len(cell) != len(radii) * len(angles):
                    raise BendTableError(
                        "bend table %s: %s T=%.9g is not a full radius x angle grid (%d rows for %d x %d)"
                        % (self.name, material, thickness, len(cell), len(radii), len(angles))
                    )
                self._grids[material][thickness] = (radii, angles, cell)

    # -- queries ------------------------------------------------------------------------

    @property
    def materials(self):
        return sorted(self._grids)

    def thicknesses(self, material):
        return sorted(self._grids[self._material(material)])

    def gauges(self, material):
        return dict(self._gauges.get(self._material(material), {}))

    def _material(self, material):
        if material in (None, ""):
            if len(self._grids) == 1:
                return next(iter(self._grids))
            raise BendTableError(
                "bend table %s lists several materials (%s); name one" % (self.name, ", ".join(self.materials))
            )
        if material not in self._grids:
            raise BendTableError(
                "bend table %s has no material %r (it lists: %s)" % (self.name, material, ", ".join(self.materials))
            )
        return material

    def thickness_of_gauge(self, material, gauge):
        material = self._material(material)
        try:
            return self._gauges[material][gauge]
        except KeyError:
            raise BendTableError(
                "bend table %s has no gauge %r for %s (it lists: %s)"
                % (self.name, gauge, material, ", ".join(sorted(self._gauges.get(material, {}))) or "none")
            )

    def value(self, thickness, radius, angle_deg, material=None):
        """The stored quantity (BA for a BA table, BD for a BD table) at a point, mm."""
        grids = self._grids[self._material(material)]
        ts = sorted(grids)
        ti, tj, tt = _interp_1d(ts, thickness, "thickness", " mm")
        at_i = self._plane(grids[ts[ti]], radius, angle_deg)
        if tt == 0.0:
            return at_i
        at_j = self._plane(grids[ts[tj]], radius, angle_deg)
        return at_i + (at_j - at_i) * tt

    @staticmethod
    def _plane(grid, radius, angle_deg):
        radii, angles, cell = grid
        ri, rj, rt = _interp_1d(radii, radius, "inside radius", " mm")
        ai, aj, at = _interp_1d(angles, angle_deg, "bend angle", " deg")

        def at_radius(ai_):
            lo = cell[(radii[ri], angles[ai_])]
            if rt == 0.0:
                return lo
            return lo + (cell[(radii[rj], angles[ai_])] - lo) * rt

        first = at_radius(ai)
        if at == 0.0:
            return first
        return first + (at_radius(aj) - first) * at

    def allowance(self, thickness, radius, angle_deg, material=None):
        """Bend allowance BA (mm), converting a BD table through BA = 2 * OSSB - BD."""
        v = self.value(thickness, radius, angle_deg, material)
        return v if self.kind == "BA" else bd_to_ba(v, thickness, radius, angle_deg)

    def deduction(self, thickness, radius, angle_deg, material=None):
        """Bend deduction BD (mm), converting a BA table through BD = 2 * OSSB - BA."""
        v = self.value(thickness, radius, angle_deg, material)
        return v if self.kind == "BD" else ba_to_bd(v, thickness, radius, angle_deg)

    def equivalent_k_factor(self, thickness, radius, angle_deg, material=None):
        """The ANSI K-factor that gives this bend's allowance: K = (BA / theta - R) / T."""
        ba = self.allowance(thickness, radius, angle_deg, material)
        return (ba / math.radians(angle_deg) - radius) / thickness

    def as_kind(self, kind):
        """A copy of this table expressed as "BA" or "BD" at its listed points (exact round trip)."""
        if kind == self.kind:
            return self
        rows = []
        for material in self.materials:
            gauge_of = {t: g for g, t in self._gauges.get(material, {}).items()}
            for thickness, (_r, _a, cell) in sorted(self._grids[material].items()):
                for (radius, angle), value in sorted(cell.items()):
                    conv = (ba_to_bd if kind == "BD" else bd_to_ba)(value, thickness, radius, angle)
                    rows.append((material, gauge_of.get(thickness, ""), thickness, radius, angle, conv))
        return BendTable(self.name, kind, rows, self.comments)

    def to_csv(self, path, comments=()):
        """Write the table in the file format above (full float precision)."""
        col = BA_COLUMN if self.kind == "BA" else BD_COLUMN
        with open(path, "w", newline="", encoding="utf-8") as handle:
            for line in list(comments) or self.comments:
                handle.write("# %s\n" % line)
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(KEY_COLUMNS + [col])
            for material in self.materials:
                gauge_of = {t: g for g, t in self._gauges.get(material, {}).items()}
                for thickness, (_r, _a, cell) in sorted(self._grids[material].items()):
                    for (radius, angle), value in sorted(cell.items()):
                        writer.writerow([material, gauge_of.get(thickness, ""), repr(thickness),
                                         repr(radius), repr(angle), repr(value)])

    def lookup(self, material=None, gauge=None):
        """A BendTableLookup bound to one material (and optionally a gauge) of this table."""
        return BendTableLookup(self, self._material(material), gauge)


class BendTableLookup:
    """What a part names instead of a K-factor: one table, one material, optional gauge.

    ``allowance`` is what the new unfolder calls; ``k_factor_for_bend`` is what the old
    unfolder's tree nodes call (it turns the table value into the K-factor that reproduces it,
    so both unfolders flatten a bend to the same length). Thickness is the measured sheet
    thickness; when a gauge is named, it must agree with the part's thickness.
    """

    def __init__(self, table, material, gauge=None):
        self.table = table
        self.material = material
        self.gauge = gauge
        self.gauge_thickness = table.thickness_of_gauge(material, gauge) if gauge else None

    def _check_gauge(self, thickness):
        if self.gauge_thickness is not None and abs(self.gauge_thickness - thickness) > 1e-3:
            raise BendTableRangeError(
                "gauge %s is %.9g mm in table %s but the part is %.9g mm thick"
                % (self.gauge, self.gauge_thickness, self.table.name, thickness)
            )

    def allowance(self, thickness, inside_radius, angle_deg):
        self._check_gauge(thickness)
        return self.table.allowance(thickness, inside_radius, angle_deg, self.material)

    def deduction(self, thickness, inside_radius, angle_deg):
        self._check_gauge(thickness)
        return self.table.deduction(thickness, inside_radius, angle_deg, self.material)

    def k_factor_for_bend(self, thickness, inside_radius, angle_deg):
        self._check_gauge(thickness)
        return self.table.equivalent_k_factor(thickness, inside_radius, angle_deg, self.material)


# -- finding tables by name ----------------------------------------------------------------

PREFIX = "bt_"


def list_table_files(directory=None):
    directory = directory or TABLE_DIR
    if not os.path.isdir(directory):
        return []
    return sorted(f for f in os.listdir(directory) if f.endswith(".csv"))


def load_table(name, directory=None):
    """Load ``<directory>/<name>.csv``; raises BendTableError with the available names."""
    directory = directory or TABLE_DIR
    path = os.path.join(directory, name + ".csv")
    if not os.path.isfile(path):
        have = ", ".join(os.path.splitext(f)[0] for f in list_table_files(directory)) or "none"
        raise BendTableError("no bend table named %r in %s (available: %s)" % (name, directory, have))
    return BendTable.from_file(path)


def is_bend_table_ref(name):
    return isinstance(name, str) and name.startswith(PREFIX)


def parse_ref(ref):
    """``bt_<table>[/<material>[/<gauge>]]`` -> (table, material or None, gauge or None)."""
    if not is_bend_table_ref(ref):
        raise BendTableError("%r is not a bend table reference (expected bt_<table>[/<material>])" % (ref,))
    parts = ref[len(PREFIX):].split("/")
    if not parts[0] or len(parts) > 3:
        raise BendTableError("bend table reference %r should read bt_<table>[/<material>[/<gauge>]]" % (ref,))
    parts += [None] * (3 - len(parts))
    return parts[0], parts[1] or None, parts[2] or None


def lookup_from_ref(ref, directory=None):
    """The BendTableLookup a part's MaterialSheet string ``bt_<table>[/<material>[/<gauge>]]`` names."""
    table, material, gauge = parse_ref(ref)
    return load_table(table, directory).lookup(material, gauge)


def list_refs(directory=None):
    """Every ``bt_<table>/<material>`` reference the shipped (or given) directory offers."""
    refs = []
    for fname in list_table_files(directory):
        try:
            table = BendTable.from_file(os.path.join(directory or TABLE_DIR, fname))
        except BendTableError:
            continue
        refs.extend("%s%s/%s" % (PREFIX, table.name, m) for m in table.materials)
    return refs
