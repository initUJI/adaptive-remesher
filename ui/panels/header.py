"""Cabecera del panel: titulo, boton de carga y fila de deshacer/rehacer."""

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

    load_btn = QPushButton("Cargar malla...")
    load_btn.setToolTip(tip(
        "Abre un archivo (OBJ, STL, PLY, VTP, GLB o GLTF), lo convierte "
        "a una malla de triangulos y lo muestra en el viewport. Si "
        "\"Limpiar automaticamente al cargar\" esta activo, tambien la "
        "limpia antes de mostrarla."
    ))
    load_btn.clicked.connect(window.load_mesh)
    panel_layout.addWidget(load_btn)

    undo_redo_row = QHBoxLayout()
    window.undo_btn = QPushButton("Deshacer")
    window.undo_btn.setToolTip(tip(
        "Deshace la ultima accion (limpieza, trazo de pincel, borrado "
        "de marcas, decimacion o restaurar original). Atajo: Ctrl+Z."
    ))
    window.undo_btn.setEnabled(False)
    window.undo_btn.clicked.connect(window.undo)
    window.redo_btn = QPushButton("Rehacer")
    window.redo_btn.setToolTip(tip(
        "Rehace la ultima accion deshecha. Atajo: Ctrl+Y."
    ))
    window.redo_btn.setEnabled(False)
    window.redo_btn.clicked.connect(window.redo)
    undo_redo_row.addWidget(window.undo_btn)
    undo_redo_row.addWidget(window.redo_btn)
    panel_layout.addLayout(undo_redo_row)

    # `activated=` es un kwarg de conveniencia especifico de PySide6 que los
    # stubs de tipos no reflejan (ven el QShortcut sin ese parametro), pero
    # que Shiboken si acepta en tiempo de ejecucion.
    QShortcut(QKeySequence.StandardKey.Undo, window, activated=window.undo)  # type: ignore[call-overload]
    QShortcut(QKeySequence.StandardKey.Redo, window, activated=window.redo)  # type: ignore[call-overload]
    QShortcut(QKeySequence("Ctrl+Y"), window, activated=window.redo)  # type: ignore[call-overload]
