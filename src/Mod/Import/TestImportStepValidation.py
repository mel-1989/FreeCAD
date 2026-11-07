# SPDX-License-Identifier: LGPL-2.1-or-later

# **************************************************************************
#   Tests for STEP geometric validation properties (G13 slice 1a).        *
#   Behaviour follows the CAx-IF Recommended Practices for Geometric and  *
#   Assembly Validation Properties, v4.2 (2014-10-09).                    *
# **************************************************************************

import os
import re
import tempfile
import unittest

import FreeCAD as App
import Import
import Part

PREF = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Part/STEP")
REL = 1e-9


def _close(a, b, rel=REL):
    return abs(a - b) <= rel * max(abs(a), abs(b), 1e-300)


class StepValidationCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="g13test")
        self.doc = App.newDocument("G13Test")
        self.saved = PREF.GetBool("WriteValidationProperties", True)
        self.savedCheck = PREF.GetBool("CheckValidationProperties", True)
        PREF.SetBool("WriteValidationProperties", True)
        PREF.SetBool("CheckValidationProperties", True)

    def tearDown(self):
        PREF.SetBool("WriteValidationProperties", self.saved)
        PREF.SetBool("CheckValidationProperties", self.savedCheck)
        App.closeDocument(self.doc.Name)
        for f in os.listdir(self.dir):
            os.remove(os.path.join(self.dir, f))
        os.rmdir(self.dir)

    # ---- helpers -------------------------------------------------------
    def path(self, name):
        return os.path.join(self.dir, name)

    def shape_object(self, shape, name):
        obj = self.doc.addObject("Part::Feature", name)
        obj.Shape = shape
        self.doc.recompute()
        return obj

    def export(self, objs, name="out.step"):
        p = self.path(name)
        Import.export(objs, p)
        return p

    def text(self, p):
        with open(p, "r", errors="replace") as fh:
            return fh.read()

    def write_text(self, p, t):
        with open(p, "w") as fh:
            fh.write(t)

    def parts(self, p):
        return Import.validateStep(p)["parts"]

    def mutate(self, p, out, volume=1.0, area=1.0):
        """Edit the stated figures in an exported file."""
        t = re.sub(r"\s*\n\s*", "", self.text(p).replace("\r", ""))
        t = re.sub(r"VOLUME_MEASURE\(([-+0-9.eE]+)\)", lambda m: "VOLUME_MEASURE(%.17g)" % (float(m.group(1)) * volume), t)
        t = re.sub(r"AREA_MEASURE\(([-+0-9.eE]+)\)", lambda m: "AREA_MEASURE(%.17g)" % (float(m.group(1)) * area), t)
        t = t.replace(";#", ";\n#")
        self.write_text(out, t)
        return out

    def move_centroid(self, p, out, dx):
        t = re.sub(r"\s*\n\s*", "", self.text(p).replace("\r", ""))
        def mv(m):
            x, y, z = (float(v) for v in m.group(1).split(","))
            return "CARTESIAN_POINT('centre point',(%.17g,%.17g,%.17g))" % (x + dx, y, z)
        t = re.sub(r"CARTESIAN_POINT\('centre point',\(([^)]*)\)\)", mv, t)
        t = t.replace(";#", ";\n#")
        self.write_text(out, t)
        return out

    def capture_stderr(self, fn):
        """Run fn with fd 1 and 2 redirected to a file; return what the console wrote."""
        import sys
        sys.stdout.flush()
        sys.stderr.flush()
        tmp = self.path("console.txt")
        saved = (os.dup(1), os.dup(2))
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
        try:
            os.dup2(fd, 1)
            os.dup2(fd, 2)
            fn()
        finally:
            os.dup2(saved[0], 1)
            os.dup2(saved[1], 2)
            os.close(saved[0])
            os.close(saved[1])
            os.close(fd)
        return self.text(tmp)

    def assert_stated_equals_shape(self, part, shape):
        solids = shape.Solids
        if solids:
            self.assertTrue(_close(part["stated"]["volume"], sum(s.Volume for s in solids)))
        else:
            self.assertIsNone(part["stated"]["volume"])
        self.assertTrue(_close(part["stated"]["area"], shape.Area))
        c = part["stated"]["centroid"]
        if solids:
            total = sum(x.Volume for x in solids)
            com = App.Vector(0, 0, 0)
            for x in solids:
                com = com + x.CenterOfMass * (x.Volume / total)
        else:
            com = shape.CenterOfMass
        for a, b in zip(c, (com.x, com.y, com.z)):
            self.assertTrue(abs(a - b) < 1e-9 * max(1.0, abs(b), shape.BoundBox.DiagonalLength))


