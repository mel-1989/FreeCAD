# SPDX-License-Identifier: LGPL-2.1-or-later
"""Ranking for the command palette. Pure Python, no GUI imports.

A query is split into words; each word is matched against four fields of every command, weighted by how specific the
field is: MenuText (10), command name (7), ToolTip (2.5), workbench label (1.5). A word matches a field word exactly,
as a prefix, by one or two edits (Damerau-Levenshtein, also against a prefix of the field word, so "chamf" and
"filet" work), or through a small synonym table of CAD vocabulary that maps task words onto the words the commands use
("round" onto "fillet", "extrude" onto "pad"). Rarer field words count more (inverse document frequency), a command
that matches every query word beats one that matches a few, and a short MenuText beats a long one. Nothing here knows
a particular query: the weights and the synonym table are general."""

import bisect
import math
import re

FIELD_WEIGHT = {"menu": 10.0, "name": 7.0, "tip": 1.8, "wb": 1.5}
KIND_FACTOR = {"exact": 1.0, "prefix": 0.8, "fuzzy1": 0.7, "fuzzy2": 0.5}
SYN_FACTOR = 0.85

STOP = frozenset("""a an the to of for in on at and or with without my me i we you it its this that these those is are be do does
how want need can could would should please from into onto by as up out all any some one new make makes making
through around across over under between so cannot not just get let use using then than there here what which where when""".split())

