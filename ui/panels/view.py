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

    view_grid.addWidget(QLabel("Background"), 2, 0)
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
    view_grid.addWidget(window.viewport_bg_combo, 2, 1)

    panel_layout.addWidget(view_group)
