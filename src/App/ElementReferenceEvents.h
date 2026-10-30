// SPDX-License-Identifier: LGPL-2.1-or-later

#ifndef APP_ELEMENTREFERENCEEVENTS_H
#define APP_ELEMENTREFERENCEEVENTS_H

#include <cstdint>
#include <memory>
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
        Added,     ///< undo, redo or rollback created a reference (a list grew, an object came back)
        Removed,   ///< undo, redo or rollback dropped a reference (a list shrank, an object went)
    };
    enum class Basis
    {
        ElementMap,  ///< the element map itself carried the reference to its successor
        Geometry,    ///< a geometric search found the successor
        Anchored,    ///< an anchored search (G01 anchors) found the successor
        History,     ///< the element-map history found the successor
        Record,      ///< a rescue record was replayed
        Undo,        ///< Document::undo restored the reference
        Redo,        ///< Document::redo restored the reference
        Rollback,    ///< an aborted transaction or an agent-turn savepoint restored the reference
    };

    std::uint64_t seq {0};   ///< process-wide, strictly increasing
    std::string document;    ///< name of the owning document
    std::string owner;       ///< name of the object holding the link property
    std::string property;    ///< name of the link property
    std::string target;      ///< name of the linked object (after the update)
    std::string oldTarget;   ///< name of the linked object before; only set by undo, redo and rollback events
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
 * Body relinking, deleting a feature and user assignments rewrite properties
 * without an event (undo, redo and rollback do emit events, see below), so a consumer must read the property for its current value and use the
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
 * - Undo, redo, abort and an agent-turn savepoint rollback do emit events (basis `undo`, `redo`,
 *   `rollback`): the link properties of the document are compared before and after the history
 *   step and every flattened reference that differs gets one event, so the journal and the
 *   document agree after a history step. A reference at the same position with another target,
 *   sub or mapped name is `remapped` (`lost` when it newly carries the missing mark, `restored`
 *   when it loses it); a position that only exists after is `added` (oldSub empty) and one that
 *   only existed before is `removed` (newSub empty), which also covers objects that undo creates
 *   or deletes. Positions are compared index by index, so a list that loses its first entry
 *   reports every later position as changed and the last as removed. The reference updates that
 *   the step itself triggers are not recorded separately, so the same change is never reported
 *   twice; a recompute after the step records its own events as usual. `oldTarget` is set only
 *   on these events.
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
    /// True while the calling thread is inside an ElementReferenceHistoryScope; the per-update
    /// recorder stays quiet then, because the scope reports the net change itself.
    static bool suppressed();

    /// Emitted after each recorded event, on the thread that recorded it.
    static fastsignals::signal<void(const ElementReferenceEvent&)> signalEvent;
};

/**
 * RAII bracket around one history step (undo, redo, abort, savepoint rollback). The constructor
 * snapshots the element references of every link property of the document, finish() (or the
 * destructor) snapshots again and records one event per flattened reference that differs, with
 * the given basis (Undo, Redo or Rollback). Purely observational: it only reads properties.
 * While it lives, the per-update recorder of PropertyLinkBase is suppressed on this thread.
 */
class AppExport ElementReferenceHistoryScope
{
public:
    ElementReferenceHistoryScope(const Document& doc, ElementReferenceEvent::Basis basis);
    ~ElementReferenceHistoryScope();
    ElementReferenceHistoryScope(const ElementReferenceHistoryScope&) = delete;
    ElementReferenceHistoryScope& operator=(const ElementReferenceHistoryScope&) = delete;

    /// Compare and record now (idempotent); the destructor calls it if you did not.
    void finish();

private:
    void release();
    struct Impl;
    std::unique_ptr<Impl> impl;
};

}  // namespace App

#endif  // APP_ELEMENTREFERENCEEVENTS_H
