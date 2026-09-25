import numpy as np
import pytest
import pyvista as pv
from scipy.spatial import cKDTree

from remesh_core import (
    ENGINE_DECIMATE_PRO,
    ENGINE_QEM,
    CleanSettings,
    DecimationSettings,
    ProtectionSettings,
    clean_mesh,
    decimate_pro,
    decimate_qem,
    decimate_with_mask,
    ensure_active_texture_coords,
    error_summary,
    load_and_prepare_mesh,
    measure_bidirectional_error,
    mesh_has_texture_coords,
    run_decimation_pipeline,
    uv_seam_point_mask,
)


# --- texturas / UV ---


def test_mesh_has_texture_coords_false_by_default(sphere_mesh):
    assert not mesh_has_texture_coords(sphere_mesh)


def test_ensure_active_texture_coords_activates_named_uv_array():
    # Un PolyData construido a mano (a diferencia de pv.Plane(), que ya trae
    # unas UV activas por defecto) para partir de una malla sin textura.
    points = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [1.0, 1.0, 0.0],
            [0.0, 1.0, 0.0],
        ]
    )
    faces = np.array([3, 0, 1, 2, 3, 0, 2, 3], dtype=np.int64)
    mesh = pv.PolyData(points, faces)
    assert mesh.active_texture_coordinates is None

    uv = np.random.default_rng(0).random((mesh.n_points, 2))
    mesh.point_data["UVMap"] = uv
    assert not mesh_has_texture_coords(mesh)

    ensure_active_texture_coords(mesh)

    assert mesh_has_texture_coords(mesh)
    np.testing.assert_allclose(mesh.active_texture_coordinates, uv)


def test_ensure_active_texture_coords_is_noop_if_already_active(sphere_mesh):
    ensure_active_texture_coords(sphere_mesh)  # no debe lanzar ni cambiar nada
    assert not mesh_has_texture_coords(sphere_mesh)


def test_uv_seam_point_mask_detects_seam(uv_seam_mesh):
    mask = uv_seam_point_mask(uv_seam_mesh)
    assert mask.tolist() == [True, False, False, True, False]


def test_uv_seam_point_mask_without_uv_returns_all_false(sphere_mesh):
    mask = uv_seam_point_mask(sphere_mesh)
    assert mask.shape == (sphere_mesh.n_points,)
    assert not mask.any()


# --- limpieza ---


def test_clean_mesh_removes_small_distant_fragment():
    big = pv.Sphere(theta_resolution=20, phi_resolution=20).triangulate()
    tiny = pv.Sphere(radius=0.01, center=(10, 10, 10), theta_resolution=6, phi_resolution=6).triangulate()
    combined = big.merge(tiny)
    assert combined.length > 10  # la diagonal la domina el fragmento lejano

    cleaned = clean_mesh(combined, min_component_pct=5.0)

    # Sin el fragmento lejano, la diagonal del resultado vuelve a ser la de
    # la esfera grande (~1), muy por debajo de la del conjunto combinado.
    assert cleaned.length < 2.0
    assert cleaned.n_cells < combined.n_cells


def test_clean_mesh_keeps_single_component_mesh(sphere_mesh):
    cleaned = clean_mesh(sphere_mesh, min_component_pct=5.0)
    assert cleaned.n_cells > 0
    assert cleaned.length == pytest.approx(sphere_mesh.length, rel=1e-3)


# --- decimacion (motores individuales) ---


def test_decimate_pro_reduces_cell_count(sphere_mesh):
    result = decimate_pro(
        sphere_mesh,
        reduction=0.5,
        feature_angle=15.0,
        preserve_topology=True,
        boundary_vertex_deletion=False,
        splitting=True,
    )
    assert 0 < result.n_cells < sphere_mesh.n_cells


def test_decimate_qem_reduces_cell_count(sphere_mesh):
    result = decimate_qem(
        sphere_mesh,
        reduction=0.5,
        volume_preservation=True,
        use_uv_weight=False,
        tcoords_weight=1.0,
        boundary_locked=False,
    )
    assert 0 < result.n_cells < sphere_mesh.n_cells


def test_decimate_pro_no_cells_is_passthrough():
    empty = pv.PolyData()
    result = decimate_pro(empty, 0.5, feature_angle=15.0, preserve_topology=True,
                           boundary_vertex_deletion=False, splitting=True)
    assert result is empty


# --- mascara de proteccion ---


def test_decimate_with_mask_preserves_protected_points(sphere_mesh):
    mask = sphere_mesh.points[:, 2] > 0  # hemisferio protegido
    settings = DecimationSettings(engine=ENGINE_DECIMATE_PRO, reduction_pct=90, preserve_topology=False)

    result = decimate_with_mask(sphere_mesh, mask, settings)

    protected_points = sphere_mesh.points[mask]
    tree = cKDTree(result.points)
    dist, _ = tree.query(protected_points)
    assert np.all(dist < 1e-6)


def test_decimate_with_mask_qem_seam_is_watertight(sphere_mesh):
    """Regresion: con el motor QEM, la costura entre la zona protegida y
    la decimada se abria (aristas no-manifold tras el merge) a partir de
    ~70% de reduccion con el peso de borde antiguo (1000), porque
    vtkQuadricDecimation solo penaliza el coste de tocar el borde, no lo
    bloquea como vtkDecimatePro. Con el peso actual (1e10) queda
    perfectamente soldada hasta ~90% de reduccion (barrido comprobado
    manualmente con varias resoluciones/mascaras; ver decimate_qem). Al
    80% -usado aqui- es fiable en todos los casos probados."""
    mask = sphere_mesh.points[:, 2] > 0.3
    settings = DecimationSettings(engine=ENGINE_QEM, reduction_pct=80, volume_preservation=False)

    result = decimate_with_mask(sphere_mesh, mask, settings)

    problem_edges = result.extract_feature_edges(
        boundary_edges=True, non_manifold_edges=True, feature_edges=False, manifold_edges=False
    )
    assert problem_edges.n_cells == 0


