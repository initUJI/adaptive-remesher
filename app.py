"""Prototipo de remesher adaptativo: reduce poligonos solo donde no hacen
falta, con dos motores de decimacion intercambiables: vtkDecimatePro
(angulo de caracteristica fijo) y Quadric Error Metric (coste continuo
ponderado por curvatura, con penalizacion opcional de distorsion UV).

Incluye limpieza previa tipica de mallas generadas por IA (fragmentos
sueltos, vertices/aristas no-manifold, relleno opcional de agujeros) via
PyMeshLab; pintado/borrado manual de zonas protegidas; proteccion
automatica de costuras UV; y validacion por error geometrico que
auto-protege zonas donde la decimacion se desvia demasiado del original.
"""

import json
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime

import numpy as np
import pymeshlab
import pyvista as pv
import vtk
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QRubberBand,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import QEvent, QRect, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from pyvistaqt import QtInteractor
from scipy.spatial import cKDTree

DEBUG = False

ENGINE_DECIMATE_PRO = 0
ENGINE_QEM = 1

PANEL_STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".panel_layout.json")

BATCH_INPUT_EXTENSIONS = (".obj", ".stl", ".ply", ".vtp", ".glb", ".gltf")

STYLESHEET = """
QMainWindow, QWidget {
    background-color: #1c1d22;
    color: #e4e6eb;
    font-family: 'Segoe UI', Arial, sans-serif;
    font-size: 13px;
}
QLabel#headerLabel {
    color: #ffffff;
    font-size: 17px;
    font-weight: 600;
    padding: 2px 0 10px 0;
}
QGroupBox {
    border: 1px solid #33353d;
    border-radius: 8px;
    margin-top: 14px;
    padding: 12px 10px 10px 10px;
    font-weight: 600;
    color: #8fb2ff;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
}
QGroupBox::indicator { width: 0px; height: 0px; }
QPushButton {
    background-color: #2a2c33;
    border: 1px solid #3d3f48;
    border-radius: 6px;
    padding: 7px 12px;
    color: #e4e6eb;
}
QPushButton:hover { border-color: #5b7cfa; }
QPushButton:pressed { background-color: #23252b; }
QPushButton:checked {
    background-color: #3355cc;
    border-color: #5b7cfa;
    color: white;
}
QPushButton#eraseButton:checked {
    background-color: #b23a3a;
    border-color: #ff6b6b;
}
QPushButton#primaryButton {
    background-color: #2f8f5b;
    border-color: #3fb875;
    font-weight: 600;
}
QPushButton#primaryButton:hover { background-color: #37a568; }
QCheckBox { spacing: 8px; padding: 2px 0; }
QSlider::groove:horizontal {
    height: 4px;
    background: #33353d;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #5b7cfa;
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}
QSlider::sub-page:horizontal { background: #4a63c9; border-radius: 2px; }
QSpinBox {
    background-color: #2a2c33;
    border: 1px solid #3d3f48;
    border-radius: 4px;
    padding: 2px 4px;
    min-width: 46px;
}
QToolTip {
    background-color: #2a2c33;
    color: #e4e6eb;
    border: 1px solid #5b7cfa;
    border-radius: 4px;
    padding: 6px 8px;
}
QComboBox {
    background-color: #2a2c33;
    border: 1px solid #3d3f48;
    border-radius: 4px;
    padding: 4px 8px;
}
QComboBox:hover { border-color: #5b7cfa; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox QAbstractItemView {
    background-color: #2a2c33;
    color: #e4e6eb;
    selection-background-color: #3355cc;
    border: 1px solid #3d3f48;
    outline: none;
}
QSplitter::handle {
    background-color: #33353d;
}
QSplitter::handle:hover { background-color: #5b7cfa; }
QScrollArea { border: none; }
QScrollBar:vertical {
    background: #1c1d22;
    width: 12px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #3d3f48;
    border-radius: 6px;
    min-height: 24px;
}
QScrollBar::handle:vertical:hover { background: #5b7cfa; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
"""


@contextmanager
def busy_cursor():
    """Muestra el cursor de espera durante una operacion potencialmente
    larga (limpieza o decimacion), para que la interfaz no parezca colgada."""
    QApplication.setOverrideCursor(Qt.WaitCursor)
    QApplication.processEvents()
    try:
        yield
    finally:
        QApplication.restoreOverrideCursor()


def tip(text: str) -> str:
    """Envuelve un texto de tooltip en HTML simple para que Qt lo ajuste a varias lineas con un ancho maximo legible."""
    return f"<div style='max-width:280px'>{text}</div>"


def debug(msg):
    """Imprime un mensaje con hora si DEBUG esta activo (diagnostico de eventos de raton/camara)."""
    if DEBUG:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def clean_mesh(
    mesh: pv.PolyData,
    min_component_pct: float,
    close_holes: bool = False,
    max_hole_size: int = 30,
    fix_orientation: bool = False,
) -> pv.PolyData:
    """Repara defectos tipicos de mallas de IA: vertices duplicados o sin
    referenciar, aristas y vertices no-manifold, y fragmentos flotantes mas
    pequenos que min_component_pct% de la diagonal del bounding box. Si
    `close_holes`, ademas rellena agujeros de hasta `max_hole_size` aristas
    (util tras quitar fragmentos, pero puede cerrar huecos intencionados
    como ojos/boca si el umbral es demasiado alto). Si `fix_orientation`,
    reorienta las caras de forma coherente entre si: corrige el sombreado
    invertido o los "agujeros" aparentes que aparecen cuando un generador
    de IA deja normales de cara mezcladas (algunas hacia dentro, otras
    hacia fuera) dentro de la misma superficie."""
    ms = pymeshlab.MeshSet()
    ms.add_mesh(pymeshlab.Mesh(vertex_matrix=mesh.points, face_matrix=mesh.regular_faces))
    ms.meshing_remove_duplicate_vertices()
    ms.meshing_remove_unreferenced_vertices()
    ms.meshing_repair_non_manifold_edges()
    ms.meshing_repair_non_manifold_vertices()
    if fix_orientation:
        ms.meshing_re_orient_faces_coherently()
    if min_component_pct > 0:
        ms.meshing_remove_connected_component_by_diameter(
            mincomponentdiag=pymeshlab.PercentageValue(min_component_pct)
        )
    if close_holes:
        ms.meshing_close_holes(maxholesize=max_hole_size)
    cleaned = ms.current_mesh()
    verts = cleaned.vertex_matrix()
    faces = cleaned.face_matrix()
    poly_faces = np.hstack([np.full((faces.shape[0], 1), 3), faces]).astype(np.int64)
    return pv.PolyData(verts, poly_faces)


def mesh_has_texture_coords(mesh: pv.PolyData) -> bool:
    """Indica si la malla tiene coordenadas de textura (UV) activas."""
    return mesh.active_texture_coordinates is not None


def ensure_active_texture_coords(mesh: pv.PolyData) -> None:
    """Si la malla no tiene UV activas pero trae un array de textura sin
    marcar como tal, lo activa. Algunos lectores (segun formato) dejan las
    coordenadas de textura como un array de punto mas, sin activarlas, en
    cuyo caso `active_texture_coordinates` da None aunque la malla si tenga
    textura: la proteccion de costuras UV y el peso de distorsion UV en QEM
    se saltarian en silencio sin este arreglo."""
    if mesh.active_texture_coordinates is not None:
        return
    keywords = ("uv", "tex", "tcoord")
    for name, array in mesh.point_data.items():
        if array.ndim == 2 and array.shape[1] == 2 and any(k in name.lower() for k in keywords):
            mesh.active_texture_coordinates = array
            return


def uv_seam_point_mask(mesh: pv.PolyData) -> np.ndarray:
    """Detecta vertices duplicados en la misma posicion 3D pero con distinta
    UV: la forma habitual en que OBJ/GLTF representan una costura de
    textura. Protegerlos evita que la decimacion los colapse y rompa la
    textura en la costura. Devuelve una mascara booleana por punto.
    Vectorizado con numpy sobre todos los pares a la vez (en vez de un
    bucle Python con un np.allclose por par): en mallas de IA con muchas
    costuras (miles de vertices duplicados) el bucle era, con diferencia,
    el cuello de botella de toda la deteccion de proteccion automatica."""
    mask = np.zeros(mesh.n_points, dtype=bool)
    tcoords = mesh.active_texture_coordinates
    if tcoords is None:
        return mask
    tol = 1e-5 * mesh.length
    tree = cKDTree(mesh.points)
    pairs = tree.query_pairs(r=tol, output_type="ndarray")
    if pairs.size == 0:
        return mask
    i, j = pairs[:, 0], pairs[:, 1]
    a, b = tcoords[i], tcoords[j]
    # Misma tolerancia que np.allclose(a, b, atol=1e-4) (rtol=1e-05 por
    # defecto), pero evaluada de golpe sobre todos los pares.
    close = np.all(np.abs(a - b) <= (1e-4 + 1e-5 * np.abs(b)), axis=1)
    differing = ~close
    mask[i[differing]] = True
    mask[j[differing]] = True
    return mask


def decimate_pro(
    mesh: pv.PolyData,
    reduction: float,
    feature_angle: float,
    preserve_topology: bool,
    boundary_vertex_deletion: bool,
    splitting: bool,
) -> pv.PolyData:
    """Aplica vtkDecimatePro a `mesh`: colapsa vertices en zonas planas segun
    `feature_angle` y deja intactos bordes duros/esquinas por encima de ese
    angulo. Devuelve la malla sin cambios si no tiene celdas."""
    if mesh.n_cells == 0:
        return mesh

    decimate = vtk.vtkDecimatePro()
    decimate.SetInputData(mesh)
    decimate.SetTargetReduction(reduction)
    decimate.SetFeatureAngle(feature_angle)
    decimate.SetPreserveTopology(preserve_topology)
    decimate.SetBoundaryVertexDeletion(boundary_vertex_deletion)
    decimate.SetSplitting(splitting)
    decimate.Update()
    return pv.wrap(decimate.GetOutput())


