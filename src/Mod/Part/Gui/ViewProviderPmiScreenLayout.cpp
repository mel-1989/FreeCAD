// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2026 The FreeCAD project association AISBL              *
 *                                                                         *
 *   This file is part of FreeCAD.                                         *
 *                                                                         *
 *   FreeCAD is free software: you can redistribute it and/or modify it    *
 *   under the terms of the GNU Lesser General Public License as           *
 *   published by the Free Software Foundation, either version 2.1 of the  *
 *   License, or (at your option) any later version.                       *
 *                                                                         *
 *   FreeCAD is distributed in the hope that it will be useful, but        *
 *   WITHOUT ANY WARRANTY; without even the implied warranty of            *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU     *
 *   Lesser General Public License for more details.                       *
 *                                                                         *
 *   You should have received a copy of the GNU Lesser General Public      *
 *   License along with FreeCAD. If not, see                               *
 *   <https://www.gnu.org/licenses/>.                                      *
 *                                                                         *
 **************************************************************************/

// The camera hook follows EditableDatumLabel::activate (an SoNodeSensor on the viewer's camera, re-attached when the
// camera node is replaced); the pixel-to-world scale at a depth follows SoAutoZoomTranslation (SbViewVolume::
// getWorldToScreenScale). Labels keep their plane, size and anchor: the solved pixel shift is mapped to a shift of the
// label parallel to the image plane at the depth of the label's centre. The leaders follow ASME Y14.41 practice
// (a leader joins a note to the feature it refers to).

#include <algorithm>
#include <string>
#include <array>
#include <cmath>
#include <map>
#include <set>
#include <vector>

#include <QCoreApplication>
#include <QEvent>
#include <QObject>
#include <QPointer>
#include <QTimer>

#include <Inventor/SbViewVolume.h>
#include <Inventor/SbViewportRegion.h>
#include <Inventor/SbMatrix.h>
#include <Inventor/nodes/SoCamera.h>
#include <Inventor/nodes/SoOrthographicCamera.h>
#include <Inventor/nodes/SoPerspectiveCamera.h>
#include <Inventor/sensors/SoNodeSensor.h>

#include <App/Application.h>
#include <App/Document.h>
#include <Base/Parameter.h>
#include <fastsignals/signal.h>

#include <Gui/Application.h>
#include <Gui/Document.h>
#include <Gui/View3DInventor.h>
#include <Gui/View3DInventorViewer.h>

#include "ViewProviderPmi.h"
#include "ViewProviderPmiScreenLayout.h"

using namespace PartGui;

