# SPDX-License-Identifier: LGPL-2.1-or-later

# **************************************************************************
#   PMI authoring (G12 slice 3a): make size, location and angular           *
#   dimensions, datums and geometric tolerances on the faces of a body,     *
#   check them against a table of standards rules, keep them in the         *
#   document as Part::Pmi* objects that the STEP writer exports as AP242.   *
#                                                                           *
#   Public sources of the vocabulary: the CAx-IF recommended practices for  *
#   the representation and presentation of PMI (AP242), ASME Y14.5-2018,    *
#   ISO 1101:2017 and ISO 5459:2011 (the rules table cites them by clause). *
# **************************************************************************

"""Author semantic PMI on a model.

Faces are given as ``(obj, ["Face3", "Face4"])`` (or a list of such pairs).  Lengths are millimetres (a
``FreeCAD.Units.Quantity`` is converted, so ``Quantity("1.5 in")`` works in any document), angles are degrees.
Every call is one undo transaction and returns the new item.  A rule that is broken never refuses an item: the item is
made and carries the rule ids in ``RuleWarnings``.  A ``ValueError`` is raised, with nothing created, when a pick or value
cannot be used.
"""

import json
import math
import os

import FreeCAD as App

TOL_TYPES = (
    "Flatness",
    "Straightness",
    "CircularityOrRoundness",
    "Cylindricity",
    "ProfileOfLine",
    "ProfileOfSurface",
    "Position",
    "Perpendicularity",
    "Parallelism",
    "Angularity",
    "Concentricity",
    "Coaxiality",
    "Symmetry",
    "CircularRunout",
    "TotalRunout",
)
FORM_TYPES = ("Flatness", "Straightness", "CircularityOrRoundness", "Cylindricity")
DATUM_NEEDING_TYPES = (
    "Perpendicularity",
    "Parallelism",
    "Angularity",
    "CircularRunout",
    "TotalRunout",
    "Concentricity",
    "Coaxiality",
    "Symmetry",
)
SIZE_TYPES = ("Size_Diameter", "Size_Radius", "Size_Thickness", "Size_SphericalDiameter", "Size_SphericalRadius", "Size_CurveLength", "Size_Angular")
LOCATION_TYPES = ("Location_None", "Location_LinearDistance", "Location_CurvedDistance", "Location_Angular")
EXCLUDED_LETTERS = ("I", "O", "Q")
MAX_COMPARTMENTS = 3
REL = 1e-9

_SEMANTIC = {
    "Size_Diameter": "diameter",
    "Size_Radius": "radius",
    "Size_Thickness": "thickness",
    "Size_Angular": "angle",
    "Location_LinearDistance": "linear distance",
    "Location_None": "linear distance",
    "Location_Angular": "angle",
}


# ------------------------------------------------------------------------------------------------
# picks and geometry
# ------------------------------------------------------------------------------------------------


def _as_pairs(picks):
    """Normalise a pick to [(obj, [names])]: accepts (obj, [..]), (obj, "Face1") or a list of those."""
    if isinstance(picks, tuple) and len(picks) == 2 and hasattr(picks[0], "TypeId"):
        picks = [picks]
    out = []
    for p in picks or []:
        if not (isinstance(p, (tuple, list)) and len(p) == 2 and hasattr(p[0], "TypeId")):
            raise ValueError("a pick is (object, [\"FaceN\", ...])")
        obj, subs = p
        if isinstance(subs, str):
            subs = [subs]
        subs = list(subs)
        if not subs:
            raise ValueError("a pick names no face")
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull():
            raise ValueError("%s has no shape" % obj.Name)
        for s in subs:
            if not (isinstance(s, str) and s.startswith("Face") and s[4:].isdigit()):
                raise ValueError("only faces can be picked, got %r" % (s,))
            if not 1 <= int(s[4:]) <= len(shape.Faces):
                raise ValueError("%s has no %s" % (obj.Name, s))
        out.append((obj, subs))
    if not out:
        raise ValueError("no faces given")
    return out


