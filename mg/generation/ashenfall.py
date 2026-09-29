"""The continent of Vantyra: Ashenfall's own generation engine.

The generic terrain chain in :mod:`mg.generation.terrain` composes noise belts, plateaus
and island masks - it makes a good *archipelago* or *highland*, but the Ashenfall
specification pins down a continent whose every landform has an exact elevation, an exact
bounding box and an exact coordinate.  So this module builds the continent the other way
round: the specification's tables are the authority and noise only supplies texture.

Layout of the engine, in order:

1. ``continentalness`` - a coast-relative radial field.  ``R_coast(theta)`` describes how
   far the shoreline sits in each compass direction (the continent reaches much further
   north than south, which is what puts the Glacial Spine inland and the Forgotten Coast
   on the water), and ``C`` is a monotone spline of ``r - R_coast``.
2. lithosphere ``erosion`` / ``ridges`` fields, remapped into each region's window from
   the specification's spline table.
3. ``climate`` - the 5-tier temperature/humidity field in spec units (``-1..1``), with
   the regional windows from the same table taking over inside each landmark.
4. base height from ``C`` plus hills, mountains and detail noise.
5. :func:`mg.generation.landform.apply_ashenfall` - the nine landmarks, the continental
   shelf dropoff and the Veil of Salt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

import numpy as np

from ..core.noise import domain_warp, fbm, ridged_fbm, smoothstep, spline, stretch01
from .landform import LandformResult, apply_ashenfall
from .landmarks import (CANVAS, HALF, LANDMARKS, SEA_LEVEL, VEIL_RADIUS, feather,
                        region_ids)

# --------------------------------------------------------------------------------------
# 1. Coastline shape
# --------------------------------------------------------------------------------------

#: ``R_coast`` control points: compass bearing (degrees, 0 = +X / east, 90 = +Z / south)
#: against the distance from the origin to the shoreline in blocks.
COAST_RADIUS: Tuple[Tuple[float, float], ...] = (
    (-180.0, 3350.0),
    (-135.0, 3320.0),
    (-90.0, 3480.0),      # north: the continent reaches furthest here (Glacial Spine)
    (-45.0, 3260.0),
    (0.0, 3150.0),        # east: Gilded Dunes sit inland of this
    (30.0, 3120.0),
    (45.0, 3150.0),       # south-east: the Whispering Fen drains into the sea here
    (70.0, 3080.0),
    (90.0, 3200.0),       # south: the Forgotten Coast spawn shoreline
    (120.0, 2860.0),
    (146.0, 2700.0),      # south-west: the Sunken Reach is a drowned shelf
    (180.0, 3150.0),      # west: Cogwork March
)

#: Interiors temperature/humidity gradient: north cold, south warm (spec §4 tier table).
CLIMATE_TIER_SPLINE = (
    (-1.00, -1.05),       # pure arctic
    (-0.75, -0.90),
    (-0.45, -0.45),
    (-0.15, -0.15),
    (0.00, 0.00),
    (0.35, 0.35),
    (0.60, 0.55),
    (1.00, 0.95),
)

#: ``C`` -> base elevation (spec §1 feature table + §3 land/ocean clamp rules).
BASE_SPLINE = (
    (-1.05, 20.0),
    (-0.85, 30.0),
    (-0.60, 40.0),
    (-0.35, 48.0),
    (-0.20, 53.0),
    (-0.02, 60.0),
    (0.05, 63.0),
    (0.12, 66.0),
    (0.25, 68.0),
    (0.45, 74.0),
    (0.65, 84.0),
    (0.85, 98.0),
    (1.15, 122.0),
)

#: ``r - R_coast`` -> continentalness, so the waterline lands on ``Delta = 0``.
COAST_SPLINE = (
    (-2600.0, 0.60),
    (-1200.0, 0.52),
    (-700.0, 0.44),
    (-400.0, 0.31),
    (-150.0, 0.15),
    (-40.0, 0.05),
    (0.0, -0.02),
    (140.0, -0.20),
    (450.0, -0.45),
    (950.0, -0.70),
    (2600.0, -1.05),
)


def coast_radius(X: np.ndarray, Z: np.ndarray) -> np.ndarray:
    """Distance from the origin to the shoreline along each cell's bearing."""
    theta = np.degrees(np.arctan2(Z, X))
    pts = np.array(COAST_RADIUS, dtype=np.float64)
    order = np.argsort(pts[:, 0])
    src_t, src_r = pts[order, 0], pts[order, 1]
    # wrap a copy of the table either side so the interpolation is periodic
    t = np.concatenate([src_t - 360.0, src_t, src_t + 360.0])
    r = np.concatenate([src_r, src_r, src_r])
    return np.interp(theta, t, r)


