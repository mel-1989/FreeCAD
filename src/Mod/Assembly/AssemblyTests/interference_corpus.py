# SPDX-License-Identifier: LGPL-2.1-or-later
"""Interference corpus (G06 slice 1a): deterministic assemblies with analytic truths.

Every case is a function ``build(doc, asm) -> truth`` registered with ``@case``. The truth is
derived from the dimensions and placements written into the builder, never from FreeCAD's
own boolean results: boxes by arithmetic, cylinders and spheres by closed formulas
(cylinder cap volume pi r^2 d, Steinmetz bicylinder 16 r^3 / 3, spherical lens
2 pi h^2 (3 r - h) / 3 with h = r - D/2, octant of a ball, regular octagon 2 (sqrt 2 - 1) s^2).

Truth format: ``{"interfering": {(a, b): volume_mm3}, "touching": {(a, b), ...},
"close": {(a, b): distance_mm}, "errors": {path, ...}, "skipped": {path, ...}}`` where a and b are instance paths as the API reports them
(``"A"``, ``"LB"``, ``"Arr.1"``, ``"Sub.Box"``). Pairs not listed are clear. ``errors`` lists visible leaves that must be reported as unresolved,
``skipped`` visible leaves that hold no solid (listed in ``skipped_non_solid``).
"""

import math

import FreeCAD as App

CASES = {}
PI = math.pi
SQ2 = math.sqrt(2.0)
Z = App.Vector(0, 0, 1)


class Case:
    def __init__(self, name, klass, build, params, big, own):
        self.name = name
        self.klass = klass
        self.build = build
        self.params = params
        self.big = big
        self.own = own


def case(klass, name=None, big=False, own=False, **params):
    def deco(fn):
        nm = name or fn.__name__
        CASES[nm] = Case(nm, klass, fn, params, big, own)
        return fn
    return deco


def T(interfering=None, touching=(), close=None, errors=(), skipped=()):
    return {"interfering": dict(interfering or {}), "touching": set(map(frozenset, touching)),
            "close": dict(close or {}), "errors": set(errors), "skipped": set(skipped)}


def P(a, b):
    return frozenset((a, b))


def _norm(truth):
    truth["interfering"] = {frozenset(k): v for k, v in truth["interfering"].items()}
    truth["close"] = {frozenset(k): v for k, v in truth["close"].items()}
    return truth


# ---------------------------------------------------------------- builders

def plc(x=0.0, y=0.0, z=0.0, rot=None, center=None):
    r = rot or App.Rotation()
    return App.Placement(App.Vector(x, y, z), r, center) if center else App.Placement(App.Vector(x, y, z), r)


def box(parent, name, size=(10, 10, 10), pos=(0, 0, 0), rot=None, center=None):
    b = parent.newObject("Part::Box", name)
    b.Length, b.Width, b.Height = size
    b.Placement = plc(*pos, rot=rot, center=center)
    return b


def cyl(parent, name, r, h, pos=(0, 0, 0), rot=None):
    c = parent.newObject("Part::Cylinder", name)
    c.Radius, c.Height = r, h
    c.Placement = plc(*pos, rot=rot)
    return c


def sph(parent, name, r, pos=(0, 0, 0)):
    s = parent.newObject("Part::Sphere", name)
    s.Radius = r
    s.Placement = plc(*pos)
    return s


def source_box(doc, name="Src", size=(10, 10, 10)):
    """A box outside the assembly, hidden, used as the linked object."""
    b = doc.addObject("Part::Box", name)
    b.Length, b.Width, b.Height = size
    b.Visibility = False
    return b


def link(asm, name, target, pos=(0, 0, 0), rot=None):
    lk = asm.newObject("App::Link", name)
    lk.LinkedObject = target
    lk.Placement = plc(*pos, rot=rot)
    return lk


