// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2014 Nathan Miller <Nathan.A.Mill[at]gmail.com>         *
 *                                                                         *
 *   This file is part of the FreeCAD CAx development system.              *
 *                                                                         *
 *   This library is free software; you can redistribute it and/or         *
 *   modify it under the terms of the GNU Library General Public           *
 *   License as published by the Free Software Foundation; either          *
 *   version 2 of the License, or (at your option) any later version.      *
 *                                                                         *
 *   This library  is distributed in the hope that it will be useful,      *
 *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
 *   GNU Library General Public License for more details.                  *
 *                                                                         *
 *   You should have received a copy of the GNU Library General Public     *
 *   License along with this library; see the file COPYING.LIB. If not,    *
 *   write to the Free Software Foundation, Inc., 59 Temple Place,         *
 *   Suite 330, Boston, MA  02111-1307, USA                                *
 *                                                                         *
 ***************************************************************************/

#include <cstdio>
#include <sstream>
#include <string>

#include <BRepBuilderAPI_MakeWire.hxx>
#include <BRepFill_Filling.hxx>
#include <BRepGProp_Face.hxx>
#include <BRepAdaptor_Surface.hxx>
#include <BRepTools.hxx>
#include <BRep_Tool.hxx>
#include <Geom2d_Curve.hxx>
#include <TopExp.hxx>
#include <TopTools_IndexedDataMapOfShapeListOfShape.hxx>
#include <TopTools_IndexedMapOfShape.hxx>
#include <TopTools_ListOfShape.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Face.hxx>
#include <TopoDS_Wire.hxx>
#include <gp_Pnt.hxx>

#include "FeatureFilling.h"


using namespace Surface;

PROPERTY_SOURCE(Surface::Filling, Part::Spline)

// Initial values

Filling::Filling()
{
    // clang-format off
    ADD_PROPERTY_TYPE(BoundaryEdges,(nullptr,""), "Filling", App::Prop_None,
                      "Boundary Edges (C0 is required for edges without a corresponding face)");
    ADD_PROPERTY_TYPE(BoundaryFaces,(""), "Filling", App::Prop_None, "Boundary Faces");
    ADD_PROPERTY_TYPE(BoundaryOrder,(-1), "Filling", App::Prop_None,
                      "Order of constraint on boundary faces (C0, G1 and G2 are possible)");

    ADD_PROPERTY_TYPE(UnboundEdges,(nullptr,""), "Filling", App::Prop_None,
                      "Unbound constraint edges (C0 is required for edges without a corresponding face)");
    ADD_PROPERTY_TYPE(UnboundFaces,(""), "Filling", App::Prop_None,
                      "Unbound constraint faces");
    ADD_PROPERTY_TYPE(UnboundOrder,(-1), "Filling", App::Prop_None,
                      "Order of constraint on curve faces (C0, G1 and G2 are possible)");

    ADD_PROPERTY_TYPE(BoundaryFaceLinks,(nullptr,""), "Filling", App::Prop_Hidden,
                      "Boundary faces as links (kept in sync with BoundaryFaces). Do not set this by "
                      "hand: it is maintained by the link layer and BoundaryFaces is rewritten from it, so a "
                      "manual change silently overwrites BoundaryFaces (set BoundaryFaces instead)");
    ADD_PROPERTY_TYPE(UnboundFaceLinks,(nullptr,""), "Filling", App::Prop_Hidden,
                      "Unbound constraint faces as links (kept in sync with UnboundFaces). Do not set this by "
                      "hand: it is maintained by the link layer and UnboundFaces is rewritten from it, so a "
                      "manual change silently overwrites UnboundFaces (set UnboundFaces instead)");
    ADD_PROPERTY_TYPE(BoundaryFaceSig,(""), "Filling", App::Prop_Hidden,
                      "Surface type and normal of each boundary face at its edge, as last resolved");
    ADD_PROPERTY_TYPE(UnboundFaceSig,(""), "Filling", App::Prop_Hidden,
                      "Surface type and normal of each unbound face at its edge, as last resolved");
    ADD_PROPERTY_TYPE(FreeFaces,(nullptr,""), "Filling", App::Prop_None, "Free constraint on a face");
    ADD_PROPERTY_TYPE(FreeOrder,(0), "Filling", App::Prop_None, "Order of constraint on free faces");

    ADD_PROPERTY_TYPE(Points,(nullptr,""), "Filling", App::Prop_None, "Constraint Points (on Surface)");
    ADD_PROPERTY_TYPE(InitialFace,(nullptr), "Filling", App::Prop_None, "Initial surface to use");

    ADD_PROPERTY_TYPE(Degree,(3), "Filling", App::Prop_None, "Starting degree");
    ADD_PROPERTY_TYPE(PointsOnCurve,(15), "Filling", App::Prop_None,
                      "Number of points on an edge for constraint");
    ADD_PROPERTY_TYPE(Iterations,(2), "Filling", App::Prop_None, "Number of iterations");
    ADD_PROPERTY_TYPE(Anisotropy,(false), "Filling", App::Prop_None, "Anisotropy");
    ADD_PROPERTY_TYPE(Tolerance2d,(0.00001), "Filling", App::Prop_None, "2D Tolerance");
    ADD_PROPERTY_TYPE(Tolerance3d,(0.0001), "Filling", App::Prop_None, "3D Tolerance");
    ADD_PROPERTY_TYPE(TolAngular,(0.01), "Filling", App::Prop_None, "G1 tolerance");
    ADD_PROPERTY_TYPE(TolCurvature,(0.1), "Filling", App::Prop_None, "G2 tolerance");
    ADD_PROPERTY_TYPE(MaximumDegree,(8), "Filling", App::Prop_None, "Maximum curve degree");
    ADD_PROPERTY_TYPE(MaximumSegments,(9), "Filling", App::Prop_None, "Maximum number of segments");
    // clang-format on

    BoundaryEdges.setScope(App::LinkScope::Global);
    UnboundEdges.setScope(App::LinkScope::Global);
    BoundaryFaceLinks.setScope(App::LinkScope::Global);
    UnboundFaceLinks.setScope(App::LinkScope::Global);
    FreeFaces.setScope(App::LinkScope::Global);
    Points.setScope(App::LinkScope::Global);
    InitialFace.setScope(App::LinkScope::Global);

    BoundaryEdges.setSize(0);
    BoundaryFaces.setSize(0);
    BoundaryOrder.setSize(0);
    UnboundEdges.setSize(0);
    UnboundFaces.setSize(0);
    UnboundOrder.setSize(0);
    FreeFaces.setSize(0);
    FreeOrder.setSize(0);
    Points.setSize(0);
}

