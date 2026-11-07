# SPDX-License-Identifier: LGPL-2.1-or-later
"""Builds the Analysis and its stock objects (solver, material, constraints, Gmsh mesh object) from a normalised spec, and resolves references.

References are stored as the `References` property of the stock constraint objects, so the element map (G01) rewrites them when the body is
edited. Lost references are read from those live properties, never from the spec's sub-element names."""

import FreeCAD
import ObjectsFem

from .spec import SUB_RE, Refusal, StudySpecError, parse_quantity


def part_name(an, role, k=None):
    return f"{an.Name}_{role}" + ("" if k is None else str(k))


def study_objects(an):
    """Every object the study created (members of the analysis group plus its construction direction lines)."""
    doc = an.Document
    objs = list(an.Group)
    objs += [o for o in doc.Objects if o.Name.startswith(an.Name + "_Dir")]
    return objs


def clear(doc, an):
    """Remove everything the study put into the analysis (not the analysis itself, not the body)."""
    for o in study_objects(an):
        try:
            doc.removeObject(o.Name)
        except Exception:  # noqa: BLE001  an object may already be gone with its parent
            pass


def make_analysis(doc, name):
    an = doc.getObject(name)
    if an is not None and getattr(an, "TypeId", "") == "Fem::FemAnalysis":
        clear(doc, an)
        return an
    if an is not None:
        raise StudySpecError(f"spec.name {name!r} is the name of an existing object that is not an Analysis")
    return ObjectsFem.makeAnalysis(doc, name)


def flat_refs(c):
    """The References of constraint object `c` as [[object name, sub-element], ...]."""
    out = []
    for obj, subs in c.References:
        for s in subs:
            out.append([obj.Name, s])
    return out


def _link_refs(doc, refs):
    out = []
    for o, s in refs:
        ob = doc.getObject(o)
        if ob is not None:
            out.append((ob, s))
    return out


def read_live(doc, an):
    """Live references per constraint: {("S", k): refs, ("L", k): refs} (only for objects that exist), and the list of problems found."""
    live, lost = {}, []
    for o in an.Group:
        n = o.Name
        for role, key in (("_Support", "S"), ("_Load", "L")):
            tag = an.Name + role
            if n.startswith(tag) and n[len(tag):].isdigit():
                k = int(n[len(tag):])
                refs = flat_refs(o) if hasattr(o, "References") else []
                live[(key, k)] = refs
                if "Invalid" in (o.State or []):
                    lost.append((key, k, refs[0] if refs else ["?", "?"], f"{o.Label} is Invalid"))
                for r in refs:
                    if r[1].startswith("?"):
                        lost.append((key, k, r, f"{o.Label} refers to {r[0]}.{r[1]}, an element that no longer exists"))
    return live, lost


def element(doc, objname, sub):
    """The Part shape of sub-element `sub` of object `objname`; raises LookupError when it does not resolve."""
    ob = doc.getObject(objname)
    if ob is None or not hasattr(ob, "Shape"):
        raise LookupError(f"object {objname!r} does not exist")
    if sub.startswith("?"):
        raise LookupError(f"{objname}.{sub} no longer exists")
    if not SUB_RE.match(sub):
        raise LookupError(f"{objname}.{sub!r} is not a Face, Edge or Vertex name")
    try:
        sh = ob.Shape.getElement(sub)
    except Exception as ex:  # noqa: BLE001
        raise LookupError(f"{objname}.{sub} does not resolve ({ex})") from None
    if sh is None or sh.isNull() or sh.ShapeType not in ("Face", "Edge", "Vertex"):
        raise LookupError(f"{objname}.{sub} does not resolve to a face, edge or vertex")
    return sh


def _direction_line(doc, an, k, direction):
    import Part

    d = FreeCAD.Vector(*direction)
    d.normalize()
    ref = doc.addObject("Part::Feature", part_name(an, "Dir", k))
    ref.Shape = Part.makeLine(FreeCAD.Vector(0, 0, 0), d * 10)
    ref.Label = ref.Name
    if hasattr(ref, "ViewObject") and ref.ViewObject:
        ref.ViewObject.Visibility = False
    doc.recompute()
    return ref


