# SPDX-License-Identifier: LGPL-2.1-or-later
"""Motion studies (G06 slice 6a): loads, masses and an integrator for an assembly's joints, a JSON-returning API.

A *motion study* is an ``App::FeaturePython`` object inside the assembly. It holds the time span, the output step
and the integrator settings, and a group of *loads* and *masses* (one object each, all stock property types), and it
stores the result of a run as a time series of every moving part's placement. It sits beside the kinematic
*Simulation* (driven joints, video export), which keeps working unchanged; nothing here touches that object.

Two modes.

- ``Dynamic``: the equations of motion of the assembly's joints under the loads, integrated in joint space. The
  joints must form a tree off a grounded part and be revolute, slider (prismatic) or fixed; anything else (a closed
  loop, a gear, a belt, a rack and pinion, a ball joint, ...) is refused with a ``MotionRefused`` that names the
  joints, nothing is guessed. The generalised coordinates are the joint values (zero at the placements the
  assembly has when the study runs, so solve first), the mass matrix is ``M(q) = sum J^T M_i J`` with the body
  Jacobians of the tree, the velocity-product terms come from a forward pass of the bias accelerations, and the
  loads enter as ``J^T F``. The integrator is ``scipy.integrate.solve_ivp`` with DOP853 at a tight tolerance on a
  fixed output grid ``t_k = k * step``. Everything is SI inside (mm to m, density in kg/m^3, mass in kg).
- ``Kinematic``: one driven joint (``DriveJoint`` with ``DriveType`` and ``DriveFormula`` in radians or mm and
  seconds, exactly as a *Simulation* motion) through the assembly's own kinematic solver (OndselSolver). It handles
  closed loops, gears, belts and rack and pinion. Loads are ignored in this mode. The measurement of that solver
  on the kinematic corpus is in the run note of slice 6a.

Loads. ``Kind`` is ``Gravity`` (``Value`` is the acceleration in m/s^2), ``Force`` (``Value`` in N at ``Point1``, a
point in the part's own frame in mm, in the ``World`` frame or the ``Part`` frame), ``Torque`` (``Value`` in N m),
``Spring`` (``Stiffness`` in N/m and ``FreeLength`` in mm between ``Point1`` on ``Part1`` and ``Point2`` on
``Part2``) and ``Damper`` (``Damping`` in N s/m between the same two points). A spring pulls the two points together
when it is longer than its free length. Masses: a ``MotionMass`` object per moving part, either from the part's
shape and a ``Density`` (kg/m^3: mass = density * volume, inertia about the centre of mass from the shape) or set by
hand (``Mass`` in kg, ``Com`` in mm in the part frame, ``Inertia`` the tensor components Ixx, Iyy, Izz, Ixy, Ixz,
Iyz in kg mm^2 about the centre of mass in the part frame).

A run never writes into a part document: it reads shapes, writes the result string into the study (one undo
transaction together with nothing else), and puts the assembly's placements back to what they were unless the
caller asks to keep one frame. Components that are suppressed (slice 5: out of the group, or a suppressed link array
element) are not part of the model, and a load that reaches one is skipped and listed in the result.

Result. ``to_json(study)`` returns ``{"schema", "study", "mode", "integrator", "t", "parts": [{"name", "entity",
"placements"}], "joints": [{"name", "entity", "type", "q", "qd"}], "energy", "loads": [{"name", "entity", "kind",
"applied"}], ...}`` with F1 entity IDs for parts, joints and loads; the series are lists of plain floats written
with ``repr`` precision, so two runs of the same study are bit-identical and so is a save and reopen.

Limits (slice 6a): contact is slice 6c; no initial velocity except ``Velocities`` (JSON ``{joint name: value}`` in
rad/s or mm/s); components must sit directly in the assembly (not in a sub-assembly); the joint frames must already
be satisfied by the placements (the study refuses otherwise and says which joint); the energy it reports is
kinetic plus gravity plus spring energy (forces, torques and dampers are not conservative and the report says so).

Behaviour sources, all public: rigid-body dynamics in joint space (the Lagrangian form with body Jacobians, see any
multibody-dynamics text), the DOP853 method of Hairer, Norsett and Wanner as shipped in SciPy, and FreeCAD's
documentation for ``App::FeaturePython``, ``App::Link`` and the Assembly workbench; the kinematic mode only calls the
workbench's existing solver.
"""

import contextlib
import json
import math

import FreeCAD as App

import ReplaceComponent as _rc

SCHEMA = "cadfork.assembly.motion/1"
PROP_GROUP = "Motion"
G0 = 9.80665
_FLOAT_ENUM = ("Dynamic", "Kinematic")
_KINDS = ("Gravity", "Force", "Torque", "Spring", "Damper")
_FRAMES = ("World", "Part")
_MODES = ("Density", "Manual")
_DRIVES = ("Angular", "Linear")
_SUPPORTED = ("Fixed", "Revolute", "Slider")
_FRAME_TOL = 1e-4        # mm: how well the joint frames must already be satisfied


class MotionError(Exception):
    """A study cannot be made or run (no mass, bad reference, ...)."""


class MotionRefused(MotionError):
    """The mechanism is outside what the solver supports; ``reasons`` lists why, joint by joint."""

    def __init__(self, reasons):
        self.reasons = list(reasons)
        super().__init__("motion study refused: " + "; ".join(self.reasons))


# ------------------------------------------------------------------ small helpers

def _entity(obj, sub=""):
    return _rc._entity(obj, sub)


@contextlib.contextmanager
def _transaction(doc, title):
    doc.openTransaction(title)
    try:
        yield
    except Exception:
        doc.abortTransaction()
        raise
    doc.commitTransaction()


_NUMPY = []


def _np():
    if not _NUMPY:
        import numpy
        _NUMPY.append(numpy)
    return _NUMPY[0]


def _q(x):
    """A number as the file keeps it. FreeCAD writes properties with 14 significant digits, so a value read back
    after a save differs from the one that was in memory in its last bits; everything the model reads goes through
    here so that a run before the save and a run after the reopen see the same inputs."""
    return float("%.14g" % float(x))


def _vec(v):
    return [float(v.x), float(v.y), float(v.z)]


