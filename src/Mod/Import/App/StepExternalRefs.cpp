// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   External file references of STEP assemblies (G13 slice 1c).           *
 *   See StepExternalRefs.h for the public source.                         *
 ***************************************************************************/

#include <algorithm>
#include <cctype>
#include <filesystem>
#include <fstream>
#include <map>
#include <set>

#include <STEPControl_Reader.hxx>
#include <StepAP214_AppliedDocumentReference.hxx>
#include <StepAP214_AppliedExternalIdentificationAssignment.hxx>
#include <StepAP214_DocumentReferenceItem.hxx>
#include <StepAP214_ExternalIdentificationItem.hxx>
#include <StepBasic_DocumentFile.hxx>
#include <StepBasic_ExternalSource.hxx>
#include <StepBasic_SourceItem.hxx>
#include <StepData_SelectMember.hxx>
#include <STEPCAFControl_ExternFile.hxx>
#include <TCollection_HAsciiString.hxx>

#include <Base/Console.h>
#include <Mod/Part/App/encodeFilename.h>

#include "StepExternalRefs.h"

namespace Import
{

namespace
{
namespace fs = std::filesystem;

const char* const kSkipped = "<reference skipped: file cycle>";
constexpr int kMaxFiles = 5000;

struct Ref
{
    std::string name;    // what the file calls the target file
    std::string source;  // external_source.source_id, the folder the practice puts the file in ("" = same folder)
    Handle(StepBasic_DocumentFile) document;
    Handle(StepAP214_AppliedExternalIdentificationAssignment) assignment;
};

fs::path pathOf(const std::string& s)
{
    return fs::path(std::u8string(reinterpret_cast<const char8_t*>(s.data()), s.size()));
}

std::string text(const Handle(TCollection_HAsciiString)& h)
{
    return h.IsNull() ? std::string() : std::string(h->ToCString());
}

std::vector<Ref> refsOf(const Handle(StepData_StepModel)& model)
{
    std::vector<Ref> out;
    std::map<Standard_Transient*, size_t> index;
    for (int i = 1; i <= model->NbEntities(); ++i) {
        if (auto ref = Handle(StepAP214_AppliedDocumentReference)::DownCast(model->Value(i))) {
            Handle(StepBasic_DocumentFile) df = Handle(StepBasic_DocumentFile)::DownCast(ref->AssignedDocument());
            if (df.IsNull() || ref->Items().IsNull()) {
                continue;
            }
            bool ofProduct = false;
            for (int k = 1; k <= ref->NbItems(); ++k) {
                ofProduct = ofProduct || !ref->ItemsValue(k).ProductDefinition().IsNull();
            }
            if (ofProduct && !index.count(df.get())) {
                index[df.get()] = out.size();
                Ref r;
                r.document = df;
                r.name = text(df->Id());
                out.push_back(std::move(r));
            }
        }
    }
    for (int i = 1; i <= model->NbEntities(); ++i) {
        auto a = Handle(StepAP214_AppliedExternalIdentificationAssignment)::DownCast(model->Value(i));
        if (a.IsNull() || a->Items().IsNull()) {
            continue;
        }
        for (int k = a->Items()->Lower(); k <= a->Items()->Upper(); ++k) {
            Handle(StepBasic_DocumentFile) df = a->Items()->Value(k).DocumentFile();
            auto at = df.IsNull() ? index.end() : index.find(df.get());
            if (at == index.end()) {
                continue;
            }
            Ref& r = out[at->second];
            r.assignment = a;
            if (!text(a->AssignedId()).empty()) {
                r.name = text(a->AssignedId());
            }
            if (!a->Source().IsNull()) {
                // the source id is a select; a plain string arrives as a named select member
                Handle(StepData_SelectMember) member = Handle(StepData_SelectMember)::DownCast(a->Source()->SourceId().Value());
                if (!member.IsNull() && member->String()) {
                    r.source = member->String();
                }
                else if (!a->Source()->SourceId().Identifier().IsNull()) {
                    r.source = text(a->Source()->SourceId().Identifier());
                }
            }
        }
    }
    return out;
}

bool mentionsDocumentFile(const fs::path& path)
{
    std::ifstream in(path, std::ios::binary);
    if (!in) {
        return false;
    }
    static const std::string needle = "DOCUMENT_FILE";
    std::string carry;
    std::vector<char> buf(1 << 20);
    while (in) {
        in.read(buf.data(), static_cast<std::streamsize>(buf.size()));
        std::string chunk = carry + std::string(buf.data(), static_cast<size_t>(in.gcount()));
        std::string upper = chunk;
        for (auto& c : upper) {
            c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
        }
        if (upper.find(needle) != std::string::npos) {
            return true;
        }
        carry = chunk.size() >= needle.size() ? chunk.substr(chunk.size() - needle.size() + 1) : chunk;
    }
    return false;
}

/// The references of another file; read while the reader that owns the model is alive (its destructor empties the model)
std::vector<Ref> readRefs(const fs::path& path)
{
    try {
        STEPControl_Reader reader;
        Handle(StepData_StepModel) m = new StepData_StepModel;
        m->InternalParameters.InitFromStatic();
        m->SetSourceCodePage(Resource_FormatType_UTF8);
        std::string name8bit = Part::encodeFilename(path.string());
        if (reader.ReadFile(name8bit.c_str(), m->InternalParameters) != IFSelect_RetDone || reader.StepModel().IsNull()) {
            return {};
        }
        return refsOf(reader.StepModel());
    }
    catch (...) {
        return {};
    }
}

fs::path normalized(const fs::path& p)
{
    std::error_code ec;
    fs::path c = fs::weakly_canonical(p, ec);
    return ec ? p.lexically_normal() : c;
}

bool hasParentComponent(const fs::path& p)
{
    return std::any_of(p.begin(), p.end(), [](const fs::path& c) { return c == ".."; });
}

class Walker
{
public:
    Walker(const fs::path& top, StepExternalReport& report, const std::string& topLabel)
        : top(normalized(top))
        , report(report)
    {
        label[this->top.string()] = topLabel;
    }

