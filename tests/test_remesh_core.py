import numpy as np
import pytest
import pyvista as pv
from scipy.spatial import cKDTree  # type: ignore[attr-defined]

from remesh_core import (
    ENGINE_DECIMATE_PRO,
    ENGINE_QEM,
    CleanSettings,
    DecimationSettings,
    ProtectionSettings,
    bidirectional_error_summary,
    clean_mesh,
    decimate_pro,
    decimate_qem,
    decimate_with_mask,
    ensure_active_texture_coords,
    error_summary,
    load_and_prepare_mesh,
    measure_bidirectional_error,
    mesh_has_texture_coords,
    refine_by_error,
    run_decimation_pipeline,
    uv_seam_point_mask,
)


# --- textures / UV ---


def test_mesh_has_texture_coords_false_by_default(sphere_mesh):
    assert not mesh_has_texture_coords(sphere_mesh)


def test_ensure_active_texture_coords_activates_named_uv_array():
    # A hand-built PolyData (unlike pv.Plane(), which already comes with
    # active UVs by default) so we start from a mesh with no texture.
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
    ensure_active_texture_coords(sphere_mesh)  # must not raise or change anything
    assert not mesh_has_texture_coords(sphere_mesh)


def test_uv_seam_point_mask_detects_seam(uv_seam_mesh):
    mask = uv_seam_point_mask(uv_seam_mesh)
    assert mask.tolist() == [True, False, False, True, False]


def test_uv_seam_point_mask_without_uv_returns_all_false(sphere_mesh):
    mask = uv_seam_point_mask(sphere_mesh)
    assert mask.shape == (sphere_mesh.n_points,)
    assert not mask.any()


# --- cleanup ---


def test_clean_mesh_removes_small_distant_fragment():
    big = pv.Sphere(theta_resolution=20, phi_resolution=20).triangulate()
    tiny = pv.Sphere(radius=0.01, center=(10, 10, 10), theta_resolution=6, phi_resolution=6).triangulate()
    combined = big.merge(tiny)
    assert combined.length > 10  # the diagonal is dominated by the distant fragment

    cleaned = clean_mesh(combined, min_component_pct=5.0)

    # Without the distant fragment, the result's diagonal goes back to the
    # big sphere's (~1), well below the combined set's.
    assert cleaned.length < 2.0
    assert cleaned.n_cells < combined.n_cells


def test_clean_mesh_keeps_single_component_mesh(sphere_mesh):
    cleaned = clean_mesh(sphere_mesh, min_component_pct=5.0)
    assert cleaned.n_cells > 0
    assert cleaned.length == pytest.approx(sphere_mesh.length, rel=1e-3)


# --- decimation (individual engines) ---


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


# --- protection mask ---


def test_decimate_with_mask_preserves_protected_points(sphere_mesh):
    mask = sphere_mesh.points[:, 2] > 0  # protected hemisphere
    settings = DecimationSettings(engine=ENGINE_DECIMATE_PRO, reduction_pct=90, preserve_topology=False)

    result = decimate_with_mask(sphere_mesh, mask, settings)

    protected_points = sphere_mesh.points[mask]
    tree = cKDTree(result.points)
    dist, _ = tree.query(protected_points)
    assert np.all(dist < 1e-6)


def test_decimate_with_mask_qem_seam_is_watertight(sphere_mesh):
    """Regression: with the QEM engine, the seam between the protected
    region and the decimated one used to open up (non-manifold edges after
    the merge) starting at ~70% reduction with the old boundary weight
    (1000), because vtkQuadricDecimation only penalizes the cost of
    touching the boundary, it doesn't lock it like vtkDecimatePro. With the
    current weight (1e10) it stays perfectly welded up to ~90% reduction
    (manually verified with a sweep of resolutions/masks; see
    decimate_qem). At 80% -used here- it's reliable in every case tested."""
    mask = sphere_mesh.points[:, 2] > 0.3
    settings = DecimationSettings(engine=ENGINE_QEM, reduction_pct=80, volume_preservation=False)

    result = decimate_with_mask(sphere_mesh, mask, settings)

    problem_edges = result.extract_feature_edges(
        boundary_edges=True, non_manifold_edges=True, feature_edges=False, manifold_edges=False
    )
    assert problem_edges.n_cells == 0


