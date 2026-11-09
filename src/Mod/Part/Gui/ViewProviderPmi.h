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

// G12 slice 2a: 3D display of PMI items. Behaviour follows the CAx-IF Recommended Practices for the Representation
// and Presentation of PMI (AP242): the presentation geometry an item carries is drawn as it was authored, in the
// coordinates of the source file, and moves with the object the item governs.

#pragma once

#include <cstdint>
#include <memory>
#include <set>
#include <string>
#include <vector>

#include <Inventor/SbVec3f.h>
#include <Mod/Part/PartGlobal.h>
#include <gp_Dir.hxx>
#include <gp_Pnt.hxx>
#include <TopoDS_Shape.hxx>

#include <Base/Placement.h>
#include <Gui/ViewProviderDocumentObject.h>
#include <Gui/ViewProviderDocumentObjectGroup.h>
#include <Mod/Part/PartGlobal.h>

class SoSeparator;
class SoTransform;
class SoCoordinate3;
class SoIndexedLineSet;
class SoIndexedFaceSet;
class SoBaseColor;
class SoInfo;

namespace App
{
class Document;
class DocumentObject;
}

namespace PartGui
{

/// Draws one Part::PmiItem as one SoSeparator named PMI_<object name>, under a transform that follows the governed
/// object: its Presentation when it has one, otherwise (slice 2b) a fallback label laid out at the governed faces. Datums
/// draw one symbol per letter per governed object (the first use that has a presentation, else the first use); other
/// uses draw nothing. Unattached items without a presentation draw nothing.
enum class PmiDrawMode
{
    None,
    Presentation,  // the geometry the source file authored
    Fallback       // our own layout: a text label at the governed faces
};

class PartGuiExport ViewProviderPmiItem: public Gui::ViewProviderDocumentObject
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartGui::ViewProviderPmiItem);

public:
    ViewProviderPmiItem();
    ~ViewProviderPmiItem() override;

    void attach(App::DocumentObject* obj) override;
    void updateData(const App::Property* prop) override;
    std::vector<std::string> getDisplayModes() const override;
    void setDisplayMode(const char* mode) override;
    void show() override;
    void hide() override;
    void finishRestoring() override;

    /// Rebuild what is drawn and where (after the group or the governed object changed)
    void refresh();
    /// The shape of a Part feature changed: re-lay out the fallback labels placed on it
    void governedShapeChanged(const App::DocumentObject* obj);
    /// Set the transform from the governed object's current global placement
    void updateTransform();
    /// Show or hide the drawing from the item's own and its group's visibility
    void applyVisibility();
    /// What this item draws (for datums only the first use of its letter on its object draws)
    PmiDrawMode drawMode() const;
    bool isDrawn() const
    {
        return drawMode() != PmiDrawMode::None;
    }

private:
    void rebuildGeometry();
    bool fallbackEligible() const;
    struct FallbackAnchor
    {
        bool ok {false};
        gp_Pnt centre;
        gp_Dir normal {0, 0, 1};
    };
    bool computeAnchor(FallbackAnchor& out);

public:
    struct LabelFrame
    {
        gp_Pnt anchor;
        gp_Pnt origin;  // the anchor of check 21, where the label starts before it is moved
        gp_Dir right {1, 0, 0}, up {0, 0, 1}, normal {0, 1, 0};
        double h {1.0};   // digit height
        double hw {1.0}, hh {1.0};  // half extent of the pick quad, in digit heights
        std::string text;
    };

private:
    bool baseFrame(LabelFrame& f, double diag);
    bool finalFrame(LabelFrame& f, double diag);
    bool buildFallback(std::vector<SbVec3f>& pts, std::vector<int32_t>& lineIdx, std::string& text);
    void setPickProxy(const std::vector<SbVec3f>& quad);
    void requestRebuild();
    void refreshSiblingUses();
    static std::set<ViewProviderPmiItem*>& aliveProviders();
    bool wantVisible() const;
    App::DocumentObject* governedObject() const;

    SoSeparator* modeRoot {nullptr};  // child of the mode switch; holds drawRoot when the item draws
    SoSeparator* drawRoot {nullptr};  // named PMI_<name>
    SoTransform* transform {nullptr};
    SoBaseColor* color {nullptr};
    SoCoordinate3* coords {nullptr};
    SoIndexedLineSet* lines {nullptr};
    SoIndexedFaceSet* faces {nullptr};
    SoInfo* info {nullptr};              // the text of a fallback label, empty otherwise
    SoSeparator* leaderRoot {nullptr};   // Leader_<name>: the leader of a displaced label, beside the drawing so its box is unchanged
    SoTransform* leaderTransform {nullptr};
    SoCoordinate3* leaderCoords {nullptr};
    SoIndexedLineSet* leaderLines {nullptr};
    SoCoordinate3* proxyCoords {nullptr};  // invisible, pickable quad over the drawing
    SoIndexedFaceSet* proxyFaces {nullptr};
    PmiDrawMode mode {PmiDrawMode::None};
    struct
    {
        bool valid {false};
        const App::DocumentObject* feat {nullptr};
        std::string refs;
        TopoDS_Shape shape;
        FallbackAnchor value;
    } anchorCache;
    struct
    {
        bool valid {false};
        std::string text;
        double hw {0}, hh {0};
    } extentCache;
    TopoDS_Shape laidOutShape;  // the governed shape the fallback label was last laid out on
    bool warnedMissingRef {false};
    std::string siblingLetter;  // the letter at the last sibling refresh, to refresh the old letter's uses too
    bool attached {false};
    bool drawing {false};
    bool rebuildPending {false};
    bool stale {true};  // the geometry has not been built since the presentation or the draw decision changed
    App::Document* watchedDoc {nullptr};
};

/// The PMI group: hiding it hides every item in it without changing the items' own Visibility
class PartGuiExport ViewProviderPmiGroup: public Gui::ViewProviderDocumentObjectGroup
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartGui::ViewProviderPmiGroup);

public:
    ViewProviderPmiGroup();
    ~ViewProviderPmiGroup() override;

    void updateData(const App::Property* prop) override;
    void show() override;
    void hide() override;
    void finishRestoring() override;
    void attach(App::DocumentObject* obj) override;

private:
    void refreshChildren(bool full);
};

/// A saved PMI view in the tree: a double click activates it (PmiAuthoringGui.activateView sets the camera and shows exactly its items)
class PartGuiExport ViewProviderPmiView: public Gui::ViewProviderDocumentObject
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartGui::ViewProviderPmiView);

public:
    ViewProviderPmiView();
    ~ViewProviderPmiView() override;

    bool doubleClicked() override;
    std::vector<std::string> getDisplayModes() const override
    {
        return {};
    }
    bool canDragObjects() const override
    {
        return false;
    }
};

}  // namespace PartGui
