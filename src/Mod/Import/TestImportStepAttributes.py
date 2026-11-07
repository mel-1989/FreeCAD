# SPDX-License-Identifier: LGPL-2.1-or-later

# **************************************************************************
#   Tests for STEP part identity, user defined attributes, layers and     *
#   external references (G13 slice 1c).                                   *
#   Behaviour follows the CAx-IF Recommended Practices for User Defined   *
#   Attributes v1.7 (sections 5, 6.1, 7) and for External (Element)       *
#   Reference v3.1 (section 5).                                           *
# **************************************************************************

import io
import os
import re
import tempfile
import unittest

import FreeCAD as App
import Import
import Part

PREF = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Part/STEP")


def _num(text):
    m = re.match(r"^(\S+)(.*)$", text)
    return float(m.group(1)), m.group(2)


class AttrCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="g13attr")
        self.docs = []
        self.saved = {k: PREF.GetBool(k, True) for k in ("ReadStepAttributes", "WriteStepAttributes")}
        self.savedScheme = PREF.GetString("Scheme") if "Scheme" in PREF.GetStrings() else None
        PREF.RemString("Scheme")

    def tearDown(self):
        for k, v in self.saved.items():
            PREF.SetBool(k, v)
        if self.savedScheme is None:
            PREF.RemString("Scheme")
        else:
            PREF.SetString("Scheme", self.savedScheme)
        for d in self.docs:
            try:
                App.closeDocument(d.Name)
            except Exception:
                pass
        for f in os.listdir(self.dir):
            os.remove(os.path.join(self.dir, f))
        os.rmdir(self.dir)

    # ---- helpers -----------------------------------------------------
    def path(self, name):
        return os.path.join(self.dir, name)

    def newdoc(self):
        d = App.newDocument("G13Attr")
        self.docs.append(d)
        return d

    def feature(self, doc, name, shape):
        o = doc.addObject("Part::Feature", name)
        o.Shape = shape
        return o

    def read(self, p):
        return open(p, "r", encoding="utf-8", errors="replace").read()

    def write(self, p, t):
        open(p, "w", encoding="utf-8").write(t)

    def box_file(self, name="Box", prop=None):
        d = self.newdoc()
        o = self.feature(d, name, Part.makeBox(10, 20, 30))
        if prop:
            self.set_attrs(o, prop)
        d.recompute()
        p = self.path(name + ".step")
        Import.export([o], p)
        return p

    def set_attrs(self, obj, mapping):
        if not hasattr(obj, "StepAttributes"):
            obj.addProperty("App::PropertyMap", "StepAttributes", "STEP")
        obj.StepAttributes = mapping

    def set_layers(self, obj, layers):
        if not hasattr(obj, "StepLayers"):
            obj.addProperty("App::PropertyStringList", "StepLayers", "STEP")
        obj.StepLayers = layers

    def product_definition(self, text, product):
        m = re.search(r"#(\d+) = PRODUCT\('%s','%s'" % (re.escape(product), re.escape(product)), text)
        pr = m.group(1)
        f = re.search(r"#(\d+) = PRODUCT_DEFINITION_FORMATION\('[^']*','[^']*',#%s\)" % pr, text).group(1)
        return re.search(r"#(\d+) = PRODUCT_DEFINITION\('design','',#%s," % f, text).group(1)

    def inject(self, p, product, items, first=900000):
        """Add attributes (key, kind, value) to a product of a file we wrote, following sections 5.1, 6.1 and 7."""
        t = self.read(p)
        pd = self.product_definition(t, product)
        ctx = re.search(r"#(\d+) = \( GEOMETRIC_REPRESENTATION_CONTEXT", t).group(1)
        n = [first]
        out = []

        def add(s):
            n[0] += 1
            out.append("#%d=%s;" % (n[0], s))
            return n[0]

        for key, kind, value in items:
            gp = add("GENERAL_PROPERTY('','%s',$)" % key)
            pdef = n[0] + 20
            if kind == "text":
                it = add("DESCRIPTIVE_REPRESENTATION_ITEM('','%s')" % value)
            elif kind == "int":
                it = add("INTEGER_REPRESENTATION_ITEM('',%s.)" % value)
            elif kind == "real":
                it = add("REAL_REPRESENTATION_ITEM('',%s)" % value)
            elif kind == "bool":
                it = add("BOOLEAN_REPRESENTATION_ITEM('',.%s.)" % value)
            elif kind == "count":
                it = add("VALUE_REPRESENTATION_ITEM('',COUNT_MEASURE(%s))" % value)
            elif kind == "mm":
                u = add("( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) )")
                it = add("MEASURE_REPRESENTATION_ITEM('',POSITIVE_LENGTH_MEASURE(%s),#%d)" % (value, u))
            elif kind == "inch":
                u0 = add("( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) )")
                mw = add("LENGTH_MEASURE_WITH_UNIT(LENGTH_MEASURE(25.4),#%d)" % u0)
                ex = add("DIMENSIONAL_EXPONENTS(1.,0.,0.,0.,0.,0.,0.)")
                u = add("( CONVERSION_BASED_UNIT('INCH',#%d) LENGTH_UNIT() NAMED_UNIT(#%d) )" % (mw, ex))
                it = add("MEASURE_REPRESENTATION_ITEM('',POSITIVE_LENGTH_MEASURE(%s),#%d)" % (value, u))
            elif kind == "kg":
                u = add("( MASS_UNIT() NAMED_UNIT(*) SI_UNIT(.KILO.,.GRAM.) )")
                it = add("MEASURE_REPRESENTATION_ITEM('',MASS_MEASURE(%s),#%d)" % (value, u))
            else:
                raise ValueError(kind)
            rep = add("REPRESENTATION('',(#%d),#%s)" % (it, ctx))
            pdef = add("PROPERTY_DEFINITION('%s','user defined attribute',#%s)" % (key, pd))
            add("PROPERTY_DEFINITION_REPRESENTATION(#%d,#%d)" % (pdef, rep))
            add("GENERAL_PROPERTY_ASSOCIATION('',$,#%d,#%d)" % (gp, pdef))
        i = t.rindex("ENDSEC;")
        self.write(p, t[:i] + "\n".join(out) + "\n" + t[i:])

    def add_layer(self, p, name, description=""):
        """A presentation layer on every manifold solid of the file."""
        t = self.read(p)
        solids = re.findall(r"#(\d+) = MANIFOLD_SOLID_BREP\(", t)
        last = max(int(x) for x in re.findall(r"^#(\d+) =", t, flags=re.M))
        line = "#%d=PRESENTATION_LAYER_ASSIGNMENT('%s','%s',(%s));\n" % (
            last + 1, name, description, ",".join("#" + s for s in solids))
        i = t.rindex("ENDSEC;")
        self.write(p, t[:i] + line + t[i:])

    def load(self, p, how="insert"):
        if how == "open":
            before = set(App.listDocuments())
            Import.open(p)
            d = [App.getDocument(n) for n in App.listDocuments() if n not in before][0]
        else:
            d = self.newdoc()
            if how == "link":
                Import.insert(p, d.Name, useLinkGroup=True)
            else:
                Import.insert(p, d.Name)
        if d not in self.docs:
            self.docs.append(d)
        return d

    def held(self, doc):
        """{object label: StepAttributes dict} for the objects that have the property."""
        return {o.Label: dict(o.StepAttributes) for o in doc.Objects if hasattr(o, "StepAttributes")}

    def layers_held(self, doc):
        return [list(o.StepLayers) for o in doc.Objects if hasattr(o, "StepLayers")]

    def stderr_of(self, fn):
        import tempfile as tf
        fd = os.dup(2)
        with tf.TemporaryFile(mode="w+b") as f:
            os.dup2(f.fileno(), 2)
            try:
                fn()
            finally:
                os.dup2(fd, 2)
                os.close(fd)
            f.seek(0)
            return f.read().decode("utf-8", "replace")

    def assertValues(self, got, want):
        self.assertEqual(sorted(got), sorted(want))
        for k in want:
            if re.match(r"^-?\d", want[k]) and re.match(r"^-?\d", got[k]):
                a, ua = _num(got[k])
                b, ub = _num(want[k])
                self.assertEqual(ua, ub, k)
                self.assertAlmostEqual(a, b, places=12, msg=k)
            else:
                self.assertEqual(got[k], want[k], k)


