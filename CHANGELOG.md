# Changelog

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Changed (dense error sampling) / Added (error of every map)
- The faces of the result are sampled at 7 points each (`DENSE_SAMPLES`: centroid,
  edge midpoints, points near the corners; the face takes the worst) in
  `measure_error`/`measure_bidirectional_error`; the source faces keep their
  centroid. It raises the error of 7 of 19 meshes by 18-37% (the vegetable garden
  goes from 0.93% to 1.20%), so all experiments were re-run: 8/19 meshes above 1%
  at the first pass, 6/19 with refinement, all 19 within 1% (max 0.98%) with the
  lowered reduction (6 meshes at 63.9-87.0%).
- `materials.map_errors`: appearance error of every map (colour, emissive,
  normal as an angle, roughness, metallic, occlusion) with a geometry floor.
  Median normal error 5.0 deg (floor 0.9), roughness 2.0%, metallic 0.8%.

### Changed (UV seam policy, finer reduction search)
- Automatic seam policy: seams are no longer frozen by default. With <15% seam
  vertices the faces are cut along the seams after decimating (`should_cut_uv_seams`,
  replaces `should_protect_uv_seams`): the colour error of protected seams (0.35% vs
  0.33% median) at 88.7% reduction instead of 83.0%; on fragmented atlases neither
  is used. Both checkboxes override it.
- `lower_reduction_to_meet_error` bisects 3 times between the first valid step and
  the failing one above it. The five lowered meshes now keep 63.9% (barrel),
  78.8% (carrot cake), 78.1% (elephant), 87.0% (bolt cutters), 77.5% (drill) instead
  of 60.1%, 75.0%, 75.0%, 85.1%, 75.0%; all 19 stay within 1% (max 0.97%).

### Added (cut faces at UV seams)
- `split_faces_at_seams` and `transfer_texture_coords(..., cut_seams=True)`; checkbox
  "Cut faces at UV seams" (off by default). Each vertex takes the chart of its
  nearest source triangle, each edge between charts gets a vertex where the chart
  changes (bisection, ignored within 8% of an end) and the faces on cut edges are
  re-triangulated conformingly, so no face is stretched across a seam. On the 7
  textured meshes with <15% seam vertices: 0.35% texture error at 88.7% reduction
  vs 0.33% at 83.0% with seams protected; on fragmented atlases it only adds
  triangles.

### Added (texture fidelity)
- `materials.texture_error`: colours points drawn on the source with its image at
  the source UV and at the UV of the closest point of the result, and reports the
  difference (% of the RGB range) plus a floor from the geometry alone.
  `experiment_texture_fidelity.py` runs it on the 13 textured dataset meshes.

