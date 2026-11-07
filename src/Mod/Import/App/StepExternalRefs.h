// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   External file references of STEP assemblies (G13 slice 1c).           *
 *                                                                         *
 *   Behaviour follows the CAx-IF Recommended Practices for External       *
 *   (Element) Reference v3.1 (2014-01-20), sections 5.1 to 5.2.4: a part  *
 *   whose geometry lives in another file is a PRODUCT_DEFINITION with an  *
 *   APPLIED_DOCUMENT_REFERENCE to a DOCUMENT_FILE; the file name is the   *
 *   assigned_id of an APPLIED_EXTERNAL_IDENTIFICATION_ASSIGNMENT, or the  *
 *   DOCUMENT_FILE id when there is none (what OCCT's reader also uses).   *
 ***************************************************************************/

#pragma once

#include <string>
#include <vector>

#include <STEPCAFControl_Reader.hxx>
#include <StepData_StepModel.hxx>
#include <Base/FileInfo.h>
#include <Mod/Import/ImportGlobal.h>

namespace Import
{

struct StepExternalReport
{
    std::vector<std::string> warnings;   // one line each, already worded
    int referencedFiles {0};             // distinct files the top file refers to, nested ones included
    int cutEdges {0};                    // references to files that lead back into a cycle, not loaded
};

/// Before STEPCAFControl_Reader::Transfer: walk the reference graph of the file (reading each referenced
/// file that itself holds references), warn about missing files, references outside the importing file's
/// folder and cycles, and neutralise in 'model' the references that would send OCCT into an endless
/// recursion (a cycle of files crashes the process otherwise). Never throws.
StepExternalReport prepareStepExternalReferences(const Handle(StepData_StepModel)& model,
                                                 const Base::FileInfo& file);

/// After Transfer: add a warning for each referenced file that exists but OCCT could not read.
void finishStepExternalReferences(const STEPCAFControl_Reader& reader,
                                  const Base::FileInfo& file,
                                  StepExternalReport& report);

/// Log the warnings of a report to the console.
void reportStepExternalReferences(const StepExternalReport& report);

}  // namespace Import
