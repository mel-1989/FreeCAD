# SPDX-License-Identifier: LGPL-2.1-or-later
"""Assembly feature corpus (G06 slice 4a): deterministic assemblies with analytic truths.

Every case is a function ``build(ctx) -> truth`` registered with ``@case``. ``ctx`` holds the
assembly document (``ctx.doc``, ``ctx.asm``) and, on demand, a separate part document
(``ctx.src``) whose parts the assembly reaches through ``App::Link`` the way a real assembly
reaches the parts of other files. The truth is derived from the dimensions and placements
written into the builder, never from FreeCAD's own boolean results: interval arithmetic for
boxes, closed formulas for cylinders (pi r^2 d; a quarter or half cylinder at a corner or an
edge; an oblique cylinder between two planes pi r^2 t / cos a; a counterbore as two coaxial
cylinders).

Truth: ``T(removed={path: mm3}, per={feature: {path: mm3}}, then=[(action, T2), ...])``.
``path`` is the instance path as the API reports it ("A", "L2", "Arr.1", "Sub.Box"); every
component not listed must be unchanged, bit for bit. ``then`` lists actions run in order
(move a part, re-solve the joints, suppress a feature) with the truth that holds afterwards.
"""

import math
import os
import shutil
import tempfile

import FreeCAD as App
import Part

import AssemblyFeature as AF
from AssemblyTests.interference_corpus import Z, box, cyl, link, link_array, plc, source_box

CASES = {}
PI = math.pi
SQ2 = math.sqrt(2.0)


class Case:
    def __init__(self, name, klass, build):
        self.name, self.klass, self.build = name, klass, build


def case(klass, name=None):
    def deco(fn):
        nm = name or fn.__name__
        CASES[nm] = Case(nm, klass, fn)
        return fn
    return deco


def T(removed=None, per=None, then=None):
    return {"removed": dict(removed or {}), "per": dict(per or {}), "then": list(then or [])}


class Ctx:
    def __init__(self, doc, asm, name):
        self.doc, self.asm, self.name = doc, asm, name
        self._src = None
        self._tmp = tempfile.mkdtemp(prefix="feat_")
        doc.saveAs(os.path.join(self._tmp, "assembly.FCStd"))  # App::Link needs both owner and target saved
        self.features = []

    @property
    def src(self):
        """The part document, saved once so that App::Link can reference it from the assembly."""
        if self._src is None:
            self._src = App.newDocument("FeatSrc_" + self.name[:40])
            self._src.saveAs(os.path.join(self._tmp, "parts.FCStd"))
        return self._src

    def close(self):
        for d in self.docs:
            App.closeDocument(d.Name)
        if self._tmp:
            shutil.rmtree(self._tmp, ignore_errors=True)

    @property
    def docs(self):
        return [self.doc] + ([self._src] if self._src is not None else [])

    def feature(self, f):
        self.features.append(f)
        return f


# ------------------------------------------------------------- analytic helpers

def ivl(lo, hi, lo2, hi2):
    return max(0.0, min(hi, hi2) - max(lo, lo2))


def bb(pos, size):
    return tuple(pos), tuple(p + s for p, s in zip(pos, size))


def ov(a, b):
    v = 1.0
    for i in range(3):
        v *= ivl(a[0][i], a[1][i], b[0][i], b[1][i])
    return v


# ------------------------------------------------------------------- builders

def part(ctx, name, size=(10, 10, 10), pos=(0, 0, 0), rot=None):
    """A box part from the part document, placed in the assembly through an App::Link."""
    src = source_box(ctx.src, "P_" + name, size)
    return link(ctx.asm, name, src, pos, rot)


def cut_box(ctx, lo, size, name="Cut", **kw):
    return ctx.feature(AF.make_cut(ctx.asm, AF.box_tool(size, lo), name=name, **kw))


