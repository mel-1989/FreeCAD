# SPDX-License-Identifier: LGPL-2.1-or-later
"""Pre-solve checks (refusals and tool checks, raised as `Refusal` or `ToolFailure`) and post-solve checks (return `suspect` findings)."""

import math
import os
import shutil
from math import isinf, isnan

import FreeCAD

from . import model
from .spec import Refusal

EQUILIBRIUM_TOL = 1e-3  # relative, |reaction_sum + applied_sum| / |applied_sum|
LARGE_DISPLACEMENT_FRACTION = 0.10  # of the smallest bounding-box edge


class ToolFailure(Exception):
    """A `failed` verdict: a tool is missing or broke. `code` is one of mesher_missing, solver_missing, mesher_failed, solver_failed, result_nan."""

    def __init__(self, code, reason):
        super().__init__(reason)
        self.code = code
        self.reason = reason


# ------------------------------------------------------------------------------------------------------------------------ refusals
def check_support_and_load(spec):
    if not spec["supports"]:
        raise Refusal("no_support", f"study {spec['name']} has no support: the body would move freely")
    if not spec["loads"]:
        raise Refusal("no_load", f"study {spec['name']} has no load: the answer would be zero displacement")


def check_body(doc, spec):
    name = spec["body"]
    ob = doc.getObject(name)
    if ob is None:
        raise Refusal("body_invalid", f"body {name!r} does not exist in the document")
    sh = getattr(ob, "Shape", None)
    if sh is None or sh.isNull():
        raise Refusal("body_invalid", f"body {name!r} has no shape")
    if not sh.Solids or not sh.Volume > 1e-9:
        raise Refusal("body_invalid", f"body {name!r} is not a solid with volume (shape type {sh.ShapeType}, volume {sh.Volume:g} mm^3)")
    if len(sh.Solids) > 1:
        raise Refusal("not_single_solid", f"body {name!r} holds {len(sh.Solids)} separate solids: the rigid-body and equilibrium checks are global, so a floating solid would pass them unseen (multibody studies are G15)")
    return ob


# the corpus (236 levels) has at least 4.9 tetrahedra per h^3 / volume; 4.0 (80% of that) is the lower bound used, so a case the corpus can solve is never stopped
ELEMENTS_PER_H3 = 4.0
MAX_H0_OVER_BODY = 10.0  # an h0 more than ten times the longest bounding-box edge is a typo in the unit, not a mesh size


def estimate_elements(volume, h):
    return ELEMENTS_PER_H3 * volume / h**3


def check_mesh_size(body, h0, cap):
    """Before any meshing: h0 against the body, and the first level against the element cap (lower-bound estimate)."""
    bb = body.Shape.BoundBox
    size = max(bb.XLength, bb.YLength, bb.ZLength)
    if h0 > MAX_H0_OVER_BODY * size:
        raise Refusal("h0_range", f"options.h0 = {h0:g} mm is more than {MAX_H0_OVER_BODY:g} times the longest edge of body {body.Name} ({size:g} mm); check the unit")
    n = estimate_elements(body.Shape.Volume, h0)
    if n > cap:
        raise Refusal("element_cap", f"the first mesh at h0 = {h0:g} mm would hold at least {n:.0f} elements for body {body.Name} (volume {body.Shape.Volume:.4g} mm^3), above the cap of {cap}: nothing was meshed")


def smallest_circular_radius(shape):
    """Smallest radius among the circular edges (circles and arcs) of `shape`, or None."""
    rs = []
    for e in shape.Edges:
        try:
            c = e.Curve
            if type(c).__name__ == "Circle":
                rs.append(c.Radius)
        except Exception:  # noqa: BLE001
            pass
    return min(rs) if rs else None


def load_size(doc, spec, k, value, mass_of_body):
    """The load's own size, in N: |force|, |pressure| times the area of its faces, |acceleration| times the mass. Zero for a load that delivers nothing."""
    ld = spec["loads"][k]
    if ld["type"] == "force":
        return abs(value)
    if ld["type"] == "gravity":
        if mass_of_body is None:
            raise Refusal("material_incomplete", "gravity needs a density in the material")
        return abs(value) * 1e-3 * mass_of_body
    area = 0.0
    for o, s in ld["refs"]:
        sh = model.element(doc, o, s)
        if sh.ShapeType != "Face":
            raise Refusal("no_load", f"loads[{k}] (pressure) acts on {o}.{s}, which is {'an edge' if sh.ShapeType == 'Edge' else 'a vertex'} with no area: a pressure needs faces and this one would deliver nothing")
        area += sh.Area
    return abs(value) * area


def check_loads(doc, spec, loads_v, mass):
    """Each load must deliver something on its own. Decided per load, not on the sum: two forces that cancel are a legitimate load case.
    Returns the sum of the loads' own sizes (N), the scale for the equilibrium check."""
    total = 0.0
    for k in range(len(spec["loads"])):
        size = load_size(doc, spec, k, loads_v[k], mass)
        if not (size > 0 and math.isfinite(size)):
            raise Refusal("no_load", f"loads[{k}] ({spec['loads'][k]['type']}) delivers a total of {size:g}: the load is zero, so the answer would be zero displacement")
        total += size
    return total


