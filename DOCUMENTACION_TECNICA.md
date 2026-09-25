# Documentacion tecnica — Adaptive Remesher

Este documento complementa al [README.md](README.md) y al [CHANGELOG.md](CHANGELOG.md):
mientras que el README explica *como instalar y usar* la aplicacion, aqui se explica
*que es, por que existe, como esta construida por dentro a nivel de algoritmos* y
*que problemas concretos se han encontrado y corregido* durante su desarrollo.

---

## 1. Que es y para que sirve

**Adaptive Remesher** es un prototipo de escritorio (Windows/macOS/Linux) para
**simplificar mallas 3D generadas por IA de forma selectiva**: reducir el numero de
triangulos donde no aportan detalle visual, preservando geometria, textura y zonas
marcadas como importantes.

### El problema que resuelve

Las mallas generadas por herramientas de IA (texto-a-3D, imagen-a-3D, escaneo
fotogrametrico, etc.) suelen venir con:

- **Densidad de poligonos uniforme y excesiva**: la misma resolucion en una pared
  plana que en una cara o unos dedos, porque el generador no sabe donde "importa"
  el detalle.
- **Defectos topologicos**: vertices duplicados o sin usar, aristas/vertices
  no-manifold (comparten mas de dos caras, o una configuracion imposible en una
  superficie real), fragmentos flotantes desconectados del cuerpo principal,
  normales de cara inconsistentes (mezcla de "hacia dentro" y "hacia fuera").
- **Coste de uso practico alto**: estas mallas son pesadas de renderizar, animar,
  imprimir en 3D o cargar en un motor de videojuego tal cual salen del generador.

Un decimador (simplificador) generico reduciria triangulos de forma uniforme, lo
que arruina precisamente las zonas donde mas importa el detalle (cara, logos,
costuras de textura). Adaptive Remesher deja que el usuario **proteja esas zonas
explicitamente** (a mano, pintando, o automaticamente por criterios geometricos) y
solo decima el resto, ademas de **medir cuanto se ha desviado el resultado del
original** para poder corregir automaticamente si la simplificacion se ha pasado
de agresiva en algun sitio.

### Para quien es

Alguien que recibe mallas de un pipeline de generacion por IA y necesita
prepararlas para un uso final (juego, impresion, visualizacion web) sin perder a
mano el control sobre que se simplifica y que no.

---

## 2. Arquitectura general

El proyecto separa dos capas con responsabilidades muy distintas:

```
remesh_core.py    Logica de dominio pura: ni una linea de Qt. Recibe y devuelve
                  objetos de PyVista/numpy, dataclasses de configuracion y
                  resultado. Testeable con pytest normal, sin QApplication.

app.py + ui/      Interfaz Qt (PySide6): estado interactivo (mesh actual,
                  mascara de proteccion, historial de undo/redo), viewport 3D
                  (PyVista/VTK embebido), y la construccion de cada seccion del
                  panel de control (una funcion build_xxx_panel() por seccion
                  en ui/panels/).
```

Esta separacion no es cosmetica: **toda la logica de decimacion, limpieza y
validacion se puede testear con mallas sinteticas de PyVista sin arrancar ninguna
ventana**, lo que permite un `tests/test_remesh_core.py` rapido y determinista. La
UI se testea aparte con `pytest-qt` (`tests/test_ui_integration.py`), pulsando
botones y sliders reales sobre una ventana real (en modo `offscreen`, tambien en
CI sin pantalla).

`app.py` actua como **orquestador**: guarda el estado (malla cargada, mascara de
proteccion, pila de undo/redo), reacciona a eventos de la UI, y delega *todo* el
trabajo pesado de dominio a funciones de `remesh_core.py`. Por ejemplo,
`AdaptiveRemesherWindow.apply_decimation()` (`app.py:814`) se reduce, en esencia, a
reunir la configuracion actual de los widgets (`_gather_clean_settings`,
`_gather_decimation_settings`, `_gather_protection_settings`) y llamar a
`run_decimation_pipeline(...)`.

