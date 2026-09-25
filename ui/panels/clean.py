"""Panel de limpieza previa (PyMeshLab)."""

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QCheckBox, QGridLayout, QLabel, QPushButton, QSlider, QSpinBox, QVBoxLayout
from PySide6.QtCore import Qt

from ui.common import tip

if TYPE_CHECKING:
    from app import AdaptiveRemesherWindow


def build_clean_panel(window: "AdaptiveRemesherWindow", panel_layout: QVBoxLayout) -> None:
    clean_group = window._add_collapsible_group("clean", "Limpieza previa (PyMeshLab)")
    clean_grid = QGridLayout(clean_group)

    window.auto_clean_cb = QCheckBox("Limpiar automaticamente al cargar")
    window.auto_clean_cb.setChecked(True)
    window.auto_clean_cb.setToolTip(tip(
        "Si esta marcado, cada vez que cargues una malla se le aplica "
        "automaticamente la limpieza de PyMeshLab (vertices duplicados, "
        "no-manifold basico y fragmentos flotantes) antes de mostrarla."
    ))
    clean_grid.addWidget(window.auto_clean_cb, 0, 0, 1, 2)

    fragment_label = QLabel("Fragmento minimo (% diagonal)")
    fragment_tip = tip(
        "Cualquier trozo de malla desconectado del resto (tipico "
        "residuo de generacion por IA: motas, restos flotantes) cuyo "
        "tamano sea menor que este porcentaje de la diagonal del "
        "bounding box se elimina por completo. Subelo si quedan restos "
        "sueltos; bajalo si desaparecen piezas pequenas que si quieres "
        "conservar. En 0% este filtro se desactiva por completo."
    )
    fragment_label.setToolTip(fragment_tip)
    clean_grid.addWidget(fragment_label, 1, 0, 1, 2)
    window.min_fragment_slider = QSlider(Qt.Orientation.Horizontal)
    window.min_fragment_slider.setRange(0, 20)
    window.min_fragment_slider.setValue(2)
    window.min_fragment_slider.setToolTip(fragment_tip)
    window.min_fragment_spin = QSpinBox()
    window.min_fragment_spin.setRange(0, 20)
    window.min_fragment_spin.setValue(2)
    window.min_fragment_spin.setSuffix("%")
    window.min_fragment_spin.setToolTip(fragment_tip)
    window.min_fragment_slider.valueChanged.connect(window.min_fragment_spin.setValue)
    window.min_fragment_spin.valueChanged.connect(window.min_fragment_slider.setValue)
    clean_grid.addWidget(window.min_fragment_slider, 2, 0)
    clean_grid.addWidget(window.min_fragment_spin, 2, 1)

    window.close_holes_cb = QCheckBox("Rellenar agujeros pequenos")
    window.close_holes_cb.setChecked(False)
    window.close_holes_cb.setToolTip(tip(
        "Cierra con caras nuevas los agujeros de hasta el tamano "
        "indicado (utiles tras quitar fragmentos). Cuidado: con un "
        "tamano demasiado alto puede cerrar huecos que si son "
        "intencionados (ojos, boca, aberturas)."
    ))
    clean_grid.addWidget(window.close_holes_cb, 3, 0, 1, 2)

    hole_size_label = QLabel("Tamano max. de agujero (aristas)")
    hole_size_tip = tip(
        "Solo se rellenan agujeros delimitados por hasta este numero "
        "de aristas. Subelo para cerrar huecos mas grandes; bajalo "
        "para no tocar aberturas grandes que pueden ser intencionadas."
    )
    hole_size_label.setToolTip(hole_size_tip)
    clean_grid.addWidget(hole_size_label, 4, 0, 1, 2)
    window.max_hole_size_spin = QSpinBox()
    window.max_hole_size_spin.setRange(3, 500)
    window.max_hole_size_spin.setValue(30)
    window.max_hole_size_spin.setToolTip(hole_size_tip)
    clean_grid.addWidget(window.max_hole_size_spin, 5, 0, 1, 2)

    window.fix_orientation_cb = QCheckBox("Reorientar caras de forma coherente")
    window.fix_orientation_cb.setChecked(False)
    window.fix_orientation_cb.setToolTip(tip(
        "Corrige caras con la normal invertida (defecto tipico de "
        "generacion por IA: algunas caras miran hacia dentro y otras "
        "hacia fuera en la misma superficie) reorientandolas todas de "
        "forma coherente entre si. Arregla sombreado invertido o "
        "\"agujeros\" que en realidad son caras del reves. No garantiza "
        "que el resultado final mire hacia fuera (solo que todas las "
        "caras conectadas miran igual)."
    ))
    clean_grid.addWidget(window.fix_orientation_cb, 6, 0, 1, 2)

    clean_now_btn = QPushButton("Limpiar ahora")
    clean_now_btn.setToolTip(tip(
        "Vuelve a aplicar la limpieza (con el umbral de fragmento "
        "actual) sobre la malla ya cargada. Como cambia el numero de "
        "vertices, borra las marcas de proteccion existentes."
    ))
    clean_now_btn.clicked.connect(window.clean_now)
    clean_grid.addWidget(clean_now_btn, 7, 0, 1, 2)

    panel_layout.addWidget(clean_group)
