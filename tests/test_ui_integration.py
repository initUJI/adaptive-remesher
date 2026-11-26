"""UI integration tests with pytest-qt: instantiate the real window (on the
"offscreen" Qt backend, see conftest.py) and exercise real buttons, sliders
and signals, instead of only the pure domain logic that
test_remesh_core.py already covers. These close the coverage gap left by
splitting app.py into ui/panels: here we verify that the wiring between
the panels and `AdaptiveRemesherWindow` still works."""

import pyvista as pv
import pytest

from app import AdaptiveRemesherWindow


@pytest.fixture
def window(qtbot):
    win = AdaptiveRemesherWindow()
    qtbot.addWidget(win)
    return win


@pytest.fixture
def loaded_window(window):
    """Window with a synthetic mesh already "loaded", without going through
    the real QFileDialog (there's no file or user interaction to simulate)."""
    mesh = pv.Sphere(theta_resolution=30, phi_resolution=30).triangulate()
    window.original_mesh = mesh
    window._reset_protection(rebuild_kdtree=True)
    window.show_mesh(mesh, is_original=True)
    window.update_stats(mesh)
    return window


def test_window_builds_with_expected_widgets(window):
    assert window.engine_combo.count() == 2
    assert not window.qem_group.isEnabled()  # vtkDecimatePro is the default engine
    assert not window.undo_btn.isEnabled()
    assert not window.redo_btn.isEnabled()


def test_apply_decimation_reduces_triangle_count(loaded_window):
    original_cells = loaded_window.original_mesh.n_cells
    loaded_window.reduction_slider.setValue(60)

    loaded_window.apply_decimation()

    assert loaded_window.decimated_mesh is not None
    assert 0 < loaded_window.decimated_mesh.n_cells < original_cells


def test_apply_decimation_with_qem_engine(loaded_window):
    loaded_window.engine_combo.setCurrentIndex(1)  # ENGINE_QEM
    assert loaded_window.qem_group.isEnabled()

    loaded_window.apply_decimation()

    assert loaded_window.decimated_mesh is not None


def test_undo_redo_roundtrip(loaded_window):
    loaded_window.apply_decimation()
    assert loaded_window.decimated_mesh is not None
    assert loaded_window.undo_btn.isEnabled()

    loaded_window.undo_btn.click()
    assert loaded_window.decimated_mesh is None

    loaded_window.redo_btn.click()
    assert loaded_window.decimated_mesh is not None


def test_paint_brush_marks_protected_points(loaded_window):
    loaded_window.paint_btn.setChecked(True)
    point = loaded_window.original_mesh.points[0]

    loaded_window._apply_brush(point)

    assert loaded_window.protected_mask.sum() > 0
    assert loaded_window.protected_count_label.text() != "Protected points: 0"


def test_clear_protection_resets_mask(loaded_window):
    loaded_window.paint_btn.setChecked(True)
    loaded_window._apply_brush(loaded_window.original_mesh.points[0])
    assert loaded_window.protected_mask.sum() > 0

    loaded_window.clear_protection()

    assert loaded_window.protected_mask.sum() == 0


def test_apply_decimation_excludes_painted_points(loaded_window):
    loaded_window.paint_btn.setChecked(True)
    loaded_window.brush_slider.setValue(20)
    loaded_window._apply_brush(loaded_window.original_mesh.points[0])
    protected_before = loaded_window.protected_mask.copy()
    assert protected_before.any()

    loaded_window.apply_decimation()

    assert loaded_window._last_run_info["manual_protected_points"] == int(protected_before.sum())


def test_toggling_paint_after_decimation_preserves_result(loaded_window):
    """Regression: enabling the brush after decimating used to discard
    `decimated_mesh` outright (via reset_mesh), forcing the decimation to be
    redone even if the user only wanted to review the protection."""
    loaded_window.apply_decimation()
    assert loaded_window.decimated_mesh is not None

    loaded_window.paint_btn.setChecked(True)

    assert loaded_window.decimated_mesh is not None


def test_gather_protection_settings_includes_max_iterations(loaded_window):
    loaded_window.error_max_iterations_spin.setValue(7)

    settings = loaded_window._gather_protection_settings()

    assert settings.error_max_iterations == 7


def test_collapsible_group_hides_children_when_collapsed(loaded_window):
    group = loaded_window.qem_group
    child = loaded_window.volume_preservation_cb
    assert child.isVisibleTo(group)

    group.setChecked(False)
    assert not child.isVisibleTo(group)

    group.setChecked(True)
    assert child.isVisibleTo(group)


