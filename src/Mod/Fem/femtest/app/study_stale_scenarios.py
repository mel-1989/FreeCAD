# SPDX-License-Identifier: LGPL-2.1-or-later
"""Brief check 5, second half: 22 edit scenarios on a solved study; after each edit `femstudy.is_stale` must be True and the result read back must
carry `stale: true`; a new run must clear both. Run inside FreeCADCmd:  FreeCADCmd study_stale_scenarios.py  with CADFORK_STALE_RUN=1 (prints STALE_ALL_PASS or the failures).
Written in run 067 before the study code exists. The base model is a PartDesign cantilever (10 x 20 x 100 mm pad along z), steel, 50 N across the end.
Spec-level edits go through the `StudySpec` JSON property that `define_static` stores on the Analysis (SPEC.md, Stale results)."""

import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def base(doc):
    import study_corpus as sc

    dd = sc._pd_prism(doc, 10.0, 20.0, 100.0)
    body = dd["body"]
    spec = sc.mkspec(body.Name, sc.mat("steel"), [sc.sup(sc.planes(body, 2, 0.0))],
                     [sc.frc(sc.planes(body, 2, 100.0), "50 N", (-1, 0, 0))], 26.0, levels=2)
    return dd, spec


def base_hole(doc):
    """Scenarios 21 and 22: a 100 x 20 x 20 mm box with a through hole (radius 4) at (50, 10), fixed at x = 0, pulled at x = 100. The verifier's moved-hole
    edits leave the volume, the bounding box and every referenced face where they were, and the first version of the hash did not see them."""
    import FreeCAD
    import study_corpus as sc

    V = FreeCAD.Vector
    box = doc.addObject("Part::Box", "Box")
    box.Length, box.Width, box.Height = 100, 20, 20
    hole = doc.addObject("Part::Cylinder", "Hole")
    hole.Radius, hole.Height = 4, 20
    hole.Placement = FreeCAD.Placement(V(50, 10, 0), FreeCAD.Rotation())
    cut = doc.addObject("Part::Cut", "Body")
    cut.Base, cut.Tool = box, hole
    doc.recompute()
    spec = sc.mkspec("Body", sc.mat("steel"), [sc.sup(sc.planes(cut, 0, 0))], [sc.frc(sc.planes(cut, 0, 100), "2000 N", (0, 0, -1))], 8.0, levels=2)
    return {"hole": hole}, spec


def move_hole(x, y):
    def f(doc, dd, name):
        import FreeCAD

        dd["hole"].Placement = FreeCAD.Placement(FreeCAD.Vector(x, y, 0), FreeCAD.Rotation())
        doc.recompute()
    return f


def edit_spec(key_path, value):
    def f(doc, dd, name):
        an = doc.getObject(name)
        s = json.loads(an.StudySpec)
        d = s
        for k in key_path[:-1]:
            d = d[k]
        d[key_path[-1]] = value
        an.StudySpec = json.dumps(s)
    return f


def set_pad(prop, value):
    return lambda doc, dd, name: (setattr(dd["pad"], prop, value), doc.recompute())