# query word -> words commands use. Keys and values are lowercase single words; values are matched like query words.
SYNONYMS = {
    "round": ["fillet"], "rounded": ["fillet"], "rounding": ["fillet"], "smooth": ["fillet", "smooth"], "blend": ["fillet", "blend"],
    "bevel": ["chamfer"], "beveled": ["chamfer"], "bevelled": ["chamfer"], "angled": ["chamfer"],
    "extrude": ["pad", "extrusion"], "extrusion": ["pad", "extrude"], "boss": ["pad"], "protrusion": ["pad"], "pull": ["pad"],
    "push": ["pocket"], "cut": ["pocket", "cut", "subtractive"], "subtract": ["cut", "pocket", "subtractive"], "dig": ["pocket"],
    "cutout": ["pocket"], "recess": ["pocket"], "groove": ["groove", "pocket"], "drill": ["hole"], "bore": ["hole"], "tap": ["hole"],
    "revolve": ["revolution", "revolve"], "lathe": ["revolution"], "spin": ["revolution", "rotate"], "turn": ["revolution", "rotate"],
    "sweep": ["pipe", "sweep"], "rail": ["pipe", "sweep"], "loft": ["loft"], "blend": ["loft"], "shell": ["thickness"], "hollow": ["thickness"],
    "thicken": ["thickness"], "wall": ["thickness"], "mirror": ["mirrored", "mirror", "symmetry"], "reflect": ["mirrored", "mirror"],
    "flip": ["mirror", "reverse"], "pattern": ["pattern", "array"], "array": ["pattern", "array"], "repeat": ["pattern", "array"],
    "duplicate": ["clone", "copy", "pattern"], "clone": ["clone", "copy"], "copy": ["copy", "clone", "duplicate"],
    "linear": ["linear"], "circular": ["polar", "circular"], "polar": ["polar", "circular"], "grid": ["array", "pattern"],
    "delete": ["delete", "remove", "erase"], "erase": ["delete", "remove"], "remove": ["delete", "remove"], "trash": ["delete"],
    "undo": ["undo"], "redo": ["redo"], "revert": ["undo", "revert"],
    "open": ["open"], "load": ["open", "import"], "new": ["new"], "save": ["save"],
    "store": ["save"], "write": ["save", "export"], "export": ["export"], "import": ["import"], "bring": ["import"],
    "print": ["print", "pdf"], "pdf": ["pdf", "print"], "picture": ["image", "screenshot", "photo"], "screenshot": ["screenshot", "save", "image"],
    "photo": ["image", "screenshot"], "render": ["render", "raytracing"],
    "measure": ["measure", "distance", "length"], "distance": ["distance", "measure", "length"], "length": ["length", "distance", "dimension"],
    "size": ["dimension", "length", "size"], "dimension": ["dimension", "constraint", "length"], "dimensions": ["dimension", "constraint"],
    "mass": ["mass", "properties", "volume"], "weight": ["mass", "properties"], "volume": ["volume", "mass", "properties"],
    "angle": ["angle"], "radius": ["radius", "diameter"], "diameter": ["diameter", "radius"], "parallel": ["parallel"],
    "perpendicular": ["perpendicular", "orthogonal"], "square": ["perpendicular", "rectangle"], "tangent": ["tangent", "tangency"],
    "equal": ["equal"], "same": ["equal"], "symmetric": ["symmetric", "symmetry"], "coincident": ["coincident", "point"],
    "fix": ["lock", "block", "fix", "fixed"], "lock": ["lock", "block", "fix"], "freeze": ["lock", "block", "fix"], "horizontal": ["horizontal"],
    "vertical": ["vertical"], "constrain": ["constraint"], "constraints": ["constraint"],
    "line": ["line", "polyline"], "circle": ["circle"], "arc": ["arc"], "ellipse": ["ellipse"], "rectangle": ["rectangle", "rect"],
    "polygon": ["polygon", "regular"], "slot": ["slot"], "spline": ["bspline", "spline", "curve"], "curve": ["curve", "bspline", "spline"],
    "point": ["point"], "text": ["text", "label", "shapestring"], "sketch": ["sketch"], "draw": ["sketch", "draw", "sketcher"],
    "plane": ["plane"], "datum": ["datum"], "reference": ["datum", "reference", "plane"], "axis": ["axis", "datum"],
    "origin": ["origin", "coordinate"], "coordinate": ["coordinate", "origin", "lcs"], "frame": ["coordinate", "lcs"],
    "boolean": ["boolean", "fuse", "cut", "common"], "union": ["fuse", "union"], "merge": ["fuse", "merge", "union", "join"],
    "combine": ["fuse", "union", "join", "compound"], "join": ["fuse", "join", "union"], "intersect": ["common", "intersection"],
    "intersection": ["common", "intersection"], "overlap": ["common", "intersection"], "section": ["section", "slice", "cross"],
    "slice": ["slice", "section"], "split": ["slice", "split", "explode"], "offset": ["offset"], "scale": ["scale", "resize"],
    "resize": ["scale"], "move": ["move", "translate", "placement"], "rotate": ["rotate", "rotation"], "place": ["placement", "move"],
    "align": ["align"], "attach": ["attach", "attachment"], "assemble": ["assembly", "joint"], "assembly": ["assembly"],
    "mate": ["joint"], "connect": ["joint", "connect"], "joint": ["joint"], "bolt": ["screw", "fastener", "hole"], "screw": ["screw", "fastener"],
    "explode": ["explode", "exploded"], "bom": ["bill", "materials"], "parts": ["bill", "parts"], "list": ["list", "bom"],
    "bend": ["bend", "fold", "flange"], "fold": ["bend", "fold", "flange"], "flange": ["flange"], "unfold": ["unfold", "flat", "flatten"],
    "flatten": ["unfold", "flat"], "flat": ["unfold", "flat"], "sheet": ["sheet", "page"], "hem": ["hem"], "tab": ["tab"],
    "weld": ["weld", "bead"], "welding": ["weld"], "frame": ["frame", "structure"], "profile": ["profile", "sketch"], "beam": ["structure", "member"],
    "view": ["view", "standard"], "zoom": ["fit", "zoom", "view"], "fit": ["fit", "all"], "orbit": ["rotate", "view"], "isometric": ["isometric", "axonometric"],
    "front": ["front"], "top": ["top"], "side": ["right", "left", "side"], "perspective": ["perspective", "camera"], "orthographic": ["orthographic", "camera"],
    "wireframe": ["wireframe", "lines", "draw", "style"], "shaded": ["shaded", "draw", "style"], "transparency": ["transparency", "transparent"],
    "transparent": ["transparency", "transparent"], "see": ["view", "show", "visible"], "show": ["show", "visibility", "visible"],
    "hide": ["hide", "visibility", "invisible"], "visible": ["visibility", "show", "toggle"], "invisible": ["hide", "visibility"],
    "color": ["color", "appearance", "colour"], "colour": ["color", "appearance"], "paint": ["color", "appearance"], "material": ["material", "appearance"],
    "appearance": ["appearance", "color"], "texture": ["texture", "appearance"], "light": ["light", "lighting"],
    "close": ["close", "leave", "exit"], "finish": ["leave", "exit", "close"], "helical": ["helix"], "coil": ["helix", "spring"],
    "everything": ["all"], "whole": ["all"], "entire": ["all"], "each": ["all"], "select": ["select", "selection"], "pick": ["select", "selection"], "all": ["all"], "box": ["box", "cube"], "cube": ["cube", "box"],
    "cylinder": ["cylinder"], "sphere": ["sphere"], "cone": ["cone"], "torus": ["torus"], "ring": ["torus", "ring"], "donut": ["torus"],
    "prism": ["prism"], "wedge": ["wedge"], "helix": ["helix", "spiral"], "spring": ["helix"], "thread": ["helix", "thread"], "spiral": ["spiral", "helix"],
    "drawing": ["drawing", "techdraw", "page"], "page": ["page", "template"], "template": ["template", "page"], "annotation": ["annotation", "dimension"],
    "balloon": ["balloon"], "hatch": ["hatch", "section"], "centerline": ["centerline", "center", "line"], "detail": ["detail"],
    "table": ["spreadsheet", "table", "cell"], "cells": ["cell", "spreadsheet"], "spreadsheet": ["spreadsheet"], "formula": ["expression", "formula", "alias"],
    "variable": ["alias", "variable", "expression"], "parameter": ["parameter", "expression", "property"], "property": ["property", "parameter"],
    "alias": ["alias"], "expression": ["expression"], "mesh": ["mesh"], "triangle": ["mesh", "triangle"], "stl": ["mesh", "stl"], "scan": ["points", "scan", "mesh"],
    "cloud": ["points", "cloud"], "analysis": ["analysis", "fem"], "simulate": ["fem", "analysis", "solver"], "simulation": ["fem", "analysis", "solver"],
    "stress": ["fem", "analysis", "stress"], "force": ["force", "load", "constraint"], "load": ["load", "force", "open"], "solver": ["solver"],
    "toolpath": ["job", "path", "toolpath", "tool"], "gcode": ["post", "gcode", "job"], "cnc": ["job", "cam", "tool"], "mill": ["job", "tool", "milling"],
    "milling": ["job", "tool", "milling"], "machine": ["job", "cam", "tool"], "machining": ["job", "cam", "tool"], "pocketing": ["pocket"],
    "console": ["console", "python"], "python": ["python", "console", "script"], "script": ["script", "macro", "python"], "macro": ["macro"],
    "record": ["record", "macro"], "run": ["execute", "run"], "execute": ["execute", "run"], "settings": ["preferences", "settings"],
    "options": ["preferences", "options"], "preferences": ["preferences"], "customize": ["customize", "toolbars"], "toolbar": ["toolbars", "customize"],
    "shortcut": ["shortcut", "keyboard", "customize"], "keyboard": ["keyboard", "shortcut"], "theme": ["theme", "style", "stylesheet"],
    "help": ["help", "documentation"], "docs": ["help", "documentation"], "documentation": ["help", "documentation"], "about": ["about"],
    "bug": ["report", "bug"], "update": ["update", "refresh", "recompute"], "refresh": ["refresh", "recompute"], "rebuild": ["recompute"],
    "recompute": ["recompute"], "regenerate": ["recompute"], "tree": ["tree", "model"], "group": ["group", "part", "container"],
    "folder": ["group", "folder"], "container": ["part", "group", "container"], "body": ["body"], "link": ["link"], "reference": ["link", "reference"],
    "check": ["check", "geometry", "validate"], "validate": ["check", "geometry"], "repair": ["repair", "fix", "check"], "heal": ["repair", "check"],
    "simplify": ["simplify", "refine", "decimate"], "refine": ["refine", "simplify"], "reduce": ["decimate", "simplify"], "cleanup": ["refine", "clean"],
    "auto": ["automatic", "auto"], "automatically": ["automatic", "auto"], "dock": ["dock", "panel"], "panel": ["panel", "dock", "view"],
    "full": ["fullscreen"], "fullscreen": ["fullscreen"], "window": ["window", "tile", "cascade"], "tile": ["tile"], "close": ["close"], "quit": ["quit", "exit"],
    "exit": ["quit", "exit"], "version": ["version", "about"], "vault": ["vault", "pdm"], "revision": ["revision", "pdm"], "checkin": ["check", "in"],
    "lock": ["lock"], "release": ["release", "state"], "workbench": ["workbench"], "switch": ["workbench", "switch"], "dimension": ["dimension"],
    "drawing": ["drawing"], "leader": ["leader", "line"], "arrow": ["arrow"], "rectangle": ["rectangle"], "wire": ["wire", "polyline"],
    "polyline": ["polyline", "wire"], "stairs": ["stairs"], "wall": ["wall"], "door": ["door"], "window": ["window"], "roof": ["roof"],
    "floor": ["floor", "level", "slab"], "building": ["building", "site"], "site": ["site"], "ifc": ["ifc"], "pipe": ["pipe"], "tube": ["pipe", "tube"],
}

