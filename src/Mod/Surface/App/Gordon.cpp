// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *  Gordon surface through a network of profile and guide curves.          *
 *                                                                         *
 *  Public sources: W. J. Gordon, "Spline-blended surface interpolation    *
 *  through curve networks", J. Math. Mech. 18(10), 1969; the OCCT 8.0     *
 *  GeomFill_Gordon header documentation. Nothing is copied from any       *
 *  other Gordon implementation.                                           *
 ***************************************************************************/

#include <algorithm>
#include <array>
#include <cmath>
#include <string>
#include <vector>

#include <BRepTools_WireExplorer.hxx>
#include <BRep_Tool.hxx>
#include <GeomConvert.hxx>
#include <GeomConvert_CompCurveToBSplineCurve.hxx>
#include <GeomFill_Gordon.hxx>
#include <Geom_BSplineCurve.hxx>
#include <Geom_BSplineSurface.hxx>
#include <Geom_Curve.hxx>
#include <Geom_TrimmedCurve.hxx>
#include <Precision.hxx>
#include <Standard_Failure.hxx>
#include <TopAbs_Orientation.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Edge.hxx>
#include <TopoDS_Wire.hxx>

#include <Base/PyWrapParseTupleAndKeywords.h>
#include <Mod/Part/App/BSplineSurfacePy.h>
#include <Mod/Part/App/Geometry.h>
#include <Mod/Part/App/GeometryCurvePy.h>
#include <Mod/Part/App/TopoShapeEdgePy.h>
#include <Mod/Part/App/TopoShapePy.h>
#include <Mod/Part/App/TopoShapeWirePy.h>

#include "Gordon.h"

using GeomFill_Gordon_Status = GeomFill_Gordon::ResultStatus;