def scenarios():
    import study_corpus as sc

    S = []
    S.append(("pad length 100 to 120", set_pad("Length", 120.0)))
    S.append(("pad length 100 to 90", set_pad("Length", 90.0)))
    S.append(("load value 50 N to 60 N", edit_spec(["loads", 0, "value"], "60 N")))
    S.append(("load value 50 N to 55 N", edit_spec(["loads", 0, "value"], "55 N")))
    S.append(("load direction flipped", edit_spec(["loads", 0, "direction"], [1, 0, 0])))
    S.append(("support letters xyz to xy", edit_spec(["supports", 0, "fixed"], "xy")))
    S.append(("second support added", lambda doc, dd, name: _add_support(doc, name, sc)))
    S.append(("load removed", edit_spec(["loads"], [])))
    S.append(("E 210 GPa to 200 GPa", edit_spec(["material", "E"], "200 GPa")))
    S.append(("nu 0.3 to 0.29", edit_spec(["material", "nu"], 0.29)))
    S.append(("density changed", edit_spec(["material", "density"], "7900 kg/m^3")))
    S.append(("yield added", edit_spec(["material", "yield"], "300 MPa")))
    S.append(("h0 26 mm to 20 mm", edit_spec(["options", "h0"], "20 mm")))
    S.append(("target 1% to 2%", edit_spec(["options", "target"], 0.02)))
    S.append(("levels 2 to 3", edit_spec(["options", "levels"], 3)))
    S.append(("pocket added behind the pad", lambda doc, dd, name: _add_pocket(doc, dd, sc)))
    S.append(("fillet inserted behind the pad", lambda doc, dd, name: sc._pd_fillet_all_z_edges(doc, dd, 2.0)))
    S.append(("pad reversed", set_pad("Reversed", True)))
    S.append(("body placement moved 1 mm", lambda doc, dd, name: (setattr(dd["body"], "Placement", _moved(dd["body"].Placement)), doc.recompute())))
    S.append(("sketch attachment offset 1 mm", lambda doc, dd, name: (setattr(doc.getObject("Sketch"), "AttachmentOffset", _moved(doc.getObject("Sketch").AttachmentOffset)), doc.recompute())))
    S.append(("hole moved along x, 50 to 8 (volume and bounding box unchanged)", move_hole(8, 10), base_hole))
    S.append(("hole moved along y, 10 to 6 (volume and bounding box unchanged)", move_hole(50, 6), base_hole))
    assert len(S) == 22
    return S


def _moved(pl):
    import FreeCAD

    return FreeCAD.Placement(pl.Base + FreeCAD.Vector(1, 0, 0), pl.Rotation)


def _add_support(doc, name, sc):
    an = doc.getObject(name)
    s = json.loads(an.StudySpec)
    body = doc.getObject(s["body"])
    s["supports"].append(sc.sup(sc.planes(body, 1, 10.0), "y"))
    an.StudySpec = json.dumps(s)


def _add_pocket(doc, dd, sc):
    body = dd["body"]
    sk = sc._pd_sketch_rect(doc, body, "CutSketch", 20.0, 40.0, z=100.0)
    pk = body.newObject("PartDesign::Pocket", "CutPocket")
    pk.Profile = sk
    pk.Length = 5.0
    doc.recompute()


def main():
    import FreeCAD
    import femstudy

    fails = []
    for i, sc_ in enumerate(scenarios(), 1):
        title, edit = sc_[0], sc_[1]
        try:
            doc = FreeCAD.newDocument(f"stale{i}")
            dd, spec = (sc_[2] if len(sc_) > 2 else base)(doc)
            res = femstudy.run_static(doc, spec)
            name = res["analysis"]
            assert not femstudy.is_stale(doc, name), "stale right after a run"
            edit(doc, dd, name)
            doc.recompute()
            ok_flag = femstudy.is_stale(doc, name)
            stored = femstudy.stored_result(doc, name)
            ok_stored = bool(stored and stored.get("stale") is True)
            res2 = femstudy.run_static(doc, name)
            cleared = (not femstudy.is_stale(doc, name)) and res2.get("stale") is False
            if not (ok_flag and ok_stored and cleared):
                fails.append(f"{i:02d} {title}: stale={ok_flag} stored.stale={ok_stored} cleared={cleared}")
        except Exception as ex:  # noqa: BLE001
            fails.append(f"{i:02d} {title}: {type(ex).__name__}: {str(ex)[:120]}")
            traceback.print_exc()
    print("STALE_ALL_PASS" if not fails else "STALE_FAIL\n" + "\n".join(fails))
    sys.stdout.flush()
    os._exit(0)


if os.environ.get("CADFORK_STALE_RUN") == "1":
    main()
