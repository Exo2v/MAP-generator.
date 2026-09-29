"""Biome classification + the population model.

Two things live here:

**Biomes** - a Whittaker-style classifier: temperature x humidity x elevation x
proximity to water, producing the biome list in :mod:`mg.core.types`.  The names follow
Still Life's vocabulary (warm temperate mountains, old growth temperate forest, humid
savanna, xeric shrubland, arid mountains ...) because those are the shapes the
generator is aiming for: natural transitions and no vanilla-style confetti.

**Population** - the Still Life idea of a *population field*: how much life a cell
carries.  It is a continuous 0..1 raster driven by climate, soil, slope and water
access, and it is what the decoration pass and the POI placer both read.  Keeping it as
data (rather than hard-coded per biome) is what lets deserts stay barren while a river
valley in the same climate band turns into a gallery forest.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np

from ..core.noise import fbm, smoothstep, stretch01
from ..core.types import BIOMES, BIOME_INDEX


@dataclass
class BiomeFields:
    biome: np.ndarray  # int16 index into BIOMES
    fertility: np.ndarray  # 0..1 soil productivity
    population: np.ndarray  # 0..1 how much life wants to live here
    snowline: float  # block Y above which snow survives


class BiomeClassifier:
    def __init__(self, cfg: Dict, region, seed: int):
        self.cfg = {**DEFAULTS, **((cfg or {}).get("biomes") or {})}
        self.region = region
        self.seed = int(seed)

    # ----------------------------------------------------------------------------------
    def compute(
        self,
        *,
        heights: np.ndarray,
        water_mask: np.ndarray,
        water_level: np.ndarray,
        temperature: np.ndarray,
        humidity: np.ndarray,
        continentality: np.ndarray,
        climate_cfg: Optional[Dict] = None,
        volcano_mask: Optional[np.ndarray] = None,
        cell_size: float = 4.0,
    ) -> BiomeFields:
        c = self.cfg
        sea = float((climate_cfg or {}).get("sea_level", 63.0))
        t = np.asarray(temperature, dtype=np.float64)
        m = np.asarray(humidity, dtype=np.float64)
        cont = np.asarray(continentality, dtype=np.float64)
        h = np.asarray(heights, dtype=np.float64)

        from ..core.erosion import slope_map, curvature

        slope = slope_map(h, cell_size)
        curv = curvature(h, cell_size)

        # ---- elevation bands (relative to sea level, so a map can sit anywhere) ------
        elev = h - sea
        highland = smoothstep(float(c["highland_band"]), float(c["alpine_band"]), elev)
        alpine = smoothstep(float(c["alpine_band"]), float(c["peak_band"]), elev)

        # ---- temperature bands --------------------------------------------------------
        band = np.digitize(t, [0.16, 0.33, 0.50, 0.67, 0.83])  # 0 frozen .. 5 torrid
        hum = np.digitize(m, [0.24, 0.42, 0.58, 0.76])

        biome = np.full(h.shape, BIOME_INDEX["plains"], dtype=np.int16)
        # default land assignment by band matrix, then override with specials below
        matrix = c["matrix"]
        for tb in range(6):
            for hb in range(5):
                name = matrix[tb][hb]
                sel = (band == tb) & (hum == hb)
                biome[sel] = BIOME_INDEX[name]

        # ---- water -------------------------------------------------------------------
        wet = water_mask > 0
        ocean = water_mask == 3
        depth = np.where(ocean, sea - h, 0.0)
        biome[ocean & (depth < float(c["shallow_depth"]))] = BIOME_INDEX["shallow_coast"]
        biome[ocean & (depth >= float(c["shallow_depth"]))] = BIOME_INDEX["ocean"]
        biome[ocean & (depth > float(c["deep_depth"]))] = BIOME_INDEX["deep_ocean"]
        biome[water_mask == 1] = BIOME_INDEX["river"]
        biome[(water_mask == 1) & (band == 0)] = BIOME_INDEX["frozen_river"]
        biome[water_mask == 2] = BIOME_INDEX["lake"]
        biome[(water_mask == 2) & (band == 0)] = BIOME_INDEX["frozen_river"]

        # ---- shorelines ---------------------------------------------------------------
        # cells just above the waterline become beach / stony shore / snowy beach
        land = ~wet
        shore_band = land & (h - sea < float(c["beach_height"])) & (
            slope < float(c["beach_max_slope"])
        )
        steep_shore = land & (h - sea < float(c["beach_height"]) * 1.5) & (
            slope >= float(c["beach_max_slope"])
        )
        biome[shore_band] = BIOME_INDEX["beach"]
        biome[shore_band & (band == 0)] = BIOME_INDEX["snowy_beach"]
        biome[shore_band & (band >= 4) & (hum <= 2)] = BIOME_INDEX["stony_shore"]
        biome[steep_shore] = BIOME_INDEX["stony_shore"]

        # ---- specials ----------------------------------------------------------------
        # swamps: flat, very wet, low ground; mangroves when it is also hot
        swampy = land & (hum >= 3) & (slope < float(c["swamp_max_slope"])) & (
            elev < float(c["swamp_max_elev"])
        )
        biome[swampy & (band >= 4)] = BIOME_INDEX["mangrove_swamp"]
        biome[swampy & (band <= 2)] = BIOME_INDEX["swamp"]
        biome[land & (band == 3) & swampy] = BIOME_INDEX["swamp"]

        # salt flats: arid, dead flat, barely above the waterline
        salt = land & (band >= 3) & (hum == 0) & (slope < 0.06) & (elev < 8.0) & (
            curv <= 0.0
        )
        biome[salt] = BIOME_INDEX["salt_flats"]

        # mesas / badlands: hot + arid + terraced high ground
        mesa = land & (band >= 4) & (hum <= 1) & (elev > 24.0)
        biome[mesa] = BIOME_INDEX["badlands_mesa"]

        # high ground wins over climate below the snowline
        hi = land & (highland > 0.5)
        biome[hi] = BIOME_INDEX["temperate_mountains"]
        biome[hi & (band >= 4)] = BIOME_INDEX["warm_temperate_mountains"]
        biome[hi & (band == 0)] = BIOME_INDEX["cold_mountains"]
        biome[hi & ((band == 1) | (band == 2)) & (hum <= 2)] = BIOME_INDEX["arid_mountains"]
        biome[hi & (band == 3)] = BIOME_INDEX["temperate_mountains"]

        # snowline and ice: an altitude-driven cold cap independent of latitude
        snowline_raster = sea + float(c["snowline_base"]) - t * float(c["snowline_temp_drop"])
        snowline = float(np.mean(snowline_raster))
        snowy = land & (h > snowline_raster)
        biome[snowy & (band <= 1)] = BIOME_INDEX["glacier"]
        biome[snowy & (band >= 2)] = BIOME_INDEX["alpine_peaks"]
        alpine_all = land & (alpine > 0.5)
        biome[alpine_all & (h > snowline_raster * 1.06)] = BIOME_INDEX["alpine_peaks"]

        # volcanoes
        if volcano_mask is not None:
            # only the summit cone counts as volcanic rock - a wide disk of dark biome
            # reads as a hole in the map and buries the surrounding alpine biomes
            biome[np.asarray(volcano_mask) > 0.78] = BIOME_INDEX["volcanic_highland"]

        # ---- fertility + population ---------------------------------------------------
        fertility = self._fertility(t, m, slope, elev, band, biome, cell_size)
        population = self._population(fertility, band, hum, slope, biome, water_mask, curv)

        return BiomeFields(
            biome=biome, fertility=fertility.astype(np.float32),
            population=population.astype(np.float32), snowline=float(snowline),
        )

    # ----------------------------------------------------------------------------------
    def _fertility(self, t, m, slope, elev, band, biome, cell_size) -> np.ndarray:
        """How productive the ground is: climate optimum x gentle slope x soil."""
        c = self.cfg
        # plants like ~0.55 temperature and lots of moisture
        temp_fit = np.exp(-((t - float(c["temp_optimum"])) ** 2) / (2 * 0.24 ** 2))
        moist_fit = np.clip(m, 0.0, 1.0) ** float(c["moisture_exponent"])
        slope_pen = np.clip(1.0 - slope / float(c["fertility_slope_cut"]), 0.0, 1.0)
        alt_pen = np.clip(1.0 - np.maximum(elev, 0.0) / float(c["fertility_alt_cut"]), 0.0, 1.0)
        soil = temp_fit * moist_fit * slope_pen ** 0.7 * alt_pen ** 0.8

        # rocky / barren biomes are capped regardless of climate
        barren = (
            (biome == BIOME_INDEX["desert"])
            | (biome == BIOME_INDEX["salt_flats"])
            | (biome == BIOME_INDEX["glacier"])
            | (biome == BIOME_INDEX["alpine_peaks"])
            | (biome == BIOME_INDEX["stony_shore"])
            | (biome == BIOME_INDEX["volcanic_highland"])
            | (biome == BIOME_INDEX["badlands_mesa"])
            | (biome == BIOME_INDEX["arid_mountains"])
        )
        soil = np.where(barren, soil * 0.18, soil)
        # a little patchiness so fertility is not a smooth gradient
        patch = stretch01(
            fbm(
                np.arange(biome.shape[1])[None, :] * cell_size,
                np.arange(biome.shape[0])[:, None] * cell_size,
                octaves=3, scale=220.0, seed=self.seed + 4001,
            ),
            2.0, 98.0,
        )
        soil = soil * (0.82 + 0.36 * patch)
        return np.clip(soil, 0.0, 1.0)

    # ----------------------------------------------------------------------------------
    def _population(self, fertility, band, hum, slope, biome, water_mask, curv) -> np.ndarray:
        """Still Life's population field: how densely life fills a cell.

        Fertility sets the ceiling; biome archetype sets the character (grassland vs.
        dense forest vs. shrubland), and water access adds a bonus so river banks are
        always richer than the surrounding plateau.
        """
        c = self.cfg
        pop = fertility.copy()

        # archetype multipliers
        forest = (biome == BIOME_INDEX["temperate_forest"]) | (
            biome == BIOME_INDEX["old_growth_temperate_forest"]
        )
        jungle = (biome == BIOME_INDEX["jungle"]) | (biome == BIOME_INDEX["tropical_rainforest"])
        sparse_jungle = biome == BIOME_INDEX["sparse_jungle"]
        grassland = (biome == BIOME_INDEX["plains"]) | (biome == BIOME_INDEX["meadow"]) | (
            biome == BIOME_INDEX["fertile_valley"]
        )
        savanna = (biome == BIOME_INDEX["savanna"]) | (biome == BIOME_INDEX["humid_savanna"])
        shrub = (biome == BIOME_INDEX["xeric_shrubland"]) | (
            biome == BIOME_INDEX["cold_shrubland"]
        )
        taiga = (biome == BIOME_INDEX["taiga"]) | (biome == BIOME_INDEX["old_growth_taiga"])
        swamp = (biome == BIOME_INDEX["swamp"]) | (biome == BIOME_INDEX["mangrove_swamp"])

        pop = np.where(forest, pop * 1.15, pop)
        pop = np.where(jungle, pop * 1.3, pop)
        pop = np.where(sparse_jungle, pop * 0.95, pop)
        pop = np.where(grassland, pop * 1.02, pop)
        pop = np.where(savanna, pop * 0.85, pop)
        pop = np.where(shrub, pop * 0.55, pop)
        pop = np.where(taiga, pop * 0.9, pop)
        pop = np.where(swamp, pop * 1.25, pop)

        # water access bonus
        near_water = np.asarray(water_mask) == 0
        wet_bonus = np.zeros_like(pop)
        if np.any(~near_water):
            try:
                from scipy.ndimage import distance_transform_edt

                d = distance_transform_edt(near_water)
                wet_bonus = np.exp(-d / float(c["water_influence_cells"])) * float(
                    c["water_bonus"]
                )
            except Exception:  # pragma: no cover
                wet_bonus = 0.0
        pop = pop + wet_bonus

        # cold and steep ground is thin ground
        pop = pop * np.clip(1.0 - slope / float(c["population_slope_cut"]), 0.0, 1.0) ** 0.5
        pop = pop * np.clip(1.0 - (band == 0) * 0.55, 0.0, 1.0)
        # ridges and cliff edges are exposed - but sunlit hollows collect life
        pop = pop * np.clip(1.0 + curv * 0.02, 0.75, 1.25)
        return np.clip(pop, 0.0, 1.0)


DEFAULTS = {
    "highland_band": 42.0,
    "alpine_band": 88.0,
    "peak_band": 150.0,
    "beach_height": 3.0,
    "beach_max_slope": 0.30,
    "shallow_depth": 12.0,
    "deep_depth": 48.0,
    "swamp_max_slope": 0.16,
    "swamp_max_elev": 10.0,
    "snowline_base": 148.0,
    "snowline_temp_drop": 46.0,
    "temp_optimum": 0.55,
    "moisture_exponent": 0.85,
    "fertility_slope_cut": 1.15,
    "fertility_alt_cut": 190.0,
    "population_slope_cut": 1.45,
    "water_influence_cells": 14.0,
    "water_bonus": 0.32,
    # temperature band x humidity band -> biome name
    "matrix": [
        # band 0: frozen
        ["snowy_plains", "snowy_taiga", "snowy_taiga", "grove", "grove"],
        # band 1: cold
        ["cold_shrubland", "taiga", "taiga", "old_growth_taiga", "old_growth_taiga"],
        # band 2: cool temperate
        ["highland_steppe", "plains", "temperate_forest", "old_growth_temperate_forest",
         "windswept_hills"],
        # band 3: warm temperate
        ["xeric_shrubland", "meadow", "fertile_valley", "temperate_forest", "swamp"],
        # band 4: subtropical
        ["desert", "savanna", "humid_savanna", "sparse_jungle", "jungle"],
        # band 5: torrid
        ["desert", "savanna", "humid_savanna", "sparse_jungle", "tropical_rainforest"],
    ],
}


def biome_histogram(biome: np.ndarray) -> Dict[str, float]:
    """Fraction of the map covered by each biome - used by the UI legend."""
    total = biome.size
    if total == 0:
        return {}
    ids, counts = np.unique(biome, return_counts=True)
    out: Dict[str, float] = {}
    for i, n in zip(ids.tolist(), counts.tolist()):
        if 0 <= i < len(BIOMES):
            out[BIOMES[i]] = n / total
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))
