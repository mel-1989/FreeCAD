// SPDX-License-Identifier: LGPL-2.1-or-later

/******************************************************************************
 *   "FCStd as folder": save a document as a directory of text files, one    *
 *   file per document part, so that it can live in git and diff cleanly.    *
 *   The reading side lives in Document::restore and Base::XMLReader.         *
 ******************************************************************************/

#include <algorithm>
#include <filesystem>
#include <fstream>
#include <cctype>
#include <cmath>
#include <cstdlib>
#include <iterator>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <system_error>
#include <vector>

#include <boost/algorithm/string.hpp>

#include <Base/Exception.h>
#include <Base/FileInfo.h>
#include <Base/Uuid.h>
#include <Base/Writer.h>

#include "Application.h"
#include "Property.h"
#include "PropertyFile.h"
#include "Document.h"

namespace fs = std::filesystem;

namespace
{

// Lists the files FreeCAD wrote on the last save, so a later save can remove exactly those
// that became stale and never anything else in the folder (a .git directory, a README...).
constexpr const char* manifestName = "FCStdFolder.txt";
constexpr const char* manifestHeader = "FCStdFolder 1";

std::string stripTrailingSlashes(std::string path)
{
    while (path.size() > 1 && (path.back() == '/' || path.back() == '\\')) {
        path.pop_back();
    }
    return path;
}

fs::path toPath(const std::string& utf8)
{
#ifdef FC_OS_WIN32
    return fs::u8path(utf8);
#else
    return fs::path(utf8);
#endif
}

std::string fromPath(const fs::path& path)
{
#ifdef FC_OS_WIN32
    return path.generic_u8string();
#else
    return path.generic_string();
#endif
}

// A manifest or Writer name must be a plain relative path: no absolute path, no empty, "." or ".."
// component (a component may merely contain dots: "notes..v2.txt" is fine).
bool isSafeRelative(const std::string& rel)
{
    if (rel.empty() || rel.front() == '/' || rel.find('\\') != std::string::npos
        || rel.find(':') != std::string::npos) {
        return false;
    }
    std::size_t start = 0;
    while (start <= rel.size()) {
        const std::size_t end = rel.find('/', start);
        const std::string part = rel.substr(start, end == std::string::npos ? end : end - start);
        if (part.empty() || part == "." || part == "..") {
            return false;
        }
        if (end == std::string::npos) {
            break;
        }
        start = end + 1;
    }
    return true;
}

bool hasDotComponent(const std::string& rel)
{
    return rel.front() == '.' || rel.find("/.") != std::string::npos;
}

// True when no existing component of target/rel is a symlink (a missing tail is fine) and the
// final one, if present, is a regular file.
bool pathHasNoSymlink(const fs::path& target, const std::string& rel, bool finalMustBeRegular)
{
    std::error_code ec;
    fs::path cur = target;
    if (fs::is_symlink(fs::symlink_status(cur, ec))) {
        return false;
    }
    const fs::path relPath = toPath(rel);
    for (auto it = relPath.begin(); it != relPath.end(); ++it) {
        cur /= *it;
        const auto st = fs::symlink_status(cur, ec);
        if (!fs::exists(st)) {
            return true;
        }
        if (fs::is_symlink(st)) {
            return false;
        }
        const bool last = std::next(it) == relPath.end();
        if (last ? (finalMustBeRegular && !fs::is_regular_file(st)) : !fs::is_directory(st)) {
            return false;
        }
    }
    return true;
}

// Folds case for the collision test (case-insensitive file systems such as APFS and NTFS)
std::string foldCase(const std::string& name)
{
    return boost::to_lower_copy(name);
}

bool sameContent(const fs::path& a, const fs::path& b)
{
    std::error_code ec;
    if (fs::file_size(a, ec) != fs::file_size(b, ec) || ec) {
        return false;
    }
    std::ifstream fa(a, std::ios::binary);
    std::ifstream fb(b, std::ios::binary);
    return fa && fb
        && std::equal(std::istreambuf_iterator<char>(fa),
                      std::istreambuf_iterator<char>(),
                      std::istreambuf_iterator<char>(fb));
}

std::set<std::string> readManifest(const fs::path& folder)
{
    std::set<std::string> files;
    std::ifstream in(folder / manifestName, std::ios::binary);
    std::string line;
    if (!in || !std::getline(in, line) || line != manifestHeader) {
        return files;
    }
    while (std::getline(in, line)) {
        if (isSafeRelative(line)) {
            files.insert(line);
        }
    }
    return files;
}

// OCCT prints a coordinate that is a rounding-error away from zero as "-0.00000000000000000"
// or "0.00000000000000000" depending on the sign of the noise, and the sign flips on every
// read-write cycle. Negative zero is zero, so the shape BREP files are written with zero. Only
// files FreeCAD wrote for shape properties are touched (never attachments), and in Document.xml
// only the numeric attributes of placement, vector and float elements.
bool normaliseBrepText(std::string& text)
{
    bool changed = false;
    std::string out;
    out.reserve(text.size());
    const std::size_t n = text.size();
    for (std::size_t i = 0; i < n; ++i) {
        if (text[i] == '-' && i + 2 < n && text[i + 1] == '0' && text[i + 2] == '.'
            && (i == 0 || text[i - 1] == ' ' || text[i - 1] == '\n')) {
            std::size_t j = i + 3;
            while (j < n && text[j] == '0') {
                ++j;
            }
            if (j == n || text[j] == ' ' || text[j] == '\n' || text[j] == '\r') {
                changed = true;
                continue;  // drop the '-'
            }
        }
        out.push_back(text[i]);
    }
    if (changed) {
        text.swap(out);
    }
    return changed;
}

// Property status bits that describe the runtime state, not the model: Touched (0) is set by
// restore itself and by edits, Busy (15) only while a signal is being delivered. Persisting them
// makes the first reopen of a saved folder change Document.xml (a restored property is touched).
// A Property element omits status when it is 0, a _Property element always writes it.
// Replaces the text of the status attribute in [from, close); returns whether it changed.
bool maskTransientStatus(std::string& text, std::size_t from, std::size_t& close, bool omitZero)
{
    const std::size_t at = text.find(" status=\"", from);
    if (at == std::string::npos || at >= close) {
        return false;
    }
    const std::size_t valueStart = at + 9;
    const std::size_t valueEnd = text.find('"', valueStart);
    if (valueEnd == std::string::npos || valueEnd > close) {
        return false;
    }
    unsigned long status = 0;
    try {
        status = std::stoul(text.substr(valueStart, valueEnd - valueStart));
    }
    catch (const std::exception&) {
        return false;
    }
    const unsigned long masked = status & ~((1UL << App::Property::Touched) | (1UL << App::Property::Busy));
    if (masked == status) {
        return false;
    }
    const std::size_t oldLen = valueEnd + 1 - at;
    const std::string replacement =
        (masked == 0 && omitZero) ? std::string() : " status=\"" + std::to_string(masked) + "\"";
    text.replace(at, oldLen, replacement);
    close = close + replacement.size() - oldLen;
    return true;
}

bool normaliseDocumentXml(std::string& text)
{
    static const char* const numericTags[] = {"PropertyPlacement", "PropertyVector", "Float"};
    bool changed = false;
    std::size_t pos = 0;
    while ((pos = text.find('<', pos)) != std::string::npos) {
        std::size_t nameEnd = pos + 1;
        while (nameEnd < text.size() && (std::isalnum(static_cast<unsigned char>(text[nameEnd]))
                                         || text[nameEnd] == '_')) {
            ++nameEnd;
        }
        const std::string name = text.substr(pos + 1, nameEnd - pos - 1);
        std::size_t close = text.find('>', nameEnd);
        if (close == std::string::npos) {
            break;
        }
        if (name == "Property" || name == "_Property") {
            changed |= maskTransientStatus(text, nameEnd, close, name == "Property");
        }
        if (std::find(std::begin(numericTags), std::end(numericTags), name)
            != std::end(numericTags)) {
            for (std::size_t q = text.find("=\"-0.", nameEnd); q != std::string::npos && q < close;
                 q = text.find("=\"-0.", q + 1)) {
                std::size_t j = q + 5;
                while (j < close && text[j] == '0') {
                    ++j;
                }
                if (text[j] == '"') {
                    text.erase(q + 2, 1);
                    changed = true;
                }
            }
        }
        pos = nameEnd;
    }
    return changed;
}

std::string readAll(const fs::path& file)
{
    std::ifstream in(file, std::ios::binary);
    return {std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>()};
}

void rewrite(const fs::path& file, const std::string& text)
{
    std::ofstream out(file, std::ios::binary | std::ios::trunc);
    out << text;
}

// Names of the BREP files that Document.xml references from <Part .../> shape properties
std::set<std::string> shapeFiles(const std::string& xml)
{
    std::set<std::string> names;
    std::size_t pos = 0;
    while ((pos = xml.find("<Part ", pos)) != std::string::npos) {
        const std::size_t close = xml.find('>', pos);
        const std::size_t f = xml.find(" file=\"", pos);
        if (close == std::string::npos) {
            break;
        }
        if (f != std::string::npos && f < close) {
            const std::size_t end = xml.find('"', f + 7);
            names.insert(xml.substr(f + 7, end - f - 7));
        }
        pos = close;
    }
    return names;
}

// True when two BREP texts have the same tokens and every numeric token differs by at most tol
bool brepTextClose(const std::string& a, const std::string& b, double tol)
{
    std::size_t i = 0;
    std::size_t j = 0;
    auto next = [](const std::string& t, std::size_t& pos, std::string& tok) {
        while (pos < t.size() && std::isspace(static_cast<unsigned char>(t[pos]))) {
            ++pos;
        }
        const std::size_t start = pos;
        while (pos < t.size() && !std::isspace(static_cast<unsigned char>(t[pos]))) {
            ++pos;
        }
        tok.assign(t, start, pos - start);
        return start != pos;
    };
    std::string x;
    std::string y;
    while (true) {
        const bool hx = next(a, i, x);
        const bool hy = next(b, j, y);
        if (hx != hy) {
            return false;
        }
        if (!hx) {
            return true;
        }
        if (x == y) {
            continue;
        }
        // TShape flag words: free, modified and checked (the first three) are OCCT bookkeeping;
        // orientable, closed, infinite and convex describe the shape and must match
        auto isFlags = [](const std::string& t) {
            return t.size() == 7 && t.find_first_not_of("01") == std::string::npos;
        };
        if (isFlags(x) && isFlags(y)) {
            if (x.compare(3, 4, y, 3, 4) != 0) {
                return false;
            }
            continue;
        }
        char* endX = nullptr;
        char* endY = nullptr;
        const double dx = std::strtod(x.c_str(), &endX);
        const double dy = std::strtod(y.c_str(), &endY);
        if (*endX != '\0' || *endY != '\0' || std::fabs(dx - dy) > tol) {
            return false;
        }
    }
}

// Splits Document.xml into literal segments and the numbers held in whole attribute values of the
// numeric elements (PropertyPlacement, PropertyVector, Float). Everything else, strings, labels and
// spreadsheet cells included, stays literal, so only those numbers are compared with a tolerance.
void splitNumbers(const std::string& t, std::vector<std::string>& segments, std::vector<double>& nums)
{
    static const char* const numericTags[] = {"PropertyPlacement", "PropertyVector", "Float"};
    std::string seg;
    std::size_t i = 0;
    std::size_t pos = 0;
    while ((pos = t.find('<', pos)) != std::string::npos) {
        std::size_t nameEnd = pos + 1;
        while (nameEnd < t.size()
               && (std::isalnum(static_cast<unsigned char>(t[nameEnd])) || t[nameEnd] == '_')) {
            ++nameEnd;
        }
        const std::size_t close = t.find('>', nameEnd);
        if (close == std::string::npos) {
            break;
        }
        const std::string name = t.substr(pos + 1, nameEnd - pos - 1);
        if (std::find(std::begin(numericTags), std::end(numericTags), name) != std::end(numericTags)) {
            for (std::size_t q = t.find("=\"", nameEnd); q != std::string::npos && q < close;
                 q = t.find("=\"", q + 1)) {
                const std::size_t start = q + 2;
                const std::size_t end = t.find('"', start);
                if (end == std::string::npos || end > close || end == start) {
                    break;
                }
                const std::string value = t.substr(start, end - start);
                char* stop = nullptr;
                const double v = std::strtod(value.c_str(), &stop);
                if (*stop != '\0' || value.find('.') == std::string::npos
                    || value.find_first_not_of("-0123456789.eE+") != std::string::npos) {
                    continue;
                }
                seg.append(t, i, start - i);
                segments.push_back(seg);
                seg.clear();
                nums.push_back(v);
                i = end;
            }
        }
        pos = close;
    }
    seg.append(t, i, std::string::npos);
    segments.push_back(seg);
}

// Same text apart from the numeric attribute values of numeric elements, which may differ by at most tol
bool textClose(const std::string& a, const std::string& b, double tol)
{
    std::vector<std::string> sa;
    std::vector<std::string> sb;
    std::vector<double> na;
    std::vector<double> nb;
    splitNumbers(a, sa, na);
    splitNumbers(b, sb, nb);
    if (sa != sb || na.size() != nb.size()) {
        return false;
    }
    for (std::size_t k = 0; k < na.size(); ++k) {
        if (std::fabs(na[k] - nb[k]) > tol) {
            return false;
        }
    }
    return true;
}

// OCCT rebuilds rotation matrices (TopLoc locations) on every read, so a shape that was just
// loaded and is saved again differs from its file in the last digits. If the file already in the
// folder holds the same shape to within 1e-12 (far below any modelling tolerance, OCCT's
// Precision::Confusion is 1e-7), keep its text: reopen and save then reaches a fixed point.
void reuseUnchangedShapes(const fs::path& staging,
                          const fs::path& target,
                          const std::vector<std::string>& files,
                          const std::set<std::string>& shapes)
{
    for (const auto& f : files) {
        if (!shapes.contains(f) || !pathHasNoSymlink(target, f, true)) {
            continue;
        }
        std::error_code ec;
        const fs::path old = target / toPath(f);
        if (!fs::is_regular_file(old, ec)) {
            continue;
        }
        const std::string before = readAll(old);
        const std::string now = readAll(staging / toPath(f));
        if (before != now && brepTextClose(before, now, 1e-12)) {
            rewrite(staging / toPath(f), before);
        }
    }
}

void normaliseStaging(const fs::path& staging,
                      const fs::path& target,
                      const std::vector<std::string>& files)
{
    std::string xml = readAll(staging / "Document.xml");
    const std::set<std::string> shapes = shapeFiles(xml);
    for (const auto& f : files) {
        if (shapes.contains(f)) {
            std::string text = readAll(staging / toPath(f));
            if (normaliseBrepText(text)) {
                rewrite(staging / toPath(f), text);
            }
        }
    }
    reuseUnchangedShapes(staging, target, files, shapes);
    if (normaliseDocumentXml(xml)) {
        rewrite(staging / "Document.xml", xml);
    }
    // A document just reloaded renormalises its placement quaternions, which moves their last
    // digits; the same 1e-12 rule keeps the stored text when nothing else changed.
    std::error_code ec;
    const fs::path oldXml = target / "Document.xml";
    if (pathHasNoSymlink(target, "Document.xml", true) && fs::is_regular_file(oldXml, ec)) {
        const std::string before = readAll(oldXml);
        const std::string now = readAll(staging / "Document.xml");
        if (before != now && textClose(before, now, 1e-12)) {
            rewrite(staging / "Document.xml", before);
        }
    }
}

// FileWriter that also reports the names of the files it was asked to write. On a case-insensitive
// file system two names differing only in case are one file in the staging directory, so the
// collision has to be seen in this list, not in a directory listing.
class RecordingWriter: public Base::FileWriter
{
public:
    explicit RecordingWriter(const char* dir)
        : Base::FileWriter(dir)
    {
        // Names the folder format owns. An attachment (PropertyFileIncluded) with one of them is
        // given a unique name by the Writer, which the property stores in Document.xml and the
        // reader resolves, so the attachment survives under e.g. "Document1.xml".
        for (const char* reserved : {"Document.xml", "FCStdFolder.txt", ".backup"}) {
            FileNameManager.addExactName(reserved);
        }
    }

