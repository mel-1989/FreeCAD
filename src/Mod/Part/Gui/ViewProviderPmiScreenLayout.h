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

// G12 slice 3b-iii: a view-dependent declutter of the fallback PMI labels. For each 3D view the labels of its document are
// projected to the screen and shifted in pixels, inside a bound around the point they govern, so that their frames do not
// cover each other; the shift is written into the labels' scene-graph nodes (see ViewProviderPmiItem::setScreenOffset),
// and a leader line joins a displaced label to the faces it governs (ASME Y14.41 leader practice). Without a 3D view
// (headless) nothing is shifted.

#pragma once

#include <Mod/Part/PartGlobal.h>

namespace App
{
class Document;
}

namespace PartGui::PmiScreenLayout
{

/// Register the 3D views of a document (a view opened after its items were drawn) and lay out their labels now
PartGuiExport void sync(const App::Document* doc);

/// The labels of a document changed (drawn, rebuilt, removed): lay out the views of the document when the event loop next runs
PartGuiExport void markDirty(const App::Document* doc);

}  // namespace PartGui::PmiScreenLayout
