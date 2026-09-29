"""Adaptive decimation panel: common engine (vtkDecimatePro/QEM) and the
options specific to Quadric Error Metric in their own collapsible group,
which is enabled or disabled depending on the chosen engine."""

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QGridLayout, QLabel, QSlider, QVBoxLayout
from PySide6.QtCore import Qt

from ui.common import tip

if TYPE_CHECKING:
    from app import AdaptiveRemesherWindow


def build_decimation_panel(window: "AdaptiveRemesherWindow", panel_layout: QVBoxLayout) -> None:
    # --- Decimation ---
    params_group = window._add_collapsible_group("decimate", "Adaptive decimation")
    grid = QGridLayout(params_group)

    engine_label = QLabel("Decimation engine")
    engine_tip = tip(
        "vtkDecimatePro classifies each vertex against a fixed angle "
        "(feature or not) and collapses the ones below it. Quadric "
        "Error Metric (QEM) instead weighs each collapse by a "
        "continuous geometric error derived from local curvature: "
        "with no fixed threshold, flat areas collapse before curved "
        "ones, gradually. QEM also lets you penalize UV texture "
        "distortion in the cost itself."
    )
    engine_label.setToolTip(engine_tip)
    grid.addWidget(engine_label, 0, 0, 1, 2)
    window.engine_combo = QComboBox()
    window.engine_combo.addItem("vtkDecimatePro (fixed angle)")
    window.engine_combo.addItem("Quadric Error Metric (curvature)")
    window.engine_combo.setToolTip(engine_tip)
    window.engine_combo.currentIndexChanged.connect(window._on_engine_changed)
    window.engine_combo.currentIndexChanged.connect(window._schedule_preview)
    grid.addWidget(window.engine_combo, 1, 0, 1, 2)

    reduction_label = QLabel("Aggressiveness (target reduction)")
    reduction_tip = tip(
        "Percentage of triangles that vtkDecimatePro will try to "
        "remove in unprotected areas (0% = no change, 99% = tries to "
        "remove almost all). It's a target, not a guarantee: the "
        "detail angle and protected areas can make the actual "
        "reduction lower."
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

    angle_label = QLabel("Detail preservation angle")
    angle_tip = tip(
        "Angle (in degrees) between neighboring faces above which an "
        "edge is considered a \"feature\" (hard edge, corner) and is "
        "kept intact. Low values preserve more detail (less actual "
        "reduction); high values allow smoothing more areas, at the "
        "cost of losing fine edges."
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

    window.preserve_topology_cb = QCheckBox("Preserve topology")
    window.preserve_topology_cb.setChecked(True)
    window.preserve_topology_cb.setToolTip(tip(
        "Prevents decimation from splitting the surface into separate "
        "pieces or creating unwanted holes. Recommended on unless you "
        "need the maximum possible reduction and don't mind changing "
        "the topology."
    ))
    grid.addWidget(window.preserve_topology_cb, 6, 0, 1, 2)

    window.boundary_vertex_deletion_cb = QCheckBox("Allow simplifying boundaries")
    window.boundary_vertex_deletion_cb.setChecked(False)
    window.boundary_vertex_deletion_cb.setToolTip(tip(
        "If disabled (default), vertices on open mesh boundaries "
        "(holes, non-closed surfaces) are barely touched. Enable it "
        "if your mesh has open boundaries you also want to simplify; "
        "it can deform the outline. Affects both engines (in QEM, it "
        "raises the boundary weight)."
    ))
    grid.addWidget(window.boundary_vertex_deletion_cb, 7, 0, 1, 2)

    window.splitting_cb = QCheckBox("Allow surface splitting")
    window.splitting_cb.setChecked(True)
    window.splitting_cb.setToolTip(tip(
        "Lets vtkDecimatePro split the surface along non-preserved "
        "edges when that helps reduce more without losing shape. "
        "Disabling it usually gives less reduction but a \"cleaner\" "
        "mesh in a single continuous piece. Only applies to the "
        "vtkDecimatePro engine."
    ))
    grid.addWidget(window.splitting_cb, 8, 0, 1, 2)

    panel_layout.addWidget(params_group)

    # --- Options specific to Quadric Error Metric ---
    window.qem_group = window._add_collapsible_group("qem", "Quadric Error Metric options")
    qem_grid = QGridLayout(window.qem_group)

    window.volume_preservation_cb = QCheckBox("Preserve volume")
    window.volume_preservation_cb.setChecked(True)
    window.volume_preservation_cb.setToolTip(tip(
        "Adjusts the collapse so the mesh's total volume changes as "
        "little as possible. Recommended on; disabling it is a bit "
        "faster but can \"shrink\" the shape under aggressive reductions."
    ))
    qem_grid.addWidget(window.volume_preservation_cb, 0, 0, 1, 2)

    window.uv_attribute_metric_cb = QCheckBox("Penalize UV distortion in the cost")
    window.uv_attribute_metric_cb.setChecked(True)
    window.uv_attribute_metric_cb.setToolTip(tip(
        "In addition to geometry, accounts for how much each collapse "
        "would stretch the texture coordinates, avoiding texture "
        "distortion even far from protected seams. Only has an effect "
        "if the mesh has UVs."
    ))
    qem_grid.addWidget(window.uv_attribute_metric_cb, 1, 0, 1, 2)

    uv_weight_label = QLabel("UV distortion weight")
    uv_weight_tip = tip(
        "How much texture distortion weighs against pure geometric "
        "error when deciding each collapse. Raise it if the texture "
        "comes out more deformed than you'd like; lower it if you "
        "prefer to prioritize shape over texture."
    )
    uv_weight_label.setToolTip(uv_weight_tip)
    qem_grid.addWidget(uv_weight_label, 2, 0, 1, 2)
    window.uv_weight_spin = QDoubleSpinBox()
    window.uv_weight_spin.setRange(0.1, 10.0)
    window.uv_weight_spin.setSingleStep(0.1)
    window.uv_weight_spin.setValue(1.0)
    window.uv_weight_spin.setToolTip(uv_weight_tip)
    qem_grid.addWidget(window.uv_weight_spin, 3, 0, 1, 2)

    window.normals_weight_cb = QCheckBox("Preserve normals (avoids faceting)")
    window.normals_weight_cb.setChecked(False)
    window.normals_weight_cb.setToolTip(tip(
        "In addition to position (and UV, if enabled), penalizes in "
        "the cost of each collapse how much the surface normal would "
        "change. Without this, QEM only measures position error and "
        "can flatten smoothly curved areas into facets visible to the "
        "naked eye even though the overall shape barely moves. "
        "Computes vertex normals automatically if the mesh doesn't "
        "have them."
    ))
    qem_grid.addWidget(window.normals_weight_cb, 4, 0, 1, 2)

    normals_weight_label = QLabel("Normal weight")
    normals_weight_tip = tip(
        "How much the normal change weighs against pure geometric "
        "error when deciding each collapse. Raise it if you still see "
        "faceting in curved areas; lower it if you prefer to "
        "prioritize reduction over shading."
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
