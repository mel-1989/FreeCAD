# SPDX-License-Identifier: LGPL-2.1-or-later
"""Corpus for the G11 static study (slice 1a, run 067): 78 cases, groups A to J, with closed-form truths.

Units are mm, N, MPa, tonne/mm^3 (the CalculiX unit system FreeCAD writes). Every truth below is a closed form written out
here; the textbook is named beside each (names only, no table or worked example is copied):

  A  axial bar:                 delta = P L / (A E), sigma = P / A                   (any strength-of-materials text)
  B  Timoshenko cantilever:     delta = P L^3 / (3 E I) + P L / (kappa G A)          (Timoshenko, Strength of Materials; Roark)
                                kappa = 5/6 (rectangle), 6(1+nu)/(7+6nu) (circle)    (Cowper 1966)
  C  cantilever, uniform load:  delta = w L^4 / (8 E I) + w L^2 / (2 kappa G A)      (Timoshenko; Roark)
  D  beam on two supports:      P L^3/(48 E I) + P L/(4 kappa G A);  5 w L^4/(384 E I) + w L^2/(8 kappa G A)   (Timoshenko; Roark)
  E  thick cylinder (Lame):     s_theta = p a^2/(b^2-a^2) (1 + b^2/r^2), s_r = p a^2/(b^2-a^2) (1 - b^2/r^2)   (Timoshenko, Theory of Elasticity)
  F  simply supported plate:    Navier double series                                  (Timoshenko and Woinowsky-Krieger, Theory of Plates and Shells)

This module imports only `math` and `random` at the top; geometry builders import FreeCAD when called, so the oracle and the
listing work in a plain Python. Corpus truth does not import or call any study code. `python3 -I study_corpus.py` prints the table.

A case is a `Case`. `case.build(doc)` creates its geometry in `doc` and returns a dict with `spec` (a SPEC.md spec dict), and where needed
`variants` (group G: four specs of one physical problem), `edit` (group I8 and J: a callable `edit(doc)` applied after `define_static` and
before `run_static`) and `env` (group I9: a run-time environment change). `case.truth` holds closed forms; `case.expect` the expected
verdict and code for groups H, I and J.
"""

import math
import random
from dataclasses import dataclass, field

SEED = 11067
ELEMENTS_PER_H3 = 5.1  # C3D10 elements per (volume / h^3), measured on a 100x10x10 bar at h = 2 mm (6429 elements); refined by BASELINE.md

# name: (E in MPa, nu, density kg/m^3, yield MPa)
MATS = {"steel": (210000.0, 0.30, 7850.0, 250.0), "aluminium": (70000.0, 0.33, 2700.0, 270.0)}


def mat(name, nu=None, with_yield=True):
    E, n, rho, sy = MATS[name]
    m = {"E": f"{E / 1000:g} GPa", "nu": n if nu is None else nu, "density": f"{rho:g} kg/m^3"}
    if with_yield:
        m["yield"] = f"{sy:g} MPa"
    return m


def Gmod(E, nu):
    return E / (2 * (1 + nu))


# ---------------------------------------------------------------------------------------------------- closed forms
def kappa_rect():
    return 5.0 / 6.0


def kappa_circle(nu):  # Cowper 1966, solid circle
    return 6 * (1 + nu) / (7 + 6 * nu)


def cant_tip(P, L, E, nu, I, A, kappa):  # Timoshenko cantilever, end load
    return P * L**3 / (3 * E * I) + P * L / (kappa * Gmod(E, nu) * A)


def cant_uniform(w, L, E, nu, I, A, kappa):  # Timoshenko cantilever, load per length w
    return w * L**4 / (8 * E * I) + w * L**2 / (2 * kappa * Gmod(E, nu) * A)


def ss_central(P, L, E, nu, I, A, kappa):  # two supports, central load
    return P * L**3 / (48 * E * I) + P * L / (4 * kappa * Gmod(E, nu) * A)


def ss_uniform(w, L, E, nu, I, A, kappa):  # two supports, uniform load per length w
    return 5 * w * L**4 / (384 * E * I) + w * L**2 / (8 * kappa * Gmod(E, nu) * A)


def lame_hoop(p, a, b, r):
    return p * a * a / (b * b - a * a) * (1 + b * b / (r * r))


def lame_radial(p, a, b, r):
    return p * a * a / (b * b - a * a) * (1 - b * b / (r * r))


def navier_w(x, y, a, b, q, D, nmax=99):
    """Kirchhoff plate, simply supported, uniform q: w = 16 q/(pi^6 D) sum_{m,n odd} sin(m pi x/a) sin(n pi y/b) / (m n (m^2/a^2 + n^2/b^2)^2).
    x, y measured from a corner; m and n run over the odd integers 1..nmax (50 terms each, 2500 terms)."""
    s = 0.0
    for m in range(1, nmax + 1, 2):
        for n in range(1, nmax + 1, 2):
            s += math.sin(m * math.pi * x / a) * math.sin(n * math.pi * y / b) / (m * n * (m * m / (a * a) + n * n / (b * b)) ** 2)
    return 16 * q / (math.pi**6 * D) * s


def rounded_rect_I(width_load, other, r):
    """Second moment about the neutral axis of a rectangle with its four corners rounded (radius r) for bending in the
    `width_load` direction (the extent along the load is width_load, the other extent `other`), by direct integration:
    I = integral of x^2 h(x) dx, h(x) the chord height at x. Independent of any CAD kernel."""
    hw, ho = width_load / 2.0, other / 2.0
    n = 20000
    tot = 0.0
    for i in range(n):
        x = -hw + (i + 0.5) * width_load / n
        ax = abs(x)
        h = other if ax <= hw - r else other - 2 * (r - math.sqrt(max(r * r - (ax - (hw - r)) ** 2, 0.0)))
        tot += x * x * h
    return tot * width_load / n


def rounded_rect_A(b, d, r):
    return b * d - (4 - math.pi) * r * r


# ---------------------------------------------------------------------------------------------------- case record
@dataclass
class Case:
    id: str
    group: str
    title: str
    h0: float  # coarsest element size, mm; the study runs h0, h0/2, h0/4
    build: object = None
    truth: dict = field(default_factory=dict)
    expect: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)
    volume: float = 0.0  # mm^3 of the (modelled) solid, for the element estimate
    timeout_s: int = 300
    variants_n: int = 1  # number of study runs the oracle makes for this case

    @property
    def h_fine(self):
        return self.h0 / 4.0

    @property
    def est_elements_fine(self):
        return int(ELEMENTS_PER_H3 * self.volume / self.h_fine**3) if self.volume else 0


