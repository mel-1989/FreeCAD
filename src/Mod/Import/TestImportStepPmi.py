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
        doc = self._insert(FC_STEP_PMI_TEST_UNKNOWN="tolerance")
        tol = self._items(doc, "Part::PmiTolerance")[0]
        self.assertTrue(tol.Unmapped)
        rep = Import.lastStepPmiReport()
        self.assertEqual(rep["unmapped"], 1)
        self.assertEqual(len(rep["warnings"]), 1)
        self.assertIn("unmapped", rep["warnings"][0])
        # and again for a modifier
        doc = self._insert(FC_STEP_PMI_TEST_UNKNOWN="modifier")
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