namespace
{

struct Pt
{
    double x {0}, y {0};
};
using Poly = std::array<Pt, 4>;

struct Box
{
    double x0, y0, x1, y1;
};

Box boxOf(const Poly& p)
{
    Box b {p[0].x, p[0].y, p[0].x, p[0].y};
    for (const Pt& q : p) {
        b.x0 = std::min(b.x0, q.x);
        b.y0 = std::min(b.y0, q.y);
        b.x1 = std::max(b.x1, q.x);
        b.y1 = std::max(b.y1, q.y);
    }
    return b;
}

/// Separating-axis test of two convex quadrilaterals; they overlap when no axis separates them by at least gap pixels
bool overlaps(const Poly& a, const Poly& b, double gap)
{
    for (const Poly* poly : {&a, &b}) {
        for (int i = 0; i < 4; ++i) {
            const Pt& p = (*poly)[i];
            const Pt& q = (*poly)[(i + 1) % 4];
            double ax = -(q.y - p.y), ay = q.x - p.x;
            const double len = std::hypot(ax, ay);
            if (len < 1e-9) {
                continue;
            }
            ax /= len;
            ay /= len;
            double a0 = 1e30, a1 = -1e30, b0 = 1e30, b1 = -1e30;
            for (const Pt& v : a) {
                const double d = ax * v.x + ay * v.y;
                a0 = std::min(a0, d);
                a1 = std::max(a1, d);
            }
            for (const Pt& v : b) {
                const double d = ax * v.x + ay * v.y;
                b0 = std::min(b0, d);
                b1 = std::max(b1, d);
            }
            if (a1 + gap <= b0 || b1 + gap <= a0) {
                return false;
            }
        }
    }
    return true;
}

double boxOverlapArea(const Box& a, const Box& b)
{
    const double w = std::min(a.x1, b.x1) - std::max(a.x0, b.x0);
    const double h = std::min(a.y1, b.y1) - std::max(a.y0, b.y0);
    return (w > 0 && h > 0) ? w * h : 0.0;
}

Poly shifted(const Poly& p, double dx, double dy)
{
    Poly r = p;
    for (Pt& q : r) {
        q.x += dx;
        q.y += dy;
    }
    return r;
}

struct Entry
{
    ViewProviderPmiItem* vp {nullptr};
    ViewProviderPmiItem::ScreenData data;
    Poly poly;           // nominal pick quad on the screen, pixels, y up
    Pt anchor, centre;   // the governed point and the quad's centre on the screen
    SbVec3f worldCentre;
    double dx {0}, dy {0};
    bool movable {false};
};

/// The pixel shifts of the labels: greedy, in name order. A label stays where it is when it covers no label placed
/// before it; otherwise it takes the nearest position on rings around its nominal place that is inside the bound around
/// the point it governs and clear of the others, and when there is none the in-bound position of least overlap
void solve(std::vector<Entry>& es, double bound)
{
    constexpr double step = 4.0;
    constexpr int sectors = 24;
    constexpr double gap = 2.0;  // pixels left between two labels
    constexpr double pi = 3.14159265358979323846;
    std::vector<Poly> placed;
    std::vector<Box> placedBox;
    for (Entry& e : es) {
        auto clashes = [&](const Poly& p, const Box& bx) {
            for (size_t i = 0; i < placed.size(); ++i) {
                if (bx.x0 < placedBox[i].x1 + gap && placedBox[i].x0 < bx.x1 + gap && bx.y0 < placedBox[i].y1 + gap
                    && placedBox[i].y0 < bx.y1 + gap && overlaps(p, placed[i], gap)) {
                    return true;
                }
            }
            return false;
        };
        auto cost = [&](const Box& bx) {
            double c = 0;
            for (const Box& o : placedBox) {
                c += boxOverlapArea(bx, o);
            }
            return c;
        };
        const double start = std::hypot(e.centre.x - e.anchor.x, e.centre.y - e.anchor.y);
        const double radius = bound;  // the bound is absolute: a label whose nominal place is farther is pulled inside it
        double bestDx = 0, bestDy = 0, bestCost = 1e30, bestDist = 0;
        bool found = false;
        {
            const Box b0 = boxOf(e.poly);
            if (!e.movable || (start <= radius && !clashes(e.poly, b0))) {
                bestDx = bestDy = 0;
                found = true;
            }
            else {
                bestCost = cost(b0);
            }
        }
        if (!found) {
            // outward direction first: away from the point the label governs, so that a label moves off its feature's edge
            double base = std::atan2(e.centre.y - e.anchor.y, e.centre.x - e.anchor.x);
            if (start < 1e-6) {
                base = pi / 2;
            }
            std::vector<std::pair<double, double>> dirs;  // (angle offset order, angle)
            for (int k = 0; k < sectors; ++k) {
                const int j = (k + 1) / 2;
                const double a = base + (k % 2 ? 1 : -1) * (2 * pi * j / sectors);
                dirs.push_back({a, a});
            }
            for (double r = (start > radius ? start - radius : 0.0) + step; r <= start + radius + step && !found; r += step) {
                for (const auto& d : dirs) {
                    const double dx = r * std::cos(d.second), dy = r * std::sin(d.second);
                    if (std::hypot(e.centre.x + dx - e.anchor.x, e.centre.y + dy - e.anchor.y) > radius) {
                        continue;
                    }
                    const Poly p = shifted(e.poly, dx, dy);
                    const Box b = boxOf(p);
                    if (!clashes(p, b)) {
                        bestDx = dx;
                        bestDy = dy;
                        found = true;
                        break;
                    }
                    const double c = cost(b);
                    if (c < bestCost - 1e-9 || (std::fabs(c - bestCost) <= 1e-9 && r < bestDist)) {
                        bestCost = c;
                        bestDx = dx;
                        bestDy = dy;
                        bestDist = r;
                    }
                }
            }
        }
        e.dx = bestDx;
        e.dy = bestDy;
        const Poly p = shifted(e.poly, e.dx, e.dy);
        placed.push_back(p);
        placedBox.push_back(boxOf(p));
    }
}

class Manager;

struct Tracked
{
    QPointer<Gui::View3DInventorViewer> viewer;
    SoNodeSensor* sensor {nullptr};
    std::pair<Manager*, QObject*>* cbData {nullptr};
    SoCamera* camera {nullptr};
    std::vector<double> signature;
    bool dirty {true};
    bool recheck {false};
};

class Manager: public QObject, public Base::Observer<const char*>
{
public:
    Manager()
        : QObject(qApp)
    {
        prefs = App::GetApplication().GetParameterGroupByPath("User parameter:BaseApp/Preferences/Mod/Part/Pmi");
        prefs->Attach(this);
        if (Gui::Application::Instance) {
            // a 3D view opened after its document's labels were drawn
            activate = Gui::Application::Instance->signalActivateView.connect([this](const Gui::MDIView* view) {
                auto* v3 = dynamic_cast<const Gui::View3DInventor*>(view);
                if (v3 && v3->getGuiDocument()) {
                    // the view is not yet listed among its document's views while it is being activated for the first time
                    markDirty(v3->getGuiDocument()->getDocument());
                }
            });
        }
    }

