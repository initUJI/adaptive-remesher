"""Domain logic for the adaptive remesher, independent of Qt.

Brings together cleanup (PyMeshLab), the two decimation engines
(vtkDecimatePro and Quadric Error Metric), automatic UV seam protection,
and error-based geometric validation/refinement. Kept separate from
`app.py` so it can be tested with pytest without needing a QApplication
or mocking Qt, and so the interactive flow (the "Apply decimation"
button) and batch processing share a single pipeline instead of two
nearly identical copies.
"""

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pymeshlab
import pyvista as pv
import vtk
from scipy.spatial import cKDTree  # type: ignore[attr-defined]

ENGINE_DECIMATE_PRO = 0
ENGINE_QEM = 1


def mesh_has_texture_coords(mesh: pv.PolyData) -> bool:
    """Whether the mesh has active texture coordinates (UV)."""
    return mesh.active_texture_coordinates is not None


def ensure_active_texture_coords(mesh: pv.PolyData) -> None:
    """If the mesh has no active UV but carries a texture array that
    isn't flagged as such, activate it. Some readers (depending on
    format) leave texture coordinates as just another point array
    without activating them, in which case `active_texture_coordinates`
    returns None even though the mesh does have texture: UV seam
    protection and the UV distortion weight in QEM would silently be
    skipped without this fix."""
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
    """Repairs defects typical of AI-generated meshes: duplicated or
    unreferenced vertices, non-manifold edges and vertices, and floating
    fragments smaller than min_component_pct% of the bounding-box
    diagonal. If `close_holes`, it also fills holes of up to
    `max_hole_size` edges (useful after removing fragments, but can
    close intentional gaps such as eyes/mouth if the threshold is too
    high). If `fix_orientation`, it reorients faces coherently with each
    other: fixes inverted shading or apparent "holes" that show up when
    an AI generator leaves mixed face normals (some pointing inward,
    some outward) within the same surface."""
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
    """Detects vertices duplicated at the same 3D position but with
    different UV: the usual way OBJ/GLTF represent a texture seam.
    Protecting them keeps decimation from collapsing them and breaking
    the texture at the seam. Returns a boolean per-point mask.
    Vectorized with numpy over all pairs at once (instead of a Python
    loop with an np.allclose per pair): on AI meshes with many seams
    (thousands of duplicated vertices) the loop was by far the
    bottleneck of the whole automatic protection detection."""
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
    # Same tolerance as np.allclose(a, b, atol=1e-4) (rtol=1e-05 by
    # default), but evaluated all at once over every pair.
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
    """Applies vtkDecimatePro to `mesh`: collapses vertices in flat areas
    according to `feature_angle` and leaves hard edges/corners above that
    angle untouched. Returns the mesh unchanged if it has no cells."""
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
    """Applies vtkQuadricDecimation (Garland-Heckbert): the cost of each
    collapse is a quadric error that already weighs local curvature
    continuously (flat areas -> low error -> collapsed first; curved
    areas -> high error -> preserved), instead of vtkDecimatePro's fixed
    binary angle. If `use_uv_weight` and the mesh has texture, it adds UV
    coordinate distortion to the cost, to discourage collapses that
    stretch the texture even outside the seams already protected
    separately. If `use_normals_weight`, it additionally adds vertex
    normal variation to the cost: without this, QEM can flatten smoothly
    curved areas into visible facets because it only measures positional
    error; penalizing normal change keeps shading closer to the original
    on heavily decimated organic meshes (computes vertex normals if the
    mesh doesn't already carry them). `boundary_locked` heavily penalizes
    moving edges (real ones, or the join with a protected zone): unlike
    vtkDecimatePro (which can fully lock them with
    SetBoundaryVertexDeletion), vtkQuadricDecimation can only penalize
    the cost, never forbid the collapse, so if the weight is
    insufficient the edge can still lose vertices, leaving non-manifold
    edges where it meets the protected zone (the subsequent merge no
    longer finds an identical vertex to weld to). Verified empirically by
    sweeping aggressiveness/resolution/mask: with the previous weight
    (1000) the seam already opened up starting at ~70% reduction; with
    1e10 it stays perfectly welded up to ~90%, and only degrades beyond
    that (not to zero, but far less than before). A higher weight
    doesn't help: in the limit (~99%) VTK prioritizes meeting the
    reduction target over respecting the edge weight, something
    vtkQuadricDecimation doesn't let you avoid entirely (unlike
    vtkDecimatePro, it doesn't expose an absolute topological lock)."""
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
        # The normals were only computed to guide the collapse cost,
        # they weren't original data of `mesh`: if left in the result, a
        # neighboring piece that didn't go through here (e.g. the
        # protected zone, not decimated) wouldn't have them, and the
        # subsequent merge would carry an array that's half full of
        # gaps.
        del output.point_data["Normals"]
    return output


@dataclass
class CleanSettings:
    """Pre-processing cleanup parameters (PyMeshLab). See `clean_mesh`."""

    min_component_pct: float = 2.0
    close_holes: bool = False
    max_hole_size: int = 30
    fix_orientation: bool = False