def sketch(ctx, name, shapes, pos=(0, 0, 0), inside=False):
    import Sketcher  # noqa: F401  (registers the type)
    sk = (ctx.asm.newObject if inside else ctx.doc.addObject)("Sketcher::SketchObject", name)
    for g in shapes:
        sk.addGeometry(g, False)
    sk.Placement = App.Placement(App.Vector(*pos), App.Rotation())
    ctx.doc.recompute()
    return sk


def rect(x0, y0, x1, y1):
    p = [App.Vector(x0, y0, 0), App.Vector(x1, y0, 0), App.Vector(x1, y1, 0), App.Vector(x0, y1, 0)]
    return [Part.LineSegment(p[i], p[(i + 1) % 4]) for i in range(4)]


def circle(cx, cy, r):
    return [Part.Circle(App.Vector(cx, cy, 0), App.Vector(0, 0, 1), r)]


# ------------------------------------------------------------ box tools, parts in the assembly

@case("box")
def box_corner_cut(ctx):
    box(ctx.asm, "A")
    cut_box(ctx, (-1, -1, -1), (4, 5, 6))
    return T({"A": 3 * 4 * 5})


@case("box")
def box_pocket_inside(ctx):
    box(ctx.asm, "A")
    cut_box(ctx, (4, 4, 4), (2, 3, 4))
    return T({"A": 24.0})


@case("box")
def box_tool_swallows_part(ctx):
    box(ctx.asm, "A")
    cut_box(ctx, (-5, -5, -5), (20, 20, 20))
    return T({"A": 1000.0})


@case("box")
def box_depth_1e_3(ctx):
    box(ctx.asm, "A")
    cut_box(ctx, (-5, 0, 0), (5.001, 10, 10))
    return T({"A": 0.1})


@case("box")
def box_depth_9_999(ctx):
    box(ctx.asm, "A")
    cut_box(ctx, (-5, 0, 0), (14.999, 10, 10))
    return T({"A": 999.9})


@case("box")
def box_two_components(ctx):
    part(ctx, "A")
    part(ctx, "B", pos=(12, 0, 0))
    cut_box(ctx, (8, 2, 2), (7, 4, 4))
    return T({"A": 2 * 4 * 4, "B": 3 * 4 * 4})


@case("box")
def box_four_components(ctx):
    for i, n in enumerate("ABCD"):
        box(ctx.asm, n, pos=(12 * i, 0, 0))
    cut_box(ctx, (5, 0, 0), (36, 5, 10))
    return T({"A": 250.0, "B": 500.0, "C": 500.0, "D": 250.0})


@case("box")
def box_tool_rotated(ctx):
    box(ctx.asm, "A")
    ctx.feature(AF.make_cut(ctx.asm, AF.box_tool((10, 4, 10), (5, 0, 0), (0, 0, 1), 90)))
    # rotated 90 degrees about z at (5,0,0): local x -> +y, local y -> -x, so x in [1,5], y in [0,10]
    return T({"A": 4 * 10 * 10})


@case("box")
def part_rotated_90_about_y(ctx):
    part(ctx, "A", rot=App.Rotation(App.Vector(0, 1, 0), 90))  # occupies x [0,10], z [-10,0]
    cut_box(ctx, (-1, -1, -3), (12, 12, 3))
    return T({"A": 300.0})


@case("box")
def part_rotated_45_half_cut(ctx):
    box(ctx.asm, "A", rot=App.Rotation(Z, 45), center=App.Vector(5, 5, 5))
    cut_box(ctx, (-50, -50, -50), (55, 100, 100))  # x up to 5: the plane of symmetry of the diamond
    return T({"A": 500.0})


@case("box")
def assembly_placement_applied_to_tool(ctx):
    ctx.asm.Placement = plc(100, 50, 0, App.Rotation(Z, 90))
    box(ctx.asm, "A", pos=(1, 2, 3))
    cut_box(ctx, (0, 0, 0), (4, 5, 6))  # tool in the assembly frame: x [1,4], y [2,5], z [3,6]
    return T({"A": 27.0})


# ------------------------------------------------------------------ extruded cuts