@dataclass
class AshenfallTerrain:
    dem: np.ndarray
    cont: np.ndarray
    erosion: np.ndarray
    ridges: np.ndarray
    temperature: np.ndarray
    humidity: np.ndarray
    ids: np.ndarray
    lava: np.ndarray
    bare: np.ndarray
    dry: np.ndarray                 # caldera interior: never flooded
    diagnostics: Dict[str, float] = field(default_factory=dict)


class AshenfallBuilder:
    """Builds the continent of Vantyra from the specification tables."""

    def __init__(self, cfg: Dict, region, seed: int):
        options = ((cfg or {}).get("terrain") or {}).get("ashenfall") or {}
        self.cfg = options
        self.region = region
        self.seed = int(seed)

    # ----------------------------------------------------------------------------------
    def _windows(self, shape, region, field: np.ndarray, key: str,
                 weight: float) -> np.ndarray:
        """Remap ``field`` into each region's spec window (``cont``/``erosion``/``ridges``).

        The specification gives every region a *range* for each of the three Lithosphere
        density functions rather than a single value, so the noise keeps its structure and
        only its amplitude is scaled into the window the region is allowed to occupy.
        """
        out = np.asarray(field, dtype=np.float64).copy()
        lo, hi = float(np.percentile(out, 1.0)), float(np.percentile(out, 99.0))
        unit = np.clip((out - lo) / max(hi - lo, 1e-9), 0.0, 1.0)
        for lm in LANDMARKS:
            window = getattr(lm.spline, key)
            mask = (region_ids(shape, region, landmarks=[lm], include_veil=False) == 0)
            if not np.any(mask):
                continue
            taper = feather(mask, cell_size=float(region.cell_size),
                            width_blocks=max(220.0, float(region.cell_size) * 12.0))
            target = window[0] + (window[1] - window[0]) * unit
            k = np.clip(taper * weight, 0.0, 1.0)
            out = out * (1.0 - k) + target * k
        return out

    # ----------------------------------------------------------------------------------
    def build(self, X: np.ndarray, Z: np.ndarray,
              temperature: np.ndarray, humidity: np.ndarray,
              cont_raw: Optional[np.ndarray] = None,
              progress=None) -> AshenfallTerrain:
        """Compose the continent. ``temperature``/``humidity`` arrive in 0..1 units."""
        cfg = self.cfg
        seed = self.seed
        region = self.region
        shape = X.shape
        cs = float(region.cell_size)

        def tick(frac: float, label: str):
            if progress:
                progress(frac, label)

        # ---- 1. continentalness -------------------------------------------------------
        wx, wz = domain_warp(X, Z, scale=2100.0, strength=900.0, seed=seed + 17, octaves=4)
        # the shoreline itself is a noisy curve, not a circle
        rc = (coast_radius(wx, wz)
              + fbm(X, Z, octaves=3, scale=2600.0, seed=seed + 19) * 620.0
              + fbm(X, Z, octaves=2, scale=820.0, seed=seed + 29) * 190.0)
        delta = np.hypot(wx, wz) - rc
        cont = spline(COAST_SPLINE, delta)
        # interior bonus: the land reaches higher inland of the caldera belt
        cont = cont + 0.12 * np.exp(-((X / 2700.0) ** 2 + ((Z + 1500.0) / 2300.0) ** 2))
        cont = np.clip(cont + fbm(X, Z, octaves=3, scale=900.0, seed=seed + 23) * 0.05,
                       -1.20, 1.20)
        cont = self._windows(shape, region, cont, "cont", 0.60)

        # ---- 2. erosion / ridges ------------------------------------------------------
        ero = stretch01(fbm(X, Z, octaves=4, scale=1500.0, seed=seed + 31), 1.0, 99.0) * 2.0 - 1.0
        ero = self._windows(shape, region, ero, "erosion", 0.70)
        rid = stretch01(fbm(X, Z, octaves=5, scale=1250.0, seed=seed + 41), 1.0, 99.0) * 2.0 - 1.0
        rid = self._windows(shape, region, rid, "ridges", 0.70)
        tick(0.15, "lithosphere splines")

        # ---- 3. climate in spec units (-1..1) ----------------------------------------
        t_spec, h_spec = self._climate(X, Z, temperature, humidity)
        tick(0.3, "climate tiers")

        # ---- 4. base height -----------------------------------------------------------
        land = smoothstep(-0.12, 0.28, cont)
        base = spline(BASE_SPLINE, cont)

        # Ridges: |R| crests sharp ridges while R ~ 0 carves wide U-shaped valleys.  The
        # relief is confined to *belts* - high ridges inside the continental interior -
        # so the lowlands stay lowlands instead of every cell growing a mountain.
        rid01 = np.clip(rid * 0.5 + 0.5, 0.0, 1.0)
        belt = smoothstep(0.48, 0.95, rid01) * smoothstep(0.20, 0.62, cont)
        ridge_amp = 18.0 + 145.0 * belt
        ridge_shape = ridged_fbm(X, Z, octaves=6, scale=1050.0, seed=seed + 51)
        relief = ridge_amp * ridge_shape * land
        # erosion: E < -0.45 leaves jagged rock, E > 0.35 flattens to floodplain
        detail_scale = np.clip(1.1 - 0.7 * (ero * 0.5 + 0.5), 0.2, 1.2)
        rolling = fbm(X, Z, octaves=4, scale=1150.0, seed=seed + 61) * (4.0 + 9.0 * land)
        hills = fbm(X, Z, octaves=4, scale=420.0, seed=seed + 71) * (3.0 + 6.0 * land)
        detail = fbm(X, Z, octaves=4, scale=120.0, seed=seed + 81) * 2.6
        dem = base + (relief + rolling + hills) * detail_scale + detail * land
        tick(0.55, "continental relief")

        # ---- 5. landmarks + shelf -----------------------------------------------------
        shaped: LandformResult = apply_ashenfall(
            dem, region, seed=seed,
            sea_level=SEA_LEVEL,
            shelf=bool(cfg.get("shelf", True)),
            landmarks=list(LANDMARKS),
            apply_landmarks=bool(cfg.get("landmarks", True)),
        )
        tick(0.85, "landmarks")

        diag = dict(shaped.diagnostics)
        diag["cont_min"] = float(cont.min())
        diag["cont_max"] = float(cont.max())
        diag["dem_min"] = float(shaped.dem.min())
        diag["dem_max"] = float(shaped.dem.max())
        return AshenfallTerrain(
            dem=shaped.dem,
            cont=cont,
            erosion=ero,
            ridges=rid,
            temperature=t_spec,
            humidity=h_spec,
            ids=shaped.ids,
            lava=shaped.lava,
            bare=shaped.bare,
            dry=shaped.basin | shaped.lava,
            diagnostics=diag,
        )

    # ----------------------------------------------------------------------------------
    def _climate(self, X: np.ndarray, Z: np.ndarray,
                 temperature: np.ndarray, humidity: np.ndarray
                 ) -> Tuple[np.ndarray, np.ndarray]:
        """Blend the generic climate into the specification's 5-tier windows.

        ``ClimateModel`` produces ``0..1`` fields; the specification works in ``-1..1``,
        so everything is converted once at the boundary (``spec = 2 * unit - 1``), the
        windows are applied in spec units, and the result is converted back.
        """
        seed = self.seed
        region = self.region
        t = np.clip(np.asarray(temperature, dtype=np.float64), 0.0, 1.0) * 2.0 - 1.0
        h = np.clip(np.asarray(humidity, dtype=np.float64), 0.0, 1.0) * 2.0 - 1.0

        # A regional temperature field carries the extremes: the arid volcanic belt is
        # hot, the north is cold.  The landmark windows below then lock each region to
        # the tier the specification assigns it.
        reg = fbm(X, Z, octaves=3, scale=2400.0, seed=seed + 91)
        t = np.clip(t * 0.45 + reg * 0.85 + 0.10, -1.15, 1.15)
        h = np.clip(h * 0.55 + fbm(X, Z, octaves=3, scale=2000.0, seed=seed + 97) * 0.7,
                    -1.15, 1.15)

        shape = X.shape
        unit_t = np.clip((t + 1.0) * 0.5, 0.0, 1.0)
        unit_h = np.clip((h + 1.0) * 0.5, 0.0, 1.0)
        for lm in LANDMARKS:
            mask = region_ids(shape, region, landmarks=[lm], include_veil=False) == 0
            if not np.any(mask):
                continue
            taper = feather(mask, cell_size=float(region.cell_size),
                            width_blocks=max(220.0, float(region.cell_size) * 12.0))
            k = np.clip(taper * 0.85, 0.0, 1.0)
            t_target = lm.climate.temp[0] + (lm.climate.temp[1] - lm.climate.temp[0]) * unit_t
            h_target = lm.climate.humidity[0] + (lm.climate.humidity[1] - lm.climate.humidity[0]) * unit_h
            t = t * (1.0 - k) + t_target * k
            h = h * (1.0 - k) + h_target * k

        # The Veil of Salt is whatever the deep ocean is: cold, wet, featureless.
        veil = np.hypot(X, Z) > VEIL_RADIUS
        t = np.where(veil, np.minimum(t, -0.55), t)
        h = np.where(veil, np.clip(h, -0.4, 0.6), h)

        t = np.clip(t, -1.2, 1.2)
        h = np.clip(h, -1.2, 1.2)
        return np.clip((t + 1.0) * 0.5, 0.0, 1.0), np.clip((h + 1.0) * 0.5, 0.0, 1.0)

    # ----------------------------------------------------------------------------------
    def blend_tiers(self, t_unit: np.ndarray) -> np.ndarray:
        """The 5-tier climate index of a ``0..1`` temperature field (spec §4)."""
        t = np.asarray(t_unit, dtype=np.float64) * 2.0 - 1.0
        tier = np.full(t.shape, 4, dtype=np.int8)     # 4 = arid/volcanic by default
        tier = np.where(t <= -0.75, 0, tier)          # glacial arctic
        tier = np.where((t > -0.75) & (t <= -0.25), 1, tier)   # boreal buffer belt
        tier = np.where((t > -0.25) & (t <= 0.40), 2, tier)    # temperate lowlands
        tier = np.where((t > 0.40) & (t < 0.70), 3, tier)      # subtropical bayou / arid
        return tier