def _merge(pairs):
    """Group the subs of one object, keeping the order of first mention."""
    order, subs = [], {}
    for obj, ss in pairs:
        if obj.Name not in subs:
            order.append(obj)
            subs[obj.Name] = []
        for s in ss:
            if s not in subs[obj.Name]:
                subs[obj.Name].append(s)
    return [(o, subs[o.Name]) for o in order]


def _names(pairs):
    return ["%s.%s" % (o.Name, s) for o, ss in pairs for s in ss]


def _faces(pairs):
    return [o.Shape.getElement(s) for o, ss in pairs for s in ss]


def _unit(v):
    n = v.Length
    return App.Vector(v.x / n, v.y / n, v.z / n)


def _desc(face):
    """('plane', point, normal) | ('cylinder', point, axis, radius) | ('sphere', centre, radius) | (kind,)"""
    s = face.Surface
    t = s.TypeId
    if t == "Part::GeomPlane":
        u0, u1, v0, v1 = face.ParameterRange
        n = face.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
        return ("plane", face.CenterOfMass, _unit(n))
    if t == "Part::GeomCylinder":
        return ("cylinder", s.Center, _unit(s.Axis), s.Radius)
    if t == "Part::GeomSphere":
        return ("sphere", s.Center, s.Radius)
    if t == "Part::GeomCone":
        return ("cone", s.Apex, _unit(s.Axis), 0.0)
    return (t,)


def _close(a, b, rel=1e-7):
    return abs(a - b) <= rel * max(1.0, abs(a), abs(b))


def _parallel(n1, n2):
    return n1.cross(n2).Length < 1e-9


def _coplanar(d1, d2):
    return _parallel(d1[2], d2[2]) and abs((d2[1] - d1[1]).dot(d1[2])) < 1e-7 * max(1.0, d1[1].Length, d2[1].Length)


def _plane_groups(descs):
    """Group plane descriptors by plane; None unless all are planes."""
    groups = []
    for d in descs:
        if d[0] != "plane":
            return None
        for g in groups:
            if _coplanar(g[0], d):
                g.append(d)
                break
        else:
            groups.append([d])
    return groups


def _cylinder_of(descs):
    """The common cylinder (radius, axis point, axis) of faces that are all one cylinder, else None."""
    if not descs or any(d[0] != "cylinder" for d in descs):
        return None
    c0 = descs[0]
    for d in descs[1:]:
        if not _close(d[3], c0[3]) or not _parallel(d[2], c0[2]):
            return None
        if (d[1] - c0[1]).cross(c0[2]).Length > 1e-7 * max(1.0, c0[3]):
            return None
    return c0[3], c0[1], c0[2]


def deriveSize(pairs, pmi_type=None):
    """Nominal size of a pick, or None when the bounded rule does not cover it.

    One cylinder (any number of faces of it) gives its diameter (radius for Size_Radius); exactly two parallel
    planes give their distance.  Returns (pmi_type, value)."""
    descs = [_desc(f) for f in _faces(pairs)]
    cyl = _cylinder_of(descs)
    if cyl is not None:
        if pmi_type == "Size_Radius":
            return "Size_Radius", cyl[0]
        if pmi_type in (None, "Size_Diameter"):
            return "Size_Diameter", 2.0 * cyl[0]
        return None
    groups = _plane_groups(descs)
    if groups is not None and len(groups) == 2 and _parallel(groups[0][0][2], groups[1][0][2]):
        d = abs((groups[1][0][1] - groups[0][0][1]).dot(groups[0][0][2]))
        if pmi_type in (None, "Size_Thickness"):
            return "Size_Thickness", d
    return None


def deriveDistance(first, second):
    """Distance between two parallel planes (each side may hold several faces of one plane), else None."""
    g1 = _plane_groups([_desc(f) for f in _faces(first)])
    g2 = _plane_groups([_desc(f) for f in _faces(second)])
    if not g1 or not g2 or len(g1) != 1 or len(g2) != 1:
        return None
    a, b = g1[0][0], g2[0][0]
    if not _parallel(a[2], b[2]):
        return None
    return abs((b[1] - a[1]).dot(a[2]))


