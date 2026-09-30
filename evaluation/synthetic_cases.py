"""Synthetic test cases for Experiments A (bidirectional error detection)
and C (masked simplification). All four are built on CLOSED surfaces
(spheres, or a small sphere merged with a bigger one) rather than open
planes: a bare `pv.Plane()` has a genuine open boundary around its
perimeter, and `decimate_with_mask` unconditionally runs
`_stitch_residual_boundary_gaps` whenever any mask is set (see
remesh_core.py) - it can't distinguish that legitimate boundary from a
seam-repair gap and would try to cap it with a triangle fan, corrupting
the mesh. Closed surfaces sidestep that entirely (verified empirically
while building this: `boundary.n_cells == 0` for all four cases below).

Each case exposes:
- `source`: the "ground truth" mesh.
- `important_mask`: boolean per-vertex mask on `source` marking the
  detail/region a user would protect - used as-is as the protection mask
  for Experiment C, and to classify source faces as
  protected/unprotected when splitting error by region.
- `handcrafted_result` (cases 1-2 only): a literal, hand-built "already
  decimated" mesh with the feature already removed/altered, bypassing
  the decimation engine entirely - a clean, unambiguous ground truth for
  Experiment A. `None` for cases 3-4, where Experiment A instead runs the
  real (unprotected) decimation pipeline to see whether the engine
  itself loses the detail.
"""

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pyvista as pv


@dataclass
class SyntheticCase:
    name: str
    description: str
    source: pv.PolyData
    important_mask: np.ndarray
    handcrafted_result: Optional[pv.PolyData] = None
    # Case 4 only: per-detail sub-masks, for a finer per-region breakdown
    # than the single combined `important_mask`.
    region_masks: Optional[dict] = None


def _sphere_bump(points: np.ndarray, center_dir, angular_radius_deg: float, height: float):
    """Displaces `points` (assumed centered at the origin, any radius)
    radially outward (or inward, if `height` is negative) in a smooth,
    cosine-falloff patch around `center_dir`. Returns (displaced_points,
    in_region_mask)."""
    center_dir = np.asarray(center_dir, dtype=float)
    center_dir = center_dir / np.linalg.norm(center_dir)
    norms = np.linalg.norm(points, axis=1)
    cos_angle = np.clip((points @ center_dir) / norms, -1.0, 1.0)
    angle = np.arccos(cos_angle)
    radius = np.radians(angular_radius_deg)
    in_region = angle < radius
    falloff = np.zeros(len(points))
    falloff[in_region] = height * (np.cos(np.pi * angle[in_region] / radius) + 1) / 2
    displaced = points * (1.0 + falloff[:, None])
    return displaced, in_region


def case1_removed_bump(resolution: int = 40) -> SyntheticCase:
    """A big sphere with a small satellite sphere floating away from it,
    merged WITHOUT welding (`merge_points=False`): the two pieces are
    entirely disconnected. `handcrafted_result` drops the satellite
    entirely, simulating a decimation (or any simplification) that
    flattened/removed it completely. This is the case that produces
    result->source ~= 0 while source->result is large: every face of
    `handcrafted_result` is already part of `source` (the big sphere), so
    its distance to `source` is ~0 regardless of the missing satellite."""
    big = pv.Sphere(theta_resolution=resolution, phi_resolution=resolution).triangulate()
    small = pv.Sphere(
        radius=0.15, center=(3.0, 3.0, 3.0), theta_resolution=12, phi_resolution=12
    ).triangulate()
    source = big.merge(small, merge_points=False)
    important_mask = np.zeros(source.n_points, dtype=bool)
    important_mask[big.n_points:] = True  # merge() appends `small`'s points after `big`'s
    return SyntheticCase(
        name="case1_removed_bump",
        description="Disconnected satellite region completely removed from the result.",
        source=source,
        important_mask=important_mask,
        handcrafted_result=big.copy(),
    )


