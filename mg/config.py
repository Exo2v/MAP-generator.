"""Configuration: defaults, presets and merging.

The whole generator is driven by one plain nested dict, which is what the UI edits and
what gets saved next to an exported world as ``mapgen.json`` so a world can always be
regenerated bit-for-bit from its seed.
"""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .core.materials import BLOCKS
from .core.types import RegionInfo
from .core.erosion import hydraulic_erosion
from .generation.biomes import DEFAULTS as BIOME_DEFAULTS
from .generation.climate import ClimateModel
from .generation.population import DEFAULT_POPULATION
from .generation.surface import DEFAULT_SURFACE
from .generation.terrain import DEFAULT_TERRAIN
from .generation.water import DEFAULT_WATER


@dataclass
class GenerationConfig:
    """Everything needed to generate (and later reproduce) a world."""

    seed: int = 20240929
    name: str = "New World"
    preset: str = "cinematic"
    region: RegionInfo = field(default_factory=RegionInfo)
    terrain: Dict[str, Any] = field(default_factory=dict)
    climate: Dict[str, Any] = field(default_factory=dict)
    water: Dict[str, Any] = field(default_factory=dict)
    biomes: Dict[str, Any] = field(default_factory=dict)
    population: Dict[str, Any] = field(default_factory=dict)
    surface: Dict[str, Any] = field(default_factory=dict)
    erosion: Dict[str, Any] = field(default_factory=dict)
    export: Dict[str, Any] = field(default_factory=dict)
    preview: Dict[str, Any] = field(default_factory=dict)

    # -- serialisation -----------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "seed": self.seed,
            "name": self.name,
            "preset": self.preset,
            "region": {
                "x0": self.region.x0,
                "z0": self.region.z0,
                "blocks_x": self.region.blocks_x,
                "blocks_z": self.region.blocks_z,
                "cell_size": self.region.cell_size,
            },
            "terrain": copy.deepcopy(self.terrain),
            "climate": copy.deepcopy(self.climate),
            "water": copy.deepcopy(self.water),
            "biomes": copy.deepcopy(self.biomes),
            "population": copy.deepcopy(self.population),
            "surface": copy.deepcopy(self.surface),
            "erosion": copy.deepcopy(self.erosion),
            "export": copy.deepcopy(self.export),
            "preview": copy.deepcopy(self.preview),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GenerationConfig":
        cfg = default_config()
        data = data or {}
        cfg.seed = int(data.get("seed", cfg.seed))
        cfg.name = str(data.get("name", cfg.name))
        cfg.preset = str(data.get("preset", cfg.preset))
        region = data.get("region") or {}
        cfg.region = RegionInfo(
            x0=int(region.get("x0", cfg.region.x0)),
            z0=int(region.get("z0", cfg.region.z0)),
            blocks_x=int(region.get("blocks_x", cfg.region.blocks_x)),
            blocks_z=int(region.get("blocks_z", cfg.region.blocks_z)),
            cell_size=int(region.get("cell_size", cfg.region.cell_size)),
        )
        for key in ("terrain", "climate", "water", "biomes", "population", "surface",
                    "erosion", "export", "preview"):
            section = data.get(key)
            if isinstance(section, dict):
                getattr(cfg, key).update(section)
        return cfg


DEFAULT_EROSION = {
    "enabled": True,
    "diffusion": 0.45,
    "thermal_iterations": 16,
    "thermal_rate": 0.26,
    "fluvial_iterations": 12,
    "fluvial_k": 0.55,
    "fluvial_m": 0.6,
    "fluvial_n": 1.0,
    "max_incision": 1.2,
    "droplets": 0,
    "deposition": 0.5,
    "channel_smoothing": 1.1,
}

DEFAULT_CLIMATE = {
    "sea_level": 63.0,
    "region_scale": 0.0,          # 0 = derive from the region size
    "region_scale_factor": 2.4,
    "region_octaves": 4,
    "temperature_bias": 0.0,
    "humidity_bias": 0.0,
    "latitude_span": 1.0,
    "polar_curve": 2.1,
    "latitude_band_noise": 0.45,
    "lapse_rate_per_block": 1.0 / 190.0,
    "wind_direction": 90.0,
    "wind_speed": 0.55,
    "moisture_steps": 26,
    "rain_shadow_strength": 1.0,
}

DEFAULT_EXPORT = {
    "output_dir": "",
    "world_name": "",
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
    "schematic_chunks": 4,
    "structures": True,
    "vegetation": True,
    "caves": True,
    "spawn_in_center": True,
    "generate_png_maps": True,
}

