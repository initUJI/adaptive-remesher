"""Panel de procesamiento por lotes."""

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout

from ui.common import tip

if TYPE_CHECKING:
    from app import AdaptiveRemesherWindow


def build_batch_panel(window: "AdaptiveRemesherWindow", panel_layout: QVBoxLayout) -> None:
    batch_group = window._add_collapsible_group("batch", "Procesamiento por lotes")
    batch_layout = QVBoxLayout(batch_group)

    batch_info = QLabel(
        "Aplica los ajustes actuales del panel (limpieza, motor, "
        "decimacion y proteccion automatica) a todos los archivos de "
        "una carpeta, sin pasar por el visor. No incluye proteccion "
        "manual (pincel ni rectangulo): solo costuras UV y el "
        "auto-ajuste por error, si estan activados arriba."
    )
    batch_info.setWordWrap(True)
    batch_layout.addWidget(batch_info)

    batch_btn = QPushButton("Procesar carpeta...")
    batch_btn.setToolTip(tip(
        "Elige una carpeta de entrada (OBJ/STL/PLY/VTP/GLB/GLTF) y una "
        "de salida: decima cada archivo con los ajustes actuales del "
        "panel y guarda el resultado (mas el informe .json, si esta "
        "marcado arriba) en la carpeta de salida con el mismo nombre. "
        "Si un archivo falla, se salta y se sigue con el resto; al "
        "final se muestra un resumen."
    ))
    batch_btn.clicked.connect(window.run_batch)
    batch_layout.addWidget(batch_btn)

    panel_layout.addWidget(batch_group)