ITEMS = [
    ("shipping information", "text", "This Side Up"),
    ("assembly step", "int", "2"),
    ("ratio", "real", "0.125"),
    ("checked", "bool", "T"),
    ("count214", "count", "5."),
    ("Hole Keep Out", "inch", "10.5"),
    ("clearance", "mm", "2.5"),
    ("weight", "kg", "0.75"),
]
WANT = {
    "shipping information": "This Side Up",
    "assembly step": "2",
    "ratio": "0.125",
    "checked": "TRUE",
    "count214": "5",
    "Hole Keep Out": "10.5 INCH",
    "clearance": "2.5 mm",
    "weight": "0.75 kg",
}


class TestRead(AttrCase):
    def test_every_value_kind_on_the_three_import_paths(self):
        p = self.box_file()
        self.inject(p, "Box", ITEMS)
        for how in ("insert", "open", "link"):
            with self.subTest(how=how):
                doc = self.load(p, how)
                held = self.held(doc)
                self.assertEqual(len(held), 1)
                self.assertValues(list(held.values())[0], WANT)

    def test_a_measure_reads_as_value_and_unit(self):
        p = self.box_file()
        self.inject(p, "Box", [("w", "inch", "1.25"), ("l", "mm", "30")])
        held = list(self.held(self.load(p)).values())[0]
        self.assertEqual(held["w"], "1.25 INCH")
        self.assertEqual(held["l"], "30 mm")

    def test_identity_description_and_revision(self):
        p = self.box_file()
        t = self.read(p)
        t = re.sub(r"PRODUCT\('Box','Box',''", "PRODUCT('MP-03-2','Box','my part'", t)
        t = re.sub(r"(PRODUCT_DEFINITION_FORMATION\()'',''", r"\1'03',''", t)
        self.write(p, t)
        held = list(self.held(self.load(p)).values())[0]
        self.assertEqual(held, {"Part Number": "MP-03-2", "Description": "my part", "Revision": "03"})

    def test_an_id_that_repeats_the_name_is_not_carried(self):
        p = self.box_file()
        self.assertEqual(self.held(self.load(p)), {})

    def test_nothing_to_carry_gives_no_property(self):
        p = self.box_file()
        t = self.read(p)
        t = re.sub(r"PRODUCT\('Box','Box',''", "PRODUCT('','Box',''", t)
        self.write(p, t)
        doc = self.load(p)
        self.assertEqual(self.held(doc), {})
        self.assertEqual(self.layers_held(doc), [])

    def test_two_level_assembly_with_a_repeated_part(self):
        d = self.newdoc()
        box = self.feature(d, "Box", Part.makeBox(10, 20, 30))
        cyl = self.feature(d, "Cyl", Part.makeCylinder(5, 40))
        asm = d.addObject("App::Part", "Asm")
        sub = d.addObject("App::Part", "Sub")
        asm.addObject(sub)
        for name, tgt, pos in (("L1", box, (100, 0, 0)), ("L2", box, (0, 100, 50))):
            lk = d.addObject("App::Link", name)
            lk.LinkedObject = tgt
            lk.Placement = App.Placement(App.Vector(*pos), App.Rotation())
            sub.addObject(lk)
        c1 = d.addObject("App::Link", "C1")
        c1.LinkedObject = cyl
        asm.addObject(c1)
        d.recompute()
        p = self.path("asm.step")
        Import.export([asm], p)
        self.inject(p, "Box", [("grade", "text", "A")])
        self.inject(p, "Cyl", [("grade", "text", "B"), ("n", "int", "3")], first=910000)
        self.inject(p, "Asm", [("grade", "text", "root")], first=920000)
        for how in ("insert", "link"):
            with self.subTest(how=how):
                held = self.held(self.load(p, how))
                grades = sorted(v["grade"] for v in held.values() if "grade" in v)
                self.assertIn("B", grades)
                self.assertIn("root", grades)
                self.assertIn("A", grades)
                for v in held.values():
                    self.assertNotIn("L1", v)
                cylinder = [v for v in held.values() if v.get("grade") == "B"][0]
                self.assertEqual(cylinder["n"], "3")
                self.assertNotIn("Part Number", cylinder)  # FreeCAD's id repeats the name, so it is not carried

    def test_attributes_at_other_levels_are_not_attached(self):
        p = self.box_file()
        t = self.read(p)
        # an attribute on the PRODUCT_DEFINITION_SHAPE, a level the importer does not surface
        pds = re.search(r"#(\d+) = PRODUCT_DEFINITION_SHAPE\('','',#\d+\)", t).group(1)
        ctx = re.search(r"#(\d+) = \( GEOMETRIC_REPRESENTATION_CONTEXT", t).group(1)
        add = ("#900001=GENERAL_PROPERTY('','deep',$);\n#900002=DESCRIPTIVE_REPRESENTATION_ITEM('','x');\n"
               "#900003=REPRESENTATION('',(#900002),#%s);\n#900004=PROPERTY_DEFINITION('deep','',#%s);\n"
               "#900005=PROPERTY_DEFINITION_REPRESENTATION(#900004,#900003);\n"
               "#900006=GENERAL_PROPERTY_ASSOCIATION('',$,#900001,#900004);\n" % (ctx, pds))
        i = t.rindex("ENDSEC;")
        self.write(p, t[:i] + add + t[i:])
        # nothing is left to carry: the id only repeats the name, the attribute sits on the shape definition
        self.assertEqual(self.held(self.load(p)), {})

    def test_duplicate_key_keeps_the_first_and_warns_once(self):
        p = self.box_file()
        self.inject(p, "Box", [("k", "text", "first"), ("k", "text", "second")])
        out = self.stderr_of(lambda: self.load(p))
        held = list(self.held(self.load(p)).values())[0]
        self.assertEqual(held["k"], "first")
        self.assertEqual(out.count("repeats the attribute 'k'"), 1)

    def test_switch_off_reads_nothing(self):
        p = self.box_file()
        self.inject(p, "Box", ITEMS)
        PREF.SetBool("ReadStepAttributes", False)
        doc = self.load(p)
        self.assertEqual(self.held(doc), {})


