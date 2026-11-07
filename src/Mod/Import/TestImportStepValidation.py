# SPDX-License-Identifier: LGPL-2.1-or-later

# **************************************************************************
#   Tests for STEP geometric validation properties (G13 slice 1a).        *
#   Behaviour follows the CAx-IF Recommended Practices for Geometric and  *
#   Assembly Validation Properties, v4.2 (2014-10-09).                    *
# **************************************************************************

import math
import os
import re
import tempfile
import time
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

    def flat(self, p):
        return re.sub(r"\s*\n\s*", "", self.text(p).replace("\r", ""))

    def save(self, t, name):
        q = self.path(name)
        self.write_text(q, t.replace(";#", ";\n#"))
        return q

    def set_area(self, p, name, value):
        """Set the absolute value of the 'surface area measure' item."""
        t = re.sub(r"('surface area measure',AREA_MEASURE\()[^)]*\)", lambda m: m.group(1) + repr(value) + ")", self.flat(p))
        return self.save(t, name)

    def drop_loose_faces(self, p, name):
        """What an importer that loses the independent surfaces would have received."""
        t = self.flat(p)
        faces = set(re.findall(r"#(\d+)\s*=\s*(?:SHELL_BASED_SURFACE_MODEL|ADVANCED_FACE)\(", t))
        solid_items = set(re.findall(r"#(\d+)\s*=\s*MANIFOLD_SOLID_BREP\(", t))
        def trim(m):
            kept = [i for i in m.group(2).split(",") if i.strip().lstrip("#") not in faces or i.strip().lstrip("#") in solid_items]
            return "%s('',(%s)" % (m.group(1), ",".join(kept))
        t2 = re.sub(r"(\w*SHAPE_REPRESENTATION)\(\'\',\(([^)]*)\)", trim, t)
        self.assertNotEqual(t, t2)
        return self.save(t2, name)

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

    def test_malformed_product_record_does_not_kill_the_caller(self):
        box = self.shape_object(Part.makeBox(10, 20, 30), "Box")
        good = self.export([box])
        text = open(good, errors="replace").read()
        record = re.search(r"#(\d+)\s*=\s*PRODUCT\s*\(.*?\)\s*;", text, re.S)
        self.assertIsNotNone(record)
        for tag, args in (("short", "'Box'"), ("numbers", "1,2,3,4"), ("none", "")):
            p = self.path("product_%s.step" % tag)
            with open(p, "w") as f:
                f.write(
                    text[: record.start()]
                    + "#%s=PRODUCT(%s);" % (record.group(1), args)
                    + text[record.end() :]
                )
            result = Import.validateStep(p)
            self.assertIn(
                result["verdict"],
                ("agree", "disagree", "invalid_geometry", "no_properties", "unusable_properties", "failed"),
            )

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
        # practice 4.7: independent surfaces are a class of their own, with their own magic strings
        shape = Part.makePlane(10, 20)
        obj = self.shape_object(shape, "Sheet")
        p = self.export([obj])
        r = Import.validateStep(p)
        self.assertEqual(r["verdict"], "agree")
        part = r["parts"][0]
        self.assertIsNone(part["stated"]["volume"])
        self.assertIsNone(part["stated"]["area"])
        self.assertIsNone(part["stated"]["centroid"])
        self.assertTrue(_close(part["stated"]["independent_area"], 200.0))
        for a, b in zip(part["stated"]["independent_centroid"], (5.0, 10.0, 0.0)):
            self.assertAlmostEqual(a, b, places=9)
        t = self.text(p).replace("\n", "")
        self.assertIn("'independent surface area measure'", t)
        self.assertIn("'surface centre point'", t)
        self.assertNotIn("'surface area measure'", t)
        self.assertNotIn("'centre point'", t)

    def test_solid_with_a_loose_face_writes_each_class_separately(self):
        # practice 4.5: solids and independent surfaces each have their own area and centroid
        box = Part.makeBox(10, 20, 30)
        face = Part.makePlane(7, 7, App.Vector(100, 0, 0))
        obj = self.shape_object(Part.makeCompound([box, face]), "Mixed")
        r = Import.validateStep(self.export([obj]))
        self.assertEqual(r["verdict"], "agree")
        st = r["parts"][0]["stated"]
        self.assertTrue(_close(st["volume"], 6000.0))
        self.assertTrue(_close(st["area"], 2200.0))
        self.assertTrue(_close(st["independent_area"], 49.0))
        for a, b in zip(st["centroid"], (5.0, 10.0, 15.0)):
            self.assertAlmostEqual(a, b, places=9)
        for a, b in zip(st["independent_centroid"], (103.5, 3.5, 0.0)):
            self.assertAlmostEqual(a, b, places=9)

    def test_non_ascii_product_name_keeps_the_independent_class(self):
        # the writer matches a part's independent surfaces by its product name, which is UTF-8 in the file
        face = Part.makePlane(9, 9, App.Vector(50, 0, 0))
        obj = self.shape_object(Part.makeCompound([Part.makeBox(5, 5, 5), face]), "Bracket")
        obj.Label = "Brkt \u00dcn\u00ef \u5b54"
        r = Import.validateStep(self.export([obj]))
        self.assertEqual(r["verdict"], "agree", r)
        self.assertTrue(_close(r["parts"][0]["stated"]["independent_area"], 81.0))

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

    def hollow(self):
        # a 30 mm cube with a 10 mm cavity: area 6000 with the void, 5400 without (practice 4.6.2)
        shape = Part.makeBox(30, 30, 30).cut(Part.makeBox(10, 10, 10, App.Vector(10, 10, 10)))
        return self.export([self.shape_object(shape, "Hollow")], "hollow.step")

    def test_wetted_area_is_compared_with_the_outer_shells(self):
        p = self.hollow()
        self.assertTrue(_close(self.parts(p)[0]["stated"]["area"], 6000.0))
        wet = self.save(self.flat(self.set_area(p, "w0.step", 5400.0)).replace("'surface area measure'", "'wetted area measure'"), "w1.step")
        part = self.parts(wet)[0]
        self.assertEqual(part["verdict"], "agree")
        self.assertTrue(part["stated_area_is_wetted"])
        self.assertTrue(_close(part["measured"]["wetted_area"], 5400.0))
        self.assertTrue(_close(part["measured"]["area"], 6000.0))

    def test_wrong_wetted_area_disagrees(self):
        p = self.hollow()
        # 6000 is the total area: under the name 'wetted area measure' it is wrong
        wet = self.save(self.flat(p).replace("'surface area measure'", "'wetted area measure'"), "w2.step")
        self.assertEqual(self.parts(wet)[0]["verdict"], "disagree")
        # and 5400 under the name 'surface area measure' (total area) is wrong too
        self.assertEqual(self.parts(self.set_area(p, "w3.step", 5400.0))[0]["verdict"], "disagree")

    def test_marker_in_the_description_field_is_read(self):
        p = self.exported_box()
        t = re.sub(r"PROPERTY_DEFINITION\('geometric validation property','([^']*)'", r"PROPERTY_DEFINITION('\1','geometric validation property'", self.flat(p))
        self.assertNotIn("PROPERTY_DEFINITION('geometric validation property'", t)
        q = self.save(t, "swapped.step")
        part = self.parts(q)[0]
        self.assertEqual(part["verdict"], "agree")
        self.assertTrue(_close(part["stated"]["volume"], 6000.0))

    def hide_part_level(self, p, name):
        """Keep only the figures at geometry level (a SHAPE_ASPECT), as some exporters write them."""
        t = self.flat(p)
        t2 = t.replace("PROPERTY_DEFINITION('geometric validation property','volume, area and centroid of the part'",
                       "PROPERTY_DEFINITION('hidden','volume, area and centroid of the part'")
        self.assertNotEqual(t, t2)
        return self.save(t2, name)

    def test_geometry_level_figures_are_read_when_part_level_has_none(self):
        q = self.hide_part_level(self.exported_box(), "geo.step")
        part = self.parts(q)[0]
        self.assertEqual(part["verdict"], "agree")
        self.assertTrue(_close(part["stated"]["volume"], 6000.0))

    def test_geometry_level_disagreement_is_found(self):
        q = self.hide_part_level(self.exported_box(), "geo2.step")
        q = self.mutate(q, self.path("geo3.step"), volume=1.01)
        self.assertEqual(self.parts(q)[0]["verdict"], "disagree")

    def test_aspect_holding_only_curve_figures_is_not_a_solid_class_source(self):
        # nist_stc_09 shape: a second shape aspect states only a centre point (section 4.8 curves)
        q = self.hide_part_level(self.exported_box(), "geo4.step")
        t = self.flat(q)
        pds = re.search(r"#(\d+)\s*=\s*PRODUCT_DEFINITION_SHAPE\(", t).group(1)
        ctx = re.search(r"#(\d+)\s*=\s*\(\s*GEOMETRIC_REPRESENTATION_CONTEXT", t).group(1)
        extra = ("#9001 = SHAPE_ASPECT('curve','',#%s,.F.);"
                 "#9002 = PROPERTY_DEFINITION('length of a curve','geometric validation property',#9001);"
                 "#9003 = CARTESIAN_POINT('centre point',(1.,2.,3.));"
                 "#9004 = REPRESENTATION('',(#9003),#%s);"
                 "#9005 = PROPERTY_DEFINITION_REPRESENTATION(#9002,#9004);" % (pds, ctx))
        t = t.replace("ENDSEC;END-ISO", extra + "ENDSEC;END-ISO")
        part = self.parts(self.save(t, "geo5.step"))[0]
        self.assertEqual(part["verdict"], "agree")
        self.assertTrue(_close(part["stated"]["volume"], 6000.0))

    def two_solids_with_aspects(self, name, aspects, second=None):
        """Two boxes (1000 and 125 mm^3) in one part (`second` replaces the small box), the part-level
        figures hidden, and one shape aspect per entry of `aspects` (a volume each, section 4.4): the
        file as an exporter that writes figures per solid at geometry level only would give it."""
        b1 = Part.makeBox(10, 10, 10)
        b2 = second if second is not None else Part.makeBox(5, 5, 5)
        b2.translate(App.Vector(50, 0, 0))
        p = self.export([self.shape_object(Part.makeCompound([b1, b2]), "Two")], name + "_raw.step")
        t = self.flat(p).replace("PROPERTY_DEFINITION('geometric validation property'", "PROPERTY_DEFINITION('hidden'")
        pds = re.search(r"#(\d+)\s*=\s*PRODUCT_DEFINITION_SHAPE\(", t).group(1)
        ctx = re.search(r"#(\d+)\s*=\s*\(\s*GEOMETRIC_REPRESENTATION_CONTEXT", t).group(1)
        unit = re.search(r"'volume measure',VOLUME_MEASURE\([^)]*\),#(\d+)\)", t).group(1)
        extra = ""
        for i, vol in enumerate(aspects):
            n = 9000 + 10 * i
            extra += ("#%d=SHAPE_ASPECT('solid %d','',#%s,.F.);"
                      "#%d=PROPERTY_DEFINITION('geometric validation property','',#%d);"
                      "#%d=MEASURE_REPRESENTATION_ITEM('volume measure',VOLUME_MEASURE(%r),#%s);"
                      "#%d=REPRESENTATION('',(#%d),#%s);"
                      "#%d=PROPERTY_DEFINITION_REPRESENTATION(#%d,#%d);"
                      % (n, i, pds, n + 1, n, n + 2, vol, unit, n + 3, n + 2, ctx, n + 4, n + 1, n + 3))
        return self.save(t.replace("ENDSEC;END-ISO", extra + "ENDSEC;END-ISO"), name + ".step")

    def test_one_aspect_on_a_two_solid_part_is_not_compared_with_the_whole_part(self):
        # the aspect describes the 1000 mm^3 solid; the part measures 1125. Comparing the two is a false disagree.
        r = Import.validateStep(self.two_solids_with_aspects("asp1", [1000.0]))
        part = r["parts"][0]
        self.assertEqual(part["verdict"], "unusable_properties", part)
        self.assertEqual(r["verdict"], "unusable_properties")

    def test_two_aspects_on_a_two_solid_part_are_unusable_not_no_properties(self):
        part = Import.validateStep(self.two_solids_with_aspects("asp2", [1000.0, 125.0]))["parts"][0]
        self.assertEqual(part["verdict"], "unusable_properties", part)

    def test_one_aspect_on_a_solid_with_an_independent_surface_is_unusable(self):
        q = self.two_solids_with_aspects("asp5", [1000.0], second=Part.makePlane(7, 7))
        part = Import.validateStep(q)["parts"][0]
        self.assertEqual(part["verdict"], "unusable_properties", part)

    def test_one_aspect_on_a_one_solid_part_is_still_compared(self):
        q = self.hide_part_level(self.exported_box(), "asp3.step")
        self.assertEqual(self.parts(q)[0]["verdict"], "agree")
        q = self.mutate(q, self.path("asp4.step"), volume=1.01)
        self.assertEqual(self.parts(q)[0]["verdict"], "disagree")

    def zero_figures(self, p, name):
        t = self.flat(p)
        t = re.sub(r"VOLUME_MEASURE\([^)]*\)", "VOLUME_MEASURE(0.0)", t)
        t = re.sub(r"AREA_MEASURE\([^)]*\)", "AREA_MEASURE(0.0)", t)
        t = re.sub(r"CARTESIAN_POINT\('centre point',\([^)]*\)\)", "CARTESIAN_POINT('centre point',(0.0,0.0,0.0))", t)
        return self.save(t, name)

    def test_zero_figures_with_a_zero_centroid_are_unusable_not_disagree(self):
        # nist_stc_06 shape: volume 0, area 0, centre (0,0,0). The centroid is part of the placeholder.
        for hidden in (False, True):
            p = self.exported_box()
            if hidden:
                p = self.hide_part_level(p, "zg.step")
            part = self.parts(self.zero_figures(p, "zero%d.step" % hidden))[0]
            self.assertEqual(part["verdict"], "unusable_properties", hidden)

    def test_zero_measures_do_not_hide_a_usable_figure(self):
        p = self.zero_figures(self.exported_box(), "z9.step")
        t = self.flat(p).replace("AREA_MEASURE(0.0)", "AREA_MEASURE(2400.0)")
        self.assertEqual(self.parts(self.save(t, "z10.step"))[0]["verdict"], "disagree")

    def mixed(self):
        face = Part.makePlane(7, 7, App.Vector(100, 0, 0))
        shape = Part.makeCompound([Part.makeBox(10, 20, 30), face])
        return self.export([self.shape_object(shape, "Mixed")], "mixed.step")

    def test_lost_independent_surface_is_found_in_a_file_we_wrote(self):
        q = self.drop_loose_faces(self.mixed(), "lost.step")
        part = self.parts(q)[0]
        self.assertEqual(part["verdict"], "disagree")
        self.assertTrue(part["disagrees"]["independent_area"])
        self.assertTrue(part["disagrees"]["independent_centroid"])
        self.assertFalse(part["disagrees"]["area"])

    def test_independent_centroid_moved_disagrees(self):
        p = self.export([self.shape_object(Part.makePlane(10, 20), "Sheet")], "sheet.step")
        t = re.sub(r"('surface centre point',\()([^,]*),", lambda m: m.group(1) + repr(float(m.group(2)) + 0.5) + ",", self.flat(p))
        part = self.parts(self.save(t, "isc.step"))[0]
        self.assertEqual(part["verdict"], "disagree")
        self.assertTrue(part["disagrees"]["independent_centroid"])

    def without_independent_figures(self, p, name):
        t = self.flat(p).replace("'independent surface area measure'", "'xx'").replace("'surface centre point'", "'xy'")
        return self.save(t, name)

    def test_either_area_convention_is_accepted_only_without_independent_figures(self):
        # practice 4.5: some systems count the independent surfaces in the solid's area, some do not
        p = self.mixed()
        bare = self.without_independent_figures(p, "bare.step")
        all_faces = self.set_area(bare, "all.step", 2249.0)
        self.assertEqual(self.parts(all_faces)[0]["verdict"], "agree")
        self.assertEqual(self.parts(bare)[0]["verdict"], "agree")
        # a wrong figure is still wrong under either convention
        self.assertEqual(self.parts(self.set_area(bare, "bad.step", 2300.0))[0]["verdict"], "disagree")
        # when the independent class is stated, the area must be the solids' own
        strict = self.set_area(p, "strict.step", 2249.0)
        self.assertEqual(self.parts(strict)[0]["verdict"], "disagree")
        self.assertTrue(self.parts(strict)[0]["disagrees"]["area"])

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


