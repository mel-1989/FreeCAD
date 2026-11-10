# SPDX-License-Identifier: LGPL-2.1-or-later

# G04 slice 1 (check 13, run 086): the placer on a crowded page, headless.
# Run with: FreeCADCmd -t TestTechDrawLayout   (also part of TestTechDrawApp)
#
# The small page of DrawingLayoutTest has room for everything, so most of the placer's mutants (cost weights, reach, candidate sets,
# annealing) cannot show there.  This page is a plate with a grid of drilled holes and counterbores: 35 annotations compete for the
# margins, each hole carries a size and a position frame, and four of the holes have concentric counterbores.  The tests measure the
# result independently of the placer (page boxes, leader segments, visible-edge segments) and call the candidate generators directly.

import math
import unittest

import numpy as np

import FreeCAD as App

import DrawingLayout as DL
import PmiAuthoring as PA
import PmiDrawing as PD

from TDTest.DrawingLayoutTest import group_of, leader_ends, overlap
from TDTest.PmiDrawingTest import cyl_face, make_page, planar_face

X, Y, Z = App.Vector(1, 0, 0), App.Vector(0, 1, 0), App.Vector(0, 0, 1)


def build_crowded(doc):
    plate = doc.addObject("Part::Box", "Plate")
    plate.Length, plate.Width, plate.Height = 200, 110, 12
    solids = [plate]
    for row in range(3):
        for col in range(4):
            x, y = 30 + 46 * col, 20 + 35 * row
            r = (3, 4, 5, 3.5, 4.5, 6)[(row + col) % 6]
            c = doc.addObject("Part::Cylinder", "Cyl%d_%d" % (row, col))
            c.Radius, c.Height = r, 20
            c.Placement.Base = App.Vector(x, y, -4)
            solids.append(c)
            if (row + col) % 5 == 0:  # a counterbore on the same axis
                c2 = doc.addObject("Part::Cylinder", "Bore%d_%d" % (row, col))
                c2.Radius, c2.Height = r + 3, 8
                c2.Placement.Base = App.Vector(x, y, -4)
                solids.append(c2)
    doc.recompute()

    def face(normal, coord):
        return (plate, [planar_face(plate, normal, coord)])

    PA.addDatum("A", face(-Z, 0))
    PA.addDatum("B", face(-X, 0))
    PA.addSizeDimension([face(-X, 0), face(X, 200)], plus_minus=(0.1, -0.1))
    PA.addSizeDimension([face(-Y, 0), face(Y, 110)])
    PA.addSizeDimension([face(-Z, 0), face(Z, 12)], plus_minus=(0.05, -0.05))
    for i, c in enumerate(solids[1:]):
        PA.addSizeDimension([(c, [cyl_face(c)])], plus_minus=(0.05, -0.05) if i % 2 == 0 else None)
        PA.addTolerance("Position", 0.2, (c, [cyl_face(c)]), datums=["A", "B"] if i % 3 == 0 else ["A"])
    PA.addTolerance("Flatness", 0.05, face(Z, 12))
    PA.addTolerance("Perpendicularity", 0.1, face(X, 200), datums=["A"])
    return solids


def seg_hits_box(p, q, b, pad=0.0):
    return DL._seg_hits_box(p, q, (b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad))


class CrowdedLayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = App.newDocument("DrawingLayoutCrowded")
        cls.solids = build_crowded(cls.doc)
        cls.page = make_page(cls.doc)
        PD.project(cls.page, cls.solids)
        cls.rep = DL.layout(cls.page)
        cls.lay, cls.group = DL._prepare(cls.page)
        cls.s = float(group_of(cls.page).Scale)
        cls.P = cls.lay.plan(cls.s, final=True, gap=DL.VIEW_GAP)  # a plan on the page as it stands, for its grid and its candidate generators
        cls.boxes = DL.annotationBoxes(cls.page)
        cls.ends = leader_ends(cls.page)

    @classmethod
    def tearDownClass(cls):
        for n in list(App.listDocuments()):
            App.closeDocument(n)

    def concentric(self, a, b):
        ga, gb = a.geo, b.geo
        if a.kind != "dim" or b.kind != "dim" or ga["type"] not in ("Diameter", "Radius") or gb["type"] not in ("Diameter", "Radius"):
            return False
        return a.view is b.view and math.hypot(ga["c"][0] - gb["c"][0], ga["c"][1] - gb["c"][1]) * self.s <= 6.0

    def leaders(self):
        """[(name of the annotation, (start x, y), (end x, y))] in page mm from the drawn way points."""
        out = []
        for o, end, anchor in self.ends:
            g = group_of(self.page)
            v = o.LeaderParent
            s = float(g.Scale)
            px, py = g.X.Value + v.X.Value, g.Y.Value + v.Y.Value
            out.append((o, (px + o.X.Value * s, py + o.Y.Value * s), end))
        return out

    def owner_box(self, leader):
        """The box of the frame or datum a leader belongs to (RichAnno whose LeaderParent link is the leader)."""
        for a in self.lay.anns:
            if a.leader is not None and a.leader.Name == leader.Name:
                return self.boxes[a.obj.Name]
        return None

    # ---- the page as a whole
    def test_the_crowded_page_is_big_enough_to_matter(self):
        self.assertGreaterEqual(len(self.lay.anns), 30)
        self.assertGreaterEqual(len([a for a in self.lay.anns if a.kind == "dim"]), 15)
        self.assertGreaterEqual(len(self.ends), 15)
        self.assertEqual(self.rep["failed"], [])

    def test_few_conflicts_remain_and_the_reach_bar_holds(self):
        r = self.rep["resid"]
        # measured independently of the placer: boxes against boxes, leaders against foreign boxes and against each other
        names = sorted(self.boxes)
        anns = {a.obj.Name: a for a in self.lay.anns}
        ov = [(a, b) for i, a in enumerate(names) for b in names[i + 1 :] if overlap(self.boxes[a], self.boxes[b]) and not self.concentric(anns[a], anns[b])]
        self.assertLessEqual(len(ov), 4, ov)
        ls = self.leaders()
        lb = 0
        for o, p, q in ls:
            own = self.owner_box(o)
            for n, b in self.boxes.items():
                if b != own and seg_hits_box(p, q, b) and not self.on_circle(anns[n], q):
                    lb += 1
        self.assertLessEqual(lb, 6, "leaders over foreign boxes")
        cr = 0
        for i in range(len(ls)):
            for j in range(i + 1, len(ls)):
                if DL._seg_cross(ls[i][1], ls[i][2], ls[j][1], ls[j][2]):
                    cr += 1
        self.assertLessEqual(cr, 4, "leader crossings")
        self.assertLessEqual(r["overlaps"] + r["leader_over_box"] + r["crossings"], 14)
        self.assertLessEqual(self.rep["max_reach"], 80.0)
        self.assertLessEqual(self.rep["median_reach"], 25.0)

    def on_circle(self, a, q):
        """A leader that ends on a circle a diameter or radius dimension measures runs inside that dimension's hull: not a conflict."""
        if a.kind != "dim" or a.geo["type"] not in ("Diameter", "Radius"):
            return False
        px, py = self.P.pos[a.view.view.Name]
        sc = self.s
        return math.hypot(q[0] - (px + a.geo["c"][0] * sc), q[1] - (py + a.geo["c"][1] * sc)) <= a.geo["r"] * sc + 3.5

    def text_boxes(self):
        """{dimension name: text box} where the layout left them."""
        P = self.P
        out = {}
        for a in self.lay.anns:
            if a.kind != "dim":
                continue
            px, py = P.pos[a.view.view.Name]
            hull, tb, _ = self.lay.dimBox(P, a, np.array([px + a.obj.X.Value]), np.array([py + a.obj.Y.Value]), self.s, (px, py))
            out[a.obj.Name] = tuple(float(t[0]) for t in tb)
        return out

    def test_concentric_diameter_texts_do_not_overlap(self):
        # run 085 stopped charging concentric diameter and radius pairs altogether: 13 of its 14 overlapping pairs overlapped in their text
        tb = self.text_boxes()
        anns = {a.obj.Name: a for a in self.lay.anns}
        pairs = [(a, b) for a in sorted(tb) for b in sorted(tb) if a < b and self.concentric(anns[a], anns[b])]
        self.assertGreaterEqual(len(pairs), 2)
        bad = [(a, b) for a, b in pairs if overlap(tb[a], tb[b])]
        self.assertEqual(bad, [])

    def test_the_energy_charges_the_text_of_concentric_pairs_and_not_their_hulls(self):
        F = DL._Field([], [], 2)
        F.FORCE_OV[0, 1] = F.FORCE_OV[1, 0] = True
        hull = np.array([[0.0, 0.0, 20.0, 20.0], [5.0, 5.0, 25.0, 25.0]])
        far = np.array([[DL.FAR, DL.FAR]] * 2)
        F.put(0, hull[0], far[0], far[0], (0.0, 8.0, 20.0, 12.0))
        F.put(1, hull[1], far[1], far[1], (22.0, 8.0, 28.0, 12.0))  # texts apart, hulls overlapping: free
        self.assertEqual(F.state(np.array([0, 1]))[1], 0)
        F.put(1, hull[1], far[1], far[1], (10.0, 9.0, 18.0, 13.0))  # texts overlap: charged
        self.assertEqual(F.state(np.array([0, 1]))[1], 2)

    # ---- sizes and offsets that the oracle calibrated
    def test_box_sizes_follow_the_calibration(self):
        widths = sorted(a.size[0] for a in self.lay.anns if a.kind == "frame")
        self.assertGreater(widths[-1], widths[0] + 6.0, "the width estimate does not follow the text")
        self.assertGreater(widths[0], 12.0)
        self.assertLess(widths[-1], 60.0)
        for a in self.lay.anns:
            if a.kind == "datum":
                self.assertAlmostEqual(a.size[1], 8.6, delta=0.5)
                self.assertAlmostEqual(a.size[0], 6.4, delta=0.5)
            if a.kind == "frame":
                self.assertAlmostEqual(a.size[1], 9.4, delta=0.5)
        tall = [a for a in self.lay.anns if a.kind == "dim" and a.geo["type"] in ("Diameter", "Radius") and a.obj.EqualTolerance and a.obj.OverTolerance.Value != 0]
        self.assertTrue(tall)
        for a in tall:
            self.assertGreaterEqual(a.size[1], 11.5)

    def test_frames_are_drawn_where_the_layout_reckons(self):
        # measured in the GUI in run 084 (TechDraw draws a RichAnno's frame this far from the leader's origin)
        self.assertEqual(DL.ANNO_OFFSET["frame"], (1.38, -1.38))
        self.assertEqual(DL.ANNO_OFFSET["datum"], (-0.2, 0.0))

    def test_view_positions_are_on_the_half_millimetre_grid(self):
        pos, ok = self.lay.viewPositions(self.s)
        self.assertTrue(ok)
        for n, (x, y) in pos.items():
            if any(v.view.Name == n for v in self.lay.managed):
                self.assertAlmostEqual(x * 2, round(x * 2), places=9, msg=n)
                self.assertAlmostEqual(y * 2, round(y * 2), places=9, msg=n)

    def test_inputs_are_quantised_to_a_hundredth_of_a_millimetre(self):
        self.assertEqual(DL._q(1.234), 1.23)
        self.assertEqual(DL._q(1.236), 1.24)
        for v in self.lay.views:
            q0, q1 = v.segments(self.lay.defl)
            self.assertTrue(len(q0))
            for arr in (q0, q1):
                page = arr * v.s0
                self.assertLess(float(np.abs(page * 100 - np.round(page * 100)).max()), 1e-6)
        for a in self.lay.anns:
            if a.kind == "dim":
                coords = [c for p in a.geo.get("pts", [a.geo.get("c")]) for c in p]
                for c in coords:
                    self.assertAlmostEqual(c * self.lay.views[0].s0 * 2, round(c * self.lay.views[0].s0 * 2), places=6)
            if a.kind != "dim":
                self.assertAlmostEqual(a.anchor[0] * 100, round(a.anchor[0] * 100), places=6)
            self.assertAlmostEqual(a.size[0] * 100, round(a.size[0] * 100), places=6)
        for v in self.lay.views:
            self.assertAlmostEqual(v.half[0] * 100, round(v.half[0] * 100), places=6)

    def test_a_tiny_change_of_the_inputs_moves_nothing(self):
        # the annealing is chaotic in its inputs: a 0.002 mm difference between a headless and a GUI view (run 085, ctc_03) moved things by metres
        base = {a.obj.Name: self.P.placed[a.obj.Name]["box"] for a in self.lay.anns if a.obj.Name in self.P.placed}
        lay2, _ = DL._prepare(self.page)
        orig = DL._V.segments

        def jitter(v, deflection):
            q0, q1 = orig(v, deflection)
            if getattr(v, "_jittered", False):
                return q0, q1
            v._jittered = True
            v.seg = (q0 + 0.002 / v.s0, q1 + 0.002 / v.s0)
            return v.seg

        DL._V.segments = jitter
        try:
            lay2.defl = self.lay.defl
            P2 = lay2.plan(self.s, final=True, gap=DL.VIEW_GAP)
        finally:
            DL._V.segments = orig
        # quantising happens when the segments are read from the view; the jitter above is added after, so it tests the cost's robustness
        # to a sub-quantum shift of the edge ink instead: positions stay equal when the shift does not change which cells are marked
        same = sum(1 for n, b in base.items() if n in P2.placed and max(abs(x - y) for x, y in zip(b, P2.placed[n]["box"])) < 0.01)
        self.assertGreaterEqual(same, int(0.9 * len(base)), "%d of %d unchanged" % (same, len(base)))

    # ---- the candidate generators, called directly
    def frame_cands(self):
        out = []
        for a in self.lay.anns:
            if a.kind != "dim":
                c = self.lay.frameCands(self.P, a, self.P.grid)
                if c is not None:
                    out.append(c)
        return out

    def dim_cands(self, kind=None):
        out = []
        for a in self.lay.anns:
            if a.kind == "dim" and (kind is None or a.geo["type"] in kind):
                c = self.lay.dimCands(self.P, a, self.P.grid)
                if c is not None:
                    out.append(c)
        return out

    def test_frame_candidates_stay_within_reach_and_the_sheet_and_carry_the_soft_costs(self):
        cs = self.frame_cands()
        self.assertGreaterEqual(len(cs), 15)
        ax0, ay0, ax1, ay1 = self.P.inner
        saw_view = saw_edge = 0
        for c in cs:
            self.assertLessEqual(float(c.extra["d"].max()), DL.REACH + 1e-9)
            self.assertGreaterEqual(float(c.extra["d"].min()), 1.5 - 1e-9)
            self.assertTrue((c.box[:, 0] >= ax0 - 1e-9).all() and (c.box[:, 2] <= ax1 + 1e-9).all() and (c.box[:, 1] >= ay0 - 1e-9).all() and (c.box[:, 3] <= ay1 + 1e-9).all())
            cV = self.P.grid.hit("V", c.box[:, 0], c.box[:, 1], c.box[:, 2], c.box[:, 3])
            cE = self.P.grid.hit("E", c.box[:, 0] - DL.EDGE_CLEAR, c.box[:, 1] - DL.EDGE_CLEAR, c.box[:, 2] + DL.EDGE_CLEAR, c.box[:, 3] + DL.EDGE_CLEAR)
            floor = c.extra["d"] + 6.0 * cV + DL.W_EDGE * cE
            self.assertTrue((c.static >= floor - 1e-9).all())
            saw_view += int(cV.any())
            saw_edge += int(cE.any())
        self.assertGreater(saw_view, 0)

    def test_frame_leaders_start_on_the_side_of_the_box_that_faces_the_anchor(self):
        cs = self.frame_cands()
        left = right = 0
        for c in cs:
            A = c.A
            x0, y0, x1, y1 = c.box[:, 0], c.box[:, 1], c.box[:, 2], c.box[:, 3]
            Ox, Oy = c.O[:, 0], c.O[:, 1]
            inside = (Ox >= x0 - 1e-6) & (Ox <= x1 + 1e-6) & (Oy >= y0 - 1e-6) & (Oy <= y1 + 1e-6)
            self.assertTrue(inside.all(), "a leader starts off its box")
            at = np.hypot(Ox - A[0], Oy - A[1])
            centre = np.hypot((x0 + x1) / 2 - A[0], (y0 + y1) / 2 - A[1])
            self.assertTrue((at <= centre + 1e-6).all(), "a leader starts on the far side of its box")
            left += int((A[0] < x0).any())
            right += int((A[0] > x1).any())
        self.assertGreater(left, 0)
        self.assertGreater(right, 0)

    def test_drawn_leaders_start_on_their_own_box(self):
        n = 0
        for o, p, q in self.leaders():
            b = self.owner_box(o)
            if b is None:
                continue
            n += 1
            self.assertTrue(b[0] - 0.6 <= p[0] <= b[2] + 0.6 and b[1] - 0.6 <= p[1] <= b[3] + 0.6, (o.Name, p, b))
        self.assertGreaterEqual(n, 15)

    def test_dimension_candidates_stay_on_the_sheet_within_reach_and_offer_every_kind_of_place(self):
        ax0, ay0, ax1, ay1 = self.P.inner
        sc = self.s
        distX = diam = 0
        for a in self.lay.anns:
            if a.kind != "dim":
                continue
            c = self.lay.dimCands(self.P, a, self.P.grid)
            if c is None:
                continue
            self.assertLessEqual(float(c.extra["d"].max()), DL.REACH + 1e-9)
            self.assertTrue((c.box[:, 0] >= ax0 - 1e-6).all() and (c.box[:, 2] <= ax1 + 1e-6).all() and (c.box[:, 1] >= ay0 - 1e-6).all() and (c.box[:, 3] <= ay1 + 1e-6).all(), a.obj.Name)
            t = a.geo["type"]
            px, py = self.P.pos[a.view.view.Name]
            if t == "DistanceX":
                xs = {round(float(x)) for x in c.extra["X"]}
                self.assertGreaterEqual(len(xs), 5, "slides along the dimension line only")
                distX += 1
            if t in ("Diameter", "Radius"):
                cx, cy = px + a.geo["c"][0] * sc, py + a.geo["c"][1] * sc
                level = (np.abs(c.extra["Y"] - cy) < 1e-6) | (np.abs(c.extra["X"] - cx) < 1e-6)
                self.assertTrue(level.any(), "no level or plumb position for " + a.obj.Name)
                diam += 1
        self.assertGreater(distX, 0)
        self.assertGreater(diam, 5)

    def test_dimension_text_stays_off_visible_edges(self):
        P = self.P
        segs = []
        for v in self.lay.views:
            q0, q1 = v.segments(self.lay.defl)
            px, py = P.pos[v.view.Name]
            sc = self.s if v in self.lay.managed else v.s0
            for a, b in zip(q0 * sc, q1 * sc):
                segs.append(((a[0] + px, a[1] + py), (b[0] + px, b[1] + py)))
        tb = self.text_boxes()
        over = [n for n, b in tb.items() if any(seg_hits_box(p, q, b) for p, q in segs)]
        self.assertLessEqual(len(over), max(2, len(tb) // 6), over)

    def test_frames_stay_off_visible_edges(self):
        P = self.P
        segs = []
        for v in self.lay.views:
            q0, q1 = v.segments(self.lay.defl)
            px, py = P.pos[v.view.Name]
            sc = self.s if v in self.lay.managed else v.s0
            for a, b in zip(q0 * sc, q1 * sc):
                segs.append(((a[0] + px, a[1] + py), (b[0] + px, b[1] + py)))
        fr = [(a.obj.Name, self.boxes[a.obj.Name]) for a in self.lay.anns if a.kind != "dim"]
        over = [n for n, b in fr if any(seg_hits_box(p, q, b) for p, q in segs)]
        self.assertLessEqual(len(over), max(3, len(fr) // 5), over)

    def test_the_assignment_is_better_than_its_greedy_seed_and_never_worse_than_the_exchanges_it_makes(self):
        # the seed alone (no sweeps, no exchanges) leaves clearly more conflicts than the full assignment on this page
        seed = self.conflicts_with(sweeps=0, swaps=0)
        full = self.conflicts_with(sweeps=DL.SWEEPS, swaps=DL.SWAPS)
        self.assertLess(full, seed)

    def conflicts_with(self, sweeps, swaps):
        old = (DL.SWEEPS, DL.SWAPS)
        DL.SWEEPS, DL.SWAPS = sweeps, swaps
        try:
            P = self.lay.plan(self.s, final=True, gap=DL.VIEW_GAP)
        finally:
            DL.SWEEPS, DL.SWAPS = old
        return P.viol

    def test_exchanges_never_make_a_page_worse(self):
        no_swaps = self.conflicts_with(sweeps=DL.SWEEPS, swaps=0)
        with_swaps = self.conflicts_with(sweeps=DL.SWEEPS, swaps=DL.SWAPS)
        self.assertLessEqual(with_swaps, no_swaps)

    def test_the_result_is_the_same_on_every_run(self):
        a = self.lay.plan(self.s, final=True, gap=DL.VIEW_GAP)
        b = self.lay.plan(self.s, final=True, gap=DL.VIEW_GAP)
        self.assertEqual({n: r["box"] for n, r in a.placed.items()}, {n: r["box"] for n, r in b.placed.items()})
        self.assertEqual(a.viol, b.viol)


class ViewGeometryCacheTest(unittest.TestCase):
    def tearDown(self):
        for n in list(App.listDocuments()):
            App.closeDocument(n)

    def test_the_cached_box_and_centre_follow_a_change_of_the_model(self):
        # the box and centre of a view cost 0.17 s each to measure and are kept across layouts: a model edit must not leave them stale
        doc = App.newDocument("ViewGeometryCache")
        box = doc.addObject("Part::Box", "Box")
        box.Length, box.Width, box.Height = 80, 50, 20
        doc.recompute()
        PA.addSizeDimension([(box, [planar_face(box, -X, 0)]), (box, [planar_face(box, X, 80)])])
        page = make_page(doc)
        PD.project(page, box)
        lay, _ = DL._prepare(page)
        front = [v for v in lay.views if v.label == "Front"][0]
        self.assertAlmostEqual(front.half[0], 40.2, delta=0.05)
        c0 = front.vw.center()
        box.Length = 120
        doc.recompute()
        PD.settle(page)
        lay, _ = DL._prepare(page)
        front = [v for v in lay.views if v.label == "Front"][0]
        self.assertAlmostEqual(front.half[0], 60.2, delta=0.05)
        c1 = front.vw.center()
        self.assertGreater((c1 - c0).Length, 10.0)  # the box grew along x by 40: its centre moved by 20 in model space
