# SPDX-License-Identifier: LGPL-2.1-or-later
"""Replace-component corpus (G06 slice 2a): deterministic assemblies with a known correct remap.

Every case builds an assembly with a grounded plate and one part ``M`` (the part to replace), joined
by one or more joints, plus a new part. The truth of a case is given as geometric descriptions of
the elements, never as element names: ``("plane", normal, offset)``, ``("cyl", point, axis, r)``,
``("circle", centre, axis, r)``, ``("line", p, q)`` and ``("vertex", p)``. A description is
resolved against a shape by ``find`` below, which compares exact analytic geometry (outward normal and
offset of a plane, axis line and radius of a cylinder, centre and radius of a circle, end points of a
line) and is independent of the matcher in ReplaceComponent. A case lists, per joint, the
description of each element on the old part and the expected result on the new part: a description
(matched), ``UN`` (cannot be matched) or ``("amb", [descriptions])`` (ambiguous, these are the
candidates).

``build_case(doc, name)`` returns a ``Built`` with the assembly, the old and new objects, the
call arguments and ``truth``: ``{"<Joint>.<Reference>": [t0, t1]}`` where each ``t`` is
``("el", name)``, ``("unmatched",)`` or ``("ambiguous", {names})`` in the new part's element names.
"""

import math
import os

import FreeCAD as App
import Part

import JointObject

CASES = {}
UN = "UN"
V = App.Vector
Z = V(0, 0, 1)


def given_identity():
    """The caller's answer for a part that was not turned: pass it as the alignment."""
    return {"alignment": App.Placement()}


class Case:
    def __init__(self, name, klass, fn, **opts):
        self.name = name
        self.klass = klass
        self.fn = fn
        self.opts = opts


def case(klass, name=None, **opts):
    def deco(fn):
        nm = name or fn.__name__
        CASES[nm] = Case(nm, klass, fn, **opts)
        return fn
    return deco


class Built:
    def __init__(self):
        self.asm = None
        self.old = None            # what is passed as ``old``
        self.new = None
        self.kwargs = {}           # extra arguments for plan and apply
        self.truth = {}            # "<Joint>.<Reference>" -> [t0, t1]
        self.joints = []           # (joint object, type name)
        self.still = False         # every referenced JCS is unchanged, so the parts must not move
        self.solid_ok = True       # False when the joints cannot all be satisfied (broken ones)
        self.moving = []           # component objects whose placement is checked for "did not move"
        self.expect_broken = []    # joint names expected broken after apply
        self.apply_kwargs = {}     # extra arguments for apply only (delete_old)
        self.expect_kept = []      # object names that must survive an apply with delete_old, with the reason in kept_old
        self.expect_deleted = []   # object names that must be gone after an apply with delete_old
        self.alignment = "identity"
        self.old_names = {}
        self.mode = ""
        self.hosts = []
        self.notes = ""


# ------------------------------------------------------------------ geometry

def local(obj):
    """The shape of an object in its own frame (placement removed)."""
    s = obj.Shape.copy()
    s.Placement = App.Placement()
    return s


def solid(shape):
    return shape.removeSplitter().Solids[0]


def bracket(w=40, d=20, h=10, holes=((10, 6, 3),), bosses=(), cut=None, chamfer_end=False):
    s = Part.makeBox(w, d, h)
    for (x, y, r) in holes:
        s = s.cut(Part.makeCylinder(r, h + 2, V(x, y, -1)))
    for (x, y, bw, bd, bh) in bosses:
        s = s.fuse(Part.makeBox(bw, bd, bh, V(x, y, h)))
    if chamfer_end:
        # remove the +x end face entirely: a wedge from (w, *, 0) up to (w - h, *, h)
        wedge = Part.makePolygon([V(w, -1, 0), V(w + 1, -1, 0), V(w + 1, -1, h + 1), V(w - h - 1, -1, h + 1), V(w, -1, 0)])
        prism = Part.Face(wedge).extrude(V(0, d + 2, 0))
        s = s.cut(prism)
    if cut is not None:
        s = s.cut(cut)
    return solid(s)


def plate(n=1):
    s = Part.makeBox(100, 100 * n, 10)
    for k in range(n):
        s = s.fuse(Part.makeCylinder(3, 20, V(50, 50 + 100 * k, 0)))
    return solid(s)


def bolt(r, length, rh, hh):
    """Axis +z from z=0 to z=length; head from z=-hh to z=0 (underside at z=0 faces +z)."""
    s = Part.makeCylinder(r, length, V(0, 0, 0)).fuse(Part.makeCylinder(rh, hh, V(0, 0, -hh)))
    return solid(s)


def tiers(remove_upper=False):
    base = Part.makeBox(40, 20, 5)
    if remove_upper:
        return solid(base)
    return solid(base.fuse(Part.makeBox(20, 20, 5, V(0, 0, 5))))


def chiral(mirror=False):
    """An L profile with an off-centre hole and an off-centre boss: no mirror symmetry."""
    s = Part.makeBox(40, 20, 10).fuse(Part.makeBox(10, 20, 30))
    s = s.cut(Part.makeCylinder(3, 12, V(25, 5, -1)))
    s = s.fuse(Part.makeBox(4, 4, 4, V(0, 14, 30)))
    s = solid(s)
    if mirror:
        s = s.mirror(V(20, 0, 0), V(1, 0, 0))
    return s


def moved(shape, plc):
    """The shape with ``plc`` baked into its geometry (its own Placement stays the identity)."""
    s = shape.copy()
    s.transformShape(plc.toMatrix(), True)
    assert s.Placement.isSame(App.Placement(), 1e-12)
    return s


# ------------------------------------------------------- independent resolver

def _par(a, b, tol=1e-6):
    return abs(abs(a.dot(b)) - 1.0) < tol


def _mid_normal(f):
    u0, u1, v0, v1 = f.ParameterRange
    return f.normalAt((u0 + u1) / 2, (v0 + v1) / 2), f.valueAt((u0 + u1) / 2, (v0 + v1) / 2)


def find(shape, spec):
    """Names of the elements of ``shape`` that satisfy a description."""
    kind = spec[0]
    out = []
    if kind == "plane":
        n, d = V(*spec[1]), spec[2]
        for i, f in enumerate(shape.Faces, 1):
            if f.Surface.TypeId != "Part::GeomPlane":
                continue
            fn, p = _mid_normal(f)
            if (fn - n).Length < 1e-6 and abs(n.dot(p) - d) < 1e-6:
                out.append("Face%d" % i)
    elif kind == "cyl":
        p, a, r = V(*spec[1]), V(*spec[2]), spec[3]
        for i, f in enumerate(shape.Faces, 1):
            s = f.Surface
            if s.TypeId != "Part::GeomCylinder" or abs(s.Radius - r) > 1e-6 or not _par(s.Axis, a):
                continue
            q = p - s.Center
            if (q - s.Axis * s.Axis.dot(q)).Length < 1e-6:
                out.append("Face%d" % i)
    elif kind == "circle":
        c, a, r = V(*spec[1]), V(*spec[2]), spec[3]
        for i, e in enumerate(shape.Edges, 1):
            cv = e.Curve
            if cv.TypeId == "Part::GeomCircle" and abs(cv.Radius - r) < 1e-6 and _par(cv.Axis, a) \
                    and (cv.Center - c).Length < 1e-6:
                out.append("Edge%d" % i)
    elif kind == "line":
        p, q = V(*spec[1]), V(*spec[2])
        for i, e in enumerate(shape.Edges, 1):
            if e.Curve.TypeId != "Part::GeomLine" or len(e.Vertexes) != 2:
                continue
            a, b = e.Vertexes[0].Point, e.Vertexes[1].Point
            if ((a - p).Length < 1e-6 and (b - q).Length < 1e-6) or ((a - q).Length < 1e-6 and (b - p).Length < 1e-6):
                out.append("Edge%d" % i)
    elif kind == "vertex":
        p = V(*spec[1])
        for i, v in enumerate(shape.Vertexes, 1):
            if (v.Point - p).Length < 1e-6:
                out.append("Vertex%d" % i)
    else:
        raise ValueError(spec)
    return out


def one(shape, spec):
    names = find(shape, spec)
    assert len(names) == 1, "description %r resolves to %r" % (spec, names)
    return names[0]


def names_for(shape, descs):
    """Element names for a pair of descriptions as a joint reference holds them: a face followed by an
    edge names the edge by its number within the face."""
    names = [one(shape, d) for d in descs]
    if len(names) == 2 and names[0].startswith("Face") and names[1].startswith("Edge"):
        face = shape.getElement(names[0])
        target = shape.getElement(names[1])
        for i, e in enumerate(face.Edges, 1):
            if e.isSame(target):
                names[1] = "Edge%d" % i
                break
        else:
            raise AssertionError("edge %s is not on face %s" % (names[1], names[0]))
    return names


