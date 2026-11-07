// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Geometric validation properties of STEP files (G13 slice 1a).         *
 *   See StepValidation.h for the public source of the behaviour.          *
 ***************************************************************************/

#include <algorithm>
#include <cmath>
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <sstream>

#include <BRep_Builder.hxx>
#include <BRepBndLib.hxx>
#include <BRepClass3d.hxx>
#include <TDataStd_Name.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Solid.hxx>
#include <TopoDS_Shell.hxx>
#include <TopoDS_Compound.hxx>
#include <BRepCheck_Analyzer.hxx>
#include <BRepGProp.hxx>
#include <Bnd_Box.hxx>
#include <GProp_GProps.hxx>
#include <HeaderSection_FileSchema.hxx>
#include <NCollection_HArray1.hxx>
#include <STEPControl_Reader.hxx>
#include <StepRepr_ShapeAspect.hxx>
#include <StepRepr_Representation.hxx>
#include <TDocStd_Document.hxx>
#include <XCAFDoc_Area.hxx>
#include <XCAFDoc_Centroid.hxx>
#include <XCAFDoc_DocumentTool.hxx>
#include <XCAFDoc_ShapeTool.hxx>
#include <XCAFDoc_Volume.hxx>
#include <StepData_StepModel.hxx>
#include <TCollection_HAsciiString.hxx>
#include <TDF_LabelSequence.hxx>
#include <TopAbs_ShapeEnum.hxx>
#include <TopExp_Explorer.hxx>
#include <TopLoc_Location.hxx>
#include <TopoDS_Shape.hxx>

#include <StepBasic_AreaUnit.hxx>
#include <StepBasic_VolumeUnit.hxx>
#include <StepBasic_ConversionBasedUnit.hxx>
#include <StepBasic_ConversionBasedUnitAndAreaUnit.hxx>
#include <StepBasic_ConversionBasedUnitAndLengthUnit.hxx>
#include <StepBasic_ConversionBasedUnitAndVolumeUnit.hxx>
#include <StepBasic_DerivedUnit.hxx>
#include <StepBasic_DerivedUnitElement.hxx>
#include <StepBasic_MeasureValueMember.hxx>
#include <StepBasic_MeasureWithUnit.hxx>
#include <StepBasic_NamedUnit.hxx>
#include <StepBasic_Product.hxx>
#include <StepBasic_ProductDefinition.hxx>
#include <StepBasic_ProductDefinitionFormation.hxx>
#include <StepBasic_SiPrefix.hxx>
#include <StepBasic_SiUnit.hxx>
#include <StepBasic_SiUnitAndAreaUnit.hxx>
#include <StepBasic_SiUnitAndLengthUnit.hxx>
#include <StepBasic_SiUnitAndVolumeUnit.hxx>
#include <StepBasic_SiUnitName.hxx>
#include <StepBasic_Unit.hxx>
#include <StepGeom_CartesianPoint.hxx>
#include <StepGeom_GeomRepContextAndGlobUnitAssCtxAndGlobUncertaintyAssCtx.hxx>
#include <StepGeom_GeometricRepresentationContextAndGlobalUnitAssignedContext.hxx>
#include <StepRepr_CharacterizedDefinition.hxx>
#include <StepRepr_GlobalUnitAssignedContext.hxx>
#include <StepRepr_MeasureRepresentationItem.hxx>
#include <StepRepr_NextAssemblyUsageOccurrence.hxx>
#include <StepRepr_ProductDefinitionShape.hxx>
#include <StepRepr_PropertyDefinition.hxx>
#include <StepRepr_PropertyDefinitionRepresentation.hxx>
#include <StepRepr_Representation.hxx>
#include <StepRepr_RepresentationContext.hxx>
#include <StepRepr_RepresentationItem.hxx>
#include <StepRepr_RepresentedDefinition.hxx>
#include <StepShape_ShapeDefinitionRepresentation.hxx>
#include <XSControl_TransferReader.hxx>
#include <XSControl_WorkSession.hxx>
#include <map>
#include <set>
#include <App/Application.h>
#include <Base/Console.h>
#include <Base/Exception.h>
#include <Mod/Part/App/encodeFilename.h>

#include "StepValidation.h"

