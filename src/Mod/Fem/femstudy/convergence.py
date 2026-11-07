# SPDX-License-Identifier: LGPL-2.1-or-later
"""Richardson estimate, observed order, monotone test, singularity test and the final verdict (SPEC.md, Verdicts and codes)."""

import math

SINGULAR_GROWTH = 1.30  # last-step growth of max von Mises above which the stress is a mesh artefact
REVERSAL_FRACTION = 0.1  # a non-monotone step counts only when the last difference exceeds this fraction of `target`
QUANTITIES = ("max_displacement", "energy")


def mesh_sizes(counts):
    """Relative element sizes from element counts, h proportional to N^(-1/3). Gmsh's CharacteristicLengthMax is only an upper bound: on a thin
    section the real mesh does not halve when the setting does (88, 290, 1336 elements for h0, h0/2, h0/4 on a 12 x 20 mm bar), so the nominal ratio
    would misreport the order of convergence."""
    return [n ** (-1.0 / 3.0) for n in counts]


def _ratio_of_differences(p, h1, h2, h3):
    return (h1**p - h2**p) / (h2**p - h3**p)


def richardson(u1, u2, u3, h1, h2, h3):
    """For three values on meshes of size h1 > h2 > h3: (limit, observed order or None, relative error of u3 against the limit, reversed).
    The order comes from the shrinking of the differences, clamped to [0.5, 4]; it is reported whenever the differences shrink. The limit uses
    that order when the sequence keeps its sign and shrinks, and order 1 (the conservative choice) when it reverses or does not shrink.
    `reversed` is True when the two differences have opposite signs."""
    d12, d23 = u2 - u1, u3 - u2
    if d23 == 0:
        return u3, None, 0.0, False
    reversed_ = d12 * d23 < 0
    # the differences shrink when their ratio exceeds the p -> 0 limit of _ratio_of_differences, ln(h1/h2) / ln(h2/h3): steps in h are uneven, so a
    # plain |d23| < |d12| misreads an order-1 sequence on steps of 1.44 then 1.97 as not shrinking
    shrinks = d12 != 0 and abs(d12) / abs(d23) > math.log(h1 / h2) / math.log(h2 / h3)
    order = None
    if shrinks:
        g = abs(d12) / abs(d23)
        lo, hi = 0.5, 4.0
        if g <= _ratio_of_differences(lo, h1, h2, h3):
            order = lo
        elif g >= _ratio_of_differences(hi, h1, h2, h3):
            order = hi
        else:
            for _ in range(60):
                mid = 0.5 * (lo + hi)
                if _ratio_of_differences(mid, h1, h2, h3) < g:
                    lo = mid
                else:
                    hi = mid
            order = 0.5 * (lo + hi)
    p = order if (order is not None and not reversed_) else 1.0
    limit = u3 + d23 * h3**p / (h2**p - h3**p)
    err = abs(limit - u3) / abs(limit) if limit else 0.0
    return limit, order, err, reversed_


def two_level(u1, u2, h1, h2):
    """With only two levels: order 1 is assumed, the estimate is the difference scaled to the finest level."""
    limit = u2 + (u2 - u1) * h2 / (h1 - h2)
    return limit, None, (abs(limit - u2) / abs(limit) if limit else 0.0)


def stress_growth(levels):
    """Max von Mises on the last refinement step: finest over the one before (None when undefined)."""
    if len(levels) < 2 or not levels[-2]["max_von_mises"] > 0:
        return None
    return levels[-1]["max_von_mises"] / levels[-2]["max_von_mises"]


def assess(levels, target):
    """Convergence numbers from the levels (coarse to fine), at least two. Returns a dict with extrapolated values, order, estimated_error,
    monotone (False only for a sign reversal whose last difference exceeds a tenth of `target`), and growth."""
    last = levels[-3:]
    hs = mesh_sizes([lv["elements"] for lv in last])
    out = {"extrapolated": {}, "order": None, "estimated_error": None, "monotone": True, "reversal": False, "growth": stress_growth(levels)}
    errs = []
    for q in QUANTITIES + ("max_von_mises",):
        if len(levels) >= 3:
            u1, u2, u3 = (lv[q] for lv in last)
            limit, order, err, rev = richardson(u1, u2, u3, *hs)
            if q in QUANTITIES and rev:
                if abs(u3 - u2) > REVERSAL_FRACTION * target * abs(u3):
                    out["monotone"] = False
                else:
                    out["reversal"] = True
        else:
            limit, order, err = two_level(last[0][q], last[1][q], *hs)
        out["extrapolated"][q] = limit
        if q in QUANTITIES:
            errs.append(err)
        if q == "max_displacement":
            out["order"] = order
    out["estimated_error"] = max(errs)
    return out


def verdict(levels, target, cap_hit, a):
    """(verdict, code, reason) for a study whose levels all passed the post-solve checks. `a` is `assess` output (None when fewer than two levels)."""
    if a is None:
        return "unconverged", "element_cap", "the element cap stopped refinement before a second level, so no convergence estimate exists"
    if cap_hit:
        return "unconverged", "element_cap", f"refinement stopped after {len(levels)} level(s) because the next mesh would exceed the element cap; estimated error {a['estimated_error']:.2e}"
    if not a["monotone"]:
        return "unconverged", "not_monotone", "the max displacement or the energy reverses direction between the last two refinements by more than a tenth of the target"
    if a["estimated_error"] > target:
        return "unconverged", "error_over_target", f"estimated error {a['estimated_error']:.2e} is above the target {target:g}"
    g = a["growth"]
    if g is not None and g > SINGULAR_GROWTH:
        return ("stress_singular", "stress_singular",
                f"displacement and energy are converged (estimated error {a['estimated_error']:.2e}) but max von Mises grew by a factor {g:.2f} on the last refinement: "
                "the peak stress is a mesh artefact of a singularity (a point or line load, a sharp corner or a line support)")
    if len(levels) < 3:
        return "converged", "ok", f"estimated error {a['estimated_error']:.2e} from two levels with an assumed first-order rate"
    return "converged", "ok", f"estimated error {a['estimated_error']:.2e} is within the target {target:g} and stress is stable under refinement"
