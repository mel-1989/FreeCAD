# SPDX-License-Identifier: LGPL-2.1-or-later
"""Motion studies (G06 slice 6a): loads, masses and an integrator for an assembly's joints, a JSON-returning API.

A *dynamic analysis* is an ``App::FeaturePython`` object inside the assembly. It holds the time span, the output step
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

Contact and joint stops (slice 6c). A ``Contact`` load makes one pair of parts collide; a pair without one never does.
It carries ``Restitution`` (0 to 1) and ``ContactTolerance`` (mm, default 1e-3). The pair's real shapes are placed at the
current pose and ``distToShape`` gives the gap and the closest points; contact is detected when the gap falls to the
tolerance (a terminal ``solve_ivp`` event on ``gap - tolerance``, which changes sign, where the OCCT distance itself is
clamped at 0 on overlap), so a pair never penetrates and rests at a gap of one tolerance. The contact normal ``u`` is
the unit vector between the closest points (from ``Part1`` to ``Part2``; the outward face normal when the shapes
already touch), ``Jn = u^T (J2 - J1)`` the Jacobian of the gap rate at those points, and Newton's restitution law
becomes a joint-space impulse ``dqd = -(1 + e) (Jn qd) M^-1 Jn^T / (Jn M^-1 Jn^T)`` that is applied where the event
fires. The integration restarts after each impulse, so the output grid stays ``t_k = k * step``. There is no
friction. An enabled limit of a Revolute or Slider joint is a stop on the joint coordinate with the same impulse law
and the study's ``LimitRestitution`` (default 0.5); ``AngleMin/Max`` (degrees) and ``LengthMin/Max`` (mm) are read as
FreeCAD's assembly reads them, the angle of the second joint frame's x axis about the first frame's z axis and the
signed distance along that z axis, so the coordinate ``q = 0`` of the study is mapped to the joint value the pose has
when the study runs; a start outside a limit is refused naming the joint.

Resting and capture. With ``e < 1`` the impacts come ever faster (infinitely many in finite time), so an impact whose
normal speed afterwards is below ``CAPTURE_SPEED`` (0.05 m/s, rad/s for a revolute stop), or that follows the
previous impact of the same pair by less than ``CAPTURE_INTERVAL`` (2 ms), makes the pair a locked unilateral
constraint: the velocity is projected so ``Jn qd = 0`` and the motion is integrated with the constraint held by a
Lagrange multiplier (acceleration level, with Baumgarte stabilisation ``BAUMGARTE`` = 20 per second; the contact point
and normal are held at their values when the constraint was locked). The lock is refused when the multiplier is
tensile in that state, and is released by a terminal event when the multiplier crosses zero; a pair that begins in
contact at t = 0 goes through the same test. The rule leaves the first bounces alone: at the heights the corpus uses the
third post-impact speed is several times the threshold. While a contact is locked its real gap is watched too: the
lock is released, with a ``release`` event marked ``separated``, as soon as the parts' real distance exceeds twice the
tolerance (the body slid off the partner's edge, or left it), whatever the multiplier says. A pair that starts inside
the tolerance band, or is released inside it, while moving away is *unarmed*: it is watched for the turn-around of
its normal speed (captured there, locked) and re-armed when the gap leaves the band, so a rise that stays below the
tolerance never lets the body fall through.

Tunnelling guard. Detection is a sign change of ``gap - tolerance`` between solver steps, so a step that carried a
body clean through a thin partner would be missed. The integration therefore runs in chunks. For each unlocked
contact the allowed relative displacement of one chunk is the larger of half the thinner part's thickness (the shortest
side of the part's own bounding box) and 0.8 of the current gap beyond the tolerance (the pair cannot reach the band
in that distance); a chunk is as long as 1.5 times a speed bound (the largest speed of the eight bounding-box corners
of each part, taken from the Jacobians, at the start and at the end of the chunk) lets that distance be covered. After the
chunk the bound is checked with the end state and, if it was exceeded, the chunk is discarded and redone with half the
length; a chunk that cannot be made shorter than a nanosecond raises ``MotionError``. The result's ``contact.guard``
counts chunks and retries. The bound is not a proof for a body whose speed peaks inside one chunk, a thin part rotated
so that its bounding box is thicker than its true width, or more than two bodies colliding in one chunk. Other limits:
one pair or stop is handled at a time (a second impact projects the velocity back onto the locked constraints, and
simultaneous impacts are taken in turn), the contact point of a face-to-face pair is the first closest-point pair OCCT
reports, and there is no friction. ``energy.conservative`` is false when any contact or stop has ``e < 1``.

Starts that are refused, naming the pair: parts that overlap at the start (their common volume is not zero), a pair
that starts inside the tolerance band without touching (gap above 1e-4 mm and not above the tolerance: the pair would
count as touching from the start), and a contact whose two parts never move.

Limits (slice 6a, contact and stops now in 6c): no initial velocity except ``Velocities`` (JSON ``{joint name: value}`` in
rad/s or mm/s); components must sit directly in the assembly (not in a sub-assembly); the joint frames must already
be satisfied by the placements (the study refuses otherwise and says which joint); the energy it reports is
kinetic plus gravity plus spring energy (forces, torques and dampers are not conservative and the report says so).

Behaviour sources, all public: rigid-body dynamics in joint space (the Lagrangian form with body Jacobians, see any
multibody-dynamics text), the DOP853 method of Hairer, Norsett and Wanner as shipped in SciPy, and FreeCAD's
documentation for ``App::FeaturePython``, ``App::Link`` and the Assembly workbench; the kinematic mode only calls the
workbench's existing solver.
"""

import contextlib
import hashlib
import json
import math

import FreeCAD as App

import ReplaceComponent as _rc

SCHEMA = "cadfork.assembly.motion/1"
PROP_GROUP = "Motion"
G0 = 9.80665
_FLOAT_ENUM = ("Dynamic", "Kinematic")
_KINDS = ("Gravity", "Force", "Torque", "Spring", "Damper", "Contact")    # append only: old files open
_FRAMES = ("World", "Part")
_MODES = ("Density", "Manual")
_DRIVES = ("Angular", "Linear")
_SUPPORTED = ("Fixed", "Revolute", "Slider")
_FRAME_TOL = 1e-4        # mm: how well the joint frames must already be satisfied
CONTACT_TOLERANCE = 1e-3  # mm: default gap at which a contact pair is detected
LIMIT_RESTITUTION = 0.5   # default restitution of a joint stop (study property LimitRestitution)
CAPTURE_SPEED = 0.05      # m/s (rad/s for a revolute stop): a post-impact normal speed below this locks the pair
CAPTURE_INTERVAL = 2e-3   # s: an impact this soon after the previous one of the same pair locks it
BAUMGARTE = 20.0         # 1/s: stabilisation of a locked constraint
_GRAZE = 1e-9            # m/s: a normal speed closer to zero than this is not an approach
_MAX_EVENTS = 20000      # impacts, locks and releases in one run
_MIN_CHUNK = 1e-9        # s: the shortest chunk of the tunnelling guard


class MotionError(Exception):
    """A study cannot be made or run (no mass, bad reference, ...)."""


class MotionRefused(MotionError):
    """The mechanism is outside what the solver supports; ``reasons`` lists why, joint by joint."""

    def __init__(self, reasons):
        self.reasons = list(reasons)
        super().__init__("dynamic analysis refused: " + "; ".join(self.reasons))


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
    return float("%.14g" % float("%.16f" % float(x)))   # floats are written with 16 decimals: a small value (1e-8/3) loses digits


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
        P("App::PropertyFloat", "LimitRestitution", PROP_GROUP,
          "Restitution (0 to 1) of the stops at enabled joint limits of revolute and slider joints")
        obj.LimitRestitution = LIMIT_RESTITUTION
        obj.TimeEnd = 1.0
        obj.TimeStep = 0.01
        obj.Rtol = 1e-12
        obj.Atol = 1e-12
        obj.SolverTolerance = 1e-6
        obj.Proxy = self