### Added (error-bounded reduction)
- `ProtectionSettings.lower_reduction_if_needed` (default on, checkbox "Lower the
  reduction if the limit cannot be met") and `lower_reduction_to_meet_error`:
  when protecting violating zones cannot bring the error under the threshold,
  or reaches it by keeping most of the mesh, the pipeline also tries requested
  reductions lowered in steps of 5 points and keeps the result with the higher
  reduction that meets the threshold. On the 19 evaluation meshes (QEM, 90%,
  1%) all now meet the threshold: 14 keep 90%, the others get 60-85%, where
  refinement alone left 5 above the threshold at -0.3% to 90%.
  `reduction_lowered_to_pct` is in `PipelineResult` and the JSON report.
- Evaluation: `experiment_error_bounded.py`, `experiment_weld_ablation.py`.

### Added (textures and viewer)
- The decimated result keeps its texture coordinates. `transfer_texture_coords`
  (`remesh_core.py`) copies the source's UV onto a mesh rebuilt from bare
  geometry: each face takes the nearest source triangle of its UV chart,
  corners are interpolated, and vertices are duplicated per chart to recreate
  the seams. `apply_decimation` and batch processing call it on the result,
  and `clean_mesh` calls it so cleaning no longer drops the UV. The evaluation
  scripts call the core functions directly and are unaffected.
- "Show texture" option: the viewer (and the comparison window) paints the
  model with the base-colour image of a glTF/GLB or an OBJ's `map_Kd`
  (`load_base_color_texture`).
- Exporting (and batch processing) a mesh that has UV also writes its texture
  next to it as `name_texture.png`; an OBJ gets a `name.mtl` plus the
  `mtllib`/`usemtl` lines so it opens textured (`save_texture_with_mesh`), a
  PLY gets a `comment TextureFile` header line.
- Material maps (`materials.py`): a glTF/GLB's base colour, normal,
  metallic-roughness, occlusion and emissive maps are loaded with their
  factors. Since the result keeps the original's UV, every map stays valid on
  it, and "Export mesh" can now write GLB, which keeps all of them (OBJ/PLY
  keep the colour texture and the normal map, the latter as `map_Bump`).
- "Show material maps (PBR)" option: shades the model with its normal,
  roughness, metallic, occlusion and emissive maps (VTK PBR; tangents are
  computed because a normal map has no effect without them, and roughness,
  metallic and occlusion are packed into the ORM image VTK expects).
- "Bake normal map" button (512 to 4096 px): bakes a tangent-space normal map
  for the decimated result from the original and the original's own normal map,
  so the shading of the removed detail comes back without triangles. Each
  texel casts a ray along the decimated surface's smooth normal, from a cage
  3% of the bounding-box diagonal above it, and takes the first (outermost)
  triangle of the original it meets; a texel whose ray misses keeps the
  decimated surface's own normal. `method="nearest"` (closest point) remains
  available in `bake_normal_map`. On the carrot cake both give the same
  fine-scale variation (a neighbouring-texel angle of 17.5 degrees against 12.0
  in the original's own map): the coloured cells over the crumbs are the
  faceted crumb geometry baked faithfully, not an artefact of the matching.
  A new decimation discards the bake.
- "Re-bake all textures (new atlas)" button (optional `xatlas` package): gives
  the decimated result its own texture layout, so no face is stretched or
  overlaps, and bakes every map from the original onto it (colour,
  metallic-roughness, occlusion and emissive are resampled, a new normal map is
  baked), at the chosen size. On the carrot cake the colour error against the
  original is about the same as keeping the original atlas (8.5 against 8.2,
  mean absolute difference over four views): the gain is a clean layout and a
  smaller atlas, not a better picture.
- GLB export stores the colour maps as JPEG (quality 90) and keeps the data
  maps (normal, metallic-roughness, occlusion) as lossless PNG. The carrot
  cake went from 49 MB to about 12 MB after re-baking at 2048 px.
- Conservative rasterization in `bake_maps`: texels just outside a triangle take
  its own extrapolated value, so a GPU that filters across a small triangle's
  edge does not read a neighbour's colour.
- "Show mesh lines" option to hide the triangle edges.
- After decimating, a hint appears in the stats when UV-seam protection froze
  15% or more of the vertices and the reduction fell more than 15 points
  short of the request (`seam_protection_limits_reduction`): textures from
  generators are often split into hundreds of tiny islands, and freezing
  their seams can leave a 90% request at under 30%. It suggests turning off
  "Protect UV seams automatically", which works because the texture is
  restored on the result.
- Options that cannot be used in the current state (no mesh loaded, no UV, the
  other engine's controls, dependent checkboxes, restore/compare without a
  result) are disabled and their tooltip says why.

### Changed (geometry)
- `decimate_with_mask` welds points that sit at the same position before
  decimating (`_weld_coincident`). Exports that store every face, or every
  texture seam, with private copies of its vertices made QEM collapse across
  what is one surface: errors of 5% to 612% of the bounding-box diagonal at a
  90% reduction on the open-boundary evaluation meshes, 0.6% to 3.6% once
  welded (a sphere with private vertex copies: 32.4% against 0.10%). With a mask
  the welded points keep their UV, because on the fragmented unprotected zone
  that term is what keeps QEM from flinging vertices (64% to 328% without it);
  on a whole mesh it is left out (it raised the error to 2.5% to 16%).
- "Protect UV seams" now follows the loaded mesh until you set it yourself
  (`should_protect_uv_seams`): it is turned off when the seams cover 15% or
  more of the vertices, and batch processing decides per file. At equal
  reduction on eight textured meshes, leaving the seams free gave a lower
  geometric error on all of them (e.g. 15.5% against 0.34% on the elephant, 35.8%
  against 0.16% on the drill) and a colour error that was lower on four, equal on
  two and higher on two sculptures whose seams cover about 13% of the vertices;
  freezing them also capped the reduction at 22% to 81%. Ticking or unticking
  the option, or loading a preset, disables the automatic rule.

### Fixed (texture orientation)
- Textures were drawn and exported upside down. `pv.Texture(array)` already
  expects the top row first (it matches `pv.read_texture` of the same file),
  and the code flipped the array again. The viewer, the PNG written next to an
  exported OBJ/PLY, and the colour used when exporting GLB all had the flip.
  `map_to_texture`, the texture loader and the exporters no longer flip, and a
  test compares the result with `pv.read_texture` so it cannot come back.
  Re-exporting a model from an earlier build is needed to get a right-side-up
  PNG.

### Fixed (warnings)
- `vtkQuadricDecimation` with the UV/normals attribute metric printed "Unable
  to factor linear system" once per singular collapse; the warnings are muted
  during that call (the result is valid and unchanged).

### Added (evaluation)
- `evaluation/experiment_baselines.py`: scores PyMeshLab's quadric edge
  collapse (default and boundary-preserving) and fast-simplification with
  the same bidirectional error on the 19-model dataset, at a fixed 90%
  reduction and at the reduction the refined run reached
  (`results/baselines.{csv,json}`, `Baselines` sheet of the tracker).
  meshoptimizer is not included (no wheel for Python 3.14).

### Added (report/diagnostics)
- Per-direction geometric error, alongside the existing pooled max/mean/p95:
  `ErrorStats`/`bidirectional_error_summary` (`remesh_core.py`) now also
  compute `_fwd` (result→original) and `_rev` (original→result) separately,
  since a high value in one direction means the decimation deviated from
  the original surface while a high value in the other means a whole
  feature of the original disappeared in the result — two different
  failure modes the pooled number alone couldn't distinguish. Surfaced in
  the on-screen stats after "Apply decimation" and in the exported/batch
  `.json` reports (`error_max_fwd`/`_rev`, etc.).
- Reduction achieved within the unprotected zone specifically
  (`PipelineResult.unprotected_zone_reduction_pct`), alongside the existing
  whole-mesh `reduction_real_pct`: the target reduction percentage only
  ever applies to the unprotected zone (the protected part is never fed to
  the decimator), so with a large protected region the whole-mesh ratio
  can look much lower than the target even though the unprotected zone hit
  it exactly. `decimate_with_mask` gained an optional `zone_stats`
  out-parameter to recover the before/after triangle counts of that zone
  without duplicating its split logic elsewhere.

### Fixed
- Interface repair no longer closes openings that belong to the source mesh.
  `_weld_seam_gaps` (PyMeshLab `close_holes`) and
  `_stitch_residual_boundary_gaps` used to treat every boundary loop left
  after merging as a seam gap, so a source with open boundaries got fan
  triangles that lay away from the surface (the evaluation traced a maximum
  error increase of up to 11.8% of the bounding-box diagonal to this
  step). `_source_open_boundary` now extracts the welded source's boundary
  once; hole filling drops new faces whose three vertices lie on it, and the
  residual stitching ignores edges whose endpoints both lie on it
  (`src_boundary` out-parameter of `decimate_with_mask`). Regression test:
  `test_decimate_with_mask_leaves_source_opening_open`.
- `refine_by_error` keeps the best pass: a pass replaces the current result
  only if its pooled maximum error is strictly lower (the first result is the
  baseline), so refinement can no longer return a result worse than its
  first pass. Regression test:
  `test_refine_by_error_never_returns_worse_than_initial_result`.
- `vtkDecimatePro` silently achieved 0% reduction on meshes exported with
  duplicated per-face vertices (no two triangles sharing a vertex -
  common in some OBJ/GLTF exporters; 4 of 19 models in the evaluation
  dataset had this): it collapses edges via triangle adjacency, so a
  mesh with no shared edges anywhere gives it nothing to collapse.
  `decimate_pro` now detects this cheaply (every edge is a boundary
  edge) and welds coincident points before decimating, only in that
  case (`_weld_if_fully_disconnected`, `remesh_core.py`).
  `vtkQuadricDecimation` wasn't affected (it clusters by spatial
  proximity instead) and is untouched.
- `decimate_with_mask` could leave a handful of non-manifold edges in
  the result, from two distinct causes: (1) `vtkDecimatePro` on its own
  can introduce them even into an otherwise clean, watertight mesh with
  no masking involved at all (verified on Stanford Armadillo/Bunny;
  `vtkQuadricDecimation` didn't reproduce this); (2) on the masked path,
  `.merge(protected_group, decimated_part, merge_points=True)` could
  also introduce them at the seam. Both are now repaired
  (`_repair_non_manifold_edges`/`_repair_non_manifold_edges_if_any`),
  with the masked path's repair placed between the existing
  `_weld_seam_gaps` and `_stitch_residual_boundary_gaps` steps
  specifically (verified empirically: repairing before `_weld_seam_gaps`
  gets undone by it; repairing after `_stitch_residual_boundary_gaps`
  leaves the boundary edges this repair opens up unclosed). A third
  cause, found by a 117-combo resolution/mask/reduction sweep at >=99%
  reduction: `_stitch_residual_boundary_gaps`'s own bowtie-vertex fan
  triangulation could itself introduce a non-manifold edge in ~14% of
  that sweep. Fixed with one more repair pass at the very end of
  `decimate_with_mask`'s masked branch (still a no-op everywhere else -
  0/19 real models trigger it), at the cost of a handful of new boundary
  edges (4-12) where it does fire - judged a better trade than leaving
  genuinely non-manifold topology in the result.
- The curvature-map checkbox stayed checked but silently had no visible
  effect while "Live preview" was on, or after "Apply decimation" until
  "Restore original" was pressed — both redraw the viewport with the
  decimated/preview mesh (`show_mesh(is_original=False)`), and curvature
  coloring only ever applies to the original mesh. Now
  `_update_curvature_availability` disables the checkbox and updates its
  tooltip whenever the original mesh isn't what's actually on screen, so
  the incompatibility is visible instead of a silent no-op.

### Added
- Viewport background color presets (Visualization panel): "Graphite"
  (default), "Onyx", "Slate" and "Steel", all sourced from the app's own
  dark palette so the 3D view stays consistent with the rest of the UI
  regardless of which is picked. Persisted in presets
  (`viewport_bg_index`); the comparison dialog inherits whichever one is
  currently selected instead of always resetting to the default.
- App icon (`assets/icon.png`/`assets/icon.ico`), set as the window/taskbar
  icon at runtime and as the packaged executable's icon on Windows.
- `remesh_core._stitch_residual_boundary_gaps`: a last resort after
  `_weld_seam_gaps` for the QEM seam at extreme reduction levels (see
  "Fixed" below). After thoroughly investigating why `_weld_seam_gaps`
  didn't fully close the gap above ~98% (tried without success:
  `meshing_repair_non_manifold_vertices` with several `vertdispratio`
  values, `meshing_snap_mismatched_borders` with several `edgedistratio`
  values, `meshing_remove_t_vertices`, and combining them with
  `vtkFillHolesFilter`), the exact diagnosis turned out to be a "bowtie"
  vertex: a point where the gap's boundary touches itself (4 boundary
  edges instead of 2), which no repair filter tried (PyMeshLab or VTK)
  knows how to resolve, because none of them separate the two loops that
  meet there. A manual closing step was implemented: it reconstructs the
  simple closed loops from the remaining boundary-edge graph (at each
  bowtie vertex, pairing each edge with the one most opposite in
  direction to separate the loops) and caps each one with a fan of
  triangles from its centroid. Reduces the residual to 0-2 edges (down
  from 5-56) across a sweep of 112 resolution/mask/aggressiveness
  combinations; still doesn't fully close in about 10% of the most
  extreme cases (>=99% with specific masks), documented as a known
  limitation.
