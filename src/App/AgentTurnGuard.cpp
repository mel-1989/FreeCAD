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
#include <chrono>
#include <format>
#include <list>
#include <optional>
#include <thread>
#include <utility>

#include <Base/Exception.h>
#include <Base/Interpreter.h>
#include <Base/Tools.h>

#include "AgentTurnGuard.h"
#include "Application.h"
#include "AutoTransaction.h"
#include "Document.h"
#include "Transactions.h"
#include "private/DocumentP.h"

FC_LOG_LEVEL_INIT("App", true, true)

using namespace App;

AgentTurnState::AgentTurnState() = default;

AgentTurnState::~AgentTurnState()
{
    // Oldest first, as Document::clearUndos does.
    for (auto* segment : parked) {
        delete segment;
    }
}

namespace
{

/// Transactions detached under the turn mutex. Destroying one can drop the last reference to a
/// Python object and run its __del__, which may give up the GIL; that must never happen while the
/// mutex is held. The turn lock can be nested (the property hook holds it around ensureSegment), so
/// the detached transactions wait here until the thread releases its outermost lock, which
/// destroys them after the mutex is out and while the GIL is still held.
thread_local std::list<Transaction*> t_graves;

void bury(std::list<Transaction*>& items)
{
    t_graves.splice(t_graves.end(), items);
}

void drainGraves()
{
    if (t_graves.empty()) {
        return;
    }
    try {
        std::optional<Base::PyGILStateLocker> gil;
        if (Py_IsInitialized()) {
            gil.emplace();
        }
        // A destructor may run Python that buries more: take them one at a time.
        while (!t_graves.empty()) {
            Transaction* t = t_graves.front();
            t_graves.pop_front();
            delete t;
        }
    }
    catch (...) {
        FC_ERR("exception while destroying detached transactions");
    }
}

AgentTurnReport makeReport(const AgentTurnState& st)
{
    AgentTurnReport r;
    r.tid = st.tid;
    r.name = st.name;
    r.nestedOpens = st.nestedOpens;
    r.nestedCommits = st.nestedCommits;
    r.nestedAborts = st.nestedAborts;
    r.strayCloses = st.strayCloses;
    r.emptyCloses = st.emptyCloses;
    r.lockedOpens = st.lockedOpens;
    r.undoRefused = st.undoRefused;
    r.redoRefused = st.redoRefused;
    r.clearUndosRefused = st.clearUndosRefused;
    r.workerRefusals = st.workerRefusals;
    r.nestedNames = st.nestedNames;
    r.tampering = st.tampering;
    return r;
}

/// The turn state when its hooks apply, else null (no turn, or the turn is folding up).
std::shared_ptr<AgentTurnState> hooked(Document& doc)
{
    auto acc = Transaction::accessDocument(doc);
    auto st = acc.d->agent;
    if (!st || st->ending) {
        return nullptr;
    }
    return st;
}

/// Park the active segment so the next change records into a fresh one.
void parkActiveSegment(Transaction::DocumentAccess& acc, AgentTurnState& st)
{
    Transaction* cur = acc.d->activeUndoTransaction;
    if (!cur) {
        return;
    }
    acc.undoMap.erase(st.tid);
    acc.d->activeUndoTransaction = nullptr;
    if (cur->isEmpty()) {
        delete cur;
    }
    else {
        st.parked.push_back(cur);
    }
}

/// Savepoint operations must not overlap a worker recompute of the same document: the worker's write
/// would otherwise land in a segment that is being detached or rolled back. Waits (without holding
/// the turn mutex, which the worker needs to record); on timeout the operation is skipped and
/// reported as tampering so the turn is rejected, never half-applied.
bool settleWorker(Document& doc, const std::shared_ptr<AgentTurnState>& st, const char* what)
{
    // Throws when another Python thread closed the document during the wait.
    const bool idle = AgentTurnGuard::waitForWorkerReleasingGil(doc, AgentTurnGuard::nestedWait);
    if (idle) {
        return true;
    }
    auto lock = AgentTurnGuard::acquire(st);
    ++st->workerRefusals;
    st->tampering.push_back(
        std::format("{} skipped: the document was still recomputing on the async worker", what)
    );
    return false;
}

}  // namespace

