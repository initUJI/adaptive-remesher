"""Manual protection panel (brush, rectangle, mask, UV seams)."""

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QCheckBox, QGridLayout, QLabel, QPushButton, QSlider, QSpinBox, QVBoxLayout
from PySide6.QtCore import Qt

from ui.common import tip

if TYPE_CHECKING:
    from app import AdaptiveRemesherWindow


def build_protection_panel(window: "AdaptiveRemesherWindow", panel_layout: QVBoxLayout) -> None:
    protect_group = window._add_collapsible_group("protect", "Manual protection")
    protect_grid = QGridLayout(protect_group)

    window.paint_btn = QPushButton("Paint")
    window.paint_btn.setCheckable(True)
    window.paint_btn.setToolTip(tip(
        "Activates the protection brush: click and drag over the mesh "
        "in the viewport to mark in red the points that must NEVER be "
        "simplified (faces, logos, fingers, fine details). Disable it "
        "by pressing this button again."
    ))
    window.erase_btn = QPushButton("Erase")
    window.erase_btn.setObjectName("eraseButton")
    window.erase_btn.setCheckable(True)
    window.erase_btn.setToolTip(tip(
        "Activates the erase brush: click and drag over points already "
        "marked in red to remove their protection, using the same "
        "brush radius as Paint mode."
    ))
    window.paint_btn.toggled.connect(
        lambda checked: window._on_brush_toggled(window.paint_btn, checked)
    )
    window.erase_btn.toggled.connect(
        lambda checked: window._on_brush_toggled(window.erase_btn, checked)
    )
    protect_grid.addWidget(window.paint_btn, 0, 0)
    protect_grid.addWidget(window.erase_btn, 0, 1)

    window.rect_btn = QPushButton("Rectangle")
    window.rect_btn.setCheckable(True)
    window.rect_btn.setToolTip(tip(
        "Activates rectangle selection: drag over the viewport to "
        "protect all visible vertices inside the rectangle at once "
        "(a real depth test against the current view: it doesn't mark "
        "points hidden behind another part of the mesh). Hold Shift "
        "while dragging to unprotect instead of protect. Useful for "
        "large, irregularly shaped areas where the circular brush is slow."
    ))
    window.rect_btn.toggled.connect(
        lambda checked: window._on_brush_toggled(window.rect_btn, checked)
    )
    protect_grid.addWidget(window.rect_btn, 6, 0, 1, 2)

    mask_export_btn = QPushButton("Save mask...")
    mask_export_btn.setToolTip(tip(
        "Saves the indices of the currently protected vertices to a "
        ".json, to reuse the same protection in another session or "
        "on another mesh with identical topology (same number and "
        "order of vertices)."
    ))
    mask_export_btn.clicked.connect(window.export_protection_mask)
    mask_import_btn = QPushButton("Load mask...")
    mask_import_btn.setToolTip(tip(
        "Loads a saved protection mask and replaces the current marks. "
        "Only applies if the vertex count matches exactly that of the "
        "current mesh."
    ))
    mask_import_btn.clicked.connect(window.import_protection_mask)
    protect_grid.addWidget(mask_export_btn, 7, 0)
    protect_grid.addWidget(mask_import_btn, 7, 1)

    brush_label = QLabel("Brush radius (% diagonal)")
    brush_tip = tip(
        "Brush size, as a percentage of the mesh's bounding-box "
        "diagonal. Each click/drag marks (or erases) every point "
        "within this radius around the cursor. Raise it to cover "
        "large areas quickly; lower it to mark fine details with precision."
    )
    brush_label.setToolTip(brush_tip)
    protect_grid.addWidget(brush_label, 1, 0, 1, 2)
    window.brush_slider = QSlider(Qt.Orientation.Horizontal)
    window.brush_slider.setRange(1, 20)
    window.brush_slider.setValue(5)
    window.brush_slider.setToolTip(brush_tip)
    window.brush_spin = QSpinBox()
    window.brush_spin.setRange(1, 20)
    window.brush_spin.setValue(5)
    window.brush_spin.setSuffix("%")
    window.brush_spin.setToolTip(brush_tip)
    window.brush_slider.valueChanged.connect(window.brush_spin.setValue)
    window.brush_spin.valueChanged.connect(window.brush_slider.setValue)
    protect_grid.addWidget(window.brush_slider, 2, 0)
    protect_grid.addWidget(window.brush_spin, 2, 1)

    clear_marks_btn = QPushButton("Clear all marks")
    clear_marks_btn.setToolTip(tip(
        "Removes all protection marks from the current mesh at once "
        "(equivalent to erasing everything with the erase brush, but "
        "in a single click)."
    ))
    clear_marks_btn.clicked.connect(window.clear_protection)
    protect_grid.addWidget(clear_marks_btn, 3, 0, 1, 2)

    window.protected_count_label = QLabel("Protected points: 0")
    window.protected_count_label.setToolTip(tip(
        "Number of vertices currently marked as protected. Those "
        "vertices and the faces touching them are excluded from "
        "collapse when applying decimation."
    ))
    protect_grid.addWidget(window.protected_count_label, 4, 0, 1, 2)

    window.protect_uv_seams_cb = QCheckBox("Protect UV seams automatically")
    window.protect_uv_seams_cb.setChecked(True)
    window.protect_uv_seams_cb.setToolTip(tip(
        "If the mesh has a texture, detects the duplicate vertices "
        "that form a UV seam (where the texture is cut when unwrapping) "
        "and protects them automatically, in addition to your manual "
        "marks. Without this, decimating can break the texture right "
        "at the seam. Does nothing if the mesh has no UVs."
    ))
    protect_grid.addWidget(window.protect_uv_seams_cb, 5, 0, 1, 2)

    panel_layout.addWidget(protect_group)
