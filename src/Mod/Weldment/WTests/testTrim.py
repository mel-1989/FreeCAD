# SPDX-License-Identifier: LGPL-2.1-or-later
"""Acceptance check 4 (slice 5a): corner treatment and trim.

Independent oracle: a prism of section area A cut by a plane has volume A x (axial length measured on
the section's centroid line); the test intersects the centroid line with the plane by hand.
"""

import math
import unittest

import FreeCAD
import Part

import Profiles
import WeldMember
import WeldTrim
from WTests import frames, oracle

V = FreeCAD.Vector
LX = 900.0
worst = {"common": 0.0, "bisect": 0.0, "volume": 0.0}


def end_face_normal(shape, u, which):
    """Normal of the largest planar face at the given end whose normal is not square to u."""
    best = None
    s_all = [(v.Point).dot(u) for v in shape.Vertexes]
    mid = (min(s_all) + max(s_all)) / 2.0
    for f in shape.Faces:
        if f.Surface.TypeId != "Part::GeomPlane":
            continue
        n = f.normalAt(*[(a + b) / 2.0 for a, b in zip(f.ParameterRange[0::2], f.ParameterRange[1::2])])
        if abs(n.dot(u)) < 1e-6:
            continue
        at_end = f.CenterOfMass.dot(u) > mid
        if at_end == (which == "End") and (best is None or f.Area > best[0]):
            best = (f.Area, n)
    return best[1]


# name, profile kind, away-from-joint direction of A, of B (unit by construction), anchor, rotation
def _dir(phi_deg, psi_deg=0.0):
    p, q = math.radians(phi_deg), math.radians(psi_deg)
    return V(-math.cos(p), math.sin(p) * math.cos(q), math.sin(p) * math.sin(q))


MITRES = [
    ("RHS 90", "RHS", 90, 0, "Centre", 0),
    ("RHS 45", "RHS", 45, 0, "Top Left", 0),
    ("RHS 135 anchored", "RHS", 135, 0, "Bottom", 20),
    ("RHS 60 3D", "RHS", 60, 35, "Centre", 0),
    ("I 90", "I", 90, 0, "Centre", 0),
    ("I 75 anchored", "I", 75, 0, "Top Right", 0),
    ("I 120 3D", "I", 120, 50, "Left", 15),
    ("L 90", "L", 90, 0, "Centre", 0),
    ("L 110 anchored", "L", 110, 0, "Bottom Left", 0),
    ("L 45 3D", "L", 45, 70, "Centre", 30),
    ("CHS 100", "CHS", 100, 0, "Centre", 0),
]


class TestMitre(unittest.TestCase):
    def tearDown(self):
        for n in list(FreeCAD.listDocuments()):
            FreeCAD.closeDocument(n)

    def test_mitre_joints(self):
        self.assertGreaterEqual(len(MITRES), 8)
        for name, kind, phi, psi, anchor, rot in MITRES:
            with self.subTest(name):
                self._mitre(name, kind, phi, psi, anchor, rot)

    def _mitre(self, name, kind, phi, psi, anchor, rot):
        doc = FreeCAD.newDocument("Mitre")
        prof = oracle.kind_profile(kind)
        P = V(1000, 300, 200)
        LA, LB = 1500.0, 1300.0
        ua = V(-1, 0, 0)  # away from the joint, member A
        ub = _dir(phi, psi)
        qa, qb = P + ua * LA, P + ub * LB
        fr = frames.Frame(doc)
        a = fr.add("A", prof.family, prof.size, [(tuple(qa), tuple(P))], anchor, rot)
        b = fr.add("B", prof.family, prof.size, [(tuple(P), tuple(qb))], anchor, rot)
        fr.mitre("A", 0, "End", "B", 0, "Start")
        doc.recompute()
        ta, tb = fr.heads["A"], fr.heads["B"]
        for t in (ta, tb):
            self.assertNotIn("Invalid", t.State, t.getStatusString())
            self.assertTrue(t.Shape.isValid())
            self.assertEqual(len(t.Shape.Solids), 1)
        sa, sb = ta.Shape.Solids[0], tb.Shape.Solids[0]
        # common volume 0
        com = sa.common(sb).Volume
        rel = com / min(sa.Volume, sb.Volume)
        worst["common"] = max(worst["common"], rel)
        self.assertLess(rel, 1e-9)
        # the mitre plane bisects the joint angle: equal angle to each member, each half of the joint angle
        joint = math.acos(ua.dot(ub))
        uA, uB = oracle.unit(P - qa), oracle.unit(qb - P)
        nA = end_face_normal(sa, uA, "End")
        nB = end_face_normal(sb, uB, "Start")
        for n in (nA, nB):
            self.assertLess(abs(abs(n.dot(nA)) - 1.0), 1e-9)  # one plane
        thetaA = math.asin(min(1.0, abs(nA.dot(uA))))
        thetaB = math.asin(min(1.0, abs(nA.dot(uB))))
        # angle between the plane and each member axis is half the joint angle
        err = max(abs(thetaA - joint / 2.0), abs(thetaB - joint / 2.0), abs(thetaA - thetaB))
        worst["bisect"] = max(worst["bisect"], err)
        self.assertLess(err, 1e-9)
        # analytic trimmed volumes: area x centroid-line length to the plane
        n = oracle.unit(ua - ub)
        oa = oracle.centroid_offset(prof, uA, anchor, rot)
        ob = oracle.centroid_offset(prof, uB, anchor, rot)
        sA_end = oracle.plane_param(qa + oa, uA, P, n)
        sB_start = oracle.plane_param(P + ob, uB, P, n)
        va = prof.area() * sA_end
        vb = prof.area() * (LB - sB_start)
        for got, want in ((sa.Volume, va), (sb.Volume, vb)):
            e = abs(got - want) / want
            worst["volume"] = max(worst["volume"], e)
            self.assertLess(e, 1e-6, name)
        FreeCAD.closeDocument(doc.Name)


