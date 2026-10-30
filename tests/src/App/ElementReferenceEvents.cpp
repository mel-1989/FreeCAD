// SPDX-License-Identifier: LGPL-2.1-or-later

#include <string>
#include <vector>

#include <gtest/gtest.h>

#include "App/AgentTurnGuard.h"
#include "App/Application.h"
#include "App/Document.h"
#include "App/DocumentObject.h"
#include "App/ElementReferenceEvents.h"
#include "App/FeatureTest.h"
#include "App/PropertyLinks.h"
#include <src/App/InitApplication.h>

using App::ElementReferenceEvent;
using App::ElementReferenceJournal;
using Basis = ElementReferenceEvent::Basis;
using Kind = ElementReferenceEvent::Kind;

class ElementReferenceUndoTest: public ::testing::Test
{
protected:
    static void SetUpTestSuite()
    {
        tests::initApplication();
    }

    void SetUp() override
    {
        _docName = App::GetApplication().getUniqueDocumentName("ref_events");
        _doc = App::GetApplication().newDocument(_docName.c_str(), "testUser");
        _a = static_cast<App::FeatureTest*>(_doc->addObject("App::FeatureTest", "A"));
        _b = static_cast<App::FeatureTest*>(_doc->addObject("App::FeatureTest", "B"));
        _c = static_cast<App::FeatureTest*>(_doc->addObject("App::FeatureTest", "C"));
        ASSERT_NE(_a, nullptr);
        ASSERT_NE(_b, nullptr);
        ASSERT_NE(_c, nullptr);
        _doc->clearUndos();
    }

    void TearDown() override
    {
        if (App::GetApplication().getDocument(_docName.c_str())) {
            if (App::AgentTurnGuard::isActive(*_doc)) {
                App::AgentTurnGuard::end(*_doc, false, std::chrono::seconds(5));
            }
            App::GetApplication().closeDocument(_docName.c_str());
        }
    }

    // One committed undo entry that sets A.LinkSub.
    void setLinkSub(App::DocumentObject* target, const std::string& sub)
    {
        _doc->openTransaction("link");
        _a->LinkSub.setValue(target, std::vector<std::string> {sub});
        _doc->commitTransaction();
    }

    std::vector<ElementReferenceEvent> since(std::uint64_t seq) const
    {
        return ElementReferenceJournal::events(_doc, seq);
    }

    std::string _docName;
    App::Document* _doc {};
    App::FeatureTest* _a {};
    App::FeatureTest* _b {};
    App::FeatureTest* _c {};
};

TEST_F(ElementReferenceUndoTest, UndoAndRedoReportTheChangedSub)
{
    setLinkSub(_b, "Face3");
    setLinkSub(_b, "Face5");
    const std::string first = _a->LinkSub.getSubValues().front();
    ASSERT_EQ(_a->LinkSub.getSubValues().front(), "Face5");

    const auto mark = ElementReferenceJournal::lastSeq();
    ASSERT_TRUE(_doc->undo());
    auto events = since(mark);
    ASSERT_EQ(events.size(), 1U);
    EXPECT_EQ(events[0].basis, Basis::Undo);
    EXPECT_EQ(events[0].kind, Kind::Remapped);
    EXPECT_EQ(events[0].owner, "A");
    EXPECT_EQ(events[0].property, "LinkSub");
    EXPECT_EQ(events[0].subIndex, 0);
    EXPECT_EQ(events[0].oldSub, "Face5");
    EXPECT_EQ(events[0].newSub, "Face3");
    EXPECT_EQ(events[0].oldTarget, "B");
    EXPECT_EQ(events[0].target, "B");
    EXPECT_EQ(_a->LinkSub.getSubValues().front(), "Face3");

    const auto mark2 = ElementReferenceJournal::lastSeq();
    ASSERT_TRUE(_doc->redo());
    events = since(mark2);
    ASSERT_EQ(events.size(), 1U);
    EXPECT_EQ(events[0].basis, Basis::Redo);
    EXPECT_EQ(events[0].oldSub, "Face3");
    EXPECT_EQ(events[0].newSub, "Face5");
}

TEST_F(ElementReferenceUndoTest, UndoThatTouchesNoReferenceEmitsNothing)
{
    setLinkSub(_b, "Face3");
    _doc->openTransaction("number");
    _a->Integer.setValue(42);
    _doc->commitTransaction();

    const auto mark = ElementReferenceJournal::lastSeq();
    ASSERT_TRUE(_doc->undo());
    EXPECT_TRUE(since(mark).empty());
    ASSERT_TRUE(_doc->redo());
    EXPECT_TRUE(since(mark).empty());
    EXPECT_EQ(_a->LinkSub.getSubValues().front(), "Face3");
}

TEST_F(ElementReferenceUndoTest, RetargetedLinkIsReportedWithBothTargets)
{
    setLinkSub(_b, "Face3");
    setLinkSub(_c, "Face3");
    const auto mark = ElementReferenceJournal::lastSeq();
    ASSERT_TRUE(_doc->undo());
    const auto events = since(mark);
    ASSERT_EQ(events.size(), 1U);
    EXPECT_EQ(events[0].oldTarget, "C");
    EXPECT_EQ(events[0].target, "B");
    EXPECT_EQ(events[0].oldSub, events[0].newSub);
}

