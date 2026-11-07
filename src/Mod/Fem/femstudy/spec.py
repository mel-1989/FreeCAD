# SPDX-License-Identifier: LGPL-2.1-or-later
"""Spec parsing for the static study (SPEC.md): structure and type errors raise `StudySpecError`, unit and range problems raise `Refusal`
(a returned `refused` verdict, never an exception to the caller)."""

import copy
import math
import re

KEYS_TOP = {"body", "material", "supports", "loads", "options", "name"}
REQUIRED_TOP = ("body", "material", "supports", "loads", "options")
KEYS_MATERIAL = {"E", "nu", "density", "yield"}
KEYS_SUPPORT = {"refs", "fixed"}
KEYS_LOAD = {"type", "value", "refs", "direction"}
KEYS_OPTIONS = {"h0", "levels", "ratio", "target", "max_elements"}
DEFAULT_OPTIONS = {"levels": 3, "ratio": 2, "target": 0.01, "max_elements": 200000}
LOAD_TYPES = ("force", "pressure", "gravity")
MIN_RATIO = 1.5  # a smaller refinement step makes the three-level estimate under-report the error (ratio 1.1 by a factor 40 in the run-067 check)
MAX_TARGET = 0.2  # a target above 20% turns "converged" into noise


class StudySpecError(ValueError):
    """A malformed spec: unknown key, wrong Python type, missing required key."""


class Refusal(Exception):
    """A pre-solve check failed: carries the `refused` code and a one-sentence reason naming the cause and the object."""

    def __init__(self, code, reason):
        super().__init__(reason)
        self.code = code
        self.reason = reason


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


SUB_RE = re.compile(r"^(Face|Edge|Vertex)[1-9][0-9]*$")


def _no_nonfinite(x, where="spec"):
    """Reject nan and inf anywhere in the spec (a number that is not a number is never a quantity, a direction or an option)."""
    if isinstance(x, float) and not math.isfinite(x):
        raise StudySpecError(f"{where} is {x!r}; every number in a spec must be finite")
    if isinstance(x, dict):
        for k, v in x.items():
            _no_nonfinite(v, f"{where}.{k}")
    elif isinstance(x, (list, tuple)):
        for i, v in enumerate(x):
            _no_nonfinite(v, f"{where}[{i}]")


def _check_keys(d, allowed, where):
    if not isinstance(d, dict):
        raise StudySpecError(f"{where} must be a dict, got {type(d).__name__}")
    extra = sorted(set(d) - allowed)
    if extra:
        raise StudySpecError(f"unknown key {extra[0]!r} in {where}")


def _refs(refs, where):
    if not isinstance(refs, (list, tuple)):
        raise StudySpecError(f"{where}.refs must be a list of [object, sub-element]")
    out = []
    for r in refs:
        if not (isinstance(r, (list, tuple)) and len(r) == 2 and all(isinstance(t, str) for t in r)):
            raise StudySpecError(f"{where}.refs entries must be [object name, sub-element name], got {r!r}")
        if not SUB_RE.match(r[1]):
            raise StudySpecError(f"{where}.refs sub-element {r[1]!r} is not a Face, Edge or Vertex name such as 'Face3'")
        out.append([r[0], r[1]])
    return out