- Configurable number of error-based auto-protection passes ("Max
  auto-protection passes" in the panel,
  `ProtectionSettings.error_max_iterations`,
  `refine_by_error(max_iterations=...)`): previously fixed at 3.
- Automatic smoke test in `.github/workflows/release.yml`: starts each
  freshly built executable (Windows/macOS/Linux) with
  `QT_QPA_PLATFORM=offscreen` and fails the build if it exits on its own
  within a few seconds — the typical symptom of a native dependency that
  didn't package correctly. Previously only the Windows build had been
  verified to actually start (see BUILD.md).
- README with installation, usage and project-structure instructions.
- `remesh_core.py`: domain logic (cleanup, both decimation engines,
  UV-seam protection, decimation + error-validation pipeline) extracted
  from `app.py`, with no Qt dependency.
- Unit test suite (`tests/`) for `remesh_core.py` using synthetic PyVista
  meshes.
- CI on GitHub Actions (lint, type checks and tests on every push/PR).
- Type checking with mypy (attribute annotations on
  `AdaptiveRemesherWindow`, no unnecessary `Optional` in `PipelineResult`).
- `app.py` split into UI modules under `ui/` (one per control-panel
  section), shrinking the main window's `__init__` from ~670 lines to a
  handful of orchestration calls.