@dataclass
class DecimationSettings:
    """Decimation parameters, common to both engines plus each engine's
    own (the ones that don't apply to the chosen engine are ignored)."""

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
    """Automatic protection parameters: UV seams and error-based
    geometric validation."""

    protect_uv_seams: bool = True
    auto_error_protection: bool = True
    error_threshold_pct: float = 1.0
    error_max_iterations: int = 3


@dataclass
class PipelineResult:
    """Result of `run_decimation_pipeline`, with the decimated mesh and
    the statistics needed both to update the UI and for the exportable
    .json report."""

    mesh: pv.PolyData
    mask: np.ndarray
    original_tris: int
    result_tris: int
    reduction_real_pct: float
    manual_protected_points: int
    uv_seam_protected_points: int
    # Always populated by run_decimation_pipeline (with or without
    # iterative refinement, the error is measured at least once), never
    # None.
    error_max: float
    error_mean: float
    error_p95: float
    error_refine_iterations: int
    error_refine_added_points: int


def load_and_prepare_mesh(path: str, clean: Optional[CleanSettings]) -> pv.PolyData:
    """Loads a surface mesh from `path`, activates its UV if present but
    unflagged, and optionally cleans it with `clean`. Raises ValueError
    if the file doesn't contain a valid surface mesh or if cleanup
    leaves it empty."""
    mesh = pv.read(path).extract_surface(algorithm="dataset_surface").triangulate()
    if mesh.n_points == 0 or mesh.n_cells == 0:
        raise ValueError("The file does not contain a valid surface mesh.")
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
                "Cleanup left the mesh empty (fragment threshold too "
                "high for this model)."
            )
    return mesh


def decimate(mesh: pv.PolyData, settings: DecimationSettings, lock_boundary: bool) -> pv.PolyData:
    """Decimates `mesh` with the engine chosen in `settings`.
    `lock_boundary=True` forces the edges of `mesh` to remain untouched
    (used for the internal seam created when splitting off a protected
    zone); if False, `settings.boundary_vertex_deletion` is respected for
    the mesh's real boundary edges."""
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
    """Decimates `source` leaving the points marked in `mask` untouched
    (protected): splits them off, decimates only the rest, and
    recomposes the result, with two passes closing residual gaps in the
    seam (`_weld_seam_gaps`, and `_stitch_residual_boundary_gaps` as a
    last resort on whatever is still left) before returning it. If
    `mask` is empty, decimates the whole mesh (there's no seam to
    repair)."""
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
    """Closes the residual gaps that the seam between the protected zone
    and the decimated one can leave in `decimate_with_mask`. These
    aren't non-manifold edges (shared by more than 2 faces, which
    `meshing_repair_non_manifold_edges` would fix), but genuine boundary
    edges on each side of the seam that don't quite line up: a real
    T-junction (a different number of edge segments on each side after
    decimating), not a positional problem, so neither a `merge_points`
    with a larger tolerance nor a standard non-manifold repair fixes it
    (checked empirically: neither changes anything here, same as
    `meshing_snap_mismatched_borders` and `meshing_remove_t_vertices`
    with different parameters). Since the gap is small and closed on
    both sides, PyMeshLab can fill it just as it would fill a genuine
    hole; `max_hole_size` (in boundary edges) is deliberately kept low
    so as not to touch large openings that are a real part of the mesh
    (eyes, mouth...) rather than a seam artifact. Closes the gap
    completely up to ~97% reduction; above that (~98-99%) it reduces the
    remaining problematic edges by 80-90% but not always to zero, hence
    `_stitch_residual_boundary_gaps` as a last resort afterward. In the
    handful of cases where the mesh resulting from the merge already has
    genuinely non-manifold edges (shared by 3+ faces, not just the usual
    T-junction), PyMeshLab can reject the filter entirely
    (`PyMeshLabException`); this is caught and `mesh` is returned as-is,
    unrepaired, rather than breaking the whole decimation pipeline over
    a failed repair attempt."""
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
    """At a 'bowtie' vertex of the residual boundary-edge graph (more
    than 2 boundary edges meet at the same point: two distinct seam
    loops that happen to coincide there), pairs each neighbor with the
    most opposite one in direction, the pairing that best continues the
    curve in a straight line through the point - this way the loops are
    separated instead of being treated as a single figure-eight, which
    no simple hole triangulator knows how to fill."""
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
    """Last resort after `_weld_seam_gaps`: if there are still unwelded
    boundary edges left (not genuine non-manifold edges, those are left
    untouched: adding more faces on top of an edge already shared by 3+
    faces makes it worse, not better), caps them by hand,
    fan-triangulating each simple closed loop it can reconstruct from
    them. At a vertex where several loops meet at the same point (see
    `_pair_seam_branch_neighbors`), the point ends up part of more than
    one fan: two patches that only touch at that vertex, not a
    non-manifold edge (extract_feature_edges doesn't flag it, even
    though it's technically a non-manifold vertex) - acceptable to fully
    close a gap that, otherwise, no PyMeshLab/VTK repairer tested knows
    how to close (see `_weld_seam_gaps`). If the graph traversal doesn't
    find a valid closed loop (a topology this heuristic doesn't know how
    to handle), it leaves those edges as they were instead of forcing an
    incorrect patch."""
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
        return mesh  # shouldn't happen; defensive safeguard

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
    """Absolute distance from each face center of `result` to the
    implicit surface `distance_fn` (approximates one-directional
    Hausdorff error). Measured at face centers, not vertices: vertices
    that survive vtkDecimatePro keep their exact original position, so
    their distance to the original surface would always be ~0 and would
    hide the real error, which occurs inside the large newly created
    triangles. `distance_fn` is received already built (with SetInput
    applied) so as not to rebuild the spatial locator on every
    refinement pass. Returns (errors, sample_points) so the bad zones
    can be located."""
    centroids = result.cell_centers().points
    dists = vtk.vtkDoubleArray()
    distance_fn.FunctionValue(pv.convert_array(centroids), dists)
    errors = np.abs(pv.convert_array(dists))
    return errors, centroids


