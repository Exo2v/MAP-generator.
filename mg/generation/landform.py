"""Ashenfall landform shaping: turns the specification's landmark table into terrain.

Each landmark is stamped as a *target elevation field* which is then blended into the
underlying continent with a feathered edge, so the nine landforms sit inside one
continuous landmass instead of looking like nine patches dropped onto a map.

The module returns, alongside the shaped DEM:

* a per-cell landmark id (used to override biome, climate and materials),
* a lava mask (the caldera basin is a lava basin - the specification's material row
  says basalt / magma / lava),
* a "bare rock" weight the decoration layer must respect (volcanic rules override
  every foliage rule).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from ..core.instances import catmull_rom, poisson_stations, render_arete, render_peak
from ..core.noise import fbm, smoothstep, stretch01
from .landmarks import (ELEVATIONS, HALF, LANDMARKS, Landmark, SEA_LEVEL, SHELF_INNER,
                        SHELF_OUTER, VEIL_RADIUS, feather, region_ids, shelf_drop)

VEIL_NAME = "veil_of_salt"

#: Which material family each landmark stamps.  Used by the surface rules.
BARE_KINDS = ("caldera", "dunes", "cordillera", "spires", "quarry")

#: Landforms that must not pond, because they are *shaped* rather than eroded and so are
#: riddled with closed sub-basins: quarry benches step down into themselves, dune swales
#: are walled in by crests, and the caldera rim encircles its surroundings.  The
#: depression filler floods all of them, and water standing in a desert dune field or a
#: drained quarry is not a plausible reading of the specification.
#:
#: Deliberately absent: the Whispering Fen and the Sunken Reach hold water by definition
#: (marsh and a drowned shelf), and the cordillera and spire fields are *natural* relief
#: where a glacial tarn is exactly what one would expect - those keep their lakes.
DRY_KINDS = ("caldera", "dunes", "quarry", "terraces", "coast")


@dataclass
class LandformResult:
    dem: np.ndarray
    ids: np.ndarray                 # (h, w) int16, -1 = freely generated terrain
    lava: np.ndarray                # (h, w) bool - fill with lava
    bare: np.ndarray                # (h, w) float 0..1 - "no decoration" weight
    basin: np.ndarray               # (h, w) bool - crater interior, never flooded
    #: (h, w) bool - every cell the water system must leave alone: the crater interior plus
    #: each closed-basin landform (see DRY_KINDS).  Fed to the water build as ``no_water``.
    dry: np.ndarray
    diagnostics: Dict[str, float]


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


def _radial(X: np.ndarray, Z: np.ndarray, cx: float, cz: float) -> np.ndarray:
    return np.hypot(X - cx, Z - cz)


def _cell(xs: np.ndarray) -> float:
    return float(abs(xs[0, 1] - xs[0, 0])) if xs.shape[1] > 1 else 4.0


def _feather_noisy(inside: np.ndarray, *, cell_size: float, width_blocks: float,
                   edge: np.ndarray, amplitude: float = 0.5) -> np.ndarray:
    """Feathered mask whose boundary is domain-warped by ``edge``.

    A plain distance feather still reads as the rectangle it was cut from.  Warping the
    *signed* distance field moves the boundary itself, so the landform dissolves along an
    irregular front - and, unlike adding noise to the finished weight, a cell far outside
    the mask stays at exactly zero influence.
    """
    if width_blocks <= 0:
        return inside.astype(np.float64)
    from scipy import ndimage

    d_in = ndimage.distance_transform_edt(inside) * float(cell_size)
    d_out = ndimage.distance_transform_edt(~inside) * float(cell_size)
    signed = d_in - d_out + np.asarray(edge, dtype=np.float64) * amplitude * float(width_blocks)
    return np.clip(signed / max(float(width_blocks), 1e-6), 0.0, 1.0) ** 0.85


# --------------------------------------------------------------------------------------
# individual landforms
# --------------------------------------------------------------------------------------


def shape_caldera(X, Z, base, seed: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Spec §2 landmark 3: ring wall at Y=146, crater basin at Y=40, throne spire Y=92."""
    r0 = _radial(X, Z, 0.0, 0.0)
    # the wall is not a bullseye: its radius wanders by a hundred blocks
    r = np.maximum(r0 + fbm(X, Z, octaves=3, scale=380.0, seed=seed + 397) * 95.0, 0.0)
    ring_r = 470.0                     # the wall sits inside the +-750 box
    ring_w = 150.0
    # ring crest wobbles so the caldera is not a perfect circle
    wobble = 1.0 + 0.20 * fbm(X, Z, octaves=3, scale=300.0, seed=seed + 401)
    wall = ELEVATIONS["caldera_rim"] * np.exp(-((r - ring_r) / ring_w) ** 2) * wobble

    basin_r = 300.0
    basin_floor = ELEVATIONS["caldera_floor"]
    basin = basin_floor + 2.5 * np.clip(r / basin_r, 0.0, 1.0)
    # the throne: a narrow obsidian spire rising out of the basin to Y=92
    spire = (ELEVATIONS["caldera_throne"] - basin_floor) * np.exp(-(r / 60.0) ** 2)

    caldera = np.maximum(basin + spire, wall)
    inner = smoothstep(basin_r, basin_r * 1.30, r)       # 0 in basin, 1 at the wall foot
    caldera = caldera * (1.0 - inner) + np.maximum(basin + spire, base * 0.35) * inner
    caldera = np.maximum(caldera, wall)

    outer = smoothstep(ring_r + ring_w * 0.8, ring_r + ring_w * 3.2, r)
    target = caldera * (1.0 - outer) + base * outer
    # lava pools on the crater floor, but never on the throne itself
    lava = (r < basin_r * 0.88) & (target < basin_floor + 7.0) & (spire < 8.0)
    # The crater is a *lava* basin: nothing inside the ring may be flooded, or the water
    # system would fill the bowl to the rim and turn the caldera into a crater lake.
    basin = r < ring_r * 1.18
    return target, lava, basin


