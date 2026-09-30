"""Experiment D - error-threshold sensitivity.

"As the error threshold tightens, expect: more protection -> less
reduction -> less geometric error" (the sheet's own hypothesis). For each
of 5 representative models (see dataset_catalog.REPRESENTATIVE_MODELS),
sweeps `error_threshold_pct` at a fixed 90% requested reduction and
reports what `refine_by_error` actually does: final tris/reduction,
iterations used, vertices auto-protected, and final pooled error
(max/mean/p95).

Run: python -u -m evaluation.experiment_d_threshold_sensitivity
Writes evaluation/results/experiment_d.{csv,json}
"""

import csv
import json
import os

import numpy as np

from remesh_core import (
    ENGINE_QEM,
    DecimationSettings,
    decimate_with_mask,
    load_and_prepare_mesh,
    refine_by_error,
)
from evaluation.dataset_catalog import MODELS_ROOT, REPRESENTATIVE_MODELS

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
REDUCTION_PCT = 90.0
THRESHOLDS_PCT = [0.25, 0.5, 1.0, 2.0, 5.0]
MAX_ITERATIONS = 3


def run_one(cat: str, fname: str, display: str, threshold_pct: float) -> dict:
    path = os.path.join(MODELS_ROOT, cat, fname)
    source = load_and_prepare_mesh(path, clean=None)
    settings = DecimationSettings(engine=ENGINE_QEM, reduction_pct=REDUCTION_PCT)
    empty_mask = np.zeros(source.n_points, dtype=bool)

    baseline = decimate_with_mask(source, empty_mask, settings, zone_stats={})
    mask = empty_mask.copy()
    result, _, stats, iterations, added = refine_by_error(
        source, baseline, mask, settings, threshold_pct, MAX_ITERATIONS, zone_stats={},
    )
    return {
        "model": display,
        "threshold_pct": threshold_pct,
        "source_tris": source.n_cells,
        "result_tris": result.n_cells,
        "iterations": iterations,
        "points_added": added,
        "error_max_final": round(stats.max, 6),
        "error_mean_final": round(stats.mean, 6),
        "error_p95_final": round(stats.p95, 6),
    }


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    rows = []
    for cat, fname, display in REPRESENTATIVE_MODELS:
        for threshold_pct in THRESHOLDS_PCT:
            try:
                row = run_one(cat, fname, display, threshold_pct)
            except Exception as exc:
                print(f"FAIL {display} tau={threshold_pct} {exc}", flush=True)
                continue
            rows.append(row)
            print(
                f"{display:42} tau={threshold_pct:>5.2f}%  "
                f"tris={row['source_tris']:>8}->{row['result_tris']:<8} "
                f"iters={row['iterations']} added={row['points_added']:>6} "
                f"E_max={row['error_max_final']:.4f}",
                flush=True,
            )

    csv_path = os.path.join(RESULTS_DIR, "experiment_d.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    json_path = os.path.join(RESULTS_DIR, "experiment_d.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
    print(f"\nWrote {csv_path}\nWrote {json_path}")


if __name__ == "__main__":
    main()
