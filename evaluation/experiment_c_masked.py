"""Experiment C - Masked simplification.

Question: can the split -> decimate -> merge strategy preserve a
user-designated region while simplifying the rest of the mesh?

For each synthetic case (see synthetic_cases.py), at the same requested
reduction percentage, compares:
- unmasked: the whole mesh handed to the decimator (no protection).
- masked: `case.important_mask` protected, only the rest decimated
  (`decimate_with_mask`).

Error is split by region: every SOURCE face is classified as
"important" if ANY of its 3 vertices is in `important_mask` (the same
rule `decimate_with_mask` itself uses to decide what counts as
protected, see remesh_core.py's `extract_points(..., adjacent_cells=True)`
- kept consistent here rather than reinvented), then source->result
error (the direction that would catch a feature degrading/disappearing,
see Experiment A) is reported separately for the important-region faces
and the rest. Also reports both the whole-mesh reduction and the
unprotected-zone-only reduction (`decimate_with_mask`'s `zone_stats`),
since a masked run's global ratio is diluted by however much of the mesh
is protected.

Run: python -m evaluation.experiment_c_masked
Writes evaluation/results/experiment_c.{csv,json}
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
REDUCTION_PCT = 90.0


def classify_source_faces_as_important(source, important_mask: np.ndarray) -> np.ndarray:
    faces = source.regular_faces
    return important_mask[faces].any(axis=1)


def region_split_error(source, result, face_is_important: np.ndarray):
    """source->result error (reverse direction, see measure_bidirectional_error),
    split into the important-region faces and the rest."""
    _, _, rev_errors, _ = measure_bidirectional_error(source, result)
    important = error_summary(rev_errors[face_is_important])
    rest = error_summary(rev_errors[~face_is_important])
    return important, rest


def reduction_pct_of(before: int, after: int) -> float:
    return round(100.0 * (1 - after / before) if before else 0.0, 2)


def run_case(case, reduction_pct: float = REDUCTION_PCT, engine: int = ENGINE_QEM) -> dict:
    source = case.source
    settings = DecimationSettings(engine=engine, reduction_pct=reduction_pct)
    empty_mask = np.zeros(source.n_points, dtype=bool)

    unmasked_zone: dict = {}
    unmasked = decimate_with_mask(source, empty_mask, settings, zone_stats=unmasked_zone)

    masked_zone: dict = {}
    masked = decimate_with_mask(source, case.important_mask, settings, zone_stats=masked_zone)

    face_is_important = classify_source_faces_as_important(source, case.important_mask)

    (um_imp_max, um_imp_mean, um_imp_p95), (um_rest_max, um_rest_mean, um_rest_p95) = region_split_error(
        source, unmasked, face_is_important
    )
    (m_imp_max, m_imp_mean, m_imp_p95), (m_rest_max, m_rest_mean, m_rest_p95) = region_split_error(
        source, masked, face_is_important
    )

    return {
        "case": case.name,
        "description": case.description,
        "reduction_requested_pct": reduction_pct,
        "important_region_faces": int(face_is_important.sum()),
        "important_region_faces_pct": round(100.0 * face_is_important.mean(), 2),
        # unmasked: whole mesh decimated, no protection
        "unmasked_global_reduction_pct": reduction_pct_of(source.n_cells, unmasked.n_cells),
        "unmasked_important_region_error_max": round(um_imp_max, 6),
        "unmasked_important_region_error_mean": round(um_imp_mean, 6),
        "unmasked_rest_error_max": round(um_rest_max, 6),
        "unmasked_rest_error_mean": round(um_rest_mean, 6),
        # masked: important_mask protected, only the rest decimated
        "masked_global_reduction_pct": reduction_pct_of(source.n_cells, masked.n_cells),
        "masked_unprotected_zone_reduction_pct": reduction_pct_of(
            masked_zone.get("unprotected_tris_before", 0), masked_zone.get("unprotected_tris_after", 0)
        ),
        "masked_important_region_error_max": round(m_imp_max, 6),
        "masked_important_region_error_mean": round(m_imp_mean, 6),
        "masked_rest_error_max": round(m_rest_max, 6),
        "masked_rest_error_mean": round(m_rest_mean, 6),
    }


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    rows = [run_case(case_fn()) for case_fn in ALL_CASES]

    csv_path = os.path.join(RESULTS_DIR, "experiment_c.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    json_path = os.path.join(RESULTS_DIR, "experiment_c.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)

    print(
        f"{'case':<22} {'unmasked E_imp_max':>18} {'masked E_imp_max':>16} "
        f"{'unmasked red%':>13} {'masked red%':>11} {'zone red%':>10}"
    )
    for row in rows:
        print(
            f"{row['case']:<22} {row['unmasked_important_region_error_max']:>18.4f} "
            f"{row['masked_important_region_error_max']:>16.4f} "
            f"{row['unmasked_global_reduction_pct']:>13.1f} "
            f"{row['masked_global_reduction_pct']:>11.1f} "
            f"{row['masked_unprotected_zone_reduction_pct']:>10.1f}"
        )
    print(f"\nWrote {csv_path}\nWrote {json_path}")


if __name__ == "__main__":
    main()
