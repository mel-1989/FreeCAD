// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2026 FreeCAD fork contributors                          *
 *                                                                         *
 *   This file is part of FreeCAD.                                         *
 *                                                                         *
 *   FreeCAD is free software: you can redistribute it and/or modify it    *
 *   under the terms of the GNU Lesser General Public License as           *
 *   published by the Free Software Foundation, either version 2.1 of the  *
 *   License, or (at your option) any later version.                       *
 *                                                                         *
 *   FreeCAD is distributed in the hope that it will be useful, but        *
 *   WITHOUT ANY WARRANTY; without even the implied warranty of            *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU      *
 *   Lesser General Public License for more details.                       *
 *                                                                         *
 *   You should have received a copy of the GNU Lesser General Public      *
 *   License along with FreeCAD. If not, see                               *
 *   <https://www.gnu.org/licenses/>.                                      *
 *                                                                         *
 **************************************************************************/

// Semantic PMI import: OCCT's STEPCAFControl_Reader (GD&T mode is on by default, measured in run 074)
// builds dimension, geometric tolerance and datum labels in the XCAF document; stock FreeCAD drops them
// when it converts the document. This file reads those labels through XCAFDoc_DimTolTool and the
// XCAFDimTolObjects_* classes (OCCT's documented XCAF API) and creates Part::Pmi* document objects.
// Vocabulary: CAx-IF Recommended Practices for the Representation and Presentation of PMI (AP242),
// ASME Y14.5, ISO 1101, ISO 286 (limits and fits). Part 21 entity ids of the governed faces are found
// through the reader's transfer process (Transfer_TransientProcess, TransferBRep::ShapeResult,
// StepData_StepModel::IdentLabel), the same route as harness/g12/probe/xcaf_faceids.cpp.

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <App/GeoFeature.h>
#include <regex>
#include <functional>
#include <cstring>
#include <map>
#include <set>
#include <unordered_map>
#include <sstream>
#include <string>

#include <BRepAdaptor_Curve.hxx>
#include <GeomAbs_CurveType.hxx>
#include <gp_Pnt.hxx>
#include <BRepGProp.hxx>
#include <GProp_GProps.hxx>
#include <Interface_InterfaceModel.hxx>
#include <NCollection_Sequence.hxx>
#include <StepAP242_ItemIdentifiedRepresentationUsage.hxx>
#include <StepAP242_ItemIdentifiedRepresentationUsageDefinition.hxx>
#include <StepRepr_CompoundRepresentationItem.hxx>
#include <StepBasic_ConversionBasedUnit.hxx>
#include <StepBasic_MeasureWithUnit.hxx>
#include <StepBasic_NamedUnit.hxx>
#include <StepBasic_SiUnit.hxx>
#include <StepData_StepModel.hxx>
#include <StepData_UndefinedEntity.hxx>
#include <StepShape_FaceSurface.hxx>
#include <StepDimTol_Datum.hxx>
#include <StepDimTol_DatumTarget.hxx>
#include <StepRepr_MeasureRepresentationItem.hxx>
#include <StepRepr_ReprItemAndMeasureWithUnit.hxx>
#include <StepRepr_ReprItemAndMeasureWithUnitAndQRI.hxx>
#include <StepRepr_ShapeAspect.hxx>
#include <StepRepr_ShapeAspectRelationship.hxx>
#include <StepShape_DimensionalCharacteristicRepresentation.hxx>
#include <StepShape_DimensionalLocation.hxx>
#include <StepShape_DimensionalSize.hxx>
#include <StepShape_LimitsAndFits.hxx>
#include <StepShape_PlusMinusTolerance.hxx>
#include <StepShape_QualifiedRepresentationItem.hxx>
#include <StepShape_ShapeDimensionRepresentation.hxx>
#include <StepShape_ToleranceMethodDefinition.hxx>
#include <StepShape_TypeQualifier.hxx>
#include <TCollection_AsciiString.hxx>
#include <TCollection_ExtendedString.hxx>
#include <TCollection_HAsciiString.hxx>
#include <TDF_ChildIterator.hxx>
#include <TDF_Label.hxx>
#include <TDF_TagSource.hxx>
#include <TDF_Tool.hxx>
#include <TNaming_Builder.hxx>
#include <TNaming_NamedShape.hxx>
#include <TDataStd_Comment.hxx>
#include <TDataStd_NamedData.hxx>
#include <TopExp.hxx>
#include <TopExp_Explorer.hxx>
#include <TopTools_IndexedMapOfShape.hxx>
#include <TopLoc_Location.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Shape.hxx>
#include <Transfer_TransientProcess.hxx>
#include <TransferBRep.hxx>
#include <XCAFDimTolObjects_DatumObject.hxx>
#include <XCAFDimTolObjects_DimensionObject.hxx>
#include <XCAFDimTolObjects_GeomToleranceObject.hxx>
#include <XCAFDoc_Datum.hxx>
#include <XCAFDoc_Dimension.hxx>
#include <XCAFDoc_DimTolTool.hxx>
#include <XCAFDoc_DocumentTool.hxx>
#include <XCAFDoc_GeomTolerance.hxx>
#include <XCAFDoc_ShapeTool.hxx>
#include <XCAFDoc_ShapeMapTool.hxx>
#include <XSControl_TransferReader.hxx>

#include <App/Document.h>
#include <App/DocumentObject.h>
#include <Base/Console.h>
#include <Base/Exception.h>
#include <Mod/Part/App/PartFeature.h>
#include <Mod/Part/App/PmiFeature.h>
#include <Mod/Part/App/Interface.h>
#include <App/Application.h>

#include "StepPmi.h"
#include <TDF_AttributeSequence.hxx>
#include <TDF_LabelSequence.hxx>
#include <Quantity_ColorRGBA.hxx>
#include "ExportOCAF2.h"