namespace Import
{

namespace
{
bool finite3(const gp_Pnt& p)
{
    return std::isfinite(p.X()) && std::isfinite(p.Y()) && std::isfinite(p.Z());
}
}  // namespace

const char* verdictName(StepVerdict v)
{
    switch (v) {
        case StepVerdict::failed:
            return "failed";
        case StepVerdict::invalid_geometry:
            return "invalid_geometry";
        case StepVerdict::disagree:
            return "disagree";
        case StepVerdict::unusable_properties:
            return "unusable_properties";
        case StepVerdict::no_properties:
            return "no_properties";
        case StepVerdict::agree:
            return "agree";
    }
    return "failed";
}

StepVerdict worseVerdict(StepVerdict a, StepVerdict b)
{
    // The enum is declared worst first, so the smaller value is the worse verdict.
    return static_cast<int>(a) <= static_cast<int>(b) ? a : b;
}

std::string schemaOf(const Handle(StepData_StepModel)& sm)
{
    if (!sm.IsNull()) {
        Handle(Standard_Transient) h = sm->HeaderEntity(STANDARD_TYPE(HeaderSection_FileSchema));
        Handle(HeaderSection_FileSchema) fs = Handle(HeaderSection_FileSchema)::DownCast(h);
        if (!fs.IsNull() && fs->NbSchemaIdentifiers() > 0) {
            return fs->SchemaIdentifiersValue(1)->ToCString();
        }
    }
    return {};
}

StepFigures computeFigures(const TopoDS_Shape& shape, bool wetted = false)
{
    StepFigures f;
    BRep_Builder builder;
    TopoDS_Compound solids;
    builder.MakeCompound(solids);
    bool anySolid = false;
    for (TopExp_Explorer ex(shape, TopAbs_SOLID); ex.More(); ex.Next()) {
        builder.Add(solids, ex.Current());
        anySolid = true;
    }
    // faces that are not part of any solid are independent surfaces (practice section 4.7)
    TopoDS_Compound loose;
    builder.MakeCompound(loose);
    bool anyLoose = false;
    for (TopExp_Explorer ex(shape, TopAbs_FACE, TopAbs_SOLID); ex.More(); ex.Next()) {
        builder.Add(loose, ex.Current());
        anyLoose = true;
    }
    GProp_GProps looseProps;
    if (anyLoose) {
        BRepGProp::SurfaceProperties(loose, looseProps);
        f.independentArea = looseProps.Mass();
        if (*f.independentArea > 0.0) {
            f.independentCentroid = looseProps.CentreOfMass();
        }
    }
    if (anySolid) {
        GProp_GProps sprops;
        BRepGProp::SurfaceProperties(solids, sprops);
        f.area = sprops.Mass();
        if (wetted) {
            // wetted area (practice 4.6.2): the outer shell of each solid, voids left out; only
            // measured when the file states a wetted area, as the outer shell costs a classification
            TopoDS_Compound outer;
            builder.MakeCompound(outer);
            for (TopExp_Explorer ex(solids, TopAbs_SOLID); ex.More(); ex.Next()) {
                TopoDS_Shell sh = BRepClass3d::OuterShell(TopoDS::Solid(ex.Current()));
                builder.Add(outer, sh.IsNull() ? ex.Current() : TopoDS_Shape(sh));
            }
            GProp_GProps oprops;
            BRepGProp::SurfaceProperties(outer, oprops);
            f.wettedArea = oprops.Mass();
        }
        GProp_GProps vprops;
        BRepGProp::VolumeProperties(solids, vprops);
        f.volume = vprops.Mass();
        f.centroid = vprops.CentreOfMass();
        // A solid with no volume (all faces coplanar) has no centre of volume: use its surface.
        if (!(std::fabs(*f.volume) > 1e-12 * std::pow(std::max(*f.area, 1e-300), 1.5))) {
            f.centroid = sprops.CentreOfMass();
        }
    }
    else if (anyLoose) {
        f.area = looseProps.Mass();
        if (*f.area > 0.0) {
            f.centroid = looseProps.CentreOfMass();
        }
    }
    return f;
}

void measureShape(StepPartFinding& part, const TopoDS_Shape& shape)
{
    part.measured = StepFigures {};
    part.diagonal = 0.0;
    part.geometryValid = true;

    Bnd_Box box;
    BRepBndLib::Add(shape, box, false);
    if (!box.IsVoid()) {
        part.diagonal = std::sqrt(box.SquareExtent());
    }

    part.geometryValid = BRepCheck_Analyzer(shape).IsValid();

    part.measured = computeFigures(shape, part.statedAreaWetted);
}

void judgePart(StepPartFinding& part, bool shapeFailed)
{
    using namespace StepValidationTolerance;
    part.volumeDisagrees = part.areaDisagrees = part.centroidDisagrees = false;
    part.independentAreaDisagrees = false;
    part.independentCentroidDisagrees = false;
    part.independentCentroidDist.reset();
    part.independentAreaDiff.reset();
    part.volumeDiff.reset();
    part.areaDiff.reset();
    part.centroidDist.reset();

    if (shapeFailed) {
        part.verdict = StepVerdict::failed;
        return;
    }
    if (!part.geometryValid || (part.measured.volume && *part.measured.volume < 0.0)) {
        part.verdict = StepVerdict::invalid_geometry;
        part.reason = !part.geometryValid ? "the arrived shape fails isValid"
                                          : "the arrived shape has a negative volume";
        return;
    }

    if (part.statedUnattributable) {
        // geometry-level figures (section 4.4) that describe some solid of the part, not known which:
        // comparing them with the whole part would be a guess either way
        part.verdict = StepVerdict::unusable_properties;
        part.reason = "geometry-level figures cannot be attributed to the part's solids";
        return;
    }

    bool unusable = part.statedUnusable;
    bool anyStated = false;
    bool placeholder = false;

    if (part.stated.volume) {
        double s = *part.stated.volume;
        if (!std::isfinite(s) || s == 0.0) {
            unusable = true;
            placeholder = true;
        }
        else {
            anyStated = true;
            if (part.measured.volume) {
                part.volumeDiff = *part.measured.volume - s;
                part.volumeDisagrees = std::fabs(*part.volumeDiff) > volumeRelative * std::fabs(s);
            }
            else {
                part.volumeDisagrees = true;  // a volume is stated and no solid arrived
            }
        }
    }
    // placeholder: a stated measure that is zero or not finite makes the set unusable; if no stated
    // measure is usable, a centroid stated beside it is part of the same placeholder (not compared)
    if (part.stated.area) {
        double s = *part.stated.area;
        if (!std::isfinite(s) || s == 0.0) {
            unusable = true;
            placeholder = true;
        }
        else {
            anyStated = true;
            // a wetted area (practice 4.6.2) is compared with the outer shells' area
            std::optional<double> solidArea = part.statedAreaWetted ? part.measured.wettedArea : part.measured.area;
            if (solidArea) {
                part.areaDiff = *solidArea - s;
                // Section 4.5: exporters differ on whether the area of a part with solids also counts
                // its independent surfaces. A file that states the independent class itself
                // (4.7) is held to the solids' faces; one that does not may follow either convention.
                if (part.measured.volume && part.measured.independentArea && !part.stated.independentArea
                    && !part.stated.independentCentroid) {
                    double all = *solidArea + *part.measured.independentArea - s;
                    if (std::fabs(all) < std::fabs(*part.areaDiff)) {
                        part.areaDiff = all;
                    }
                }
                part.areaDisagrees = std::fabs(*part.areaDiff) > areaRelative * std::fabs(s);
            }
            else {
                part.areaDisagrees = true;
            }
        }
    }
    if (part.stated.independentArea) {
        double s = *part.stated.independentArea;
        if (!std::isfinite(s) || s == 0.0) {
            unusable = true;
            placeholder = true;
        }
        else {
            anyStated = true;
            double m = part.measured.independentArea.value_or(0.0);
            part.independentAreaDiff = m - s;
            part.independentAreaDisagrees = std::fabs(*part.independentAreaDiff) > areaRelative * std::fabs(s);
        }
    }
    const bool placeholderSet = placeholder && !anyStated;
    if (part.stated.independentCentroid) {
        if (!finite3(*part.stated.independentCentroid)) {
            unusable = true;
        }
        else if (!placeholderSet) {
            anyStated = true;
            if (part.measured.independentCentroid) {
                part.independentCentroidDist =
                    part.measured.independentCentroid->Distance(*part.stated.independentCentroid);
                part.independentCentroidDisagrees =
                    *part.independentCentroidDist > centroidOfDiagonal * part.diagonal;
            }
            else {
                part.independentCentroidDisagrees = true;
            }
        }
    }
    if (part.stated.centroid) {
        if (!finite3(*part.stated.centroid)) {
            unusable = true;
        }
        else if (!placeholderSet) {
            anyStated = true;
            if (part.measured.centroid) {
                part.centroidDist = part.measured.centroid->Distance(*part.stated.centroid);
                part.centroidDisagrees = *part.centroidDist > centroidOfDiagonal * part.diagonal;
            }
            else {
                part.centroidDisagrees = true;
            }
        }
    }

    if (part.volumeDisagrees || part.areaDisagrees || part.independentAreaDisagrees
        || part.centroidDisagrees || part.independentCentroidDisagrees) {
        part.verdict = StepVerdict::disagree;
    }
    else if (unusable) {
        part.verdict = StepVerdict::unusable_properties;
    }
    else if (!anyStated) {
        part.verdict = StepVerdict::no_properties;
    }
    else {
        part.verdict = StepVerdict::agree;
    }
}

namespace
{
using HAscii = Handle(TCollection_HAsciiString);

std::string lowerTrim(const HAscii& h)
{
    std::string s = h.IsNull() ? std::string() : std::string(h->ToCString());
    size_t b = s.find_first_not_of(" \t");
    size_t e = s.find_last_not_of(" \t");
    s = (b == std::string::npos) ? std::string() : s.substr(b, e - b + 1);
    for (auto& c : s) {
        c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    }
    return s;
}

double prefixFactor(const Handle(StepBasic_SiUnit)& si)
{
    if (!si->HasPrefix()) {
        return 1.0;
    }
    switch (si->Prefix()) {
        case StepBasic_spExa: return 1e18;
        case StepBasic_spPeta: return 1e15;
        case StepBasic_spTera: return 1e12;
        case StepBasic_spGiga: return 1e9;
        case StepBasic_spMega: return 1e6;
        case StepBasic_spKilo: return 1e3;
        case StepBasic_spHecto: return 1e2;
        case StepBasic_spDeca: return 1e1;
        case StepBasic_spDeci: return 1e-1;
        case StepBasic_spCenti: return 1e-2;
        case StepBasic_spMilli: return 1e-3;
        case StepBasic_spMicro: return 1e-6;
        case StepBasic_spNano: return 1e-9;
        case StepBasic_spPico: return 1e-12;
        case StepBasic_spFemto: return 1e-15;
        case StepBasic_spAtto: return 1e-18;
    }
    return 1.0;
}

/// Factor from one named unit to its SI base unit (metre for lengths), or nullopt.
std::optional<double> namedUnitFactor(const Handle(StepBasic_NamedUnit)& nu, int depth = 0)
{
    if (nu.IsNull() || depth > 4) {
        return std::nullopt;
    }
    if (Handle(StepBasic_SiUnit) si = Handle(StepBasic_SiUnit)::DownCast(nu)) {
        return prefixFactor(si);
    }
    if (Handle(StepBasic_ConversionBasedUnit) cb = Handle(StepBasic_ConversionBasedUnit)::DownCast(nu)) {
        Handle(StepBasic_MeasureWithUnit) mwu =
            Handle(StepBasic_MeasureWithUnit)::DownCast(cb->ConversionFactor());
        if (mwu.IsNull()) {
            return std::nullopt;
        }
        StepBasic_Unit u = mwu->UnitComponent();
        auto base = namedUnitFactor(u.NamedUnit(), depth + 1);
        if (!base) {
            return std::nullopt;
        }
        return mwu->ValueComponent() * *base;
    }
    return std::nullopt;
}

/// Length unit (metres per unit) of a representation's context.
std::optional<double> contextLengthFactor(const Handle(StepRepr_RepresentationContext)& ctx)
{
    // a complex entity: GEOMETRIC_REPRESENTATION_CONTEXT plus GLOBAL_UNIT_ASSIGNED_CONTEXT
    Handle(StepRepr_GlobalUnitAssignedContext) gu;
    if (Handle(StepGeom_GeometricRepresentationContextAndGlobalUnitAssignedContext) c =
            Handle(StepGeom_GeometricRepresentationContextAndGlobalUnitAssignedContext)::DownCast(ctx)) {
        gu = c->GlobalUnitAssignedContext();
    }
    else if (Handle(StepGeom_GeomRepContextAndGlobUnitAssCtxAndGlobUncertaintyAssCtx) c3 =
                 Handle(StepGeom_GeomRepContextAndGlobUnitAssCtxAndGlobUncertaintyAssCtx)::DownCast(ctx)) {
        gu = c3->GlobalUnitAssignedContext();
    }
    if (gu.IsNull()) {
        return std::nullopt;
    }
    for (int i = 1; i <= gu->NbUnits(); ++i) {
        Handle(StepBasic_NamedUnit) nu = gu->UnitsValue(i);
        if (nu.IsNull()) {
            continue;
        }
        if (nu->IsKind(STANDARD_TYPE(StepBasic_SiUnitAndLengthUnit))
            || nu->IsKind(STANDARD_TYPE(StepBasic_ConversionBasedUnitAndLengthUnit))) {
            return namedUnitFactor(nu);
        }
    }
    return std::nullopt;
}

/// Recovers the elements of the older AREA_UNIT((#e, ...)) and VOLUME_UNIT((#e, ...)) records.
/// OCCT's parser drops them, so the file text is read, once and only when such a unit is met.
/// Nothing here throws: a missing, compressed, malformed or dangling record gives nullopt.
class ElementlessUnits
{
public:
    ElementlessUnits(const Handle(StepData_StepModel)& m, const char* path)
        : model {m}
        , path {path ? path : ""}
    {}

