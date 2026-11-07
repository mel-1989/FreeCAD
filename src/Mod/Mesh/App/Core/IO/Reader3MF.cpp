// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2022 Werner Mayer <wmayer[at]users.sourceforge.net>     *
 *                                                                         *
 *   This file is part of the FreeCAD CAx development system.              *
 *                                                                         *
 *   This library is free software; you can redistribute it and/or         *
 *   modify it under the terms of the GNU Library General Public           *
 *   License as published by the Free Software Foundation; either          *
 *   version 2 of the License, or (at your option) any later version.      *
 *                                                                         *
 *   This library  is distributed in the hope that it will be useful,      *
 *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
 *   GNU Library General Public License for more details.                  *
 *                                                                         *
 *   You should have received a copy of the GNU Library General Public     *
 *   License along with this library; see the file COPYING.LIB. If not,    *
 *   write to the Free Software Foundation, Inc., 59 Temple Place,         *
 *   Suite 330, Boston, MA  02111-1307, USA                                *
 *                                                                         *
 ***************************************************************************/

#include <algorithm>
#include <array>
#include <cmath>
#include <map>
#include <memory>
#include <numeric>
#include <set>
#include <sstream>
#include <xercesc/dom/DOM.hpp>
#include <xercesc/parsers/XercesDOMParser.hpp>

#include "Core/MeshIO.h"
#include "Core/MeshKernel.h"
#include <Base/Console.h>
#include <Base/InputSource.h>
#include <Base/XMLTools.h>
#include <Base/ZipHeader.h>
#include <zipios++/zipfile.h>

#include "Reader3MF.h"


using namespace MeshCore;
using namespace XERCES_CPP_NAMESPACE;

namespace
{
constexpr int maxComponentDepth = 32;
constexpr std::size_t maxPieces = 100000;
const char* const defaultModelPath = "3D/3dmodel.model";

std::string localName(const DOMNode* node)
{
    std::string name = StrX(node->getNodeName()).c_str();
    auto pos = name.find(':');
    return pos == std::string::npos ? name : name.substr(pos + 1);
}

bool isElement(const DOMNode* node)
{
    return node && node->getNodeType() == DOMNode::ELEMENT_NODE;
}

bool hasAttr(const DOMNode* node, const char* name)
{
    return static_cast<const DOMElement*>(node)->hasAttribute(XStr(name).unicodeForm());
}

std::string attr(const DOMNode* node, const char* name)
{
    return StrX(static_cast<const DOMElement*>(node)->getAttribute(XStr(name).unicodeForm())).c_str();
}

/// value of a production-extension "path" attribute whatever its namespace prefix
std::string pathAttr(const DOMNode* node)
{
    DOMNamedNodeMap* map = node->getAttributes();
    for (XMLSize_t i = 0; map && i < map->getLength(); i++) {
        DOMNode* a = map->item(i);
        std::string name = StrX(a->getNodeName()).c_str();
        if (name == "path" || (name.size() > 5 && name.compare(name.size() - 5, 5, ":path") == 0)) {
            return StrX(a->getNodeValue()).c_str();
        }
    }
    return {};
}

std::optional<int> toInt(const std::string& s)
{
    if (s.empty()) {
        return {};
    }
    try {
        std::size_t used = 0;
        int v = std::stoi(s, &used);
        if (used == s.size()) {
            return v;
        }
    }
    catch (const std::exception&) {
    }
    return {};
}

int hexValue(char c)
{
    if (c >= '0' && c <= '9') {
        return c - '0';
    }
    if (c >= 'a' && c <= 'f') {
        return c - 'a' + 10;
    }
    if (c >= 'A' && c <= 'F') {
        return c - 'A' + 10;
    }
    return -1;
}

/// "#RRGGBB" or "#RRGGBBAA" (alpha FF when absent) as a packed 0xRRGGBBAA value
std::optional<uint32_t> parseColor(const std::string& s)
{
    if (s.size() != 7 && s.size() != 9) {
        return {};
    }
    if (s[0] != '#') {
        return {};
    }
    uint32_t v = 0;
    for (std::size_t i = 1; i < s.size(); i++) {
        int h = hexValue(s[i]);
        if (h < 0) {
            return {};
        }
        v = (v << 4) | static_cast<uint32_t>(h);
    }
    return s.size() == 7 ? ((v << 8) | 0xFFU) : v;
}

std::optional<double> unitScale(const std::string& unit)
{
    if (unit.empty() || unit == "millimeter") {
        return 1.0;
    }
    if (unit == "micron") {
        return 0.001;
    }
    if (unit == "centimeter") {
        return 10.0;
    }
    if (unit == "inch") {
        return 25.4;
    }
    if (unit == "foot") {
        return 304.8;
    }
    if (unit == "meter") {
        return 1000.0;
    }
    return {};
}

std::optional<Base::Matrix4D> readTransform(const std::string& text, double scale)
{
    constexpr std::size_t numEntries = 12;
    std::vector<double> v;
    std::string tok;
    std::string s = text;
    std::replace(s.begin(), s.end(), ',', ' ');
    std::istringstream in(s);
    try {
        while (in >> tok) {
            v.push_back(std::stod(tok));
        }
    }
    catch (const std::exception&) {
        return {};
    }
    if (v.size() != numEntries) {
        return {};
    }
    // 3MF stores the first three rows of a row-vector matrix; the last three numbers are the shift
    Base::Matrix4D mat;
    for (std::size_t k = 0; k < numEntries; k++) {
        mat[k % 3][k / 3] = v[k];
    }
    mat[0][3] *= scale;
    mat[1][3] *= scale;
    mat[2][3] *= scale;
    return mat;
}

bool isRigid(const Base::Matrix4D& m)
{
    constexpr double eps = 1e-9;
    for (int i = 0; i < 3; i++) {
        for (int j = 0; j < 3; j++) {
            double d = 0;
            for (int k = 0; k < 3; k++) {
                d += m[k][i] * m[k][j];
            }
            if (std::abs(d - (i == j ? 1.0 : 0.0)) > eps) {
                return false;
            }
        }
    }
    return m.determinant3() > 0;
}
}  // namespace

