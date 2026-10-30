# SPDX-License-Identifier: LGPL-2.1-or-later
"""Pivy overlay of added (green) and removed (red) material in the active 3D view's view-provider root.

The pieces come from ``AgentTurn.overlay_pieces()``, i.e. from the face-level matcher and the
local booleans of ``diff/geometry.py``; this module only draws them. The overlay is plain scene-graph
content, so ``View3DInventor.saveImage`` renders it. It is flat shaded (no normals needed), drawn with
a polygon offset so material that coincides with the model's own faces does not z-fight, and its edges
are drawn opaque.
"""

from pivy import coin

NAME = "AgentCoreOverlay"
COLORS = {"added": (0.10, 0.75, 0.20), "removed": (0.90, 0.15, 0.15)}
TRANSPARENCY = 0.55


def _tolerance(shape):
    d = shape.BoundBox.DiagonalLength if shape.BoundBox.isValid() else 1.0
    return max(0.02, d / 200.0)


def _piece_node(kind, shape):
    sep = coin.SoSeparator()
    color = COLORS[kind]
    verts, tris = shape.tessellate(_tolerance(shape))
    if tris:
        faces = coin.SoSeparator()
        mat = coin.SoMaterial()
        mat.diffuseColor.setValue(*color)
        mat.transparency.setValue(TRANSPARENCY)
        faces.addChild(mat)
        offset = coin.SoPolygonOffset()
        offset.factor.setValue(-1.0)
        offset.units.setValue(-1.0)
        offset.styles.setValue(coin.SoPolygonOffset.FILLED)
        faces.addChild(offset)
        coords = coin.SoCoordinate3()
        coords.point.setValues(0, len(verts), [(v.x, v.y, v.z) for v in verts])
        faces.addChild(coords)
        idx = []
        for a, b, c in tris:
            idx += [a, b, c, -1]
        fs = coin.SoIndexedFaceSet()
        fs.coordIndex.setValues(0, len(idx), idx)
        faces.addChild(fs)
        sep.addChild(faces)
    lines = []
    for e in shape.Edges:
        try:
            lines.append(e.discretize(Deflection=_tolerance(shape)))
        except Exception:
            pass
    if lines:
        wire = coin.SoSeparator()
        mat = coin.SoMaterial()
        mat.diffuseColor.setValue(*[c * 0.7 for c in color])
        wire.addChild(mat)
        style = coin.SoDrawStyle()
        style.lineWidth.setValue(2.0)
        wire.addChild(style)
        coords = coin.SoCoordinate3()
        pts = [(p.x, p.y, p.z) for line in lines for p in line]
        coords.point.setValues(0, len(pts), pts)
        wire.addChild(coords)
        ls = coin.SoLineSet()
        ls.numVertices.setValues(0, len(lines), [len(line) for line in lines])
        wire.addChild(ls)
        sep.addChild(wire)
    return sep


def view_provider_root(view):
    """The node ``saveImage`` and ``fitAll`` render: the viewer's view-provider root
    (``SoFCUnifiedSelection``), a child of ``getSceneGraph()``. Content added beside it is drawn on
    screen but left out of ``saveImage`` and of ``fitAll``'s bounding box."""
    sg = view.getSceneGraph()
    for i in range(sg.getNumChildren()):
        child = sg.getChild(i)
        if "UnifiedSelection" in child.getTypeId().getName().getString():
            return child
    raise RuntimeError("the 3D view has no view-provider root")


class Overlay:
    """One separator under a view's scene graph; ``detach`` is idempotent."""

    def __init__(self, pieces):
        self.pieces = list(pieces)
        self.root = None
        self._parent = None

    def build(self):
        root = coin.SoSeparator()
        root.setName(NAME)
        light = coin.SoLightModel()
        light.model.setValue(coin.SoLightModel.BASE_COLOR)
        root.addChild(light)
        hints = coin.SoShapeHints()
        hints.vertexOrdering.setValue(coin.SoShapeHints.UNKNOWN_ORDERING)
        root.addChild(hints)
        ttype = coin.SoTransparencyType()
        ttype.value.setValue(coin.SoTransparencyType.SORTED_OBJECT_BLEND)
        root.addChild(ttype)
        for name, kind, shape in self.pieces:
            node = _piece_node(kind, shape)
            node.setName(f"{NAME}_{kind}_{name}")
            root.addChild(node)
        return root

    def attach(self, view):
        if self.root is not None or not self.pieces:
            return False
        parent = view_provider_root(view)
        self.root = self.build()
        parent.addChild(self.root)
        self._parent = parent
        return True

    def detach(self):
        if self.root is not None and self._parent is not None:
            try:
                self._parent.removeChild(self.root)
            except Exception:
                pass
        self.root = self._parent = None

    @property
    def attached(self):
        return self.root is not None


def find_overlays(view):
    """Overlay separators currently in the view's scene graph (tests)."""
    action = coin.SoSearchAction()
    action.setName(NAME)
    action.setInterest(coin.SoSearchAction.ALL)
    action.apply(view.getSceneGraph())
    paths = action.getPaths()
    return [paths[i].getTail() for i in range(paths.getLength())]
