"""Material maps (base colour, normal, metallic-roughness, occlusion,
emissive): reading them from glTF/GLB, writing a GLB that keeps them, and
baking a tangent-space normal map that carries the detail a decimation
removed. Independent of Qt. All images are uint8 arrays stored top row first,
like image files; UV coordinates follow VTK (v up)."""

import base64
import json
import os
import struct
from dataclasses import dataclass, field
from io import BytesIO
from typing import Callable, Dict, Optional
from urllib.parse import unquote

import numpy as np
import pyvista as pv
from scipy.ndimage import distance_transform_edt
from scipy.spatial import cKDTree  # type: ignore[attr-defined]

MAP_NAMES = ("base", "normal", "metal_rough", "occlusion", "emissive")


@dataclass
class MaterialMaps:
    """The images of one material plus the glTF factors that go with them.
    `metal_rough` is glTF's packed map (G roughness, B metallic) and
    `occlusion` may be the same image."""

    base: Optional[np.ndarray] = None
    normal: Optional[np.ndarray] = None
    metal_rough: Optional[np.ndarray] = None
    occlusion: Optional[np.ndarray] = None
    emissive: Optional[np.ndarray] = None
    factors: Dict[str, object] = field(default_factory=dict)

    def available(self) -> list:
        return [n for n in MAP_NAMES if getattr(self, n) is not None]


def _decode(data: bytes) -> Optional[np.ndarray]:
    try:
        from PIL import Image

        return np.asarray(Image.open(BytesIO(data)).convert("RGB"))
    except Exception:
        return None


def _read_gltf(path: str):
    with open(path, "rb") as f:
        raw = f.read()
    binary = None
    if raw[:4] == b"glTF":
        offset, doc = 12, None
        while offset + 8 <= len(raw):
            length, kind = struct.unpack_from("<II", raw, offset)
            chunk = raw[offset + 8 : offset + 8 + length]
            if kind == 0x4E4F534A:
                doc = json.loads(chunk.decode("utf-8"))
            elif kind == 0x004E4942:
                binary = chunk
            offset += 8 + length
        return doc, binary
    return json.loads(raw.decode("utf-8")), None


def _gltf_image(doc: dict, binary: Optional[bytes], path: str, texture_index: int) -> Optional[np.ndarray]:
    source = doc["textures"][texture_index].get("source")
    if source is None:
        return None
    image = doc["images"][source]
    if "bufferView" in image and binary is not None:
        view = doc["bufferViews"][image["bufferView"]]
        start = view.get("byteOffset", 0)
        return _decode(binary[start : start + view["byteLength"]])
    uri = image.get("uri", "")
    if uri.startswith("data:"):
        return _decode(base64.b64decode(uri.split(",", 1)[1]))
    image_path = os.path.join(os.path.dirname(path), unquote(uri))
    if os.path.isfile(image_path):
        with open(image_path, "rb") as f:
            return _decode(f.read())
    return None


def load_material_maps(path: str) -> Optional[MaterialMaps]:
    """Every map of the first textured material of a .gltf/.glb, with its
    factors; None for other formats, no textured material, or any error."""
    if path.lower().rsplit(".", 1)[-1] not in ("gltf", "glb"):
        return None
    try:
        doc, binary = _read_gltf(path)
        if doc is None:
            return None
        for material in doc.get("materials", []):
            pbr = material.get("pbrMetallicRoughness", {})
            slots = {
                "base": pbr.get("baseColorTexture"),
                "normal": material.get("normalTexture"),
                "metal_rough": pbr.get("metallicRoughnessTexture"),
                "occlusion": material.get("occlusionTexture"),
                "emissive": material.get("emissiveTexture"),
            }
            if not any(slots.values()):
                continue
            maps = MaterialMaps()
            for name, ref in slots.items():
                if ref is not None:
                    setattr(maps, name, _gltf_image(doc, binary, path, ref["index"]))
            maps.factors = {
                "baseColorFactor": pbr.get("baseColorFactor"),
                "metallicFactor": pbr.get("metallicFactor"),
                "roughnessFactor": pbr.get("roughnessFactor"),
                "emissiveFactor": material.get("emissiveFactor"),
                "normalScale": (slots["normal"] or {}).get("scale"),
                "occlusionStrength": (slots["occlusion"] or {}).get("strength"),
            }
            return maps if maps.available() else None
    except Exception:
        return None
    return None


def map_to_texture(image: np.ndarray) -> pv.Texture:
    """A pyvista texture from a top-row-first image. pyvista already expects
    that order (it matches `pv.read_texture` of the same file), so the array
    is not flipped; flipping it would draw every texture upside down."""
    return pv.Texture(np.ascontiguousarray(image))


