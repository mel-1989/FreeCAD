// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2026 The FreeCAD project association AISBL              *
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
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU     *
 *   Lesser General Public License for more details.                       *
 *                                                                         *
 *   You should have received a copy of the GNU Lesser General Public      *
 *   License along with FreeCAD. If not, see                               *
 *   <https://www.gnu.org/licenses/>.                                      *
 *                                                                         *
 **************************************************************************/

// G12 slices 2a and 2b. Sources: CAx-IF Recommended Practices for the Representation and Presentation of PMI (AP242), for
// presentation geometry authored by the source file and kept in its coordinates; ISO 10303-242 for the annotation
// occurrences that carry it. Slice 2b lays out items without a presentation (see ViewProviderPmiLayout.cpp for the
// symbol sources): a label at the centre of the governed faces, offset along the first face's outward normal.

#include <algorithm>
#include <array>
#include <cmath>
#include <map>
#include <set>
#include <string>
#include <vector>

#include <BRepAdaptor_Curve.hxx>
#include <BRepBndLib.hxx>
#include <BRepBuilderAPI_Copy.hxx>
#include <BRepClass_FaceClassifier.hxx>
#include <BRepGProp.hxx>
#include <BRepGProp_Face.hxx>
#include <GProp_GProps.hxx>
#include <Geom_Surface.hxx>
#include <GeomAPI_ProjectPointOnSurf.hxx>
#include <gp_Pnt2d.hxx>
#include <Bnd_OBB.hxx>
#include <BRepMesh_IncrementalMesh.hxx>
#include <BRep_Tool.hxx>
#include <Bnd_Box.hxx>
#include <GCPnts_TangentialDeflection.hxx>
#include <Poly_Polygon3D.hxx>
#include <Poly_Triangulation.hxx>
#include <Standard_Failure.hxx>
#include <TopExp_Explorer.hxx>
#include <TopLoc_Location.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Edge.hxx>
#include <TopoDS_Face.hxx>
#include <TopoDS_Shape.hxx>
#include <gp_Pnt.hxx>

#include <QTimer>

#include <Inventor/SbVec3f.h>
#include <Inventor/nodes/SoBaseColor.h>
#include <Inventor/nodes/SoCoordinate3.h>
#include <Inventor/nodes/SoDrawStyle.h>
#include <Inventor/nodes/SoIndexedFaceSet.h>
#include <Inventor/nodes/SoInfo.h>
#include <Inventor/nodes/SoIndexedLineSet.h>
#include <Inventor/nodes/SoLightModel.h>
#include <Inventor/nodes/SoPickStyle.h>
#include <Inventor/nodes/SoSeparator.h>
#include <Inventor/nodes/SoShapeHints.h>
#include <Inventor/nodes/SoTransform.h>

#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentObject.h>
#include <App/GeoFeature.h>
#include <App/GroupExtension.h>
#include <Base/Console.h>
#include <Base/Interpreter.h>
#include <Gui/Application.h>
#include <Gui/Document.h>
#include <Gui/Selection/Selection.h>
#include <Mod/Part/App/PmiFeature.h>
#include <Mod/Part/App/TopoShape.h>

#include <tuple>

#include <Mod/Part/App/PartFeature.h>

#include "ViewProviderExt.h"
#include "ViewProviderPmi.h"
#include "ViewProviderPmiLayout.h"

using namespace PartGui;

namespace
{

/// One connection per document that tells the item providers of that document to re-place their drawings when any
/// Placement changes (a body moved, or a container of it did); cheaper than one connection per item
class PlacementWatcher
{
public:
    static PlacementWatcher& instance()
    {
        static PlacementWatcher w;
        return w;
    }

    void add(App::Document* doc, ViewProviderPmiItem* vp)
    {
        auto& e = docs[doc];
        if (e.items.empty()) {
            e.connection = doc->signalChangedObject.connect(
                [this, doc](const App::DocumentObject& obj, const App::Property& prop) { changed(doc, obj, prop); }
            );
        }
        e.items.insert(vp);
    }

    void remove(App::Document* doc, ViewProviderPmiItem* vp)
    {
        auto it = docs.find(doc);
        if (it == docs.end()) {
            return;
        }
        it->second.items.erase(vp);
        if (it->second.items.empty()) {
            docs.erase(it);
        }
    }

private:
    struct Entry
    {
        std::set<ViewProviderPmiItem*> items;
        fastsignals::scoped_connection connection;
    };

    void changed(App::Document* doc, const App::DocumentObject& obj, const App::Property& prop)
    {
        const char* name = prop.getName();
        if (!name) {
            return;
        }
        const std::string n = name;
        if (n != "Placement" && !(n == "Shape" && dynamic_cast<const Part::Feature*>(&obj))) {
            return;
        }
        auto it = docs.find(doc);
        if (it == docs.end()) {
            return;
        }
        const std::vector<ViewProviderPmiItem*> items(it->second.items.begin(), it->second.items.end());
        for (auto* vp : items) {
            if (n == "Placement") {
                vp->updateTransform();
            }
            else {
                vp->governedShapeChanged(&obj);
            }
        }
    }

    std::map<App::Document*, Entry> docs;
};

/// Selecting a PMI item highlights the faces it governs through the view provider of the governed object (per-face
/// materials in the scene graph only: no property of the object changes and nothing is saved); the selection itself
/// holds only the item. Deselecting the item, clearing the selection or deleting the item restores the colours
class PmiSelectionObserver: public Gui::SelectionObserver
{
public:
    PmiSelectionObserver()
        : Gui::SelectionObserver(true, Gui::ResolveMode::NoResolve)
    {}

    static void ensure()
    {
        static PmiSelectionObserver* inst = new PmiSelectionObserver();  // lives for the session
        (void)inst;
    }

private:
    struct Body
    {
        std::string doc, obj;
        bool operator<(const Body& o) const
        {
            return std::tie(doc, obj) < std::tie(o.doc, o.obj);
        }
    };
    using Faces = std::map<Body, std::set<int>>;  // governed object -> 1-based face numbers

    void onSelectionChanged(const Gui::SelectionChanges& msg) override
    {
        using T = Gui::SelectionChanges;
        switch (msg.Type) {
            case T::AddSelection:
                if (msg.pSubName && *msg.pSubName) {
                    return;
                }
                add(msg.pDocName, msg.pObjectName);
                break;
            case T::RmvSelection:
                if (msg.pSubName && *msg.pSubName) {
                    return;
                }
                remove(msg.pDocName, msg.pObjectName);
                break;
            case T::SetSelection: {
                std::set<Body> touched = clearTracked();
                for (auto& o : Gui::Selection().getSelection(msg.pDocName, Gui::ResolveMode::NoResolve)) {
                    if (o.pObject) {
                        track(o.DocName, o.FeatName, touched);
                    }
                }
                refresh(touched);
                break;
            }
            case T::ClrSelection:
                refresh(clearTracked());
                break;
            default:
                break;
        }
    }

    static std::string key(const char* doc, const char* obj)
    {
        return std::string(doc ? doc : "") + "#" + (obj ? obj : "");
    }

