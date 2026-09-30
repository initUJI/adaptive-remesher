"""UV seam detection and geometric protection.

Two complementary checks, matching the UV_Seam_Detection sheet's own
framing ("A: does the detector find the intended seam? B: does turning
protection on stop those vertices from collapsing?"):

1. `run_point_confusion_matrix` - a raw point-level confusion matrix
   (TP/FP/FN/TN) for `uv_seam_point_mask` against a hand-built ground
   truth that also probes its numeric tolerance boundary (a UV
   difference just below vs. just above the hardcoded threshold), not
   just the conceptual "duplicated point, different UV" rule. Kept as an
   independent correctness check (results/uv_seam_confusion_matrix.json),
   separate from the sheet below.

2. `run_model_rows` - one row pair per synthetic seam-grid "model" (see
   uv_seam_test_meshes.py: independently-UV-unwrapped texture islands
   placed side by side, sharing 3D position at the touching edge but not
   welded - the standard atlas-seam pattern, with a KNOWN seam-vertex
   count by construction). For each model:
   - "No protection": a single plain `decimate()` call (no split/merge -
     preserves point_data through VTK's own attribute interpolation), at
     70% reduction. Whether the seam survived is re-checked with
     `uv_seam_point_mask` on the result (UV data is still there to check
     against).
   - "Protection ON": `decimate_with_mask` with the detected seam mask
     protected. Protected points are extracted verbatim and never handed
     to the decimator, so they cannot be geometrically collapsed by
     construction - checked here empirically (their exact coordinates
     must still be present in the result) rather than just asserted. But
     this goes through decimate_with_mask's split->decimate->merge path,
     which (see remesh_core.py's `_weld_seam_gaps`) rebuilds the mesh
     from bare PyMeshLab arrays with no point_data attached: UV survives
     geometrically-protected points but the UV *data* itself does not
     survive on the mesh at all - checked directly here rather than
     assumed, since it's the one substantive caveat this sheet exists to
     surface for the paper.

Run: python -u -m evaluation.experiment_uv_seam_detection
Writes evaluation/results/{uv_seam_confusion_matrix,uv_seam_detection}.{csv,json}
"""

import csv
import json
import os

import numpy as np
import pyvista as pv
from scipy.spatial import cKDTree

from remesh_core import (
    ENGINE_QEM,
    DecimationSettings,
    decimate,
    decimate_with_mask,
    uv_seam_point_mask,
)
from evaluation.uv_seam_test_meshes import all_seam_test_meshes

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
REDUCTION_PCT = 70.0


# --- 1. point-level confusion matrix (detector correctness/tolerance) ---

def build_confusion_ground_truth():
    points, uvs, is_seam, group = [], [], [], []

    def add_pair(pos, uv_a, uv_b, seam: bool, name: str):
        for uv in (uv_a, uv_b):
            points.append(pos)
            uvs.append(uv)
            is_seam.append(seam)
            group.append(name)

    add_pair((0.0, 0.0, 0.0), (0.0, 0.0), (0.5, 0.5), True, "seam_large_diff")
    add_pair((1.0, 0.0, 0.0), (0.0, 1.0), (0.5, 0.1), True, "seam_large_diff")
    add_pair((2.0, 0.0, 0.0), (0.3, 0.3), (0.3 + 2e-4, 0.3), True, "seam_above_tolerance")
    add_pair((3.0, 0.0, 0.0), (0.7, 0.2), (0.7, 0.2), False, "duplicate_identical_uv")
    add_pair((4.0, 0.0, 0.0), (0.4, 0.4), (0.4 + 1e-6, 0.4), False, "duplicate_below_tolerance")

    rng = np.random.default_rng(0)
    for i in range(8):
        points.append((10.0 + i * 1.7, 5.0, 0.0))
        uvs.append(tuple(rng.uniform(0, 1, size=2)))
        is_seam.append(False)
        group.append("unique_point")

    mesh = pv.PolyData(np.asarray(points, dtype=float))
    mesh.active_texture_coordinates = np.asarray(uvs, dtype=float)
    return mesh, np.asarray(is_seam, dtype=bool), group