def orm_image(maps: MaterialMaps) -> Optional[np.ndarray]:
    """The packed occlusion (R), roughness (G), metallic (B) image VTK's PBR
    shading expects, built from glTF's separate maps: roughness and metallic
    from `metal_rough`, occlusion from `occlusion` (white, i.e. none, if the
    material has no occlusion map). None if it has neither map."""
    if maps.metal_rough is None and maps.occlusion is None:
        return None
    reference = maps.metal_rough if maps.metal_rough is not None else maps.occlusion
    out = np.empty_like(reference)
    if maps.metal_rough is not None:
        out[...] = maps.metal_rough
    else:
        out[...] = 255  # no roughness/metallic map: factors alone decide
    if maps.occlusion is not None:
        occlusion = maps.occlusion
        if occlusion.shape[:2] != reference.shape[:2]:
            from PIL import Image

            occlusion = np.asarray(
                Image.fromarray(occlusion).resize((reference.shape[1], reference.shape[0]))
            )
        out[..., 0] = occlusion[..., 0]
    else:
        out[..., 0] = 255
    return out


def with_tangents(mesh: pv.PolyData) -> pv.PolyData:
    """A copy of `mesh` with smooth normals and tangents, which VTK's PBR
    shading needs before it applies a normal map (without them the map has no
    effect). Requires texture coordinates."""
    import vtk

    shaded = mesh.compute_normals(cell_normals=False, split_vertices=False, auto_orient_normals=False)
    tangents = vtk.vtkPolyDataTangents()
    tangents.SetInputData(shaded)
    tangents.Update()
    return pv.wrap(tangents.GetOutput())


def _png(image: np.ndarray) -> bytes:
    from PIL import Image

    buf = BytesIO()
    Image.fromarray(np.ascontiguousarray(image)).save(buf, format="PNG")
    return buf.getvalue()


def welded_vertex_normals(mesh: pv.PolyData) -> np.ndarray:
    """Smooth area-weighted vertex normals computed over welded positions, so
    the two copies of a texture-seam vertex get the same normal and shading
    does not show the seam."""
    pts = np.asarray(mesh.points, dtype=float)
    tri = np.asarray(mesh.regular_faces)
    face_n = np.cross(pts[tri[:, 1]] - pts[tri[:, 0]], pts[tri[:, 2]] - pts[tri[:, 0]])
    scale = float(mesh.length) or 1.0
    _, weld = np.unique(np.round(pts / (1e-7 * scale)), axis=0, return_inverse=True)
    weld = weld.reshape(-1)
    acc = np.zeros((int(weld.max()) + 1, 3))
    for k in range(3):
        np.add.at(acc, weld[tri[:, k]], face_n)
    normals = acc[weld]
    length = np.linalg.norm(normals, axis=1, keepdims=True)
    return normals / np.where(length > 0, length, 1.0)


def _jpeg(image: np.ndarray, quality: int) -> bytes:
    from PIL import Image

    buf = BytesIO()
    Image.fromarray(np.ascontiguousarray(image)).save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def write_glb(
    path: str,
    mesh: pv.PolyData,
    maps: Optional[MaterialMaps] = None,
    jpeg_quality: Optional[int] = 90,
) -> None:
    """Writes `mesh` (triangles, with UV if it has them) as a GLB. With
    `maps`, the material keeps every map and factor, so metallic-roughness,
    occlusion and emissive survive a decimation that left the UV unchanged.
    The colour maps (base colour, emissive) are stored as JPEG at
    `jpeg_quality`, which is a fraction of the size of a 4k PNG for a loss
    that is hard to see; the maps that carry data (normal, metallic-roughness,
    occlusion) stay lossless PNG. `jpeg_quality=None` keeps everything PNG."""
    pts = np.asarray(mesh.points, dtype="<f4")
    tri = np.asarray(mesh.regular_faces, dtype="<u4")
    normals = welded_vertex_normals(mesh).astype("<f4")
    uv = mesh.active_texture_coordinates
    chunks: list = []
    views: list = []
    accessors: list = []

    def add_view(data: bytes, target: Optional[int] = None) -> int:
        offset = sum(len(c) for c in chunks)
        chunks.append(data + b"\x00" * (-len(data) % 4))
        view = {"buffer": 0, "byteOffset": offset, "byteLength": len(data)}
        if target:
            view["target"] = target
        views.append(view)
        return len(views) - 1

    attributes = {}
    accessors.append({
        "bufferView": add_view(pts.tobytes(), 34962), "componentType": 5126, "count": len(pts), "type": "VEC3",
        "min": pts.min(axis=0).tolist(), "max": pts.max(axis=0).tolist(),
    })
    attributes["POSITION"] = len(accessors) - 1
    accessors.append({"bufferView": add_view(normals.tobytes(), 34962), "componentType": 5126, "count": len(pts), "type": "VEC3"})
    attributes["NORMAL"] = len(accessors) - 1
    if uv is not None:
        gl_uv = np.column_stack([uv[:, 0], 1.0 - uv[:, 1]]).astype("<f4")  # glTF's v points down
        accessors.append({"bufferView": add_view(gl_uv.tobytes(), 34962), "componentType": 5126, "count": len(pts), "type": "VEC2"})
        attributes["TEXCOORD_0"] = len(accessors) - 1
    accessors.append({"bufferView": add_view(tri.reshape(-1).tobytes(), 34963), "componentType": 5125, "count": tri.size, "type": "SCALAR"})
    indices = len(accessors) - 1

    doc: dict = {"asset": {"version": "2.0", "generator": "Adaptive Remesher"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}]}
    primitive = {"attributes": attributes, "indices": indices, "mode": 4}
    if maps is not None and uv is not None and maps.available():
        images, textures = [], []

        def texture_ref(name: str, **extra) -> Optional[dict]:
            image = getattr(maps, name)
            if image is None:
                return None
            if jpeg_quality is not None and name in ("base", "emissive"):
                images.append({"bufferView": add_view(_jpeg(image, jpeg_quality)), "mimeType": "image/jpeg"})
            else:
                images.append({"bufferView": add_view(_png(image)), "mimeType": "image/png"})
            textures.append({"source": len(images) - 1})
            return {"index": len(textures) - 1, **extra}

        f = maps.factors
        pbr: dict = {}
        if f.get("baseColorFactor") is not None:
            pbr["baseColorFactor"] = f["baseColorFactor"]
        if f.get("metallicFactor") is not None:
            pbr["metallicFactor"] = f["metallicFactor"]
        if f.get("roughnessFactor") is not None:
            pbr["roughnessFactor"] = f["roughnessFactor"]
        material: dict = {"pbrMetallicRoughness": pbr}
        base = texture_ref("base")
        if base:
            pbr["baseColorTexture"] = base
        mr = texture_ref("metal_rough")
        if mr:
            pbr["metallicRoughnessTexture"] = mr
        normal = texture_ref("normal", **({"scale": f["normalScale"]} if f.get("normalScale") is not None else {}))
        if normal:
            material["normalTexture"] = normal
        occ = texture_ref("occlusion", **({"strength": f["occlusionStrength"]} if f.get("occlusionStrength") is not None else {}))
        if occ:
            material["occlusionTexture"] = occ
        emi = texture_ref("emissive")
        if emi:
            material["emissiveTexture"] = emi
        if f.get("emissiveFactor") is not None:
            material["emissiveFactor"] = f["emissiveFactor"]
        doc.update({"images": images, "textures": textures, "materials": [material]})
        primitive["material"] = 0
    doc["meshes"] = [{"primitives": [primitive]}]
    blob = b"".join(chunks)
    doc.update({"buffers": [{"byteLength": len(blob)}], "bufferViews": views, "accessors": accessors})
    text = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    text += b" " * (-len(text) % 4)
    total = 12 + 8 + len(text) + 8 + len(blob)
    with open(path, "wb") as out:
        out.write(struct.pack("<4sII", b"glTF", 2, total))
        out.write(struct.pack("<II", len(text), 0x4E4F534A) + text)
        out.write(struct.pack("<II", len(blob), 0x004E4942) + blob)


