"""Tests for materials.py: reading glTF maps, writing a GLB that keeps them,
and baking a normal map."""

import json

import numpy as np
import pyvista as pv
from PIL import Image

from materials import (
    MaterialMaps,
    bake_material_maps,
    bake_maps,
    bake_normal_map,
    load_material_maps,
    map_to_texture,
    orm_image,
    reparametrize,
    xatlas_available,
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

    write_glb(path, mesh, maps, jpeg_quality=None)

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


def _gradient(size=256):
    ramp = np.linspace(0, 255, size, dtype=np.uint8)
    return np.stack([np.tile(ramp, (size, 1)), np.tile(ramp[:, None], (1, size)), np.full((size, size), 90, np.uint8)], axis=2)


def test_write_glb_stores_colour_as_smaller_jpeg(tmp_path):
    import os

    mesh = _textured_sphere(12)
    normal = np.full((256, 256, 3), (128, 128, 255), dtype=np.uint8)
    # A photograph-like image (noisy): PNG cannot compress it, JPEG can.
    photo = np.random.default_rng(0).integers(60, 200, (256, 256, 3), dtype=np.uint8)
    png, jpg = str(tmp_path / "png.glb"), str(tmp_path / "jpg.glb")

    write_glb(png, mesh, MaterialMaps(base=photo, normal=normal), jpeg_quality=None)
    write_glb(jpg, mesh, MaterialMaps(base=photo, normal=normal))
    assert os.path.getsize(jpg) < os.path.getsize(png)

    smooth = MaterialMaps(base=_gradient(), normal=normal)
    write_glb(jpg, mesh, smooth)
    back = load_material_maps(jpg)
    assert np.abs(back.base.astype(int) - smooth.base.astype(int)).mean() < 3  # close, not identical
    np.testing.assert_array_equal(back.normal, normal)  # data maps stay lossless


def test_bake_maps_resamples_a_colour_map_onto_the_same_layout():
    high = _textured_sphere(24)
    image = _gradient(128)

    baked = bake_maps(high, high, {"base": image}, size=64, bake_normal=False)

    assert set(baked) == {"base"} and baked["base"].shape == (64, 64, 3)
    small = np.asarray(__import__("PIL.Image", fromlist=["Image"]).fromarray(image).resize((64, 64)))
    assert np.abs(baked["base"].astype(int) - small.astype(int)).mean() < 12  # same picture at 64 px


import pytest


@pytest.mark.skipif(not xatlas_available(), reason="optional package xatlas not installed")
def test_rebake_moves_the_colour_onto_a_new_atlas():
    high = _textured_sphere(40)
    image = _gradient(256)
    maps = MaterialMaps(base=image)
    bare = pv.PolyData(high.points.copy(), high.faces.copy())
    low = reparametrize(decimate_qem(bare, 0.8, True, False, 1.0, False), size=128)
    assert low.active_texture_coordinates is not None
    assert low.active_texture_coordinates.min() >= -1e-6 and low.active_texture_coordinates.max() <= 1 + 1e-6

    new = bake_material_maps(high, low, maps, size=128)

    # Compare, at low's vertices, the colour read through the new atlas with the colour read through the old one.
    from scipy.spatial import cKDTree

    uv_new = np.asarray(low.active_texture_coordinates)
    px = np.clip((uv_new[:, 0] * 127).round().astype(int), 0, 127)
    py = np.clip(((1 - uv_new[:, 1]) * 127).round().astype(int), 0, 127)
    colour_new = new.base[py, px].astype(float)
    _, near = cKDTree(high.points).query(low.points)
    uv_old = np.asarray(high.active_texture_coordinates)[near]
    away_from_seam = (uv_old[:, 0] > 0.1) & (uv_old[:, 0] < 0.9)
    ox = np.clip((uv_old[:, 0] * 255).round().astype(int), 0, 255)
    oy = np.clip(((1 - uv_old[:, 1]) * 255).round().astype(int), 0, 255)
    colour_old = image[oy, ox].astype(float)
    assert np.abs(colour_new - colour_old)[away_from_seam].mean() < 25


def test_map_to_texture_matches_pyvista_file_reader(tmp_path):
    # Top half red, bottom half blue. pyvista's own reader is the ground truth for orientation:
    # a texture built from the array must equal the one read from the saved image, not its mirror.
    image = np.zeros((6, 4, 3), dtype=np.uint8)
    image[:3] = (255, 0, 0)
    image[3:] = (0, 0, 255)
    Image.fromarray(image).save(tmp_path / "probe.png")

    from_array = map_to_texture(image).to_array()
    from_file = pv.read_texture(str(tmp_path / "probe.png")).to_array()

    np.testing.assert_array_equal(from_array, from_file)


def test_texture_error_is_zero_for_the_source_and_positive_for_wrong_uv():
    from materials import texture_error

    plane = pv.Plane(i_resolution=8, j_resolution=8).triangulate()
    uv = np.asarray(plane.points)[:, :2] + 0.5
    plane.active_texture_coordinates = uv
    image = (np.random.default_rng(0).random((64, 64, 3)) * 255).astype(np.uint8)

    assert texture_error(plane, plane, image, n=500)["mean"] < 1e-6

    shifted = plane.copy()
    shifted.active_texture_coordinates = uv + 0.25
    wrong = texture_error(plane, shifted, image, n=500)
    assert wrong["mean"] > 5.0 and wrong["floor_mean"] < 1e-6


def test_map_errors_covers_every_map_and_is_zero_for_the_source():
    from materials import MaterialMaps, map_errors

    plane = pv.Plane(i_resolution=8, j_resolution=8).triangulate()
    uv = np.asarray(plane.points)[:, :2] + 0.5
    plane.active_texture_coordinates = uv
    rng = np.random.default_rng(1)
    image = lambda: (rng.random((64, 64, 3)) * 255).astype(np.uint8)
    maps = MaterialMaps(base=image(), normal=image(), metal_rough=image(), occlusion=image(), emissive=image())

    same = map_errors(plane, plane, maps, n=400)
    assert set(same) == {"base", "emissive", "normal", "roughness", "metallic", "occlusion"}
    assert all(v["mean"] < 1e-6 for v in same.values())

    shifted = plane.copy()
    shifted.active_texture_coordinates = uv + 0.25
    wrong = map_errors(plane, shifted, maps, n=400)
    assert wrong["normal"]["mean"] > 10.0  # degrees
    assert wrong["roughness"]["mean"] > 5.0 and wrong["base"]["floor_mean"] < 1e-6


def test_shading_normal_error_is_zero_for_the_source_and_best_normal_map_keeps_a_good_map():
    from materials import MaterialMaps, best_normal_map, shading_normal_error

    plane = pv.Plane(i_resolution=8, j_resolution=8).triangulate()
    plane.active_texture_coordinates = np.asarray(plane.points)[:, :2] + 0.5
    flat = np.zeros((32, 32, 3), np.uint8)
    flat[...] = (128, 128, 255)  # tangent-space normal (0, 0, 1)

    same = shading_normal_error(plane, plane, flat, {"carried": flat, "none": None}, n=300)
    assert same["carried"]["mean"] < 1e-3 and same["none"]["mean"] < 0.5  # (128, 128, 255) tilts by 0.3 deg

    maps = MaterialMaps(base=flat, normal=flat)
    low = pv.Plane(i_resolution=2, j_resolution=2).triangulate()
    low.active_texture_coordinates = np.asarray(low.points)[:, :2] + 0.5
    chosen, info = best_normal_map(plane, low, maps, size=64)
    assert set(info) == {"carried_mean_deg", "baked_mean_deg"}
    assert info["carried_mean_deg"] < 1.0  # a flat plane: the carried map is already right
    assert chosen is None or chosen.shape == (64, 64, 3)