    std::optional<std::pair<double, double>> resolve(const Handle(StepBasic_NamedUnit)& nu)
    {
        try {
            if (nu.IsNull() || model.IsNull()) {
                return std::nullopt;
            }
            int id = model->IdentLabel(nu);
            auto hit = cache.find(id);
            if (hit != cache.end()) {
                return hit->second;
            }
            auto res = resolveId(id, nu->IsKind(STANDARD_TYPE(StepBasic_AreaUnit)) ? "AREA_UNIT" : "VOLUME_UNIT");
            cache[id] = res;
            return res;
        }
        catch (...) {
            return std::nullopt;
        }
    }

private:
    Handle(StepData_StepModel) model;
    std::string path;
    bool loaded {false};
    std::string text;
    std::map<int, Handle(Standard_Transient)> byIdent;
    std::map<int, std::string> bodies;
    std::map<int, std::optional<std::pair<double, double>>> cache;

    void load()
    {
        loaded = true;
        if (path.empty()) {
            return;
        }
        std::ifstream in(path, std::ios::binary);
        if (!in) {
            return;
        }
        std::ostringstream ss;
        ss << in.rdbuf();
        text = ss.str();
        scan();
        text.clear();
        text.shrink_to_fit();
        for (int i = 1; i <= model->NbEntities(); ++i) {
            Handle(Standard_Transient) e = model->Value(i);
            byIdent[model->IdentLabel(e)] = e;
        }
    }

    /// One forward pass over the file (ISO 10303-21): comments and the contents of quoted strings
    /// ('' is an escaped quote) are skipped, so neither can carry a record or end one early, and only
    /// the statements of the DATA section are kept, as id -> text after '='. The first definition of
    /// an id wins. Only the records the unit chains need are stored.
    void scan()
    {
        const std::string& t = text;
        const size_t n = t.size();
        bool inData = false;
        std::string stmt;
        size_t i = 0;
        while (i < n) {
            const char c = t[i];
            if (c == '/' && i + 1 < n && t[i + 1] == '*') {
                size_t e = t.find("*/", i + 2);
                if (e == std::string::npos) {
                    break;
                }
                i = e + 2;
                stmt.push_back(' ');
            }
            else if (c == '\'') {
                stmt += "''";
                for (++i; i < n; ++i) {
                    if (t[i] == '\'') {
                        if (i + 1 < n && t[i + 1] == '\'') {
                            ++i;
                            continue;
                        }
                        ++i;
                        break;
                    }
                }
            }
            else if (c == ';') {
                statement(stmt, inData);
                stmt.clear();
                ++i;
            }
            else {
                stmt.push_back(c);
                ++i;
            }
        }
    }

