# SPDX-License-Identifier: LGPL-2.1-or-later
"""Deterministic task banks for agent_eval.py: the task text, the exact objects it names, and a check
expression whose expected value is computed here from closed-form geometry (never by a model).

``macro_tasks(n)``  ->  [{"id","family","task","check"}] (20 families x 5 parameter sets = 100)
``refactor_tasks(summaries)``  ->  [{"id","doc_name","family","task","check"}] over the scripted corpus documents
"""
import math
import random

PI = math.pi


def _v(d, key="V"):
    return f'doc.getObject("{d}").Shape.Volume'


def _near(expr, value, rel=1e-4):
    return f"abs({expr} - {value!r}) <= {rel} * abs({value!r}) + 1e-6"


def macro_tasks(n=100, seed=7):
    rnd = random.Random(seed)
    fam = []

    def f(name):
        def deco(fn):
            fam.append((name, fn))
            return fn
        return deco

    def dims():
        return [rnd.choice([4, 5, 6, 8, 10, 12, 15, 20, 25, 30]) for _ in range(3)]

    @f("box")
    def _():
        a, b, c = dims()
        return (f'Create a Part::Box named "Box" with Length={a}, Width={b}, Height={c} mm.', _near(_v("Box"), a * b * c))

    @f("cylinder_placed")
    def _():
        r, h = rnd.choice([2, 3, 5, 7]), rnd.choice([10, 15, 20])
        x, y, z = rnd.sample([-10, -5, 0, 5, 10, 20], 3)
        return (f'Create a Part::Cylinder named "Cyl" with Radius={r} and Height={h}, placed with its base point at ({x}, {y}, {z}).',
                _near(_v("Cyl"), PI * r * r * h) + f' and doc.getObject("Cyl").Placement.Base == FreeCAD.Vector({x}, {y}, {z})')

    @f("sphere")
    def _():
        r = rnd.choice([2, 3, 4, 5, 8])
        return (f'Create a Part::Sphere named "Ball" with Radius={r}.', _near(_v("Ball"), 4 / 3 * PI * r ** 3))

    @f("cone")
    def _():
        r1, r2, h = rnd.choice([(5, 2, 10), (6, 0, 9), (4, 1, 12), (8, 3, 5), (3, 3, 7)])
        return (f'Create a Part::Cone named "Cone" with Radius1={r1}, Radius2={r2} and Height={h}.',
                _near(_v("Cone"), PI * h / 3 * (r1 * r1 + r1 * r2 + r2 * r2)))

    @f("torus")
    def _():
        R, r = rnd.choice([(10, 2), (12, 3), (8, 1), (15, 4)])
        return (f'Create a Part::Torus named "Ring" with Radius1={R} and Radius2={r}.', _near(_v("Ring"), 2 * PI * PI * R * r * r))

    @f("hole_cut")
    def _():
        a, b, c = dims()
        r = min(a, b) / 4
        return (f'Create a Part::Box "Plate" (Length={a}, Width={b}, Height={c} mm, Length x Width x Height) and a Part::Cylinder "Drill" with Radius={r} and Height={c} '
                f'whose axis runs through the middle of the plate along Z (placed at x={a / 2}, y={b / 2}, z=0). Cut the drill from the plate with a Part::Cut named "Result" (Base=Plate, Tool=Drill).',
                _near(_v("Result"), a * b * c - PI * r * r * c, 1e-3))

    @f("fuse_disjoint")
    def _():
        a, b, c = dims()
        return (f'Create two Part::Box objects "B1" (Length={a}, Width={b}, Height={c} mm) at the origin and "B2" (same size) placed at x={a + 5}. Fuse them with a Part::Fuse named "Joined".',
                _near(_v("Joined"), 2 * a * b * c))

    @f("fuse_overlap")
    def _():
        a, b, c = dims()
        s = a / 2
        return (f'Create two Part::Box objects "B1" (Length={a}, Width={b}, Height={c} mm) at the origin and "B2" (same size) shifted by x={s}. Fuse them with a Part::Fuse named "Joined".',
                _near(_v("Joined"), (a + s) * b * c))

    @f("common")
    def _():
        a, b, c = dims()
        s = a / 4
        return (f'Create two Part::Box objects "B1" (Length={a}, Width={b}, Height={c} mm) at the origin and "B2" (same size) shifted by x={s}. Intersect them with a Part::Common named "Overlap".',
                _near(_v("Overlap"), (a - s) * b * c))

    @f("makebox_feature")
    def _():
        a, b, c = dims()
        return (f'Build the solid with Part.makeBox({a}, {b}, {c}) and show it as a Part::Feature named "Solid" (set its Shape).', _near(_v("Solid"), a * b * c))

    @f("pad")
    def _():
        w, d, h = rnd.choice([(30, 20, 10), (40, 10, 5), (25, 25, 8), (12, 8, 20)])
        return (f'Create a PartDesign Body "Body" with a Sketcher sketch "Sketch" on the XY plane containing a closed {w} x {d} mm rectangle (four lines, corner at the origin), '
                f'and a PartDesign::Pad named "Pad" of length {h} mm on that sketch.', _near(_v("Pad"), w * d * h))

    @f("pad_pocket")
    def _():
        w, d, h, r = rnd.choice([(40, 30, 10, 4), (30, 30, 12, 5), (50, 20, 8, 3)])
        return (f'Create a PartDesign Body "Body": sketch "Sketch" on XY with a {w} x {d} mm rectangle (corner at origin), Pad "Pad" length {h}; '
                f'then sketch "HoleSketch" attached to the XY plane with its attachment offset z={h} containing a circle of radius {r} centred at ({w / 2}, {d / 2}); '
                f'then a PartDesign::Pocket "Pocket" of length {h} (cutting down through the pad) on HoleSketch.', _near(_v("Pocket"), w * d * h - PI * r * r * h, 1e-3))

    @f("revolve")
    def _():
        R, r, h = rnd.choice([(10, 6, 20), (8, 5, 15), (12, 9, 10)])
        return (f'Create a PartDesign Body "Body" with a sketch "Sketch" on the XZ plane containing the closed rectangle with x from {r} to {R} and z from 0 to {h}, '
                f'and a PartDesign::Revolution named "Rev" of 360 degrees about the sketch V_Axis (the Z axis).', _near(_v("Rev"), PI * (R * R - r * r) * h, 1e-3))

    @f("sketch_solve")
    def _():
        w, d = rnd.choice([(10, 6), (20, 12), (15, 15)])
        return (f'Create a Sketcher sketch "S" (document "T") with a closed rectangle of four lines, then constrain it fully: coincident corners, two horizontal, two vertical, '
                f'a DistanceX of {w} and a DistanceY of {d}, and the lower-left corner fixed at the origin. It must solve with no conflicts.',
                f'doc.getObject("S").solve() == 0 and abs(doc.getObject("S").Shape.BoundBox.XLength - {w}) < 1e-6 and abs(doc.getObject("S").Shape.BoundBox.YLength - {d}) < 1e-6')

    @f("spreadsheet_driven")
    def _():
        L, W, H = dims()
        return (f'Create a Spreadsheet "Dims" with aliases "len"={L}, "wid"={W}, "hgt"={H} (cells A1..A3, values in mm), and a Part::Box "Box" whose Length, Width, Height '
                f'are bound by expressions to Dims.len, Dims.wid and Dims.hgt.', _near(_v("Box"), L * W * H) + ' and len(doc.getObject("Box").ExpressionEngine) == 3')

    @f("varset_driven")
    def _():
        r, h = rnd.choice([(3, 10), (4, 12), (5, 8)])
        return (f'Create an App::VarSet "Params" with a Length property "Rad"={r} mm and a Length property "Hgt"={h} mm, and a Part::Cylinder "Cyl" whose Radius and Height '
                f'are bound by expressions to Params.Rad and Params.Hgt.', _near(_v("Cyl"), PI * r * r * h) + ' and len(doc.getObject("Cyl").ExpressionEngine) == 2')

    @f("group_labels")
    def _():
        k = rnd.choice([3, 4, 5, 6])
        return (f'Create {k} Part::Box objects named "P0" ... "P{k - 1}" (10 mm cubes, "P<i>" shifted by x=15*i), put them all in an App::DocumentObjectGroup named "Parts", '
                f'and set the Label of each box to "Part <i>".',
                f'len(doc.getObject("Parts").Group) == {k} and all(doc.getObject("P%d" % i).Label == "Part %d" % i for i in range({k}))')

    @f("rotated_box")
    def _():
        a, b, c = rnd.choice([(20, 10, 5), (30, 8, 4), (12, 6, 9)])
        return (f'Create a Part::Box "Box" (Length={a}, Width={b}, Height={c} mm) and rotate it 90 degrees about the Z axis through its Placement (rotation about the origin).',
                f'abs(doc.getObject("Box").Shape.BoundBox.XLength - {b}) < 1e-6 and abs(doc.getObject("Box").Shape.BoundBox.YLength - {a}) < 1e-6')

    @f("linear_pattern")
    def _():
        a, k = rnd.choice([(10, 3), (8, 4), (6, 5)])
        return (f'Create a PartDesign Body "Body" with a {a} x {a} mm square sketch "Sketch" on XY (corner at origin) padded 5 mm as "Pad", then a PartDesign::LinearPattern '
                f'"Pattern" of "Pad" along the sketch H_Axis with Length={a * 2 * (k - 1)} and Occurrences={k} (the copies are spaced {a * 2} mm apart and do not touch).',
                _near(_v("Pattern"), k * a * a * 5))

    @f("fillet")
    def _():
        a = rnd.choice([10, 20, 30])
        return (f'Create a Part::Box "Box" ({a} mm cube) and a Part::Fillet "Rounded" of "Box" that rounds all 12 edges with radius 1 mm.',
                f'0 < {_v("Rounded")} < {a ** 3} and len(doc.getObject("Rounded").Shape.Faces) == 26')

    @f("mirror")
    def _():
        a, b, c = dims()
        return (f'Create a Part::Box "Box" (Length={a}, Width={b}, Height={c} mm) at the origin and a Part::Mirroring "Mirrored" of it about the YZ plane (base point at the origin, normal (1,0,0)).',
                f'abs(doc.getObject("Mirrored").Shape.BoundBox.XMin + {a}) < 1e-6 and abs(doc.getObject("Mirrored").Shape.BoundBox.XMax) < 1e-6')

    @f("compound_count")
    def _():
        k = rnd.choice([3, 4, 5, 6])
        return (f'Using Part.makeBox/Part.makeSphere style shape functions, build {k} spheres of radius 2 centred at x=10*i (i=0..{k - 1}) and store one Part.makeCompound of them '
                f'in a Part::Feature named "Spheres".', f'len(doc.getObject("Spheres").Shape.Solids) == {k}')

    out = []
    per = -(-n // len(fam))
    for name, fn in fam:
        for i in range(per):
            task, check = fn()
            out.append({"id": f"{name}_{i}", "family": name, "task": task, "check": check})
    return out[:n]


def refactor_tasks(summaries, n=100, refs=None):
    """summaries: {doc file name: object listing text}. A task applies when its precondition holds on the listing.

    ``refs`` ({doc file name: reference counts from agent_eval_worker.py's ``reference`` mode}) sizes the lift and
    across-bodies checks: they require that the literals were really replaced by expressions bound to the
    container (at least as many as the reference API binds) and that every body changed. A document where the
    reference binds nothing gets no such task, so a vacuous task cannot pass."""
    refs = refs if refs is not None else {}
    kinds = [
        ("label_sketches", "Give every Sketcher sketch a Label that starts with \"Sk_\".",
         "any(o.TypeId=='Sketcher::SketchObject' for o in doc.Objects) and all(o.Label.startswith('Sk_') for o in doc.Objects if o.TypeId=='Sketcher::SketchObject')",
         lambda s, r: "Sketcher::SketchObject" in s),
        ("label_unique", "Give every PartDesign and Sketcher object a unique Label of the form <T>_<n>, where <T> is the part of its TypeId after \"::\" (for example \"Pad_1\", \"SketchObject_1\") and n counts from 1 per type in document order; Names must not change.",
         "len({o.Label for o in doc.Objects}) == len(doc.Objects) and all(o.Label.startswith(o.TypeId.split('::')[-1] + '_') for o in doc.Objects if o.TypeId.startswith(('PartDesign::', 'Sketcher::')))",
         lambda s, r: "PartDesign::" in s),
        ("lift_pads_varset", "Move the Length of every PartDesign::Pad into an App::VarSet named \"Dims\" (one property per pad) and bind each pad's Length to it with an expression.",
         "doc.getObject('Dims') is not None and doc.getObject('Dims').TypeId=='App::VarSet' and all(any(p=='Length' for p,e in o.ExpressionEngine) for o in doc.Objects if o.TypeId=='PartDesign::Pad')",
         lambda s, r: "PartDesign::Pad" in s),
        ("lift_pads_sheet", "Move the Length of every PartDesign::Pad into a Spreadsheet named \"Dims\" (one aliased cell per pad) and bind each pad's Length to it with an expression.",
         "doc.getObject('Dims') is not None and doc.getObject('Dims').TypeId=='Spreadsheet::Sheet' and all(any(p=='Length' for p,e in o.ExpressionEngine) for o in doc.Objects if o.TypeId=='PartDesign::Pad')",
         lambda s, r: "PartDesign::Pad" in s),
        ("lift_all_varset", "Lift every literal dimension that can safely be bound (feature lengths, angles, named sketch constraints) into one App::VarSet named \"Parameters\", so that each of those properties is driven by an expression that reads its value from the VarSet.",
         "doc.getObject('Parameters') is not None and doc.getObject('Parameters').TypeId=='App::VarSet' and ref['lift_varset'] > 0 "
         "and sum(1 for o in doc.Objects for _p, e in o.ExpressionEngine if 'Parameters.' in e) >= ref['lift_varset']",
         lambda s, r: "PartDesign::" in s and r.get("lift_varset", 0) > 0),
        ("lift_all_sheet", "Lift every literal dimension that can safely be bound into one Spreadsheet named \"Parameters\", so that each of those properties is driven by an expression that reads its value from a cell alias.",
         "doc.getObject('Parameters') is not None and doc.getObject('Parameters').TypeId=='Spreadsheet::Sheet' and ref['lift_sheet'] > 0 "
         "and sum(1 for o in doc.Objects for _p, e in o.ExpressionEngine if 'Parameters.' in e) >= ref['lift_sheet']",
         lambda s, r: "PartDesign::" in s and r.get("lift_sheet", 0) > 0),
        ("across_bodies", "In every PartDesign Body, replace the literal dimensions of its features by expressions that read them from a Spreadsheet or VarSet (a container shared by the bodies is fine). No body may be left unparameterised.",
         "len(ref['bodies']) > 0 and all(sum(1 for f in b.Group for _p, e in f.ExpressionEngine if any(c.Name in e for c in doc.Objects if c.TypeId in ('Spreadsheet::Sheet','App::VarSet'))) >= max(1, ref['bodies'].get(b.Name, 0)) "
         "for b in doc.Objects if b.TypeId == 'PartDesign::Body')",
         lambda s, r: "PartDesign::Body" in s and bool(r.get("bodies")) and all(v > 0 for v in r["bodies"].values())),
        ("label_bodies", "Set the Label of every PartDesign Body to \"Body_<Name>\" (its immutable Name after an underscore).",
         "all(o.Label=='Body_'+o.Name for o in doc.Objects if o.TypeId=='PartDesign::Body')", lambda s, r: "PartDesign::Body" in s),
        ("number_pads", "Relabel every PartDesign::Pad as \"Pad_1\", \"Pad_2\", ... in document order (the order of doc.Objects).",
         "[o.Label for o in doc.Objects if o.TypeId=='PartDesign::Pad'] == ['Pad_%d' % (i + 1) for i in range(len([o for o in doc.Objects if o.TypeId=='PartDesign::Pad']))]",
         lambda s, r: "PartDesign::Pad" in s),
        ("label_pockets", "Give every PartDesign::Pocket a Label that starts with \"Cut_\".",
         "any(o.TypeId=='PartDesign::Pocket' for o in doc.Objects) and all(o.Label.startswith('Cut_') for o in doc.Objects if o.TypeId=='PartDesign::Pocket')",
         lambda s, r: "PartDesign::Pocket" in s),
        ("note_sketches", "Add a string property \"RefactorNote\" with the value \"checked\" to every Sketcher sketch.",
         "any(o.TypeId=='Sketcher::SketchObject' for o in doc.Objects) and all(getattr(o,'RefactorNote',None)=='checked' for o in doc.Objects if o.TypeId=='Sketcher::SketchObject')",
         lambda s, r: "Sketcher::SketchObject" in s),
        ("note_property", "Add a string property \"RefactorNote\" with the value \"checked\" to every PartDesign Body.",
         "all(getattr(o,'RefactorNote',None)=='checked' for o in doc.Objects if o.TypeId=='PartDesign::Body')", lambda s, r: "PartDesign::Body" in s),
    ]
    out = []
    for name, text in sorted(summaries.items()):
        for k, task, check, applies in kinds:
            if applies(text, refs.get(name, {})):
                t = {"id": f"{name[:3]}_{k}", "doc_name": name, "family": k, "task": task, "check": check}
                if "ref[" in check:
                    t["ref"] = refs[name]
                out.append(t)
    # round-robin across families so a cut at n keeps all of them
    by_fam = {}
    for t in out:
        by_fam.setdefault(t["family"], []).append(t)
    mixed = []
    while any(by_fam.values()) and len(mixed) < n:
        for k in list(by_fam):
            if by_fam[k]:
                mixed.append(by_fam[k].pop(0))
    return mixed[:n]