    /// target of a reference in 'from'
    fs::path target(const fs::path& from, const Ref& r) const
    {
        fs::path name = pathOf(r.name);
        return normalized(name.is_absolute() ? name : from.parent_path() / name);
    }

    bool outside(const Ref& r) const
    {
        fs::path name = pathOf(r.name);
        fs::path source = pathOf(r.source);
        return name.is_absolute() || hasParentComponent(name.lexically_normal())
            || (!r.source.empty() && (source.is_absolute() || hasParentComponent(source.lexically_normal())));
    }

    void warn(const std::string& key, const std::string& message)
    {
        if (warned.insert(key).second) {
            report.warnings.push_back(message);
        }
    }

    /// One reference of 'node'; true when following it reaches a cycle (the node itself counts as grey).
    bool edge(const fs::path& node, const Ref& r)
    {
        if (r.name.empty() || r.name == kSkipped) {
            return false;
        }
        const std::string key = node.string();
        fs::path next = target(node, r);
        const std::string nkey = next.string();
        std::error_code ec;
        const bool isFolder = fs::is_directory(next, ec);
        const bool exists = fs::exists(next, ec) && !isFolder;
        if (outside(r)) {
            warn("out|" + key + "|" + r.name + "|" + r.source,
                 "STEP external reference: '" + r.name + (r.source.empty() ? "" : "' in '" + r.source) + "' referenced by '"
                     + node.filename().string() + "' lies outside its folder"
                     + (exists ? " (it is "
                               + std::string(pathOf(r.name).is_absolute() || hasParentComponent(pathOf(r.name).lexically_normal())
                                                 ? "loaded from the path as written"
                                                 : "looked for beside the importing file, the folder named is not used")
                               + ")"
                               : (isFolder ? " (it is a folder, not a file)" : " (not found)")));
        }
        if (!exists) {
            warn("miss|" + nkey,
                 "STEP external reference: file '" + r.name + "' referenced by '" + node.filename().string()
                     + (isFolder ? "' is a folder, not a file" : "' was not found"));
            return false;
        }
        report.referencedFiles += seen.insert(nkey).second ? 1 : 0;
        if (nkey == key) {
            warn("self|" + key,
                 "STEP external reference: '" + node.filename().string() + "' refers to itself; the reference is skipped");
            return false;
        }
        if (color[nkey] == 1) {
            warn("cycle|" + nkey,
                 "STEP external reference: '" + r.name + "' referenced by '" + node.filename().string()
                     + "' leads back into a cycle of files; the reference is not loaded");
            return true;
        }
        if (color[nkey] == 2) {
            return cyclic[nkey];
        }
        if (static_cast<int>(color.size()) > kMaxFiles) {
            warn("limit", "STEP external reference: more than 5000 files in the reference graph; the rest is not checked");
            return false;
        }
        std::vector<Ref> nested;
        if (mentionsDocumentFile(next)) {
            nested = readRefs(next);
        }
        return visit(next, nested);
    }