    void statement(const std::string& stmt, bool& inData)
    {
        size_t b = 0;
        while (b < stmt.size() && std::isspace(static_cast<unsigned char>(stmt[b]))) {
            ++b;
        }
        size_t e = stmt.size();
        while (e > b && std::isspace(static_cast<unsigned char>(stmt[e - 1]))) {
            --e;
        }
        std::string s = stmt.substr(b, e - b);
        if (s.empty()) {
            return;
        }
        if (!inData) {
            std::string up = s;
            for (char& c : up) {
                c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
            }
            if (up == "DATA" || up.rfind("DATA(", 0) == 0 || up.rfind("DATA (", 0) == 0) {
                inData = true;
            }
            return;
        }
        if (s[0] != '#') {
            std::string up = s;
            for (char& c : up) {
                c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
            }
            if (up == "ENDSEC") {
                inData = false;
            }
            return;
        }
        char* end = nullptr;
        long id = std::strtol(s.c_str() + 1, &end, 10);
        if (end == s.c_str() + 1 || id <= 0) {
            return;
        }
        while (*end != '\0' && std::isspace(static_cast<unsigned char>(*end))) {
            ++end;
        }
        if (*end != '=') {
            return;
        }
        std::string body = s.substr(static_cast<size_t>(end - s.c_str()) + 1);
        std::string up = body;
        for (char& c : up) {
            c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
        }
        if (up.find("AREA_UNIT") != std::string::npos || up.find("VOLUME_UNIT") != std::string::npos
            || up.find("DERIVED_UNIT_ELEMENT") != std::string::npos) {
            bodies.emplace(static_cast<int>(id), std::move(body));
        }
    }

    /// The text after '=' of record #id (data section only), or empty.
    std::string record(int id) const
    {
        auto it = bodies.find(id);
        return it == bodies.end() ? std::string() : it->second;
    }

    /// The text inside the parentheses that follow `keyword` (matched, upper-case) in a record body.
    static std::string argsOf(const std::string& body, const std::string& keyword)
    {
        std::string up = body;
        for (char& c : up) {
            c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
        }
        size_t k = up.find(keyword);
        while (k != std::string::npos) {
            size_t after = k + keyword.size();
            bool startOk = k == 0 || !(std::isalnum(static_cast<unsigned char>(up[k - 1])) || up[k - 1] == '_');
            while (after < up.size() && std::isspace(static_cast<unsigned char>(up[after]))) {
                ++after;
            }
            if (startOk && after < up.size() && up[after] == '(') {
                int depth = 0;
                for (size_t i = after; i < up.size(); ++i) {
                    depth += up[i] == '(' ? 1 : (up[i] == ')' ? -1 : 0);
                    if (depth == 0) {
                        return body.substr(after + 1, i - after - 1);
                    }
                }
                return {};
            }
            k = up.find(keyword, k + 1);
        }
        return {};
    }

    static std::vector<int> refsIn(const std::string& s)
    {
        std::vector<int> out;
        for (size_t i = 0; i < s.size(); ++i) {
            if (s[i] == '#') {
                char* end = nullptr;
                long v = std::strtol(s.c_str() + i + 1, &end, 10);
                if (end != s.c_str() + i + 1 && v > 0) {
                    out.push_back(static_cast<int>(v));
                }
            }
        }
        return out;
    }