short Filling::mustExecute() const
{
    if (BoundaryEdges.isTouched() || BoundaryFaces.isTouched() || BoundaryOrder.isTouched()
        || UnboundEdges.isTouched() || UnboundFaces.isTouched() || UnboundOrder.isTouched()
        || FreeFaces.isTouched() || FreeOrder.isTouched() || Points.isTouched()
        || InitialFace.isTouched() || Degree.isTouched() || PointsOnCurve.isTouched()
        || Iterations.isTouched() || Anisotropy.isTouched() || Tolerance2d.isTouched()
        || Tolerance3d.isTouched() || TolAngular.isTouched() || TolCurvature.isTouched()
        || MaximumDegree.isTouched() || MaximumSegments.isTouched()) {
        return 1;
    }
    return 0;
}

void Filling::addConstraints(
    BRepFill_Filling& builder,
    const App::PropertyLinkSubList& edges,
    const std::vector<std::string>& faces,
    const App::PropertyIntegerList& orders,
    Standard_Boolean bnd
)
{
    auto edge_obj = edges.getValues();
    auto edge_sub = edges.getSubValues();
    auto face_sub = faces;
    auto contvals = orders.getValues();

    // if the number of continuities doesn't match then fall back to C0
    if (edge_sub.size() != contvals.size()) {
        contvals.resize(edge_sub.size());
        std::fill(contvals.begin(), contvals.end(), static_cast<long>(GeomAbs_C0));
    }

    // if the number of faces doesn't match then fall back to empty strings
    // an empty face string indicates that there is no face associated to an edge
    if (face_sub.size() != edge_sub.size()) {
        face_sub.resize(edge_obj.size());
        std::fill(face_sub.begin(), face_sub.end(), std::string());
    }

    if (edge_obj.size() == edge_sub.size()) {
        // BRepFill_Filling crashes if the boundary edges are not added in a consecutive order.
        // these edges are first added to a test wire to check that they can be securely added to
        // the Filling algo
        BRepBuilderAPI_MakeWire testWire;
        for (std::size_t index = 0; index < edge_obj.size(); index++) {
            // get the part object
            App::DocumentObject* obj = edge_obj[index];
            const std::string& sub = edge_sub[index];

            if (obj && obj->isDerivedFrom<Part::Feature>()) {
                // get the sub-edge of the part's shape
                const Part::TopoShape& shape = static_cast<Part::Feature*>(obj)->Shape.getShape();
                TopoDS_Shape edge = shape.getSubShape(sub.c_str());
                if (!edge.IsNull() && edge.ShapeType() == TopAbs_EDGE) {
                    GeomAbs_Shape cont = static_cast<GeomAbs_Shape>(contvals[index]);

                    // check for an adjacent face of the edge
                    std::string subFace = face_sub[index];

                    // edge doesn't have set an adjacent face
                    if (subFace.empty()) {
                        if (!bnd) {
                            // not a boundary edge: safe to add it directly
                            builder.Add(TopoDS::Edge(edge), cont, bnd);
                        }
                        else {
                            // boundary edge: try to add it to the test wire first
                            testWire.Add(TopoDS::Edge(edge));
                            if (testWire.IsDone()) {
                                builder.Add(TopoDS::Edge(edge), cont, bnd);
                            }
                            else {
                                throw Standard_Failure(
                                    "Boundary edges must be added in a consecutive order"
                                );
                            }
                        }
                    }
                    else {
                        TopoDS_Shape face = shape.getSubShape(subFace.c_str());
                        if (!face.IsNull() && face.ShapeType() == TopAbs_FACE) {
                            // The face is named by a bare string that the link layer does not
                            // follow when the model changes, so it can be stale. A face that does
                            // not carry the boundary edge cannot be the intended support face.
                            TopTools_IndexedMapOfShape faceEdges;
                            TopExp::MapShapes(face, TopAbs_EDGE, faceEdges);
                            if (!faceEdges.Contains(edge)) {
                                throw Standard_Failure(
                                    "Support face does not contain its boundary edge "
                                    "(the face reference is out of date)"
                                );
                            }
                            if (!bnd) {
                                // not a boundary edge: safe to add it directly
                                builder.Add(TopoDS::Edge(edge), TopoDS::Face(face), cont, bnd);
                            }
                            else {
                                // boundary edge: try to add it to the test wire first
                                testWire.Add(TopoDS::Edge(edge));
                                if (testWire.IsDone()) {
                                    builder.Add(TopoDS::Edge(edge), TopoDS::Face(face), cont, bnd);
                                }
                                else {
                                    throw Standard_Failure(
                                        "Boundary edges must be added in a consecutive order"
                                    );
                                }
                            }
                        }
                        else {
                            throw Standard_Failure("Sub-shape is not a face");
                        }
                    }
                }
                else {
                    throw Standard_Failure("Sub-shape is not an edge");
                }
            }
        }
    }
    else {
        throw Standard_Failure("Number of links does not match with the number of orders");
    }
}

