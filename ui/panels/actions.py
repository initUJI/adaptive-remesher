"""Botones de accion principal: aplicar, restaurar, comparar y exportar."""

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QCheckBox, QPushButton, QVBoxLayout

from ui.common import tip

if TYPE_CHECKING:
    from app import AdaptiveRemesherWindow


def build_action_buttons(window: "AdaptiveRemesherWindow", panel_layout: QVBoxLayout) -> None:
    apply_btn = QPushButton("Aplicar decimacion")
    apply_btn.setObjectName("primaryButton")
    apply_btn.setToolTip(tip(
        "Ejecuta el motor de decimacion elegido sobre la malla "
        "original con los parametros actuales. Si hay zonas "
        "protegidas (pintadas, costuras UV o auto-detectadas por "
        "error), solo se decima el resto y luego se recompone el "
        "resultado."
    ))
    apply_btn.clicked.connect(window.apply_decimation)
    panel_layout.addWidget(apply_btn)

    reset_btn = QPushButton("Restaurar original")
    reset_btn.setToolTip(tip(
        "Descarta el resultado decimado y vuelve a mostrar la malla "
        "original tal como se cargo (o se limpio), conservando las "
        "marcas de proteccion."
    ))
    reset_btn.clicked.connect(window.reset_mesh)
    panel_layout.addWidget(reset_btn)

    compare_btn = QPushButton("Comparar original/decimado")
    compare_btn.setToolTip(tip(
        "Abre una ventana con dos vistas lado a lado -original y "
        "decimado- con las camaras sincronizadas, para juzgar la "
        "perdida de detalle. Necesita haber aplicado una decimacion "
        "primero."
    ))
    compare_btn.clicked.connect(window.open_comparison)
    panel_layout.addWidget(compare_btn)

    window.export_report_cb = QCheckBox("Exportar informe .json junto a la malla")
    window.export_report_cb.setChecked(True)
    window.export_report_cb.setToolTip(tip(
        "Al exportar, ademas del archivo de malla, guarda un .json con "
        "el mismo nombre (sufijo _report) con las estadisticas de la "
        "ultima decimacion: motor, reduccion, puntos protegidos y "
        "error medido."
    ))
    panel_layout.addWidget(window.export_report_cb)

    export_btn = QPushButton("Exportar malla...")
    export_btn.setToolTip(tip(
        "Guarda en disco la malla decimada; si todavia no has aplicado "
        "ninguna decimacion, guarda la malla original (ya limpiada, si "
        "aplica). OBJ y PLY conservan las coordenadas UV si la malla "
        "tiene textura; STL no soporta UV/textura (limitacion del "
        "propio formato)."
    ))
    export_btn.clicked.connect(window.export_mesh)
    panel_layout.addWidget(export_btn)