    void OnChange(Base::Subject<const char*>&, const char* reason) override
    {
        if (reason && std::string(reason) == "ScreenLayout") {
            for (auto& [v, t] : tracked) {
                t.dirty = true;
                layout(t);
            }
        }
    }

    ~Manager() override
    {
        prefs->Detach(this);
        for (auto& [v, t] : tracked) {
            release(t);
        }
    }

    static Manager& instance()
    {
        static QPointer<Manager> m;
        if (!m) {
            m = new Manager;
        }
        return *m;
    }

    void sync(const App::Document* doc)
    {
        Gui::Document* gdoc = Gui::Application::Instance ? Gui::Application::Instance->getDocument(doc) : nullptr;
        if (!gdoc) {
            return;
        }
        for (Gui::MDIView* v : gdoc->getMDIViewsOfType(Gui::View3DInventor::getClassTypeId(), true)) {
            auto* view = static_cast<Gui::View3DInventor*>(v);
            Gui::View3DInventorViewer* viewer = view->getViewer();
            if (viewer && !tracked.count(viewer)) {
                track(viewer);
            }
        }
        for (auto& [v, t] : tracked) {
            if (docOf(t) == doc) {
                t.dirty = true;
                layout(t);
            }
        }
    }

    void markDirty(const App::Document* doc)
    {
        for (auto& [v, t] : tracked) {
            if (docOf(t) == doc) {
                t.dirty = true;
            }
        }
        pendingDocs.insert(doc);
        if (!timerArmed) {
            timerArmed = true;
            QTimer::singleShot(0, this, [this]() {
                timerArmed = false;
                auto docs = pendingDocs;
                pendingDocs.clear();
                for (const App::Document* d : docs) {
                    sync(d);  // does nothing for a document that has no Gui document any more
                }
            });
        }
    }