class TestApi(StepValidationCase):
    def test_missing_file_is_failed_not_an_exception(self):
        r = Import.validateStep(self.path("does_not_exist.step"))
        self.assertEqual(r["verdict"], "failed")
        self.assertEqual(r["parts"], [])

    def test_garbage_file_is_failed(self):
        p = self.path("garbage.step")
        self.write_text(p, "this is not STEP\x00\x01\x02" * 50)
        self.assertEqual(Import.validateStep(p)["verdict"], "failed")

    def test_empty_file_is_failed(self):
        p = self.path("empty.step")
        self.write_text(p, "")
        self.assertEqual(Import.validateStep(p)["verdict"], "failed")

    def test_result_shape(self):
        box = self.shape_object(Part.makeBox(10, 20, 30), "Box")
        r = Import.validateStep(self.export([box]))
        self.assertEqual(set(r), {"verdict", "schema", "message", "parts"})
        self.assertTrue(r["schema"].startswith("AUTOMOTIVE_DESIGN"))
        part = r["parts"][0]
        for k in ("name", "verdict", "stated", "measured", "difference", "disagrees", "valid", "diagonal"):
            self.assertIn(k, part)
        for k in ("volume", "area", "centroid"):
            self.assertIn(k, part["stated"])
            self.assertIn(k, part["measured"])
            self.assertIn(k, part["difference"])
        self.assertEqual(part["name"], "Box")