    std::set<Body> clearTracked()
    {
        std::set<Body> bodies;
        for (auto& [k, faces] : tracked) {
            for (auto& [b, f] : faces) {
                bodies.insert(b);
            }
        }
        tracked.clear();
        return bodies;
    }

    void track(const char* docName, const char* objName, std::set<Body>& touched)
    {
        App::Document* doc = App::GetApplication().getDocument(docName);
        App::DocumentObject* o = doc && objName ? doc->getObject(objName) : nullptr;
        auto* item = o ? dynamic_cast<Part::PmiItem*>(o) : nullptr;
        if (!item || tracked.count(key(docName, objName))) {
            return;
        }
        Faces mine;
        for (App::DocumentObject* target : item->References.getValues()) {
            if (!target || !target->isAttachedToDocument()) {
                continue;
            }
            for (const auto& s : item->References.getSubValues(target)) {
                if (s.rfind("Face", 0) == 0 && s.size() > 4) {
                    mine[Body {docName, target->getNameInDocument()}].insert(std::atoi(s.c_str() + 4));
                }
            }
        }
        for (auto& [b, f] : mine) {
            touched.insert(b);
        }
        tracked[key(docName, objName)] = std::move(mine);
    }

    void add(const char* docName, const char* objName)
    {
        std::set<Body> touched;
        track(docName, objName, touched);
        refresh(touched);
    }

    void remove(const char* docName, const char* objName)
    {
        auto it = tracked.find(key(docName, objName));
        if (it == tracked.end()) {
            return;
        }
        std::set<Body> touched;
        for (auto& [b, f] : it->second) {
            touched.insert(b);
        }
        tracked.erase(it);
        refresh(touched);
    }

    /// Set or clear the highlight of each body from the union of the faces of every tracked item
    void refresh(const std::set<Body>& bodies)
    {
        for (const Body& b : bodies) {
            App::Document* doc = App::GetApplication().getDocument(b.doc.c_str());
            App::DocumentObject* o = doc ? doc->getObject(b.obj.c_str()) : nullptr;
            auto* feat = o ? dynamic_cast<Part::Feature*>(o) : nullptr;
            auto* vp = feat ? dynamic_cast<PartGui::ViewProviderPartExt*>(Gui::Application::Instance->getViewProvider(feat))
                            : nullptr;
            if (!vp) {
                continue;
            }
            std::set<int> faces;
            for (auto& [k, f] : tracked) {
                auto it = f.find(b);
                if (it != f.end()) {
                    faces.insert(it->second.begin(), it->second.end());
                }
            }
            const int n = static_cast<int>(feat->Shape.getShape().countSubShapes("Face"));
            if (faces.empty() || n < 2) {
                vp->unsetHighlightedFaces();
                continue;
            }
            const std::vector<App::Material> base = vp->ShapeAppearance.getValues();
            std::vector<App::Material> mats(n);
            for (int i = 0; i < n; ++i) {
                if (!base.empty()) {
                    mats[i] = base[static_cast<size_t>(i) < base.size() ? i : 0];
                }
                if (faces.count(i + 1)) {
                    mats[i].diffuseColor = Base::Color(1.0f, 0.55f, 0.0f);
                }
            }
            vp->setHighlightedFaces(mats);
        }
    }

    std::map<std::string, Faces> tracked;
};

/// Polylines of the edges and triangles of the faces of a presentation shape. Edges that carry a 3D polygon use it,
/// the others are sampled by tangential deflection; faces use their triangulation or are meshed on a copy
void discretize(
    const TopoDS_Shape& shape,
    std::vector<SbVec3f>& pts,
    std::vector<int32_t>& lineIdx,
    std::vector<int32_t>& faceIdx
)
{
    Bnd_Box box;
    BRepBndLib::Add(shape, box);
    const double diag = box.IsVoid() ? 1.0 : std::sqrt(box.SquareExtent());
    const double defl = std::max(diag * 1e-3, 1e-4);

    for (TopExp_Explorer ex(shape, TopAbs_EDGE); ex.More(); ex.Next()) {
        const TopoDS_Edge& edge = TopoDS::Edge(ex.Current());
        if (BRep_Tool::Degenerated(edge)) {
            continue;
        }
        TopLoc_Location loc;
        std::vector<gp_Pnt> line;
        Handle(Poly_Polygon3D) poly = BRep_Tool::Polygon3D(edge, loc);
        if (!poly.IsNull()) {
            for (int i = 1; i <= poly->NbNodes(); ++i) {
                line.push_back(poly->Nodes().Value(i).Transformed(loc.Transformation()));
            }
        }
        else {
            BRepAdaptor_Curve curve(edge);
            GCPnts_TangentialDeflection td(curve, 0.1, defl);
            for (int i = 1; i <= td.NbPoints(); ++i) {
                line.push_back(td.Value(i));
            }
        }
        if (line.size() < 2) {
            continue;
        }
        for (const gp_Pnt& p : line) {
            lineIdx.push_back(static_cast<int32_t>(pts.size()));
            pts.emplace_back(static_cast<float>(p.X()), static_cast<float>(p.Y()), static_cast<float>(p.Z()));
        }
        lineIdx.push_back(-1);
    }

    for (TopExp_Explorer ex(shape, TopAbs_FACE); ex.More(); ex.Next()) {
        TopoDS_Face face = TopoDS::Face(ex.Current());
        TopLoc_Location loc;
        Handle(Poly_Triangulation) tri = BRep_Tool::Triangulation(face, loc);
        if (tri.IsNull()) {
            BRepBuilderAPI_Copy copy(face);
            face = TopoDS::Face(copy.Shape());
            BRepMesh_IncrementalMesh mesher(face, defl);
            tri = BRep_Tool::Triangulation(face, loc);
        }
        if (tri.IsNull()) {
            continue;
        }
        const int base = static_cast<int>(pts.size());
        for (int i = 1; i <= tri->NbNodes(); ++i) {
            gp_Pnt p = tri->Node(i).Transformed(loc.Transformation());
            pts.emplace_back(static_cast<float>(p.X()), static_cast<float>(p.Y()), static_cast<float>(p.Z()));
        }
        for (int i = 1; i <= tri->NbTriangles(); ++i) {
            int a, b, c;
            tri->Triangle(i).Get(a, b, c);
            faceIdx.push_back(base + a - 1);
            faceIdx.push_back(base + b - 1);
            faceIdx.push_back(base + c - 1);
            faceIdx.push_back(-1);
        }
    }
}

}  // namespace

std::set<ViewProviderPmiItem*>& ViewProviderPmiItem::aliveProviders()
{
    static std::set<ViewProviderPmiItem*> alive;
    return alive;
}

namespace
{

App::DocumentObject* groupOf(const App::DocumentObject* obj)
{
    return App::GroupExtension::getGroupOfObject(obj);
}

}  // namespace

// ------------------------------------------------------------------------------------------------------------
PROPERTY_SOURCE(PartGui::ViewProviderPmiItem, Gui::ViewProviderDocumentObject)

