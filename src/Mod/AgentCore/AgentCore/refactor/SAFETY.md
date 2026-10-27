---
tags: [type/method, for/human, status/live]
---

# Refactor safety rules (F6): draft for human sign-off

Status: **draft, not yet signed off.** Rewritten after the verifier's air-pocket finding (R8 fixed, R9 and R10 added). The brief reserves the definition of "redundant" for a human decision (10 h budgeted). These are the rules the code in `ops.py` implements today, written so each can be accepted, tightened or struck. Anything not listed here is never merged.

## What the geometry hash does and does not prove

`invariant(doc, fn)` proves the geometry of every shape-bearing object that survives is unchanged within I3's tolerance policy (volume, area, bounding box, centre of mass, topology counts, validity, sampled point-to-surface deviation). It does **not** prove the feature tree is still editable in the way the user expects: names, dependencies, expression links and the meaning of the tree can all change with the geometry intact. The rules below exist to keep that second kind of change small.

## Lifting literals into VarSet or Spreadsheet (`lift_literal`)

| Rule | Statement |
|---|---|
| L1 | Only objects whose type starts with `PartDesign::`, `Sketcher::` or `Part::` are touched, and never a `...Python` type. Draft, Arch, BIM, FEM and CAM objects are left alone because their Python proxies read their own properties. |
| L2 | Only unit-bearing numeric properties (`PropertyLength`, `PropertyDistance`, `PropertyAngle`) and `PropertyFloat`, and only named, driving sketch constraints of type Distance, DistanceX, DistanceY, Radius, Diameter or Angle. Read-only, hidden, output and transient properties are skipped. |
| L3 | A property that already has an expression is never rebound. |
| L4 | The container receives the exact current value; the expression is evaluated straight away and compared for equality with the original. Any difference removes the expression and raises. |
| L6 | An object with a `...Python` object (Arch, Draft, BIM proxy) anywhere downstream is not lifted, because lifting touches it and everything downstream recomputes. Found by the corpus run: touching an Arch section plane's input collapses its shape to a 1 x 1 default when recomputed headless. |
| L5 | One shared container per document (`Parameters`), created if missing. Aliases are `<ObjectName>_<Property>`, made unique, and never look like a cell address or a unit. |

## Relabel (`relabel`)

| Rule | Statement |
|---|---|
| B1 | Only `Label` changes; `Name` is checked unchanged after every assignment. |
| B3 | Only PartDesign, Sketcher and Part objects are relabelled, never a `...Python` type: a Label change touches the object and a Python proxy re-executes. Found by the corpus run: relabelling an Arch section plane changed its geometry headless. |
| B2 | FreeCAD may append digits to keep labels unique; the label actually set is reported. Expressions that refer to an object by label are rewritten by FreeCAD itself. |

## Merging redundant features (`find_redundant`, `merge_redundant`)

A feature is merged away only when **all** of R1 to R10 hold. The merge is one feature at a time, re-checking after each removal, and is meant to run under `invariant`.

| Rule | Statement |
|---|---|
| R1 | It is a `PartDesign::` solid feature inside a `PartDesign::Body` (never a sketch, datum, or body). |
| R2 | It has a `BaseFeature` in the same body (the first solid is never removed). |
| R3 | Its only consumers are the body and the next feature, which uses it through `BaseFeature` alone: no pattern `Originals`, no sketch attached to its faces, no other link. |
| R4 | It is not suppressed. A suppressed feature is something the user parked, not something redundant. |
| R5 | It has no expressions of its own. |
| R6 | No expression anywhere in the document mentions its name or label. |
| R8 | It still equals its base when each of its own size properties (`Length`, `Distance` or `Angle` type) is scaled by 10 and by 0.1, one at a time; a property that is zero is probed at 1 mm or 1 degree instead, so a feature whose sizes are all zero is probed rather than waved through. The probe runs on a copy of the whole Body in a throw-away document, so the live document is never written and the check nests safely inside an F3 `AgentTurn`. |
| R9 | Its type and mode are on an explicit allow-list: `Pad` and `Pocket` in `Length` mode, `Revolution` and `Groove` in `Angle` mode, `LinearPattern` and `PolarPattern` with one occurrence. Every other type, and every open-ended mode (`UpToFace`, `ThroughAll`, `UpToFirst`, `UpToLast`, `UpToShape`, `TwoLengths`, `TwoAngles`), is never merged, however the measurements come out, because those modes depend on geometry the feature does not own (the next face, the stock behind it) and can start to cut after an unrelated edit. |
| R10 | On the same throw-away copy, with the feature suppressed, the Body and every feature after it keep their shapes (the R7 equality, to 1e-9, deviation included). Equality of the feature with its base alone does not prove that taking it out leaves the downstream tree unchanged. |
| R7 | Its resulting shape equals its base feature's shape: same type and validity, identical topology counts, volume, area, bounding box and centre of mass to 1e-9 relative, and sampled point-to-surface deviation of at most 1e-9 mm in both directions (or the same OCCT shape). |

Removal rewires the next feature's `BaseFeature` to the removed feature's base, moves the body's `Tip` if the removed feature was the Tip, then removes the object from the body and the document.

R7 alone is evidence by measurement, not a proof of equivalence: a feature could match its base today and differ after a parameter change (a pocket that cuts nothing at 3 mm and something at 30 mm). R8 measures the feature at 10x and 0.1x each size, R9 refuses every type and mode whose result depends on context (found by the verifier: `data/tests/PocketTest.fcstd` from upstream FreeCAD lost `Pocket001`, an `UpToFace` pocket that equalled its base at the time, under the old R8, which returned true for a feature with no non-zero size and ignored the mode), and R10 requires the downstream tree to survive the removal. Together this is still a measurement on the allow-listed kinds, not a proof; the allow-list is the conservative part. A pocket that equals its base at today's depth but would cut deeper at another depth (the verifier's `AirPocket` probe) is kept, and so is a duplicate pocket that only matches because both cut to the same depth.

## What the oracle covers

The point-to-surface deviation is measured for up to `geomhash.DEFAULT_DEVIATION_BODIES` (400) top-level bodies per document, and each result reports `deviation_covered` and `deviation_truncated` so a larger document is never checked silently with less; beyond the cap only the metric tolerances apply.

## Open questions for the reviewer

1. Should a redundant Tip be merged (the Tip moves to the base), or only interior features?
2. Is the R9 allow-list the right size (should a one-occurrence `Mirrored`, or a `Pad` that adds no volume, be added, or `PolarPattern` dropped)?
3. Should lifting be restricted to the properties that drive a feature (Length, Radius, Angle) and skip incidental ones such as `TaperAngle` = 0?
4. Should relabelling skip objects imported from another file or referenced by a Link?

## Never done

- Merging features by the agent's own judgement of "similar".
- Deleting sketches, datums, bodies, spreadsheets or VarSets.
- Touching `Name`, placement, attachment, or any geometry-defining value other than by binding the same value to an expression.