// Add free support faces with their continuities
void Filling::addConstraints(
    BRepFill_Filling& builder,
    const App::PropertyLinkSubList& faces,
    const App::PropertyIntegerList& orders
)
{
    auto face_obj = faces.getValues();
    auto face_sub = faces.getSubValues();
    auto contvals = orders.getValues();

    if (face_obj.size() == face_sub.size() && face_obj.size() == contvals.size()) {
        for (std::size_t index = 0; index < face_obj.size(); index++) {
            App::DocumentObject* obj = face_obj[index];
            const std::string& sub = face_sub[index];
            if (obj && obj->isDerivedFrom<Part::Feature>()) {
                const Part::TopoShape& shape = static_cast<Part::Feature*>(obj)->Shape.getShape();
                TopoDS_Shape face = shape.getSubShape(sub.c_str());
                if (!face.IsNull() && face.ShapeType() == TopAbs_FACE) {
                    GeomAbs_Shape cont = static_cast<GeomAbs_Shape>(contvals[index]);
                    builder.Add(TopoDS::Face(face), cont);
                }
                else {
                    throw Standard_Failure("Sub-shape is not a face");
                }
            }
        }
    }
    else {
        throw Standard_Failure("Number of links does not match with the number of orders");
    }
}

void Filling::addConstraints(BRepFill_Filling& builder, const App::PropertyLinkSubList& pointsList)
{
    auto points = pointsList.getSubListValues();
    for (const auto& it : points) {
        App::DocumentObject* obj = it.first;
        std::vector<std::string> sub = it.second;
        if (obj && obj->isDerivedFrom<Part::Feature>()) {
            const Part::TopoShape& shape = static_cast<Part::Feature*>(obj)->Shape.getShape();
            for (const auto& jt : sub) {
                TopoDS_Shape subShape = shape.getSubShape(jt.c_str());
                if (!subShape.IsNull() && subShape.ShapeType() == TopAbs_VERTEX) {
                    gp_Pnt pnt = BRep_Tool::Pnt(TopoDS::Vertex(subShape));
                    builder.Add(pnt);
                }
            }
        }
    }
}

