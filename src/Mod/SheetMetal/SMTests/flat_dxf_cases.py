# SPDX-License-Identifier: LGPL-2.1-or-later
"""The parts behind the flat-pattern DXF goldens (cad-fork run 026, G07 slice 3b).

Each case builds a sheet-metal part headlessly from the vendored features, unfolds it through
the default Unfold feature (V1 unfolder) and gives the bend positions the developed-length
formula predicts. Parts and formulas come from the existing oracle modules (Machinery's
Handbook bend allowance: BA = theta * (R + K * T), the bend centreline lies BA / 2 inside the
bend zone). The goldens in ``fixtures/flat_dxf/`` are the DXF files these cases export; see
``regen_flat_dxf.py`` for how to rebuild them.
"""

import math
import os

import FreeCAD
import Part

import SheetMetalBendTable as BT
import SheetMetalConvertSolid as CS
import SheetMetalEdgeFlange as EF
import SheetMetalHemFeature as HF
import SheetMetalLighteningCutout as LC

from SMTests import testConvertSolid as TCS
from SMTests import testEdgeFlange as TEF
from SMTests import testHem as THM
from SMTests.testOracle import BASE_CASES, Case, build_base, inner_root_face, unfold_target_face
from SMTests.testUnfoldFeature import feature_unfold

TABLE_REF = "bt_mild_steel_ba"


class FlatCase:
    """name; build(doc) -> (object, face name, material, k); expect(doc) -> dict.

    expect keys: "strip" (list of bend-centreline distances from one end of a straight strip, the
    flat length), "dirs" (UP / DOWN per bend in that order from the same end), "angles",
    "ba" (per bend), "radius", "pairs" (tray: required centreline distances) and "holes"
    (circle radius, centre distance from the first bend line)."""

    def __init__(self, name, build, expect, group):
        self.name, self.build, self.expect, self.group = name, build, expect, group


def _setback(case, i):
    """Setback of bend i from the profile's mould-line corner. The profile sketch is the mould
    line on the side the material lies on; for a turn the other way the sketch line is the
    outside of the bend, so the setback is tan(theta / 2) * (R + T) instead of R."""
    r = case.R if case.angles[i] > 0 else case.R + case.T
    return r * math.tan(math.radians(abs(case.angles[i])) / 2.0)


def _strip_expect(case, mode="step", ba_override=None):
    """Bend centreline positions from the start of the profile for a base-flange Case."""
    pos, total = [], 0.0
    bas = []
    for i, seg in enumerate(case.segs):
        s = seg
        if i > 0:
            s -= _setback(case, i - 1)
        if i < len(case.angles):
            s -= _setback(case, i)
        total += s
        if i < len(case.angles):
            ba = (ba_override[i] if ba_override else
                  math.radians(abs(case.angles[i])) * (case.R + case.k_for(mode) * case.T))
            bas.append(ba)
            pos.append(total + ba / 2.0)
            total += ba
    return dict(strip=pos, length=total, angles=[abs(a) for a in case.angles], ba=bas,
                radius=case.R, signs=[1 if a > 0 else -1 for a in case.angles])


def base_case(name, case, group="base", hole=False, root="largest", material="_manual",
              lookup=None):
    def build(doc):
        base = build_base(doc, case)
        if hole:
            base = _drill(doc, base, case)
        face = unfold_target_face(base.Shape) if root == "largest" else inner_root_face(base.Shape)
        return base, face, material, case.k if case.k is not None else 0.4

    def expect(doc):
        if lookup is not None:
            ba = [lookup.allowance(case.T, case.R, abs(a)) for a in case.angles]
            exp = _strip_expect(case, ba_override=ba)
        else:
            exp = _strip_expect(case)
        exp["hole"] = hole
        return exp

    return FlatCase(name, build, expect, group)


def _drill(doc, base, case):
    """A 5 mm radius hole in the middle of the web and a 12 x 6 rectangular slot 10 mm further
    along the strip (both through the web)."""
    shape = base.Shape
    web = max((f for f in shape.Faces if f.Surface.TypeId == "Part::GeomPlane"), key=lambda f: f.Area)
    n = web.normalAt(0, 0)
    c = web.CenterOfMass
    cyl = Part.makeCylinder(5.0, 6 * case.T, c - n * (3 * case.T), n)
    shape = shape.cut(cyl)
    # slot: a box 12 (across the strip) x 6 (along the strip) centred 10 mm along the strip axis
    # from the web centre, axis direction = the web's longest edge direction
    edges = sorted(web.Edges, key=lambda e: -e.Length)
    along = (edges[0].Vertexes[-1].Point - edges[0].Vertexes[0].Point).normalize()
    across = n.cross(along)
    centre = c + along * 10.0
    corner = centre - along * 3.0 - across * 6.0 - n * (3 * case.T)
    poly = Part.makePolygon([corner, corner + along * 6.0, corner + along * 6.0 + across * 12.0,
                             corner + across * 12.0, corner])
    box = Part.Face(poly).extrude(n * (6 * case.T))
    shape = shape.cut(box)
    out = doc.addObject("Part::Feature", "Drilled_" + case.name)
    out.Shape = shape
    doc.recompute()
    return out