def deriveAngle(first, second):
    """Angle in degrees between two planes, in [0, 90] (the acute one), else None."""
    g1 = _plane_groups([_desc(f) for f in _faces(first)])
    g2 = _plane_groups([_desc(f) for f in _faces(second)])
    if not g1 or not g2 or len(g1) != 1 or len(g2) != 1:
        return None
    c = max(-1.0, min(1.0, g1[0][0][2].dot(g2[0][0][2])))
    ang = math.degrees(math.acos(c))
    return min(ang, 180.0 - ang)


# ------------------------------------------------------------------------------------------------
# values
# ------------------------------------------------------------------------------------------------


def _length(v, what="value"):
    if isinstance(v, App.Units.Quantity):
        return v.getValueAs("mm").Value
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ValueError("%s must be a number or a length Quantity, got %r" % (what, v))


def _angle(v, what="value"):
    if isinstance(v, App.Units.Quantity):
        return v.getValueAs("deg").Value
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ValueError("%s must be a number (degrees) or an angle Quantity, got %r" % (what, v))


def _bounds(plus_minus, limits, fit_class, conv):
    given = [x is not None for x in (plus_minus, limits, fit_class)]
    if sum(given) > 1:
        raise ValueError("give at most one of plus_minus, limits and fit_class")
    if plus_minus is not None:
        up, lo = plus_minus
        return ("plus_minus", conv(up), conv(lo), "")
    if limits is not None:
        up, lo = limits
        return ("range", conv(up), conv(lo), "")
    if fit_class is not None:
        fc = str(fit_class)
        if not fc or not fc[0].isalpha() or not fc[-1].isdigit():
            raise ValueError("a fit class looks like H7 or g6, got %r" % fc)
        return ("class", 0.0, 0.0, fc)
    return None


# ------------------------------------------------------------------------------------------------
# document helpers
# ------------------------------------------------------------------------------------------------


def _doc_of(pairs):
    docs = {o.Document.Name for o, _ in pairs}
    if len(docs) != 1:
        raise ValueError("the picks belong to more than one document")
    return pairs[0][0].Document


def _group(doc):
    for o in doc.Objects:
        if o.TypeId == "Part::PmiGroup":
            return o
    g = doc.addObject("Part::PmiGroup", "PMI")
    g.Label = "PMI"
    return g


class _Transaction:
    """One undo step for the whole call; aborted when the body raises."""

    def __init__(self, doc, name):
        self.doc = doc
        self.name = name

    def __enter__(self):
        if self.doc.UndoMode == 0:
            self.doc.UndoMode = 1
        self.doc.openTransaction(self.name)
        return self

    def __exit__(self, et, ev, tb):
        if et is not None:
            self.doc.abortTransaction()
            return False
        self.doc.commitTransaction()
        return False


def _set_refs(item, pairs, second=None):
    item.References = _merge(pairs)
    if second:
        item.SecondSide = _names(second)
    item.Unattached = False


def _label(item, text):
    item.Label = text


def _fmt(x):
    return ("%.6f" % x).rstrip("0").rstrip(".") or "0"


def _finish(item, doc):
    """Rule check of one new item; one report-view warning when something is broken."""
    warns = checkItem(item)
    if warns:
        App.Console.PrintWarning(
            "PMI: %s (%s) breaks %d rule(s): %s\n" % (item.Name, item.Label, len(warns), "; ".join(warns))
        )
    return item


# ------------------------------------------------------------------------------------------------
# dimensions
# ------------------------------------------------------------------------------------------------


def _dimension_common(item, pmi_type, value, bnds, qualifier, modifiers, semantic):
    item.PmiType = pmi_type
    item.SemanticName = semantic or _SEMANTIC.get(pmi_type, "")
    item.Value = value
    if bnds is not None:
        kind, up, lo, cls = bnds
        if kind == "class":
            item.ToleranceClass = cls
        else:
            item.HasBounds = True
            item.BoundsKind = kind
            item.UpperBound = up
            item.LowerBound = lo
    if qualifier:
        item.Qualifier = qualifier
    if modifiers:
        item.Modifiers = list(modifiers)