namespace Import
{

namespace
{

StepPmiReport gReport;

constexpr int kStashTag = 701;  // private label under 0:1, next to the 700 of StepAttributes
constexpr int kFaceTag = 7702;  // under the document root, outside the shape tool's 0:1: faces of STEP datum features, one child
                                // per Part 21 id, kept as TNaming shapes

// ---------------------------------------------------------------------------------------------
// the one environment switch (never a preference)
// ---------------------------------------------------------------------------------------------

/// FC_STEP_PMI=0 turns the pass off (tests and timing); FC_STEP_PMI=fail makes the pass stop after creating its objects (a test
/// of the rollback). FC_STEP_PMI=unknown:dimension|tolerance|modifier makes the
/// first item of that kind look unrecognised, so the unmapped path can be tested without a file OCCT mis-types.
std::string pmiSwitch()
{
    const char* v = std::getenv("FC_STEP_PMI");
    return v ? std::string(v) : std::string();
}

bool testForceUnknown(const char* kind)
{
    static std::set<std::string> used;
    if (std::string(kind) == "reset") {
        used.clear();
        return false;
    }
    if (pmiSwitch() != std::string("unknown:") + kind) {
        return false;
    }
    return used.insert(kind).second;
}

// ---------------------------------------------------------------------------------------------
// text helpers
// ---------------------------------------------------------------------------------------------

std::string num(double v)
{
    char b[48];
    std::snprintf(b, sizeof b, "%.17g", v);
    return b;
}

std::string upperSnake(const std::string& s)
{
    // CamelCase or Snake_Case to UPPER_SNAKE
    std::string o;
    for (size_t i = 0; i < s.size(); ++i) {
        char c = s[i];
        if (std::isupper(static_cast<unsigned char>(c)) && i > 0 && std::islower(static_cast<unsigned char>(s[i - 1]))) {
            o += '_';
        }
        o += static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
    }
    return o;
}

std::string join(const std::vector<std::string>& v, const char* sep)
{
    std::string o;
    for (size_t i = 0; i < v.size(); ++i) {
        o += (i ? sep : "") + v[i];
    }
    return o;
}

std::string hstr(const Handle(TCollection_HAsciiString)& h)
{
    return h.IsNull() ? std::string() : std::string(h->ToCString());
}

std::string entryOf(const TDF_Label& l)
{
    TCollection_AsciiString e;
    TDF_Tool::Entry(l, e);
    return e.ToCString();
}

std::string vec3(double x, double y, double z)
{
    return num(x) + " " + num(y) + " " + num(z);
}

// ---------------------------------------------------------------------------------------------
// enumeration names (XCAFDimTolObjects_*); 'known' goes false for a value the table lacks
// ---------------------------------------------------------------------------------------------

std::string unknownName(int v)
{
    return "unknown(" + std::to_string(v) + ")";
}

std::string dimTypeName(XCAFDimTolObjects_DimensionType t, bool& known)
{
    switch (t) {
#define D(x) \
    case XCAFDimTolObjects_DimensionType_##x: \
        return #x;
        D(Location_None)
        D(Location_CurvedDistance)
        D(Location_LinearDistance)
        D(Location_LinearDistance_FromCenterToOuter)
        D(Location_LinearDistance_FromCenterToInner)
        D(Location_LinearDistance_FromOuterToCenter)
        D(Location_LinearDistance_FromOuterToOuter)
        D(Location_LinearDistance_FromOuterToInner)
        D(Location_LinearDistance_FromInnerToCenter)
        D(Location_LinearDistance_FromInnerToOuter)
        D(Location_LinearDistance_FromInnerToInner)
        D(Location_Angular)
        D(Location_Oriented)
        D(Location_WithPath)
        D(Size_CurveLength)
        D(Size_Diameter)
        D(Size_SphericalDiameter)
        D(Size_Radius)
        D(Size_SphericalRadius)
        D(Size_ToroidalMinorDiameter)
        D(Size_ToroidalMajorDiameter)
        D(Size_ToroidalMinorRadius)
        D(Size_ToroidalMajorRadius)
        D(Size_ToroidalHighMajorDiameter)
        D(Size_ToroidalLowMajorDiameter)
        D(Size_ToroidalHighMajorRadius)
        D(Size_ToroidalLowMajorRadius)
        D(Size_Thickness)
        D(Size_Angular)
        D(Size_WithPath)
        D(CommonLabel)
        D(DimensionPresentation)
#undef D
        default:
            break;
    }
    known = false;
    return unknownName(static_cast<int>(t));
}

std::string tolTypeName(XCAFDimTolObjects_GeomToleranceType t, bool& known)
{
    switch (t) {
#define D(x) \
    case XCAFDimTolObjects_GeomToleranceType_##x: \
        return #x;
        D(Angularity)
        D(CircularRunout)
        D(CircularityOrRoundness)
        D(Coaxiality)
        D(Concentricity)
        D(Cylindricity)
        D(Flatness)
        D(Parallelism)
        D(Perpendicularity)
        D(Position)
        D(ProfileOfLine)
        D(ProfileOfSurface)
        D(Straightness)
        D(Symmetry)
        D(TotalRunout)
#undef D
        case XCAFDimTolObjects_GeomToleranceType_None:
            known = false;  // a tolerance without a kind says nothing we can keep as a type
            return "None";
        default:
            break;
    }
    known = false;
    return unknownName(static_cast<int>(t));
}

std::string dimModifName(XCAFDimTolObjects_DimensionModif m, bool& known)
{
    switch (m) {
#define D(x) \
    case XCAFDimTolObjects_DimensionModif_##x: \
        return upperSnake(#x);
        D(ControlledRadius)
        D(Square)
        D(StatisticalTolerance)
        D(ContinuousFeature)
        D(TwoPointSize)
        D(LocalSizeDefinedBySphere)
        D(LeastSquaresAssociationCriterion)
        D(MaximumInscribedAssociation)
        D(MinimumCircumscribedAssociation)
        D(CircumferenceDiameter)
        D(AreaDiameter)
        D(VolumeDiameter)
        D(MaximumSize)
        D(MinimumSize)
        D(AverageSize)
        D(MedianSize)
        D(MidRangeSize)
        D(RangeOfSizes)
        D(AnyRestrictedPortionOfFeature)
        D(AnyCrossSection)
        D(SpecificFixedCrossSection)
        D(CommonTolerance)
        D(FreeStateCondition)
        D(Between)
#undef D
        default:
            break;
    }
    known = false;
    return unknownName(static_cast<int>(m));
}

std::string tolModifName(XCAFDimTolObjects_GeomToleranceModif m, bool& known)
{
    switch (m) {
#define D(x) \
    case XCAFDimTolObjects_GeomToleranceModif_##x: \
        return upperSnake(#x);
        D(Any_Cross_Section)
        D(Common_Zone)
        D(Each_Radial_Element)
        D(Free_State)
        D(Least_Material_Requirement)
        D(Line_Element)
        D(Major_Diameter)
        D(Maximum_Material_Requirement)
        D(Minor_Diameter)
        D(Not_Convex)
        D(Pitch_Diameter)
        D(Reciprocity_Requirement)
        D(Separate_Requirement)
        D(Statistical_Tolerance)
        D(Tangent_Plane)
        D(All_Around)
        D(All_Over)
#undef D
        default:
            break;
    }
    known = false;
    return unknownName(static_cast<int>(m));
}

std::string datumModifName(XCAFDimTolObjects_DatumSingleModif m, bool& known)
{
    switch (m) {
#define D(x) \
    case XCAFDimTolObjects_DatumSingleModif_##x: \
        return upperSnake(#x);
        D(AnyCrossSection)
        D(Any_LongitudinalSection)
        D(Basic)
        D(ContactingFeature)
        D(DegreeOfFreedomConstraintU)
        D(DegreeOfFreedomConstraintV)
        D(DegreeOfFreedomConstraintW)
        D(DegreeOfFreedomConstraintX)
        D(DegreeOfFreedomConstraintY)
        D(DegreeOfFreedomConstraintZ)
        D(DistanceVariable)
        D(FreeState)
        D(LeastMaterialRequirement)
        D(Line)
        D(MajorDiameter)
        D(MaximumMaterialRequirement)
        D(MinorDiameter)
        D(Orientation)
        D(PitchDiameter)
        D(Plane)
        D(Point)
        D(Translation)
#undef D
        default:
            break;
    }
    known = false;
    return unknownName(static_cast<int>(m));
}

std::string datumValueModifName(XCAFDimTolObjects_DatumModifWithValue m, bool& known)
{
    switch (m) {
        case XCAFDimTolObjects_DatumModifWithValue_None:
            return "NONE";
        case XCAFDimTolObjects_DatumModifWithValue_CircularOrCylindrical:
            return "CIRCULAR_OR_CYLINDRICAL";
        case XCAFDimTolObjects_DatumModifWithValue_Distance:
            return "DISTANCE";
        case XCAFDimTolObjects_DatumModifWithValue_Projected:
            return "PROJECTED";
        case XCAFDimTolObjects_DatumModifWithValue_Spherical:
            return "SPHERICAL";
        default:
            break;
    }
    known = false;
    return unknownName(static_cast<int>(m));
}

std::string dimQualifierName(XCAFDimTolObjects_DimensionQualifier q, bool& known)
{
    switch (q) {
        case XCAFDimTolObjects_DimensionQualifier_None:
            return "";
        case XCAFDimTolObjects_DimensionQualifier_Min:
            return "Min";
        case XCAFDimTolObjects_DimensionQualifier_Max:
            return "Max";
        case XCAFDimTolObjects_DimensionQualifier_Avg:
            return "Avg";
        default:
            break;
    }
    known = false;
    return unknownName(static_cast<int>(q));
}

std::string angQualifierName(XCAFDimTolObjects_AngularQualifier q, bool& known)
{
    switch (q) {
        case XCAFDimTolObjects_AngularQualifier_None:
            return "";
        case XCAFDimTolObjects_AngularQualifier_Small:
            return "Small";
        case XCAFDimTolObjects_AngularQualifier_Large:
            return "Large";
        case XCAFDimTolObjects_AngularQualifier_Equal:
            return "Equal";
        default:
            break;
    }
    known = false;
    return unknownName(static_cast<int>(q));
}

std::string matReqName(XCAFDimTolObjects_GeomToleranceMatReqModif m, bool& known)
{
    switch (m) {
        case XCAFDimTolObjects_GeomToleranceMatReqModif_None:
            return "";
        case XCAFDimTolObjects_GeomToleranceMatReqModif_M:
            return "MAXIMUM_MATERIAL_REQUIREMENT";
        case XCAFDimTolObjects_GeomToleranceMatReqModif_L:
            return "LEAST_MATERIAL_REQUIREMENT";
        default:
            break;
    }
    known = false;
    return unknownName(static_cast<int>(m));
}

std::string zoneValueName(XCAFDimTolObjects_GeomToleranceTypeValue v, bool& known)
{
    switch (v) {
        case XCAFDimTolObjects_GeomToleranceTypeValue_None:
            return "";
        case XCAFDimTolObjects_GeomToleranceTypeValue_Diameter:
            return "Diameter";
        case XCAFDimTolObjects_GeomToleranceTypeValue_SphericalDiameter:
            return "SphericalDiameter";
        default:
            break;
    }
    known = false;
    return unknownName(static_cast<int>(v));
}

std::string zoneModifName(XCAFDimTolObjects_GeomToleranceZoneModif v, bool& known)
{
    switch (v) {
        case XCAFDimTolObjects_GeomToleranceZoneModif_None:
            return "";
        case XCAFDimTolObjects_GeomToleranceZoneModif_Projected:
            return "Projected";
        case XCAFDimTolObjects_GeomToleranceZoneModif_Runout:
            return "Runout";
        case XCAFDimTolObjects_GeomToleranceZoneModif_NonUniform:
            return "NonUniform";
        default:
            break;
    }
    known = false;
    return unknownName(static_cast<int>(v));
}

std::string affectedPlaneName(XCAFDimTolObjects_ToleranceZoneAffectedPlane v, bool& known)
{
    switch (v) {
        case XCAFDimTolObjects_ToleranceZoneAffectedPlane_None:
            return "";
        case XCAFDimTolObjects_ToleranceZoneAffectedPlane_Intersection:
            return "Intersection";
        case XCAFDimTolObjects_ToleranceZoneAffectedPlane_Orientation:
            return "Orientation";
        default:
            break;
    }
    known = false;
    return unknownName(static_cast<int>(v));
}

std::string targetTypeName(XCAFDimTolObjects_DatumTargetType v, bool& known)
{
    switch (v) {
        case XCAFDimTolObjects_DatumTargetType_Point:
            return "Point";
        case XCAFDimTolObjects_DatumTargetType_Line:
            return "Line";
        case XCAFDimTolObjects_DatumTargetType_Rectangle:
            return "Rectangle";
        case XCAFDimTolObjects_DatumTargetType_Circle:
            return "Circle";
        case XCAFDimTolObjects_DatumTargetType_Area:
            return "Area";
        default:
            break;
    }
    known = false;
    return unknownName(static_cast<int>(v));
}

std::string formVarianceName(XCAFDimTolObjects_DimensionFormVariance v, bool& known)
{
    switch (v) {
#define D(x) \
    case XCAFDimTolObjects_DimensionFormVariance_##x: \
        return #x;
        D(None)
        D(A) D(B) D(C) D(CD) D(D) D(E) D(EF) D(F) D(FG) D(G) D(H) D(JS) D(J) D(K) D(M) D(N) D(P)
        D(R) D(S) D(T) D(U) D(V) D(X) D(Y) D(Z) D(ZA) D(ZB) D(ZC)
#undef D
        default:
            break;
    }
    known = false;
    return unknownName(static_cast<int>(v));
}

std::string gradeName(XCAFDimTolObjects_DimensionGrade v, bool& known)
{
    int i = static_cast<int>(v);
    if (i >= static_cast<int>(XCAFDimTolObjects_DimensionGrade_IT01)
        && i <= static_cast<int>(XCAFDimTolObjects_DimensionGrade_IT18)) {
        return i == 0 ? std::string("IT01") : "IT" + std::to_string(i - 1);
    }
    known = false;
    return unknownName(i);
}

// ---------------------------------------------------------------------------------------------
// the STEP model itself: what the file states, to check the kernel's reading against
// (CAx-IF Recommended Practices for the Representation and Presentation of PMI, AP242: a dimension is a
// DIMENSIONAL_SIZE or DIMENSIONAL_LOCATION whose DIMENSIONAL_CHARACTERISTIC_REPRESENTATION lists named value
// items such as 'nominal value', 'lower limit' and 'upper limit'; a qualifier is a TYPE_QUALIFIER on such an
// item; a class of tolerance is a LIMITS_AND_FITS of a PLUS_MINUS_TOLERANCE; a datum is a DATUM whose faces
// are those of the DATUM_FEATURE related to it)
// ---------------------------------------------------------------------------------------------

constexpr char kFieldSep = '\x1f';
constexpr char kRecordSep = '\x1e';

struct StatedValue
{
    std::string name;
    std::string unit;
    std::string qual;  // type qualifier text of the item: maximum, minimum, average
    double raw {0};    // as written in the file
    double conv {0};   // in the unit OCCT reports (mm or degree)
    bool ok {false};   // conv is meaningful
};

struct DimEntity
{
    int ident {0};
    std::string name;
    bool location {false};
    std::set<int> faces;
    std::vector<StatedValue> items;
    std::vector<std::string> fits;
};

struct DatumFaces
{
    std::set<int> ids;       // faces of the datum feature that the kernel produced
    std::set<int> unmapped;  // ADVANCED_FACEs the file lists for it that the kernel produced no face for
    int entities {0};
};

bool same(double a, double b)
{
    return std::fabs(a - b) <= 1e-9 * std::max(std::fabs(a), std::fabs(b)) + 1e-12;
}

/// The SI value (metre or radian) of one unit of nu; angle is set for a plane angle unit, known when it is a length
/// or plane angle unit this code understands
double siOfUnit(const Handle(StepBasic_NamedUnit)& nu, bool& angle, bool& known, std::string& name, int depth = 0)
{
    angle = false;
    known = false;
    if (nu.IsNull() || depth > 4) {
        return 1.0;
    }
    std::string tn = nu->DynamicType()->Name();
    angle = tn.find("PlaneAngle") != std::string::npos;
    bool length = tn.find("Length") != std::string::npos;
    Handle(StepBasic_SiUnit) si = Handle(StepBasic_SiUnit)::DownCast(nu);
    if (!si.IsNull()) {
        double p = 1.0;
        std::string pn;
        if (si->HasPrefix()) {
            switch (si->Prefix()) {
                case StepBasic_spExa: p = 1e18; pn = "EXA"; break;
                case StepBasic_spPeta: p = 1e15; pn = "PETA"; break;
                case StepBasic_spTera: p = 1e12; pn = "TERA"; break;
                case StepBasic_spGiga: p = 1e9; pn = "GIGA"; break;
                case StepBasic_spMega: p = 1e6; pn = "MEGA"; break;
                case StepBasic_spKilo: p = 1e3; pn = "KILO"; break;
                case StepBasic_spHecto: p = 1e2; pn = "HECTO"; break;
                case StepBasic_spDeca: p = 1e1; pn = "DECA"; break;
                case StepBasic_spDeci: p = 1e-1; pn = "DECI"; break;
                case StepBasic_spCenti: p = 1e-2; pn = "CENTI"; break;
                case StepBasic_spMilli: p = 1e-3; pn = "MILLI"; break;
                case StepBasic_spMicro: p = 1e-6; pn = "MICRO"; break;
                case StepBasic_spNano: p = 1e-9; pn = "NANO"; break;
                case StepBasic_spPico: p = 1e-12; pn = "PICO"; break;
                case StepBasic_spFemto: p = 1e-15; pn = "FEMTO"; break;
                case StepBasic_spAtto: p = 1e-18; pn = "ATTO"; break;
                default: break;
            }
        }
        name = pn + (angle ? "RADIAN" : "METRE");
        known = angle || length;
        return p;
    }
    Handle(StepBasic_ConversionBasedUnit) cb = Handle(StepBasic_ConversionBasedUnit)::DownCast(nu);
    if (!cb.IsNull()) {
        name = hstr(cb->Name());
        Handle(StepBasic_MeasureWithUnit) f = Handle(StepBasic_MeasureWithUnit)::DownCast(cb->ConversionFactor());
        if (!f.IsNull()) {
            bool a2 = false, k2 = false;
            std::string n2;
            double inner = siOfUnit(f->UnitComponent().NamedUnit(), a2, k2, n2, depth + 1);
            known = k2 && (angle || length) && a2 == angle;
            return f->ValueComponent() * inner;
        }
    }
    return 1.0;
}

StatedValue statedOf(const Handle(StepBasic_MeasureWithUnit)& m, const std::string& name)
{
    StatedValue v;
    v.name = name;
    if (m.IsNull()) {
        return v;
    }
    v.raw = m->ValueComponent();
    bool angle = false, known = false;
    std::string un;
    double si = siOfUnit(m->UnitComponent().NamedUnit(), angle, known, un);
    v.unit = un;
    v.ok = known;
    v.conv = angle ? v.raw * si * 180.0 / M_PI : v.raw * si * 1000.0;
    return v;
}

class StepModelScan
{
public:
    StepModelScan(const Handle(StepData_StepModel)& model, const std::set<int>& faceIds, const char* path)
    {
        const int n = model->NbEntities();
        bool inlineSets = false;
        // which of the entity classes of interest each entity belongs to, worked out once per dynamic type
        enum : unsigned
        {
            kFace = 1,
            kIiru = 2,
            kSar = 4,
            kDimSize = 8,
            kDimLoc = 16,
            kDcr = 32,
            kPmt = 64,
            kDatumEnt = 128
        };
        std::unordered_map<const Standard_Type*, unsigned> kindOfType;
        std::vector<unsigned> kinds(static_cast<size_t>(n) + 1, 0);
        for (int i = 1; i <= n; ++i) {
            const Handle(Standard_Transient)& e = model->Value(i);
            const Standard_Type* t = e->DynamicType().get();
            auto it = kindOfType.find(t);
            if (it == kindOfType.end()) {
                unsigned k = 0;
                k |= e->IsKind(STANDARD_TYPE(StepShape_FaceSurface)) ? kFace : 0;
                k |= e->IsKind(STANDARD_TYPE(StepAP242_ItemIdentifiedRepresentationUsage)) ? kIiru : 0;
                k |= e->IsKind(STANDARD_TYPE(StepRepr_ShapeAspectRelationship)) ? kSar : 0;
                k |= e->IsKind(STANDARD_TYPE(StepShape_DimensionalSize)) ? kDimSize : 0;
                k |= e->IsKind(STANDARD_TYPE(StepShape_DimensionalLocation)) ? kDimLoc : 0;
                k |= e->IsKind(STANDARD_TYPE(StepShape_DimensionalCharacteristicRepresentation)) ? kDcr : 0;
                k |= e->IsKind(STANDARD_TYPE(StepShape_PlusMinusTolerance)) ? kPmt : 0;
                k |= e->IsKind(STANDARD_TYPE(StepDimTol_Datum)) ? kDatumEnt : 0;
                it = kindOfType.emplace(t, k).first;
            }
            kinds[static_cast<size_t>(i)] = it->second;
        }
        std::set<int> allFaces;  // every face entity of the file, mapped by the kernel or not
        for (int i = 1; i <= n; ++i) {
            if (kinds[static_cast<size_t>(i)] & kFace) {
                allFaces.insert(static_cast<int>(model->IdentLabel(model->Value(i))));
            }
        }
        mapped = faceIds;
        for (int i = 1; i <= n; ++i) {
            if (!(kinds[static_cast<size_t>(i)] & (kIiru | kSar))) {
                continue;
            }
            const Handle(Standard_Transient)& ent = model->Value(i);
            // GEOMETRIC_ITEM_SPECIFIC_USAGE and ITEM_IDENTIFIED_REPRESENTATION_USAGE name the faces of a shape aspect, the
            // latter often through a SET_REPRESENTATION_ITEM of faces
            Handle(StepAP242_ItemIdentifiedRepresentationUsage) g =
                Handle(StepAP242_ItemIdentifiedRepresentationUsage)::DownCast(ent);
            if (!g.IsNull()) {
                Handle(StepRepr_ShapeAspect) a = g->Definition().ShapeAspect();
                if (!a.IsNull() && !g->IdentifiedItem().IsNull()) {
                    for (int k = 1; k <= g->NbIdentifiedItem(); ++k) {
                        // an inline SET_REPRESENTATION_ITEM((#a,#b)) reads as a null item: OCCT cannot hold it
                        inlineSets = inlineSets || g->IdentifiedItemValue(k).IsNull();
                        collectFaces(model, allFaces, g->IdentifiedItemValue(k), gisu[a.get()], 0);
                    }
                }
                continue;
            }
            Handle(StepRepr_ShapeAspectRelationship) r = Handle(StepRepr_ShapeAspectRelationship)::DownCast(ent);
            // a DIMENSIONAL_LOCATION is a relationship between the two aspects it measures, not a part-of link
            if (!r.IsNull() && !(kinds[static_cast<size_t>(i)] & kDimLoc) && !r->RelatingShapeAspect().IsNull()
                && !r->RelatedShapeAspect().IsNull()) {
                down[r->RelatingShapeAspect().get()].push_back(r->RelatedShapeAspect().get());
                up[r->RelatedShapeAspect().get()].push_back(r->RelatingShapeAspect().get());
            }
        }
        if (inlineSets && path) {
            readInlineSets(model, allFaces, path);
        }
        std::map<const Standard_Transient*, size_t> at;
        for (int i = 1; i <= n; ++i) {
            if (!(kinds[static_cast<size_t>(i)] & (kDimSize | kDimLoc))) {
                continue;
            }
            const Handle(Standard_Transient)& ent = model->Value(i);
            Handle(StepShape_DimensionalSize) sz = Handle(StepShape_DimensionalSize)::DownCast(ent);
            Handle(StepShape_DimensionalLocation) lc = Handle(StepShape_DimensionalLocation)::DownCast(ent);
            if (sz.IsNull() && lc.IsNull()) {
                continue;
            }
            DimEntity e;
            e.ident = static_cast<int>(model->IdentLabel(ent));
            std::set<const Standard_Transient*> seen;
            if (!sz.IsNull()) {
                e.name = hstr(sz->Name());
                if (!sz->AppliesTo().IsNull()) {
                    closure(sz->AppliesTo().get(), e.faces, seen);
                }
            }
            else {
                e.location = true;
                e.name = hstr(lc->Name());
                if (!lc->RelatingShapeAspect().IsNull()) {
                    closure(lc->RelatingShapeAspect().get(), e.faces, seen);
                }
                if (!lc->RelatedShapeAspect().IsNull()) {
                    closure(lc->RelatedShapeAspect().get(), e.faces, seen);
                }
            }
            e.faces = onlyMapped(e.faces);
            at[ent.get()] = dims.size();
            dims.push_back(e);
        }
        auto entityOf = [&](const Handle(Standard_Transient)& d) -> DimEntity* {
            if (d.IsNull()) {
                return nullptr;
            }
            auto it = at.find(d.get());
            return it == at.end() ? nullptr : &dims[it->second];
        };
        for (int i = 1; i <= n; ++i) {
            if (!(kinds[static_cast<size_t>(i)] & (kDcr | kPmt | kDatumEnt))) {
                continue;
            }
            const Handle(Standard_Transient)& ent = model->Value(i);
            Handle(StepShape_DimensionalCharacteristicRepresentation) dcr =
                Handle(StepShape_DimensionalCharacteristicRepresentation)::DownCast(ent);
            if (!dcr.IsNull()) {
                DimEntity* e = entityOf(dcr->Dimension().Value());
                if (e && !dcr->Representation().IsNull()) {
                    collectItems(dcr->Representation(), *e);
                }
                continue;
            }
            Handle(StepShape_PlusMinusTolerance) pm = Handle(StepShape_PlusMinusTolerance)::DownCast(ent);
            if (!pm.IsNull()) {
                DimEntity* e = entityOf(pm->TolerancedDimension().Value());
                Handle(StepShape_LimitsAndFits) lf = pm->Range().LimitsAndFits();
                if (e && !lf.IsNull()) {
                    e->fits.push_back(hstr(lf->FormVariance()) + "/" + hstr(lf->ZoneVariance()));
                }
                continue;
            }
            Handle(StepDimTol_Datum) d = Handle(StepDimTol_Datum)::DownCast(ent);
            if (!d.IsNull()) {
                DatumFaces& df = datums[hstr(d->Identification())];
                ++df.entities;
                std::set<const Standard_Transient*> seen;
                std::set<int> all;
                closure(d.get(), all, seen);
                auto u = up.find(d.get());
                if (u != up.end()) {
                    for (const Standard_Transient* f : u->second) {
                        // what the file relates to the datum is its datum feature (OCCT reads some as a combined class that
                        // is not a StepDimTol_DatumFeature, some as an undefined entity); datum targets are not the feature
                        if (!dynamic_cast<const StepDimTol_DatumTarget*>(f)) {
                            closure(f, all, seen);
                        }
                    }
                }
                for (int id : all) {
                    (mapped.count(id) ? df.ids : df.unmapped).insert(id);
                }
            }
        }
    }

    std::vector<DimEntity> dims;
    std::map<std::string, DatumFaces> datums;

private:
    std::set<int> mapped;
    std::set<int> onlyMapped(const std::set<int>& in) const
    {
        std::set<int> o;
        for (int i : in) {
            if (mapped.count(i)) {
                o.insert(i);
            }
        }
        return o;
    }
    std::map<const Standard_Transient*, std::set<int>> gisu;
    std::map<const Standard_Transient*, std::vector<const Standard_Transient*>> down;
    std::map<const Standard_Transient*, std::vector<const Standard_Transient*>> up;

    /// Blank, in place and keeping every offset, what ISO 10303-21 makes opaque: /* ... */ comments, and the characters
    /// between the quotes of a string ('' inside a string is an escaped quote, not its end). What stays can be scanned
    /// for #n references and ';' without a comment or a name string being taken for syntax.
    static void blankOpaque(std::string& t)
    {
        const size_t n = t.size();
        size_t i = 0;
        while (i < n) {
            if (t[i] == '/' && i + 1 < n && t[i + 1] == '*') {
                size_t e = t.find("*/", i + 2);
                size_t stop = e == std::string::npos ? n : e + 2;
                for (size_t k = i; k < stop; ++k) {
                    if (t[k] != '\n') {
                        t[k] = ' ';
                    }
                }
                i = stop;
            }
            else if (t[i] == '\'') {
                size_t k = i + 1;
                while (k < n) {
                    if (t[k] == '\'') {
                        if (k + 1 < n && t[k + 1] == '\'') {
                            t[k] = t[k + 1] = ' ';
                            k += 2;
                            continue;
                        }
                        break;
                    }
                    if (t[k] != '\n') {
                        t[k] = ' ';
                    }
                    ++k;
                }
                i = k + 1;  // past the closing quote
            }
            else {
                ++i;
            }
        }
    }

    /// Entity references (#n) of one Part 21 statement, outside quoted strings, in order
    static std::vector<int> refsOf(const std::string& stmt)
    {
        std::vector<int> out;
        bool quoted = false;
        for (size_t i = 0; i < stmt.size(); ++i) {
            char c = stmt[i];
            if (c == '\'') {
                quoted = !quoted;  // a doubled quote inside a string toggles twice
            }
            else if (!quoted && c == '#') {
                size_t j = i + 1;
                int v = 0;
                while (j < stmt.size() && std::isdigit(static_cast<unsigned char>(stmt[j]))) {
                    v = v * 10 + (stmt[j] - '0');
                    ++j;
                }
                if (j > i + 1) {
                    out.push_back(v);
                }
                i = j - 1;
            }
        }
        return out;
    }

    /// The statements of the file text that start with an entity name (the previous non-blank character is '='); only
    /// those are returned, with the text up to the closing ';'
    static void statementsOf(const std::string& txt, const char* name, std::vector<std::string>& out)
    {
        const size_t len = std::strlen(name);
        size_t pos = 0;
        while ((pos = txt.find(name, pos)) != std::string::npos) {
            size_t start = pos;
            pos += len;
            size_t k = start;
            while (k > 0 && (txt[k - 1] == ' ' || txt[k - 1] == '\n' || txt[k - 1] == '\r' || txt[k - 1] == '\t')) {
                --k;
            }
            if (k == 0 || txt[k - 1] != '=') {
                continue;
            }
            size_t end = txt.find(';', pos);
            if (end == std::string::npos) {
                break;
            }
            out.push_back(txt.substr(start, end - start));
            pos = end;
        }
    }

    /// The faces that OCCT's model loses: ITEM_IDENTIFIED_REPRESENTATION_USAGE(...,#aspect,#rep,SET_REPRESENTATION_ITEM((#f1,#f2)))
    /// is read from the file text (only the entities of that exact shape; nothing else is taken from the text)
    void readInlineSets(const Handle(StepData_StepModel)& model, const std::set<int>& faceIds, const char* path)
    {
        std::ifstream in(path, std::ios::binary);
        if (!in) {
            return;
        }
        std::stringstream buf;
        buf << in.rdbuf();
        std::string txt = buf.str();
        blankOpaque(txt);
        std::map<int, const Standard_Transient*> byIdent;
        for (int i = 1; i <= model->NbEntities(); ++i) {
            byIdent[static_cast<int>(model->IdentLabel(model->Value(i)))] = model->Value(i).get();
        }
        // shape aspect relationships whose end is an entity type OCCT does not read (an undefined entity) have no
        // related shape aspect in the model; their ends are taken from the text
        std::vector<std::string> stmts;
        statementsOf(txt, "SHAPE_ASPECT_RELATIONSHIP", stmts);
        for (const auto& st : stmts) {
            std::vector<int> r = refsOf(st);
            if (r.size() != 2) {
                continue;
            }
            auto a = byIdent.find(r[0]);
            auto b = byIdent.find(r[1]);
            if (a == byIdent.end() || b == byIdent.end()) {
                continue;
            }
            if (dynamic_cast<const StepData_UndefinedEntity*>(a->second) || dynamic_cast<const StepData_UndefinedEntity*>(b->second)) {
                down[a->second].push_back(b->second);
                up[b->second].push_back(a->second);
            }
        }
        stmts.clear();
        statementsOf(txt, "ITEM_IDENTIFIED_REPRESENTATION_USAGE", stmts);
        for (const auto& st : stmts) {
            if (st.find("SET_REPRESENTATION_ITEM") == std::string::npos) {
                continue;
            }
            std::vector<int> r = refsOf(st);
            if (r.size() < 3) {
                continue;
            }
            auto found = byIdent.find(r[0]);
            if (found == byIdent.end()) {
                continue;
            }
            for (size_t k = 2; k < r.size(); ++k) {
                if (faceIds.count(r[k])) {
                    gisu[found->second].insert(r[k]);
                }
            }
        }
    }

    static void collectFaces(
        const Handle(StepData_StepModel)& model,
        const std::set<int>& faceIds,
        const Handle(StepRepr_RepresentationItem)& it,
        std::set<int>& out,
        int depth
    )
    {
        if (it.IsNull() || depth > 3) {
            return;
        }
        int id = static_cast<int>(model->IdentLabel(it));
        if (faceIds.count(id)) {
            out.insert(id);
            return;
        }
        Handle(StepRepr_CompoundRepresentationItem) c = Handle(StepRepr_CompoundRepresentationItem)::DownCast(it);
        if (!c.IsNull() && !c->ItemElement().IsNull()) {
            for (int k = 1; k <= c->NbItemElement(); ++k) {
                collectFaces(model, faceIds, c->ItemElementValue(k), out, depth + 1);
            }
        }
    }

    void closure(const Standard_Transient* a, std::set<int>& out, std::set<const Standard_Transient*>& seen) const
    {
        if (!seen.insert(a).second) {
            return;
        }
        auto g = gisu.find(a);
        if (g != gisu.end()) {
            out.insert(g->second.begin(), g->second.end());
        }
        auto d = down.find(a);
        if (d != down.end()) {
            for (const Standard_Transient* c : d->second) {
                closure(c, out, seen);
            }
        }
    }

    static void collectItems(const Handle(StepShape_ShapeDimensionRepresentation)& rep, DimEntity& e)
    {
        std::vector<Handle(Standard_Transient)> list;
        if (!rep->ItemsAP242().IsNull()) {
            for (int k = rep->ItemsAP242()->Lower(); k <= rep->ItemsAP242()->Upper(); ++k) {
                list.push_back(rep->ItemsAP242()->Value(k).Value());
            }
        }
        if (!rep->Items().IsNull()) {
            for (int k = rep->Items()->Lower(); k <= rep->Items()->Upper(); ++k) {
                list.push_back(rep->Items()->Value(k));
            }
        }
        std::set<const Standard_Transient*> done;
        for (const auto& t : list) {
            if (t.IsNull() || !done.insert(t.get()).second) {
                continue;
            }
            Handle(StepRepr_ReprItemAndMeasureWithUnit) cm = Handle(StepRepr_ReprItemAndMeasureWithUnit)::DownCast(t);
            if (!cm.IsNull()) {
                std::string nm = hstr(cm->Name());
                if (nm.empty() && !cm->GetRepresentationItem().IsNull()) {
                    nm = hstr(cm->GetRepresentationItem()->Name());
                }
                StatedValue v = statedOf(cm->GetMeasureWithUnit(), nm);
                Handle(StepRepr_ReprItemAndMeasureWithUnitAndQRI) q =
                    Handle(StepRepr_ReprItemAndMeasureWithUnitAndQRI)::DownCast(cm);
                if (!q.IsNull() && !q->GetQualifiedRepresentationItem().IsNull()) {
                    auto qri = q->GetQualifiedRepresentationItem();
                    for (int k = 1; k <= qri->NbQualifiers(); ++k) {
                        Handle(StepShape_TypeQualifier) tq = qri->QualifiersValue(k).TypeQualifier();
                        if (!tq.IsNull()) {
                            v.qual = hstr(tq->Name());
                        }
                    }
                }
                e.items.push_back(v);
                continue;
            }
            Handle(StepRepr_MeasureRepresentationItem) mi = Handle(StepRepr_MeasureRepresentationItem)::DownCast(t);
            if (!mi.IsNull()) {
                e.items.push_back(statedOf(mi->Measure(), hstr(mi->Name())));
            }
        }
    }
};

std::string encodeItems(const std::vector<StatedValue>& items)
{
    std::string o;
    for (const auto& v : items) {
        if (!o.empty()) {
            o += kRecordSep;
        }
        o += v.name + kFieldSep + v.unit + kFieldSep + v.qual + kFieldSep + num(v.raw) + kFieldSep + num(v.conv)
            + kFieldSep + (v.ok ? "1" : "0");
    }
    return o;
}

std::vector<std::string> splitOn(const std::string& t, char sep)
{
    std::vector<std::string> out;
    std::string cur;
    for (char c : t) {
        if (c == sep) {
            out.push_back(cur);
            cur.clear();
        }
        else {
            cur += c;
        }
    }
    out.push_back(cur);
    return out;
}

std::vector<StatedValue> decodeItems(const std::string& t)
{
    std::vector<StatedValue> out;
    if (t.empty()) {
        return out;
    }
    for (const auto& rec : splitOn(t, kRecordSep)) {
        auto f = splitOn(rec, kFieldSep);
        if (f.size() != 6) {
            continue;
        }
        StatedValue v;
        v.name = f[0];
        v.unit = f[1];
        v.qual = f[2];
        v.raw = std::atof(f[3].c_str());
        v.conv = std::atof(f[4].c_str());
        v.ok = f[5] == "1";
        out.push_back(v);
    }
    return out;
}

/// Which STEP dimension entity a dimension label comes from: the entities whose stated values agree with what the
/// kernel read and whose governed faces equal the label's. Several candidates that state the same things count as one;
/// candidates that differ leave the label unmatched (no guess). note says what happened.
const DimEntity* matchDimension(
    const std::vector<DimEntity>& ents,
    const std::set<int>& labelIds,
    const Handle(XCAFDimTolObjects_DimensionObject)& o,
    std::string& note
)
{
    auto stated = [](const DimEntity& e, double v) {
        for (const auto& s : e.items) {
            if (s.ok && same(s.conv, v)) {
                return true;
            }
        }
        return false;
    };
    std::vector<const DimEntity*> cand;
    for (const auto& e : ents) {
        bool ok = false;
        if (e.items.empty()) {
            ok = o->GetValue() == 0.0 && !o->IsDimWithRange() && !o->IsDimWithPlusMinusTolerance();
        }
        else {
            ok = stated(e, o->GetValue())
                || (o->IsDimWithRange() && stated(e, o->GetLowerBound()) && stated(e, o->GetUpperBound()));
        }
        if (ok) {
            cand.push_back(&e);
        }
    }
    std::vector<const DimEntity*> byFace;
    for (const auto* e : cand) {
        if (e->faces == labelIds) {
            byFace.push_back(e);
        }
    }
    if (byFace.empty()) {
        for (const auto* e : cand) {
            if (e->faces.empty() || labelIds.empty()) {
                byFace.push_back(e);
            }
        }
    }
    if (byFace.empty()) {
        note = "none";
        return nullptr;
    }
    // what a candidate states, without its own name: candidates that differ only in the entity name state the same
    // values on the same faces, so either pairing confirms the same figures
    auto sig = [](const DimEntity* e) {
        std::string s = encodeItems(e->items);
        for (const auto& f : e->fits) {
            s += kRecordSep + f;
        }
        return s;
    };
    bool sameStated = true;
    bool sameName = true;
    for (const auto* e : byFace) {
        sameStated = sameStated && sig(e) == sig(byFace[0]);
        sameName = sameName && e->name == byFace[0]->name;
    }
    if (!sameStated) {
        note = "ambiguous";
        return nullptr;
    }
    note = sameName ? "unique" : "equivalent";
    return byFace[0];
}

// ---------------------------------------------------------------------------------------------
// face id stash: a private label under the document root, one slot per dimension/tolerance/datum label
// ---------------------------------------------------------------------------------------------

TDF_Label stashRoot(const TDF_Label& any, bool create)
{
    TDF_Label base = any.Root().FindChild(1, create);
    if (base.IsNull()) {
        return base;
    }
    return base.FindChild(kStashTag, create);
}

std::string idsText(const std::set<int>& s)
{
    std::string o;
    for (int i : s) {
        o += (o.empty() ? "" : ",") + std::to_string(i);
    }
    return o;
}

std::set<int> idsFromText(const std::string& t)
{
    std::set<int> s;
    std::stringstream ss(t);
    std::string item;
    while (std::getline(ss, item, ',')) {
        if (!item.empty()) {
            s.insert(std::atoi(item.c_str()));
        }
    }
    return s;
}

struct StashedIds
{
    std::set<int> first;
    std::set<int> second;
    std::map<std::string, std::string> extra;  // what the STEP model states (step.*), see stashStepPmiFaceIds
    bool present {false};
};

std::map<std::string, StashedIds> readStash(const TDF_Label& any)
{
    std::map<std::string, StashedIds> out;
    TDF_Label root = stashRoot(any, false);
    if (root.IsNull()) {
        return out;
    }
    for (TDF_ChildIterator it(root, false); it.More(); it.Next()) {
        Handle(TDataStd_Comment) c;
        Handle(TDataStd_NamedData) nd;
        if (!it.Value().FindAttribute(TDataStd_Comment::GetID(), c)
            || !it.Value().FindAttribute(TDataStd_NamedData::GetID(), nd)) {
            continue;
        }
        TCollection_AsciiString key(c->Get());
        StashedIds s;
        s.present = true;
        if (nd->HasString(TCollection_ExtendedString("first"))) {
            s.first = idsFromText(TCollection_AsciiString(nd->GetString("first")).ToCString());
        }
        if (nd->HasString(TCollection_ExtendedString("second"))) {
            s.second = idsFromText(TCollection_AsciiString(nd->GetString("second")).ToCString());
        }
        const auto& strings = nd->GetStringsContainer();
        for (NCollection_DataMap<TCollection_ExtendedString, TCollection_ExtendedString>::Iterator it2(strings);
             it2.More();
             it2.Next()) {
            std::string k = TCollection_AsciiString(it2.Key()).ToCString();
            if (k.rfind("step.", 0) == 0) {
                s.extra[k] = TCollection_AsciiString(it2.Value()).ToCString();
            }
        }
        out[key.ToCString()] = s;
    }
    return out;
}

/// A face of the reader's model by its Part 21 id, kept on the stash label so that its TShape identity survives
TopoDS_Shape stashedFace(const TDF_Label& any, int id)
{
    TDF_Label faces = any.Root().FindChild(kFaceTag, false);
    if (faces.IsNull()) {
        return TopoDS_Shape();
    }
    TDF_Label l = faces.FindChild(id, false);
    Handle(TNaming_NamedShape) ns;
    if (l.IsNull() || !l.FindAttribute(TNaming_NamedShape::GetID(), ns)) {
        return TopoDS_Shape();
    }
    return ns->Get();
}

// ---------------------------------------------------------------------------------------------
// links to the imported objects
// ---------------------------------------------------------------------------------------------

struct FaceHit
{
    App::DocumentObject* obj {nullptr};
    std::string sub;
    std::string geom;  // area (or length) and centre of mass of the XCAF face in its own frame, for the link check
    bool byGeometry {false};  // an edge found by curve type, length and centre of mass, not by shape identity
};

class ShapeIndex
{
public:
    explicit ShapeIndex(const std::vector<App::DocumentObject*>& objects)
    {
        for (App::DocumentObject* obj : objects) {
            auto* feat = dynamic_cast<Part::Feature*>(obj);
            if (!feat) {
                continue;
            }
            const TopoDS_Shape& sh = feat->Shape.getShape().getShape();
            if (sh.IsNull()) {
                continue;
            }
            objShapes.emplace_back(obj, sh);
            TopTools_IndexedMapOfShape faces;
            TopExp::MapShapes(sh, TopAbs_FACE, faces);
            for (int i = 1; i <= faces.Extent(); ++i) {
                faceMap.emplace(faces(i).TShape().get(), FaceHit {obj, "Face" + std::to_string(i), {}});
            }
            TopTools_IndexedMapOfShape edges;
            TopExp::MapShapes(sh, TopAbs_EDGE, edges);
            for (int i = 1; i <= edges.Extent(); ++i) {
                edgeMap.emplace(edges(i).TShape().get(), FaceHit {obj, "Edge" + std::to_string(i), {}});
            }
        }
    }

    /// The faces (or, for a shape without faces, edges) of an XCAF shape label as document sub-elements
    void resolve(const NCollection_Sequence<TDF_Label>& labels, std::vector<FaceHit>& hits, int& unresolved) const
    {
        for (int k = 1; k <= labels.Length(); ++k) {
            TopoDS_Shape sh = XCAFDoc_ShapeTool::GetShape(labels.Value(k));
            if (sh.IsNull()) {
                continue;
            }
            bool any = false;
            for (TopExp_Explorer ex(sh, TopAbs_FACE); ex.More(); ex.Next()) {
                any = true;
                auto it = faceMap.find(ex.Current().TShape().get());
                if (it == faceMap.end()) {
                    ++unresolved;
                    continue;
                }
                FaceHit h = it->second;
                h.geom = geometryText(ex.Current());
                add(hits, h);
            }
            if (!any) {
                for (TopExp_Explorer ex(sh, TopAbs_EDGE); ex.More(); ex.Next()) {
                    FaceHit h;
                    auto it = edgeMap.find(ex.Current().TShape().get());
                    if (it != edgeMap.end()) {
                        h = it->second;
                    }
                    else if (!byGeometry(TopoDS::Edge(ex.Current()), h)) {
                        ++unresolved;
                        continue;
                    }
                    h.geom = geometryText(ex.Current());
                    add(hits, h);
                }
            }
        }
    }

    /// One face of the reader as a document sub-element (the TShape identity route)
    bool faceHit(const TopoDS_Shape& face, FaceHit& out) const
    {
        auto it = faceMap.find(face.TShape().get());
        if (it == faceMap.end()) {
            return false;
        }
        out = it->second;
        out.geom = geometryText(face);
        return true;
    }

    static void addHit(std::vector<FaceHit>& hits, const FaceHit& h)
    {
        add(hits, h);
    }

private:
    struct EdgeRec
    {
        App::DocumentObject* obj;
        std::string sub;
        GeomAbs_CurveType type;
        double length;
        gp_Pnt centre;
    };

    static EdgeRec measureEdge(const TopoDS_Edge& e)
    {
        TopoDS_Edge bare = TopoDS::Edge(e.Located(TopLoc_Location()));
        GProp_GProps props;
        BRepGProp::LinearProperties(bare, props);
        EdgeRec r {nullptr, std::string(), BRepAdaptor_Curve(bare).GetType(), props.Mass(), props.CentreOfMass()};
        return r;
    }

    /// An edge the shape-identity lookup misses (the converter rebuilds free curves; the 10 edge-governed items of the NIST
    /// set stay unattached because the converter turns their TRIMMED_CURVE edges into degenerate 4e100-long lines, so no
    /// edge of the document is theirs): the one edge of the document with the
    /// same curve type, length and centre of mass within 1e-9 relative. Several such edges, or none, give no link.
    bool byGeometry(const TopoDS_Edge& e, FaceHit& out) const
    {
        if (!edgeRecsBuilt) {
            edgeRecsBuilt = true;
            for (const auto& os : objShapes) {
                TopTools_IndexedMapOfShape edges;
                TopExp::MapShapes(os.second, TopAbs_EDGE, edges);
                for (int i = 1; i <= edges.Extent(); ++i) {
                    EdgeRec r = measureEdge(TopoDS::Edge(edges(i)));
                    r.obj = os.first;
                    r.sub = "Edge" + std::to_string(i);
                    edgeRecs.push_back(r);
                }
            }
        }
        EdgeRec want = measureEdge(e);
        const EdgeRec* found = nullptr;
        for (const auto& r : edgeRecs) {
            if (r.type != want.type || !same(r.length, want.length)) {
                continue;
            }
            double scale = 1.0 + std::fabs(want.centre.X()) + std::fabs(want.centre.Y()) + std::fabs(want.centre.Z());
            if (r.centre.Distance(want.centre) > 1e-9 * scale) {
                continue;
            }
            if (found) {
                return false;
            }
            found = &r;
        }
        if (!found) {
            return false;
        }
        out.obj = found->obj;
        out.sub = found->sub;
        out.byGeometry = true;
        return true;
    }

    std::vector<std::pair<App::DocumentObject*, TopoDS_Shape>> objShapes;
    mutable std::vector<EdgeRec> edgeRecs;
    mutable bool edgeRecsBuilt {false};

    /// "area|x y z" of a face, "length|x y z" of an edge, measured without any location so that only the
    /// shape itself counts (the same measure is taken from the document object's face by the test)
    std::string geometryText(const TopoDS_Shape& sh) const
    {
        auto cached = geomCache.find(sh.TShape().get());
        if (cached != geomCache.end()) {
            return cached->second;
        }
        GProp_GProps props;
        TopoDS_Shape bare = sh.Located(TopLoc_Location());
        if (bare.ShapeType() == TopAbs_FACE) {
            BRepGProp::SurfaceProperties(bare, props);
        }
        else {
            BRepGProp::LinearProperties(bare, props);
        }
        gp_Pnt c = props.CentreOfMass();
        std::string t = num(props.Mass()) + "|" + vec3(c.X(), c.Y(), c.Z());
        geomCache[sh.TShape().get()] = t;
        return t;
    }

    mutable std::map<const TopoDS_TShape*, std::string> geomCache;

    static void add(std::vector<FaceHit>& hits, const FaceHit& h)
    {
        for (const auto& e : hits) {
            if (e.obj == h.obj && e.sub == h.sub) {
                return;
            }
        }
        hits.push_back(h);
    }

    std::map<const TopoDS_TShape*, FaceHit> faceMap;
    std::map<const TopoDS_TShape*, FaceHit> edgeMap;
};

void setLinks(Part::PmiItem* item, const std::vector<FaceHit>& hits)
{
    std::map<App::DocumentObject*, std::vector<std::string>> grouped;
    std::vector<App::DocumentObject*> order;
    for (const auto& h : hits) {
        if (!grouped.count(h.obj)) {
            order.push_back(h.obj);
        }
        grouped[h.obj].push_back(h.sub);
    }
    if (!grouped.empty()) {
        item->References.setValues(grouped);
        // the presentation is in the coordinates of the file: remember where the governed object stood (slice 2 display).
        // setValues orders the objects by pointer, and the display follows References[0], so take that one
        const auto& linked = item->References.getValues();
        if (!linked.empty()) {
            if (auto* gf = dynamic_cast<App::GeoFeature*>(linked.front())) {
                item->ReferencePlacement.setValue(gf->globalPlacement());
            }
        }
    }
}

// ---------------------------------------------------------------------------------------------
// raw attribute serialisation
// ---------------------------------------------------------------------------------------------

using Raw = std::map<std::string, std::string>;

void rawAx2(Raw& raw, const std::string& key, const gp_Ax2& a)
{
    raw[key] = vec3(a.Location().X(), a.Location().Y(), a.Location().Z()) + " | "
        + vec3(a.Direction().X(), a.Direction().Y(), a.Direction().Z()) + " | "
        + vec3(a.XDirection().X(), a.XDirection().Y(), a.XDirection().Z());
}

void rawPnt(Raw& raw, const std::string& key, const gp_Pnt& p)
{
    raw[key] = vec3(p.X(), p.Y(), p.Z());
}

struct Flags
{
    bool unknownType {false};
    bool unknownOther {false};
    std::vector<std::string> notes;
    void note(const std::string& n)
    {
        notes.push_back(n);
    }
};

// ---------------------------------------------------------------------------------------------
// the pass
// ---------------------------------------------------------------------------------------------

std::string label6(double v)
{
    char b[48];
    std::snprintf(b, sizeof b, "%.6g", v);
    return b;
}

void warn(const std::string& msg)
{
    gReport.warnings.push_back(msg);
    Base::Console().warning("{}\n", msg);
}

struct CommonResult
{
    std::vector<FaceHit> hits;
    std::set<int> ids;
    int unresolved {0};
    int byGeometry {0};
    std::map<std::string, std::string> extra;  // step.* of the stash slot
};

CommonResult resolveCommon(
    const TDF_Label& label,
    const ShapeIndex& index,
    const std::map<std::string, StashedIds>& stash,
    Raw& raw
)
{
    CommonResult r;
    NCollection_Sequence<TDF_Label> f, s;
    XCAFDoc_DimTolTool::GetRefShapeLabel(label, f, s);
    // the faces of each list, in order, so that export can rebuild the two lists of a location dimension
    auto resolveList = [&](const NCollection_Sequence<TDF_Label>& labs, const char* key) {
        std::vector<std::string> names;
        for (int k = 1; k <= labs.Length(); ++k) {
            NCollection_Sequence<TDF_Label> one;
            one.Append(labs.Value(k));
            std::vector<FaceHit> tmp;
            index.resolve(one, tmp, r.unresolved);
            for (const auto& h : tmp) {
                std::string n = std::string(h.obj->getNameInDocument()) + "." + h.sub;
                if (std::find(names.begin(), names.end(), n) == names.end()) {
                    names.push_back(n);
                }
                ShapeIndex::addHit(r.hits, h);
            }
        }
        raw[key] = join(names, ";");
    };
    resolveList(f, "link.first");
    resolveList(s, "link.second");
    auto it = stash.find(entryOf(label));
    if (it != stash.end()) {
        r.ids = it->second.first;
        r.ids.insert(it->second.second.begin(), it->second.second.end());
        r.extra = it->second.extra;
        raw["step.face_ids.first"] = idsText(it->second.first);
        raw["step.face_ids.second"] = idsText(it->second.second);
    }
    raw["xcaf.label"] = entryOf(label);
    raw["xcaf.shapes.first"] = std::to_string(f.Length());
    raw["xcaf.shapes.second"] = std::to_string(s.Length());
    raw["link.unresolved_faces"] = std::to_string(r.unresolved);
    for (const auto& h : r.hits) {
        r.byGeometry += h.byGeometry ? 1 : 0;
    }
    if (r.byGeometry) {
        raw["link.edges_by_geometry"] = std::to_string(r.byGeometry);
    }
    {
        std::vector<std::string> g;
        for (const auto& h : r.hits) {
            g.push_back(std::string(h.obj->getNameInDocument()) + "." + h.sub + "|" + h.geom);
        }
        raw["link.geometry"] = join(g, ";");
    }
    return r;
}

void finishItem(
    Part::PmiItem* item,
    const CommonResult& c,
    Raw& raw,
    const Flags& fl,
    const char* what,
    const std::vector<std::string>& kernelLoss = {}
)
{
    std::vector<long> ids(c.ids.begin(), c.ids.end());
    item->StepFaceIds.setValues(ids);
    setLinks(item, c.hits);
    item->Unattached.setValue(c.hits.empty());
    item->Unmapped.setValue(fl.unknownType || fl.unknownOther);
    item->RawAttributes.setValue(raw);
    gReport.unresolvedFaces += c.unresolved;
    gReport.edgesByGeometry += c.byGeometry;
    item->KernelLoss.setValues(kernelLoss);
    if (!kernelLoss.empty()) {
        ++gReport.kernelLoss;
        raw["kernel.loss"] = join(kernelLoss, " || ");
        item->RawAttributes.setValue(raw);
        gReport.kernelLossItems.push_back(std::string(item->getNameInDocument()) + ": " + kernelLoss.front());
    }
    if (fl.unknownType || fl.unknownOther) {
        ++gReport.unmapped;
        warn(std::string("STEP PMI: ") + what + " '" + item->PmiType.getValue() + "' has an unrecognised "
             + (fl.unknownType ? "type" : "modifier or attribute") + " (" + join(fl.notes, ", ")
             + "); imported as an unmapped item");
    }
}


std::string lowerOf(std::string t)
{
    std::transform(t.begin(), t.end(), t.begin(), [](unsigned char c) {
        return static_cast<char>(std::tolower(c));
    });
    return t;
}

/// Compare the kernel's reading of a dimension with what the STEP model states and record the difference: values
/// the file states and the item lacks are listed in kl ("lost:"), a nominal value the kernel got wrong is corrected
/// from the file ("corrected:"); OCCT's own figures stay in the raw attributes.
void checkDimension(
    Part::PmiDimension* item,
    const Handle(XCAFDimTolObjects_DimensionObject)& o,
    const std::map<std::string, std::string>& extra,
    const std::string& type,
    Raw& raw,
    std::vector<std::string>& kl
)
{
    auto get = [&](const char* k) {
        auto it = extra.find(k);
        return it == extra.end() ? std::string() : it->second;
    };
    std::string nameInFile;
    if (!get("step.match").empty()) {
        raw["step.match"] = get("step.match");
    }
    const std::string matchNote = get("step.match");
    if (matchNote == "none" || matchNote == "ambiguous") {
        // the check against the file was tried and could not run: the kernel's figures stay unconfirmed
        kl.push_back(std::string("lost: value not verified against the file (")
                     + (matchNote == "none" ? "no STEP dimension entity states this label's value on its faces"
                                            : "several STEP dimensions on the same faces state different values and the label "
                                              "cannot be paired with one of them")
                     + "); the kernel's value and limits are kept as read");
    }
    if (get("step.stated") == "1") {
        if (!get("step.entity").empty()) {
            nameInFile = get("step.name");
            raw["step.entity"] = "#" + get("step.entity");
            raw["step.name"] = nameInFile;
            item->StepEntityName.setValue(nameInFile);
        }
        raw["step.kind"] = get("step.kind");
        std::vector<StatedValue> items = decodeItems(get("step.items"));
        std::vector<std::string> txt;
        const StatedValue *nom = nullptr, *lo = nullptr, *up = nullptr;
        std::set<std::string> quals;
        for (const auto& v : items) {
            txt.push_back(v.name + "=" + num(v.raw) + " " + v.unit + (v.ok ? " -> " + num(v.conv) : std::string(" (unit not understood)"))
                          + (v.qual.empty() ? std::string() : " [" + v.qual + "]"));
            std::string ln = lowerOf(v.name);
            if (ln.find("nominal") != std::string::npos) {
                nom = &v;
            }
            else if (ln.find("lower limit") != std::string::npos) {
                lo = &v;
            }
            else if (ln.find("upper limit") != std::string::npos) {
                up = &v;
            }
            if (v.qual == "maximum" || v.qual == "minimum" || v.qual == "average") {
                quals.insert(v.qual);
            }
        }
        raw["step.stated"] = join(txt, "; ");
        // a stated figure whose unit is not understood, or a plane angle unit on a length item (or a length unit on an
        // angle), cannot confirm or correct the kernel's figures
        const bool angularDim = type.find("Angular") != std::string::npos;
        bool unverified = false;
        std::string why;
        for (auto& v : items) {
            std::string un = lowerOf(v.unit);
            bool angleUnit = un.find("radian") != std::string::npos || un.find("degree") != std::string::npos;
            if (!v.ok) {
                unverified = true;
                why = "the unit of '" + v.name + "' is not understood";
            }
            else if (angleUnit != angularDim) {
                v.ok = false;
                unverified = true;
                why = "the unit of '" + v.name + "' (" + v.unit + ") does not suit " + (angularDim ? "an angle" : "a length");
            }
        }
        if (unverified) {
            kl.push_back("lost: value not verified against the file (" + why + "); the kernel's value and limits are kept as read");
        }
        if (nom && lo && up && nom->ok && lo->ok && up->ok && lo->conv > up->conv && !same(lo->conv, up->conv)) {
            kl.push_back("lost: the file states a lower limit (" + label6(lo->conv) + ") above the upper limit ("
                         + label6(up->conv) + "); shown as stated");
        }
        if (nom && lo && up && nom->ok && lo->ok && up->ok) {
            if (o->IsDimWithRange()) {
                if (!same(item->Value.getValue(), nom->conv) || !same(item->LowerBound.getValue(), lo->conv)
                    || !same(item->UpperBound.getValue(), up->conv)) {
                    kl.push_back("corrected: the kernel read value " + label6(item->Value.getValue()) + " and limits "
                                 + label6(item->LowerBound.getValue()) + " to " + label6(item->UpperBound.getValue())
                                 + "; the file states nominal " + label6(nom->conv) + ", lower limit " + label6(lo->conv)
                                 + ", upper limit " + label6(up->conv) + " (value and limits taken from the file)");
                    item->Value.setValue(nom->conv);
                    item->LowerBound.setValue(lo->conv);
                    item->UpperBound.setValue(up->conv);
                }
            }
            else {
                kl.push_back("lost: the file states nominal " + label6(nom->conv) + ", lower limit " + label6(lo->conv)
                             + ", upper limit " + label6(up->conv) + "; the kernel read no limits");
            }
        }
        if (angularDim && o->IsDimWithPlusMinusTolerance() && nom && nom->ok && nom->raw != 0.0 && !same(nom->raw, nom->conv)) {
            // the kernel converts the nominal value of an angle to degrees but hands back the deviations in the unit of the
            // file (radians here); the nominal's own ratio converts them
            const double k = nom->conv / nom->raw;
            kl.push_back("corrected: the kernel read the angle deviations " + label6(item->LowerBound.getValue()) + " and "
                         + label6(item->UpperBound.getValue()) + " in the unit of the file (" + nom->unit
                         + ") while the value is in degrees; converted to degrees");
            item->UpperBound.setValue(item->UpperBound.getValue() * k);
            item->LowerBound.setValue(item->LowerBound.getValue() * k);
        }
        for (const auto& f : splitOn(get("step.fits"), '|')) {
            if (!f.empty() && !o->IsDimWithClassOfTolerance()) {
                kl.push_back("lost: limits-and-fits class '" + f + "' (form variance/zone variance) is stated in the file; the kernel did not read it");
            }
        }
        for (const auto& q : quals) {
            std::string want = q == "maximum" ? "Max" : q == "minimum" ? "Min" : "Avg";
            if (item->Qualifier.getValue() != want) {
                kl.push_back("lost: the file states the qualifier '" + q + "' on this dimension; the kernel did not read it");
            }
        }
    }
    if (type == "Location_None" && get("step.items").empty() && item->Value.getValue() == 0.0 && !o->IsDimWithRange()
        && !o->IsDimWithPlusMinusTolerance() && !o->IsDimWithClassOfTolerance()) {
        kl.push_back(std::string("lost: no value is stated for this dimension")
                     + (nameInFile.empty() ? std::string() : " (STEP entity '" + nameInFile + "')")
                     + "; the kernel typed it Location_None and Value 0.0 is not a measurement");
    }
}

}  // namespace

namespace
{

/// The letter of a datum label as the item will carry it: identification, else the object's name, else the label's name
std::string datumLetter(const TDF_Label& dl)
{
    Handle(XCAFDoc_Datum) attr;
    if (!dl.FindAttribute(XCAFDoc_Datum::GetID(), attr)) {
        return std::string();
    }
    Handle(XCAFDimTolObjects_DatumObject) o = attr->GetObject();
    std::string letter = hstr(attr->GetIdentification());
    if (letter.empty() && !o.IsNull()) {
        letter = hstr(o->GetName());
    }
    if (letter.empty()) {
        letter = hstr(attr->GetName());
    }
    return letter;
}

}  // namespace

bool stepPmiEnabled()
{
    return pmiSwitch() != "0";
}

const StepPmiReport& lastStepPmiReport()
{
    return gReport;
}

void stashStepPmiFaceIds(const Handle(XSControl_WorkSession)& ws, const Handle(TDocStd_Document)& hDoc, const char* path)
{
    try {
        if (!stepPmiEnabled() || ws.IsNull() || hDoc.IsNull()) {
            return;
        }
        Handle(XCAFDoc_DimTolTool) tool = XCAFDoc_DocumentTool::DimTolTool(hDoc->Main());
        NCollection_Sequence<TDF_Label> dims, tols, datums;
        tool->GetDimensionLabels(dims);
        tool->GetGeomToleranceLabels(tols);
        tool->GetDatumLabels(datums);
        if (dims.Length() + tols.Length() + datums.Length() == 0) {
            return;
        }
        Handle(StepData_StepModel) model = Handle(StepData_StepModel)::DownCast(ws->Model());
        Handle(XSControl_TransferReader) reader = ws->TransferReader();
        if (model.IsNull() || reader.IsNull() || reader->TransientProcess().IsNull()) {
            return;
        }
        Handle(Transfer_TransientProcess) tp = reader->TransientProcess();
        // face TShape -> Part 21 entity number of the ADVANCED_FACE it was made from
        std::map<const TopoDS_TShape*, int> faceEnt;
        std::map<int, TopoDS_Shape> idFace;
        for (int m = 1; m <= tp->NbMapped(); ++m) {
            Handle(Standard_Transient) ent = tp->Mapped(m);
            TopoDS_Shape r = TransferBRep::ShapeResult(tp, ent);
            if (r.IsNull() || r.ShapeType() != TopAbs_FACE) {
                continue;
            }
            int id = static_cast<int>(model->IdentLabel(ent));
            faceEnt.emplace(r.TShape().get(), id);
            idFace.emplace(id, r);
        }
        std::set<int> allIds;
        for (const auto& kv : idFace) {
            allIds.insert(kv.first);
        }
        StepModelScan scan(model, allIds, path);
        auto idsOf = [&](const NCollection_Sequence<TDF_Label>& shapes) {
            std::set<int> s;
            for (int k = 1; k <= shapes.Length(); ++k) {
                TopoDS_Shape sh = XCAFDoc_ShapeTool::GetShape(shapes.Value(k));
                for (TopExp_Explorer ex(sh, TopAbs_FACE); ex.More(); ex.Next()) {
                    auto it = faceEnt.find(ex.Current().TShape().get());
                    if (it != faceEnt.end()) {
                        s.insert(it->second);
                    }
                }
            }
            return s;
        };
        TDF_Label root = stashRoot(hDoc->Main(), true);
        TDF_Label faceRoot = hDoc->Main().Root().FindChild(kFaceTag, true);
        auto keepFace = [&](int id) {
            auto it = idFace.find(id);
            if (it == idFace.end()) {
                return;
            }
            TDF_Label l = faceRoot.FindChild(id, true);
            if (!l.IsAttribute(TNaming_NamedShape::GetID())) {
                TNaming_Builder b(l);
                b.Generated(it->second);
            }
        };
        auto put = [&](const TDF_Label& label, const std::map<std::string, std::string>& extra) {
            NCollection_Sequence<TDF_Label> f, s;
            XCAFDoc_DimTolTool::GetRefShapeLabel(label, f, s);
            if (f.Length() + s.Length() == 0 && extra.empty()) {
                return;
            }
            TDF_Label slot = TDF_TagSource::NewChild(root);
            TDataStd_Comment::Set(slot, TCollection_ExtendedString(entryOf(label).c_str()));
            Handle(TDataStd_NamedData) nd = TDataStd_NamedData::Set(slot);
            nd->SetString(TCollection_ExtendedString("first"),
                          TCollection_ExtendedString(idsText(idsOf(f)).c_str()));
            nd->SetString(TCollection_ExtendedString("second"),
                          TCollection_ExtendedString(idsText(idsOf(s)).c_str()));
            for (const auto& kv : extra) {
                nd->SetString(TCollection_ExtendedString(kv.first.c_str()),
                              TCollection_ExtendedString(kv.second.c_str()));
            }
        };
        for (int i = 1; i <= dims.Length(); ++i) {
            std::map<std::string, std::string> extra;
            Handle(XCAFDoc_Dimension) attr;
            if (dims.Value(i).FindAttribute(XCAFDoc_Dimension::GetID(), attr) && !attr->GetObject().IsNull()) {
                NCollection_Sequence<TDF_Label> f, s;
                XCAFDoc_DimTolTool::GetRefShapeLabel(dims.Value(i), f, s);
                std::set<int> ids = idsOf(f);
                std::set<int> ids2 = idsOf(s);
                ids.insert(ids2.begin(), ids2.end());
                std::string note;
                const DimEntity* e = matchDimension(scan.dims, ids, attr->GetObject(), note);
                extra["step.match"] = note;
                if (e) {
                    // several entities that state the same values (equivalent) confirm the figures but not which
                    // entity this is: the entity number and name are left out then
                    if (note == "unique") {
                        extra["step.entity"] = std::to_string(e->ident);
                        extra["step.name"] = e->name;
                    }
                    extra["step.kind"] = e->location ? "location" : "size";
                    extra["step.items"] = encodeItems(e->items);
                    extra["step.fits"] = join(e->fits, "|");
                    extra["step.stated"] = "1";
                }
            }
            put(dims.Value(i), extra);
        }
        for (int i = 1; i <= tols.Length(); ++i) {
            put(tols.Value(i), {});
        }
        for (int i = 1; i <= datums.Length(); ++i) {
            std::map<std::string, std::string> extra;
            std::string letter = datumLetter(datums.Value(i));
            auto it = scan.datums.find(letter);
            if (!letter.empty() && it != scan.datums.end()) {
                extra["step.datum_ids"] = idsText(it->second.ids);
                extra["step.datum_entities"] = std::to_string(it->second.entities);
                extra["step.datum_unmapped"] = idsText(it->second.unmapped);
                if (it->second.entities == 1) {
                    for (int id : it->second.ids) {
                        keepFace(id);
                    }
                }
            }
            put(datums.Value(i), extra);
        }
    }
    catch (const Standard_Failure& e) {
        Base::Console().warning("STEP PMI: STEP-side checks not recorded: {}\n", e.GetMessageString());
    }
    catch (...) {
        Base::Console().warning("STEP PMI: STEP-side checks not recorded\n");
    }
}

namespace
{

Part::PmiDatum* makeDatum(
    App::Document* doc,
    const TDF_Label& dl,
    const ShapeIndex& index,
    const std::map<std::string, StashedIds>& stash,
    std::vector<App::DocumentObject*>& created
)
{
    Handle(XCAFDoc_Datum) attr;
    if (!dl.FindAttribute(XCAFDoc_Datum::GetID(), attr)) {
        return nullptr;
    }
    Handle(XCAFDimTolObjects_DatumObject) o = attr->GetObject();
    auto* item = doc->addObject<Part::PmiDatum>("PmiDatum");
    created.push_back(item);
    Raw raw;
    Flags fl;
    std::string letter = hstr(attr->GetIdentification());
    if (letter.empty() && !o.IsNull()) {
        letter = hstr(o->GetName());
    }
    if (letter.empty()) {
        letter = hstr(attr->GetName());
    }
    item->PmiType.setValue("Datum");
    item->Letter.setValue(letter);
    raw["datum.identification"] = hstr(attr->GetIdentification());
    raw["datum.name"] = hstr(attr->GetName());
    raw["datum.description"] = hstr(attr->GetDescription());
    std::vector<std::string> mods;
    if (!o.IsNull()) {
        item->SemanticName.setValue(hstr(o->GetSemanticName()));
        raw["datum.object_name"] = hstr(o->GetName());
        raw["datum.semantic_name"] = hstr(o->GetSemanticName());
        item->Position.setValue(o->GetPosition());
        raw["datum.position"] = std::to_string(o->GetPosition());
        auto ms = o->GetModifiers();
        for (int i = 1; i <= ms.Length(); ++i) {
            bool known = true;
            std::string n = datumModifName(ms.Value(i), known);
            if (!known || testForceUnknown("modifier")) {
                fl.unknownOther = true;
                fl.note("datum modifier " + n);
            }
            mods.push_back(n);
        }
        XCAFDimTolObjects_DatumModifWithValue mv = XCAFDimTolObjects_DatumModifWithValue_None;
        double mval = 0;
        o->GetModifierWithValue(mv, mval);
        bool known = true;
        std::string mvn = datumValueModifName(mv, known);
        if (!known) {
            fl.unknownOther = true;
            fl.note("datum modifier with value " + mvn);
        }
        raw["datum.modifier_with_value"] = mvn;
        raw["datum.modifier_value"] = num(mval);
        if (mv != XCAFDimTolObjects_DatumModifWithValue_None) {
            mods.push_back(mvn);
        }
        raw["datum.modifiers"] = join(mods, ",");  // as OCCT hands them over
        // OCCT's reader appends the modifiers of every earlier datum of the frame to the next datum (a file
        // with MMR on D, E and F reads back D(), E(MMR), F(MMR,MMR)); one modifier named twice on one datum
        // says nothing more, so the typed list keeps each name once, in first-seen order
        {
            std::vector<std::string> once;
            for (const auto& m : mods) {
                if (std::find(once.begin(), once.end(), m) == once.end()) {
                    once.push_back(m);
                }
            }
            mods = once;
        }
        item->IsTarget.setValue(o->IsDatumTarget());
        raw["datum.is_target"] = o->IsDatumTarget() ? "1" : "0";
        if (o->IsDatumTarget()) {
            bool k2 = true;
            raw["datum.target_type"] = targetTypeName(o->GetDatumTargetType(), k2);
            raw["datum.target_number"] = std::to_string(o->GetDatumTargetNumber());
            raw["datum.target_length"] = num(o->GetDatumTargetLength());
            raw["datum.target_width"] = num(o->GetDatumTargetWidth());
            rawAx2(raw, "datum.target_axis", o->GetDatumTargetAxis());
            raw["datum.target_has_shape"] = o->GetDatumTarget().IsNull() ? "0" : "1";
        }
        raw["datum.has_plane"] = o->HasPlane() ? "1" : "0";
        if (o->HasPlane()) {
            rawAx2(raw, "datum.plane", o->GetPlane());
        }
        raw["datum.has_point"] = o->HasPoint() ? "1" : "0";
        if (o->HasPoint()) {
            rawPnt(raw, "datum.point", o->GetPoint());
        }
        raw["datum.has_text_point"] = o->HasPointText() ? "1" : "0";
        if (o->HasPointText()) {
            rawPnt(raw, "datum.text_point", o->GetPointTextAttach());
        }
        raw["datum.presentation_name"] = hstr(o->GetPresentationName());
        if (!o->GetPresentation().IsNull()) {
            item->Presentation.setValue(o->GetPresentation());
        }
    }
    item->Modifiers.setValues(mods);
    CommonResult c = resolveCommon(dl, index, stash, raw);
    std::vector<std::string> kl;
    {
        // OCCT attaches the faces to the first datum of a reference frame only, and may keep a subset of them; the faces
        // of the DATUM_FEATURE the file relates to the DATUM complete them (only where the file has one DATUM of that letter)
        auto en = c.extra.find("step.datum_ids");
        auto cnt = c.extra.find("step.datum_entities");
        if (en != c.extra.end() && cnt != c.extra.end() && cnt->second == "1") {
            std::set<int> want = idsFromText(en->second);
            raw["step.datum_ids"] = en->second;
            std::vector<FaceHit> fromStep;
            for (int id : want) {
                FaceHit h;
                TopoDS_Shape f = stashedFace(dl, id);
                if (f.IsNull() || !index.faceHit(f, h)) {
                    ++c.unresolved;
                    continue;
                }
                ShapeIndex::addHit(fromStep, h);
            }
            bool conflict = false;
            for (const auto& h : c.hits) {
                bool found = false;
                for (const auto& g : fromStep) {
                    found = found || (g.obj == h.obj && g.sub == h.sub);
                }
                conflict = conflict || !found;
            }
            if (conflict && !want.empty()) {
                kl.push_back("lost: the faces the kernel attached to this datum are not all faces of the STEP datum feature; both kept");
            }
            size_t before = c.hits.size();
            for (const auto& g : fromStep) {
                ShapeIndex::addHit(c.hits, g);
            }
            if (c.hits.size() > before) {
                ++gReport.datumsFromStep;
                raw["link.source"] = before ? "kernel, completed from the STEP datum feature" : "STEP datum feature";
            }
            if (!want.empty()) {
                c.ids = want;
            }
            auto um = c.extra.find("step.datum_unmapped");
            if (um != c.extra.end() && !um->second.empty()) {
                raw["step.datum_unmapped"] = um->second;
                kl.push_back("lost: the file lists " + std::to_string(idsFromText(um->second).size())
                             + " further face(s) for this datum feature (Part 21 ids " + um->second
                             + ") that the kernel produced no face for; the datum is linked to the " + std::to_string(c.hits.size())
                             + " it did");
            }
            std::vector<std::string> g;
            for (const auto& h : c.hits) {
                g.push_back(std::string(h.obj->getNameInDocument()) + "." + h.sub + "|" + h.geom);
            }
            raw["link.geometry"] = join(g, ";");
        }
    }
    if (c.hits.empty() && !item->IsTarget.getValue()) {
        kl.push_back("lost: no face could be found for this datum (neither the kernel nor the STEP datum feature gave one)");
    }
    finishItem(item, c, raw, fl, "datum", kl);
    if (c.hits.empty()) {
        ++gReport.unattachedDatums;
    }
    item->Label.setValue("Datum " + letter);
    return item;
}

}  // namespace

void importStepPmi(const Handle(TDocStd_Document)& hDoc, App::Document* doc)
{
    gReport = StepPmiReport();
    testForceUnknown("reset");
    std::vector<App::DocumentObject*> created;
    std::string groupName;
    // if the pass stops midway nothing of it stays in the document: the objects made so far are removed again
    auto rollback = [&]() {
        std::vector<std::string> names;
        if (!groupName.empty()) {
            names.push_back(groupName);
        }
        for (auto it = created.rbegin(); it != created.rend(); ++it) {
            names.push_back((*it)->getNameInDocument() ? (*it)->getNameInDocument() : "");
        }
        for (const auto& n : names) {
            try {
                if (!n.empty() && doc->getObject(n.c_str())) {
                    doc->removeObject(n.c_str());
                }
            }
            catch (...) {
            }
        }
        created.clear();
        // nothing of the pass stays, so its counts do not either (the warnings do: they say why)
        std::vector<std::string> kept = gReport.warnings;
        gReport = StepPmiReport();
        gReport.ran = true;
        gReport.warnings = kept;
    };
    try {
        if (!stepPmiEnabled() || hDoc.IsNull() || !doc) {
            return;
        }
        Handle(XCAFDoc_DimTolTool) tool = XCAFDoc_DocumentTool::DimTolTool(hDoc->Main());
        NCollection_Sequence<TDF_Label> dims, tols, datums;
        tool->GetDimensionLabels(dims);
        tool->GetGeomToleranceLabels(tols);
        tool->GetDatumLabels(datums);
        if (dims.Length() + tols.Length() + datums.Length() == 0) {
            return;  // no GD&T at all: no group, no message
        }
        gReport.ran = true;
        ShapeIndex index(doc->getObjects());
        std::map<std::string, StashedIds> stash = readStash(hDoc->Main());
        std::set<std::string> usedDatums;

        // dimensions
        for (int i = 1; i <= dims.Length(); ++i) {
            TDF_Label dl = dims.Value(i);
            Handle(XCAFDoc_Dimension) attr;
            if (!dl.FindAttribute(XCAFDoc_Dimension::GetID(), attr)) {
                continue;
            }
            Handle(XCAFDimTolObjects_DimensionObject) o = attr->GetObject();
            if (o.IsNull()) {
                continue;
            }
            Flags fl;
            bool known = true;
            std::string type = dimTypeName(o->GetType(), known);
            if (type == "DimensionPresentation" || type == "CommonLabel") {
                ++gReport.graphicOnly;  // graphic-only presentation: counted, kept as an annotation, not a semantic item
                auto* ann = doc->addObject<Part::PmiAnnotation>("PmiAnnotation");
                created.push_back(ann);
                Raw araw;
                ann->PmiType.setValue(type);
                ann->SemanticName.setValue(hstr(o->GetSemanticName()));
                araw["dim.type"] = type;
                araw["dim.semantic_name"] = hstr(o->GetSemanticName());
                araw["dim.presentation_name"] = hstr(o->GetPresentationName());
                araw["dim.has_text_point"] = o->HasTextPoint() ? "1" : "0";
                if (o->HasTextPoint()) {
                    rawPnt(araw, "dim.text_point", o->GetPointTextAttach());
                }
                araw["dim.has_plane"] = o->HasPlane() ? "1" : "0";
                if (o->HasPlane()) {
                    rawAx2(araw, "dim.plane", o->GetPlane());
                }
                araw["dim.descriptions"] = std::to_string(o->NbDescriptions());
                for (int k = 1; k <= o->NbDescriptions(); ++k) {
                    araw["dim.description." + std::to_string(k)] = hstr(o->GetDescription(k));
                    araw["dim.description_name." + std::to_string(k)] = hstr(o->GetDescriptionName(k));
                }
                if (!o->GetPresentation().IsNull()) {
                    ann->Presentation.setValue(o->GetPresentation());
                }
                {
                    // the kernel gives a presentation label the faces its callout points at; the writer needs them to write it
                    CommonResult ac = resolveCommon(dl, index, stash, araw);
                    std::vector<long> aids(ac.ids.begin(), ac.ids.end());
                    ann->StepFaceIds.setValues(aids);
                    setLinks(ann, ac.hits);
                    ann->Unattached.setValue(ac.hits.empty());
                }
                ann->RawAttributes.setValue(araw);
                ann->Label.setValue("Annotation " + hstr(o->GetPresentationName()));
                continue;
            }
            bool semantic = type.rfind("Size_", 0) == 0 || type.rfind("Location_", 0) == 0;
            if (!known || !semantic || testForceUnknown("dimension")) {
                fl.unknownType = true;
                fl.note("dimension type " + type);
            }
            auto* item = doc->addObject<Part::PmiDimension>("PmiDimension");
            created.push_back(item);
            Raw raw;
            item->PmiType.setValue(type);
            item->SemanticName.setValue(hstr(o->GetSemanticName()));
            item->Value.setValue(o->GetValue());
            raw["dim.type"] = type;
            raw["dim.semantic_name"] = hstr(o->GetSemanticName());
            raw["dim.value"] = num(o->GetValue());
            {
                auto vals = o->GetValues();
                std::vector<std::string> vs;
                if (!vals.IsNull()) {
                    for (int k = vals->Lower(); k <= vals->Upper(); ++k) {
                        vs.push_back(num(vals->Value(k)));
                    }
                }
                raw["dim.values"] = join(vs, ",");
            }
            if (o->HasQualifier()) {
                bool k2 = true;
                std::string q = dimQualifierName(o->GetQualifier(), k2);
                if (!k2) {
                    fl.unknownOther = true;
                    fl.note("qualifier " + q);
                }
                item->Qualifier.setValue(q);
                raw["dim.qualifier"] = q;
            }
            if (o->HasAngularQualifier()) {
                bool k2 = true;
                std::string q = angQualifierName(o->GetAngularQualifier(), k2);
                if (!k2) {
                    fl.unknownOther = true;
                    fl.note("angular qualifier " + q);
                }
                item->Qualifier.setValue(q);
                raw["dim.angular_qualifier"] = q;
            }
            raw["dim.is_range"] = o->IsDimWithRange() ? "1" : "0";
            raw["dim.is_plus_minus"] = o->IsDimWithPlusMinusTolerance() ? "1" : "0";
            if (o->IsDimWithRange()) {
                item->HasBounds.setValue(true);
                item->BoundsKind.setValue("range");
                item->UpperBound.setValue(o->GetUpperBound());
                item->LowerBound.setValue(o->GetLowerBound());
                raw["dim.upper_bound"] = num(o->GetUpperBound());
                raw["dim.lower_bound"] = num(o->GetLowerBound());
            }
            else if (o->IsDimWithPlusMinusTolerance()) {
                item->HasBounds.setValue(true);
                item->BoundsKind.setValue("plus_minus");
                // OCCT returns the lower deviation as a positive magnitude (a file that states -0.2 reads back
                // 0.2); the property holds the signed deviation as the file states it, so lower <= upper
                item->UpperBound.setValue(o->GetUpperTolValue());
                item->LowerBound.setValue(-o->GetLowerTolValue());
                raw["dim.upper_tol"] = num(o->GetUpperTolValue());
                raw["dim.lower_tol"] = num(o->GetLowerTolValue());
            }
            bool hole = false;
            XCAFDimTolObjects_DimensionFormVariance fv = XCAFDimTolObjects_DimensionFormVariance_None;
            XCAFDimTolObjects_DimensionGrade gr = XCAFDimTolObjects_DimensionGrade_IT01;
            raw["dim.is_class"] = o->IsDimWithClassOfTolerance() ? "1" : "0";
            if (o->GetClassOfTolerance(hole, fv, gr)) {
                bool k2 = true;
                std::string fvn = formVarianceName(fv, k2);
                std::string grn = gradeName(gr, k2);
                if (!k2) {
                    fl.unknownOther = true;
                    fl.note("class of tolerance " + fvn + grn);
                }
                raw["dim.class_hole"] = hole ? "1" : "0";
                raw["dim.class_variance"] = fvn;
                raw["dim.class_grade"] = grn;
                std::string cls = fvn == "None" ? std::string() : fvn;
                if (!hole) {
                    std::transform(cls.begin(), cls.end(), cls.begin(), [](unsigned char c) {
                        return static_cast<char>(std::tolower(c));
                    });
                }
                cls += grn.rfind("IT", 0) == 0 ? grn.substr(2) : grn;
                item->ToleranceClass.setValue(cls);
                if (!item->HasBounds.getValue()) {
                    item->BoundsKind.setValue("class");
                }
            }
            int l = 0, r = 0;
            o->GetNbOfDecimalPlaces(l, r);
            raw["dim.decimal_places"] = std::to_string(l) + "," + std::to_string(r);
            std::vector<std::string> mods;
            auto ms = o->GetModifiers();
            for (int k = 1; k <= ms.Length(); ++k) {
                bool k2 = true;
                std::string n = dimModifName(ms.Value(k), k2);
                if (!k2 || testForceUnknown("modifier")) {
                    fl.unknownOther = true;
                    fl.note("dimension modifier " + n);
                }
                mods.push_back(n);
            }
            item->Modifiers.setValues(mods);
            raw["dim.modifiers"] = join(mods, ",");
            gp_Dir dir;
            if (o->GetDirection(dir)) {
                raw["dim.direction"] = vec3(dir.X(), dir.Y(), dir.Z());
            }
            raw["dim.has_path"] = o->GetPath().IsNull() ? "0" : "1";
            raw["dim.has_text_point"] = o->HasTextPoint() ? "1" : "0";
            if (o->HasTextPoint()) {
                rawPnt(raw, "dim.text_point", o->GetPointTextAttach());
            }
            raw["dim.has_plane"] = o->HasPlane() ? "1" : "0";
            if (o->HasPlane()) {
                rawAx2(raw, "dim.plane", o->GetPlane());
            }
            if (o->HasPoint()) {
                rawPnt(raw, "dim.point1", o->GetPoint());
                raw["dim.point1_is_point"] = o->IsPointConnection() ? "1" : "0";
            }
            if (o->HasPoint2()) {
                rawPnt(raw, "dim.point2", o->GetPoint2());
                raw["dim.point2_is_point"] = o->IsPointConnection2() ? "1" : "0";
            }
            raw["dim.presentation_name"] = hstr(o->GetPresentationName());
            if (!o->GetPresentation().IsNull()) {
                item->Presentation.setValue(o->GetPresentation());
            }
            raw["dim.descriptions"] = std::to_string(o->NbDescriptions());
            for (int k = 1; k <= o->NbDescriptions(); ++k) {
                raw["dim.description." + std::to_string(k)] = hstr(o->GetDescription(k));
                raw["dim.description_name." + std::to_string(k)] = hstr(o->GetDescriptionName(k));
            }
            CommonResult c = resolveCommon(dl, index, stash, raw);
            std::vector<std::string> kl;
            checkDimension(item, o, c.extra, type, raw, kl);
            {
                // a dimensional location relates two shape aspects (CAx-IF PMI practice); when only one side reached the
                // document the item cannot be written back as a location, so say so (a dimension with no stated value is
                // flagged above and does not need this second entry)
                bool noValue = false;
                for (const auto& k : kl) {
                    noValue = noValue || k.rfind("lost: no value is stated", 0) == 0;
                }
                if (!noValue && type.rfind("Location_", 0) == 0 && !c.hits.empty() && raw["link.second"].empty()) {
                    kl.push_back("lost: only one side of this location dimension reached the document (the kernel gave no face for the "
                                 "second shape aspect); a dimensional location needs two, so it is not written on export");
                }
            }
            finishItem(item, c, raw, fl, "dimension", kl);
            ++gReport.dimensions;
            if (c.hits.empty()) {
                ++gReport.unattached;
                gReport.unattachedItems.push_back(std::string(item->getNameInDocument()) + " (" + type + " "
                                                  + label6(o->GetValue()) + ")");
            }
            bool noValue = false;
            for (const auto& k : kl) {
                noValue = noValue || k.rfind("lost: no value is stated", 0) == 0;
            }
            item->Label.setValue(
                noValue ? std::string("Dimension without a stated value")
                        + (item->StepEntityName.getStrValue().empty() ? std::string()
                                                                       : " (" + item->StepEntityName.getStrValue() + ")")
                        : type + " " + label6(item->Value.getValue())
            );
        }

        // tolerances, each followed by the datum uses it refers to
        for (int i = 1; i <= tols.Length(); ++i) {
            TDF_Label tl = tols.Value(i);
            Handle(XCAFDoc_GeomTolerance) attr;
            if (!tl.FindAttribute(XCAFDoc_GeomTolerance::GetID(), attr)) {
                continue;
            }
            Handle(XCAFDimTolObjects_GeomToleranceObject) o = attr->GetObject();
            if (o.IsNull()) {
                continue;
            }
            Flags fl;
            bool known = true;
            std::string type = tolTypeName(o->GetType(), known);
            if (!known || testForceUnknown("tolerance")) {
                fl.unknownType = true;
                fl.note("tolerance type " + type);
            }
            auto* item = doc->addObject<Part::PmiTolerance>("PmiTolerance");
            created.push_back(item);
            Raw raw;
            item->PmiType.setValue(type);
            item->SemanticName.setValue(hstr(o->GetSemanticName()));
            item->Value.setValue(o->GetValue());
            raw["tol.type"] = type;
            raw["tol.semantic_name"] = hstr(o->GetSemanticName());
            raw["tol.value"] = num(o->GetValue());
            {
                bool k2 = true;
                std::string z = zoneValueName(o->GetTypeOfValue(), k2);
                if (!k2) {
                    fl.unknownOther = true;
                    fl.note("zone shape " + z);
                }
                item->ZoneShape.setValue(z);
                raw["tol.type_of_value"] = z;
                std::string zm = zoneModifName(o->GetZoneModifier(), k2);
                if (!k2) {
                    fl.unknownOther = true;
                    fl.note("zone modifier " + zm);
                }
                item->ZoneModifier.setValue(zm);
                item->ZoneModifierValue.setValue(o->GetValueOfZoneModifier());
                raw["tol.zone_modifier"] = zm;
                raw["tol.zone_modifier_value"] = num(o->GetValueOfZoneModifier());
            }
            std::vector<std::string> mods;
            {
                bool k2 = true;
                std::string mr = matReqName(o->GetMaterialRequirementModifier(), k2);
                if (!k2) {
                    fl.unknownOther = true;
                    fl.note("material requirement " + mr);
                }
                raw["tol.material_requirement"] = mr;
                if (!mr.empty()) {
                    mods.push_back(mr);
                }
                auto ms = o->GetModifiers();
                std::vector<std::string> own;
                for (int k = 1; k <= ms.Length(); ++k) {
                    bool k3 = true;
                    std::string n = tolModifName(ms.Value(k), k3);
                    if (!k3 || testForceUnknown("modifier")) {
                        fl.unknownOther = true;
                        fl.note("tolerance modifier " + n);
                    }
                    own.push_back(n);
                    if (std::find(mods.begin(), mods.end(), n) == mods.end()) {
                        mods.push_back(n);
                    }
                }
                raw["tol.modifiers"] = join(own, ",");
            }
            item->Modifiers.setValues(mods);
            raw["tol.max_value_modifier"] = num(o->GetMaxValueModifier());
            raw["tol.has_axis"] = o->HasAxis() ? "1" : "0";
            if (o->HasAxis()) {
                rawAx2(raw, "tol.axis", o->GetAxis());
            }
            {
                bool k2 = true;
                raw["tol.affected_plane_type"] = affectedPlaneName(o->GetAffectedPlaneType(), k2);
                if (!k2) {
                    fl.unknownOther = true;
                    fl.note("affected plane type");
                }
                raw["tol.has_affected_plane"] = o->HasAffectedPlane() ? "1" : "0";
            }
            rawAx2(raw, "tol.plane", o->GetPlane());
            rawPnt(raw, "tol.point", o->GetPoint());
            rawPnt(raw, "tol.text_point", o->GetPointTextAttach());
            raw["tol.has_plane"] = o->HasPlane() ? "1" : "0";
            raw["tol.has_point"] = o->HasPoint() ? "1" : "0";
            raw["tol.has_text_point"] = o->HasPointText() ? "1" : "0";
            raw["tol.presentation_name"] = hstr(o->GetPresentationName());
            if (!o->GetPresentation().IsNull()) {
                item->Presentation.setValue(o->GetPresentation());
            }

            // datum uses in precedence order (OCCT's position: 1 primary, 2 secondary, ...)
            NCollection_Sequence<TDF_Label> dl;
            XCAFDoc_DimTolTool::GetDatumWithObjectOfTolerLabels(tl, dl);
            std::vector<std::pair<int, TDF_Label>> uses;
            for (int k = 1; k <= dl.Length(); ++k) {
                Handle(XCAFDoc_Datum) da;
                int pos = 0;
                if (dl.Value(k).FindAttribute(XCAFDoc_Datum::GetID(), da) && !da->GetObject().IsNull()) {
                    pos = da->GetObject()->GetPosition();
                }
                uses.emplace_back(pos, dl.Value(k));
            }
            std::stable_sort(uses.begin(), uses.end(), [](const auto& a, const auto& b) {
                return a.first < b.first;
            });
            std::vector<std::string> refs;
            std::vector<App::DocumentObject*> datumObjs;
            for (const auto& u : uses) {
                usedDatums.insert(entryOf(u.second));
                Part::PmiDatum* d = makeDatum(doc, u.second, index, stash, created);
                if (!d) {
                    continue;
                }
                datumObjs.push_back(d);
                ++gReport.datums;
                std::string ref = d->Letter.getValue();
                const auto& dm = d->Modifiers.getValues();
                if (!dm.empty()) {
                    ref += "(" + join(dm, ",") + ")";
                }
                refs.push_back(ref);
            }
            item->DatumReferences.setValues(refs);
            item->Datums.setValues(datumObjs);
            raw["tol.datum_references"] = join(refs, "|");
            CommonResult c = resolveCommon(tl, index, stash, raw);
            finishItem(item, c, raw, fl, "tolerance");
            ++gReport.tolerances;
            if (c.hits.empty()) {
                ++gReport.unattached;
                gReport.unattachedItems.push_back(std::string(item->getNameInDocument()) + " (" + type + " "
                                                  + label6(o->GetValue()) + ")");
            }
            std::string lab = type + " " + label6(o->GetValue());
            if (!refs.empty()) {
                lab += " | " + join(refs, " | ");
            }
            item->Label.setValue(lab);
        }

        // datum labels no tolerance refers to (datum targets, loose datums)
        for (int i = 1; i <= datums.Length(); ++i) {
            if (usedDatums.count(entryOf(datums.Value(i)))) {
                continue;
            }
            if (makeDatum(doc, datums.Value(i), index, stash, created)) {
                ++gReport.datums;
            }
        }

        bool graphicOnlyFile = gReport.dimensions + gReport.tolerances + gReport.datums == 0;
        if (graphicOnlyFile) {
            if (gReport.graphicOnly > 0) {
                warn("STEP PMI: only graphic PMI was found (" + std::to_string(gReport.graphicOnly)
                     + " presentation-only dimension labels); there are no semantic dimensions, tolerances or "
                       "datums to import; the graphic callouts are kept as annotations");
            }
            if (created.empty()) {
                return;
            }
        }
        if (pmiSwitch() == "fail") {
            throw Base::RuntimeError("FC_STEP_PMI=fail: test of the rollback");
        }
        auto* group = doc->addObject<Part::PmiGroup>("PMI");
        groupName = group->getNameInDocument();
        group->Label.setValue("PMI");
        group->addObjects(created);
        Base::Console().message(
            "STEP PMI: {} dimensions, {} tolerances, {} datum references imported ({} presentation-only "
            "labels not imported, {} unmapped, {} dimensions and tolerances attached to no face or edge, {} datums "
            "attached to no face, {} items where the STEP reader lost or altered something the file states)\n",
            gReport.dimensions,
            gReport.tolerances,
            gReport.datums,
            gReport.graphicOnly,
            gReport.unmapped,
            gReport.unattached,
            gReport.unattachedDatums,
            gReport.kernelLoss
        );
        if (gReport.kernelLoss > 0) {
            std::string list;
            for (const auto& k : gReport.kernelLossItems) {
                list += (list.empty() ? "" : "; ") + k;
            }
            warn("STEP PMI: " + std::to_string(gReport.kernelLoss)
                 + " item(s) carry a KernelLoss entry, where the STEP reader lost or altered something the file states ("
                 + list + ")");
        }
        if (!gReport.unattachedItems.empty()) {
            std::string list;
            for (const auto& s : gReport.unattachedItems) {
                list += (list.empty() ? "" : ", ") + s;
            }
            warn("STEP PMI: " + std::to_string(gReport.unattachedItems.size())
                 + " item(s) attach to no face or edge of the document and were kept unattached: " + list);
        }
    }
    catch (const Base::Exception& e) {
        rollback();
        warn(std::string("STEP PMI: import stopped, no PMI object was kept: ") + e.what());
    }
    catch (const Standard_Failure& e) {
        rollback();
        warn(std::string("STEP PMI: import stopped, no PMI object was kept: ") + e.GetMessageString());
    }
    catch (const std::exception& e) {
        rollback();
        warn(std::string("STEP PMI: import stopped, no PMI object was kept: ") + e.what());
    }
}


// =============================================================================================
// export: write the PMI items of the document back as AP242 semantic PMI (G12 slice 1b-ii)
//
// Each PmiDimension, PmiTolerance, PmiDatum and PmiAnnotation is rebuilt as the XCAF object OCCT's STEPCAFControl_Writer
// writes (XCAFDoc_DimTolTool: AddDimension/SetDimension, AddGeomTolerance/SetGeomTolerance, AddDatum/SetDatum/
// SetDatumToGeomTol) from the item's properties; RawAttributes only fills the fields the model has no property for
// (planes, points, decimal places, descriptions, names). The output follows the CAx-IF Recommended Practices for the
// Representation and Presentation of PMI (AP242): one DATUM per datum reference use, tessellated annotations for the
// presentation, values in the unit of the file (angles in radians, OCCT's writer assumes it).
// =============================================================================================

namespace
{

StepPmiExportReport gExport;

/// A size or location written with absolute limits: OCCT's writer states the nominal value as the middle of the limits,
/// so the nominal the item holds is put back in the file text (repairWrittenStepPmi). Values in the units of the file
/// (millimetres, radians).
struct RangeNominal
{
    double upper, lower, nominal;
    bool angular;
};
std::vector<RangeNominal> gRangeNominals;

void exportWarn(const std::string& msg)
{
    gExport.warnings.push_back(msg);
    Base::Console().warning("{}\n", msg);
}

template<class E, class F>
bool enumFromName(const std::string& name, F fn, E& out, int maxv = 64)
{
    for (int i = 0; i < maxv; ++i) {
        bool known = true;
        std::string n = fn(static_cast<E>(i), known);
        if (known && n == name) {
            out = static_cast<E>(i);
            return true;
        }
    }
    return false;
}

std::string rawGet(const Raw& raw, const std::string& key)
{
    auto it = raw.find(key);
    return it == raw.end() ? std::string() : it->second;
}

bool rawFlag(const Raw& raw, const std::string& key)
{
    return rawGet(raw, key) == "1";
}

double rawNum(const Raw& raw, const std::string& key, double dflt = 0.0)
{
    auto it = raw.find(key);
    if (it == raw.end() || it->second.empty()) {
        return dflt;
    }
    return std::strtod(it->second.c_str(), nullptr);
}

bool parseNums(const std::string& t, std::vector<double>& v)
{
    std::istringstream is(t);
    double d;
    while (is >> d) {
        v.push_back(d);
    }
    return !v.empty();
}

bool rawPoint(const Raw& raw, const std::string& key, gp_Pnt& p)
{
    std::vector<double> v;
    if (!parseNums(rawGet(raw, key), v) || v.size() != 3) {
        return false;
    }
    p = gp_Pnt(v[0], v[1], v[2]);
    return true;
}

bool rawAxis(const Raw& raw, const std::string& key, gp_Ax2& a)
{
    std::string t = rawGet(raw, key);
    std::vector<double> v;
    for (char& c : t) {
        if (c == '|') {
            c = ' ';
        }
    }
    if (!parseNums(t, v) || v.size() != 9) {
        return false;
    }
    try {
        a = gp_Ax2(gp_Pnt(v[0], v[1], v[2]), gp_Dir(v[3], v[4], v[5]), gp_Dir(v[6], v[7], v[8]));
    }
    catch (const Standard_Failure&) {
        return false;
    }
    return true;
}

Handle(TCollection_HAsciiString) hs(const std::string& s)
{
    return new TCollection_HAsciiString(s.c_str());
}

bool isAngular(const std::string& type)
{
    return type.find("Angular") != std::string::npos;
}

int countEdges(const TopoDS_Shape& s)
{
    int n = 0;
    for (TopExp_Explorer ex(s, TopAbs_EDGE); ex.More(); ex.Next()) {
        ++n;
    }
    return n;
}

/// Everything a pass needs to turn document objects into labels
struct ExportCtx
{
    Handle(TDocStd_Document) doc;
    Handle(XCAFDoc_ShapeTool) st;
    Handle(XCAFDoc_DimTolTool) dt;
    const ExportOCAF2& ocaf;
    std::map<std::string, TDF_Label> cache;
    int facePresentationStripped {0};
    std::vector<std::string> strippedNames;
};

/// The label of a sub-shape (FaceN or EdgeN) of an exported object, on the label that holds the object's own shape;
/// null label and a reason if there is none
TDF_Label subShapeLabel(ExportCtx& cx, App::DocumentObject* obj, const std::string& sub, std::string& why)
{
    std::string key = std::string(obj->getNameInDocument()) + "." + sub;
    auto cit = cx.cache.find(key);
    if (cit != cx.cache.end()) {
        return cit->second;
    }
    TDF_Label proto = cx.ocaf.exportedLabel(obj);
    if (proto.IsNull()) {
        why = std::string(obj->getNameInDocument()) + " is not part of the export";
        return TDF_Label();
    }
    TopAbs_ShapeEnum kind;
    int idx = 0;
    if (sub.rfind("Face", 0) == 0) {
        kind = TopAbs_FACE;
        idx = std::atoi(sub.c_str() + 4);
    }
    else if (sub.rfind("Edge", 0) == 0) {
        kind = TopAbs_EDGE;
        idx = std::atoi(sub.c_str() + 4);
    }
    else {
        why = "unsupported reference " + sub;
        return TDF_Label();
    }
    auto* feat = dynamic_cast<Part::Feature*>(obj);
    TopoDS_Shape protoShape = XCAFDoc_ShapeTool::GetShape(proto);
    if (!feat || protoShape.IsNull()) {
        why = std::string(obj->getNameInDocument()) + " has no shape in the export";
        return TDF_Label();
    }
    TopoDS_Shape orig;
    try {
        orig = feat->Shape.getShape().getSubShape(sub.c_str(), true);
    }
    catch (...) {
    }
    TopoDS_Shape pick;
    if (!orig.IsNull()) {
        // the exported shape keeps the TShapes of the object's own (location stripped), so a partner is the same face
        for (TopExp_Explorer ex(protoShape, kind); ex.More(); ex.Next()) {
            if (ex.Current().IsPartner(orig)) {
                pick = ex.Current();
                break;
            }
        }
    }
    if (pick.IsNull()) {
        // a copied shape (placement kept in the geometry): same structure, so the same index
        TopTools_IndexedMapOfShape m;
        TopExp::MapShapes(protoShape, kind, m);
        if (idx >= 1 && idx <= m.Extent()) {
            pick = m(idx);
        }
    }
    if (pick.IsNull()) {
        why = std::string(obj->getNameInDocument()) + "." + sub + " is not in the exported shape";
        return TDF_Label();
    }
    // the same work-around ExportOCAF2 uses for OCCT versions that do not set the shape map of a label
    Handle(XCAFDoc_ShapeMapTool) A;
    if (!proto.FindAttribute(XCAFDoc_ShapeMapTool::GetID(), A)) {
        A = XCAFDoc_ShapeMapTool::Set(proto);
        A->SetShape(protoShape);
    }
    TDF_Label l = cx.st->AddSubShape(proto, pick);
    if (l.IsNull()) {
        why = std::string(obj->getNameInDocument()) + "." + sub + " could not be added as a sub-shape";
        return TDF_Label();
    }
    cx.cache[key] = l;
    return l;
}

/// The labels for a list of "Object.FaceN" names; false and a reason if one cannot be made
bool labelsFor(
    ExportCtx& cx,
    App::Document* doc,
    const std::vector<std::string>& names,
    NCollection_Sequence<TDF_Label>& out,
    std::string& why
)
{
    for (const auto& n : names) {
        auto dot = n.rfind('.');
        if (dot == std::string::npos) {
            why = "bad reference " + n;
            return false;
        }
        App::DocumentObject* obj = doc->getObject(n.substr(0, dot).c_str());
        if (!obj) {
            why = "object " + n.substr(0, dot) + " is gone";
            return false;
        }
        TDF_Label l = subShapeLabel(cx, obj, n.substr(dot + 1), why);
        if (l.IsNull()) {
            return false;
        }
        out.Append(l);
    }
    return true;
}

std::vector<std::string> splitNames(const std::string& t)
{
    std::vector<std::string> v;
    std::string cur;
    for (char c : t) {
        if (c == ';') {
            if (!cur.empty()) {
                v.push_back(cur);
            }
            cur.clear();
        }
        else {
            cur += c;
        }
    }
    if (!cur.empty()) {
        v.push_back(cur);
    }
    return v;
}

/// The "Object.FaceN" names the item's References hold
std::vector<std::string> referenceNames(const Part::PmiItem* item)
{
    std::vector<std::string> v;
    for (App::DocumentObject* o : item->References.getValues()) {
        if (!o) {
            continue;
        }
        for (const auto& s : item->References.getSubValues(o)) {
            v.push_back(std::string(o->getNameInDocument()) + "." + s);
        }
    }
    return v;
}

/// The two lists of faces of a dimension: those recorded at import when they still are the References, else all in the first
void dimensionLists(
    const Part::PmiItem* item,
    const Raw& raw,
    std::vector<std::string>& first,
    std::vector<std::string>& second
)
{
    std::vector<std::string> all = referenceNames(item);
    if (rawGet(raw, "link.first").empty() && rawGet(raw, "link.second").empty() && !item->SecondSide.getValues().empty()) {
        // an authored location dimension (PmiAuthoring) names the faces of its second side; the rest of References is the first
        std::set<std::string> sec(item->SecondSide.getValues().begin(), item->SecondSide.getValues().end());
        for (const auto& n : all) {
            (sec.count(n) ? second : first).push_back(n);
        }
        return;
    }
    std::vector<std::string> a = splitNames(rawGet(raw, "link.first"));
    std::vector<std::string> b = splitNames(rawGet(raw, "link.second"));
    std::set<std::string> u(a.begin(), a.end());
    u.insert(b.begin(), b.end());
    if (u == std::set<std::string>(all.begin(), all.end())) {
        first = a;
        second = b;
    }
    else {
        first = all;
        second.clear();
    }
}

void setPresentation(
    ExportCtx& cx,
    const Part::PmiItem* item,
    const std::string& nameKey,
    const Raw& raw,
    const std::function<void(const TopoDS_Shape&, const Handle(TCollection_HAsciiString)&)>& put
)
{
    const TopoDS_Shape sh = item->Presentation.getShape().getShape();
    if (sh.IsNull()) {
        return;
    }
    if (countEdges(sh) == 0) {
        // OCCT's writer reports an error for a presentation that holds faces only (nothing to draw as a curve set)
        ++cx.facePresentationStripped;
        cx.strippedNames.push_back(item->getNameInDocument());
        return;
    }
    put(sh, hs(rawGet(raw, nameKey)));
}

/// The nominal written for a dimension that states no value (HasValue false): OCCT writes a nominal for every dimension, so the
/// item gets this marker and the repair pass takes the nominal out of the file again (see dropValuelessNominals).
constexpr double kNoValueMarker = 1.2345678901234e-123;

Handle(XCAFDimTolObjects_DimensionObject) buildDimension(
    ExportCtx& cx,
    const Part::PmiItem* item,
    const Raw& raw,
    const std::string& tolClass,
    std::string& why
)
{
    XCAFDimTolObjects_DimensionType type;
    if (item->Unmapped.getValue()
        || !enumFromName(item->PmiType.getStrValue(), dimTypeName, type)) {
        why = "its type " + item->PmiType.getStrValue() + " is not one the writer knows";
        return nullptr;
    }
    Handle(XCAFDimTolObjects_DimensionObject) o = new XCAFDimTolObjects_DimensionObject();
    o->SetType(type);
    o->SetSemanticName(hs(item->SemanticName.getStrValue()));
    const double k = isAngular(item->PmiType.getStrValue()) ? std::acos(-1.0) / 180.0 : 1.0;
    // SetValue resets the values array and with it the bounds, so the value comes first; an item with no stated value
    // (HasValue false, never with bounds) gets the marker nominal, which the repair pass removes from the file again
    const bool valueless = !item->HasValue.getValue() && !item->HasBounds.getValue();
    o->SetValue(valueless ? kNoValueMarker : item->Value.getValue() * k);
    if (item->HasBounds.getValue()) {
        if (item->BoundsKind.getStrValue() == "range") {
            o->SetUpperBound(item->UpperBound.getValue() * k);
            o->SetLowerBound(item->LowerBound.getValue() * k);
        }
        else if (item->BoundsKind.getStrValue() == "plus_minus") {
            // OCCT keeps the lower deviation as a magnitude
            o->SetUpperTolValue(item->UpperBound.getValue() * k);
            o->SetLowerTolValue(-item->LowerBound.getValue() * k);
        }
    }
    if (!tolClass.empty()) {
        std::string letters, digits;
        for (char c : tolClass) {
            (std::isdigit(static_cast<unsigned char>(c)) ? digits : letters) += c;
        }
        bool hole = !letters.empty() && std::isupper(static_cast<unsigned char>(letters[0]));
        std::string up = letters;
        std::transform(up.begin(), up.end(), up.begin(), [](unsigned char c) { return static_cast<char>(std::toupper(c)); });
        XCAFDimTolObjects_DimensionFormVariance fv = XCAFDimTolObjects_DimensionFormVariance_None;
        XCAFDimTolObjects_DimensionGrade gr;
        bool okFv = up.empty() || enumFromName(up, formVarianceName, fv);
        bool okGr = !digits.empty() && enumFromName("IT" + digits, gradeName, gr);
        if (okFv && okGr) {
            o->SetClassOfTolerance(hole, fv, gr);
        }
    }
    const std::string q = item->Qualifier.getStrValue();
    if (!q.empty()) {
        XCAFDimTolObjects_DimensionQualifier dq;
        XCAFDimTolObjects_AngularQualifier aq;
        if (enumFromName(q, dimQualifierName, dq)) {
            o->SetQualifier(dq);
        }
        else if (enumFromName(q, angQualifierName, aq)) {
            o->SetAngularQualifier(aq);
        }
    }
    NCollection_Sequence<XCAFDimTolObjects_DimensionModif> mods;
    for (const auto& m : item->Modifiers.getValues()) {
        XCAFDimTolObjects_DimensionModif dm;
        if (enumFromName(m, dimModifName, dm)) {
            mods.Append(dm);
        }
    }
    o->SetModifiers(mods);
    std::string dp = rawGet(raw, "dim.decimal_places");
    if (!dp.empty()) {
        int l = 0, r = 0;
        if (std::sscanf(dp.c_str(), "%d,%d", &l, &r) == 2) {
            o->SetNbOfDecimalPlaces(l, r);
        }
    }
    std::vector<double> dv;
    if (parseNums(rawGet(raw, "dim.direction"), dv) && dv.size() == 3) {
        try {
            o->SetDirection(gp_Dir(dv[0], dv[1], dv[2]));
        }
        catch (const Standard_Failure&) {
        }
    }
    gp_Pnt p;
    gp_Ax2 ax;
    if (rawFlag(raw, "dim.has_text_point") && rawPoint(raw, "dim.text_point", p)) {
        o->SetPointTextAttach(p);
    }
    if (rawFlag(raw, "dim.has_plane") && rawAxis(raw, "dim.plane", ax)) {
        o->SetPlane(ax);
    }
    if (rawPoint(raw, "dim.point1", p)) {
        o->SetPoint(p);
    }
    if (rawPoint(raw, "dim.point2", p)) {
        o->SetPoint2(p);
    }
    int nd = std::atoi(rawGet(raw, "dim.descriptions").c_str());
    for (int i = 1; i <= nd; ++i) {
        o->AddDescription(
            hs(rawGet(raw, "dim.description." + std::to_string(i))),
            hs(rawGet(raw, "dim.description_name." + std::to_string(i)))
        );
    }
    setPresentation(cx, item, "dim.presentation_name", raw, [&](const TopoDS_Shape& s, const Handle(TCollection_HAsciiString)& n) {
        o->SetPresentation(s, n);
    });
    return o;
}

Handle(XCAFDimTolObjects_GeomToleranceObject) buildTolerance(
    ExportCtx& cx,
    const Part::PmiTolerance* item,
    const Raw& raw,
    std::string& why
)
{
    XCAFDimTolObjects_GeomToleranceType type;
    if (item->Unmapped.getValue() || !enumFromName(item->PmiType.getStrValue(), tolTypeName, type)) {
        why = "its type " + item->PmiType.getStrValue() + " is not one the writer knows";
        return nullptr;
    }
    Handle(XCAFDimTolObjects_GeomToleranceObject) o = new XCAFDimTolObjects_GeomToleranceObject();
    o->SetType(type);
    o->SetSemanticName(hs(item->SemanticName.getStrValue()));
    o->SetValue(item->Value.getValue());
    XCAFDimTolObjects_GeomToleranceTypeValue tv;
    if (enumFromName(item->ZoneShape.getStrValue(), zoneValueName, tv)) {
        o->SetTypeOfValue(tv);
    }
    XCAFDimTolObjects_GeomToleranceZoneModif zm;
    if (enumFromName(item->ZoneModifier.getStrValue(), zoneModifName, zm)) {
        o->SetZoneModifier(zm);
        o->SetValueOfZoneModifier(item->ZoneModifierValue.getValue());
    }
    // item modifiers hold the material requirement first, then the other modifiers; a modifier the file lists among the
    // object's own modifiers is written there too
    std::set<std::string> own;
    {
        std::string t = rawGet(raw, "tol.modifiers");
        std::string cur;
        for (char c : t + ",") {
            if (c == ',') {
                if (!cur.empty()) {
                    own.insert(cur);
                }
                cur.clear();
            }
            else {
                cur += c;
            }
        }
    }
    NCollection_Sequence<XCAFDimTolObjects_GeomToleranceModif> mods;
    for (const auto& m : item->Modifiers.getValues()) {
        XCAFDimTolObjects_GeomToleranceMatReqModif mr;
        XCAFDimTolObjects_GeomToleranceModif tm;
        bool isMat = (m == "MAXIMUM_MATERIAL_REQUIREMENT" || m == "LEAST_MATERIAL_REQUIREMENT")
            && enumFromName(m, matReqName, mr);
        if (isMat) {
            o->SetMaterialRequirementModifier(mr);
        }
        if ((!isMat || own.count(m)) && enumFromName(m, tolModifName, tm)) {
            mods.Append(tm);
        }
    }
    o->SetModifiers(mods);
    o->SetMaxValueModifier(rawNum(raw, "tol.max_value_modifier"));
    gp_Pnt p;
    gp_Ax2 ax;
    if (rawFlag(raw, "tol.has_axis") && rawAxis(raw, "tol.axis", ax)) {
        o->SetAxis(ax);
    }
    if (rawFlag(raw, "tol.has_plane") && rawAxis(raw, "tol.plane", ax)) {
        o->SetPlane(ax);
    }
    if (rawFlag(raw, "tol.has_point") && rawPoint(raw, "tol.point", p)) {
        o->SetPoint(p);
    }
    if (rawFlag(raw, "tol.has_text_point") && rawPoint(raw, "tol.text_point", p)) {
        o->SetPointTextAttach(p);
    }
    XCAFDimTolObjects_ToleranceZoneAffectedPlane ap;
    if (enumFromName(rawGet(raw, "tol.affected_plane_type"), affectedPlaneName, ap)) {
        o->SetAffectedPlaneType(ap);
    }
    setPresentation(cx, item, "tol.presentation_name", raw, [&](const TopoDS_Shape& s, const Handle(TCollection_HAsciiString)& n) {
        o->SetPresentation(s, n);
    });
    return o;
}

Handle(XCAFDimTolObjects_DatumObject) buildDatum(ExportCtx& cx, const Part::PmiDatum* item, const Raw& raw)
{
    Handle(XCAFDimTolObjects_DatumObject) o = new XCAFDimTolObjects_DatumObject();
    o->SetName(hs(rawGet(raw, "datum.object_name").empty() ? item->Letter.getStrValue() : rawGet(raw, "datum.object_name")));
    o->SetSemanticName(hs(item->SemanticName.getStrValue()));
    o->SetPosition(item->Position.getValue());
    NCollection_Sequence<XCAFDimTolObjects_DatumSingleModif> mods;
    XCAFDimTolObjects_DatumModifWithValue withValue = XCAFDimTolObjects_DatumModifWithValue_None;
    for (const auto& m : item->Modifiers.getValues()) {
        XCAFDimTolObjects_DatumSingleModif dm;
        XCAFDimTolObjects_DatumModifWithValue vm;
        if (enumFromName(m, datumModifName, dm)) {
            mods.Append(dm);
        }
        else if (enumFromName(m, datumValueModifName, vm)) {
            withValue = vm;
        }
    }
    o->SetModifiers(mods);
    if (withValue != XCAFDimTolObjects_DatumModifWithValue_None) {
        o->SetModifierWithValue(withValue, rawNum(raw, "datum.modifier_value"));
    }
    if (item->IsTarget.getValue()) {
        o->IsDatumTarget(true);
        XCAFDimTolObjects_DatumTargetType tt;
        if (enumFromName(rawGet(raw, "datum.target_type"), targetTypeName, tt)) {
            o->SetDatumTargetType(tt);
        }
        o->SetDatumTargetNumber(std::atoi(rawGet(raw, "datum.target_number").c_str()));
        o->SetDatumTargetLength(rawNum(raw, "datum.target_length"));
        o->SetDatumTargetWidth(rawNum(raw, "datum.target_width"));
        gp_Ax2 ax;
        if (rawAxis(raw, "datum.target_axis", ax)) {
            o->SetDatumTargetAxis(ax);
        }
    }
    gp_Pnt p;
    gp_Ax2 ax;
    if (rawFlag(raw, "datum.has_plane") && rawAxis(raw, "datum.plane", ax)) {
        o->SetPlane(ax);
    }
    if (rawFlag(raw, "datum.has_point") && rawPoint(raw, "datum.point", p)) {
        o->SetPoint(p);
    }
    if (rawFlag(raw, "datum.has_text_point") && rawPoint(raw, "datum.text_point", p)) {
        o->SetPointTextAttach(p);
    }
    setPresentation(cx, item, "datum.presentation_name", raw, [&](const TopoDS_Shape& s, const Handle(TCollection_HAsciiString)& n) {
        o->SetPresentation(s, n);
    });
    return o;
}

/// One datum label for one use; the faces come from the item's References. Empty label if it cannot be made
TDF_Label writeDatum(ExportCtx& cx, Part::PmiDatum* d, std::string& why)
{
    Raw raw = d->RawAttributes.getValues();
    NCollection_Sequence<TDF_Label> faces;
    if (!labelsFor(cx, d->getDocument(), referenceNames(d), faces, why)) {
        return TDF_Label();
    }
    Handle(XCAFDimTolObjects_DatumObject) obj = buildDatum(cx, d, raw);
    TDF_Label l = cx.dt->AddDatum(
        hs(rawGet(raw, "datum.name").empty() ? d->Letter.getStrValue() : rawGet(raw, "datum.name")),
        hs(rawGet(raw, "datum.description")),
        hs(d->Letter.getStrValue())
    );
    XCAFDoc_Datum::Set(l)->SetObject(obj);
    if (faces.Length() > 0) {
        cx.dt->SetDatum(faces, l);
    }
    return l;
}

}  // namespace

const StepPmiExportReport& lastStepPmiExportReport()
{
    return gExport;
}

bool stepPmiWriteEnabled()
{
    Base::Reference<ParameterGrp> grp = App::GetApplication().GetParameterGroupByPath(
        "User parameter:BaseApp/Preferences/Mod/Part/STEP"
    );
    return grp->GetBool("WritePmi", true);
}


/// OCCT writes one DATUM_SYSTEM (with its own compartments) per tolerance frame. Frames that name the same datums with the
/// same modifiers state the same datum reference frame, which source files (the NIST test models) write once and share
/// (CAx-IF PMI recommended practice, datum systems). Merges the systems whose compartments are identical in the file text:
/// the duplicate and its compartments are dropped and the frames refer to the first. Returns the number of systems removed.
static int mergeIdenticalDatumSystems(std::string& text)
{
    static const std::regex comp(R"(\n#(\d+)\s*=\s*DATUM_REFERENCE_COMPARTMENT\(([^;]*)\);)");
    static const std::regex sys(R"(\n#(\d+)\s*=\s*DATUM_SYSTEM\(([^;]*?)\(\s*(#\d+(?:\s*,\s*#\d+)*)\s*\)\s*\);)");
    std::map<std::string, std::string> compArgs;
    for (std::sregex_iterator it(text.begin(), text.end(), comp), end; it != end; ++it) {
        std::string a = (*it)[2].str();
        a.erase(std::remove_if(a.begin(), a.end(), [](unsigned char c) { return std::isspace(c); }), a.end());
        compArgs[(*it)[1].str()] = a;
    }
    struct Sys
    {
        std::string id, sig;
        std::vector<std::string> comps;
    };
    std::vector<Sys> systems;
    for (std::sregex_iterator it(text.begin(), text.end(), sys), end; it != end; ++it) {
        Sys sy;
        sy.id = (*it)[1].str();
        std::string head = (*it)[2].str();
        head.erase(std::remove_if(head.begin(), head.end(), [](unsigned char c) { return std::isspace(c); }), head.end());
        std::string list = (*it)[3].str();
        sy.sig = head + "|";
        bool ok = true;
        static const std::regex idr(R"(#(\d+))");
        for (std::sregex_iterator c(list.begin(), list.end(), idr), ce; c != ce; ++c) {
            auto f = compArgs.find((*c)[1].str());
            if (f == compArgs.end()) {
                ok = false;
                break;
            }
            sy.comps.push_back((*c)[1].str());
            sy.sig += f->second + "|";
        }
        if (ok) {
            systems.push_back(sy);
        }
    }
    auto uses = [&](const std::string& id) {
        int n = 0;
        const std::string tok = "#" + id;
        for (size_t p = text.find(tok); p != std::string::npos; p = text.find(tok, p + 1)) {
            if (p + tok.size() >= text.size() || !std::isdigit(static_cast<unsigned char>(text[p + tok.size()]))) {
                ++n;
            }
        }
        return n;
    };
    std::map<std::string, std::string> keeper;
    std::map<std::string, std::string> replace;
    std::vector<Sys> drop;
    for (const auto& sy : systems) {
        auto k = keeper.find(sy.sig);
        if (k == keeper.end()) {
            keeper[sy.sig] = sy.id;
            continue;
        }
        bool exclusive = true;
        for (const auto& c : sy.comps) {
            exclusive = exclusive && uses(c) == 2;  // its definition and the list of the system
        }
        if (exclusive) {
            replace[sy.id] = k->second;
            drop.push_back(sy);
        }
    }
    for (const auto& sy : drop) {
        auto removeDef = [&](const std::string& id, const char* kind) {
            const std::regex def("\\n#" + id + "\\s*=\\s*" + kind + "\\([^;]*\\);");
            text = std::regex_replace(text, def, "");
        };
        removeDef(sy.id, "DATUM_SYSTEM");
        for (const auto& c : sy.comps) {
            removeDef(c, "DATUM_REFERENCE_COMPARTMENT");
        }
    }
    for (const auto& r : replace) {
        const std::regex ref("#" + r.first + R"((?!\d))");
        text = std::regex_replace(text, ref, "#" + r.second);
    }
    return static_cast<int>(drop.size());
}


/// Takes the nominal out of the dimensions written with kNoValueMarker: the measure entity, the SHAPE_DIMENSION_REPRESENTATION
/// that holds it and the DIMENSIONAL_CHARACTERISTIC_REPRESENTATION that links it, so the dimension entity stands alone as the
/// NIST files write a size or location with no stated value. Returns the number of dimensions treated.
static int dropValuelessNominals(std::string& text)
{
    // Complex measure instances list their partial entities in alphabetical order, so a length nominal reads
    // LENGTH_MEASURE_WITH_UNIT() MEASURE_REPRESENTATION_ITEM() MEASURE_WITH_UNIT(...) and an angle nominal
    // MEASURE_REPRESENTATION_ITEM() MEASURE_WITH_UNIT(...) PLANE_ANGLE_MEASURE_WITH_UNIT(): match any order
    static const std::regex measure(R"(\n(#\d+)\s*=\s*\(([^;]*)\)\s*;)");
    static const std::regex value(R"(MEASURE_WITH_UNIT\(\s*[A-Z_]+\(\s*([-+0-9.eE]+)\s*\))");
    std::vector<std::string> measures;
    for (std::sregex_iterator it(text.begin(), text.end(), measure), end; it != end; ++it) {
        const std::string body = (*it)[2].str();
        if (body.find("MEASURE_REPRESENTATION_ITEM") == std::string::npos
            || body.find("'nominal value'") == std::string::npos) {
            continue;
        }
        std::smatch vm;
        if (!std::regex_search(body, vm, value)) {
            continue;
        }
        const double v = std::strtod(vm[1].str().c_str(), nullptr);
        if (std::fabs(v - kNoValueMarker) <= 1e-9 * kNoValueMarker) {
            measures.push_back((*it)[1].str());
        }
    }
    int n = 0;
    for (const auto& m : measures) {
        const std::regex sdr("\\n(#\\d+)\\s*=\\s*SHAPE_DIMENSION_REPRESENTATION\\(\\s*'[^']*'\\s*,\\s*\\(\\s*" + m + "\\s*\\)[^;]*;");
        std::smatch sm;
        std::string rep;
        if (std::regex_search(text, sm, sdr)) {
            rep = sm[1].str();
            text.erase(sm.position(0), sm.length(0));
            const std::regex dcr("\\n#\\d+\\s*=\\s*DIMENSIONAL_CHARACTERISTIC_REPRESENTATION\\(\\s*#\\d+\\s*,\\s*" + rep + "\\s*\\)\\s*;");
            std::smatch dm;
            if (std::regex_search(text, dm, dcr)) {
                text.erase(dm.position(0), dm.length(0));
            }
        }
        const std::regex me("\\n" + m + "\\s*=\\s*\\([^;]*;");
        std::smatch mm;
        if (std::regex_search(text, mm, me)) {
            text.erase(mm.position(0), mm.length(0));
        }
        ++n;
    }
    return n;
}

/// Puts the nominal value of the dimensions written with absolute limits back (see RangeNominal). A triple of 'nominal value',
/// 'lower limit' and 'upper limit' items whose limits belong to items that agree on one nominal gets that nominal; limits
/// shared by items that disagree are left as written, with a warning. Returns the number of nominals restated.
static int restateRangeNominals(std::string& text)
{
    if (gRangeNominals.empty()) {
        return 0;
    }
    static const std::regex sdr(R"(SHAPE_DIMENSION_REPRESENTATION\(\s*'[^']*'\s*,\s*\(\s*(#\d+)\s*,\s*(#\d+)\s*,\s*(#\d+)\s*\))");
    auto number = [&](const std::string& id, const std::string& name, double& v, size_t& numPos, size_t& numLen) {
        const std::string def = "\n" + id + " = ( ";
        size_t p = text.find(def);
        if (p == std::string::npos) {
            return false;
        }
        size_t e = text.find(';', p);
        const std::string ent = text.substr(p, e - p);
        if (ent.find("'" + name + "'") == std::string::npos) {
            return false;
        }
        static const std::regex mw(R"(MEASURE_WITH_UNIT\(\s*[A-Z_]+\(\s*([-+0-9.eE]+)\s*\))");
        std::smatch m;
        if (!std::regex_search(ent, m, mw)) {
            return false;
        }
        v = std::strtod(m[1].str().c_str(), nullptr);
        numPos = p + m.position(1);
        numLen = m[1].length();
        return true;
    };
    struct Edit
    {
        size_t pos, len;
        std::string txt;
    };
    std::vector<Edit> edits;
    int ambiguous = 0;
    for (std::sregex_iterator it(text.begin(), text.end(), sdr), end; it != end; ++it) {
        double nom = 0, lo = 0, up = 0;
        size_t pn = 0, ln = 0, pl = 0, ll = 0, pu = 0, lu = 0;
        if (!number((*it)[1].str(), "nominal value", nom, pn, ln) || !number((*it)[2].str(), "lower limit", lo, pl, ll)
            || !number((*it)[3].str(), "upper limit", up, pu, lu)) {
            continue;
        }
        std::vector<double> want;
        for (const auto& r : gRangeNominals) {
            const double tol = 1e-9 * std::max(1.0, std::fabs(up));
            if (std::fabs(r.upper - up) <= tol && std::fabs(r.lower - lo) <= tol) {
                want.push_back(r.nominal);
            }
        }
        if (want.empty()) {
            continue;
        }
        if (std::any_of(want.begin(), want.end(), [&](double w) { return std::fabs(w - want.front()) > 1e-12 * std::max(1.0, std::fabs(w)); })) {
            ++ambiguous;
            continue;
        }
        if (std::fabs(want.front() - nom) <= 1e-12 * std::max(1.0, std::fabs(nom))) {
            continue;
        }
        std::ostringstream os;
        os.precision(15);
        os << want.front();
        std::string t = os.str();
        if (t.find_first_of(".eE") == std::string::npos) {
            t += ".";
        }
        edits.push_back({pn, ln, t});
    }
    std::sort(edits.begin(), edits.end(), [](const Edit& a, const Edit& b) { return a.pos > b.pos; });
    for (const auto& e : edits) {
        text.replace(e.pos, e.len, e.txt);
    }
    if (ambiguous > 0) {
        exportWarn("STEP PMI: " + std::to_string(ambiguous) + " dimension(s) with absolute limits share their limits with a dimension of another nominal value; their nominal was written as the middle of the limits");
    }
    return static_cast<int>(edits.size());
}

int repairWrittenStepPmi(const std::string& path)
{
    if (!gExport.ran) {
        return 0;
    }
    try {
        std::string text;
        {
            std::ifstream in(path, std::ios::binary);
            std::ostringstream ss;
            ss << in.rdbuf();
            text = ss.str();
        }
        // A common datum (the datums H-J of one compartment): OCCT writes the modifiers of a DATUM_REFERENCE_ELEMENT as bare
        // enumeration values and the COMMON_DATUM_LIST as a flat argument list. ISO 10303-242 types both: the modifiers are
        // SIMPLE_DATUM_REFERENCE_MODIFIER selects and the list is one list argument, as the source files write them. Without
        // the wrappers a reader sees the elements but not their modifiers (MMR is lost).
        {
            static const std::regex elem(
                R"((DATUM_REFERENCE_ELEMENT\([^;]*?,\s*#\d+\s*,\s*)\(\s*(\.[A-Z_]+\.(?:\s*,\s*\.[A-Z_]+\.)*)\s*\)\s*\))"
            );
            static const std::regex enumv(R"(\.[A-Z_]+\.)");
            std::string out;
            auto last = text.cbegin();
            for (std::sregex_iterator it(text.begin(), text.end(), elem), end; it != end; ++it) {
                out.append(last, text.cbegin() + it->position());
                std::string mods = (*it)[2].str();
                std::string wrapped;
                for (std::sregex_iterator e(mods.begin(), mods.end(), enumv), ee; e != ee; ++e) {
                    wrapped += (wrapped.empty() ? "" : ",") + std::string("SIMPLE_DATUM_REFERENCE_MODIFIER(") + e->str() + ")";
                }
                out += (*it)[1].str() + "(" + wrapped + "))";
                last = text.cbegin() + it->position() + it->length();
                ++gExport.commonDatumsRepaired;
            }
            out.append(last, text.cend());
            text = out;
            static const std::regex lst(R"(COMMON_DATUM_LIST\(\s*(#\d+(?:\s*,\s*#\d+)*)\s*\))");
            const std::string before = text;
            text = std::regex_replace(text, lst, "COMMON_DATUM_LIST((" "$1" "))");
            if (text != before) {
                ++gExport.commonDatumsRepaired;
            }
        }
        const int mergedSystems = mergeIdenticalDatumSystems(text) + restateRangeNominals(text) + dropValuelessNominals(text);
        if (gExport.allAround.empty()) {
            if (gExport.commonDatumsRepaired > 0 || mergedSystems > 0) {
                std::ofstream outf(path, std::ios::binary | std::ios::trunc);
                outf << text;
            }
            return 0;
        }
        // Pair the tolerances of the file with the ones written. The writer's tolerance entities come in write order but not in
        // one form (a tolerance with datums is a complex GEOMETRIC_TOLERANCE_WITH_DATUM_REFERENCE instance, one without is a plain
        // FLATNESS_TOLERANCE(...)), and nothing in them names the item. So the file's sequence of (kind, datum or not, magnitude)
        // must equal the written sequence exactly, and within a run of identical entries the all-around flag must be the same;
        // otherwise the pairing is not proved and the entities stay as OCCT wrote them, with a warning.
        static const std::map<std::string, std::string> kinds = {
            {"Flatness", "FLATNESS_TOLERANCE"}, {"Straightness", "STRAIGHTNESS_TOLERANCE"},
            {"CircularityOrRoundness", "ROUNDNESS_TOLERANCE"}, {"Cylindricity", "CYLINDRICITY_TOLERANCE"},
            {"ProfileOfLine", "LINE_PROFILE_TOLERANCE"}, {"ProfileOfSurface", "SURFACE_PROFILE_TOLERANCE"},
            {"Position", "POSITION_TOLERANCE"}, {"Perpendicularity", "PERPENDICULARITY_TOLERANCE"},
            {"Parallelism", "PARALLELISM_TOLERANCE"}, {"Angularity", "ANGULARITY_TOLERANCE"},
            {"Concentricity", "CONCENTRICITY_TOLERANCE"}, {"Coaxiality", "COAXIALITY_TOLERANCE"},
            {"Symmetry", "SYMMETRY_TOLERANCE"}, {"CircularRunout", "CIRCULAR_RUNOUT_TOLERANCE"},
            {"TotalRunout", "TOTAL_RUNOUT_TOLERANCE"}};
        static const std::regex measure(R"(#(\d+)\s*=\s*LENGTH_MEASURE_WITH_UNIT\(\s*LENGTH_MEASURE\(([^)]*)\))");
        static const std::regex entity(R"((?:^|\n)#(\d+)\s*=\s*([^;]*);)");
        static const std::regex args(R"(\(\s*'(?:[^']|'')*'\s*,\s*'(?:[^']|'')*'\s*,\s*#(\d+)\s*,\s*#(\d+)\s*\))");
        std::map<std::string, double> lengths;
        for (std::sregex_iterator it(text.begin(), text.end(), measure), end; it != end; ++it) {
            lengths[(*it)[1].str()] = std::strtod((*it)[2].str().c_str(), nullptr);
        }
        struct Found
        {
            std::string kind, aspect;
            bool datum;
            double value;
            std::string letters;
        };
        std::map<std::string, std::string> bodies;
        for (std::sregex_iterator it(text.begin(), text.end(), entity), end; it != end; ++it) {
            bodies[(*it)[1].str()] = (*it)[2].str();
        }
        // the datum letters a datum system lists, in order: a system holds compartments, a compartment a datum or a common list of
        // elements, an element a datum (the first #id of each is the product definition shape)
        std::function<std::string(const std::string&, int)> letters = [&](const std::string& id, int depth) -> std::string {
            auto b = bodies.find(id);
            if (b == bodies.end() || depth > 6) {
                return "?";
            }
            const std::string& body = b->second;
            if (body.rfind("DATUM(", 0) == 0 || body.find("DATUM(") == 0) {
                auto q2 = body.rfind('\'');
                auto q1 = q2 == std::string::npos || q2 == 0 ? std::string::npos : body.rfind('\'', q2 - 1);
                return q1 == std::string::npos ? "?" : body.substr(q1 + 1, q2 - q1 - 1);
            }
            std::string out;
            bool first = true;
            static const std::regex idref(R"(#(\d+))");
            for (std::sregex_iterator r(body.begin(), body.end(), idref), e; r != e; ++r) {
                if (first) {
                    first = false;
                    continue;
                }
                out += (out.empty() ? "" : ",") + letters((*r)[1].str(), depth + 1);
            }
            return out;
        };
        static const std::regex dref(R"(GEOMETRIC_TOLERANCE_WITH_DATUM_REFERENCE\(\(\s*#(\d+))");
        std::vector<Found> found;
        for (std::sregex_iterator it(text.begin(), text.end(), entity), end; it != end; ++it) {
            const std::string body = (*it)[2].str();
            for (const auto& kv : kinds) {
                if (body.find(kv.second + "(") == std::string::npos) {
                    continue;
                }
                // the plain form carries the arguments on the kind itself, the complex one on GEOMETRIC_TOLERANCE
                std::string head = body.find("GEOMETRIC_TOLERANCE(") != std::string::npos ? "GEOMETRIC_TOLERANCE" : kv.second;
                auto pos = body.find(head + "(");
                std::smatch m;
                std::string rest = body.substr(pos + head.size());
                if (std::regex_search(rest, m, args, std::regex_constants::match_continuous) && lengths.count(m[1].str())) {
                    std::string let;
                    std::smatch dm;
                    if (std::regex_search(body, dm, dref)) {
                        let = letters(dm[1].str(), 0);
                        let.erase(std::remove(let.begin(), let.end(), '('), let.end());
                    }
                    found.push_back({kv.second, m[2].str(), body.find("GEOMETRIC_TOLERANCE_WITH_DATUM_REFERENCE") != std::string::npos,
                                     lengths[m[1].str()], let});
                }
                break;
            }
        }
        const auto& vals = gExport.writtenToleranceValues;
        const auto& wkinds = gExport.writtenToleranceKinds;
        const auto& wdat = gExport.writtenToleranceDatum;
        const auto& wlet = gExport.writtenToleranceLetters;
        std::set<int> wanted(gExport.allAround.begin(), gExport.allAround.end());
        bool proved = found.size() == vals.size();
        for (size_t i = 0; proved && i < vals.size(); ++i) {
            auto k = kinds.find(wkinds[i]);
            proved = k != kinds.end() && k->second == found[i].kind && wdat[i] == found[i].datum && wlet[i] == found[i].letters
                && std::fabs(vals[i] - found[i].value) <= 1e-6 * std::max(1.0, std::fabs(vals[i]));
        }
        // Identical tolerances (same kind, datums and magnitude) cannot be told apart by order. Only a composite shape aspect can
        // be all-around, so when a group of identical ones disagrees on all-around, it is settled only if as many of them have
        // a COMPOSITE_SHAPE_ASPECT as are all-around; then those are the ones. Otherwise they stay as OCCT wrote them.
        auto sameTol = [&](size_t i, size_t j) {
            return found[i].kind == found[j].kind && found[i].datum == found[j].datum && found[i].letters == found[j].letters
                && std::fabs(vals[i] - vals[j]) <= 1e-6 * std::max(1.0, std::fabs(vals[i]));
        };
        auto composite = [&](size_t i) {
            auto b = bodies.find(found[i].aspect);
            return b != bodies.end() && b->second.rfind("COMPOSITE_SHAPE_ASPECT(", 0) == 0;
        };
        std::set<int> unproved, target = wanted;
        for (size_t i = 0; proved && i < vals.size(); ++i) {
            std::vector<size_t> group;
            int nWanted = 0;
            bool mixed = false;
            for (size_t j = 0; j < vals.size(); ++j) {
                if (sameTol(i, j)) {
                    group.push_back(j);
                    nWanted += wanted.count(static_cast<int>(j)) ? 1 : 0;
                }
            }
            for (size_t j : group) {
                mixed = mixed || (wanted.count(static_cast<int>(j)) != wanted.count(static_cast<int>(i)));
            }
            if (!mixed) {
                continue;
            }
            std::vector<size_t> comps;
            for (size_t j : group) {
                if (composite(j)) {
                    comps.push_back(j);
                }
            }
            bool exact = static_cast<int>(comps.size()) == nWanted;
            for (size_t j : comps) {
                exact = exact && wanted.count(static_cast<int>(j));  // every composite one is flagged, hence every flagged one is composite
            }
            if (exact) {
                for (size_t j : group) {
                    target.erase(static_cast<int>(j));
                }
                for (size_t j : comps) {
                    target.insert(static_cast<int>(j));
                }
            }
            else {
                for (size_t j : group) {
                    unproved.insert(static_cast<int>(j));
                }
            }
        }
        int fixed = 0;
        if (proved) {
            for (int ord : target) {
                if (unproved.count(ord)) {
                    continue;
                }
                const std::string def = "\n#" + found[ord].aspect + " = COMPOSITE_SHAPE_ASPECT(";
                auto pos = text.find(def);
                if (pos == std::string::npos) {
                    continue;  // a tolerance on one face has a plain shape aspect: all-around has no meaning to state
                }
                text.replace(pos, def.size(), "\n#" + found[ord].aspect + " = ALL_AROUND_SHAPE_ASPECT(");
                ++fixed;
            }
        }
        if (fixed != static_cast<int>(wanted.size())) {
            exportWarn("STEP PMI: " + std::to_string(wanted.size() - fixed) + " of " + std::to_string(wanted.size())
                       + " all-around tolerance(s) could not be restated and were written without it"
                       + (proved && unproved.empty() ? std::string()
                                                     : " (the written tolerances could not be paired with the items with certainty)"));
        }
        if (fixed > 0 || gExport.commonDatumsRepaired > 0 || mergedSystems > 0) {
            std::ofstream outf(path, std::ios::binary | std::ios::trunc);
            outf << text;
        }
        return fixed;
    }
    catch (const std::exception& e) {
        exportWarn(std::string("STEP PMI: the all-around tolerances could not be restated: ") + e.what());
    }
    return 0;
}

bool acceptStepPmiWriteError(const std::vector<std::pair<std::string, std::string>>& fails, const std::string& path)
{
    // the error is a known one only when every failed check is a null reference of a draughting model item association
    // (one per presentation-only label, so no more than the annotations written) or of the draughting model itself (the
    // one that lists those associations; at most one)
    int associations = 0;
    int models = 0;
    for (const auto& f : fails) {
        if (f.second.find("Null Reference") == std::string::npos) {
            return false;
        }
        if (f.first == "StepAP242_DraughtingModelItemAssociation") {
            ++associations;
        }
        else if (f.first == "StepVisual_DraughtingModel") {
            ++models;
        }
        else {
            return false;
        }
    }
    if (associations == 0 || associations > gExport.annotations || models > 1) {
        return false;
    }
    // the written file must be complete: it ends with the exchange-structure trailer and holds every annotation occurrence
    std::ifstream in(path, std::ios::binary);
    if (!in) {
        return false;
    }
    std::string text((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    const size_t endPos = text.rfind("END-ISO-10303-21;");
    if (endPos == std::string::npos || text.find_first_not_of(" \r\n\t", endPos + 17) != std::string::npos) {
        return false;
    }
    int occurrences = 0;
    for (size_t pos = text.find("TESSELLATED_ANNOTATION_OCCURRENCE("); pos != std::string::npos;
         pos = text.find("TESSELLATED_ANNOTATION_OCCURRENCE(", pos + 1)) {
        ++occurrences;
    }
    if (occurrences < gExport.annotations) {
        return false;
    }
    gExport.writeErrorAccepted = true;
    return true;
}

void reportStepPmiExportFallback(const std::string& why)
{
    gExport.fellBack = true;
    exportWarn("STEP PMI: the file could not be written with PMI (" + why + "); it was written again without PMI");
}

int exportStepPmi(
    const Handle(TDocStd_Document)& hDoc,
    const ExportOCAF2& ocaf,
    const std::vector<App::DocumentObject*>& objs
)
{
    gExport = StepPmiExportReport();
    gRangeNominals.clear();
    try {
        if (hDoc.IsNull() || !stepPmiWriteEnabled() || pmiSwitch() == "0") {
            return 0;
        }
        std::vector<App::Document*> docs;
        for (auto* o : objs) {
            if (o && o->getDocument() && std::find(docs.begin(), docs.end(), o->getDocument()) == docs.end()) {
                docs.push_back(o->getDocument());
            }
        }
        std::vector<Part::PmiItem*> items;
        for (auto* d : docs) {
            for (auto* o : d->getObjectsOfType(Part::PmiItem::getClassTypeId())) {
                items.push_back(static_cast<Part::PmiItem*>(o));
            }
        }
        if (items.empty()) {
            return 0;
        }
        gExport.ran = true;
        // PMI goes out under an AP242 schema only (ISO 10303-242 is the first protocol with semantic PMI; AP203 and AP214
        // have none); the saved schema stays, run 070
        std::string scheme = Part::Interface::preferredStepScheme();
        if (scheme.rfind("AP242", 0) != 0) {
            exportWarn("STEP PMI: not written, the STEP schema is " + scheme + " and semantic PMI needs an AP242 schema");
            return 0;
        }

        ExportCtx cx {hDoc, XCAFDoc_DocumentTool::ShapeTool(hDoc->Main()), XCAFDoc_DocumentTool::DimTolTool(hDoc->Main()), ocaf, {}, 0, {}};
        std::set<Part::PmiItem*> done;
        auto skip = [&](Part::PmiItem* it, const std::string& why) {
            ++gExport.skipped;
            exportWarn(
                std::string("STEP PMI: ") + it->getNameInDocument() + " (" + it->Label.getStrValue() + ") not written: " + why
            );
        };

        // dimensions
        for (auto* it : items) {
            auto* d = dynamic_cast<Part::PmiDimension*>(it);
            if (!d) {
                continue;
            }
            Raw raw = d->RawAttributes.getValues();
            std::vector<std::string> first, second;
            dimensionLists(d, raw, first, second);
            std::string why;
            if (first.empty()) {
                skip(d, "it governs no face or edge of the export");
                continue;
            }
            const std::string ptype = d->PmiType.getStrValue();
            if (ptype.rfind("Location_", 0) == 0 && second.empty()) {
                // a dimensional location relates two shape aspects (CAx-IF PMI practice, section 5); one with a single side
                // (a dimension that states no value) cannot be written, OCCT's writer reports an error for it
                const auto& kl = d->KernelLoss.getValues();
                skip(d, "a location dimension needs two sides and this one has one" + (kl.empty() ? std::string() : " (" + kl.front() + ")"));
                continue;
            }
            NCollection_Sequence<TDF_Label> f, s;
            if (!labelsFor(cx, d->getDocument(), first, f, why) || !labelsFor(cx, d->getDocument(), second, s, why)) {
                skip(d, why);
                continue;
            }
            Handle(XCAFDimTolObjects_DimensionObject) obj = buildDimension(cx, d, raw, d->ToleranceClass.getStrValue(), why);
            if (obj.IsNull()) {
                skip(d, why);
                continue;
            }
            TDF_Label l = cx.dt->AddDimension();
            XCAFDoc_Dimension::Set(l)->SetObject(obj);
            cx.dt->SetDimension(f, s, l);
            ++gExport.dimensions;
            if (d->HasBounds.getValue() && d->BoundsKind.getStrValue() == "range") {
                const bool ang = isAngular(d->PmiType.getStrValue());
                const double k = ang ? std::acos(-1.0) / 180.0 : 1.0;
                gRangeNominals.push_back({d->UpperBound.getValue() * k, d->LowerBound.getValue() * k, d->Value.getValue() * k, ang});
            }
        }

        // tolerances with their datum uses
        for (auto* it : items) {
            auto* t = dynamic_cast<Part::PmiTolerance*>(it);
            if (!t) {
                continue;
            }
            Raw raw = t->RawAttributes.getValues();
            std::string why;
            if (referenceNames(t).empty()) {
                skip(t, "it governs no face or edge of the export");
                continue;
            }
            NCollection_Sequence<TDF_Label> f;
            if (!labelsFor(cx, t->getDocument(), referenceNames(t), f, why)) {
                skip(t, why);
                continue;
            }
            Handle(XCAFDimTolObjects_GeomToleranceObject) obj = buildTolerance(cx, t, raw, why);
            if (obj.IsNull()) {
                skip(t, why);
                continue;
            }
            {
                const auto& tm = t->Modifiers.getValues();
                if (std::find(tm.begin(), tm.end(), "ALL_AROUND") != tm.end()) {
                    gExport.allAround.push_back(gExport.tolerances);
                }
                gExport.writtenToleranceValues.push_back(t->Value.getValue());
                gExport.writtenToleranceKinds.push_back(t->PmiType.getStrValue());
                gExport.writtenToleranceDatum.push_back(!t->Datums.getValues().empty());
                {
                    // OCCT lists the datums by their position in the frame, in the order they were added within a position
                    std::vector<Part::PmiDatum*> ds;
                    for (auto* o : t->Datums.getValues()) {
                        if (auto* d = dynamic_cast<Part::PmiDatum*>(o)) {
                            ds.push_back(d);
                        }
                    }
                    std::stable_sort(ds.begin(), ds.end(), [](auto* x, auto* y) {
                        return x->Position.getValue() < y->Position.getValue();
                    });
                    std::string l;
                    for (auto* d : ds) {
                        l += (l.empty() ? "" : ",") + d->Letter.getStrValue();
                    }
                    gExport.writtenToleranceLetters.push_back(l);
                }
            }
            TDF_Label l = cx.dt->AddGeomTolerance();
            XCAFDoc_GeomTolerance::Set(l)->SetObject(obj);
            cx.dt->SetGeomTolerance(f, l);
            for (auto* dobj : t->Datums.getValues()) {
                auto* dd = dynamic_cast<Part::PmiDatum*>(dobj);
                if (!dd) {
                    continue;
                }
                std::string dwhy;
                TDF_Label dl = writeDatum(cx, dd, dwhy);
                done.insert(dd);
                if (dl.IsNull()) {
                    skip(dd, dwhy);
                    continue;
                }
                cx.dt->SetDatumToGeomTol(dl, l);
                ++gExport.datums;
            }
            ++gExport.tolerances;
        }

        // datums no written tolerance refers to (targets, loose datums)
        for (auto* it : items) {
            auto* dd = dynamic_cast<Part::PmiDatum*>(it);
            if (!dd || done.count(dd)) {
                continue;
            }
            std::string why;
            TDF_Label dl = writeDatum(cx, dd, why);
            if (dl.IsNull()) {
                skip(dd, why);
                continue;
            }
            ++gExport.datums;
        }

        // graphic-only callouts: with the semantic items they came with, or alone when the document has none
        bool semantic = false;
        for (auto* it : items) {
            semantic = semantic || !dynamic_cast<Part::PmiAnnotation*>(it);
        }
        int noFaceCallouts = 0;
        if (gExport.dimensions + gExport.tolerances + gExport.datums > 0 || !semantic) {
            for (auto* it : items) {
                auto* a = dynamic_cast<Part::PmiAnnotation*>(it);
                if (!a) {
                    continue;
                }
                Raw raw = a->RawAttributes.getValues();
                XCAFDimTolObjects_DimensionType type;
                if (!enumFromName(a->PmiType.getStrValue(), dimTypeName, type)) {
                    skip(a, "its type " + a->PmiType.getStrValue() + " is not one the writer knows");
                    continue;
                }
                if (a->Presentation.getShape().isNull()) {
                    continue;
                }
                NCollection_Sequence<TDF_Label> af;
                std::string awhy;
                if (!labelsFor(cx, a->getDocument(), referenceNames(a), af, awhy)) {
                    skip(a, awhy);
                    continue;
                }
                if (af.Length() == 0) {
                    // OCCT's writer reports an error for a presentation label that has no shape to refer to, although
                    // the file is complete; the label is written and the error is judged by acceptStepPmiWriteError
                    ++noFaceCallouts;
                }
                Handle(XCAFDimTolObjects_DimensionObject) obj = new XCAFDimTolObjects_DimensionObject();
                obj->SetType(type);
                obj->SetSemanticName(hs(a->SemanticName.getStrValue()));
                gp_Pnt p;
                gp_Ax2 ax;
                if (rawFlag(raw, "dim.has_text_point") && rawPoint(raw, "dim.text_point", p)) {
                    obj->SetPointTextAttach(p);
                }
                if (rawFlag(raw, "dim.has_plane") && rawAxis(raw, "dim.plane", ax)) {
                    obj->SetPlane(ax);
                }
                int nd = std::atoi(rawGet(raw, "dim.descriptions").c_str());
                for (int i = 1; i <= nd; ++i) {
                    obj->AddDescription(
                        hs(rawGet(raw, "dim.description." + std::to_string(i))),
                        hs(rawGet(raw, "dim.description_name." + std::to_string(i)))
                    );
                }
                setPresentation(cx, a, "dim.presentation_name", raw, [&](const TopoDS_Shape& s, const Handle(TCollection_HAsciiString)& n) {
                    obj->SetPresentation(s, n);
                });
                TDF_Label l = cx.dt->AddDimension();
                XCAFDoc_Dimension::Set(l)->SetObject(obj);
                if (af.Length() > 0) {
                    cx.dt->SetDimension(af, NCollection_Sequence<TDF_Label>(), l);
                }
                ++gExport.annotations;
            }
        }
        gExport.shapelessCallouts = noFaceCallouts;
        if (noFaceCallouts > 0) {
            exportWarn(
                "STEP PMI: " + std::to_string(noFaceCallouts)
                + " graphic callout(s) point at no face and were written without one (the STEP writer may report an error for "
                  "them although the file is complete; that error is accepted only for that known kind)"
            );
        }
        if (cx.facePresentationStripped > 0) {
            gExport.presentationsStripped = cx.facePresentationStripped;
            exportWarn(
                "STEP PMI: " + std::to_string(cx.facePresentationStripped)
                + " presentation(s) hold faces and no curves and were left out (the writer reports an error for them): "
                + join(std::vector<std::string>(cx.strippedNames.begin(), cx.strippedNames.begin() + std::min<size_t>(5, cx.strippedNames.size())), ", ")
                + (cx.strippedNames.size() > 5 ? ", ..." : "")
            );
        }
        return gExport.dimensions + gExport.tolerances + gExport.datums + gExport.annotations;
    }
    catch (const Standard_Failure& e) {
        exportWarn(std::string("STEP PMI: not written, the writer stopped: ") + e.GetMessageString());
    }
    catch (const Base::Exception& e) {
        exportWarn(std::string("STEP PMI: not written, the writer stopped: ") + e.what());
    }
    catch (const std::exception& e) {
        exportWarn(std::string("STEP PMI: not written: ") + e.what());
    }
    // nothing of a stopped pass stays in the XCAF document
    try {
        Handle(XCAFDoc_DimTolTool) dt = XCAFDoc_DocumentTool::DimTolTool(hDoc->Main());
        NCollection_Sequence<TDF_Label> dims, tols, datums;
        dt->GetDimensionLabels(dims);
        dt->GetGeomToleranceLabels(tols);
        dt->GetDatumLabels(datums);
        for (auto* seq : {&dims, &tols, &datums}) {
            for (int i = 1; i <= seq->Length(); ++i) {
                seq->Value(i).ForgetAllAttributes(true);
            }
        }
    }
    catch (...) {
    }
    gExport.dimensions = gExport.tolerances = gExport.datums = gExport.annotations = 0;
    return 0;
}

}  // namespace Import
