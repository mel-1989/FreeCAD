// SPDX-License-Identifier: LGPL-2.1-or-later

/******************************************************************************
 *                                                                            *
 *   FreeCAD is free software: you can redistribute it and/or modify          *
 *   it under the terms of the GNU Lesser General Public License as           *
 *   published by the Free Software Foundation, either version 2.1            *
 *   of the License, or (at your option) any later version.                   *
 *                                                                            *
 *   FreeCAD is distributed in the hope that it will be useful,               *
 *   but WITHOUT ANY WARRANTY; without even the implied warranty              *
 *   of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.                  *
 *   See the GNU Lesser General Public License for more details.              *
 *                                                                            *
 ******************************************************************************/

#include <algorithm>
#include <map>
#include <unordered_map>
#include <unordered_set>

#include <Base/Exception.h>
#include <Base/Interpreter.h>
#include <Base/Quantity.h>
#include <Base/QuantityPy.h>
#include <CXX/Objects.hxx>

#include "AgentTurnGuard.h"
#include "AgentTurnIntrospect.h"
#include "Document.h"
#include "DocumentObject.h"
#include "Property.h"
#include "PropertyLinks.h"
#include "PropertyPythonObject.h"
#include "PropertyStandard.h"
#include "PropertyUnits.h"
#include "Transactions.h"
#include "private/DocumentP.h"

using namespace App;

// The transaction records (Transaction::_Objects and _Earlier, TransactionObject::status,
// _PropChangeMap and _NameInDocument) are private or protected, and Transactions.h is
// deliberately left untouched so that the dozen files including it do not rebuild. The
// explicit-instantiation access idiom below reads those members without editing the header:
// the C++ standard exempts the arguments of an explicit instantiation from access checks.
// Every use is compile-checked against the member's real type, so a change to Transactions.h
// breaks this file's build instead of silently misreading the records.
namespace
{

template<class Tag, auto Member>
struct Steal
{
    friend auto steal(Tag)
    {
        return Member;
    }
};

struct ObjectsTag
{
    friend auto steal(ObjectsTag);
};
struct EarlierTag
{
    friend auto steal(EarlierTag);
};
struct StatusTag
{
    friend auto steal(StatusTag);
};
struct PropMapTag
{
    friend auto steal(PropMapTag);
};
struct NameTag
{
    friend auto steal(NameTag);
};
struct XDocNameTag
{
    friend auto steal(XDocNameTag);
};

template struct Steal<ObjectsTag, &Transaction::_Objects>;
template struct Steal<EarlierTag, &Transaction::_Earlier>;
template struct Steal<StatusTag, &TransactionObject::status>;
template struct Steal<PropMapTag, &TransactionObject::_PropChangeMap>;
template struct Steal<NameTag, &TransactionObject::_NameInDocument>;
template struct Steal<XDocNameTag, &PropertyXLink::docName>;

}  // namespace

