"""Prototipo de remesher adaptativo: reduce poligonos solo donde no hacen
falta, con dos motores de decimacion intercambiables: vtkDecimatePro
(angulo de caracteristica fijo) y Quadric Error Metric (coste continuo
ponderado por curvatura, con penalizacion opcional de distorsion UV).

Incluye limpieza previa tipica de mallas generadas por IA (fragmentos
sueltos, vertices/aristas no-manifold, relleno opcional de agujeros) via
PyMeshLab; pintado/borrado manual de zonas protegidas; proteccion
automatica de costuras UV; y validacion por error geometrico que
auto-protege zonas donde la decimacion se desvia demasiado del original.

La construccion de la UI esta dividida por seccion en `ui/panels/` (una
funcion `build_xxx_panel(window, panel_layout)` por cada grupo del panel de
control); este modulo conserva el estado y toda la logica de la ventana.
"""

import json
import logging
import os
import sys
from datetime import datetime

import numpy as np
import pyvista as pv
import vtk
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
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
from pyvistaqt import QtInteractor
from scipy.spatial import cKDTree

from remesh_core import (
    ENGINE_QEM,
    CleanSettings,
    DecimationSettings,
    ProtectionSettings,
    clean_mesh,
    decimate_with_mask,
    load_and_prepare_mesh,
    mesh_has_texture_coords,
    run_decimation_pipeline,
    uv_seam_point_mask,
)
from logging_config import configure_logging
from ui.common import STYLESHEET, CollapsibleGroupBox, ComparisonDialog, busy_cursor
from ui.panels import (
    build_action_buttons,
    build_batch_panel,
    build_clean_panel,
    build_decimation_panel,
    build_header,
    build_presets_panel,
    build_protection_panel,
    build_validation_panel,
    build_view_panel,
)

PANEL_STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".panel_layout.json")

BATCH_INPUT_EXTENSIONS = (".obj", ".stl", ".ply", ".vtp", ".glb", ".gltf")

logger = logging.getLogger(__name__)