    bool eventFilter(QObject* obj, QEvent* ev) override
    {
        if (ev->type() == QEvent::Resize) {
            auto it = tracked.find(obj);
            if (it != tracked.end()) {
                it->second.dirty = true;
                const App::Document* d = docOf(it->second);
                if (d) {
                    markDirty(d);
                }
            }
        }
        return false;
    }

private:
    std::map<QObject*, Tracked> tracked;
    std::set<const App::Document*> pendingDocs;
    bool timerArmed {false};
    bool solving {false};
    ParameterGrp::handle prefs;
    fastsignals::scoped_connection activate;

    static const App::Document* docOf(const Tracked& t)
    {
        if (!t.viewer) {
            return nullptr;
        }
        Gui::Document* g = t.viewer->getDocument();
        return g ? g->getDocument() : nullptr;
    }

    void release(Tracked& t)
    {
        if (t.sensor) {
            t.sensor->detach();
            delete t.sensor;
            t.sensor = nullptr;
            delete t.cbData;
            t.cbData = nullptr;
        }
        t.camera = nullptr;
    }

    void attachSensor(Tracked& t)
    {
        SoCamera* cam = t.viewer ? t.viewer->getCamera() : nullptr;
        if (cam == t.camera && t.sensor) {
            return;
        }
        if (t.sensor) {
            t.sensor->detach();
        }
        else {
            t.cbData = new std::pair<Manager*, QObject*>(this, t.viewer.data());
            t.sensor = new SoNodeSensor(
                [](void* data, SoSensor*) {
                    auto* self = static_cast<std::pair<Manager*, QObject*>*>(data);
                    self->first->cameraChanged(self->second);
                },
                t.cbData
            );
        }
        t.camera = cam;
        if (cam) {
            t.sensor->attach(cam);
        }
    }

    void track(Gui::View3DInventorViewer* viewer)
    {
        Tracked t;
        t.viewer = viewer;
        QObject* key = viewer;
        viewer->installEventFilter(this);
        QObject::connect(viewer, &QObject::destroyed, this, [this, key]() {
            auto it = tracked.find(key);
            if (it != tracked.end()) {
                release(it->second);
                tracked.erase(it);
            }
        });
        // the viewer replaces its camera node when the camera type changes (perspective or orthographic): follow it
        QObject::connect(viewer, &Gui::View3DInventorViewer::cameraChanged, this, [this, key]() {
            auto it = tracked.find(key);
            if (it != tracked.end()) {
                it->second.dirty = true;
                layout(it->second);
            }
        });
        auto& slot = tracked[key];
        slot = t;
        attachSensor(slot);
    }

    void cameraChanged(QObject* key)
    {
        auto it = tracked.find(key);
        if (it == tracked.end() || solving) {
            return;
        }
        layout(it->second);
        // Coin runs an immediate sensor when the camera starts to change, not after each of its fields (position,
        // orientation, height): look again once the event loop has the whole change; the signature makes this cheap
        if (!it->second.recheck) {
            it->second.recheck = true;
            QTimer::singleShot(0, this, [this, key]() {
                auto again = tracked.find(key);
                if (again != tracked.end()) {
                    again->second.recheck = false;
                    layout(again->second);
                }
            });
        }
    }

    void layout(Tracked& t)
    {
        if (solving || !t.viewer) {
            return;
        }
        attachSensor(t);
        SoCamera* cam = t.camera;
        const App::Document* doc = docOf(t);
        if (!cam || !doc) {
            return;
        }
        const SbViewportRegion& vp = t.viewer->getSoRenderManager()->getViewportRegion();
        const SbVec2s sz = vp.getViewportSizePixels();
        if (sz[0] <= 0 || sz[1] <= 0) {
            return;
        }
        const float aspect = vp.getViewportAspectRatio();
        std::vector<double> sig;
        {
            SbVec3f p = cam->position.getValue();
            float q[4];
            cam->orientation.getValue().getValue(q[0], q[1], q[2], q[3]);
            sig = {p[0], p[1], p[2], q[0], q[1], q[2], q[3], double(sz[0]), double(sz[1]), double(aspect)};
            if (cam->isOfType(SoOrthographicCamera::getClassTypeId())) {
                sig.push_back(static_cast<SoOrthographicCamera*>(cam)->height.getValue());
            }
            else if (cam->isOfType(SoPerspectiveCamera::getClassTypeId())) {
                sig.push_back(static_cast<SoPerspectiveCamera*>(cam)->heightAngle.getValue());
            }
            sig.push_back(cam->nearDistance.getValue());
            sig.push_back(App::GetApplication().GetParameterGroupByPath("User parameter:BaseApp/Preferences/Mod/Part/Pmi")->GetBool("ScreenLayout", true) ? 1.0 : 0.0);
            sig.push_back(cam->farDistance.getValue());
        }
        if (!t.dirty && sig == t.signature) {
            return;
        }
        t.signature = sig;
        t.dirty = false;
        solving = true;
        try {
            run(t, cam, doc, sz, aspect);
        }
        catch (...) {
            solving = false;
            throw;
        }
        solving = false;
    }