# ---------------------------------------------------------------------------------------------------- geometry helpers
def _fc():
    import FreeCAD
    import Part

    return FreeCAD, Part, FreeCAD.Vector


def _feat(doc, shape, name="Body"):
    o = doc.addObject("Part::Feature", name)
    o.Shape = shape
    doc.recompute()
    return o


def _ext(bb, axis):
    return (bb.XLength, bb.YLength, bb.ZLength)[axis]


def _lo(bb, axis):
    return (bb.XMin, bb.YMin, bb.ZMin)[axis]


def planes(obj, axis, val, tol=1e-6):
    """All faces of `obj` that lie in the plane `axis` (0 x, 1 y, 2 z) = val, as [name, 'FaceN'] pairs."""
    out = []
    for i, f in enumerate(obj.Shape.Faces, 1):
        bb = f.BoundBox
        if _ext(bb, axis) < tol and abs(_lo(bb, axis) - val) < tol and type(f.Surface).__name__ == "Plane":
            out.append([obj.Name, f"Face{i}"])
    if not out:
        raise LookupError(f"no face in plane {'xyz'[axis]}={val}")
    return out


def edges_along(obj, axis, at, tol=1e-6):
    """All straight edges parallel to `axis` whose other two coordinates equal `at` = {other_axis: value}."""
    out = []
    for i, e in enumerate(obj.Shape.Edges, 1):
        bb = e.BoundBox
        if _ext(bb, axis) < tol:
            continue
        if all(_ext(bb, o) < tol and abs(_lo(bb, o) - v) < tol for o, v in at.items()):
            out.append([obj.Name, f"Edge{i}"])
    if not out:
        raise LookupError(f"no edge along {'xyz'[axis]} at {at}")
    return out


def vertex_at(obj, pt, tol=1e-6):
    for i, v in enumerate(obj.Shape.Vertexes, 1):
        p = v.Point
        if abs(p.x - pt[0]) < tol and abs(p.y - pt[1]) < tol and abs(p.z - pt[2]) < tol:
            return [[obj.Name, f"Vertex{i}"]]
    raise LookupError(f"no vertex at {pt}")


def cyl_faces(obj, radius, tol=1e-6):
    out = []
    for i, f in enumerate(obj.Shape.Faces, 1):
        s = f.Surface
        if type(s).__name__ == "Cylinder" and abs(s.Radius - radius) < tol:
            out.append([obj.Name, f"Face{i}"])
    if not out:
        raise LookupError(f"no cylinder face of radius {radius}")
    return out


def box_split(doc, dims, axis, at, origin=(0, 0, 0), name="Body"):
    """A box fused from boxes split at the planes axis = at (one value or a list), so edges and split faces exist there."""
    FreeCAD, Part, V = _fc()
    cuts = sorted([at] if isinstance(at, (int, float)) else list(at))
    o = list(origin)
    edges = [o[axis]] + cuts + [o[axis] + dims[axis]]
    shape = None
    for lo, hi in zip(edges[:-1], edges[1:]):
        d = list(dims)
        d[axis] = hi - lo
        oo = list(o)
        oo[axis] = lo
        piece = Part.makeBox(*d, V(*oo))
        shape = piece if shape is None else shape.fuse(piece)
    if len(shape.Solids) != 1:
        raise RuntimeError("split box did not fuse to one solid")
    return _feat(doc, shape, name)


def sup(refs, fixed="xyz"):
    return {"refs": refs, "fixed": fixed}


def frc(refs, value, direction):
    return {"type": "force", "value": value, "refs": refs, "direction": list(direction)}


def prs(refs, value):
    return {"type": "pressure", "value": value, "refs": refs}


def mkspec(body, material, supports, loads, h0, **opts):
    o = {"h0": f"{h0:g} mm"}
    o.update(opts)
    return {"body": body, "material": material, "supports": supports, "loads": loads, "options": o}


def _rng():
    return random.Random(SEED)


# ---------------------------------------------------------------------------------------------------- group A: axial bars
def _bar_geometry(doc, section, dims, L):
    FreeCAD, Part, V = _fc()
    if section == "circle":
        d = dims[0]
        return _feat(doc, Part.makeCylinder(d / 2, L, V(0, 0, 0), V(1, 0, 0))), math.pi * d * d / 4, math.pi * d * d / 4 * L
    b, d = dims
    return _feat(doc, Part.makeBox(L, b, d, V(0, -b / 2, -d / 2))), b * d, b * d * L


def _build_A(section, dims, L, matname, sigma, h0):
    E, nu, _, _ = MATS[matname]
    A = math.pi * dims[0] ** 2 / 4 if section == "circle" else dims[0] * dims[1]
    P = round(sigma * A, 0)

    def build(doc):
        body, A_, _ = _bar_geometry(doc, section, dims, L)
        return {
            "spec": mkspec(
                body.Name, mat(matname, nu=0), [sup(planes(body, 0, 0))], [frc(planes(body, 0, L), f"{P:g} N", (1, 0, 0))], h0
            )
        }

    # The element family is exact on flat-sided sections only. A meshed circle is not a circle, and the loaded end carries a local
    # disturbance of about 0.5% whatever the load distribution (SPEC.md, "Checks that may not hold", item 1), so round bars get a
    # looser bar. This was set in run 067 after the study code existed, and the run note says so.
    round_section = section == "circle"
    truth = {
        "disp": P * L / (A * E),
        "tol_disp": 1e-3 if round_section else 1e-5,
        "max_vm": P / A,
        "tol_vm": 1e-2 if round_section else 1e-5,
        "energy": 0.5 * P * (P * L / (A * E)),
    }
    return build, truth, A * L, {"section": section, "dims": dims, "L": L, "material": matname, "P_N": P}


def _cases_A(rng):
    out = []
    specs = [("square", (10, 10)), ("square", (12, 12)), ("rect", (20, 10)), ("rect", (16, 8)), ("rect", (24, 12)),
             ("circle", (12,)), ("circle", (16,)), ("square", (10, 10))]
    for i, (sec, dims) in enumerate(specs, 1):
        matname = ["steel", "aluminium"][(i + rng.randint(0, 1)) % 2]
        L = rng.choice([60.0, 100.0, 150.0])
        sigma = rng.choice([40.0, 50.0, 60.0])
        sec_ = "circle" if sec == "circle" else "rect"
        m = min(dims)
        h0 = (0.5 if sec == "circle" else 1.5) * m  # round: h0 = d/2. At d (or 4/3 d) Gmsh finds no volume or a coarse level 25 times off (measured 147 to 244 elements for d = 12)
        build, truth, vol, par = _build_A(sec_, dims, L, matname, sigma, h0)
        c = Case(f"A{i:02d}", "A", f"axial bar {sec} {'x'.join(f'{x:g}' for x in dims)} L={L:g} {matname}", h0, build, truth, {}, par, vol)
        out.append(c)
    return out


