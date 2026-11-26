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

Real-dataset findings worth flagging (current code: the error of a result is
sampled at 7 points per face, and coincident vertices are welded before
decimation): without refinement 8/19 meshes exceed tau=1% (median max error
0.80% of the diagonal, worst 3.55%), with refinement 6/19 (median 0.73%, worst
2.16%). Refinement lowers the error on 5 meshes (barrel, elephant, drill,
T. rex tooth, netsuke), leaves it unchanged on 14, raises it on none; on the
barrel, elephant and drill it protects 495-1,168 vertices and the achieved
reduction falls to 0.7%, -1.2% and 8.1%. The weld ablation
(`experiment_weld_ablation.py`) shows that most of the error without the weld
comes from decimating meshes whose seams are stored as duplicated vertices.
On the real meshes the result-to-source direction is the larger on 13/19; on 4
meshes source-to-result alone would stay below tau while result-to-source
exceeds it, and on 1 (vegetable garden) the reverse.

- **`experiment_d_threshold_sensitivity.py`** — Experiment D (error-
  threshold sensitivity). Sweeps `error_threshold_pct` (0.25/0.5/1.0/2.0/
  5.0%) at a fixed 90% requested reduction, on the 5 meshes where refinement
  acted in Experiment B (`dataset_catalog.REPRESENTATIVE_MODELS`: elephant,
  bolt cutters, drill, T. rex tooth, rat netsuke; chosen after Experiment B,
  not a random sample). Run:
  `python -u -m evaluation.experiment_d_threshold_sensitivity`.
- **`experiment_e_reduction_sensitivity.py`** — Experiment E (requested-
  reduction sensitivity). Same 5 models, sweeps the requested reduction
  (50/70/80/90/95%) at a fixed 1% threshold, reporting whole-mesh vs.
  unprotected-zone-only reduction achieved. Run: `python -u -m
  evaluation.experiment_e_reduction_sensitivity`.
- **`experiment_weld_ablation.py`** — Experiment B (QEM, 90%, empty mask,
  tau 1%, 3 iterations) with the weld of coincident vertices switched off
  (`remesh_core._weld_coincident` replaced by the identity); the weld-on rows
  are Experiment B. Without the weld: 12/19 meshes above tau (8 with it),
  median max error 1.96% (0.80%), barrel 1,469% (worst with weld 3.55%), the
  loop runs >1 iteration on 12 meshes (8), median reduction of those 66%
  (89.9%). Run: `python -u -m evaluation.experiment_weld_ablation`.
- **`experiment_error_bounded.py`** — Experiment B's configuration through
  `run_decimation_pipeline` with `lower_reduction_if_needed` on. All 19 meshes
  end within the 1% threshold (max 0.98%): 13 keep 90%; barrel 63.9%, carrot
  cake 78.8%, elephant 78.1%, drill 76.3%, bolt cutters 87.0%, vegetable garden
  85.6% (refinement alone: 6 meshes above the threshold, and 0.7%, -1.2%, 8.1%
  reduction on three). MeshLab/fast-simplification on welded input at the same
  reduction are comparable (0.87-1.71% / 0.79-2.21%). Run:
  `python -u -m evaluation.experiment_error_bounded`. With arguments (`pro`/`qem` and a
  request) it writes `error_bounded_<engine>_<request>.json`: all 19 meshes stay within 1%
  with QEM at 95% (max 0.99%; 8 lowered, the rat netsuke down to 28.1% because the error
  is not monotonic) and with DecimatePro at 90% and 95% (max 1.00%; 1-2 lowered, median
  reduction 68%: the engine stops before the threshold does).