namespace
{

using CurveHandle = occ::handle<Geom_Curve>;

const char* statusName(GeomFill_Gordon_Status s)
{
    switch (s) {
        case GeomFill_Gordon_Status::NotStarted:
            return "NotStarted";
        case GeomFill_Gordon_Status::Done:
            return "Done";
        case GeomFill_Gordon_Status::InvalidInput:
            return "InvalidInput";
        case GeomFill_Gordon_Status::ConversionFailed:
            return "ConversionFailed";
        case GeomFill_Gordon_Status::IntersectionFailed:
            return "IntersectionFailed";
        case GeomFill_Gordon_Status::OrderingFailed:
            return "OrderingFailed";
        case GeomFill_Gordon_Status::ReparametrizationFailed:
            return "ReparametrizationFailed";
        case GeomFill_Gordon_Status::CompatibilityFailed:
            return "CompatibilityFailed";
        case GeomFill_Gordon_Status::CurveCompatibilityFailed:
            return "CurveCompatibilityFailed";
        case GeomFill_Gordon_Status::RationalReparametrizationFailed:
            return "RationalReparametrizationFailed";
        case GeomFill_Gordon_Status::SkinningFailed:
            return "SkinningFailed";
        case GeomFill_Gordon_Status::ReferenceSurfaceFailed:
            return "ReferenceSurfaceFailed";
        case GeomFill_Gordon_Status::KnotAlignmentFailed:
            return "KnotAlignmentFailed";
        case GeomFill_Gordon_Status::RationalDegreeOverflow:
            return "RationalDegreeOverflow";
        case GeomFill_Gordon_Status::RationalConstructionFailed:
            return "RationalConstructionFailed";
        case GeomFill_Gordon_Status::PeriodicityFailed:
            return "PeriodicityFailed";
        case GeomFill_Gordon_Status::ApproximationFailed:
            return "ApproximationFailed";
        case GeomFill_Gordon_Status::ConstructionFailed:
            return "ConstructionFailed";
    }
    return "Unknown";
}

bool isBounded(const CurveHandle& c)
{
    double f = c->FirstParameter();
    double l = c->LastParameter();
    return std::isfinite(f) && std::isfinite(l) && !Precision::IsInfinite(f)
        && !Precision::IsInfinite(l) && l > f;
}

/// The edge's 3D curve over its parameter range, with the edge's location applied.
CurveHandle edgeCurve(const TopoDS_Edge& edge, bool applyOrientation)
{
    double f = 0.0;
    double l = 0.0;
    // this overload applies the edge's TopLoc_Location (the placement of the shape)
    CurveHandle c = BRep_Tool::Curve(edge, f, l);
    if (c.IsNull()) {
        throw Py::ValueError("InvalidInput: an edge has no 3D curve");
    }
    if (!isBounded(c) && !(std::isfinite(f) && std::isfinite(l) && !Precision::IsInfinite(f)
                           && !Precision::IsInfinite(l))) {
        throw Py::ValueError("InvalidInput: an edge has an unbounded range");
    }
    occ::handle<Geom_TrimmedCurve> t = new Geom_TrimmedCurve(c, f, l);
    CurveHandle out = t;
    if (applyOrientation && edge.Orientation() == TopAbs_REVERSED) {
        out = t->Reversed();
    }
    return out;
}

CurveHandle wireCurve(const TopoDS_Wire& wire, double tolerance)
{
    std::vector<CurveHandle> parts;
    for (BRepTools_WireExplorer ex(wire); ex.More(); ex.Next()) {
        parts.push_back(edgeCurve(ex.Current(), true));
    }
    if (parts.empty()) {
        throw Py::ValueError("InvalidInput: a wire has no edges");
    }
    occ::handle<Geom_BSplineCurve> first = GeomConvert::CurveToBSplineCurve(parts.front());
    if (parts.size() == 1) {
        return first;
    }
    GeomConvert_CompCurveToBSplineCurve joined(first);
    double joinTol = std::max(tolerance, 1.0e-7);
    for (size_t i = 1; i < parts.size(); ++i) {
        occ::handle<Geom_BSplineCurve> next = GeomConvert::CurveToBSplineCurve(parts[i]);
        if (!joined.Add(next, joinTol)) {
            throw Py::ValueError("InvalidInput: the edges of a wire could not be joined");
        }
    }
    return joined.BSplineCurve();
}

CurveHandle toCurve(PyObject* item, double tolerance)
{
    if (PyObject_TypeCheck(item, &Part::GeometryCurvePy::Type)) {
        auto* pyc = static_cast<Part::GeometryCurvePy*>(item);
        CurveHandle c = CurveHandle::DownCast(pyc->getGeometryPtr()->handle());
        if (c.IsNull()) {
            throw Py::TypeError("TypeError: not a curve");
        }
        if (!isBounded(c)) {
            throw Py::ValueError("InvalidInput: an unbounded curve cannot be part of a network");
        }
        return c;
    }
    if (PyObject_TypeCheck(item, &Part::TopoShapeEdgePy::Type)) {
        auto* pye = static_cast<Part::TopoShapeEdgePy*>(item);
        const TopoDS_Shape& s = pye->getTopoShapePtr()->getShape();
        if (s.IsNull() || s.ShapeType() != TopAbs_EDGE) {
            throw Py::ValueError("InvalidInput: an empty edge");
        }
        return edgeCurve(TopoDS::Edge(s), false);
    }
    if (PyObject_TypeCheck(item, &Part::TopoShapeWirePy::Type)) {
        auto* pyw = static_cast<Part::TopoShapeWirePy*>(item);
        const TopoDS_Shape& s = pyw->getTopoShapePtr()->getShape();
        if (s.IsNull() || s.ShapeType() != TopAbs_WIRE) {
            throw Py::ValueError("InvalidInput: an empty wire");
        }
        return wireCurve(TopoDS::Wire(s), tolerance);
    }
    throw Py::TypeError("TypeError: a profile or guide must be a bounded Part.Curve, a Part.Edge or a Part.Wire");
}

NCollection_Array1<CurveHandle> toArray(PyObject* seq, const char* what, double tolerance)
{
    if (!PyList_Check(seq) && !PyTuple_Check(seq)) {
        throw Py::TypeError(std::string("TypeError: ") + what + " must be a list or tuple");
    }
    Py::Sequence list(seq);
    std::vector<CurveHandle> curves;
    for (Py::Sequence::size_type i = 0; i < list.size(); ++i) {
        Py::Object item = list[i];
        curves.push_back(toCurve(item.ptr(), tolerance));
    }
    if (curves.empty()) {
        // keep the lower bound valid; OCCT reports InvalidInput for fewer than two curves
        NCollection_Array1<CurveHandle> empty(1, 0);
        return empty;
    }
    NCollection_Array1<CurveHandle> out(1, static_cast<int>(curves.size()));
    for (size_t i = 0; i < curves.size(); ++i) {
        out(static_cast<int>(i) + 1) = curves[i];
    }
    return out;
}

}  // namespace

