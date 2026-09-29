"""Terrain construction - the "World Machine" half of the app.

The heightfield is assembled as an explicit macro chain, exactly the way you would
wire a World Machine device graph:

    continent noise  ->  height spline  ->  + mountain belts (ridged, warped)
                                          ->  + plateaus (terraced)
                                          ->  + island mask (cellular)
                                          ->  + volcanoes (cones + craters)
                                          ->  + hills / roughness (climate modulated)
                                          ->  + fine detail
                     ->  coastal shaping (cliffs vs. gradual beaches)
                     ->  multi-scale diffusion  (Lithosphere-style smoothness)

Everything is expressed as cheap numpy field maths over the whole region so the same
code serves the live preview and the final full-resolution export.
"""

from __future__ import annotations

import math
from typing import Dict, Optional, Tuple

import numpy as np

from ..core.noise import (
    cellular,
    domain_warp,
    fbm,
    gaussian_blur,
    hash2,
    normalize01,
    ridged_fbm,
    smoothstep,
    spline,
    stretch01,
)

# Default macro graph - every value is overridable from the UI / preset JSON.
#
# Scales given as ``*_region_factor`` are multiplied by the region's short side, so the
# same preset produces a comparable composition at 1 km and at 8 km.  Scales given in
# blocks are absolute, so Minecraft-scale detail stays Minecraft-scale.
DEFAULT_TERRAIN = {
    "sea_level": 63.0,
    # --- continents ---------------------------------------------------------------
    "continent_region_factor": 2.6,
    "continent_scale": 0.0,  # 0 = derive from the region factor
    "continent_octaves": 6,
    "coast_sharpness": 0.16,
    "land_fraction": 0.62,
    "base_spline": [
        [0.00, 18.0],
        [0.20, 32.0],
        [0.38, 48.0],
        [0.46, 57.0],
        [0.50, 62.5],
        [0.55, 67.0],
        [0.63, 72.0],
        [0.72, 79.0],
        [0.86, 90.0],
        [1.00, 104.0],
    ],
    # --- mountain ranges ----------------------------------------------------------
    "mountain_region_factor": 1.15,
    "mountain_mask_scale": 0.0,
    "mountain_threshold": 0.44,
    "mountain_sharpness": 0.34,
    "mountain_height": 148.0,
    "mountain_ridge_factor": 0.34,
    "mountain_ridge_scale": 0.0,
    "mountain_ridge_power": 1.35,
    "mountain_octaves": 6,
    "mountain_min_continent": 0.42,
    # --- plateaus ------------------------------------------------------------------
    "plateau_amount": 0.20,
    "plateau_factor": 0.55,
    "plateau_scale": 0.0,
    "plateau_steps": 4,
    # --- islands -------------------------------------------------------------------
    "island_count": 18,
    "island_radius": 0.30,
    "island_height": 30.0,
    "island_scale_factor": 0.40,
    # --- structural ridgelines: spline-scattered arête / peak instances (spec 2.1) --
    "ridge_count": 34,
    "ridge_base_altitude": 210.0,
    "ridge_sigma": 22.0,
    "ridge_length_min": 140.0,
    "ridge_length_max": 320.0,
    "ridge_peak_ratio": 0.34,
    "ridge_blend_k": 14.0,
    "ridge_noise": 0.18,
    "ridge_min_continent": 0.42,
    "ridge_spine": [],            # optional [[x, z], ...] in block coordinates
    # --- volcanoes -----------------------------------------------------------------
    "volcano_count": 2,
    "volcano_radius": 130.0,
    "volcano_height": 95.0,
    # --- mid + fine detail ---------------------------------------------------------
    "hill_amp": 17.0,
    "hill_factor": 0.30,
    "hill_scale": 0.0,
    "rolling_amp": 26.0,
    "rolling_factor": 0.72,
    "rolling_scale": 0.0,
    "detail_amp": 3.2,
    "detail_scale": 110.0,
    "roughness_arid_boost": 0.45,
    # --- coastal + smoothing -------------------------------------------------------
    "cliff_amount": 0.65,
    "cliff_height": 13.0,
    "coast_smoothing": 0.85,
    "diffusion": 0.32,
    "flatten_lowlands": 0.30,
    "terrace": 0.0,
}


