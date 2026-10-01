"""Runtime overhead of the default pipeline (DecimatePro, 90% reduction, UV seams
protected, auto-error-protection on - `DecimationSettings`/
`ProtectionSettings` defaults) on each real model.

Only two stages are cleanly separable from OUTSIDE remesh_core.py without
editing it (this harness is deliberately independent of the shipped app,
see evaluation/README.md): the single initial `decimate_with_mask` call
(T_Decimation), and the total wall time of the whole run (T_Total).
Everything the iterative refinement loop does - error measurement AND
every repeat decimation AND the boundary-gap repair inside each of those
- happens inside one already-black-box call to `refine_by_error` and
can't be teased apart without instrumenting remesh_core.py itself (which
the Legend sheet's own note anticipates as optional: "T_Error"/"T_Repair"
are left blank here rather than a fabricated split; T_Other's formula
(`T_Total - SUM(T_Decimation:T_Repair)`) therefore reports "everything
the refinement loop took", not a pure measurement-overhead residual - do
not read it as the latter in the paper.

Run: python -u -m evaluation.experiment_runtime
Writes evaluation/results/runtime.{csv,json}
"""

import csv
import json
import os
import time

import numpy as np

from remesh_core import (
    DecimationSettings,
    ProtectionSettings,
    decimate_with_mask,
    load_and_prepare_mesh,
    mesh_has_texture_coords,
    refine_by_error,
    uv_seam_point_mask,
)
from evaluation.dataset_catalog import CATALOG, MODELS_ROOT

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
REDUCTION_PCT = 90.0
CONFIG_NAME = f"Default pipeline (DecimatePro, {REDUCTION_PCT:.0f}%, UV+auto-error protection)"


def run_model(cat: str, fname: str, display: str) -> dict:
    path = os.path.join(MODELS_ROOT, cat, fname)
    source = load_and_prepare_mesh(path, clean=None)
    decimation = DecimationSettings(reduction_pct=REDUCTION_PCT)
    protection = ProtectionSettings()

    t0 = time.perf_counter()
    mask = np.zeros(source.n_points, dtype=bool)
    if protection.protect_uv_seams and mesh_has_texture_coords(source):
        mask |= uv_seam_point_mask(source)

    t_decimate_start = time.perf_counter()
    zone_stats: dict = {}
    initial_result = decimate_with_mask(source, mask, decimation, zone_stats=zone_stats)
    t_decimation = time.perf_counter() - t_decimate_start

    t_refine_start = time.perf_counter()
    result, _, _, iterations, added = refine_by_error(
        source, initial_result, mask.copy(), decimation,
        protection.error_threshold_pct, protection.error_max_iterations, zone_stats=zone_stats,
    )
    t_refine_loop = time.perf_counter() - t_refine_start
    t_total = time.perf_counter() - t0

    return {
        "model": display,
        "source_tris": source.n_cells,
        "config": CONFIG_NAME,
        "t_total_s": round(t_total, 4),
        "t_decimation_s": round(t_decimation, 4),
        "t_error_s": None,
        "t_repair_s": None,
        "t_refine_loop_s_informational": round(t_refine_loop, 4),
        "iterations": iterations,
        "auto_added_vertices": added,
        "result_tris": result.n_cells,
    }


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    rows = []
    for cat, fname, display, _url, _lic, _notes in CATALOG:
        try:
            row = run_model(cat, fname, display)
        except Exception as exc:
            print(f"FAIL {cat:11} {fname:38} {exc}", flush=True)
            continue
        rows.append(row)
        print(
            f"OK   {cat:11} {fname:38} tris={row['source_tris']:>8} "
            f"T_total={row['t_total_s']:>7.3f}s T_decimation={row['t_decimation_s']:>7.3f}s "
            f"T_refine_loop={row['t_refine_loop_s_informational']:>7.3f}s iters={row['iterations']}",
            flush=True,
        )

    csv_path = os.path.join(RESULTS_DIR, "runtime.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    json_path = os.path.join(RESULTS_DIR, "runtime.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
    print(f"\nWrote {csv_path}\nWrote {json_path}")


if __name__ == "__main__":
    main()
