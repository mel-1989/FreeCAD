# SPDX-License-Identifier: LGPL-2.1-or-later

# **************************************************************************
#   Tests for the semantic PMI import of STEP AP242 files (G12 slice 1b-i).  *
#   Behaviour follows the CAx-IF Recommended Practices for the               *
#   Representation and Presentation of PMI (AP242), ASME Y14.5 and ISO 1101. *
#   The fixture below is a cylinder (radius 5, height 20) with one size      *
#   dimension with deviations, one location dimension, one perpendicularity  *
#   tolerance with a material requirement modifier and datum A, written by   *
#   OCCT's documented XCAF API (STEPCAFControl_Writer) from a throwaway      *
#   program; it contains no third-party data.                                *
# **************************************************************************

import math
import os
import tempfile
import unittest

import FreeCAD as App
import Import
import Part

FIXTURE = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('Open CASCADE Model'),'2;1');
FILE_NAME('Open CASCADE Shape Model','2026-11-08T06:25:55',('Author'),(
    'Open CASCADE'),'Open CASCADE STEP processor 8.0','Open CASCADE 8.0'
  ,'Unknown');
FILE_SCHEMA((
'AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF {1 0 10303 442 1 1 4 }'
    ));
ENDSEC;
DATA;
#1 = APPLICATION_PROTOCOL_DEFINITION('international standard',
  'ap242_managed_model_based_3d_engineering',2013,#2);
#2 = APPLICATION_CONTEXT('Managed model based 3d engineering');
#3 = SHAPE_DEFINITION_REPRESENTATION(#4,#10);
#4 = PRODUCT_DEFINITION_SHAPE('','',#5);
#5 = PRODUCT_DEFINITION('design','',#6,#9);
#6 = PRODUCT_DEFINITION_FORMATION('','',#7);
#7 = PRODUCT('SOLID','SOLID','',(#8));
#8 = PRODUCT_CONTEXT('',#2,'mechanical');
#9 = PRODUCT_DEFINITION_CONTEXT('part definition',#2,'design');
#10 = ADVANCED_BREP_SHAPE_REPRESENTATION('',(#11,#15),#113);
#11 = AXIS2_PLACEMENT_3D('',#12,#13,#14);
#12 = CARTESIAN_POINT('',(0.,0.,0.));
#13 = DIRECTION('',(0.,0.,1.));
#14 = DIRECTION('',(1.,0.,-0.));
#15 = MANIFOLD_SOLID_BREP('',#16);
#16 = CLOSED_SHELL('',(#17,#105,#109));
#17 = ADVANCED_FACE('',(#18),#31,.T.);
#18 = FACE_BOUND('',#19,.T.);
#19 = EDGE_LOOP('',(#20,#54,#77,#104));
#20 = ORIENTED_EDGE('',*,*,#21,.F.);
#21 = EDGE_CURVE('',#22,#22,#24,.T.);
#22 = VERTEX_POINT('',#23);
#23 = CARTESIAN_POINT('',(5.,-1.224646799147E-15,20.));
#24 = SURFACE_CURVE('',#25,(#30,#42),.PCURVE_S1.);
#25 = CIRCLE('',#26,5.);
#26 = AXIS2_PLACEMENT_3D('',#27,#28,#29);
#27 = CARTESIAN_POINT('',(0.,0.,20.));
#28 = DIRECTION('',(0.,0.,1.));
#29 = DIRECTION('',(1.,0.,-0.));
#30 = PCURVE('',#31,#36);
#31 = CYLINDRICAL_SURFACE('',#32,5.);
#32 = AXIS2_PLACEMENT_3D('',#33,#34,#35);
#33 = CARTESIAN_POINT('',(0.,0.,0.));
#34 = DIRECTION('',(0.,0.,1.));
#35 = DIRECTION('',(1.,0.,-0.));
#36 = DEFINITIONAL_REPRESENTATION('',(#37),#41);
#37 = LINE('',#38,#39);
#38 = CARTESIAN_POINT('',(0.,20.));
#39 = VECTOR('',#40,1.);
#40 = DIRECTION('',(1.,0.));
#41 = ( GEOMETRIC_REPRESENTATION_CONTEXT(2) 
PARAMETRIC_REPRESENTATION_CONTEXT() REPRESENTATION_CONTEXT('2D SPACE',''
  ) );
#42 = PCURVE('',#43,#48);
#43 = PLANE('',#44);
#44 = AXIS2_PLACEMENT_3D('',#45,#46,#47);
#45 = CARTESIAN_POINT('',(0.,0.,20.));
#46 = DIRECTION('',(0.,0.,1.));
#47 = DIRECTION('',(1.,0.,-0.));
#48 = DEFINITIONAL_REPRESENTATION('',(#49),#53);
#49 = CIRCLE('',#50,5.);
#50 = AXIS2_PLACEMENT_2D('',#51,#52);
#51 = CARTESIAN_POINT('',(0.,0.));
#52 = DIRECTION('',(1.,0.));
#53 = ( GEOMETRIC_REPRESENTATION_CONTEXT(2) 
PARAMETRIC_REPRESENTATION_CONTEXT() REPRESENTATION_CONTEXT('2D SPACE',''
  ) );
#54 = ORIENTED_EDGE('',*,*,#55,.F.);
#55 = EDGE_CURVE('',#56,#22,#58,.T.);
#56 = VERTEX_POINT('',#57);
#57 = CARTESIAN_POINT('',(5.,-1.224646799147E-15,0.));
#58 = SEAM_CURVE('',#59,(#63,#70),.PCURVE_S1.);
#59 = LINE('',#60,#61);
#60 = CARTESIAN_POINT('',(5.,-1.224646799147E-15,0.));
#61 = VECTOR('',#62,1.);
#62 = DIRECTION('',(0.,0.,1.));
#63 = PCURVE('',#31,#64);
#64 = DEFINITIONAL_REPRESENTATION('',(#65),#69);
#65 = LINE('',#66,#67);
#66 = CARTESIAN_POINT('',(0.,-0.));
#67 = VECTOR('',#68,1.);
#68 = DIRECTION('',(0.,1.));
#69 = ( GEOMETRIC_REPRESENTATION_CONTEXT(2) 
PARAMETRIC_REPRESENTATION_CONTEXT() REPRESENTATION_CONTEXT('2D SPACE',''
  ) );
#70 = PCURVE('',#31,#71);
#71 = DEFINITIONAL_REPRESENTATION('',(#72),#76);
#72 = LINE('',#73,#74);
#73 = CARTESIAN_POINT('',(6.28318530718,-0.));
#74 = VECTOR('',#75,1.);
#75 = DIRECTION('',(0.,1.));
#76 = ( GEOMETRIC_REPRESENTATION_CONTEXT(2) 
PARAMETRIC_REPRESENTATION_CONTEXT() REPRESENTATION_CONTEXT('2D SPACE',''
  ) );
#77 = ORIENTED_EDGE('',*,*,#78,.T.);
#78 = EDGE_CURVE('',#56,#56,#79,.T.);
#79 = SURFACE_CURVE('',#80,(#85,#92),.PCURVE_S1.);
#80 = CIRCLE('',#81,5.);
#81 = AXIS2_PLACEMENT_3D('',#82,#83,#84);
#82 = CARTESIAN_POINT('',(0.,0.,0.));
#83 = DIRECTION('',(0.,0.,1.));
#84 = DIRECTION('',(1.,0.,-0.));
#85 = PCURVE('',#31,#86);
#86 = DEFINITIONAL_REPRESENTATION('',(#87),#91);
#87 = LINE('',#88,#89);
#88 = CARTESIAN_POINT('',(0.,0.));
#89 = VECTOR('',#90,1.);
#90 = DIRECTION('',(1.,0.));
#91 = ( GEOMETRIC_REPRESENTATION_CONTEXT(2) 
PARAMETRIC_REPRESENTATION_CONTEXT() REPRESENTATION_CONTEXT('2D SPACE',''
  ) );
#92 = PCURVE('',#93,#98);
#93 = PLANE('',#94);
#94 = AXIS2_PLACEMENT_3D('',#95,#96,#97);
#95 = CARTESIAN_POINT('',(0.,0.,0.));
#96 = DIRECTION('',(0.,0.,1.));
#97 = DIRECTION('',(1.,0.,-0.));
#98 = DEFINITIONAL_REPRESENTATION('',(#99),#103);
#99 = CIRCLE('',#100,5.);
#100 = AXIS2_PLACEMENT_2D('',#101,#102);
#101 = CARTESIAN_POINT('',(0.,0.));
#102 = DIRECTION('',(1.,0.));
#103 = ( GEOMETRIC_REPRESENTATION_CONTEXT(2) 
PARAMETRIC_REPRESENTATION_CONTEXT() REPRESENTATION_CONTEXT('2D SPACE',''
  ) );
