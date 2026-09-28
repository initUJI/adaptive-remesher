# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).

## [No publicado]

### Added
- `remesh_core._stitch_residual_boundary_gaps`: ultimo recurso tras
  `_weld_seam_gaps` para la costura QEM en reducciones extremas (ver
  "Fixed" mas abajo). Tras investigar a fondo por que `_weld_seam_gaps`
  no cerraba del todo por encima de ~98% (probados sin exito:
  `meshing_repair_non_manifold_vertices` con varios `vertdispratio`,
  `meshing_snap_mismatched_borders` con varios `edgedistratio`,
  `meshing_remove_t_vertices`, y combinarlos con `vtkFillHolesFilter`),
  el diagnostico exacto resulto ser un vertice "bowtie": un punto donde
  el contorno del hueco se toca a si mismo (4 aristas de borde en vez de
  2), que ningun filtro de reparacion probado (PyMeshLab o VTK) sabe
  resolver porque ninguno separa los dos bucles que se tocan ahi. Se
  implemento un cierre a mano: reconstruye los bucles cerrados simples
  del grafo de aristas de borde restantes (emparejando, en cada vertice
  bowtie, cada arista con la mas opuesta en direccion para separar los
  bucles) y tapa cada uno con un abanico de triangulos desde su
  centroide. Reduce a un residuo de 0-2 aristas (antes 5-56) en un
  barrido de 112 combinaciones de resolucion/mascara/agresividad; sigue
  sin cerrar del todo en un ~10% de los casos mas extremos (>=99% con
  mascaras concretas), documentado como limitacion conocida.
- Numero de pasadas de auto-proteccion por error configurable ("Pasadas
  maximas de auto-proteccion" en el panel, `ProtectionSettings.error_max_iterations`,
  `refine_by_error(max_iterations=...)`): antes fijo a 3.
- Prueba de humo automatica en `.github/workflows/release.yml`: arranca
  cada ejecutable recien construido (Windows/macOS/Linux) con
  `QT_QPA_PLATFORM=offscreen` y falla el build si termina solo antes de
  unos segundos, el sintoma tipico de una dependencia nativa que no se
  empaqueto bien. Antes solo el build de Windows se habia verificado
  arrancando de verdad (ver BUILD.md).
- README con instalacion, uso y estructura del proyecto.
- `remesh_core.py`: logica de dominio (limpieza, ambos motores de
  decimacion, proteccion de costuras UV, pipeline de decimacion +
  validacion por error) extraida de `app.py`, sin dependencia de Qt.
- Suite de tests unitarios (`tests/`) sobre `remesh_core.py` con mallas
  sinteticas de PyVista.
- CI en GitHub Actions (lint, tipos y tests en cada push/PR).
- Chequeo de tipos con mypy (anotaciones de atributos en
  `AdaptiveRemesherWindow`, sin `Optional` innecesarios en
  `PipelineResult`).
- `app.py` dividido en modulos de UI bajo `ui/` (uno por seccion del
  panel de control), reduciendo el `__init__` de la ventana principal de
  ~670 a unas pocas llamadas de orquestacion.
- Empaquetado con PyInstaller para generar un ejecutable de escritorio
  (ver [BUILD.md](BUILD.md)).
- Ganchos de pre-commit (ruff, mypy, pytest) y Dependabot (pip y
  GitHub Actions) para mantener dependencias y calidad al dia.
- Licencia MIT.
- `logging_config.py`: logging a consola (INFO, o DEBUG con
  `ADAPTIVE_REMESHER_DEBUG=1`) y archivo rotativo, sustituyendo al flag
  global `DEBUG`/`print` de antes.
- Tests de integracion de la UI con pytest-qt (`tests/test_ui_integration.py`):
  instancian la ventana real y pulsan botones/sliders, cerrando el hueco
  de cobertura que dejaba `ui/` fuera de los tests automatizados.
- Workflow de releases (`.github/workflows/release.yml`): construye el
  ejecutable en Windows, macOS y Linux con PyInstaller y lo adjunta a una
  GitHub Release al empujar un tag `vX.Y.Z`.

### Changed
- Dependencias fijadas a versiones concretas en `requirements.txt` en
  vez de minimos abiertos (`>=`).
- `apply_decimation` y el procesamiento por lotes comparten ahora el
  mismo pipeline (`run_decimation_pipeline`), eliminando la logica
  duplicada que tenian antes.
