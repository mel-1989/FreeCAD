# SPDX-License-Identifier: LGPL-2.1-or-later
# Build the F6 refactor corpus of scripted documents: the 13 golden models plus parameter
# variants (all numeric parameters scaled, counts kept). Runs INSIDE FreeCADCmd:
#   CADFORK_F6_MODELS=<fixtures/golden/models> CADFORK_F6_OUT=<dir> FreeCADCmd -c "exec(open(THIS).read())"
# Writes <dir>/<name>.FCStd and <dir>/built.json (name -> {"model", "factor", "path"}). A variant whose
# recompute leaves an object in error is dropped and listed under "dropped".
import importlib.util
import json
import os
import sys

import FreeCAD

MODELS = os.environ["CADFORK_F6_MODELS"]
OUT = os.environ["CADFORK_F6_OUT"]
FACTORS = [1.0, 0.8, 1.25, 1.5, 2.0, 0.6]
KEEP = {"occ"}


def _load(name):
    sys.path.insert(0, MODELS)
    spec = importlib.util.spec_from_file_location(name, os.path.join(MODELS, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


helpers = _load("_helpers")
built, dropped = {}, {}
os.makedirs(OUT, exist_ok=True)
for fn in sorted(f for f in os.listdir(MODELS) if f.startswith("m") and f.endswith(".py") and f[1:3].isdigit()):
    model = fn[:-3]
    mod = _load(model)
    for factor in FACTORS:
        params = {k: (v if k in KEEP else (round(v * factor, 6) if isinstance(v, float) or factor != 1.0 else v))
                  for k, v in mod.PARAMS.items()}
        name = model if factor == 1.0 else f"{model}_x{factor:g}".replace(".", "p")
        doc = FreeCAD.newDocument(name)
        try:
            mod.build(doc, params, helpers)
            doc.recompute(None, True)
            bad = [o.Name for o in doc.Objects if "Invalid" in o.State or "Error" in o.State]
            if bad:
                dropped[name] = bad
                continue
            path = os.path.join(OUT, name + ".FCStd")
            doc.saveAs(path)
            built[name] = {"model": model, "factor": factor, "path": path}
        except Exception as exc:
            dropped[name] = repr(exc)
        finally:
            FreeCAD.closeDocument(doc.Name)
with open(os.path.join(OUT, "built.json"), "w") as fh:
    json.dump({"built": built, "dropped": dropped}, fh, indent=1, sort_keys=True)
print(f"F6_CORPUS_BUILT built={len(built)} dropped={len(dropped)}")