# ---------------------------------------------------------------------------------------------------- group B and C: cantilevers
SEC_RECT = (12.0, 20.0)  # b (y extent), d (z extent), centred on the x axis
SEC_CIRC = 14.0


def _section(kind, axis):
    """Properties of the section for a load along `axis` ('y' or 'z'): h (extent along the load), c, I, A, kappa-function, width."""
    if kind == "circle":
        d = SEC_CIRC
        return {"h": d, "c": d / 2, "I": math.pi * d**4 / 64, "A": math.pi * d * d / 4, "kappa": "circle", "width": d}
    b, d = SEC_RECT
    if axis == "z":
        return {"h": d, "c": d / 2, "I": b * d**3 / 12, "A": b * d, "kappa": "rect", "width": b}
    return {"h": b, "c": b / 2, "I": d * b**3 / 12, "A": b * d, "kappa": "rect", "width": d}


def _kap(sec, nu):
    return kappa_circle(nu) if sec["kappa"] == "circle" else kappa_rect()


def _beam_body(doc, kind, L):
    FreeCAD, Part, V = _fc()
    if kind == "circle":
        return _feat(doc, Part.makeCylinder(SEC_CIRC / 2, L, V(0, 0, 0), V(1, 0, 0)))
    b, d = SEC_RECT
    return _feat(doc, Part.makeBox(L, b, d, V(0, -b / 2, -d / 2)))


def _bend_probe(axis, c, L, xmid):
    return {"kind": "bend", "load_axis": axis, "c": c, "x": xmid}


def _cases_B(rng):
    out = []
    i = 0
    for kind in ("rect", "circle"):
        for ratio in (5, 10, 20):
            for axis in ("y", "z"):
                i += 1
                matname = rng.choice(["steel", "aluminium"])
                E, nu, _, _ = MATS[matname]
                s = _section(kind, axis)
                L = ratio * s["h"]
                kap = _kap(s, nu)
                delta_t = L / 1000.0  # design tip deflection, keeps stress under yield and displacement under 10% of thickness
                P = float(f"{delta_t * 3 * E * s['I'] / L**3:.3g}")
                delta = cant_tip(P, L, E, nu, s["I"], s["A"], kap)
                sig = P * (L - L / 2) * s["c"] / s["I"]  # bending stress at mid-length (Euler-Bernoulli moment, extreme fibre)
                m = min(SEC_RECT) if kind == "rect" else SEC_CIRC
                h0 = 4 * m / 3.0 if kind == "rect" else m / 2.0  # round: h0 = d/2, see group A
                direction = (0, -1, 0) if axis == "y" else (0, 0, -1)

                def build(doc, kind=kind, L=L, P=P, matname=matname, direction=direction, h0=h0):
                    body = _beam_body(doc, kind, L)
                    return {"spec": mkspec(body.Name, mat(matname), [sup(planes(body, 0, 0))], [frc(planes(body, 0, L), f"{P:g} N", direction)], h0)}

                truth = {"disp": delta, "tol_disp": 0.03,
                         "sigma": [{"name": "bending at mid-length", "probe": _bend_probe(axis, s["c"], L, L / 2), "value": sig, "tol": 0.04}]}
                vol = (s["A"]) * L
                par = {"section": kind, "load_axis": axis, "L_over_h": ratio, "L": L, "P_N": P, "material": matname}
                out.append(Case(f"B{i:02d}", "B", f"cantilever {kind} L/h={ratio} load {axis} {matname}", h0, build, truth, {}, par, vol))
    return out


def _cases_C(rng):
    out = []
    i = 0
    for ratio in (5, 10, 20):
        for axis in ("y", "z"):
            i += 1
            matname = rng.choice(["steel", "aluminium"])
            E, nu, _, _ = MATS[matname]
            s = _section("rect", axis)
            L = ratio * s["h"]
            kap = kappa_rect()
            w = float(f"{(L / 1000.0) * 8 * E * s['I'] / L**4:.3g}")  # load per length, N/mm, for a design deflection L/1000 (bending part)
            p = w / s["width"]
            delta = cant_uniform(w, L, E, nu, s["I"], s["A"], kap)
            sig = w * (L - L / 2) ** 2 / 2 * s["c"] / s["I"]  # M(x) = w (L-x)^2 / 2 at x = L/2
            h0 = 4 * min(SEC_RECT) / 3.0
            loaded = 2 if axis == "z" else 1  # the face the pressure acts on: z = +d/2 or y = +b/2
            coord = SEC_RECT[1] / 2 if axis == "z" else SEC_RECT[0] / 2

            def build(doc, L=L, p=p, matname=matname, loaded=loaded, coord=coord, h0=h0):
                body = _beam_body(doc, "rect", L)
                return {"spec": mkspec(body.Name, mat(matname), [sup(planes(body, 0, 0))], [prs(planes(body, loaded, coord), f"{p:.6g} MPa")], h0)}

            truth = {"disp": delta, "tol_disp": 0.03,
                     "sigma": [{"name": "bending at mid-length", "probe": _bend_probe(axis, s["c"], L, L / 2), "value": sig, "tol": 0.04}]}
            par = {"section": "rect", "load_axis": axis, "L_over_h": ratio, "L": L, "p_MPa": p, "w_N_per_mm": w, "material": matname}
            out.append(Case(f"C{i:02d}", "C", f"cantilever uniform pressure L/h={ratio} load {axis} {matname}", h0, build, truth, {}, par, s["A"] * L))
    return out


