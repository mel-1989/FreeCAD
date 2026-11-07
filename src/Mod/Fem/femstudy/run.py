# SPDX-License-Identifier: LGPL-2.1-or-later
"""The level loop: Gmsh at h0, h0/ratio, h0/ratio^2, a CalculiX deck from the stock writer (edited in the study's own working directory), ccx,
the outputs read back, post-solve checks per level and the element cap."""

import math
import os
import shutil
import tempfile
import time

import FreeCAD

from . import checks, deck, model
from .checks import ToolFailure


def applied_vector(doc, spec, k, value):
    """The resultant the spec requested for load `k`, in N: force by value and direction, pressure by integrating over the faces as they are,
    gravity as mass times acceleration."""
    ld = spec["loads"][k]
    if ld["type"] == "force":
        d = FreeCAD.Vector(*ld["direction"])
        d.normalize()
        return [value * d.x, value * d.y, value * d.z]
    if ld["type"] == "gravity":
        vol = doc.getObject(spec["body"]).Shape.Volume
        from .spec import resolve_material

        rho = resolve_material(spec["material"])["density"]
        if rho is None:
            from .spec import Refusal

            raise Refusal("material_incomplete", "gravity needs a density in the material")
        d = FreeCAD.Vector(*ld["direction"])
        d.normalize()
        m = rho * vol * 1e-9  # kg
        f = m * value * 1e-3  # N, with the acceleration in mm/s^2
        return [f * d.x, f * d.y, f * d.z]
    tot = [0.0, 0.0, 0.0]
    for o, s in ld["refs"]:
        face = model.element(doc, o, s)
        pts, tris = face.tessellate(0.002)
        for a, b, c in tris:
            A, B, C = pts[a], pts[b], pts[c]
            area = (B - A).cross(C - A).Length / 2
            cen = (A + B + C) * (1.0 / 3.0)
            u, v = face.Surface.parameter(cen)
            n = face.normalAt(u, v)
            for i in range(3):
                tot[i] += -value * n[i] * area
    return tot


def _clean(path):
    if not os.environ.get("CADFORK_STUDY_KEEP"):
        shutil.rmtree(path, ignore_errors=True)


def _one_level(doc, an, spec, objs, loads_v, applied, requested, h, k, bbox):
    import Fem
    from femmesh import gmshtools
    from femtools import ccxtools

    mesh, sol = objs["mesh"], objs["solver"]
    wd = tempfile.mkdtemp(prefix=f"study_L{k}_")
    try:
        mesh.WorkingDirectory = wd
        mesh.CharacteristicLengthMax = f"{h:.12g} mm"
        mesh.FemMesh = Fem.FemMesh()  # an empty mesh, so a failed Gmsh run cannot leave the previous level's mesh behind
        err = gmshtools.GmshTools(mesh).create_mesh()
        elements = mesh.FemMesh.VolumeCount
        if not elements:
            raise ToolFailure("mesher_failed", f"Gmsh produced no volume elements for {spec['body']} at h = {h:g} mm ({(err or '')[:160].strip()})")
        fea = ccxtools.FemToolsCcx(an, sol, test_mode=False)
        fea.update_objects()
        fea.setup_working_dir(wd)
        fea.setup_ccx()
        if not fea.ccx_binary_present:
            raise ToolFailure("solver_missing", "the ccx binary found does not answer as CalculiX")
        msg = fea.check_prerequisites()
        if msg:
            raise ToolFailure("solver_failed", "FEM prerequisites failed: " + " ".join(msg.split())[:200])
        fea.write_inp_file()
        inp = fea.inp_file_name
        info = deck.prepare(inp, requested)
        t1 = time.time()
        rc = fea.ccx_run()
        solve_seconds = time.time() - t1
        if rc != 0:
            raise ToolFailure("solver_failed", f"ccx exited with code {rc} at h = {h:g} mm: {' '.join(((fea.ccx_stderr or '') + (fea.ccx_stdout or ''))[-200:].split())}")
        base = os.path.splitext(inp)[0]
        for ext in (".frd", ".dat"):
            if not os.path.isfile(base + ext) or os.path.getsize(base + ext) == 0:
                raise ToolFailure("solver_failed", f"ccx wrote no {ext} file at h = {h:g} mm")
        dat = deck.parse_dat(open(base + ".dat").read())
        before = {o.Name for o in an.Group}
        fea.load_results_ccxfrd()
        doc.recompute()
        new = [o for o in an.Group if o.Name not in before and o.isDerivedFrom("Fem::FemResultObject")]
        if not new:
            raise ToolFailure("solver_failed", f"ccx finished but FreeCAD read no result object from the .frd file at h = {h:g} mm")
        res = new[-1]
        if not len(res.DisplacementLengths):
            raise ToolFailure("solver_failed", f"the result object {res.Name} holds no displacements at h = {h:g} mm")
        union = dat["rf"].get("NSTUDYSUPPORT")
        if union is None or dat["energy"] is None:
            raise ToolFailure("solver_failed", f"the .dat file lacks the reaction or energy totals at h = {h:g} mm")
        vals = list(res.DisplacementLengths) + list(res.vonMises) + union + [dat["energy"]]
        if checks.has_nan(vals):
            raise ToolFailure("result_nan", f"the ccx result contains nan or inf at h = {h:g} mm")
        lc = info["load_on_constrained"]
        lv = {
            "h": h,
            "nodes": mesh.FemMesh.NodeCount,
            "elements": elements,
            "max_displacement": max(res.DisplacementLengths),
            "energy": dat["energy"],
            "max_von_mises": max(res.vonMises),
            "reaction_sum": [union[i] - lc[i] for i in range(3)],
            "applied_sum": list(applied),
            "rf_total": list(union),
            "load_on_constrained": list(lc),
            "delivered_sum": [sum(v[c] for v in info["applied_nodal"].values()) for c in range(3)],
            "solve_seconds": round(solve_seconds, 3),
            "result": res.Name,
        }
        return lv, info
    finally:
        _clean(wd)


