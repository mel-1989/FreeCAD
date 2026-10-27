# SPDX-License-Identifier: LGPL-2.1-or-later
"""Agent-usability eval for AgentCore.state: 40 query tasks.

Each task names a fixture document (a key of builders.ALL), a question an
agent might ask about the model, the answer kind, and ``truth(doc)``: the
answer computed straight from the open FreeCAD document with the FreeCAD API,
never from a state dump. ``python -m``-free: FreeCAD process only.

Answer kinds and grading (see grade.py): ``number`` (relative tolerance
1e-3), ``int``, ``bool``, ``string`` (trimmed, case-insensitive) and ``set``
(order-free list of strings or ints).
"""

TASKS = []


def task(tid, fixture, question, kind):
    def deco(fn):
        TASKS.append({"id": tid, "fixture": fixture, "question": question, "kind": kind, "truth": fn})
        return fn
    return deco


def _vars(doc, name):
    base = set(doc.addObject("App::VarSet", "_eval_tmp").PropertiesList)
    out = sorted(set(doc.getObject(name).PropertiesList) - base)
    doc.removeObject("_eval_tmp")
    return out


def _solved(sk):
    sk.solve()
    return sk


B = "built_partdesign_block"


@task("block_pad_length", B, "What is the length of the Pad feature, in mm?", "number")
def _(doc):
    return doc.getObject("Pad").Length.Value


@task("block_pad_alias", B, "Which spreadsheet alias drives the Pad's Length through an expression? Give the alias name only.", "string")
def _(doc):
    expr = dict(doc.getObject("Pad").ExpressionEngine)["Length"]
    return expr.split(".", 1)[1]


@task("block_cell_b1", B, "What is the value of spreadsheet cell B1 of the sheet 'Params', in mm (number only)?", "number")
def _(doc):
    return doc.getObject("Params").get("B1").Value


@task("block_varset_vars", B, "List the user variables defined in the VarSet named 'Vars'.", "set")
def _(doc):
    return _vars(doc, "Vars")


@task("block_body_tip", B, "Which feature is the Tip of the Body? Give its object name.", "string")
def _(doc):
    return doc.getObject("Body").Tip.Name


@task("block_body_features", B, "List the object names of everything in the Body's Group (excluding the Origin).", "set")
def _(doc):
    return [o.Name for o in doc.getObject("Body").Group if o.TypeId != "App::Origin"]


@task("block_fillet_radius", B, "What is the Fillet's radius in mm?", "number")
def _(doc):
    return doc.getObject("Fillet").Radius.Value


@task("block_sketch_full", B, "Is the sketch named 'Sketch' fully constrained? Answer true or false.", "bool")
def _(doc):
    return bool(_solved(doc.getObject("Sketch")).FullyConstrained)


@task("block_body_volume", B, "What is the volume of the Body's final solid, in cubic mm?", "number")
def _(doc):
    return doc.getObject("Body").Shape.Volume


@task("block_fillet_faces", B, "How many faces does the Fillet feature's shape have?", "int")
def _(doc):
    return len(doc.getObject("Fillet").Shape.Faces)


@task("block_pad_inlist", B, "Which objects directly depend on (link to) the object 'Pad'? List their names.", "set")
def _(doc):
    return sorted({o.Name for o in doc.getObject("Pad").InList})


@task("block_pocket_type", B, "What is the Pocket feature's Type setting (its enumeration value)?", "string")
def _(doc):
    return str(doc.getObject("Pocket").Type)


@task("block_holesketch_support", B, "To which object is the sketch 'HoleSketch' attached? Give the object name.", "string")
def _(doc):
    return doc.getObject("HoleSketch").AttachmentSupport[0][0].Name


S = "built_sketch_states"


@task("sk_under_dof", S, "How many degrees of freedom remain in the sketch 'Under'?", "int")
def _(doc):
    return int(_solved(doc.getObject("Under")).DoF)


@task("sk_redundant_sketch", S, "Which sketch has redundant constraints? Give its name.", "string")
def _(doc):
    return [o.Name for o in doc.Objects if _solved(o).RedundantConstraints][0]


@task("sk_redundant_ids", S, "List the redundant constraint numbers the solver reports for sketch 'Over' (as reported by FreeCAD, 1-based).", "set")
def _(doc):
    return sorted(_solved(doc.getObject("Over")).RedundantConstraints)


@task("sk_under_geometry", S, "How many geometry elements does the sketch 'Under' contain?", "int")
def _(doc):
    return len(doc.getObject("Under").Geometry)