def check_references(doc, spec, live, lost):
    """Every support and load reference must still resolve. `lost` comes from model.read_live."""
    if lost:
        role, k, ref, why = lost[0]
        kind = "support" if role == "S" else "load"
        raise Refusal("reference_lost", f"{kind} {k} ({why}); the study never guesses a face by its index")
    for role, items in (("S", spec["supports"]), ("L", spec["loads"])):
        for k, it in enumerate(items):
            if it.get("type") == "gravity":
                continue
            refs = live.get((role, k)) or it["refs"]
            if role == "S" and not refs:
                raise Refusal("reference_lost", f"support {k} has no reference left")
            if role == "L" and len(refs) < len(it["refs"]) and (role, k) in live:
                raise Refusal("reference_lost", f"load {k} lost {len(it['refs']) - len(refs)} of its references")
            for o, s in refs:
                try:
                    model.element(doc, o, s)
                except LookupError as ex:
                    raise Refusal("reference_lost", f"{'support' if role == 'S' else 'load'} {k}: {ex}") from None


def _sample_points(shape, size):
    pts = [v.Point for v in shape.Vertexes]
    t = shape.ShapeType
    if t == "Edge":
        try:
            pts += list(shape.discretize(7))
        except Exception:  # noqa: BLE001
            pass
    elif t == "Face":
        try:
            pts += list(shape.tessellate(max(size / 10.0, 1e-3))[0])
        except Exception:  # noqa: BLE001
            pass
        pts.append(shape.CenterOfMass)
    return pts


def check_rigid_body(doc, spec, live):
    """The supports must remove all six rigid-body modes: u = t + w x p, one row per held direction at each sampled support point."""
    import numpy as np

    body = doc.getObject(spec["body"])
    bb = body.Shape.BoundBox
    size = max(bb.XLength, bb.YLength, bb.ZLength) or 1.0
    rows = []
    for k, sp in enumerate(spec["supports"]):
        refs = live.get(("S", k)) or sp["refs"]
        for o, s in refs:
            for p in _sample_points(model.element(doc, o, s), size):
                x, y, z = p.x / size, p.y / size, p.z / size
                # unknowns (tx, ty, tz, wx, wy, wz); (w x p) = (wy z - wz y, wz x - wx z, wx y - wy x)
                for ax in sp["fixed"]:
                    rows.append({"x": [1, 0, 0, 0, z, -y], "y": [0, 1, 0, -z, 0, x], "z": [0, 0, 1, y, -x, 0]}[ax])
    if rows:
        sv = np.linalg.svd(np.array(rows, dtype=float), compute_uv=False)
        rank = int((sv > 1e-6 * sv[0]).sum())
    else:
        rank = 0
    if rank < 6:
        raise Refusal("rigid_body_motion", f"the supports of study {spec['name']} leave {6 - rank} rigid-body mode(s) free (rank {rank} of 6): the body can move or rotate without deforming")


# ------------------------------------------------------------------------------------------------------------------------ tools
def gmsh_binary():
    p = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem/Gmsh").GetString("gmshBinaryPath", "")
    return shutil.which(p) if p else shutil.which("gmsh")


def ccx_binary():
    p = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem/Ccx").GetString("ccxBinaryPath", "")
    return shutil.which(p) if p else shutil.which("ccx")


def check_tools():
    """Verify mesher and solver exist before anything is meshed (preferences first, then PATH, the stock route)."""
    if not gmsh_binary():
        raise ToolFailure("mesher_missing", "no gmsh binary on PATH or in the FEM preferences; the study cannot mesh the body")
    if not ccx_binary():
        raise ToolFailure("solver_missing", "no ccx (CalculiX) binary on PATH or in the FEM preferences; the study cannot solve")


# ------------------------------------------------------------------------------------------------------------------------ post-solve
def has_nan(values):
    return any(isnan(v) or isinf(v) for v in values)


def check_equilibrium(reaction, applied, scale=0.0):
    """None when |reaction + applied| / |applied| is within tolerance, else the relative miss. A nan fails. When the loads cancel (|applied| is
    nil against `scale`, the sum of the loads' own sizes) the miss is measured against `scale`, so a zero resultant is never a divisor."""
    na = math.sqrt(sum(a * a for a in applied))
    d = math.sqrt(sum((r + a) ** 2 for r, a in zip(reaction, applied)))
    den = na if na > 1e-9 * scale else scale
    if not den > 0:
        den = 1.0
    rel = d / den
    return None if rel <= EQUILIBRIUM_TOL else rel


def check_displacement(max_disp, bbox):
    """Finding text when the displacement exceeds 10% of the smallest bounding-box edge."""
    edge = min(bbox.XLength, bbox.YLength, bbox.ZLength)
    if not max_disp <= LARGE_DISPLACEMENT_FRACTION * edge:
        return f"{max_disp:.4g} mm is {max_disp / edge:.0%} of the smallest bounding-box edge ({edge:g} mm)"
    return None


def check_yield(max_vm, yield_mpa):
    if yield_mpa is not None and not max_vm <= yield_mpa:
        return f"max von Mises {max_vm:.4g} MPa exceeds the yield stress {yield_mpa:g} MPa"
    return None
