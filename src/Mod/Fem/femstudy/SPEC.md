---
tags: [type/method, for/agent, status/live]
---

# femstudy: static study specification (G11 slice 1a)

The contract between the corpus, the oracle and the implementation of `femstudy.run_static`. Written in run 067 before any study code. The implementer may not change a key, a type or a code without recording why in the run note.

## Files the implementation declares

The mutant script (`femtest/app/study_mutants.py`) patches these names, so they are fixed.

| File | Holds |
|---|---|
| `femstudy/__init__.py` | `define_static`, `run_static`, `is_stale`, `stored_result`, the exception classes |
| `femstudy/spec.py` | `parse_spec` (structure and type errors raise), `parse_quantity` (units and dimensions) |
| `femstudy/model.py` | builds the Analysis and constraint objects, resolves references |
| `femstudy/checks.py` | pre-solve checks (refusals) and post-solve checks (suspect) |
| `femstudy/run.py` | Gmsh at the levels, writes the CalculiX deck, runs ccx, parses outputs, element cap |
| `femstudy/convergence.py` | Richardson estimate, observed order, singularity test, verdict |
| `femstudy/stale.py` | input hash, the `stale` flag |
| `femstudy/deck.py` | edits the written CalculiX deck in the study's own working directory (load correction, output requests, load on constrained nodes) and reads the `.dat` |

## Entry points

`define_static(doc, spec) -> str` creates (or replaces the contents of) the Analysis named `spec.get("name", "StaticStudy")` with its solver, material and constraint objects and stores the spec and input hash on it. It does not mesh or solve. `run_static(doc, spec_or_name) -> dict` takes either a spec dict (it calls `define_static` first) or the name of an existing study Analysis (it runs what is stored, so the references resolve at run time against the geometry as it is now). `is_stale(doc, name) -> bool` and `stored_result(doc, name) -> dict | None` read back. Neither entry point changes the body.

## The spec dict

```python
{
  "body": "Body",                                   # name of a document object with a solid Shape (required)
  "material": {"E": "210 GPa", "nu": 0.3,          # E, nu required; density, yield optional
               "density": "7850 kg/m^3", "yield": "250 MPa"}
              # or a str: the name of a Material card (slice 4; not exercised by the corpus)
  "supports": [{"refs": [["Body", "Face1"]], "fixed": "xyz"}],   # list; may be empty (then refused/no_support)
  "loads":    [{"type": "force",    "value": "5 kN", "refs": [["Body", "Face2"]], "direction": [0, 0, -1]},
               {"type": "pressure", "value": "1 MPa", "refs": [["Body", "Face6"]]},
               {"type": "gravity",  "value": "9.81 m/s^2", "direction": [0, 0, -1]}],
  "options": {"h0": "5 mm", "levels": 3, "ratio": 2, "target": 0.01, "max_elements": 200000},
  "name": "StaticStudy"                             # optional Analysis name
}
```

| Key | Type | Meaning |
|---|---|---|
| `refs` | list of `[object_name, sub_element]` | `sub_element` is `FaceN`, `EdgeN` or `VertexN`, resolved at run time; see References |
| `fixed` | str over the letters `x y z` | global translation directions held at zero; `"xyz"` is a fixed support; `"z"` is a roller in z; the corpus uses it for symmetry planes |
| force `value` | quantity string | total force on the referenced elements, spread by the stock node-load distribution; `direction` is a global vector, not necessarily unit |
| pressure `value` | quantity string | positive presses into the body, normal to each referenced face |
| gravity | quantity string | acceleration on the whole body, no `refs` |
| `E`, `yield` | quantity string | pressure dimension; a bare number is never read as MPa or Pa (refused/unit_missing) |
| `nu` | number or numeric string | dimensionless, must be in [0, 0.5) |
| `density` | quantity string | mass per volume |
| `h0` | quantity string of length | coarsest element size; levels are `h0`, `h0/ratio`, `h0/ratio^2` |
| `levels`, `ratio`, `target`, `max_elements` | int, number, number, int | defaults 3, 2, 0.01, 200000 |

Units inside FreeCAD are mm, N, MPa; the oracle compares in those.

## References