def test_decimate_with_mask_qem_seam_is_watertight_at_extreme_reduction(sphere_mesh):
    """Regression for `_weld_seam_gaps`: before adding the residual-gap
    closing step in `decimate_with_mask`, 98% reduction left boundary edges
    unwelded on the seam (a genuine T-junction, not solvable by raising
    `merge_points`'s tolerance nor with a standard non-manifold repair,
    verified empirically). With the gap-closing step added, this
    resolution/mask stays perfectly welded up to 98% (above ~99% the
    remaining gap is no longer a simple polygon and PyMeshLab can't always
    close it fully, see `_weld_seam_gaps`)."""
    mask = sphere_mesh.points[:, 2] > 0.3
    settings = DecimationSettings(engine=ENGINE_QEM, reduction_pct=98, volume_preservation=False)

    result = decimate_with_mask(sphere_mesh, mask, settings)

    problem_edges = result.extract_feature_edges(
        boundary_edges=True, non_manifold_edges=True, feature_edges=False, manifold_edges=False
    )
    assert problem_edges.n_cells == 0


def test_decimate_with_mask_qem_seam_closed_by_residual_gap_stitcher():
    """Regression for `_stitch_residual_boundary_gaps`: at this resolution,
    mask and reduction level (deliberately different from the `sphere_mesh`
    fixture's: at that specific resolution the residual, although
    reproducible, is no longer a simple polygon and even the final stitch
    doesn't close it completely -see known limitations in the CHANGELOG),
    `_weld_seam_gaps` (PyMeshLab's hole closing) alone left 5 boundary
    edges unwelded -a 'bowtie' vertex that no PyMeshLab/VTK repair filter
    tried knows how to resolve (several attempts documented in
    `_weld_seam_gaps`). The fan triangulation of
    `_stitch_residual_boundary_gaps` as a last resort closes them
    completely here."""
    sphere = pv.Sphere(theta_resolution=30, phi_resolution=30).triangulate()
    mask = sphere.points[:, 2] > 0.3
    settings = DecimationSettings(engine=ENGINE_QEM, reduction_pct=99, volume_preservation=False)

    result = decimate_with_mask(sphere, mask, settings)

    problem_edges = result.extract_feature_edges(
        boundary_edges=True, non_manifold_edges=True, feature_edges=False, manifold_edges=False
    )
    assert problem_edges.n_cells == 0


def test_decimate_with_mask_never_raises_when_pymeshlab_repair_rejects_mesh():
    """Regression: in a handful of resolution/mask combinations, the mesh
    resulting from the merge has genuinely non-manifold edges (shared by 3+
    faces), not just the usual T-junction, and PyMeshLab rejects
    `meshing_close_holes` with an exception instead of repairing it.
    `_weld_seam_gaps` must catch it and return the unrepaired mesh instead
    of letting it blow up all of `decimate_with_mask` (and with it, the
    whole `run_decimation_pipeline`)."""
    sphere = pv.Sphere(theta_resolution=25, phi_resolution=25).triangulate()
    mask = sphere.points[:, 2] > 0.3
    settings = DecimationSettings(engine=ENGINE_QEM, reduction_pct=99, volume_preservation=False)

    result = decimate_with_mask(sphere, mask, settings)  # must not raise

    assert result.n_cells > 0


def test_decimate_with_mask_no_mask_decimates_everything(sphere_mesh):
    mask = np.zeros(sphere_mesh.n_points, dtype=bool)
    settings = DecimationSettings(engine=ENGINE_QEM, reduction_pct=50)

    result = decimate_with_mask(sphere_mesh, mask, settings)

    assert 0 < result.n_cells < sphere_mesh.n_cells


# --- geometric error ---


def test_error_summary_empty_array():
    assert error_summary(np.array([])) == (0.0, 0.0, 0.0)


def test_error_summary_values():
    errors = np.array([0.0, 1.0, 2.0, 3.0, 100.0])
    max_e, mean_e, p95_e = error_summary(errors)
    assert max_e == 100.0
    assert mean_e == pytest.approx(21.2)
    assert p95_e == pytest.approx(np.percentile(errors, 95))