TEST_F(ElementReferenceUndoTest, ListGrowingAndShrinkingAreAddedAndRemoved)
{
    _doc->openTransaction("one");
    _a->LinkSubList.setValues({_b}, std::vector<const char*> {"Edge1"});
    _doc->commitTransaction();
    _doc->openTransaction("three");
    _a->LinkSubList.setValues({_b, _b, _c},
                              std::vector<const char*> {"Edge1", "Edge2", "Edge3"});
    _doc->commitTransaction();

    auto mark = ElementReferenceJournal::lastSeq();
    ASSERT_TRUE(_doc->undo());
    auto events = since(mark);
    ASSERT_EQ(events.size(), 2U);
    for (const auto& e : events) {
        EXPECT_EQ(e.kind, Kind::Removed);
        EXPECT_EQ(e.basis, Basis::Undo);
        EXPECT_TRUE(e.newSub.empty());
        EXPECT_EQ(e.property, "LinkSubList");
    }
    EXPECT_EQ(events[0].subIndex, 1);
    EXPECT_EQ(events[0].oldSub, "Edge2");
    EXPECT_EQ(events[1].subIndex, 2);
    EXPECT_EQ(events[1].oldTarget, "C");

    mark = ElementReferenceJournal::lastSeq();
    ASSERT_TRUE(_doc->redo());
    events = since(mark);
    ASSERT_EQ(events.size(), 2U);
    for (const auto& e : events) {
        EXPECT_EQ(e.kind, Kind::Added);
        EXPECT_EQ(e.basis, Basis::Redo);
        EXPECT_TRUE(e.oldSub.empty());
    }
    EXPECT_EQ(events[1].target, "C");
    EXPECT_EQ(events[1].newSub, "Edge3");
}

TEST_F(ElementReferenceUndoTest, MissingMarkIsLostAndRestored)
{
    setLinkSub(_b, "Edge4");
    setLinkSub(_b, "?Edge4");
    auto mark = ElementReferenceJournal::lastSeq();
    ASSERT_TRUE(_doc->undo());
    auto events = since(mark);
    ASSERT_EQ(events.size(), 1U);
    EXPECT_EQ(events[0].kind, Kind::Restored);
    mark = ElementReferenceJournal::lastSeq();
    ASSERT_TRUE(_doc->redo());
    events = since(mark);
    ASSERT_EQ(events.size(), 1U);
    EXPECT_EQ(events[0].kind, Kind::Lost);
}

TEST_F(ElementReferenceUndoTest, UndoOfAnObjectDeletionAndCreationReportsItsReferences)
{
    setLinkSub(_b, "Face3");
    _doc->openTransaction("delete");
    _doc->removeObject("A");
    _doc->commitTransaction();

    auto mark = ElementReferenceJournal::lastSeq();
    ASSERT_TRUE(_doc->undo());
    auto events = since(mark);
    ASSERT_EQ(events.size(), 1U);
    EXPECT_EQ(events[0].kind, Kind::Added);
    EXPECT_EQ(events[0].owner, "A");
    EXPECT_EQ(events[0].newSub, "Face3");

    mark = ElementReferenceJournal::lastSeq();
    ASSERT_TRUE(_doc->redo());
    events = since(mark);
    ASSERT_EQ(events.size(), 1U);
    EXPECT_EQ(events[0].kind, Kind::Removed);
    EXPECT_EQ(events[0].oldSub, "Face3");
}

TEST_F(ElementReferenceUndoTest, AbortedTransactionReportsRollback)
{
    setLinkSub(_b, "Face3");
    const auto mark = ElementReferenceJournal::lastSeq();
    _doc->openTransaction("fails");
    _a->LinkSub.setValue(_b, std::vector<std::string> {"Face7"});
    _doc->abortTransaction();
    const auto events = since(mark);
    ASSERT_EQ(events.size(), 1U);
    EXPECT_EQ(events[0].basis, Basis::Rollback);
    EXPECT_EQ(events[0].oldSub, "Face7");
    EXPECT_EQ(events[0].newSub, "Face3");
    EXPECT_EQ(_a->LinkSub.getSubValues().front(), "Face3");
}

TEST_F(ElementReferenceUndoTest, NestedAbortInsideAnAgentTurnReportsRollback)
{
    setLinkSub(_b, "Face3");
    App::AgentTurnGuard::begin(*_doc, "turn");
    _a->LinkSub.setValue(_b, std::vector<std::string> {"Face4"});  // stays in the turn
    const auto mark = ElementReferenceJournal::lastSeq();
    _doc->openTransaction("savepoint");
    _a->LinkSub.setValue(_c, std::vector<std::string> {"Face9"});
    _doc->abortTransaction();
    const auto events = since(mark);
    ASSERT_EQ(events.size(), 1U);
    EXPECT_EQ(events[0].basis, Basis::Rollback);
    EXPECT_EQ(events[0].oldTarget, "C");
    EXPECT_EQ(events[0].oldSub, "Face9");
    EXPECT_EQ(events[0].target, "B");
    EXPECT_EQ(events[0].newSub, "Face4");

    // the whole turn aborted: back to Face3
    const auto mark2 = ElementReferenceJournal::lastSeq();
    App::AgentTurnGuard::end(*_doc, false);
    const auto ended = since(mark2);
    ASSERT_EQ(ended.size(), 1U);
    EXPECT_EQ(ended[0].basis, Basis::Rollback);
    EXPECT_EQ(ended[0].oldSub, "Face4");
    EXPECT_EQ(ended[0].newSub, "Face3");
}

TEST_F(ElementReferenceUndoTest, SubscribersSeeTheEventsAndAfterwardsRecordingResumes)
{
    setLinkSub(_b, "Face3");
    setLinkSub(_b, "Face5");
    int seen = 0;
    auto connection = ElementReferenceJournal::signalEvent.connect(
        [&seen](const ElementReferenceEvent& e) { seen += e.basis == Basis::Undo ? 1 : 0; });
    ASSERT_TRUE(_doc->undo());
    EXPECT_EQ(seen, 1);
    EXPECT_FALSE(ElementReferenceJournal::suppressed());
}
