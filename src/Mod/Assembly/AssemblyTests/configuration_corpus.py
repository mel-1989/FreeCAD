# SPDX-License-Identifier: LGPL-2.1-or-later
"""Configuration corpus (G06 slice 5a): deterministic assemblies with an analytic truth per configuration.

Every case builds an assembly (``build_case(doc, name)``) and returns a ``Built``: the assembly, its
components (``Comp``: the object, the interference keys it contributes, the label of its BOM row, the box
it fills), the configurations to make (``Cfg``: the ``create`` arguments and, per configuration, the expected
placement of every component, the expected suppressed paths and the expected set of cut components) and a
``sequence`` of activations the suite walks (``None`` is the default). The truth is closed form and never read
back from a solve of the same configuration:

- a slider at distance d: the block sits at x = d on its rail (the distance joint measures from the rail's frame);
- a revolute at angle t: the arm turns by -t about the hinge (the angle joint's frame convention, checked in
  the suite against a plain build of the mechanism at the target value);
- a slider-crank: crank pin at O + Rz(-t)(r, 0), rod end at x = pin.x + sqrt(L^2 - pin.y'^2), the rod turned by
  atan2 of its direction;
- a part jointed to a ground plate face by a face-to-face fixed joint: its base is the face centre of the plate
  minus the centre of the part's matching face.

Parts that lose every joint keep the placement they had before the activation (the solver never moves a part
that no joint reaches).
"""

import math

import FreeCAD as App
import Part

import AssemblyFeature
import JointObject

V = App.Vector
P = App.Placement
R = App.Rotation
ZX = R(V(0, 1, 0), 90)       # z axis along +x
CASES = {}


def case(klass, name=None):
    def deco(fn):
        CASES[name or fn.__name__] = (klass, fn)
        return fn
    return deco


class Comp:
    def __init__(self, path, obj, source, keys=None, bom=True, parent=None, box=None, kind="link"):
        self.path = path
        self.obj = obj
        self.source = source            # label of the BOM row
        self.keys = keys if keys is not None else [path]
        self.bom = bom
        self.parent = parent            # path of the sub-assembly the BOM row sits under
        self.box = box                  # (l, w, h) of the solid it contributes, None for a sub-assembly
        self.kind = kind


class Cfg:
    def __init__(self, name, create, pl=None, suppressed=(), cut=None, unsuppressed_joints=(), alt=None):
        self.name = name
        self.create = create
        self.pl = pl or {}               # object name -> expected Placement (missing: the default)
        self.suppressed = list(suppressed)
        self.cut = cut                   # expected set of cut component paths per feature name
        self.alt = alt or {}
        self.volume = {}                 # component path -> volume of what it holds (alternative parts)
        self.joint_outcome = None
        self.removed_keys = None         # existing assemblies: interference keys the configuration removes


class Built:
    def __init__(self, doc, asm):
        self.doc = doc
        self.asm = asm
        self.comps = []
        self.cfgs = {}
        self.sequence = []
        self.default_pl = {}
        self.features = []
        self.parts_docs = []
        self.notes = ""
        self.klass = ""
        self.name = ""
        self.ext_part = None             # name of a part object that lives in another document
        self.bom_group = None

    def add(self, cfg):
        self.cfgs[cfg.name] = cfg
        return cfg


# ---------------------------------------------------------------- construction

def src_box(doc, name, l, w, h, visible=False):
    o = doc.addObject("Part::Box", name)
    o.Length, o.Width, o.Height = l, w, h
    o.Visibility = visible
    return o


def new_assembly(doc):
    asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
    jg = asm.newObject("Assembly::JointGroup", "Joints")
    return asm, jg


def add_bom(asm):
    bg = asm.newObject("Assembly::BomGroup", "Bom")
    bom = bg.newObject("Assembly::BomObject", "BOM")
    bom.columnsNames = ["Index", "Name", "Quantity"]
    return bom


def jt(jg, name, typ, r1, p1, r2, p2, sub1="Face1", sub2="Face1", **kw):
    """A joint with explicit joint frames in the parts' own frames."""
    j = jg.newObject("App::FeaturePython", name)
    JointObject.Joint(j, JointObject.JointTypes.index(typ))
    j.Detach1 = True
    j.Detach2 = True
    j.Reference1 = (r1, [sub1])
    j.Reference2 = (r2, [sub2])
    j.Placement1 = p1
    j.Placement2 = p2
    for k, v in kw.items():
        setattr(j, k, v)
    return j


