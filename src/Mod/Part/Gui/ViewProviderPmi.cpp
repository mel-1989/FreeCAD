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

// G12 slice 2a. Sources: CAx-IF Recommended Practices for the Representation and Presentation of PMI (AP242), for
// presentation geometry authored by the source file and kept in its coordinates; ISO 10303-242 for the annotation
// occurrences that carry it.

#include <algorithm>
#include <cmath>
#include <map>
#include <set>
#include <string>
#include <vector>

#include <BRepAdaptor_Curve.hxx>
#include <BRepBndLib.hxx>
#include <BRepBuilderAPI_Copy.hxx>
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
#include <Gui/Application.h>
#include <Gui/Document.h>
#include <Gui/Selection/Selection.h>
#include <Mod/Part/App/PmiFeature.h>
#include <Mod/Part/App/TopoShape.h>

#include "ViewProviderPmi.h"

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
                [this, doc](const App::DocumentObject&, const App::Property& prop) { changed(doc, prop); }
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

    void changed(App::Document* doc, const App::Property& prop)
    {
        const char* name = prop.getName();
        if (!name || std::string(name) != "Placement") {
            return;
        }
        auto it = docs.find(doc);
        if (it == docs.end()) {
            return;
        }
        const std::vector<ViewProviderPmiItem*> items(it->second.items.begin(), it->second.items.end());
        for (auto* vp : items) {
            vp->updateTransform();
        }
    }

    std::map<App::Document*, Entry> docs;
};

/// Selecting a PMI item adds the faces and edges it governs to the selection (the item stays selected), so the 3D view
/// highlights them the way it highlights any selected sub-element; deselecting the item removes what this added
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
    using Sub = std::pair<std::string, std::string>;  // object name, sub-element name

    void onSelectionChanged(const Gui::SelectionChanges& msg) override
    {
        if (busy) {
            return;
        }
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
                added.clear();
                for (auto& o : Gui::Selection().getSelection(msg.pDocName, Gui::ResolveMode::NoResolve)) {
                    if (o.pObject) {
                        add(o.DocName, o.FeatName);
                    }
                }
                break;
            }
            case T::ClrSelection:
                added.clear();
                break;
            default:
                break;
        }
    }

    static std::string key(const char* doc, const char* obj)
    {
        return std::string(doc ? doc : "") + "#" + (obj ? obj : "");
    }

    void add(const char* docName, const char* objName)
    {
        App::Document* doc = App::GetApplication().getDocument(docName);
        App::DocumentObject* o = doc && objName ? doc->getObject(objName) : nullptr;
        auto* item = o ? dynamic_cast<Part::PmiItem*>(o) : nullptr;
        if (!item || added.count(key(docName, objName))) {
            return;
        }
        std::vector<Sub> mine;
        for (App::DocumentObject* target : item->References.getValues()) {
            if (!target || !target->isAttachedToDocument()) {
                continue;
            }
            for (const auto& s : item->References.getSubValues(target)) {
                mine.emplace_back(target->getNameInDocument(), s);
            }
        }
        busy = true;
        for (const auto& [target, sub] : mine) {
            Gui::Selection().addSelection(docName, target.c_str(), sub.c_str());
        }
        busy = false;
        added[key(docName, objName)] = mine;
    }

    void remove(const char* docName, const char* objName)
    {
        auto it = added.find(key(docName, objName));
        if (it == added.end()) {
            return;
        }
        auto subs = it->second;
        added.erase(it);
        busy = true;
        for (const auto& [target, sub] : subs) {
            Gui::Selection().rmvSelection(docName, target.c_str(), sub.c_str());
        }
        busy = false;
    }

    bool busy {false};
    std::map<std::string, std::vector<Sub>> added;
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
    pick->style = SoPickStyle::UNPICKABLE;  // picking in the view is slice 2b
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

bool ViewProviderPmiItem::isDrawn() const
{
    auto* item = getObject<Part::PmiItem>();
    if (!item || item->Presentation.getShape().isNull()) {
        return false;
    }
    auto* datum = dynamic_cast<Part::PmiDatum*>(item);
    if (!datum) {
        return true;
    }
    // ruling 3 of the slice 2 brief: one symbol per letter per governed object, the first use that has a presentation
    App::DocumentObject* group = groupOf(item);
    auto* grp = group ? group->getExtensionByType<App::GroupExtension>(true) : nullptr;
    if (!grp) {
        return true;
    }
    const App::DocumentObject* body = governedObject();
    for (App::DocumentObject* o : grp->Group.getValues()) {
        auto* other = dynamic_cast<Part::PmiDatum*>(o);
        if (!other || other->Letter.getStrValue() != datum->Letter.getStrValue()
            || other->Presentation.getShape().isNull()) {
            continue;
        }
        const App::DocumentObject* ob = other->References.getValues().empty() ? nullptr : other->References.getValues().front();
        if (ob == body) {
            return other == datum;
        }
    }
    return true;
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

void ViewProviderPmiItem::rebuildGeometry()
{
    // Geometry is built only for what is visible (a group hidden at open costs no drawing); stale until then
    if (!attached) {
        return;
    }
    stale = true;
    if (!isDrawn()) {
        if (drawing) {
            modeRoot->removeAllChildren();
            drawing = false;
        }
        coords->point.setNum(0);
        lines->coordIndex.setNum(0);
        faces->coordIndex.setNum(0);
        stale = false;
        return;
    }
    if (!wantVisible()) {
        return;
    }
    stale = false;
    auto* item = getObject<Part::PmiItem>();
    const TopoDS_Shape shape = item->Presentation.getShape().getShape();
    std::vector<SbVec3f> pts;
    std::vector<int32_t> lineIdx, faceIdx;
    try {
        discretize(shape, pts, lineIdx, faceIdx);
    }
    catch (const Standard_Failure& e) {
        Base::Console().warning(
            "PMI display: %s: the presentation could not be read (%s)\n",
            item->getNameInDocument(),
            e.GetMessageString()
        );
    }
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
        delta = body->globalPlacement() * item->ReferencePlacement.getValue().inverse();
    }
    double q0, q1, q2, q3;
    delta.getRotation().getValue(q0, q1, q2, q3);
    const Base::Vector3d& t = delta.getPosition();
    transform->translation.setValue(static_cast<float>(t.x), static_cast<float>(t.y), static_cast<float>(t.z));
    transform->rotation.setValue(SbRotation(static_cast<float>(q0), static_cast<float>(q1), static_cast<float>(q2), static_cast<float>(q3)));
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

void ViewProviderPmiItem::refresh()
{
    rebuildGeometry();
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