ViewProviderPmiItem::ViewProviderPmiItem()
{
    aliveProviders().insert(this);
}

ViewProviderPmiItem::~ViewProviderPmiItem()
{
    aliveProviders().erase(this);
    if (drawRoot) {
        drawRoot->unref();
    }
    if (leaderRoot) {
        leaderRoot->unref();
    }
    if (watchedDoc) {
        PlacementWatcher::instance().remove(watchedDoc, this);
    }
}

std::vector<std::string> ViewProviderPmiItem::getDisplayModes() const
{
    return {"Pmi"};
}

void ViewProviderPmiItem::setDisplayMode(const char* mode)
{
    Gui::ViewProviderDocumentObject::setDisplayMode(mode);
}

void ViewProviderPmiItem::attach(App::DocumentObject* obj)
{
    Gui::ViewProviderDocumentObject::attach(obj);
    PmiSelectionObserver::ensure();

    modeRoot = new SoSeparator;
    drawRoot = new SoSeparator;
    drawRoot->ref();
    drawRoot->setName(SbName((std::string("PMI_") + obj->getNameInDocument()).c_str()));
    auto* pick = new SoPickStyle;
    pick->style = SoPickStyle::SHAPE;  // clicking an item in the view picks the item, not the body behind it
    drawRoot->addChild(pick);
    auto* light = new SoLightModel;
    light->model = SoLightModel::BASE_COLOR;
    drawRoot->addChild(light);
    auto* style = new SoDrawStyle;
    style->lineWidth = 1.0f;
    drawRoot->addChild(style);
    transform = new SoTransform;
    drawRoot->addChild(transform);
    color = new SoBaseColor;
    color->rgb.setValue(0.05f, 0.25f, 0.65f);
    drawRoot->addChild(color);
    auto* hints = new SoShapeHints;
    hints->vertexOrdering = SoShapeHints::UNKNOWN_ORDERING;
    hints->shapeType = SoShapeHints::UNKNOWN_SHAPE_TYPE;
    drawRoot->addChild(hints);
    coords = new SoCoordinate3;
    drawRoot->addChild(coords);
    lines = new SoIndexedLineSet;
    drawRoot->addChild(lines);
    faces = new SoIndexedFaceSet;
    drawRoot->addChild(faces);
    info = new SoInfo;  // the text of a fallback label; empty for presentation items
    info->string.setValue("");
    drawRoot->addChild(info);
    // lines are hard to hit: an invisible quad over the drawing (Coin still picks invisible shapes) makes the whole
    // label or annotation one click target
    auto* proxy = new SoSeparator;
    auto* invisible = new SoDrawStyle;
    invisible->style = SoDrawStyle::INVISIBLE;
    proxy->addChild(invisible);
    proxyCoords = new SoCoordinate3;
    proxy->addChild(proxyCoords);
    proxyFaces = new SoIndexedFaceSet;
    proxy->addChild(proxyFaces);
    drawRoot->addChild(proxy);

    leaderRoot = new SoSeparator;  // not named PMI_*: the drawn-item search counts one separator per item
    leaderRoot->ref();
    leaderRoot->setName(SbName((std::string("Leader_") + obj->getNameInDocument()).c_str()));
    auto* leaderPick = new SoPickStyle;
    leaderPick->style = SoPickStyle::UNPICKABLE;  // a leader never takes a click from a label or from the body
    leaderRoot->addChild(leaderPick);
    auto* leaderLight = new SoLightModel;
    leaderLight->model = SoLightModel::BASE_COLOR;
    leaderRoot->addChild(leaderLight);
    leaderTransform = new SoTransform;
    leaderRoot->addChild(leaderTransform);
    auto* leaderColor = new SoBaseColor;
    leaderColor->rgb.setValue(0.05f, 0.25f, 0.65f);
    leaderRoot->addChild(leaderColor);
    leaderCoords = new SoCoordinate3;
    leaderRoot->addChild(leaderCoords);
    leaderLines = new SoIndexedLineSet;
    leaderRoot->addChild(leaderLines);

    addDisplayMaskMode(modeRoot, "Pmi");
    setDisplayMaskMode("Pmi");
    attached = true;

    watchedDoc = obj->getDocument();
    PlacementWatcher::instance().add(watchedDoc, this);
    refresh();
}

void ViewProviderPmiItem::finishRestoring()
{
    Gui::ViewProviderDocumentObject::finishRestoring();
    refresh();
}

App::DocumentObject* ViewProviderPmiItem::governedObject() const
{
    auto* item = getObject<Part::PmiItem>();
    if (!item) {
        return nullptr;
    }
    for (App::DocumentObject* o : item->References.getValues()) {
        if (o && o->isAttachedToDocument()) {
            return o;
        }
    }
    return nullptr;
}

bool ViewProviderPmiItem::fallbackEligible() const
{
    auto* item = getObject<Part::PmiItem>();
    if (!item || item->Unattached.getValue() || dynamic_cast<Part::PmiAnnotation*>(item)) {
        return false;
    }
    auto* feat = dynamic_cast<Part::Feature*>(governedObject());
    return feat && !feat->Shape.getShape().isNull();
}

PmiDrawMode ViewProviderPmiItem::drawMode() const
{
    auto* item = getObject<Part::PmiItem>();
    if (!item) {
        return PmiDrawMode::None;
    }
    const bool hasPresentation = !item->Presentation.getShape().isNull();
    const PmiDrawMode own = hasPresentation ? PmiDrawMode::Presentation
                                            : (fallbackEligible() ? PmiDrawMode::Fallback : PmiDrawMode::None);
    auto* datum = dynamic_cast<Part::PmiDatum*>(item);
    if (!datum) {
        return own;
    }
    // ruling 3 of the slice 2 brief: one symbol per letter per governed object, the first use that has a presentation,
    // or, when no use of the letter has one, the first use
    App::DocumentObject* group = groupOf(item);
    auto* grp = group ? group->getExtensionByType<App::GroupExtension>(true) : nullptr;
    if (!grp) {
        return own;
    }
    const App::DocumentObject* body = governedObject();
    const Part::PmiDatum* firstPresented = nullptr;
    const Part::PmiDatum* firstAny = nullptr;
    for (App::DocumentObject* o : grp->Group.getValues()) {
        auto* other = dynamic_cast<Part::PmiDatum*>(o);
        if (!other || other->Letter.getStrValue() != datum->Letter.getStrValue()) {
            continue;
        }
        const App::DocumentObject* ob = other->References.getValues().empty() ? nullptr : other->References.getValues().front();
        if (ob != body) {
            continue;
        }
        firstAny = firstAny ? firstAny : other;
        if (!firstPresented && !other->Presentation.getShape().isNull()) {
            firstPresented = other;
        }
    }
    if (firstPresented) {
        return firstPresented == datum ? PmiDrawMode::Presentation : PmiDrawMode::None;
    }
    return firstAny == datum ? own : PmiDrawMode::None;
}

