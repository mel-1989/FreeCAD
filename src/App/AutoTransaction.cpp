// SPDX-License-Identifier: LGPL-2.1-or-later

/****************************************************************************
 *   Copyright (c) 2019 Zheng Lei (realthunder) <realthunder.dev@gmail.com> *
 *                                                                          *
 *   This file is part of the FreeCAD CAx development system.               *
 *                                                                          *
 *   This library is free software; you can redistribute it and/or          *
 *   modify it under the terms of the GNU Library General Public            *
 *   License as published by the Free Software Foundation; either           *
 *   version 2 of the License, or (at your option) any later version.       *
 *                                                                          *
 *   This library  is distributed in the hope that it will be useful,       *
 *   but WITHOUT ANY WARRANTY; without even the implied warranty of         *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the          *
 *   GNU Library General Public License for more details.                   *
 *                                                                          *
 *   You should have received a copy of the GNU Library General Public      *
 *   License along with this library; see the file COPYING.LIB. If not,     *
 *   write to the Free Software Foundation, Inc., 59 Temple Place,          *
 *   Suite 330, Boston, MA  02111-1307, USA                                 *
 *                                                                          *
 ****************************************************************************/


#include <algorithm>
#include <mutex>
#include <vector>
#include <Base/Interpreter.h>

#include "AutoTransaction.h"
#include "Application.h"
#include "Document.h"
#include "Transactions.h"
#include "AgentTurnGuard.h"


FC_LOG_LEVEL_INIT("App", true, true)

using namespace App;

AutoTransaction::AutoTransaction(int tid)
    : tid(tid)
{

}
AutoTransaction::AutoTransaction(Document* doc, const std::string& name)
    : AutoTransaction(doc->openTransaction(name))
{
    
}

AutoTransaction::~AutoTransaction()
{
    close(TransactionCloseMode::Commit);
}

void AutoTransaction::close(TransactionCloseMode mode)
{
    if (tid != NullTransaction) {
        GetApplication().closeActiveTransaction(mode, tid);
        tid = 0;
    }
}

int Application::setActiveTransaction(TransactionName name)
{
    if (name.name.empty()) {
        name.name = "Command";
    }

    if (_pActiveDoc != nullptr) {
        return _pActiveDoc->setActiveTransaction(name);
    }
    return openGlobalTransaction(name);
}

int Application::openGlobalTransaction(TransactionName name)
{
    if (name.name.empty()) {
        name.name = "Command";
    }

    FC_WARN("Setting a global transaction with name='" << name.name);
    if (_globalTransactionID != 0 && transactionTmpName(_globalTransactionID)) {
        setTransactionName(_globalTransactionID, name);
    } else {
        FC_LOG("set global transaction '" << name.name << "'");

        if (_globalTransactionID != 0 && !commitTransaction(_globalTransactionID)) {
            FC_WARN("could not close current global transaction");
            return _globalTransactionID;
        }

        _globalTransactionID = Transaction::getNewID();
        setTransactionDescription(
            _globalTransactionID,
            TransactionDescription {
                .initiator = nullptr,
                .name = name
            }
        );
    }

    // A guarded agent turn turns the global open into a savepoint inside its own transaction.
    for (auto& docNameAndDoc : DocMap) {
        if (AgentTurnGuard::turnId(*docNameAndDoc.second) != NullTransaction) {
            AgentTurnGuard::nestedOpen(*docNameAndDoc.second, name.name, _globalTransactionID);
        }
    }

    return _globalTransactionID;
}
int Application::getGlobalTransaction() const
{
    return _globalTransactionID;
}
bool Application::transactionIsActive(int tid) const
{
    return transactionDescription(tid) != std::nullopt;
}
std::string Application::getTransactionName(int tid) const
{
    auto desc = transactionDescription(tid);
    return desc ? desc->name.name : "";
}
bool Application::transactionTmpName(int tid) const
{
    auto desc = transactionDescription(tid);
    return desc ? desc->name.temporary : false;
}
Document* Application::transactionInitiator(int tid) const
{
    auto desc = transactionDescription(tid);
    return desc ? desc->initiator : nullptr;
}
std::optional<TransactionDescription> Application::transactionDescription(int tid) const
{
    if (tid == NullTransaction) {
        return std::nullopt;
    }
    auto found = _activeTransactionDescriptions.find(tid);
    if (found != _activeTransactionDescriptions.end()) {
        return std::optional<TransactionDescription>(found->second);
    }
    return std::nullopt;
}
void Application::setTransactionDescription(int tid, const TransactionDescription& desc)
{
    if (tid == NullTransaction) {
        return;
    }
    auto found = _activeTransactionDescriptions.find(tid);
    bool wasPresent = (found != _activeTransactionDescriptions.end());

    if (wasPresent && found->second.name.temporary) {
        for (auto& v : DocMap) {
            v.second->renameTransaction(desc.name.name, tid);
        }
    }

    if (!wasPresent || found->second.name.temporary) {
        _activeTransactionDescriptions[tid] = desc;
        FC_LOG("transaction rename to '" << desc.name.name << "'");
    }
}
void Application::setTransactionName(int tid, const TransactionName& name)
{
    if (tid == NullTransaction || !transactionIsActive(tid)) {
        return;
    }
    auto found = _activeTransactionDescriptions.find(tid);
    if (found == _activeTransactionDescriptions.end() || found->second.name.temporary) {
        _activeTransactionDescriptions[tid].name = name;
        FC_LOG("transaction rename to '" << name.name << "'");
        for (auto& v : DocMap) {
            v.second->renameTransaction(name.name, tid);
        }
    } 
}

