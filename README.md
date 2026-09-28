# Adaptive Remesher

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Prototipo de escritorio para **simplificar (decimar) mallas 3D generadas por
IA de forma selectiva**, reduciendo poligonos solo donde no hacen falta y
preservando detalle, texturas y zonas marcadas manualmente como
importantes (caras, logos, dedos...).

## Que hace

- **Dos motores de decimacion** intercambiables:
  - `vtkDecimatePro`: colapsa vertices en zonas planas segun un angulo de
    caracteristica fijo.
  - **Quadric Error Metric (QEM)**: coste de colapso continuo ponderado
    por curvatura, con penalizacion opcional de distorsion UV y de
    normales.
- **Limpieza previa** con PyMeshLab: vertices duplicados/no referenciados,
  aristas y vertices no-manifold, fragmentos flotantes, relleno opcional
  de agujeros y reorientacion de caras (defectos tipicos de mallas
  generadas por IA).
- **Proteccion manual**: pincel de pintar/borrar y seleccion por
  rectangulo sobre el viewport 3D, para marcar zonas que nunca se deben
  simplificar.
- **Proteccion automatica de costuras UV**: detecta y protege los
  vertices duplicados que forman una costura de textura.
- **Validacion por error geometrico**: mide la desviacion de la malla
  decimada frente a la original (aproxima Hausdorff) y auto-protege
  iterativamente las zonas donde el error supera un umbral.
- Undo/redo, presets de configuracion (.json), vista previa en tiempo
  real, comparacion lado a lado original/decimado y **procesamiento por
  lotes** de carpetas enteras.

## Requisitos

- Python 3.11 (las versiones fijadas en `requirements.txt` se han
  comprobado con esta version).
- Windows, macOS o Linux con soporte para Qt (PySide6) y VTK.

## Instalacion

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS / Linux

pip install -r requirements.txt
```

## Uso

```bash
python app.py
```

1. **Cargar malla...**: abre un archivo OBJ, STL, PLY, VTP, GLB o GLTF.
   Si "Limpiar automaticamente al cargar" esta marcado, se limpia antes
   de mostrarla.
2. Opcionalmente, pinta zonas protegidas con **Pintar** / **Rectangulo**
   sobre el viewport.
3. Ajusta el motor de decimacion, la agresividad y las opciones de
   proteccion automatica (costuras UV, validacion por error).
4. **Aplicar decimacion** y, si quieres, **Comparar original/decimado**.
5. **Exportar malla...** guarda el resultado (y, si esta marcado, un
   informe `.json` con las estadisticas de la decimacion).

### Procesamiento por lotes

"Procesar carpeta..." aplica los ajustes actuales del panel a todos los
archivos compatibles de una carpeta de entrada, sin pasar por el visor.
No incluye proteccion manual (pincel/rectangulo): solo la limpieza,
motor de decimacion y proteccion automatica configurados.

### Presets

"Guardar preset..." / "Cargar preset..." guardan y restauran todos los
parametros del panel (sin la malla ni las marcas de proteccion manual)
en un `.json` reutilizable entre sesiones o entre mallas similares.

## Estructura del proyecto

```
app.py               Ventana principal Qt: estado de la malla/proteccion,
                     undo/redo, presets, procesamiento por lotes. La
                     construccion de cada seccion del panel vive en ui/.
remesh_core.py       Logica de dominio pura, independiente de Qt: limpieza,
                     ambos motores de decimacion, proteccion de costuras UV
                     y el pipeline de decimacion+validacion por error
                     compartido entre el flujo interactivo y el batch.
logging_config.py    Logging a consola + archivo rotativo (ver mas abajo).
ui/
  common.py          Estilos, dialogos y widgets reutilizables (tooltip,
                     cursor de espera, panel plegable, comparacion lado a
                     lado).
  panels/            Una funcion build_xxx_panel(window, layout) por cada
                     seccion del panel de control (limpieza, proteccion,
                     decimacion, validacion por error, batch...).
tests/               Tests de remesh_core.py (mallas sinteticas, sin Qt) y
                     de la UI con pytest-qt (botones/sliders reales).
```

## Desarrollo

```bash
pip install -r requirements-dev.txt
pytest              # tests unitarios + de integracion de UI (pytest-qt)
ruff check .        # lint
mypy remesh_core.py app.py logging_config.py ui tests   # tipos
```

`tests/test_remesh_core.py` cubre `remesh_core.py` (limpieza, ambos
motores de decimacion, proteccion por mascara, costuras UV y el pipeline
completo) con mallas sinteticas; no requiere interfaz grafica.
`tests/test_ui_integration.py` instancia la ventana real y pulsa botones/
sliders de verdad con pytest-qt, en el backend Qt "offscreen" (sin
necesitar pantalla, tambien en CI).

Para que ruff/mypy/pytest se ejecuten solos en cada commit:

```bash
pre-commit install
```

## Logging

La consola muestra INFO y superior; `ADAPTIVE_REMESHER_DEBUG=1 python
app.py` sube la consola a DEBUG. Un archivo rotativo junto al script
(`adaptive_remesher.log`, hasta 3 de 1MB) siempre captura DEBUG, para
poder diagnosticar un fallo reportado por alguien sin volver a
reproducirlo (ver `logging_config.py`).

## Build y releases

Ver [BUILD.md](BUILD.md) para generar un ejecutable de escritorio con
PyInstaller (manual, o automatico en Windows/macOS/Linux al empujar un
tag `vX.Y.Z`), y [CHANGELOG.md](CHANGELOG.md) para el historial de
cambios.

## Limitaciones conocidas

- El motor QEM con proteccion puede, en un ~10% de combinaciones de
  resolucion/mascara probadas, dejar 1-2 aristas de costura sin soldar en
  reducciones extremas (>=99%), incluso tras las dos pasadas automaticas
  de reparacion (ver CHANGELOG). Es un caso raro, con agresividad muy por
  encima de lo habitual, y el residuo que puede quedar es minimo.
- Las herramientas de proteccion (pincel, rectangulo) se desactivan
  automaticamente tras decimar y, al reactivarlas, vuelven a mostrar la
  malla original: trabajan contra su KDTree/z-buffer, y con el motor QEM
  (que si mueve vertices) el resultado decimado ya no coincidiria con lo
  mostrado. El resultado decimado no se pierde por esto, solo cambia la
  vista para poder seguir pintando.
- El build de PyInstaller (ver BUILD.md) se ha probado interactivamente
  solo en Windows; el workflow de releases incluye una prueba de humo
  automatica (arranca sin errores) en las tres plataformas.

## Licencia

[MIT](LICENSE) © 2026 CEVI. Libre para usar, modificar y redistribuir
(incluso comercialmente), manteniendo el aviso de copyright.