namespace
{

struct FaceSig
{
    int type {-1};
    gp_Vec normal;
    int outerEdges {0};  // number of edges of the outer wire: a trimmed outline changes it
};

// Surface type and oriented normal of a face at the midpoint of one of its edges.
bool faceSignature(const TopoDS_Shape& face, const TopoDS_Shape& edge, FaceSig& out)
{
    if (face.IsNull() || edge.IsNull() || face.ShapeType() != TopAbs_FACE
        || edge.ShapeType() != TopAbs_EDGE) {
        return false;
    }
    Standard_Real first = 0.0;
    Standard_Real last = 0.0;
    Handle(Geom2d_Curve) c2d = BRep_Tool::CurveOnSurface(TopoDS::Edge(edge), TopoDS::Face(face), first, last);
    if (c2d.IsNull()) {
        return false;
    }
    gp_Pnt2d uv = c2d->Value(0.5 * (first + last));
    BRepGProp_Face gf(TopoDS::Face(face));
    gp_Pnt p;
    gp_Vec n;
    gf.Normal(uv.X(), uv.Y(), p, n);
    if (n.Magnitude() < 1e-12) {
        return false;
    }
    n.Normalize();
    out.type = static_cast<int>(BRepAdaptor_Surface(TopoDS::Face(face)).GetType());
    out.normal = n;
    TopoDS_Wire outer = BRepTools::OuterWire(TopoDS::Face(face));
    TopTools_IndexedMapOfShape wireEdges;
    if (!outer.IsNull()) {
        TopExp::MapShapes(outer, TopAbs_EDGE, wireEdges);
    }
    out.outerEdges = wireEdges.Extent();
    return true;
}

std::string encodeSig(const FaceSig& s)
{
    std::ostringstream os;
    os.precision(17);
    os << s.type << ' ' << s.normal.X() << ' ' << s.normal.Y() << ' ' << s.normal.Z() << ' '
       << s.outerEdges;
    return os.str();
}

bool decodeSig(const std::string& str, FaceSig& s)
{
    std::istringstream is(str);
    double x = 0;
    double y = 0;
    double z = 0;
    if (!(is >> s.type >> x >> y >> z >> s.outerEdges)) {
        return false;
    }
    s.normal = gp_Vec(x, y, z);
    return true;
}

const TopoDS_Shape subShape(const App::DocumentObject* obj, const std::string& sub)
{
    if (!obj || !obj->isDerivedFrom<Part::Feature>() || sub.empty()) {
        return TopoDS_Shape();
    }
    return static_cast<const Part::Feature*>(obj)->Shape.getShape().getSubShape(sub.c_str());
}

// The mirror lost the support face's mapped name ("?"), but the boundary edge still resolves. The
// face is the unique face adjacent to that edge that has the surface type, the number of outer-wire
// edges and (within 0.5 degrees) the oriented normal the support face had when it last resolved
// (same plane and same outer outline, as ruled in DECISIONS.md D6; a face whose outline was trimmed
// is a different element). Returns "" when there is none or
// more than one.
std::string rescueFace(const App::DocumentObject* obj, const std::string& edgeSub, const std::string& sigStr)
{
    FaceSig want;
    if (!decodeSig(sigStr, want) || !obj || !obj->isDerivedFrom<Part::Feature>()) {
        return std::string();
    }
    TopoDS_Shape edge = subShape(obj, edgeSub);
    if (edge.IsNull() || edge.ShapeType() != TopAbs_EDGE) {
        return std::string();
    }
    TopoDS_Shape whole = static_cast<const Part::Feature*>(obj)->Shape.getShape().getShape();
    TopTools_IndexedDataMapOfShapeListOfShape ancestors;
    TopExp::MapShapesAndAncestors(whole, TopAbs_EDGE, TopAbs_FACE, ancestors);
    if (!ancestors.Contains(edge)) {
        return std::string();
    }
    TopTools_IndexedMapOfShape faces;
    TopExp::MapShapes(whole, TopAbs_FACE, faces);

    int found = 0;
    int foundIndex = 0;
    TopTools_IndexedMapOfShape seen;
    for (const TopoDS_Shape& cand : ancestors.FindFromKey(edge)) {
        if (seen.Contains(cand)) {
            continue;
        }
        seen.Add(cand);
        FaceSig got;
        if (faceSignature(cand, edge, got) && got.type == want.type
            && got.outerEdges == want.outerEdges && got.normal.Dot(want.normal) > 0.99996) {
            ++found;
            foundIndex = faces.FindIndex(cand);
        }
    }
    if (found != 1 || foundIndex == 0) {
        return std::string();
    }
    return "Face" + std::to_string(foundIndex);
}

// Signatures for the current names; an entry that cannot be computed keeps its old value.
std::vector<std::string> currentSigs(
    const std::vector<App::DocumentObject*>& objs,
    const std::vector<std::string>& edgeSubs,
    const std::vector<std::string>& names,
    const std::vector<std::string>& old
)
{
    std::vector<std::string> out(names.size());
    for (std::size_t i = 0; i < names.size(); ++i) {
        if (names[i].empty()) {
            continue;
        }
        if (old.size() == names.size()) {
            out[i] = old[i];
        }
        if (i >= objs.size() || i >= edgeSubs.size()) {
            continue;
        }
        TopoDS_Shape face = subShape(objs[i], names[i]);
        TopoDS_Shape edge = subShape(objs[i], edgeSubs[i]);
        FaceSig s;
        if (!face.IsNull() && !edge.IsNull() && faceSignature(face, edge, s)) {
            out[i] = encodeSig(s);
        }
    }
    return out;
}

}  // namespace

