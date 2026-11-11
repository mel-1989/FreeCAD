# SPDX-License-Identifier: LGPL-2.1-or-later

# **************************************************************************
#   Sheet sets (G04 slice 6): the title blocks of a drawing set kept right  *
#   as sheets are added, removed and reordered and as the document and the  *
#   model change.                                                           *
#                                                                           *
#   Public sources: the ISO 7200 data-field names as carried in the         *
#   bundled templates (field names only, in TitleBlockKeys.json), the       *
#   templates themselves, and FreeCAD's own autofill code                   *
#   (DrawTemplate::getAutofillValue, DrawUtil::nearestFraction) for the     *
#   conventions stock follows.  No standard text is copied and no           *
#   compliance is claimed.                                                  *
# **************************************************************************

"""A set of drawing pages whose title blocks stay consistent.

    import SheetSet
    sset = SheetSet.make(doc, pages=[p1, p2], source=body, fields={"drawing_number": "DN-100", "title": "Bracket"})
    SheetSet.add_sheet(sset)                    # a new page on the last sheet's template; every sheet now says n / N
    SheetSet.move_sheet(sset, p2, 0)            # the order of ``Pages`` is the sheet order
    SheetSet.remove_sheet(sset, p1)
    SheetSet.fields_of(sset, p1)                # {canonical field: value} as written in the sheet's title block
    SheetSet.unpin(sset, p1, "sheet")           # release a sheet field the user typed over

The driver is a ``App::FeaturePython`` with ``Pages`` (ordered), ``Source`` (a model object whose ``ShapeMaterial`` and ``Shape``
give material and weight, and whose ``PartNumber`` string gives the drawing number), and ``Fields`` (canonical field name to
value).  A plain ``doc.recompute()`` writes, into each template's ``EditableTexts``, the sheet number and total, title,
subtitle, drawing number, scale, author, checker, date, company, material, weight, document type, tolerance and revision, under
whatever key each template calls them (``TitleBlockKeys.json``).  A shared field a user types into one sheet's title block
becomes ``Fields[name]`` and reaches every sheet; a per-sheet field (sheet, scale) a user types is kept and listed in
``Warning`` until ``unpin``.
"""

import datetime
import json
import os
import re

import FreeCAD as App

import HoleTable as HT

CANONICAL = (
    "title",
    "subtitle",
    "drawing_number",
    "sheet",
    "sheet_number",
    "sheet_total",
    "scale",
    "author",
    "checker",
    "date",
    "company",
    "material",
    "weight",
    "document_type",
    "tolerance",
    "revision",
)
PER_SHEET = ("sheet", "sheet_number", "sheet_total", "scale")
REVISION_KEYS = ("revision_index", "date_of_issue", "issue_date")  # RevisionTable's own, on a page that has one

_SHEET_DEFAULT = re.compile(r"^(?P<pre>.*?)(?P<n>\d+)(?P<sep>\s*(?:/|of|OF|von|de|di)\s*)(?P<m>\d+)(?P<suf>.*)$", re.S)
_KEYS = {}
_TEMPLATES = {}


# ------------------------------------------------------------------------------------------------
# the key map
# ------------------------------------------------------------------------------------------------


def keys_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "TitleBlockKeys.json")


def key_data():
    """The parsed ``TitleBlockKeys.json``: synonyms per canonical field, and per-template resolved maps with overrides."""
    d = _KEYS.get("data")
    if d is None:
        with open(keys_path(), encoding="utf-8") as fh:
            d = _KEYS["data"] = json.load(fh)
    return d


def norm(key):
    return re.sub(r"[\s_\-]+", "", key).lower()