def addSizeDimension(
    picks,
    value=None,
    *,
    pmi_type=None,
    plus_minus=None,
    limits=None,
    fit_class=None,
    qualifier="",
    modifiers=(),
    semantic_name=None,
):
    """A size dimension on the faces of one feature of size.

    value: explicit nominal (mm); when omitted it is derived from one cylinder (diameter) or two parallel planes
    (thickness).  plus_minus=(upper, lower) signed deviations; limits=(upper, lower) absolute limits; fit_class="H7"."""
    pairs = _as_pairs(picks)
    doc = _doc_of(pairs)
    if pmi_type is not None and pmi_type not in SIZE_TYPES:
        raise ValueError("%s is not a size dimension type" % pmi_type)
    if value is None:
        d = deriveSize(pairs, pmi_type)
        if d is None:
            raise ValueError("the nominal size cannot be derived from these faces; give value=")
        pmi_type, v = d
    else:
        v = _angle(value) if pmi_type == "Size_Angular" else _length(value)
        pmi_type = pmi_type or "Size_Diameter"
    conv = _angle if pmi_type == "Size_Angular" else _length
    bnds = _bounds(plus_minus, limits, fit_class, conv)
    with _Transaction(doc, "Add PMI size"):
        item = doc.addObject("Part::PmiDimension", "PmiDimension")
        _dimension_common(item, pmi_type, v, bnds, qualifier, modifiers, semantic_name)
        _set_refs(item, pairs)
        _label(item, "%s %s" % (pmi_type, _fmt(v)))
        _group(doc).addObject(item)
        doc.recompute()
        return _finish(item, doc)


def addLocationDimension(
    first,
    second,
    value=None,
    *,
    pmi_type="Location_LinearDistance",
    plus_minus=None,
    limits=None,
    fit_class=None,
    qualifier="",
    modifiers=(),
    semantic_name=None,
):
    """A location dimension between two face sets.  value omitted: the distance between two parallel planes."""
    p1, p2 = _as_pairs(first), _as_pairs(second)
    doc = _doc_of(p1 + p2)
    if pmi_type not in LOCATION_TYPES or pmi_type == "Location_Angular":
        raise ValueError("%s is not a linear location dimension type" % pmi_type)
    if value is None:
        v = deriveDistance(p1, p2)
        if v is None:
            raise ValueError("the distance cannot be derived from these faces; give value=")
    else:
        v = _length(value)
    bnds = _bounds(plus_minus, limits, fit_class, _length)
    with _Transaction(doc, "Add PMI location"):
        item = doc.addObject("Part::PmiDimension", "PmiDimension")
        _dimension_common(item, pmi_type, v, bnds, qualifier, modifiers, semantic_name)
        _set_refs(item, p1 + p2, p2)
        _label(item, "%s %s" % (pmi_type, _fmt(v)))
        _group(doc).addObject(item)
        doc.recompute()
        return _finish(item, doc)


def addAngularDimension(
    first,
    second,
    value=None,
    *,
    plus_minus=None,
    limits=None,
    qualifier="",
    modifiers=(),
    semantic_name=None,
):
    """An angular location dimension between two face sets (degrees).  value omitted: the acute angle of two planes."""
    p1, p2 = _as_pairs(first), _as_pairs(second)
    doc = _doc_of(p1 + p2)
    if value is None:
        v = deriveAngle(p1, p2)
        if v is None:
            raise ValueError("the angle cannot be derived from these faces; give value=")
    else:
        v = _angle(value)
    bnds = _bounds(plus_minus, limits, None, _angle)
    with _Transaction(doc, "Add PMI angle"):
        item = doc.addObject("Part::PmiDimension", "PmiDimension")
        _dimension_common(item, "Location_Angular", v, bnds, qualifier, modifiers, semantic_name)
        _set_refs(item, p1 + p2, p2)
        _label(item, "Location_Angular %s" % _fmt(v))
        _group(doc).addObject(item)
        doc.recompute()
        return _finish(item, doc)


