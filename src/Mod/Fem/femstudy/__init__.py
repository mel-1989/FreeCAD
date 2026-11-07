# SPDX-License-Identifier: LGPL-2.1-or-later
"""femstudy: a static study with honest results (G11 slice 1a). See SPEC.md in this folder for the contract.

    name = femstudy.define_static(doc, spec)       # builds the Analysis and its stock objects, stores the spec; does not mesh or solve
    result = femstudy.run_static(doc, spec_or_name)  # meshes at three sizes, solves, checks, returns a verdict dict
    femstudy.is_stale(doc, name), femstudy.stored_result(doc, name)
"""

import json
import time
import traceback

import FreeCAD

from . import checks, convergence, model, run, stale
from .checks import ToolFailure
from .spec import Refusal, StudySpecError, parse_quantity, parse_spec, resolve_material

__all__ = ["define_static", "run_static", "is_stale", "stored_result", "StudySpecError", "parse_spec", "parse_quantity"]

UNITS = {"length": "mm", "force": "N", "stress": "MPa", "energy": "mJ"}
PROPS = (("StudySpec", "JSON of the study spec"), ("InputHash", "Hash of the inputs of the last run"), ("ResultJSON", "The last result as JSON"))


def _ensure_props(an):
    for p, d in PROPS:
        if p not in an.PropertiesList:
            an.addProperty("App::PropertyString", p, "Study", d)


def define_static(doc, spec):
    """Create (or replace the contents of) the study's Analysis; returns its name."""
    s = parse_spec(spec)
    an = model.make_analysis(doc, s["name"])
    _ensure_props(an)
    try:
        loads = None
        mat = None
        model.build(doc, an, s, None, mat, loads)
    finally:
        an.StudySpec = json.dumps(s, sort_keys=True)
        an.InputHash = ""
        an.ResultJSON = ""
    doc.recompute()
    return an.Name


def _analysis(doc, name):
    an = doc.getObject(name)
    if an is None or "StudySpec" not in getattr(an, "PropertiesList", []):
        raise StudySpecError(f"{name!r} is not a study Analysis in this document")
    return an


def _stored_spec(an):
    return parse_spec(json.loads(an.StudySpec))


def _live(doc, an):
    return model.read_live(doc, an)


def _result(spec, an, verdict, code, reason, levels=None, warnings=None, a=None):
    r = {
        "verdict": verdict,
        "code": code,
        "reason": reason,
        "units": dict(UNITS),
        "levels": levels or [],
        "extrapolated": a["extrapolated"] if a else None,
        "order": a["order"] if a else None,
        "estimated_error": a["estimated_error"] if a else None,
        "warnings": warnings or [],
        "analysis": an.Name,
        "stale": False,
    }
    return r


def _execute(doc, an, spec, live, lost):
    try:
        checks.check_support_and_load(spec)
        mat = resolve_material(spec["material"])
        loads_v = model.checked_loads(spec)
        h0 = parse_quantity(spec["options"]["h0"], "length", "options.h0")
        if not h0 > 0:
            raise Refusal("unit_dimension", "options.h0 must be a positive length")
        body = checks.check_body(doc, spec)
        checks.check_references(doc, spec, live, lost)
        mass = mat["density"] * body.Shape.Volume * 1e-9 if mat["density"] is not None else None  # kg
        load_scale = checks.check_loads(doc, spec, loads_v, mass)
        checks.check_rigid_body(doc, spec, live)
        checks.check_mesh_size(body, h0, int(spec["options"]["max_elements"]))
        checks.check_tools()
        model.clear(doc, an)
        objs = model.build(doc, an, spec, live, mat, loads_v)
        levels, warns, stop, cap_hit = run.solve_levels(doc, an, spec, objs, loads_v, h0, body.Shape.BoundBox, mat["yield"], load_scale)
        a = convergence.assess(levels, spec["options"]["target"]) if len(levels) >= 2 else None
        if levels:
            warns = warns + convergence.stress_warnings(levels, checks.smallest_circular_radius(body.Shape))
        if stop is not None:
            return _result(spec, an, stop[0], stop[1], stop[2], levels, warns, a)
        v, c, why = convergence.verdict(levels, spec["options"]["target"], cap_hit, a)
        return _result(spec, an, v, c, why, levels, warns, a)
    except Refusal as r:
        return _result(spec, an, "refused", r.code, r.reason)
    except ToolFailure as t:
        return _result(spec, an, "failed", t.code, t.reason)
    except Exception as ex:  # noqa: BLE001  never a crash: an unexpected error is a named failure
        FreeCAD.Console.PrintError(traceback.format_exc())
        return _result(spec, an, "failed", "internal_error", f"{type(ex).__name__} in study {spec['name']}: {str(ex)[:200]}")


def run_static(doc, spec_or_name):
    """Run a study. A dict is defined first (replacing a study of the same name); a name runs what is stored, resolving the references now.
    Raises nothing but StudySpecError; anything else that goes wrong outside a study run comes back as a `failed` / `internal_error` result."""
    try:
        return _run_static(doc, spec_or_name)
    except StudySpecError:
        raise
    except Exception as ex:  # noqa: BLE001
        FreeCAD.Console.PrintError(traceback.format_exc())
        return {"verdict": "failed", "code": "internal_error", "reason": f"{type(ex).__name__} outside the study run: {str(ex)[:200]}", "units": dict(UNITS), "levels": [],
                "extrapolated": None, "order": None, "estimated_error": None, "warnings": [], "analysis": None, "stale": False}


def _run_static(doc, spec_or_name):
    if isinstance(spec_or_name, str):
        an = _analysis(doc, spec_or_name)
        reuse = True
    else:
        an = _analysis(doc, define_static(doc, spec_or_name))
        reuse = False
    spec = _stored_spec(an)
    live, lost = _live(doc, an)
    input_hash = stale.input_hash(doc, spec, live)
    stored_hash = an.InputHash
    cached = None
    if reuse and an.ResultJSON and stored_hash:
        try:
            cached = json.loads(an.ResultJSON)
            if any(doc.getObject(lv.get("result", "")) is None for lv in cached["levels"]):
                cached = None
        except ValueError:
            cached = None
    if input_hash != stored_hash or not cached:
        res = _execute(doc, an, spec, live, lost)
        live2, _ = _live(doc, an)
        an.InputHash = stale.input_hash(doc, spec, live2 if live2 else live)
        res["stale"] = False
    else:
        res = cached
        res["stale"] = False
    an.ResultJSON = json.dumps(res)
    return res


def is_stale(doc, name):
    """True when the stored result does not belong to the model as it is now (recomputed on every call)."""
    an = _analysis(doc, name)
    spec = _stored_spec(an)
    live, _ = _live(doc, an)
    stored = an.InputHash
    h = stale.input_hash(doc, spec, live)
    return stale.stale_flag(h, stored)


def stored_result(doc, name):
    """The last result of study `name` with its `stale` flag set from the current model, or None before any run."""
    an = _analysis(doc, name)
    if not an.ResultJSON:
        return None
    res = json.loads(an.ResultJSON)
    res["stale"] = is_stale(doc, name)
    return res
