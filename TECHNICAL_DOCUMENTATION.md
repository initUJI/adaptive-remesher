# Technical Documentation — Adaptive Remesher

This document explains what the software is and who it is for, how it is
built internally down to the algorithm level, and which concrete problems
were found in the mesh-processing code during development, with the
reasoning and evidence behind each fix.

---

## 1. What it is and what it is for

**Adaptive Remesher** is a desktop prototype (Windows/macOS/Linux) for
**selectively simplifying (decimating) AI-generated 3D meshes**: reducing
triangle count where detail doesn't matter, while preserving geometry, texture
and any region the user marks as important.

### The problem it solves

Meshes coming out of AI generation pipelines (text-to-3D, image-to-3D,
photogrammetry, etc.) typically have two independent problems:

1. **Uniform, excessive polygon density.** The generator has no concept of
   where detail "matters" — a flat wall gets the same triangle density as a
   face or fingers, because the generation process doesn't reason about
   downstream use (rendering budget, print resolution, animation rig).
2. **Topological defects.** Duplicate or unreferenced vertices, non-manifold
   edges/vertices (an edge shared by more than two faces, or a vertex
   configuration that cannot exist on a real surface), disconnected floating
   fragments, and inconsistent face winding (some faces facing outward, some
   inward, within the same continuous surface).

A generic decimator would reduce triangles uniformly, which destroys exactly
the regions that matter most (a face, a logo, a texture seam) along with the
ones that don't. Adaptive Remesher lets the user **protect regions explicitly**
— by painting them by hand, or automatically via geometric criteria — and only
decimates the rest. It additionally **measures how far the decimated result has
drifted from the original** so it can automatically go back and protect more
geometry if the simplification was too aggressive somewhere, instead of leaving
that entirely up to a fixed angle threshold.

### Who it's for

Someone who receives meshes from an AI generation pipeline and needs to prepare
them for a downstream use (game engine, 3D printing, web viewer) without giving
up manual control over what gets simplified and what doesn't.

### How AI-specific is this, really?

Worth being precise about, since the framing above is easy to over-read:
**nothing in the codebase detects or reasons about mesh origin.** Every
technique — cleanup (§3.1), UV-seam protection (§3.2), both decimation
engines (§3.3), bidirectional error validation (§3.5), and the seam-gap
topology repair (§5.1) — is a generic mesh-processing technique that works
identically on a photogrammetry scan, a careless CAD export, or a hand-modeled
asset. None of them inspect where the mesh came from, and there is no
heuristic tuned to a specific generator's characteristic artifacts (e.g. no
detection of marching-cubes stair-stepping or diffusion-model surface noise).
"For AI-generated meshes" describes the *motivating use case* — this
particular combination of problems (uniform density unrelated to visual
importance, topological defects, floating fragments) is unusually common in
that pipeline — not a claim that the algorithms are origin-aware. Two
specific things worth calling out because they'd be reasonable to *assume*
otherwise:

- **Error-driven auto-protection (§3.5) does not account for the original
  mesh's density.** It only measures geometric deviation (a Hausdorff-style
  distance), with one global threshold scaled by the bounding-box diagonal —
  not local vertex density. A flat region that happens to have unusually high
  original density (common in AI output, since density there is arbitrary)
  gets decimated just as aggressively as any other flat region; there's no
  signal that says "this area used to be dense, be more careful here." What
  *does* end up protected more, as a side effect of measuring geometric
  error rather than by design, is high-curvature detail — decimating a curved
  region produces more positional deviation than decimating a flat one, so it
  crosses the error threshold sooner.
- **Floating-fragment removal (§3.1) is exactly the generic filter.**
  `clean_mesh` calls PyMeshLab's `meshing_remove_connected_component_by_diameter`
  with a single size-relative-to-whole-mesh threshold — the same operation any
  general-purpose mesh cleaner offers. There's no shape analysis or
  classification distinguishing "generation noise" from "a legitimately
  separate small object"; it's a pure size cutoff, exposed to the user as a
  slider rather than hidden behind an "AI cleanup" label.

---

## 2. High-level architecture

The project is split into two layers with clearly different responsibilities:

```
remesh_core.py    Pure domain logic: zero Qt dependency. Takes and returns
                  PyVista/numpy objects plus small configuration/result
                  dataclasses. Fully testable with plain pytest, no
                  QApplication, no mocked Qt.

app.py + ui/      Qt (PySide6) interface: interactive state (current mesh,
                  protection mask, undo/redo history), the embedded 3D
                  viewport (PyVista/VTK via pyvistaqt), and the construction
                  of every control-panel section, split one function per
                  section in ui/panels/.
```

This split is not cosmetic. **All decimation, cleaning, and validation logic
can be exercised with synthetic PyVista meshes without ever opening a window**,
which is what makes `tests/test_remesh_core.py` fast and deterministic — no Qt
event loop, no rendering, no platform plugin. The UI itself is tested
separately with `pytest-qt` (`tests/test_ui_integration.py`), which instantiates
the real `AdaptiveRemesherWindow`, presses real buttons/sliders, and asserts on
real widget state, running against Qt's `offscreen` platform plugin so it also
works headless in CI.

`app.py`'s `AdaptiveRemesherWindow` acts purely as an **orchestrator**: it owns
UI state (loaded mesh, protection mask, undo/redo stack) and reacts to Qt
events, but delegates *all* heavy domain work to `remesh_core.py`. For example,
`AdaptiveRemesherWindow.apply_decimation()` ([app.py:912](app.py:912)) reduces to
gathering the current panel configuration (`_gather_clean_settings`,
`_gather_decimation_settings`, `_gather_protection_settings`) and calling
`run_decimation_pipeline(...)` — the entire mesh-processing logic lives outside
the class.

Batch processing (`run_batch` / `_process_one_batch_file`, [app.py:1060](app.py:1060))
and the interactive "Apply decimation" button **share the exact same pipeline**
function, `run_decimation_pipeline`. Before this was refactored, the codebase
had two near-identical copies of this logic — one for the interactive path, one
for batch — which is a classic source of silent divergence bugs (a fix applied
to one copy but not the other). The only difference between the two call sites
today is that batch processing always passes `manual_mask=None`, since there is
no open viewport to paint on for files that are never displayed.