# ------------------------------------------------------------------------------------------------
# datums and tolerances
# ------------------------------------------------------------------------------------------------


def addDatum(letter, picks, modifiers=()):
    """A datum feature: letter and the faces of the feature.  Tolerance frames that name the letter get their own use of it."""
    letter = str(letter)
    if len(letter) != 1 or not letter.isalpha() or not letter.isupper():
        raise ValueError("a datum letter is one capital letter, got %r" % letter)
    pairs = _as_pairs(picks)
    doc = _doc_of(pairs)
    with _Transaction(doc, "Add PMI datum"):
        item = doc.addObject("Part::PmiDatum", "PmiDatum")
        item.PmiType = "Datum"
        item.Letter = letter
        item.Position = 0
        if modifiers:
            item.Modifiers = list(modifiers)
        _set_refs(item, pairs)
        _label(item, "Datum %s" % letter)
        _group(doc).addObject(item)
        doc.recompute()
        return _finish(item, doc)


def _feature_of(doc, letter):
    """The datum feature of a letter: a loose PmiDatum with faces (Position 0), else any use of it that has faces."""
    best = None
    for o in doc.Objects:
        if o.TypeId == "Part::PmiDatum" and o.Letter == letter and not o.IsTarget and len(o.References):
            if o.Position == 0:
                return o
            best = best or o
    return best


def _datum_spec(d):
    if isinstance(d, str):
        return d, []
    if len(d) == 1:
        return d[0], []
    letter, mods = d
    return letter, ([mods] if isinstance(mods, str) else list(mods))


def addTolerance(
    tol_type,
    value,
    picks,
    *,
    datums=(),
    zone_shape="",
    zone_modifier="",
    zone_modifier_value=0.0,
    modifiers=(),
    semantic_name=None,
):
    """A geometric tolerance frame on the picked faces.

    tol_type: one of TOL_TYPES.  datums: up to three compartments, each "A" or ("B", ["MAXIMUM_MATERIAL_REQUIREMENT"]).
    zone_shape: "", "Diameter" or "SphericalDiameter".  zone_modifier: "", "Projected", "Runout" or "NonUniform".
    modifiers: e.g. ["MAXIMUM_MATERIAL_REQUIREMENT", "ALL_AROUND"], material requirement first.
    Each datum compartment gets its own PmiDatum use (never shared with another frame) with the faces of the datum
    feature of that letter."""
    if tol_type not in TOL_TYPES:
        raise ValueError("%r is not one of the tolerance types %s" % (tol_type, ", ".join(TOL_TYPES)))
    if zone_shape not in ("", "Diameter", "SphericalDiameter"):
        raise ValueError("zone_shape is '', 'Diameter' or 'SphericalDiameter'")
    if zone_modifier not in ("", "Projected", "Runout", "NonUniform"):
        raise ValueError("zone_modifier is '', 'Projected', 'Runout' or 'NonUniform'")
    specs = [_datum_spec(d) for d in datums]
    if len(specs) > MAX_COMPARTMENTS:
        raise ValueError("a frame holds at most %d datum compartments" % MAX_COMPARTMENTS)
    pairs = _as_pairs(picks)
    doc = _doc_of(pairs)
    v = _length(value, "tolerance value")
    with _Transaction(doc, "Add PMI tolerance"):
        item = doc.addObject("Part::PmiTolerance", "PmiTolerance")
        item.PmiType = tol_type
        item.SemanticName = semantic_name or tol_type
        item.Value = v
        item.ZoneShape = zone_shape
        item.ZoneModifier = zone_modifier
        item.ZoneModifierValue = _length(zone_modifier_value)
        if modifiers:
            item.Modifiers = list(modifiers)
        _set_refs(item, pairs)
        grp = _group(doc)
        uses, refs = [], []
        for pos, (letter, mods) in enumerate(specs, start=1):
            use = doc.addObject("Part::PmiDatum", "PmiDatum")
            use.PmiType = "Datum"
            use.Letter = letter
            use.Position = pos
            if mods:
                use.Modifiers = mods
            feat = _feature_of(doc, letter)
            if feat is not None and feat is not use:
                use.References = [(o, list(s)) for o, s in _ref_pairs(feat)]
                use.Unattached = False
            else:
                use.Unattached = True
            use.Label = "Datum %s" % letter
            grp.addObject(use)
            uses.append(use)
            refs.append(letter + ("(" + ",".join(mods) + ")" if mods else ""))
        item.Datums = uses
        item.DatumReferences = refs
        _label(item, "%s %s%s" % (tol_type, _fmt(v), "".join(" | " + r for r in refs)))
        grp.addObject(item)
        doc.recompute()
        for u in uses:
            checkItem(u)
        return _finish(item, doc)


