"""Texture fidelity - how well does the result reproduce the source's appearance?

For every dataset mesh that carries UV and a base-colour image, the source is
decimated (QEM, 90% request, 1% error limit, three refinement iterations,
lowering of the reduction on) and the result gets its UV back by
`transfer_texture_coords`, as the application does. `materials.texture_error`
then colours 20,000 area-weighted source points with the image at the source
UV and at the UV of the closest point of the result, and reports the colour
difference as a percentage of the full RGB range. Configurations:
UV seams protected, UV seams free, and the application's automatic policy
(`should_protect_uv_seams`); MeshLab's quadric decimation on the welded input
(with the same UV transfer) is the baseline at 90%.

Run: python -u -m evaluation.experiment_texture_fidelity
Writes evaluation/results/texture_fidelity.{csv,json}
"""

import csv
import json
import os

import numpy as np

import remesh_core as rc
from materials import MaterialMaps, bake_material_maps, load_material_maps, map_errors, reparametrize, xatlas_available
from evaluation.dataset_catalog import CATALOG, MODELS_ROOT
from evaluation.experiment_baselines import meshlab_qem

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")


def _row(display, cat, config, source, result, maps):
    errors = map_errors(source, result, maps)
    err = errors["base"]
    f, _, r, _ = rc.measure_bidirectional_error(source, result)
    st = rc.bidirectional_error_summary(f, r)
    row = {
        "model": display, "category": cat, "config": config, "input_tris": source.n_cells,
        "output_tris": result.n_cells, "reduction_pct": round(100 * (1 - result.n_cells / source.n_cells), 2),
        "geometry_error_pct": round(100 * max(st.max_fwd, st.max_rev) / source.length, 4),
        "texture_error_mean_pct": round(err["mean"], 4), "texture_error_p95_pct": round(err["p95"], 4),
        "texture_error_max_pct": round(err["max"], 4), "share_above_10pct": round(err["share_above_10"], 4),
        "geometry_floor_mean_pct": round(err["floor_mean"], 4), "geometry_floor_p95_pct": round(err["floor_p95"], 4),
    }
    for name, e in errors.items():  # base colour is above; the other maps in their own unit
        if name != "base":
            unit = "deg" if name == "normal" else "pct"
            row[f"{name}_error_mean_{unit}"] = round(e["mean"], 4)
            row[f"{name}_error_p95_{unit}"] = round(e["p95"], 4)
            row[f"{name}_floor_mean_{unit}"] = round(e["floor_mean"], 4)
    return row


def main():
    rows = []
    for cat, fname, display, *_ in CATALOG:
        path = os.path.join(MODELS_ROOT, cat, fname)
        source = rc.load_and_prepare_mesh(path, clean=None)
        maps = load_material_maps(path)
        if not rc.mesh_has_texture_coords(source) or maps is None or maps.base is None:
            print(f"skip {display[:40]} (no UV or base colour image)", flush=True)
            continue
        auto = rc.should_protect_uv_seams(source)
        decimation = rc.DecimationSettings(engine=rc.ENGINE_QEM, reduction_pct=90.0)
        for config, seams in (("Seams protected", True), ("Seams free", False)):
            protection = rc.ProtectionSettings(protect_uv_seams=seams)
            res = rc.run_decimation_pipeline(source, decimation, protection)
            rows.append(_row(display, cat, config, source, rc.transfer_texture_coords(source, res.mesh), maps))
            rows[-1]["seam_share_pct"] = round(100 * rc.seam_vertex_fraction(source), 2)
            rows[-1]["automatic_policy_protects"] = auto
            if not seams:
                rows.append(_row(display, cat, 'Seams free, cut at seams', source, rc.transfer_texture_coords(source, res.mesh, cut_seams=True), maps))
                rows[-1]['seam_share_pct'] = rows[-2]['seam_share_pct']
                rows[-1]['automatic_policy_protects'] = auto
            if not seams and xatlas_available():  # new atlas baked from the source
                low = reparametrize(res.mesh, size=2048)
                baked = bake_material_maps(source, low, maps, size=2048)
                low.active_texture_coordinates = low.active_texture_coordinates
                rows.append(_row(display, cat, "Seams free, re-baked (new atlas)", source, low, baked))
                rows[-1]["seam_share_pct"] = rows[-2]["seam_share_pct"]
                rows[-1]["automatic_policy_protects"] = auto
        welded, _ = rc._weld_coincident(source, np.zeros(source.n_points, bool), keep_uv=False)
        ml = rc.transfer_texture_coords(source, meshlab_qem(welded, 90.0, False))
        rows.append(_row(display, cat, "MeshLab (welded), UV transferred", source, ml, maps))
        rows[-1]["seam_share_pct"] = rows[-2]["seam_share_pct"]
        rows[-1]["automatic_policy_protects"] = auto
        print(" | ".join(f"{r['config'][:14]} tex {r['texture_error_mean_pct']:.2f}% red {r['reduction_pct']:.0f}%" for r in rows[-5:]), display[:30], flush=True)
    keys = sorted({k for r in rows for k in r}, key=lambda k: list(rows[0]).index(k) if k in rows[0] else 99)
    with open(os.path.join(RESULTS_DIR, "texture_fidelity.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(RESULTS_DIR, "texture_fidelity.json"), "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