def shape_quarry(X, Z, base, lm: Landmark, seed: int) -> np.ndarray:
    """Spec §2 landmark 2: concentric 9 m quarry benches, brass river chasms."""
    r = _radial(X, Z, lm.x, lm.z)
    step = 9.0
    cone = np.clip(1.0 - r / 950.0, 0.0, 1.0)
    # floor((y + 34) / 9) * 9 with the pit bottom at the centre: 85, 94, 103, 112 ...
    benches = np.floor((lm.y + 34.0 * (1.0 - cone)) / step) * step
    # roughen the ring edges so they read as cut rock, not as a bullseye
    grit = fbm(X, Z, octaves=3, scale=110.0, seed=seed + 511) * step * 0.4
    return np.maximum(benches + grit, ELEVATIONS["quarry_low"] - 22.0)


def shape_cordillera(X, Z, base, lm: Landmark, seed: int) -> np.ndarray:
    """Spec §2 landmark 4: alpine cordillera, Matterhorn aretes, cirque tarns."""
    cs = _cell(X)
    ox, oz = float(X[0, 0]), float(Z[0, 0])
    x1, x2, z1, z2 = lm.box
    span = x2 - x1
    # the spine runs along the long axis (X), bowed so the range is not a straight wall
    waypoints = [
        (x1 + 0.02 * span, lm.z - 240.0),
        (x1 + 0.26 * span, lm.z + 140.0),
        (x1 + 0.50 * span, lm.z - 60.0),
        (x1 + 0.74 * span, lm.z - 280.0),
        (x1 + 0.98 * span, lm.z - 20.0),
    ]
    relief = np.zeros_like(base)
    xs_, zs_, dxs, dzs = catmull_rom(waypoints, samples=768)
    rng = np.random.default_rng(seed & 0xFFFFFFFF)
    stations = poisson_stations(xs_, zs_, dxs, dzs, 30, min_spacing=200.0,
                                jitter=60.0, rng=rng)
    # the specification pins a summit at the landmark's exact centre coordinate
    stations.insert(0, (lm.x, lm.z, 0.0))
    for i, (sx, sz, angle) in enumerate(stations):
        lx, lz = sx - ox, sz - oz          # renderers work in grid-local coordinates
        if rng.random() < 0.45:
            render_peak(relief, lx, lz, ELEVATIONS["spine_high"] - 10.0,
                        radius=float(rng.uniform(150.0, 210.0)),
                        arms=int(rng.integers(3, 6)), gamma=1.9, arm_amp=0.26,
                        cell=cs, blend_k=26.0, seed=seed + i)
        else:
            render_arete(relief, lx, lz, angle, ELEVATIONS["spine_high"] - 24.0,
                         length=float(rng.uniform(320.0, 620.0)),
                         sigma=float(rng.uniform(38.0, 62.0)), cell=cs,
                         blend_k=26.0, seed=seed + i)
    return np.maximum(base, relief)


