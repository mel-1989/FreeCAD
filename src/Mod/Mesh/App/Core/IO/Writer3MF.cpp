// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2022 Werner Mayer <wmayer[at]users.sourceforge.net>     *
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

#include <map>
#include <ostream>
#include <sstream>


#include "Core/Evaluation.h"
#include "Core/MeshKernel.h"
#include <Base/Console.h>
#include <Base/Tools.h>
#include <Base/XMLTools.h>

#include "Writer3MF.h"


using namespace MeshCore;

namespace
{
const char* const materialNamespace = "http://schemas.microsoft.com/3dmanufacturing/material/2015/02";
}

Writer3MF::Writer3MF(std::ostream& str)
    : zip(str)
{
    zip.putNextEntry("3D/3dmodel.model");
}

Writer3MF::Writer3MF(const std::string& filename)
    : zip(filename)
{
    zip.putNextEntry("3D/3dmodel.model");
}

void Writer3MF::SetForceModel(bool model)
{
    forceModel = model;
}

void Writer3MF::SetBuffered(bool on)
{
    buffered = on;
}

void Writer3MF::EnsureHeader()
{
    if (!buffered && !headerDone) {
        Initialize(zip, false);
        headerDone = true;
    }
}

void Writer3MF::Initialize(std::ostream& str, bool colors)
{
    str << "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
           "<model unit=\"millimeter\" xml:lang=\"en-US\" "
           "xmlns=\"http://schemas.microsoft.com/3dmanufacturing/core/2015/02\"";
    if (colors) {
        str << " xmlns:m=\"" << materialNamespace << "\" requiredextensions=\"m\"";
    }
    str << ">\n"
           " <metadata name=\"Application\">FreeCAD</metadata>\n";
    str << Base::blanks(1) << "<resources>\n";
}

void Writer3MF::Finish(std::ostream& str)
{
    str << Base::blanks(1) << "</resources>\n";
    str << Base::blanks(1) << "<build>\n";
    for (const auto& it : items) {
        str << Base::blanks(2) << it;
    }
    str << Base::blanks(1) << "</build>\n";
    str << "</model>\n";
}

namespace
{
std::string colorText(const Base::Color& c)
{
    // #RRGGBB, or #RRGGBBAA when not opaque
    static const char* digits = "0123456789ABCDEF";
    uint32_t v = c.getPackedValue();
    std::string s = "#";
    const int count = (v & 0xFFU) == 0xFFU ? 3 : 4;
    for (int i = 0; i < count; i++) {
        uint32_t byte = (v >> (24 - 8 * i)) & 0xFFU;
        s += digits[byte >> 4];
        s += digits[byte & 0xFU];
    }
    return s;
}
}  // namespace

