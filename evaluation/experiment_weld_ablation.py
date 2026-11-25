"""Weld ablation - what does welding coincident vertices before decimation contribute?

Same configuration as Experiment B (QEM, 90% request, empty mask, threshold
1%, at most 3 refinement iterations), with the weld stage switched off by
replacing `remesh_core._weld_coincident` with the identity. The "weld on"
rows are those of Experiment B (results/experiment_b.json).

Run: python -u -m evaluation.experiment_weld_ablation
Writes evaluation/results/weld_ablation.{csv,json}
"""

import csv
import json
import os

import numpy as np

import remesh_core as rc
from evaluation.dataset_catalog import CATALOG, MODELS_ROOT

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")


def _no_weld(source, mask, keep_uv=True):
    return source, mask


def main():
    weld = rc._weld_coincident
    rows = []
    for cat, fname, display, *_ in CATALOG:
        source = rc.load_and_prepare_mesh(os.path.join(MODELS_ROOT, cat, fname), clean=None)
        settings = rc.DecimationSettings(engine=rc.ENGINE_QEM, reduction_pct=90.0)
        empty = np.zeros(source.n_points, dtype=bool)
        rc._weld_coincident = _no_weld
        try:
            base = rc.decimate_with_mask(source, empty, settings, zone_stats={})
            f, _, r, _ = rc.measure_bidirectional_error(source, base)
            st = rc.bidirectional_error_summary(f, r)
            refined, _, rst, iters, added = rc.refine_by_error(source, base, empty.copy(), settings, 1.0, 3, zone_stats={})
        finally:
            rc._weld_coincident = weld
        for cfg, mesh, s, it, ad in (("No refinement", base, st, 0, 0), ("Bidirectional refinement", refined, rst, iters, added)):
            rows.append({
                "model": display, "category": cat, "config": cfg, "weld": "off",
                "input_tris": source.n_cells, "output_tris": mesh.n_cells,
                "error_max_pct_of_diagonal": round(100 * max(s.max_fwd, s.max_rev) / source.length, 4),
                "iterations": it, "auto_added_vertices": ad,
            })
        print(f"OK {display[:30]:30} noref {rows[-2]['error_max_pct_of_diagonal']:.3f}% ref {rows[-1]['error_max_pct_of_diagonal']:.3f}%", flush=True)
    with open(os.path.join(RESULTS_DIR, "weld_ablation.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(RESULTS_DIR, "weld_ablation.json"), "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
