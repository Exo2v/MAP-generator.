"""Erosion + terrain diffusion.

Three complementary processes, all the things World Machine's erosion device does:

* ``thermal_erosion``      - hillslope diffusion; material creeps down-slope until the
                             talus angle is reached.  This is the "terrain diffusion"
                             pass: it smooths noise octaves into believable slopes.
* ``stream_power_erosion`` - detachment-limited incision ``E = K A^m S^n`` (Howard,
                             Whipple & Tucker).  Combined with flow accumulation this is
                             what carves dendritic valley networks and gives ridges
                             their branching spurs.
* ``droplet_erosion``      - particle based erosion/deposition for fine detail (gullies,
                             alluvial fans).  Opt-in because it is the slow one.

``hydraulic_erosion`` chains them in the order a terrain artist would use, and then
``deposit_sediment`` drops the mobilised material where the gradient flattens out.
"""

from __future__ import annotations

import math
from typing import Optional, Tuple

import numpy as np

from .hydrology import flow_accumulation, flow_directions, priority_flood

__all__ = [
    "slope_map",
    "curvature",
    "hillshade",
    "thermal_erosion",
    "talus_relaxation",
    "stream_power_erosion",
    "droplet_erosion",
    "deposit_sediment",
    "hydraulic_erosion",
    "diffusion_pass",
]


def slope_map(dem: np.ndarray, cell_size: float = 1.0) -> np.ndarray:
    """Gradient magnitude in blocks of rise per block of run."""
    dy, dx = np.gradient(np.asarray(dem, dtype=np.float64), cell_size)
    return np.hypot(dx, dy)


def curvature(dem: np.ndarray, cell_size: float = 1.0) -> np.ndarray:
    """Laplacian (negative == concave/valley, positive == convex/ridge)."""
    dem = np.asarray(dem, dtype=np.float64)
    lap = -4.0 * dem
    lap += np.roll(dem, 1, 0) + np.roll(dem, -1, 0) + np.roll(dem, 1, 1) + np.roll(dem, -1, 1)
    return lap / (cell_size * cell_size)


def hillshade(dem: np.ndarray, cell_size: float = 1.0, azimuth: float = 315.0,
              altitude: float = 45.0, z_factor: float = 1.4) -> np.ndarray:
    """Classic hillshade in ``[0, 1]`` - used by the 2D map preview."""
    dem = np.asarray(dem, dtype=np.float64)
    dy, dx = np.gradient(dem * z_factor, cell_size)
    slope = np.arctan(np.hypot(dx, dy))
    aspect = np.arctan2(-dx, dy)
    az = math.radians(360.0 - azimuth + 90.0)
    alt = math.radians(altitude)
    shaded = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect)
    return np.clip(shaded, 0.0, 1.0)


def _laplacian(dem: np.ndarray) -> np.ndarray:
    lap = -4.0 * dem
    lap += np.roll(dem, 1, 0) + np.roll(dem, -1, 0) + np.roll(dem, 1, 1) + np.roll(dem, -1, 1)
    return lap


def thermal_erosion(dem: np.ndarray, *, iterations: int = 30, rate: float = 0.28,
                    talus: float = 1.2, cell_size: float = 1.0) -> np.ndarray:
    """Diffusive hillslope relaxation (a.k.a. thermal erosion).

    Material moves from each cell to its lower neighbours, but only the *excess* above
    the talus angle is moved, so cliffs steeper than ``talus`` blocks per block get
    rounded off while gentle ground is barely touched.
    """
    dem = np.asarray(dem, dtype=np.float64).copy()
    h, w = dem.shape
    for _ in range(max(0, int(iterations))):
        moved = np.zeros_like(dem)
        received = np.zeros_like(dem)
        for dz, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nz = np.clip(np.arange(h)[:, None] + dz, 0, h - 1)
            nx = np.clip(np.arange(w)[None, :] + dx, 0, w - 1)
            diff = dem - dem[nz, nx]
            over = np.maximum(diff - talus * cell_size, 0.0)
            transfer = np.minimum(over * rate, np.maximum(diff, 0.0)) * 0.25
            moved += transfer
            received[nz, nx] += transfer
        dem += received - moved
    return dem


