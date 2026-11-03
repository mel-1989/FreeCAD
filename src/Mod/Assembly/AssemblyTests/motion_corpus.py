# SPDX-License-Identifier: LGPL-2.1-or-later
"""Motion corpus (G06 slice 6a): deterministic mechanisms with a closed-form truth, built by FreeCADCmd.

Every case builds a small assembly (``Rig``) in a fresh document, sets up a ``MotionStudy`` and returns a ``Case``
whose ``check(result, rows)`` compares the stored result with the truth and appends one row per metric
(``case, metric, error, bar, ok``). The truth is closed form and never read from a run of the same mechanism:

- simple pendulum: T = 4 K(sin^2(theta0/2)) / sqrt(g/L) (complete elliptic integral of the first kind; at 1 degree
  it differs from 2 pi sqrt(L/g) by 1.9e-5 relative);
- compound pendulum: T = 4 K / sqrt(m g d / I_pivot) with I_pivot from the box or cylinder dimensions
  (I_com = m (a^2 + b^2) / 12 for a box, m R^2 / 2 for a disc about its axis, plus m d^2), not from OCCT;
- spring and mass: omega = sqrt(k / m), x(t) = x0 cos(omega t) (and about the static equilibrium under gravity);
- damped oscillator: log decrement 2 pi zeta / sqrt(1 - zeta^2) between successive peaks, and the closed-form
  response x(t) for under, critical and over damping;
- free fall z0 + v0 t - g t^2 / 2 and a constant force x = F t^2 / (2 m) on a slider;
- double pendulum and spring pendulum: total energy over time (no closed form for the positions);
- kinematics through the assembly's own solver: slider-crank x = r cos(a) + sqrt(L^2 - r^2 sin^2(a)), four-bar by
  Freudenstein's equation, gear ratio -r1/r2 (belt +r1/r2), rack travel r * angle.

Masses are density times the volume of the part's own box or cylinder (7850 kg/m^3 steel, 8000 for the 1 kg
blocks) or set by hand for the point masses. Every case is built twice: ``single`` (the boxes sit in the assembly's
own group) and ``link`` (``App::Link`` components whose sources are outside the assembly, in the same document or,
for ``xdoc``, in another document).
"""

import math

import FreeCAD as App

import AssemblyMotion as MO
import AssemblyTests.configuration_corpus as cc

V = App.Vector
P = App.Placement
R = App.Rotation
G = 9.80665
STEEL = 7850.0
BLOCK_RHO = 8000.0            # a 50 mm cube of it weighs exactly 1 kg
CASES = {}
MODES = ("single", "link")


def case(fn):
    CASES[fn.__name__] = fn
    return fn


# ------------------------------------------------------------------ rows

class Rows(list):
    def add(self, case, metric, err, bar, extra=""):
        self.append({"case": case, "metric": metric, "error": float(err), "bar": float(bar),
                     "ok": bool(err <= bar), "info": extra})


class Case:
    def __init__(self, name, klass, rig, study, check, step, end, integrator, expects=""):
        self.name, self.klass, self.rig, self.study = name, klass, rig, study
        self.check = check
        self.step, self.end, self.integrator = step, end, integrator
        self.expects = expects


# ------------------------------------------------------------------ the rig

def rot_about(pl, pivot, axis, deg):
    """The placement ``pl`` turned by ``deg`` about the line through ``pivot`` along ``axis``."""
    return P(V(), R(V(*axis), deg), V(*pivot)).multiply(pl)


def frame_at(point, axis):
    """World placement whose z axis points along ``axis`` and whose origin is ``point``."""
    return P(V(*point), R(V(0, 0, 1), V(*axis)))


class Rig:
    """An assembly with a grounded plate, parts in ``single`` or ``link`` form, and joints given in world terms."""

    def __init__(self, doc, mode, host=None, ground_size=(400, 400, 10)):
        self.doc, self.mode = doc, mode
        self.host = host or doc
        self.asm, self.jg = cc.new_assembly(doc)
        self.parts = {}
        self.shape = {}
        self.G = self.box("G", ground_size[0], ground_size[1], ground_size[2], P(V(-200, -200, -10), R()))
        cc.ground(self.jg, self.G)

    def _place(self, name, src_factory, pl):
        if self.mode == "single":
            o = src_factory(self.asm.newObject, name)
            o.Placement = pl
        else:
            src = src_factory(self.host.addObject, name + "Src")
            src.Visibility = False
            o = self.asm.newObject("App::Link", name)
            o.LinkedObject = src
            o.Placement = pl
        self.parts[name] = o
        return o

    def box(self, name, l, w, h, pl):
        def make(f, n):
            b = f("Part::Box", n)
            b.Length, b.Width, b.Height = l, w, h
            return b
        self.shape[name] = ("box", l, w, h)
        return self._place(name, make, pl)

    def cyl(self, name, r, h, pl):
        def make(f, n):
            c = f("Part::Cylinder", n)
            c.Radius, c.Height = r, h
            return c
        self.shape[name] = ("cyl", r, h)
        return self._place(name, make, pl)

    def sph(self, name, r, pl):
        def make(f, n):
            s = f("Part::Sphere", n)
            s.Radius = r
            return s
        self.shape[name] = ("sph", r)
        return self._place(name, make, pl)

    def _local(self, obj, world_frame):
        return obj.Placement.inverse().multiply(world_frame)

    def joint(self, name, typ, A, B, frame_world, frame_world_b=None, **kw):
        fa = self._local(A, frame_world)
        fb = self._local(B, frame_world_b if frame_world_b is not None else frame_world)
        return cc.jt(self.jg, name, typ, A, fa, B, fb, **kw)

    def rev(self, name, A, B, pivot, axis):
        return self.joint(name, "Revolute", A, B, frame_at(pivot, axis))

    def slide(self, name, A, B, point, axis):
        return self.joint(name, "Slider", A, B, frame_at(point, axis))

    def fixed(self, name, A, B, point=(0, 0, 0)):
        return self.joint(name, "Fixed", A, B, P(V(*point), R()))

    def L(self, part, world):
        """A world point in the part's own frame (mm), as a tuple."""
        return tuple(part.Placement.inverse().multVec(V(*world)))

    def finish(self):
        self.doc.recompute()
        return self