namespace Surface
{

Py::Object makeGordon(const Py::Tuple& args, const Py::Dict& kwds)
{
    PyObject* profiles = nullptr;
    PyObject* guides = nullptr;
    double tolerance = 1.0e-4;
    PyObject* approximateObj = Py_False;
    static const std::array<const char*, 5> kwlist = {"profiles", "guides", "tolerance", "approximate", nullptr};
    if (!Base::Wrapped_ParseTupleAndKeywords(
            args.ptr(),
            kwds.ptr(),
            "OO|dO",
            kwlist,
            &profiles,
            &guides,
            &tolerance,
            &approximateObj
        )) {
        throw Py::Exception();
    }
    if (!std::isfinite(tolerance) || !(tolerance > 0.0)) {
        throw Py::ValueError("InvalidTolerance: tolerance must be finite and greater than 0");
    }
    int approximateInt = PyObject_IsTrue(approximateObj);
    if (approximateInt < 0) {
        throw Py::Exception();
    }
    const bool approximate = approximateInt != 0;

    NCollection_Array1<CurveHandle> profileArray = toArray(profiles, "profiles", tolerance);
    NCollection_Array1<CurveHandle> guideArray = toArray(guides, "guides", tolerance);

    GeomFill_Gordon gordon;
    try {
        gordon.Init(profileArray, guideArray, tolerance);
        if (approximate) {
            gordon.SetApproximationMode(GeomFill_Gordon::ApproximationMode::AllowApproximateFallback);
        }
        gordon.Perform();
    }
    catch (Standard_Failure& e) {
        throw Py::ValueError(std::string("ConstructionFailed: ") + e.GetMessageString());
    }
    if (!gordon.IsDone() || gordon.Surface().IsNull()) {
        throw Py::ValueError(statusName(gordon.Status()));
    }

    const GeomFill_Gordon::BuildReport& report = gordon.Report();
    const double deviation = std::max(report.MaxProfileDeviation, report.MaxGuideDeviation);
    const bool exceeded = !(deviation <= tolerance);
    if (exceeded && !approximate) {
        throw Py::ValueError(
            "DeviationExceeded: the surface misses the input curves by " + std::to_string(deviation)
            + " mm, more than the tolerance " + std::to_string(tolerance)
            + " mm (pass approximate=True to accept it)"
        );
    }

    Py::Dict result;
    result.setItem("surface", Py::asObject(new Part::BSplineSurfacePy(new Part::GeomBSplineSurface(gordon.Surface()))));
    result.setItem("approximate", Py::Boolean(gordon.IsApproximate() || (approximate && exceeded)));
    result.setItem("max_contact_gap", Py::Float(report.MaxContactGap));
    result.setItem("max_profile_deviation", Py::Float(report.MaxProfileDeviation));
    result.setItem("max_guide_deviation", Py::Float(report.MaxGuideDeviation));
    return result;
}

}  // namespace Surface
