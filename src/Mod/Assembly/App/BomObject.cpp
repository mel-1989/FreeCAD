// SPDX-License-Identifier: LGPL-2.1-or-later
/****************************************************************************
 *                                                                          *
 *   Copyright (c) 2023 Ondsel <development@ondsel.com>                     *
 *                                                                          *
 *   This file is part of FreeCAD.                                          *
 *                                                                          *
 *   FreeCAD is free software: you can redistribute it and/or modify it     *
 *   under the terms of the GNU Lesser General Public License as            *
 *   published by the Free Software Foundation, either version 2.1 of the   *
 *   License, or (at your option) any later version.                        *
 *                                                                          *
 *   FreeCAD is distributed in the hope that it will be useful, but         *
 *   WITHOUT ANY WARRANTY; without even the implied warranty of             *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU       *
 *   Lesser General Public License for more details.                        *
 *                                                                          *
 *   You should have received a copy of the GNU Lesser General Public       *
 *   License along with FreeCAD. If not, see                                *
 *   <https://www.gnu.org/licenses/>.                                       *
 *                                                                          *
 ***************************************************************************/

#include <algorithm>
#include <cmath>
#include <map>
#include <vector>

#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentObjectGroup.h>
#include <App/FeaturePythonPyImp.h>
#include <App/Link.h>
#include <App/PropertyLinks.h>
#include <App/PropertyStandard.h>
#include <App/PropertyPythonObject.h>
#include <App/Range.h>
#include <Base/Console.h>
#include <Base/Parameter.h>
#include <Base/Placement.h>
#include <Base/Rotation.h>
#include <Base/Tools.h>
#include <Base/Interpreter.h>
#include <QObject>

#include <Mod/Part/App/PartFeature.h>
#include <Mod/PartDesign/App/Body.h>
#include <Mod/Spreadsheet/App/Cell.h>

#include "AssemblyObject.h"
#include "AssemblyLink.h"
#include "BomObject.h"
#include "BomObjectPy.h"


using namespace Assembly;

// ================================ Assembly Object ============================

PROPERTY_SOURCE(Assembly::BomObject, Spreadsheet::Sheet)

BomObject::BomObject()
    : Spreadsheet::Sheet()
{
    ADD_PROPERTY_TYPE(
        columnsNames,
        ("Index"),
        "Bom",
        (App::PropertyType)(App::Prop_None),
        "List of the columns of the Bill of Materials."
    );

    ADD_PROPERTY_TYPE(
        detailSubAssemblies,
        (true),
        "Bom",
        (App::PropertyType)(App::Prop_None),
        "Detail sub-assemblies components."
    );

    ADD_PROPERTY_TYPE(
        detailParts,
        (true),
        "Bom",
        (App::PropertyType)(App::Prop_None),
        "Detail Parts sub-components."
    );

    ADD_PROPERTY_TYPE(
        onlyParts,
        (false),
        "Bom",
        (App::PropertyType)(App::Prop_None),
        "Only Part containers will be added. Solids like PartDesign Bodies will be ignored."
    );
}
BomObject::~BomObject() = default;

PyObject* BomObject::getPyObject()
{
    if (PythonObject.is(Py::_None())) {
        // ref counter is set to 1
        PythonObject = Py::Object(new BomObjectPy(this), true);
    }
    return Py::new_reference_to(PythonObject);
}

App::DocumentObjectExecReturn* BomObject::execute()
{
    generateBOM();

    return Spreadsheet::Sheet::execute();
}

short BomObject::mustExecute() const
{
    if (short res = Spreadsheet::Sheet::mustExecute()) {
        return res;
    }
    if (isRestoring() || isError()) {
        return 0;
    }
    // The BOM sits inside its assembly's group, so it cannot depend on the assembly's components
    // without a cycle. Compare what the last run listed with what the assembly lists now, so that
    // a plain recompute after components were added, removed, relabelled or swapped regenerates it.
    std::size_t current = componentSignature();
    if (!hasSignature) {
        // loaded from a file, or never run: adopt the current state instead of regenerating
        lastSignature = current;
        hasSignature = true;
        return 0;
    }
    return current != lastSignature ? 1 : 0;
}

std::vector<App::DocumentObject*> BomObject::bomRoots() const
{
    auto* assembly = getAssembly();
    if (assembly) {
        return assembly->getOutList();
    }
    return getDocument()->getRootObjectsIgnoreLinks();
}