struct Reader3MF::Impl
{
    enum class Kind
    {
        Color,
        Base,
        OutOfScope
    };
    struct Group
    {
        Kind kind = Kind::Color;
        std::string tag;
        std::vector<std::optional<uint32_t>> colors;
        std::vector<std::string> names;
    };
    struct TriProp
    {
        int pid = -1;
        int p1 = -1;
        bool multi = false;
    };
    struct CompRef
    {
        int objectId = -1;
        Base::Matrix4D transform;
        std::string path;
    };
    struct Object
    {
        int id = -1;
        std::string name;
        int pid = -1;
        int pindex = -1;
        bool isMesh = false;
        MeshPointArray points;
        MeshFacetArray facets;
        std::vector<TriProp> props;
        MeshKernel kernel;
        std::vector<Base::Color> colors;
        std::vector<std::string> materials;
        std::vector<CompRef> comps;
    };
    struct ItemRef
    {
        int objectId = -1;
        Base::Matrix4D transform;
        std::string path;
    };
    struct Part
    {
        std::map<int, Group> groups;
        std::map<int, Object> objects;
        std::vector<int> order;
        std::vector<ItemRef> items;
    };
    struct Counters
    {
        std::size_t danglingPid = 0;
        std::size_t badIndex = 0;
        std::size_t badColor = 0;
        std::size_t multiP = 0;
        std::size_t badTransform = 0;
        std::map<std::string, std::size_t> outOfScope;
    };

    Impl(Reader3MF& r)
        : owner(r)
    {}

    Reader3MF& owner;
    double scale = 1.0;
    Counters count;
    Part root;
    std::map<std::string, std::unique_ptr<Part>> external;

    static std::unique_ptr<XercesDOMParser> makeDomParser()
    {
        std::unique_ptr<XercesDOMParser> parser(new XercesDOMParser);
        parser->setValidationScheme(XercesDOMParser::Val_Auto);
        parser->setDoNamespaces(false);
        parser->setDoSchema(false);
        parser->setValidationSchemaFullChecking(false);
        parser->setCreateEntityReferenceNodes(false);
        return parser;
    }

    static std::unique_ptr<DOMDocument> parseXml(std::istream& str, const char* id)
    {
        Base::StdInputSource inputSource(str, id);
        std::unique_ptr<XercesDOMParser> parser = makeDomParser();
        parser->parse(inputSource);
        return std::unique_ptr<DOMDocument>(parser->adoptDocument());
    }

