# SPDX-License-Identifier: LGPL-2.1-or-later
"""Scenes and helpers shared by the assembly-feature GUI tests and the harness screenshot scenes (G06 slice 4b).

A document is built from a case of ``feature_corpus`` (slice 4a) or from one of the local cases below, and
the case's feature is then taken out again: the panel is driven with the same parameters and has to rebuild
a feature whose effect equals the case's analytic truth. ``build`` returns the document, the truth and a
*recipe* (what the feature was); ``drive`` pours a recipe into a panel the way a user fills it in.
"""

import json

import FreeCAD as App

import AssemblyFeature as AF
from AssemblyTests import feature_corpus as C
from AssemblyTests.interference_corpus import link_array, source_box

DEFAULT_CASE = "hole_through_stack"

#: local cases (not in the 4a corpus, which the 4a accuracy test iterates): a link array cut by a solid tool
LOCAL = {}


def local(fn):
    LOCAL[fn.__name__] = fn
    return fn


@local
def array_solid_tool(ctx):
    """Four elements of one source 12 mm apart, cut by a box solid at x [5, 26]: elements 0, 1, 2 lose 250, 500, 100."""
    link_array(ctx.doc, ctx.asm, "Arr", source_box(ctx.src), 4, 12)
    tool = ctx.doc.addObject("Part::Box", "ToolBox")
    tool.Length, tool.Width, tool.Height = 21, 5, 10
    tool.Placement = App.Placement(App.Vector(5, 0, 0), App.Rotation())
    tool.Visibility = False
    ctx.doc.recompute()
    ctx.feature(AF.make_cut(ctx.asm, AF.solid_tool(), tool_object=tool))
    return C.T({"Arr.0": 5 * 5 * 10, "Arr.1": 10 * 5 * 10, "Arr.2": 2 * 5 * 10})


def recipe_of(feat):
    spec = json.loads(feat.Spec)
    return {"kind": feat.Kind, "spec": spec, "tool_object": feat.ToolObject.Name if feat.ToolObject is not None else None,
            "scope": list(feat.Scope), "name": feat.Name}


def show_all(asm):
    """With the GUI up a link array's elements start hidden, and hidden components are not cut: show them all."""
    if not App.GuiUp:
        return
    for obj in asm.Group:
        if getattr(obj, "ViewObject", None) is not None:
            obj.ViewObject.Visibility = True
        for el in getattr(obj, "ElementList", []) if getattr(obj, "ShowElement", False) else []:
            el.Visibility = True


def build(name=DEFAULT_CASE, remove_feature=True):
    """Build the case into fresh documents; returns (ctx, truth, recipe). The feature is removed unless asked not to."""
    if name in LOCAL:
        doc = App.newDocument("FeatGui_" + name[:40])
        asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
        ctx = C.Ctx(doc, asm, name)
        truth = LOCAL[name](ctx)
        doc.recompute()
        for d in ctx.docs:
            d.recompute()
    else:
        ctx, truth = C.build_case(name)
    show_all(ctx.asm)
    feats = AF.features(ctx.asm)
    if len(feats) != 1:
        raise ValueError("case %s holds %d features, the panel makes one" % (name, len(feats)))
    recipe = recipe_of(feats[0])
    if remove_feature:
        ctx.doc.removeObject(feats[0].Name)
        ctx.doc.recompute()
        show_all(ctx.asm)
    return ctx, truth, recipe


def drive(panel, recipe, doc):
    """Fill ``panel`` in the way a user would so that it makes the feature of ``recipe``."""
    spec = recipe["spec"]
    if recipe["kind"] == "hole":
        for w, v in zip(panel.pos, spec["position"]):
            w.setValue(v)
        for w, v in zip(panel.axis, spec["axis"]):
            w.setValue(v)
        panel.diameter.setValue(spec["diameter"])
        if spec["through"]:
            panel.through.setChecked(True)
        else:
            panel.depth.setValue(spec["depth"])
        if spec["cbore_diameter"]:
            panel.cbore.setChecked(True)
            panel.cbore_diameter.setValue(spec["cbore_diameter"])
            panel.cbore_depth.setValue(spec["cbore_depth"])
    else:
        t = spec["tool"]
        obj = doc.getObject(recipe["tool_object"])
        if t["kind"] == "extrude":
            panel.tool_kind.setCurrentIndex(0)
            panel.set_tool_object(obj)
            for w, v in zip((panel.dir_x, panel.dir_y, panel.dir_z), t["direction"]):
                w.setValue(v)
            if t["through"]:
                panel.through.setChecked(True)
            else:
                panel.length.setValue(t["length"])
            panel.symmetric.setChecked(t["symmetric"])
        elif t["kind"] == "solid":
            panel.tool_kind.setCurrentIndex(1)
            panel.set_tool_object(obj)
        else:
            raise ValueError("the panel does not make %s tools" % t["kind"])
    if recipe["scope"]:
        import Interference as I
        insts = I.resolve(panel.assembly, include_hidden=True, apply_features=False).insts
        panel.set_explicit_scope([i.key for i in insts if any(I._matches(i, s) for s in recipe["scope"])])


def scene_doc(name, case=DEFAULT_CASE):
    """A fresh document holding the case without its feature; returns (ctx, truth, recipe)."""
    return build(case)


def scene_panel(asm, recipe, doc, new=True):
    """The panel a harness scene shows: opened on the assembly and driven with ``recipe``. Nothing is shown yet."""
    from AssemblyFeaturePanel import TaskAssemblyFeature

    panel = TaskAssemblyFeature(asm, kind=recipe["kind"], selection=[])
    drive(panel, recipe, doc)
    return panel


def grab_png(panel, path, size):
    """Save the panel's form as a PNG at a fixed size and style, with no focus state."""
    from PySide import QtWidgets

    w = panel.form
    w.setStyle(QtWidgets.QStyleFactory.create("Fusion"))
    w.setFixedSize(*size)
    w.ensurePolished()
    w.layout().activate()
    w.show()
    for _ in range(4):
        QtWidgets.QApplication.processEvents()
    img = w.grab().toImage()
    img.save(path)
    w.hide()
    return img.width(), img.height()


# ------------------------------------------------------------------ signatures

def doc_signature(doc):
    """Object-and-property signature of a document: names, types, every property value, group membership, history."""
    from AssemblyTests.TestAssemblyFeature import _norm

    objs = {}
    for o in doc.Objects:
        props = {}
        for p in o.PropertiesList:
            if p == "_Part_ShapeCache":     # FreeCAD's own transient cache of a Part::Feature, filled by any read of its shape
                continue
            try:
                props[p] = _norm(o.getPropertyByName(p))
            except Exception as exc:  # noqa: BLE001
                props[p] = "unreadable:" + type(exc).__name__
        objs[o.Name] = (o.TypeId, props)
    return {"objects": objs, "undo": doc.UndoCount, "redo": doc.RedoCount, "pending": bool(doc.HasPendingTransaction)}