class TestLayers(AttrCase):
    def test_layer_on_a_whole_solid(self):
        p = self.box_file()
        self.add_layer(p, "red", "Layer red")
        for how in ("insert", "link"):
            with self.subTest(how=how):
                doc = self.load(p, how)
                self.assertEqual(self.layers_held(doc), [["red"]])

    def test_empty_name_takes_the_description(self):
        p = self.box_file()
        self.add_layer(p, "", "Layer 7")
        self.assertEqual(self.layers_held(self.load(p)), [["Layer 7"]])

    def test_layers_on_other_targets_are_not_attached(self):
        p = self.box_file()
        t = self.read(p)
        pt = re.search(r"#(\d+) = CARTESIAN_POINT", t).group(1)
        last = max(int(x) for x in re.findall(r"^#(\d+) =", t, flags=re.M))
        i = t.rindex("ENDSEC;")
        self.write(p, t[:i] + "#%d=PRESENTATION_LAYER_ASSIGNMENT('pts','',(#%s));\n" % (last + 1, pt) + t[i:])
        self.assertEqual(self.layers_held(self.load(p)), [])

    def test_switch_off(self):
        p = self.box_file()
        self.add_layer(p, "red")
        PREF.SetBool("ReadStepAttributes", False)
        self.assertEqual(self.layers_held(self.load(p)), [])