def new_study(rig, end, step, mode="Dynamic", **kw):
    return MO.create_study(rig.asm, mode=mode, time_end=end, step=step, **kw)


# ------------------------------------------------------------------ series helpers

def series(res, name):
    for p in res["parts"]:
        if p["name"] == name:
            return p["placements"]
    raise KeyError(name)


def joint(res, name):
    for j in res["joints"]:
        if j["name"] == name:
            return j
    raise KeyError(name)


def pl_of(d):
    return P(V(*d["base"]), R(*d["rotation"]))


def upcrossings(t, y, level):
    """Times at which y crosses ``level`` upward, linearly interpolated."""
    out = []
    for i in range(len(y) - 1):
        a, b = y[i] - level, y[i + 1] - level
        if a < 0 <= b:
            out.append(t[i] + (t[i + 1] - t[i]) * (-a) / (b - a))
    return out


def period_of(t, y, level):
    c = upcrossings(t, y, level)
    if len(c) < 3:
        return None, len(c)
    return (c[-1] - c[0]) / (len(c) - 1), len(c) - 1


def peaks(t, y):
    """Local maxima with parabolic refinement: [(time, value)]."""
    out = []
    for i in range(1, len(y) - 1):
        if y[i] > y[i - 1] and y[i] >= y[i + 1] and y[i] > 0:
            a, b, c = y[i - 1], y[i], y[i + 1]
            den = a - 2 * b + c
            off = 0.5 * (a - c) / den if den else 0.0
            val = b - 0.25 * (a - c) * off
            out.append((t[i] + off * (t[i + 1] - t[i]), val))
    return out


def ellipk(m):
    from scipy.special import ellipk as _k
    return float(_k(m))


def yaw(q):
    """Rotation angle about z of a quaternion (x, y, z, w) that is a pure z rotation."""
    return 2.0 * math.atan2(q[2], q[3])


def wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


def energy_rows(rows, name, res, scale_truth=None, bar=1e-3):
    """Total energy within ``bar`` of its start (relative to max(|E0|, max kinetic)); the kinetic maximum must
    also match the analytic swing energy when the case gives one (so a missing energy cannot pass)."""
    e = res["energy"]
    tot = e["total"]
    ke = e["kinetic"]
    scale = max(abs(tot[0]), max(ke))
    if scale <= 0:
        rows.add(name, "energy scale", 1.0, 0.0, "no energy reported")
        return
    drift = max(abs(x - tot[0]) for x in tot) / scale
    rows.add(name, "energy drift", drift, bar, "max |E-E0| / max(|E0|, max KE)")
    if scale_truth is not None:
        rows.add(name, "max kinetic energy", abs(max(ke) - scale_truth) / scale_truth, 0.01,
                 "against the analytic swing energy")


# ------------------------------------------------------------------ pendulums

def _pendulum_common(doc, mode, name, L_mm, theta0_deg, end_periods=10.2, step=0.002, host=None):
    """Point-mass pendulum (hand mass) on an axis along y through (0, 0, 1000); gravity in -z."""
    rig = Rig(doc, mode, host)
    pivot = (0.0, 0.0, 1000.0)
    hang = P(V(0, 0, 1000), R())
    bob = rig.box("Bob", 20, 20, 20, rot_about(hang, pivot, (0, 1, 0), -theta0_deg))
    rig.rev("Hinge", rig.G, bob, pivot, (0, 1, 0))
    rig.finish()
    study = new_study(rig, end_periods * 4 * ellipk(math.sin(math.radians(theta0_deg) / 2) ** 2)
                      / math.sqrt(G / (L_mm * 1e-3)), step)
    m = 0.75
    MO.set_mass(study, bob, mass=m, com=(0, 0, -L_mm), inertia=[0.0] * 6)
    MO.add_gravity(study)
    return rig, study, bob, pivot, m


def make_pendulum(name, L_mm, theta0, small=False):
    def build(doc, mode, host=None):
        rig, study, bob, pivot, m = _pendulum_common(doc, mode, name, L_mm, theta0, host=host)

        def check(res, rows):
            L = L_mm * 1e-3
            t = res["t"]
            q = joint(res, "Hinge")["q"]
            th0 = math.radians(theta0)
            T_true = 4 * ellipk(math.sin(th0 / 2) ** 2) / math.sqrt(G / L)
            if small:
                T_true = 2 * math.pi * math.sqrt(L / G)
            T, n = period_of(t, q, th0)
            rows.add(name, "period", abs(T - T_true) / T_true, 1e-3, "%d periods" % n)
            # the part follows the joint: COM from the placements against the geometry
            worst = 0.0
            for k in range(0, len(t), 7):
                pl = pl_of(series(res, "Bob")[k])
                com = pl.multVec(V(0, 0, -L_mm))
                ang = th0 - q[k]
                truth = V(L_mm * math.sin(ang), 0, 1000 - L_mm * math.cos(ang))
                worst = max(worst, (com - truth).Length)
            rows.add(name, "bob position (mm)", worst, 1e-6)
            e_swing = m * G * L * (1 - math.cos(th0))
            energy_rows(rows, name, res, e_swing)
        return Case(name, "dynamics/pendulum", rig, study, check, 0.002, study.TimeEnd, "DOP853")
    CASES[name] = build
    return build