def ground(jg, obj, name="Grounded"):
    gj = jg.newObject("App::FeaturePython", name)
    JointObject.GroundedJoint(gj, obj)
    return gj


def finish(b, bom=True):
    """Recompute, solve and record the default placements."""
    b.doc.recompute()
    b.asm.solve()
    b.doc.recompute()
    import AssemblyConfiguration as ac
    b.default_pl = {o.Name: P(o.Placement) for o in ac._components(b.asm)}
    return b


def rz(angle_deg):
    return R(V(0, 0, 1), angle_deg)


def rot_base(hinge, local, angle_deg):
    """Base of a body whose local point ``local`` sits at ``hinge`` after turning by ``angle_deg`` about z."""
    return hinge - rz(angle_deg).multVec(local)


# ------------------------------------------------------------------ mechanisms

def _mech_base(doc):
    asm, jg = new_assembly(doc)
    G = asm.newObject("Part::Box", "G")
    G.Length, G.Width, G.Height = 200, 100, 10
    ground(jg, G)
    return asm, jg, G


def _box_in(asm, n, l, w, h, pl):
    b = asm.newObject("Part::Box", n)
    b.Length, b.Width, b.Height = l, w, h
    b.Placement = pl
    return b


def slider_case(doc, d_target, d_default=60.0):
    asm, jg, G = _mech_base(doc)
    p = _box_in(asm, "P", 10, 10, 10, P(V(d_default, 20, 10), R()))
    jt(jg, "Slide", "Slider", G, P(V(0, 25, 15), ZX), p, P(V(0, 5, 5), ZX))
    jt(jg, "Dist", "Distance", G, P(V(0, 25, 15), ZX), p, P(V(0, 5, 5), ZX), Distance=d_default)
    add_bom(asm)
    b = Built(doc, asm)
    b.comps = [Comp("G", G, "G", box=(200, 100, 10)), Comp("P", p, "P", box=(10, 10, 10))]
    finish(b)
    b.add(Cfg("D%g" % d_target, {"joints": {"Dist": {"Distance": d_target}}},
              pl={"P": P(V(d_target, 20, 10), R())}))
    b.sequence = ["D%g" % d_target, None, "D%g" % d_target]
    return b


def revolute_case(doc, theta, th_default=0.0):
    asm, jg, G = _mech_base(doc)
    hinge = V(50, 50, 10)
    c5 = V(5, 5, 0)
    arm = _box_in(asm, "Arm", 60, 10, 10, P(rot_base(hinge, c5, -th_default), rz(-th_default)))
    jt(jg, "Hinge", "Revolute", G, P(hinge, R()), arm, P(c5, R()))
    jt(jg, "Ang", "Angle", G, P(hinge, ZX), arm, P(V(5, 5, 5), ZX), Angle=th_default)
    add_bom(asm)
    b = Built(doc, asm)
    b.comps = [Comp("G", G, "G", box=(200, 100, 10)), Comp("Arm", arm, "Arm", box=(60, 10, 10))]
    finish(b)
    b.add(Cfg("T%g" % theta, {"joints": {"Ang": {"Angle": theta}}},
              pl={"Arm": P(rot_base(hinge, c5, -theta), rz(-theta))}))
    b.sequence = ["T%g" % theta, None, "T%g" % theta]
    return b


def crank_truth(theta, O=V(30, 50, 15), r=20.0, L=60.0):
    t = -theta
    pin = O + rz(t).multVec(V(r, 0, 0))
    sx = pin.x + math.sqrt(L * L - (pin.y - O.y) ** 2)
    psi = math.degrees(math.atan2(O.y - pin.y, sx - pin.x))
    c5 = V(5, 5, 5)
    return {"Crank": P(O - rz(t).multVec(c5), rz(t)),
            "Rod": P(pin - rz(psi).multVec(c5), rz(psi)),
            "Slide": P(V(sx - 5, O.y - 5, 10), R())}