_WORD = re.compile(r"[a-z0-9]+")


def stem(w):
    """Crude plural and suffix folding (holes -> hole, sketches -> sketch, thinner -> thin); applied to commands and queries alike."""
    n = len(w)
    if n > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if n > 4 and w.endswith(("ches", "shes", "sses", "xes")):
        return w[:-2]
    if n > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is", "as")):
        return w[:-1]
    if n > 5 and w.endswith("ing"):
        return w[:-3]
    if n > 5 and w.endswith("er") and w[-3] not in "aeiou":
        return w[:-2]
    return w


def words_of(text):
    """Lowercase stemmed words; CamelCase and underscores split, so PartDesign_AdditiveBox gives part design additive box."""
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    return [stem(w) for w in _WORD.findall(text.lower())]


def _dl_within(a, b, k):
    """True with the distance when the Damerau-Levenshtein (optimal string alignment) distance of a and b is at most k."""
    la, lb = len(a), len(b)
    if abs(la - lb) > k:
        return None
    prev2 = None
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        lo = i
        for j in range(1, lb + 1):
            c = 0 if a[i - 1] == b[j - 1] else 1
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + c)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                v = min(v, prev2[j - 2] + 1)
            cur[j] = v
            if v < lo:
                lo = v
        if lo > k:
            return None
        prev2, prev = prev, cur
    return prev[lb] if prev[lb] <= k else None