bool ViewProviderPmiItem::wantVisible() const
{
    // the view provider's own Visibility changes first and the object's follows, so ask the view providers
    App::DocumentObject* group = groupOf(getObject());
    bool groupOn = true;
    if (group) {
        auto* gvp = dynamic_cast<Gui::ViewProviderDocumentObject*>(Gui::Application::Instance->getViewProvider(group));
        groupOn = gvp ? gvp->Visibility.getValue() : group->Visibility.getValue();
    }
    return Visibility.getValue() && groupOn;
}

namespace
{

/// The model diagonal: the bounding box of every Part feature of the document (the shapes carry their placement);
/// cached per object against the shape it was measured on, because every fallback item asks for it
double modelDiagonal(const App::Document* doc)
{
    static std::map<const App::DocumentObject*, std::pair<TopoDS_Shape, Bnd_Box>> cache;
    Bnd_Box all;
    for (App::DocumentObject* o : doc->getObjects()) {
        auto* feat = dynamic_cast<Part::Feature*>(o);
        if (!feat || feat->Shape.getShape().isNull()) {
            continue;
        }
        const TopoDS_Shape shape = feat->Shape.getShape().getShape();
        auto it = cache.find(feat);
        if (it == cache.end() || !it->second.first.IsPartner(shape) || !it->second.first.Location().IsEqual(shape.Location())) {
            Bnd_Box box;
            try {
                BRepBndLib::Add(shape, box, true);
            }
            catch (const Standard_Failure&) {
                continue;
            }
            it = cache.insert_or_assign(feat, std::make_pair(shape, box)).first;
        }
        // datum planes and axes are infinite: they say nothing about the size of the model
        if (!it->second.second.IsVoid() && !it->second.second.IsOpen() && it->second.second.SquareExtent() < 1e18) {
            all.Add(it->second.second);
        }
    }
    return all.IsVoid() ? 0.0 : std::sqrt(all.SquareExtent());
}

const std::pair<int, int> corners[4] = {{-1, -1}, {1, -1}, {1, 1}, {-1, 1}};

SbVec3f toSb(const gp_Pnt& p)
{
    return SbVec3f(static_cast<float>(p.X()), static_cast<float>(p.Y()), static_cast<float>(p.Z()));
}

}  // namespace

/// Where the fallback label of this item goes (check 21): the centre of the bounding box of the governed faces and the
/// outward normal of the first of them, in the governed object's own coordinates. Cached against the shape and the
/// references it was measured on, because stacking asks every fallback item of a face for the others' anchors
bool ViewProviderPmiItem::computeAnchor(FallbackAnchor& out)
{
    auto* item = getObject<Part::PmiItem>();
    auto* feat = dynamic_cast<Part::Feature*>(governedObject());
    if (!item || !feat) {
        return false;
    }
    const Part::TopoShape& ts = feat->Shape.getShape();
    const TopoDS_Shape whole = ts.getShape();
    std::string refs;
    for (const std::string& sub : item->References.getSubValues(feat)) {
        refs += sub + ",";
    }
    if (anchorCache.valid && anchorCache.feat == feat && anchorCache.refs == refs && anchorCache.shape.IsPartner(whole)
        && anchorCache.shape.Location().IsEqual(whole.Location())) {
        out = anchorCache.value;
        return out.ok;
    }
    const TopLoc_Location toLocal = whole.Location().Inverted();

    Bnd_Box box, otherBox;  // the faces govern; edges and vertices only when the item names no face
    TopoDS_Face firstFace;
    int named = 0, resolved = 0;
    for (const std::string& sub : item->References.getSubValues(feat)) {
        TopoDS_Shape s;
        ++named;
        try {
            if (sub.rfind("Face", 0) == 0 && sub.size() > 4) {
                s = ts.getSubShape(TopAbs_FACE, std::atoi(sub.c_str() + 4), true);
            }
            else if (sub.rfind("Edge", 0) == 0 && sub.size() > 4) {
                s = ts.getSubShape(TopAbs_EDGE, std::atoi(sub.c_str() + 4), true);
            }
            else if (sub.rfind("Vertex", 0) == 0 && sub.size() > 6) {
                s = ts.getSubShape(TopAbs_VERTEX, std::atoi(sub.c_str() + 6), true);
            }
        }
        catch (const Standard_Failure&) {
            continue;
        }
        if (s.IsNull()) {
            continue;
        }
        ++resolved;
        s = s.Moved(toLocal);
        BRepBndLib::Add(s, s.ShapeType() == TopAbs_FACE ? box : otherBox, true);
        if (firstFace.IsNull() && s.ShapeType() == TopAbs_FACE) {
            firstFace = TopoDS::Face(s);
        }
    }
    FallbackAnchor res;
    if (named > 0 && resolved == 0) {
        // every reference names a sub-shape that does not exist: no place for the label, so draw nothing
        if (!warnedMissingRef) {
            warnedMissingRef = true;
            Base::Console().warning(
                "PMI display: {}: its references name no existing face or edge of {}; no fallback label drawn\n",
                item->getNameInDocument(),
                feat->getNameInDocument()
            );
        }
    }
    else {
        if (box.IsVoid()) {
            box = otherBox;
        }
        Bnd_Box wholeBox;
        if (firstFace.IsNull()) {  // only an edge or nothing resolved: the outward direction comes from the whole object
            BRepBndLib::Add(whole.Moved(toLocal), wholeBox, true);
        }
        if (box.IsVoid()) {
            box = wholeBox;
        }
        if (!box.IsVoid()) {
            const gp_Pnt lo = box.CornerMin(), hi = box.CornerMax();
            res.centre = gp_Pnt((lo.X() + hi.X()) / 2, (lo.Y() + hi.Y()) / 2, (lo.Z() + hi.Z()) / 2);
            res.normal = gp_Dir(0, 0, 1);
            if (!firstFace.IsNull()) {
                try {
                    // the normal where the face is: at the middle of the parameter range when that lies inside the trimmed
                    // face, else at its centre of mass projected on the surface (when that lies inside), else the middle
                    BRepGProp_Face gf(firstFace);
                    double u0, u1, v0, v1;
                    gf.Bounds(u0, u1, v0, v1);
                    double u = (u0 + u1) / 2, v = (v0 + v1) / 2;
                    BRepClass_FaceClassifier mid(firstFace, gp_Pnt2d(u, v), 1e-7);
                    if (mid.State() != TopAbs_IN && mid.State() != TopAbs_ON) {
                        GProp_GProps props;
                        BRepGProp::SurfaceProperties(firstFace, props);
                        Handle(Geom_Surface) surf = BRep_Tool::Surface(firstFace);
                        if (!surf.IsNull() && props.Mass() > 0) {
                            GeomAPI_ProjectPointOnSurf proj(props.CentreOfMass(), surf);
                            if (proj.NbPoints() > 0) {
                                double pu, pv;
                                proj.LowerDistanceParameters(pu, pv);
                                BRepClass_FaceClassifier cls(firstFace, gp_Pnt2d(pu, pv), 1e-7);
                                if (cls.State() == TopAbs_IN || cls.State() == TopAbs_ON) {
                                    u = pu;
                                    v = pv;
                                }
                            }
                        }
                    }
                    gp_Pnt p;
                    gp_Vec n;
                    gf.Normal(u, v, p, n);
                    if (n.Magnitude() > 1e-12) {
                        res.normal = gp_Dir(n);
                    }
                }
                catch (const Standard_Failure&) {
                }
            }
            else if (!wholeBox.IsVoid()) {
                const gp_Pnt wl = wholeBox.CornerMin(), wh = wholeBox.CornerMax();
                const gp_Vec out(
                    res.centre.X() - (wl.X() + wh.X()) / 2,
                    res.centre.Y() - (wl.Y() + wh.Y()) / 2,
                    res.centre.Z() - (wl.Z() + wh.Z()) / 2
                );
                if (out.Magnitude() > 1e-9) {
                    res.normal = gp_Dir(out);
                }
            }
            res.ok = true;
        }
    }
    anchorCache.valid = true;
    anchorCache.feat = feat;
    anchorCache.refs = refs;
    anchorCache.shape = whole;
    anchorCache.value = res;
    out = res;
    return res.ok;
}