// The face names of BoundaryFaces and UnboundFaces are bare strings, which the link layer does not
// follow when the model changes. The hidden link property mirrors them with the edges' objects; the
// link layer rescues those, and the strings are rewritten from them. The links are rebuilt from the
// strings whenever the user (or a stock file) supplies the strings. When the link layer cannot
// rescue a face (its name becomes "?"), the face is looked for locally through the boundary edge,
// by the surface type and normal it had when it last resolved.
std::vector<std::string> Filling::followedFaces(
    const App::PropertyLinkSubList& edges,
    App::PropertyStringList& faces,
    App::PropertyLinkSubList& links,
    App::PropertyStringList& sigs
)
{
    std::vector<std::string> names = faces.getValues();
    auto objs = edges.getValues();
    auto edgeSubs = edges.getSubValues();
    if (names.size() != objs.size() || objs.empty() || edgeSubs.size() != objs.size()) {
        return names;
    }
    std::vector<std::string> oldSigs = sigs.getValues();

    bool rebuild = faces.isTouched() || links.getValues() != objs;
    if (!rebuild) {
        std::vector<std::string> followed = links.getSubValues();
        if (followed.size() != names.size()) {
            rebuild = true;
        }
        else {
            for (std::size_t i = 0; i < followed.size(); ++i) {
                if (!followed[i].empty() && followed[i][0] == '?') {
                    std::string rescued;
                    if (oldSigs.size() == names.size()) {
                        rescued = rescueFace(objs[i], edgeSubs[i], oldSigs[i]);
                    }
                    if (rescued.empty()) {
                        throw Standard_Failure("Support face reference is lost");
                    }
                    followed[i] = rescued;
                    rebuild = true;
                }
            }
            if (followed != names) {
                faces.setValues(followed);
                names = followed;
            }
        }
    }
    if (rebuild) {
        links.setValues(objs, names);
    }
    std::vector<std::string> newSigs = currentSigs(objs, edgeSubs, names, oldSigs);
    if (newSigs != oldSigs) {
        sigs.setValues(newSigs);
    }
    return names;
}