def pl(n, d):
    return ("plane", n, d)


def cy(p, a, r):
    return ("cyl", p, a, r)


def ci(c, a, r):
    return ("circle", c, a, r)


def vx(p):
    return ("vertex", p)


# ------------------------------------------------------------- joint recipes

GPIN = lambda k=0: cy((50, 50 + 100 * k, 0), (0, 0, 1), 3)
GPIN_TOP = lambda k=0: ci((50, 50 + 100 * k, 20), (0, 0, 1), 3)
G_XP = pl((1, 0, 0), 100)
G_ZP = pl((0, 0, 1), 10)
G_V = vx((100, 100, 10))

# type -> (JointTypes index, G description pair, distance)
RECIPES = {
    "fixed": (0, (G_XP, G_V), 0.0),
    "revolute": (1, (GPIN(), GPIN_TOP()), 0.0),
    "cylindrical": (2, (GPIN(), GPIN()), 0.0),
    "slider": (3, (G_XP, G_XP), 0.0),
    "ball": (4, (G_V, G_V), 0.0),
    "planar": (5, (G_ZP, G_ZP), 0.0),
    "distance": (5, (G_XP, G_XP), 2.0),
}


def jspec(jtype, m_old, m_new, name=None, g=None, dist=None, comp_path=""):
    """One joint of a case: type, M descriptions (old part), expected descriptions (new part)."""
    return {"type": jtype, "m_old": m_old, "m_new": m_new, "name": name or jtype.capitalize(),
            "g": g, "dist": dist}


def _pair(x):
    return x if isinstance(x, tuple) and len(x) == 2 and not isinstance(x[0], str) else (x, x)


# the usual descriptions on the base bracket (hole r3 at (10,10), 40 x 20 x 10)
BR = {
    "fixed": (pl((0, 0, -1), 0), vx((0, 0, 0))),
    "fixed_top": (pl((0, 0, 1), 10), vx((40, 20, 10))),
    "revolute": (cy((10, 6, 0), (0, 0, 1), 3), ci((10, 6, 10), (0, 0, 1), 3)),
    "cylindrical": (cy((10, 6, 0), (0, 0, 1), 3), cy((10, 6, 0), (0, 0, 1), 3)),
    "slider": (pl((0, -1, 0), 0), pl((0, -1, 0), 0)),
    "slider_x": (pl((1, 0, 0), 40), pl((1, 0, 0), 40)),
    "ball": (vx((40, 20, 10)), vx((40, 20, 10))),
    "planar": (pl((0, 0, -1), 0), pl((0, 0, -1), 0)),
    "distance": (pl((-1, 0, 0), 0), pl((-1, 0, 0), 0)),
    "distance_y": (pl((0, 1, 0), 20), pl((0, 1, 0), 20)),
}


# ------------------------------------------------------------------ the scene

def _mk(doc, name, shape, visible=False):
    o = doc.addObject("Part::Feature", name)
    o.Shape = shape
    o.Visibility = visible
    return o


def _truth_pair(descs, new_shape):
    """[truth, truth] for a pair of descriptions on the new part."""
    out = []
    plain = [d for d in descs if d != UN and not (isinstance(d, tuple) and d[0] == "amb")]
    names = names_for(new_shape, descs) if len(plain) == 2 else None
    for i, d in enumerate(descs):
        if d == UN:
            out.append(("unmatched",))
        elif isinstance(d, tuple) and d[0] == "amb":
            out.append(("ambiguous", frozenset(n for x in d[1] for n in find(new_shape, x))))
        else:
            out.append(("el", names[i] if names else one(new_shape, d)))
    return out


def scene(doc, old_shape, new_shape, joints, mode="link", mplace=None, plate_n=1, new_placement=None,
          kwargs=None, array=False, still=False, ground_old=False, new_is_link=False, old_obj=None):
    """Build the standard scene. ``joints`` is a list of ``jspec`` dicts (one per M-side reference)."""
    b = Built()
    b.mode = mode
    asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
    jg = asm.newObject("Assembly::JointGroup", "Joints")
    b.asm = asm
    G = _mk(doc, "G", plate(plate_n))
    old = old_obj if old_obj is not None else _mk(doc, "Old", old_shape)
    new = _mk(doc, "New", new_shape)
    if new_placement is not None:
        new.Placement = new_placement
    gl = asm.newObject("App::Link", "GL")
    gl.LinkedObject = G
    mplace = mplace or App.Placement(V(5, 5, 30), App.Rotation())
    comps = []
    sub = None
    if mode == "direct":
        asm.addObject(old)
        old.Visibility = True
        old.Placement = mplace
        comps = [old]
        b.old = old
    elif mode in ("link", "source"):
        ml = asm.newObject("App::Link", "ML")
        ml.LinkedObject = old
        ml.Placement = mplace
        comps = [ml]
        b.old = ml if mode == "link" else old
    elif mode == "array":
        arr = asm.newObject("App::Link", "Arr")
        arr.LinkedObject = old
        arr.ElementCount = len(joints)
        arr.ShowElement = True
        doc.recompute()
        for i, el in enumerate(arr.ElementList):
            el.Placement = App.Placement(V(200, 100 * i, 0), App.Rotation())
        comps = list(arr.ElementList)
        b.old = arr
    elif mode == "sub":
        sub = asm.newObject("Assembly::AssemblyObject", "Sub")
        ml = sub.newObject("App::Link", "ML")
        ml.LinkedObject = old
        ml.Placement = mplace
        comps = [sub]
        b.old = ml
    elif mode == "two":
        m1 = asm.newObject("App::Link", "ML")
        m1.LinkedObject = old
        m1.Placement = mplace
        m2 = asm.newObject("App::Link", "ML2")
        m2.LinkedObject = old
        m2.Placement = App.Placement(V(5, 105, 30), App.Rotation())
        comps = [m1, m2]
        b.old = old
    else:
        raise ValueError(mode)
    if new_is_link:
        nl = asm.newObject("App::Link", "NewLink")
        nl.LinkedObject = new
        b.new = nl
    else:
        b.new = new
    gj = jg.newObject("App::FeaturePython", "Grounded")
    JointObject.GroundedJoint(gj, comps[0] if ground_old else gl)
    doc.recompute()
    b.kwargs = dict(kwargs or {})
    per_comp = array or mode == "two"
    for i, js in enumerate(joints):
        tidx, gdesc, dist = RECIPES[js["type"]]
        dist = js["dist"] if js["dist"] is not None else dist
        pin = i if per_comp else 0
        gd = js["g"] or gdesc
        if js["g"] is None and js["type"] == "revolute":
            gd = (GPIN(pin), GPIN_TOP(pin))
        elif js["g"] is None and js["type"] == "cylindrical":
            gd = (GPIN(pin), GPIN(pin))
        comp = comps[i if per_comp else 0]
        gnames = names_for(G.Shape, _pair(gd))
        mnames = names_for(old_shape, _pair(js["m_old"]))
        prefix = "ML." if mode == "sub" else ""
        refg = (gl, gnames)
        refm = (comp, [prefix + n for n in mnames])
        j = jg.newObject("App::FeaturePython", js["name"])
        JointObject.Joint(j, tidx)
        if dist:
            j.Distance = dist
        j.Proxy.setJointConnectors(j, [refm, refg] if ground_old else [refg, refm])
        b.joints.append((j, js["type"]))
        mprop = "Reference1" if j.Reference1[0] != gl else "Reference2"
        b.old_names["%s.%s" % (j.Name, mprop)] = mnames
        b.truth["%s.%s" % (j.Name, mprop)] = _truth_pair(_pair(js["m_new"]), new_shape)
    doc.recompute()
    b.moving = list(comps)
    b.hosts = list(comps)
    b.still = still
    for key, t in b.truth.items():
        if any(x[0] != "el" for x in t):
            b.expect_broken.append(key.split(".")[0])
    return b


def build_case(doc, name):
    c = CASES[name]
    doc.UndoMode = 1
    b = c.fn(doc)
    b.name = name
    b.klass = c.klass
    b.opts = c.opts
    doc.recompute()
    return b


# ----------------------------------------------------------------- the cases

BASE = bracket()


def _same(js_type, key=None):
    d = BR[key or js_type]
    return jspec(js_type, d, d)