#104 = ORIENTED_EDGE('',*,*,#55,.T.);
#105 = ADVANCED_FACE('',(#106),#43,.T.);
#106 = FACE_BOUND('',#107,.T.);
#107 = EDGE_LOOP('',(#108));
#108 = ORIENTED_EDGE('',*,*,#21,.T.);
#109 = ADVANCED_FACE('',(#110),#93,.F.);
#110 = FACE_BOUND('',#111,.T.);
#111 = EDGE_LOOP('',(#112));
#112 = ORIENTED_EDGE('',*,*,#78,.F.);
#113 = ( GEOMETRIC_REPRESENTATION_CONTEXT(3) 
GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT((#117)) GLOBAL_UNIT_ASSIGNED_CONTEXT
((#114,#115,#116)) REPRESENTATION_CONTEXT('Context #1',
  '3D Context with UNIT and UNCERTAINTY') );
#114 = ( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) );
#115 = ( NAMED_UNIT(*) PLANE_ANGLE_UNIT() SI_UNIT($,.RADIAN.) );
#116 = ( NAMED_UNIT(*) SI_UNIT($,.STERADIAN.) SOLID_ANGLE_UNIT() );
#117 = UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-07),#114,
  'distance_accuracy_value','confusion accuracy');
#118 = PRODUCT_RELATED_PRODUCT_CATEGORY('part',$,(#7));
#119 = DATUM_FEATURE('','',#4,.T.);
#120 = GEOMETRIC_ITEM_SPECIFIC_USAGE('','',#119,#10,#109);
#121 = DATUM('','',#4,.F.,'A');
#122 = SHAPE_ASPECT_RELATIONSHIP('',$,#119,#121);
#123 = SHAPE_ASPECT('','',#4,.T.);
#124 = GEOMETRIC_ITEM_SPECIFIC_USAGE('','',#123,#10,#17);
#125 = ( LENGTH_MEASURE_WITH_UNIT() MEASURE_REPRESENTATION_ITEM() 
MEASURE_WITH_UNIT(POSITIVE_LENGTH_MEASURE(10.),#114) REPRESENTATION_ITEM
('nominal value') );
#126 = SHAPE_DIMENSION_REPRESENTATION('',(#125),#113);
#127 = DIMENSIONAL_CHARACTERISTIC_REPRESENTATION(#128,#126);
#128 = DIMENSIONAL_SIZE(#123,'diameter');
#129 = MEASURE_WITH_UNIT(0.1,#114);
#130 = MEASURE_WITH_UNIT(-0.2,#114);
#131 = TOLERANCE_VALUE(#130,#129);
#132 = PLUS_MINUS_TOLERANCE(#131,#128);
#133 = SHAPE_ASPECT('distance','',#4,.T.);
#134 = GEOMETRIC_ITEM_SPECIFIC_USAGE('distance','',#133,#10,#105);
#135 = SHAPE_ASPECT('distance','',#4,.T.);
#136 = GEOMETRIC_ITEM_SPECIFIC_USAGE('distance','',#135,#10,#109);
#137 = ( LENGTH_MEASURE_WITH_UNIT() MEASURE_REPRESENTATION_ITEM() 
MEASURE_WITH_UNIT(POSITIVE_LENGTH_MEASURE(20.),#114) REPRESENTATION_ITEM
('nominal value') );
#138 = SHAPE_DIMENSION_REPRESENTATION('',(#137),#113);
#139 = DIMENSIONAL_CHARACTERISTIC_REPRESENTATION(#140,#138);
#140 = DIMENSIONAL_LOCATION('linear distance',$,#133,#135);
#141 = DATUM_REFERENCE_COMPARTMENT('','',#4,.F.,#121,(
    SIMPLE_DATUM_REFERENCE_MODIFIER(.MAXIMUM_MATERIAL_REQUIREMENT.)));