- Packaging with PyInstaller to produce a desktop executable (see
  [BUILD.md](BUILD.md)).
- Pre-commit hooks (ruff, mypy, pytest) and Dependabot (pip and GitHub
  Actions) to keep dependencies and code quality current.
- MIT license.
- `logging_config.py`: console logging (INFO, or DEBUG with
  `ADAPTIVE_REMESHER_DEBUG=1`) plus a rotating file, replacing the old
  global `DEBUG`/`print` flag.
- UI integration tests with pytest-qt (`tests/test_ui_integration.py`):
  instantiate the real window and press real buttons/sliders, closing
  the coverage gap that left `ui/` out of automated testing.
- Release workflow (`.github/workflows/release.yml`): builds the
  executable on Windows, macOS and Linux with PyInstaller and attaches
  it to a GitHub Release when a `vX.Y.Z` tag is pushed.

### Changed
- Dependencies pinned to exact versions in `requirements.txt` instead of
  open minimums (`>=`).
- `apply_decimation` and batch processing now share the same pipeline
  (`run_decimation_pipeline`), removing the duplicated logic they used
  to have.
- **Error validation is now bidirectional**
  (`remesh_core.measure_bidirectional_error`): previously only the
  distance from each face of the result to the original surface was
  measured, which fails to detect a protruding original detail that was
  removed entirely (no face of the result lands near the missing area,
  so the measured error stayed ~0). Confirmed with a synthetic case: the
  original direction reported 0.0 error with an entire "bump" gone
  missing, the new reverse direction (original -> result) correctly
  detects it. `refine_by_error` now protects vertices flagged by either
  direction, and the displayed/exported error statistics (max/mean/p95)
  combine both.