class TestWrite(StepValidationCase):
    def test_box_round_trip(self):
        shape = Part.makeBox(10, 20, 30)
        box = self.shape_object(shape, "Box")
        p = self.export([box])
        t = self.text(p)
        self.assertIn("geometric validation property", t)
        # the writer may wrap a long string over two physical lines
        self.assertIn(
            "CAx-IF Rec.Pracs.---Geometric and Assembly Validation Properties---4.2---2014-10-09",
            t.replace("\n", ""),
        )
        r = Import.validateStep(p)
        self.assertEqual(r["verdict"], "agree")
        self.assertEqual(len(r["parts"]), 1)
        self.assert_stated_equals_shape(r["parts"][0], shape)

    def test_holed_solid(self):
        shape = Part.makeBox(40, 40, 40).cut(Part.makeCylinder(5, 60, App.Vector(20, 20, -10)))
        obj = self.shape_object(shape, "Holed")
        r = Import.validateStep(self.export([obj]))
        self.assertEqual(r["verdict"], "agree")
        self.assert_stated_equals_shape(r["parts"][0], shape)

    def test_compound_of_two_solids(self):
        a = Part.makeBox(10, 10, 10)
        b = Part.makeSphere(4, App.Vector(30, 5, 5))
        shape = Part.makeCompound([a, b])
        obj = self.shape_object(shape, "Pair")
        r = Import.validateStep(self.export([obj]))
        self.assertEqual(r["verdict"], "agree")
        total = sum(p["stated"]["volume"] for p in r["parts"])
        self.assertTrue(_close(total, a.Volume + b.Volume))

    def test_surface_only_part(self):
        shape = Part.makePlane(10, 20)
        obj = self.shape_object(shape, "Sheet")
        p = self.export([obj])
        r = Import.validateStep(p)
        self.assertEqual(r["verdict"], "agree")
        part = r["parts"][0]
        self.assertIsNone(part["stated"]["volume"])
        self.assertTrue(_close(part["stated"]["area"], 200.0))
        for a, b in zip(part["stated"]["centroid"], (5.0, 10.0, 0.0)):
            self.assertAlmostEqual(a, b, places=9)

    def make_assembly(self):
        box = self.shape_object(Part.makeBox(10, 20, 30), "Box")
        cyl = self.shape_object(Part.makeCylinder(5, 40), "Cyl")
        asm = self.doc.addObject("App::Part", "Asm")
        sub = self.doc.addObject("App::Part", "Sub")
        asm.addObject(sub)
        l1 = self.doc.addObject("App::Link", "L1")
        l1.LinkedObject = box
        l1.Placement = App.Placement(App.Vector(100, 0, 0), App.Rotation(App.Vector(0, 0, 1), 30))
        l2 = self.doc.addObject("App::Link", "L2")
        l2.LinkedObject = box
        l2.Placement = App.Placement(App.Vector(0, 100, 50), App.Rotation(App.Vector(1, 0, 0), 90))
        c1 = self.doc.addObject("App::Link", "C1")
        c1.LinkedObject = cyl
        c1.Placement = App.Placement(App.Vector(7, 8, 9), App.Rotation())
        sub.addObject(l1)
        sub.addObject(l2)
        asm.addObject(c1)
        sub.Placement = App.Placement(App.Vector(1, 2, 3), App.Rotation(App.Vector(0, 1, 0), 20))
        self.doc.recompute()
        return asm, box, cyl

    def test_assembly_instances_agree_in_the_parts_own_frame(self):
        asm, box, cyl = self.make_assembly()
        r = Import.validateStep(self.export([asm]))
        self.assertEqual(r["verdict"], "agree")
        by_name = {p["name"]: p for p in r["parts"]}
        # the repeated part appears once, the assemblies carry no figures of their own
        self.assertEqual(sorted(by_name), ["Box", "Cyl"])
        self.assert_stated_equals_shape(by_name["Box"], box.Shape)
        self.assert_stated_equals_shape(by_name["Cyl"], cyl.Shape)
        # the stated centroid is in the part's own frame, not the placed one
        for a, b in zip(by_name["Box"]["stated"]["centroid"], (5.0, 10.0, 15.0)):
            self.assertAlmostEqual(a, b, places=9)

    def test_off_switch_writes_nothing(self):
        box = self.shape_object(Part.makeBox(10, 20, 30), "Box")
        PREF.SetBool("WriteValidationProperties", False)
        p = self.export([box])
        t = self.text(p)
        self.assertNotIn("validation property", t)
        self.assertNotIn("CAx-IF Rec.Pracs", t)
        self.assertEqual(Import.validateStep(p)["verdict"], "no_properties")