@task("sk_full_constraints", S, "How many constraints does the sketch 'Full' have?", "int")
def _(doc):
    return len(doc.getObject("Full").Constraints)


@task("sk_fully_constrained_set", S, "List the names of all sketches that are fully constrained.", "set")
def _(doc):
    return sorted(o.Name for o in doc.Objects if _solved(o).FullyConstrained)


T = "built_techdraw"


@task("td_template_file", T, "What is the file name (basename only) of the drawing template used by the page 'Page'?", "string")
def _(doc):
    return doc.getObject("Template").Template.replace("\\", "/").rsplit("/", 1)[-1]


@task("td_view_count", T, "How many items (views and dimensions) are on the page 'Page'?", "int")
def _(doc):
    return len(doc.getObject("Page").Views)


@task("td_dimension_text", T, "What text does the dimension 'Dimension' display?", "string")
def _(doc):
    return doc.getObject("Dimension").getText()


@task("td_topview_source", T, "Which 3D object is the source of the drawing view 'TopView'? Give its name.", "string")
def _(doc):
    return doc.getObject("TopView").Source[0].Name


@task("td_cut_volume", T, "What is the volume of the object 'Cut' in cubic mm?", "number")
def _(doc):
    return doc.getObject("Cut").Shape.Volume


@task("td_cyl_radius", T, "What is the radius of the cylinder 'Cyl' in mm?", "number")
def _(doc):
    return doc.getObject("Cyl").Radius.Value


C = "upstream_crank"


@task("crank_feature_count", C, "How many objects of type Part::Feature are in the document?", "int")
def _(doc):
    return sum(1 for o in doc.Objects if o.TypeId == "Part::Feature")


@task("crank_largest_volume", C, "Which object has the solid with the largest volume? Give its name.", "string")
def _(doc):
    return max((o for o in doc.Objects if hasattr(o, "Shape") and not o.Shape.isNull()), key=lambda o: o.Shape.Volume).Name


@task("crank_object_count", C, "How many objects does the document contain in total?", "int")
def _(doc):
    return len(doc.Objects)


@task("crank_shape001_xsize", C, "What is the extent of object 'Shape001' along the X axis (bounding box XMax - XMin), in mm?", "number")
def _(doc):
    return doc.getObject("Shape001").Shape.BoundBox.XLength


@task("crank_shape005_faces", C, "How many faces does the shape of 'Shape005' have?", "int")
def _(doc):
    return len(doc.getObject("Shape005").Shape.Faces)


P = "upstream_padtest"


@task("pad_pad001_length", P, "What is the Length of the feature 'Pad001', in mm?", "number")
def _(doc):
    return doc.getObject("Pad001").Length.Value


@task("pad_body_tip", P, "Which feature is the Tip of the Body? Give its object name.", "string")
def _(doc):
    return doc.getObject("Body").Tip.Name


@task("pad_body_volume", P, "What is the volume of the Body's final solid, in cubic mm?", "number")
def _(doc):
    return doc.getObject("Body").Shape.Volume


@task("pad_sketch002_support", P, "To which object is 'Sketch002' attached? Give the object name.", "string")
def _(doc):
    return doc.getObject("Sketch002").AttachmentSupport[0][0].Name


@task("pad_sketch002_full", P, "Is 'Sketch002' fully constrained? Answer true or false.", "bool")
def _(doc):
    return bool(_solved(doc.getObject("Sketch002")).FullyConstrained)


A = "upstream_assembly_example"


def _joints(doc):
    return [o for o in doc.Objects if hasattr(o, "JointType")]


@task("asm_revolute_count", A, "How many joints of type Revolute does the assembly have?", "int")
def _(doc):
    return sum(1 for j in _joints(doc) if j.JointType == "Revolute")


@task("asm_grounded", A, "Which object is grounded in the assembly? Give the grounded object's name.", "string")
def _(doc):
    return next(o.ObjectToGround.Name for o in doc.Objects if hasattr(o, "ObjectToGround"))


@task("asm_joint_count", A, "How many joints (not counting the grounding joint) does the assembly have?", "int")
def _(doc):
    return len(_joints(doc))


@task("asm_slider_refs", A, "Which two objects does the joint 'Slider' connect? Give the names of its two referenced objects.", "set")
def _(doc):
    j = doc.getObject("Slider")
    return sorted({j.Reference1[0].Name, j.Reference2[0].Name})


@task("asm_shape013_label", A, "What is the Label of the object named 'Shape013'?", "string")
def _(doc):
    return doc.getObject("Shape013").Label


assert len(TASKS) == 40, len(TASKS)
assert len({t["id"] for t in TASKS}) == 40
