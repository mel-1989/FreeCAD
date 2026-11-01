# SPDX-License-Identifier: LGPL-2.1-or-later
"""Vault fixture for the BOM and diff tests (G03 slice 6a): a machine assembly of three levels.

Documents (all vaulted, built in this process, nothing here is a test)::

    bracket   one PartDesign body, 20 x 10 x 10 (Pad length 10), Material "Steel S235"
    frame     a weldment: box frame of 12 members in 3 cut-list lines (EN SHS), from the Weldment test frames
    machine   Direct  -> link to bracket                                  (level 1)
              FrameLink -> link to frame's members                        (level 1, a weldment row with its cut list)
              Sub (App::Part, used twice through links SubA and SubB)     (level 1, quantity 2)
                  Plate  a local body 30 x 10 x 5, Material "Aluminium 6061"  (level 2)
                  Bolts  link array of bracket, ElementCount 4                (level 2, quantity 4)

``revisions()`` then adds, in this order: bracket A.2 (Pad length 15), machine A.2 (a third link to Sub, a new
sub-assembly Cover with one local body CoverPlate), bracket A.3 (Body relabelled "Renamed").
"""

import os

import FreeCAD as App
import Part  # noqa: F401
import Sketcher  # noqa: F401

from PDM import vault as V

try:
    from WTests import frames
except Exception:  # the Weldment tests are not installed
    frames = None


def pad_body(doc, name, length, width=20.0, height=10.0, material=None):
    body = doc.addObject("PartDesign::Body", name)
    sk = body.newObject("Sketcher::SketchObject", name + "Sketch")
    xy = [o for o in body.Origin.OriginFeatures if o.Role == "XY_Plane"][0]
    sk.AttachmentSupport = [(xy, "")]
    sk.MapMode = "FlatFace"
    pts = [(0, 0), (width, 0), (width, height), (0, height)]
    for i in range(4):
        a, b = pts[i], pts[(i + 1) % 4]
        sk.addGeometry(Part.LineSegment(App.Vector(a[0], a[1], 0), App.Vector(b[0], b[1], 0)))
    pad = body.newObject("PartDesign::Pad", name + "Pad")
    pad.Profile = sk
    pad.Length = length
    if material:
        body.addProperty("App::PropertyString", "Material", "Base")
        body.Material = material
    return body


def close_all():
    for n in list(App.listDocuments()):
        App.closeDocument(n)


class Fixture:
    def __init__(self, tmp, weldment=True):
        self.tmp = tmp = os.path.realpath(tmp)  # /var is a symlink on macOS; link paths are made relative to the real path
        self.weldment = weldment and frames is not None
        self.vault_path = V.Vault.create(os.path.join(tmp, "vault.git"))
        self.v = V.Vault.clone(self.vault_path, os.path.join(tmp, "wc-alice"), "alice")

    def _edit(self, name, fn, message):
        self.v.checkout(name)
        doc = App.openDocument(self.v.doc_path(name))
        fn(doc)
        doc.recompute()
        doc.save()
        rev = self.v.checkin(name, message)["revision"]
        close_all()
        return rev

    def build(self):
        v = self.v
        d = App.newDocument("bracket")
        pad_body(d, "Body", 10.0, material="Steel S235")
        d.recompute()
        d.saveAsFolder(v.doc_path("bracket"))
        close_all()
        v.add_document("bracket")
        if self.weldment:
            d = App.newDocument("frame")
            frames.box_frame(d)
            d.recompute()
            d.saveAsFolder(v.doc_path("frame"))
            close_all()
            v.add_document("frame")
        bracket = App.openDocument(v.doc_path("bracket"))
        frame = App.openDocument(v.doc_path("frame")) if self.weldment else None
        m = App.newDocument("machine")
        m.saveAsFolder(v.doc_path("machine"))
        plate = pad_body(m, "Plate", 5.0, width=30.0, height=10.0, material="Aluminium 6061")
        sub = m.addObject("App::Part", "Sub")
        sub.addObject(plate)
        bolts = sub.newObject("App::Link", "Bolts")
        bolts.LinkedObject = bracket.getObject("Body")
        bolts.ElementCount = 4
        for n in ("SubA", "SubB"):
            m.addObject("App::Link", n).LinkedObject = sub
        m.addObject("App::Link", "Direct").LinkedObject = bracket.getObject("Body")
        if frame is not None:
            m.addObject("App::Link", "FrameLink").LinkedObject = frame.getObject("Cols")
        m.recompute()
        m.save()
        close_all()
        v.add_document("machine")

    def revisions(self):
        """Bracket A.2 (Pad 15), machine A.2 (third Sub link, Cover), bracket A.3 (relabelled). Returns labels."""
        out = {}
        out["bracket2"] = self._edit("bracket", lambda d: setattr(d.getObject("BodyPad"), "Length", 15.0), "longer")

        def cover(doc):
            c = doc.addObject("App::Part", "Cover")
            c.addObject(pad_body(doc, "CoverPlate", 2.0, width=40.0, height=40.0, material="Polycarbonate"))
            doc.addObject("App::Link", "CoverLink").LinkedObject = c
            doc.addObject("App::Link", "SubC").LinkedObject = doc.getObject("Sub")
        out["machine2"] = self._edit("machine", cover, "cover and a third sub-assembly")
        out["bracket3"] = self._edit("bracket", lambda d: setattr(d.getObject("Body"), "Label", "Renamed"), "relabel")
        return out