@case("extrude")
def extrude_rect_blind(ctx):
    box(ctx.asm, "Plate", (20, 20, 10))
    sk = sketch(ctx, "Sk", rect(4, 3, 8, 6), pos=(0, 0, 10))
    ctx.feature(AF.make_cut(ctx.asm, AF.extrude_tool((0, 0, -1), length=6), tool_object=sk))
    return T({"Plate": 4 * 3 * 6})


@case("extrude")
def extrude_circle_blind(ctx):
    part(ctx, "Plate", (20, 20, 10))
    sk = sketch(ctx, "Sk", circle(10, 10, 3), pos=(0, 0, 10))
    ctx.feature(AF.make_cut(ctx.asm, AF.extrude_tool((0, 0, -1), length=4), tool_object=sk))
    return T({"Plate": PI * 9 * 4})


@case("extrude")
def extrude_through_three_plates(ctx):
    for i, n in enumerate(("P1", "P2", "P3")):
        box(ctx.asm, n, (20, 20, 5), (0, 0, 6 * i))
    sk = sketch(ctx, "Sk", circle(10, 10, 2), pos=(0, 0, 20))
    ctx.feature(AF.make_cut(ctx.asm, AF.extrude_tool((0, 0, -1), through=True), tool_object=sk))
    return T({n: PI * 4 * 5 for n in ("P1", "P2", "P3")})


@case("extrude")
def extrude_symmetric(ctx):
    box(ctx.asm, "Plate", (20, 20, 10))
    sk = sketch(ctx, "Sk", circle(10, 10, 2), pos=(0, 0, 5))
    ctx.feature(AF.make_cut(ctx.asm, AF.extrude_tool((0, 0, 1), length=6, symmetric=True), tool_object=sk))
    return T({"Plate": PI * 4 * 6})


@case("extrude")
def extrude_oblique_direction(ctx):
    box(ctx.asm, "Plate", (30, 30, 10))
    sk = sketch(ctx, "Sk", rect(5, 5, 8, 8), pos=(0, 0, 10))
    # direction (3,0,-4)/5, length 10: the prism drops 8 mm, a sheared prism of volume area x vertical drop
    ctx.feature(AF.make_cut(ctx.asm, AF.extrude_tool((3, 0, -4), length=10), tool_object=sk))
    return T({"Plate": 9 * 8.0})


@case("extrude")
def extrude_sketch_inside_assembly(ctx):
    ctx.asm.Placement = plc(10, 20, 30, App.Rotation(Z, 90))
    box(ctx.asm, "Plate", (20, 20, 10))
    sk = sketch(ctx, "Sk", rect(2, 2, 6, 5), pos=(0, 0, 10), inside=True)
    ctx.feature(AF.make_cut(ctx.asm, AF.extrude_tool((0, 0, -1), length=3), tool_object=sk))
    return T({"Plate": 4 * 3 * 3})


@case("extrude")
def solid_tool_cylinder(ctx):
    box(ctx.asm, "A")
    tool = ctx.doc.addObject("Part::Cylinder", "ToolCyl")
    tool.Radius, tool.Height = 2, 6
    tool.Placement = plc(5, 5, 7)
    tool.Visibility = False
    ctx.doc.recompute()
    ctx.feature(AF.make_cut(ctx.asm, AF.solid_tool(), tool_object=tool))
    return T({"A": PI * 4 * 3})


@case("extrude")
def cylinder_tool_across_box(ctx):
    part(ctx, "A")
    ctx.feature(AF.make_cut(ctx.asm, AF.cylinder_tool(3, 20, (5, 5, -5), (0, 0, 1))))
    return T({"A": PI * 9 * 10})


# ------------------------------------------------------------------------ holes

@case("hole")
def hole_blind(ctx):
    box(ctx.asm, "Plate", (20, 20, 10))
    ctx.feature(AF.make_hole(ctx.asm, (10, 10, 10), (0, 0, -1), 6, depth=4))
    return T({"Plate": PI * 9 * 4})