class TestOlderDerivedUnits(StepValidationCase):
    """AREA_UNIT((#e)) and VOLUME_UNIT((#e)) over DERIVED_UNIT_ELEMENT: OCCT drops the elements, so the
    checker reads them from the file text and honours the declared unit (CAx-IF practice 4.2). The
    context here is inch and the declared unit is millimetre, which the context fallback got wrong."""

    def older_form(self, name, volume=1.0, area=1.0, dangle_volume=False):
        p = self.export([self.shape_object(Part.makeBox(10, 20, 30), "Box")])
        t = self.flat(p)
        inch = re.sub(r"(#\d+\s*=\s*)\(\s*LENGTH_UNIT\(\)\s*NAMED_UNIT\(\*\)\s*SI_UNIT\(\.MILLI\.,\.METRE\.\)\s*\)",
                      r"\1(CONVERSION_BASED_UNIT('INCH',#9001)LENGTH_UNIT()NAMED_UNIT(#9003))", t, count=1)
        self.assertNotEqual(inch, t)
        extra = ("#9001=LENGTH_MEASURE_WITH_UNIT(LENGTH_MEASURE(25.4),#9002);"
                 "#9002=(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT(.MILLI.,.METRE.));"
                 "#9003=DIMENSIONAL_EXPONENTS(1.,0.,0.,0.,0.,0.,0.);")
        inch = inch.replace("ENDSEC;END-ISO", extra + "ENDSEC;END-ISO")
        old = re.sub(r"DERIVED_UNIT\(\(", "AREA_UNIT((", inch, count=1)
        old = re.sub(r"DERIVED_UNIT\(\(", "VOLUME_UNIT((", old, count=1)
        self.assertIn("AREA_UNIT((", old)
        self.assertIn("VOLUME_UNIT((", old)
        if dangle_volume:
            old = re.sub(r"VOLUME_UNIT\(\(#\d+\)\)", "VOLUME_UNIT((#99999))", old)
        q = self.save(old, name + "_raw.step")
        return self.mutate(q, self.path(name + ".step"), volume=volume, area=area)

    def test_declared_mm_unit_in_an_inch_context_agrees_when_stated_in_mm(self):
        q = self.older_form("older_ok", volume=25.4 ** 3, area=25.4 ** 2)
        part = self.parts(q)[0]
        self.assertEqual(part["verdict"], "agree", part)
        self.assertTrue(_close(part["stated"]["volume"], 6000.0 * 25.4 ** 3))
        self.assertTrue(_close(part["stated"]["area"], 2200.0 * 25.4 ** 2))

    def test_figure_in_cubic_inches_under_an_mm_element_disagrees(self):
        q = self.older_form("older_bad", volume=1.0, area=1.0)
        part = self.parts(q)[0]
        self.assertEqual(part["verdict"], "disagree", part)
        self.assertTrue(_close(part["stated"]["volume"], 6000.0))
        self.assertTrue(part["disagrees"]["volume"])

    def test_dangling_element_is_unusable_not_a_guess_from_the_context(self):
        q = self.older_form("older_dangle", volume=25.4 ** 3, area=25.4 ** 2, dangle_volume=True)
        part = self.parts(q)[0]
        self.assertEqual(part["verdict"], "unusable_properties", part)

    def test_garbled_element_record_never_throws(self):
        q = self.older_form("older_trunc", volume=25.4 ** 3, area=25.4 ** 2)
        t = self.text(q)
        self.write_text(q, re.sub(r"DERIVED_UNIT_ELEMENT\([^;]*;", "DERIVED_UNIT_ELEMENT(;", t))
        self.assertIn(self.parts(q)[0]["verdict"], ("unusable_properties", "disagree"))

    def ids_for_decoy(self, q):
        t = self.text(q)
        elem = re.search(r"VOLUME_UNIT\(\(#(\d+)\)\)", t).group(1)
        inch = re.search(r"#(\d+)\s*=\s*\(CONVERSION_BASED_UNIT\('INCH'", t).group(1)
        return elem, inch

    def test_a_record_in_a_comment_does_not_decide_the_unit(self):
        q = self.older_form("tok_c", volume=25.4 ** 3, area=25.4 ** 2)
        self.assertEqual(self.parts(q)[0]["verdict"], "agree")
        elem, inch = self.ids_for_decoy(q)
        t = self.text(q).replace("DATA;", "DATA;\n/* #%s=DERIVED_UNIT_ELEMENT(#%s,3.0); */" % (elem, inch), 1)
        self.write_text(q, t)
        self.assertEqual(self.parts(q)[0]["verdict"], "agree")

    def test_a_record_in_the_header_or_in_a_string_does_not_decide_the_unit(self):
        q = self.older_form("tok_s", volume=25.4 ** 3, area=25.4 ** 2)
        elem, inch = self.ids_for_decoy(q)
        decoy = "#%s=DERIVED_UNIT_ELEMENT(#%s,3.0);" % (elem, inch)
        t = self.text(q)
        t = re.sub(r"FILE_NAME\('[^']*'", "FILE_NAME('x %s y'" % decoy, t, count=1)
        t = t.replace("DATA;", "DATA;\n#9100=DESCRIPTION('a;%s b');" % decoy.replace("'", "''"), 1)
        self.write_text(q, t)
        self.assertEqual(self.parts(q)[0]["verdict"], "agree")

    def test_a_semicolon_in_a_string_or_comment_does_not_truncate_a_record(self):
        q = self.older_form("tok_t", volume=25.4 ** 3, area=25.4 ** 2)
        t = self.text(q)
        t = re.sub(r"VOLUME_UNIT\(\((#\d+)\)\)", r"VOLUME_UNIT((';',\1))", t, count=1)
        t = re.sub(r"(DERIVED_UNIT_ELEMENT\()(#\d+,)", r"\1/* ; */\2", t)
        self.write_text(q, t)
        self.assertEqual(self.parts(q)[0]["verdict"], "agree")

    def test_a_file_of_many_records_is_read_once(self):
        q = self.older_form("tok_n", volume=25.4 ** 3, area=25.4 ** 2)
        filler = "".join("#%d=CARTESIAN_POINT('p',(0.,0.,%d.));\n" % (20000 + i, i) for i in range(40000))
        self.write_text(q, self.text(q).replace("DATA;", "DATA;\n" + filler, 1))
        start = time.perf_counter()
        self.assertEqual(self.parts(q)[0]["verdict"], "agree")
        self.assertLess(time.perf_counter() - start, 20.0)


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


