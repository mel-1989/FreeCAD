// SPDX-License-Identifier: LGPL-2.1-or-later

#include <atomic>
#include <deque>
#include <mutex>
#include <unordered_map>

#include "Application.h"
#include "Document.h"
#include "ElementReferenceEvents.h"

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
