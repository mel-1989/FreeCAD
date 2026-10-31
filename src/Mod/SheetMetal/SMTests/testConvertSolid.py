# SPDX-License-Identifier: LGPL-2.1-or-later
"""Oracle tests for the clean-room convert-from-solid (G07 slice 2c).

Expected values come from fabrication geometry only (Machinery's Handbook bend allowance, outside
setback tan(theta / 2) * radius of the mould-line surface). With s the mould-line radius (R + T
for thickness inward, R for outward), q the rip setback (T + gap inward, gap outward), e = s - q
and BA = theta * (R + K T):

- wall volume   = flat polygon area * T, the polygon being the source face shrunk by s * tan(theta/2)
                  at every bend edge and by q at every ripped edge
- bend volume   = theta * T * (R + T/2) * length, length = overlap of the two walls along the edge
- flat area     = sum of wall areas + BA * bend length
- flat outline  = the L / U strips are rectangles; the tray is a cross whose arms are a strip BA
                  long and as wide as the bottom side, then a wall (h - s) high that is wider than
                  the strip by e on both sides
"""

import math
import os
import unittest
from SMTests.quiet import expected_recompute_errors

import FreeCAD
import Part
from FreeCAD import Vector as V

import SheetMetalConvertSolid as CS
import SheetMetalUnfolder
from SMTests.testOracle import inner_root_face

AX = {"+x": (1, 0, 0), "-x": (-1, 0, 0), "+y": (0, 1, 0), "-y": (0, -1, 0), "+z": (0, 0, 1),
      "-z": (0, 0, -1)}
DIRS4 = ("+x", "-x", "+y", "-y")


def face_named(shape, direction):
    n = V(*AX[direction])
    hits = ["Face%d" % (i + 1) for i, f in enumerate(shape.Faces) if (f.normalAt(0, 0) - n).Length < 1e-9]
    assert len(hits) == 1, (direction, hits)
    return hits[0]


def edge_between(shape, d1, d2):
    a, b = shape.getElement(face_named(shape, d1)), shape.getElement(face_named(shape, d2))
    hits = ["Edge%d" % (i + 1) for i, e in enumerate(shape.Edges)
            if any(e.isSame(x) for x in a.Edges) and any(e.isSame(x) for x in b.Edges)]
    assert len(hits) == 1
    return hits[0]


def vbox(shape):
    pts = [v.Point for v in shape.Vertexes]
    return (min(p.x for p in pts), max(p.x for p in pts), min(p.y for p in pts),
            max(p.y for p in pts), min(p.z for p in pts), max(p.z for p in pts))


def outline_lengths(face):
    """Sorted edge lengths of a face's outer outline after merging collinear neighbours."""
    pts = [v.Point for v in face.OuterWire.OrderedVertexes]
    keep = []
    n = len(pts)
    for i in range(n):
        a, b, c = pts[i - 1], pts[i], pts[(i + 1) % n]
        if (b - a).cross(c - b).Length > 1e-9 * max(1.0, (b - a).Length * (c - b).Length):
            keep.append(b)
    return sorted((keep[i] - keep[i - 1]).Length for i in range(len(keep)))


def _case(name, kind, T, r_t, side, g_r=0.5, k=0.42, dims=(40.0, 30.0, 20.0)):
    R = r_t * T
    return dict(name=name, kind=kind, T=T, R=R, inward=(side == "Inward"), g=g_r * R, k=k, dims=dims)