def _ref_pairs(item):
    return [(o, list(s)) for o, s in item.References]


# ------------------------------------------------------------------------------------------------
# rules
# ------------------------------------------------------------------------------------------------

_RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "PmiRules.json")
_rules_cache = None


def loadRules(path=None):
    """The rules table: a list of dicts with id, severity, message, citation (and the standard it comes from)."""
    global _rules_cache
    if path is None and _rules_cache is not None:
        return _rules_cache
    with open(path or _RULES_PATH, encoding="utf-8") as f:
        rules = json.load(f)["rules"]
    missing = [r["id"] for r in rules if r["id"] not in _CHECKS]
    if missing:
        raise RuntimeError("rules without a check: %s" % ", ".join(missing))
    if path is None:
        _rules_cache = rules
    return rules


def _item_faces(item):
    """Face objects of an item's References, [] when something does not resolve."""
    out = []
    for o, subs in item.References:
        if o is None or not hasattr(o, "Shape"):
            return []
        for s in subs:
            try:
                out.append(o.Shape.getElement(s))
            except Exception:
                return []
    return out


def _side_faces(item):
    sec = set(item.SecondSide)
    first, second = [], []
    for o, subs in item.References:
        for s in subs:
            try:
                f = o.Shape.getElement(s)
            except Exception:
                return [], []
            (second if ("%s.%s" % (o.Name, s)) in sec else first).append(f)
    return first, second


def _is_fos(descs):
    """Feature of size: faces of one cylinder or one sphere, or exactly two parallel planes."""
    if not descs:
        return None
    if _cylinder_of(descs) is not None:
        return True
    if all(d[0] == "sphere" for d in descs):
        return all(_close(d[2], descs[0][2]) and (d[1] - descs[0][1]).Length < 1e-7 for d in descs)
    g = _plane_groups(descs)
    if g is not None:
        return len(g) == 2 and _parallel(g[0][0][2], g[1][0][2])
    return False


def _tolerance_datums(item):
    return [d for d in item.Datums if d is not None]


def _c_form_no_datum(item, doc):
    if item.TypeId == "Part::PmiTolerance" and item.PmiType in FORM_TYPES and (len(item.DatumReferences) or len(item.Datums)):
        return "%s is a form tolerance and takes no datum reference" % item.PmiType


def _c_datum_needed(item, doc):
    if item.TypeId == "Part::PmiTolerance" and item.PmiType in DATUM_NEEDING_TYPES and not len(item.DatumReferences):
        return "%s controls a feature relative to a datum and has none" % item.PmiType


def _c_material_needs_fos(item, doc):
    if item.TypeId == "Part::PmiTolerance":
        mats = [m for m in item.Modifiers if m in ("MAXIMUM_MATERIAL_REQUIREMENT", "LEAST_MATERIAL_REQUIREMENT")]
        if mats:
            descs = [_desc(f) for f in _item_faces(item)]
            if descs and _is_fos(descs) is False:
                return "a material-condition modifier needs a feature of size, and the governed faces are not one"
    if item.TypeId == "Part::PmiDatum":
        mats = [m for m in item.Modifiers if m in ("MAXIMUM_MATERIAL_REQUIREMENT", "LEAST_MATERIAL_REQUIREMENT")]
        if mats:
            descs = [_desc(f) for f in _item_faces(item)]
            if descs and _is_fos(descs) is False:
                return "a material-condition modifier on a datum needs a datum feature of size, and these faces are not one"