# joint types, identical revision (a copy of the part: every element matches exactly)
for _t in ("fixed", "revolute", "cylindrical", "slider", "planar", "ball", "distance"):
    def _mk_case(t=_t):
        @case("joint_type", name="type_%s_identical" % t)
        def _c(doc):
            return scene(doc, BASE, bracket(), [_same(t)], still=True)
    _mk_case()

# a feature added that renumbers faces and edges (a boss on top at the near end; the hole stays)
_BOSSED = bracket(bosses=((0, 5, 5, 10, 4),))
for _t, _key in (("revolute", "revolute"), ("fixed", "fixed"), ("slider", "slider"), ("planar", "planar"),
                 ("cylindrical", "cylindrical")):
    def _mk_case(t=_t, key=_key):
        @case("renumber", name="renumber_%s" % t, renumbers=True)
        def _c(doc):
            return scene(doc, BASE, _BOSSED, [_same(t, key)], still=(t in ("revolute", "cylindrical", "planar")))
    _mk_case()

# resized part, same topology (60 x 30 x 10, hole stays at (10, 10))
_BIG = bracket(60, 30, 10)
CASES_RESIZE = {
    "fixed": jspec("fixed", BR["fixed_top"], (pl((0, 0, 1), 10), vx((60, 30, 10)))),
    "slider": jspec("slider", BR["slider_x"], pl((1, 0, 0), 60)),
    "planar": jspec("planar", BR["distance_y"], pl((0, 1, 0), 30)),
    "distance": jspec("distance", BR["distance_y"], pl((0, 1, 0), 30)),
    "ball": jspec("ball", BR["ball"], vx((60, 30, 10))),
}
for _t, _js in CASES_RESIZE.items():
    def _mk_case(t=_t, js=_js):
        @case("resize", name="resize_%s" % t)
        def _c(doc):
            return scene(doc, BASE, _BIG, [js])
    _mk_case()

# a revision with the hole moved to (30, 10)
_HOLE30 = bracket(holes=((30, 6, 3),))
# The bracket with the hole at x = 30 is also the bracket with the hole at x = 10 turned 180 degrees about y:
# nothing in the geometry says which one the designer meant. By default the replace reports that
# (ambiguous alignment); the caller who knows the revision kept the part's frame passes the identity.
AMB = ("amb", [])
for _t in ("revolute", "cylindrical"):
    def _mk_case(t=_t):
        @case("hole_moved", name="hole_moved_%s" % t)
        def _c(doc):
            new_c = (cy((30, 6, 0), (0, 0, 1), 3), ci((30, 6, 10), (0, 0, 1), 3)) if t == "revolute" else \
                (cy((30, 6, 0), (0, 0, 1), 3), cy((30, 6, 0), (0, 0, 1), 3))
            return scene(doc, BASE, _HOLE30, [jspec(t, BR[t], new_c)], kwargs={"alignment": App.Placement()})

        @case("hole_moved", name="hole_moved_%s_alignment_open" % t)
        def _c2(doc):
            return scene(doc, BASE, _HOLE30, [jspec(t, BR[t], (AMB, AMB))])
    _mk_case()


# a revision where the referenced face (or hole) is gone
@case("face_removed")
def removed_hole_revolute(doc):
    return scene(doc, BASE, bracket(holes=()), [jspec("revolute", BR["revolute"], UN)])


@case("face_removed")
def removed_end_face_slider(doc):
    return scene(doc, BASE, bracket(chamfer_end=True), [jspec("slider", BR["slider_x"], UN)])


@case("face_removed")
def removed_tier_slider(doc):
    # the +x face of the upper tier (x = 20) disappears with the tier; the lower tier's +x face is another face
    return scene(doc, tiers(), tiers(remove_upper=True),
                 [jspec("slider", pl((1, 0, 0), 20), UN)])


@case("face_removed")
def removed_tier_and_base_shortened_slider(doc):
    # as above, and the lower tier is shorter too, so no new face is an exact twin of an old one
    return scene(doc, tiers(), solid(Part.makeBox(35, 20, 5)), [jspec("slider", pl((1, 0, 0), 20), UN)])


@case("face_removed")
def removed_smaller_hole_vs_larger(doc):
    # the r3 hole is gone and an r6 hole sits elsewhere: 100 percent radius change is not the same hole
    return scene(doc, BASE, bracket(holes=((30, 6, 6),)), [jspec("cylindrical", BR["cylindrical"], UN)])


# a mirrored part
@case("mirrored")
def mirrored_chiral_slider(doc):
    # the plane x = 0 face of the L bracket is the mirror image of its x = 40 face: the part is a mirror image, so unmatched
    return scene(doc, chiral(), chiral(mirror=True), [jspec("slider", pl((-1, 0, 0), 0), UN)])


@case("mirrored")
def mirrored_symmetric_fixed(doc):
    # a symmetric part mirrored is the same part: everything matches exactly
    box = solid(Part.makeBox(30, 20, 10))
    d = (pl((0, 0, 1), 10), vx((30, 20, 10)))
    return scene(doc, box, solid(box.mirror(V(15, 0, 0), V(1, 0, 0))), [jspec("fixed", d, d)], still=True)


# a different part of the same family (bolt M6 to M8 and back)
_M6, _M8 = bolt(3, 20, 5, 4), bolt(4, 25, 6.5, 5.3)
_BOLT = {
    "revolute": (cy((0, 0, 0), (0, 0, 1), 3), ci((0, 0, 20), (0, 0, 1), 3)),
    "planar": pl((0, 0, 1), 0),
    "cylindrical": (cy((0, 0, 0), (0, 0, 1), 3), cy((0, 0, 0), (0, 0, 1), 3)),
}
_BOLT8 = {
    "revolute": (cy((0, 0, 0), (0, 0, 1), 4), ci((0, 0, 25), (0, 0, 1), 4)),
    "planar": pl((0, 0, 1), 0),
    "cylindrical": (cy((0, 0, 0), (0, 0, 1), 4), cy((0, 0, 0), (0, 0, 1), 4)),
}


@case("family")
def bolt_m6_to_m8_revolute(doc):
    return scene(doc, _M6, _M8, [jspec("revolute", _BOLT["revolute"], _BOLT8["revolute"])])


@case("family")
def bolt_m6_to_m8_planar(doc):
    return scene(doc, _M6, _M8, [jspec("planar", _BOLT["planar"], _BOLT8["planar"])])


@case("family")
def bolt_m8_to_m6_cylindrical(doc):
    return scene(doc, _M8, _M6, [jspec("cylindrical", _BOLT8["cylindrical"], _BOLT["cylindrical"])])


# two geometrically identical candidates: the match is ambiguous
_TWO_HOLES = bracket(holes=((10, 6, 3), (30, 6, 3)))
_MID_HOLE = bracket(holes=((20, 6, 3),))
_AMB_CYL = ("amb", [cy((10, 6, 0), (0, 0, 1), 3), cy((30, 6, 0), (0, 0, 1), 3)])
_AMB_TOP = ("amb", [ci((10, 6, 10), (0, 0, 1), 3), ci((30, 6, 10), (0, 0, 1), 3)])


@case("ambiguous")
def sym_two_holes_revolute(doc):
    return scene(doc, _MID_HOLE, _TWO_HOLES, [jspec("revolute",
                                                   (cy((20, 6, 0), (0, 0, 1), 3), ci((20, 6, 10), (0, 0, 1), 3)),
                                                   (_AMB_CYL, _AMB_TOP))])


@case("ambiguous")
def sym_two_holes_cylindrical(doc):
    d = cy((20, 6, 0), (0, 0, 1), 3)
    return scene(doc, _MID_HOLE, _TWO_HOLES, [jspec("cylindrical", d, _AMB_CYL)])


@case("ambiguous")
def sym_two_bosses_planar(doc):
    # one boss top (z = 14) at the middle; the new part has two, left and right of it by the same distance
    old = bracket(bosses=((18, 5, 4, 10, 4),))
    new = bracket(bosses=((14, 5, 4, 10, 4), (22, 5, 4, 10, 4)))
    # boss top faces: x 18..22 (centre 20) versus x 14..18 (centre 16) and x 22..26 (centre 24)
    return scene(doc, old, new, [jspec("planar", pl((0, 0, 1), 14),
                                      ("amb", [pl((0, 0, 1), 14), pl((0, 0, 1), 14)]))])


@case("ambiguous")
def sym_bar_rotated_alignment(doc):
    # a square bar turned onto another axis with another origin: its inertia cannot say which way it went
    bar = solid(Part.makeBox(20, 20, 60))
    plc = App.Placement(V(300, 0, 0), App.Rotation(V(0, 1, 0), 90))
    return scene(doc, bar, moved(bar, plc), [jspec("fixed", (pl((0, 0, 1), 60), vx((20, 20, 60))),
                                                  ("amb", []))])