/// The nominal frame of the label (check 21): anchor, reading directions and the half extent of its pick quad in digit
/// heights. The extent is cached against the text, because laying out a face asks for every neighbour's
bool ViewProviderPmiItem::baseFrame(LabelFrame& f, double diag)
{
    auto* item = getObject<Part::PmiItem>();
    FallbackAnchor a;
    if (!item || !computeAnchor(a)) {
        return false;
    }
    const std::string text = PmiLayout::labelText(item);
    if (text.empty()) {
        return false;
    }
    if (!extentCache.valid || extentCache.text != text) {
        PmiLayout::Label label = PmiLayout::buildLabel(item);
        if (label.lines.empty()) {
            return false;
        }
        double hw = 0, hh = 0;
        for (const auto& pl : label.lines) {
            for (const auto& q : pl) {
                hw = std::max(hw, std::fabs(q[0]));
                hh = std::max(hh, std::fabs(q[1]));
            }
        }
        extentCache = {true, text, hw + 0.3, hh + 0.3};  // the pick quad is a little larger than the label
    }
    const gp_Dir normal = a.normal;
    f.h = 0.033 * diag;  // digit height of the label
    const gp_Vec up0 = std::fabs(normal.Z()) < 0.9 ? gp_Vec(0, 0, 1) : gp_Vec(0, 1, 0);
    f.right = gp_Dir(up0.Crossed(gp_Vec(normal)));  // right x up = normal: the label faces outward
    f.up = gp_Dir(gp_Vec(normal).Crossed(gp_Vec(f.right)));
    f.normal = normal;
    f.anchor = gp_Pnt(
        a.centre.X() + normal.X() * 0.05 * diag,
        a.centre.Y() + normal.Y() * 0.05 * diag,
        a.centre.Z() + normal.Z() * 0.05 * diag
    );
    f.origin = f.anchor;
    f.hw = extentCache.hw;
    f.hh = extentCache.hh;
    f.text = text;
    return true;
}

namespace
{

using LabelFrame = ViewProviderPmiItem::LabelFrame;

struct Vec2
{
    double x, y;
};

/// Whether two convex polygons (up to four corners, possibly degenerate to a segment) overlap with positive area
/// (separating axes from the edges of both)
bool convexOverlap(const std::array<Vec2, 4>& a, const std::array<Vec2, 4>& b)
{
    for (const std::array<Vec2, 4>* poly : {&a, &b}) {
        constexpr size_t n = 4;
        for (size_t i = 0; i < n; ++i) {
            const Vec2 p = (*poly)[i], q = (*poly)[(i + 1) % n];
            double ax = -(q.y - p.y), ay = q.x - p.x;
            const double len = std::hypot(ax, ay);
            if (len < 1e-12) {
                continue;
            }
            ax /= len;
            ay /= len;
            double amin = 1e300, amax = -1e300, bmin = 1e300, bmax = -1e300;
            for (const Vec2& v : a) {
                const double s = ax * v.x + ay * v.y;
                amin = std::min(amin, s);
                amax = std::max(amax, s);
            }
            for (const Vec2& v : b) {
                const double s = ax * v.x + ay * v.y;
                bmin = std::min(bmin, s);
                bmax = std::max(bmax, s);
            }
            if (amax <= bmin + 1e-9 || bmax <= amin + 1e-9) {
                return false;
            }
        }
    }
    return true;
}

/// The four corners of a label's pick rectangle (grown by the margin) relative to a point, as vectors
std::array<gp_Vec, 4> frameCorners(const LabelFrame& f, const gp_Pnt& origin, double margin)
{
    std::array<gp_Vec, 4> out;
    const double hw = f.hw * f.h + margin, hh = f.hh * f.h + margin;
    int i = 0;
    for (const auto& [sx, sy] : {std::pair<int, int> {-1, -1}, {1, -1}, {1, 1}, {-1, 1}}) {
        out[i++] = gp_Vec(
            f.anchor.X() - origin.X() + sx * hw * f.right.X() + sy * hh * f.up.X(),
            f.anchor.Y() - origin.Y() + sx * hw * f.right.Y() + sy * hh * f.up.Y(),
            f.anchor.Z() - origin.Z() + sx * hw * f.right.Z() + sy * hh * f.up.Z()
        );
    }
    return out;
}

/// Whether the two labels' rectangles (A grown by a gap of a fifth of a digit height) overlap as seen along a direction
bool coverAlong(const LabelFrame& a, const LabelFrame& b, const gp_Dir& d)
{
    // seen along d, labels whose circumscribed circles are apart (across d) cannot meet, whatever their depth
    const gp_Vec between(a.anchor, b.anchor);
    const gp_Vec across = between - gp_Vec(d) * between.Dot(gp_Vec(d));
    if (across.Magnitude() > a.h * std::hypot(a.hw, a.hh) + 0.2 * a.h + b.h * std::hypot(b.hw, b.hh)) {
        return false;
    }
    const gp_Dir e1 = std::fabs(d.Z()) < 0.9 ? gp_Dir(gp_Vec(0, 0, 1).Crossed(gp_Vec(d))) : gp_Dir(gp_Vec(0, 1, 0).Crossed(gp_Vec(d)));
    const gp_Dir e2 = gp_Dir(gp_Vec(d).Crossed(gp_Vec(e1)));
    std::array<Vec2, 4> pa, pb;
    const gp_Vec g1(e1), g2(e2);
    const auto ca = frameCorners(a, a.anchor, 0.2 * a.h);
    const auto cb = frameCorners(b, a.anchor, 0.0);
    for (size_t i = 0; i < 4; ++i) {
        pa[i] = {ca[i].Dot(g1), ca[i].Dot(g2)};
        pb[i] = {cb[i].Dot(g1), cb[i].Dot(g2)};
    }
    return convexOverlap(pa, pb);
}

/// Whether two labels would cover each other for a reader looking at either one from its front or from 30 degrees to
/// the left or right of its normal (the way a model is turned to read a face), or along the default isometric direction
/// of the application (a camera at +x, -y, +z of the model, in the governed object's own axes). A layout cannot know the
/// camera, so it protects the views a label is read from and the one a model opens in
bool framesCover(const LabelFrame& a, const LabelFrame& b)
{
    static const gp_Dir iso(1, -1, 1);
    constexpr double tilt = 30.0 * M_PI / 180.0;
    for (const LabelFrame* f : {&a, &b}) {
        const gp_Vec n(f->normal), r(f->right);
        const gp_Vec dirs[3] = {n, n * std::cos(tilt) + r * std::sin(tilt), n * std::cos(tilt) - r * std::sin(tilt)};
        for (const gp_Vec& d : dirs) {
            if (coverAlong(a, b, gp_Dir(d))) {
                return true;
            }
        }
    }
    return coverAlong(a, b, iso);
}

/// The grid cells a label may be moved to, nearest first: (i, j) is i pitches along the reading direction and j pitches
/// up. Up is preferred to down and the reading direction to the opposite one, so a stack of labels on one face grows
/// upward as it did in slice 2
const std::vector<std::pair<int, int>>& candidateCells()
{
    static const std::vector<std::pair<int, int>> cells = []() {
        constexpr int radius = 6;  // the reach of 0.25 diagonals is 5 cells of 1.5 digit heights at 0.033
        std::vector<std::pair<int, int>> v;
        for (int i = -radius; i <= radius; ++i) {
            for (int j = -radius; j <= radius; ++j) {
                v.emplace_back(i, j);
            }
        }
        auto cost = [](const std::pair<int, int>& c) {
            return 2.0 * c.first * c.first + (c.second >= 0 ? 1.0 : 2.0) * c.second * c.second + (c.first < 0 ? 0.01 : 0.0);
        };
        std::stable_sort(v.begin(), v.end(), [&](const auto& x, const auto& y) { return cost(x) < cost(y); });
        return v;
    }();
    return cells;
}

/// One governed object's laid-out fallback labels, kept until something they depend on changes so that the whole object
/// is laid out once per change and not once per item
struct LayoutEntry
{
    std::string signature;
    std::map<std::string, LabelFrame> frames;
};

std::map<const App::DocumentObject*, LayoutEntry>& layoutCache()
{
    static std::map<const App::DocumentObject*, LayoutEntry> cache;
    return cache;
}

}  // namespace

