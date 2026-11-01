# SPDX-License-Identifier: LGPL-2.1-or-later
"""Acceptance check 5 (slice 5b): the BOM contract, tested through a real PDM vault.

A weldment document (portal frame with trims, plus two posts welded to a beam) is saved in folder
format, added to a vault in a temp dir, checked out from a second working copy and opened there;
``bom_rows`` of the reopened document must equal the rows of the original. A second revision (a
larger weld) checked in from one client must show up in the BOM of a third client's checkout.
"""

import math
import os
import shutil
import tempfile
import unittest

import FreeCAD

import WeldBOM
import WeldCutList
import WeldEndCap
import WeldGusset
from PDM import vault as V
from WTests import frames
from WTests.testBead import edges_at
from WeldBead import make_bead, weld_summary

P = FreeCAD.Vector


def build(doc):
    """Portal frame (mitres and butts) plus a beam with two posts standing on it, each welded."""
    portal = frames.portal_frame(doc)
    doc.recompute()
    WeldGusset.make_gusset(doc, portal.heads["Columns"], 0, "End", portal.heads["Rafters"], 0, "Start",
                           300.0, 250.0, 10.0, 20.0, name="Eave")
    WeldEndCap.make_end_cap(doc, portal.heads["Columns"], 0, "Start", 8.0, name="Base")
    fr = frames.Frame(doc)
    fam = "EN SHS"
    beam = fr.add("Beam", fam, frames.pick(fam, 0.9), [((10000, 0, 0), (14000, 0, 0))], anchor="Top")
    posts = fr.add("Post", fam, frames.pick(fam, 0.15), [((11000, 0, 0), (11000, 0, 800)), ((13000, 0, 0), (13000, 0, 800))],
                   anchor="Bottom")
    doc.recompute()
    beads = []
    for k, (cx, z) in enumerate(((11000.0, 4.0), (13000.0, 3.0))):
        straight = []
        for i, e in enumerate(posts.Shape.Edges):
            a, b = e.valueAt(e.FirstParameter), e.valueAt(e.LastParameter)
            if abs(a.z) < 1e-9 and abs(b.z) < 1e-9 and abs(a.x - cx) < 400 and abs(e.Length - (b - a).Length) < 1e-9:
                straight.append((math.hypot(e.CenterOfMass.x - cx, e.CenterOfMass.y), "Edge%d" % (i + 1)))
        outer = [n for _, n in sorted(straight, reverse=True)[:4]]
        beads.append(make_bead(doc, beam, posts, [(posts, n) for n in outer], z, name="Weld%d" % (k + 1)))
    doc.recompute()
    return beads


def same_rows(test, got, want, tol=1e-6):
    """Rows equal: text, counts and keys exactly; floats (lengths in mm, angles in deg, kg) within tol.

    A document reopened from the vault reloads its shapes from BREP, so cut lengths and end angles
    come back with last-digit noise (the cut list itself clusters at 1e-6)."""
    test.assertEqual(len(got), len(want))
    worst = 0.0
    for g, w in zip(got, want):
        test.assertEqual(list(g), list(w))
        for k in w:
            if isinstance(w[k], float):
                worst = max(worst, abs(g[k] - w[k]))
                test.assertLess(abs(g[k] - w[k]), tol, (w["item"], k))
            else:
                test.assertEqual(g[k], w[k], (w["item"], k))
    return worst