bool Application::closeActiveTransaction(TransactionCloseMode mode, int id)
{
    bool abort = (mode == TransactionCloseMode::Abort);
    
    if (id == NullTransaction) {
        if (_pActiveDoc != nullptr && _pActiveDoc->getBookedTransactionID() != NullTransaction) {
            id = _pActiveDoc->getBookedTransactionID();
        } else {
            id = _globalTransactionID;
        }
    }
    if (id == NullTransaction || id == currentlyClosingID) {
        return false;
    }
    currentlyClosingID = id;

    // Documents in a guarded agent turn treat a close of their turn id (or of
    // the global id a nested open came in through) as the end of a savepoint:
    // the turn's transaction and its description stay open until the turn ends.
    bool closesTurn = false;
    std::vector<Document*> docsToPoke;
    for (auto& docNameAndDoc : DocMap) {
        const bool guarded = AgentTurnGuard::ownsId(*docNameAndDoc.second, id);
        if (docNameAndDoc.second->getBookedTransactionID() != id && !guarded) {
            continue;
        }
        if(docNameAndDoc.second->isTransactionLocked() || docNameAndDoc.second->transacting()) {
            FC_LOG("pending " << (abort ? "abort" : "close") << " transaction");
            if (guarded) {
                // Remembered, and performed when the lock is released.
                AgentTurnGuard::deferClose(*docNameAndDoc.second, abort);
            }
            currentlyClosingID = 0;
            return false;
        }
        if (guarded && AgentTurnGuard::turnId(*docNameAndDoc.second) == id) {
            closesTurn = true;
        }
        docsToPoke.push_back(docNameAndDoc.second);
    }

    if (!closesTurn) {
        FC_LOG("close transaction '" << _activeTransactionDescriptions[id].name.name << "' " << abort);
        _activeTransactionDescriptions.erase(id);
        if (id == _globalTransactionID) {
            _globalTransactionID = 0;
        }
    }

    TransactionSignaller signaller(abort, false);

    for (auto& doc : docsToPoke) {
        if (abort) {
            doc->_abortTransaction();
        }
        else {
            doc->_commitTransaction();
        }
    }
    currentlyClosingID = 0;

    return true;
}
bool Application::commitTransaction(int tid)
{
    return  closeActiveTransaction(TransactionCloseMode::Commit, tid);
}
bool Application::abortTransaction(int tid)
{
    return closeActiveTransaction(TransactionCloseMode::Abort, tid);
}

////////////////////////////////////////////////////////////////////////

TransactionLocker::TransactionLocker(Document* doc, bool lock)
    : active(lock)
    , doc(doc)
{
    if (lock) {
        doc->lockTransaction();
    }
}

TransactionLocker::~TransactionLocker()
{
    if (active) {
        try {
            activate(false);
            return;
        }
        catch (Base::Exception& e) {
            e.reportException();
        }
        catch (Py::Exception&) {
            Base::PyException e;
            e.reportException();
        }
        catch (std::exception& e) {
            FC_ERR(e.what());
        }
        catch (...) {
        }
        FC_ERR("Exception when unlocking transaction");
    }
}

void TransactionLocker::activate(bool enable)
{
    if (active == enable) {
        return;
    }

    active = enable;
    if (active) {
        doc->lockTransaction();
        return;
    }

    doc->unlockTransaction();
}

bool AutoTransaction::waitForRecompute(const std::string& docName, std::chrono::milliseconds timeout)
{
    Application& app = GetApplication();
    std::unique_lock<std::mutex> lock(app._recomputeMutex);
    return app._recomputeStateChanged.wait_for(lock, timeout, [&app, &docName] {
        if (app._recomputeDocumentsInProgress.contains(docName)) {
            return false;
        }
        return std::none_of(
            app._recomputeRequests.begin(),
            app._recomputeRequests.end(),
            [&docName](const RecomputeRequest& request) { return request.documentName == docName; }
        );
    });
}
