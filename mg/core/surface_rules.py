"""Block-level biome rules.

The generation pipeline classifies biomes on the simulation grid (cheap, and that raster
is what the maps and the population layer consume).  At export time we need the same
biomes *without* the cell-sized staircase along every edge, so this module re-derives
them from the interpolated climate fields on the block lattice.

Keeping the two in one place stops them from drifting apart: :func:`biome_from_climate`
is the single source of truth for "what biome is this column", and
:class:`~mg.generation.biomes.BiomeClassifier` only adds the raster-level extras
(fertility, population, mountains, volcanoes) on top of it.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from .types import BIOME_INDEX

# temperature band edges (0 frozen .. 5 torrid) and humidity bands (0 arid .. 4 soaked)
TEMP_EDGES = (0.16, 0.33, 0.50, 0.67, 0.83)
HUM_EDGES = (0.24, 0.42, 0.58, 0.76)

#: The base land matrix, identical to the one in the BiomeClassifier.
LAND_MATRIX = (
    ("snowy_plains", "snowy_taiga", "snowy_taiga", "grove", "grove"),
    ("cold_shrubland", "taiga", "taiga", "old_growth_taiga", "old_growth_taiga"),
    ("highland_steppe", "plains", "temperate_forest", "old_growth_temperate_forest",
     "windswept_hills"),
    ("xeric_shrubland", "meadow", "fertile_valley", "temperate_forest", "swamp"),
    ("desert", "savanna", "humid_savanna", "sparse_jungle", "jungle"),
    ("desert", "savanna", "humid_savanna", "sparse_jungle", "tropical_rainforest"),
)

#: Biomes that must always come from the simulation raster because they encode
#: topology (a coastline, a watershed) rather than climate.
SPECIAL_BIOMES = {
    BIOME_INDEX[n]
    for n in (
        "deep_ocean", "ocean", "shallow_coast", "river", "lake", "frozen_river",
        "beach", "snowy_beach", "stony_shore", "salt_flats", "badlands_mesa",
        "volcanic_highland", "alpine_peaks", "glacier", "mangrove_swamp",
    )
}

#: Pre-computed lookup: [temp_band][hum_band] -> biome index
_MATRIX_IDX = np.array(
    [[BIOME_INDEX[name] for name in row] for row in LAND_MATRIX], dtype=np.int16
)


def bands(temperature, humidity):
    """Digitise climate into (temp_band, humidity_band) integer arrays."""
    tb = np.digitize(np.clip(temperature, 0.0, 1.0), TEMP_EDGES).astype(np.int16)
    hb = np.digitize(np.clip(humidity, 0.0, 1.0), HUM_EDGES).astype(np.int16)
    return tb, hb


def biome_from_climate(*, temp, humidity, height, water_mask, special, snowline,
                       sea_level=63.0, highland_band=42.0, alpine_band=88.0):
    """Return a biome index array for block-resolution climate fields."""
    temp = np.asarray(temp, dtype=np.float64)
    humidity = np.asarray(humidity, dtype=np.float64)
    height = np.asarray(height, dtype=np.float64)
    special = np.asarray(special, dtype=np.int16)
    water_mask = np.asarray(water_mask)

    tb, hb = bands(temp, humidity)
    biome = _MATRIX_IDX[tb, hb].astype(np.int16)

    # high ground reclassifies the climate biome
    elev = height - float(sea_level)
    land = water_mask == 0
    hi = land & (elev > highland_band)
    biome = np.where(hi, BIOME_INDEX["temperate_mountains"], biome)
    biome = np.where(hi & (tb >= 4), BIOME_INDEX["warm_temperate_mountains"], biome)
    biome = np.where(hi & (tb == 0), BIOME_INDEX["cold_mountains"], biome)
    biome = np.where(
        hi & ((tb == 1) | (tb == 2)) & (hb <= 2), BIOME_INDEX["arid_mountains"], biome
    )
    alpine = land & (elev > alpine_band)
    biome = np.where(alpine, BIOME_INDEX["alpine_peaks"], biome)
    snowy = land & (height > float(snowline))
    biome = np.where(snowy & (tb <= 1), BIOME_INDEX["glacier"], biome)
    biome = np.where(snowy & (tb >= 2), BIOME_INDEX["alpine_peaks"], biome)

    # topology-driven biomes win outright (they came from the raster)
    is_special = np.isin(special, list(SPECIAL_BIOMES))
    biome = np.where(is_special, special, biome)
    return biome.astype(np.int16)
