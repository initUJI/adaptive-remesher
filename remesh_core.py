"""Logica de dominio del remesher adaptativo, independiente de Qt.

Concentra limpieza (PyMeshLab), los dos motores de decimacion (vtkDecimatePro
y Quadric Error Metric), la proteccion automatica de costuras UV y la
validacion/refinamiento por error geometrico. Separado de `app.py` para que
se pueda testear con pytest sin necesidad de un QApplication ni de mockear
Qt, y para que el flujo interactivo (boton "Aplicar decimacion") y el
procesamiento por lotes compartan un unico pipeline en vez de dos copias
casi identicas.
"""

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pymeshlab
import pyvista as pv
import vtk
from scipy.spatial import cKDTree

ENGINE_DECIMATE_PRO = 0
ENGINE_QEM = 1


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
    protegida): a diferencia de vtkDecimatePro (que si puede bloquearlos
    del todo con SetBoundaryVertexDeletion), vtkQuadricDecimation solo
    permite penalizar el coste, nunca prohibir el colapso, asi que si el
    peso es insuficiente el borde puede perder vertices igual, dejando
    aristas no-manifold donde se une con la zona protegida (el merge
    posterior ya no encuentra un vertice identico con el que soldar).
    Comprobado empiricamente barriendo agresividad/resolucion/mascara: con
    el peso anterior (1000) la costura ya se abria a partir de ~70% de
    reduccion; con 1e10 se mantiene perfectamente soldada hasta ~90%, y
    solo se degrada (no a cero, pero mucho menos que antes) por encima de
    eso. Un peso mayor no ayuda: en el limite (~99%) VTK prioriza cumplir
    el target de reduccion sobre respetar el peso del borde, algo que
    vtkQuadricDecimation no permite evitar del todo (no expone, a
    diferencia de vtkDecimatePro, un bloqueo topologico absoluto)."""
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
    decimate.SetBoundaryWeightFactor(1e10 if boundary_locked else 1.0)
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


@dataclass
class CleanSettings:
    """Parametros de limpieza previa (PyMeshLab). Ver `clean_mesh`."""

    min_component_pct: float = 2.0
    close_holes: bool = False
    max_hole_size: int = 30
    fix_orientation: bool = False


@dataclass
class DecimationSettings:
    """Parametros de decimacion, comunes a ambos motores mas los propios de
    cada uno (los que no aplican al motor elegido se ignoran)."""

    engine: int = ENGINE_DECIMATE_PRO
    reduction_pct: float = 50.0
    feature_angle: float = 15.0
    preserve_topology: bool = True
    boundary_vertex_deletion: bool = False
    splitting: bool = True
    volume_preservation: bool = True
    use_uv_weight: bool = True
    tcoords_weight: float = 1.0
    use_normals_weight: bool = False
    normals_weight: float = 1.0

    @property
    def reduction(self) -> float:
        return self.reduction_pct / 100.0


@dataclass
class ProtectionSettings:
    """Parametros de proteccion automatica: costuras UV y validacion por error geometrico."""

    protect_uv_seams: bool = True
    auto_error_protection: bool = True
    error_threshold_pct: float = 1.0
    error_max_iterations: int = 3


@dataclass
class PipelineResult:
    """Resultado de `run_decimation_pipeline`, con la malla decimada y las
    estadisticas necesarias tanto para actualizar la UI como para el
    informe .json exportable."""

    mesh: pv.PolyData
    mask: np.ndarray
    original_tris: int
    result_tris: int
    reduction_real_pct: float
    manual_protected_points: int
    uv_seam_protected_points: int
    # Siempre poblados por run_decimation_pipeline (con o sin refinamiento
    # iterativo, el error se mide al menos una vez), nunca None.
    error_max: float
    error_mean: float
    error_p95: float
    error_refine_iterations: int
    error_refine_added_points: int


def load_and_prepare_mesh(path: str, clean: Optional[CleanSettings]) -> pv.PolyData:
    """Carga una malla de superficie desde `path`, activa sus UV si las
    trae sin marcar, y opcionalmente la limpia con `clean`. Lanza
    ValueError si el archivo no contiene una malla de superficie valida o
    si la limpieza la deja vacia."""
    mesh = pv.read(path).extract_surface(algorithm="dataset_surface").triangulate()
    if mesh.n_points == 0 or mesh.n_cells == 0:
        raise ValueError("El archivo no contiene una malla de superficie valida.")
    ensure_active_texture_coords(mesh)
    if clean is not None:
        mesh = clean_mesh(
            mesh,
            clean.min_component_pct,
            clean.close_holes,
            clean.max_hole_size,
            clean.fix_orientation,
        )
        if mesh.n_points == 0 or mesh.n_cells == 0:
            raise ValueError(
                "La limpieza ha dejado la malla vacia (umbral de fragmento "
                "demasiado alto para este modelo)."
            )
    return mesh


def decimate(mesh: pv.PolyData, settings: DecimationSettings, lock_boundary: bool) -> pv.PolyData:
    """Decima `mesh` con el motor elegido en `settings`. `lock_boundary=True`
    fuerza que los bordes de `mesh` queden intactos (se usa para la costura
    interna creada al separar una zona protegida); si es False, se respeta
    `settings.boundary_vertex_deletion` para los bordes reales de la malla."""
    resolved_lock = lock_boundary or not settings.boundary_vertex_deletion
    if settings.engine == ENGINE_DECIMATE_PRO:
        return decimate_pro(
            mesh,
            settings.reduction,
            feature_angle=settings.feature_angle,
            preserve_topology=settings.preserve_topology,
            boundary_vertex_deletion=not resolved_lock,
            splitting=settings.splitting,
        )
    return decimate_qem(
        mesh,
        settings.reduction,
        volume_preservation=settings.volume_preservation,
        use_uv_weight=settings.use_uv_weight,
        tcoords_weight=settings.tcoords_weight,
        boundary_locked=resolved_lock,
        use_normals_weight=settings.use_normals_weight,
        normals_weight=settings.normals_weight,
    )


def decimate_with_mask(
    source: pv.PolyData, mask: np.ndarray, settings: DecimationSettings
) -> pv.PolyData:
    """Decima `source` dejando intactos los puntos marcados en `mask`
    (protegidos): los separa, decima solo el resto y recompone el
    resultado, con dos pasadas de cierre de huecos residuales en la
    costura (`_weld_seam_gaps`, y `_stitch_residual_boundary_gaps` como
    ultimo recurso sobre lo que aun quede) antes de devolverlo. Si `mask`
    esta vacia, decima la malla entera (no hay costura que reparar)."""
    if not mask.any():
        return decimate(source, settings, lock_boundary=False)

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
    decimated_part = decimate(unprotected_group, settings, lock_boundary=True)
    merged = protected_group.merge(decimated_part, merge_points=True)
    return _stitch_residual_boundary_gaps(_weld_seam_gaps(merged))


def _weld_seam_gaps(mesh: pv.PolyData, max_hole_size: int = 100) -> pv.PolyData:
    """Cierra los huecos residuales que puede dejar la costura entre la
    zona protegida y la decimada en `decimate_with_mask`. No son aristas
    no-manifold (compartidas por mas de 2 caras, lo que corregiria
    `meshing_repair_non_manifold_edges`), sino aristas de borde genuinas a
    cada lado de la costura que no llegan a coincidir: un T-junction real
    (distinto numero de segmentos de arista en cada lado tras decimar),
    no un problema de posicion, asi que ni un `merge_points` con
    tolerancia mayor ni una reparacion de no-manifold estandar lo
    resuelven (comprobado empiricamente: ninguna de las dos cambia nada
    aqui, igual que `meshing_snap_mismatched_borders` y
    `meshing_remove_t_vertices` con distintos parametros). Como el hueco
    es pequeno y esta cerrado por los dos lados, PyMeshLab puede
    rellenarlo igual que rellenaria un agujero genuino; `max_hole_size`
    (en aristas del contorno) se mantiene bajo a proposito para no tocar
    aperturas grandes que sean parte real de la malla (ojos, boca...) en
    vez de un artefacto de la costura. Cierra el hueco por completo hasta
    ~97% de reduccion; por encima (~98-99%) reduce las aristas
    problematicas restantes en un 80-90% pero no siempre a cero, de ahi
    `_stitch_residual_boundary_gaps` como ultimo recurso despues. En el
    puñado de casos donde la malla resultante del merge ya tiene aristas
    genuinamente no-manifold (compartidas por 3+ caras, no solo el
    T-junction habitual), PyMeshLab puede rechazar el filtro por completo
    (`PyMeshLabException`); se captura y se devuelve `mesh` tal cual, sin
    reparar, antes que romper todo el pipeline de decimacion por un
    intento de reparacion fallido."""
    if mesh.n_cells == 0:
        return mesh
    try:
        ms = pymeshlab.MeshSet()
        ms.add_mesh(pymeshlab.Mesh(vertex_matrix=mesh.points, face_matrix=mesh.regular_faces))
        ms.meshing_close_holes(maxholesize=max_hole_size)
        repaired = ms.current_mesh()
        verts = repaired.vertex_matrix()
        faces = repaired.face_matrix()
        poly_faces = np.hstack([np.full((faces.shape[0], 1), 3), faces]).astype(np.int64)
        return pv.PolyData(verts, poly_faces)
    except pymeshlab.PyMeshLabException:
        return mesh


def _pair_seam_branch_neighbors(vertex: int, neighbors: list, points: np.ndarray):
    """En un vertice 'bowtie' del grafo de aristas de borde residuales
    (mas de 2 aristas de borde se tocan en el mismo punto: dos bucles de
    costura distintos que coinciden ahi), empareja cada vecino con el mas
    opuesto en direccion, la pareja que mejor continua la curva en linea
    recta a traves del punto -asi se separan los bucles en vez de
    tratarlos como uno solo en forma de ocho, que ningun triangulador de
    agujeros simple sabe rellenar."""
    direcciones = []
    for n in neighbors:
        d = points[n] - points[vertex]
        norm = np.linalg.norm(d)
        if norm > 1e-12:
            direcciones.append((n, d / norm))
    restantes = list(direcciones)
    pares = []
    while len(restantes) >= 2:
        n0, d0 = restantes.pop(0)
        mejor_j, mejor_cos = None, 2.0
        for j, (_, d1) in enumerate(restantes):
            cos = float(np.dot(d0, d1))
            if cos < mejor_cos:
                mejor_cos, mejor_j = cos, j
        if mejor_j is None:
            break
        n1, _ = restantes.pop(mejor_j)
        pares.append((n0, n1))
    return pares


def _stitch_residual_boundary_gaps(mesh: pv.PolyData) -> pv.PolyData:
    """Ultimo recurso tras `_weld_seam_gaps`: si aun quedan aristas de
    borde sin soldar (no aristas no-manifold genuinas, esas se dejan
    intactas: anadir mas caras encima de una arista ya compartida por 3+
    caras la empeora, no la arregla), las tapa a mano triangulando en
    abanico cada bucle cerrado simple que puede reconstruir a partir de
    ellas. En un vertice donde varios bucles se tocan en el mismo punto
    (ver `_pair_seam_branch_neighbors`), el punto acaba formando parte de
    mas de un abanico: dos parches que solo se tocan en ese vertice, no
    una arista no-manifold (extract_feature_edges no lo marca, aunque
    tecnicamente sea un vertice no-manifold) -aceptable para cerrar del
    todo un hueco que, por lo demas, ningun reparador de PyMeshLab/VTK
    probado sabe cerrar (ver `_weld_seam_gaps`). Si el recorrido del grafo
    no encuentra un bucle cerrado valido (topologia con la que este
    heuristico no sabe lidiar), deja esas aristas como estaban en vez de
    forzar un parche incorrecto."""
    if mesh.n_cells == 0:
        return mesh
    boundary = mesh.extract_feature_edges(
        boundary_edges=True, non_manifold_edges=False, feature_edges=False, manifold_edges=False
    )
    if boundary.n_cells == 0:
        return mesh

    tree = cKDTree(mesh.points)
    dist, mesh_idx = tree.query(boundary.points)
    if dist.max() > 1e-6:
        return mesh  # no deberia pasar; salvaguarda defensiva

    lines = boundary.lines.reshape(-1, 3)[:, 1:]
    adjacency: dict = {}
    for a, b in lines:
        ga, gb = int(mesh_idx[a]), int(mesh_idx[b])
        adjacency.setdefault(ga, []).append(gb)
        adjacency.setdefault(gb, []).append(ga)

    points = mesh.points
    allowed_next: dict = {}
    for v, neighbors in adjacency.items():
        if len(neighbors) == 2 and neighbors[0] != neighbors[1]:
            allowed_next[v] = {neighbors[0]: neighbors[1], neighbors[1]: neighbors[0]}
        else:
            paired = {}
            for a, b in _pair_seam_branch_neighbors(v, neighbors, points):
                paired[a] = b
                paired[b] = a
            allowed_next[v] = paired

    visited_edges = set()
    loops = []
    for v, neighbors in adjacency.items():
        for n in set(neighbors):
            edge = (min(v, n), max(v, n))
            if edge in visited_edges:
                continue
            loop = [v, n]
            visited_edges.add(edge)
            prev, current = v, n
            ok = True
            while current != v:
                nxt = allowed_next.get(current, {}).get(prev)
                if nxt is None:
                    ok = False
                    break
                e2 = (min(current, nxt), max(current, nxt))
                if e2 in visited_edges:
                    ok = nxt == v
                    if ok:
                        visited_edges.add(e2)
                        loop.append(nxt)
                    break
                visited_edges.add(e2)
                loop.append(nxt)
                prev, current = current, nxt
            if ok and loop[0] == loop[-1] and len(loop) >= 4:
                loops.append(loop[:-1])

    if not loops:
        return mesh

    new_points = list(points)
    new_faces = []
    for loop in loops:
        centroid = points[loop].mean(axis=0)
        centroid_idx = len(new_points)
        new_points.append(centroid)
        n = len(loop)
        for i in range(n):
            new_faces.append([3, loop[i], loop[(i + 1) % n], centroid_idx])

    all_points = np.array(new_points)
    all_faces = np.vstack([mesh.faces.reshape(-1, 4), np.array(new_faces, dtype=np.int64)])
    return pv.PolyData(all_points, all_faces)


def measure_error(result: pv.PolyData, distance_fn: "vtk.vtkImplicitPolyDataDistance"):
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


def error_summary(errors: np.ndarray):
    """Reduce un array de errores por centro de cara a tres numeros que
    resumen la calidad del resultado: el maximo (peor caso puntual, lo
    unico que se mostraba antes), la media (calidad global) y el
    percentil 95 (que tan mal esta la franja de peores puntos sin que
    un unico pico aislado lo distorsione, mas informativo que el
    maximo en mallas grandes)."""
    if errors.size == 0:
        return 0.0, 0.0, 0.0
    return float(errors.max()), float(errors.mean()), float(np.percentile(errors, 95))


def measure_bidirectional_error(
    source: pv.PolyData,
    result: pv.PolyData,
    distance_to_source: Optional["vtk.vtkImplicitPolyDataDistance"] = None,
):
    """Mide el error geometrico en las dos direcciones entre `source` y
    `result`, no solo `result` -> `source`: esa unica direccion (la unica
    que se media antes) no detecta que un detalle sobresaliente del
    original haya desaparecido por completo en el resultado, porque solo
    pregunta "¿que tan lejos esta cada centro de cara de RESULT de la
    superficie original?" y si el resultado ya no tiene ninguna cara cerca
    de la zona eliminada, esa pregunta nunca llega a hacerse alli. La
    direccion inversa, `source` -> `result`, pregunta lo contrario: "¿que
    tan lejos esta cada centro de cara ORIGINAL del resultado?", y si esa
    zona se ha aplanado/eliminado, el centro original correspondiente
    seguira estando lejos de la superficie decimada, delatandolo. El
    maximo de Hausdorff real es el mayor de las dos direcciones; aqui se
    devuelven ambas por separado (en vez de ya combinadas) porque cada una
    localiza el problema en una malla distinta -`result` o `source`- y
    `refine_by_error` necesita saber en cual para poder proteger los
    vertices correctos. `distance_to_source` se puede pasar ya construido
    (con SetInput aplicado) para reutilizarlo entre pasadas de
    refinamiento, ya que `source` no cambia entre ellas; `result` si, asi
    que su localizador (`distance_to_result`) se reconstruye siempre.
    Devuelve (errores_result, centros_result, errores_source,
    centros_source)."""
    if distance_to_source is None:
        distance_to_source = vtk.vtkImplicitPolyDataDistance()
        distance_to_source.SetInput(source)
    forward_errors, forward_centroids = measure_error(result, distance_to_source)

    distance_to_result = vtk.vtkImplicitPolyDataDistance()
    distance_to_result.SetInput(result)
    reverse_errors, reverse_centroids = measure_error(source, distance_to_result)

    return forward_errors, forward_centroids, reverse_errors, reverse_centroids


def refine_by_error(
    source: pv.PolyData,
    result: pv.PolyData,
    mask: np.ndarray,
    settings: DecimationSettings,
    error_threshold_pct: float,
    max_iterations: int = 3,
):
    """Mide el error geometrico bidireccional entre `source` y `result`
    (ver `measure_bidirectional_error`) y, donde cualquiera de las dos
    direcciones supera el umbral configurado, protege esos vertices en la
    malla original y vuelve a decimar; repite hasta `max_iterations`
    pasadas o hasta que el error quede dentro del umbral. Las zonas malas
    detectadas en `result` (direccion original) se traducen a vertices de `source` por
    vecino mas cercano (aproximado: los centros de `result` no coinciden
    con ningun vertice de `source`); las detectadas directamente en
    `source` (direccion nueva) se traducen exactas, usando los propios
    vertices de la cara marcada. Devuelve (resultado, mascara
    actualizada, error maximo final, error medio final, error percentil
    95 final, nº de pasadas, puntos anadidos)."""
    threshold = (error_threshold_pct / 100.0) * source.length
    source_tree = cKDTree(source.points)
    source_faces = source.regular_faces
    # Se construye una sola vez y se reutiliza en las hasta max_iterations
    # pasadas: `source` no cambia entre iteraciones, solo `result`, asi
    # que reconstruir su localizador espacial en cada pasada seria trabajo
    # repetido innecesario (el de `result` si hace falta reconstruirlo
    # cada vez, ver measure_bidirectional_error).
    distance_to_source = vtk.vtkImplicitPolyDataDistance()
    distance_to_source.SetInput(source)
    added_total = 0
    fwd_errors, fwd_centroids, rev_errors, _ = measure_bidirectional_error(
        source, result, distance_to_source
    )
    iterations = 0
    for iterations in range(1, max_iterations + 1):
        bad_fwd = fwd_errors > threshold
        bad_rev = rev_errors > threshold
        if not bad_fwd.any() and not bad_rev.any():
            break
        _, nearest_from_fwd = source_tree.query(fwd_centroids[bad_fwd])
        from_rev = np.unique(source_faces[bad_rev].ravel())
        nearest_idx = np.unique(np.concatenate([nearest_from_fwd, from_rev]).astype(np.int64))
        newly = nearest_idx[~mask[nearest_idx]]
        if newly.size == 0:
            break
        mask[newly] = True
        added_total += int(newly.size)
        result = decimate_with_mask(source, mask, settings)
        fwd_errors, fwd_centroids, rev_errors, _ = measure_bidirectional_error(
            source, result, distance_to_source
        )
    max_error, mean_error, p95_error = error_summary(np.concatenate([fwd_errors, rev_errors]))
    return result, mask, max_error, mean_error, p95_error, iterations, added_total


def run_decimation_pipeline(
    mesh: pv.PolyData,
    decimation: DecimationSettings,
    protection: ProtectionSettings,
    manual_mask: Optional[np.ndarray] = None,
) -> PipelineResult:
    """Pipeline completo de decimacion adaptativa sobre una malla ya cargada
    (y ya limpiada, si aplica): aplica la mascara de proteccion (manual +
    costuras UV automaticas), decima, y si `protection.auto_error_protection`
    esta activo, refina iterativamente protegiendo zonas de error alto. Es
    el unico pipeline compartido por el flujo interactivo ("Aplicar
    decimacion") y por el procesamiento por lotes, para no mantener dos
    copias de la misma logica. Lanza ValueError si el resultado se queda
    sin caras."""
    mask = (
        manual_mask.copy()
        if manual_mask is not None
        else np.zeros(mesh.n_points, dtype=bool)
    )
    manual_count = int(mask.sum())

    uv_locked = 0
    if protection.protect_uv_seams and mesh_has_texture_coords(mesh):
        seam_mask = uv_seam_point_mask(mesh)
        uv_locked = int((seam_mask & ~mask).sum())
        mask |= seam_mask

    result = decimate_with_mask(mesh, mask, decimation)
    if result.n_cells == 0:
        raise ValueError(
            "La decimacion ha dejado la malla sin caras. Prueba con una "
            "agresividad menor o menos zonas protegidas."
        )

    iterations = added = 0
    if protection.auto_error_protection:
        result, mask, error_max, error_mean, error_p95, iterations, added = refine_by_error(
            mesh, result, mask, decimation, protection.error_threshold_pct,
            protection.error_max_iterations,
        )
        if result.n_cells == 0:
            raise ValueError(
                "La decimacion ha dejado la malla sin caras. Prueba con una "
                "agresividad menor o menos zonas protegidas."
            )
    else:
        # Aunque no se auto-proteja por error, medirlo una vez (sin
        # refinamiento iterativo) sigue siendo util como diagnostico de
        # calidad del resultado. Bidireccional por la misma razon que en
        # refine_by_error: la direccion original (result->mesh) no
        # detectaria un detalle del original eliminado por completo.
        fwd_errors, _, rev_errors, _ = measure_bidirectional_error(mesh, result)
        error_max, error_mean, error_p95 = error_summary(np.concatenate([fwd_errors, rev_errors]))

    original_tris = mesh.n_cells
    result_tris = result.n_cells
    reduction_real_pct = round(
        100.0 * (1 - result_tris / original_tris) if original_tris else 0, 2
    )

    return PipelineResult(
        mesh=result,
        mask=mask,
        original_tris=original_tris,
        result_tris=result_tris,
        reduction_real_pct=reduction_real_pct,
        manual_protected_points=manual_count,
        uv_seam_protected_points=uv_locked,
        error_max=error_max,
        error_mean=error_mean,
        error_p95=error_p95,
        error_refine_iterations=iterations,
        error_refine_added_points=added,
    )
