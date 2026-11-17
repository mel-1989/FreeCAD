# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI tests of SurfaceAnalysisGui: curvature, draft and zebra overlays in the 3D view.

Run with ``FreeCAD -t TestSurfaceGui`` (needs the GUI binary; Qt's offscreen platform is
enough for the scene-graph checks, the pixel checks need a real display).
"""

import math
import os
import random
import tempfile
import time
import unittest
import zipfile

import FreeCAD
import FreeCADGui
import Part

import SurfaceAnalysis as SA
import SurfaceAnalysisGui as SG

V = FreeCAD.Vector


def _pump(n=4, d=0.02):
    from PySide import QtWidgets

    for _ in range(n):
        QtWidgets.QApplication.processEvents()
        time.sleep(d)


def _ramp(s):
    """Independent ramp: blue, cyan, green, yellow, red at quarters."""
    s = min(1.0, max(0.0, s))
    stops = [(0, 0, 1), (0, 1, 1), (0, 1, 0), (1, 1, 0), (1, 0, 0)]
    x = s * 4.0
    i = min(3, int(x))
    f = x - i
    return tuple(stops[i][k] + f * (stops[i + 1][k] - stops[i][k]) for k in range(3))


def _child(node, type_name):
    for i in range(node.getNumChildren()):
        ch = node.getChild(i)
        if ch.getTypeId().getName().getString() in (type_name, type_name[2:]):
            return ch
    return None


def _colors(node):
    m = _child(node, "SoMaterial")
    n = m.diffuseColor.getNum()
    return [tuple(c.getValue()) for c in m.diffuseColor.getValues(0)][:n]


def _points(node):
    c = _child(node, "SoCoordinate3")
    return [tuple(p.getValue()) for p in c.point.getValues(0)][: c.point.getNum()]


def _lateral_cyl():
    return next(f for f in Part.makeCylinder(5, 10).Faces if f.Surface.TypeId == "Part::GeomCylinder")


class TestSurfaceAnalysisGui(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument("G09Gui")
        anchor = self.doc.addObject("Part::Feature", "Anchor")  # a document needs content before its view shows
        anchor.Shape = Part.makeBox(1, 1, 1, V(500, 500, 500))
        self.doc.recompute()
        FreeCADGui.ActiveDocument = FreeCADGui.getDocument(self.doc.Name)
        _pump()
        self.view = FreeCADGui.ActiveDocument.ActiveView
        self.assertIsNotNone(self.view, "no 3D view")
        self.ufs = SG._find_selection_node(self.view)
        self.base_children = self.ufs.getNumChildren()

    def tearDown(self):
        SG.clear(self.view)
        FreeCAD.closeDocument(self.doc.Name)

    def obj(self, shape, name="S"):
        o = self.doc.addObject("Part::Feature", name)
        o.Shape = shape
        self.doc.recompute()
        _pump()
        return o

    def assertClean(self):
        self.assertEqual(self.ufs.getNumChildren(), self.base_children)

    # ------------------------------------------------------------ check 1

    def test_vertices_lie_on_faces_and_match_nodes(self):
        box = Part.makeBox(20, 20, 10)
        hole = Part.makeCylinder(4, 10, V(10, 10, 0))
        reversed_sphere = Part.makeSphere(10).Faces[0].copy()
        reversed_sphere.reverse()
        bs = Part.makeSphere(10).Faces[0].Surface.toBSpline().toShape()
        shapes = [
            Part.makeSphere(10),
            Part.makeCylinder(5, 10),
            Part.makeTorus(20, 5),
            box.cut(hole),
            reversed_sphere,
            bs,
        ]
        for shp in shapes:
            h = SG.show_curvature(shp, "mean", view=self.view)
            pts = _points(h.node)
            self.assertEqual(len(pts), len(h.vertices))
            nfaces = len(shp.Faces)
            seen = set()
            for rec, q in zip(h.vertices, pts):
                seen.add(rec["face"])
                # the node stores the single-precision rounding of the exact vertex
                self.assertLess((SG._f32(rec["point"]) - V(*q)).Length, 1e-9)
                face = shp.Faces[rec["face"] - 1]
                self.assertLess(Part.Vertex(rec["point"]).distToShape(face)[0], 1e-6)
            self.assertEqual(seen, set(range(1, nfaces + 1)))
            SG.remove(h)
        self.assertClean()

    # ------------------------------------------------------------ check 2/3

    def test_curvature_values_and_colors(self):
        for kind, expect in (("mean", 0.1), ("kmax", 0.1), ("kmin", 0.1), ("gauss", 0.01)):
            h = SG.show_curvature(Part.makeSphere(10), kind, view=self.view)
            vals = [r["value"] for r in h.vertices if not math.isnan(r["value"])]
            for x in vals:
                self.assertAlmostEqual(x, expect, delta=1e-6 * expect)
            lo, hi = h.range
            self.assertAlmostEqual(hi, expect, delta=1e-9 * expect)
            self.assertAlmostEqual(lo, -expect, delta=1e-9 * expect)
            cols = _colors(h.node)
            self.assertEqual(len(cols), len(h.vertices))
            for r, c in zip(h.vertices, cols):
                if math.isnan(r["value"]):
                    want = (0.5, 0.5, 0.5)
                else:
                    want = _ramp((r["value"] - lo) / (hi - lo))
                for k in range(3):
                    self.assertAlmostEqual(c[k], want[k], delta=1 / 255.0)
                    self.assertAlmostEqual(r["color"][k], want[k], delta=1e-6)
            SG.remove(h)

    def test_curvature_sign_cylinder_and_reversed(self):
        cyl = Part.makeCylinder(5, 10).Faces[0]
        h = SG.show_curvature(cyl, "kmax", view=self.view)
        self.assertTrue(all(abs(r["value"] - 0.2) < 1e-6 for r in h.vertices))
        h2 = SG.show_curvature(cyl, "kmin", view=self.view)
        self.assertTrue(all(abs(r["value"]) < 1e-9 for r in h2.vertices))
        rev = Part.makeSphere(10).Faces[0].copy()
        rev.reverse()
        h3 = SG.show_curvature(rev, "mean", view=self.view)
        vals = [r["value"] for r in h3.vertices if not math.isnan(r["value"])]
        self.assertTrue(all(abs(x + 0.1) < 1e-6 for x in vals))

    def test_user_range_and_matches_slice1(self):
        face = Part.makeTorus(20, 5).Faces[0]
        h = SG.show_curvature(face, "mean", range=(-0.05, 0.1), view=self.view)
        self.assertEqual(h.range, (-0.05, 0.1))
        for r in h.vertices[::17]:
            ref = SA.curvature(face, r["u"], r["v"])["mean"]
            self.assertAlmostEqual(r["value"], ref, delta=1e-9 + 1e-9 * abs(ref))
            want = _ramp((ref + 0.05) / 0.15)
            for k in range(3):
                self.assertAlmostEqual(r["color"][k], want[k], delta=1e-6)

    def test_nan_at_poles_and_apex(self):
        for shp in (Part.makeSphere(10), Part.makeCone(5, 0, 10)):
            h = SG.show_curvature(shp, "mean", view=self.view)
            nans = [r for r in h.vertices if math.isnan(r["value"])]
            self.assertGreaterEqual(len(nans), 1)
            for r in nans:
                self.assertEqual(r["color"], (0.5, 0.5, 0.5))
            SG.remove(h)

    # ------------------------------------------------------------ check 4

    def test_draft_classes(self):
        box = Part.makeBox(10, 10, 10)
        h = SG.show_draft(box, V(0, 0, 1), view=self.view)
        for r in h.vertices:
            n = box.Faces[r["face"] - 1].normalAt(r["u"], r["v"])
            if n.z > 0.5:
                self.assertEqual(r["color"], (0.0, 2.0 / 3.0, 0.0))
                self.assertAlmostEqual(r["value"], 90.0, places=6)
            elif n.z < -0.5:
                self.assertEqual(r["color"], (0.8627, 0.0, 0.0))
            else:
                self.assertEqual(r["color"], (1.0, 0.7843, 0.0))
        self.assertEqual(h.kind, "draft")
        self.assertIsNone(h.range)
        # cone widening by 2 degrees along the pull: -2 degrees of draft, negative at 1, insufficient at 3
        height = 20.0
        r2 = 5.0 + height * math.tan(math.radians(2.0))
        cone = Part.makeCone(5.0, r2, height)
        face = [f for f in cone.Faces if f.Surface.TypeId == "Part::GeomCone"][0]
        for md, cls in ((1.0, (0.8627, 0.0, 0.0)), (3.0, (1.0, 0.7843, 0.0))):
            h = SG.show_draft(face, V(0, 0, 1), min_draft=md, view=self.view)
            for r in h.vertices:
                self.assertAlmostEqual(abs(r["value"]), 2.0, places=6)
                self.assertAlmostEqual(r["value"], -2.0, places=6)
                self.assertEqual(r["color"], cls)
            SG.remove(h)

    # ------------------------------------------------------------ check 5

    def test_zebra_parameter_and_texture(self):
        cyl = Part.makeCylinder(5, 10)
        for bands in (8, 16, 32):
            h = SG.show_zebra(cyl, (1, 0, 0), bands=bands, view=self.view)
            tc = _child(h.node, "SoTextureCoordinate2")
            n = tc.point.getNum()
            self.assertEqual(n, len(h.vertices))
            tvals = [x[0] for x in tc.point.getValues(0)][:n]
            for r, t in zip(h.vertices, tvals):
                face = cyl.Faces[r["face"] - 1]
                nrm = face.normalAt(r["u"], r["v"])
                want = math.acos(max(-1.0, min(1.0, nrm.x))) / math.pi
                self.assertAlmostEqual(r["value"], want, delta=1e-9)
                self.assertAlmostEqual(t, want, delta=1e-6)
            tx = _child(h.node, "SoTexture2")
            tok = tx.image.get().getString().split()
            self.assertEqual(tok[:3], ["256", "1", "3"])
            texels = [int(x, 16) for x in tok[3:]]
            self.assertEqual(len(texels), 256)
            width = 256 // bands
            for i, texel in enumerate(texels):
                self.assertEqual(texel, 0xFFFFFF if (i // width) % 2 else 0x000000)
            SG.remove(h)
        sph = Part.makeSphere(10).Faces[0].copy()
        sph.reverse()
        h = SG.show_zebra(sph, (0, 0, 1), view=self.view)
        for r in h.vertices:
            if math.isnan(r["value"]):
                continue
            nrm = sph.normalAt(r["u"], r["v"])
            self.assertAlmostEqual(r["value"], math.acos(max(-1.0, min(1.0, nrm.z))) / math.pi, delta=1e-9)

    # ------------------------------------------------------------ check 7

    def test_lifecycle_and_persistence(self):
        a = self.obj(Part.makeBox(10, 10, 10), "A")
        b = self.obj(Part.makeSphere(5), "B")
        before_objs = len(self.doc.Objects)
        h1 = SG.show_curvature(a, view=self.view, legend=False)  # the legend is tested in TestSurfaceAnalysisCommands
        h2 = SG.show_zebra(b, view=self.view)
        h3 = SG.show_draft(a, (0, 0, 1), view=self.view, legend=False)
        self.assertEqual(self.ufs.getNumChildren(), self.base_children + 3)
        self.assertEqual(h1.node.getName().getString(), "G09_OVERLAY_curvature")
        self.assertEqual(h2.node.getName().getString(), "G09_OVERLAY_zebra")
        self.assertEqual(h3.node.getName().getString(), "G09_OVERLAY_draft")
        path = os.path.join(tempfile.mkdtemp(prefix="g09gui-"), "t.FCStd")
        self.doc.saveAs(path)
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                self.assertNotIn(b"G09_OVERLAY", z.read(name))
        SG.remove(h1)
        self.assertEqual(self.ufs.getNumChildren(), self.base_children + 2)
        SG.remove(h1)  # twice is a no-op
        self.assertEqual(self.ufs.getNumChildren(), self.base_children + 2)
        SG.clear(self.view)
        self.assertClean()
        SG.remove(h2)
        self.assertClean()
        self.assertEqual(len(self.doc.Objects), before_objs)

    def test_no_side_effects_on_inputs(self):
        o = self.obj(Part.makeBox(10, 10, 10).fuse(Part.makeSphere(4, V(10, 5, 5))), "Fused")
        vp = o.ViewObject
        props = {p: str(vp.getPropertyByName(p)) for p in vp.PropertiesList}
        brep = o.Shape.exportBrepToString()
        n_objs = len(self.doc.Objects)
        for h in (
            SG.show_curvature(o, "gauss", view=self.view),
            SG.show_draft(o, (0, 0, 1), view=self.view),
            SG.show_zebra(o, view=self.view),
        ):
            SG.remove(h)
        self.assertEqual(o.Shape.exportBrepToString(), brep)
        self.assertEqual(len(self.doc.Objects), n_objs)
        self.assertEqual({p: str(vp.getPropertyByName(p)) for p in vp.PropertiesList}, props)

    def test_overlay_is_unlit_unpickable_and_offset(self):
        for h in (
            SG.show_curvature(Part.makeSphere(5), view=self.view),
            SG.show_draft(Part.makeSphere(5), (0, 0, 1), view=self.view),
            SG.show_zebra(Part.makeSphere(5), view=self.view),
        ):
            from pivy import coin

            ps = _child(h.node, "SoPickStyle")
            lm = _child(h.node, "SoLightModel")
            self.assertEqual(ps.style.getValue(), coin.SoPickStyle.UNPICKABLE)
            self.assertEqual(lm.model.getValue(), coin.SoLightModel.BASE_COLOR)
            self.assertIsNotNone(_child(h.node, "SoPolygonOffset"))
            if h.kind != "zebra":
                mb = _child(h.node, "SoMaterialBinding")
                self.assertEqual(mb.value.getValue(), coin.SoMaterialBinding.PER_VERTEX_INDEXED)
            SG.remove(h)

    # ------------------------------------------------------------ check 8

    def test_errors_leave_scene_untouched(self):
        shp = Part.makeSphere(5)
        obj = self.obj(shp)
        nofaces = self.doc.addObject("App::FeaturePython", "NoShape")
        bad = [
            lambda: SG.show_curvature(shp, "bogus", view=self.view),
            lambda: SG.show_draft(shp, (0, 0, 0), view=self.view),
            lambda: SG.show_zebra(shp, (0, 0, 0), view=self.view),
            lambda: SG.show_curvature(shp, range=(1.0, 1.0), view=self.view),
            lambda: SG.show_curvature(shp, range=(2.0, 1.0), view=self.view),
            lambda: SG.show_curvature(nofaces, view=self.view),
            lambda: SG.show_curvature(Part.Shape(), view=self.view),
            lambda: SG.show_zebra(obj, bands=0, view=self.view),
        ]
        bad += [(lambda b=b: SG.show_zebra(shp, bands=b, view=self.view)) for b in (0, 1, 7, 300)]
        for f in bad:
            with self.assertRaises(ValueError):
                f()
            self.assertClean()

    # ------------------------------------------------------------ check 6

    def test_pixels_flat_colour(self):
        """A plane whose user range puts every vertex at one ramp stop renders in that colour."""
        from PySide import QtGui

        o2 = self.obj(Part.makePlane(20, 20, V(-10, -10, 0)), "Plane")
        self.view.setCameraType("Orthographic")
        self.view.setCamera(
            "#Inventor V2.1 ascii\nOrthographicCamera { position 0 0 50 orientation 0 0 1 0 "
            "nearDistance 1 farDistance 200 aspectRatio 1.3333 focalDistance 50 height 30 }"
        )
        o2.ViewObject.Visibility = False
        # plane mean curvature is 0: range (-1, 1) puts s = 0.5 (green); (0, 2) clamps to s = 0 (blue)
        for rng, want in (((-1.0, 1.0), (0, 255, 0)), ((0.0, 2.0), (0, 0, 255))):
            h = SG.show_curvature(o2, "mean", range=rng, view=self.view)
            _pump(6)
            path = os.path.join(tempfile.mkdtemp(prefix="g09gui-"), "p.png")
            self.view.saveImage(path, 480, 360, "White")
            img = QtGui.QImage(path).convertToFormat(QtGui.QImage.Format_RGB888)
            c = img.pixelColor(240, 180)
            self.assertEqual((c.red(), c.green(), c.blue()), want)
            SG.remove(h)
        o2.ViewObject.Visibility = True

    # ------------------------------------------------------------ check 9

    def test_speed(self):
        t0 = time.time()
        h = SG.show_curvature(Part.makeSphere(10), "mean", view=self.view)
        t_sphere = time.time() - t0
        SG.remove(h)
        self.assertLess(t_sphere, 0.5)

        def bspline_face(rng, n, degree):
            ph = [rng.uniform(0, 6.28) for _ in range(4)]
            poles = []
            for i in range(n):
                row = []
                for j in range(n):
                    x, y = 3.0 * i, 3.0 * j
                    z = 2.0 * math.sin(0.35 * x + ph[0]) * math.cos(0.3 * y + ph[1])
                    z += 0.5 * math.sin(0.2 * x + 0.25 * y + ph[2])
                    row.append(V(x, y, z))
                poles.append(row)
            nk = n - degree + 1
            mults = [degree + 1] + [1] * (nk - 2) + [degree + 1]
            knots = [i / (nk - 1) for i in range(nk)]
            s = Part.BSplineSurface()
            s.buildFromPolesMultsKnots(poles, mults, mults, knots, knots, False, False, degree, degree)
            return s.toShape()

        rng = random.Random(7)
        faces = [bspline_face(rng, 11, 3) for _ in range(50)] + [bspline_face(rng, 16, 5) for _ in range(50)]
        comp = Part.makeCompound(faces)
        self.assertEqual(len(comp.Faces), 100)
        t0 = time.time()
        h = SG.show_curvature(comp, "mean", view=self.view)
        t_100 = time.time() - t0
        SG.remove(h)
        print("G09 overlay speed: sphere %.3f s, 100 faces %.2f s" % (t_sphere, t_100))
        self.assertLess(t_100, 20.0)

    def test_bspline_face_values_match_slice1(self):
        bs = Part.makeSphere(10).Faces[0].Surface.toBSpline().toShape().Faces[0]
        h = SG.show_curvature(bs, "gauss", view=self.view)
        good = [r for r in h.vertices if not math.isnan(r["value"])]
        self.assertGreater(len(good), 10)
        for r in good[::7]:
            ref = SA.curvature(bs, r["u"], r["v"])["gauss"]
            self.assertAlmostEqual(r["value"], ref, delta=1e-9 * max(1.0, abs(ref)))
            p = bs.valueAt(r["u"], r["v"])
            self.assertLess((p - r["point"]).Length, 1e-6)

    # ------------------------------------------------------- slice 2c: isophotes

    def test_isophote_values_and_segment_endpoints(self):
        d = V(0, 0, 1)
        rev = Part.makeSphere(10).Faces[0].copy()
        rev.reverse()
        for shp, sign in ((Part.makeSphere(10), 1.0), (rev, -1.0)):
            for kw in ({"count": 4}, {"count": 8}, {"angles": [30, 60, 120]}):
                h = SG.show_isophotes(shp, d, view=self.view, **kw)
                self.assertEqual(h.kind, "isophote")
                self.assertIsNone(h.range)
                self.assertIsNone(h.legend)
                for r in h.vertices:
                    if math.isnan(r["value"]):
                        continue
                    p = r["point"]
                    want = math.degrees(math.acos(max(-1.0, min(1.0, sign * p.z / 10.0))))
                    self.assertAlmostEqual(r["value"], want, delta=1e-6)
                    self.assertEqual(r["color"], (0.0, 0.0, 0.0))
                self.assertGreater(len(h.segments), 0)
                levels = {s["level"] for s in h.segments}
                want_levels = kw.get("angles") or [k * 180.0 / kw["count"] for k in range(1, kw["count"])]
                self.assertTrue(levels <= set(float(x) for x in want_levels))
                SG.remove(h)

    def test_isophote_geometry_sphere_cylinder_plane(self):
        h = SG.show_isophotes(Part.makeSphere(10), (0, 0, 1), count=4, view=self.view)
        for lvl in (45.0, 90.0, 135.0):
            total = sum((s["p1"] - s["p0"]).Length for s in h.segments if s["level"] == lvl)
            want = 2 * math.pi * 10 * math.sin(math.radians(lvl))
            self.assertLess(abs(total - want) / want, 0.02, (lvl, total, want))
            for s in h.segments:
                if s["level"] == lvl:
                    for p in (s["p0"], s["p1"]):
                        th = math.degrees(math.acos(max(-1, min(1, p.z / p.Length))))
                        self.assertLess(abs(th - lvl), 0.5)
        SG.remove(h)
        cyl = _lateral_cyl()
        h = SG.show_isophotes(cyl, (1, 0, 0), count=4, view=self.view)
        for lvl in (45.0, 90.0, 135.0):
            total = sum((s["p1"] - s["p0"]).Length for s in h.segments if s["level"] == lvl)
            self.assertLess(abs(total - 2 * 10.0) / 20.0, 0.02, (lvl, total))
        SG.remove(h)
        h = SG.show_isophotes(Part.makePlane(10, 10), (0, 0, 1), count=8, view=self.view)
        self.assertEqual(h.segments, [])
        SG.remove(h)
        self.assertClean()

    def test_isophote_overlay_is_unlit_unpickable_black_and_clearable(self):
        from pivy import coin

        o = self.obj(Part.makeSphere(5))
        h = SG.show_isophotes(o, view=self.view)
        self.assertEqual(h.node.getName().getString(), "G09_OVERLAY_isophote")
        self.assertEqual(_child(h.node, "SoPickStyle").style.getValue(), coin.SoPickStyle.UNPICKABLE)
        self.assertEqual(_child(h.node, "SoLightModel").model.getValue(), coin.SoLightModel.BASE_COLOR)
        self.assertEqual(self.ufs.getNumChildren(), self.base_children + 1)
        SG.remove(h)
        self.assertClean()
        SG.remove(h)
        self.assertClean()
        SG.show_isophotes(o, view=self.view)
        SG.show_combs(o, view=self.view)
        self.assertEqual(self.ufs.getNumChildren(), self.base_children + 2)
        SG.clear(self.view)
        self.assertClean()

    def test_isophote_errors_leave_scene_untouched(self):
        shp = Part.makeSphere(5)
        for kw in ({"count": 1}, {"count": 91}, {"count": True}, {"count": 2.5}, {"angles": [0]},
                   {"angles": [180]}, {"angles": [30, -1]}, {"angles": []}, {"direction": (0, 0, 0)}):
            with self.assertRaises(ValueError, msg=str(kw)):
                SG.show_isophotes(shp, view=self.view, **kw)
            self.assertClean()

    def test_isophote_lines_are_visible_on_a_shaded_cylinder(self):
        from PySide import QtGui

        o = self.obj(Part.makeCylinder(5, 10, V(0, 0, -5)), "Cyl")
        self.view.setCameraType("Orthographic")
        self.view.setCamera(
            "#Inventor V2.1 ascii\nOrthographicCamera { position 0 -50 0 orientation 1 0 0 1.5707963 "
            "nearDistance 1 farDistance 200 aspectRatio 1.3333 focalDistance 50 height 30 }"
        )
        self.doc.getObject("Anchor").ViewObject.Visibility = False
        h = SG.show_isophotes(o, (1, 0, 0), count=8, view=self.view)
        _pump(8)
        path = os.path.join(tempfile.mkdtemp(prefix="g09gui-"), "iso.png")
        self.view.saveImage(path, 480, 360, "White")
        img = QtGui.QImage(path).convertToFormat(QtGui.QImage.Format_RGB888)
        for k in range(1, 8):
            px = 240 + 5.0 * math.cos(k * math.pi / 8) * 12.0
            dark = [x for x in range(int(px) - 2, int(px) + 4) if img.pixelColor(x, 150).red() < 80]
            self.assertTrue(dark, "no dark pixel near x = %.1f for k = %d" % (px, k))
        SG.remove(h)

    # ---------------------------------------------------- slice 2c: curvature combs

    def test_comb_values_circle_ellipse_helix(self):
        cases = [
            (Part.makeCircle(10), lambda p: 0.1),
            (Part.Ellipse(V(0, 0, 0), 10, 4).toShape(), None),
        ]
        for edge, truth in cases:
            h = SG.show_combs(edge, samples=9, view=self.view)
            self.assertEqual(h.kind, "comb")
            self.assertEqual(h.vertices, [])
            self.assertEqual(len(h.teeth), 9)
            first, last = edge.FirstParameter, edge.LastParameter
            for i, th in enumerate(h.teeth):
                self.assertAlmostEqual(th["t"], first + i * (last - first) / 8, delta=1e-12)
                if truth is not None:
                    want = truth(th["point"])
                else:
                    t = math.atan2(th["point"].y / 4.0, th["point"].x / 10.0)
                    want = 10 * 4 / (100 * math.sin(t) ** 2 + 16 * math.cos(t) ** 2) ** 1.5
                self.assertLess(abs(th["curvature"] - want) / want, 1e-9)
                away = th["tip"] - th["point"]
                self.assertLess(abs(away.Length - h.scale * th["curvature"]), 1e-9 * max(1.0, away.Length))
                # a tooth points outward: against the inward normal of a convex curve
                self.assertGreater(away.dot(th["point"]), 0.0)
            SG.remove(h)
        helix = Part.makeHelix(8, 16, 5)
        h = SG.show_combs(helix, samples=9, view=self.view)
        c = 8 / (2 * math.pi)
        for th in h.teeth:
            self.assertLess(abs(th["curvature"] - 5 / (25 + c * c)) / th["curvature"], 1e-3)  # OCCT's helix is a spline approximation
        SG.remove(h)

    def test_comb_scale_rule_and_lines(self):
        edge = Part.makeCircle(10)
        h = SG.show_combs(edge, view=self.view)
        longest = max((th["tip"] - th["point"]).Length for th in h.teeth)
        self.assertLess(abs(longest - 0.2 * edge.BoundBox.DiagonalLength) / longest, 1e-9)
        SG.remove(h)
        h = SG.show_combs(edge, scale=2.5, view=self.view)
        self.assertEqual(h.scale, 2.5)
        SG.remove(h)
        line = Part.makeLine(V(0, 0, 0), V(5, 0, 0))
        h = SG.show_combs([line], view=self.view)
        self.assertEqual(h.scale, 1.0)
        for th in h.teeth:
            self.assertEqual(th["curvature"], 0.0)
            self.assertLess((th["tip"] - th["point"]).Length, 1e-12)
        SG.remove(h)

    def test_comb_drawing_lifecycle_and_errors(self):
        from pivy import coin

        circ = Part.makeCircle(10)
        h = SG.show_combs(circ, view=self.view)
        self.assertEqual(h.node.getName().getString(), "G09_OVERLAY_comb")
        self.assertEqual(_child(h.node, "SoPickStyle").style.getValue(), coin.SoPickStyle.UNPICKABLE)
        self.assertEqual(_child(h.node, "SoLightModel").model.getValue(), coin.SoLightModel.BASE_COLOR)
        SG.remove(h)
        self.assertClean()
        for kw in ({"samples": 1}, {"samples": 2.5}, {"samples": True}, {"scale": 0}, {"scale": -1},
                   {"scale": float("nan")}, {"scale": float("inf")}):
            with self.assertRaises(ValueError, msg=str(kw)):
                SG.show_combs(circ, view=self.view, **kw)
            self.assertClean()
        with self.assertRaises(ValueError):
            SG.show_combs(Part.Vertex(V(1, 2, 3)), view=self.view)
        with self.assertRaises(ValueError):
            SG.show_combs([], view=self.view)
        self.assertClean()

    def test_comb_on_object_in_placed_part_follows_placement(self):
        part = self.doc.addObject("App::Part", "Holder")
        part.Placement = FreeCAD.Placement(V(100, 0, 0), FreeCAD.Rotation(V(0, 0, 1), 90))
        box = self.doc.addObject("Part::Box", "Box")
        part.addObject(box)
        self.doc.recompute()
        want = SG.placed_shape(box)
        h = SG.show_combs(box, view=self.view)
        self.assertEqual(len(h.teeth), 12 * 33)
        for th in h.teeth[::17]:
            self.assertLess(Part.Vertex(th["point"]).distToShape(want)[0], 1e-6)
        SG.remove(h)

    def test_comb_pixels_magenta_on_rays(self):
        from PySide import QtGui

        self.doc.getObject("Anchor").ViewObject.Visibility = False
        self.view.setCameraType("Orthographic")
        self.view.setCamera(
            "#Inventor V2.1 ascii\nOrthographicCamera { position 0 0 50 orientation 0 0 1 0 "
            "nearDistance 1 farDistance 200 aspectRatio 1.3333 focalDistance 50 height 40 }"
        )
        h = SG.show_combs(Part.makeCircle(10), scale=20, samples=33, view=self.view)
        _pump(8)
        path = os.path.join(tempfile.mkdtemp(prefix="g09gui-"), "comb.png")
        self.view.saveImage(path, 480, 360, "White")
        img = QtGui.QImage(path).convertToFormat(QtGui.QImage.Format_RGB888)
        hits = 0
        for ang in range(0, 360, 45):
            r = 11.5 * 9.0
            x = int(round(240 + r * math.cos(math.radians(ang))))
            y = int(round(180 - r * math.sin(math.radians(ang))))
            near = [img.pixelColor(x + dx, y + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
            hits += any(abs(c.red() - 204) < 30 and c.green() < 60 and abs(c.blue() - 204) < 30 for c in near)
        self.assertEqual(hits, 8)
        SG.remove(h)