make_pendulum("pend_small_L500", 500.0, 1.0, small=True)
make_pendulum("pend_small_L250", 250.0, 1.0, small=True)
make_pendulum("pend_30_L400", 400.0, 30.0)
make_pendulum("pend_60_L500", 500.0, 60.0)
make_pendulum("pend_120_L500", 500.0, 120.0)


def make_compound(name, kind, theta0, pivot_from_top=0.0):
    def build(doc, mode, host=None):
        rig = Rig(doc, mode, host)
        pivot = (0.0, 0.0, 1000.0)
        if kind == "rod":
            Lx, Ly, Lz = 20.0, 20.0, 600.0
            base = P(V(-10, -10, 1000 - Lz + pivot_from_top), R())
            part = rig.box("Rod", Lx, Ly, Lz, rot_about(base, pivot, (0, 1, 0), -theta0))
            m = STEEL * Lx * Ly * Lz * 1e-9
            d = (Lz / 2 - pivot_from_top) * 1e-3
            I_com = m * ((Lx * 1e-3) ** 2 + (Lz * 1e-3) ** 2) / 12
        else:
            Rr, Hh = 80.0, 20.0
            hang = P(V(0, 0, 1000 - Rr), R(V(1, 0, 0), -90)).multiply(P(V(0, 0, -Hh / 2), R()))
            part = rig.cyl("Disc", Rr, Hh, rot_about(hang, pivot, (0, 1, 0), -theta0))
            m = STEEL * math.pi * Rr ** 2 * Hh * 1e-9
            d = Rr * 1e-3
            I_com = 0.5 * m * (Rr * 1e-3) ** 2
        rig.rev("Hinge", rig.G, part, pivot, (0, 1, 0))
        rig.finish()
        I_piv = I_com + m * d * d
        w0 = math.sqrt(m * G * d / I_piv)
        th0 = math.radians(theta0)
        T_true = 4 * ellipk(math.sin(th0 / 2) ** 2) / w0
        study = new_study(rig, 10.2 * T_true, 0.002)
        MO.set_mass(study, part, density=STEEL)
        MO.add_gravity(study)

        def check(res, rows):
            q = joint(res, "Hinge")["q"]
            T, n = period_of(res["t"], q, th0)
            rows.add(name, "period", abs(T - T_true) / T_true, 1e-3, "%d periods, I_pivot=%.6g kg m2" % (n, I_piv))
            energy_rows(rows, name, res, m * G * d * (1 - math.cos(th0)))
        return Case(name, "dynamics/compound pendulum", rig, study, check, 0.002, study.TimeEnd, "DOP853")
    CASES[name] = build
    return build


make_compound("compound_rod_end", "rod", 5.0)
make_compound("compound_rod_quarter", "rod", 8.0, pivot_from_top=150.0)
make_compound("compound_disc_rim", "disc", 10.0)


def make_welded_rod(name, theta0):
    """The compound rod of ``compound_rod_end`` cut in two halves joined by a fixed joint: same period."""
    def build(doc, mode, host=None):
        rig = Rig(doc, mode, host)
        pivot = (0.0, 0.0, 1000.0)
        top = rig.box("RodTop", 20, 20, 300, rot_about(P(V(-10, -10, 700), R()), pivot, (0, 1, 0), -theta0))
        low = rig.box("RodLow", 20, 20, 300, rot_about(P(V(-10, -10, 400), R()), pivot, (0, 1, 0), -theta0))
        rig.rev("Hinge", rig.G, top, pivot, (0, 1, 0))
        weld = rot_about(P(V(0, 0, 700), R()), pivot, (0, 1, 0), -theta0)
        rig.joint("Weld", "Fixed", top, low, weld)
        rig.finish()
        m = STEEL * 20 * 20 * 600 * 1e-9
        d = 0.3
        I_piv = m * (0.02 ** 2 + 0.6 ** 2) / 12 + m * d * d
        th0 = math.radians(theta0)
        T_true = 4 * ellipk(math.sin(th0 / 2) ** 2) / math.sqrt(m * G * d / I_piv)
        study = new_study(rig, 10.2 * T_true, 0.002)
        MO.set_mass(study, top, density=STEEL)
        MO.set_mass(study, low, density=STEEL)
        MO.add_gravity(study)

        def check(res, rows):
            q = joint(res, "Hinge")["q"]
            T, n = period_of(res["t"], q, th0)
            rows.add(name, "period", abs(T - T_true) / T_true, 1e-3, "%d periods, two bodies, one fixed joint" % n)
            energy_rows(rows, name, res, m * G * d * (1 - math.cos(th0)))
            # the welded half stays rigid with the first: relative placement constant
            rel0 = None
            worst = 0.0
            for k in range(0, len(res["t"]), 13):
                a, b = pl_of(series(res, "RodTop")[k]), pl_of(series(res, "RodLow")[k])
                rel = a.inverse().multiply(b)
                rel0 = rel0 or rel
                worst = max(worst, (rel.Base - rel0.Base).Length)
            rows.add(name, "weld stays rigid (mm)", worst, 1e-9)
        return Case(name, "dynamics/compound pendulum", rig, study, check, 0.002, study.TimeEnd, "DOP853")
    CASES[name] = build
    return build


make_welded_rod("compound_rod_welded", 5.0)


# ------------------------------------------------------------------ spring and mass, damped