def talus_relaxation(dem: np.ndarray, *, talus: float = 1.4, iterations: int = 20,
                     cell_size: float = 1.0) -> np.ndarray:
    """Hard angle-of-repose limiter - removes impossible spikes and cliffs.

    Any drop steeper than ``talus`` blocks of rise per block of run is split between the
    two cells involved.  Unlike :func:`thermal_erosion` this is *rate limited by the
    violation itself*, so a single sharp spike is ironed out in a few passes while gentle
    ground is untouched.
    """
    dem = np.asarray(dem, dtype=np.float64).copy()
    h, w = dem.shape
    max_drop = float(talus) * float(cell_size)
    rows, cols = np.mgrid[0:h, 0:w]
    for _ in range(max(0, int(iterations))):
        moved_any = False
        for dz, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            rz = rows + dz
            cx = cols + dx
            valid = (rz >= 0) & (rz < h) & (cx >= 0) & (cx < w)
            nz = np.clip(rz, 0, h - 1)
            nx = np.clip(cx, 0, w - 1)
            neighbour = dem[nz, nx]
            excess = np.where(valid, dem - neighbour - max_drop, 0.0)
            if not np.any(excess > 1e-9):
                continue
            moved_any = True
            move = excess * 0.5
            dem -= move
            received = np.zeros_like(dem)
            np.add.at(received, (nz[valid], nx[valid]), move[valid])
            dem += received
        if not moved_any:
            break
    return dem


def stream_power_erosion(
    dem: np.ndarray,
    *,
    cell_size: float = 4.0,
    iterations: int = 10,
    k: float = 0.55,
    m: float = 0.6,
    n: float = 1.0,
    dt: float = 1.0,
    uplift: float = 0.0,
    max_incision: float = 1.2,
    min_slope: float = 1e-4,
    reference_area: float = 260.0,
) -> Tuple[np.ndarray, np.ndarray]:
    """Detachment-limited fluvial incision.  Returns ``(dem, accum)``.

    ``E = k * (A / A_ref)^m * S^n`` - drainage area is normalised against
    ``reference_area`` so the rate stays meaningful across cell sizes and region
    sizes, and each iteration re-routes flow on the *current* surface.  That
    re-routing is what makes the drainage network organise itself into dendritic,
    self-similar valleys instead of a static painted pattern.
    """
    dem = np.asarray(dem, dtype=np.float64).copy()
    accum = np.ones_like(dem)
    max_incision = float(max_incision)
    for _ in range(max(0, int(iterations))):
        filled, _ = priority_flood(dem, epsilon=1e-3)
        dirs = flow_directions(filled)
        accum = flow_accumulation(filled, dirs)
        slope = slope_map(dem, cell_size)
        relative_area = np.maximum(accum, 1.0) / max(reference_area, 1.0)
        erosion = k * np.power(relative_area, m) * np.power(np.maximum(slope, min_slope), n) * dt
        erosion = np.minimum(erosion, max_incision)
        dem -= erosion
        dem += uplift * dt
    return dem, accum


