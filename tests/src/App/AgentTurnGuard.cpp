// SPDX-License-Identifier: LGPL-2.1-or-later

#include <chrono>
#include <atomic>
#include <condition_variable>
#include <future>
#include <mutex>
#include <thread>

#include <gtest/gtest.h>

#include "App/AgentTurnGuard.h"
#include "App/Application.h"
#include "App/AutoTransaction.h"
#include "App/Document.h"
#include "App/DocumentObject.h"
#include "App/FeatureTest.h"
#include "App/PropertyStandard.h"
#include <Base/Exception.h>
#include <src/App/InitApplication.h>

using namespace std::chrono_literals;

namespace
{

// A worker-safe feature whose recompute holds at a gate and then writes a property, like a
// solver that stores its result: the write must land in the active agent turn.
struct Gate
{
    std::mutex mutex;
    std::condition_variable changed;
    bool started = false;
    bool open = true;

    void reset(bool opened)
    {
        std::lock_guard<std::mutex> lock(mutex);
        started = false;
        open = opened;
    }
    bool waitStarted(std::chrono::milliseconds timeout)
    {
        std::unique_lock<std::mutex> lock(mutex);
        return changed.wait_for(lock, timeout, [this] { return started; });
    }
    void release()
    {
        {
            std::lock_guard<std::mutex> lock(mutex);
            open = true;
        }
        changed.notify_all();
    }
    void pass()
    {
        std::unique_lock<std::mutex> lock(mutex);
        started = true;
        changed.notify_all();
        changed.wait(lock, [this] { return open; });
    }
};

Gate& gate()
{
    static Gate g;
    return g;
}

}  // namespace

namespace App
{

class AgentWorkerWriter: public App::DocumentObject
{
    PROPERTY_HEADER_WITH_OVERRIDE(App::AgentWorkerWriter);

public:
    App::PropertyInteger Value;

    AgentWorkerWriter()
    {
        ADD_PROPERTY(Value, (0));
    }
    bool canRecomputeOnWorker() const override
    {
        return true;
    }
    App::DocumentObjectExecReturn* execute() override
    {
        gate().pass();
        Value.setValue(77);
        return StdReturn;
    }
};


// A worker-safe feature that writes a property thousands of times during its recompute, so the
// main thread's savepoint operations overlap the worker.
inline std::atomic<bool> agentLoopStarted {false};

class AgentLoopWriter: public App::DocumentObject
{
    PROPERTY_HEADER_WITH_OVERRIDE(App::AgentLoopWriter);

public:
    App::PropertyInteger Value;

    AgentLoopWriter()
    {
        ADD_PROPERTY(Value, (0));
    }
    bool canRecomputeOnWorker() const override
    {
        return true;
    }
    App::DocumentObjectExecReturn* execute() override
    {
        agentLoopStarted = true;
        for (int i = 1; i <= 3000; ++i) {
            Value.setValue(i);
            std::this_thread::yield();
        }
        return StdReturn;
    }
};

}  // namespace App

PROPERTY_SOURCE(App::AgentWorkerWriter, App::DocumentObject)
PROPERTY_SOURCE(App::AgentLoopWriter, App::DocumentObject)

class AgentTurnGuardTest: public ::testing::Test
{
protected:
    static void SetUpTestSuite()
    {
        tests::initApplication();
        static bool registered = false;
        if (!registered) {
            App::AgentWorkerWriter::init();
            App::AgentLoopWriter::init();
            registered = true;
        }
    }

    void SetUp() override
    {
        _docName = App::GetApplication().getUniqueDocumentName("agent_turn");
        _doc = App::GetApplication().newDocument(_docName.c_str(), "testUser");
        App::GetApplication().setActiveDocument(_doc);
        _a = static_cast<App::FeatureTest*>(_doc->addObject("App::FeatureTest", "A"));
        _b = static_cast<App::FeatureTest*>(_doc->addObject("App::FeatureTest", "B"));
        ASSERT_NE(_a, nullptr);
        ASSERT_NE(_b, nullptr);
    }

    void TearDown() override
    {
        gate().release();
        if (_doc && App::GetApplication().getDocument(_docName.c_str())) {
            if (App::AgentTurnGuard::isActive(*_doc)) {
                App::AgentTurnGuard::end(*_doc, false, 5s);
            }
            App::GetApplication().closeDocument(_docName.c_str());
        }
    }