# a different origin and orientation
_ROT = App.Placement(V(100, 50, 20), App.Rotation(V(0, 0, 1), 90))
_ROTX = App.Placement(V(-30, 60, 5), App.Rotation(V(1, 0, 0), 90))


def _moved_desc(desc, plc):
    k = desc[0]
    tp = lambda p: tuple(plc.multVec(V(*p)))
    tv = lambda v: tuple(plc.Rotation.multVec(V(*v)))
    if k == "plane":
        n = plc.Rotation.multVec(V(*desc[1]))
        pt = plc.multVec(V(*desc[1]) * desc[2])
        return ("plane", tuple(round(c, 12) + 0.0 for c in n), n.dot(pt))
    if k == "cyl":
        return ("cyl", tp(desc[1]), tv(desc[2]), desc[3])
    if k == "circle":
        return ("circle", tp(desc[1]), tv(desc[2]), desc[3])
    return ("vertex", tp(desc[1]))


@case("origin")
def origin_rot_z_revolute(doc):
    new = moved(BASE, _ROT)
    nd = tuple(_moved_desc(d, _ROT) for d in BR["revolute"])
    return scene(doc, BASE, new, [jspec("revolute", BR["revolute"], nd)], still=True)


@case("origin")
def origin_rot_x_fixed(doc):
    new = moved(BASE, _ROTX)
    nd = tuple(_moved_desc(d, _ROTX) for d in BR["fixed"])
    return scene(doc, BASE, new, [jspec("fixed", BR["fixed"], nd)], still=True)


@case("origin")
def origin_shift_only_slider(doc):
    plc = App.Placement(V(-12, 7, 3), App.Rotation())
    new = moved(BASE, plc)
    nd = _moved_desc(BR["slider"][0], plc)
    return scene(doc, BASE, new, [jspec("slider", BR["slider"], nd)], still=True)


# how the old part is reached
_SOURCE_NEW = _BOSSED


@case("link")
def link_with_rotation_revolute(doc):
    return scene(doc, BASE, _BOSSED, [_same("revolute")], mplace=App.Placement(V(5, 5, 30), App.Rotation(30, 20, 10)),
                 still=True)


@case("link")
def link_source_given_cylindrical(doc):
    return scene(doc, BASE, _BOSSED, [_same("cylindrical")], mode="source", still=True)


@case("link")
def link_two_links_one_source(doc):
    return scene(doc, BASE, _BOSSED, [_same("revolute"), jspec("revolute", BR["revolute"], BR["revolute"], name="Revolute2")],
                 mode="two", plate_n=2, still=True)


@case("link")
def link_new_given_as_link(doc):
    return scene(doc, BASE, _BOSSED, [_same("revolute")], new_is_link=True, still=True)


@case("direct")
def direct_part_fixed(doc):
    return scene(doc, BASE, _BIG, [CASES_RESIZE["fixed"]], mode="direct")


@case("direct")
def direct_part_revolute_renumbered(doc):
    return scene(doc, BASE, _BOSSED, [_same("revolute")], mode="direct", still=True)


@case("direct")
def direct_grounded_part_replaced(doc):
    return scene(doc, BASE, _BOSSED, [_same("revolute")], mode="direct", ground_old=True, still=True)


@case("array")
def array_elements_revolute(doc):
    js = [jspec("revolute", BR["revolute"], BR["revolute"], name="Rev%d" % i) for i in range(3)]
    return scene(doc, BASE, _BOSSED, js, mode="array", plate_n=3, still=True)


@case("array")
def array_elements_hole_moved(doc):
    nd = (cy((30, 6, 0), (0, 0, 1), 3), ci((30, 6, 10), (0, 0, 1), 3))
    js = [jspec("revolute", BR["revolute"], nd, name="Rev%d" % i) for i in range(3)]
    return scene(doc, BASE, _HOLE30, js, mode="array", plate_n=3, kwargs={"alignment": App.Placement()})


@case("array")
def array_element_given_as_old(doc):
    b = scene(doc, BASE, _BOSSED, [jspec("revolute", BR["revolute"], BR["revolute"], name="Rev%d" % i) for i in range(2)],
              mode="array", plate_n=2, still=True)
    b.old = b.hosts[1]
    return b


@case("subassembly")
def sub_revolute_renumbered(doc):
    return scene(doc, BASE, _BOSSED, [_same("revolute")], mode="sub", still=True)


@case("subassembly")
def sub_fixed_resized(doc):
    return scene(doc, BASE, _BIG, [CASES_RESIZE["fixed"]], mode="sub")


@case("subassembly")
def sub_hole_removed(doc):
    return scene(doc, BASE, bracket(holes=()), [jspec("revolute", BR["revolute"], UN)], mode="sub")


# mixed outcomes in one assembly: the matched joint remaps, the unmatched one is marked broken
@case("mixed")
def mixed_matched_and_broken(doc):
    js = [jspec("revolute", BR["revolute"], UN, name="Rev"),
          jspec("planar", BR["planar"], BR["planar"], name="Plan")]
    return scene(doc, BASE, bracket(holes=()), js)


# harder revisions: the referenced face survives a feature that changes its neighbours or its size
@case("feature_edit")
def pocket_in_top_planar(doc):
    pocket = Part.makeBox(8, 6, 4, V(24, 7, 6))
    return scene(doc, BASE, bracket(cut=pocket), [jspec("planar", BR["fixed_top"][0], BR["fixed_top"][0])])


@case("feature_edit")
def hole_radius_3_to_4_cylindrical(doc):
    new = bracket(holes=((10, 6, 4),))
    d = (cy((10, 6, 0), (0, 0, 1), 4), cy((10, 6, 0), (0, 0, 1), 4))
    return scene(doc, BASE, new, [jspec("cylindrical", BR["cylindrical"], d)])


@case("feature_edit")
def hole_became_slot_revolute(doc):
    # the hole grows into a slot to the right: the left end of the slot is the old hole's half
    slot = Part.makeBox(6, 6, 12, V(10, 3, -1)).fuse(Part.makeCylinder(3, 12, V(16, 6, -1)))
    new = bracket(cut=slot)
    return scene(doc, BASE, new, [jspec("revolute", BR["revolute"], BR["revolute"])])


@case("feature_edit", reported_ok=True)
def chamfered_top_edges_planar(doc):
    # chamfers on the four top edges: the top face is the same face, but none of its side neighbours is
    # still adjacent, so the matcher must say it cannot tell (reported, not wrong)
    s = Part.makeBox(40, 20, 10).cut(Part.makeCylinder(3, 12, V(10, 6, -1)))
    top_edges = [e for e in s.Edges if all(abs(v.Point.z - 10) < 1e-9 for v in e.Vertexes) and e.Curve.TypeId == "Part::GeomLine"]
    new = solid(s.makeChamfer(1.0, top_edges))
    return scene(doc, BASE, new, [jspec("planar", BR["fixed_top"][0], BR["fixed_top"][0])])


@case("face_removed")
def two_holes_first_removed_cylindrical(doc):
    # the hole at x = 10 is gone; the one at x = 30 stays and is not a moved copy of it
    old = bracket(holes=((10, 6, 3), (30, 6, 3)))
    new = bracket(holes=((30, 6, 3),))
    # the part is symmetric under a half turn about Y (the two holes swap): a changed part may have been turned,
    # so the caller says it was not; two_holes_first_removed_open leaves the alignment open
    return scene(doc, old, new, [jspec("cylindrical", (cy((10, 6, 0), (0, 0, 1), 3),) * 2, UN)], kwargs=given_identity())


@case("hole_moved")
def two_holes_second_removed_still_first_matches(doc):
    old = bracket(holes=((10, 6, 3), (30, 6, 3)))
    new = bracket(holes=((10, 6, 3),))
    d = (cy((10, 6, 0), (0, 0, 1), 3),) * 2
    return scene(doc, old, new, [jspec("cylindrical", d, d)], kwargs=given_identity())


# The same two parts without the caller's answer: the old part is symmetric under the half turn, the new part is
# not, so "hole at x = 30 removed" and "turned, hole at x = 10 removed" read the same: ambiguous.
@case("hole_moved")
def two_holes_first_removed_open(doc):
    old = bracket(holes=((10, 6, 3), (30, 6, 3)))
    new = bracket(holes=((30, 6, 3),))
    return scene(doc, old, new, [jspec("cylindrical", (cy((10, 6, 0), (0, 0, 1), 3),) * 2, (AMB, AMB))])


