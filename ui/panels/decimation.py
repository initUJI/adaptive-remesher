"""Panel de decimacion adaptativa: motor comun (vtkDecimatePro/QEM) y las
opciones especificas de Quadric Error Metric en su propio grupo plegable,
que se habilita o deshabilita segun el motor elegido."""

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QGridLayout, QLabel, QSlider, QVBoxLayout
from PySide6.QtCore import Qt

from ui.common import tip

if TYPE_CHECKING:
    from app import AdaptiveRemesherWindow


def build_decimation_panel(window: "AdaptiveRemesherWindow", panel_layout: QVBoxLayout) -> None:
    # --- Decimacion ---
    params_group = window._add_collapsible_group("decimate", "Decimacion adaptativa")
    grid = QGridLayout(params_group)

    engine_label = QLabel("Motor de decimacion")
    engine_tip = tip(
        "vtkDecimatePro clasifica cada vertice segun un angulo fijo "
        "(caracteristica o no) y colapsa los que no lo superan. Quadric "
        "Error Metric (QEM) en cambio pondera cada colapso por un error "
        "geometrico continuo derivado de la curvatura local: sin "
        "umbral fijo, las zonas planas se colapsan antes que las "
        "curvas de forma gradual. QEM tambien permite penalizar la "
        "distorsion de textura UV en el propio coste."
    )
    engine_label.setToolTip(engine_tip)
    grid.addWidget(engine_label, 0, 0, 1, 2)
    window.engine_combo = QComboBox()
    window.engine_combo.addItem("vtkDecimatePro (angulo fijo)")
    window.engine_combo.addItem("Quadric Error Metric (curvatura)")
    window.engine_combo.setToolTip(engine_tip)
    window.engine_combo.currentIndexChanged.connect(window._on_engine_changed)
    window.engine_combo.currentIndexChanged.connect(window._schedule_preview)
    grid.addWidget(window.engine_combo, 1, 0, 1, 2)

    reduction_label = QLabel("Agresividad (target reduction)")
    reduction_tip = tip(
        "Porcentaje de triangulos que vtkDecimatePro intentara "
        "eliminar en las zonas no protegidas (0% = no cambia nada, "
        "99% = intenta quitar casi todos). Es un objetivo, no una "
        "garantia: el angulo de detalle y las zonas protegidas pueden "
        "hacer que la reduccion real sea menor."
    )
    reduction_label.setToolTip(reduction_tip)
    grid.addWidget(reduction_label, 2, 0, 1, 2)
    window.reduction_slider = QSlider(Qt.Orientation.Horizontal)
    window.reduction_slider.setRange(0, 99)
    window.reduction_slider.setValue(50)
    window.reduction_slider.setToolTip(reduction_tip)
    window.reduction_slider.valueChanged.connect(
        lambda v: window.reduction_value_label.setText(f"{v}%")
    )
    window.reduction_slider.valueChanged.connect(window._schedule_preview)
    grid.addWidget(window.reduction_slider, 3, 0)
    window.reduction_value_label = QLabel("50%")
    grid.addWidget(window.reduction_value_label, 3, 1)

    angle_label = QLabel("Angulo de preservacion de detalle")
    angle_tip = tip(
        "Angulo (en grados) entre caras vecinas por encima del cual un "
        "borde se considera \"caracteristica\" (arista dura, esquina) y "
        "se conserva intacto. Valores bajos preservan mas detalle "
        "(menos reduccion real); valores altos permiten alisar mas "
        "zonas, a costa de perder aristas finas."
    )
    angle_label.setToolTip(angle_tip)
    grid.addWidget(angle_label, 4, 0, 1, 2)
    window.angle_slider = QSlider(Qt.Orientation.Horizontal)
    window.angle_slider.setRange(1, 90)
    window.angle_slider.setValue(15)
    window.angle_slider.setToolTip(angle_tip)
    window.angle_slider.valueChanged.connect(
        lambda v: window.angle_value_label.setText(f"{v}°")
    )
    window.angle_slider.valueChanged.connect(window._schedule_preview)
    grid.addWidget(window.angle_slider, 5, 0)
    window.angle_value_label = QLabel("15°")
    grid.addWidget(window.angle_value_label, 5, 1)

    window.preserve_topology_cb = QCheckBox("Preservar topologia")
    window.preserve_topology_cb.setChecked(True)
    window.preserve_topology_cb.setToolTip(tip(
        "Impide que la decimacion parta la superficie en piezas "
        "separadas o cree agujeros no deseados. Recomendado activado "
        "salvo que necesites la maxima reduccion posible y no te "
        "importe cambiar la topologia."
    ))
    grid.addWidget(window.preserve_topology_cb, 6, 0, 1, 2)

    window.boundary_vertex_deletion_cb = QCheckBox("Permitir simplificar bordes")
    window.boundary_vertex_deletion_cb.setChecked(False)
    window.boundary_vertex_deletion_cb.setToolTip(tip(
        "Si esta desactivado (por defecto), los vertices en bordes "
        "abiertos de la malla (huecos, superficies no cerradas) apenas "
        "se tocan. Activalo si tu malla tiene bordes abiertos que "
        "tambien quieres simplificar; puede deformar el contorno. "
        "Afecta a ambos motores (en QEM, sube el peso de los bordes)."
    ))
    grid.addWidget(window.boundary_vertex_deletion_cb, 7, 0, 1, 2)

    window.splitting_cb = QCheckBox("Permitir dividir superficie (splitting)")
    window.splitting_cb.setChecked(True)
    window.splitting_cb.setToolTip(tip(
        "Deja que vtkDecimatePro divida la superficie a lo largo de "
        "aristas no preservadas cuando eso ayuda a reducir mas sin "
        "perder forma. Desactivarlo suele dar menos reduccion pero una "
        "malla mas \"limpia\" en una sola pieza continua. Solo aplica "
        "al motor vtkDecimatePro."
    ))
    grid.addWidget(window.splitting_cb, 8, 0, 1, 2)

    panel_layout.addWidget(params_group)

    # --- Opciones especificas de Quadric Error Metric ---
    window.qem_group = window._add_collapsible_group("qem", "Opciones Quadric Error Metric")
    qem_grid = QGridLayout(window.qem_group)

    window.volume_preservation_cb = QCheckBox("Preservar volumen")
    window.volume_preservation_cb.setChecked(True)
    window.volume_preservation_cb.setToolTip(tip(
        "Ajusta el colapso para que el volumen total de la malla "
        "cambie lo menos posible. Recomendado activado; desactivarlo "
        "es un poco mas rapido pero puede \"encoger\" la forma en "
        "reducciones agresivas."
    ))
    qem_grid.addWidget(window.volume_preservation_cb, 0, 0, 1, 2)

    window.uv_attribute_metric_cb = QCheckBox("Penalizar distorsion UV en el coste")
    window.uv_attribute_metric_cb.setChecked(True)
    window.uv_attribute_metric_cb.setToolTip(tip(
        "Ademas de la geometria, tiene en cuenta cuanto estiraria cada "
        "colapso las coordenadas de textura, evitando deformar la "
        "textura incluso lejos de las costuras protegidas. Solo tiene "
        "efecto si la malla tiene UVs."
    ))
    qem_grid.addWidget(window.uv_attribute_metric_cb, 1, 0, 1, 2)

    uv_weight_label = QLabel("Peso de la distorsion UV")
    uv_weight_tip = tip(
        "Cuanto pesa la distorsion de textura frente al error "
        "geometrico puro al decidir cada colapso. Subelo si la "
        "textura sale mas deformada de lo que te gustaria; bajalo si "
        "prefieres priorizar la forma sobre la textura."
    )
    uv_weight_label.setToolTip(uv_weight_tip)
    qem_grid.addWidget(uv_weight_label, 2, 0, 1, 2)
    window.uv_weight_spin = QDoubleSpinBox()
    window.uv_weight_spin.setRange(0.1, 10.0)
    window.uv_weight_spin.setSingleStep(0.1)
    window.uv_weight_spin.setValue(1.0)
    window.uv_weight_spin.setToolTip(uv_weight_tip)
    qem_grid.addWidget(window.uv_weight_spin, 3, 0, 1, 2)

    window.normals_weight_cb = QCheckBox("Preservar normales (evita facetado)")
    window.normals_weight_cb.setChecked(False)
    window.normals_weight_cb.setToolTip(tip(
        "Ademas de la posicion (y la UV, si esta activo), penaliza en "
        "el coste de cada colapso cuanto cambiaria la normal de la "
        "superficie. Sin esto, QEM solo mide error de posicion y puede "
        "aplanar zonas suavemente curvas en facetas visibles a simple "
        "vista aunque la forma general apenas se mueva. Calcula las "
        "normales de vertice automaticamente si la malla no las trae."
    ))
    qem_grid.addWidget(window.normals_weight_cb, 4, 0, 1, 2)

    normals_weight_label = QLabel("Peso de la normal")
    normals_weight_tip = tip(
        "Cuanto pesa el cambio de normal frente al error geometrico "
        "puro al decidir cada colapso. Subelo si sigues viendo "
        "facetado en zonas curvas; bajalo si prefieres priorizar la "
        "reduccion sobre el sombreado."
    )
    normals_weight_label.setToolTip(normals_weight_tip)
    qem_grid.addWidget(normals_weight_label, 5, 0, 1, 2)
    window.normals_weight_spin = QDoubleSpinBox()
    window.normals_weight_spin.setRange(0.1, 10.0)
    window.normals_weight_spin.setSingleStep(0.1)
    window.normals_weight_spin.setValue(1.0)
    window.normals_weight_spin.setToolTip(normals_weight_tip)
    qem_grid.addWidget(window.normals_weight_spin, 6, 0, 1, 2)

    panel_layout.addWidget(window.qem_group)
    window._on_engine_changed(window.engine_combo.currentIndex())
