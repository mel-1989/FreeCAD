// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Part identity, user defined attributes and layers of STEP files       *
 *   (G13 slice 1c).                                                       *
 *                                                                         *
 *   Behaviour follows the CAx-IF Recommended Practices for User Defined   *
 *   Attributes v1.7 (2020-08-25), sections 5, 6.1 and 7, and the CAx-IF   *
 *   practice for model styling and organization (presentation layers).    *
 *                                                                         *
 *   An import reads the file's entities into the XCAF document (as named  *
 *   data on the label of each part), ImportOCAF2 turns that into the      *
 *   properties StepAttributes and StepLayers, and an export does the      *
 *   same in the other direction.                                          *
 ***************************************************************************/

#pragma once

#include <map>
#include <string>
#include <utility>
#include <vector>

#include <StepBasic_ProductDefinition.hxx>
#include <StepData_StepModel.hxx>
#include <TDF_Label.hxx>
#include <TDocStd_Document.hxx>
#include <XSControl_WorkSession.hxx>
#include <Mod/Import/ImportGlobal.h>

namespace App
{
class DocumentObject;
}

namespace Import
{

/// Preferences ReadStepAttributes and WriteStepAttributes (Mod/Part/STEP), default on. They cover
/// attributes, product identity and layers. Reading a preference never writes it.
bool readStepAttributesEnabled();
bool writeStepAttributesEnabled();

/// What the file says about one part (one PRODUCT_DEFINITION).
struct StepPartData
{
    Handle(StepBasic_ProductDefinition) definition;
    std::string partNumber;   // PRODUCT.id
    std::string description;  // PRODUCT.description
    std::string revision;     // PRODUCT_DEFINITION_FORMATION.id
    /// part-level attributes in file order; a repeated key keeps the first
    std::vector<std::pair<std::string, std::string>> attributes;
    std::vector<std::string> duplicateKeys;
    int unsupported {0};      // attribute definitions without exactly one usable value item
    std::vector<std::string> layers;  // layers assigned to a whole solid of the part, first appearance order
    /// the same assignments with the solid they name (the MANIFOLD_SOLID_BREP entity), one per solid and layer
    std::vector<std::pair<Handle(Standard_Transient), std::string>> solidLayers;
};

struct StepAttributeCounts
{
    std::map<std::string, int> otherLevelAttributes;  // by the entity type the attribute is attached to
    std::map<std::string, int> otherLayerTargets;     // by the entity type a layer is assigned to
};

/// The text a measure, number or flag takes in StepAttributes is documented in harness/g13/uda_expect.py
std::vector<StepPartData> collectStepPartData(const Handle(StepData_StepModel)& model,
                                              StepAttributeCounts* counts = nullptr);

/// After a STEP transfer into hDoc: store each part's data on the part's label (named data
/// "id:<key>", "uda:<key>", "layer:<ordinal>"). Never throws. Logs what it did not take.
void stashStepAttributes(const Handle(XSControl_WorkSession)& ws, const Handle(TDocStd_Document)& hDoc);

/// Create StepAttributes / StepLayers on obj from the stash on label, when there is something to hold.
/// 'includeSubShapes': layers of the solids filed under the label belong to obj too (false for a container whose
/// solids get objects of their own).
void applyStepAttributes(App::DocumentObject* obj, const TDF_Label& label, bool includeSubShapes = true);

/// Export: copy StepAttributes / StepLayers of obj onto the part label so the writer finds them.
void stashObjectStepData(const TDF_Label& label, App::DocumentObject* obj);

/// After STEPCAFControl_Writer::Transfer: write the stashed data of every part label as STEP entities.
/// Returns the number of parts that received attributes or layers.
int writeStepAttributes(const Handle(StepData_StepModel)& model,
                        const Handle(XSControl_WorkSession)& ws,
                        const Handle(TDocStd_Document)& hDoc);

}  // namespace Import