    // ---- reading one model part ------------------------------------------------------------

    bool readPart(std::istream& str, const char* id, Part& part, bool isRoot)
    {
        std::unique_ptr<DOMDocument> doc;
        try {
            doc = parseXml(str, id);
        }
        catch (const XMLException&) {
            return false;
        }
        catch (const DOMException&) {
            return false;
        }
        if (!doc) {
            return false;
        }
        DOMElement* model = doc->getDocumentElement();
        if (!model || localName(model) != "model") {
            return false;
        }
        if (isRoot) {
            std::string unit = attr(model, "unit");
            if (auto s = unitScale(unit)) {
                scale = *s;
            }
            else {
                Base::Console().warning("3MF: unknown unit '{}', taking millimetre\n", unit);
            }
        }
        for (DOMNode* n = model->getFirstChild(); n; n = n->getNextSibling()) {
            if (!isElement(n)) {
                continue;
            }
            std::string tag = localName(n);
            if (tag == "resources") {
                readResources(n, part);
            }
            else if (tag == "build") {
                readBuild(n, part);
            }
        }
        finalize(part);
        return true;
    }

    void readResources(DOMNode* res, Part& part)
    {
        for (DOMNode* n = res->getFirstChild(); n; n = n->getNextSibling()) {
            if (!isElement(n)) {
                continue;
            }
            std::string tag = localName(n);
            if (tag == "object") {
                readObject(n, part);
            }
            else if (tag == "colorgroup" || tag == "basematerials") {
                if (!owner.readColors) {
                    continue;
                }
                auto id = toInt(attr(n, "id"));
                if (!id) {
                    continue;
                }
                Group g;
                g.tag = tag;
                g.kind = tag == "colorgroup" ? Kind::Color : Kind::Base;
                for (DOMNode* c = n->getFirstChild(); c; c = c->getNextSibling()) {
                    if (!isElement(c)) {
                        continue;
                    }
                    std::string text = attr(c, g.kind == Kind::Color ? "color" : "displaycolor");
                    auto col = parseColor(text);
                    if (!col) {
                        count.badColor++;
                    }
                    g.colors.push_back(col);
                    g.names.push_back(attr(c, "name"));
                }
                part.groups[*id] = std::move(g);
            }
            else if (tag == "texture2dgroup" || tag == "multiproperties" || tag == "compositematerials") {
                if (auto id = toInt(attr(n, "id")); id && owner.readColors) {
                    Group g;
                    g.kind = Kind::OutOfScope;
                    g.tag = tag;
                    part.groups[*id] = std::move(g);
                }
            }
        }
    }

    void readObject(DOMNode* node, Part& part)
    {
        auto id = toInt(attr(node, "id"));
        if (!id) {
            return;
        }
        Object obj;
        obj.id = *id;
        obj.name = attr(node, "name");
        if (owner.readColors) {
            obj.pid = toInt(attr(node, "pid")).value_or(-1);
            obj.pindex = toInt(attr(node, "pindex")).value_or(-1);
        }
        for (DOMNode* n = node->getFirstChild(); n; n = n->getNextSibling()) {
            if (!isElement(n)) {
                continue;
            }
            std::string tag = localName(n);
            if (tag == "mesh" && !obj.isMesh) {
                obj.isMesh = true;
                readMesh(n, obj);
            }
            else if (tag == "components") {
                for (DOMNode* c = n->getFirstChild(); c; c = c->getNextSibling()) {
                    if (isElement(c) && localName(c) == "component") {
                        CompRef ref;
                        ref.objectId = toInt(attr(c, "objectid")).value_or(-1);
                        ref.path = pathAttr(c);
                        if (hasAttr(c, "transform")) {
                            auto m = readTransform(attr(c, "transform"), scale);
                            if (m) {
                                ref.transform = *m;
                            }
                            else {
                                count.badTransform++;
                            }
                        }
                        obj.comps.push_back(ref);
                    }
                }
            }
        }
        if (!part.objects.count(obj.id)) {
            part.order.push_back(obj.id);
        }
        part.objects[obj.id] = std::move(obj);
    }

