// SPDX-License-Identifier: LGPL-2.1-or-later

#include <algorithm>
#include <atomic>
#include <deque>
#include <map>
#include <mutex>
#include <unordered_map>

#include <Base/BaseClass.h>

#include "Application.h"
#include "Document.h"
#include "DocumentObject.h"
#include "ElementNamingUtils.h"
#include "ElementReferenceEvents.h"
#include "PropertyLinks.h"

using namespace App;

namespace
{
struct JournalState
{
    std::mutex mutex;
    std::unordered_map<const Document*, std::deque<ElementReferenceEvent>> journals;
    std::unordered_map<const Document*, std::uint64_t> droppedThrough;
    std::atomic<std::uint64_t> seq {0};
    fastsignals::scoped_connection deleteConnection;
    bool connected {false};
};

// Never destroyed: observers may run during static destruction.
JournalState& state()
{
    static JournalState* s = new JournalState;
    return *s;
}
}  // namespace

fastsignals::signal<void(const ElementReferenceEvent&)> ElementReferenceJournal::signalEvent;

const char* ElementReferenceEvent::kindName(Kind k)
{
    switch (k) {
        case Kind::Remapped:
            return "remapped";
        case Kind::Lost:
            return "lost";
        case Kind::Restored:
            return "restored";
        case Kind::Added:
            return "added";
        case Kind::Removed:
            return "removed";
    }
    return "";
}

const char* ElementReferenceEvent::basisName(Basis b)
{
    switch (b) {
        case Basis::ElementMap:
            return "element_map";
        case Basis::Geometry:
            return "geometry";
        case Basis::Anchored:
            return "anchored";
        case Basis::History:
            return "history";
        case Basis::Record:
            return "record";
        case Basis::Undo:
            return "undo";
        case Basis::Redo:
            return "redo";
        case Basis::Rollback:
            return "rollback";
    }
    return "";
}

void ElementReferenceJournal::record(const Document* doc, ElementReferenceEvent event)
{
    if (!doc) {
        return;
    }
    auto& s = state();
    {
        std::lock_guard<std::mutex> lock(s.mutex);
        if (!s.connected) {
            s.connected = true;
            s.deleteConnection = App::GetApplication().signalDeleteDocument.connect(
                [](const Document& d) { ElementReferenceJournal::clear(&d); });
        }
        event.seq = ++s.seq;
        auto& journal = s.journals[doc];
        journal.push_back(event);
        while (journal.size() > MaxEventsPerDocument) {
            s.droppedThrough[doc] = journal.front().seq;
            journal.pop_front();
        }
    }
    signalEvent(event);
}

std::vector<ElementReferenceEvent> ElementReferenceJournal::events(const Document* doc,
                                                                   std::uint64_t since)
{
    auto& s = state();
    std::lock_guard<std::mutex> lock(s.mutex);
    std::vector<ElementReferenceEvent> out;
    auto it = s.journals.find(doc);
    if (it == s.journals.end()) {
        return out;
    }
    for (const auto& e : it->second) {
        if (e.seq > since) {
            out.push_back(e);
        }
    }
    return out;
}

std::uint64_t ElementReferenceJournal::lastSeq()
{
    return state().seq.load();
}

void ElementReferenceJournal::clear(const Document* doc)
{
    auto& s = state();
    std::lock_guard<std::mutex> lock(s.mutex);
    s.journals.erase(doc);
    s.droppedThrough.erase(doc);
}

std::uint64_t ElementReferenceJournal::lastDroppedSeq(const Document* doc)
{
    auto& s = state();
    std::lock_guard<std::mutex> lock(s.mutex);
    auto it = s.droppedThrough.find(doc);
    return it == s.droppedThrough.end() ? 0 : it->second;
}

namespace
{
// Documents with a live history scope on this thread (a document may appear more than once).
thread_local std::vector<const Document*> g_historyDocs;

// One flattened element reference, as the Python side flattens a link property: a link with no
// subs counts as one reference with an empty sub.
struct FlatRef
{
    std::string target;
    std::string targetDocument;
    std::string sub;
    std::string mapped;