def template_fields(path):
    """-> [(key, autofill id, default text)] of the editable text elements of the template file ``path`` (cached by mtime)."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return []
    hit = _TEMPLATES.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    import xml.etree.ElementTree as ET

    out = []
    try:
        root = ET.parse(path).getroot()
        for el in root.iter():
            if el.tag.rsplit("}", 1)[-1] != "text":
                continue
            attrs = {k.rsplit("}", 1)[-1]: v for k, v in el.attrib.items()}
            if "editable" in attrs:
                out.append((attrs["editable"], attrs.get("autofill", ""), "".join(el.itertext()).strip()))
    except Exception:
        out = []
    _TEMPLATES[path] = (mtime, out)
    return out


def classify(fields, exclude=(), add=None, data=None):
    """-> (``{key: canonical}``, ``[excluded keys]``) for the editable ``fields`` of one template.

    A key is matched by its normalised spelling against the synonym table, else by its ``freecad:autofill`` id; ``exclude`` names keys
    a template carries that are not what their name says, ``add`` maps further keys.
    """
    data = data or key_data()
    syn = {}
    for canon, names in data["synonyms"].items():
        for n in names:
            syn[n] = canon
    auto = data["autofill"]
    mapped, excluded = {}, []
    for key, au, _ in fields:
        if key in exclude:
            excluded.append(key)
            continue
        c = (add or {}).get(key) or syn.get(norm(key)) or auto.get(au)
        if c:
            mapped[key] = c
    return mapped, excluded


def _rel_template(path):
    p = path.replace("\\", "/")
    i = p.rfind("/Templates/")
    return p[i + len("/Templates/") :] if i >= 0 else os.path.basename(p)


def key_map(path):
    """-> (``{key: canonical}``, ``[excluded keys]``) of the template file ``path``: its entry in the data file, else by synonyms."""
    data = key_data()
    ent = data["templates"].get(_rel_template(path))
    if ent is not None:
        return dict(ent["keys"]), list(ent.get("excluded", []))
    return classify(template_fields(path), data=data)


def default_text(path, key):
    for k, _, d in template_fields(path):
        if k == key:
            return d
    return ""


# ------------------------------------------------------------------------------------------------
# values
# ------------------------------------------------------------------------------------------------


def nearest_fraction(val, maxden=999):
    """DrawUtil::nearestFraction: the best rational approximation a/b of ``val`` with b <= ``maxden`` (continued fractions)."""
    m = [[1, 0], [0, 1]]
    x = float(val)
    startx = x
    while m[1][0] * int(x) + m[1][1] <= maxden:
        ai = int(x)
        t = m[0][0] * ai + m[0][1]
        m[0][1] = m[0][0]
        m[0][0] = t
        t = m[1][0] * ai + m[1][1]
        m[1][1] = m[1][0]
        m[1][0] = t
        if x == float(ai):
            break
        x = 1 / (x - ai)
        if x > 2147483647:
            break
    e1 = startx - m[0][0] / float(m[1][0])
    n1, d1 = m[0][0], m[1][0]
    ai = (maxden - m[1][1]) // m[1][0]
    m[0][0] = m[0][0] * ai + m[0][1]
    m[1][0] = m[1][0] * ai + m[1][1]
    e2 = startx - m[0][0] / float(m[1][0])
    return (n1, d1) if abs(e1) <= abs(e2) else (m[0][0], m[1][0])


def scale_text(value):
    n, d = nearest_fraction(value)
    return "%d : %d" % (n, d)


def sheet_text(default, n, total):
    """Ruling 61: keep the default's prefix, separator and suffix when it reads ``number separator number``, else ``n / N``."""
    m = _SHEET_DEFAULT.match(default or "")
    if m:
        return "%s%d%s%d%s" % (m.group("pre"), n, m.group("sep"), total, m.group("suf"))
    return "%d / %d" % (n, total)


def page_scale(page):
    """The ``Scale`` of the page's first projection group or part view in its ``Views`` order, else the page's own."""
    for v in page.Views:
        if v.isDerivedFrom("TechDraw::DrawProjGroup") or v.isDerivedFrom("TechDraw::DrawViewPart"):
            return float(v.Scale)
    return float(page.Scale)


def model_values(source):
    """-> (material name or None, weight text or None) of the source object; ``Default`` counts as no material."""
    if source is None:
        return None, None
    mat = getattr(source, "ShapeMaterial", None)
    name = getattr(mat, "Name", None) if mat is not None else None
    if not name or name == "Default":
        return None, None
    weight = None
    try:
        shape = source.Shape
        if not shape.isNull():
            # PhysicalProperties of the property's own copy; getPhysicalValue on it would leave the source object Touched
            dens = mat.PhysicalProperties.get("Density") if hasattr(mat.PhysicalProperties, "get") else None
            if dens is None:  # a card that inherits its density: the library's own copy knows it
                import Materials

                dens = Materials.MaterialManager().getMaterial(mat.UUID).getPhysicalValue("Density")
            if not isinstance(dens, App.Units.Quantity):
                dens = App.Units.Quantity(str(dens))
            kg = shape.Volume * dens.getValueAs("kg/mm^3").Value  # plain float arithmetic: volume in mm^3 times density in kg/mm^3
            weight = "%.3f kg" % kg
    except Exception:
        weight = None
    return name, weight


