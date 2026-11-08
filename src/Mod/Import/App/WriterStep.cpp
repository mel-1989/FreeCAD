// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2023 Werner Mayer <wmayer[at]users.sourceforge.net>     *
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

#include <Standard_Version.hxx>
#include <APIHeaderSection_MakeHeader.hxx>
#include <NCollection_Vector.hxx>
#include <STEPCAFControl_Writer.hxx>
#include <NCollection_Sequence.hxx>
#include <TDF_Label.hxx>
#include <XCAFDoc_DimTolTool.hxx>
#include <XCAFDoc_DocumentTool.hxx>
#include <cstdlib>
#include <utility>
#include <vector>
#include <Interface_CheckIterator.hxx>
#include <Interface_Check.hxx>
#include <Interface_CheckStatus.hxx>
#include <IFSelect_WorkSession.hxx>
#include <STEPControl_Writer.hxx>
#include <string>


#include "WriterStep.h"
#include "StepAttributes.h"
#include "StepValidation.h"
#include "StepPmi.h"
#include <Base/Exception.h>
#include <App/Application.h>
#include <Mod/Part/App/encodeFilename.h>
#include <Mod/Part/App/Interface.h>

using namespace Import;

WriterStep::WriterStep(const Base::FileInfo& file)  // NOLINT
    : file {file}
{}

