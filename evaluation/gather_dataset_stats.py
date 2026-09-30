"""Reads every model in the real-model dataset (see dataset_catalog.py)
with the app's own loading pattern and computes the stats that populate
the Dataset sheet: triangles, vertices, connected components, UV,
normals, bounding-box diagonal.

Connected components are counted with vtkPolyDataConnectivityFilter
directly (region count only) rather than pyvista's `split_bodies()`,
which extracts every region into its own PolyData - some of these
models (duplicated-vertex OBJ exports, see notes below) have as many
components as triangles, and split_bodies() never finished on those in
over 20 minutes; the direct filter takes well under a second.

Run: python -m evaluation.gather_dataset_stats
Writes evaluation/results/dataset.json
"""

import json
import os
import time

import pyvista as pv
import vtk

from evaluation.dataset_catalog import CATALOG, MODELS_ROOT

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")

# Models whose "components" count equals their triangle count: every face
# has 3 unique, non-shared vertices (a duplicated-vertex/flat-shaded
# export artifact), so the topological component count doesn't reflect
# real geometric fragmentation - flagged explicitly rather than left to
# look like a badly broken mesh.
NON_WELDED_NOTE = (
    " (components == triangles: source export duplicates vertices per "
    "face, not real fragmentation)"
)


def count_connected_components(mesh: pv.PolyData) -> int:
    conn = vtk.vtkPolyDataConnectivityFilter()
    conn.SetInputData(mesh)
    conn.SetExtractionModeToAllRegions()
    conn.Update()
    return conn.GetNumberOfExtractedRegions()


def gather() -> list:
    rows = []
    for cat, fname, display, url, lic, notes in CATALOG:
        t0 = time.perf_counter()
        path = os.path.join(MODELS_ROOT, cat, fname)
        mesh = pv.read(path).extract_surface(algorithm="dataset_surface").triangulate()
        n_components = count_connected_components(mesh)
        has_uv = mesh.active_texture_coordinates is not None
        has_normals = "Normals" in mesh.point_data or "Normals" in mesh.cell_data
        full_notes = notes + (NON_WELDED_NOTE if n_components == mesh.n_cells else "")
        rows.append({
            "model": display,
            "file": f"{cat}/{fname}",
            "category": cat,
            "source_url": url,
            "license": lic,
            "triangles": mesh.n_cells,
            "vertices": mesh.n_points,
            "components": n_components,
            "has_uv": has_uv,
            "has_normals": has_normals,
            "bbox_diagonal": round(float(mesh.length), 4),
            "notes": full_notes,
        })
        dt = time.perf_counter() - t0
        print(
            f"{cat:11} {fname:38} tris={mesh.n_cells:>8} verts={mesh.n_points:>8} "
            f"comp={n_components:>7} UV={has_uv} N={has_normals} diag={mesh.length:.3f} ({dt:.1f}s)",
            flush=True,
        )
    return rows


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    rows = gather()
    json_path = os.path.join(RESULTS_DIR, "dataset.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
    print(f"\nWrote {json_path}")


if __name__ == "__main__":
    main()