def newest_revision(page):
    """-> (rev, date) of the newest row of the page's revision table, or None when it has none or no row yet."""
    import RevisionTable as RT

    for d in RT.drivers_on(page):
        if d.Sheet is not None:
            rows = RT.read_rows(d.Sheet)
            if len(rows) > 1:
                return rows[-1][1], rows[-1][3]
    return None


def has_revision_table(page):
    import RevisionTable as RT

    return bool(RT.drivers_on(page))


# ------------------------------------------------------------------------------------------------
# a template set (or set again) re-runs stock autofill and replaces every field: that is not a user edit
# ------------------------------------------------------------------------------------------------

_RESET = set()


class _TemplateWatch:
    def slotChangedObject(self, obj, prop):
        if prop == "Template" and obj.TypeId == "TechDraw::DrawSVGTemplate":
            _RESET.add((obj.Document.Name, obj.Name))


_WATCH = _TemplateWatch()
App.addDocumentObserver(_WATCH)


# ------------------------------------------------------------------------------------------------
# the driver
# ------------------------------------------------------------------------------------------------


def _load_state(obj):
    try:
        s = json.loads(obj.State) if obj.State else {}
    except ValueError:
        s = {}
    for k, v in (("date", ""), ("written", {}), ("pinned", {}), ("file", {}), ("conflicts", [])):
        s.setdefault(k, v)
    return s


def _label(page):
    return page.Label or page.Name


