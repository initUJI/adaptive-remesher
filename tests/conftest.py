import os

# Must be set before any fixture creates a QApplication (pytest-qt does
# this as soon as a test requests `qtbot`): without this, UI tests would
# fail on any machine/CI without a display (not overridden if the user
# already set their own Qt backend).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
import pyvista as pv


@pytest.fixture
def sphere_mesh() -> pv.PolyData:
    """Triangulated sphere with enough resolution for decimation to have
    real room to reduce triangles in the tests."""
    return pv.Sphere(theta_resolution=48, phi_resolution=48).triangulate()


@pytest.fixture
def uv_seam_mesh() -> pv.PolyData:
    """Two triangles sharing an edge, with the shared vertex duplicated
    (same 3D position at indices 0 and 3, different UV): the usual way
    OBJ/GLTF represent a texture seam when unwrapping a mesh."""
    points = np.array(
        [
            [0.0, 0.0, 0.0],  # 0: same position as 3, different UV -> seam
            [1.0, 0.0, 0.0],  # 1
            [0.0, 1.0, 0.0],  # 2
            [0.0, 0.0, 0.0],  # 3: duplicate of 0
            [1.0, 1.0, 0.0],  # 4
        ]
    )
    faces = np.array([3, 0, 1, 2, 3, 3, 4, 1], dtype=np.int64)
    mesh = pv.PolyData(points, faces)
    mesh.active_texture_coordinates = np.array(
        [
            [0.0, 0.0],
            [1.0, 0.0],
            [0.0, 1.0],
            [0.0, 1.0],  # 3's UV differs from 0's -> seam
            [1.0, 1.0],
        ]
    )
    return mesh
