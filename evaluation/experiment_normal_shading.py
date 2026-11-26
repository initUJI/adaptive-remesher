"""Shading-normal fidelity - what does baking a normal map for the result add?

For every dataset mesh with a normal map, the source is decimated with the default
pipeline (QEM, 90% request, 1% error limit, three refinement iterations, lowering of
the reduction on), the texture coordinates are transferred back (faces cut at the seams
when the automatic policy says so) and `materials.shading_normal_error` measures the
angle between the shading normal of the source (vertex normals and its normal map) and
that of the result at 20,000 area-weighted points, for the result carrying: the source's
normal map on the transferred UV, a normal map baked for it (1024 pixels, the
application's default), and no normal map. `best_normal_map` is the application's choice
between the first two.

Run: python -u -m evaluation.experiment_normal_shading
Writes evaluation/results/normal_shading.{csv,json}
"""

import csv
import json
import os

import remesh_core as rc
from materials import best_normal_map, load_material_maps, shading_normal_error, bake_normal_map
from evaluation.dataset_catalog import CATALOG, MODELS_ROOT

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
SIZE = 1024


def main():
    rows = []
    for cat, fname, display, *_ in CATALOG:
        path = os.path.join(MODELS_ROOT, cat, fname)
        maps = load_material_maps(path)
        if maps is None or maps.normal is None:
            continue
        source = rc.load_and_prepare_mesh(path, clean=None)
        res = rc.run_decimation_pipeline(
            source, rc.DecimationSettings(engine=rc.ENGINE_QEM, reduction_pct=90.0),
            rc.ProtectionSettings(protect_uv_seams=False),
        )
        low = rc.transfer_texture_coords(source, res.mesh, cut_seams=rc.should_cut_uv_seams(source))
        size = min(SIZE, maps.normal.shape[0])
        baked = bake_normal_map(source, low, size=size, high_normal_map=maps.normal, high_normal_scale=maps.factors.get("normalScale") or 1.0)
        err = shading_normal_error(source, low, maps.normal, {"carried": maps.normal, "baked": baked, "none": None})
        chosen, info = best_normal_map(source, low, maps, size)
        rows.append({
            "model": display, "category": cat, "output_tris": low.n_cells, "bake_size": size,
            "carried_mean_deg": round(err["carried"]["mean"], 3), "carried_p95_deg": round(err["carried"]["p95"], 3),
            "baked_mean_deg": round(err["baked"]["mean"], 3), "baked_p95_deg": round(err["baked"]["p95"], 3),
            "none_mean_deg": round(err["none"]["mean"], 3), "none_p95_deg": round(err["none"]["p95"], 3),
            "application_keeps": "baked" if chosen is not None else "carried",
        })
        r = rows[-1]
        print(f"{display[:28]:28} carried {r['carried_mean_deg']:6.2f}  baked {r['baked_mean_deg']:6.2f}  none {r['none_mean_deg']:6.2f}  keeps {r['application_keeps']}", flush=True)
    with open(os.path.join(RESULTS_DIR, "normal_shading.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(RESULTS_DIR, "normal_shading.json"), "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
