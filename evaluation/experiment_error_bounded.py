"""Error-bounded reduction - what does lowering the reduction add to refinement?

Same configuration as Experiment B (QEM, 90% request, empty mask, threshold
1%, at most 3 refinement iterations) run through `run_decimation_pipeline`
with `lower_reduction_if_needed` on (the default): when protecting violating
zones fails to reach the threshold, or reaches it by keeping most of the mesh,
the pipeline also tries lower requested reductions in steps of 5 points and
keeps the result with the higher reduction that meets the threshold.
Refinement-only rows are those of Experiment B.

Run: python -u -m evaluation.experiment_error_bounded [qem|pro] [reduction]
Writes evaluation/results/error_bounded.{csv,json} (QEM, 90%) or
error_bounded_<engine>_<reduction>.{csv,json} for another engine or request.
"""

import csv
import json
import os
import sys
import time

import numpy as np

import remesh_core as rc
from evaluation.dataset_catalog import CATALOG, MODELS_ROOT

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")


def main(engine_name: str = "qem", reduction: float = 90.0):
    engine = rc.ENGINE_QEM if engine_name == "qem" else rc.ENGINE_DECIMATE_PRO
    tag = "error_bounded" if (engine_name, reduction) == ("qem", 90.0) else f"error_bounded_{engine_name}_{int(reduction)}"
    rows = []
    for cat, fname, display, *_ in CATALOG:
        source = rc.load_and_prepare_mesh(os.path.join(MODELS_ROOT, cat, fname), clean=None)
        decimation = rc.DecimationSettings(engine=engine, reduction_pct=reduction)
        protection = rc.ProtectionSettings(
            protect_uv_seams=False, auto_error_protection=True, error_threshold_pct=1.0,
            error_max_iterations=3, lower_reduction_if_needed=True,
        )
        t0 = time.perf_counter()
        res = rc.run_decimation_pipeline(source, decimation, protection)
        dt = time.perf_counter() - t0
        rows.append({
            "model": display, "category": cat, "input_tris": source.n_cells, "output_tris": res.result_tris,
            "reduction_pct": round(res.reduction_real_pct, 2),
            "error_max_pct_of_diagonal": round(100 * res.error_max / source.length, 4),
            "reduction_lowered_to_pct": res.reduction_lowered_to_pct,
            "refine_iterations": res.error_refine_iterations, "auto_added_vertices": res.error_refine_added_points,
            "runtime_s": round(dt, 3),
        })
        r = rows[-1]
        print(f"OK {display[:28]:28} red {r['reduction_pct']:5.1f}% err {r['error_max_pct_of_diagonal']:.3f}% lowered {r['reduction_lowered_to_pct']} ({dt:.1f}s)", flush=True)
    with open(os.path.join(RESULTS_DIR, tag + ".csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(RESULTS_DIR, tag + ".json"), "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "qem", float(sys.argv[2]) if len(sys.argv) > 2 else 90.0)
