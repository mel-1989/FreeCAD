# SPDX-License-Identifier: LGPL-2.1-or-later
"""Indented engineering BOM of a vaulted assembly, as of any revision (G03 slice 6a, check 3).

``Vault.bom`` delegates here. The vault state at one commit is exported side by side into a temporary
directory (the way the vault lays documents out, so the relative ``App::Link`` paths resolve), the
assembly is opened with FreeCAD and FreeCAD's own link graph is walked: ``App::Link`` (arrays count
``ElementCount``), ``App::LinkGroup``, ``App::Part`` and Assembly containers, recursing into the linked
vaulted documents as they were at that commit. A weldment document contributes its cut list through the
``WeldBOM.bom_rows()`` contract of G07 slice 5b (imported lazily, so PDM works without the Weldment module).

Row schema of ``bom()`` (a list of plain dicts in tree order, children sorted by name; JSON-safe)
-------------------------------------------------------------------------------------------------
``item``      1-based line number
``level``     0 for the document asked about, 1 for its components, and so on
``name``      part name: the vault document name for a linked vaulted document, the object's Label inside a
              document, the cut-list description for a weldment member, plate or weld
``kind``      ``assembly``, ``part``, ``weldment``, ``member``, ``plate`` or ``weld``
``revision``  revision label of the source document at that commit (``None`` for a document outside the vault)
``quantity``  number of identical children under the same parent (identical siblings are counted, not
              repeated; a link array counts its ``ElementCount``); per parent, so multiply down the tree
              for a total (``flatten`` does that)
``material``  the ``Material`` string of the object or its linked object, else its ``ShapeMaterial`` name, else ``""``
``document``  the source document (vault name)
``object``    the object's name in that document (``None`` for weldment rows)
``extra``     weldment rows: ``family``, ``size``, ``length_mm``, ``mass_kg``; ``{}`` otherwise

Identical siblings are those with the same name, kind, source document and object; the subtree of the first
is kept. Quantities multiply through links (a link to a link array, a link to a link). A link to a sub-object
(``setLink(part, "Inner.X.")``) lists that sub-object. Policy for hidden and excluded objects: the EBOM lists
every component regardless of ``Visibility`` (a view state; hidden elements of a ``ShowElement`` array still
count), but an object with a true ``Suppressed`` property or ``ExcludeFromAssembly`` flag, and links to it, are
left out. A document that contributes more than one distinct part to a BOM names them ``document:label``. Only ``kind == "EBOM"`` exists; an MBOM is refused.
"""

import csv
import io
import os
import shutil
import subprocess
import tempfile

from PDM import vault as V

CSV_COLUMNS = ("level", "name", "kind", "revision", "quantity", "material", "document")
FLAT_COLUMNS = ("name", "kind", "revision", "quantity", "material", "document")
_MAX_DEPTH = 64


def _stem(doc):
    base = os.path.basename((doc.FileName or "").rstrip("/\\"))
    return base[:-len(V.DOC_SUFFIX)] if base.endswith(V.DOC_SUFFIX) else (base or doc.Label)