A reference names a document object and a sub-element and is resolved at run time. This is the rule the existing FEM constraints already follow (survival harness scenarios S110 to S122): `define_static` stores the references as the `References` property of the stock constraint objects (`App::PropertyLinkSubList`, global scope), so the G01 element map rewrites them when the body is edited.

Observed on this fork (run 067, PartDesign body, stock constraints, see `BASELINE.md`): after an added pocket, a reference to a surviving face is renumbered (`Face5` became `Face2`), a reference to a removed face becomes `?Face6` and the constraint's state is `Invalid`; after a fillet inserted behind the pad both end-face references became `?Face5` and `?Face6` and `Invalid` although the faces still exist (G01 is conservative there). A reference is lost when its stored sub-name starts with `?`, the constraint state contains `Invalid`, or `getSubObject` fails. A lost reference gives `refused` with code `reference_lost` and a reason naming the constraint and the sub-element; the study never resolves a lost reference by index, because the index may now name another face (a moved load).

## Return dict

```python
{
  "verdict": "converged", "code": "ok", "reason": "...",   # reason is one sentence naming the cause and the object; "" is not allowed for any verdict but converged
  "units": {"length": "mm", "force": "N", "stress": "MPa", "energy": "mJ"},
  "levels": [{"h": 5.0, "nodes": 0, "elements": 0, "max_displacement": 0.0, "energy": 0.0, "max_von_mises": 0.0,
              "reaction_sum": [fx, fy, fz], "applied_sum": [fx, fy, fz], "solve_seconds": 0.0, "result": "CCX_Results"}],
  "extrapolated": {"max_displacement": 0.0, "energy": 0.0, "max_von_mises": 0.0},   # Richardson values
  "order": 1.9,                  # observed order of max_displacement, None if not defined
  "estimated_error": 0.002,      # max over displacement and energy of |extrapolated - finest| / |extrapolated|
  "warnings": [{"code": "...", "text": "..."}],
  "analysis": "StaticStudy", "stale": False,
}
```

Additions to the brief's list, needed by the oracle: `units` (brief check 4), `levels[i].result` (the name of the `Fem::FemResultObject` of that level, so the oracle can sample stresses independently of the study), `reaction_sum` and `applied_sum` as 3-vectors in N. `energy` is the total strain energy in N mm (= mJ). Three further keys per level are diagnostics for the equilibrium: `rf_total` (the `RF` totals of the union of all constrained nodes), `load_on_constrained` (the whole external load on those nodes, free directions included) and `delivered_sum` (the sum of the nodal loads in the deck after correction); `reaction_sum` is `rf_total - load_on_constrained` (ccx prints RF = K u, which holds the node's own external load). A level that was not reached (refused, failed, cap) is absent; `levels` may be empty.

## Verdicts and codes (closed list, one code per cause)

| Verdict | Code | Cause |
|---|---|---|
| `converged` | `ok` | all checks passed, estimated error at most `target`, no stress growth |
| `unconverged` | `not_monotone` | the three displacement or energy values do not move monotonically toward a limit |
| `unconverged` | `error_over_target` | estimated error above `target` |
| `unconverged` | `mesh_not_refined` | a finer level has no more elements than the one before: Gmsh did not refine, so no convergence statement is possible |
| `unconverged` | `element_cap` | refinement stopped because the next level would exceed `max_elements` |
| `stress_singular` | `stress_singular` | displacement and energy within target, max von Mises grew by more than 30% on the last refinement |
| `suspect` | `equilibrium` | `\|reaction_sum + applied_sum\| / \|applied_sum\|` above 1e-3 on any level |
| `suspect` | `large_displacement` | max displacement above 10% of the smallest bounding-box edge |
| `suspect` | `yield_exceeded` | max von Mises above `yield`, when `yield` is set |
| `refused` | `no_support` | `supports` empty |
| `refused` | `no_load` | `loads` empty |
| `refused` | `rigid_body_motion` | the supports leave a rigid-body mode (for instance every support is a single line or point fixed in all directions, or translations are not all held) |
| `refused` | `material_incomplete` | `E` or `nu` missing |
| `refused` | `unit_missing` | a quantity string without a unit, or a bare number where a quantity is required |
| `refused` | `unit_unknown` | a unit FreeCAD cannot parse (`kip`, `furlong`); never guessed |
| `refused` | `unit_dimension` | a quantity of the wrong dimension (`210 N` for `E`) |
| `refused` | `poisson_range` | `nu` outside [0, 0.5) |
| `refused` | `body_invalid` | zero volume, not a solid, or the object missing |
| `refused` | `reference_lost` | a support or load reference no longer resolves |
| `failed` | `mesher_missing` | no `gmsh` on PATH |
| `failed` | `solver_missing` | no `ccx` on PATH |
| `failed` | `mesher_failed` | Gmsh failed or produced no volume elements |
| `failed` | `solver_failed` | ccx exit code not zero, or the result object holds no displacements |
| `failed` | `result_nan` | a `nan` or `inf` in a result |
| `failed` | `internal_error` | an unexpected exception inside the study; the reason names its type and message (a bug in the study, never a verdict about the model) |

