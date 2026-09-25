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
    resultado. Si `mask` esta vacia, decima la malla entera."""
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
    return protected_group.merge(decimated_part, merge_points=True)


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


def refine_by_error(
    source: pv.PolyData,
    result: pv.PolyData,
    mask: np.ndarray,
    settings: DecimationSettings,
    error_threshold_pct: float,
):
    """Mide el error geometrico de `result` frente a `source` y, donde
    supera el umbral configurado, protege esos vertices en la malla
    original y vuelve a decimar; repite hasta 3 pasadas o hasta que el
    error quede dentro del umbral. Devuelve (resultado, mascara
    actualizada, error maximo final, error medio final, error percentil
    95 final, nº de pasadas, puntos anadidos)."""
    threshold = (error_threshold_pct / 100.0) * source.length
    source_tree = cKDTree(source.points)
    # Se construye una sola vez y se reutiliza en las hasta 3 pasadas:
    # `source` no cambia entre iteraciones, solo `result`, asi que
    # reconstruir el localizador espacial en cada pasada seria trabajo
    # repetido innecesario.
    distance_fn = vtk.vtkImplicitPolyDataDistance()
    distance_fn.SetInput(source)
    added_total = 0
    errors, centroids = measure_error(result, distance_fn)
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
        result = decimate_with_mask(source, mask, settings)
        errors, centroids = measure_error(result, distance_fn)
    max_error, mean_error, p95_error = error_summary(errors)
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
            mesh, result, mask, decimation, protection.error_threshold_pct
        )
        if result.n_cells == 0:
            raise ValueError(
                "La decimacion ha dejado la malla sin caras. Prueba con una "
                "agresividad menor o menos zonas protegidas."
            )
    else:
        # Aunque no se auto-proteja por error, medirlo una vez (sin
        # refinamiento iterativo) sigue siendo util como diagnostico de
        # calidad del resultado.
        distance_fn = vtk.vtkImplicitPolyDataDistance()
        distance_fn.SetInput(mesh)
        errors, _ = measure_error(result, distance_fn)
        error_max, error_mean, error_p95 = error_summary(errors)

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