El procesamiento por lotes (`run_batch`, `app.py:980`) y el boton interactivo
"Aplicar decimacion" **comparten exactamente el mismo pipeline**
(`run_decimation_pipeline`): antes de la refactorizacion existian dos copias casi
identicas de esta logica (una para el flujo interactivo, otra para el batch), lo
que era una fuente de bugs por divergencia silenciosa entre ambas.

### Flujo de datos de extremo a extremo

```
archivo (OBJ/STL/PLY/VTP/GLB/GLTF)
        │  load_and_prepare_mesh()
        ▼
malla PyVista (triangulada, UV activas si las trae)
        │  clean_mesh()            [opcional: limpieza PyMeshLab]
        ▼
malla limpia
        │  mascara de proteccion = manual (pincel/rectangulo) OR costuras UV
        │  run_decimation_pipeline()
        ├─► decimate_with_mask()   [separa protegido/no-protegido, decima, junta]
        └─► refine_by_error()      [si auto-error activo: mide error, protege
                                     zonas malas, re-decima, hasta 3 pasadas]
        ▼
PipelineResult (malla final + estadisticas)
        │
        ▼
viewport (comparacion lado a lado opcional) / export a disco (+ informe .json)
```

---

## 3. Funcionamiento tecnico interno

### 3.1. Carga y limpieza (`load_and_prepare_mesh`, `clean_mesh`)

La carga usa `pyvista.read()` seguido de `extract_surface()` y `triangulate()`
para normalizar cualquier formato de entrada a una malla de superficie triangular
pura, y activa las coordenadas de textura si el archivo las trae pero el lector no
las marco como "activas" (`ensure_active_texture_coords`: algunos formatos dejan
el UV como un array de punto mas, sin el flag que lo identifica como coordenadas
de textura — sin este paso, la proteccion de costuras UV y el peso de distorsion
UV en QEM se saltarian en silencio).

La limpieza delega en **PyMeshLab** (`clean_mesh`), que corrige defectos tipicos
de mallas de IA:

- vertices duplicados y sin referenciar,
- aristas y vertices no-manifold,
- fragmentos flotantes por debajo de un `min_component_pct`% de la diagonal del
  bounding box,
- opcionalmente, relleno de agujeros pequenos y reorientacion coherente de caras.

### 3.2. Proteccion de zonas

Hay dos fuentes de proteccion, combinables, que se resuelven en una unica
**mascara booleana por vertice**:

- **Manual**: el usuario pinta o selecciona por rectangulo sobre el viewport
  (`app.py`, `_apply_brush`, `_apply_rect_selection`), trabajando contra un
  `cKDTree` de la malla original para encontrar los vertices bajo el pincel.
- **Automatica por costuras UV** (`uv_seam_point_mask`): detecta vertices que
  ocupan la misma posicion 3D pero tienen coordenadas UV distintas — la forma
  habitual en que OBJ/GLTF representan una costura de textura (la superficie es
  continua pero el "desenvuelto" de la textura la corta ahi). Si se colapsan
  estos vertices en la decimacion, la costura se abre visualmente en la textura
  aunque la geometria siga siendo correcta. La deteccion esta vectorizada con
  `scipy.spatial.cKDTree.query_pairs` sobre **todos** los pares de vertices
  cercanos a la vez, en vez de un bucle Python comparando UV par a par: en mallas
  con miles de vertices de costura, ese bucle era el cuello de botella real de
  toda la deteccion automatica.

### 3.3. Los dos motores de decimacion

`decimate_pro` envuelve **`vtkDecimatePro`**: colapsa vertices en zonas planas
segun un **angulo de caracteristica fijo** (`feature_angle`) — por debajo del
angulo se considera "plano" y se puede simplificar, por encima se trata como
borde duro/esquina y se preserva. Es predecible y rapido, pero binario: no hay
gradacion entre "se preserva del todo" y "se colapsa del todo".