    App::AgentWorkerWriter* addWriter()
    {
        try {
            return static_cast<App::AgentWorkerWriter*>(_doc->addObject("App::AgentWorkerWriter", "Writer"));
        }
        catch (const Base::Exception& e) {
            ADD_FAILURE() << "addObject failed: " << e.what();
        }
        return nullptr;
    }

    int undos() const
    {
        return _doc->getAvailableUndos();
    }

    // A human edit, committed as its own undo entry.
    void humanEdit(const char* name, long value)
    {
        _doc->openTransaction(name);
        _a->Integer.setValue(value);
        _doc->commitTransaction();
    }

    std::string _docName;
    App::Document* _doc {};
    App::FeatureTest* _a {};
    App::FeatureTest* _b {};
};

TEST_F(AgentTurnGuardTest, NestedOpenAndCommitGiveOneUndoEntry)
{
    const long intBefore = _a->Integer.getValue();
    const double floatBefore = _a->Float.getValue();
    const std::string stringBefore = _a->String.getValue();

    const int tid = App::AgentTurnGuard::begin(*_doc, "the turn");
    EXPECT_NE(tid, 0);
    EXPECT_TRUE(App::AgentTurnGuard::isActive(*_doc));
    _a->Integer.setValue(1);
    _doc->openTransaction("nested");
    _a->Float.setValue(2.5);
    _doc->commitTransaction();
    _a->String.setValue("agent");
    EXPECT_EQ(undos(), 1) << "the turn's pending transaction counts as the one undo available";
    EXPECT_EQ(_doc->getAvailableUndoNames().size(), 1U);

    const auto report = App::AgentTurnGuard::end(*_doc, true);
    EXPECT_FALSE(App::AgentTurnGuard::isActive(*_doc));
    EXPECT_TRUE(report.committed);
    EXPECT_TRUE(report.recorded);
    EXPECT_EQ(report.undoEntries, 1);
    EXPECT_EQ(report.segments, 2);
    EXPECT_EQ(report.nestedOpens, 1);
    EXPECT_EQ(report.nestedCommits, 1);
    EXPECT_EQ(report.nestedAborts, 0);
    ASSERT_EQ(report.nestedNames.size(), 1U);
    EXPECT_EQ(report.nestedNames[0], "nested");
    EXPECT_TRUE(report.tampering.empty());

    ASSERT_EQ(undos(), 1);
    EXPECT_EQ(_doc->getAvailableUndoNames().front(), "the turn");
    EXPECT_EQ(_a->Integer.getValue(), 1);

    EXPECT_TRUE(_doc->undo());
    EXPECT_EQ(_a->Integer.getValue(), intBefore);
    EXPECT_DOUBLE_EQ(_a->Float.getValue(), floatBefore);
    EXPECT_EQ(std::string(_a->String.getValue()), stringBefore);
    EXPECT_EQ(undos(), 0);
    ASSERT_EQ(_doc->getAvailableRedos(), 1);

    EXPECT_TRUE(_doc->redo());
    EXPECT_EQ(_a->Integer.getValue(), 1);
    EXPECT_DOUBLE_EQ(_a->Float.getValue(), 2.5);
    EXPECT_EQ(std::string(_a->String.getValue()), "agent");
    EXPECT_EQ(undos(), 1);
}

TEST_F(AgentTurnGuardTest, NestedAbortRollsBackOnlyItsOwnPart)
{
    const double floatBefore = _a->Float.getValue();

    App::AgentTurnGuard::begin(*_doc, "the turn");
    _a->Integer.setValue(1);                 // before the nested open: stays
    _doc->openTransaction("fails");
    _a->Float.setValue(9.0);                 // rolled back
    _b->Integer.setValue(5);                 // rolled back
    _doc->abortTransaction();
    EXPECT_DOUBLE_EQ(_a->Float.getValue(), floatBefore);
    EXPECT_EQ(_a->Integer.getValue(), 1) << "an abort must not touch changes made before its open";
    _a->String.setValue("after");            // after the abort: recorded again

    const auto report = App::AgentTurnGuard::end(*_doc, true);
    EXPECT_EQ(report.nestedAborts, 1);
    EXPECT_EQ(report.undoEntries, 1);
    EXPECT_EQ(undos(), 1);

    EXPECT_TRUE(_doc->undo());
    EXPECT_NE(_a->Integer.getValue(), 1);
    EXPECT_EQ(std::string(_a->String.getValue()), "4711");
    EXPECT_NE(_b->Integer.getValue(), 5);
}