bool Writer3MF::AddMesh(
    const MeshKernel& mesh,
    const Base::Matrix4D& mat,
    const std::string& name,
    const ObjectColor3MF* colors
)
{
    int groupId = 0;
    std::size_t objectPindex = 0;
    std::vector<std::pair<int, std::size_t>> triColor;  // property group and index per triangle
    if (colors && !colors->empty()) {
        std::vector<Base::Color> distinct;
        std::vector<std::size_t> triDistinct;
        if (colors->overall) {
            distinct.push_back(*colors->overall);
        }
        else if (colors->faceColors.size() == mesh.CountFacets()) {
            triDistinct.reserve(colors->faceColors.size());
            std::map<uint32_t, std::size_t> seen;
            for (const auto& c : colors->faceColors) {
                auto it = seen.find(c.getPackedValue());
                if (it == seen.end()) {
                    it = seen.emplace(c.getPackedValue(), distinct.size()).first;
                    distinct.push_back(c);
                }
                triDistinct.push_back(it->second);
            }
        }
        if (!distinct.empty()) {
            EnsureHeader();
            std::ostream& str = buffered ? static_cast<std::ostream&>(body) : zip;
            // a colour gets a name only when it equals the colour recorded for that name
            std::vector<std::string> nameOf(distinct.size());
            std::size_t used = 0;
            for (std::size_t i = 0; i < distinct.size(); i++) {
                if (colors->overall) {
                    nameOf[i] = colors->overallName;
                    used++;
                    continue;
                }
                for (const auto& nc : colors->namedColors) {
                    if (nc.second == distinct[i].getPackedValue()) {
                        nameOf[i] = nc.first;
                        used++;
                        break;
                    }
                }
            }
            if (!colors->overall && colors->namesDeclared && !warnedNames
                && (used < colors->namedColors.size() || colors->namedColors.empty())) {
                warnedNames = true;
                Base::Console().warning(
                    "3MF export: some material names are not written because no colour equals the colour recorded for them\n"
                );
            }
            std::vector<std::pair<int, std::size_t>> where(distinct.size());
            int baseGroup = 0;
            int colorGroup = 0;
            std::vector<std::size_t> baseList;
            std::vector<std::size_t> colorList;
            for (std::size_t i = 0; i < distinct.size(); i++) {
                (nameOf[i].empty() ? colorList : baseList).push_back(i);
            }
            if (!baseList.empty()) {
                baseGroup = ++objectIndex;
                str << Base::blanks(2) << "<basematerials id=\"" << baseGroup << "\">\n";
                for (std::size_t k = 0; k < baseList.size(); k++) {
                    str << Base::blanks(3) << "<base name=\"" << XMLTools::escapeXml(nameOf[baseList[k]])
                        << "\" displaycolor=\"" << colorText(distinct[baseList[k]]) << "\" />\n";
                    where[baseList[k]] = {baseGroup, k};
                }
                str << Base::blanks(2) << "</basematerials>\n";
            }
            if (!colorList.empty()) {
                usesColorGroup = true;
                colorGroup = ++objectIndex;
                str << Base::blanks(2) << "<m:colorgroup id=\"" << colorGroup << "\">\n";
                for (std::size_t k = 0; k < colorList.size(); k++) {
                    str << Base::blanks(3) << "<m:color color=\"" << colorText(distinct[colorList[k]]) << "\" />\n";
                    where[colorList[k]] = {colorGroup, k};
                }
                str << Base::blanks(2) << "</m:colorgroup>\n";
            }
            if (triDistinct.empty()) {
                groupId = where[0].first;  // one colour for the whole object
            }
            else {
                triColor.reserve(triDistinct.size());
                for (std::size_t d : triDistinct) {
                    triColor.push_back(where[d]);
                }
                if (distinct.size() == 1) {
                    groupId = triColor[0].first;
                    triColor.clear();  // one colour: pid and pindex on the object suffice
                }
                else {
                    groupId = triColor[0].first;
                }
            }
            if (triDistinct.empty() || distinct.size() == 1) {
                objectPindex = where[0].second;
            }
            else {
                objectPindex = triColor[0].second;
            }
        }
    }
    EnsureHeader();
    int id = ++objectIndex;
    SaveBuildItem(id, mat);
    return SaveObject(buffered ? static_cast<std::ostream&>(body) : zip, id, mesh, name, triColor.empty() ? nullptr : &triColor, groupId, objectPindex);
}

void Writer3MF::AddResource(const Resource3MF& res)
{
    resources.emplace_back(res);
}

bool Writer3MF::Save()
{
    if (buffered) {
        Initialize(zip, usesColorGroup);
        zip << body.str();
        headerDone = true;
    }
    EnsureHeader();
    Finish(zip);
    zip.closeEntry();

    zip.putNextEntry("_rels/.rels");
    if (!SaveRels(zip)) {
        return false;
    }
    zip.closeEntry();

    zip.putNextEntry("[Content_Types].xml");
    if (!SaveContent(zip)) {
        return false;
    }
    zip.closeEntry();
    for (const auto& it : resources) {
        zip.putNextEntry(it.fileNameInZip);
        zip.write(it.fileContent.data(), static_cast<std::streamsize>(it.fileContent.size()));
        zip.closeEntry();
    }

    return true;
}

