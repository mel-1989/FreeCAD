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


#include <App/Application.h>
#include <App/Document.h>
#include <App/PropertyStandard.h>
#include <Base/FileInfo.h>
#include <Base/Parameter.h>
#include <Base/Placement.h>

#include <cstdio>

#include "Core/IO/Reader3MF.h"

#include "Importer.h"
#include "MeshFeature.h"


using namespace Mesh;


Importer::Importer(App::Document* doc)
    : document(doc)
{}

void Importer::load(const std::string& fileName)
{
    if (Base::FileInfo(fileName.c_str()).hasExtension("3mf") && load3mf(fileName)) {
        return;
    }

    MeshObject mesh;
    MeshCore::Material mat;
    std::string name;

    if (mesh.load(fileName.c_str(), &mat, &name)) {
        Feature* feature = nullptr;
        Base::FileInfo file(fileName.c_str());
        unsigned long segmct = mesh.countSegments();
        if (segmct > 1) {
            createMeshFromSegments(file.fileNamePure(), mat, mesh);
        }
        else if (
            mat.binding == MeshCore::MeshIO::PER_VERTEX
            && mat.diffuseColor.size() == mesh.countPoints()
        ) {
            feature = createMesh(file.fileNamePure(), mesh);
            addVertexColors(feature, mat.diffuseColor);
            feature->purgeTouched();
        }
        else if (
            mat.binding == MeshCore::MeshIO::PER_FACE && mat.diffuseColor.size() == mesh.countFacets()
        ) {
            feature = createMesh(file.fileNamePure(), mesh);
            addFaceColors(feature, mat.diffuseColor);
            feature->purgeTouched();
        }
        else {
            feature = createMesh(file.fileNamePure(), mesh);
            feature->purgeTouched();
        }

        if (feature && !name.empty()) {
            feature->Label.setValue(name);
            feature->purgeTouched();
        }
    }
    else {
        Base::Console().error("Failed to load {}\n", fileName);
    }
}

std::string Mesh::encodeMaterialsDoc(
    const std::vector<std::string>& names,
    const std::vector<std::optional<uint32_t>>& colors
)
{
    std::string doc = materialsDocPrefix;
    static const char* hex = "0123456789ABCDEF";
    for (std::size_t i = 0; i < names.size() && i < colors.size(); i++) {
        if (!colors[i]) {
            continue;
        }
        std::string enc;
        for (unsigned char c : names[i]) {
            if (c == '%' || c == ';' || c == '=' || c < 0x20) {
                enc += '%';
                enc += hex[c >> 4];
                enc += hex[c & 0xF];
            }
            else {
                enc += static_cast<char>(c);
            }
        }
        char buf[16];
        std::snprintf(buf, sizeof(buf), "%08X", static_cast<unsigned>(*colors[i]));
        doc += enc + "=" + buf + ";";
    }
    return doc;
}

std::vector<std::pair<std::string, uint32_t>> Mesh::decodeMaterialsDoc(const std::string& doc)
{
    std::vector<std::pair<std::string, uint32_t>> out;
    const std::string prefix = materialsDocPrefix;
    if (doc.compare(0, prefix.size(), prefix) != 0) {
        return out;
    }
    std::size_t pos = prefix.size();
    while (pos < doc.size()) {
        std::size_t end = doc.find(';', pos);
        std::size_t eq = doc.find('=', pos);
        if (end == std::string::npos || eq == std::string::npos || eq > end || end - eq != 9) {
            return {};
        }
        std::string name;
        for (std::size_t i = pos; i < eq; i++) {
            if (doc[i] == '%' && i + 2 < eq) {
                name += static_cast<char>(std::stoi(doc.substr(i + 1, 2), nullptr, 16));
                i += 2;
            }
            else {
                name += doc[i];
            }
        }
        try {
            out.emplace_back(name, static_cast<uint32_t>(std::stoul(doc.substr(eq + 1, 8), nullptr, 16)));
        }
        catch (const std::exception&) {
            return {};
        }
        pos = end + 1;
    }
    return out;
}

