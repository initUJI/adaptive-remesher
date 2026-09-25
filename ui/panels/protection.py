"""Panel de proteccion manual (pincel, rectangulo, mascara, costuras UV)."""

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QCheckBox, QGridLayout, QLabel, QPushButton, QSlider, QSpinBox, QVBoxLayout
from PySide6.QtCore import Qt

from ui.common import tip

if TYPE_CHECKING:
    from app import AdaptiveRemesherWindow


def build_protection_panel(window: "AdaptiveRemesherWindow", panel_layout: QVBoxLayout) -> None:
    protect_group = window._add_collapsible_group("protect", "Proteccion manual")
    protect_grid = QGridLayout(protect_group)

    window.paint_btn = QPushButton("Pintar")
    window.paint_btn.setCheckable(True)
    window.paint_btn.setToolTip(tip(
        "Activa el pincel de proteccion: haz click y arrastra sobre la "
        "malla en el viewport para marcar en rojo los puntos que NUNCA "
        "se deben simplificar (caras, logos, dedos, detalles finos). "
        "Se desactiva volviendo a pulsar este boton."
    ))
    window.erase_btn = QPushButton("Borrar")
    window.erase_btn.setObjectName("eraseButton")
    window.erase_btn.setCheckable(True)
    window.erase_btn.setToolTip(tip(
        "Activa el pincel de borrado: click y arrastra sobre puntos ya "
        "marcados en rojo para quitarles la proteccion, con el mismo "
        "radio de pincel que el modo Pintar."
    ))
    window.paint_btn.toggled.connect(
        lambda checked: window._on_brush_toggled(window.paint_btn, checked)
    )
    window.erase_btn.toggled.connect(
        lambda checked: window._on_brush_toggled(window.erase_btn, checked)
    )
    protect_grid.addWidget(window.paint_btn, 0, 0)
    protect_grid.addWidget(window.erase_btn, 0, 1)

    window.rect_btn = QPushButton("Rectangulo")
    window.rect_btn.setCheckable(True)
    window.rect_btn.setToolTip(tip(
        "Activa la seleccion por rectangulo: arrastra sobre el "
        "viewport para proteger de golpe todos los vertices dentro "
        "del rectangulo (sin test de profundidad: tambien marca "
        "puntos ocultos detras de la malla). Manten pulsado Mayus "
        "mientras arrastras para desproteger en vez de proteger. "
        "Util para zonas grandes de forma irregular donde el pincel "
        "circular es lento."
    ))
    window.rect_btn.toggled.connect(
        lambda checked: window._on_brush_toggled(window.rect_btn, checked)
    )
    protect_grid.addWidget(window.rect_btn, 6, 0, 1, 2)

    mask_export_btn = QPushButton("Guardar mascara...")
    mask_export_btn.setToolTip(tip(
        "Guarda en un .json los indices de los vertices protegidos "
        "actualmente, para reutilizar la misma proteccion en otra "
        "sesion o en otra malla con identica topologia (mismo numero "
        "y orden de vertices)."
    ))
    mask_export_btn.clicked.connect(window.export_protection_mask)
    mask_import_btn = QPushButton("Cargar mascara...")
    mask_import_btn.setToolTip(tip(
        "Carga una mascara de proteccion guardada y sustituye las "
        "marcas actuales. Solo se aplica si el numero de vertices "
        "coincide exactamente con el de la malla actual."
    ))
    mask_import_btn.clicked.connect(window.import_protection_mask)
    protect_grid.addWidget(mask_export_btn, 7, 0)
    protect_grid.addWidget(mask_import_btn, 7, 1)

    brush_label = QLabel("Radio de pincel (% diagonal)")
    brush_tip = tip(
        "Tamano del pincel, como porcentaje de la diagonal del bounding "
        "box de la malla. Cada click/arrastre marca (o borra) todos los "
        "puntos dentro de este radio alrededor del cursor. Subelo para "
        "cubrir zonas grandes rapido; bajalo para marcar detalles finos "
        "con precision."
    )
    brush_label.setToolTip(brush_tip)
    protect_grid.addWidget(brush_label, 1, 0, 1, 2)
    window.brush_slider = QSlider(Qt.Orientation.Horizontal)
    window.brush_slider.setRange(1, 20)
    window.brush_slider.setValue(5)
    window.brush_slider.setToolTip(brush_tip)
    window.brush_spin = QSpinBox()
    window.brush_spin.setRange(1, 20)
    window.brush_spin.setValue(5)
    window.brush_spin.setSuffix("%")
    window.brush_spin.setToolTip(brush_tip)
    window.brush_slider.valueChanged.connect(window.brush_spin.setValue)
    window.brush_spin.valueChanged.connect(window.brush_slider.setValue)
    protect_grid.addWidget(window.brush_slider, 2, 0)
    protect_grid.addWidget(window.brush_spin, 2, 1)

    clear_marks_btn = QPushButton("Borrar todas las marcas")
    clear_marks_btn.setToolTip(tip(
        "Quita de golpe todas las marcas de proteccion de la malla "
        "actual (equivale a borrar todo con el pincel de borrado, pero "
        "en un solo click)."
    ))
    clear_marks_btn.clicked.connect(window.clear_protection)
    protect_grid.addWidget(clear_marks_btn, 3, 0, 1, 2)

    window.protected_count_label = QLabel("Puntos protegidos: 0")
    window.protected_count_label.setToolTip(tip(
        "Numero de vertices actualmente marcados como protegidos. Esos "
        "vertices y las caras que tocan quedan excluidos del colapso al "
        "aplicar la decimacion."
    ))
    protect_grid.addWidget(window.protected_count_label, 4, 0, 1, 2)

    window.protect_uv_seams_cb = QCheckBox("Proteger costuras UV automaticamente")
    window.protect_uv_seams_cb.setChecked(True)
    window.protect_uv_seams_cb.setToolTip(tip(
        "Si la malla tiene textura, detecta los vertices duplicados que "
        "forman una costura UV (donde la textura se corta al "
        "desenvolver) y los protege automaticamente, ademas de tus "
        "marcas manuales. Sin esto, decimar puede romper la textura "
        "justo en la costura. No hace nada si la malla no tiene UVs."
    ))
    protect_grid.addWidget(window.protect_uv_seams_cb, 5, 0, 1, 2)

    panel_layout.addWidget(protect_group)