def test_curvature_map_disabled_while_incompatible(loaded_window):
    """Regression: the curvature map only colors show_mesh's `is_original`
    call, so it silently did nothing while Live preview was on or after
    applying a decimation, even though the checkbox stayed checked."""
    assert loaded_window.curvature_cb.isEnabled()

    loaded_window.live_preview_cb.setChecked(True)
    assert not loaded_window.curvature_cb.isEnabled()

    loaded_window.live_preview_cb.setChecked(False)
    assert loaded_window.curvature_cb.isEnabled()

    loaded_window.apply_decimation()
    assert not loaded_window.curvature_cb.isEnabled()

    loaded_window.reset_mesh()
    assert loaded_window.curvature_cb.isEnabled()

    loaded_window.apply_decimation()
    loaded_window.paint_btn.setChecked(True)  # switches the view back to the original mesh
    assert loaded_window.curvature_cb.isEnabled()


def test_preset_roundtrip_restores_controls(loaded_window):
    loaded_window.reduction_slider.setValue(77)
    loaded_window.engine_combo.setCurrentIndex(1)
    preset = loaded_window._gather_preset()

    loaded_window.reduction_slider.setValue(10)
    loaded_window.engine_combo.setCurrentIndex(0)

    loaded_window._apply_preset(preset)

    assert loaded_window.reduction_slider.value() == 77
    assert loaded_window.engine_combo.currentIndex() == 1


def test_viewport_background_presets(loaded_window):
    from ui.common import VIEWPORT_BACKGROUND_PRESETS

    assert loaded_window.viewport_bg_combo.count() == len(VIEWPORT_BACKGROUND_PRESETS)

    for index, (_label, hex_color) in enumerate(VIEWPORT_BACKGROUND_PRESETS):
        loaded_window.viewport_bg_combo.setCurrentIndex(index)
        assert loaded_window.plotter.background_color.hex_rgb == hex_color


def test_viewport_background_persists_through_preset_roundtrip(loaded_window):
    loaded_window.viewport_bg_combo.setCurrentIndex(2)
    preset = loaded_window._gather_preset()

    loaded_window.viewport_bg_combo.setCurrentIndex(0)
    loaded_window._apply_preset(preset)

    assert loaded_window.viewport_bg_combo.currentIndex() == 2


def test_options_unavailable_without_mesh_say_why(window):
    assert not window.apply_btn.isEnabled()
    assert "Load a mesh first" in window.apply_btn.toolTip()
    assert not window.protect_uv_seams_cb.isEnabled()


def test_uv_options_disabled_when_mesh_has_no_texture_coordinates(loaded_window):
    # The loaded sphere has no UV.
    assert not loaded_window.protect_uv_seams_cb.isEnabled()
    assert "texture coordinates" in loaded_window.protect_uv_seams_cb.toolTip()
    assert not loaded_window.uv_attribute_metric_cb.isEnabled()
    assert loaded_window.apply_btn.isEnabled()


def test_uv_options_enabled_when_mesh_has_texture_coordinates(loaded_window):
    import numpy as np

    mesh = loaded_window.original_mesh.copy()
    mesh.active_texture_coordinates = np.zeros((mesh.n_points, 2))
    loaded_window.original_mesh = mesh
    loaded_window.engine_combo.setCurrentIndex(1)  # QEM group holds the UV cost option
    loaded_window._refresh_availability()

    assert loaded_window.protect_uv_seams_cb.isEnabled()
    assert loaded_window.uv_attribute_metric_cb.isEnabled()


def test_engine_specific_options_explain_why_they_are_disabled(window):
    window.engine_combo.setCurrentIndex(1)  # QEM
    assert not window.angle_slider.isEnabled()
    assert "vtkDecimatePro" in window.angle_slider.toolTip()
    window.engine_combo.setCurrentIndex(0)
    assert window.angle_slider.isEnabled()
    assert "QEM" in window.qem_group.toolTip()


def test_restore_and_compare_need_a_result(loaded_window):
    assert not loaded_window.compare_btn.isEnabled()
    assert "Apply a decimation first" in loaded_window.compare_btn.toolTip()

    loaded_window.apply_decimation()

    assert loaded_window.compare_btn.isEnabled()
    assert loaded_window.reset_btn.isEnabled()


