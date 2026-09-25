"""Panel de validacion por error geometrico de superficie."""

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QCheckBox, QDoubleSpinBox, QGridLayout, QLabel, QVBoxLayout

from ui.common import tip

if TYPE_CHECKING:
    from app import AdaptiveRemesherWindow


def build_validation_panel(window: "AdaptiveRemesherWindow", panel_layout: QVBoxLayout) -> None:
    error_group = window._add_collapsible_group("error", "Validacion por error de superficie")
    error_grid = QGridLayout(error_group)

    window.auto_error_cb = QCheckBox("Auto-proteger zonas con error alto")
    window.auto_error_cb.setChecked(True)
    window.auto_error_cb.setToolTip(tip(
        "Tras decimar, mide cuanto se aleja cada punto del resultado de "
        "la superficie original (aproxima la distancia de Hausdorff). "
        "Donde el error supera el umbral, protege esos vertices en la "
        "malla original y vuelve a decimar (hasta 3 pasadas), en vez de "
        "fiarlo todo al angulo de deteccion fijo."
    ))
    error_grid.addWidget(window.auto_error_cb, 0, 0, 1, 2)

    error_threshold_label = QLabel("Error maximo permitido (% diagonal)")
    error_threshold_tip = tip(
        "Umbral de error, como porcentaje de la diagonal del bounding "
        "box, a partir del cual una zona decimada se considera "
        "demasiado deformada y se protege automaticamente. Bajalo para "
        "exigir mas fidelidad (mas zonas se auto-protegen, menos "
        "reduccion real); subelo para permitir mas deformacion."
    )
    error_threshold_label.setToolTip(error_threshold_tip)
    error_grid.addWidget(error_threshold_label, 1, 0, 1, 2)
    window.error_threshold_spin = QDoubleSpinBox()
    window.error_threshold_spin.setRange(0.1, 10.0)
    window.error_threshold_spin.setSingleStep(0.1)
    window.error_threshold_spin.setValue(1.0)
    window.error_threshold_spin.setSuffix("%")
    window.error_threshold_spin.setToolTip(error_threshold_tip)
    error_grid.addWidget(window.error_threshold_spin, 2, 0, 1, 2)

    panel_layout.addWidget(error_group)
