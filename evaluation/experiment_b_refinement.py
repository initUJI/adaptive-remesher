"""Experiment B - Refinement ablation (real-model dataset).

Question: does the auto-error-protection loop (`refine_by_error`) actually
reduce the worst-case geometric error on real models, and at what cost in
achieved reduction? Matches the Exp_B_Refinement_Ablation sheet layout:
two rows per model, "No refinement" vs "Bidirectional refinement",
both starting from the exact same initial decimation pass (so only the
refinement loop itself is being switched on/off - no manual/UV
protection is involved, isolating this one mechanism).

- No refinement: a single `decimate_with_mask` pass, empty mask.
  Mirrors `run_decimation_pipeline`'s `auto_error_protection=False` branch.
- Bidirectional refinement: same starting mesh, then `refine_by_error`
  with the app's own defaults (`error_threshold_pct=1.0`,
  `max_iterations=3`, see `ProtectionSettings`). Mirrors
  `run_decimation_pipeline`'s `auto_error_protection=True` branch.

Run: python -u -m evaluation.experiment_b_refinement
Writes evaluation/results/experiment_b.{csv,json}
"""

import csv
import json
import os
import time

import numpy as np

from remesh_core import (
    ENGINE_QEM,
    DecimationSettings,
    bidirectional_error_summary,
    decimate_with_mask,
    load_and_prepare_mesh,
    measure_bidirectional_error,
    refine_by_error,
)
from evaluation.dataset_catalog import CATALOG as _FULL_CATALOG, MODELS_ROOT

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
REDUCTION_PCT = 90.0
ERROR_THRESHOLD_PCT = 1.0  # ProtectionSettings default
MAX_ITERATIONS = 3  # ProtectionSettings default

# (category, filename, display name) - only the fields this script needs,
# taken from the shared dataset_catalog so the two scripts can't drift.
CATALOG = [(cat, fname, display) for cat, fname, display, _url, _lic, _notes in _FULL_CATALOG]


def run_model(cat: str, fname: str, display: str, engine: int = ENGINE_QEM) -> list:
    path = os.path.join(MODELS_ROOT, cat, fname)
    source = load_and_prepare_mesh(path, clean=None)
    settings = DecimationSettings(engine=engine, reduction_pct=REDUCTION_PCT)
    empty_mask = np.zeros(source.n_points, dtype=bool)

    t0 = time.perf_counter()
    baseline_zone: dict = {}
    baseline = decimate_with_mask(source, empty_mask, settings, zone_stats=baseline_zone)
    fwd_e, _, rev_e, _ = measure_bidirectional_error(source, baseline)
    baseline_stats = bidirectional_error_summary(fwd_e, rev_e)
    t_baseline = time.perf_counter() - t0

    t0 = time.perf_counter()
    refine_mask = empty_mask.copy()
    refined, _, refined_stats, iterations, added = refine_by_error(
        source, baseline, refine_mask, settings, ERROR_THRESHOLD_PCT,
        MAX_ITERATIONS, zone_stats={},
    )
    t_refined = time.perf_counter() - t0

    rows = [
        {
            "model": display,
            "category": cat,
            "config": "No refinement",
            "input_tris": source.n_cells,
            "output_tris": baseline.n_cells,
            "error_max_fwd": round(baseline_stats.max_fwd, 6),
            "error_mean_fwd": round(baseline_stats.mean_fwd, 6),
            "error_p95_fwd": round(baseline_stats.p95_fwd, 6),
            "error_max_rev": round(baseline_stats.max_rev, 6),
            "error_mean_rev": round(baseline_stats.mean_rev, 6),
            "error_p95_rev": round(baseline_stats.p95_rev, 6),
            "manually_protected_vertices": 0,
            "auto_added_vertices": 0,
            "iterations": 0,
            "runtime_s": round(t_baseline, 3),
        },
        {
            "model": display,
            "category": cat,
            "config": "Bidirectional refinement",
            "input_tris": source.n_cells,
            "output_tris": refined.n_cells,
            "error_max_fwd": round(refined_stats.max_fwd, 6),
            "error_mean_fwd": round(refined_stats.mean_fwd, 6),
            "error_p95_fwd": round(refined_stats.p95_fwd, 6),
            "error_max_rev": round(refined_stats.max_rev, 6),
            "error_mean_rev": round(refined_stats.mean_rev, 6),
            "error_p95_rev": round(refined_stats.p95_rev, 6),
            "manually_protected_vertices": 0,
            "auto_added_vertices": added,
            "iterations": iterations,
            "runtime_s": round(t_refined, 3),
        },
    ]
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
        base, ref = model_rows
        print(
            f"OK   {cat:11} {fname:38} tris={base['input_tris']:>8} "
            f"baseline E_max_fwd/rev={base['error_max_fwd']:.4f}/{base['error_max_rev']:.4f} "
            f"refined E_max_fwd/rev={ref['error_max_fwd']:.4f}/{ref['error_max_rev']:.4f} "
            f"iters={ref['iterations']} added={ref['auto_added_vertices']} ({dt:.1f}s)",
            flush=True,
        )

    csv_path = os.path.join(RESULTS_DIR, "experiment_b.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    json_path = os.path.join(RESULTS_DIR, "experiment_b.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)

    print(f"\nWrote {csv_path}\nWrote {json_path}")


if __name__ == "__main__":
    main()