# ---------------------------------------------------------------------------------------------------- group D: beams on two supports
def _cases_D(rng):
    out = []
    i = 0
    for kind in ("central", "uniform"):
        for ratio in (10, 20):
            for matname in ("steel", "aluminium"):
                i += 1
                E, nu, _, _ = MATS[matname]
                b, d = SEC_RECT  # load along z
                I, A, c = b * d**3 / 12, b * d, d / 2
                L = ratio * d
                kap = kappa_rect()
                if kind == "central":
                    P = float(f"{(L / 1000.0) * 48 * E * I / L**3:.3g}")
                    delta = ss_central(P, L, E, nu, I, A, kap)
                    sig = P * L / 4 * c / I
                    par = {"P_N": P}
                else:
                    w = float(f"{(L / 1000.0) * 384 * E * I / (5 * L**4):.3g}")
                    delta = ss_uniform(w, L, E, nu, I, A, kap)
                    sig = w * L * L / 8 * c / I
                    par = {"w_N_per_mm": w, "p_MPa": w / b}
                h0 = 4 * min(b, d) / 3.0

                def build(doc, kind=kind, L=L, matname=matname, h0=h0, par=par):
                    # Quarter model on two symmetry planes (x = L/2 and y = 0); the support is the whole end face x = 0 held in z only, so no
                    # line or point support (those make the stress singular and the verdict stress_singular, measured in run 067).
                    # A "central load" is a pressure on a 10 mm strip centred on mid-span (a line load is singular too).
                    cuts = [L / 2 - 5.0] if kind == "central" else None
                    if cuts:
                        body = box_split(doc, (L / 2, b / 2, d), 0, cuts, origin=(0, 0, -d / 2))
                    else:
                        body = _feat(doc, _fc()[1].makeBox(L / 2, b / 2, d, _fc()[2](0, 0, -d / 2)))
                    supports = [sup(planes(body, 0, 0.0), "z"), sup(planes(body, 0, L / 2), "x"), sup(planes(body, 1, 0.0), "y")]
                    if kind == "central":
                        strip = [f for f in planes(body, 2, d / 2) if abs(body.Shape.getElement(f[1]).CenterOfMass.x - (L / 2 - 2.5)) < 1e-6]
                        loads = [prs(strip, f"{par['P_N'] / (10.0 * b):.6g} MPa")]
                    else:
                        loads = [prs(planes(body, 2, d / 2), f"{par['p_MPa']:.6g} MPa")]
                    return {"spec": mkspec(body.Name, mat(matname), supports, loads, h0)}

                truth = {"disp": delta, "tol_disp": 0.03,
                         "sigma": [{"name": "bending at mid-span", "probe": {"kind": "bend", "load_axis": "z", "c": c, "x": L / 2, "support": True, "side": -1}, "value": sig, "tol": 0.04}]}
                par.update({"kind": kind, "L_over_h": ratio, "L": L, "material": matname})
                out.append(Case(f"D{i:02d}", "D", f"beam on two supports {kind} L/h={ratio} {matname}", h0, build, truth, {}, par, A * L / 4))
    return out


# ---------------------------------------------------------------------------------------------------- group E: thick cylinders
def _cases_E(rng):
    out = []
    i = 0
    for ba in (1.5, 2.0, 3.0):
        for matname in ("steel", "aluminium"):
            i += 1
            E, nu, _, sy = MATS[matname]
            a = 10.0
            b = a * ba
            H2 = 2.5 * b  # half length; the symmetry plane z = 0 is the mid-length
            # inner-wall von Mises = sqrt(s_t^2 + p^2 - s_t (-p)) with s_t = hoop; keep it near 0.4 yield
            sig_t_over_p = (b * b + a * a) / (b * b - a * a)
            vm_over_p = math.sqrt(sig_t_over_p**2 + 1 + sig_t_over_p)
            p = float(f"{0.4 * sy / vm_over_p:.2g}")
            # b/a >= 2: h0 = a = 10 mm. At 4/3 (b - a) or (b - a) the coarsest level puts 14 to 18 quadratic faces on the quarter circle and the load on the constrained
            # edge nodes (recomputed from the deck) is off by 0.8e-3 to 2.1e-3 (E05, E06 fail the 1e-3 equilibrium bar); at h0 = a it is 7e-5 to 1.2e-4, and the energy of E03 and E04
            # no longer sits flat over two coarse levels before a 1.7% jump. b/a = 1.5 keeps 4/3 (b - a): its coarsest level passes (1.2e-4).
            h0 = 4 * (b - a) / 3.0 if ba < 2.0 else a

            def build(doc, a=a, b=b, H2=H2, p=p, matname=matname, h0=h0):
                FreeCAD, Part, V = _fc()
                ring = Part.makeCylinder(b, H2).cut(Part.makeCylinder(a, H2))
                quarter = ring.common(Part.makeBox(b, b, H2))
                body = _feat(doc, quarter)
                supports = [sup(planes(body, 0, 0.0), "x"), sup(planes(body, 1, 0.0), "y"), sup(planes(body, 2, 0.0), "z")]
                return {"spec": mkspec(body.Name, mat(matname), supports, [prs(cyl_faces(body, a), f"{p:g} MPa")], h0)}

            truth = {"sigma": [
                {"name": "hoop at inner radius", "probe": {"kind": "hoop", "r": a, "z": 0.0}, "value": lame_hoop(p, a, b, a), "tol": 0.03},
                {"name": "hoop at outer radius", "probe": {"kind": "hoop", "r": b, "z": 0.0}, "value": lame_hoop(p, a, b, b), "tol": 0.03}],
                "primary": "hoop at inner radius"}
            par = {"a": a, "b": b, "b_over_a": ba, "half_length": H2, "p_MPa": p, "material": matname,
                   "radial_at_inner": lame_radial(p, a, b, a)}
            vol = math.pi / 4 * (b * b - a * a) * H2
            out.append(Case(f"E{i:02d}", "E", f"thick cylinder b/a={ba:g} {matname}", h0, build, truth, {}, par, vol))
    return out


# ---------------------------------------------------------------------------------------------------- group F: plates
def _cases_F(rng):
    out = []
    i = 0
    for ar in (1.0, 1.5, 2.0):
        for ha in (0.05, 0.10):
            i += 1
            matname = rng.choice(["steel", "aluminium"])
            E, nu, _, _ = MATS[matname]
            a = 100.0
            b = a * ar
            t = a * ha
            q = 0.05 if ha == 0.05 else 0.4  # MPa, deflection stays far under 10% of thickness
            D = E * t**3 / (12 * (1 - nu * nu))
            w0 = navier_w(a / 2, b / 2, a, b, q, D)
            h0 = 4 * t / 3.0

            def build(doc, a=a, b=b, t=t, q=q, matname=matname, h0=h0):
                # Quarter plate [0,a/2] x [0,b/2] x [0,t]. Symmetry planes x = 0 and y = 0. The two simply supported sides x = a/2 and y = b/2 are a
                # hard simple support: the whole side face holds z and the in-plane direction along that side (x = a/2 holds y and z; y = b/2 holds
                # x and z), the condition Navier's solution assumes (a soft support, out of plane only, adds a boundary layer linear in h/a).
                FreeCAD, Part, V = _fc()
                body = _feat(doc, Part.makeBox(a / 2, b / 2, t))
                supports = [sup(planes(body, 0, 0.0), "x"), sup(planes(body, 1, 0.0), "y"),
                            sup(planes(body, 0, a / 2), "yz"), sup(planes(body, 1, b / 2), "xz")]
                return {"spec": mkspec(body.Name, mat(matname), supports, [prs(planes(body, 2, t), f"{q:g} MPa")], h0)}

            truth = {"disp": w0, "tol_disp": 0.05, "note": "Kirchhoff (no shear deformation); centre deflection"}
            par = {"a": a, "b": b, "h_over_a": ha, "t": t, "q_MPa": q, "material": matname, "D": D}
            out.append(Case(f"F{i:02d}", "F", f"plate a={a:g} b/a={ar:g} h/a={ha:g} {matname}", h0, build, truth, {}, par, a / 2 * b / 2 * t))
    return out