class TestButtFaceBody(unittest.TestCase):
    def tearDown(self):
        for n in list(FreeCAD.listDocuments()):
            FreeCAD.closeDocument(n)

    def _tee(self, doc, ykind, xkind, ux, anchor_x="Centre", rot=0.0, trim_end="End", via="Butt"):
        """Through member Y along X; member X ends on Y's axis at P, coming from direction ux."""
        py = oracle.kind_profile(ykind, 1.0)
        px = oracle.kind_profile(xkind, 0.0)
        P = V(2000, 0, 0)
        LX = 900.0
        ux = oracle.unit(ux)
        far = P + ux * LX
        fr = frames.Frame(doc)
        fr.add("Y", py.family, py.size, [((0, 0, 0), (4000, 0, 0))])
        seg = (tuple(far), tuple(P)) if trim_end == "End" else (tuple(P), tuple(far))
        fr.add("X", px.family, px.size, [seg], anchor_x, rot)
        return fr, py, px, P, far

    def test_butt_to_member_face(self):
        cases = [
            ("RHS on RHS 90", "RHS", "RHS", V(0, 0, 1), "Centre", 0, "End"),
            ("RHS on RHS 45 start end", "RHS", "RHS", V(0.7071067811865476, 0, 0.7071067811865476), "Centre", 0, "Start"),
            ("RHS on RHS 135 anchored", "RHS", "RHS", V(-0.5, 0.2, 0.8), "Bottom", 10, "End"),
            ("I on RHS 60", "RHS", "I", V(0.5, 0, 0.8660254037844386), "Centre", 0, "End"),
            ("L on RHS 70 tilted", "RHS", "L", V(-0.3, 0.1, 0.9), "Top Left", 0, "Start"),
            ("CHS on RHS 90", "RHS", "CHS", V(0, 0, 1), "Centre", 0, "End"),
        ]
        for name, yk, xk, ux, anchor, rot, end in cases:
            with self.subTest(name):
                doc = FreeCAD.newDocument("Butt")
                fr, py, px, P, far = self._tee(doc, yk, xk, ux, anchor, rot, end)
                fr.butt("X", 0, end, "Y", 0)
                doc.recompute()
                head = fr.heads["X"]
                self.assertNotIn("Invalid", head.State, head.getStatusString())
                sol = head.Shape.Solids[0]
                self.assertTrue(sol.isValid())
                # Y's top face is z = ymax of its section (Y runs along X with profile y = Z, Centre anchor)
                top = (py.bbox()[3] - py.bbox()[2]) / 2.0
                uX = oracle.unit(P - far) if end == "End" else oracle.unit(far - P)
                oX = oracle.centroid_offset(px, uX, anchor, rot)
                p0 = far if end == "End" else P
                s_hit = oracle.plane_param(p0 + oX, uX, V(0, 0, top), V(0, 0, 1))
                if end == "End":
                    want = px.area() * s_hit
                else:
                    want = px.area() * (LX - s_hit)
                err = abs(sol.Volume - want) / want
                worst["volume"] = max(worst["volume"], err)
                self.assertLess(err, 1e-6)
                # no common volume with Y; touching it
                y = fr.members["Y"].Shape
                self.assertLess(sol.common(y).Volume / sol.Volume, 1e-9)
                self.assertLess(sol.distToShape(y)[0], 1e-6)
                FreeCAD.closeDocument(doc.Name)

    def test_trim_and_extend_to_planar_face(self):
        """Face kind: the axis meets the plane beyond the path end (extend) or before it (trim)."""
        for mode, reach in (("trim", 1200.0), ("extend", 700.0), ("trim oblique", 1200.0), ("extend oblique", 600.0)):
            with self.subTest(mode):
                doc = FreeCAD.newDocument("Face")
                prof = oracle.kind_profile("RHS", 0.3)
                tilt = 0.0 if "oblique" not in mode else 0.45
                u = oracle.unit(V(tilt, 0.1 * tilt, 1.0))
                p0 = V(100, 200, 0)
                p1 = p0 + u * reach
                plate = doc.addObject("Part::Box", "Plate")
                plate.Length, plate.Width, plate.Height = 6000, 6000, 20
                plate.Placement = FreeCAD.Placement(V(-2000, -2000, 1000), FreeCAD.Rotation())
                fr = frames.Frame(doc)
                fr.add("X", prof.family, prof.size, [(tuple(p0), tuple(p1))])
                doc.recompute()
                face = "Face%d" % (1 + [round(f.normalAt(0, 0).z) for f in plate.Shape.Faces].index(-1))  # bottom face
                fr.heads["X"] = WeldTrim.make_trim(doc, fr.members["X"], 0, "End", "Face", plate, face)
                doc.recompute()
                head = fr.heads["X"]
                self.assertNotIn("Invalid", head.State, head.getStatusString())
                o = oracle.centroid_offset(prof, u)
                s_hit = oracle.plane_param(p0 + o, u, V(0, 0, 1000), V(0, 0, 1))
                self.assertEqual(s_hit > reach, "extend" in mode)
                want = prof.area() * s_hit
                got = head.Shape.Solids[0].Volume
                err = abs(got - want) / want
                worst["volume"] = max(worst["volume"], err)
                self.assertLess(err, 1e-6)
                FreeCAD.closeDocument(doc.Name)

    def test_trim_and_extend_to_body(self):
        for mode, reach, start in (("trim", 1300.0, False), ("extend", 800.0, False), ("start end", 1100.0, True)):
            with self.subTest(mode):
                doc = FreeCAD.newDocument("Body")
                prof = oracle.kind_profile("I", 0.2)
                u = oracle.unit(V(0.3, -0.2, 1.0))
                p0 = V(0, 0, 0)
                block = doc.addObject("Part::Box", "Block")
                block.Length, block.Width, block.Height = 3000, 3000, 400
                block.Placement = FreeCAD.Placement(V(-1500, -1500, 1000), FreeCAD.Rotation())
                fr = frames.Frame(doc)
                if not start:
                    seg = (tuple(p0), tuple(p0 + u * reach))
                    fr.add("X", prof.family, prof.size, [seg])
                    end = "End"
                else:
                    # path runs from inside the block down to the origin: its Start end is trimmed to the block
                    seg = (tuple(p0 + u * reach), tuple(p0))
                    fr.add("X", prof.family, prof.size, [seg])
                    end = "Start"
                doc.recompute()
                if start:
                    uu = oracle.unit(-u)
                else:
                    uu = u
                head = WeldTrim.make_trim(doc, fr.members["X"], 0, end, "Body", block, "", -1)
                doc.recompute()
                self.assertNotIn("Invalid", head.State, head.getStatusString())
                o = oracle.centroid_offset(prof, uu)
                p_start = seg[0]
                s_hit = oracle.plane_param(V(*p_start) + o, uu, V(0, 0, 1000), V(0, 0, 1))
                want = prof.area() * ((reach - s_hit) if start else s_hit)
                got = head.Shape.Solids[0].Volume
                self.assertLess(abs(got - want) / want, 1e-6)
                self.assertLess(head.Shape.Solids[0].common(block.Shape).Volume / got, 1e-9)
                FreeCAD.closeDocument(doc.Name)