@case("hole")
def hole_through_stack(ctx):
    for i, n in enumerate(("P1", "P2", "P3")):
        part(ctx, n, (20, 20, 5), (0, 0, 6 * i))
    ctx.feature(AF.make_hole(ctx.asm, (10, 10, 25), (0, 0, -1), 4, through=True))
    return T({n: PI * 4 * 5 for n in ("P1", "P2", "P3")})


@case("hole")
def hole_counterbore(ctx):
    box(ctx.asm, "Plate", (20, 20, 10))
    ctx.feature(AF.make_hole(ctx.asm, (10, 10, 10), (0, 0, -1), 4, depth=10, cbore_diameter=8, cbore_depth=3))
    return T({"Plate": PI * 16 * 3 + PI * 4 * 7})


@case("hole")
def hole_at_corner_quarter(ctx):
    part(ctx, "Plate", (20, 20, 10))
    ctx.feature(AF.make_hole(ctx.asm, (0, 0, 10), (0, 0, -1), 6, depth=4))
    return T({"Plate": PI * 9 * 4 / 4.0})


@case("hole")
def hole_on_edge_half(ctx):
    box(ctx.asm, "Plate", (20, 20, 10))
    ctx.feature(AF.make_hole(ctx.asm, (10, 0, 10), (0, 0, -1), 6, depth=4))
    return T({"Plate": PI * 9 * 4 / 2.0})


@case("hole")
def hole_oblique_45(ctx):
    box(ctx.asm, "Slab", (40, 40, 10))
    # axis tilted 45 degrees from the slab normal, entry above the top face so the whole cap clears it:
    # the volume between the two faces is pi r^2 t / cos(45)
    ctx.feature(AF.make_hole(ctx.asm, (20, 14, 12), (0, 1, -1), 4, through=True))
    return T({"Slab": PI * 4 * 10 * SQ2})


@case("hole")
def hole_deeper_than_part(ctx):
    part(ctx, "Plate", (20, 20, 10))
    ctx.feature(AF.make_hole(ctx.asm, (10, 10, 10), (0, 0, -1), 6, depth=50))
    return T({"Plate": PI * 9 * 10})


@case("hole")
def hole_in_rotated_and_translated_part(ctx):
    part(ctx, "Plate", (20, 20, 10), (30, 10, 5), App.Rotation(App.Vector(1, 0, 0), 90))
    # turned 90 degrees about x, (x,y,z) -> (x,-z,y): the plate spans x [30,50], y [0,10], z [5,25]
    ctx.feature(AF.make_hole(ctx.asm, (40, 12, 15), (0, -1, 0), 4, through=True))
    return T({"Plate": PI * 4 * 10})


# ------------------------------------------------------ touching and empty reach

@case("miss")
def tool_touches_face_only(ctx):
    box(ctx.asm, "A")
    part(ctx, "B", pos=(30, 0, 0))
    cut_box(ctx, (10, 0, 0), (4, 10, 10))
    return T()


@case("miss")
def tool_reaches_nothing(ctx):
    box(ctx.asm, "A")
    cut_box(ctx, (100, 100, 100), (5, 5, 5))
    return T()


@case("miss")
def hole_tangent_outside(ctx):
    box(ctx.asm, "A")
    ctx.feature(AF.make_hole(ctx.asm, (-3, 5, 10), (0, 0, -1), 6, depth=10))
    return T()


@case("miss")
def tool_touches_edge_only(ctx):
    box(ctx.asm, "A")
    cut_box(ctx, (10, 10, 0), (4, 4, 10))
    return T()


# ----------------------------------------------------------------------- links

@case("link")
def link_placed_and_turned(ctx):
    part(ctx, "L", pos=(30, 0, 0), rot=App.Rotation(Z, 90))  # occupies x [20,30], y [0,10]
    cut_box(ctx, (24, 2, 0), (10, 5, 4))
    return T({"L": 6 * 5 * 4})