    void readMesh(DOMNode* mesh, Object& obj)
    {
        for (DOMNode* n = mesh->getFirstChild(); n; n = n->getNextSibling()) {
            if (!isElement(n)) {
                continue;
            }
            std::string tag = localName(n);
            if (tag == "vertices") {
                for (DOMNode* v = n->getFirstChild(); v; v = v->getNextSibling()) {
                    if (isElement(v) && hasAttr(v, "x") && hasAttr(v, "y") && hasAttr(v, "z")) {
                        // NOLINTBEGIN
                        float x = coordinate(attr(v, "x"));
                        float y = coordinate(attr(v, "y"));
                        float z = coordinate(attr(v, "z"));
                        obj.points.emplace_back(x, y, z);
                        // NOLINTEND
                    }
                }
            }
            else if (tag == "triangles") {
                for (DOMNode* t = n->getFirstChild(); t; t = t->getNextSibling()) {
                    if (isElement(t) && hasAttr(t, "v1") && hasAttr(t, "v2") && hasAttr(t, "v3")) {
                        PointIndex v1 = std::stoul(attr(t, "v1"));
                        PointIndex v2 = std::stoul(attr(t, "v2"));
                        PointIndex v3 = std::stoul(attr(t, "v3"));
                        obj.facets.emplace_back(v1, v2, v3);
                        if (owner.readColors) {
                            TriProp tp;
                            tp.pid = toInt(attr(t, "pid")).value_or(-1);
                            tp.p1 = toInt(attr(t, "p1")).value_or(-1);
                            if (hasAttr(t, "p2") && hasAttr(t, "p3")) {
                                std::string a = attr(t, "p1");
                                tp.multi = a != attr(t, "p2") || a != attr(t, "p3");
                            }
                            obj.props.push_back(tp);
                        }
                    }
                }
            }
        }
    }

    float coordinate(const std::string& s) const
    {
        if (scale == 1.0) {
            return std::stof(s);
        }
        return static_cast<float>(std::stod(s) * scale);
    }

    void readBuild(DOMNode* build, Part& part)
    {
        for (DOMNode* n = build->getFirstChild(); n; n = n->getNextSibling()) {
            if (isElement(n) && localName(n) == "item") {
                auto id = toInt(attr(n, "objectid"));
                if (!id) {
                    continue;
                }
                ItemRef item;
                item.objectId = *id;
                item.path = pathAttr(n);
                if (hasAttr(n, "transform")) {
                    auto m = readTransform(attr(n, "transform"), scale);
                    if (m) {
                        item.transform = *m;
                    }
                    else {
                        count.badTransform++;
                    }
                }
                part.items.push_back(item);
            }
        }
    }

    // ---- colours, cleanup ------------------------------------------------------------------

    void finalize(Part& part)
    {
        for (int id : part.order) {
            Object& obj = part.objects[id];
            if (!obj.isMesh) {
                continue;
            }
            Material mat;
            mat.binding = MeshIO::PER_FACE;
            bool any = false;
            if (owner.readColors && obj.props.size() == obj.facets.size()) {
                any = resolveColors(part, obj, mat.diffuseColor);
            }
            MeshCleanup meshCleanup(obj.points, obj.facets);
            if (any) {
                meshCleanup.SetMaterial(&mat);
            }
            meshCleanup.RemoveInvalids();
            MeshPointFacetAdjacency meshAdj(obj.points.size(), obj.facets);
            meshAdj.SetFacetNeighbourhood();
            obj.kernel.Adopt(obj.points, obj.facets);
            if (any) {
                obj.colors = std::move(mat.diffuseColor);
            }
            obj.props = {};
        }
    }

    /// resolve one colour per facet; false when no facet has a colour of its own
    bool resolveColors(const Part& part, Object& obj, std::vector<Base::Color>& out)
    {
        const Base::Color unset(204U << 24 | 204U << 16 | 204U << 8 | 255U);
        out.assign(obj.props.size(), unset);
        bool any = false;
        for (std::size_t i = 0; i < obj.props.size(); i++) {
            const TriProp& tp = obj.props[i];
            if (tp.multi) {
                count.multiP++;
            }
            int pid = tp.pid >= 0 ? tp.pid : obj.pid;
            int idx = tp.p1 >= 0 ? tp.p1 : obj.pindex;
            if (pid < 0 || idx < 0) {
                continue;
            }
            auto it = part.groups.find(pid);
            if (it == part.groups.end()) {
                count.danglingPid++;
                continue;
            }
            const Group& g = it->second;
            if (g.kind == Kind::OutOfScope) {
                count.outOfScope[g.tag]++;
                continue;
            }
            if (static_cast<std::size_t>(idx) >= g.colors.size()) {
                count.badIndex++;
                continue;
            }
            if (g.kind == Kind::Base && !g.names[idx].empty()
                && std::find(obj.materials.begin(), obj.materials.end(), g.names[idx])
                    == obj.materials.end()) {
                obj.materials.push_back(g.names[idx]);
            }
            if (g.colors[idx]) {
                out[i] = Base::Color(*g.colors[idx]);
                any = true;
            }
        }
        return any;
    }

