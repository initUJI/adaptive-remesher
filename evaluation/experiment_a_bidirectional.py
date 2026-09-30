"""Experiment A - Bidirectional error detection.

Question: can source->result distance detect missing/altered geometry
that result->source distance misses?

For each synthetic case (see synthetic_cases.py): measures both
directions of geometric error between `source` and `result` (forward =
result->source, reverse = source->result) and reports max/mean/p95 for
each direction plus the pooled max (matching `PipelineResult.error_max`,
i.e. what the app itself would report for this pair). Cases 1-2 use a
literal, hand-built "already decimated" result (clean, unambiguous ground
truth); cases 3-4 run the real (unprotected) decimation engine at an
aggressive reduction, to see whether the engine itself loses the detail.

Run: python -m evaluation.experiment_a_bidirectional
Writes evaluation/results/experiment_a.{csv,json}
"""

import csv
import json
import os

import numpy as np

from evaluation.synthetic_cases import ALL_CASES
from remesh_core import (
    ENGINE_QEM,
    DecimationSettings,
    decimate_with_mask,
    error_summary,
    measure_bidirectional_error,
)

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
REDUCTION_PCT_FOR_ENGINE_CASES = 90.0


def run_case(case, reduction_pct: float = REDUCTION_PCT_FOR_ENGINE_CASES, engine: int = ENGINE_QEM) -> dict:
    if case.handcrafted_result is not None:
        source, result = case.source, case.handcrafted_result
        config = "handcrafted (no decimation engine involved)"
    else:
        settings = DecimationSettings(engine=engine, reduction_pct=reduction_pct)
        empty_mask = np.zeros(case.source.n_points, dtype=bool)
        source = case.source
        result = decimate_with_mask(source, empty_mask, settings)
        engine_name = "QEM" if engine == ENGINE_QEM else "DecimatePro"
        config = f"unprotected decimation, engine={engine_name}, reduction={reduction_pct}%"

    fwd_errors, _, rev_errors, _ = measure_bidirectional_error(source, result)
    fwd_max, fwd_mean, fwd_p95 = error_summary(fwd_errors)
    rev_max, rev_mean, rev_p95 = error_summary(rev_errors)
    pooled_max, pooled_mean, pooled_p95 = error_summary(np.concatenate([fwd_errors, rev_errors]))

    return {
        "case": case.name,
        "description": case.description,
        "config": config,
        "source_tris": source.n_cells,
        "result_tris": result.n_cells,
        "error_max_fwd": round(fwd_max, 6),
        "error_mean_fwd": round(fwd_mean, 6),
        "error_p95_fwd": round(fwd_p95, 6),
        "error_max_rev": round(rev_max, 6),
        "error_mean_rev": round(rev_mean, 6),
        "error_p95_rev": round(rev_p95, 6),
        "error_max_pooled": round(pooled_max, 6),
        "error_mean_pooled": round(pooled_mean, 6),
        "error_p95_pooled": round(pooled_p95, 6),
    }


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    rows = [run_case(case_fn()) for case_fn in ALL_CASES]

    csv_path = os.path.join(RESULTS_DIR, "experiment_a.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    json_path = os.path.join(RESULTS_DIR, "experiment_a.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)

    print(f"{'case':<22} {'E_max_fwd':>10} {'E_max_rev':>10} {'E_max':>10}")
    for row in rows:
        print(
            f"{row['case']:<22} {row['error_max_fwd']:>10.4f} "
            f"{row['error_max_rev']:>10.4f} {row['error_max_pooled']:>10.4f}"
        )
    print(f"\nWrote {csv_path}\nWrote {json_path}")


if __name__ == "__main__":
    main()