@case("link")
def link_two_instances_cut_one(ctx):
    src = source_box(ctx.src, "Shared")
    link(ctx.asm, "L1", src)
    link(ctx.asm, "L2", src, (30, 0, 0))
    cut_box(ctx, (32, 0, 0), (4, 10, 10))
    return T({"L2": 4 * 10 * 10})


@case("link")
def link_two_instances_cut_both(ctx):
    src = source_box(ctx.src, "Shared")
    link(ctx.asm, "L1", src)
    link(ctx.asm, "L2", src, (12, 0, 0))
    cut_box(ctx, (8, 0, 0), (7, 10, 10))
    return T({"L1": 200.0, "L2": 300.0})


@case("link")
def link_to_part_with_two_solids(ctx):
    pr = ctx.src.addObject("App::Part", "PartSrc")
    box(pr, "b1", (4, 4, 4))
    box(pr, "b2", (4, 4, 4), (6, 0, 0))
    ctx.src.recompute()
    link(ctx.asm, "LP", pr, (20, 0, 0), App.Rotation(Z, 180))
    # turned 180 degrees about (20,0,0): b1 -> x [16,20], y [-4,0]; b2 -> x [10,14]
    cut_box(ctx, (12, -4, 0), (6, 4, 4))
    return T({"LP.b1": 2 * 4 * 4, "LP.b2": 2 * 4 * 4})


# ------------------------------------------------------------------ link arrays

@case("array")
def array_elements_cut_independently(ctx):
    link_array(ctx.doc, ctx.asm, "Arr", source_box(ctx.src), 4, 12)
    cut_box(ctx, (5, 0, 0), (21, 5, 10))  # x [5,26]
    return T({"Arr.0": 5 * 5 * 10, "Arr.1": 10 * 5 * 10, "Arr.2": 2 * 5 * 10})


@case("array")
def array_noshow_elements(ctx):
    link_array(ctx.doc, ctx.asm, "Arr", source_box(ctx.src), 3, 12, show_element=False)
    cut_box(ctx, (21, 0, 0), (10, 10, 10))  # elements at 0, 12, 24
    return T({"Arr.1": 1 * 100.0, "Arr.2": 7 * 100.0})


@case("array")
def array_scope_one_element(ctx):
    link_array(ctx.doc, ctx.asm, "Arr", source_box(ctx.src), 3, 10)
    cut_box(ctx, (-1, 0, 0), (40, 5, 10), scope=["Arr.1"])
    return T({"Arr.1": 10 * 5 * 10})


# ---------------------------------------------------------------- sub-assemblies

@case("nested")
def subassembly_part_placed(ctx):
    box(ctx.asm, "A", pos=(0, 20, 0))
    sub = ctx.asm.newObject("App::Part", "Sub")
    sub.Placement = plc(0, 0, 15, App.Rotation(Z, 90))
    box(sub, "Box", pos=(3, 0, 0))  # turned 90 about z: x [-10,0], y [3,13], z [15,25]
    cut_box(ctx, (-6, 0, 18), (10, 20, 10))
    return T({"Sub.Box": 6 * 10 * 7})


@case("nested")
def assembly_link_instance(ctx):
    sub = ctx.doc.addObject("Assembly::AssemblyObject", "SubAsm")
    box(sub, "SB", pos=(3, 0, 0))
    al = ctx.asm.newObject("Assembly::AssemblyLink", "AL")
    al.LinkedObject = sub
    al.Placement = plc(1000)
    ctx.doc.recompute()
    leaf = "AL." + al.getSubObjects()[0].rstrip(".")
    cut_box(ctx, (1006, 0, 0), (10, 4, 10))  # SB spans x [1003,1013]
    return T({leaf: 7 * 4 * 10})


@case("nested")
def nested_part_in_part(ctx):
    outer = ctx.asm.newObject("App::Part", "Outer")
    outer.Placement = plc(10, 0, 0)
    inner = outer.newObject("App::Part", "Inner")
    inner.Placement = plc(0, 10, 0)
    box(inner, "B")  # global x [10,20], y [10,20]
    cut_box(ctx, (8, 8, 2), (5, 5, 5))
    return T({"Outer.Inner.B": 3 * 3 * 5})