# --- normal map baking ---


def _triangle_frames(pts: np.ndarray, uv: np.ndarray, tri: np.ndarray):
    """Per-triangle tangent (direction of increasing u) and bitangent
    (increasing v, v up), from positions and UV."""
    p0, p1, p2 = pts[tri[:, 0]], pts[tri[:, 1]], pts[tri[:, 2]]
    e1, e2 = p1 - p0, p2 - p0
    d1, d2 = uv[tri[:, 1]] - uv[tri[:, 0]], uv[tri[:, 2]] - uv[tri[:, 0]]
    det = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(np.abs(det) > 1e-20, 1.0 / det, 0.0)
    tangent = (e1 * d2[:, 1:2] - e2 * d1[:, 1:2]) * r[:, None]
    bitangent = (e2 * d1[:, 0:1] - e1 * d2[:, 0:1]) * r[:, None]
    return tangent, bitangent


def _normalized(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.where(n > 1e-20, n, 1.0)


def _frame(normal: np.ndarray, tangent: np.ndarray, bitangent: np.ndarray):
    """Orthonormal (T, B, N): T from the triangle's tangent made orthogonal
    to N, B = N x T flipped when the UV mapping is mirrored."""
    t = _normalized(tangent - normal * np.einsum("ij,ij->i", tangent, normal)[:, None])
    b = np.cross(normal, t)
    flip = np.einsum("ij,ij->i", b, bitangent) < 0
    b[flip] *= -1.0
    return t, b


def _rasterize(uv: np.ndarray, tri: np.ndarray, size: int, expand: int = 1) -> np.ndarray:
    """(size, size) int32 with the index of the triangle covering each texel
    (image rows, top first) or -1. With `expand`, texels within about that many
    texels outside a triangle also take its index (when no triangle covers
    them), so a GPU that filters bilinearly across a small triangle's edge
    reads that triangle's own extrapolated colour and not a neighbour's. The
    filled interiors are drawn last, so they are never overwritten."""
    from PIL import Image, ImageDraw

    canvas = Image.new("I", (size, size), 0)
    draw = ImageDraw.Draw(canvas)
    x = uv[:, 0] * size
    y = (1.0 - uv[:, 1]) * size
    corners = [[(x[a], y[a]), (x[b], y[b]), (x[c], y[c])] for a, b, c in tri]
    if expand > 0:
        for i, pts in enumerate(corners):
            draw.line(pts + [pts[0]], fill=i + 1, width=2 * expand + 1)
    for i, pts in enumerate(corners):
        draw.polygon(pts, fill=i + 1)
    return np.asarray(canvas, dtype=np.int64).astype(np.int32) - 1


def _sample(image: np.ndarray, uv: np.ndarray) -> np.ndarray:
    """Bilinear samples (n, C) of a top-first image at VTK UV (wrapping)."""
    h, w = image.shape[:2]
    x = (uv[:, 0] % 1.0) * (w - 1)
    y = ((1.0 - uv[:, 1]) % 1.0) * (h - 1)
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    x1, y1 = np.minimum(x0 + 1, w - 1), np.minimum(y0 + 1, h - 1)
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    img = image.astype(float)
    top = img[y0, x0] * (1 - fx) + img[y0, x1] * fx
    bottom = img[y1, x0] * (1 - fx) + img[y1, x1] * fx
    return top * (1 - fy) + bottom * fy


def _uv_pairs(source: pv.PolyData, result: pv.PolyData, n: int, seed: int):
    """`n` points drawn on the source surface (area-weighted) and, for each, the
    UV it has on the source, the UV of the closest point of `result`, and the UV
    of the source where that closest point projects back (the floor from the
    geometry alone, which no UV transfer can improve on)."""
    from remesh_core import _closest_point_barycentric

    s_tri = np.asarray(source.regular_faces)
    s_pts = np.asarray(source.points, dtype=float)
    s_uv = np.asarray(source.active_texture_coordinates, dtype=float)
    a, b, c = (s_pts[s_tri[:, i]] for i in range(3))
    area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
    rng = np.random.default_rng(seed)
    face = rng.choice(len(s_tri), size=n, p=area / area.sum())
    r1, r2 = np.sqrt(rng.random(n)), rng.random(n)
    w = np.column_stack([1 - r1, r1 * (1 - r2), r1 * r2])
    pts = w[:, 0:1] * a[face] + w[:, 1:2] * b[face] + w[:, 2:3] * c[face]
    uv_src = sum(w[:, i : i + 1] * s_uv[s_tri[face, i]] for i in range(3))

    r_tri = np.asarray(result.regular_faces)
    r_pts = np.asarray(result.points, dtype=float)
    r_uv = np.asarray(result.active_texture_coordinates, dtype=float)
    cell, near = result.find_closest_cell(pts, return_closest_point=True)
    cell, near = np.asarray(cell), np.asarray(near)
    ra, rb, rc = (r_pts[r_tri[cell, i]] for i in range(3))
    rw, _ = _closest_point_barycentric(pts, ra, rb, rc)
    uv_res = sum(rw[:, i : i + 1] * r_uv[r_tri[cell, i]] for i in range(3))

    back = np.asarray(source.find_closest_cell(near))
    fa, fb, fc = (s_pts[s_tri[back, i]] for i in range(3))
    fw, _ = _closest_point_barycentric(near, fa, fb, fc)
    uv_back = sum(fw[:, i : i + 1] * s_uv[s_tri[back, i]] for i in range(3))
    return uv_src, uv_res, uv_back


def _summary(err: np.ndarray, floor: np.ndarray) -> dict:
    return {
        "mean": float(err.mean()), "p95": float(np.percentile(err, 95)),
        "max": float(err.max()), "share_above_10": float((err > 10.0).mean()),
        "floor_mean": float(floor.mean()), "floor_p95": float(np.percentile(floor, 95)),
    }


def _colour_error(image: np.ndarray, uv_a: np.ndarray, uv_b: np.ndarray, image_b: Optional[np.ndarray] = None) -> np.ndarray:
    """Colour difference between `image` at `uv_a` and `image_b` (default: `image`) at
    `uv_b`, in percent of the full RGB range (L2 norm over sqrt(3))."""
    diff = _sample(image, uv_a) - _sample(image if image_b is None else image_b, uv_b)
    return 100.0 * np.linalg.norm(diff, axis=1) / (255.0 * np.sqrt(3.0))


def _normal_angle(image: np.ndarray, uv_a: np.ndarray, uv_b: np.ndarray, image_b: Optional[np.ndarray] = None) -> np.ndarray:
    """Angle in degrees between the tangent-space normals that `image` stores at
    `uv_a` and `image_b` (default: `image`) stores at `uv_b`."""
    def decode(img, uv):
        v = _sample(img, uv) / 255.0 * 2.0 - 1.0
        return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)

    cos = np.clip(np.einsum("ij,ij->i", decode(image, uv_a), decode(image if image_b is None else image_b, uv_b)), -1.0, 1.0)
    return np.degrees(np.arccos(cos))


