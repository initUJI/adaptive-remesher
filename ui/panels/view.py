"""Visualization panel (curvature map, live preview, viewport background)."""

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QCheckBox, QComboBox, QGridLayout, QLabel, QVBoxLayout

from ui.common import VIEWPORT_BACKGROUND_PRESETS, tip

if TYPE_CHECKING:
    from app import AdaptiveRemesherWindow


def build_view_panel(window: "AdaptiveRemesherWindow", panel_layout: QVBoxLayout) -> None:
    view_group = window._add_collapsible_group("view", "Visualization")
    view_grid = QGridLayout(view_group)

    window.curvature_cb = QCheckBox("Curvature map (original mesh)")
    window.curvature_cb.setChecked(False)
    # Tooltip and enabled state are kept up to date dynamically by
    # window._update_curvature_availability(): it only works while the
    # original mesh is actually on screen, which "Live preview" and an
    # applied decimation both temporarily override.
    window.curvature_cb.toggled.connect(window._on_curvature_toggled)
    view_grid.addWidget(window.curvature_cb, 0, 0)

    window.live_preview_cb = QCheckBox("Live preview")
    window.live_preview_cb.setChecked(False)
    window.live_preview_cb.setToolTip(tip(
        "While you move the aggressiveness, the detail angle or "
        "change engine, instantly shows a preview of the result "
        "(without the up-to-3 error-based auto-adjustment passes, "
        "to keep it fast). Doesn't replace 'Apply decimation': press "
        "that to confirm the result and be able to export or compare it."
    ))
    window.live_preview_cb.toggled.connect(window._on_live_preview_toggled)
    view_grid.addWidget(window.live_preview_cb, 1, 0)

    window.show_edges_cb = QCheckBox("Show mesh lines")
    window.show_edges_cb.setChecked(True)
    window.show_edges_cb.setToolTip(tip(
        "Draws the triangle edges over the surface. Turn it off to see "
        "the model as a smooth solid, for example to judge its overall "
        "shape or to take a clean screenshot."
    ))
    window.show_edges_cb.toggled.connect(window._on_curvature_toggled)
    view_grid.addWidget(window.show_edges_cb, 2, 0)

    window.show_texture_cb = QCheckBox("Show texture")
    window.show_texture_cb.setChecked(True)
    window.show_texture_cb.setToolTip(tip(
        "Paints the model with its colour texture, when the loaded file "
        "has one and the mesh keeps its texture coordinates, so you can "
        "check that the decimated result still looks right."
    ))
    window.show_texture_cb.toggled.connect(window._on_curvature_toggled)
    view_grid.addWidget(window.show_texture_cb, 3, 0)

    window.show_normals_cb = QCheckBox("Show normal map")
    window.show_normals_cb.setChecked(False)
    window.show_normals_cb.setToolTip(tip(
        "Shades the model with its normal map (the loaded file's own, or "
        "the one you baked after decimating) using physically based "
        "lighting, which looks different from the plain view."
    ))
    window.show_normals_cb.toggled.connect(window._on_curvature_toggled)
    view_grid.addWidget(window.show_normals_cb, 4, 0)

    view_grid.addWidget(QLabel("Background"), 5, 0)
    window.viewport_bg_combo = QComboBox()
    for label, _hex in VIEWPORT_BACKGROUND_PRESETS:
        window.viewport_bg_combo.addItem(label)
    window.viewport_bg_combo.setToolTip(tip(
        "Changes the 3D viewport's background color. Presets are taken "
        "from the app's own dark palette, picked to keep good contrast "
        "against the mesh (light gray with dark edges) and the "
        "curvature-map colors, whichever looks better for a given model."
    ))
    window.viewport_bg_combo.currentIndexChanged.connect(window._on_viewport_bg_changed)
    view_grid.addWidget(window.viewport_bg_combo, 5, 1)

    panel_layout.addWidget(view_group)