# ------------------------------------------------------------------------- scope

@case("scope")
def scope_excludes_one(ctx):
    for i, n in enumerate("ABC"):
        box(ctx.asm, n, pos=(12 * i, 0, 0))
    cut_box(ctx, (-1, 0, 0), (40, 5, 10), scope=["B"])
    return T({"B": 10 * 5 * 10})


@case("scope")
def scope_by_path_prefix(ctx):
    box(ctx.asm, "A")
    sub = ctx.asm.newObject("App::Part", "Sub")
    box(sub, "S1", pos=(12, 0, 0))
    box(sub, "S2", pos=(24, 0, 0))
    cut_box(ctx, (-1, 0, 0), (40, 5, 10), scope=["Sub"])
    return T({"Sub.S1": 500.0, "Sub.S2": 500.0})


@case("scope")
def scope_by_label_for_link(ctx):
    src = source_box(ctx.src, "Shared")
    link(ctx.asm, "L1", src)
    link(ctx.asm, "L2", src, (12, 0, 0))
    cut_box(ctx, (-1, 0, 0), (30, 5, 10), scope=["L2"])
    return T({"L2": 10 * 5 * 10})


@case("scope")
def scope_empty_list_means_all(ctx):
    box(ctx.asm, "A")
    box(ctx.asm, "B", pos=(12, 0, 0))
    cut_box(ctx, (-1, 0, 0), (30, 5, 10), scope=[])
    return T({"A": 500.0, "B": 500.0})


# ------------------------------------------------------------------------ hidden

@case("hidden")
def hidden_component_is_cut(ctx):
    a = box(ctx.asm, "A")
    a.Visibility = False
    box(ctx.asm, "B", pos=(12, 0, 0))
    cut_box(ctx, (5, 0, 0), (12, 5, 10))  # x [5,17]
    return T({"A": 250.0, "B": 250.0})


# ---------------------------------------------------------------------- sequence

@case("sequence")
def two_cuts_overlapping(ctx):
    box(ctx.asm, "A")
    cut_box(ctx, (0, 0, 0), (6, 10, 10), name="C1")             # x [0,6]
    cut_box(ctx, (4, 0, 0), (5, 10, 10), name="C2")             # x [4,9]; x [4,6] is already gone
    return T({"A": 900.0}, per={"C1": {"A": 600.0}, "C2": {"A": 300.0}})


@case("sequence")
def hole_then_cut_other_component(ctx):
    box(ctx.asm, "A", (20, 20, 10))
    box(ctx.asm, "B", (20, 20, 10), (0, 0, 12))
    ctx.feature(AF.make_hole(ctx.asm, (10, 10, 30), (0, 0, -1), 4, through=True, name="H"))
    cut_box(ctx, (0, 0, 8), (5, 5, 6), name="C")                # z [8,14]: A z [8,10], B z [12,14]
    return T({"A": PI * 4 * 10 + 5 * 5 * 2, "B": PI * 4 * 10 + 5 * 5 * 2},
             per={"H": {"A": PI * 4 * 10, "B": PI * 4 * 10}, "C": {"A": 50.0, "B": 50.0}})


@case("sequence")
def second_feature_suppressed(ctx):
    box(ctx.asm, "A")
    box(ctx.asm, "B", pos=(12, 0, 0))
    cut_box(ctx, (0, 0, 0), (3, 3, 10), name="C1")
    c2 = cut_box(ctx, (12, 0, 0), (3, 3, 10), name="C2")
    c2.Suppressed = True
    return T({"A": 90.0})


@case("sequence")
def suppress_then_restore(ctx):
    box(ctx.asm, "A")
    cut_box(ctx, (0, 0, 0), (3, 3, 10), name="C1")

    def suppress(c):
        c.features[0].Suppressed = True

    def restore(c):
        c.features[0].Suppressed = False

    return T({"A": 90.0}, then=[(suppress, T()), (restore, T({"A": 90.0}))])