def _channel_error(image: np.ndarray, channel: int, uv_a: np.ndarray, uv_b: np.ndarray, image_b: Optional[np.ndarray] = None) -> np.ndarray:
    """Absolute difference of one channel of `image` at `uv_a` and of `image_b`
    (default: `image`) at `uv_b`, in percent."""
    other = image if image_b is None else image_b
    return 100.0 * np.abs(_sample(image, uv_a)[:, channel] - _sample(other, uv_b)[:, channel]) / 255.0


def map_errors(source: pv.PolyData, result: pv.PolyData, maps: "MaterialMaps", n: int = 20000, seed: int = 0,
               source_maps: Optional["MaterialMaps"] = None) -> dict:
    """Appearance error of every map the material has, measured like
    `texture_error` (same points): `source_maps` (default: `maps`) at the source UV
    against `maps` at the UV of the closest point of `result`. Pass the source's own
    maps as `source_maps` when `maps` are different images (baked for a new atlas).
    The base colour and the emissive map are a colour difference in percent of the
    RGB range, the normal map the angle in degrees between the stored normals,
    roughness (G of the metallic-roughness map), metallic (B) and occlusion (R) an
    absolute difference in percent. Each entry has mean, p95 and the floor from the
    geometry alone, in the same unit."""
    ref = source_maps if source_maps is not None else maps
    uv_src, uv_res, uv_back = _uv_pairs(source, result, n, seed)
    out = {}
    if maps.base is not None and ref.base is not None:
        out["base"] = _summary(_colour_error(ref.base, uv_src, uv_res, maps.base), _colour_error(ref.base, uv_src, uv_back))
    if maps.emissive is not None and ref.emissive is not None:
        out["emissive"] = _summary(_colour_error(ref.emissive, uv_src, uv_res, maps.emissive), _colour_error(ref.emissive, uv_src, uv_back))
    if maps.normal is not None and ref.normal is not None:
        out["normal"] = _summary(_normal_angle(ref.normal, uv_src, uv_res, maps.normal), _normal_angle(ref.normal, uv_src, uv_back))
    if maps.metal_rough is not None and ref.metal_rough is not None:
        out["roughness"] = _summary(_channel_error(ref.metal_rough, 1, uv_src, uv_res, maps.metal_rough), _channel_error(ref.metal_rough, 1, uv_src, uv_back))
        out["metallic"] = _summary(_channel_error(ref.metal_rough, 2, uv_src, uv_res, maps.metal_rough), _channel_error(ref.metal_rough, 2, uv_src, uv_back))
    if maps.occlusion is not None and ref.occlusion is not None:
        out["occlusion"] = _summary(_channel_error(ref.occlusion, 0, uv_src, uv_res, maps.occlusion), _channel_error(ref.occlusion, 0, uv_src, uv_back))
    return out