def _block_rig(doc, mode, host, axis, x0_mm, ground_anchor=(0.0, 25.0, 25.0), start=(0.0, 0.0, 0.0)):
    """A 1 kg 50 mm cube on a slider along ``axis`` from the point ``start``."""
    rig = Rig(doc, mode, host)
    blk = rig.box("Block", 50, 50, 50, P(V(*start), R()))
    rig.slide("Rail", rig.G, blk, (start[0] + 25, start[1] + 25, start[2] + 25), axis)
    return rig, blk


def make_spring_mass(name, k, vertical=False):
    def build(doc, mode, host=None):
        m = 1.0
        w = math.sqrt(k / m)
        T = 2 * math.pi / w
        if not vertical:
            rig, blk = _block_rig(doc, mode, host, (1, 0, 0), 30.0, start=(230.0, 0.0, 0.0))
            rig.finish()
            study = new_study(rig, 10.5 * T, T / 200)
            MO.set_mass(study, blk, density=BLOCK_RHO)
            MO.add_spring(study, rig.G, rig.L(rig.G, (0, 25, 25)), blk, (0, 25, 25), k, 200.0)
            x_ref = 230.0
            eq = 200.0
            amp = 30.0
        else:
            L0 = 200.0
            ext0 = 40.0
            ext_eq = m * G / k * 1e3                 # mm
            anchor_z = 1000.0
            top = anchor_z - L0 - ext0               # the block point sits ext0 below the free end
            rig, blk = _block_rig(doc, mode, host, (0, 0, 1), 0.0, start=(0.0, 0.0, top - 25.0))
            rig.finish()
            study = new_study(rig, 10.5 * T, T / 200)
            MO.set_mass(study, blk, density=BLOCK_RHO)
            MO.add_spring(study, rig.G, rig.L(rig.G, (25, 25, anchor_z)), blk, (25, 25, 25), k, L0)
            MO.add_gravity(study)
            amp = ext0 - ext_eq

        def check(res, rows):
            t = res["t"]
            q = joint(res, "Rail")["q"]            # metres along the rail
            if vertical:
                level = ext_eq * 1e-3
                # q is the z displacement from the start (down is negative): extension = ext0 + q... in metres
                ext = [(ext0 * 1e-3) + qq * -1.0 for qq in q]
                y = [e for e in ext]
                eq_m = ext_eq * 1e-3
                Tm, n = period_of(t, [-(e - eq_m) for e in y], 0.0)
                truth = [eq_m + (ext0 * 1e-3 - eq_m) * math.cos(w * tt) for tt in t]
                perr = max(abs(a - b) for a, b in zip(y, truth)) / (ext0 * 1e-3 - eq_m)
                # the block keeps hanging below the anchor: its placement z
                zbad = 0.0
                for k_ in range(0, len(t), 11):
                    pl = pl_of(series(res, "Block")[k_])
                    zbad = max(zbad, abs(pl.Base.z - (top - 25.0 + q[k_] * 1e3)))
                rows.add(name, "placement against joint (mm)", zbad, 1e-6)
            else:
                truth = [(amp * 1e-3) * math.cos(w * tt) for tt in t]
                # q starts at 0 and the block starts at +amp from the spring's free position
                disp = [amp * 1e-3 + qq for qq in q]
                perr = max(abs(a - b) for a, b in zip(disp, truth)) / (amp * 1e-3)
                Tm, n = period_of(t, [-(d) for d in disp], 0.0)
            rows.add(name, "period", abs(Tm - T) / T, 1e-3, "%d periods" % n)
            rows.add(name, "frequency", abs((1 / Tm) - (1 / T)) / (1 / T), 1e-3)
            rows.add(name, "position vs x0 cos(wt) (relative to amplitude)", perr, 1e-6)
            energy_rows(rows, name, res, 0.5 * k * ((amp * 1e-3) ** 2) if not vertical else None)
        return Case(name, "dynamics/spring-mass", rig, study, check, T / 200, study.TimeEnd, "DOP853")
    CASES[name] = build
    return build


make_spring_mass("spring_mass_k200", 200.0)
make_spring_mass("spring_mass_k800", 800.0)
make_spring_mass("spring_mass_k3200", 3200.0)
make_spring_mass("spring_mass_vertical_k500", 500.0, vertical=True)


def make_damped(name, zeta):
    def build(doc, mode, host=None):
        k, m = 400.0, 1.0
        wn = math.sqrt(k / m)
        c = 2 * zeta * math.sqrt(k * m)
        rig, blk = _block_rig(doc, mode, host, (1, 0, 0), 30.0, start=(230.0, 0.0, 0.0))
        rig.finish()
        if zeta < 1:
            end = 4.5 / (zeta * wn)
            step = 0.002
        elif zeta == 1:
            end, step = 1.0, 0.002
        else:
            end, step = 1.5, 0.002
        study = new_study(rig, end, step)
        MO.set_mass(study, blk, density=BLOCK_RHO)
        MO.add_spring(study, rig.G, rig.L(rig.G, (0, 25, 25)), blk, (0, 25, 25), k, 200.0)
        MO.add_damper(study, rig.G, rig.L(rig.G, (0, 25, 25)), blk, (0, 25, 25), c)
        x0 = 0.03

        def x_true(t):
            if zeta < 1:
                wd = wn * math.sqrt(1 - zeta ** 2)
                return x0 * math.exp(-zeta * wn * t) * (math.cos(wd * t) + zeta * wn / wd * math.sin(wd * t))
            if zeta == 1:
                return x0 * (1 + wn * t) * math.exp(-wn * t)
            r = math.sqrt(zeta ** 2 - 1)
            s1, s2 = -wn * (zeta - r), -wn * (zeta + r)
            return x0 * (s2 * math.exp(s1 * t) - s1 * math.exp(s2 * t)) / (s2 - s1)

        def check(res, rows):
            t = res["t"]
            q = joint(res, "Rail")["q"]
            disp = [x0 + qq for qq in q]
            rows.add(name, "response vs closed form (relative to x0)",
                     max(abs(a - x_true(tt)) for a, tt in zip(disp, t)) / x0, 1e-6 if zeta >= 1 else 1e-5)
            if zeta < 1:
                pk = peaks(t, disp)
                lds = [math.log(pk[i][1] / pk[i + 1][1]) for i in range(len(pk) - 1)]
                truth = 2 * math.pi * zeta / math.sqrt(1 - zeta ** 2)
                rows.add(name, "log decrement", max(abs(d - truth) for d in lds) / truth, 0.01, "%d peaks" % len(pk))
        return Case(name, "dynamics/damped", rig, study, check, step, end, "DOP853")
    CASES[name] = build
    return build


