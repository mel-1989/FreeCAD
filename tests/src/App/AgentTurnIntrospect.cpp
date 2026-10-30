// SPDX-License-Identifier: LGPL-2.1-or-later

#include <chrono>
#include <map>
#include <string>
#include <vector>

#include <gtest/gtest.h>

#include "App/AgentTurnGuard.h"
#include "App/AgentTurnIntrospect.h"
#include "App/Application.h"
#include "App/Document.h"
#include "App/DocumentObject.h"
#include "App/FeatureTest.h"
#include "App/PropertyStandard.h"
#include <Base/Exception.h>
#include <src/App/InitApplication.h>

using namespace std::chrono_literals;

namespace
{

struct Seen
{
    std::string name;
    App::AgentObjectChange::Status status;
    bool replaced;
    std::map<std::string, App::AgentObjectChange::PropertyChange> props;
    std::map<std::string, std::string> oldText;
};

}  // namespace

class AgentTurnIntrospectTest: public ::testing::Test
{
protected:
    static void SetUpTestSuite()
    {
        tests::initApplication();
    }

    void SetUp() override
    {
        _docName = App::GetApplication().getUniqueDocumentName("agent_introspect");
        _doc = App::GetApplication().newDocument(_docName.c_str(), "testUser");
        App::GetApplication().setActiveDocument(_doc);
        _a = static_cast<App::FeatureTest*>(_doc->addObject("App::FeatureTest", "A"));
        _b = static_cast<App::FeatureTest*>(_doc->addObject("App::FeatureTest", "B"));
        ASSERT_NE(_a, nullptr);
        ASSERT_NE(_b, nullptr);
        _a->Integer.setValue(1);
        _b->Integer.setValue(2);
    }

    void TearDown() override
    {
        if (_doc && App::GetApplication().getDocument(_docName.c_str())) {
            if (App::AgentTurnGuard::isActive(*_doc)) {
                App::AgentTurnGuard::end(*_doc, false, 5s);
            }
            App::GetApplication().closeDocument(_docName.c_str());
        }
    }

    std::vector<Seen> changes(int* segments = nullptr, int* uncomparable = nullptr)
    {
        std::vector<Seen> out;
        App::AgentTurnIntrospect::collect(*_doc, 5s, [&](const App::AgentTurnChangeSet& cs) {
            if (segments) {
                *segments = cs.segments;
            }
            if (uncomparable) {
                *uncomparable = cs.uncomparable;
            }
            for (const auto& o : cs.objects) {
                Seen s {o.name, o.status, o.replaced, {}, {}};
                for (const auto& p : o.properties) {
                    s.props[p.name] = p.change;
                }
                out.push_back(std::move(s));
            }
        });
        return out;
    }

    static const Seen* find(const std::vector<Seen>& v, const std::string& name, App::AgentObjectChange::Status st)
    {
        for (const auto& s : v) {
            if (s.name == name && s.status == st) {
                return &s;
            }
        }
        return nullptr;
    }

    std::string _docName;
    App::Document* _doc {};
    App::FeatureTest* _a {};
    App::FeatureTest* _b {};
};

using Status = App::AgentObjectChange::Status;
using Change = App::AgentObjectChange::PropertyChange;

TEST_F(AgentTurnIntrospectTest, OutsideATurnItThrows)
{
    EXPECT_THROW(changes(), Base::RuntimeError);
}

TEST_F(AgentTurnIntrospectTest, TurnThatChangesNothingIsEmpty)
{
    App::AgentTurnGuard::begin(*_doc, "turn");
    EXPECT_TRUE(changes().empty());
    App::AgentTurnGuard::end(*_doc, false);
}

TEST_F(AgentTurnIntrospectTest, ModifiedPropertyCarriesOldAndCurrentValue)
{
    App::AgentTurnGuard::begin(*_doc, "turn");
    _a->Integer.setValue(10);
    bool checked = false;
    App::AgentTurnIntrospect::collect(*_doc, 5s, [&](const App::AgentTurnChangeSet& cs) {
        ASSERT_EQ(cs.objects.size(), 1U);
        const auto& o = cs.objects[0];
        EXPECT_EQ(o.name, "A");
        EXPECT_EQ(o.status, Status::Modified);
        ASSERT_EQ(o.properties.size(), 1U);
        const auto& p = o.properties[0];
        EXPECT_EQ(p.name, "Integer");
        ASSERT_NE(p.oldValue, nullptr);
        ASSERT_NE(p.current, nullptr);
        EXPECT_EQ(static_cast<const App::PropertyInteger*>(p.oldValue)->getValue(), 1);
        EXPECT_EQ(static_cast<const App::PropertyInteger*>(p.current)->getValue(), 10);
        checked = true;
    });
    EXPECT_TRUE(checked);
    App::AgentTurnGuard::end(*_doc, false);
}

TEST_F(AgentTurnIntrospectTest, TwoSetsAcrossSegmentsReportTheTurnStartValue)
{
    App::AgentTurnGuard::begin(*_doc, "turn");
    _a->Integer.setValue(5);
    _doc->openTransaction("nested");
    _a->Integer.setValue(9);
    _doc->commitTransaction();
    int segments = 0;
    bool checked = false;
    App::AgentTurnIntrospect::collect(*_doc, 5s, [&](const App::AgentTurnChangeSet& cs) {
        segments = cs.segments;
        ASSERT_EQ(cs.objects.size(), 1U);
        ASSERT_EQ(cs.objects[0].properties.size(), 1U);
        const auto& p = cs.objects[0].properties[0];
        EXPECT_EQ(static_cast<const App::PropertyInteger*>(p.oldValue)->getValue(), 1);
        EXPECT_EQ(static_cast<const App::PropertyInteger*>(p.current)->getValue(), 9);
        checked = true;
    });
    EXPECT_TRUE(checked);
    EXPECT_GE(segments, 2);
    App::AgentTurnGuard::end(*_doc, false);
}

