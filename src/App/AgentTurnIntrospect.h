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

#ifndef APP_AGENTTURNINTROSPECT_H
#define APP_AGENTTURNINTROSPECT_H

#include <chrono>
#include <string>
#include <type_traits>
#include <vector>

#include <FCGlobal.h>

struct _object;

namespace App
{

class Document;
class DocumentObject;
class Property;
class Transaction;

/**
 * @brief What a guarded agent turn changed, read from FreeCAD's own transaction records.
 *
 * The turn's segments (those parked by nested opens plus the active one; nested
 * aborts already detached and rolled back theirs) are walked oldest first. The
 * oldest record of a property holds its value at the start of the turn; the
 * current value is read from the live object. See AgentTurnIntrospect.cpp for the
 * rules (added-then-removed objects are omitted, a property restored to its old
 * value is dropped, ...).
 */
struct AgentObjectChange
{
    enum class Status
    {
        Added,
        Removed,
        Modified
    };
    enum class PropertyChange
    {
        Modified,
        Added,    ///< a dynamic property created in the turn
        Removed,  ///< a dynamic property deleted in the turn
        Renamed,
        Moved
    };
    struct PropertyEntry
    {
        std::string name;
        std::string typeName;
        PropertyChange change {PropertyChange::Modified};
        /// Renamed: the earlier name.
        std::string oldName;
        /// Copy of the value at the start of the turn (null: the property did not exist). Not owned.
        const Property* oldValue {nullptr};
        /// The live property (null: it no longer exists). Not owned.
        const Property* current {nullptr};
        /**
         * Both values are Python-object properties and whether they differ is undecided: comparing
         * them runs Python (__eq__), which must not happen under the turn mutex. The entry is listed
         * as modified; asPython() decides after the lock is released (identical object: dropped;
         * equal by ==: kept and flagged maybe_unchanged).
         */
        bool pyCompare {false};
    };

    std::string name;
    std::string typeId;
    std::string label;
    /// The object itself (for a removed one, the instance the transaction keeps). Not owned.
    const DocumentObject* object {nullptr};
    Status status {Status::Modified};
    /// Another object of the same name was removed (when added) or created (when removed) in the turn.
    bool replaced {false};
    std::vector<PropertyEntry> properties;
};

struct AgentTurnChangeSet
{
    int tid {0};
    std::string turnName;
    int segments {0};
    std::vector<AgentObjectChange> objects;
    /// Properties the walker could not compare (isSame threw); they are reported as modified.
    int uncomparable {0};
};

class AppExport AgentTurnIntrospect
{
public:
    /**
     * @brief Collect the changes of the active guarded turn on @p doc.
     *
     * Waits for the async recompute worker to be idle (@p wait; the GIL is released for the
     * wait when the caller holds it) and holds the turn mutex while reading, so the worker cannot
     * record meanwhile. The visitor runs under that lock, so the Property pointers it receives are
     * valid only during the call, and it must not run Python code (see the lock order in
     * AgentTurnGuard.h).
     * @throw Base::RuntimeError when no turn is active or the worker does not become idle.
     */
    template<class Visitor>
    static void collect(Document& doc, std::chrono::milliseconds wait, Visitor&& visit);

    /**
     * @brief Python view of collect(): a dict with the turn and its objects.
     *
     * Values are converted to JSON-safe Python values through AgentCore.state.values (the
     * representation F1's dump uses) when @p jsonValues is set and AgentCore is importable;
     * otherwise the raw Python value of the property is returned. Returns a new reference.
     * @throw Base::RuntimeError as collect() does.
     */
    static _object* asPython(Document& doc, bool jsonValues, std::chrono::milliseconds wait);

    /// The same as collect(), type-erased (used by the template and the tests).
    using Callback = void (*)(const AgentTurnChangeSet&, void*);
    static void collectWith(Document& doc, std::chrono::milliseconds wait, Callback cb, void* user);
};

template<class Visitor>
void AgentTurnIntrospect::collect(Document& doc, std::chrono::milliseconds wait, Visitor&& visit)
{
    collectWith(
        doc,
        wait,
        [](const AgentTurnChangeSet& cs, void* user) { (*static_cast<std::remove_reference_t<Visitor>*>(user))(cs); },
        &visit
    );
}

}  // namespace App

#endif  // APP_AGENTTURNINTROSPECT_H
