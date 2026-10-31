# SPDX-License-Identifier: LGPL-2.1-or-later
"""G03 slice 1: the "FCStd as folder" save mode (Document.saveAsFolder, saveAs "*.FCStd.d").

Acceptance checks of briefs/phase1/G03-pdm-plm.md: round trip over the golden corpus in a fresh
process, determinism, diffability, links between folder documents, and an untouched zip path.
"""

import difflib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

import FreeCAD as App

from AgentCore.paths import source_root

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.join(HERE, "fixtures", "folder", "folder_worker.py")
CTL = os.path.expanduser(os.environ.get("CADFORK_CTL", "~/_agents/repos/app development/cad-fork"))
GOLDEN = os.path.join(CTL, "fixtures", "golden")
MODELS = os.path.join(GOLDEN, "models")
CMD = os.path.join(App.getHomePath(), "bin", "FreeCADCmd")
ENV = dict(os.environ, QT_QPA_PLATFORM="offscreen")
REL_TOL = 4e-6  # tolerances.json rel_tol of I3, relative to the quantity (to the diagonal for positions)
MANIFEST = "FCStdFolder.txt"


def run_cmd(args, timeout=1800):
    return subprocess.run([CMD] + args, env=ENV, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, timeout=timeout)


def tree_text(path):
    out = {}
    for root, _dirs, files in os.walk(path):
        for f in files:
            full = os.path.join(root, f)
            with open(full, "rb") as fh:
                out[os.path.relpath(full, path)] = fh.read()
    return out


def corpus_files(scratch):
    """(id, path) of every FCStd in the golden corpus: the scripted models (built here) and the
    upstream manifest's FCStd files."""
    items = []
    if os.path.isdir(MODELS):
        out = os.path.join(scratch, "scripted")
        env = dict(ENV, CADFORK_CORPUS_MODELS=MODELS, CADFORK_CORPUS_OUT=out)
        script = os.path.join(CTL, "harness", "golden", "build_corpus.py")
        subprocess.run([CMD, "-c", f"exec(open({script!r}).read())"], env=env, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=900)
        for f in sorted(os.listdir(out)) if os.path.isdir(out) else []:
            if f.endswith(".FCStd"):
                items.append(("scripted_" + f[:-6], os.path.join(out, f)))
    root = source_root()
    with open(os.path.join(GOLDEN, "manifest-upstream.txt")) as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and line.lower().endswith(".fcstd"):
                p = os.path.join(root, line)
                if os.path.isfile(p):
                    items.append(("upstream_" + line.replace("/", "__")[:-6], p))
    return items


NUMBER = re.compile(r"-?\d+\.\d+(?:[eE][-+]?\d+)?")


def text_close(a, b, tol=1e-12):
    """Equal text, or equal apart from decimal numbers that differ by at most tol: the first
    reload renormalises quaternions and rotation matrices, which moves the last digits."""
    if a == b:
        return True
    if not (isinstance(a, str) and isinstance(b, str)) or NUMBER.sub("#", a) != NUMBER.sub("#", b):
        return False
    return all(abs(float(x) - float(y)) <= tol for x, y in zip(NUMBER.findall(a), NUMBER.findall(b)))


def props_close(pa, pb):
    """Property dumps (member name -> text or hash) equal within text_close."""
    return set(pa) == set(pb) and all(text_close(pa[m], pb[m]) for m in pa)


def metrics_close(a, b):
    """Metrics of the same shape loaded from zip and from folder, within I3's relative tolerance."""
    if a is None or b is None:
        return a == b
    for key in ("type", "solids", "faces", "edges", "vertexes"):
        if a.get(key) != b.get(key):
            return False
    diag = max(a.get("diag", 0.0), 1e-12)
    for key in ("volume", "area"):
        if key in a and abs(a[key] - b[key]) > REL_TOL * max(abs(a[key]), 1e-12):
            return False
    for key in ("bbox", "com"):
        if key in a and any(abs(x - y) > REL_TOL * diag for x, y in zip(a[key], b[key])):
            return False
    return True


FEM_PROPS = {"FemMesh", "Data", "Normals"}
FEM_DOCS = set()  # filled from the corpus ids: every document whose id mentions Fem or FEM