def _c_diameter_zone(item, doc):
    if item.TypeId != "Part::PmiTolerance" or item.ZoneShape not in ("Diameter", "SphericalDiameter"):
        return None
    descs = [_desc(f) for f in _item_faces(item)]
    if not descs:
        return None
    if item.ZoneShape == "SphericalDiameter":
        ok = all(d[0] == "sphere" for d in descs)
    else:
        ok = all(d[0] in ("cylinder", "cone", "sphere") for d in descs)
    if not ok:
        return "a %s zone needs a cylindrical (or spherical) feature, and the governed faces are not" % item.ZoneShape


def _c_value_positive(item, doc):
    if item.TypeId == "Part::PmiTolerance" and not item.Value > 0:
        return "the tolerance value is %s and must be greater than zero" % _fmt(item.Value)


def _c_bounds_ordered(item, doc):
    if item.TypeId == "Part::PmiDimension" and item.HasBounds and item.UpperBound < item.LowerBound:
        return "the upper bound %s is below the lower bound %s" % (_fmt(item.UpperBound), _fmt(item.LowerBound))


def _c_letters_excluded(item, doc):
    bad = []
    if item.TypeId == "Part::PmiDatum" and item.Letter in EXCLUDED_LETTERS:
        bad.append(item.Letter)
    if item.TypeId == "Part::PmiTolerance":
        bad += [r[0] for r in item.DatumReferences if r[:1] in EXCLUDED_LETTERS]
    if bad:
        return "datum letter(s) %s are not used" % ", ".join(sorted(set(bad)))


def _c_letter_repeat(item, doc):
    if item.TypeId == "Part::PmiTolerance":
        ls = [r[:1] for r in item.DatumReferences]
        dup = sorted({x for x in ls if ls.count(x) > 1})
        if dup:
            return "datum letter(s) %s repeat within one frame" % ", ".join(dup)


def _c_letter_exists(item, doc):
    if item.TypeId == "Part::PmiTolerance":
        have = {
            o.Letter
            for o in doc.Objects
            if o.TypeId == "Part::PmiDatum" and not o.IsTarget and len(o.References)
        }
        miss = [r[:1] for r in item.DatumReferences if r[:1] not in have]
        if miss:
            return "datum letter(s) %s are referenced and no datum feature of that letter exists" % ", ".join(sorted(set(miss)))


def _c_nominal_agrees(item, doc):
    if item.TypeId != "Part::PmiDimension" or item.Value == 0 or not len(item.References):
        return None
    t = item.PmiType
    try:
        if t in ("Size_Diameter", "Size_Radius", "Size_Thickness"):
            pairs = [(o, list(s)) for o, s in item.References]
            d = deriveSize(pairs, t)
            if d is None:
                return None
            expect = d[1]
        elif t in ("Location_LinearDistance", "Location_None", "Location_CurvedDistance"):
            f1, f2 = _side_faces(item)
            if not f1 or not f2:
                return None
            expect = _distance_faces(f1, f2)
        elif t in ("Location_Angular", "Size_Angular"):
            f1, f2 = _side_faces(item)
            if not f1 or not f2:
                return None
            a = _angle_faces(f1, f2)
            if a is None:
                return None
            ok = _close(item.Value, a, 1e-6) or _close(item.Value, 180.0 - a, 1e-6)
            return None if ok else "the stated angle %s differs from the measured %s" % (_fmt(item.Value), _fmt(a))
        else:
            return None
    except Exception:
        return None
    if expect is None:
        return None
    if not _close(item.Value, expect, 1e-6):
        return "the stated nominal %s differs from the measured %s" % (_fmt(item.Value), _fmt(expect))