def test_show_mesh_lines_option_redraws_without_error(loaded_window):
    assert loaded_window.show_edges_cb.isChecked()
    loaded_window.show_edges_cb.setChecked(False)
    actor = next(iter(loaded_window.plotter.renderer.actors.values()))
    assert not actor.prop.show_edges
    loaded_window.show_edges_cb.setChecked(True)


def test_show_texture_option_explains_when_unavailable(loaded_window):
    # The loaded sphere has no texture image.
    assert not loaded_window.show_texture_cb.isEnabled()
    assert "texture image" in loaded_window.show_texture_cb.toolTip()


def test_stats_hint_when_seam_protection_limits_reduction(loaded_window):
    import app as app_module

    real = app_module.seam_protection_limits_reduction
    # The heuristic itself is tested in test_remesh_core; here only the wiring.
    app_module.seam_protection_limits_reduction = lambda *a, **k: True
    try:
        loaded_window.apply_decimation()
    finally:
        app_module.seam_protection_limits_reduction = real
    assert "Hint: UV-seam protection" in loaded_window.stats_label.text()


def _window_with_textured_model(loaded_window):
    import numpy as np

    from materials import MaterialMaps

    mesh = pv.Sphere(theta_resolution=40, phi_resolution=40).triangulate().texture_map_to_sphere(inplace=False)
    loaded_window.original_mesh = mesh
    loaded_window._reset_protection(rebuild_kdtree=True)
    loaded_window.maps = MaterialMaps(
        base=np.full((8, 8, 3), 200, dtype=np.uint8),
        normal=np.full((8, 8, 3), (128, 128, 255), dtype=np.uint8),
    )
    loaded_window.texture = None
    loaded_window.show_mesh(mesh, is_original=True)
    return loaded_window


def test_bake_normal_map_flow(loaded_window):
    window = _window_with_textured_model(loaded_window)
    assert not window.bake_btn.isEnabled()
    assert "Apply a decimation first" in window.bake_btn.toolTip()

    window.protect_uv_seams_cb.setChecked(False)
    window.auto_bake_cb.setChecked(False)
    window.reduction_slider.setValue(80)
    window.apply_decimation()
    assert window.bake_btn.isEnabled() and window.baked_normal is None

    window.bake_size_combo.setCurrentText("512")
    window.bake_normal_map_now()

    assert window.baked_normal is not None and window.baked_normal.shape == (512, 512, 3)
    assert window.show_normals_cb.isEnabled() and window.show_normals_cb.isChecked()
    # A new decimation invalidates the bake.
    window.apply_decimation()
    assert window.baked_normal is None


def test_normal_map_option_explains_when_model_has_none(loaded_window):
    window = _window_with_textured_model(loaded_window)
    window.maps.normal = None
    window._refresh_availability()

    assert not window.show_normals_cb.isEnabled()
    assert "Bake normal map" in window.show_normals_cb.toolTip()


def test_export_maps_use_the_baked_normal_for_the_result(loaded_window, tmp_path):
    import numpy as np

    from materials import load_material_maps, write_glb

    window = _window_with_textured_model(loaded_window)
    window.protect_uv_seams_cb.setChecked(False)
    window.apply_decimation()
    window.bake_size_combo.setCurrentText("512")
    window.bake_normal_map_now()

    maps = window._maps_for_export(window.decimated_mesh)
    assert maps is not None and maps.normal is window.baked_normal
    assert window._maps_for_export(window.original_mesh).normal is window.maps.normal

    path = str(tmp_path / "out.glb")
    write_glb(path, window.decimated_mesh, maps)
    back = load_material_maps(path)
    np.testing.assert_array_equal(back.normal, window.baked_normal)


def test_material_maps_option_draws_roughness_and_occlusion(loaded_window):
    import numpy as np

    window = _window_with_textured_model(loaded_window)
    window.maps.normal = None
    window.maps.metal_rough = np.full((8, 8, 3), (255, 120, 30), dtype=np.uint8)
    window.maps.occlusion = np.full((8, 8, 3), 90, dtype=np.uint8)
    window.texture = pv.Texture(np.full((8, 8, 3), 180, dtype=np.uint8))
    window._refresh_availability()

    assert window.show_normals_cb.isEnabled()  # roughness/occlusion alone are enough
    found = window._pbr_maps_for(window.original_mesh)
    assert set(found) == {"orm"}
    window.show_normals_cb.setChecked(True)  # redraws through the PBR path without raising
    assert window.plotter.renderer.actors


