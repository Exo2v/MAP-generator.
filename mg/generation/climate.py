"""Climate: temperature, humidity and continentality fields.

Modelled on what Lithosphere does to the Overworld's climate sampler:

* **bigger climate cells** - one low frequency noise pair drives the whole map, so
  biomes come out as large regions instead of a confetti of 30-block patches;
* **latitude bands on top of the noise** - a north/south gradient so poles read cold
  and an equatorial belt reads hot, instead of vanilla's pure noise;
* **altitude lapse rate** - temperature falls ~1 unit per 180 blocks of elevation, so
  mountains carry their own snowline regardless of latitude;
* **continentality** - interiors swing to extremes, coastlines stay mild and damp;
* **orographic rain shadow** - moisture is advected along the prevailing wind, rains
  out on the windward flank of every ridge, and leaves the leeward side dry.

The output is three normalised fields (0..1) plus a precipitation field derived from
the moisture budget.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np

from ..core.noise import cellular, fbm, normalize01, smoothstep, spline, stretch01


@dataclass
class ClimateFields:
    temperature: np.ndarray  # 0 = frozen, 1 = equatorial
    humidity: np.ndarray  # 0 = arid, 1 = soaking
    continentality: np.ndarray  # 0 = coast, 1 = deep interior
    precipitation: np.ndarray  # arbitrary units, for map export / biome tuning
    seasonality: np.ndarray  # 0 = aseasonal (tropics), 1 = harsh continent interior
    wind: Tuple[float, float] = (1.0, 0.0)


class ClimateModel:
    """Evaluates the climate fields for one region."""

    def __init__(self, cfg: Dict, region, seed: int):
        self.cfg = dict((cfg or {}).get("climate", {}) or {})
        self.root = cfg or {}
        self.region = region
        self.seed = int(seed)
        # make the model usable stand-alone: derive the region scale if unset
        if not float(self.cfg.get("region_scale") or 0):
            short = float(min(region.blocks_x, region.blocks_z))
            self.cfg["region_scale"] = max(
                short * float(self.cfg.get("region_scale_factor", 2.4)), 1200.0
            )

    # ----------------------------------------------------------------------------------
    def compute(
        self,
        X: np.ndarray,
        Y: np.ndarray,
        heights: Optional[np.ndarray] = None,
        ocean: Optional[np.ndarray] = None,
        progress=None,
    ) -> ClimateFields:
        c = self.cfg
        seed = self.seed
        w = X.shape[1]
        h = X.shape[0]

        scale = float(c.get("region_scale", 4096.0))
        octaves = int(c.get("region_octaves", 4))
        warmth_bias = float(c.get("temperature_bias", 0.0))
        humidity_bias = float(c.get("humidity_bias", 0.0))
        lat_noise = float(c.get("latitude_band_noise", 0.5))

        # ---- temperature ------------------------------------------------------------
        # Base: a north/south gradient, warped so the band edges meander instead of
        # running dead straight.  The curve is deliberately flat across the middle -
        # most of a map should be temperate, with the extremes only at the edges.
        band_warp = fbm(X, Y, octaves=3, scale=scale * 1.6, seed=seed + 11) * lat_noise * 0.45
        lat = np.clip((Y / max(self.region.blocks_z, 1)) + band_warp, -0.30, 1.30)
        lat_span = float(c.get("latitude_span", 1.0))
        polar = np.clip(lat * lat_span, 0.0, 1.0) ** float(c.get("polar_curve", 2.1))
        core = 1.0 - polar
        # Regional variation - the "large temperature regions" knob (Lithosphere's
        # bigger temperature fields).
        regional = fbm(X, Y, octaves=octaves, scale=scale, seed=seed + 101)
        temperature = core * 0.70 + stretch01(regional) * 0.30 + warmth_bias

        # ---- continentality (drives humidity loss + seasonality) --------------------
        cont_noise = fbm(X, Y, octaves=3, scale=scale * 0.6, seed=seed + 303)
        continentality = normalize01(cont_noise)

        # ---- humidity: advected moisture with orographic depletion ------------------
        humidity = self._moisture_advection(X, Y, heights, ocean, continentality)
        humidity = normalize01(humidity) * 0.85 + normalize01(
            fbm(X, Y, octaves=3, scale=scale * 0.5, seed=seed + 505) * 0.5 + 0.5
        ) * 0.15
        humidity = np.clip(humidity + humidity_bias - continentality * 0.12, 0.0, 1.0)

        # ---- altitude lapse rate ----------------------------------------------------
        # ~0.5 of a climate unit per 160 blocks of elevation: enough to give every
        # mountain its own snowline, not enough to freeze the whole map.
        if heights is not None:
            lapse = float(c.get("lapse_rate_per_block", 1.0 / 190.0))
            sea = float(c.get("sea_level", 63))
            above = np.maximum(heights - sea, 0.0)
            temperature = temperature - np.minimum(above * lapse, 0.62)
        temperature = np.clip(temperature, 0.0, 1.0)

        precipitation = np.clip(humidity * (0.35 + temperature * 0.9) * 2600.0, 0.0, 4000.0)
        seasonality = np.clip(
            continentality * 0.7 + (1.0 - temperature) * 0.4
            + np.abs(temperature - 0.5) * 0.5,
            0.0,
            1.0,
        )

        ang = math.radians(float(c.get("wind_direction", 90.0)))
        wind = (math.cos(ang), math.sin(ang))
        return ClimateFields(
            temperature=temperature.astype(np.float32),
            humidity=humidity.astype(np.float32),
            continentality=continentality.astype(np.float32),
            precipitation=precipitation.astype(np.float32),
            seasonality=seasonality.astype(np.float32),
            wind=wind,
        )

    # ----------------------------------------------------------------------------------
    def finalize(
        self,
        fields: ClimateFields,
        heights: np.ndarray,
        *,
        ocean: Optional[np.ndarray] = None,
        progress=None,
    ) -> ClimateFields:
        """Second climate pass, once the terrain exists.

        This is the important half: with a heightfield available we can run the
        orographic moisture advection for real (air forced over a ridge rains out, the
        far side is dry) and apply the altitude lapse rate, which is what produces
        deserts behind mountain ranges and snowlines on peaks instead of a purely
        latitude-driven climate.
        """
        X, Y = self._xy_for(heights.shape)
        c = self.cfg
        seed = self.seed
        hum = self._moisture_advection(X, Y, heights, ocean, fields.continentality)
        patch = normalize01(
            fbm(X, Y, octaves=3, scale=self.cfg.get("region_scale", 2048.0) * 0.5,
                seed=seed + 505)
        )
        weight = float(c.get("rain_shadow_weight", 0.55))
        humidity = (1.0 - weight) * fields.humidity + weight * (
            normalize01(hum) * 0.85 + patch * 0.15
        )
        humidity = np.clip(
            humidity + float(c.get("humidity_bias", 0.0))
            - fields.continentality * 0.10,
            0.0, 1.0,
        )

        lapse = float(c.get("lapse_rate_per_block", 1.0 / 190.0))
        sea = float(c.get("sea_level", 63))
        above = np.maximum(np.asarray(heights, dtype=np.float64) - sea, 0.0)
        temperature = np.clip(
            fields.temperature - np.minimum(above * lapse, float(c.get("max_lapse_drop", 0.62))),
            0.0, 1.0,
        )

        precipitation = np.clip(humidity * (0.35 + temperature * 0.9) * 2600.0, 0.0, 4000.0)
        seasonality = np.clip(
            fields.continentality * 0.7 + (1.0 - temperature) * 0.4
            + np.abs(temperature - 0.5) * 0.5,
            0.0, 1.0,
        )
        if progress:
            progress(1.0, "climate refinement")
        return ClimateFields(
            temperature=temperature.astype(np.float32),
            humidity=humidity.astype(np.float32),
            continentality=fields.continentality,
            precipitation=precipitation.astype(np.float32),
            seasonality=seasonality.astype(np.float32),
            wind=fields.wind,
        )

    def _xy_for(self, shape):
        h, w = shape
        ys = (np.arange(h, dtype=np.float64) + 0.5) * self.region.cell_size + self.region.z0
        xs = (np.arange(w, dtype=np.float64) + 0.5) * self.region.cell_size + self.region.x0
        return np.meshgrid(xs, ys)

    # ----------------------------------------------------------------------------------
    def _moisture_advection(
        self,
        X: np.ndarray,
        Y: np.ndarray,
        heights: Optional[np.ndarray],
        ocean: Optional[np.ndarray],
        continentality: np.ndarray,
    ) -> np.ndarray:
        """Semi-Lagrangian moisture transport with rain-out over high ground.

        Moisture enters from the ocean/edges, is carried along the wind, condenses out
        when the air is forced over rising terrain, and evaporates back in warm lowlands.
        """
        c = self.cfg
        h, w = X.shape
        steps = int(c.get("moisture_steps", 26))
        wind_speed = float(c.get("wind_speed", 0.55))  # cells per step
        rain_factor = float(c.get("rain_shadow_strength", 1.0))
        ang = math.radians(float(c.get("wind_direction", 90.0)))
        dx, dy = math.cos(ang) * wind_speed, math.sin(ang) * wind_speed

        # Terrain gradient along the wind = forced ascent (rising ground wrings out rain)
        if heights is not None:
            gy, gx = np.gradient(np.asarray(heights, dtype=np.float64))
            ascent = np.maximum(gx * dx + gy * dy, 0.0)
            ascent = np.clip(ascent * 0.12 * rain_factor, 0.0, 1.0)
        else:
            ascent = np.zeros((h, w))

        try:
            from scipy.ndimage import map_coordinates

            have_map = True
        except Exception:  # pragma: no cover
            have_map = False

        moisture = np.full((h, w), 0.35, dtype=np.float64)
        if ocean is not None:
            moisture = np.where(ocean, 1.0, moisture)
        else:
            shoreline = np.clip(1.0 - continentality, 0.0, 1.0)
            moisture = np.maximum(moisture, shoreline * 0.9)

        rows, cols = np.mgrid[0:h, 0:w].astype(np.float64)
        for _ in range(max(4, steps)):
            if have_map:
                src_y = rows - dy
                src_x = cols - dx
                adv = map_coordinates(moisture, [src_y, src_x], order=1, mode="nearest")
            else:  # pragma: no cover
                adv = np.roll(moisture, (int(round(-dy)), int(round(-dx))), axis=(0, 1))
            # ocean/edge inflow, evaporation in warm low land, rain-out on ascent
            inflow = 0.0
            if ocean is not None:
                inflow = np.where(ocean, 0.25, 0.0)
            evap = 0.015 * np.clip(1.0 - continentality, 0.0, 1.0)
            moisture = adv * (1.0 - 0.06) - ascent * 0.22 + evap + inflow
            moisture = np.clip(moisture, 0.0, 1.4)
        return moisture


def temperature_bands(temperature: np.ndarray) -> np.ndarray:
    """Buckets used by the biome classifier and the legend."""
    return np.digitize(temperature, [0.15, 0.32, 0.5, 0.68, 0.85]).astype(np.int8)


def humidity_bands(humidity: np.ndarray) -> np.ndarray:
    return np.digitize(humidity, [0.22, 0.4, 0.58, 0.76]).astype(np.int8)


def climate_color(temperature: np.ndarray, humidity: np.ndarray) -> np.ndarray:
    """RGB (uint8) climate map: x = humidity, y = temperature - a Whittaker plot."""
    t = np.clip(temperature, 0, 1)
    m = np.clip(humidity, 0, 1)
    r = 30 + 225 * t * (1.0 - 0.35 * m)
    g = 40 + 200 * (1.0 - np.abs(t - 0.5) * 1.4) * (0.4 + 0.6 * m)
    b = 60 + 195 * (1.0 - t) * (0.55 + 0.45 * m)
    return np.stack([r, g, b], axis=-1).clip(0, 255).astype(np.uint8)