def link_array(doc, asm, name, target, count, pitch, show_element=True):
    arr = asm.newObject("App::Link", name)
    arr.LinkedObject = target
    arr.ElementCount = count
    arr.ShowElement = True
    doc.recompute()
    for i, el in enumerate(arr.ElementList):
        el.Placement = plc(pitch * i)
    if not show_element:
        arr.ShowElement = False  # the placements stay in PlacementList
    doc.recompute()
    return arr


# ------------------------------------------------- box overlaps, depth ladder

def _depth_case(d):
    def build(doc, asm):
        box(asm, "A")
        box(asm, "B", pos=(10 - d, 0, 0))
        return T({("A", "B"): d * 100.0})
    return build


for _d, _tag in ((1e-3, "1e-3"), (1e-2, "1e-2"), (1e-1, "1e-1"), (1.0, "1"), (5.0, "5"), (9.999, "9.999")):
    case("interfering", "box_overlap_depth_" + _tag)(_depth_case(_d))


@case("interfering")
def box_overlap_3axis(doc, asm):
    box(asm, "A")
    box(asm, "B", pos=(7, 8, 9))
    return T({("A", "B"): 6.0})


@case("interfering")
def box_contained(doc, asm):
    box(asm, "A")
    box(asm, "B", (2, 2, 2), (4, 4, 4))
    return T({("A", "B"): 8.0})


@case("interfering")
def box_identical(doc, asm):
    box(asm, "A")
    box(asm, "B")
    return T({("A", "B"): 1000.0})


@case("interfering")
def box_rot90(doc, asm):
    box(asm, "A")
    box(asm, "B", (10, 20, 10), (5, 0, 0), App.Rotation(Z, 90))
    return T({("A", "B"): 500.0})


@case("interfering")
def box_rot45_octagon(doc, asm):
    box(asm, "A")
    box(asm, "B", pos=(0, 0, 0), rot=App.Rotation(Z, 45), center=App.Vector(5, 5, 5))
    return T({("A", "B"): 2 * (SQ2 - 1) * 100.0 * 10.0})


# ------------------------------------------------------- cylinders, spheres

def _cyl_depth(d):
    def build(doc, asm):
        cyl(asm, "A", 5, 10)
        cyl(asm, "B", 5, 10, (0, 0, 10 - d))
        return T({("A", "B"): PI * 25 * d})
    return build


for _d, _tag in ((1e-3, "1e-3"), (0.5, "0.5"), (5.0, "5")):
    case("interfering", "cyl_coaxial_depth_" + _tag)(_cyl_depth(_d))


@case("interfering")
def cyl_steinmetz(doc, asm):
    r = 4.0
    cyl(asm, "A", r, 20, (0, 0, -10))
    cyl(asm, "B", r, 20, (-10, 0, 0), App.Rotation(App.Vector(0, 1, 0), 90))
    return T({("A", "B"): 16.0 * r ** 3 / 3.0})


@case("interfering")
def cyl_inside_box(doc, asm):
    box(asm, "A")
    cyl(asm, "B", 3, 6, (5, 5, 2))
    return T({("A", "B"): PI * 9 * 6})


@case("interfering")
def cyl_quarter_in_box(doc, asm):
    box(asm, "A")
    cyl(asm, "B", 5, 6, (0, 0, 2))
    return T({("A", "B"): PI * 25 * 6 / 4.0})


def _lens(r, d):
    h = d / 2.0
    return 2.0 * PI * h * h * (3 * r - h) / 3.0


def _sph_lens(d):
    def build(doc, asm):
        sph(asm, "A", 5)
        sph(asm, "B", 5, (10 - d, 0, 0))
        return T({("A", "B"): _lens(5.0, d)})
    return build


for _d, _tag in ((1e-3, "1e-3"), (1e-1, "1e-1"), (4.0, "4")):
    case("interfering", "sph_lens_depth_" + _tag)(_sph_lens(_d))


@case("interfering")
def sph_contained(doc, asm):
    sph(asm, "A", 5)
    sph(asm, "B", 2, (1, 1, 1))
    return T({("A", "B"): 4.0 / 3.0 * PI * 8})


