// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Part identity, user defined attributes and layers of STEP files       *
 *   (G13 slice 1c). See StepAttributes.h for the public source.           *
 ***************************************************************************/

#include <algorithm>
#include <charconv>
#include <cmath>
#include <set>
#include <sstream>

#include <NCollection_Array1.hxx>
#include <NCollection_Sequence.hxx>
#include <STEPConstruct.hxx>
#include <StepBasic_ConversionBasedUnit.hxx>
#include <StepBasic_GeneralProperty.hxx>
#include <StepBasic_GeneralPropertyAssociation.hxx>
#include <StepBasic_MeasureValueMember.hxx>
#include <StepBasic_MeasureWithUnit.hxx>
#include <StepBasic_NamedUnit.hxx>
#include <StepBasic_Product.hxx>
#include <StepBasic_ProductDefinitionFormation.hxx>
#include <StepBasic_SiPrefix.hxx>
#include <StepBasic_SiUnit.hxx>
#include <StepBasic_SiUnitName.hxx>
#include <StepBasic_Unit.hxx>
#include <StepRepr_BooleanRepresentationItem.hxx>
#include <StepRepr_DescriptiveRepresentationItem.hxx>
#include <StepRepr_IntegerRepresentationItem.hxx>
#include <StepRepr_MeasureRepresentationItem.hxx>
#include <StepRepr_NextAssemblyUsageOccurrence.hxx>
#include <StepRepr_ProductDefinitionShape.hxx>
#include <StepRepr_PropertyDefinition.hxx>
#include <StepRepr_PropertyDefinitionRepresentation.hxx>
#include <StepRepr_RealRepresentationItem.hxx>
#include <StepRepr_Representation.hxx>
#include <StepRepr_RepresentationRelationship.hxx>
#include <StepRepr_RepresentationRelationshipWithTransformation.hxx>
#include <StepRepr_ValueRepresentationItem.hxx>
#include <StepRepr_RepresentationContext.hxx>
#include <StepRepr_RepresentedDefinition.hxx>
#include <StepRepr_CharacterizedDefinition.hxx>
#include <NCollection_HArray1.hxx>
#include <StepVisual_LayeredItem.hxx>
#include <StepShape_ManifoldSolidBrep.hxx>
#include <StepShape_ShapeDefinitionRepresentation.hxx>
#include <StepVisual_LayeredItem.hxx>
#include <StepVisual_PresentationLayerAssignment.hxx>
#include <TCollection_AsciiString.hxx>
#include <TCollection_ExtendedString.hxx>
#include <TCollection_HAsciiString.hxx>
#include <TDataStd_Name.hxx>
#include <TDataStd_NamedData.hxx>
#include <TDataStd_Comment.hxx>
#include <TDF_TagSource.hxx>
#include <TDF_Tool.hxx>
#include <TopAbs_ShapeEnum.hxx>
#include <TopExp_Explorer.hxx>
#include <TopoDS_Iterator.hxx>
#include <TDF_ChildIterator.hxx>
#include <TopoDS_Shape.hxx>
#include <Transfer_Binder.hxx>
#include <Transfer_FinderProcess.hxx>
#include <Transfer_TransientProcess.hxx>
#include <TransferBRep.hxx>
#include <XCAFDoc_DocumentTool.hxx>
#include <XCAFDoc_ShapeTool.hxx>
#include <XSControl_TransferReader.hxx>
#include <XSControl_TransferWriter.hxx>

#include <App/Application.h>
#include <App/DocumentObject.h>
#include <App/PropertyContainer.h>
#include <App/PropertyStandard.h>
#include <Base/Console.h>

#include "StepAttributes.h"