class AdaptiveRemesherWindow(QMainWindow):
    """Ventana principal: viewport 3D + panel de controles para limpiar,
    proteger manualmente y decimar de forma adaptativa una malla."""

    # Widgets creados y asignados por las funciones build_xxx_panel de
    # ui.panels (llamadas desde __init__): anotados aqui sin asignar, solo
    # para que mypy conozca su tipo -la asignacion real ocurre dentro de
    # cada build_xxx_panel, no aqui.
    undo_btn: QPushButton
    redo_btn: QPushButton
    auto_clean_cb: QCheckBox
    min_fragment_slider: QSlider
    min_fragment_spin: QSpinBox
    close_holes_cb: QCheckBox
    max_hole_size_spin: QSpinBox
    fix_orientation_cb: QCheckBox
    curvature_cb: QCheckBox
    live_preview_cb: QCheckBox
    paint_btn: QPushButton
    erase_btn: QPushButton
    rect_btn: QPushButton
    brush_slider: QSlider
    brush_spin: QSpinBox
    protected_count_label: QLabel
    protect_uv_seams_cb: QCheckBox
    engine_combo: QComboBox
    reduction_slider: QSlider
    reduction_value_label: QLabel
    angle_slider: QSlider
    angle_value_label: QLabel
    preserve_topology_cb: QCheckBox
    boundary_vertex_deletion_cb: QCheckBox
    splitting_cb: QCheckBox
    qem_group: CollapsibleGroupBox
    volume_preservation_cb: QCheckBox
    uv_attribute_metric_cb: QCheckBox
    uv_weight_spin: QDoubleSpinBox
    normals_weight_cb: QCheckBox
    normals_weight_spin: QDoubleSpinBox
    auto_error_cb: QCheckBox
    error_threshold_spin: QDoubleSpinBox
    export_report_cb: QCheckBox

    def __init__(self) -> None:
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
        # pyvista envuelve set_background con un decorador que los stubs de
        # tipos no reflejan bien (ver el mismo patron en ui/common.py).
        self.plotter.set_background("#24262c")  # type: ignore[arg-type]
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
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # Splitter en vez de un ancho fijo: el borde entre el viewport y el
        # panel se puede arrastrar con el raton para hacer el panel mas
        # ancho o mas estrecho (dentro de 260-700px), en vez de quedar
        # bloqueado a 336px.
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(6)
        splitter.addWidget(self.plotter.interactor)
        splitter.addWidget(scroll)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([self.width() - 336, 336])
        layout.addWidget(splitter)

        build_header(self, panel_layout)
        build_presets_panel(self, panel_layout)

        self.stats_label = QLabel("Sin malla cargada")
        self.stats_label.setWordWrap(True)
        panel_layout.addWidget(self.stats_label)

        build_clean_panel(self, panel_layout)
        build_view_panel(self, panel_layout)

        build_protection_panel(self, panel_layout)
        build_decimation_panel(self, panel_layout)
        build_validation_panel(self, panel_layout)
        build_action_buttons(self, panel_layout)
        build_batch_panel(self, panel_layout)

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
                clean = self._gather_clean_settings() if self.auto_clean_cb.isChecked() else None
                mesh = load_and_prepare_mesh(path, clean)
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
                clean = self._gather_clean_settings()
                cleaned = clean_mesh(
                    self.original_mesh,
                    clean.min_component_pct,
                    clean.close_holes,
                    clean.max_hole_size,
                    clean.fix_orientation,
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
                # Invariante garantizado por el "return" de arriba: si
                # llegamos aqui con checked=True, original_mesh no es None.
                assert self.original_mesh is not None
                self.show_mesh(self.original_mesh, is_original=True)
                self._refresh_protection_overlay()
        if checked:
            for other in (self.paint_btn, self.erase_btn, self.rect_btn):
                if other is not button:
                    other.setChecked(False)
        logger.debug("BRUSH %s %s", button.text(), "ON" if checked else "OFF")
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
        # Solo se llama desde eventFilter cuando rect_btn esta activo, lo
        # que _on_brush_toggled ya garantiza que exige una malla cargada.
        assert self.original_mesh is not None
        assert self.protected_mask is not None
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
        `_curvature_for`. Usada por la vista previa en tiempo real, que
        recalcularia esta mascara en cada tick aunque la malla no hubiera
        cambiado desde la ultima vez; en mallas de IA con muchas costuras
        eso se notaba al arrastrar el slider de agresividad con la vista
        previa activa. 'Aplicar decimacion' usa en su lugar el pipeline
        compartido de `remesh_core`, que recalcula la mascara una unica vez
        por click (no es un caso de uso repetitivo que necesite cache)."""
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
            # camera_position, como set_background/reset_camera, es un
            # descriptor de pyvista que los stubs no reflejan bien.
            self.plotter.camera_position = preserve_camera  # type: ignore[call-arg]
        else:
            self.plotter.reset_camera()  # type: ignore[call-arg]
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
        decimation = self._gather_decimation_settings()
        mask = (
            self.protected_mask.copy()
            if self.protected_mask is not None
            else np.zeros(source.n_points, dtype=bool)
        )
        if self.protect_uv_seams_cb.isChecked() and mesh_has_texture_coords(source):
            mask |= self._uv_seam_mask_for(source)
        try:
            preview = decimate_with_mask(source, mask, decimation)
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

    def _gather_clean_settings(self) -> CleanSettings:
        """Recoge los parametros de limpieza previa configurados en el panel."""
        return CleanSettings(
            min_component_pct=self.min_fragment_slider.value(),
            close_holes=self.close_holes_cb.isChecked(),
            max_hole_size=self.max_hole_size_spin.value(),
            fix_orientation=self.fix_orientation_cb.isChecked(),
        )

    def _gather_decimation_settings(self) -> DecimationSettings:
        """Recoge los parametros de decimacion (ambos motores) configurados en el panel."""
        return DecimationSettings(
            engine=self.engine_combo.currentIndex(),
            reduction_pct=self.reduction_slider.value(),
            feature_angle=float(self.angle_slider.value()),
            preserve_topology=self.preserve_topology_cb.isChecked(),
            boundary_vertex_deletion=self.boundary_vertex_deletion_cb.isChecked(),
            splitting=self.splitting_cb.isChecked(),
            volume_preservation=self.volume_preservation_cb.isChecked(),
            use_uv_weight=self.uv_attribute_metric_cb.isChecked(),
            tcoords_weight=self.uv_weight_spin.value(),
            use_normals_weight=self.normals_weight_cb.isChecked(),
            normals_weight=self.normals_weight_spin.value(),
        )

    def _gather_protection_settings(self) -> ProtectionSettings:
        """Recoge los parametros de proteccion automatica (costuras UV y validacion por error) del panel."""
        return ProtectionSettings(
            protect_uv_seams=self.protect_uv_seams_cb.isChecked(),
            auto_error_protection=self.auto_error_cb.isChecked(),
            error_threshold_pct=self.error_threshold_spin.value(),
        )

    def apply_decimation(self):
        """Decima la malla actual segun los parametros del panel: excluye
        del colapso las zonas pintadas y las costuras UV, y si la
        validacion por error esta activa, refina el resultado protegiendo
        automaticamente donde la deformacion supera el umbral. Delega todo
        el calculo en `run_decimation_pipeline` (remesh_core), el mismo
        pipeline que usa el procesamiento por lotes."""
        if self.original_mesh is None:
            return

        decimation = self._gather_decimation_settings()
        protection = self._gather_protection_settings()

        try:
            with busy_cursor():
                result = run_decimation_pipeline(
                    self.original_mesh, decimation, protection, manual_mask=self.protected_mask
                )
        except ValueError as exc:
            QMessageBox.warning(self, "Resultado vacio", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, "Error al decimar", str(exc))
            return

        self._push_undo()
        self.decimated_mesh = result.mesh
        self.show_mesh(result.mesh, is_original=False)
        # Si el pincel ya estaba activo antes de decimar, desactivarlo: a
        # partir de ahora pintaria contra el resultado decimado mientras
        # compara posiciones con el KDTree de la malla original, lo que con
        # el motor QEM (mueve vertices) marcaria puntos en el sitio
        # equivocado sin avisar. Reactivarlo restaura antes el original
        # (ver _on_brush_toggled).
        self.paint_btn.setChecked(False)
        self.erase_btn.setChecked(False)

        notes = []
        if result.manual_protected_points:
            notes.append("zonas pintadas excluidas")
        if result.uv_seam_protected_points:
            notes.append(f"{result.uv_seam_protected_points} pts de costura UV protegidos")
        note = f" ({', '.join(notes)})" if notes else ""
        if protection.auto_error_protection:
            error_note = (
                f"\nError geometrico (post-ajuste): max {result.error_max:.4f}, "
                f"medio {result.error_mean:.4f}, p95 {result.error_p95:.4f} "
                f"({result.error_refine_iterations} pasada(s), "
                f"+{result.error_refine_added_points} pts auto-protegidos)"
            )
        else:
            error_note = (
                f"\nError geometrico: max {result.error_max:.4f}, "
                f"medio {result.error_mean:.4f}, p95 {result.error_p95:.4f}"
            )
        self.stats_label.setText(
            f"Original: {result.original_tris} tris\n"
            f"Resultado: {result.result_tris} tris\n"
            f"Reduccion real: {result.reduction_real_pct:.1f}%{note}{error_note}"
        )

        self._last_run_info = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "engine": self.engine_combo.currentText(),
            "original_tris": result.original_tris,
            "result_tris": result.result_tris,
            "reduction_target_pct": self.reduction_slider.value(),
            "reduction_real_pct": result.reduction_real_pct,
            "manual_protected_points": result.manual_protected_points,
            "uv_seam_protected_points": result.uv_seam_protected_points,
            "auto_error_protection_enabled": protection.auto_error_protection,
            "error_max": round(result.error_max, 6),
            "error_mean": round(result.error_mean, 6),
            "error_p95": round(result.error_p95, 6),
        }
        if protection.auto_error_protection:
            self._last_run_info.update({
                "error_threshold_pct": protection.error_threshold_pct,
                "error_refine_iterations": result.error_refine_iterations,
                "error_refine_added_points": result.error_refine_added_points,
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

    def _process_one_batch_file(self, path: str) -> dict:
        """Carga, limpia (si toca) y decima un unico archivo con los
        ajustes actuales del panel, mediante el mismo `run_decimation_pipeline`
        que usa `apply_decimation`, pero sin proteccion manual (no hay malla
        abierta en el visor de la que leer marcas de pincel/rectangulo):
        solo costuras UV y auto-ajuste por error, si estan activados.
        Devuelve un diccionario con la malla resultante y las estadisticas,
        listo para guardar. Lanza una excepcion si el archivo no es una
        malla de superficie valida o si el resultado se queda sin caras."""
        clean = self._gather_clean_settings() if self.auto_clean_cb.isChecked() else None
        mesh = load_and_prepare_mesh(path, clean)

        decimation = self._gather_decimation_settings()
        protection = self._gather_protection_settings()
        result = run_decimation_pipeline(mesh, decimation, protection, manual_mask=None)

        info = {
            "source_file": os.path.basename(path),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "engine": self.engine_combo.currentText(),
            "original_tris": result.original_tris,
            "result_tris": result.result_tris,
            "reduction_target_pct": self.reduction_slider.value(),
            "reduction_real_pct": result.reduction_real_pct,
            "uv_seam_protected_points": result.uv_seam_protected_points,
            "auto_error_protection_enabled": protection.auto_error_protection,
            "error_max": round(result.error_max, 6),
            "error_mean": round(result.error_mean, 6),
            "error_p95": round(result.error_p95, 6),
        }
        if protection.auto_error_protection:
            info.update({
                "error_threshold_pct": protection.error_threshold_pct,
                "error_refine_iterations": result.error_refine_iterations,
                "error_refine_added_points": result.error_refine_added_points,
            })
        return {"mesh": result.mesh, "info": info}

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
                processed = self._process_one_batch_file(src_path)
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
    """Punto de entrada: configura logging, crea la QApplication, aplica el
    tema y muestra la ventana principal. `ADAPTIVE_REMESHER_DEBUG=1` sube
    el nivel de la consola a DEBUG (el archivo de log siempre lo captura,
    ver logging_config.py)."""
    configure_logging(verbose=bool(os.environ.get("ADAPTIVE_REMESHER_DEBUG")))
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)
    window = AdaptiveRemesherWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
