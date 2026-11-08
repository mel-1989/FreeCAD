# SPDX-License-Identifier: LGPL-2.1-or-later

# G12 slices 2a and 2b: the 3D display of PMI items (checks 17 to 24 of briefs/phase1/G12-pmi.md).
#
# Behaviour follows the CAx-IF Recommended Practices for the Representation and Presentation of PMI (AP242): the
# presentation geometry of an item is drawn as authored, in the coordinates of the source file, and moves with the object
# the item governs; slice 2b lays out items with no presentation as a text label at the faces they govern (ISO 1101 /
# ASME Y14.5 symbols from TechDraw's bundled Y14.5-FreeCAD font). The synthetic case is always run; the NIST corpus (never committed) is used when it is present.
#
# Run: FreeCAD -t TestPartPmiGui   (needs the GUI; a scratch FREECAD_USER_HOME, see hard rule 12 of cad-fork)

import glob
import os
import shutil
import tempfile
import time
import unittest
import zipfile

import FreeCAD as App
import FreeCADGui as Gui
import Import
import Part

from pivy import coin

CORPUS = os.environ.get(
    "PMI_CORPUS", os.path.expanduser("~/src/cad-fork/scratch/r068/corpus/nist/NIST-PMI-STEP-Files")
)
CORPUS_FILES = sorted(glob.glob(os.path.join(CORPUS, "*ap242*.stp")))
SELECT_FILES = ("nist_ctc_01_asme1_ap242-e1", "nist_ftc_06_asme1_ap242-e2", "nist_stc_06_asme1_ap242-e3")


# ---------------------------------------------------------------------------------------------------------------
# scene graph helpers (ruling 2 of the slice 2 brief: drawn items are SoSeparators named PMI_<object name>)
def scene_of(doc):
    return Gui.getDocument(doc.Name).ActiveView.getSceneGraph()


def drawn_paths(doc):
    """name -> SoPath of every separator named PMI_*, hidden ones included"""
    search = coin.SoSearchAction()
    search.setType(coin.SoSeparator.getClassTypeId())
    search.setInterest(coin.SoSearchAction.ALL)
    search.setSearchingAll(True)
    search.apply(scene_of(doc))
    paths = search.getPaths()
    out = {}
    for i in range(paths.getLength()):
        p = paths[i]
        name = p.getTail().getName().getString()
        if name.startswith("PMI_"):
            out[name[4:]] = p.copy()  # the action frees its paths with itself
    return out


def is_visible(path):
    """True when every switch on the path selects the branch the path takes"""
    for i in range(path.getLength() - 1):
        node = path.getNode(i)
        if node.isOfType(coin.SoSwitch.getClassTypeId()):
            which = node.whichChild.getValue()
            if which == coin.SO_SWITCH_ALL:
                continue
            if which != path.getIndex(i + 1):
                return False
    return True


def visible_names(doc):
    return sorted(n for n, p in drawn_paths(doc).items() if is_visible(p))


def bbox_of(path):
    act = coin.SoGetBoundingBoxAction(coin.SbViewportRegion())
    act.apply(path)
    box = act.getBoundingBox()
    if box.isEmpty():
        return None
    lo, hi = box.getMin().getValue(), box.getMax().getValue()
    return tuple(lo) + tuple(hi)


def count_vertices(path):
    """Number of vertices in the item's coordinate node, and how many line and face indices it draws"""
    search = coin.SoSearchAction()
    search.setType(coin.SoCoordinate3.getClassTypeId())
    search.apply(path.getTail())
    n = search.getPath().getTail().point.getNum() if search.getPath() else 0
    return n


def semantic_items(doc):
    return [o for o in doc.Objects if o.TypeId.startswith("Part::Pmi") and o.TypeId != "Part::PmiGroup"]


def expected_presented(doc):
    """Check 17's count: items with a Presentation, annotations, datums once per letter per governed object"""
    n = 0
    seen = set()
    for o in semantic_items(doc):
        if o.Presentation.isNull():
            continue
        if o.TypeId == "Part::PmiDatum":
            body = o.References[0][0].Name if o.References else None
            if (body, o.Letter) in seen:
                continue
            seen.add((body, o.Letter))
        n += 1
    return n


def _datum_key(o):
    return (o.References[0][0].Name if o.References else None, o.Letter)


def expected_fallback(doc):
    """Check 21: attached dimensions and tolerances without a Presentation, and datum letters with no presented use
    on their object (one per letter per object)"""
    presented = {_datum_key(o) for o in semantic_items(doc) if o.TypeId == "Part::PmiDatum" and not o.Presentation.isNull()}
    seen = set()
    n = 0
    for o in semantic_items(doc):
        if not o.Presentation.isNull() or o.TypeId == "Part::PmiAnnotation":
            continue
        if o.Unattached or not o.References:
            continue
        if o.TypeId == "Part::PmiDatum":
            key = _datum_key(o)
            if key in presented or key in seen:
                continue
            seen.add(key)
        n += 1
    return n


def expected_total(doc):
    """Every drawn item: check 17's count plus the fallback labels"""
    return expected_presented(doc) + expected_fallback(doc)


def semantic_count(doc):
    """Dimensions, tolerances and datum letters (one per letter per object) plus the annotations"""
    keys = set()
    n = 0
    for o in semantic_items(doc):
        if o.TypeId == "Part::PmiDatum":
            keys.add(_datum_key(o))
        else:
            n += 1
    return n + len(keys)


def label_text(path):
    """The text of a fallback label (empty for a presentation item), from the item's SoInfo"""
    search = coin.SoSearchAction()
    search.setType(coin.SoInfo.getClassTypeId())
    search.apply(path.getTail())
    p = search.getPath()
    if p is None:
        return ""
    node = p.getTail()
    return str(node.string.getValue())


def pick_proxy_quad(path):
    """The four corners of the invisible pick quad of a drawn item"""
    search = coin.SoSearchAction()
    search.setType(coin.SoCoordinate3.getClassTypeId())
    search.setInterest(coin.SoSearchAction.ALL)
    search.apply(path.getTail())
    paths = search.getPaths()
    if paths.getLength() < 2:
        return None
    node = paths[1].getTail()
    if node.point.getNum() < 4:
        return None
    return [App.Vector(*node.point[i].getValue()) for i in range(4)]