Order of tests as implemented: structure (raises); `no_support`, `no_load`; `material_incomplete`, `unit_*`, `poisson_range`; `body_invalid`; `reference_lost`; `rigid_body_motion` (after the reference check, because it needs the references to resolve); then the `failed` tool checks (`mesher_missing`, `solver_missing`), then per level the post-solve checks (`equilibrium`, then `large_displacement`; each stops the run at the first level that fails), then `yield_exceeded` on the finest level only, then convergence. `stress_singular` is decided only when displacement and energy are within target; it beats `unconverged`. The singularity test uses the last refinement step (measured growth on the corpus: see Checks that may not hold).

## Exceptions

`StudySpecError(ValueError)` is raised by `run_static` and `define_static` for a malformed spec: unknown key at any level, wrong Python type, a missing required key (`body`, `material`, `supports`, `loads`, `options.h0`), a non-numeric `nu`, a `fixed` string with letters other than `x y z`, an unknown load `type`. Nothing else is raised to the caller: every other failure is a returned verdict. Internal classes `SolverNotFound` and `MesherNotFound` exist and are converted to `failed` with `solver_missing` and `mesher_missing`.

## Stale results

`define_static` writes four properties on the Analysis, so they persist in the saved file: `StudySpec` (JSON of the spec), `InputHash` (set by `run_static`), `ResultJSON` (the returned dict), `Stale` is not stored. `is_stale` recomputes the hash and compares it with `InputHash` on every call; the returned dict's `stale` is set the same way when `stored_result` reads it back. The hash is the sha256 of: the canonical spec JSON; for the body, its volume, bounding box and the signature (type, area or length, centre rounded to 1e-6) of every referenced element as it resolves now; the resolved material values in base units; the options. Any change to the body, a load, a support or a material value changes the hash. The flag is never cached, so it cannot go missing after an edit.

## Notes for the implementer

- `ccxtools.FemToolsCcx.ccx_run()` returns the exit code and only prints a non-zero one; check it and check that `FemResultObject.DisplacementLengths` is non-empty (run 067 saw empty result objects in 2 of 3 runs of one case).
- Gmsh is multi-threaded by default (`ParallelProcessing = True`) and gives a different mesh in every run (element counts of one problem differed by 4 percent). Set `ParallelProcessing = False` on the mesh object: with it four runs of the unit-equivalence problem agree to the last bit; without it they differ by 1e-4 in displacement.
- `MeshSizeFromCurvature` defaults to 12 per full circle, so on a round section it overrides `h`: the three levels `h0`, `h0/2`, `h0/4` of a 12 to 16 mm circular bar gave the same mesh and the estimated error read 0.0 while the answer was 1% off. Set it to 0 on the study mesh object.
- Total strain energy: add `*EL PRINT, ELSET=Eall, TOTALS=ONLY` / `ELSE` before `*END STEP` in the written deck; ccx 2.23 prints `total internal energy for set EALL` in the `.dat` file. The stock writer already prints `RF` totals for each Fixed constraint and each Displacement constraint holding a translation, as `total force (fx,fy,fz) for set <constraint name>`; `*NODE PRINT, NSET=Nall, TOTALS=ONLY` with `RF` sums to zero and is useless.
- The `RF` totals leave out load applied directly on a constrained node (a pressure face that shares an edge with the support face): measured 0.5% to 2.1% short on cantilever, plate and cylinder cases. The equilibrium test must add the load that sits on constrained nodes (recomputable from the written deck: `DLOAD` faces, midside nodes carry one third of face area times pressure on `C3D10`, corner nodes none) or use the external-work form. See Checks that may not hold.
- `Fem::ConstraintForce.Direction` must be a linear edge or planar face. The baseline driver makes a construction `Part::Feature` line when the body has no straight edge parallel to the wanted direction (round bars).
- `FemToolsCcx` writes `Mesh.inp` and results into its working directory and `gmshtools` writes `.geo`, `.brep` and `.vtk` into the system temp folder; set `TMPDIR` to a folder that survives cleaning.

