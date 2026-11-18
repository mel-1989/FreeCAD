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

__all__ = ["GordonSurface", "makeGordonFeature", "resolve_curve", "resolve_links", "build_gordon"]


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

    def execute(self, obj):
        result = build_gordon(obj.Profiles, obj.Guides, obj.Tolerance, obj.AllowApproximation)
        face = Part.Face(result["surface"])
        obj.Shape = face
        obj.IsApproximate = bool(result["approximate"])
        obj.MaxDeviation = max(result["max_profile_deviation"], result["max_guide_deviation"])

    def onDocumentRestored(self, obj):
        obj.Proxy = self
        for name in ("IsApproximate", "MaxDeviation"):
            if name in obj.PropertiesList:
                obj.setPropertyStatus(name, "ReadOnly")

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
    if App.GuiUp:
        try:
            import SurfaceGordonCommands

            SurfaceGordonCommands.ViewProviderGordon(obj.ViewObject)
        except Exception:
            pass
    return obj
