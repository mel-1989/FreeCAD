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

#ifndef APP_AGENTTURNGUARD_H
#define APP_AGENTTURNGUARD_H

#include <atomic>
#include <chrono>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include <FCGlobal.h>

/*
 * LOCK ORDER (the invariant of this file, Document's property hook, AgentTurnIntrospect and the
 * Python bindings): the GIL is OUTSIDE the turn mutex, strictly.
 *
 * 1. A thread takes the GIL first, then the turn mutex. AgentTurnGuard::acquire() does both
 *    (PyGILState_Ensure, then a plain blocking lock), so no caller can get the order wrong, and the
 *    returned AgentTurnLock gives the mutex back first and the GIL last.
 * 2. While the turn mutex is held no Python bytecode runs and the GIL is never given up or taken
 *    (no PyGILStateRelease, no Py_BEGIN_ALLOW_THREADS, no blocking wait for another thread).
 *    Py_INCREF/DECREF by the thread that holds the GIL is fine. Work that runs Python code
 *    (a transaction's apply, which fires FeaturePython onChanged; closing a transaction, which
 *    fires observers; comparing Python-object properties with __eq__) is done outside the mutex.
 * 3. A thread that holds the GIL therefore never waits long for the mutex: its holder holds the
 *    GIL as well and runs no Python, so it finishes without needing anything from the waiter.
 *    Nothing releases the GIL in the middle of a document mutation, so no other Python thread can
 *    close the document under a waiter.
 *
 * Work that must run Python on the turn's behalf outside the mutex marks the turn "busy"
 * (AgentTurnBusy): the other threads that need the turn meanwhile get a RuntimeError when they
 * hold the GIL (they must not wait for the busy thread, which needs the GIL to proceed) and wait
 * without it when they do not (the recompute worker).
 */

namespace App
{

class Document;
class Transaction;
class AgentTurnLock;

/**
 * @brief Per-document state of a guarded agent turn.
 *
 * An agent turn is one transaction T on one document. While it is active,
 * every route that would open, commit or abort a transaction on the document
 * (Document::openTransaction, setActiveTransaction, commitTransaction,
 * abortTransaction, Application::setActiveTransaction, openGlobalTransaction,
 * closeActiveTransaction, AutoTransaction, a GUI doCommand) becomes a
 * savepoint inside T instead of ending it:
 *
 * - a nested open parks the segment recorded so far and starts a fresh one;
 * - a nested commit keeps the changes (they are in T either way);
 * - a nested abort rolls back only the segment recorded since the nested open;
 * - an empty close, or a close with no matching open, is absorbed;
 * - at the end of the turn the segments are folded into one composite
 *   Transaction, which is committed as exactly one undo entry (or aborted,
 *   which restores the document).
 *
 * Undo, redo and clearUndos on the document are refused while the turn is
 * active and are reported as tampering.
 */
struct AgentTurnState
{
    int tid {0};
    std::string name;
    /// True while end() folds the turn: hooks fall through to the plain code.
    bool ending {false};
    /// Application-level transaction id a nested open came in through (0: none).
    int viaGlobalId {0};
    /// A nested open is waiting for its commit or abort.
    bool savepointOpen {false};
    bool announced {false};
    /// A close that arrived while the transactions were locked: 0 none, 1 commit, 2 abort.
    int pendingClose {0};
    std::size_t baseUndoCount {0};
    /// Guards the fields below and the active transaction against the recompute worker.
    std::recursive_mutex mutex;
    /// Run Python outside the mutex (nested abort, end()): the owner is busy, others must not touch the turn.
    std::atomic<int> busyDepth {0};
    std::atomic<std::thread::id> busyOwner {};
    /// How many AgentTurnLocks the owner of the mutex holds (changed under the mutex only).
    int lockDepth {0};
    /// The first change of the turn needs signalOpenTransaction; raised when the last lock is released.
    bool announceDue {false};
    Document* announceDoc {nullptr};
    /// Segments parked by nested opens, oldest first (owned).
    std::vector<Transaction*> parked;