- **`experiment_texture_fidelity.py`** — appearance error of textured results
  (13 dataset meshes with UV and a base-colour image; 20,000 area-weighted
  points; colour difference as % of the RGB range, with the source image on the
  result's transferred UV). Median mean error: 1.03% with seams free (90%
  reduction), 0.35% with seams protected (76% reduction), 0.59% under the
  automatic policy (cut at seams when <15% seam vertices, 87%), 1.06% for MeshLab on the welded input with the same
  transfer (90%). The geometry-only floor is 0.05%, so almost all of it comes
  from the UV transfer. With the faces cut at the seams (`cut_seams=True`): 0.59% at 86.6% (0.35% at 88.7% on the 7 meshes with <15% seam vertices, vs 0.33% at 83.0% protected; on the 6 fragmented atlases no gain, 66.8% reduction vs 78.5%). Other maps (policy config, medians over the meshes that have them): normal 5.0 deg mean (95th pct 24 deg, floor 0.9) on 7 meshes, roughness 2.0%, metallic 0.8%, occlusion 0.8% (1 mesh), emissive 0.3% (2 meshes). A new atlas (`reparametrize` + bake, 2048 px) gives
  0.40% (95th pct 1.39%, 0.2% of the points above 10%) at 89.9%: the lowest error at that
  reduction, better than the carried UV on 10 of 13 meshes and worse on the drill
  (3.9% vs 3.3%). On an AI-generated Meshy model (460,224 tris, 2048 px texture, 622
  charts) the carried UV give 0.34% (0.22% cut at the seams, 85.1%) and the new atlas
  0.34% at 90.0%; freezing its seams reduces only 57.7% with 1.73% geometry error.
  (An earlier 10.1% for the new atlas came from baking colour with rays along a 3%
  cage, which hit other parts of the body; colour maps now use the nearest point,
  and `map_errors` takes the source maps separately.) Splitting the faces
  at their centroid (tried, not kept) recovered little (e.g. elephant 1.73 ->
  1.23% at 8x the triangles). Run: `python -u -m evaluation.experiment_texture_fidelity`.
- **`experiment_normal_shading.py`** — angle between the world-space shading
  normal of the source and of the result (7 meshes with a normal map, default
  pipeline, 20,000 points). Median mean: 21.1 deg with no normal map, 13.5 with
  the source map carried over, 6.4 with a map baked at the source map's size (up
  to 2048 px) with rays from a 3% cage, 5.7 with the application's choice
  (`best_normal_map`: best of rays 3%, rays 1% and nearest point, or the carried map;
  nearest point on 3 meshes, rays 1% on 2, rays 3% on 2; 95th pct 29.3 deg, worst mesh
  the bolt cutters at 17.9). Run: `python -u -m evaluation.experiment_normal_shading`.
- **`experiment_error_sampling.py`** — the unrefined 90% result measured again
  with a lattice of 105 points per result face and 28 per source face (meshes of up
  to 40,000 faces) as an independent reference for the sampled maximum. Result
  side: the application's 7 points capture a median 98.5% of the reference
  (78.7-107.6%; drill worst), the centroid alone 68.7% (min 49.2%). Source side
  (centroids): 85.4-96.9%. Both sides, 9 meshes: 89-101% (median 95.7%); no mesh is
  below the 1% threshold in the application and above it in the reference. Run:
  `python -u -m evaluation.experiment_error_sampling`.
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
  survive in the result, not just assumed) — and after `transfer_texture_coords` (as the app does) the result has exactly
  the expected number of seam vertices on every model (12, 20, 32, 32, 40). Run: `python -u -m evaluation.experiment_uv_seam_detection`.
- **`experiment_runtime.py`** — wall-clock cost of the default pipeline,
  all 19 models. Only `T_Decimation` (the single initial
  `decimate_with_mask` call) is cleanly separable from outside
  remesh_core.py without instrumenting it; `T_Error`/`T_Repair` are left
  blank rather than a fabricated split (see the script's docstring for
  why), so the sheet's `T_Other = T_Total - SUM(...)` formula reports the
  whole refinement-loop time (error measurement + any repeat decimations
  + repair), not pure overhead — don't read it as the latter in the
  paper. Total pipeline time ranged from 32 ms (Barrel, 1473 tris) to
  36.8 s (electrocardiograph, 1.87M tris, and even then only because of
  the sheer mesh size — 1 refinement iteration, no protection added),
  median 0.98 s.
  Run: `python -u -m evaluation.experiment_runtime`.
- **`experiment_baselines.py`** — the same 19 models simplified by
  PyMeshLab quadric edge collapse (default and with `preserveboundary`) and
  by fast-simplification, scored with the same bidirectional error, at a
  fixed 90% request and at the reduction Experiment B's refined run
  reached (read from `results/experiment_b.json`, so run Experiment B
  first). Each baseline also runs on a welded copy of the
  input (the step Adaptive Remesher applies). At the same 90% request the
  median max error is 0.80% of the diagonal for Adaptive Remesher
  unrefined, 2.29% for MeshLab and 1.69% for fast-simplification as loaded,
  and 0.88% / 1.01% once welded; at the reduction refinement reached,
  MeshLab (welded) deviates far less than the refined result on the three
  meshes below 80% reduction (0.02% vs 1.58%, 0.04% vs 1.05%, 0.03% vs
  2.16%). MeshLab with `preserveboundary` reduced nothing on four meshes
  and produced a degenerate result on the bolt cutters, so treat it as a
  note, not a baseline. Needs `pip install pymeshlab fast-simplification`.
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
