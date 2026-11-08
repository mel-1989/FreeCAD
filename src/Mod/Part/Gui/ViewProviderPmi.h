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

#include <memory>
#include <set>

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

namespace App
{
class Document;
class DocumentObject;
}

namespace PartGui
{

/// Draws the Presentation of one Part::PmiItem as one SoSeparator named PMI_<object name>, under a transform that
/// follows the governed object. Datums draw one symbol per letter per governed object (the first use that has a
/// presentation); other uses draw nothing. Items without a presentation draw nothing in this slice.
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
    /// Set the transform from the governed object's current global placement
    void updateTransform();
    /// Show or hide the drawing from the item's own and its group's visibility
    void applyVisibility();
    /// Whether this item draws anything (has a presentation, and for datums is the first use of its letter on its object)
    bool isDrawn() const;

private:
    void rebuildGeometry();
    void requestRebuild();
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

}  // namespace PartGui