class TestBOM(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wbom")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        for n in list(FreeCAD.listDocuments()):
            FreeCAD.closeDocument(n)

    def test_rows_schema_and_oracle(self):
        doc = FreeCAD.newDocument("Frame")
        beads = build(doc)
        for b in beads:
            self.assertNotIn("Invalid", b.State, b.getStatusString())
        rows = WeldBOM.bom_rows(doc)
        self.assertEqual(rows, WeldBOM.bom_rows(doc))
        self.assertTrue(rows)
        for n, r in enumerate(rows, 1):
            self.assertEqual(list(r), WeldBOM.KEYS)
            self.assertEqual(r["item"], n)
            self.assertEqual(r["document"], "Frame")  # unsaved: the Label
            self.assertAlmostEqual(r["mass_kg"], r["mass_each_kg"] * r["quantity"], places=9)
        kinds = [r["kind"] for r in rows]
        self.assertEqual(kinds, sorted(kinds, key=["member", "plate", "weld"].index))
        # members: the cut list's rows, one for one
        cut = [c for c in WeldCutList.cut_list(WeldBOM.chain_heads(doc)) if c.get("kind") != "plate"]
        members = [r for r in rows if r["kind"] == "member"]
        self.assertEqual(len(members), len(cut))
        self.assertEqual(sum(r["quantity"] for r in members), sum(c["quantity"] for c in cut))
        for r, c in zip(members, cut):
            self.assertEqual((r["family"], r["size"], r["quantity"], r["material"]), (c["family"], c["size"], c["quantity"], c["material"]))
            self.assertEqual(r["length_mm"], c["cut_length_mm"])
            self.assertAlmostEqual(r["mass_kg"], c["mass_total_kg"], places=9)
        # the members the trims consume are not counted twice: 3 portal members (5 pieces) + beam + 2 posts
        self.assertEqual(sum(r["quantity"] for r in members), 5 + 1 + 2)
        # welds: z = 3 and 4, nominal volume z^2/2 L, mass at 7850 kg/m^3, independent of weld_summary
        welds = [r for r in rows if r["kind"] == "weld"]
        self.assertEqual([r["size"] for r in welds], ["z=3 mm", "z=4 mm"])
        for r, b, z in zip(welds, (beads[1], beads[0]), (3.0, 4.0)):
            total = sum(l[0].Shape.getElement(sub).Length for l in b.Edges for sub in l[1])
            self.assertAlmostEqual(r["length_mm"], total, places=6)
            self.assertEqual(r["quantity"], 1)
            self.assertAlmostEqual(r["mass_kg"], z * z / 2 * total * 7850e-9, places=12)
            self.assertEqual(r["material"], "Weld metal")
        self.assertEqual([w["leg_z_mm"] for w in weld_summary(doc)], [3.0, 4.0])
        # plates: one gusset and one end cap, from the cut list's plate items; mass = volume x 7850 kg/m^3
        plates = [r for r in rows if r["kind"] == "plate"]
        self.assertEqual([(r["family"], r["size"], r["quantity"]) for r in plates],
                         [("End cap", "t=8 mm", 1), ("Gusset", "t=10 mm", 1)])
        for r, name in zip(plates, ("Base", "Eave")):
            o = doc.getObject(name)
            self.assertAlmostEqual(r["mass_kg"], o.Shape.Volume * 7850e-9, places=9)
            self.assertAlmostEqual(r["length_mm"], o.OutlineLengthMm, places=9)
            self.assertEqual(r["material"], "Structural steel")
            self.assertIn("plate t=", r["description"])
            self.assertEqual((r["start_angle_deg"], r["end_angle_deg"]), (None, None))

    def _client(self, name, user):
        wc = os.path.join(self.tmp, "wc-" + name)
        return V.Vault.clone(self.vault, wc, user)

    def test_vault_round_trip(self):
        self.vault = V.Vault.create(os.path.join(self.tmp, "vault.git"))
        alice = self._client("a", "alice")
        doc = FreeCAD.newDocument("Frame")
        build(doc)
        doc.saveAsFolder(alice.doc_path("frame"))
        rows = WeldBOM.bom_rows(doc)
        alice.add_document("frame", message="weldment frame")
        # a second client gets the document from the vault and opens it
        bob = self._client("b", "bob")
        path = bob.checkout("frame")
        doc2 = FreeCAD.openDocument(path)
        self.assertNotEqual(doc2.Name, doc.Name)
        rows2 = WeldBOM.bom_rows(doc2)
        worst = same_rows(self, rows2, rows)
        print("\nBOM reopened-from-vault worst numeric difference %.3g" % worst)
        self.assertEqual({r["document"] for r in rows2}, {"frame"})
        # a new revision with a bigger weld flows into the BOM of a third checkout
        doc2.getObject("Weld1").Size = 6.0
        doc2.recompute()
        doc2.save()
        rev = bob.checkin("frame", "bigger weld")
        self.assertEqual(rev["revision"], "A.2")
        carol = self._client("c", "carol")
        doc3 = FreeCAD.openDocument(carol.checkout("frame"))
        rows3 = WeldBOM.bom_rows(doc3)
        self.assertNotEqual(rows3, rows)
        self.assertEqual([r["size"] for r in rows3 if r["kind"] == "weld"], ["z=3 mm", "z=6 mm"])
        same_rows(self, [r for r in rows3 if r["kind"] == "member"], [r for r in rows if r["kind"] == "member"])
        w6 = [r for r in rows3 if r["size"] == "z=6 mm"][0]
        w4 = [r for r in rows if r["size"] == "z=4 mm"][0]
        self.assertAlmostEqual(w6["mass_kg"] / w4["mass_kg"], 36.0 / 16.0, places=9)


if __name__ == "__main__":
    unittest.main()