namespace
{
inline void hashCombine(std::size_t& seed, std::size_t value)
{
    seed ^= value + 0x9e3779b97f4a7c15ULL + (seed << 6) + (seed >> 2);
}

inline void hashObject(std::size_t& seed, const App::DocumentObject* obj)
{
    hashCombine(seed, std::hash<const void*> {}(obj));
    if (obj) {
        hashCombine(seed, std::hash<std::string> {}(obj->Label.getValue()));
    }
}
}  // namespace

void BomObject::hashChildren(
    const std::vector<App::DocumentObject*>& objs,
    std::size_t& seed,
    int depth
) const
{
    if (depth > 64) {
        return;  // a cyclic assembly structure
    }
    for (auto* child : objs) {
        if (!child) {
            continue;
        }
        hashObject(seed, child);
        hashCombine(seed, isObjMirrored(child) ? 1 : 0);
        App::DocumentObject* target = child;
        if (auto* asmLink = freecad_cast<AssemblyLink*>(child)) {
            target = asmLink->getLinkedAssembly();
        }
        else if (child->isDerivedFrom<App::Link>()) {
            auto* link = static_cast<App::Link*>(child);
            hashCombine(seed, static_cast<std::size_t>(linkInstanceCount(link)));
            target = linkTarget(link);
        }
        if (!target) {
            continue;
        }
        hashObject(seed, target);
        for (auto& columnName : columnsNames.getValues()) {
            if (columnName.starts_with(".")) {
                hashCombine(
                    seed,
                    std::hash<std::string> {}(getBomPropertyValue(target, columnName.substr(1), true))
                );
            }
        }
        bool detail = (target->isDerivedFrom<AssemblyObject>() && detailSubAssemblies.getValue())
            || (!target->isDerivedFrom<AssemblyObject>() && target->isDerivedFrom<App::Part>()
                && detailParts.getValue());
        if (detail) {
            hashChildren(target->getOutList(), seed, depth + 1);
        }
    }
}

std::size_t BomObject::componentSignature() const
{
    std::size_t seed = 0;
    hashChildren(bomRoots(), seed, 0);
    return seed;
}

void BomObject::saveCustomColumnData()
{
    // This function saves data that is not automatically generated.
    dataElements.clear();
    std::tuple<App::CellAddress, App::CellAddress> res = getUsedRange();
    int maxRow = std::get<1>(res).row();
    int nameColIndex = getColumnIndex("Name");

    for (int row = 1; row <= maxRow; ++row) {

        size_t col = 0;
        // Here we do not iterate columnName : columnsNames.getValues() because they may have
        // changed
        for (size_t i = 0; i < columnsNames.getValues().size(); ++i) {
            std::string columnName = getText(0, i);
            if (columnName != "Index" && columnName != "Name" && columnName != "Quantity"
                && columnName != "File Name") {
                // Base::Console().warning("row col {} {}\n", row, col);
                //  save custom data if any.
                std::string text = getText(row, col);
                if (text != "") {
                    std::string objName = getText(row, nameColIndex);
                    BomDataElement el(objName, columnName, text);
                    dataElements.push_back(el);
                }
            }

            ++col;
        }
    }
}

void BomObject::generateBOM()
{
    saveCustomColumnData();
    // clearAll() forgets the column widths; a user (or a drawing's parts list) that set them keeps them
    std::map<int, int> widths = getColumnWidths();
    clearAll();
    for (auto& [column, width] : widths) {
        setColumnWidth(column, width);
    }
    obj_list.clear();
    obj_mirrored_list.clear();
    size_t row = 0;
    size_t col = 0;

    auto hGrp = App::GetApplication().GetParameterGroupByPath(
        "User parameter:BaseApp/Preferences/Mod/Assembly"
    );
    mirroredSuffix = hGrp->GetASCII("BomMirroredSuffix", " (mirrored)");

    // Populate headers
    for (auto& columnName : columnsNames.getValues()) {
        setCell(App::CellAddress(row, col), columnName.c_str());
        ++col;
    }
    ++row;

    addObjectChildrenToBom(bomRoots(), row, "");
    lastSignature = componentSignature();
    hasSignature = true;
}