make_damped("damped_under_z005", 0.05)
make_damped("damped_under_z010", 0.10)
make_damped("damped_under_z030", 0.30)
make_damped("damped_critical", 1.0)
make_damped("damped_over_z2", 2.0)


# ------------------------------------------------------------------ free fall and a constant force

def make_fall(name, axis, v0=0.0):
    def build(doc, mode, host=None):
        ax = V(*axis)
        ax.normalize()
        rig = Rig(doc, mode, host)
        start = (0.0, 0.0, 800.0)
        blk = rig.box("Block", 50, 50, 50, P(V(*start), R()))
        rig.slide("Rail", rig.G, blk, (25, 25, 825), tuple(ax))
        rig.finish()
        study = new_study(rig, 0.4, 0.004)
        MO.set_mass(study, blk, density=BLOCK_RHO)
        MO.add_gravity(study)
        if v0:
            study.Velocities = '{"Rail": %r}' % v0
        ga = -G * ax.z                                   # gravity along the rail axis, m/s^2

        def check(res, rows):
            t = res["t"]
            pls = series(res, "Block")
            worst, span = 0.0, 0.0
            for tt, d in zip(t, pls):
                s = (v0 * 1e-3) * tt + 0.5 * ga * tt * tt         # metres along the axis
                truth = V(*start) + ax * (s * 1e3)
                span = max(span, abs(s * 1e3))
                worst = max(worst, (pl_of(d).Base - truth).Length)
            rows.add(name, "position (relative to the travel)", worst / span, 1e-6, "travel %.4g mm" % span)
        return Case(name, "dynamics/free fall", rig, study, check, 0.004, 0.4, "DOP853")
    CASES[name] = build
    return build


make_fall("fall_vertical", (0, 0, -1))
make_fall("fall_inclined_30", (math.sin(math.radians(30)), 0, -math.cos(math.radians(30))))
make_fall("fall_with_initial_speed", (0, 0, 1), v0=2000.0)


def make_falling_ball(name, gap_mm):
    """A steel ball released ``gap_mm`` above the grounded plate. There is no contact model in 6a, so it falls
    through; the interference API is asked for the first step at which ball and plate overlap."""
    def build(doc, mode, host=None):
        r = 25.0
        rig = Rig(doc, mode, host)
        ball = rig.sph("Ball", r, P(V(0, 0, gap_mm + r), R()))
        rig.slide("Rail", rig.G, ball, (0, 0, gap_mm + r), (0, 0, -1))
        rig.finish()
        step, end = 0.004, 0.4
        study = new_study(rig, end, step)
        MO.set_mass(study, ball, density=STEEL)
        MO.add_gravity(study)

        def check(res, rows):
            worst = 0.0
            for tt, d in zip(res["t"], series(res, "Ball")):
                worst = max(worst, abs(pl_of(d).Base.z - (gap_mm + r - 0.5 * G * tt * tt * 1e3)))
            rows.add(name, "position (mm, relative to the 1 m travel)", worst / 1000.0, 1e-6)
        c = Case(name, "interplay/collision", rig, study, check, step, end, "DOP853")
        c.plate, c.ball, c.contact_time = rig.G, ball, math.sqrt(2 * gap_mm * 1e-3 / G)
        return c
    CASES[name] = build
    return build


make_falling_ball("falling_ball_on_plate", 400.0)


def make_force(name, axis_deg, force, frame):
    def build(doc, mode, host=None):
        ax = V(math.cos(math.radians(axis_deg)), math.sin(math.radians(axis_deg)), 0)
        rig = Rig(doc, mode, host)
        base = P(V(0, 0, 0), R(V(0, 0, 1), axis_deg))
        blk = rig.box("Block", 50, 50, 50, base)
        rig.slide("Rail", rig.G, blk, tuple(base.multVec(V(25, 25, 25))), tuple(ax))
        rig.finish()
        study = new_study(rig, 0.5, 0.005)
        MO.set_mass(study, blk, density=BLOCK_RHO)
        MO.add_force(study, blk, (25, 25, 25), force, frame=frame)
        F = V(*force)
        f_axis = (F.x * ax.x + F.y * ax.y) if frame == "World" else F.x      # N along the rail
        a = f_axis / 1.0

        def check(res, rows):
            t = res["t"]
            worst, span = 0.0, 0.0
            for tt, d in zip(t, series(res, "Block")):
                s = 0.5 * a * tt * tt * 1e3
                truth = base.Base + ax * s
                span = max(span, abs(s))
                worst = max(worst, (pl_of(d).Base - truth).Length)
            rows.add(name, "position (relative to the travel)", worst / span, 1e-6, "travel %.4g mm" % span)
        return Case(name, "dynamics/constant force", rig, study, check, 0.005, 0.5, "DOP853")
    CASES[name] = build
    return build