def lightening_case(name, case, group="cutout"):
    """A two-bend U strip with a 3 x 2 grid of 12 x 5 slots cut in the web by the lightening
    cutout (clean-room, cad-fork run 027); the strip expectations are those of the plain part."""
    def build(doc):
        from SMTests import testLighteningCutout as TLC
        base = build_base(doc, case)
        face = TLC.planar_big(base.Shape)
        geo, _a, _p = TLC.slot_geo(0.0, 0.0, 12.0, 5.0)
        sk = TLC.make_sketch(doc, face, geo, du=-24.0, dv=-6.0)
        feat = LC.make_lightening_cutout(doc, base, TLC.face_name(base.Shape, face), sk, Pattern="Grid",
                                         CountX=3, CountY=2, PitchX=12.0 + 12.0, PitchY=12.0,
                                         Direction=TLC.face_frame(face)[1])
        return feat, unfold_target_face(feat.Shape), "_manual", case.k

    def expect(doc):
        exp = _strip_expect(case)
        exp["hole"] = False
        return exp

    return FlatCase(name, build, expect, group)


def edge_flange_case(name, c):
    def build(doc):
        box = TEF.make_box(doc, c["T"])
        ff = EF.make_edge_flange(doc, box, [TEF.find_edge(box, e) for e in c["edges"]],
                                 length=c["L"], angle=c["angle"], radius=c["R"], kfactor=c["k"],
                                 gap=c["gap"], flip=c["flip"], measure=c["measure"])
        return ff, unfold_target_face(ff.Shape), "_manual", c["k"]

    def expect(doc):
        s = TEF.expected(c, "+x")[0]
        ba = math.radians(c["angle"]) * (c["R"] + c["k"] * c["T"])
        return dict(strip=[TEF.PL + ba / 2.0], length=TEF.PL + ba + s, angles=[c["angle"]], ba=[ba],
                    radius=c["R"])

    return FlatCase(name, build, expect, "edge_flange")


def hem_case(name, c):
    def build(doc):
        box = TEF.make_box(doc, c["T"])
        hem = HF.make_hem(doc, box, [TEF.find_edge(box, e) for e in c["edges"]], hemType=c["type"],
                          gap=c["gap"] or 1.0, length=c["L"], kfactor=c["k"], flip=c["flip"],
                          endGap=c["end_gap"])
        return hem, unfold_target_face(hem.Shape), "_manual", c["k"]

    def expect(doc):
        ba = math.pi * (c["R"] + c["k"] * c["T"])
        return dict(strip=[TEF.PL + ba / 2.0], length=TEF.PL + ba + c["L"], angles=[180.0],
                    ba=[ba], radius=c["R"])

    return FlatCase(name, build, expect, "hem")


def convert_case(name, c):
    def build(doc):
        src = TCS.make_source(doc, c)
        fixed, bends, opens, _kept = TCS.selection(c, src.Shape)
        props = dict(thickness=c["T"], radius=c["R"], ripGap=c["g"], kfactor=c["k"],
                     thicknessSide="Inward" if c["inward"] else "Outward")
        ft = CS.make_convert_solid(doc, src, fixed, bends, opens, **props)
        return ft, inner_root_face(ft.Shape), "_manual", c["k"]

    def expect(doc):
        ex = TCS.expected(c)
        theta = ex["theta"]
        ba = theta * (c["R"] + c["k"] * c["T"])
        a, b, h = c["dims"]
        s = c["R"] + c["T"] if c["inward"] else c["R"]
        out = dict(ba=[ba] * ex["bends"], angles=[math.degrees(theta)] * ex["bends"],
                   radius=c["R"], bend_len=sorted(ex["bend_len"]))
        if c["kind"] == "L":
            out.update(strip=[(a - s) + ba / 2.0], length=(a - s) + ba + (h - s))
        elif c["kind"] == "U":
            wall = h - s
            out.update(strip=[wall + ba / 2.0, wall + ba + (a - 2 * s) + ba / 2.0],
                       length=2 * wall + (a - 2 * s) + 2 * ba)
        else:  # tray: the cross is symmetric, so only the distances between opposite bends
            out.update(pairs=sorted([(a - 2 * s) + ba, (b - 2 * s) + ba]))
        return out

    return FlatCase(name, build, expect, "convert")


