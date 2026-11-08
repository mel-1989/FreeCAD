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
#include <map>
#include <set>
#include <sstream>
#include <string>

#include <BRepGProp.hxx>
#include <GProp_GProps.hxx>
#include <Interface_InterfaceModel.hxx>
#include <NCollection_Sequence.hxx>
#include <StepData_StepModel.hxx>
#include <TCollection_AsciiString.hxx>
#include <TCollection_ExtendedString.hxx>
#include <TCollection_HAsciiString.hxx>
#include <TDF_ChildIterator.hxx>
#include <TDF_Label.hxx>
#include <TDF_TagSource.hxx>
#include <TDF_Tool.hxx>
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
#include <XSControl_TransferReader.hxx>

#include <App/Document.h>
#include <App/DocumentObject.h>
#include <Base/Console.h>
#include <Mod/Part/App/PartFeature.h>
#include <Mod/Part/App/PmiFeature.h>

#include "StepPmi.h"

namespace Import
{

namespace
{

StepPmiReport gReport;

constexpr int kStashTag = 701;  // private label under 0:1, next to the 700 of StepAttributes

// ---------------------------------------------------------------------------------------------
// test switches (environment variables, never preferences)
// ---------------------------------------------------------------------------------------------

/// FC_STEP_PMI_TEST_UNKNOWN=dimension|tolerance|modifier makes the first item of that kind look unrecognised,
/// so the unmapped path can be tested without a file OCCT mis-types.
bool testForceUnknown(const char* kind)
{
    static std::set<std::string> used;
    if (std::string(kind) == "reset") {
        used.clear();
        return false;
    }
    if (std::string(kind) == "reset") {
        used.clear();
        return false;
    }
    const char* v = std::getenv("FC_STEP_PMI_TEST_UNKNOWN");
    if (!v || std::string(v) != kind) {
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
        out[key.ToCString()] = s;
    }
    return out;
}

// ---------------------------------------------------------------------------------------------
// links to the imported objects
// ---------------------------------------------------------------------------------------------

struct FaceHit
{
    App::DocumentObject* obj {nullptr};
    std::string sub;
    std::string geom;  // area (or length) and centre of mass of the XCAF face in its own frame, for the link check
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
                    auto it = edgeMap.find(ex.Current().TShape().get());
                    if (it == edgeMap.end()) {
                        ++unresolved;
                        continue;
                    }
                    FaceHit h = it->second;
                    h.geom = geometryText(ex.Current());
                    add(hits, h);
                }
            }
        }
    }

private:
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
    index.resolve(f, r.hits, r.unresolved);
    index.resolve(s, r.hits, r.unresolved);
    auto it = stash.find(entryOf(label));
    if (it != stash.end()) {
        r.ids = it->second.first;
        r.ids.insert(it->second.second.begin(), it->second.second.end());
        raw["step.face_ids.first"] = idsText(it->second.first);
        raw["step.face_ids.second"] = idsText(it->second.second);
    }
    raw["xcaf.label"] = entryOf(label);
    raw["xcaf.shapes.first"] = std::to_string(f.Length());
    raw["xcaf.shapes.second"] = std::to_string(s.Length());
    raw["link.unresolved_faces"] = std::to_string(r.unresolved);
    {
        std::vector<std::string> g;
        for (const auto& h : r.hits) {
            g.push_back(std::string(h.obj->getNameInDocument()) + "." + h.sub + "|" + h.geom);
        }
        raw["link.geometry"] = join(g, ";");
    }
    return r;
}

void finishItem(Part::PmiItem* item, const CommonResult& c, Raw& raw, const Flags& fl, const char* what)
{
    std::vector<long> ids(c.ids.begin(), c.ids.end());
    item->StepFaceIds.setValues(ids);
    setLinks(item, c.hits);
    item->Unattached.setValue(c.hits.empty());
    item->Unmapped.setValue(fl.unknownType || fl.unknownOther);
    item->RawAttributes.setValue(raw);
    gReport.unresolvedFaces += c.unresolved;
    if (fl.unknownType || fl.unknownOther) {
        ++gReport.unmapped;
        warn(std::string("STEP PMI: ") + what + " '" + item->PmiType.getValue() + "' has an unrecognised "
             + (fl.unknownType ? "type" : "modifier or attribute") + " (" + join(fl.notes, ", ")
             + "); imported as an unmapped item");
    }
}

}  // namespace

bool stepPmiEnabled()
{
    const char* v = std::getenv("FC_STEP_PMI");
    return !(v && std::string(v) == "0");
}

const StepPmiReport& lastStepPmiReport()
{
    return gReport;
}