make_force("force_along_rail", 0.0, (10.0, 0.0, 0.0), "World")
make_force("force_off_axis", 0.0, (10.0, 10.0, 0.0), "World")
make_force("force_in_part_frame", 40.0, (7.0, 3.0, 0.0), "Part")


# ------------------------------------------------------------------ interplay: a suppressed component

def make_suppressed_spring(name, via):
    """A point-mass pendulum with a stiff spring (or a damper) to a part that a configuration suppresses: the
    truth is the bare pendulum. ``via`` is the load kind."""
    def build(doc, mode, host=None):
        import AssemblyConfiguration as AC
        rig, study, bob, pivot, m = _pendulum_common(doc, mode, name, 400.0, 30.0, host=host)
        anchor = rig.box("Anchor", 20, 20, 20, P(V(-300, 0, 700), R()))
        rig.finish()
        if via == "spring":
            MO.add_spring(study, anchor, (10, 10, 10), bob, (0, 0, -400), 5000.0, 50.0)
        else:
            MO.add_damper(study, anchor, (10, 10, 10), bob, (0, 0, -400), 50.0)
        AC.create(rig.asm, "NoAnchor", suppress=["Anchor"])
        AC.activate(rig.asm, "NoAnchor")

        def check(res, rows):
            L = 0.4
            th0 = math.radians(30.0)
            q = joint(res, "Hinge")["q"]
            T_true = 4 * ellipk(math.sin(th0 / 2) ** 2) / math.sqrt(G / L)
            T, n = period_of(res["t"], q, th0)
            rows.add(name, "period of the bare pendulum", abs(T - T_true) / T_true, 1e-3, "%d periods" % n)
            energy_rows(rows, name, res, m * G * L * (1 - math.cos(th0)))
            skipped = [x for x in res["loads"] if x["kind"] in ("Spring", "Damper")]
            rows.add(name, "the load is listed as skipped", 0.0 if skipped and not skipped[0]["applied"] else 1.0, 0.0)
        return Case(name, "interplay/suppressed", rig, study, check, 0.002, study.TimeEnd, "DOP853")
    CASES[name] = build
    return build


make_suppressed_spring("suppressed_spring_to_pendulum", "spring")
make_suppressed_spring("suppressed_damper_to_pendulum", "damper")


# ------------------------------------------------------------------ energy only

def make_double(name, th1, th2, spring=False):
    def build(doc, mode, host=None):
        rig = Rig(doc, mode, host)
        pivot1 = (0.0, 0.0, 1000.0)
        L = 300.0
        h1 = P(V(-10, -10, 1000 - L), R())
        l1 = rig.box("Link1", 20, 20, L, rot_about(h1, pivot1, (0, 1, 0), -th1))
        # the second hinge sits at the lower end of link 1 in its turned pose
        piv2 = l1.Placement.multVec(V(10, 10, 0))
        h2 = P(V(-10, -10, 1000 - 2 * L), R())
        l2 = rig.box("Link2", 20, 20, L, rot_about(rot_about(h2, pivot1, (0, 1, 0), -th1), tuple(piv2), (0, 1, 0), -th2))
        piv2 = l1.Placement.multVec(V(10, 10, 0))
        rig.rev("Hinge1", rig.G, l1, pivot1, (0, 1, 0))
        rig.rev("Hinge2", l1, l2, tuple(piv2), (0, 1, 0))
        rig.finish()
        study = new_study(rig, 20.0, 0.005)
        MO.set_mass(study, l1, density=STEEL)
        MO.set_mass(study, l2, density=STEEL)
        MO.add_gravity(study)
        if spring:
            MO.add_spring(study, rig.G, rig.L(rig.G, (150, 0, 1000)), l2, (10, 10, 100), 120.0, 150.0)

        def check(res, rows):
            energy_rows(rows, name, res, None)
            rows.add(name, "motion happened (max kinetic energy, J)", 0.0 if max(res["energy"]["kinetic"]) > 1e-3 else 1.0, 0.0)
        return Case(name, "dynamics/energy", rig, study, check, 0.005, 20.0, "DOP853")
    CASES[name] = build
    return build


make_double("double_pendulum_large", 120.0, 60.0)
make_double("double_pendulum_small", 20.0, -10.0)
make_double("spring_double_pendulum", 70.0, 30.0, spring=True)


# ------------------------------------------------------------------ kinematics (the assembly's own solver)

def _kin_study(rig, joint_obj, formula, end, step, dtype="Angular"):
    st = new_study(rig, end, step, mode="Kinematic")
    st.DriveJoint = joint_obj.Name
    st.DriveType = dtype
    st.DriveFormula = formula
    return st