TEST_F(AgentTurnGuardTest, SetActiveTransactionAndAutoTransactionAreSavepoints)
{
    App::AgentTurnGuard::begin(*_doc, "the turn");

    // Application::setActiveTransaction / closeActiveTransaction, as a GUI doCommand does.
    const int t1 = App::GetApplication().setActiveTransaction(App::TransactionName {.name = "gui command"});
    EXPECT_NE(t1, 0);
    _a->Integer.setValue(1);
    EXPECT_TRUE(App::GetApplication().closeActiveTransaction(App::TransactionCloseMode::Commit, t1));
    // closing with no id and nothing open is absorbed too
    App::GetApplication().closeActiveTransaction(App::TransactionCloseMode::Commit);

    // Document::setActiveTransaction without a tid (the old leak: it committed directly).
    _doc->setActiveTransaction(App::TransactionName {.name = "direct"});
    _a->Float.setValue(3.5);
    _doc->commitTransaction();

    {
        App::AutoTransaction scope(_doc, "scoped");
        _b->Integer.setValue(8);
    }  // commits its savepoint
    {
        App::AutoTransaction scope(_doc, "scoped abort");
        _b->Float.setValue(8.5);
        scope.close(App::TransactionCloseMode::Abort);
    }
    EXPECT_EQ(undos(), 0);

    const auto report = App::AgentTurnGuard::end(*_doc, true);
    EXPECT_EQ(report.undoEntries, 1);
    EXPECT_EQ(report.nestedAborts, 1);
    EXPECT_EQ(undos(), 1);
    EXPECT_EQ(_a->Integer.getValue(), 1);
    EXPECT_DOUBLE_EQ(_a->Float.getValue(), 3.5);
    EXPECT_EQ(_b->Integer.getValue(), 8);
    EXPECT_NE(_b->Float.getValue(), 8.5);

    EXPECT_TRUE(_doc->undo());
    EXPECT_NE(_a->Integer.getValue(), 1);
    EXPECT_NE(_b->Integer.getValue(), 8);
    EXPECT_EQ(undos(), 0);
}

TEST_F(AgentTurnGuardTest, GlobalTransactionOpenIsASavepoint)
{
    App::AgentTurnGuard::begin(*_doc, "the turn");
    _a->Integer.setValue(1);
    // With no active document the application falls back to a global transaction.
    App::GetApplication().setActiveDocument(static_cast<App::Document*>(nullptr));
    const int global = App::GetApplication().setActiveTransaction(App::TransactionName {.name = "global"});
    _b->Integer.setValue(6);
    App::GetApplication().closeActiveTransaction(App::TransactionCloseMode::Abort, global);
    EXPECT_NE(_b->Integer.getValue(), 6);
    EXPECT_EQ(_a->Integer.getValue(), 1);
    App::GetApplication().setActiveDocument(_doc);
    const auto report = App::AgentTurnGuard::end(*_doc, true);
    EXPECT_EQ(report.nestedAborts, 1);
    EXPECT_EQ(undos(), 1);
}