def slider_crank_case(doc, theta, th_default=0.0):
    asm, jg, G = _mech_base(doc)
    r, L, O, c5 = 20.0, 60.0, V(30, 50, 15), V(5, 5, 5)
    d = crank_truth(th_default)
    crank = _box_in(asm, "Crank", r + 10, 10, 10, d["Crank"])
    rod = _box_in(asm, "Rod", L + 10, 10, 10, d["Rod"])
    slid = _box_in(asm, "Slide", 10, 10, 10, d["Slide"])
    jt(jg, "CrankHinge", "Revolute", G, P(O, R()), crank, P(c5, R()))
    jt(jg, "CrankAng", "Angle", G, P(O, ZX), crank, P(c5, ZX), Angle=th_default)
    jt(jg, "Pin", "Revolute", crank, P(V(5 + r, 5, 5), R()), rod, P(c5, R()))
    jt(jg, "RodEnd", "Revolute", rod, P(V(5 + L, 5, 5), R()), slid, P(c5, R()))
    jt(jg, "Rail", "Slider", G, P(V(0, 50, 15), ZX), slid, P(V(0, 5, 5), ZX))
    add_bom(asm)
    b = Built(doc, asm)
    b.comps = [Comp("G", G, "G", box=(200, 100, 10)), Comp("Crank", crank, "Crank", box=(r + 10, 10, 10)),
               Comp("Rod", rod, "Rod", box=(L + 10, 10, 10)), Comp("Slide", slid, "Slide", box=(10, 10, 10))]
    finish(b)
    b.add(Cfg("C%g" % theta, {"joints": {"CrankAng": {"Angle": theta}}}, pl=crank_truth(theta)))
    b.sequence = ["C%g" % theta, None, "C%g" % theta]
    return b


for _d in (10.0, 25.0, 80.0, 0.5):
    CASES["slider_d%g" % _d] = ("joint_value", (lambda d: (lambda doc: slider_case(doc, d)))(_d))
for _t in (30.0, 90.0, 45.0, 175.0):
    CASES["revolute_t%g" % _t] = ("joint_value", (lambda t: (lambda doc: revolute_case(doc, t)))(_t))
for _t in (30.0, 45.0, 60.0, 75.0):
    CASES["slider_crank_t%g" % _t] = ("joint_value", (lambda t: (lambda doc: slider_crank_case(doc, t)))(_t))


# ------------------------------------------------------------------ the rig

SPOTS = {"A": (20, 20), "B": (50, 20)}
C_RAIL_Y = 80.0       # C slides along x on a rail at this y
C_DEFAULT_D = 70.0


def rig(doc, with_features=False, array=False, sub=False, ext_doc=None):
    """Ground plate GL (grounded); A, B, C fixed to its top; D fixed on top of A. Links to shared sources."""
    asm, jg = new_assembly(doc)
    host = ext_doc or doc
    s_plate = src_box(host, "PlateSrc", 100, 100, 10)
    s_cube = src_box(host, "CubeSrc", 10, 10, 10)
    s_small = src_box(host, "SmallSrc", 6, 6, 6)
    gl = asm.newObject("App::Link", "GL")
    gl.LinkedObject = s_plate
    ground(jg, gl)
    b = Built(doc, asm)
    b.comps.append(Comp("GL", gl, "PlateSrc", box=(100, 100, 10)))
    links = {}
    for n, (x, y) in SPOTS.items():
        lk = asm.newObject("App::Link", "L" + n)
        lk.LinkedObject = s_cube
        lk.Placement = P(V(x - 5, y - 5, 10), R())
        links[n] = lk
        jt(jg, "J" + n, "Fixed", gl, P(V(x, y, 10), R()), lk, P(V(5, 5, 0), R()))
        b.comps.append(Comp("L" + n, lk, "CubeSrc", box=(10, 10, 10)))
    lc = asm.newObject("App::Link", "LC")
    lc.LinkedObject = s_cube
    lc.Placement = P(V(C_DEFAULT_D, C_RAIL_Y - 5, 10), R())
    links["C"] = lc
    jt(jg, "JC", "Slider", gl, P(V(0, C_RAIL_Y, 15), ZX), lc, P(V(0, 5, 5), ZX))
    jt(jg, "JCd", "Distance", gl, P(V(0, C_RAIL_Y, 15), ZX), lc, P(V(0, 5, 5), ZX), Distance=C_DEFAULT_D)
    b.comps.append(Comp("LC", lc, "CubeSrc", box=(10, 10, 10)))
    ld = asm.newObject("App::Link", "LD")
    ld.LinkedObject = s_small
    ld.Placement = P(V(20 - 3, 20 - 3, 20), R())
    jt(jg, "JD", "Fixed", links["A"], P(V(5, 5, 10), R()), ld, P(V(3, 3, 0), R()))
    b.comps.append(Comp("LD", ld, "SmallSrc", box=(6, 6, 6)))
    b.src = {"plate": s_plate, "cube": s_cube, "small": s_small}
    b.links = dict(links, D=ld, G=gl)
    b.jg = jg
    add_bom(asm)
    return b


