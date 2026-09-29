"""Population layer: where life, resources and settlements go.

This is the Still Life half of the app, extended from "how densely do plants grow" to
"where does anything *populate* the world":

* **vegetation scatter** - per-biome species tables sampled with a Poisson-ish
  stratified sampler, weighted by the population field, so a rain forest is dense and a
  desert has the occasional cactus;
* **resource scatter** - ore blobs, boulders, surface deposits, driven by the same
  population/geology fields (World Painter's "populate" layers);
* **settlements & POIs** - candidate sites scored from suitability (flat ground, fresh
  water, fertility, forest, coast, natural shelter) and then thinned with
  Poisson-disk separation so two villages never sit on top of each other;
* **roads** - least-cost paths between neighbouring settlements that prefer flat,
  dry, short crossings, which is exactly the kind of route a player would walk.

Everything returns plain data so the preview can draw it and the exporter can stamp it.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from ..core.noise import fbm, hash2, stretch01
from ..core.types import BIOMES, BIOME_INDEX, POI

DEFAULT_POPULATION = {
    "poi_count": 14,
    "min_poi_separation_cells": 22,
    "hamlet_ratio": 0.55,
    "ruin_ratio": 0.2,
    "outpost_ratio": 0.15,
    "landmark_ratio": 0.1,
    "road_connect_distance_cells": 90,
    "road_max_links": 2,
    "road_curve_cost": 2.4,
    "road_water_cost": 55.0,
    "road_slope_cost": 22.0,
    "tree_density": 1.0,
    "shrub_density": 1.0,
    "flower_density": 0.6,
    "boulder_density": 0.35,
    "ore_veins": 90,
    "ore_vein_radius": 3.2,
    "buried_treasure": 0.06,
    "port_chance": 0.35,
}


@dataclass
class TreeNode:
    """One placed plant: species + trunk height (decided at build time)."""

    cell_x: int
    cell_z: int
    species: str
    height: int
    radius: float = 0.0
    kind: str = "tree"  # tree | shrub | flower | grass | boulder | cactus | rock


@dataclass
class OreVein:
    cell_x: int
    cell_z: int
    ore: str
    radius: float
    depth: float  # blocks below the surface of the vein centre
    richness: float = 1.0


@dataclass
class PopulationResult:
    pois: List[POI] = field(default_factory=list)
    roads: List[List[Tuple[float, float]]] = field(default_factory=list)
    #: (n, 5) int32: cell_x, cell_z, species_id, height, kind  - sorted by (cell_z, cell_x)
    vegetation: Optional[np.ndarray] = None
    species_table: List[str] = field(default_factory=list)
    veins: List[OreVein] = field(default_factory=list)
    diagnostics: Dict[str, float] = field(default_factory=dict)


# --------------------------------------------------------------------------------------
# Species tables (Still Life keeps everything vanilla-flavoured: no new blocks)
# --------------------------------------------------------------------------------------

# species -> (trunk, leaves, trunk height range, canopy radius, weight, min population)
TREES: Dict[str, Dict] = {
    "oak": dict(trunk="oak_log", leaves="oak_leaves", height=(5, 7), radius=2.2, pop=0.35),
    "birch": dict(trunk="birch_log", leaves="birch_leaves", height=(6, 8), radius=1.8, pop=0.35),
    "spruce": dict(trunk="spruce_log", leaves="spruce_leaves", height=(9, 16), radius=2.0, pop=0.25),
    "pine": dict(trunk="spruce_log", leaves="spruce_leaves", height=(11, 19), radius=1.9, pop=0.2),
    "jungle": dict(trunk="jungle_log", leaves="jungle_leaves", height=(11, 22), radius=2.8, pop=0.55),
    "acacia": dict(trunk="acacia_log", leaves="acacia_leaves", height=(6, 9), radius=3.0, pop=0.25),
    "dark_oak": dict(trunk="dark_oak_log", leaves="dark_oak_leaves", height=(7, 11), radius=3.4, pop=0.6),
    "mangrove": dict(trunk="mangrove_log", leaves="mangrove_leaves", height=(6, 9), radius=2.2, pop=0.5),
    "palm": dict(trunk="jungle_log", leaves="jungle_leaves", height=(7, 11), radius=2.0, pop=0.4),
}

# biome -> layered species mix (dominant first)
BIOME_VEGETATION: Dict[str, Dict[str, List[str]]] = {
    "plains": {"trees": ["oak"], "shrubs": ["oak"], "plants": ["grass", "flowers"]},
    "meadow": {"trees": ["oak", "birch"], "shrubs": ["oak"], "plants": ["grass", "flowers"]},
    "fertile_valley": {"trees": ["oak", "birch", "dark_oak"], "shrubs": ["oak"],
                       "plants": ["grass", "flowers"]},
    "temperate_forest": {"trees": ["oak", "birch", "dark_oak"], "shrubs": ["oak"],
                         "plants": ["grass", "flowers"]},
    "old_growth_temperate_forest": {"trees": ["dark_oak", "oak", "birch"],
                                    "shrubs": ["oak"], "plants": ["grass", "flowers"]},
    "windswept_hills": {"trees": ["oak", "spruce"], "shrubs": ["oak"], "plants": ["grass"]},
    "taiga": {"trees": ["spruce", "pine"], "shrubs": ["spruce"], "plants": ["grass", "flowers"]},
    "old_growth_taiga": {"trees": ["pine", "spruce"], "shrubs": ["pine"], "plants": ["grass"]},
    "grove": {"trees": ["spruce"], "shrubs": ["spruce"], "plants": ["grass"]},
    "snowy_plains": {"trees": [], "shrubs": [], "plants": ["grass"]},
    "snowy_taiga": {"trees": ["spruce"], "shrubs": ["spruce"], "plants": ["grass"]},
    "swamp": {"trees": ["oak"], "shrubs": ["oak"], "plants": ["grass", "lily"]},
    "mangrove_swamp": {"trees": ["mangrove"], "shrubs": ["mangrove"], "plants": ["grass", "lily"]},
    "jungle": {"trees": ["jungle", "dark_oak"], "shrubs": ["jungle"], "plants": ["grass", "flowers"]},
    "tropical_rainforest": {"trees": ["jungle"], "shrubs": ["jungle"], "plants": ["grass", "flowers"]},
    "sparse_jungle": {"trees": ["jungle", "acacia"], "shrubs": ["jungle"], "plants": ["grass"]},
    "savanna": {"trees": ["acacia"], "shrubs": ["acacia"], "plants": ["grass"]},
    "humid_savanna": {"trees": ["acacia", "oak"], "shrubs": ["acacia"], "plants": ["grass"]},
    "xeric_shrubland": {"trees": [], "shrubs": ["shrub"], "plants": ["dry_grass"]},
    "cold_shrubland": {"trees": [], "shrubs": ["shrub"], "plants": ["grass"]},
    "highland_steppe": {"trees": [], "shrubs": ["shrub"], "plants": ["grass"]},
    "desert": {"trees": [], "shrubs": ["cactus"], "plants": ["dry_grass", "dead_bush"]},
    "badlands_mesa": {"trees": [], "shrubs": ["dead_bush"], "plants": ["dead_bush"]},
    "salt_flats": {"trees": [], "shrubs": [], "plants": []},
    "beach": {"trees": ["palm"], "shrubs": [], "plants": ["grass"]},
    "snowy_beach": {"trees": [], "shrubs": [], "plants": []},
    "stony_shore": {"trees": [], "shrubs": [], "plants": []},
    "cold_mountains": {"trees": ["spruce"], "shrubs": ["spruce"], "plants": ["grass"]},
    "temperate_mountains": {"trees": ["spruce", "oak"], "shrubs": ["spruce"], "plants": ["grass"]},
    "warm_temperate_mountains": {"trees": ["spruce", "oak"], "shrubs": ["oak"], "plants": ["grass"]},
    "arid_mountains": {"trees": [], "shrubs": ["dead_bush"], "plants": ["dry_grass"]},
    "alpine_peaks": {"trees": ["spruce"], "shrubs": [], "plants": ["grass"]},
    "glacier": {"trees": [], "shrubs": [], "plants": []},
    "volcanic_highland": {"trees": [], "shrubs": ["dead_bush"], "plants": ["dry_grass"]},
    "shallow_coast": {"trees": [], "shrubs": [], "plants": ["seagrass"]},
    "ocean": {"trees": [], "shrubs": [], "plants": ["seagrass"]},
    "deep_ocean": {"trees": [], "shrubs": [], "plants": []},
    "river": {"trees": [], "shrubs": [], "plants": ["seagrass"]},
    "lake": {"trees": [], "shrubs": [], "plants": ["seagrass"]},
    "frozen_river": {"trees": [], "shrubs": [], "plants": []},
}

# biome -> ore profile weights (depth band, abundance)
BIOME_ORES: Dict[str, Dict[str, float]] = {
    "default": {"coal": 1.0, "iron": 1.0, "copper": 0.6, "tin": 0.3},
    "desert": {"coal": 0.8, "iron": 1.2, "gold": 0.7, "copper": 0.8},
    "badlands_mesa": {"gold": 1.4, "copper": 1.2, "iron": 0.9, "redstone": 0.7},
    "arid_mountains": {"gold": 1.1, "iron": 1.4, "copper": 1.1, "lapis": 0.5},
    "volcanic_highland": {"iron": 1.6, "copper": 1.4, "redstone": 0.8, "diamond": 0.35},
    "temperate_mountains": {"coal": 1.1, "iron": 1.3, "redstone": 0.6, "diamond": 0.3},
    "warm_temperate_mountains": {"coal": 1.0, "iron": 1.3, "gold": 0.8, "diamond": 0.3},
    "cold_mountains": {"coal": 1.2, "iron": 1.2, "lapis": 0.7, "diamond": 0.35},
    "snowy_plains": {"coal": 1.0, "iron": 0.9, "lapis": 0.8},
    "swamp": {"coal": 0.9, "iron": 0.7, "copper": 0.7},
    "salt_flats": {"copper": 1.4, "gold": 0.6, "iron": 0.8},
}


# --------------------------------------------------------------------------------------
# Main builder
# --------------------------------------------------------------------------------------


class PopulationModel:
    def __init__(self, cfg: Dict, region, seed: int):
        self.cfg = {**DEFAULT_POPULATION, **((cfg or {}).get("population") or {})}
        self.region = region
        self.seed = int(seed)

    # ----------------------------------------------------------------------------------
    def build(
        self,
        *,
        heights: np.ndarray,
        biome: np.ndarray,
        population: np.ndarray,
        fertility: np.ndarray,
        water_mask: np.ndarray,
        temperature: np.ndarray,
        humidity: np.ndarray,
        sea_level: float = 63.0,
        progress=None,
    ) -> PopulationResult:
        c = self.cfg
        region = self.region
        cs = float(region.cell_size)
        h, w = heights.shape

        def tick(f, label):
            if progress:
                progress(f, label)

        from ..core.erosion import slope_map

        slope = slope_map(heights, cs)
        land = (water_mask == 0) | (water_mask == 4)

        # ---- 1. settlement suitability ------------------------------------------------
        suitability = self._suitability(
            heights, biome, population, fertility, slope, water_mask, sea_level
        )
        tick(0.15, "scoring settlement sites")

        # ---- 2. POIs -------------------------------------------------------------------
        pois = self._place_pois(suitability, heights, biome, water_mask, slope, sea_level)
        tick(0.35, "placing settlements")

        # ---- 3. roads ------------------------------------------------------------------
        roads = self._connect_pois(pois, heights, water_mask, slope, sea_level)
        tick(0.5, "routing roads")

        # ---- 4. vegetation --------------------------------------------------------------
        vegetation = self._scatter_vegetation(heights, biome, population, water_mask, slope)
        tick(0.8, "populating vegetation")
        from .structures import SPECIES as _SPECIES

        # ---- 5. ores / boulders ----------------------------------------------------------
        veins = self._place_ores(heights, biome, water_mask, slope)
        tick(0.95, "seeding ore deposits")

        diag = {
            "pois": float(len(pois)),
            "roads": float(len(roads)),
            "road_length_blocks": float(
                sum(
                    np.hypot(np.diff(np.array(r)[:, 0]), np.diff(np.array(r)[:, 1])).sum()
                    for r in roads
                    if len(r) > 1
                )
            ),
            "vegetation": float(0 if vegetation is None else len(vegetation)),
            "ore_veins": float(len(veins)),
        }
        return PopulationResult(pois=pois, roads=roads, vegetation=vegetation,
                                species_table=list(_SPECIES), veins=veins, diagnostics=diag)

    # ----------------------------------------------------------------------------------
    def _suitability(self, heights, biome, population, fertility, slope, water_mask,
                     sea_level) -> np.ndarray:
        """Weighted score for 'a person would build here'."""
        flat = np.exp(-slope / 0.35)
        water = np.asarray(water_mask)
        # fresh water close by (rivers/lakes) and the sea close by (ports)
        try:
            from scipy.ndimage import distance_transform_edt

            fresh = (water == 1) | (water == 2)
            d_fresh = distance_transform_edt(~fresh) if np.any(fresh) else np.full_like(slope, 1e6)
            d_sea = distance_transform_edt(~(water == 3))
            fresh_boost = np.exp(-d_fresh / 18.0)
            port_boost = np.exp(-d_sea / 26.0)
        except Exception:  # pragma: no cover
            fresh_boost = np.zeros_like(slope)
            port_boost = np.zeros_like(slope)

        above = np.clip((heights - sea_level) / 40.0, 0.0, 1.0)
        high_penalty = np.clip(1.0 - np.maximum(heights - (sea_level + 70), 0) / 60.0, 0.0, 1.0)

        score = (
            flat * 1.35
            + fertility * 1.15
            + np.asarray(population) * 0.55
            + fresh_boost * 1.25
            + port_boost * self.cfg["port_chance"] * 0.8
            + above * 0.25
        )
        score = score * high_penalty
        # nothing gets built on water or snow-capped rock
        land = (water == 0) | (water == 4)
        score = np.where(land, score, 0.0)
        return score

    # ----------------------------------------------------------------------------------
    def _place_pois(self, suitability, heights, biome, water_mask, slope, sea_level) -> List[POI]:
        c = self.cfg
        region = self.region
        cs = float(region.cell_size)
        h, w = suitability.shape
        n_target = int(c["poi_count"])
        separation = float(c["min_poi_separation_cells"])
        rng = np.random.default_rng(self.seed + 991)

        # jitter the score so sites are not all on the single global maximum
        jitter = rng.random((h, w)) * 0.22
        score = suitability + jitter * (suitability > 0.5)
        work = score.copy()
        pois: List[POI] = []
        kinds = self._kind_plan(n_target)

        for kind in kinds:
            if not np.any(work > 0.35):
                break
            idx = int(np.argmax(work))
            z, x = divmod(idx, w)
            water = int(water_mask[z, x])
            if water not in (0, 4):
                work[z, x] = 0.0
                continue
            # ports only make sense next to the sea
            if kind == "port" and not self._near(water_mask, z, x, 3, 3):
                kind = "village"
            name = _settlement_name(kind, int(x), int(z), self.seed, biome[z, x])
            pois.append(
                POI(
                    x=region.x0 + (x + 0.5) * cs,
                    z=region.z0 + (z + 0.5) * cs,
                    y=float(heights[z, x]),
                    kind=kind,
                    weight=float(np.clip(work[z, x], 0.0, 3.0)),
                    biome=BIOMES[int(biome[z, x])] if 0 <= biome[z, x] < len(BIOMES) else "",
                    name=name,
                )
            )
            # Poisson-disk style thinning
            rr = int(separation)
            z0, z1 = max(0, z - rr), min(h, z + rr + 1)
            x0, x1 = max(0, x - rr), min(w, x + rr + 1)
            zz, xx = np.mgrid[z0:z1, x0:x1]
            d = np.hypot(zz - z, xx - x)
            work[z0:z1, x0:x1] = np.where(d < separation, 0.0, work[z0:z1, x0:x1])

        return pois

    def _kind_plan(self, n: int) -> List[str]:
        c = self.cfg
        plan: List[str] = ["village", "village"]
        n = max(1, n)
        for _ in range(max(0, int(n * c["hamlet_ratio"]))):
            plan.append("hamlet")
        for _ in range(max(1, int(n * c["ruin_ratio"]))):
            plan.append("ruin")
        for _ in range(max(1, int(n * c["outpost_ratio"]))):
            plan.append("outpost")
        for _ in range(max(1, int(n * c["landmark_ratio"]))):
            plan.append("landmark")
        for _ in range(max(1, int(n * c["port_chance"] * 4))):
            plan.append("port")
        return plan[:n]

    @staticmethod
    def _near(mask: np.ndarray, z: int, x: int, k: int, value: int) -> bool:
        h, w = mask.shape
        z0, z1 = max(0, z - k), min(h, z + k + 1)
        x0, x1 = max(0, x - k), min(w, x + k + 1)
        return bool(np.any(mask[z0:z1, x0:x1] == value))

    # ----------------------------------------------------------------------------------
    def _connect_pois(self, pois: List[POI], heights, water_mask, slope, sea_level
                      ) -> List[List[Tuple[float, float]]]:
        """Least-cost paths between nearby settlements (A* on the cell grid)."""
        c = self.cfg
        region = self.region
        cs = float(region.cell_size)
        if len(pois) < 2:
            return []
        h, w = heights.shape
        max_links = int(c["road_max_links"])
        max_dist = float(c["road_connect_distance_cells"]) * cs

        links: List[Tuple[int, int]] = []
        for i, a in enumerate(pois):
            d = [(float(np.hypot(b.x - a.x, b.z - a.z)), j) for j, b in enumerate(pois) if j != i]
            d.sort()
            for dist, j in d[:max_links]:
                if dist <= max_dist and (min(i, j), max(i, j)) not in links:
                    links.append((min(i, j), max(i, j)))

        roads: List[List[Tuple[float, float]]] = []
        cost_cache: Dict[int, np.ndarray] = {}
        for i, j in links:
            a, b = pois[i], pois[j]
            ax = int((a.x - region.x0) / cs)
            az = int((a.z - region.z0) / cs)
            bx = int((b.x - region.x0) / cs)
            bz = int((b.z - region.z0) / cs)
            ax, az = int(np.clip(ax, 0, w - 1)), int(np.clip(az, 0, h - 1))
            bx, bz = int(np.clip(bx, 0, w - 1)), int(np.clip(bz, 0, h - 1))
            path = self._astar(ax, az, bx, bz, heights, water_mask, slope, sea_level)
            if path and len(path) > 1:
                pts = [
                    (region.x0 + (px + 0.5) * cs, region.z0 + (pz + 0.5) * cs) for pz, px in path
                ]
                roads.append(pts)
        return roads

    def _astar(self, ax, az, bx, bz, heights, water_mask, slope, sea_level):
        c = self.cfg
        h, w = heights.shape
        curve_cost = float(c["road_curve_cost"])
        water_cost = float(c["road_water_cost"])
        slope_cost = float(c["road_slope_cost"])

        def cell_cost(z, x):
            base = 1.0 + slope[z, x] * slope_cost
            if water_mask[z, x] == 3:
                base += water_cost
            elif water_mask[z, x] in (1, 2):
                base += water_cost * 0.55
            return base

        n = h * w
        dist = np.full(n, np.inf)
        prev = np.full(n, -1, dtype=np.int64)
        start = az * w + ax
        goal = bz * w + bx
        dist[start] = 0.0
        pq = [(0.0, start)]
        seen = np.zeros(n, dtype=bool)
        neigh = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
        while pq:
            d, cur = heapq.heappop(pq)
            if seen[cur]:
                continue
            seen[cur] = True
            if cur == goal:
                break
            cz, cx = divmod(cur, w)
            for dz, dx in neigh:
                nz, nx = cz + dz, cx + dx
                if nz < 0 or nx < 0 or nz >= h or nx >= w:
                    continue
                nidx = nz * w + nx
                if seen[nidx]:
                    continue
                step = math.hypot(dz, dx) * cell_cost(nz, nx)
                # discourage switchbacks
                if prev[cur] >= 0:
                    pz, px = divmod(int(prev[cur]), w)
                    if (nz - cz) != (cz - pz) or (nx - cx) != (cx - px):
                        step += curve_cost * 0.35
                nd = d + step
                if nd < dist[nidx]:
                    dist[nidx] = nd
                    prev[nidx] = cur
                    heur = math.hypot(nz - bz, nx - bx) * 1.05
                    heapq.heappush(pq, (nd + heur, nidx))
        if not np.isfinite(dist[goal]):
            return []
        path = []
        cur = goal
        guard = 0
        while cur != -1 and guard < n:
            z, x = divmod(int(cur), w)
            path.append((z, x))
            cur = int(prev[cur])
            guard += 1
        path.reverse()
        return self._smooth_path(path)

    @staticmethod
    def _smooth_path(path, iterations: int = 2):
        """Chaikin-ish smoothing so roads bend like paths, not like grid lines."""
        pts = [(float(x), float(z)) for z, x in path]
        for _ in range(iterations):
            if len(pts) < 3:
                break
            out = [pts[0]]
            for i in range(len(pts) - 1):
                x0, z0 = pts[i]
                x1, z1 = pts[i + 1]
                out.append((0.75 * x0 + 0.25 * x1, 0.75 * z0 + 0.25 * z1))
                out.append((0.25 * x0 + 0.75 * x1, 0.25 * z0 + 0.75 * z1))
            out.append(pts[-1])
            pts = out
        return [(z, x) for x, z in pts]

    # ----------------------------------------------------------------------------------
    def _scatter_vegetation(self, heights, biome, population, water_mask, slope
                            ) -> np.ndarray:
        """Stratified Poisson-ish scatter: one candidate per stratum, accepted by weight.

        Returns a compact ``(n, 5)`` int32 array sorted by ``(cell_z, cell_x)``:
        ``cell_x, cell_z, species_id, height, kind``.  A compact array keeps a few
        hundred thousand plants at a few megabytes instead of tens of megabytes of
        Python objects, and sorted order makes per-chunk lookups a slice.
        """
        c = self.cfg
        region = self.region
        cs = float(region.cell_size)
        h, w = heights.shape
        rng = np.random.default_rng(self.seed + 4242)
        out: List[Tuple[int, int, int, int, int]] = []
        kind_code = {"tree": 0, "shrub": 1, "plant": 2}

        # stratum size in cells: one tree per ~(3x3 cells) in the densest biomes
        for kind, density_key, min_pop, chance in (
            ("tree", "tree_density", 0.12, 0.95),
            ("shrub", "shrub_density", 0.08, 0.7),
            ("plant", "flower_density", 0.04, 0.9),
        ):
            # trees get a 2-cell stratum (8 blocks at cell_size 4) so forests read as
            # forests; undergrowth is coarser because nobody counts individual ferns.
            step = 2 if kind == "tree" else 4
            zz = np.arange(0, h, step)
            xx = np.arange(0, w, step)
            for z0 in zz:
                for x0 in xx:
                    z1 = min(z0 + step, h)
                    x1 = min(x0 + step, w)
                    block_biome = biome[z0:z1, x0:x1]
                    block_pop = population[z0:z1, x0:x1]
                    if block_pop.size == 0 or block_biome.size == 0:
                        continue
                    b_idx = int(np.argmax(np.bincount(block_biome.ravel())))
                    if not (0 <= b_idx < len(BIOMES)):
                        continue
                    name = BIOMES[b_idx]
                    table = BIOME_VEGETATION.get(name, {})
                    keys = {"tree": "trees", "shrub": "shrubs", "plant": "plants"}
                    species = table.get(keys[kind], [])
                    if not species:
                        continue
                    pop = float(np.mean(block_pop))
                    if pop < min_pop:
                        continue
                    p = float(np.clip(pop * float(c[density_key]) * chance, 0.0, 0.95))
                    # thinning by biome character: forests dense, shrubland sparse
                    if kind == "tree" and b_idx in (
                        BIOME_INDEX["desert"],
                        BIOME_INDEX["salt_flats"],
                        BIOME_INDEX["glacier"],
                        BIOME_INDEX["alpine_peaks"],
                    ):
                        p *= 0.02
                    if rng.random() > p:
                        continue
                    z = int(rng.integers(z0, z1))
                    x = int(rng.integers(x0, x1))
                    if water_mask[z, x] != 0:
                        continue
                    if kind == "tree" and slope[z, x] > 0.95:
                        continue
                    sp = species[int(rng.integers(0, len(species)))]
                    if sp in TREES:
                        spec = TREES[sp]
                        lo, hi = spec["height"]
                        height = int(rng.integers(lo, hi + 1))
                        out.append((x, z, _species_id(sp), height, kind_code["tree"]))
                    else:
                        out.append((x, z, _species_id(sp), 1, kind_code[kind]))

        if not out:
            return np.zeros((0, 5), dtype=np.int32)
        arr = np.asarray(out, dtype=np.int32)
        order = np.lexsort((arr[:, 0], arr[:, 1]))  # cell_z major, cell_x minor
        return arr[order]

    # ----------------------------------------------------------------------------------
    def _place_ores(self, heights, biome, water_mask, slope) -> List[OreVein]:
        c = self.cfg
        rng = np.random.default_rng(self.seed + 777)
        h, w = heights.shape
        veins: List[OreVein] = []
        n = int(c["ore_veins"])
        if n <= 0:
            return veins
        for _ in range(n):
            z = int(rng.integers(0, h))
            x = int(rng.integers(0, w))
            if water_mask[z, x] == 3:
                continue
            b = BIOMES[int(biome[z, x])] if 0 <= biome[z, x] < len(BIOMES) else "default"
            table = BIOME_ORES.get(b, BIOME_ORES["default"])
            names = list(table.keys())
            weights = np.array([table[k] for k in names], dtype=np.float64)
            weights /= weights.sum()
            ore = names[int(rng.choice(len(names), p=weights))]
            depth = float(rng.random() ** 2 * 48.0 + 4.0)
            veins.append(
                OreVein(
                    cell_x=x,
                    cell_z=z,
                    ore=ore,
                    radius=float(c["ore_vein_radius"]) * float(0.6 + rng.random()),
                    depth=depth,
                    richness=float(0.6 + rng.random() * 0.8),
                )
            )
        # buried treasure: rare, on beaches - a classic seeded loot site
        n_treasure = int(w * h * float(c["buried_treasure"]) / 8000.0)
        for _ in range(max(0, n_treasure)):
            z = int(rng.integers(0, h))
            x = int(rng.integers(0, w))
            b = BIOMES[int(biome[z, x])] if 0 <= biome[z, x] < len(BIOMES) else ""
            if b in ("beach", "snowy_beach"):
                veins.append(OreVein(x, z, "buried_treasure", 1.0, 1.0, 1.0))
        return veins


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


def _species_id(name: str) -> int:
    """Map a species name onto the template table in :mod:`mg.generation.structures`."""
    from .structures import SPECIES_ID

    return int(SPECIES_ID.get(name, 0))

_NAME_A = ["Ash", "Bracken", "Cinder", "Dun", "Elder", "Fen", "Gale", "Hollow", "Iron",
           "Keld", "Lark", "Moss", "North", "Oak", "Pine", "Quarry", "Raven", "Stone",
           "Thorn", "Vale", "Willow", "Yarrow", "Amber", "Birch", "Clay", "Dusk"]
_NAME_B = ["ford", "mere", "wick", "stead", "hold", "gate", "hollow", "reach", "fall",
           "crest", "moor", "haven", "brook", "ridge", "field", "watch", "keep", "march"]


def _settlement_name(kind: str, x: int, z: int, seed: int, biome_idx: int) -> str:
    a = int(hash2(np.int64(x), np.int64(z), seed + 17) * len(_NAME_A))
    b = int(hash2(np.int64(z), np.int64(x), seed + 29) * len(_NAME_B))
    base = f"{_NAME_A[a % len(_NAME_A)]}{_NAME_B[b % len(_NAME_B)]}"
    prefix = {
        "hamlet": "Hamlet of ",
        "ruin": "Ruins of ",
        "outpost": "",
        "landmark": "",
        "port": "Port ",
        "village": "",
    }.get(kind, "")
    suffix = {
        "outpost": " Outpost",
        "landmark": " Landmark",
    }.get(kind, "")
    return f"{prefix}{base}{suffix}".strip()