TEST_F(AgentTurnGuardTest, UndoAndRedoAreRefusedAndReported)
{
    humanEdit("human", 5);
    ASSERT_EQ(undos(), 1);

    App::AgentTurnGuard::begin(*_doc, "the turn");
    _a->Integer.setValue(6);
    EXPECT_FALSE(_doc->undo());
    EXPECT_FALSE(_doc->redo());
    _doc->clearUndos();
    EXPECT_EQ(_a->Integer.getValue(), 6) << "the refused undo changed nothing";
    EXPECT_EQ(undos(), 2) << "the human's entry plus the turn's pending one: nothing was undone";

    // A tampered turn is never committed, even when the caller asks for it: it is aborted.
    const auto report = App::AgentTurnGuard::end(*_doc, true);
    EXPECT_EQ(report.undoRefused, 1);
    EXPECT_EQ(report.redoRefused, 1);
    EXPECT_EQ(report.clearUndosRefused, 1);
    EXPECT_EQ(report.tampering.size(), 3U);
    EXPECT_FALSE(report.committed);
    EXPECT_TRUE(report.aborted);
    EXPECT_EQ(report.undoEntries, 0);
    EXPECT_FALSE(report.reason.empty());
    EXPECT_EQ(_a->Integer.getValue(), 5) << "the turn's change was rolled back";
    EXPECT_EQ(undos(), 1);
    EXPECT_EQ(_doc->getAvailableUndoNames().front(), "human");
}

TEST_F(AgentTurnGuardTest, EndWithAbortRestoresEveryProperty)
{
    humanEdit("human", 5);
    const long a = _a->Integer.getValue();
    const double f = _a->Float.getValue();
    const std::string s = _a->String.getValue();
    const long b = _b->Integer.getValue();
    const std::size_t objects = _doc->getObjects().size();

    App::AgentTurnGuard::begin(*_doc, "the turn");
    _a->Integer.setValue(100);
    _doc->openTransaction("nested");
    _a->Float.setValue(1.5);
    _a->String.setValue("changed");
    _doc->commitTransaction();
    auto* c = _doc->addObject("App::FeatureTest", "C");
    ASSERT_NE(c, nullptr);
    _doc->removeObject("B");
    _doc->openTransaction("nested 2");
    _doc->getObject("A")->Label.setValue("renamed");
    _doc->commitTransaction();

    const auto report = App::AgentTurnGuard::end(*_doc, false);
    EXPECT_TRUE(report.aborted);
    EXPECT_EQ(report.undoEntries, 0);
    EXPECT_EQ(undos(), 1) << "only the human's entry remains";
    EXPECT_FALSE(App::AgentTurnGuard::isActive(*_doc));

    EXPECT_EQ(_a->Integer.getValue(), a);
    EXPECT_DOUBLE_EQ(_a->Float.getValue(), f);
    EXPECT_EQ(std::string(_a->String.getValue()), s);
    EXPECT_EQ(std::string(_a->Label.getValue()), "A");
    EXPECT_EQ(_doc->getObjects().size(), objects);
    EXPECT_EQ(_doc->getObject("C"), nullptr);
    ASSERT_NE(_doc->getObject("B"), nullptr);
    EXPECT_EQ(static_cast<App::FeatureTest*>(_doc->getObject("B"))->Integer.getValue(), b);
}

TEST_F(AgentTurnGuardTest, ObjectCreatedAndDeletedAcrossSegmentsUndoesCleanly)
{
    const std::size_t objects = _doc->getObjects().size();
    App::AgentTurnGuard::begin(*_doc, "the turn");
    _doc->addObject("App::FeatureTest", "Temp");
    _doc->openTransaction("nested");
    _doc->removeObject("Temp");
    _doc->commitTransaction();
    _b->Integer.setValue(3);
    const auto report = App::AgentTurnGuard::end(*_doc, true);
    EXPECT_EQ(report.undoEntries, 1);
    EXPECT_EQ(_doc->getObjects().size(), objects);
    EXPECT_TRUE(_doc->undo());
    EXPECT_EQ(_doc->getObjects().size(), objects);
    EXPECT_NE(_b->Integer.getValue(), 3);
    EXPECT_TRUE(_doc->redo());
    EXPECT_EQ(_b->Integer.getValue(), 3);
    EXPECT_EQ(_doc->getObject("Temp"), nullptr);
}

TEST_F(AgentTurnGuardTest, TurnThatRecordedNothingLeavesNoEntryAndKeepsRedo)
{
    humanEdit("human", 5);
    EXPECT_TRUE(_doc->undo());
    ASSERT_EQ(_doc->getAvailableRedos(), 1);

    App::AgentTurnGuard::begin(*_doc, "empty turn");
    _doc->openTransaction("nested");
    _doc->commitTransaction();
    const auto report = App::AgentTurnGuard::end(*_doc, true);
    EXPECT_FALSE(report.recorded);
    EXPECT_EQ(report.undoEntries, 0);
    EXPECT_EQ(undos(), 0);
    EXPECT_EQ(_doc->getAvailableRedos(), 1);
}