    void run(Tracked&, SoCamera* cam, const App::Document* doc, const SbVec2s& sz, float aspect)
    {
        const SbViewVolume vv = cam->getViewVolume(aspect);
        const double W = sz[0], H = sz[1];
        auto project = [&](const SbVec3f& w, Pt& out) {
            SbVec3f s;
            vv.projectToScreen(w, s);
            out.x = s[0] * W;
            out.y = s[1] * H;
            return std::isfinite(s[0]) && std::isfinite(s[1]) && s[2] >= 0.0f && s[2] <= 1.0f;
        };
        std::vector<Entry> es;
        for (ViewProviderPmiItem* vp : ViewProviderPmiItem::fallbackProviders(doc)) {
            Entry e;
            e.vp = vp;
            if (!vp->screenData(e.data)) {
                continue;
            }
            bool ok = true;
            SbVec3f c(0, 0, 0);
            for (int i = 0; i < 4; ++i) {
                SbVec3f w;
                e.data.toWorld.multVecMatrix(e.data.quad[i], w);
                c += w;
                ok = project(w, e.poly[i]) && ok;
            }
            c *= 0.25f;
            e.worldCentre = c;
            SbVec3f wa;
            e.data.toWorld.multVecMatrix(e.data.anchor, wa);
            ok = project(wa, e.anchor) && project(c, e.centre) && ok;
            e.movable = ok;  // a label behind the camera or outside the clip volume stays where it is
            es.push_back(e);
        }
        // the preference Mod/Part/Pmi ScreenLayout (default on) switches the declutter off: labels then sit at their nominal places
        const bool on = App::GetApplication().GetParameterGroupByPath("User parameter:BaseApp/Preferences/Mod/Part/Pmi")->GetBool("ScreenLayout", true);
        if (on) {
            solve(es, 0.15 * H);
        }


        SbVec3f right, up;  // the camera's right and up in world coordinates
        cam->orientation.getValue().multVec(SbVec3f(1, 0, 0), right);
        cam->orientation.getValue().multVec(SbVec3f(0, 1, 0), up);
        for (Entry& e : es) {
            SbVec3f local(0, 0, 0);
            const bool moved = std::hypot(e.dx, e.dy) >= 0.5;
            if (moved) {
                // one pixel at the depth of the label's centre, parallel to the image plane
                const float pixel = vv.getWorldToScreenScale(e.worldCentre, static_cast<float>(1.0 / W));
                const SbVec3f world = (right * static_cast<float>(e.dx) + up * static_cast<float>(e.dy)) * pixel;
                e.data.toLocal.multDirMatrix(world, local);
            }
            e.vp->setScreenOffset(local, moved);
        }
    }
};

}  // namespace

void PmiScreenLayout::sync(const App::Document* doc)
{
    if (doc && qApp) {
        Manager::instance().sync(doc);
    }
}

void PmiScreenLayout::markDirty(const App::Document* doc)
{
    if (doc && qApp) {
        Manager::instance().markDirty(doc);
    }
}