bool AgentTurnGuard::canEnd(Document& doc, std::chrono::milliseconds wait, std::string& reason)
{
    auto acc = Transaction::accessDocument(doc);
    if (!acc.d->agent) {
        reason = "no agent turn is active on this document";
        return false;
    }
    if (doc.isPerformingTransaction() || acc.d->committing) {
        reason = "the document is undoing, redoing or committing";
        return false;
    }
    if (doc.isTransactionLocked()) {
        reason = "the document's transactions are locked";
        return false;
    }
    if (!AutoTransaction::waitForRecompute(doc.getName(), wait)) {
        reason = "the document is still recomputing on the async worker";
        return false;
    }
    return true;
}

int AgentTurnGuard::begin(Document& doc, const std::string& name, std::chrono::milliseconds wait)
{
    auto acc = Transaction::accessDocument(doc);
    DocumentP::OpScope opScope(*acc.d, DocOp::TurnBusy, DocumentP::OnClosing::Throw);
    if (acc.d->agent) {
        throw Base::RuntimeError("an agent turn is already active on this document");
    }
    if (doc.isPerformingTransaction() || acc.d->committing) {
        throw Base::RuntimeError("the document is undoing, redoing or committing");
    }
    if (doc.isTransactionLocked()) {
        throw Base::RuntimeError("the document's transactions are locked");
    }
    if (!AutoTransaction::waitForRecompute(doc.getName(), wait)) {
        throw Base::RuntimeError("the document is still recomputing on the async worker");
    }
    // The human's own pending edit becomes its own undo entry, before the turn.
    if (doc.hasPendingTransaction() || doc.getBookedTransactionID() != NullTransaction) {
        doc.commitTransaction();
        if (doc.hasPendingTransaction()) {
            throw Base::RuntimeError("could not commit the document's pending transaction");
        }
        acc.d->bookedTransaction = NullTransaction;
    }

    auto st = std::make_shared<AgentTurnState>();
    st->tid = Transaction::getNewID();
    st->name = name.empty() ? "Agent turn" : name;
    st->baseUndoCount = acc.undo.size();
    GetApplication().setTransactionDescription(
        st->tid,
        TransactionDescription {.initiator = &doc,
                                .name = TransactionName {.name = st->name, .temporary = false}}
    );
    acc.d->bookedTransaction = st->tid;
    acc.d->agent = st;
    return st->tid;
}

AgentTurnReport AgentTurnGuard::end(Document& doc, bool commit, std::chrono::milliseconds wait)
{
    auto acc = Transaction::accessDocument(doc);
    DocumentP::OpScope opScope(*acc.d, DocOp::TurnBusy, DocumentP::OnClosing::Throw);
    std::shared_ptr<AgentTurnState> st = acc.d->agent;
    if (!st || st->ending) {
        throw Base::RuntimeError("no agent turn is active on this document");
    }
    AgentTurnReport report = makeReport(*st);

    auto refuse = [&report](const char* why) {
        report.refused = true;
        report.reason = why;
        return report;
    };
    if (doc.isPerformingTransaction() || acc.d->committing) {
        return refuse("the document is undoing, redoing or committing");
    }
    if (doc.isTransactionLocked()) {
        return refuse("the document's transactions are locked");
    }
    // Never commit while the worker recomputes the document: its property changes
    // must land in the turn, and a commit halfway would split them off.
    if (!AutoTransaction::waitForRecompute(doc.getName(), wait)) {
        return refuse("the document is still recomputing on the async worker");
    }

    auto lock = AgentTurnGuard::acquire(st);
    st->ending = true;
    if (commit && !st->tampering.empty()) {
        // A tampered turn (undo/redo/clearUndos refused, or a savepoint operation skipped because
        // the worker was busy) is never committed, not even by a direct C++ caller: abort it.
        commit = false;
        report.reason = "the turn was tampered with, so it was aborted instead of committed";
        report.tampering = st->tampering;
    }

    // Fold: the active segment is the newest, the parked ones are older.
    Transaction* base = acc.d->activeUndoTransaction;
    std::vector<Transaction*> older;
    for (auto* segment : st->parked) {
        if (segment->isEmpty()) {
            delete segment;
        }
        else {
            older.push_back(segment);
        }
    }
    st->parked.clear();
    if (base && base->isEmpty()) {
        acc.undoMap.erase(st->tid);
        acc.d->activeUndoTransaction = nullptr;
        delete base;
        base = nullptr;
    }
    if (!base && !older.empty()) {
        base = older.back();
        older.pop_back();
    }
    if (base) {
        base->adoptEarlierSegments(std::move(older));
        base->Name = st->name;
        acc.undoMap[st->tid] = base;
        acc.d->activeUndoTransaction = base;
        report.recorded = true;
        report.segments = static_cast<int>(base->segmentCount());
    }
    acc.d->bookedTransaction = st->tid;

    // The plain close now does the real work: it commits (one undo entry) or aborts
    // (restores the document), erases the turn's description and clears the booking.
    // Application::closeActiveTransaction does not treat the id as a turn any more
    // because `ending` is set. It fires observers and, on abort, FeaturePython onChanged, which
    // is Python: it runs outside the turn mutex, with the turn marked busy.
    AgentTurnBusy busy;
    busy.engage(st);
    lock.unlock();
    GetApplication().closeActiveTransaction(
        commit ? TransactionCloseMode::Commit : TransactionCloseMode::Abort,
        st->tid
    );
    {
        auto relock = AgentTurnGuard::acquire(st);
        acc.d->bookedTransaction = NullTransaction;
        acc.d->agent.reset();
    }

    report.committed = commit;
    report.aborted = !commit;
    report.undoEntries = (commit && report.recorded) ? 1 : 0;
    return report;
}