@case("interfering")
def sph_octant_in_box(doc, asm):
    box(asm, "A")
    sph(asm, "B", 5)
    return T({("A", "B"): 4.0 / 3.0 * PI * 125 / 8.0})


# ----------------------------------------------------------------- touching

def _touch_box(pos):
    def build(doc, asm):
        box(asm, "A")
        box(asm, "B", pos=pos)
        return T(touching=[("A", "B")])
    return build


for _n, _p in (("face_full", (10, 0, 0)), ("face_partial", (10, 4, 3)),
               ("edge", (10, 10, 0)), ("vertex", (10, 10, 10))):
    case("touching", "touch_box_" + _n)(_touch_box(_p))


@case("touching")
def touch_cyl_tangent_plane(doc, asm):
    box(asm, "A")
    cyl(asm, "B", 5, 10, (0, 5, 15), App.Rotation(App.Vector(0, 1, 0), 90))
    return T(touching=[("A", "B")])


@case("touching")
def touch_sph_point_on_face(doc, asm):
    box(asm, "A")
    sph(asm, "B", 5, (5, 5, 15))
    return T(touching=[("A", "B")])


@case("touching")
def touch_sph_sph_tangent(doc, asm):
    sph(asm, "A", 5)
    sph(asm, "B", 5, (10, 0, 0))
    return T(touching=[("A", "B")])


@case("touching")
def touch_cyl_cyl_line(doc, asm):
    cyl(asm, "A", 5, 10)
    cyl(asm, "B", 5, 10, (10, 0, 0))
    return T(touching=[("A", "B")])


@case("touching")
def touch_cyl_end_on_box(doc, asm):
    box(asm, "A")
    cyl(asm, "B", 3, 6, (5, 5, 10))
    return T(touching=[("A", "B")])


# ---------------------------------------------------------------- near miss

def _gap_box(g):
    def build(doc, asm):
        box(asm, "A")
        box(asm, "B", pos=(10 + g, 0, 0))
        return T(close={("A", "B"): g})
    return build


for _g, _tag in ((1e-3, "1e-3"), (1e-2, "1e-2"), (1e-1, "1e-1"), (1.0, "1"), (10.0, "10")):
    case("near_miss", "near_box_gap_" + _tag, clearance=20.0)(_gap_box(_g))


@case("near_miss", clearance=20.0)
def near_box_diagonal(doc, asm):
    box(asm, "A")
    box(asm, "B", pos=(11, 11, 11))
    return T(close={("A", "B"): math.sqrt(3.0)})


@case("near_miss", clearance=20.0)
def near_sph_gap(doc, asm):
    sph(asm, "A", 5)
    sph(asm, "B", 5, (12.5, 0, 0))
    return T(close={("A", "B"): 2.5})


@case("near_miss", clearance=20.0)
def near_cyl_parallel(doc, asm):
    cyl(asm, "A", 5, 10)
    cyl(asm, "B", 5, 10, (12, 0, 0))
    return T(close={("A", "B"): 2.0})


@case("near_miss", clearance=5.0)
def near_box_rotated(doc, asm):
    box(asm, "A")
    box(asm, "B", pos=(20.5, 0, 0), rot=App.Rotation(Z, 90))
    return T(close={("A", "B"): 0.5})


@case("near_miss", clearance=0.5)
def near_beyond_clearance(doc, asm):
    box(asm, "A")
    box(asm, "B", pos=(11, 0, 0))
    return T()


@case("near_miss", clearance=1.0)
def near_sph_diagonal_clear(doc, asm):
    """Bounding boxes overlap but the true gap (8 sqrt 2 - 10 = 1.31) exceeds the clearance."""
    sph(asm, "A", 5)
    sph(asm, "B", 5, (8, 8, 0))
    return T()


# -------------------------------------------------------------------- links

@case("link")
def link_offset_overlap(doc, asm):
    src = source_box(doc)
    box(asm, "A")
    link(asm, "LB", src, (7, 0, 0))
    return T({("A", "LB"): 300.0})