# ---------------------------------------------------------------------------------------------------- group G: unit equivalence sets
PSI_MPA = 0.006894757293168  # 1 psi in MPa (exact: 6894.757293168 Pa)
LBF_N = 4.4482216152605  # 1 lbf in N (exact)


def _e_spellings(E_mpa):
    return [f"{E_mpa / 1000:.12g} GPa", f"{E_mpa:.12g} MPa", f"{E_mpa * 1e6:.12g} Pa", f"{E_mpa / PSI_MPA:.12g} psi"]


def _p_spellings(P_n):
    # the brief's third spelling is kip; FreeCAD's unit parser has no `kip` (syntax error, run 067), so millinewtons stand in
    return [f"{P_n:.12g} N", f"{P_n / 1000:.12g} kN", f"{P_n * 1000:.12g} mN", f"{P_n / LBF_N:.12g} lbf"]


def _cases_G(rng):
    out = []
    probs = [("bar", "square"), ("bar", "rect"), ("bar", "circle"), ("cant", "rect"), ("cant", "circle"), ("cant", "square")]
    for i, (kind, sec) in enumerate(probs, 1):
        matname = rng.choice(["steel", "aluminium"])
        E, nu, _, _ = MATS[matname]
        P = float(rng.choice([1000, 2000, 3000]))
        L = 100.0
        dims = {"square": (10.0, 10.0), "rect": (20.0, 10.0), "circle": (12.0,)}[sec]
        m = min(dims)
        h0 = (0.5 if sec == "circle" else 2.0) * m  # round: h0 = d/2, see group A
        if kind == "cant":
            P /= 50.0  # 20 to 60 N: a cantilever of this section at 1 to 3 kN deflects by 30% to 100% of its depth, which the study rightly calls suspect

        def build(doc, kind=kind, sec=sec, dims=dims, P=P, L=L, matname=matname, h0=h0, E=E):
            FreeCAD, Part, V = _fc()
            body, _, _ = _bar_geometry(doc, "circle" if sec == "circle" else "rect", dims, L)
            direction = (1, 0, 0) if kind == "bar" else (0, 0, -1)
            variants = []
            for es, ps in zip(_e_spellings(E), _p_spellings(P)):
                mm = mat(matname)
                mm["E"] = es
                variants.append(mkspec(body.Name, mm, [sup(planes(body, 0, 0))], [frc(planes(body, 0, L), ps, direction)], h0))
            return {"spec": variants[0], "variants": variants}

        A = math.pi * dims[0] ** 2 / 4 if sec == "circle" else dims[0] * dims[1]
        truth = {"agree_rel": 1e-6, "quantities": ["max_displacement", "energy", "max_von_mises"]}
        if kind == "bar":
            truth["info_disp"] = P * L / (A * E)
        par = {"kind": kind, "section": sec, "dims": dims, "P_N": P, "material": matname, "spellings_E": _e_spellings(E), "spellings_P": _p_spellings(P)}
        c = Case(f"G{i:02d}", "G", f"unit set {kind} {sec} {matname}", h0, build, truth, {}, par, A * L)
        c.variants_n = 4
        out.append(c)
    return out


# ---------------------------------------------------------------------------------------------------- group H: known singularities
H_EXPECT = {"verdict": "stress_singular", "code": "stress_singular", "displacement": "converged"}


def _cases_H(rng):
    out = []
    MS = mat("steel", with_yield=False)  # no yield value: a large local stress must not turn the verdict into `suspect`

    def b1(doc):  # H01: point force on a vertex of a slender cantilever tip (L/h = 20), 50 N: tip deflection 7.6% of the depth (100 N gave 15%, which the size rule rightly flags)
        FreeCAD, Part, V = _fc()
        body = _feat(doc, Part.makeBox(200, 10, 10))
        return {"spec": mkspec(body.Name, MS, [sup(planes(body, 0, 0))], [frc(vertex_at(body, (200, 10, 10)), "50 N", (0, 0, -1))], 8.0)}

    def b2(doc):  # H02: 4 mm plate in tension with a 12 mm square notch (two re-entrant 90 degree corners)
        FreeCAD, Part, V = _fc()
        plate = Part.makeBox(60, 30, 4)
        notch = Part.makeBox(12, 12, 4, V(24, 18, 0))
        body = _feat(doc, plate.cut(notch))
        return {"spec": mkspec(body.Name, MS, [sup(planes(body, 0, 0))], [frc(planes(body, 0, 60), "1500 N", (1, 0, 0))], 6.0)}

    def b3(doc):  # H03: 2 mm tab held along the two edges of its end face (line supports), loaded across the far end
        FreeCAD, Part, V = _fc()
        body = _feat(doc, Part.makeBox(40, 20, 2))
        held = edges_along(body, 1, {0: 0.0, 2: 0.0}) + edges_along(body, 1, {0: 0.0, 2: 2.0})
        return {"spec": mkspec(body.Name, MS, [sup(held, "xyz")], [frc(planes(body, 0, 40), "20 N", (0, 0, -1))], 4.0)}

    def b4(doc):  # H04: 200 N line load across the middle of a 100 x 10 x 10 beam on two supports (stress under a line load goes as 1/r)
        FreeCAD, Part, V = _fc()
        body = box_split(doc, (100, 10, 10), 0, 50.0, origin=(0, -5, -5))
        left = edges_along(body, 1, {0: 0.0, 2: -5.0})
        right = edges_along(body, 1, {0: 100.0, 2: -5.0})
        mid = edges_along(body, 1, {0: 50.0, 2: 5.0})
        return {"spec": mkspec(body.Name, MS, [sup(left, "xyz"), sup(right, "z")], [frc(mid, "200 N", (0, 0, -1))], 8.0)}

    def b5(doc):  # H05: L bracket, sharp internal corner, end load
        FreeCAD, Part, V = _fc()
        prof = Part.makePolygon([V(0, 0, 0), V(60, 0, 0), V(60, 20, 0), V(20, 20, 0), V(20, 60, 0), V(0, 60, 0), V(0, 0, 0)])
        body = _feat(doc, Part.Face(prof).extrude(V(0, 0, 10)))
        return {"spec": mkspec(body.Name, MS, [sup(planes(body, 1, 0.0))], [frc(planes(body, 1, 60.0), "500 N", (1, 0, 0))], 8.0)}

    def b6(doc):  # H06: stepped 4 mm plate in tension, neck 12 mm wide behind a flat shoulder: two sharp internal corners
        FreeCAD, Part, V = _fc()
        pts = [(0, 0), (40, 0), (40, 9), (80, 9), (80, 21), (40, 21), (40, 30), (0, 30), (0, 0)]
        prof = Part.makePolygon([V(x, y, 0) for x, y in pts])
        body = _feat(doc, Part.Face(prof).extrude(V(0, 0, 4)))
        return {"spec": mkspec(body.Name, MS, [sup(planes(body, 0, 0.0))], [frc(planes(body, 0, 80.0), "1500 N", (1, 0, 0))], 6.0)}

    rows = [("H01", "point force on a vertex, slender cantilever", b1, 8.0, 20000),
            ("H02", "tension plate with a re-entrant square notch", b2, 6.0, 60 * 30 * 4 - 12 * 12 * 4),
            ("H03", "2 mm tab held along two edge lines", b3, 4.0, 40 * 20 * 2),
            ("H04", "line load across the middle of a beam", b4, 8.0, 10000),
            ("H05", "L bracket, sharp internal corner", b5, 8.0, (60 * 20 + 20 * 40) * 10),
            ("H06", "stepped plate, sharp shoulder", b6, 6.0, (40 * 30 + 40 * 12) * 4)]
    for cid, title, bf, h0, vol in rows:
        out.append(Case(cid, "H", title, h0, bf, {"displacement": "converged"}, dict(H_EXPECT), {}, vol))
    return out