bool AgentTurnGuard::waitForWorker(Document& doc, std::chrono::milliseconds wait)
{
    return AutoTransaction::waitForRecompute(doc.getName(), wait);
}

bool AgentTurnGuard::waitForWorkerReleasingGil(Document& doc, std::chrono::milliseconds wait)
{
    using Clock = std::chrono::steady_clock;
    const std::string name = doc.getName();  // before the GIL is given up: doc may die meanwhile
    const Document* const self = &doc;
    const bool holdsGil = Py_IsInitialized() && PyGILState_Check();
    const auto deadline = Clock::now() + wait;
    bool idle = false;
    for (;;) {
        // A poll in slices: the document is looked up again after every wake.
        const auto left = std::chrono::duration_cast<std::chrono::milliseconds>(deadline - Clock::now());
        const auto slice = std::clamp(left, std::chrono::milliseconds(0), std::chrono::milliseconds(50));
        if (holdsGil) {
            // The worker may need the GIL to finish a Python feature's recompute.
            Base::PyGILStateRelease release;
            idle = AutoTransaction::waitForRecompute(name, slice);
        }
        else {
            idle = AutoTransaction::waitForRecompute(name, slice);
        }
        if (GetApplication().getDocument(name.c_str()) != self) {
            throw Base::RuntimeError("the document was closed while waiting for the async worker");
        }
        // Also wait out another thread's nested abort or end() of the turn (it runs Python and
        // needs the GIL, which this thread must not hold while it waits for the turn).
        bool busyElsewhere = false;
        if (idle) {
            if (auto st = Transaction::accessDocument(doc).d->agent) {
                busyElsewhere = st->busyDepth.load() > 0 && st->busyOwner.load() != std::this_thread::get_id();
            }
        }
        if ((idle && !busyElsewhere) || Clock::now() >= deadline) {
            return idle && !busyElsewhere;
        }
    }
}

AgentTurnReport AgentTurnGuard::peek(Document& doc)
{
    auto st = Transaction::accessDocument(doc).d->agent;
    if (!st) {
        return {};
    }
    auto lock = AgentTurnGuard::acquire(st);
    return makeReport(*st);
}

bool AgentTurnGuard::isActive(const Document& doc)
{
    return Transaction::accessDocument(const_cast<Document&>(doc)).d->agent != nullptr;
}

int AgentTurnGuard::turnId(const Document& doc)
{
    auto st = hooked(const_cast<Document&>(doc));
    return st ? st->tid : NullTransaction;
}

bool AgentTurnGuard::ownsId(const Document& doc, int id)
{
    if (id == NullTransaction) {
        return false;
    }
    auto st = hooked(const_cast<Document&>(doc));
    return st && (st->tid == id || st->viaGlobalId == id);
}

