"""Adaptive remesher prototype: reduces polygons only where they aren't
needed, with two interchangeable decimation engines: vtkDecimatePro (fixed
feature angle) and Quadric Error Metric (continuous cost weighted by
curvature, with optional UV-distortion penalty).

Includes pre-cleanup typical of AI-generated meshes (loose fragments,
non-manifold vertices/edges, optional hole filling) via PyMeshLab; manual
paint/erase of protected regions; automatic UV-seam protection; and
geometric error validation that auto-protects regions where decimation
deviates too far from the original.

UI construction is split by section under `ui/panels/` (one
`build_xxx_panel(window, panel_layout)` function per control-panel group);
this module holds the window's state and all its logic.
"""

import dataclasses
import json
import logging
import os
import sys
from datetime import datetime
from typing import Optional, cast

import numpy as np
import pyvista as pv
import vtk
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QRubberBand,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QIcon, QMouseEvent
from pyvistaqt import QtInteractor
from scipy.spatial import cKDTree  # type: ignore[attr-defined]

from remesh_core import (
    ENGINE_QEM,
    CleanSettings,
    DecimationSettings,
    ProtectionSettings,
    clean_mesh,
    decimate_with_mask,
    load_and_prepare_mesh,
    load_base_color_texture,
    mesh_has_texture_coords,
    run_decimation_pipeline,
    save_texture_with_mesh,
    SEAM_FREEZE_LIMIT,
    seam_protection_limits_reduction,
    seam_vertex_fraction,
    should_cut_uv_seams,
    transfer_texture_coords,
    uv_seam_point_mask,
)
from materials import (
    MaterialMaps,
    bake_material_maps,
    bake_normal_map,
    best_normal_map,
    load_material_maps,
    map_to_texture,
    orm_image,
    reparametrize,
    with_tangents,
    xatlas_available,
    write_glb,
)
from logging_config import configure_logging
from ui.common import (
    STYLESHEET,
    VIEWPORT_BACKGROUND_PRESETS,
    CollapsibleGroupBox,
    ComparisonDialog,
    busy_cursor,
    set_available,
    tip,
)
from ui.panels import (
    build_action_buttons,
    build_batch_panel,
    build_clean_panel,
    build_decimation_panel,
    build_header,
    build_presets_panel,
    build_protection_panel,
    build_validation_panel,
    build_view_panel,
)