    int nestedOpens {0};
    int nestedCommits {0};
    int nestedAborts {0};
    int strayCloses {0};
    int emptyCloses {0};
    int lockedOpens {0};
    int undoRefused {0};
    int redoRefused {0};
    int clearUndosRefused {0};
    int workerRefusals {0};
    std::vector<std::string> nestedNames;
    std::vector<std::string> tampering;

    AgentTurnState();
    AgentTurnState(const AgentTurnState&) = delete;
    AgentTurnState& operator=(const AgentTurnState&) = delete;
    ~AgentTurnState();
};

/// Result of AgentTurnGuard::end().
struct AgentTurnReport
{
    int tid {0};
    std::string name;
    /// end() could not finish (the document was recomputing on the worker, or a lock was held): the turn is still open.
    bool refused {false};
    std::string reason;
    bool committed {false};
    bool aborted {false};
    /// The turn recorded at least one change.
    bool recorded {false};
    /// Undo entries the turn added to the document's undo stack (0 or 1).
    int undoEntries {0};
    /// Segments folded into the one entry.
    int segments {0};
    int nestedOpens {0};
    int nestedCommits {0};
    int nestedAborts {0};
    int strayCloses {0};
    int emptyCloses {0};
    int lockedOpens {0};
    int undoRefused {0};
    int redoRefused {0};
    int clearUndosRefused {0};
    /// Savepoint operations skipped because the async worker was still recomputing the document.
    int workerRefusals {0};
    std::vector<std::string> nestedNames;
    std::vector<std::string> tampering;
};

/**
 * @brief Holds a turn's mutex and, beneath it, the GIL (see the lock order at the top of this file).
 *
 * Releasing the last lock a thread holds on a turn also raises a deferred signalOpenTransaction,
 * which runs observers (Python) and so must not run under the mutex.
 */
class AppExport AgentTurnLock
{
public:
    AgentTurnLock() = default;
    AgentTurnLock(const AgentTurnLock&) = delete;
    AgentTurnLock& operator=(const AgentTurnLock&) = delete;
    AgentTurnLock(AgentTurnLock&& other) noexcept;
    AgentTurnLock& operator=(AgentTurnLock&& other) noexcept;
    ~AgentTurnLock();

    bool owns_lock() const
    {
        return lock_.owns_lock();
    }
    /// Give the mutex back, then the GIL.
    void unlock();

private:
    friend class AgentTurnGuard;
    std::shared_ptr<AgentTurnState> state_;
    std::unique_lock<std::recursive_mutex> lock_;
    bool gil_ {false};
    int gilState_ {0};
};

/**
 * @brief Marks a turn busy while its owner runs Python outside the mutex (a nested abort's
 * rollback, the close of the turn's transaction). Clears the mark on destruction.
 */
class AppExport AgentTurnBusy
{
public:
    AgentTurnBusy() = default;
    AgentTurnBusy(const AgentTurnBusy&) = delete;
    AgentTurnBusy& operator=(const AgentTurnBusy&) = delete;
    ~AgentTurnBusy();

    /// Mark @p st busy for the calling thread. The caller holds the turn's lock.
    void engage(const std::shared_ptr<AgentTurnState>& st);
    void release() noexcept;

private:
    std::shared_ptr<AgentTurnState> st_;
};

class AppExport AgentTurnGuard
{
public:
    /// Longest a nested open, commit or abort waits for the worker.
    static constexpr std::chrono::milliseconds nestedWait {10000};

    /// Longest end() or begin() waits for the document's recompute on the worker.
    static constexpr std::chrono::milliseconds defaultWait {60000};

    /**
     * @brief Begin a guarded turn on @p doc and return its transaction id.
     *
     * Commits a transaction the document still has pending (the human's own
     * edit), waits for any recompute of the document on the async worker, and
     * books the turn transaction. The transaction itself opens lazily at the
     * first change, like any FreeCAD transaction.
     * @throw Base::RuntimeError when a turn is already active, the document is
     * undoing, redoing or committing, its transactions are locked, or the
     * worker does not finish within @p wait.
     */
    static int begin(Document& doc, const std::string& name, std::chrono::milliseconds wait = defaultWait);