void WriterStep::write(Handle(TDocStd_Document) hDoc) const  // NOLINT
{
    std::string utf8Name = file.filePath();
    std::string name8bit = Part::encodeFilename(utf8Name);

    auto configure = [](STEPCAFControl_Writer& writer) {
#if OCC_VERSION_HEX >= 0x070900
    // Temporary workaround for OCCT 7.9+, see: https://github.com/Open-Cascade-SAS/OCCT/issues/1327
    // TODO: Remove or refine the guards or remove when the issue is fixed in OCCT 8.0.1
    writer.SetShapeFixParameters(DESTEP_Parameters::GetDefaultShapeFixParameters());
    ShapeProcess::OperationsFlags aFlags;
    aFlags.set(ShapeProcess::Operation::SplitCommonVertex);
    aFlags.set(ShapeProcess::Operation::DirectFaces);
    writer.SetShapeProcessFlags(aFlags);
#endif
    };
    STEPCAFControl_Writer writer;
    configure(writer);
    Part::Interface::writeStepAssembly(Part::Interface::Assembly::On);
    Part::Interface::applyStepSchemePreference();

    // Validation properties (CAx-IF Recommended Practices, v4.2): on by default.
    Base::Reference<ParameterGrp> stepGrp = App::GetApplication()
                                                .GetUserParameter()
                                                .GetGroup("BaseApp")
                                                ->GetGroup("Preferences")
                                                ->GetGroup("Mod/Part")
                                                ->GetGroup("STEP");
    bool withProps = stepGrp->GetBool("WriteValidationProperties", true);
    writer.SetPropsMode(withProps);
    std::vector<IndependentFigures> independent;
    if (withProps) {
        attachValidationProperties(hDoc, &independent);
    }
    // PMI (G12): when the hDoc holds GD&T labels the first attempt writes them; if OCCT's writer reports an error or
    // throws, the file is written again without PMI and Import::reportStepPmiExportFallback says so. Without GD&T labels
    // there is one attempt, exactly as before.
    bool hasPmi = false;
    {
        Handle(XCAFDoc_DimTolTool) dtool = XCAFDoc_DocumentTool::DimTolTool(hDoc->Main());
        NCollection_Sequence<TDF_Label> dims, tols, datums;  // each Get...Labels call clears the sequence it is given
        dtool->GetDimensionLabels(dims);
        dtool->GetGeomToleranceLabels(tols);
        dtool->GetDatumLabels(datums);
        hasPmi = dims.Length() + tols.Length() + datums.Length() > 0;
    }
    auto failed = [](IFSelect_ReturnStatus r) {
        return r == IFSelect_RetError || r == IFSelect_RetFail || r == IFSelect_RetStop;
    };
    auto attempt = [&](bool withPmi, STEPCAFControl_Writer& writer) -> IFSelect_ReturnStatus {
        if (!withPmi && hasPmi) {
            writer.SetDimTolMode(false);
        }
        writer.Transfer(hDoc, STEPControl_AsIs);
        writeStepAttributes(writer.ChangeWriter().Model(), writer.ChangeWriter().WS(), hDoc);
        int partsWithProps = 0;
        if (withProps) {
            partsWithProps = addPartLevelValidationProperties(writer.ChangeWriter().Model(), independent);
        }

        APIHeaderSection_MakeHeader makeHeader(writer.ChangeWriter().Model());
        Base::Reference<ParameterGrp> hGrp = App::GetApplication()
                                                 .GetUserParameter()
                                                 .GetGroup("BaseApp")
                                                 ->GetGroup("Preferences")
                                                 ->GetGroup("Mod/Part")
                                                 ->GetGroup("STEP");

        // Don't set name because STEP doesn't support UTF-8
        // https://forum.freecad.org/viewtopic.php?f=8&t=52967
        makeHeader.SetAuthorValue(
            1,
            new TCollection_HAsciiString(hGrp->GetASCII("Author", "Author").c_str())
        );
        makeHeader.SetOrganizationValue(1, new TCollection_HAsciiString(hGrp->GetASCII("Company").c_str()));
        makeHeader.SetOriginatingSystem(
            new TCollection_HAsciiString(App::Application::getExecutableName().c_str())
        );
        makeHeader.SetDescriptionValue(1, new TCollection_HAsciiString("FreeCAD Model"));
        if (partsWithProps > 0) {
            // the practice's document identification string (its section 3)
            auto desc = new NCollection_HArray1<Handle(TCollection_HAsciiString)>(1, 2);
            desc->SetValue(1, new TCollection_HAsciiString("FreeCAD Model"));
            desc->SetValue(2, new TCollection_HAsciiString(validationPropertiesHeaderId()));
            makeHeader.SetDescription(desc);
        }
        if (withPmi && hasPmi) {
            const char* sw = std::getenv("FC_STEP_PMI");
            if (sw && std::string(sw) == "writefail") {
                return IFSelect_RetError;  // test hook: a PMI write that fails
            }
        }
        IFSelect_ReturnStatus st = writer.Write(name8bit.c_str());
        if (withPmi && hasPmi && failed(st)) {
            // judge OCCT's error: accepted only when it is the known one for presentation-only labels (see StepPmi.h)
            std::vector<std::pair<std::string, std::string>> fails;
            Interface_CheckIterator cl = writer.ChangeWriter().WS()->LastRunCheckList();
            for (cl.Start(); cl.More(); cl.Next()) {
                Handle(Interface_Check) ch = cl.Value();
                if (ch->NbFails() == 0) {
                    continue;
                }
                Handle(Standard_Transient) ent = ch->Entity();
                fails.emplace_back(
                    ent.IsNull() ? "" : ent->DynamicType()->Name(),
                    ch->CFail(1) ? ch->CFail(1) : ""
                );
            }
            if (acceptStepPmiWriteError(fails, name8bit)) {
                return IFSelect_RetDone;
            }
        }
        return st;
    };
    IFSelect_ReturnStatus ret = IFSelect_RetError;
    bool pmiFailed = false;
    std::string pmiWhy;
    if (hasPmi) {
        try {
            ret = attempt(true, writer);
            pmiFailed = failed(ret);
            pmiWhy = "the STEP writer reported an error";
        }
        catch (const Standard_Failure& e) {
            pmiFailed = true;
            pmiWhy = e.GetMessageString() ? e.GetMessageString() : "the STEP writer stopped";
        }
        if (pmiFailed) {
            reportStepPmiExportFallback(pmiWhy);
            STEPCAFControl_Writer again;
            configure(again);
            again.SetPropsMode(withProps);
            ret = attempt(false, again);
        }
    }
    else {
        ret = attempt(true, writer);
    }
    if (failed(ret)) {
        throw Base::FileException("Cannot open file: ", file);
    }
    if (hasPmi && !pmiFailed) {
        repairWrittenStepPmi(name8bit);
    }
}