def make_slider_crank(name, r, L):
    def build(doc, mode, host=None):
        rig = Rig(doc, mode, host)
        O = V(30, 50, 15)
        c5 = V(5, 5, 5)
        crank = rig.box("Crank", r + 10, 10, 10, P(O - c5, R()))
        pin = O + V(r, 0, 0)
        rod = rig.box("Rod", L + 10, 10, 10, P(pin - c5, R()))
        end = pin + V(L, 0, 0)
        sl = rig.box("Slide", 10, 10, 10, P(end - c5, R()))
        h = rig.rev("CrankHinge", rig.G, crank, tuple(O), (0, 0, 1))
        rig.rev("Pin", crank, rod, tuple(pin), (0, 0, 1))
        rig.rev("RodEnd", rod, sl, tuple(end), (0, 0, 1))
        rig.slide("Rail", rig.G, sl, (0, 50, 15), (1, 0, 0))
        rig.finish()
        n = 90
        study = _kin_study(rig, h, "2*pi*time", 1.0, 1.0 / n)

        def check(res, rows):
            t = res["t"]
            ex = ea = 0.0
            for k, tt in enumerate(t):
                th = 2 * math.pi * tt
                xs = O.x + r * math.cos(th) + math.sqrt(L * L - (r * math.sin(th)) ** 2)
                got = pl_of(series(res, "Slide")[k]).Base.x + 5.0
                ex = max(ex, abs(got - xs))
                ac = yaw(series(res, "Crank")[k]["rotation"])
                ea = max(ea, abs(wrap(ac - th)))
                ar = yaw(series(res, "Rod")[k]["rotation"])
                ea = max(ea, abs(wrap(ar - math.atan2(-r * math.sin(th), xs - (O.x + r * math.cos(th))))))
            rows.add(name, "slider position (mm)", ex, 1e-6)
            rows.add(name, "crank and rod angles (rad)", ea, 1e-9)
        return Case(name, "kinematics/slider-crank", rig, study, check, 1.0 / n, 1.0, "OndselSolver")
    CASES[name] = build
    return build


make_slider_crank("slider_crank_r20_L60", 20.0, 60.0)
make_slider_crank("slider_crank_r35_L90", 35.0, 90.0)


def make_four_bar(name, a, b, c, d, sweep_turns):
    def build(doc, mode, host=None):
        rig = Rig(doc, mode, host)
        c5 = V(5, 5, 5)
        O2 = V(60, 100, 15)
        O4 = O2 + V(d, 0, 0)
        A = O2 + V(a, 0, 0)
        dx = O4.x - A.x
        D = abs(dx)
        aa = (b * b - c * c + D * D) / (2 * D)
        hh = math.sqrt(b * b - aa * aa)
        Bp = V(A.x + aa * dx / D, A.y + hh, 15)
        crank = rig.box("Crank", a + 10, 10, 10, P(O2 - c5, R()))
        ang = math.degrees(math.atan2((Bp - A).y, (Bp - A).x))
        coup = rig.box("Coupler", b + 10, 10, 10, rot_about(P(A - c5, R()), tuple(A), (0, 0, 1), ang))
        angf = math.degrees(math.atan2((Bp - O4).y, (Bp - O4).x))
        fol = rig.box("Follower", c + 10, 10, 10, rot_about(P(O4 - c5, R()), tuple(O4), (0, 0, 1), angf))
        h = rig.rev("H2", rig.G, crank, tuple(O2), (0, 0, 1))
        rig.rev("H4", rig.G, fol, tuple(O4), (0, 0, 1))
        rig.rev("PinA", crank, coup, tuple(A), (0, 0, 1))
        rig.rev("PinB", coup, fol, tuple(Bp), (0, 0, 1))
        rig.finish()
        n = 72
        study = _kin_study(rig, h, "2*pi*%r*time" % sweep_turns, 1.0, 1.0 / n)
        K1, K2, K3 = d / a, d / c, (a * a - b * b + c * c + d * d) / (2 * a * c)
        th4_0 = math.atan2((Bp - O4).y, (Bp - O4).x)

        def th4(t2, near):
            A_ = math.cos(t2) - K1 - K2 * math.cos(t2) + K3
            B_ = -2 * math.sin(t2)
            C_ = K1 - (K2 + 1) * math.cos(t2) + K3
            disc = B_ * B_ - 4 * A_ * C_
            cands = [2 * math.atan2(-B_ + s * math.sqrt(disc), 2 * A_) for s in (1, -1)]
            return min(cands, key=lambda x: abs(wrap(x - near)))

        def check(res, rows):
            t = res["t"]
            near = th4_0
            ea = 0.0
            ep = 0.0
            for k, tt in enumerate(t):
                t2 = 2 * math.pi * sweep_turns * tt
                near = th4(t2, near)
                ea = max(ea, abs(wrap(yaw(series(res, "Follower")[k]["rotation"]) - near)))
                ea = max(ea, abs(wrap(yaw(series(res, "Crank")[k]["rotation"]) - t2)))
                # coupler end on the follower pin: positions of pin B computed from both links
                Bc = V(O4.x + c * math.cos(near), O4.y + c * math.sin(near), 15)
                pc = pl_of(series(res, "Coupler")[k])
                Bf = pc.multVec(V(5 + b, 5, 5))
                ep = max(ep, (Bf - Bc).Length)
            rows.add(name, "follower and crank angle (rad)", ea, 1e-9, "Freudenstein, K1=%.4g K2=%.4g K3=%.4g" % (K1, K2, K3))
            rows.add(name, "coupler end position (mm)", ep, 1e-6)
        return Case(name, "kinematics/four-bar", rig, study, check, 1.0 / n, 1.0, "OndselSolver")
    CASES[name] = build
    return build


make_four_bar("four_bar_crank_rocker", 20.0, 60.0, 50.0, 55.0, 0.25)
make_four_bar("four_bar_long_coupler", 25.0, 80.0, 45.0, 70.0, 0.2)


def _gear_rig(doc, mode, host, r1, r2, belt):
    rig = Rig(doc, mode, host)
    A = V(100, 100, 5)
    B = A + V(r1 + r2 if not belt else r1 + r2 + 30, 0, 0)
    ga = rig.cyl("GearA", r1, 10, P(A - V(0, 0, 5), R()))
    gb = rig.cyl("GearB", r2, 10, P(B - V(0, 0, 5), R()))
    h1 = rig.rev("AxisA", rig.G, ga, tuple(A), (0, 0, 1))
    rig.rev("AxisB", rig.G, gb, tuple(B), (0, 0, 1))
    return rig, ga, gb, h1, A, B