    /**
     * @brief End the guarded turn on @p doc.
     *
     * Waits for the document's recompute on the worker (so nothing commits
     * halfway through it). When the worker does not finish within @p wait, or
     * the transactions are locked, the turn stays open and the report has
     * refused set. Otherwise commits the turn as exactly one undo entry (a
     * turn that recorded nothing leaves no entry and keeps the redo stack), or
     * aborts it and restores the document.
     * @throw Base::RuntimeError when no turn is active.
     */
    static AgentTurnReport end(Document& doc, bool commit, std::chrono::milliseconds wait = defaultWait);

    /// True when end() would not refuse now: not transacting, not locked, and the worker idle within @p wait. @p reason says why not.
    static bool canEnd(Document& doc, std::chrono::milliseconds wait, std::string& reason);

    /// Wait until the async worker is not recomputing @p doc (false on timeout). Callers that hold the Python GIL use the variant below.
    static bool waitForWorker(Document& doc, std::chrono::milliseconds wait);
    /**
     * @brief waitForWorker() for callers that hold the GIL: gives it up for the wait.
     *
     * Never called with a turn mutex held. The wait is a poll in slices of 50 ms; the GIL is given
     * up for each slice and the document is looked up again after every wake, because another
     * Python thread may close it meanwhile (nothing of @p doc is read while the GIL is away).
     * @throw Base::RuntimeError when the document no longer exists.
     */
    static bool waitForWorkerReleasingGil(Document& doc, std::chrono::milliseconds wait);

    /// The turn's counters and tampering so far, without ending it (default report when no turn is active).
    static AgentTurnReport peek(Document& doc);

    /// True while a guarded turn is active on @p doc (also during end()).
    static bool isActive(const Document& doc);

    // ---- hooks called from Document and Application -----------------------------------------

    /// Transaction id of the active turn when its hooks apply (0: no turn, or the turn is ending).
    static int turnId(const Document& doc);
    /// True when @p id is the active turn's id or the global id a nested open came in through.
    static bool ownsId(const Document& doc, int id);
    /// A transaction was opened (setActiveTransaction, openTransaction ...): open a savepoint. Returns the turn id.
    static int nestedOpen(Document& doc, const std::string& name, int viaGlobalId = 0);
    /// A transaction was committed: release the savepoint.
    static bool nestedCommit(Document& doc);
    /**
     * A transaction was aborted: detach the segment recorded since the savepoint and return it, or
     * null when there is nothing to roll back. The caller (Document::_abortTransaction) applies it
     * backwards under the rollback flag and deletes it. The apply runs Python (FeaturePython
     * onChanged), so it runs OUTSIDE the turn mutex: @p busy marks the turn busy meanwhile, and
     * other threads that need the turn are refused (GIL holders) or wait (the worker).
     */
    static Transaction* beginNestedAbort(Document& doc, AgentTurnBusy& busy);
    /// The first change after a savepoint needs an active transaction: open the segment. Returns the turn id.
    static int ensureSegment(Document& doc);
    /// A close arrived while the transactions were locked: remember it.
    static void deferClose(Document& doc, bool abort);
    /// The transaction lock was released: take the close remembered by deferClose (0 none, 1 commit, 2 abort).
    static int takeDeferredClose(Document& doc);
    /// undo, redo or clearUndos was called: record tampering and refuse.
    static bool refuseHistory(Document& doc, const char* what);
    /// The document is being destroyed: drop the turn.
    static void onDocumentDestroy(Document& doc);
    /// Lock that serialises the recompute worker against turn bookkeeping (empty when no turn).
    static AgentTurnLock lockFor(Document& doc);

    /**
     * @brief Take the GIL, then the turn's mutex (blocking), and keep @p st alive meanwhile.
     *
     * The only way to lock a turn. When another thread has marked the turn busy (AgentTurnBusy)
     * this refuses with Base::RuntimeError if the caller holds the GIL (waiting would deadlock:
     * the busy thread needs the GIL), and polls without the GIL otherwise. @p respectBusy false
     * skips that check (the document is being destroyed).
     */
    static AgentTurnLock acquire(const std::shared_ptr<AgentTurnState>& st, bool respectBusy = true);
};

}  // namespace App

#endif  // APP_AGENTTURNGUARD_H