# Phrase rules: when every stem of the left side occurs in the query, the right-hand words are searched as well (an intent
# that no single word carries). Same general CAD vocabulary as SYNONYMS, written per intent, not per query.
RULES = [
    ({"same", "length"}, ["equal"]), ({"same", "size"}, ["equal"]), ({"equal", "length"}, ["equal"]), ({"same", "radius"}, ["equal"]),
    ({"see", "through"}, ["transparency"]), ({"look", "through"}, ["transparency"]), ({"x", "ray"}, ["transparency"]),
    ({"whole", "model"}, ["fit", "all"]), ({"everything", "screen"}, ["fit", "all"]), ({"show", "everything"}, ["fit", "all"]),
    ({"look", "above"}, ["top", "view"]), ({"from", "above"}, ["top", "view"]), ({"from", "below"}, ["bottom", "view"]), ({"from", "side"}, ["right", "left", "view"]),
    ({"from", "front"}, ["front", "view"]), ({"from", "behind"}, ["rear", "view"]), ({"from", "back"}, ["rear", "view"]),
    ({"how", "heavy"}, ["mass", "properties"]), ({"how", "much", "weigh"}, ["mass", "properties"]), ({"center", "gravity"}, ["mass", "properties"]),
    ({"how", "long"}, ["measure", "distance"]), ({"how", "far"}, ["measure", "distance"]), ({"how", "big"}, ["measure", "dimension"]),
    ({"fix", "point"}, ["lock", "block"]), ({"cannot", "move"}, ["lock", "block", "fixed"]), ({"not", "move"}, ["lock", "block", "fixed"]),
    ({"hold", "place"}, ["lock", "block", "fixed"]), ({"stay", "put"}, ["lock", "block", "fixed"]),
    ({"make", "horizontal"}, ["horizontal"]), ({"make", "vertical"}, ["vertical"]), ({"make", "parallel"}, ["parallel"]),
    ({"right", "angle"}, ["perpendicular"]), ({"90", "degree"}, ["perpendicular"]), ({"touch", "each", "other"}, ["coincident", "tangent"]),
    ({"join", "point"}, ["coincident"]), ({"snap", "together"}, ["coincident", "joint"]),
    ({"set", "size"}, ["dimension", "radius", "diameter", "length"]), ({"set", "radius"}, ["radius"]), ({"set", "length"}, ["length", "distance"]),
    ({"size", "circle"}, ["radius", "diameter"]), ({"size", "hole"}, ["diameter", "radius"]),
    ({"cross", "line"}, ["trim", "split"]), ({"line", "cross"}, ["trim", "split"]), ({"cut", "line"}, ["trim", "split"]), ({"cut", "edge"}, ["trim", "split"]),
    ({"remove", "material"}, ["pocket", "groove", "subtractive"]), ({"cut", "away"}, ["pocket", "groove", "subtractive"]), ({"cut", "hole"}, ["hole", "pocket"]),
    ({"make", "hole"}, ["hole"]), ({"add", "hole"}, ["hole"]), ({"add", "material"}, ["pad", "additive"]), ({"add", "volume"}, ["pad", "additive"]),
    ({"sketch", "3d"}, ["pad", "extrude"]), ({"sketch", "solid"}, ["pad", "extrude"]), ({"2d", "3d"}, ["pad", "extrude"]), ({"give", "thickness"}, ["pad", "thickness"]),
    ({"profile", "axis"}, ["revolution", "revolve"]), ({"around", "axis"}, ["revolution", "revolve"]),
    ({"along", "path"}, ["pipe", "sweep"]), ({"follow", "path"}, ["pipe", "sweep"]), ({"between", "profile"}, ["loft"]),
    ({"several", "time"}, ["pattern", "array"]), ({"many", "copy"}, ["pattern", "array"]), ({"multiple", "copy"}, ["pattern", "array"]),
    ({"copy", "row"}, ["linear", "pattern"]), ({"in", "row"}, ["linear", "pattern"]), ({"around", "circle"}, ["polar", "pattern"]), ({"around", "axis"}, ["polar"]),
    ({"two", "solid"}, ["boolean", "fuse", "cut", "common"]), ({"two", "shape"}, ["boolean", "fuse", "cut", "common"]), ({"two", "body"}, ["boolean", "fuse", "cut", "common"]),
    ({"join", "solid"}, ["fuse", "union"]), ({"join", "shape"}, ["fuse", "union"]), ({"glue", "together"}, ["fuse", "union"]), ({"add", "solid"}, ["fuse", "union"]),
    ({"subtract", "solid"}, ["cut"]), ({"one", "from", "another"}, ["cut", "difference"]), ({"take", "away"}, ["cut", "difference"]),
    ({"overlap", "shape"}, ["common", "intersection"]), ({"shared", "volume"}, ["common", "intersection"]), ({"what", "common"}, ["common", "intersection"]),
    ({"hollow", "out"}, ["thickness"]), ({"thinner", "wall"}, ["thickness"]), ({"wall", "thickness"}, ["thickness"]), ({"thin", "wall"}, ["thickness"]),
    ({"round", "corner"}, ["fillet"]), ({"round", "edge"}, ["fillet"]), ({"soften", "edge"}, ["fillet", "chamfer"]), ({"sharp", "edge"}, ["fillet", "chamfer"]),
    ({"flip", "across"}, ["mirror"]), ({"reflect", "plane"}, ["mirror"]), ({"other", "side"}, ["mirror", "symmetry"]), ({"mirror", "image"}, ["mirror"]),
    ({"sheet", "metal"}, ["sheet", "flange", "bend"]), ({"bent", "part"}, ["unfold", "flat"]), ({"flat", "pattern"}, ["unfold"]), ({"lay", "flat"}, ["unfold", "flat"]),
    ({"turn", "sheet"}, ["convert", "sheet"]), ({"solid", "sheet"}, ["convert"]), ({"corner", "relief"}, ["relief"]),
    ({"connect", "part"}, ["joint"]), ({"attach", "part"}, ["joint"]), ({"can", "turn"}, ["revolute", "cylindrical", "ball"]), ({"can", "slide"}, ["slider", "cylindrical"]),
    ({"can", "rotate"}, ["revolute", "cylindrical", "ball"]), ({"hinge"}, ["revolute"]), ({"pivot"}, ["revolute", "ball"]), ({"rigid", "together"}, ["fixed", "rigid"]),
    ({"lock", "part"}, ["fixed", "rigid", "grounded"]), ({"fix", "part"}, ["fixed", "grounded"]),
    ({"list", "part"}, ["bill", "materials"]), ({"part", "list"}, ["bill", "materials"]), ({"parts", "count"}, ["bill", "materials"]),
    ({"take", "apart"}, ["exploded", "view"]), ({"pull", "apart"}, ["exploded", "view"]), ({"show", "apart"}, ["exploded", "view"]),
    ({"undo"}, ["undo"]), ({"go", "back"}, ["undo"]), ({"last", "change"}, ["undo"]), ({"step", "back"}, ["undo"]),
    ({"store", "file"}, ["save"]), ({"write", "file"}, ["save"]), ({"keep", "work"}, ["save"]), ({"save", "copy"}, ["save", "copy"]),
    ({"step", "file"}, ["import", "export"]), ({"iges"}, ["import", "export"]), ({"another", "cad"}, ["export"]), ({"other", "cad"}, ["export"]),
    ({"send", "model"}, ["export"]), ({"share", "model"}, ["export"]), ({"to", "other", "program"}, ["export"]),
    ({"drawing", "page"}, ["page", "techdraw"]), ({"paper", "drawing"}, ["page"]), ({"engineering", "drawing"}, ["page", "view"]), ({"2d", "drawing"}, ["page", "view"]),
    ({"new", "drawing"}, ["page"]), ({"make", "drawing"}, ["page"]), ({"print", "drawing"}, ["page", "print"]),
    ({"g", "code"}, ["post", "process"]), ({"cnc", "program"}, ["post", "process", "job"]), ({"nc", "file"}, ["post", "process"]),
    ({"stress"}, ["fem", "analysis", "force", "pressure"]), ({"apply", "force"}, ["force", "load"]), ({"fixed", "support"}, ["fixed", "displacement"]),
    ({"colour", "object"}, ["appearance", "color"]), ({"change", "color"}, ["appearance", "color"]), ({"paint", "face"}, ["appearance", "color", "face"]),
    ({"python", "console"}, ["python", "console", "command", "line"]), ({"run", "script"}, ["macro", "execute"]), ({"type", "command"}, ["command", "line"]),
    ({"new", "part"}, ["body", "part"]), ({"start", "part"}, ["body"]), ({"new", "file"}, ["new", "document"]), ({"blank", "document"}, ["new", "document"]),
    ({"full", "screen"}, ["fullscreen"]), ({"hide", "everything"}, ["hide", "object"]), ({"show", "all"}, ["show", "object"]),
    ({"rotate", "view"}, ["rotate", "view"]), ({"turn", "model"}, ["turntable", "rotate"]), ({"spin", "model"}, ["turntable"]),
    ({"3d", "text"}, ["shape", "text"]), ({"write", "text"}, ["text"]), ({"engrave", "text"}, ["text", "engrave"]),
    ({"thread"}, ["helix", "thread"]), ({"gear"}, ["gear", "involute", "sprocket"]), ({"shaft"}, ["shaft", "wizard"]),
    ({"weld", "line"}, ["weld", "bead"]), ({"steel", "profile"}, ["member", "structural"]), ({"frame", "beam"}, ["member", "structural"]),
    ({"corner", "joint"}, ["mitre", "butt"]), ({"cut", "list"}, ["cut", "list"]), ({"check", "in"}, ["check", "in"]),
]
_SYN = {}
for _k, _v in SYNONYMS.items():
    _SYN.setdefault(stem(_k), []).extend(stem(x) for x in _v)