namespace
{

/// Names the protected enumerators of TransactionObject. Never instantiated.
struct Peek: TransactionObject
{
    // From the undo point of view: New = the object was removed (undo re-adds it), Del = the
    // object was added (undo deletes it), Chn = the object was changed.
    static constexpr int kNew = static_cast<int>(New);
    static constexpr int kDel = static_cast<int>(Del);
};

using PropEntry = AgentObjectChange::PropertyEntry;
using PropKind = AgentObjectChange::PropertyChange;

/// One property of one object, as the oldest record saw it.
struct PropAcc
{
    std::string name;
    std::string oldName;
    PropKind kind {PropKind::Modified};
    const Property* old {nullptr};  // value at the start of the turn (null: did not exist)
    bool hasOld {false};
};

struct ObjAcc
{
    const TransactionalObject* ptr {nullptr};
    int first {-1};  // status in the oldest segment that holds the object
    int last {-1};   // status in the newest segment that holds it
    std::string removedName;
    std::vector<std::string> order;
    std::map<std::string, PropAcc> props;
};

/// A transaction keeps a copy of a Quantity property as a plain PropertyFloat (PropertyQuantity
/// does not override Copy()), so Property::isSame() would call every such property changed.
bool sameFloat(const Property& old, const Property& current)
{
    const auto* o = freecad_cast<const PropertyFloat*>(&old);
    const auto* c = freecad_cast<const PropertyFloat*>(&current);
    return o && c && o->getTypeId() == PropertyFloat::getClassTypeId() && o->getValue() == c->getValue();
}

/// Python-object properties are compared as Python objects (identity, then ==). Property::isSame
/// would pickle both through Python, giving up the GIL in the middle of reading the live property,
/// which another thread may be replacing: a use-after-free.
bool samePython(const Property& old, const Property& current)
{
    Base::PyGILStateLocker lock;
    Py::Object a(const_cast<Property&>(old).getPyObject(), true);
    Py::Object b(const_cast<Property&>(current).getPyObject(), true);
    if (a.ptr() == b.ptr()) {
        return true;
    }
    const int eq = PyObject_RichCompareBool(a.ptr(), b.ptr(), Py_EQ);
    if (eq < 0) {
        PyErr_Clear();
    }
    return eq == 1;
}

void collectSegments(const Transaction* t, std::vector<const Transaction*>& out)
{
    for (const auto* earlier : (*t).*steal(EarlierTag {})) {
        collectSegments(earlier, out);
    }
    out.push_back(t);
}

const char* nameOf(const DocumentObject* obj)
{
    const char* n = obj->getNameInDocument();
    return n ? n : "";
}

/// Fold one segment's record of an object into the accumulated one.
void foldEntry(ObjAcc& acc, const TransactionObject* to)
{
    const int status = static_cast<int>(to->*steal(StatusTag {}));
    if (acc.first < 0) {
        acc.first = status;
    }
    if (status == Peek::kNew) {
        acc.last = status;
        acc.removedName = to->*steal(NameTag {});
    }
    else if (status == Peek::kDel) {
        acc.last = status;
    }
    else if (acc.last < 0) {
        acc.last = status;
    }
    if (acc.first == Peek::kDel) {
        return;  // added in the turn: reported whole, no property detail
    }
    // Within one segment prefer the record that carries an old value over a
    // "created" record of the same name (a dynamic property removed and re-created).
    std::map<std::string, PropAcc> local;
    for (const auto& [id, data] : to->*steal(PropMapTag {})) {
        (void)id;
        PropAcc p;
        p.name = data.name;
        if (!data.nameOrig.empty()) {
            p.kind = PropKind::Renamed;
            p.oldName = data.nameOrig;
        }
        else if (data.propertyTarget && data.target) {
            p.kind = PropKind::Moved;
        }
        else {
            p.old = data.property;
            p.hasOld = data.property != nullptr;
        }
        if (p.name.empty() && data.propertyOrig) {
            if (const char* n = acc.ptr->getPropertyName(data.propertyOrig)) {
                p.name = n;
            }
        }
        if (p.name.empty()) {
            continue;
        }
        const std::string key = p.name + (p.kind == PropKind::Modified ? "" : "\x01" + std::to_string(int(p.kind)));
        auto it = local.find(key);
        if (it == local.end() || (!it->second.hasOld && p.hasOld)) {
            local[key] = std::move(p);
        }
    }
    for (auto& [key, p] : local) {
        // The oldest record wins: it holds the value at the start of the turn.
        if (acc.props.emplace(key, std::move(p)).second) {
            acc.order.push_back(key);
        }
    }
}

AgentTurnChangeSet buildChangeSet(Document& doc, AgentTurnState& st, const std::vector<const Transaction*>& segs)
{
    AgentTurnChangeSet cs;
    cs.tid = st.tid;
    cs.turnName = st.name;
    cs.segments = static_cast<int>(segs.size());

    std::vector<ObjAcc> accs;
    std::unordered_map<const TransactionalObject*, std::size_t> index;
    for (const auto* seg : segs) {
        for (const auto& info : ((*seg).*steal(ObjectsTag {})).get<0>()) {
            auto [it, fresh] = index.emplace(info.first, accs.size());
            if (fresh) {
                accs.emplace_back();
                accs.back().ptr = info.first;
            }
            foldEntry(accs[it->second], info.second);
        }
    }

    std::unordered_set<std::string> removedNames;
    std::unordered_set<std::string> addedNames;
    for (auto& a : accs) {
        const auto* obj = freecad_cast<const DocumentObject*>(a.ptr);
        if (!obj) {
            continue;
        }
        const bool added = a.first == Peek::kDel;
        const bool removed = a.last == Peek::kNew;
        if (added && removed) {
            continue;  // created and removed inside the turn: no net change
        }
        AgentObjectChange oc;
        oc.object = obj;
        oc.typeId = obj->getTypeId().getName();
        oc.label = obj->Label.getValue();
        if (removed) {
            oc.status = AgentObjectChange::Status::Removed;
            oc.name = a.removedName;
            removedNames.insert(oc.name);
        }
        else if (added) {
            oc.status = AgentObjectChange::Status::Added;
            oc.name = nameOf(obj);
            addedNames.insert(oc.name);
        }
        else {
            oc.name = nameOf(obj);
            for (const auto& key : a.order) {
                const PropAcc& p = a.props.at(key);
                PropEntry e;
                e.name = p.name;
                e.change = p.kind;
                e.oldName = p.oldName;
                e.oldValue = p.old;
                if (p.kind == PropKind::Modified) {
                    e.current = obj->getPropertyByName(p.name.c_str());
                    if (!p.hasOld && !e.current) {
                        continue;  // created and deleted inside the turn
                    }
                    if (!p.hasOld) {
                        e.change = PropKind::Added;
                    }
                    else if (!e.current) {
                        e.change = PropKind::Removed;
                    }
                    else {
                        bool same = false;
                        try {
                            same = freecad_cast<const PropertyPythonObject*>(p.old)
                                ? samePython(*p.old, *e.current)
                                : (p.old->isSame(*e.current) || sameFloat(*p.old, *e.current));
                        }
                        catch (...) {
                            ++cs.uncomparable;
                        }
                        if (same) {
                            continue;  // set and restored: no net change
                        }
                    }
                    e.typeName = (e.current ? e.current : p.old)->getTypeId().getName();
                }
                else {
                    e.current = obj->getPropertyByName(p.name.c_str());
                    e.typeName = e.current ? e.current->getTypeId().getName() : "";
                }
                oc.properties.push_back(std::move(e));
            }
            if (oc.properties.empty()) {
                continue;
            }
        }
        cs.objects.push_back(std::move(oc));
    }
    for (auto& oc : cs.objects) {
        if (oc.status == AgentObjectChange::Status::Added && removedNames.count(oc.name)) {
            oc.replaced = true;
        }
        else if (oc.status == AgentObjectChange::Status::Removed && addedNames.count(oc.name)) {
            oc.replaced = true;
        }
    }
    (void)doc;
    return cs;
}

}  // namespace

