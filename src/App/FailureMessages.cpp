// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Readable recompute-failure messages (cad-fork G08a, slice 1).         *
 ***************************************************************************/

#include <string_view>

#include "FailureMessages.h"
#include "Document.h"
#include "DocumentObject.h"

namespace App
{

namespace
{

struct Entry
{
    const char* key;
    /// Substring of the object's TypeId name that must be present (nullptr: any type).
    const char* typeContains;
    /// Substring of the raw text that selects the entry.
    const char* rawContains;
    /// Message; "{label}" is the feature label, "{detail}" the first line of the raw text.
    const char* text;
};

// Order matters: the first matching entry wins, so type-specific entries precede general ones.
// Wording is original; behaviour is taken from the messages FreeCAD and its workbenches produce.
constexpr Entry catalogue[] = {
    // Geometry kernel failures
    {"fillet.radius", "Fillet", "BRep_API: command not done",
     "'{label}' could not round the selected edges: the radius is too large for them or the edges "
     "cannot be rounded together, so reduce the radius or select different edges."},
    {"chamfer.size", "Chamfer", "BRep_API: command not done",
     "'{label}' could not chamfer the selected edges: the chamfer size is too large for them, so "
     "reduce the size or select different edges."},
    {"kernel.not_done", nullptr, "BRep_API: command not done",
     "'{label}' could not be built: the operation does not work with its current inputs, so check "
     "that its input shapes overlap or touch as intended and change a dimension."},
    {"reference.lost", nullptr, "NCollection_IndexedMap::FindKey",
     "'{label}' refers to an edge, face or vertex that no longer exists, so select the geometry "
     "again."},
    {"reference.lost", nullptr, "does not belong to the shape",
     "'{label}' refers to an edge or face that is not part of its shape, so select the geometry "
     "again."},
    {"direction.zero", nullptr, "input vector has zero norm",
     "'{label}' uses a direction or axis of zero length, so set a non-zero axis."},
    {"face.from_wire", nullptr, "Failed to create face from wire",
     "'{label}' cannot make a face from its outline, so check that the outline is closed and not "
     "of zero size."},

    // Missing or wrong inputs (Part, PartDesign, Surface)
    {"input.missing", nullptr, "No object linked",
     "'{label}' has no source object, so link the sketch or object it should work on."},
    {"input.not_part", nullptr, "Linked object is not a Part object",
     "'{label}' is linked to something that has no solid shape, so link a Part object."},
    {"input.too_few", nullptr, "Not enough shape objects linked",
     "'{label}' needs more input shapes, so add the missing shapes."},
    {"input.empty", nullptr, "Linked shape object is empty",
     "'{label}' uses a sketch or object that is empty, so draw a closed profile in it or fix the "
     "object it links to."},
    {"input.null_shape", nullptr, "Base shape is null",
     "'{label}' has an empty base shape, so link a base object that produces a shape."},
    {"input.null_shape", nullptr, "Null input shape",
     "'{label}' received an empty shape, so check that the objects it uses produce a shape."},
    {"input.null_shape", nullptr, "Null shape",
     "'{label}' produced an empty shape, so reduce any offset, cut or thickness that consumes the "
     "whole shape."},
    {"input.null_shape", nullptr, "null shape",
     "'{label}' received an empty shape, so check that the objects it uses produce a shape."},
    {"size.too_small", nullptr, " too small",
     "'{label}' has a dimension of zero or almost zero, so enter a larger value."},
    {"profile.open", nullptr, "Wire is not closed",
     "The profile of '{label}' is not closed, so close the sketch outline into a loop."},
    {"profile.missing", nullptr, "No profile linked",
     "'{label}' has no profile, so select a sketch for it."},
    {"length.zero", nullptr, "total length of zero",
     "'{label}' has a length of zero, so enter a positive length."},
    {"axis.invalid", nullptr, "Reference axis is invalid",
     "The axis of '{label}' is not usable, so choose a sketch line or origin axis that lies in the "
     "profile plane and does not cross the profile."},
    {"edges.none", nullptr, "No edges specified",
     "'{label}' has no edges to work on, so select the edges."},
    {"edges.none", nullptr, "Fillet not possible on selected shapes",
     "'{label}' has no edges it can round, so select the edges."},
    {"sections.too_few", nullptr, "At least one section is needed",
     "'{label}' needs at least two sections, so add another profile."},
    {"sections.too_few", nullptr, "at least two vertices, edges or wires",
     "'{label}' needs at least two sections, so add another profile."},
    {"sections.too_few", nullptr, "At least two sections are required",
     "'{label}' needs at least two sections, so add another curve."},
    {"sweep.spine", nullptr, "No spine",
     "'{label}' has no path to follow, so set its path (spine)."},
    {"surface.border", nullptr, "Border must have at least one curve",
     "'{label}' has no boundary curves, so add at least one edge."},

    // Sketcher
    {"sketch.conflicting", nullptr, "conflicting constraints",
     "The sketch '{label}' has conflicting constraints, so open it and delete or relax one of the "
     "constraints the solver lists."},
    {"sketch.redundant", nullptr, "redundant constraints",
     "The sketch '{label}' has redundant constraints, so open it and delete the redundant "
     "constraint the solver lists."},
    {"sketch.unsolved", nullptr, "Solving the sketch failed",
     "The sketch '{label}' could not be solved, so open it and remove or relax a constraint that "
     "contradicts the others."},

    // Expressions
    {"expression.failed", nullptr, "in expression:",
     "An expression bound to '{label}' could not be evaluated ({detail}), so fix or remove the "
     "expression."},
    {"expression.failed", nullptr, "in property binding",
     "An expression bound to '{label}' could not be evaluated ({detail}), so fix or remove the "
     "expression."},

    // Weldment
    {"weld.profile", nullptr, "unknown profile",
     "'{label}' uses a profile family or size that is not in the profile tables, so pick a listed "
     "family and size."},
    {"weld.path", nullptr, "has an empty path",
     "'{label}' has no path, so link a sketch or straight edges for it to follow."},
    {"weld.edges", nullptr, "has no edges; link",
     "'{label}' has no edges to weld, so link the edges to weld."},
    {"weld.bodies", nullptr, "needs both bodies linked",
     "'{label}' needs two bodies, so link both bodies."},
    {"weld.corner", nullptr, "forms a free corner",
     "A weld edge of '{label}' is not a corner a fillet weld can fill, so pick an edge where faces "
     "of two different bodies meet at an open corner."},
    {"weld.not_member", nullptr, "has no attribute 'Family'",
     "'{label}' is linked to an object that is not a weldment member, so link a member."},

    // Sheet metal
    {"sheet.edge", nullptr, "not an edge of a sheet face",
     "The edge chosen for '{label}' is not along the end of the sheet, so pick an edge on an end "
     "face of the sheet."},
    {"sheet.line", nullptr, "exactly one straight line",
     "The sketch of '{label}' must hold exactly one straight line, so draw a single line in it."},

    // Draft
    {"draft.empty_base", nullptr, "cannot build shape from",
     "'{label}' cannot build its shape because the object it copies has no shape, so link an "
     "object that produces a shape."},

    // Python exceptions raised by a scripted feature (the traceback goes to the log)
    {"python.missing_attribute", nullptr, "object has no attribute",
     "'{label}' is missing something it needs, so check that all its links and properties are set."},
    {"python.wrong_type", nullptr, "unsupported operand type",
     "'{label}' received a value of the wrong type, so check its property values."},
    {"python.zero_division", nullptr, "division by zero",
     "'{label}' divides by zero, so check its dimensions for a zero value."},
};

std::string firstLine(const std::string& raw)
{
    std::string_view view {raw};
    // skip leading blank lines
    while (!view.empty() && (view.front() == '\n' || view.front() == '\r' || view.front() == ' ')) {
        view.remove_prefix(1);
    }
    const auto end = view.find_first_of("\r\n");
    std::string line {view.substr(0, end)};
    while (!line.empty() && (line.back() == ' ' || line.back() == '.' || line.back() == ';')) {
        line.pop_back();
    }
    constexpr std::size_t maxLength = 160;
    if (line.size() > maxLength) {
        // cut on a UTF-8 character boundary: never keep a lone lead or continuation byte
        std::size_t cut = maxLength;
        while (cut > 0 && (static_cast<unsigned char>(line[cut]) & 0xC0) == 0x80) {
            --cut;
        }
        line.resize(cut);
        line += "...";
    }
    return line;
}

std::string substitute(std::string text, const std::string& label, const std::string& detail)
{
    auto replaceAll = [&text](const std::string& token, const std::string& value) {
        for (auto pos = text.find(token); pos != std::string::npos; pos = text.find(token, pos)) {
            text.replace(pos, token.size(), value);
            pos += value.size();
        }
    };
    replaceAll("{label}", label);
    replaceAll("{detail}", detail);
    return text;
}

}  // namespace

FailureMessage describeFailure(
    const std::string& label,
    const std::string& typeName,
    const std::string& raw
)
{
    const std::string detail = firstLine(raw);
    for (const auto& entry : catalogue) {
        if (entry.typeContains && typeName.find(entry.typeContains) == std::string::npos) {
            continue;
        }
        if (raw.find(entry.rawContains) == std::string::npos) {
            continue;
        }
        return {entry.key, substitute(entry.text, label, detail)};
    }

    // No entry: the exception's own one-line message, with the label.
    if (detail.empty()) {
        return {"generic",
                "'" + label + "' failed to recompute and gave no reason, so check its inputs."};
    }
    return {"generic", "'" + label + "' failed to recompute: " + detail + "."};
}

FailureMessage describeFailure(const DocumentObject* obj, const std::string& raw)
{
    if (!obj) {
        return describeFailure("?", "", raw);
    }
    const char* label = obj->Label.getValue();
    return describeFailure(label ? label : obj->getNameInDocument() ? obj->getNameInDocument() : "?",
                           std::string(obj->getTypeId().getName()),
                           raw);
}

}  // namespace App