void BomObject::addObjectChildrenToBom(
    std::vector<App::DocumentObject*> objs,
    size_t& row,
    std::string index
)
{
    int quantityColIndex = getColumnIndex("Quantity");
    bool hasQuantityCol = hasQuantityColumn();

    // Rows added at this level only. A row range from the first sibling to the current row would also
    // contain the detailed children of an earlier sub-assembly, and a sibling would be merged into them.
    std::vector<size_t> siblingRows;

    if (index != "") {
        index = index + ".";
    }

    size_t sub_i = 1;

    for (auto* child : objs) {
        if (!child) {
            continue;
        }

        bool isMirrored = isObjMirrored(child);
        int instances = 1;

        if (auto* asmLink = freecad_cast<AssemblyLink*>(child)) {
            child = asmLink->getLinkedAssembly();
            if (!child) {
                continue;
            }
        }
        else if (child->isDerivedFrom<App::Link>()) {
            instances = linkInstanceCount(static_cast<App::Link*>(child));
            child = linkTarget(static_cast<App::Link*>(child));
            if (!child || instances <= 0) {
                continue;
            }
        }

        if (!child->isDerivedFrom<AssemblyObject>() && !child->isDerivedFrom<App::Part>()
            && !(child->isDerivedFrom<Part::Feature>() && !onlyParts.getValue())) {
            continue;
        }

        if (hasQuantityCol && !siblingRows.empty()) {
            // Check if the object is not already in (case of links). And if so just increment.
            // Note: an object can be used in several parts. In which case we do no want to blindly
            // increment.
            // We also check if the Mirror state matches. Mirrored parts should not group with
            // non-mirrored parts.
            bool found = false;
            for (size_t i : siblingRows) {
                size_t idInList = i - 1;  // -1 for the header
                if (idInList < obj_list.size() && child == obj_list[idInList]
                    && idInList < obj_mirrored_list.size()
                    && isMirrored == obj_mirrored_list[idInList]) {

                    int qty = std::stoi(getText(i, quantityColIndex)) + instances;
                    setCell(App::CellAddress(i, quantityColIndex), std::to_string(qty).c_str());
                    found = true;
                    break;
                }
            }
            if (found) {
                continue;
            }
        }

        std::string sub_index = index + std::to_string(sub_i);
        ++sub_i;

        addObjectToBom(child, row, sub_index, isMirrored);
        if (hasQuantityCol && instances != 1) {
            setCell(App::CellAddress(row, quantityColIndex), std::to_string(instances).c_str());
        }
        siblingRows.push_back(row);
        ++row;

        if ((child->isDerivedFrom<AssemblyObject>() && detailSubAssemblies.getValue())
            || (!child->isDerivedFrom<AssemblyObject>() && child->isDerivedFrom<App::Part>()
                && detailParts.getValue())) {
            addObjectChildrenToBom(child->getOutList(), row, sub_index);
        }
    }
}

App::DocumentObject* BomObject::linkTarget(App::Link* link)
{
    // getLinkedObject() of a link array answers the array itself, so read what the array repeats.
    auto* countProp = dynamic_cast<App::PropertyInteger*>(link->getPropertyByName("ElementCount"));
    if (countProp && countProp->getValue() > 0) {
        if (auto* linked = dynamic_cast<App::PropertyXLink*>(link->getPropertyByName("LinkedObject"))) {
            if (auto* target = linked->getValue()) {
                return target->getLinkedObject();
            }
        }
    }
    return link->getLinkedObject();
}

int BomObject::linkInstanceCount(App::Link* link)
{
    // A link array (ElementCount > 0) stands for ElementCount instances of its target. When the
    // elements are shown, the ones suppressed by a configuration are not instances any more.
    auto* countProp = dynamic_cast<App::PropertyInteger*>(link->getPropertyByName("ElementCount"));
    int count = countProp ? countProp->getValue() : 0;
    if (count <= 0) {
        return 1;
    }
    auto* showProp = dynamic_cast<App::PropertyBool*>(link->getPropertyByName("ShowElement"));
    auto* listProp = dynamic_cast<App::PropertyLinkList*>(link->getPropertyByName("ElementList"));
    if (showProp && showProp->getValue() && listProp) {
        for (auto* element : listProp->getValues()) {
            auto* suppressed = element
                ? dynamic_cast<App::PropertyBool*>(element->getPropertyByName("Suppressed"))
                : nullptr;
            if (suppressed && suppressed->getValue()) {
                --count;
            }
        }
    }
    return std::max(count, 0);
}

bool BomObject::isObjMirrored(App::DocumentObject* obj) const
{
    // Determine mirror state based on Scale properties.
    // We multiply scales to handle nested mirroring (e.g., Mirrored LinkElement inside Mirrored
    // LinkGroup = Normal).
    double accumulatedScale = 1.0;
    if (auto element = freecad_cast<App::LinkElement*>(obj)) {
        accumulatedScale *= element->Scale.getValue();

        if (auto group = element->getLinkGroup()) {
            accumulatedScale *= group->Scale.getValue();
        }
    }
    else if (auto link = freecad_cast<App::Link*>(obj)) {
        accumulatedScale *= link->Scale.getValue();
    }
    return accumulatedScale < 0.0;
}