def _shading_normals(mesh: pv.PolyData, vertex_normals: np.ndarray, face: np.ndarray, bary: np.ndarray,
                     uv: np.ndarray, image: Optional[np.ndarray]) -> np.ndarray:
    """World-space shading normals at points given by (face, barycentric weights)
    of `mesh`: the interpolated vertex normal, tilted by the tangent-space normal
    that `image` stores at `uv` (none: the vertex normal alone), in the frame of
    the face's tangent and bitangent."""
    tri = np.asarray(mesh.regular_faces)
    pts = np.asarray(mesh.points, dtype=float)
    n = _normalized(np.einsum("sk,skd->sd", bary, vertex_normals[tri[face]]))
    if image is None:
        return n
    tangent, bitangent = _triangle_frames(pts, np.asarray(mesh.active_texture_coordinates, dtype=float), tri)
    t, b = _frame(n, tangent[face], bitangent[face])
    v = _sample(image, uv) / 255.0 * 2.0 - 1.0
    v = v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)
    return _normalized(t * v[:, 0:1] + b * v[:, 1:2] + n * v[:, 2:3])


def shading_normal_error(source: pv.PolyData, result: pv.PolyData, source_normal_map: np.ndarray,
                         result_normal_maps: Dict[str, Optional[np.ndarray]], n: int = 20000, seed: int = 0) -> dict:
    """Angle in degrees between the shading normal of the source (its vertex
    normals and its normal map) and that of the result, at `n` area-weighted
    points of the source and the closest point of the result, for each entry of
    `result_normal_maps` (the normal map the result would carry: the source's
    on the transferred UV, a baked one, or None for the vertex normals alone).
    Unlike `map_errors`, this includes the tangent frame of each surface.
    Returns {name: {mean, p95}}."""
    from remesh_core import _closest_point_barycentric

    s_tri = np.asarray(source.regular_faces)
    s_pts = np.asarray(source.points, dtype=float)
    s_uv = np.asarray(source.active_texture_coordinates, dtype=float)
    a, b, c = (s_pts[s_tri[:, i]] for i in range(3))
    area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
    rng = np.random.default_rng(seed)
    face = rng.choice(len(s_tri), size=n, p=area / area.sum())
    r1, r2 = np.sqrt(rng.random(n)), rng.random(n)
    w = np.column_stack([1 - r1, r1 * (1 - r2), r1 * r2])
    pts = w[:, 0:1] * a[face] + w[:, 1:2] * b[face] + w[:, 2:3] * c[face]
    uv_src = np.einsum("sk,skd->sd", w, s_uv[s_tri[face]])
    ns = _shading_normals(source, welded_vertex_normals(source), face, w, uv_src, source_normal_map)

    r_tri = np.asarray(result.regular_faces)
    r_pts = np.asarray(result.points, dtype=float)
    r_uv = np.asarray(result.active_texture_coordinates, dtype=float)
    cell = np.asarray(result.find_closest_cell(pts))
    rw, _ = _closest_point_barycentric(pts, r_pts[r_tri[cell, 0]], r_pts[r_tri[cell, 1]], r_pts[r_tri[cell, 2]])
    uv_res = np.einsum("sk,skd->sd", rw, r_uv[r_tri[cell]])
    r_vn = welded_vertex_normals(result)
    out = {}
    for name, image in result_normal_maps.items():
        nr = _shading_normals(result, r_vn, cell, rw, uv_res, image)
        angle = np.degrees(np.arccos(np.clip(np.einsum("ij,ij->i", ns, nr), -1.0, 1.0)))
        out[name] = {"mean": float(angle.mean()), "p95": float(np.percentile(angle, 95))}
    return out