@case("link")
def link_rotated_overlap(doc, asm):
    src = source_box(doc)
    box(asm, "A")
    link(asm, "LB", src, (5, 0, 0), App.Rotation(Z, 90))
    return T({("A", "LB"): 500.0})


@case("link")
def link_two_instances_of_one_part(doc, asm):
    src = source_box(doc)
    link(asm, "L1", src)
    link(asm, "L2", src, (8, 0, 0))
    link(asm, "L3", src, (100, 0, 0))
    return T({("L1", "L2"): 200.0})


@case("link")
def link_to_part_same_part_ignored(doc, asm):
    part = doc.addObject("App::Part", "PartSrc")
    box(part, "b1")
    box(part, "b2", pos=(5, 0, 0))
    link(asm, "LP", part)
    box(asm, "X", pos=(-5, 0, 0))
    return T({("X", "LP.b1"): 500.0}, touching=[("X", "LP.b2")])


@case("link")
def link_part_placed(doc, asm):
    part = doc.addObject("App::Part", "PartSrc")
    box(part, "b1", (4, 4, 4))
    box(part, "b2", (4, 4, 4), (6, 0, 0))
    link(asm, "LP", part, (20, 0, 0), App.Rotation(Z, 180))
    box(asm, "X", (4, 4, 4), (12, -4, 0))
    # LP maps part-local x to -x about (20,0,0): b1 -> x [16,20], y [-4,0]; b2 -> x [10,14]
    return T({("X", "LP.b2"): 2 * 4 * 4.0}, touching=[("X", "LP.b1")])


# ------------------------------------------------------------- link arrays

@case("array")
def array_link_overlap(doc, asm):
    arr = link_array(doc, asm, "Arr", source_box(doc), 4, 8)
    return T({("Arr.0", "Arr.1"): 200.0, ("Arr.1", "Arr.2"): 200.0, ("Arr.2", "Arr.3"): 200.0})


@case("array")
def array_link_touching(doc, asm):
    link_array(doc, asm, "Arr", source_box(doc), 4, 10)
    return T(touching=[("Arr.0", "Arr.1"), ("Arr.1", "Arr.2"), ("Arr.2", "Arr.3")])


@case("array", clearance=5.0)
def array_link_gap(doc, asm):
    link_array(doc, asm, "Arr", source_box(doc), 4, 12)
    return T(close={("Arr.0", "Arr.1"): 2.0, ("Arr.1", "Arr.2"): 2.0, ("Arr.2", "Arr.3"): 2.0})


@case("array")
def array_last_element_only(doc, asm):
    link_array(doc, asm, "Arr", source_box(doc), 4, 30)
    box(asm, "X", pos=(95, 0, 0))
    return T({("Arr.3", "X"): 500.0})


@case("array")
def array_first_element_only(doc, asm):
    link_array(doc, asm, "Arr", source_box(doc), 4, 30)
    box(asm, "X", pos=(-5, 0, 0))
    return T({("Arr.0", "X"): 500.0})


@case("array")
def array_all_elements_one_box(doc, asm):
    link_array(doc, asm, "Arr", source_box(doc), 4, 30)
    box(asm, "X", (130, 20, 20), (-5, -5, -5))
    return T({("Arr.%d" % i, "X"): 1000.0 for i in range(4)})


@case("array")
def array_hidden_element(doc, asm):
    arr = link_array(doc, asm, "Arr", source_box(doc), 3, 5)
    arr.ElementList[1].Visibility = False
    return T(touching=[("Arr.0", "Arr.2")])


@case("array")
def array_suppressed_element(doc, asm):
    arr = link_array(doc, asm, "Arr", source_box(doc), 3, 5)
    arr.ElementList[1].Suppressed = True
    doc.recompute()
    return T(touching=[("Arr.0", "Arr.2")])


@case("array")
def array_noshow_hidden_source(doc, asm):
    """ShowElement=False with the corpus convention of a hidden source: the array stays visible."""
    link_array(doc, asm, "Arr", source_box(doc), 3, 8, show_element=False)
    return T({("Arr.0", "Arr.1"): 200.0, ("Arr.1", "Arr.2"): 200.0})


