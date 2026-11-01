# SPDX-License-Identifier: LGPL-2.1-or-later
"""Reference frames for the weldment tests: box frame, portal frame, pitched truss.

Sizes are picked by position in the profile tables (``pick``), never by name, so the frames build
against any table. The golden cut lists in ``WTests/golden/`` depend on which sizes those positions
select and on each profile's linear mass, so they must be regenerated whenever a table changes:

    FreeCADCmd -c "import sys; sys.path.insert(0, '<repo>/src/Mod/Weldment'); \\
                   import WTests.frames as f; f.regenerate_goldens()"

(or run ``TestWeldment`` with ``WELDMENT_REGEN_GOLDEN=1``, which rewrites the files instead of
comparing). Review the diff of the golden files before committing.
"""

import os

import FreeCAD
import Part

import Profiles
from WeldMember import make_member
from WeldTrim import make_trim, make_corner

V = FreeCAD.Vector
GOLDEN_HEADER = "# SPDX-License-Identifier: LGPL-2.1-or-later\n"
GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")


def pick(family, frac):
    """The size at relative position frac (0..1) in the family's table ordered by outline area."""
    from WTests.oracle import by_outline

    sizes = by_outline(family)
    return sizes[int(round(frac * (len(sizes) - 1)))]


def lines(doc, name, segments):
    """A Part::Feature holding straight edges, the path source for members."""
    o = doc.addObject("Part::Feature", name)
    o.Shape = Part.Compound([Part.makeLine(V(*a), V(*b)) for a, b in segments])
    return o


class Frame:
    """Members by name with their current chain heads (the object to cut-list and recompute)."""

    def __init__(self, doc):
        self.doc = doc
        self.members = {}
        self.heads = {}
        self.paths = {}

    def add(self, name, family, size, segments, anchor="Centre", rotation=0.0):
        path = lines(self.doc, name + "Path", segments)
        m = make_member(self.doc, family, size, [path], name, anchor, rotation)
        self.members[name] = m
        self.heads[name] = m
        self.paths[name] = path
        return m

    def mitre(self, a, seg_a, end_a, b, seg_b, end_b):
        ta, tb = make_corner(self.doc, self.heads[a], seg_a, end_a, self.heads[b], seg_b, end_b, "Mitre")
        self.heads[a], self.heads[b] = ta, tb

    def butt(self, a, seg_a, end_a, b, seg_b):
        """Member a, segment seg_a, end end_a is trimmed to the envelope of b's segment seg_b."""
        self.heads[a] = make_trim(
            self.doc, self.heads[a], seg_a, end_a, "Butt", self.members[b], "", seg_b, "Start", "Butt"
        )

    def final(self):
        return [self.heads[k] for k in sorted(self.heads)]


def box_frame(doc, x=3000.0, y=2000.0, z=2500.0):
    """Twelve members: four columns, four top beams, four bottom beams (all SHS, untrimmed)."""
    f = Frame(doc)
    fam = "EN SHS"
    pts = [(0, 0), (x, 0), (x, y), (0, y)]
    f.add("Cols", fam, pick(fam, 0.6), [((px, py, 0), (px, py, z)) for px, py in pts])
    beams = []
    for h in (0.0, z):
        for i in range(4):
            a, b = pts[i], pts[(i + 1) % 4]
            beams.append(((a[0], a[1], h), (b[0], b[1], h)))
    f.add("Beams", fam, pick(fam, 0.4), beams)
    return f


def portal_frame(doc, span=8000.0, eave=4000.0, rise=1500.0, tie=3000.0):
    """Two columns, two rafters, a tie beam: columns and rafters mitred, tie butted to both columns."""
    f = Frame(doc)
    fam = "EN SHS"
    half = span / 2.0
    f.add("Columns", fam, pick(fam, 0.75), [((0, 0, 0), (0, 0, eave)), ((span, 0, 0), (span, 0, eave))])
    f.add("Rafters", fam, pick(fam, 0.75), [((0, 0, eave), (half, 0, eave + rise)), ((span, 0, eave), (half, 0, eave + rise))])
    f.add("Tie", fam, pick(fam, 0.3), [((0, 0, tie), (span, 0, tie))])
    f.mitre("Columns", 0, "End", "Rafters", 0, "Start")
    f.mitre("Columns", 1, "End", "Rafters", 1, "Start")
    f.mitre("Rafters", 0, "End", "Rafters", 1, "End")
    f.butt("Tie", 0, "Start", "Columns", 0)
    f.butt("Tie", 0, "End", "Columns", 1)
    return f


def pitched_truss(doc, span=8000.0, rise=1600.0):
    """Seven members: bottom chord, two rafters, two verticals, two diagonals; eaves and ridge mitred, webs butted."""
    f = Frame(doc)
    fam = "EN SHS"
    half = span / 2.0
    zr = lambda x: rise * min(x, span - x) / half  # rafter height at x
    f.add(
        "Chords", fam, pick(fam, 0.7),
        [((0, 0, 0), (span, 0, 0)), ((0, 0, 0), (half, 0, rise)), ((span, 0, 0), (half, 0, rise))],
    )
    xs = (span / 4.0, 3 * span / 4.0)
    f.add(
        "Web", fam, pick(fam, 0.25),
        [((x, 0, 0), (x, 0, zr(x))) for x in xs]
        + [((half - 500.0, 0, 0), (xs[0], 0, zr(xs[0]))), ((half + 500.0, 0, 0), (xs[1], 0, zr(xs[1])))],
    )
    f.mitre("Chords", 0, "Start", "Chords", 1, "Start")
    f.mitre("Chords", 0, "End", "Chords", 2, "Start")
    f.mitre("Chords", 1, "End", "Chords", 2, "End")
    for i, rafter in ((0, 1), (1, 2), (2, 1), (3, 2)):
        f.butt("Web", i, "Start", "Chords", 0)
        f.butt("Web", i, "End", "Chords", rafter)
    return f


def golden_path(name):
    return os.path.join(GOLDEN_DIR, name)


def regenerate_goldens():
    import WeldCutList

    for name, build in (("portal_cutlist.csv", portal_frame), ("truss_cutlist.csv", pitched_truss)):
        doc = FreeCAD.newDocument("Golden")
        try:
            fr = build(doc)
            doc.recompute()
            text = GOLDEN_HEADER + WeldCutList.to_csv(WeldCutList.cut_list(fr.final()))
            os.makedirs(GOLDEN_DIR, exist_ok=True)
            with open(golden_path(name), "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
            print("wrote", golden_path(name))
        finally:
            FreeCAD.closeDocument(doc.Name)
