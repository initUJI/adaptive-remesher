"""Pre-cleanup panel (PyMeshLab)."""

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QCheckBox, QGridLayout, QLabel, QPushButton, QSlider, QSpinBox, QVBoxLayout
from PySide6.QtCore import Qt

from ui.common import tip

if TYPE_CHECKING:
    from app import AdaptiveRemesherWindow


def build_clean_panel(window: "AdaptiveRemesherWindow", panel_layout: QVBoxLayout) -> None:
    clean_group = window._add_collapsible_group("clean", "Pre-cleanup (PyMeshLab)")
    clean_grid = QGridLayout(clean_group)

    window.auto_clean_cb = QCheckBox("Clean automatically on load")
    window.auto_clean_cb.setChecked(True)
    window.auto_clean_cb.setToolTip(tip(
        "If checked, every time you load a mesh the PyMeshLab cleanup "
        "(duplicate vertices, basic non-manifold and floating "
        "fragments) is applied automatically before showing it."
    ))
    clean_grid.addWidget(window.auto_clean_cb, 0, 0, 1, 2)

    fragment_label = QLabel("Minimum fragment (% diagonal)")
    fragment_tip = tip(
        "Any piece of mesh disconnected from the rest (a typical "
        "AI-generation artifact: specks, floating debris) whose size "
        "is smaller than this percentage of the bounding-box diagonal "
        "is removed entirely. Raise it if loose debris remains; lower "
        "it if small pieces you actually want to keep disappear. At "
        "0% this filter is fully disabled."
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

    window.close_holes_cb = QCheckBox("Fill small holes")
    window.close_holes_cb.setChecked(False)
    window.close_holes_cb.setToolTip(tip(
        "Closes with new faces the holes up to the indicated size "
        "(useful after removing fragments). Careful: too large a "
        "size can close gaps that are actually intentional (eyes, "
        "mouth, openings)."
    ))
    clean_grid.addWidget(window.close_holes_cb, 3, 0, 1, 2)

    hole_size_label = QLabel("Max. hole size (edges)")
    hole_size_tip = tip(
        "Only holes bounded by up to this number of edges are filled. "
        "Raise it to close larger gaps; lower it to leave large "
        "openings untouched, since they may be intentional."
    )
    hole_size_label.setToolTip(hole_size_tip)
    clean_grid.addWidget(hole_size_label, 4, 0, 1, 2)
    window.max_hole_size_spin = QSpinBox()
    window.max_hole_size_spin.setRange(3, 500)
    window.max_hole_size_spin.setValue(30)
    window.max_hole_size_spin.setToolTip(hole_size_tip)
    clean_grid.addWidget(window.max_hole_size_spin, 5, 0, 1, 2)

    window.fix_orientation_cb = QCheckBox("Reorient faces consistently")
    window.fix_orientation_cb.setChecked(False)
    window.fix_orientation_cb.setToolTip(tip(
        "Fixes faces with an inverted normal (a typical AI-generation "
        "defect: some faces point inward and others outward on the "
        "same surface) by reorienting all of them consistently with "
        "each other. Fixes inverted shading or \"holes\" that are "
        "actually backwards faces. Doesn't guarantee the final result "
        "faces outward (only that all connected faces face the same way)."
    ))
    clean_grid.addWidget(window.fix_orientation_cb, 6, 0, 1, 2)

    clean_now_btn = QPushButton("Clean now")
    clean_now_btn.setToolTip(tip(
        "Reapplies the cleanup (with the current fragment threshold) "
        "to the already-loaded mesh. Since it changes the vertex "
        "count, it clears the existing protection marks."
    ))
    clean_now_btn.clicked.connect(window.clean_now)
    clean_grid.addWidget(clean_now_btn, 7, 0, 1, 2)

    panel_layout.addWidget(clean_group)