def _x_spline(points, t):
    return spline(np.asarray(points, dtype=np.float64), t)


class TerrainGenerator:
    def __init__(self, cfg: Dict, region, seed: int):
        self.cfg = {**DEFAULT_TERRAIN, **((cfg or {}).get("terrain") or {})}
        cfg = cfg or {}
        self.climate_cfg = cfg.get("climate") or {}
        self.region = region
        self.seed = int(seed)
        # resolve all region-relative scales into block units once
        R = float(min(region.blocks_x, region.blocks_z))
        c = self.cfg
        self.continent_scale = float(c["continent_scale"]) or max(
            R * float(c["continent_region_factor"]), 1536.0
        )
        self.belt_scale = float(c["mountain_mask_scale"]) or max(
            R * float(c["mountain_region_factor"]), 900.0
        )
        self.ridge_scale = float(c["mountain_ridge_scale"]) or max(
            R * float(c["mountain_ridge_factor"]), 240.0
        )
        self.plateau_scale = float(c["plateau_scale"]) or max(R * float(c["plateau_factor"]), 320.0)
        self.hill_scale = float(c["hill_scale"]) or max(R * float(c["hill_factor"]), 140.0)
        self.rolling_scale = float(c["rolling_scale"]) or max(R * float(c["rolling_factor"]), 320.0)
        self.island_scale = max(R * float(c["island_scale_factor"]), 260.0)

    # ----------------------------------------------------------------------------------
    def generate(
        self,
        X: np.ndarray,
        Y: np.ndarray,
        climate,
        progress=None,
    ) -> Tuple[np.ndarray, Dict[str, np.ndarray]]:
        c = self.cfg
        seed = self.seed
        sea = float(c["sea_level"])
        self.instances = []
        masks: Dict[str, np.ndarray] = {}

        def tick(f, label):
            if progress:
                progress(f, label)

        # ---- 1. continentalness ------------------------------------------------------
        cont_scale = self.continent_scale
        wx, wy = domain_warp(
            X, Y, scale=cont_scale * 0.65, strength=cont_scale * 0.16, seed=seed + 7, octaves=3
        )
        cont = fbm(wx, wy, octaves=int(c["continent_octaves"]), scale=cont_scale, seed=seed + 1)
        cont = stretch01(cont, 1.0, 99.0)
        # dial the land/ocean split so the artist gets the coastline they asked for:
        # push the (1 - land_fraction) quantile onto the 0.5 contour line
        land_fraction = float(np.clip(c["land_fraction"], 0.02, 0.98))
        shift = 0.5 - float(np.quantile(cont, 1.0 - land_fraction))
        cont = np.clip(cont + shift, 0.0, 1.0)
        sharp = float(c["coast_sharpness"])
        if sharp > 0:
            cont = np.clip((cont - 0.5) / (1.0 - sharp) + 0.5, 0.0, 1.0)
        masks["continentalness"] = cont.astype(np.float32)
        tick(0.08, "continents")

        # ---- 2. base elevation from the height spline --------------------------------
        base = _x_spline(c["base_spline"], cont)
        masks["base"] = base.astype(np.float32)

        # ---- 3. mountain ranges: snaking belts along large-scale contour bands -------
        # A signed low-frequency field gives the "spine" of each range; the belt mask is
        # a band around its zero contour, so ranges come out as long, connected chains
        # instead of blobs.
        spine = fbm(X, Y, octaves=3, scale=self.belt_scale, seed=seed + 21)
        spine = (stretch01(spine, 2.0, 98.0) - 0.5) * 2.0
        width = float(np.clip(c["mountain_threshold"], 0.05, 0.95))
        belt = np.clip(1.0 - np.abs(spine) / max(width, 1e-3), 0.0, 1.0)
        belt = belt ** (1.0 / max(float(c["mountain_sharpness"]), 0.05) * 0.5)
        belt = belt * smoothstep(float(c["mountain_min_continent"]), 0.62, cont)

        rw, ry = domain_warp(
            X, Y, scale=self.ridge_scale * 1.6,
            strength=self.ridge_scale * 0.40, seed=seed + 33, octaves=3,
        )
        ridge = ridged_fbm(
            rw, ry, octaves=int(c["mountain_octaves"]),
            scale=self.ridge_scale, seed=seed + 41, lacunarity=2.07, gain=0.48,
        )
        ridge = np.power(np.clip(ridge, 0.0, 1.0), float(c["mountain_ridge_power"]))
        mountains = ridge * belt * float(c["mountain_height"])
        masks["mountain_belt"] = belt.astype(np.float32)
        masks["ridges"] = ridge.astype(np.float32)
        tick(0.22, "mountain ranges")

        # ---- 4. plateaus (terraced mesas) --------------------------------------------
        p_amount = float(c["plateau_amount"])
        plateaus = np.zeros_like(base)
        if p_amount > 0:
            plat = stretch01(fbm(X, Y, octaves=3, scale=self.plateau_scale, seed=seed + 61), 5.0, 95.0)
            plat = smoothstep(0.60, 0.80, plat) * p_amount * float(c["mountain_height"]) * 0.32
            if float(c["plateau_steps"]) > 0:
                steps = max(2, int(c["plateau_steps"]))
                step_h = float(c["mountain_height"]) * 0.26 / steps
                plat = np.floor(plat / max(step_h, 1e-6)) * step_h
            plateaus = plat * smoothstep(0.5, 0.7, cont)
        masks["plateaus"] = plateaus.astype(np.float32)

        # ---- 5. islands (smooth radial falloff, Lithosphere's archipelagos) ----------
        island_h = float(c["island_height"])
        islands = np.zeros_like(base)
        n_islands = int(c["island_count"])
        if n_islands > 0:
            radius = float(c["island_radius"])
            f1 = cellular(X, Y, scale=self.island_scale, seed=seed + 71, mode="f1")
            ids = cellular(X, Y, scale=self.island_scale, seed=seed + 71, mode="id")
            # a smooth density field decides how often an island wants to exist - no
            # hard Voronoi steps, the profile simply fades in and out
            density = stretch01(
                fbm(X, Y, octaves=2, scale=self.island_scale * 1.7, seed=seed + 73), 5.0, 95.0
            )
            spawn = np.clip((density - 0.45) * 3.0, 0.0, 1.0) * min(n_islands / 24.0, 1.4)
            profile = np.clip(1.0 - f1 / max(radius, 1e-6), 0.0, 1.0)
            profile = profile * profile * (3.0 - 2.0 * profile)
            islands = profile * island_h * spawn * (1.0 - smoothstep(0.46, 0.60, cont))
        masks["islands"] = islands.astype(np.float32)

        # ---- 6. rolling landform + hills + detail (climate modulated) ----------------
        rolling = fbm(X, Y, octaves=4, scale=self.rolling_scale, seed=seed + 79)
        rolling = rolling * float(c["rolling_amp"]) * smoothstep(0.44, 0.56, cont)
        hills = fbm(X, Y, octaves=4, scale=self.hill_scale, seed=seed + 81) * float(c["hill_amp"])
        hills = hills * smoothstep(0.43, 0.53, cont)
        # dry climates keep their roughness, wet ones are rounded off by erosion
        aridity = 1.0 + (1.0 - np.asarray(climate.humidity, dtype=np.float64)) * float(
            c["roughness_arid_boost"]
        )
        detail = (
            fbm(X, Y, octaves=5, scale=float(c["detail_scale"]), seed=seed + 91)
            * float(c["detail_amp"])
        )
        detail = detail * smoothstep(0.42, 0.52, cont) * aridity

        dem = base + mountains + plateaus + islands + rolling + hills + detail

        # ---- 6b. structural ridgelines (spline-scattered instances) -------------------
        # Spec 2.1: fault spline -> Poisson-spaced stations -> arête/peak primitives,
        # composited with polynomial smooth-max so crests stay sharp and cols stay smooth.
        from ..core.instances import default_spine, scatter_ridges, smooth_max

        ridge_count = int(c.get("ridge_count", 0))
        masks["ridge_instances"] = np.zeros(1, dtype=np.float32)
        if ridge_count > 0:
            spine = c.get("ridge_spine") or None
            if spine:
                spine = [(float(p[0]), float(p[1])) for p in spine]
            else:
                spine = default_spine(self.region)
            relief, instances = scatter_ridges(
                dem.shape, self.region,
                waypoints=spine,
                peak_count=ridge_count,
                base_altitude=float(c.get("ridge_base_altitude", 210.0)),
                ridge_sigma=float(c.get("ridge_sigma", 22.0)),
                length_range=(float(c.get("ridge_length_min", 140.0)),
                              float(c.get("ridge_length_max", 320.0))),
                peak_ratio=float(c.get("ridge_peak_ratio", 0.34)),
                blend_k=float(c.get("ridge_blend_k", 14.0)),
                noise_amp=float(c.get("ridge_noise", 0.18)),
                seed=seed + 131,
            )
            # only intrude where the cordillera rises above the surrounding macro terrain
            intrudes = relief > dem
            if np.any(intrudes):
                dem = np.where(intrudes, smooth_max(dem, relief,
                                                    float(c.get("ridge_blend_k", 14.0))), dem)
            # keep ranges on land: fade the instances out under water
            self.instances = instances
            masks["ridge_relief"] = relief.astype(np.float32)
        tick(0.30, "structural ridgelines")

        # ---- 7. volcanoes -------------------------------------------------------------
        volc_meta = []
        if int(c["volcano_count"]) > 0:
            dem, volc_meta = self._add_volcanoes(dem, X, Y, cont)
        masks["volcano"] = np.zeros_like(base, dtype=np.float32)
        for vx, vy, vr in volc_meta:
            d = np.hypot(X - vx, Y - vy) / max(vr, 1e-6)
            masks["volcano"] = np.maximum(masks["volcano"], np.clip(1.0 - d, 0.0, 1.0))
        tick(0.34, "volcanoes")

        # ---- 8. coastal shaping: gradual shores, cliffs where mountains meet the sea --
        cliff_amt = float(c["cliff_amount"])
        cliff_h = float(c["cliff_height"])
        if cliff_amt > 0:
            shore = smoothstep(0.40, 0.56, cont) * (1.0 - smoothstep(0.56, 0.72, cont))
            steep = smoothstep(0.35, 0.85, ridge) * masks["mountain_belt"]
            cliff_band = smoothstep(sea - 3.0, sea + 2.0, dem) * shore
            cliffs = cliff_h * cliff_amt * np.clip(steep * 2.0, 0.0, 1.0) * cliff_band
            dem = dem + cliffs
            masks["cliffs"] = cliffs.astype(np.float32)

        # ---- 9. lowland flattening (still-water lowlands / flood plains) --------------
        # Gated on local slope so it softens ground that is already nearly flat and
        # never leaves a ledge across a hillside.
        flat_amt = float(c["flatten_lowlands"])
        if flat_amt > 0:
            from ..core.erosion import slope_map

            low = np.clip(smoothstep(sea + 30.0, sea - 6.0, dem), 0.0, 1.0)
            gentle = np.clip(1.0 - slope_map(dem, 4.0) / 0.55, 0.0, 1.0)
            w = low * gentle * flat_amt * 0.5
            dem = dem * (1.0 - w) + (sea + 7.0) * w

        # ---- 10. terracing (optional stylised look) -----------------------------------
        terrace = float(c["terrace"])
        if terrace > 0:
            step = 3.0
            terraced = np.round(dem / step) * step
            dem = dem * (1.0 - terrace) + terraced * terrace

        # ---- 11. smoothing / diffusion ------------------------------------------------
        diff = float(c["diffusion"])
        if diff > 0:
            dem = self._multi_scale_smooth(dem, diff, float(c["coast_smoothing"]), sea)
        tick(0.44, "coastal shaping")

        masks["cont"] = cont.astype(np.float32)
        return dem.astype(np.float64), masks

    # ----------------------------------------------------------------------------------
    def _multi_scale_smooth(self, dem: np.ndarray, strength: float, coast_sigma: float,
                            sea_level: float) -> np.ndarray:
        """Blend several blur radii so large forms stay and small noise dies.

        This is the "terrain diffusion logic" knob: higher values give Lithosphere's
        famously smooth transitions; zero keeps every fractal octave intact.  Only the
        higher frequencies are removed, which is what separates this from a plain blur
        that would erase the mountain silhouettes too.
        """
        strength = float(np.clip(strength, 0.0, 1.0))
        if strength <= 0:
            return dem
        low = gaussian_blur(dem, 5.0 * strength)
        detail = dem - low  # everything above the low-frequency trend
        detail_reduced = detail * (1.0 - 0.85 * strength)
        out = low + detail_reduced

        # near the shore, diffuse harder so beaches come out wide and gentle
        if coast_sigma > 0:
            near_sea = np.clip(np.exp(-np.abs(dem - sea_level) / 9.0), 0.0, 1.0) * float(
                np.clip(coast_sigma, 0.0, 1.0)
            )
            soft = gaussian_blur(dem, 3.5 * max(coast_sigma, 0.1))
            out = out * (1.0 - near_sea) + soft * near_sea
        return out

    # ----------------------------------------------------------------------------------
    def _add_volcanoes(self, dem: np.ndarray, X: np.ndarray, Y: np.ndarray, cont: np.ndarray):
        """Scatter a handful of stratovolcanoes with craters and radial gullies."""
        c = self.cfg
        n = int(c["volcano_count"])
        radius = float(c["volcano_radius"])
        height = float(c["volcano_height"])
        seed = self.seed
        h, w = dem.shape
        meta = []
        land = cont > 0.55
        if not np.any(land):
            return dem, meta

        # use a coarse cellular field to seed candidate positions deterministically
        cell = max(radius * 0.75, 64.0)
        ids = cellular(X, Y, scale=cell, seed=seed + 131, mode="id")
        # candidate centres: strongest local maxima of a hash field
        cand = np.zeros((h, w), dtype=bool)
        step = max(int(cell / self._cell_size(X)), 1)
        samp = np.zeros_like(ids)
        samp[::step, ::step] = ids[::step, ::step]
        for _ in range(n * 12):
            idx = int(np.argmax(samp))
            z, x = divmod(idx, w)
            if samp[z, x] <= 0:
                break
            samp[max(0, z - step) : z + step, max(0, x - step) : x + step] = 0.0
            if not land[z, x]:
                continue
            cx, cy = float(X[z, x]), float(Y[z, x])
            d = np.hypot(X - cx, Y - cy) / radius
            # a slightly irregular cone so it never reads as a perfect circle
            wobble = fbm(X, Y, octaves=2, scale=radius * 0.5, seed=seed + 3311) * 0.16
            d = d * (1.0 + wobble)
            cone = np.clip(1.0 - d, 0.0, 1.0) ** 1.55 * height
            # crater: shallow, wide, broken up by gullies instead of a clean disc
            crater = np.exp(-((d / 0.17) ** 2)) * height * 0.30
            gullies = (
                fbm(X, Y, octaves=4, scale=radius * 0.12, seed=seed + int(abs(cx)) % 9999) * 0.5
                + 0.5
            )
            radial = np.clip(0.55 + 0.45 * np.cos(np.arctan2(Y - cy, X - cx) * 7.0), 0.0, 1.0)
            dem = dem + (cone - crater) * (0.65 + 0.25 * gullies + 0.10 * radial)
            meta.append((cx, cy, radius))
            if len(meta) >= n:
                break
        return dem, meta

    @staticmethod
    def _cell_size(X: np.ndarray) -> float:
        return float(X[0, 1] - X[0, 0]) if X.shape[1] > 1 else 1.0


# --------------------------------------------------------------------------------------
# Post-processing helpers used by the pipeline
# --------------------------------------------------------------------------------------


def strata_bands(dem: np.ndarray, *, sea_level: float = 63.0, strength: float = 1.0,
                 band_height: float = 7.0) -> np.ndarray:
    """Return a 0..1 'strata' field for mesa/badlands banding in the surface pass."""
    band = np.sin(dem / max(band_height, 0.5) * math.pi)
    return (np.clip(band, -1, 1) * 0.5 + 0.5) * strength


def shore_distance_approx(dem: np.ndarray, sea_level: float, cell_size: float) -> np.ndarray:
    """Approximate distance (in blocks) to the shoreline, signed: + inland, - at sea."""
    from scipy.ndimage import distance_transform_edt

    land = dem >= sea_level
    d_land = distance_transform_edt(~land, sampling=cell_size)
    d_sea = distance_transform_edt(land, sampling=cell_size)
    return np.where(land, d_sea, -d_land)