DEFAULT_PREVIEW = {
    "mesh_size": 192,
    "vertex_stride": 2,
    "show_water": True,
    "show_biomes": True,
    "show_flow": False,
    "show_roads": True,
    "show_pois": True,
    "sun_angle": 45.0,
    "sun_azimuth": 315.0,
    "exaggeration": 1.0,
    "water_opacity": 0.78,
    "maps": ["height", "biome", "water", "flow", "population", "climate", "slope",
             "hillshade", "discharge"],
}


def default_config() -> GenerationConfig:
    return GenerationConfig(
        terrain=copy.deepcopy(DEFAULT_TERRAIN),
        climate=copy.deepcopy(DEFAULT_CLIMATE),
        water=copy.deepcopy(DEFAULT_WATER),
        biomes=copy.deepcopy(BIOME_DEFAULTS),
        population=copy.deepcopy(DEFAULT_POPULATION),
        surface=copy.deepcopy(DEFAULT_SURFACE),
        erosion=copy.deepcopy(DEFAULT_EROSION),
        export=copy.deepcopy(DEFAULT_EXPORT),
        preview=copy.deepcopy(DEFAULT_PREVIEW),
    )


# --------------------------------------------------------------------------------------
# Presets
# --------------------------------------------------------------------------------------


def _preset(**kw) -> Dict[str, Any]:
    return kw


PRESETS: Dict[str, Dict[str, Any]] = {
    "cinematic": _preset(
        label="Cinematic continents",
        description="Lithosphere-style big smooth continents, wide beaches, deep valleys.",
        terrain={"continent_region_factor": 2.9, "land_fraction": 0.58, "coast_sharpness": 0.18,
                 "mountain_region_factor": 1.15, "mountain_height": 152.0, "rolling_amp": 30.0,
                 "diffusion": 0.38, "cliff_amount": 0.75, "coast_smoothing": 0.9},
        climate={"latitude_span": 1.3, "wind_speed": 0.5},
        water={"river_threshold": 420.0, "bank_flare": 3.0, "valley_depth": 1.15},
        erosion={"diffusion": 0.5, "thermal_iterations": 18, "fluvial_k": 0.5},
    ),
    "archipelago": _preset(
        label="Archipelago",
        description="Scattered islands, shallow seas, lots of coastline and lagoons.",
        terrain={"continent_region_factor": 1.5, "land_fraction": 0.34, "island_count": 40,
                 "island_height": 42.0, "island_scale_factor": 0.30, "continent_octaves": 6,
                 "mountain_height": 96.0, "diffusion": 0.5, "coast_smoothing": 1.1},
        climate={"humidity_bias": 0.12, "wind_speed": 0.7},
        water={"river_threshold": 260.0, "lake_min_cells": 4},
        erosion={"diffusion": 0.62, "thermal_iterations": 20, "fluvial_k": 0.4},
    ),
    "highland": _preset(
        label="Highland frontier",
        description="Dense snaking mountain chains, high plateaus, deep glacial valleys.",
        terrain={"mountain_region_factor": 0.85, "mountain_threshold": 0.6,
                 "mountain_height": 190.0, "mountain_ridge_power": 1.2, "plateau_amount": 0.45,
                 "land_fraction": 0.78, "rolling_amp": 38.0, "cliff_amount": 0.9,
                 "diffusion": 0.25, "snow_line_softness": 8.0},
        climate={"lapse_rate_per_block": 1.0 / 150.0, "latitude_span": 1.4},
        water={"river_threshold": 300.0, "valley_depth": 1.5, "bank_flare": 2.0,
               "waterfall_min_drop": 3.0},
        erosion={"thermal_iterations": 22, "fluvial_k": 0.75, "max_incision": 1.6,
                 "diffusion": 0.3},
    ),
    "rainforest": _preset(
        label="Rainforest basin",
        description="Hot, soaked, huge rivers and mangrove deltas - Still Life at its lushest.",
        terrain={"land_fraction": 0.85, "mountain_height": 120.0, "rolling_amp": 22.0,
                 "diffusion": 0.45, "plateau_amount": 0.12},
        climate={"temperature_bias": 0.28, "humidity_bias": 0.3, "rain_shadow_strength": 1.3,
                 "moisture_steps": 32},
        water={"river_threshold": 200.0, "climate_river_bias": 0.9, "floodplain_width": 0.55,
               "bank_flare": 3.4},
        biomes={"swamp_max_elev": 14.0},
    ),
    "desert": _preset(
        label="Arid world",
        description="Xeric shrubland, mesas, salt flats and rare oasis rivers.",
        terrain={"land_fraction": 0.88, "mountain_height": 130.0, "cliff_amount": 1.0,
                 "mountain_ridge_power": 1.6, "plateau_amount": 0.6, "rolling_amp": 20.0,
                 "terrace": 0.18, "diffusion": 0.2, "roughness_arid_boost": 0.7},
        climate={"temperature_bias": 0.24, "humidity_bias": -0.34, "rain_shadow_strength": 1.5},
        water={"river_threshold": 900.0, "climate_river_bias": 1.2, "lake_min_cells": 8},
    ),
    "worldmachine": _preset(
        label="World Machine study",
        description="Deliberately eroded, carved, layered - closest to an artist's WM graph.",
        terrain={"continent_region_factor": 2.2, "mountain_region_factor": 0.9,
                 "mountain_height": 175.0, "plateau_amount": 0.5, "plateau_steps": 6,
                 "terrace": 0.08, "diffusion": 0.18, "detail_amp": 4.5, "rolling_amp": 34.0},
        climate={"rain_shadow_strength": 1.1, "latitude_span": 1.2},
        water={"river_threshold": 280.0, "carve_passes": 3, "bank_flare": 2.0,
               "valley_depth": 1.35},
        erosion={"droplets": 60000, "fluvial_k": 0.8, "fluvial_iterations": 16,
                 "thermal_iterations": 24, "diffusion": 0.24, "channel_smoothing": 1.4},
    ),
    "floating_plateau": _preset(
        label="Terrace plateau",
        description="Stylised stepped mesas and calm lakes - good for building maps.",
        terrain={"terrace": 0.65, "plateau_amount": 0.7, "plateau_steps": 8, "land_fraction": 0.9,
                 "mountain_height": 110.0, "rolling_amp": 16.0, "diffusion": 0.55,
                 "cliff_amount": 0.35},
        water={"river_threshold": 500.0, "lake_min_depth": 1.4, "lake_min_cells": 8},
        erosion={"thermal_iterations": 10, "fluvial_iterations": 6, "diffusion": 0.6},
    ),
}


