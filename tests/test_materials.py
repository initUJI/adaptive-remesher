"""Tests for materials.py: reading glTF maps, writing a GLB that keeps them,
and baking a normal map."""

import json

import numpy as np
import pyvista as pv
from PIL import Image

from materials import (
    MaterialMaps,
    bake_normal_map,
    load_material_maps,
    orm_image,
    welded_vertex_normals,
    write_glb,
)
from remesh_core import decimate_qem, transfer_texture_coords


def _textured_sphere(n=40):
    sphere = pv.Sphere(theta_resolution=n, phi_resolution=n).triangulate()
    return sphere.texture_map_to_sphere(inplace=False)


def test_load_material_maps_reads_every_slot(tmp_path):
    for name, color in (("base", (200, 30, 30)), ("normal", (128, 128, 255)), ("mr", (0, 120, 10))):
        Image.new("RGB", (4, 2), color).save(tmp_path / f"{name}.png")
    gltf = {
        "asset": {"version": "2.0"},
        "images": [{"uri": "base.png"}, {"uri": "normal.png"}, {"uri": "mr.png"}],
        "textures": [{"source": 0}, {"source": 1}, {"source": 2}],
        "materials": [{
            "pbrMetallicRoughness": {
                "baseColorTexture": {"index": 0},
                "metallicRoughnessTexture": {"index": 2},
                "roughnessFactor": 0.7,
            },
            "normalTexture": {"index": 1, "scale": 0.5},
        }],
    }
    (tmp_path / "m.gltf").write_text(json.dumps(gltf))

    maps = load_material_maps(str(tmp_path / "m.gltf"))

    assert maps is not None
    assert maps.available() == ["base", "normal", "metal_rough"]
    assert maps.base.shape == (2, 4, 3)
    assert tuple(maps.base[0, 0]) == (200, 30, 30)
    assert maps.factors["roughnessFactor"] == 0.7
    assert maps.factors["normalScale"] == 0.5


def test_load_material_maps_none_without_textures(tmp_path):
    (tmp_path / "m.gltf").write_text(json.dumps({"asset": {"version": "2.0"}, "materials": [{}]}))
    assert load_material_maps(str(tmp_path / "m.gltf")) is None
    assert load_material_maps(str(tmp_path / "x.obj")) is None


def test_write_glb_keeps_geometry_uv_and_maps(tmp_path):
    mesh = _textured_sphere(12)
    base = np.random.default_rng(1).integers(0, 255, (8, 8, 3), dtype=np.uint8)
    normal = np.full((8, 8, 3), (128, 128, 255), dtype=np.uint8)
    maps = MaterialMaps(base=base, normal=normal, factors={"roughnessFactor": 0.4, "normalScale": 1.0})
    path = str(tmp_path / "out.glb")

    write_glb(path, mesh, maps)

    back = load_material_maps(path)
    assert back is not None and back.available() == ["base", "normal"]
    np.testing.assert_array_equal(back.base, base)
    assert back.factors["roughnessFactor"] == 0.4
    loaded = pv.read(path)
    block = loaded.combine().extract_surface(algorithm="dataset_surface")
    assert block.n_cells == mesh.n_cells


def test_write_glb_without_uv_or_maps(tmp_path):
    mesh = pv.Sphere(theta_resolution=8, phi_resolution=8).triangulate()
    mesh.clear_data()
    path = str(tmp_path / "plain.glb")

    write_glb(path, mesh, None)

    assert pv.read(path).combine().extract_surface(algorithm="dataset_surface").n_cells == mesh.n_cells


def test_welded_normals_match_across_duplicated_vertices():
    mesh = _textured_sphere(12)
    normals = welded_vertex_normals(mesh)
    assert normals.shape == (mesh.n_points, 3)
    np.testing.assert_allclose(np.linalg.norm(normals, axis=1), 1.0, atol=1e-6)


def test_bake_normal_map_is_flat_when_low_equals_high():
    high = _textured_sphere(24)

    baked = bake_normal_map(high, high, size=64)

    assert baked.shape == (64, 64, 3) and baked.dtype == np.uint8
    covered = baked.reshape(-1, 3)
    # Same surface, same smooth normals: the map is the identity (128, 128, 255).
    assert np.abs(covered[:, 0].astype(int) - 128).mean() < 6
    assert np.abs(covered[:, 1].astype(int) - 128).mean() < 6
    assert covered[:, 2].astype(int).mean() > 240


def test_bake_normal_map_captures_detail_lost_by_decimation():
    # A sphere with a ripple: its normals tilt back and forth, and a coarse
    # copy (smooth shading over few triangles) cannot reproduce that.
    high = _textured_sphere(90)
    radius = 1.0 + 0.05 * np.sin(10.0 * np.arctan2(high.points[:, 1], high.points[:, 0]))
    high.points = high.points * (radius / np.linalg.norm(high.points, axis=1))[:, None]
    bare = pv.PolyData(high.points.copy(), high.faces.copy())
    low = transfer_texture_coords(high, decimate_qem(bare, 0.95, True, False, 1.0, False))
    assert low.n_cells < high.n_cells / 10

    baked = bake_normal_map(high, low, size=128)

    decoded = baked.reshape(-1, 3).astype(float) / 255.0 * 2.0 - 1.0
    assert np.abs(decoded[:, :2]).mean() > 0.05  # the map bends the normals back
    assert decoded[:, 2].mean() > 0.7  # but stays a plausible tangent-space map


def test_bake_normal_map_needs_uv():
    mesh = pv.Sphere(theta_resolution=8, phi_resolution=8).triangulate()
    mesh.clear_data()
    try:
        bake_normal_map(mesh, mesh, size=16)
    except ValueError as exc:
        assert "texture coordinates" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_bake_normal_map_ray_miss_keeps_the_low_poly_normal():
    high = _textured_sphere(24)
    low = high.copy()
    low.points = low.points * 3.0  # far outside the cage: every ray misses

    baked = bake_normal_map(high, low, size=32)

    assert (baked.reshape(-1, 3) == (128, 128, 255)).all()


def test_bake_normal_map_nearest_method_still_works():
    high = _textured_sphere(24)

    baked = bake_normal_map(high, high, size=32, method="nearest")

    assert baked.shape == (32, 32, 3)
    assert np.abs(baked[..., 0].astype(int) - 128).mean() < 6


def test_orm_image_packs_occlusion_roughness_metallic():
    mr = np.zeros((4, 4, 3), dtype=np.uint8)
    mr[..., 1], mr[..., 2] = 100, 200  # roughness, metallic
    occ = np.zeros((2, 2, 3), dtype=np.uint8)
    occ[..., 0] = 50  # smaller image: resized to the roughness map

    packed = orm_image(MaterialMaps(metal_rough=mr, occlusion=occ))

    assert packed.shape == (4, 4, 3)
    assert (packed[..., 0] == 50).all() and (packed[..., 1] == 100).all() and (packed[..., 2] == 200).all()


def test_orm_image_without_occlusion_is_white_and_none_without_maps():
    mr = np.full((2, 2, 3), (7, 100, 200), dtype=np.uint8)
    assert (orm_image(MaterialMaps(metal_rough=mr))[..., 0] == 255).all()  # glTF: no occlusion map, no occlusion
    assert orm_image(MaterialMaps(base=mr)) is None
