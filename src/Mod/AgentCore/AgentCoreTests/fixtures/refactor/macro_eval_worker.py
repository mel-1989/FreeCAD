# SPDX-License-Identifier: LGPL-2.1-or-later
# In-FreeCAD half of macro_eval.py: exec each macro, then its check expression. Run with FreeCADCmd <this file>.
import json
import os
import sys

import traceback

import FreeCAD

sys.path.insert(0, os.environ["CADFORK_F6_AGENTCORE"])
items = json.load(open(os.environ["CADFORK_F6_IN"]))
out = {}
for t in items:
    r = {"ran": False, "check": None, "error": None}
    try:
        ns = {"__name__": "__main__"}
        exec(compile(t["macro"], t["id"], "exec"), ns)
        r["ran"] = True
        doc = FreeCAD.getDocument("T")
        r["check"] = bool(eval(t["check"], {"FreeCAD": FreeCAD, "doc": doc}))
    except BaseException:
        r["error"] = traceback.format_exc(limit=3)[-400:]
    finally:
        try:
            FreeCAD.closeDocument("T")
        except Exception:
            pass
    out[t["id"]] = r
json.dump(out, open(os.environ["CADFORK_F6_OUT"], "w"))
print("F6_MACRO_WORKER_DONE")