@case("hole_moved")
def two_holes_second_removed_open(doc):
    old = bracket(holes=((10, 6, 3), (30, 6, 3)))
    new = bracket(holes=((10, 6, 3),))
    d = (cy((10, 6, 0), (0, 0, 1), 3),) * 2
    return scene(doc, old, new, [jspec("cylindrical", d, (AMB, AMB))])


# --- from the Assembly workbench's own tests (TestCore) ---------------------------------------

def _core_boxes(doc, old_dims, new_dims, ref_old, ref_new, jtype_index=0, rack=False):
    """TestCore.test_solve_assembly: two Part::Box in an assembly, Face6 + Vertex7 fixed joint.

    A box is symmetric under half turns (a cube under quarter turns too), but a resize keeps the topology,
    so the part stands as placed with the default alignment (``_same_topology``)."""
    b = Built()
    asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
    jg = asm.newObject("Assembly::JointGroup", "Joints")
    b.asm = asm
    ground = asm.newObject("Part::Box", "Ground")
    ground.Length, ground.Width, ground.Height = 10, 10, 10
    ground.Placement = App.Placement(V(40, 50, 60), App.Rotation(45, 55, 65))
    box = asm.newObject("Part::Box", "Box")
    box.Length, box.Width, box.Height = old_dims
    box.Placement = App.Placement(V(10, 20, 30), App.Rotation(15, 25, 35))
    newbox = doc.addObject("Part::Box", "NewBox")
    newbox.Length, newbox.Width, newbox.Height = new_dims
    newbox.Visibility = False
    gj = jg.newObject("App::FeaturePython", "GroundedJoint")
    JointObject.GroundedJoint(gj, ground)
    doc.recompute()
    j = jg.newObject("App::FeaturePython", "Fixed")
    JointObject.Joint(j, jtype_index)
    j.Proxy.setJointConnectors(j, [(ground, ["Face6", "Vertex7"]), (box, ["Face6", "Vertex7"])])
    doc.recompute()
    b.joints.append((j, "fixed"))
    b.old, b.new = box, newbox
    b.mode = "direct"
    b.hosts = [box]
    b.moving = [box]
    b.truth["Fixed.Reference2"] = [("el", one(newbox.Shape, ref_new[0])), ("el", one(newbox.Shape, ref_new[1]))]
    assert one(local(box), ref_old[0]) == "Face6" and one(local(box), ref_old[1]) == "Vertex7"
    return b


@case("workbench_tests")
def core_solve_assembly_resized(doc):
    L, W, H = 10, 10, 10
    return _core_boxes(doc, (L, W, H), (20, 14, 10), (pl((0, 0, 1), H), vx((L, W, H))),
                       (pl((0, 0, 1), 10), vx((20, 14, 10))))


# Resize controls: an ordinary resize keeps the topology, so it matches on the default alignment even though the
# old part is symmetric under a turn that the new part is not (a bar lengthened, a cube made a flat plate).
@case("workbench_tests")
def core_resize_square_bar_lengthened(doc):
    return _core_boxes(doc, (10, 10, 40), (10, 10, 60), (pl((0, 0, 1), 40), vx((10, 10, 40))),
                       (pl((0, 0, 1), 60), vx((10, 10, 60))))


@case("workbench_tests")
def core_resize_cube_to_flat_plate(doc):
    return _core_boxes(doc, (10, 10, 10), (15, 15, 4), (pl((0, 0, 1), 10), vx((10, 10, 10))),
                       (pl((0, 0, 1), 4), vx((15, 15, 4))))


@case("workbench_tests")
def core_resize_two_hole_plate_scaled(doc):
    # the two-hole bracket is symmetric under a half turn about Y; scaled uniformly by 1.05 it still has its two
    # holes, each where it was in proportion, so no feature was added, removed or moved: placed, not turned
    old = bracket(holes=((10, 6, 3), (30, 6, 3)))
    new = bracket(w=42, d=21, h=10.5, holes=((10.5, 6.3, 3.15), (31.5, 6.3, 3.15)))
    return scene(doc, old, new, [jspec("cylindrical", (cy((10, 6, 0), (0, 0, 1), 3),) * 2,
                                       (cy((10.5, 6.3, 0), (0, 0, 1), 3.15),) * 2)])


@case("workbench_tests")
def core_solve_assembly_same(doc):
    d = (pl((0, 0, 1), 10), vx((10, 6, 10)))
    b = _core_boxes(doc, (10, 6, 10), (10, 6, 10), d, d)
    b.still = True
    return b


@case("workbench_tests")
def core_rack_pinion_slider(doc):
    """TestCore.test_rack_pinion_with_slider_offset: a slider between a ground plate and a rack box;
    the rack is replaced by a longer one (the slider's face reference is the rack's Face1, the -x face)."""
    b = Built()
    asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
    jg = asm.newObject("Assembly::JointGroup", "Joints")
    b.asm = asm
    ground = asm.newObject("Part::Box", "Ground")
    ground.Length, ground.Width, ground.Height = 200, 200, 10
    rack = asm.newObject("Part::Box", "Rack")
    rack.Length, rack.Width, rack.Height = 10, 100, 10
    rack.Placement.Base = V(50, 0, 10)
    newrack = doc.addObject("Part::Box", "NewRack")
    newrack.Length, newrack.Width, newrack.Height = 10, 140, 10
    newrack.Visibility = False
    doc.recompute()
    gj = jg.newObject("App::FeaturePython", "GroundedJoint")
    JointObject.GroundedJoint(gj, ground)
    along_rack = App.Rotation(V(1, 0, 0), -90)
    yaw_offset = App.Rotation(V(0, 0, 1), -90)
    slider = jg.newObject("App::FeaturePython", "Slider")
    JointObject.Joint(slider, JointObject.JointTypes.index("Slider"))
    slider.Detach1 = True
    slider.Detach2 = True
    slider.Reference1 = (ground, ["Face1"])
    slider.Reference2 = (rack, ["Face1"])
    slider.Placement1 = App.Placement(V(), along_rack)
    slider.Placement2 = App.Placement(V(), along_rack * yaw_offset)
    doc.recompute()
    b.joints.append((slider, "slider"))
    b.old, b.new = rack, newrack
    b.mode = "direct"
    b.hosts = [rack]
    b.moving = [rack]
    assert find(local(rack), pl((-1, 0, 0), 0)) == ["Face1"]
    b.truth["Slider.Reference2"] = [("el", one(local(newrack), pl((-1, 0, 0), 0)))]
    b.still = False
    b.detached = True
    return b


@case("workbench_tests")
def core_generated_array_joint(doc):
    """TestCore._check_generated_array_joint: a link array with a joint on element 1 and 2 (Face1)."""
    b = Built()
    asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
    jg = asm.newObject("Assembly::JointGroup", "Joints")
    b.asm = asm
    source = doc.addObject("Part::Box", "ArraySource")
    source.Visibility = False
    newsource = doc.addObject("Part::Box", "ArraySource2")
    newsource.Length, newsource.Width, newsource.Height = 12, 10, 10
    newsource.Visibility = False
    array = asm.newObject("App::Link", "Array")
    array.LinkedObject = source
    array.ElementCount = 3
    array.ShowElement = True
    doc.recompute()
    for i, el in enumerate(array.ElementList):
        el.Placement = App.Placement(V(30 * i, 0, 0), App.Rotation())
    gj = jg.newObject("App::FeaturePython", "GroundedJoint")
    JointObject.GroundedJoint(gj, array.ElementList[1])
    joint = jg.newObject("App::FeaturePython", "Joint")
    JointObject.Joint(joint, 0)
    joint.Reference1 = (array.ElementList[1], ["Face1"])
    joint.Reference2 = (array.ElementList[2], ["Face1"])
    doc.recompute()
    b.joints.append((joint, "fixed"))
    b.old, b.new = array, newsource
    b.mode = "array"
    b.hosts = list(array.ElementList)
    b.moving = list(array.ElementList)
    t = ("el", one(newsource.Shape, pl((-1, 0, 0), 0)))
    assert one(source.Shape, pl((-1, 0, 0), 0)) == "Face1"
    b.truth["Joint.Reference1"] = [t]
    b.truth["Joint.Reference2"] = [t]
    # the source is a cube: symmetric under quarter turns, but 10 x 10 x 10 to 12 x 10 x 10 is a resize: placed
    return b


# --- from the Assembly workbench's example (data/examples/AssemblyExample.FCStd) ----------------