# --------------------------------------------------------------------------------------
# Landmark biomes (spec §2 landmark table)
# --------------------------------------------------------------------------------------


def landmark_biome_field(ids: np.ndarray, heights: np.ndarray,
                         water: Optional[np.ndarray] = None) -> np.ndarray:
    """Per-cell biome override, ``-1`` where the climate classifier keeps authority.

    The specification names the biomes of every region, and those names are vanilla ids,
    so they are written straight into the export rather than being re-derived from the
    temperature field.  Within a region the first-listed biome covers the low ground and
    the second takes the high or deep ground, which is how the specification describes
    them ("frozen peaks, jagged peaks, grove" reading summit to foot).
    """
    from ..core.types import BIOME_INDEX

    out = np.full(np.asarray(ids).shape, -1, dtype=np.int16)
    h = np.asarray(heights, dtype=np.float64)

    def put(mask: np.ndarray, name: str) -> None:
        idx = BIOME_INDEX.get(name)
        if idx is not None:
            out[mask] = idx

    for i, lm in enumerate(LANDMARKS):
        cell = ids == i
        if not np.any(cell):
            continue
        if lm.kind == "caldera":
            put(cell, "basalt_deltas")
            put(cell & (h < 62.0), "basalt_deltas")          # the molten basin
            put(cell & (h > 120.0), "eroded_badlands")       # the outer volcanic apron
        elif lm.kind == "cordillera":
            put(cell & (h > 240.0), "frozen_peaks")
            put(cell & (h > 196.0) & (h <= 240.0), "jagged_peaks")
            put(cell & (h <= 196.0), "grove")
        elif lm.kind == "dunes":
            put(cell & (h > 88.0), "badlands")
            put(cell & (h <= 88.0), "desert")
        elif lm.kind == "fen":
            put(cell & (h < 63.2), "mangrove_swamp")
            put(cell & (h >= 63.2), "swamp")
        elif lm.kind == "drowned_shelf":
            put(cell & (h > 50.0), "warm_ocean")
            put(cell & (h <= 50.0), "lukewarm_ocean")
        elif lm.kind == "spires":
            put(cell & (h > 150.0), "windswept_hills")
            put(cell & (h <= 150.0), "meadow")
        elif lm.kind == "terraces":
            put(cell & (h > 126.0), "cherry_grove")
            put(cell & (h <= 126.0), "meadow")
        elif lm.kind == "coast":
            put(cell & (h > 71.0), "meadow")
            put(cell & (h <= 71.0), "plains")
        else:                                                 # the Cogwork March
            put(cell & (h > 104.0), "windswept_hills")
            put(cell & (h <= 104.0), "wooded_badlands")

    veil = ids >= len(LANDMARKS)
    if np.any(veil):
        put(veil, "deep_cold_ocean")
    if water is not None:
        deep = np.asarray(water) == 3
        put(veil & deep, "deep_cold_ocean")
    return out


