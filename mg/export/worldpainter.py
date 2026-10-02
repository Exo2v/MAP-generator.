"""WorldPainter export: the specification's turnkey deliverable (spec §5, §6).

``python generate_ashfall.py --res 2048 --out worldpainter`` produces

* ``ASHFALL_HEIGHTMAP_16BIT.png`` - the uint16 heightfield, ``h = (Y + 64) / 384``,
* ``ASHFALL_POPULATE_MASK.png`` - binary mask for WorldPainter's native Populate layer,
* ``ashenfall_worldpainter_setup.js`` - a JSR-223 script that builds the world from them,

plus the auxiliary masks (scree, frost, slopes, biomes) and a JSON copy of the surface
table so the same materials can be painted by hand.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np

from ..core.bluenoise import BlueNoiseMatrix
from ..core.erosion import slope_map
from ..core.types import BIOMES, BIOME_COLORS
from ..generation.landmarks import ELEVATIONS, SEA_LEVEL, y_to_u16


@dataclass
class WorldPainterResult:
    output_dir: str
    files: List[str] = field(default_factory=list)
    resolution: int = 2048
    seconds: float = 0.0
    diagnostics: Dict[str, float] = field(default_factory=dict)


def _resize(a: np.ndarray, res: int, *, nearest: bool = False) -> np.ndarray:
    from PIL import Image

    src = np.asarray(a, dtype=np.float32)
    if src.shape == (res, res):
        return src
    mode = Image.NEAREST if nearest else Image.BILINEAR
    img = Image.fromarray(src, mode="F").resize((res, res), mode)
    return np.asarray(img, dtype=np.float32)


def ashenfall_masks(terrain, export_cfg: Dict[str, Any], *, seed: int = 0
                    ) -> Dict[str, np.ndarray]:
    """Every mask the specification asks for, at simulation resolution.

    ``POPULATE`` follows spec §6 exactly: full canopy below 25 degrees of slope, 40 %
    density through the 25-35 degree band, nothing on scree, nothing above the treeline,
    nothing over water, and nothing inside a volcanic barrenness mask.
    """
    heights = np.asarray(terrain.heights, dtype=np.float64)
    region = terrain.region
    cs = float(region.cell_size)
    sea = float(export_cfg.get("sea_level", SEA_LEVEL))
    treeline = float(export_cfg.get("treeline", 225.0))
    snowline = float(export_cfg.get("snowline", 200.0))
    water = np.asarray(terrain.water_mask) > 0
    slope = np.degrees(np.arctan(slope_map(heights, cs)))

    masks = (terrain.meta or {}).get("masks") or {}
    bare = np.asarray(masks.get("ashenfall_bare", np.zeros_like(heights)), dtype=np.float64)

    bn = BlueNoiseMatrix(int(export_cfg.get("populate_noise_size", 64)), seed=int(seed))
    noise = bn.tiled(heights.shape, offset=(region.z0, region.x0))

    flat = slope < 25.0
    mid = (slope >= 25.0) & (slope <= 35.0)
    canopy = (flat | (mid & (noise < 0.40))) & (bare < 0.5)
    canopy &= ~water & (heights > sea + 1.0) & (heights <= treeline)

    scree = np.clip((slope - 25.0) / 20.0, 0.0, 1.0) * (~water)
    frost = np.clip((heights - snowline) / 40.0, 0.0, 1.0) * (~water)
    return {
        "populate": bn.mask8(canopy.astype(np.float64), offset=(region.z0, region.x0)),
        "scree": (scree * 255.0).astype(np.uint8),
        "frost": (frost * 255.0).astype(np.uint8),
        "slope": np.clip(slope / 60.0, 0.0, 1.0),
        "water": water.astype(np.uint8) * 255,
    }


def biome_map(terrain) -> np.ndarray:
    """RGB raster of the biome field, for painting WorldPainter biome layers."""
    biome = np.asarray(terrain.biome).astype(np.int64)
    out = np.zeros(biome.shape + (3,), dtype=np.uint8)
    for i, name in enumerate(BIOMES):
        if i >= 256:
            break
        rgb = BIOME_COLORS.get(name, (128, 128, 128))
        out[biome == i] = rgb
    return out


def worldpainter_script(res: int, world_name: str = "Ashenfall",
                        heightmap: str = "ASHFALL_HEIGHTMAP_16BIT.png",
                        populate: str = "ASHFALL_POPULATE_MASK.png",
                        biome_png: str = "ASHFALL_BIOME_MAP.png") -> str:
    """A JSR-223 script for WorldPainter (``wpscript ashenfall_worldpainter_setup.js``)."""
    return f'''// Ashenfall - WorldPainter setup script (JSR-223)
//
//   Heightmap : {heightmap}  ({res} x {res}, 16-bit, h = (Y + 64) / 384)
//   Populate  : {populate}
//   Biomes    : {biome_png}
//
// Run from this folder with:  wpscript ashenfall_worldpainter_setup.js
// (WorldPainter -> Tools -> Run script... does the same thing.)
//
// The world is built to the specification's canvas: Minecraft 1.21.1, Y -64 .. 320,
// sea level Y = 62.  Chunks are NOT pre-decorated; the Populate layer is what tells the
// game (and with it Still Life) to run its own placed features.

var WORLD_NAME = '{world_name}';
var MIN_Y = -64, MAX_Y = 320, SEA_LEVEL = 62;

// ---- 1. the heightfield ------------------------------------------------------------
// A 16-bit height map comes in as levels 0..65535; map them onto the extended world
// height range so the abyssal trench and the glacial spine land on their exact Y.
var heightMap = wp.getHeightMap().fromFile('{heightmap}').go();
var world = wp.createWorld()
        .fromHeightMap(heightMap)
        .fromLevels(0, 65535)
        .toLevels(MIN_Y, MAX_Y)
        .go();

// ---- 2. terrain materials by elevation (spec section 6) -----------------------------
// Terrain type indices are WorldPainter's own; see
// https://www.worldpainter.net/trac/wiki/Scripting/TerrainTypeValues
wp.applyHeightMap(heightMap)
        .toWorld(world)
        .applyToTerrain()
        .fromLevels(0, 13631).toTerrain(36)    // abyssal trench  -> Sandstone/beach
        .fromLevels(13632, 21503).toTerrain(39) // ocean floor     -> Gravel
        .fromLevels(21504, 22251).toTerrain(36) // shoreline       -> Beaches
        .fromLevels(22252, 32511).toTerrain(0)  // lowland         -> Grass
        .fromLevels(32512, 40703).toTerrain(3)  // highland        -> Permadirt
        .fromLevels(40704, 47359).toTerrain(29) // alpine          -> Rock
        .fromLevels(47360, 65535).toTerrain(24) // snow line       -> Snow
        .go();

// ---- 3. the Still Life populate mask (spec section 5, method 1) ---------------------
// Black = leave the chunk undecorated, white = let the game's decorators run.
try {{
    var mask = wp.getHeightMap().fromFile('{populate}').go();
    var populate = wp.getLayer().withName('Populate').go();
    wp.applyHeightMap(mask)
            .toWorld(world)
            .applyToLayer(populate)
            .fromLevel(0).toLevel(0)
            .fromLevels(1, 255).toLevel(1)
            .go();
    wp.console.print('Ashenfall: populate layer applied from {populate}');
}} catch (err) {{
    wp.console.print('Ashenfall: could not apply the populate layer automatically (' +
                     err + '). Open the Populate layer and paint from {populate} instead - ' +
                     'white cells are the ones Still Life may decorate.');
}}

// ---- 4. frost above the treeline ----------------------------------------------------
try {{
    var frost = wp.getLayer().withName('Frost').go();
    wp.applyHeightMap(heightMap)
            .toWorld(world)
            .applyToLayer(frost)
            .fromLevels(0, 47359).toLevel(0)
            .fromLevels(47360, 65535).toLevel(1)
            .go();
}} catch (err) {{
    wp.console.print('Ashenfall: frost layer skipped (' + err + ')');
}}

// ---- 5. save ------------------------------------------------------------------------
wp.saveWorld(world).toFile(WORLD_NAME + '.world').go();
wp.console.print('Ashenfall: world written to ' + WORLD_NAME + '.world');
'''


def export_worldpainter(terrain, cfg: Dict[str, Any], out_dir: str, *,
                        res: int = 2048, seed: int = 0,
                        progress=None) -> WorldPainterResult:
    """Write the WorldPainter deliverable into ``out_dir``."""
    from PIL import Image

    t0 = time.time()
    os.makedirs(out_dir, exist_ok=True)
    export_cfg = dict((cfg or {}).get("export") or {})
    export_cfg.setdefault("sea_level", SEA_LEVEL)
    files: List[str] = []

    def tick(f: float, label: str):
        if progress:
            progress(f, label)

    # ---- heightmap -------------------------------------------------------------------
    heights = _resize(np.asarray(terrain.heights, dtype=np.float32), res)
    u16 = y_to_u16(heights)
    path = os.path.join(out_dir, "ASHFALL_HEIGHTMAP_16BIT.png")
    Image.fromarray(u16).save(path)
    files.append(path)
    tick(0.4, "heightmap")

    # ---- masks -----------------------------------------------------------------------
    masks = ashenfall_masks(terrain, export_cfg, seed=seed)
    named = {
        "populate": "ASHFALL_POPULATE_MASK.png",
        "scree": "ASHFALL_SCREE_MASK.png",
        "frost": "ASHFALL_FROST_MASK.png",
        "water": "ASHFALL_WATER_MASK.png",
        "slope": "ASHFALL_SLOPE_MASK.png",
    }
    for key, filename in named.items():
        layer = _resize(masks[key], res, nearest=True)
        img = Image.fromarray(layer.astype(np.uint8))
        p = os.path.join(out_dir, filename)
        img.save(p)
        files.append(p)
    tick(0.7, "masks")

    # ---- biome map -------------------------------------------------------------------
    biomes = biome_map(terrain)
    img = Image.fromarray(
        np.asarray(Image.fromarray(biomes).resize((res, res), Image.NEAREST))
    )
    p = os.path.join(out_dir, "ASHFALL_BIOME_MAP.png")
    img.save(p)
    files.append(p)

    # ---- the script + the surface table ----------------------------------------------
    script = worldpainter_script(res, world_name=str(export_cfg.get("world_name")
                                                      or "Ashenfall"))
    p = os.path.join(out_dir, "ashenfall_worldpainter_setup.js")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(script)
    files.append(p)

    table = {
        "canvas": {"width": 8000, "depth": 8000, "min_y": -64, "max_y": 320,
                   "sea_level": int(SEA_LEVEL), "resolution": res},
        "normalisation": "uint16 = round((Y + 64) / 384 * 65535)",
        "elevations": {k: v for k, v in ELEVATIONS.items()},
        "surface_rule": {
            "0-25deg": "grass_block / deep loam, 100 % population",
            "25-35deg": "coarse_dirt / podzol, 40 % population",
            "35-45deg": "cobblestone / scree / gravel, 5 % population",
            ">45deg": "granite / basalt bedrock, 0 % population",
            ">225": "snow_block / packed_ice / calcite, treeline",
            "caldera": "blackstone / basalt / magma_block / obsidian, barren",
        },
        "masks": {k: os.path.basename(v) for k, v in named.items()
                  if k in ("populate", "scree", "frost")},
        "worldpainter": {"script": "ashenfall_worldpainter_setup.js",
                         "populate_layer": "Populate", "frost_layer": "Frost"},
    }
    p = os.path.join(out_dir, "ASHFALL_SURFACE_TABLE.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(table, fh, indent=2)
    files.append(p)

    # convenience aliases: the specification writes ASHFALL_*, the world is Ashenfall
    for src in list(files):
        base = os.path.basename(src)
        if base.startswith("ASHFALL_"):
            alias = os.path.join(out_dir, "ASHENFALL_" + base[len("ASHFALL_"):])
            with open(src, "rb") as fh_in, open(alias, "wb") as fh_out:
                fh_out.write(fh_in.read())
            files.append(alias)

    covered = float(np.count_nonzero(masks["populate"])) / max(masks["populate"].size, 1)
    tick(1.0, "worldpainter export")
    return WorldPainterResult(
        output_dir=out_dir,
        files=files,
        resolution=res,
        seconds=time.time() - t0,
        diagnostics={"populate_fraction": covered,
                     "height_max": float(heights.max()),
                     "height_min": float(heights.min())},
    )