int AgentTurnGuard::nestedOpen(Document& doc, const std::string& name, int viaGlobalId)
{
    auto st = hooked(doc);
    if (!st) {
        return NullTransaction;
    }
    if (!settleWorker(doc, st, "nested open")) {
        return st->tid;
    }
    auto lock = AgentTurnGuard::acquire(st);
    if (doc.isTransactionLocked()) {
        // Same as Document::openTransaction: an open while locked is ignored.
        ++st->lockedOpens;
        return st->tid;
    }
    auto acc = Transaction::accessDocument(doc);
    ++st->nestedOpens;
    st->nestedNames.push_back(name);
    parkActiveSegment(acc, *st);
    st->savepointOpen = true;
    st->viaGlobalId = viaGlobalId;
    return st->tid;
}

bool AgentTurnGuard::nestedCommit(Document& doc)
{
    auto st = hooked(doc);
    if (!st) {
        return false;
    }
    if (!settleWorker(doc, st, "nested commit")) {
        return true;
    }
    auto lock = AgentTurnGuard::acquire(st);
    if (st->savepointOpen) {
        st->savepointOpen = false;
        st->viaGlobalId = 0;
        ++st->nestedCommits;
    }
    else if (doc.hasPendingTransaction()) {
        ++st->strayCloses;
    }
    else {
        ++st->emptyCloses;
    }
    return true;
}

Transaction* AgentTurnGuard::beginNestedAbort(Document& doc, AgentTurnBusy& busy)
{
    auto st = hooked(doc);
    if (!st) {
        return nullptr;
    }
    if (!settleWorker(doc, st, "nested abort")) {
        return nullptr;
    }
    auto lock = AgentTurnGuard::acquire(st);
    if (!st->savepointOpen) {
        // An abort with no open savepoint has nothing of its own to roll back; the
        // turn itself ends only through end().
        ++st->strayCloses;
        return nullptr;
    }
    st->savepointOpen = false;
    st->viaGlobalId = 0;
    ++st->nestedAborts;

    auto acc = Transaction::accessDocument(doc);
    Transaction* cur = acc.d->activeUndoTransaction;
    if (cur) {
        acc.undoMap.erase(st->tid);
        acc.d->activeUndoTransaction = nullptr;
    }
    if (cur) {
        // The caller applies the segment, which runs Python: outside the mutex, turn busy.
        busy.engage(st);
    }
    return cur;
}

int AgentTurnGuard::ensureSegment(Document& doc)
{
    auto st = hooked(doc);
    if (!st) {
        return NullTransaction;
    }
    auto lock = AgentTurnGuard::acquire(st);
    auto acc = Transaction::accessDocument(doc);
    if (acc.d->activeUndoTransaction) {
        return st->tid;
    }
    if (acc.d->opentransaction) {
        return NullTransaction;
    }
    Base::FlagToggler<> flag(acc.d->opentransaction);
    std::list<Transaction*> redos;
    Transaction::clearRedosOf(doc, &redos);
    bury(redos);  // destroyed when this thread's outermost turn lock is released
    auto* segment = new Transaction(st->tid);
    segment->Name = st->name;
    acc.undoMap[st->tid] = segment;
    acc.d->activeUndoTransaction = segment;
    if (!st->announced) {
        // The signal runs observers (Python): raised when the last lock of this thread is released.
        st->announced = true;
        st->announceDue = true;
        st->announceDoc = &doc;
    }
    return st->tid;
}

void AgentTurnGuard::deferClose(Document& doc, bool abort)
{
    auto st = hooked(doc);
    if (st) {
        auto lock = AgentTurnGuard::acquire(st);
        st->pendingClose = std::max(st->pendingClose, abort ? 2 : 1);
    }
}

int AgentTurnGuard::takeDeferredClose(Document& doc)
{
    auto st = hooked(doc);
    if (!st || st->pendingClose == 0) {
        return 0;
    }
    auto lock = AgentTurnGuard::acquire(st);
    return std::exchange(st->pendingClose, 0);
}

bool AgentTurnGuard::refuseHistory(Document& doc, const char* what)
{
    auto st = hooked(doc);
    if (!st) {
        return false;
    }
    auto lock = AgentTurnGuard::acquire(st);
    const std::string call = what;
    if (call == "undo") {
        ++st->undoRefused;
    }
    else if (call == "redo") {
        ++st->redoRefused;
    }
    else {
        ++st->clearUndosRefused;
    }
    st->tampering.push_back(std::format("{}() called inside the guarded turn (refused)", call));
    return true;
}