class MotionLoad(_Proxy):
    def __init__(self, obj):
        P = obj.addProperty
        P("App::PropertyEnumeration", "Kind", PROP_GROUP, "Gravity, Force, Torque, Spring, Damper or Contact")
        obj.Kind = list(_KINDS)
        obj.Kind = "Gravity"
        P("App::PropertyString", "Part1", PROP_GROUP, "Name of the part the load acts on (first end of a spring or damper)")
        P("App::PropertyString", "Part2", PROP_GROUP, "Name of the second part of a spring, damper or contact")
        P("App::PropertyVector", "Point1", PROP_GROUP, "Point on Part1 in the part frame, mm")
        P("App::PropertyVector", "Point2", PROP_GROUP, "Point on Part2 in the part frame, mm")
        P("App::PropertyVector", "Value", PROP_GROUP, "Gravity in m/s^2, force in N or torque in N m")
        P("App::PropertyEnumeration", "Frame", PROP_GROUP, "Frame of a force or torque")
        obj.Frame = list(_FRAMES)
        obj.Frame = "World"
        P("App::PropertyFloat", "Stiffness", PROP_GROUP, "Spring stiffness in N/m")
        P("App::PropertyFloat", "Damping", PROP_GROUP, "Damper coefficient in N s/m")
        P("App::PropertyFloat", "FreeLength", PROP_GROUP, "Spring free length in mm")
        P("App::PropertyFloat", "Restitution", PROP_GROUP, "Contact: coefficient of restitution, 0 (plastic) to 1 (elastic)")
        P("App::PropertyFloat", "ContactTolerance", PROP_GROUP, "Contact: gap in mm at which the pair is in contact")
        obj.Restitution = 0.5
        obj.ContactTolerance = CONTACT_TOLERANCE
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


def study_of(obj):
    """The study that holds a load or a mass (the study itself for a study), or None."""
    if is_study(obj):
        return obj
    for o in obj.InList:
        if is_study(o):
            return o
    return None


def assembly_of(study):
    for o in study.InList:
        if o.isDerivedFrom("Assembly::AssemblyObject"):
            return o
    raise MotionError("the study %s is not inside an assembly" % study.Name)


# ------------------------------------------------------------------ creating and editing

def create_study(assembly, name="MotionStudy", mode="Dynamic", time_end=1.0, step=0.01, **props):
    """A new study in the assembly (one undo transaction)."""
    doc = assembly.Document
    with _transaction(doc, "Create dynamic analysis"):
        study = assembly.newObject("App::FeaturePython", name)
        MotionStudy(study)
        study.Mode = mode
        _assign(study, dict(props, TimeEnd=time_end, TimeStep=step))
    doc.recompute()
    return study


def _new_load(study, name, kind, **props):
    doc = study.Document
    with _transaction(doc, "Add dynamic analysis load"):
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
                   "SolverTolerance", "Restitution", "ContactTolerance", "LimitRestitution"):
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


def add_contact(study, part1, part2, restitution=0.5, tolerance=CONTACT_TOLERANCE, name="Contact"):
    """Make ``part1`` and ``part2`` collide (one undo transaction). A pair without a Contact load never collides.
    ``restitution`` is Newton's coefficient (0 to 1), ``tolerance`` the gap in mm at which the pair counts as in
    contact. There is no friction."""
    _check_contact_values(name, restitution, tolerance)
    if _name_of(part1) == _name_of(part2):
        raise MotionError("a contact needs two different parts, got %s twice" % _name_of(part1))
    both = grounded_pair(study, part1, part2)
    if both:
        raise MotionError("a contact needs a part that can move, but %s and %s are both grounded" % both)
    return _new_load(study, name, "Contact", Part1=part1, Part2=part2, Restitution=restitution,
                     ContactTolerance=tolerance)


def grounded_pair(study, part1, part2):
    """``(name1, name2)`` when both parts are grounded in the study's assembly (a contact between them can never act),
    else ``None``."""
    asm = assembly_of(study)
    if asm is None:
        return None
    fixed = set()
    for j in _joints_of(asm):
        if "ObjectToGround" in j.PropertiesList and not _is_joint(j) and j.ObjectToGround is not None:
            fixed.add(j.ObjectToGround.Name)
    n1, n2 = _name_of(part1), _name_of(part2)
    return (n1, n2) if n1 in fixed and n2 in fixed else None


def _check_contact_values(what, restitution, tolerance):
    e, tol = float(restitution), float(tolerance)
    if not math.isfinite(e) or not 0.0 <= e <= 1.0:
        raise MotionError("%s: the restitution must be between 0 and 1, got %r" % (what, restitution))
    if not math.isfinite(tol) or tol <= 0.0:
        raise MotionError("%s: the contact tolerance must be a finite positive length in mm, got %r" % (what, tolerance))


def edit_load(load, **props):
    """Change properties of a load (one undo transaction). A contact's restitution and tolerance are checked as in
    ``add_contact``."""
    if load.Kind == "Contact" and ("Restitution" in props or "ContactTolerance" in props):
        _check_contact_values(load.Name, props.get("Restitution", load.Restitution),
                              props.get("ContactTolerance", load.ContactTolerance))
    with _transaction(load.Document, "Edit dynamic analysis load"):
        _assign(load, props)
    return load


def remove_load(load):
    with _transaction(load.Document, "Remove dynamic analysis load"):
        load.Document.removeObject(load.Name)


def set_mass(study, part, density=None, mass=None, com=None, inertia=None, name="Mass"):
    """Give ``part`` a mass: from the shape and ``density`` (kg/m^3), or by hand (``mass`` kg, ``com`` mm,
    ``inertia`` = Ixx, Iyy, Izz, Ixy, Ixz, Iyz in kg mm^2). Replaces the part's existing mass object."""
    if (density is None) == (mass is None):
        raise MotionError("give either a density or a mass")
    what = "the mass of %s" % _name_of(part)
    if density is not None:
        _check_scalar(density, what + " (density)")
    else:
        _check_scalar(mass, what + " (mass)")
        _check_inertia(list(inertia or [0.0] * 6), _np(), what)
    doc = study.Document
    with _transaction(doc, "Set dynamic analysis mass"):
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
    with _transaction(mass.Document, "Remove dynamic analysis mass"):
        mass.Document.removeObject(mass.Name)


# ------------------------------------------------------------------ the assembly's parts and joints

def _suppressed_element(obj):
    return obj.TypeId == "App::LinkElement" and bool(getattr(obj, "Suppressed", False))


def live_components(assembly):
    """Components the solver sees: in the group of the assembly (or a container) and not suppressed."""
    import AssemblyConfiguration as ac
    return [o for o in ac._components(assembly) if not _suppressed_element(o)]


def is_direct(assembly, obj):
    """True when ``obj`` is a component of ``assembly`` itself and not of a sub-assembly (or another container with a
    placement of its own) inside it. The dynamic analysis places loads and masses in the frame of the assembly's own parts;
    a part inside a sub-assembly sits in that sub-assembly's frame, which this solver does not carry."""
    if obj is None:
        return False
    parent = obj.getParentGeoFeatureGroup()
    return parent is None or parent.Name == assembly.Name


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


def _lim_value(j, prop):
    v = getattr(j, prop, 0.0)
    return _q(getattr(v, "Value", v))


def _q_bound(limit, value0, sgn):
    """The bound on the joint coordinate (in the limit's unit) of a limit on the joint value: ``value = value0 + sgn * q``
    and ``q = 0`` is the pose the study starts from."""
    return sgn * (limit - value0)


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


def _check_inertia(i, np, what):
    """Six finite components, positive semi-definite (a point mass has zero inertia)."""
    if len(i) != 6 or not all(math.isfinite(float(x)) for x in i):
        raise MotionError("%s: the inertia needs exactly 6 finite numbers (Ixx, Iyy, Izz, Ixy, Ixz, Iyz), got %r"
                          % (what, list(i)))
    Ixx, Iyy, Izz, Ixy, Ixz, Iyz = (float(x) for x in i)
    M = np.array([[Ixx, Ixy, Ixz], [Ixy, Iyy, Iyz], [Ixz, Iyz, Izz]])
    ev = np.linalg.eigvalsh(M)
    if ev[0] < -1e-9 * max(1.0, abs(ev[-1])):
        raise MotionError("%s: the inertia tensor is not positive semi-definite (eigenvalue %.6g)" % (what, ev[0]))