def _resource_path(*parts):
    """Resolves a path next to the source tree, or inside the PyInstaller
    bundle (`sys._MEIPASS`) when frozen."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


PANEL_STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".panel_layout.json")
ICON_PATH = _resource_path("assets", "icon.png")

BATCH_INPUT_EXTENSIONS = (".obj", ".stl", ".ply", ".vtp", ".glb", ".gltf")

logger = logging.getLogger(__name__)


class AdaptiveRemesherWindow(QMainWindow):
    """Main window: 3D viewport + control panel to clean, manually protect
    and adaptively decimate a mesh."""

    # Widgets created and assigned by the build_xxx_panel functions in
    # ui.panels (called from __init__): annotated here without assignment,
    # purely so mypy knows their type — the actual assignment happens inside
    # each build_xxx_panel, not here.
    undo_btn: QPushButton
    redo_btn: QPushButton
    auto_clean_cb: QCheckBox
    min_fragment_slider: QSlider
    min_fragment_spin: QSpinBox
    close_holes_cb: QCheckBox
    max_hole_size_spin: QSpinBox
    fix_orientation_cb: QCheckBox
    curvature_cb: QCheckBox
    live_preview_cb: QCheckBox
    viewport_bg_combo: QComboBox
    paint_btn: QPushButton
    erase_btn: QPushButton
    rect_btn: QPushButton
    brush_slider: QSlider
    brush_spin: QSpinBox
    protected_count_label: QLabel
    protect_uv_seams_cb: QCheckBox
    cut_seams_cb: QCheckBox
    auto_bake_cb: QCheckBox
    engine_combo: QComboBox
    reduction_slider: QSlider
    reduction_value_label: QLabel
    angle_slider: QSlider
    angle_value_label: QLabel
    preserve_topology_cb: QCheckBox
    boundary_vertex_deletion_cb: QCheckBox
    splitting_cb: QCheckBox
    qem_group: CollapsibleGroupBox
    volume_preservation_cb: QCheckBox
    uv_attribute_metric_cb: QCheckBox
    uv_weight_spin: QDoubleSpinBox
    normals_weight_cb: QCheckBox
    normals_weight_spin: QDoubleSpinBox
    auto_error_cb: QCheckBox
    error_threshold_spin: QDoubleSpinBox
    error_max_iterations_spin: QSpinBox
    lower_reduction_cb: QCheckBox
    export_report_cb: QCheckBox

    def __init__(self) -> None:
        """Builds the 3D viewport and the control panel, and initializes the mesh/protection state."""
        super().__init__()
        self.setWindowTitle("Adaptive Remesher — prototype")
        self.resize(1300, 800)

        self._panel_state = self._load_panel_state()

        self.original_mesh: pv.PolyData | None = None
        self.decimated_mesh: pv.PolyData | None = None
        self.texture: pv.Texture | None = None
        self.maps: MaterialMaps | None = None  # every map of the loaded file's material
        self.baked_normal: np.ndarray | None = None  # normal map baked for the decimated result
        self.result_maps: MaterialMaps | None = None  # full maps of a re-atlased result
        self._tangent_cache: tuple | None = None
        self._map_texture_cache: dict = {}
        # True until the user ticks or unticks "Protect UV seams" (or loads a preset): while it is,
        # the option follows the loaded mesh (see `_apply_seam_policy`).
        self._seam_auto = True
        self.protected_mask: np.ndarray | None = None
        self.point_kdtree: cKDTree | None = None
        self.protected_actor = None
        self._is_painting = False
        self._paint_picker = vtk.vtkPointPicker()
        self._paint_picker.SetTolerance(0.005)
        self._undo_stack: list[dict] = []
        self._redo_stack: list[dict] = []
        self._max_history = 15
        self._last_run_info: dict | None = None
        self._rect_origin: QPoint | None = None
        self._rect_erase = False
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(150)
        self._preview_timer.timeout.connect(self._update_live_preview)
        # Single-slot caches (curvature, UV seams) to avoid repeating
        # expensive computations on the same mesh on every redraw or on
        # every live-preview tick: they invalidate themselves as soon as
        # the mesh's identity changes (new load, cleanup, undo/redo...).
        self._curvature_cache: tuple | None = None
        self._uv_seam_cache: tuple | None = None
        self._last_brush_point: np.ndarray | None = None
        # Tracks what show_mesh most recently drew, so the curvature-map
        # checkbox can be disabled while it wouldn't have any visible effect
        # (see _update_curvature_availability).
        self._viewing_original = False

        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.plotter = QtInteractor(self)
        # pyvista wraps set_background with a decorator that the type stubs
        # don't reflect well (see the same pattern in ui/common.py).
        self.plotter.set_background("#24262c")  # type: ignore[arg-type]
        # Intercepts the mouse at the Qt level, before it reaches VTK: this
        # is the only reliable way to stop the camera style from consuming
        # the drag while painting/erasing (VTK observers alone aren't
        # enough, see _on_brush_toggled/eventFilter).
        self.plotter.interactor.installEventFilter(self)
        self._rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self.plotter.interactor)

        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(14, 14, 14, 14)
        panel_layout.setSpacing(10)

        # The panel no longer fits entirely in a normal-sized window (tested:
        # it needs ~1150px of height), so it lives inside a scroll area so
        # that no control — not even the "Apply decimation" button — becomes
        # unreachable on smaller screens.
        scroll = QScrollArea()
        scroll.setWidget(panel)
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(260)
        scroll.setMaximumWidth(700)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # A splitter instead of a fixed width: the edge between the viewport
        # and the panel can be dragged with the mouse to make the panel
        # wider or narrower (within 260-700px), instead of being locked to
        # 336px.
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(6)
        splitter.addWidget(self.plotter.interactor)
        splitter.addWidget(scroll)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([self.width() - 336, 336])
        layout.addWidget(splitter)

        build_header(self, panel_layout)
        build_presets_panel(self, panel_layout)

        self.stats_label = QLabel("No mesh loaded")
        self.stats_label.setWordWrap(True)
        panel_layout.addWidget(self.stats_label)

        build_clean_panel(self, panel_layout)
        build_view_panel(self, panel_layout)

        build_protection_panel(self, panel_layout)
        build_decimation_panel(self, panel_layout)
        build_validation_panel(self, panel_layout)
        build_action_buttons(self, panel_layout)
        build_batch_panel(self, panel_layout)

        panel_layout.addStretch(1)
        for toggle in (
            self.auto_error_cb,
            self.uv_attribute_metric_cb,
            self.normals_weight_cb,
            self.close_holes_cb,
        ):
            toggle.toggled.connect(self._refresh_availability)
        self._update_curvature_availability()
        self._refresh_availability()

    # --- collapsible sections ---

    def _load_panel_state(self) -> dict:
        """Loads which panel sections were collapsed/expanded in the
        previous session from `PANEL_STATE_PATH`. If the file doesn't exist
        or is corrupt, nothing is collapsed by default (everything
        expanded)."""
        try:
            with open(PANEL_STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_panel_state(self):
        """Saves which panel sections are collapsed/expanded, to restore it next time the app is opened."""
        try:
            with open(PANEL_STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(self._panel_state, f)
        except Exception:
            pass

    def _add_collapsible_group(self, key: str, title: str) -> CollapsibleGroupBox:
        """Creates a `CollapsibleGroupBox` restoring its saved
        collapsed/expanded state (expanded by default if nothing is saved
        for `key`), and wires it to persist any change the user makes by
        collapsing or expanding it."""
        expanded = self._panel_state.get(key, True)
        group = CollapsibleGroupBox(title, expanded=expanded)
        group.toggled.connect(lambda checked, k=key: self._on_panel_toggled(k, checked))
        return group

    def _on_panel_toggled(self, key: str, expanded: bool):
        """Immediately saves the new collapsed/expanded state of a panel section."""
        self._panel_state[key] = expanded
        self._save_panel_state()

    # --- undo / redo ---

    def _snapshot(self) -> dict:
        """Saves the state relevant to the undo/redo history. The original
        and decimated meshes are NOT copied (only the object reference is
        saved): no app operation mutates them in place once published to
        self.original_mesh/decimated_mesh — cleaning or decimating always
        creates a new pv.PolyData object instead of mutating the existing
        one — so a deep copy would be wasted work and, on large meshes, a
        noticeable cost that used to be paid on every brush stroke (one
        `_push_undo` per click). Only `protected_mask` is an array that
        IS mutated in place (the brush writes directly into it), which is
        why it's the only one that needs copying."""
        return {
            "original_mesh": self.original_mesh,
            "protected_mask": self.protected_mask.copy() if self.protected_mask is not None else None,
            "decimated_mesh": self.decimated_mesh,
            "baked_normal": self.baked_normal,
            "result_maps": self.result_maps,
        }

    def _push_undo(self):
        """Saves the current state onto the undo stack, before an action modifies it; invalidates the redo stack."""
        if self.original_mesh is None:
            return
        self._undo_stack.append(self._snapshot())
        if len(self._undo_stack) > self._max_history:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._update_undo_redo_buttons()

    def _restore_snapshot(self, state: dict):
        """Applies a saved state (from undo or redo) and refreshes the view, the stats, and the protection overlay."""
        self.original_mesh = state["original_mesh"]
        self.protected_mask = state["protected_mask"]
        self.decimated_mesh = state["decimated_mesh"]
        self.baked_normal = state.get("baked_normal")
        self.result_maps = state.get("result_maps")
        self.point_kdtree = (
            cKDTree(self.original_mesh.points) if self.original_mesh is not None else None
        )
        if self.original_mesh is not None:
            shown = self.decimated_mesh if self.decimated_mesh is not None else self.original_mesh
            self.show_mesh(shown, is_original=self.decimated_mesh is None)
            self.update_stats(shown)
        self._refresh_protection_overlay()
        self._update_undo_redo_buttons()

    def undo(self):
        """Steps back to the most recent saved state, moving the current state onto the redo stack."""
        if not self._undo_stack:
            return
        self._redo_stack.append(self._snapshot())
        self._restore_snapshot(self._undo_stack.pop())

    def redo(self):
        """Redoes the last undone action, moving the current state onto the undo stack."""
        if not self._redo_stack:
            return
        self._undo_stack.append(self._snapshot())
        self._restore_snapshot(self._redo_stack.pop())

    def _update_undo_redo_buttons(self):
        """Enables/disables the undo/redo buttons depending on whether there's anything to undo/redo."""
        self.undo_btn.setEnabled(bool(self._undo_stack))
        self.redo_btn.setEnabled(bool(self._redo_stack))

    # --- loading / cleanup ---

    def load_mesh(self):
        """Opens a file dialog, loads the chosen mesh, cleans it if applicable, and displays it."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load mesh",
            "",
            "3D meshes (*.obj *.stl *.ply *.vtp *.glb *.gltf)",
        )
        if not path:
            return

        try:
            with busy_cursor():
                clean = self._gather_clean_settings() if self.auto_clean_cb.isChecked() else None
                mesh = load_and_prepare_mesh(path, clean)
        except Exception as exc:
            QMessageBox.critical(self, "Error loading mesh", str(exc))
            return

        self.original_mesh = mesh
        self.decimated_mesh = None
        self.maps = load_material_maps(path)
        self.baked_normal = None
        self.result_maps = None
        if self.maps is not None and self.maps.base is not None:
            self.texture = map_to_texture(self.maps.base)
        else:
            self.texture = load_base_color_texture(path)
        self._reset_protection(rebuild_kdtree=True)
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._update_undo_redo_buttons()
        self.show_mesh(mesh, is_original=True)
        self.update_stats(mesh)
        self._apply_seam_policy(mesh)

    def clean_now(self):
        """Re-cleans the current mesh with the configured fragment threshold and resets protection."""
        if self.original_mesh is None:
            return
        try:
            with busy_cursor():
                clean = self._gather_clean_settings()
                cleaned = clean_mesh(
                    self.original_mesh,
                    clean.min_component_pct,
                    clean.close_holes,
                    clean.max_hole_size,
                    clean.fix_orientation,
                )
                if cleaned.n_points == 0 or cleaned.n_cells == 0:
                    raise ValueError(
                        "Cleanup left the mesh empty (fragment threshold "
                        "too high for this model)."
                    )
        except Exception as exc:
            QMessageBox.critical(self, "Error cleaning mesh", str(exc))
            return
        self._push_undo()
        self.original_mesh = cleaned
        self.decimated_mesh = None
        self.baked_normal = None
        self.result_maps = None
        self._reset_protection(rebuild_kdtree=True)
        self.show_mesh(cleaned, is_original=True)
        self.update_stats(cleaned)
        self._apply_seam_policy(cleaned)

    # --- manual protection (paint/erase) ---

    def _on_brush_toggled(self, button: QPushButton, checked: bool):
        """Activates at most one protection tool at a time (paint, erase or
        rectangle); if no mesh is loaded, cancels the activation. If an
        already-applied decimated result is being viewed (or a live
        preview), switches the view to the original mesh before allowing
        it to operate: the protection tools work against
        `self.original_mesh` (they look up the nearest point in its
        KDTree/z-buffer), and after decimating (especially with the QEM
        engine, which does move vertices) that would no longer match what's
        displayed. This only changes the view, without discarding
        `self.decimated_mesh` or spending an undo step: if the user just
        wanted to review or add protection and ends up disabling the brush
        without reapplying decimation, the already-computed result is
        preserved instead of forcing it to be redone from scratch."""
        if checked and self.original_mesh is None:
            button.setChecked(False)
            return
        if checked and (self.decimated_mesh is not None or self.live_preview_cb.isChecked()):
            # Invariant guaranteed by the "return" above: if we get here
            # with checked=True, original_mesh is not None.
            assert self.original_mesh is not None
            self.show_mesh(
                self.original_mesh, is_original=True, preserve_camera=self.plotter.camera_position
            )
            self._refresh_protection_overlay()
        if checked:
            for other in (self.paint_btn, self.erase_btn, self.rect_btn):
                if other is not button:
                    other.setChecked(False)
        logger.debug("BRUSH %s %s", button.text(), "ON" if checked else "OFF")
        self._is_painting = False
        self._rubber_band.hide()

    def eventFilter(self, obj, event):
        """Intercepts the mouse over the 3D viewport while a protection tool
        is active (paint/erase brush, or rectangle selection), so it
        operates on the mesh instead of rotating the camera."""
        if obj is not self.plotter.interactor or self.original_mesh is None:
            return super().eventFilter(obj, event)

        et = event.type()
        # Only reached for actual mouse events: the `et ==` checks below only
        # ever pass for MouseButtonPress/Move/Release, and non-mouse events
        # fall through to super().eventFilter() without touching `mouse_event`.
        mouse_event = cast(QMouseEvent, event)
        brush_active = self.paint_btn.isChecked() or self.erase_btn.isChecked()
        if brush_active:
            if et == QEvent.Type.MouseButtonPress and mouse_event.button() == Qt.MouseButton.LeftButton:
                self._push_undo()  # one undo step per stroke, not per movement
                self._is_painting = True
                self._last_brush_point = None
                self._apply_brush_at_qt_pos(mouse_event.position())
                return True
            if et == QEvent.Type.MouseMove and self._is_painting:
                self._apply_brush_at_qt_pos(mouse_event.position())
                return True
            if et == QEvent.Type.MouseButtonRelease and mouse_event.button() == Qt.MouseButton.LeftButton:
                self._is_painting = False
                return True
            return super().eventFilter(obj, event)

        if self.rect_btn.isChecked():
            if et == QEvent.Type.MouseButtonPress and mouse_event.button() == Qt.MouseButton.LeftButton:
                self._push_undo()  # one undo step per rectangle
                self._rect_origin = mouse_event.position().toPoint()
                self._rect_erase = bool(mouse_event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                self._rubber_band.setGeometry(QRect(self._rect_origin, self._rect_origin))
                self._rubber_band.show()
                return True
            if et == QEvent.Type.MouseMove and self._rubber_band.isVisible() and self._rect_origin is not None:
                rect = QRect(self._rect_origin, mouse_event.position().toPoint()).normalized()
                self._rubber_band.setGeometry(rect)
                return True
            if (
                et == QEvent.Type.MouseButtonRelease
                and mouse_event.button() == Qt.MouseButton.LeftButton
                and self._rubber_band.isVisible()
                and self._rect_origin is not None
            ):
                rect = QRect(self._rect_origin, mouse_event.position().toPoint()).normalized()
                self._rubber_band.hide()
                self._apply_rect_selection(rect)
                return True

        return super().eventFilter(obj, event)

    def _apply_brush_at_qt_pos(self, qt_pos):
        """Converts a Qt position (pixels from top-left) to VTK coordinates and applies the brush there."""
        height = self.plotter.interactor.height()
        x, y = qt_pos.x(), height - qt_pos.y()
        self._paint_picker.Pick(x, y, 0, self.plotter.renderer)
        if self._paint_picker.GetPointId() < 0:
            return
        self._apply_brush(self._paint_picker.GetPickPosition())

    def _apply_brush(self, point):
        """Marks or unmarks as protected the points within the brush radius
        around `point`. If `point` is very close (less than 15% of the
        radius) to the last point applied in the same stroke, it's skipped:
        the result would be practically the same circle of already-marked
        points, and on large meshes repeating the KDTree query and the
        overlay redraw on every micro-movement of the mouse (several times
        a second while dragging) is an unnecessary cost."""
        # Only called (via _apply_brush_at_qt_pos) from eventFilter while a
        # brush tool is active, which requires a loaded mesh and therefore a
        # built point_kdtree (see _reset_protection).
        assert self.original_mesh is not None
        assert self.point_kdtree is not None
        assert self.protected_mask is not None
        radius = (self.brush_slider.value() / 100.0) * self.original_mesh.length
        point = np.asarray(point, dtype=float)
        if (
            self._last_brush_point is not None
            and np.linalg.norm(point - self._last_brush_point) < radius * 0.15
        ):
            return
        self._last_brush_point = point
        nearby = self.point_kdtree.query_ball_point(point, r=radius)
        if not nearby:
            return
        self.protected_mask[nearby] = not self.erase_btn.isChecked()
        self._refresh_protection_overlay()

    def _project_points_to_qt(self, points: np.ndarray):
        """Projects 3D world points to Qt pixel coordinates (top-left
        origin) using the current camera matrix, for rectangle selection.
        Vectorized with numpy instead of calling vtkCoordinate point by
        point, which would be too slow on meshes with thousands of
        vertices. Returns (xy, in_front): `in_front` flags the points in
        front of the camera (w>0 after perspective projection), the only
        ones with a valid screen position."""
        renderer = self.plotter.renderer
        camera = renderer.GetActiveCamera()
        width = max(self.plotter.interactor.width(), 1)
        height = max(self.plotter.interactor.height(), 1)
        vtk_matrix = camera.GetCompositeProjectionTransformMatrix(width / height, -1, 1)
        matrix = np.array(
            [[vtk_matrix.GetElement(i, j) for j in range(4)] for i in range(4)]
        )
        homogeneous = np.hstack([points, np.ones((points.shape[0], 1))])
        clip = homogeneous @ matrix.T
        w = clip[:, 3]
        in_front = w > 1e-8
        safe_w = np.where(in_front, w, 1.0)
        ndc = clip[:, :3] / safe_w[:, None]
        display_x = (ndc[:, 0] + 1.0) / 2.0 * width
        display_y_bottom_up = (ndc[:, 1] + 1.0) / 2.0 * height
        qt_y = height - display_y_bottom_up
        return np.column_stack([display_x, qt_y]), in_front

    def _apply_rect_selection(self, rect: QRect):
        """Protects (or unprotects, if dragged with Shift held) the original
        vertices whose screen projection falls inside `rect` (Qt
        coordinates) and that are also visible from the current camera. The
        screen projection (`_project_points_to_qt`) is a cheap prefilter to
        narrow down to a handful of candidates out of all the vertices;
        over those candidates, `vtkSelectVisiblePoints` does the real depth
        test against the already-rendered z-buffer, discarding any that are
        hidden behind another part of the mesh."""
        if rect.width() < 3 or rect.height() < 3:
            return
        # Only called from eventFilter when rect_btn is active, which
        # _on_brush_toggled already guarantees requires a loaded mesh.
        assert self.original_mesh is not None
        assert self.protected_mask is not None
        xy, in_front = self._project_points_to_qt(self.original_mesh.points)
        inside = (
            in_front
            & (xy[:, 0] >= rect.left()) & (xy[:, 0] <= rect.right())
            & (xy[:, 1] >= rect.top()) & (xy[:, 1] <= rect.bottom())
        )
        candidate_idx = np.where(inside)[0]
        if candidate_idx.size == 0:
            return

        candidates = pv.PolyData(self.original_mesh.points[candidate_idx])
        candidates.point_data["_orig_idx"] = candidate_idx
        self.plotter.render()  # ensures the z-buffer reflects the latest view before reading it
        visible_filter = vtk.vtkSelectVisiblePoints()
        visible_filter.SetInputData(candidates)
        visible_filter.SetRenderer(self.plotter.renderer)
        visible_filter.Update()
        visible = pv.wrap(visible_filter.GetOutput())
        if visible.n_points == 0:
            return

        visible_idx = np.asarray(visible.point_data["_orig_idx"], dtype=np.int64)
        self.protected_mask[visible_idx] = not self._rect_erase
        self._refresh_protection_overlay()

    def clear_protection(self):
        """Clears all protection marks on the current mesh."""
        self._push_undo()
        self._reset_protection(rebuild_kdtree=False)

    def _reset_protection(self, rebuild_kdtree: bool):
        """Resets the protection mask (and optionally the point KDTree) for the current mesh."""
        if self.original_mesh is not None:
            self.protected_mask = np.zeros(self.original_mesh.n_points, dtype=bool)
            if rebuild_kdtree:
                self.point_kdtree = cKDTree(self.original_mesh.points)
        else:
            self.protected_mask = None
            self.point_kdtree = None
        if self.protected_actor is not None:
            self.plotter.remove_actor(self.protected_actor)
            self.protected_actor = None
        self.protected_count_label.setText("Protected points: 0")

    def _refresh_protection_overlay(self):
        """Redraws the protected points in red over the mesh, without touching the current camera."""
        if self.protected_actor is not None:
            self.plotter.remove_actor(self.protected_actor)
            self.protected_actor = None
        count = int(self.protected_mask.sum()) if self.protected_mask is not None else 0
        if count:
            assert self.original_mesh is not None
            assert self.protected_mask is not None
            pts = self.original_mesh.points[self.protected_mask]
            cloud = pv.PolyData(pts)
            self.protected_actor = self.plotter.add_mesh(
                cloud,
                color="#ff4d4d",
                point_size=10,
                render_points_as_spheres=True,
                pickable=False,
                reset_camera=False,
            )
        self.protected_count_label.setText(f"Protected points: {count}")

    def export_protection_mask(self):
        """Saves the current protection mask (marked vertex indices) to a
        .json, to reuse it in another session or on another mesh with the
        same topology (same number and order of vertices, e.g. variants of
        the same model)."""
        if self.protected_mask is None:
            QMessageBox.information(self, "Export mask", "No mesh is loaded.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export protection mask", "", "Protection mask (*.json)"
        )
        if not path:
            return
        try:
            data = {
                "n_points": int(self.protected_mask.size),
                "protected_indices": np.where(self.protected_mask)[0].tolist(),
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception as exc:
            QMessageBox.critical(self, "Error exporting mask", str(exc))

    def import_protection_mask(self):
        """Loads a saved protection mask and applies it to the current mesh,
        replacing the existing marks. Only works if the number of vertices
        matches exactly the one from when it was saved (same mesh or a
        variant with identical topology); if it doesn't match, warns and
        changes nothing."""
        if self.original_mesh is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Import protection mask", "", "Protection mask (*.json)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            n_points = data.get("n_points")
            indices = np.array(data.get("protected_indices", []), dtype=int)
        except Exception as exc:
            QMessageBox.critical(self, "Error importing mask", str(exc))
            return
        if n_points != self.original_mesh.n_points:
            QMessageBox.warning(
                self,
                "Incompatible mask",
                f"The mask has {n_points} saved vertices and the current "
                f"mesh has {self.original_mesh.n_points}: the topology "
                "doesn't match, nothing was applied.",
            )
            return
        self._push_undo()
        self.protected_mask = np.zeros(self.original_mesh.n_points, dtype=bool)
        if indices.size:
            self.protected_mask[indices] = True
        self._refresh_protection_overlay()

    # --- visualization ---

    def _curvature_for(self, mesh: pv.PolyData) -> np.ndarray:
        """Returns the mean curvature of `mesh`, cached by the object's
        identity (plus its point count, to rule out an id() collision with
        an already-freed mesh). Without this, turning on the curvature map
        or simply toggling another view option that redraws while
        preserving the camera would recompute the curvature of the whole
        mesh every time, a noticeable cost on large meshes."""
        key = (id(mesh), mesh.n_points)
        if self._curvature_cache is not None and self._curvature_cache[0] == key:
            return self._curvature_cache[1]
        curvature = mesh.curvature("mean")
        self._curvature_cache = (key, curvature)
        return curvature

    def _uv_seam_mask_for(self, mesh: pv.PolyData) -> np.ndarray:
        """Returns the UV-seam mask of `mesh`, cached the same way as
        `_curvature_for`. Used by the live preview, which would otherwise
        recompute this mask on every tick even if the mesh hadn't changed
        since the last one; on AI-generated meshes with many seams this was
        noticeable while dragging the aggressiveness slider with live
        preview on. 'Apply decimation' instead uses `remesh_core`'s shared
        pipeline, which recomputes the mask once per click (not a
        repetitive use case that needs caching)."""
        key = (id(mesh), mesh.n_points)
        if self._uv_seam_cache is not None and self._uv_seam_cache[0] == key:
            return self._uv_seam_cache[1]
        seam_mask = uv_seam_point_mask(mesh)
        self._uv_seam_cache = (key, seam_mask)
        return seam_mask

    def show_mesh(self, mesh: pv.PolyData, is_original: bool, preserve_camera=None):
        """Clears the viewport and displays `mesh`. If `is_original` and the
        curvature map is on, colors by mean curvature instead of the usual
        solid gray. Reframes the camera unless `preserve_camera` is passed
        (a previous position to restore, so zoom isn't lost when just
        switching display mode)."""
        self.plotter.clear()
        edges = self.show_edges_cb.isChecked()
        if is_original and self.curvature_cb.isChecked():
            curvature = self._curvature_for(mesh)
            self.plotter.add_mesh(
                mesh,
                scalars=curvature,
                cmap="coolwarm",
                show_edges=edges,
                edge_color="#101114",
                scalar_bar_args={"color": "#e4e6eb"},
            )
        elif self.show_normals_cb.isChecked() and self._pbr_maps_for(mesh) is not None:
            self._add_material_mapped_mesh(mesh, self._pbr_maps_for(mesh), edges)
        elif (
            self.show_texture_cb.isChecked()
            and self._texture_for(mesh) is not None
            and mesh_has_texture_coords(mesh)
        ):
            self.plotter.add_mesh(mesh, texture=self._texture_for(mesh), show_edges=edges, edge_color="#101114")
        else:
            self.plotter.add_mesh(mesh, show_edges=edges, color="#d8dadd", edge_color="#101114")
        if preserve_camera is not None:
            # camera_position, like set_background/reset_camera, is a
            # pyvista descriptor the type stubs don't reflect well.
            self.plotter.camera_position = preserve_camera  # type: ignore[call-arg]
        else:
            self.plotter.reset_camera()  # type: ignore[call-arg]
        self.protected_actor = None
        self._viewing_original = is_original
        self._update_curvature_availability()
        self._refresh_availability()

    def _maps_in_use(self, mesh: pv.PolyData) -> MaterialMaps | None:
        """The material maps that belong on `mesh`: the full set of a
        re-atlased result when `mesh` is that result, else the loaded file's."""
        if mesh is self.decimated_mesh and self.result_maps is not None:
            return self.result_maps
        return self.maps

    def _texture_for(self, mesh: pv.PolyData) -> pv.Texture | None:
        """The colour texture to draw on `mesh` (None if it has none)."""
        if mesh is self.decimated_mesh and self.result_maps is not None:
            return self._texture_of(self.result_maps.base) if self.result_maps.base is not None else None
        return self.texture

    def _on_seam_option_clicked(self, _checked: bool):
        """The user chose; from now on the loaded mesh no longer decides."""
        self._seam_auto = False

    def _apply_seam_policy(self, mesh: pv.PolyData):
        """While the options are still automatic, sets the UV-seam options for
        the loaded mesh: seams are not frozen, and the faces are cut along them
        when they cover less than `SEAM_FREEZE_LIMIT` of the vertices (the colour
        error of protected seams at a higher reduction); on a fragmented atlas
        neither is used, since both only cost triangles or reduction. Says so in
        the stats; ticking or unticking either option afterwards is respected."""
        if not self._seam_auto or not mesh_has_texture_coords(mesh):
            return
        fraction = seam_vertex_fraction(mesh)
        cut = should_cut_uv_seams(mesh)
        self.protect_uv_seams_cb.setChecked(False)
        self.cut_seams_cb.setChecked(cut)
        how = (
            "the faces that cross a seam are cut along it after decimating"
            if cut else
            "neither freezing nor cutting them is used, since a fragmented atlas would lose reduction or gain triangles"
        )
        self.stats_label.setText(
            self.stats_label.text()
            + f"\nUV seams cover {100 * fraction:.0f}% of the vertices: {how}. "
            "\"Protect UV seams\" and \"Cut faces at UV seams\" can be ticked to override."
        )

    def _normal_map_for(self, mesh: pv.PolyData) -> np.ndarray | None:
        """The normal map that belongs on `mesh`: the one baked for the
        decimated result if there is one, else the loaded file's own. None
        without texture coordinates or a map."""
        maps = self._maps_in_use(mesh)
        if maps is None or not mesh_has_texture_coords(mesh):
            return None
        if mesh is self.decimated_mesh and self.baked_normal is not None and self.result_maps is None:
            return self.baked_normal
        return maps.normal

    def _pbr_maps_for(self, mesh: pv.PolyData) -> dict | None:
        """The material maps the viewer can draw on `mesh` (normal, packed
        occlusion-roughness-metallic, emissive); None if there are none or the
        mesh has no UV."""
        maps = self._maps_in_use(mesh)
        if maps is None or not mesh_has_texture_coords(mesh):
            return None
        found = {
            "normal": self._normal_map_for(mesh),
            "orm": orm_image(maps),
            "emissive": maps.emissive,
        }
        found = {k: v for k, v in found.items() if v is not None}
        return found or None

    def _texture_of(self, image: np.ndarray) -> pv.Texture:
        """A pyvista texture for `image`, cached by identity (4k maps are slow to upload every redraw)."""
        cache = self._map_texture_cache
        entry = cache.get(id(image))
        if entry is None or entry[0] is not image:
            if len(cache) >= 8:
                cache.clear()
            entry = (image, map_to_texture(image))
            cache[id(image)] = entry  # the entry keeps `image` alive, so its id stays unique
        return entry[1]

    def _albedo_texture(self, texture: pv.Texture) -> pv.Texture:
        """`texture` flagged as sRGB, which PBR shading expects (the plain view
        must keep using the unflagged one, or it darkens)."""
        key = ("srgb", id(texture))
        if self._map_texture_cache.get("albedo_key") != key:
            albedo = pv.Texture(texture.to_array())
            albedo.SetUseSRGBColorSpace(True)
            self._map_texture_cache["albedo_key"] = key
            self._map_texture_cache["albedo"] = albedo
            self._map_texture_cache["albedo_source"] = texture  # keeps the id valid
        return self._map_texture_cache["albedo"]

    def _add_material_mapped_mesh(self, mesh: pv.PolyData, found: dict, edges: bool):
        """Draws `mesh` with VTK's PBR shading and the maps in `found`. PBR needs
        tangents on the mesh (cached per mesh) for a normal map to have any effect."""
        key = (id(mesh), mesh.n_points)
        if self._tangent_cache is None or self._tangent_cache[0] != key:
            self._tangent_cache = (key, with_tangents(mesh))
        maps = self._maps_in_use(mesh)
        factors = maps.factors if maps is not None else {}
        texture = self._texture_for(mesh)
        has_orm = "orm" in found
        # With a packed map the factors scale it (glTF: 1.0 when unspecified).
        metallic = factors.get("metallicFactor")
        roughness = factors.get("roughnessFactor")
        look = {"color": "#d8dadd"} if texture is None else {}
        actor = self.plotter.add_mesh(
            self._tangent_cache[1],
            pbr=True,
            metallic=(1.0 if metallic is None else metallic) if has_orm else (metallic or 0.0),
            roughness=(1.0 if roughness is None else roughness) if has_orm else (roughness or 0.6),
            show_edges=edges,
            edge_color="#101114",
            **look,
        )
        if texture is not None:
            actor.prop.SetBaseColorTexture(self._albedo_texture(texture))
        if "normal" in found:
            actor.prop.SetNormalTexture(self._texture_of(found["normal"]))
            actor.prop.SetNormalScale(factors.get("normalScale") or 1.0)
        if has_orm:
            actor.prop.SetORMTexture(self._texture_of(found["orm"]))
            actor.prop.SetOcclusionStrength(factors.get("occlusionStrength") or 1.0)
        if "emissive" in found:
            actor.prop.SetEmissiveTexture(self._texture_of(found["emissive"]))
            emissive = factors.get("emissiveFactor") or [1.0, 1.0, 1.0]
            actor.prop.SetEmissiveFactor(*emissive[:3])

    def _refresh_availability(self, *_args):
        """Disables every control that cannot be used in the current state
        and makes its tooltip say why, instead of leaving it enabled and
        silently doing nothing (or failing with a dialog). The reason
        replaces the control's usual tooltip until it becomes available."""
        has_mesh = self.original_mesh is not None
        has_result = self.decimated_mesh is not None
        no_mesh = "Load a mesh first."
        has_uv = has_mesh and mesh_has_texture_coords(self.original_mesh)
        if not has_mesh:
            uv_reason: Optional[str] = no_mesh
        elif not has_uv:
            uv_reason = (
                "The loaded mesh has no texture coordinates (UV). If the "
                "file has a texture, cleaning discards it: turn off "
                "\"Clean automatically on load\" and load the file again."
            )
        else:
            uv_reason = None
        set_available(self.protect_uv_seams_cb, uv_reason)
        set_available(self.cut_seams_cb, uv_reason)
        set_available(self.uv_attribute_metric_cb, uv_reason)
        uv_cost_on = self.uv_attribute_metric_cb.isChecked()
        uv_weight_reason = uv_reason or (
            None if uv_cost_on else "Turn on \"Penalize UV distortion in the cost\" first."
        )
        for w in (self.uv_weight_label, self.uv_weight_spin):
            set_available(w, uv_weight_reason)

        normals_reason = None if self.normals_weight_cb.isChecked() else (
            "Turn on \"Preserve normals\" first."
        )
        for w in (self.normals_weight_label, self.normals_weight_spin):
            set_available(w, normals_reason)

        holes_reason = None if self.close_holes_cb.isChecked() else (
            "Turn on \"Fill small holes\" first."
        )
        for w in (self.hole_size_label, self.max_hole_size_spin):
            set_available(w, holes_reason)

        auto_reason = None if self.auto_error_cb.isChecked() else (
            "Turn on \"Auto-protect high-error areas\" first."
        )
        for w in (
            self.error_threshold_label,
            self.error_threshold_spin,
            self.error_max_iterations_label,
            self.error_max_iterations_spin,
            self.lower_reduction_cb,
        ):
            set_available(w, auto_reason)

        if not has_mesh:
            texture_reason: Optional[str] = no_mesh
        elif self._texture_for(self.decimated_mesh if has_result else self.original_mesh) is None:
            texture_reason = (
                "The loaded file has no texture image the viewer can read "
                "(supported: the base-colour image of a glTF/GLB, or the "
                "map_Kd image of an OBJ)."
            )
        elif not has_uv:
            texture_reason = (
                "This mesh has no texture coordinates (UV). If the file has "
                "a texture, cleaning discards it: turn off \"Clean "
                "automatically on load\" and load the file again."
            )
        elif self.curvature_cb.isChecked() and self._viewing_original:
            texture_reason = "Not available while the curvature map is on."
        else:
            texture_reason = None
        set_available(self.show_texture_cb, texture_reason)

        shown = self.decimated_mesh if has_result else self.original_mesh
        if not has_mesh:
            normals_reason: Optional[str] = no_mesh
        elif not mesh_has_texture_coords(shown):
            normals_reason = "This mesh has no texture coordinates (UV), which material maps need."
        elif self._pbr_maps_for(shown) is None:
            normals_reason = (
                "This model has no normal, roughness/metallic, occlusion or "
                "emissive map. Decimate it and press \"Bake normal map\" to "
                "create a normal map."
            )
        elif self.curvature_cb.isChecked() and self._viewing_original:
            normals_reason = "Not available while the curvature map is on."
        else:
            normals_reason = None
        set_available(self.show_normals_cb, normals_reason)

        if not has_mesh:
            bake_reason: Optional[str] = no_mesh
        elif not has_result:
            bake_reason = "Apply a decimation first."
        elif not (has_uv and mesh_has_texture_coords(self.decimated_mesh)):
            bake_reason = "Baking a normal map needs the model to have texture coordinates (UV)."
        else:
            bake_reason = None
        set_available(self.bake_btn, bake_reason)
        set_available(self.bake_size_combo, bake_reason)
        if bake_reason is None and not xatlas_available():
            rebake_reason: Optional[str] = (
                "Needs the optional xatlas package (pip install xatlas; it is built "
                "from source and needs a C++ compiler on Windows)."
            )
        else:
            rebake_reason = bake_reason
        set_available(self.rebake_btn, rebake_reason)

        mesh_reason = None if has_mesh else no_mesh
        for w in (
            self.apply_btn,
            self.export_btn,
            self.paint_btn,
            self.erase_btn,
            self.rect_btn,
            self.clear_marks_btn,
            self.mask_export_btn,
            self.mask_import_btn,
            self.live_preview_cb,
        ):
            set_available(w, mesh_reason)
        result_reason = mesh_reason or (
            None if has_result else "Apply a decimation first."
        )
        set_available(self.reset_btn, result_reason)
        set_available(self.compare_btn, result_reason)

    def _update_curvature_availability(self):
        """Enables the curvature-map checkbox only while the original mesh
        is actually what's on screen: coloring by curvature has no visible
        effect while Live preview is on (it keeps redrawing the preview
        mesh) or after 'Apply decimation' (the applied result is shown
        instead), until 'Restore original' or a protection brush switches
        the view back — both cases previously left the checkbox checked
        but silently inert, which was confusing. Disabling it also makes
        the incompatibility with those other options visible instead of
        a no-op."""
        can_apply = self.original_mesh is not None and self._viewing_original
        self.curvature_cb.setEnabled(can_apply)
        if can_apply:
            reason = (
                "Colors the original mesh according to its mean curvature "
                "(blue/red = flat/curved areas), to see at a glance where it "
                "makes sense to paint protected zones before decimating."
            )
        elif self.original_mesh is None:
            reason = "Load a mesh first."
        elif self.live_preview_cb.isChecked():
            reason = "Not available while \"Live preview\" is on."
        else:
            reason = (
                "Not available while viewing the decimated result. "
                "Use \"Restore original\" or a protection brush to view "
                "the original mesh again."
            )
        self.curvature_cb.setToolTip(tip(reason))

    def _on_curvature_toggled(self, _checked: bool):
        """Redraws the current mesh (without resetting the camera) when the curvature map is turned on or off."""
        if self.original_mesh is None:
            return
        shown = self.decimated_mesh if self.decimated_mesh is not None else self.original_mesh
        cam = self.plotter.camera_position
        self.show_mesh(shown, is_original=self.decimated_mesh is None, preserve_camera=cam)
        self._refresh_protection_overlay()

    def _on_viewport_bg_changed(self, index: int):
        """Applies the chosen background preset to the 3D viewport and
        redraws immediately, without touching the camera or the mesh."""
        _, color = VIEWPORT_BACKGROUND_PRESETS[index]
        self.plotter.set_background(color)  # type: ignore[arg-type]
        self.plotter.render()

    def _on_live_preview_toggled(self, checked: bool):
        """When enabling live preview, computes it immediately; when
        disabling it, goes back to showing the real state (the already
        applied result, or the original mesh if none has been applied)."""
        if checked:
            self._update_live_preview()
        elif self.original_mesh is not None:
            shown = self.decimated_mesh if self.decimated_mesh is not None else self.original_mesh
            cam = self.plotter.camera_position
            self.show_mesh(shown, is_original=self.decimated_mesh is None, preserve_camera=cam)
            self._refresh_protection_overlay()
            self.update_stats(shown)

    def _schedule_preview(self, *_args):
        """Schedules a live-preview refresh (with a short delay so it
        doesn't recompute on every step while dragging a slider), if it's
        active. Ignores whatever argument Qt's signals pass it (slider
        value, combo index...), since `_update_live_preview` re-reads the
        current state of the controls anyway."""
        if not self.live_preview_cb.isChecked() or self.original_mesh is None:
            return
        self._preview_timer.start()

    def _update_live_preview(self):
        """Computes and displays a fast decimation (without the iterative
        error-based refinement, so it's practically instant) with the
        panel's current parameters, without touching the undo history or
        the already-'applied' result (`self.decimated_mesh` is not
        modified: 'Apply decimation' has to be pressed to confirm and be
        able to export or compare this result)."""
        if not self.live_preview_cb.isChecked() or self.original_mesh is None:
            return
        source = self.original_mesh
        decimation = self._gather_decimation_settings()
        mask = (
            self.protected_mask.copy()
            if self.protected_mask is not None
            else np.zeros(source.n_points, dtype=bool)
        )
        if self.protect_uv_seams_cb.isChecked() and mesh_has_texture_coords(source):
            mask |= self._uv_seam_mask_for(source)
        try:
            preview = decimate_with_mask(source, mask, decimation)
        except Exception:
            return
        if preview.n_cells == 0:
            return
        cam = self.plotter.camera_position
        self.show_mesh(preview, is_original=False, preserve_camera=cam)
        self._refresh_protection_overlay()
        self.stats_label.setText(
            f"Preview: {preview.n_cells} tris "
            "(not applied; no error-based auto-refinement)"
        )

    def update_stats(self, mesh: pv.PolyData):
        """Updates the stats label with `mesh`'s vertex/triangle count."""
        self.stats_label.setText(
            f"Vertices: {mesh.n_points}\nTriangles: {mesh.n_cells}"
        )

    # --- decimation ---

    def _on_engine_changed(self, index: int):
        """Enables each engine's own controls (angle/topology/splitting for
        vtkDecimatePro, or QEM options) depending on the one chosen."""
        is_qem = index == ENGINE_QEM
        pro_only = "Only used by the vtkDecimatePro engine; the current engine is QEM."
        for w in (self.angle_slider, self.preserve_topology_cb, self.splitting_cb):
            set_available(w, pro_only if is_qem else None)
        set_available(
            self.qem_group,
            None if is_qem else "Only used by the QEM engine; the current engine is vtkDecimatePro.",
        )

    def _gather_clean_settings(self) -> CleanSettings:
        """Gathers the pre-cleanup parameters configured in the panel."""
        return CleanSettings(
            min_component_pct=self.min_fragment_slider.value(),
            close_holes=self.close_holes_cb.isChecked(),
            max_hole_size=self.max_hole_size_spin.value(),
            fix_orientation=self.fix_orientation_cb.isChecked(),
        )

    def _gather_decimation_settings(self) -> DecimationSettings:
        """Gathers the decimation parameters (both engines) configured in the panel."""
        return DecimationSettings(
            engine=self.engine_combo.currentIndex(),
            reduction_pct=self.reduction_slider.value(),
            feature_angle=float(self.angle_slider.value()),
            preserve_topology=self.preserve_topology_cb.isChecked(),
            boundary_vertex_deletion=self.boundary_vertex_deletion_cb.isChecked(),
            splitting=self.splitting_cb.isChecked(),
            volume_preservation=self.volume_preservation_cb.isChecked(),
            use_uv_weight=self.uv_attribute_metric_cb.isChecked(),
            tcoords_weight=self.uv_weight_spin.value(),
            use_normals_weight=self.normals_weight_cb.isChecked(),
            normals_weight=self.normals_weight_spin.value(),
        )

    def _gather_protection_settings(self) -> ProtectionSettings:
        """Gathers the automatic protection parameters (UV seams and error validation) from the panel."""
        return ProtectionSettings(
            protect_uv_seams=self.protect_uv_seams_cb.isChecked(),
            auto_error_protection=self.auto_error_cb.isChecked(),
            error_threshold_pct=self.error_threshold_spin.value(),
            error_max_iterations=self.error_max_iterations_spin.value(),
            lower_reduction_if_needed=self.lower_reduction_cb.isChecked(),
        )

    def apply_decimation(self):
        """Decimates the current mesh according to the panel's parameters:
        excludes painted regions and UV seams from the collapse, and if
        error validation is on, refines the result by automatically
        protecting wherever the deformation exceeds the threshold. Delegates
        all the computation to `run_decimation_pipeline` (remesh_core), the
        same pipeline batch processing uses."""
        if self.original_mesh is None:
            return

        decimation = self._gather_decimation_settings()
        protection = self._gather_protection_settings()

        try:
            with busy_cursor():
                result = run_decimation_pipeline(
                    self.original_mesh, decimation, protection, manual_mask=self.protected_mask
                )
        except ValueError as exc:
            QMessageBox.warning(self, "Empty result", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, "Error decimating", str(exc))
            return

        self._push_undo()
        # The pipeline works on bare geometry; give the result the source's UV back.
        self.decimated_mesh = transfer_texture_coords(
            self.original_mesh, result.mesh, cut_seams=self.cut_seams_cb.isChecked()
        )
        self.baked_normal = None  # a new result needs its own bake
        self.result_maps = None
        normal_note = self._auto_normal_map()
        self.show_mesh(self.decimated_mesh, is_original=False)
        # If any protection tool was already active before decimating,
        # disable it: from now on it would work against the decimated
        # result (the current view) while comparing positions with the
        # original mesh's KDTree/z-buffer, which with the QEM engine
        # (moves vertices) would mark points in the wrong place without
        # warning. Reactivating it switches back to the original to keep
        # painting (see _on_brush_toggled), without discarding this result.
        self.paint_btn.setChecked(False)
        self.erase_btn.setChecked(False)
        self.rect_btn.setChecked(False)

        notes = []
        if result.manual_protected_points:
            notes.append("painted regions excluded")
        if result.uv_seam_protected_points:
            notes.append(f"{result.uv_seam_protected_points} UV-seam points protected")
        note = f" ({', '.join(notes)})" if notes else ""
        error_direction_note = (
            f"\n  result→original: max {result.error_max_fwd:.4f} "
            f"| original→result: max {result.error_max_rev:.4f}"
        )
        if protection.auto_error_protection:
            error_note = (
                f"\nGeometric error (post-refinement): max {result.error_max:.4f}, "
                f"mean {result.error_mean:.4f}, p95 {result.error_p95:.4f} "
                f"({result.error_refine_iterations} pass(es), "
                f"+{result.error_refine_added_points} points auto-protected)"
                f"{error_direction_note}"
                + (
                    "\nThe error limit needed a lower reduction: decimated with a request of "
                    f"{result.reduction_lowered_to_pct:.0f}% instead of {decimation.reduction_pct:.0f}%."
                    if result.reduction_lowered_to_pct is not None else ""
                )
            )
        else:
            error_note = (
                f"\nGeometric error: max {result.error_max:.4f}, "
                f"mean {result.error_mean:.4f}, p95 {result.error_p95:.4f}"
                f"{error_direction_note}"
            )
        seam_hint = ""
        if seam_protection_limits_reduction(
            self.reduction_slider.value(),
            result.reduction_real_pct,
            result.uv_seam_protected_points,
            self.original_mesh.n_points,
        ):
            share = 100 * result.uv_seam_protected_points / self.original_mesh.n_points
            seam_hint = (
                f"\nHint: UV-seam protection froze {share:.0f}% of the vertices, "
                "which limits the reduction. Turn off \"Protect UV seams "
                "automatically\" to reduce more; the texture is restored on the "
                "result, but faces next to a seam may look slightly stretched."
            )
        self.stats_label.setText(
            f"Original: {result.original_tris} tris\n"
            f"Result: {result.result_tris} tris\n"
            f"Actual reduction: {result.reduction_real_pct:.1f}% overall, "
            f"{result.unprotected_zone_reduction_pct:.1f}% in the unprotected zone"
            f"{note}{error_note}{seam_hint}{normal_note}"
        )

        self._last_run_info = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "engine": self.engine_combo.currentText(),
            "original_tris": result.original_tris,
            "result_tris": result.result_tris,
            "reduction_target_pct": self.reduction_slider.value(),
            "reduction_real_pct": result.reduction_real_pct,
            "unprotected_zone_tris_before": result.unprotected_zone_tris_before,
            "unprotected_zone_tris_after": result.unprotected_zone_tris_after,
            "unprotected_zone_reduction_pct": result.unprotected_zone_reduction_pct,
            "manual_protected_points": result.manual_protected_points,
            "uv_seam_protected_points": result.uv_seam_protected_points,
            "auto_error_protection_enabled": protection.auto_error_protection,
            "error_max": round(result.error_max, 6),
            "error_mean": round(result.error_mean, 6),
            "error_p95": round(result.error_p95, 6),
            "error_max_fwd": round(result.error_max_fwd, 6),
            "error_mean_fwd": round(result.error_mean_fwd, 6),
            "error_p95_fwd": round(result.error_p95_fwd, 6),
            "error_max_rev": round(result.error_max_rev, 6),
            "error_mean_rev": round(result.error_mean_rev, 6),
            "error_p95_rev": round(result.error_p95_rev, 6),
        }
        if protection.auto_error_protection:
            self._last_run_info.update({
                "error_threshold_pct": protection.error_threshold_pct,
                "error_refine_iterations": result.error_refine_iterations,
                "error_refine_added_points": result.error_refine_added_points,
                "reduction_lowered_to_pct": result.reduction_lowered_to_pct,
            })

    def reset_mesh(self):
        """Discards the decimated result and goes back to showing the original mesh with its protection marks."""
        if self.original_mesh is None:
            return
        self._push_undo()
        self.decimated_mesh = None
        self.baked_normal = None
        self.result_maps = None
        self.show_mesh(self.original_mesh, is_original=True)
        self.update_stats(self.original_mesh)
        self._refresh_protection_overlay()

    def _auto_normal_map(self) -> str:
        """After a decimation, bakes the normal map of the result and keeps it if
        it shades closer to the original than the original's map carried over
        (see `best_normal_map`). Returns a line for the stats ('' if nothing to do)."""
        if (
            not self.auto_bake_cb.isChecked()
            or self.maps is None
            or self.maps.normal is None
            or self.original_mesh is None
            or self.decimated_mesh is None
        ):
            return ""
        try:
            with busy_cursor():
                baked, info = best_normal_map(
                    self.original_mesh, self.decimated_mesh, self.maps, int(self.bake_size_combo.currentText())
                )
        except Exception as exc:
            return f"\nNormal map not baked: {exc}"
        self.baked_normal = baked
        kept = "baked normal map kept" if baked is not None else "original normal map kept"
        return (
            f"\nNormal map: {kept} (shading error {info['baked_mean_deg']:.1f}\u00b0 baked, "
            f"{info['carried_mean_deg']:.1f}\u00b0 carried over)"
        )

    def bake_normal_map_now(self):
        """Bakes a tangent-space normal map for the decimated result from the
        original (and the original's own normal map, if it has one), so the
        shading of the removed detail can be shown again without triangles.
        The result keeps the original's UV, so the model's other maps stay
        valid and only the normal map is replaced."""
        if self.original_mesh is None or self.decimated_mesh is None:
            return
        size = int(self.bake_size_combo.currentText())
        high_map = self.maps.normal if self.maps is not None else None
        scale = (self.maps.factors.get("normalScale") if self.maps is not None else None) or 1.0
        try:
            with busy_cursor():
                baked = bake_normal_map(
                    self.original_mesh,
                    self.decimated_mesh,
                    size=size,
                    high_normal_map=high_map,
                    high_normal_scale=scale,
                )
        except Exception as exc:
            QMessageBox.critical(self, "Error baking the normal map", str(exc))
            return
        self._push_undo()
        self.baked_normal = baked
        self.show_normals_cb.setChecked(True)
        self._on_curvature_toggled(True)  # redraws the current mesh

    def rebake_textures_now(self):
        """Gives the decimated result its own, new texture atlas (xatlas) and
        bakes every map of the model onto it from the original: colour,
        metallic-roughness, occlusion and emissive are resampled, and a new
        normal map is baked. The new layout has no stretched or overlapping
        faces and its size is chosen here, but it needs the optional `xatlas`
        package."""
        if self.original_mesh is None or self.decimated_mesh is None:
            return
        size = int(self.bake_size_combo.currentText())
        source_maps = dataclasses.replace(self.maps) if self.maps is not None else MaterialMaps()
        if source_maps.base is None and self.texture is not None:
            source_maps.base = np.ascontiguousarray(self.texture.to_array())
        try:
            with busy_cursor():
                new_mesh = reparametrize(self.decimated_mesh, size=size)
                new_maps = bake_material_maps(self.original_mesh, new_mesh, source_maps, size=size)
        except Exception as exc:
            QMessageBox.critical(self, "Error re-baking the textures", str(exc))
            return
        self._push_undo()
        self.decimated_mesh = new_mesh
        self.result_maps = new_maps
        self.baked_normal = None
        self.show_mesh(new_mesh, is_original=False)
        self.update_stats(new_mesh)

    def open_comparison(self):
        """Opens the side-by-side comparison dialog; requires a decimation to already have been applied."""
        if self.original_mesh is None or self.decimated_mesh is None:
            QMessageBox.information(
                self,
                "Compare",
                "Apply a decimation first to be able to compare the original and the result.",
            )
            return
        _, bg_color = VIEWPORT_BACKGROUND_PRESETS[self.viewport_bg_combo.currentIndex()]
        dialog = ComparisonDialog(
            self.original_mesh,
            self.decimated_mesh,
            self,
            background=bg_color,
            show_edges=self.show_edges_cb.isChecked(),
            texture=self.texture if self.show_texture_cb.isChecked() else None,
            decimated_texture=self._texture_for(self.decimated_mesh) if self.show_texture_cb.isChecked() else None,
        )
        dialog.exec()

    def _maps_for_export(self, mesh: pv.PolyData) -> MaterialMaps | None:
        """The material maps that go with `mesh`: the loaded file's maps, with
        the baked normal map replacing the original's when `mesh` is the
        decimated result; None if there is nothing to keep."""
        if mesh is self.decimated_mesh and self.result_maps is not None:
            return dataclasses.replace(self.result_maps) if self.result_maps.available() else None
        base = np.ascontiguousarray(self.texture.to_array()) if self.texture is not None else None
        maps = dataclasses.replace(self.maps) if self.maps is not None else MaterialMaps()
        if maps.base is None:
            maps.base = base
        if mesh is self.decimated_mesh and self.baked_normal is not None:
            maps.normal = self.baked_normal
        return maps if maps.available() else None

    def export_mesh(self):
        """Saves the decimated mesh to disk (or the original if none has
        been decimated) in the chosen format. If checked and a previous
        decimation exists, also writes a .json report with that
        decimation's statistics next to the mesh file."""
        mesh = self.decimated_mesh if self.decimated_mesh is not None else self.original_mesh
        if mesh is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export mesh", "", "OBJ (*.obj);;PLY (*.ply);;STL (*.stl);;GLB (*.glb)"
        )
        if not path:
            return
        try:
            maps = self._maps_for_export(mesh)
            if path.lower().endswith(".glb"):
                write_glb(path, mesh, maps)
            else:
                mesh.save(path)
                if mesh_has_texture_coords(mesh):
                    colour = map_to_texture(maps.base) if maps is not None and maps.base is not None else self.texture
                    save_texture_with_mesh(path, colour, normal_map=maps.normal if maps is not None else None)
            if self.export_report_cb.isChecked() and self._last_run_info is not None:
                report_path = f"{os.path.splitext(path)[0]}_report.json"
                with open(report_path, "w", encoding="utf-8") as f:
                    json.dump(self._last_run_info, f, indent=2, ensure_ascii=False)
        except Exception as exc:
            QMessageBox.critical(self, "Error exporting", str(exc))

    # --- batch processing ---

    def _process_one_batch_file(self, path: str) -> dict:
        """Loads, cleans (if applicable) and decimates a single file with
        the panel's current settings, via the same `run_decimation_pipeline`
        `apply_decimation` uses, but without manual protection (there's no
        mesh open in the viewer to read brush/rectangle marks from): only
        UV seams and error-based auto-refinement, if enabled. Returns a
        dict with the resulting mesh and the statistics, ready to save.
        Raises an exception if the file isn't a valid surface mesh or if
        the result ends up with no faces."""
        clean = self._gather_clean_settings() if self.auto_clean_cb.isChecked() else None
        mesh = load_and_prepare_mesh(path, clean)

        decimation = self._gather_decimation_settings()
        protection = self._gather_protection_settings()
        cut_seams = self.cut_seams_cb.isChecked()
        if self._seam_auto and mesh_has_texture_coords(mesh):  # decided per file
            protection.protect_uv_seams = False
            cut_seams = should_cut_uv_seams(mesh)
        result = run_decimation_pipeline(mesh, decimation, protection, manual_mask=None)

        info = {
            "source_file": os.path.basename(path),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "engine": self.engine_combo.currentText(),
            "original_tris": result.original_tris,
            "result_tris": result.result_tris,
            "reduction_target_pct": self.reduction_slider.value(),
            "reduction_real_pct": result.reduction_real_pct,
            "unprotected_zone_tris_before": result.unprotected_zone_tris_before,
            "unprotected_zone_tris_after": result.unprotected_zone_tris_after,
            "unprotected_zone_reduction_pct": result.unprotected_zone_reduction_pct,
            "uv_seam_protected_points": result.uv_seam_protected_points,
            "auto_error_protection_enabled": protection.auto_error_protection,
            "error_max": round(result.error_max, 6),
            "error_mean": round(result.error_mean, 6),
            "error_p95": round(result.error_p95, 6),
            "error_max_fwd": round(result.error_max_fwd, 6),
            "error_mean_fwd": round(result.error_mean_fwd, 6),
            "error_p95_fwd": round(result.error_p95_fwd, 6),
            "error_max_rev": round(result.error_max_rev, 6),
            "error_mean_rev": round(result.error_mean_rev, 6),
            "error_p95_rev": round(result.error_p95_rev, 6),
        }
        if protection.auto_error_protection:
            info.update({
                "error_threshold_pct": protection.error_threshold_pct,
                "error_refine_iterations": result.error_refine_iterations,
                "error_refine_added_points": result.error_refine_added_points,
                "reduction_lowered_to_pct": result.reduction_lowered_to_pct,
            })
        return {"mesh": transfer_texture_coords(mesh, result.mesh, cut_seams=cut_seams), "info": info, "source": mesh}

    def run_batch(self):
        """Applies the panel's current settings to every compatible mesh
        file in a folder, without going through the viewer: for each file,
        cleans (if applicable), decimates with the configured engine and
        automatic protection, and saves the result (plus a .json report, if
        checked) to the output folder. If a file fails, the error is logged
        and processing continues with the rest; a summary of successes and
        failures is shown at the end."""
        input_dir = QFileDialog.getExistingDirectory(self, "Input folder (meshes to process)")
        if not input_dir:
            return

        files = sorted(
            f for f in os.listdir(input_dir)
            if os.path.splitext(f)[1].lower() in BATCH_INPUT_EXTENSIONS
        )
        if not files:
            QMessageBox.information(
                self,
                "Batch processing",
                "No compatible mesh (OBJ/STL/PLY/VTP/GLB/GLTF) was found in that folder.",
            )
            return

        output_dir = QFileDialog.getExistingDirectory(self, "Output folder (results)")
        if not output_dir:
            return

        ext, ok = QInputDialog.getItem(
            self, "Output format", "Save results as:",
            ["obj", "ply", "stl", "glb"], 0, False,
        )
        if not ok:
            return

        warning = ""
        if os.path.normcase(os.path.abspath(input_dir)) == os.path.normcase(os.path.abspath(output_dir)):
            warning = (
                "\n\nNote: the output folder is the same as the input "
                "folder; a file with the same name and extension would be "
                "overwritten."
            )
        reply = QMessageBox.question(
            self,
            "Batch processing",
            f"{len(files)} file(s) from \"{input_dir}\" will be processed "
            f"with the {self.engine_combo.currentText()} engine at "
            f"{self.reduction_slider.value()}% aggressiveness, and the "
            f"result saved to \"{output_dir}\" as .{ext}. Manual protection "
            "(brush/rectangle) is not applied, only the automatic "
            f"protection configured above.{warning}\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        progress = QProgressDialog("Processing...", "Cancel", 0, len(files), self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)

        ok_count = 0
        failures = []
        for i, filename in enumerate(files):
            if progress.wasCanceled():
                break
            progress.setLabelText(f"Processing {filename} ({i + 1}/{len(files)})")
            progress.setValue(i)
            src_path = os.path.join(input_dir, filename)
            try:
                processed = self._process_one_batch_file(src_path)
                out_name = os.path.splitext(filename)[0] + f".{ext}"
                out_path = os.path.join(output_dir, out_name)
                file_maps = load_material_maps(src_path)
                if self.auto_bake_cb.isChecked() and file_maps is not None and file_maps.normal is not None:
                    baked, _ = best_normal_map(
                        processed["source"], processed["mesh"], file_maps, int(self.bake_size_combo.currentText())
                    )
                    if baked is not None:
                        file_maps.normal = baked
                if ext == "glb":
                    write_glb(out_path, processed["mesh"], file_maps)
                else:
                    processed["mesh"].save(out_path)
                    if mesh_has_texture_coords(processed["mesh"]):
                        base = (
                            map_to_texture(file_maps.base)
                            if file_maps is not None and file_maps.base is not None
                            else load_base_color_texture(src_path)
                        )
                        save_texture_with_mesh(
                            out_path, base, normal_map=file_maps.normal if file_maps is not None else None
                        )
                if self.export_report_cb.isChecked():
                    report_path = os.path.join(
                        output_dir, os.path.splitext(filename)[0] + "_report.json"
                    )
                    with open(report_path, "w", encoding="utf-8") as f:
                        json.dump(processed["info"], f, indent=2, ensure_ascii=False)
                ok_count += 1
            except Exception as exc:
                failures.append(f"{filename}: {exc}")
            QApplication.processEvents()

        progress.setValue(len(files))

        summary = f"Successfully processed: {ok_count}/{len(files)}."
        if failures:
            shown = failures[:20]
            summary += "\n\nFailures:\n" + "\n".join(shown)
            if len(failures) > len(shown):
                summary += f"\n... and {len(failures) - len(shown)} more."
        QMessageBox.information(self, "Batch processing finished", summary)

    # --- configuration presets ---

    def _gather_preset(self) -> dict:
        """Gathers every configurable panel parameter (cleanup, view,
        automatic protection, decimation and error validation) into a
        serializable dict, to save as a preset. Doesn't include the mesh or
        the manual protection marks, which are specific to each model."""
        return {
            "auto_clean": self.auto_clean_cb.isChecked(),
            "min_fragment_pct": self.min_fragment_slider.value(),
            "close_holes": self.close_holes_cb.isChecked(),
            "max_hole_size": self.max_hole_size_spin.value(),
            "fix_orientation": self.fix_orientation_cb.isChecked(),
            "curvature_view": self.curvature_cb.isChecked(),
            "live_preview": self.live_preview_cb.isChecked(),
            "viewport_bg_index": self.viewport_bg_combo.currentIndex(),
            "brush_radius_pct": self.brush_slider.value(),
            "protect_uv_seams": self.protect_uv_seams_cb.isChecked(),
            "cut_seams": self.cut_seams_cb.isChecked(),
            "engine_index": self.engine_combo.currentIndex(),
            "reduction_pct": self.reduction_slider.value(),
            "feature_angle": self.angle_slider.value(),
            "preserve_topology": self.preserve_topology_cb.isChecked(),
            "boundary_vertex_deletion": self.boundary_vertex_deletion_cb.isChecked(),
            "splitting": self.splitting_cb.isChecked(),
            "volume_preservation": self.volume_preservation_cb.isChecked(),
            "uv_attribute_metric": self.uv_attribute_metric_cb.isChecked(),
            "uv_weight": self.uv_weight_spin.value(),
            "normals_weight_enabled": self.normals_weight_cb.isChecked(),
            "normals_weight": self.normals_weight_spin.value(),
            "auto_error_protection": self.auto_error_cb.isChecked(),
            "error_threshold_pct": self.error_threshold_spin.value(),
            "error_max_iterations": self.error_max_iterations_spin.value(),
            "lower_reduction_if_needed": self.lower_reduction_cb.isChecked(),
            "export_report": self.export_report_cb.isChecked(),
        }

    def _apply_preset(self, data: dict):
        """Applies a loaded preset's values to the panel; any missing key
        keeps the corresponding control's current value, to stay compatible
        with presets saved by an earlier version of the app that didn't yet
        have some field."""
        def get(key, current):
            return data[key] if key in data else current

        self.auto_clean_cb.setChecked(get("auto_clean", self.auto_clean_cb.isChecked()))
        self.min_fragment_slider.setValue(get("min_fragment_pct", self.min_fragment_slider.value()))
        self.close_holes_cb.setChecked(get("close_holes", self.close_holes_cb.isChecked()))
        self.max_hole_size_spin.setValue(get("max_hole_size", self.max_hole_size_spin.value()))
        self.fix_orientation_cb.setChecked(get("fix_orientation", self.fix_orientation_cb.isChecked()))
        self.curvature_cb.setChecked(get("curvature_view", self.curvature_cb.isChecked()))
        self.live_preview_cb.setChecked(get("live_preview", self.live_preview_cb.isChecked()))
        self.viewport_bg_combo.setCurrentIndex(
            get("viewport_bg_index", self.viewport_bg_combo.currentIndex())
        )
        self.brush_slider.setValue(get("brush_radius_pct", self.brush_slider.value()))
        self.protect_uv_seams_cb.setChecked(get("protect_uv_seams", self.protect_uv_seams_cb.isChecked()))
        self.cut_seams_cb.setChecked(get("cut_seams", self.cut_seams_cb.isChecked()))
        self._seam_auto = False  # a preset is an explicit choice
        self.engine_combo.setCurrentIndex(get("engine_index", self.engine_combo.currentIndex()))
        self.reduction_slider.setValue(get("reduction_pct", self.reduction_slider.value()))
        self.angle_slider.setValue(get("feature_angle", self.angle_slider.value()))
        self.preserve_topology_cb.setChecked(get("preserve_topology", self.preserve_topology_cb.isChecked()))
        self.boundary_vertex_deletion_cb.setChecked(
            get("boundary_vertex_deletion", self.boundary_vertex_deletion_cb.isChecked())
        )
        self.splitting_cb.setChecked(get("splitting", self.splitting_cb.isChecked()))
        self.volume_preservation_cb.setChecked(
            get("volume_preservation", self.volume_preservation_cb.isChecked())
        )
        self.uv_attribute_metric_cb.setChecked(
            get("uv_attribute_metric", self.uv_attribute_metric_cb.isChecked())
        )
        self.uv_weight_spin.setValue(get("uv_weight", self.uv_weight_spin.value()))
        self.normals_weight_cb.setChecked(get("normals_weight_enabled", self.normals_weight_cb.isChecked()))
        self.normals_weight_spin.setValue(get("normals_weight", self.normals_weight_spin.value()))
        self.auto_error_cb.setChecked(get("auto_error_protection", self.auto_error_cb.isChecked()))
        self.error_threshold_spin.setValue(get("error_threshold_pct", self.error_threshold_spin.value()))
        self.error_max_iterations_spin.setValue(
            get("error_max_iterations", self.error_max_iterations_spin.value())
        )
        self.lower_reduction_cb.setChecked(
            get("lower_reduction_if_needed", self.lower_reduction_cb.isChecked())
        )
        self.export_report_cb.setChecked(get("export_report", self.export_report_cb.isChecked()))

    def save_preset(self):
        """Saves the panel's current configuration to a reusable .json file for other meshes of the same kind."""
        path, _ = QFileDialog.getSaveFileName(self, "Save preset", "", "Preset (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._gather_preset(), f, indent=2, ensure_ascii=False)
        except Exception as exc:
            QMessageBox.critical(self, "Error saving preset", str(exc))

    def load_preset(self):
        """Loads a previously saved preset and applies its values to every panel control."""
        path, _ = QFileDialog.getOpenFileName(self, "Load preset", "", "Preset (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._apply_preset(data)
        except Exception as exc:
            QMessageBox.critical(self, "Error loading preset", str(exc))


def main():
    """Entry point: configures logging, creates the QApplication, applies
    the theme, and shows the main window. `ADAPTIVE_REMESHER_DEBUG=1` raises
    the console level to DEBUG (the log file always captures it, see
    logging_config.py)."""
    configure_logging(verbose=bool(os.environ.get("ADAPTIVE_REMESHER_DEBUG")))
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(ICON_PATH))
    app.setStyleSheet(STYLESHEET)
    window = AdaptiveRemesherWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