def decimate_qem(
    mesh: pv.PolyData,
    reduction: float,
    volume_preservation: bool,
    use_uv_weight: bool,
    tcoords_weight: float,
    boundary_locked: bool,
    use_normals_weight: bool = False,
    normals_weight: float = 1.0,
) -> pv.PolyData:
    """Aplica vtkQuadricDecimation (Garland-Heckbert): el coste de cada
    colapso es un error cuadratico que ya pondera la curvatura local de
    forma continua (zonas planas -> error bajo -> se colapsan primero;
    zonas curvas -> error alto -> se preservan), en vez del angulo fijo
    binario de vtkDecimatePro. Si `use_uv_weight` y la malla tiene textura,
    anade al coste la distorsion de coordenadas UV, para desalentar
    colapses que estiren la textura incluso fuera de las costuras ya
    protegidas por separado. Si `use_normals_weight`, anade ademas la
    variacion de la normal de vertice al coste: sin esto, QEM puede
    aplanar zonas suavemente curvas en facetas visibles porque solo mide
    error de posicion; penalizar el cambio de normal mantiene el sombreado
    mas fiel al original en mallas organicas muy decimadas (calcula las
    normales de vertice si la malla aun no las trae). `boundary_locked`
    penaliza fuertemente mover los bordes (reales o de union con una zona
    protegida)."""
    if mesh.n_cells == 0:
        return mesh

    use_uv = use_uv_weight and mesh_has_texture_coords(mesh)
    working = mesh
    if use_normals_weight and "Normals" not in working.point_data:
        working = working.compute_normals(
            cell_normals=False,
            point_normals=True,
            split_vertices=False,
            consistent_normals=False,
            auto_orient_normals=False,
        )

    decimate = vtk.vtkQuadricDecimation()
    decimate.SetInputData(working)
    decimate.SetTargetReduction(reduction)
    decimate.SetVolumePreservation(volume_preservation)
    if use_uv or use_normals_weight:
        decimate.SetAttributeErrorMetric(True)
        if use_uv:
            decimate.SetTCoordsAttribute(True)
            decimate.SetTCoordsWeight(tcoords_weight)
        if use_normals_weight:
            decimate.SetNormalsAttribute(True)
            decimate.SetNormalsWeight(normals_weight)
    decimate.SetBoundaryWeightFactor(1000.0 if boundary_locked else 1.0)
    decimate.Update()
    output = pv.wrap(decimate.GetOutput())
    if working is not mesh and "Normals" in output.point_data:
        # Las normales se calcularon solo para guiar el coste de colapso,
        # no eran un dato original de `mesh`: si se dejan en el resultado,
        # una pieza vecina que no paso por aqui (p.ej. la zona protegida,
        # sin decimar) no las tendria, y el merge posterior arrastraria un
        # array a medias con huecos.
        del output.point_data["Normals"]
    return output


class ComparisonDialog(QDialog):
    """Ventana de comparacion lado a lado: malla original a la izquierda,
    resultado decimado a la derecha, con las camaras de ambos viewports
    sincronizadas (orbitar/hacer zoom en uno mueve el otro igual)."""

    def __init__(self, original: pv.PolyData, decimated: pv.PolyData, parent=None):
        """Construye el dialogo con dos sub-vistas de un mismo QtInteractor y encuadra la camara sobre el original."""
        super().__init__(parent)
        self.setWindowTitle("Comparar original vs. decimado")
        self.resize(1100, 650)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.plotter = QtInteractor(self, shape=(1, 2))
        self.plotter.set_background("#24262c")
        layout.addWidget(self.plotter.interactor)

        self.plotter.subplot(0, 0)
        self.plotter.add_mesh(original, show_edges=True, color="#d8dadd", edge_color="#101114")
        self.plotter.add_text(
            f"Original: {original.n_points} vert / {original.n_cells} tris",
            color="white",
            font_size=10,
        )

        self.plotter.subplot(0, 1)
        self.plotter.add_mesh(decimated, show_edges=True, color="#d8dadd", edge_color="#101114")
        self.plotter.add_text(
            f"Decimado: {decimated.n_points} vert / {decimated.n_cells} tris",
            color="white",
            font_size=10,
        )

        self.plotter.link_views()
        self.plotter.subplot(0, 0)
        self.plotter.reset_camera()


class CollapsibleGroupBox(QGroupBox):
    """QGroupBox cuyo titulo actua de interruptor: un click lo pliega o
    despliega. Al plegarlo solo se ocultan sus widgets hijos (siguen
    existiendo y conservan su valor); se usa para que quepan muchas
    secciones en el panel sin obligar a hacer scroll todo el rato con
    grupos que en ese momento no interesan."""

    def __init__(self, title: str, parent=None, expanded: bool = True):
        super().__init__(parent)
        self._base_title = title
        self.setCheckable(True)
        self.setChecked(expanded)
        self._apply_expanded(expanded)
        self.toggled.connect(self._apply_expanded)

    def _apply_expanded(self, expanded: bool):
        """Antepone una flecha (desplegado/plegado) al titulo y muestra u
        oculta los widgets hijos directos del layout del grupo."""
        arrow = "▾" if expanded else "▸"
        self.setTitle(f"{arrow} {self._base_title}")
        layout = self.layout()
        if layout is not None:
            for i in range(layout.count()):
                widget = layout.itemAt(i).widget()
                if widget is not None:
                    widget.setVisible(expanded)