def test_decimate_with_mask_no_mask_decimates_everything(sphere_mesh):
    mask = np.zeros(sphere_mesh.n_points, dtype=bool)
    settings = DecimationSettings(engine=ENGINE_QEM, reduction_pct=50)

    result = decimate_with_mask(sphere_mesh, mask, settings)

    assert 0 < result.n_cells < sphere_mesh.n_cells


# --- error geometrico ---


def test_error_summary_empty_array():
    assert error_summary(np.array([])) == (0.0, 0.0, 0.0)


def test_error_summary_values():
    errors = np.array([0.0, 1.0, 2.0, 3.0, 100.0])
    max_e, mean_e, p95_e = error_summary(errors)
    assert max_e == 100.0
    assert mean_e == pytest.approx(21.2)
    assert p95_e == pytest.approx(np.percentile(errors, 95))


def test_measure_bidirectional_error_detects_fully_removed_feature():
    """La direccion original (result->source) no detecta que un trozo
    entero del original haya desaparecido del resultado: todas las caras
    que sobreviven en `result` ya estaban en `source`, asi que su
    distancia a la superficie original es ~0 independientemente de lo que
    falte. La direccion inversa (source->result) si lo detecta, porque
    las caras de `source` que formaban la parte eliminada ya no tienen
    ninguna superficie cerca en `result`."""
    base = pv.Plane(i_resolution=10, j_resolution=10).triangulate()
    # Bulto lejos de la base, para que su ausencia sea inequivoca.
    bump = pv.Plane(i_resolution=2, j_resolution=2, center=(10, 10, 5)).triangulate()
    source = base.merge(bump, merge_points=False)
    result = base  # "olvida" el bulto por completo, como una decimacion que lo aplanara del todo

    fwd_errors, _, rev_errors, _ = measure_bidirectional_error(source, result)

    fwd_max, _, _ = error_summary(fwd_errors)
    rev_max, _, _ = error_summary(rev_errors)
    assert fwd_max < 1e-6
    assert rev_max > 1.0


# --- pipeline completo ---


def test_run_decimation_pipeline_reduces_triangle_count(sphere_mesh):
    decimation = DecimationSettings(engine=ENGINE_DECIMATE_PRO, reduction_pct=50, preserve_topology=True)
    protection = ProtectionSettings(protect_uv_seams=False, auto_error_protection=True, error_threshold_pct=1.0)

    result = run_decimation_pipeline(sphere_mesh, decimation, protection)

    assert result.original_tris == sphere_mesh.n_cells
    assert 0 < result.result_tris < sphere_mesh.n_cells
    assert result.reduction_real_pct > 0
    assert result.error_max is not None
    assert result.manual_protected_points == 0
    assert result.uv_seam_protected_points == 0


def test_run_decimation_pipeline_respects_manual_mask(sphere_mesh):
    manual_mask = np.zeros(sphere_mesh.n_points, dtype=bool)
    manual_mask[: sphere_mesh.n_points // 4] = True
    decimation = DecimationSettings(engine=ENGINE_QEM, reduction_pct=80)
    protection = ProtectionSettings(protect_uv_seams=False, auto_error_protection=False)

    result = run_decimation_pipeline(sphere_mesh, decimation, protection, manual_mask=manual_mask)

    assert result.manual_protected_points == int(manual_mask.sum())
    protected_points = sphere_mesh.points[manual_mask]
    tree = cKDTree(result.mesh.points)
    dist, _ = tree.query(protected_points)
    assert np.all(dist < 1e-6)


def test_run_decimation_pipeline_protects_uv_seams(uv_seam_mesh):
    decimation = DecimationSettings(engine=ENGINE_QEM, reduction_pct=10)
    protection = ProtectionSettings(protect_uv_seams=True, auto_error_protection=False)

    result = run_decimation_pipeline(uv_seam_mesh, decimation, protection)

    assert result.uv_seam_protected_points == 2


def test_run_decimation_pipeline_raises_when_result_is_empty():
    # target_reduction es un objetivo, no una garantia (vtkDecimatePro no
    # necesariamente deja una malla real en 0 caras aunque se pida 100%),
    # asi que se fuerza el caso "sin caras" con una malla ya vacia de origen.
    empty = pv.PolyData()
    decimation = DecimationSettings(engine=ENGINE_DECIMATE_PRO, reduction_pct=50)
    protection = ProtectionSettings(protect_uv_seams=False, auto_error_protection=False)

    with pytest.raises(ValueError):
        run_decimation_pipeline(empty, decimation, protection)


# --- carga desde disco ---


def test_load_and_prepare_mesh_reads_and_cleans(tmp_path, sphere_mesh):
    path = tmp_path / "sphere.obj"
    sphere_mesh.save(str(path))

    mesh = load_and_prepare_mesh(str(path), CleanSettings(min_component_pct=0))

    assert mesh.n_cells > 0


def test_load_and_prepare_mesh_without_clean_settings(tmp_path, sphere_mesh):
    path = tmp_path / "sphere.obj"
    sphere_mesh.save(str(path))

    mesh = load_and_prepare_mesh(str(path), None)

    assert mesh.n_cells == sphere_mesh.n_cells


def test_load_and_prepare_mesh_rejects_empty_file(tmp_path):
    path = tmp_path / "empty.vtp"
    pv.PolyData().save(str(path))

    with pytest.raises(ValueError):
        load_and_prepare_mesh(str(path), None)