_STOP = frozenset(STOP) | frozenset(stem(w) for w in STOP)
_RULES = [(frozenset(stem(w) for w in lhs), [stem(w) for w in rhs]) for lhs, rhs in RULES]

# Small priors: the everyday workbenches and the core commands win ties against the same MenuText in a niche workbench.
WB_PRIOR = {"": 1.06, "PartWorkbench": 1.04, "PartDesignWorkbench": 1.04, "SketcherWorkbench": 1.04, "AssemblyWorkbench": 1.03,
            "DraftWorkbench": 1.02, "SpreadsheetWorkbench": 1.02, "SMWorkbench": 1.02, "TechDrawWorkbench": 1.01}
ACTIVE_BOOST = 1.08
# Verbs that most task phrasings contain and most command names do not mean ("draw a circle" is not about Draw commands).
GENERIC = frozenset(stem(w) for w in "draw make create add new set show toggle edit apply insert use select change".split())
GENERIC_FACTOR = 0.25


class Ranker:
    def __init__(self, entries, wb_labels=None):
        self.entries = entries
        wb_labels = wb_labels or {}
        n = len(entries)
        self.vocab = {f: {} for f in FIELD_WEIGHT}   # field -> word -> [entry index]
        self.joined = {}                             # two adjacent words run together -> [(field, entry index)]
        self.prior = [WB_PRIOR.get(e.get("wb", ""), 1.0) * (0.8 if e["name"].startswith(("Std_Test", "Test_", "Std_MDITest")) else 1.0)
                      for e in entries]
        self.wb_of = [e.get("wb", "") for e in entries]
        self.menu_len = []
        df = {}
        for idx, e in enumerate(entries):
            mw = words_of(e["menu"])
            self.menu_len.append(len(mw))
            nw = words_of(e["name"].split("_", 1)[-1])
            tw = words_of(e.get("tip", ""))[:60]
            ww = words_of(wb_labels.get(e.get("wb", ""), e.get("wb", "")).replace("Workbench", "")) if e.get("wb") else ["core", "std"]
            for field, ws in (("menu", mw), ("name", nw), ("tip", tw), ("wb", ww)):
                for w in set(ws):
                    self.vocab[field].setdefault(w, []).append(idx)
            for field, ws in (("menu", mw), ("name", nw)):    # "Cut list" is also reachable as "cutlist"
                for a, b in zip(ws, ws[1:]):
                    self.joined.setdefault(a + b, []).append((field, idx))
            for w in set(mw) | set(nw) | set(tw):
                df[w] = df.get(w, 0) + 1
        self.idf = {w: math.log((n + 1) / (c + 0.5)) / math.log(n + 1) + 0.25 for w, c in df.items()}
        self.sorted_vocab = {f: sorted(v) for f, v in self.vocab.items()}
        self.fuzzy_words = sorted(set(self.vocab["menu"]) | set(self.vocab["name"]))
        self._cache = {}
        self.menu_lc = [e["menu"].lower() for e in entries]
        self.menu_words = [words_of(e["menu"]) for e in entries]

    def _word_hits(self, t):
        """{(field, word): factor} for one query word, best factor per pair; fuzzy only when menu/name have no exact or prefix hit."""
        hit = self._cache.get(t)
        if hit is not None:
            return hit
        hits = {}
        strong = False
        for field in ("menu", "name", "tip", "wb"):
            sv = self.sorted_vocab[field]
            if t in self.vocab[field]:
                hits[(field, t)] = KIND_FACTOR["exact"]
                if field in ("menu", "name"):
                    strong = True
            i = bisect.bisect_left(sv, t)
            while i < len(sv) and sv[i].startswith(t):
                if sv[i] != t and len(t) >= 2:
                    hits.setdefault((field, sv[i]), KIND_FACTOR["prefix"] * (0.6 + 0.4 * len(t) / len(sv[i])))
                    if field in ("menu", "name"):
                        strong = True
                i += 1
        if not strong and len(t) >= 3:
            k = 1 if len(t) <= 6 else 2
            for w in self.fuzzy_words:
                if abs(len(w) - len(t)) > k + 3:
                    continue
                d = _dl_within(t, w, k) if len(t) >= 4 or len(w) == len(t) else None
                kind = None
                if d is not None:
                    kind = "fuzzy1" if d <= 1 else "fuzzy2"
                elif len(w) > len(t) >= 4:
                    d = _dl_within(t, w[:len(t)], 1)
                    if d is not None:
                        kind = "fuzzy2"
                if kind:
                    for field in ("menu", "name"):
                        if w in self.vocab[field]:
                            hits[(field, w)] = max(hits.get((field, w), 0), KIND_FACTOR[kind])
        self._cache[t] = hits
        return hits

    def _token_scores(self, t, scale, scores):
        gen = GENERIC_FACTOR if t in GENERIC else 1.0
        for (field, w), fac in self._word_hits(t).items():
            idf = self.idf.get(w, 0.5) if fac == 1.0 else 0.8
            fac *= gen     # a prefix or a typo: the rarity of the word it landed on says little
            base = FIELD_WEIGHT[field] * fac * scale * idf
            for idx in self.vocab[field][w]:
                if base > scores.get(idx, 0.0):
                    scores[idx] = base

    def search(self, query, limit=20, active_wb=""):
        """[(entry index, score)] best first. `active_wb` (a workbench class name) gets a small boost."""
        q = query.lower().strip()
        raw = words_of(q)
        toks = [t for t in raw if t not in _STOP] or raw
        if not toks:
            return []
        per_tok = []
        for t in toks:
            scores = {}
            self._token_scores(t, 1.0, scores)
            for syn in _SYN.get(t, ()):
                if syn != t:
                    self._token_scores(syn, SYN_FACTOR, scores)
            per_tok.append(scores)
        # adjacent query words that form one command word ("cut list" typed "cutlist" or the reverse)
        bonus = {}
        for key in [a + b for a, b in zip(raw, raw[1:])] + raw:
            for field, idx in self.joined.get(key, ()):
                bonus[idx] = max(bonus.get(idx, 0.0), FIELD_WEIGHT[field] * 1.6)
        rawset = set(raw)
        for lhs, rhs in _RULES:
            if lhs <= rawset:
                tmp = {}
                for w in rhs:
                    self._token_scores(w, 1.0, tmp)
                for idx, v in tmp.items():
                    bonus[idx] = bonus.get(idx, 0.0) + 0.9 * v / max(1, len(rhs)) ** 0.5
        qwords = set(toks)
        for t in toks:
            qwords.update(_SYN.get(t, ()))
        cand = set(bonus)
        for s_ in per_tok:
            cand.update(s_)
        results = []
        ntok = len(toks)
        for idx in cand:
            total = 0.0
            matched = 0
            for s_ in per_tok:
                v = s_.get(idx)
                if v:
                    total += v
                    matched += 1
            if matched < ntok:
                total *= (0.35 + 0.65 * matched / ntok) * (0.8 if matched * 2 < ntok else 1.0)
            total += bonus.get(idx, 0.0)
            ml = self.menu_lc[idx]
            if ml == q:
                total += 12
            elif ml.startswith(q):
                total += 4
            mw = self.menu_words[idx]
            if mw:    # a menu text the query covers fully beats a longer one that merely contains it
                total *= 0.75 + 0.25 * sum(1 for w in mw if w in qwords) / len(mw)
            total -= 0.15 * max(0, self.menu_len[idx] - 2)
            total *= self.prior[idx]
            if active_wb and self.wb_of[idx] == active_wb:
                total *= ACTIVE_BOOST
            results.append((total, -len(self.entries[idx]["menu"]), idx))
        results.sort(reverse=True)
        return [(idx, round(sc, 3)) for sc, _, idx in results[:limit]]