class TestVerdicts(StepValidationCase):
    def exported_box(self):
        return self.export([self.shape_object(Part.makeBox(10, 20, 30), "Box")])

    def test_volume_plus_one_percent_disagrees(self):
        p = self.mutate(self.exported_box(), self.path("v.step"), volume=1.01)
        part = Import.validateStep(p)["parts"][0]
        self.assertEqual(part["verdict"], "disagree")
        self.assertTrue(part["disagrees"]["volume"])
        self.assertFalse(part["disagrees"]["area"])

    def test_area_minus_one_percent_disagrees(self):
        p = self.mutate(self.exported_box(), self.path("a.step"), area=0.99)
        part = Import.validateStep(p)["parts"][0]
        self.assertEqual(part["verdict"], "disagree")
        self.assertTrue(part["disagrees"]["area"])
        self.assertFalse(part["disagrees"]["volume"])

    def test_centroid_moved_one_percent_of_diagonal_disagrees(self):
        diag = Part.makeBox(10, 20, 30).BoundBox.DiagonalLength
        p = self.move_centroid(self.exported_box(), self.path("c.step"), 0.01 * diag)
        part = Import.validateStep(p)["parts"][0]
        self.assertEqual(part["verdict"], "disagree")
        self.assertTrue(part["disagrees"]["centroid"])

    def test_inside_tolerance_agrees(self):
        p = self.mutate(self.exported_box(), self.path("t.step"), volume=1.0005, area=0.9995)
        self.assertEqual(Import.validateStep(p)["parts"][0]["verdict"], "agree")

    def test_tolerance_edge_just_outside(self):
        p = self.mutate(self.exported_box(), self.path("t2.step"), volume=1.0015)
        self.assertEqual(Import.validateStep(p)["parts"][0]["verdict"], "disagree")

    def test_zero_stated_volume_is_unusable(self):
        p = self.mutate(self.exported_box(), self.path("z.step"), volume=0.0)
        part = Import.validateStep(p)["parts"][0]
        self.assertEqual(part["verdict"], "unusable_properties")

    def test_disagree_outranks_unusable(self):
        p = self.mutate(self.exported_box(), self.path("z2.step"), volume=0.0, area=1.05)
        self.assertEqual(Import.validateStep(p)["parts"][0]["verdict"], "disagree")

    def test_no_properties(self):
        PREF.SetBool("WriteValidationProperties", False)
        p = self.exported_box()
        r = Import.validateStep(p)
        self.assertEqual(r["verdict"], "no_properties")
        self.assertEqual(r["parts"][0]["verdict"], "no_properties")

    def test_file_verdict_is_the_worst_part(self):
        asm = TestWrite.make_assembly(self)[0]
        good = self.export([asm])
        t = re.sub(r"\s*\n\s*", "", self.text(good))
        t = t.replace(";#", ";\n#")
        # change only the first stated volume in the file (one of the two parts)
        t = re.sub(r"VOLUME_MEASURE\(([-+0-9.eE]+)\)", lambda m: "VOLUME_MEASURE(%.17g)" % (float(m.group(1)) * 1.5), t, count=1)
        q = self.path("one_bad.step")
        self.write_text(q, t)
        r = Import.validateStep(q)
        verdicts = sorted(p["verdict"] for p in r["parts"])
        self.assertEqual(verdicts, ["agree", "disagree"])
        self.assertEqual(r["verdict"], "disagree")

    def test_other_validation_property_types_are_ignored(self):
        p = self.exported_box()
        t = self.text(p).replace("'geometric validation property'", "'pmi validation property'")
        q = self.path("pmi.step")
        self.write_text(q, t)
        self.assertEqual(Import.validateStep(q)["verdict"], "no_properties")

    def test_wetted_area_measure_is_read(self):
        p = self.exported_box()
        t = self.text(p).replace("'surface area measure'", "'wetted area measure'")
        q = self.path("wetted.step")
        self.write_text(q, t)
        part = Import.validateStep(q)["parts"][0]
        self.assertTrue(_close(part["stated"]["area"], 2200.0))
        self.assertEqual(part["verdict"], "agree")

    def test_area_just_outside_tolerance_disagrees(self):
        p = self.mutate(self.exported_box(), self.path("a2.step"), area=0.9985)
        self.assertEqual(Import.validateStep(p)["parts"][0]["verdict"], "disagree")

    def test_centroid_tolerance_is_a_fraction_of_the_diagonal(self):
        big = self.shape_object(Part.makeBox(1000, 1000, 1000), "Big")
        p = self.export([big], "big.step")
        diag = 1000.0 * 3 ** 0.5
        inside = self.move_centroid(p, self.path("c_in.step"), 0.0005 * diag)
        outside = self.move_centroid(p, self.path("c_out.step"), 0.0015 * diag)
        self.assertEqual(Import.validateStep(inside)["parts"][0]["verdict"], "agree")
        self.assertEqual(Import.validateStep(outside)["parts"][0]["verdict"], "disagree")

    def test_unit_prefix_is_applied(self):
        """The same part written in centimetres: figures must be converted to mm."""
        p = self.exported_box()
        t = re.sub(r"\s*\n\s*", "", self.text(p)).replace("SI_UNIT(.MILLI.,.METRE.)", "SI_UNIT(.CENTI.,.METRE.)")
        t = t.replace(";#", ";\n#")
        # coordinates are now centimetres: the arrived solid is 10x larger, so scale the figures
        q = self.path("cm.step")
        self.write_text(q, t)
        part = Import.validateStep(q)["parts"][0]
        self.assertEqual(part["verdict"], "agree")
        self.assertTrue(_close(part["stated"]["volume"], 6000.0 * 1000.0))
        self.assertTrue(_close(part["measured"]["volume"], 6000.0 * 1000.0))

    def test_conversion_based_inch_is_applied(self):
        p = self.exported_box()
        t = re.sub(r"\s*\n\s*", "", self.text(p))
        mm = "( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) )"
        self.assertIn(mm.replace(" ", ""), t.replace(" ", ""))
        t = re.sub(r"\(\s*LENGTH_UNIT\(\)\s*NAMED_UNIT\(\*\)\s*SI_UNIT\(\.MILLI\.,\.METRE\.\)\s*\)",
                   "(CONVERSION_BASED_UNIT('INCH',#9001)LENGTH_UNIT()NAMED_UNIT(#9003))", t)
        extra = ("#9001=LENGTH_MEASURE_WITH_UNIT(LENGTH_MEASURE(25.4),#9002);"
                 "#9002=(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT(.MILLI.,.METRE.));"
                 "#9003=DIMENSIONAL_EXPONENTS(1.,0.,0.,0.,0.,0.,0.);")
        t = t.replace("ENDSEC;END-ISO", extra + "ENDSEC;END-ISO")
        t = t.replace(";#", ";\n#")
        q = self.path("inch.step")
        self.write_text(q, t)
        part = Import.validateStep(q)["parts"][0]
        self.assertEqual(part["verdict"], "agree")
        self.assertTrue(_close(part["stated"]["volume"], 6000.0 * 25.4 ** 3, 1e-9))
        self.assertTrue(_close(part["measured"]["volume"], 6000.0 * 25.4 ** 3, 1e-9))


