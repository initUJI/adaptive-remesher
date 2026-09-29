"""Batch processing panel."""

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout

from ui.common import tip

if TYPE_CHECKING:
    from app import AdaptiveRemesherWindow


def build_batch_panel(window: "AdaptiveRemesherWindow", panel_layout: QVBoxLayout) -> None:
    batch_group = window._add_collapsible_group("batch", "Batch processing")
    batch_layout = QVBoxLayout(batch_group)

    batch_info = QLabel(
        "Applies the panel's current settings (cleanup, engine, "
        "decimation and automatic protection) to every file in a "
        "folder, without going through the viewer. Doesn't include "
        "manual protection (brush or rectangle): only UV seams and "
        "the error-based auto-adjustment, if enabled above."
    )
    batch_info.setWordWrap(True)
    batch_layout.addWidget(batch_info)

    batch_btn = QPushButton("Process folder...")
    batch_btn.setToolTip(tip(
        "Choose an input folder (OBJ/STL/PLY/VTP/GLB/GLTF) and an "
        "output one: decimates each file with the panel's current "
        "settings and saves the result (plus the .json report, if "
        "checked above) in the output folder under the same name. If "
        "a file fails, it's skipped and processing continues with "
        "the rest; a summary is shown at the end."
    ))
    batch_btn.clicked.connect(window.run_batch)
    batch_layout.addWidget(batch_btn)

    panel_layout.addWidget(batch_group)
