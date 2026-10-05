# Evaluation harness

Supporting code for the paper's Evaluation section. Independent of the
shipped app (`app.py`/`ui/`): not imported by it, not bundled by the
PyInstaller spec.

## What's implemented

- **`synthetic_cases.py`** — the 4 synthetic cases from the experimental
  design (removed region, partially-removed connected detail, small
  detail on a continuous surface, multiple regions of different
  importance). All built on closed surfaces (spheres) rather than open
  planes: `decimate_with_mask` unconditionally runs a boundary-gap
  repair step whenever a mask is set, which can't tell a legitimate open
  mesh boundary from a seam-repair gap and would try to cap it — verified
  empirically that all 4 cases have zero boundary edges before and after
  a masked decimation, so this doesn't come up.
- **`experiment_a_bidirectional.py`** — Experiment A (bidirectional error
  detection). Cases 1-2 use a hand-built "already decimated" result
  (clean ground truth, no decimation engine involved); cases 3-4 run the
  real *unprotected* decimation pipeline at 90% reduction, to see whether
  the engine itself loses the detail. Run: `python -m
  evaluation.experiment_a_bidirectional`.
- **`experiment_c_masked.py`** — Experiment C (masked simplification).
  For each case, compares an unmasked (whole-mesh) decimation against a
  masked one (`important_mask` protected), at the same 90% target, and
  reports source→result error split into the important region vs. the
  rest, plus both the whole-mesh and unprotected-zone-only reduction
  percentages. Run: `python -m evaluation.experiment_c_masked`.
- **`dataset_catalog.py`** — single source of truth for the real-model
  dataset: 19 models across all 6 categories (Characters, Objects,
  Mechanical, Organic, Scanned, Generated), each with its category,
  filename, display name, source URL, and verified license. The actual
  model files live locally under `MODELS_ROOT`
  (`<repo>/models` by default, or the folder named by the
  `ADAPTIVE_REMESHER_MODELS` environment variable, organized as
  `<category>/<file>`), not in the repo (third-party/
  licensed, some multi-hundred-MB) — `gather_dataset_stats.py` and
  `experiment_b_refinement.py` both import this module so the model list
  can't drift between them.
- **`gather_dataset_stats.py`** — reads every model in the catalog with
  the app's own loading pattern (`load_and_prepare_mesh`) and computes
  triangles/vertices/connected components/UV/normals/bbox diagonal.
  Connected components are counted with `vtkPolyDataConnectivityFilter`
  directly rather than pyvista's `split_bodies()`: several of these
  models (duplicated-vertex OBJ exports - every face has 3 unique,
  non-shared vertices) have as many "components" as triangles, and
  `split_bodies()` never finished on those (20+ minutes, still running)
  because it extracts every region into its own PolyData; the direct
  filter (region count only) takes under a second. Run:
  `python -m evaluation.gather_dataset_stats`.
- **`experiment_b_refinement.py`** — Experiment B (refinement ablation),
  run on the real-model dataset. For each of the 19 models, both configs
  start from the exact same initial `decimate_with_mask` pass (empty
  mask, no manual/UV protection - isolates just the refinement
  mechanism): "No refinement" stops there
  (`auto_error_protection=False`); "Bidirectional refinement" then runs
  `refine_by_error` with the app's own defaults (`error_threshold_pct=1.0`,
  `max_iterations=3`). Reports bidirectional error (fwd/rev, max/mean/p95),
  iterations used, vertices auto-protected, and runtime per config. Run:
  `python -u -m evaluation.experiment_b_refinement`.