TEST_F(AgentTurnGuardTest, BeginAndEndAreChecked)
{
    EXPECT_THROW(App::AgentTurnGuard::end(*_doc, true), Base::RuntimeError);
    App::AgentTurnGuard::begin(*_doc, "t");
    EXPECT_THROW(App::AgentTurnGuard::begin(*_doc, "again"), Base::RuntimeError);
    App::AgentTurnGuard::end(*_doc, true);
    EXPECT_THROW(App::AgentTurnGuard::end(*_doc, true), Base::RuntimeError);
}

TEST_F(AgentTurnGuardTest, PendingHumanTransactionBecomesItsOwnEntry)
{
    _doc->openTransaction("human pending");
    _a->Integer.setValue(5);
    ASSERT_TRUE(_doc->hasPendingTransaction());
    App::AgentTurnGuard::begin(*_doc, "the turn");
    EXPECT_EQ(undos(), 1);
    _a->Integer.setValue(6);
    App::AgentTurnGuard::end(*_doc, true);
    EXPECT_EQ(undos(), 2);
}

TEST_F(AgentTurnGuardTest, LockedCloseIsRememberedAndPerformedOnUnlock)
{
    App::AgentTurnGuard::begin(*_doc, "the turn");
    _a->Integer.setValue(1);
    const int tid = _doc->openTransaction("nested");
    _b->Integer.setValue(9);
    {
        App::TransactionLocker locker(_doc);
        EXPECT_FALSE(App::GetApplication().closeActiveTransaction(App::TransactionCloseMode::Abort, tid));
        EXPECT_EQ(_b->Integer.getValue(), 9) << "the close waits for the lock";
    }
    EXPECT_NE(_b->Integer.getValue(), 9) << "the remembered abort ran on unlock";
    EXPECT_EQ(_a->Integer.getValue(), 1);
    EXPECT_EQ(App::AgentTurnGuard::end(*_doc, true).nestedAborts, 1);
}

TEST_F(AgentTurnGuardTest, EndWaitsForAsyncRecomputeAndWorkerWriteLandsInTheTurn)
{
    auto* w = addWriter();
    ASSERT_NE(w, nullptr);
    gate().reset(false);

    App::AgentTurnGuard::begin(*_doc, "the turn");
    _a->Integer.setValue(1);
    w->touch();
    App::GetApplication().queueRecomputeRequest(App::RecomputeRequest::fromDocumentObject(*w));
    ASSERT_TRUE(gate().waitStarted(2s));

    // The worker is inside the recompute: end must refuse, not commit halfway.
    const auto refused = App::AgentTurnGuard::end(*_doc, true, 100ms);
    EXPECT_TRUE(refused.refused);
    EXPECT_FALSE(refused.committed);
    EXPECT_TRUE(App::AgentTurnGuard::isActive(*_doc));
    EXPECT_TRUE(_doc->hasPendingTransaction()) << "still the turn's open transaction, not committed";
    EXPECT_EQ(undos(), 1);

    // end blocks until the worker is done, then commits everything as one entry.
    auto ending = std::async(std::launch::async, [this] {
        return App::AgentTurnGuard::end(*_doc, true, 10s);
    });
    EXPECT_EQ(ending.wait_for(100ms), std::future_status::timeout);
    gate().release();
    ASSERT_EQ(ending.wait_for(10s), std::future_status::ready);
    const auto report = ending.get();
    EXPECT_FALSE(report.refused);
    EXPECT_TRUE(report.committed);
    EXPECT_EQ(report.undoEntries, 1);
    EXPECT_EQ(w->Value.getValue(), 77);
    EXPECT_EQ(undos(), 1);

    EXPECT_TRUE(_doc->undo());
    EXPECT_EQ(w->Value.getValue(), 0) << "the worker's write was part of the turn";
    EXPECT_NE(_a->Integer.getValue(), 1);
}