// Build the mirror and the signatures from the strings of a freshly restored document, before any
// edit, so a file saved by stock FreeCAD (no mirror) follows its faces from the first edit on.
void Filling::seedMirror(
    const App::PropertyLinkSubList& edges,
    const App::PropertyStringList& faces,
    App::PropertyLinkSubList& links,
    App::PropertyStringList& sigs
)
{
    const std::vector<std::string>& names = faces.getValues();
    auto objs = edges.getValues();
    auto edgeSubs = edges.getSubValues();
    if (names.size() != objs.size() || objs.empty() || edgeSubs.size() != objs.size()) {
        return;
    }
    if (links.getValues() != objs || links.getSubValues().size() != names.size()) {
        links.setValues(objs, names);
    }
    std::vector<std::string> newSigs = currentSigs(objs, edgeSubs, names, sigs.getValues());
    if (newSigs != sigs.getValues()) {
        sigs.setValues(newSigs);
    }
}

void Filling::onDocumentRestored()
{
    Part::Spline::onDocumentRestored();
    bool wasTouched = isTouched();
    seedMirror(BoundaryEdges, BoundaryFaces, BoundaryFaceLinks, BoundaryFaceSig);
    seedMirror(UnboundEdges, UnboundFaces, UnboundFaceLinks, UnboundFaceSig);
    if (!wasTouched) {
        purgeTouched();
    }
}

App::DocumentObjectExecReturn* Filling::execute()
{
    // Assign Variables
    unsigned int degree = Degree.getValue();
    unsigned int ptsoncurve = PointsOnCurve.getValue();
    unsigned int numIter = Iterations.getValue();
    bool anisotropy = Anisotropy.getValue();
    double tol2d = Tolerance2d.getValue();
    double tol3d = Tolerance3d.getValue();
    double tolG1 = TolAngular.getValue();
    double tolG2 = TolCurvature.getValue();
    unsigned int maxdeg = MaximumDegree.getValue();
    unsigned int maxseg = MaximumSegments.getValue();

    try {
        BRepFill_Filling
            builder(degree, ptsoncurve, numIter, anisotropy, tol2d, tol3d, tolG1, tolG2, maxdeg, maxseg);

        if ((BoundaryEdges.getSize()) < 1) {
            return new App::DocumentObjectExecReturn("Border must have at least one curve defined.");
        }

        // Load the initial surface if set
        App::DocumentObject* initFace = InitialFace.getValue();
        if (initFace && initFace->isDerivedFrom<Part::Feature>()) {
            const Part::TopoShape& shape = static_cast<Part::Feature*>(initFace)->Shape.getShape();
            std::vector<std::string> subNames = InitialFace.getSubValues();
            for (const auto& it : subNames) {
                TopoDS_Shape subShape = shape.getSubShape(it.c_str());
                if (!subShape.IsNull() && subShape.ShapeType() == TopAbs_FACE) {
                    builder.LoadInitSurface(TopoDS::Face(subShape));
                    break;
                }
            }
        }

        // Add the constraints of border curves/faces (bound)
        int numBoundaries = BoundaryEdges.getSize();
        addConstraints(
            builder,
            BoundaryEdges,
            followedFaces(BoundaryEdges, BoundaryFaces, BoundaryFaceLinks, BoundaryFaceSig),
            BoundaryOrder,
            Standard_True
        );

        // Add additional edge constraints if available (unbound)
        if (UnboundEdges.getSize() > 0) {
            addConstraints(
                builder,
                UnboundEdges,
                followedFaces(UnboundEdges, UnboundFaces, UnboundFaceLinks, UnboundFaceSig),
                UnboundOrder,
                Standard_False
            );
        }

        // Add additional constraint on free faces
        if (FreeFaces.getSize() > 0) {
            addConstraints(builder, FreeFaces, FreeOrder);
        }

        // App point constraints
        if (Points.getSize() > 0) {
            addConstraints(builder, Points);
        }

        // Build the face
        if (numBoundaries > 1) {
            builder.Build();
        }
        if (!builder.IsDone()) {
            throw Standard_Failure("Failed to create a face from constraints");
        }

        // Return the face
        TopoDS_Face aFace = builder.Face();
        this->Shape.setValue(aFace);
        return App::DocumentObject::StdReturn;
    }
    catch (Standard_Failure& e) {
        return new App::DocumentObjectExecReturn(e.GetMessageString());
    }
}
