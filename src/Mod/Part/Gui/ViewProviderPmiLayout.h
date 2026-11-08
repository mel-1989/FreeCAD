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

// G12 slice 2b: fallback layout for PMI items that carry no presentation. Symbol choice follows the tolerance symbol
// tables of ISO 1101 and ASME Y14.5 (characteristic, diameter and spherical-diameter zone, material condition and
// other modifiers, datum letter in a box); the glyphs are outlines of the Y14.5-FreeCAD font that TechDraw bundles,
// converted to line geometry. No vendor UI text or glyph art is used.

#pragma once

#include <array>
#include <string>
#include <vector>

#include <Mod/Part/PartGlobal.h>

namespace Part
{
class PmiItem;
}

namespace PartGui::PmiLayout
{

using Polyline = std::vector<std::array<double, 2>>;

struct Label
{
    std::string text;            // the label as words and Unicode symbols, compartments separated by " | "
    std::vector<Polyline> lines; // outlines, in units of the text height (digit height 1), centred on the origin
    bool fontLoaded {false};     // glyphs came from the Y14.5 font; false when the named-word fallback was used
};

/// The text of the label of an item (value in mm or degrees; tolerances as a frame; datums in a box), or empty when
/// the item type has no fallback label
PartGuiExport std::string labelText(const Part::PmiItem* item);

/// The text and the outlines of the label of an item. lines is empty when the item has no fallback label
PartGuiExport Label buildLabel(const Part::PmiItem* item);

/// Whether the Y14.5 font could be registered with Qt
PartGuiExport bool glyphFontAvailable();

}  // namespace PartGui::PmiLayout
