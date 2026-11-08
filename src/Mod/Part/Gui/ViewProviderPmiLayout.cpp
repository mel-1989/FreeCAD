// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2026 The FreeCAD project association AISBL              *
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
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU     *
 *   Lesser General Public License for more details.                       *
 *                                                                         *
 *   You should have received a copy of the GNU Lesser General Public      *
 *   License along with FreeCAD. If not, see                               *
 *   <https://www.gnu.org/licenses/>.                                      *
 *                                                                         *
 **************************************************************************/

// G12 slice 2b. Sources: ISO 1101 and ASME Y14.5 for the geometric characteristic symbols, the diameter and spherical
// diameter zone symbols, the material-condition and other modifier symbols and the feature control frame layout
// (symbol compartment, tolerance compartment, datum compartments in order), the datum feature symbol (letter in a box);
// the TechDraw project's Y14.5-FreeCAD font (Peter Kanold's Y14.5 font, redistributable, see Y14.5Font.license) for the
// glyph outlines. The code points below were read off that font's own table by rendering them, not from any vendor.

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <string>
#include <vector>

#include <QFont>
#include <QFontDatabase>
#include <QFontMetricsF>
#include <QPainterPath>
#include <QPolygonF>
#include <QString>
#include <QTransform>

#include <App/Application.h>
#include <Mod/Part/App/PmiFeature.h>

#include "ViewProviderPmiLayout.h"