class Tree:
    """The vault tree at one commit, exported to a temp dir; documents opened on demand, all closed on exit."""

    def __init__(self, vault, commit):
        self.vault = vault
        self.commit = commit
        self._revs = {}
        self.base = None

    def __enter__(self):
        import FreeCAD
        self.base = os.path.realpath(tempfile.mkdtemp(prefix="pdm-bom-"))  # a symlinked path would open links twice
        arch = V._git(["archive", "--format=tar", self.commit], cwd=self.vault.workdir)
        subprocess.run(["tar", "-x", "-C", self.base], input=arch.stdout, check=True)
        self._before = set(FreeCAD.listDocuments())
        return self

    def __exit__(self, *exc):
        import FreeCAD
        for n in list(FreeCAD.listDocuments()):
            if n not in self._before:
                try:
                    FreeCAD.closeDocument(n)
                except Exception:
                    pass
        shutil.rmtree(self.base, ignore_errors=True)

    def _links_of(self, name):
        xml = self.vault._document_xml(self.commit, name)
        if xml is None:
            return []
        out = []
        for _obj, _prop, f in self.vault._scan_xlinks(xml):
            tgt = self.vault._link_target(f)
            if tgt is not None and tgt != name and tgt not in out:
                out.append(tgt)
        return sorted(out)

    def open(self, name):
        """Open ``name`` after every vault document it links to (transitively, dependencies first). FreeCAD
        loads a document that is only reached through a link partially (just the linked objects), so the
        linked documents are opened explicitly, whole, before the document that links to them."""
        import FreeCAD
        order, seen = [], set()

        def visit(n):
            if n in seen:
                return
            seen.add(n)
            if not os.path.isdir(os.path.join(self.base, n + V.DOC_SUFFIX)):
                if n == name:
                    raise V.DocumentError(f"{name} is not in the vault at {self.commit[:10]}")
                raise V.DocumentError(f"{name} links to {n}, which is not in the vault at {self.commit[:10]}")
            for dep in self._links_of(n):
                visit(dep)
            order.append(n)

        visit(name)
        docs = {}
        for n in order:
            path = os.path.join(self.base, n + V.DOC_SUFFIX)
            docs[n] = next((d for d in FreeCAD.listDocuments().values()
                            if os.path.realpath(d.FileName.rstrip("/\\")) == path), None) or FreeCAD.openDocument(path)
        for _ in range(3):  # a document opened as the target of a link in a cycle is only partially loaded
            partial = [n for n in order if docs[n].Partial]
            if not partial:
                break
            for n in partial:
                path = docs[n].FileName
                FreeCAD.closeDocument(docs[n].Name)
                docs[n] = FreeCAD.openDocument(path)
        return docs[name]

    def revision(self, stem):
        """Revision label of a vault document as of this commit, ``None`` when it is not a vault document."""
        if stem not in self._revs:
            recs = self.vault._records(stem, ref=self.commit)
            self._revs[stem] = recs[0]["revision"] if recs else None
        return self._revs[stem]


# ---- object classification ---------------------------------------------------------------------------

def _is_link(o):
    return o.TypeId in ("App::Link", "App::LinkElement")


def _is_linkgroup(o):
    return o.TypeId == "App::LinkGroup"


def _is_container(o):
    return o.TypeId == "App::Part" or o.TypeId.startswith("Assembly::AssemblyObject")


def _is_component(o):
    if _is_link(o) or _is_linkgroup(o) or _is_container(o) or o.TypeId == "PartDesign::Body":
        return True
    shp = getattr(o, "Shape", None)
    return o.isDerivedFrom("Part::Feature") and shp is not None and not shp.isNull() and len(shp.Solids) > 0


def _material(o):
    for obj in (o, getattr(o, "LinkedObject", None) if _is_link(o) else None):
        if obj is None:
            continue
        m = getattr(obj, "Material", None)
        if isinstance(m, str) and m:
            return m
        sm = getattr(obj, "ShapeMaterial", None)
        name = getattr(sm, "Name", "") if sm is not None else ""
        if name and name != "Default":  # FreeCAD's "Default" is what a part has when no material was assigned
            return name
    return ""


def _excluded(o):
    """Left out of the EBOM: a true ``Suppressed`` property or an ``ExcludeFromAssembly``-style flag. Visibility is a
    view state and never excludes (see the README)."""
    return any(getattr(o, p, False) is True for p in ("Suppressed", "ExcludeFromAssembly"))


def _link_target(link):
    """The object a link points at; a link to a sub-object (``LinkedObject`` is ``(object, subname)``) resolves
    through ``getSubObject``. None when the link has no target."""
    t = link.LinkedObject
    if isinstance(t, tuple):
        obj, sub = t
        if obj is not None and sub:
            return obj.getSubObject(sub, 1)
        return obj
    return t


def _is_sub_link(o):
    return _is_link(o) and isinstance(o.LinkedObject, tuple) and bool(o.LinkedObject[1])


def _top_objects(doc):
    """Components of a document that nothing in the same document uses. A link from another document, or a link
    to a sub-object, does not make its target 'used'."""
    return [o for o in doc.Objects if _is_component(o) and not _excluded(o)
            and not any(i.Document is doc and not _is_sub_link(i) for i in o.InList)]


def _weldment_module():
    try:
        import WeldBOM
    except Exception:
        return None
    return WeldBOM


def _is_weldment_doc(doc):
    wb = _weldment_module()
    return wb is not None and any(wb._is_member(o) for o in doc.Objects)


# ---- the walk ----------------------------------------------------------------------------------------