def _check_scalar(x, what, positive=True):
    x = float(x)
    if not math.isfinite(x) or (positive and x <= 0.0):
        raise MotionError("%s must be a finite positive number, got %r" % (what, x))
    return x


def _inertia_to_world(R0, inertia):
    return R0 @ inertia @ R0.T


def _mass_of(body, mass_obj, np):
    """mass (kg), COM in the part frame (m) and inertia about the COM in the part frame (kg m^2)."""
    if mass_obj.Mode == "Manual":
        what = "the mass of %s" % body.name
        m = _check_scalar(_q(mass_obj.Mass), what + " (Mass)")
        c = np.array(_mmvec(mass_obj.Com)) * 1e-3
        i = list(mass_obj.Inertia)
        _check_inertia(i, np, what)
        Ixx, Iyy, Izz, Ixy, Ixz, Iyz = (_q(x) * 1e-6 for x in i)
        return m, c, np.array([[Ixx, Ixy, Ixz], [Ixy, Iyy, Iyz], [Ixz, Iyz, Izz]])
    _check_scalar(_density_of(mass_obj), "the density of %s" % body.name)
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
        self.unilaterals = []       # contact pairs and joint stops, in a fixed order
        e_lim = _q(getattr(study, "LimitRestitution", LIMIT_RESTITUTION))
        if not math.isfinite(e_lim) or not 0.0 <= e_lim <= 1.0:
            raise MotionError("LimitRestitution must be between 0 and 1, got %r" % e_lim)
        self._build_tree()
        self._masses()
        self._loads()
        for u in self.unilaterals:
            if u.kind == "contact":
                u.check_start(self)

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
                self._stops_of(jt, j, typ, child, b, reasons)
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
        jt = _Joint(j, typ, z1 / np.linalg.norm(z1), o1, parent, child)
        jt.sgn = 1.0 if child.name == b.Name else -1.0       # the coordinate moves Reference2's part (+1) or Reference1's (-1)
        if typ == "Revolute":
            x2 = R1.T @ R2[:, 0]                              # frame 2's x axis in frame 1, as FreeCAD reads the angle
            jt.value0 = math.degrees(math.atan2(x2[1], x2[0]))
        else:
            jt.value0 = float(np.dot(z1 / np.linalg.norm(z1), o2 - o1)) * 1e3     # signed distance along z1, mm
        return jt

    def _stops_of(self, jt, j, typ, child, b, reasons):
        """The stops of the enabled limits of a revolute or slider joint, as events on the joint coordinate."""
        if typ == "Revolute":
            names = ("EnableAngleMin", "EnableAngleMax", "AngleMin", "AngleMax")
            unit, scale = "deg", math.pi / 180.0
        elif typ == "Slider":
            names = ("EnableLengthMin", "EnableLengthMax", "LengthMin", "LengthMax")
            unit, scale = "mm", 1e-3
        else:
            return
        en_lo, en_hi = bool(getattr(j, names[0], False)), bool(getattr(j, names[1], False))
        if not (en_lo or en_hi):
            return
        lo, hi = _lim_value(j, names[2]), _lim_value(j, names[3])
        entered = (lo, hi, en_lo, en_hi)
        swapped = False
        if en_lo and en_hi and lo > hi:                        # FreeCAD swaps a reversed pair
            lo, hi, en_lo, en_hi = hi, lo, en_hi, en_lo
            swapped = True
        e = _q(getattr(self.study, "LimitRestitution", LIMIT_RESTITUTION))
        if not math.isfinite(e) or not 0.0 <= e <= 1.0:
            raise MotionError("LimitRestitution must be between 0 and 1, got %r" % e)
        v0 = jt.value0
        if (en_lo and v0 < lo - 1e-9) or (en_hi and v0 > hi + 1e-9):
            elo, ehi, een_lo, een_hi = entered
            reasons.append("joint %s starts outside its limit (value %.6g %s, limits %s to %s %s as entered%s): move it "
                           "inside the range and solve first"
                           % (j.Name, v0, unit, ("%.6g" % elo) if een_lo else "none", ("%.6g" % ehi) if een_hi else "none",
                              unit, "; FreeCAD reads a reversed pair as swapped" if swapped else ""))
            return
        # value = value0 + sgn * q / scale; a lower limit on the value is a lower bound on q when sgn = +1, an upper one when -1
        sgn = _joint_sign(typ, jt.sgn)
        for enabled, val, side in ((en_lo, lo, "lower"), (en_hi, hi, "upper")):
            if not enabled:
                continue
            qb = _q_bound(val, v0, sgn) * scale
            qside = side if sgn > 0 else ("upper" if side == "lower" else "lower")
            self.unilaterals.append(_Limit(jt, "%s.%s" % (j.Name, side), qside, qb, e))

    # -- masses
    def _masses(self):
        np = self.np
        by = {}
        for m in masses(self.study):
            if not m.Component:
                continue
            self._direct_or_refuse(self.asm.Document.getObject(m.Component), "the mass %s" % m.Name)
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
            b.I0 = _inertia_to_world(b.R0, inertia)
        self.masses = by
        self.warnings = []
        for name in sorted(by):
            if name not in self.bodies:
                obj = self.asm.Document.getObject(name)
                if obj is None:
                    self.warnings.append("the mass object for %s names a part that no longer exists" % name)
                elif name in self.live:
                    raise MotionRefused(["part %s has a mass but no joint chain to a grounded part: it would float "
                                         "in place, so the study is refused" % name])
                else:
                    self.warnings.append("the mass of %s is skipped: the part is suppressed" % name)
        if not any(b.moving for b in self.order):
            gone = [n for n in sorted(by) if self.asm.Document.getObject(n) is None]
            if gone:
                raise MotionRefused(["no part moves: the only mass belongs to a part that no longer exists (%s); "
                                     "remove its mass object or add the part back" % ", ".join(gone)])
            self.warnings.append("no part moves: the mechanism has no free joint")
        for w in self.warnings:
            App.Console.PrintWarning("AssemblyMotion: %s\n" % w)

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
        self.conservative = not (self.forces or self.torques or self.dampers) and all(
            _contact_conservative(u.e) for u in self.unilaterals)

    def _direct_or_refuse(self, obj, what):
        if obj is not None and not is_direct(self.asm, obj):
            raise MotionRefused(["%s names %s, which is inside a sub-assembly; loads and masses can only be put on the "
                                 "parts of the assembly itself" % (what, obj.Name)])

    def _one_contact(self, ld, b1):
        doc = self.asm.Document
        what = "the contact %s" % ld.Name
        _check_contact_values(what, _q(ld.Restitution), _q(ld.ContactTolerance))
        if ld.Part1 == ld.Part2:
            raise MotionError("%s names %s twice" % (what, ld.Part1))
        b2 = self._body_of(_lookup(doc, ld.Part2))
        if b2 is None:
            return False, "part %s is not in the live assembly" % (ld.Part2 or "(none)")
        if not (b1.moving or b2.moving):
            raise MotionRefused(["%s: neither %s nor %s moves (both are grounded or fixed to ground), so the contact can "
                                 "never act: remove it or contact a part that moves" % (what, ld.Part1, ld.Part2)])
        self.unilaterals.append(_Contact(self, ld, b1, b2))
        return True, ""

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
        for nm in (ld.Part1,) + ((ld.Part2,) if kind in ("Spring", "Damper") else ()):
            self._direct_or_refuse(_lookup(doc, nm), "the load %s" % ld.Name)
        b1 = self._body_of(_lookup(doc, ld.Part1))
        if b1 is None:
            return False, "part %s is not in the live assembly" % (ld.Part1 or "(none)")
        if kind == "Contact":
            return self._one_contact(ld, b1)
        if kind in ("Force", "Torque") and not b1.moving:
            return False, "part %s does not move (grounded or fixed to ground), the load has no effect" % ld.Part1
        if kind in ("Spring", "Damper") and not b1.moving:
            b2c = self._body_of(_lookup(doc, ld.Part2))
            if b2c is not None and not b2c.moving:
                return False, "neither %s nor %s moves, the load has no effect" % (ld.Part1, ld.Part2)
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
                ac = ao + _cross(al, r) + _cross(w, _cross(w, r)) + _coriolis(w_p, a, qdj)
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
            Tw = _torque_world(Rd[b.name], b.R0, T, local)
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
        try:
            qdd = np.linalg.solve(M, Q - h)
        except np.linalg.LinAlgError:
            raise MotionError("the mass matrix is singular: a moving part has zero mass or inertia along a free "
                              "joint (give it a positive mass or inertia)")
        return np.concatenate([qd, qdd])

    def pose(self, b, Rd, td):
        """Placement (base in mm, rotation matrix) of a body from the motion since the reference."""
        R = Rd[b.name] @ b.R0
        base = Rd[b.name] @ b.base0 + td[b.name]
        return base * 1e3, R

    # -- unilateral elements (contact pairs and joint stops), slice 6c
    def locked_accel(self, q, qd, locks):
        """Accelerations and Lagrange multipliers with the locked constraints held: ``M qdd = Q - h + Jc^T lam`` and
        ``Jc qdd = -dJc/dt qd - 2 a Jc qd - a^2 (c - c0)`` (``lam`` > 0 pushes the pair apart)."""
        np = self.np
        n, k = self.n, len(locks)
        M, h, Q = self.dynamics(q, qd)
        Jc = np.zeros((k, n))
        rhs2 = np.zeros(k)
        for i, lk in enumerate(locks):
            row, c = lk.el.frozen_row(self, q, lk)
            Jc[i] = row
            jdot = 0.0
            if lk.el.kind == "contact" and np.any(qd):
                eps = 1e-6
                rp = lk.el.frozen_row(self, q + eps * qd, lk)[0]
                rm = lk.el.frozen_row(self, q - eps * qd, lk)[0]
                jdot = float(((rp - rm) / (2 * eps)) @ qd)
            v = float(row @ qd)
            rhs2[i] = -jdot - 2.0 * BAUMGARTE * v - BAUMGARTE ** 2 * (c - lk.c0)
        K = np.zeros((n + k, n + k))
        K[:n, :n] = M
        K[:n, n:] = -Jc.T
        K[n:, :n] = Jc
        try:
            sol = np.linalg.solve(K, np.concatenate([Q - h, rhs2]))
        except np.linalg.LinAlgError:
            raise MotionError("a locked contact or stop is redundant with another constraint (singular system)")
        return sol[:n], sol[n:]

    def rhs_locked(self, locks):
        np = self.np
        n = self.n

        def f(t, y):
            qdd, _lam = self.locked_accel(y[:n], y[n:], locks)
            return np.concatenate([y[n:], qdd])
        return f

    def project(self, q, qd, locks):
        """``qd`` with the normal velocities of the locked constraints removed (mass-weighted projection)."""
        if not locks:
            return qd
        np = self.np
        M = self.dynamics(q, qd)[0]
        Jc = np.array([lk.el.frozen_row(self, q, lk)[0] for lk in locks])
        MiJ = np.linalg.solve(M, Jc.T)
        return qd - MiJ @ np.linalg.solve(Jc @ MiJ, Jc @ qd)