class TestSphereLuneRoundTrip(StepValidationCase):
    """G13 slice 2a: our own export of a part of a sphere comes back the right side of the sphere.

    A sweep corner is a surface of revolution of a circle about a line through its centre in its plane. STEP
    carries it as SURFACE_OF_REVOLUTION (ISO 10303-42), the file keeps the two meridians but not the pole edges,
    and OCCT's reader builds the complementary lune. The write converts such faces, and only those, to a sphere.
    """

    R = 5.0

    def lune(self, r=5.0, axis=App.Vector(0, 1, 0), width=90.0, half="even", reverse=False):
        c = Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), r)
        ax = App.Vector(axis)
        ax.normalize()
        a = math.atan2(ax.dot(c.YAxis), ax.dot(c.XAxis))
        t0 = a + (0.0 if half == "even" else math.pi)
        f = Part.SurfaceOfRevolution(c, App.Vector(0, 0, 0), ax).toShape(0.0, math.radians(width), t0, t0 + math.pi)
        if reverse:
            f = f.copy()
            f.reverse()
        return f

    def sweep(self, angle=90.0, reverse=False, r=5.0, centre_offset=0.0):
        b = App.Vector(0, 0, 40)
        c2 = b + App.Vector(40 * math.sin(math.radians(angle)), 0, 40 * math.cos(math.radians(angle)))
        pts = [App.Vector(0, 0, 0), b, c2]
        if reverse:
            pts.reverse()
        t = (pts[1] - pts[0]).normalize()
        prof = Part.Wire([Part.makeCircle(r, pts[0] + App.Vector(centre_offset, 0, 0), t)])
        return Part.Wire(Part.makePolygon(pts)).makePipeShell([prof], True, False, 2)

    def back_via_export(self, shape, name="s"):
        obj = self.shape_object(shape, name)
        p = self.export([obj], name + ".step")
        return p, Part.read(p)

    def back_via_export_step(self, shape, name="s"):
        p = self.path(name + "_es.step")
        shape.exportStep(p)
        return p, Part.read(p)

    def assertSame(self, shape, back, rel=1e-6):
        self.assertTrue(back.isValid())
        self.assertTrue(_close(shape.Area, back.Area, rel), (shape.Area, back.Area))
        if shape.Solids:
            self.assertTrue(_close(shape.Volume, back.Volume, rel), (shape.Volume, back.Volume))

    def test_round_corner_sweep_keeps_its_volume_through_import_export(self):
        sh = self.sweep(90.0)
        self.assertEqual(sum(type(f.Surface).__name__ == "SurfaceOfRevolution" for f in sh.Faces), 1)
        p, back = self.back_via_export(sh)
        self.assertSame(sh, back)
        self.assertEqual(Import.validateStep(p)["verdict"], "agree")

    def test_round_corner_sweep_keeps_its_volume_through_export_step(self):
        sh = self.sweep(90.0)
        _, back = self.back_via_export_step(sh)
        self.assertSame(sh, back)

    def test_sweep_in_both_path_directions_and_many_angles(self):
        for angle in (10.0, 45.0, 90.0, 135.0, 150.0):
            for reverse in (False, True):
                with self.subTest(angle=angle, reverse=reverse):
                    sh = self.sweep(angle, reverse)
                    self.assertSame(sh, self.back_via_export_step(sh, "w")[1])

    def test_lunes_of_every_width_half_orientation_and_axis(self):
        for axis in (App.Vector(0, 1, 0), App.Vector(1, 0, 0), App.Vector(1, 1, 0)):
            for width in (5.0, 89.0, 90.0, 91.0, 179.0, 180.0, 181.0, 270.0, 355.0):
                for half in ("even", "odd"):
                    for reverse in (False, True):
                        with self.subTest(axis=tuple(axis), width=width, half=half, reverse=reverse):
                            f = self.lune(axis=axis, width=width, half=half, reverse=reverse)
                            back = self.back_via_export_step(f, "l")[1]
                            self.assertSame(f, back)
                            # the closed form, not the source shape: area of a lune is 2 r^2 w
                            self.assertTrue(_close(back.Area, 2 * 25.0 * math.radians(width), 1e-6))

    def test_written_file_names_a_sphere_for_the_converted_face(self):
        p, _ = self.back_via_export(self.sweep(90.0))
        t = self.flat(p)
        self.assertIn("SPHERICAL_SURFACE", t)
        self.assertNotIn("SURFACE_OF_REVOLUTION", t)

    def test_export_leaves_the_document_shape_alone(self):
        obj = self.shape_object(self.sweep(90.0), "Sweep")
        before = obj.Shape.exportBrepToString()
        self.export([obj])
        obj.Shape.exportStep(self.path("direct.step"))
        flags = re.compile(r"^\s*[01]{7}\s*$", re.M)  # TShape bookkeeping that stock export clears as well
        self.assertEqual(flags.sub("F", before), flags.sub("F", obj.Shape.exportBrepToString()))
        self.assertEqual(sum(type(f.Surface).__name__ == "SurfaceOfRevolution" for f in obj.Shape.Faces), 1)

    def test_a_shape_without_such_a_face_is_written_as_before(self):
        obj = self.shape_object(Part.makeCylinder(5, 20), "Cyl")
        on = self.export([obj], "on.step")
        PREF.SetBool("WriteSphereLunes", False)
        try:
            off = self.export([obj], "off.step")
        finally:
            PREF.SetBool("WriteSphereLunes", True)
        strip = lambda p: re.sub(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", "T", self.text(p))
        self.assertEqual(strip(on), strip(off))

    def test_switching_the_conversion_off_writes_the_revolution(self):
        PREF.SetBool("WriteSphereLunes", False)
        try:
            p, _ = self.back_via_export(self.sweep(90.0))
        finally:
            PREF.SetBool("WriteSphereLunes", True)
        self.assertIn("SURFACE_OF_REVOLUTION", self.flat(p))

    def test_only_a_circle_about_a_line_through_its_centre_in_its_plane_is_converted(self):
        z = App.Vector(0, 0, 1)
        cases = {
            "centre off the axis": (Part.Circle(App.Vector(2, 0, 0), z, 5.0), App.Vector(0, 0, 0), App.Vector(0, 1, 0)),
            "axis out of the circle's plane": (Part.Circle(App.Vector(0, 0, 0), z, 5.0), App.Vector(0, 0, 0), App.Vector(0, 1, 1)),
            "ellipse": (Part.Ellipse(App.Vector(0, 0, 0), 6.0, 5.0), App.Vector(0, 0, 0), App.Vector(0, 1, 0)),
        }
        for name, (curve, loc, axis) in cases.items():
            with self.subTest(name):
                f = Part.SurfaceOfRevolution(curve, loc, axis).toShape(0.0, 1.0, 1.6, 3.0)
                p = self.path("n.step")
                f.exportStep(p)
                self.assertNotIn("SPHERICAL_SURFACE", self.flat(p))
                self.assertIn("SURFACE_OF_REVOLUTION", self.flat(p))

    def test_a_face_across_both_halves_of_the_circle_is_left_alone(self):
        c = Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 5.0)
        f = Part.SurfaceOfRevolution(c, App.Vector(0, 0, 0), App.Vector(0, 1, 0)).toShape(0.0, 1.0, 0.3, 5.0)
        p = self.path("both.step")
        f.exportStep(p)
        self.assertNotIn("SPHERICAL_SURFACE", self.flat(p))

    def test_wedge_volume_is_the_closed_form(self):
        r, w = 5.0, math.radians(120.0)
        c = Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), r)
        lune = Part.SurfaceOfRevolution(c, App.Vector(0, 0, 0), App.Vector(0, 1, 0)).toShape(0.0, w, math.pi / 2, 3 * math.pi / 2)
        faces = [lune]
        for u in (0.0, w):
            arc = Part.ArcOfCircle(c, math.pi / 2, 3 * math.pi / 2).toShape()
            d = Part.Face(Part.Wire([arc, Part.makeLine(arc.Vertexes[-1].Point, arc.Vertexes[0].Point)]))
            d.rotate(App.Vector(0, 0, 0), App.Vector(0, 1, 0), math.degrees(u))
            faces.append(d)
        wedge = Part.Solid(Part.Shell(faces))
        back = self.back_via_export_step(wedge, "wedge")[1]
        self.assertTrue(_close(back.Volume, 2.0 / 3.0 * r ** 3 * w, 1e-6))

    def test_names_and_placed_instances_of_a_converted_part_survive(self):
        a = self.doc.addObject("Part::Feature", "Elbow")
        a.Shape = self.sweep(90.0)
        b = self.doc.addObject("Part::Feature", "Elbow2")
        b.Shape = self.sweep(60.0)
        b.Placement = App.Placement(App.Vector(100, 20, 0), App.Rotation(App.Vector(0, 0, 1), 30))
        self.doc.recompute()
        p = self.export([a, b], "asm.step")
        t = self.flat(p)
        self.assertIn("'Elbow'", t)
        self.assertIn("'Elbow2'", t)
        d = App.newDocument("G13Back")
        try:
            Import.insert(p, d.Name)
            byLabel = {o.Label: o for o in d.Objects if hasattr(o, "Shape") and o.Shape.Solids}
            self.assertIn("Elbow", byLabel)
            self.assertIn("Elbow2", byLabel)
            for name, src in (("Elbow", a), ("Elbow2", b)):
                back = byLabel[name].Shape
                self.assertTrue(back.isValid())
                self.assertTrue(_close(back.Volume, src.Shape.Volume, 1e-6), (name, back.Volume, src.Shape.Volume))
                self.assertTrue(_close(back.Area, src.Shape.Area, 1e-6), (name, back.Area, src.Shape.Area))
            # the placed instance stayed where it was
            self.assertLess(byLabel["Elbow2"].Shape.BoundBox.XMin, 100 + 1e-3)
            self.assertGreater(byLabel["Elbow2"].Shape.BoundBox.YMax, 20)
        finally:
            App.closeDocument(d.Name)


if __name__ == "__main__":
    unittest.main()
