// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2002 Jürgen Riegel <juergen.riegel@web.de>              *
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

#pragma once

#ifdef _MSC_VER
#pragma warning(disable : 4834)
#endif

#include <array>
#include <atomic>
#include <map>
#include <string>
#include <memory>
#include <thread>
#include <vector>
#include <unordered_map>
#include <unordered_set>
#include <optional>

#include <boost/bimap.hpp>
#include <boost/graph/adjacency_list.hpp>

#include <CXX/Objects.hxx>

#include <Base/Exception.h>

#include <App/DocumentObject.h>
#include <App/DocumentObserver.h>
#include <App/StringHasher.h>
#include <App/ExportInfo.h>
#include <Base/UniqueNameManager.h>

// using VertexProperty = boost::property<boost::vertex_root_t, DocumentObject* >;
using DependencyList = boost::adjacency_list<
    boost::vecS,         // class OutEdgeListS  : a Sequence or an AssociativeContainer
    boost::vecS,         // class VertexListS   : a Sequence or a RandomAccessContainer
    boost::directedS,    // class DirectedS     : This is a directed graph
    boost::no_property,  // class VertexProperty:
    boost::no_property,  // class EdgeProperty:
    boost::no_property,  // class GraphProperty:
    boost::listS         // class EdgeListS:
    >;
using Traits = boost::graph_traits<DependencyList>;
using Vertex = Traits::vertex_descriptor;
using Edge = Traits::edge_descriptor;
using Node = std::vector<size_t>;
using Path = std::vector<size_t>;

namespace App
{
using HasherMap = boost::bimap<StringHasherRef, int>;
class Transaction;
struct AgentTurnState;

/// Operations that must not be interrupted by closing the document (Document::closeRefusal).
enum class DocOp
{
    Transaction,   ///< open, commit, abort, undo, redo or a history clear (runs observers and Python)
    Recompute,     ///< a recompute (Python features execute)
    TurnBusy,      ///< an agent turn's begin or end
    PropertyHook,  ///< a thread inside the pre-change property hook, possibly parked on the turn lock
    Count
};

// Pimpl class
struct DocumentP
{
    /// How many threads are inside each DocOp right now. Atomic: read by the thread that closes.
    std::array<std::atomic<int>, static_cast<std::size_t>(DocOp::Count)> activeOps {};
    /// Set by the thread that closes the document (beginClose) until the document is freed, or
    /// until the close is refused. Operations that find it set back out (see OpScope).
    std::atomic<bool> closing {false};
    /// The thread that set @c closing; it alone may still count itself into OnClosing::CloserMay scopes.
    std::atomic<std::thread::id> closer {};
    // Array to preserve the creation order of created objects
    std::vector<DocumentObject*> objectArray;
    std::unordered_set<App::DocumentObject*> touchedObjs;
    std::unordered_map<std::string, DocumentObject*> objectMap;
    Base::UniqueNameManager objectNameManager;
    Base::UniqueNameManager objectLabelManager;
    std::unordered_map<long, DocumentObject*> objectIdMap;
    std::unordered_map<std::string, bool> partialLoadObjects;
    std::vector<DocumentObjectT> pendingRemove;
    long lastObjectId {};
    DocumentObject* activeObject {nullptr};
    Transaction* activeUndoTransaction {nullptr};
    // State of a guarded agent turn on this document, if one is active (AgentTurnGuard.h)
    std::shared_ptr<AgentTurnState> agent;
    // pointer to the python class
    Py::Object DocumentPythonObject;
    int iTransactionMode {0};
    bool rollback {false};
    bool undoing {false};  ///< document in the middle of undo or redo
    bool committing {false};
    bool definingTransaction {false};
    bool opentransaction {false};
    std::bitset<32> StatusBits;
    unsigned int UndoMemSize {0};
    unsigned int UndoMaxStackSize {20};
    unsigned int TransactionLock {0};
    // Id and name that the next transaction will take
    // as soon as there is a change to the document
    int bookedTransaction {0};

    std::string programVersion;
    mutable HasherMap hashers;
    std::multimap<const App::DocumentObject*, std::unique_ptr<App::DocumentObjectExecReturn>>
        _RecomputeLog;
    ExportInfo exportInfo;

    StringHasherRef Hasher {new StringHasher};

    DocumentP();

    /// The operation that is in progress on the document, or an empty string when none is.
    std::string activeOpReason() const
    {
        const auto active = [this](DocOp op) {
            return activeOps[static_cast<std::size_t>(op)].load() > 0;
        };
        if (active(DocOp::Transaction)) {
            return "a transaction (open, commit, abort, undo or redo) is in progress on it";
        }
        if (active(DocOp::Recompute)) {
            return "a recompute is in progress on it";
        }
        if (active(DocOp::TurnBusy)) {
            return "an agent turn is busy on it";
        }
        if (active(DocOp::PropertyHook)) {
            return "a thread is changing a property of it (parked on the agent turn)";
        }
        return {};
    }

    /**
     * The closing thread's half of the handshake with OpScope (Dekker): publish @c closing, then
     * read the counters. OpScope does the reverse (count, then read @c closing), so at least one
     * side sees the other and an operation can never start unseen under a close. Both sides use
     * the default sequentially consistent atomic operations; relaxed or acquire/release ones
     * would let both loads miss the other store.
     *
     * Returns an empty string when the close may go on (@c closing stays set until the document
     * is freed), else the reason, with @c closing cleared again so a refused close leaves the
     * document fully usable.
     */
    std::string beginClose()
    {
        bool expected = false;
        if (!closing.compare_exchange_strong(expected, true)) {
            return "it is already being closed (a re-entrant close, for instance from an observer)";
        }
        closer.store(std::this_thread::get_id());
        std::string why = activeOpReason();
        if (!why.empty()) {
            abortClose();
        }
        return why;
    }