class _Lock:
    """A pair or stop held as a locked unilateral constraint: ``c0`` is the gap function when it was locked and ``data``
    what the element froze then."""

    def __init__(self, el, c0, data):
        self.el, self.c0, self.data = el, c0, data


class _Limit:
    """A stop on a joint coordinate: the gap is the distance of the coordinate from its bound, on the allowed side."""
    kind = "limit"
    tol = 0.0

    def __init__(self, joint, name, side, bound, e):
        self.joint, self.name, self.side, self.bound, self.e = joint, name, side, bound, e
        self.s = _stop_side(side)

    def probe(self, model, q):
        row = model.np.zeros(model.n)
        row[self.joint.dof] = self.s
        return self.s * (q[self.joint.dof] - self.bound), row, None

    def frozen_row(self, model, q, lk):
        gap, row, _ = self.probe(model, q)
        return row, gap

    def describe(self):
        return {"name": self.name, "kind": "limit", "restitution": self.e, "tolerance": 0.0, "bound": self.bound,
                "side": self.side, "joint": self.joint.obj.Name}


class _Contact:
    """Contact of two parts' real shapes: the gap and closest points come from OCCT's ``distToShape`` at the pose."""
    kind = "contact"

    def __init__(self, model, load, b1, b2):
        self.b1, self.b2 = b1, b2
        self.name = load.Name
        self.e = _q(load.Restitution)
        self.tol = _q(load.ContactTolerance)
        self.load = load.Name
        self.sh1, self.sh2 = _local_shape(b1.obj), _local_shape(b2.obj)
        np = model.np
        # the guard's geometry: the thinner part's thickness (shortest bounding-box side, mm to m, never below four
        # tolerances) and the eight bounding-box corners of each part (m, part frame)
        thick = min(min(s.BoundBox.XLength, s.BoundBox.YLength, s.BoundBox.ZLength) for s in (self.sh1, self.sh2))
        self.t_min = max(thick, 4.0 * self.tol) * 1e-3
        self.corners = []
        for s in (self.sh1, self.sh2):
            bb = s.BoundBox
            self.corners.append([np.array([x, y, z]) * 1e-3 for x in (bb.XMin, bb.XMax)
                                 for y in (bb.YMin, bb.YMax) for z in (bb.ZMin, bb.ZMax)])

    def speed(self, model, q, v):
        """Bound of the speed (m/s) of any point of the two parts, relative to the ground, for the joint rates ``v``:
        the largest over the eight bounding-box corners of each part, summed over the two parts."""
        np = model.np
        Rd, td, A, O = model.kin(q)
        tot = 0.0
        for b, cs in ((self.b1, self.corners[0]), (self.b2, self.corners[1])):
            if not b.chain:
                continue
            base, R = model.pose(b, Rd, td)
            base = base * 1e-3
            tot += max(float(np.linalg.norm(model.jac(b, R @ c + base, A, O)[0] @ v)) for c in cs)
        return tot

    def check_start(self, model):
        """Refuse a start that is overlapping, or inside the tolerance band without touching."""
        np = model.np
        q = np.zeros(model.n)
        gap, _row, _data = self.probe(model, q)
        if gap <= 1e-4:
            try:
                common = self.sh1.common(self.sh2)
                vol = float(common.Volume) if not common.isNull() else 0.0
            except Exception as exc:                        # noqa: BLE001 - the boolean can fail on odd shapes
                model.warnings.append("the contact %s: the overlap test at the start failed (%s)" % (self.name, exc))
                return
            small = min(abs(self.sh1.Volume), abs(self.sh2.Volume))
            if vol > _overlap_limit(small):
                raise MotionRefused(["contact %s: the parts %s and %s overlap at the start (common volume %.4g mm^3); "
                                     "move them apart and solve first" % (self.name, self.b1.name, self.b2.name, vol)])
        elif gap <= self.tol * (1.0 + 1e-9):
            raise MotionRefused(["contact %s: the parts %s and %s start %.6g mm apart, inside the contact tolerance of "
                                 "%.6g mm, so they would count as touching from the start: move them apart by more than "
                                 "the tolerance, put them in contact, or lower the tolerance"
                                 % (self.name, self.b1.name, self.b2.name, gap, self.tol)])

    def _place(self, model, q):
        Rd, td, A, O = model.kin(q)
        for b, sh in ((self.b1, self.sh1), (self.b2, self.sh2)):
            base, R = model.pose(b, Rd, td)
            sh.Placement = App.Placement(App.Vector(*(float(v) for v in base)), App.Rotation(*_quat(R)))
        return Rd, td, A, O

    def probe(self, model, q):
        np = model.np
        Rd, td, A, O = self._place(model, q)
        dist, pts, info = self.sh1.distToShape(self.sh2)
        cands = []
        for i, (p1, p2) in enumerate(pts if dist <= 1e-4 else pts[:1]):
            x1 = np.array([p1.x, p1.y, p1.z]) * 1e-3
            x2 = np.array([p2.x, p2.y, p2.z]) * 1e-3
            us = [(x2 - x1) / np.linalg.norm(x2 - x1)] if dist > 1e-4 else self._touching_normals(np, info[i])
            J1 = model.jac(self.b1, x1, A, O)[0]
            J2 = model.jac(self.b2, x2, A, O)[0]
            for u in us:
                cands.append((u @ (J2 - J1), u, x1, x2))
        # touching shapes have no direction between their closest points: of the candidate face normals take the one
        # along which the bodies can move at all (the largest gap-rate row), the first on a tie
        row, u, x1, x2 = max(cands, key=lambda c: float(np.linalg.norm(c[0]) + 0.0)) if len(cands) > 1 else cands[0]
        data = {"u": u, "x01": Rd[self.b1.name].T @ (x1 - td[self.b1.name]),
                "x02": Rd[self.b2.name].T @ (x2 - td[self.b2.name])}
        return float(dist), row, data

    def _touching_normals(self, np, inf):
        """Unit normals from part 1 to part 2 for one pair of support elements of touching shapes: the outward normal of a
        face of one of them, else the direction between the centres of mass."""
        out = []
        try:
            t1, i1, a1, t2, i2, a2 = inf
            if t1 == "Face":
                n = self.sh1.Faces[i1].normalAt(*a1)
                out.append(np.array([n.x, n.y, n.z]))
            if t2 == "Face":
                n = self.sh2.Faces[i2].normalAt(*a2)
                out.append(-np.array([n.x, n.y, n.z]))
        except Exception:                                   # noqa: BLE001 - fall back to the centres
            out = []
        if not out:
            n = self.sh2.CenterOfMass - self.sh1.CenterOfMass
            out.append(np.array([n.x, n.y, n.z]))
        return [n / np.linalg.norm(n) for n in out]

    def frozen_row(self, model, q, lk):
        np = model.np
        d = lk.data
        Rd, td, A, O = model.kin(q)
        x1 = Rd[self.b1.name] @ d["x01"] + td[self.b1.name]
        x2 = Rd[self.b2.name] @ d["x02"] + td[self.b2.name]
        J1 = model.jac(self.b1, x1, A, O)[0]
        J2 = model.jac(self.b2, x2, A, O)[0]
        return d["u"] @ (J2 - J1), float(d["u"] @ (x2 - x1))

    def describe(self):
        return {"name": self.name, "kind": "contact", "restitution": self.e, "tolerance": self.tol,
                "parts": [self.b1.name, self.b2.name]}


