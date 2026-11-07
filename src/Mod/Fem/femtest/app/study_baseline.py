# SPDX-License-Identifier: LGPL-2.1-or-later
"""Stock-pipeline baseline for the G11 corpus (run 067): the FEM objects a user would build by hand, no verdicts, no checks.

`run_stock(doc, spec, h, workdir)` translates a SPEC.md spec into ObjectsFem objects, meshes with `gmshtools.GmshTools` (second order, one global
CharacteristicLengthMax = h), writes the input deck with `ccxtools.FemToolsCcx`, adds the energy request, runs CalculiX and reads results back.
It reports what the stock pipeline produces and never judges it. It runs inside FreeCAD (`FreeCADCmd`); the driver calls it.
"""

import math
import os
import re
import time


def _vec(d):
    return [float(x) for x in d]


def _body(doc, spec):
    return doc.getObject(spec["body"])


def _refs(doc, refs):
    return [(doc.getObject(o), s) for o, s in refs]


def _edge_along(body, direction):
    """A straight edge of `body` parallel to `direction` (an axis vector), and the sign that makes it point along `direction`."""
    import FreeCAD

    want = FreeCAD.Vector(*_vec(direction)).normalize()
    for i, e in enumerate(body.Shape.Edges, 1):
        if type(e.Curve).__name__ != "Line":
            continue
        t = e.tangentAt(e.FirstParameter)
        d = t.dot(want)
        if abs(abs(d) - 1.0) < 1e-9:
            return f"Edge{i}", d < 0
    raise LookupError(f"no straight edge parallel to {direction}: the stock Force constraint needs one (or a face) to carry its direction")


def build_stock(doc, spec, h):
    """Create Analysis, solver, material, constraints and a Gmsh mesh object for `spec` in `doc`. Returns (analysis, solver, mesh)."""
    import FreeCAD
    import ObjectsFem

    body = _body(doc, spec)
    an = ObjectsFem.makeAnalysis(doc, "StockAnalysis")
    sol = ObjectsFem.makeSolverCalculiXCcxTools(doc, "StockSolver")
    sol.AnalysisType = "static"
    an.addObject(sol)
    m = spec["material"]
    mo = ObjectsFem.makeMaterialSolid(doc, "StockMaterial")
    d = dict(mo.Material)
    d["Name"] = "StockMat"
    d["YoungsModulus"] = str(m["E"])  # handed over as written: the stock card does not validate units
    d["PoissonRatio"] = str(m["nu"])
    if "density" in m:
        d["Density"] = str(m["density"])
    mo.Material = d
    an.addObject(mo)
    for k, s in enumerate(spec["supports"]):
        if s["fixed"] == "xyz":
            c = ObjectsFem.makeConstraintFixed(doc, f"StockFixed{k}")
        else:
            c = ObjectsFem.makeConstraintDisplacement(doc, f"StockDisp{k}")
            for ax in "xyz":
                if ax in s["fixed"]:
                    setattr(c, ax + "Free", False)
                    setattr(c, ax + "Displacement", 0.0)
        c.References = _refs(doc, s["refs"])
        an.addObject(c)
    for k, ld in enumerate(spec["loads"]):
        if ld["type"] == "force":
            c = ObjectsFem.makeConstraintForce(doc, f"StockForce{k}")
            c.References = _refs(doc, ld["refs"])
            c.Force = ld["value"]
            try:
                edge, rev = _edge_along(body, ld["direction"])
                c.Direction = (body, [edge])
            except LookupError:  # a round body has no straight edge in that direction: the user would draw a datum line; so do we
                import Part

                dv = FreeCAD.Vector(*_vec(ld["direction"])).normalize()
                ref = doc.addObject("Part::Feature", f"StockDir{k}")
                ref.Shape = Part.makeLine(FreeCAD.Vector(0, 0, 0), dv * 10)
                doc.recompute()
                c.Direction = (ref, ["Edge1"])
                rev = False
            c.Reversed = rev
        elif ld["type"] == "pressure":
            c = ObjectsFem.makeConstraintPressure(doc, f"StockPressure{k}")
            c.References = _refs(doc, ld["refs"])
            c.Pressure = ld["value"]
        else:
            raise NotImplementedError(ld["type"])
        an.addObject(c)
    mesh = ObjectsFem.makeMeshGmsh(doc, "StockMesh")
    mesh.Shape = body
    mesh.CharacteristicLengthMax = f"{h:g} mm"
    mesh.ElementOrder = "2nd"
    mesh.ElementDimension = "3D"
    if os.environ.get("CADFORK_BASELINE_SERIAL") == "1":
        mesh.ParallelProcessing = False  # Gmsh with General.NumThreads = 1: the multi-threaded default gives a different mesh per run
    an.addObject(mesh)
    doc.recompute()
    return an, sol, mesh