def parse_spec(spec):
    """Validate `spec` and return a normalised deep copy (defaults filled in). Raises StudySpecError for structure and type errors only."""
    _check_keys(spec, KEYS_TOP, "spec")
    _no_nonfinite(spec)
    for k in REQUIRED_TOP:
        if k not in spec:
            raise StudySpecError(f"missing required key {k!r} in spec")
    s = copy.deepcopy(dict(spec))
    if not isinstance(s["body"], str):
        raise StudySpecError("spec.body must be an object name (str)")
    if "name" in s and not (isinstance(s["name"], str) and s["name"]):
        raise StudySpecError("spec.name must be a non-empty str")
    s.setdefault("name", "StaticStudy")

    m = s["material"]
    if not isinstance(m, str):
        _check_keys(m, KEYS_MATERIAL, "material")
        if "nu" in m:
            nu = m["nu"]
            if isinstance(nu, str):
                try:
                    nu = float(nu)
                except ValueError:
                    raise StudySpecError(f"material.nu must be numeric, got {m['nu']!r}") from None
            if not _num(nu) or not math.isfinite(nu):
                raise StudySpecError(f"material.nu must be numeric, got {m['nu']!r}")
            m["nu"] = float(nu)
        for k in ("E", "density", "yield"):
            if k in m and not (isinstance(m[k], str) or _num(m[k])):
                raise StudySpecError(f"material.{k} must be a quantity string, got {type(m[k]).__name__}")

    if not isinstance(s["supports"], (list, tuple)):
        raise StudySpecError("spec.supports must be a list")
    s["supports"] = list(s["supports"])
    for i, sp in enumerate(s["supports"]):
        _check_keys(sp, KEYS_SUPPORT, f"supports[{i}]")
        for k in KEYS_SUPPORT:
            if k not in sp:
                raise StudySpecError(f"missing required key {k!r} in supports[{i}]")
        sp["refs"] = _refs(sp["refs"], f"supports[{i}]")
        f = sp["fixed"]
        if not (isinstance(f, str) and f and set(f) <= set("xyz")):
            raise StudySpecError(f"supports[{i}].fixed must be a non-empty string over the letters x y z, got {f!r}")

    if not isinstance(s["loads"], (list, tuple)):
        raise StudySpecError("spec.loads must be a list")
    s["loads"] = list(s["loads"])
    for i, ld in enumerate(s["loads"]):
        _check_keys(ld, KEYS_LOAD, f"loads[{i}]")
        t = ld.get("type")
        if t not in LOAD_TYPES:
            raise StudySpecError(f"loads[{i}].type must be one of {LOAD_TYPES}, got {t!r}")
        need = {"force": ("value", "refs", "direction"), "pressure": ("value", "refs"), "gravity": ("value", "direction")}[t]
        for k in need:
            if k not in ld:
                raise StudySpecError(f"missing required key {k!r} in loads[{i}] ({t})")
        extra = set(ld) - set(need) - {"type"}
        if extra:
            raise StudySpecError(f"key {sorted(extra)[0]!r} does not apply to a {t} load (loads[{i}])")
        if not (isinstance(ld["value"], str) or _num(ld["value"])):
            raise StudySpecError(f"loads[{i}].value must be a quantity string, got {type(ld['value']).__name__}")
        if "refs" in ld:
            ld["refs"] = _refs(ld["refs"], f"loads[{i}]")
            if not ld["refs"]:
                raise StudySpecError(f"loads[{i}].refs is empty")
        if "direction" in ld:
            d = ld["direction"]
            if not (isinstance(d, (list, tuple)) and len(d) == 3 and all(_num(x) for x in d)):
                raise StudySpecError(f"loads[{i}].direction must be three numbers, got {d!r}")
            if not any(d):
                raise StudySpecError(f"loads[{i}].direction is the zero vector")
            if not all(math.isfinite(x) for x in d) or not math.isfinite(sum(x * x for x in d)) or sum(x * x for x in d) == 0:
                raise StudySpecError(f"loads[{i}].direction {d!r} is not a usable vector")
            ld["direction"] = [float(x) for x in d]

    o = s["options"]
    _check_keys(o, KEYS_OPTIONS, "options")
    if "h0" not in o:
        raise StudySpecError("missing required key 'h0' in options")
    if not (isinstance(o["h0"], str) or _num(o["h0"])):
        raise StudySpecError("options.h0 must be a quantity string")
    for k, typ in (("levels", int), ("max_elements", int)):
        if k in o and (not isinstance(o[k], int) or isinstance(o[k], bool)):
            raise StudySpecError(f"options.{k} must be an int, got {o[k]!r}")
    for k in ("ratio", "target"):
        if k in o and not _num(o[k]):
            raise StudySpecError(f"options.{k} must be a number, got {o[k]!r}")
    for k, v in DEFAULT_OPTIONS.items():
        o.setdefault(k, v)
    if not 2 <= o["levels"] <= 6:
        raise StudySpecError(f"options.levels must be between 2 and 6, got {o['levels']}")
    if not (math.isfinite(o["ratio"]) and o["ratio"] >= MIN_RATIO):
        raise StudySpecError(f"options.ratio must be a finite number of at least {MIN_RATIO:g} (a smaller step makes the Richardson estimate unreliable), got {o['ratio']}")
    if not (math.isfinite(o["target"]) and 0 < o["target"] <= MAX_TARGET):
        raise StudySpecError(f"options.target must be above 0 and at most {MAX_TARGET:g}, got {o['target']}")
    if o["max_elements"] < 1:
        raise StudySpecError("options.max_elements must be positive")
    return s


