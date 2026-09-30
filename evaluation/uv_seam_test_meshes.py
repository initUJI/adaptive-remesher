"""Builds small synthetic flat-grid "models" with a known, deliberately
constructed number of UV-seam vertices: several independently-unwrapped
texture islands placed side by side, each sharing the 3D position of its
touching edge with its neighbor but NOT welded to it (its own separate
point entries, each island normalizing its own U in [0,1] across its own
width) - the standard way a texture atlas produces a seam. Ground truth
(which points are a real seam) is therefore known by construction, not
guessed, unlike testing on a real scan where nobody knows the "true"
answer independently of the detector itself.
"""

from dataclasses import dataclass

import numpy as np
import pyvista as pv


@dataclass
class SeamTestMesh:
    name: str
    mesh: pv.PolyData
    expected_seam_mask: np.ndarray  # ground truth, aligned with mesh.points


def _grid_island_data(rows: int, cols: int, x_offset: int):
    """Points, UV, and triangle faces (local indices) for one flat
    rows x cols grid, at absolute x in [x_offset, x_offset + cols - 1],
    y in [0, rows - 1], z=0, with its own local UV normalized to [0, 1]
    across its own width/height."""
    xs = np.arange(cols) + x_offset
    ys = np.arange(rows)
    gx, gy = np.meshgrid(xs, ys)
    points = np.column_stack([gx.ravel(), gy.ravel(), np.zeros(gx.size)])

    u = np.linspace(0.0, 1.0, cols)
    v = np.linspace(0.0, 1.0, rows)
    gu, gv = np.meshgrid(u, v)
    uv = np.column_stack([gu.ravel(), gv.ravel()])

    faces = []
    for r in range(rows - 1):
        for c in range(cols - 1):
            i00 = r * cols + c
            i01 = r * cols + (c + 1)
            i10 = (r + 1) * cols + c
            i11 = (r + 1) * cols + (c + 1)
            faces.append((i00, i10, i11))
            faces.append((i00, i11, i01))
    return points, uv, faces


def build_islands(name: str, rows: int, island_cols: list) -> SeamTestMesh:
    """`island_cols`: column count of each island (each island's first
    column coincides in 3D with the previous island's last column - the
    shared seam). A single-element list builds one island with no seam
    at all (a control case: expected_seam_mask is all False).

    Built by hand-concatenating points/UV/faces (not `PolyData.merge()`):
    merge() doesn't reliably keep the combined mesh's texture-coordinate
    array marked "active", which would silently make every detector call
    downstream see `active_texture_coordinates is None` and report zero
    seam points - a bug caught by inspecting these islands' UV explicitly
    after construction, not assumed to be fine because merge() ran
    without error."""
    all_points, all_uv, all_faces, expected = [], [], [], []
    offset = 0
    x_offset = 0
    for i, cols in enumerate(island_cols):
        points, uv, faces = _grid_island_data(rows, cols, x_offset)
        all_points.append(points)
        all_uv.append(uv)
        all_faces.extend((a + offset, b + offset, c + offset) for a, b, c in faces)

        island_expected = np.zeros(points.shape[0], dtype=bool)
        if i > 0:
            island_expected[np.arange(rows) * cols] = True  # this island's first column
        if i < len(island_cols) - 1:
            island_expected[np.arange(rows) * cols + (cols - 1)] = True  # this island's last column
        expected.append(island_expected)

        offset += points.shape[0]
        x_offset += cols - 1  # next island starts at this island's last column

    points = np.vstack(all_points)
    uv = np.vstack(all_uv)
    expected_mask = np.concatenate(expected)
    face_array = np.hstack(
        [np.full((len(all_faces), 1), 3), np.asarray(all_faces, dtype=np.int64)]
    ).astype(np.int64)

    mesh = pv.PolyData(points, face_array)
    mesh.active_texture_coordinates = uv
    return SeamTestMesh(name=name, mesh=mesh, expected_seam_mask=expected_mask)


def all_seam_test_meshes() -> list:
    return [
        build_islands("grid_no_seam_control", rows=8, island_cols=[8]),
        build_islands("grid_1seam_small", rows=6, island_cols=[6, 6]),
        build_islands("grid_1seam_medium", rows=10, island_cols=[9, 9]),
        build_islands("grid_1seam_large", rows=16, island_cols=[12, 12]),
        build_islands("grid_2seams_multi", rows=8, island_cols=[6, 6, 6]),
        build_islands("grid_1seam_thin_strip", rows=20, island_cols=[4, 4]),
    ]