def shape_dunes(X, Z, base, lm: Landmark, seed: int) -> np.ndarray:
    """Spec §2 landmark 5: barchan dunes with vitrified glass crests.

    Barchans are asymmetric - a long gentle windward ramp and a short 45 degree lee
    face - so the ridge train is warped and its negative half compressed.
    """
    warp = fbm(X, Z, octaves=3, scale=900.0, seed=seed + 601)
    # spec: 45 degree barchan ridges - the crest lines run diagonally across the field
    phase = (X + Z) / 230.0 * 2.0 * np.pi + warp * 9.0
    cross = (X - Z * 0.9) / 1900.0 * 2.0 * np.pi
    s = np.sin(phase) * (0.75 + 0.25 * np.sin(cross))
    # barchans: a long windward ramp, a short 45 degree lee face
    asym = np.where(s >= 0.0, np.abs(s) ** 0.65, -(np.abs(s) ** 1.7))
    # a dune sea is not uniform: large-scale envelope gives dunes and interdune flats
    envelope = stretch01(fbm(X, Z, octaves=3, scale=1500.0, seed=seed + 605), 3.0, 97.0)
    crest = asym * (0.35 + 0.65 * envelope)
    # spec: low mesas at Y=75, crests at Y=94
    dunes = 76.0 + 16.0 * (crest * 0.5 + 0.5)
    grit = fbm(X, Z, octaves=4, scale=110.0, seed=seed + 607) * 2.0
    # a few terracotta mesas stand between the dunes
    plateau = stretch01(fbm(X, Z, octaves=3, scale=760.0, seed=seed + 611), 4.0, 96.0)
    mesa = smoothstep(0.62, 0.82, plateau) * 24.0
    return np.maximum(dunes + grit + mesa, ELEVATIONS["sea_level"] + 4.0)


def shape_fen(X, Z, base, lm: Landmark, seed: int) -> np.ndarray:
    """Spec §2 landmark 6: flat sunken bayou at Y=62-66 with braided delta channels."""
    r = _radial(X, Z, lm.x, lm.z)
    # a bayou is a bowl: flat peat at the centre, rising to the surrounding forest rim
    target = 63.5 + 26.0 * np.clip((r - 420.0) / 620.0, 0.0, 1.0) ** 1.6
    target = target + fbm(X, Z, octaves=3, scale=440.0, seed=seed + 701) * 1.8
    braid = np.ones_like(target)
    for k in range(5):
        off = (k - 2) * 150.0
        wob = fbm(X, Z, octaves=2, scale=380.0, seed=seed + 710 + k) * 200.0
        d = np.abs((X + Z) * 0.5 - off + wob)
        braid = np.minimum(braid, np.clip(d / 30.0, 0.0, 1.0))
    return target - (1.0 - braid) * 3.4


def shape_drowned_shelf(X, Z, base, lm: Landmark, seed: int) -> np.ndarray:
    """Spec §2 landmark 7: drowned caldera shelf, coral atolls, barrier sandbars."""
    shelf = 53.0 + fbm(X, Z, octaves=4, scale=560.0, seed=seed + 801) * 3.5
    bars = stretch01(fbm(X, Z, octaves=2, scale=320.0, seed=seed + 807), 6.0, 94.0)
    shelf = shelf + smoothstep(0.70, 0.86, bars) * 8.0        # barrier sandbars
    atoll = stretch01(fbm(X, Z, octaves=3, scale=280.0, seed=seed + 811), 8.0, 92.0)
    shelf = shelf + smoothstep(0.80, 0.94, atoll) * 12.0      # coral atolls
    return np.clip(shelf, lm.y_range[0] - 3.0, SEA_LEVEL + 1.5)