    /// True when an attachment holds the fixed name the GUI document is read back from
    bool attachmentTakesGuiName() const
    {
        for (const auto& entry : FileList) {
            if (entry.FileName == "GuiDocument.xml"
                && dynamic_cast<const App::PropertyFileIncluded*>(entry.Object)) {
                return true;
            }
        }
        return false;
    }

    std::vector<std::string> requestedNames() const
    {
        std::vector<std::string> names;
        for (const auto& entry : FileList) {
            names.push_back(entry.FileName);
        }
        return names;
    }
};

// Thrown when an undo move failed: the staging directory then holds the only copy of some old
// files and must stay on disk.
class RollbackFailed: public Base::FileException
{
public:
    using Base::FileException::FileException;
};

// One file move done by commitFolder, so a failure can be undone
struct Move
{
    fs::path from;
    fs::path to;
};

// Moves staged files into the target such that a failure leaves the previous saved state in
// place: everything that is replaced or removed is first moved into backup, new files are moved
// in, and on any error all moves are undone in reverse order. Nothing is deleted before the new
// state is complete.
void commitFolder(const fs::path& staging,
                  const fs::path& target,
                  const std::vector<std::string>& files,
                  const std::set<std::string>& previous)
{
    std::error_code ec;
    const fs::path backup = staging / ".backup";
    std::set<std::string> current(files.begin(), files.end());

    std::vector<std::string> toBackup;   // existing files that get replaced or removed
    std::vector<std::string> toPlace;    // staged files that differ from what is there
    std::set<std::string> staleFolded;   // case-folded names of stale files that get removed
    // Files an earlier save wrote and this one does not: only plain regular files below
    // non-hidden directories, never through a symlink and never outside the target. A stale name
    // that differs from a new one only in case goes too, before the new file takes its place on a
    // case-insensitive file system.
    for (const auto& p : previous) {
        if (current.contains(p) || hasDotComponent(p) || !isSafeRelative(p)
            || !pathHasNoSymlink(target, p, true)) {
            continue;
        }
        std::error_code e2;
        const fs::path canonicalParent = fs::weakly_canonical((target / toPath(p)).parent_path(), e2);
        const fs::path canonicalTarget = fs::weakly_canonical(target, e2);
        if (e2 || canonicalParent.string().rfind(canonicalTarget.string(), 0) != 0) {
            continue;
        }
        if (fs::is_regular_file(target / toPath(p), ec)) {
            toBackup.push_back(p);
            staleFolded.insert(foldCase(p));
        }
    }
    for (const auto& f : files) {
        if (!pathHasNoSymlink(target, f, true)) {
            throw Base::FileException("Refusing to write through a symbolic link or over a "
                                      "directory in a folder document", f.c_str());
        }
        const fs::path dst = target / toPath(f);
        if (fs::is_regular_file(dst, ec)) {
            if (!staleFolded.contains(foldCase(f)) && sameContent(staging / toPath(f), dst)) {
                continue;
            }
            toBackup.push_back(f);
        }
        toPlace.push_back(f);
    }

    std::vector<Move> done;
    std::vector<fs::path> created;
    // Test hook: FREECAD_FOLDER_TEST_FAULT=undo fails the commit after the new files are in
    // place and makes every undo move fail, to check that nothing is deleted then.
    const char* faultEnv = std::getenv("FREECAD_FOLDER_TEST_FAULT");
    const bool faultUndo = faultEnv && std::string(faultEnv) == "undo";
    auto undo = [&]() {
        std::size_t failed = 0;
        for (auto it = created.rbegin(); it != created.rend(); ++it) {
            std::error_code e3;
            if (faultUndo) {
                ++failed;
            }
            else {
                fs::remove(*it, e3);
                failed += e3 ? 1 : 0;
            }
        }
        for (auto it = done.rbegin(); it != done.rend(); ++it) {
            std::error_code e3;
            if (faultUndo) {
                e3 = std::make_error_code(std::errc::io_error);
            }
            else {
                fs::rename(it->to, it->from, e3);
            }
            failed += e3 ? 1 : 0;
        }
        if (failed) {
            throw RollbackFailed("The folder save failed and could not be fully undone ("
                                     + std::to_string(failed)
                                     + " moves). The previous files are kept in '"
                                     + fromPath(backup) + "'; copy them back by hand",
                                 fromPath(staging).c_str());
        }
    };
    try {
        for (const auto& f : toBackup) {
            const fs::path to = backup / toPath(f);
            fs::create_directories(to.parent_path());
            // A name that casefolds onto one already backed up stays where it is: on a
            // case-insensitive file system it is the same file
            if (fs::exists(to, ec)) {
                continue;
            }
            fs::rename(target / toPath(f), to);
            done.push_back({target / toPath(f), to});
        }
        // Document.xml and the manifest last: they are what makes the new state visible
        std::stable_partition(toPlace.begin(), toPlace.end(), [](const std::string& f) {
            return f != "Document.xml" && f != manifestName;
        });
        for (const auto& f : toPlace) {
            const fs::path dst = target / toPath(f);
            fs::create_directories(dst.parent_path());
            fs::rename(staging / toPath(f), dst);
            created.push_back(dst);
        }
        if (faultUndo) {
            throw Base::RuntimeError("injected fault after placing the new files");
        }
    }
    catch (...) {
        undo();
        throw;
    }
    // Success: the old copies are no longer needed. Remove emptied directories left by stale files.
    for (const auto& f : toBackup) {
        if (!current.contains(f)) {
            for (fs::path dir = (target / toPath(f)).parent_path(); dir != target;
                 dir = dir.parent_path()) {
                if (!fs::remove(dir, ec)) {
                    break;
                }
            }
        }
    }
}

}  // namespace

