"""Validity checks - post-simplification mesh integrity.

For each real model, compares raw mesh integrity ("Before") against the
result of the app's actual default pipeline ("After": `DecimationSettings`
defaults - engine=DecimatePro, 90% reduction - and `ProtectionSettings`
defaults - UV seams protected, auto-error-protection on). Checks the
things a bad decimation or a failed boundary-gap repair could introduce:
duplicate vertices, open boundary edges, non-manifold edges, degenerate
(near-zero-area) faces, connected-component count, and watertightness.
Important because `_stitch_residual_boundary_gaps` (remesh_core.py)
fan-triangulates leftover seam gaps by hand as a last resort - this is
where a bug in that heuristic would show up (a non-manifold edge or a
degenerate sliver it introduced, not one already in the source).

CAVEAT surfaced by this run, not assumed going in: for the 4 models whose
export duplicates vertices per face (no two triangles share a vertex -
see gather_dataset_stats.py's NON_WELDED_NOTE: shark, vegetable garden,
electrocardiograph, heart), "After" is byte-identical to "Before" -
DecimatePro achieves exactly 0% reduction on them (confirmed independently
in experiment_engine_comparison.py's results: QEM reduces the same 4
meshes to the requested 90% without trouble). vtkDecimatePro collapses
edges via triangle adjacency, so a mesh with no shared edges between
triangles gives it nothing to collapse; vtkQuadricDecimation clusters by
proximity instead and isn't affected the same way. Their "After" row here
therefore validates the *pipeline's default engine choice*, not a
representative decimation result, for those 4 models specifically.

Run: python -u -m evaluation.experiment_validity_checks
Writes evaluation/results/validity_checks.{csv,json}
"""

import csv
import json
import os

import numpy as np
from scipy.spatial import cKDTree

from remesh_core import (
    DecimationSettings,
    ProtectionSettings,
    load_and_prepare_mesh,
    run_decimation_pipeline,
)
from evaluation.dataset_catalog import CATALOG, MODELS_ROOT
from evaluation.gather_dataset_stats import count_connected_components

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
REDUCTION_PCT = 90.0


def count_duplicate_vertices(mesh) -> int:
    tol = 1e-8 * max(mesh.length, 1e-9)
    tree = cKDTree(mesh.points)
    pairs = tree.query_pairs(r=tol, output_type="ndarray")
    if pairs.size == 0:
        return 0
    return int(np.unique(pairs.ravel()).size)


def count_degenerate_faces(mesh) -> int:
    if mesh.n_cells == 0:
        return 0
    sized = mesh.compute_cell_sizes(length=False, area=True, volume=False)
    areas = sized.cell_data["Area"]
    eps = 1e-12 * max(mesh.length, 1e-9) ** 2
    return int(np.sum(areas <= eps))


def mesh_metrics(mesh) -> dict:
    boundary = mesh.extract_feature_edges(
        boundary_edges=True, non_manifold_edges=False, feature_edges=False, manifold_edges=False
    )
    non_manifold = mesh.extract_feature_edges(
        boundary_edges=False, non_manifold_edges=True, feature_edges=False, manifold_edges=False
    )
    return {
        "faces": mesh.n_cells,
        "vertices": mesh.n_points,
        "duplicate_vertices": count_duplicate_vertices(mesh),
        "boundary_edges": boundary.n_cells,
        "non_manifold_edges": non_manifold.n_cells,
        "degenerate_faces": count_degenerate_faces(mesh),
        "connected_components": count_connected_components(mesh),
    }


def run_model(cat: str, fname: str, display: str) -> list:
    path = os.path.join(MODELS_ROOT, cat, fname)
    source = load_and_prepare_mesh(path, clean=None)
    before = mesh_metrics(source)

    result = run_decimation_pipeline(
        source, DecimationSettings(reduction_pct=REDUCTION_PCT), ProtectionSettings()
    )
    after = mesh_metrics(result.mesh)

    rows = []
    for config, metrics in (("Before (raw load)", before), ("After (default pipeline, 90%)", after)):
        rows.append({"model": display, "category": cat, "config": config, **metrics})
    return rows


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    rows = []
    for cat, fname, display, _url, _lic, _notes in CATALOG:
        try:
            model_rows = run_model(cat, fname, display)
        except Exception as exc:
            print(f"FAIL {cat:11} {fname:38} {exc}", flush=True)
            continue
        rows.extend(model_rows)
        before, after = model_rows
        print(
            f"OK   {cat:11} {fname:38} "
            f"before(bnd={before['boundary_edges']:>4} nm={before['non_manifold_edges']:>3} deg={before['degenerate_faces']:>3}) "
            f"after(bnd={after['boundary_edges']:>4} nm={after['non_manifold_edges']:>3} deg={after['degenerate_faces']:>3})",
            flush=True,
        )

    csv_path = os.path.join(RESULTS_DIR, "validity_checks.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    json_path = os.path.join(RESULTS_DIR, "validity_checks.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
    print(f"\nWrote {csv_path}\nWrote {json_path}")


if __name__ == "__main__":
    main()