    std::optional<std::pair<double, double>> resolveId(int id, const char* keyword)
    {
        if (!loaded) {
            load();
        }
        if (bodies.empty() || id <= 0) {
            return std::nullopt;
        }
        std::vector<int> elems = refsIn(argsOf(record(id), keyword));
        if (elems.empty()) {
            return std::nullopt;
        }
        double f = 1.0;
        double ex = 0.0;
        for (int el : elems) {
            std::string a = argsOf(record(el), "DERIVED_UNIT_ELEMENT");
            size_t comma = a.rfind(',');
            std::vector<int> u = refsIn(a);
            if (comma == std::string::npos || u.size() != 1) {
                return std::nullopt;
            }
            auto ent = byIdent.find(u[0]);
            if (ent == byIdent.end()) {
                return std::nullopt;
            }
            Handle(StepBasic_NamedUnit) len = Handle(StepBasic_NamedUnit)::DownCast(ent->second);
            if (len.IsNull() || !(len->IsKind(STANDARD_TYPE(StepBasic_SiUnitAndLengthUnit))
                                  || len->IsKind(STANDARD_TYPE(StepBasic_ConversionBasedUnitAndLengthUnit)))) {
                return std::nullopt;
            }
            auto uf = namedUnitFactor(len);
            char* end = nullptr;
            double e = std::strtod(a.c_str() + comma + 1, &end);
            if (!uf || end == a.c_str() + comma + 1 || !std::isfinite(e)) {
                return std::nullopt;
            }
            f *= std::pow(*uf, e);
            ex += e;
        }
        if (!std::isfinite(f)) {
            return std::nullopt;
        }
        return std::make_pair(f, ex);
    }
};

/// (factor to the SI base unit, exponent of length) for the unit of a measure.
std::optional<std::pair<double, double>> measureUnit(const StepBasic_Unit& u, ElementlessUnits& elementless)
{
    if (Handle(StepBasic_DerivedUnit) du = u.DerivedUnit()) {
        double f = 1.0;
        double ex = 0.0;
        for (int i = 1; i <= du->NbElements(); ++i) {
            Handle(StepBasic_DerivedUnitElement) el = du->ElementsValue(i);
            auto uf = namedUnitFactor(el->Unit());
            if (!uf) {
                return std::nullopt;
            }
            f *= std::pow(*uf, el->Exponent());
            ex += el->Exponent();
        }
        return std::make_pair(f, ex);
    }
    if (Handle(StepBasic_NamedUnit) nu = u.NamedUnit()) {
        // The older AREA_UNIT((elements)) and VOLUME_UNIT((elements)) arrive as element-less
        // StepBasic_AreaUnit / StepBasic_VolumeUnit: OCCT's parser drops the elements. We read them
        // back from the file text; where the chain cannot be resolved the unit is unusable, and we
        // do not guess the representation context's length unit (CAx-IF practice 4.2).
        if (nu->IsKind(STANDARD_TYPE(StepBasic_AreaUnit)) || nu->IsKind(STANDARD_TYPE(StepBasic_VolumeUnit))) {
            return elementless.resolve(nu);
        }
        auto uf = namedUnitFactor(nu);
        if (!uf) {
            return std::nullopt;
        }
        double ex = 1.0;
        if (nu->IsKind(STANDARD_TYPE(StepBasic_ConversionBasedUnitAndAreaUnit))) { ex = 2.0; }
        if (nu->IsKind(STANDARD_TYPE(StepBasic_ConversionBasedUnitAndVolumeUnit))) { ex = 3.0; }
        if (nu->IsKind(STANDARD_TYPE(StepBasic_SiUnitAndAreaUnit))) { ex = 2.0; }
        if (nu->IsKind(STANDARD_TYPE(StepBasic_SiUnitAndVolumeUnit))) { ex = 3.0; }
        return std::make_pair(std::pow(*uf, ex), ex);
    }
    return std::nullopt;
}

/// Multiplier from the stated figure to mm^exponent; nullopt when the unit cannot be resolved
/// or is not a unit of that dimension.
std::optional<double> toMillimetres(const std::optional<std::pair<double, double>>& unit, double exponent)
{
    if (!unit || unit->second != exponent) {
        return std::nullopt;
    }
    return unit->first * std::pow(1000.0, exponent);
}

/// The figures stated for one part, from the practice's pattern (sections 4.3, 4.6, 4.7, 4.10).
struct Stated
{
    StepFigures figures;
    bool unusable {false};
    bool seen {false};
    bool hasMeasure {false};   // a volume or area measure was among the items (a solid or surface class)
    bool areaWetted {false};   // the area was named 'wetted area measure' (section 4.6.2)
};

/// The practice's marker may stand in the name or the description of the PROPERTY_DEFINITION
/// (some exporters swap the two).
bool isGeometricValidationProperty(const Handle(StepRepr_PropertyDefinition)& pd)
{
    static const std::string marker = "geometric validation property";
    return !pd.IsNull() && (lowerTrim(pd->Name()) == marker || lowerTrim(pd->Description()) == marker);
}

void readRepresentation(const Handle(StepRepr_Representation)& rep, Stated& st, ElementlessUnits& elementless)
{
    if (rep.IsNull()) {
        return;
    }
    std::optional<double> ctxLen = contextLengthFactor(rep->ContextOfItems());
    for (int i = 1; i <= rep->NbItems(); ++i) {
        Handle(StepRepr_RepresentationItem) it = rep->ItemsValue(i);
        std::string name = lowerTrim(it->Name());
        if (Handle(StepRepr_MeasureRepresentationItem) mri =
                Handle(StepRepr_MeasureRepresentationItem)::DownCast(it)) {
            Handle(StepBasic_MeasureWithUnit) m = mri->Measure();
            if (m.IsNull() || m->ValueComponentMember().IsNull()) {
                continue;
            }
            std::string type = m->ValueComponentMember()->Name() ? m->ValueComponentMember()->Name() : "";
            double val = m->ValueComponent();
            if (type == "VOLUME_MEASURE" && name == "volume measure") {
                st.seen = st.hasMeasure = true;
                auto k = toMillimetres(measureUnit(m->UnitComponent(), elementless), 3.0);
                if (!k) { st.unusable = true; }
                else if (!st.figures.volume) { st.figures.volume = val * *k; }
            }
            else if (type == "AREA_MEASURE" && name == "independent surface area measure") {
                st.seen = st.hasMeasure = true;
                auto k = toMillimetres(measureUnit(m->UnitComponent(), elementless), 2.0);
                if (!k) { st.unusable = true; }
                else if (!st.figures.independentArea) { st.figures.independentArea = val * *k; }
            }
            else if (type == "AREA_MEASURE"
                     && (name == "surface area measure" || name == "wetted area measure")) {
                st.seen = st.hasMeasure = true;
                auto k = toMillimetres(measureUnit(m->UnitComponent(), elementless), 2.0);
                if (!k) { st.unusable = true; }
                else if (!st.figures.area) {
                    st.figures.area = val * *k;
                    st.areaWetted = (name == "wetted area measure");
                }
            }
        }
        else if (Handle(StepGeom_CartesianPoint) cp = Handle(StepGeom_CartesianPoint)::DownCast(it)) {
            // section 4.6.3 'centre point' (solids), section 4.7.2 'surface centre point' (independent surfaces)
            if ((name == "centre point" || name == "surface centre point") && cp->NbCoordinates() == 3) {
                std::optional<gp_Pnt>& target =
                    name == "centre point" ? st.figures.centroid : st.figures.independentCentroid;
                st.seen = true;
                if (!ctxLen) {
                    st.unusable = true;
                }
                else if (!target) {
                    double k = *ctxLen * 1000.0;
                    target = gp_Pnt(cp->CoordinatesValue(1) * k, cp->CoordinatesValue(2) * k, cp->CoordinatesValue(3) * k);
                }
            }
        }
    }
}

}  // namespace

void validateWorkSession(const Handle(XSControl_WorkSession)& ws, StepFileFinding& out)
{
    Handle(StepData_StepModel) model = Handle(StepData_StepModel)::DownCast(ws->Model());
    Handle(XSControl_TransferReader) tr = ws->TransferReader();
    if (model.IsNull() || tr.IsNull()) {
        return;
    }

    ElementlessUnits elementless(model, ws->LoadedFile());

    // Pass 1: the validation properties attached at part level, by product definition.
    std::map<Standard_Transient*, Stated> stated;
    // geometry level (practice 4.4): per part, per shape aspect; used only where part level has nothing
    std::map<Standard_Transient*, std::map<Standard_Transient*, Stated>> aspectStated;
    std::vector<Handle(StepShape_ShapeDefinitionRepresentation)> sdrs;
    std::set<Standard_Transient*> assemblies;  // products with children: assembly figures are not read
    for (int i = 1; i <= model->NbEntities(); ++i) {
        Handle(Standard_Transient) ent = model->Value(i);
        if (Handle(StepRepr_NextAssemblyUsageOccurrence) nauo =
                Handle(StepRepr_NextAssemblyUsageOccurrence)::DownCast(ent)) {
            Handle(StepBasic_ProductDefinition) parent = nauo->RelatingProductDefinition();
            if (!parent.IsNull()) {
                assemblies.insert(parent.get());
            }
            continue;
        }
        if (Handle(StepRepr_PropertyDefinitionRepresentation) pdr =
                Handle(StepRepr_PropertyDefinitionRepresentation)::DownCast(ent)) {
            if (Handle(StepShape_ShapeDefinitionRepresentation)::DownCast(ent)) {
                sdrs.push_back(Handle(StepShape_ShapeDefinitionRepresentation)::DownCast(ent));
                continue;
            }
            Handle(StepRepr_PropertyDefinition) pd = pdr->Definition().PropertyDefinition();
            if (!isGeometricValidationProperty(pd)) {
                continue;
            }
            Handle(StepRepr_ProductDefinitionShape) pds = pd->Definition().ProductDefinitionShape();
            if (pds.IsNull()) {
                Handle(StepRepr_ShapeAspect) sa = pd->Definition().ShapeAspect();
                if (!sa.IsNull() && !sa->OfShape().IsNull() && !sa->OfShape()->Definition().ProductDefinition().IsNull()) {
                    readRepresentation(pdr->UsedRepresentation(),
                                       aspectStated[sa->OfShape()->Definition().ProductDefinition().get()][sa.get()],
                                       elementless);
                }
                continue;
            }
            Handle(StepBasic_ProductDefinition) prod = pds->Definition().ProductDefinition();
            if (prod.IsNull()) {
                continue;  // assembly usage, not a part
            }
            readRepresentation(pdr->UsedRepresentation(), stated[prod.get()], elementless);
        }
    }
    // Geometry level fills in for a part that states nothing at part level (aspects holding only
    // curve lengths are not its solids). The figures of a shape aspect describe what the aspect
    // refers to (sections 4.4.2, 4.4.3), not the whole part: a single aspect is applied to the part
    // only when the part turns out to have exactly one solid and no independent surfaces (checked
    // once the shape is measured, below); several aspects, or one that cannot be tied to the
    // part's geometry, make the part unusable and never "no properties".
    std::set<Standard_Transient*> fromAspect;  // parts that took the figures of their one shape aspect
    for (const auto& [prod, aspects] : aspectStated) {
        auto have = stated.find(prod);
        if (have != stated.end() && have->second.seen) {
            continue;
        }
        const Stated* only = nullptr;
        int n = 0;
        for (const auto& [sa, st] : aspects) {
            if (st.hasMeasure) {
                only = &st;
                ++n;
            }
        }
        if (n == 1) {
            stated[prod] = *only;
            fromAspect.insert(prod);
        }
        else if (n > 1) {
            Stated unattributed;
            unattributed.seen = unattributed.unusable = true;
            stated[prod] = unattributed;
        }
    }
    // Pass 2: the parts, each once, with the shape that arrived in the part's own frame.
    std::set<Standard_Transient*> done;
    for (const auto& sdr : sdrs) {
        Handle(StepRepr_ProductDefinitionShape) pds =
            Handle(StepRepr_ProductDefinitionShape)::DownCast(sdr->Definition().PropertyDefinition());
        Handle(StepRepr_Representation) rep = sdr->UsedRepresentation();
        if (pds.IsNull() || rep.IsNull()) {
            continue;
        }
        Handle(StepBasic_ProductDefinition) prod = pds->Definition().ProductDefinition();
        if (prod.IsNull() || assemblies.count(prod.get()) || done.count(prod.get())) {
            continue;
        }
        // OCCT binds the transferred shape of a part to its PRODUCT_DEFINITION, in the part's own frame
        TopoDS_Shape shape = tr->ShapeResult(prod);
        if (shape.IsNull()) {
            // only the root of an assembly keeps its binder: transfer this part by itself, which
            // gives its prototype shape in its own frame, without the placement of any instance
            try {
                tr->TransferOne(prod);
                shape = tr->ShapeResult(prod);
            }
            catch (Standard_Failure&) {
                shape.Nullify();
            }
        }
        if (shape.IsNull()) {
            shape = tr->ShapeResult(sdr);
        }
        if (shape.IsNull()) {
            shape = tr->ShapeResult(rep);
        }
        auto st = stated.find(prod.get());
        if (shape.IsNull() && (st == stated.end() || !st->second.seen)) {
            continue;  // a product without geometry and without figures is not a part
        }
        done.insert(prod.get());

        StepPartFinding part;
        if (!prod->Formation().IsNull() && !prod->Formation()->OfProduct().IsNull()) {
            Handle(StepBasic_Product) product = prod->Formation()->OfProduct();
            part.name = lowerTrim(product->Name()).empty() ? std::string(product->Id()->ToCString())
                                                           : std::string(product->Name()->ToCString());
        }
        auto it = stated.find(prod.get());
        if (it != stated.end()) {
            part.stated = it->second.figures;
            part.statedUnusable = it->second.unusable;
            part.statedAreaWetted = it->second.areaWetted;
        }
        bool failed = shape.IsNull();
        if (failed) {
            part.reason = "no shape arrived for this part";
        }
        else {
            try {
                measureShape(part, shape);  // the transferred prototype carries no instance location
            }
            catch (Standard_Failure& e) {
                failed = true;
                part.reason = e.GetMessageString() ? e.GetMessageString() : "exception";
            }
        }
        if (!failed && fromAspect.count(prod.get())) {
            int solids = 0;
            for (TopExp_Explorer ex(shape, TopAbs_SOLID); ex.More(); ex.Next()) {
                ++solids;
            }
            if (solids != 1 || part.measured.independentArea) {
                part.statedUnattributable = true;
            }
        }
        judgePart(part, failed);
        out.verdict = out.parts.empty() ? part.verdict : worseVerdict(out.verdict, part.verdict);
        out.parts.push_back(std::move(part));
    }
}

StepFileFinding validateStepFile(const Base::FileInfo& file)
{
    StepFileFinding out;
    out.verdict = StepVerdict::failed;
    try {
        if (!file.exists()) {
            out.message = "file not found";
            return out;
        }
        std::string name8bit = Part::encodeFilename(file.filePath());
        STEPControl_Reader reader;
#if OCC_VERSION_HEX < 0x070800
        if (reader.ReadFile(name8bit.c_str()) != IFSelect_RetDone) {
#else
        Handle(StepData_StepModel) model = new StepData_StepModel;
        model->InternalParameters.InitFromStatic();
        model->SetSourceCodePage(Resource_FormatType_UTF8);
        if (reader.ReadFile(name8bit.c_str(), model->InternalParameters) != IFSelect_RetDone) {
#endif
            out.message = "cannot read STEP file";
            return out;
        }
        out.schema = schemaOf(reader.StepModel());
        reader.TransferRoots();
        out.verdict = StepVerdict::no_properties;
        validateWorkSession(reader.WS(), out);
        if (out.parts.empty()) {
            out.verdict = StepVerdict::failed;
            out.message = "no part arrived";
        }
    }
    catch (Standard_Failure& e) {
        out.verdict = StepVerdict::failed;
        out.message = e.GetMessageString() ? e.GetMessageString() : "exception";
    }
    catch (const std::exception& e) {
        out.verdict = StepVerdict::failed;
        out.message = e.what();
    }
    catch (...) {
        out.verdict = StepVerdict::failed;
        out.message = "unknown error";
    }
    return out;
}

std::string describeFinding(const StepPartFinding& part)
{
    std::ostringstream os;
    os.precision(10);
    os << "STEP validation: part '" << part.name << "' " << verdictName(part.verdict);
    if (part.verdict == StepVerdict::invalid_geometry) {
        os << ": " << part.reason;
        if (part.measured.volume) {
            os << " (volume " << *part.measured.volume << " mm^3)";
        }
        return os.str();
    }
    auto rel = [&](const char* what, const std::optional<double>& s, const std::optional<double>& m) {
        if (s && m) {
            os << "; " << what << " stated " << *s << ", measured " << *m << ", difference "
               << (*m - *s) << " (" << (*s != 0.0 ? 100.0 * (*m - *s) / *s : 0.0) << "%)";
        }
    };
    if (part.volumeDisagrees) {
        if (part.measured.volume) {
            rel("volume", part.stated.volume, part.measured.volume);
        }
        else {
            os << "; volume stated " << *part.stated.volume << " mm^3, no solid arrived";
        }
    }
    if (part.areaDisagrees) {
        rel(part.statedAreaWetted ? "wetted area" : "area", part.stated.area,
            part.statedAreaWetted ? part.measured.wettedArea : part.measured.area);
    }
    if (part.independentAreaDisagrees) {
        rel("independent surface area", part.stated.independentArea,
            std::optional<double>(part.measured.independentArea.value_or(0.0)));
    }
    if (part.independentCentroidDisagrees && part.stated.independentCentroid) {
        os << "; independent surface centroid stated (" << part.stated.independentCentroid->X() << ", "
           << part.stated.independentCentroid->Y() << ", " << part.stated.independentCentroid->Z() << ")";
        if (part.measured.independentCentroid) {
            os << ", distance " << *part.independentCentroidDist << " mm of diagonal " << part.diagonal << " mm";
        }
    }
    if (part.centroidDisagrees && part.stated.centroid) {
        os << "; centroid stated (" << part.stated.centroid->X() << ", " << part.stated.centroid->Y()
           << ", " << part.stated.centroid->Z() << ")";
        if (part.measured.centroid) {
            os << ", measured (" << part.measured.centroid->X() << ", " << part.measured.centroid->Y()
               << ", " << part.measured.centroid->Z() << "), distance "
               << *part.centroidDist << " mm of diagonal " << part.diagonal << " mm";
        }
    }
    return os.str();
}

}  // namespace Import

namespace Import
{

int attachValidationProperties(Handle(TDocStd_Document) hDoc, std::vector<IndependentFigures>* independent)
{
    Handle(XCAFDoc_ShapeTool) shapeTool = XCAFDoc_DocumentTool::ShapeTool(hDoc->Main());
    TDF_LabelSequence labels;
    shapeTool->GetShapes(labels);
    int count = 0;
    for (Standard_Integer i = 1; i <= labels.Length(); ++i) {
        TDF_Label label = labels.Value(i);
        // one set of figures per part prototype, in the part's own frame
        if (shapeTool->IsReference(label) || shapeTool->IsSubShape(label)
            || shapeTool->IsAssembly(label)) {
            continue;
        }
        TopoDS_Shape shape = shapeTool->GetShape(label);
        if (shape.IsNull()) {
            continue;
        }
        StepFigures f;
        try {
            f = computeFigures(shape.Located(TopLoc_Location()));
        }
        catch (Standard_Failure&) {
            continue;
        }
        bool any = false;
        if (f.volume && std::isfinite(*f.volume) && *f.volume > 0.0) {
            XCAFDoc_Volume::Set(label, *f.volume);
            any = true;
        }
        if (f.area && std::isfinite(*f.area) && *f.area > 0.0) {
            XCAFDoc_Area::Set(label, *f.area);
            any = true;
        }
        if (f.centroid && finite3(*f.centroid)) {
            XCAFDoc_Centroid::Set(label, *f.centroid);
            any = true;
        }
        count += any ? 1 : 0;
        // the independent surfaces are a class of their own (sections 4.5, 4.7): no XCAF slot holds
        // them beside the solids', so hand them on by the part's name
        if (independent && f.independentArea && std::isfinite(*f.independentArea) && *f.independentArea > 0.0) {
            Handle(TDataStd_Name) nameAttr;
            if (label.FindAttribute(TDataStd_Name::GetID(), nameAttr)) {
                IndependentFigures ind;
                ind.name = TCollection_AsciiString(nameAttr->Get()).ToCString();  // UTF-8, as the product name is stored
                ind.surfaceOnly = !f.volume;
                ind.area = f.independentArea;
                ind.centroid = f.independentCentroid;
                independent->push_back(ind);
            }
        }
    }
    return count;
}

}  // namespace Import

namespace Import
{

const char* validationPropertiesHeaderId()
{
    return "CAx-IF Rec.Pracs.---Geometric and Assembly Validation Properties---4.2---2014-10-09";
}

int addPartLevelValidationProperties(const Handle(StepData_StepModel)& model,
                                     const std::vector<IndependentFigures>& independent)
{
    // independent figures by product name; a name two parts share with different figures is left out
    std::map<std::string, const IndependentFigures*> byName;
    std::set<std::string> ambiguous;
    for (const auto& ind : independent) {
        auto it = byName.find(ind.name);
        if (it == byName.end()) {
            byName[ind.name] = &ind;
        }
        else if (!it->second->area || !ind.area || std::fabs(*it->second->area - *ind.area) > 1e-9 * *ind.area
                 || it->second->surfaceOnly != ind.surfaceOnly) {
            ambiguous.insert(ind.name);
        }
    }

    // OCCT's writer attaches the figures at the geometry level (a SHAPE_ASPECT of the part's
    // PRODUCT_DEFINITION_SHAPE). The practice puts them first at the part level (section 4.4), in
    // one combined representation with an empty name (section 4.10): add that, sharing the items.
    struct Group
    {
        Handle(StepRepr_ProductDefinitionShape) pds;
        Handle(StepRepr_RepresentationContext) context;
        std::map<std::string, Handle(StepRepr_RepresentationItem)> items;  // by item name
        bool occt {false};  // OCCT already wrote part-level figures for this PRODUCT_DEFINITION_SHAPE
    };
    std::vector<Group> groups;
    std::map<Standard_Transient*, size_t> index;
    std::set<Standard_Transient*> alreadyPartLevel;  // OCCT sometimes writes at part level itself

    for (int i = 1; i <= model->NbEntities(); ++i) {
        Handle(StepRepr_PropertyDefinitionRepresentation) pdr =
            Handle(StepRepr_PropertyDefinitionRepresentation)::DownCast(model->Value(i));
        if (pdr.IsNull() || pdr->UsedRepresentation().IsNull()) {
            continue;
        }
        Handle(StepRepr_PropertyDefinition) pd = pdr->Definition().PropertyDefinition();
        if (!isGeometricValidationProperty(pd)) {
            continue;
        }
        Handle(StepRepr_ProductDefinitionShape) pds;
        bool direct = false;
        if (Handle(StepRepr_ProductDefinitionShape) d = pd->Definition().ProductDefinitionShape()) {
            alreadyPartLevel.insert(d.get());
            pds = d;
            direct = true;
        }
        else {
            Handle(StepRepr_ShapeAspect) sa = pd->Definition().ShapeAspect();
            if (sa.IsNull() || sa->OfShape().IsNull()) {
                continue;
            }
            pds = sa->OfShape();
        }
        if (!index.count(pds.get())) {
            index[pds.get()] = groups.size();
            groups.push_back(Group {pds, pdr->UsedRepresentation()->ContextOfItems(), {}});
        }
        Group& g = groups[index[pds.get()]];
        g.occt = g.occt || direct;
        Handle(StepRepr_Representation) rep = pdr->UsedRepresentation();
        for (int k = 1; k <= rep->NbItems(); ++k) {
            g.items.emplace(lowerTrim(rep->ItemsValue(k)->Name()), rep->ItemsValue(k));
        }
    }

    int added = static_cast<int>(alreadyPartLevel.size());
    for (const Group& g : groups) {
        // the independent class of this part, if any (practice 4.5 and 4.7)
        const IndependentFigures* ind = nullptr;
        {
            Handle(StepBasic_ProductDefinition) prod = g.pds->Definition().ProductDefinition();
            if (!prod.IsNull() && !prod->Formation().IsNull() && !prod->Formation()->OfProduct().IsNull()) {
                std::string pname = prod->Formation()->OfProduct()->Name().IsNull()
                                        ? std::string()
                                        : std::string(prod->Formation()->OfProduct()->Name()->ToCString());
                auto f = byName.find(pname);
                if (f != byName.end() && !ambiguous.count(pname)) {
                    ind = f->second;
                }
            }
        }
        std::map<std::string, Handle(StepRepr_RepresentationItem)> items = g.items;
        std::vector<Handle(StepRepr_RepresentationItem)> newIndependent;
        if (ind && ind->surfaceOnly) {
            // a part of surfaces only: what OCCT wrote as the area and centre are the independent class
            const std::vector<std::pair<std::string, std::string>> renames {
                {"surface area measure", "independent surface area measure"}, {"centre point", "surface centre point"}};
            for (const auto& [from, to] : renames) {
                auto it = items.find(from);
                if (it != items.end()) {
                    it->second->SetName(new TCollection_HAsciiString(to.c_str()));
                    items[to] = it->second;
                    items.erase(from);
                }
            }
        }
        else if (ind && ind->area) {
            auto areaIt = items.find("surface area measure");
            Handle(StepRepr_MeasureRepresentationItem) areaItem =
                areaIt == items.end() ? Handle(StepRepr_MeasureRepresentationItem)()
                                      : Handle(StepRepr_MeasureRepresentationItem)::DownCast(areaIt->second);
            if (!areaItem.IsNull() && !areaItem->Measure().IsNull()) {
                Handle(StepBasic_MeasureValueMember) vm = new StepBasic_MeasureValueMember;
                vm->SetName("AREA_MEASURE");
                vm->SetReal(*ind->area);
                Handle(StepRepr_MeasureRepresentationItem) ni = new StepRepr_MeasureRepresentationItem;
                ni->Init(new TCollection_HAsciiString("independent surface area measure"), vm,
                         areaItem->Measure()->UnitComponent());
                items["independent surface area measure"] = ni;
                newIndependent.push_back(ni);
                if (ind->centroid) {
                    Handle(StepGeom_CartesianPoint) cp = new StepGeom_CartesianPoint;
                    cp->Init3D(new TCollection_HAsciiString("surface centre point"), ind->centroid->X(),
                               ind->centroid->Y(), ind->centroid->Z());
                    items["surface centre point"] = cp;
                    newIndependent.push_back(cp);
                }
            }
        }
        if (g.occt) {
            // OCCT wrote the solid class at part level itself, one definition per figure: the
            // independent class goes in a definition of its own, combined as in section 4.10
            if (!newIndependent.empty()) {
                Handle(NCollection_HArray1<Handle(StepRepr_RepresentationItem)>) arr =
                    new NCollection_HArray1<Handle(StepRepr_RepresentationItem)>(1, static_cast<int>(newIndependent.size()));
                for (size_t k = 0; k < newIndependent.size(); ++k) {
                    arr->SetValue(static_cast<int>(k) + 1, newIndependent[k]);
                }
                Handle(StepRepr_Representation) irep = new StepRepr_Representation;
                irep->Init(new TCollection_HAsciiString(""), arr, g.context);
                StepRepr_CharacterizedDefinition icd;
                icd.SetValue(g.pds);
                Handle(StepRepr_PropertyDefinition) ipd = new StepRepr_PropertyDefinition;
                ipd->Init(new TCollection_HAsciiString("geometric validation property"), true,
                          new TCollection_HAsciiString("independent surfaces of the part"), icd);
                StepRepr_RepresentedDefinition ird;
                ird.SetValue(ipd);
                Handle(StepRepr_PropertyDefinitionRepresentation) ipdr = new StepRepr_PropertyDefinitionRepresentation;
                ipdr->Init(ird, irep);
                model->AddWithRefs(ipdr);
            }
            continue;
        }
        // fixed order: volume, area, centroid, then the independent class
        std::vector<Handle(StepRepr_RepresentationItem)> ordered;
        for (const char* key : {"volume measure", "surface area measure", "centre point",
                                "independent surface area measure", "surface centre point"}) {
            auto it = items.find(key);
            if (it != items.end()) {
                ordered.push_back(it->second);
            }
        }
        if (ordered.empty()) {
            continue;
        }
        Handle(NCollection_HArray1<Handle(StepRepr_RepresentationItem)>) arr =
            new NCollection_HArray1<Handle(StepRepr_RepresentationItem)>(1, static_cast<int>(ordered.size()));
        for (size_t k = 0; k < ordered.size(); ++k) {
            arr->SetValue(static_cast<int>(k) + 1, ordered[k]);
        }
        Handle(StepRepr_Representation) rep = new StepRepr_Representation;
        rep->Init(new TCollection_HAsciiString(""), arr, g.context);

        StepRepr_CharacterizedDefinition cd;
        cd.SetValue(g.pds);
        Handle(StepRepr_PropertyDefinition) pd = new StepRepr_PropertyDefinition;
        pd->Init(new TCollection_HAsciiString("geometric validation property"), true,
                 new TCollection_HAsciiString("volume, area and centroid of the part"), cd);

        StepRepr_RepresentedDefinition rd;
        rd.SetValue(pd);
        Handle(StepRepr_PropertyDefinitionRepresentation) pdr = new StepRepr_PropertyDefinitionRepresentation;
        pdr->Init(rd, rep);
        model->AddWithRefs(pdr);
        ++added;
    }
    return added;
}

}  // namespace Import

namespace Import
{

int reportStepValidation(const Handle(XSControl_WorkSession)& ws)
{
    int warned = 0;
    try {
        bool enabled = App::GetApplication()
                           .GetUserParameter()
                           .GetGroup("BaseApp")
                           ->GetGroup("Preferences")
                           ->GetGroup("Mod/Part")
                           ->GetGroup("STEP")
                           ->GetBool("CheckValidationProperties", true);
        if (!enabled || ws.IsNull()) {
            return 0;
        }
        StepFileFinding finding;
        validateWorkSession(ws, finding);
        for (const auto& part : finding.parts) {
            if (part.verdict == StepVerdict::disagree || part.verdict == StepVerdict::invalid_geometry) {
                Base::Console().warning("{}\n", describeFinding(part));
                ++warned;
            }
        }
    }
    catch (...) {
        // checking never stops an import
    }
    return warned;
}

}  // namespace Import