void AgentTurnGuard::onDocumentDestroy(Document& doc)
{
    auto acc = Transaction::accessDocument(doc);
    if (auto st = acc.d->agent) {
        // Parked segments go first (oldest first); the active one is committed and
        // deleted by clearUndos like any other.
        auto lock = AgentTurnGuard::acquire(st, false);
        std::list<Transaction*> parked(st->parked.begin(), st->parked.end());
        st->parked.clear();
        bury(parked);  // destroyed when this thread's outermost turn lock is released
        st->ending = true;
        st->announceDue = false;  // the document is going away: no signal about it
        acc.d->agent.reset();
    }
}

AgentTurnLock AgentTurnGuard::lockFor(Document& doc)
{
    auto acc = Transaction::accessDocument(doc);
    auto st = acc.d->agent;
    if (!st) {
        return {};
    }
    return AgentTurnGuard::acquire(st);
}

AgentTurnLock AgentTurnGuard::acquire(const std::shared_ptr<AgentTurnState>& st, bool respectBusy)
{
    const bool python = Py_IsInitialized();
    const bool heldGil = python && PyGILState_Check();
    for (;;) {
        AgentTurnLock held;
        held.state_ = st;
        // GIL first, then the mutex, plainly blocking (see the lock order in the header).
        if (python) {
            held.gilState_ = static_cast<int>(PyGILState_Ensure());
            held.gil_ = true;
        }
        held.lock_ = std::unique_lock<std::recursive_mutex>(st->mutex);
        ++st->lockDepth;
        if (!respectBusy || st->busyDepth.load() == 0 || st->busyOwner.load() == std::this_thread::get_id()) {
            return held;
        }
        held.unlock();
        if (heldGil) {
            // Waiting would deadlock: the busy thread needs the GIL to finish.
            throw Base::RuntimeError(
                "the agent turn is being rolled back or closed by another thread; retry afterwards"
            );
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
}

AgentTurnLock::AgentTurnLock(AgentTurnLock&& other) noexcept
    : state_(std::move(other.state_))
    , lock_(std::move(other.lock_))
    , gil_(std::exchange(other.gil_, false))
    , gilState_(other.gilState_)
{}

AgentTurnLock& AgentTurnLock::operator=(AgentTurnLock&& other) noexcept
{
    if (this != &other) {
        unlock();
        state_ = std::move(other.state_);
        lock_ = std::move(other.lock_);
        gil_ = std::exchange(other.gil_, false);
        gilState_ = other.gilState_;
    }
    return *this;
}

AgentTurnLock::~AgentTurnLock()
{
    unlock();
}

void AgentTurnLock::unlock()
{
    Document* announce = nullptr;
    std::string name;
    bool outermost = false;
    if (lock_.owns_lock()) {
        outermost = --state_->lockDepth == 0;
        if (outermost && state_->announceDue) {
            state_->announceDue = false;
            announce = state_->announceDoc;
            name = state_->name;
        }
        lock_.unlock();
    }
    if (outermost) {
        // Mutex out, GIL still ours: transactions detached under the lock die here (their
        // destructors may run Python).
        drainGraves();
    }
    if (announce) {
        // After the mutex, before the GIL goes: observers may run Python.
        try {
            announce->signalOpenTransaction(*announce, name);
        }
        catch (...) {
            FC_ERR("signalOpenTransaction raised in an observer");
        }
    }
    if (gil_) {
        gil_ = false;
        PyGILState_Release(static_cast<PyGILState_STATE>(gilState_));
    }
    state_.reset();
}

void AgentTurnBusy::engage(const std::shared_ptr<AgentTurnState>& st)
{
    // The caller holds the turn's lock.
    st->busyOwner = std::this_thread::get_id();
    ++st->busyDepth;
    st_ = st;
}

void AgentTurnBusy::release() noexcept
{
    if (!st_) {
        return;
    }
    try {
        auto lock = AgentTurnGuard::acquire(st_, false);
        if (--st_->busyDepth == 0) {
            st_->busyOwner = std::thread::id {};
        }
    }
    catch (...) {
        FC_ERR("could not clear the agent turn's busy mark");
    }
    st_.reset();
}

AgentTurnBusy::~AgentTurnBusy()
{
    release();
}
