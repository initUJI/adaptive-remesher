# Changelog

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
