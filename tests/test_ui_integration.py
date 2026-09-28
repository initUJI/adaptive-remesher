"""Tests de integracion de la UI con pytest-qt: instancian la ventana real
(en el backend Qt "offscreen", ver conftest.py) y ejercitan botones,
sliders y senales de verdad, en vez de solo la logica de dominio pura que
ya cubre test_remesh_core.py. Cierran el hueco de cobertura que dejaba la
division de app.py en ui/panels: aqui se prueba que el wiring entre
paneles y `AdaptiveRemesherWindow` sigue intacto."""

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
    """Ventana con una malla sintetica ya "cargada", sin pasar por el
    QFileDialog real (no hay archivo ni interaccion de usuario que simular)."""
    mesh = pv.Sphere(theta_resolution=30, phi_resolution=30).triangulate()
    window.original_mesh = mesh
    window._reset_protection(rebuild_kdtree=True)
    window.show_mesh(mesh, is_original=True)
    window.update_stats(mesh)
    return window


def test_window_builds_with_expected_widgets(window):
    assert window.engine_combo.count() == 2
    assert not window.qem_group.isEnabled()  # vtkDecimatePro es el motor por defecto
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
    assert loaded_window.protected_count_label.text() != "Puntos protegidos: 0"


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
    """Regresion: activar el pincel tras decimar solia descartar
    `decimated_mesh` de golpe (via reset_mesh), obligando a repetir la
    decimacion aunque el usuario solo quisiera revisar la proteccion."""
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


def test_preset_roundtrip_restores_controls(loaded_window):
    loaded_window.reduction_slider.setValue(77)
    loaded_window.engine_combo.setCurrentIndex(1)
    preset = loaded_window._gather_preset()

    loaded_window.reduction_slider.setValue(10)
    loaded_window.engine_combo.setCurrentIndex(0)

    loaded_window._apply_preset(preset)

    assert loaded_window.reduction_slider.value() == 77
    assert loaded_window.engine_combo.currentIndex() == 1
