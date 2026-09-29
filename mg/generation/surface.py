"""Block-level surface generation - the World Painter half of the app.

Given the cell-resolution fields produced by the terrain / water / biome / population
stages, this module materialises actual Minecraft blocks for any chunk in the region.

Resolution strategy
-------------------
Generating an 8 km x 8 km world column by column in Python is hopeless, so:

* the **macro shape** comes from the interpolated cell grid (cheap, already cached),
* the **block-level detail** comes from a handful of 2D and cheap pseudo-3D noise
  functions evaluated straight on the block lattice,
* everything is **vectorised over the 256 columns of one chunk at a time**, so a chunk
  costs a few milliseconds instead of a few seconds.

The result is that the same preset gives you a World-Painter-quality world at full
1-block resolution and still exports a 4 km x 4 km region in a reasonable time.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from ..core.materials import (
    AIR,
    BLOCK_ID,
    ID_BLOCK,
    block_id,
    ore_block,
    profile_for,
)
from ..core.noise import fbm, hash2, hash3, value_noise
from ..core.types import BIOMES, BIOME_INDEX, CHUNK, MAX_Y, MIN_Y, SEA_LEVEL, TerrainGrid

WORLD_HEIGHT = MAX_Y - MIN_Y  # 384


@dataclass
class ChunkBlocks:
    """A generated chunk: blocks plus the metadata the exporter needs."""

    cx: int
    cz: int
    blocks: np.ndarray  # (WORLD_HEIGHT, 16, 16) uint16, index 0 = y = MIN_Y
    biomes: np.ndarray  # (16, 16) uint8
    surface_y: np.ndarray  # (16, 16) int16
    water_y: np.ndarray  # (16, 16) int16  (-1 = dry)

    @property
    def nonempty(self) -> bool:
        return bool(np.any(self.blocks))


DEFAULT_SURFACE = {
    "detail_amplitude": 1.35,
    "detail_scale": 42.0,
    "micro_scale": 11.0,
    "micro_amplitude": 0.45,
    "rock_patch_scale": 60.0,
    "snow_line_softness": 6.0,
    "beach_sand_depth": 3.0,
    "caves": 0.55,
    "cave_scale": 78.0,
    "cave_threshold": 0.72,
    "cave_min_depth": 6.0,
    "cave_max_y": 90.0,
    "ravines": 0.18,
    "ravine_scale": 260.0,
    "water_depth": 1.0,
    "ice_on_water": True,
    "deepslate_start": 8,
    "vegetation_in_export": 1.0,
    "structures_in_export": 1.0,
}


class TerrainSampler:
    """Interpolates the cell-resolution fields onto the block lattice."""

    def __init__(self, terrain: TerrainGrid, *, seed: int, cfg: Optional[Dict] = None):
        self.t = terrain
        self.region = terrain.region
        self.seed = int(seed)
        self.cfg = {**DEFAULT_SURFACE, **((cfg or {}).get("surface") or {})}
        self.cs = float(terrain.region.cell_size)
        # cell-field cache: bilinear lookups are done per chunk, so precompute nothing
        self._chunk_cache: Dict[Tuple[int, int], dict] = {}
        # Roads and POIs are indexed per chunk once, so the per-chunk decorate pass does
        # not have to walk every road and settlement in the whole region.
        self._roads_by_chunk: Dict[Tuple[int, int], List[np.ndarray]] = {}
        self._pois_by_chunk: Dict[Tuple[int, int], List] = {}
        self._index_roads_and_pois()
        self._veins_by_chunk: Dict[Tuple[int, int], List] = {}
        self._index_veins()

    # ----------------------------------------------------------------------------------
    def _index_roads_and_pois(self) -> None:
        t = self.t
        region = self.region
        cs = self.cs
        roads = getattr(t, "roads", None) or []
        for road in roads:
            pts = np.asarray(road, dtype=np.float64)
            if len(pts) < 2:
                continue
            # densify once, then bucket the samples by chunk
            d = np.hypot(np.diff(pts[:, 0]), np.diff(pts[:, 1]))
            n = max(2, int(d.sum() / max(cs * 0.5, 1.0)))
            param = np.linspace(0, len(pts) - 1, n)
            px = np.interp(param, np.arange(len(pts)), pts[:, 0])
            pz = np.interp(param, np.arange(len(pts)), pts[:, 1])
            cx = np.floor((px - region.x0) / CHUNK).astype(np.int64)
            cz = np.floor((pz - region.z0) / CHUNK).astype(np.int64)
            keep = (cx >= 0) & (cz >= 0) & (cx < region.blocks_x // CHUNK) & (
                cz < region.blocks_z // CHUNK
            )
            for k in np.unique(cz[keep] * 4096 + cx[keep]):
                key = (int(k % 4096), int(k // 4096))
                arr = np.stack([px[keep], pz[keep]], axis=1)
                self._roads_by_chunk.setdefault(key, []).append(arr)

        for poi in getattr(t, "pois", []) or []:
            cx = int(np.floor((poi.x - region.x0) / CHUNK))
            cz = int(np.floor((poi.z - region.z0) / CHUNK))
            # structures can poke into neighbouring chunks
            for dz in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    self._pois_by_chunk.setdefault((cx + dx, cz + dz), []).append(poi)

    def _index_veins(self) -> None:
        """Bucket ore veins by cell so each chunk only tests nearby ones."""
        t = self.t
        veins = getattr(t, "veins", None) or []
        if not veins:
            return
        region = self.region
        cs = self.cs
        for vein in veins:
            vx = region.x0 + (vein.cell_x + 0.5) * cs
            vz = region.z0 + (vein.cell_z + 0.5) * cs
            radius = max(vein.radius * cs * 0.5, 2.0) + 24.0
            cx0 = int((vx - radius - region.x0) // CHUNK)
            cx1 = int((vx + radius - region.x0) // CHUNK)
            cz0 = int((vz - radius - region.z0) // CHUNK)
            cz1 = int((vz + radius - region.z0) // CHUNK)
            for cz in range(min(cz0, cz1), max(cz0, cz1) + 1):
                for cx in range(min(cx0, cx1), max(cx0, cx1) + 1):
                    self._veins_by_chunk.setdefault((cx, cz), []).append(vein)

    # ----------------------------------------------------------------------------------
    # field interpolation
    # ----------------------------------------------------------------------------------
    def _cell_coords(self, wx: np.ndarray, wz: np.ndarray):
        """Convert block coordinates to fractional cell indices + bilinear weights."""
        u = (wx - self.region.x0) / self.cs - 0.5
        v = (wz - self.region.z0) / self.cs - 0.5
        i0 = np.floor(u).astype(np.int64)
        j0 = np.floor(v).astype(np.int64)
        fu = (u - i0)[..., None] if u.ndim else (u - i0)
        fv = (v - j0) if not np.isscalar(v) else (v - j0)
        i0c = np.clip(i0, 0, self.t.cells_x - 2)
        j0c = np.clip(j0, 0, self.t.cells_z - 2)
        return i0c, j0c, (u - i0), (v - j0)

    def sample_field(self, field: np.ndarray, wx: np.ndarray, wz: np.ndarray,
                     outside=np.nan, *, nearest: bool = False) -> np.ndarray:
        """Sample a cell field at block coordinates.

        Bilinear by default; ``nearest=True`` for categorical rasters (landmark ids,
        biome overrides) where interpolating between two labels is meaningless.
        """
        if nearest:
            h, w = field.shape
            i = np.clip(np.rint((wx - self.region.x0) / self.cs - 0.5).astype(np.int64),
                        0, w - 1)
            j = np.clip(np.rint((wz - self.region.z0) / self.cs - 0.5).astype(np.int64),
                        0, h - 1)
            return np.asarray(field)[j, i]
        h, w = field.shape
        u = (wx - self.region.x0) / self.cs - 0.5
        v = (wz - self.region.z0) / self.cs - 0.5
        i0 = np.floor(u).astype(np.int64)
        j0 = np.floor(v).astype(np.int64)
        fu = u - i0
        fv = v - j0
        i0c = np.clip(i0, 0, w - 2)
        j0c = np.clip(j0, 0, h - 2)
        f = np.asarray(field, dtype=np.float64)
        a = f[j0c, i0c]
        b = f[j0c, i0c + 1]
        c = f[j0c + 1, i0c]
        d = f[j0c + 1, i0c + 1]
        out = (
            a * (1 - fu) * (1 - fv)
            + b * fu * (1 - fv)
            + c * (1 - fu) * fv
            + d * fu * fv
        )
        if outside is not np.nan and np.isscalar(outside):
            inside = (i0 >= 0) & (i0 < w - 1) & (j0 >= 0) & (j0 < h - 1)
            out = np.where(inside, out, outside)
        return out

    # ----------------------------------------------------------------------------------
    # terrain detail
    # ----------------------------------------------------------------------------------
    def _block_detail(self, wx: np.ndarray, wz: np.ndarray, biome: np.ndarray,
                      slope: np.ndarray, humidity: np.ndarray) -> np.ndarray:
        """Sub-cell relief so cell boundaries are invisible at block scale."""
        c = self.cfg
        amp = float(c["detail_amplitude"])
        d1 = value_noise(wx / float(c["detail_scale"]), wz / float(c["detail_scale"]),
                         self.seed + 5501) * amp
        d2 = value_noise(wx / float(c["micro_scale"]), wz / float(c["micro_scale"]),
                         self.seed + 5502) * float(c["micro_amplitude"])
        # mountains keep more bite, swamps almost none
        from ..core.types import BIOME_INDEX as B

        wild = np.isin(biome, [B["temperate_mountains"], B["cold_mountains"],
                               B["arid_mountains"], B["warm_temperate_mountains"],
                               B["alpine_peaks"], B["volcanic_highland"]])
        soft = np.isin(biome, [B["swamp"], B["mangrove_swamp"], B["salt_flats"],
                               B["beach"], B["shallow_coast"]])
        mult = np.where(wild, 1.9, np.where(soft, 0.45, 1.0))
        mult = mult * (1.0 + (1.0 - humidity) * 0.3)
        return (d1 + d2) * mult * (1.0 + slope)

    # ----------------------------------------------------------------------------------
    # Ashenfall: the specification's own surface rule (spec §6)
    # ----------------------------------------------------------------------------------
    def _ashenfall_masks(self) -> Optional[Dict[str, np.ndarray]]:
        masks = (self.t.meta or {}).get("masks") or {}
        if "ashenfall_ids" not in masks:
            return None
        return masks

    def _ashenfall_sample(self, WX, WZ) -> Optional[Dict[str, np.ndarray]]:
        masks = self._ashenfall_masks()
        if masks is None:
            return None
        out = {
            "ids": self.sample_field(masks["ashenfall_ids"], WX, WZ, outside=-1,
                                     nearest=True).astype(np.int16),
            "bare": np.clip(self.sample_field(masks["ashenfall_bare"], WX, WZ,
                                              outside=0.0), 0.0, 1.0),
        }
        if "ashenfall_lava" in masks:
            out["lava"] = self.sample_field(masks["ashenfall_lava"], WX, WZ,
                                            outside=0.0, nearest=True) > 0.5
        if "ashenfall_biome" in masks:
            out["biome"] = self.sample_field(masks["ashenfall_biome"], WX, WZ,
                                             outside=-1, nearest=True).astype(np.int16)
        if "ashenfall_basin" in masks:
            out["dry"] = self.sample_field(masks["ashenfall_basin"], WX, WZ,
                                           outside=0.0, nearest=True) > 0.5
        return out

    # ----------------------------------------------------------------------------------
    # chunk generation
    # ----------------------------------------------------------------------------------
    def generate_chunk(self, cx: int, cz: int, *, decorate: bool = True) -> ChunkBlocks:
        t = self.t
        region = self.region
        c = self.cfg
        cs = self.cs
        x0 = region.x0 + cx * CHUNK
        z0 = region.z0 + cz * CHUNK
        xs = np.arange(x0, x0 + CHUNK, dtype=np.float64) + 0.5
        zs = np.arange(z0, z0 + CHUNK, dtype=np.float64) + 0.5
        WX, WZ = np.meshgrid(xs, zs)  # (16, 16)

        # ---- macro fields -------------------------------------------------------------
        h_macro = self.sample_field(t.heights, WX, WZ, outside=SEA_LEVEL)
        temp = self.sample_field(t.temperature, WX, WZ, outside=0.5)
        hum = self.sample_field(t.humidity, WX, WZ, outside=0.5)
        fert = self.sample_field(t.fertility, WX, WZ, outside=0.0)
        wl = self.sample_field(t.water_level, WX, WZ, outside=-1e9)
        wm = np.rint(self.sample_field(t.water_mask.astype(np.float64), WX, WZ, outside=0)).astype(np.uint8)
        # Biomes: specials (water, beach, snow cap, swamp, mesa, volcano) come from the
        # cell raster so they stay crisp, but the *climate* biomes are re-derived per
        # block from the interpolated temperature/humidity, which removes the
        # cell-quantised staircase along every forest edge.
        bi = self._biome_at_block(WX, WZ, temp, hum, h_macro, bi_cell=None)

        # The specification assigns each landmark its own biomes; those cells carry an
        # explicit label that outranks the climate classifier.
        af = self._ashenfall_sample(WX, WZ)
        if af is not None and "biome" in af:
            over = af["biome"] >= 0
            bi = np.where(over, af["biome"], bi)

        slope = np.abs(np.gradient(h_macro, axis=1)) + np.abs(np.gradient(h_macro, axis=0))

        detail = self._block_detail(WX, WZ, bi, slope, hum)
        h_float = h_macro + detail
        snowline = float(t.meta.get("snowline", 160.0))
        snowline_jitter = value_noise(WX / 60.0, WZ / 60.0, self.seed + 6001) * float(
            c["snow_line_softness"]
        )
        snow_line = snowline + snowline_jitter

        ground = np.rint(h_float).astype(np.int16)
        ground = np.clip(ground, MIN_Y + 2, MAX_Y - 30)

        # ---- rock column ---------------------------------------------------------------
        ys = np.arange(MIN_Y, MAX_Y, dtype=np.int16)
        ygrid = ys[:, None, None]  # (384,1,1)
        gy = ground[None, :, :]
        depth = gy - ygrid  # how far below the surface

        blocks = np.zeros((WORLD_HEIGHT, CHUNK, CHUNK), dtype=np.uint16)

        stone = BLOCK_ID["stone"]
        deepslate = BLOCK_ID["deepslate"]
        bedrock = BLOCK_ID["bedrock"]
        deep_start = float(c["deepslate_start"])
        # deepslate takes over below y = 0 with a soft boundary
        deep_mask = ygrid < (0 - np.abs(value_noise(WX / 90.0, WZ / 90.0, self.seed + 6100)) * deep_start)
        solid = ygrid <= gy
        blocks[:] = np.where(solid, stone, AIR)
        blocks = np.where(solid & deep_mask, deepslate, blocks)
        # bedrock floor
        bed_noise = hash2(np.floor(WX).astype(np.int64), np.floor(WZ).astype(np.int64),
                          self.seed + 6200)
        bed_top = (MIN_Y + 1 + (bed_noise * 3).astype(np.int16))[None, :, :]
        blocks = np.where(ygrid <= bed_top, bedrock, blocks)

        # ---- surface layers ------------------------------------------------------------
        top_ids, filler_ids, snow_ids, veg = self._surface_layers(
            bi, temp, hum, fert, ground, snow_line, wm, slope, WX, WZ
        )
        soil_depth = (2 + (hash2(np.floor(WX).astype(np.int64), np.floor(WZ).astype(np.int64),
                                 self.seed + 6300) * 3).astype(np.int16))
        soil_mask = (depth >= 0) & (depth < soil_depth[None, :, :])
        surface_mask = depth == 0
        blocks = np.where(soil_mask, filler_ids[None, :, :], blocks)
        blocks = np.where(surface_mask, top_ids[None, :, :], blocks)

        # lava basins: the caldera floor is molten, so a sheet of lava sits on it (the
        # water system was told to keep out of these cells)
        if af is not None and af.get("lava") is not None and np.any(af["lava"]):
            lava_y = (ground + 1)[None, :, :]
            blocks = np.where((ygrid == lava_y) & af["lava"][None, :, :],
                              BLOCK_ID["lava"], blocks)

        # snow layer on top of the finished surface
        snow_layer = np.where(snow_ids > 0, 1, 0).astype(np.int16)
        snow_y = (ground + 1)[None, :, :]
        blocks = np.where((ygrid == snow_y) & (snow_layer > 0), BLOCK_ID["snow"], blocks)
        # extra snow thickness in real glaciers
        thick = np.where(snow_ids >= 2, 2, 0).astype(np.int16)[None, :, :]
        blocks = np.where(
            (ygrid > snow_y) & (ygrid <= snow_y + thick) & (thick > 0), BLOCK_ID["snow_block"], blocks
        )

        # ---- caves and ravines ----------------------------------------------------------
        if float(c["caves"]) > 0:
            cave = self._cave_mask(WX, WZ, ygrid, depth, ground[None, :, :])
            blocks = np.where(cave & (depth > float(c["cave_min_depth"])), AIR, blocks)
            blocks = np.where(cave & (depth > float(c["cave_min_depth"])) & (ygrid > 300), AIR, blocks)

        # ---- water ----------------------------------------------------------------------
        wy = np.where(wl > -1e8, np.rint(wl), -1).astype(np.int16)
        water_top = np.where(wm > 0, wy, np.where(h_float < SEA_LEVEL, int(SEA_LEVEL), -1))
        if af is not None and "dry" in af:
            # the caldera basin sits below sea level but is molten, not flooded
            water_top = np.where(af["dry"], -1, water_top)
        water_top = np.rint(water_top).astype(np.int16)
        is_water = (water_top > -1) & (ygrid <= water_top[None, :, :]) & (ygrid > gy)
        blocks = np.where(is_water, BLOCK_ID["water"], blocks)
        # ice cap on cold water
        if bool(c["ice_on_water"]):
            cold = temp < 0.18
            ice = is_water & (ygrid == water_top[None, :, :]) & cold[None, :, :]
            blocks = np.where(ice, BLOCK_ID["ice"], blocks)
        # dry land never sits below the water table
        blocks = np.where(
            (water_top > -1)[None, :, :] & (ygrid <= water_top[None, :, :]) & (depth < 0),
            BLOCK_ID["water"],
            blocks,
        )

        # ---- ores -----------------------------------------------------------------------
        blocks = self._apply_ores(blocks, cx, cz, x0, z0, ground)

        # ---- vegetation + structures ------------------------------------------------------
        if decorate and (float(c["vegetation_in_export"]) > 0
                         or float(c["structures_in_export"]) > 0):
            blocks = self._decorate(blocks, cx, cz, x0, z0, ground, bi, wm, water_top, veg)

        return ChunkBlocks(
            cx=cx,
            cz=cz,
            blocks=blocks,
            biomes=bi.astype(np.uint8),
            surface_y=ground.copy(),
            water_y=water_top.copy(),
        )

    # ----------------------------------------------------------------------------------
    def _biome_at_block(self, WX, WZ, temp, hum, height, bi_cell=None):
        """Classify the biome on the block lattice for seamless transitions."""
        from ..core.surface_rules import biome_from_climate

        t = self.t
        special = np.rint(
            self.sample_field(t.biome.astype(np.float64), WX, WZ, outside=0)
        ).astype(np.int16)
        wm = np.rint(self.sample_field(t.water_mask.astype(np.float64), WX, WZ, outside=0)).astype(np.uint8)
        snowline = float(t.meta.get("snowline", 165.0))
        return biome_from_climate(
            temp=temp, humidity=hum, height=height, water_mask=wm, special=special,
            snowline=snowline, sea_level=float(t.meta.get("sea_level", SEA_LEVEL)),
        )

    # ----------------------------------------------------------------------------------
    def _surface_layers(self, biome, temp, hum, fert, ground, snow_line, water_mask, slope,
                        WX, WZ):
        """Vectorised per-column surface/top block selection."""
        n = int(np.size(biome))
        top = np.full(n, BLOCK_ID["grass_block"], dtype=np.uint16)
        filler = np.full(n, BLOCK_ID["dirt"], dtype=np.uint16)
        snow = np.zeros(n, dtype=np.int16)
        # per-column dominant plant (the actual scatter comes from the population layer)
        veg_code = np.zeros(n, dtype=np.int16)

        # rock patches: exposed stone/gravel where the surface is steep or noise says so
        rock_patch = value_noise(WX / float(self.cfg["rock_patch_scale"]),
                                 WZ / float(self.cfg["rock_patch_scale"]), self.seed + 6400)
        steep = slope > 0.85

        flat_bi = biome.ravel()

        # resolve a profile per distinct biome in this chunk - usually 1-3 of them
        temp_flat = temp.ravel() if np.ndim(temp) else np.full(n, float(temp))
        hum_flat = hum.ravel() if np.ndim(hum) else np.full(n, float(hum))
        unique = np.unique(flat_bi)
        for b in unique.tolist():
            idx = np.nonzero(flat_bi == b)[0]
            prof = profile_for(
                int(b),
                temperature=float(np.mean(temp_flat[idx])) if idx.size else 0.5,
                humidity=float(np.mean(hum_flat[idx])) if idx.size else 0.5,
            )
            top[idx] = block_id(prof.top)
            filler[idx] = block_id(prof.filler)
            if prof.surface_snow:
                snow[idx] = prof.surface_snow
            if prof.ground_cover:
                from .structures import SPECIES_ID

                veg_code[idx] = SPECIES_ID.get(prof.ground_cover[0], 0)

        # climate overrides
        cold = temp.ravel() < 0.2
        hot_dry = (temp.ravel() > 0.6) & (hum.ravel() < 0.25)
        top[cold & (snow.ravel() == 0) & (ground.ravel() > snow_line.ravel())] = BLOCK_ID["snow_block"]
        snow[cold & (ground.ravel() > snow_line.ravel())] = np.maximum(
            snow[cold & (ground.ravel() > snow_line.ravel())], 1
        )
        dry_idx = np.nonzero(hot_dry & (top == BLOCK_ID["grass_block"]))[0]
        top[dry_idx] = BLOCK_ID["sand"]
        filler[dry_idx] = BLOCK_ID["sandstone"]

        # underwater ground is never grass
        wet = water_mask.ravel() > 0
        top[wet] = np.where(
            (top[wet] == BLOCK_ID["grass_block"]) | (top[wet] == BLOCK_ID["podzol"]),
            BLOCK_ID["sand"],
            top[wet],
        )
        filler[wet] = np.where(filler[wet] == BLOCK_ID["dirt"], BLOCK_ID["sand"], filler[wet])

        # exposed rock: steep faces above the local slope threshold, patchy so it reads
        # as outcrops rather than a uniform cliff texture
        rock_flat = np.asarray(rock_patch, dtype=np.float64).ravel()
        slope_flat = np.asarray(slope, dtype=np.float64).ravel()
        snow_flat = snow.ravel()
        ground_flat = ground.ravel()
        snow_line_flat = np.asarray(snow_line, dtype=np.float64).ravel()
        steep_flat = slope_flat > 0.65
        exposed = steep_flat & ~wet & (snow_flat == 0)
        top[exposed] = np.where(
            rock_flat[exposed] > 0.45, BLOCK_ID["gravel"], BLOCK_ID["stone"]
        )
        # snow always wins on high cold ground
        high_cold = (snow_flat > 0) & (ground_flat > snow_line_flat)
        top[high_cold] = BLOCK_ID["snow_block"]

        self._apply_ashenfall_surface(
            top, filler, snow, biome, ground, slope, WX, WZ, water_mask
        )

        return (
            top.reshape(np.shape(biome)).astype(np.uint16),
            filler.reshape(np.shape(biome)).astype(np.uint16),
            snow.reshape(np.shape(biome)),
            veg_code.reshape(np.shape(biome)),
        )

    def _apply_ashenfall_surface(self, top, filler, snow, biome, ground, slope,
                                 WX, WZ, water_mask) -> None:
        """Spec §6: the Still Life slope-aware surface rule, applied in place.

        ============  =========================================================
        slope         surface
        ============  =========================================================
        0 - 25 deg    grass block / deep loam (populated)
        25 - 35 deg   coarse dirt, podzol (40 % density)
        35 - 45 deg   cobblestone, stone scree, gravel (5 % density)
        > 45 deg      granite / basalt bedrock (0 %)
        Y > 225       snow block, packed ice, calcite (treeline)
        caldera       blackstone, basalt, magma block (barren override)
        ============  =========================================================
        """
        af = self._ashenfall_sample(WX, WZ)
        if af is None:
            return
        top_f = top.ravel()
        filler_f = filler.ravel()
        snow_f = snow.ravel()
        deg = np.degrees(np.arctan(np.asarray(slope, dtype=np.float64))).ravel()
        ground_f = np.asarray(ground).ravel()
        wet = np.asarray(water_mask).ravel() > 0
        gravel = BLOCK_ID["gravel"]
        stone = BLOCK_ID["stone"]

        band1 = (deg >= 25.0) & (deg < 35.0) & ~wet
        band2 = (deg >= 35.0) & (deg <= 45.0) & ~wet
        band3 = (deg > 45.0) & ~wet
        top_f[band1] = BLOCK_ID["coarse_dirt"]
        filler_f[band1] = BLOCK_ID["coarse_dirt"]
        top_f[band2] = np.where(deg[band2] > 0.55 * 45.0 + 13.0,
                                BLOCK_ID["cobblestone"], BLOCK_ID["stone"])
        filler_f[band2] = stone
        scree = np.clip((deg - 35.0) / 12.0, 0.0, 1.0)
        # sheer cliffs are bedrock/granite: no topsoil survives 45 degrees
        top_f[band3] = np.where(np.asarray(biome).ravel()[band3] == BIOME_INDEX["basalt_deltas"],
                                BLOCK_ID["basalt"], BLOCK_ID["granite"])
        filler_f[band3] = np.where(deg[band3] > 60.0, BLOCK_ID["bedrock"], stone)

        # treeline (spec §6: Y > 225)
        treeline = ground_f > 225.0
        if np.any(treeline):
            top_f[treeline] = np.where(
                np.asarray(biome).ravel()[treeline] == BIOME_INDEX["frozen_peaks"],
                BLOCK_ID["snow_block"], BLOCK_ID["calcite"])
            filler_f[treeline] = BLOCK_ID["packed_ice"]
            snow_f[treeline] = np.maximum(snow_f[treeline], 1)

        lava = af.get("lava")
        ids = af["ids"].ravel()
        if lava is not None and np.any(lava):
            lv = lava.ravel()
            top_f[lv] = BLOCK_ID["magma_block"]
            filler_f[lv] = BLOCK_ID["magma_block"]

        # the caldera rim and crater: volcanic barrenness overrides every other rule
        from .landmarks import ELEVATIONS, LANDMARKS

        cal_idx = next((i for i, lm in enumerate(LANDMARKS) if lm.kind == "caldera"), -1)
        if cal_idx >= 0:
            cal = ids == cal_idx
            rim = cal & (ground_f > ELEVATIONS["caldera_floor"] + 22.0)
            top_f[rim] = np.where(deg[rim] > 30.0, BLOCK_ID["blackstone"],
                                  BLOCK_ID["basalt"])
            filler_f[rim] = BLOCK_ID["blackstone"]
            # the Obsidian Throne itself, and the glassy shoulders below the rim
            throne = cal & (ground_f > ELEVATIONS["caldera_throne"] - 6.0) \
                & (ground_f < ELEVATIONS["caldera_throne"] + 30.0)
            top_f[throne] = BLOCK_ID["obsidian"]
            filler_f[throne] = BLOCK_ID["obsidian"]
        # volcanic glass on the dune crests (spec: vitrified black-glass dunes)
        dunes_idx = next((i for i, lm in enumerate(LANDMARKS) if lm.kind == "dunes"), -1)
        if dunes_idx >= 0:
            crest = (ids == dunes_idx) & (ground_f > 89.0)
            top_f[crest] = BLOCK_ID["black_glazed_terracotta"]
        # the Veil of Salt: white crust over abyssal gravel
        veil = ids >= len(LANDMARKS)
        if np.any(veil):
            crust = veil & ~wet
            top_f[crust] = np.where(
                hash2(np.floor(np.asarray(WX)).astype(np.int64).ravel()[crust],
                      np.floor(np.asarray(WZ)).astype(np.int64).ravel()[crust],
                      self.seed + 7300) > 0.55,
                BLOCK_ID["calcite"], BLOCK_ID["gravel"])
            filler_f[crust] = BLOCK_ID["gravel"]
        top[:] = top_f.reshape(top.shape)
        filler[:] = filler_f.reshape(filler.shape)
        snow[:] = snow_f.reshape(snow.shape)

    # ----------------------------------------------------------------------------------
    def _cave_mask(self, WX, WZ, ygrid, depth, ground):
        """Cheap pseudo-3D cave system: tunnel networks + chambers.

        Full 3D noise per block would be the accurate way, but a chunk has ~98k blocks
        and we need thousands of chunks.  Instead the cave field is a 2D worm pattern
        (``|noise| < t`` gives winding tunnels) modulated by a coarse 3D chamber field,
        plus a depth gate - which reads the same in game and costs a fraction as much.
        """
        c = self.cfg
        scale = float(c["cave_scale"])
        cave_amount = float(c["caves"])
        worm = fbm(WX, WZ, octaves=2, scale=scale, seed=self.seed + 7001)
        band = np.abs(worm) < (0.055 * cave_amount)
        band2 = np.abs(fbm(WX, WZ, octaves=2, scale=scale * 0.55, seed=self.seed + 7002)) < (
            0.035 * cave_amount
        )
        shell = band | band2
        # truncate caves by depth so they stay underground
        gate = np.clip((depth - float(c["cave_min_depth"])) / 6.0, 0.0, 1.0)
        gate = gate * np.clip((float(c["cave_max_y"]) - ygrid + 40.0) / 30.0, 0.0, 1.0)
        caves = (shell[None, :, :] & (gate > 0.35))
        # occasional big chambers
        chamber = fbm(WX, WZ, octaves=2, scale=scale * 2.6, seed=self.seed + 7003)
        caves |= (chamber[None, :, :] > (1.0 - 0.10 * float(c["caves"]))) & (depth > 14)
        # ravines: narrow, deep, long slots
        if float(c["ravines"]) > 0:
            rv = np.abs(fbm(WX, WZ, octaves=2, scale=float(c["ravine_scale"]),
                            seed=self.seed + 7004))
            caves |= (rv < (0.012 * float(c["ravines"])))[None, :, :] & (depth > 10)
        # never break the surface layer
        caves &= depth > 3
        return caves

    # ----------------------------------------------------------------------------------
    def _apply_ores(self, blocks, cx, cz, x0, z0, ground):
        veins = self._veins_by_chunk.get((cx, cz))
        if not veins:
            return blocks
        region = self.region
        cs = self.cs
        xs = np.arange(x0, x0 + CHUNK) + 0.5
        zs = np.arange(z0, z0 + CHUNK) + 0.5
        WX, WZ = np.meshgrid(xs, zs)
        for vein in veins:
            vx = region.x0 + (vein.cell_x + 0.5) * cs
            vz = region.z0 + (vein.cell_z + 0.5) * cs
            radius = max(vein.radius * cs * 0.5, 2.0)
            dist = np.hypot(WX - vx, WZ - vz)
            inside = dist <= radius
            if not np.any(inside):
                continue
            cy = ground - vein.depth
            rng = np.random.default_rng(self.seed + int(vein.cell_x) * 7919 + int(vein.cell_z))
            for (zz, xx) in zip(*np.nonzero(inside)):
                top_y = int(cy[zz, xx])
                if top_y <= MIN_Y + 3:
                    continue
                n_blocks = int(2 + rng.random() * 5 * vein.richness)
                for _ in range(n_blocks):
                    oy = int(top_y - rng.random() * 7)
                    ox = int(xx + rng.integers(-2, 3))
                    oz = int(zz + rng.integers(-2, 3))
                    if not (0 <= ox < CHUNK and 0 <= oz < CHUNK):
                        continue
                    yi = oy - MIN_Y
                    if 0 <= yi < WORLD_HEIGHT:
                        current = blocks[yi, oz, ox]
                        if current in (BLOCK_ID["stone"], BLOCK_ID["deepslate"],
                                       BLOCK_ID["andesite"], BLOCK_ID["granite"],
                                       BLOCK_ID["diorite"], BLOCK_ID["tuff"]):
                            deep = oy < 0
                            blocks[yi, oz, ox] = ore_block(vein.ore, deep)
        return blocks

    # ----------------------------------------------------------------------------------
    def _decorate(self, blocks, cx, cz, x0, z0, ground, biome, water_mask, water_top, veg):
        """Stamp vegetation and structures from the population layer."""
        from .structures import stamp_structure, stamp_vegetation

        t = self.t
        if float(self.cfg["vegetation_in_export"]) > 0 and getattr(t, "vegetation", None) is not None:
            blocks = stamp_vegetation(
                blocks,
                vegetation=t.vegetation,
                cell_size=self.cs,
                region_x0=self.region.x0,
                region_z0=self.region.z0,
                chunk_x0=x0,
                chunk_z0=z0,
                ground=ground,
                seed=self.seed,
            )
        if float(self.cfg["structures_in_export"]) > 0:
            near_water = bool(water_top is not None and np.any(water_top > 0))
            for poi in self._pois_by_chunk.get((cx, cz), ()):
                lx = int(round(poi.x - x0))
                lz = int(round(poi.z - z0))
                if -8 <= lx <= 23 and -8 <= lz <= 23:
                    stamp_structure(
                        blocks,
                        ground,
                        kind=poi.kind,
                        local_x=lx,
                        local_z=lz,
                        seed=self.seed + int(poi.x) * 31 + int(poi.z),
                        biome=poi.biome,
                        near_water=near_water,
                    )
            roads = self._roads_by_chunk.get((cx, cz))
            if roads:
                self._stamp_roads(blocks, x0, z0, ground, roads)
        return blocks

    def _stamp_roads(self, blocks, x0, z0, ground, road_samples):
        """Cut a walkable road surface under each (pre-bucketed) road sample."""
        mat = BLOCK_ID["coarse_dirt"]
        air = AIR
        for pts in road_samples:
            lx = np.rint(pts[:, 0] - x0).astype(np.int64)
            lz = np.rint(pts[:, 1] - z0).astype(np.int64)
            keep = (lx >= 0) & (lx < CHUNK) & (lz >= 0) & (lz < CHUNK)
            # widen by one block on each side, vectorised
            for w in (-1, 0, 1):
                xs = lx[keep] + w
                zs = lz[keep]
                ok = (xs >= 0) & (xs < CHUNK)
                if not np.any(ok):
                    continue
                xs, zs = xs[ok], zs[ok]
                gy = ground[zs, xs].astype(np.int64)
                blocks[gy - MIN_Y, zs, xs] = mat
                above = gy - MIN_Y + 1
                valid = above < WORLD_HEIGHT
                if np.any(valid):
                    blocks[above[valid], zs[valid], xs[valid]] = air


# --------------------------------------------------------------------------------------
# Column generation used by the interactive "inspect a column" UI feature
# --------------------------------------------------------------------------------------


def column_profile(sampler: TerrainSampler, wx: float, wz: float) -> List[Tuple[int, str]]:
    """Return the full block column at world (x, z) - used by the UI inspector."""
    cx = int(math.floor((wx - sampler.region.x0) / CHUNK))
    cz = int(math.floor((wz - sampler.region.z0) / CHUNK))
    chunk = sampler.generate_chunk(cx, cz)
    lx = int(wx - sampler.region.x0) % CHUNK
    lz = int(wz - sampler.region.z0) % CHUNK
    out: List[Tuple[int, str]] = []
    last = None
    for yi in range(WORLD_HEIGHT):
        bid = int(chunk.blocks[yi, lz, lx])
        name = ID_BLOCK.get(bid, "stone")
        if name != last:
            out.append((MIN_Y + yi, name))
            last = name
    return out
