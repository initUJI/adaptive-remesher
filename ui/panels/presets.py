"""Configuration profiles panel (save/load .json preset)."""

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QGridLayout, QPushButton, QVBoxLayout

from ui.common import tip

if TYPE_CHECKING:
    from app import AdaptiveRemesherWindow


def build_presets_panel(window: "AdaptiveRemesherWindow", panel_layout: QVBoxLayout) -> None:
    preset_group = window._add_collapsible_group("presets", "Configuration profiles")
    preset_grid = QGridLayout(preset_group)

    save_preset_btn = QPushButton("Save preset...")
    save_preset_btn.setToolTip(tip(
        "Saves all the panel's current parameters (cleanup, "
        "automatic seam protection, decimation engine and its "
        "options, error-based validation...) into a reusable .json, "
        "to quickly apply the same settings to other similar meshes "
        "(characters, props, terrain...). Does not include the mesh "
        "itself nor the manual protection marks."
    ))
    save_preset_btn.clicked.connect(window.save_preset)
    load_preset_btn = QPushButton("Load preset...")
    load_preset_btn.setToolTip(tip(
        "Loads a previously saved preset and applies its values to "
        "all the panel's controls. Does not touch the loaded mesh nor "
        "the existing manual protection marks."
    ))
    load_preset_btn.clicked.connect(window.load_preset)
    preset_grid.addWidget(save_preset_btn, 0, 0)
    preset_grid.addWidget(load_preset_btn, 0, 1)
    panel_layout.addWidget(preset_group)
