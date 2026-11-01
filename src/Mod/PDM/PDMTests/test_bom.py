# SPDX-License-Identifier: LGPL-2.1-or-later
"""Slice 6a, checks 3 and 4 of G03: the indented BOM and the revision diff.

One vault fixture (``bom_fixture.py``): ``machine`` (three levels: a sub-assembly used twice through links, a
link array of a part from another vaulted document, a local part), ``bracket`` (the linked part) and ``frame``
(a weldment). Every expected BOM and diff below is written by hand from the fixture's construction.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

import FreeCAD as App

from PDM import bom as B
from PDM import diff as D
from PDM import vault as V
from PDMTests import bom_fixture as F

CTL = os.path.expanduser("~/_agents/repos/app development/cad-fork")
WELD = [  # the weldment's cut list (WeldBOM contract): three member lines of four pieces each
    (2, "EN SHS 125x125x12, cut 2500.000 mm, ends square/square", "member", "A.1", 4, "Structural steel", "frame"),
    (2, "EN SHS 90x90x6, cut 3000.000 mm, ends square/square", "member", "A.1", 4, "Structural steel", "frame"),
    (2, "EN SHS 90x90x6, cut 2000.000 mm, ends square/square", "member", "A.1", 4, "Structural steel", "frame"),
]
# (level, name, kind, revision, quantity, material, document)
BOM_A1 = [
    (0, "machine", "assembly", "A.1", 1, "", "machine"),
    (1, "Sub", "assembly", "A.1", 2, "", "machine"),
    (2, "Plate", "part", "A.1", 1, "Aluminium 6061", "machine"),
    (2, "bracket", "part", "A.1", 4, "Steel S235", "bracket"),
    (1, "bracket", "part", "A.1", 1, "Steel S235", "bracket"),
    (1, "frame", "weldment", "A.1", 1, "Structural steel", "frame"),
] + WELD
BOM_HEAD = [
    (0, "machine", "assembly", "A.2", 1, "", "machine"),
    (1, "Cover", "assembly", "A.2", 1, "", "machine"),
    (2, "CoverPlate", "part", "A.2", 1, "Polycarbonate", "machine"),
    (1, "Sub", "assembly", "A.2", 3, "", "machine"),
    (2, "Plate", "part", "A.2", 1, "Aluminium 6061", "machine"),
    (2, "bracket", "part", "A.3", 4, "Steel S235", "bracket"),
    (1, "bracket", "part", "A.3", 1, "Steel S235", "bracket"),
    (1, "frame", "weldment", "A.1", 1, "Structural steel", "frame"),
] + WELD
# (name, kind, revision, quantity, material, document); Sub x2, bracket 4 in each Sub plus 1 direct
FLAT_A1 = [
    ("EN SHS 125x125x12, cut 2500.000 mm, ends square/square", "member", "A.1", 4, "Structural steel", "frame"),
    ("EN SHS 90x90x6, cut 2000.000 mm, ends square/square", "member", "A.1", 4, "Structural steel", "frame"),
    ("EN SHS 90x90x6, cut 3000.000 mm, ends square/square", "member", "A.1", 4, "Structural steel", "frame"),
    ("Plate", "part", "A.1", 2, "Aluminium 6061", "machine"),
    ("bracket", "part", "A.1", 9, "Steel S235", "bracket"),
]
CSV_A1_HEAD = ["level,name,kind,revision,quantity,material,document",
               "0,machine,assembly,A.1,1,,machine",
               "1,Sub,assembly,A.1,2,,machine",
               "2,Plate,part,A.1,1,Aluminium 6061,machine"]


def tup(rows):
    return [(r["level"], r["name"], r["kind"], r["revision"], r["quantity"], r["material"], r["document"]) for r in rows]


def flat_tup(rows):
    return [(r["name"], r["kind"], r["revision"], r["quantity"], r["material"], r["document"]) for r in rows]


@unittest.skipIf(F.frames is None, "the Weldment test frames are not installed")
class FixtureCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("CADFORK_CTL", CTL)
        cls.tmp = os.path.realpath(tempfile.mkdtemp(prefix="pdmbom"))
        F.close_all()
        cls.fx = F.Fixture(cls.tmp)
        cls.fx.build()
        cls.v = cls.fx.v
        cls.early = cls.v.bom("machine")  # taken before any later revision exists
        cls.revs = cls.fx.revisions()

    @classmethod
    def tearDownClass(cls):
        F.close_all()
        shutil.rmtree(cls.tmp, ignore_errors=True)


class TestBOM(FixtureCase):
    def test_indented_bom_matches_hand_written(self):
        self.assertEqual(tup(self.early), BOM_A1)
        self.assertEqual([r["item"] for r in self.early], list(range(1, len(self.early) + 1)))
        self.assertEqual(self.early[0]["extra"], {})
        self.assertEqual(set(self.early[-1]["extra"]), {"family", "size", "length_mm", "mass_kg"})

    def test_older_revision_is_the_bom_as_of_that_revision(self):
        self.assertEqual(self.revs, {"bracket2": "A.2", "machine2": "A.2", "bracket3": "A.3"})
        self.assertEqual(self.v.bom("machine", "A.1"), self.early)  # rows equal exactly, floats included
        self.assertEqual(tup(self.v.bom("machine", "A.1")), BOM_A1)  # bracket still A.1 in it, not A.3
        self.assertEqual(tup(self.v.bom("machine")), BOM_HEAD)
        self.assertEqual(tup(self.v.bom("machine", "A.2")), BOM_HEAD[:5] + [(2, "bracket", "part", "A.2", 4, "Steel S235", "bracket"),
                                                                          (1, "bracket", "part", "A.2", 1, "Steel S235", "bracket")] + BOM_HEAD[7:])

    def test_part_and_weldment_boms(self):
        self.assertEqual(tup(self.v.bom("bracket", "A.1")), [(0, "bracket", "part", "A.1", 1, "Steel S235", "bracket")])
        self.assertEqual(tup(self.v.bom("frame")), [(0, "frame", "weldment", "A.1", 1, "", "frame")] +
                         [(1,) + w[1:] for w in WELD])

    def test_weldment_rows_are_the_weldbom_contract(self):
        import WeldBOM
        doc = self.v.open_revision("frame", "A.1")
        want = WeldBOM.bom_rows(doc)
        got = [r for r in self.early if r["kind"] == "member"]
        self.assertEqual([(g["name"], g["quantity"], g["material"]) for g in got],
                         [(w["description"], w["quantity"], w["material"]) for w in want])
        for g, w in zip(got, want):
            self.assertAlmostEqual(g["extra"]["length_mm"], w["length_mm"], places=6)
            self.assertAlmostEqual(g["extra"]["mass_kg"], w["mass_kg"], places=6)

    def test_flat_bom_and_csv(self):
        self.assertEqual(flat_tup(self.v.bom_flat("machine", "A.1")), FLAT_A1)
        head = {r[0]: r for r in flat_tup(self.v.bom_flat("machine"))}
        self.assertEqual(head["bracket"][3], 13)  # 1 direct + 3 Sub x 4
        self.assertEqual(head["Plate"][3], 3)
        self.assertEqual(head["CoverPlate"][3], 1)
        csv = self.v.bom_csv("machine", "A.1")
        self.assertEqual(csv.splitlines()[:4], CSV_A1_HEAD)
        self.assertTrue(csv.endswith("\n") and "\r" not in csv)
        flat = self.v.bom_csv("machine", "A.1", flat=True)
        self.assertEqual(flat.splitlines()[0], "name,kind,revision,quantity,material,document")
        self.assertIn("bracket,part,A.1,9,Steel S235,bracket", flat.splitlines())

    def test_csv_is_deterministic_across_processes(self):
        want = (self.v.bom_csv("machine", "A.1"), self.v.bom_csv("machine", "A.1", flat=True))
        root = os.path.dirname(os.path.dirname(os.path.abspath(V.__file__)))
        code = ("import sys; sys.path.insert(0, %r); from PDM import vault as V; v = V.Vault.open(%r, 'alice'); "
                "sys.stdout.write('<<' + v.bom_csv('machine', 'A.1') + '||' + v.bom_csv('machine', 'A.1', flat=True) + '>>')"
                % (root, self.v.workdir))
        cmd = os.path.join(App.getHomePath(), "bin", "FreeCADCmd")
        for seed in ("1", "12345"):
            env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONHASHSEED=seed)
            p = subprocess.run([cmd, "-c", code], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, timeout=300)
            out = p.stdout[p.stdout.index("<<") + 2:p.stdout.rindex(">>")]
            self.assertEqual(tuple(out.split("||")), want, f"PYTHONHASHSEED={seed}: {p.stderr[-500:]}")

    def test_another_client_reads_the_same_bom(self):
        other = V.Vault.clone(self.fx.vault_path, os.path.join(self.tmp, "wc-bob"), "bob")
        self.assertEqual(other.bom("machine", "A.1"), self.early)

    def test_refusals_and_no_leaked_documents(self):
        before = set(App.listDocuments())
        with self.assertRaises(V.PDMError) as cm:
            self.v.bom("machine", kind="MBOM")
        self.assertIn("MBOM", str(cm.exception))
        with self.assertRaises(V.DocumentError):
            self.v.bom("machine", "Z.9")
        with self.assertRaises(V.DocumentError):
            self.v.bom("nosuch")
        self.v.bom("machine")
        self.assertEqual(set(App.listDocuments()), before)

    def test_remote_vault_gets_bom_and_diff_by_inheritance(self):
        from PDM import remote
        for m in ("bom", "bom_flat", "bom_csv", "diff"):
            self.assertIs(getattr(remote.RemoteVault, m), getattr(V.Vault, m))


class TestDiff(FixtureCase):
    def d(self, name, a, b):
        res = self.v.diff(name, a, b)
        json.dumps(res)  # plain data, so an ECO can carry it
        return res

    def golden(self):
        return D._golden() is not None

    def test_pad_length_change_touches_exactly_that_property_and_downstream_shapes(self):
        r = self.d("bracket", "A.1", "A.2")
        self.assertEqual([(p["object"], p["property"], p["before"]["value"], p["after"]["value"]) for p in r["properties"]],
                         [("BodyPad", "Length", 10.0, 15.0)])
        self.assertEqual([(p["object"], p["property"]) for p in r["outputs"]], [("BodyPad", "AddSubShape")])
        self.assertEqual(r["objects"]["modified"], ["Body", "BodyPad"])  # the sketch is untouched
        self.assertEqual((r["objects"]["added"], r["objects"]["removed"], r["objects"]["renamed"]), ([], [], []))
        self.assertEqual((r["expressions"], r["structure"]), ([], []))
        self.assertEqual(sorted(s["object"] for s in r["shapes"]), ["Body", "BodyPad"])
        self.assertEqual({s["status"] for s in r["shapes"]}, {"changed"})
        self.assertEqual(r["bom"], {"added": [], "removed": [], "changed": []})
        self.assertFalse(r["empty"])
        if self.golden():
            for s in r["shapes"]:  # 20 x 10 x 10 -> 20 x 10 x 15
                self.assertAlmostEqual(s["before"]["volume"], 2000.0, places=6)
                self.assertAlmostEqual(s["after"]["volume"], 3000.0, places=6)
                self.assertAlmostEqual(s["delta"]["volume"], 1000.0, places=6)
                self.assertAlmostEqual(s["delta"]["area"], 300.0, places=6)
                self.assertEqual(s["after"]["bbox"], [0.0, 0.0, 0.0, 20.0, 10.0, 15.0])
                self.assertAlmostEqual(s["deviation"]["max_cand_to_gold"], 5.0, places=4)

    def test_renamed_object_is_reported_once(self):
        r = self.d("bracket", "A.2", "A.3")
        self.assertEqual(r["objects"]["renamed"], [{"object": "Body", "before": "Body", "after": "Renamed"}])
        self.assertEqual((r["properties"], r["outputs"], r["shapes"], r["expressions"], r["structure"]), ([], [], [], [], []))
        self.assertEqual((r["objects"]["modified"], r["objects"]["added"], r["objects"]["removed"]), ([], [], []))
        self.assertFalse(r["empty"])

    def test_added_subassembly_shows_in_the_bom_delta(self):
        r = self.d("machine", "A.1", "A.2")
        names = {o["name"] for o in r["objects"]["added"]}
        self.assertTrue({"Cover", "CoverLink", "CoverPlate", "SubC"} <= names)
        self.assertEqual(r["objects"]["removed"], [])
        self.assertEqual([(a["path"], a["level"], a["kind"], a["quantity"]) for a in r["bom"]["added"]],
                         [("Cover", 1, "assembly", 1), ("Cover/CoverPlate", 2, "part", 1)])
        self.assertEqual(r["bom"]["removed"], [])
        # a third link to Sub; every component row of the machine document moved to revision A.2; bracket A.1 -> A.2 in between
        want = {
            "Sub": {"quantity": [2, 3], "revision": ["A.1", "A.2"]},
            "Sub/Plate": {"revision": ["A.1", "A.2"]},
            "Sub/bracket:Body": {"revision": ["A.1", "A.2"]},
            "bracket:Body": {"revision": ["A.1", "A.2"]},
        }
        self.assertEqual({c["path"]: c["changes"] for c in r["bom"]["changed"]}, want)

    def test_identical_revisions_diff_to_empty(self):
        for name, rev in (("bracket", "A.2"), ("machine", "A.1"), ("frame", "A.1")):
            r = self.d(name, rev, rev)
            self.assertTrue(r["empty"], r)
            self.assertEqual((r["properties"], r["outputs"], r["shapes"], r["expressions"], r["structure"]), ([], [], [], [], []))
            self.assertEqual((r["objects"]["added"], r["objects"]["removed"], r["objects"]["modified"], r["objects"]["renamed"]),
                             ([], [], [], []))
            self.assertEqual(r["bom"], {"added": [], "removed": [], "changed": []})

    def test_return_shape_and_no_leaked_documents(self):
        before = set(App.listDocuments())
        r = self.d("bracket", "A.1", "A.2")
        self.assertEqual(set(r), {"schema", "document", "from", "to", "empty", "objects", "properties", "outputs",
                                  "expressions", "structure", "shapes", "bom"})
        self.assertEqual((r["schema"], r["document"], r["from"]["revision"], r["to"]["revision"]), ("pdm.diff/1", "bracket", "A.1", "A.2"))
        self.assertEqual(set(App.listDocuments()), before)
        with self.assertRaises(V.DocumentError):
            self.v.diff("bracket", "A.1", "Z.9")


def rows(r):
    return [(x["level"], x["name"], x["kind"], x["quantity"]) for x in r]


class TestEcoDiff(FixtureCase):
    """Check 4, last sentence: an ECO shows the diff of every document it changes."""

    def test_eco_diff_is_vault_diff_per_document_from_the_last_released_revision(self):
        v = self.v
        v.transition("bracket", "InReview")
        v.transition("bracket", "Released")  # bracket A.3 is Released
        v.checkout("bracket", new_revision=True)
        doc = App.openDocument(v.doc_path("bracket"))
        doc.getObject("BodyPad").Length = 25.0
        doc.recompute()
        doc.save()
        self.assertEqual(v.checkin("bracket", "longer again")["revision"], "B.1")
        F.close_all()
        eco = v.eco_create("longer bracket, newer machine", "fit", {"bracket": "B.1", "machine": "A.2"})
        res = v.eco_diff(eco["number"])
        self.assertEqual([(r["document"], r["from"], r["to"]) for r in res],
                         [("bracket", "A.3", "B.1"), ("machine", "A.1", "A.2")])  # machine was never released: its first revision
        self.assertEqual(res[0]["diff"], v.diff("bracket", "A.3", "B.1"))
        self.assertEqual(res[1]["diff"], v.diff("machine", "A.1", "A.2"))
        self.assertEqual([(p["object"], p["property"], p["before"]["value"], p["after"]["value"])
                          for p in res[0]["diff"]["properties"]], [("BodyPad", "Length", 15.0, 25.0)])
        self.assertTrue({"Cover", "SubC"} <= {o["name"] for o in res[1]["diff"]["objects"]["added"]})
        json.dumps(res)


class TestBOMLinkEdgeCases(unittest.TestCase):
    """Verifier findings of run 031: links to sub-objects, links to links and arrays, cross-document cycles,
    hidden and excluded objects, material fallback, unique names. Each expectation is written by hand."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("CADFORK_CTL", CTL)
        cls.tmp = os.path.realpath(tempfile.mkdtemp(prefix="pdmbom2"))
        F.close_all()
        cls.v = V.Vault.clone(V.Vault.create(os.path.join(cls.tmp, "vault.git")), os.path.join(cls.tmp, "wc"), "alice")

    @classmethod
    def tearDownClass(cls):
        F.close_all()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def newdoc(self, name):
        d = App.newDocument(name)
        d.saveAsFolder(self.v.doc_path(name))
        return d

    @staticmethod
    def box(d, name, material=None):
        b = d.addObject("Part::Box", name)
        if material:
            b.addProperty("App::PropertyString", "Material", "Base")
            b.Material = material
        return b

    def commit(self, *names):
        for n in names:
            d = App.getDocument(n)
            d.recompute()
            d.save()
        F.close_all()
        for n in names:
            self.v.add_document(n)

    def test_link_to_sub_object_lists_that_sub_object(self):
        d = self.newdoc("sub1")
        p, inner = d.addObject("App::Part", "P"), d.addObject("App::Part", "Inner")
        p.addObject(inner)
        inner.addObject(self.box(d, "X"))
        inner.addObject(self.box(d, "Z"))
        d.addObject("App::Link", "LS").setLink(p, "Inner.X.")
        self.commit("sub1")
        # P stays a component (a link to a sub-object does not use it up); LS is one more X
        self.assertEqual(rows(self.v.bom("sub1")), [(0, "sub1", "assembly", 1), (1, "P", "assembly", 1),
                                                   (2, "Inner", "assembly", 1), (3, "X", "part", 1),
                                                   (3, "Z", "part", 1), (1, "X", "part", 1)])

    def test_cross_document_sub_object_link_is_the_only_component(self):
        d = self.newdoc("lib1")
        p, inner = d.addObject("App::Part", "P"), d.addObject("App::Part", "Inner")
        p.addObject(inner)
        inner.addObject(self.box(d, "X", "Cu"))
        m = self.newdoc("mach1")
        m.addObject("App::Link", "LS").setLink(p, "Inner.X.")
        self.commit("lib1", "mach1")
        # not collapsed to a lone part: the document is an assembly of one linked component
        r = self.v.bom("mach1")
        self.assertEqual(rows(r), [(0, "mach1", "assembly", 1), (1, "lib1", "part", 1)])
        self.assertEqual(r[1]["material"], "Cu")

    def test_quantities_multiply_through_links(self):
        d = self.newdoc("qty1")
        b = self.box(d, "Bx")
        l1 = d.addObject("App::Link", "L1")
        l1.LinkedObject, l1.ElementCount = b, 3
        l2 = d.addObject("App::Link", "L2")
        l2.LinkedObject, l2.ElementCount = l1, 2        # 2 x 3 = 6
        l3 = d.addObject("App::Link", "L3")
        l3.LinkedObject = l1                            # ElementCount 0 counts as 1: 3
        l4 = d.addObject("App::Link", "L4")
        l4.LinkedObject = l3                            # link to a link to the array: 3
        d2 = self.newdoc("qty2")
        c = self.box(d2, "Cx")
        a1 = d2.addObject("App::Link", "A1")
        a1.LinkedObject, a1.ElementCount = c, 3
        d2.addObject("App::Link", "A2").LinkedObject = a1
        self.commit("qty1", "qty2")
        # top level of qty1 is L2 and L4 (L1, L3 are used): 6 + 3 identical Bx = 9
        self.assertEqual(rows(self.v.bom("qty1")), [(0, "qty1", "assembly", 1), (1, "Bx", "part", 9)])
        self.assertEqual([(x["name"], x["quantity"]) for x in self.v.bom_flat("qty1")], [("Bx", 9)])
        # a plain link to an array of 3 is 3
        self.assertEqual(rows(self.v.bom("qty2")), [(0, "qty2", "assembly", 1), (1, "Cx", "part", 3)])

    def test_cross_document_cycle_keeps_both_components_and_terminates(self):
        p, q = self.newdoc("cyc_p"), self.newdoc("cyc_q")
        bp, bq = self.box(p, "BP"), self.box(q, "BQ")
        self.box(p, "P2")
        self.box(q, "Q2")
        p.addObject("App::Link", "LtoQ").LinkedObject = bq
        q.addObject("App::Link", "LtoP").LinkedObject = bp
        self.commit("cyc_p", "cyc_q")  # added one after the other: cyc_p's first revision predates cyc_q
        want_p = [(0, "cyc_p", "assembly", 1), (1, "BP", "part", 1), (1, "P2", "part", 1), (1, "cyc_q", "part", 1)]
        want_q = [(0, "cyc_q", "assembly", 1), (1, "BQ", "part", 1), (1, "Q2", "part", 1), (1, "cyc_p", "part", 1)]
        self.assertEqual(rows(self.v.bom("cyc_p")), want_p)
        self.assertEqual(rows(self.v.bom("cyc_q")), want_q)
        # the first revision of cyc_p predates cyc_q: a clear error naming the missing document
        with self.assertRaises(V.DocumentError) as cm:
            self.v.bom("cyc_p", "A.1")
        self.assertIn("cyc_q", str(cm.exception))
        self.assertIn("not in the vault", str(cm.exception))
        # a second revision of cyc_p, taken when cyc_q exists: BOM and diff work, a diff with itself is empty
        self.v.checkout("cyc_p")
        d = App.openDocument(self.v.doc_path("cyc_p"))
        d.getObject("BP").Length = 20
        d.recompute()
        d.save()
        rev = self.v.checkin("cyc_p", "longer")["revision"]
        F.close_all()
        self.assertEqual(rows(self.v.bom("cyc_p", rev)), want_p)
        r = self.v.diff("cyc_p", rev, rev)
        self.assertTrue(r["empty"])
        self.assertEqual(r["bom"], {"added": [], "removed": [], "changed": []})

    def test_hidden_listed_excluded_and_suppressed_left_out(self):
        d = self.newdoc("vis1")
        self.box(d, "Vis")
        self.box(d, "Hidden").Visibility = False   # view state: still a component
        for prop, name in (("ExcludeFromAssembly", "Excl"), ("Suppressed", "Supp")):
            o = self.box(d, name)
            o.addProperty("App::PropertyBool", prop, "Base")
            setattr(o, prop, True)
        keep = self.box(d, "KeepFlag")
        keep.addProperty("App::PropertyBool", "Suppressed", "Base")  # present but false: kept
        d.addObject("App::Link", "ToExcl").LinkedObject = d.getObject("Excl")  # a link to an excluded object is left out
        arr = d.addObject("App::Link", "Arr")
        arr.LinkedObject, arr.ElementCount, arr.ShowElement = self.box(d, "Bx"), 4, True
        arr.ElementList[0].Visibility = False      # a hidden element of a ShowElement array still counts
        self.commit("vis1")
        self.assertEqual(rows(self.v.bom("vis1")), [(0, "vis1", "assembly", 1), (1, "Bx", "part", 4),
                                                   (1, "Hidden", "part", 1), (1, "KeepFlag", "part", 1),
                                                   (1, "Vis", "part", 1)])

    def test_material_default_is_unassigned(self):
        import types
        mk = lambda name: types.SimpleNamespace(TypeId="Part::Box", Material="", ShapeMaterial=types.SimpleNamespace(Name=name))
        self.assertEqual(B._material(mk("Default")), "")
        self.assertEqual(B._material(mk("Steel-Generic")), "Steel-Generic")
        d = self.newdoc("mat1")
        self.box(d, "Plain")
        self.commit("mat1")
        self.assertEqual(self.v.bom("mat1")[0]["material"], "")

    def test_two_parts_of_one_document_get_unique_names(self):
        lib = self.newdoc("lib2")
        a, b = self.box(lib, "PartA"), self.box(lib, "PartB")
        a.Label, b.Label = "Axle", "Bearing"
        m = self.newdoc("mach2")
        m.addObject("App::Link", "LA").LinkedObject = a
        m.addObject("App::Link", "LB").LinkedObject = b
        lb2 = m.addObject("App::Link", "LB2")
        lb2.LinkedObject, lb2.ElementCount = b, 2
        self.commit("lib2", "mach2")
        r = self.v.bom("mach2")
        self.assertEqual(rows(r), [(0, "mach2", "assembly", 1), (1, "lib2:Axle", "part", 1), (1, "lib2:Bearing", "part", 3)])
        flat = self.v.bom_flat("mach2")
        self.assertEqual([(x["name"], x["quantity"]) for x in flat], [("lib2:Axle", 1), ("lib2:Bearing", 3)])

    def test_sub_object_link_with_element_count_counts_each_element_once(self):
        d = self.newdoc("sub2")
        p, inner = d.addObject("App::Part", "P"), d.addObject("App::Part", "Inner")
        p.addObject(inner)
        inner.addObject(self.box(d, "X"))
        inner.addObject(self.box(d, "Z"))
        ls = d.addObject("App::Link", "LS")
        ls.setLink(p, "Inner.X.")
        ls.ElementCount = 4  # creates LinkElements LS_i0..3, which are the link's own, not components
        self.commit("sub2")
        self.assertEqual(rows(self.v.bom("sub2")), [(0, "sub2", "assembly", 1), (1, "P", "assembly", 1),
                                                   (2, "Inner", "assembly", 1), (3, "X", "part", 1),
                                                   (3, "Z", "part", 1), (1, "X", "part", 4)])
        self.assertEqual({x["name"]: x["quantity"] for x in self.v.bom_flat("sub2")}, {"X": 5, "Z": 1})

    def test_adding_a_sibling_part_reports_only_the_addition(self):
        lib = self.newdoc("lib3")
        self.box(lib, "Bolt")
        self.box(lib, "Nut")
        m = self.newdoc("main3")
        m.addObject("App::Link", "L").LinkedObject = lib.getObject("Bolt")
        self.commit("lib3", "main3")

        def add_nut(doc):
            lib_doc = App.openDocument(self.v.doc_path("lib3"))
            doc.addObject("App::Link", "L2").LinkedObject = lib_doc.getObject("Nut")
        self.v.checkout("main3")
        doc = App.openDocument(self.v.doc_path("main3"))
        add_nut(doc)
        doc.recompute()
        doc.save()
        self.v.checkin("main3", "add nut")
        F.close_all()
        d = self.v.diff("main3", "A.1", "A.2")["bom"]
        self.assertEqual([r["name"] for r in d["added"]], ["lib3:Nut"])
        self.assertEqual(d["removed"], [])
        self.assertEqual(d["changed"], [])

    def test_flat_grouping_keeps_object_identity(self):
        rws = [{"level": 0, "name": "r", "kind": "assembly", "revision": "A.1", "quantity": 1, "material": "",
                "document": "r", "object": None},
               {"level": 1, "name": "P", "kind": "part", "revision": "A.1", "quantity": 1, "material": "",
                "document": "r", "object": "Body"},
               {"level": 1, "name": "P", "kind": "part", "revision": "A.1", "quantity": 2, "material": "",
                "document": "r", "object": "Body001"}]
        self.assertEqual([x["quantity"] for x in B.flatten(rws)], [1, 2])


if __name__ == "__main__":
    unittest.main()
