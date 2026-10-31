# SPDX-License-Identifier: LGPL-2.1-or-later
"""Oracle tests for the forming-tool library (G07 slice 4a, acceptance check 6).

Each tool is formed into a plate through the vendored makeforming. Expected volume change is
computed by SheetMetalFormingTools.expected_volume_change from the tool's 2D section (offset
lines intersected in plain Python, Pappus or extrusion), not from OCCT booleans or offsets; the
tool volume is compared with its analytic value; the wall below the plate is checked to stay T
thick by the distance between every offset face and the tool surface; and what the default
unfolder does with the formed part is measured and asserted (it must be flagged, never silently
wrong).
"""

import json
import math
import os
import tempfile
import unittest

import FreeCAD
import Part
from FreeCAD import Vector as V

import SheetMetalFlatPattern as FP
import SheetMetalFormingTools as FT
import SheetMetalUnfoldCmd as UC
from SMTests import flat_dxf_cases as C
from SMTests.testOracle import Case, build_base
from SMTests.testOracle import unfold_target_face
from SMTests.testUnfoldFeature import feature_unfold

rows = []  # (tool, params, d_tool_vol, d_dV, wall_err, flat info)
UNFOLD = {}

PARAM_SETS = [  # (tool, plate T, parameter overrides)
    ("dimple", 2.0, {}),
    ("dimple", 1.5, dict(top_diameter=14.0, bottom_diameter=2.0, depth=4.0)),
    ("extruded_hole", 2.0, {}),
    ("extruded_hole", 3.0, dict(hole_diameter=9.0, depth=6.0)),
    ("countersunk_hole", 2.0, {}),
    ("countersunk_hole", 1.0, dict(hole_diameter=5.0, countersink_diameter=11.0, stub=2.0)),
    ("rib", 2.0, {}),
    ("rib", 1.0, dict(width=5.0, length=26.0, depth=3.0)),
    ("louver", 2.0, {}),
    ("louver", 1.5, dict(length=26.0, width=14.0, depth=6.0, slope_inset=5.0)),
    ("lance", 2.0, {}),
    ("lance", 1.0, dict(length=20.0, width=12.0, depth=3.0)),
]


def top_face_name(shape):
    return "Face%d" % (1 + [i for i, f in enumerate(shape.Faces) if abs(f.CenterOfMass.z) < 1e-9
                            and f.normalAt(0, 0).z > 0.9][0])