def _droplet_pass(dem: np.ndarray, n_droplets: int, *, seed: int, inertia: float = 0.05,
                  capacity: float = 4.0, deposit_rate: float = 0.3, erode_rate: float = 0.3,
                  gravity: float = 4.0, evaporate: float = 0.02, radius: int = 3,
                  max_steps: int = 48) -> np.ndarray:
    """Particle erosion.  Returns a flat delta that should be added to the DEM.

    Kept in plain Python on purpose: numpy scalar indexing is slower than float maths
    for this branchy, single-particle-at-a-time algorithm.
    """
    h, w = dem.shape
    height = dem.ravel().tolist()
    delta = [0.0] * (h * w)
    rng = np.random.default_rng(seed)
    # Pre-roll the brush weights for the deposition radius.
    weights = []
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            d2 = dx * dx + dy * dy
            if d2 < radius * radius:
                weights.append((dx, dy, 1.0 - math.sqrt(d2) / radius))
    wsum = sum(wt for _, _, wt in weights)

    for _ in range(max(0, int(n_droplets))):
        px = rng.random() * (w - 3) + 1.0
        py = rng.random() * (h - 3) + 1.0
        dx = dy = 0.0
        speed = 1.0
        water = 1.0
        sediment = 0.0
        for _step in range(max_steps):
            ix, iy = int(px), int(py)
            fx, fy = px - ix, py - iy
            i = iy * w + ix
            h00 = height[i]
            h10 = height[i + 1]
            h01 = height[i + w]
            h11 = height[i + w + 1]
            grad_x = (h10 - h00) * (1 - fy) + (h11 - h01) * fy
            grad_y = (h01 - h00) * (1 - fx) + (h11 - h10) * fx
            height_v = (h00 * (1 - fx) + h10 * fx) * (1 - fy) + (h01 * (1 - fx) + h11 * fx) * fy

            dx = dx * inertia - grad_x * (1.0 - inertia)
            dy = dy * inertia - grad_y * (1.0 - inertia)
            norm = math.hypot(dx, dy)
            if norm < 1e-9:
                break
            dx /= norm
            dy /= norm
            px += dx
            py += dy
            if px < 1 or py < 1 or px >= w - 2 or py >= h - 2:
                break

            new_h = (h00 * (1 - fx) + h10 * fx) * (1 - fy) + (h01 * (1 - fx) + h11 * fx) * fy
            dh = new_h - height_v

            capacity_v = max(-dh * speed * water * capacity, 0.0)
            if sediment > capacity_v or dh > 0:
                amount = (capacity_v - sediment) if dh > 0 else (sediment - capacity_v) * deposit_rate
                sediment -= amount
                si = int(py) * w + int(px)
                for wx, wy, wt in weights:
                    tx, ty = int(px) + wx, int(py) + wy
                    if 0 <= tx < w and 0 <= ty < h:
                        delta[ty * w + tx] += amount * wt / wsum
            else:
                amount = min((capacity_v - sediment) * erode_rate, -dh)
                if amount > 0:
                    si = int(py) * w + int(px)
                    if 0 <= si < len(delta):
                        delta[si] -= amount
                        height[si] -= amount
                    sediment += amount

            speed = math.sqrt(max(speed * speed + (-dh) * gravity, 0.0))
            water *= (1.0 - evaporate)
            if water < 0.01:
                break
    return np.asarray(delta, dtype=np.float64).reshape(h, w)


def droplet_erosion(dem: np.ndarray, *, droplets: int = 20000, seed: int = 1337, mask=None,
                    **kw) -> np.ndarray:
    """Public wrapper around the particle pass."""
    dem = np.asarray(dem, dtype=np.float64)
    delta = _droplet_pass(dem, droplets, seed=seed, **kw)
    if mask is not None:
        delta = delta * mask
    return dem + delta


def deposit_sediment(dem: np.ndarray, accum: np.ndarray, *, cell_size: float = 4.0,
                     threshold: float = 400.0, rate: float = 0.04, passes: int = 3,
                     max_fill: float = 3.0) -> np.ndarray:
    """Alluvial deposition: material dropped where a channel loses gradient.

    Uses a locally smoothed channel floor as the deposition target, which builds up
    valley floors and fans without needing a full sediment-transport solve.
    """
    from scipy import ndimage

    dem = np.asarray(dem, dtype=np.float64).copy()
    channel = accum > threshold
    if not np.any(channel):
        return dem
    for _ in range(max(1, int(passes))):
        smoothed = ndimage.uniform_filter(dem, size=3, mode="nearest")
        low = channel & (smoothed > dem)
        fill = np.where(low, np.minimum((smoothed - dem) * rate, max_fill), 0.0)
        dem += fill
    return dem


def diffusion_pass(dem: np.ndarray, *, strength: float = 1.0, radius: float = 1.5,
                   sea_level: float = 63.0, shore_extra: float = 1.0) -> np.ndarray:
    """Anisotropic smoothing pass tuned for coastlines.

    More diffusion near sea level (so beaches read as gradual) and less on steep
    mountain flanks (so peaks stay crisp) - this is the "terrain smoothing" that
    Lithosphere is known for compared to vanilla's blocky layers.
    """
    try:
        from scipy.ndimage import gaussian_filter
    except Exception:  # pragma: no cover
        from .noise import gaussian_blur as gaussian_filter

    dem = np.asarray(dem, dtype=np.float64)
    near_sea = np.exp(-np.abs(dem - sea_level) / 12.0)
    base = gaussian_filter(dem, radius, mode="wrap")
    detail = gaussian_filter(dem, max(radius * 0.35, 0.5), mode="wrap")
    blend = np.clip(strength * (0.35 + shore_extra * near_sea), 0.0, 0.9)
    return dem * (1.0 - blend) + (base * 0.6 + detail * 0.4) * blend


