"""Visualization panel (curvature map, live preview)."""

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QCheckBox, QGridLayout, QVBoxLayout

from ui.common import tip

if TYPE_CHECKING:
    from app import AdaptiveRemesherWindow


def build_view_panel(window: "AdaptiveRemesherWindow", panel_layout: QVBoxLayout) -> None:
    view_group = window._add_collapsible_group("view", "Visualization")
    view_grid = QGridLayout(view_group)

    window.curvature_cb = QCheckBox("Curvature map (original mesh)")
    window.curvature_cb.setChecked(False)
    window.curvature_cb.setToolTip(tip(
        "Colors the original mesh according to its mean curvature "
        "(blue/red = flat/curved areas), to see at a glance where it "
        "makes sense to paint protected zones before decimating. Only "
        "applies while viewing the original mesh, not the decimated result."
    ))
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

    panel_layout.addWidget(view_group)