def test_measure_bidirectional_error_detects_fully_removed_feature():
    """The original direction (result->source) fails to detect that an
    entire chunk of the original has disappeared from the result: every
    face that survives in `result` was already in `source`, so its
    distance to the original surface is ~0 regardless of what's missing.
    The reverse direction (source->result) does detect it, because the
    `source` faces that made up the removed part no longer have any
    surface nearby in `result`."""
    base = pv.Plane(i_resolution=10, j_resolution=10).triangulate()
    # Bump far from the base, so its absence is unambiguous.
    bump = pv.Plane(i_resolution=2, j_resolution=2, center=(10, 10, 5)).triangulate()
    source = base.merge(bump, merge_points=False)
    result = base  # "forgets" the bump entirely, like a decimation that flattened it out completely

    fwd_errors, _, rev_errors, _ = measure_bidirectional_error(source, result)

    fwd_max, _, _ = error_summary(fwd_errors)
    rev_max, _, _ = error_summary(rev_errors)
    assert fwd_max < 1e-6
    assert rev_max > 1.0


def test_bidirectional_error_summary_pools_and_splits_by_direction():
    fwd_errors = np.array([0.0, 1.0, 2.0])
    rev_errors = np.array([0.0, 10.0])

    stats = bidirectional_error_summary(fwd_errors, rev_errors)

    assert stats.max_fwd == 2.0
    assert stats.max_rev == 10.0
    assert stats.max == 10.0 == max(stats.max_fwd, stats.max_rev)
    pooled_max, pooled_mean, pooled_p95 = error_summary(np.concatenate([fwd_errors, rev_errors]))
    assert (stats.max, stats.mean, stats.p95) == (pooled_max, pooled_mean, pooled_p95)


def test_refine_by_error_respects_max_iterations(sphere_mesh):
    """`max_iterations` caps how many times `refine_by_error` can decimate
    again; with a threshold so strict it's never fully satisfied, it must
    not exceed the requested limit even if the error is still above the
    threshold after that pass."""
    mask = np.zeros(sphere_mesh.n_points, dtype=bool)
    settings = DecimationSettings(engine=ENGINE_QEM, reduction_pct=90)
    result = decimate_with_mask(sphere_mesh, mask, settings)

    _, _, _, iterations, _ = refine_by_error(
        sphere_mesh, result, mask, settings, error_threshold_pct=0.001, max_iterations=1
    )

    assert iterations <= 1


# --- full pipeline ---


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
    # With no protected region, the unprotected zone IS the whole mesh, so
    # the zone-relative and whole-mesh reduction ratios should match.
    assert result.unprotected_zone_tris_before == sphere_mesh.n_cells
    assert result.unprotected_zone_reduction_pct == pytest.approx(result.reduction_real_pct, abs=0.5)
    assert result.error_max == pytest.approx(max(result.error_max_fwd, result.error_max_rev))


def test_pipeline_zone_reduction_diverges_from_global_with_large_protection(sphere_mesh):
    """Regression: reduction_real_pct is a whole-mesh ratio, diluted by
    however much of the mesh is protected (never fed to the decimator),
    while unprotected_zone_reduction_pct tracks what SetTargetReduction
    actually applies to. A large protected region should make the two
    numbers diverge noticeably."""
    manual_mask = np.zeros(sphere_mesh.n_points, dtype=bool)
    manual_mask[: sphere_mesh.n_points * 3 // 4] = True  # protect ~75% of the mesh
    decimation = DecimationSettings(engine=ENGINE_QEM, reduction_pct=90)
    protection = ProtectionSettings(protect_uv_seams=False, auto_error_protection=False)

    result = run_decimation_pipeline(
        sphere_mesh, decimation, protection, manual_mask=manual_mask
    )

    assert result.unprotected_zone_reduction_pct > result.reduction_real_pct + 10


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
    # target_reduction is a goal, not a guarantee (vtkDecimatePro doesn't
    # necessarily leave an actual mesh at 0 faces even if 100% is
    # requested), so the "no faces" case is forced with an already-empty
    # source mesh.
    empty = pv.PolyData()
    decimation = DecimationSettings(engine=ENGINE_DECIMATE_PRO, reduction_pct=50)
    protection = ProtectionSettings(protect_uv_seams=False, auto_error_protection=False)

    with pytest.raises(ValueError):
        run_decimation_pipeline(empty, decimation, protection)


# --- loading from disk ---


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