All four write raw metrics to `results/{experiment_a,experiment_b,
experiment_c,dataset}.{csv,json}` where applicable (already run once;
current numbers are checked in, and also copied into
`evaluation_tracker.xlsx`'s `Dataset`/`Exp_B_Refinement_Ablation` sheets).
Re-run any module to regenerate after a code change.

Real-dataset finding worth flagging: with the original interface repair,
refinement raised the maximum error on four meshes (trilobite, blastoid,
lion, heart) because the capping step closed openings that belong to the
source mesh. The repair now leaves those openings alone and
`refine_by_error` keeps the best pass, so refinement raises the error on no
mesh; on the trilobite, blastoid and lion it returns the first result
unchanged (no pass improved on it). Refinement is still a trade of
reduction for fidelity, not a strict improvement.
Aggregate result (current code): `error_max_pooled > threshold` (tau=1%) in
12/19 models without refinement, vs. 10/19 with it.

- **`experiment_d_threshold_sensitivity.py`** — Experiment D (error-
  threshold sensitivity). Sweeps `error_threshold_pct` (0.25/0.5/1.0/2.0/
  5.0%) at a fixed 90% requested reduction, on 5 representative models
  (`dataset_catalog.REPRESENTATIVE_MODELS` — a clean baseline, a small
  model needing many refinement passes, a large organic scan, the "Loewe"
  anomaly above, and the largest mesh in the dataset). Run:
  `python -u -m evaluation.experiment_d_threshold_sensitivity`.
- **`experiment_e_reduction_sensitivity.py`** — Experiment E (requested-
  reduction sensitivity). Same 5 models, sweeps the requested reduction
  (50/70/80/90/95%) at a fixed 1% threshold, reporting whole-mesh vs.
  unprotected-zone-only reduction achieved. Run: `python -u -m
  evaluation.experiment_e_reduction_sensitivity`.
- **`experiment_engine_comparison.py`** — vtkDecimatePro vs. QEM, same
  settings, all 19 models, no protection (isolates the engine choice).
  **Finding, now fixed** (`remesh_core.py`'s `_weld_if_fully_disconnected`,
  see `TECHNICAL_DOCUMENTATION.md` section 5.6): on the 4 models whose
  export duplicates vertices per face (no two triangles share a vertex —
  see `gather_dataset_stats.py`'s `NON_WELDED_NOTE`: shark, vegetable
  garden, electrocardiograph, heart), **DecimatePro used to achieve
  exactly 0% reduction** while QEM reached the full 90% target on the
  same mesh - DecimatePro collapses edges via triangle adjacency, so a
  mesh with no shared edges gave it nothing to collapse; QEM clusters by
  spatial proximity instead and wasn't affected. Since
  `DecimationSettings`'s own default engine is DecimatePro, this used to
  be a silent **no-op decimation** for anyone who fed the app one of
  these malformed exports without running cleanup first; the current
  checked-in results (re-run after the fix) show all 4 reducing normally
  (31-90%, engine/mesh-dependent). Still true and unrelated to that bug:
  DecimatePro sometimes falls short of the requested reduction even on
  normal meshes (`preserve_topology` blocking further collapses; e.g.
  76.5% instead of 90% on one Characters model), which QEM doesn't do.
  Run: `python -u -m evaluation.experiment_engine_comparison`.
- **`experiment_validity_checks.py`** — post-simplification mesh
  integrity (duplicate vertices, boundary/non-manifold edges, degenerate
  faces, connected components, watertightness), Before (raw load) vs.
  After (default pipeline) for all 19 models. **Finding, now fixed**
  (`remesh_core.py`'s `_repair_non_manifold_edges`/
  `_repair_non_manifold_edges_if_any`, see `TECHNICAL_DOCUMENTATION.md`
  section 5.6): several models that start with 0 non-manifold edges used
  to end up with a handful after the default pipeline (e.g. Stanford
  Armadillo: 0 → 7; Bolt Cutters: 0 → 12), from two distinct causes -
  `vtkDecimatePro` on its own (no masking involved at all) and
  `merge_points=True` on the masked path - both now repaired
  automatically; the current checked-in results (re-run after the fix)
  show 0 non-manifold edges on all 19 models. This also used to confirm
  the DecimatePro/non-welded-mesh bug above from a different angle
  (electrocardiograph's "After" used to be byte-identical to "Before"
  under the default engine - now genuinely reduced) and surfaced a
  related interaction worth keeping in mind even though it's no longer
  the headline finding: on the "Realistic Heart" model, UV-seam
  auto-protection flags ~16% of points as seams (near-universal on a
  non-welded export) and triggers `decimate_with_mask`'s split/merge path
  - before the DecimatePro fix, the unprotected zone still failed to
  reduce there while the merge's point-welding alone dropped vertex count
  94200 → 15662 with zero actual triangle reduction, which could have
  looked like progress in a UI but wasn't; now that DecimatePro itself
  reduces that zone too, this no longer happens. Run: `python -u -m
  evaluation.experiment_validity_checks`.
- **`uv_seam_test_meshes.py`** / **`experiment_uv_seam_detection.py`** —
  UV seam detection and geometric protection. Two parts: (1) a point-level
  confusion matrix for `uv_seam_point_mask` against hand-built ground
  truth that also probes its numeric tolerance boundary (100%
  precision/recall, including right at the threshold); (2) 6 synthetic
  "texture atlas" models (independently-UV-unwrapped islands placed side
  by side, sharing 3D position at the seam but not welded — the standard
  real-world seam pattern, with a known seam-vertex count by
  construction) x 2 configs (protection off/on). Detected seam count
  matched the known count exactly in every model. With protection off,
  seams sometimes survived a 70% decimation and sometimes didn't
  (model-dependent); with protection on, **zero seam vertices were ever
  geometrically lost** (verified by checking their exact coordinates
  survive in the result, not just assumed) — but UV *data* itself never
  survived the protected path either, on any model, confirming the
  existing caveat (`_weld_seam_gaps` rebuilds the mesh from bare arrays
  with no point_data) with real measured numbers instead of just a prose
  note. Run: `python -u -m evaluation.experiment_uv_seam_detection`.
- **`experiment_runtime.py`** — wall-clock cost of the default pipeline,
  all 19 models. Only `T_Decimation` (the single initial
  `decimate_with_mask` call) is cleanly separable from outside
  remesh_core.py without instrumenting it; `T_Error`/`T_Repair` are left
  blank rather than a fabricated split (see the script's docstring for
  why), so the sheet's `T_Other = T_Total - SUM(...)` formula reports the
  whole refinement-loop time (error measurement + any repeat decimations
  + repair), not pure overhead — don't read it as the latter in the
  paper. Total pipeline time ranged from 20 ms (Barrel, 1473 tris) to
  32.7 s (electrocardiograph, 1.87M tris, and even then only because of
  the sheer mesh size — 1 refinement iteration, no protection added),
  median 0.52 s (after the interface-repair fix).
  Run: `python -u -m evaluation.experiment_runtime`.
- **`experiment_baselines.py`** — the same 19 models simplified by
  PyMeshLab quadric edge collapse (default and with `preserveboundary`) and
  by fast-simplification, scored with the same bidirectional error, at a
  fixed 90% request and at the reduction Experiment B's refined run
  reached (read from `results/experiment_b.json`, so run Experiment B
  first). At the same 90% request the median max error is 1.96% of the
  diagonal for Adaptive Remesher unrefined, 1.94% for MeshLab and 1.69%
  for fast-simplification; at the reduction refinement reached, MeshLab
  deviates less than the refined result on all eight meshes below 80%
  reduction. MeshLab with `preserveboundary` reduced nothing on four
  meshes and produced a degenerate result on the bolt cutters, so treat it
  as a note, not a baseline. Needs `pip install pymeshlab fast-simplification`.
  Run: `python -u -m evaluation.experiment_baselines`.

All experiments write raw metrics to `results/*.{csv,json}` (already run
once; current numbers are checked in, and also copied into
`evaluation_tracker.xlsx`'s per-experiment sheets, recalculated via Excel
COM automation — LibreOffice's `recalc.py` doesn't work on this Windows
environment, see the session history). Re-run any module to regenerate
after a code change; sheets with more rows than their original template
(Engine_Comparison, Validity_Checks: 38 rows vs. 25) had their
formula/styling pattern extended by hand into the new rows rather than
truncating the real dataset to fit.

## What's NOT implemented yet

Nothing from the original plan remains unstarted. Both app-level bugs
found by the Engine_Comparison/Validity_Checks experiments (DecimatePro's
silent no-op on non-welded meshes, and non-manifold edges from two
distinct causes) are fixed in `remesh_core.py` - see
`TECHNICAL_DOCUMENTATION.md` section 5.6 and `CHANGELOG.md`. Possible
next step, not requested yet: expanding the real-model dataset beyond 19
(the categories with only 2-3 models - Objects, Mechanical - are the
thinnest).
