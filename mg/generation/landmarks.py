"""Ashenfall: the continent of Vantyra.

Everything in this module comes from the *Ashenfall Master World Map & Geological
Specification*.  Nothing here is invented: the canvas, the landform table, the landmark
coordinates and bounding boxes, the shelf dropoff, the spline ranges, the climate tiers
and the slope-aware surface rules are all transcribed from that document, and where two
parts of it disagree the code records which one it followed and why.

References in comments are to that document's sections.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

# --------------------------------------------------------------------------------------
# 1. Canvas and elevation table  (spec §1)
# --------------------------------------------------------------------------------------

CANVAS = 8000                      # 8,000 x 8,000 blocks, centred on (0, 0)
HALF = CANVAS // 2                 # -4000 .. +4000
MIN_Y = -64
MAX_Y = 320
SEA_LEVEL = 62.0                   # "Sea level is anchored at Y = 62"
DATA_VERSION = 3953                # 1.21.1 (Pack 48)

#: Named elevations from the specification's feature table, in blocks.
ELEVATIONS: Dict[str, float] = {
    "bedrock_floor": -64.0,
    "abyss_floor": -32.0,          # Veil of Salt / abyssal trench floor
    "trench_top": 10.0,
    "caldera_floor": 40.0,         # Sunken Caldera Crater Floor: 38 - 42
    "caldera_throne": 92.0,        # Obsidian Throne spire
    "shelf_top": 56.0,
    "sea_level": SEA_LEVEL,
    "coast_low": 68.0,
    "coast_high": 74.0,
    "dunes_low": 82.0,
    "dunes_high": 96.0,
    "quarry_low": 85.0,
    "quarry_high": 110.0,
    "caldera_rim": 146.0,          # Volcanic rim: 142 - 156
    "taiga_low": 150.0,
    "taiga_high": 190.0,
    "spine_low": 220.0,
    "spine_high": 279.2,
    "treeline": 225.0,             # Still Life climatic treeline cutoff
    "ceiling": 320.0,
}

#: 16-bit normalisation (spec §1): h = (Y + 64) / 384, uint16 = round(h * 65535)
def norm_to_y(norm) -> np.ndarray:
    return -64.0 + np.asarray(norm, dtype=np.float64) * 384.0


def y_to_norm(y) -> np.ndarray:
    return (np.asarray(y, dtype=np.float64) + 64.0) / 384.0


def y_to_u16(y) -> np.ndarray:
    return np.rint(np.clip(y_to_norm(y), 0.0, 1.0) * 65535.0).astype(np.uint16)


# --------------------------------------------------------------------------------------
# 2. Continental shelf dropoff  (spec §1)
# --------------------------------------------------------------------------------------

SHELF_INNER = 3300.0               # R_inner
SHELF_OUTER = 3800.0               # R_outer
SHELF_DEPTH_SPEC = -600.0          # the document's H_drop magnitude


def shelf_drop(radius: np.ndarray, *, inner: float = SHELF_INNER,
               outer: float = SHELF_OUTER, depth: float = SHELF_DEPTH_SPEC,
               floor: float = ELEVATIONS["abyss_floor"]) -> np.ndarray:
    """Hermite S-curve dropoff beyond the continental shelf (spec §1).

    ``t = clamp((r - 3300) / 500)``, ``S(t) = 3t^2 - 2t^3``, ``H_drop = -600 * S(t)``.

    The document's ``-600`` cannot be applied literally: the same table puts the abyssal
    trench floor at Y = -32 and the world floor at Y = -64, so a six-hundred-block drop
    would fall through the bottom of the world.  The curve is therefore treated as the
    *shape* of the dropoff and its depth is clamped to the stated abyss floor, which is
    what makes the trench land on the Y = -32 .. 10 band the table describes.
    """
    r = np.asarray(radius, dtype=np.float64)
    t = np.clip((r - float(inner)) / max(float(outer) - float(inner), 1e-6), 0.0, 1.0)
    s = 3.0 * t * t - 2.0 * t * t * t
    return np.maximum(float(depth) * s, float(floor) - SEA_LEVEL)


# --------------------------------------------------------------------------------------
# 3. Lithosphere density ranges per region  (spec §3)
# --------------------------------------------------------------------------------------

@dataclass
class SplineRange:
    """The continentalness / erosion / ridges window a region occupies."""

    cont: Tuple[float, float]
    erosion: Tuple[float, float]
    ridges: Tuple[float, float]


@dataclass
class Climate:
    """The 5-tier climate window (spec §4), in the document's own units."""

    temp: Tuple[float, float]
    humidity: Tuple[float, float]


