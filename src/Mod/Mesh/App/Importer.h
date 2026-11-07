// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2021 Werner Mayer <wmayer[at]users.sourceforge.net>     *
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

#pragma once

#include <string>
#include <cstdint>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace App
{
class Document;
class Color;
}  // namespace App

namespace MeshCore
{
struct Material;
}
namespace Mesh
{
/// Documentation text of the Material3mf property: the reader records each name with its RGBA colour here,
/// "<prefix>name=RRGGBBAA;..." with name percent-encoded for % ; = and control characters. The 3MF writer
/// attaches a name only to a colour equal to the one recorded for it.
constexpr const char* materialsDocPrefix = "3MF basematerials names with their colours: ";
std::string encodeMaterialsDoc(const std::vector<std::string>& names, const std::vector<std::optional<uint32_t>>& colors);
std::vector<std::pair<std::string, uint32_t>> decodeMaterialsDoc(const std::string& doc);

class MeshObject;
class Feature;

class Importer
{
public:
    explicit Importer(App::Document*);
    void load(const std::string& fileName);

private:
    bool load3mf(const std::string& fileName);
    void addVertexColors(Feature*, const std::vector<Base::Color>&);
    void addFaceColors(Feature*, const std::vector<Base::Color>&);
    void addColors(Feature*, const std::string& property, const std::vector<Base::Color>&);
    Feature* createMesh(const std::string& name, MeshObject&);
    void createMeshFromSegments(const std::string& name, MeshCore::Material& mat, MeshObject& mesh);

private:
    App::Document* document;
};

}  // namespace Mesh