@case("array")
def array_noshow_visible_source(doc, asm):
    src = source_box(doc)
    src.Visibility = True
    link_array(doc, asm, "Arr", src, 3, 8, show_element=False)
    return T({("Arr.0", "Arr.1"): 200.0, ("Arr.1", "Arr.2"): 200.0})


@case("array")
def array_noshow_hidden_element(doc, asm):
    arr = link_array(doc, asm, "Arr", source_box(doc), 3, 5, show_element=False)
    arr.setElementVisible("1", False)
    return T(touching=[("Arr.0", "Arr.2")])


@case("array")
def array_noshow_array_hidden(doc, asm):
    """Hiding the array hides every element, whatever the source says."""
    box(asm, "V1", pos=(100, 0, 0))
    box(asm, "V2", pos=(108, 0, 0))
    src = source_box(doc)
    src.Visibility = True
    link_array(doc, asm, "Arr", src, 3, 8, show_element=False).Visibility = False
    return T({("V1", "V2"): 200.0})


@case("nested")
def assembly_link_instance(doc, asm):
    """An Assembly::AssemblyLink places a copy of another assembly at its own placement."""
    sub = doc.addObject("Assembly::AssemblyObject", "Sub")
    box(sub, "SB", pos=(3, 0, 0))
    al = asm.newObject("Assembly::AssemblyLink", "AL")
    al.LinkedObject = sub
    al.Placement = plc(1000)
    doc.recompute()
    leaf = "AL." + al.getSubObjects()[0].rstrip(".")
    box(asm, "X", pos=(1008, 0, 0))   # AL box spans x 1003..1013, X 1008..1018
    return T({(leaf, "X"): 500.0})


# --------------------------------------------- parts that cannot be checked

@case("unresolved")
def link_deleted_target(doc, asm):
    box(asm, "A")
    box(asm, "B", pos=(5, 0, 0))
    src = source_box(doc, "GoneSrc")
    link(asm, "Broken", src, (2, 0, 0))
    doc.recompute()
    doc.removeObject("GoneSrc")
    return T({("A", "B"): 500.0}, errors=["Broken"])


@case("unresolved")
def null_shape_part(doc, asm):
    box(asm, "A")
    box(asm, "B", pos=(5, 0, 0))
    box(asm, "Bad", size=(0, 10, 10))   # a zero dimension: the recompute fails, the shape is null
    return T({("A", "B"): 500.0}, errors=["Bad"])


@case("unresolved")
def hidden_unresolved_not_an_error(doc, asm):
    """A hidden leaf is not resolved, so a broken hidden part is not reported."""
    vis = _visible_pair(asm)
    box(asm, "Bad", size=(0, 10, 10)).Visibility = False
    return T(vis)


@case("nonsolid")
def face_wire_shell_sketch_listed(doc, asm):
    """Only solids are checked: a face, a wire, a closed shell and a sketch are listed, not tested."""
    import Part
    box(asm, "A")
    f = asm.newObject("Part::Feature", "Face")
    f.Shape = Part.makePlane(10, 10, App.Vector(5, 0, 5))
    w = asm.newObject("Part::Feature", "Wire")
    w.Shape = Part.makePolygon([App.Vector(0, 5, 5), App.Vector(20, 5, 5)])
    sh = asm.newObject("Part::Feature", "Shell")
    sh.Shape = Part.makeShell(Part.makeBox(10, 10, 10, App.Vector(5, 0, 0)).Faces)
    asm.newObject("Sketcher::SketchObject", "Sk")
    hid = asm.newObject("Part::Feature", "HiddenFace")
    hid.Shape = Part.makePlane(10, 10)
    hid.Visibility = False
    return T(skipped=["Face", "Wire", "Shell", "Sk"])