# ---------------------------------------------------------------------------------------------------- group I: refusals and failures
def _cases_I(rng):
    out = []
    S = mat("steel")
    P = "5 kN"

    def base(doc, supports=True, loads=True, material=None, h0=13.3):
        FreeCAD, Part, V = _fc()
        body = _feat(doc, Part.makeBox(100, 10, 10))
        sp = [sup(planes(body, 0, 0))] if supports else []
        ld = [frc(planes(body, 0, 100), P, (0, 0, -1))] if loads else []
        return body, mkspec(body.Name, material or dict(S), sp, ld, h0)

    def i01(doc):
        return {"spec": base(doc, supports=False)[1]}

    def i02(doc):
        return {"spec": base(doc, loads=False)[1]}

    def i03(doc):  # a single fixed edge leaves rotation about it free
        FreeCAD, Part, V = _fc()
        body = _feat(doc, Part.makeBox(100, 20, 20))
        return {"spec": mkspec(body.Name, dict(S), [sup(edges_along(body, 1, {0: 0.0, 2: 0.0}), "xyz")], [frc(planes(body, 0, 100), P, (0, 0, -1))], 26.0)}

    def i04(doc):
        m = dict(S)
        m["E"] = "210000"  # bare number: never read as MPa or Pa
        return {"spec": base(doc, material=m)[1]}

    def i05(doc):
        m = dict(S)
        m["E"] = "210 N"
        return {"spec": base(doc, material=m)[1]}

    def i06(doc):
        m = dict(S)
        m["nu"] = 0.5
        return {"spec": base(doc, material=m)[1]}

    def i07(doc):  # a face, not a solid: zero volume
        FreeCAD, Part, V = _fc()
        body = _feat(doc, Part.makePlane(100, 10))
        return {"spec": mkspec(body.Name, dict(S), [sup([[body.Name, "Edge1"]])], [frc([[body.Name, "Edge3"]], P, (0, 0, -1))], 13.3)}

    def i08(doc):  # load on a hole's cylinder face, then the hole is suppressed: the face is gone
        d = _pd_plate_with_hole(doc)
        ref_hole = cyl_faces(d["body"], 3.0)
        spec = mkspec(d["body"].Name, dict(S), [sup(planes(d["body"], 2, 0.0))], [frc(ref_hole, "100 N", (1, 0, 0))], 12.0)
        return {"spec": spec, "edit": lambda doc: setattr(doc.getObject("HolePocket"), "Suppressed", True)}

    def i09(doc):
        return {"spec": base(doc)[1], "env": {"path_only": ["gmsh"]}}

    def i10(doc):  # 2 mm strip, 5 N at the tip: deflection ~ 60% of the thickness
        FreeCAD, Part, V = _fc()
        body = _feat(doc, Part.makeBox(100, 10, 2))
        return {"spec": mkspec(body.Name, dict(S), [sup(planes(body, 0, 0))], [frc(planes(body, 0, 100), "5 N", (0, 0, -1))], 4.0)}

    def i11(doc):  # `kip` is not a FreeCAD unit; an unknown unit is refused by name, never guessed
        body, spec = base(doc)
        spec["loads"][0]["value"] = "5 kip"
        return {"spec": spec}

    def i12(doc):  # a `ccx` that writes results and then exits non-zero (shim on PATH, see study_corpus_run.py)
        return {"spec": base(doc)[1]}

    def i13(doc):  # a `ccx` whose results hold nan
        return {"spec": base(doc)[1]}

    def i14(doc):  # a small max_elements: refinement must stop and say so
        body, spec = base(doc)
        spec["options"]["max_elements"] = 600
        spec["loads"][0]["value"] = "200 N"  # the default 5 kN deflects 95% of the depth: this case must isolate the cap, not trip another check
        return {"spec": spec}

    rows = [("I01", "no support", i01, "refused", "no_support", 13.3, 10000), ("I02", "no load", i02, "refused", "no_load", 13.3, 10000),
            ("I03", "rotation about a fixed edge left free", i03, "refused", "rigid_body_motion", 26.0, 40000),
            ("I04", "modulus with no unit", i04, "refused", "unit_missing", 13.3, 10000),
            ("I05", "modulus in newtons", i05, "refused", "unit_dimension", 13.3, 10000),
            ("I06", "Poisson ratio 0.5", i06, "refused", "poisson_range", 13.3, 10000),
            ("I07", "body of zero volume (a face)", i07, "refused", "body_invalid", 13.3, 0),
            ("I08", "load on a face removed by an edit", i08, "refused", "reference_lost", 12.0, 4000),
            ("I09", "CalculiX missing from PATH", i09, "failed", "solver_missing", 13.3, 10000),
            ("I10", "displacement above 10% of thickness", i10, "suspect", "large_displacement", 4.0, 2000)]
    rows += [("I11", "load in an unknown unit (kip)", i11, "refused", "unit_unknown", 13.3, 10000),
             ("I12", "ccx exits non-zero after writing results", i12, "failed", "solver_failed", 13.3, 10000),
             ("I13", "ccx results hold nan", i13, "failed", "result_nan", 13.3, 10000),
             ("I14", "max_elements 600 stops the refinement", i14, "unconverged", "element_cap", 13.3, 10000)]
    shims = {"I12": "exit3", "I13": "nan"}
    for cid, title, bf, verdict, code, h0, vol in rows:
        out.append(Case(cid, "I", title, h0, bf, {}, {"verdict": verdict, "code": code}, {"shim": shims[cid]} if cid in shims else {}, vol))
    return out


