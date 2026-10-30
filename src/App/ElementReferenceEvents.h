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
    std::string target;      ///< name of the linked object (after the update)
    std::string targetDocument;  ///< name of the document of the linked object
    int subIndex {-1};       ///< position of the sub in the property (see below), -1 if unknown
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
 *
 * What the journal is, and is not. It is an incremental log of the rewrites that
 * _updateElementReference performed, in order. It is not a replayable history of the property:
 * undo and redo, Body relinking, deleting a feature and user assignments rewrite properties
 * without an event, so a consumer must read the property for its current value and use the
 * events only to learn what FreeCAD did and why. Further caveats:
 * - `lost` means the reference's mapped name is gone. The visible sub may still show the old
 *   index, because updateLinkReference writes the mapped name back over the "?" mark.
 * - `restored` is also used when a reference recovers from the missing mark (`?Edge5` to
 *   `Edge5`), and a `restored` event may have an unchanged short sub (only the mapped name
 *   changed).
 * - `subIndex` is the flattened position of the sub within its property: the index in the sub
 *   list for PropertyLinkSub and PropertyLinkSubList, and for PropertyXLinkSubList the number of
 *   subs (an empty sub list counts as one) of the earlier children plus the index within the
 *   child. It matches the order in which Python flattens the property value, and it is the same
 *   for every event of one update pass, so (owner, property, subIndex, oldSub) identifies the
 *   reference an event belongs to.
 * - A reference may leave and return within one edit (`Edge8`, `?Edge8`, `Edge8`); every step
 *   is logged.
 * - The journal keeps at most MaxEventsPerDocument events. lastDroppedSeq() tells a reader
 *   whether events older than its `since` were discarded (truncated when since < that value).
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
    /// Highest seq discarded from doc's journal by the size cap (0 if none, reset by clear).
    static std::uint64_t lastDroppedSeq(const Document* doc);
    static void clear(const Document* doc);

    /// Emitted after each recorded event, on the thread that recorded it.
    static fastsignals::signal<void(const ElementReferenceEvent&)> signalEvent;
};

}  // namespace App

#endif  // APP_ELEMENTREFERENCEEVENTS_H