def _generated(length, count=3):
    def build(doc, asm):
        la = asm.newObject("Part::LinkArrayLinear", "LA")
        la.LinkedObject = source_box(doc)
        la.Length = length
        la.Occurrences = count
        doc.recompute()
        pitch = length / (count - 1.0)
        tr = {}
        for i in range(count - 1):
            tr[("LA.%d" % i, "LA.%d" % (i + 1))] = pitch
        return tr, pitch
    return build


@case("array", clearance=5.0)
def array_generated_gap(doc, asm):
    tr, pitch = _generated(24)(doc, asm)
    return T(close={k: pitch - 10.0 for k in tr})


@case("array")
def array_generated_overlap(doc, asm):
    tr, pitch = _generated(16)(doc, asm)
    return T({k: (10.0 - pitch) * 100.0 for k in tr})


# ------------------------------------------------------------------ nesting

@case("nested")
def nested_part_translated(doc, asm):
    box(asm, "A")
    sub = asm.newObject("App::Part", "Sub")
    sub.Placement = plc(20)
    box(sub, "Box", pos=(-12, 0, 0))
    return T({("A", "Sub.Box"): 200.0})


@case("nested")
def nested_part_rotated(doc, asm):
    box(asm, "A")
    sub = asm.newObject("App::Part", "Sub")
    sub.Placement = plc(5, rot=App.Rotation(Z, 90))
    box(sub, "Box", pos=(0, 3, 0))
    return T({("A", "Sub.Box"): 200.0})


@case("nested")
def nested_part_in_part(doc, asm):
    box(asm, "A")
    outer = asm.newObject("App::Part", "Outer")
    outer.Placement = plc(10)
    inner = outer.newObject("App::Part", "Inner")
    inner.Placement = plc(5)
    box(inner, "Box", pos=(-6, 0, 0))
    return T({("A", "Outer.Inner.Box"): 100.0})


@case("nested")
def nested_subassembly(doc, asm):
    box(asm, "A")
    sub = asm.newObject("Assembly::AssemblyObject", "Sub")
    sub.Placement = plc(0, 0, 15)
    box(sub, "Box", pos=(3, 0, -6))
    return T({("A", "Sub.Box"): 70.0})


@case("nested", name="nested_assembly_placement_composed")
def nested_assembly_placement_composed(doc, asm):
    asm.Placement = plc(100, 0, 0, App.Rotation(Z, 90))
    box(asm, "A")
    sub = asm.newObject("App::Part", "Sub")
    sub.Placement = plc(0, 8, 0)
    box(sub, "Box")
    return T({("A", "Sub.Box"): 200.0})


# ------------------------------------------------------------ joint-solved

def _joint_case(offset, clearance=None):
    def build(doc, asm):
        import JointObject
        jg = asm.newObject("Assembly::JointGroup", "Joints")
        b1 = box(asm, "Fixed")
        b2 = box(asm, "Moved", pos=(40, 50, 60), rot=App.Rotation(10, 20, 30))
        doc.recompute()
        g = jg.newObject("App::FeaturePython", "Ground")
        JointObject.GroundedJoint(g, b1)
        j = jg.newObject("App::FeaturePython", "Joint")
        JointObject.Joint(j, 0)
        j.Proxy.setJointConnectors(j, [[b1, ["Face6", "Vertex7"]], [b2, ["Face6", "Vertex7"]]])
        j.Offset1 = plc(0, 0, offset)
        doc.recompute()
        asm.solve()
        doc.recompute()
        if offset < 10:
            return T({("Fixed", "Moved"): (10.0 - offset) * 100.0})
        if offset == 10:
            return T(touching=[("Fixed", "Moved")])
        return T(close={("Fixed", "Moved"): offset - 10.0})
    return build


case("joint", "joint_fixed_coincident")(_joint_case(0.0))
case("joint", "joint_offset_7")(_joint_case(7.0))
case("joint", "joint_offset_10_touching")(_joint_case(10.0))
case("joint", "joint_offset_12_close", clearance=5.0)(_joint_case(12.0))


# ------------------------------------------------------------------- hidden