def _qvec(v):
    return [_q(v.x), _q(v.y), _q(v.z)]


def _mm(x):
    """A length in mm to a picometre: positions that come out of a rotation carry noise far below that (1e-14 for a
    value that is mathematically zero), which a save and reopen would move."""
    return round(float(x), 9) + 0.0


def _mmvec(v):
    return [_mm(v.x), _mm(v.y), _mm(v.z)]


def _pl_np(pl, np):
    """Rotation matrix and base (m) of a placement, from the quantised quaternion and base."""
    # FreeCAD normalises a quaternion when it reads one back, which moves its last bits and leaves noise in the
    # components that are zero; 1e-12 is far below anything a placement means and far above that noise
    x, y, z, w = (round(float(c), 12) + 0.0 for c in pl.Rotation.Q)
    n = math.sqrt(x * x + y * y + z * z + w * w)
    x, y, z, w = x / n, y / n, z / n, w / n
    Rm = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                   [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                   [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    return Rm, np.array(_mmvec(pl.Base)) * 1e-3


# ------------------------------------------------------------------ the objects

class _Proxy:
    """Proxy of a study, load or mass object; no behaviour of its own."""

    @classmethod
    def attach(cls, obj):
        obj.Proxy = cls.__new__(cls)

    def execute(self, obj):
        pass

    def dumps(self):
        return None

    def loads(self, state):
        return None


class MotionStudy(_Proxy):
    def __init__(self, obj):
        obj.addExtension("App::GroupExtensionPython")
        P = obj.addProperty
        P("App::PropertyEnumeration", "Mode", PROP_GROUP, "Dynamic (loads and joints) or Kinematic (one driven joint)")
        obj.Mode = list(_FLOAT_ENUM)
        obj.Mode = "Dynamic"
        P("App::PropertyFloat", "TimeEnd", PROP_GROUP, "End of the time span in seconds (it starts at 0)")
        P("App::PropertyFloat", "TimeStep", PROP_GROUP, "Output step in seconds (the output grid is k * step)")
        P("App::PropertyEnumeration", "Integrator", PROP_GROUP, "Integrator of the dynamic mode")
        obj.Integrator = ["DOP853"]
        P("App::PropertyFloat", "Rtol", PROP_GROUP, "Relative tolerance of the integrator")
        P("App::PropertyFloat", "Atol", PROP_GROUP, "Absolute tolerance of the integrator")
        P("App::PropertyString", "Velocities", PROP_GROUP, "Initial joint velocities (JSON: joint name to rad/s or mm/s)")
        P("App::PropertyString", "DriveJoint", PROP_GROUP, "Kinematic mode: name of the joint that is driven")
        P("App::PropertyEnumeration", "DriveType", PROP_GROUP, "Kinematic mode: angular or linear motion")
        obj.DriveType = list(_DRIVES)
        obj.DriveType = "Angular"
        P("App::PropertyString", "DriveFormula", PROP_GROUP, "Kinematic mode: the motion formula in time (radians or mm)")
        P("App::PropertyFloat", "SolverTolerance", PROP_GROUP, "Kinematic mode: global error tolerance of the solver")
        P("App::PropertyString", "Result", PROP_GROUP, "The result of the last run (JSON)")
        obj.TimeEnd = 1.0
        obj.TimeStep = 0.01
        obj.Rtol = 1e-12
        obj.Atol = 1e-12
        obj.SolverTolerance = 1e-6
        obj.Proxy = self


class MotionLoad(_Proxy):
    def __init__(self, obj):
        P = obj.addProperty
        P("App::PropertyEnumeration", "Kind", PROP_GROUP, "Gravity, Force, Torque, Spring or Damper")
        obj.Kind = list(_KINDS)
        obj.Kind = "Gravity"
        P("App::PropertyString", "Part1", PROP_GROUP, "Name of the part the load acts on (first end of a spring or damper)")
        P("App::PropertyString", "Part2", PROP_GROUP, "Name of the second part of a spring or damper")
        P("App::PropertyVector", "Point1", PROP_GROUP, "Point on Part1 in the part frame, mm")
        P("App::PropertyVector", "Point2", PROP_GROUP, "Point on Part2 in the part frame, mm")
        P("App::PropertyVector", "Value", PROP_GROUP, "Gravity in m/s^2, force in N or torque in N m")
        P("App::PropertyEnumeration", "Frame", PROP_GROUP, "Frame of a force or torque")
        obj.Frame = list(_FRAMES)
        obj.Frame = "World"
        P("App::PropertyFloat", "Stiffness", PROP_GROUP, "Spring stiffness in N/m")
        P("App::PropertyFloat", "Damping", PROP_GROUP, "Damper coefficient in N s/m")
        P("App::PropertyFloat", "FreeLength", PROP_GROUP, "Spring free length in mm")
        obj.Proxy = self


class MotionMass(_Proxy):
    def __init__(self, obj):
        P = obj.addProperty
        P("App::PropertyString", "Component", PROP_GROUP, "Name of the part this mass belongs to")
        P("App::PropertyEnumeration", "Mode", PROP_GROUP, "From the shape and a density, or set by hand")
        obj.Mode = list(_MODES)
        obj.Mode = "Density"
        P("App::PropertyFloat", "Density", PROP_GROUP, "Density in kg/m^3 (Density mode)")
        P("App::PropertyFloat", "Mass", PROP_GROUP, "Mass in kg (Manual mode)")
        P("App::PropertyVector", "Com", PROP_GROUP, "Centre of mass in the part frame, mm (Manual mode)")
        P("App::PropertyFloatList", "Inertia", PROP_GROUP,
          "Ixx, Iyy, Izz, Ixy, Ixz, Iyz in kg mm^2 about the centre of mass, part axes (Manual mode)")
        obj.Inertia = [0.0] * 6
        obj.Proxy = self


_STUDY_PROPS = ("Mode", "TimeEnd", "TimeStep", "Rtol", "Atol", "Result")
_LOAD_PROPS = ("Kind", "Part1", "Stiffness", "Damping", "FreeLength")
_MASS_PROPS = ("Component", "Density", "Com", "Inertia")


def _has(obj, props):
    return obj.TypeId == "App::FeaturePython" and all(p in obj.PropertiesList for p in props)


def is_study(obj):
    return isinstance(getattr(obj, "Proxy", None), MotionStudy) or _has(obj, _STUDY_PROPS)


def is_load(obj):
    return isinstance(getattr(obj, "Proxy", None), MotionLoad) or _has(obj, _LOAD_PROPS)


def is_mass(obj):
    return isinstance(getattr(obj, "Proxy", None), MotionMass) or _has(obj, _MASS_PROPS)


def _reattach(obj):
    if is_study(obj) and not isinstance(getattr(obj, "Proxy", None), MotionStudy):
        MotionStudy.attach(obj)
    elif is_load(obj) and not isinstance(getattr(obj, "Proxy", None), MotionLoad):
        MotionLoad.attach(obj)
    elif is_mass(obj) and not isinstance(getattr(obj, "Proxy", None), MotionMass):
        MotionMass.attach(obj)
    return obj


def studies(assembly):
    """The motion studies of an assembly, in creation order."""
    return [_reattach(o) for o in assembly.Group if is_study(o)]


def loads(study):
    return [_reattach(o) for o in study.Group if is_load(o)]


def masses(study):
    return [_reattach(o) for o in study.Group if is_mass(o)]


def assembly_of(study):
    for o in study.InList:
        if o.isDerivedFrom("Assembly::AssemblyObject"):
            return o
    raise MotionError("the study %s is not inside an assembly" % study.Name)


# ------------------------------------------------------------------ creating and editing

def create_study(assembly, name="MotionStudy", mode="Dynamic", time_end=1.0, step=0.01, **props):
    """A new study in the assembly (one undo transaction)."""
    doc = assembly.Document
    with _transaction(doc, "Create motion study"):
        study = assembly.newObject("App::FeaturePython", name)
        MotionStudy(study)
        study.Mode = mode
        _assign(study, dict(props, TimeEnd=time_end, TimeStep=step))
    doc.recompute()
    return study


def _new_load(study, name, kind, **props):
    doc = study.Document
    with _transaction(doc, "Add motion load"):
        o = study.newObject("App::FeaturePython", name)
        MotionLoad(o)
        o.Kind = kind
        _assign(o, props)
    return o


def _name_of(v):
    """Parts and joints are held by object name, not by App link: a link would put the load in the scope of the
    part, and suppressing the part (slice 5 takes it out of the assembly's group) would take every object
    connected to it through such links out of the group as well."""
    return v.Name if hasattr(v, "Name") else str(v or "")


def _lookup(doc, name):
    return doc.getObject(name) if name else None


def _assign(obj, props):
    for k, v in props.items():
        if k not in obj.PropertiesList:
            raise MotionError("%s has no property %s" % (obj.Name, k))
        if k in ("Part1", "Part2", "Component", "DriveJoint"):
            v = _name_of(v)
        elif k in ("Point1", "Point2", "Value", "Com"):
            v = App.Vector(*_qvec(v if isinstance(v, App.Vector) else App.Vector(*v)))
        elif k in ("Stiffness", "Damping", "FreeLength", "Density", "Mass", "TimeEnd", "TimeStep", "Rtol", "Atol",
                   "SolverTolerance"):
            v = _q(v)
        elif k == "Inertia":
            v = [_q(x) for x in v]
        setattr(obj, k, v)


def add_gravity(study, g=(0.0, 0.0, -G0), name="Gravity"):
    return _new_load(study, name, "Gravity", Value=g)


def add_force(study, part, point, force, frame="World", name="Force"):
    return _new_load(study, name, "Force", Part1=part, Point1=point, Value=force, Frame=frame)


def add_torque(study, part, torque, frame="World", name="Torque"):
    return _new_load(study, name, "Torque", Part1=part, Value=torque, Frame=frame)


def add_spring(study, part1, point1, part2, point2, stiffness, free_length, name="Spring"):
    return _new_load(study, name, "Spring", Part1=part1, Point1=point1, Part2=part2, Point2=point2,
                     Stiffness=stiffness, FreeLength=free_length)


def add_damper(study, part1, point1, part2, point2, damping, name="Damper"):
    return _new_load(study, name, "Damper", Part1=part1, Point1=point1, Part2=part2, Point2=point2, Damping=damping)


def edit_load(load, **props):
    """Change properties of a load (one undo transaction)."""
    with _transaction(load.Document, "Edit motion load"):
        _assign(load, props)
    return load


def remove_load(load):
    with _transaction(load.Document, "Remove motion load"):
        load.Document.removeObject(load.Name)


def set_mass(study, part, density=None, mass=None, com=None, inertia=None, name="Mass"):
    """Give ``part`` a mass: from the shape and ``density`` (kg/m^3), or by hand (``mass`` kg, ``com`` mm,
    ``inertia`` = Ixx, Iyy, Izz, Ixy, Ixz, Iyz in kg mm^2). Replaces the part's existing mass object."""
    if (density is None) == (mass is None):
        raise MotionError("give either a density or a mass")
    doc = study.Document
    with _transaction(doc, "Set motion mass"):
        old = [m for m in masses(study) if m.Component == _name_of(part)]
        o = old[0] if old else None
        if o is None:
            o = study.newObject("App::FeaturePython", name)
            MotionMass(o)
            o.Component = _name_of(part)
        if density is not None:
            _assign(o, {"Mode": "Density", "Density": density})
        else:
            _assign(o, {"Mode": "Manual", "Mass": mass, "Com": com or (0, 0, 0),
                        "Inertia": inertia or [0.0] * 6})
    return o


def remove_mass(mass):
    with _transaction(mass.Document, "Remove motion mass"):
        mass.Document.removeObject(mass.Name)


# ------------------------------------------------------------------ the assembly's parts and joints

def _suppressed_element(obj):
    return obj.TypeId == "App::LinkElement" and bool(getattr(obj, "Suppressed", False))


def live_components(assembly):
    """Components the solver sees: in the group of the assembly (or a container) and not suppressed."""
    import AssemblyConfiguration as ac
    return [o for o in ac._components(assembly) if not _suppressed_element(o)]


def _joints_of(assembly):
    out = []
    for g in assembly.Group:
        if g.TypeId == "Assembly::JointGroup":
            out.extend(g.Group)
    return out


def _is_joint(o):
    return "JointType" in o.PropertiesList and "Reference1" in o.PropertiesList


def _ref_obj(j, prop):
    ref = getattr(j, prop)
    if not ref or ref[0] is None:
        return None
    return ref[0]


# ------------------------------------------------------------------ the model (SI)

class _Joint:
    def __init__(self, obj, kind, a0, o0, parent, child):
        self.obj, self.kind, self.a0, self.o0 = obj, kind, a0, o0
        self.parent, self.child = parent, child
        self.dof = None


class _Body:
    def __init__(self, obj, np):
        self.obj = obj
        self.name = obj.Name
        self.R0, self.base0 = _pl_np(obj.Placement, np)
        self.parent = None
        self.joint = None
        self.chain = []              # joints with a coordinate, root first
        self.mass = 0.0
        self.com0 = None
        self.I0 = None
        self.moving = False
        self.index = 0

    def world0(self, p_local_mm, np):
        return self.R0 @ (np.array(p_local_mm, dtype=float) * 1e-3) + self.base0


def _density_of(mass_obj):
    return _q(mass_obj.Density)


def _shape_inertia(shape):
    """Unit-density inertia of a shape about its centre of mass, part axes, mm^5 (OCCT's MatrixOfInertia)."""
    m = shape.MatrixOfInertia
    return [[m.A11, m.A12, m.A13], [m.A21, m.A22, m.A23], [m.A31, m.A32, m.A33]], shape.CenterOfMass


def _local_shape(obj):
    src = obj.getLinkedObject(True) if obj.TypeId in ("App::Link", "App::LinkElement") else obj
    shp = getattr(src, "Shape", None)
    if shp is None or shp.isNull():
        raise MotionError("%s has no shape to take a mass from" % obj.Name)
    shp = shp.copy()
    shp.Placement = App.Placement()
    return shp


def _mass_of(body, mass_obj, np):
    """mass (kg), COM in the part frame (m) and inertia about the COM in the part frame (kg m^2)."""
    if mass_obj.Mode == "Manual":
        m = _q(mass_obj.Mass)
        c = np.array(_mmvec(mass_obj.Com)) * 1e-3
        i = list(mass_obj.Inertia) + [0.0] * 6
        Ixx, Iyy, Izz, Ixy, Ixz, Iyz = (_q(x) * 1e-6 for x in i[:6])
        return m, c, np.array([[Ixx, Ixy, Ixz], [Ixy, Iyy, Iyz], [Ixz, Iyz, Izz]])
    shp = _local_shape(body.obj)
    vol = float(shp.Volume)
    if vol <= 0:
        raise MotionError("%s has no volume to take a mass from" % body.name)
    inertia, com = _shape_inertia(shp)
    rho = _density_of(mass_obj) * 1e-9          # kg/mm^3
    return (rho * vol, np.array(_vec(com)) * 1e-3, np.array(inertia, dtype=float) * rho * 1e-6)


class Model:
    """The kinematic tree, masses and loads of one study, in SI, with the equations of motion."""

    def __init__(self, study):
        np = _np()
        self.np = np
        self.study = study
        self.asm = assembly_of(study)
        self.bodies = {}
        self.order = []             # reached bodies, parents before children
        self.joints = []            # joints with a coordinate
        self.all_joints = []        # every joint of the tree (with fixed ones)
        self.skipped = []
        self.static = []
        self.refusals = []
        self._build_tree()
        self._masses()
        self._loads()

    # -- tree
    def _body(self, obj):
        b = self.bodies.get(obj.Name)
        if b is None:
            b = _Body(obj, self.np)
            b.index = len(self.bodies)
            self.bodies[obj.Name] = b
        return b

    def _build_tree(self):
        np = self.np
        live = {o.Name: o for o in live_components(self.asm)}
        self.live = live
        grounded = []
        edges = []
        for j in _joints_of(self.asm):
            if "ObjectToGround" in j.PropertiesList and not _is_joint(j):
                o = j.ObjectToGround
                if o is not None and o.Name in live:
                    grounded.append(o)
                continue
            if not _is_joint(j) or getattr(j, "Suppressed", False):
                continue
            a, b = _ref_obj(j, "Reference1"), _ref_obj(j, "Reference2")
            if a is None or b is None or a.Name not in live or b.Name not in live:
                continue
            edges.append((j, a, b))
        if not grounded:
            raise MotionRefused(["no grounded part"])
        adj = {}
        for e in edges:
            adj.setdefault(e[1].Name, []).append(e)
            adj.setdefault(e[2].Name, []).append(e)
        seen = {}
        queue = []
        for g in grounded:
            if g.Name in seen:
                continue
            seen[g.Name] = None
            self._body(g)
            queue.append(g.Name)
        used = set()
        reasons = []
        qi = 0
        while qi < len(queue):
            un = queue[qi]
            qi += 1
            for (j, a, b) in adj.get(un, []):
                if j.Name in used:
                    continue
                other = b if a.Name == un else a
                typ = j.JointType
                if other.Name in seen:
                    reasons.append("closed loop: joint %s closes a loop (%s)" % (j.Name, typ))
                    used.add(j.Name)
                    continue
                used.add(j.Name)
                if typ not in _SUPPORTED:
                    reasons.append("unsupported joint type %s (joint %s)" % (typ, j.Name))
                    seen[other.Name] = un
                    queue.append(other.Name)
                    continue
                seen[other.Name] = un
                parent = self.bodies[un]
                child = self._body(other)
                jt = self._joint(j, typ, parent, child, a, b, reasons)
                child.parent = parent
                child.joint = jt
                self.all_joints.append(jt)
                queue.append(other.Name)
        # helper joints between unreached parts are not part of the tree
        if reasons:
            raise MotionRefused(reasons)
        for name in queue:
            self.order.append(self.bodies[name])
        d = 0
        for b in self.order:
            chain = list(b.parent.chain) if b.parent is not None else []
            if b.joint is not None:
                if b.joint.kind != "Fixed":
                    b.joint.dof = d
                    d += 1
                    self.joints.append(b.joint)
                    chain.append(b.joint)
            b.chain = chain
            b.moving = bool(chain)
        self.n = d
        self.grounded = [self.bodies[g.Name] for g in grounded]
        self.unreached = [o.Name for o in live.values() if o.Name not in self.bodies]

    def _joint(self, j, typ, parent, child, a, b, reasons):
        np = self.np
        # joint frame 1 sits on part a (Reference1), frame 2 on part b
        A = self._body(a)
        B = self._body(b)
        f1, t1 = _pl_np(j.Placement1, np)
        f2, t2 = _pl_np(j.Placement2, np)
        R1 = A.R0 @ f1
        o1 = A.R0 @ t1 + A.base0
        R2 = B.R0 @ f2
        o2 = B.R0 @ t2 + B.base0
        z1, z2 = R1[:, 2], R2[:, 2]
        tol = _FRAME_TOL * 1e-3
        if typ == "Fixed":
            if np.linalg.norm(o1 - o2) > tol or np.abs(R1 - R2).max() > 1e-6:
                reasons.append("joint %s (Fixed) is not satisfied by the current placements (solve first)" % j.Name)
            return _Joint(j, typ, z1, o1, parent, child)
        if np.linalg.norm(_cross(z1, z2)) > 1e-6 or np.dot(z1, z2) < 0:
            reasons.append("joint %s (%s) axes differ in the current placements (solve first)" % (j.Name, typ))
        d = o2 - o1
        if typ == "Revolute":
            if np.linalg.norm(d) > tol:
                reasons.append("joint %s (Revolute) origins differ by %.3g mm (solve first)"
                               % (j.Name, np.linalg.norm(d) * 1e3))
        else:
            if np.linalg.norm(_cross(d, z1)) > tol:
                reasons.append("joint %s (Slider) origins are off the axis by %.3g mm (solve first)"
                               % (j.Name, np.linalg.norm(_cross(d, z1)) * 1e3))
        return _Joint(j, typ, z1 / np.linalg.norm(z1), o1, parent, child)

    # -- masses
    def _masses(self):
        np = self.np
        by = {}
        for m in masses(self.study):
            if not m.Component:
                continue
            by[m.Component] = m
        for b in self.order:
            if not b.moving:
                continue
            m = by.get(b.name)
            if m is None:
                raise MotionError("no mass for the moving part %s (add one with set_mass)" % b.name)
            mass, com, inertia = _mass_of(b, m, np)
            b.mass = float(mass)
            b.com0 = b.R0 @ self._com(com) + b.base0
            b.I0 = b.R0 @ inertia @ b.R0.T
        self.masses = by

    def _com(self, com):
        return com

    # -- loads
    def _loads(self):
        np = self.np
        self.g = np.zeros(3)
        self.forces, self.torques, self.springs, self.dampers = [], [], [], []
        self.applied = []
        for ld in loads(self.study):
            ok, why = self._one_load(ld)
            self.applied.append({"name": ld.Name, "entity": _entity(ld), "kind": ld.Kind, "applied": ok,
                                 "reason": why})
        self.conservative = not (self.forces or self.torques or self.dampers)

    def _body_of(self, obj):
        """The body a load acts on, or None when that part is not in the live assembly (suppressed)."""
        if obj is None:
            return None
        if not _is_live(self, obj):
            return None
        b = self.bodies.get(obj.Name)
        if b is None:
            b = self._body(obj)       # a part no joint reaches: it stays where it is
            b.parent = None
        return b

    def _one_load(self, ld):
        np = self.np
        kind = ld.Kind
        if kind == "Gravity":
            self.g = self.g + _gravity_vector(ld, np)
            return True, ""
        doc = self.asm.Document
        b1 = self._body_of(_lookup(doc, ld.Part1))
        if b1 is None:
            return False, "part %s is not in the live assembly" % (ld.Part1 or "(none)")
        if kind in ("Force", "Torque"):
            val = np.array(_qvec(ld.Value))
            if kind == "Force":
                self.forces.append((b1, b1.world0(_mmvec(ld.Point1), np), val, ld.Frame == "Part"))
            else:
                self.torques.append((b1, val, ld.Frame == "Part"))
            return True, ""
        b2 = self._body_of(_lookup(doc, ld.Part2))
        if b2 is None:
            return False, "part %s is not in the live assembly" % (ld.Part2 or "(none)")
        x1, x2 = b1.world0(_mmvec(ld.Point1), np), b2.world0(_mmvec(ld.Point2), np)
        if kind == "Spring":
            self.springs.append((b1, x1, b2, x2, _q(ld.Stiffness), _free_length(ld) * 1e-3))
        else:
            _route_damper(self, b1, x1, b2, x2, _damping(ld))
        return True, ""

    # -- kinematics
    def kin(self, q):
        """Per body: rotation and translation of the motion since the reference; per joint: current axis, origin."""
        np = self.np
        I3 = np.eye(3)
        Rd, td = {}, {}
        A, O = {}, {}
        for b in self.order:
            p = b.parent
            if p is None:
                Rd[b.name], td[b.name] = I3, np.zeros(3)
                continue
            Rp, tp = Rd[p.name], td[p.name]
            j = b.joint
            a0, o0 = j.a0, j.o0
            if j.kind == "Fixed":
                Rs, st = I3, np.zeros(3)
            elif j.kind == "Revolute":
                Rs = _rot(a0, q[j.dof], np)
                st = o0 - Rs @ o0
            else:
                Rs, st = I3, a0 * q[j.dof]
            Rd[b.name] = Rp @ Rs
            td[b.name] = Rp @ st + tp
            if j.dof is not None:
                A[j.dof] = Rp @ a0
                O[j.dof] = Rp @ o0 + tp
        for b in self.bodies.values():
            if b.name not in Rd:
                Rd[b.name], td[b.name] = I3, np.zeros(3)
        return Rd, td, A, O

    def jac(self, body, x, A, O):
        np = self.np
        Jv = np.zeros((3, self.n))
        Jw = np.zeros((3, self.n))
        for j in body.chain:
            d = j.dof
            a = A[d]
            if j.kind == "Revolute":
                Jw[:, d] = a
                Jv[:, d] = _cross(a, x - O[d])
            else:
                Jv[:, d] = a
        return Jv, Jw

    def dynamics(self, q, qd):
        """Mass matrix, bias force (velocity products) and generalised force of the loads at (q, qd)."""
        np = self.np
        n = self.n
        Rd, td, A, O = self.kin(q)
        M = np.zeros((n, n))
        h = np.zeros(n)
        Q = np.zeros(n)
        st = {}
        zero = np.zeros(3)
        for b in self.order:
            p = b.parent
            if p is None:
                st[b.name] = (zero, zero, zero, zero)
                continue
            w_p, al_p, vc_p, ac_p = st[p.name]
            cp = Rd[p.name] @ p.com0 + td[p.name] if p.moving else zero
            j = b.joint
            if j.dof is not None:
                o = O[j.dof]
                a = A[j.dof]
            else:
                o = cp
                a = zero
            rp = o - cp
            vo = vc_p + _cross(w_p, rp)
            ao = ac_p + _cross(al_p, rp) + _cross(w_p, _cross(w_p, rp))
            if not b.moving:
                st[b.name] = (zero, zero, zero, zero)
                continue
            c = Rd[b.name] @ b.com0 + td[b.name]
            r = c - o
            if j.kind == "Revolute":
                qdj = qd[j.dof]
                w = w_p + a * qdj
                al = al_p + _cross(w_p, a * qdj)
                vc = vo + _cross(w, r)
                ac = ao + _cross(al, r) + _cross(w, _cross(w, r))
            elif j.kind == "Slider":
                qdj = qd[j.dof]
                w, al = w_p, al_p
                vc = vo + a * qdj + _cross(w, r)
                ac = ao + _cross(al, r) + _cross(w, _cross(w, r)) + 2.0 * _cross(w_p, a * qdj)
            else:
                w, al = w_p, al_p
                vc = vo + _cross(w, r)
                ac = ao + _cross(al, r) + _cross(w, _cross(w, r))
            st[b.name] = (w, al, vc, ac)
            Iw = _inertia_world(Rd[b.name], b.I0)
            Jv, Jw = self.jac(b, c, A, O)
            M += b.mass * (Jv.T @ Jv) + Jw.T @ Iw @ Jw
            h += _scale_bias(b.mass * (Jv.T @ ac) + Jw.T @ (Iw @ al + _cross(w, Iw @ w)))
            Q += b.mass * (Jv.T @ self.g)
        # applied loads
        for (b, x0, F, local) in self.forces:
            if not b.moving:
                continue
            x = Rd[b.name] @ x0 + td[b.name]
            Fw = _force_world(Rd[b.name], b.R0, F, local)
            Jv, _ = self.jac(b, x, A, O)
            Q += Jv.T @ Fw
        for (b, T, local) in self.torques:
            if not b.moving:
                continue
            Tw = _force_world(Rd[b.name], b.R0, T, local)
            _, Jw = self.jac(b, zero, A, O)
            Q += Jw.T @ Tw
        if self.springs or self.dampers:
            qdv = qd
            for (b1, x01, b2, x02, k, L0) in self.springs:
                F1, J1, J2 = self._pair(b1, x01, b2, x02, Rd, td, A, O, qdv)
                u, L = F1
                f = _spring_force(k, L, L0)
                Q += J1.T @ (f * u) + J2.T @ (-f * u)
            for (b1, x01, b2, x02, c) in self.dampers:
                F1, J1, J2 = self._pair(b1, x01, b2, x02, Rd, td, A, O, qdv)
                u, L = F1
                vr = float(u @ ((J2 - J1) @ qdv))
                f = _damper_force(c, vr)
                Q += J1.T @ (f * u) + J2.T @ (-f * u)
        return M, h, Q

    def _pair(self, b1, x01, b2, x02, Rd, td, A, O, qd):
        np = self.np
        x1 = Rd[b1.name] @ x01 + td[b1.name]
        x2 = Rd[b2.name] @ x02 + td[b2.name]
        d = x2 - x1
        L = float(np.linalg.norm(d))
        u = d / L if L > 1e-12 else np.zeros(3)
        J1 = self.jac(b1, x1, A, O)[0] if b1.moving else np.zeros((3, self.n))
        J2 = self.jac(b2, x2, A, O)[0] if b2.moving else np.zeros((3, self.n))
        return (u, L), J1, J2

    def energy(self, q, qd):
        np = self.np
        M, h, Q = self.dynamics(q, qd)
        Rd, td, A, O = self.kin(q)
        ke = _kinetic(M, qd, np)
        pe = 0.0
        for b in self.order:
            if b.moving:
                c = Rd[b.name] @ b.com0 + td[b.name]
                pe -= b.mass * float(self.g @ c)
        pes = 0.0
        for (b1, x01, b2, x02, k, L0) in self.springs:
            x1 = Rd[b1.name] @ x01 + td[b1.name]
            x2 = Rd[b2.name] @ x02 + td[b2.name]
            L = float(np.linalg.norm(x2 - x1))
            pes += 0.5 * k * (L - L0) ** 2
        return _energy(ke, pe, pes)

    def rhs(self, t, y):
        np = self.np
        n = self.n
        q, qd = y[:n], y[n:]
        M, h, Q = self.dynamics(q, qd)
        qdd = np.linalg.solve(M, Q - h)
        return np.concatenate([qd, qdd])

    def pose(self, b, Rd, td):
        """Placement (base in mm, rotation matrix) of a body from the motion since the reference."""
        R = Rd[b.name] @ b.R0
        base = Rd[b.name] @ b.base0 + td[b.name]
        return base * 1e3, R


def _is_live(model, obj):
    return obj.Name in model.live


def _spring_force(k, L, L0):
    """Force on the first end along the unit vector to the second end: positive when the spring is stretched."""
    return k * (L - L0)


def _damper_force(c, vr):
    """Force on the first end along the unit vector to the second end; ``vr`` is the rate of lengthening."""
    return c * vr


def _route_damper(model, b1, x1, b2, x2, c):
    model.dampers.append((b1, x1, b2, x2, c))


def _scale_bias(h):
    return h


def _inertia_world(Rd, I0):
    return Rd @ I0 @ Rd.T


def _force_world(Rd, R0, F, local):
    return Rd @ (R0 @ F) if local else F


def _kinetic(M, qd, np):
    return 0.5 * float(qd @ (M @ qd))


def _energy(ke, pe, pes):
    return ke, pe, pes


def _gravity_vector(load, np):
    return np.array(_qvec(load.Value))


def _free_length(load):
    return _mm(load.FreeLength)


def _damping(load):
    return _q(load.Damping)


def _cross(a, b):
    return _np().array([a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]])


def _rot(a, q, np):
    c, s = math.cos(q), math.sin(q)
    K = np.array([[0.0, -a[2], a[1]], [a[2], 0.0, -a[0]], [-a[1], a[0], 0.0]])
    return c * np.eye(3) + s * K + (1.0 - c) * np.outer(a, a)


def _quat(R):
    """Unit quaternion (x, y, z, w) of a rotation matrix, largest component positive."""
    t = R[0, 0] + R[1, 1] + R[2, 2]
    if t > 0:
        s = math.sqrt(t + 1.0) * 2
        w, x, y, z = 0.25 * s, (R[2, 1] - R[1, 2]) / s, (R[0, 2] - R[2, 0]) / s, (R[1, 0] - R[0, 1]) / s
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = math.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2
        w, x, y, z = (R[2, 1] - R[1, 2]) / s, 0.25 * s, (R[0, 1] + R[1, 0]) / s, (R[0, 2] + R[2, 0]) / s
    elif R[1, 1] > R[2, 2]:
        s = math.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2
        w, x, y, z = (R[0, 2] - R[2, 0]) / s, (R[0, 1] + R[1, 0]) / s, 0.25 * s, (R[1, 2] + R[2, 1]) / s
    else:
        s = math.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2
        w, x, y, z = (R[1, 0] - R[0, 1]) / s, (R[0, 2] + R[2, 0]) / s, (R[1, 2] + R[2, 1]) / s, 0.25 * s
    n = math.sqrt(x * x + y * y + z * z + w * w)
    q = [x / n, y / n, z / n, w / n]
    big = max(range(4), key=lambda i: abs(q[i]))
    if q[big] < 0:
        q = [-v for v in q]
    return [float(v) for v in q]


# ------------------------------------------------------------------ running

def _grid(study):
    step = _q(study.TimeStep)
    end = _q(study.TimeEnd)
    if step <= 0 or end <= 0:
        raise MotionError("the time span and the step must be positive")
    n = int(round(end / step))
    return step, [k * step for k in range(n + 1)]


def _save_placements(assembly):
    import AssemblyConfiguration as ac
    return {o.Name: App.Placement(o.Placement) for o in ac._components(assembly)}


def _restore_placements(assembly, saved):
    for name, pl in saved.items():
        o = assembly.Document.getObject(name)
        if o is not None and not o.Placement.isSame(pl, 0.0):
            o.Placement = pl


def _write_series(study, text):
    study.Result = text


def _loads_report(model):
    return model.applied


def _run_dynamic(study):
    np = _np()
    from scipy.integrate import solve_ivp
    model = Model(study)
    step, grid = _grid(study)
    n = model.n
    q0 = np.zeros(n)
    qd0 = np.zeros(n)
    if study.Velocities.strip():
        vel = json.loads(study.Velocities)
        for j in model.joints:
            if j.obj.Name in vel:
                qd0[j.dof] = _q(vel[j.obj.Name]) * (1.0 if j.kind == "Revolute" else 1e-3)
    if n:
        sol = solve_ivp(model.rhs, (0.0, grid[-1]), np.concatenate([q0, qd0]), method=str(study.Integrator),
                        t_eval=np.array(grid), rtol=_q(study.Rtol), atol=_q(study.Atol))
        if not sol.success or sol.y.shape[1] != len(grid):
            raise MotionError("the integrator failed: %s" % sol.message)
        Y = sol.y
    else:
        Y = np.zeros((0, len(grid)))
    parts = [b for b in model.order if b.moving]
    series = {b.name: [] for b in parts}
    qs = [[] for _ in range(n)]
    qds = [[] for _ in range(n)]
    ke_l, pe_l, pes_l = [], [], []
    for k in range(len(grid)):
        q = Y[:n, k]
        qd = Y[n:, k]
        Rd, td, A, O = model.kin(q)
        for b in parts:
            base, R = model.pose(b, Rd, td)
            series[b.name].append({"base": [float(v) for v in base], "rotation": _quat(R)})
        for d in range(n):
            qs[d].append(float(q[d]))
            qds[d].append(float(qd[d]))
        ke, pe, pes = model.energy(q, qd)
        ke_l.append(float(ke))
        pe_l.append(float(pe))
        pes_l.append(float(pes))
    out = {
        "schema": SCHEMA, "study": _entity(study), "mode": "Dynamic", "integrator": str(study.Integrator),
        "rtol": _q(study.Rtol), "atol": _q(study.Atol), "step": step, "t": [float(t) for t in grid],
        "units": {"length": "mm", "time": "s", "angle": "rad", "energy": "J"},
        "parts": [{"name": b.name, "entity": _entity(b.obj), "placements": series[b.name]} for b in parts],
        "joints": [{"name": j.obj.Name, "entity": _entity(j.obj), "type": j.kind, "q": qs[j.dof], "qd": qds[j.dof],
                    "unit": "rad" if j.kind == "Revolute" else "m"} for j in model.joints],
        "energy": {"kinetic": ke_l, "gravity": pe_l, "spring": pes_l,
                   "total": [a + b + c for a, b, c in zip(ke_l, pe_l, pes_l)],
                   "conservative": bool(model.conservative)},
        "loads": _loads_report(model),
        "static_parts": sorted(set(b.name for b in model.bodies.values() if not b.moving)),
        "unreached_parts": sorted(model.unreached),
    }
    return out


def _run_kinematic(study):
    """One driven joint through the assembly's own kinematic solver (OndselSolver)."""
    asm = assembly_of(study)
    joint = _lookup(asm.Document, study.DriveJoint)
    if joint is None:
        raise MotionError("the kinematic study has no DriveJoint")
    step, grid = _grid(study)
    doc = asm.Document
    live = live_components(asm)
    sim = asm.newObject("App::FeaturePython", "MotionSim")
    sim.addExtension("App::GroupExtensionPython")
    for n, t, v in (("aTimeStart", "App::PropertyTime", 0.0), ("bTimeEnd", "App::PropertyTime", _q(study.TimeEnd)),
                    ("cTimeStepOutput", "App::PropertyTime", step),
                    ("fGlobalErrorTolerance", "App::PropertyFloat", _q(study.SolverTolerance))):
        sim.addProperty(t, n, "Simulation", "")
        setattr(sim, n, v)
    mot = sim.newObject("App::FeaturePython", "MotionDrive")
    mot.addProperty("App::PropertyXLinkSubHidden", "Joint", "Motion", "")
    mot.addProperty("App::PropertyString", "Formula", "Motion", "")
    mot.addProperty("App::PropertyEnumeration", "MotionType", "Motion", "")
    mot.MotionType = list(_DRIVES)
    mot.MotionType = str(study.DriveType)
    mot.Joint = (joint, [""])
    mot.Formula = str(study.DriveFormula)
    try:
        doc.recompute()
        rc = asm.generateSimulation(sim)
        if rc != 0:
            raise MotionError("the kinematic solver failed (code %s)" % rc)
        nfr = asm.numberOfFrames()
        first = 1 if nfr == len(grid) + 1 else 0     # the solver repeats the first frame
        if nfr - first != len(grid):
            raise MotionError("the kinematic solver returned %d frames for %d output steps" % (nfr - first, len(grid)))
        moving = [o for o in live]
        series = {o.Name: [] for o in moving}
        for k in range(len(grid)):
            asm.updateForFrame(k + first)
            for o in moving:
                pl = o.Placement
                series[o.Name].append({"base": _vec(pl.Base), "rotation": [float(x) for x in pl.Rotation.Q]})
    finally:
        for o in (mot, sim):
            if doc.getObject(o.Name) is not None:
                doc.removeObject(o.Name)
    return {
        "schema": SCHEMA, "study": _entity(study), "mode": "Kinematic", "integrator": "OndselSolver",
        "rtol": _q(study.SolverTolerance), "atol": _q(study.SolverTolerance), "step": step,
        "t": [float(t) for t in grid],
        "units": {"length": "mm", "time": "s", "angle": "rad"},
        "parts": [{"name": o.Name, "entity": _entity(o), "placements": series[o.Name]} for o in moving],
        "joints": [], "energy": {"kinetic": [], "gravity": [], "spring": [], "total": [], "conservative": False},
        "loads": [], "static_parts": [], "unreached_parts": [],
    }


def run(study, keep_frame=None):
    """Run the study. Returns the result (see ``to_json``). The assembly's placements are put back unless
    ``keep_frame`` (a step index) asks to keep one frame; no part document is touched."""
    asm = assembly_of(study)
    doc = asm.Document
    saved = _save_placements(asm)
    try:
        with _transaction(doc, "Run motion study"):
            res = _run_kinematic(study) if study.Mode == "Kinematic" else _run_dynamic(study)
            _write_series(study, json.dumps(res, sort_keys=True))
    finally:
        _restore_placements(asm, saved)
    if keep_frame is not None:
        apply_frame(study, keep_frame)
    return res


def result(study):
    """The stored result of the last run, or None."""
    text = study.Result
    return json.loads(text) if text else None


def to_json(study):
    """The stored result as a JSON-serialisable dict with F1 entity IDs (parts, joints, loads)."""
    res = result(study)
    if res is None:
        raise MotionError("the study %s has not been run" % study.Name)
    return res


def apply_frame(study, k):
    """Move the assembly's parts to step ``k`` of the stored result."""
    asm = assembly_of(study)
    res = to_json(study)
    doc = asm.Document
    if not 0 <= k < len(res["t"]):
        raise MotionError("no step %s in a result of %d steps" % (k, len(res["t"])))
    with _transaction(doc, "Show motion frame"):
        for p in res["parts"]:
            o = doc.getObject(p["name"])
            if o is None:
                continue
            pl = p["placements"][k]
            o.Placement = App.Placement(App.Vector(*pl["base"]), App.Rotation(*pl["rotation"]))
    return res["t"][k]


def first_collision(study, part_a, part_b, classes=("interfering",), **interference_kwargs):
    """The first output step at which ``part_a`` and ``part_b`` collide in the stored result (slice 1's
    interference check at every step). Returns ``{"step", "time", "class"}`` or None. The assembly is put back."""
    import Interference
    asm = assembly_of(study)
    res = to_json(study)
    doc = asm.Document
    saved = _save_placements(asm)
    keys = {part_a.Name, part_b.Name, _entity(part_a), _entity(part_b), part_a.Label, part_b.Label}
    try:
        for k, t in enumerate(res["t"]):
            for p in res["parts"]:
                o = doc.getObject(p["name"])
                if o is not None:
                    pl = p["placements"][k]
                    o.Placement = App.Placement(App.Vector(*pl["base"]), App.Rotation(*pl["rotation"]))
            rep = Interference.check(asm, **interference_kwargs)
            for pair in rep["pairs"]:
                if pair["class"] not in classes:
                    continue
                ka = {pair["a"]["path"], pair["a"]["entity"], pair["a"]["label"]}
                kb = {pair["b"]["path"], pair["b"]["entity"], pair["b"]["label"]}
                if (ka & keys) and (kb & keys) and ka != kb:
                    return {"step": k, "time": t, "class": pair["class"]}
    finally:
        _restore_placements(asm, saved)
    return None


def signature(doc):
    """Object-and-property signature of a document for the undo and part-document checks (the assembly's
    ``_Part_ShapeCache``, which the core fills lazily, is not part of it)."""
    import AssemblySkeleton as AS
    sig = AS.signature(doc)
    for rec in sig.values():
        rec["props"].pop("_Part_ShapeCache", None)
    return sig