class TestWrite(AttrCase):
    def roundtrip(self, doc, name):
        objs = [o for o in doc.Objects if o.TypeId == "Part::Feature"]
        p = self.path(name)
        Import.export(objs, p)
        return p

    def test_round_trip_twice_gives_the_same_map(self):
        d = self.newdoc()
        o = self.feature(d, "Box", Part.makeBox(1, 2, 3))
        self.set_attrs(o, {"Part Number": "PN-7", "Revision": "B", "note": "café ✓", "w": "10.5 INCH"})
        self.set_layers(o, ["alpha", "beta"])
        want = dict(o.StepAttributes)
        p = self.roundtrip(d, "one.step")
        d2 = self.load(p)
        self.assertEqual(self.held(d2), {list(self.held(d2))[0]: want})
        self.assertEqual(self.layers_held(d2), [["alpha", "beta"]])
        p2 = self.roundtrip(d2, "two.step")
        d3 = self.load(p2)
        self.assertEqual(list(self.held(d3).values()), [want])
        self.assertEqual(self.layers_held(d3), [["alpha", "beta"]])

    def test_import_export_import_keeps_a_file_with_every_kind(self):
        p = self.box_file()
        self.inject(p, "Box", ITEMS)
        d = self.load(p)
        first = list(self.held(d).values())[0]
        p1 = self.roundtrip(d, "r1.step")
        d1 = self.load(p1)
        second = list(self.held(d1).values())[0]
        self.assertEqual(second, first)
        p2 = self.roundtrip(d1, "r2.step")
        self.assertEqual(list(self.held(self.load(p2)).values())[0], first)

    def test_attributes_are_written_at_the_part_in_the_practices_structure(self):
        d = self.newdoc()
        o = self.feature(d, "Box", Part.makeBox(1, 2, 3))
        self.set_attrs(o, {"a": "1", "b": "two"})
        t = self.read(self.roundtrip(d, "s.step"))
        pd = re.search(r"#(\d+) = PRODUCT_DEFINITION\('design'", t).group(1)
        for key in ("a", "b"):
            self.assertEqual(len(re.findall(r"PROPERTY_DEFINITION\('%s','user defined attribute',#%s\)" % (key, pd), t)), 1)
            self.assertEqual(len(re.findall(r"GENERAL_PROPERTY\('','%s'," % key, t)), 1)
        self.assertEqual(len(re.findall(r"GENERAL_PROPERTY_ASSOCIATION", t)), 2)

    def test_repeated_part_writes_its_attributes_once(self):
        d = self.newdoc()
        box = self.feature(d, "Box", Part.makeBox(10, 20, 30))
        self.set_attrs(box, {"grade": "A"})
        asm = d.addObject("App::Part", "Asm")
        for name, pos in (("L1", (100, 0, 0)), ("L2", (0, 100, 50))):
            lk = d.addObject("App::Link", name)
            lk.LinkedObject = box
            lk.Placement = App.Placement(App.Vector(*pos), App.Rotation())
            asm.addObject(lk)
        d.recompute()
        p = self.path("rep.step")
        Import.export([asm], p)
        t = self.read(p)
        self.assertEqual(len(re.findall(r"PROPERTY_DEFINITION\('grade'", t)), 1)
        held = self.held(self.load(p))
        self.assertEqual([v for v in held.values() if v.get("grade") == "A"][0]["grade"], "A")

    def test_attributes_of_an_assembly_stay_on_the_assembly(self):
        d = self.newdoc()
        box = self.feature(d, "Box", Part.makeBox(10, 20, 30))
        self.set_attrs(box, {"grade": "A"})
        asm = d.addObject("App::Part", "Asm")
        lk = d.addObject("App::Link", "L1")
        lk.LinkedObject = box
        lk.Placement = App.Placement(App.Vector(100, 0, 0), App.Rotation())
        asm.addObject(lk)
        self.set_attrs(asm, {"role": "top"})
        d.recompute()
        p = self.path("asmattr.step")
        Import.export([asm], p)
        held = list(self.held(self.load(p)).values())
        self.assertIn({"role": "top"}, held)
        self.assertIn({"grade": "A"}, held)
        self.assertEqual(len(held), 2)

    # ---- product id and name on export: asserted on the raw text of the written file ----------------
    def products(self, p):
        """[(id, name, description)] of every PRODUCT record in a file."""
        return re.findall(r"= PRODUCT\('([^']*)','([^']*)','([^']*)'", self.read(p))

    def test_an_id_equal_to_the_name_survives_import_and_export(self):
        d = self.load(self.box_file("P"))
        self.assertEqual(self.held(d), {})
        self.assertEqual(self.products(self.roundtrip(d, "p.step")), [("P", "P", "")])

    def test_layers_only_still_write_the_label_as_id_and_name(self):
        d = self.newdoc()
        o = self.feature(d, "Lay", Part.makeBox(1, 2, 3))
        self.set_layers(o, ["L1"])
        self.assertEqual(self.products(self.roundtrip(d, "l.step")), [("Lay", "Lay", "")])

    def test_a_description_only_keeps_id_and_name_and_writes_the_description(self):
        d = self.newdoc()
        o = self.feature(d, "Bracket", Part.makeBox(1, 2, 3))
        self.set_attrs(o, {"Description": "A desc"})
        self.assertEqual(self.products(self.roundtrip(d, "d.step")), [("Bracket", "Bracket", "A desc")])

    def test_a_deleted_part_number_falls_back_to_the_label(self):
        d = self.newdoc()
        o = self.feature(d, "Gear", Part.makeBox(1, 2, 3))
        self.set_attrs(o, {"Part Number": "PN-1", "Description": "d"})
        self.set_attrs(o, {"Description": "d"})
        self.assertEqual(self.products(self.roundtrip(d, "g.step")), [("Gear", "Gear", "d")])

    def test_an_empty_part_number_falls_back_to_the_label(self):
        d = self.newdoc()
        o = self.feature(d, "Gear", Part.makeBox(1, 2, 3))
        self.set_attrs(o, {"Part Number": "", "Description": "d"})
        self.assertEqual(self.products(self.roundtrip(d, "g.step")), [("Gear", "Gear", "d")])

    def test_a_part_number_that_differs_from_the_name_is_written_beside_it(self):
        d = self.newdoc()
        o = self.feature(d, "Gear", Part.makeBox(1, 2, 3))
        self.set_attrs(o, {"Part Number": "PN-1"})
        self.assertEqual(self.products(self.roundtrip(d, "g.step")), [("PN-1", "Gear", "")])

    def test_a_part_number_equal_to_the_name_keeps_the_name(self):
        d = self.newdoc()
        o = self.feature(d, "Gear", Part.makeBox(1, 2, 3))
        self.set_attrs(o, {"Part Number": "Gear", "Description": "d"})
        self.assertEqual(self.products(self.roundtrip(d, "g.step")), [("Gear", "Gear", "d")])

    def test_a_rename_keeps_the_part_number_and_the_name_follows_the_label(self):
        d = self.newdoc()
        o = self.feature(d, "Gear", Part.makeBox(1, 2, 3))
        self.set_attrs(o, {"Part Number": "PN-1"})
        o.Label = "Cog"
        self.assertEqual(self.products(self.roundtrip(d, "g.step")), [("PN-1", "Cog", "")])

    def test_a_raw_empty_name_with_an_id_exports_with_a_name(self):
        p = self.box_file("Schenkel")
        t = self.read(p)
        self.assertIn("PRODUCT('Schenkel','Schenkel'", t)
        self.write(p, t.replace("PRODUCT('Schenkel','Schenkel'", "PRODUCT('Schenkel',''"))
        d = self.load(p)
        self.assertEqual(self.held(d), {})
        self.assertEqual(self.products(self.roundtrip(d, "s.step")), [("Schenkel", "Schenkel", "")])

    def test_an_assembly_whose_children_lack_a_part_number_has_no_empty_id(self):
        d = self.newdoc()
        a = self.feature(d, "A", Part.makeBox(1, 2, 3))
        b = self.feature(d, "B", Part.makeBox(2, 2, 2))
        asm = d.addObject("App::Part", "Asm")
        for o, x in ((a, 0), (b, 50)):
            lk = d.addObject("App::Link", "L" + o.Name)
            lk.LinkedObject = o
            lk.Placement = App.Placement(App.Vector(x, 0, 0), App.Rotation())
            asm.addObject(lk)
        self.set_attrs(asm, {"Description": "the top"})
        self.set_attrs(b, {"role": "b"})
        d.recompute()
        p = self.path("asm.step")
        Import.export([asm], p)
        found = self.products(p)
        self.assertEqual(len(found), 3)
        for pid, name, _ in found:
            self.assertTrue(pid and name, found)
            self.assertEqual(pid, name)

    def test_two_copies_with_one_part_number_warn_once(self):
        d = self.newdoc()
        a = self.feature(d, "Plate", Part.makeBox(1, 2, 3))
        b = self.feature(d, "Plate copy", Part.makeBox(1, 2, 3))
        for o in (a, b):
            self.set_attrs(o, {"Part Number": "PN-9"})
        p = self.path("two.step")
        out = self.stderr_of(lambda: Import.export([a, b], p))
        self.assertEqual(out.count("Part Number 'PN-9'"), 1, out)
        self.assertEqual(sorted(x for x in self.products(p) if x[0] == "PN-9"), [("PN-9", "Plate", ""), ("PN-9", "Plate_copy", "")])

    def test_links_of_one_part_are_not_a_duplicate_part_number(self):
        d = self.newdoc()
        box = self.feature(d, "Box", Part.makeBox(10, 20, 30))
        self.set_attrs(box, {"Part Number": "PN-5"})
        asm = d.addObject("App::Part", "Asm")
        for name, pos in (("L1", (100, 0, 0)), ("L2", (0, 100, 50))):
            lk = d.addObject("App::Link", name)
            lk.LinkedObject = box
            lk.Placement = App.Placement(App.Vector(*pos), App.Rotation())
            asm.addObject(lk)
        d.recompute()
        out = self.stderr_of(lambda: Import.export([asm], self.path("l.step")))
        self.assertNotIn("Part Number 'PN-5'", out)

    def test_attributes_of_an_assembly_stay_on_the_assembly_when_a_child_sits_at_the_origin(self):
        # the writer's solid lookup never reaches a child's product through an assembly's shape (probed: no solid of an
        # assembly's compound is found in the written file), so the first-solid route is dead code and the name
        # fallback decides; this test pins the outcome, not that route
        d = self.newdoc()
        box = self.feature(d, "Box", Part.makeBox(10, 20, 30))
        self.set_attrs(box, {"grade": "A"})
        asm = d.addObject("App::Part", "Asm")
        lk = d.addObject("App::Link", "L1")
        lk.LinkedObject = box
        asm.addObject(lk)
        self.set_attrs(asm, {"role": "top"})
        d.recompute()
        p = self.path("asmorigin.step")
        Import.export([asm], p)
        held = list(self.held(self.load(p)).values())
        self.assertIn({"role": "top"}, held)
        self.assertIn({"grade": "A"}, held)
        self.assertEqual(len(held), 2)

    def test_off_switch_writes_the_stock_file(self):
        d = self.newdoc()
        o = self.feature(d, "Box", Part.makeBox(1, 2, 3))
        d.recompute()
        stock = self.path("stock.step")
        Import.export([o], stock)
        self.set_attrs(o, {"a": "1", "Part Number": "PN"})
        self.set_layers(o, ["x"])
        PREF.SetBool("WriteStepAttributes", False)
        off = self.path("off.step")
        Import.export([o], off)
        strip = lambda t: re.sub(r"FILE_NAME\([^;]*;", "", t)
        self.assertEqual(strip(self.read(stock)), strip(self.read(off)))

    def test_objects_without_the_property_write_the_stock_file(self):
        d = self.newdoc()
        o = self.feature(d, "Box", Part.makeBox(1, 2, 3))
        d.recompute()
        a, b = self.path("a.step"), self.path("b.step")
        Import.export([o], a)
        Import.export([o], b)
        strip = lambda t: re.sub(r"FILE_NAME\([^;]*;", "", t)
        self.assertEqual(strip(self.read(a)), strip(self.read(b)))
        self.assertNotIn("GENERAL_PROPERTY", self.read(a))