def example_path():
    cands = []
    root = os.environ.get("FC_SRC")
    if root:
        cands.append(os.path.join(root, "data", "examples", "AssemblyExample.FCStd"))
    here = os.path.dirname(os.path.abspath(__file__))
    for up in range(3, 7):
        cands.append(os.path.join(here, *([".."] * up), "data", "examples", "AssemblyExample.FCStd"))
    cands.append(os.path.join(App.getResourceDir(), "examples", "AssemblyExample.FCStd"))
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


def _notch(shape, keep_clear):
    """The shape with a small block cut off its bounding box surface, clear of the given element
    shapes: a feature that renumbers elements without touching the referenced ones."""
    bb = shape.BoundBox
    size = max(bb.DiagonalLength * 0.02, 0.5)
    for frac in (0.0, 1.0, 0.5, 0.25, 0.75):
        for ix, iy, iz in ((fx, fy, fz) for fx in (0.0, 1.0, 0.5, 0.25, 0.75) for fy in (0.0, 1.0, 0.5, 0.25, 0.75)
                           for fz in (0.0, 1.0, 0.5, 0.25, 0.75)):
            if frac not in (ix, iy, iz):
                continue
            c = V(bb.XMin + ix * bb.XLength, bb.YMin + iy * bb.YLength, bb.ZMin + iz * bb.ZLength)
            box = Part.makeBox(size, size, size, c - V(size / 2, size / 2, size / 2))
            out = shape.cut(box)
            if out.Volume > shape.Volume - 1e-6 or len(out.Solids) != 1:
                continue
            if all(box.distToShape(e)[0] > 1e-3 for e in keep_clear):
                return solid(out)
    raise AssertionError("no clear spot for a notch")


def _geom_key(el):
    """Independent identity of an element by exact geometry (type, rounded size and centre)."""
    if hasattr(el, "Surface"):
        t, size = el.Surface.TypeId, el.Area
    elif hasattr(el, "Curve"):
        t, size = el.Curve.TypeId, el.Length
    else:
        t, size = "Vertex", 0.0
    c = el.CenterOfMass if hasattr(el, "CenterOfMass") and t != "Vertex" else (el.Point if t == "Vertex" else None)
    return (t, round(size, 5), round(c.x, 5) + 0.0, round(c.y, 5) + 0.0, round(c.z, 5) + 0.0)


def _same_geom_names(old_shape, new_shape, name):
    kind = name[:4] if name.startswith("Face") or name.startswith("Edge") else "Vert"
    olds = {"Face": old_shape.Faces, "Edge": old_shape.Edges, "Vert": old_shape.Vertexes}[kind]
    news = {"Face": new_shape.Faces, "Edge": new_shape.Edges, "Vert": new_shape.Vertexes}[kind]
    idx = int(name.lstrip("FaceEdgVrt"))
    key = _geom_key(olds[idx - 1])
    hits = [("%s%d" % ({"Face": "Face", "Edge": "Edge", "Vert": "Vertex"}[kind], i)) for i, e in enumerate(news, 1)
            if _geom_key(e) == key]
    assert len(hits) == 1, (name, hits)
    return hits[0]


def _global_edge(shape, face_name, num):
    t, i = shape.findSubShape(shape.getElement(face_name).Edges[num - 1])
    return "Edge%d" % i


def _local_edge(shape, face_name, edge_name):
    target = shape.getElement(edge_name)
    for i, e in enumerate(shape.getElement(face_name).Edges, 1):
        if e.isSame(target):
            return "Edge%d" % i
    raise AssertionError("%s is not on %s" % (edge_name, face_name))


def example_truth(old_shape, new_shape, subs):
    """Truth for a reference of the example: each element's exact-geometry twin on the new part."""
    els = [x.rsplit(".", 1)[-1] for x in subs]
    face_edge = len(els) == 2 and els[0].startswith("Face") and els[1].startswith("Edge")
    g = list(els)
    if face_edge:
        g[1] = _global_edge(old_shape, els[0], int(els[1][4:]))
    n = [_same_geom_names(old_shape, new_shape, x) for x in g]
    if face_edge:
        n[1] = _local_edge(new_shape, n[0], n[1])
    return [("el", x) for x in n]


def build_example_case(doc, part_name):
    """Open the example assembly and replace one part by a revision with a notch elsewhere on it; the
    truth is each referenced element's exact-geometry twin. Returns None if the example is missing."""
    path = example_path()
    if path is None:
        return None
    src = App.openDocument(path, True)
    try:
        asm = src.getObject("Assembly")
        link = src.getObject(part_name)
        leaf = link.LinkedObject
        refs = [(o, prop) for o in src.Objects if hasattr(o, "Reference1") and hasattr(o, "JointType")
                for prop in ("Reference1", "Reference2")
                if getattr(o, prop) and getattr(o, prop)[0] is not None and getattr(o, prop)[0].Name == link.Name]
        clear = []
        for o, prop in refs:
            for x in getattr(o, prop)[1]:
                el = x.rsplit(".", 1)[-1]
                if el.startswith("Edge") and len(getattr(o, prop)[1]) == 2 and getattr(o, prop)[1][0].rsplit(".", 1)[-1].startswith("Face"):
                    continue
                clear.append(leaf.Shape.getElement(el))
        newshape = _notch(leaf.Shape, clear)
        new = src.addObject("Part::Feature", part_name + "_rev")
        new.Shape = newshape
        new.Visibility = False
        b = Built()
        b.asm, b.old, b.new, b.mode = asm, link, new, "link"
        b.hosts, b.moving = [link], [link]
        b.doc = src
        src.UndoMode = 1
        for o, prop in refs:
            b.truth["%s.%s" % (o.Name, prop)] = example_truth(leaf.Shape, newshape, getattr(o, prop)[1])
            if all(j is not o for j, _t in b.joints):
                b.joints.append((o, o.JointType))
        b.still = True
        return b
    except Exception:
        App.closeDocument(src.Name)
        raise


EXAMPLE_PARTS = ("Stick", "Bucket", "Boom")


# --- a revision checked out of the PDM vault ----------------------------------------------------

def build_pdm_case(doc, workdir):
    """The new part is the object of revision A.2 (hole moved) checked out of a local PDM vault by a
    second working copy; the old part is revision A.1. Returns None if git or PDM is unavailable."""
    try:
        from PDM import vault as PV
    except Exception:
        return None
    import shutil
    import subprocess
    if shutil.which("git") is None:
        return None
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    vpath = PV.Vault.create(os.path.join(workdir, "vault.git"))
    v1 = PV.Vault.clone(vpath, os.path.join(workdir, "wc1"), "alice")
    d1 = App.newDocument("PdmBracket")
    _mk(d1, "Bracket", BASE)
    d1.saveAsFolder(v1.doc_path("bracket"))
    App.closeDocument(d1.Name)
    v1.add_document("bracket")
    v1.checkout("bracket")
    d1 = App.openDocument(v1.doc_path("bracket"))
    d1.getObject("Bracket").Shape = _HOLE30
    d1.recompute()
    d1.saveAsFolder(v1.doc_path("bracket"))
    App.closeDocument(d1.Name)
    v1.checkin("bracket", "hole moved")
    v2 = PV.Vault.clone(vpath, os.path.join(workdir, "wc2"), "bob")
    path = v2.checkout("bracket")
    d2 = App.openDocument(path)
    new = d2.getObject("Bracket")
    nd = (cy((30, 6, 0), (0, 0, 1), 3), ci((30, 6, 10), (0, 0, 1), 3))
    b = scene(doc, BASE, new.Shape, [jspec("revolute", BR["revolute"], nd)], kwargs={"alignment": App.Placement()})
    b.new = new
    b.extra_docs = [d2]
    b.revision = "A.2"
    doc.saveAs(os.path.join(workdir, "assembly.FCStd"))   # a link into another document needs both saved
    return b



# --- adversarial cases from the independent verifier of run 040 (G06 slice 2a) -----------------

def _bar(boss_x=None, boss=(2, 2, 1)):
    s = Part.makeBox(100, 20, 10)
    if boss_x is not None:
        s = s.fuse(Part.makeBox(boss[0], boss[1], boss[2], V(boss_x, 9, 10)))
    return solid(s)


def _turn(shape, axis, deg, centre):
    return moved(shape, App.Placement(V(0, 0, 0), App.Rotation(V(*axis), deg), V(*centre)))


_TIERS_UP = lambda w: solid(Part.makeBox(w, 20, 5))
_X20 = pl((1, 0, 0), 20)


@case("one_to_one")
def a1_step_removed_lower_face_two_mm_away(doc):
    # the upper step's +x face (x = 20) is gone; the lower tier's +x face is a different face 2 mm away
    return scene(doc, tiers(), _TIERS_UP(22), [jspec("slider", _X20, UN)])