    bool operator==(const FlatRef& o) const
    {
        return target == o.target && targetDocument == o.targetDocument && sub == o.sub
            && mapped == o.mapped;
    }
};

using Key = std::pair<std::string, std::string>;  // owner, property
using Snapshot = std::map<Key, std::vector<FlatRef>>;

void flatten(std::vector<FlatRef>& out,
             const DocumentObject* obj,
             const std::vector<std::string>& subs,
             const std::vector<PropertyLinkBase::ShadowSub>& shadows)
{
    if (!obj) {
        return;
    }
    FlatRef base;
    if (obj->getNameInDocument()) {
        base.target = obj->getNameInDocument();
    }
    if (obj->getDocument()) {
        base.targetDocument = obj->getDocument()->getName();
    }
    if (subs.empty()) {
        out.push_back(base);
        return;
    }
    for (std::size_t i = 0; i < subs.size(); ++i) {
        FlatRef r = base;
        r.sub = subs[i];
        if (i < shadows.size()) {
            r.mapped = shadows[i].newName;
        }
        out.push_back(std::move(r));
    }
}

void flattenProperty(std::vector<FlatRef>& out, Property* prop, bool& known)
{
    known = true;
    if (auto* l = freecad_cast<PropertyXLinkSubList*>(prop)) {
        for (const auto& child : l->getSubListValues()) {
            flatten(out, child.getValue(), child.getSubValues(), child.getShadowSubs());
        }
    }
    else if (auto* x = freecad_cast<PropertyXLink*>(prop)) {
        flatten(out, x->getValue(), x->getSubValues(), x->getShadowSubs());
    }
    else if (auto* ls = freecad_cast<PropertyLinkSubList*>(prop)) {
        const auto& objs = ls->getValues();
        const auto& subs = ls->getSubValues();
        const auto& shadows = ls->getShadowSubs();
        for (std::size_t i = 0; i < objs.size(); ++i) {
            // one entry per object/sub pair, in list order
            std::vector<std::string> one {i < subs.size() ? subs[i] : std::string()};
            std::vector<PropertyLinkBase::ShadowSub> sh;
            if (i < shadows.size()) {
                sh.push_back(shadows[i]);
            }
            flatten(out, objs[i], one, sh);
        }
    }
    else if (auto* s = freecad_cast<PropertyLinkSub*>(prop)) {
        flatten(out, s->getValue(), s->getSubValues(), s->getShadowSubs());
    }
    else {
        known = false;
    }
}

Snapshot takeSnapshot(const Document& doc)
{
    Snapshot snap;
    std::vector<Property*> props;
    for (const auto* obj : doc.getObjects()) {
        if (!obj || !obj->getNameInDocument()) {
            continue;
        }
        props.clear();
        obj->getPropertyList(props);
        for (auto* prop : props) {
            std::vector<FlatRef> refs;
            bool known = false;
            flattenProperty(refs, prop, known);
            if (known && !refs.empty() && prop->getName()) {
                snap[{obj->getNameInDocument(), prop->getName()}] = std::move(refs);
            }
        }
    }
    return snap;
}

std::string shortOf(const std::string& sub)
{
    const char* e = Data::findElementName(sub.c_str());
    return (e && e[0] && !Data::isMappedElement(e)) ? e : "";
}
}  // namespace

bool ElementReferenceJournal::suppressed(const Document* doc)
{
    return std::find(g_historyDocs.begin(), g_historyDocs.end(), doc) != g_historyDocs.end();
}

struct ElementReferenceHistoryScope::Impl
{
    const Document* doc;
    ElementReferenceEvent::Basis basis;
    Snapshot before;
    bool done {false};
    bool suppressing {false};
};

