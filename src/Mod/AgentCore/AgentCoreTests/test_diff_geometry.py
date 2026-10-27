# SPDX-License-Identifier: LGPL-2.1-or-later
"""F3: face-level geometry diff (signature match, sampled deviation, local booleans)."""

import math
import os
import sys
import unittest

import FreeCAD as App
import Part

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "diff")
if FIX not in sys.path:
    sys.path.insert(0, FIX)

import diff_builders as db  # noqa: E402
from AgentCore.diff import AgentTurn, ApplyAuthority, match_faces, shape_diff  # noqa: E402
from AgentCore.diff import geometry  # noqa: E402

V = App.Vector


def holed(r, length=40):
    box = Part.makeBox(length, 30, 10)
    return box.cut(Part.makeCylinder(r, 20, V(10, 10, -5)))


class TestFaceMatcher(unittest.TestCase):
    def test_identical_shapes_match_exactly_with_zero_deviation(self):
        a, b = holed(3), holed(3)
        pairs, ub, ua, how = match_faces(a.Faces, b.Faces)
        self.assertEqual((len(pairs), ub, ua), (len(a.Faces), [], []))
        self.assertTrue(all(h == "exact" for h in how.values()))
        d = shape_diff(a, b)
        self.assertEqual(d["status"], "unchanged")
        self.assertEqual(d["max_deviation"], 0.0)
        self.assertNotIn("region", d)                      # no boolean when nothing changed

    def test_reordered_faces_still_match(self):
        a = holed(3)
        b = Part.Shell(list(reversed(a.Faces)))
        pairs, ub, ua, _ = match_faces(a.Faces, b.Faces)
        self.assertEqual((len(pairs), ub, ua), (len(a.Faces), [], []))
        for i, j in pairs:
            self.assertAlmostEqual(geometry.deviation(a.Faces[i], b.Faces[j]), 0.0, places=7)

    def test_bigger_hole_is_local_change(self):
        a, b = holed(3), holed(4)
        d = shape_diff(a, b)
        self.assertEqual(d["status"], "modified")
        self.assertEqual(d["faces_before"], d["faces_after"])
        # the 4 side walls are untouched; the hole wall and the top/bottom faces change
        self.assertEqual(d["unchanged_faces"], 4)
        mod = {m["after"] for m in d["modified_faces"]}
        kinds = {geometry._kind(b.Faces[int(n[4:]) - 1]) for n in mod}
        self.assertIn("Part::GeomCylinder", kinds)
        self.assertAlmostEqual(max(m["max_deviation"] for m in d["modified_faces"]), 1.0, places=3)
        reg = d["region"]
        self.assertLess(reg["fraction"], 0.5)              # booleans ran on the changed region only
        expect = math.pi * (4 ** 2 - 3 ** 2) * 10
        self.assertAlmostEqual(reg["removed_volume"], expect, delta=1e-3 * expect)
        self.assertAlmostEqual(reg["added_volume"], 0.0, places=6)
        self.assertAlmostEqual(d["volume_before"] - d["volume_after"], expect, delta=1e-3 * expect)

    def test_added_material_reported_as_added(self):
        a, b = holed(3, 40), holed(3, 45)
        d = shape_diff(a, b)
        self.assertEqual(d["status"], "modified")
        self.assertAlmostEqual(d["region"]["added_volume"], 5 * 30 * 10, delta=1e-3)
        self.assertAlmostEqual(d["region"]["removed_volume"], 0.0, places=6)

    def test_new_face_is_added_not_matched(self):
        a = Part.makeBox(10, 10, 10)
        b = a.cut(Part.makeBox(2, 2, 2, V(4, 4, 8)))   # a pocket in the top face
        d = shape_diff(a, b)
        self.assertEqual(d["faces_after"] - d["faces_before"], 5)
        self.assertEqual(len(d["added_faces"]), 5)
        self.assertAlmostEqual(d["region"]["removed_volume"], 8.0, places=6)
        self.assertLess(d["region"]["fraction"], 0.2)

    def test_added_and_removed_whole_shapes(self):
        s = Part.makeBox(1, 2, 3)
        self.assertEqual(shape_diff(None, s)["status"], "added")
        self.assertEqual(shape_diff(s, None)["status"], "removed")
        self.assertAlmostEqual(shape_diff(None, s)["volume"], 6.0)


class TestGeometryInTurn(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.auth = ApplyAuthority.install()

    @classmethod
    def tearDownClass(cls):
        cls.auth.uninstall()

    def test_turn_geometry_diff_and_reject_restores_geometry(self):
        doc = db.build_holed_block("f3_geo")
        try:
            vol, area = doc.Cut.Shape.Volume, doc.Cut.Shape.Area
            with AgentTurn(doc, label="hole 3->4", verify=False) as t:
                doc.Hole.Radius = 4
            g = t.change_set()["geometry"]
            self.assertEqual(g["Cut"]["status"], "modified")
            self.assertLess(g["Cut"]["region"]["fraction"], 0.5)
            self.assertEqual(g["Hole"]["status"], "modified")
            self.assertNotIn("Block", g)                   # unchanged shapes are not diffed
            t.reject()
            after = doc.Cut.Shape
            self.assertAlmostEqual(after.Volume, vol, places=9)
            self.assertAlmostEqual(after.Area, area, places=9)
            self.assertEqual(shape_diff(t.before_shapes["Cut"], after)["status"], "unchanged")
        finally:
            db.close(doc)


if __name__ == "__main__":
    unittest.main()