bool Writer3MF::SaveObject(
    std::ostream& str,
    int id,
    const MeshKernel& mesh,
    const std::string& name,
    const std::vector<std::pair<int, std::size_t>>* triColor,
    int groupId,
    std::size_t objectPindex
) const
{
    // NOLINTBEGIN(readability-magic-numbers, cppcoreguidelines-avoid-magic-numbers)
    const MeshPointArray& rPoints = mesh.GetPoints();
    const MeshFacetArray& rFacets = mesh.GetFacets();

    if (!str || str.bad()) {
        return false;
    }

    str << Base::blanks(2) << "<object id=\"" << id << "\" type=\"" << GetType(mesh) << "\"";
    if (!name.empty()) {
        str << " name=\"" << XMLTools::escapeXml(name) << "\"";
    }
    if (groupId > 0) {
        str << " pid=\"" << groupId << "\" pindex=\"" << objectPindex << "\"";
    }
    str << ">\n";
    str << Base::blanks(3) << "<mesh>\n";

    // vertices
    str << Base::blanks(4) << "<vertices>\n";
    for (const auto& rPoint : rPoints) {
        str << Base::blanks(5) << "<vertex x=\"" << rPoint.x << "\" y=\"" << rPoint.y << "\" z=\""
            << rPoint.z << "\" />\n";
    }
    str << Base::blanks(4) << "</vertices>\n";

    // facet indices
    str << Base::blanks(4) << "<triangles>\n";
    std::size_t index = 0;
    for (const auto& it : rFacets) {
        str << Base::blanks(5) << "<triangle v1=\"" << it._aulPoints[0] << "\" v2=\""
            << it._aulPoints[1] << "\" v3=\"" << it._aulPoints[2] << "\"";
        if (triColor && groupId > 0) {
            const auto c = (*triColor)[index];
            // pid, p2 and p3 are written too because lib3mf ignores a triangle that has p1 alone
            str << " pid=\"" << c.first << "\" p1=\"" << c.second << "\" p2=\"" << c.second << "\" p3=\"" << c.second << "\"";
        }
        str << " />\n";
        index++;
    }
    str << Base::blanks(4) << "</triangles>\n";

    str << Base::blanks(3) << "</mesh>\n";
    str << Base::blanks(2) << "</object>\n";
    // NOLINTEND(readability-magic-numbers, cppcoreguidelines-avoid-magic-numbers)

    return true;
}

std::string Writer3MF::GetType(const MeshKernel& mesh) const
{
    bool isSolid = (forceModel || MeshEvalSolid(mesh).Evaluate());
    return isSolid ? "model" : "surface";
}

void Writer3MF::SaveBuildItem(int id, const Base::Matrix4D& mat)
{
    std::stringstream str;
    str << "<item objectid=\"" << id << "\" transform=\"" << DumpMatrix(mat) << "\" />\n";
    items.push_back(str.str());
}

std::string Writer3MF::DumpMatrix(const Base::Matrix4D& mat) const
{
    // NOLINTBEGIN(cppcoreguidelines-pro-bounds-pointer-arithmetic)
    // The matrix representation in the specs is the transposed version of Matrix4D
    // This means that for the 3x3 sub-matrix the indices must be swapped
    //
    // 3D Manufacturing Format / Core Specification & Reference Guide v1.2.3
    // Chapter: 3.3 3D Matrices (page 9)
    std::stringstream str;
    if (buffered) {
        // with colours on the placement survives a round trip; the default precision keeps the old bytes
        str.precision(17);
    }
    str << mat[0][0] << " " << mat[1][0] << " " << mat[2][0] << " " << mat[0][1] << " " << mat[1][1]
        << " " << mat[2][1] << " " << mat[0][2] << " " << mat[1][2] << " " << mat[2][2] << " "
        << mat[0][3] << " " << mat[1][3] << " " << mat[2][3];
    return str.str();
    // NOLINTEND(cppcoreguidelines-pro-bounds-pointer-arithmetic)
}

bool Writer3MF::SaveRels(std::ostream& str) const
{
    // NOLINTBEGIN(modernize-raw-string-literal)
    int ids = 1;
    str << "<?xml version='1.0' encoding='UTF-8'?>\n"
        << "<Relationships "
           "xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">\n"
        << " <Relationship Target=\"/3D/3dmodel.model\" Id=\"rel0\""
        << " Type=\"http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel\" />\n";
    for (const auto& it : resources) {
        str << " <Relationship Target=\"" << it.relationshipTarget << "\" Id=\"rel" << ++ids
            << "\" Type=\"" << it.relationshipType << "\" />\n";
    }
    str << "</Relationships>\n";
    return true;
    // NOLINTEND(modernize-raw-string-literal)
}

bool Writer3MF::SaveContent(std::ostream& str) const
{
    str << "<?xml version='1.0' encoding='UTF-8'?>\n"
        << "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">\n"
        << " <Default Extension=\"rels\" "
           "ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/>\n"
        << " <Default Extension=\"model\" "
           "ContentType=\"application/vnd.ms-package.3dmanufacturing-3dmodel+xml\"/>\n";
    for (const auto& it : resources) {
        str << " <Default Extension=\"" << it.extension << "\" ContentType=\"" << it.contentType
            << "\"/>\n";
    }
    str << "</Types>";
    return true;
}