    void report() const
    {
        if (!owner.readColors) {
            return;
        }
        if (count.danglingPid) {
            Base::Console().warning(
                "3MF: {} triangles refer to a property group (pid) that does not exist, no colour read for them\n",
                count.danglingPid
            );
        }
        if (count.badIndex) {
            Base::Console().warning(
                "3MF: {} triangles have a property index (pindex or p1) outside their property group, no colour read for them\n",
                count.badIndex
            );
        }
        if (count.badColor) {
            Base::Console().warning("3MF: {} colour values could not be parsed (bad colour string)\n", count.badColor);
        }
        for (const auto& [tag, n] : count.outOfScope) {
            Base::Console().warning("3MF: {} triangles use a {} which is not read, they get no colour of their own\n", n, tag);
        }
        if (count.multiP) {
            Base::Console().warning("3MF: {} triangles have distinct p1, p2, p3, taking p1\n", count.multiP);
        }
    }

    // ---- build items -----------------------------------------------------------------------

    Part* partFor(const std::string& path)
    {
        if (path.empty()) {
            return &root;
        }
        auto it = external.find(path);
        if (it != external.end()) {
            return it->second.get();
        }
        std::unique_ptr<Part> part;
        if (std::unique_ptr<std::istream> str = owner.OpenPart(path)) {
            part = std::make_unique<Part>();
            if (!readPart(*str, path.c_str(), *part, false)) {
                part.reset();
            }
        }
        Part* ptr = part.get();
        external[path] = std::move(part);
        return ptr;
    }

    struct Piece
    {
        const Object* obj;
        Base::Matrix4D transform;
        bool identity;
    };

    void flatten(Part* part, int objectId, const Base::Matrix4D& tr, bool identity, int depth, std::vector<const Object*>& stack, std::vector<Piece>& out)
    {
        if (!part || depth > maxComponentDepth) {
            return;
        }
        auto it = part->objects.find(objectId);
        if (it == part->objects.end()) {
            return;
        }
        const Object* obj = &it->second;
        if (std::find(stack.begin(), stack.end(), obj) != stack.end()) {
            return;  // a component cycle
        }
        if (out.size() >= maxPieces) {
            return;
        }
        if (obj->isMesh) {
            out.push_back({obj, tr, identity});
            return;
        }
        stack.push_back(obj);
        for (const CompRef& c : obj->comps) {
            Part* sub = c.path.empty() ? part : partFor(c.path);
            Base::Matrix4D m = tr;
            m *= c.transform;  // the component applies first, then the transform of its parent
            flatten(sub, c.objectId, m, false, depth + 1, stack, out);
        }
        stack.pop_back();
    }

    void addItem(Part* part, int objectId, const Base::Matrix4D& transform)
    {
        std::vector<Piece> pieces;
        std::vector<const Object*> stack;
        Base::Matrix4D unit;
        flatten(part, objectId, unit, true, 0, stack, pieces);
        if (pieces.empty()) {
            return;
        }
        Item3MF item;
        item.objectId = objectId;
        item.transform = transform;
        item.rigid = isRigid(transform);
        auto top = part->objects.find(objectId);
        item.name = top->second.name;
        bool first = true;
        bool anyColors = false;
        for (const Piece& p : pieces) {
            anyColors = anyColors || !p.obj->colors.empty();
        }
        std::vector<Base::Color> colors;
        for (const Piece& p : pieces) {
            MeshKernel k = p.obj->kernel;
            if (!p.identity) {
                k.Transform(p.transform);
            }
            if (first) {
                item.kernel = std::move(k);
                first = false;
            }
            else {
                item.kernel.Merge(k);
            }
            if (anyColors) {
                if (p.obj->colors.empty()) {
                    colors.insert(colors.end(), p.obj->kernel.CountFacets(), Base::Color(204U << 24 | 204U << 16 | 204U << 8 | 255U));
                }
                else {
                    colors.insert(colors.end(), p.obj->colors.begin(), p.obj->colors.end());
                }
            }
            for (const std::string& m : p.obj->materials) {
                if (std::find(item.materials.begin(), item.materials.end(), m) == item.materials.end()) {
                    item.materials.push_back(m);
                }
            }
        }
        item.faceColors = std::move(colors);
        owner.items.push_back(std::move(item));
    }