/// Fallback labels are laid out per governed object in object-name order. The first sits at its anchor (check 21). Each
/// next one stays at its anchor unless it would cover a label already placed, for a reader looking along the normal of
/// either label; then it moves, in whole pitches (3 digit heights), along its reading direction and up, to the nearest
/// free cell. Source: our own design; the CAx-IF recommended practice for PMI presentation asks only that annotations
/// stay legible and do not overlap, and gives no layout algorithm
bool ViewProviderPmiItem::finalFrame(LabelFrame& mine, double diag)
{
    auto* item = getObject<Part::PmiItem>();
    App::DocumentObject* gov = governedObject();
    std::vector<ViewProviderPmiItem*> peers;
    for (auto* vp : aliveProviders()) {
        auto* other = vp->getObject<Part::PmiItem>();
        if (other && vp->governedObject() == gov && other->getDocument() == item->getDocument()
            && vp->drawMode() == PmiDrawMode::Fallback) {
            peers.push_back(vp);
        }
    }
    std::sort(peers.begin(), peers.end(), [](ViewProviderPmiItem* x, ViewProviderPmiItem* y) {
        return std::string(x->getObject()->getNameInDocument()) < y->getObject()->getNameInDocument();
    });
    // what the layout depends on: the shape, the model diagonal and, per label, its name, references and text
    std::string signature;
    if (auto* feat = dynamic_cast<Part::Feature*>(gov)) {
        signature += std::to_string(reinterpret_cast<uintptr_t>(feat->Shape.getShape().getShape().TShape().get())) + ";";
        for (auto* vp : peers) {
            auto* it = vp->getObject<Part::PmiItem>();
            signature += it->getNameInDocument();
            signature += '|';
            for (const std::string& sub : it->References.getSubValues(feat)) {
                signature += sub + ",";
            }
            signature += '|' + PmiLayout::labelText(it) + ";";
        }
    }
    signature += std::to_string(diag);
    LayoutEntry& entry = layoutCache()[gov];
    if (entry.signature != signature) {
        entry.signature = signature;
        entry.frames.clear();
        std::vector<LabelFrame> placed;
        for (auto* vp : peers) {
            LabelFrame f;
            if (!vp->baseFrame(f, diag)) {
                continue;
            }
            const gp_Pnt base = f.anchor;
            const double pitch = 1.5 * f.h;  // cells of half a pitch: a stack still moves in whole pitches (two cells)
            const double reach = 0.25 * diag;  // a label stays within a quarter of the model diagonal of its anchor
            LabelFrame best = f;
            int fewest = 1 << 30;
            for (const auto& [ci, cj] : candidateCells()) {
                if (pitch * std::hypot(static_cast<double>(ci), static_cast<double>(cj)) > reach) {
                    continue;
                }
                f.anchor = gp_Pnt(
                    base.X() + f.right.X() * pitch * ci + f.up.X() * pitch * cj,
                    base.Y() + f.right.Y() * pitch * ci + f.up.Y() * pitch * cj,
                    base.Z() + f.right.Z() * pitch * ci + f.up.Z() * pitch * cj
                );
                int clashes = 0;
                for (const LabelFrame& p : placed) {
                    if (framesCover(f, p)) {
                        ++clashes;
                    }
                }
                if (clashes < fewest) {  // nearest cell first: the first free cell, else the one covering the fewest
                    fewest = clashes;
                    best = f;
                    if (!clashes) {
                        break;
                    }
                }
            }
            placed.push_back(best);
            entry.frames[vp->getObject()->getNameInDocument()] = best;
        }
    }
    auto it = entry.frames.find(item->getNameInDocument());
    if (it == entry.frames.end()) {
        return false;
    }
    mine = it->second;
    return true;
}