def _visible_pair(asm):
    box(asm, "V1", pos=(100, 0, 0))
    box(asm, "V2", pos=(108, 0, 0))
    return {("V1", "V2"): 200.0}


@case("hidden")
def hidden_box(doc, asm):
    vis = _visible_pair(asm)
    box(asm, "A")
    box(asm, "H", pos=(5, 0, 0)).Visibility = False
    return T(vis)


@case("hidden")
def hidden_link(doc, asm):
    vis = _visible_pair(asm)
    src = source_box(doc)
    link(asm, "LA", src)
    link(asm, "LH", src, (5, 0, 0)).Visibility = False
    return T(vis)


@case("hidden")
def hidden_box_inside_part(doc, asm):
    vis = _visible_pair(asm)
    box(asm, "A")
    part = asm.newObject("App::Part", "P")
    box(part, "H", pos=(5, 0, 0)).Visibility = False
    return T(vis)


@case("hidden")
def hidden_part_hides_children(doc, asm):
    vis = _visible_pair(asm)
    box(asm, "A")
    part = asm.newObject("App::Part", "P")
    box(part, "Child", pos=(5, 0, 0))
    part.Visibility = False
    return T(vis)


@case("hidden")
def hidden_subassembly(doc, asm):
    vis = _visible_pair(asm)
    box(asm, "A")
    sub = asm.newObject("Assembly::AssemblyObject", "Sub")
    box(sub, "Child", pos=(5, 0, 0))
    sub.Visibility = False
    return T(vis)


# ------------------------------------------------------------- ignore rules

@case("ignore", ignore=[["A", "B"]])
def ignore_listed_pair(doc, asm):
    box(asm, "A")
    box(asm, "B", pos=(5, 0, 0))
    box(asm, "C", pos=(0, 5, 0))
    return T({("A", "C"): 500.0, ("B", "C"): 250.0})


@case("ignore", ignore=["B"])
def ignore_whole_part(doc, asm):
    box(asm, "A")
    box(asm, "B", pos=(5, 0, 0))
    box(asm, "C", pos=(0, 5, 0))
    return T({("A", "C"): 500.0})


@case("ignore", ignore_names=["thread"])
def ignore_name_pattern(doc, asm):
    box(asm, "A")
    box(asm, "T1", pos=(5, 0, 0)).Label = "Screw_thread"
    box(asm, "C", pos=(0, 5, 0))
    return T({("A", "C"): 500.0})


# ----------------------------------------- Assembly workbench's own examples
# Constructions follow AssemblyTests/TestCore.py (the same objects and properties), with
# explicit element placements so the truth does not depend on a default pitch.

@case("own", own=True)
def own_component_count_link_array(doc, asm):
    source = doc.addObject("Part::Box", "ArraySource")
    array = link_array(doc, asm, "Array", source, 3, 8)
    return T({("Array.0", "Array.1"): 200.0, ("Array.1", "Array.2"): 200.0})


@case("own", own=True)
def own_suppressed_link_element(doc, asm):
    source = doc.addObject("Part::Box", "ArraySource")
    array = link_array(doc, asm, "Array", source, 3, 4)
    array.ElementList[1].Suppressed = True
    doc.recompute()
    return T({("Array.0", "Array.2"): 200.0})


@case("own", own=True, clearance=5.0)
def own_generated_array(doc, asm):
    source = doc.addObject("Part::Box", "ArraySource")
    array = asm.newObject("Part::LinkArrayLinear", "Array")
    array.LinkedObject = source
    array.Occurrences = 3
    array.Length = 24
    doc.recompute()
    return T(close={("Array.0", "Array.1"): 2.0, ("Array.1", "Array.2"): 2.0})


