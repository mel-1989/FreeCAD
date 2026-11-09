# SPDX-License-Identifier: LGPL-2.1-or-later

# **************************************************************************
#   Tests for PmiAuthoring (G12 slice 3a): the authoring API, bounded       *
#   nominal derivation, per-use datums, undo, the rules table, and the      *
#   export of authored items as AP242 semantic PMI.                         *
#   Run: FreeCADCmd -t TestPartPmiAuthoring  (a scratch FREECAD_USER_HOME)  *
# **************************************************************************

import json
import math
import os
import re
import tempfile
import unittest

import FreeCAD as App
import Part
import PmiAuthoring as PA

Q = App.Units.Quantity


def _prism(doc, name, pts, h=10.0):
    """A prism over a polygon in the XY plane, as a plain Part::Feature."""
    wire = Part.makePolygon([App.Vector(*p) for p in pts] + [App.Vector(*pts[0])])
    sh = Part.Face(wire).extrude(App.Vector(0, 0, h))
    o = doc.addObject("Part::Feature", name)
    o.Shape = sh
    return o


class PmiAuthoringBase(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("PmiAuth")
        self.doc.UndoMode = 1
        self.box = self.doc.addObject("Part::Box", "Box")
        self.box.Length, self.box.Width, self.box.Height = 100.0, 60.0, 40.0
        self.cyl = self.doc.addObject("Part::Cylinder", "Cyl")
        self.cyl.Radius, self.cyl.Height = 5.0, 30.0
        self.cyl.Placement.Base = App.Vector(200, 0, 0)
        self.doc.recompute()

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def pmi_objects(self):
        return [o for o in self.doc.Objects if o.TypeId.startswith("Part::Pmi") and o.TypeId != "Part::PmiGroup"]

    def rules_of(self, item):
        return [r for r, _ in PA.ruleHits(item)]


class TestPmiAuthoringApi(PmiAuthoringBase):
    # check 28
    def test_size_item_properties(self):
        d = PA.addSizeDimension((self.cyl, ["Face1"]), plus_minus=(0.1, -0.2))
        self.assertEqual(d.TypeId, "Part::PmiDimension")
        self.assertEqual(d.PmiType, "Size_Diameter")
        self.assertAlmostEqual(d.Value, 10.0, places=9)
        self.assertTrue(d.HasBounds)
        self.assertEqual((d.BoundsKind, d.UpperBound, d.LowerBound), ("plus_minus", 0.1, -0.2))
        self.assertEqual([(o.Name, list(s)) for o, s in d.References], [("Cyl", ["Face1"])])
        self.assertFalse(d.Unattached)
        self.assertEqual(list(d.StepFaceIds), [])
        self.assertEqual(dict(d.RawAttributes), {})
        self.assertTrue(d.Presentation.isNull())
        groups = [o for o in self.doc.Objects if o.TypeId == "Part::PmiGroup"]
        self.assertEqual(len(groups), 1)
        self.assertIn(d, groups[0].Group)

    def test_group_reused(self):
        PA.addSizeDimension((self.cyl, ["Face1"]))
        PA.addDatum("A", (self.box, ["Face5"]))
        self.assertEqual(len([o for o in self.doc.Objects if o.TypeId == "Part::PmiGroup"]), 1)

    def test_bounds_kinds(self):
        r = PA.addSizeDimension((self.cyl, ["Face1"]), limits=(10.1, 9.9))
        self.assertEqual((r.BoundsKind, r.UpperBound, r.LowerBound), ("range", 10.1, 9.9))
        c = PA.addSizeDimension((self.cyl, ["Face1"]), fit_class="H7")
        self.assertEqual(c.ToleranceClass, "H7")
        self.assertFalse(c.HasBounds)
        with self.assertRaises(ValueError):
            PA.addSizeDimension((self.cyl, ["Face1"]), plus_minus=(1, 0), fit_class="H7")
        with self.assertRaises(ValueError):
            PA.addSizeDimension((self.cyl, ["Face1"]), fit_class="seven")

    def test_location_and_angular(self):
        loc = PA.addLocationDimension((self.box, ["Face1"]), (self.box, ["Face2"]))
        self.assertEqual(loc.PmiType, "Location_LinearDistance")
        self.assertAlmostEqual(loc.Value, 100.0, places=9)
        self.assertEqual(list(loc.SecondSide), ["Box.Face2"])
        self.assertEqual([tuple(s) for o, s in loc.References], [("Face1", "Face2")])
        ang = PA.addAngularDimension((self.box, ["Face1"]), (self.box, ["Face3"]))
        self.assertEqual(ang.PmiType, "Location_Angular")
        self.assertAlmostEqual(ang.Value, 90.0, places=9)

    def test_datum(self):
        d = PA.addDatum("A", (self.box, ["Face5"]))
        self.assertEqual((d.Letter, d.Position, d.IsTarget), ("A", 0, False))
        self.assertEqual([(o.Name, list(s)) for o, s in d.References], [("Box", ["Face5"])])
        for bad in ("a", "AB", "1", ""):
            with self.assertRaises(ValueError):
                PA.addDatum(bad, (self.box, ["Face5"]))

    def test_all_fifteen_tolerance_types(self):
        PA.addDatum("A", (self.box, ["Face5"]))
        self.assertEqual(len(PA.TOL_TYPES), 15)
        for t in PA.TOL_TYPES:
            tol = PA.addTolerance(t, 0.05, (self.box, ["Face6"]), datums=["A"])
            self.assertEqual((tol.TypeId, tol.PmiType, tol.Value), ("Part::PmiTolerance", t, 0.05))
            self.assertEqual(list(tol.DatumReferences), ["A"])
        with self.assertRaises(ValueError):
            PA.addTolerance("Flatnes", 0.1, (self.box, ["Face6"]))

    def test_tolerance_zone_modifiers_and_compartments(self):
        for letter, face in (("A", "Face5"), ("B", "Face1"), ("C", "Face3")):
            PA.addDatum(letter, (self.box, [face]))
        tol = PA.addTolerance(
            "Position",
            0.3,
            (self.cyl, ["Face1"]),
            datums=["A", ("B", ["MAXIMUM_MATERIAL_REQUIREMENT"]), "C"],
            zone_shape="Diameter",
            zone_modifier="Projected",
            zone_modifier_value=4.0,
            modifiers=["MAXIMUM_MATERIAL_REQUIREMENT"],
        )
        self.assertEqual((tol.ZoneShape, tol.ZoneModifier, tol.ZoneModifierValue), ("Diameter", "Projected", 4.0))
        self.assertEqual(list(tol.Modifiers), ["MAXIMUM_MATERIAL_REQUIREMENT"])
        self.assertEqual(list(tol.DatumReferences), ["A", "B(MAXIMUM_MATERIAL_REQUIREMENT)", "C"])
        self.assertEqual([d.Letter for d in tol.Datums], ["A", "B", "C"])
        self.assertEqual([d.Position for d in tol.Datums], [1, 2, 3])
        self.assertEqual(list(tol.Datums[1].Modifiers), ["MAXIMUM_MATERIAL_REQUIREMENT"])
        self.assertEqual([[tuple(s) for _, s in d.References] for d in tol.Datums], [[("Face5",)], [("Face1",)], [("Face3",)]])
        with self.assertRaises(ValueError):
            PA.addTolerance("Position", 0.3, (self.cyl, ["Face1"]), datums=["A", "B", "C", "D"])

    # check 32, ruling 3: a datum use belongs to one frame
    def test_datums_are_per_use(self):
        PA.addDatum("A", (self.box, ["Face5"]))
        PA.addDatum("B", (self.box, ["Face1"]))
        t1 = PA.addTolerance("Perpendicularity", 0.1, (self.box, ["Face6"]), datums=["A"])
        t2 = PA.addTolerance("Position", 0.2, (self.cyl, ["Face1"]), datums=["A", "B"])
        t3 = PA.addTolerance("Parallelism", 0.1, (self.box, ["Face2"]), datums=["A"])
        uses = [d for t in (t1, t2, t3) for d in t.Datums]
        self.assertEqual(len(uses), 4)
        self.assertEqual(len({d.Name for d in uses}), 4, "a datum use is shared between frames")
        frames = [o for o in self.pmi_objects() if o.TypeId == "Part::PmiTolerance"]
        for d in uses:
            self.assertEqual(len([f for f in frames if d in f.Datums]), 1)
        a_uses = [d for d in uses if d.Letter == "A"]
        self.assertTrue(all([(o.Name, list(s)) for o, s in d.References] == [("Box", ["Face5"])] for d in a_uses))
        self.assertEqual([d.Position for d in t2.Datums], [1, 2])

    def test_picks_validated(self):
        n = len(self.doc.Objects)
        for bad in ((self.box, ["Edge1"]), (self.box, ["Face99"]), (self.box, []), (self.box, ["Face0"]), "Box.Face1"):
            with self.assertRaises(ValueError):
                PA.addSizeDimension(bad, value=1.0)
        self.assertEqual(len(self.doc.Objects), n)

    # check 28: one undo transaction per call
    def test_undo_redo(self):
        PA.addDatum("A", (self.box, ["Face5"]))
        before = [o.Name for o in self.doc.Objects]
        t = PA.addTolerance("Position", 0.3, (self.cyl, ["Face1"]), datums=["A", "A"])
        after = [o.Name for o in self.doc.Objects]
        self.assertEqual(len(after) - len(before), 3)  # the frame and its two datum uses
        self.doc.undo()
        self.assertEqual([o.Name for o in self.doc.Objects], before)
        self.doc.redo()
        self.assertEqual([o.Name for o in self.doc.Objects], after)
        t = self.doc.getObject(t.Name)
        self.assertEqual([d.Position for d in t.Datums], [1, 2])
        self.assertEqual(len(self.doc.getObject(t.Name).Datums), 2)

    def test_undo_removes_group_made_by_the_call(self):
        self.assertFalse([o for o in self.doc.Objects if o.TypeId == "Part::PmiGroup"])
        PA.addSizeDimension((self.cyl, ["Face1"]))
        self.doc.undo()
        self.assertFalse(self.pmi_objects())
        self.assertFalse([o for o in self.doc.Objects if o.TypeId == "Part::PmiGroup"])

    def test_failed_call_leaves_nothing(self):
        n = len(self.doc.Objects)
        with self.assertRaises(ValueError):
            PA.addTolerance("Position", "wide", (self.cyl, ["Face1"]))
        self.assertEqual(len(self.doc.Objects), n)


class TestPmiAuthoringDerivation(unittest.TestCase):
    # check 29, synthetic part
    def _doc(self, name):
        doc = App.newDocument(name)
        self.addCleanup(App.closeDocument, doc.Name)
        return doc

    def test_box_and_cylinder_mm_and_inch(self):
        for unit, (l, w, h, r) in (("mm", (80.0, 50.0, 30.0, 7.5)), ("in", (3.0, 2.0, 1.25, 0.375))):
            doc = self._doc("Derive")
            box = doc.addObject("Part::Box", "B")
            box.Length, box.Width, box.Height = Q(l, unit), Q(w, unit), Q(h, unit)
            cyl = doc.addObject("Part::Cylinder", "C")
            cyl.Radius, cyl.Height = Q(r, unit), Q(h, unit)
            doc.recompute()
            f = 25.4 if unit == "in" else 1.0
            d = PA.addSizeDimension((cyl, ["Face1"]))
            self.assertEqual(d.PmiType, "Size_Diameter")
            self.assertLess(abs(d.Value - 2 * r * f), 1e-9 * 2 * r * f)
            for faces, want in ((("Face1", "Face2"), l), (("Face3", "Face4"), w), (("Face5", "Face6"), h)):
                th = PA.addSizeDimension((box, list(faces)))
                self.assertEqual(th.PmiType, "Size_Thickness")
                self.assertLess(abs(th.Value - want * f), 1e-9 * want * f, unit)
                loc = PA.addLocationDimension((box, [faces[0]]), (box, [faces[1]]))
                self.assertLess(abs(loc.Value - want * f), 1e-9 * want * f, unit)
            rad = PA.addSizeDimension((cyl, ["Face1"]), pmi_type="Size_Radius")
            self.assertLess(abs(rad.Value - r * f), 1e-9 * r * f)

    def test_wedge_angle(self):
        doc = self._doc("Wedge")
        for deg in (25.0, 40.0, 75.0):
            a = math.radians(deg)
            w = _prism(doc, "W", [(0, 0, 0), (30, 0, 0), (30 * math.cos(a), 30 * math.sin(a), 0)])
            # the two side faces that meet at the origin: one lies on y=0, the other is the slanted one through the origin
            def side(f):
                return f.Surface.TypeId == "Part::GeomPlane" and abs(f.normalAt(0, 0).z) < 1e-9

            planes = [("Face%d" % i, f) for i, f in enumerate(w.Shape.Faces, start=1) if side(f)]
            on_x = [n for n, f in planes if abs(f.CenterOfMass.y) < 1e-9]
            slant = [n for n, f in planes if abs(f.CenterOfMass.y) > 1e-9 and f.CenterOfMass.x < 20 and abs(f.CenterOfMass.y / f.CenterOfMass.x - math.tan(a)) < 1e-9]
            self.assertEqual((len(on_x), len(slant)), (1, 1))
            ang = PA.addAngularDimension((w, on_x), (w, slant))
            self.assertLess(abs(ang.Value - deg), 1e-9 * deg, deg)

    def test_not_derivable_raises_and_creates_nothing(self):
        doc = self._doc("NoDerive")
        box = doc.addObject("Part::Box", "B")
        cone = doc.addObject("Part::Cone", "K")
        cone.Radius1, cone.Radius2, cone.Height = 5.0, 2.0, 10.0
        doc.recompute()
        n = len(doc.Objects)
        for call in (
            lambda: PA.addSizeDimension((cone, ["Face1"])),
            lambda: PA.addSizeDimension((box, ["Face1", "Face3"])),  # two planes that are not parallel
            lambda: PA.addSizeDimension((box, ["Face1", "Face2", "Face3"])),
            lambda: PA.addLocationDimension((box, ["Face1"]), (box, ["Face3"])),
            lambda: PA.addAngularDimension((box, ["Face1"]), (cone, ["Face1"])),
        ):
            with self.assertRaises(ValueError):
                call()
        self.assertEqual(len(doc.Objects), n)
        self.assertFalse([o for o in doc.Objects if o.TypeId.startswith("Part::Pmi")])
        # an explicit value is the way out
        self.assertEqual(PA.addSizeDimension((cone, ["Face1"]), value=7.0).Value, 7.0)

    def test_value_units(self):
        doc = self._doc("Units")
        box = doc.addObject("Part::Box", "B")
        doc.recompute()
        d = PA.addSizeDimension((box, ["Face1"]), value=Q("0.5 in"), pmi_type="Size_Thickness")
        self.assertAlmostEqual(d.Value, 12.7, places=9)
        a = PA.addAngularDimension((box, ["Face1"]), (box, ["Face3"]), value=Q("45 deg"))
        self.assertAlmostEqual(a.Value, 45.0, places=9)


# ----------------------------------------------------------------------------------------------------------
# rules (check 33): one scenario that fires and one that does not, per rule
# ----------------------------------------------------------------------------------------------------------

SCENARIOS = {}


def scenario(rule):
    def deco(fn):
        SCENARIOS[rule] = fn
        return fn

    return deco


@scenario("form-no-datum")
def _(t):
    PA.addDatum("A", (t.box, ["Face5"]))
    return (
        PA.addTolerance("Flatness", 0.1, (t.box, ["Face6"]), datums=["A"]),
        PA.addTolerance("Flatness", 0.1, (t.box, ["Face6"])),
    )


@scenario("datum-needed")
def _(t):
    PA.addDatum("A", (t.box, ["Face5"]))
    return (
        PA.addTolerance("Perpendicularity", 0.1, (t.box, ["Face6"])),
        PA.addTolerance("Perpendicularity", 0.1, (t.box, ["Face6"]), datums=["A"]),
    )


@scenario("material-needs-fos")
def _(t):
    return (
        PA.addTolerance("Position", 0.1, (t.box, ["Face6"]), modifiers=["MAXIMUM_MATERIAL_REQUIREMENT"]),
        PA.addTolerance("Position", 0.1, (t.cyl, ["Face1"]), modifiers=["MAXIMUM_MATERIAL_REQUIREMENT"]),
    )


@scenario("diameter-zone-feature")
def _(t):
    return (
        PA.addTolerance("Position", 0.1, (t.box, ["Face6"]), zone_shape="Diameter"),
        PA.addTolerance("Position", 0.1, (t.cyl, ["Face1"]), zone_shape="Diameter"),
    )


@scenario("value-positive")
def _(t):
    return (
        PA.addTolerance("Flatness", 0.0, (t.box, ["Face6"])),
        PA.addTolerance("Flatness", 0.1, (t.box, ["Face6"])),
    )


@scenario("bounds-ordered")
def _(t):
    return (
        PA.addSizeDimension((t.cyl, ["Face1"]), plus_minus=(-0.2, 0.1)),
        PA.addSizeDimension((t.cyl, ["Face1"]), plus_minus=(0.1, -0.2)),
    )


@scenario("letters-excluded")
def _(t):
    return PA.addDatum("Q", (t.box, ["Face5"])), PA.addDatum("A", (t.box, ["Face5"]))


@scenario("letter-repeat")
def _(t):
    PA.addDatum("A", (t.box, ["Face5"]))
    PA.addDatum("B", (t.box, ["Face1"]))
    return (
        PA.addTolerance("Position", 0.1, (t.cyl, ["Face1"]), datums=["A", "A"]),
        PA.addTolerance("Position", 0.1, (t.cyl, ["Face1"]), datums=["A", "B"]),
    )


@scenario("letter-exists")
def _(t):
    PA.addDatum("A", (t.box, ["Face5"]))
    return (
        PA.addTolerance("Perpendicularity", 0.1, (t.box, ["Face6"]), datums=["Z"]),
        PA.addTolerance("Perpendicularity", 0.1, (t.box, ["Face6"]), datums=["A"]),
    )


@scenario("nominal-agrees")
def _(t):
    return (
        PA.addSizeDimension((t.cyl, ["Face1"]), value=11.0),
        PA.addSizeDimension((t.cyl, ["Face1"]), value=10.0),
    )


@scenario("compartment-order")
def _(t):
    PA.addDatum("A", (t.box, ["Face5"]))
    PA.addDatum("B", (t.box, ["Face1"]))
    bad = PA.addTolerance("Position", 0.1, (t.cyl, ["Face1"]), datums=["A", "B"])
    ok = PA.addTolerance("Position", 0.1, (t.cyl, ["Face1"]), datums=["A", "B"])
    bad.Datums = list(reversed(bad.Datums))
    return bad, ok


@scenario("max-compartments")
def _(t):
    for letter, face in (("A", "Face5"), ("B", "Face1"), ("C", "Face3")):
        PA.addDatum(letter, (t.box, [face]))
    bad = PA.addTolerance("Position", 0.1, (t.cyl, ["Face1"]), datums=["A", "B", "C"])
    ok = PA.addTolerance("Position", 0.1, (t.cyl, ["Face1"]), datums=["A", "B", "C"])
    fourth = t.doc.addObject("Part::PmiDatum", "PmiDatum")
    fourth.Letter, fourth.Position = "D", 4
    bad.Datums = list(bad.Datums) + [fourth]
    bad.DatumReferences = ["A", "B", "C", "D"]
    return bad, ok



@scenario("round-form")
def _(t):
    return (
        PA.addTolerance("Cylindricity", 0.1, (t.box, ["Face6"])),
        PA.addTolerance("Cylindricity", 0.1, (t.cyl, ["Face1"])),
    )


@scenario("projected-height")
def _(t):
    return (
        PA.addTolerance("Position", 0.1, (t.cyl, ["Face1"]), zone_modifier="Projected", zone_modifier_value=0.0),
        PA.addTolerance("Position", 0.1, (t.cyl, ["Face1"]), zone_modifier="Projected", zone_modifier_value=5.0),
    )


class TestPmiRules(PmiAuthoringBase):
    def test_table(self):
        rules = PA.loadRules()
        self.assertGreaterEqual(len(rules), 10)
        ids = [r["id"] for r in rules]
        self.assertEqual(len(ids), len(set(ids)))
        for r in rules:
            self.assertIn(r["severity"], ("error", "warning"))
            self.assertGreater(len(r["message"]), 20)
            self.assertTrue(r["citations"])
            for c in r["citations"]:
                self.assertRegex(c["standard"], r"^(ASME Y14\.5-2018|ISO 1101:2017|ISO 5459:2011)$")
                self.assertTrue(c["clause"])
        self.assertEqual(set(ids), set(SCENARIOS), "every rule needs a firing and a quiet scenario")
        self.assertGreaterEqual(len({c["standard"] for r in rules for c in r["citations"]}), 3)

    def test_rule_without_check_is_refused(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "r.json")
        with open(p, "w") as f:
            json.dump({"rules": [{"id": "no-such-check", "severity": "warning", "message": "x" * 30, "citations": []}]}, f)
        with self.assertRaises(RuntimeError):
            PA.loadRules(p)
        os.remove(p)
        os.rmdir(d)

    def test_warnings_stored_and_never_refuse(self):
        t = PA.addTolerance("Flatness", -1.0, (self.box, ["Face6"]), datums=["Z"])
        self.assertEqual(t.TypeId, "Part::PmiTolerance")
        ids = sorted(r.split(":")[0] for r in t.RuleWarnings)
        self.assertEqual(ids, ["form-no-datum", "letter-exists", "value-positive"])
        self.assertEqual(sorted(i for n, i, _ in PA.checkPmi(self.doc) if n == t.Name), ids)

    def test_check_pmi_returns_and_clears(self):
        t = PA.addTolerance("Flatness", 0.0, (self.box, ["Face6"]))
        self.assertTrue(t.RuleWarnings)
        t.Value = 0.1
        self.assertEqual(PA.checkPmi(self.doc), [])
        self.assertEqual(list(t.RuleWarnings), [])

    def test_zero_at_material_condition_and_common_datums_are_legal(self):
        # found on the NIST models: a zero position tolerance at MMC, and two datums sharing a compartment (a common datum)
        PA.addDatum("A", (self.box, ["Face5"]))
        PA.addDatum("B", (self.box, ["Face1"]))
        zero = PA.addTolerance("Position", 0.0, (self.cyl, ["Face1"]), modifiers=["MAXIMUM_MATERIAL_REQUIREMENT"])
        self.assertNotIn("value-positive", self.rules_of(zero))
        self.assertIn("value-positive", self.rules_of(PA.addTolerance("Position", -0.1, (self.cyl, ["Face1"]), modifiers=["MAXIMUM_MATERIAL_REQUIREMENT"])))
        t = PA.addTolerance("CircularRunout", 0.1, (self.cyl, ["Face1"]), datums=["A", "B"])
        t.Datums[1].Position = 1
        self.assertEqual(self.rules_of(t), [])
        t.Datums[1].Position = 3
        self.assertIn("compartment-order", self.rules_of(t))

    def test_nominal_follows_the_geometry(self):
        d = PA.addSizeDimension((self.cyl, ["Face1"]))
        self.assertEqual(PA.checkPmi(self.doc), [])
        self.cyl.Radius = 6.0
        self.doc.recompute()
        got = PA.checkPmi(self.doc)
        self.assertEqual([(n, r) for n, r, _ in got], [(d.Name, "nominal-agrees")])

    def test_save_reopen_keeps_warnings(self):
        t = PA.addTolerance("Flatness", 0.0, (self.box, ["Face6"]))
        warns, name = list(t.RuleWarnings), t.Name
        self.assertTrue(warns)
        path = os.path.join(tempfile.mkdtemp(), "w.FCStd")
        self.doc.saveAs(path)
        App.closeDocument(self.doc.Name)
        self.doc = App.openDocument(path)
        again = self.doc.getObject(name)
        self.assertEqual(list(again.RuleWarnings), warns)
        self.assertEqual([(o.Name, list(s)) for o, s in again.References], [("Box", ["Face6"])])
        os.remove(path)
        os.rmdir(os.path.dirname(path))

    def test_new_properties_default_empty(self):
        t = PA.addTolerance("Flatness", 0.1, (self.box, ["Face6"]))
        self.assertEqual(list(t.RuleWarnings), [])
        self.assertEqual(list(t.SecondSide), [])


def _make_rule_tests():
    for rule in SCENARIOS:

        def fires(self, rule=rule):
            fire, _ = SCENARIOS[rule](self)
            self.assertIn(rule, self.rules_of(fire), "%s did not fire" % rule)

        def quiet(self, rule=rule):
            _, ok = SCENARIOS[rule](self)
            self.assertNotIn(rule, self.rules_of(ok), "%s fired on a clean item" % rule)

        name = rule.replace("-", "_")
        setattr(TestPmiRules, "test_rule_%s_fires" % name, fires)
        setattr(TestPmiRules, "test_rule_%s_quiet" % name, quiet)


_make_rule_tests()


# ----------------------------------------------------------------------------------------------------------
# export: authored items leave as AP242 semantic PMI
# ----------------------------------------------------------------------------------------------------------


class TestPmiAuthoringExport(PmiAuthoringBase):
    PREF = "User parameter:BaseApp/Preferences/Mod/Part/STEP"

    def setUp(self):
        super().setUp()
        import Import

        self.Import = Import
        self.dir = tempfile.mkdtemp(prefix="g12auth")
        grp = App.ParamGet(self.PREF)
        self._saved = (grp.GetBool("WritePmi", True), grp.GetString("Scheme"))
        grp.SetBool("WritePmi", True)
        self.reimported = []

    def tearDown(self):
        grp = App.ParamGet(self.PREF)
        grp.SetBool("WritePmi", self._saved[0])
        grp.SetString("Scheme", self._saved[1])
        for d in self.reimported:
            App.closeDocument(d.Name)
        for f in os.listdir(self.dir):
            os.remove(os.path.join(self.dir, f))
        os.rmdir(self.dir)
        super().tearDown()

    def _round_trip(self):
        path = os.path.join(self.dir, "a.stp")
        self.Import.export([self.box, self.cyl], path)
        doc = App.newDocument("Back")
        self.reimported.append(doc)
        self.Import.insert(path, doc.Name)
        doc.recompute()
        return doc, path

    @staticmethod
    def _of(doc, tid):
        return [o for o in doc.Objects if o.TypeId == tid]

    def test_authored_items_export_and_come_back(self):
        PA.addDatum("A", (self.box, ["Face5"]))
        PA.addDatum("B", (self.box, ["Face1"]))
        PA.addSizeDimension((self.cyl, ["Face1"]), plus_minus=(0.1, -0.2))
        PA.addSizeDimension((self.cyl, ["Face1"]), limits=(10.1, 9.8))
        PA.addLocationDimension((self.box, ["Face1"]), (self.box, ["Face2"]), plus_minus=(0.5, -0.5))
        PA.addAngularDimension((self.box, ["Face1"]), (self.box, ["Face3"]), plus_minus=(0.5, -0.5))
        PA.addTolerance("Flatness", 0.05, (self.box, ["Face6"]))
        PA.addTolerance("Perpendicularity", 0.1, (self.box, ["Face2"]), datums=["A"])
        PA.addTolerance(
            "Position",
            0.3,
            (self.cyl, ["Face1"]),
            datums=["A", ("B", ["MAXIMUM_MATERIAL_REQUIREMENT"])],
            zone_shape="Diameter",
            modifiers=["MAXIMUM_MATERIAL_REQUIREMENT"],
        )
        back, path = self._round_trip()
        rep = self.Import.lastStepPmiExportReport()
        self.assertEqual(rep["skipped"], 0, rep["warnings"])
        self.assertFalse(rep["fell_back"])
        dims = self._of(back, "Part::PmiDimension")
        tols = self._of(back, "Part::PmiTolerance")
        self.assertEqual((len(dims), len(tols)), (4, 3))
        self.assertEqual(
            sorted(d.PmiType for d in dims),
            ["Location_Angular", "Location_LinearDistance", "Size_Diameter", "Size_Diameter"],
        )
        self.assertEqual(sorted(round(d.Value, 6) for d in dims), [10.0, 10.0, 90.0, 100.0])
        loc = [d for d in dims if d.PmiType == "Location_LinearDistance"][0]
        self.assertAlmostEqual(loc.UpperBound, 0.5)
        self.assertAlmostEqual(loc.LowerBound, -0.5)
        pos = [t for t in tols if t.PmiType == "Position"][0]
        self.assertEqual(list(pos.DatumReferences), ["A", "B(MAXIMUM_MATERIAL_REQUIREMENT)"])
        self.assertEqual(pos.ZoneShape, "Diameter")
        self.assertIn("MAXIMUM_MATERIAL_REQUIREMENT", list(pos.Modifiers))
        self.assertEqual(sorted(t.PmiType for t in tols), ["Flatness", "Perpendicularity", "Position"])

    def test_range_nominal_is_written_as_given(self):
        # OCCT's writer states the middle of the limits as the nominal; the file must carry the nominal the item holds
        PA.addSizeDimension((self.cyl, ["Face1"]), limits=(10.1, 9.8))
        PA.addLocationDimension((self.box, ["Face1"]), (self.box, ["Face2"]), limits=(100.3, 99.9))
        back, path = self._round_trip()
        self.assertEqual(sorted(round(d.Value, 6) for d in self._of(back, "Part::PmiDimension")), [10.0, 100.0])

    def test_two_frames_naming_one_letter_do_not_corrupt_each_other(self):
        # the trap of run 076: a datum shared by letter between frames corrupts the written frames
        PA.addDatum("A", (self.box, ["Face5"]))
        PA.addDatum("B", (self.box, ["Face1"]))
        PA.addTolerance("Perpendicularity", 0.1, (self.box, ["Face6"]), datums=["A"])
        PA.addTolerance("Position", 0.3, (self.cyl, ["Face1"]), datums=["A", "B"])
        PA.addTolerance("Parallelism", 0.2, (self.box, ["Face2"]), datums=["B", "A"])
        back, path = self._round_trip()
        got = sorted((t.PmiType, tuple(t.DatumReferences)) for t in self._of(back, "Part::PmiTolerance"))
        self.assertEqual(got, [("Parallelism", ("B", "A")), ("Perpendicularity", ("A",)), ("Position", ("A", "B"))])

    def test_rule_warnings_do_not_change_the_export(self):
        t = PA.addTolerance("Flatness", 0.05, (self.box, ["Face6"]))
        path1 = os.path.join(self.dir, "c.stp")
        self.Import.export([self.box, self.cyl], path1)
        t.RuleWarnings = ["value-positive: made up"]
        path2 = os.path.join(self.dir, "d.stp")
        self.Import.export([self.box, self.cyl], path2)

        def body(p):
            t = re.sub(r"NEXT_ASSEMBLY_USAGE_OCCURRENCE\('\d+'", "NEXT_ASSEMBLY_USAGE_OCCURRENCE('n'", open(p).read())
            return [l for l in t.splitlines() if not l.startswith("FILE_NAME")]

        self.assertEqual(body(path1), body(path2))

    def test_export_never_fails_on_odd_items(self):
        PA.addTolerance("Flatness", 0.05, (self.box, ["Face6"]))
        t = PA.addTolerance("Flatness", 0.05, (self.box, ["Face1"]))
        t.References = []  # governs nothing
        PA.addSizeDimension((self.cyl, ["Face1"]))
        self.cyl.Radius = 6.0
        self.doc.recompute()
        path = os.path.join(self.dir, "odd.stp")
        self.Import.export([self.box, self.cyl], path)
        self.assertGreater(os.path.getsize(path), 1000)
        self.assertGreaterEqual(self.Import.lastStepPmiExportReport()["skipped"], 1)

    def test_authored_items_are_semantic_only(self):
        PA.addSizeDimension((self.cyl, ["Face1"]))
        back, path = self._round_trip()
        text = open(path).read()
        self.assertNotIn("TESSELLATED_ANNOTATION_OCCURRENCE", text)
        self.assertIn("DIMENSIONAL_SIZE", text)

    def test_identical_frames_share_one_datum_system_in_the_file(self):
        PA.addDatum("A", (self.box, ["Face5"]))
        PA.addTolerance("Perpendicularity", 0.1, (self.box, ["Face6"]), datums=["A"])
        PA.addTolerance("Parallelism", 0.1, (self.box, ["Face2"]), datums=["A"])
        back, path = self._round_trip()
        text = open(path).read()
        self.assertEqual(text.count("DATUM_SYSTEM("), 1)
        tols = self._of(back, "Part::PmiTolerance")
        self.assertEqual(sorted(tuple(t.DatumReferences) for t in tols), [("A",), ("A",)])


# ----------------------------------------------------------------------------------------------------------
# check 35: PMI on a parametric body survives edits (G01 element map)
# ----------------------------------------------------------------------------------------------------------


class TestPmiAuthoringPartDesign(unittest.TestCase):
    def setUp(self):
        import Sketcher

        self.Sketcher = Sketcher
        self.doc = App.newDocument("PmiPD")
        doc = self.doc
        body = self.body = doc.addObject("PartDesign::Body", "Body")
        xy = [o for o in body.Origin.OutList if o.Role == "XY_Plane"][0]
        sk = body.newObject("Sketcher::SketchObject", "Sketch")
        sk.AttachmentSupport = [(xy, "")]
        sk.MapMode = "FlatFace"
        pts = [App.Vector(0, 0, 0), App.Vector(60, 0, 0), App.Vector(60, 40, 0), App.Vector(0, 40, 0)]
        for i in range(4):
            sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]))
        self.pad = body.newObject("PartDesign::Pad", "Pad")
        self.pad.Profile = sk
        self.pad.Length = 20
        doc.recompute()
        self.sk2 = body.newObject("Sketcher::SketchObject", "Sketch2")
        self.sk2.AttachmentSupport = [(xy, "")]
        self.sk2.MapMode = "FlatFace"
        self.sk2.AttachmentOffset = App.Placement(App.Vector(0, 0, 20), App.Rotation())
        self.sk2.addGeometry(Part.Circle(App.Vector(30, 20, 0), App.Vector(0, 0, 1), 4.0))
        self.sk2.addConstraint(Sketcher.Constraint("Radius", 0, 4.0))
        self.pocket = body.newObject("PartDesign::Pocket", "Pocket")
        self.pocket.Profile = self.sk2
        self.pocket.Type = 1
        doc.recompute()
        cyl = self._hole_face()
        bottom = [
            "Face%d" % i
            for i, f in enumerate(body.Shape.Faces, 1)
            if f.Surface.TypeId == "Part::GeomPlane" and abs(f.CenterOfMass.z) < 1e-9
        ][0]
        PA.addDatum("A", (body, [bottom]))
        self.size = PA.addSizeDimension((body, [cyl]))
        self.pos = PA.addTolerance("Position", 0.2, (body, [cyl]), datums=["A"], zone_shape="Diameter")
        self.size_name, self.pos_name = self.size.Name, self.pos.Name
        self.assertAlmostEqual(self.size.Value, 8.0, places=9)

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def _hole_face(self, body=None):
        body = body or self.body
        hits = [
            "Face%d" % i
            for i, f in enumerate(body.Shape.Faces, 1)
            if f.Surface.TypeId == "Part::GeomCylinder"
            and abs(f.Surface.Radius - self.radius) < 1e-9
            and abs(f.Surface.Center.x - 30) < 1e-9
            and abs(f.Surface.Center.y - 20) < 1e-9
        ]
        self.assertEqual(len(hits), 1)
        return hits[0]

    radius = 4.0

    def _assert_same_faces(self, doc, label):
        for name in (self.size_name, self.pos_name):
            item = doc.getObject(name)
            (obj, subs), = [(o, list(s)) for o, s in item.References]
            self.assertEqual(len(subs), 1, label)
            f = obj.Shape.getElement(subs[0])
            self.assertEqual(f.Surface.TypeId, "Part::GeomCylinder", "%s: %s retargeted" % (label, name))
            self.assertAlmostEqual(f.Surface.Radius, self.radius, places=9)
            self.assertAlmostEqual(f.Surface.Center.x, 30, places=9)
            self.assertAlmostEqual(f.Surface.Center.y, 20, places=9)
        datum_use = doc.getObject(self.pos_name).Datums[0]
        (obj, subs), = [(o, list(s)) for o, s in datum_use.References]
        f = obj.Shape.getElement(subs[0])
        self.assertEqual(f.Surface.TypeId, "Part::GeomPlane", label)
        self.assertAlmostEqual(f.CenterOfMass.z, 0.0, places=9)

    def test_edits_and_reopen(self):
        self._assert_same_faces(self.doc, "initial")
        self.assertEqual(PA.checkPmi(self.doc), [])
        # 1. the pad gets longer
        self.pad.Length = 30
        self.doc.recompute()
        self._assert_same_faces(self.doc, "pad length")
        self.assertEqual(PA.checkPmi(self.doc), [])
        # 2. a feature inserted before the hole renumbers the faces
        before = self.size.References[0][1][0]
        edge = [i + 1 for i, e in enumerate(self.pad.Shape.Edges) if abs(e.CenterOfMass.z - 30) < 1e-9][0]
        ch = self.doc.addObject("PartDesign::Chamfer", "Chamfer")
        ch.Base = (self.pad, ["Edge%d" % edge])
        ch.Size = 2.0
        self.body.insertObject(ch, self.pad, True)
        self.doc.recompute()
        self.assertTrue(ch.isValid() and self.pocket.isValid())
        self.assertEqual(self.body.Group.index(ch) < self.body.Group.index(self.pocket), True)
        self._assert_same_faces(self.doc, "inserted feature")
        self.assertEqual(PA.checkPmi(self.doc), [])
        # 3. save and reopen
        path = os.path.join(tempfile.mkdtemp(), "pd.FCStd")
        self.doc.saveAs(path)
        name = self.doc.Name
        App.closeDocument(name)
        self.doc = App.openDocument(path)
        self._assert_same_faces(self.doc, "reopen")
        self.assertEqual(PA.checkPmi(self.doc), [])
        self.doc.getObject("Pad").Length = 35
        self.doc.recompute()
        self._assert_same_faces(self.doc, "edit after reopen")
        os.remove(path)
        os.rmdir(os.path.dirname(path))

    def test_hole_diameter_change_trips_the_nominal_rule(self):
        self.assertEqual(PA.checkPmi(self.doc), [])
        self.sk2.setDatum(0, App.Units.Quantity("5 mm"))
        self.doc.recompute()
        self.radius = 5.0
        got = PA.checkPmi(self.doc)
        self.assertEqual([(n, r) for n, r, _ in got], [(self.size_name, "nominal-agrees")])
        self.assertEqual(list(self.pos.RuleWarnings), [])
        self._assert_same_faces(self.doc, "diameter change")