@case("one_to_one")
def a2_step_removed_lower_face_one_mm_away(doc):
    return scene(doc, tiers(), _TIERS_UP(21), [jspec("slider", _X20, UN)])


@case("one_to_one")
def a3_step_removed_lower_face_ten_mm_away(doc):
    return scene(doc, tiers(), _TIERS_UP(30), [jspec("slider", _X20, UN)])


@case("one_to_one")
def b1_upper_step_shorter_top_face_follows(doc):
    # the whole part is lower, the top face of the upper step is still that face: z = 10 becomes z = 8
    new = solid(Part.makeBox(40, 20, 5).fuse(Part.makeBox(20, 20, 3, V(0, 0, 5))))
    return scene(doc, tiers(), new, [jspec("planar", pl((0, 0, 1), 10), pl((0, 0, 1), 8), g=pl((0, 0, -1), 0))])


@case("one_to_one")
def c1_hole_gone_other_hole_elsewhere_and_wider(doc):
    # r3 hole removed, an r3.9 hole sits 20 mm away: moved and resized is another feature
    return scene(doc, bracket(holes=((10, 6, 3),)), bracket(holes=((30, 6, 3.9),)),
                 [jspec("cylindrical", BR["cylindrical"], UN)])


@case("one_to_one")
def c2_hole_gone_wider_hole_nearby(doc):
    return scene(doc, bracket(holes=((10, 6, 3),)), bracket(holes=((10, 14, 4),)),
                 [jspec("cylindrical", BR["cylindrical"], UN)])


@case("one_to_one")
def c4_first_of_two_holes_gone_second_shifted(doc):
    # the hole at x = 10 is gone and the one at x = 30 moved by 0.5: the survivor is not the removed hole
    return scene(doc, _TWO_HOLES, bracket(holes=((30.5, 6, 3),)),
                 [jspec("cylindrical", (cy((10, 6, 0), (0, 0, 1), 3),) * 2, UN)], kwargs=given_identity())


@case("one_to_one")
def c5_second_of_two_holes_gone_first_shifted(doc):
    return scene(doc, _TWO_HOLES, bracket(holes=((10.5, 6, 3),)),
                 [jspec("cylindrical", (cy((30, 6, 0), (0, 0, 1), 3),) * 2, UN)], kwargs=given_identity())


_H = lambda x, r=3: (cy((x, 6, 0), (0, 0, 1), r),) * 2


@case("one_to_one")
def d1_both_holes_referenced_first_gone_second_shifted(doc):
    js = [jspec("cylindrical", _H(10), UN, name="J0"), jspec("cylindrical", _H(30), _H(30.5), name="J1")]
    return scene(doc, _TWO_HOLES, bracket(holes=((30.5, 6, 3),)), js, mode="two", plate_n=2, kwargs=given_identity())


@case("one_to_one")
def d2_both_holes_referenced_both_shifted(doc):
    js = [jspec("cylindrical", _H(10), _H(10.5), name="J0"), jspec("cylindrical", _H(30), _H(30.5), name="J1")]
    return scene(doc, _TWO_HOLES, bracket(holes=((10.5, 6, 3), (30.5, 6, 3))), js, mode="two", plate_n=2, kwargs=given_identity())


@case("one_to_one")
def d2_both_holes_referenced_both_shifted_open(doc):
    # both holes moved by 0.5 the same way: as placed each hole follows itself, turned each follows the other
    js = [jspec("cylindrical", _H(10), (AMB, AMB), name="J0"), jspec("cylindrical", _H(30), (AMB, AMB), name="J1")]
    return scene(doc, _TWO_HOLES, bracket(holes=((10.5, 6, 3), (30.5, 6, 3))), js, mode="two", plate_n=2)


@case("one_to_one")
def d3_both_holes_referenced_radii_changed(doc):
    js = [jspec("cylindrical", _H(10), _H(10, 3.5), name="J0"), jspec("cylindrical", _H(30), _H(30, 2.9), name="J1")]
    return scene(doc, _TWO_HOLES, bracket(holes=((10, 6, 3.5), (30, 6, 2.9))), js, mode="two", plate_n=2, kwargs=given_identity())


# a near-symmetric bar: the part as placed and the part turned fit about equally, so the alignment is ambiguous
_BAR_BOSS = _bar(96)
_BAR_C = (50, 10, 5)
_TOP, _XEND = pl((0, 0, 1), 10), pl((1, 0, 0), 100)


@case("alignment")
def e1_bar_turned_about_x_two_references(doc):
    js = [jspec("slider", _TOP, AMB, name="J0"), jspec("slider", _XEND, AMB, name="J1")]
    return scene(doc, _BAR_BOSS, _turn(_BAR_BOSS, (1, 0, 0), 180, _BAR_C), js)


@case("alignment")
def e2_bar_turned_about_x_boss_top(doc):
    return scene(doc, _BAR_BOSS, _turn(_BAR_BOSS, (1, 0, 0), 180, _BAR_C), [jspec("slider", pl((0, 0, 1), 11), AMB)])


@case("alignment")
def e3_bar_turned_about_z_boss_top_and_end(doc):
    js = [jspec("slider", pl((0, 0, 1), 11), AMB, name="J0"), jspec("slider", _XEND, AMB, name="J1")]
    return scene(doc, _BAR_BOSS, _turn(_BAR_BOSS, (0, 0, 1), 180, _BAR_C), js)


@case("alignment")
def e4_bar_translated_by_7(doc):
    plc = App.Placement(V(7, 0, 0), App.Rotation())
    return scene(doc, _BAR_BOSS, moved(_BAR_BOSS, plc), [jspec("slider", _XEND, pl((1, 0, 0), 107))], still=True)


_CUBE_BOSS = solid(Part.makeBox(20, 20, 20).fuse(Part.makeBox(1, 1, 1, V(19, 19, 20))))


@case("alignment")
def e5_cube_with_corner_boss_turned_quarter(doc):
    # no principal axes to enumerate (equal moments): identity is kept and says so; the boss top cannot
    # be matched, the +x face lies on the turning axis and is the same face
    new = _turn(_CUBE_BOSS, (1, 0, 0), 90, (10, 10, 10))
    js = [jspec("planar", pl((0, 0, 1), 21), UN, name="J0"), jspec("slider", pl((1, 0, 0), 20), pl((1, 0, 0), 20), name="J1")]
    return scene(doc, _CUBE_BOSS, new, js)


@case("alignment")
def e7_boss_moved_to_the_other_end(doc):
    # the same shape as e3: indistinguishable from a turned part
    js = [jspec("slider", pl((0, 0, 1), 11), AMB, name="J0"), jspec("slider", _XEND, AMB, name="J1")]
    return scene(doc, _BAR_BOSS, _bar(2), js)


# --- a part turned and also changed (carried MEDIUM of run 040): no exact twins, near twins say "turned" ---

_SMALL_HOLE = bracket(holes=((10, 6, 3), (25, 14, 2)))
_SMALL_BOSS = bracket(bosses=((30, 5, 4, 6, 2),))


def _turn_y(shape):
    return moved(shape, App.Placement(V(0, 0, 0), App.Rotation(V(0, 1, 0), 180), V(20, 10, 5)))


@case("alignment")
def n1_bracket_turned_and_gained_a_small_hole(doc):
    # the hole at x = 10 of the old part is at x = 30 once the new part is turned back and as placed: ambiguous
    return scene(doc, BASE, _turn_y(_SMALL_HOLE), [jspec("revolute", BR["revolute"], (AMB, AMB))])


@case("alignment")
def n2_bracket_turned_and_gained_a_boss(doc):
    return scene(doc, BASE, _turn_y(_SMALL_BOSS), [jspec("cylindrical", BR["cylindrical"], (AMB, AMB))])


@case("alignment")
def n3_control_plain_revision_gained_a_small_hole(doc):
    # not turned: the same revolute joint must stay unambiguous
    return scene(doc, BASE, _SMALL_HOLE, [jspec("revolute", BR["revolute"], BR["revolute"])], still=True)


@case("alignment")
def n4_control_plain_revision_gained_a_boss(doc):
    return scene(doc, BASE, _SMALL_BOSS, [jspec("cylindrical", BR["cylindrical"], BR["cylindrical"])], still=True)


# --- the same question asked of the referenced elements (r041): a symmetric plate turned 180 degrees about Y that gained an
# off-centre hole. Faces are explained alike as placed and as turned, but the hole's rim edge goes to opposite rims.

