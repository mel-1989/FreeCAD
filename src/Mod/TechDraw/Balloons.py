# SPDX-License-Identifier: LGPL-2.1-or-later

# **************************************************************************
#   Balloons tied to the rows of a parts list (G04 slice 2b): one stock      *
#   TechDraw::DrawViewBalloon per top-level row of the page's parts list,    *
#   numbered with the row's item, its arrow on a visible edge of the row's   *
#   component, the bubbles set around the view with leaders that do not      *
#   cross.                                                                   *
#                                                                            *
#   Public source of the conventions: ISO 6433:2012 (part references:        *
#   leaders do not intersect, as short as practicable).  No text of the      *
#   standard is copied.                                                      *
# **************************************************************************

"""Balloons that follow a parts list.

    import PartsList
    table = PartsList.make(page, assembly)
    balloons = PartsList.balloon(page, view)             # same as Balloons.balloon(page, view, table)

A Python feature, ``BalloonDriver``, sits between the table, the view and the page.  Whenever the parts list or the view is
recomputed it numbers the balloons with the rows' items, removes the balloons of rows that are gone, adds one for each new row
and places them: the arrow tip on a visible edge of the row's component that no other row's component shares, the bubble on a
ring around the view's box.  Slots are chosen so that bubbles stay clear of each other, of the view, the table and the title
block, and leaders do not cross.  A balloon the user has moved keeps its ``X`` and ``Y``; a balloon made by hand (not by this
module) is never touched.
"""

import math
import random

import FreeCAD as App

import PartsList as PL

ROW_PROP = "BalloonRow"  # key of the row (the component's object name)
DRIVER_PROP = "BalloonDriver"  # object name of the driver that owns this balloon
PLACED_X = "PlacedX"  # the X and Y the driver wrote; a different value means the user moved the balloon
PLACED_Y = "PlacedY"

SKIP_EDGE_END = 0.3  # candidates stay this far from the end of a visible edge (mm)
CAND_STEP = 0.5  # spacing of candidate anchor points along visible edges (page mm)
DEFLECTION = 0.01  # page mm: polyline error of discretised edges
PAGE_MARGIN = 3.0  # bubble keeps this far from the frame of the drawing area (mm)
CLEAR = 2.0  # bubble keeps this far from the view box, the table, the title block (mm)
BUBBLE_GAP = 1.5  # between two bubbles (mm)
LEADER_GAP = 0.4  # two leaders closer than this count as crossing (mm)
RING_FIRST = 5.0  # first ring: bubble edge this far from the view box (mm)
RING_STEP = 8.0
MAX_CENTRE_DIST = 38.0  # bubble centres stay within 40 mm of the view box
SLOT_STEP = 5.0
MAX_CANDS = 120
PENALTY = 1000.0
SNAP = 0.1  # anchors of the clear tiers are rounded to this grid (mm), well inside the 0.5 mm tolerance
_RNG_SEED = 20261110


# ------------------------------------------------------------------------------------------------
# rows of the parts list and their components
# ------------------------------------------------------------------------------------------------


def _target(child):
    """-> the object a child of the assembly stands for (link target), or None."""
    t = child
    for _ in range(20):
        if t.TypeId in ("App::Link", "App::LinkElement", "Assembly::AssemblyLink"):
            nxt = getattr(t, "LinkedObject", None)
            if nxt is None:
                return None
            t = nxt
        else:
            break
    return t


def _instances(link):
    count = getattr(link, "ElementCount", 0) or 0
    if count <= 0:
        return 1
    if getattr(link, "ShowElement", False):
        for el in getattr(link, "ElementList", []) or []:
            if getattr(el, "Suppressed", False):
                count -= 1
    return max(count, 0)


def _mirrored(child):
    return float(getattr(child, "Scale", 1.0) or 1.0) < 0.0 if child.TypeId == "App::Link" else False


def top_rows(assembly, only_parts=False):
    """-> [{key, label, paths}] for the assembly's top-level rows, in the order of the stock BOM (same merge rule)."""
    rows, by_key = [], {}
    for child in assembly.OutList:
        if child.TypeId == "Assembly::AssemblyLink" or child.isDerivedFrom("App::Link"):
            if child.isDerivedFrom("App::Link") and _instances(child) <= 0:
                continue
            t = _target(child)
        else:
            t = child
        if t is None:
            continue
        if not (t.isDerivedFrom("Assembly::AssemblyObject") or t.isDerivedFrom("App::Part") or (t.isDerivedFrom("Part::Feature") and not only_parts)):
            continue
        key = t.Name + ("~" if _mirrored(child) else "")
        if key in by_key:
            rows[by_key[key]]["paths"].append(child.Name + ".")
            continue
        by_key[key] = len(rows)
        rows.append({"key": key, "label": t.Label, "paths": [child.Name + "."]})
    return rows