def error_summary(errors: np.ndarray):
    """Reduces an array of per-face-center errors to three numbers
    summarizing result quality: the maximum (worst single-point case,
    the only one shown before), the mean (overall quality), and the
    95th percentile (how bad the band of worst points is without a
    single isolated spike distorting it, more informative than the
    maximum on large meshes)."""
    if errors.size == 0:
        return 0.0, 0.0, 0.0
    return float(errors.max()), float(errors.mean()), float(np.percentile(errors, 95))


def measure_bidirectional_error(
    source: pv.PolyData,
    result: pv.PolyData,
    distance_to_source: Optional["vtk.vtkImplicitPolyDataDistance"] = None,
):
    """Measures the geometric error in both directions between `source`
    and `result`, not only `result` -> `source`: that single direction
    (the only one measured before) doesn't detect that a prominent
    detail from the original has completely disappeared in the result,
    because it only asks "how far is each face center of RESULT from the
    original surface?" and if the result no longer has any face near the
    removed area, that question never gets asked there. The reverse
    direction, `source` -> `result`, asks the opposite: "how far is each
    ORIGINAL face center from the result?", and if that area has been
    flattened/removed, the corresponding original center will still be
    far from the decimated surface, giving it away. The true Hausdorff
    maximum is the larger of the two directions; both are returned
    separately here (instead of already combined) because each one
    locates the problem on a different mesh -`result` or `source`- and
    `refine_by_error` needs to know which one in order to protect the
    correct vertices. `distance_to_source` can be passed already built
    (with SetInput applied) to reuse it across refinement passes, since
    `source` doesn't change between them; `result` does, so its locator
    (`distance_to_result`) is always rebuilt. Returns (result_errors,
    result_centroids, source_errors, source_centroids)."""
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
    """Measures the bidirectional geometric error between `source` and
    `result` (see `measure_bidirectional_error`) and, wherever either
    direction exceeds the configured threshold, protects those vertices
    in the original mesh and decimates again; repeats up to
    `max_iterations` passes or until the error is within the threshold.
    Bad zones detected in `result` (original direction) are translated
    to `source` vertices by nearest neighbor (approximate: `result`
    centers don't coincide with any `source` vertex); those detected
    directly in `source` (new direction) are translated exactly, using
    the marked face's own vertices. Returns (result, updated mask, final
    max error, final mean error, final 95th percentile error, number of
    passes, points added)."""
    threshold = (error_threshold_pct / 100.0) * source.length
    source_tree = cKDTree(source.points)
    source_faces = source.regular_faces
    # Built once and reused across up to max_iterations passes: `source`
    # doesn't change between iterations, only `result` does, so
    # rebuilding its spatial locator on every pass would be unnecessary
    # repeated work (the one for `result` does need rebuilding every
    # time, see measure_bidirectional_error).
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
    """Full adaptive decimation pipeline over an already-loaded mesh (and
    already cleaned, if applicable): applies the protection mask (manual
    + automatic UV seams), decimates, and if
    `protection.auto_error_protection` is active, iteratively refines by
    protecting high-error zones. This is the single pipeline shared by
    the interactive flow (the "Apply decimation" button) and by batch
    processing, so as not to keep two copies of the same logic. Raises
    ValueError if the result ends up with no faces."""
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
            "Decimation left the mesh with no faces. Try a lower "
            "aggressiveness or fewer protected zones."
        )

    iterations = added = 0
    if protection.auto_error_protection:
        result, mask, error_max, error_mean, error_p95, iterations, added = refine_by_error(
            mesh, result, mask, decimation, protection.error_threshold_pct,
            protection.error_max_iterations,
        )
        if result.n_cells == 0:
            raise ValueError(
                "Decimation left the mesh with no faces. Try a lower "
                "aggressiveness or fewer protected zones."
            )
    else:
        # Even without auto error-protection, measuring it once (without
        # iterative refinement) is still useful as a quality diagnostic
        # of the result. Bidirectional for the same reason as in
        # refine_by_error: the original direction (result->mesh) wouldn't
        # detect a detail from the original that was completely removed.
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
