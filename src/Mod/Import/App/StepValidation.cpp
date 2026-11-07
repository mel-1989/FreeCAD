// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Geometric validation properties of STEP files (G13 slice 1a).         *
 *   See StepValidation.h for the public source of the behaviour.          *
 ***************************************************************************/

#include <cmath>
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <sstream>

#include <BRepBndLib.hxx>
#include <BRepCheck_Analyzer.hxx>
#include <BRepGProp.hxx>
#include <Bnd_Box.hxx>
#include <GProp_GProps.hxx>
#include <HeaderSection_FileSchema.hxx>
#include <STEPControl_Reader.hxx>
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

    GProp_GProps sprops;
    BRepGProp::SurfaceProperties(shape, sprops);
    part.measured.area = sprops.Mass();

    TopExp_Explorer solids(shape, TopAbs_SOLID);
    if (solids.More()) {
        GProp_GProps vprops;
        BRepGProp::VolumeProperties(shape, vprops);
        part.measured.volume = vprops.Mass();
        part.measured.centroid = vprops.CentreOfMass();
    }
    else if (part.measured.area && *part.measured.area > 0.0) {
        part.measured.centroid = sprops.CentreOfMass();
    }
}

void judgePart(StepPartFinding& part, bool shapeFailed)
{
    using namespace StepValidationTolerance;
    part.volumeDisagrees = part.areaDisagrees = part.centroidDisagrees = false;
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

    bool unusable = part.statedUnusable;
    bool anyStated = false;

    if (part.stated.volume) {
        double s = *part.stated.volume;
        if (!std::isfinite(s) || s == 0.0) {
            unusable = true;
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
    if (part.stated.area) {
        double s = *part.stated.area;
        if (!std::isfinite(s) || s == 0.0) {
            unusable = true;
        }
        else {
            anyStated = true;
            if (part.measured.area) {
                part.areaDiff = *part.measured.area - s;
                part.areaDisagrees = std::fabs(*part.areaDiff) > areaRelative * std::fabs(s);
            }
            else {
                part.areaDisagrees = true;
            }
        }
    }
    if (part.stated.centroid) {
        if (!finite3(*part.stated.centroid)) {
            unusable = true;
        }
        else {
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

    if (part.volumeDisagrees || part.areaDisagrees || part.centroidDisagrees) {
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

/// (factor to the SI base unit, exponent of length) for the unit of a measure.
std::optional<std::pair<double, double>> measureUnit(const StepBasic_Unit& u, std::optional<double> contextLength)
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
        // StepBasic_AreaUnit / StepBasic_VolumeUnit: OCCT's parser drops the elements. We then
        // take the length unit of the representation's own context, the way those files are written.
        if (nu->IsKind(STANDARD_TYPE(StepBasic_AreaUnit)) || nu->IsKind(STANDARD_TYPE(StepBasic_VolumeUnit))) {
            if (!contextLength) {
                return std::nullopt;
            }
            double e = nu->IsKind(STANDARD_TYPE(StepBasic_AreaUnit)) ? 2.0 : 3.0;
            return std::make_pair(std::pow(*contextLength, e), e);
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
};

void readRepresentation(const Handle(StepRepr_Representation)& rep, Stated& st)
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
                st.seen = true;
                auto k = toMillimetres(measureUnit(m->UnitComponent(), ctxLen), 3.0);
                if (!k) { st.unusable = true; }
                else if (!st.figures.volume) { st.figures.volume = val * *k; }
            }
            else if (type == "AREA_MEASURE"
                     && (name == "surface area measure" || name == "wetted area measure")) {
                st.seen = true;
                auto k = toMillimetres(measureUnit(m->UnitComponent(), ctxLen), 2.0);
                if (!k) { st.unusable = true; }
                else if (!st.figures.area) { st.figures.area = val * *k; }
            }
        }
        else if (Handle(StepGeom_CartesianPoint) cp = Handle(StepGeom_CartesianPoint)::DownCast(it)) {
            if (name == "centre point" && cp->NbCoordinates() == 3) {
                st.seen = true;
                if (!ctxLen) {
                    st.unusable = true;
                }
                else if (!st.figures.centroid) {
                    double k = *ctxLen * 1000.0;
                    st.figures.centroid =
                        gp_Pnt(cp->CoordinatesValue(1) * k, cp->CoordinatesValue(2) * k, cp->CoordinatesValue(3) * k);
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

    // Pass 1: the validation properties attached at part level, by product definition.
    std::map<Standard_Transient*, Stated> stated;
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
            if (pd.IsNull() || lowerTrim(pd->Name()) != "geometric validation property") {
                continue;
            }
            Handle(StepRepr_ProductDefinitionShape) pds = pd->Definition().ProductDefinitionShape();
            if (pds.IsNull()) {
                continue;  // geometry level (shape aspect) is not read; the practice puts the part level first
            }
            Handle(StepBasic_ProductDefinition) prod = pds->Definition().ProductDefinition();
            if (prod.IsNull()) {
                continue;  // assembly usage, not a part
            }
            readRepresentation(pdr->UsedRepresentation(), stated[prod.get()]);
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
        rel("area", part.stated.area, part.measured.area);
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
