"""Payload builders for the web UI: 3D meshes, 2D map rasters and inspector data."""

from __future__ import annotations

import base64
import io
import os
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from ..core.erosion import hillshade, slope_map
from ..core.types import BIOMES, BIOME_COLORS, SEA_LEVEL, biome_color
from ..generation.biomes import biome_histogram


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


def _b64(arr: np.ndarray, dtype: str) -> str:
    return base64.b64encode(np.ascontiguousarray(arr.astype(dtype)).tobytes()).decode("ascii")


def _downsample(field: np.ndarray, size: int) -> np.ndarray:
    """Nearest-neighbour-ish block-average downsample of a cell raster to ``size``."""
    h, w = field.shape
    if max(h, w) <= size:
        return field
    step = max(1, int(np.ceil(max(h, w) / size)))
    hh = (h // step) * step
    ww = (w // step) * step
    trimmed = field[:hh, :ww]
    if field.dtype.kind == "f":
        return trimmed.reshape(hh // step, step, ww // step, step).mean(axis=(1, 3))
    # integers: majority would be nicer but mode is slow; use the top-left sample
    return trimmed[::step, ::step]


def _exaggerate_heights(heights: np.ndarray, sea_level: float, exaggeration: float) -> np.ndarray:
    """Scale relief around sea level (1.0 = true scale)."""
    return sea_level + (heights - sea_level) * float(exaggeration)


# --------------------------------------------------------------------------------------
# 3D mesh
# --------------------------------------------------------------------------------------


def build_mesh(
    terrain,
    *,
    size: int = 192,
    exaggeration: float = 1.0,
    show_water: bool = True,
    show_biomes: bool = True,
    max_roads: int = 400,
    max_rivers: int = 400,
) -> Dict[str, Any]:
    """Build a compact, render-ready mesh payload for the browser viewport."""
    region = terrain.region
    cs = float(region.cell_size)
    heights = np.asarray(terrain.heights, dtype=np.float32)
    heights = _downsample(heights, size)
    h, w = heights.shape

    # world-space X/Z for the (downsampled) grid
    step_x = (region.blocks_x / w)
    step_z = (region.blocks_z / h)
    xs = region.x0 + (np.arange(w, dtype=np.float64) + 0.5) * step_x
    zs = region.z0 + (np.arange(h, dtype=np.float64) + 0.5) * step_z
    X, Z = np.meshgrid(xs, zs)

    sea = float(terrain.meta.get("sea_level", SEA_LEVEL))
    Y = _exaggerate_heights(heights.astype(np.float64), sea, exaggeration)

    # ---- vertex colours ---------------------------------------------------------------
    biome = _downsample(terrain.biome.astype(np.int16), size).astype(np.int16)
    palette = np.zeros((len(BIOMES), 3), dtype=np.float64)
    for i, name in enumerate(BIOMES):
        palette[i] = BIOME_COLORS.get(name, (128, 128, 128))
    if show_biomes:
        colors = palette[np.clip(biome, 0, len(BIOMES) - 1)]
    else:
        norm = np.clip((heights - heights.min()) / max(heights.max() - heights.min(), 1e-6), 0, 1)
        colors = np.stack([norm, norm, norm], axis=-1) * 200 + 40

    shade = hillshade(heights.astype(np.float64), cell_size=step_x, z_factor=1.2)
    shade = 0.55 + 0.75 * shade
    colors = np.clip(colors * shade[..., None], 0, 255)

    vertices = np.stack([X.ravel(), Y.ravel(), Z.ravel()], axis=1).astype(np.float32)
    color_bytes = colors.reshape(-1, 3).astype(np.uint8)

    # ---- indices (two triangles per quad) ----------------------------------------------
    idx = np.arange(h * w, dtype=np.uint32).reshape(h, w)
    a = idx[:-1, :-1].ravel()
    b = idx[:-1, 1:].ravel()
    c = idx[1:, :-1].ravel()
    d = idx[1:, 1:].ravel()
    indices = np.empty((a.size * 6,), dtype=np.uint32)
    indices[0::6] = a
    indices[1::6] = c
    indices[2::6] = b
    indices[3::6] = b
    indices[4::6] = c
    indices[5::6] = d

    payload: Dict[str, Any] = {
        "vertices": _b64(vertices, "float32"),
        "colors": _b64(color_bytes, "uint8"),
        "indices": _b64(indices, "uint32"),
        "count": int(h * w),
        "triangles": int(indices.size // 3),
        "bounds": {
            "x0": float(region.x0), "z0": float(region.z0),
            "x1": float(region.x1), "z1": float(region.z1),
            "min_y": float(np.min(Y)), "max_y": float(np.max(Y)),
        },
        "sea_level": sea,
        "exaggeration": float(exaggeration),
        "cell_size": cs,
    }

    # ---- water surface -----------------------------------------------------------------
    if show_water:
        water_level = np.asarray(terrain.water_level, dtype=np.float32)
        water_mask = np.asarray(terrain.water_mask)
        wl = _downsample(water_level, size)
        wm = _downsample(water_mask, size)
        sea_mask = wm > 0
        deep = wm == 3
        wy = np.where(sea_mask, np.where(np.isfinite(wl) & (wl > -1e8), wl, sea), sea)
        wy = _exaggerate_heights(wy.astype(np.float64), sea, exaggeration)
        water_colors = np.zeros((h * w, 3), dtype=np.uint8)
        water_colors[:] = (44, 96, 176)
        water_colors[deep.ravel()] = (24, 62, 120)
        water_colors[(wm == 1).ravel()] = (62, 138, 200)
        water_colors[(wm == 2).ravel()] = (52, 124, 190)
        water_colors[(wm == 4).ravel()] = (208, 236, 255)
        payload["water"] = {
            "vertices": _b64(
                np.stack([X.ravel(), wy.ravel(), Z.ravel()], axis=1).astype(np.float32), "float32"
            ),
            "colors": _b64(water_colors, "uint8"),
            "indices": payload["indices"],
            "mask": _b64(sea_mask.astype(np.uint8), "uint8"),
        }

    # ---- rivers as polylines -------------------------------------------------------------
    rivers = []
    for river in terrain.rivers[:max_rivers]:
        pts = np.asarray(river.points, dtype=np.float64)
        if len(pts) < 2:
            continue
        ys = _exaggerate_heights(pts[:, 2], sea, exaggeration)
        rivers.append({
            "pts": _b64(np.stack([pts[:, 0], ys, pts[:, 1]], axis=1).astype(np.float32), "float32"),
            "order": int(river.order),
            "ends_in": river.ends_in,
        })
    payload["rivers"] = rivers

    # ---- roads ----------------------------------------------------------------------------
    roads = []
    for road in terrain.roads[:max_roads]:
        pts = np.asarray(road, dtype=np.float64)
        if len(pts) < 2:
            continue
        # lift the line to the ground
        px = np.clip(((pts[:, 0] - region.x0) / cs).astype(int), 0, terrain.cells_x - 1)
        pz = np.clip(((pts[:, 1] - region.z0) / cs).astype(int), 0, terrain.cells_z - 1)
        gy = terrain.heights[pz, px].astype(np.float64)
        gy = _exaggerate_heights(gy, sea, exaggeration) + 2.0
        roads.append(_b64(np.stack([pts[:, 0], gy, pts[:, 1]], axis=1).astype(np.float32), "float32"))
    payload["roads"] = roads

    # ---- POIs ------------------------------------------------------------------------------
    payload["pois"] = [
        {
            "x": float(p.x), "z": float(p.z),
            "y": float(_exaggerate_heights(np.array([p.y]), sea, exaggeration)[0]) + 4.0,
            "kind": p.kind, "name": p.name, "biome": p.biome,
        }
        for p in terrain.pois
    ]
    payload["stats"] = terrain.stats()
    payload["biome_histogram"] = biome_histogram(terrain.biome)

    # ---- scalar fields, quantised to one byte per vertex -------------------------------
    # Sending these lets the UI switch colour modes (height / temperature / population /
    # discharge ...) instantly without another round trip, for ~6% of the payload cost.
    fields: Dict[str, Any] = {}
    ranges: Dict[str, Tuple[float, float]] = {}

    def add_field(name: str, field: np.ndarray, *, transform=None) -> None:
        arr = _downsample(np.asarray(field, dtype=np.float64), size)
        if transform is not None:
            arr = transform(arr)
        finite = arr[np.isfinite(arr)]
        if finite.size == 0:
            lo, hi = 0.0, 1.0
        else:
            lo, hi = float(np.min(finite)), float(np.max(finite))
        if hi - lo < 1e-9:
            hi = lo + 1.0
        q = np.clip((np.nan_to_num(arr, nan=lo) - lo) / (hi - lo), 0.0, 1.0)
        fields[name] = _b64((q * 255.0).astype(np.uint8), "uint8")
        ranges[name] = (lo, hi)

    add_field("height", heights)
    add_field("temperature", terrain.temperature)
    add_field("humidity", terrain.humidity)
    add_field("population", terrain.population)
    add_field("fertility", terrain.fertility)
    add_field("discharge", terrain.discharge, transform=np.log1p)
    add_field("continentality", terrain.continentality)
    add_field("soil", terrain.soil_depth)
    slope = slope_map(heights.astype(np.float64), float(region.cell_size))
    add_field("slope", slope)
    wm = _downsample(np.asarray(terrain.water_mask), size).astype(np.uint8)
    fields["water_mask"] = _b64(wm, "uint8")
    ranges["water_mask"] = (0.0, 4.0)
    payload["fields"] = fields
    payload["field_ranges"] = {k: list(v) for k, v in ranges.items()}
    return payload


# --------------------------------------------------------------------------------------
# 2D maps
# --------------------------------------------------------------------------------------


def _hypsometric(n: np.ndarray) -> np.ndarray:
    """Classic elevation ramp: deep sea -> shelf -> beach -> green -> rock -> snow."""
    stops = [
        (0.00, (12, 38, 88)),
        (0.16, (32, 86, 150)),
        (0.21, (58, 122, 175)),
        (0.235, (226, 214, 170)),   # beach line
        (0.30, (104, 166, 84)),
        (0.46, (140, 172, 96)),
        (0.58, (172, 150, 96)),
        (0.70, (140, 120, 92)),
        (0.80, (128, 128, 132)),
        (0.89, (196, 198, 205)),
        (0.96, (244, 246, 250)),
        (1.00, (255, 255, 255)),
    ]
    pos = np.array([p for p, _ in stops])
    cols = np.array([c for _, c in stops], dtype=np.float64)
    out = np.empty((*n.shape, 3), dtype=np.float64)
    for c in range(3):
        out[..., c] = np.interp(n, pos, cols[:, c])
    return out


def map_raster(terrain, kind: str, *, size: int = 1024) -> np.ndarray:
    """Render one of the named rasters as a ``uint8`` RGB image."""
    heights = np.asarray(terrain.heights, dtype=np.float64)
    h, w = heights.shape
    kind = (kind or "height").lower()

    def norm(a: np.ndarray) -> np.ndarray:
        lo, hi = float(np.nanmin(a)), float(np.nanmax(a))
        if hi - lo < 1e-9:
            return np.zeros_like(a)
        return (a - lo) / (hi - lo)

    if kind in ("height", "elevation"):
        n = norm(heights)
        shade = 0.72 + 0.42 * hillshade(heights, cell_size=float(terrain.region.cell_size))
        rgb = _hypsometric(n) * shade[..., None]
    elif kind == "hillshade":
        sh = hillshade(heights, cell_size=float(terrain.region.cell_size), z_factor=1.3)
        sh = np.clip(0.22 + 0.85 * sh, 0.0, 1.0) ** 0.9   # no crushed blacks
        rgb = np.repeat((sh * 255)[..., None], 3, axis=-1)
    elif kind == "slope":
        s = norm(slope_map(heights, float(terrain.region.cell_size)))
        rgb = np.stack([s * 255, (1 - s) * 160, (1 - s) * 90], axis=-1)
    elif kind in ("biome", "biomes"):
        palette = np.zeros((len(BIOMES), 3), dtype=np.float64)
        for i, name in enumerate(BIOMES):
            palette[i] = BIOME_COLORS.get(name, (128, 128, 128))
        rgb = palette[np.clip(terrain.biome, 0, len(BIOMES) - 1)]
        rgb = rgb * (0.74 + 0.42 * hillshade(heights, cell_size=float(terrain.region.cell_size)))[..., None]
    elif kind in ("water", "hydrology"):
        mask = terrain.water_mask
        rgb = np.zeros((*mask.shape, 3), dtype=np.float64)
        rgb[:] = np.array([96, 112, 96])
        rgb[mask == 1] = (70, 150, 215)
        rgb[mask == 2] = (56, 130, 200)
        rgb[mask == 3] = (28, 70, 132)
        rgb[mask == 4] = (225, 245, 255)
    elif kind == "flow":
        flow = np.asarray(terrain.flow_dir, dtype=np.float64)
        mask = np.asarray(terrain.water_mask)
        angle = np.arctan2(flow[..., 1], flow[..., 0])
        rgb = np.zeros((*angle.shape, 3), dtype=np.float64)
        rgb[..., 0] = (np.sin(angle) * 0.5 + 0.5) * 255
        rgb[..., 1] = (np.cos(angle) * 0.5 + 0.5) * 255
        rgb[..., 2] = np.clip(np.log1p(np.asarray(terrain.discharge)) / 12.0, 0, 1) * 255
        rgb[mask == 3] *= 0.35
        sh = hillshade(heights, cell_size=float(terrain.region.cell_size))
        rgb = rgb * (0.5 + 0.7 * sh)[..., None]
    elif kind in ("discharge", "rivers"):
        d = np.log1p(np.asarray(terrain.discharge, dtype=np.float64))
        n = norm(d)
        rgb = np.stack([n ** 0.6 * 200 + 20, n * 240, n ** 2 * 255], axis=-1)
        rgb = rgb * (0.55 + 0.6 * hillshade(heights, cell_size=float(terrain.region.cell_size)))[..., None]
    elif kind in ("population", "vegetation"):
        p = np.asarray(terrain.population, dtype=np.float64)
        rgb = np.stack([p * 60 + 30, p * 220 + 20, p * 90 + 30], axis=-1)
        rgb = rgb * (0.6 + 0.6 * hillshade(heights, cell_size=float(terrain.region.cell_size)))[..., None]
    elif kind == "fertility":
        f = np.asarray(terrain.fertility, dtype=np.float64)
        rgb = np.stack([f * 120 + 40, f * 210 + 30, f * 80 + 30], axis=-1)
    elif kind in ("temperature", "temp"):
        t = np.asarray(terrain.temperature, dtype=np.float64)
        rgb = np.stack([t * 255, (1 - np.abs(t - 0.5) * 1.6) * 230, (1 - t) * 255], axis=-1)
    elif kind == "humidity":
        hm = np.asarray(terrain.humidity, dtype=np.float64)
        rgb = np.stack([(1 - hm) * 210 + 20, (1 - np.abs(hm - 0.55) * 1.5) * 200 + 20, hm * 220 + 30], axis=-1)
    elif kind == "climate":
        from ..generation.climate import climate_color

        rgb = climate_color(terrain.temperature, terrain.humidity).astype(np.float64)
        rgb = rgb * (0.7 + 0.5 * hillshade(heights, cell_size=float(terrain.region.cell_size)))[..., None] / 255 * 255
    elif kind in ("soil", "soil_depth"):
        s = norm(np.asarray(terrain.soil_depth, dtype=np.float64))
        rgb = np.stack([s * 150 + 60, s * 120 + 60, s * 80 + 50], axis=-1)
    elif kind == "continentality":
        c = np.asarray(terrain.continentality, dtype=np.float64)
        rgb = np.stack([c * 240 + 15, c * 200 + 40, (1 - c) * 220 + 30], axis=-1)
    else:
        raise ValueError(f"unknown map kind {kind!r}")

    from PIL import Image

    img = np.clip(rgb, 0, 255).astype(np.uint8)
    if max(img.shape[:2]) > size:
        im = Image.fromarray(img)
        scale = size / max(img.shape[:2])
        im = im.resize((max(1, int(img.shape[1] * scale)), max(1, int(img.shape[0] * scale))),
                       Image.LANCZOS)
        img = np.asarray(im)
    return img


def write_map_bundle(terrain, world_dir: str, config: Dict[str, Any]) -> List[str]:
    """Write every requested raster into ``<world>/maps`` and return their paths."""
    from PIL import Image

    export_cfg = (config or {}).get("export") or {}
    kinds = export_cfg.get("bundle_maps") or ["height", "biome", "climate", "population",
                                              "flow", "water", "soil"]
    maps_dir = os.path.join(world_dir, "maps")
    os.makedirs(maps_dir, exist_ok=True)
    written: List[str] = []
    for kind in kinds:
        try:
            img = map_raster(terrain, kind, size=2048 if kind in ("height", "biome") else 1024)
        except Exception:
            continue
        path = os.path.join(maps_dir, f"{kind}.png")
        Image.fromarray(img).save(path)
        written.append(path)
    # legend + stats
    import json

    with open(os.path.join(maps_dir, "legend.json"), "w", encoding="utf-8") as fh:
        json.dump(
            {
                "biomes": [{"name": n, "color": list(BIOME_COLORS.get(n, (128, 128, 128)))}
                           for n in BIOMES],
                "histogram": biome_histogram(terrain.biome),
                "stats": terrain.stats(),
                "sea_level": terrain.meta.get("sea_level", SEA_LEVEL),
                "cell_size": terrain.region.cell_size,
                "origin": [terrain.region.x0, terrain.region.z0],
            },
            fh,
            indent=2,
        )
        written.append(os.path.join(maps_dir, "legend.json"))
    return written


def png_bytes(img: np.ndarray) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format="PNG", optimize=False)
    return buf.getvalue()


# --------------------------------------------------------------------------------------
# Inspector
# --------------------------------------------------------------------------------------


def column_info(terrain, x: float, z: float) -> Dict[str, Any]:
    """Everything the UI needs to describe the cell under the cursor."""
    region = terrain.region
    cs = float(region.cell_size)
    cx = int(np.clip((x - region.x0) / cs, 0, terrain.cells_x - 1))
    cz = int(np.clip((z - region.z0) / cs, 0, terrain.cells_z - 1))
    idx = int(terrain.biome[cz, cx])
    from ..core.materials import profile_for

    prof = profile_for(
        idx,
        temperature=float(terrain.temperature[cz, cx]),
        humidity=float(terrain.humidity[cz, cx]),
    )
    wet = int(terrain.water_mask[cz, cx])
    return {
        "cell": [cx, cz],
        "world": [x, z],
        "height": float(terrain.heights[cz, cx]),
        "surface_y": int(round(float(terrain.heights[cz, cx]))),
        "water_level": None if wet == 0 else float(terrain.water_level[cz, cx]),
        "water_kind": {0: "dry", 1: "river", 2: "lake", 3: "ocean", 4: "waterfall"}[wet],
        "biome": BIOMES[idx] if 0 <= idx < len(BIOMES) else "unknown",
        "biome_color": list(biome_color(idx)),
        "temperature": float(terrain.temperature[cz, cx]),
        "humidity": float(terrain.humidity[cz, cx]),
        "continentality": float(terrain.continentality[cz, cx]),
        "fertility": float(terrain.fertility[cz, cx]),
        "population": float(terrain.population[cz, cx]),
        "discharge": float(terrain.discharge[cz, cx]),
        "flow": [
            float(terrain.flow_dir[cz, cx, 0]),
            float(terrain.flow_dir[cz, cx, 1]),
        ],
        "soil_depth": float(terrain.soil_depth[cz, cx]),
        "profile": {
            "top": prof.top,
            "filler": prof.filler,
            "stone": prof.stone,
            "deep_stone": prof.deep_stone,
            "ground_cover": list(prof.ground_cover),
        },
    }
