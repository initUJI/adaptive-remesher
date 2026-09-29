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