def solve_levels(doc, an, spec, objs, loads_v, h0, bbox, yield_mpa):
    """Run the levels. Returns (levels, warnings, stop, cap_hit): `stop` is None or (verdict, code, reason) for a failed post-solve check."""
    opts = spec["options"]
    ratio, n = opts["ratio"], opts["levels"]
    cap = int(opts["max_elements"])
    load_scale = 1.0
    applied = [0.0, 0.0, 0.0]
    requested = {}
    for k, ld in enumerate(spec["loads"]):
        v = applied_vector(doc, spec, k, loads_v[k])
        for i in range(3):
            applied[i] += v[i]
        if ld["type"] in ("force", "pressure"):
            requested[objs["loads"][k].Name] = [x * load_scale for x in v]
    levels, warns, worst = [], [], {}
    stop, cap_hit = None, False
    for k in range(n):
        h = h0 / ratio ** k
        if levels and levels[-1]["elements"] * ratio**3 > cap:
            cap_hit = True
            break
        lv, info = _one_level(doc, an, spec, objs, loads_v, applied, requested, h, k, bbox)
        if lv["elements"] > cap:  # the first mesh is already over the cap: nothing is solved beyond it
            cap_hit = True
            levels.append(lv)
            break
        if levels and lv["elements"] <= levels[-1]["elements"]:
            levels.append(lv)
            stop = ("unconverged", "mesh_not_refined", f"level {k} has {lv['elements']} elements, no more than level {k - 1} ({levels[-2]['elements']}): the mesh did not get finer, so no convergence statement is possible")
            break
        levels.append(lv)
        for name, s in info["scale"].items():
            if abs(s - 1.0) > 1e-3 and abs(s - 1.0) > abs(worst.get(name, 1.0) - 1.0) - 1e-15:
                worst[name] = s
        for name, rel in info["unscaled"].items():
            warns.append({"code": "load_mismatch", "text": f"{name}: the load delivered to the mesh differs from the request by {rel:.2%} and was not corrected"})
        rel = checks.check_equilibrium(lv["reaction_sum"], lv["applied_sum"])
        if rel is not None:
            stop = ("suspect", "equilibrium", f"at h = {h:g} mm the reaction forces differ from the applied load by {rel:.2e} (limit {checks.EQUILIBRIUM_TOL:g}) in study {spec['name']}")
            break
        msg = checks.check_displacement(lv["max_displacement"], bbox)
        if msg:
            stop = ("suspect", "large_displacement", f"{msg}: linear theory is not trustworthy for body {spec['body']}")
            break
    if stop is None and levels and not cap_hit:
        msg = checks.check_yield(levels[-1]["max_von_mises"], yield_mpa)
        if msg:
            stop = ("suspect", "yield_exceeded", f"{msg} in body {spec['body']}")
    for name, s in worst.items():
        warns.append({"code": "load_corrected", "text": f"{name}: the stock writer delivered {1 / s:.4f} times the requested load to the mesh; the nodal loads were scaled by {s:.6f} to match the request"})
    return levels, warns, stop, cap_hit