def rig_feature(b):
    """A cut through A, B and D only: a box over x 10..60, y 10..30, z 11..23 (A, B and D reach it; D loses its lower half... its top half)."""
    f = AssemblyFeature.make_cut(b.asm, AssemblyFeature.box_tool((50, 20, 12), pos=(10, 10, 11)), name="Cut1")
    b.features = ["Cut1"]
    return f


def comp_path_hit(path, sup):
    return any(path == s or path.startswith(s + ".") for s in sup)


def _rig_sup_case(doc, sup, name=None, parts_doc=False):
    b = rig(doc)
    finish(b)
    b.add(Cfg("S_" + "_".join(sup), {"suppress": list(sup)}, suppressed=sup))
    b.sequence = ["S_" + "_".join(sup), None, "S_" + "_".join(sup)]
    return b


for _sup in (("LA",), ("LB",), ("LD",), ("GL",), ("LA", "LB"), ("LA", "LB", "LC", "LD"), ("LC", "LD"),
             ("GL", "LD")):
    CASES["suppress_" + "_".join(_sup)] = ("suppress", (lambda s: (lambda doc: _rig_sup_case(doc, s)))(_sup))


@case("suppress")
def suppress_with_feature_dependency(doc):
    """Suppress D, which sits on A: A's joint to D is the only joint to go."""
    return _rig_sup_case(doc, ("LD",))


# ---- feature cases

@case("feature")
def feature_suppressed(doc):
    b = rig(doc)
    rig_feature(b)
    finish(b)
    b.add(Cfg("NoCut", {"features": {"Cut1": True}}, cut={"Cut1": set()}))
    b.default_cut = {"Cut1": {"LA", "LB", "LD"}}
    b.sequence = ["NoCut", None, "NoCut"]
    return b


@case("feature")
def feature_and_component(doc):
    """The suppressed component is one the feature would cut: the feature then cuts the rest."""
    b = rig(doc)
    rig_feature(b)
    finish(b)
    b.add(Cfg("NoA", {"suppress": ["LA"]}, suppressed=["LA"], cut={"Cut1": {"LB", "LD"}}))
    b.default_cut = {"Cut1": {"LA", "LB", "LD"}}
    b.sequence = ["NoA", None, "NoA"]
    return b


@case("feature")
def feature_untouched_part(doc):
    """The suppressed component is outside the cut; the feature still cuts A, B and D."""
    b = rig(doc)
    rig_feature(b)
    finish(b)
    b.add(Cfg("NoC", {"suppress": ["LC"]}, suppressed=["LC"], cut={"Cut1": {"LA", "LB", "LD"}}))
    b.default_cut = {"Cut1": {"LA", "LB", "LD"}}
    b.sequence = ["NoC", None, "NoC"]
    return b


# ---- alternative parts

