"""Spline-guided mountain instance scattering (specification §2.1).

Pure gradient noise produces isotropic, amorphous mounds.  Real cordilleras are
*structural*: arêtes, knife-edge ridgelines and pyramidal peaks arranged along fault
lines.  This module builds them the way the blueprint describes:

1.  a parametric fault spline ``C(t)`` defines the continental spine;
2.  stations are sampled along it with Poisson-disk spacing plus jitter;
3.  each station stamps a primitive - an **arête** ridge or a **pyramidal peak**;

    ``h_ridge(u, v) = H * exp(-|v| / sigma) * (1 - (u / L)^2) * (1 + eta * noise)``

    ``h_peak(r, th) = H * (1 - r / R)^gamma * (1 + alpha * cos(k * th))``

4.  primitives are composited with **polynomial smooth-max**, so crests stay sharp but
    saddle passes (cols) stay smooth:  ``smax(a, b, k) = (a + b + sqrt((a-b)^2 + k)) / 2``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from .noise import fbm

Point = Tuple[float, float]


# --------------------------------------------------------------------------------------
# instance records
# --------------------------------------------------------------------------------------


@dataclass
class ScenarioInstance:
    """One scattered mountain primitive, kept so the preview/export can describe it."""

    kind: str            # "arete" | "peak"
    x: float
    z: float
    height: float
    angle: float = 0.0   # radians, ridge tangent (arêtes only)
    length: float = 0.0  # along-ridge full length
    sigma: float = 0.0   # cross-ridge decay width
    radius: float = 0.0  # footprint radius (peaks)
    arms: int = 4        # radiating arêtes (peaks)
    gamma: float = 1.8   # concave glacial flank exponent

    def to_dict(self) -> Dict[str, float]:
        return {
            "kind": self.kind, "x": round(self.x, 1), "z": round(self.z, 1),
            "height": round(self.height, 1), "angle": round(math.degrees(self.angle), 1),
            "length": round(self.length, 1), "sigma": round(self.sigma, 1),
            "radius": round(self.radius, 1), "arms": self.arms,
        }


# --------------------------------------------------------------------------------------
# spline maths
# --------------------------------------------------------------------------------------


def catmull_rom(waypoints: Sequence[Point], samples: int = 512,
                ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Interpolate ``waypoints`` with a centripetal Catmull-Rom / cubic Hermite spline.

    Returns ``(x, z, dx, dz)`` sampled uniformly in ``t``; the tangents are analytic, so
    the ridge instances can be oriented along the local fault direction.
    """
    pts = np.asarray(waypoints, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[1] != 2 or len(pts) < 2:
        raise ValueError("need at least two (x, z) waypoints")
    if len(pts) == 2:  # straight spine
        t = np.linspace(0.0, 1.0, samples)
        x = pts[0, 0] + (pts[1, 0] - pts[0, 0]) * t
        z = pts[0, 1] + (pts[1, 1] - pts[0, 1]) * t
        dx = np.full_like(t, pts[1, 0] - pts[0, 0])
        dz = np.full_like(t, pts[1, 1] - pts[0, 1])
        return x, z, dx, dz

    # pad the ends so the first/last segment keep a tangent
    p = np.vstack([pts[0] + (pts[0] - pts[1]), pts, pts[-1] + (pts[-1] - pts[-2])])
    n = len(pts) - 1
    t_samples = np.linspace(0.0, float(n), samples)
    seg = np.clip(np.floor(t_samples).astype(int), 0, n - 1)
    u = t_samples - seg
    p0, p1, p2, p3 = p[seg], p[seg + 1], p[seg + 2], p[seg + 3]

    # Catmull-Rom (centripetal-ish) to Hermite tangents
    m1 = (p2 - p0) * 0.5
    m2 = (p3 - p1) * 0.5
    u2, u3 = u * u, u * u * u
    h00 = 2 * u3 - 3 * u2 + 1
    h10 = u3 - 2 * u2 + u
    h01 = -2 * u3 + 3 * u2
    h11 = u3 - u2
    pos = (h00[:, None] * p1 + h10[:, None] * m1 + h01[:, None] * p2 + h11[:, None] * m2)
    # analytic derivative
    d00 = 6 * u2 - 6 * u
    d10 = 3 * u2 - 4 * u + 1
    d01 = -6 * u2 + 6 * u
    d11 = 3 * u2 - 2 * u
    der = (d00[:, None] * p1 + d10[:, None] * m1 + d01[:, None] * p2 + d11[:, None] * m2)
    return pos[:, 0], pos[:, 1], der[:, 0], der[:, 1]


def poisson_stations(x: np.ndarray, z: np.ndarray, dx: np.ndarray, dz: np.ndarray,
                     count: int, *, min_spacing: float, jitter: float,
                     rng: np.random.Generator) -> List[Tuple[float, float, float]]:
    """Pick ``count`` stations along a sampled spline with Poisson-disk spacing.

    Stations are drawn greedily from a shuffled candidate list; a candidate is kept if it
    is at least ``min_spacing`` away from every station already chosen.  When the spacing
    cannot be satisfied the spacing relaxes, so we always end up with ``count`` stations.
    """
    n = len(x)
    if n == 0 or count <= 0:
        return []
    order = rng.permutation(n)
    chosen: List[int] = []
    spacing = float(min_spacing)
    tries = 0
    while len(chosen) < count and tries < 40:
        for idx in order:
            if len(chosen) >= count:
                break
            px, pz = x[idx], z[idx]
            ok = True
            for j in chosen:
                if (px - x[j]) ** 2 + (pz - z[j]) ** 2 < spacing * spacing:
                    ok = False
                    break
            if ok:
                chosen.append(int(idx))
        if len(chosen) < count:
            spacing *= 0.7  # relax and try again
            tries += 1

    out: List[Tuple[float, float, float]] = []
    for idx in chosen:
        jx = (rng.random() * 2.0 - 1.0) * jitter
        jz = (rng.random() * 2.0 - 1.0) * jitter
        angle = math.atan2(float(dz[idx]), float(dx[idx]))
        out.append((float(x[idx]) + jx, float(z[idx]) + jz, angle))
    return out


# --------------------------------------------------------------------------------------
# primitive rendering
# --------------------------------------------------------------------------------------


def smooth_max(a: np.ndarray, b: np.ndarray, k: float = 12.0) -> np.ndarray:
    """Polynomial smooth-max: ``(a + b + sqrt((a - b)^2 + k)) / 2``.

    ``max`` leaves derivative discontinuities (razor creases); plain addition stacks
    instances into flat-topped plateaus.  ``smax`` gives sharp crests with smooth cols.
    """
    k = max(0.0, float(k))
    return 0.5 * (a + b + np.sqrt((a - b) ** 2 + k))


def _blend_instance(window: np.ndarray, inst: np.ndarray, support: np.ndarray,
                    blend_k: float) -> np.ndarray:
    """Composite an instance into the macro relief with a tapered smooth-max.

    ``smax(a, 0, k)`` is not zero - it is ``sqrt(k) / 2`` - so stamping the raw result
    over the whole bounding box leaves a visible raised rectangle around every primitive.
    Tapering the blend by the instance *support* keeps the operator's sharp-crest /
    smooth-col behaviour where the primitive exists and leaves the ground untouched
    outside it, with no step at the boundary.
    """
    sm = smooth_max(window, inst, blend_k)
    t = np.clip(support * 6.0, 0.0, 1.0)
    t = t * t * (3.0 - 2.0 * t)                   # smoothstep
    return window + (sm - window) * t


def render_arete(relief: np.ndarray, cx: float, cz: float, angle: float, height: float,
                 length: float, sigma: float, *, noise_amp: float = 0.18,
                 noise_scale: float = 40.0, cell: float = 1.0, blend_k: float = 12.0,
                 seed: int = 0) -> None:
    """Stamp one arête (ridge) instance into ``relief`` in place."""
    h, w = relief.shape
    reach = max(length * 0.5, sigma * 4.0)
    x_min = max(0, int((cx - reach) / cell))
    x_max = min(w, int((cx + reach) / cell) + 1)
    z_min = max(0, int((cz - reach) / cell))
    z_max = min(h, int((cz + reach) / cell) + 1)
    if x_min >= x_max or z_min >= z_max:
        return

    X, Z = np.meshgrid(np.arange(x_min, x_max) * cell, np.arange(z_min, z_max) * cell)
    dx, dz = X - cx, Z - cz
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    u = dx * cos_a + dz * sin_a          # along the ridge
    v = -dx * sin_a + dz * cos_a         # across the ridge

    along = np.clip(1.0 - (u / max(length * 0.5, 1e-6)) ** 2, 0.0, 1.0)
    cross = np.exp(-np.abs(v) / max(sigma, 1e-6))
    support = along * cross                       # 0 outside the primitive's footprint
    inst = height * support
    if noise_amp > 0:
        inst = inst * (1.0 + noise_amp * (fbm(X, Z, octaves=3, scale=noise_scale,
                                               seed=seed + 17) * 2.0 - 1.0))
    window = relief[z_min:z_max, x_min:x_max]
    relief[z_min:z_max, x_min:x_max] = _blend_instance(window, inst, support, blend_k)


def render_peak(relief: np.ndarray, cx: float, cz: float, height: float, radius: float,
                *, arms: int = 4, gamma: float = 1.8, arm_amp: float = 0.22,
                cell: float = 1.0, blend_k: float = 12.0,
                noise_amp: float = 0.12, noise_scale: float = 55.0, seed: int = 0) -> None:
    """Stamp a pyramidal peak with ``arms`` radiating arêtes (Matterhorn / cirque)."""
    h, w = relief.shape
    x_min = max(0, int((cx - radius) / cell))
    x_max = min(w, int((cx + radius) / cell) + 1)
    z_min = max(0, int((cz - radius) / cell))
    z_max = min(h, int((cz + radius) / cell) + 1)
    if x_min >= x_max or z_min >= z_max:
        return

    X, Z = np.meshgrid(np.arange(x_min, x_max) * cell, np.arange(z_min, z_max) * cell)
    dx, dz = X - cx, Z - cz
    r = np.hypot(dx, dz)
    theta = np.arctan2(dz, dx)
    radial = np.clip(1.0 - r / max(radius, 1e-6), 0.0, 1.0) ** gamma
    support = radial
    inst = height * radial * (1.0 + arm_amp * np.cos(max(1, int(arms)) * theta))
    if noise_amp > 0:
        inst = inst * (1.0 + noise_amp * (fbm(X, Z, octaves=2, scale=noise_scale,
                                              seed=seed + 29) * 2.0 - 1.0))
    window = relief[z_min:z_max, x_min:x_max]
    relief[z_min:z_max, x_min:x_max] = _blend_instance(window, inst, support, blend_k)


# --------------------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------------------


def default_spine(region) -> List[Point]:
    """A gentle arc across the region - used when no waypoints are configured."""
    x0, z0 = float(region.x0), float(region.z0)
    bx, bz = float(region.blocks_x), float(region.blocks_z)
    return [
        (x0 + 0.08 * bx, z0 + 0.22 * bz),
        (x0 + 0.30 * bx, z0 + 0.40 * bz),
        (x0 + 0.52 * bx, z0 + 0.52 * bz),
        (x0 + 0.76 * bx, z0 + 0.70 * bz),
        (x0 + 0.94 * bx, z0 + 0.86 * bz),
    ]


def scatter_ridges(
    shape: Tuple[int, int],
    region,
    *,
    waypoints: Optional[Iterable[Point]] = None,
    peak_count: int = 40,
    base_altitude: float = 240.0,
    height_jitter: float = 0.25,
    ridge_sigma: float = 18.0,
    length_range: Tuple[float, float] = (120.0, 260.0),
    peak_ratio: float = 0.35,
    blend_k: float = 12.0,
    noise_amp: float = 0.18,
    seed: int = 0,
) -> Tuple[np.ndarray, List[ScenarioInstance]]:
    """Build a relief field of scattered ridge/peak instances along a fault spline.

    ``shape`` is the simulation grid shape (cells).  The returned relief is in blocks and
    is meant to be composited into the macro terrain with :func:`smooth_max`.
    """
    h, w = shape
    cell = float(region.cell_size)
    relief = np.zeros((h, w), dtype=np.float64)
    if peak_count <= 0:
        return relief, []

    pts = list(waypoints) if waypoints else default_spine(region)
    rng = np.random.default_rng(int(seed) & 0xFFFFFFFF)
    radius = max(region.blocks_x, region.blocks_z) * 0.5
    xs, zs, dxs, dzs = catmull_rom(pts, samples=max(256, peak_count * 16))

    # stations spaced about half a ridge length apart along the spine
    min_spacing = max(40.0, float(np.mean(length_range)) * 0.45)
    stations = poisson_stations(xs, zs, dxs, dzs, int(peak_count),
                                min_spacing=min_spacing, jitter=min_spacing * 0.35,
                                rng=rng)

    instances: List[ScenarioInstance] = []
    for i, (sx, sz, angle) in enumerate(stations):
        # keep ranges mostly on land: sample the spine but fade where it is too low
        jitter_h = 1.0 + (rng.random() * 2.0 - 1.0) * height_jitter
        if rng.random() < peak_ratio:
            height = base_altitude * jitter_h * 1.15
            pr = radius * float(rng.uniform(0.012, 0.022)) + 60.0
            arms = int(rng.integers(3, 5))
            render_peak(relief, sx, sz, height, pr, arms=arms, cell=cell,
                        blend_k=blend_k, noise_amp=noise_amp, seed=seed + i)
            instances.append(ScenarioInstance("peak", sx, sz, height, angle=angle,
                                              radius=pr, arms=arms))
        else:
            height = base_altitude * jitter_h * 0.85
            length = float(rng.uniform(*length_range))
            sigma = ridge_sigma * float(rng.uniform(0.7, 1.35))
            render_arete(relief, sx, sz, angle, height, length, sigma, cell=cell,
                         blend_k=blend_k, noise_amp=noise_amp, seed=seed + i)
            instances.append(ScenarioInstance("arete", sx, sz, height, angle=angle,
                                              length=length, sigma=sigma))
    return relief, instances


def instance_summary(instances: Sequence[ScenarioInstance]) -> Dict[str, float]:
    """Small diagnostics blob for the job result / preview panel."""
    if not instances:
        return {"count": 0, "aretes": 0, "peaks": 0, "max_height": 0.0}
    return {
        "count": len(instances),
        "aretes": sum(1 for i in instances if i.kind == "arete"),
        "peaks": sum(1 for i in instances if i.kind == "peak"),
        "max_height": round(max(i.height for i in instances), 1),
        "spine": [i.to_dict() for i in instances[:64]],
    }