- **Validacion por error ahora bidireccional** (`remesh_core.measure_bidirectional_error`):
  antes solo se media la distancia de cada cara del resultado a la
  superficie original, lo que no detecta un detalle saliente del
  original eliminado por completo (ninguna cara del resultado cae cerca
  de la zona ausente, asi que el error medido seguia siendo ~0).
  Confirmado con un caso sintetico: la direccion original reportaba error
  0.0 con un "bulto" entero desaparecido, la nueva direccion inversa
  (original -> resultado) lo detecta correctamente. `refine_by_error`
  ahora protege vertices detectados por cualquiera de las dos
  direcciones, y las estadisticas de error (max/medio/p95) mostradas y
  exportadas combinan ambas.
- Las herramientas de proteccion (pincel, rectangulo) ya no descartan el
  resultado decimado al reactivarse: solo cambian la vista de vuelta a la
  malla original para poder seguir pintando contra ella
  (`_on_brush_toggled`). Antes, tocar cualquiera de esas herramientas tras
  decimar obligaba a repetir la decimacion desde cero aunque el usuario
  solo quisiera revisar o anadir proteccion sin llegar a pintar nada.

### Fixed
- **Seleccion por rectangulo sin test de profundidad.** Marcaba tambien
  puntos ocultos detras de otra parte de la malla. Ahora, tras el filtro
  barato de proyeccion a pantalla, se usa `vtk.vtkSelectVisiblePoints`
  contra el z-buffer ya renderizado para descartar los puntos no
  visibles desde la camara actual (`_apply_rect_selection`).
- **Huecos residuales en la costura QEM a partir de ~95% de reduccion.**
  Tras la subida de peso de borde (ver mas abajo), quedaba un margen de
  fallo por el que la costura no llegaba a soldar del todo en
  reducciones muy agresivas: no eran aristas no-manifold (una reparacion
  estandar no cambiaba nada, comprobado empiricamente) sino un
  T-junction real -distinto numero de segmentos de arista a cada lado
  tras decimar-, que tampoco se resolvia subiendo la tolerancia de
  `merge_points` (con tolerancia alta ademas empeoraba, soldando puntos
  no relacionados). La solucion que si funciono: cerrar esos huecos
  pequenos con `pymeshlab.meshing_close_holes` justo despues del merge
  (`remesh_core._weld_seam_gaps`, usada por `decimate_with_mask`),
  limitando el tamano de agujero a rellenar para no tocar aperturas
  grandes que sean parte real de la malla. Comprobado con un barrido de
  resoluciones/mascaras/agresividad: cierra el hueco por completo hasta
  ~98% de reduccion (antes ~90%); por encima, `_stitch_residual_boundary_gaps`
  (ver "Added") cierra casi todo lo que quede.
- **`_weld_seam_gaps` podia lanzar una excepcion y romper todo el pipeline
  de decimacion.** En un puñado de combinaciones de resolucion/mascara, la
  malla resultante del merge tiene aristas genuinamente no-manifold (3+
  caras), no solo el T-junction habitual, y PyMeshLab rechazaba
  `meshing_close_holes` con `PyMeshLabException` en vez de repararla.
  Ahora se captura esa excepcion y se devuelve la malla sin reparar en
  ese paso, en vez de dejar que la excepcion se propague y cancele toda
  la decimacion.
- **Motor QEM: costura protegida no soldaba a partir de ~70% de
  reduccion.** `vtkQuadricDecimation` solo *penalizaba* el coste de
  tocar el borde compartido con una zona protegida (`SetBoundaryWeightFactor`),
  a diferencia de vtkDecimatePro, que puede bloquearlo del todo; con
  reducciones agresivas acababa colapsando vertices de esa costura,
  dejando decenas de aristas no-manifold tras recomponer el resultado
  (grietas invisibles en el viewport pero reales en la malla exportada).
  Subido el peso de 1000 a 1e10 (`remesh_core.decimate_qem`): la costura
  queda soldada de forma fiable hasta ~90% de reduccion, comprobado con
  un barrido de resoluciones y mascaras de proteccion distintas. Persiste
  un margen de fallo por encima de ese punto (~95-99%): es una limitacion
  del propio `vtkQuadricDecimation`, que no expone un bloqueo topologico
  absoluto como vtkDecimatePro.

## [0.1.0] - 2026-09-21

### Added
- Prototipo inicial: remesher adaptativo con vtkDecimatePro y Quadric
  Error Metric, limpieza con PyMeshLab, proteccion manual (pincel/
  rectangulo), proteccion automatica de costuras UV, validacion por
  error geometrico, undo/redo, presets, vista previa en tiempo real,
  comparacion lado a lado y procesamiento por lotes.