    /// Undo beginClose after the close was given up.
    void abortClose()
    {
        closer.store(std::thread::id {});
        closing.store(false);
    }

    /// What an OpScope does when the document is being closed.
    enum class OnClosing
    {
        Throw,     ///< throw Base::RuntimeError (becomes a Python RuntimeError)
        Refuse,    ///< no throw: the scope reports refused() and the caller returns without acting
        CloserMay  ///< same as Refuse for other threads (kept to mark sites the destructor itself reaches)
    };

    /**
     * Marks an operation on the document for the lifetime of the object (re-entrant, any thread)
     * and refuses it when the document is being closed.
     *
     * The closing thread itself is never refused, whatever the policy; only other threads are,
     * so Refuse and CloserMay differ only in name now, and Throw differs from them only for
     * other threads (it throws rather than returning).
     *
     * Order matters: increment the counter, then read @c closing, and back out of the counter
     * when it is set. A throwing policy is only safe where the caller may throw (a Python-facing
     * entry point); a destructor, an observer or a redo clear running in a destructor would
     * std::terminate on it, so those use Refuse and test refused().
     *
     * Every construction site and its policy (the table is in the run 023 note):
     *
     * | site | policy |
     * |---|---|
     * | Document::openTransaction (public), commitTransaction, abortTransaction | Throw |
     * | Document::undo, redo | Throw |
     * | Document::recompute | Throw (the async worker catches it and reports it) |
     * | AgentTurnGuard::begin, end | Throw |
     * | Document::_openTransaction | Refuse (returns 0) |
     * | Document::_commitTransaction | CloserMay (returns false; the destructor's clearUndos reaches it) |
     * | Document::_abortTransaction | Refuse (returns) |
     * | Document::_clearRedos | CloserMay (returns, nothing cleared; the destructor's clearUndos reaches it) |
     * | Document::clearUndos | CloserMay (the destructor's own call must run) |
     * | Document::onBeforeChangeProperty | CloserMay (object destructors set properties; others skip the hook) |
     */
    class OpScope
    {
    public:
        OpScope(DocumentP& dp, DocOp op, OnClosing policy)
            : counter(dp.activeOps[static_cast<std::size_t>(op)])
        {
            ++counter;
            if (!dp.closing.load()) {
                return;
            }
            // The closing thread runs the destructor, the observers and the __del__ code of the
            // close; it is let through every scope (it only counts itself in). The double free is
            // prevented by beginClose's compare-exchange, not by the scopes.
            // An empty closer means the closing thread has set the flag but not yet recorded its
            // id: it has not read the counters yet either (it stores the id first), so letting
            // this operation through is safe, because its counter increment is already visible to
            // that read. Refusing it instead could refuse the closing thread itself.
            const std::thread::id closerId = dp.closer.load();
            if (closerId == std::this_thread::get_id() || closerId == std::thread::id {}) {
                return;
            }
            --counter;
            held = false;
            if (policy == OnClosing::Throw) {
                throw Base::RuntimeError("the document is being closed");
            }
        }
        OpScope(const OpScope&) = delete;
        OpScope& operator=(const OpScope&) = delete;
        ~OpScope()
        {
            if (held) {
                --counter;
            }
        }
        /// True when the document is being closed and the operation must not go on (not thrown).
        bool refused() const
        {
            return !held;
        }

    private:
        std::atomic<int>& counter;
        bool held {true};
    };

    void addRecomputeLog(const char* why, App::DocumentObject* obj)
    {
        addRecomputeLog(new DocumentObjectExecReturn(why, obj));
    }

    void addRecomputeLog(const std::string& why, App::DocumentObject* obj)
    {
        addRecomputeLog(new DocumentObjectExecReturn(why, obj));
    }

    void addRecomputeLog(DocumentObjectExecReturn* returnCode)
    {
        if (!returnCode->Which) {
            delete returnCode;
            return;
        }
        _RecomputeLog.emplace(returnCode->Which,
                              std::unique_ptr<DocumentObjectExecReturn>(returnCode));
        returnCode->Which->setStatus(ObjectStatus::Error, true);
    }

    void clearRecomputeLog(const App::DocumentObject* obj = nullptr)
    {
        if (!obj) {
            _RecomputeLog.clear();
        }
        else {
            _RecomputeLog.erase(obj);
        }
    }

    void clearDocument()
    {
        objectLabelManager.clear();
        objectArray.clear();
        for (auto& v : objectMap) {
            v.second->setStatus(ObjectStatus::Destroy, true);
            delete (v.second);
            v.second = nullptr;
        }
        objectMap.clear();
        objectNameManager.clear();
        objectIdMap.clear();
    }

    const char* findRecomputeLog(const App::DocumentObject* obj)
    {
        auto range = _RecomputeLog.equal_range(obj);
        if (range.first == range.second) {
            return nullptr;
        }
        return (--range.second)->second->Why.c_str();
    }

    static void findAllPathsAt(const std::vector<Node>& all_nodes,
                               size_t id,
                               std::vector<Path>& all_paths,
                               Path tmp);
    std::vector<App::DocumentObject*>
    topologicalSort(const std::vector<App::DocumentObject*>& objects) const;
    static std::vector<App::DocumentObject*>
    partialTopologicalSort(const std::vector<App::DocumentObject*>& objects);
    static void checkStringHasher(const Base::XMLReader& reader);
};

}  // namespace App
