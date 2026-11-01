# SPDX-License-Identifier: LGPL-2.1-or-later
"""Hem: a 180-degree bend and a flat return leg on a straight edge of a sheet-metal part.

cad-fork G07 slice 2. Written without reference to the add-on's GPL SheetMetalHem.py: only the
public fabrication knowledge below, OCCT/Part, and the LGPL helpers vendored beside this file
(the profile builder of SheetMetalEdgeFlange is reused with a 180 degree turn).

Public references used
- Oberg, Jones, Horton, Ryffel, "Machinery's Handbook" (Industrial Press), sheet-metal chapter,
  "Bend allowance": BA = (pi / 180) * theta * (R + K * T), here theta = 180 so BA = pi (R + K T).
- Wikipedia, "Hem (sheet metal)" / "Bending (metalworking)" and common fabricator design guides
  (hem types): a closed (flat) hem folds the sheet back onto itself, in practice with an inside
  radius near zero; an open hem leaves a gap (about one thickness or more) between the legs, with
  an inside radius of half that gap; teardrop and rolled hems have a non-circular or multi-turn
  bend and are not modelled.

Geometry (profile in the plane perpendicular to the edge, x away from the part, z up the side
the hem folds towards, the selected edge at the origin on that face):

- inner arc: centre (0, R), radius R, from (0, 0) through 180 degrees to (0, 2R);
- outer arc: same centre, radius R + T;
- return leg: straight length L from the arc end back over the part, inner face at z = 2R, so the
  gap between base face and return leg is 2R (open: the gap property; closed: 2 * 0.1 T).

Closed hems use the minimum radius 0.1 T (gap 0.2 T): a true zero radius cannot be built or
unfolded, and fabricators quote 0 to 1 T for closed hems. The return length L is measured from the
bend tangent line to the leg end and must be at least T. Developed length of the result:
plate + pi (R + K T) + L.
"""

import FreeCAD

import SheetMetalEdgeFlange as EF
import SheetMetalTools

EPS = 1e-6
CLOSED_RADIUS_FACTOR = 0.1  # closed hem inside radius = 0.1 T (documented minimum)
smElementMapVersion = "sm1."


class HemError(Exception):
    """The hem cannot be built on the given edge with the given parameters."""


def hem_radius(hem_type, gap, thickness):
    """Inside bend radius: half the gap for an open hem, 0.1 T for a closed one."""
    if hem_type == "Closed":
        return CLOSED_RADIUS_FACTOR * thickness
    return gap / 2.0


def hem_bend_allowance(radius, thickness, k):
    return EF.bend_allowance(180.0, radius, thickness, k)


def build_hem(base_shape, edge_names, hem_type="Closed", gap=1.0, length=10.0, k_factor=0.4,
              flip=False, end_gap=0.0):
    """Pure geometry: fuse a hem onto each named straight edge of `base_shape`.

    hem_type 'Closed' or 'Open'; gap is the inner gap of an open hem (ignored when closed);
    length is the flat return leg; end_gap sets the hem back from each end of the edge. Returns one
    valid solid. Raises HemError with a readable message on bad input."""
    if hem_type not in ("Closed", "Open"):
        raise HemError("hem type must be 'Closed' or 'Open' (teardrop and rolled are not supported)")
    if length <= 0.0:
        raise HemError("hem length must be greater than zero, got %g" % length)
    if hem_type == "Open" and gap <= 0.0:
        raise HemError("open hem gap must be greater than zero, got %g" % gap)
    if end_gap < 0.0:
        raise HemError("gap from the edge ends cannot be negative")
    if not edge_names:
        raise HemError("no edge selected")
    result = base_shape
    for name in edge_names:
        edge = base_shape.getElement(SheetMetalTools.getElementFromTNP(name))
        try:
            p0, along, w_edge, out, n_sheet, thk = EF._edge_frame(base_shape, edge)
        except EF.EdgeFlangeError as exc:
            raise HemError(str(exc))
        radius = hem_radius(hem_type, gap, thk)
        if length < thk - EPS:
            raise HemError("hem length %g is shorter than the thickness %g; increase it" % (length, thk))
        width = w_edge - 2.0 * end_gap
        if width <= EPS:
            raise HemError("gap from the edge ends leaves no hem width")
        up = n_sheet * (-1.0 if flip else 1.0)
        origin = p0 + along * end_gap
        if flip:
            origin = origin - n_sheet * thk
        hem = EF.make_flange_solid(origin, along, width, out, up, radius, thk, 180.0, length)
        expected = result.Volume + hem.Volume
        fused = result.fuse(hem).removeSplitter()
        if not fused.isValid() or len(fused.Solids) != 1:
            raise HemError("the hem does not fuse into one valid solid")
        if abs(fused.Volume - expected) > 1e-6 * expected:
            raise HemError("the hem collides with the part (volume overlap %g mm3)"
                           % (expected - fused.Volume))
        result = fused.Solids[0] if fused.ShapeType != "Solid" else fused
    return result