### End-to-end data flow

```
file (OBJ / STL / PLY / VTP / GLB / GLTF)
        │  load_and_prepare_mesh()
        │    - pv.read() -> extract_surface() -> triangulate()
        │    - ensure_active_texture_coords()
        ▼
PyVista PolyData (triangulated, UV active if present)
        │  clean_mesh()                       [optional: PyMeshLab cleanup]
        ▼
cleaned mesh
        │  protection mask = manual (brush/rectangle) OR UV-seam mask (or both)
        │  run_decimation_pipeline()
        ├─► decimate_with_mask()   [split protected/unprotected, decimate, merge]
        └─► refine_by_error()      [if auto-error-protection is on: measure
                                     bidirectional error, protect bad regions,
                                     re-decimate, up to 3 passes]
        ▼
PipelineResult (final mesh + all statistics)
        │
        ▼
viewport (optional side-by-side comparison) / export to disk (+ optional .json report)
```

---

## 3. Internal technical operation

### 3.1. Loading and cleaning

`load_and_prepare_mesh` ([remesh_core.py:351](remesh_core.py:351)) normalizes any
supported input format to a pure triangulated surface mesh via
`pyvista.read()` → `extract_surface(algorithm="dataset_surface")` →
`triangulate()`. It raises `ValueError` if the result has no points or cells
(e.g. the file didn't contain a valid surface).

`ensure_active_texture_coords` ([remesh_core.py:30](remesh_core.py:30)) exists
because some file readers leave texture coordinates as a plain point-data
array without flagging it as the "active" texture coordinate array
(`mesh.active_texture_coordinates` stays `None` even though a UV-shaped array is
present under a name like `"UVMap"` or `"TEXCOORD_0"`). Without this step, UV
seam protection and the QEM UV-distortion weight would both silently no-op —
not raise, not warn, just quietly skip a feature the user thinks is active. The
function scans `point_data` for a 2-column float array whose name contains
`"uv"`, `"tex"`, or `"tcoord"` (case-insensitive) and promotes the first match.

`clean_mesh` ([remesh_core.py:47](remesh_core.py:47)) delegates to **PyMeshLab**,
building a `pymeshlab.MeshSet` from the PyVista vertex/face arrays and running,
in order:

1. `meshing_remove_duplicate_vertices()`
2. `meshing_remove_unreferenced_vertices()`
3. `meshing_repair_non_manifold_edges()`
4. `meshing_repair_non_manifold_vertices()`
5. optionally, `meshing_re_orient_faces_coherently()` (fixes mixed inward/outward
   face winding — the "inside-out shading" artifact some generators produce)
6. `meshing_remove_connected_component_by_diameter(mincomponentdiag=...)` if
   `min_component_pct > 0` — removes floating fragments smaller than a
   percentage of the bounding-box diagonal
7. optionally, `meshing_close_holes(maxholesize=...)` — fills small holes, which
   is useful after fragment removal but can also close *intentional* openings
   (eyes, mouth) if the threshold is set too high, hence it defaults to off

The cleaned mesh is converted back to a `pv.PolyData` by reading
`vertex_matrix()`/`face_matrix()` off the PyMeshLab mesh and re-prefixing each
face row with `3` (PyVista's flat face-array convention: `[n, v0, v1, v2, n,
v0, v1, v2, ...]`).

### 3.2. Region protection

There are two independent sources of protection, both resolved into a single
**per-vertex boolean mask** before decimation:

- **Manual painting/selection** (`app.py`, `_apply_brush` /
  `_apply_rect_selection`): the user paints or rubber-band-selects over the 3D
  viewport; vertices under the cursor are found against a `scipy.spatial.cKDTree`
  built once from the *original* mesh's points. This is why the brush is
  auto-disabled after decimation (see 3.6) — the tree indexes the original
  mesh, and after a QEM pass the surviving vertices may no longer sit at those
  same tree-indexed positions.
- **Automatic UV-seam protection** (`uv_seam_point_mask`,
  [remesh_core.py:85](remesh_core.py:85)): detects vertices that occupy the same
  3D position but carry different UV coordinates — the standard way OBJ/GLTF
  represent a texture seam (the surface is geometrically continuous, but the
  texture "unwrap" had to cut it there). If decimation collapses these
  duplicated vertices together, the seam visibly tears open in the texture even
  though the underlying geometry is fine.

  The detection is fully vectorized: `cKDTree.query_pairs(r=tol,
  output_type="ndarray")` finds every pair of vertices within `1e-5 *
  mesh.length` of each other in *one* call, then a single boolean numpy
  comparison (`np.abs(a - b) <= (1e-4 + 1e-5 * np.abs(b))`, matching
  `np.allclose`'s default tolerances) checks all pairs' UV coordinates at once.
  This replaced an earlier per-pair Python loop calling `np.allclose` one pair
  at a time — on meshes with thousands of seam vertices (common for AI-generated
  assets with heavy UV atlasing), that loop was, by a wide margin, the slowest
  part of the entire automatic-protection detection.

### 3.3. The two decimation engines

`decimate_pro` ([remesh_core.py:140](remesh_core.py:140)) wraps
**`vtk.vtkDecimatePro`**: it collapses vertices in flat regions according to a
**fixed feature angle** (`SetFeatureAngle`) — below the angle a region is
considered flat and eligible for simplification, above it the edge is treated
as a hard feature/corner and preserved as-is. It also exposes
`SetPreserveTopology` (refuse collapses that would change the mesh's genus) and
`SetSplitting`/`SetBoundaryVertexDeletion`. This algorithm is fast and
predictable, but binary: there is no gradation between "fully preserved" and
"fully collapsed" — a region is either above or below the angle threshold.

`decimate_qem` ([remesh_core.py:166](remesh_core.py:166)) wraps
**`vtk.vtkQuadricDecimation`** (Garland-Heckbert *Quadric Error Metric*): the
cost of each edge collapse is a continuous quadratic error that already
encodes local curvature — a nearly flat area has low error and collapses
first, a highly curved area has high error and survives longer. This produces
smoother results than a fixed angle cutoff. It also supports folding extra
error terms into the same cost function via `SetAttributeErrorMetric(True)`:

- **UV distortion weight** (`use_uv_weight` / `tcoords_weight` →
  `SetTCoordsAttribute` / `SetTCoordsWeight`): discourages collapses that would
  stretch the texture, even outside the seams that are separately protected by
  `uv_seam_point_mask`.
- **Vertex-normal weight** (`use_normals_weight` / `normals_weight` →
  `SetNormalsAttribute` / `SetNormalsWeight`): without this, QEM can flatten a
  smoothly curved area into visibly faceted triangles, because by default it
  only measures *positional* error, not shading error. Penalizing normal
  change keeps the shaded appearance closer to the original on heavily
  decimated organic meshes. If the mesh doesn't already carry vertex normals,
  `decimate_qem` computes them on the fly via `compute_normals(cell_normals=
  False, point_normals=True, split_vertices=False, consistent_normals=False,
  auto_orient_normals=False)` — and deletes them from the output afterward if
  they weren't part of the original mesh (see 3.4, this matters for the merge
  step).

### 3.4. Decimating while respecting protection (`decimate_with_mask`)

VTK has no parameter meaning "leave these specific vertices untouched during
decimation" for either engine. `decimate_with_mask`
([remesh_core.py:404](remesh_core.py:404)) works around this purely
geometrically, in three steps:

1. **Split** the mesh into two independent `PolyData` objects with
   `PolyData.extract_points`: the protected region (`adjacent_cells=True`, so
   it keeps whole faces touching a protected vertex) and everything else
   (`adjacent_cells=False`).
2. **Decimate only the unprotected piece**, with its inner boundary — the edge
   that used to be shared with the protected region — locked
   (`lock_boundary=True` → `boundary_locked=True` for QEM, or
   `boundary_vertex_deletion=False` for DecimatePro). If there's no unprotected
   geometry at all, the protected piece is returned as-is.
3. **Recombine** both pieces with `protected_group.merge(decimated_part,
   merge_points=True)`. `merge_points=True` welds vertices that land at (nearly)
   the same 3D position, which is what stitches the shared boundary back into
   one continuous surface.

The entire correctness of this approach hinges on step 2's boundary staying
**exactly** where it was before decimation — if even a handful of boundary
vertices move or get removed, `merge_points` can't find an identical vertex to
weld to on the other side, and the seam opens up as a set of non-manifold
edges. This is exactly the failure mode analyzed in section 4.1, because
`vtkDecimatePro` can enforce that "exactly" as a hard constraint while
`vtkQuadricDecimation` can only discourage it with a cost penalty.

Note that `SetTargetReduction`/`settings.reduction_pct` (step 2) is passed to
`decimate()` as the reduction target for the **unprotected piece only** — the
protected piece is never given to the decimator at all, so the requested
percentage is never a fraction of the whole original mesh. `PipelineResult`
reports both: `reduction_real_pct` (`1 - result_tris/original_tris`, the whole
merged mesh) and `unprotected_zone_reduction_pct` (the same ratio, but of just
the unprotected piece's triangle count before/after step 2 — what the target
percentage actually applies to). With a large protected region these two can
diverge substantially: requesting 50% with most of the mesh protected can
still report a low whole-mesh `reduction_real_pct` even though the unprotected
zone hit its 50% target exactly, and without the second number that looked
like the reduction wasn't applying at all. `decimate_with_mask`'s optional
`zone_stats` out-parameter (a plain dict, filled in place) is how
`run_decimation_pipeline` recovers these two counts from whichever
`decimate_with_mask` call ends up being the last one run (either the initial
call, or the last iteration of `refine_by_error` if auto-error-protection
added more protected vertices afterward).

`decimate()` ([remesh_core.py:376](remesh_core.py:376)) is the small dispatcher
between the two engines that `decimate_with_mask` calls for the unprotected
piece; it resolves `lock_boundary` against the user's own
`boundary_vertex_deletion` setting so a genuinely open mesh boundary (not a
protection seam) still follows the user's preference when there's no
protection mask in play at all.

### 3.5. Geometric error validation

`measure_error` ([remesh_core.py:679](remesh_core.py:679)) approximates
one-directional Hausdorff distance: for every face centroid of a mesh, its
absolute distance to a reference surface represented as a
`vtk.vtkImplicitPolyDataDistance`. Two implementation details matter:

- It measures at **face centroids**, not at vertices. Vertices that survive
  `vtkDecimatePro` keep their *exact* original 3D position, so their distance
  to the original surface would always be ~0 and would completely hide the real
  error, which lives in the interior of the large new triangles created by
  collapsing their neighbors.
- The `vtkImplicitPolyDataDistance` (`distance_fn`) is accepted as an
  already-built parameter rather than constructed inside the function, so that
  `refine_by_error`'s iterative loop (below) doesn't rebuild the spatial
  locator against an unchanging `source` mesh on every one of its up-to-3
  passes.

`error_summary` ([remesh_core.py:697](remesh_core.py:697)) reduces a raw error
array to three numbers: **max** (worst single point — the only number shown
before this work), **mean** (overall quality), and **95th percentile** (how bad
the worst *band* of the mesh is without a single outlier pixel dominating the
number — more informative than the max on large meshes where one degenerate
triangle can otherwise make the whole result look worse than it is).

`measure_bidirectional_error` ([remesh_core.py:724](remesh_core.py:724)) measures
**both** directions — `result → source` (the original one) and
`source → result` (added in this session; see section 4.2 for why the first
direction alone is insufficient) — and returns all four arrays
`(forward_errors, forward_centroids, reverse_errors, reverse_centroids)` so the
caller can localize a problem in whichever mesh it was actually detected in.

`refine_by_error` ([remesh_core.py:761](remesh_core.py:761)) closes the loop:
given a threshold (`error_threshold_pct`, expressed as a percentage of the
mesh's bounding-box diagonal, so it scales with mesh size), it:

1. Measures bidirectional error once.
2. If any centroid/point on either side exceeds the threshold, maps the bad
   regions back to **source vertex indices** — forward hits (found on `result`)
   via approximate nearest-neighbor lookup against a `cKDTree` of `source.points`
   (approximate because a face centroid never coincides exactly with a source
   vertex), reverse hits (found directly on `source`) via **exact** lookup
   using the flagged face's own vertex indices (`source.regular_faces[bad_rev]`,
   which gives the exact `(n_cells, 3)` vertex-index array for a triangulated
   mesh — no approximation needed since the point already *is* a source
   vertex).
3. Adds any newly-identified, not-yet-protected vertices to the mask and
   re-runs `decimate_with_mask`.
4. Repeats for up to **3 iterations**, or stops early once both directions are
   within threshold, or stops if a pass adds zero new protected vertices
   (guards against a threshold so strict it can never be satisfied).

Final statistics are computed by `bidirectional_error_summary`
([remesh_core.py:709](remesh_core.py:709)) as an `ErrorStats`
([remesh_core.py:288](remesh_core.py:288)): it calls `error_summary` three
times — once on both directions' error arrays concatenated together (`max`,
`mean`, `p95`, the single pooled numbers this app always showed before), and
once each on `fwd_errors` alone (`max_fwd`/`mean_fwd`/`p95_fwd`,
result→source) and `rev_errors` alone (`max_rev`/`mean_rev`/`p95_rev`,
source→result). The pooled number alone can't distinguish *why* a run scored
badly; the split can, because the two directions signal different failure
modes: a high `_fwd` error means the decimation itself deviated from the
original surface (e.g. an edge collapse bulging out), while a high `_rev`
error means a whole feature of the original disappeared in the result with
nothing left nearby to measure against (see the "fully removed feature" case
above). Both the pooled and the per-direction numbers end up in
`PipelineResult` and the exported `.json` report, not just the pooled ones.

`run_decimation_pipeline` ([remesh_core.py:820](remesh_core.py:820)) is the
single public entry point used by both the interactive button and batch
processing: it builds the mask (manual ∪ UV-seam), calls `decimate_with_mask`,
raises `ValueError` if that leaves zero cells, and then either calls
`refine_by_error` (if `protection.auto_error_protection`) or measures
bidirectional error once anyway, purely as a diagnostic for the exported
report — even users who don't want automatic re-protection still get to see
how far the result deviates from the source.

### 3.6. UI layer (`app.py`, `ui/`)

`AdaptiveRemesherWindow` ([app.py:101](app.py:101)) owns:

- `original_mesh` / `decimated_mesh`: the two `pv.PolyData` currently held.
- `protected_mask`: the per-vertex boolean protection mask edited by
  brush/rectangle tools.
- An **undo/redo stack** built from full state snapshots
  (`_snapshot`/`_push_undo`/`_restore_snapshot`, [app.py:288](app.py:288)):
  every destructive operation (decimate, reset, clear protection, import mask)
  pushes a snapshot of `{mesh state, protection mask}` *before* it runs, so
  undo/redo simply swaps to a previously captured snapshot rather than
  attempting to reverse an operation analytically.
- A collapsible-group layout with its expand/collapse state persisted to
  `.panel_layout.json` next to the script.

Class-level type annotations (e.g. `undo_btn: QPushButton`,
`auto_error_cb: QCheckBox`, [app.py:109-144](app.py:109)) declare every widget
that a `build_xxx_panel()` function in `ui/panels/` will attach to `self` at
construction time — the assignment itself happens inside those functions, not
in `__init__`; the annotations exist purely so `mypy` can type-check code that
reads `window.some_widget.value()` elsewhere in `app.py` without treating every
such access as an unknown attribute.

Each control-panel section lives in its own module under `ui/panels/`
(`header.py`, `presets.py`, `clean.py`, `protection.py`, `decimation.py`,
`validation.py`, `view.py`, `actions.py`, `batch.py`), each exposing one
`build_xxx_panel(window, panel_layout)` function that both constructs the
widgets and wires their signals to methods on `window`. This is what shrank
`AdaptiveRemesherWindow.__init__` from roughly 670 lines of inline widget
construction to a short sequence of `build_xxx_panel(self, layout)` calls.

`apply_decimation()` ([app.py:912](app.py:912)) is the main interactive
entry point: gather settings → `run_decimation_pipeline` inside a
`busy_cursor()` context manager → on success, push undo, swap in the new mesh,
disable the paint/erase buttons (see below), and format both the on-screen
stats label and a `_last_run_info` dict that becomes the exported `.json`
report if the user checked "export report" at export time.

The brush is force-disabled right after a successful decimation
(`self.paint_btn.setChecked(False)`, [app.py:947](app.py:947)) because it paints
against the *original* mesh's `cKDTree`, and after a QEM pass the unprotected
vertices have moved — painting immediately after decimating would silently
mark the wrong points as protected against a stale coordinate mapping.

`export_mesh()` ([app.py:1036](app.py:1036)) writes the current mesh to disk
(`mesh.save(path)`, format inferred from the chosen extension via VTK/PyVista's
writers) and, if "export report" is checked and a decimation has already run,
writes a sibling `<name>_report.json` with the `_last_run_info` dict.

### 3.7. Batch processing

`run_batch()` ([app.py:1107](app.py:1107)) lets the same configured pipeline run
over every compatible file (`.obj/.stl/.ply/.vtp/.glb/.gltf`) in an input
folder, writing results (and optional per-file `.json` reports) to an output
folder, without ever loading anything into the viewport. Per file,
`_process_one_batch_file()` ([app.py:1060](app.py:1060)) calls
`load_and_prepare_mesh` → `run_decimation_pipeline(..., manual_mask=None)` —
**manual protection never applies in batch mode**, since there's no open
viewport to have painted on; only UV-seam protection and error-based
auto-protection are available for batch runs. A failure on one file is caught,
logged, and does not stop the rest of the batch; the user gets a final
success/failure summary. The tool also warns (but does not block) if the
chosen output folder is the same as the input folder, since same-named files
would be silently overwritten.

### 3.8. Presets

`_gather_preset()` / `_apply_preset()` ([app.py:1202](app.py:1202)/[app.py:1235](app.py:1235))
serialize/restore every panel parameter (engine choice, reduction target,
feature angle, QEM weights, protection toggles, error threshold, etc.) to and
from a `.json` file — deliberately **excluding** the loaded mesh and the manual
protection mask, since a preset is meant to be reusable across different
meshes/sessions, not tied to one specific model.

### 3.9. Logging

`logging_config.configure_logging()` sets up standard Python `logging`: console
output at `INFO` (or `DEBUG` if the environment variable
`ADAPTIVE_REMESHER_DEBUG=1` is set) plus a `RotatingFileHandler` writing to
`adaptive_remesher.log` next to the script (rotating at 1 MB, keeping up to 3
backups) that **always** captures `DEBUG`, regardless of the console level —
so that a bug report from someone running the packaged executable comes with a
detailed log file without needing to ask them to reproduce it with a debug flag
turned on live.

### 3.10. Packaging, CI and dependency management

Beyond the mesh-processing code itself, the project ships supporting
infrastructure worth documenting for completeness:

- **Tests** (`pytest`): `tests/test_remesh_core.py` exercises `remesh_core.py`
  exclusively with synthetic PyVista meshes (spheres, planes) and needs no
  display; `tests/test_ui_integration.py` uses `pytest-qt`'s `qtbot` fixture to
  drive the real `AdaptiveRemesherWindow` (`tests/conftest.py` sets
  `QT_QPA_PLATFORM=offscreen` before Qt is imported, so this also runs headless
  in CI).
- **Static analysis**: `ruff` for linting, `mypy` for type-checking
  (`pyproject.toml`'s `[tool.mypy]` deliberately does **not** pin
  `python_version` — see 4.3).
- **CI** (`.github/workflows/ci.yml`): installs system Qt libraries, sets
  `QT_QPA_PLATFORM=offscreen`, then runs ruff, mypy, and pytest on every
  push/PR.
- **Dependabot** (`.github/dependabot.yml`): keeps pip and GitHub Actions
  dependencies current.
- **Pre-commit** (`.pre-commit-config.yaml`): ruff, mypy (with
  `additional_dependencies: [numpy, scipy]` so it can resolve their stubs), and
  a local pytest hook, so the same checks CI runs also run locally before a
  commit is created.
- **Packaging**: `adaptive-remesher.spec` (PyInstaller) builds a standalone
  desktop executable; `.github/workflows/release.yml` builds it on
  Windows/macOS/Linux and attaches the artifacts to a GitHub Release whenever a
  `vX.Y.Z` tag is pushed.
- **Dependency pinning**: `requirements.txt` lists runtime dependencies with
  open (`>=`) minimum versions (a deliberate choice — the project briefly
  pinned exact versions, then reverted, to avoid forcing a specific patch
  version on users who may already have a compatible environment);
  `requirements-dev.txt` pins exact versions for the dev/test tools
  (`pytest==8.3.3`, `pytest-qt==4.5.0`, `ruff==0.7.4`, `mypy==2.3.1`,
  `pre-commit==4.0.1`, `pyinstaller==6.22.3`) since those only need to be
  reproducible for contributors, not for end users.

---

## 4. Problems found and how they were fixed

These are the two **real algorithmic problems** (as opposed to infrastructure
issues) found through targeted empirical testing of `remesh_core.py`, each
reproduced with a regression test that fails before the fix and passes after.

### 4.1. QEM engine failed to weld the protection seam above ~70% reduction

**Symptom:** decimating with the QEM engine and an active protection mask, at
aggressive reduction targets, produced dozens of non-manifold edges in the
exported mesh — invisible cracks in the viewport (surviving vertices were
still nearly in place) but real defects in the exported geometry, the kind
that break downstream tools expecting a watertight/manifold mesh.

**Root cause:** `vtkDecimatePro` can **fully lock** a region's boundary
(`SetBoundaryVertexDeletion(False)`), but `vtkQuadricDecimation` exposes no
equivalent hard lock — it can only **penalize** the cost of touching the
boundary via `SetBoundaryWeightFactor`. With the original weight (`1000.0`), at
roughly 70%+ reduction the algorithm's global cost minimization decided that
collapsing a boundary vertex was worth it despite the penalty, moved it, and
`merge_points` at the subsequent recombination step could no longer find an
identical position on the other side of the seam to weld to.

An alternative hypothesis — that boundary vertices *drifted in position*
after QEM decimation — was investigated first by directly measuring the
displacement of surviving boundary vertices: it was on the order of `1e-14`
(numerically zero), so this was ruled out. The real cause was purely
**topological**: the two sides of the seam ended up with a different number of
boundary edge segments after decimation (a genuine T-junction mismatch), which
no amount of positional snapping alone can fix. A weld-by-snapping fallback was
prototyped in a scratch script but deliberately **not** ported into
`remesh_core.py`, since it doesn't reliably address the underlying topological
mismatch and would add complexity for inconsistent benefit.

**Fix** ([remesh_core.py:230](remesh_core.py:230), inside `decimate_qem`): raise
the boundary weight from `1000.0` to `1e10`. Verified with a manual sweep
across mesh resolutions and protection masks: the seam now stays reliably
welded up to ~90% reduction. A residual failure margin remains above that
(~95–99%): this is an inherent limitation of `vtkQuadricDecimation`, which has
no way to enforce an absolute topological lock the way `vtkDecimatePro` does —
raising the weight further doesn't help, because at that extreme VTK
prioritizes meeting the requested `TargetReduction` over respecting the
boundary weight.

**Regression test:**
[`test_decimate_with_mask_qem_seam_is_watertight`](tests/test_remesh_core.py)
decimates a sphere with the QEM engine at 80% reduction (a level empirically
confirmed reliable) and asserts that
`extract_feature_edges(boundary_edges=True, non_manifold_edges=True,
feature_edges=False, manifold_edges=False)` finds zero problem edges.

### 4.2. Error measurement only looked in one direction (a Hausdorff blind spot)

**Symptom (theoretical, confirmed with a hand-built synthetic case):** the
geometric error validation could report ~0 error even when decimation had
**completely removed** a protruding feature of the original mesh (a bump, a
spike, an appendage) — exactly the kind of loss the feature exists to catch.

**Root cause:** `measure_error` only asked *"how far is every face centroid of
the **result** from the **original** surface?"*. If the result no longer has a
single face anywhere near the removed area, that question is simply never
posed there — every face that survives in the result was already part of the
original, so its distance to the original is ~0 regardless of what's missing
elsewhere. This mirrors exactly why the *true* Hausdorff distance is defined as
the maximum of **both** directions, not one.

Two earlier reproduction attempts were inconclusive before this was pinned
down cleanly: a single-vertex "spike" decimated with `vtkDecimatePro` (VTK
correctly preserved it as a sharp feature via the angle threshold, so there
was no actual gap to detect), and a multi-vertex "bump" decimated with QEM
(mixed results — in that specific case the forward-direction mean was actually
*higher* than the reverse-direction mean, not a clean demonstration of the
blind spot). The test that finally demonstrated the problem unambiguously was
constructed by hand: `source` = a base plane merged with a separate bump
*without* welding points, and `result` = only the base plane (simulating a
decimation pass that flattens the bump away entirely).

**Fix:**

- `measure_bidirectional_error` (new function in `remesh_core.py`,
  [remesh_core.py:724](remesh_core.py:724)): measures both `result → source`
  (the original direction) and `source → result` (the new one), reusing the
  `source`-side spatial locator across refinement passes for efficiency (see
  3.5).
- `refine_by_error` rewritten to protect vertices flagged by **either**
  direction — approximate nearest-neighbor lookup for forward-detected issues,
  exact face-vertex lookup for reverse-detected issues — and the reported
  max/mean/p95 error statistics now pool both directions' arrays.
- `run_decimation_pipeline`'s diagnostic-only branch (when
  `auto_error_protection` is off) also measures both directions, for the same
  reason: even without automatic re-protection, the exported report shouldn't
  claim near-zero error when a feature was in fact silently dropped.

**Regression test:**
[`test_measure_bidirectional_error_detects_fully_removed_feature`](tests/test_remesh_core.py)
reproduces the base-plane-plus-bump case above and asserts the forward
direction reports max error `< 1e-6` (sees nothing wrong) while the reverse
direction reports `> 1.0` (correctly detects the missing bump).

### 4.3. Smaller defects surfaced while hardening the project

Introducing the test suite and `mypy` also surfaced (and fixed) several
smaller but genuine issues, found incidentally rather than through targeted
investigation:

- **`ensure_active_texture_coords` failed to activate a non-default-named UV
  array** in one tested case
  (`test_ensure_active_texture_coords_activates_named_uv_array`): the
  name-based detection logic didn't cover the array-naming pattern exercised
  by the test. The detection logic was adjusted so the test became a genuine,
  passing regression rather than a known gap.
- **`run_decimation_pipeline` didn't reliably raise** when decimation left the
  mesh with zero faces in every expected case
  (`test_run_decimation_pipeline_raises_when_result_is_empty`): the
  `result.n_cells == 0` guard was strengthened to also cover the path after
  `refine_by_error`'s iterative refinement, not just the initial decimation
  call.
- **Pinning `python_version` in the `mypy` config broke type-checking**
  against newer `numpy` stubs, which use PEP 695 generic syntax unavailable
  under an artificially declared older Python version. The pin was removed
  from `pyproject.toml`'s `[tool.mypy]` section entirely rather than kept in
  sync with whatever numpy happens to require.

---

## 5. Follow-up: closing the previously-known limitations

Section 4's fixes were followed by a second pass specifically targeting the
five items that used to be listed here as accepted-but-unfixed limitations.
Four turned out to be genuinely fixable; one is now much smaller but not
completely eliminated.

### 5.1. QEM seam gaps above ~95% reduction — mitigated further

Section 4.1 fixed the seam cracking up to ~90% reduction by raising the
boundary weight. Above that, a residual gap remained: not non-manifold edges
(faces sharing an edge with more than one neighbor — a genuine repair filter
would apply there), but ordinary **boundary edges on both sides of the seam
that simply don't line up**, a real T-junction caused by the two sides ending
up with a different number of edge segments after independent decimation.

Two natural-seeming fixes were tried and empirically ruled out before finding
one that works:

- **`pymeshlab.meshing_repair_non_manifold_edges`/`..._vertices`** — no
  effect whatsoever (triangle count identical before/after across a sweep of
  resolutions/masks/reduction levels), because these target edges shared by
  *more than two* faces, not simple unmatched boundary edges.
- **Raising `merge_points`'s tolerance** in the recombination step — barely
  helped at a small tolerance, and *increased* the number of problem edges at
  a larger one, because it started welding unrelated nearby points into
  degenerate connectivity elsewhere on the mesh.

What actually works: **`pymeshlab.meshing_close_holes`**, applied right after
the merge, with a small `maxholesize` cap (100 boundary edges) so it only
patches small artifacts like this seam gap and never touches a large opening
that's a genuine part of the mesh (eyes, mouth). New helper
`_weld_seam_gaps()` in [remesh_core.py](remesh_core.py) is now called
unconditionally at the end of `decimate_with_mask`'s merge branch. Verified
with the same kind of sweep as before: **fully watertight up to ~98%
reduction** (up from ~90%); at the most extreme case tested (99%), it closes
80–90% of the remaining problem edges but not always all of them — a residual
gap this small (single digits of edges, down from dozens) is the one item from
this list that remains only *mitigated*, not eliminated. New regression test:
[`test_decimate_with_mask_qem_seam_is_watertight_at_extreme_reduction`](tests/test_remesh_core.py).

**Update — closed further with a custom stitcher.** Asked to look harder at
this residual instead of accepting it, a deeper investigation diagnosed the
exact remaining defect: a "bowtie" vertex, a single point where the gap's
boundary loop touches itself (4 boundary edges meet there instead of 2) —
this is why neither PyMeshLab's `meshing_repair_non_manifold_vertices` (any
`vertdispratio`), `meshing_snap_mismatched_borders` (any `edgedistratio`), nor
`meshing_remove_t_vertices`, nor VTK's own `vtkFillHolesFilter`, could close
it: none of them separate the two loops that meet at that one point. A
targeted post-process, `_stitch_residual_boundary_gaps`
([remesh_core.py:584](remesh_core.py:584)), now runs after `_weld_seam_gaps`:
it rebuilds the simple closed loops hiding in the residual boundary-edge
graph — at each bowtie vertex, pairing each incident edge with the one most
opposite in direction (`_pair_seam_branch_neighbors`, the pairing that best
continues each loop in a straight line through the point) — and caps each
loop with a fan of triangles from its centroid. Verified on the same 112-case
sweep (resolutions 15–75, three mask shapes, reduction up to 99.9%): residual
problem edges dropped from 5–56 down to 0–2 in the remaining hard cases,
fully closing about 90% of the previously-failing combinations. Also
uncovered and fixed in the same pass: `_weld_seam_gaps` could raise
`pymeshlab.PyMeshLabException` and crash the whole decimation pipeline, on
the handful of cases where the raw merge already contains genuinely
non-manifold edges (3+ faces sharing one edge, not just the usual
T-junction) — PyMeshLab refuses to run `meshing_close_holes` on those at all.
This is now caught, falling back to the unrepaired mesh rather than
propagating the exception. New regression tests:
[`test_decimate_with_mask_qem_seam_closed_by_residual_gap_stitcher`](tests/test_remesh_core.py)
and
[`test_decimate_with_mask_never_raises_when_pymeshlab_repair_rejects_mesh`](tests/test_remesh_core.py).

**Openings that belong to the source are left open.** Both repair steps used
to treat every boundary loop left after merging as a seam gap. On a source
that itself has open boundaries (4 of the 19 evaluation meshes), this capped
them with fan triangles away from the surface and raised the maximum error
(up to 11.8% of the bounding-box diagonal on the blastoid). `_source_open_boundary`
now extracts the welded source's boundary edges once (a `cKDTree` of their
vertices, tolerance 1e-6 of the source length); `_weld_seam_gaps` drops new
faces whose three vertices lie on it and `_stitch_residual_boundary_gaps`
ignores edges whose endpoints both lie on it. `refine_by_error` also keeps
the best pass (strictly lower pooled maximum error than the current result;
the first result is the baseline). After both changes refinement raised the
error on none of the 19 meshes, but on three of them (trilobite, blastoid,
lion) it returns the first result unchanged, and the evaluation did not
separate how much is due to each change. Tests:
[`test_decimate_with_mask_leaves_source_opening_open`](tests/test_remesh_core.py)
and
[`test_refine_by_error_never_returns_worse_than_initial_result`](tests/test_remesh_core.py).

### 5.2. Rectangle selection now does real depth testing — fixed

`_apply_rect_selection()` ([app.py:560](app.py:560)) kept its cheap
screen-projection prefilter (`_project_points_to_qt`, to narrow the whole mesh
down to the handful of vertices inside the rectangle), but now runs
`vtk.vtkSelectVisiblePoints` on those candidates against the renderer's actual
z-buffer (`SetRenderer(self.plotter.renderer)`), which discards any candidate
occluded by another part of the mesh from the current camera angle. The
mapping back to original vertex indices is threaded through a point-data array
(`candidates.point_data["_orig_idx"]`) that the VTK filter carries through to
its output, since the filter's output is a subset of input points, not a
boolean mask.

**Testing caveat:** an automated `pytest-qt` regression test for this was
attempted (drive a rectangle covering a whole rendered sphere and assert only
the front-facing half gets marked) but had to be dropped — on this Windows
sandbox, the `offscreen` Qt platform plugin has no real OpenGL context
(`QOpenGLWidget is not supported on this platform`), so `vtkSelectVisiblePoints`
reads an empty z-buffer and marks nothing. `vtkSelectVisiblePoints` against a
render window's z-buffer is standard, well-established VTK usage for a real
on-screen session, so this is very likely a sandbox-only limitation rather
than a code defect — but it could not be verified end-to-end here, including
whether Linux CI's software `libgl1` rendering fares any better. **This
behavior should be manually verified in a real windowed session** (drag a
rectangle across a mesh where part of it faces away from the camera) before
relying on it.

### 5.3. Brush/rectangle no longer discards the decimated result — fixed

Previously, `_on_brush_toggled()` called `reset_mesh()` whenever a protection
tool was turned on while viewing a decimated result — which explicitly sets
`self.decimated_mesh = None`, permanently discarding it even if the user just
wanted to glance at the original mesh and turn the tool back off without
painting anything. The fix switches the *view* back to the original mesh
(preserving the current camera position) without nulling `decimated_mesh` or
pushing an undo step. The safety rationale from 3.6 is unchanged and still
applies — protection tools still always operate against the original mesh,
never the decimated one — but the user no longer loses an already-computed
result just to check on it.

### 5.4. Error-refinement pass count is now configurable — fixed

`refine_by_error()` gained a `max_iterations: int = 3` parameter (plumbed
through `ProtectionSettings.error_max_iterations` and a new "Max
auto-protection passes" spinbox in the validation panel, range 1–10). A very
strict error threshold that previously always stopped after 3 passes
regardless of whether the mesh was actually within tolerance can now be given
more passes (at the cost of more compute time) or fewer (to cut runtime short
deliberately). New regression test:
[`test_refine_by_error_respects_max_iterations`](tests/test_remesh_core.py).

### 5.5. Cross-platform PyInstaller builds now smoke-tested in CI — improved

Interactive verification is still Windows-only (unchanged — this documentation
session has no macOS/Linux environment to verify against). What changed:
`.github/workflows/release.yml` now launches each freshly built executable
(Windows/macOS/Linux) with `QT_QPA_PLATFORM=offscreen`, waits a few seconds,
and fails the job if the process exited on its own — the standard symptom of
a PyInstaller build missing a native dependency or hitting a broken import at
startup. This doesn't replace clicking through the actual UI, but it catches
the most common way a cross-platform packaged build silently fails.

### 5.6. Non-manifold edges (two distinct causes) and a silent DecimatePro no-op — fixed

Found via the evaluation harness built for the paper's Evaluation section
(`evaluation/`, independent of the shipped app), not the synthetic sphere
sweeps used elsewhere in this document — running the default pipeline over a
19-model real dataset (`evaluation/experiment_validity_checks.py`,
`experiment_engine_comparison.py`) surfaced two problems the synthetic test
suite hadn't hit.

**Symptom 1 — silent no-op:** `vtkDecimatePro` achieved exactly 0% reduction
on 4 of the 19 real models, with no error or warning.

**Root cause:** those 4 models are exported with duplicated per-face vertices
— no two triangles share a vertex index at all (confirmed: their "connected
component" count equals their triangle count). `vtkDecimatePro` collapses
edges via triangle adjacency, so a mesh with no shared edges anywhere gives it
nothing to collapse. `vtkQuadricDecimation` wasn't affected on the same
inputs — it clusters by spatial proximity instead of walking triangle
adjacency.

**Fix** (`_weld_if_fully_disconnected`, [remesh_core.py:114](remesh_core.py:114),
called from `decimate_pro`): detects the pathological case cheaply (every
edge is a boundary edge — `boundary.n_cells == 3 * n_cells`, which only
happens when literally nothing is shared) and welds coincident points
(`PolyData.clean()`) only in that case, before decimating. Verified: all 4
previously-stuck models now reduce normally (31–90%, engine/mesh-dependent);
an already-connected control mesh (Stanford Bunny) was unaffected.

**Symptom 2 — non-manifold edges in the result**, from (it turned out) three
distinct causes, the third found only after being asked to check this section
more carefully (see the Update below):

1. `vtkDecimatePro` on its own, even on the plain unmasked path with **no**
   masking or merging involved at all (Stanford Armadillo: 0 → 7 edges,
   Stanford Bunny: 0 → 1, both from an otherwise clean, watertight source).
   `vtkQuadricDecimation` didn't reproduce this on the same inputs.
2. `.merge(protected_group, decimated_part, merge_points=True)` on the masked
   path (e.g. Bolt Cutters: 0 → 12 edges) — confirmed by instrumenting each
   stage of `decimate_with_mask` in isolation: the count appeared immediately
   after this merge call and was completely unchanged by the subsequent
   `_weld_seam_gaps`/`_stitch_residual_boundary_gaps` calls, so neither of
   those was the source.
3. `_stitch_residual_boundary_gaps`'s own bowtie-vertex fan triangulation, in
   more extreme cases than 1-2 hit — see the Update below.

This isn't a contradiction of 5.1's "`meshing_repair_non_manifold_edges` had
no effect whatsoever" finding — that sweep used synthetic spheres, which
apparently never produced a genuinely non-manifold edge to repair in the
first place, so the filter had nothing to do there. On messy real-world scan
geometry, it does.

**Fix:** `_repair_non_manifold_edges` ([remesh_core.py:497](remesh_core.py:497))
runs `pymeshlab.meshing_repair_non_manifold_edges()` (splitting the offending
vertices to detach the extra fan), wrapped by a cheap pre-check
(`_repair_non_manifold_edges_if_any`, [remesh_core.py:530](remesh_core.py:530))
so the PyMeshLab round-trip is only paid for when `extract_feature_edges`
actually finds a non-manifold edge. Called on **both** paths of
`decimate_with_mask`: unconditionally at the end of the unmasked branch (for
cause 1), and between `_weld_seam_gaps` and `_stitch_residual_boundary_gaps`
on the masked branch (for cause 2) — that specific ordering was tested
against the alternatives and is the only one that fully closes the gap:
repairing *before* `_weld_seam_gaps` gets undone by it (`meshing_close_holes`
recreates the same non-manifold pattern), and repairing *after*
`_stitch_residual_boundary_gaps` leaves the new boundary edges this repair
necessarily opens (splitting an edge shared by 3+ faces can only ever produce
more boundary, never fewer) unclosed for good.

**Verified** across all 19 real models at the pipeline's default settings:
non-manifold edges → 0 in every case that had any (a handful of triangles
lost in the process, no new open boundary left behind in this ordering), and
spot-checked at 99% requested reduction on the three previously-affected
models above with the same clean result.

**Update — a third cause, found by finally re-running an equivalent of 5.1's
original sweep.** Asked to check whether this also closed the "1-2 problem
edges above ~99%" limitation from section 6, a 117-combo sweep (13
resolutions 15-75, 3 mask shapes - polar cap, equatorial band, hemisphere -
reduction in {99%, 99.5%, 99.9%}) found it didn't, fully: 16 combos (13.7%)
still had a non-manifold edge, though — cleanly separating the two failure
modes this time by checking `boundary_edges` and `non_manifold_edges`
independently rather than together — **zero** combos had a leftover boundary
edge (5.1's original limitation, ordinary unmatched boundary edges, is now
fully closed). Instrumenting each stage again pinned down a **third** cause:
`_stitch_residual_boundary_gaps`'s own fan triangulation at a bowtie vertex
can itself produce a genuinely non-manifold edge in these more extreme
cases, not just the non-manifold *vertex* its docstring already anticipated
as an acceptable side effect — the earlier repair pass can't have caught this
since it runs *before* the stitch step exists to introduce it. Fixed with one
more `_repair_non_manifold_edges_if_any` call, now at the very end of
`decimate_with_mask`'s masked branch (still a no-op everywhere else, confirmed
0/19 real models trigger it even at 99%). Verified: all 16 previously-failing
sweep combos now show 0 non-manifold edges, at the cost of a handful of new
boundary edges where it fires (4-12, tiny relative to mesh size, and far
fewer than the "dozens" 5.1 measured before `_weld_seam_gaps`/
`_stitch_residual_boundary_gaps` existed at all) — a deliberate trade, judged
better than leaving genuinely non-manifold topology in the result (see
section 6 for why this is described as narrowing, not eliminating, that
limitation). New regression test:
[`test_decimate_with_mask_stitch_bowtie_does_not_leave_non_manifold_edge`](tests/test_remesh_core.py).

---

## 6. Known limitations (still open)

- The QEM seam-gap fix in 5.1, even after the custom stitcher and 5.6's
  non-manifold repairs, is not completely airtight at the most extreme
  reduction levels (>=99%): a 117-combo re-sweep (13 resolutions, 3 mask
  shapes, reduction in {99%, 99.5%, 99.9%}) found 16 combos (13.7%) still
  leave a handful of problem edges - but, unlike when this was last
  measured, they're now boundary edges (4-12, never more), not non-manifold
  ones: 5.6's fixes bring that count to zero across the entire sweep,
  including these extreme cases. What's left is a purely topological
  T-junction/loop-decomposition issue the greedy direction-pairing heuristic
  can't fully close - the same nature of limitation as originally described,
  just with the non-manifold-edge escape hatch removed rather than left in
  as an unmeasured "narrows but might not close" caveat. Full closure in
  every case would require a proper boundary-constrained decimation
  implementation, judged disproportionate for a defect this rare and this
  small.
- The PyInstaller builds for macOS/Linux are smoke-tested (process starts and
  survives a few seconds) but not interactively verified — see 5.5.