#142 = DATUM_SYSTEM('','',#4,.F.,(#141));
#143 = LENGTH_MEASURE_WITH_UNIT(LENGTH_MEASURE(5.E-02),#114);
#144 = SHAPE_ASPECT('','',#4,.T.);
#145 = GEOMETRIC_ITEM_SPECIFIC_USAGE('','',#144,#10,#17);
#146 = ( GEOMETRIC_TOLERANCE('','',#143,#144) 
GEOMETRIC_TOLERANCE_WITH_DATUM_REFERENCE((#142)) 
GEOMETRIC_TOLERANCE_WITH_MODIFIERS((.MAXIMUM_MATERIAL_REQUIREMENT.)) 
PERPENDICULARITY_TOLERANCE() );
#147 = TOLERANCE_ZONE_FORM('cylindrical or circular');
#148 = TOLERANCE_ZONE('','',#4,.F.,(#146),#147);
#149 = RUNOUT_ZONE_DEFINITION(#148,());
#150 = RUNOUT_ZONE_ORIENTATION(#151);
#151 = PLANE_ANGLE_MEASURE_WITH_UNIT(PLANE_ANGLE_MEASURE(0.),#115);
ENDSEC;
END-ISO-10303-21;
"""


def _write_fixture(directory):
    path = os.path.join(directory, "pmi_block.stp")
    with open(path, "w") as f:
        f.write(FIXTURE)
    return path


class _Env:
    """Set an environment variable for the pass for one import (the switches are read at call time)."""

    def __init__(self, **kw):
        self.kw = kw
        self.old = {}

    def __enter__(self):
        for k, v in self.kw.items():
            self.old[k] = os.environ.get(k)
            os.environ[k] = v

    def __exit__(self, *a):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestImportStepPmi(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="g12pmi")
        self.path = _write_fixture(self.dir)
        self.docs = []

    def tearDown(self):
        for d in self.docs:
            try:
                App.closeDocument(d.Name)
            except Exception:
                pass
        for f in os.listdir(self.dir):
            os.remove(os.path.join(self.dir, f))
        os.rmdir(self.dir)

    def _insert(self, path=None, **env):
        doc = App.newDocument("G12Pmi")
        self.docs.append(doc)
        with _Env(**env):
            Import.insert(path or self.path, doc.Name)
        return doc

    @staticmethod
    def _items(doc, typeid):
        return [o for o in doc.Objects if o.TypeId == typeid]

    def test_counts_and_group(self):
        doc = self._insert()
        self.assertEqual(len(self._items(doc, "Part::PmiDimension")), 2)
        self.assertEqual(len(self._items(doc, "Part::PmiTolerance")), 1)
        self.assertEqual(len(self._items(doc, "Part::PmiDatum")), 1)
        groups = self._items(doc, "Part::PmiGroup")
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].Label, "PMI")
        self.assertEqual(len(groups[0].Group), 4)
        rep = Import.lastStepPmiReport()
        self.assertEqual((rep["dimensions"], rep["tolerances"], rep["datums"]), (2, 1, 1))
        self.assertEqual(rep["warnings"], [])

    def test_values_bounds_modifiers_datums(self):
        doc = self._insert()
        size = [o for o in self._items(doc, "Part::PmiDimension") if o.PmiType == "Size_Diameter"][0]
        self.assertAlmostEqual(size.Value, 10.0, places=9)
        self.assertTrue(size.HasBounds)
        self.assertEqual(size.BoundsKind, "plus_minus")
        self.assertAlmostEqual(size.UpperBound, 0.1, places=9)
        self.assertAlmostEqual(size.LowerBound, -0.2, places=9)  # signed deviation, as the file states it
        loc = [o for o in self._items(doc, "Part::PmiDimension") if o.PmiType == "Location_LinearDistance"][0]
        self.assertAlmostEqual(loc.Value, 20.0, places=9)
        self.assertFalse(loc.HasBounds)
        tol = self._items(doc, "Part::PmiTolerance")[0]
        self.assertEqual(tol.PmiType, "Perpendicularity")
        self.assertAlmostEqual(tol.Value, 0.05, places=9)
        self.assertEqual(list(tol.Modifiers), ["MAXIMUM_MATERIAL_REQUIREMENT"])
        self.assertEqual(list(tol.DatumReferences), ["A(MAXIMUM_MATERIAL_REQUIREMENT)"])
        self.assertEqual(tol.ZoneShape, "Diameter")
        datum = self._items(doc, "Part::PmiDatum")[0]
        self.assertEqual(datum.Letter, "A")
        self.assertEqual(datum.Position, 1)
        self.assertEqual([o.Name for o in tol.Datums], [datum.Name])

    def test_face_links_and_step_ids(self):
        doc = self._insert()
        feat = [o for o in doc.Objects if o.TypeId == "Part::Feature"][0]
        size = [o for o in self._items(doc, "Part::PmiDimension") if o.PmiType == "Size_Diameter"][0]
        loc = [o for o in self._items(doc, "Part::PmiDimension") if o.PmiType == "Location_LinearDistance"][0]
        datum = self._items(doc, "Part::PmiDatum")[0]
        # the size dimension governs the lateral face (2 pi r h), the location dimension the two end faces
        (obj, subs), = [(e[0], list(e[1])) for e in size.References]
        self.assertEqual(obj.Name, feat.Name)
        self.assertEqual(len(subs), 1)
        self.assertAlmostEqual(feat.Shape.getElement(subs[0]).Area, 2 * math.pi * 5 * 20, places=6)
        (obj, subs), = [(e[0], list(e[1])) for e in loc.References]
        self.assertEqual(len(subs), 2)
        for s in subs:
            self.assertAlmostEqual(feat.Shape.getElement(s).Area, math.pi * 25, places=6)
        (obj, dsubs), = [(e[0], list(e[1])) for e in datum.References]
        self.assertEqual(len(dsubs), 1)
        self.assertIn(dsubs[0], subs)
        # the Part 21 ids of the governed faces are kept
        self.assertEqual(len(size.StepFaceIds), 1)
        self.assertEqual(len(loc.StepFaceIds), 2)
        self.assertEqual(list(datum.StepFaceIds), [d for d in loc.StepFaceIds if d in datum.StepFaceIds])
        # the text of the file names those ids as ADVANCED_FACE entities
        for i in list(size.StepFaceIds) + list(loc.StepFaceIds):
            self.assertIn("#%d = ADVANCED_FACE" % i, FIXTURE)
        self.assertFalse(size.Unattached)

    def test_non_pmi_objects_equal_stock(self):
        stock = self._insert(FC_STEP_PMI="0")
        self.assertEqual(len(self._items(stock, "Part::PmiGroup")), 0)
        doc = self._insert()
        a = [(o.Name, o.TypeId, o.Label, o.Shape.exportBrepToString() if hasattr(o, "Shape") else None)
             for o in stock.Objects]
        b = [(o.Name, o.TypeId, o.Label, o.Shape.exportBrepToString() if hasattr(o, "Shape") else None)
             for o in doc.Objects if not o.TypeId.startswith("Part::Pmi")]
        self.assertEqual(a, b)
        # removing the group and its members restores the stock document
        group = self._items(doc, "Part::PmiGroup")[0]
        for o in list(group.Group):
            doc.removeObject(o.Name)
        doc.removeObject(group.Name)
        self.assertEqual([o.Name for o in doc.Objects], [o.Name for o in stock.Objects])

    def test_open_gives_the_same_items(self):
        before = set(App.listDocuments())
        Import.open(self.path)
        new = [App.getDocument(n) for n in App.listDocuments() if n not in before]
        self.docs += new
        self.assertEqual(len(new), 1)
        self.assertEqual(len(self._items(new[0], "Part::PmiDimension")), 2)
        self.assertEqual(len(self._items(new[0], "Part::PmiTolerance")), 1)
        self.assertEqual(len(self._items(new[0], "Part::PmiDatum")), 1)

    def test_raw_attributes_are_kept(self):
        doc = self._insert()
        size = [o for o in self._items(doc, "Part::PmiDimension") if o.PmiType == "Size_Diameter"][0]
        raw = dict(size.RawAttributes)
        self.assertEqual(raw["dim.type"], "Size_Diameter")
        self.assertAlmostEqual(float(raw["dim.value"]), 10.0, places=9)
        self.assertAlmostEqual(float(raw["dim.upper_tol"]), 0.1, places=9)
        self.assertAlmostEqual(float(raw["dim.lower_tol"]), 0.2, places=9)  # OCCT's own sign, kept as given
        self.assertIn("dim.is_range", raw)
        self.assertIn("link.geometry", raw)
        tol = self._items(doc, "Part::PmiTolerance")[0]
        raw = dict(tol.RawAttributes)
        self.assertEqual(raw["tol.type"], "Perpendicularity")
        self.assertEqual(raw["tol.material_requirement"], "MAXIMUM_MATERIAL_REQUIREMENT")
        self.assertEqual(raw["tol.type_of_value"], "Diameter")
        self.assertEqual(raw["tol.datum_references"], "A(MAXIMUM_MATERIAL_REQUIREMENT)")
        datum = self._items(doc, "Part::PmiDatum")[0]
        self.assertEqual(dict(datum.RawAttributes)["datum.identification"], "A")

    def test_unknown_type_is_unmapped_not_an_error(self):
        doc = self._insert(FC_STEP_PMI="unknown:tolerance")
        tol = self._items(doc, "Part::PmiTolerance")[0]
        self.assertTrue(tol.Unmapped)
        rep = Import.lastStepPmiReport()
        self.assertEqual(rep["unmapped"], 1)
        self.assertEqual(len(rep["warnings"]), 1)
        self.assertIn("unmapped", rep["warnings"][0])
        # and again for a modifier
        doc = self._insert(FC_STEP_PMI="unknown:modifier")
        self.assertEqual(Import.lastStepPmiReport()["unmapped"], 1)
        self.assertEqual(len(Import.lastStepPmiReport()["warnings"]), 1)

    def test_geometry_only_file_gives_no_group_and_no_warning(self):
        doc = App.newDocument("G12Box")
        self.docs.append(doc)
        box = doc.addObject("Part::Box", "Box")
        doc.recompute()
        path = os.path.join(self.dir, "box.stp")
        Part.export([box], path)
        d2 = self._insert(path)
        self.assertEqual(len(self._items(d2, "Part::PmiGroup")), 0)
        rep = Import.lastStepPmiReport()
        self.assertFalse(rep["ran"])
        self.assertEqual(rep["warnings"], [])

    def test_items_survive_save_reload_and_recompute(self):
        doc = self._insert()
        doc.recompute()
        for o in doc.Objects:
            self.assertNotIn("Invalid", o.State)
        path = os.path.join(self.dir, "pmi.FCStd")
        doc.saveAs(path)
        App.closeDocument(doc.Name)
        self.docs.remove(doc)
        d2 = App.openDocument(path)
        self.docs.append(d2)
        tol = self._items(d2, "Part::PmiTolerance")[0]
        self.assertEqual(list(tol.DatumReferences), ["A(MAXIMUM_MATERIAL_REQUIREMENT)"])
        self.assertEqual(dict(tol.RawAttributes)["tol.type"], "Perpendicularity")
        self.assertEqual(len(tol.StepFaceIds), 1)
        self.assertEqual(len(tol.References), 1)
        self.assertIsNotNone(tol.References[0][0].Shape.getElement(tol.References[0][1][0]))
        d2.recompute()


def _patched_fixture():
    """The fixture above with four additions, written by hand for this test (no third-party data): the size dimension
    stated as a limit triple (lower limit, nominal value, upper limit, in inches through a conversion-based unit), a
    DIMENSIONAL_SIZE 'diameter' without any stated value, a dimension on an edge, and a second tolerance that uses the
    datum A again in its own reference frame."""
    import re

    t = FIXTURE
    t = re.sub(r"#125 = .*?#126 = SHAPE_DIMENSION_REPRESENTATION\('',\(#125\),#113\);", "#126 = SHAPE_DIMENSION_REPRESENTATION('',(#403,#404,#405),#113);", t, flags=re.S)
    t = re.sub(r"#129 = .*?#132 = PLUS_MINUS_TOLERANCE\(#131,#128\);\n", "", t, flags=re.S)
    add = """#400 = ( CONVERSION_BASED_UNIT('INCH',#401) LENGTH_UNIT() NAMED_UNIT(#402) );
#401 = LENGTH_MEASURE_WITH_UNIT(LENGTH_MEASURE(25.4),#114);
#402 = DIMENSIONAL_EXPONENTS(1.,0.,0.,0.,0.,0.,0.);
#403 = ( LENGTH_MEASURE_WITH_UNIT() MEASURE_REPRESENTATION_ITEM() MEASURE_WITH_UNIT(POSITIVE_LENGTH_MEASURE(0.38),#400) REPRESENTATION_ITEM('lower limit') );
#404 = ( LENGTH_MEASURE_WITH_UNIT() MEASURE_REPRESENTATION_ITEM() MEASURE_WITH_UNIT(POSITIVE_LENGTH_MEASURE(0.4),#400) REPRESENTATION_ITEM('nominal value') );
#405 = ( LENGTH_MEASURE_WITH_UNIT() MEASURE_REPRESENTATION_ITEM() MEASURE_WITH_UNIT(POSITIVE_LENGTH_MEASURE(0.42),#400) REPRESENTATION_ITEM('upper limit') );
#410 = SHAPE_ASPECT('','',#4,.T.);
#411 = GEOMETRIC_ITEM_SPECIFIC_USAGE('','',#410,#10,#105);
#412 = DIMENSIONAL_SIZE(#410,'diameter');
#420 = SHAPE_ASPECT('','',#4,.T.);
#421 = GEOMETRIC_ITEM_SPECIFIC_USAGE('','',#420,#10,#21);
#422 = ( LENGTH_MEASURE_WITH_UNIT() MEASURE_REPRESENTATION_ITEM() MEASURE_WITH_UNIT(POSITIVE_LENGTH_MEASURE(7.),#114) REPRESENTATION_ITEM('nominal value') );
#423 = SHAPE_DIMENSION_REPRESENTATION('',(#422),#113);
#424 = DIMENSIONAL_CHARACTERISTIC_REPRESENTATION(#425,#423);
#425 = DIMENSIONAL_SIZE(#420,'diameter');
#430 = DATUM_REFERENCE_COMPARTMENT('','',#4,.F.,#121,$);
#431 = DATUM_SYSTEM('','',#4,.F.,(#430));
#432 = LENGTH_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-01),#114);
#433 = SHAPE_ASPECT('','',#4,.T.);
#434 = GEOMETRIC_ITEM_SPECIFIC_USAGE('','',#433,#10,#17);
#435 = ( GEOMETRIC_TOLERANCE('','',#432,#433) GEOMETRIC_TOLERANCE_WITH_DATUM_REFERENCE((#431)) PARALLELISM_TOLERANCE() );
"""
    return t.replace("ENDSEC;\nEND-ISO", add + "ENDSEC;\nEND-ISO")


class TestImportStepPmiKernelChecks(unittest.TestCase):
    """What the pass finds in the STEP model that OCCT's reader lost or got wrong (finding H1, M1 to M3 of run 075)."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="g12pmik")
        self.path = os.path.join(self.dir, "pmi_kernel.stp")
        with open(self.path, "w") as f:
            f.write(_patched_fixture())
        self.docs = []
        doc = App.newDocument("G12PmiK")
        self.docs.append(doc)
        Import.insert(self.path, doc.Name)
        self.doc = doc
        self.report = dict(Import.lastStepPmiReport())
        self.dims = [o for o in doc.Objects if o.TypeId == "Part::PmiDimension"]
        self.tols = [o for o in doc.Objects if o.TypeId == "Part::PmiTolerance"]
        self.datums = [o for o in doc.Objects if o.TypeId == "Part::PmiDatum"]

    def tearDown(self):
        for d in self.docs:
            try:
                App.closeDocument(d.Name)
            except Exception:
                pass
        for f in os.listdir(self.dir):
            os.remove(os.path.join(self.dir, f))
        os.rmdir(self.dir)

    def test_limit_triple_takes_value_and_limits_from_the_file(self):
        tri = [o for o in self.dims if "step.stated" in dict(o.RawAttributes) and "lower limit" in dict(o.RawAttributes)["step.stated"]]
        self.assertEqual(len(tri), 1)
        d = tri[0]
        # 0.38, 0.40, 0.42 inch
        self.assertAlmostEqual(d.Value, 0.4 * 25.4, places=9)
        self.assertEqual(d.BoundsKind, "range")
        self.assertAlmostEqual(d.LowerBound, 0.38 * 25.4, places=9)
        self.assertAlmostEqual(d.UpperBound, 0.42 * 25.4, places=9)
        self.assertEqual(d.StepEntityName, "diameter")
        # OCCT's own figures stay in the raw store, and the repair says so (or the kernel read it right)
        raw = dict(d.RawAttributes)
        self.assertIn("dim.values", raw)
        self.assertIn("INCH", raw["step.stated"])
        # OCCT reads this ordering of the triple wrongly (nominal taken as the lower limit); the pass says it corrected that
        self.assertEqual(len(d.KernelLoss), 1)
        self.assertTrue(d.KernelLoss[0].startswith("corrected:"), d.KernelLoss[0])
        self.assertNotAlmostEqual(float(raw["dim.value"]), d.Value, places=6)
        self.assertEqual(self.report["kernel_loss"], 2)

    def test_dimension_without_a_stated_value_is_flagged_not_shown_as_zero(self):
        empty = [o for o in self.dims if o.PmiType == "Location_None"]
        self.assertEqual(len(empty), 1)
        d = empty[0]
        self.assertEqual(d.StepEntityName, "diameter")
        self.assertEqual(len(d.KernelLoss), 1)
        self.assertTrue(d.KernelLoss[0].startswith("lost: no value is stated"))
        self.assertIn("without a stated value", d.Label)
        self.assertTrue(len(d.References) >= 1)
        self.assertEqual(self.report["kernel_loss"] >= 1, True)

    def test_dimension_on_an_edge_links_to_the_edge(self):
        edged = [o for o in self.dims if abs(o.Value - 7.0) < 1e-9]
        self.assertEqual(len(edged), 1)
        subs = [s for e in edged[0].References for s in e[1]]
        self.assertTrue(subs and all(s.startswith("Edge") for s in subs), subs)
        self.assertFalse(edged[0].Unattached)

    def test_datum_used_in_two_frames_is_linked_in_both(self):
        letters = [d.Letter for d in self.datums]
        self.assertEqual(letters.count("A"), 2)
        for d in self.datums:
            self.assertEqual(d.Letter, "A")
            self.assertFalse(d.Unattached, d.Name)
            self.assertEqual(len(d.References), 1)
            self.assertEqual(len(d.StepFaceIds), 1)
        self.assertEqual(self.report["unattached_datums"], 0)
        self.assertEqual(len(self.tols), 2)

    def test_stopped_pass_leaves_no_pmi_object_behind(self):
        old = os.environ.get("FC_STEP_PMI")
        os.environ["FC_STEP_PMI"] = "fail"
        try:
            doc = App.newDocument("G12PmiFail")
            self.docs.append(doc)
            Import.insert(self.path, doc.Name)
        finally:
            if old is None:
                os.environ.pop("FC_STEP_PMI", None)
            else:
                os.environ["FC_STEP_PMI"] = old
        self.assertEqual([o.Name for o in doc.Objects if o.TypeId.startswith("Part::Pmi")], [])
        rep = Import.lastStepPmiReport()
        self.assertEqual(len(rep["warnings"]), 1)
        self.assertIn("no PMI object was kept", rep["warnings"][0])
        self.assertEqual((rep["dimensions"], rep["tolerances"], rep["datums"]), (0, 0, 0))
        # nothing stays, so no count of the stopped pass does either
        self.assertEqual((rep["kernel_loss"], rep["unattached"], rep["unattached_datums"], rep["datums_from_step"], rep["unresolved_faces"]), (0, 0, 0, 0, 0))


def _fixture_with_duplicate_size(lower):
    """_patched_fixture() plus a second diameter dimension on the same face as the limit-triple one (#17), in inches
    like it, stating nominal 0.40 and upper 0.42 and a lower limit of its own."""
    add = """#450 = LENGTH_MEASURE_WITH_UNIT(LENGTH_MEASURE(25.4),#114);
#451 = SHAPE_ASPECT('','',#4,.T.);
#452 = GEOMETRIC_ITEM_SPECIFIC_USAGE('','',#451,#10,#17);
#453 = ( LENGTH_MEASURE_WITH_UNIT() MEASURE_REPRESENTATION_ITEM() MEASURE_WITH_UNIT(POSITIVE_LENGTH_MEASURE(%s),#400) REPRESENTATION_ITEM('lower limit') );
#454 = ( LENGTH_MEASURE_WITH_UNIT() MEASURE_REPRESENTATION_ITEM() MEASURE_WITH_UNIT(POSITIVE_LENGTH_MEASURE(0.4),#400) REPRESENTATION_ITEM('nominal value') );
#455 = ( LENGTH_MEASURE_WITH_UNIT() MEASURE_REPRESENTATION_ITEM() MEASURE_WITH_UNIT(POSITIVE_LENGTH_MEASURE(0.42),#400) REPRESENTATION_ITEM('upper limit') );
#456 = SHAPE_DIMENSION_REPRESENTATION('',(#453,#454,#455),#113);
#457 = DIMENSIONAL_CHARACTERISTIC_REPRESENTATION(#458,#456);
#458 = DIMENSIONAL_SIZE(#451,'diameter');
""" % lower
    return _patched_fixture().replace("ENDSEC;\nEND-ISO", add + "ENDSEC;\nEND-ISO")


def _fixture_with_inline_datum_set(inner, name="''"):
    """_patched_fixture() with the geometric usage of datum feature A (#120) replaced by an inline
    SET_REPRESENTATION_ITEM of the faces #109 and #17, the one construct whose faces are read from the file text;
    inner is put inside the statement (a comment, or text a careless reader would take for syntax)."""
    t = _patched_fixture()
    old = "#120 = GEOMETRIC_ITEM_SPECIFIC_USAGE('','',#119,#10,#109);"
    assert old in t
    new = "#120 = ITEM_IDENTIFIED_REPRESENTATION_USAGE(%s,$,#119,#10,%s SET_REPRESENTATION_ITEM((#109,#17)));" % (name, inner)
    return t.replace(old, new)


class _ImportedText(unittest.TestCase):
    def _import(self, text):
        d = tempfile.mkdtemp(prefix="g12pmir")
        self.addCleanup(lambda: [os.remove(os.path.join(d, f)) for f in os.listdir(d)] or os.rmdir(d))
        path = os.path.join(d, "pmi_text.stp")
        with open(path, "w") as f:
            f.write(text)
        doc = App.newDocument("G12PmiT")
        self.addCleanup(lambda: App.closeDocument(doc.Name))
        Import.insert(path, doc.Name)
        return doc


class TestImportStepPmiDuplicateDimensions(_ImportedText):
    """Two STEP dimensions on the same faces that differ only in a limit cannot be told apart by the label, so the
    value is not taken from either and the item says it was not verified (run 075 repair round 2, finding M1, M3)."""

    def test_ambiguous_pairing_is_refused_and_flagged(self):
        doc = self._import(_fixture_with_duplicate_size("0.37"))
        rng = [o for o in doc.Objects if o.TypeId == "Part::PmiDimension" and o.BoundsKind == "range"]
        self.assertEqual(len(rng), 2)
        for d in rng:
            self.assertEqual(dict(d.RawAttributes)["step.match"], "ambiguous", d.Name)
            self.assertFalse([k for k in d.KernelLoss if k.startswith("corrected:")], list(d.KernelLoss))
            self.assertTrue([k for k in d.KernelLoss if "not verified against the file" in k], list(d.KernelLoss))
        self.assertGreaterEqual(Import.lastStepPmiReport()["kernel_loss"], 3)

    def test_identical_duplicates_confirm_the_same_figures(self):
        doc = self._import(_fixture_with_duplicate_size("0.38"))
        rng = [o for o in doc.Objects if o.TypeId == "Part::PmiDimension" and o.BoundsKind == "range"]
        self.assertEqual(len(rng), 2)
        for d in rng:
            self.assertFalse([k for k in d.KernelLoss if "not verified" in k], list(d.KernelLoss))
            self.assertAlmostEqual(d.LowerBound, 0.38 * 25.4, places=9)
            self.assertAlmostEqual(d.Value, 0.4 * 25.4, places=9)


class TestImportStepPmiPart21Text(_ImportedText):
    """The Part 21 text is read for the one construct the kernel loses: comments (/* */) and strings (with '' as an
    escaped quote) are opaque, ISO 10303-21 section 5 (run 075 repair round 2, finding M2, M3)."""

    @staticmethod
    def _datum_faces(doc):
        ds = [o for o in doc.Objects if o.TypeId == "Part::PmiDatum" and o.Letter == "A"]
        assert ds, "no datum A"
        return sorted({i for d in ds for i in d.StepFaceIds})

    def test_comment_and_string_text_does_not_change_the_faces(self):
        base = self._datum_faces(self._import(_fixture_with_inline_datum_set("")))
        self.assertEqual(len(base), 2)  # both faces of the inline set arrived
        for inner, name in [
            ("/* see #105 */", "''"),
            ("/* it's; #105 */", "''"),
            ("", "'#105; it''s =ITEM_IDENTIFIED_REPRESENTATION_USAGE(#105'"),
        ]:
            self.assertEqual(self._datum_faces(self._import(_fixture_with_inline_datum_set(inner, name))), base, (inner, name))


# ----------------------------------------------------------------------------------------------------------
# Export and round trip (G12 slice 1b-ii). The same generated fixture; behaviour follows the CAx-IF PMI
# practice and ISO 10303-242: PMI is written under an AP242 schema only, angles in radians, one datum per use.
# ----------------------------------------------------------------------------------------------------------


class TestExportStepPmi(unittest.TestCase):
    PREF = "User parameter:BaseApp/Preferences/Mod/Part/STEP"

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="g12pmiexp")
        self.src = _write_fixture(self.dir)
        self.docs = []
        grp = App.ParamGet(self.PREF)
        self._saved = (grp.GetBool("WritePmi", True), grp.GetString("Scheme"))
        grp.SetBool("WritePmi", True)
        self.doc = self._insert(self.src)

    def tearDown(self):
        grp = App.ParamGet(self.PREF)
        grp.SetBool("WritePmi", self._saved[0])
        grp.SetString("Scheme", self._saved[1])
        os.environ.pop("FC_STEP_PMI", None)
        for d in self.docs:
            try:
                App.closeDocument(d.Name)
            except Exception:
                pass
        for f in os.listdir(self.dir):
            os.remove(os.path.join(self.dir, f))
        os.rmdir(self.dir)

    def _insert(self, path):
        doc = App.newDocument("G12PmiExp")
        self.docs.append(doc)
        Import.insert(path, doc.Name)
        return doc

    @staticmethod
    def _roots(doc):
        return [o for o in doc.Objects if not o.TypeId.startswith("Part::Pmi")
                and not [p for p in o.InList if not p.TypeId.startswith("Part::Pmi")]]

    def _export(self, doc, name="out.stp"):
        path = os.path.join(self.dir, name)
        Import.export(self._roots(doc), path)
        return path

    @staticmethod
    def _of(doc, typeid):
        return [o for o in doc.Objects if o.TypeId == typeid]

    @staticmethod
    def _text(path):
        with open(path) as f:
            return f.read()

    def _face_area(self, item):
        return sorted(round(ob.Shape.Faces[int(sub[4:]) - 1].Area, 6)
                      for ob, subs in [(e[0], e[1]) for e in item.References] for sub in subs if sub.startswith("Face"))

    def test_round_trip_keeps_items_values_bounds_modifiers_datums(self):
        out = self._export(self.doc)
        rep = Import.lastStepPmiExportReport()
        self.assertEqual((rep["dimensions"], rep["tolerances"], rep["datums"]), (2, 1, 1))
        self.assertFalse(rep["fell_back"])
        back = self._insert(out)
        self.assertEqual(len(self._of(back, "Part::PmiDimension")), 2)
        self.assertEqual(len(self._of(back, "Part::PmiTolerance")), 1)
        self.assertEqual(len(self._of(back, "Part::PmiDatum")), 1)
        size = [o for o in self._of(back, "Part::PmiDimension") if o.PmiType == "Size_Diameter"][0]
        self.assertAlmostEqual(size.Value, 10.0, places=9)
        self.assertEqual(size.BoundsKind, "plus_minus")
        self.assertAlmostEqual(size.UpperBound, 0.1, places=9)
        self.assertAlmostEqual(size.LowerBound, -0.2, places=9)
        loc = [o for o in self._of(back, "Part::PmiDimension") if o.PmiType == "Location_LinearDistance"][0]
        self.assertAlmostEqual(loc.Value, 20.0, places=9)
        tol = self._of(back, "Part::PmiTolerance")[0]
        self.assertEqual(tol.PmiType, "Perpendicularity")
        self.assertAlmostEqual(tol.Value, 0.05, places=9)
        self.assertEqual(list(tol.Modifiers), ["MAXIMUM_MATERIAL_REQUIREMENT"])
        self.assertEqual(list(tol.DatumReferences), ["A(MAXIMUM_MATERIAL_REQUIREMENT)"])
        self.assertEqual(self._of(back, "Part::PmiDatum")[0].Letter, "A")
        # the governed faces are the same faces (by area) in and out
        for a, b in zip(sorted(self._of(self.doc, "Part::PmiTolerance"), key=lambda o: o.Name),
                        sorted(self._of(back, "Part::PmiTolerance"), key=lambda o: o.Name)):
            self.assertEqual(self._face_area(a), self._face_area(b))
        for a in self._of(self.doc, "Part::PmiDimension"):
            m = [b for b in self._of(back, "Part::PmiDimension") if b.PmiType == a.PmiType][0]
            self.assertEqual(self._face_area(a), self._face_area(m))

    def test_a_value_equal_to_the_no_value_marker_is_written_as_a_value(self):
        # run 079's LOW: OCCT writes a nominal for every dimension, so a dimension with no stated value is written with a marker
        # nominal that the repair pass takes out again; an authored value equal to the marker was taken out with them
        marker = 1.2345678901234e-123
        size = [o for o in self._of(self.doc, "Part::PmiDimension") if o.PmiType == "Size_Diameter"][0]
        size.Value = marker
        size.HasBounds = False
        loc = [o for o in self._of(self.doc, "Part::PmiDimension") if o.PmiType != "Size_Diameter"][0]
        loc.HasValue = False
        out = self._export(self.doc)
        self.assertEqual(Import.lastStepPmiExportReport()["dimensions"], 2)
        back = self._insert(out)
        dims = self._of(back, "Part::PmiDimension")
        self.assertEqual(len(dims), 2)
        have = [d for d in dims if d.Value != 0.0]
        self.assertEqual(len(have), 1)
        self.assertAlmostEqual(have[0].Value / marker, 1.0, places=9)
        valueless = [d for d in dims if d.Value == 0.0]
        self.assertEqual(len(valueless), 1)  # the one with no stated value is still written with none

    def test_pmi_views_do_not_change_the_written_file(self):
        # saved views are document-only on export (OCCT 8.0.1's writer has no view mode)
        def data(path):
            t = self._text(path)
            return t[t.index("DATA;"):]

        before = data(self._export(self.doc, "before.stp"))
        grp = self._of(self.doc, "Part::PmiGroup")[0]
        items = [o for o in self.doc.Objects if o.isDerivedFrom("Part::PmiItem")]
        for k in range(2):
            v = self.doc.addObject("Part::PmiView", "PmiView")
            v.Camera = "#Inventor V2.1 ascii\n\n\nOrthographicCamera {\n  position 0 0 10\n  height 30\n}\n"
            v.Items = items[: k + 1]
            grp.addObject(v)
        self.doc.recompute()
        after = data(self._export(self.doc, "after.stp"))
        self.assertEqual(before, after)
        self.assertEqual(Import.lastStepPmiExportReport()["dimensions"], 2)


    def test_second_round_trip_gives_the_same_items(self):
        back = self._insert(self._export(self.doc))
        back2 = self._insert(self._export(back, "out2.stp"))
        for t in ("Part::PmiDimension", "Part::PmiTolerance", "Part::PmiDatum"):
            self.assertEqual(
                sorted((o.PmiType, round(o.Value, 9), tuple(o.Modifiers), tuple(o.DatumReferences)) for o in self._of(back, t)),
                sorted((o.PmiType, round(o.Value, 9), tuple(o.Modifiers), tuple(o.DatumReferences)) for o in self._of(back2, t)),
            )

    def test_an_edited_value_is_what_is_written(self):
        tol = self._of(self.doc, "Part::PmiTolerance")[0]
        tol.Value = 0.07
        size = [o for o in self._of(self.doc, "Part::PmiDimension") if o.PmiType == "Size_Diameter"][0]
        size.UpperBound = 0.3
        back = self._insert(self._export(self.doc))
        self.assertAlmostEqual(self._of(back, "Part::PmiTolerance")[0].Value, 0.07, places=9)
        size2 = [o for o in self._of(back, "Part::PmiDimension") if o.PmiType == "Size_Diameter"][0]
        self.assertAlmostEqual(size2.UpperBound, 0.3, places=9)
        self.assertAlmostEqual(size2.LowerBound, -0.2, places=9)

    def test_datum_modifier_and_order_follow_the_item(self):
        tol = self._of(self.doc, "Part::PmiTolerance")[0]
        datum = self._of(self.doc, "Part::PmiDatum")[0]
        datum.Modifiers = []
        back = self._insert(self._export(self.doc))
        self.assertEqual(list(self._of(back, "Part::PmiTolerance")[0].DatumReferences), ["A"])
        self.assertEqual(tol.Datums[0].Name, datum.Name)

    def test_write_pmi_off_gives_the_output_of_a_document_without_pmi(self):
        App.ParamGet(self.PREF).SetBool("WritePmi", False)
        off = self._text(self._export(self.doc, "off.stp"))
        self.assertEqual(Import.lastStepPmiExportReport()["dimensions"], 0)
        self.assertNotIn("GEOMETRIC_TOLERANCE", off)
        self.assertNotIn("DIMENSIONAL_", off)
        bare = self._insert(self.src)
        for o in list(bare.Objects):
            if o.TypeId.startswith("Part::Pmi"):
                bare.removeObject(o.Name)
        plain = self._text(self._export(bare, "plain.stp"))

        def norm(t):
            return "\n".join(l for l in t.splitlines() if not l.startswith("FILE_NAME"))
        self.assertEqual(norm(off), norm(plain))
        App.ParamGet(self.PREF).SetBool("WritePmi", True)
        on = self._text(self._export(self.doc, "on.stp"))
        self.assertIn("GEOMETRIC_TOLERANCE", on)

    def test_other_schema_writes_no_pmi_and_warns_once(self):
        App.ParamGet(self.PREF).SetString("Scheme", "AP214IS")
        text = self._text(self._export(self.doc, "ap214.stp"))
        rep = Import.lastStepPmiExportReport()
        self.assertNotIn("GEOMETRIC_TOLERANCE", text)
        self.assertEqual((rep["dimensions"], rep["tolerances"], rep["datums"]), (0, 0, 0))
        self.assertEqual(len(rep["warnings"]), 1)
        self.assertIn("AP214IS", rep["warnings"][0])
        self.assertIn("AP242", rep["warnings"][0])

    def _planar_faces(self):
        for o in self.doc.Objects:
            if o.TypeId == "Part::Feature" and not o.Shape.isNull():
                planes = [i + 1 for i, f in enumerate(o.Shape.Faces) if f.Surface.__class__.__name__ == "Plane"]
                if len(planes) >= 2:
                    return o, planes
        self.fail("no object with two planar faces")

    def _add_item(self, kind, **props):
        item = self.doc.addObject(kind, kind.split("::")[1])
        for k, v in props.items():
            setattr(item, k, v)
        return item

    def test_angles_are_written_in_radians_and_read_back_in_degrees(self):
        obj, planes = self._planar_faces()
        item = self._add_item("Part::PmiDimension", PmiType="Location_Angular", Value=37.5, HasBounds=True,
                              BoundsKind="plus_minus", UpperBound=0.5, LowerBound=-0.25)
        item.References = [(obj, ["Face%d" % planes[0], "Face%d" % planes[1]])]
        item.RawAttributes = {"link.first": "%s.Face%d" % (obj.Name, planes[0]),
                              "link.second": "%s.Face%d" % (obj.Name, planes[1])}
        text = self._text(self._export(self.doc))
        self.assertEqual(Import.lastStepPmiExportReport()["dimensions"], 3)
        self.assertIn("POSITIVE_PLANE_ANGLE_MEASURE(0.65449846949", text)  # 37.5 degrees in radians, the unit of the file
        back = self._insert(os.path.join(self.dir, "out.stp"))
        ang = [o for o in self._of(back, "Part::PmiDimension") if o.PmiType.endswith("Angular")][0]
        self.assertAlmostEqual(ang.Value, 37.5, places=6)
        # the deviations are written in radians too and come back in degrees (the kernel hands them back in the file's unit)
        self.assertAlmostEqual(ang.UpperBound, 0.5, places=6)
        self.assertAlmostEqual(ang.LowerBound, -0.25, places=6)

    def test_common_datum_keeps_the_modifier_of_each_element(self):
        obj, planes = self._planar_faces()
        tol = self._of(self.doc, "Part::PmiTolerance")[0]
        first = tol.Datums[0]
        second = self._add_item("Part::PmiDatum", PmiType="Datum", Letter="B", Position=first.Position,
                                Modifiers=["MAXIMUM_MATERIAL_REQUIREMENT"])
        second.References = [(obj, ["Face%d" % planes[0]])]
        tol.Datums = [first, second]
        tol.DatumReferences = ["A(MAXIMUM_MATERIAL_REQUIREMENT)", "B(MAXIMUM_MATERIAL_REQUIREMENT)"]
        out = self._export(self.doc)
        text = self._text(out)
        self.assertIn("COMMON_DATUM_LIST((", text)
        elems = [l for l in text.replace("\n    ", "").splitlines() if "DATUM_REFERENCE_ELEMENT(" in l]
        self.assertEqual(len(elems), 2)
        for l in elems:
            self.assertIn("SIMPLE_DATUM_REFERENCE_MODIFIER(.MAXIMUM_MATERIAL_REQUIREMENT.)", l)
        back = self._insert(out)
        self.assertEqual(sorted(self._of(back, "Part::PmiTolerance")[0].DatumReferences),
                         ["A(MAXIMUM_MATERIAL_REQUIREMENT)", "B(MAXIMUM_MATERIAL_REQUIREMENT)"])

    def test_one_sided_dimension_is_left_out_with_a_warning_and_the_export_succeeds(self):
        obj, planes = self._planar_faces()
        item = self._add_item("Part::PmiDimension", PmiType="Location_None", Value=0.0)
        item.References = [(obj, ["Face%d" % planes[0]])]
        out = self._export(self.doc)
        rep = Import.lastStepPmiExportReport()
        self.assertEqual(rep["skipped"], 1)
        self.assertEqual(len([w for w in rep["warnings"] if "two sides" in w]), 1)
        self.assertEqual(rep["dimensions"], 2)
        self.assertFalse(rep["fell_back"])
        self.assertEqual(len(self._of(self._insert(out), "Part::PmiDimension")), 2)

    def test_item_governing_an_object_outside_the_export_is_skipped_with_a_warning(self):
        obj, planes = self._planar_faces()
        other = self.doc.addObject("Part::Feature", "Other")
        other.Shape = Part.makeBox(1, 1, 1)
        item = self._add_item("Part::PmiTolerance", PmiType="Flatness", Value=0.01)
        item.References = [(other, ["Face1"])]
        out = os.path.join(self.dir, "subset.stp")
        Import.export([obj], out)
        rep = Import.lastStepPmiExportReport()
        self.assertEqual(rep["skipped"], 1)
        self.assertEqual(rep["tolerances"], 1)
        self.assertTrue(any("not part of the export" in w for w in rep["warnings"]))

    def test_a_failing_pmi_write_falls_back_to_a_file_without_pmi(self):
        os.environ["FC_STEP_PMI"] = "writefail"
        out = self._export(self.doc)
        os.environ.pop("FC_STEP_PMI")
        rep = Import.lastStepPmiExportReport()
        self.assertTrue(rep["fell_back"])
        self.assertTrue(any("written again without PMI" in w for w in rep["warnings"]))
        text = self._text(out)
        self.assertNotIn("GEOMETRIC_TOLERANCE", text)
        self.assertIn("MANIFOLD_SOLID_BREP", text)

    def test_write_error_of_an_unknown_kind_falls_back_but_the_known_kind_is_accepted(self):
        # run 077 repair 1 (mutant of check 15): the check list of the write holds a null reference of the known kind
        # alone (accepted: the file keeps its PMI) or with a failure of another kind (refused: no-PMI fallback and a warning)
        self._annotation(True)
        try:
            os.environ["FC_STEP_PMI"] = "checkfail:known"
            out = self._export(self.doc, "known.stp")
            rep = Import.lastStepPmiExportReport()
            self.assertFalse(rep["fell_back"])
            self.assertTrue(rep["write_error_accepted"])
            self.assertIn("GEOMETRIC_TOLERANCE", self._text(out))
            os.environ["FC_STEP_PMI"] = "checkfail:other"
            out = self._export(self.doc, "other.stp")
            rep = Import.lastStepPmiExportReport()
            self.assertTrue(rep["fell_back"])
            self.assertFalse(rep["write_error_accepted"])
            self.assertTrue(any("written again without PMI" in w for w in rep["warnings"]))
            self.assertNotIn("GEOMETRIC_TOLERANCE", self._text(out))
            self.assertIn("MANIFOLD_SOLID_BREP", self._text(out))
        finally:
            os.environ.pop("FC_STEP_PMI", None)

    # ---- run 076 verifier cases: pairing of all-around, graphic callouts ---------------------------------------------

    def _add_tol(self, kind, value, refs, mods=(), datums=(), nfaces=2):
        obj, planes = self._planar_faces()
        t = self._add_item("Part::PmiTolerance", PmiType=kind, Value=value, Modifiers=list(mods))
        t.References = [(obj, ["Face%d" % planes[i] for i in range(nfaces)])]
        if datums:
            t.Datums = list(datums)
            t.DatumReferences = [d.Letter for d in datums]
        return t

    def _all_around_entities(self, text):
        return text.count("ALL_AROUND_SHAPE_ASPECT(")

    def test_all_around_goes_to_the_right_tolerance_when_forms_and_magnitudes_mix(self):
        # a datum-less Flatness 0.1 (written as a plain FLATNESS_TOLERANCE entity) with ALL_AROUND, and a Position 0.1 without
        flat = self._add_tol("Flatness", 0.1, None, mods=["ALL_AROUND"])
        pos = self._add_tol("Position", 0.1, None)
        out = self._export(self.doc)
        self.assertEqual(self._all_around_entities(self._text(out)), 1)
        back = self._insert(out)
        got = {o.PmiType: list(o.Modifiers) for o in self._of(back, "Part::PmiTolerance") if o.PmiType in ("Flatness", "Position")}
        self.assertIn("ALL_AROUND", got["Flatness"])
        self.assertNotIn("ALL_AROUND", got["Position"])

    def test_all_around_is_left_alone_when_the_pairing_cannot_be_proved(self):
        datum = self._of(self.doc, "Part::PmiDatum")[0]
        a = self._add_tol("Position", 0.1, None, mods=["ALL_AROUND"], datums=[datum])
        b = self._add_tol("Position", 0.1, None, datums=[datum])
        out = self._export(self.doc)
        rep = Import.lastStepPmiExportReport()
        self.assertEqual(self._all_around_entities(self._text(out)), 0)
        self.assertTrue(any("all-around" in w and "could not be restated" in w for w in rep["warnings"]), rep["warnings"])
        self.assertEqual(rep["tolerances"], 3)

    def _annotation(self, with_face):
        ann = self._add_item("Part::PmiAnnotation", PmiType="DimensionPresentation")
        ann.Presentation = Part.makePolygon([App.Vector(0, 0, 0), App.Vector(5, 0, 0), App.Vector(5, 5, 0)])
        if with_face:
            obj, planes = self._planar_faces()
            ann.References = [(obj, ["Face%d" % planes[0]])]
        return ann

    def test_graphic_callout_with_a_face_is_written_and_read_back_with_its_face(self):
        self._annotation(True)
        out = self._export(self.doc)
        rep = Import.lastStepPmiExportReport()
        self.assertEqual(rep["annotations"], 1)
        self.assertIn("TESSELLATED_ANNOTATION_OCCURRENCE", self._text(out))
        back = self._insert(out)
        anns = self._of(back, "Part::PmiAnnotation")
        self.assertEqual(len(anns), 1)
        self.assertGreaterEqual(len(anns[0].References), 1)  # import keeps the faces of a callout
        self.assertFalse(anns[0].Presentation.isNull())

    def test_graphic_callout_pointing_at_no_face_is_written_with_one_warning(self):
        # run 077, check 15: OCCT's writer reports an error for a presentation label without a shape although the file
        # is complete; the error is accepted only for that known kind, and the file keeps its semantic PMI and the callout
        self._annotation(False)
        out = self._export(self.doc)
        rep = Import.lastStepPmiExportReport()
        self.assertEqual(rep["shapeless_callouts"], 1)
        self.assertFalse(rep["fell_back"])
        self.assertEqual(len([w for w in rep["warnings"] if "point at no face" in w]), 1)
        self.assertGreater(rep["dimensions"] + rep["tolerances"] + rep["datums"], 0)
        back = self._insert(out)
        self.assertEqual(len(self._of(back, "Part::PmiDimension")), len(self._of(self.doc, "Part::PmiDimension")))

    def test_all_around_is_left_alone_when_the_flagged_one_is_not_the_composite_one(self):
        # identical datum-less Flatness 0.1: the flagged one governs one face (a plain shape aspect), the other two faces (composite)
        self._add_tol("Flatness", 0.1, None, mods=["ALL_AROUND"], nfaces=1)
        self._add_tol("Flatness", 0.1, None, nfaces=2)
        out = self._export(self.doc)
        rep = Import.lastStepPmiExportReport()
        self.assertEqual(self._all_around_entities(self._text(out)), 0)
        self.assertTrue(any("all-around" in w and "could not be restated" in w for w in rep["warnings"]), rep["warnings"])

    def test_common_datum_list_is_restated_even_without_modifiers(self):
        obj, planes = self._planar_faces()
        tol = self._of(self.doc, "Part::PmiTolerance")[0]
        first = tol.Datums[0]
        first.Modifiers = []
        second = self._add_item("Part::PmiDatum", PmiType="Datum", Letter="B", Position=first.Position)
        second.References = [(obj, ["Face%d" % planes[0]])]
        tol.Datums = [first, second]
        tol.DatumReferences = ["A", "B"]
        tol.Modifiers = []
        text = self._text(self._export(self.doc))
        self.assertIn("COMMON_DATUM_LIST((", text)
        self.assertNotIn("COMMON_DATUM_LIST(\n", text.replace("COMMON_DATUM_LIST(\n    (", "COMMON_DATUM_LIST("))