# ---------------------------------------------------------------------------------------------------- group J: reference survival (PartDesign body)
def _pd_plane(body, plane):
    role = {"XY": "XY_Plane", "XZ": "XZ_Plane", "YZ": "YZ_Plane"}[plane]
    for f in body.Origin.OriginFeatures:
        if f.Role == role:
            return f
    raise KeyError(plane)


def _pd_sketch_rect(doc, body, name, w, h, z=0.0):
    FreeCAD, Part, V = _fc()
    import Sketcher

    sk = body.newObject("Sketcher::SketchObject", name)
    sk.AttachmentSupport = (_pd_plane(body, "XY"), [""])
    sk.MapMode = "FlatFace"
    if z:
        sk.AttachmentOffset = FreeCAD.Placement(V(0, 0, z), FreeCAD.Rotation())
    p = [V(-w / 2, -h / 2, 0), V(w / 2, -h / 2, 0), V(w / 2, h / 2, 0), V(-w / 2, h / 2, 0)]
    for i in range(4):
        sk.addGeometry(Part.LineSegment(p[i], p[(i + 1) % 4]))
    for i in range(4):
        sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
    return sk


def _pd_prism(doc, b, d, L):
    """PartDesign body: pad of a b (x) by d (y) rectangle centred on the z axis, length L along +z. Returns dict(body, pad)."""
    body = doc.addObject("PartDesign::Body", "Body")
    sk = _pd_sketch_rect(doc, body, "Sketch", b, d)
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Profile = sk
    pad.Length = L
    doc.recompute()
    return {"body": body, "pad": pad}


def _pd_plate_with_hole(doc):
    FreeCAD, Part, V = _fc()
    import Sketcher

    d = _pd_prism(doc, 40.0, 20.0, 10.0)
    body = d["body"]
    sk = body.newObject("Sketcher::SketchObject", "HoleSketch")
    sk.AttachmentSupport = (_pd_plane(body, "XY"), [""])
    sk.MapMode = "FlatFace"
    sk.AttachmentOffset = FreeCAD.Placement(V(0, 0, 10.0), FreeCAD.Rotation())
    sk.addGeometry(Part.Circle(V(0, 0, 0), V(0, 0, 1), 3.0))
    pk = body.newObject("PartDesign::Pocket", "HolePocket")
    pk.Profile = sk
    pk.Length = 12.0
    doc.recompute()
    return d


def _pd_fillet_all_z_edges(doc, d, r):
    body, pad = d["body"], d["pad"]
    edges = []
    for i, e in enumerate(pad.Shape.Edges, 1):
        bb = e.BoundBox
        if bb.XLength < 1e-6 and bb.YLength < 1e-6 and bb.ZLength > 1e-6:
            edges.append(f"Edge{i}")
    f = doc.addObject("PartDesign::Fillet", "Fillet")
    body.insertObject(f, pad, True)
    f.Base = (pad, edges)
    f.Radius = r
    body.Tip = f
    doc.recompute()


def _cases_J(rng):
    S = mat("steel")
    S0 = mat("steel", nu=0)
    E, nu, _, _ = MATS["steel"]
    b, d, L = 10.0, 20.0, 100.0  # section x by y; the beam axis is z; the load is along x, so bending is about y, h = b
    I0, A0 = d * b**3 / 12, b * d
    P = 50.0  # N

    def spec_for(doc, dd, material, load):
        body = dd["body"]
        return mkspec(body.Name, material, [sup(planes(body, 2, 0.0))], [load(body)], 13.3)

    def force_x(body, Lz=L):
        return frc(planes(body, 2, Lz), f"{P:g} N", (-1, 0, 0))

    def pd(doc):
        return _pd_prism(doc, b, d, L)

    def j1(doc):  # earlier fillet: radius 2 on the four z edges, inserted after the pad
        dd = pd(doc)
        sp = spec_for(doc, dd, dict(S), force_x)
        return {"spec": sp, "edit": lambda doc: _pd_fillet_all_z_edges(doc, dd, 2.0)}

    def j2(doc):  # added pocket: the last 10 mm are cut off, so the end face moves from z = 100 to z = 90
        dd = pd(doc)
        sp = spec_for(doc, dd, dict(S), force_x)

        def edit(doc):
            body = dd["body"]
            sk = _pd_sketch_rect(doc, body, "CutSketch", 2 * b, 2 * d, z=L)
            pk = body.newObject("PartDesign::Pocket", "CutPocket")
            pk.Profile = sk
            pk.Length = 10.0
            doc.recompute()

        return {"spec": sp, "edit": edit}

    def j3(doc):  # changed length 100 -> 130
        dd = pd(doc)
        sp = spec_for(doc, dd, dict(S), force_x)
        return {"spec": sp, "edit": lambda doc: (setattr(dd["pad"], "Length", 130.0), doc.recompute())}

    def j4(doc):  # axial bar, nu = 0 (exact), length 100 -> 150
        dd = pd(doc)
        sp = spec_for(doc, dd, dict(S0), lambda body: frc(planes(body, 2, L), f"{P * 20:g} N", (0, 0, 1)))
        return {"spec": sp, "edit": lambda doc: (setattr(dd["pad"], "Length", 150.0), doc.recompute())}

    I_f = rounded_rect_I(b, d, 2.0)
    A_f = rounded_rect_A(b, d, 2.0)
    rows = [("J01", "earlier fillet on the four long edges", j1, {"disp": cant_tip(P, L, E, nu, I_f, A_f, kappa_rect()), "tol_disp": 0.03}),
            ("J02", "added pocket cutting 10 mm off the loaded end", j2, {"disp": cant_tip(P, L - 10, E, nu, I0, A0, kappa_rect()), "tol_disp": 0.03}),
            ("J03", "pad length changed 100 to 130", j3, {"disp": cant_tip(P, 130.0, E, nu, I0, A0, kappa_rect()), "tol_disp": 0.03}),
            ("J04", "axial bar, pad length changed 100 to 150", j4, {"disp": P * 20 * 150.0 / (A0 * E), "tol_disp": 1e-5})]
    out = []
    for cid, title, bf, truth in rows:
        t = dict(truth)
        t["accept"] = "either refused with code reference_lost, or a result within tol of the edited geometry's closed form"
        out.append(Case(cid, "J", title, 13.3, bf, t, {"verdict": "converged|refused", "code": "ok|reference_lost"}, {"edit": True}, A0 * L))
    return out