# the hooks below are single points the mutant matrix of the tests swaps out
def _event_value(gap, tol):
    return gap - tol


def _ratio(e):
    return e


def _impulse_sign():
    return -1.0


def _should_capture(vn_after, since_last):
    return vn_after < CAPTURE_SPEED or since_last < CAPTURE_INTERVAL


def _guard_enabled():
    return True


def _allow(el, gap):
    """Relative displacement (m) of the pair that one guard chunk may cover."""
    return max(0.5 * el.t_min, 0.8 * (gap - el.tol) * 1e-3)


def _plan_accel(a):
    return a


def _plan_chunk(v0, a, allow):
    """Longest chunk (s) over which 1.5 (v0 + a h) h stays within ``allow``: speed bound ``v0`` (m/s) and the bound ``a`` (m/s^2)
    of its rate of change."""
    if a > 1e-12:
        return (-1.5 * v0 + math.sqrt(2.25 * v0 * v0 + 6.0 * a * allow)) / (3.0 * a)
    if v0 > 1e-12:
        return allow / (1.5 * v0)
    return math.inf


def _chunk_too_long(dt, v0, v1, allow):
    return 1.5 * dt * max(v0, v1) > allow * (1.0 + 1e-9)


def _unarmed_in_band():
    return True


def _joint_sign(typ, sgn):
    return sgn


def _overlap_limit(small):
    """Common volume (mm^3) above which two parts count as overlapping."""
    return max(1e-9 * small, 1e-12)


def _separation_value(gap, tol):
    """Positive while a locked contact is still in contact, zero when the real gap reaches twice the tolerance."""
    return 2.0 * tol - gap


def _lock_ok(lam):
    return lam > 0.0


def _release_value(lam):
    return lam


def _contact_conservative(e):
    return e >= 1.0


def _stop_side(side):
    return 1.0 if side == "lower" else -1.0


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


def _coriolis(w_p, a, qdj):
    return 2.0 * _cross(w_p, a * qdj)


def _torque_world(Rd, R0, T, local):
    return Rd @ (R0 @ T) if local else T


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

def _grid(study, end=None, step=None):
    """The output grid. ``end`` and ``step`` override the study's own values (the GUI panel runs on edits it has not
    applied yet); None reads the study."""
    step = _q(study.TimeStep if step is None else step)
    end = _q(study.TimeEnd if end is None else end)
    if not (math.isfinite(step) and math.isfinite(end)) or step <= 0 or end <= 0:
        raise MotionError("the time span and the step must be finite and positive (TimeEnd=%r, TimeStep=%r)"
                          % (end, step))
    ratio = end / step
    n = int(round(ratio))
    if n < 1 or abs(ratio - n) > 1e-9 * max(1.0, ratio):
        raise MotionError("the time span %g s is not a whole number of steps of %g s (%.12g steps); the output grid "
                          "is k * step and the end point is not silently dropped" % (end, step, ratio))
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


def _bad_constant(name):
    raise MotionError("%s is not a number" % name)