void AgentTurnIntrospect::collectWith(Document& doc, std::chrono::milliseconds wait, Callback cb, void* user)
{
    if (!AgentTurnGuard::isActive(doc)) {
        throw Base::RuntimeError("no agent turn is active on this document");
    }
    const std::shared_ptr<AgentTurnState> turn = Transaction::accessDocument(doc).d->agent;
    // Gives up the GIL for the wait; throws when the document was closed meanwhile.
    if (!AgentTurnGuard::waitForWorkerReleasingGil(doc, wait)) {
        throw Base::RuntimeError("the document is still recomputing on the async worker");
    }
    auto acc = Transaction::accessDocument(doc);
    std::shared_ptr<AgentTurnState> st = acc.d->agent;
    if (!st || st != turn) {
        throw Base::RuntimeError("the agent turn ended while waiting for the async worker");
    }
    // Keeps the worker from recording while the segments are read.
    auto lock = AgentTurnGuard::acquire(st->mutex);
    std::vector<const Transaction*> segs(st->parked.begin(), st->parked.end());
    if (acc.d->activeUndoTransaction) {
        segs.push_back(acc.d->activeUndoTransaction);
    }
    // A composite never occurs here (the fold happens in end()), but flatten it anyway.
    std::vector<const Transaction*> flat;
    for (const auto* seg : segs) {
        collectSegments(seg, flat);
    }
    AgentTurnChangeSet cs = buildChangeSet(doc, *st, flat);
    cb(cs, user);
}