class TestRefusals(unittest.TestCase):
    def tearDown(self):
        for n in list(FreeCAD.listDocuments()):
            FreeCAD.closeDocument(n)

    def _setup(self):
        doc = FreeCAD.newDocument("Ref")
        prof = oracle.kind_profile("RHS", 0.3)
        fr = frames.Frame(doc)
        fr.add("X", prof.family, prof.size, [((0, 0, 0), (0, 0, 800))])
        plate = doc.addObject("Part::Box", "Plate")
        plate.Length, plate.Width, plate.Height = 500, 500, 20
        return doc, fr, plate, prof

    def _bad(self, trim, words):
        trim.Document.recompute()
        self.assertIn("Invalid", trim.State, trim.Name)
        self.assertIn(words, trim.getStatusString())

    def _face(self, plate, nz):
        return "Face%d" % (1 + [round(f.normalAt(0, 0).z) for f in plate.Shape.Faces].index(nz))

    def test_extend_to_face_not_reached(self):
        doc, fr, plate, prof = self._setup()
        # 1 face off to the side: the axis meets its plane well outside the 500 x 500 plate
        plate.Placement = FreeCAD.Placement(V(5000, 5000, 1500), FreeCAD.Rotation())
        doc.recompute()
        t = WeldTrim.make_trim(doc, fr.members["X"], 0, "End", "Face", plate, self._face(plate, -1))
        self._bad(t, "does not reach")
        doc2, fr2, plate2, _ = self._setup()
        # 2 plane parallel to the axis
        plate2.Placement = FreeCAD.Placement(V(300, -250, 0), FreeCAD.Rotation(V(0, 1, 0), 90))
        doc2.recompute()
        face = "Face%d" % (1 + [round(f.normalAt(0, 0).x) for f in plate2.Shape.Faces].index(-1))
        t2 = WeldTrim.make_trim(doc2, fr2.members["X"], 0, "End", "Face", plate2, face)
        self._bad(t2, "never reaches")
        doc3, fr3, plate3, _ = self._setup()
        # 3 plane behind the start of the member
        plate3.Placement = FreeCAD.Placement(V(-250, -250, -500), FreeCAD.Rotation())
        doc3.recompute()
        t3 = WeldTrim.make_trim(doc3, fr3.members["X"], 0, "End", "Face", plate3, self._face(plate3, 1))
        self._bad(t3, "behind the start")

    def test_body_not_reached(self):
        doc, fr, plate, prof = self._setup()
        plate.Placement = FreeCAD.Placement(V(3000, 3000, 900), FreeCAD.Rotation())
        t = WeldTrim.make_trim(doc, fr.members["X"], 0, "End", "Body", plate, "", -1)
        self._bad(t, "never reaches the body")

    def test_mitre_refusals(self):
        doc = FreeCAD.newDocument("Ref")
        prof = oracle.kind_profile("RHS", 0.3)
        fr = frames.Frame(doc)
        fr.add("A", prof.family, prof.size, [((0, 0, 0), (1000, 0, 0))])
        fr.add("B", prof.family, prof.size, [((1500, 0, 0), (1500, 0, 900))])  # does not meet A
        fr.add("C", prof.family, prof.size, [((1000, 0, 0), (0, 1, 0))])  # folds back over A
        fr.mitre("A", 0, "End", "B", 0, "Start")
        self._bad(fr.heads["A"], "do not meet")
        doc2 = FreeCAD.newDocument("Ref2")
        fr2 = frames.Frame(doc2)
        fr2.add("A", prof.family, prof.size, [((0, 0, 0), (1000, 0, 0))])
        fr2.add("C", prof.family, prof.size, [((1000, 0, 0), (0, 1, 0))])
        fr2.mitre("A", 0, "End", "C", 0, "Start")
        self._bad(fr2.heads["A"], "fold back")

    def test_end_treated_twice(self):
        doc, fr, plate, prof = self._setup()
        plate.Placement = FreeCAD.Placement(V(-250, -250, 1000), FreeCAD.Rotation())
        doc.recompute()
        f = self._face(plate, -1)
        t1 = WeldTrim.make_trim(doc, fr.members["X"], 0, "End", "Face", plate, f)
        t2 = WeldTrim.make_trim(doc, t1, 0, "End", "Face", plate, f)
        self._bad(t2, "already treated")


if __name__ == "__main__":
    unittest.main()
