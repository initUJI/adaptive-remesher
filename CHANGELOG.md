# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).

## [No publicado]

### Added
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

### Fixed
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