def shape_spires(X, Z, base, lm: Landmark, seed: int) -> np.ndarray:
    """Spec §2 landmark 8: solitary granite needles."""
    cs = _cell(X)
    ox, oz = float(X[0, 0]), float(Z[0, 0])
    relief = np.zeros_like(base)
    rng = np.random.default_rng((seed + 901) & 0xFFFFFFFF)
    r = _radial(X, Z, lm.x, lm.z)
    # the centre coordinate sits on a granite bench at Y = 140; the needles rise to 185
    bench = lm.y * np.clip(1.0 - (r / 620.0) ** 2.0, 0.0, 1.0)
    for i in range(int(rng.integers(6, 10))):
        lx = lm.x + float(rng.uniform(-420, 420)) - ox
        lz = lm.z + float(rng.uniform(-420, 420)) - oz
        render_peak(relief, lx, lz, float(rng.uniform(38.0, 46.0)),
                    float(rng.uniform(46.0, 84.0)), arms=int(rng.integers(3, 5)),
                    gamma=2.4, arm_amp=0.18, cell=cs, blend_k=18.0, seed=seed + i)
    return np.maximum(base, bench + relief)


def shape_terraces(X, Z, base, lm: Landmark, seed: int) -> np.ndarray:
    """Spec §2 landmark 9: stepped highland terraces for the Byzantine Choir."""
    r = _radial(X, Z, lm.x, lm.z)
    climb = np.clip(1.0 - r / 660.0, 0.0, 1.0)
    # spec centre is Y=120 inside a 110-145 amphitheatre: the rim is the high ground
    target = lm.y_range[1] - climb * (lm.y_range[1] - lm.y_range[0])
    step = 6.0
    target = np.floor(target / step) * step
    return target + fbm(X, Z, octaves=3, scale=320.0, seed=seed + 1001) * step * 0.7


def shape_coast(X, Z, base, lm: Landmark, seed: int) -> np.ndarray:
    """Spec §2 landmark 1: cold pebble bluffs and rolling wildflower bluffs.

    The contour that matters is the one the continent already has: the box is the band of
    ground between Y = 68 and Y = 74, so the shaper keeps the existing relief and clamps
    it into that band.  Inland it flattens to rolling bluffs; seaward it follows the beach
    down into the surf, which puts the shoreline exactly where the coast spline put it.
    """
    b = np.asarray(base, dtype=np.float64)
    rolling = fbm(X, Z, octaves=4, scale=360.0, seed=seed + 1101) * 3.0
    bluffs = fbm(X, Z, octaves=3, scale=95.0, seed=seed + 1107) * 1.4
    inland = np.clip(b, ELEVATIONS["coast_low"] - 10.0, ELEVATIONS["coast_high"] + 1.0)
    return np.minimum(inland + rolling + bluffs, ELEVATIONS["coast_high"] + 4.0)


SHAPERS: Dict[str, Callable] = {
    "quarry": shape_quarry,
    "cordillera": shape_cordillera,
    "dunes": shape_dunes,
    "fen": shape_fen,
    "drowned_shelf": shape_drowned_shelf,
    "spires": shape_spires,
    "terraces": shape_terraces,
    "coast": shape_coast,
}


# --------------------------------------------------------------------------------------
# the continent
# --------------------------------------------------------------------------------------