- The protection tools (brush, rectangle) no longer discard the
  decimated result when reactivated: they only switch the view back to
  the original mesh so painting can continue against it
  (`_on_brush_toggled`). Previously, touching any of these tools after
  decimating forced starting the decimation over from scratch even if
  the user only wanted to review or add protection without painting
  anything.

### Fixed
- **Rectangle selection with no depth test.** It also marked points
  hidden behind another part of the mesh. Now, after the cheap
  screen-projection prefilter, `vtk.vtkSelectVisiblePoints` is used
  against the already-rendered z-buffer to discard points not visible
  from the current camera (`_apply_rect_selection`).
- **Residual gaps in the QEM seam above ~95% reduction.** After raising
  the boundary weight (see below), a failure margin remained where the
  seam wouldn't fully weld at very aggressive reduction levels: these
  weren't non-manifold edges (a standard repair changed nothing, verified
  empirically) but a genuine T-junction — a different number of edge
  segments on each side after decimation — which also wasn't resolved by
  raising `merge_points`'s tolerance (a high tolerance made things worse,
  welding unrelated points together). The fix that did work: closing
  those small gaps with `pymeshlab.meshing_close_holes` right after the
  merge (`remesh_core._weld_seam_gaps`, used by `decimate_with_mask`),
  capping the hole size to fill so it never touches large openings that
  are a genuine part of the mesh. Verified with a sweep of
  resolutions/masks/aggressiveness: fully closes the gap up to ~98%
  reduction (previously ~90%); above that, `_stitch_residual_boundary_gaps`
  (see "Added") closes almost everything that's left.
- **`_weld_seam_gaps` could raise an exception and break the whole
  decimation pipeline.** In a handful of resolution/mask combinations,
  the mesh resulting from the merge has genuinely non-manifold edges (3+
  faces), not just the usual T-junction, and PyMeshLab rejected
  `meshing_close_holes` with a `PyMeshLabException` instead of repairing
  it. This exception is now caught, returning the unrepaired mesh from
  that step instead of letting the exception propagate and cancel the
  whole decimation.
- **QEM engine: protected seam wouldn't weld above ~70% reduction.**
  `vtkQuadricDecimation` only *penalized* the cost of touching the
  boundary shared with a protected region (`SetBoundaryWeightFactor`),
  unlike vtkDecimatePro, which can lock it completely; at aggressive
  reduction levels it ended up collapsing vertices on that seam, leaving
  dozens of non-manifold edges after recombining the result (cracks
  invisible in the viewport but real in the exported mesh). Raised the
  weight from 1000 to 1e10 (`remesh_core.decimate_qem`): the seam now
  welds reliably up to ~90% reduction, verified with a sweep of
  resolutions and different protection masks. A failure margin persists
  above that point (~95-99%): it's a limitation of `vtkQuadricDecimation`
  itself, which doesn't expose an absolute topological lock the way
  vtkDecimatePro does.

## [0.1.0] - 2026-09-21

### Added
- Initial prototype: adaptive remesher with vtkDecimatePro and Quadric
  Error Metric, cleanup with PyMeshLab, manual protection (brush/
  rectangle), automatic UV-seam protection, geometric error validation,
  undo/redo, presets, live preview, side-by-side comparison, and batch
  processing.
