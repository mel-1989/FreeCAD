---
tags: [type/method, for/human, status/live]
---

# Refactor safety rules (F6): draft for human sign-off

Status: **draft, not yet signed off.** The brief reserves the definition of "redundant" for a human decision (10 h budgeted). These are the rules the code in `ops.py` implements today, written so each can be accepted, tightened or struck. Anything not listed here is never merged.

## What the geometry hash does and does not prove

`invariant(doc, fn)` proves the geometry of every shape-bearing object that survives is unchanged within I3's tolerance policy (volume, area, bounding box, centre of mass, topology counts, validity, sampled point-to-surface deviation). It does **not** prove the feature tree is still editable in the way the user expects: names, dependencies, expression links and the meaning of the tree can all change with the geometry intact. The rules below exist to keep that second kind of change small.

## Lifting literals into VarSet or Spreadsheet (`lift_literal`)

| Rule | Statement |
|---|---|
| L1 | Only objects whose type starts with `PartDesign::`, `Sketcher::` or `Part::` are touched, and never a `...Python` type. Draft, Arch, BIM, FEM and CAM objects are left alone because their Python proxies read their own properties. |
| L2 | Only unit-bearing numeric properties (`PropertyLength`, `PropertyDistance`, `PropertyAngle`) and `PropertyFloat`, and only named, driving sketch constraints of type Distance, DistanceX, DistanceY, Radius, Diameter or Angle. Read-only, hidden, output and transient properties are skipped. |
| L3 | A property that already has an expression is never rebound. |
| L4 | The container receives the exact current value; the expression is evaluated straight away and compared for equality with the original. Any difference removes the expression and raises. |
| L5 | One shared container per document (`Parameters`), created if missing. Aliases are `<ObjectName>_<Property>`, made unique, and never look like a cell address or a unit. |

## Relabel (`relabel`)

| Rule | Statement |
|---|---|
| B1 | Only `Label` changes; `Name` is checked unchanged after every assignment. |
| B2 | FreeCAD may append digits to keep labels unique; the label actually set is reported. Expressions that refer to an object by label are rewritten by FreeCAD itself. |

## Merging redundant features (`find_redundant`, `merge_redundant`)

A feature is merged away only when **all** of R1 to R7 hold. The merge is one feature at a time, re-checking after each removal, and is meant to run under `invariant`.

| Rule | Statement |
|---|---|
| R1 | It is a `PartDesign::` solid feature inside a `PartDesign::Body` (never a sketch, datum, or body). |
| R2 | It has a `BaseFeature` in the same body (the first solid is never removed). |
| R3 | Its only consumers are the body and the next feature, which uses it through `BaseFeature` alone: no pattern `Originals`, no sketch attached to its faces, no other link. |
| R4 | It is not suppressed. A suppressed feature is something the user parked, not something redundant. |
| R5 | It has no expressions of its own. |
| R6 | No expression anywhere in the document mentions its name or label. |
| R7 | Its resulting shape equals its base feature's shape: same type and validity, identical topology counts, volume, area, bounding box and centre of mass to 1e-9 relative, and sampled point-to-surface deviation of at most 1e-9 mm in both directions (or the same OCCT shape). |

Removal rewires the next feature's `BaseFeature` to the removed feature's base, moves the body's `Tip` if the removed feature was the Tip, then removes the object from the body and the document.

R7 is evidence by measurement, not a proof of equivalence: a feature could match its base today and differ after a parameter change (for example a pocket that cuts nothing at a depth of 3 mm and something at 30 mm). Decision needed from a human: whether R7 alone is enough, or whether a merge must also require the feature type to be one of a short allowlist (pattern with one occurrence, pocket or groove that removes no volume, pad or revolution that adds none).

## Open questions for the reviewer

1. Should a redundant Tip be merged (the Tip moves to the base), or only interior features?
2. Is R7's measured equality enough, or add a type allowlist?
3. Should lifting be restricted to the properties that drive a feature (Length, Radius, Angle) and skip incidental ones such as `TaperAngle` = 0?
4. Should relabelling skip objects imported from another file or referenced by a Link?

## Never done

- Merging features by the agent's own judgement of "similar".
- Deleting sketches, datums, bodies, spreadsheets or VarSets.
- Touching `Name`, placement, attachment, or any geometry-defining value other than by binding the same value to an expression.