class SheetSetDriver:
    """Python feature that writes the set's title-block fields into every page's template on each recompute."""

    def __init__(self, obj):
        obj.Proxy = self
        add = obj.addProperty
        add("App::PropertyLinkList", "Pages", "SheetSet", "The sheets in order; this order is the sheet order")
        add("App::PropertyLink", "Source", "SheetSet", "The model object whose material, weight and PartNumber the title blocks show")
        add("App::PropertyMap", "Fields", "SheetSet", "Canonical field name to value; a field set here overrides its default")
        add("App::PropertyString", "Warning", "SheetSet", "What the set could not do or keeps as the user left it, or empty")
        add("App::PropertyString", "State", "SheetSet", "What the driver last wrote per sheet and key, pinned fields, the set's date (JSON)")
        obj.setEditorMode("State", 2)

    def dumps(self):
        return None

    def loads(self, state):
        return None

    def onDocumentRestored(self, obj):
        obj.Proxy = self

    def mustExecute(self, obj):
        # the document's Label, CreatedBy and Company touch no object: look at them every recompute (the work is a few dict compares)
        return 1

    def execute(self, obj):
        doc = obj.Document
        state = _load_state(obj)
        pages = [p for p in obj.Pages if p is not None]
        total = len(pages)
        fields = dict(obj.Fields)
        warnings = []
        # a page that left the set is forgotten
        names = {p.Name for p in pages}
        for sec in ("written", "pinned", "file"):
            for n in [n for n in state[sec] if n not in names]:
                del state[sec][n]

        # 1. what the user typed since we last wrote
        plans = []
        edits = {}  # canonical -> [(index, page, key, value)]
        for i, page in enumerate(pages, start=1):
            tpl = page.Template
            if tpl is None or not hasattr(tpl, "EditableTexts"):
                warnings.append("%s: no SVG template, left as it is" % _label(page))
                continue
            path = tpl.Template
            kmap, excluded = key_map(path)
            cur = dict(tpl.EditableTexts)
            wr = state["written"].setdefault(page.Name, {})
            pins = state["pinned"].setdefault(page.Name, [])
            reset = (doc.Name, tpl.Name) in _RESET
            _RESET.discard((doc.Name, tpl.Name))
            if reset or state["file"].get(page.Name) != path:  # a template set (or re-set) re-ran stock autofill: nothing here is a user edit
                state["file"][page.Name] = path
                wr.clear()
                del pins[:]
            revown = has_revision_table(page)
            keys = {k: c for k, c in kmap.items() if not (revown and (k in REVISION_KEYS))}
            if not kmap:
                warnings.append("%s: template %s has no title-block key SheetSet maps, left as it is" % (_label(page), os.path.basename(path)))
            for k in excluded:
                warnings.append("%s: key %s is excluded from the map (it is not what its name says), left as it is" % (_label(page), k))
            changed = [k for k in keys if wr.get(k) is not None and cur.get(k, wr[k]) != wr[k]]
            for k in changed:
                c = keys[k]
                if c in PER_SHEET:
                    if c not in pins:
                        pins.append(c)
                else:
                    edits.setdefault(c, []).append((i, page, k, cur[k]))
            plans.append((i, page, tpl, path, keys, cur, wr, pins, revown))

        # 2. shared fields typed on a sheet become Fields; the earlier sheet wins a conflict
        changed_fields = False
        conflicts = []
        for c, lst in edits.items():
            lst.sort(key=lambda t: t[0])
            _, wpage, _, val = lst[0]
            if fields.get(c) != val:
                fields[c] = val
                changed_fields = True
            for _, page, k, v in lst[1:]:
                if v != val:
                    conflicts.append("%s: typed differently on %s (%s); the value from %s is kept" % (c, _label(page), k, _label(wpage)))
        if changed_fields:
            obj.Fields = fields
        if edits:  # the second pass of a recompute no longer sees the edit: keep what was said about it until the next edit
            state["conflicts"] = conflicts

        # 3. the values
        if not state["date"]:
            state["date"] = datetime.date.today().isoformat()
        source = obj.Source
        shared = {}
        shared["title"] = fields.get("title", doc.Label or None)
        shared["author"] = fields.get("author", doc.CreatedBy or None)
        shared["company"] = fields.get("company", doc.Company or None)
        shared["date"] = fields.get("date", state["date"])
        pn = getattr(source, "PartNumber", None) if source is not None else None
        shared["drawing_number"] = fields.get("drawing_number", pn if isinstance(pn, str) and pn else None)
        mat, weight = model_values(source)
        shared["material"] = fields.get("material", mat)
        shared["weight"] = fields.get("weight", weight)
        for c in ("subtitle", "checker", "document_type", "tolerance"):
            shared[c] = fields.get(c)
        set_rev = None  # the first sheet of the set that has a revision row
        for page in pages:
            set_rev = newest_revision(page)
            if set_rev:
                break
        shared["revision"] = fields.get("revision", set_rev[0] if set_rev else None)

        # 4. write
        for i, page, tpl, path, keys, cur, wr, pins, revown in plans:
            own = newest_revision(page) if revown else None
            new = dict(cur)
            for k, c in keys.items():
                if c in pins:
                    continue
                if c == "sheet":
                    v = sheet_text(default_text(path, k), i, total)
                elif c == "sheet_number":
                    v = str(i)
                elif c == "sheet_total":
                    v = str(total)
                elif c == "scale":
                    v = scale_text(page_scale(page))
                elif c == "revision" and revown:
                    v = own[0] if own else None
                else:
                    v = shared.get(c)
                if v is None:
                    continue
                new[k] = v
                wr[k] = v
            if new != cur:
                tpl.EditableTexts = new
            for c in pins:
                warnings.append("%s: %s was typed by the user and is kept (SheetSet.unpin releases it)" % (_label(page), c))

        text = " | ".join(warnings + list(state.get("conflicts", [])))
        if obj.Warning != text:
            obj.Warning = text
        js = json.dumps(state, sort_keys=True)
        if obj.State != js:
            obj.State = js


# ------------------------------------------------------------------------------------------------
# public API
# ------------------------------------------------------------------------------------------------


def _settle(doc, drv):
    drv.touch()
    doc.recompute()
    if any("Touched" in o.State for o in doc.Objects if o.TypeId in ("TechDraw::DrawPage", "TechDraw::DrawSVGTemplate") or o == drv):
        doc.recompute()


def drivers(doc):
    return [o for o in doc.Objects if o.TypeId == "App::FeaturePython" and isinstance(getattr(o, "Proxy", None), SheetSetDriver)]