def test_rebake_textures_flow(loaded_window, tmp_path):
    import numpy as np
    import pytest

    from materials import load_material_maps, write_glb, xatlas_available

    if not xatlas_available():
        pytest.skip("optional package xatlas not installed")
    window = _window_with_textured_model(loaded_window)
    window.protect_uv_seams_cb.setChecked(False)
    window.reduction_slider.setValue(80)
    window.apply_decimation()
    assert window.rebake_btn.isEnabled()

    window.bake_size_combo.setCurrentText("512")
    window.rebake_textures_now()

    assert window.result_maps is not None and window.result_maps.base.shape == (512, 512, 3)
    assert window.result_maps.normal.shape == (512, 512, 3)
    assert window._texture_for(window.decimated_mesh) is not None
    window.show_normals_cb.setChecked(True)  # draws with the result's own maps without raising
    maps = window._maps_for_export(window.decimated_mesh)
    assert maps is not None and maps.base.shape == (512, 512, 3)
    write_glb(str(tmp_path / "r.glb"), window.decimated_mesh, maps)
    assert load_material_maps(str(tmp_path / "r.glb")).available() == maps.available()
    # A new decimation drops the re-baked maps.
    window.apply_decimation()
    assert window.result_maps is None


def test_rebake_explains_when_xatlas_is_missing(loaded_window, monkeypatch):
    import app as app_module

    window = _window_with_textured_model(loaded_window)
    window.apply_decimation()
    monkeypatch.setattr(app_module, "xatlas_available", lambda: False)
    window._refresh_availability()

    assert not window.rebake_btn.isEnabled()
    assert "xatlas" in window.rebake_btn.toolTip()


def _fragmented_uv_mesh():
    import numpy as np

    sphere = pv.Sphere(theta_resolution=20, phi_resolution=20).triangulate()
    tri = np.asarray(sphere.regular_faces)
    points = np.asarray(sphere.points)[tri].reshape(-1, 3)
    mesh = pv.PolyData(points, np.hstack([np.full((len(tri), 1), 3), np.arange(len(points)).reshape(-1, 3)]).astype(np.int64))
    mesh.active_texture_coordinates = np.random.default_rng(0).random((len(points), 2))
    return mesh


def test_seam_protection_follows_the_mesh_until_the_user_chooses(loaded_window):
    window = loaded_window
    mesh = _fragmented_uv_mesh()
    window.original_mesh = mesh  # a model with UV, so the option is available
    window._refresh_availability()

    window._apply_seam_policy(mesh)
    assert not window.protect_uv_seams_cb.isChecked()  # seams cover (almost) every vertex
    assert "UV seams cover" in window.stats_label.text()

    window.protect_uv_seams_cb.click()  # the user ticks it
    assert window.protect_uv_seams_cb.isChecked() and not window._seam_auto
    window._apply_seam_policy(mesh)
    assert window.protect_uv_seams_cb.isChecked()  # respected


def test_seam_policy_leaves_meshes_without_uv_alone(loaded_window):
    window = loaded_window
    before = window.protect_uv_seams_cb.isChecked()

    window._apply_seam_policy(window.original_mesh)  # the sphere has no UV

    assert window.protect_uv_seams_cb.isChecked() == before


def test_seam_policy_cuts_instead_of_freezing_when_seams_are_few(loaded_window):
    import numpy as np

    window = loaded_window
    plane = pv.Plane(i_resolution=6, j_resolution=6).triangulate()
    plane.active_texture_coordinates = np.asarray(plane.points)[:, :2] + 0.5  # UV, no seams
    window.original_mesh = plane
    window._refresh_availability()

    window._apply_seam_policy(plane)
    assert window.cut_seams_cb.isChecked() and not window.protect_uv_seams_cb.isChecked()

    fragmented = _fragmented_uv_mesh()
    window._apply_seam_policy(fragmented)
    assert not window.cut_seams_cb.isChecked() and not window.protect_uv_seams_cb.isChecked()

    window.cut_seams_cb.click()  # the user's choice is kept
    assert window.cut_seams_cb.isChecked() and not window._seam_auto


def test_decimating_bakes_the_normal_map_and_says_which_one_it_kept(loaded_window):
    window = _window_with_textured_model(loaded_window)
    window.protect_uv_seams_cb.setChecked(False)
    window.bake_size_combo.setCurrentText("512")
    assert window.auto_bake_cb.isChecked()

    window.apply_decimation()

    assert "Normal map:" in window.stats_label.text()