class _Sim:
    """Event-driven integration of a model with contacts and stops: segments of ``solve_ivp`` ended by terminal events
    (a gap closing, a multiplier going tensile), an impulse or a lock or release at each, and a restart. The output
    columns are those of the grid ``t_k = k * step`` whatever the events do in between."""

    def __init__(self, model, grid, step, method, rtol, atol):
        self.m, self.grid, self.step, self.method, self.rtol, self.atol = model, grid, step, method, rtol, atol
        self.locks = []
        self.events = []
        self.last = {}
        self.armed = {}
        self.chunks = 0
        self.retries = 0

    def note(self, t, el, what, **kw):
        rec = {"t": float(t), "element": el.name, "what": what}
        rec.update({k: float(v) for k, v in kw.items()})
        self.events.append(rec)

    def locked(self, el):
        return any(lk.el is el for lk in self.locks)

    def _try_lock(self, el, q, qd, data, t, why):
        """Lock ``el`` when the multiplier is compressive in the projected state; returns the new velocity or None."""
        np = self.m.np
        lk = _Lock(el, 0.0, data)
        lk.c0 = el.frozen_row(self.m, q, lk)[1]
        trial = self.locks + [lk]
        qd_p = self.m.project(q, qd, trial)
        _qdd, lam = self.m.locked_accel(q, qd_p, trial)
        if not all(_lock_ok(float(x)) for x in lam):
            return None
        self.locks.append(lk)
        self.note(t, el, "capture", reason_start=1.0 if why == "start" else 0.0, lam=lam[-1])
        return qd_p

    def start(self, y):
        """Pairs and stops already in contact at t = 0: an approach is an impact, a rest is a lock."""
        m = self.m
        n = m.n
        q, qd = y[:n].copy(), y[n:].copy()
        for el in m.unilaterals:
            qd = self._settle(el, 0.0, q, qd, "start")
        return np_concat(m.np, q, qd)

    def _settle(self, el, t, q, qd, mode):
        """What a pair is doing at a restart. Outside its band it is armed (a gap event is enough). Inside it (at the
        start, or just released) a closing pair is an impact, a resting one a lock (``mode == "start"``), and one that is
        moving away is unarmed: its gap event could never change sign if it stays inside the band, so it is watched
        for the turn-around of its normal speed instead."""
        m = self.m
        gap, row, data = el.probe(m, q)
        if _event_value(gap, el.tol) > 0.0:
            self.armed[el.name] = True
            return qd
        if mode == "start":
            vn = float(row @ qd)
            if vn < -_GRAZE:
                qd = self.impact(el, t, q, qd, gap, row, data)
            elif vn <= _GRAZE:
                new = self._try_lock(el, q, qd, data, t, "start")
                if new is not None:
                    qd = new
        if el.kind == "contact" and not self.locked(el) and _unarmed_in_band():
            self.armed[el.name] = False
        return qd

    def impact(self, el, t, q, qd, gap, row, data):
        m = self.m
        np = m.np
        vn = float(row @ qd)
        if vn >= -_GRAZE:
            return qd                                   # grazing or separating: nothing to resolve
        M = m.dynamics(q, qd)[0]
        Mj = np.linalg.solve(M, row)
        den = float(row @ Mj)
        if den <= 0.0:
            return qd
        qd_new = qd + _impulse_sign() * (1.0 + _ratio(el.e)) * vn * Mj / den
        qd_new = m.project(q, qd_new, self.locks)
        vn_after = float(row @ qd_new)
        since = t - self.last.get(el.name, -math.inf)
        self.last[el.name] = t
        self.note(t, el, "impact", vn_before=vn, vn_after=vn_after, gap=gap)
        if _should_capture(vn_after, since):
            new = self._try_lock(el, q, qd, data, t, "impact")
            if new is not None:
                return new
        return qd_new

    def run(self):
        m = self.m
        np = m.np
        from scipy.integrate import solve_ivp
        n = m.n
        grid = np.array(self.grid)
        cols = [None] * len(grid)
        y = self.start(np.concatenate([np.zeros(n), self._qd0]))
        cols[0] = y.copy()
        k0, t_cur, fired_n = 1, 0.0, 0
        while k0 < len(grid):
            evs, owners = self._events()
            rhs = m.rhs_locked(list(self.locks)) if self.locks else m.rhs
            guards = [el for el in m.unilaterals if el.kind == "contact" and not self.locked(el)] \
                if _guard_enabled() else []
            plan = self._plan(guards, t_cur, y, rhs) if guards else None
            f = 1.0
            while True:
                t_end = grid[-1] if plan is None else min(grid[-1], t_cur + max(plan[0] * f, _MIN_CHUNK))
                sel = grid[k0:]
                sel = sel[sel <= t_end]
                extra = len(sel) == 0 or sel[-1] < t_end
                t_eval = np.append(sel, t_end) if extra else sel
                sol = solve_ivp(rhs, (t_cur, t_end), y, method=self.method, t_eval=t_eval, events=evs,
                                rtol=self.rtol, atol=self.atol, max_step=self.step)
                if sol.status < 0:
                    raise MotionError("the integrator failed: %s" % sol.message)
                ys = np.asarray(sol.y)
                fired = [(float(te[0]), i) for i, te in enumerate(sol.t_events) if len(te)]
                if fired:
                    te, i = min(fired)
                    ye = np.asarray(sol.y_events[i][0], dtype=float)
                    t_stop, y_stop = te, ye
                else:
                    t_stop, y_stop = t_end, ys[:, -1]
                if plan is not None and not self._chunk_ok(plan, t_cur, t_stop, y_stop):
                    f *= 0.5
                    self.retries += 1
                    if plan[0] * f < _MIN_CHUNK:
                        raise MotionError("the contact guard cannot keep a body from crossing its partner unseen: the "
                                          "motion is too fast for the thickness of the parts at t = %.6g s" % t_cur)
                    continue
                break
            self.chunks += 1
            got = min(len(sol.t), len(sel))
            for j in range(got):
                cols[k0 + j] = ys[:, j].copy()
            k0 += got
            if not fired:
                y, t_cur = ys[:, -1].copy(), t_end
                continue
            fired_n += 1
            if fired_n > _MAX_EVENTS:
                raise MotionError("more than %d contact events in one run: the pair is chattering" % _MAX_EVENTS)
            y = self._resolve(owners[i][0], owners[i][1], te, ye)
            t_cur = te
        if k0 < len(grid) or any(c is None for c in cols):
            raise MotionError("the integrator did not reach the end of the time span (%d of %d steps)" % (k0, len(grid)))
        return np.array(cols).T

    def _events(self):
        evs, owners = [], []

        def add(fn, direction, el, what):
            fn.terminal, fn.direction = True, direction
            evs.append(fn)
            owners.append((el, what))
        for el in self.m.unilaterals:
            lk = next((x for x in self.locks if x.el is el), None)
            if lk is not None:
                add(self._release_event(lk), -1, el, "release")
                if el.kind == "contact":
                    add(self._separation_event(el), -1, el, "sep")
            elif el.kind == "contact" and not self.armed.get(el.name, True):
                add(self._turn_event(el), -1, el, "turn")
                add(self._gap_event(el), 1, el, "rearm")
            else:
                add(self._gap_event(el), -1, el, "gap")
        return evs, owners

    def _plan(self, guards, t, y, rhs):
        """Chunk length and, per unlocked contact, the speed bound and the relative displacement one chunk may cover."""
        m = self.m
        n = m.n
        q, qd = y[:n], y[n:]
        qdd = rhs(t, y)[n:]
        h_min, items = max(self.grid[-1] - t, self.step), []
        for el in guards:
            gap = el.probe(m, q)[0]
            allow = _allow(el, gap)
            v0 = el.speed(m, q, qd)
            a = _plan_accel(el.speed(m, q, qdd))
            items.append((el, v0, allow))
            h_min = min(h_min, _plan_chunk(v0, a, allow))
        return h_min, items

    def _chunk_ok(self, plan, t0, t1, y1):
        """The chunk from ``t0`` to ``t1`` ended in ``y1``: no body can have covered more than the allowed distance."""
        dt = t1 - t0
        if dt <= _MIN_CHUNK:
            return True
        m = self.m
        for el, v0, allow in plan[1]:
            v1 = el.speed(m, y1[:m.n], y1[m.n:])
            if _chunk_too_long(dt, v0, v1, allow):
                return False
        return True

    def _gap_event(self, el):
        m = self.m

        def f(t, y):
            return _event_value(el.probe(m, y[:m.n])[0], el.tol)
        return f

    def _turn_event(self, el):
        m = self.m

        def f(t, y):
            return float(el.probe(m, y[:m.n])[1] @ y[m.n:])
        return f

    def _separation_event(self, el):
        m = self.m

        def f(t, y):
            return _separation_value(el.probe(m, y[:m.n])[0], el.tol)
        return f

    def _release_event(self, lk):
        m = self.m
        idx = next(i for i, x in enumerate(self.locks) if x is lk)
        locks = list(self.locks)

        def f(t, y):
            return _release_value(float(m.locked_accel(y[:m.n], y[m.n:], locks)[1][idx]))
        return f

    def _resolve(self, el, what, t, y):
        m = self.m
        n = m.n
        q, qd = y[:n].copy(), y[n:].copy()
        if what in ("release", "sep"):
            lk = next(x for x in self.locks if x.el is el)
            self.locks.remove(lk)
            if what == "sep":
                self.note(t, el, "release", separated=1.0)
            else:
                self.note(t, el, "release")
            if el.kind == "contact":
                qd = self._settle(el, t, q, qd, "release")
            return np_concat(m.np, q, qd)
        if what == "rearm":
            self.armed[el.name] = True
            return y
        gap, row, data = el.probe(m, q)
        if what == "turn":
            new = self._try_lock(el, q, qd, data, t, "turn")
            return y if new is None else np_concat(m.np, q, new)
        qd = self.impact(el, t, q, qd, gap, row, data)
        return np_concat(m.np, q, qd)


def np_concat(np, q, qd):
    return np.concatenate([q, qd])