TEST_F(AgentTurnGuardTest, BeginWaitsForAsyncRecompute)
{
    auto* w = addWriter();
    ASSERT_NE(w, nullptr);
    gate().reset(false);
    w->touch();
    App::GetApplication().queueRecomputeRequest(App::RecomputeRequest::fromDocumentObject(*w));
    ASSERT_TRUE(gate().waitStarted(2s));
    EXPECT_THROW(App::AgentTurnGuard::begin(*_doc, "t", 50ms), Base::RuntimeError);
    gate().release();
    EXPECT_NO_THROW(App::AgentTurnGuard::begin(*_doc, "t", 10s));
    App::AgentTurnGuard::end(*_doc, false);
}

// Outside a guarded turn nothing changes: the lock blocks a close, nested transactions split.
TEST_F(AgentTurnGuardTest, OutsideATurnTransactionsBehaveAsBefore)
{
    const int tid = _doc->openTransaction("plain");
    _a->Integer.setValue(3);
    {
        App::TransactionLocker locker(_doc);
        EXPECT_FALSE(App::GetApplication().closeActiveTransaction(App::TransactionCloseMode::Commit, tid));
        EXPECT_TRUE(_doc->hasPendingTransaction());
    }
    EXPECT_TRUE(App::GetApplication().closeActiveTransaction(App::TransactionCloseMode::Commit, tid));
    EXPECT_EQ(undos(), 1);

    _doc->openTransaction("second");
    _a->Integer.setValue(4);
    _doc->commitTransaction();
    _doc->openTransaction("third");
    _a->Integer.setValue(5);
    _doc->commitTransaction();
    EXPECT_EQ(undos(), 3) << "no guard: every commit is its own undo entry";

    _doc->openTransaction("aborted");
    _a->Integer.setValue(6);
    _doc->abortTransaction();
    EXPECT_EQ(_a->Integer.getValue(), 5);
    EXPECT_TRUE(_doc->undo());
    EXPECT_EQ(_a->Integer.getValue(), 4);
}

// Nested open/abort cycles on the main thread while the worker keeps writing a property: every
// savepoint operation waits for the worker, so the worker's writes stay inside the turn and
// rejecting the turn restores the pre-turn value. (Found by the V-r016-opus verifier.)
TEST_F(AgentTurnGuardTest, NestedAbortsDuringAsyncRecomputeKeepWorkerWritesInTheTurn)
{
    auto* w = static_cast<App::AgentLoopWriter*>(_doc->addObject("App::AgentLoopWriter", "Loop"));
    ASSERT_NE(w, nullptr);
    ASSERT_EQ(w->Value.getValue(), 0);

    App::AgentTurnGuard::begin(*_doc, "the turn");
    _a->Integer.setValue(1);
    App::agentLoopStarted = false;
    w->touch();
    App::GetApplication().queueRecomputeRequest(App::RecomputeRequest::fromDocumentObject(*w));
    while (!App::agentLoopStarted) {
        std::this_thread::yield();
    }
    for (int i = 0; i < 300; ++i) {
        _doc->openTransaction("nested");
        _a->Integer.setValue(100 + i);
        _doc->abortTransaction();
    }
    ASSERT_TRUE(App::AutoTransaction::waitForRecompute(_doc->getName(), 30s));

    const auto report = App::AgentTurnGuard::end(*_doc, false, 10s);
    EXPECT_FALSE(report.refused);
    EXPECT_EQ(report.workerRefusals, 0);
    EXPECT_EQ(w->Value.getValue(), 0) << "worker writes escaped the guarded turn";
    EXPECT_EQ(_a->Integer.getValue(), 4711);
    EXPECT_EQ(undos(), 0);
}

TEST_F(AgentTurnGuardTest, CanEndReportsBusyWorkerAndLock)
{
    App::AgentTurnGuard::begin(*_doc, "t");
    std::string why;
    EXPECT_TRUE(App::AgentTurnGuard::canEnd(*_doc, 10ms, why));
    {
        App::TransactionLocker locker(_doc);
        EXPECT_FALSE(App::AgentTurnGuard::canEnd(*_doc, 10ms, why));
        EXPECT_NE(why.find("locked"), std::string::npos);
    }
    App::AgentTurnGuard::end(*_doc, false);
    EXPECT_FALSE(App::AgentTurnGuard::canEnd(*_doc, 10ms, why));
}