class Base(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument("FormTest")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def plate(self, thick):
        obj = self.doc.addObject("Part::Feature", "Plate")
        obj.Shape = Part.makeBox(60.0, 40.0, thick, V(-30, -20, -thick))
        self.doc.recompute()
        return obj


class TestFormingToolLibrary(Base):
    def test_library_content(self):
        names = FT.list_tools()
        self.assertGreaterEqual(len(names), 5)
        kinds = {FT.tool_info(n)["kind"] for n in names}
        for k in ("louver", "dimple", "lance", "rib"):
            self.assertIn(k, kinds)
        self.assertTrue(kinds & {"extruded_hole", "countersunk_hole"})
        for n in names:
            info = FT.tool_info(n)
            self.assertTrue(info["source"] and info["description"])
            for key, rec in info["parameters"].items():
                self.assertEqual(set(rec) >= {"default", "unit", "min", "doc"}, True, (n, key))
            tool = FT.make_tool(n)
            self.assertTrue(tool.shape.isValid())
            self.assertEqual(len(tool.shape.Solids), 1)

    def test_bad_input_is_refused_readably(self):
        with self.assertRaises(FT.FormingToolError) as cm:
            FT.make_tool("nonesuch")
        self.assertIn("available", str(cm.exception))
        with self.assertRaises(FT.FormingToolError):
            FT.make_tool("dimple", colour="red")
        with self.assertRaises(FT.FormingToolError):
            FT.make_tool("dimple", top_diameter=4.0, bottom_diameter=6.0)
        with self.assertRaises(FT.FormingToolError):
            FT.make_tool("rib", width=10.0, length=8.0)
        with self.assertRaises(FT.FormingToolError):
            FT.make_tool("louver", length=10.0, slope_inset=6.0)

    def test_tool_shallower_than_the_sheet_is_refused(self):
        plate = self.plate(3.0)
        tool = FT.make_tool("dimple", depth=2.0)
        with self.assertRaises(FT.FormingToolError) as cm:
            FT.apply_tool(plate.Shape, top_face_name(plate.Shape), tool)
        self.assertIn("thinner", str(cm.exception))

    def test_non_planar_face_is_refused(self):
        cyl = self.doc.addObject("Part::Feature", "Cyl")
        cyl.Shape = Part.makeCylinder(20, 3)
        face = "Face%d" % (1 + [i for i, f in enumerate(cyl.Shape.Faces) if f.Surface.TypeId == "Part::GeomCylinder"][0])
        with self.assertRaises(FT.FormingToolError):
            FT.apply_tool(cyl.Shape, face, FT.make_tool("dimple"))


class TestFormingTools(Base):
    def run_set(self, name, thk, over):
        plate = self.plate(thk)
        tool = FT.make_tool(name, **over)
        d_tool = abs(tool.volume - tool.analytic_volume()) / tool.analytic_volume()
        self.assertLess(d_tool, 1e-6, "tool volume against analytic")
        formed = FT.apply_tool(plate.Shape, top_face_name(plate.Shape), tool)
        self.assertEqual(formed.ShapeType, "Solid")
        self.assertTrue(formed.isValid())
        got = formed.Volume - plate.Shape.Volume
        want = tool.expected_volume_change(thk)
        d_dv = abs(got - want) / abs(want)
        self.assertLess(d_dv, 1e-6, "volume change %g against expected %g" % (got, want))
        # wall thickness: every outer face below the plate bottom is T from the tool surface,
        # except the open sheared end faces that touch it.
        skip = [tool.top_face] + list(tool.shear_faces)
        shell = Part.Compound([tool.shape.getElement(n) for n in
                               ["Face%d" % (i + 1) for i in range(len(tool.shape.Faces))] if n not in skip])
        checked, wall_err = 0, 0.0
        for f in formed.Faces:
            if f.CenterOfMass.z < -thk - 1e-6:
                d = f.distToShape(shell)[0]
                if d < 1e-6:
                    continue  # open end face, flush with the tool's sheared face
                checked += 1
                wall_err = max(wall_err, abs(d - thk))
        self.assertGreater(checked, 0)
        self.assertLess(wall_err, 1e-6, "wall thickness differs from T")
        rows.append((name, over, d_tool, d_dv, wall_err, checked))
        return plate, tool, formed

    def test_each_tool_on_a_plate(self):
        for name, thk, over in PARAM_SETS:
            with self.subTest(tool=name, T=thk, over=over):
                self.run_set(name, thk, over)

    def test_feature_path_matches_direct_path(self):
        for name in FT.list_tools():
            with self.subTest(tool=name):
                plate = self.plate(2.0)
                tool = FT.make_tool(name)
                direct = FT.apply_tool(plate.Shape, top_face_name(plate.Shape), tool)
                feat = FT.make_forming_feature(self.doc, plate, top_face_name(plate.Shape), tool)
                self.assertNotIn("Invalid", feat.State)
                self.assertAlmostEqual(feat.Shape.Volume, direct.Volume, delta=1e-6 * direct.Volume)

    def test_offset_moves_the_tool(self):
        plate = self.plate(2.0)
        tool = FT.make_tool("dimple")
        a = FT.apply_tool(plate.Shape, top_face_name(plate.Shape), tool, offset=(10.0, -5.0))
        self.assertAlmostEqual(a.Volume - plate.Shape.Volume, tool.expected_volume_change(2.0), delta=1e-6 * 200)
        # the dimple floor (smallest z) now sits under (10, -5)
        low = min(a.Faces, key=lambda f: f.CenterOfMass.z)
        self.assertAlmostEqual(low.CenterOfMass.x, 10.0, delta=1e-6)
        self.assertAlmostEqual(low.CenterOfMass.y, -5.0, delta=1e-6)


class TestFormedPartUnfold(Base):
    """What the default (V1) unfolder does with a formed part: it flattens the plate but drops or
    distorts the formed region, so the flat is wrong. The Unfold feature must flag that (BendData
    problem, which also makes the DXF export refuse); with the forming feature suppressed the
    unfold is the plain plate and is not flagged."""

    def formed(self, name):
        plate = self.plate(2.0)
        tool = FT.make_tool(name)
        return plate, FT.make_forming_feature(self.doc, plate, top_face_name(plate.Shape), tool)

    def test_unfolder_flags_every_formed_part(self):
        for name in FT.list_tools():
            with self.subTest(tool=name):
                plate, feat = self.formed(name)
                flat = feature_unfold(self.doc, feat, unfold_target_face(feat.Shape), old=None)
                problem = json.loads(flat.BendData)["problem"] if flat.BendData else ""
                self.assertNotEqual(problem, "", "a wrong flat was not flagged")
                UNFOLD[name] = (flat.Shape.isValid(), flat.Shape.Volume, feat.Shape.Volume, problem)

    def test_suppressed_forming_unfolds_cleanly(self):
        for name in FT.list_tools():
            with self.subTest(tool=name):
                plate, feat = self.formed(name)
                feat.SuppressFeature = True
                self.doc.recompute()
                flat = feature_unfold(self.doc, feat, unfold_target_face(feat.Shape), old=None)
                problem = json.loads(flat.BendData)["problem"] if flat.BendData else ""
                self.assertEqual(problem, "")
                self.assertAlmostEqual(flat.Shape.Volume, plate.Shape.Volume, delta=1e-6 * plate.Shape.Volume)
                self.assertTrue(flat.Shape.isValid())


def csk_oracle(p, thickness):
    """Volume change of a countersunk hole formed into a plate, by numerical integration over z of
    the wall (annulus between the tool and its offset by T, below the plate bottom) minus the
    pocket, independent of FormingTool.expected_volume_change. Valid for stub >= T tan(angle/4)."""
    rh, rc, ang, stub = (p["hole_diameter"] / 2, p["countersink_diameter"] / 2, p["angle"], p["stub"])
    d = (rc - rh) / math.tan(math.radians(ang / 2))
    h = d + stub
    shift = thickness / math.cos(math.radians(ang / 2))

    def tool_r(z):
        return rh + (rc - rh) * (z + d) / d if z >= -d else rh

    def outer_r(z):  # the larger of the offset cone line and the offset cylinder
        return max(rh + (rc - rh) * (z + d) / d + shift, rh + thickness)

    def integ(f, a, b, n=20000):
        if b <= a:
            return 0.0
        step = (b - a) / n
        tot = f(a) + f(b)
        for i in range(1, n):
            tot += (4 if i % 2 else 2) * f(a + i * step)
        return tot * step / 3.0

    wall = integ(lambda z: math.pi * (outer_r(z) ** 2 - tool_r(z) ** 2), -h, -thickness)
    pocket = integ(lambda z: math.pi * tool_r(z) ** 2, -thickness, 0.0)
    return wall - pocket


class TestRepairs027(Base):
    """Run 027 verifier findings: countersink fallback, containment, face frame."""

    def test_countersink_sweep_never_silently_wrong(self):
        applied = refused = 0
        for thk in (1.0, 2.0, 3.0):
            for angle in (60.0, 82.0, 90.0, 100.0, 120.0):
                for stub in (0.1, 0.2, 0.5, 1.0, 1.5, 2.0, 3.0):
                    for hole, csk in ((4.0, 8.0), (5.0, 10.5), (4.0, 12.0)):
                        with self.subTest(T=thk, angle=angle, stub=stub, hole=hole, csk=csk):
                            tool = FT.make_tool("countersunk_hole", angle=angle, stub=stub,
                                                hole_diameter=hole, countersink_diameter=csk)
                            plate = self.plate(thk)
                            try:
                                formed = FT.apply_tool(plate.Shape, top_face_name(plate.Shape), tool)
                            except FT.FormingToolError:
                                refused += 1
                                continue
                            applied += 1
                            got = formed.Volume - plate.Shape.Volume
                            self.assertLess(abs(got - tool.expected_volume_change(thk)),
                                            1e-6 * max(abs(got), tool.volume))
                            self.assertLess(abs(got - csk_oracle(tool.params, thk)),
                                            1e-4 * max(abs(got), tool.volume))
        self.assertGreater(applied, 100)
        self.assertGreater(refused, 20)

    def test_countersink_stub_rule_and_defaults(self):
        for thk in (0.5, 1.0, 1.5, 2.0, 3.0):  # the default tool must work on every common sheet
            plate = self.plate(thk)
            FT.apply_tool(plate.Shape, top_face_name(plate.Shape), FT.make_tool("countersunk_hole"))
        for angle in (82.0, 90.0):
            need = FT.min_countersink_stub(angle, 3.0)
            plate = self.plate(3.0)
            FT.apply_tool(plate.Shape, top_face_name(plate.Shape),
                          FT.make_tool("countersunk_hole", angle=angle, stub=need * 1.01))
            with self.assertRaises(FT.FormingToolError) as cm:
                FT.apply_tool(plate.Shape, top_face_name(plate.Shape),
                              FT.make_tool("countersunk_hole", angle=angle, stub=need * 0.9))
            self.assertIn("stub", str(cm.exception))
        with self.assertRaises(FT.FormingToolError):
            FT.make_tool("countersunk_hole", angle=121.0)
        with self.assertRaises(FT.FormingToolError):
            FT.make_tool("countersunk_hole", angle=59.0)

    def test_reported_wrong_countersink_is_refused(self):
        plate = self.plate(3.0)
        tool = FT.make_tool("countersunk_hole", angle=82.0, hole_diameter=5.0,
                            countersink_diameter=10.5, stub=1.0)
        with self.assertRaises(FT.FormingToolError):
            FT.apply_tool(plate.Shape, top_face_name(plate.Shape), tool)

    def test_footprint_must_be_inside_the_face(self):
        for name in ("dimple", "rib", "louver", "lance", "extruded_hole", "countersunk_hole"):
            for off in ((26.0, 0.0), (40.0, 0.0)):
                with self.subTest(tool=name, offset=off):
                    plate = self.plate(2.0)
                    with self.assertRaises(FT.FormingToolError):
                        FT.apply_tool(plate.Shape, top_face_name(plate.Shape), FT.make_tool(name),
                                      offset=off)

    def test_overreaching_wall_is_refused_by_the_volume_check(self):
        plate = self.plate(2.0)  # hole footprint inside (29 < 30) but the collar wall reaches the edge
        with self.assertRaises(FT.FormingToolError) as cm:
            FT.apply_tool(plate.Shape, top_face_name(plate.Shape), FT.make_tool("extruded_hole"),
                          offset=(26.0, 0.0))
        self.assertIn("instead of the expected", str(cm.exception))

    def test_min_edge_distance(self):
        plate = self.plate(2.0)
        tool = FT.make_tool("dimple")  # radius 5, centre 10 from the middle: 15 from the edge
        FT.apply_tool(plate.Shape, top_face_name(plate.Shape), tool, offset=(10.0, 0.0),
                      min_edge_distance=14.0)
        with self.assertRaises(FT.FormingToolError) as cm:
            FT.apply_tool(plate.Shape, top_face_name(plate.Shape), tool, offset=(10.0, 0.0),
                          min_edge_distance=16.0)
        self.assertIn("below the minimum", str(cm.exception))

    def test_feature_refuses_off_plate_and_near_edge(self):
        plate = self.plate(2.0)
        feat = FT.make_forming_feature(self.doc, plate, top_face_name(plate.Shape),
                                       FT.make_tool("louver"), offset=(40.0, 0.0))
        self.assertIn("Invalid", feat.State)
        feat = FT.make_forming_feature(self.doc, plate, top_face_name(plate.Shape),
                                       FT.make_tool("dimple"), offset=(10.0, 0.0), min_edge_distance=16.0)
        self.assertIn("Invalid", feat.State)
        feat = FT.make_forming_feature(self.doc, plate, top_face_name(plate.Shape),
                                       FT.make_tool("dimple"), offset=(10.0, 0.0), min_edge_distance=14.0)
        self.assertNotIn("Invalid", feat.State)

    def l_faces(self):
        base = build_base(self.doc, Case("L", 2.0, 1.0, 0.42, [60.0, 40.0], [90.0]))
        faces = [(i + 1, f) for i, f in enumerate(base.Shape.Faces)
                 if f.Surface.TypeId == "Part::GeomPlane" and f.Area > 500]
        self.assertEqual(len(faces), 4)  # two walls, inside and outside
        return base, faces

    def test_offset_acts_in_the_face_frame_on_every_face(self):
        import SheetMetalFormingCmd as FC
        base, faces = self.l_faces()
        tool = FT.make_tool("dimple")
        want_dv = tool.expected_volume_change(2.0)
        for idx, face in faces:
            u, v, n = FC.face_frame(face)
            self.assertAlmostEqual(u.dot(v), 0.0, places=12)
            self.assertAlmostEqual(n.cross(u).dot(v), 1.0, places=12)
            for off in ((0.0, 0.0), (5.0, 3.0), (-4.0, 6.0)):
                with self.subTest(face=idx, offset=off):
                    formed = FT.apply_tool(base.Shape, "Face%d" % idx, tool, offset=off)
                    self.assertAlmostEqual(formed.Volume - base.Shape.Volume, want_dv, delta=1e-6 * 200)
                    # the dimple floor lies on the normal line through centre + off0 u + off1 v
                    centre = face.CenterOfMass + u * off[0] + v * off[1]
                    floor = [f for f in formed.Faces if f.Surface.TypeId == "Part::GeomPlane"
                             and abs(f.Area - math.pi * 9.0) < 1e-3]
                    self.assertEqual(len(floor), 1)
                    floor = floor[0]
                    rel = floor.CenterOfMass - centre
                    rel = rel - n * rel.dot(n)
                    self.assertLess(rel.Length, 1e-6)
                    feat = FT.make_forming_feature(self.doc, base, "Face%d" % idx, tool, offset=off)
                    self.assertNotIn("Invalid", feat.State)
                    self.assertAlmostEqual(feat.Shape.Volume, formed.Volume, delta=1e-6 * formed.Volume)

    def test_lance_on_a_wall_has_the_same_volume_change(self):
        base, faces = self.l_faces()
        tool = FT.make_tool("lance")
        want = tool.expected_volume_change(2.0)
        for idx, _face in faces:
            with self.subTest(face=idx):
                formed = FT.apply_tool(base.Shape, "Face%d" % idx, tool, offset=(5.0, 3.0))
                self.assertAlmostEqual(formed.Volume - base.Shape.Volume, want, delta=1e-6 * 150)


class TestFormedUnfoldRepairs(Base):
    """Run 027: an unsuppressed forming feature always flags the flat, and the volume check is
    tied to the bends' own expected volume change."""

    def flat_of(self, feat):
        return feature_unfold(self.doc, feat, unfold_target_face(feat.Shape), old=None)

    def test_forming_on_a_large_plate_is_flagged_and_dxf_refuses(self):
        for name in FT.list_tools():
            with self.subTest(tool=name):
                plate = self.doc.addObject("Part::Feature", "Big")
                plate.Shape = Part.makeBox(300.0, 300.0, 2.0, V(-150, -150, -2))
                self.doc.recompute()
                feat = FT.make_forming_feature(self.doc, plate, top_face_name(plate.Shape),
                                               FT.make_tool(name))
                self.assertNotIn("Invalid", feat.State)
                flat = self.flat_of(feat)
                problem = json.loads(flat.BendData)["problem"]
                self.assertIn("Suppress the forming features", problem)
                self.assertEqual(FP.forming_features(feat), [feat.Label])
                out = os.path.join(tempfile.gettempdir(), "g07rb_form.dxf")
                with self.assertRaises(ValueError):
                    UC.export_flat_dxf(flat, out)

    def test_forming_on_a_u_channel_is_flagged(self):
        case = Case("U", 2.0, 1.0, 0.42, [25.0, 200.0, 25.0], [90.0, 90.0])
        for name in FT.list_tools():
            with self.subTest(tool=name):
                doc_base = build_base(self.doc, case)
                web = max((f for f in doc_base.Shape.Faces if f.Surface.TypeId == "Part::GeomPlane"),
                          key=lambda f: f.Area)
                fn = "Face%d" % (1 + [i for i, f in enumerate(doc_base.Shape.Faces) if f.isSame(web)][0])
                feat = FT.make_forming_feature(self.doc, doc_base, fn, FT.make_tool(name))
                if "Invalid" in feat.State:
                    continue  # refused at the feature: nothing to unfold
                flat = self.flat_of(feat)
                self.assertIn("Suppress the forming features", json.loads(flat.BendData)["problem"])

    def test_suppressing_the_feature_clears_the_flag(self):
        plate = self.doc.addObject("Part::Feature", "Big")
        plate.Shape = Part.makeBox(300.0, 300.0, 2.0, V(-150, -150, -2))
        self.doc.recompute()
        feat = FT.make_forming_feature(self.doc, plate, top_face_name(plate.Shape), FT.make_tool("dimple"))
        feat.SuppressFeature = True
        self.doc.recompute()
        self.assertEqual(FP.forming_features(feat), [])
        self.assertEqual(json.loads(self.flat_of(feat).BendData)["problem"], "")

    def test_volume_check_alone_flags_a_formed_flat(self):
        """Without rule (a), the tightened volume bound still flags a formed flat on a plate where
        the form is more than 1e-4 of the volume (the verifier's 300 x 300 lance, 3e-5, is the
        case only rule (a) can catch)."""
        for name in ("dimple", "extruded_hole", "rib", "louver"):
            with self.subTest(tool=name):
                plate = self.doc.addObject("Part::Feature", "P")
                plate.Shape = Part.makeBox(100.0, 100.0, 2.0, V(-50, -50, -2))
                self.doc.recompute()
                feat = FT.make_forming_feature(self.doc, plate, top_face_name(plate.Shape), FT.make_tool(name))
                flat = self.flat_of(feat)
                problem = FP.flat_problem(flat.Shape, V(0, 0, 1), [], 2.0, 0, feat.Shape.Volume)
                self.assertIn("volume differs", problem)

    def test_expected_bend_volume_change_matches_the_goldens(self):
        """Over the golden flats, flat - part volume equals the sum of the bends' expected
        W T (BA - theta (r + T / 2)) to far better than VOLUME_RESIDUAL."""
        worst = 0.0
        for case in C.all_cases():
            doc = FreeCAD.newDocument("Res")
            try:
                feat, _ = C.build_flat(doc, case)
                bends, _n, problem = FP.bends_and_problem_from_json(feat.BendData)
                self.assertEqual(problem, "", case.name)
                src = None
                for prop in feat.PropertiesList:
                    val = getattr(feat, prop)
                    if hasattr(val, "Shape") and prop != "Shape" and val is not feat:
                        src = val
                        break
                    if isinstance(val, tuple) and len(val) == 2 and hasattr(val[0], "Shape"):
                        src = val[0]
                        break
                residual = abs((feat.Shape.Volume - src.Shape.Volume) - FP.expected_bend_volume_change(bends))
                worst = max(worst, residual / src.Shape.Volume)
            finally:
                FreeCAD.closeDocument(doc.Name)
        print("largest golden residual / part volume: %.2e (bound %.0e)" % (worst, FP.VOLUME_RESIDUAL))
        self.assertLess(worst, FP.VOLUME_RESIDUAL / 100.0)


def print_rows():
    print("%-17s %-60s %9s %9s %9s %5s" % ("tool", "params", "d_toolvol", "d_dV", "wall_err", "faces"))
    for r in rows:
        print("%-17s %-60s %9.2e %9.2e %9.2e %5d" % (r[0], str(r[1]), r[2], r[3], r[4], r[5]))
    for k, v in UNFOLD.items():
        print("unfold", k, "valid", v[0], "flatV %.1f srcV %.1f" % (v[1], v[2]), v[3][:70])


if __name__ == "__main__":
    unittest.main()
