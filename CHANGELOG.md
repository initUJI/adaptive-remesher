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

### Changed
- Dependencias fijadas a versiones concretas en `requirements.txt` en
  vez de minimos abiertos (`>=`).
- `apply_decimation` y el procesamiento por lotes comparten ahora el
  mismo pipeline (`run_decimation_pipeline`), eliminando la logica
  duplicada que tenian antes.

## [0.1.0] - 2026-09-21

### Added
- Prototipo inicial: remesher adaptativo con vtkDecimatePro y Quadric
  Error Metric, limpieza con PyMeshLab, proteccion manual (pincel/
  rectangulo), proteccion automatica de costuras UV, validacion por
  error geometrico, undo/redo, presets, vista previa en tiempo real,
  comparacion lado a lado y procesamiento por lotes.