# ---------------------------------------------------------------------- follows

@case("follow")
def follow_placement_edit(ctx):
    a = box(ctx.asm, "A")
    cut_box(ctx, (-1, 0, 0), (9, 10, 10))  # x up to 8

    def move(c):
        a.Placement = plc(6, 0, 0)   # now x [6,16]: 2 mm inside the tool

    def move_away(c):
        a.Placement = plc(50, 0, 0)

    return T({"A": 8 * 100.0}, then=[(move, T({"A": 2 * 100.0})), (move_away, T())])


@case("follow")
def follow_link_placement_and_turn(ctx):
    lk = part(ctx, "L")
    cut_box(ctx, (0, 0, 0), (5, 5, 5))

    def turn(c):
        lk.Placement = plc(0, 0, 0, App.Rotation(Z, 90))  # y goes negative: x [-10,0], y [0,10]

    def turn_back_shifted(c):
        lk.Placement = plc(-8, 0, 0, App.Rotation(Z, 90))  # x [-18,-8]

    def shift(c):
        lk.Placement = plc(3, 2, 1)

    return T({"L": 125.0}, then=[(turn, T()), (turn_back_shifted, T()), (shift, T({"L": 2 * 3 * 4}))])


@case("follow")
def follow_array_element_edit(ctx):
    arr = link_array(ctx.doc, ctx.asm, "Arr", source_box(ctx.src), 3, 12)
    cut_box(ctx, (-1, 0, 0), (7, 10, 10))  # x up to 6: element 0 only

    def move_element(c):
        arr.ElementList[2].Placement = plc(3, 0, 0)  # element 2 now x [3,13]

    return T({"Arr.0": 600.0}, then=[(move_element, T({"Arr.0": 600.0, "Arr.2": 300.0}))])


def _joint_follow(ctx):
    import JointObject
    asm, doc = ctx.asm, ctx.doc
    jg = asm.newObject("Assembly::JointGroup", "Joints")
    b1 = box(asm, "Fixed")
    b2 = box(asm, "Moved", pos=(40, 50, 60), rot=App.Rotation(10, 20, 30))
    cut_box(ctx, (-1, -1, -1), (4, 12, 8))  # x [-1,3], y [-1,11], z [-1,7]: clear of the joint faces (z=10 top)
    doc.recompute()
    g = jg.newObject("App::FeaturePython", "Ground")
    JointObject.GroundedJoint(g, b1)
    j = jg.newObject("App::FeaturePython", "Joint")
    JointObject.Joint(j, 0)
    j.Proxy.setJointConnectors(j, [[b1, ["Face6", "Vertex7"]], [b2, ["Face6", "Vertex7"]]])
    j.Offset1 = plc(0, 0, 2)
    doc.recompute()
    return j


@case("follow")
def follow_joint_resolve(ctx):
    j = _joint_follow(ctx)
    fixed = ctx.asm.getObject("Fixed")

    # the recompute in the builder solved the joint: Moved sits on Fixed, shifted up by the 2 mm offset
    def move_ground(c):
        fixed.Placement = plc(-4, 0, 0)  # Fixed x [-4,6]; the re-solve carries Moved with it

    def change_offset(c):
        j.Offset1 = plc(0, 0, 4)         # Moved z [4,14]

    return T({"Fixed": 3 * 10 * 7.0, "Moved": 3 * 10 * 5.0},
             then=[(move_ground, T({"Fixed": 4 * 10 * 7.0, "Moved": 4 * 10 * 5.0})),
                   (change_offset, T({"Fixed": 4 * 10 * 7.0, "Moved": 4 * 10 * 3.0}))])


def build_case(name):
    """Build case ``name`` into fresh documents; returns (ctx, truth)."""
    doc = App.newDocument("Feat_" + name[:40])
    asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
    ctx = Ctx(doc, asm, name)
    truth = CASES[name].build(ctx)
    doc.recompute()
    for d in ctx.docs:
        d.recompute()
    return ctx, truth
