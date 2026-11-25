"""Surface geometric error-based validation panel."""

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QGridLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)

from ui.common import tip

if TYPE_CHECKING:
    from app import AdaptiveRemesherWindow


def build_validation_panel(window: "AdaptiveRemesherWindow", panel_layout: QVBoxLayout) -> None:
    error_group = window._add_collapsible_group("error", "Surface error-based validation")
    error_grid = QGridLayout(error_group)

    window.auto_error_cb = QCheckBox("Auto-protect high-error areas")
    window.auto_error_cb.setChecked(True)
    window.auto_error_cb.setToolTip(tip(
        "After decimating, measures in both directions how far the "
        "result strays from the original (approximates the Hausdorff "
        "distance): both areas of the result deviated from the "
        "original and original details the result has removed "
        "entirely. Where the error exceeds the threshold, protects "
        "those vertices in the original mesh and decimates again (up "
        "to the number of passes below), instead of relying entirely "
        "on the fixed detection angle."
    ))
    error_grid.addWidget(window.auto_error_cb, 0, 0, 1, 2)

    window.error_threshold_label = QLabel("Maximum allowed error (% diagonal)")
    error_threshold_tip = tip(
        "Error threshold, as a percentage of the bounding-box "
        "diagonal, above which a decimated area is considered too "
        "deformed and gets protected automatically. Lower it to "
        "demand more fidelity (more areas get auto-protected, less "
        "actual reduction); raise it to allow more deformation."
    )
    window.error_threshold_label.setToolTip(error_threshold_tip)
    error_grid.addWidget(window.error_threshold_label, 1, 0, 1, 2)
    window.error_threshold_spin = QDoubleSpinBox()
    window.error_threshold_spin.setRange(0.1, 10.0)
    window.error_threshold_spin.setSingleStep(0.1)
    window.error_threshold_spin.setValue(1.0)
    window.error_threshold_spin.setSuffix("%")
    window.error_threshold_spin.setToolTip(error_threshold_tip)
    error_grid.addWidget(window.error_threshold_spin, 2, 0, 1, 2)

    window.error_max_iterations_label = QLabel("Maximum auto-protection passes")
    error_max_iterations_tip = tip(
        "Maximum number of times the cycle of measuring error and "
        "decimating again while protecting the worst areas is "
        "repeated. With a very demanding threshold, more passes bring "
        "the result closer to the threshold at the cost of more "
        "computation time; fewer passes stop earlier even if the "
        "error is still above it."
    )
    window.error_max_iterations_label.setToolTip(error_max_iterations_tip)
    error_grid.addWidget(window.error_max_iterations_label, 3, 0, 1, 2)
    window.error_max_iterations_spin = QSpinBox()
    window.error_max_iterations_spin.setRange(1, 10)
    window.error_max_iterations_spin.setValue(3)
    window.error_max_iterations_spin.setToolTip(error_max_iterations_tip)
    error_grid.addWidget(window.error_max_iterations_spin, 4, 0, 1, 2)

    window.lower_reduction_cb = QCheckBox("Lower the reduction if the limit cannot be met")
    window.lower_reduction_cb.setChecked(True)
    window.lower_reduction_cb.setToolTip(tip(
        "If protecting the high-error areas cannot bring the error "
        "under the maximum, decimates less (in steps of 5 points below "
        "the requested reduction) until it does. Small or detailed "
        "meshes often cannot be reduced by 90% within 1% error; "
        "protecting areas then keeps most of the mesh, whereas a "
        "lower reduction keeps the error within the limit."
    ))
    error_grid.addWidget(window.lower_reduction_cb, 5, 0, 1, 2)

    panel_layout.addWidget(error_group)