_PLATE = bracket(holes=((20, 10, 3),))
_PLATE_HOLE = bracket(holes=((20, 10, 3), (8, 4, 1.5)))
_PLATE_JOINT = (cy((20, 10, 0), (0, 0, 1), 3), ci((20, 10, 10), (0, 0, 1), 3))


@case("alignment")
def n5_symmetric_plate_turned_and_gained_an_offcentre_hole(doc):
    # as placed the joint's rim edge goes to one rim, turned back to the opposite one (10 mm apart): ambiguous
    return scene(doc, _PLATE, _turn_y(_PLATE_HOLE), [jspec("revolute", _PLATE_JOINT, (AMB, AMB))])


# --- the symmetry rule (r041, second repair): a part symmetric under a proper turn that is replaced by a changed version may
# have been placed or turned, whatever kind of element the joint holds. Every reference is planned under both readings;
# if they differ the alignment is ambiguous, unless every referenced element is the same under both.

def _turn_x(shape):
    return moved(shape, App.Placement(V(0, 0, 0), App.Rotation(V(1, 0, 0), 180), V(20, 10, 5)))


_RIM = ci((20, 10, 10), (0, 0, 1), 3)
_CORNER = vx((40, 20, 10))


@case("alignment")
def n6_symmetric_plate_turned_about_y_edge_only_reference(doc):
    # the rim edge alone, no face: as placed it is the top rim, turned the other one
    return scene(doc, _PLATE, _turn_y(_PLATE_HOLE), [jspec("revolute", (_RIM, _RIM), (AMB, AMB))])


@case("alignment")
def n7_symmetric_plate_turned_about_x_edge_only_reference(doc):
    return scene(doc, _PLATE, _turn_x(_PLATE_HOLE), [jspec("revolute", (_RIM, _RIM), (AMB, AMB))])


@case("alignment")
def n8_symmetric_plate_turned_vertex_only_reference(doc):
    # the corner (40, 20, 10) goes to the opposite corner when turned: 41 mm apart
    return scene(doc, _PLATE, _turn_y(_PLATE_HOLE), [jspec("ball", (_CORNER, _CORNER), (AMB, AMB))])


@case("alignment")
def n9_symmetric_plate_turned_face_and_vertex_reference(doc):
    top = pl((0, 0, 1), 10)
    return scene(doc, _PLATE, _turn_y(_PLATE_HOLE), [jspec("ball", (top, _CORNER), (AMB, AMB))])


@case("alignment")
def n10_symmetric_plate_turned_and_gained_an_offcentre_hole_of_the_same_radius(doc):
    # the new hole has the radius of the joint's hole: the faces trade places under the turn, the rule still asks
    new = _turn_y(bracket(holes=((20, 10, 3), (8, 4, 3))))
    return scene(doc, _PLATE, new, [jspec("revolute", _PLATE_JOINT, (AMB, AMB))])


@case("alignment")
def n11_bracket_turned_and_lost_a_chamfered_end(doc):
    # not symmetric, overlap 0.81 as placed and 0.78 turned: the near twin check has to reach that far
    return scene(doc, BASE, _turn_y(bracket(chamfer_end=True)), [jspec("revolute", BR["revolute"], (AMB, AMB))])


@case("alignment")
def n12_symmetric_plate_gained_an_offcentre_hole_not_turned(doc):
    # not turned, but the plate is symmetric and the hole's rim edge is not invariant: nothing in the geometry
    # says it was not turned, so it is reported
    return scene(doc, _PLATE, _PLATE_HOLE, [jspec("revolute", _PLATE_JOINT, (AMB, AMB))])


@case("alignment")
def n13_control_symmetric_plate_gained_a_hole_reference_is_the_centre_hole(doc):
    # every turn of the plate maps the centre hole's cylinder onto itself: the readings agree, matched
    d = (cy((20, 10, 0), (0, 0, 1), 3),) * 2
    return scene(doc, _PLATE, _PLATE_HOLE, [jspec("cylindrical", d, d)], still=True)


@case("alignment")
def n14_control_asymmetric_bracket_gained_a_tiny_hole_far_away(doc):
    return scene(doc, BASE, bracket(holes=((10, 6, 3), (36, 16, 1))), [jspec("revolute", BR["revolute"], BR["revolute"])],
                 still=True)


@case("alignment")
def n15_control_symmetric_plate_resized_keeping_the_symmetry(doc):
    # 40 x 20 becomes 50 x 25 about the same corner, the hole stays at the new centre: the new plate is symmetric under every
    # turn too, so placed and turned are the same body
    new = bracket(50, 25, 10, holes=((25, 12.5, 3),))
    return scene(doc, _PLATE, new, [jspec("revolute", _PLATE_JOINT, (cy((25, 12.5, 0), (0, 0, 1), 3), ci((25, 12.5, 10), (0, 0, 1), 3)))])


@case("alignment")
def n16_control_symmetric_plate_made_thicker(doc):
    # 10 becomes 14 thick, hole still centred: the new plate is symmetric under every turn about its own centre, so placed
    # and turned are the same body and the rim edge follows the top face
    new = bracket(40, 20, 14, holes=((20, 10, 3),))
    return scene(doc, _PLATE, new, [jspec("revolute", _PLATE_JOINT, (cy((20, 10, 0), (0, 0, 1), 3), ci((20, 10, 14), (0, 0, 1), 3)))])


# --- delete_old must not destroy what is still used --------------------------------------------

def _other_user(doc, target, mode_name="Assembly2"):
    asm2 = doc.addObject("Assembly::AssemblyObject", mode_name)
    lk = asm2.newObject("App::Link", "OtherUse")
    lk.LinkedObject = target
    return lk


@case("delete_safety")
def t6_delete_old_direct_part_used_by_another_assembly(doc):
    b = scene(doc, BASE, _BOSSED, [_same("revolute")], mode="direct", still=True)
    _other_user(doc, b.old)
    doc.recompute()
    b.apply_kwargs = {"delete_old": True}
    b.expect_kept = ["Old"]
    return b


@case("delete_safety")
def t7_delete_old_source_linked_by_another_assembly(doc):
    b = scene(doc, BASE, _BOSSED, [_same("revolute")], mode="source", still=True)
    _other_user(doc, b.old)
    doc.recompute()
    b.apply_kwargs = {"delete_old": True}
    b.expect_kept = ["Old"]
    return b


@case("delete_safety")
def t8_delete_old_direct_part_used_by_an_expression(doc):
    b = scene(doc, BASE, _BOSSED, [_same("revolute")], mode="direct", still=True)
    holder = doc.addObject("Part::Box", "Holder")
    holder.setExpression("Length", "Old.Shape.BoundBox.XLength")
    doc.recompute()
    b.apply_kwargs = {"delete_old": True}
    b.expect_kept = ["Old"]
    return b


@case("delete_safety")
def t9_delete_old_direct_part_nobody_else_uses(doc):
    b = scene(doc, BASE, _BOSSED, [_same("revolute")], mode="direct", still=True)
    b.apply_kwargs = {"delete_old": True}
    b.expect_deleted = ["Old"]
    return b


def _body_scene(doc, other_user):
    """A PartDesign body (a sketch and a pad: a 40 x 20 x 10 box) replaced by a box with a boss."""
    body = doc.addObject("PartDesign::Body", "Body")
    sk = body.newObject("Sketcher::SketchObject", "Sketch")
    pts = [V(0, 0, 0), V(40, 0, 0), V(40, 20, 0), V(0, 20, 0)]
    for i in range(4):
        sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]))
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Profile = sk
    pad.Length = 10
    doc.recompute()
    old_shape = solid(body.Shape)
    new = bracket(holes=(), bosses=((0, 5, 5, 10, 4),))
    # the body is a plain plate, symmetric under half turns: the caller says the revision kept its frame
    b = scene(doc, old_shape, new, [jspec("planar", BR["planar"], BR["planar"])], mode="direct", still=True, old_obj=body,
              kwargs=given_identity())
    if other_user:
        lk = doc.addObject("App::Link", "PadUser")
        lk.LinkedObject = pad
    doc.recompute()
    b.apply_kwargs = {"delete_old": True}
    return b


@case("delete_safety")
def t10_delete_old_body_nobody_else_uses(doc):
    b = _body_scene(doc, False)
    b.expect_deleted = ["Body", "Sketch", "Pad"]
    return b


@case("delete_safety")
def t11_delete_old_body_whose_pad_is_linked_elsewhere(doc):
    b = _body_scene(doc, True)
    b.expect_kept = ["Body"]
    return b


def all_case_names():
    return list(CASES)