def smooth_channels(dem: np.ndarray, accum: np.ndarray, *, threshold, sigma: float = 1.1,
                    passes: int = 2, blend: float = 0.55) -> np.ndarray:
    """Round off the staircase corners a D8 flow router leaves behind.

    D8 (and any grid-aligned flow router) can only move in eight directions, so carved
    channels come out with 45 degree corners.  Real streams meander.  Blurring *only*
    the channel corridor along its own flow direction recovers smooth, sinuous valleys
    without touching the hillslopes.
    """
    from scipy.ndimage import gaussian_filter

    dem = np.asarray(dem, dtype=np.float64)
    core = accum >= (np.asarray(threshold) * 0.6 if np.ndim(threshold) else threshold * 0.6)
    if not np.any(core):
        return dem
    out = dem.copy()
    for _ in range(max(1, int(passes))):
        blurred = gaussian_filter(out, sigma, mode="nearest")
        # a soft corridor mask keeps the blur from leaking into the surrounding slopes
        corridor = gaussian_filter(core.astype(np.float64), sigma * 1.6, mode="nearest")
        corridor = np.clip(corridor * 1.6, 0.0, 1.0) * float(blend)
        out = out * (1.0 - corridor) + blurred * corridor
    return out


def hydraulic_erosion(
    dem: np.ndarray,
    *,
    cell_size: float = 4.0,
    sea_level: float = 63.0,
    thermal_iterations: int = 16,
    thermal_rate: float = 0.26,
    diffusion: float = 0.45,
    sp_iterations: int = 12,
    sp_k: float = 0.55,
    sp_m: float = 0.6,
    sp_n: float = 1.0,
    sp_reference_area: float = 260.0,
    max_incision: float = 1.2,
    droplets: int = 0,
    droplet_seed: int = 1337,
    deposit: float = 0.5,
    smooth_sigma: float = 1.1,
    progress=None,
) -> Tuple[np.ndarray, np.ndarray]:
    """Run the full erosion recipe.  Returns ``(dem, accumulation)``."""
    dem = np.asarray(dem, dtype=np.float64).copy()

    def tick(frac, label):
        if progress:
            progress(frac, label)

    if diffusion > 0:
        dem = diffusion_pass(dem, strength=float(diffusion), sea_level=sea_level)
        tick(0.15, "diffusion")
    if thermal_iterations > 0:
        dem = thermal_erosion(dem, iterations=thermal_iterations, rate=thermal_rate,
                              cell_size=cell_size)
        tick(0.35, "thermal erosion")
    dem, accum = stream_power_erosion(
        dem, cell_size=cell_size, iterations=sp_iterations, k=sp_k, m=sp_m, n=sp_n,
        uplift=0.0, reference_area=sp_reference_area, max_incision=max_incision,
    )
    tick(0.7, "fluvial incision")
    if droplets > 0:
        dem = droplet_erosion(dem, droplets=droplets, seed=droplet_seed)
        tick(0.85, "droplet erosion")
        filled, _ = priority_flood(dem, epsilon=1e-3)
        dirs = flow_directions(filled)
        accum = flow_accumulation(filled, dirs)
    if smooth_sigma > 0:
        dem = smooth_channels(dem, accum,
                              threshold=max(150.0, 1.2e4 / (cell_size * cell_size)),
                              sigma=float(smooth_sigma))
        tick(0.9, "channel smoothing")
    if deposit > 0:
        dem = deposit_sediment(dem, accum, cell_size=cell_size,
                               threshold=max(200.0, 2.0e4 / (cell_size * cell_size)),
                               rate=0.05 * deposit)
        tick(0.95, "deposition")
    return dem, accum


def erosion_stats(before: np.ndarray, after: np.ndarray, accum: np.ndarray) -> dict:
    d = after - before
    return {
        "eroded_volume": float(np.sum(np.minimum(d, 0.0))) * -1.0,
        "deposited_volume": float(np.sum(np.maximum(d, 0.0))),
        "max_incision": float(-np.min(d)) if d.size else 0.0,
        "mean_change": float(np.mean(d)) if d.size else 0.0,
        "drainage_max": float(np.max(accum)) if accum.size else 0.0,
    }
