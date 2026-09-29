"""Continuous environmental scoring for feature placement (specification section 3.2).

Terrain synthesis produces continuous fields - height, slope, curvature, moisture.  The
game needs discrete decisions.  This module holds the response curves that translate one
into the other, as an *environmental multi-criteria evaluation*:

    ``P_feature(x, z) = f_height(H) * f_slope(theta) * f_moisture(M)
                         * f_curvature(kappa) * f_exclusion(x, z)``

with the exact formulations the blueprint calls out:

* foliage canopies - logistic slope filter down to zero above 35 degrees, a treeline that
  fades between 180 and 225 blocks, and a water buffer of one block above sea level;
* alpine scree / bedrock - a complementary logistic that dominates past 40 degrees;
* glacial frost - an altitude ramp whose snow is shed from steep faces (``1 - 0.6 sin t``).

Every curve is a pure function of numpy arrays, so a caller can evaluate a whole region at
once and hand the result straight to the ditherer in :mod:`mg.core.bluenoise`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Optional

import numpy as np

from .erosion import curvature as curvature_field
from .erosion import slope_map


# --------------------------------------------------------------------------------------
# response curves (all take degrees where they say degrees)
# --------------------------------------------------------------------------------------


def f_slope_canopy(slope_deg: np.ndarray, *, midpoint: float = 32.0,
                   steepness: float = 0.4) -> np.ndarray:
    """``1 / (1 + exp(k * (theta - mid)))`` - ~1.0 below 25 deg, ~0 above 35 deg."""
    return 1.0 / (1.0 + np.exp(steepness * (np.asarray(slope_deg, float) - midpoint)))


def f_height_treeline(elevation: np.ndarray, *, full_below: float = 180.0,
                      fade_to: float = 225.0) -> np.ndarray:
    """1.0 below the treeline, linear fade to 0.0 by ``fade_to``, 0.0 above it."""
    h = np.asarray(elevation, float)
    span = max(fade_to - full_below, 1e-6)
    return np.clip((fade_to - h) / span, 0.0, 1.0)


def f_water_buffer(elevation: np.ndarray, *, sea_level: float = 63.0,
                   buffer: float = 1.0) -> np.ndarray:
    """0.0 at or below ``sea_level + buffer`` (no submerged or beach-line trees), 1.0 above."""
    return np.clip((np.asarray(elevation, float) - (sea_level + buffer)) / max(buffer, 1e-6),
                   0.0, 1.0)


def f_slope_rock(slope_deg: np.ndarray, *, midpoint: float = 38.0,
                 steepness: float = 0.5) -> np.ndarray:
    """``1 / (1 + exp(-k * (theta - mid)))`` - bare bedrock dominates past 40 deg."""
    return 1.0 / (1.0 + np.exp(-steepness * (np.asarray(slope_deg, float) - midpoint)))


def f_snow(elevation: np.ndarray, slope_deg: np.ndarray, *, base: float = 200.0,
           span: float = 40.0, slope_shed: float = 0.6) -> np.ndarray:
    """``clamp((H - base) / span) * (1 - 0.6 * sin(theta))`` - steep faces shed snow."""
    h = np.asarray(elevation, float)
    theta = np.radians(np.asarray(slope_deg, float))
    alt = np.clip((h - base) / max(span, 1e-6), 0.0, 1.0)
    return np.clip(alt * (1.0 - slope_shed * np.sin(theta)), 0.0, 1.0)


def f_moisture(moisture: np.ndarray, *, exponent: float = 1.0,
               floor: float = 0.0) -> np.ndarray:
    """Simple power response; ``floor`` keeps hyper-arid ground from going exactly zero."""
    m = np.clip(np.asarray(moisture, float), 0.0, 1.0)
    return np.clip(m ** exponent, 0.0, 1.0) * (1.0 - floor) + floor


def f_curvature(kappa: np.ndarray, *, preference: float = 0.0,
                width: float = 0.4) -> np.ndarray:
    """Gaussian response around ``preference`` (negative = hollows, positive = ridges)."""
    k = np.asarray(kappa, float)
    return np.exp(-((k - preference) ** 2) / max(2.0 * width * width, 1e-9))


@dataclass
class EcologyConfig:
    """Knobs for the ecological evaluator; the defaults follow the specification."""

    treeline_full: float = 180.0
    treeline_fade: float = 225.0
    canopy_slope_mid: float = 32.0
    canopy_slope_k: float = 0.4
    rock_slope_mid: float = 38.0
    rock_slope_k: float = 0.5
    snow_base: float = 200.0
    snow_span: float = 40.0
    snow_slope_shed: float = 0.6
    water_buffer: float = 1.0
    moisture_exponent: float = 1.2
    curvature_width: float = 0.45
    extra: Dict[str, float] = field(default_factory=dict)


@dataclass
class EcologyFields:
    """Every continuous suitability field the placement stages consume."""

    slope_deg: np.ndarray
    curvature: np.ndarray
    canopy: np.ndarray          # foliage / Still Life canopies
    rock: np.ndarray            # alpine scree & bare bedrock
    snow: np.ndarray            # glacial permafrost & snow crust
    treeline: np.ndarray        # altitude component on its own, for masking
    water_buffer: np.ndarray

    def as_masks(self) -> Dict[str, np.ndarray]:
        """The 8-bit distribution masks written for WorldPainter (spec 3.3 / stage 2)."""
        return {
            "populate": _u8(self.canopy),
            "forest_tiers": _u8(np.clip(self.canopy * self.treeline, 0.0, 1.0)),
            "scree": _u8(self.rock),
            "frost": _u8(self.snow),
            "riverbeds": _u8(np.clip(1.0 - self.canopy, 0.0, 1.0) * (1.0 - self.rock)),
        }


def _u8(a: np.ndarray) -> np.ndarray:
    return np.clip(np.asarray(a, float), 0.0, 1.0).__mul__(255.0).astype(np.uint8)


# --------------------------------------------------------------------------------------
# evaluator
# --------------------------------------------------------------------------------------


class EcologicalEvaluator:
    """Multi-criteria evaluation over a generated region."""

    def __init__(self, cfg: Optional[EcologyConfig] = None):
        self.cfg = cfg or EcologyConfig()

    def evaluate(self, heights: np.ndarray, *, cell_size: float = 4.0,
                 humidity: Optional[np.ndarray] = None,
                 sea_level: float = 63.0,
                 water_mask: Optional[np.ndarray] = None) -> EcologyFields:
        c = self.cfg
        slope_deg = np.degrees(np.arctan(slope_map(heights, cell_size)))
        curv = curvature_field(heights, cell_size)

        canopy = f_slope_canopy(slope_deg, midpoint=c.canopy_slope_mid,
                                steepness=c.canopy_slope_k)
        treeline = f_height_treeline(heights, full_below=c.treeline_full,
                                     fade_to=c.treeline_fade)
        buffer = f_water_buffer(heights, sea_level=sea_level, buffer=c.water_buffer)
        canopy = canopy * treeline * buffer
        if humidity is not None:
            canopy = canopy * f_moisture(humidity, exponent=c.moisture_exponent, floor=0.05)
        if water_mask is not None:
            canopy = canopy * np.where(np.asarray(water_mask) > 0, 0.0, 1.0)

        rock = f_slope_rock(slope_deg, midpoint=c.rock_slope_mid, steepness=c.rock_slope_k)
        snow = f_snow(heights, slope_deg, base=c.snow_base, span=c.snow_span,
                      slope_shed=c.snow_slope_shed)
        return EcologyFields(slope_deg=slope_deg, curvature=curv, canopy=canopy,
                             rock=rock, snow=snow, treeline=treeline, water_buffer=buffer)

    def feature_probability(self, name: str, heights: np.ndarray, *,
                            cell_size: float = 4.0,
                            humidity: Optional[np.ndarray] = None,
                            sea_level: float = 63.0,
                            curvature_preference: float = 0.0,
                            water_mask: Optional[np.ndarray] = None) -> np.ndarray:
        """Convenience wrapper returning one named suitability field.

        Known names: ``canopy``, ``forest_tiers``, ``rock``, ``snow``.
        """
        f = self.evaluate(heights, cell_size=cell_size, humidity=humidity,
                          sea_level=sea_level, water_mask=water_mask)
        if name == "canopy":
            return f.canopy
        if name == "forest_tiers":
            return f.canopy * f.treeline
        if name == "rock":
            return f.rock
        if name == "snow":
            return f.snow
        raise KeyError(f"unknown feature '{name}'")
