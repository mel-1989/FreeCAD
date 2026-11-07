---
tags: [type/research, for/agent, status/live]
---

# G11 baseline: what the stock FEM pipeline answers on the corpus (run 067)

The number the study has to beat on honesty rather than accuracy. Stock pipeline: `ObjectsFem` constraints and material, `Fem::FemMeshGmsh` through `gmshtools` (second order, one global `CharacteristicLengthMax`), `ccxtools.FemToolsCcx`, C3D10, CalculiX 2.23, Gmsh 4.15.2, no verdicts and no checks. One mesh at the corpus's finest h (`h0/4`); group H at three levels. Driver: `femtest/app/study_corpus_run.py --mode baseline`; oracle tolerances from the brief; raw JSON lines are under the run's scratch folder, not tracked. Every case, whatever its group, completed in 0.4 to 6.7 s, so none came near the 60 s limit and no `h0` was changed on that account.

| Group | Cases answered within tolerance | Elements at finest h | Seconds per case (median, max) | What the stock pipeline does |
|---|---|---|---|---|
| A axial bars | 6 of 8 | 775 to 2,828 | 0.6, 0.8 | exact for the six rectangular bars (displacement within 3e-6, stress 1.00e-5); the two round bars are 1.0% off, cause mesh geometry and a force constraint that delivers 1.14% less than requested |
| B cantilevers | 12 of 12 | 1,213 to 8,763 | 0.8, 1.7 | displacement 0.02% to 1.8% from Timoshenko, mid-length bending stress within 0.9%; round sections need a datum line for the force direction (the stock constraint wants an edge or face) |
| C uniform pressure | 6 of 6 | 1,402 to 8,666 | 1.0, 1.8 | displacement within 2.4%, stress within 0.6%; reaction totals short by up to 0.9% |
| D two supports | 8 of 8 | 1,174 to 2,503 | 0.8, 0.9 | displacement within 0.4%, stress within 3.0% (quarter model on symmetry planes, see SPEC Checks that may not hold, item 3) |
| E thick cylinders | 6 of 6 | 1,338 to 4,757 | 0.8, 1.5 | hoop stress within 0.35%; displacement sequence changes sign between levels (item 4) |
| F plates | 2 of 6 | 3,950 to 30,676 | 2.7, 6.7 | converged 3D answer 2.7% to 13.8% above Kirchhoff; fails the 5% bar on four of six for physical reasons (item 2) |
| G unit sets | 0 of 6 agree to 1e-6 | 486 to 1,896 | 1.2, 1.8 | the four spellings give different meshes (Gmsh multi-threaded): displacement spread 7.6e-5 to 2.1e-4; with `ParallelProcessing = False` all six agree to 0.0; `kip` is not parseable |
| H singularities | 0 of 6 (no verdicts exist) | 6,818 to 12,909 | 2.6, 3.0 | solves everything and reports a number; max von Mises grows 1.3 to 2.7 per refinement on the last step with no flag, and nothing says that H03 has not converged |
| I refusals | 3 of 10 refused loudly, 7 not | up to 12,066 | 0.4, 2.3 | I01 no support: `check_prerequisites` message. I04 `210000` and I05 `210 N`: a `ValueError` unit mismatch from the writer (loud, not named). I07 zero volume: mesher yields no elements. I09 no ccx: `FileNotFoundError`. I08 lost face: `ValueError: Invalid shape name ?Face7`. I06 `nu = 0.5`: ccx exit code 201, no results. I02 no load: solves to displacement 0.0. I03 rotation free: solves and returns a displacement of 4e11 mm with exit code 0. I10 displacement 59% of thickness: solves, no warning |
| J reference survival | 2 of 4 solve, both right | 4,251 to 4,791 | 1.1, 1.5 | J03, J04 (length changes): the load follows the face and the answer is right (within 0.9% and 4e-7). J01 (fillet inserted) and J02 (pocket removing the loaded face): the constraint goes `Invalid`, its reference reads `?Face5` or `?Face6`, and the mesh step raises `ValueError`; nothing solves with a moved load |

Counted over groups A to F (46 cases with a numeric truth and a stated tolerance) the stock pipeline answers 40 within tolerance at the finest mesh, 87%. The 6 misses are two round bars and four plates, and none is a solver fault. The stock pipeline is silent about which of the 40 can be trusted. On the 26 cases of groups G to J, which test honesty, it never produces a named refusal and never meets the 1e-6 unit agreement; its failures are an unnamed exception or message in 9 cases and a plausible-looking or absurd number with no flag in the rest (I02, I03, I10, H, G).

Three-level behaviour of the stock pipeline on A to F (3 meshes, last refinement step): displacement changes by at most 1.0% (C), 0.7% (B), 0.6% (F), 0.2% (E), 0.01% (D), 0.0% (A); max von Mises changes by at most 7% (C). The six round beams and bars (A06, A07, B07 to B12) have one mesh at all three levels.

CalculiX energy request, confirmed on ccx 2.23: `*EL PRINT, ELSET=Eall, TOTALS=ONLY` followed by `ELSE` before `*END STEP` writes `total internal energy for set EALL` to the `.dat` (a 100 x 10 x 10 steel cantilever with 100 N at the tip: 9.525 N mm, against 9.552 for half the load times the largest tip displacement).
