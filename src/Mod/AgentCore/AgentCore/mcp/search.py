# SPDX-License-Identifier: LGPL-2.1-or-later
"""Local parts-corpus search for ``search_parts`` (pure stdlib, no FreeCAD).

Indexes ``.FCStd`` (object names, labels and types read from the archive's
``Document.xml``), ``.step``/``.stp`` (header ``FILE_NAME`` and ``PRODUCT``
names) and ``.brep`` (file name only) under the configured roots. Scoring is
token overlap, weighted: file name 3, label or product 2, object name 1.5, type 1.
The index is rebuilt when a root's file list or mtimes change.
"""

import os
import re
import xml.etree.ElementTree as ET
import zipfile

EXTS = (".fcstd", ".step", ".stp", ".brep", ".brp")
_TOKEN = re.compile(r"[A-Za-z]+|\d+")


def tokens(s):
    out = []
    for t in _TOKEN.findall(s or ""):
        # split camelCase / PascalCase too
        out.extend(p.lower() for p in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", t) or [t])
        out.append(t.lower())
    return out


def _fcstd_meta(path):
    objs = []
    try:
        with zipfile.ZipFile(path) as z:
            root = ET.fromstring(z.read("Document.xml"))
    except Exception as e:
        return {"objects": [], "error": f"{type(e).__name__}: {e}"}
    types = {}
    for o in root.iter("Object"):
        if o.get("type") and o.get("name"):
            types[o.get("name")] = o.get("type")
    labels = {}
    for od in root.iter("Object"):
        name = od.get("name")
        if not name or od.find("Properties") is None:
            continue
        for p in od.find("Properties").iter("Property"):
            if p.get("name") == "Label":
                s = p.find("String")
                if s is not None:
                    labels[name] = s.get("value", "")
    for name, typ in types.items():
        objs.append({"name": name, "type": typ, "label": labels.get(name, name)})
    return {"objects": objs}


def _step_meta(path):
    try:
        with open(path, "rb") as fh:
            head = fh.read(65536).decode("latin-1", "replace")
    except OSError as e:
        return {"objects": [], "error": str(e)}
    names = re.findall(r"PRODUCT\s*\(\s*'([^']*)'", head)
    fn = re.findall(r"FILE_NAME\s*\(\s*'([^']*)'", head)
    return {"objects": [{"name": n, "type": "STEP product", "label": n} for n in dict.fromkeys(names)],
            "file_name": fn[0] if fn else ""}


class PartsIndex:
    def __init__(self, roots):
        self.roots = [os.path.abspath(os.path.expanduser(r)) for r in roots if r]
        self._sig = None
        self.entries = []

    def _scan(self):
        files = []
        for root in self.roots:
            if os.path.isfile(root) and root.lower().endswith(EXTS):
                files.append(root)
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d != "__pycache__")
                for f in sorted(filenames):
                    if f.lower().endswith(EXTS):
                        files.append(os.path.join(dirpath, f))
        return files

    def refresh(self):
        files = self._scan()
        sig = tuple((f, int(os.path.getmtime(f))) for f in files)
        if sig == self._sig:
            return
        entries = []
        for f in files:
            low = f.lower()
            if low.endswith(".fcstd"):
                meta, fmt = _fcstd_meta(f), "FCStd"
            elif low.endswith((".step", ".stp")):
                meta, fmt = _step_meta(f), "STEP"
            else:
                meta, fmt = {"objects": []}, "BREP"
            entries.append({"path": f, "format": fmt, "bytes": os.path.getsize(f), **meta})
        self.entries = entries
        self._sig = sig

    def search(self, query, limit=10, formats=None):
        self.refresh()
        q = set(tokens(query))
        fmts = {x.lower() for x in formats} if formats else None
        hits = []
        for e in self.entries:
            if fmts and e["format"].lower() not in fmts:
                continue
            stem = os.path.splitext(os.path.basename(e["path"]))[0]
            score = 3.0 * len(q & set(tokens(stem)))
            matched = []
            for o in e.get("objects", []):
                s = 2.0 * len(q & set(tokens(o["label"]))) + 1.5 * len(q & set(tokens(o["name"]))) \
                    + 1.0 * len(q & set(tokens(o["type"].split("::")[-1])))
                if s:
                    matched.append({**o, "score": s})
            score += sum(m["score"] for m in matched[:5])
            if q and score == 0:
                continue
            matched.sort(key=lambda m: (-m["score"], m["name"]))
            hits.append({"path": e["path"], "format": e["format"], "score": round(score, 2),
                         "object_count": len(e.get("objects", [])), "matches": matched[:8]})
        hits.sort(key=lambda h: (-h["score"], h["path"]))
        return {"query": query, "roots": self.roots, "indexed": len(self.entries), "results": hits[:int(limit)]}