namespace App
{

bool Document::isFolderPath(const char* file)
{
    const std::string fn = stripTrailingSlashes(file ? file : "");
    if (boost::iends_with(fn, ".fcstd.d")) {
        return true;
    }
    std::error_code ec;
    const fs::path path = toPath(fn);
    return fs::is_directory(path, ec)
        && (fs::exists(path / "Document.xml", ec) || fs::exists(path / manifestName, ec));
}

bool Document::saveAsFolder(const char* dir)
{
    const std::string folder = stripTrailingSlashes(dir);
    std::string label = toPath(folder).filename().string();
    if (boost::iends_with(label, ".d")) {
        label.erase(label.size() - 2);
    }
    if (boost::iends_with(label, ".fcstd")) {
        label.erase(label.size() - 6);
    }
    const std::string oldName = FileName.getStrValue();
    const std::string oldLabel = Label.getStrValue();
    if (oldName != folder) {
        FileName.setValue(folder);
        Label.setValue(label);
        Uid.touch();  // forces a rename of the transient directory, as in saveAs
    }
    try {
        // forced folder mode, whatever the name
        if (save(true)) {
            return true;
        }
    }
    catch (...) {
        FileName.setValue(oldName);
        Label.setValue(oldLabel);
        throw;
    }
    FileName.setValue(oldName);
    Label.setValue(oldLabel);
    return false;
}

bool Document::saveToFolder(const char* dirname) const
{
    signalStartSave(*this, dirname);

    std::error_code ec;
    fs::path target = fs::weakly_canonical(fs::absolute(toPath(stripTrailingSlashes(dirname))), ec);
    if (ec) {
        target = fs::absolute(toPath(stripTrailingSlashes(dirname)));
    }
    if (fs::exists(target, ec) && !fs::is_directory(target, ec)) {
        throw Base::FileException("Cannot save a folder document: the path is a file", dirname);
    }
    fs::create_directories(target.parent_path());

    // Everything is written to a staging directory next to the target first. A failure here
    // leaves the target untouched; commitFolder then swaps the new state in and can undo itself.
    const fs::path staging =
        target.parent_path() / (target.filename().string() + ".saving-" + Base::Uuid::createUuid());
    fs::create_directories(staging);

    try {
        const std::string stagingName = fromPath(staging);
        std::vector<std::string> requested;
        {
            RecordingWriter writer(stagingName.c_str());
            // Always text BREP in folder mode, whatever the SaveBinaryBrep preference says
            writer.putNextEntry("Document.xml");
            writer.Stream() << "<?xml version='1.0' encoding='utf-8'?>" << '\n'
                            << "<!--" << '\n'
                            << " FreeCAD Document, see https://www.freecad.org for more "
                               "information..."
                            << '\n'
                            << "-->" << '\n';
            Document::Save(writer);
            signalSaveDocument(writer);
            writer.writeFiles();
            writer.close();
            if (writer.attachmentTakesGuiName()) {
                throw Base::FileException(
                    "An attached file is named GuiDocument.xml, which a folder document reserves "
                    "for the GUI state; rename the attachment",
                    dirname);
            }
            requested = writer.requestedNames();
            requested.emplace_back("Document.xml");
            if (writer.hasErrors()) {
                std::stringstream message;
                message << "Failed to write all data to folder " << writer.getErrors().front();
                throw Base::FileException(message.str().c_str(), dirname);
            }
        }
        GetApplication().signalSaveDocument(*this);

        std::vector<std::string> files;
        for (const auto& entry : fs::recursive_directory_iterator(staging)) {
            if (entry.is_regular_file()) {
                files.push_back(fromPath(fs::relative(entry.path(), staging)));
            }
        }
        std::sort(files.begin(), files.end());

        // Two names that differ only in case would be one file on APFS or NTFS and one object's
        // data would silently replace the other's: refuse (objects "Box" and "box")
        std::map<std::string, std::string> folded;
        for (const auto& f : requested) {
            const auto [it, fresh] = folded.emplace(foldCase(f), f);
            if (!fresh) {
                throw Base::FileException(
                    "Folder documents need file names that differ by more than case, but '"
                        + it->second + "' and '" + f
                        + "' would be one file on a case-insensitive file system "
                          "(rename one of the objects)",
                    dirname);
            }
        }
        for (const auto& f : files) {
            if (!isSafeRelative(f)) {
                throw Base::FileException("Unsafe file name in folder document", f.c_str());
            }
        }

        normaliseStaging(staging, target, files);
        files.emplace_back(manifestName);
        {
            std::ofstream manifest(staging / manifestName, std::ios::binary);
            manifest << manifestHeader << '\n';
            for (const auto& f : files) {
                if (f != manifestName) {
                    manifest << f << '\n';
                }
            }
        }
        std::sort(files.begin(), files.end());

        fs::create_directories(target);
        commitFolder(staging, target, files, readManifest(target));
    }
    catch (const RollbackFailed&) {
        throw;  // keep the staging directory: it holds the backup of the previous state
    }
    catch (...) {
        fs::remove_all(staging, ec);
        throw;
    }
    fs::remove_all(staging, ec);

    signalFinishSave(*this, dirname);
    return true;
}

}  // namespace App