class TestImportBehaviour(StepValidationCase):
    def exported_box(self):
        return self.export([self.shape_object(Part.makeBox(10, 20, 30), "Box")])

    def import_into_new_doc(self):
        d = App.newDocument("G13Imp")
        try:
            Import.insert(self.imp_path, d.Name)
            return [o.Shape.exportBrepToString() for o in d.Objects if hasattr(o, "Shape") and not o.Shape.isNull()]
        finally:
            App.closeDocument(d.Name)

    def test_one_warning_per_disagreeing_part_and_none_for_agree(self):
        good = self.exported_box()
        bad = self.mutate(good, self.path("bad.step"), volume=1.02)
        self.imp_path = good
        out = self.capture_stderr(self.import_into_new_doc)
        self.assertNotIn("STEP validation", out)
        self.imp_path = bad
        out = self.capture_stderr(self.import_into_new_doc)
        warnings = [l for l in out.splitlines() if "STEP validation" in l]
        self.assertEqual(len(warnings), 1, out)
        w = warnings[0]
        self.assertIn("'Box'", w)
        self.assertIn("disagree", w)
        self.assertIn("volume stated", w)
        self.assertIn("measured", w)

    def test_import_does_not_change_the_geometry(self):
        bad = self.mutate(self.exported_box(), self.path("bad.step"), volume=1.02)
        self.imp_path = bad
        PREF.SetBool("CheckValidationProperties", True)
        with_check = self.import_into_new_doc()
        PREF.SetBool("CheckValidationProperties", False)
        without = self.import_into_new_doc()
        self.assertEqual(with_check, without)
        self.assertTrue(with_check)

    def test_check_can_be_switched_off(self):
        bad = self.mutate(self.exported_box(), self.path("bad.step"), volume=1.02)
        self.imp_path = bad
        PREF.SetBool("CheckValidationProperties", False)
        out = self.capture_stderr(self.import_into_new_doc)
        self.assertNotIn("STEP validation", out)

    def test_assembly_with_repeated_part_warns_once(self):
        asm = TestWrite.make_assembly(self)[0]
        good = self.export([asm])
        bad = self.mutate(good, self.path("bad_asm.step"), volume=1.05)
        self.imp_path = bad
        out = self.capture_stderr(self.import_into_new_doc)
        warnings = [l for l in out.splitlines() if "STEP validation" in l]
        self.assertEqual(len(warnings), 2, out)  # Box (placed twice) and Cyl: once each


if __name__ == "__main__":
    unittest.main()