class SMHem:
    def __init__(self, obj, selobj, sel_elements):
        """Add a hem to the edge(s) `sel_elements` of `selobj`."""
        self.addVerifyProperties(obj)
        obj.addProperty("App::PropertyLinkSub", "baseObject", "Parameters",
                        "Base object and edge(s)").baseObject = (selobj, sel_elements)
        obj.Proxy = self

    def addVerifyProperties(self, obj):
        tr = FreeCAD.Qt.translate
        SheetMetalTools.smAddEnumProperty(obj, "hemType", "Hem type", ["Closed", "Open"], "Closed")
        SheetMetalTools.smAddLengthProperty(obj, "gap", tr("App::Property", "Inner gap of an open hem"), 2.0)
        SheetMetalTools.smAddLengthProperty(obj, "length", tr("App::Property", "Flat return leg length"), 10.0)
        SheetMetalTools.smAddFloatProperty(obj, "kfactor", tr("App::Property", "K-factor (ANSI)"), 0.4)
        SheetMetalTools.smAddBoolProperty(obj, "flip", tr("App::Property", "Fold towards the other face"), False)
        SheetMetalTools.smAddLengthProperty(obj, "endGap", tr("App::Property", "Setback from each end of the edge"), 0.0)
        SheetMetalTools.smAddLengthProperty(obj, "thickness", tr("App::Property", "Thickness (from the base)"), 0.0)
        obj.setEditorMode("thickness", 1)
        SheetMetalTools.smAddLengthProperty(obj, "radius", tr("App::Property", "Inside bend radius used"), 0.0)
        obj.setEditorMode("radius", 1)
        SheetMetalTools.smAddLengthProperty(obj, "bendAllowance", tr("App::Property", "Bend allowance (from K-factor)"), 0.0)
        obj.setEditorMode("bendAllowance", 1)

    def getElementMapVersion(self, _fp, ver, _prop, restored):
        return smElementMapVersion + ver if not restored else None

    def execute(self, fp):
        self.addVerifyProperties(fp)
        base = fp.baseObject[0].Shape.copy()
        try:
            shape = build_hem(base, fp.baseObject[1], str(fp.hemType), fp.gap.Value,
                              fp.length.Value, fp.kfactor, fp.flip, fp.endGap.Value)
            edge = base.getElement(SheetMetalTools.getElementFromTNP(fp.baseObject[1][0]))
            thk = EF._edge_frame(base, edge)[5]
        except (HemError, EF.EdgeFlangeError) as exc:
            raise FreeCAD.Base.FreeCADError("Hem: %s" % exc)
        radius = hem_radius(str(fp.hemType), fp.gap.Value, thk)
        fp.thickness = thk
        fp.radius = radius
        fp.bendAllowance = hem_bend_allowance(radius, thk, fp.kfactor)
        fp.Shape = shape


def make_hem(doc, base_obj, edge_names, name="Hem", **props):
    """Create a recomputed hem feature; props set the feature's properties."""
    obj = doc.addObject("Part::FeaturePython", name)
    SMHem(obj, base_obj, list(edge_names))
    for key, value in props.items():
        setattr(obj, key, value)
    doc.recompute()
    SheetMetalTools.attach_view_provider(obj)
    return obj