def _run_dynamic(study, end=None, step=None):
    np = _np()
    try:
        from scipy.integrate import solve_ivp
    except ImportError:
        raise MotionError("the dynamic mode needs SciPy (scipy.integrate), which this FreeCAD does not provide")
    model = Model(study)
    step, grid = _grid(study, end, step)
    n = model.n
    q0 = np.zeros(n)
    qd0 = np.zeros(n)
    if study.Velocities.strip():
        try:
            vel = json.loads(study.Velocities, parse_constant=_bad_constant)
        except (ValueError, MotionError) as exc:
            raise MotionError("Velocities is not valid JSON of finite numbers: %s" % exc)
        if not isinstance(vel, dict):
            raise MotionError("Velocities must be a JSON object {joint name: value}")
        by_name = {j.obj.Name: j for j in model.joints}
        for key, val in vel.items():
            if key not in by_name:
                raise MotionError("Velocities names %r, which is not a free joint of the mechanism (free joints: %s)"
                                  % (key, ", ".join(sorted(by_name)) or "none"))
            if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val):
                raise MotionError("Velocities[%r] must be a finite number, got %r" % (key, val))
            j = by_name[key]
            qd0[j.dof] = _q(val) * (1.0 if j.kind == "Revolute" else 1e-3)
    sim = None
    if n and model.unilaterals:
        sim = _Sim(model, grid, step, str(study.Integrator), _q(study.Rtol), _q(study.Atol))
        sim._qd0 = qd0
        Y = sim.run()
    elif n:
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
        "warnings": list(model.warnings),
    }
    if sim is not None:
        out["contact"] = {"events": sim.events, "elements": [u.describe() for u in model.unilaterals],
                          "capture_speed": CAPTURE_SPEED, "capture_interval": CAPTURE_INTERVAL,
                          "guard": {"chunks": sim.chunks, "retries": sim.retries}}
    return out


def _run_kinematic(study, end=None, step=None):
    """One driven joint through the assembly's own kinematic solver (OndselSolver)."""
    asm = assembly_of(study)
    joint = _lookup(asm.Document, study.DriveJoint)
    if joint is None:
        raise MotionError("the kinematic study has no DriveJoint")
    step, grid = _grid(study, end, step)
    doc = asm.Document
    live = live_components(asm)
    sim = asm.newObject("App::FeaturePython", "MotionSim")
    sim.addExtension("App::GroupExtensionPython")
    for n, t, v in (("aTimeStart", "App::PropertyTime", 0.0), ("bTimeEnd", "App::PropertyTime", _q(study.TimeEnd if end is None else end)),
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
        "loads": [], "static_parts": [], "unreached_parts": [], "warnings": [],
    }


_FP_STUDY = ("Mode", "Integrator", "Rtol", "Atol", "Velocities", "DriveJoint", "DriveType", "DriveFormula",
             "SolverTolerance")
_FP_LOAD = ("Kind", "Part1", "Part2", "Point1", "Point2", "Value", "Frame", "Stiffness", "Damping", "FreeLength")
_FP_MASS = ("Component", "Mode", "Density", "Mass", "Com", "Inertia")


_FP_JOINT_SKIP = ("Label", "Label2", "Visibility", "Proxy", "ExpressionEngine", "Shape", "Placement")


def _fp_value(v, snap=False):
    """A property value as the fingerprint keeps it: floats at the 14 digits a file keeps (as every input of a run is
    read), vectors, placements and lists of them likewise, links by object name and sub-element names."""
    if isinstance(v, float):
        return _q(v)
    if isinstance(v, App.Vector):
        return _qvec(v)
    if isinstance(v, App.Units.Quantity):
        return [_q(v.Value), str(v.Unit)]
    if isinstance(v, App.Placement):
        # FreeCAD normalises a quaternion when it reads one back, which turns an exact 0 into 1e-16; ``snap`` (studies
        # with a contact or a stop, from slice 6c on) drops those, earlier studies keep the hash they were stored with
        return [_qvec(v.Base)] + [_q(x) if not snap or abs(x) > 1e-12 else 0.0 for x in v.Rotation.Q]
    if hasattr(v, "Name") and hasattr(v, "TypeId"):
        return v.Name
    if isinstance(v, (list, tuple)):
        return [_fp_value(x, snap) for x in v]
    if v is None or isinstance(v, (bool, int, str)):
        return v
    return repr(v)


def _digits(x, n=9):
    """``x`` to ``n`` significant digits: values derived from a shape are recomputed from the stored BREP after a reopen
    and differ from the in-memory ones in their last bits, which 14 digits would not absorb."""
    return float("%.*g" % (n, float(x)))


def _shape_digest(obj):
    """Volume, centre of mass and inertia of a part's shape, the last two scaled by the part's own size and rounded to 8
    digits: a product of inertia that is mathematically zero comes out of OCCT as noise that a reopened file moves."""
    try:
        shp = _local_shape(obj)
        m, com, vol = shp.MatrixOfInertia, shp.CenterOfMass, float(shp.Volume)
        size = abs(vol) ** (1.0 / 3.0) or 1.0
        norm = lambda x, k: round(float(x) / k, 8) + 0.0                      # noqa: E731
        return [_digits(vol)] + [norm(c, size) for c in (com.x, com.y, com.z)] + [
            norm(x, max(abs(vol), 1e-300) * size * size) for x in (m.A11, m.A22, m.A33, m.A12, m.A13, m.A23)]
    except Exception:                           # noqa: BLE001 - a part with no usable shape is the same each time
        return None


def _placement_digest(pl):
    """A placement at a nanometre and a nano-radian: the solver moves a part by noise far below that on each recompute."""
    return [round(float(x), 6) + 0.0 for x in pl.Base] + [round(float(x), 9) + 0.0 for x in pl.Rotation.Q]


def inputs_fingerprint(study):
    """A hash of everything a run reads except the span and the step: the study's settings, its loads and masses, the
    assembly's joints, and each part's shape digest and initial placement. A result carries the value it was computed
    with, so a later edit shows up as a difference."""
    def rec(o, names):
        return [o.Name] + [repr(_fp_value(getattr(o, n, None), snap)) for n in names]

    asm = assembly_of(study)
    joints = []
    parts = []
    # the 6c properties and the quaternion snap join the fingerprint only where they matter, so the results of earlier
    # studies keep the hash they were stored with
    stops = asm is not None and any(
        getattr(j, p, False) for j in _joints_of(asm) if _is_joint(j)
        for p in ("EnableAngleMin", "EnableAngleMax", "EnableLengthMin", "EnableLengthMax"))
    snap = stops or any(o.Kind == "Contact" for o in loads(study))
    if asm is not None:
        for j in _joints_of(asm):
            joints.append([j.Name] + [repr(_fp_value(getattr(j, n, None), snap))
                                      for n in sorted(j.PropertiesList) if n not in _FP_JOINT_SKIP])
        for o in live_components(asm):
            parts.append([o.Name, _placement_digest(o.Placement), _shape_digest(o)])
    data = [rec(study, _FP_STUDY + (("LimitRestitution",) if stops else ()))] \
        + [rec(o, _FP_LOAD + (("Restitution", "ContactTolerance") if o.Kind == "Contact" else ())) for o in loads(study)] \
        + [rec(o, _FP_MASS) for o in masses(study)] + [joints, parts]
    return hashlib.sha256(json.dumps(data).encode()).hexdigest()[:16]


def stale_reason(study, res, end=None, step=None):
    """Why ``res`` is not the result of the study as it is now (``end`` and ``step`` override the study's span and step),
    or "" when it is current: the span or the step differs, the loads, masses or settings were edited since the run, or a
    part the result names no longer exists."""
    if res is None:
        return ""
    end = _q(study.TimeEnd if end is None else end)
    step = _q(study.TimeStep if step is None else step)
    if abs(res["step"] - step) > 1e-12 * max(1.0, step) or abs(res["t"][-1] - end) > 1e-9 * max(1.0, end):
        return "it was computed for a span of %g s and a step of %g s, the analysis now has %g s and %g s" % (
            res["t"][-1], res["step"], end, step)
    if "inputs" in res and res["inputs"] != inputs_fingerprint(study):
        return "the loads, masses, settings, joints or the shape or placement of a part were edited after it was computed"
    gone = [p["name"] for p in res["parts"] if study.Document.getObject(p["name"]) is None]
    if gone:
        return "it names parts that no longer exist (%s)" % ", ".join(gone)
    return ""


def _compute(study, end=None, step=None):
    res = _run_kinematic(study, end, step) if study.Mode == "Kinematic" else _run_dynamic(study, end, step)
    res["inputs"] = inputs_fingerprint(study)
    return res