namespace PartGui::PmiLayout
{

namespace
{

constexpr int pixelSize = 100;  // the font is outlined at this size; the result is scaled to digit height 1
constexpr double cellPad = 28.0;
constexpr double tokenGap = 14.0;
constexpr double framePad = 42.0;

/// The family name of the registered Y14.5-FreeCAD font, empty when it could not be registered
const QString& glyphFamily()
{
    static const QString family = []() {
        const std::string path = App::Application::getResourceDir() + "Mod/TechDraw/Resources/fonts/Y14.5-FreeCAD.ttf";
        const int id = QFontDatabase::addApplicationFont(QString::fromStdString(path));
        if (id < 0) {
            return QString();
        }
        const QStringList fams = QFontDatabase::applicationFontFamilies(id);
        return fams.isEmpty() ? QString() : fams.first();
    }();
    return family;
}

/// One thing in a compartment: the glyph string in the Y14.5 font, the same as Unicode text for the label string, and
/// the named word drawn with the application font when the Y14.5 font is missing
struct Tok
{
    QString glyph;
    std::string plain;
    std::string word;
};

using Cell = std::vector<Tok>;

Tok ascii(const std::string& s)
{
    return {QString::fromStdString(s), s, s};
}

Tok sym(unsigned code, const char* plain, const char* word)
{
    return {QString(QChar(static_cast<ushort>(code))), plain, word};
}

std::string num(double v)
{
    char b[48];
    std::snprintf(b, sizeof b, "%.3f", v);
    return b;
}

std::string signedNum(double v, bool negativeZero)
{
    char b[48];
    if (v < 0 || (negativeZero && v == 0.0)) {
        std::snprintf(b, sizeof b, "-%.3f", std::fabs(v));
    }
    else {
        std::snprintf(b, sizeof b, "+%.3f", v);
    }
    return b;
}

/// Characteristic symbols of ISO 1101 / ASME Y14.5 and their codes in the Y14.5-FreeCAD font
bool toleranceSymbol(const std::string& type, Tok& out)
{
    struct Row
    {
        const char* type;
        unsigned code;
        const char* plain;
        const char* word;
    };
    static const Row rows[] = {
        {"Straightness", 0xb6, "⏤", "straightness"},
        {"Flatness", 0xb8, "⏥", "flatness"},
        {"CircularityOrRoundness", 0xb4, "○", "circularity"},
        {"Cylindricity", 0xb3, "⌭", "cylindricity"},
        {"ProfileOfLine", 0xb9, "⌒", "profile of a line"},
        {"ProfileOfSurface", 0xba, "⌓", "profile of a surface"},
        {"Angularity", 0xb2, "∠", "angularity"},
        {"Perpendicularity", 0xbc, "⟂", "perpendicularity"},
        {"Parallelism", 0xb7, "∥", "parallelism"},
        {"Symmetry", 0xbb, "⌯", "symmetry"},
        {"Position", 0xbf, "⌖", "position"},
        {"Concentricity", 0xb5, "◎", "concentricity"},
        {"CircularRunout", 0xbd, "↗", "circular runout"},
        {"TotalRunout", 0xbe, "⌰", "total runout"},
    };
    for (const Row& r : rows) {
        if (type == r.type) {
            out = sym(r.code, r.plain, r.word);
            return true;
        }
    }
    return false;
}

Tok modifierTok(const std::string& m)
{
    if (m == "MAXIMUM_MATERIAL_REQUIREMENT") {
        return sym(0xcc, "Ⓜ", "MMC");
    }
    if (m == "LEAST_MATERIAL_REQUIREMENT") {
        return sym(0xcb, "Ⓛ", "LMC");
    }
    if (m == "FREE_STATE") {
        return sym(0xc5, "Ⓕ", "F");
    }
    if (m == "TANGENT_PLANE") {
        return sym(0xd2, "Ⓣ", "T");
    }
    if (m == "STATISTICAL_TOLERANCE") {
        return sym(0xc6, "ST", "ST");
    }
    if (m == "ALL_AROUND") {
        return ascii("ALL AROUND");
    }
    if (m == "SEPARATE_REQUIREMENT") {
        return ascii("SEP REQT");
    }
    std::string w = m;
    std::replace(w.begin(), w.end(), '_', ' ');
    return ascii(w);
}

/// Diameter symbol (U+2300 in Unicode text), and its Y14.5-FreeCAD code
Tok diameterTok()
{
    return sym(0xd8, "⌀", "dia");
}

std::vector<Cell> dimensionCells(const Part::PmiDimension* d)
{
    Cell c;
    const std::string type = d->PmiType.getStrValue();
    if (type == "Size_Diameter") {
        c.push_back(diameterTok());
    }
    else if (type == "Size_SphericalDiameter") {
        c.push_back(sym(0xdb, "S⌀", "S dia"));
    }
    else if (type == "Size_Radius") {
        c.push_back(sym(0xd9, "R", "R"));
    }
    else if (type == "Size_SphericalRadius") {
        c.push_back(sym(0xda, "SR", "SR"));
    }
    std::string s = num(d->Value.getValue());
    if (type == "Size_Angular" || type == "Location_Angular") {
        c.push_back(ascii(s));
        c.push_back(sym(0xb0, "°", "deg"));
    }
    else {
        c.push_back(ascii(s));
    }
    const std::string kind = d->BoundsKind.getStrValue();
    if (d->HasBounds.getValue() && kind == "plus_minus") {
        c.push_back(ascii(" " + signedNum(d->UpperBound.getValue(), false) + "/" + signedNum(d->LowerBound.getValue(), true)));
    }
    else if (d->HasBounds.getValue() && kind == "range") {
        c.push_back(ascii(" (" + num(d->LowerBound.getValue()) + " to " + num(d->UpperBound.getValue()) + ")"));
    }
    else if (kind == "class" && !d->ToleranceClass.getStrValue().empty()) {
        c.push_back(ascii(" " + d->ToleranceClass.getStrValue()));
    }
    return {c};
}

std::vector<Cell> toleranceCells(const Part::PmiTolerance* t)
{
    std::vector<Cell> cells;
    Tok s;
    if (!toleranceSymbol(t->PmiType.getStrValue(), s)) {
        std::string w = t->PmiType.getStrValue();
        std::transform(w.begin(), w.end(), w.begin(), [](unsigned char ch) { return static_cast<char>(std::tolower(ch)); });
        s = ascii(w);
    }
    cells.push_back({s});
    Cell zone;
    const std::string shape = t->ZoneShape.getStrValue();
    const std::string value = num(t->Value.getValue());
    if (shape == "Diameter") {
        zone.push_back({QString(QChar(0xd8)) + QString::fromStdString(value), "\u2300" + value, "dia " + value});
    }
    else if (shape == "SphericalDiameter") {
        zone.push_back({QString(QChar(0xdb)) + QString::fromStdString(value), "S\u2300" + value, "S dia " + value});
    }
    else {
        zone.push_back(ascii(value));
    }
    if (t->ZoneModifier.getStrValue() == "Projected") {
        zone.push_back(sym(0xce, "Ⓟ", "P"));
        if (t->ZoneModifierValue.getValue() > 0) {
            zone.push_back(ascii(num(t->ZoneModifierValue.getValue())));
        }
    }
    for (const std::string& m : t->Modifiers.getValues()) {
        zone.push_back(modifierTok(m));
    }
    cells.push_back(zone);
    for (const std::string& r : t->DatumReferences.getValues()) {
        // a reference reads "E" or "E(MAXIMUM_MATERIAL_REQUIREMENT)": the letter, then the modifier as its symbol
        const size_t open = r.find('(');
        if (open == std::string::npos || r.back() != ')') {
            cells.push_back({ascii(r)});
            continue;
        }
        Cell c {ascii(r.substr(0, open))};
        std::string mods = r.substr(open + 1, r.size() - open - 2);
        size_t from = 0;
        while (from <= mods.size()) {
            const size_t comma = mods.find(',', from);
            const std::string m = mods.substr(from, comma == std::string::npos ? std::string::npos : comma - from);
            if (!m.empty()) {
                c.push_back(modifierTok(m));
            }
            if (comma == std::string::npos) {
                break;
            }
            from = comma + 1;
        }
        cells.push_back(c);
    }
    return cells;
}

std::vector<Cell> datumCells(const Part::PmiDatum* d)
{
    const std::string l = d->Letter.getStrValue();
    return {{Tok {QString::fromStdString(l), "[" + l + "]", l}}};
}

/// Compartments of the label and whether they are drawn in boxes (frame, datum box) or as plain text (dimension)
bool cellsOf(const Part::PmiItem* item, std::vector<Cell>& cells, bool& boxed)
{
    if (auto* d = dynamic_cast<const Part::PmiDimension*>(item)) {
        cells = dimensionCells(d);
        boxed = false;
    }
    else if (auto* t = dynamic_cast<const Part::PmiTolerance*>(item)) {
        cells = toleranceCells(t);
        boxed = true;
    }
    else if (auto* dt = dynamic_cast<const Part::PmiDatum*>(item)) {
        cells = datumCells(dt);
        boxed = true;
    }
    else {
        return false;
    }
    return true;
}

}  // namespace

bool glyphFontAvailable()
{
    return !glyphFamily().isEmpty();
}

std::string labelText(const Part::PmiItem* item)
{
    std::vector<Cell> cells;
    bool boxed = false;
    if (!item || !cellsOf(item, cells, boxed)) {
        return {};
    }
    std::string out;
    for (size_t i = 0; i < cells.size(); ++i) {
        if (i) {
            out += " | ";
        }
        for (size_t j = 0; j < cells[i].size(); ++j) {
            // a frame compartment separates its tokens by a space; the tokens of a dimension join as written
            if (j && boxed) {
                out += " ";
            }
            out += cells[i][j].plain;
        }
    }
    return out;
}

Label buildLabel(const Part::PmiItem* item)
{
    Label label;
    std::vector<Cell> cells;
    bool boxed = false;
    if (!item || !cellsOf(item, cells, boxed)) {
        return label;
    }
    label.text = labelText(item);
    const QString family = glyphFamily();
    label.fontLoaded = !family.isEmpty();
    QFont font = label.fontLoaded ? QFont(family) : QFont();
    font.setPixelSize(pixelSize);
    const QFontMetricsF fm(font);
    double cap = fm.capHeight();
    if (cap < 10) {
        cap = 0.7 * pixelSize;
    }

    std::vector<Polyline> lines;  // Qt coordinates: y down, baseline at 0
    auto addPath = [&lines](const QPainterPath& p) {
        for (const QPolygonF& poly : p.toSubpathPolygons(QTransform())) {
            Polyline pl;
            for (const QPointF& q : poly) {
                pl.push_back({q.x(), q.y()});
            }
            if (pl.size() >= 2 && (pl.front()[0] != pl.back()[0] || pl.front()[1] != pl.back()[1])) {
                pl.push_back(pl.front());
            }
            if (pl.size() >= 2) {
                lines.push_back(std::move(pl));
            }
        }
    };

    double x = 0.0;
    const double top = -cap - framePad, bottom = framePad;
    std::vector<double> dividers;
    for (size_t i = 0; i < cells.size(); ++i) {
        if (boxed) {
            x += cellPad;
        }
        for (size_t j = 0; j < cells[i].size(); ++j) {
            const Tok& tk = cells[i][j];
            const QString s = label.fontLoaded ? tk.glyph : QString::fromStdString(tk.word);
            QPainterPath p;
            p.addText(QPointF(x, 0.0), font, s);
            addPath(p);
            x += fm.horizontalAdvance(s);
            if (j + 1 < cells[i].size()) {
                x += tokenGap;
            }
        }
        if (boxed) {
            x += cellPad;
            dividers.push_back(x);
        }
    }
    if (boxed) {
        const double right = dividers.empty() ? x : dividers.back();
        lines.push_back({{0, top}, {right, top}, {right, bottom}, {0, bottom}, {0, top}});
        for (size_t i = 0; i + 1 < dividers.size(); ++i) {
            lines.push_back({{dividers[i], top}, {dividers[i], bottom}});
        }
    }

    // centre on the bounding box, flip y to up and scale to digit height 1
    double x0 = 1e300, x1 = -1e300, y0 = 1e300, y1 = -1e300;
    for (const Polyline& pl : lines) {
        for (const auto& q : pl) {
            x0 = std::min(x0, q[0]);
            x1 = std::max(x1, q[0]);
            y0 = std::min(y0, q[1]);
            y1 = std::max(y1, q[1]);
        }
    }
    if (lines.empty()) {
        return label;
    }
    const double cx = 0.5 * (x0 + x1), cy = 0.5 * (y0 + y1);
    for (Polyline& pl : lines) {
        for (auto& q : pl) {
            q = {(q[0] - cx) / cap, -(q[1] - cy) / cap};
        }
    }
    label.lines = std::move(lines);
    return label;
}

}  // namespace PartGui::PmiLayout