def case2_partial_detail(resolution: int = 60) -> SyntheticCase:
    """A sphere with one raised, connected bump (not a separate piece).
    `handcrafted_result` flattens only the OUTER ring of the bump back to
    the sphere's surface, keeping the inner peak - a partial, not total,
    loss of detail, to check that bidirectional error still picks up a
    graded/partial feature loss, not just a fully-vanished one."""
    sphere = pv.Sphere(theta_resolution=resolution, phi_resolution=resolution).triangulate()
    pts = sphere.points
    center_dir = (0.0, 0.0, 1.0)
    bump_radius_deg = 20.0
    displaced, in_region = _sphere_bump(pts, center_dir, bump_radius_deg, height=0.6)
    source = sphere.copy()
    source.points = displaced

    norms = np.linalg.norm(pts, axis=1)
    cos_angle = np.clip((pts @ np.array(center_dir)) / norms, -1.0, 1.0)
    angle_deg = np.degrees(np.arccos(cos_angle))
    outer_ring = in_region & (angle_deg > bump_radius_deg * 0.4)  # outer 60% of the bump's radius

    result_pts = displaced.copy()
    result_pts[outer_ring] = pts[outer_ring]  # flatten just the outer ring back to the sphere
    result = sphere.copy()
    result.points = result_pts

    return SyntheticCase(
        name="case2_partial_detail",
        description="Connected raised bump, only its outer ring flattened (partial removal).",
        source=source,
        important_mask=in_region,
        handcrafted_result=result,
    )


def case3_surface_relief(resolution: int = 60) -> SyntheticCase:
    """A sphere with a small, localized bump, smaller and more subtle
    than case 2's. Unlike cases 1-2, no handcrafted result: Experiment A
    runs this through the REAL (unprotected) decimation engine at an
    aggressive reduction to see whether the engine itself smooths the
    detail away, and whether bidirectional error catches it even though
    the detail was never a disconnected piece, just a small part of one
    continuous surface."""
    sphere = pv.Sphere(theta_resolution=resolution, phi_resolution=resolution).triangulate()
    pts = sphere.points
    displaced, in_region = _sphere_bump(pts, center_dir=(1.0, 0.0, 0.3), angular_radius_deg=8.0, height=0.35)
    source = sphere.copy()
    source.points = displaced
    return SyntheticCase(
        name="case3_surface_relief",
        description="Small, localized bump on an otherwise continuous surface (no handcrafted result).",
        source=source,
        important_mask=in_region,
        handcrafted_result=None,
    )


def case4_multi_region(resolution: int = 70) -> SyntheticCase:
    """A sphere with three raised details of decreasing size/prominence
    (A large, B medium, C small), at different locations. No handcrafted
    result: used to check whether error-guided auto-protection singles
    out only the details that actually exceed the error threshold,
    rather than all-or-nothing."""
    sphere = pv.Sphere(theta_resolution=resolution, phi_resolution=resolution).triangulate()
    pts = sphere.points

    pts_a, mask_a = _sphere_bump(pts, center_dir=(0.0, 0.0, 1.0), angular_radius_deg=18.0, height=0.5)
    pts_b, mask_b = _sphere_bump(pts_a, center_dir=(1.0, 0.0, -0.3), angular_radius_deg=10.0, height=0.3)
    pts_c, mask_c = _sphere_bump(pts_b, center_dir=(-0.7, 0.7, -0.2), angular_radius_deg=5.0, height=0.15)

    source = sphere.copy()
    source.points = pts_c
    important_mask = mask_a | mask_b | mask_c
    return SyntheticCase(
        name="case4_multi_region",
        description="Three raised details of decreasing size/prominence at different locations.",
        source=source,
        important_mask=important_mask,
        handcrafted_result=None,
        region_masks={"A": mask_a, "B": mask_b, "C": mask_c},
    )


ALL_CASES = [
    case1_removed_bump,
    case2_partial_detail,
    case3_surface_relief,
    case4_multi_region,
]