def make(doc, pages=None, source=None, fields=None):
    """Make a sheet set of ``pages`` (default: every page of the document, in document order); -> the driver.  One undo step."""
    own = HT.begin(doc, "Sheet set")
    try:
        drv = doc.addObject("App::FeaturePython", "SheetSet")
        SheetSetDriver(drv)
        drv.Label = "Sheet set"
        drv.Pages = list(pages) if pages is not None else list(doc.findObjects("TechDraw::DrawPage"))
        drv.Source = source
        drv.Fields = {str(k): str(v) for k, v in (fields or {}).items()}
        st = _load_state(drv)
        st["date"] = datetime.date.today().isoformat()  # the day the set was made, kept: never the clock at a later recompute
        drv.State = json.dumps(st, sort_keys=True)
        _settle(doc, drv)
    except Exception:
        HT.fail(doc, own)
        raise
    HT.end(doc, own)
    return drv


def add_sheet(sset, template=None, label=None):
    """Add a page on ``template`` (default: the file of the last sheet) at the end of the set; -> the page.  One undo step."""
    doc = sset.Document
    if template is None:
        last = [p for p in sset.Pages if p is not None and p.Template is not None]
        if not last:
            raise ValueError("add_sheet: the set has no sheet to take a template from; pass template=")
        template = last[-1].Template.Template
    if not os.path.isabs(template) and not os.path.exists(template):
        template = os.path.join(App.getResourceDir(), "Mod", "TechDraw", "Templates", template)
    own = HT.begin(doc, "Add sheet")
    try:
        page = doc.addObject("TechDraw::DrawPage", "Page")
        t = doc.addObject("TechDraw::DrawSVGTemplate", "Template")
        page.Template = t  # link first: stock autofill numbers the page by its place in the template's InList
        t.Template = template
        if label:
            page.Label = label
        sset.Pages = list(sset.Pages) + [page]
        _settle(doc, sset)
    except Exception:
        HT.fail(doc, own)
        raise
    HT.end(doc, own)
    return page


def remove_sheet(sset, page):
    """Delete ``page`` as stock does (and its template) and take it out of the set.  One undo step."""
    doc = sset.Document
    own = HT.begin(doc, "Remove sheet")
    try:
        sset.Pages = [p for p in sset.Pages if p is not None and p != page]
        doc.removeObject(page.Name)
        _settle(doc, sset)
    except Exception:
        HT.fail(doc, own)
        raise
    HT.end(doc, own)


def move_sheet(sset, page, index):
    """Put ``page`` at ``index`` (0 = first) in the set's order.  One undo step."""
    doc = sset.Document
    own = HT.begin(doc, "Move sheet")
    try:
        pages = [p for p in sset.Pages if p is not None and p != page]
        index = max(0, min(int(index), len(pages)))
        pages.insert(index, page)
        sset.Pages = pages
        _settle(doc, sset)
    except Exception:
        HT.fail(doc, own)
        raise
    HT.end(doc, own)


def fields_of(sset, page):
    """-> {canonical: value} as written in the page's title block (the first key of each canonical field the template has)."""
    tpl = page.Template
    if tpl is None or not hasattr(tpl, "EditableTexts"):
        return {}
    kmap, _ = key_map(tpl.Template)
    cur = dict(tpl.EditableTexts)
    out = {}
    for k, c in kmap.items():
        if c not in out and k in cur:
            out[c] = cur[k]
    return out


def warning_of(sset):
    return sset.Warning


def unpin(sset, page, name):
    """Release the per-sheet field ``name`` the user typed over on ``page``: the next recompute writes the computed value again."""
    doc = sset.Document
    own = HT.begin(doc, "Unpin sheet field")
    try:
        st = _load_state(sset)
        pins = st["pinned"].get(page.Name, [])
        if name in pins:
            pins.remove(name)
            kmap, _ = key_map(page.Template.Template)
            wr = st["written"].get(page.Name, {})
            for k, c in kmap.items():
                if c == name:
                    wr.pop(k, None)  # the typed value is the new baseline, not an edit
            sset.State = json.dumps(st, sort_keys=True)
        _settle(doc, sset)
    except Exception:
        HT.fail(doc, own)
        raise
    HT.end(doc, own)