class _Walker:
    def __init__(self, tree):
        self.tree = tree

    def node(self, name, kind, doc, obj, qty=1, material="", children=None, extra=None, label=None):
        stem = _stem(doc) if doc is not None else None
        return {"label": label, "name": name, "kind": kind, "document": stem, "object": obj,
                "revision": self.tree.revision(stem) if stem else None, "quantity": qty,
                "material": material, "extra": extra or {}, "children": children or []}

    def doc_children(self, doc, stack):
        """Components at the top level of a document: objects nothing else uses."""
        return self.merge([self.expand(o, stack) for o in _top_objects(doc)])

    def weld_children(self, doc):
        stem = _stem(doc)
        rev = self.tree.revision(stem)
        out = []
        for r in _weldment_module().bom_rows(doc):
            out.append({"name": r["description"], "kind": r["kind"], "document": stem, "object": None,
                        "revision": rev, "quantity": r["quantity"], "material": r["material"] or "",
                        "extra": {"family": r["family"], "size": r["size"], "length_mm": r["length_mm"],
                                  "mass_kg": r["mass_kg"]}, "children": []})
        return out

    def root(self, name):
        doc = self.tree.open(name)
        if _is_weldment_doc(doc):
            return self.node(name, "weldment", doc, None, children=self.weld_children(doc))
        top = _top_objects(doc)
        kids = self.doc_children(doc, ())
        if (len(top) == 1 and len(kids) == 1 and not (_is_link(top[0]) or _is_linkgroup(top[0]))
                and kids[0]["kind"] == "part" and kids[0]["quantity"] == 1):
            # a document that is one part is that part, not an assembly of one
            return self.node(name, "part", doc, kids[0]["object"], material=kids[0]["material"])
        return self.node(name, "assembly" if kids else "part", doc, None, children=kids)

    def expand(self, o, stack):
        key = (_stem(o.Document), o.Name)
        if key in stack or len(stack) > _MAX_DEPTH:
            raise V.DocumentError(f"link cycle through {key[0]}:{key[1]}")
        stack = stack + (key,)
        if _is_link(o):
            tgt = _link_target(o)
            if tgt is None:
                raise V.DocumentError(f"{key[0]}:{o.Name} is a link with no target")
            if _excluded(tgt):
                return None
            qty = int(o.ElementCount) if getattr(o, "ElementCount", 0) and o.ElementCount > 0 else 1
            n = self.expand_target(tgt, stack, o.Document)
            if n is None:
                return None
            n["quantity"] *= qty  # a link to a link array, or a link to a link, multiplies
            n["material"] = _material(o) or n["material"]
            return n
        if _is_linkgroup(o):
            kids = self.merge([self.expand(e, stack) for e in o.ElementList if _is_link(e) and not _excluded(e)])
            return self.node(o.Label, "assembly", o.Document, o.Name, material=_material(o), children=kids)
        if _excluded(o):
            return None
        return self.expand_target(o, stack, o.Document)

    def expand_target(self, t, stack, from_doc):
        if _is_link(t) or _is_linkgroup(t):
            return self.expand(t, stack)
        cross = t.Document is not from_doc and _stem(t.Document) != _stem(from_doc)
        name = _stem(t.Document) if cross else t.Label
        if cross and _is_weldment_doc(t.Document):
            return self.node(name, "weldment", t.Document, t.Name, material=_material(t),
                             children=self.weld_children(t.Document))
        if _is_container(t):
            kids = self.merge([self.expand(c, stack) for c in t.Group if _is_component(c) and not _excluded(c)])
            return self.node(name, "assembly", t.Document, t.Name, material=_material(t), children=kids)
        return self.node(name, "part", t.Document, t.Name, material=_material(t), label=t.Label if cross else None)

    @staticmethod
    def merge(nodes):
        """Identical siblings counted once with a summed quantity; deterministic order."""
        by = {}
        for n in nodes:
            if n is None:
                continue
            k = (n["name"], n["kind"], n["document"], n["object"] or "")
            if k in by:
                by[k]["quantity"] += n["quantity"]
            else:
                by[k] = n
        return [by[k] for k in sorted(by)]


def _rows(node, level, out):
    out.append({"item": len(out) + 1, "level": level, "name": node["name"], "kind": node["kind"],
                "revision": node["revision"], "quantity": node["quantity"], "material": node["material"],
                "document": node["document"], "object": node["object"], "extra": node["extra"]})
    for c in node["children"]:
        _rows(c, level + 1, out)


