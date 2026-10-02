"""Vectorised coherent-noise toolkit.

Everything here works on arbitrary-shaped ``numpy`` coordinate arrays, which lets
the generators evaluate whole grids at once instead of looping per cell.

Implemented primitives
----------------------
* ``hash2`` / ``hash3``      - deterministic integer hashes (no RNG state)
* ``value_noise``            - smooth value noise
* ``gradient_noise``         - Perlin-style gradient noise (default)
* ``fbm``                    - fractal Brownian motion, optionally ridged/billowed
* ``cellular``               - Worley / F1-F2 cellular noise (river + POI masks)
* ``domain_warp``            - Quilez-style domain warping
* ``warped_fbm``             - fBm evaluated through a warp field (World-Machine look)

All coordinates are in *noise space*: one unit == one lattice cell.  Callers
usually divide world coordinates by a feature size before calling.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

__all__ = [
    "hash2",
    "hash3",
    "value_noise",
    "gradient_noise",
    "fbm",
    "cellular",
    "domain_warp",
    "warped_fbm",
    "ridged_fbm",
    "billow_fbm",
    "FractalSpec",
    "NoiseField",
    "GRAD8",
]

_U32 = 0xFFFFFFFF

# 8 unit-ish gradients arranged on a circle: cheap and isotropic enough.
GRAD8 = np.array(
    [
        [1.0, 0.0],
        [-1.0, 0.0],
        [0.0, 1.0],
        [0.0, -1.0],
        [0.70710678, 0.70710678],
        [-0.70710678, 0.70710678],
        [0.70710678, -0.70710678],
        [-0.70710678, -0.70710678],
    ],
    dtype=np.float64,
)


def _as_float(a) -> np.ndarray:
    return np.asarray(a, dtype=np.float64)


def hash2(ix, iy, seed: int = 0) -> np.ndarray:
    """Integer hash of a 2D lattice point -> float in [0, 1).

    Uses unsigned 32-bit wraparound arithmetic (numpy wraps silently for unsigned
    types), which keeps this allocation-light and warning-free on big grids.
    """
    x = np.asarray(ix, dtype=np.uint64).astype(np.uint32)
    y = np.asarray(iy, dtype=np.uint64).astype(np.uint32)
    s = np.uint32(int(seed) & 0xFFFFFFFF)
    h = (np.multiply(x, np.uint32(0x27D4EB2D), dtype=np.uint32))
    h = (h ^ (np.multiply(y, np.uint32(0x165667B1), dtype=np.uint32))).astype(np.uint32)
    h = (h ^ (h >> np.uint32(15))).astype(np.uint32)
    h = (np.multiply(h, np.uint32(0x2545F491), dtype=np.uint32))
    h = (h ^ (h >> np.uint32(13))).astype(np.uint32)
    h = (h ^ s).astype(np.uint32)
    h = (np.multiply(h, np.uint32(0x9E3779B1), dtype=np.uint32))
    h = (h ^ (h >> np.uint32(16))).astype(np.uint32)
    return h.astype(np.float64) * (1.0 / 4294967296.0)


def hash3(ix, iy, iz, seed: int = 0) -> np.ndarray:
    """Integer hash of a 3D lattice point -> float in [0, 1)."""
    x = np.asarray(ix, dtype=np.uint64).astype(np.uint32)
    y = np.asarray(iy, dtype=np.uint64).astype(np.uint32)
    z = np.asarray(iz, dtype=np.uint64).astype(np.uint32)
    s = np.uint32(int(seed) & 0xFFFFFFFF)
    h = (np.multiply(x, np.uint32(0x27D4EB2D), dtype=np.uint32))
    h = (h ^ (np.multiply(y, np.uint32(0x165667B1), dtype=np.uint32))).astype(np.uint32)
    h = (h ^ (np.multiply(z, np.uint32(0x1B873593), dtype=np.uint32))).astype(np.uint32)
    h = (h ^ (h >> np.uint32(15))).astype(np.uint32)
    h = (np.multiply(h, np.uint32(0x2545F491), dtype=np.uint32))
    h = (h ^ s).astype(np.uint32)
    h = (np.multiply(h, np.uint32(0x9E3779B1), dtype=np.uint32))
    h = (h ^ (h >> np.uint32(16))).astype(np.uint32)
    return h.astype(np.float64) * (1.0 / 4294967296.0)


def _quintic(t: np.ndarray) -> np.ndarray:
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


def value_noise(x, y, seed: int = 0) -> np.ndarray:
    """Smooth value noise in ``[-1, 1]``."""
    x = _as_float(x)
    y = _as_float(y)
    x0 = np.floor(x)
    y0 = np.floor(y)
    fx = _quintic(x - x0)
    fy = _quintic(y - y0)
    ix = x0.astype(np.int64)
    iy = y0.astype(np.int64)
    n00 = hash2(ix, iy, seed)
    n10 = hash2(ix + 1, iy, seed)
    n01 = hash2(ix, iy + 1, seed)
    n11 = hash2(ix + 1, iy + 1, seed)
    nx0 = n00 + (n10 - n00) * fx
    nx1 = n01 + (n11 - n01) * fx
    out = nx0 + (nx1 - nx0) * fy
    return out * 2.0 - 1.0


def gradient_noise(x, y, seed: int = 0) -> np.ndarray:
    """Perlin-style gradient noise in roughly ``[-1, 1]``."""
    x = _as_float(x)
    y = _as_float(y)
    x0 = np.floor(x)
    y0 = np.floor(y)
    dx = x - x0
    dy = y - y0
    ix = x0.astype(np.int64)
    iy = y0.astype(np.int64)

    def grad(ox, oy, ux, uy):
        g = hash2(ix + ox, iy + oy, seed)
        idx = np.minimum((g * 8.0).astype(np.int64), 7)
        gv = GRAD8[idx]
        return gv[..., 0] * ux + gv[..., 1] * uy

    n00 = grad(0, 0, dx, dy)
    n10 = grad(1, 0, dx - 1.0, dy)
    n01 = grad(0, 1, dx, dy - 1.0)
    n11 = grad(1, 1, dx - 1.0, dy - 1.0)
    ux = _quintic(dx)
    uy = _quintic(dy)
    nx0 = n00 + (n10 - n00) * ux
    nx1 = n01 + (n11 - n01) * ux
    return (nx0 + (nx1 - nx0) * uy) * 1.4


_NOISE_FUNCS = {"perlin": gradient_noise, "value": value_noise}


@dataclass
class FractalSpec:
    """Octave stacking parameters for a single fractal noise field."""

    octaves: int = 6
    lacunarity: float = 2.0
    gain: float = 0.5
    base_scale: float = 256.0
    kind: str = "perlin"
    ridged: bool = False
    billow: bool = False
    seed_offset: int = 0
    normalize: bool = True


def fbm(
    x,
    y,
    *,
    octaves: int = 6,
    lacunarity: float = 2.0,
    gain: float = 0.5,
    scale: float = 256.0,
    seed: int = 0,
    kind: str = "perlin",
    ridged: bool = False,
    billow: bool = False,
    normalize: bool = True,
) -> np.ndarray:
    """Fractal Brownian motion.  Returns roughly ``[-1, 1]`` (``[0, 1]`` when ridged)."""
    fn = _NOISE_FUNCS.get(kind, gradient_noise)
    x = _as_float(x) / float(scale)
    y = _as_float(y) / float(scale)
    amp = 1.0
    freq = 1.0
    total = 0.0
    norm = 0.0
    prev = None
    for o in range(int(octaves)):
        n = fn(x * freq, y * freq, seed + o * 1013)
        if ridged:
            n = 1.0 - np.abs(n)
            n = n * n
        elif billow:
            n = np.abs(n)
        if prev is not None and gain != 0.5:
            # keep some spectral continuity between octaves
            n = 0.5 * (n + prev * 0.0) + 0.5 * n
        total = total + n * amp
        norm += amp
        amp *= gain
        freq *= lacunarity
        prev = n
    if normalize and norm > 0:
        total = total / norm
    return total


def ridged_fbm(x, y, **kw) -> np.ndarray:
    kw["ridged"] = True
    return fbm(x, y, **kw)


def billow_fbm(x, y, **kw) -> np.ndarray:
    kw["billow"] = True
    return fbm(x, y, **kw)


def cellular(
    x,
    y,
    *,
    scale: float = 256.0,
    seed: int = 0,
    jitter: float = 1.0,
    mode: str = "f1",
) -> np.ndarray:
    """Worley noise.  ``mode`` is one of ``f1``, ``f2``, ``f2-f1``, ``id``.

    ``f2-f1`` gives the classic river/crack network mask, ``id`` gives a
    per-cell identifier in ``[0, 1)`` useful for scattering points / POIs.
    """
    x = _as_float(x) / float(scale)
    y = _as_float(y) / float(scale)
    ix = np.floor(x)
    iy = np.floor(y)
    best1 = np.full(x.shape, 1e9)
    best2 = np.full(x.shape, 1e9)
    best_id = np.zeros(x.shape)
    for oy in (-1, 0, 1):
        for ox in (-1, 0, 1):
            cx = ix + ox
            cy = iy + oy
            jx = hash2(cx, cy, seed)
            jy = hash2(cx, cy, seed + 7919)
            fid = hash2(cx, cy, seed + 104729)
            px = cx + 0.5 + (jx - 0.5) * jitter
            py = cy + 0.5 + (jy - 0.5) * jitter
            d = np.hypot(px - x, py - y)
            closer = d < best1
            best2 = np.where(closer, best1, np.minimum(best2, d))
            best_id = np.where(closer, fid, best_id)
            best1 = np.where(closer, d, best1)
    if mode == "f1":
        return best1
    if mode == "f2":
        return best2
    if mode == "f2-f1":
        return best2 - best1
    if mode == "id":
        return best_id
    raise ValueError(f"unknown cellular mode {mode!r}")


def domain_warp(x, y, *, scale: float, strength: float, seed: int = 0, octaves: int = 3):
    """Return warped ``(x, y)`` coordinates (Quilez fBm warping)."""
    wx = fbm(x, y, octaves=octaves, scale=scale, seed=seed + 51)
    wy = fbm(x, y, octaves=octaves, scale=scale, seed=seed + 923)
    return x + wx * strength, y + wy * strength


def warped_fbm(x, y, *, scale: float, warp_scale: float, warp_strength: float,
               seed: int = 0, octaves: int = 6, ridged: bool = False,
               lacunarity: float = 2.0, gain: float = 0.5) -> np.ndarray:
    """fBm through a domain warp - the 'World Machine' style flowy terrain."""
    wx, wy = domain_warp(x, y, scale=warp_scale, strength=warp_strength, seed=seed)
    return fbm(
        wx,
        wy,
        octaves=octaves,
        scale=scale,
        seed=seed,
        ridged=ridged,
        lacunarity=lacunarity,
        gain=gain,
    )


@dataclass
class NoiseField:
    """Convenience wrapper: a named fractal field bound to a seed + world extent."""

    spec: FractalSpec = field(default_factory=FractalSpec)
    seed: int = 0
    x_offset: float = 0.0
    y_offset: float = 0.0

    def sample(self, x, y) -> np.ndarray:
        return fbm(
            _as_float(x) + self.x_offset,
            _as_float(y) + self.y_offset,
            octaves=self.spec.octaves,
            lacunarity=self.spec.lacunarity,
            gain=self.spec.gain,
            scale=self.spec.base_scale,
            seed=self.seed + self.spec.seed_offset,
            kind=self.spec.kind,
            ridged=self.spec.ridged,
            billow=self.spec.billow,
            normalize=self.spec.normalize,
        )


def grid_coords(width: int, height: int, cell_size: float = 1.0, origin=(0.0, 0.0)):
    """Return ``(X, Y)`` world-coordinate grids for a ``height x width`` raster."""
    ys = (np.arange(height, dtype=np.float64) + 0.5) * cell_size + origin[1]
    xs = (np.arange(width, dtype=np.float64) + 0.5) * cell_size + origin[0]
    return np.meshgrid(xs, ys)


def smoothstep(a, b, t):
    t = np.clip((_as_float(t) - a) / (b - a + 1e-12), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def ease_in_out(t):
    t = np.clip(_as_float(t), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def lerp(a, b, t):
    return a + (b - a) * np.clip(t, 0.0, 1.0)


def spline(points: Sequence[Sequence[float]], t) -> np.ndarray:
    """Monotone-ish piecewise cubic-ish spline through ``points`` (x, y).

    Uses Catmull-Rom tangents so curves stay smooth like World Machine splines.
    """
    pts = np.asarray(points, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[1] != 2:
        raise ValueError("spline points must be a sequence of (x, y)")
    t = _as_float(t)
    order = np.argsort(pts[:, 0])
    xs = pts[order, 0]
    ys = pts[order, 1]
    if len(xs) < 2:
        return np.full_like(t, float(ys[0]) if len(ys) else 0.0)

    idx = np.clip(np.searchsorted(xs, t, side="right") - 1, 0, len(xs) - 2)
    x0 = xs[idx]
    x1 = xs[idx + 1]
    y0 = ys[idx]
    y1 = ys[idx + 1]
    h = np.maximum(x1 - x0, 1e-9)
    u = np.clip((t - x0) / h, 0.0, 1.0)

    # Catmull-Rom tangents (one sided at the ends), per lattice segment
    span = np.maximum(np.diff(xs), 1e-9)
    m = np.zeros_like(ys)
    m[1:-1] = (ys[2:] - ys[:-2]) / (xs[2:] - xs[:-2] + 1e-12)
    m[0] = (ys[1] - ys[0]) / span[0]
    m[-1] = (ys[-1] - ys[-2]) / span[-1]
    m0 = m[idx] * h
    m1 = m[idx + 1] * h

    u2 = u * u
    u3 = u2 * u
    out = (
        (2 * u3 - 3 * u2 + 1) * y0
        + (u3 - 2 * u2 + u) * m0
        + (-2 * u3 + 3 * u2) * y1
        + (u3 - u2) * m1
    )
    # clamp flat outside the control points
    out = np.where(t <= xs[0], ys[0], out)
    out = np.where(t >= xs[-1], ys[-1], out)
    return out


def fbm_2d_grid(width: int, height: int, **kw) -> np.ndarray:
    """Convenience: evaluate an fBm field over a raster of the given size."""
    X, Y = grid_coords(width, height)
    return fbm(X, Y, **kw)


def normalize01(a: np.ndarray, lo=None, hi=None, eps: float = 1e-9) -> np.ndarray:
    a = _as_float(a)
    lo = float(np.min(a)) if lo is None else lo
    hi = float(np.max(a)) if hi is None else hi
    return (a - lo) / max(hi - lo, eps)


def stretch01(a: np.ndarray, lo_pct: float = 2.0, hi_pct: float = 98.0,
              eps: float = 1e-9) -> np.ndarray:
    """Percentile stretch to ``[0, 1]``.

    fBm output is roughly Gaussian, so a min/max normalisation would leave every field
    squeezed around the middle.  Stretching between the 2nd and 98th percentiles gives
    full contrast without a single outlier flattening the map.
    """
    a = _as_float(a)
    lo = float(np.percentile(a, lo_pct))
    hi = float(np.percentile(a, hi_pct))
    return np.clip((a - lo) / max(hi - lo, eps), 0.0, 1.0)


def gaussian_blur(a: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian blur with wrap-around edges (no scipy dependency required)."""
    if sigma <= 0:
        return np.asarray(a, dtype=np.float64)
    try:  # scipy is faster and better behaved when available
        from scipy.ndimage import gaussian_filter

        return gaussian_filter(np.asarray(a, dtype=np.float64), sigma, mode="wrap")
    except Exception:
        pass
    radius = max(1, int(math.ceil(sigma * 3)))
    xs = np.arange(-radius, radius + 1, dtype=np.float64)
    k = np.exp(-0.5 * (xs / sigma) ** 2)
    k /= k.sum()
    out = np.asarray(a, dtype=np.float64)
    out = np.apply_along_axis(lambda m: np.convolve(np.r_[m[-radius:], m, m[:radius]], k, mode="same")[radius:-radius], 0, out)
    out = np.apply_along_axis(lambda m: np.convolve(np.r_[m[-radius:], m, m[:radius]], k, mode="same")[radius:-radius], 1, out)
    return out
