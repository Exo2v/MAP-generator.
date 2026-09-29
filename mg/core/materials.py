"""Block palette + per-biome surface profiles.

Two jobs:

1. **The palette** - every block this generator can emit, with a stable numeric id so
   the preview and the exporter agree.  Names are Minecraft 1.20.1 ids; ``BLOCK_ALIASES``
   handles the few blocks that were renamed in later versions ("grass" -> "short_grass")
   so the same preset exports to a modern world without manual edits.

2. **Surface profiles** - the World Painter "layer" concept: for each biome we decide
   the surface block, the soil layer beneath it, how deep that layer goes, the rock
   below, and any snow / water / vegetation dressing.  Profiles are resolved from
   climate as well as biome, so a cold desert gets gravel where a hot one gets sand.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .types import BIOMES, BIOME_INDEX

# --------------------------------------------------------------------------------------
# Palette
# --------------------------------------------------------------------------------------

#: Ordered block names.  Index in this list + 1 is the numeric id used everywhere.
#: Index 0 is always air.
BLOCKS: List[str] = [
    "air",  # 0
    # rock / ground
    "bedrock", "stone", "deepslate", "granite", "diorite", "andesite", "tuff", "calcite",
    "dirt", "coarse_dirt", "rooted_dirt", "mud", "grass_block", "podzol", "mycelium",
    "moss_block", "clay", "gravel", "sand", "red_sand", "sandstone", "red_sandstone",
    "terracotta", "white_terracotta", "orange_terracotta", "yellow_terracotta",
    "brown_terracotta", "red_terracotta", "light_gray_terracotta", "muddy_mangrove_roots",
    "snow_block", "snow", "powder_snow", "ice", "packed_ice", "blue_ice",
    "basalt", "smooth_basalt", "blackstone", "obsidian", "magma_block", "dripstone_block",
    "crimson_nylium", "warped_nylium", "twisting_vines", "weeping_vines", "soul_sand",
    "amethyst_block", "flowering_azalea_leaves", "glow_lichen", "bone_block",
    "black_terracotta", "black_glazed_terracotta", "honeycomb_block",
    # liquids / gases
    "water", "lava",
    # ores
    "coal_ore", "iron_ore", "copper_ore", "gold_ore", "redstone_ore", "lapis_ore",
    "diamond_ore", "emerald_ore", "deepslate_coal_ore", "deepslate_iron_ore",
    "deepslate_copper_ore", "deepslate_gold_ore", "deepslate_redstone_ore",
    "deepslate_lapis_ore", "deepslate_diamond_ore",
    # wood
    "oak_log", "birch_log", "spruce_log", "jungle_log", "acacia_log", "dark_oak_log",
    "mangrove_log", "cherry_log",
    "oak_leaves", "birch_leaves", "spruce_leaves", "jungle_leaves", "acacia_leaves",
    "dark_oak_leaves", "mangrove_leaves", "cherry_leaves",
    # fungal wood (the Whispering Fen's "giant fungal heartwood")
    "crimson_stem", "warped_stem", "crimson_hyphae", "warped_hyphae",
    "nether_wart_block", "warped_wart_block", "shroomlight",
    # reef / drowned-shelf dressing for the Sunken Reach
    "tube_coral_block", "brain_coral_block", "bubble_coral_block", "fire_coral_block",
    "horn_coral_block", "prismarine", "prismarine_bricks", "sea_lantern",
    "oak_planks", "spruce_planks", "dark_oak_planks", "cobblestone", "mossy_cobblestone",
    "stone_bricks", "mossy_stone_bricks", "deepslate_bricks", "bricks", "glass",
    "glass_pane", "oak_fence", "oak_door", "oak_stairs", "oak_slab", "torch", "lantern",
    "campfire", "crafting_table", "barrel", "bookshelf", "hay_block", "cobweb",
    "mossy_cobblestone_wall", "stone_brick_wall", "iron_bars", "chest", "bell",
    # plants
    "grass", "tall_grass", "fern", "large_fern", "dead_bush", "dandelion", "poppy",
    "cornflower", "oxeye_daisy", "azure_bluet", "allium", "blue_orchid", "lily_of_the_valley",
    "cactus", "bamboo", "sugar_cane", "seagrass", "kelp", "lily_pad", "sweet_berry_bush",
    "red_mushroom", "brown_mushroom", "sunflower", "lilac", "rose_bush", "peony",
    "vine", "glow_lichen", "spore_blossom", "moss_carpet", "brown_mushroom_block",
    "red_mushroom_block", "mushroom_stem", "pink_petals", "cherry_sapling",
]

BLOCK_ID: Dict[str, int] = {name: i for i, name in enumerate(BLOCKS)}
ID_BLOCK: Dict[int, str] = {i: name for i, name in enumerate(BLOCKS)}

AIR = 0
WATER = BLOCK_ID["water"]

#: Blocks renamed across versions: export-time renames (id -> version specific name).
BLOCK_ALIASES: Dict[str, Dict[str, str]] = {
    "1.20": {"grass": "grass", "short_grass": "grass"},
    "1.21": {"grass": "short_grass", "short_grass": "short_grass"},
}

#: Blocks that cannot be placed without support / that we never want in the export.
UNSUPPORTED = {"air"}


def block_id(name: str) -> int:
    """Look an id up by name, accepting ``minecraft:`` prefixes and aliases."""
    n = name.split(":")[-1]
    synonyms = {
        "tin_ore": "copper_ore",
        "silver_ore": "iron_ore",
        "sulfur_ore": "coal_ore",
        "grass_block_top": "grass_block",
        "short_grass": "grass",
        "oak_wood": "oak_log",
        "stone_andesite": "andesite",
        "snow_layer": "snow",
        "gravel_ore": "gravel",
        "diamond_block": "stone",
    }
    n = synonyms.get(n, n)
    if n in BLOCK_ID:
        return BLOCK_ID[n]
    # unknown modded block -> reasonable vanilla stand-in
    if n.endswith("_log"):
        return BLOCK_ID["oak_log"]
    if n.endswith("_leaves"):
        return BLOCK_ID["oak_leaves"]
    if n.endswith("_ore"):
        return BLOCK_ID["iron_ore"]
    if n.endswith("_planks"):
        return BLOCK_ID["oak_planks"]
    return BLOCK_ID["stone"]


def block_name(idx: int) -> str:
    return ID_BLOCK.get(int(idx), "stone")


def namespaced(idx: int, version: str = "1.21") -> str:
    """Fully qualified block id for the requested Minecraft version."""
    name = block_name(idx)
    alias = BLOCK_ALIASES.get(version, {})
    name = alias.get(name, name)
    return f"minecraft:{name}"


def blocks_used(*ids: int) -> List[int]:
    return sorted({int(i) for i in ids})


# --------------------------------------------------------------------------------------
# Surface profiles
# --------------------------------------------------------------------------------------


@dataclass
class SurfaceProfile:
    """What the top of the world is made of in a given biome/climate."""

    top: str = "grass_block"
    filler: str = "dirt"
    filler_depth: Tuple[int, int] = (3, 5)  # inclusive range in blocks
    subsoil: str = ""  # optional band between soil and stone
    subsoil_depth: Tuple[int, int] = (0, 0)
    stone: str = "stone"
    stone_blend: str = "andesite"  # mixed into the stone for variation
    deep_stone: str = "deepslate"
    surface_snow: int = 0  # snow layers on top
    water_block: str = "water"
    icicle: bool = False
    sand_band: int = 0  # blocks of beach sand near the waterline
    rocky_noise: float = 0.25  # chance of exposed rock patches
    vegetation: Tuple[str, ...] = ()
    ground_cover: Tuple[str, ...] = ()


def _p(name: str) -> SurfaceProfile:
    return PROFILES[name]


PROFILES: Dict[str, SurfaceProfile] = {
    "default": SurfaceProfile(),
    # ---- oceans -------------------------------------------------------------------
    "deep_ocean": SurfaceProfile(top="gravel", filler="gravel", filler_depth=(1, 3),
                                 stone="stone", deep_stone="deepslate", rocky_noise=0.1),
    "ocean": SurfaceProfile(top="sand", filler="sand", filler_depth=(2, 5), rocky_noise=0.3),
    "shallow_coast": SurfaceProfile(top="sand", filler="sand", filler_depth=(2, 4)),
    "beach": SurfaceProfile(top="sand", filler="sand", filler_depth=(3, 6), rocky_noise=0.05,
                            vegetation=("sugar_cane",)),
    "snowy_beach": SurfaceProfile(top="snow_block", filler="sand", filler_depth=(2, 4),
                                  surface_snow=1),
    "stony_shore": SurfaceProfile(top="gravel", filler="gravel", filler_depth=(1, 3),
                                  stone="stone", rocky_noise=0.75),
    # ---- water bodies --------------------------------------------------------------
    "river": SurfaceProfile(top="sand", filler="sand", filler_depth=(1, 3)),
    "lake": SurfaceProfile(top="clay", filler="clay", filler_depth=(1, 3)),
    "frozen_river": SurfaceProfile(top="gravel", filler="gravel", filler_depth=(1, 3),
                                   surface_snow=0, icicle=True),
    # ---- cold ----------------------------------------------------------------------
    "snowy_plains": SurfaceProfile(top="grass_block", filler="dirt", surface_snow=1,
                                   ground_cover=("grass",)),
    "snowy_taiga": SurfaceProfile(top="grass_block", filler="dirt", surface_snow=1,
                                  ground_cover=("fern", "grass")),
    "grove": SurfaceProfile(top="snow_block", filler="dirt", surface_snow=1, rocky_noise=0.35),
    "cold_shrubland": SurfaceProfile(top="grass_block", filler="coarse_dirt", rocky_noise=0.35,
                                     ground_cover=("grass", "dead_bush")),
    "cold_mountains": SurfaceProfile(top="grass_block", filler="gravel", stone="stone",
                                     rocky_noise=0.7, surface_snow=0, ground_cover=("grass",)),
    "glacier": SurfaceProfile(top="packed_ice", filler="ice", filler_depth=(4, 12),
                              stone="stone", surface_snow=2, rocky_noise=0.0),
    "alpine_peaks": SurfaceProfile(top="snow_block", filler="stone", filler_depth=(1, 2),
                                   surface_snow=1, rocky_noise=0.8),
    # ---- temperate ------------------------------------------------------------------
    "plains": SurfaceProfile(top="grass_block", filler="dirt",
                             ground_cover=("grass", "grass", "dandelion", "poppy", "cornflower")),
    "meadow": SurfaceProfile(top="grass_block", filler="dirt",
                             ground_cover=("grass", "dandelion", "oxeye_daisy", "allium",
                                           "azure_bluet", "cornflower", "sunflower")),
    "fertile_valley": SurfaceProfile(top="grass_block", filler="dirt",
                                     ground_cover=("grass", "tall_grass", "poppy", "dandelion",
                                                   "oxeye_daisy")),
    "temperate_forest": SurfaceProfile(top="grass_block", filler="dirt",
                                       ground_cover=("grass", "fern", "red_mushroom",
                                                     "brown_mushroom", "lily_of_the_valley")),
    "old_growth_temperate_forest": SurfaceProfile(top="podzol", filler="dirt",
                                                  ground_cover=("fern", "large_fern",
                                                                "red_mushroom", "brown_mushroom",
                                                                "grass")),
    "windswept_hills": SurfaceProfile(top="grass_block", filler="coarse_dirt", rocky_noise=0.5,
                                      ground_cover=("grass", "fern")),
    "temperate_mountains": SurfaceProfile(top="grass_block", filler="gravel", rocky_noise=0.7,
                                          stone="stone", ground_cover=("grass", "fern")),
    "highland_steppe": SurfaceProfile(top="coarse_dirt", filler="coarse_dirt", rocky_noise=0.45,
                                      ground_cover=("grass", "dead_bush")),
    "swamp": SurfaceProfile(top="grass_block", filler="mud", filler_depth=(3, 6),
                            subsoil="clay", subsoil_depth=(1, 2),
                            water_block="water", ground_cover=("grass", "lily_pad",
                                                               "brown_mushroom", "fern")),
    "mangrove_swamp": SurfaceProfile(top="muddy_mangrove_roots", filler="mud",
                                     filler_depth=(3, 6), subsoil="clay",
                                     ground_cover=("grass", "lily_pad", "vine")),
    # ---- warm -----------------------------------------------------------------------
    "taiga": SurfaceProfile(top="podzol", filler="dirt", ground_cover=("fern", "grass",
                                                                       "sweet_berry_bush")),
    "old_growth_taiga": SurfaceProfile(top="podzol", filler="coarse_dirt",
                                       ground_cover=("fern", "large_fern", "sweet_berry_bush",
                                                     "brown_mushroom")),
    "humid_savanna": SurfaceProfile(top="grass_block", filler="dirt",
                                    ground_cover=("grass", "tall_grass", "sunflower")),
    "savanna": SurfaceProfile(top="grass_block", filler="coarse_dirt", rocky_noise=0.3,
                              ground_cover=("grass", "tall_grass")),
    "xeric_shrubland": SurfaceProfile(top="coarse_dirt", filler="gravel", rocky_noise=0.5,
                                      ground_cover=("dead_bush", "grass")),
    "warm_temperate_mountains": SurfaceProfile(top="grass_block", filler="gravel",
                                               rocky_noise=0.6, ground_cover=("grass", "fern")),
    "fertile_hills": SurfaceProfile(top="grass_block", filler="dirt",
                                    ground_cover=("grass", "poppy", "dandelion")),
    # ---- hot / dry -------------------------------------------------------------------
    "desert": SurfaceProfile(top="sand", filler="sand", filler_depth=(4, 9),
                             subsoil="sandstone", subsoil_depth=(3, 6), rocky_noise=0.0,
                             ground_cover=("dead_bush", "cactus")),
    "badlands_mesa": SurfaceProfile(top="red_sand", filler="terracotta", filler_depth=(3, 8),
                                    subsoil="white_terracotta", subsoil_depth=(2, 5),
                                    rocky_noise=0.2, ground_cover=("dead_bush",)),
    "arid_mountains": SurfaceProfile(top="coarse_dirt", filler="gravel", stone="stone",
                                     rocky_noise=0.8, ground_cover=("dead_bush",)),
    "salt_flats": SurfaceProfile(top="white_terracotta", filler="sand", filler_depth=(1, 3),
                                 rocky_noise=0.0),
    "volcanic_highland": SurfaceProfile(top="basalt", filler="blackstone", filler_depth=(2, 6),
                                        stone="basalt", deep_stone="deepslate",
                                        rocky_noise=0.5, ground_cover=("dead_bush",)),
    # ---- tropics ---------------------------------------------------------------------
    "jungle": SurfaceProfile(top="grass_block", filler="dirt",
                             ground_cover=("grass", "tall_grass", "fern", "large_fern",
                                           "bamboo", "vine", "brown_mushroom")),
    "tropical_rainforest": SurfaceProfile(top="grass_block", filler="dirt",
                                          ground_cover=("grass", "large_fern", "fern",
                                                        "bamboo", "vine", "red_mushroom",
                                                        "brown_mushroom")),
    "sparse_jungle": SurfaceProfile(top="grass_block", filler="coarse_dirt",
                                    ground_cover=("grass", "tall_grass", "fern")),
    # ---- Ashenfall regions (spec §2 landmark table + §6 surface rule) -----------------
    "wooded_badlands": SurfaceProfile(top="orange_terracotta", filler="terracotta",
                                      filler_depth=(3, 6), subsoil="yellow_terracotta",
                                      subsoil_depth=(2, 5), rocky_noise=0.35,
                                      ground_cover=("dead_bush", "grass")),
    "eroded_badlands": SurfaceProfile(top="terracotta", filler="red_sand",
                                      filler_depth=(2, 5), subsoil="white_terracotta",
                                      subsoil_depth=(2, 4), rocky_noise=0.45,
                                      ground_cover=("dead_bush",)),
    "badlands": SurfaceProfile(top="orange_terracotta", filler="terracotta",
                               filler_depth=(3, 6), rocky_noise=0.3,
                               ground_cover=("dead_bush",)),
    # spec §2: basalt / blackstone / magma_block / obsidian
    "basalt_deltas": SurfaceProfile(top="basalt", filler="blackstone", filler_depth=(3, 8),
                                    stone="basalt", deep_stone="deepslate",
                                    rocky_noise=0.55, ground_cover=("dead_bush",)),
    # spec §2: snow_block / packed_ice / calcite / stone
    "frozen_peaks": SurfaceProfile(top="snow_block", filler="packed_ice",
                                   filler_depth=(2, 4), subsoil="calcite",
                                   surface_snow=1, rocky_noise=0.4),
    "jagged_peaks": SurfaceProfile(top="stone", filler="gravel", filler_depth=(1, 3),
                                   subsoil="calcite", surface_snow=1, rocky_noise=0.7),
    # spec §2: cherry terraces / stone / gilded ruins
    "cherry_grove": SurfaceProfile(top="grass_block", filler="dirt",
                                   ground_cover=("grass", "pink_petals", "allium")),
    # spec §2: sand / coral reefs / prismarine gravel
    "warm_ocean": SurfaceProfile(top="sand", filler="sand", filler_depth=(2, 5),
                                 subsoil="prismarine", subsoil_depth=(2, 4),
                                 rocky_noise=0.0),
    "lukewarm_ocean": SurfaceProfile(top="sand", filler="sand", filler_depth=(2, 5),
                                     subsoil="prismarine", subsoil_depth=(1, 3)),
    # spec §2: gravel / deepslate / packed salt crust
    "deep_cold_ocean": SurfaceProfile(top="gravel", filler="gravel", filler_depth=(2, 5),
                                      subsoil="deepslate", subsoil_depth=(3, 8),
                                      rocky_noise=0.3),
}

# climates that override the biome default (cold desert -> gravel, etc.)
CLIMATE_OVERRIDES = {
    "cold_dry": SurfaceProfile(top="gravel", filler="gravel", filler_depth=(1, 3),
                               rocky_noise=0.6, ground_cover=("dead_bush",)),
    "cold_wet": SurfaceProfile(top="podzol", filler="dirt", ground_cover=("fern", "grass")),
    "hot_wet": SurfaceProfile(top="grass_block", filler="mud", filler_depth=(3, 6),
                              ground_cover=("grass", "large_fern", "vine")),
    "hot_dry": SurfaceProfile(top="sand", filler="sandstone", filler_depth=(3, 6),
                              rocky_noise=0.1, ground_cover=("dead_bush",)),
    "temperate_dry": SurfaceProfile(top="coarse_dirt", filler="coarse_dirt",
                                    ground_cover=("grass", "dead_bush")),
}


def profile_for(biome_idx: int, *, temperature: float, humidity: float,
                snowline_hit: bool = False, underwater: bool = False) -> SurfaceProfile:
    """Resolve the profile for a biome, nudged by the local climate."""
    name = BIOMES[int(biome_idx)] if 0 <= int(biome_idx) < len(BIOMES) else "default"
    base = PROFILES.get(name, PROFILES["default"])

    if underwater:
        return base

    if snowline_hit:
        cold = SurfaceProfile(**{**base.__dict__})
        cold.top = "snow_block"
        cold.surface_snow = max(1, cold.surface_snow)
        return cold

    return base


# --------------------------------------------------------------------------------------
# Ore blocks per depth
# --------------------------------------------------------------------------------------

ORE_BLOCKS: Dict[str, Dict[str, str]] = {
    "coal": {"stone": "coal_ore", "deepslate": "deepslate_coal_ore"},
    "iron": {"stone": "iron_ore", "deepslate": "deepslate_iron_ore"},
    "copper": {"stone": "copper_ore", "deepslate": "deepslate_copper_ore"},
    "gold": {"stone": "gold_ore", "deepslate": "deepslate_gold_ore"},
    "redstone": {"stone": "redstone_ore", "deepslate": "deepslate_redstone_ore"},
    "lapis": {"stone": "lapis_ore", "deepslate": "deepslate_lapis_ore"},
    "diamond": {"stone": "diamond_ore", "deepslate": "deepslate_diamond_ore"},
    "emerald": {"stone": "emerald_ore", "deepslate": "emerald_ore"},
}


def ore_block(ore: str, deep: bool) -> int:
    table = ORE_BLOCKS.get(ore, ORE_BLOCKS["iron"])
    return BLOCK_ID[table["deepslate" if deep else "stone"]]
