# SPDX-License-Identifier: LGPL-2.1-or-later
"""Brief check 5, second half: 20 edit scenarios on a solved study; after each edit `femstudy.is_stale` must be True and the result read back must
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
    assert len(S) == 20
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
    for i, (title, edit) in enumerate(scenarios(), 1):
        try:
            doc = FreeCAD.newDocument(f"stale{i}")
            dd, spec = base(doc)
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
