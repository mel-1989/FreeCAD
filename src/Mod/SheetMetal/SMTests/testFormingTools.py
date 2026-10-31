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
import unittest

import FreeCAD
import Part
from FreeCAD import Vector as V

import SheetMetalFormingTools as FT
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


def print_rows():
    print("%-17s %-60s %9s %9s %9s %5s" % ("tool", "params", "d_toolvol", "d_dV", "wall_err", "faces"))
    for r in rows:
        print("%-17s %-60s %9.2e %9.2e %9.2e %5d" % (r[0], str(r[1]), r[2], r[3], r[4], r[5]))
    for k, v in UNFOLD.items():
        print("unfold", k, "valid", v[0], "flatV %.1f srcV %.1f" % (v[1], v[2]), v[3][:70])


if __name__ == "__main__":
    unittest.main()