def default_bake_size(maps: Optional["MaterialMaps"], cap: int = 2048) -> int:
    """Pixels per side for a baked map: that of the model's own normal map, from 512
    to `cap` (a 4096-pixel source map baked at 1024 loses its detail), or 1024
    without one."""
    if maps is None or maps.normal is None:
        return 1024
    return int(min(cap, max(512, maps.normal.shape[0])))


def best_normal_map(high: pv.PolyData, low: pv.PolyData, maps: "MaterialMaps", size: int = 1024):
    """Bakes a normal map for `low` from `high` (and `high`'s own normal map) and
    keeps it only if the shading normals of `low` then differ less from those of
    `high` (mean angle at sampled points, `shading_normal_error`) than with
    `high`'s map carried over on the transferred UV; baking can lose on thin or
    small parts, and the carried map costs nothing. `low` must share `high`'s
    atlas. Returns (baked image or None to keep the carried map, info with both
    mean angles in degrees)."""
    if maps is None or maps.normal is None:
        return None, {}
    scale = maps.factors.get("normalScale") or 1.0
    baked = bake_normal_map(high, low, size=size, high_normal_map=maps.normal, high_normal_scale=scale)
    err = shading_normal_error(high, low, maps.normal, {"carried": maps.normal, "baked": baked})
    info = {"carried_mean_deg": err["carried"]["mean"], "baked_mean_deg": err["baked"]["mean"]}
    return (baked if err["baked"]["mean"] < err["carried"]["mean"] else None), info


def texture_error(source: pv.PolyData, result: pv.PolyData, image: np.ndarray, n: int = 20000, seed: int = 0) -> dict:
    """Appearance error of a textured result: `n` points are drawn on the source
    surface (area-weighted), each is coloured by `image` at its source UV and at
    the UV of the closest point of `result` (the same image on the result's
    transferred UV), and the colour difference is reported as a percentage of
    the full RGB range (L2 norm over sqrt(3)). It captures both the displacement
    of the geometry and the UV transfer (stretched faces near seams). Returns
    mean, p95 and max in percent, the share of points above 10%, and the floor
    from the geometry alone."""
    uv_src, uv_res, uv_back = _uv_pairs(source, result, n, seed)
    return _summary(_colour_error(image, uv_src, uv_res), _colour_error(image, uv_src, uv_back))


def _ray_hits(locator, origins: np.ndarray, directions: np.ndarray, length: float):
    """For each ray (origin, unit direction) the first triangle hit within
    `length`, as (cell ids, hit points, hit mask). Casting from the outside of
    the cage inwards makes the first hit the outermost surface, i.e. the one
    a viewer sees."""
    import vtk

    n = len(origins)
    cells = np.full(n, -1, dtype=np.int64)
    points = np.zeros((n, 3))
    ends = origins + directions * length
    t, sub, cell = vtk.mutable(0.0), vtk.mutable(0), vtk.mutable(0)
    x, pc = [0.0] * 3, [0.0] * 3
    o_list, e_list = origins.tolist(), ends.tolist()
    intersect = locator.IntersectWithLine
    for i in range(n):
        if intersect(o_list[i], e_list[i], 1e-9, t, x, pc, sub, cell):
            cells[i] = int(cell)
            points[i] = x
    return cells, points, cells >= 0