CASES = [
    _case("L_T1_R.5T_in", "L", 1.0, 0.5, "Inward"),
    _case("L_T2_R1T_out", "L", 2.0, 1.0, "Outward", k=0.33),
    _case("U_T3_R3T_in", "U", 3.0, 3.0, "Inward", k=0.5),
    _case("U_T1.5_R2T_out", "U", 1.5, 2.0, "Outward"),
    _case("U_T2_R1T_in_long", "U", 2.0, 1.0, "Inward", dims=(60.0, 45.0, 25.0)),
    _case("tray_T1_R1T_g.5R_in", "tray", 1.0, 1.0, "Inward"),
    _case("tray_T2_R2T_g.75R_out", "tray", 2.0, 2.0, "Outward", g_r=0.75),
    _case("tray_T3_R.5T_g.5R_in", "tray", 3.0, 0.5, "Inward", dims=(50.0, 40.0, 30.0)),
    _case("box_closed_corner_T2_R1T_in", "tray", 2.0, 1.0, "Inward", g_r=0.05, dims=(30.0, 30.0, 30.0)),
    _case("box_closed_corner_T1_R3T_out", "tray", 1.0, 3.0, "Outward", g_r=0.1, dims=(35.0, 25.0, 15.0)),
    _case("slant60_T2_R2T_in", "slant", 2.0, 2.0, "Inward", dims=(40.0, 30.0, 20.0)),
    _case("slant60_T3_R1T_out", "slant", 3.0, 1.0, "Outward", k=0.5, dims=(40.0, 30.0, 20.0)),
]

SLANT_LEN = 20.0  # slanted wall width in the source profile; the turn angle is 60 degrees
SLANT_THETA = math.radians(60.0)


def make_source(doc, c):
    a, b, h = c["dims"]
    if c["kind"] == "slant":
        # profile in xz: bottom (0,0)-(a,0), slanted face rising to the right at 60 degrees to the
        # bottom's outward normal, extruded along y
        w = SLANT_LEN * math.cos(SLANT_THETA)
        hh = SLANT_LEN * math.sin(SLANT_THETA)
        pts = [V(0, 0, 0), V(a, 0, 0), V(a + w, 0, hh), V(0, 0, hh)]
        face = Part.Face(Part.makePolygon(pts + [pts[0]]))
        obj = doc.addObject("Part::Feature", "Src")
        obj.Shape = face.extrude(V(0, b, 0))
        doc.recompute()
        return obj
    box = doc.addObject("Part::Box", "Src")
    box.Length, box.Width, box.Height = a, b, h
    doc.recompute()
    return box


def selection(c, shape):
    """(fixed face, bend edges, open faces) names for the case."""
    kind = c["kind"]
    if kind == "L":
        keep, bends = ["-z", "-x"], [("-z", "-x")]
    elif kind == "U":
        keep, bends = ["-z", "-x", "+x"], [("-z", "-x"), ("-z", "+x")]
    elif kind == "tray":
        keep, bends = ["-z", "-x", "+x", "-y", "+y"], [("-z", d) for d in DIRS4]
    else:
        keep, bends = ["-z"], []
    if kind == "slant":
        slanted = [("Face%d" % (i + 1)) for i, f in enumerate(shape.Faces)
                   if f.normalAt(0, 0).z < -1e-9 and f.normalAt(0, 0).x > 1e-9]
        assert len(slanted) == 1
        bottom = face_named(shape, "-z")
        a, b = shape.getElement(bottom), shape.getElement(slanted[0])
        edge = next("Edge%d" % (i + 1) for i, e in enumerate(shape.Edges)
                    if any(e.isSame(x) for x in a.Edges) and any(e.isSame(x) for x in b.Edges))
        opens = [("Face%d" % (i + 1)) for i in range(len(shape.Faces))
                 if "Face%d" % (i + 1) not in (bottom, slanted[0])]
        return bottom, [edge], opens, [bottom, slanted[0]]
    kept_names = [face_named(shape, d) for d in keep]
    opens = [("Face%d" % (i + 1)) for i in range(len(shape.Faces)) if "Face%d" % (i + 1) not in kept_names]
    return kept_names[0], [edge_between(shape, *p) for p in bends], opens, kept_names