def bom_items(sheet):
    """-> [(index, name)] for the top-level rows of the BOM sheet (no dot in the index)."""
    return [(r[0], r[1]) for r in PL.read_table(sheet)[1:] if r and "." not in r[0]]


def match_rows(rows, items):
    """Pair walked rows with BOM items by order, falling back to labels; -> [(row, item or None)]."""
    if len(rows) == len(items) and all(r["label"] == it[1] for r, it in zip(rows, items)):
        return list(zip(rows, items))
    out, used = [], set()
    for r in rows:
        hit = next((i for i, it in enumerate(items) if i not in used and it[1] == r["label"]), None)
        if hit is None:
            out.append((r, None))
        else:
            used.add(hit)
            out.append((r, items[hit]))
    return out


# ------------------------------------------------------------------------------------------------
# projection into the view
# ------------------------------------------------------------------------------------------------


class Projector:
    """Model point -> page-scaled view coordinates (y up), the frame of ``getVisibleEdges(True)``."""

    def __init__(self, view):
        self.view, self.scale = view, float(view.Scale)
        self.gc = view.getGeometricCenter()
        o = view.projectPoint(App.Vector(0, 0, 0) - self.gc)
        self.o = (o.x, o.y)
        self.m = []
        for ax in (App.Vector(1, 0, 0), App.Vector(0, 1, 0), App.Vector(0, 0, 1)):
            p = view.projectPoint(ax - self.gc)
            self.m.append((p.x - o.x, p.y - o.y))
        self.linear = True
        probe = App.Vector(3.7, -11.2, 5.3)
        a = self._lin(probe)
        b = view.projectPoint(probe - self.gc)
        if abs(a[0] - b.x) > 1e-6 or abs(a[1] - b.y) > 1e-6:
            self.linear = False

    def _lin(self, p):
        return (
            self.o[0] + p.x * self.m[0][0] + p.y * self.m[1][0] + p.z * self.m[2][0],
            self.o[1] + p.x * self.m[0][1] + p.y * self.m[1][1] + p.z * self.m[2][1],
        )

    def __call__(self, p):
        if self.linear:
            x, y = self._lin(p)
        else:
            q = self.view.projectPoint(p - self.gc)
            x, y = q.x, q.y
        return (x * self.scale, y * self.scale)


def _polyline(edge, deflection):
    try:
        pts = edge.discretize(Deflection=deflection)
    except Exception:
        try:
            pts = [v.Point for v in edge.Vertexes]
        except Exception:
            pts = []
    return pts


def view_polylines(view):
    """Visible edges of the view as polylines of (x, y) in page mm relative to the view (y up)."""
    out = []
    for e in view.getVisibleEdges(True):
        pts = _polyline(e, DEFLECTION)
        if len(pts) >= 2:
            out.append([(p.x, p.y) for p in pts])
    return out


def row_polylines(assembly, row, proj):
    """Projected edges of a row's component(s), page mm relative to the view; [] when the shape is not available."""
    import Part

    out = []
    defl = DEFLECTION / max(proj.scale, 1e-9)
    for path in row["paths"]:
        try:
            shape = Part.getShape(assembly, path, transform=True)
        except Exception:
            continue
        if shape is None or shape.isNull():
            continue
        for e in shape.Edges:
            pts = _polyline(e, defl)
            if len(pts) >= 2:
                out.append([proj(p) for p in pts])
    return out


# ------------------------------------------------------------------------------------------------
# small geometry
# ------------------------------------------------------------------------------------------------


def seg_point_dist(ax, ay, bx, by, px, py):
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 <= 0.0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    qx, qy = ax + t * dx, ay + t * dy
    return math.hypot(px - qx, py - qy), qx, qy


def _cross(ax, ay, bx, by, cx, cy):
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def segs_intersect(a, b):
    (x1, y1, x2, y2), (x3, y3, x4, y4) = a, b
    d1, d2 = _cross(x3, y3, x4, y4, x1, y1), _cross(x3, y3, x4, y4, x2, y2)
    d3, d4 = _cross(x1, y1, x2, y2, x3, y3), _cross(x1, y1, x2, y2, x4, y4)
    return d1 * d2 < 0 and d3 * d4 < 0


def seg_seg_dist(a, b):
    if segs_intersect(a, b):
        return 0.0
    return min(
        seg_point_dist(*a, b[0], b[1])[0],
        seg_point_dist(*a, b[2], b[3])[0],
        seg_point_dist(*b, a[0], a[1])[0],
        seg_point_dist(*b, a[2], a[3])[0],
    )


def seg_rect(seg, rect, pad=0.0):
    """True when the segment touches the rectangle (x0, y0, x1, y1) grown by ``pad``."""
    x0, y0, x1, y1 = rect[0] - pad, rect[1] - pad, rect[2] + pad, rect[3] + pad
    ax, ay, bx, by = seg
    if x0 <= ax <= x1 and y0 <= ay <= y1 or x0 <= bx <= x1 and y0 <= by <= y1:
        return True
    edges = ((x0, y0, x1, y0), (x1, y0, x1, y1), (x1, y1, x0, y1), (x0, y1, x0, y0))
    return any(segs_intersect(seg, e) for e in edges)