namespace
{

const char* statusName(AgentObjectChange::Status s)
{
    switch (s) {
        case AgentObjectChange::Status::Added:
            return "added";
        case AgentObjectChange::Status::Removed:
            return "removed";
        default:
            return "modified";
    }
}

const char* changeName(PropKind k)
{
    switch (k) {
        case PropKind::Added:
            return "added";
        case PropKind::Removed:
            return "removed";
        case PropKind::Renamed:
            return "renamed";
        case PropKind::Moved:
            return "moved";
        default:
            return "modified";
    }
}

/// One cross-document link as JSON: its target's object name and document (name, or path when the
/// document is not open), which Property::getPyObject cannot give for a copy kept by a transaction.
Py::Object xlinkJson(const PropertyXLink& l, bool withSubs)
{
    std::string obj;
    std::string doc;
    const DocumentObject* o = l.getValue();
    if (o && o->isAttachedToDocument()) {
        obj = o->getNameInDocument();
        doc = o->getDocument()->getName();
    }
    else {
        if (const char* n = l.getObjectName()) {
            obj = n;
        }
        // A transaction's copy keeps the target document's name (empty for a link by file path).
        doc = l.*steal(XDocNameTag {});
        if (doc.empty()) {
            if (const char* path = l.getDocumentPath()) {
                doc = path;
            }
        }
    }
    if (obj.empty()) {
        return Py::None();
    }
    Py::Dict d;
    d.setItem("object", Py::String(obj));
    d.setItem("document", Py::String(doc));
    const auto subs = l.getSubValues(false);
    if (withSubs || !subs.empty()) {
        Py::List sl;
        for (const auto& sub : subs) {
            sl.append(Py::String(sub));
        }
        d.setItem("subs", sl);
    }
    return d;
}

/// JSON for the XLink family; null when @p prop is not one.
bool xlinkFamilyJson(const Property* prop, const std::string& typeName, Py::Object& out)
{
    if (!prop) {
        return false;
    }
    if (const auto* x = freecad_cast<const PropertyXLink*>(prop)) {
        out = xlinkJson(*x, false);
        return true;
    }
    if (const auto* xl = freecad_cast<const PropertyXLinkSubList*>(prop)) {
        // The transaction copies an XLinkList as an XLinkSubList, so the live type name decides.
        const bool plain = typeName.find("XLinkList") != std::string::npos;
        Py::List list;
        for (const auto& link : xl->getSubListValues()) {
            list.append(xlinkJson(link, !plain));
        }
        out = list;
        return true;
    }
    return false;
}

/// Value of a property as a Python object; None when the property cannot express one.
Py::Object rawValue(const Property* prop, const Property* unitFrom)
{
    if (!prop) {
        return Py::None();
    }
    try {
        // PropertyLinkList::getPyObject turns an object that is no longer attached (removed in the
        // turn) into None; keep the object so that its name can still be looked up.
        if (const auto* ll = freecad_cast<const PropertyLinkList*>(prop)) {
            Py::List list;
            for (const auto* o : ll->getValues()) {
                list.append(o ? Py::asObject(const_cast<DocumentObject*>(o)->getPyObject()) : Py::None());
            }
            return list;
        }
        // Restore the unit that the transaction's float copy of a quantity lost.
        const auto* q = unitFrom ? freecad_cast<const PropertyQuantity*>(unitFrom) : nullptr;
        const auto* f = freecad_cast<const PropertyFloat*>(prop);
        if (q && f && prop != unitFrom && f->getTypeId() == PropertyFloat::getClassTypeId()) {
            return Py::asObject(new Base::QuantityPy(new Base::Quantity(f->getValue(), q->getUnit())));
        }
        PyObject* o = const_cast<Property*>(prop)->getPyObject();
        if (!o) {
            PyErr_Clear();
            return Py::None();
        }
        return Py::asObject(o);
    }
    catch (...) {
        PyErr_Clear();
        return Py::None();
    }
}

/// Phase 2 of asPython: runs Python code, so it must not hold the turn mutex.
Py::Object convert(const Py::Object& converter, const Py::Object& raw, const std::string& typeName, const Py::Object& removed)
{
    if (converter.isNone() || raw.isNone()) {
        return raw;
    }
    try {
        Py::Callable fn(converter);
        Py::Tuple args(3);
        args.setItem(0, raw);
        args.setItem(1, Py::String(typeName));
        args.setItem(2, removed);
        return fn.apply(args);
    }
    catch (Py::Exception& e) {
        e.clear();
        Py::Dict d;
        d.setItem("unserialised", Py::String(typeName));
        return d;
    }
}

/// Phase 1 of asPython (under the turn mutex, no Python code runs): the property's value as a
/// Python object, final already for the cross-document link family.
Py::Object snapshotValue(const Property* prop, const Property* unitFrom, const std::string& typeName, bool jsonValues, bool& final)
{
    final = false;
    if (!prop) {
        return Py::None();
    }
    Py::Object xl;
    if (jsonValues && xlinkFamilyJson(prop, typeName, xl)) {
        final = true;
        return xl;
    }
    return rawValue(prop, unitFrom);
}

/// Types for which Property::isSame is known to say "different" for equal values: a rebuilt shape.
bool isShapeType(const std::string& typeName)
{
    return typeName.find("PartShape") != std::string::npos;
}

/// A shape summary that is complete (not an error or unserialised marker).
bool isShapeSummary(const Py::Object& v)
{
    return v.isDict() && PyDict_GetItemString(v.ptr(), "null") != nullptr;
}

struct Pending
{
    Py::Dict entry;
    Py::Object oldRaw;
    Py::Object newRaw;
    bool oldFinal {false};
    bool newFinal {false};
    bool modified {false};
    bool values {false};
    std::string typeName;
};

}  // namespace

