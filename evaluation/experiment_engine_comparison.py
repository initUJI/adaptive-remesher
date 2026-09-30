"""Engine comparison: vtkDecimatePro vs vtkQuadricDecimation (QEM).

For each real model, decimates once with each engine at the same target
reduction (empty mask, no protection - isolates the engine choice from
every other feature), both with `DecimationSettings`'s own per-engine
defaults (DecimatePro: feature_angle=15, preserve_topology=True,
splitting=True; QEM: volume_preservation=True, use_uv_weight=True,
tcoords_weight=1.0). Reports achieved reduction, bidirectional error, and
runtime per engine, matching the app's actual dispatch in `decimate()`.

Run: python -u -m evaluation.experiment_engine_comparison
Writes evaluation/results/engine_comparison.{csv,json}
"""

import csv
import json
import os
import time

import numpy as np

from remesh_core import (
    ENGINE_DECIMATE_PRO,
    ENGINE_QEM,
    DecimationSettings,
    bidirectional_error_summary,
    decimate_with_mask,
    load_and_prepare_mesh,
    measure_bidirectional_error,
)
from evaluation.dataset_catalog import CATALOG as _FULL_CATALOG, MODELS_ROOT

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
REDUCTION_PCT = 90.0
ENGINES = [(ENGINE_DECIMATE_PRO, "DecimatePro"), (ENGINE_QEM, "QEM")]
CATALOG = [(cat, fname, display) for cat, fname, display, _url, _lic, _notes in _FULL_CATALOG]


def reduction_pct_of(before: int, after: int) -> float:
    return round(100.0 * (1 - after / before) if before else 0.0, 2)


def run_model(cat: str, fname: str, display: str) -> list:
    path = os.path.join(MODELS_ROOT, cat, fname)
    source = load_and_prepare_mesh(path, clean=None)
    empty_mask = np.zeros(source.n_points, dtype=bool)

    rows = []
    for engine, engine_name in ENGINES:
        settings = DecimationSettings(engine=engine, reduction_pct=REDUCTION_PCT)
        t0 = time.perf_counter()
        result = decimate_with_mask(source, empty_mask, settings, zone_stats={})
        fwd_e, _, rev_e, _ = measure_bidirectional_error(source, result)
        stats = bidirectional_error_summary(fwd_e, rev_e)
        dt = time.perf_counter() - t0
        boundary = result.extract_feature_edges(
            boundary_edges=True, non_manifold_edges=False, feature_edges=False, manifold_edges=False
        )
        rows.append({
            "model": display,
            "category": cat,
            "engine": engine_name,
            "input_tris": source.n_cells,
            "output_tris": result.n_cells,
            "reduction_pct": reduction_pct_of(source.n_cells, result.n_cells),
            "error_max_pooled": round(stats.max, 6),
            "error_mean_pooled": round(stats.mean, 6),
            "error_max_fwd": round(stats.max_fwd, 6),
            "error_mean_fwd": round(stats.mean_fwd, 6),
            "error_p95_fwd": round(stats.p95_fwd, 6),
            "error_max_rev": round(stats.max_rev, 6),
            "error_mean_rev": round(stats.mean_rev, 6),
            "error_p95_rev": round(stats.p95_rev, 6),
            "runtime_s": round(dt, 3),
            "protected_vertices": 0,
            "boundary_edges_after": boundary.n_cells,
        })
    return rows


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    rows = []
    for cat, fname, display in CATALOG:
        t0 = time.perf_counter()
        try:
            model_rows = run_model(cat, fname, display)
        except Exception as exc:
            print(f"FAIL {cat:11} {fname:38} {exc}", flush=True)
            continue
        rows.extend(model_rows)
        dt = time.perf_counter() - t0
        pro, qem = model_rows
        print(
            f"OK   {cat:11} {fname:38} tris={pro['input_tris']:>8} "
            f"DecimatePro E_max_fwd/rev={pro['error_max_fwd']:.4f}/{pro['error_max_rev']:.4f} "
            f"QEM E_max_fwd/rev={qem['error_max_fwd']:.4f}/{qem['error_max_rev']:.4f} ({dt:.1f}s)",
            flush=True,
        )

    csv_path = os.path.join(RESULTS_DIR, "engine_comparison.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    json_path = os.path.join(RESULTS_DIR, "engine_comparison.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)

    print(f"\nWrote {csv_path}\nWrote {json_path}")


if __name__ == "__main__":
    main()
