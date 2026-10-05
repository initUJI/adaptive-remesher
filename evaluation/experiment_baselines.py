"""Baseline comparison on the real-model dataset.

Runs two independent simplification baselines on the same meshes and
scores them with the same bidirectional geometric error as Adaptive
Remesher, so that every number comes from one measure:

- MeshLab: PyMeshLab's Quadric Edge Collapse Decimation, with its default
  parameters (``MeshLab QEM``) and with boundary preservation switched on
  (``MeshLab QEM (boundary)``).
- fast-simplification: the Python quadric simplifier used by PyVista.

(meshoptimizer is not included: it has no wheel for this Python version
and could not be built here without a C++ toolchain.)

Two comparisons per model:

1. ``fixed``: every method is asked for the same reduction
   (``REDUCTION_PCT``), i.e. the setting used by Experiment B.
2. ``matched``: every baseline is asked for the reduction that Adaptive
   Remesher's refined run actually achieved on that model (read from
   ``MATCHED_FROM``), so the errors can be compared at equal reduction.

Adaptive Remesher's own numbers (unprotected 90% and refined) are taken
from Experiment B's results, not recomputed. Error is the larger of the
two directional maxima, as a percentage of the source bounding-box
diagonal (the app's own scale for its threshold).

Run: python -u -m evaluation.experiment_baselines
Writes evaluation/results/baselines.{csv,json}
"""

import csv
import json
import os
import time

import numpy as np
import pymeshlab
import pyvista as pv

from remesh_core import (
    bidirectional_error_summary,
    load_and_prepare_mesh,
    measure_bidirectional_error,
)
from evaluation.dataset_catalog import CATALOG as _FULL_CATALOG, MODELS_ROOT

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
MATCHED_FROM = os.path.join(RESULTS_DIR, "experiment_b.json")
REDUCTION_PCT = 90.0
CATALOG = [(cat, fname, display) for cat, fname, display, _url, _lic, _notes in _FULL_CATALOG]


def _faces_to_polydata(points: np.ndarray, faces: np.ndarray) -> pv.PolyData:
    poly = np.hstack([np.full((faces.shape[0], 1), 3), faces]).astype(np.int64)
    return pv.PolyData(np.asarray(points, dtype=float), poly)


def meshlab_qem(source: pv.PolyData, reduction_pct: float, preserve_boundary: bool) -> pv.PolyData:
    target = max(4, int(round(source.n_cells * (1 - reduction_pct / 100.0))))
    ms = pymeshlab.MeshSet()
    ms.add_mesh(pymeshlab.Mesh(vertex_matrix=source.points, face_matrix=source.regular_faces))
    ms.meshing_decimation_quadric_edge_collapse(
        targetfacenum=target, preserveboundary=preserve_boundary
    )
    mesh = ms.current_mesh()
    return _faces_to_polydata(mesh.vertex_matrix(), mesh.face_matrix())


def fast_simplification_qem(source: pv.PolyData, reduction_pct: float) -> pv.PolyData:
    import fast_simplification

    points, faces = fast_simplification.simplify(
        source.points, source.regular_faces, target_reduction=reduction_pct / 100.0
    )
    return _faces_to_polydata(points, faces)


METHODS = [
    ("MeshLab QEM", lambda m, r: meshlab_qem(m, r, False)),
    ("MeshLab QEM (boundary)", lambda m, r: meshlab_qem(m, r, True)),
    ("fast-simplification", fast_simplification_qem),
]


def boundary_edges(mesh: pv.PolyData) -> int:
    return mesh.extract_feature_edges(
        boundary_edges=True, non_manifold_edges=False, feature_edges=False, manifold_edges=False
    ).n_cells


def score(source: pv.PolyData, result: pv.PolyData) -> dict:
    fwd_e, _, rev_e, _ = measure_bidirectional_error(source, result)
    stats = bidirectional_error_summary(fwd_e, rev_e)
    return {
        "output_tris": result.n_cells,
        "reduction_pct": round(100.0 * (1 - result.n_cells / source.n_cells), 2),
        "error_max_fwd": round(stats.max_fwd, 6),
        "error_max_rev": round(stats.max_rev, 6),
        "error_max_pct_of_diagonal": round(100.0 * max(stats.max_fwd, stats.max_rev) / source.length, 4),
        "boundary_edges": boundary_edges(result),
    }


def run_model(cat: str, fname: str, display: str, matched_pct: float) -> list:
    source = load_and_prepare_mesh(os.path.join(MODELS_ROOT, cat, fname), clean=None)
    rows = []
    for comparison, pct in (("fixed", REDUCTION_PCT), ("matched", matched_pct)):
        for name, fn in METHODS:
            row = {
                "model": display,
                "category": cat,
                "comparison": comparison,
                "method": name,
                "input_tris": source.n_cells,
                "requested_reduction_pct": round(pct, 2),
            }
            t0 = time.perf_counter()
            try:
                result = fn(source, pct)
                row["runtime_s"] = round(time.perf_counter() - t0, 3)
                row.update(score(source, result))
            except Exception as exc:  # keep going; record the failure
                row["error"] = f"{type(exc).__name__}: {exc}"
            rows.append(row)
    return rows


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(MATCHED_FROM, encoding="utf-8") as f:
        exp_b = json.load(f)
    refined = {
        r["model"]: 100.0 * (1 - r["output_tris"] / r["input_tris"])
        for r in exp_b
        if r["config"] != "No refinement"
    }
    rows = []
    for cat, fname, display in CATALOG:
        matched = refined.get(display)
        if matched is None:
            print(f"SKIP {display}: no refined run in {MATCHED_FROM}", flush=True)
            continue
        # Never ask a baseline for a negative reduction.
        matched = max(0.0, min(matched, REDUCTION_PCT))
        t0 = time.perf_counter()
        model_rows = run_model(cat, fname, display, matched if matched > 0 else 1.0)
        rows.extend(model_rows)
        print(f"OK   {cat:11} {fname:38} ({time.perf_counter() - t0:.1f}s)", flush=True)

    csv_path = os.path.join(RESULTS_DIR, "baselines.csv")
    keys = sorted({k for r in rows for k in r}, key=lambda k: list(rows[0]).index(k) if k in rows[0] else 99)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)
    json_path = os.path.join(RESULTS_DIR, "baselines.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
    print(f"\nWrote {csv_path}\nWrote {json_path}")


if __name__ == "__main__":
    main()
