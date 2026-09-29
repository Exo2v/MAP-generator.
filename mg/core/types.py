"""Shared data containers for the generation pipeline."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# --------------------------------------------------------------------------------------
# Minecraft constants
# --------------------------------------------------------------------------------------

MIN_Y = -64
MAX_Y = 320
SEA_LEVEL = 63

CHUNK = 16
REGION = 512

# --------------------------------------------------------------------------------------
# Region / tiling
# --------------------------------------------------------------------------------------


@dataclass
class RegionInfo:
    """A rectangular slice of the world.

    ``x0``/``z0`` are block coordinates of the min corner (inclusive),
    ``blocks_x``/``blocks_z`` the size in blocks and ``cell_size`` how many blocks
    one simulation cell covers.
    """

    x0: int = 0
    z0: int = 0
    blocks_x: int = 1024
    blocks_z: int = 1024
    cell_size: int = 4

    @property
    def cells_x(self) -> int:
        return max(1, int(math.ceil(self.blocks_x / self.cell_size)))

    @property
    def cells_z(self) -> int:
        return max(1, int(math.ceil(self.blocks_z / self.cell_size)))

    @property
    def x1(self) -> int:
        return self.x0 + self.blocks_x

    @property
    def z1(self) -> int:
        return self.z0 + self.blocks_z

    def as_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.update(cells_x=self.cells_x, cells_z=self.cells_z, x1=self.x1, z1=self.z1)
        return d


# --------------------------------------------------------------------------------------
# Terrain data
# --------------------------------------------------------------------------------------


@dataclass
class TerrainGrid:
    """The full state of a generated region at simulation resolution."""

    region: RegionInfo
    heights: np.ndarray  # (cells_z, cells_x) float32, absolute block Y of the ground
    water_level: np.ndarray  # (cells_z, cells_x) float32 - local water surface, NaN/-1 = dry
    temperature: np.ndarray  # 0..1 (1 = hot equator)
    humidity: np.ndarray  # 0..1 (1 = soaking wet)
    continentality: np.ndarray  # 0..1 (1 = deep inland)
    fertility: np.ndarray  # 0..1 (Still Life population driver)
    population: np.ndarray  # 0..1 (Still Life population density)
    biome: np.ndarray  # int16 biome index
    soil_depth: np.ndarray  # blocks of soil/material above bedrock
    flow_dir: np.ndarray  # (cells_z, cells_x, 2) float32 unit vectors, surface flow
    discharge: np.ndarray  # accumulated upstream area, in cells
    water_mask: np.ndarray  # uint8: 1 = river/stream, 2 = lake, 3 = ocean, 4 = waterfall
    rivers: List["RiverPath"] = field(default_factory=list)
    lakes: List["Lake"] = field(default_factory=list)
    pois: List["POI"] = field(default_factory=list)
    roads: List[List[Tuple[float, float]]] = field(default_factory=list)
    #: (n, 5) int32 vegetation records: cell_x, cell_z, species_id, height, kind
    vegetation: Optional[np.ndarray] = None
    species_table: List[str] = field(default_factory=list)
    veins: List[Any] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    # -- convenience -------------------------------------------------------------------
    @property
    def cells_z(self) -> int:
        return self.heights.shape[0]

    @property
    def cells_x(self) -> int:
        return self.heights.shape[1]

    def surface_height_at(self, cell_x: int, cell_z: int) -> float:
        return float(self.heights[cell_z, cell_x])

    def stats(self) -> Dict[str, Any]:
        h = self.heights
        wet = self.water_level > 0
        return {
            "min_height": float(np.min(h)),
            "max_height": float(np.max(h)),
            "mean_height": float(np.mean(h)),
            "relief": float(np.max(h) - np.min(h)),
            "water_cells": int(np.count_nonzero(wet)),
            "water_fraction": float(np.count_nonzero(wet) / h.size),
            "river_cells": int(np.count_nonzero(self.water_mask == 1)),
            "lake_cells": int(np.count_nonzero(self.water_mask == 2)),
            "ocean_cells": int(np.count_nonzero(self.water_mask == 3)),
            "river_count": len(self.rivers),
            "lake_count": len(self.lakes),
            "poi_count": len(self.pois),
            "road_count": len(self.roads),
            "tree_count": 0 if self.vegetation is None else int(len(self.vegetation)),
            "ore_vein_count": len(self.veins),
        }


@dataclass
class RiverPath:
    """A single stream/river, ordered from source to mouth."""

    points: np.ndarray  # (n, 3): world x, world z, surface height
    discharge: np.ndarray  # (n,) accumulated area at each point (cells)
    width: np.ndarray  # (n,) channel width in blocks
    order: int = 1  # Strahler order
    name: str = ""
    ends_in: str = "sea"  # sea | lake | sink

    @property
    def length(self) -> float:
        if len(self.points) < 2:
            return 0.0
        d = np.diff(self.points[:, :2], axis=0)
        return float(np.hypot(d[:, 0], d[:, 1]).sum())


@dataclass
class Lake:
    """A depression filled with water."""

    cells: np.ndarray  # (n, 2) cell coordinates
    surface: float  # water surface height in blocks
    volume: float  # in cells*blocks
    outlet: Optional[Tuple[float, float]] = None
    depth_max: float = 0.0


@dataclass
class POI:
    """A point of interest placed by the population layer."""

    x: float
    z: float
    y: float
    kind: str  # village | hamlet | camp | ruin | outpost | landmark | ...
    weight: float = 1.0
    biome: str = ""
    name: str = ""


@dataclass
class GenerationResult:
    """What the pipeline hands to the exporter / preview."""

    terrain: TerrainGrid
    config: Dict[str, Any]
    stages: List[Dict[str, Any]] = field(default_factory=list)
    diagnostics: Dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------------------
# Biomes
# --------------------------------------------------------------------------------------

# Still-Life-flavoured temperate biome names, bucketed by (temperature, humidity,
# elevation band).  Order matters: index 0 is used for unresolved cells.
BIOMES: List[str] = [
    "deep_ocean",
    "ocean",
    "shallow_coast",
    "beach",
    "stony_shore",
    "river",
    "lake",
    "frozen_river",
    "snowy_beach",
    "snowy_plains",
    "snowy_taiga",
    "grove",
    "taiga",
    "old_growth_taiga",
    "cold_mountains",
    "cold_shrubland",
    "plains",
    "meadow",
    "temperate_forest",
    "old_growth_temperate_forest",
    "temperate_mountains",
    "warm_temperate_mountains",
    "swamp",
    "mangrove_swamp",
    "humid_savanna",
    "savanna",
    "xeric_shrubland",
    "desert",
    "arid_mountains",
    "badlands_mesa",
    "jungle",
    "tropical_rainforest",
    "sparse_jungle",
    "highland_steppe",
    "alpine_peaks",
    "glacier",
    "volcanic_highland",
    "salt_flats",
    "windswept_hills",
    "fertile_valley",
]

#: Vanilla biomes the Ashenfall specification names directly for its regions.  They are
#: appended so every existing index stays put, and ``BIOME_TO_VANILLA`` in the exporter
#: resolves them to themselves.
ASHENFALL_BIOMES: List[str] = [
    "wooded_badlands",
    "eroded_badlands",
    "badlands",
    "basalt_deltas",
    "frozen_peaks",
    "jagged_peaks",
    "cherry_grove",
    "warm_ocean",
    "lukewarm_ocean",
    "deep_cold_ocean",
]
for _name in ASHENFALL_BIOMES:
    if _name not in BIOMES:
        BIOMES.append(_name)

BIOME_INDEX: Dict[str, int] = {name: i for i, name in enumerate(BIOMES)}

# Display colours used by the preview + biome raster export.
BIOME_COLORS: Dict[str, Tuple[int, int, int]] = {
    "wooded_badlands": (140, 88, 58),
    "eroded_badlands": (168, 96, 56),
    "badlands": (186, 108, 62),
    "basalt_deltas": (44, 42, 46),
    "frozen_peaks": (226, 236, 244),
    "jagged_peaks": (198, 210, 220),
    "cherry_grove": (232, 174, 196),
    "warm_ocean": (54, 140, 158),
    "lukewarm_ocean": (62, 152, 166),
    "deep_cold_ocean": (12, 40, 74),
    "deep_ocean": (12, 40, 82),
    "ocean": (24, 68, 128),
    "shallow_coast": (56, 122, 168),
    "beach": (222, 208, 158),
    "stony_shore": (128, 126, 120),
    "river": (56, 132, 190),
    "lake": (48, 118, 176),
    "frozen_river": (150, 196, 220),
    "snowy_beach": (232, 236, 240),
    "snowy_plains": (238, 242, 246),
    "snowy_taiga": (156, 176, 168),
    "grove": (176, 196, 190),
    "taiga": (94, 122, 88),
    "old_growth_taiga": (72, 100, 76),
    "cold_mountains": (168, 176, 180),
    "cold_shrubland": (150, 158, 130),
    "plains": (134, 172, 92),
    "meadow": (146, 186, 108),
    "temperate_forest": (78, 128, 66),
    "old_growth_temperate_forest": (58, 104, 58),
    "temperate_mountains": (140, 142, 134),
    "warm_temperate_mountains": (146, 140, 122),
    "swamp": (72, 96, 62),
    "mangrove_swamp": (60, 96, 72),
    "humid_savanna": (168, 178, 96),
    "savanna": (186, 176, 96),
    "xeric_shrubland": (166, 158, 108),
    "desert": (222, 206, 150),
    "arid_mountains": (176, 156, 122),
    "badlands_mesa": (176, 108, 72),
    "jungle": (48, 122, 56),
    "tropical_rainforest": (36, 104, 48),
    "sparse_jungle": (96, 142, 70),
    "highland_steppe": (162, 168, 128),
    "alpine_peaks": (206, 210, 214),
    "glacier": (238, 246, 252),
    "volcanic_highland": (104, 92, 88),
    "salt_flats": (232, 230, 220),
    "windswept_hills": (132, 156, 110),
    "fertile_valley": (118, 164, 86),
}


def biome_color(idx: int) -> Tuple[int, int, int]:
    if 0 <= idx < len(BIOMES):
        return BIOME_COLORS.get(BIOMES[idx], (128, 128, 128))
    return (128, 128, 128)
