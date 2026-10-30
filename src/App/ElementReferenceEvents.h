// SPDX-License-Identifier: LGPL-2.1-or-later

#ifndef APP_ELEMENTREFERENCEEVENTS_H
#define APP_ELEMENTREFERENCEEVENTS_H

#include <cstdint>
#include <string>
#include <vector>
#include <fastsignals/signal.h>
#include <FCGlobal.h>

namespace App
{

class Document;

/// What happened to one element reference when FreeCAD updated it.
struct AppExport ElementReferenceEvent
{
    enum class Kind
    {
        Remapped,  ///< the reference now designates a different short element name
        Lost,      ///< the reference newly entered the missing state
        Restored,  ///< a rescue record gave an earlier name back
    };
    enum class Basis
    {
        ElementMap,  ///< the element map itself carried the reference to its successor
        Geometry,    ///< a geometric search found the successor
        Anchored,    ///< an anchored search (G01 anchors) found the successor
        History,     ///< the element-map history found the successor
        Record,      ///< a rescue record was replayed
    };

    std::uint64_t seq {0};   ///< process-wide, strictly increasing
    std::string document;    ///< name of the owning document
    std::string owner;       ///< name of the object holding the link property
    std::string property;    ///< name of the link property
    std::string target;      ///< name of the linked object
    std::string oldSub;      ///< sub-element string before the update
    std::string newSub;      ///< sub-element string after the update
    std::string oldMapped;   ///< mapped (shadow) name before, may be empty
    std::string newMapped;   ///< mapped (shadow) name after, may be empty
    Kind kind {Kind::Remapped};
    Basis basis {Basis::ElementMap};

    static const char* kindName(Kind k);
    static const char* basisName(Basis b);
};

/**
 * Bounded per-document journal of element-reference updates.
 *
 * Purely observational: PropertyLinkBase::_updateElementReference records into it and
 * nothing in FreeCAD reads it back. A journal is dropped when its document is deleted.
 * All functions are thread-safe.
 */
class AppExport ElementReferenceJournal
{
public:
    /// Journals keep at most this many events per document; the oldest are dropped.
    static constexpr std::size_t MaxEventsPerDocument = 10000;

    /// Append an event (its seq is assigned here). Notifies observers of signalEvent.
    static void record(const Document* doc, ElementReferenceEvent event);
    /// Events of doc with seq > since, oldest first.
    static std::vector<ElementReferenceEvent> events(const Document* doc, std::uint64_t since = 0);
    /// The highest seq handed out so far in this process (0 if none); a valid `since` for later reads.
    static std::uint64_t lastSeq();
    static void clear(const Document* doc);

    /// Emitted after each recorded event, on the thread that recorded it.
    static fastsignals::signal<void(const ElementReferenceEvent&)> signalEvent;
};

}  // namespace App

#endif  // APP_ELEMENTREFERENCEEVENTS_H