@dataclass
class Landmark:
    """One of the nine cardinal landmarks plus the Veil of Salt."""

    key: str
    name: str
    x: float
    y: float
    z: float
    box: Tuple[float, float, float, float]        # x1, x2, z1, z2
    y_range: Tuple[float, float]
    kind: str
    biomes: Tuple[str, ...]
    spline: SplineRange
    climate: Climate
    surface: str = ""
    notes: str = ""


LANDMARKS: List[Landmark] = [
    Landmark(
        key="forgotten_coast",
        name="Forgotten Coast",
        x=0.0, y=68.0, z=2500.0,
        box=(-600.0, 600.0, 2000.0, 3200.0),
        y_range=(68.0, 74.0),
        kind="coast",
        biomes=("plains", "meadow"),
        spline=SplineRange((0.10, 0.30), (-0.10, 0.20), (-0.30, 0.30)),
        climate=Climate((-0.15, 0.40), (-0.1, 0.8)),
        surface="grass_block, podzol, stony_shore",
        notes="spawn: cold pebble bluffs, rolling wildflower bluffs, "
              "ancient stone beacon, starter pier",
    ),
    Landmark(
        key="cogwork_march",
        name="Cogwork March",
        x=-2100.0, y=85.0, z=0.0,
        box=(-2800.0, -1400.0, -700.0, 700.0),
        y_range=(85.0, 110.0),
        kind="quarry",
        biomes=("windswept_hills", "wooded_badlands"),
        spline=SplineRange((0.25, 0.60), (-0.35, 0.15), (-0.80, -0.20)),
        climate=Climate((0.0, 0.5), (-0.4, 0.3)),
        surface="orange/yellow terracotta, stone, andesite",
        notes="concentric 9m quarry steps, brass river chasms",
    ),
    Landmark(
        key="ashen_caldera",
        name="The Ashen Caldera",
        x=0.0, y=80.0, z=0.0,
        box=(-750.0, 750.0, -750.0, 750.0),
        y_range=(38.0, 150.0),
        kind="caldera",
        biomes=("basalt_deltas", "eroded_badlands"),
        spline=SplineRange((0.20, 0.50), (-0.20, 0.30), (0.00, 0.40)),
        climate=Climate((0.70, 1.0), (-0.8, -0.2)),
        surface="basalt, blackstone, magma_block, obsidian",
        notes="volcanic ring wall Y=146, sunken crater basin Y=40, "
              "Obsidian Throne spire Y=92",
    ),
    Landmark(
        key="glacial_spine",
        name="Solitary Glacial Spine",
        x=0.0, y=220.0, z=-2500.0,
        box=(-1800.0, 1800.0, -3500.0, -1500.0),
        y_range=(180.0, 279.0),
        kind="cordillera",
        biomes=("frozen_peaks", "jagged_peaks", "grove"),
        spline=SplineRange((0.45, 0.90), (-0.80, -0.45), (0.50, 0.95)),
        climate=Climate((-1.0, -0.75), (-0.4, 0.4)),
        surface="snow_block, packed_ice, calcite, stone",
        notes="alpine cordillera, Matterhorn aretes, cirque tarns, "
              "flash-frozen pilgrim trails",
    ),
    Landmark(
        key="gilded_dunes",
        name="The Gilded Dunes",
        x=2300.0, y=75.0, z=0.0,
        box=(1600.0, 3100.0, -800.0, 800.0),
        y_range=(75.0, 94.0),
        kind="dunes",
        biomes=("desert", "badlands"),
        spline=SplineRange((0.20, 0.55), (0.25, 0.70), (-0.40, 0.40)),
        climate=Climate((0.70, 1.0), (-0.8, -0.2)),
        surface="red_sand, sandstone, terracotta (table) / black glass crests (map)",
        notes="vitrified black-glass dunes, 45 degree barchan ridges, "
              "terracotta canyon mesas",
    ),
    Landmark(
        key="whispering_fen",
        name="The Whispering Fen",
        x=2000.0, y=63.0, z=2000.0,
        box=(1300.0, 2700.0, 1300.0, 2700.0),
        y_range=(62.0, 66.0),
        kind="fen",
        biomes=("swamp", "mangrove_swamp"),
        spline=SplineRange((0.05, 0.25), (0.40, 0.85), (-0.20, 0.20)),
        climate=Climate((0.35, 0.60), (0.5, 1.0)),
        surface="mud, peat, moss, coarse_dirt",
        notes="flat sunken bayous, braided delta channels, giant fungal heartwood trees",
    ),
    Landmark(
        key="sunken_reach",
        name="The Sunken Reach",
        x=-2400.0, y=54.0, z=1600.0,
        box=(-3200.0, -1700.0, 1000.0, 2300.0),
        y_range=(50.0, 62.0),
        kind="drowned_shelf",
        biomes=("warm_ocean", "lukewarm_ocean"),
        spline=SplineRange((-0.35, -0.10), (0.10, 0.50), (-0.50, 0.50)),
        climate=Climate((0.35, 0.60), (0.4, 1.0)),
        surface="sand, coral reefs, prismarine gravel",
        notes="drowned coastal caldera shelf (100 fathoms), coral atolls, "
              "barrier sandbars",
    ),
    Landmark(
        key="hermits_spire",
        name="The Hermit's Spire",
        x=-1800.0, y=140.0, z=-1800.0,
        box=(-2300.0, -1300.0, -2300.0, -1300.0),
        y_range=(140.0, 185.0),
        kind="spires",
        biomes=("windswept_hills", "meadow"),
        spline=SplineRange((0.30, 0.60), (-0.60, -0.20), (0.20, 0.70)),
        climate=Climate((-0.60, -0.25), (-0.2, 0.6)),
        surface="granite, stone, cobblestone",
        notes="solitary granite needles, ascetic rope bridges, "
              "silent wind-scoured cloisters",
    ),
    Landmark(
        key="byzantine_choir",
        name="The Byzantine Choir",
        x=1800.0, y=120.0, z=-1800.0,
        box=(1300.0, 2300.0, -2300.0, -1300.0),
        y_range=(110.0, 145.0),
        kind="terraces",
        biomes=("meadow", "cherry_grove"),
        spline=SplineRange((0.30, 0.65), (-0.40, 0.10), (0.10, 0.60)),
        climate=Climate((-0.15, 0.40), (-0.1, 0.8)),
        surface="cherry terraces, stone, gilded ruins",
        notes="gilded resonant basilicas, pink cherry terraces, harmonic acoustic ruins",
    ),
]