def bake_maps(
    high: pv.PolyData,
    low: pv.PolyData,
    sources: Optional[Dict[str, np.ndarray]] = None,
    size: int = 1024,
    bake_normal: bool = True,
    high_normal_map: Optional[np.ndarray] = None,
    high_normal_scale: float = 1.0,
    progress: Optional[Callable[[float], None]] = None,
    chunk: int = 300_000,
    method: str = "rays",
    cage_pct: float = 3.0,
) -> Dict[str, np.ndarray]:
    """Bakes maps for `low`'s UV layout from `high`: every texel of `low`'s
    layout is located on `low`'s surface and matched to a point of `high`.

    - Each image in `sources` (name -> top-row-first image over `high`'s UV,
      e.g. the base colour) is resampled at the matched point's UV, giving a
      map of the same name over `low`'s layout. This is how a texture is
      moved onto a mesh with a new atlas.
    - With `bake_normal`, a tangent-space normal map ("normal") is added: the
      normal at the matched point (smooth, plus `high_normal_map` if the
      original had one) expressed in `low`'s tangent frame.

    Both meshes need UV. `method="rays"` (default) casts a ray per texel along
    `low`'s smooth normal from a cage `cage_pct` % of the bounding-box
    diagonal above the surface and takes the first triangle of `high` it meets;
    a texel whose ray misses keeps `low`'s own normal (identity) in the normal
    map and the nearest point of `high` for the colour maps.
    `method="nearest"` matches the nearest point instead (no cage to tune). The
    colour maps always use the nearest point. Empty
    texels are filled from the nearest covered one so bilinear filtering does
    not bleed across charts."""
    import vtk

    from remesh_core import _closest_point_barycentric  # same module family; imported lazily

    if low.active_texture_coordinates is None or high.active_texture_coordinates is None:
        raise ValueError("Both meshes need texture coordinates to bake maps.")
    sources = sources or {}
    l_pts = np.asarray(low.points, dtype=float)
    l_tri = np.asarray(low.regular_faces)
    l_uv = np.asarray(low.active_texture_coordinates, dtype=float)
    l_n = welded_vertex_normals(low)
    l_t, l_b = _triangle_frames(l_pts, l_uv, l_tri)

    h_pts = np.asarray(high.points, dtype=float)
    h_tri = np.asarray(high.regular_faces)
    h_uv = np.asarray(high.active_texture_coordinates, dtype=float)
    h_n = welded_vertex_normals(high)
    h_t, h_b = _triangle_frames(h_pts, h_uv, h_tri)
    a, b, c = h_pts[h_tri[:, 0]], h_pts[h_tri[:, 1]], h_pts[h_tri[:, 2]]
    tree = cKDTree((a + b + c) / 3.0)
    locator = None
    if method == "rays":
        locator = vtk.vtkStaticCellLocator()
        locator.SetDataSet(high)
        locator.BuildLocator()
    cage = cage_pct / 100.0 * float(high.length)

    def nearest(p):
        _, cand = tree.query(p, k=min(8, len(h_tri)))
        cand = np.asarray(cand).reshape(len(p), -1)
        best = cand[:, 0].copy()
        best_d = np.full(len(p), np.inf)
        for j in range(cand.shape[1]):
            ht = cand[:, j]
            _, d = _closest_point_barycentric(p, a[ht], b[ht], c[ht])
            better = d < best_d
            best[better], best_d[better] = ht[better], d[better]
        hb, _ = _closest_point_barycentric(p, a[best], b[best], c[best])
        return best, hb

    cover = _rasterize(l_uv, l_tri, size)
    ys, xs = np.nonzero(cover >= 0)
    tid_all = cover[ys, xs]
    outputs: Dict[str, np.ndarray] = {name: np.zeros((size, size, 3), dtype=np.uint8) for name in sources}
    if bake_normal:
        outputs["normal"] = np.zeros((size, size, 3), dtype=np.uint8)
        outputs["normal"][...] = (128, 128, 255)
    total = len(ys)
    for start in range(0, total, chunk):
        sl = slice(start, min(start + chunk, total))
        tid, x, y = tid_all[sl], xs[sl], ys[sl]
        tex_uv = np.column_stack([(x + 0.5) / size, 1.0 - (y + 0.5) / size])
        # barycentric coordinates of the texel centre in its low triangle (clamped)
        i0, i1, i2 = l_tri[tid, 0], l_tri[tid, 1], l_tri[tid, 2]
        u0, u1, u2 = l_uv[i0], l_uv[i1], l_uv[i2]
        det = (u1[:, 0] - u0[:, 0]) * (u2[:, 1] - u0[:, 1]) - (u2[:, 0] - u0[:, 0]) * (u1[:, 1] - u0[:, 1])
        det = np.where(np.abs(det) > 1e-20, det, 1e-20)
        w1 = ((tex_uv[:, 0] - u0[:, 0]) * (u2[:, 1] - u0[:, 1]) - (u2[:, 0] - u0[:, 0]) * (tex_uv[:, 1] - u0[:, 1])) / det
        w2 = ((u1[:, 0] - u0[:, 0]) * (tex_uv[:, 1] - u0[:, 1]) - (tex_uv[:, 0] - u0[:, 0]) * (u1[:, 1] - u0[:, 1])) / det
        w = np.clip(np.column_stack([1.0 - w1 - w2, w1, w2]), 0.0, None)
        w = w / np.maximum(w.sum(axis=1, keepdims=True), 1e-12)
        p = w[:, 0:1] * l_pts[i0] + w[:, 1:2] * l_pts[i1] + w[:, 2:3] * l_pts[i2]
        n_low = _normalized(w[:, 0:1] * l_n[i0] + w[:, 1:2] * l_n[i1] + w[:, 2:3] * l_n[i2])

        if method == "rays":
            # From the cage inwards along -normal: the first hit is the outermost surface.
            best, hit_points, hit = _ray_hits(locator, p + n_low * cage, -n_low, 2.0 * cage)
            best = np.where(hit, best, 0)
            hb, _ = _closest_point_barycentric(np.where(hit[:, None], hit_points, p), a[best], b[best], c[best])
        else:
            best, hb = nearest(p)
            hit = np.ones(len(p), dtype=bool)

        if sources:
            # A colour map resamples the surface where it is: the nearest point of `high`.
            # (Rays are for the normal map, which projects detail: along a cage they also
            # hit other parts close to the surface, such as limbs near a torso, and the
            # colour would come from there.)
            c_best, c_hb = (best, hb) if method != "rays" else nearest(p)
            k0, k1, k2 = h_tri[c_best, 0], h_tri[c_best, 1], h_tri[c_best, 2]
            q_uv = c_hb[:, 0:1] * h_uv[k0] + c_hb[:, 1:2] * h_uv[k1] + c_hb[:, 2:3] * h_uv[k2]
            for name, image in sources.items():
                outputs[name][y, x] = np.clip(_sample(image, q_uv) + 0.5, 0, 255).astype(np.uint8)

        if bake_normal:
            t_low, b_low = _frame(n_low, l_t[tid], l_b[tid])
            j0, j1, j2 = h_tri[best, 0], h_tri[best, 1], h_tri[best, 2]
            n_high = _normalized(hb[:, 0:1] * h_n[j0] + hb[:, 1:2] * h_n[j1] + hb[:, 2:3] * h_n[j2])
            if high_normal_map is not None:
                q_uv = hb[:, 0:1] * h_uv[j0] + hb[:, 1:2] * h_uv[j1] + hb[:, 2:3] * h_uv[j2]
                m = _sample(high_normal_map, q_uv) / 255.0 * 2.0 - 1.0
                m[:, :2] *= high_normal_scale
                t_h, b_h = _frame(n_high, h_t[best], h_b[best])
                n_high = _normalized(t_h * m[:, 0:1] + b_h * m[:, 1:2] + n_high * m[:, 2:3])
            local = _normalized(np.column_stack([
                np.einsum("ij,ij->i", n_high, t_low),
                np.einsum("ij,ij->i", n_high, b_low),
                np.einsum("ij,ij->i", n_high, n_low),
            ]))
            local[~hit] = (0.0, 0.0, 1.0)  # ray missed: keep the low-poly normal
            outputs["normal"][y, x] = np.clip((local * 0.5 + 0.5) * 255.0 + 0.5, 0, 255).astype(np.uint8)
        if progress is not None:
            progress(min(1.0, sl.stop / max(total, 1)))

    empty = cover < 0
    if empty.any() and not empty.all():
        _, (iy, ix) = distance_transform_edt(empty, return_indices=True)
        outputs = {name: image[iy, ix] for name, image in outputs.items()}
    return outputs


