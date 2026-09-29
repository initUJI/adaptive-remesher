"""Panel header: title, load button and undo/redo row."""

from typing import TYPE_CHECKING

from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ui.common import tip

if TYPE_CHECKING:
    from app import AdaptiveRemesherWindow


def build_header(window: "AdaptiveRemesherWindow", panel_layout: QVBoxLayout) -> None:
    header = QLabel("Adaptive Remesher")
    header.setObjectName("headerLabel")
    panel_layout.addWidget(header)

    load_btn = QPushButton("Load mesh...")
    load_btn.setToolTip(tip(
        "Opens a file (OBJ, STL, PLY, VTP, GLB or GLTF), converts it "
        "to a triangle mesh and shows it in the viewport. If "
        "\"Clean automatically on load\" is enabled, it also cleans "
        "it before showing it."
    ))
    load_btn.clicked.connect(window.load_mesh)
    panel_layout.addWidget(load_btn)

    undo_redo_row = QHBoxLayout()
    window.undo_btn = QPushButton("Undo")
    window.undo_btn.setToolTip(tip(
        "Undoes the last action (cleanup, brush stroke, erasing "
        "marks, decimation or restore original). Shortcut: Ctrl+Z."
    ))
    window.undo_btn.setEnabled(False)
    window.undo_btn.clicked.connect(window.undo)
    window.redo_btn = QPushButton("Redo")
    window.redo_btn.setToolTip(tip(
        "Redoes the last undone action. Shortcut: Ctrl+Y."
    ))
    window.redo_btn.setEnabled(False)
    window.redo_btn.clicked.connect(window.redo)
    undo_redo_row.addWidget(window.undo_btn)
    undo_redo_row.addWidget(window.redo_btn)
    panel_layout.addLayout(undo_redo_row)

    # `activated=` is a PySide6-specific convenience kwarg that the type
    # stubs don't reflect (they see QShortcut without that parameter), but
    # that Shiboken does accept at runtime.
    QShortcut(QKeySequence.StandardKey.Undo, window, activated=window.undo)  # type: ignore[call-overload]
    QShortcut(QKeySequence.StandardKey.Redo, window, activated=window.redo)  # type: ignore[call-overload]
    QShortcut(QKeySequence("Ctrl+Y"), window, activated=window.redo)  # type: ignore[call-overload]