namespace Import
{

namespace
{
bool stepPreference(const char* name)
{
    return App::GetApplication()
        .GetUserParameter()
        .GetGroup("BaseApp")
        ->GetGroup("Preferences")
        ->GetGroup("Mod/Part")
        ->GetGroup("STEP")
        ->GetBool(name, true);
}

using HAscii = Handle(TCollection_HAsciiString);

std::string text(const HAscii& h)
{
    return h.IsNull() ? std::string() : std::string(h->ToCString());
}

std::string toUtf8(const TCollection_ExtendedString& s)
{
    std::string out(static_cast<size_t>(s.LengthOfCString()) + 1, '\0');
    char* buffer = out.data();
    s.ToUTF8CString(buffer);
    out.resize(std::strlen(out.c_str()));
    return out;
}

TCollection_ExtendedString fromUtf8(const std::string& s)
{
    return TCollection_ExtendedString(s.c_str(), true);
}

std::string numberText(double v)
{
    char buf[64];
    auto r = std::to_chars(buf, buf + sizeof(buf), v);
    return std::string(buf, r.ptr);
}

const char* prefixSymbol(StepBasic_SiPrefix p)
{
    switch (p) {
        case StepBasic_spExa: return "E";
        case StepBasic_spPeta: return "P";
        case StepBasic_spTera: return "T";
        case StepBasic_spGiga: return "G";
        case StepBasic_spMega: return "M";
        case StepBasic_spKilo: return "k";
        case StepBasic_spHecto: return "h";
        case StepBasic_spDeca: return "da";
        case StepBasic_spDeci: return "d";
        case StepBasic_spCenti: return "c";
        case StepBasic_spMilli: return "m";
        case StepBasic_spMicro: return "u";
        case StepBasic_spNano: return "n";
        case StepBasic_spPico: return "p";
        case StepBasic_spFemto: return "f";
        case StepBasic_spAtto: return "a";
    }
    return "";
}

const char* baseSymbol(StepBasic_SiUnitName n)
{
    switch (n) {
        case StepBasic_sunMetre: return "m";
        case StepBasic_sunGram: return "g";
        case StepBasic_sunSecond: return "s";
        case StepBasic_sunAmpere: return "A";
        case StepBasic_sunKelvin: return "K";
        case StepBasic_sunMole: return "mol";
        case StepBasic_sunCandela: return "cd";
        case StepBasic_sunRadian: return "rad";
        case StepBasic_sunSteradian: return "sr";
        case StepBasic_sunHertz: return "Hz";
        case StepBasic_sunNewton: return "N";
        case StepBasic_sunPascal: return "Pa";
        case StepBasic_sunJoule: return "J";
        case StepBasic_sunWatt: return "W";
        case StepBasic_sunCoulomb: return "C";
        case StepBasic_sunVolt: return "V";
        case StepBasic_sunFarad: return "F";
        case StepBasic_sunOhm: return "ohm";
        case StepBasic_sunSiemens: return "S";
        case StepBasic_sunWeber: return "Wb";
        case StepBasic_sunTesla: return "T";
        case StepBasic_sunHenry: return "H";
        case StepBasic_sunDegreeCelsius: return "degC";
        case StepBasic_sunLumen: return "lm";
        case StepBasic_sunLux: return "lx";
        case StepBasic_sunBecquerel: return "Bq";
        case StepBasic_sunGray: return "Gy";
        case StepBasic_sunSievert: return "Sv";
    }
    return "?";
}

std::string unitSymbol(const StepBasic_Unit& unit)
{
    Handle(StepBasic_NamedUnit) nu = unit.NamedUnit();
    if (nu.IsNull()) {
        return "?";  // a derived unit: not resolved
    }
    if (Handle(StepBasic_ConversionBasedUnit) cb = Handle(StepBasic_ConversionBasedUnit)::DownCast(nu)) {
        return text(cb->Name());
    }
    if (Handle(StepBasic_SiUnit) si = Handle(StepBasic_SiUnit)::DownCast(nu)) {
        std::string pre = si->HasPrefix() ? prefixSymbol(si->Prefix()) : "";
        return pre + baseSymbol(si->Name());
    }
    return "?";
}

/// The text of one attribute value item; nullopt when the item is not one of the value kinds.
std::optional<std::string> valueText(const Handle(StepRepr_RepresentationItem)& item)
{
    if (item.IsNull()) {
        return std::nullopt;
    }
    if (auto d = Handle(StepRepr_DescriptiveRepresentationItem)::DownCast(item)) {
        return text(d->Description());
    }
    if (auto i = Handle(StepRepr_IntegerRepresentationItem)::DownCast(item)) {
        return std::to_string(i->Value());
    }
    if (auto r = Handle(StepRepr_RealRepresentationItem)::DownCast(item)) {
        return numberText(r->Value());
    }
    if (auto b = Handle(StepRepr_BooleanRepresentationItem)::DownCast(item)) {
        return std::string(b->Value() ? "TRUE" : "FALSE");
    }
    if (auto v = Handle(StepRepr_ValueRepresentationItem)::DownCast(item)) {
        if (v->ValueComponentMember().IsNull()) {
            return std::nullopt;
        }
        return numberText(v->ValueComponentMember()->Real());
    }
    if (auto m = Handle(StepRepr_MeasureRepresentationItem)::DownCast(item)) {
        if (m->Measure().IsNull()) {
            return std::nullopt;
        }
        return numberText(m->Measure()->ValueComponent()) + " " + unitSymbol(m->Measure()->UnitComponent());
    }
    return std::nullopt;
}

/// Representations that belong to one part, joined by relationships without a transformation.
class RepGroups
{
public:
    Standard_Transient* find(Standard_Transient* x)
    {
        auto it = parent.find(x);
        if (it == parent.end()) {
            parent[x] = x;
            return x;
        }
        while (parent[x] != x) {
            parent[x] = parent[parent[x]];
            x = parent[x];
        }
        return x;
    }
    void join(Standard_Transient* a, Standard_Transient* b)
    {
        parent[find(a)] = find(b);
    }

private:
    std::map<Standard_Transient*, Standard_Transient*> parent;
};

struct ProductLookup
{
    RepGroups groups;
    std::map<Standard_Transient*, Handle(StepBasic_ProductDefinition)> pdOfGroup;
    std::map<Standard_Transient*, Standard_Transient*> repOfItem;  // item -> representation
    std::map<Standard_Transient*, Handle(StepRepr_RepresentationContext)> contextOfPd;

    explicit ProductLookup(const Handle(StepData_StepModel)& model)
    {
        for (int i = 1; i <= model->NbEntities(); ++i) {
            Handle(Standard_Transient) ent = model->Value(i);
            if (auto sdr = Handle(StepShape_ShapeDefinitionRepresentation)::DownCast(ent)) {
                Handle(StepRepr_PropertyDefinition) def = sdr->Definition().PropertyDefinition();
                Handle(StepRepr_ProductDefinitionShape) pds = Handle(StepRepr_ProductDefinitionShape)::DownCast(def);
                if (!pds.IsNull() && !sdr->UsedRepresentation().IsNull()) {
                    Handle(StepBasic_ProductDefinition) pd = pds->Definition().ProductDefinition();
                    if (!pd.IsNull()) {
                        groups.find(sdr->UsedRepresentation().get());
                        pdOfGroup[sdr->UsedRepresentation().get()] = pd;
                        if (!contextOfPd.count(pd.get())) {
                            contextOfPd[pd.get()] = sdr->UsedRepresentation()->ContextOfItems();
                        }
                    }
                }
            }
            else if (auto rr = Handle(StepRepr_RepresentationRelationship)::DownCast(ent)) {
                if (Handle(StepRepr_RepresentationRelationshipWithTransformation)::DownCast(ent).IsNull()
                    && !rr->Rep1().IsNull() && !rr->Rep2().IsNull()) {
                    groups.join(rr->Rep1().get(), rr->Rep2().get());
                }
            }
            if (auto rep = Handle(StepRepr_Representation)::DownCast(ent)) {
                if (!rep->Items().IsNull()) {
                    for (int k = 1; k <= rep->NbItems(); ++k) {
                        if (!rep->ItemsValue(k).IsNull()) {
                            repOfItem.emplace(rep->ItemsValue(k).get(), rep.get());
                        }
                    }
                }
            }
        }
        // resolve the group of every registered representation once the relationships are known
        std::map<Standard_Transient*, Handle(StepBasic_ProductDefinition)> resolved;
        for (auto& [rep, pd] : pdOfGroup) {
            resolved.emplace(groups.find(rep), pd);
        }
        pdOfGroup = std::move(resolved);
    }

