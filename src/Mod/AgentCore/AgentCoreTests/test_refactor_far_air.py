# SPDX-License-Identifier: LGPL-2.1-or-later
"""F6 R8: a Length pocket/pad that does nothing now but would act under a plausible Length change is kept,
however far the sketch sits from the part (the verifier's far-air probes, run 001)."""

import unittest

import FreeCAD

from AgentCore import refactor
from AgentCoreTests.fixtures.refactor import refactor_builders as rb


class TestFarAirIsKept(unittest.TestCase):
    def _check(self, build, name):
        doc = FreeCAD.newDocument("F6far")
        try:
            body, *_ = rb.plate_with_slot(doc)
            build(doc, body)
            doc.recompute(None, True)
            self.assertEqual(refactor.find_redundant(doc), [], name)
            r = refactor.merge_redundant(doc)
            self.assertEqual(r["removed"], [], name)
            self.assertIsNotNone(doc.getObject(name))
        finally:
            FreeCAD.closeDocument(doc.Name)

    def _air(self, offset, depth):
        def build(doc, body):
            sk = rb.rect_sketch(body, 4.0, 4.0, "AirSketch", offset=offset)
            rb.pocket(body, sk, depth, "AirPocket")
        return build

    def test_gap_20_depth_1(self):
        self._check(self._air(30.0, 1.0), "AirPocket")

    def test_gap_50_depth_half(self):
        self._check(self._air(60.0, 0.5), "AirPocket")

    def test_gap_100_depth_1(self):
        self._check(self._air(110.0, 1.0), "AirPocket")

    def test_gap_9_5_depth_half(self):
        self._check(self._air(19.5, 0.5), "AirPocket")

    def test_half_mm_pad_inside_plate(self):
        def build(doc, body):
            sk = rb.rect_sketch(body, 4.0, 4.0, "InSk", offset=2.0)
            rb.pad(body, sk, 0.5, "InPad")
        self._check(build, "InPad")


if __name__ == "__main__":
    unittest.main()