def rect_dist(rect, px, py):
    dx = max(rect[0] - px, 0.0, px - rect[2])
    dy = max(rect[1] - py, 0.0, py - rect[3])
    return math.hypot(dx, dy)


def bubble_diameter(text):
    """Outer diameter in page mm of a circular stock balloon with ``text`` (measured in run 087; it does not scale with the view)."""
    return max(12.4, 8.8 + 2.35 * len(text))


# ------------------------------------------------------------------------------------------------
# anchors: points on visible edges that belong to one row's component only (ruling 19)
# ------------------------------------------------------------------------------------------------


class SegGrid:
    """Segments of every row's projected edges in a hash grid (cell 2 mm), for distance queries up to ``RADIUS``."""

    CELL, RADIUS = 2.0, 1.2

    def __init__(self, polys_by_row):
        self.cells = {}
        self.segs = []
        for ri, polys in polys_by_row.items():
            for pl in polys:
                for a, b in zip(pl[:-1], pl[1:]):
                    si = len(self.segs)
                    self.segs.append((ri, a[0], a[1], b[0], b[1]))
                    L = math.hypot(b[0] - a[0], b[1] - a[1])
                    n = max(1, int(math.ceil(L / 1.0)))
                    seen = set()
                    for k in range(n + 1):
                        t = k / n
                        c = (int((a[0] + (b[0] - a[0]) * t) // self.CELL), int((a[1] + (b[1] - a[1]) * t) // self.CELL))
                        if c not in seen:
                            seen.add(c)
                            self.cells.setdefault(c, []).append(si)

    def row_dists(self, px, py):
        """-> {row: distance} for rows with an edge within RADIUS of the point."""
        cx, cy = int(px // self.CELL), int(py // self.CELL)
        out = {}
        done = set()
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for si in self.cells.get((cx + dx, cy + dy), ()):
                    if si in done:
                        continue
                    done.add(si)
                    ri, x0, y0, x1, y1 = self.segs[si]
                    d = seg_point_dist(x0, y0, x1, y1, px, py)[0]
                    if d < out.get(ri, 1e9):
                        out[ri] = d
        return out


# tiers: (largest distance to the row's own edges, smallest lead of every other row over it)
TIERS = ((0.03, 1.0), (0.03, 0.3), (0.1, 0.15), (0.3, 0.06))


def anchor_candidates(vis_polys, grid, nrows, own_polys=None):
    """-> per row a list of (x, y, quality) on visible edges, best tier only; quality is the tier index (lower is better)."""
    tiers = [[[] for _ in TIERS] for _ in range(nrows)]
    for pl in vis_polys:
        total = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pl[:-1], pl[1:]))
        if total <= 2 * SKIP_EDGE_END:
            continue
        n = max(1, int(total // CAND_STEP))
        pts = _resample(pl, [(k + 0.5) * total / n for k in range(n)])
        for (x, y) in pts:
            d = grid.row_dists(x, y)
            if not d:
                continue
            own = min(d, key=d.get)
            od = d[own]
            other = min((v for k, v in d.items() if k != own), default=1e9)
            for ti, (own_max, lead) in enumerate(TIERS):
                if od <= own_max and other - od >= lead:
                    tiers[own][ti].append((x, y, ti))
                    break
    out = []
    for row in range(nrows):
        pick = next((t for t in tiers[row] if t), [])
        if not pick and own_polys is not None:
            pick = _hidden_candidates(row, own_polys.get(row, []), grid)
        out.append(pick)
    return out


def _hidden_candidates(row, polys, grid):
    """A component with no visible edge of its own (wholly behind others): points on its own projected edges that no other row shares."""
    best = [[] for _ in TIERS]
    for pl in polys:
        total = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pl[:-1], pl[1:]))
        if total <= 2 * SKIP_EDGE_END:
            continue
        n = max(1, int(total // CAND_STEP))
        for (x, y) in _resample(pl, [(k + 0.5) * total / n for k in range(n)]):
            d = grid.row_dists(x, y)
            od = d.get(row, 1e9)
            other = min((v for k, v in d.items() if k != row), default=1e9)
            for ti, (own_max, lead) in enumerate(TIERS):
                if od <= own_max and other - od >= lead:
                    best[ti].append((x, y, ti + 10))
                    break
    return next((t for t in best if t), [])


def _resample(pl, dists):
    """Points at the given arc lengths along a polyline (sorted ascending)."""
    out, acc, i = [], 0.0, 0
    for d in dists:
        while i < len(pl) - 2 and acc + math.hypot(pl[i + 1][0] - pl[i][0], pl[i + 1][1] - pl[i][1]) < d:
            acc += math.hypot(pl[i + 1][0] - pl[i][0], pl[i + 1][1] - pl[i][1])
            i += 1
        a, b = pl[i], pl[i + 1]
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        t = 0.0 if L <= 0 else min(1.0, max(0.0, (d - acc) / L))
        out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return out


def _snap(x, y, q, vx, vy):
    g = SNAP if q <= 1 else 0.01
    return (round(x / g) * g + vx, round(y / g) * g + vy, q)


def thin(cands, centre, k=MAX_CANDS):
    """Keep at most ``k`` candidates: evenly spaced ones plus the outermost one in each of 24 directions."""
    if len(cands) <= k:
        return cands
    keep = {}
    for j in range(24):
        a = 2 * math.pi * j / 24
        ux, uy = math.cos(a), math.sin(a)
        best = max(cands, key=lambda c: (c[0] - centre[0]) * ux + (c[1] - centre[1]) * uy)
        keep[id(best)] = best
    step = len(cands) / float(k - len(keep))
    i = 0.0
    while int(i) < len(cands):
        c = cands[int(i)]
        keep[id(c)] = c
        i += step
    return list(keep.values())


# ------------------------------------------------------------------------------------------------
# layout
# ------------------------------------------------------------------------------------------------


def ring_slots(box, area, rmax, rects, keep_out):
    """Bubble centres on rounded rectangles around ``box`` (distance rmax+5, then every 8 mm up to 38 mm), spaced about 5 mm."""
    slots = []
    D = rmax + RING_FIRST
    while D <= MAX_CENTRE_DIST + 1e-9:
        w, h = box[2] - box[0], box[3] - box[1]
        perim = 2 * (w + h) + 2 * math.pi * D
        n = max(8, int(perim / SLOT_STEP))
        for k in range(n):
            s = perim * k / n
            slots.append(_on_round_rect(box, D, s))
        D += RING_STEP
    ok = []
    for (x, y) in slots:
        if x - rmax < area[0] + PAGE_MARGIN or x + rmax > area[2] - PAGE_MARGIN:
            continue
        if y - rmax < area[1] + PAGE_MARGIN or y + rmax > area[3] - PAGE_MARGIN:
            continue
        if any(rect_dist(r, x, y) < rmax + CLEAR for r in rects):
            continue
        ok.append((x, y))
    return ok


def _on_round_rect(box, D, s):
    """Point at arc length ``s`` on the rectangle ``box`` grown by ``D`` with round corners, counter clockwise from the middle of the right side."""
    w, h = box[2] - box[0], box[3] - box[1]
    q = 0.5 * math.pi * D
    cy = 0.5 * (box[1] + box[3])
    corners = [(box[2], box[3], 0.0), (box[0], box[3], 0.5 * math.pi), (box[0], box[1], math.pi), (box[2], box[1], 1.5 * math.pi)]
    pieces = [
        ("line", (box[2] + D, cy), (box[2] + D, box[3]), h / 2.0),
        ("arc", corners[0], q),
        ("line", (box[2], box[3] + D), (box[0], box[3] + D), w),
        ("arc", corners[1], q),
        ("line", (box[0] - D, box[3]), (box[0] - D, box[1]), h),
        ("arc", corners[2], q),
        ("line", (box[0], box[1] - D), (box[2], box[1] - D), w),
        ("arc", corners[3], q),
        ("line", (box[2] + D, box[1]), (box[2] + D, cy), h / 2.0),
    ]
    acc = 0.0
    for pc in pieces:
        L = pc[3] if pc[0] == "line" else pc[2]
        if s <= acc + L or pc is pieces[-1]:
            t = 0.0 if L <= 0 else min(1.0, max(0.0, (s - acc) / L))
            if pc[0] == "line":
                (xa, ya), (xb, yb) = pc[1], pc[2]
                return (xa + (xb - xa) * t, ya + (yb - ya) * t)
            ccx, ccy, a0 = pc[1]
            a = a0 + t * 0.5 * math.pi
            return (ccx + D * math.cos(a), ccy + D * math.sin(a))
        acc += L
    return (box[2] + D, cy)


class Layout:
    """Chooses, for every row, a bubble slot and an anchor so that nothing overlaps and no leaders cross."""

    def __init__(self, rows, slots, obstacles, keep_out_segs=()):
        # rows: list of dict(r=radius, cands=[(x,y,q)], fixed=(x,y) or None)
        self.rows, self.slots, self.rects = rows, slots, obstacles
        self.rng = random.Random(_RNG_SEED + len(rows))

    # a state is a list of (pos, tip) per row
    def best_tip(self, i, pos):
        cs = self.rows[i]["cands"]
        if not cs:
            return None
        return min(cs, key=lambda c: (c[0] - pos[0]) ** 2 + (c[1] - pos[1]) ** 2)[:2]

    def cost(self, state):
        n = len(state)
        total = 0.0
        segs = []
        for (pos, tip) in state:
            if tip is None:
                segs.append(None)
                continue
            segs.append((pos[0], pos[1], tip[0], tip[1]))
            total += math.hypot(pos[0] - tip[0], pos[1] - tip[1])
        viol = 0
        for i in range(n):
            pi, ti = state[i]
            ri = self.rows[i]["r"]
            for j in range(i + 1, n):
                pj, tj = state[j]
                rj = self.rows[j]["r"]
                if math.hypot(pi[0] - pj[0], pi[1] - pj[1]) < ri + rj + BUBBLE_GAP:
                    viol += 1
            si = segs[i]
            if si is None:
                continue
            for j in range(n):
                if j == i:
                    continue
                pj, tj = state[j]
                rj = self.rows[j]["r"]
                d = seg_point_dist(*si, pj[0], pj[1])[0]
                if d < rj + 0.7:
                    viol += 1  # leader of i through the bubble of j
                if tj is not None and j > i:
                    sj = segs[j]
                    if sj is not None and seg_seg_dist(si, sj) < LEADER_GAP:
                        viol += 1  # leaders cross
                if tj is not None and seg_point_dist(*si, tj[0], tj[1])[0] < 0.7:
                    viol += 1  # leader of i runs over the arrow of j
            for r in self.rects:
                if seg_rect(si, r, 0.5):
                    viol += 1
        return total + PENALTY * viol, viol

    def initial(self):
        free = list(range(len(self.slots)))
        state = [None] * len(self.rows)
        order = sorted(range(len(self.rows)), key=lambda i: self.rows[i].get("angle", 0.0))
        # fixed rows first
        for i in range(len(self.rows)):
            f = self.rows[i]["fixed"]
            if f:
                state[i] = (f, self.best_tip(i, f))
        pairs = []
        for i in order:
            if state[i] is not None:
                continue
            for s in free:
                pos = self.slots[s]
                tip = self.best_tip(i, pos)
                if tip is None:
                    continue
                pairs.append((math.hypot(pos[0] - tip[0], pos[1] - tip[1]), i, s))
        pairs.sort()
        used_s, used_i = set(), set()
        for d, i, s in pairs:
            if i in used_i or s in used_s:
                continue
            pos = self.slots[s]
            if any(state[j] is not None and math.hypot(pos[0] - state[j][0][0], pos[1] - state[j][0][1]) < self.rows[i]["r"] + self.rows[j]["r"] + BUBBLE_GAP for j in range(len(state)) if j != i):
                continue
            state[i] = (pos, self.best_tip(i, pos))
            used_i.add(i)
            used_s.add(s)
        for i in range(len(state)):
            if state[i] is None:  # no slot or no candidate: leave the bubble near its fixed position or the first free slot
                pos = self.slots[0] if self.slots else (0.0, 0.0)
                state[i] = (pos, self.best_tip(i, pos))
        return state

    def solve(self, max_iter=4000, patience=800):
        state = self.initial()
        cur, viol = self.cost(state)
        best, best_cost, best_viol = list(state), cur, viol
        movable = [i for i in range(len(state)) if not self.rows[i]["fixed"] and self.rows[i]["cands"]]
        stale = 0
        for it in range(max_iter):
            if not movable or (viol == 0 and stale > patience // 4) or stale > patience:
                break
            new = list(state)
            kind = self.rng.random()
            i = self.rng.choice(movable)
            if kind < 0.4 and self.slots:
                s = self.slots[self.rng.randrange(len(self.slots))]
                new[i] = (s, self.best_tip(i, s))
            elif kind < 0.6 and len(movable) > 1:
                j = self.rng.choice(movable)
                if j == i:
                    continue
                pi, pj = state[i][0], state[j][0]
                new[i] = (pj, self.best_tip(i, pj))
                new[j] = (pi, self.best_tip(j, pi))
            elif kind < 0.9:
                c = self.rng.choice(self.rows[i]["cands"])
                new[i] = (state[i][0], c[:2])
            else:  # nudge the bubble to a slot close to the present one
                px, py = state[i][0]
                near = [s for s in self.slots if math.hypot(s[0] - px, s[1] - py) < 20.0]
                if not near:
                    continue
                s = self.rng.choice(near)
                new[i] = (s, self.best_tip(i, s))
            c2, v2 = self.cost(new)
            if c2 <= cur:
                if c2 < cur - 1e-9:
                    stale = 0
                else:
                    stale += 1
                state, cur, viol = new, c2, v2
                if cur < best_cost:
                    best, best_cost, best_viol = list(state), cur, viol
            else:
                stale += 1
        return best, best_viol


# ------------------------------------------------------------------------------------------------
# the driver
# ------------------------------------------------------------------------------------------------

TOL = 1e-5


def _num(q):
    return float(getattr(q, "Value", q))


def _near(a, b, tol=TOL):
    return abs(_num(a) - _num(b)) <= tol


def _set(obj, prop, value):
    """Assign only when the value differs, so an unchanged balloon is not touched."""
    cur = getattr(obj, prop)
    if isinstance(value, float):
        if _near(cur, value, 1e-9):
            return False
    elif cur == value:
        return False
    setattr(obj, prop, value)
    return True


def balloons_of(driver):
    return [o for o in driver.Document.Objects if o.TypeId == "TechDraw::DrawViewBalloon" and getattr(o, DRIVER_PROP, None) == driver.Name]


def user_moved(b):
    return not (_near(b.X, getattr(b, PLACED_X)) and _near(b.Y, getattr(b, PLACED_Y)))


def table_rects(page):
    out = []
    for v in PL.views_on(page):
        fit = PL.fit_of(v)
        if fit is None or float(fit.Width) <= 0:
            continue
        w, h = float(fit.Width), float(fit.Height)
        x, y = v.X.Value, v.Y.Value
        out.append((x - w / 2.0, y - h / 2.0, x + w / 2.0, y + h / 2.0))
    return out


class BalloonDriver:
    """Python feature that keeps one balloon per top-level row of a parts list."""

    def __init__(self, obj):
        obj.Proxy = self
        add = obj.addProperty
        add("App::PropertyLink", "View", "Balloons", "The view the balloons point into")
        add("App::PropertyLink", "Table", "Balloons", "The parts list table (DrawViewSpreadsheet) the balloons follow")
        add("App::PropertyString", "PageName", "Balloons", "Name of the page the balloons are on")
        add("App::PropertyString", "Signature", "Balloons", "What the balloons were last placed against")
        add("App::PropertyInteger", "Violations", "Balloons", "Layout rules the last placement could not meet")
        obj.setEditorMode("Signature", 2)

    def dumps(self):
        return None

    def loads(self, state):
        return None

    def onDocumentRestored(self, obj):
        obj.Proxy = self
        _arm(obj.Document)

    # -- what the placement depends on -------------------------------------------------------

    def _context(self, obj):
        doc = obj.Document
        page = doc.getObject(obj.PageName) if obj.PageName else None
        view, table = obj.View, obj.Table
        if page is None or view is None or table is None or table.Source is None:
            return None
        asm = PL.assembly_of(table)
        if asm is None:
            return None
        return page, view, table, asm

    def stale(self, obj):
        """True when the view's geometry or the rows are not what the balloons were last placed against."""
        ctx = self._context(obj)
        if ctx is None:
            return False
        rows = [r for r, it in match_rows(top_rows(ctx[3], bool(getattr(ctx[2].Source, "onlyParts", False))), bom_items(ctx[2].Source)) if it is not None]
        return self._signature(obj, ctx, rows) != obj.Signature

    def _signature(self, obj, ctx, rows):
        page, view, table, asm = ctx
        import Part

        parts = [
            "%.6f" % float(view.Scale),
            "%.4f,%.4f" % (view.X.Value, view.Y.Value),
            ",".join("%.5f" % c for c in (view.Direction.x, view.Direction.y, view.Direction.z)),
            page.Template.Name if page.Template is not None else "",
            repr([tuple(round(c, 2) for c in r) for r in table_rects(page)]),
            repr([tuple(round(c, 2) for c in a) for a in PL.page_areas(page) if a]),
        ]
        try:
            gc = view.getGeometricCenter()
            parts.append("%.4f,%.4f,%.4f" % (gc.x, gc.y, gc.z))
            bb = [e.BoundBox for e in view.getVisibleEdges(True)]
            parts.append("%d:%.2f,%.2f,%.2f,%.2f" % (len(bb), min([b.XMin for b in bb] or [0]), min([b.YMin for b in bb] or [0]), max([b.XMax for b in bb] or [0]), max([b.YMax for b in bb] or [0])))
        except Exception:
            parts.append("novis")
        for r in rows:
            box = ""
            for path in r["paths"]:
                try:
                    s = Part.getShape(asm, path, transform=True)
                    b = s.BoundBox
                    box += "%.2f,%.2f,%.2f,%.2f,%.2f,%.2f;" % (b.XMin, b.YMin, b.ZMin, b.XMax, b.YMax, b.ZMax)
                except Exception:
                    box += "none;"
            parts.append(r["key"] + "=" + box)
        return "|".join(parts)

    # -- the work ---------------------------------------------------------------------------

    def execute(self, obj):
        ctx = self._context(obj)
        if ctx is None:
            return
        page, view, table, asm = ctx
        doc = obj.Document
        bom = table.Source
        rows = top_rows(asm, bool(getattr(bom, "onlyParts", False)))
        pairs = [(r, it) for r, it in match_rows(rows, bom_items(bom)) if it is not None]
        keep_rows = [r for r, _ in pairs]
        mine = {getattr(b, ROW_PROP): b for b in balloons_of(obj)}
        want = {r["key"] for r in keep_rows}
        sig = self._signature(obj, ctx, keep_rows)
        changed, new = [], []

        for key, b in list(mine.items()):
            if key not in want:  # the row is gone: the observer removes the balloon from the document when the recompute ends
                _SETTLE.doomed.setdefault(doc.Uid, set()).add(b.Name)
                del mine[key]
        fresh = False
        for r, it in pairs:
            if r["key"] not in mine:
                mine[r["key"]] = self._new_balloon(obj, page, view, r["key"])
                new.append(mine[r["key"]])
                fresh = True
        if sig != obj.Signature or fresh:
            placed = self._place(obj, ctx, pairs, mine)
            obj.Signature = sig
            changed += placed
        for r, it in pairs:
            b = mine[r["key"]]
            if _set(b, "Text", it[0]):
                changed.append(b)
        # A balloon added or changed in the middle of a recompute may be touched again by the view's second pass, and a
        # document recomputes each object at most twice; the observer below settles whatever is left once the recompute ends.
        _arm(doc)

    def _new_balloon(self, drv, page, view, key):
        doc = drv.Document
        b = doc.addObject("TechDraw::DrawViewBalloon", "Balloon")
        b.SourceView = view
        page.addView(b)
        b.addProperty("App::PropertyString", ROW_PROP, "Balloons", "Component this balloon points at")
        b.addProperty("App::PropertyString", DRIVER_PROP, "Balloons", "Name of the driver that keeps this balloon (a name, not a link, so it adds no dependency)")
        b.addProperty("App::PropertyFloat", PLACED_X, "Balloons", "X written by the driver; a different X means the user moved it")
        b.addProperty("App::PropertyFloat", PLACED_Y, "Balloons", "Y written by the driver")
        setattr(b, ROW_PROP, key)
        setattr(b, DRIVER_PROP, drv.Name)
        for p in (ROW_PROP, DRIVER_PROP, PLACED_X, PLACED_Y):
            b.setEditorMode(p, 1)
        b.BubbleShape = "Circular"
        b.KinkLength = 0.0
        return b

    def _place(self, drv, ctx, pairs, mine):
        page, view, table, asm = ctx
        S = float(view.Scale)
        vx, vy = view.X.Value, view.Y.Value
        proj = Projector(view)
        vis = view_polylines(view)
        if not vis:
            return []
        n = len(pairs)
        polys = {i: row_polylines(asm, r, proj) for i, (r, _) in enumerate(pairs)}
        grid = SegGrid(polys)
        cands = anchor_candidates(vis, grid, n, polys)
        allx = [p[0] for pl in vis for p in pl]
        ally = [p[1] for pl in vis for p in pl]
        # the GUI's hidden line removal and the headless one differ by a few hundredths of a millimetre: snap the inputs so both
        # place the same way
        box = (math.floor(min(allx)) + vx, math.floor(min(ally)) + vy, math.ceil(max(allx)) + vx, math.ceil(max(ally)) + vy)
        centre = (0.5 * (box[0] + box[2]), 0.5 * (box[1] + box[3]))
        area, title = PL.page_areas(page)
        rects = table_rects(page) + ([tuple(title)] if title else [])
        lrows = []
        for i, (r, it) in enumerate(pairs):
            b = mine[r["key"]]
            cs = sorted({_snap(x, y, q, vx, vy) for (x, y, q) in cands[i]})
            cs = thin(cs, centre)
            fixed = None
            if user_moved(b):
                fixed = (vx + _num(b.X) * S, vy + _num(b.Y) * S)
            ang = 0.0
            if cs:
                mx, my = sum(c[0] for c in cs) / len(cs), sum(c[1] for c in cs) / len(cs)
                ang = math.atan2(my - centre[1], mx - centre[0])
            lrows.append({"r": bubble_diameter(it[0]) / 2.0, "cands": cs, "fixed": fixed, "angle": ang})
        rmax = max([lr["r"] for lr in lrows] or [7.0])
        slots = ring_slots(box, area, rmax, rects + [box], ())
        lay = Layout(lrows, slots, rects)
        state, viol = lay.solve()
        drv.Violations = int(viol)
        changed = []
        for i, (r, it) in enumerate(pairs):
            b = mine[r["key"]]
            pos, tip = state[i]
            if tip is None:
                tip = centre
            ch = False
            ch |= _set(b, "OriginX", float((tip[0] - vx) / S))
            ch |= _set(b, "OriginY", float((tip[1] - vy) / S))
            if not lrows[i]["fixed"]:
                px, py = float((pos[0] - vx) / S), float((pos[1] - vy) / S)
                ch |= _set(b, "X", px)
                ch |= _set(b, "Y", py)
                setattr(b, PLACED_X, px)
                setattr(b, PLACED_Y, py)
            if ch:
                changed.append(b)
        return changed


class _Settle:
    """Document observer: at the end of every recompute, bring the balloons of a driver up to date (what the driver added)."""

    def __init__(self):
        self.docs = set()  # Uid of the documents that hold a driver
        self.pending = set()  # names of documents with a re-placement scheduled
        self.doomed = {}  # Uid -> names of balloons whose row is gone

    def slotRecomputedObject(self, obj):
        # In the GUI a view finds its edges in a worker thread: its balloons are recomputed when that ends
        # (DrawViewPart::postHlrTasks), which is the moment the geometry the driver needs is new.  Place again then.
        if not App.GuiUp or obj.TypeId != "TechDraw::DrawViewBalloon" or not getattr(obj, DRIVER_PROP, ""):
            return
        doc = obj.Document
        if doc.Uid in self.docs and doc.Name not in self.pending:
            self.pending.add(doc.Name)
            from PySide import QtCore

            QtCore.QTimer.singleShot(30, lambda name=doc.Name: self._replace(name))

    def _replace(self, name):
        self.pending.discard(name)
        doc = App.getDocument(name) if name in App.listDocuments() else None
        if doc is None:
            return
        stale = False
        for o in doc.Objects:
            p = getattr(o, "Proxy", None)
            if o.TypeId == "App::FeaturePython" and isinstance(p, BalloonDriver) and p.stale(o):
                o.touch()
                stale = True
        if stale:
            doc.recompute()

    def slotRecomputedDocument(self, doc):
        if doc.Uid not in self.docs:
            return
        names = self.doomed.pop(doc.Uid, set())
        if names:
            # removal inside a recompute is deferred and touches the page and view after the pass; do it here and give back the touch
            keep = {}
            for n in names:
                o = doc.getObject(n)
                if o is None:
                    continue
                for dep in [o.SourceView] + [p for p in o.InList if p.TypeId == "TechDraw::DrawPage"]:
                    if dep is not None and dep.Name not in keep:
                        keep[dep.Name] = "Touched" in dep.State
                doc.removeObject(n)
            for n, was in keep.items():
                dep = doc.getObject(n)
                if dep is not None and not was and "Touched" in dep.State:
                    dep.purgeTouched()
        for o in doc.Objects:
            if o.TypeId == "TechDraw::DrawViewBalloon" and getattr(o, DRIVER_PROP, "") and "Touched" in o.State:
                try:
                    o.recompute()
                except Exception:
                    pass
                if "Touched" in o.State:
                    try:
                        o.purgeTouched()  # DrawViewBalloon::execute only asks for a repaint, which the property changes already did
                    except Exception:
                        pass


_SETTLE = _Settle()
App.addDocumentObserver(_SETTLE)


def _arm(doc):
    _SETTLE.docs.add(doc.Uid)


def balloon_driver_for(view, table):
    for o in view.Document.Objects:
        if o.TypeId == "App::FeaturePython" and isinstance(getattr(o, "Proxy", None), BalloonDriver) and o.View == view and o.Table == table:
            return o
    return None


def _wait_for_geometry(view, timeout=20.0):
    """In the GUI a view's hidden line removal runs in a worker thread and its edges arrive with the event loop: let it finish."""
    if not App.GuiUp:
        return
    import time

    from PySide import QtWidgets

    def sig():
        try:
            return [(round(e.BoundBox.XMax, 3), round(e.BoundBox.YMax, 3)) for e in view.getVisibleEdges(True)]
        except Exception:
            return []

    t0, last, last_change = time.time(), None, time.time()
    while time.time() - t0 < timeout:
        QtWidgets.QApplication.processEvents()
        now = sig()
        if now != last:
            last, last_change = now, time.time()
        elif now and time.time() - last_change > 0.25:
            return
        time.sleep(0.01)


def balloon(page, view, partslist=None):
    """Put one balloon on ``view`` for each top-level row of the page's parts list and return them.

    ``partslist`` is the table returned by ``PartsList.make``; None means the only parts list on the page.  One undo transaction.
    Afterwards a plain ``doc.recompute()`` keeps the balloons right as the assembly changes.
    """
    doc = page.Document
    if partslist is None:
        tables = PL.views_on(page)
        if len(tables) != 1:
            raise ValueError("balloon: the page has %d parts lists, pass the one to follow" % len(tables))
        partslist = tables[0]
    _wait_for_geometry(view)
    doc.openTransaction("Balloons")
    try:
        drv = balloon_driver_for(view, partslist)
        if drv is None:
            drv = doc.addObject("App::FeaturePython", "BalloonDriver")
            BalloonDriver(drv)
            drv.Label = "Balloon driver"
            drv.View = view
            drv.Table = partslist
            drv.PageName = page.Name
        drv.Signature = ""
        drv.touch()
        doc.recompute()
        if any("Touched" in o.State for o in balloons_of(drv)):
            doc.recompute()
    except Exception:
        doc.abortTransaction()
        raise
    doc.commitTransaction()
    return balloons_of(drv)
