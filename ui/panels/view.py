"""Panel de visualizacion (mapa de curvatura, vista previa en tiempo real)."""

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QCheckBox, QGridLayout, QVBoxLayout

from ui.common import tip

if TYPE_CHECKING:
    from app import AdaptiveRemesherWindow


def build_view_panel(window: "AdaptiveRemesherWindow", panel_layout: QVBoxLayout) -> None:
    view_group = window._add_collapsible_group("view", "Visualizacion")
    view_grid = QGridLayout(view_group)

    window.curvature_cb = QCheckBox("Mapa de curvatura (malla original)")
    window.curvature_cb.setChecked(False)
    window.curvature_cb.setToolTip(tip(
        "Colorea la malla original segun su curvatura media (azul/rojo "
        "= zonas planas/curvas), para ver de un vistazo donde conviene "
        "pintar zonas protegidas antes de decimar. Solo se aplica "
        "mientras se ve la malla original, no el resultado decimado."
    ))
    window.curvature_cb.toggled.connect(window._on_curvature_toggled)
    view_grid.addWidget(window.curvature_cb, 0, 0)

    window.live_preview_cb = QCheckBox("Vista previa en tiempo real")
    window.live_preview_cb.setChecked(False)
    window.live_preview_cb.setToolTip(tip(
        "Mientras mueves la agresividad, el angulo de detalle o "
        "cambias de motor, muestra al momento una previsualizacion "
        "del resultado (sin las hasta 3 pasadas de auto-ajuste por "
        "error, para que sea rapida). No sustituye a 'Aplicar "
        "decimacion': pulsalo para confirmar el resultado y poder "
        "exportarlo o compararlo."
    ))
    window.live_preview_cb.toggled.connect(window._on_live_preview_toggled)
    view_grid.addWidget(window.live_preview_cb, 1, 0)

    panel_layout.addWidget(view_group)
