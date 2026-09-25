import numpy as np
import pytest
import pyvista as pv


@pytest.fixture
def sphere_mesh() -> pv.PolyData:
    """Esfera triangulada con suficiente resolucion para que la decimacion
    tenga margen real para reducir triangulos en los tests."""
    return pv.Sphere(theta_resolution=48, phi_resolution=48).triangulate()


@pytest.fixture
def uv_seam_mesh() -> pv.PolyData:
    """Dos triangulos que comparten una arista, con el vertice compartido
    duplicado (misma posicion 3D en los indices 0 y 3, distinta UV): la
    forma habitual en que OBJ/GLTF representan una costura de textura al
    desenvolver la malla."""
    points = np.array(
        [
            [0.0, 0.0, 0.0],  # 0: misma posicion que 3, UV distinta -> costura
            [1.0, 0.0, 0.0],  # 1
            [0.0, 1.0, 0.0],  # 2
            [0.0, 0.0, 0.0],  # 3: duplicado de 0
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
            [0.0, 1.0],  # UV de 3 distinta de la de 0 -> costura
            [1.0, 1.0],
        ]
    )
    return mesh
