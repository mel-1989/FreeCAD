// SPDX-License-Identifier: LGPL-2.1-or-later

/******************************************************************************
 *   "FCStd as folder": save a document as a directory of text files, one    *
 *   file per document part, so that it can live in git and diff cleanly.    *
 *   The reading side lives in Document::restore and Base::XMLReader.         *
 ******************************************************************************/

#include <algorithm>
#include <filesystem>
#include <fstream>
#include <iterator>
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

bool isSafeRelative(const std::string& rel)
{
    if (rel.empty() || rel.front() == '/' || rel.find('\\') != std::string::npos
        || rel.find(':') != std::string::npos) {
        return false;
    }
    for (const auto& part : toPath(rel)) {
        if (part == ".." || part == ".") {
            return false;
        }
    }
    return true;
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
// read-write cycle. Negative zero is zero, so write it as zero: the text then reaches a fixed
// point after one save instead of flapping in a diff.
bool normaliseNegativeZeros(std::string& text, bool xml)
{
    bool changed = false;
    std::string out;
    out.reserve(text.size());
    const std::size_t n = text.size();
    bool stringLine = false;  // XML: leave the text of <String .../> values alone
    for (std::size_t i = 0; i < n; ++i) {
        if (xml && (i == 0 || text[i - 1] == '\n')) {
            const std::size_t first = text.find_first_not_of(' ', i);
            stringLine = first != std::string::npos && text.compare(first, 7, "<String") == 0;
        }
        if (!stringLine && text[i] == '-' && i + 2 < n && text[i + 1] == '0' && text[i + 2] == '.'
            && (i == 0 || text[i - 1] == ' ' || text[i - 1] == '\n'
                || (xml && text[i - 1] == '"'))) {
            std::size_t j = i + 3;
            while (j < n && text[j] == '0') {
                ++j;
            }
            if (j == n || text[j] == ' ' || text[j] == '\n' || text[j] == '\r'
                || (xml && text[j] == '"')) {
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

void normaliseFile(const fs::path& file, bool xml)
{
    std::string text;
    {
        std::ifstream in(file, std::ios::binary);
        text.assign(std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>());
    }
    if (normaliseNegativeZeros(text, xml)) {
        std::ofstream out(file, std::ios::binary | std::ios::trunc);
        out << text;
    }
}

void moveIntoPlace(const fs::path& from, const fs::path& to)
{
    std::error_code ec;
    if (fs::is_regular_file(to, ec) && sameContent(from, to)) {
        return;  // leave unchanged files (and their mtime) alone
    }
    fs::create_directories(to.parent_path());
    fs::rename(from, to, ec);
    if (ec) {  // e.g. across devices
        fs::copy_file(from, to, fs::copy_options::overwrite_existing);
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
    if (FileName.getStrValue() != folder) {
        FileName.setValue(folder);
        Label.setValue(label);
        Uid.touch();  // forces a rename of the transient directory, as in saveAs
    }
    return save();
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
    // leaves the target untouched; only the final renames modify it, Document.xml last.
    const fs::path staging =
        target.parent_path() / (target.filename().string() + ".saving-" + Base::Uuid::createUuid());
    fs::create_directories(staging);

    try {
        const std::string stagingName = fromPath(staging);
        {
            Base::FileWriter writer(stagingName.c_str());
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
                const std::string leaf = entry.path().filename().string();
                if (boost::iends_with(leaf, ".brp")) {
                    normaliseFile(entry.path(), false);
                }
                else if (boost::iends_with(leaf, ".xml")) {
                    normaliseFile(entry.path(), true);
                }
                files.push_back(fromPath(fs::relative(entry.path(), staging)));
            }
        }
        std::sort(files.begin(), files.end());
        {
            std::ofstream manifest(staging / manifestName, std::ios::binary);
            manifest << manifestHeader << '\n';
            for (const auto& f : files) {
                manifest << f << '\n';
            }
        }

        fs::create_directories(target);
        const std::set<std::string> previous = readManifest(target);
        const std::set<std::string> current(files.begin(), files.end());

        for (const auto& f : files) {
            if (f != "Document.xml") {
                moveIntoPlace(staging / toPath(f), target / toPath(f));
            }
        }
        moveIntoPlace(staging / "Document.xml", target / "Document.xml");
        moveIntoPlace(staging / manifestName, target / manifestName);

        // Remove only what a previous save wrote and this one did not
        for (const auto& f : previous) {
            if (current.contains(f)) {
                continue;
            }
            fs::path stale = target / toPath(f);
            if (fs::is_regular_file(stale, ec)) {
                fs::remove(stale, ec);
            }
            for (fs::path dir = stale.parent_path(); dir != target; dir = dir.parent_path()) {
                if (!fs::remove(dir, ec)) {  // not empty (or gone): stop
                    break;
                }
            }
        }
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