# --- the cases ------------------------------------------------------------------------------
_B = {c.name: c for c in BASE_CASES}
_TABLE_CASE = Case("t01_table_90x2_T2_R1T", 2.0, 1.0, None, [20.0, 40.0, 20.0], [90.0, 90.0])
_HOLE_CASE = Case("h01_hole_90x2_T2_R1T", 2.0, 1.0, 0.42, [30.0, 70.0, 30.0], [90.0, 90.0])
_FLANGE = next(c for c in TEF.CASES if c["name"] == "c02_45_T2_R1T")
_FLANGE90 = next(c for c in TEF.CASES if c["name"] == "c05_90_T2_R2T")
_HEM_CLOSED = next(c for c in THM.CASES if c["name"] == "h02_closed_T2")
_HEM_OPEN = next(c for c in THM.CASES if c["name"] == "h05_open_T2_g1T")
_TRAY = next(c for c in TCS.CASES if c["name"] == "tray_T1_R1T_g.5R_in")
_ZIG = Case("m01_zigzag_T2_R1T", 2.0, 1.0, 0.42, [20.0, 30.0, 20.0], [90.0, -90.0])
_HAT = Case("m02_hat_T2_R1T", 2.0, 1.0, 0.42, [20.0, 30.0, 40.0, 30.0, 20.0], [90.0, -90.0, -90.0, 90.0])
_LIGHT = Case("l01_lightening_u_T2_R1T", 2.0, 1.0, 0.42, [30.0, 70.0, 30.0], [90.0, 90.0])
_L = next(c for c in TCS.CASES if c["name"] == "L_T1_R.5T_in")
_U = next(c for c in TCS.CASES if c["name"] == "U_T3_R3T_in")


def all_cases():
    return [
        base_case("g01_base_1bend_90", _B["b02_90_T2_R1T_K.42"]),
        base_case("g02_base_1bend_135", _B["b07_135_T3_R3T_K.33"]),
        base_case("g03_base_2bends_u", _B["b08_90x2_T2_R1T_K.42"]),
        base_case("g04_base_2bends_45_inner_root", _B["b09_45x2_T1_R2T_K.5"], root="inner"),
        base_case("g05_base_3bends_z", _B["b10_90x3_T3_R0.5T_K.33"]),
        base_case("g06_base_3bends_mixed_angles", _B["b11_30_45_90_T2_R2T_K.42"]),
        edge_flange_case("g07_edge_flange_45", _FLANGE),
        edge_flange_case("g08_edge_flange_90", _FLANGE90),
        hem_case("g09_hem_closed", _HEM_CLOSED),
        hem_case("g10_hem_open", _HEM_OPEN),
        base_case("g11_hole_and_slot", _HOLE_CASE, hole=True),
        base_case("g12_table_bt_mild_steel_ba", _TABLE_CASE, material=TABLE_REF,
                  lookup=BT.lookup_from_ref(TABLE_REF)),
        convert_case("g13_convert_solid_l", _L),
        convert_case("g14_convert_solid_u", _U),
        convert_case("g15_convert_solid_tray", _TRAY),
        base_case("g16_mixed_updown_zigzag", _ZIG, group="mixed"),
        base_case("g17_mixed_updown_hat", _HAT, group="mixed"),
        lightening_case("g18c_lightening_cutout_grid", _LIGHT),
    ]


def build_flat(doc, case):
    """Build, unfold through the default feature; returns (feature, expectation)."""
    obj, face, material, k = case.build(doc)
    feat = feature_unfold(doc, obj, face, material=material, k=k, old=None)
    return feat, case.expect(doc)


FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "flat_dxf")


def regenerate(out_dir=FIXTURE_DIR):
    """Rebuild every golden DXF. Run from src/Mod/SheetMetal:

        FreeCADCmd -c "import sys; sys.path.insert(0, '.'); \\
            from SMTests import flat_dxf_cases as C; C.regenerate()"

    Review the diff (`git diff --stat src/Mod/SheetMetal/SMTests/fixtures`) before committing:
    a golden changes only when the unfolder, the bend tables or the exporter changed on purpose.
    """
    import SheetMetalUnfoldCmd

    os.makedirs(out_dir, exist_ok=True)
    for case in all_cases():
        doc = FreeCAD.newDocument("Regen")
        try:
            feat, _exp = build_flat(doc, case)
            SheetMetalUnfoldCmd.export_flat_dxf(feat, os.path.join(out_dir, case.name + ".dxf"))
            print("wrote", case.name)
        finally:
            FreeCAD.closeDocument(doc.Name)