def expected(c):
    """Closed-form oracle: dict with volume, bbox (outer dimensions), flat area, outline lengths."""
    a, b, h = c["dims"]
    T, R, k = c["T"], c["R"], c["k"]
    s = R + T if c["inward"] else R
    q = T + c["g"] if c["inward"] else c["g"]
    kind = c["kind"]
    theta = SLANT_THETA if kind == "slant" else math.pi / 2.0
    ba = theta * (R + k * T)
    bend_vol = lambda length: theta * T * (R + T / 2.0) * length  # noqa: E731
    if kind == "L":
        walls = (a - s) * b + b * (h - s)
        bend_len = [b]
        flat_len = (a - s) + ba + (h - s)
        outline = sorted([b, b, flat_len, flat_len])
    elif kind == "U":
        walls = (a - 2 * s) * b + 2 * b * (h - s)
        bend_len = [b, b]
        flat_len = (a - 2 * s) + 2 * ba + 2 * (h - s)
        outline = sorted([b, b, flat_len, flat_len])
    elif kind == "tray":
        assert q < s  # the tests keep the rip setback inside the bend setback
        H = h - s
        walls = (a - 2 * s) * (b - 2 * s) + 2 * (b - 2 * q) * H + 2 * (a - 2 * q) * H
        bend_len = [a - 2 * s, a - 2 * s, b - 2 * s, b - 2 * s]
        e = s - q
        arms = []
        for width in (a - 2 * q, a - 2 * q, b - 2 * q, b - 2 * q):
            arms += [ba, ba, e, e, H, H, width]
        outline = sorted(arms)
    else:
        t = math.tan(theta / 2.0)
        walls = ((a - s * t) + (SLANT_LEN - s * t)) * b
        bend_len = [b]
        flat_len = (a - s * t) + ba + (SLANT_LEN - s * t)
        outline = sorted([b, b, flat_len, flat_len])
    volume = walls * T + sum(bend_vol(x) for x in bend_len)
    flat_area = walls + sum(ba * x for x in bend_len)
    if kind == "slant":
        w = SLANT_LEN * math.cos(theta)
        hh = SLANT_LEN * math.sin(theta)
        if c["inward"]:
            bbox = (0.0, a + w, 0.0, b, 0.0, hh + w * T / SLANT_LEN)
        else:
            bbox = (0.0, a + w + hh * T / SLANT_LEN, 0.0, b, -T, hh)
    else:
        kept = {"L": ["-z", "-x"], "U": ["-z", "-x", "+x"], "tray": ["-z", "-x", "+x", "-y", "+y"]}[kind]
        lo = [0.0, 0.0, 0.0]
        hi = [a, b, h]
        if not c["inward"]:
            for d in kept:
                axis = "xyz".index(d[1])
                if d[0] == "+":
                    hi[axis] += T
                else:
                    lo[axis] -= T
        bbox = (lo[0], hi[0], lo[1], hi[1], lo[2], hi[2])
    return dict(volume=volume, bbox=bbox, flat_area=flat_area, outline=outline, bends=len(bend_len),
                bend_len=bend_len, theta=theta)