def apply_preset(cfg: GenerationConfig, preset: str) -> GenerationConfig:
    data = PRESETS.get(preset)
    if not data:
        return cfg
    cfg.preset = preset
    for key, value in data.items():
        if key in ("label", "description"):
            continue
        section = getattr(cfg, key, None)
        if isinstance(section, dict):
            section.update(value)
    return cfg


def preset_list() -> List[Dict[str, str]]:
    return [
        {"id": k, "label": v.get("label", k), "description": v.get("description", "")}
        for k, v in PRESETS.items()
    ]


def save_config(cfg: GenerationConfig, path: str) -> str:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg.to_dict(), fh, indent=2)
    return path


def load_config(path: str) -> GenerationConfig:
    with open(path, "r", encoding="utf-8") as fh:
        return GenerationConfig.from_dict(json.load(fh))


# --------------------------------------------------------------------------------------
# Validation / derived values
# --------------------------------------------------------------------------------------


def validate(cfg: GenerationConfig) -> List[str]:
    """Sanity-check a config; returns a list of human readable warnings/errors."""
    problems: List[str] = []
    r = cfg.region
    if r.blocks_x < 128 or r.blocks_z < 128:
        problems.append("Region must be at least 128 x 128 blocks.")
    if r.cell_size < 1 or r.cell_size > 16:
        problems.append("Cell size must be between 1 and 16 blocks.")
    cells = r.cells_x * r.cells_z
    if cells > 4_000_000:
        problems.append(
            f"Region has {cells:,} simulation cells - generation will be very slow "
            "(consider a larger cell size)."
        )
    sea = cfg.climate.get("sea_level", 63.0)
    if not (0 < sea < 250):
        problems.append("Sea level must be between 0 and 250.")
    if cfg.water.get("river_threshold", 300) <= 1:
        problems.append("River threshold must be greater than 1 cell.")
    return problems


def resolve_derived(cfg: GenerationConfig) -> GenerationConfig:
    """Fill in values that depend on the region size (noise scales etc.)."""
    R = float(max(128, min(cfg.region.blocks_x, cfg.region.blocks_z)))
    cl = cfg.climate
    if not cl.get("region_scale"):
        cl["region_scale"] = max(R * float(cl.get("region_scale_factor", 2.4)), 1200.0)
    cfg.terrain.setdefault("sea_level", cl.get("sea_level", 63.0))
    cfg.water.setdefault("sea_level", cl.get("sea_level", 63.0))
    return cfg