/// The fallback label (check 21 of the slice 2 brief): the text of the item laid out as line geometry, centred on the
/// centre of the bounding box of the governed faces and offset along the outward normal of the first of them by 5% of
/// the model diagonal, in the plane of that normal (readable from outside, up along the model's z where it can be).
/// The points are in the governed object's own coordinates; the transform of the drawing is its global placement
bool ViewProviderPmiItem::buildFallback(std::vector<SbVec3f>& pts, std::vector<int32_t>& lineIdx, std::string& text)
{
    auto* item = getObject<Part::PmiItem>();
    if (!item) {
        return false;
    }
    const double diag = modelDiagonal(item->getDocument());
    LabelFrame f;
    if (diag <= 0 || !finalFrame(f, diag)) {
        return false;
    }
    PmiLayout::Label label = PmiLayout::buildLabel(item);
    if (label.lines.empty()) {
        return false;
    }
    text = label.text;
    const gp_Dir rightDir = f.right, up = f.up;
    const gp_Pnt anchor = f.anchor;
    const double h = f.h;
    for (const auto& pl : label.lines) {
        for (const auto& q : pl) {
            const gp_Pnt p(
                anchor.X() + h * (q[0] * rightDir.X() + q[1] * up.X()),
                anchor.Y() + h * (q[0] * rightDir.Y() + q[1] * up.Y()),
                anchor.Z() + h * (q[0] * rightDir.Z() + q[1] * up.Z())
            );
            lineIdx.push_back(static_cast<int32_t>(pts.size()));
            pts.push_back(toSb(p));
        }
        lineIdx.push_back(-1);
    }
    std::vector<SbVec3f> quad;
    for (const auto& [sx, sy] : corners) {
        quad.push_back(toSb(gp_Pnt(
            anchor.X() + h * (sx * f.hw * rightDir.X() + sy * f.hh * up.X()),
            anchor.Y() + h * (sx * f.hw * rightDir.Y() + sy * f.hh * up.Y()),
            anchor.Z() + h * (sx * f.hw * rightDir.Z() + sy * f.hh * up.Z())
        )));
    }
    setPickProxy(quad);
    // a label that was moved from its anchor draws a leader from the nearest point of its frame to the anchor
    leaderCoords->point.setNum(0);
    leaderLines->coordIndex.setNum(0);
    if (f.anchor.Distance(f.origin) > 1e-6 * diag) {
        const gp_Vec d(f.anchor, f.origin);
        const double lx = std::clamp(d.Dot(gp_Vec(rightDir)), -f.hw * h, f.hw * h);
        const double ly = std::clamp(d.Dot(gp_Vec(up)), -f.hh * h, f.hh * h);
        const gp_Pnt start(
            anchor.X() + lx * rightDir.X() + ly * up.X(),
            anchor.Y() + lx * rightDir.Y() + ly * up.Y(),
            anchor.Z() + lx * rightDir.Z() + ly * up.Z()
        );
        leaderCoords->point.set1Value(0, toSb(start));
        leaderCoords->point.set1Value(1, toSb(f.origin));
        const int32_t idx[3] = {0, 1, -1};
        leaderLines->coordIndex.setValues(0, 3, idx);
    }
    if (auto* feat = dynamic_cast<Part::Feature*>(governedObject())) {
        laidOutShape = feat->Shape.getShape().getShape();  // what the label was laid out on
    }
    return true;
}

void ViewProviderPmiItem::setPickProxy(const std::vector<SbVec3f>& quad)
{
    if (quad.size() < 3) {
        proxyCoords->point.setNum(0);
        proxyFaces->coordIndex.setNum(0);
        return;
    }
    proxyCoords->point.setNum(static_cast<int>(quad.size()));
    SbVec3f* dst = proxyCoords->point.startEditing();
    std::copy(quad.begin(), quad.end(), dst);
    proxyCoords->point.finishEditing();
    std::vector<int32_t> idx;
    for (size_t i = 0; i < quad.size(); ++i) {
        idx.push_back(static_cast<int32_t>(i));
    }
    idx.push_back(-1);
    proxyFaces->coordIndex.setValues(0, static_cast<int>(idx.size()), idx.data());
}

void ViewProviderPmiItem::rebuildGeometry()
{
    // Geometry is built only for what is visible (a group hidden at open costs no drawing); stale until then
    if (!attached) {
        return;
    }
    leaderCoords->point.setNum(0);
    leaderLines->coordIndex.setNum(0);
    stale = true;
    mode = drawMode();
    updateTransform();  // the transform depends on what is drawn
    if (mode == PmiDrawMode::None) {
        if (drawing) {
            modeRoot->removeAllChildren();
            drawing = false;
        }
        coords->point.setNum(0);
        lines->coordIndex.setNum(0);
        faces->coordIndex.setNum(0);
        setPickProxy({});
        info->string.setValue("");
        stale = false;
        return;
    }
    if (!wantVisible()) {
        return;
    }
    stale = false;
    auto* item = getObject<Part::PmiItem>();
    std::vector<SbVec3f> pts;
    std::vector<int32_t> lineIdx, faceIdx;
    std::string text;
    if (mode == PmiDrawMode::Presentation) {
        const TopoDS_Shape shape = item->Presentation.getShape().getShape();
        try {
            discretize(shape, pts, lineIdx, faceIdx);
        }
        catch (const Standard_Failure& e) {
            Base::Console().warning(
                "PMI display: {}: the presentation could not be read ({})\n",
                item->getNameInDocument(),
                e.GetMessageString()
            );
        }
        // lines only: a quad over a presentation would cover the items drawn near it, and it is picked on its own lines
        setPickProxy({});
    }
    else if (!buildFallback(pts, lineIdx, text)) {
        // the object cannot give a place for the label: draw nothing for this item
        mode = PmiDrawMode::None;
        if (drawing) {
            modeRoot->removeAllChildren();
            drawing = false;
        }
        coords->point.setNum(0);
        lines->coordIndex.setNum(0);
        faces->coordIndex.setNum(0);
        setPickProxy({});
        info->string.setValue("");
        return;
    }
    info->string.setValue(text.c_str());
    coords->point.setNum(static_cast<int>(pts.size()));
    SbVec3f* dst = coords->point.startEditing();
    std::copy(pts.begin(), pts.end(), dst);
    coords->point.finishEditing();
    lines->coordIndex.setValues(0, static_cast<int>(lineIdx.size()), lineIdx.data());
    faces->coordIndex.setValues(0, static_cast<int>(faceIdx.size()), faceIdx.data());
    if (lineIdx.empty()) {
        lines->coordIndex.setNum(0);
    }
    if (faceIdx.empty()) {
        faces->coordIndex.setNum(0);
    }

    if (!drawing) {
        modeRoot->addChild(drawRoot);
        modeRoot->addChild(leaderRoot);
        drawing = true;
    }
}

void ViewProviderPmiItem::updateTransform()
{
    if (!attached) {
        return;
    }
    auto* item = getObject<Part::PmiItem>();
    Base::Placement delta;  // identity: items whose object is not found draw where they were imported
    if (auto* body = dynamic_cast<App::GeoFeature*>(governedObject())) {
        // presentation geometry is in the global coordinates of import; a fallback label is in the object's own
        delta = mode == PmiDrawMode::Fallback ? body->globalPlacement()
                                              : body->globalPlacement() * item->ReferencePlacement.getValue().inverse();
    }
    double q0, q1, q2, q3;
    delta.getRotation().getValue(q0, q1, q2, q3);
    const Base::Vector3d& t = delta.getPosition();
    transform->translation.setValue(static_cast<float>(t.x), static_cast<float>(t.y), static_cast<float>(t.z));
    transform->rotation.setValue(SbRotation(static_cast<float>(q0), static_cast<float>(q1), static_cast<float>(q2), static_cast<float>(q3)));
    leaderTransform->translation.setValue(static_cast<float>(t.x), static_cast<float>(t.y), static_cast<float>(t.z));
    leaderTransform->rotation.setValue(SbRotation(static_cast<float>(q0), static_cast<float>(q1), static_cast<float>(q2), static_cast<float>(q3)));
}