def _material_object(doc, an, spec, mat):
    mo = ObjectsFem.makeMaterialSolid(doc, part_name(an, "Material"))
    d = dict(mo.Material)
    d["Name"] = "StudyMaterial"
    m = spec["material"]
    if mat is not None:
        d["YoungsModulus"] = f"{mat['E']:.15g} MPa"
        d["PoissonRatio"] = str(m["nu"])
        if mat.get("density") is not None:
            d["Density"] = f"{mat['density']:.15g} kg/m^3"
    elif isinstance(m, dict):  # define_static before validation: as written, the run rebuilds this with checked values
        d["YoungsModulus"] = str(m.get("E", "0 MPa"))
        d["PoissonRatio"] = str(m.get("nu", 0.3))
    mo.Material = d
    an.addObject(mo)
    return mo


def build(doc, an, spec, live=None, mat=None, loads=None):
    """Create the stock objects in `an`. `live` holds references read from a previous generation of the objects (None at define time);
    `mat` the checked material values in base units, `loads` the checked force or pressure values in N or MPa (None at define time)."""
    live = live or {}
    body = doc.getObject(spec["body"])
    sol = ObjectsFem.makeSolverCalculiXCcxTools(doc, part_name(an, "Solver"))
    sol.AnalysisType = "static"
    an.addObject(sol)
    mo = _material_object(doc, an, spec, mat)
    sup, lds = [], []
    for k, sp in enumerate(spec["supports"]):
        refs = live.get(("S", k)) or sp["refs"]
        if sp["fixed"] == "xyz":
            c = ObjectsFem.makeConstraintFixed(doc, part_name(an, "Support", k))
        else:
            c = ObjectsFem.makeConstraintDisplacement(doc, part_name(an, "Support", k))
            for ax in "xyz":
                if ax in sp["fixed"]:
                    setattr(c, ax + "Free", False)
                    setattr(c, ax + "Displacement", 0.0)
        c.References = _link_refs(doc, refs)
        an.addObject(c)
        sup.append(c)
    for k, ld in enumerate(spec["loads"]):
        name = part_name(an, "Load", k)
        if ld["type"] == "gravity":
            c = ObjectsFem.makeConstraintSelfWeight(doc, name)
            d = FreeCAD.Vector(*ld["direction"])
            d.normalize()
            c.GravityDirection = d
            if loads is not None:
                c.GravityAcceleration = f"{loads[k]:.15g} mm/s^2"
            an.addObject(c)
            lds.append(c)
            continue
        refs = live.get(("L", k)) or ld["refs"]
        if ld["type"] == "force":
            c = ObjectsFem.makeConstraintForce(doc, name)
            c.References = _link_refs(doc, refs)
            if loads is not None:
                c.Force = f"{loads[k]:.15g} N"
            line = _direction_line(doc, an, k, ld["direction"])
            c.Direction = (line, ["Edge1"])
            c.Reversed = False
        else:
            c = ObjectsFem.makeConstraintPressure(doc, name)
            c.References = _link_refs(doc, refs)
            if loads is not None:
                c.Pressure = f"{loads[k]:.15g} MPa"
        an.addObject(c)
        lds.append(c)
    mesh = ObjectsFem.makeMeshGmsh(doc, part_name(an, "Mesh"))
    if body is not None:
        mesh.Shape = body
    mesh.ElementOrder = "2nd"
    mesh.ElementDimension = "3D"
    mesh.MeshSizeFromCurvature = 0  # the stock default of 12 per circle would override h on a round section: every level the same mesh
    mesh.ParallelProcessing = False  # multi-threaded Gmsh gives a different mesh in every run
    mesh.HighOrderOptimize = "Optimization"  # repairs second-order tetrahedra with a non-positive Jacobian on curved boundaries (ccx exit 201)
    an.addObject(mesh)
    doc.recompute()
    return {"solver": sol, "material": mo, "supports": sup, "loads": lds, "mesh": mesh}


def checked_loads(spec):
    """Load values in base units (N, MPa or mm/s^2) per load index. Raises Refusal."""
    out = []
    for i, ld in enumerate(spec["loads"]):
        dim = {"force": "force", "pressure": "pressure", "gravity": "acceleration"}[ld["type"]]
        out.append(parse_quantity(ld["value"], dim, f"loads[{i}] ({ld['type']}) value"))
    return out