def run_point_confusion_matrix():
    mesh, ground_truth, group = build_confusion_ground_truth()
    predicted = uv_seam_point_mask(mesh)

    tp = int(np.sum(predicted & ground_truth))
    fp = int(np.sum(predicted & ~ground_truth))
    fn = int(np.sum(~predicted & ground_truth))
    tn = int(np.sum(~predicted & ~ground_truth))
    precision = round(tp / (tp + fp), 4) if (tp + fp) else 1.0
    recall = round(tp / (tp + fn), 4) if (tp + fn) else 1.0

    per_group = {}
    for g in sorted(set(group)):
        idx = [i for i, gg in enumerate(group) if gg == g]
        gt, pred = ground_truth[idx], predicted[idx]
        per_group[g] = {
            "ground_truth_seam": bool(gt[0]),
            "all_predicted_correctly": bool(np.array_equal(pred, gt)),
            "n_points": len(idx),
        }

    result = {
        "n_points": mesh.n_points, "true_positives": tp, "false_positives": fp,
        "false_negatives": fn, "true_negatives": tn, "precision": precision, "recall": recall,
        "accuracy": round((tp + tn) / len(ground_truth), 4), "per_group": per_group,
    }
    with open(os.path.join(RESULTS_DIR, "uv_seam_confusion_matrix.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"[confusion matrix] TP={tp} FP={fp} FN={fn} TN={tn} precision={precision} recall={recall}")
    for g, row in per_group.items():
        status = "OK" if row["all_predicted_correctly"] else "MISCLASSIFIED"
        print(f"  {g:28} seam={row['ground_truth_seam']!s:5} n={row['n_points']} -> {status}")
    return result


# --- 2. model-level rows (Excel sheet shape) ---

def _points_present(haystack: np.ndarray, needles: np.ndarray, tol: float) -> int:
    """How many rows of `needles` have a matching point in `haystack`
    within `tol` (used to check protected points weren't lost)."""
    if needles.shape[0] == 0 or haystack.shape[0] == 0:
        return 0
    tree = cKDTree(haystack)
    dist, _ = tree.query(needles, k=1)
    return int(np.sum(dist <= tol))


def run_model(seam_mesh, engine: int = ENGINE_QEM) -> list:
    source = seam_mesh.mesh
    expected_n = int(seam_mesh.expected_seam_mask.sum())
    detected_mask = uv_seam_point_mask(source)
    detected_n = int(detected_mask.sum())
    tol = 1e-6 * max(source.length, 1e-9)

    settings = DecimationSettings(engine=engine, reduction_pct=REDUCTION_PCT)

    # "No protection": plain single-engine decimate, no split/merge.
    off_result = decimate(source, settings, lock_boundary=False)
    off_uv_preserved = off_result.active_texture_coordinates is not None
    if off_uv_preserved and expected_n:
        off_detected_after = int(uv_seam_point_mask(off_result).sum())
        off_collapsed = "Yes" if off_detected_after < expected_n else "No"
    else:
        off_collapsed = "Yes" if expected_n else "No"  # can't verify without UV -> assume worst case

    # "Protection ON": decimate_with_mask with the detected seam mask protected.
    if detected_n:
        on_result = decimate_with_mask(source, detected_mask, settings, zone_stats={})
        seam_points = source.points[detected_mask]
        n_present = _points_present(on_result.points, seam_points, tol)
        on_collapsed = "No" if n_present == detected_n else "Yes"
    else:
        on_result = decimate(source, settings, lock_boundary=False)
        on_collapsed = "No"
    on_uv_preserved = on_result.active_texture_coordinates is not None

    return [
        {
            "model": seam_mesh.name, "expected_seam_vertices": expected_n,
            "detected_seam_vertices": detected_n, "uv_protection_on": "No",
            "seam_vertices_collapsed": off_collapsed,
            "uv_data_preserved_after_merge": "Yes" if off_uv_preserved else "No",
        },
        {
            "model": seam_mesh.name, "expected_seam_vertices": expected_n,
            "detected_seam_vertices": detected_n, "uv_protection_on": "Yes",
            "seam_vertices_collapsed": on_collapsed,
            "uv_data_preserved_after_merge": "Yes" if on_uv_preserved else "No",
        },
    ]


def run_model_rows():
    rows = []
    for seam_mesh in all_seam_test_meshes():
        model_rows = run_model(seam_mesh)
        rows.extend(model_rows)
        off, on = model_rows
        print(
            f"{seam_mesh.name:24} expected={off['expected_seam_vertices']:>3} "
            f"detected={off['detected_seam_vertices']:>3}  "
            f"OFF: collapsed={off['seam_vertices_collapsed']:<3} UVpreserved={off['uv_data_preserved_after_merge']:<3}  "
            f"ON: collapsed={on['seam_vertices_collapsed']:<3} UVpreserved={on['uv_data_preserved_after_merge']:<3}"
        )

    csv_path = os.path.join(RESULTS_DIR, "uv_seam_detection.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    json_path = os.path.join(RESULTS_DIR, "uv_seam_detection.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
    print(f"\nWrote {csv_path}\nWrote {json_path}")
    return rows


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    run_point_confusion_matrix()
    print()
    run_model_rows()


if __name__ == "__main__":
    main()