`decimate_qem` envuelve **`vtkQuadricDecimation`** (Garland-Heckbert, *Quadric
Error Metric*): el coste de cada colapso de arista es un error cuadratico
continuo que ya captura la curvatura local — una zona casi plana tiene error bajo
y se colapsa primero, una zona muy curva tiene error alto y sobrevive mas tiempo.
Esto da resultados mas suaves que un angulo fijo. Ademas admite penalizar, en el
mismo coste:

- **distorsion de coordenadas UV** (`use_uv_weight`/`tcoords_weight`): desalienta
  colapses que estiren la textura, incluso fuera de las costuras ya protegidas
  aparte;
- **variacion de normal de vertice** (`use_normals_weight`/`normals_weight`): sin
  esto, QEM puede aplanar una zona suavemente curva en facetas visibles a ojo
  porque solo mide error de *posicion*; penalizar el cambio de normal mantiene el
  sombreado mas fiel al original en mallas organicas muy decimadas.

### 3.4. Decimar respetando la proteccion (`decimate_with_mask`)

No existe un parametro de VTK para decir "no toques estos vertices durante la
decimacion". El truco (`decimate_with_mask`) es geometrico:

1. **Separar** la malla en dos piezas independientes con
   `PolyData.extract_points`: la zona protegida (con sus celdas adyacentes) y el
   resto.
2. **Decimar solo la pieza no protegida**, con el borde interno (el que antes
   compartia con la zona protegida) bloqueado (`lock_boundary=True`) para que no
   se mueva.
3. **Recomponer** ambas piezas con `merge(..., merge_points=True)`, que suelda
   por posicion los vertices del borde compartido.

El paso critico es que el borde de la pieza no protegida quede **exactamente**
donde estaba, o el `merge_points` no encuentra un vertice identico con el que
soldar y aparecen grietas (aristas no-manifold) en la costura — este es
precisamente el problema descrito en la seccion 4.1.

### 3.5. Validacion por error geometrico

`measure_error` aproxima la distancia de Hausdorff **en una direccion**: para
cada centro de cara de una malla, su distancia absoluta a una superficie
implicita de referencia (`vtk.vtkImplicitPolyDataDistance`). Se mide en
**centros de cara**, no en vertices, a proposito: los vertices que sobreviven a
`vtkDecimatePro` conservan su posicion original exacta, asi que su distancia al
original seria siempre ~0 y ocultaria el error real, que ocurre en el interior de
los triangulos grandes recien creados por el colapso.

`measure_bidirectional_error` mide **las dos direcciones**: `resultado→original`
y `original→resultado` (ver seccion 4.2 para por que la primera sola no basta).

`refine_by_error` usa esta medicion para cerrar el bucle: si alguna zona supera
el umbral configurado (`error_threshold_pct`, como % de la diagonal del bounding
box), anade esos vertices a la mascara de proteccion y vuelve a decimar, hasta 3
pasadas o hasta quedar dentro de umbral. Las zonas detectadas en la direccion
`resultado→original` se traducen a vertices del original por **vecino mas
cercano** (aproximado, porque los centros de cara de `result` no coinciden con
ningun vertice exacto de `source`); las detectadas en la direccion
`original→resultado` se traducen de forma **exacta**, usando directamente los
vertices de la cara del original que se marco como mala (`source.regular_faces`).

`run_decimation_pipeline` es el punto de entrada unico: monta la mascara
(manual + costuras UV), decima, y si la auto-proteccion por error esta activada
llama a `refine_by_error`; si no, mide el error una sola vez igualmente (sin
refinar) porque sigue siendo util como diagnostico de calidad en el informe
exportable.

### 3.6. Capa de UI (`app.py`, `ui/`)

`AdaptiveRemesherWindow` mantiene el estado interactivo: malla actual, mascara de
proteccion, y una pila de snapshots para **undo/redo** (`_snapshot`/`_push_undo`/
`_restore_snapshot`) que captura malla + mascara antes de cada operacion
destructiva. Cada seccion del panel de control (limpieza, proteccion, motor de
decimacion, validacion por error, presets, batch...) vive en su propio modulo
`ui/panels/*.py`, con una funcion `build_xxx_panel(window, layout)` que crea los
widgets y los cuelga como atributos de `window` — esto redujo el `__init__` de la
ventana principal de unas 670 lineas a una serie de llamadas de orquestacion.