    void assemble()
    {
        for (const ItemRef& it : root.items) {
            addItem(partFor(it.path), it.objectId, it.transform);
        }
        if (owner.items.empty()) {
            // no usable build section: keep what the file holds, every mesh object once
            for (int id : root.order) {
                if (root.objects[id].isMesh) {
                    addItem(&root, id, Base::Matrix4D());
                }
            }
        }
    }
};

// ---- Reader3MF -------------------------------------------------------------------------------

Reader3MF::Reader3MF(std::istream& str)
{
    file = std::make_unique<zipios::ZipHeader>(str);
    OpenStartPart();
}

Reader3MF::Reader3MF(const std::string& filename)
{
    file = std::make_unique<zipios::ZipFile>(filename);
    OpenStartPart();
}

Reader3MF::~Reader3MF() = default;

std::unique_ptr<std::istream> Reader3MF::OpenPart(const std::string& path) const
{
    if (!file || !file->isValid()) {
        return nullptr;
    }
    auto percentDecode = [](const std::string& s) {
        std::string out;
        for (std::size_t i = 0; i < s.size(); i++) {
            if (s[i] == '%' && i + 2 < s.size() + 0 && hexValue(s[i + 1]) >= 0 && hexValue(s[i + 2]) >= 0) {
                out += static_cast<char>(hexValue(s[i + 1]) * 16 + hexValue(s[i + 2]));
                i += 2;
            }
            else {
                out += s[i];
            }
        }
        return out;
    };
    std::string raw = path;
    while (!raw.empty() && raw[0] == '/') {
        raw.erase(0, 1);
    }
    // some packages store the percent-encoded name literally, so try both forms
    for (const std::string& name : {percentDecode(raw), raw}) {
        if (name.empty()) {
            continue;
        }
        std::unique_ptr<std::istream> s(file->getInputStream(name));
        if (s) {
            return s;
        }
    }
    return nullptr;
}

void Reader3MF::OpenStartPart()
{
    if (!file || !file->isValid()) {
        return;
    }
    std::string target;
    if (std::unique_ptr<std::istream> rels = OpenPart("_rels/.rels")) {
        try {
            auto doc = Impl::parseXml(*rels, "rels");
            DOMElement* top = doc ? doc->getDocumentElement() : nullptr;
            for (DOMNode* n = top ? top->getFirstChild() : nullptr; n && target.empty(); n = n->getNextSibling()) {
                if (isElement(n) && localName(n) == "Relationship") {
                    std::string type = attr(n, "Type");
                    constexpr const char* suffix = "/3dmodel";
                    if (type.size() >= 8 && type.compare(type.size() - 8, 8, suffix) == 0) {
                        target = attr(n, "Target");
                    }
                }
            }
        }
        catch (const XMLException&) {
        }
        catch (const DOMException&) {
        }
    }
    if (!target.empty()) {
        zip = OpenPart(target);
    }
    if (!zip) {
        zip.reset(file->getInputStream(defaultModelPath));
    }
}

std::vector<int> Reader3MF::GetMeshIds() const
{
    std::vector<int> ids(items.size());
    std::iota(ids.begin(), ids.end(), 0);
    return ids;
}

bool Reader3MF::Load()
{
    try {
        return TryLoad();
    }
    catch (const std::exception&) {
        return false;
    }
}

bool Reader3MF::TryLoad()
{
    if (!zip) {
        return false;
    }
    Impl impl(*this);
    bool ok = false;
    try {
        ok = impl.readPart(*zip, defaultModelPath, impl.root, true);
    }
    catch (const std::exception&) {
        ok = false;
    }
    if (!ok) {
        return false;
    }
    impl.assemble();
    impl.report();
    return !items.empty();
}
