"""Panel de perfiles de configuracion (guardar/cargar preset .json)."""

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QGridLayout, QPushButton, QVBoxLayout

from ui.common import tip

if TYPE_CHECKING:
    from app import AdaptiveRemesherWindow


def build_presets_panel(window: "AdaptiveRemesherWindow", panel_layout: QVBoxLayout) -> None:
    preset_group = window._add_collapsible_group("presets", "Perfiles de configuracion")
    preset_grid = QGridLayout(preset_group)

    save_preset_btn = QPushButton("Guardar preset...")
    save_preset_btn.setToolTip(tip(
        "Guarda todos los parametros actuales del panel (limpieza, "
        "proteccion automatica de costuras, motor de decimacion y sus "
        "opciones, validacion por error...) en un .json reutilizable, "
        "para aplicar rapidamente el mismo ajuste a otras mallas "
        "parecidas (personajes, props, terreno...). No incluye la "
        "malla en si ni las marcas de proteccion manual."
    ))
    save_preset_btn.clicked.connect(window.save_preset)
    load_preset_btn = QPushButton("Cargar preset...")
    load_preset_btn.setToolTip(tip(
        "Carga un preset guardado previamente y aplica sus valores a "
        "todos los controles del panel. No toca la malla cargada ni "
        "las marcas de proteccion manual existentes."
    ))
    load_preset_btn.clicked.connect(window.load_preset)
    preset_grid.addWidget(save_preset_btn, 0, 0)
    preset_grid.addWidget(load_preset_btn, 0, 1)
    panel_layout.addWidget(preset_group)
