# SPDX-License-Identifier: LGPL-2.1-or-later

"""Gordon surface feature: one B-spline face through a network of profile and guide curves.

``makeGordonFeature(doc, profiles, guides)`` adds a ``Part::FeaturePython`` whose ``Shape`` is the
face of ``Surface.makeGordon`` on the curves its ``Profiles`` and ``Guides`` links name.

Each link entry resolves as the selection rule of the Surface analysis commands does: a sub-name
ending in an edge gives that edge with the placement of every object on its path applied
(``Part.getShape(top, sub, needSubElement=True)``); an entry with no sub-name gives the object's
shape, which must be one edge or one wire.

Public sources: W. J. Gordon, "Spline-blended surface interpolation through curve networks",
J. Math. Mech. 18(10), 1969; the OCCT 8.0 ``GeomFill_Gordon`` header documentation.
"""

import FreeCAD as App
import Part

__all__ = [
    "GordonSurface",
    "makeGordonFeature",
    "resolve_curve",
    "resolve_links",
    "build_gordon",
    "ensure_input_guard",
    "check_inputs_alive",
    "record_inputs",
    "note_link_change",
]

_GUARD = "InputNames"
_LINK_PROPS = ("Profiles", "Guides", "Profile", "Rail1", "Rail2")


def _split(entry):
    """One link-list entry as ``(object, [subname, ...])``. ``entry`` may be an object, an
    ``(object, "sub")`` pair or an ``(object, ("sub", ...))`` pair; a link list keeps an entry only
    with at least one sub-name, so a whole-object entry carries the sub-name ``""``."""
    if isinstance(entry, (tuple, list)):
        obj, subs = entry[0], entry[1] if len(entry) > 1 else ()
        if isinstance(subs, str):
            subs = (subs,)
        return obj, [s for s in subs] or [""]
    return entry, [""]


def resolve_curve(obj, sub=""):
    """The edge or wire one link entry names, with every placement on its path applied."""
    if not sub or sub.endswith("."):
        shape = Part.getShape(obj, sub, needSubElement=False, transform=True) if sub else getattr(obj, "Shape", None)
        if shape is None or shape.isNull():
            raise ValueError("InvalidInput: %s has no shape" % obj.Label)
        if shape.ShapeType in ("Edge", "Wire"):
            return shape
        if len(shape.Edges) == 1:
            return shape.Edges[0]
        raise ValueError("InvalidInput: %s is not one edge or one wire; pick its edges" % obj.Label)
    if not sub.rsplit(".", 1)[-1].startswith("Edge"):
        raise ValueError("InvalidInput: %s.%s is not an edge" % (obj.Label, sub))
    shape = Part.getShape(obj, sub, needSubElement=True)
    if shape is None or shape.isNull() or shape.ShapeType != "Edge":
        raise ValueError("InvalidInput: %s.%s does not resolve to an edge" % (obj.Label, sub))
    return shape


def resolve_links(links):
    """Every curve of a link list (``Profiles`` or ``Guides``), in list order."""
    out = []
    for entry in links:
        obj, subs = _split(entry)
        if obj is None:
            continue
        for sub in subs or [""]:
            out.append(resolve_curve(obj, sub))
    return out


# ------------------------------------------------------- deleted-input guard (R114a)
#
# A link property drops a deleted object silently, so a feature would shrink its network or lose a
# rail without a word. The feature therefore keeps a hidden list of the names and labels of the objects
# it linked at its last good recompute; a name that is no longer in the document means "deleted" (an
# object that is still there but no longer linked was removed on purpose).


def _linked_objects(obj):
    out = []
    for prop in _LINK_PROPS:
        if prop not in obj.PropertiesList:
            continue
        value = getattr(obj, prop)
        if prop in ("Profiles", "Guides"):
            items = [e[0] for e in value if e and e[0] is not None]
        else:
            items = [value[0]] if value and value[0] is not None else []
        for item in items:
            if all(item.Name != o.Name for o in out):
                out.append(item)
    return out


def record_inputs(obj):
    """Remember the objects ``obj`` links now (call after a good recompute)."""
    names = ["%s\t%s" % (o.Name, o.Label) for o in _linked_objects(obj)]
    if list(getattr(obj, _GUARD)) != names:
        setattr(obj, _GUARD, names)


def _link_signature(obj):
    sig = []
    for prop in _LINK_PROPS:
        if prop in obj.PropertiesList:
            value = getattr(obj, prop)
            entries = value if prop in ("Profiles", "Guides") else ([value] if value else [])
            for e in entries:
                if e and e[0] is not None:
                    subs = e[1] if not isinstance(e[1], str) else (e[1],)
                    sig.append((prop, e[0].Name, tuple(subs)))
    return tuple(sig)