void stashStepPmiFaceIds(const Handle(XSControl_WorkSession)& ws, const Handle(TDocStd_Document)& hDoc)
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
        for (int m = 1; m <= tp->NbMapped(); ++m) {
            Handle(Standard_Transient) ent = tp->Mapped(m);
            TopoDS_Shape r = TransferBRep::ShapeResult(tp, ent);
            if (r.IsNull() || r.ShapeType() != TopAbs_FACE) {
                continue;
            }
            faceEnt.emplace(r.TShape().get(), static_cast<int>(model->IdentLabel(ent)));
        }
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
        auto put = [&](const TDF_Label& label) {
            NCollection_Sequence<TDF_Label> f, s;
            XCAFDoc_DimTolTool::GetRefShapeLabel(label, f, s);
            if (f.Length() + s.Length() == 0) {
                return;
            }
            TDF_Label slot = TDF_TagSource::NewChild(root);
            TDataStd_Comment::Set(slot, TCollection_ExtendedString(entryOf(label).c_str()));
            Handle(TDataStd_NamedData) nd = TDataStd_NamedData::Set(slot);
            nd->SetString(TCollection_ExtendedString("first"),
                          TCollection_ExtendedString(idsText(idsOf(f)).c_str()));
            nd->SetString(TCollection_ExtendedString("second"),
                          TCollection_ExtendedString(idsText(idsOf(s)).c_str()));
        };
        for (int i = 1; i <= dims.Length(); ++i) {
            put(dims.Value(i));
        }
        for (int i = 1; i <= tols.Length(); ++i) {
            put(tols.Value(i));
        }
        for (int i = 1; i <= datums.Length(); ++i) {
            put(datums.Value(i));
        }
    }
    catch (const Standard_Failure& e) {
        Base::Console().warning("STEP PMI: face ids not recorded: {}\n", e.GetMessageString());
    }
    catch (...) {
        Base::Console().warning("STEP PMI: face ids not recorded\n");
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
        raw["datum.presentation_name"] = hstr(o->GetPresentationName());
    }
    item->Modifiers.setValues(mods);
    CommonResult c = resolveCommon(dl, index, stash, raw);
    finishItem(item, c, raw, fl, "datum");
    item->Label.setValue("Datum " + letter);
    return item;
}

}  // namespace

void importStepPmi(const Handle(TDocStd_Document)& hDoc, App::Document* doc)
{
    gReport = StepPmiReport();
    testForceUnknown("reset");
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
        std::vector<App::DocumentObject*> created;
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
                ++gReport.graphicOnly;  // graphic-only presentation: counted, not an item
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
            raw["dim.descriptions"] = std::to_string(o->NbDescriptions());
            for (int k = 1; k <= o->NbDescriptions(); ++k) {
                raw["dim.description." + std::to_string(k)] = hstr(o->GetDescription(k));
                raw["dim.description_name." + std::to_string(k)] = hstr(o->GetDescriptionName(k));
            }
            CommonResult c = resolveCommon(dl, index, stash, raw);
            finishItem(item, c, raw, fl, "dimension");
            ++gReport.dimensions;
            if (c.hits.empty()) {
                ++gReport.unattached;
                gReport.unattachedItems.push_back(std::string(item->getNameInDocument()) + " (" + type + " "
                                                  + label6(o->GetValue()) + ")");
            }
            item->Label.setValue(type + " " + label6(o->GetValue()));
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
            raw["tol.presentation_name"] = hstr(o->GetPresentationName());

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

        if (gReport.dimensions + gReport.tolerances + gReport.datums == 0) {
            for (auto* obj : created) {
                doc->removeObject(obj->getNameInDocument());
            }
            created.clear();
            if (gReport.graphicOnly > 0) {
                warn("STEP PMI: only graphic PMI was found (" + std::to_string(gReport.graphicOnly)
                     + " presentation-only dimension labels); there are no semantic dimensions, tolerances or "
                       "datums to import");
            }
            return;
        }
        auto* group = doc->addObject<Part::PmiGroup>("PMI");
        group->Label.setValue("PMI");
        group->addObjects(created);
        Base::Console().message(
            "STEP PMI: {} dimensions, {} tolerances, {} datum references imported ({} presentation-only "
            "labels not imported, {} unmapped, {} attached to no face)\n",
            gReport.dimensions,
            gReport.tolerances,
            gReport.datums,
            gReport.graphicOnly,
            gReport.unmapped,
            gReport.unattached
        );
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
        warn(std::string("STEP PMI: import stopped: ") + e.what());
    }
    catch (const Standard_Failure& e) {
        warn(std::string("STEP PMI: import stopped: ") + e.GetMessageString());
    }
    catch (const std::exception& e) {
        warn(std::string("STEP PMI: import stopped: ") + e.what());
    }
}

}  // namespace Import