void ViewProviderPmiItem::applyVisibility()
{
    if (!attached) {
        return;
    }
    if (wantVisible()) {
        if (stale) {
            rebuildGeometry();
        }
        Gui::ViewProvider::show();
    }
    else {
        Gui::ViewProvider::hide();
    }
}

void ViewProviderPmiItem::requestRebuild()
{
    stale = true;
    if (groupOf(getObject())) {
        rebuildGeometry();
        applyVisibility();
        refreshSiblingUses();
        return;
    }
    // An item is created before its group takes it up: wait for the group (its refresh builds, or not, by its visibility);
    // an item that stays outside any group is built when the event loop next runs
    if (!rebuildPending) {
        rebuildPending = true;
        QTimer::singleShot(0, [this]() {
            if (aliveProviders().count(this)) {
                rebuildPending = false;
                refresh();
            }
        });
    }
}

void ViewProviderPmiItem::governedShapeChanged(const App::DocumentObject* obj)
{
    if (!attached || governedObject() != obj || mode != PmiDrawMode::Fallback) {
        return;
    }
    auto* feat = dynamic_cast<const Part::Feature*>(obj);
    if (feat && !laidOutShape.IsNull() && laidOutShape.IsPartner(feat->Shape.getShape().getShape())) {
        return;  // a move of the body re-sets its shape's location only: the transform follows, the label stays as laid out
    }
    stale = true;
    anchorCache.valid = false;
    rebuildGeometry();
    applyVisibility();
}

void ViewProviderPmiItem::refreshSiblingUses()
{
    // another use of the same letter on the same object may now be the one that draws (or no longer be)
    auto* me = getObject<Part::PmiDatum>();
    App::DocumentObject* group = groupOf(getObject());
    if (!me || !group) {
        return;
    }
    const std::string now = me->Letter.getStrValue();
    const std::string before = siblingLetter;
    siblingLetter = now;
    const std::set<ViewProviderPmiItem*> alive = aliveProviders();
    for (auto* vp : alive) {
        auto* other = vp == this ? nullptr : vp->getObject<Part::PmiDatum>();
        if (!other || groupOf(other) != group) {
            continue;
        }
        const std::string l = other->Letter.getStrValue();
        if (l == now || (!before.empty() && l == before)) {  // the new letter's uses and the old letter's
            vp->rebuildGeometry();
            vp->applyVisibility();
        }
    }
}

void ViewProviderPmiItem::refresh()
{
    rebuildGeometry();
    if (auto* d = getObject<Part::PmiDatum>()) {
        siblingLetter = d->Letter.getStrValue();
    }
    updateTransform();
    applyVisibility();
}

void ViewProviderPmiItem::updateData(const App::Property* prop)
{
    Gui::ViewProviderDocumentObject::updateData(prop);
    if (!attached || !prop) {
        return;
    }
    auto* item = getObject<Part::PmiItem>();
    if (!item) {
        return;
    }
    if (prop == &item->Presentation) {
        requestRebuild();
    }
    else if (prop == &item->References || prop == &item->ReferencePlacement) {
        updateTransform();
        requestRebuild();  // the governed object decides which datum use draws
    }
    else if (auto* datum = dynamic_cast<Part::PmiDatum*>(item); datum && prop == &datum->Letter) {
        requestRebuild();
    }
    else if (prop->getContainer() == item && prop->getName()) {
        // what the fallback label says, and whether it can be drawn at all
        static const std::set<std::string> labelProps {
            "PmiType", "Value", "HasBounds", "BoundsKind", "UpperBound", "LowerBound", "Modifiers", "DatumReferences",
            "ToleranceClass", "ZoneShape", "ZoneModifier", "ZoneModifierValue", "Unattached"};
        if (labelProps.count(prop->getName())) {
            requestRebuild();
        }
    }
}

void ViewProviderPmiItem::show()
{
    Gui::ViewProviderDocumentObject::show();
    applyVisibility();
}

void ViewProviderPmiItem::hide()
{
    Gui::ViewProviderDocumentObject::hide();
    applyVisibility();
}

// ------------------------------------------------------------------------------------------------------------
PROPERTY_SOURCE(PartGui::ViewProviderPmiGroup, Gui::ViewProviderDocumentObjectGroup)

ViewProviderPmiGroup::ViewProviderPmiGroup() = default;

ViewProviderPmiGroup::~ViewProviderPmiGroup() = default;

void ViewProviderPmiGroup::refreshChildren(bool full)
{
    std::vector<Gui::ViewProviderDocumentObject*> vps;
    getViewProviders(vps);
    for (auto* vp : vps) {
        if (auto* item = dynamic_cast<ViewProviderPmiItem*>(vp)) {
            if (full) {
                item->refresh();
            }
            else {
                item->applyVisibility();
            }
        }
    }
}

void ViewProviderPmiGroup::updateData(const App::Property* prop)
{
    Gui::ViewProviderDocumentObjectGroup::updateData(prop);
    auto* grp = getObject() ? getObject()->getExtensionByType<App::GroupExtension>(true) : nullptr;
    if (grp && prop == &grp->Group) {
        refreshChildren(true);  // new members, and the datum use that draws may have changed
    }
}

void ViewProviderPmiGroup::show()
{
    Gui::ViewProviderDocumentObjectGroup::show();
    refreshChildren(false);
}

void ViewProviderPmiGroup::hide()
{
    Gui::ViewProviderDocumentObjectGroup::hide();
    refreshChildren(false);
}

void ViewProviderPmiGroup::attach(App::DocumentObject* obj)
{
    Gui::ViewProviderDocumentObjectGroup::attach(obj);
    // user preference: show PMI when a file is opened (default on); off imports it hidden and draws nothing until shown
    if (!obj->getDocument()->testStatus(App::Document::Restoring)) {
        auto grp = App::GetApplication().GetParameterGroupByPath("User parameter:BaseApp/Preferences/Mod/Part/PmiDisplay");
        if (!grp->GetBool("ShowOnOpen", true)) {
            Visibility.setValue(false);
        }
    }
}

void ViewProviderPmiGroup::finishRestoring()
{
    Gui::ViewProviderDocumentObjectGroup::finishRestoring();
    refreshChildren(false);
}

PROPERTY_SOURCE(PartGui::ViewProviderPmiView, Gui::ViewProviderDocumentObject)

ViewProviderPmiView::ViewProviderPmiView() = default;

ViewProviderPmiView::~ViewProviderPmiView() = default;

bool ViewProviderPmiView::doubleClicked()
{
    // the behaviour lives in PmiAuthoringGui (one place for the command and the double click)
    auto* obj = getObject();
    if (!obj || !obj->getDocument()) {
        return false;
    }
    try {
        std::string cmd = "import FreeCAD as App\nimport PmiAuthoringGui\nPmiAuthoringGui.activateView(App.getDocument(\"" + std::string(obj->getDocument()->getName())
            + "\").getObject(\"" + std::string(obj->getNameInDocument()) + "\"))";
        Base::Interpreter().runString(cmd.c_str());
    }
    catch (const Base::Exception& e) {
        Base::Console().error("PMI view: {}\n", e.what());
        return false;
    }
    return true;
}
