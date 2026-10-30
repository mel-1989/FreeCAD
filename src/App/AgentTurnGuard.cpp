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
#include <format>
#include <utility>

#include <Base/Exception.h>
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
bool settleWorker(Document& doc, AgentTurnState& st, const char* what)
{
    if (AutoTransaction::waitForRecompute(doc.getName(), AgentTurnGuard::nestedWait)) {
        return true;
    }
    std::lock_guard<std::recursive_mutex> lock(st.mutex);
    ++st.workerRefusals;
    st.tampering.push_back(
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

    std::unique_lock<std::recursive_mutex> lock(st->mutex);
    st->ending = true;

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
    // because `ending` is set.
    GetApplication().closeActiveTransaction(
        commit ? TransactionCloseMode::Commit : TransactionCloseMode::Abort,
        st->tid
    );
    acc.d->bookedTransaction = NullTransaction;
    acc.d->agent.reset();

    report.committed = commit;
    report.aborted = !commit;
    report.undoEntries = (commit && report.recorded) ? 1 : 0;
    return report;
}

bool AgentTurnGuard::waitForWorker(Document& doc, std::chrono::milliseconds wait)
{
    return AutoTransaction::waitForRecompute(doc.getName(), wait);
}

AgentTurnReport AgentTurnGuard::peek(Document& doc)
{
    auto st = Transaction::accessDocument(doc).d->agent;
    if (!st) {
        return {};
    }
    std::lock_guard<std::recursive_mutex> lock(st->mutex);
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
    if (!settleWorker(doc, *st, "nested open")) {
        return st->tid;
    }
    std::lock_guard<std::recursive_mutex> lock(st->mutex);
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
    if (!settleWorker(doc, *st, "nested commit")) {
        return true;
    }
    std::lock_guard<std::recursive_mutex> lock(st->mutex);
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

Transaction* AgentTurnGuard::beginNestedAbort(Document& doc, std::unique_lock<std::recursive_mutex>& lock)
{
    auto st = hooked(doc);
    if (!st) {
        return nullptr;
    }
    if (!settleWorker(doc, *st, "nested abort")) {
        return nullptr;
    }
    lock = std::unique_lock<std::recursive_mutex>(st->mutex);
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
    return cur;
}

int AgentTurnGuard::ensureSegment(Document& doc)
{
    auto st = hooked(doc);
    if (!st) {
        return NullTransaction;
    }
    std::lock_guard<std::recursive_mutex> lock(st->mutex);
    auto acc = Transaction::accessDocument(doc);
    if (acc.d->activeUndoTransaction) {
        return st->tid;
    }
    if (acc.d->opentransaction) {
        return NullTransaction;
    }
    Base::FlagToggler<> flag(acc.d->opentransaction);
    Transaction::clearRedosOf(doc);
    auto* segment = new Transaction(st->tid);
    segment->Name = st->name;
    acc.undoMap[st->tid] = segment;
    acc.d->activeUndoTransaction = segment;
    if (!st->announced) {
        st->announced = true;
        doc.signalOpenTransaction(doc, st->name);
    }
    return st->tid;
}

void AgentTurnGuard::deferClose(Document& doc, bool abort)
{
    auto st = hooked(doc);
    if (st) {
        std::lock_guard<std::recursive_mutex> lock(st->mutex);
        st->pendingClose = std::max(st->pendingClose, abort ? 2 : 1);
    }
}

int AgentTurnGuard::takeDeferredClose(Document& doc)
{
    auto st = hooked(doc);
    if (!st || st->pendingClose == 0) {
        return 0;
    }
    std::lock_guard<std::recursive_mutex> lock(st->mutex);
    return std::exchange(st->pendingClose, 0);
}

bool AgentTurnGuard::refuseHistory(Document& doc, const char* what)
{
    auto st = hooked(doc);
    if (!st) {
        return false;
    }
    std::lock_guard<std::recursive_mutex> lock(st->mutex);
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
        std::lock_guard<std::recursive_mutex> lock(st->mutex);
        for (auto* segment : st->parked) {
            delete segment;
        }
        st->parked.clear();
        st->ending = true;
        acc.d->agent.reset();
    }
}

std::unique_lock<std::recursive_mutex> AgentTurnGuard::lockFor(Document& doc)
{
    auto acc = Transaction::accessDocument(doc);
    auto st = acc.d->agent;
    if (!st) {
        return {};
    }
    return std::unique_lock<std::recursive_mutex>(st->mutex);
}