def apply_ashenfall(
    dem: np.ndarray,
    region,
    *,
    seed: int,
    sea_level: float = SEA_LEVEL,
    shelf: bool = True,
    landmarks: Sequence[Landmark] = LANDMARKS,
    apply_landmarks: bool = True,
) -> LandformResult:
    """Shape ``dem`` into the continent of Vantyra and report what was applied."""
    h, w = dem.shape
    cs = float(region.cell_size)
    xs = region.x0 + (np.arange(w) + 0.5) * cs
    zs = region.z0 + (np.arange(h) + 0.5) * cs
    X, Z = np.meshgrid(xs, zs)
    dem = np.asarray(dem, dtype=np.float64).copy()
    diag: Dict[str, float] = {}

    # ---- 1. radial shelf: the continent fades into the Veil of Salt ---------------------
    radius = np.hypot(X, Z)
    if shelf:
        inner_w = smoothstep(SHELF_INNER - 900.0, SHELF_INNER, radius)
        outer_w = smoothstep(SHELF_INNER, SHELF_OUTER, radius)
        abyss_w = smoothstep(VEIL_RADIUS, VEIL_RADIUS + 220.0, radius)
        drop = shelf_drop(radius)
        # the shelf break takes the coastal plain down with it
        dem = dem * (1.0 - inner_w * 0.7) + (sea_level + drop) * (inner_w * 0.7)
        floor = np.maximum(sea_level + drop, ELEVATIONS["abyss_floor"])
        dem = dem * (1.0 - outer_w) + floor * outer_w
        # the Veil of Salt: a flat, dead brine floor crusted with salt
        veil_floor = ELEVATIONS["abyss_floor"] + 3.0 + 5.0 * stretch01(
            fbm(X, Z, octaves=3, scale=1400.0, seed=seed + 1301), 3.0, 97.0
        )
        dem = dem * (1.0 - abyss_w) + veil_floor * abyss_w
        diag["shelf_drop_min"] = float(np.min(drop))
        diag["veil_cells"] = float(np.count_nonzero(radius > VEIL_RADIUS))

    # ---- 2. the nine landmarks ---------------------------------------------------------
    ids = region_ids(dem.shape, region, landmarks=list(landmarks), include_veil=True)
    lava = np.zeros(dem.shape, dtype=bool)
    basin = np.zeros(dem.shape, dtype=bool)
    bare = np.zeros(dem.shape, dtype=np.float64)

    if apply_landmarks:
        for idx, lm in enumerate(landmarks):
            inside = ids == idx
            if not np.any(inside):
                continue
            # The specification's bounding boxes are extents, not plates: the mask is
            # feathered over a long distance and the ramp itself is domain-warped so the
            # landform dissolves into the continent along an irregular edge.
            soft = lm.kind in ("fen", "drowned_shelf", "coast")
            near = lm.kind in ("caldera", "quarry")
            width = max(200.0, cs * (26.0 if soft else (14.0 if near else 30.0)))
            edge = fbm(X, Z, octaves=3, scale=max(260.0, width * 1.6),
                       seed=seed + 3000 + idx * 13)
            w_ = _feather_noisy(inside, cell_size=cs, width_blocks=width,
                                edge=edge, amplitude=0.55)
            if lm.kind == "caldera":
                target, lava_lm, basin_lm = shape_caldera(X, Z, dem, seed)
                blend = np.clip(w_ * 1.8, 0.0, 1.0)
                dem = dem * (1.0 - blend) + target * blend
                lava |= lava_lm & inside
                basin |= basin_lm & inside
                bare = np.maximum(bare, np.clip(w_ * 1.4, 0.0, 1.0))
            else:
                fn = SHAPERS.get(lm.kind)
                if fn is None:
                    continue
                target = fn(X, Z, dem, lm, seed + idx * 137)
                # landforms blend rather than overwrite: they lift or flatten the
                # continent instead of replacing a rectangle of it
                soft = lm.kind in ("fen", "drowned_shelf", "coast")
                # excavated and wind-carved landforms replace the continent outright;
                # the cordillera composes with it (it is a maximum, not a blend)
                hard = lm.kind in ("quarry", "dunes", "terraces", "spires")
                strong = np.clip(w_ * (1.25 if soft else (2.0 if hard else 0.9)), 0.0, 1.0)
                dem = dem * (1.0 - strong) + target * strong
            if lm.kind in BARE_KINDS:
                bare = np.maximum(bare, w_ * (0.9 if lm.kind == "caldera" else 0.55))
            # The specification lists an exact centre coordinate for every landmark, so
            # the shaped ground is pinned to that Y around the centre point: the landform
            # keeps its character but the coordinate itself is never a few blocks out.
            # (The caldera is exempt - its centre is the Obsidian Throne spire.)
            if lm.kind != "caldera":
                pin_r = 260.0 if lm.kind in ("dunes", "cordillera") else 150.0
                d = _radial(X, Z, lm.x, lm.z)
                pin = np.clip(1.0 - d / pin_r, 0.0, 1.0) ** 2.0
                dem = dem * (1.0 - pin) + lm.y * pin

    # ---- 2b. no-ponding mask ------------------------------------------------------------
    # The crater interior is never flooded, and neither is any closed-basin landform: a
    # stepped quarry, a dune field and a caldera rim all enclose pockets that `fill_lakes`
    # would otherwise turn into ponds.  The water system already honours this mask for the
    # caldera, so widening it costs nothing and keeps the specification's landforms dry.
    dry = np.array(basin, dtype=bool)
    for idx, lm in enumerate(landmarks):
        if lm.kind in DRY_KINDS:
            dry |= ids == idx

    # ---- 3. clamp to the elevation table -----------------------------------------------
    dem = np.clip(dem, ELEVATIONS["abyss_floor"], ELEVATIONS["spine_high"] + 4.0)
    counts = {lm.key: int(np.count_nonzero(ids == i)) for i, lm in enumerate(landmarks)}
    counts[VEIL_NAME] = int(np.count_nonzero(ids == len(landmarks)))
    diag["landmark_cells"] = float(sum(v for k, v in counts.items() if k != VEIL_NAME))
    diag["lava_cells"] = float(np.count_nonzero(lava))
    for key, value in counts.items():
        diag[f"cells_{key}"] = float(value)
    diag["dry_cells"] = float(np.count_nonzero(dry))
    return LandformResult(dem=dem, ids=ids, lava=lava, bare=bare, basin=basin, dry=dry,
                          diagnostics=diag)