class TestExternalReferences(AttrCase):
    def make_parts(self):
        d = self.newdoc()
        a = self.feature(d, "A", Part.makeBox(10, 20, 30))
        b = self.feature(d, "B", Part.makeCylinder(5, 40))
        d.recompute()
        Import.export([a], self.path("a.step"))
        Import.export([b], self.path("b.step"))

    def master(self, files=("a.step", "b.step"), sources=("", ""), ids=None):
        ctx = ("#2=APPLICATION_CONTEXT('managed model based 3d engineering');\n"
               "#4=PRODUCT_CONTEXT('',#2,'mechanical');\n#5=PRODUCT_DEFINITION_CONTEXT('part definition',#2,'design');\n"
               "#6=( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) );\n"
               "#7=( NAMED_UNIT(*) PLANE_ANGLE_UNIT() SI_UNIT($,.RADIAN.) );\n"
               "#8=( NAMED_UNIT(*) SI_UNIT($,.STERADIAN.) SOLID_ANGLE_UNIT() );\n"
               "#9=UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-07),#6,'distance_accuracy_value','confusion accuracy');\n"
               "#10=( GEOMETRIC_REPRESENTATION_CONTEXT(3) GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT((#9)) "
               "GLOBAL_UNIT_ASSIGNED_CONTEXT((#6,#7,#8)) REPRESENTATION_CONTEXT('ctx','3D') );\n")
        n = [100]
        out = []

        def add(s):
            n[0] += 1
            out.append("#%d=%s;" % (n[0], s))
            return n[0]

        def axis(x, y, z):
            p = add("CARTESIAN_POINT('',(%r,%r,%r))" % (float(x), float(y), float(z)))
            d1 = add("DIRECTION('',(0.,0.,1.))")
            d2 = add("DIRECTION('',(1.,0.,0.))")
            return add("AXIS2_PLACEMENT_3D('',#%d,#%d,#%d)" % (p, d1, d2))

        def product(name):
            pr = add("PRODUCT('%s','%s','',(#4))" % (name, name))
            pf = add("PRODUCT_DEFINITION_FORMATION('','',#%d)" % pr)
            pd = add("PRODUCT_DEFINITION('design','',#%d,#5)" % pf)
            return pd, add("PRODUCT_DEFINITION_SHAPE('','',#%d)" % pd)

        root = axis(0, 0, 0)
        pax = [axis(100, 0, 0), axis(0, 100, 50)]
        rpd, rpds = product("Master")
        rrep = add("SHAPE_REPRESENTATION('',(#%d,#%d,#%d),#10)" % (root, pax[0], pax[1]))
        add("SHAPE_DEFINITION_REPRESENTATION(#%d,#%d)" % (rpds, rrep))
        for k, (fn, name) in enumerate(zip(files, ("PartA", "PartB"))):
            pd, pds = product(name)
            ax = axis(0, 0, 0)
            rep = add("SHAPE_REPRESENTATION('',(#%d),#10)" % ax)
            add("SHAPE_DEFINITION_REPRESENTATION(#%d,#%d)" % (pds, rep))
            dt = add("DOCUMENT_TYPE('geometry')")
            df = add("DOCUMENT_FILE('%s','',$,#%d,'',$)" % ((ids or (None, None))[k] or fn, dt))
            add("DOCUMENT_REPRESENTATION_TYPE('digital',#%d)" % df)
            add("APPLIED_DOCUMENT_REFERENCE(#%d,'',(#%d))" % (df, pd))
            es = add("EXTERNAL_SOURCE('%s')" % sources[k])
            role = add("IDENTIFICATION_ROLE('external document id and location',$)")
            add("APPLIED_EXTERNAL_IDENTIFICATION_ASSIGNMENT('%s',#%d,#%d,(#%d))" % (fn, role, es, df))
            pdef = add("PROPERTY_DEFINITION('external definition','',#%d)" % df)
            add("PROPERTY_DEFINITION_REPRESENTATION(#%d,#%d)" % (pdef, rep))
            nauo = add("NEXT_ASSEMBLY_USAGE_OCCURRENCE('%d','%s','',#%d,#%d,$)" % (k + 1, name, rpd, pd))
            npds = add("PRODUCT_DEFINITION_SHAPE('Placement','Placement of an item',#%d)" % nauo)
            idt = add("ITEM_DEFINED_TRANSFORMATION('','',#%d,#%d)" % (ax, pax[k]))
            rr = add("( REPRESENTATION_RELATIONSHIP('','',#%d,#%d) REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION(#%d) "
                     "SHAPE_REPRESENTATION_RELATIONSHIP() )" % (rep, rrep, idt))
            add("CONTEXT_DEPENDENT_SHAPE_REPRESENTATION(#%d,#%d)" % (rr, npds))
        head = ("ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('master'),'2;1');\n"
                "FILE_NAME('master.step','2026-01-01T00:00:00',(''),(''),'','','');\n"
                "FILE_SCHEMA(('AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF { 1 0 10303 442 1 1 4 }'));\nENDSEC;\nDATA;\n")
        return head + ctx + "\n".join(out) + "\nENDSEC;\nEND-ISO-10303-21;\n"

    def parts(self, doc):
        return {o.Label: (round(o.Shape.Volume, 6), tuple(round(x, 6) for x in o.Placement.Base))
                for o in doc.Objects if o.TypeId == "Part::Feature"}

    def test_all_parts_arrive_with_their_placements(self):
        self.make_parts()
        p = self.path("master.step")
        self.write(p, self.master())
        out = self.stderr_of(lambda: self.load(p))
        got = self.parts(self.load(p))
        self.assertEqual(sorted(got), ["PartA", "PartB"])
        self.assertAlmostEqual(got["PartA"][0], 6000.0, places=6)
        self.assertEqual(got["PartA"][1], (100.0, 0.0, 0.0))
        self.assertEqual(got["PartB"][1], (0.0, 100.0, 50.0))
        self.assertNotIn("STEP external reference", out)
        # placements and volumes to 1e-9, not to the rounding of parts()
        doc = self.load(p)
        by = {o.Label: o for o in doc.Objects if o.TypeId == "Part::Feature"}
        for label, want in (("PartA", (100.0, 0.0, 0.0)), ("PartB", (0.0, 100.0, 50.0))):
            got = by[label].Placement.Base
            for a, b in zip((got.x, got.y, got.z), want):
                self.assertLess(abs(a - b), 1e-9)
        self.assertLess(abs(by["PartA"].Shape.Volume - 6000.0), 1e-9 * 6000.0)

    def test_a_missing_file_gives_one_warning_and_the_rest_arrives(self):
        self.make_parts()
        os.remove(self.path("b.step"))
        p = self.path("master.step")
        self.write(p, self.master())
        out = self.stderr_of(lambda: self.load(p))
        self.assertEqual(out.count("'b.step'"), 1, out)
        self.assertEqual(out.count("was not found"), 1)
        self.assertEqual(sorted(self.parts(self.load(p))), ["PartA"])

    def test_self_reference_finishes(self):
        self.make_parts()
        p = self.path("self.step")
        self.write(p, self.master(files=("a.step", "self.step")))
        out = self.stderr_of(lambda: self.load(p))
        self.assertIn("refers to itself", out)
        self.assertEqual(sorted(self.parts(self.load(p))), ["PartA"])

    def test_a_cycle_of_two_files_finishes(self):
        self.make_parts()
        self.write(self.path("c1.step"), self.master(files=("c2.step", "b.step")))
        self.write(self.path("c2.step"), self.master(files=("c1.step", "b.step")))
        out = self.stderr_of(lambda: self.load(self.path("c1.step")))
        self.assertIn("cycle", out)
        self.assertEqual(sorted(self.parts(self.load(self.path("c1.step")))), ["PartB"])

    def test_references_outside_the_folder_are_named(self):
        self.make_parts()
        os.mkdir(self.path("sub"))
        sub = self.path("sub")
        for n in ("a.step", "b.step"):
            os.rename(self.path(n), os.path.join(sub, n))
        p = os.path.join(sub, "..", "dots.step")
        self.write(os.path.join(self.dir, "dots.step"), self.master(files=("sub/a.step", "sub/b.step")))
        out = self.stderr_of(lambda: self.load(os.path.join(self.dir, "dots.step")))
        self.assertNotIn("outside", out)  # below the folder is inside
        self.write(os.path.join(sub, "up.step"), self.master(files=("../x.step", "b.step")))
        out = self.stderr_of(lambda: self.load(os.path.join(sub, "up.step")))
        self.assertEqual(out.count("'../x.step'"), 2, out)  # outside, and not found
        self.assertIn("lies outside its folder", out)
        os.remove(os.path.join(sub, "up.step"))
        for n in ("a.step", "b.step"):
            os.remove(os.path.join(sub, n))
        os.remove(os.path.join(self.dir, "dots.step"))
        os.rmdir(sub)


