# Adaptive Remesher

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
app.py            Interfaz Qt (viewport, panel de controles, undo/redo,
                   presets, procesamiento por lotes).
remesh_core.py     Logica de dominio pura, independiente de Qt: limpieza,
                   ambos motores de decimacion, proteccion de costuras UV
                   y el pipeline de decimacion+validacion por error
                   compartido entre el flujo interactivo y el batch.
tests/             Tests de remesh_core.py con pytest (mallas sinteticas,
                   sin necesidad de un QApplication).
```

## Desarrollo

```bash
pip install -r requirements-dev.txt
pytest          # tests unitarios de remesh_core.py
ruff check .    # lint
```

Los tests cubren `remesh_core.py` (limpieza, ambos motores de decimacion,
proteccion por mascara, costuras UV y el pipeline completo) con mallas
sinteticas generadas con PyVista; no requieren interfaz grafica.

## Limitaciones conocidas

- Prototipo de un solo archivo de interfaz; sin empaquetado para
  distribuir un ejecutable de escritorio.
- La seleccion por rectangulo no hace test de profundidad: tambien
  marca puntos ocultos detras de la malla.
- El pincel de proteccion se desactiva automaticamente tras decimar,
  porque trabaja contra el KDTree de la malla original y el motor QEM
  puede mover vertices.
