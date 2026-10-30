# SPDX-License-Identifier: LGPL-2.1-or-later
"""F2 thickness map and F4 render tool: ramp, per-face thickness, the render tool's paths (stubbed GUI for the
protocol checks, the real GUI for the end-to-end check when this machine has one)."""

import base64
import json
import os
import struct
import tempfile
import unittest
import zlib
from unittest import mock

import FreeCAD
import Part

from AgentCore.render import host, ramp
from AgentCore.verify import thickness
from AgentCoreTests.fixtures.mcp import mcp_common as mc

V = FreeCAD.Vector


def stepped_block():
    """30 x 30 x 20 block fused with a 30 x 30 x 2 plate: top and bottom faces are 20 mm and 2 mm thick."""
    return Part.makeBox(30, 30, 20).fuse(Part.makeBox(30, 30, 2, V(30, 0, 0))).removeSplitter()


def read_png(path):
    """Minimal PNG reader (8-bit RGB or RGBA, non-interlaced): returns (width, height, rows of (r, g, b))."""
    data = open(path, "rb").read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, w = 8, b"", None
    while pos < len(data):
        n, typ = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        if typ == b"IHDR":
            w, h, depth, ctype, _, _, inter = struct.unpack(">IIBBBBB", body)
            assert depth == 8 and ctype in (2, 6) and inter == 0, (depth, ctype, inter)
        elif typ == b"IDAT":
            idat += body
        pos += 12 + n
    bpp = 3 if ctype == 2 else 4
    raw = zlib.decompress(idat)
    stride, prev, rows, i = w * bpp, bytearray(w * bpp), [], 0
    for _ in range(h):
        f, line, i = raw[i], bytearray(raw[i + 1:i + 1 + stride]), i + 1 + stride
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0
            b = prev[x]
            c = prev[x - bpp] if x >= bpp else 0
            if f == 1:
                line[x] = (line[x] + a) & 255
            elif f == 2:
                line[x] = (line[x] + b) & 255
            elif f == 3:
                line[x] = (line[x] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append([tuple(line[k:k + 3]) for k in range(0, stride, bpp)])
        prev = line
    return w, h, rows


class TestRamp(unittest.TestCase):
    def test_ramp_endpoints_and_grey(self):
        self.assertEqual(ramp.colour(1.0, 1.5, 10.0), ramp.RED)
        self.assertEqual(ramp.colour(1.5, 1.5, 10.0), ramp.RED)
        self.assertEqual(ramp.colour(10.0, 1.5, 10.0), ramp.BLUE)
        self.assertEqual(ramp.colour(None, 1.5, 10.0), ramp.GREY)
        mid = ramp.colour(5.75, 1.5, 10.0)
        self.assertAlmostEqual(mid[0], 0.5)
        self.assertAlmostEqual(mid[2], 0.5)
        self.assertEqual(ramp.colour(5.0, 5.0, 5.0), ramp.RED)   # everything at the threshold is red
        self.assertEqual(ramp.colour(9.0, 5.0, 5.0), ramp.BLUE)  # nothing thicker than min_ok exists to blend to
        self.assertEqual(ramp.colours([2.0, None, 20.0], 3.0), [ramp.RED, ramp.GREY, ramp.BLUE])


class TestThicknessMap(unittest.TestCase):
    def check(self, kernel):
        shape = stepped_block()
        faces, res = thickness.thickness_map(shape, kernel=kernel)
        self.assertEqual(len(faces), len(shape.Faces))
        known = [t for t in faces if t is not None]
        self.assertAlmostEqual(min(known), res["min"], delta=1e-12)
        self.assertAlmostEqual(res["min"], 2.0, delta=1e-6)
        self.assertEqual(faces[res["face"] - 1], res["min"])
        thin = [faces[i] for i, f in enumerate(shape.Faces) if abs(f.normalAt(0, 0).z) > 0.9 and f.CenterOfMass.x > 30]
        thick = [faces[i] for i, f in enumerate(shape.Faces) if abs(f.normalAt(0, 0).z) > 0.9 and f.CenterOfMass.x < 30]
        self.assertTrue(all(abs(t - 2.0) < 1e-6 for t in thin) and thin)
        self.assertTrue(all(abs(t - 20.0) < 1e-6 for t in thick) and thick)
        return faces, res

    def test_native_kernel(self):
        _, res = self.check("native")
        self.assertEqual(res["kernel"], "part-cpp/1")

    def test_python_reference_kernel_agrees_with_native(self):
        fn, _ = self.check("native")
        fp, res = self.check("python")
        self.assertEqual(res["kernel"], "python-raycast/1")
        for a, b in zip(fn, fp):
            self.assertEqual(a is None, b is None)
            if a is not None:
                self.assertAlmostEqual(a, b, delta=1e-4)


class TestRenderTool(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="acrender-")
        cls.paths = mc.save_fixtures(["box"], cls.tmp)
        cls.s = mc.make_server(stub=False)
        out, err = mc.call(cls.s, "open_document", path=cls.paths["box"])
        assert not err, out
        cls.doc = out["doc"]

    @classmethod
    def tearDownClass(cls):
        cls.s.close()

    def fake_render(self, fcstd, out_png, **kw):
        self.assertTrue(os.path.isfile(fcstd))
        png = (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
               + b"\0\0\0\0")
        with open(out_png, "wb") as fh:
            fh.write(png)
        self.calls.append((fcstd, out_png, kw))
        res = {"path": out_png, "view": kw["view"], "width": kw["width"], "height": kw["height"]}
        if kw.get("thickness_map"):
            res["thickness_map"] = {"min_ok": kw["thickness_map"]["min_ok"], "min": 2.0, "max": 20.0}
        return res

    def render(self, **args):
        resp = self.s.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": "render", "arguments": args}})
        return resp["result"]

    def setUp(self):
        self.calls = []

    def test_working_path_returns_path_and_image_content(self):
        with mock.patch.object(host, "availability", return_value=("r", "b")), \
                mock.patch.object(host, "render_file", side_effect=self.fake_render):
            res = self.render(doc=self.doc, view="top", width=200, height=100, thickness_map=True, min_ok=3.0)
        self.assertFalse(res["isError"], res)
        sc = res["structuredContent"]
        self.assertTrue(sc["path"].endswith(".png"))
        self.assertTrue(os.path.isfile(sc["path"]))
        self.assertEqual((sc["view"], sc["width"], sc["height"]), ("top", 200, 100))
        self.assertEqual(sc["thickness_map"]["min_ok"], 3.0)
        self.assertEqual([c["type"] for c in res["content"]], ["text", "image"])
        self.assertEqual(res["content"][1]["mimeType"], "image/png")
        self.assertEqual(base64.b64decode(res["content"][1]["data"])[:4], b"\x89PNG")
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(os.path.exists(self.calls[0][0]), "the snapshot is removed after the render")

    def test_file_argument_and_bad_arguments(self):
        with mock.patch.object(host, "availability", return_value=("r", "b")), \
                mock.patch.object(host, "render_file", side_effect=self.fake_render):
            res = self.render(file=self.paths["box"])
            self.assertFalse(res["isError"], res)
            self.assertEqual(self.calls[-1][0], self.paths["box"])
            for bad, code in ((dict(file=self.paths["box"], doc=self.doc), "bad_argument"),
                              (dict(file=os.path.join(self.tmp, "nope.FCStd")), "not_found"),
                              (dict(doc=self.doc, view="diagonal"), "bad_argument"),
                              (dict(doc=self.doc, path=self.paths["box"]), "bad_argument")):
                res = self.render(**bad)
                self.assertTrue(res["isError"], bad)
                self.assertEqual(res["structuredContent"]["error"], code, bad)

    def test_unavailable_when_disabled(self):
        with mock.patch.dict(os.environ, {"CADFORK_RENDER": "0"}):
            res = self.render(doc=self.doc)
        self.assertTrue(res["isError"])
        self.assertEqual(res["structuredContent"]["error"], "unavailable")
        self.assertIn("CADFORK_RENDER=0", res["structuredContent"]["message"])

    def test_unavailable_without_gui_build(self):
        with mock.patch.dict(os.environ, {"CADFORK_GUI_BUILD_DIR": self.tmp}):
            res = self.render(doc=self.doc)
        self.assertTrue(res["isError"])
        self.assertEqual(res["structuredContent"]["error"], "unavailable")
        self.assertIn("no GUI build", res["structuredContent"]["message"])

    def test_gui_failure_is_a_clean_error(self):
        with mock.patch.object(host, "availability", return_value=("r", "b")), \
                mock.patch.object(host, "render_file", side_effect=host.RenderFailed("boom", returncode=1)):
            res = self.render(doc=self.doc)
        self.assertTrue(res["isError"])
        self.assertEqual(res["structuredContent"]["error"], "render_failed")
        out, err = mc.call(self.s, "list_documents")   # the server keeps serving
        self.assertFalse(err)


def _gui_here():
    try:
        host.availability(mc.freecadcmd())
        return True
    except host.RenderUnavailable:
        return False


@unittest.skipUnless(_gui_here(), "no GUI build or display for the real render")
class TestRealGuiRender(unittest.TestCase):
    """One real launch of the GUI (about 20 s): a stepped block rendered as a thickness map."""

    def test_thickness_map_png_has_red_thin_faces_and_blue_thick_faces(self):
        with tempfile.TemporaryDirectory(prefix="acrender-") as d:
            doc = FreeCAD.newDocument("stepped")
            try:
                o = doc.addObject("Part::Feature", "Stepped")
                o.Shape = stepped_block()
                doc.recompute()
                fcstd = os.path.join(d, "stepped.FCStd")
                doc.saveAs(fcstd)
            finally:
                FreeCAD.closeDocument(doc.Name)
            out = os.path.join(d, "tm.png")
            res = host.render_file(fcstd, out, view="iso", width=480, height=360,
                                   thickness_map={"min_ok": 3.0, "samples": 5}, freecadcmd=mc.freecadcmd())
            self.assertEqual(res["path"], out)
            self.assertAlmostEqual(res["thickness_map"]["min"], 2.0, delta=1e-6)
            self.assertGreaterEqual(res["thickness_map"]["max"], 20.0)
            w, h, rows = read_png(out)
            self.assertEqual((w, h), (480, 360))
            px = [p for r in rows for p in r]
            red = sum(1 for r, g, b in px if r > 150 and g < 90 and b < 90)
            blue = sum(1 for r, g, b in px if b > 100 and r < 90 and g < 90)
            self.assertGreater(red, 500)
            self.assertGreater(blue, 500)
            self.assertFalse(os.path.exists(out + ".json"))