    /// depth first; true when something reachable from 'node' lies on a cycle
    bool visit(const fs::path& node, const std::vector<Ref>& refs)
    {
        const std::string key = node.string();
        color[key] = 1;
        bool reaches = false;
        for (const Ref& r : refs) {
            reaches = edge(node, r) || reaches;
        }
        color[key] = 2;
        cyclic[key] = reaches;
        return reaches;
    }

    bool cyclicChild(const fs::path& node) const
    {
        auto it = cyclic.find(node.string());
        return it != cyclic.end() && it->second;
    }

    fs::path top;
    StepExternalReport& report;
    std::map<std::string, int> color;
    std::map<std::string, bool> cyclic;
    std::map<std::string, std::string> label;
    std::set<std::string> warned;
    std::set<std::string> seen;
};
}  // namespace

StepExternalReport prepareStepExternalReferences(const Handle(StepData_StepModel)& model, const Base::FileInfo& file)
{
    StepExternalReport report;
    try {
        if (model.IsNull()) {
            return report;
        }
        std::vector<Ref> refs = refsOf(model);
        if (refs.empty()) {
            return report;
        }
        fs::path top = pathOf(file.filePath());
        Walker walker(top, report, top.filename().string());
        // the top file stays grey while its references are walked, so a file that leads back to it is a cycle
        walker.color[walker.top.string()] = 1;
        for (const Ref& r : refs) {
            if (walker.edge(walker.top, r)) {
                // the file this reference names reaches a cycle: OCCT would recurse for ever, so it is not loaded
                if (!r.document.IsNull()) {
                    r.document->SetId(new TCollection_HAsciiString(kSkipped));
                }
                if (!r.assignment.IsNull()) {
                    r.assignment->SetAssignedId(new TCollection_HAsciiString(kSkipped));
                }
                report.warnings.push_back("STEP external reference: '" + r.name
                                          + "' is not loaded because the files it refers to lead back into a cycle");
                ++report.cutEdges;
            }
        }
    }
    catch (...) {
        // a reference check never stops an import
    }
    return report;
}

void finishStepExternalReferences(const STEPCAFControl_Reader& reader, const Base::FileInfo& file, StepExternalReport& report)
{
    try {
        fs::path folder = pathOf(file.filePath()).parent_path();
        for (NCollection_DataMap<TCollection_AsciiString, Handle(STEPCAFControl_ExternFile)>::Iterator it(reader.ExternFiles());
             it.More();
             it.Next()) {
            Handle(STEPCAFControl_ExternFile) ef = it.Value();
            std::string name = it.Key().ToCString();
            if (ef.IsNull() || name == kSkipped || ef->GetLoadStatus() == IFSelect_RetDone) {
                continue;
            }
            std::error_code ec;
            fs::path p = pathOf(name);
            fs::path full = normalized(p.is_absolute() ? p : folder / p);
            if (fs::exists(full, ec) && !fs::is_directory(full, ec)) {
                report.warnings.push_back("STEP external reference: file '" + name + "' exists but could not be read");
            }
        }
    }
    catch (...) {
    }
}

void reportStepExternalReferences(const StepExternalReport& report)
{
    for (const auto& w : report.warnings) {
        Base::Console().warning("{}\n", w);
    }
}

}  // namespace Import
