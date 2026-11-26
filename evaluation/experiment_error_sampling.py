"""Sampling of the error - how much does the maximum error miss?

The geometric error is a maximum over sample points: the application samples the
faces of the result at 7 points each and those of the source at their centers. Here
the result of the unrefined QEM decimation at 90% (empty mask, as in Experiment B) is
measured again with a lattice of barycentric points per face, as a reference that
does not share the application's points: about 105 points per result face and, for
meshes of up to 40,000 source faces, 28 per source face. The reference is not exact
either (it is still a sampled maximum), so its ratio to the application's value says
how much the application's maximum misses, not whether it is exact.

Run: python -u -m evaluation.experiment_error_sampling
Writes evaluation/results/error_sampling.{csv,json}
"""

import csv
import json
import os

import numpy as np
import vtk
import pyvista as pv

import remesh_core as rc
from evaluation.dataset_catalog import CATALOG, MODELS_ROOT

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
RESULT_LATTICE = 13  # (13 + 1)(13 + 2) / 2 = 105 points
SOURCE_LATTICE = 6  # 28 points
SOURCE_LIMIT = 40_000


def lattice(n: int) -> np.ndarray:
    """Barycentric weights of the points of a triangular lattice with n subdivisions."""
    return np.array([[(n - i - j) / n, i / n, j / n] for i in range(n + 1) for j in range(n + 1 - i)])


def max_error(mesh: pv.PolyData, distance, weights: np.ndarray, chunk: int = 200_000) -> tuple:
    """Largest and 95th-percentile distance from the points `weights` of every face of `mesh`
    to the surface behind `distance` (per-face maxima for the percentile)."""
    tri = np.asarray(mesh.regular_faces)
    corners = np.asarray(mesh.points, dtype=float)[tri]
    per_face = np.empty(len(tri))
    step = max(1, chunk // len(weights))
    for start in range(0, len(tri), step):
        pts = np.einsum("sk,nkd->nsd", weights, corners[start : start + step]).reshape(-1, 3)
        out = vtk.vtkDoubleArray()
        distance.FunctionValue(pv.convert_array(pts), out)
        err = np.abs(pv.convert_array(out)).reshape(-1, len(weights))
        per_face[start : start + step] = err.max(axis=1)
    return float(per_face.max()), float(np.percentile(per_face, 95))


def main():
    rows = []
    centre = np.array([[1 / 3, 1 / 3, 1 / 3]])
    for cat, fname, display, *_ in CATALOG:
        source = rc.load_and_prepare_mesh(os.path.join(MODELS_ROOT, cat, fname), clean=None)
        result = rc.decimate_with_mask(
            source, np.zeros(source.n_points, bool), rc.DecimationSettings(engine=rc.ENGINE_QEM, reduction_pct=90.0), zone_stats={}
        )
        to_source, to_result = vtk.vtkImplicitPolyDataDistance(), vtk.vtkImplicitPolyDataDistance()
        to_source.SetInput(source)
        to_result.SetInput(result)
        scale = 100.0 / source.length
        res_centre = max_error(result, to_source, centre)[0]
        res_app = max_error(result, to_source, rc.DENSE_SAMPLES)[0]
        res_ref = max_error(result, to_source, lattice(RESULT_LATTICE))[0]
        row = {
            "model": display, "category": cat, "source_tris": source.n_cells, "result_tris": result.n_cells,
            "result_to_source_centroid_pct": round(scale * res_centre, 4),
            "result_to_source_application_pct": round(scale * res_app, 4),
            "result_to_source_reference_pct": round(scale * res_ref, 4),
        }
        src_app = max_error(source, to_result, centre)[0]
        row["source_to_result_application_pct"] = round(scale * src_app, 4)
        if source.n_cells <= SOURCE_LIMIT:
            row["source_to_result_reference_pct"] = round(scale * max_error(source, to_result, lattice(SOURCE_LATTICE))[0], 4)
            ref = max(res_ref, max_error(source, to_result, lattice(SOURCE_LATTICE))[0])
        else:
            ref = None
        app = max(res_app, src_app)
        row["application_max_pct"] = round(scale * app, 4)
        row["reference_max_pct"] = round(scale * ref, 4) if ref is not None else None
        row["ratio_application_to_reference"] = round(app / ref, 4) if ref else None
        rows.append(row)
        print(f"{display[:28]:28} app {row['application_max_pct']:.3f}%  ref {row['reference_max_pct']}  ratio {row['ratio_application_to_reference']}", flush=True)
    keys = sorted({k for r in rows for k in r}, key=lambda k: list(rows[0]).index(k) if k in rows[0] else 99)
    with open(os.path.join(RESULTS_DIR, "error_sampling.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(RESULTS_DIR, "error_sampling.json"), "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