def _alt_case(doc, alt_name, shape_fn, expect):
    """One part M linked to the plate by a face-to-face fixed joint (plate top to the part's bottom face)."""
    asm, jg = new_assembly(doc)
    s_plate = src_box(doc, "PlateSrc", 100, 100, 10)
    s_m = src_box(doc, "MSrc", 10, 10, 10)
    alt = doc.addObject("Part::Feature", alt_name)
    alt.Shape = shape_fn()
    alt.Visibility = False
    gl = asm.newObject("App::Link", "GL")
    gl.LinkedObject = s_plate
    ground(jg, gl)
    lm = asm.newObject("App::Link", "LM")
    lm.LinkedObject = s_m
    lm.Placement = P(V(45, 45, 10), R())
    doc.recompute()
    # explicit joint frames (the face frames of a Part::Box come out as the identity in this build, so a joint
    # that follows its faces cannot give a closed-form truth); the references still name the faces the matcher
    # has to carry over to the alternative part
    jt(jg, "JM", "Fixed", gl, P(V(50, 50, 10), R()), lm, P(V(5, 5, 0), R()), sub1="Face6", sub2="Face5")
    add_bom(asm)
    b = Built(doc, asm)
    b.comps = [Comp("GL", gl, "PlateSrc", box=(100, 100, 10)), Comp("LM", lm, "MSrc", box=(10, 10, 10))]
    finish(b)
    exp = expect()
    cfg = b.add(Cfg("Alt", {"alternatives": {"LM": alt_name}}, alt={"LM": alt_name}))
    cfg.pl = exp["pl"]
    cfg.joint_outcome = exp["joint"]
    cfg.volume = {"LM": exp["vol"]}
    b.sequence = ["Alt", None, "Alt"]
    return b


def _box_shape(l, w, h):
    return Part.makeBox(l, w, h)


@case("alternative")
def alt_same_geometry(doc):
    return _alt_case(doc, "MAlt", lambda: _box_shape(10, 10, 10),
                     lambda: {"pl": {}, "joint": "remapped", "vol": 1000.0})


@case("alternative")
def alt_resized(doc):
    # the bottom face (normal -z) keeps its plane; the joint frame is explicit, so the cube stays where it was
    return _alt_case(doc, "MAlt", lambda: _box_shape(20, 14, 10),
                     lambda: {"pl": {}, "joint": "remapped", "vol": 2800.0})


@case("alternative")
def alt_taller(doc):
    return _alt_case(doc, "MAlt", lambda: _box_shape(10, 10, 24),
                     lambda: {"pl": {}, "joint": "remapped", "vol": 2400.0})


@case("alternative")
def alt_unmatched(doc):
    """A sphere has no planar face: the joint's references cannot match, the joint is suppressed and the
    component stays where it was."""
    return _alt_case(doc, "MAlt", lambda: Part.makeSphere(5),
                     lambda: {"pl": {}, "joint": "suppressed", "vol": 4.0 / 3.0 * math.pi * 125.0})


# ---- joint offsets

# ---- combinations and several configurations on one assembly

@case("combined")
def three_configs(doc):
    b = rig(doc)
    finish(b)
    b.add(Cfg("NoB", {"suppress": ["LB"]}, suppressed=["LB"]))
    b.add(Cfg("Slide", {"joints": {"JCd": {"Distance": 40.0}}}, pl={"LC": P(V(40, C_RAIL_Y - 5, 10), R())}))
    b.add(Cfg("NoAD", {"suppress": ["LA", "LD"]}, suppressed=["LA", "LD"]))
    b.sequence = ["NoB", "Slide", "NoAD", "NoB", None, "Slide", "NoB"]
    return b


@case("combined")
def all_kinds(doc):
    """A joint value, a suppression and a feature suppression in one configuration."""
    b = rig(doc)
    rig_feature(b)
    finish(b)
    b.add(Cfg("All", {"suppress": ["LB"], "joints": {"JCd": {"Distance": 25.0}},
                      "features": {"Cut1": True}}, suppressed=["LB"],
              pl={"LC": P(V(25, C_RAIL_Y - 5, 10), R())}, cut={"Cut1": set()}))
    b.default_cut = {"Cut1": {"LA", "LB", "LD"}}
    b.sequence = ["All", None, "All"]
    return b


@case("combined")
def alt_and_suppress(doc):
    """An alternative part for the cube B and the suppression of C in one configuration."""
    b = rig(doc)
    big = doc.addObject("Part::Feature", "BigCube")
    big.Shape = Part.makeBox(10, 10, 10)
    big.Visibility = False
    finish(b)
    b.add(Cfg("AltB", {"suppress": ["LC"], "alternatives": {"LB": "BigCube"}}, suppressed=["LC"],
              alt={"LB": "BigCube"}))
    b.cfgs["AltB"].joint_outcome = "remapped"
    b.cfgs["AltB"].volume = {"LB": 1000.0}
    b.sequence = ["AltB", None, "AltB"]
    return b


