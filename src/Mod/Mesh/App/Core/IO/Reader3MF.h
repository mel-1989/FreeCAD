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


#pragma once

#include <Base/Color.h>
#include <Mod/Mesh/App/Core/MeshKernel.h>
#include <Mod/Mesh/MeshGlobal.h>
#include <iosfwd>
#include <memory>
#include <optional>
#include <string>
#include <vector>
#include <xercesc/util/XercesDefs.hpp>

namespace XERCES_CPP_NAMESPACE
{
class DOMDocument;
class DOMElement;
class DOMNode;
}  // namespace XERCES_CPP_NAMESPACE

namespace zipios
{
class FileCollection;
}

namespace MeshCore
{

/** One build item of a 3MF package after its components are flattened and its units are millimetre. */
struct MeshExport Item3MF
{
    MeshKernel kernel;          /**< vertices in millimetre, component transforms applied, item transform not */
    Base::Matrix4D transform;   /**< build item transform, translation in millimetre */
    std::string name;           /**< name of the object, may be empty */
    int objectId = -1;
    bool rigid = true;          /**< the transform is a rotation plus a translation */
    std::vector<Base::Color> faceColors;  /**< one per facet, empty when no facet has a colour */
    std::vector<std::string> materials;   /**< basematerials names in order of first use */
    bool materialsPaired = true;          /**< no colorgroup colour in the item, so names pair with colours in order */
};

/** Loads the mesh objects from data in 3MF format.
 *
 * The model part is found through the package's root relationship, the model unit is scaled to
 * millimetre and every build item becomes one entry. Sources: 3MF Core Specification and the
 * Materials and Properties Extension (3MF Consortium).
 */
class MeshExport Reader3MF
{
public:
    /*! Passes an input stream to the constructor. */
    explicit Reader3MF(std::istream& str);

    /*! Passes a file name to the constructor */
    explicit Reader3MF(const std::string& filename);
    ~Reader3MF();
    Reader3MF(const Reader3MF&) = delete;
    Reader3MF& operator=(const Reader3MF&) = delete;

    /*! Read colours and materials (default true). Switch off before Load(). */
    void SetReadColors(bool on)
    {
        readColors = on;
    }
    /*!
     * \brief Load the mesh from the input stream or file
     * \return true on success and false otherwise
     */
    bool Load();
    std::vector<int> GetMeshIds() const;
    const MeshKernel& GetMesh(int id) const
    {
        return items.at(id).kernel;
    }
    const Base::Matrix4D& GetTransform(int id) const
    {
        return items.at(id).transform;
    }
    const std::string& GetName(int id) const
    {
        return items.at(id).name;
    }
    const Item3MF& GetItem(int id) const
    {
        return items.at(id);
    }

private:
    struct Impl;
    bool TryLoad();
    void OpenStartPart();
    std::unique_ptr<std::istream> OpenPart(const std::string& path) const;

private:
    bool readColors = true;
    bool startFromRelationship = false;
    std::vector<Item3MF> items;
    std::unique_ptr<zipios::FileCollection> file;
    std::unique_ptr<std::istream> zip;
};

/** Reads a 3MF file into one entry per build item, falling back to a repaired archive. */
MeshExport bool Load3MFItems(const char* fileName, bool readColors, std::vector<Item3MF>& items);

}  // namespace MeshCore
