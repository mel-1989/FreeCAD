# SPDX-License-Identifier: LGPL-2.1-or-later
"""FreeCAD Python API reference generated from the ``.pyi`` binding stubs.

FreeCAD's C++ Python bindings are declared in ``.pyi`` files under ``src/``
(``App/DocumentObject.pyi``, ``Mod/Part/App/TopoShape.pyi`` and so on). This
module parses them with ``ast`` (no FreeCAD import) into one JSON index:
classes, bases, attributes and methods with signatures and the first paragraph
of each docstring. The MCP server serves it as resources.

Generate (build time; output is committed next to this file and cached):

    python -m AgentCore.mcp.apidocs --src <freecad>/src --out AgentCore/mcp/api_docs.json

At run time ``load()`` uses the committed file; when it is missing and the stub
tree is reachable it generates into ``~/.cache/cadfork-mcp/`` keyed by a hash of
the stub files' paths and contents.
"""

import ast
import hashlib
import json
import os
import sys

API_SCHEMA = "agentcore.mcp.apidocs/1"
_HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGED = os.path.join(_HERE, "api_docs.json")
URI_PREFIX = "cadfork://api/"
DOC_LIMIT = 600


def default_stub_root():
    """``src/`` of the FreeCAD tree this package sits in (src/Mod/AgentCore/AgentCore/mcp)."""
    cand = os.path.normpath(os.path.join(_HERE, "..", "..", "..", ".."))
    return cand if os.path.isdir(os.path.join(cand, "App")) else None


def stub_files(src_root):
    out = []
    for dirpath, dirnames, filenames in os.walk(src_root):
        dirnames[:] = sorted(d for d in dirnames if d not in ("3rdParty", "__pycache__", ".git"))
        for f in sorted(filenames):
            if f.endswith(".pyi"):
                out.append(os.path.join(dirpath, f))
    return out


def tree_hash(src_root):
    h = hashlib.sha256()
    for p in stub_files(src_root):
        h.update(os.path.relpath(p, src_root).replace(os.sep, "/").encode() + b"\0")
        with open(p, "rb") as fh:
            h.update(hashlib.sha256(fh.read()).digest())
    return h.hexdigest()[:16]


def _first_para(doc):
    if not doc:
        return ""
    text = " ".join(doc.strip().split("\n\n")[0].split())
    return text[:DOC_LIMIT]


def _ann(node):
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _signature(fn):
    try:
        args = ast.unparse(fn.args)
    except Exception:
        args = "..."
    ret = _ann(fn.returns)
    return f"({args})" + (f" -> {ret}" if ret else "")


def parse_stub(path, module):
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), path)
    classes = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        methods, attrs = {}, []
        body = node.body
        for i, item in enumerate(body):
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if item.name.startswith("_") and item.name not in ("__init__", "__call__"):
                    continue
                sig = _signature(item)
                doc = _first_para(ast.get_docstring(item))
                deco = [_ann(d) for d in item.decorator_list]
                if item.name in methods:  # @overload: keep every signature, first non-empty doc
                    methods[item.name]["signatures"].append(sig)
                    methods[item.name]["doc"] = methods[item.name]["doc"] or doc
                else:
                    methods[item.name] = {"name": item.name, "signatures": [sig], "doc": doc,
                                          "const": "constmethod" in deco,
                                          "static": "staticmethod" in deco or "classmethod" in deco}
            elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                doc = ""
                if i + 1 < len(body) and isinstance(body[i + 1], ast.Expr) and isinstance(
                        getattr(body[i + 1], "value", None), ast.Constant) and isinstance(body[i + 1].value.value, str):
                    doc = _first_para(body[i + 1].value.value)
                ann = _ann(item.annotation)
                attrs.append({"name": item.target.id, "type": ann, "readonly": ann.startswith("Final"), "doc": doc})
        classes.append({
            "name": node.name, "module": module, "bases": [_ann(b) for b in node.bases],
            "doc": _first_para(ast.get_docstring(node)),
            "attributes": attrs, "methods": list(methods.values()),
        })
    return classes


def generate(src_root):
    src_root = os.path.abspath(src_root)
    files = stub_files(src_root)
    classes = {}
    errors = []
    for p in files:
        rel = os.path.relpath(p, src_root)
        module = os.path.dirname(rel).replace(os.sep, ".") or "root"
        try:
            for c in parse_stub(p, module):
                classes[f"{module}/{c['name']}"] = dict(c, stub=rel)
        except SyntaxError as e:
            errors.append(f"{rel}: {e}")
    return {
        "schema": API_SCHEMA,
        "source_hash": tree_hash(src_root),
        "stub_files": len(files),
        "class_count": len(classes),
        "method_count": sum(len(c["methods"]) for c in classes.values()),
        "errors": errors,
        "classes": dict(sorted(classes.items())),
    }


def write(data, out):
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, separators=(",", ":"), sort_keys=True)
        fh.write("\n")
    os.replace(tmp, out)


def cache_dir():
    return os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "cadfork-mcp")


def load(src_root=None):
    """The API index: committed file, else cached generation, else ``None``."""
    if os.path.exists(PACKAGED):
        with open(PACKAGED, encoding="utf-8") as fh:
            return json.load(fh)
    src_root = src_root or default_stub_root()
    if not src_root:
        return None
    path = os.path.join(cache_dir(), f"api_docs-{tree_hash(src_root)}.json")
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write(generate(src_root), path)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# -- rendering for resources
def index_markdown(data):
    lines = ["# FreeCAD Python API (from the .pyi binding stubs)", "",
             f"{data['class_count']} classes and {data['method_count']} methods from {data['stub_files']} stub files "
             f"(source hash {data['source_hash']}). Read one class with the resource `{URI_PREFIX}<module>/<Class>`.", ""]
    by_mod = {}
    for key, c in data["classes"].items():
        by_mod.setdefault(c["module"], []).append(c)
    for mod in sorted(by_mod):
        lines.append(f"## {mod}")
        lines.append("")
        for c in by_mod[mod]:
            lines.append(f"- `{URI_PREFIX}{mod}/{c['name']}`: {len(c['methods'])} methods. {c['doc'][:120]}")
        lines.append("")
    return "\n".join(lines)


def class_markdown(c):
    lines = [f"# {c['name']}", "", f"Module `{c['module']}`, stub `{c['stub']}`. Bases: "
             + (", ".join(f"`{b}`" for b in c["bases"]) or "none") + ".", ""]
    if c["doc"]:
        lines += [c["doc"], ""]
    if c["attributes"]:
        lines += ["## Attributes", ""]
        for a in c["attributes"]:
            lines.append(f"- `{a['name']}: {a['type']}`" + (f": {a['doc']}" if a["doc"] else ""))
        lines.append("")
    if c["methods"]:
        lines += ["## Methods", ""]
        for m in c["methods"]:
            for s in m["signatures"]:
                lines.append(f"- `{m['name']}{s}`")
            if m["doc"]:
                lines.append(f"  {m['doc']}")
        lines.append("")
    return "\n".join(lines)


def main(argv=None):
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src", default=default_stub_root(), help="FreeCAD src/ directory with the .pyi stubs")
    ap.add_argument("--out", default=PACKAGED)
    a = ap.parse_args(argv)
    if not a.src:
        ap.error("--src not given and no stub tree found next to this package")
    data = generate(a.src)
    write(data, a.out)
    print(f"wrote {a.out}: {data['class_count']} classes, {data['method_count']} methods, "
          f"{data['stub_files']} stubs, {len(data['errors'])} errors, hash {data['source_hash']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