def repin_landmark_centres(
    dem: np.ndarray,
    X: np.ndarray,
    Z: np.ndarray,
    *,
    water_mask: Optional[np.ndarray] = None,
    water_level: Optional[np.ndarray] = None,
    landmarks: Sequence[Landmark] = LANDMARKS,
    radius: float = 220.0,
    strength: float = 1.0,      # land exactly on the specified elevation at the centre
    max_delta: float = 6.0,
) -> Tuple[np.ndarray, Dict[str, float]]:
    """Pull the finished surface back onto each landmark's specified centre elevation.

    ``apply_ashenfall`` pins every centre while the landform is being stamped, but erosion
    and hydrology run afterwards and both move the ground - a river crossing the Gilded
    Dunes cuts two or three blocks out of it.  Since the specification lists an exact
    elevation for each landmark, the pin is re-applied here to the *finished* heightfield:
    a small, smooth, radius-limited nudge - never more than ``max_delta`` blocks - rather
    than a flattening of the landform.

    Water is carried with the ground it sits on.  A channel whose bed is lifted has its
    surface lifted by the same amount, so the cross-section survives instead of leaving
    the water stranded below its own bank.  Lakes and the sea are left alone entirely, and
    the caldera is skipped because its centre is the Obsidian Throne, not a ground level.

    Returns ``(dem, diagnostics)``.
    """
    dem = np.asarray(dem, dtype=np.float64).copy()
    stats: Dict[str, float] = {}
    wm = None if water_mask is None else np.asarray(water_mask)
    wl = None if water_level is None else np.asarray(water_level)

    for lm in landmarks:
        if lm.kind == "caldera":
            continue
        d = _radial(X, Z, lm.x, lm.z)
        w = np.clip(1.0 - d / float(radius), 0.0, 1.0) ** 2.0
        if not bool((w > 0).any()):
            continue
        delta = np.clip(float(lm.y) - dem, -float(max_delta), float(max_delta)) * w * strength
        if wm is not None:
            # never disturb a lake or the sea; a river keeps its cross-section
            delta = np.where((wm == 0) | (wm == 1), delta, 0.0)
        if not bool((delta != 0.0).any()):
            continue
        dem = dem + delta
        if wl is not None and wm is not None:
            river = (wm == 1) & (delta != 0.0)
            if bool(river.any()):
                wl[river] = wl[river] + delta[river]
        stats[f"repin_{lm.key}"] = float(np.abs(delta).max())

    stats["repin_max"] = float(max(stats.values())) if stats else 0.0
    return dem, stats
