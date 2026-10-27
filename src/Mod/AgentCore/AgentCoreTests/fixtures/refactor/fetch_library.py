#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Download the 64 public FreeCAD-library files of results/freecad_library_manifest.json (pinned commit) and
verify their sha256: `fetch_library.py <dest dir>` writes <dest>/<id>.FCStd for corpus_run.py --extra-dir.

The files are not stored in this repository (third-party data, mostly CC0 parts); the manifest holds each path
and checksum. Treat the downloads as untrusted data: open them only in FreeCADCmd.
"""
import hashlib
import json
import os
import subprocess
import sys
import urllib.parse

COMMIT = "f5ca01cdcaa2f69b9295a6382719c2d90628780c"
HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    dest = sys.argv[1]
    os.makedirs(dest, exist_ok=True)
    bad = []
    for e in json.load(open(os.path.join(HERE, "results", "freecad_library_manifest.json"))):
        out = os.path.join(dest, e["id"] + ".FCStd")
        url = f"https://raw.githubusercontent.com/FreeCAD/FreeCAD-library/{COMMIT}/" + urllib.parse.quote(e["path"])
        subprocess.run(["curl", "-sfL", "-o", out, url], check=True)
        if hashlib.sha256(open(out, "rb").read()).hexdigest() != e["sha256"]:
            bad.append(e["id"])
    print("checksum mismatches:", bad or "none")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