# ---------------------------------------------------------------------------------------------------------------
# G12 slice 3b-i: saved views (CAMERA_MODEL_D3) imported as Part::PmiView. The NIST corpus is never committed: skipped when absent.
import glob
import re

CORPUS = os.environ.get(
    "PMI_CORPUS", os.path.expanduser("~/src/cad-fork/scratch/r068/corpus/nist/NIST-PMI-STEP-Files")
)


def _part21_cameras(path):
    """name -> (z axis of the view reference system) of every CAMERA_MODEL_D3 of a file, read as text (no OCCT)"""
    t = open(path, errors="ignore").read()
    ents = {a: re.sub(r"\s+", " ", b) for a, b in re.findall(r"#(\d+)\s*=\s*(.*?);", t, re.S)}
    out = []
    for b in ents.values():
        m = re.match(r"CAMERA_MODEL_D3\('([^']*)',#(\d+),#(\d+)\)", b)
        if not m:
            continue
        refs = re.findall(r"#(\d+)", ents[m.group(2)])
        z = tuple(float(x) for x in re.findall(r"[-0-9.]+(?:E[-+]?\d+)?", re.search(r"\((.*)\)", ents[refs[1]]).group(1)))
        out.append((m.group(1), z))
    return out


@unittest.skipUnless(os.path.isdir(CORPUS), "the NIST PMI corpus is not present (it is never committed)")
class TestImportStepPmiViews(unittest.TestCase):
    def tearDown(self):
        for name in list(App.listDocuments()):
            App.closeDocument(name)

    def _open(self, stem):
        path = os.path.join(CORPUS, stem + ".stp")
        doc = App.newDocument("Views")
        Import.insert(path, doc.Name)
        return doc, path

    def test_ftc_06_has_one_view_per_camera_model_with_its_items(self):
        doc, path = self._open("nist_ftc_06_asme1_ap242-e2")
        views = [o for o in doc.Objects if o.TypeId == "Part::PmiView"]
        cams = _part21_cameras(path)
        self.assertEqual(len(cams), 3)
        self.assertEqual(len(views), len(cams))
        self.assertEqual(Import.lastStepPmiReport()["views"], 3)
        self.assertEqual(sorted(v.Label for v in views), sorted(c[0] for c in cams))
        grp = [o for o in doc.Objects if o.TypeId == "Part::PmiGroup"][0]
        items = {o.Name for o in doc.Objects if o.isDerivedFrom("Part::PmiItem")}
        for v in views:
            self.assertIn(v, grp.Group)
            self.assertGreater(len(v.Items), 0)
            self.assertTrue({o.Name for o in v.Items} <= items)
        self.assertEqual(sorted(len(v.Items) for v in views), [32, 36, 53])

    def _view(self, doc, name):
        return [o for o in doc.Objects if o.TypeId == "Part::PmiView" and o.Label == name][0]

    def test_a_view_holds_the_dimensions_and_tolerances_its_callouts_present(self):
        # the callouts a view lists (DRAUGHTING_CALLOUT, through ANNOTATION_PLANE) present dimensions and tolerances through
        # DRAUGHTING_MODEL_ITEM_ASSOCIATION (CAx-IF PMI recommended practice, saved views): XCAF's own view label only gives the
        # graphic annotations. Per view: (dimensions, tolerances) of the file's Part 21 text (pmi_expect.py, run 080), less the
        # entity of ftc_06 the kernel makes no label for (#348, a profile of surface in MBD_A)
        doc, _ = self._open("nist_ftc_06_asme1_ap242-e2")
        want = {"MBD_A": (10, 10), "MBD_B": (0, 9), "MBD_C": (4, 8)}
        for name, (nd, nt) in want.items():
            v = self._view(doc, name)
            self.assertEqual(len([o for o in v.Items if o.TypeId == "Part::PmiDimension"]), nd, name)
            self.assertEqual(len([o for o in v.Items if o.TypeId == "Part::PmiTolerance"]), nt, name)
        # a dimension or tolerance is in a view by the entity it was made from: no item is in a view its entity is not in
        ents = {}
        for v in (self._view(doc, n) for n in want):
            for o in v.Items:
                e = o.RawAttributes.get("step.view_entity") if o.TypeId in ("Part::PmiDimension", "Part::PmiTolerance") else None
                if e:
                    ents.setdefault(e, set()).add(v.Label)
        self.assertTrue(all(vs <= set(want) for vs in ents.values()))

    def test_every_tolerance_in_a_view_brings_its_datum_uses_along(self):
        for stem, view in (("nist_ctc_01_asme1_ap242-e1", "MBD_0"), ("nist_ftc_06_asme1_ap242-e2", "MBD_A")):
            doc, _ = self._open(stem)
            v = self._view(doc, view)
            held = {o.Name for o in v.Items}
            tols = [o for o in v.Items if o.TypeId == "Part::PmiTolerance"]
            self.assertGreater(len(tols), 0)
            n = 0
            for t in tols:
                for d in t.Datums:
                    n += 1
                    self.assertIn(d.Name, held, "%s: %s has its datum use %s out of the view" % (view, t.Label, d.Label))
            self.assertGreater(n, 0)
            App.closeDocument(doc.Name)

    def test_the_view_window_is_in_mm_and_the_camera_is_where_the_file_puts_it(self):
        # ctc_05 is in inches: PLANAR_BOX 20.3814 x 20.3814 and 17.4987 x 17.4987 inch are 517.69 and 444.47 mm. The view looks at
        # the middle of its window, which lies the view plane distance in front of the camera of the file, not at the middle of the model
        doc, _ = self._open("nist_ctc_05_asme1_ap242-e1")
        for name, mm in (("MBD_A", 20.3814 * 25.4), ("MBD_B", 17.4987 * 25.4)):
            v = self._view(doc, name)
            h = float(re.search(r"\n\s*height (\S+)", v.Camera).group(1))
            self.assertAlmostEqual(h / mm, 1.0, delta=0.05, msg=name)
        doc, _ = self._open("nist_ftc_06_asme1_ap242-e2")
        v = self._view(doc, "MBD_A")
        pos = App.Vector(*(float(x) for x in re.search(r"position (\S+) (\S+) (\S+)", v.Camera).groups()))
        m = re.search(r"orientation (\S+) (\S+) (\S+) (\S+)", v.Camera)
        rot = App.Rotation(App.Vector(*(float(x) for x in m.groups()[:3])), math.degrees(float(m.group(4))))
        look = rot.multVec(App.Vector(0, 0, -1))
        # the file's camera is at (779.86, 681.71, -616.47) and its view plane 1104.23 in front; the eye is on that line
        eye = App.Vector(779.861938476563, 681.706726074219, -616.473510742188)
        centre = eye + look * 1104.23413085938
        d = (centre - pos)
        self.assertLess((d - look * d.dot(look)).Length, 1e-3)  # the middle of the window is on the line of sight
        self.assertAlmostEqual(float(re.search(r"\n\s*height (\S+)", v.Camera).group(1)), 530.206285269942, places=6)

    def test_the_camera_looks_along_the_z_axis_of_the_files_view_reference_system(self):
        doc, path = self._open("nist_ftc_06_asme1_ap242-e2")
        for name, z in _part21_cameras(path):
            v = [o for o in doc.Objects if o.TypeId == "Part::PmiView" and o.Label == name][0]
            m = re.search(r"orientation (\S+) (\S+) (\S+) (\S+)", v.Camera)
            rot = App.Rotation(App.Vector(*(float(x) for x in m.groups()[:3])), math.degrees(float(m.group(4))))
            look = rot.multVec(App.Vector(0, 0, -1))  # Inventor looks along its -Z
            for a, b in zip(look, z):
                self.assertAlmostEqual(a, b, places=6, msg=name)
            self.assertTrue(v.Camera.startswith("#Inventor V2.1 ascii"))
            self.assertIn("OrthographicCamera", v.Camera)  # the file's view volume is parallel
            h = float(re.search(r"height (\S+)", v.Camera).group(1))
            self.assertGreater(h, 1.0)

    def test_view_counts_equal_the_oracle_on_the_files_the_kernel_reads_views_of(self):
        # OCCT 8.0.1 builds a view label only from some structures of CAMERA_MODEL_D3 (run 080 measured it per file)
        expect = {"nist_ctc_01_asme1_ap242-e1": 1, "nist_ctc_02_asme1_ap242-e2": 3, "nist_ctc_03_asme1_ap242-e2": 1, "nist_ctc_05_asme1_ap242-e1": 2,
                  "nist_stc_08_asme1_ap242-e3": 4}
        for stem, n in expect.items():
            doc, path = self._open(stem)
            self.assertEqual(len([o for o in doc.Objects if o.TypeId == "Part::PmiView"]), n, stem)
            self.assertEqual(len(_part21_cameras(path)), n, stem)
            App.closeDocument(doc.Name)

    def test_a_file_without_saved_views_makes_no_view(self):
        d = tempfile.mkdtemp(prefix="g12views")
        try:
            src = _write_fixture(d)
            doc = App.newDocument("NoViews")
            Import.insert(src, doc.Name)
            self.assertEqual([o for o in doc.Objects if o.TypeId == "Part::PmiView"], [])
            self.assertEqual(Import.lastStepPmiReport()["views"], 0)
        finally:
            for f in os.listdir(d):
                os.remove(os.path.join(d, f))
            os.rmdir(d)

    def test_views_survive_save_and_reopen(self):
        doc, _ = self._open("nist_ftc_06_asme1_ap242-e2")
        d = tempfile.mkdtemp(prefix="g12views")
        try:
            path = os.path.join(d, "v.FCStd")
            before = sorted((v.Label, v.Camera, tuple(o.Name for o in v.Items)) for v in doc.Objects if v.TypeId == "Part::PmiView")
            doc.saveAs(path)
            App.closeDocument(doc.Name)
            doc2 = App.openDocument(path)
            after = sorted((v.Label, v.Camera, tuple(o.Name for o in v.Items)) for v in doc2.Objects if v.TypeId == "Part::PmiView")
            self.assertEqual(before, after)
        finally:
            for f in os.listdir(d):
                os.remove(os.path.join(d, f))
            os.rmdir(d)
