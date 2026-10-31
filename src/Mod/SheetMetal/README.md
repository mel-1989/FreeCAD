---
tags: [type/reference, for/agent, status/live]
---

# SheetMetal: LGPL subset of the SheetMetal add-on (cad-fork G07, slice 1)

Sheet metal features and flat patterns, vendored into core as a Python-only module. Headless: nothing imports PySide at module import time; GUI code sits behind `SheetMetalTools.isGuiLoaded()`.

## Source

| Field | Value |
|---|---|
| Repository | https://github.com/shaise/FreeCAD_SheetMetal (`shaise/FreeCAD_SheetMetal`) |
| Commit | `cbf6595` (v0.8.26, 2026-10-17) |
| Licence | LGPL-2.1-or-later (repository `LICENSE`, `package.xml`), applied per file by header |

Every vendored file keeps its original copyright and licence header. Files written for cad-fork carry `SPDX-License-Identifier: LGPL-2.1-or-later`.

## Files taken

| Group | Files |
|---|---|
| Features | `SheetMetalBaseCmd.py`, `SheetMetalBend.py`, `SheetMetalBendSolid.py`, `SheetMetalBendCuts.py`, `SheetMetalExtendCmd.py`, `SheetMetalFoldCmd.py`, `SheetMetalFormingCmd.py`, `SheetMetalRelief.py` |
| Unfolding | `SheetMetalUnfolder.py` (old), `SheetMetalNewUnfolder.py` (new, needs `networkx`), `SheetMetalUnfoldCmd.py` |
| Support | `SheetMetalTools.py`, `SheetMetalKfactor.py`, `SheetMetalLookup.py`, `engineering_mode.py`, `smwb_locator.py` |
| Data | `MDS/mild_steel.csv` |
| Resources | the icons and `.ui` panels that the files above load (`Resources/icons`, `Resources/panels`) |
| Tests | `SMTests/testKfactor.py`, `SMTests/testFolder.py`, `SMTests/testBendCuts.py` |

Local changes to vendored files: `SheetMetalTools.py` and `SheetMetalUnfolder.py` import `importDXF`, `importSVG` and `Draft` inside the functions that use them (they pull in PySide); in `SheetMetalNewUnfolder.py` the bend-line label call now goes to `SheetMetalBendLabels` (below), and a missing `networkx` no longer raises at import (`nx` is `None`, a warning is printed, and the new unfolder is unavailable; the `nx.Graph` return annotation became a string); in `SMTests/testBendCuts.py` two tests skip without `networkx`; `SMTests/__init__.py` (empty upstream) and `testBendCuts.py` (no header upstream) gained an SPDX line.

## Files written for cad-fork

| File | What |
|---|---|
| `Init.py`, `InitGui.py` | Module registration; the workbench lists only the vendored commands |
| `CMakeLists.txt` | Copies and installs the Python, CSV, UI and SVG files |
| `SheetMetalBendLabels.py` | Replacement for the add-on's GPL text helper: seven-segment strokes for digits, `.`, `-` and `^`, no font file needed |
| `TestSheetMetal.py` | Suite entry point (`FreeCADCmd -t TestSheetMetal`) |
| `SMTests/testOracle.py` | Flat-pattern oracle: unfold against the developed-length formula, plus round trip |
| `SMTests/testLicence.py` | Fails if a file here carries a GPL-only header or imports a left-out module |

## Files left out

| File | Why |
|---|---|
| `SheetMetalCmd.py`, `SheetMetalHem.py`, `SheetMetalFromSolid.py`, `SheetMetalCornerReliefCmd.py`, `SheetMetalJunction.py`, `SheetMetalSketch.py`, `SheetMetalText.py`, `ExtrudedCutout.py` | GPL-2.0-or-later headers. Their features (edge flange, hem, convert-from-solid, corner relief, junction, sketch, bend-line text, extruded cutout) are re-written clean-room in later slices by an implementer who has not read them |
| `current.py` | GPL, imported by nothing |
| `SheetMetalBaseShapeCmd.py` | Imports the GPL `SheetMetalCmd` |
| `SketchOnSheetMetalCmd.py` | Imports the GPL `SheetMetalCornerReliefCmd` |
| `Macros/`, `tools/`, `Resources/translations`, `SMprefs.ui`, icons and panels of left-out commands | Not needed by the vendored files, or name left-out commands |

## Dependencies

`networkx` (3.4.2 or later) is needed by the new unfolder only; the pixi environment does not ship it, so the new-unfolder oracle cases skip when it is missing. The old unfolder, the base flange, bend, fold and the K-factor code do not need it.