def make_gears(name, r1, r2, belt=False):
    def build(doc, mode, host=None):
        rig, ga, gb, h1, A, B = _gear_rig(doc, mode, host, r1, r2, belt)
        typ = "Belt" if belt else "Gears"
        fa, fb = frame_at(tuple(A), (0, 0, 1)), frame_at(tuple(B), (0, 0, 1))
        rig.joint("Mesh", typ, ga, gb, fa, fb, Distance=r1, Distance2=r2)
        rig.finish()
        n = 60
        study = _kin_study(rig, h1, "2*pi*time", 1.0, 1.0 / n)
        ratio = (r1 / r2) if belt else -(r1 / r2)

        def check(res, rows):
            e = 0.0
            for k, tt in enumerate(res["t"]):
                a1 = 2 * math.pi * tt
                g1 = yaw(series(res, "GearA")[k]["rotation"])
                g2 = yaw(series(res, "GearB")[k]["rotation"])
                e = max(e, abs(wrap(g1 - a1)), abs(wrap(g2 - ratio * a1)))
            rows.add(name, "gear angles (rad)", e, 1e-9, "ratio %.6g" % ratio)
        return Case(name, "kinematics/gear" if not belt else "kinematics/belt", rig, study, check, 1.0 / n, 1.0,
                    "OndselSolver")
    CASES[name] = build
    return build


make_gears("gears_r30_r15", 30.0, 15.0)
make_gears("belt_r20_r30", 20.0, 30.0, belt=True)


def make_rack(name, r, turns):
    def build(doc, mode, host=None):
        rig = Rig(doc, mode, host)
        C = V(100, 100, 5)
        pinion = rig.cyl("Pinion", r, 10, P(C - V(0, 0, 5), R()))
        rack = rig.box("Rack", 200, 10, 10, P(V(0, 100 - r - 10, 0), R()))      # lies below the pinion, along x
        h = rig.rev("PinionAxis", rig.G, pinion, tuple(C), (0, 0, 1))
        rig.slide("RackRail", rig.G, rack, (0, 100 - r - 5, 5), (1, 0, 0))
        fa = frame_at((0, 100 - r - 5, 5), (1, 0, 0))
        fb = frame_at(tuple(C), (0, 0, 1))
        rig.joint("Mesh", "RackPinion", rack, pinion, fa, fb, Distance=r)
        rig.finish()
        n = 60
        study = _kin_study(rig, h, "2*pi*%r*time" % turns, 1.0, 1.0 / n)

        def check(res, rows):
            e_pos = e_ang = 0.0
            for k, tt in enumerate(res["t"]):
                th = 2 * math.pi * turns * tt
                got = pl_of(series(res, "Rack")[k]).Base.x - 0.0
                e_ang = max(e_ang, abs(wrap(yaw(series(res, "Pinion")[k]["rotation"]) - th)))
                e_pos = max(e_pos, abs(got - r * th))
            rows.add(name, "rack travel (mm)", e_pos, 1e-6, "travel = r * angle, r=%g" % r)
            rows.add(name, "pinion angle (rad)", e_ang, 1e-9)
        return Case(name, "kinematics/rack-pinion", rig, study, check, 1.0 / n, 1.0, "OndselSolver")
    CASES[name] = build
    return build


make_rack("rack_pinion_r15", 15.0, 0.5)
make_rack("rack_pinion_r25", 25.0, 0.25)


# ------------------------------------------------------------------ running a case

def grid_rows(rows, name, study, res):
    """The output grid is the one the study asks for (a doubled step cannot hide behind its own time axis)."""
    n = int(round(study.TimeEnd / study.TimeStep))
    t = res["t"]
    bad = 0.0 if len(t) == n + 1 and abs((t[1] - t[0]) - study.TimeStep) <= 1e-12 * study.TimeStep and t[0] == 0.0 else 1.0
    rows.add(name, "output grid is k * TimeStep", bad, 0.0, "%d steps of %g s" % (n, study.TimeStep))


def scratch_dir():
    import os
    import tempfile
    return os.path.realpath(tempfile.mkdtemp(prefix="motion_"))


def open_docs(name, mode):
    """(assembly document, part document or None, scratch directory or None) for a mode."""
    import os
    if mode != "xdoc":
        return App.newDocument("Motion_%s_%s" % (name, mode)), None, None
    d = scratch_dir()
    xdoc = App.newDocument("MotionParts")
    xdoc.saveAs(os.path.join(d, "parts.FCStd"))
    doc = App.newDocument("MotionAsm")
    doc.saveAs(os.path.join(d, "asm.FCStd"))
    return doc, xdoc, d


def close_docs(*docs, directory=None):
    import shutil
    for d in docs:
        if d is not None and d.Name in App.listDocuments():
            App.closeDocument(d.Name)
    if directory:
        shutil.rmtree(directory, ignore_errors=True)


def evaluate(name, mode="single", rows=None, keep=False):
    """Build, run and check one case in one mode (``single``, ``link`` or ``xdoc``: links to another document).
    Returns (rows, case, result); closes the documents unless ``keep`` (then ``case.docs`` is set)."""
    rows = rows if rows is not None else Rows()
    doc, xdoc, tmp = open_docs(name, mode)
    try:
        c = CASES[name](doc, "link" if mode == "xdoc" else mode, xdoc)
        c.docs = (doc, xdoc, tmp)
        res = MO.run(c.study)
        c.check(res, rows)
        grid_rows(rows, name, c.study, res)
        return rows, c, res
    finally:
        if not keep:
            close_docs(doc, xdoc, directory=tmp)