def _unique_names(node):
    """A document that contributes more than one distinct part is named ``document:object label`` for each, so two
    different linked parts of one document are not both called after the document."""
    seen = {}

    def collect(n):
        if n.get("label") is not None:
            seen.setdefault(n["document"], set()).add(n["object"])
        for c in n["children"]:
            collect(c)

    def apply(n):
        if n.get("label") is not None and len(seen[n["document"]]) > 1:
            n["name"] = f"{n['document']}:{n['label']}"
        for c in n["children"]:
            apply(c)

    collect(node)
    apply(node)


def rows_of_node(node):
    out = []
    _rows(node, 0, out)
    return out


def bom_at(tree, name):
    """Indented BOM rows of document ``name`` inside an open ``Tree``."""
    node = _Walker(tree).root(name)
    _unique_names(node)
    return rows_of_node(node)


def bom(vault, name, revision=None, kind="EBOM"):
    if kind != "EBOM":
        raise V.PDMError(f"BOM kind {kind!r} is not available: only the engineering BOM (EBOM) is implemented")
    V._check_name(name)
    vault._fetch()
    if revision is None:
        vault._require_present(name)
        commit, root = vault._head(), name
    else:
        rec = vault.find_revision(name, revision)
        commit, root = rec["sha"], rec.get("document") or name
    with Tree(vault, commit) as tree:
        return bom_at(tree, root)


# ---- flat BOM, CSV, delta ----------------------------------------------------------------------------

def flatten(rows):
    """Summed BOM: every leaf row with its quantity multiplied down the tree, equal leaves added."""
    mult = []
    acc = {}
    for i, r in enumerate(rows):
        del mult[r["level"]:]
        mult.append(r["quantity"])
        leaf = i + 1 == len(rows) or rows[i + 1]["level"] <= r["level"]
        if not leaf:
            continue
        total = 1
        for q in mult:
            total *= q
        k = (r["name"], r["kind"], r["document"] or "", r.get("object") or "", r["revision"] or "", r["material"])
        if k in acc:
            acc[k]["quantity"] += total
        else:
            acc[k] = {"name": r["name"], "kind": r["kind"], "revision": r["revision"], "quantity": total,
                      "material": r["material"], "document": r["document"]}
    return [acc[k] for k in sorted(acc)]


def to_csv(rows, flat=False):
    """Deterministic CSV text (``\\n`` line ends, fixed columns) of an indented BOM, or of its flat sum."""
    cols = FLAT_COLUMNS if flat else CSV_COLUMNS
    data = flatten(rows) if flat else rows
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(cols)
    for r in data:
        w.writerow(["" if r[c] is None else r[c] for c in cols])
    return buf.getvalue()


def _paths(rows):
    """Component rows below level 0 -> unique path key ``a/b/c`` of names below the document (a repeated key gets
    ``#2``...), so a document renamed by a move compares equal to itself."""
    stack, out, seen = [], {}, {}
    for r in rows:
        del stack[r["level"]:]
        stack.append(r["name"])
        if r["level"] == 0:
            continue
        p = "/".join(stack[1:])
        seen[p] = seen.get(p, 0) + 1
        out[p if seen[p] == 1 else f"{p}#{seen[p]}"] = r
    return out


def delta(rows_a, rows_b):
    """Components added, removed, or changed in quantity, revision or material between two indented BOMs of one
    document (its own level-0 row is not compared)."""
    a, b = _paths(rows_a), _paths(rows_b)
    slim = lambda p, r: {"path": p, "level": r["level"], "name": r["name"], "kind": r["kind"],
                         "revision": r["revision"], "quantity": r["quantity"], "material": r["material"],
                         "document": r["document"]}
    changed = []
    for p in sorted(set(a) & set(b)):
        diffs = {k: [a[p][k], b[p][k]] for k in ("quantity", "revision", "material") if a[p][k] != b[p][k]}
        if diffs:
            changed.append({"path": p, "name": b[p]["name"], "level": b[p]["level"], "document": b[p]["document"],
                            "changes": diffs})
    return {"added": [slim(p, b[p]) for p in sorted(set(b) - set(a))],
            "removed": [slim(p, a[p]) for p in sorted(set(a) - set(b))], "changed": changed}
