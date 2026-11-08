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

/// What the last STEP PMI export did (see Import.lastStepPmiExportReport in Python).
struct ImportExport StepPmiExportReport
{
    bool ran {false};          // the document held PMI items
    int dimensions {0};        // written
    int tolerances {0};
    int datums {0};            // datum labels written, one per datum reference use
    int annotations {0};       // graphic-only callouts written
    int skipped {0};           // items left out, each with a warning
    int presentationsStripped {0};  // presentations the writer cannot take (faces only), left out
    bool fellBack {false};     // the file was written again without PMI after a write error
    std::vector<double> writtenToleranceValues;  // in write order
    int commonDatumsRepaired {0};                // datum reference elements whose modifiers were restated
    std::vector<int> allAround;                  // ordinals (in write order) of the written tolerances that carry ALL_AROUND
    std::vector<std::string> warnings;
};

class ExportOCAF2;

/// True unless the preference User parameter:BaseApp/Preferences/Mod/Part/STEP/WritePmi is off.
ImportExport bool stepPmiWriteEnabled();

/// After ExportOCAF2::exportObjects and before WriterStep::write: rebuild the PMI items of the documents of objs as XCAF
/// dimensions, tolerances and datums on hDoc, on the labels ExportOCAF2 made for the objects they govern. Writes nothing
/// (and says why in one warning) under a schema other than AP242. Never throws; leaves hDoc without GD&T if it stops.
/// Returns the number of labels written.
ImportExport int exportStepPmi(
    const Handle(TDocStd_Document)& hDoc,
    const ExportOCAF2& ocaf,
    const std::vector<App::DocumentObject*>& objs
);

/// Called by WriterStep when the file had to be written again without PMI.
ImportExport void reportStepPmiExportFallback(const std::string& why);

/// After STEPCAFControl_Writer::Write succeeded with PMI: make the file state what OCCT's writer cannot. A tolerance that
/// applies all around is written by OCCT with a COMPOSITE_SHAPE_ASPECT; ISO 10303-242 (and the CAx-IF PMI practice,
/// "all around" and "all over" tolerance zones) states it with the subtype ALL_AROUND_SHAPE_ASPECT of the same attributes,
/// so the entity is renamed in the file text. Returns the number of tolerances repaired; warns when it cannot.
ImportExport int repairWrittenStepPmi(const std::string& path);

ImportExport const StepPmiExportReport& lastStepPmiExportReport();

}  // namespace Import

#endif  // IMPORT_STEPPMI_H