#: The Veil of Salt: everything beyond radius 3550 (spec §2, RIM row)
VEIL_RADIUS = 3550.0
VEIL = Landmark(
    key="veil_of_salt",
    name="The Veil of Salt",
    x=0.0, y=0.0, z=0.0,
    box=(-HALF, HALF, -HALF, HALF),
    y_range=(ELEVATIONS["abyss_floor"], SEA_LEVEL),
    kind="veil",
    biomes=("deep_cold_ocean", "deep_ocean"),
    spline=SplineRange((-0.90, -0.45), (-0.60, 0.40), (-1.00, 1.00)),
    climate=Climate((-1.0, 1.0), (-1.0, 1.0)),
    surface="gravel, deepslate, calcite salt crust",
    notes="syrupy caustic brine abyss, white salt crusts",
)


# --------------------------------------------------------------------------------------
# 4. Landmark envelopes
# --------------------------------------------------------------------------------------


def region_ids(shape: Tuple[int, int], region, *, landmarks: Sequence[Landmark] = LANDMARKS,
               include_veil: bool = True) -> np.ndarray:
    """Per-cell landmark id, ``-1`` for terrain governed only by the base noise.

    Cells are assigned by *bounding box* exactly as the specification lists them; where
    boxes overlap the smaller box wins, so a compact landmark is never swallowed by a
    large neighbour.
    """
    h, w = shape
    cs = float(region.cell_size)
    xs = region.x0 + (np.arange(w) + 0.5) * cs
    zs = region.z0 + (np.arange(h) + 0.5) * cs
    X, Z = np.meshgrid(xs, zs)
    ids = np.full((h, w), -1, dtype=np.int16)
    areas: List[Tuple[float, int]] = []
    for idx, lm in enumerate(landmarks):
        x1, x2, z1, z2 = lm.box
        area = (x2 - x1) * (z2 - z1)
        areas.append((area, idx))
    for _area, idx in sorted(areas, reverse=True):    # large first, small overwrite
        lm = landmarks[idx]
        x1, x2, z1, z2 = lm.box
        inside = (X >= x1) & (X <= x2) & (Z >= z1) & (Z <= z2)
        ids[inside] = idx
    if include_veil:
        r = np.hypot(X, Z)
        ids[r > VEIL_RADIUS] = len(landmarks)          # the Veil of Salt sentinel
    return ids


def feather(inside: np.ndarray, *, cell_size: float, width_blocks: float) -> np.ndarray:
    """Taper a hard mask at its border so a stamped landform blends into its surroundings."""
    from scipy import ndimage

    if width_blocks <= 0:
        return inside.astype(np.float64)
    dist = ndimage.distance_transform_edt(inside) * float(cell_size)
    return np.clip(dist / max(float(width_blocks), 1e-6), 0.0, 1.0) ** 0.8