Los presets (`_gather_preset`/`_apply_preset`) serializan todos los parametros de
los paneles (no la malla ni la mascara de proteccion manual) a un `.json`
reutilizable entre sesiones.

### 3.7. Logging

`logging_config.py` configura logging estandar de Python: consola a nivel INFO
(o DEBUG si `ADAPTIVE_REMESHER_DEBUG=1`) y un archivo rotativo junto al script
(`adaptive_remesher.log`, hasta 3 ficheros de 1MB) que siempre captura DEBUG, para
poder diagnosticar un fallo que alguien reporte sin tener que reproducirlo en vivo.

---

## 4. Problemas encontrados y como se han corregido

Estos son los dos problemas **algoritmicos reales** (no de infraestructura)
encontrados mediante pruebas empiricas dirigidas sobre `remesh_core.py`, cada uno
reproducido con un test que falla antes del arreglo y pasa despues.

### 4.1. El motor QEM no soldaba la costura de proteccion a partir de ~70% de reduccion

**Sintoma:** al decimar con el motor QEM y una zona protegida, a reducciones
agresivas aparecian decenas de aristas "no-manifold" en el resultado exportado —
grietas invisibles en el viewport (los vertices seguian casi en su sitio) pero
reales en la malla final.

**Causa raiz:** `vtkDecimatePro` puede **bloquear por completo** el borde de una
region (`SetBoundaryVertexDeletion(False)`), pero `vtkQuadricDecimation` no
expone ese bloqueo absoluto — solo puede **penalizar** el coste de tocar el borde
via `SetBoundaryWeightFactor`. Con el peso anterior (`1000.0`), a partir de ~70%
de reduccion el algoritmo decidia que el ahorro de colapsar un vertice del borde
compensaba la penalizacion, movia ese vertice, y el `merge_points` posterior ya
no encontraba una posicion identica con la que soldar la costura.

Se investigo primero una hipotesis alternativa (que los vertices del borde
*se desplazaban* ligeramente de posicion) midiendo el desplazamiento real: era de
~1e-14 (esencialmente cero), asi que se descarto — el problema no era de
posicion sino puramente **topologico** (distinto numero de segmentos de arista a
cada lado de la costura tras la decimacion).

**Correccion** (`remesh_core.decimate_qem`): subir el peso de `1000.0` a `1e10`.
Verificado con un barrido manual de resoluciones de malla y mascaras de
proteccion distintas: la costura queda soldada de forma fiable hasta ~90% de
reduccion. Persiste un margen de fallo residual a reducciones extremas (~95-99%):
es una limitacion inherente de `vtkQuadricDecimation`, que no tiene forma de
bloquear el borde de manera absoluta como `vtkDecimatePro` — subir aun mas el
peso no ayuda, porque en ese extremo VTK prioriza cumplir el `TargetReduction`
solicitado por encima de respetar el peso del borde.

**Test de regresion:**
[`test_decimate_with_mask_qem_seam_is_watertight`](tests/test_remesh_core.py)
decima una esfera con QEM al 80% de reduccion (nivel confirmado fiable) y
comprueba que `extract_feature_edges(boundary_edges=True,
non_manifold_edges=True, ...)` no encuentra ninguna arista problematica.

### 4.2. La medicion de error solo miraba en una direccion (punto ciego de Hausdorff)

**Sintoma (teorico, confirmado con un caso sintetico):** la validacion por error
podia reportar error ~0 aunque la decimacion hubiera **eliminado por completo**
un detalle sobresaliente del original (un bulto, un pico, un apendice).

**Causa raiz:** `measure_error` solo preguntaba *"¿que tan lejos esta cada centro
de cara del **resultado** de la superficie **original**?"*. Si el resultado ya no
tiene ninguna cara cerca de la zona eliminada, esa pregunta nunca llega a
formularse alli — todas las caras que sobreviven en el resultado ya estaban en el
original, asi que su distancia a el es ~0 independientemente de lo que falte. Es
el mismo motivo por el que la distancia de Hausdorff real se define como el
**maximo de las dos direcciones**, no solo una.