class Doc(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument("ConvertSolidTest")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def feature(self, c, **over):
        src = make_source(self.doc, c)
        fixed, bends, opens, _kept = selection(c, src.Shape)
        props = dict(thickness=c["T"], radius=c["R"], ripGap=c["g"], kfactor=c["k"],
                     thicknessSide="Inward" if c["inward"] else "Outward")
        props.update(over)
        return src, CS.make_convert_solid(self.doc, src, fixed, bends, opens, **props)


class TestConvertSolid(Doc):
    measured = []

    def test_module_header_and_name(self):
        path = CS.__file__
        self.assertNotEqual(os.path.basename(path), "SheetMetalFromSolid.py")
        with open(path, encoding="utf-8") as fh:
            head = fh.read(2500)
        self.assertIn("SPDX-License-Identifier: LGPL-2.1-or-later", head.splitlines()[0])
        self.assertIn("Written without opening", head)

    def test_oracle_cases(self):
        self.assertGreaterEqual(len(CASES), 10)
        for c in CASES:
            with self.subTest(case=c["name"]):
                src, ft = self.feature(c)
                self.assertNotIn("Invalid", ft.State, c["name"])
                sh = ft.Shape
                ex = expected(c)
                self.assertTrue(sh.isValid())
                self.assertEqual(len(sh.Solids), 1)
                dv = sh.Volume / ex["volume"] - 1.0
                self.assertAlmostEqual(dv, 0.0, delta=1e-6, msg="volume")
                self.assertEqual(ft.bendCount, ex["bends"])
                got = vbox(sh)
                for g_, e_, label in zip(got, ex["bbox"], ("xmin", "xmax", "ymin", "ymax", "zmin", "zmax")):
                    self.assertAlmostEqual(g_, e_, delta=1e-6, msg="%s outer dimension" % label)
                # bend faces: cylinders of radius R and R + T, two per bend
                cyl = sorted(round(f.Surface.Radius, 9) for f in sh.Faces
                             if f.Surface.TypeId == "Part::GeomCylinder")
                self.assertEqual(cyl, sorted([round(c["R"], 9), round(c["R"] + c["T"], 9)] * ex["bends"]))
                self.measured.append((c["name"], dv, [round(x, 6) for x in (got[1] - got[0], got[3] - got[2], got[5] - got[4])]))

    def test_unfold_cases(self):
        for c in CASES:
            with self.subTest(case=c["name"]):
                src, ft = self.feature(c)
                ex = expected(c)
                # start on the inner face of the bends (the vendored unfolder, started on the outer
                # face of a 60 degree outward bend, silently returns only the first wall)
                flat, _f, _n, _nm, err, _s, _o = SheetMetalUnfolder.getUnfold(
                    {1: c["k"]}, ft, inner_root_face(ft.Shape), "ansi")
                self.assertEqual(err, 0, c["name"])
                planes = [f for f in flat.Faces if f.Surface.TypeId == "Part::GeomPlane"]
                top = max(planes, key=lambda f: f.Area)
                self.assertAlmostEqual(top.Area, ex["flat_area"], delta=1e-3, msg="flat area")
                same = [f for f in planes if abs(f.Area - top.Area) < 1e-6]
                self.assertEqual(len(same), 2, "one flat face on top and one on the bottom")
                self.assertAlmostEqual(flat.Volume, ex["flat_area"] * c["T"], delta=1e-3 * c["T"] + 1e-6)
                got = outline_lengths(top)
                self.assertEqual(len(got), len(ex["outline"]), "flat outline edge count")
                for g_, e_ in zip(got, ex["outline"]):
                    self.assertAlmostEqual(g_, e_, delta=1e-3, msg="flat edge length")

    def test_fixed_face_choice_does_not_change_the_part(self):
        c = CASES[5]
        src, ft = self.feature(c)
        v0 = ft.Shape.Volume
        fixed, bends, opens, kept = selection(c, src.Shape)
        ft2 = CS.make_convert_solid(self.doc, src, kept[1], bends, opens, name="Other",
                                    thickness=c["T"], radius=c["R"], ripGap=c["g"])
        self.assertAlmostEqual(ft2.Shape.Volume, v0, delta=1e-6 * v0)

    def test_recompute_after_source_change(self):
        c = CASES[5]
        src, ft = self.feature(c)
        v0 = ft.Shape.Volume
        c2 = dict(c, dims=(55.0, 33.0, 24.0))
        src.Length, src.Width, src.Height = 55.0, 33.0, 24.0
        self.doc.recompute()
        self.assertNotIn("Invalid", ft.State)
        self.assertAlmostEqual(ft.Shape.Volume / expected(c2)["volume"] - 1.0, 0.0, delta=1e-6)
        self.assertGreater(ft.Shape.Volume, v0)
        self.assertEqual(len(ft.Shape.Solids), 1)
        # parameters recompute too
        ft.thickness = 1.5
        ft.radius = 1.5
        ft.ripGap = 0.5
        self.doc.recompute()
        c3 = dict(c2, T=1.5, R=1.5, g=0.5)
        self.assertAlmostEqual(ft.Shape.Volume / expected(c3)["volume"] - 1.0, 0.0, delta=1e-6)

    def test_thickness_side_flips_the_dimensions(self):
        c = CASES[5]
        src, ft = self.feature(c)
        inner = vbox(ft.Shape)
        ft.thicknessSide = "Outward"
        self.doc.recompute()
        outer = vbox(ft.Shape)
        T = c["T"]
        self.assertAlmostEqual(outer[1] - outer[0] - (inner[1] - inner[0]), 2 * T, delta=1e-6)
        self.assertAlmostEqual(outer[5] - outer[4] - (inner[5] - inner[4]), T, delta=1e-6)

    def test_file_survives_save_and_restore(self):
        c = CASES[6]
        src, ft = self.feature(c)
        v0 = ft.Shape.Volume
        import tempfile
        path = os.path.join(tempfile.mkdtemp(prefix="cs-"), "cs.FCStd")
        self.doc.saveAs(path)
        name, ft_name = self.doc.Name, ft.Name
        FreeCAD.closeDocument(name)
        self.doc = FreeCAD.openDocument(path)
        ft2 = self.doc.getObject(ft_name)
        self.doc.recompute()
        self.assertAlmostEqual(ft2.Shape.Volume, v0, delta=1e-6 * v0)
        self.assertEqual(ft2.bendCount, 4)

    def test_refusals_are_readable(self):
        box = make_source(self.doc, dict(kind="tray", dims=(40.0, 30.0, 20.0)))
        sh = box.Shape
        solid = CS.source_solid(sh)
        sel = lambda d: [i for i, f in enumerate(solid.Faces) if (f.normalAt(0, 0) - V(*AX[d])).Length < 1e-9][0]  # noqa: E731

        def edge_idx(d1, d2):
            n = edge_between(sh, d1, d2)
            return int(n[4:]) - 1

        bottom = sel("-z")
        bends = [edge_idx("-z", d) for d in DIRS4]
        opens = [sel("+z")]

        def build(**kw):
            args = dict(fixed_face=bottom, bend_edges=bends, open_faces=opens, thickness=2.0,
                        radius=2.0, rip_gap=1.0)
            args.update(kw)
            return CS.build_from_solid(solid, **args)

        self.assertTrue(build()[0].isValid())
        # curved face
        cyl = self.doc.addObject("Part::Cylinder", "Cyl")
        cyl.Radius, cyl.Height = 15.0, 20.0
        self.doc.recompute()
        csolid = cyl.Shape
        side = next(i for i, f in enumerate(csolid.Faces) if f.Surface.TypeId == "Part::GeomCylinder")
        flat_top = next(i for i, f in enumerate(csolid.Faces)
                        if f.Surface.TypeId == "Part::GeomPlane" and f.normalAt(0, 0).z > 0.5)
        with self.assertRaisesRegex(CS.ConvertSolidError, "Face%d is not planar" % (side + 1)):
            CS.build_from_solid(csolid, flat_top, [], [i for i in range(3) if i != side and i != flat_top],
                                2.0, 2.0, 1.0)
        # a bend edge between a kept and an open face
        with self.assertRaisesRegex(CS.ConvertSolidError, "not shared by two kept faces"):
            build(open_faces=[sel("+z"), sel("+x")])
        # a bend edge that borders the open face only
        with self.assertRaisesRegex(CS.ConvertSolidError, "not shared by two kept faces"):
            build(bend_edges=bends + [edge_idx("+z", "+x")])
        # radius too large for the walls
        with self.assertRaisesRegex(CS.ConvertSolidError, "too small|no length|no flat"):
            build(radius=14.0)
        # gap too large for the walls
        with self.assertRaisesRegex(CS.ConvertSolidError, "too small|no length"):
            build(rip_gap=40.0)
        # thickness larger than the box allows: walls overlap
        with self.assertRaisesRegex(CS.ConvertSolidError, "too small|overlap|valid"):
            build(thickness=16.0, radius=0.5)
        # zero and negative numbers
        for kw in (dict(rip_gap=0.0), dict(radius=0.0), dict(thickness=0.0), dict(thickness=-1.0)):
            with self.assertRaises(CS.ConvertSolidError, msg=str(kw)):
                build(**kw)
        # tiny gap the unfolder cannot separate, and non-finite numbers
        with self.assertRaisesRegex(CS.ConvertSolidError, "rip gap must be at least"):
            build(rip_gap=1e-7)
        for kw in (dict(thickness=float("nan")), dict(radius=float("inf")), dict(rip_gap=float("nan")),
                   dict(thickness=float("inf"))):
            with self.assertRaisesRegex(CS.ConvertSolidError, "finite number", msg=str(kw)):
                build(**kw)
        # a wall a hair wider than nothing gets the same clear message as exactly nothing
        for height in (2.0, 2.0000001, 2.00001):
            slim = self.doc.addObject("Part::Box", "Slim")
            slim.Length, slim.Width, slim.Height = 40.0, 30.0, height
            self.doc.recompute()
            ss = slim.Shape
            sol = CS.source_solid(ss)
            idx = lambda d: [i for i, f in enumerate(sol.Faces) if (f.normalAt(0, 0) - V(*AX[d])).Length < 1e-9][0]  # noqa: E731, B023
            bl = [int(edge_between(ss, "-z", d)[4:]) - 1 for d in DIRS4]
            with self.assertRaisesRegex(CS.ConvertSolidError, "too small", msg="height %r" % height):
                CS.build_from_solid(sol, idx("-z"), bl, [idx("+z")], 1.0, 1.0, 1.0)
        # fixed face open
        with self.assertRaisesRegex(CS.ConvertSolidError, "fixed face"):
            build(fixed_face=sel("+z"))
        # kept faces not all joined by bends (a side left without its bend edge)
        with self.assertRaisesRegex(CS.ConvertSolidError, "not all joined"):
            build(bend_edges=bends[:3])
        # a closed loop of bends: the four vertical edges bend, plus the bottom ones
        vert = [edge_idx("+x", "+y"), edge_idx("+y", "-x"), edge_idx("-x", "-y"), edge_idx("-y", "+x")]
        with self.assertRaisesRegex(CS.ConvertSolidError, "closed loop"):
            build(bend_edges=bends + vert)
        # a concave edge: L-shaped prism, bend the reflex edge
        poly = [V(0, 0, 0), V(30, 0, 0), V(30, 10, 0), V(10, 10, 0), V(10, 30, 0), V(0, 30, 0)]
        lsolid = Part.Face(Part.makePolygon(poly + [poly[0]])).extrude(V(0, 0, 10))
        reflex = next(i for i, e in enumerate(lsolid.Edges)
                      if abs(e.Vertexes[0].Point.x - 10) < 1e-9 and abs(e.Vertexes[0].Point.y - 10) < 1e-9
                      and abs(e.Vertexes[-1].Point.x - 10) < 1e-9 and abs(e.Vertexes[-1].Point.y - 10) < 1e-9)
        faces_at = [i for i, f in enumerate(lsolid.Faces) if any(f.Edges[j].isSame(lsolid.Edges[reflex])
                                                                  for j in range(len(f.Edges)))]
        others = [i for i in range(len(lsolid.Faces)) if i not in faces_at]
        with self.assertRaisesRegex(CS.ConvertSolidError, "concave"):
            CS.build_from_solid(lsolid, faces_at[0], [reflex], others, 1.0, 1.0, 1.0)
        # through the feature: an invalid recompute, no crash, and it recovers
        c = CASES[5]
        src, ft = self.feature(c)
        ft.ripGap = 0.0
        with expected_recompute_errors():
            self.doc.recompute()
        self.assertIn("Invalid", ft.State)
        ft.ripGap = c["g"]
        self.doc.recompute()
        self.assertNotIn("Invalid", ft.State)
        self.assertTrue(ft.Shape.isValid())

    def test_zzz_report(self):
        # prints the measured table when the suite is run with -v / from the console
        for name, dv, dims in self.measured:
            FreeCAD.Console.PrintMessage("convert-solid %s volume rel err %.2e outer %s\n" % (name, dv, dims))


if __name__ == "__main__":
    unittest.main()
