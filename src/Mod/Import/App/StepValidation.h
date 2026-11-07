// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Geometric validation properties of STEP files (G13 slice 1a).         *
 *                                                                         *
 *   Behaviour follows the CAx-IF Recommended Practices for Geometric and  *
 *   Assembly Validation Properties, v4.2 (2014-10-09), sections 4.3, 4.6, *
 *   4.7 and 4.10.                                                         *
 ***************************************************************************/

#pragma once

#include <optional>
#include <string>
#include <vector>

#include <gp_Pnt.hxx>
#include <TopoDS_Shape.hxx>
#include <TDocStd_Document.hxx>
#include <StepData_StepModel.hxx>
#include <XSControl_WorkSession.hxx>
#include <Mod/Import/ImportGlobal.h>
#include <Base/FileInfo.h>

namespace Import
{

namespace StepValidationTolerance
{
// Ours; the practice gives none. Volume and area: relative difference.
// Centroid: distance as a fraction of the arrived shape's bounding-box diagonal.
constexpr double volumeRelative = 1e-3;
constexpr double areaRelative = 1e-3;
constexpr double centroidOfDiagonal = 1e-3;
}  // namespace StepValidationTolerance

/// Closed list, in order of file-level severity (worst first).
enum class StepVerdict
{
    failed,
    invalid_geometry,
    disagree,
    unusable_properties,
    no_properties,
    agree
};

const char* verdictName(StepVerdict v);

/// Return the worse of two verdicts (the file-level rule).
StepVerdict worseVerdict(StepVerdict a, StepVerdict b);

/// Pure comparison rules, separate from OCCT so they can be tested and mutated.
struct StepFigures
{
    std::optional<double> volume;     // mm^3
    std::optional<double> area;       // mm^2, faces of solids (all faces when no solid arrived)
    std::optional<double> wettedArea; // mm^2, faces of the outer shells of the solids (voids left out, section 4.6.2)
    std::optional<double> independentArea;  // mm^2, faces outside any solid (section 4.7)
    std::optional<gp_Pnt> centroid;   // mm, of the solids (of the surfaces when no solid arrived)
    std::optional<gp_Pnt> independentCentroid;  // mm, of the faces outside any solid (section 4.7)
};

struct StepPartFinding
{
    std::string name;
    StepVerdict verdict {StepVerdict::failed};
    std::string reason;
    StepFigures stated;
    StepFigures measured;
    bool statedAreaWetted {false};  // the stated area was named 'wetted area measure' (section 4.6.2)
    bool statedUnusable {false};    // some stated figure was zero, non-finite or in an unresolvable unit
    bool geometryValid {true};
    double diagonal {0.0};          // bounding-box diagonal of the arrived shape, mm
    std::optional<double> volumeDiff;    // measured - stated, mm^3
    std::optional<double> areaDiff;      // measured - stated, mm^2
    std::optional<double> centroidDist;  // |measured - stated|, mm
    bool volumeDisagrees {false};
    bool areaDisagrees {false};
    bool independentAreaDisagrees {false};
    std::optional<double> independentAreaDiff;
    bool centroidDisagrees {false};
    std::optional<double> independentCentroidDist;  // |measured - stated|, mm
    bool independentCentroidDisagrees {false};
};

struct StepFileFinding
{
    StepVerdict verdict {StepVerdict::failed};
    std::string schema;
    std::string message;
    std::vector<StepPartFinding> parts;
};

/// Decide the verdict of one part from its stated and measured figures.
void judgePart(StepPartFinding& part, bool shapeFailed);

/// Fill 'measured' (and the geometry flags) from the part's own prototype shape.
void measureShape(StepPartFinding& part, const TopoDS_Shape& shape);

/// Judge every part of a work session that has transferred a STEP file: the stated figures are
/// read from the file's own entities (not through OCCT's ReadValProps, see the run note).
void validateWorkSession(const Handle(XSControl_WorkSession)& ws, StepFileFinding& out);

/// Read a STEP file (properties on) into a scratch document and validate it.
/// Never throws: a missing or garbage file gives verdict 'failed'.
StepFileFinding validateStepFile(const Base::FileInfo& file);

/// Figures of the independent surfaces of one part (section 4.5 and 4.7), kept apart from the solids'.
struct IndependentFigures
{
    std::string name;                  // the part's product name as the writer will give it
    bool surfaceOnly {false};          // no solid: the XCAF area and centroid are the surfaces' own
    std::optional<double> area;        // mm^2
    std::optional<gp_Pnt> centroid;    // mm
};

/// Set XCAFDoc_Volume, XCAFDoc_Area and XCAFDoc_Centroid on every part prototype label so that
/// STEPCAFControl_Writer writes them. Returns the number of labels that received figures. The
/// independent surfaces of a part have no XCAF slot: when 'independent' is given they are returned
/// there for addPartLevelValidationProperties.
int attachValidationProperties(Handle(TDocStd_Document) hDoc, std::vector<IndependentFigures>* independent = nullptr);

/// The identification string the practice asks for in FILE_DESCRIPTION (its section 3).
const char* validationPropertiesHeaderId();

/// After STEPCAFControl_Writer::Transfer: add the part-level validation properties (one combined
/// representation per part, practice section 4.10) next to what OCCT wrote at geometry level.
/// Returns the number of parts that received them.
int addPartLevelValidationProperties(const Handle(StepData_StepModel)& model,
                                     const std::vector<IndependentFigures>& independent = {});

/// After a STEP transfer: log one warning per part whose verdict is disagree or invalid_geometry.
/// Never throws, never touches the document. Preference CheckValidationProperties (default on).
/// Returns the number of warnings.
int reportStepValidation(const Handle(XSControl_WorkSession)& ws);

/// One-line report-view text for a part that disagrees or is invalid.
std::string describeFinding(const StepPartFinding& part);

}  // namespace Import