ElementReferenceHistoryScope::ElementReferenceHistoryScope(const Document& doc,
                                                           ElementReferenceEvent::Basis basis)
    : impl(std::make_unique<Impl>())
{
    impl->doc = &doc;
    impl->basis = basis;
    g_historyDocs.push_back(&doc);
    impl->suppressing = true;
    try {
        impl->before = takeSnapshot(doc);
    }
    catch (...) {
        impl->done = true;
    }
}

ElementReferenceHistoryScope::~ElementReferenceHistoryScope()
{
    try {
        finish();
    }
    catch (...) {
    }
    release();
}

void ElementReferenceHistoryScope::release()
{
    if (impl->suppressing) {
        impl->suppressing = false;
        auto it = std::find(g_historyDocs.rbegin(), g_historyDocs.rend(), impl->doc);
        if (it != g_historyDocs.rend()) {
            g_historyDocs.erase(std::next(it).base());
        }
    }
}

void ElementReferenceHistoryScope::finish()
{
    // observational only: a failure here (bad_alloc) must not turn a successful history step
    // into an exception
    try {
        compareAndRecord();
    }
    catch (...) {
        release();
    }
}

void ElementReferenceHistoryScope::compareAndRecord()
{
    using Event = ElementReferenceEvent;
    if (impl->done) {
        release();
        return;
    }
    impl->done = true;
    // the recorder resumes as soon as the net change is measured, so observers of the history
    // signals that trigger reference updates are journaled as usual
    const Snapshot after = [this] {
        try {
            auto snap = takeSnapshot(*impl->doc);
            release();
            return snap;
        }
        catch (...) {
            release();
            throw;
        }
    }();
    const Snapshot& before = impl->before;
    static const std::vector<FlatRef> none;

    // owners in both snapshots, in a deterministic order
    std::vector<Key> keys;
    for (const auto& kv : before) {
        keys.push_back(kv.first);
    }
    for (const auto& kv : after) {
        if (!before.count(kv.first)) {
            keys.push_back(kv.first);
        }
    }
    std::sort(keys.begin(), keys.end());

    const std::string docName = impl->doc->getName();
    for (const auto& key : keys) {
        auto bi = before.find(key);
        auto ai = after.find(key);
        const auto& b = bi == before.end() ? none : bi->second;
        const auto& a = ai == after.end() ? none : ai->second;
        const std::size_t n = std::max(a.size(), b.size());
        for (std::size_t i = 0; i < n; ++i) {
            const bool hasOld = i < b.size();
            const bool hasNew = i < a.size();
            if (hasOld && hasNew && b[i] == a[i]) {
                continue;
            }
            Event ev;
            ev.basis = impl->basis;
            ev.document = docName;
            ev.owner = key.first;
            ev.property = key.second;
            ev.subIndex = static_cast<int>(i);
            if (hasOld) {
                ev.oldTarget = b[i].target;
                ev.oldSub = b[i].sub;
                ev.oldMapped = b[i].mapped;
            }
            if (hasNew) {
                ev.target = a[i].target;
                ev.targetDocument = a[i].targetDocument;
                ev.newSub = a[i].sub;
                ev.newMapped = a[i].mapped;
            }
            else {
                ev.targetDocument = b[i].targetDocument;
            }
            if (!hasOld) {
                ev.kind = Event::Kind::Added;
            }
            else if (!hasNew) {
                ev.kind = Event::Kind::Removed;
            }
            else {
                const bool wasMissing = shortOf(b[i].sub).rfind('?', 0) == 0
                    || b[i].sub.rfind('?', 0) == 0;
                const bool isMissing = shortOf(a[i].sub).rfind('?', 0) == 0
                    || a[i].sub.rfind('?', 0) == 0;
                ev.kind = (isMissing && !wasMissing) ? Event::Kind::Lost
                    : (wasMissing && !isMissing)     ? Event::Kind::Restored
                                                     : Event::Kind::Remapped;
            }
            ElementReferenceJournal::record(impl->doc, std::move(ev));
        }
    }
}