def applied_sum(doc, spec):
    """Resultant of the applied loads in N, from the spec: forces directly, pressures by integrating over the referenced faces."""
    import FreeCAD
    from FreeCAD import Units

    tot = [0.0, 0.0, 0.0]
    for ld in spec["loads"]:
        if ld["type"] == "force":
            q = Units.Quantity(ld["value"]).getValueAs("N").Value
            dv = FreeCAD.Vector(*_vec(ld["direction"])).normalize()
            for i in range(3):
                tot[i] += q * dv[i]
        elif ld["type"] == "pressure":
            p = Units.Quantity(ld["value"]).getValueAs("MPa").Value
            for o, s in ld["refs"]:
                face = doc.getObject(o).Shape.getElement(s)
                pts, tris = face.tessellate(0.02)
                for a, b, c in tris:
                    A, B, C = pts[a], pts[b], pts[c]
                    cr = (B - A).cross(C - A)
                    area = cr.Length / 2
                    cen = (A + B + C) * (1.0 / 3.0)
                    u, v = face.Surface.parameter(cen)
                    n = face.normalAt(u, v)
                    for i in range(3):
                        tot[i] += -p * n[i] * area
    return tot


def parse_dat(path):
    """Totals from the .dat file: sum of the reaction `total force` blocks, and the `total internal energy`."""
    rf = [0.0, 0.0, 0.0]
    energy = None
    rf_all = None
    if not os.path.exists(path):
        return rf, energy, rf_all
    lines = open(path).read().splitlines()
    for i, ln in enumerate(lines):
        if "total force (fx,fy,fz) for set NALL" in ln and i + 2 < len(lines):
            rf_all = [float(x) for x in lines[i + 2].split()[:3]]
        elif "total force (fx,fy,fz)" in ln and i + 2 < len(lines):
            vals = [float(x) for x in lines[i + 2].split()[:3]]
            rf = [a + b for a, b in zip(rf, vals)]
        elif "total internal energy" in ln and i + 2 < len(lines):
            energy = float(lines[i + 2].split()[0])
    return rf, energy, rf_all


def run_stock(doc, spec, h, workdir, edit=None):
    """Mesh and solve once at element size h. `edit` (a callable) is applied after the constraints exist and before meshing (groups I8 and J)."""
    from femmesh import gmshtools
    from femtools import ccxtools

    t0 = time.time()
    out = {"h": h, "error": None}
    an, sol, mesh = build_stock(doc, spec, h)
    if edit:
        edit(doc)
        doc.recompute()
    err = gmshtools.GmshTools(mesh).create_mesh()
    out["mesher_error"] = err
    out["nodes"] = mesh.FemMesh.NodeCount
    out["elements"] = mesh.FemMesh.VolumeCount
    if not out["elements"]:
        out["error"] = "mesher produced no volume elements"
        return out
    fea = ccxtools.FemToolsCcx(an, sol, test_mode=False)
    fea.update_objects()
    os.makedirs(workdir, exist_ok=True)
    fea.setup_working_dir(workdir)
    fea.setup_ccx()
    msg = fea.check_prerequisites()
    if msg:
        out["error"] = "check_prerequisites: " + msg.strip().replace("\n", " ")[:300]
        out["prereq_blocked"] = True
        return out
    fea.purge_results()
    fea.write_inp_file()
    inp = fea.inp_file_name
    txt = open(inp).read()
    # total strain energy: *EL PRINT with ELSE and TOTALS=ONLY over the whole element set (ccx 2.23)
    txt = txt.replace("*END STEP", "*EL PRINT, ELSET=Eall, TOTALS=ONLY\nELSE\n*NODE PRINT, NSET=Nall, TOTALS=ONLY\nRF\n*END STEP")
    open(inp, "w").write(txt)
    t1 = time.time()
    out["ccx_exit_code"] = fea.ccx_run()  # FemToolsCcx returns the exit code; a non-zero one is only printed, never raised
    out["solve_seconds"] = time.time() - t1
    if out["ccx_exit_code"]:
        out["ccx_tail"] = ((fea.ccx_stderr or "") + (fea.ccx_stdout or ""))[-300:]
    fea.load_results()
    if not fea.results_present:
        out["error"] = "no results read back"
        return out
    res = [o for o in doc.Objects if o.isDerivedFrom("Fem::FemResultObject")]
    if not res:
        out["error"] = "no result object"
        return out
    r = res[-1]
    out["result"] = r.Name
    if not len(r.DisplacementLengths):
        out["error"] = "result object holds no displacements (ccx exit code %s)" % out.get("ccx_exit_code")
        return out
    out["max_displacement"] = max(r.DisplacementLengths)
    out["max_von_mises"] = max(r.vonMises)
    rf, en, rf_all = parse_dat(os.path.join(workdir, os.path.basename(inp).replace(".inp", ".dat")))
    out["reaction_sum"], out["energy"], out["reaction_sum_nall"] = rf, en, rf_all
    try:
        out["applied_sum"] = applied_sum(doc, spec)
    except Exception as ex:  # noqa: BLE001
        out["applied_sum"] = [float("nan")] * 3
        out["applied_error"] = str(ex)[:120]
    out["total_seconds"] = time.time() - t0
    return out