# ---------------------------------------------------------------------------------------------------------------------- quantities
def _units():
    from FreeCAD import Units

    return Units


def to_mpa(q):
    return q.getValueAs("MPa").Value


def _kind(name):
    U = _units()
    return {"pressure": U.Pressure, "force": U.Force, "length": U.Length, "density": U.Density, "acceleration": U.Acceleration}[name]


def parse_quantity(value, dim, what):
    """Value of the quantity `value` (a string with a unit) in the base unit of `dim`: pressure MPa, force N, length mm, density kg/m^3,
    acceleration mm/s^2. `what` names the property for the refusal reason. A bare number or a string without a unit is `unit_missing`, an
    unparseable unit (`kip`) is `unit_unknown`, a unit of the wrong dimension is `unit_dimension`."""
    U = _units()
    if isinstance(value, bool) or not (isinstance(value, str) or _num(value)):
        raise Refusal("unit_missing", f"{what} is not a quantity with a unit")
    if _num(value):
        raise Refusal("unit_missing", f"{what} is the bare number {value!r}; a unit is required and no unit is assumed")
    text = value.strip()
    try:
        float(text)
        bare = True
    except ValueError:
        bare = False
    if bare:
        raise Refusal("unit_missing", f"{what} is {value!r} with no unit; a unit is required and no unit is assumed")
    try:
        q = U.Quantity(text)
    except Exception:  # noqa: BLE001  FreeCAD raises a plain exception with a parser message
        raise Refusal("unit_unknown", f"{what} = {value!r} has a unit FreeCAD does not know") from None
    if not math.isfinite(q.Value):
        raise Refusal("value_not_finite", f"{what} = {value!r} is not a finite number")
    if q.Unit == U.Unit():
        raise Refusal("unit_missing", f"{what} is {value!r} with no unit; a unit is required and no unit is assumed")
    if q.Unit != _kind(dim):
        raise Refusal("unit_dimension", f"{what} = {value!r} has the dimension {q.Unit.Type or q.Unit}, expected {dim}")
    if dim == "pressure":
        return to_mpa(q)
    if dim == "force":
        return q.getValueAs("N").Value
    if dim == "length":
        return q.getValueAs("mm").Value
    if dim == "density":
        return q.getValueAs("kg/m^3").Value
    return q.getValueAs("mm/s^2").Value


def resolve_material(m):
    """Material values in base units: {'E': MPa, 'nu', 'density': kg/m^3 or None, 'yield': MPa or None}. Raises Refusal."""
    if isinstance(m, str):
        raise Refusal("material_incomplete", f"material card {m!r} cannot be looked up yet (material cards arrive in slice 4); give E and nu")
    for k in ("E", "nu"):
        if k not in m:
            raise Refusal("material_incomplete", f"material has no {k}; a static study needs E and nu")
    out = {"E": parse_quantity(m["E"], "pressure", "material E")}
    out["density"] = parse_quantity(m["density"], "density", "material density") if "density" in m else None
    out["yield"] = parse_quantity(m["yield"], "pressure", "material yield") if "yield" in m else None
    nu = m["nu"]
    if not (-1.0 < nu < 0.5):
        raise Refusal("poisson_range", f"material nu = {nu:g} is outside (-1, 0.5): nu = 0.5 makes a solid incompressible and nu = -1 makes the shear-to-bulk ratio singular, and the stiffness is singular at both")
    out["nu"] = nu
    if not (math.isfinite(out["E"]) and out["E"] > 0):
        raise Refusal("material_incomplete", "material E is not positive")
    return out