def compute(study, time_end=None, step=None):
    """The result of a run as a dict, without writing anything into the study (the GUI panel runs this and applies
    the result on OK). ``time_end`` and ``step`` override the study's values. The assembly's placements are put back
    and no part document is touched; no undo transaction is opened. ``json.dumps(res, sort_keys=True)`` of it is
    exactly the string ``run`` stores when the overrides are the study's own values."""
    asm = assembly_of(study)
    saved = _save_placements(asm)
    try:
        return _compute(study, time_end, step)
    finally:
        _restore_placements(asm, saved)


def result_text(res):
    """The string form in which a result is stored in ``Result``."""
    return json.dumps(res, sort_keys=True)


def run(study, keep_frame=None):
    """Run the study. Returns the result (see ``to_json``). The assembly's placements are put back unless
    ``keep_frame`` (a step index) asks to keep one frame; no part document is touched."""
    asm = assembly_of(study)
    doc = asm.Document
    saved = _save_placements(asm)
    try:
        with _transaction(doc, "Run dynamic analysis"):
            res = _compute(study)
            _write_series(study, result_text(res))
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
    with _transaction(doc, "Show dynamic analysis frame"):
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


def check_grid(end, step):
    """(step, number of steps) of a time span, or a ``MotionError`` that says what is wrong with it (the same refusal
    a run makes)."""
    class _S:
        TimeEnd, TimeStep = end, step
    st, grid = _grid(_S)
    return st, len(grid) - 1


def apply_study(study, time_end=None, step=None, result=None, title="Edit dynamic analysis"):
    """Set the span and the step and store ``result`` (a dict from ``compute``) as one undo transaction. None leaves
    a value alone; nothing is opened when nothing differs from what the study holds. A ``result`` that does not match the
    span and step being set, or that names a part that no longer exists, is refused. A stored result that the new span or
    step makes stale is cleared in the same transaction (``cleared`` in the return value of ``apply_study_report``)."""
    return apply_study_report(study, time_end, step, result, title)["changed"]


def apply_study_report(study, time_end=None, step=None, result=None, title="Edit dynamic analysis"):
    """``apply_study``, returning ``{"changed": bool, "cleared": reason or ""}``."""
    edits = {}
    if time_end is not None and _q(time_end) != _q(study.TimeEnd):
        edits["TimeEnd"] = time_end
    if step is not None and _q(step) != _q(study.TimeStep):
        edits["TimeStep"] = step
    end2, step2 = edits.get("TimeEnd", study.TimeEnd), edits.get("TimeStep", study.TimeStep)
    check_grid(end2, step2)
    text = None if result is None else result_text(result)
    if result is not None:
        why = stale_reason(study, result, end2, step2)
        if why:
            raise MotionError("the result cannot be stored: " + why)
    cleared = ""
    if result is None and study.Result:
        cleared = stale_reason(study, json.loads(study.Result), end2, step2)
        if cleared:
            text = ""
    if text is not None and text == study.Result:
        text = None
    if not edits and text is None:
        return {"changed": False, "cleared": cleared}
    with _transaction(study.Document, title):
        _assign(study, edits)
        if text is not None:
            _write_series(study, text)
    return {"changed": True, "cleared": cleared}


# ------------------------------------------------------------------ plotted quantities

AXES = ("x", "y", "z")


def quantities(res):
    """What can be plotted from a result: ``[{"id", "label", "unit", "kind"}]``. A position is the part placement's
    base point (mm); a velocity is the velocity of that point (mm/s); a spring or damper force is the force along the
    line between its two points (N, positive when the spring is stretched or the damper's ends move apart)."""
    out = []
    for p in res["parts"]:
        for a in AXES:
            out.append({"id": "position:%s:%s" % (p["name"], a), "kind": "position", "unit": "mm",
                        "label": "%s position %s" % (p["name"], a.upper())})
    if res["mode"] == "Dynamic":
        for p in res["parts"]:
            for a in AXES:
                out.append({"id": "velocity:%s:%s" % (p["name"], a), "kind": "velocity", "unit": "mm/s",
                            "label": "%s velocity %s" % (p["name"], a.upper())})
        for ld in res["loads"]:
            if ld["applied"] and ld["kind"] in ("Spring", "Damper"):
                out.append({"id": "force:%s" % ld["name"], "kind": "force", "unit": "N",
                            "label": "%s force" % ld["name"]})
    return out


def series_of(study, res, quantity, derived=None):
    """``(t, values, info)`` of one quantity of ``res``. Positions come straight from the stored placements.
    Velocities and forces are computed from the stored joint coordinates and rates (``derived_series``), which
    needs the assembly at the placements it had when the run started: pass the dict from one ``derived_series`` call
    taken in that state as ``derived`` to reuse it."""
    info = next((q for q in quantities(res) if q["id"] == quantity), None)
    if info is None:
        raise MotionError("the result has no quantity %r" % quantity)
    if info["kind"] == "position":
        _k, name, axis = quantity.split(":")
        p = next(p for p in res["parts"] if p["name"] == name)
        i = AXES.index(axis)
        return list(res["t"]), [d["base"][i] for d in p["placements"]], info
    if derived is None:
        derived = derived_series(study, res)
    return list(res["t"]), list(derived[quantity]), info


def derived_series(study, res):
    """Velocity and force series of a dynamic result, ``{quantity id: [values]}``, from the stored joint coordinates and
    rates and the kinematic model of the study. The assembly must be at the placements it had when the study was run
    (the panel restores them before it calls this); nothing is written."""
    if res["mode"] != "Dynamic":
        return {}
    doc = study.Document
    for p in res["parts"]:
        o = doc.getObject(p["name"])
        f0 = p["placements"][0]
        if o is None or not o.Placement.isSame(
                App.Placement(App.Vector(*f0["base"]), App.Rotation(*f0["rotation"])), 1e-6):
            raise MotionError("the part %s is not where it was when the result was computed, so velocities and forces "
                              "cannot be derived from it; run the analysis again" % p["name"])
    model = Model(study)
    np = model.np
    doc = model.asm.Document
    by_joint = {j["name"]: j for j in res["joints"]}
    n_t = len(res["t"])
    parts = [b for b in model.order if b.moving]
    out = {}
    for b in parts:
        for a in AXES:
            out["velocity:%s:%s" % (b.name, a)] = []
    for p in res["parts"]:
        if p["name"] not in {b.name for b in parts}:
            for a in AXES:
                out["velocity:%s:%s" % (p["name"], a)] = [0.0] * n_t
    flds = []
    for ld in loads(study):
        if ld.Kind in ("Spring", "Damper") and any(r["name"] == ld.Name and r["applied"] for r in res["loads"]):
            b1 = model._body_of(_lookup(doc, ld.Part1))
            b2 = model._body_of(_lookup(doc, ld.Part2))
            flds.append((ld, b1, b1.world0(_mmvec(ld.Point1), np), b2, b2.world0(_mmvec(ld.Point2), np)))
            out["force:%s" % ld.Name] = []
    for k in range(n_t):
        q = np.zeros(model.n)
        qd = np.zeros(model.n)
        for j in model.joints:
            rec = by_joint[j.obj.Name]
            q[j.dof] = rec["q"][k]
            qd[j.dof] = rec["qd"][k]
        Rd, td, A, O = model.kin(q)
        for b in parts:
            base, _R = model.pose(b, Rd, td)
            v = model.jac(b, base * 1e-3, A, O)[0] @ qd * 1e3
            for i, a in enumerate(AXES):
                out["velocity:%s:%s" % (b.name, a)].append(float(v[i]))
        for (ld, b1, x01, b2, x02) in flds:
            (u, L), J1, J2 = model._pair(b1, x01, b2, x02, Rd, td, A, O, qd)
            if ld.Kind == "Spring":
                f = _spring_force(_q(ld.Stiffness), L, _free_length(ld) * 1e-3)
            else:
                f = _damper_force(_damping(ld), float(u @ ((J2 - J1) @ qd)))
            out["force:%s" % ld.Name].append(float(f))
    return out


def signature(doc):
    """Object-and-property signature of a document for the undo and part-document checks (the assembly's
    ``_Part_ShapeCache``, which the core fills lazily, is not part of it)."""
    import AssemblySkeleton as AS
    sig = AS.signature(doc)
    for rec in sig.values():
        rec["props"].pop("_Part_ShapeCache", None)
    return sig