# ---- link array, sub-assembly, separate part document

@case("link")
def array_element(doc):
    asm, jg = new_assembly(doc)
    s_plate = src_box(doc, "PlateSrc", 100, 100, 10)
    s_cube = src_box(doc, "CubeSrc", 10, 10, 10)
    gl = asm.newObject("App::Link", "GL")
    gl.LinkedObject = s_plate
    ground(jg, gl)
    arr = asm.newObject("App::Link", "Arr")
    arr.LinkedObject = s_cube
    arr.ElementCount = 3
    arr.ShowElement = True
    doc.recompute()
    for i, el in enumerate(arr.ElementList):
        el.Placement = P(V(15 + 30 * i, 15, 10), R())
        jt(jg, "JE%d" % i, "Fixed", gl, P(V(20 + 30 * i, 20, 10), R()), el, P(V(5, 5, 0), R()))
    add_bom(asm)
    b = Built(doc, asm)
    b.comps = [Comp("GL", gl, "PlateSrc", box=(100, 100, 10)), Comp("Arr", arr, "CubeSrc", bom=False, keys=[], kind="array")]
    for i, el in enumerate(arr.ElementList):
        b.comps.append(Comp(el.Name, el, "CubeSrc", keys=["Arr.%d" % i], bom=False, box=(10, 10, 10), kind="element"))
    b.arr = arr
    finish(b)
    mid = arr.ElementList[1].Name
    b.add(Cfg("NoMid", {"suppress": [mid]}, suppressed=[mid]))
    b.sequence = ["NoMid", None, "NoMid"]
    return b


@case("link")
def array_two_elements(doc):
    b = array_element(doc)
    b.cfgs.clear()
    names = [e.Name for e in b.arr.ElementList]
    b.add(Cfg("Ends", {"suppress": [names[0], names[2]]}, suppressed=[names[0], names[2]]))
    b.sequence = ["Ends", None, "Ends"]
    return b


def _sub_scene(doc):
    asm, jg = new_assembly(doc)
    s_plate = src_box(doc, "PlateSrc", 100, 100, 10)
    s_cube = src_box(doc, "CubeSrc", 10, 10, 10)
    gl = asm.newObject("App::Link", "GL")
    gl.LinkedObject = s_plate
    ground(jg, gl)
    sub = asm.newObject("Assembly::AssemblyObject", "Sub")
    sl = sub.newObject("App::Link", "SL")
    sl.LinkedObject = s_cube
    sl2 = sub.newObject("App::Link", "SL2")
    sl2.LinkedObject = s_cube
    sl2.Placement = P(V(20, 0, 0), R())
    sub.Placement = P(V(30, 20, 10), R())
    doc.recompute()
    jt(jg, "JS", "Fixed", gl, P(V(35, 25, 10), R()), sub, P(V(5, 5, 0), R()), sub2="SL.Face1")
    add_bom(asm)
    b = Built(doc, asm)
    b.comps = [Comp("GL", gl, "PlateSrc", box=(100, 100, 10)),
               Comp("Sub", sub, "Sub", keys=[], box=None, kind="sub"),
               Comp("Sub.SL", sl, "CubeSrc", parent="Sub", box=(10, 10, 10)),
               Comp("Sub.SL2", sl2, "CubeSrc", parent="Sub", box=(10, 10, 10))]
    finish(b)
    return b


@case("link")
def sub_assembly_child(doc):
    b = _sub_scene(doc)
    b.add(Cfg("NoSL2", {"suppress": ["Sub.SL2"]}, suppressed=["Sub.SL2"]))
    b.sequence = ["NoSL2", None, "NoSL2"]
    return b


@case("link")
def sub_assembly_whole(doc):
    b = _sub_scene(doc)
    b.add(Cfg("NoSub", {"suppress": ["Sub"]}, suppressed=["Sub"]))
    b.sequence = ["NoSub", None, "NoSub"]
    return b


@case("link")
def sub_assembly_jointed_child(doc):
    """The child the parent's joint reaches (SL) is suppressed: the joint goes with it and the sub-assembly
    stays where the last solve left it."""
    b = _sub_scene(doc)
    b.add(Cfg("NoSL", {"suppress": ["Sub.SL"]}, suppressed=["Sub.SL"]))
    b.sequence = ["NoSL", None, "NoSL"]
    return b