void BomObject::addObjectToBom(App::DocumentObject* obj, size_t row, std::string index, bool isMirrored)
{
    obj_list.push_back(obj);
    obj_mirrored_list.push_back(isMirrored);
    size_t col = 0;
    for (auto& columnName : columnsNames.getValues()) {
        if (columnName == "Index") {
            setCell(App::CellAddress(row, col), (std::string("'") + index).c_str());
        }
        else if (columnName == "Name") {
            std::string name = obj->Label.getValue();
            // Distinctly label mirrored parts so they are identifiable in the BOM
            if (isMirrored) {
                if (auto* linkedObj = obj->getLinkedObject()) {
                    // We add a suffix only if the label is the same.
                    if (name == linkedObj->Label.getValue()) {
                        name += mirroredSuffix;
                    }
                }
            }
            setCell(App::CellAddress(row, col), name.c_str());
        }
        else if (columnName == "File Name") {
            setCell(App::CellAddress(row, col), obj->getDocument()->getFileName());
        }
        else if (columnName == "Quantity") {
            setCell(App::CellAddress(row, col), std::to_string(1).c_str());
        }
        else if (columnName.starts_with(".")) {
            // Column names that start with a dot are considered property names
            // Extract the property name
            std::string baseName = columnName.substr(1);

            auto propertyValue = getBomPropertyValue(obj, baseName);
            if (!propertyValue.empty()) {
                setCell(App::CellAddress(row, col), propertyValue.c_str());
            }
        }
        else {
            // load custom data if any.
            for (auto& el : dataElements) {
                if (el.objName == obj->Label.getValue() && el.columnName == columnName) {
                    setCell(App::CellAddress(row, col), el.value.c_str());
                    break;
                }
            }
        }
        ++col;
    }
}

std::string BomObject::getBomPropertyValue(App::DocumentObject* obj, const std::string& baseName, bool quiet) const
{
    App::Property* prop = obj->getPropertyByName(baseName.c_str());

    if (!prop) {
        if (quiet) {
            return {};
        }
        Base::Console().warning("Property not found: {}\n", baseName);
        return QObject::tr("N/A").toStdString();
    }

    // Only support a subset of property types for BOM
    if (auto propStr = freecad_cast<App::PropertyString*>(prop)) {
        return propStr->getValue();
    }
    if (auto propQuantity = freecad_cast<App::PropertyQuantity*>(prop)) {
        return propQuantity->getQuantityValue().getUserString();
    }
    if (auto propEnum = freecad_cast<App::PropertyEnumeration*>(prop)) {
        return propEnum->getValueAsString();
    }
    if (auto propFloat = freecad_cast<App::PropertyFloat*>(prop)) {
        return std::to_string(propFloat->getValue());
    }
    if (auto propInt = freecad_cast<App::PropertyInteger*>(prop)) {
        return std::to_string(propInt->getValue());
    }
    if (auto propBool = freecad_cast<App::PropertyBool*>(prop)) {
        return propBool->getValue() ? "True" : "False";
    }

    if (quiet) {
        return {};
    }
    Base::Console().warning("Property type not supported for: {}\n", prop->getName());
    return QObject::tr("Not supported").toStdString();
}

AssemblyObject* BomObject::getAssembly() const
{
    for (auto& obj : getInList()) {
        if (obj->isDerivedFrom<AssemblyObject>()) {
            return static_cast<AssemblyObject*>(obj);
        }
    }
    return nullptr;
}

bool BomObject::hasQuantityColumn() const
{
    for (auto& columnName : columnsNames.getValues()) {
        if (columnName == "Quantity") {
            return true;
        }
    }
    return false;
}

std::string Assembly::BomObject::getText(size_t row, size_t col) const
{
    const Spreadsheet::Cell* cell = getCell(App::CellAddress(row, col));
    std::string cellName;
    if (cell) {
        cell->getStringContent(cellName);

        // getStringContent is adding a ' before the string for whatever reason.
        if (!cellName.empty() && cellName.front() == '\'') {
            cellName.erase(0, 1);  // Remove the first character if it's a '
        }
    }

    return cellName;
}

int BomObject::getColumnIndex(std::string name) const
{
    int col = 0;
    for (auto& columnName : columnsNames.getValues()) {
        if (columnName == name) {
            return col;
        }
        ++col;
    }
    return -1;
}
