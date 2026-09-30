"""Experiment E - requested-reduction sensitivity.

"The error-guided refinement never changes the reduction target; it only
grows protection. Global reduction can end up well below the request;
unprotected-zone reduction should stay close to it" (the sheet's own
hypothesis). For each of 5 representative models (see
dataset_catalog.REPRESENTATIVE_MODELS), sweeps the requested reduction at
a fixed 1% error threshold and reports the whole-mesh reduction actually
achieved vs. the unprotected-zone-only reduction (`zone_stats`, see
`decimate_with_mask`/`refine_by_error` in remesh_core.py), plus final
pooled max error and iterations.

Run: python -u -m evaluation.experiment_e_reduction_sensitivity
Writes evaluation/results/experiment_e.{csv,json}
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
REDUCTIONS_PCT = [50.0, 70.0, 80.0, 90.0, 95.0]
ERROR_THRESHOLD_PCT = 1.0
MAX_ITERATIONS = 3


def run_one(cat: str, fname: str, display: str, reduction_pct: float) -> dict:
    path = os.path.join(MODELS_ROOT, cat, fname)
    source = load_and_prepare_mesh(path, clean=None)
    settings = DecimationSettings(engine=ENGINE_QEM, reduction_pct=reduction_pct)
    empty_mask = np.zeros(source.n_points, dtype=bool)

    zone_stats: dict = {}
    baseline = decimate_with_mask(source, empty_mask, settings, zone_stats=zone_stats)
    mask = empty_mask.copy()
    result, _, stats, iterations, _added = refine_by_error(
        source, baseline, mask, settings, ERROR_THRESHOLD_PCT, MAX_ITERATIONS, zone_stats=zone_stats,
    )
    return {
        "model": display,
        "requested_reduction_pct": reduction_pct,
        "source_tris": source.n_cells,
        "result_tris": result.n_cells,
        "unprotected_tris_before": zone_stats.get("unprotected_tris_before", 0),
        "unprotected_tris_after": zone_stats.get("unprotected_tris_after", 0),
        "error_max": round(stats.max, 6),
        "iterations": iterations,
    }


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    rows = []
    for cat, fname, display in REPRESENTATIVE_MODELS:
        for reduction_pct in REDUCTIONS_PCT:
            try:
                row = run_one(cat, fname, display, reduction_pct)
            except Exception as exc:
                print(f"FAIL {display} reduction={reduction_pct} {exc}", flush=True)
                continue
            rows.append(row)
            global_pct = 100.0 * (1 - row["result_tris"] / row["source_tris"])
            zone_pct = (
                100.0 * (1 - row["unprotected_tris_after"] / row["unprotected_tris_before"])
                if row["unprotected_tris_before"] else 0.0
            )
            print(
                f"{display:42} req={reduction_pct:>5.1f}%  "
                f"global={global_pct:>5.1f}% zone={zone_pct:>5.1f}% "
                f"iters={row['iterations']} E_max={row['error_max']:.4f}",
                flush=True,
            )

    csv_path = os.path.join(RESULTS_DIR, "experiment_e.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    json_path = os.path.join(RESULTS_DIR, "experiment_e.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
    print(f"\nWrote {csv_path}\nWrote {json_path}")


if __name__ == "__main__":
    main()