bool Importer::load3mf(const std::string& fileName)
{
    // Read3mfColors is only read here, never written
    bool readColors = App::GetApplication()
                          .GetParameterGroupByPath("User parameter:BaseApp/Preferences/Mod/Mesh")
                          ->GetBool("Read3mfColors", true);
    std::vector<MeshCore::Item3MF> items;
    if (!MeshCore::Load3MFItems(fileName.c_str(), readColors, items) || items.empty()) {
        return false;
    }

    Base::FileInfo file(fileName.c_str());
    const bool single = items.size() == 1;
    for (auto& item : items) {
        // one build item is baked into the vertices as always; several keep a rigid transform as Placement
        const bool asPlacement = !single && item.rigid;
        if (!asPlacement) {
            item.kernel.Transform(item.transform);
        }
        std::string label = item.name;
        if (label.empty()) {
            label = single ? file.fileNamePure() : std::to_string(item.objectId);
        }

        MeshObject mesh;
        mesh.swap(item.kernel);
        Feature* feature = createMesh(single ? file.fileNamePure() : label, mesh);
        if (asPlacement) {
            feature->Placement.setValue(Base::Placement(item.transform));
        }
        if (!item.faceColors.empty()) {
            addFaceColors(feature, item.faceColors);
        }
        if (!item.materials.empty()) {
            auto prop = static_cast<App::PropertyStringList*>(
                feature->addDynamicProperty(
                    "App::PropertyStringList",
                    "Material3mf",
                    "3MF",
                    Mesh::encodeMaterialsDoc(item.materials, item.materialColors).c_str()
                )
            );
            if (prop) {
                prop->setValues(item.materials);
            }
        }
        feature->Label.setValue(label);
        feature->purgeTouched();
    }
    return true;
}

void Importer::addVertexColors(Feature* feature, const std::vector<Base::Color>& colors)
{
    addColors(feature, "VertexColors", colors);
}

void Importer::addFaceColors(Feature* feature, const std::vector<Base::Color>& colors)
{
    addColors(feature, "FaceColors", colors);
}

void Importer::addColors(
    Feature* feature,
    const std::string& property,
    const std::vector<Base::Color>& colors
)
{
    App::PropertyColorList* prop = static_cast<App::PropertyColorList*>(
        feature->addDynamicProperty("App::PropertyColorList", property.c_str())
    );
    if (prop) {
        prop->setValues(colors);
    }
}

void Importer::createMeshFromSegments(const std::string& name, MeshCore::Material& mat, MeshObject& mesh)
{
    unsigned long segmct = mesh.countSegments();
    for (unsigned long i = 0; i < segmct; i++) {
        const Segment& group = mesh.getSegment(i);
        std::string groupName = group.getName();
        if (groupName.empty()) {
            groupName = name;
        }

        std::unique_ptr<MeshObject> segm(mesh.meshFromSegment(group.getIndices()));
        Feature* feature = createMesh(groupName, *segm);

        // if colors are set per face
        if (mat.binding == MeshCore::MeshIO::PER_FACE
            && mat.diffuseColor.size() == mesh.countFacets()) {

            std::vector<Base::Color> diffuseColor;
            diffuseColor.reserve(group.getIndices().size());
            for (const auto& it : group.getIndices()) {
                diffuseColor.push_back(mat.diffuseColor[it]);
            }

            addFaceColors(feature, diffuseColor);
        }
        feature->purgeTouched();
    }
}

Feature* Importer::createMesh(const std::string& name, MeshObject& mesh)
{
    Mesh::Feature* pcFeature = document->addObject<Mesh::Feature>(name.c_str());
    pcFeature->Label.setValue(name);
    pcFeature->Mesh.swapMesh(mesh);
    return pcFeature;
}
