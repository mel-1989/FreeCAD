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
#include <BRepTools_Modifier.hxx>
#include <STEPCAFControl_Writer.hxx>
#include <TDF_Label.hxx>
#include <TDF_LabelSequence.hxx>
#include <TNaming_NamedShape.hxx>
#include <XCAFDoc_DocumentTool.hxx>
#include <XCAFDoc_ShapeTool.hxx>


#include "WriterStep.h"
#include "StepValidation.h"
#include <Base/Exception.h>
#include <App/Application.h>
#include <Mod/Part/App/encodeFilename.h>
#include <Mod/Part/App/Interface.h>
#include <Mod/Part/App/StepSphereLunes.h>

using namespace Import;

WriterStep::WriterStep(const Base::FileInfo& file)  // NOLINT
    : file {file}
{}

namespace
{

/// Faces that are a part of a sphere written as a surface of revolution come back from STEP as
/// the complementary lune, so the export document's shapes are rewritten with a spherical surface
/// there (Part::StepSphereLunes). The labels stay, so names, colours and the placed instances of
/// an assembly survive; only the geometry of the affected shapes and their sub-shape labels is
/// replaced. The document is the exporter's own, never the FreeCAD document.
void convertSphereLunes(const Handle(TDocStd_Document)& hDoc)
{
    Handle(XCAFDoc_ShapeTool) shapeTool = XCAFDoc_DocumentTool::ShapeTool(hDoc->Main());
    TDF_LabelSequence labels;
    shapeTool->GetShapes(labels);
    for (int i = 1; i <= labels.Length(); ++i) {
        const TDF_Label label = labels.Value(i);
        if (!XCAFDoc_ShapeTool::IsSimpleShape(label)) {
            continue;
        }
        const TopoDS_Shape shape = XCAFDoc_ShapeTool::GetShape(label);
        BRepTools_Modifier modifier;
        if (shape.IsNull() || !Part::StepSphereLunes::convert(shape, modifier)) {
            continue;
        }
        TDF_LabelSequence subs;
        XCAFDoc_ShapeTool::GetSubShapes(label, subs);
        label.ForgetAttribute(TNaming_NamedShape::GetID());
        shapeTool->SetShape(label, modifier.ModifiedShape(shape));
        for (int j = 1; j <= subs.Length(); ++j) {
            const TDF_Label sub = subs.Value(j);
            const TopoDS_Shape oldSub = XCAFDoc_ShapeTool::GetShape(sub);
            if (oldSub.IsNull()) {
                continue;
            }
            const TopoDS_Shape newSub = modifier.ModifiedShape(oldSub);
            if (newSub.IsNull() || newSub.IsSame(oldSub)) {
                continue;
            }
            sub.ForgetAttribute(TNaming_NamedShape::GetID());
            shapeTool->SetShape(sub, newSub);
        }
    }
}

}  // namespace

void WriterStep::write(Handle(TDocStd_Document) hDoc) const  // NOLINT
{
    std::string utf8Name = file.filePath();
    std::string name8bit = Part::encodeFilename(utf8Name);

    STEPCAFControl_Writer writer;
#if OCC_VERSION_HEX >= 0x070900
    // Temporary workaround for OCCT 7.9+, see: https://github.com/Open-Cascade-SAS/OCCT/issues/1327
    // TODO: Remove or refine the guards or remove when the issue is fixed in OCCT 8.0.1
    writer.SetShapeFixParameters(DESTEP_Parameters::GetDefaultShapeFixParameters());
    ShapeProcess::OperationsFlags aFlags;
    aFlags.set(ShapeProcess::Operation::SplitCommonVertex);
    aFlags.set(ShapeProcess::Operation::DirectFaces);
    writer.SetShapeProcessFlags(aFlags);
#endif
    Part::Interface::writeStepAssembly(Part::Interface::Assembly::On);

    // Validation properties (CAx-IF Recommended Practices, v4.2): on by default.
    Base::Reference<ParameterGrp> stepGrp = App::GetApplication()
                                                .GetUserParameter()
                                                .GetGroup("BaseApp")
                                                ->GetGroup("Preferences")
                                                ->GetGroup("Mod/Part")
                                                ->GetGroup("STEP");
    bool withProps = stepGrp->GetBool("WriteValidationProperties", true);
    writer.SetPropsMode(withProps);
    convertSphereLunes(hDoc);
    std::vector<IndependentFigures> independent;
    if (withProps) {
        attachValidationProperties(hDoc, &independent);
    }
    writer.Transfer(hDoc, STEPControl_AsIs);
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
    IFSelect_ReturnStatus ret = writer.Write(name8bit.c_str());
    if (ret == IFSelect_RetError || ret == IFSelect_RetFail || ret == IFSelect_RetStop) {
        throw Base::FileException("Cannot open file: ", file);
    }
}