class AdaptiveRemesherWindow(QMainWindow):
    """Ventana principal: viewport 3D + panel de controles para limpiar,
    proteger manualmente y decimar de forma adaptativa una malla."""

    def __init__(self):
        """Construye el viewport 3D y el panel de controles, e inicializa el estado de la malla/proteccion."""
        super().__init__()
        self.setWindowTitle("Adaptive Remesher — prototipo")
        self.resize(1300, 800)

        self._panel_state = self._load_panel_state()

        self.original_mesh: pv.PolyData | None = None
        self.decimated_mesh: pv.PolyData | None = None
        self.protected_mask: np.ndarray | None = None
        self.point_kdtree: cKDTree | None = None
        self.protected_actor = None
        self._is_painting = False
        self._paint_picker = vtk.vtkPointPicker()
        self._paint_picker.SetTolerance(0.005)
        self._undo_stack: list[dict] = []
        self._redo_stack: list[dict] = []
        self._max_history = 15
        self._last_run_info: dict | None = None
        self._rect_origin = None
        self._rect_erase = False
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(150)
        self._preview_timer.timeout.connect(self._update_live_preview)
        # Caches de un solo hueco (curvatura, costuras UV) para no repetir
        # calculos caros sobre la misma malla en cada redibujado o en cada
        # tick de la vista previa en tiempo real: se invalidan solas en
        # cuanto cambia la identidad de la malla (nueva carga, limpieza,
        # deshacer/rehacer...).
        self._curvature_cache: tuple | None = None
        self._uv_seam_cache: tuple | None = None
        self._last_brush_point: np.ndarray | None = None

        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.plotter = QtInteractor(self)
        self.plotter.set_background("#24262c")
        # Intercepta el raton a nivel de Qt, antes de que llegue a VTK: es la
        # unica forma fiable de evitar que el estilo de camara consuma el
        # arrastre mientras se pinta/borra (los observadores de VTK no
        # bastan, ver _on_brush_toggled/eventFilter).
        self.plotter.interactor.installEventFilter(self)
        self._rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self.plotter.interactor)

        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(14, 14, 14, 14)
        panel_layout.setSpacing(10)

        # El panel ya no cabe entero en una ventana de tamano normal (se
        # comprobo: necesita ~1150px de alto), asi que va dentro de un
        # scroll para que ningun control -ni el boton "Aplicar decimacion"-
        # quede inaccesible en pantallas mas pequenas.
        scroll = QScrollArea()
        scroll.setWidget(panel)
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(260)
        scroll.setMaximumWidth(700)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # Splitter en vez de un ancho fijo: el borde entre el viewport y el
        # panel se puede arrastrar con el raton para hacer el panel mas
        # ancho o mas estrecho (dentro de 260-700px), en vez de quedar
        # bloqueado a 336px.
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(6)
        splitter.addWidget(self.plotter.interactor)
        splitter.addWidget(scroll)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([self.width() - 336, 336])
        layout.addWidget(splitter)

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
        load_btn.clicked.connect(self.load_mesh)
        panel_layout.addWidget(load_btn)

        undo_redo_row = QHBoxLayout()
        self.undo_btn = QPushButton("Deshacer")
        self.undo_btn.setToolTip(tip(
            "Deshace la ultima accion (limpieza, trazo de pincel, borrado "
            "de marcas, decimacion o restaurar original). Atajo: Ctrl+Z."
        ))
        self.undo_btn.setEnabled(False)
        self.undo_btn.clicked.connect(self.undo)
        self.redo_btn = QPushButton("Rehacer")
        self.redo_btn.setToolTip(tip(
            "Rehace la ultima accion deshecha. Atajo: Ctrl+Y."
        ))
        self.redo_btn.setEnabled(False)
        self.redo_btn.clicked.connect(self.redo)
        undo_redo_row.addWidget(self.undo_btn)
        undo_redo_row.addWidget(self.redo_btn)
        panel_layout.addLayout(undo_redo_row)

        QShortcut(QKeySequence.Undo, self, activated=self.undo)
        QShortcut(QKeySequence.Redo, self, activated=self.redo)
        QShortcut(QKeySequence("Ctrl+Y"), self, activated=self.redo)

        # --- Perfiles de configuracion ---
        preset_group = self._add_collapsible_group("presets", "Perfiles de configuracion")
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
        save_preset_btn.clicked.connect(self.save_preset)
        load_preset_btn = QPushButton("Cargar preset...")
        load_preset_btn.setToolTip(tip(
            "Carga un preset guardado previamente y aplica sus valores a "
            "todos los controles del panel. No toca la malla cargada ni "
            "las marcas de proteccion manual existentes."
        ))
        load_preset_btn.clicked.connect(self.load_preset)
        preset_grid.addWidget(save_preset_btn, 0, 0)
        preset_grid.addWidget(load_preset_btn, 0, 1)
        panel_layout.addWidget(preset_group)

        self.stats_label = QLabel("Sin malla cargada")
        self.stats_label.setWordWrap(True)
        panel_layout.addWidget(self.stats_label)

        # --- Limpieza previa ---
        clean_group = self._add_collapsible_group("clean", "Limpieza previa (PyMeshLab)")
        clean_grid = QGridLayout(clean_group)

        self.auto_clean_cb = QCheckBox("Limpiar automaticamente al cargar")
        self.auto_clean_cb.setChecked(True)
        self.auto_clean_cb.setToolTip(tip(
            "Si esta marcado, cada vez que cargues una malla se le aplica "
            "automaticamente la limpieza de PyMeshLab (vertices duplicados, "
            "no-manifold basico y fragmentos flotantes) antes de mostrarla."
        ))
        clean_grid.addWidget(self.auto_clean_cb, 0, 0, 1, 2)

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
        self.min_fragment_slider = QSlider(Qt.Horizontal)
        self.min_fragment_slider.setRange(0, 20)
        self.min_fragment_slider.setValue(2)
        self.min_fragment_slider.setToolTip(fragment_tip)
        self.min_fragment_spin = QSpinBox()
        self.min_fragment_spin.setRange(0, 20)
        self.min_fragment_spin.setValue(2)
        self.min_fragment_spin.setSuffix("%")
        self.min_fragment_spin.setToolTip(fragment_tip)
        self.min_fragment_slider.valueChanged.connect(self.min_fragment_spin.setValue)
        self.min_fragment_spin.valueChanged.connect(self.min_fragment_slider.setValue)
        clean_grid.addWidget(self.min_fragment_slider, 2, 0)
        clean_grid.addWidget(self.min_fragment_spin, 2, 1)

        self.close_holes_cb = QCheckBox("Rellenar agujeros pequenos")
        self.close_holes_cb.setChecked(False)
        self.close_holes_cb.setToolTip(tip(
            "Cierra con caras nuevas los agujeros de hasta el tamano "
            "indicado (utiles tras quitar fragmentos). Cuidado: con un "
            "tamano demasiado alto puede cerrar huecos que si son "
            "intencionados (ojos, boca, aberturas)."
        ))
        clean_grid.addWidget(self.close_holes_cb, 3, 0, 1, 2)

        hole_size_label = QLabel("Tamano max. de agujero (aristas)")
        hole_size_tip = tip(
            "Solo se rellenan agujeros delimitados por hasta este numero "
            "de aristas. Subelo para cerrar huecos mas grandes; bajalo "
            "para no tocar aberturas grandes que pueden ser intencionadas."
        )
        hole_size_label.setToolTip(hole_size_tip)
        clean_grid.addWidget(hole_size_label, 4, 0, 1, 2)
        self.max_hole_size_spin = QSpinBox()
        self.max_hole_size_spin.setRange(3, 500)
        self.max_hole_size_spin.setValue(30)
        self.max_hole_size_spin.setToolTip(hole_size_tip)
        clean_grid.addWidget(self.max_hole_size_spin, 5, 0, 1, 2)

        self.fix_orientation_cb = QCheckBox("Reorientar caras de forma coherente")
        self.fix_orientation_cb.setChecked(False)
        self.fix_orientation_cb.setToolTip(tip(
            "Corrige caras con la normal invertida (defecto tipico de "
            "generacion por IA: algunas caras miran hacia dentro y otras "
            "hacia fuera en la misma superficie) reorientandolas todas de "
            "forma coherente entre si. Arregla sombreado invertido o "
            "\"agujeros\" que en realidad son caras del reves. No garantiza "
            "que el resultado final mire hacia fuera (solo que todas las "
            "caras conectadas miran igual)."
        ))
        clean_grid.addWidget(self.fix_orientation_cb, 6, 0, 1, 2)

        clean_now_btn = QPushButton("Limpiar ahora")
        clean_now_btn.setToolTip(tip(
            "Vuelve a aplicar la limpieza (con el umbral de fragmento "
            "actual) sobre la malla ya cargada. Como cambia el numero de "
            "vertices, borra las marcas de proteccion existentes."
        ))
        clean_now_btn.clicked.connect(self.clean_now)
        clean_grid.addWidget(clean_now_btn, 7, 0, 1, 2)

        panel_layout.addWidget(clean_group)

        # --- Visualizacion ---
        view_group = self._add_collapsible_group("view", "Visualizacion")
        view_grid = QGridLayout(view_group)

        self.curvature_cb = QCheckBox("Mapa de curvatura (malla original)")
        self.curvature_cb.setChecked(False)
        self.curvature_cb.setToolTip(tip(
            "Colorea la malla original segun su curvatura media (azul/rojo "
            "= zonas planas/curvas), para ver de un vistazo donde conviene "
            "pintar zonas protegidas antes de decimar. Solo se aplica "
            "mientras se ve la malla original, no el resultado decimado."
        ))
        self.curvature_cb.toggled.connect(self._on_curvature_toggled)
        view_grid.addWidget(self.curvature_cb, 0, 0)

        self.live_preview_cb = QCheckBox("Vista previa en tiempo real")
        self.live_preview_cb.setChecked(False)
        self.live_preview_cb.setToolTip(tip(
            "Mientras mueves la agresividad, el angulo de detalle o "
            "cambias de motor, muestra al momento una previsualizacion "
            "del resultado (sin las hasta 3 pasadas de auto-ajuste por "
            "error, para que sea rapida). No sustituye a 'Aplicar "
            "decimacion': pulsalo para confirmar el resultado y poder "
            "exportarlo o compararlo."
        ))
        self.live_preview_cb.toggled.connect(self._on_live_preview_toggled)
        view_grid.addWidget(self.live_preview_cb, 1, 0)

        panel_layout.addWidget(view_group)

        # --- Proteccion manual ---
        protect_group = self._add_collapsible_group("protect", "Proteccion manual")
        protect_grid = QGridLayout(protect_group)

        self.paint_btn = QPushButton("Pintar")
        self.paint_btn.setCheckable(True)
        self.paint_btn.setToolTip(tip(
            "Activa el pincel de proteccion: haz click y arrastra sobre la "
            "malla en el viewport para marcar en rojo los puntos que NUNCA "
            "se deben simplificar (caras, logos, dedos, detalles finos). "
            "Se desactiva volviendo a pulsar este boton."
        ))
        self.erase_btn = QPushButton("Borrar")
        self.erase_btn.setObjectName("eraseButton")
        self.erase_btn.setCheckable(True)
        self.erase_btn.setToolTip(tip(
            "Activa el pincel de borrado: click y arrastra sobre puntos ya "
            "marcados en rojo para quitarles la proteccion, con el mismo "
            "radio de pincel que el modo Pintar."
        ))
        self.paint_btn.toggled.connect(
            lambda checked: self._on_brush_toggled(self.paint_btn, checked)
        )
        self.erase_btn.toggled.connect(
            lambda checked: self._on_brush_toggled(self.erase_btn, checked)
        )
        protect_grid.addWidget(self.paint_btn, 0, 0)
        protect_grid.addWidget(self.erase_btn, 0, 1)

        self.rect_btn = QPushButton("Rectangulo")
        self.rect_btn.setCheckable(True)
        self.rect_btn.setToolTip(tip(
            "Activa la seleccion por rectangulo: arrastra sobre el "
            "viewport para proteger de golpe todos los vertices dentro "
            "del rectangulo (sin test de profundidad: tambien marca "
            "puntos ocultos detras de la malla). Manten pulsado Mayus "
            "mientras arrastras para desproteger en vez de proteger. "
            "Util para zonas grandes de forma irregular donde el pincel "
            "circular es lento."
        ))
        self.rect_btn.toggled.connect(
            lambda checked: self._on_brush_toggled(self.rect_btn, checked)
        )
        protect_grid.addWidget(self.rect_btn, 6, 0, 1, 2)

        mask_export_btn = QPushButton("Guardar mascara...")
        mask_export_btn.setToolTip(tip(
            "Guarda en un .json los indices de los vertices protegidos "
            "actualmente, para reutilizar la misma proteccion en otra "
            "sesion o en otra malla con identica topologia (mismo numero "
            "y orden de vertices)."
        ))
        mask_export_btn.clicked.connect(self.export_protection_mask)
        mask_import_btn = QPushButton("Cargar mascara...")
        mask_import_btn.setToolTip(tip(
            "Carga una mascara de proteccion guardada y sustituye las "
            "marcas actuales. Solo se aplica si el numero de vertices "
            "coincide exactamente con el de la malla actual."
        ))
        mask_import_btn.clicked.connect(self.import_protection_mask)
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
        self.brush_slider = QSlider(Qt.Horizontal)
        self.brush_slider.setRange(1, 20)
        self.brush_slider.setValue(5)
        self.brush_slider.setToolTip(brush_tip)
        self.brush_spin = QSpinBox()
        self.brush_spin.setRange(1, 20)
        self.brush_spin.setValue(5)
        self.brush_spin.setSuffix("%")
        self.brush_spin.setToolTip(brush_tip)
        self.brush_slider.valueChanged.connect(self.brush_spin.setValue)
        self.brush_spin.valueChanged.connect(self.brush_slider.setValue)
        protect_grid.addWidget(self.brush_slider, 2, 0)
        protect_grid.addWidget(self.brush_spin, 2, 1)

        clear_marks_btn = QPushButton("Borrar todas las marcas")
        clear_marks_btn.setToolTip(tip(
            "Quita de golpe todas las marcas de proteccion de la malla "
            "actual (equivale a borrar todo con el pincel de borrado, pero "
            "en un solo click)."
        ))
        clear_marks_btn.clicked.connect(self.clear_protection)
        protect_grid.addWidget(clear_marks_btn, 3, 0, 1, 2)

        self.protected_count_label = QLabel("Puntos protegidos: 0")
        self.protected_count_label.setToolTip(tip(
            "Numero de vertices actualmente marcados como protegidos. Esos "
            "vertices y las caras que tocan quedan excluidos del colapso al "
            "aplicar la decimacion."
        ))
        protect_grid.addWidget(self.protected_count_label, 4, 0, 1, 2)

        self.protect_uv_seams_cb = QCheckBox("Proteger costuras UV automaticamente")
        self.protect_uv_seams_cb.setChecked(True)
        self.protect_uv_seams_cb.setToolTip(tip(
            "Si la malla tiene textura, detecta los vertices duplicados que "
            "forman una costura UV (donde la textura se corta al "
            "desenvolver) y los protege automaticamente, ademas de tus "
            "marcas manuales. Sin esto, decimar puede romper la textura "
            "justo en la costura. No hace nada si la malla no tiene UVs."
        ))
        protect_grid.addWidget(self.protect_uv_seams_cb, 5, 0, 1, 2)

        panel_layout.addWidget(protect_group)

        # --- Decimacion ---
        params_group = self._add_collapsible_group("decimate", "Decimacion adaptativa")
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
        self.engine_combo = QComboBox()
        self.engine_combo.addItem("vtkDecimatePro (angulo fijo)")
        self.engine_combo.addItem("Quadric Error Metric (curvatura)")
        self.engine_combo.setToolTip(engine_tip)
        self.engine_combo.currentIndexChanged.connect(self._on_engine_changed)
        self.engine_combo.currentIndexChanged.connect(self._schedule_preview)
        grid.addWidget(self.engine_combo, 1, 0, 1, 2)

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
        self.reduction_slider = QSlider(Qt.Horizontal)
        self.reduction_slider.setRange(0, 99)
        self.reduction_slider.setValue(50)
        self.reduction_slider.setToolTip(reduction_tip)
        self.reduction_slider.valueChanged.connect(
            lambda v: self.reduction_value_label.setText(f"{v}%")
        )
        self.reduction_slider.valueChanged.connect(self._schedule_preview)
        grid.addWidget(self.reduction_slider, 3, 0)
        self.reduction_value_label = QLabel("50%")
        grid.addWidget(self.reduction_value_label, 3, 1)

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
        self.angle_slider = QSlider(Qt.Horizontal)
        self.angle_slider.setRange(1, 90)
        self.angle_slider.setValue(15)
        self.angle_slider.setToolTip(angle_tip)
        self.angle_slider.valueChanged.connect(
            lambda v: self.angle_value_label.setText(f"{v}°")
        )
        self.angle_slider.valueChanged.connect(self._schedule_preview)
        grid.addWidget(self.angle_slider, 5, 0)
        self.angle_value_label = QLabel("15°")
        grid.addWidget(self.angle_value_label, 5, 1)

        self.preserve_topology_cb = QCheckBox("Preservar topologia")
        self.preserve_topology_cb.setChecked(True)
        self.preserve_topology_cb.setToolTip(tip(
            "Impide que la decimacion parta la superficie en piezas "
            "separadas o cree agujeros no deseados. Recomendado activado "
            "salvo que necesites la maxima reduccion posible y no te "
            "importe cambiar la topologia."
        ))
        grid.addWidget(self.preserve_topology_cb, 6, 0, 1, 2)

        self.boundary_vertex_deletion_cb = QCheckBox("Permitir simplificar bordes")
        self.boundary_vertex_deletion_cb.setChecked(False)
        self.boundary_vertex_deletion_cb.setToolTip(tip(
            "Si esta desactivado (por defecto), los vertices en bordes "
            "abiertos de la malla (huecos, superficies no cerradas) apenas "
            "se tocan. Activalo si tu malla tiene bordes abiertos que "
            "tambien quieres simplificar; puede deformar el contorno. "
            "Afecta a ambos motores (en QEM, sube el peso de los bordes)."
        ))
        grid.addWidget(self.boundary_vertex_deletion_cb, 7, 0, 1, 2)

        self.splitting_cb = QCheckBox("Permitir dividir superficie (splitting)")
        self.splitting_cb.setChecked(True)
        self.splitting_cb.setToolTip(tip(
            "Deja que vtkDecimatePro divida la superficie a lo largo de "
            "aristas no preservadas cuando eso ayuda a reducir mas sin "
            "perder forma. Desactivarlo suele dar menos reduccion pero una "
            "malla mas \"limpia\" en una sola pieza continua. Solo aplica "
            "al motor vtkDecimatePro."
        ))
        grid.addWidget(self.splitting_cb, 8, 0, 1, 2)

        panel_layout.addWidget(params_group)

        # --- Opciones especificas de Quadric Error Metric ---
        self.qem_group = self._add_collapsible_group("qem", "Opciones Quadric Error Metric")
        qem_grid = QGridLayout(self.qem_group)

        self.volume_preservation_cb = QCheckBox("Preservar volumen")
        self.volume_preservation_cb.setChecked(True)
        self.volume_preservation_cb.setToolTip(tip(
            "Ajusta el colapso para que el volumen total de la malla "
            "cambie lo menos posible. Recomendado activado; desactivarlo "
            "es un poco mas rapido pero puede \"encoger\" la forma en "
            "reducciones agresivas."
        ))
        qem_grid.addWidget(self.volume_preservation_cb, 0, 0, 1, 2)

        self.uv_attribute_metric_cb = QCheckBox("Penalizar distorsion UV en el coste")
        self.uv_attribute_metric_cb.setChecked(True)
        self.uv_attribute_metric_cb.setToolTip(tip(
            "Ademas de la geometria, tiene en cuenta cuanto estiraria cada "
            "colapso las coordenadas de textura, evitando deformar la "
            "textura incluso lejos de las costuras protegidas. Solo tiene "
            "efecto si la malla tiene UVs."
        ))
        qem_grid.addWidget(self.uv_attribute_metric_cb, 1, 0, 1, 2)

        uv_weight_label = QLabel("Peso de la distorsion UV")
        uv_weight_tip = tip(
            "Cuanto pesa la distorsion de textura frente al error "
            "geometrico puro al decidir cada colapso. Subelo si la "
            "textura sale mas deformada de lo que te gustaria; bajalo si "
            "prefieres priorizar la forma sobre la textura."
        )
        uv_weight_label.setToolTip(uv_weight_tip)
        qem_grid.addWidget(uv_weight_label, 2, 0, 1, 2)
        self.uv_weight_spin = QDoubleSpinBox()
        self.uv_weight_spin.setRange(0.1, 10.0)
        self.uv_weight_spin.setSingleStep(0.1)
        self.uv_weight_spin.setValue(1.0)
        self.uv_weight_spin.setToolTip(uv_weight_tip)
        qem_grid.addWidget(self.uv_weight_spin, 3, 0, 1, 2)

        self.normals_weight_cb = QCheckBox("Preservar normales (evita facetado)")
        self.normals_weight_cb.setChecked(False)
        self.normals_weight_cb.setToolTip(tip(
            "Ademas de la posicion (y la UV, si esta activo), penaliza en "
            "el coste de cada colapso cuanto cambiaria la normal de la "
            "superficie. Sin esto, QEM solo mide error de posicion y puede "
            "aplanar zonas suavemente curvas en facetas visibles a simple "
            "vista aunque la forma general apenas se mueva. Calcula las "
            "normales de vertice automaticamente si la malla no las trae."
        ))
        qem_grid.addWidget(self.normals_weight_cb, 4, 0, 1, 2)

        normals_weight_label = QLabel("Peso de la normal")
        normals_weight_tip = tip(
            "Cuanto pesa el cambio de normal frente al error geometrico "
            "puro al decidir cada colapso. Subelo si sigues viendo "
            "facetado en zonas curvas; bajalo si prefieres priorizar la "
            "reduccion sobre el sombreado."
        )
        normals_weight_label.setToolTip(normals_weight_tip)
        qem_grid.addWidget(normals_weight_label, 5, 0, 1, 2)
        self.normals_weight_spin = QDoubleSpinBox()
        self.normals_weight_spin.setRange(0.1, 10.0)
        self.normals_weight_spin.setSingleStep(0.1)
        self.normals_weight_spin.setValue(1.0)
        self.normals_weight_spin.setToolTip(normals_weight_tip)
        qem_grid.addWidget(self.normals_weight_spin, 6, 0, 1, 2)

        panel_layout.addWidget(self.qem_group)
        self._on_engine_changed(self.engine_combo.currentIndex())

        # --- Validacion por error ---
        error_group = self._add_collapsible_group("error", "Validacion por error de superficie")
        error_grid = QGridLayout(error_group)

        self.auto_error_cb = QCheckBox("Auto-proteger zonas con error alto")
        self.auto_error_cb.setChecked(True)
        self.auto_error_cb.setToolTip(tip(
            "Tras decimar, mide cuanto se aleja cada punto del resultado de "
            "la superficie original (aproxima la distancia de Hausdorff). "
            "Donde el error supera el umbral, protege esos vertices en la "
            "malla original y vuelve a decimar (hasta 3 pasadas), en vez de "
            "fiarlo todo al angulo de deteccion fijo."
        ))
        error_grid.addWidget(self.auto_error_cb, 0, 0, 1, 2)

        error_threshold_label = QLabel("Error maximo permitido (% diagonal)")
        error_threshold_tip = tip(
            "Umbral de error, como porcentaje de la diagonal del bounding "
            "box, a partir del cual una zona decimada se considera "
            "demasiado deformada y se protege automaticamente. Bajalo para "
            "exigir mas fidelidad (mas zonas se auto-protegen, menos "
            "reduccion real); subelo para permitir mas deformacion."
        )
        error_threshold_label.setToolTip(error_threshold_tip)
        error_grid.addWidget(error_threshold_label, 1, 0, 1, 2)
        self.error_threshold_spin = QDoubleSpinBox()
        self.error_threshold_spin.setRange(0.1, 10.0)
        self.error_threshold_spin.setSingleStep(0.1)
        self.error_threshold_spin.setValue(1.0)
        self.error_threshold_spin.setSuffix("%")
        self.error_threshold_spin.setToolTip(error_threshold_tip)
        error_grid.addWidget(self.error_threshold_spin, 2, 0, 1, 2)

        panel_layout.addWidget(error_group)

        apply_btn = QPushButton("Aplicar decimacion")
        apply_btn.setObjectName("primaryButton")
        apply_btn.setToolTip(tip(
            "Ejecuta el motor de decimacion elegido sobre la malla "
            "original con los parametros actuales. Si hay zonas "
            "protegidas (pintadas, costuras UV o auto-detectadas por "
            "error), solo se decima el resto y luego se recompone el "
            "resultado."
        ))
        apply_btn.clicked.connect(self.apply_decimation)
        panel_layout.addWidget(apply_btn)

        reset_btn = QPushButton("Restaurar original")
        reset_btn.setToolTip(tip(
            "Descarta el resultado decimado y vuelve a mostrar la malla "
            "original tal como se cargo (o se limpio), conservando las "
            "marcas de proteccion."
        ))
        reset_btn.clicked.connect(self.reset_mesh)
        panel_layout.addWidget(reset_btn)

        compare_btn = QPushButton("Comparar original/decimado")
        compare_btn.setToolTip(tip(
            "Abre una ventana con dos vistas lado a lado -original y "
            "decimado- con las camaras sincronizadas, para juzgar la "
            "perdida de detalle. Necesita haber aplicado una decimacion "
            "primero."
        ))
        compare_btn.clicked.connect(self.open_comparison)
        panel_layout.addWidget(compare_btn)

        self.export_report_cb = QCheckBox("Exportar informe .json junto a la malla")
        self.export_report_cb.setChecked(True)
        self.export_report_cb.setToolTip(tip(
            "Al exportar, ademas del archivo de malla, guarda un .json con "
            "el mismo nombre (sufijo _report) con las estadisticas de la "
            "ultima decimacion: motor, reduccion, puntos protegidos y "
            "error medido."
        ))
        panel_layout.addWidget(self.export_report_cb)

        export_btn = QPushButton("Exportar malla...")
        export_btn.setToolTip(tip(
            "Guarda en disco la malla decimada; si todavia no has aplicado "
            "ninguna decimacion, guarda la malla original (ya limpiada, si "
            "aplica). OBJ y PLY conservan las coordenadas UV si la malla "
            "tiene textura; STL no soporta UV/textura (limitacion del "
            "propio formato)."
        ))
        export_btn.clicked.connect(self.export_mesh)
        panel_layout.addWidget(export_btn)

        # --- Procesamiento por lotes ---
        batch_group = self._add_collapsible_group("batch", "Procesamiento por lotes")
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
        batch_btn.clicked.connect(self.run_batch)
        batch_layout.addWidget(batch_btn)

        panel_layout.addWidget(batch_group)

        panel_layout.addStretch(1)

    # --- bloques plegables ---

    def _load_panel_state(self) -> dict:
        """Carga que bloques del panel estaban plegados/desplegados en la
        sesion anterior desde `PANEL_STATE_PATH`. Si el archivo no existe o
        esta corrupto, no pliega nada por defecto (todos desplegados)."""
        try:
            with open(PANEL_STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_panel_state(self):
        """Guarda que bloques del panel estan plegados/desplegados, para restaurarlo la proxima vez que se abra la app."""
        try:
            with open(PANEL_STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(self._panel_state, f)
        except Exception:
            pass

    def _add_collapsible_group(self, key: str, title: str) -> CollapsibleGroupBox:
        """Crea un `CollapsibleGroupBox` restaurando su estado plegado/
        desplegado guardado (por defecto desplegado si no hay nada
        guardado para `key`), y lo conecta para persistir cualquier
        cambio que el usuario haga al plegarlo o desplegarlo."""
        expanded = self._panel_state.get(key, True)
        group = CollapsibleGroupBox(title, expanded=expanded)
        group.toggled.connect(lambda checked, k=key: self._on_panel_toggled(k, checked))
        return group

    def _on_panel_toggled(self, key: str, expanded: bool):
        """Guarda de inmediato el nuevo estado plegado/desplegado de un bloque del panel."""
        self._panel_state[key] = expanded
        self._save_panel_state()

    # --- deshacer / rehacer ---

    def _snapshot(self) -> dict:
        """Guarda el estado relevante para el historial de deshacer/rehacer.
        La malla original y la decimada NO se copian (solo se guarda la
        referencia al objeto): ninguna operacion de la app las modifica en
        el sitio una vez publicadas en self.original_mesh/decimated_mesh
        -limpiar o decimar siempre crea un objeto pv.PolyData nuevo en vez
        de mutar el existente-, asi que una copia profunda seria trabajo
        perdido y, en mallas grandes, un coste notable que antes se pagaba
        en cada trazo de pincel (un `_push_undo` por cada click). Solo
        `protected_mask` es un array que si se muta en el sitio (el pincel
        escribe directamente en el), por eso ese si necesita copiarse."""
        return {
            "original_mesh": self.original_mesh,
            "protected_mask": self.protected_mask.copy() if self.protected_mask is not None else None,
            "decimated_mesh": self.decimated_mesh,
        }

    def _push_undo(self):
        """Guarda el estado actual en la pila de deshacer, antes de que una accion lo modifique; invalida la pila de rehacer."""
        if self.original_mesh is None:
            return
        self._undo_stack.append(self._snapshot())
        if len(self._undo_stack) > self._max_history:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._update_undo_redo_buttons()

    def _restore_snapshot(self, state: dict):
        """Aplica un estado guardado (de deshacer o rehacer) y refresca la vista, las estadisticas y el overlay de proteccion."""
        self.original_mesh = state["original_mesh"]
        self.protected_mask = state["protected_mask"]
        self.decimated_mesh = state["decimated_mesh"]
        self.point_kdtree = (
            cKDTree(self.original_mesh.points) if self.original_mesh is not None else None
        )
        if self.original_mesh is not None:
            shown = self.decimated_mesh if self.decimated_mesh is not None else self.original_mesh
            self.show_mesh(shown, is_original=self.decimated_mesh is None)
            self.update_stats(shown)
        self._refresh_protection_overlay()
        self._update_undo_redo_buttons()

    def undo(self):
        """Retrocede al estado guardado mas reciente, moviendo el estado actual a la pila de rehacer."""
        if not self._undo_stack:
            return
        self._redo_stack.append(self._snapshot())
        self._restore_snapshot(self._undo_stack.pop())

    def redo(self):
        """Rehace la ultima accion deshecha, moviendo el estado actual a la pila de deshacer."""
        if not self._redo_stack:
            return
        self._undo_stack.append(self._snapshot())
        self._restore_snapshot(self._redo_stack.pop())

    def _update_undo_redo_buttons(self):
        """Habilita/deshabilita los botones de deshacer/rehacer segun si hay algo que deshacer/rehacer."""
        self.undo_btn.setEnabled(bool(self._undo_stack))
        self.redo_btn.setEnabled(bool(self._redo_stack))

    # --- carga / limpieza ---

    def load_mesh(self):
        """Abre un dialogo de archivo, carga la malla elegida, la limpia si toca y la muestra."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Cargar malla",
            "",
            "Mallas 3D (*.obj *.stl *.ply *.vtp *.glb *.gltf)",
        )
        if not path:
            return

        try:
            with busy_cursor():
                mesh = pv.read(path).extract_surface().triangulate()
                if mesh.n_points == 0 or mesh.n_cells == 0:
                    raise ValueError("El archivo no contiene una malla de superficie valida.")
                ensure_active_texture_coords(mesh)
                if self.auto_clean_cb.isChecked():
                    mesh = clean_mesh(
                        mesh,
                        self.min_fragment_slider.value(),
                        self.close_holes_cb.isChecked(),
                        self.max_hole_size_spin.value(),
                        self.fix_orientation_cb.isChecked(),
                    )
        except Exception as exc:
            QMessageBox.critical(self, "Error al cargar la malla", str(exc))
            return

        self.original_mesh = mesh
        self.decimated_mesh = None
        self._reset_protection(rebuild_kdtree=True)
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._update_undo_redo_buttons()
        self.show_mesh(mesh, is_original=True)
        self.update_stats(mesh)

    def clean_now(self):
        """Vuelve a limpiar la malla actual con el umbral de fragmento configurado y reinicia la proteccion."""
        if self.original_mesh is None:
            return
        try:
            with busy_cursor():
                cleaned = clean_mesh(
                    self.original_mesh,
                    self.min_fragment_slider.value(),
                    self.close_holes_cb.isChecked(),
                    self.max_hole_size_spin.value(),
                    self.fix_orientation_cb.isChecked(),
                )
                if cleaned.n_points == 0 or cleaned.n_cells == 0:
                    raise ValueError(
                        "La limpieza ha dejado la malla vacia (umbral de fragmento "
                        "demasiado alto para este modelo)."
                    )
        except Exception as exc:
            QMessageBox.critical(self, "Error al limpiar la malla", str(exc))
            return
        self._push_undo()
        self.original_mesh = cleaned
        self.decimated_mesh = None
        self._reset_protection(rebuild_kdtree=True)
        self.show_mesh(cleaned, is_original=True)
        self.update_stats(cleaned)

    # --- proteccion manual (pintar/borrar) ---

    def _on_brush_toggled(self, button: QPushButton, checked: bool):
        """Activa como mucho una herramienta de proteccion a la vez (pintar,
        borrar o rectangulo); si no hay malla cargada, cancela la
        activacion. Si se esta viendo un resultado decimado ya aplicado,
        vuelve primero a la malla original: las herramientas de proteccion
        trabajan contra `self.original_mesh` (buscan el punto mas cercano
        en su KDTree), y tras decimar (sobre todo con el motor QEM, que si
        mueve vertices) eso ya no coincidiria con lo mostrado. Si solo hay
        una vista previa en tiempo real sin aplicar (el resultado 'real'
        sigue siendo el original), basta con refrescar la vista sin gastar
        un paso de deshacer."""
        if checked and self.original_mesh is None:
            button.setChecked(False)
            return
        if checked:
            if self.decimated_mesh is not None:
                self.reset_mesh()
            elif self.live_preview_cb.isChecked():
                self.show_mesh(self.original_mesh, is_original=True)
                self._refresh_protection_overlay()
        if checked:
            for other in (self.paint_btn, self.erase_btn, self.rect_btn):
                if other is not button:
                    other.setChecked(False)
        debug(f"BRUSH {button.text()} {'ON' if checked else 'OFF'}")
        self._is_painting = False
        self._rubber_band.hide()

    def eventFilter(self, obj, event):
        """Intercepta el raton sobre el viewport 3D mientras una herramienta
        de proteccion esta activa (pincel de pintar/borrar, o seleccion por
        rectangulo), para operar sobre la malla en vez de rotar la camara."""
        if obj is not self.plotter.interactor or self.original_mesh is None:
            return super().eventFilter(obj, event)

        et = event.type()
        brush_active = self.paint_btn.isChecked() or self.erase_btn.isChecked()
        if brush_active:
            if et == QEvent.Type.MouseButtonPress and event.button() == Qt.LeftButton:
                self._push_undo()  # un solo paso de deshacer por trazo, no por movimiento
                self._is_painting = True
                self._last_brush_point = None
                self._apply_brush_at_qt_pos(event.position())
                return True
            if et == QEvent.Type.MouseMove and self._is_painting:
                self._apply_brush_at_qt_pos(event.position())
                return True
            if et == QEvent.Type.MouseButtonRelease and event.button() == Qt.LeftButton:
                self._is_painting = False
                return True
            return super().eventFilter(obj, event)

        if self.rect_btn.isChecked():
            if et == QEvent.Type.MouseButtonPress and event.button() == Qt.LeftButton:
                self._push_undo()  # un solo paso de deshacer por rectangulo
                self._rect_origin = event.position().toPoint()
                self._rect_erase = bool(event.modifiers() & Qt.ShiftModifier)
                self._rubber_band.setGeometry(QRect(self._rect_origin, self._rect_origin))
                self._rubber_band.show()
                return True
            if et == QEvent.Type.MouseMove and self._rubber_band.isVisible():
                rect = QRect(self._rect_origin, event.position().toPoint()).normalized()
                self._rubber_band.setGeometry(rect)
                return True
            if et == QEvent.Type.MouseButtonRelease and event.button() == Qt.LeftButton and self._rubber_band.isVisible():
                rect = QRect(self._rect_origin, event.position().toPoint()).normalized()
                self._rubber_band.hide()
                self._apply_rect_selection(rect)
                return True

        return super().eventFilter(obj, event)

    def _apply_brush_at_qt_pos(self, qt_pos):
        """Convierte una posicion de Qt (pixeles desde arriba-izquierda) a coordenadas VTK y aplica el pincel ahi."""
        height = self.plotter.interactor.height()
        x, y = qt_pos.x(), height - qt_pos.y()
        self._paint_picker.Pick(x, y, 0, self.plotter.renderer)
        if self._paint_picker.GetPointId() < 0:
            return
        self._apply_brush(self._paint_picker.GetPickPosition())

    def _apply_brush(self, point):
        """Marca o desmarca como protegidos los puntos dentro del radio de
        pincel alrededor de `point`. Si `point` esta muy cerca (menos de un
        15% del radio) del ultimo punto aplicado en el mismo trazo, se
        salta: el resultado seria practicamente el mismo circulo de puntos
        ya marcado, y en mallas grandes repetir la consulta al KDTree y el
        redibujado del overlay en cada micro-movimiento del raton (varias
        veces por segundo mientras se arrastra) es un coste innecesario."""
        radius = (self.brush_slider.value() / 100.0) * self.original_mesh.length
        point = np.asarray(point, dtype=float)
        if (
            self._last_brush_point is not None
            and np.linalg.norm(point - self._last_brush_point) < radius * 0.15
        ):
            return
        self._last_brush_point = point
        nearby = self.point_kdtree.query_ball_point(point, r=radius)
        if not nearby:
            return
        self.protected_mask[nearby] = not self.erase_btn.isChecked()
        self._refresh_protection_overlay()

    def _project_points_to_qt(self, points: np.ndarray):
        """Proyecta puntos 3D del mundo a coordenadas de pixel Qt (origen
        arriba-izquierda) usando la matriz de camara actual, para la
        seleccion por rectangulo. Vectorizado con numpy en vez de llamar a
        vtkCoordinate punto a punto, que seria demasiado lento con mallas
        de miles de vertices. Devuelve (xy, in_front): `in_front` marca los
        puntos delante de la camara (w>0 tras la proyeccion en perspectiva),
        los unicos con una posicion en pantalla valida."""
        renderer = self.plotter.renderer
        camera = renderer.GetActiveCamera()
        width = max(self.plotter.interactor.width(), 1)
        height = max(self.plotter.interactor.height(), 1)
        vtk_matrix = camera.GetCompositeProjectionTransformMatrix(width / height, -1, 1)
        matrix = np.array(
            [[vtk_matrix.GetElement(i, j) for j in range(4)] for i in range(4)]
        )
        homogeneous = np.hstack([points, np.ones((points.shape[0], 1))])
        clip = homogeneous @ matrix.T
        w = clip[:, 3]
        in_front = w > 1e-8
        safe_w = np.where(in_front, w, 1.0)
        ndc = clip[:, :3] / safe_w[:, None]
        display_x = (ndc[:, 0] + 1.0) / 2.0 * width
        display_y_bottom_up = (ndc[:, 1] + 1.0) / 2.0 * height
        qt_y = height - display_y_bottom_up
        return np.column_stack([display_x, qt_y]), in_front

    def _apply_rect_selection(self, rect: QRect):
        """Protege (o desprotege, si se arrastro con Mayus pulsado) todos
        los vertices originales cuya proyeccion en pantalla cae dentro del
        rectangulo `rect` (coordenadas Qt). No hace test de profundidad:
        tambien selecciona puntos ocultos detras de otra parte de la malla,
        una limitacion aceptable para un prototipo frente a implementar
        picking por frustum completo."""
        if rect.width() < 3 or rect.height() < 3:
            return
        xy, in_front = self._project_points_to_qt(self.original_mesh.points)
        inside = (
            in_front
            & (xy[:, 0] >= rect.left()) & (xy[:, 0] <= rect.right())
            & (xy[:, 1] >= rect.top()) & (xy[:, 1] <= rect.bottom())
        )
        if not inside.any():
            return
        self.protected_mask[inside] = not self._rect_erase
        self._refresh_protection_overlay()

    def clear_protection(self):
        """Borra todas las marcas de proteccion de la malla actual."""
        self._push_undo()
        self._reset_protection(rebuild_kdtree=False)

    def _reset_protection(self, rebuild_kdtree: bool):
        """Reinicia la mascara de proteccion (y opcionalmente el KDTree de puntos) para la malla actual."""
        if self.original_mesh is not None:
            self.protected_mask = np.zeros(self.original_mesh.n_points, dtype=bool)
            if rebuild_kdtree:
                self.point_kdtree = cKDTree(self.original_mesh.points)
        else:
            self.protected_mask = None
            self.point_kdtree = None
        if self.protected_actor is not None:
            self.plotter.remove_actor(self.protected_actor)
            self.protected_actor = None
        self.protected_count_label.setText("Puntos protegidos: 0")

    def _refresh_protection_overlay(self):
        """Redibuja los puntos protegidos en rojo sobre la malla, sin tocar la camara actual."""
        if self.protected_actor is not None:
            self.plotter.remove_actor(self.protected_actor)
            self.protected_actor = None
        count = int(self.protected_mask.sum()) if self.protected_mask is not None else 0
        if count:
            pts = self.original_mesh.points[self.protected_mask]
            cloud = pv.PolyData(pts)
            self.protected_actor = self.plotter.add_mesh(
                cloud,
                color="#ff4d4d",
                point_size=10,
                render_points_as_spheres=True,
                pickable=False,
                reset_camera=False,
            )
        self.protected_count_label.setText(f"Puntos protegidos: {count}")

    def export_protection_mask(self):
        """Guarda la mascara de proteccion actual (indices de vertices
        marcados) en un .json, para reutilizarla en otra sesion o en otra
        malla con la misma topologia (mismo numero y orden de vertices,
        p.ej. variantes de un mismo modelo)."""
        if self.protected_mask is None:
            QMessageBox.information(self, "Exportar mascara", "No hay ninguna malla cargada.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Exportar mascara de proteccion", "", "Mascara de proteccion (*.json)"
        )
        if not path:
            return
        try:
            data = {
                "n_points": int(self.protected_mask.size),
                "protected_indices": np.where(self.protected_mask)[0].tolist(),
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception as exc:
            QMessageBox.critical(self, "Error al exportar mascara", str(exc))

    def import_protection_mask(self):
        """Carga una mascara de proteccion guardada y la aplica a la malla
        actual, sustituyendo las marcas existentes. Solo funciona si el
        numero de vertices coincide exactamente con el de cuando se guardo
        (misma malla o una variante con identica topologia); si no
        coincide, avisa y no cambia nada."""
        if self.original_mesh is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Importar mascara de proteccion", "", "Mascara de proteccion (*.json)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            n_points = data.get("n_points")
            indices = np.array(data.get("protected_indices", []), dtype=int)
        except Exception as exc:
            QMessageBox.critical(self, "Error al importar mascara", str(exc))
            return
        if n_points != self.original_mesh.n_points:
            QMessageBox.warning(
                self,
                "Mascara incompatible",
                f"La mascara tiene {n_points} vertices guardados y la malla "
                f"actual tiene {self.original_mesh.n_points}: no coincide "
                "la topologia, no se ha aplicado.",
            )
            return
        self._push_undo()
        self.protected_mask = np.zeros(self.original_mesh.n_points, dtype=bool)
        if indices.size:
            self.protected_mask[indices] = True
        self._refresh_protection_overlay()

    # --- visualizacion ---

    def _curvature_for(self, mesh: pv.PolyData) -> np.ndarray:
        """Devuelve la curvatura media de `mesh`, cacheada por identidad del
        objeto (mas su numero de puntos, para descartar una coincidencia de
        id() con una malla ya liberada). Sin esto, activar el mapa de
        curvatura o simplemente alternar otra opcion de visualizacion que
        redibuja preservando la camara recalcularia la curvatura de toda la
        malla cada vez, un calculo notable en mallas grandes."""
        key = (id(mesh), mesh.n_points)
        if self._curvature_cache is not None and self._curvature_cache[0] == key:
            return self._curvature_cache[1]
        curvature = mesh.curvature("mean")
        self._curvature_cache = (key, curvature)
        return curvature

    def _uv_seam_mask_for(self, mesh: pv.PolyData) -> np.ndarray:
        """Devuelve la mascara de costuras UV de `mesh`, cacheada igual que
        `_curvature_for`. La proteccion de costuras se recalculaba antes en
        cada 'Aplicar decimacion' y en cada tick de la vista previa en
        tiempo real, aunque la malla no hubiera cambiado desde la ultima
        vez; en mallas de IA con muchas costuras eso se notaba al arrastrar
        el slider de agresividad con la vista previa activa."""
        key = (id(mesh), mesh.n_points)
        if self._uv_seam_cache is not None and self._uv_seam_cache[0] == key:
            return self._uv_seam_cache[1]
        seam_mask = uv_seam_point_mask(mesh)
        self._uv_seam_cache = (key, seam_mask)
        return seam_mask

    def show_mesh(self, mesh: pv.PolyData, is_original: bool, preserve_camera=None):
        """Limpia el viewport y muestra `mesh`. Si `is_original` y el mapa
        de curvatura esta activo, colorea por curvatura media en vez del
        gris solido habitual. Reencuadra la camara salvo que se pase
        `preserve_camera` (posicion previa a restaurar, para no perder el
        zoom al solo cambiar de modo de visualizacion)."""
        self.plotter.clear()
        if is_original and self.curvature_cb.isChecked():
            curvature = self._curvature_for(mesh)
            self.plotter.add_mesh(
                mesh,
                scalars=curvature,
                cmap="coolwarm",
                show_edges=True,
                edge_color="#101114",
                scalar_bar_args={"color": "#e4e6eb"},
            )
        else:
            self.plotter.add_mesh(mesh, show_edges=True, color="#d8dadd", edge_color="#101114")
        if preserve_camera is not None:
            self.plotter.camera_position = preserve_camera
        else:
            self.plotter.reset_camera()
        self.protected_actor = None

    def _on_curvature_toggled(self, _checked: bool):
        """Redibuja la malla actual (sin resetear la camara) al activar o desactivar el mapa de curvatura."""
        if self.original_mesh is None:
            return
        shown = self.decimated_mesh if self.decimated_mesh is not None else self.original_mesh
        cam = self.plotter.camera_position
        self.show_mesh(shown, is_original=self.decimated_mesh is None, preserve_camera=cam)
        self._refresh_protection_overlay()

    def _on_live_preview_toggled(self, checked: bool):
        """Al activar la vista previa la calcula al momento; al
        desactivarla, vuelve a mostrar el estado real (el resultado ya
        aplicado, o la malla original si no se ha aplicado ninguno)."""
        if checked:
            self._update_live_preview()
        elif self.original_mesh is not None:
            shown = self.decimated_mesh if self.decimated_mesh is not None else self.original_mesh
            cam = self.plotter.camera_position
            self.show_mesh(shown, is_original=self.decimated_mesh is None, preserve_camera=cam)
            self._refresh_protection_overlay()
            self.update_stats(shown)

    def _schedule_preview(self, *_args):
        """Programa un refresco de la vista previa en tiempo real (con un
        pequeno retardo para no recalcular en cada paso mientras se
        arrastra un slider), si esta activa. Ignora el argumento que le
        pasen las senales de Qt (valor del slider, indice del combo...),
        ya que `_update_live_preview` vuelve a leer el estado actual de
        los controles."""
        if not self.live_preview_cb.isChecked() or self.original_mesh is None:
            return
        self._preview_timer.start()

    def _update_live_preview(self):
        """Calcula y muestra una decimacion rapida (sin el refinamiento
        iterativo por error, para que sea practicamente instantanea) con
        los parametros actuales del panel, sin tocar el historial de
        deshacer ni el resultado ya 'aplicado' (`self.decimated_mesh`
        no se modifica: hace falta pulsar 'Aplicar decimacion' para
        confirmar y poder exportar o comparar este resultado)."""
        if not self.live_preview_cb.isChecked() or self.original_mesh is None:
            return
        source = self.original_mesh
        reduction = self.reduction_slider.value() / 100.0
        mask = (
            self.protected_mask.copy()
            if self.protected_mask is not None
            else np.zeros(source.n_points, dtype=bool)
        )
        if self.protect_uv_seams_cb.isChecked() and mesh_has_texture_coords(source):
            mask |= self._uv_seam_mask_for(source)
        try:
            preview = self._decimate_with_mask(source, mask, reduction)
        except Exception:
            return
        if preview.n_cells == 0:
            return
        cam = self.plotter.camera_position
        self.show_mesh(preview, is_original=False, preserve_camera=cam)
        self._refresh_protection_overlay()
        self.stats_label.setText(
            f"Vista previa: {preview.n_cells} tris "
            "(sin aplicar; sin auto-ajuste por error)"
        )

    def update_stats(self, mesh: pv.PolyData):
        """Actualiza la etiqueta de estadisticas con el recuento de vertices/triangulos de `mesh`."""
        self.stats_label.setText(
            f"Vertices: {mesh.n_points}\nTriangulos: {mesh.n_cells}"
        )

    # --- decimacion ---

    def _on_engine_changed(self, index: int):
        """Habilita los controles propios de cada motor (angulo/topologia/
        splitting para vtkDecimatePro, u opciones QEM) segun el elegido."""
        is_qem = index == ENGINE_QEM
        self.angle_slider.setEnabled(not is_qem)
        self.preserve_topology_cb.setEnabled(not is_qem)
        self.splitting_cb.setEnabled(not is_qem)
        self.qem_group.setEnabled(is_qem)

    def _decimate(self, mesh: pv.PolyData, reduction: float, lock_boundary: bool) -> pv.PolyData:
        """Decima `mesh` con el motor elegido en el panel (vtkDecimatePro o
        Quadric Error Metric). `lock_boundary=True` fuerza que los bordes de
        `mesh` quedan intactos (se usa para la costura interna creada al
        separar una zona protegida); si es False, se respeta la casilla
        "Permitir simplificar bordes" para los bordes reales de la malla."""
        resolved_lock = lock_boundary or not self.boundary_vertex_deletion_cb.isChecked()
        if self.engine_combo.currentIndex() == ENGINE_DECIMATE_PRO:
            return decimate_pro(
                mesh,
                reduction,
                feature_angle=float(self.angle_slider.value()),
                preserve_topology=self.preserve_topology_cb.isChecked(),
                boundary_vertex_deletion=not resolved_lock,
                splitting=self.splitting_cb.isChecked(),
            )
        return decimate_qem(
            mesh,
            reduction,
            volume_preservation=self.volume_preservation_cb.isChecked(),
            use_uv_weight=self.uv_attribute_metric_cb.isChecked(),
            tcoords_weight=self.uv_weight_spin.value(),
            boundary_locked=resolved_lock,
            use_normals_weight=self.normals_weight_cb.isChecked(),
            normals_weight=self.normals_weight_spin.value(),
        )

    def _decimate_with_mask(self, source: pv.PolyData, mask: np.ndarray, reduction: float) -> pv.PolyData:
        """Decima `source` dejando intactos los puntos marcados en `mask`
        (protegidos): los separa, decima solo el resto y recompone el
        resultado. Si `mask` esta vacia, decima la malla entera."""
        if not mask.any():
            return self._decimate(source, reduction, lock_boundary=False)

        protected_ids = np.where(mask)[0]
        unprotected_ids = np.where(~mask)[0]
        protected_group = source.extract_points(
            protected_ids, adjacent_cells=True
        ).extract_surface(algorithm="dataset_surface")
        if unprotected_ids.size == 0:
            return protected_group

        unprotected_group = source.extract_points(
            unprotected_ids, adjacent_cells=False
        ).extract_surface(algorithm="dataset_surface")
        decimated_part = self._decimate(unprotected_group, reduction, lock_boundary=True)
        return protected_group.merge(decimated_part, merge_points=True)

    def _measure_error(self, result: pv.PolyData, distance_fn: vtk.vtkImplicitPolyDataDistance):
        """Distancia absoluta de cada centro de cara de `result` a la
        superficie implicita `distance_fn` (aproxima el error de Hausdorff
        unidireccional). Se mide en centros de cara, no en vertices: los
        vertices que sobreviven a vtkDecimatePro conservan su posicion
        original exacta, asi que su distancia a la superficie original
        siempre seria ~0 y ocultaria el error real, que ocurre en el
        interior de los triangulos grandes recien creados. `distance_fn` se
        recibe ya construido (con SetInput aplicado) para no reconstruir el
        localizador espacial en cada pasada de refinamiento. Devuelve
        (errores, puntos_de_muestra) para poder localizar las zonas malas."""
        centroids = result.cell_centers().points
        dists = vtk.vtkDoubleArray()
        distance_fn.FunctionValue(pv.convert_array(centroids), dists)
        errors = np.abs(pv.convert_array(dists))
        return errors, centroids

    def _refine_by_error(
        self,
        source: pv.PolyData,
        result: pv.PolyData,
        mask: np.ndarray,
        reduction: float,
    ):
        """Mide el error geometrico de `result` frente a `source` y, donde
        supera el umbral configurado, protege esos vertices en la malla
        original y vuelve a decimar; repite hasta 3 pasadas o hasta que el
        error quede dentro del umbral. Devuelve (resultado, mascara
        actualizada, error maximo final, error medio final, error percentil
        95 final, nº de pasadas, puntos anadidos)."""
        threshold = (self.error_threshold_spin.value() / 100.0) * source.length
        source_tree = cKDTree(source.points)
        # Se construye una sola vez y se reutiliza en las hasta 3 pasadas:
        # `source` no cambia entre iteraciones, solo `result`, asi que
        # reconstruir el localizador espacial en cada pasada seria trabajo
        # repetido innecesario.
        distance_fn = vtk.vtkImplicitPolyDataDistance()
        distance_fn.SetInput(source)
        added_total = 0
        errors, centroids = self._measure_error(result, distance_fn)
        iterations = 0
        for iterations in range(1, 4):
            bad = errors > threshold
            if not bad.any():
                break
            _, nearest_idx = source_tree.query(centroids[bad])
            nearest_idx = np.unique(nearest_idx)
            newly = nearest_idx[~mask[nearest_idx]]
            if newly.size == 0:
                break
            mask[newly] = True
            added_total += int(newly.size)
            result = self._decimate_with_mask(source, mask, reduction)
            errors, centroids = self._measure_error(result, distance_fn)
        max_error, mean_error, p95_error = self._error_summary(errors)
        return result, mask, max_error, mean_error, p95_error, iterations, added_total

    @staticmethod
    def _error_summary(errors: np.ndarray):
        """Reduce un array de errores por centro de cara a tres numeros que
        resumen la calidad del resultado: el maximo (peor caso puntual, lo
        unico que se mostraba antes), la media (calidad global) y el
        percentil 95 (que tan mal esta la franja de peores puntos sin que
        un unico pico aislado lo distorsione, mas informativo que el
        maximo en mallas grandes)."""
        if errors.size == 0:
            return 0.0, 0.0, 0.0
        return float(errors.max()), float(errors.mean()), float(np.percentile(errors, 95))

    def apply_decimation(self):
        """Decima la malla actual segun los parametros del panel: excluye
        del colapso las zonas pintadas y las costuras UV, y si la
        validacion por error esta activa, refina el resultado protegiendo
        automaticamente donde la deformacion supera el umbral."""
        if self.original_mesh is None:
            return

        source = self.original_mesh
        reduction = self.reduction_slider.value() / 100.0

        mask = (
            self.protected_mask.copy()
            if self.protected_mask is not None
            else np.zeros(source.n_points, dtype=bool)
        )
        manual_count = int(mask.sum())

        max_error = mean_error = p95_error = None
        iterations = 0
        added = 0
        try:
            with busy_cursor():
                uv_locked = 0
                if self.protect_uv_seams_cb.isChecked() and mesh_has_texture_coords(source):
                    seam_mask = self._uv_seam_mask_for(source)
                    uv_locked = int((seam_mask & ~mask).sum())
                    mask |= seam_mask

                result = self._decimate_with_mask(source, mask, reduction)

                error_note = ""
                if result.n_cells > 0:
                    if self.auto_error_cb.isChecked():
                        result, mask, max_error, mean_error, p95_error, iterations, added = (
                            self._refine_by_error(source, result, mask, reduction)
                        )
                        error_note = (
                            f"\nError geometrico (post-ajuste): max {max_error:.4f}, "
                            f"medio {mean_error:.4f}, p95 {p95_error:.4f} "
                            f"({iterations} pasada(s), +{added} pts auto-protegidos)"
                        )
                    else:
                        # Aunque no se auto-proteja por error, medirlo una
                        # vez (sin refinamiento iterativo) sigue siendo
                        # util como diagnostico de calidad del resultado.
                        distance_fn = vtk.vtkImplicitPolyDataDistance()
                        distance_fn.SetInput(source)
                        errors, _ = self._measure_error(result, distance_fn)
                        max_error, mean_error, p95_error = self._error_summary(errors)
                        error_note = (
                            f"\nError geometrico: max {max_error:.4f}, "
                            f"medio {mean_error:.4f}, p95 {p95_error:.4f}"
                        )
        except Exception as exc:
            QMessageBox.critical(self, "Error al decimar", str(exc))
            return

        if result.n_cells == 0:
            QMessageBox.warning(
                self,
                "Resultado vacio",
                "La decimacion ha dejado la malla sin caras. Prueba con una "
                "agresividad menor o menos zonas protegidas.",
            )
            return

        self._push_undo()
        self.decimated_mesh = result
        self.show_mesh(result, is_original=False)
        # Si el pincel ya estaba activo antes de decimar, desactivarlo: a
        # partir de ahora pintaria contra el resultado decimado mientras
        # compara posiciones con el KDTree de la malla original, lo que con
        # el motor QEM (mueve vertices) marcaria puntos en el sitio
        # equivocado sin avisar. Reactivarlo restaura antes el original
        # (ver _on_brush_toggled).
        self.paint_btn.setChecked(False)
        self.erase_btn.setChecked(False)

        original_tris = self.original_mesh.n_cells
        new_tris = result.n_cells
        pct = 100.0 * (1 - new_tris / original_tris) if original_tris else 0
        notes = []
        if manual_count:
            notes.append("zonas pintadas excluidas")
        if uv_locked:
            notes.append(f"{uv_locked} pts de costura UV protegidos")
        note = f" ({', '.join(notes)})" if notes else ""
        self.stats_label.setText(
            f"Original: {original_tris} tris\n"
            f"Resultado: {new_tris} tris\n"
            f"Reduccion real: {pct:.1f}%{note}{error_note}"
        )

        self._last_run_info = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "engine": self.engine_combo.currentText(),
            "original_tris": original_tris,
            "result_tris": new_tris,
            "reduction_target_pct": self.reduction_slider.value(),
            "reduction_real_pct": round(pct, 2),
            "manual_protected_points": manual_count,
            "uv_seam_protected_points": uv_locked,
            "auto_error_protection_enabled": self.auto_error_cb.isChecked(),
        }
        if max_error is not None:
            self._last_run_info.update({
                "error_max": round(max_error, 6),
                "error_mean": round(mean_error, 6),
                "error_p95": round(p95_error, 6),
            })
            if self.auto_error_cb.isChecked():
                self._last_run_info.update({
                    "error_threshold_pct": self.error_threshold_spin.value(),
                    "error_refine_iterations": iterations,
                    "error_refine_added_points": added,
                })

    def reset_mesh(self):
        """Descarta el resultado decimado y vuelve a mostrar la malla original con sus marcas de proteccion."""
        if self.original_mesh is None:
            return
        self._push_undo()
        self.decimated_mesh = None
        self.show_mesh(self.original_mesh, is_original=True)
        self.update_stats(self.original_mesh)
        self._refresh_protection_overlay()

    def open_comparison(self):
        """Abre el dialogo de comparacion lado a lado; requiere haber decimado ya."""
        if self.original_mesh is None or self.decimated_mesh is None:
            QMessageBox.information(
                self,
                "Comparar",
                "Primero aplica una decimacion para poder comparar original y resultado.",
            )
            return
        dialog = ComparisonDialog(self.original_mesh, self.decimated_mesh, self)
        dialog.exec()

    def export_mesh(self):
        """Guarda en disco la malla decimada (o la original si no se ha
        decimado) en el formato elegido. Si esta marcado y hay una
        decimacion previa, escribe ademas un informe .json con las
        estadisticas de esa decimacion junto al archivo de malla."""
        mesh = self.decimated_mesh if self.decimated_mesh is not None else self.original_mesh
        if mesh is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Exportar malla", "", "OBJ (*.obj);;PLY (*.ply);;STL (*.stl)"
        )
        if not path:
            return
        try:
            mesh.save(path)
            if self.export_report_cb.isChecked() and self._last_run_info is not None:
                report_path = f"{os.path.splitext(path)[0]}_report.json"
                with open(report_path, "w", encoding="utf-8") as f:
                    json.dump(self._last_run_info, f, indent=2, ensure_ascii=False)
        except Exception as exc:
            QMessageBox.critical(self, "Error al exportar", str(exc))

    # --- procesamiento por lotes ---

    def _process_one_batch_file(self, path: str, reduction: float) -> dict:
        """Carga, limpia (si toca) y decima un unico archivo con los
        ajustes actuales del panel, igual que `apply_decimation` pero sin
        proteccion manual (no hay malla abierta en el visor de la que
        leer marcas de pincel/rectangulo): solo costuras UV y auto-ajuste
        por error, si estan activados. Devuelve un diccionario con la
        malla resultante y las estadisticas, listo para guardar. Lanza
        una excepcion si el archivo no es una malla de superficie valida
        o si el resultado se queda sin caras."""
        mesh = pv.read(path).extract_surface().triangulate()
        if mesh.n_points == 0 or mesh.n_cells == 0:
            raise ValueError("El archivo no contiene una malla de superficie valida.")
        ensure_active_texture_coords(mesh)
        if self.auto_clean_cb.isChecked():
            mesh = clean_mesh(
                mesh,
                self.min_fragment_slider.value(),
                self.close_holes_cb.isChecked(),
                self.max_hole_size_spin.value(),
                self.fix_orientation_cb.isChecked(),
            )

        mask = np.zeros(mesh.n_points, dtype=bool)
        uv_locked = 0
        if self.protect_uv_seams_cb.isChecked() and mesh_has_texture_coords(mesh):
            seam_mask = uv_seam_point_mask(mesh)
            uv_locked = int(seam_mask.sum())
            mask |= seam_mask

        result = self._decimate_with_mask(mesh, mask, reduction)

        info = {
            "source_file": os.path.basename(path),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "engine": self.engine_combo.currentText(),
            "original_tris": mesh.n_cells,
            "reduction_target_pct": self.reduction_slider.value(),
            "uv_seam_protected_points": uv_locked,
            "auto_error_protection_enabled": self.auto_error_cb.isChecked(),
        }
        if self.auto_error_cb.isChecked() and result.n_cells > 0:
            result, mask, max_error, mean_error, p95_error, iterations, added = (
                self._refine_by_error(mesh, result, mask, reduction)
            )
            info.update({
                "error_threshold_pct": self.error_threshold_spin.value(),
                "error_max": round(max_error, 6),
                "error_mean": round(mean_error, 6),
                "error_p95": round(p95_error, 6),
                "error_refine_iterations": iterations,
                "error_refine_added_points": added,
            })

        if result.n_cells == 0:
            raise ValueError("La decimacion ha dejado la malla sin caras.")

        info["result_tris"] = result.n_cells
        info["reduction_real_pct"] = round(
            100.0 * (1 - result.n_cells / mesh.n_cells) if mesh.n_cells else 0, 2
        )
        return {"mesh": result, "info": info}

    def run_batch(self):
        """Aplica los ajustes actuales del panel a todos los archivos de
        malla compatibles de una carpeta, sin pasar por el visor: por cada
        archivo, limpia (si toca), decima con el motor y la proteccion
        automatica configurados y guarda el resultado (mas un informe
        .json, si esta marcado) en la carpeta de salida. Si un archivo
        falla, se registra el error y se sigue con el resto; al terminar
        se muestra un resumen de aciertos y fallos."""
        input_dir = QFileDialog.getExistingDirectory(self, "Carpeta de entrada (mallas a procesar)")
        if not input_dir:
            return

        files = sorted(
            f for f in os.listdir(input_dir)
            if os.path.splitext(f)[1].lower() in BATCH_INPUT_EXTENSIONS
        )
        if not files:
            QMessageBox.information(
                self,
                "Procesar por lotes",
                "No se ha encontrado ninguna malla compatible "
                "(OBJ/STL/PLY/VTP/GLB/GLTF) en esa carpeta.",
            )
            return

        output_dir = QFileDialog.getExistingDirectory(self, "Carpeta de salida (resultados)")
        if not output_dir:
            return

        ext, ok = QInputDialog.getItem(
            self, "Formato de salida", "Guardar los resultados como:",
            ["obj", "ply", "stl"], 0, False,
        )
        if not ok:
            return

        warning = ""
        if os.path.normcase(os.path.abspath(input_dir)) == os.path.normcase(os.path.abspath(output_dir)):
            warning = (
                "\n\nOjo: la carpeta de salida es la misma que la de "
                "entrada; un archivo con el mismo nombre y extension se "
                "sobrescribiria."
            )
        reply = QMessageBox.question(
            self,
            "Procesar por lotes",
            f"Se van a procesar {len(files)} archivo(s) de \"{input_dir}\" "
            f"con el motor {self.engine_combo.currentText()} al "
            f"{self.reduction_slider.value()}% de agresividad, y guardar "
            f"el resultado en \"{output_dir}\" como .{ext}. No se aplica "
            "proteccion manual (pincel/rectangulo), solo la automatica "
            f"configurada arriba.{warning}\n\n¿Continuar?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        reduction = self.reduction_slider.value() / 100.0
        progress = QProgressDialog("Procesando...", "Cancelar", 0, len(files), self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)

        ok_count = 0
        failures = []
        for i, filename in enumerate(files):
            if progress.wasCanceled():
                break
            progress.setLabelText(f"Procesando {filename} ({i + 1}/{len(files)})")
            progress.setValue(i)
            src_path = os.path.join(input_dir, filename)
            try:
                processed = self._process_one_batch_file(src_path, reduction)
                out_name = os.path.splitext(filename)[0] + f".{ext}"
                processed["mesh"].save(os.path.join(output_dir, out_name))
                if self.export_report_cb.isChecked():
                    report_path = os.path.join(
                        output_dir, os.path.splitext(filename)[0] + "_report.json"
                    )
                    with open(report_path, "w", encoding="utf-8") as f:
                        json.dump(processed["info"], f, indent=2, ensure_ascii=False)
                ok_count += 1
            except Exception as exc:
                failures.append(f"{filename}: {exc}")
            QApplication.processEvents()

        progress.setValue(len(files))

        summary = f"Procesados correctamente: {ok_count}/{len(files)}."
        if failures:
            shown = failures[:20]
            summary += "\n\nFallos:\n" + "\n".join(shown)
            if len(failures) > len(shown):
                summary += f"\n... y {len(failures) - len(shown)} mas."
        QMessageBox.information(self, "Procesamiento por lotes terminado", summary)

    # --- presets de configuracion ---

    def _gather_preset(self) -> dict:
        """Recoge en un diccionario serializable todos los parametros
        configurables del panel (limpieza, visualizacion, proteccion
        automatica, decimacion y validacion por error), para guardarlos
        como preset. No incluye la malla ni las marcas de proteccion
        manual, que son especificas de cada modelo."""
        return {
            "auto_clean": self.auto_clean_cb.isChecked(),
            "min_fragment_pct": self.min_fragment_slider.value(),
            "close_holes": self.close_holes_cb.isChecked(),
            "max_hole_size": self.max_hole_size_spin.value(),
            "fix_orientation": self.fix_orientation_cb.isChecked(),
            "curvature_view": self.curvature_cb.isChecked(),
            "live_preview": self.live_preview_cb.isChecked(),
            "brush_radius_pct": self.brush_slider.value(),
            "protect_uv_seams": self.protect_uv_seams_cb.isChecked(),
            "engine_index": self.engine_combo.currentIndex(),
            "reduction_pct": self.reduction_slider.value(),
            "feature_angle": self.angle_slider.value(),
            "preserve_topology": self.preserve_topology_cb.isChecked(),
            "boundary_vertex_deletion": self.boundary_vertex_deletion_cb.isChecked(),
            "splitting": self.splitting_cb.isChecked(),
            "volume_preservation": self.volume_preservation_cb.isChecked(),
            "uv_attribute_metric": self.uv_attribute_metric_cb.isChecked(),
            "uv_weight": self.uv_weight_spin.value(),
            "normals_weight_enabled": self.normals_weight_cb.isChecked(),
            "normals_weight": self.normals_weight_spin.value(),
            "auto_error_protection": self.auto_error_cb.isChecked(),
            "error_threshold_pct": self.error_threshold_spin.value(),
            "export_report": self.export_report_cb.isChecked(),
        }

    def _apply_preset(self, data: dict):
        """Aplica al panel los valores de un preset cargado; cualquier
        clave ausente conserva el valor actual del control correspondiente,
        para seguir siendo compatible con presets guardados por una
        version anterior de la app que no tuviera todavia algun campo."""
        def get(key, current):
            return data[key] if key in data else current

        self.auto_clean_cb.setChecked(get("auto_clean", self.auto_clean_cb.isChecked()))
        self.min_fragment_slider.setValue(get("min_fragment_pct", self.min_fragment_slider.value()))
        self.close_holes_cb.setChecked(get("close_holes", self.close_holes_cb.isChecked()))
        self.max_hole_size_spin.setValue(get("max_hole_size", self.max_hole_size_spin.value()))
        self.fix_orientation_cb.setChecked(get("fix_orientation", self.fix_orientation_cb.isChecked()))
        self.curvature_cb.setChecked(get("curvature_view", self.curvature_cb.isChecked()))
        self.live_preview_cb.setChecked(get("live_preview", self.live_preview_cb.isChecked()))
        self.brush_slider.setValue(get("brush_radius_pct", self.brush_slider.value()))
        self.protect_uv_seams_cb.setChecked(get("protect_uv_seams", self.protect_uv_seams_cb.isChecked()))
        self.engine_combo.setCurrentIndex(get("engine_index", self.engine_combo.currentIndex()))
        self.reduction_slider.setValue(get("reduction_pct", self.reduction_slider.value()))
        self.angle_slider.setValue(get("feature_angle", self.angle_slider.value()))
        self.preserve_topology_cb.setChecked(get("preserve_topology", self.preserve_topology_cb.isChecked()))
        self.boundary_vertex_deletion_cb.setChecked(
            get("boundary_vertex_deletion", self.boundary_vertex_deletion_cb.isChecked())
        )
        self.splitting_cb.setChecked(get("splitting", self.splitting_cb.isChecked()))
        self.volume_preservation_cb.setChecked(
            get("volume_preservation", self.volume_preservation_cb.isChecked())
        )
        self.uv_attribute_metric_cb.setChecked(
            get("uv_attribute_metric", self.uv_attribute_metric_cb.isChecked())
        )
        self.uv_weight_spin.setValue(get("uv_weight", self.uv_weight_spin.value()))
        self.normals_weight_cb.setChecked(get("normals_weight_enabled", self.normals_weight_cb.isChecked()))
        self.normals_weight_spin.setValue(get("normals_weight", self.normals_weight_spin.value()))
        self.auto_error_cb.setChecked(get("auto_error_protection", self.auto_error_cb.isChecked()))
        self.error_threshold_spin.setValue(get("error_threshold_pct", self.error_threshold_spin.value()))
        self.export_report_cb.setChecked(get("export_report", self.export_report_cb.isChecked()))

    def save_preset(self):
        """Guarda la configuracion actual del panel en un archivo .json reutilizable para otras mallas del mismo tipo."""
        path, _ = QFileDialog.getSaveFileName(self, "Guardar preset", "", "Preset (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._gather_preset(), f, indent=2, ensure_ascii=False)
        except Exception as exc:
            QMessageBox.critical(self, "Error al guardar preset", str(exc))

    def load_preset(self):
        """Carga un preset guardado previamente y aplica sus valores a todos los controles del panel."""
        path, _ = QFileDialog.getOpenFileName(self, "Cargar preset", "", "Preset (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._apply_preset(data)
        except Exception as exc:
            QMessageBox.critical(self, "Error al cargar preset", str(exc))


def main():
    """Punto de entrada: crea la QApplication, aplica el tema y muestra la ventana principal."""
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)
    window = AdaptiveRemesherWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