Antes de llegar a esta conclusion se probaron dos reproducciones que no dieron un
resultado claro: una "espina" de un solo vertice con `vtkDecimatePro` (VTK la
preservo correctamente como caracteristica afilada, asi que no habia hueco que
detectar) y un "bulto" multi-vertice con QEM (resultado mixto/no concluyente). El
caso que finalmente demostro el problema de forma inequivoca fue uno construido a
mano: una malla `source` = plano base + un bulto separado fusionado sin soldar
puntos, y `result` = solo el plano base (simulando una decimacion que aplana el
bulto del todo).

**Correccion:**

- `measure_bidirectional_error` (nueva funcion en `remesh_core.py`): mide ambas
  direcciones, `resultado→original` (la de siempre) y `original→resultado` (la
  nueva), reutilizando el localizador espacial del original entre pasadas de
  refinamiento por eficiencia.
- `refine_by_error` reescrita para proteger vertices marcados por **cualquiera**
  de las dos direcciones (aproximado por vecino mas cercano para la direccion
  original, exacto por vertices de cara para la nueva), y las estadisticas de
  error (maximo/medio/percentil 95) que se muestran y se exportan combinan ambas.
- `run_decimation_pipeline`, en su rama de solo-diagnostico (auto-proteccion por
  error desactivada), tambien mide en las dos direcciones.

**Test de regresion:**
[`test_measure_bidirectional_error_detects_fully_removed_feature`](tests/test_remesh_core.py)
reproduce el caso del plano+bulto descrito arriba y comprueba que la direccion
original reporta error maximo `< 1e-6` (no ve nada raro) mientras que la
direccion inversa reporta `> 1.0` (detecta el bulto ausente).

### 4.3. Otros defectos menores encontrados durante el endurecimiento del proyecto

Ademas de los dos anteriores, la introduccion de tests y `mypy` saco a la luz
(y se corrigieron) varios problemas mas pequenos pero reales:

- **`ensure_active_texture_coords` no activaba UV nombradas de forma no estandar**
  en algunos casos de prueba: el test
  `test_ensure_active_texture_coords_activates_named_uv_array` fallaba porque la
  deteccion por nombre de array no cubria el caso probado; se ajusto la logica de
  deteccion y el test paso a ser una regresion real.
- **`run_decimation_pipeline` no lanzaba error de forma fiable** cuando la malla
  de entrada resultaba vacia tras la decimacion en todos los casos esperados
  (`test_run_decimation_pipeline_raises_when_result_is_empty`); se reforzo la
  comprobacion de `result.n_cells == 0` tanto tras la decimacion inicial como
  tras el refinamiento por error.
- **Fijar `python_version` en la config de `mypy`** rompia la comprobacion de
  tipos contra las stubs de `numpy` mas recientes (usan sintaxis PEP 695, no
  disponible si se fuerza una version antigua declarada); se elimino ese pin en
  `pyproject.toml` en vez de mantener una version de Python declarada
  artificialmente antigua.

---

## 5. Limitaciones conocidas (no resueltas, documentadas conscientemente)

- El motor QEM con proteccion aun puede dejar una costura no soldada en
  reducciones extremas (~95-99%); ver 4.1. No hay arreglo disponible dentro de la
  API de `vtkQuadricDecimation`.
- La seleccion por rectangulo en el viewport no hace test de profundidad: tambien
  marca puntos ocultos detras de la malla.
- El pincel de proteccion se desactiva tras decimar, porque trabaja contra el
  `cKDTree` de la malla original y el motor QEM puede mover vertices no
  protegidos.
- El refinamiento iterativo por error (`refine_by_error`) esta acotado a 3
  pasadas: si el umbral configurado es muy exigente, puede terminar sin bajar del
  todo del umbral en vez de iterar indefinidamente (compromiso deliberado para
  evitar bucles largos en mallas grandes).