class TestHostile(AttrCase):
    def test_truncated_and_mangled_copies_do_not_kill_the_caller(self):
        p = self.box_file()
        self.inject(p, "Box", ITEMS)
        base = self.read(p)
        bad = []
        mutations = [
            lambda t: t[: len(t) // 2],
            lambda t: t.replace("DESCRIPTIVE_REPRESENTATION_ITEM('','This Side Up')", "DESCRIPTIVE_REPRESENTATION_ITEM('','This Side"),
            lambda t: re.sub(r"PROPERTY_DEFINITION_REPRESENTATION\(#(\d+),#(\d+)\)", r"PROPERTY_DEFINITION_REPRESENTATION(#\2,#\1)", t),
            lambda t: re.sub(r"(GENERAL_PROPERTY_ASSOCIATION\('',\$,)#(\d+),#(\d+)", r"\1#\3,#\2", t),
            lambda t: t.replace("This Side Up", "x" * 1_000_000),
            lambda t: t.replace("GENERAL_PROPERTY('','ratio',$)", "GENERAL_PROPERTY('','shipping information',$)")
                       .replace("PROPERTY_DEFINITION('ratio'", "PROPERTY_DEFINITION('shipping information'"),
        ]
        for i, m in enumerate(mutations):
            q = self.path("m%d.step" % i)
            self.write(q, m(base))
            try:
                self.load(q)
            except Exception:
                pass  # an exception is fine, a crash is not: reaching the next line is the check
        self.assertEqual(bad, [])

    def test_a_long_value_survives(self):
        p = self.box_file()
        self.inject(p, "Box", [("big", "text", "y" * 6000)])
        held = list(self.held(self.load(p)).values())[0]
        self.assertEqual(len(held["big"]), 6000)

    def test_a_one_megabyte_value_is_cut_on_export_with_a_warning_and_the_file_reads_back(self):
        d = self.newdoc()
        o = self.feature(d, "Box", Part.makeBox(1, 2, 3))
        self.set_attrs(o, {"big": "y" * 1_000_000, "small": "ok"})
        p = self.path("big.step")
        out = self.stderr_of(lambda: Import.export([o], p))
        self.assertIn("too long", out)
        held = list(self.held(self.load(p)).values())[0]
        self.assertEqual(held["small"], "ok")
        self.assertEqual(held["big"], "y" * 8000)

    def test_a_one_megabyte_value_in_a_file_is_refused_cleanly(self):
        p = self.box_file()
        self.inject(p, "Box", [("big", "text", "y" * 1_000_000)])
        with self.assertRaises(OSError):
            self.load(p)  # OCCT's reader refuses the record; the caller gets an exception, not a crash


if __name__ == "__main__":
    unittest.main()