def bake_normal_map(
    high: pv.PolyData,
    low: pv.PolyData,
    size: int = 1024,
    high_normal_map: Optional[np.ndarray] = None,
    high_normal_scale: float = 1.0,
    progress: Optional[Callable[[float], None]] = None,
    chunk: int = 300_000,
    method: str = "rays",
    cage_pct: float = 3.0,
) -> np.ndarray:
    """Tangent-space normal map (size x size x 3, uint8, top row first) for
    `low` that reproduces the shading of `high`; see `bake_maps`. `low` is
    expected to share `high`'s atlas (see transfer_texture_coords), so its
    other maps stay valid."""
    if low.active_texture_coordinates is None or high.active_texture_coordinates is None:
        raise ValueError("Both meshes need texture coordinates to bake a normal map.")
    return bake_maps(
        high, low, None, size, True, high_normal_map, high_normal_scale, progress, chunk, method, cage_pct
    )["normal"]


def reparametrize(mesh: pv.PolyData, size: int = 1024, padding: int = 4) -> pv.PolyData:
    """A copy of `mesh` with a new UV atlas from xatlas (a few large charts
    packed without overlap), vertices duplicated along the new seams. Needs the
    optional `xatlas` package. The geometry is unchanged: every face keeps its
    corners' positions."""
    import xatlas

    positions = np.ascontiguousarray(np.asarray(mesh.points), dtype=np.float32)
    faces = np.ascontiguousarray(np.asarray(mesh.regular_faces), dtype=np.uint32)
    atlas = xatlas.Atlas()
    atlas.add_mesh(positions, faces)
    pack = xatlas.PackOptions()
    pack.resolution = size
    pack.padding = padding
    pack.bilinear = True
    atlas.generate(chart_options=xatlas.ChartOptions(), pack_options=pack)
    vmapping, indices, uvs = atlas[0]
    out = pv.PolyData(
        positions[vmapping].astype(float),
        np.hstack([np.full((len(indices), 1), 3), indices]).astype(np.int64),
    )
    out.active_texture_coordinates = np.asarray(uvs, dtype=float)
    return out


def xatlas_available() -> bool:
    try:
        import xatlas  # noqa: F401
    except Exception:
        return False
    return True


def bake_material_maps(
    high: pv.PolyData,
    low: pv.PolyData,
    maps: MaterialMaps,
    size: int = 1024,
    progress: Optional[Callable[[float], None]] = None,
    method: str = "rays",
) -> MaterialMaps:
    """Moves a material onto `low`'s own UV layout (typically from
    `reparametrize`): the colour, metallic-roughness, occlusion and emissive
    maps are resampled from `high`'s, and a new normal map is baked that
    includes `high`'s own normal map. The factors are kept (the normal scale is
    already applied, so it is reset)."""
    sources = {
        name: getattr(maps, name)
        for name in ("base", "metal_rough", "occlusion", "emissive")
        if getattr(maps, name) is not None
    }
    scale = maps.factors.get("normalScale") or 1.0
    baked = bake_maps(
        high, low, sources, size, True, maps.normal, scale, progress, method=method
    )
    factors = dict(maps.factors)
    factors["normalScale"] = None
    return MaterialMaps(
        base=baked.get("base"),
        normal=baked.get("normal"),
        metal_rough=baked.get("metal_rough"),
        occlusion=baked.get("occlusion"),
        emissive=baked.get("emissive"),
        factors=factors,
    )
