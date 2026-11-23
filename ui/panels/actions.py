"""Main action buttons: apply, restore, compare and export."""

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QCheckBox, QPushButton, QVBoxLayout

from ui.common import tip

if TYPE_CHECKING:
    from app import AdaptiveRemesherWindow


def build_action_buttons(window: "AdaptiveRemesherWindow", panel_layout: QVBoxLayout) -> None:
    window.apply_btn = QPushButton("Apply decimation")
    window.apply_btn.setObjectName("primaryButton")
    window.apply_btn.setToolTip(tip(
        "Runs the chosen decimation engine on the original mesh with "
        "the current parameters. If there are protected areas "
        "(painted, UV seams or auto-detected by error), only the rest "
        "is decimated and then the result is recomposed."
    ))
    window.apply_btn.clicked.connect(window.apply_decimation)
    panel_layout.addWidget(window.apply_btn)

    window.reset_btn = QPushButton("Restore original")
    window.reset_btn.setToolTip(tip(
        "Discards the decimated result and shows the original mesh "
        "again as it was loaded (or cleaned), keeping the protection marks."
    ))
    window.reset_btn.clicked.connect(window.reset_mesh)
    panel_layout.addWidget(window.reset_btn)

    window.compare_btn = QPushButton("Compare original/decimated")
    window.compare_btn.setToolTip(tip(
        "Opens a window with two side-by-side views -original and "
        "decimated- with synchronized cameras, to judge the loss of "
        "detail. Requires having applied a decimation first."
    ))
    window.compare_btn.clicked.connect(window.open_comparison)
    panel_layout.addWidget(window.compare_btn)

    window.export_report_cb = QCheckBox("Export .json report alongside the mesh")
    window.export_report_cb.setChecked(True)
    window.export_report_cb.setToolTip(tip(
        "When exporting, besides the mesh file, saves a .json with "
        "the same name (suffix _report) containing the statistics of "
        "the last decimation: engine, reduction, protected points and "
        "measured error."
    ))
    panel_layout.addWidget(window.export_report_cb)

    window.export_btn = QPushButton("Export mesh...")
    window.export_btn.setToolTip(tip(
        "Saves the decimated mesh to disk; if you haven't applied any "
        "decimation yet, saves the original mesh (already cleaned, if "
        "applicable). OBJ and PLY preserve UV coordinates if the mesh "
        "has a texture; STL doesn't support UV/texture (a limitation "
        "of the format itself)."
    ))
    window.export_btn.clicked.connect(window.export_mesh)
    panel_layout.addWidget(window.export_btn)
