// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Opt-in timers for the editor's rendering path (G10 slice 4c).         *
 *                                                                         *
 *   Off unless the environment variable FREECAD_SKETCHER_RENDER_TIMING    *
 *   names a file; the variable is read once.  When on, every scoped timer *
 *   appends one line "<name>\t<seconds>" to that file when it ends.  When *
 *   off a timer costs one test of a static flag and draws nothing.        *
 ***************************************************************************/

#ifndef SKETCHERGUI_RENDERTIMING_H
#define SKETCHERGUI_RENDERTIMING_H

#include <chrono>
#include <cstdio>
#include <cstdlib>

namespace SketcherGui
{

class RenderTiming
{
public:
    static const char* path()
    {
        static const char* p = std::getenv("FREECAD_SKETCHER_RENDER_TIMING");
        return (p && *p) ? p : nullptr;
    }

    static bool enabled()
    {
        static const bool on = path() != nullptr;
        return on;
    }

    static void record(const char* name, double seconds)
    {
        if (FILE* f = std::fopen(path(), "a")) {
            std::fprintf(f, "%s\t%.9f\n", name, seconds);
            std::fclose(f);
        }
    }

    class Scope
    {
    public:
        explicit Scope(const char* n)
            : name(n)
            , on(RenderTiming::enabled())
        {
            if (on) {
                start = std::chrono::steady_clock::now();
            }
        }
        ~Scope()
        {
            if (on) {
                std::chrono::duration<double> d = std::chrono::steady_clock::now() - start;
                RenderTiming::record(name, d.count());
            }
        }
        Scope(const Scope&) = delete;
        Scope& operator=(const Scope&) = delete;

    private:
        const char* name;
        bool on;
        std::chrono::steady_clock::time_point start;
    };
};

}  // namespace SketcherGui

#define SKETCHER_RENDER_TIMER(var, name) SketcherGui::RenderTiming::Scope var(name)

#endif  // SKETCHERGUI_RENDERTIMING_H