def _distance_faces(f1, f2):
    g1 = _plane_groups([_desc(f) for f in f1])
    g2 = _plane_groups([_desc(f) for f in f2])
    if not g1 or not g2 or len(g1) != 1 or len(g2) != 1:
        return None
    a, b = g1[0][0], g2[0][0]
    return abs((b[1] - a[1]).dot(a[2])) if _parallel(a[2], b[2]) else None


def _angle_faces(f1, f2):
    g1 = _plane_groups([_desc(f) for f in f1])
    g2 = _plane_groups([_desc(f) for f in f2])
    if not g1 or not g2 or len(g1) != 1 or len(g2) != 1:
        return None
    c = max(-1.0, min(1.0, g1[0][0][2].dot(g2[0][0][2])))
    a = math.degrees(math.acos(c))
    return min(a, 180.0 - a)


def _c_compartment_order(item, doc):
    if item.TypeId == "Part::PmiTolerance":
        for i, d in enumerate(_tolerance_datums(item), start=1):
            if d.Position != i:
                return "datum %s sits in compartment %d but is recorded as position %d" % (d.Letter, i, d.Position)


def _c_max_compartments(item, doc):
    if item.TypeId == "Part::PmiTolerance" and len(item.DatumReferences) > MAX_COMPARTMENTS:
        return "a frame holds at most %d datum compartments, this one has %d" % (MAX_COMPARTMENTS, len(item.DatumReferences))


def _c_round_form(item, doc):
    if item.TypeId == "Part::PmiTolerance" and item.PmiType in ("CircularityOrRoundness", "Cylindricity"):
        descs = [_desc(f) for f in _item_faces(item)]
        if descs and not all(d[0] in ("cylinder", "cone", "sphere") for d in descs):
            return "%s applies to a round surface, and the governed faces include a non-round one" % item.PmiType


def _c_projected_value(item, doc):
    if item.TypeId == "Part::PmiTolerance" and item.ZoneModifier == "Projected" and not item.ZoneModifierValue > 0:
        return "a projected tolerance zone needs a height greater than zero"


_CHECKS = {
    "form-no-datum": _c_form_no_datum,
    "datum-needed": _c_datum_needed,
    "material-needs-fos": _c_material_needs_fos,
    "diameter-zone-feature": _c_diameter_zone,
    "value-positive": _c_value_positive,
    "bounds-ordered": _c_bounds_ordered,
    "letters-excluded": _c_letters_excluded,
    "letter-repeat": _c_letter_repeat,
    "letter-exists": _c_letter_exists,
    "nominal-agrees": _c_nominal_agrees,
    "compartment-order": _c_compartment_order,
    "max-compartments": _c_max_compartments,
    "round-form": _c_round_form,
    "projected-height": _c_projected_value,
}


def ruleHits(item):
    """[(rule id, message)] for one item, without writing anything."""
    doc = item.Document
    hits = []
    for r in loadRules():
        try:
            msg = _CHECKS[r["id"]](item, doc)
        except Exception as e:  # a check that cannot judge stays silent; checkPmi never raises
            msg = None
        if msg:
            hits.append((r["id"], msg))
    return hits


def checkItem(item):
    """Run the rules on one item and store the result in its RuleWarnings; returns the list of strings."""
    if not item.isDerivedFrom("Part::PmiItem") or item.TypeId == "Part::PmiAnnotation":
        return []
    warns = ["%s: %s" % h for h in ruleHits(item)]
    if list(item.RuleWarnings) != warns:
        item.RuleWarnings = warns
    return warns


def checkPmi(doc):
    """Check every PMI item of a document against the rules table.

    Sets RuleWarnings on each item and returns [(item name, rule id, message)].  Never raises."""
    out = []
    for o in list(doc.Objects):
        try:
            if not o.isDerivedFrom("Part::PmiItem") or o.TypeId == "Part::PmiAnnotation":
                continue
            checkItem(o)
            for w in o.RuleWarnings:
                rid, _, msg = w.partition(": ")
                out.append((o.Name, rid, msg))
        except Exception as e:
            App.Console.PrintWarning("PMI: rules could not be run on %s: %s\n" % (getattr(o, "Name", "?"), e))
    return out
