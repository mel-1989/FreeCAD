# SPDX-License-Identifier: LGPL-2.1-or-later

# Regenerates the "templates" section of TitleBlockKeys.json: for every bundled template that carries editable fields, the resolved
# map from the template's own keys to SheetSet's canonical field names, with the "overrides" of the file baked in.
# Public sources: the bundled templates' freecad:editable keys and freecad:autofill ids (field names only).
#
#   TECHDRAW_SRC=<worktree>/src/Mod/TechDraw FreeCADCmd TitleBlockKeysGen.py      (rewrites TitleBlockKeys.json there in place)

import json
import os
import sys

import FreeCAD as App

HERE = os.environ.get("TECHDRAW_SRC") or os.getcwd()
sys.path.insert(0, HERE)

import SheetSet as SS


def main(json_path):
    with open(json_path, encoding="utf-8") as fh:
        data = json.load(fh)
    data["templates"] = {}
    SS._KEYS["data"] = data
    root = os.path.join(App.getResourceDir(), "Mod", "TechDraw", "Templates")
    for dirpath, _, files in sorted(os.walk(root)):
        for f in sorted(files):
            if not f.endswith(".svg"):
                continue
            path = os.path.join(dirpath, f)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            fields = SS.template_fields(path)
            if not fields:
                continue
            exclude, add = [], {}
            for ov in data.get("overrides", []):
                if rel.startswith(ov["match"]):
                    exclude += ov.get("exclude", [])
                    add.update(ov.get("add", {}))
            mapped, excluded = SS.classify(fields, exclude=exclude, add=add, data=data)
            data["templates"][rel] = {"keys": mapped, "excluded": excluded}
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1, sort_keys=False)
        fh.write("\n")
    print("TitleBlockKeysGen: %d templates" % len(data["templates"]))


main(os.path.join(HERE, "TitleBlockKeys.json"))