# ---------------------------------------------------------------------------------------------------- stubby cantilevers (run 067, mutant M15)
def _cases_B_stubby():
    """B13, B14: rectangular cantilevers at L/h = 2, where the shear term is about 16% of the deflection, so that kappa = 1 in the formula would
    move the truth by 2.7%. Displacement only: Euler-Bernoulli stress at mid-length is not a truth at L/h = 2. Own random stream (the main one
    stays as it was, so no other case changes)."""
    rng = random.Random(SEED + 1)
    out = []
    for i, axis in ((13, "z"), (14, "y")):
        matname = rng.choice(["steel", "aluminium"])
        E, nu, _, _ = MATS[matname]
        s = _section("rect", axis)
        L = 2 * s["h"]
        P = float(f"{(L / 1000.0) * 3 * E * s['I'] / L**3:.3g}")
        delta = cant_tip(P, L, E, nu, s["I"], s["A"], kappa_rect())
        h0 = 4 * min(SEC_RECT) / 3.0
        direction = (0, -1, 0) if axis == "y" else (0, 0, -1)

        def build(doc, L=L, P=P, matname=matname, direction=direction, h0=h0):
            body = _beam_body(doc, "rect", L)
            return {"spec": mkspec(body.Name, mat(matname), [sup(planes(body, 0, 0))], [frc(planes(body, 0, L), f"{P:g} N", direction)], h0)}

        par = {"section": "rect", "load_axis": axis, "L_over_h": 2, "L": L, "P_N": P, "material": matname}
        out.append(Case(f"B{i}", "B", f"stubby cantilever rect L/h=2 load {axis} {matname}", h0, build, {"disp": delta, "tol_disp": 0.03}, {}, par, s["A"] * L))
    return out


# ---------------------------------------------------------------------------------------------------- registry
def all_cases():
    rng = _rng()
    cases = []
    for fn in (_cases_A, _cases_B, _cases_C, _cases_D, _cases_E, _cases_F, _cases_G, _cases_H, _cases_I, _cases_J):
        cases += fn(rng)
    # B13 and B14 (_cases_B_stubby, L/h = 2) are not graded: L/h = 2 is outside where Timoshenko's formula is a valid truth. They do not kill M15 either.
    return cases


EXPECTED_COUNTS = {"A": 8, "B": 12, "C": 6, "D": 8, "E": 6, "F": 6, "G": 6, "H": 6, "I": 14, "J": 4}


def select(spec=None):
    """`spec` is a comma list of group letters and case ids (e.g. 'A,B03,H'); None means all."""
    cases = all_cases()
    if not spec:
        return cases
    want = [s.strip() for s in spec.split(",") if s.strip()]
    return [c for c in cases if c.group in want or c.id in want]


def selfcheck():
    """Second-route checks of the closed forms, independent of any FE run and of study code."""
    # Navier: square plate, centre deflection 0.00406 q a^4 / D (Timoshenko and Woinowsky-Krieger, tabulated coefficient, written here as a number only)
    a, q, D = 100.0, 1.0, 1.0
    w = navier_w(a / 2, a / 2, a, a, q, D)
    assert abs(w / (q * a**4 / D) - 0.00406) < 5e-6, w / (q * a**4 / D)
    # Lame: the hoop stress integrated over the wall carries the pressure on the half ring, integral_a^b s_theta dr = p a
    p_, a_, b_ = 40.0, 10.0, 25.0
    n = 20000
    tot = sum(lame_hoop(p_, a_, b_, a_ + (i + 0.5) * (b_ - a_) / n) for i in range(n)) * (b_ - a_) / n
    assert abs(tot - p_ * a_) < 1e-6 * p_ * a_, tot
    assert abs(lame_radial(p_, a_, b_, a_) + p_) < 1e-9 and abs(lame_radial(p_, a_, b_, b_)) < 1e-9  # s_r = -p inside, 0 outside
    # Timoshenko limits: slender beam tends to the Euler value; kappa values
    E, nu, I, A, L, P = 210000.0, 0.3, 1000.0, 100.0, 1e5, 1.0
    assert abs(cant_tip(P, L, E, nu, I, A, 0.8) / (P * L**3 / (3 * E * I)) - 1) < 1e-6
    assert abs(kappa_circle(0.3) - 6 * 1.3 / 8.8) < 1e-12
    # section integral: no rounding gives b d^3/12 form, a full semicircle-ended stadium is bigger than the circle-corner-free rect minus corners
    assert abs(rounded_rect_I(10.0, 20.0, 0.0) - 20 * 10**3 / 12) < 1e-6 * 20 * 10**3 / 12
    assert abs(rounded_rect_I(10.0, 10.0, 5.0) - math.pi * 5**4 / 4) < 1e-4 * math.pi * 5**4 / 4  # r = b/2 on a square is the circle: pi r^4 / 4
    # unit spellings agree with the unit definitions: 1 lbf = 4.4482216152605 N, 1 psi = 6894.757293168 Pa
    assert abs(float(_p_spellings(1000.0)[3].split()[0]) * LBF_N - 1000.0) < 1e-8 and abs(float(_e_spellings(210000.0)[3].split()[0]) * PSI_MPA - 210000.0) < 1e-5
    print("selfcheck ok: Navier 0.00406, Lame wall balance, Timoshenko limit, kappa, section integral, unit constants")


def main():
    if "--selfcheck" in __import__("sys").argv:
        selfcheck()
    cs = all_cases()
    counts = {}
    for c in cs:
        counts[c.group] = counts.get(c.group, 0) + 1
    assert counts == EXPECTED_COUNTS, counts
    print(f"{len(cs)} cases, seed {SEED}")
    for c in cs:
        print(f"{c.id}\th0={c.h0:g}\test_elems_fine={c.est_elements_fine}\t{c.title}")


if __name__ == "__main__":
    main()