def note_link_change(proxy, obj, prop):
    """Call from ``onChanged``. The first change of a link property after an input vanished is the
    link dropping the dead object (the delete itself); any later change of a link property is a
    deliberate assignment (a relink) and clears the deleted state by recording the links as they are."""
    if prop not in _LINK_PROPS or _GUARD not in obj.PropertiesList:
        return
    doc = obj.Document
    if doc is None:
        return
    if all(doc.getObject(e.partition("\t")[0]) is not None for e in getattr(obj, _GUARD)):
        proxy._drop_sig = None
        return
    sig = _link_signature(obj)
    dropped = getattr(proxy, "_drop_sig", None)
    if dropped is None:
        proxy._drop_sig = sig
    elif sig != dropped:
        proxy._drop_sig = None
        record_inputs(obj)


def ensure_input_guard(obj):
    """Add the hidden list when the object has none (a new feature, or a file from before R114a)."""
    if _GUARD in obj.PropertiesList:
        return
    obj.addProperty("App::PropertyStringList", _GUARD, "Base", "Objects linked at the last good recompute")
    obj.setPropertyStatus(_GUARD, ["Hidden", "ReadOnly"])
    record_inputs(obj)


def check_inputs_alive(obj):
    """Raise ``InputDeleted`` naming the first recorded input object that is gone from the document."""
    doc = obj.Document
    for entry in getattr(obj, _GUARD, ()):
        name, _tab, label = entry.partition("\t")
        if doc.getObject(name) is None:
            obj.Proxy._drop_sig = _link_signature(obj)  # later link assignments differ from this
            raise ValueError("InputDeleted: %s was deleted" % (label or name))


def build_gordon(profile_links, guide_links, tolerance=1e-4, approximate=False):
    """``Surface.makeGordon`` on the curves of two link lists; returns its result dict."""
    import Surface

    return Surface.makeGordon(
        resolve_links(profile_links), resolve_links(guide_links), tolerance=tolerance, approximate=approximate
    )


class GordonSurface:
    """Proxy of the Gordon surface feature."""

    def __init__(self, obj):
        obj.Proxy = self
        obj.addProperty(
            "App::PropertyLinkSubList", "Profiles", "Gordon",
            "Edges or wires of the profile curves (one direction of the network)",
        )
        obj.addProperty(
            "App::PropertyLinkSubList", "Guides", "Gordon",
            "Edges or wires of the guide curves (the other direction of the network)",
        )
        obj.addProperty(
            "App::PropertyFloat", "Tolerance", "Gordon",
            "Largest gap between crossing curves, and largest miss of the surface, in mm",
        )
        obj.addProperty(
            "App::PropertyBool", "AllowApproximation", "Gordon",
            "Accept a surface that misses the curves by more than the tolerance",
        )
        obj.addProperty("App::PropertyBool", "IsApproximate", "Gordon", "True if the surface is not an exact interpolant")
        obj.addProperty("App::PropertyFloat", "MaxDeviation", "Gordon", "Largest miss of the surface from the input curves, in mm")
        obj.Tolerance = 1e-4
        obj.AllowApproximation = False
        obj.IsApproximate = False
        obj.MaxDeviation = 0.0
        obj.setPropertyStatus("IsApproximate", "ReadOnly")
        obj.setPropertyStatus("MaxDeviation", "ReadOnly")
        ensure_input_guard(obj)

    def execute(self, obj):
        check_inputs_alive(obj)
        result = build_gordon(obj.Profiles, obj.Guides, obj.Tolerance, obj.AllowApproximation)
        face = Part.Face(result["surface"])
        obj.Shape = face
        obj.IsApproximate = bool(result["approximate"])
        obj.MaxDeviation = max(result["max_profile_deviation"], result["max_guide_deviation"])
        record_inputs(obj)

    def onChanged(self, obj, prop):
        note_link_change(self, obj, prop)

    def onDocumentRestored(self, obj):
        obj.Proxy = self
        for name in ("IsApproximate", "MaxDeviation"):
            if name in obj.PropertiesList:
                obj.setPropertyStatus(name, "ReadOnly")
        ensure_input_guard(obj)

    def dumps(self):
        return None

    def loads(self, state):
        return None


def makeGordonFeature(doc, profiles, guides, name="GordonSurface"):
    """Add a Gordon surface feature to ``doc`` and return it (not yet recomputed).

    ``profiles`` and ``guides`` are lists of document objects or ``(object, "SubName")`` pairs."""
    obj = doc.addObject("Part::FeaturePython", name)
    GordonSurface(obj)
    obj.Profiles = [_split(e) for e in profiles]
    obj.Guides = [_split(e) for e in guides]
    record_inputs(obj)
    if App.GuiUp:
        try:
            import SurfaceGordonCommands

            SurfaceGordonCommands.ViewProviderGordon(obj.ViewObject)
        except Exception:
            pass
    return obj