@case("own", own=True)
def own_solve_assembly(doc, asm):
    import JointObject
    jg = asm.newObject("Assembly::JointGroup", "Joints")
    b1 = box(asm, "Box", pos=(10, 20, 30), rot=App.Rotation(15, 25, 35))
    b2 = box(asm, "Box2", pos=(40, 50, 60), rot=App.Rotation(45, 55, 65))
    doc.recompute()
    ground = jg.newObject("App::FeaturePython", "GroundedJoint")
    JointObject.GroundedJoint(ground, b2)
    joint = jg.newObject("App::FeaturePython", "testJoint")
    JointObject.Joint(joint, 0)
    joint.Proxy.setJointConnectors(joint, [[b2, ["Face6", "Vertex7"]], [b1, ["Face6", "Vertex7"]]])
    doc.recompute()
    return T({("Box", "Box2"): 1000.0})


@case("own", own=True)
def own_grounded_joint_box(doc, asm):
    """TestCore.test_toggle_grounded_joint: a grounded box, plus a second box overlapping it."""
    import JointObject
    jg = asm.newObject("Assembly::JointGroup", "Joints")
    b = box(asm, "Box")
    box(asm, "Box2", pos=(0, 0, 9))
    doc.recompute()
    gj = jg.newObject("App::FeaturePython", "GroundedJoint")
    JointObject.GroundedJoint(gj, b)
    doc.recompute()
    return T({("Box", "Box2"): 100.0})


# ---------------------------------------------------------------------- big

BIG_PITCH = 30.0


@case("big", big=True, clearance=1.0)
def big_grid_225(doc, asm):
    src = source_box(doc)
    idx = range(6)
    for i in idx:
        for j in idx:
            for k in idx:
                nm = "G_%d_%d_%d" % (i, j, k)
                pos = (BIG_PITCH * i, BIG_PITCH * j, BIG_PITCH * k)
                if (i + j + k) % 2 == 0:
                    link(asm, nm, src, pos)
                else:
                    box(asm, nm, pos=pos)
    truth = T()
    cells = [(i, i, i) for i in range(5)]
    for n, (i, j, k) in enumerate(cells):
        d = float(n + 1)
        base = (BIG_PITCH * i, BIG_PITCH * j, BIG_PITCH * k)
        box(asm, "I%d" % n, pos=(base[0] + 10 - d, base[1], base[2]))
        truth["interfering"][P("G_%d_%d_%d" % (i, j, k), "I%d" % n)] = d * 100.0
    for n, (i, j, k) in enumerate([(5, 5, 5), (0, 5, 0)]):
        base = (BIG_PITCH * i, BIG_PITCH * j, BIG_PITCH * k)
        box(asm, "T%d" % n, pos=(base[0] + 10, base[1], base[2]))
        truth["touching"].add(P("G_%d_%d_%d" % (i, j, k), "T%d" % n))
    for n, (i, j, k) in enumerate([(5, 0, 5), (3, 0, 3)]):
        base = (BIG_PITCH * i, BIG_PITCH * j, BIG_PITCH * k)
        box(asm, "C%d" % n, pos=(base[0] + 10.5, base[1], base[2]))
        truth["close"][P("G_%d_%d_%d" % (i, j, k), "C%d" % n)] = 0.5
    return truth


# -------------------------------------------- all three classes in one assembly (GUI scenes)

@case("mixed", clearance=3.0)
def mixed_all_classes(doc, asm):
    """One interference, one touching pair, one close pair and a clearance set that the close pair violates.

    A and B overlap by 4 mm along x (4 * 10 * 10 = 400 mm^3); D touches B's far face; C stands 2.5 mm from A's near face."""
    import Interference

    box(asm, "A")
    box(asm, "B", pos=(6, 0, 0))
    box(asm, "C", pos=(-12.5, 0, 0))
    box(asm, "D", pos=(16, 0, 0))
    Interference.set_clearance_set(asm, "Keep clear", 5.0, [("A", "C")])
    return T({("A", "B"): 400.0}, touching=[("B", "D")], close={("A", "C"): 2.5})


def build_case(doc, name):
    """Build case ``name`` into ``doc`` (a fresh document); returns (assembly, truth)."""
    c = CASES[name]
    asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
    truth = c.build(doc, asm)
    doc.recompute()
    return asm, _norm(truth)
