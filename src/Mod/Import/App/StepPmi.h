// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2026 FreeCAD fork contributors                          *
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
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU      *
 *   Lesser General Public License for more details.                       *
 *                                                                         *
 *   You should have received a copy of the GNU Lesser General Public      *
 *   License along with FreeCAD. If not, see                               *
 *   <https://www.gnu.org/licenses/>.                                      *
 *                                                                         *
 **************************************************************************/

#ifndef IMPORT_STEPPMI_H
#define IMPORT_STEPPMI_H

#include <string>
#include <utility>
#include <vector>

#include <TDocStd_Document.hxx>
#include <TopoDS_Shape.hxx>
#include <XSControl_WorkSession.hxx>

#include <Mod/Import/ImportGlobal.h>

namespace App
{
class Document;
class DocumentObject;
}  // namespace App

namespace Import
{

/// What the last STEP PMI pass found and did (see Import.lastStepPmiReport in Python).
struct ImportExport StepPmiReport
{
    bool ran {false};
    int dimensions {0};        // semantic size and location dimensions imported
    int graphicOnly {0};       // dimension labels that only carry presentation (counted, not imported)
    int tolerances {0};
    int datums {0};            // datum labels, one per datum reference use, plus datum targets
    int unmapped {0};
    int unattached {0};        // dimension and tolerance items that attach to no face of the document
    int unresolvedFaces {0};   // governed faces that no imported object carries
    int unattachedDatums {0};  // datum items that attach to no face of the document
    int edgesByGeometry {0};   // edge links found by curve type, length and centre of mass, not by shape identity
    int datumsFromStep {0};    // datum items whose faces were completed from the STEP datum feature
    int kernelLoss {0};        // items with a KernelLoss entry (the kernel lost or altered something the file states)
    std::vector<std::string> kernelLossItems;
    std::vector<std::string> unattachedItems;
    std::vector<std::string> warnings;
};

/// The pass is on unless the environment variable FC_STEP_PMI is 0 (a switch for tests and timing, not a preference;
/// FC_STEP_PMI=unknown:dimension|tolerance|modifier is the test hook of the unmapped path).
ImportExport bool stepPmiEnabled();

/// During STEPCAFControl_Reader's lifetime, after Transfer: record on a private label of hDoc the Part 21 ids
/// (#n) of the faces each dimension, tolerance and datum label refers to, and what the STEP model itself states about
/// them (stated values, qualifiers, classes of tolerance, the faces of datum features), to check the kernel's reading
/// against. path, when given, is the file: the one construct OCCT's model loses (an inline SET_REPRESENTATION_ITEM of
/// faces) is read from its text. Never throws.
ImportExport void stashStepPmiFaceIds(const Handle(XSControl_WorkSession)& ws, const Handle(TDocStd_Document)& hDoc, const char* path = nullptr);

/// After the XCAF document has been converted into document objects: create one PMI object per semantic
/// dimension, tolerance and datum in a Part::PmiGroup of doc. The governed faces are found among the
/// Part::Feature objects of doc by the identity of their TShape (the converter keeps the shapes OCCT read), so
/// this does not depend on which converter (link-group or legacy) made the objects. Never throws; clears and
/// refills the report.
ImportExport void importStepPmi(const Handle(TDocStd_Document)& hDoc, App::Document* doc);

ImportExport const StepPmiReport& lastStepPmiReport();

}  // namespace Import

#endif  // IMPORT_STEPPMI_H