@case("link")
def part_in_separate_document(doc):
    """The parts live in a second document (saved, never modified); the assembly links to them."""
    import os
    import tempfile
    tmp = tempfile.mkdtemp(prefix="cfgparts")
    pdoc = App.newDocument("CfgParts")
    pdoc.saveAs(os.path.join(tmp, "CfgParts.FCStd"))
    doc.saveAs(os.path.join(tmp, "CfgAsm.FCStd"))
    b = rig(doc, ext_doc=pdoc)
    pdoc.save()
    b.parts_docs.append(pdoc)
    b.tmpdir = tmp
    finish(b)
    b.add(Cfg("NoA", {"suppress": ["LA"]}, suppressed=["LA"]))
    b.add(Cfg("Slide", {"joints": {"JCd": {"Distance": 55.0}}},
              pl={"LC": P(V(55, C_RAIL_Y - 5, 10), R())}))
    b.sequence = ["NoA", "Slide", "NoA", None]
    return b


# ---- from the Assembly workbench's own tests and example

@case("workbench_tests")
def wb_rack_slider(doc):
    """TestCore.test_rack_pinion_with_slider_offset: the rack hangs on a slider joint to the ground; the
    configuration suppresses the rack."""
    from AssemblyTests import replace_corpus as rcc
    rb = rcc.CASES["core_rack_pinion_slider"].fn(doc)
    return _wrap_existing(doc, rb.asm, "NoRack", ["Rack"])


@case("workbench_tests")
def wb_array_joint(doc):
    """TestCore._check_generated_array_joint: a link array with a joint between elements 1 and 2; the
    configuration suppresses element 2."""
    from AssemblyTests import replace_corpus as rcc
    rb = rcc.CASES["core_generated_array_joint"].fn(doc)
    el = rb.asm.getObject("Array").ElementList if hasattr(rb.asm, "getObject") else None
    arr = doc.getObject("Array")
    return _wrap_existing(doc, rb.asm, "NoEl2", [arr.ElementList[2].Name])


@case("workbench_tests")
def wb_example_assembly(doc):
    """data/examples/AssemblyExample.FCStd: the first component of the example is suppressed."""
    from AssemblyTests import replace_corpus as rcc
    import os
    path = rcc.example_path()
    if path is None:
        raise RuntimeError("the example assembly is missing")
    App.closeDocument(doc.Name)
    d = App.openDocument(path)
    d.UndoMode = 1
    asm = [o for o in d.Objects if o.TypeId == "Assembly::AssemblyObject"][0]
    comps = [o for o in asm.Group if o.TypeId not in ("Assembly::JointGroup", "Assembly::ViewGroup",
                                                       "Assembly::BomGroup", "Assembly::SimulationGroup",
                                                       "Assembly::SnapshotGroup", "App::Origin")
             and hasattr(o, "Placement")]
    return _wrap_existing(d, asm, "NoFirst", [comps[0].Name])


def _wrap_existing(doc, asm, cfg_name, suppress):
    import AssemblyConfiguration as ac
    b = Built(doc, asm)
    doc.UndoMode = 1
    finish(b)
    b.comps = []
    for o in ac._components(asm):
        b.comps.append(Comp(o.Name, o, o.Label, keys=None, bom=False, kind="existing"))
    b.existing = True
    cfg = b.add(Cfg(cfg_name, {"suppress": list(suppress)}, suppressed=list(suppress)))
    cfg.removed_keys = set()
    for path in suppress:
        o = doc.getObject(path.split(".")[-1])
        if o.TypeId == "App::LinkElement":
            arr = [x for x in doc.Objects if x.TypeId == "App::Link" and o in x.ElementList][0]
            cfg.removed_keys.add("%s.%d" % (arr.Name, list(arr.ElementList).index(o)))
        else:
            cfg.removed_keys.add(path)
    b.sequence = [cfg_name, None, cfg_name]
    return b


def build_case(doc, name):
    klass, fn = CASES[name]
    doc.UndoMode = 1
    b = fn(doc)
    b.name = name
    b.klass = klass
    return b