    Handle(StepBasic_ProductDefinition) ofRepresentation(Standard_Transient* rep)
    {
        auto it = pdOfGroup.find(groups.find(rep));
        return it == pdOfGroup.end() ? Handle(StepBasic_ProductDefinition)() : it->second;
    }

    Handle(StepBasic_ProductDefinition) ofItem(Standard_Transient* item)
    {
        auto it = repOfItem.find(item);
        return it == repOfItem.end() ? Handle(StepBasic_ProductDefinition)() : ofRepresentation(it->second);
    }
};

std::string entityKind(const Handle(Standard_Transient)& e)
{
    return e.IsNull() ? std::string("?") : std::string(e->DynamicType()->Name());
}

TDF_Label labelFromShape(const Handle(XCAFDoc_ShapeTool)& tool, const TopoDS_Shape& shape)
{
    if (shape.IsNull()) {
        return TDF_Label();
    }
    return tool->FindShape(shape.Located(TopLoc_Location()));
}

}  // namespace

/// OCCT's own reader refuses a STEP record of about 16 000 characters; a text longer than this is cut
/// where the cost of writing it (a non-ASCII character takes ten characters as \\X2\\hhhh\\X0\\) passes the cap.
std::string capForStep(const std::string& s, size_t cap, bool& cut)
{
    size_t cost = 0;
    size_t i = 0;
    while (i < s.size()) {
        unsigned char c = static_cast<unsigned char>(s[i]);
        size_t len = c < 0x80 ? 1 : (c >> 5) == 6 ? 2 : (c >> 4) == 14 ? 3 : 4;
        size_t add = c < 0x80 ? 1 : 10;
        if (cost + add > cap) {
            cut = true;
            return s.substr(0, i);
        }
        cost += add;
        i += len;
    }
    return s;
}

bool readStepAttributesEnabled()
{
    return stepPreference("ReadStepAttributes");
}

bool writeStepAttributesEnabled()
{
    return stepPreference("WriteStepAttributes");
}

std::vector<StepPartData> collectStepPartData(const Handle(StepData_StepModel)& model, StepAttributeCounts* counts)
{
    std::vector<StepPartData> out;
    if (model.IsNull()) {
        return out;
    }
    std::map<Standard_Transient*, size_t> index;  // product definition -> out
    std::set<Standard_Transient*> attributeDefinitions;
    std::map<Standard_Transient*, std::vector<Handle(StepRepr_PropertyDefinitionRepresentation)>> valuesOf;

    for (int i = 1; i <= model->NbEntities(); ++i) {
        Handle(Standard_Transient) ent = model->Value(i);
        if (auto pd = Handle(StepBasic_ProductDefinition)::DownCast(ent)) {
            if (pd->Formation().IsNull()) {
                continue;
            }
            StepPartData d;
            d.definition = pd;
            d.revision = text(pd->Formation()->Id());
            if (!pd->Formation()->OfProduct().IsNull()) {
                d.partNumber = text(pd->Formation()->OfProduct()->Id());
                // FreeCAD writes the object's label as both id and name; an id that only repeats the name
                // tells the user nothing the object's label does not
                if (d.partNumber == text(pd->Formation()->OfProduct()->Name())) {
                    d.partNumber.clear();
                }
                d.description = text(pd->Formation()->OfProduct()->Description());
            }
            index[pd.get()] = out.size();
            out.push_back(std::move(d));
        }
        else if (auto gpa = Handle(StepBasic_GeneralPropertyAssociation)::DownCast(ent)) {
            if (!gpa->PropertyDefinition().IsNull()) {
                attributeDefinitions.insert(gpa->PropertyDefinition().get());
            }
        }
        else if (auto pdr = Handle(StepRepr_PropertyDefinitionRepresentation)::DownCast(ent)) {
            if (!Handle(StepShape_ShapeDefinitionRepresentation)::DownCast(ent).IsNull()) {
                continue;
            }
            Handle(StepRepr_PropertyDefinition) def = pdr->Definition().PropertyDefinition();
            if (!def.IsNull()) {
                valuesOf[def.get()].push_back(pdr);
            }
        }
    }

    // attributes, in the order of their definitions
    for (int i = 1; i <= model->NbEntities(); ++i) {
        Handle(StepRepr_PropertyDefinition) def = Handle(StepRepr_PropertyDefinition)::DownCast(model->Value(i));
        if (def.IsNull() || !attributeDefinitions.count(def.get())) {
            continue;
        }
        Handle(StepBasic_ProductDefinition) target = def->Definition().ProductDefinition();
        if (target.IsNull()) {
            if (counts) {
                Handle(Standard_Transient) t = def->Definition().Value();
                ++counts->otherLevelAttributes[entityKind(t)];
            }
            continue;
        }
        auto at = index.find(target.get());
        if (at == index.end()) {
            continue;
        }
        StepPartData& part = out[at->second];
        std::optional<std::string> value;
        auto reps = valuesOf.find(def.get());
        if (reps != valuesOf.end() && reps->second.size() == 1) {
            Handle(StepRepr_Representation) rep = reps->second.front()->UsedRepresentation();
            if (!rep.IsNull() && !rep->Items().IsNull() && rep->NbItems() == 1) {
                value = valueText(rep->ItemsValue(1));
            }
        }
        if (!value) {
            ++part.unsupported;
            continue;
        }
        std::string key = text(def->Name());
        auto same = std::find_if(part.attributes.begin(), part.attributes.end(), [&](const auto& kv) {
            return kv.first == key;
        });
        if (same != part.attributes.end()) {
            part.duplicateKeys.push_back(key);
            continue;
        }
        part.attributes.emplace_back(key, *value);
    }

    // layers on whole solids
    ProductLookup lookup(model);
    for (int i = 1; i <= model->NbEntities(); ++i) {
        Handle(StepVisual_PresentationLayerAssignment) pla =
            Handle(StepVisual_PresentationLayerAssignment)::DownCast(model->Value(i));
        if (pla.IsNull() || pla->AssignedItems().IsNull()) {
            continue;
        }
        std::string name = text(pla->Name());
        std::string label = name.empty() ? text(pla->Description()) : name;
        for (int k = 1; k <= pla->NbAssignedItems(); ++k) {
            StepVisual_LayeredItem item = pla->AssignedItemsValue(k);
            Handle(StepRepr_RepresentationItem) ri = item.RepresentationItem();
            if (!Handle(StepShape_ManifoldSolidBrep)::DownCast(ri).IsNull()) {
                Handle(StepBasic_ProductDefinition) pd = lookup.ofItem(ri.get());
                auto at = pd.IsNull() ? index.end() : index.find(pd.get());
                if (at != index.end()) {
                    auto& layers = out[at->second].layers;
                    if (std::find(layers.begin(), layers.end(), label) == layers.end()) {
                        layers.push_back(label);
                    }
                    out[at->second].solidLayers.emplace_back(ri, label);
                }
            }
            else if (counts) {
                Handle(Standard_Transient) t = item.Value();
                ++counts->otherLayerTargets[entityKind(t)];
            }
        }
    }
    return out;
}

namespace
{
constexpr const char* kIdPrefix = "id:";
constexpr const char* kAttrPrefix = "uda:";
constexpr const char* kLayerPrefix = "layer:";

std::string labelEntry(const TDF_Label& l)
{
    TCollection_AsciiString e;
    TDF_Tool::Entry(l, e);
    return e.ToCString();
}

/// The data of a part label lives on a private label (not on the part label itself, because OCCT's writer
/// writes the named data of every shape label as STEP properties of its own).
Handle(TDataStd_NamedData) namedData(const TDF_Label& label, bool create)
{
    TDF_Label root = label.Root().FindChild(1, true).FindChild(700, true);
    std::string entry = labelEntry(label);
    for (TDF_ChildIterator it(root, false); it.More(); it.Next()) {
        Handle(TDataStd_Comment) c;
        if (it.Value().FindAttribute(TDataStd_Comment::GetID(), c) && toUtf8(c->Get()) == entry) {
            Handle(TDataStd_NamedData) nd;
            it.Value().FindAttribute(TDataStd_NamedData::GetID(), nd);
            return nd;
        }
    }
    Handle(TDataStd_NamedData) nd;
    if (create) {
        TDF_Label slot = TDF_TagSource::NewChild(root);
        TDataStd_Comment::Set(slot, fromUtf8(entry));
        nd = TDataStd_NamedData::Set(slot);
    }
    return nd;
}

void setString(const Handle(TDataStd_NamedData)& nd, const std::string& key, const std::string& value)
{
    nd->SetString(fromUtf8(key), fromUtf8(value));
}

std::string ordinal(size_t n)
{
    char buf[16];
    std::snprintf(buf, sizeof(buf), "%06zu", n);
    return buf;
}

std::string labelName(const TDF_Label& l)
{
    Handle(TDataStd_Name) n;
    if (l.FindAttribute(TDataStd_Name::GetID(), n)) {
        return toUtf8(n->Get());
    }
    return {};
}
}  // namespace

void stashStepAttributes(const Handle(XSControl_WorkSession)& ws, const Handle(TDocStd_Document)& hDoc)
{
    try {
        if (!readStepAttributesEnabled() || ws.IsNull() || hDoc.IsNull()) {
            return;
        }
        Handle(StepData_StepModel) model = Handle(StepData_StepModel)::DownCast(ws->Model());
        Handle(XSControl_TransferReader) reader = ws->TransferReader();
        if (model.IsNull() || reader.IsNull() || reader->TransientProcess().IsNull()) {
            return;
        }
        StepAttributeCounts counts;
        std::vector<StepPartData> data = collectStepPartData(model, &counts);
        Handle(XCAFDoc_ShapeTool) tool = XCAFDoc_DocumentTool::ShapeTool(hDoc->Main());
        Handle(Transfer_TransientProcess) tp = reader->TransientProcess();

        // assemblies: the label whose components refer to the labels of the product's children
        std::map<Standard_Transient*, std::vector<Standard_Transient*>> children;
        for (int i = 1; i <= model->NbEntities(); ++i) {
            if (auto nauo = Handle(StepRepr_NextAssemblyUsageOccurrence)::DownCast(model->Value(i))) {
                if (!nauo->RelatingProductDefinition().IsNull() && !nauo->RelatedProductDefinition().IsNull()) {
                    children[nauo->RelatingProductDefinition().get()].push_back(nauo->RelatedProductDefinition().get());
                }
            }
        }
        // parts: the label whose shape is the one transferred for the PRODUCT_DEFINITION
        std::map<Standard_Transient*, TDF_Label> labelOf;
        std::set<std::string> claimed;
        for (const auto& d : data) {
            Handle(Transfer_Binder) b = tp->Find(d.definition);
            if (b.IsNull()) {
                continue;
            }
            TopoDS_Shape result = TransferBRep::ShapeResult(b);
            TDF_Label l = labelFromShape(tool, result);
            // OCCT keeps a compound that holds one thing as that thing
            while (l.IsNull() && !children.count(d.definition.get()) && !result.IsNull() && result.ShapeType() == TopAbs_COMPOUND) {
                TopoDS_Iterator it(result, false, false);
                if (!it.More()) {
                    break;
                }
                result = it.Value();
                it.Next();
                if (it.More()) {
                    break;
                }
                l = labelFromShape(tool, result);
            }
            if (!l.IsNull() && claimed.insert(labelEntry(l)).second) {
                labelOf[d.definition.get()] = l;
            }
        }
        NCollection_Sequence<TDF_Label> shapes;
        tool->GetShapes(shapes);
        std::map<std::string, TDF_Label> assemblyLabels;                       // entry -> label
        std::map<std::string, std::vector<std::string>> assemblySignature;     // entry -> sorted referred entries
        for (int i = 1; i <= shapes.Length(); ++i) {
            TDF_Label l = shapes.Value(i);
            if (!tool->IsAssembly(l) || claimed.count(labelEntry(l))) {
                continue;
            }
            NCollection_Sequence<TDF_Label> comps;
            tool->GetComponents(l, comps, false);
            std::vector<std::string> sig;
            for (int c = 1; c <= comps.Length(); ++c) {
                TDF_Label ref;
                if (tool->GetReferredShape(comps.Value(c), ref)) {
                    sig.push_back(labelEntry(ref));
                }
            }
            std::sort(sig.begin(), sig.end());
            assemblyLabels[labelEntry(l)] = l;
            assemblySignature[labelEntry(l)] = std::move(sig);
        }
        bool progress = true;
        while (progress) {
            progress = false;
            for (const auto& d : data) {
                auto ch = children.find(d.definition.get());
                if (ch == children.end() || labelOf.count(d.definition.get())) {
                    continue;
                }
                std::vector<std::string> sig;
                bool ready = true;
                for (Standard_Transient* c : ch->second) {
                    auto lo = labelOf.find(c);
                    if (lo == labelOf.end()) {
                        ready = false;
                        break;
                    }
                    sig.push_back(labelEntry(lo->second));
                }
                if (!ready) {
                    continue;
                }
                std::sort(sig.begin(), sig.end());
                std::vector<std::string> candidates;
                for (const auto& [entry, s] : assemblySignature) {
                    if (s == sig && !claimed.count(entry)) {
                        candidates.push_back(entry);
                    }
                }
                if (candidates.size() > 1) {
                    std::string pname = text(d.definition->Formation()->OfProduct()->Name());
                    std::vector<std::string> named;
                    for (const auto& e : candidates) {
                        if (labelName(assemblyLabels[e]) == pname) {
                            named.push_back(e);
                        }
                    }
                    candidates = named;
                }
                if (candidates.size() == 1) {
                    labelOf[d.definition.get()] = assemblyLabels[candidates.front()];
                    claimed.insert(candidates.front());
                    progress = true;
                }
            }
        }

        // a product OCCT turned into an assembly of its own representation items (a solid and a curve set):
        // the one unclaimed assembly label that carries the product's name
        for (const auto& d : data) {
            if (labelOf.count(d.definition.get()) || children.count(d.definition.get())
                || (d.attributes.empty() && d.partNumber.empty() && d.description.empty() && d.revision.empty())) {
                continue;
            }
            std::string pname = text(d.definition->Formation()->OfProduct()->Name());
            std::string pid = text(d.definition->Formation()->OfProduct()->Id());
            std::vector<TDF_Label> candidates;
            for (int i = 1; i <= shapes.Length(); ++i) {
                TDF_Label l = shapes.Value(i);
                std::string ln = labelName(l);
                if (tool->IsAssembly(l) && !claimed.count(labelEntry(l)) && (ln == pname || ln == pid)) {
                    candidates.push_back(l);
                }
            }
            if (candidates.size() == 1) {
                labelOf[d.definition.get()] = candidates.front();
                claimed.insert(labelEntry(candidates.front()));
            }
        }
        // layers: the label of the solid the layer names, found through the entity OCCT transferred it from
        std::map<Standard_Transient*, TDF_Label> solidLabel;
        for (int i = 1; i <= shapes.Length(); ++i) {
            NCollection_Sequence<TDF_Label> candidates;
            candidates.Append(shapes.Value(i));
            NCollection_Sequence<TDF_Label> subs;
            tool->GetSubShapes(shapes.Value(i), subs);
            for (int k = 1; k <= subs.Length(); ++k) {
                candidates.Append(subs.Value(k));
            }
            for (int k = 1; k <= candidates.Length(); ++k) {
                TopoDS_Shape sh = tool->GetShape(candidates.Value(k));
                if (sh.IsNull() || sh.ShapeType() != TopAbs_SOLID) {
                    continue;
                }
                Handle(Standard_Transient) ent = reader->EntityFromShapeResult(sh.Located(TopLoc_Location()), 1);
                if (!ent.IsNull()) {
                    solidLabel.emplace(ent.get(), candidates.Value(k));
                }
            }
        }
        std::map<std::string, size_t> layerCount;
        for (const auto& d : data) {
            for (const auto& [solid, name] : d.solidLayers) {
                auto sl = solidLabel.find(solid.get());
                if (sl == solidLabel.end()) {
                    Base::Console().log("STEP attributes: layer '{}' names a solid that has no object; not attached\n", name);
                    continue;
                }
                Handle(TDataStd_NamedData) nd = namedData(sl->second, true);
                std::string e = labelEntry(sl->second);
                setString(nd, std::string(kLayerPrefix) + ordinal(layerCount[e]++), name);
            }
        }
        int stashed = 0;
        for (const auto& d : data) {
            auto lo = labelOf.find(d.definition.get());
            bool holds = !d.partNumber.empty() || !d.description.empty() || !d.revision.empty()
                || !d.attributes.empty();
            if (lo == labelOf.end()) {
                if (!d.attributes.empty() || !d.layers.empty()) {
                    Base::Console().log(
                        "STEP attributes: product '{}' has {} attribute(s) and {} layer(s) but no object of its own; not attached\n",
                        d.partNumber,
                        d.attributes.size(),
                        d.layers.size()
                    );
                }
                continue;
            }
            if (!holds) {
                continue;
            }
            Handle(TDataStd_NamedData) nd = namedData(lo->second, true);
            if (!d.partNumber.empty()) {
                setString(nd, std::string(kIdPrefix) + "Part Number", d.partNumber);
            }
            if (!d.description.empty()) {
                setString(nd, std::string(kIdPrefix) + "Description", d.description);
            }
            if (!d.revision.empty()) {
                setString(nd, std::string(kIdPrefix) + "Revision", d.revision);
            }
            for (const auto& [k, v] : d.attributes) {
                setString(nd, std::string(kAttrPrefix) + k, v);
            }
            for (const auto& k : d.duplicateKeys) {
                Base::Console().warning("STEP attributes: part '{}' repeats the attribute '{}'; the first value is kept\n",
                                        d.partNumber,
                                        k);
            }
            ++stashed;
        }
        int other = 0;
        for (const auto& [k, n] : counts.otherLevelAttributes) {
            other += n;
            Base::Console().log("STEP attributes: {} attribute(s) attached to {} are not imported\n", n, k);
        }
        for (const auto& [k, n] : counts.otherLayerTargets) {
            Base::Console().log("STEP attributes: {} layer assignment(s) to {} are not imported\n", n, k);
        }
        (void)other;
        (void)stashed;
    }
    catch (...) {
        // reading attributes never stops an import
    }
}

void applyStepAttributes(App::DocumentObject* obj, const TDF_Label& label, bool includeSubShapes)
{
    try {
        if (!obj || label.IsNull() || !readStepAttributesEnabled()) {
            return;
        }
        std::map<std::string, std::string> attributes;
        std::map<std::string, std::string> layerByOrdinal;
        std::map<std::string, std::string> udas;
        // the label itself holds identity and attributes; layers come from the label's own solid and from
        // the solids filed under it (sub-shape labels)
        auto collect = [&](const TDF_Label& l, bool own, int rank) {
            Handle(TDataStd_NamedData) nd = namedData(l, false);
            if (nd.IsNull() || !nd->HasStrings()) {
                return;
            }
            for (NCollection_DataMap<TCollection_ExtendedString, TCollection_ExtendedString>::Iterator it(nd->GetStringsContainer());
                 it.More();
                 it.Next()) {
                std::string key = toUtf8(it.Key());
                if (own && key.rfind(kIdPrefix, 0) == 0) {
                    attributes.emplace(key.substr(std::strlen(kIdPrefix)), toUtf8(it.Value()));
                }
                else if (own && key.rfind(kAttrPrefix, 0) == 0) {
                    udas.emplace(key.substr(std::strlen(kAttrPrefix)), toUtf8(it.Value()));
                }
                else if (key.rfind(kLayerPrefix, 0) == 0) {
                    layerByOrdinal.emplace(ordinal(static_cast<size_t>(rank)) + ":" + key.substr(std::strlen(kLayerPrefix)), toUtf8(it.Value()));
                }
            }
        };
        collect(label, true, 0);
        int rank = 1;
        for (TDF_ChildIterator ci(label, false); includeSubShapes && ci.More(); ci.Next()) {
            collect(ci.Value(), false, rank++);
        }
        for (auto& kv : udas) {
            attributes.emplace(kv.first, kv.second);  // an identity key already present wins
        }
        // identity first: a user attribute named like an identity key cannot replace it
        if (!attributes.empty() && !obj->getPropertyByName("StepAttributes")) {
            auto* prop = static_cast<App::PropertyMap*>(
                obj->addDynamicProperty("App::PropertyMap", "StepAttributes", "STEP", "STEP part identity and attributes")
            );
            if (prop) {
                prop->setValues(attributes);
            }
        }
        if (!layerByOrdinal.empty() && !obj->getPropertyByName("StepLayers")) {
            auto* prop = static_cast<App::PropertyStringList*>(
                obj->addDynamicProperty("App::PropertyStringList", "StepLayers", "STEP", "STEP presentation layers of the solids")
            );
            if (prop) {
                std::vector<std::string> values;
                for (const auto& [k, v] : layerByOrdinal) {
                    if (std::find(values.begin(), values.end(), v) == values.end()) {
                        values.push_back(v);
                    }
                }
                prop->setValues(values);
            }
        }
    }
    catch (...) {
        // a property that cannot be made never stops an import
    }
}

void stashObjectStepData(const TDF_Label& label, App::DocumentObject* obj)
{
    try {
        if (!obj || label.IsNull() || !writeStepAttributesEnabled()) {
            return;
        }
        auto* attrs = dynamic_cast<App::PropertyMap*>(obj->getPropertyByName("StepAttributes"));
        auto* layers = dynamic_cast<App::PropertyStringList*>(obj->getPropertyByName("StepLayers"));
        bool hasAttrs = attrs && !attrs->getValues().empty();
        bool hasLayers = layers && !layers->getValues().empty();
        if (!hasAttrs && !hasLayers) {
            return;
        }
        Handle(TDataStd_NamedData) nd = namedData(label, true);
        // StepAttributes present: it is the identity too, so a missing key means an empty field
        nd->SetString(fromUtf8("meta:present"), fromUtf8("1"));
        if (hasAttrs) {
            for (const auto& [k, v] : attrs->getValues()) {
                bool identity = (k == "Part Number" || k == "Description" || k == "Revision");
                setString(nd, std::string(identity ? kIdPrefix : kAttrPrefix) + k, v);
            }
        }
        if (hasLayers) {
            size_t n = 0;
            for (const auto& l : layers->getValues()) {
                setString(nd, std::string(kLayerPrefix) + ordinal(n++), l);
            }
        }
    }
    catch (...) {
    }
}

int writeStepAttributes(const Handle(StepData_StepModel)& model,
                        const Handle(XSControl_WorkSession)& ws,
                        const Handle(TDocStd_Document)& hDoc)
{
    int written = 0;
    try {
        if (model.IsNull() || ws.IsNull() || hDoc.IsNull() || !writeStepAttributesEnabled()) {
            return 0;
        }
        Handle(XCAFDoc_ShapeTool) tool = XCAFDoc_DocumentTool::ShapeTool(hDoc->Main());
        Handle(Transfer_FinderProcess) fp = ws->TransferWriter()->FinderProcess();
        ProductLookup lookup(model);
        NCollection_Sequence<TDF_Label> shapes;
        tool->GetShapes(shapes);

        std::map<Standard_Transient*, Handle(StepBasic_ProductDefinition)> byName;
        std::map<std::string, std::vector<Handle(StepBasic_ProductDefinition)>> productsByName;
        for (int i = 1; i <= model->NbEntities(); ++i) {
            if (auto pd = Handle(StepBasic_ProductDefinition)::DownCast(model->Value(i))) {
                if (!pd->Formation().IsNull() && !pd->Formation()->OfProduct().IsNull()) {
                    productsByName[text(pd->Formation()->OfProduct()->Name())].push_back(pd);
                }
            }
        }
        std::map<std::string, std::vector<Handle(StepRepr_RepresentationItem)>> solidsOfLayer;
        std::vector<std::string> layerOrder;

        for (int i = 1; i <= shapes.Length(); ++i) {
            TDF_Label label = shapes.Value(i);
            Handle(TDataStd_NamedData) nd = namedData(label, false);
            if (nd.IsNull() || !nd->HasStrings()) {
                continue;
            }
            TopoDS_Shape shape = tool->GetShape(label).Located(TopLoc_Location());
            if (shape.IsNull()) {
                continue;
            }
            // the PRODUCT_DEFINITION of this label: for a part, through the solid the writer made of it; for an
            // assembly (whose solids belong to its parts) and as a fallback, by product name
            Handle(StepBasic_ProductDefinition) pd;
            std::vector<Handle(StepRepr_RepresentationItem)> solidItems;
            const bool assembly = tool->IsAssembly(label);
            for (TopExp_Explorer ex(shape, TopAbs_SOLID); ex.More() && !assembly; ex.Next()) {
                Handle(StepRepr_RepresentationItem) item = STEPConstruct::FindEntity(fp, ex.Current());
                if (!item.IsNull()) {
                    solidItems.push_back(item);
                    if (pd.IsNull()) {
                        pd = lookup.ofItem(item.get());
                    }
                }
            }
            if (pd.IsNull()) {
                auto named = productsByName.find(labelName(label));
                if (named != productsByName.end() && named->second.size() == 1) {
                    pd = named->second.front();
                }
                else if (named != productsByName.end() && assembly) {
                    // several products of that name: the assembly's component names must match
                    NCollection_Sequence<TDF_Label> comps;
                    tool->GetComponents(label, comps, false);
                    std::vector<std::string> want;
                    for (int c = 1; c <= comps.Length(); ++c) {
                        TDF_Label ref;
                        if (tool->GetReferredShape(comps.Value(c), ref)) {
                            want.push_back(labelName(ref));
                        }
                    }
                    std::sort(want.begin(), want.end());
                    int matches = 0;
                    for (const auto& cand : named->second) {
                        std::vector<std::string> have;
                        for (int k = 1; k <= model->NbEntities(); ++k) {
                            if (auto nauo = Handle(StepRepr_NextAssemblyUsageOccurrence)::DownCast(model->Value(k))) {
                                if (nauo->RelatingProductDefinition() == cand && !nauo->RelatedProductDefinition().IsNull()
                                    && !nauo->RelatedProductDefinition()->Formation().IsNull()) {
                                    have.push_back(text(nauo->RelatedProductDefinition()->Formation()->OfProduct()->Name()));
                                }
                            }
                        }
                        std::sort(have.begin(), have.end());
                        if (have == want) {
                            pd = cand;
                            ++matches;
                        }
                    }
                    if (matches != 1) {
                        pd.Nullify();
                    }
                }
            }
            if (pd.IsNull()) {
                Base::Console().warning("STEP attributes: could not find the product of '{}' in the written file; its attributes are not written\n", labelName(label));
                continue;
            }

            std::map<std::string, std::string> identity, udas;
            std::vector<std::string> layers;
            bool present = false;
            for (NCollection_DataMap<TCollection_ExtendedString, TCollection_ExtendedString>::Iterator it(nd->GetStringsContainer());
                 it.More();
                 it.Next()) {
                std::string key = toUtf8(it.Key());
                if (key == "meta:present") {
                    present = true;
                }
                else if (key.rfind(kIdPrefix, 0) == 0) {
                    identity[key.substr(std::strlen(kIdPrefix))] = toUtf8(it.Value());
                }
                else if (key.rfind(kAttrPrefix, 0) == 0) {
                    udas[key.substr(std::strlen(kAttrPrefix))] = toUtf8(it.Value());
                }
            }
            {
                std::map<std::string, std::string> ordered;
                for (NCollection_DataMap<TCollection_ExtendedString, TCollection_ExtendedString>::Iterator it(nd->GetStringsContainer());
                     it.More();
                     it.Next()) {
                    std::string key = toUtf8(it.Key());
                    if (key.rfind(kLayerPrefix, 0) == 0) {
                        ordered[key] = toUtf8(it.Value());
                    }
                }
                for (auto& kv : ordered) {
                    layers.push_back(kv.second);
                }
            }
            if (!present && identity.empty() && udas.empty() && layers.empty()) {
                continue;
            }

            if (present || !identity.empty()) {
                Handle(StepBasic_Product) product = pd->Formation()->OfProduct();
                auto pick = [&](const char* key) {
                    auto f = identity.find(key);
                    return f == identity.end() ? std::string() : f->second;
                };
                product->SetId(new TCollection_HAsciiString(pick("Part Number").c_str()));
                if (!pick("Part Number").empty() && pick("Part Number") == text(product->Name())) {
                    // the id carries the text, as in the file it may have come from; an id equal to the name is not read back
                    product->SetName(new TCollection_HAsciiString(""));
                }
                product->SetDescription(new TCollection_HAsciiString(pick("Description").c_str()));
                pd->Formation()->SetId(new TCollection_HAsciiString(pick("Revision").c_str()));
            }
            Handle(StepRepr_RepresentationContext) context = lookup.contextOfPd.count(pd.get())
                ? lookup.contextOfPd[pd.get()]
                : Handle(StepRepr_RepresentationContext)();
            for (const auto& [rawKey, rawValue] : udas) {
                bool cut = false;
                const std::string key = capForStep(rawKey, 2000, cut);
                const std::string value = capForStep(rawValue, 8000, cut);
                if (cut) {
                    Base::Console().warning("STEP attributes: the attribute '{}' is too long for a STEP record and is cut\n", key);
                }
                Handle(StepBasic_GeneralProperty) gp = new StepBasic_GeneralProperty;
                gp->Init(new TCollection_HAsciiString(""), new TCollection_HAsciiString(key.c_str()), false, HAscii());
                StepRepr_CharacterizedDefinition cd;
                cd.SetValue(pd);
                Handle(StepRepr_PropertyDefinition) def = new StepRepr_PropertyDefinition;
                def->Init(new TCollection_HAsciiString(key.c_str()),
                          true,
                          new TCollection_HAsciiString("user defined attribute"),
                          cd);
                Handle(StepRepr_DescriptiveRepresentationItem) item = new StepRepr_DescriptiveRepresentationItem;
                item->Init(new TCollection_HAsciiString(""), new TCollection_HAsciiString(value.c_str()));
                Handle(NCollection_HArray1<Handle(StepRepr_RepresentationItem)>) items =
                    new NCollection_HArray1<Handle(StepRepr_RepresentationItem)>(1, 1);
                items->SetValue(1, item);
                Handle(StepRepr_Representation) rep = new StepRepr_Representation;
                rep->Init(new TCollection_HAsciiString(""), items, context);
                StepRepr_RepresentedDefinition rd;
                rd.SetValue(def);
                Handle(StepRepr_PropertyDefinitionRepresentation) pdr = new StepRepr_PropertyDefinitionRepresentation;
                pdr->Init(rd, rep);
                Handle(StepBasic_GeneralPropertyAssociation) gpa = new StepBasic_GeneralPropertyAssociation;
                gpa->Init(new TCollection_HAsciiString(""), new TCollection_HAsciiString(""), gp, def);
                model->AddWithRefs(pdr);
                model->AddWithRefs(gpa);
            }
            for (const auto& l : layers) {
                auto& list = solidsOfLayer[l];
                if (list.empty()) {
                    layerOrder.push_back(l);
                }
                for (const auto& item : solidItems) {
                    list.push_back(item);
                }
            }
            ++written;
        }
        for (const auto& name : layerOrder) {
            const auto& items = solidsOfLayer[name];
            if (items.empty()) {
                continue;
            }
            Handle(NCollection_HArray1<StepVisual_LayeredItem>) arr =
                new NCollection_HArray1<StepVisual_LayeredItem>(1, static_cast<int>(items.size()));
            for (size_t k = 0; k < items.size(); ++k) {
                StepVisual_LayeredItem li;
                li.SetValue(items[k]);
                arr->SetValue(static_cast<int>(k) + 1, li);
            }
            Handle(StepVisual_PresentationLayerAssignment) pla = new StepVisual_PresentationLayerAssignment;
            pla->Init(new TCollection_HAsciiString(name.c_str()), new TCollection_HAsciiString(name.c_str()), arr);
            model->AddWithRefs(pla);
        }
    }
    catch (Standard_Failure& e) {
        Base::Console().warning("STEP attributes: writing stopped ({})\n", e.GetMessageString() ? e.GetMessageString() : "OCCT failure");
    }
    catch (...) {
    }
    return written;
}

}  // namespace Import
