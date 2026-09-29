"""Rivers, streams, lakes and flow maps.

The build order is deliberate and mirrors how Streams Reflowing keeps streams and lakes
consistent with each other:

1.  **watershed plan** - fill depressions, label basins, route flow, measure drainage
    area.  Nothing is drawn yet, this is just the plan.
2.  **climate-aware channel set** - a cell becomes a channel when its drainage area
    passes a threshold that *shrinks in wet climates*, so rainforests carry far more
    streams than deserts.
3.  **carve along paths** - incision follows the flow path, with hydraulic geometry
    (W ~ Q^0.5, D ~ Q^0.4) and a bank flare so the channel blends into the slope.
4.  **re-route and repeat** - carving changes the surface, so flow is recomputed and
    the channel is carved again.  Two passes is enough to remove every step, dry
    basin and "fountain" where a stream used to climb over its own banks.
5.  **water surfaces** - lake surfaces from the filled basins, ocean from sea level,
    stream surfaces at bank height so the water body is continuous and only ever
    flows downhill.
6.  **mouths** - where a stream reaches the sea it digs through the beach into deep
    water instead of stopping on the sand.

Export of the resulting ``water_mask``/``water_level``/``flow`` fields is what makes the
generated world behave like the mod in game: the flow map tells shaders and water
physics which way the current runs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from ..core.hydrology import (
    build_river_network,
    carve_footprint,
    carve_tiered_channels,
    classify_tiers,
    dinf_accumulation,
    dinf_flow,
    match_accumulation_scale,
    dinf_flow_vectors,
    carve_channels,
    warp_field,
    channel_geometry,
    detect_waterfalls,
    fill_lakes,
    flow_accumulation,
    flow_directions,
    flow_vectors,
    ocean_mask,
    priority_flood,
)
from ..core.types import Lake, RiverPath

DRY = -1e9

DEFAULT_WATER = {
    "sea_level": 63.0,
    "river_threshold": 320.0,
    "climate_river_bias": 0.75,
    "valley_depth": 1.0,
    "bank_flare": 2.6,
    "carve_passes": 2,
    "lake_min_depth": 0.9,
    "lake_min_cells": 5,
    "max_lakes": 240,
    "waterfall_min_drop": 3.5,
    "coast_mouth_dig": 1.0,
    "river_logic": 1.0,            # graded beds, meander migration, deltas (own design)
    "flow_model": "dinf",          # "dinf" (spec 2.3) or "d8" (classic)
    "tier_carve": True,            # per-tier V / U / alluvial cross-sections
    "alluvial_spread": 3.0,        # how far lowland floodplains bleed outward
    "enforce_downhill": 1.0,
    "floodplain_width": 0.35,
    "meander_strength": 1.0,
}


@dataclass
class WaterResult:
    dem: np.ndarray
    water_level: np.ndarray
    water_mask: np.ndarray
    dirs: np.ndarray
    accum: np.ndarray
    flow: np.ndarray
    rivers: List[RiverPath]
    lakes: List[Lake]
    waterfalls: List[Tuple[float, float, float]] = field(default_factory=list)
    tiers: Optional[np.ndarray] = None      # 0 dry, 1 torrent, 2 mid-tier, 3 lowland
    diagnostics: Dict[str, float] = field(default_factory=dict)


class WaterSystem:
    def __init__(self, cfg: Dict, region, seed: int):
        self.cfg = {**DEFAULT_WATER, **((cfg or {}).get("water") or {})}
        self.region = region
        self.seed = int(seed)

    # ----------------------------------------------------------------------------------
    def build(self, dem: np.ndarray, climate, progress=None,
              no_water: Optional[np.ndarray] = None) -> WaterResult:
        """Build the river/lake/ocean system.

        ``no_water`` is an optional boolean mask of cells that must stay dry whatever the
        heightfield says - the Ashenfall caldera basin is below sea level but is filled
        with lava, so the surrounding sea must not pour into it.
        """
        c = self.cfg
        region = self.region
        cs = float(region.cell_size)
        sea = float(c["sea_level"])
        if no_water is not None:
            no_water = np.asarray(no_water, dtype=bool)
            if no_water.shape != dem.shape:
                no_water = np.zeros(dem.shape, dtype=bool)
        dem = np.asarray(dem, dtype=np.float64).copy()

        def tick(f, label):
            if progress:
                progress(f, label)

        # ---- 0. meander seed ----------------------------------------------------------
        # A grid flow router can only choose among eight directions, so on a smooth
        # surface rivers come out as dead-straight 45 degree lines.  Nudging the routing
        # surface with two octaves of sub-block noise gives the network the wiggle real
        # channels have, without changing the terrain the player stands on (the largest
        # displacement is a couple of blocks, which simply reads as uneven banks).
        if float(c.get("meander_strength", 1.0)) > 0:
            dem = self._meander_seed(dem, float(c["meander_strength"]))

        # ---- 1. watershed plan --------------------------------------------------------
        filled, fill_depth = priority_flood(dem, epsilon=2e-4)
        lake_mask, lakes = fill_lakes(
            dem, filled, fill_depth,
            min_depth=float(c["lake_min_depth"]),
            min_cells=int(c["lake_min_cells"]),
            sea_level=sea,
        )
        lakes.sort(key=lambda l: -l.volume)
        lakes = lakes[: int(c["max_lakes"])]
        keep = np.zeros_like(lake_mask)
        for lk in lakes:
            keep[lk.cells[:, 0], lk.cells[:, 1]] = True
        lake_mask = keep
        if no_water is not None:
            lake_mask = lake_mask & ~no_water
        ocean = ocean_mask(dem, sea, exclude=no_water) if no_water is not None \
            else ocean_mask(dem, sea)

        dinf = str(c.get("flow_model", "dinf")).lower() == "dinf"
        flow_fracs = None
        dirs = flow_directions(filled)
        accum_scale = 1.0
        if dinf:
            flow_fracs, dinf_vec = dinf_flow(filled)
            accum = dinf_accumulation(filled, flow_fracs)
            # D-infinity spreads each cell's water between two neighbours, so absolute
            # drainage areas sit on a different scale than the D8 numbers every
            # threshold was written against.  Measure the ratio once and rescale the
            # river / tier thresholds, or the whole network would vanish.
            accum_scale = match_accumulation_scale(
                flow_accumulation(filled, dirs), accum,
                mask=flow_accumulation(filled, dirs) > 4.0,
            )
            accum = accum * accum_scale
            c["_accum_scale"] = accum_scale
        else:
            dinf_vec = None
            accum = flow_accumulation(filled, dirs)
        tick(0.2, "watershed routing")

        # ---- 2. climate-aware channel threshold --------------------------------------
        humidity = np.asarray(climate.humidity, dtype=np.float64)
        bias = float(c["climate_river_bias"])
        threshold_map = float(c["river_threshold"]) * (1.0 + bias - bias * 2.0 * humidity)
        threshold_map = np.maximum(threshold_map, 24.0)

        # ---- 3+4. carve, re-route, repeat --------------------------------------------
        current = dem.copy()
        for i in range(int(c["carve_passes"])):
            if bool(c.get("tier_carve", True)):
                # spec 2.3 A.4: torrents carve V-shaped ravines, mid-tier rivers U-shaped
                # gravel beds, lowland rivers wide alluvial floors with levees
                warped = warp_field(accum, cell_size=cs, seed=self.seed + i * 17,
                                    strength=float(c["meander_strength"]), scale=14.0)
                current, tier_ids = carve_tiered_channels(
                    current, warped,
                    cell_size=cs,
                    threshold=float(np.median(np.asarray(threshold_map))),
                    bank_flare=float(c["bank_flare"]),
                    sea_level=sea,
                    valley_depth_scale=float(c["valley_depth"]),
                    passes=1,
                    lake_mask=lake_mask,
                    alluvial_spread=float(c.get("alluvial_spread", 3.0)),
                )
            else:
                current = carve_channels(
                    current, dirs, accum,
                    cell_size=cs,
                    min_discharge=threshold_map,
                    strength=float(c["valley_depth"]),
                    bank_flare=float(c["bank_flare"]),
                    lake_mask=lake_mask,
                    sea_level=sea,
                    passes=1,
                    meander_strength=float(c["meander_strength"]),
                    seed=self.seed + i * 17,
                )
            if i < int(c["carve_passes"]) - 1:
                filled, _ = priority_flood(current, epsilon=2e-4)
                dirs = flow_directions(filled)
                if dinf:
                    flow_fracs, dinf_vec = dinf_flow(filled)
                    accum = dinf_accumulation(filled, flow_fracs) * accum_scale
                else:
                    accum = flow_accumulation(filled, dirs)
            tick(0.35 + 0.2 * (i + 1), f"carving channels ({i + 1})")

        if float(c["floodplain_width"]) > 0:
            current = self._floodplain(current, accum, threshold_map,
                                       float(c["floodplain_width"]), cs)

        # final routing on the carved surface
        filled, fill_depth = priority_flood(current, epsilon=2e-4)
        dirs = flow_directions(filled)
        if dinf:
            flow_fracs, dinf_vec = dinf_flow(filled)
            accum = dinf_accumulation(filled, flow_fracs) * accum_scale
        else:
            accum = flow_accumulation(filled, dirs)

        # ---- 5. streams ---------------------------------------------------------------
        # Land channels only: the sea floor is not a river, and excluding it stops the
        # exported network from drawing lines across open water.
        land_channel = np.where(ocean, -1.0, accum)
        rivers = build_river_network(
            current, dirs, land_channel,
            cell_size=cs,
            origin=(region.x0, region.z0),
            min_discharge=threshold_map,
            mouth_is_water=lake_mask | ocean,
        )
        tick(0.72, "extracting river networks")

        # ---- 5b. river shaping: graded beds, meander migration, deltas -----------------
        # This is the river logic proper.  Flow routing decided *where* the water goes;
        # the shaper decides what the channel looks like once it gets there, then hands
        # back a rasterised footprint so the carve, the water mask and the drawn ribbons
        # all describe the same channel.
        shaped_mask = None
        if float(c.get("river_logic", 1.0)) > 0 and rivers:
            from .rivers import RiverShaper, rasterize_channels

            shaper = RiverShaper(c, seed=self.seed + 91)
            shaped = shaper.shape(
                current, rivers,
                cell_size=cs,
                threshold=float(np.median(np.asarray(threshold_map))),
                ocean=ocean,
                lake_mask=lake_mask,
                sea_level=sea,
                origin=(region.x0, region.z0),
            )
            current = shaped.dem
            rivers = shaped.rivers
            for lk in shaped.oxbows:
                lakes.append(lk)
            if shaped.waterfalls:
                extra_falls = shaped.waterfalls
            else:
                extra_falls = []
            shaped_mask, shaped_hw, shaped_dp, shaped_lv = rasterize_channels(
                rivers, current.shape, cell_size=cs, origin=(region.x0, region.z0),
                min_discharge=float(np.median(np.asarray(threshold_map))),
            )
            current = carve_footprint(
                current, shaped_mask, shaped_hw, shaped_dp,
                cell_size=cs, sea_level=sea, bank_flare=float(c["bank_flare"]),
                level=shaped_lv, lake_mask=lake_mask,
            )
            diag_rivers = shaped.diagnostics
            tick(0.78, "shaping rivers")
        else:
            extra_falls = []
            diag_rivers = {}

        water_mask = np.zeros_like(dem, dtype=np.uint8)
        water_level = np.full_like(dem, DRY, dtype=np.float64)

        # Re-derive the channel footprint from the accumulated final state, warped the
        # same way the carve was so the water body sits inside the channel it cut.
        warped_accum = warp_field(accum, cell_size=cs, seed=self.seed + 3,
                                  strength=float(c["meander_strength"]), scale=14.0)
        channel = warped_accum >= threshold_map
        channel &= ~lake_mask
        channel &= ~ocean
        if shaped_mask is not None:
            # the migrated meanders are the channel now; union so the water mask covers
            # every reach the shaper cut, including lateral excursions the accumulation
            # raster never saw
            channel = (channel | (shaped_mask & ~lake_mask & ~ocean))

        # ---- 6. mouths: let streams break through the beach berm into deep water ------
        # Only the berm itself is cut, and never below the shelf, so a river mouth
        # becomes a proper channel instead of a hole punched in the sea floor.
        if float(c["coast_mouth_dig"]) > 0:
            berm = (current < sea + 1.5) & (current > sea - 4.0) & channel
            dig = np.where(berm, np.clip(sea + 0.5 - current, 0.0, 9.0)
                           * float(c["coast_mouth_dig"]), 0.0)
            current = np.where(berm, np.maximum(current - dig, sea - 6.0), current)

        bank_height = dem  # pre-carve terrain height == the top of the channel bank
        # water sits at the bank height, so the carved channel is full
        river_surface = np.where(channel, np.minimum(bank_height, sea + 80.0), DRY)
        if float(c["enforce_downhill"]) > 0:
            river_surface = self._monotonic_along_flow(current, dirs, river_surface, channel)

        water_mask[channel] = 1
        water_level = np.where(channel, river_surface, water_level)

        # ---- 7. lakes ---------------------------------------------------------------
        for idx, lk in enumerate(lakes):
            rows, cols = lk.cells[:, 0], lk.cells[:, 1]
            lk.outlet = self._lake_outlet(current, lk)
            water_mask[rows, cols] = 2
            water_level[rows, cols] = lk.surface
            # a lake must be at least as deep as the ground under it is low
            water_level[rows, cols] = np.maximum(water_level[rows, cols], current[rows, cols] + 0.4)

        # ---- 8. ocean ---------------------------------------------------------------
        ocean_final = ocean_mask(current, sea)
        water_mask[ocean_final] = 3
        water_level[ocean_final] = sea
        # shallow water right at the shore so beaches read correctly
        if not np.any(ocean_final):
            shore = (current < sea) & (current > sea - 1.0)
            water_mask[shore & (water_mask == 0)] = 3
            water_level[shore & (water_mask == 0)] = sea

        # ---- 9. waterfalls ------------------------------------------------------------
        waterfalls = detect_waterfalls(current, rivers, min_drop=float(c["waterfall_min_drop"]))
        if extra_falls:
            seen = {(round(x, 1), round(z, 1)) for x, z, _ in waterfalls}
            for x, z, y in extra_falls:
                if (round(x, 1), round(z, 1)) not in seen:
                    waterfalls.append((x, z, y))
        for wx, wz, _wy in waterfalls[:4000]:
            cx = int((wx - region.x0) / cs)
            cz = int((wz - region.z0) / cs)
            if 0 <= cz < water_mask.shape[0] and 0 <= cx < water_mask.shape[1]:
                if water_mask[cz, cx] == 1:
                    water_mask[cz, cx] = 4
        tick(0.85, "water surfaces")

        if dinf and dinf_vec is not None:
            flow = dinf_flow_vectors(dinf_vec, smooth=1)
        else:
            flow = flow_vectors(dirs, smooth=1)
        # dry cells keep the terrain gradient as their "flow" so wind/sediment maps still
        # have something continuous to read.
        dry = (water_mask == 0)
        flow[dry] = 0.0

        tier_ids = classify_tiers(accum, threshold=float(np.median(np.asarray(threshold_map))))
        diag = {
            "flow_model": "dinf" if dinf else "d8",
            "accum_scale": round(float(accum_scale), 3),
            "torrent_cells": float(np.count_nonzero(tier_ids == 1)),
            "midriver_cells": float(np.count_nonzero(tier_ids == 2)),
            "lowland_cells": float(np.count_nonzero(tier_ids == 3)),
            **{f"river_{k}": v for k, v in (diag_rivers or {}).items()},
            "channels": float(np.count_nonzero(channel)),
            "lakes": float(len(lakes)),
            "waterfalls": float(len(waterfalls)),
            "max_discharge": float(np.max(accum)) if accum.size else 0.0,
            "carved_volume": float(np.sum(np.maximum(dem - current, 0.0))),
        }
        if no_water is not None:
            # the lava basin: dry, and never counted as ocean in the diagnostics
            water_mask[no_water] = 0
            water_level[no_water] = np.nan
        tick(1.0, "hydrology complete")
        return WaterResult(
            dem=current,
            water_level=water_level.astype(np.float32),
            water_mask=water_mask,
            dirs=dirs,
            accum=accum.astype(np.float32),
            flow=flow,
            rivers=rivers,
            lakes=lakes,
            waterfalls=waterfalls,
            tiers=tier_ids.astype(np.uint8),
            diagnostics=diag,
        )

    # ----------------------------------------------------------------------------------
    def _meander_seed(self, dem: np.ndarray, strength: float) -> np.ndarray:
        """Two-octave routing jitter: fine wiggles + broad bends."""
        from ..core.noise import fbm, grid_coords

        region = self.region
        cs = float(region.cell_size)
        h, w = dem.shape
        X, Y = grid_coords(w, h, cs, (region.x0, region.z0))
        fine = fbm(X, Y, octaves=2, scale=cs * 3.0, seed=self.seed + 8123)
        coarse = fbm(X, Y, octaves=2, scale=cs * 13.0, seed=self.seed + 8124)
        jitter = fine * (0.45 * strength) + coarse * (1.45 * strength)
        # keep the coast and the deep sea untouched so shorelines stay clean
        sea = float(self.cfg.get("sea_level", 63.0))
        shore_free = np.clip((dem - sea) / 4.0, 0.0, 1.0)
        return dem + jitter * shore_free

    # ----------------------------------------------------------------------------------
    def _floodplain(self, dem: np.ndarray, accum: np.ndarray, threshold, amount: float,
                    cell_size: float) -> np.ndarray:
        """Widen valley floors beside big rivers (alluvial flats, oxbow country)."""
        from scipy.ndimage import uniform_filter

        big = accum > threshold * 4.0
        if not np.any(big):
            return dem
        smooth = uniform_filter(dem, size=5, mode="nearest")
        target = np.where(big, smooth, dem)
        # grow the influence outward a couple of cells
        for _ in range(2):
            pad = np.pad(target, 1, mode="edge")
            neigh = np.maximum.reduce(
                [pad[:-2, 1:-1], pad[2:, 1:-1], pad[1:-1, :-2], pad[1:-1, 2:]]
            )
            m = big & (neigh > target)
            target = np.where(m, target + (neigh - target) * 0.5, target)
        return dem * (1.0 - amount) + np.minimum(dem, target) * amount

    # ----------------------------------------------------------------------------------
    def _monotonic_along_flow(self, dem: np.ndarray, dirs: np.ndarray, surface: np.ndarray,
                             channel: np.ndarray) -> np.ndarray:
        """Force the water surface to never climb along a flow path.

        Walks cells high -> low and pushes each cell's surface down to at most
        downstream + a small drop, which is what removes "fountains" (water that would
        have to run uphill) after carving.
        """
        from ..core.hydrology import downstream_order

        h, w = dem.shape
        out = surface.copy()
        order = downstream_order(dem, dirs)
        ys, xs = np.divmod(np.arange(h * w), w)
        d8 = np.array([[-1, -1], [-1, 0], [-1, 1], [0, 1], [1, 1], [1, 0], [1, -1], [0, -1]])
        flat_dirs = dirs.ravel()
        out_flat = out.ravel()
        chan_flat = channel.ravel()
        for idx in order:
            if not chan_flat[idx] or flat_dirs[idx] < 0:
                continue
            dz, dx = d8[flat_dirs[idx]]
            nz, nx = ys[idx] + dz, xs[idx] + dx
            if nz < 0 or nx < 0 or nz >= h or nx >= w:
                continue
            nidx = nz * w + nx
            if not chan_flat[nidx]:
                continue
            limit = out_flat[nidx] + 0.05
            if out_flat[idx] > limit:
                out_flat[idx] = limit
        return out_flat.reshape(h, w)

    # ----------------------------------------------------------------------------------
    def _lake_outlet(self, dem: np.ndarray, lake: Lake) -> Optional[Tuple[float, float]]:
        """Lowest rim cell around a lake - where the outflow stream leaves."""
        rows, cols = lake.cells[:, 0], lake.cells[:, 1]
        inside = set(zip(rows.tolist(), cols.tolist()))
        h, w = dem.shape
        best = None
        best_h = math.inf
        for z, x in zip(rows.tolist(), cols.tolist()):
            for dz in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    nz, nx = z + dz, x + dx
                    if not (0 <= nz < h and 0 <= nx < w):
                        continue
                    if (nz, nx) in inside:
                        continue
                    hv = dem[nz, nx]
                    if hv < best_h:
                        best_h = hv
                        best = (nz, nx)
        if best is None:
            return None
        cs = float(self.region.cell_size)
        return (
            self.region.x0 + (best[1] + 0.5) * cs,
            self.region.z0 + (best[0] + 0.5) * cs,
        )


def build_water(cfg: Dict, region, seed: int, dem: np.ndarray, climate, progress=None
                ) -> WaterResult:
    return WaterSystem(cfg, region, seed).build(dem, climate, progress=progress)