TEST_F(AgentTurnIntrospectTest, PropertyRestoredToItsOldValueIsDropped)
{
    App::AgentTurnGuard::begin(*_doc, "turn");
    _a->Integer.setValue(5);
    _a->Integer.setValue(1);
    EXPECT_TRUE(changes().empty());
    App::AgentTurnGuard::end(*_doc, false);
}

TEST_F(AgentTurnIntrospectTest, AddedObjectIsReportedAdded)
{
    App::AgentTurnGuard::begin(*_doc, "turn");
    _doc->addObject("App::FeatureTest", "New");
    const auto v = changes();
    ASSERT_NE(find(v, "New", Status::Added), nullptr);
    App::AgentTurnGuard::end(*_doc, false);
}

TEST_F(AgentTurnIntrospectTest, RemovedObjectIsReportedRemovedWithItsName)
{
    App::AgentTurnGuard::begin(*_doc, "turn");
    _doc->removeObject("B");
    const auto v = changes();
    ASSERT_NE(find(v, "B", Status::Removed), nullptr);
    App::AgentTurnGuard::end(*_doc, false);
}

TEST_F(AgentTurnIntrospectTest, AddedThenRemovedInTheTurnIsOmitted)
{
    App::AgentTurnGuard::begin(*_doc, "turn");
    _doc->addObject("App::FeatureTest", "Temp");
    _doc->openTransaction("nested");
    _doc->removeObject("Temp");
    _doc->commitTransaction();
    EXPECT_TRUE(changes().empty());
    App::AgentTurnGuard::end(*_doc, false);
}

TEST_F(AgentTurnIntrospectTest, RemovedThenRecreatedUnderTheSameNameReportsBothReplaced)
{
    App::AgentTurnGuard::begin(*_doc, "turn");
    _doc->removeObject("B");
    _doc->openTransaction("nested");
    _doc->addObject("App::FeatureTest", "B");
    _doc->commitTransaction();
    const auto v = changes();
    const Seen* removed = find(v, "B", Status::Removed);
    const Seen* added = find(v, "B", Status::Added);
    ASSERT_NE(removed, nullptr);
    ASSERT_NE(added, nullptr);
    EXPECT_TRUE(removed->replaced);
    EXPECT_TRUE(added->replaced);
    App::AgentTurnGuard::end(*_doc, false);
}

TEST_F(AgentTurnIntrospectTest, NestedAbortChangesAreAbsent)
{
    App::AgentTurnGuard::begin(*_doc, "turn");
    _a->Integer.setValue(7);
    _doc->openTransaction("nested");
    _b->Integer.setValue(99);
    _doc->addObject("App::FeatureTest", "Gone");
    _doc->abortTransaction();
    const auto v = changes();
    EXPECT_NE(find(v, "A", Status::Modified), nullptr);
    EXPECT_EQ(find(v, "B", Status::Modified), nullptr);
    EXPECT_EQ(find(v, "Gone", Status::Added), nullptr);
    EXPECT_EQ(v.size(), 1U);
    App::AgentTurnGuard::end(*_doc, false);
}

TEST_F(AgentTurnIntrospectTest, DynamicPropertyAddedAndRemoved)
{
    _b->addDynamicProperty("App::PropertyInteger", "Old", "Extra", "doc");
    App::AgentTurnGuard::begin(*_doc, "turn");
    _a->addDynamicProperty("App::PropertyInteger", "Fresh", "Extra", "doc");
    _b->removeDynamicProperty("Old");
    const auto v = changes();
    const Seen* a = find(v, "A", Status::Modified);
    const Seen* b = find(v, "B", Status::Modified);
    ASSERT_NE(a, nullptr);
    ASSERT_NE(b, nullptr);
    ASSERT_EQ(a->props.count("Fresh"), 1U);
    EXPECT_EQ(a->props.at("Fresh"), Change::Added);
    ASSERT_EQ(b->props.count("Old"), 1U);
    EXPECT_EQ(b->props.at("Old"), Change::Removed);
    App::AgentTurnGuard::end(*_doc, false);
}

TEST_F(AgentTurnIntrospectTest, EachDocumentReportsItsOwnChanges)
{
    App::Document* other = App::GetApplication().newDocument("agent_introspect_other", "testUser");
    auto* c = static_cast<App::FeatureTest*>(other->addObject("App::FeatureTest", "C"));
    c->Integer.setValue(3);
    App::AgentTurnGuard::begin(*_doc, "turn");
    App::AgentTurnGuard::begin(*other, "turn");
    _a->Integer.setValue(11);
    c->Integer.setValue(12);
    const auto v = changes();
    EXPECT_EQ(v.size(), 1U);
    EXPECT_NE(find(v, "A", Status::Modified), nullptr);
    std::vector<std::string> names;
    App::AgentTurnIntrospect::collect(*other, 5s, [&](const App::AgentTurnChangeSet& cs) {
        for (const auto& o : cs.objects) {
            names.push_back(o.name);
        }
    });
    ASSERT_EQ(names.size(), 1U);
    EXPECT_EQ(names[0], "C");
    App::AgentTurnGuard::end(*other, false);
    App::AgentTurnGuard::end(*_doc, false);
    App::GetApplication().closeDocument("agent_introspect_other");
}