class TestCorpusRoundTrip(unittest.TestCase):
    """Check 1 (round trip in a fresh process) and check 2 (determinism) over the whole corpus."""

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(MODELS):
            raise unittest.SkipTest("control repo golden corpus not found; set CADFORK_CTL")
        cls.tmp = tempfile.mkdtemp(prefix="g03corpus")
        cls.items = corpus_files(cls.tmp)
        FEM_DOCS.update(i for i, _ in cls.items if "Fem" in i or "FEM" in i)
        spec = os.path.join(cls.tmp, "spec.json")
        with open(spec, "w") as fh:
            json.dump({"out": os.path.join(cls.tmp, "out"),
                       "items": [{"id": i, "src": p} for i, p in cls.items]}, fh)
        cls.results = {}
        for mode in ("save", "load"):
            outj = os.path.join(cls.tmp, mode + ".json")
            r = run_cmd([WORKER, mode, spec, outj])
            if not os.path.isfile(outj):
                raise RuntimeError(f"worker {mode} failed:\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
            with open(outj) as fh:
                cls.results[mode] = json.load(fh)

    @classmethod
    def tearDownClass(cls):
        if os.environ.get("G03_KEEP") == "1":
            print("\nG03 scratch kept at", cls.tmp)
        else:
            shutil.rmtree(cls.tmp, ignore_errors=True)

    def usable(self):
        """Corpus entries that open from zip at all (the baseline); a file that fails to open
        in the original format is a corpus problem, not a folder-format one."""
        return [i for i, _ in self.items if "error" not in self.results["save"][i]]

    def test_corpus_is_not_empty(self):
        self.assertGreaterEqual(len(self.items), 70)
        self.assertGreaterEqual(len(self.usable()), len(self.items) - 3,
                                {i: r["error"][-300:] for i, r in self.results["save"].items() if "error" in r})

    def test_round_trip_properties_and_geometry(self):
        failures = []
        for i in self.usable():
            s, l = self.results["save"][i], self.results["load"].get(i, {})
            if "error" in l:
                failures.append((i, "load error", l["error"][-300:]))
                continue
            a, b = s["dump"], l["dump"]
            if l["shape_mismatch"]:
                failures.append((i, "shapes beyond 1e-12", l["shape_mismatch"]))
            if sorted(a["objects"]) != sorted(b["objects"]):
                failures.append((i, "object set", sorted(set(a["objects"]) ^ set(b["objects"]))))
                continue
            badd = [k for k in a["doc"] if k not in b["doc"] or not props_close(a["doc"][k], b["doc"][k])]
            if badd:
                failures.append((i, "document properties", badd))
            for name, oa in a["objects"].items():
                ob = b["objects"][name]
                if oa["type"] != ob["type"]:
                    failures.append((i, name, "type"))
                bad = sorted(k for k in set(oa["props"]) | set(ob["props"])
                             if k not in oa["props"] or k not in ob["props"]
                             or not props_close(oa["props"][k], ob["props"][k]))
                if i in FEM_DOCS:
                    # FEM mesh and result arrays are binary data files whose bytes the FEM
                    # property classes produce differently on each write (zip path included)
                    bad = [k for k in bad if k not in FEM_PROPS]
                if bad:
                    failures.append((i, name, "properties", bad))
                if not metrics_close(oa["metrics"], ob["metrics"]):
                    failures.append((i, name, "geometry", oa["metrics"], ob["metrics"]))
        n = len(self.usable())
        print(f"\nG03 round trip: {n - len({f[0] for f in failures})}/{n} corpus documents identical "
              f"({len(self.items)} in corpus)")
        self.maxDiff = None
        self.assertEqual(failures[:6], [])

    def test_save_twice_is_byte_identical(self):
        bad = [i for i in self.usable() if not self.results["save"][i]["twice_identical"]]
        self.assertEqual(bad, [])

    def test_save_after_reopen_is_identical(self):
        """Check 2. The first reload of a document moves two things once, as in the zip path
        (ArchDetail.FCStd changes 20 lines of Document.xml on its first zip reload and none on
        the second): the Touched bit of some property status attributes, and the last digits of
        BREP Location matrices (OCCT rebuilds them on read; at most 1e-12). The folder tree may
        differ from the first save only in those two ways, and the second reload must change
        nothing at all."""
        load = {i: self.results["load"][i] for i in self.usable()}
        strict = [i for i, r in load.items() if r["reopen_saveCopy_identical"] and r["reopen_save_identical"]]
        print(f"\nG03 save after reopen: {len(strict)}/{len(load)} corpus documents byte-identical on the "
              f"first reopen; the rest differ only in Touched bits / 1e-12 Location noise; ")
        bad = [(i, r["reopen_differs"]) for i, r in load.items() if not r["reopen_status_only"]]
        self.assertEqual(bad, [])
        settled = sum(r["settled_identical"] for r in load.values())
        print(f"G03 second reopen: {settled}/{len(load)} byte-identical, "
              f"{sum(r['settled_benign'] for r in load.values())}/{len(load)} identical apart from the same two kinds of noise")
        self.assertEqual([i for i, r in load.items() if not r["settled_benign"]], [])

    def test_brep_files_are_text(self):
        bad = [i for i in self.usable() if not self.results["save"][i]["text_brep"]]
        self.assertEqual(bad, [])

    def test_folder_doc_is_bound_to_folder(self):
        for i in self.usable():
            self.assertTrue(self.results["load"][i]["filename"].endswith("A.FCStd.d"), i)


def build_bracket():
    """The scripted bracket (Body, Sketches, Pad, Pocket, Fillet) as a fresh document."""
    import importlib.util
    helpers = importlib.util.spec_from_file_location("_helpers", os.path.join(MODELS, "_helpers.py"))
    h = importlib.util.module_from_spec(helpers)
    helpers.loader.exec_module(h)
    sys.path.insert(0, MODELS)
    try:
        spec = importlib.util.spec_from_file_location("m13_bracket", os.path.join(MODELS, "m13_bracket.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(MODELS)
    doc = App.newDocument("Bracket")
    mod.build(doc, dict(mod.PARAMS), h)
    doc.recompute(None, True)
    return doc


@unittest.skipUnless(os.path.isdir(MODELS), "control repo golden models not found; set CADFORK_CTL")
class TestFolderBehaviour(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="g03")
        self.doc = build_bracket()

    def tearDown(self):
        for d in list(App.listDocuments()):
            App.closeDocument(d)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def p(self, *a):
        return os.path.join(self.tmp, *a)

    def test_save_as_suffix_and_explicit_api_agree(self):
        self.doc.saveCopy(self.p("c.FCStd.d"))
        self.doc.saveAsFolder(self.p("x.FCStd.d"))
        self.assertEqual(self.doc.FileName, self.p("x.FCStd.d"))
        self.assertEqual(self.doc.Label, "x")
        self.doc.saveAs(self.p("y.FCStd.d"))
        self.assertEqual(self.doc.FileName, self.p("y.FCStd.d"))
        self.assertTrue(os.path.isfile(self.p("y.FCStd.d", "Document.xml")))
        self.assertTrue(os.path.isfile(self.p("y.FCStd.d", MANIFEST)))
        self.assertFalse(os.path.exists(self.p("y.FCStd.d.FCStd")))

    def test_open_and_save_back_to_folder(self):
        self.doc.saveAsFolder(self.p("b.FCStd.d"))
        App.closeDocument(self.doc.Name)
        doc = App.openDocument(self.p("b.FCStd.d"))
        self.assertEqual(doc.FileName, self.p("b.FCStd.d"))
        self.assertTrue({"Body", "Pad", "SlotPocket", "Fillet"} <= {o.Name for o in doc.Objects})
        # A document built in memory settles on its first reload (transient property status bits);
        # from then on every reopen and save gives the same tree.
        doc.save()
        App.closeDocument(doc.Name)
        before = tree_text(self.p("b.FCStd.d"))
        doc = App.openDocument(self.p("b.FCStd.d"))
        doc.save()
        self.assertEqual(tree_text(self.p("b.FCStd.d")), before)
        self.assertTrue(doc.getObject("Fillet").Shape.isValid())

    def test_diff_of_one_pad_length(self):
        """Check 3: one Pad length changes only that parameter and the shapes recomputed from it."""
        self.doc.saveAsFolder(self.p("d.FCStd.d"))
        a = tree_text(self.p("d.FCStd.d"))
        pad = self.doc.getObject("Pad")
        pad.Length = float(pad.Length) + 3.0
        self.doc.recompute(None, True)
        self.doc.save()
        b = tree_text(self.p("d.FCStd.d"))
        changed = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
        self.assertEqual(set(a), set(b), "files appeared or vanished")
        xml = [l for l in difflib.unified_diff(a["Document.xml"].decode().splitlines(),
                                               b["Document.xml"].decode().splitlines(), lineterm="", n=0)
               if l[:1] in "+-" and l[:3] not in ("+++", "---")]
        print(f"\nG03 diff of one Pad length: {len(xml)} changed lines in Document.xml, "
              f"{len(changed)} files changed: {changed}")
        # Everything that changed belongs to the Pad or to objects downstream of it
        allowed = ("Document.xml", "StringHasher.Table.txt", "Pad.", "SlotPocket.", "Fillet.", "Body.")
        self.assertEqual([c for c in changed if not c.startswith(allowed)], [])
        for sketch_file in [c for c in changed if c.startswith(("PlateSketch.", "SlotSketch."))]:
            self.fail(f"sketch file changed: {sketch_file}")
        self.assertIn("Document.xml", changed)
        self.assertLessEqual(len(xml), 24, "\n".join(xml))
        self.assertTrue(any("13" in l or "Float" in l for l in xml), xml)

    def test_stale_files_removed_and_foreign_files_kept(self):
        target = self.p("s.FCStd.d")
        self.doc.saveAsFolder(target)
        os.makedirs(os.path.join(target, ".git"))
        with open(os.path.join(target, ".git", "HEAD"), "w") as fh:
            fh.write("ref: refs/heads/main\n")
        with open(os.path.join(target, "README.md"), "w") as fh:
            fh.write("keep me\n")
        before = set(tree_text(target))
        self.doc.removeObject("Fillet")
        self.doc.recompute()
        self.doc.save()
        after = set(tree_text(target))
        self.assertFalse([f for f in after if f.startswith("Fillet.")])
        self.assertTrue([f for f in before if f.startswith("Fillet.")])
        self.assertIn("README.md", after)
        self.assertIn(os.path.join(".git", "HEAD"), after)
        self.assertFalse([f for f in os.listdir(self.tmp) if ".saving-" in f])

    def test_failed_save_leaves_folder_intact(self):
        target = self.p("f.FCStd.d")
        self.doc.saveAsFolder(target)
        before = tree_text(target)
        # a file where the staging directory's parent would have to be: the save must fail
        # before touching the target, and leave no staging directory behind
        blocker = self.p("blocked")
        open(blocker, "w").close()
        with self.assertRaises(Exception):
            self.doc.saveCopy(os.path.join(blocker, "x.FCStd.d"))
        self.assertEqual(tree_text(target), before)
        # a target path that is a regular file is refused too
        open(self.p("g.FCStd.d"), "w").close()
        with self.assertRaises(Exception):
            self.doc.saveCopy(self.p("g.FCStd.d"))
        self.assertFalse([f for f in os.listdir(self.tmp) if ".saving-" in f])
        doc = App.openDocument(target)
        self.assertTrue(doc.getObject("Fillet").Shape.isValid())

    def test_broken_folder_is_refused(self):
        empty = self.p("e.FCStd.d")
        os.makedirs(empty)
        with self.assertRaises(Exception):
            App.openDocument(empty)

    def test_zip_path_unchanged(self):
        """Check 5: .FCStd still writes a zip, and its Document.xml is the folder's, byte for byte."""
        self.doc.saveCopy(self.p("z.FCStd"))
        self.doc.saveCopy(self.p("z.FCStd.d"))
        with zipfile.ZipFile(self.p("z.FCStd")) as z:
            names = set(z.namelist())
            zxml = z.read("Document.xml")
        folder = tree_text(self.p("z.FCStd.d"))
        self.assertEqual(names, set(folder) - {MANIFEST})
        # identical apart from negative zeros, which the folder writer writes as zero
        self.assertEqual(re.sub(rb'(?<=")-(0\.0+)(?=")', rb"\1", zxml), folder["Document.xml"])
        doc = App.openDocument(self.p("z.FCStd"))
        self.assertFalse(os.path.isdir(doc.FileName))
        doc.save()
        self.assertTrue(zipfile.is_zipfile(self.p("z.FCStd")))
        self.assertEqual(self.p("z.FCStd"), doc.FileName)

    def test_zip_last_modified_date_still_updates(self):
        self.doc.saveAs(self.p("t.FCStd"))
        first = self.doc.LastModifiedDate
        import time
        time.sleep(1.2)
        self.doc.save()
        self.assertNotEqual(self.doc.LastModifiedDate, first)

    def test_folder_last_modified_date_is_pinned(self):
        self.doc.saveAsFolder(self.p("l.FCStd.d"))
        first = self.doc.LastModifiedDate
        self.assertTrue(first)
        import time
        time.sleep(1.2)
        self.doc.save()
        self.assertEqual(self.doc.LastModifiedDate, first)


def shape_doc(name, **shapes):
    import Part
    doc = App.newDocument(name)
    for n, shape in shapes.items():
        doc.addObject("Part::Feature", n).Shape = shape
    return doc


class TestFolderRepair(unittest.TestCase):
    """Verifier findings on slice 1 (data-loss and safety edge cases)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="g03r")

    def tearDown(self):
        for d in list(App.listDocuments()):
            App.closeDocument(d)
        subprocess.call(["chflags", "-R", "nouchg", self.tmp], stderr=subprocess.DEVNULL) if sys.platform == "darwin" else None
        shutil.rmtree(self.tmp, ignore_errors=True)

    def p(self, *a):
        return os.path.join(self.tmp, *a)

    def test_case_collision_is_refused_and_rename_by_case_survives(self):
        import Part
        # HIGH 1a: Box and box would be one file on APFS: refuse loudly, whatever the platform
        doc = shape_doc("caseA", Box=Part.makeBox(1, 2, 3), box=Part.makeSphere(5))
        with self.assertRaises(Exception) as cm:
            doc.saveAsFolder(self.p("a.FCStd.d"))
        self.assertIn("case", str(cm.exception))
        self.assertEqual(doc.FileName, "")  # LOW 8: a failed saveAs does not rename the document
        # HIGH 1b: delete Box, add box, save again: the new file must not be swept away
        doc = shape_doc("caseB", Box=Part.makeBox(1, 2, 3))
        doc.saveAsFolder(self.p("b.FCStd.d"))
        doc.removeObject("Box")
        doc.addObject("Part::Feature", "box").Shape = Part.makeBox(4, 5, 6)
        doc.save()
        self.assertEqual(sorted(f for f in os.listdir(self.p("b.FCStd.d")) if f.endswith(".brp")),
                         ["box.Shape.brp"])
        App.closeDocument(doc.Name)
        doc = App.openDocument(self.p("b.FCStd.d"))
        self.assertAlmostEqual(doc.box.Shape.Volume, 120.0)
        # same content under a re-cased name: the stale file still must not delete the new one
        doc.removeObject("box")
        doc.addObject("Part::Feature", "BOX").Shape = Part.makeBox(4, 5, 6)
        doc.save()
        App.closeDocument(doc.Name)
        doc = App.openDocument(self.p("b.FCStd.d"))
        self.assertAlmostEqual(doc.BOX.Shape.Volume, 120.0)
        self.assertEqual(sorted(f for f in os.listdir(self.p("b.FCStd.d")) if f.endswith(".brp")),
                         ["BOX.Shape.brp"])

    def test_manifest_cannot_delete_outside_or_hidden_files(self):
        import Part
        outside = self.p("outside")
        os.makedirs(outside)
        for n in ("precious.txt", "keep.txt"):
            open(os.path.join(outside, n), "w").write("user data")
        target = self.p("crafted.FCStd.d")
        os.makedirs(os.path.join(target, ".git"))
        open(os.path.join(target, ".git", "HEAD"), "w").write("ref\n")
        open(os.path.join(target, "README"), "w").write("readme")
        os.symlink(outside, os.path.join(target, "lnk"))
        open(self.p("sibling.txt"), "w").write("sibling")
        with open(os.path.join(target, MANIFEST), "w") as fh:
            fh.write("FCStdFolder 1\nlnk/precious.txt\n.git/HEAD\nREADME\n../sibling.txt\n"
                     "sub/../../sibling.txt\n./../sibling.txt\n/etc/hosts_x\n")
        doc = shape_doc("crafted", Box=Part.makeBox(1, 1, 1))
        doc.saveAsFolder(target)
        self.assertTrue(os.path.exists(os.path.join(outside, "precious.txt")))
        self.assertTrue(os.path.exists(self.p("sibling.txt")))
        self.assertTrue(os.path.exists(os.path.join(target, ".git", "HEAD")))
        self.assertTrue(os.path.islink(os.path.join(target, "lnk")))
        # README is a plain top-level name from the (previous) manifest: only that may go
        self.assertTrue(set(os.listdir(outside)) == {"precious.txt", "keep.txt"})

    @unittest.skipUnless(sys.platform == "darwin", "uses chflags uchg")
    def test_failed_save_leaves_previous_state_loadable(self):
        import Part
        doc = shape_doc("atomic", Aaa=Part.makeBox(1, 1, 1), Zzz=Part.makeBox(2, 2, 2))
        target = self.p("atomic.FCStd.d")
        doc.saveAsFolder(target)
        before = tree_text(target)
        subprocess.check_call(["chflags", "uchg", os.path.join(target, "Zzz.Shape.brp")])
        doc.Aaa.Shape = Part.makeBox(3, 3, 3)
        doc.Zzz.Shape = Part.makeBox(4, 4, 4)
        doc.Aaa.Label = "changed"
        with self.assertRaises(Exception):
            doc.save()
        self.assertEqual(tree_text(target), before, "a failed save changed the folder")
        self.assertFalse([f for f in os.listdir(self.tmp) if ".saving-" in f])
        subprocess.check_call(["chflags", "nouchg", os.path.join(target, "Zzz.Shape.brp")])
        App.closeDocument(doc.Name)
        doc = App.openDocument(target)
        self.assertAlmostEqual(doc.Aaa.Shape.Volume, 1.0)
        self.assertAlmostEqual(doc.Zzz.Shape.Volume, 8.0)
        self.assertNotEqual(doc.Aaa.Label, "changed")

    def test_negative_zero_rewrite_only_touches_shapes(self):
        src = self.p("inc")
        os.makedirs(src)
        files = {"notes..v2.txt": "hello", "data.xml": '<a v="-0.000"> -0.000 \n-0.000\n</a>\n',
                 "pts.brp": "1 -0.000 2\n-0.000000 x\n"}
        doc = App.newDocument("nz")
        for i, (n, c) in enumerate(files.items()):
            with open(os.path.join(src, n), "w") as fh:
                fh.write(c)
            o = doc.addObject("App::FeaturePython", "G%d" % i)
            o.addProperty("App::PropertyFileIncluded", "Inc")
            o.Inc = os.path.join(src, n)
        m = doc.addObject("App::FeaturePython", "M")
        m.addProperty("App::PropertyMap", "Map")
        m.Map = {"k": "-0.000"}
        m.addProperty("App::PropertyStringList", "SL")
        m.SL = ["-0.000"]
        m.addProperty("App::PropertyString", "S")
        m.S = "-0.000"
        m.addProperty("App::PropertyEnumeration", "E")
        m.E = ["-0.000", "x"]
        m.E = "-0.000"
        doc.Comment = "-0.000"
        target = self.p("nz.FCStd.d")
        doc.saveAsFolder(target)
        App.closeDocument(doc.Name)
        for rep in range(2):  # MEDIUM 5: the ".." file name must survive open and save
            doc = App.openDocument(target)
            for i, (n, c) in enumerate(files.items()):
                f = doc.getObject("G%d" % i).Inc
                self.assertTrue(f and os.path.exists(f), (rep, n, f))
                with open(f) as fh:
                    self.assertEqual(fh.read(), c, n)
            m = doc.M
            self.assertEqual((m.Map, m.SL, m.S, m.E, doc.Comment),
                             ({"k": "-0.000"}, ["-0.000"], "-0.000", "-0.000", "-0.000"))
            doc.M.Label = "touched%d" % rep
            doc.save()  # raised before the fix when an attachment name contained ".."
            App.closeDocument(doc.Name)

    def test_save_as_folder_forces_folder_mode(self):
        import Part
        doc = shape_doc("plain", Box=Part.makeBox(1, 1, 1))
        doc.saveAsFolder(self.p("plaindir"))
        self.assertTrue(os.path.isdir(self.p("plaindir")))
        self.assertTrue(os.path.isfile(self.p("plaindir", "Document.xml")))
        doc.save()  # stays a folder
        self.assertTrue(os.path.isfile(self.p("plaindir", "Document.xml")))
        # an existing directory is written into, never moved aside
        os.makedirs(self.p("existing"))
        open(self.p("existing", "keep.txt"), "w").write("k")
        doc.saveAsFolder(self.p("existing"))
        doc.save()
        self.assertTrue(os.path.isfile(self.p("existing", "keep.txt")))
        self.assertTrue(os.path.isfile(self.p("existing", "Document.xml")))
        self.assertEqual([f for f in os.listdir(self.tmp) if "FCBak" in f or "fcbak" in f.lower()], [])
        App.closeDocument(doc.Name)
        doc = App.openDocument(self.p("plaindir"))
        self.assertAlmostEqual(doc.Box.Shape.Volume, 1.0)

    def test_trailing_slash_opens_the_same_document(self):
        import Part
        doc = shape_doc("twice", Box=Part.makeBox(1, 1, 1))
        doc.saveAsFolder(self.p("t.FCStd.d") + "/")
        self.assertFalse(doc.FileName.endswith("/"))
        App.closeDocument(doc.Name)
        d1 = App.openDocument(self.p("t.FCStd.d"))
        d2 = App.openDocument(self.p("t.FCStd.d") + "/")
        self.assertEqual(d1.Name, d2.Name)
        self.assertEqual(len(App.listDocuments()), 1)

    def test_failed_save_as_keeps_name(self):
        import Part
        doc = shape_doc("fail", Box=Part.makeBox(1, 1, 1))
        doc.saveAs(self.p("ok.FCStd.d"))
        label = doc.Label
        open(self.p("isfile.FCStd.d"), "w").write("not a folder")
        with self.assertRaises(Exception):
            doc.saveAs(self.p("isfile.FCStd.d"))
        self.assertEqual(doc.FileName, self.p("ok.FCStd.d"))
        self.assertEqual(doc.Label, label)
        self.assertEqual(open(self.p("isfile.FCStd.d")).read(), "not a folder")


@unittest.skipUnless(os.path.isdir(MODELS), "control repo golden models not found; set CADFORK_CTL")
class TestFolderLinks(unittest.TestCase):
    """Check 5 of the brief: a folder document with App::Link / PropertyXLink to another folder
    document opens with the link resolved (in a fresh process)."""

    def test_links_between_folder_documents_resolve(self):
        tmp = tempfile.mkdtemp(prefix="g03link")
        try:
            lib = App.newDocument("Lib")
            box = lib.addObject("Part::Box", "Box")
            box.Length, box.Width, box.Height = 10, 20, 30
            lib.recompute()
            lib.saveAsFolder(os.path.join(tmp, "Lib.FCStd.d"))
            asm = App.newDocument("Asm")
            asm.saveAsFolder(os.path.join(tmp, "Asm.FCStd.d"))  # links need a saved owner
            link = asm.addObject("App::Link", "BoxLink")
            link.LinkedObject = box
            link.Placement.Base = App.Vector(100, 0, 0)
            asm.recompute()
            asm.save()
            for d in list(App.listDocuments()):
                App.closeDocument(d)
            probe = os.path.join(tmp, "probe.py")
            with open(probe, "w") as fh:
                fh.write(PROBE)
            out = os.path.join(tmp, "out.json")
            r = run_cmd([probe, os.path.join(tmp, "Asm.FCStd.d"), out])
            self.assertTrue(os.path.isfile(out), r.stdout[-1500:] + r.stderr[-1500:])
            with open(out) as fh:
                res = json.load(fh)
            self.assertEqual(res["linked"], ["Lib", "Box"], res)
            self.assertAlmostEqual(res["volume"], 10 * 20 * 30)
            self.assertIn("Lib", res["docs"])
            self.assertTrue(res["lib_filename"].endswith("Lib.FCStd.d"), res)
            # the link survives a save of the dependent folder document and a second reopen
            self.assertEqual(res["linked_again"], ["Lib", "Box"], res)
        finally:
            for d in list(App.listDocuments()):
                App.closeDocument(d)
            shutil.rmtree(tmp, ignore_errors=True)


PROBE = '''
import json, sys
import FreeCAD as App
src, out = sys.argv[-2:]
doc = App.openDocument(src)
link = doc.getObject("BoxLink")
lo = link.LinkedObject
res = {"docs": sorted(App.listDocuments()), "linked": [lo.Document.Name, lo.Name] if lo else None}
res["volume"] = lo.Shape.Volume if lo else None
res["lib_filename"] = lo.Document.FileName if lo else None
doc.save()
App.closeDocument(doc.Name)
for d in list(App.listDocuments()):
    App.closeDocument(d)
doc = App.openDocument(src)
lo = doc.getObject("BoxLink").LinkedObject
res["linked_again"] = [lo.Document.Name, lo.Name] if lo else None
json.dump(res, open(out, "w"))
'''


if __name__ == "__main__":
    unittest.main()