## Checks that may not hold

Each item was measured on the mini in run 067 with the stock pipeline (`ObjectsFem` constraints, `Fem::FemMeshGmsh` through `gmshtools`, `FemToolsCcx`, C3D10, ccx 2.23, Gmsh 4.15.2). Nothing below was tuned to the corpus; where the corpus differs from the brief's wording, the difference is listed and the reason given. The numbers are in `BASELINE.md` and in the JSON lines under the scratch folder of the run.

1. **Check 3, group A, circular sections within 1e-5.** Not reached, and no practical mesh reaches it. With the curvature override gone (`MeshSizeFromCurvature = 0`), serial Gmsh, `h0` = d/2 and the study's load correction, the finest level (25,121 elements for A06, 12,697 for A07) is 1.11e-4 and 2.11e-4 off the closed-form displacement and 4.7e-3 and 4.2e-3 off in max von Mises (the max is a nodal value on a faceted second-order boundary). The study's own estimate of the error is 3.7e-5 and 7.2e-5, and its target is 1e-2, so it ends `converged`, which the oracle then reports as SILENT_WRONG against the 1e-5 bar. The bar and the default target are inconsistent for round bars; the six rectangular bars are exact (displacement within 3e-6, max von Mises 1.00e-5). Not changed in run 067: the decision (judge round bars at 2%, drop them, or give group A `target: 1e-5` so the study ends `unconverged` on them) is the orchestrator's. Run 067 repair: a consistent quadratic-face traction left the result unchanged to 7 digits, so the load distribution is not the cause; mid-length stress is within about 6e-6, and the peak is a local effect at the loaded face. Round bars are now judged at 1e-3 displacement and 1e-2 stress. That bar was set after the study code existed and is listed in the brief.
2. **Check 3, group F, centre deflection within 5%.** Corrected in run 067. The earlier excess of 5% to 14% over Kirchhoff, called unexplained, was the soft-support boundary layer: the whole side face held out of plane only (and, before that, a mid-thickness line) lets the edge rotate in a way a hard simple support does not. A hard simple support (each side face holds z and the in-plane direction along that side) in plain CalculiX C3D20 gives 1.046, 1.012 and 1.002 times Kirchhoff at h/a 0.1, 0.05 and 0.02, in line with shear deformation. The group F cases are built that way now, and all six end `converged` within the 5% bar at the finest level (F01 1.18%, F02 4.55%, F03 0.82%, F04 3.38%, F05 0.71%, F06 2.82%, the largest at h/a = 0.1). The check "error at h/4 smaller than at h" still fails for all six: the converged 3D answer lies above the Kirchhoff truth by the shear term, so refinement moves away from the truth. That clause cannot hold for F by physics. No stress singularity appeared at the hard-supported plate corners.
3. **Checks 3 and 4, the stress rule against line supports and line loads.** A beam on two supports built with edge supports (the corpus's first version of group D) shows the support line as a stress singularity: max von Mises grows by 1.51 and 1.52 on the last refinement (D01, D02), 1.65 and 1.98 (D05) and 1.55 to 1.61 (D07, D08) while the displacement is converged, so the study's own rule would call six of eight `stress_singular`, contradicting "ends `converged`". A line load across a beam does the same (H04, 1.32 to 1.45). Group D is therefore built as a quarter model on symmetry planes with the whole end face held in z, and the central load is a pressure on a 10 mm strip; the closed forms are unchanged (the strip lowers the midspan deflection by about 0.7%). The finest-level answers then are within 0.4% of the closed-form deflection, the midspan bottom stress is within 3.0% (tolerance 4%), and max von Mises grows by at most 1.01.
4. **Check 3, group E and monotone convergence.** The displacement of the thick-cylinder cases changes sign between the two refinement steps by a few hundredths of a percent. `not_monotone` therefore means a sign reversal whose last difference exceeds a tenth of `target` of the value; a smaller reversal has settled. Differences that keep their sign but do not shrink are not "non-monotone": the extrapolation takes order 1 for them and `error_over_target` decides. The estimate and the order use the actual mesh size (h proportional to the cube root of the volume per element), because Gmsh's size is only an upper bound and does not halve the mesh on a thin section (88, 290 and 1,336 elements on a 12 x 20 mm bar); with the nominal ratio of 2 the observed order of the same data reads 0.5 to 0.9 instead of 1.4 to 2.5.
5. **Check 3, circular sections: the three levels are one mesh.** With the stock `MeshSizeFromCurvature` (12 per full circle) the levels h0, h0/2, h0/4 of every round bar and round beam (A06, A07, B07 to B12) produce the same mesh; the displacement drift between levels is 0.000% and an estimator would read zero error on an answer that is 1 to 2% off. The study must set `MeshSizeFromCurvature = 0` on its mesh object. The oracle catches a zero estimate through `estimate_recomputed` only when the levels differ, so mutant M05 is the guard.
6. **Check 4, group G agreement to 1e-6.** The spellings are computed from the exact unit definitions (`psi` and `lbf` as FreeCAD defines them) and agree to 0.0 over the six sets: serial Gmsh (`ParallelProcessing = False`) gives the same mesh in every spelling. `kip` is not a FreeCAD unit; `mN` takes its place, and `5 kip` is refused as `unit_unknown` (case I11). The three cantilever sets carry loads of 20 to 60 N: the first version's 1 to 3 kN deflected the beam by 30% to 100% of its depth, which the size rule rightly calls `suspect`.
7. **Check 5, reaction sum against applied sum to 1e-3.** The stock `RF` totals miss load that is applied directly on constrained nodes. Measured on the finest levels: A 0 to 1.1% (the round-end-face shortfall of item 1), B 0 to 1.1%, C 0.13 to 0.9%, D 2.7 to 10%, E 2.1 to 6.5%, F 0.8 to 1.2%; 34 of 46 cases miss 1e-3. The cause was verified on C02: the `DLOAD` faces on the pressure face sum to 1200 mm2 exactly, and the load that midside nodes of those faces put on nodes also in the Fixed set is 6.8556 N, which is the shortfall (1333.148 N against 1340.004 N) to 4 digits. The check is attainable only if `reaction_sum` includes the load on constrained nodes; the energy form (twice the strain energy against the external work) needs no such correction and is the simpler route.
8. **Check 4, group H and the 30% rule.** The rule is met only barely and not reproducibly. Last-step max von Mises growth in the final baseline run: H01 2.72 (vertex force, L/h 20), H02 1.312 (notch), H03 1.379 (tab edges), H04 1.32 (line load), H05 1.383 (L bracket), H06 1.338 (step); first-step growth 0.97, 1.27, 1.16, 1.09, 1.25, 1.11. Two runs of the same case with a multi-threaded mesh differed by up to 0.07 (H05 1.31 and 1.38). The asymptote for a 90 degree re-entrant corner is 1.37, so a threshold of 1.30 leaves a margin of 5%. The brief's vertex case needs slenderness to keep the displacement converged: at L/h 10 the tip displacement drifts by +0.69% and then +0.93% (growing, so not converged); at L/h 20 the drift is +0.18% and +0.15%, which is what the corpus uses. H03 (line supports on a thin tab) has displacement drift +0.27% then +1.87%: under the brief's rules it ends `unconverged`, not `stress_singular`, because a line support makes the displacement itself diverge. The stress rule applied to the last refinement step only is the one the oracle uses. In the study run (run 067, final): H01 (growth 2.87), H02 (1.31) and H04 (1.33) end `stress_singular`; H03 (displacement estimated error 2.6%), H05 (1.17%, last-step growth only 0.79) end `unconverged`/`error_over_target`, H06 (growth 1.32, displacement reverses by 1.2e-3) ends `unconverged`/`not_monotone`. None ends `converged`.
9. **Check 4, the "very small face" in group H.** A 0.2 mm loaded boss face is not singular for global refinement: Gmsh sizes the face from its own edges, so the local mesh does not change with h and the stress is mesh-independent (max von Mises 2581, 2825, 2697 for h of 8, 4, 2; no monotone growth). The corpus replaces it by a line load across a beam (growth 1.09 then 1.32).
10. **Check 6, mutants.** The corpus carries fault injection now: I12 is a `ccx` shim on `PATH` that writes valid results and exits 3 (kills M09), I13 a shim that writes nan (M10), I14 runs with `max_elements = 600` (M12). 15 of 16 mutants are caught. M15 (`kappa` taken as 1) survives: the stubby cantilevers B13 and B14 (L/h = 2, shear about 16% of the deflection) do not end `converged` and pass clean (B13 fails its own error-shrinks clause: its error against Timoshenko grows from 0.26% to 2.6% under refinement, because the converged 3D deflection of a clamped short beam moves away from the Timoshenko value), and the other cantilevers have a shear share too small to move the truth past the 3% bar. Later in run 067, B13 and B14 were removed from the graded corpus, because L/h 2 is outside where the Timoshenko formula is a valid truth; M15 stays a named survivor.
11. **Check 7, speed.** Not a constraint on this corpus: the stock pipeline needs 0.4 to 6.7 s per case at the finest level (median 0.8 s; F05 is the slowest with 30,000 elements). The brief's limits (90 s, 90 minutes) leave room for finer `h0` where accuracy needs it; the corpus keeps four to five elements per h/4 across the smallest section dimension.
12. **Implementation findings (run 067, after the study code).** (a) The equilibrium holds to 7.9e-4 or better on 236 of 238 solved levels. The two exceptions are the coarsest levels of E05 and E06 (100 elements, 2.07e-3: the curved loaded face is a handful of quadratic faces whose resultant is rotated against the exact one, which no scalar load correction removes); both end `suspect`/`equilibrium`, on purpose. (b) The stock force constraint delivers 3.4% to 3.5% less than requested on a round face at the corpus meshes (0.9646 to 0.9668; the earlier 1.14% was one mesh); the study scales the nodal loads of the written deck to the request and warns (`load_corrected`) when the factor differs from 1 by more than 1e-3, or `load_mismatch` when it is outside 0.5 to 2 and left alone. Flat faces need no correction. (c) `RF` in a ccx `.dat` is K u at the node: at a constrained node it holds the node's external load, at a free direction of a constrained node it equals that load. The reaction is therefore the `RF` total of the union of all constrained nodes minus the whole external load on those nodes, all three directions; counting only constrained directions leaves a shortfall of up to 3% on F01. (d) Gmsh's size is an upper bound; the three levels have element counts 88, 290 and 1,336 on a thin bar, so the order is computed against h ~ N^(-1/3). (e) Round sections need `h0` no larger than d/2: at d to 4/3 d Gmsh finds no volume or a coarse level 25 times off in displacement. (f) Second-order tetrahedra on curved boundaries come out with a non-positive Jacobian from plain Gmsh in some cases (ccx exit 201 at E03 and E04 finest levels); the mesh object is set to `HighOrderOptimize = Optimization`, with `Elastic+Optimization` and `None` as retries when Gmsh leaves no mesh (B07, B08 finest level), reported as `mesh_retry`. (g) The empty result object seen in 2 of 3 runs of D04 in run 067 did not recur in 238 solved levels with serial Gmsh and the study's own result handling; the study checks `DisplacementLengths` anyway. Later in run 067, `h0` of E03 to E06 became 10 mm, which gives 148 and 365 elements at the coarsest level and equilibrium of 7e-5 or better; all four end `converged`. The mechanism of the coarse-level miss is not proved.