# --------------------------------------------------------------------------------------
# Preset
# --------------------------------------------------------------------------------------

#: Spec §2 landmark -> the vanilla biomes the specification assigns it, in the order the
#: table lists them (primary first).  ``None`` means "let the generic classifier decide".
LANDMARK_BIOMES: Dict[str, Tuple[str, ...]] = {
    lm.key: lm.biomes for lm in LANDMARKS
}

ASHENFALL_TERRAIN: Dict[str, Any] = {
    "engine": "ashenfall",
    "sea_level": SEA_LEVEL,
    "ashenfall": {
        "enabled": True,
        "shelf": True,
        "landmarks": True,
    },
}

ASHENFALL_PRESET: Dict[str, Any] = {
    "label": "Ashenfall - Vantyra",
    "description": "The 8,000 x 8,000 continent of Vantyra, exactly as specified.",
    "seed": 20250929,
    "name": "Ashenfall",
    "region": {
        "x0": -HALF,
        "z0": -HALF,
        "blocks_x": CANVAS,
        "blocks_z": CANVAS,
        "cell_size": 4,
    },
    "terrain": ASHENFALL_TERRAIN,
    "climate": {
        "sea_level": SEA_LEVEL,
        # spec §4: T_eff = T_base - 0.0055 * max(0, y - 62), expressed in the pipeline's
        # 0..1 temperature units (the spec's -1..1 range is twice as wide).
        "lapse_rate_per_block": 0.00275,
        "max_lapse_drop": 0.60,
        "region_scale": 3200.0,
        "region_octaves": 4,
        "latitude_span": 1.0,
        "polar_curve": 2.0,
        "latitude_band_noise": 0.4,
        "temperature_bias": 0.0,
        "humidity_bias": 0.0,
        "wind_direction": 90.0,
        "wind_speed": 0.5,
        "moisture_steps": 26,
        "rain_shadow_strength": 1.0,
    },
    "water": {
        "sea_level": SEA_LEVEL,
        "river_threshold": 900.0,
        "climate_river_bias": 0.7,
        "valley_depth": 1.25,
        "bank_flare": 2.0,
        "carve_passes": 3,
        "lake_min_depth": 1.0,
        "lake_min_cells": 4,
        "max_lakes": 200,
        "waterfall_min_drop": 3.5,
        "coast_mouth_dig": 1.0,
        "enforce_downhill": 1.0,
        "floodplain_width": 0.3,
        "meander_strength": 1.0,
        "river_logic": True,
    },
    "biomes": {
        "highland_band": 40.0,
        "alpine_band": 90.0,
        "peak_band": 160.0,
        "beach_height": 4.0,
        "beach_max_slope": 0.35,
        "shallow_depth": 14.0,
        "deep_depth": 50.0,
        "swamp_max_slope": 0.18,
        "swamp_max_elev": 12.0,
        "snowline_base": 190.0,
        "snowline_temp_drop": 60.0,
        "temp_optimum": 0.5,
        "moisture_exponent": 0.9,
        "fertility_slope_cut": 1.2,
        "fertility_alt_cut": 200.0,
        "population_slope_cut": 1.45,
        "water_influence_cells": 16.0,
        "water_bonus": 0.3,
    },
    "population": {
        "poi_count": 0,
        "tree_density": 1.0,
        "shrub_density": 1.0,
        "flower_density": 0.6,
        "boulder_density": 0.35,
        "ore_veins": 90,
        "ore_vein_radius": 3.2,
    },
    "surface": {
        "detail_amplitude": 1.2,
        "detail_scale": 42.0,
        "micro_scale": 11.0,
        "micro_amplitude": 0.4,
        "rock_patch_scale": 60.0,
        "snow_line_softness": 6.0,
        "beach_sand_depth": 3.0,
        "caves": 0.5,
        "cave_scale": 78.0,
        "cave_threshold": 0.74,
        "cave_min_depth": 6.0,
        "cave_max_y": 90.0,
        "ravines": 0.16,
        "ravine_scale": 260.0,
        "water_depth": 1.0,
        "ice_on_water": True,
        "deepslate_start": 8,
        "vegetation_in_export": 1.0,
        "structures_in_export": 0.0,
    },
    # The specification's landforms are already the erosion product - a caldera ring, a
    # quarried bench, a barchan field - so the pipeline only grades slopes and cuts
    # channels.  Heavy global erosion would sand the Obsidian Throne down and punch the
    # abyssal floor through Y = -32.
    "erosion": {
        "enabled": True,
        "diffusion": 0.10,
        "thermal_iterations": 6,
        "thermal_rate": 0.15,
        "fluvial_iterations": 6,
        "fluvial_k": 0.50,
        "fluvial_m": 0.6,
        "fluvial_n": 1.0,
        "max_incision": 0.6,
        "droplets": 0,
        "deposition": 0.5,
        "channel_smoothing": 1.2,
    },
    "export": {
        "output_dir": "",
        "world_name": "Ashenfall",
        "dimension": "overworld",
        "min_y": -64,
        "max_y": 320,
        "version": "1.21",
        "data_version": 3953,
        "compression": 2,
        "chunk_batch": 512,
        "write_level_dat": True,
        "write_bundle": True,
        "bundle_maps": ["height", "biome", "climate", "population", "flow", "water", "soil"],
        "write_schematic": False,
        "structures": False,
        "vegetation": False,
        # spec §5 method 1: chunks stay undecorated so Still Life's placed features run
        # natively; the populate mask tells the game where.
        "decoration": "mods",
        "write_populate_mask": True,
        "treeline": 225.0,
        "snowline": 200.0,
        "populate_noise_size": 64.0,
        "generate_png_maps": True,
    },
    "preview": {
        "mesh_size": 256,
        "vertex_stride": 2,
        "show_water": True,
        "show_biomes": True,
        "show_flow": False,
        "show_roads": False,
        "show_pois": False,
        "sun_angle": 45.0,
        "sun_azimuth": 315.0,
        "exaggeration": 1.0,
        "water_opacity": 0.78,
        "maps": ["height", "biome", "water", "population", "slope", "hillshade"],
    },
}