def model_diagonal(doc):
    diag = 0.0
    box = None
    for o in doc.Objects:
        if o.TypeId.startswith("Part::Pmi") or not hasattr(o, "Shape") or o.Shape.isNull():
            continue
        bb = o.Shape.BoundBox
        if not bb.isValid() or bb.DiagonalLength > 1e9:  # an empty container reports an unbounded box
            continue
        if box is None:
            box = App.BoundBox(bb)
        else:
            box.add(bb)
    return box.DiagonalLength if box else 0.0


HIGHLIGHT = (1.0, 0.55, 0.0)


def body_materials(obj):
    """Diffuse colour arrays of every SoMaterial under the body's view provider root (the scene-graph state)"""
    search = coin.SoSearchAction()
    search.setType(coin.SoMaterial.getClassTypeId())
    search.setInterest(coin.SoSearchAction.ALL)
    search.setSearchingAll(True)
    search.apply(vp_of(obj).RootNode)
    out = []
    paths = search.getPaths()
    for i in range(paths.getLength()):
        m = paths[i].getTail()
        out.append(tuple(tuple(round(c, 5) for c in m.diffuseColor[j].getValue()) for j in range(m.diffuseColor.getNum())))
    return out


def highlighted_faces(obj):
    """1-based face numbers whose scene-graph diffuse colour is the highlight colour"""
    faces = set()
    for arr in body_materials(obj):
        for j, c in enumerate(arr):
            if len(arr) > 1 and all(abs(a - b) < 1e-4 for a, b in zip(c, HIGHLIGHT)):
                faces.add(j + 1)
    return faces


def body_colour_properties(obj):
    v = vp_of(obj)
    return (str(v.ShapeAppearance), str(v.ShapeColor), v.Transparency)


def gui_xml_without_camera(path):
    import re

    with zipfile.ZipFile(path) as z:
        text = z.read("GuiDocument.xml").decode("utf-8")
    # the colour properties of every view provider (tree rank and camera legitimately differ between two saves)
    props = re.findall(r'<Property name="(?:ShapeAppearance|ShapeMaterial|ShapeColor|Transparency|DiffuseColor|LineColor|PointColor)".*?</Property>', text, flags=re.S)
    assert props, "no colour property found in GuiDocument.xml"
    return "\n".join(props)


def reference_faces(item):
    out = {}
    for obj, subs in item.References:
        out.setdefault(obj.Name, set()).update(int(x[4:]) for x in subs if x.startswith("Face"))
    return out


def group_of(doc):
    groups = [o for o in doc.Objects if o.TypeId == "Part::PmiGroup"]
    return groups[0] if groups else None


def vp_of(obj):
    return Gui.getDocument(obj.Document.Name).getObject(obj.Name)


# ---------------------------------------------------------------------------------------------------------------
# the synthetic case: always present, no NIST file
def polyline(*pts):
    return Part.makePolygon([App.Vector(*p) for p in pts])


def make_synthetic(name="PmiSyn", body_placement=None):
    doc = App.newDocument(name)
    box = doc.addObject("Part::Feature", "Body")
    box.Shape = Part.makeBox(10, 20, 30)
    if body_placement is not None:
        box.Placement = body_placement
    other = doc.addObject("Part::Feature", "Other")
    other.Shape = Part.makeBox(5, 5, 5, App.Vector(100, 0, 0))
    doc.recompute()
    grp = doc.addObject("Part::PmiGroup", "PMI")
    items = []

    def add(typeid, label, presentation, body=None, face=None, **props):
        o = doc.addObject(typeid, label)
        for k, v in props.items():
            setattr(o, k, v)
        if presentation is not None:
            o.Presentation = presentation
        if body is not None:
            o.References = [(body, [face])]
            o.ReferencePlacement = body.Placement
        items.append(o)
        return o

    # a dimension: a leader line, an extension line and an arrow head face
    arrow = Part.Face(Part.makePolygon([App.Vector(0, 0, 40), App.Vector(2, 1, 40), App.Vector(2, -1, 40), App.Vector(0, 0, 40)]))
    dim = add("Part::PmiDimension", "Dim", Part.Compound([polyline((0, 0, 40), (10, 0, 40)), polyline((0, 0, 30), (0, 0, 40)), arrow]),
              box, "Face1", PmiType="Size_LinearDistance", Value=10.0)
    tol = add("Part::PmiTolerance", "Tol", Part.Compound([polyline((0, 10, 30), (10, 10, 30), (10, 10, 35))]), box, "Face6",
              PmiType="Flatness", Value=0.1)
    add("Part::PmiTolerance", "TolNoPres", None, box, "Face2", PmiType="Position", Value=0.2)
    d1 = add("Part::PmiDatum", "DatumA1", Part.Compound([polyline((0, 0, 0), (0, -5, 0), (3, -5, 0))]), box, "Face5", Letter="A")
    d2 = add("Part::PmiDatum", "DatumA2", Part.Compound([polyline((10, 0, 0), (10, -5, 0), (13, -5, 0))]), box, "Face5", Letter="A")
    d3 = add("Part::PmiDatum", "DatumB1", Part.Compound([polyline((0, 20, 0), (0, 25, 0))]), box, "Face3", Letter="B")
    d4 = add("Part::PmiDatum", "DatumA3", Part.Compound([polyline((100, 0, 0), (100, -5, 0))]), other, "Face5", Letter="A")
    ann = add("Part::PmiAnnotation", "Callout", Part.Compound([polyline((0, 20, 30), (-5, 25, 35))]), PmiType="CommonLabel")
    grp.addObjects(items)
    doc.recompute()
    return doc, dict(box=box, other=other, dim=dim, tol=tol, datums=[d1, d2, d3, d4], ann=ann, group=grp, items=items)


