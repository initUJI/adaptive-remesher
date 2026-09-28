"""Panel de validacion por error geometrico de superficie."""

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
    error_group = window._add_collapsible_group("error", "Validacion por error de superficie")
    error_grid = QGridLayout(error_group)

    window.auto_error_cb = QCheckBox("Auto-proteger zonas con error alto")
    window.auto_error_cb.setChecked(True)
    window.auto_error_cb.setToolTip(tip(
        "Tras decimar, mide en las dos direcciones cuanto se aleja el "
        "resultado del original (aproxima la distancia de Hausdorff): "
        "tanto zonas del resultado desviadas del original como detalles "
        "del original que el resultado haya eliminado por completo. "
        "Donde el error supera el umbral, protege esos vertices en la "
        "malla original y vuelve a decimar (hasta el numero de pasadas "
        "de abajo), en vez de fiarlo todo al angulo de deteccion fijo."
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

    error_max_iterations_label = QLabel("Pasadas maximas de auto-proteccion")
    error_max_iterations_tip = tip(
        "Numero maximo de veces que se repite el ciclo de medir error y "
        "volver a decimar protegiendo las zonas peores. Con un umbral "
        "muy exigente, mas pasadas acercan mas el resultado al umbral a "
        "costa de mas tiempo de calculo; menos pasadas cortan antes "
        "aunque el error siga por encima."
    )
    error_max_iterations_label.setToolTip(error_max_iterations_tip)
    error_grid.addWidget(error_max_iterations_label, 3, 0, 1, 2)
    window.error_max_iterations_spin = QSpinBox()
    window.error_max_iterations_spin.setRange(1, 10)
    window.error_max_iterations_spin.setValue(3)
    window.error_max_iterations_spin.setToolTip(error_max_iterations_tip)
    error_grid.addWidget(window.error_max_iterations_spin, 4, 0, 1, 2)

    panel_layout.addWidget(error_group)