_object* AgentTurnIntrospect::asPython(Document& doc, bool jsonValues, std::chrono::milliseconds wait)
{
    Py::Dict out;
    Py::Dict removedNames;
    std::vector<Pending> pending;
    // Phase 1, under the turn mutex: everything is read into Python objects. (Comparing
    // Python-object properties can still run Python; that is safe because no thread ever blocks
    // on the turn mutex while holding the GIL, see AgentTurnGuard::acquire.)
    collect(doc, wait, [&](const AgentTurnChangeSet& cs) {
        // Removed objects have lost their Name, but old link values still point at them.
        for (const auto& oc : cs.objects) {
            if (oc.status == AgentObjectChange::Status::Removed && oc.object) {
                Py::Object po(const_cast<DocumentObject*>(oc.object)->getPyObject(), true);
                removedNames.setItem(Py::Long(reinterpret_cast<std::uintptr_t>(po.ptr())), Py::String(oc.name));
            }
        }
        out.setItem("document", Py::String(doc.getName()));
        out.setItem("tid", Py::Long(cs.tid));
        out.setItem("turn", Py::String(cs.turnName));
        out.setItem("segments", Py::Long(cs.segments));
        out.setItem("uncomparable", Py::Long(cs.uncomparable));
        Py::List objects;
        for (const auto& oc : cs.objects) {
            Py::Dict o;
            o.setItem("name", Py::String(oc.name));
            o.setItem("type", Py::String(oc.typeId));
            o.setItem("label", Py::String(oc.label));
            o.setItem("status", Py::String(statusName(oc.status)));
            o.setItem("replaced", Py::Boolean(oc.replaced));
            Py::List props;
            for (const auto& pe : oc.properties) {
                Py::Dict p;
                p.setItem("name", Py::String(pe.name));
                p.setItem("type", Py::String(pe.typeName));
                p.setItem("change", Py::String(changeName(pe.change)));
                if (pe.change == PropKind::Renamed) {
                    p.setItem("old_name", Py::String(pe.oldName));
                }
                if (pe.change == PropKind::Modified || pe.change == PropKind::Removed
                    || pe.change == PropKind::Added) {
                    Pending pd;
                    pd.entry = p;
                    pd.values = true;
                    pd.modified = pe.change == PropKind::Modified;
                    pd.typeName = pe.typeName;
                    pd.oldRaw = snapshotValue(pe.oldValue, pe.current, pe.typeName, jsonValues, pd.oldFinal);
                    pd.newRaw = snapshotValue(pe.current, nullptr, pe.typeName, jsonValues, pd.newFinal);
                    pending.push_back(std::move(pd));
                }
                props.append(p);
            }
            o.setItem("properties", props);
            objects.append(o);
        }
        out.setItem("objects", objects);
    });
    // Phase 2, no turn lock: convert the snapshot with the F1 value representation.
    Py::Object converter = Py::None();
    if (jsonValues) {
        try {
            Py::Module values(PyImport_ImportModule("AgentCore.state.values"), true);
            converter = values.getAttr("value_json");
        }
        catch (Py::Exception& e) {
            e.clear();  // AgentCore not available: raw Python values
        }
    }
    for (auto& pd : pending) {
        Py::Object oldJ = pd.oldFinal ? pd.oldRaw : convert(converter, pd.oldRaw, pd.typeName, removedNames);
        Py::Object newJ = pd.newFinal ? pd.newRaw : convert(converter, pd.newRaw, pd.typeName, removedNames);
        pd.entry.setItem("old", oldJ);
        pd.entry.setItem("new", newJ);
        // Property::isSame says "different" for a rebuilt shape that is in fact unchanged: such an
        // entry stays, flagged, when the two (complete) shape summaries are equal. No other type
        // is ever flagged: JSON rounds floats, truncates long lists and cannot express every type.
        if (pd.modified) {
            bool maybe = false;
            if (isShapeType(pd.typeName) && isShapeSummary(oldJ) && isShapeSummary(newJ)) {
                const int eq = PyObject_RichCompareBool(oldJ.ptr(), newJ.ptr(), Py_EQ);
                if (eq < 0) {
                    PyErr_Clear();
                }
                maybe = eq == 1;
            }
            pd.entry.setItem("maybe_unchanged", Py::Boolean(maybe));
        }
    }
    PyObject* r = out.ptr();
    Py_INCREF(r);
    return r;
}