class TestPmiDisplaySynthetic(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pmigui")
        self.doc, self.s = make_synthetic()
        Gui.updateGui()

    def tearDown(self):
        Gui.Selection.clearSelection()
        for name in list(App.listDocuments()):
            App.closeDocument(name)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_drawn_count_datums_once_per_letter_per_body(self):
        # dim, tol, annotation, A and B on Body, A on Other = 6, and the tolerance without a Presentation draws a
        # fallback label (slice 2b); DatumA2 is a second use of A on Body and draws nothing
        names = sorted(drawn_paths(self.doc))
        self.assertEqual(names, sorted(["Dim", "Tol", "TolNoPres", "Callout", "DatumA1", "DatumB1", "DatumA3"]))
        self.assertEqual(visible_names(self.doc), names)
        self.assertEqual(len(names), expected_total(self.doc))

    def test_every_drawn_item_has_geometry_within_twice_the_model_diagonal(self):
        diag = model_diagonal(self.doc)
        self.assertGreater(diag, 0)
        for name, path in drawn_paths(self.doc).items():
            self.assertGreaterEqual(count_vertices(path), 2, name)
            box = bbox_of(path)
            self.assertIsNotNone(box, name)
            extent = ((box[3] - box[0]) ** 2 + (box[4] - box[1]) ** 2 + (box[5] - box[2]) ** 2) ** 0.5
            self.assertLessEqual(extent, 2 * diag, name)

    def test_selecting_an_item_highlights_its_faces_and_selects_only_the_item(self):
        body = self.s["box"]
        base_scene = body_materials(body)
        base_props = body_colour_properties(body)
        for item in self.s["items"]:
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(item)
            self.assertEqual([o.Name for o in Gui.Selection.getSelection(self.doc.Name)], [item.Name])
            self.assertEqual([o.Object.Name for o in Gui.Selection.getSelectionEx(self.doc.Name)], [item.Name])
            want = reference_faces(item).get(body.Name, set())
            self.assertEqual(highlighted_faces(body), want, item.Name)
            self.assertEqual(body_colour_properties(body), base_props)  # no property changes
            Gui.Selection.clearSelection()
            self.assertEqual(highlighted_faces(body), set())
            self.assertEqual(body_materials(body), base_scene, item.Name)

    def test_two_items_union_and_deselect_one(self):
        body = self.s["box"]
        d, t = self.s["dim"], self.s["tol"]
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(d)
        Gui.Selection.addSelection(t)
        self.assertEqual(highlighted_faces(body), {1, 6})
        Gui.Selection.removeSelection(d)
        self.assertEqual(highlighted_faces(body), {6})
        Gui.Selection.clearSelection()
        self.assertEqual(highlighted_faces(body), set())

    def test_deleting_the_selected_item_restores_the_colours(self):
        body = self.s["box"]
        base_scene = body_materials(body)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.s["dim"])
        self.assertEqual(highlighted_faces(body), {1})
        self.doc.removeObject(self.s["dim"].Name)
        Gui.updateGui()
        self.assertEqual(highlighted_faces(body), set())
        self.assertEqual(body_materials(body), base_scene)

    def test_saving_while_an_item_is_selected_writes_no_changed_colours(self):
        a = os.path.join(self.tmp, "a.FCStd")
        b = os.path.join(self.tmp, "b.FCStd")
        self.doc.saveAs(a)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.s["dim"])
        self.assertEqual(highlighted_faces(self.s["box"]), {1})
        self.doc.saveAs(b)
        self.assertEqual(gui_xml_without_camera(a), gui_xml_without_camera(b))

    def test_unattached_items_select_alone(self):
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.s["ann"])
        self.assertEqual([o.Object.Name for o in Gui.Selection.getSelectionEx(self.doc.Name)], ["Callout"])
        self.assertEqual(highlighted_faces(self.s["box"]), set())

    def _two_body_doc(self):
        App.closeDocument(self.doc.Name)
        doc = App.newDocument("PmiTwo")
        b1 = doc.addObject("Part::Feature", "B1")
        b1.Shape = Part.makeBox(10, 10, 10)
        b1.Placement = App.Placement(App.Vector(30, 0, 0), App.Rotation())
        b2 = doc.addObject("Part::Feature", "B2")
        b2.Shape = Part.makeBox(10, 10, 10)
        b2.Placement = App.Placement(App.Vector(0, 70, 0), App.Rotation(App.Vector(0, 0, 1), 90))
        grp = doc.addObject("Part::PmiGroup", "PMI")
        dim = doc.addObject("Part::PmiDimension", "Dim")
        dim.Presentation = Part.Compound([polyline((30, 0, 20), (0, 70, 20))])
        dim.References = [(b2, ["Face1"]), (b1, ["Face1"])]  # the item governs both; References[0] decides what it follows
        first = dim.References[0][0]
        dim.ReferencePlacement = first.Placement
        grp.addObjects([dim])
        doc.recompute()
        Gui.updateGui()
        return doc, b1, b2, dim, first

    def test_item_referencing_two_bodies_follows_the_first_reference_only(self):
        doc, b1, b2, dim, first = self._two_body_doc()
        other = b1 if first is b2 else b2
        before = bbox_of(drawn_paths(doc)["Dim"])
        # placement of the first reference moves it, the other body does not
        other.Placement = App.Placement(other.Placement.Base + App.Vector(10, 0, 0), other.Placement.Rotation)
        Gui.updateGui()
        same = bbox_of(drawn_paths(doc)["Dim"])
        for i in range(6):
            self.assertAlmostEqual(same[i], before[i], delta=1e-4)
        first.Placement = App.Placement(first.Placement.Base + App.Vector(10, 0, 0), first.Placement.Rotation)
        Gui.updateGui()
        moved = bbox_of(drawn_paths(doc)["Dim"])
        self.assertAlmostEqual(moved[0] - before[0], 10.0, delta=1e-4)
        self.assertAlmostEqual(moved[3] - before[3], 10.0, delta=1e-4)

    def test_old_document_with_two_bodies_draws_in_place(self):
        doc, b1, b2, dim, first = self._two_body_doc()
        before = bbox_of(drawn_paths(doc)["Dim"])
        path = os.path.join(self.tmp, "two.FCStd")
        doc.saveAs(path)
        App.closeDocument(doc.Name)
        doc = App.openDocument(self._strip_reference_placement(path))
        Gui.updateGui()
        after = bbox_of(drawn_paths(doc)["Dim"])
        for i in range(6):
            self.assertAlmostEqual(after[i], before[i], delta=1e-4)
        d = doc.getObject("Dim")
        self.assertEqual(d.ReferencePlacement.Base, d.References[0][0].Placement.Base)

    def test_old_document_with_a_body_at_a_non_identity_placement_draws_in_place(self):
        # check 23 repair: ReferencePlacement missing from the file (not just identity) is set to the body's current
        # global placement on restore, so the drawing does not move
        App.closeDocument(self.doc.Name)
        doc, s = make_synthetic("PmiOld", App.Placement(App.Vector(50, 0, 0), App.Rotation()))
        Gui.updateGui()
        before = {n: bbox_of(p) for n, p in drawn_paths(doc).items()}
        path = os.path.join(self.tmp, "oldp.FCStd")
        doc.saveAs(path)
        App.closeDocument(doc.Name)
        stripped = self._strip_reference_placement(path)
        doc = App.openDocument(stripped)
        Gui.updateGui()
        after = {n: bbox_of(p) for n, p in drawn_paths(doc).items()}
        self.assertEqual(sorted(after), sorted(before))
        for n in before:
            for i in range(6):
                self.assertAlmostEqual(after[n][i], before[n][i], delta=1e-4, msg=n)
        dim = doc.getObject("Dim")
        self.assertAlmostEqual(dim.ReferencePlacement.Base.x, 50.0, delta=1e-9)

    def test_item_follows_its_body_and_only_its_body(self):
        before = {n: bbox_of(p) for n, p in drawn_paths(self.doc).items()}
        body = self.s["box"]
        self.doc.openTransaction("move")
        body.Placement = App.Placement(App.Vector(10, 0, 0), App.Rotation())
        self.doc.commitTransaction()
        Gui.updateGui()
        governed = {o.Name for o in self.s["items"] if o.References and o.References[0][0].Name == body.Name}
        after = {n: bbox_of(p) for n, p in drawn_paths(self.doc).items()}
        for n in before:
            shift = [after[n][i] - before[n][i] for i in range(6)]
            if n in governed:
                for i in (0, 3):
                    self.assertAlmostEqual(shift[i], 10.0, delta=1e-4, msg=n)
                for i in (1, 2, 4, 5):
                    self.assertAlmostEqual(shift[i], 0.0, delta=1e-4, msg=n)
            else:
                for i in range(6):
                    self.assertAlmostEqual(shift[i], 0.0, delta=1e-6, msg=n)
        self.doc.recompute()
        again = {n: bbox_of(p) for n, p in drawn_paths(self.doc).items()}
        for n in after:
            for i in range(6):
                self.assertAlmostEqual(again[n][i], after[n][i], delta=1e-6, msg=n)
        self.doc.undo()
        Gui.updateGui()
        restored = {n: bbox_of(p) for n, p in drawn_paths(self.doc).items()}
        for n in before:
            for i in range(6):
                self.assertAlmostEqual(restored[n][i], before[n][i], delta=1e-4, msg=n)

    def test_rotation_of_the_body_rotates_the_drawing(self):
        item = self.s["dim"]
        path = drawn_paths(self.doc)[item.Name]
        before = bbox_of(path)
        self.s["box"].Placement = App.Placement(App.Vector(0, 0, 0), App.Rotation(App.Vector(0, 0, 1), 90))
        Gui.updateGui()
        after = bbox_of(drawn_paths(self.doc)[item.Name])
        # a 90 degree turn about z swaps the x and y extents
        self.assertAlmostEqual(after[4] - after[1], before[3] - before[0], delta=1e-3)

    def test_item_whose_body_is_not_found_draws_where_it_was_imported(self):
        ann = drawn_paths(self.doc)["Callout"]
        before = bbox_of(ann)
        self.s["box"].Placement = App.Placement(App.Vector(50, 0, 0), App.Rotation())
        Gui.updateGui()
        self.assertEqual(bbox_of(drawn_paths(self.doc)["Callout"]), before)

    def test_group_and_item_visibility(self):
        total = visible_names(self.doc)
        group_vp = vp_of(self.s["group"])
        group_vp.Visibility = False
        self.assertEqual(visible_names(self.doc), [])
        group_vp.Visibility = True
        self.assertEqual(visible_names(self.doc), total)
        vp_of(self.s["dim"]).Visibility = False
        self.assertEqual(visible_names(self.doc), sorted(set(total) - {"Dim"}))
        vp_of(self.s["dim"]).Visibility = True
        self.assertEqual(visible_names(self.doc), total)

    def _reopen(self, doc):
        path = os.path.join(self.tmp, "syn.FCStd")
        doc.saveAs(path)
        App.closeDocument(doc.Name)
        return App.openDocument(path), path

    def test_visibility_survives_save_and_reopen(self):
        vp_of(self.s["tol"]).Visibility = False
        doc, _ = self._reopen(self.doc)
        Gui.updateGui()
        self.assertEqual(visible_names(doc), sorted(set(["Dim", "TolNoPres", "Callout", "DatumA1", "DatumB1", "DatumA3"])))
        vp_of(group_of(doc)).Visibility = False
        doc, _ = self._reopen(doc)
        Gui.updateGui()
        self.assertEqual(visible_names(doc), [])
        vp_of(group_of(doc)).Visibility = True  # showing a group shows its members (App::GroupExtension)
        self.assertEqual(visible_names(doc), sorted(["Dim", "Tol", "TolNoPres", "Callout", "DatumA1", "DatumB1", "DatumA3"]))

    def _strip_reference_placement(self, path):
        import re

        old = path[:-6] + "_stripped.FCStd"

        with zipfile.ZipFile(path) as zin, zipfile.ZipFile(old, "w", zipfile.ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                data = zin.read(info.filename)
                if info.filename == "Document.xml":
                    text = data.decode("utf-8")
                    removed = [0]

                    def drop(m):
                        body, n = re.subn(r'\s*<Property name="ReferencePlacement".*?</Property>', "", m.group(2), flags=re.S)
                        removed[0] += n
                        return '<Properties Count="%d"%s</Properties>' % (int(m.group(1)) - n, body)

                    text = re.sub(r'<Properties Count="(\d+)"(.*?)</Properties>', drop, text, flags=re.S)
                    self.assertGreater(removed[0], 0)
                    data = text.encode("utf-8")
                zout.writestr(info, data)
        return old

    def test_document_without_reference_placement_opens_and_draws(self):
        # a document saved before this slice has no ReferencePlacement in its Document.xml
        path = os.path.join(self.tmp, "old.FCStd")
        self.doc.saveAs(path)
        App.closeDocument(self.doc.Name)
        old = self._strip_reference_placement(path)
        doc = App.openDocument(old)
        Gui.updateGui()
        self.assertEqual(len(visible_names(doc)), 7)
        self.assertEqual(len(visible_names(doc)), expected_total(doc))


# ---------------------------------------------------------------------------------------------------------------
# slice 2b: fallback layout (check 21) and picking in the view (check 22)
def face_anchor(doc, item, diag):
    """Where check 21 puts the label of an item: the centre of the bounding box of its governed faces, offset along the
    outward normal of the first by 5% of the model diagonal (global coordinates, computed from the Part API)"""
    obj, subs = item.References[0]
    box = None
    first = None
    for sub in subs:
        if not sub.startswith("Face"):
            continue
        face = obj.Shape.getElement(sub)
        if first is None:
            first = face
        box = App.BoundBox(face.BoundBox) if box is None else box.add(face.BoundBox) or box
    if first is None:
        return None
    u0, u1, v0, v1 = first.ParameterRange
    n = first.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
    n.normalize()
    return box.Center + n * (0.05 * diag), n


class TestPmiFallbackSynthetic(unittest.TestCase):
    def setUp(self):
        self.doc, self.s = make_synthetic("PmiFb")
        doc, box = self.doc, self.s["box"]

        other = self.s["other"]  # the small box: one face each, so no two labels sit on the same face

        def add(typeid, label, face, **props):
            o = doc.addObject(typeid, label)
            for k, v in props.items():
                setattr(o, k, v)
            if face:
                o.References = [(other, [face])]
                o.ReferencePlacement = other.Placement
            self.s["group"].addObject(o)
            return o

        self.s["dimfb"] = add("Part::PmiDimension", "DimFb", "Face1", PmiType="Size_Diameter", Value=12.7, HasBounds=True,
                              BoundsKind="plus_minus", UpperBound=0.05, LowerBound=0.0)
        self.s["rangefb"] = add("Part::PmiDimension", "DimRange", "Face2", PmiType="Size_Linear", Value=10.0, HasBounds=True,
                                BoundsKind="range", UpperBound=10.2, LowerBound=9.9)
        self.s["classfb"] = add("Part::PmiDimension", "DimClass", "Face4", PmiType="Size_Radius", Value=3.5,
                                BoundsKind="class", ToleranceClass="H7")
        self.s["tolfb"] = add("Part::PmiTolerance", "TolFb", "Face3", PmiType="Position", Value=0.635, ZoneShape="Diameter",
                              Modifiers=["MAXIMUM_MATERIAL_REQUIREMENT"], DatumReferences=["C", "A", "B"])
        self.s["datumfb"] = add("Part::PmiDatum", "DatumC", "Face6", Letter="C")
        self.s["datumfb2"] = add("Part::PmiDatum", "DatumC2", "Face6", Letter="C")
        self.s["loose"] = add("Part::PmiDimension", "Loose", None, PmiType="Location_LinearDistance", Value=5.0, Unattached=True)
        doc.recompute()
        Gui.updateGui()

    def tearDown(self):
        Gui.Selection.clearSelection()
        for name in list(App.listDocuments()):
            App.closeDocument(name)

    def test_text_of_each_fallback_item(self):
        paths = drawn_paths(self.doc)
        want = {
            "TolNoPres": "\u2316 | 0.200",
            "DimFb": "\u230012.700 +0.050/-0.000",
            "DimRange": "10.000 (9.900 to 10.200)",
            "DimClass": "R3.500 H7",
            "TolFb": "\u2316 | \u23000.635 \u24c2 | C | A | B",
            "DatumC": "[C]",
        }
        for name, text in want.items():
            self.assertIn(name, paths, name)
            self.assertEqual(label_text(paths[name]), text, name)
        # presentation items carry no label text; a second use of a letter, an unattached item and annotations draw nothing
        # fallback
        for name in ("Dim", "Tol", "Callout", "DatumA1"):
            self.assertEqual(label_text(paths[name]), "", name)
        self.assertNotIn("DatumC2", paths)
        self.assertNotIn("Loose", paths)

    def test_counts(self):
        self.assertEqual(len(drawn_paths(self.doc)), expected_total(self.doc))
        # TolNoPres, DimFb, DimRange, DimClass, TolFb and DatumC (the first use of C); Loose is unattached, DatumC2 is a
        # second use
        self.assertEqual(expected_fallback(self.doc), 6)
        fb = [n for n, p in drawn_paths(self.doc).items() if label_text(p)]
        self.assertEqual(len(fb), expected_fallback(self.doc))

    def test_label_is_at_the_faces_centre_offset_5_percent_along_the_normal(self):
        diag = model_diagonal(self.doc)
        paths = drawn_paths(self.doc)
        for key in ("dimfb", "tolfb", "datumfb", "rangefb"):
            item = self.s[key]
            anchor, n = face_anchor(self.doc, item, diag)
            box = bbox_of(paths[item.Name])
            centre = App.Vector((box[0] + box[3]) / 2, (box[1] + box[4]) / 2, (box[2] + box[5]) / 2)
            self.assertLess((centre - anchor).Length, 0.01, item.Name)
            # the label lies in the plane through the anchor perpendicular to the normal
            for corner in pick_proxy_quad(paths[item.Name]):
                self.assertAlmostEqual((corner - anchor).dot(n), 0.0, delta=1e-3, msg=item.Name)

    def test_label_follows_its_body(self):
        paths = drawn_paths(self.doc)
        before = bbox_of(paths["DimFb"])
        self.s["other"].Placement = App.Placement(App.Vector(10, 0, 0), App.Rotation())
        Gui.updateGui()
        after = bbox_of(drawn_paths(self.doc)["DimFb"])
        self.assertAlmostEqual(after[0] - before[0], 10.0, delta=1e-4)
        self.assertAlmostEqual(after[4] - before[4], 0.0, delta=1e-4)

    def test_geometry_is_line_geometry_inside_twice_the_diagonal(self):
        diag = model_diagonal(self.doc)
        for key in ("dimfb", "tolfb", "datumfb"):
            path = drawn_paths(self.doc)[self.s[key].Name]
            self.assertGreater(count_vertices(path), 10)
            box = bbox_of(path)
            ext = ((box[3] - box[0]) ** 2 + (box[4] - box[1]) ** 2 + (box[5] - box[2]) ** 2) ** 0.5
            self.assertLessEqual(ext, 2 * diag)

    def test_deleting_the_presentation_of_a_datum_use_moves_the_drawn_use(self):
        # DatumC (first use) has no presentation: it draws; giving the second use a presentation makes that one draw
        self.s["datumfb2"].Presentation = Part.Compound([polyline((0, 0, 0), (1, 1, 1))])
        self.doc.recompute()
        Gui.updateGui()
        names = drawn_paths(self.doc)
        self.assertIn("DatumC2", names)
        self.assertNotIn("DatumC", names)
        self.assertEqual(label_text(names["DatumC2"]), "")

    def test_pick_in_the_view(self):
        self.assertEqual(pick_items(self.doc, ["DimFb", "TolFb", "DatumC", "Tol"]), [])


PICK_MARGIN_PX = 8  # Coin's ray pick takes a few pixels around a line


def aim_at(doc, name):
    """Point the camera at a drawn item from outside along its label normal; returns the pixel of its centre"""
    view = Gui.getDocument(doc.Name).ActiveView
    cam = view.getCameraNode()
    diag = model_diagonal(doc)
    quad = pick_proxy_quad(drawn_paths(doc)[name])
    assert quad is not None, name
    c = (quad[0] + quad[2]) * 0.5
    normal = (quad[1] - quad[0]).cross(quad[3] - quad[0])
    normal.normalize()
    item = doc.getObject(name)
    if item.References:
        ctr = item.References[0][0].Shape.BoundBox.Center
        if (c - ctr).dot(normal) < 0:
            normal = normal * -1
    up = coin.SbVec3f(0, 0, 1) if abs(normal.z) < 0.9 else coin.SbVec3f(0, 1, 0)
    cam.position.setValue(coin.SbVec3f(*(c + normal * (1.5 * diag))))
    cam.pointAt(coin.SbVec3f(c.x, c.y, c.z), up)
    cam.nearDistance.setValue(0.01 * diag)
    cam.farDistance.setValue(10 * diag)
    if cam.isOfType(coin.SoOrthographicCamera.getClassTypeId()):
        cam.height.setValue(2.0 * diag)
    Gui.updateGui()
    x, y = view.getPointOnScreen(c.x, c.y, c.z)
    return int(x), int(y)


def covered_by_other(doc, name, x, y):
    """Whether the pixel lies inside (or within the pick margin of) the screen footprint of the pick quad of another drawn item that is at least as
    near the camera (two labels on one face share a place, so a click there is ambiguous by construction)"""
    view = Gui.getDocument(doc.Name).ActiveView
    eye = App.Vector(*view.getCameraNode().position.getValue().getValue())
    mine = pick_proxy_quad(drawn_paths(doc)[name])
    mine_dist = (eye - (mine[0] + mine[2]) * 0.5).Length
    for other, path in drawn_paths(doc).items():
        quad = pick_proxy_quad(path)
        if other == name or quad is None:
            continue
        if (eye - (quad[0] + quad[2]) * 0.5).Length > mine_dist + 1e-3 * model_diagonal(doc):
            continue  # behind the item: it cannot take the click
        pts = [view.getPointOnScreen(q.x, q.y, q.z) for q in quad]
        signs = []
        near = False
        for i in range(4):
            (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % 4]
            signs.append((x1 - x0) * (y - y0) - (y1 - y0) * (x - x0))
            # an item seen edge-on is a line on the screen: the pick radius (a few pixels) still hits it
            seg2 = (x1 - x0) ** 2 + (y1 - y0) ** 2
            t = 0.0 if seg2 == 0 else max(0.0, min(1.0, ((x - x0) * (x1 - x0) + (y - y0) * (y1 - y0)) / seg2))
            if ((x0 + t * (x1 - x0) - x) ** 2 + (y0 + t * (y1 - y0) - y) ** 2) ** 0.5 <= PICK_MARGIN_PX:
                near = True
        if near or all(v >= 0 for v in signs) or all(v <= 0 for v in signs):
            return True
    return False


def pick_items(doc, names):
    """Click the projected centre of each named item with the camera looking at it from outside; returns the wrong picks
    as (item, what was picked)"""
    view = Gui.getDocument(doc.Name).ActiveView
    wrong = []
    for n in names:
        x, y = aim_at(doc, n)
        info = view.getObjectInfo((x, y))
        got = info.get("Object") if info else None
        if got != n:
            wrong.append((n, got))
    return wrong


# ---------------------------------------------------------------------------------------------------------------
# the corpus: skipped cleanly when the NIST files are absent
@unittest.skipUnless(CORPUS_FILES, "the NIST PMI corpus is not present (it is never committed)")
class TestPmiDisplayCorpus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pmigui")

    def tearDown(self):
        Gui.Selection.clearSelection()
        for name in list(App.listDocuments()):
            App.closeDocument(name)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _open(self, path):
        Import.open(path)
        doc = App.ActiveDocument
        Gui.updateGui()
        return doc

    def test_drawn_count_equals_the_ceiling_on_every_file(self):
        for path in CORPUS_FILES:
            with self.subTest(file=os.path.basename(path)):
                doc = self._open(path)
                try:
                    names = drawn_paths(doc)
                    self.assertEqual(len(names), expected_total(doc))
                    self.assertEqual(len(visible_names(doc)), expected_total(doc))
                    diag = model_diagonal(doc)
                    for n, p in names.items():
                        self.assertGreaterEqual(count_vertices(p), 1, n)
                        box = bbox_of(p)
                        self.assertIsNotNone(box, n)
                        ext = ((box[3] - box[0]) ** 2 + (box[4] - box[1]) ** 2 + (box[5] - box[2]) ** 2) ** 0.5
                        self.assertLessEqual(ext, 2 * diag, n)
                finally:
                    App.closeDocument(doc.Name)

    def test_fallback_layout_counts_text_and_position_on_every_file(self):
        # check 21: items without a Presentation draw a label at their faces; check 17's count plus these, plus the
        # unattached items that draw nothing, is every semantic item (datums per letter)
        table = {}
        anchors_checked = 0
        anchors_off = []
        for path in CORPUS_FILES:
            stem = os.path.basename(path)
            with self.subTest(file=stem):
                doc = self._open(path)
                try:
                    paths = drawn_paths(doc)
                    texts = {n: label_text(p) for n, p in paths.items()}
                    fallback = [n for n, t in texts.items() if t]
                    presented = [n for n, t in texts.items() if not t]
                    self.assertEqual(len(presented), expected_presented(doc))
                    self.assertEqual(len(fallback), expected_fallback(doc))
                    # what draws nothing: semantic items without a presentation that are unattached (datums per letter)
                    pres_keys = {_datum_key(o) for o in semantic_items(doc) if o.TypeId == "Part::PmiDatum" and not o.Presentation.isNull()}
                    skipped = set()
                    for o in semantic_items(doc):
                        if not o.Presentation.isNull() or o.TypeId == "Part::PmiAnnotation":
                            continue
                        if o.Unattached or not o.References:
                            skipped.add(_datum_key(o) if o.TypeId == "Part::PmiDatum" else o.Name)
                    skipped = {k for k in skipped if not (isinstance(k, tuple) and k in pres_keys)}
                    self.assertEqual(len(presented) + len(fallback) + len(skipped), semantic_count(doc))
                    diag = model_diagonal(doc)
                    for n in fallback:
                        item = doc.getObject(n)
                        box = bbox_of(paths[n])
                        self.assertIsNotNone(box, n)
                        ext = ((box[3] - box[0]) ** 2 + (box[4] - box[1]) ** 2 + (box[5] - box[2]) ** 2) ** 0.5
                        self.assertLessEqual(ext, 2 * diag, n)
                        self.assertGreaterEqual(count_vertices(paths[n]), 1, n)
                        got = face_anchor(doc, item, diag) if item.References else None
                        if got is not None:
                            anchors_checked += 1
                            centre = App.Vector((box[0] + box[3]) / 2, (box[1] + box[4]) / 2, (box[2] + box[5]) / 2)
                            if (centre - got[0]).Length > 0.005 * diag:
                                anchors_off.append((stem, n, round((centre - got[0]).Length, 3), round(diag, 1)))
                    table[stem] = dict(
                        presentation=len(presented), fallback=len(fallback), unattached_skipped=len(skipped),
                        drawn=len(paths), semantic=semantic_count(doc), annotations=sum(1 for o in semantic_items(doc) if o.TypeId == "Part::PmiAnnotation"),
                    )
                finally:
                    App.closeDocument(doc.Name)
        out = os.environ.get("PMI_TABLE_OUT")
        if out and table:
            import json

            with open(out, "w") as f:
                json.dump(dict(table=table, anchors_checked=anchors_checked, anchors_off=anchors_off), f, indent=1)
        self.assertEqual(anchors_off, [], "fallback labels not at their expected anchor, of %d checked" % anchors_checked)

    def test_pick_in_the_view(self):
        # check 22: a click at the projected centre of a drawn item selects that item: 10 items across 3 files
        want = {SELECT_FILES[0]: (2, 2), SELECT_FILES[1]: (3, 0), SELECT_FILES[2]: (0, 3)}  # (fallback, presented)
        results = []
        for stem, (n_fb, n_pr) in want.items():
            path = os.path.join(CORPUS, stem + ".stp")
            if not os.path.exists(path):
                self.skipTest("corpus file missing: " + stem)
            doc = self._open(path)
            try:
                paths = drawn_paths(doc)
                texts = {n: label_text(p) for n, p in paths.items()}
                chosen = []
                overlapped = []
                for n, t in sorted(texts.items()):
                    item = doc.getObject(n)
                    if item.TypeId == "Part::PmiAnnotation" or not item.References:
                        continue
                    if t and sum(1 for c in chosen if texts[c]) >= n_fb:
                        continue
                    if not t and sum(1 for c in chosen if not texts[c]) >= n_pr:
                        continue
                    x, y = aim_at(doc, n)
                    if covered_by_other(doc, n, x, y):
                        overlapped.append(n)  # another label sits over the centre of this one: not a fair click
                        continue
                    chosen.append(n)
                self.assertEqual(len(chosen), n_fb + n_pr, stem)
                wrong = pick_items(doc, chosen)
                results.append((stem, chosen, wrong, overlapped))
            finally:
                App.closeDocument(doc.Name)
        out = os.environ.get("PMI_PICK_OUT")
        if out:
            import json

            with open(out, "w") as f:
                json.dump(results, f, indent=1)
        self.assertEqual(sum(len(r[1]) for r in results), 10)
        self.assertEqual([r[2] for r in results if r[2]], [], "wrong picks")

    def test_selection_highlights_references(self):
        for stem in SELECT_FILES:
            path = os.path.join(CORPUS, stem + ".stp")
            self.assertTrue(os.path.exists(path), path)
            with self.subTest(file=stem):
                doc = self._open(path)
                try:
                    # the objects the items govern (a container above one of them carries its child's nodes in its own root)
                    governed = {n for i in semantic_items(doc) for n in reference_faces(i)}
                    bodies = [doc.getObject(n) for n in sorted(governed) if len(doc.getObject(n).Shape.Faces) > 1]
                    self.assertTrue(bodies)
                    base_scene = {b.Name: body_materials(b) for b in bodies}
                    base_props = {b.Name: body_colour_properties(b) for b in bodies}
                    checked = 0
                    for item in semantic_items(doc):
                        refs = reference_faces(item)
                        if not refs:
                            continue
                        Gui.Selection.clearSelection()
                        Gui.Selection.addSelection(item)
                        self.assertEqual([o.Name for o in Gui.Selection.getSelection(doc.Name)], [item.Name])
                        for b in bodies:
                            self.assertEqual(highlighted_faces(b), refs.get(b.Name, set()), item.Name)
                            self.assertEqual(body_colour_properties(b), base_props[b.Name])
                        checked += 1
                    self.assertGreater(checked, 0)
                    print("PMI_SELECT_CHECKED file=%s items=%d mismatches=0" % (stem, checked))
                    Gui.Selection.clearSelection()
                    for b in bodies:
                        self.assertEqual(highlighted_faces(b), set())
                        self.assertEqual(body_materials(b), base_scene[b.Name])
                    # saving while an item is selected writes no changed colours
                    a = os.path.join(self.tmp, stem + "_a.FCStd")
                    c = os.path.join(self.tmp, stem + "_b.FCStd")
                    doc.saveAs(a)
                    item = next(i for i in semantic_items(doc) if reference_faces(i))
                    Gui.Selection.addSelection(item)
                    doc.saveAs(c)
                    self.assertEqual(gui_xml_without_camera(a), gui_xml_without_camera(c))
                    # an item that governs nothing selects alone
                    for item in semantic_items(doc):
                        if not item.References:
                            Gui.Selection.clearSelection()
                            Gui.Selection.addSelection(item)
                            self.assertEqual([o.Name for o in Gui.Selection.getSelection(doc.Name)], [item.Name])
                            break
                finally:
                    Gui.Selection.clearSelection()
                    App.closeDocument(doc.Name)

    def test_follow_group_visibility_and_save_reopen(self):
        for path in CORPUS_FILES:
            stem = os.path.basename(path)
            with self.subTest(file=stem):
                doc = self._open(path)
                try:
                    total = visible_names(doc)
                    self.assertEqual(len(total), expected_total(doc))
                    # check 19: the governed body moves by 10 mm in x, every item of that body by 10 mm
                    paths = drawn_paths(doc)
                    by_body = {}
                    for o in semantic_items(doc):
                        if o.Name in paths and o.References:
                            by_body.setdefault(o.References[0][0].Name, []).append(o.Name)
                    if by_body:
                        body_name = max(by_body, key=lambda k: len(by_body[k]))
                        body = doc.getObject(body_name)
                        before = {n: bbox_of(p) for n, p in drawn_paths(doc).items()}
                        pl = body.Placement
                        body.Placement = App.Placement(pl.Base + App.Vector(10, 0, 0), pl.Rotation)
                        Gui.updateGui()
                        after = {n: bbox_of(p) for n, p in drawn_paths(doc).items()}
                        for n in before:
                            shift = [after[n][i] - before[n][i] for i in range(6)]
                            want = 10.0 if n in by_body[body_name] else 0.0
                            for i in (0, 3):
                                self.assertAlmostEqual(shift[i], want, delta=1e-4, msg=n)
                            for i in (1, 2, 4, 5):
                                self.assertAlmostEqual(shift[i], 0.0, delta=1e-4, msg=n)
                        doc.recompute()
                        again = {n: bbox_of(p) for n, p in drawn_paths(doc).items()}
                        for n in after:
                            for i in range(6):
                                self.assertAlmostEqual(again[n][i], after[n][i], delta=1e-6, msg=n)
                        body.Placement = pl
                    # check 20: the group hides every item, showing it restores the count
                    gvp = vp_of(group_of(doc))
                    gvp.Visibility = False
                    self.assertEqual(visible_names(doc), [])
                    gvp.Visibility = True
                    self.assertEqual(visible_names(doc), total)
                    # check 23: save, close, reopen, same count
                    out = os.path.join(self.tmp, stem + ".FCStd")
                    doc.saveAs(out)
                    App.closeDocument(doc.Name)
                    doc = App.openDocument(out)
                    Gui.updateGui()
                    self.assertEqual(visible_names(doc), total)
                finally:
                    for name in list(App.listDocuments()):
                        App.closeDocument(name)

    def test_open_time_with_and_without_display(self):
        # check 24: the largest file by item count, display on versus the group hidden at open (preference ShowOnOpen)
        sizes = {}
        for path in CORPUS_FILES:
            doc = self._open(path)
            sizes[path] = len(semantic_items(doc))
            App.closeDocument(doc.Name)
        biggest = max(sizes, key=sizes.get)
        pref = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Part/PmiDisplay")
        saved = pref.GetBool("ShowOnOpen", True)
        res = {}
        try:
            for mode in ("hidden", "on", "hidden", "on", "hidden", "on"):
                pref.SetBool("ShowOnOpen", mode == "on")
                t = time.perf_counter()
                Import.open(biggest)
                Gui.updateGui()
                dt = time.perf_counter() - t
                doc = App.ActiveDocument
                res.setdefault(mode, []).append(dt)
                res["nodes"] = len(drawn_paths(doc))
                App.closeDocument(doc.Name)
        finally:
            pref.SetBool("ShowOnOpen", saved)
        on = sorted(res["on"])[1]
        hidden = sorted(res["hidden"])[1]
        print("PMI_OPEN_TIME items=%d file=%s on=%.3fs hidden=%.3fs ratio=%.3f drawn_separators_on=%d" % (sizes[biggest], os.path.basename(biggest), on, hidden, on / hidden, res["nodes"]))
        self.assertLessEqual(on / hidden, 1.5)


if __name__ == "__main__":
    unittest.main()
