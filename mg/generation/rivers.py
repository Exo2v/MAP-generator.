"""River logic - designed for this engine rather than borrowed.

The rest of the hydrology stack answers "where does water go" (flow routing) and "how
much is there" (accumulation).  This module answers the question those leave open: **what
shape should the river itself be, from headwater to sea?**

That is a different problem, and it is solved here with a purpose-built algorithm built
from three geomorphic ideas:

1.  **Graded longitudinal profile.**  A river is not a passive tracer of the terrain it
    crosses - it grades its own bed.  Given the total fall between source and mouth, the
    equilibrium distribution of that fall is the concave-up Flint profile
    ``S(s) ~ Q(s) ** -theta``: steep torrents at the head, nearly flat water at the mouth.
    The bed is relaxed toward that profile, which removes the noisy stair-steps a
    depositional terrain leaves in a channel and produces the long smooth valley floors
    that read as real rivers in game.

2.  **Lateral migration and cutoffs.**  Lowland rivers do not sit still: they erode their
    outer bank, which grows a bend, which sharpens its own curvature, until the neck of
    the loop is breached and an oxbow is abandoned.  The model is applied directly -
    resample the reach finely, seed a bend train at the physically correct wavelength
    (~10-14 channel widths, Leopold & Wolman), let curvature drive lateral displacement
    weighted by bankfull width and discharge, and cut off any loop that touches itself.

3.  **Hydraulic geometry and deltas.**  Width, depth and bank shape follow discharge
    through the network's hierarchy; where a large river meets the sea its mouth splits
    into distributaries and spreads into a delta.

Everything is pure numpy on ordered polylines, so the whole thing costs a fraction of a
second on a large region.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from ..core.types import Lake, RiverPath

# --------------------------------------------------------------------------------------
# tuning
# --------------------------------------------------------------------------------------

DEFAULT_RIVER_LOGIC: Dict[str, float] = {
    "enabled": 1.0,
    "theta": 0.45,            # Flint's law concavity exponent (0.4 - 0.6 is the real range)
    "grade_strength": 0.85,   # how hard the bed is pulled onto the graded profile
    "grade_min_q": 90.0,      # discharge below which a stream keeps its natural bed
    "knickpoint_ratio": 2.2,  # slope multiple over equilibrium that counts as a knickpoint
    "knickpoint_min_drop": 3.0,
    "knickpoint_migrate": 0.35,
    "meander_min_q": 320.0,
    "meander_max_slope": 0.05,
    "meander_erosion": 0.45,     # lateral displacement per pass at a developed bend,
                                 # as a fraction of bankfull width
    "meander_smoothing": 0.10,
    "meander_passes": 6,
    "belt_scale": 2.6,           # max lateral excursion as a multiple of channel width
    "cutoff_width_factor": 1.6,  # self-contact distance that triggers a cutoff
    "turn_reference": 0.35,      # turn angle (radians) counting as a developed bend
    "delta_min_q": 800.0,
    "delta_branches": 3,
    "delta_reach": 90.0,
}


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


def _smooth_polyline(a: np.ndarray, *, passes: int = 1, weight: float = 0.5) -> np.ndarray:
    """Laplacian smoothing that keeps the endpoints (a river's source and mouth are fixed)."""
    out = np.asarray(a, dtype=np.float64).copy()
    for _ in range(max(0, int(passes))):
        if out.shape[0] < 3:
            return out
        mid = 0.5 * (out[:-2] + out[2:])
        out[1:-1] = out[1:-1] * (1.0 - weight) + mid * weight
    return out


def _curvature(x: np.ndarray, z: np.ndarray) -> np.ndarray:
    """Signed curvature of a polyline, in 1/blocks (positive = left turn)."""
    x = np.asarray(x, dtype=np.float64)
    z = np.asarray(z, dtype=np.float64)
    if x.size < 3:
        return np.zeros_like(x)
    dx = np.gradient(x)
    dz = np.gradient(z)
    ddx = np.gradient(dx)
    ddz = np.gradient(dz)
    denom = (dx * dx + dz * dz) ** 1.5
    denom = np.where(denom < 1e-9, 1e-9, denom)
    return (dx * ddz - dz * ddx) / denom


def _resample(pts: np.ndarray, spacing: float) -> np.ndarray:
    """Resample a polyline at (roughly) constant arclength spacing."""
    pts = np.asarray(pts, dtype=np.float64)
    if pts.shape[0] < 2:
        return pts.copy()
    seg = np.hypot(*np.diff(pts[:, :2], axis=0).T)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    total = float(s[-1])
    if total <= 1e-6:
        return pts.copy()
    n = max(4, int(total / max(spacing, 1e-6)) + 1)
    target = np.linspace(0.0, total, n)
    out = np.empty((n, pts.shape[1]), dtype=np.float64)
    for c in range(pts.shape[1]):
        out[:, c] = np.interp(target, s, pts[:, c])
    return out


def _resample_attr(a: np.ndarray, n: int) -> np.ndarray:
    """Resample a per-point attribute array to ``n`` samples."""
    a = np.asarray(a, dtype=np.float64)
    if a.size == n:
        return a.copy()
    if a.size < 2:
        return np.full(n, float(a[0]) if a.size else 0.0)
    return np.interp(np.linspace(0.0, 1.0, n), np.linspace(0.0, 1.0, a.size), a)


# --------------------------------------------------------------------------------------
# 1. longitudinal profile
# --------------------------------------------------------------------------------------


def plan_longitudinal_profile(
    z: np.ndarray,
    discharge: np.ndarray,
    s: np.ndarray,
    *,
    theta: float = 0.45,
    strength: float = 0.85,
    min_discharge: float = 90.0,
) -> Tuple[np.ndarray, np.ndarray]:
    """Relax a channel bed toward its graded (concave-up) profile.

    ``z`` and ``discharge`` are ordered source -> mouth, ``s`` is distance along the
    channel.  The profile keeps the *total* fall of the reach - a river cannot dig itself
    below its base level, and the mouth elevation is set by the sea or lake it enters -
    and redistributes that fall as ``S ~ Q ** -theta``.

    Returns ``(graded_z, target_slope)``.
    """
    z = np.asarray(z, dtype=np.float64)
    q = np.maximum(np.asarray(discharge, dtype=np.float64), 1.0)
    s = np.asarray(s, dtype=np.float64)
    n = z.size
    if n < 3:
        return z.copy(), np.zeros_like(z)

    fall = z[0] - z[-1]
    if fall <= 1e-6:
        return z.copy(), np.zeros_like(z)

    weights = np.power(q, -float(theta))
    ds = np.maximum(np.diff(s), 1e-6)
    seg_weight = 0.5 * (weights[:-1] + weights[1:])
    denom = float(np.sum(seg_weight * ds))
    if denom <= 0:
        return z.copy(), np.zeros_like(z)
    slopes = fall * seg_weight / denom

    graded = np.empty_like(z)
    graded[-1] = z[-1]
    for i in range(n - 2, -1, -1):     # walk upstream from the mouth
        graded[i] = graded[i + 1] + slopes[i] * ds[i]

    target_slope = np.zeros_like(z)
    target_slope[:-1] = slopes
    target_slope[-1] = slopes[-1] if slopes.size else 0.0

    # Only large rivers grade their bed; headwater torrents keep the terrain's own steps,
    # which is exactly where waterfalls live.
    w = np.clip((q - float(min_discharge)) / max(float(min_discharge), 1.0), 0.0, 1.0)
    w = w * float(strength)
    return z * (1.0 - w) + graded * w, target_slope


def find_knickpoints(z: np.ndarray, equilibrium_slope: np.ndarray, s: np.ndarray, *,
                     ratio: float = 2.2, min_drop: float = 3.0) -> np.ndarray:
    """Indices where the bed is far steeper than its graded slope - rapids / waterfalls."""
    z = np.asarray(z, dtype=np.float64)
    s = np.asarray(s, dtype=np.float64)
    if z.size < 3:
        return np.zeros(0, dtype=np.int64)
    ds = np.maximum(np.diff(s), 1e-6)
    slope = np.abs(np.diff(z)) / ds
    eq = np.maximum(np.asarray(equilibrium_slope, dtype=np.float64)[:-1], 1e-4)
    drop = np.abs(np.diff(z))
    hot = (slope > ratio * eq) & (drop > float(min_drop))
    return np.flatnonzero(hot)


def migrate_knickpoints(z: np.ndarray, q: np.ndarray, s: np.ndarray,
                        indices: np.ndarray, *, amount: float = 0.35) -> np.ndarray:
    """Move steps headward and spread each drop so the long profile stays continuous.

    A knickpoint is a wave in the long profile: it travels upstream at a rate that grows
    with drainage area.  Migrating them keeps waterfalls at the head of a system instead
    of scattered mid-valley, and softening each drop stops the 1-cell staircases that read
    as terrain bugs in game.
    """
    z = np.asarray(z, dtype=np.float64).copy()
    if indices.size == 0 or z.size < 4:
        return z
    q = np.maximum(np.asarray(q, dtype=np.float64), 1.0)
    shift = int(max(1, round(float(amount) * 3.0)))
    for idx in np.atleast_1d(indices):
        idx = int(idx)
        if idx + 1 >= z.size:
            continue
        drop = z[idx] - z[idx + 1]
        if drop <= 0:
            continue
        target = max(0, idx - shift)
        # the reach between the new and old position is re-graded, and half the drop is
        # handed to the headward end so the step is continuous on both sides
        z[target:idx + 2] = np.linspace(z[target], z[idx + 1], idx + 2 - target)
        z[target] += drop * 0.5
    return z


# --------------------------------------------------------------------------------------
# 2. lateral migration
# --------------------------------------------------------------------------------------


def _find_loop(pts: np.ndarray, width: np.ndarray, *, gap: int,
               factor: float) -> Optional[Tuple[int, int]]:
    """First non-adjacent self-contact along the polyline, as an ``(i, j)`` index pair."""
    n = pts.shape[0]
    if n < gap * 3:
        return None
    stride = 2
    for i in range(0, n - gap * 3, stride):
        j0 = i + gap * 3
        dx = pts[j0:, 0] - pts[i, 0]
        dz = pts[j0:, 1] - pts[i, 1]
        lim = np.maximum(width[i], width[j0:]) * float(factor)
        hits = np.flatnonzero(dx * dx + dz * dz < lim * lim)
        if hits.size:
            return i, int(j0 + hits[0])
    return None


def migrate_meanders(
    points: np.ndarray,
    *,
    width: np.ndarray,
    discharge: np.ndarray,
    slope: np.ndarray,
    passes: int = 6,
    erosion: float = 0.45,
    smoothing: float = 0.10,
    belt_scale: float = 2.6,
    cutoff_width_factor: float = 1.6,
    turn_reference: float = 0.35,
    seed: int = 0,
) -> Tuple[np.ndarray, List[np.ndarray]]:
    """Grow meander bends by outer-bank erosion; return the abandoned oxbow loops.

    Application of the classical bend-migration model:

    * the reach is resampled so a bend wavelength is actually resolved;
    * a bend train is seeded at ~11 channel widths - the wavelength real meanders grow at
      (Leopold & Wolman) - because the erosion feedback is proportional to curvature,
      which falls off with the square of the wavelength while per-pass smoothing damps
      every scale equally, so a perturbation at the wrong scale simply cannot grow;
    * lateral displacement per pass is ``E * W * (turn / turn_ref)``, where ``turn`` is
      the angle the centreline turns per point (curvature x spacing).  Expressing the
      driver as an angle rather than a raw curvature is what keeps the coefficient
      meaningful across cell sizes and river sizes;
    * easing by slope and discharge confines meanders to slow, large lowland reaches;
    * whenever the migrated line comes within ``cutoff_width_factor * W`` of a
      non-adjacent part of itself, the neck is breached: the loop is removed from the
      channel and returned as an oxbow lake.

    Returns ``(new_points, oxbows)`` where ``new_points`` is ``(n, k)`` matching the input
    layout (x, z [, y]).
    """
    pts = np.asarray(points, dtype=np.float64).copy()
    if pts.shape[0] < 6 or float(erosion) <= 0:
        return pts, []

    width = np.maximum(np.asarray(width, dtype=np.float64), 1.0)
    q = np.maximum(np.asarray(discharge, dtype=np.float64), 1.0)
    slope = np.asarray(slope, dtype=np.float64)
    belt = np.clip(float(belt_scale) * width, 1.0, None)
    q_ref = float(np.median(q)) if q.size else 1.0
    rng = np.random.default_rng(int(seed) & 0xFFFFFFFF)

    # --- resample to resolve the bend wavelength ---------------------------------------
    seg0 = np.hypot(*np.diff(pts[:, :2], axis=0).T)
    span = float(seg0.sum())
    if span > 1e-6:
        want = int(np.clip(span / max(float(np.median(width)) / 2.0, 4.0), 16, 6000))
        if want > pts.shape[0]:
            pts = _resample(pts, span / want)
            width = _resample_attr(width, pts.shape[0])
            q = _resample_attr(q, pts.shape[0])
            slope = _resample_attr(slope, pts.shape[0])
            belt = np.clip(float(belt_scale) * width, 1.0, None)
            q_ref = float(np.median(q)) if q.size else 1.0

    # --- seed the bend train ------------------------------------------------------------
    dx0 = np.gradient(pts[:, 0])
    dz0 = np.gradient(pts[:, 1])
    m0 = np.hypot(dx0, dz0)
    m0 = np.where(m0 < 1e-9, 1.0, m0)
    seg = np.hypot(*np.diff(pts[:, :2], axis=0).T)
    u_s = np.concatenate([[0.0], np.cumsum(seg)])
    wav = max(11.0 * float(np.median(width)), 24.0)
    phase = 2.0 * np.pi * u_s / wav + rng.random() * 6.283
    seed_amp = float(np.clip(float(np.median(belt)) * 0.12, 0.5, 12.0))
    wobble = (np.sin(phase) * 0.72
              + np.sin(phase * 2.3 + rng.random() * 6.283) * 0.28
              + rng.normal(0.0, 0.12, pts.shape[0]))
    lateral = _smooth_polyline((wobble * seed_amp)[:, None], passes=2, weight=0.4)[:, 0]
    pts[:, 0] += (-dz0 / m0) * lateral
    pts[:, 1] += (dx0 / m0) * lateral
    centreline = pts.copy()
    oxbows: List[np.ndarray] = []

    for _ in range(max(0, int(passes))):
        dx = np.gradient(pts[:, 0])
        dz = np.gradient(pts[:, 1])
        mag = np.hypot(dx, dz)
        mag = np.where(mag < 1e-9, 1.0, mag)
        nx, nz = -dz / mag, dx / mag

        # Sign convention matters here, and getting it wrong *flattens* the river: the
        # unit normal (-dz, dx) points to the LEFT of the flow, but erosion happens on the
        # OUTER bank, which is the right-hand side when the channel turns left.  The
        # displacement therefore carries a minus sign - a bend grows away from its own
        # centre of curvature.
        kappa = _curvature(pts[:, 0], pts[:, 1])
        turn = -kappa * mag
        turn_norm = turn / max(float(turn_reference), 1e-6)
        # slow water builds a point bar and undercuts the outer bank; fast water just cuts
        slowness = 1.0 / (1.0 + 14.0 * np.clip(slope, 0.0, None))
        eligible = (q >= float(np.percentile(q, 40))) if q.size >= 5 else np.ones_like(q, bool)
        dn = (float(erosion) * width * turn_norm * slowness
              * np.power(q / max(q_ref, 1e-6), 0.45))
        dn = dn * np.where(eligible, 1.0, 0.0)
        limit = np.maximum(belt, 1.0) * 0.35
        dn = np.clip(dn, -limit, limit)
        dn = _smooth_polyline(dn[:, None], passes=1, weight=0.25)[:, 0]

        pts[:, 0] += nx * dn
        pts[:, 1] += nz * dn
        pts = _smooth_polyline(pts, passes=1, weight=float(smoothing))
        # a river may wander, but it stays inside its own valley
        pts[:, :2] = np.clip(pts[:, :2], centreline[:, :2] - belt[:, None] * 1.35,
                             centreline[:, :2] + belt[:, None] * 1.35)

        # --- cutoff: a non-adjacent self-contact means the neck has been breached -------
        loop = _find_loop(pts[:, :2], width, gap=6, factor=float(cutoff_width_factor))
        if loop is not None:
            i, j = loop
            oxbows.append(pts[i:j + 1].copy())
            bridge = np.linspace(pts[i], pts[j], max(3, (j - i) // 4))
            pts = np.concatenate([pts[:i], bridge, pts[j + 1:]], axis=0)
            centreline = np.concatenate([
                centreline[:i],
                np.linspace(centreline[i], centreline[j], max(3, (j - i) // 4)),
                centreline[j + 1:],
            ], axis=0)
            width = np.concatenate([width[:i], np.full(bridge.shape[0], width[i]), width[j + 1:]])
            q = np.concatenate([q[:i], np.full(bridge.shape[0], q[i]), q[j + 1:]])
            slope = np.concatenate([slope[:i], np.full(bridge.shape[0], slope[i]), slope[j + 1:]])
            belt = np.concatenate([belt[:i], np.full(bridge.shape[0], belt[i]), belt[j + 1:]])
    return pts, oxbows


# --------------------------------------------------------------------------------------
# 3. rasterising the shaped network back into terrain
# --------------------------------------------------------------------------------------


def rasterize_channels(
    rivers: Sequence[RiverPath],
    shape: Tuple[int, int],
    *,
    cell_size: float,
    origin: Tuple[float, float],
    min_discharge: float = 0.0,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Burn the shaped polylines into rasters the carver and the water mask can share.

    Returns ``(mask, half_width, depth, bed_level)`` in cell coordinates.  Keeping the
    geometry in a raster - rather than carving from the accumulation threshold - is what
    lets a *migrated* meander actually shape the ground: the channel the player sees, the
    channel the carver cuts and the channel the water mask fills are then the same object.
    """
    h, w = shape
    mask = np.zeros((h, w), dtype=bool)
    half_width = np.zeros((h, w), dtype=np.float64)
    depth = np.zeros((h, w), dtype=np.float64)
    level = np.full((h, w), np.nan, dtype=np.float64)
    cs = max(float(cell_size), 1e-6)
    ox, oz = float(origin[0]), float(origin[1])

    for river in rivers:
        pts = np.asarray(river.points, dtype=np.float64)
        if pts.shape[0] == 0:
            continue
        q = np.maximum(np.asarray(river.discharge, dtype=np.float64), 1.0)
        wid = np.maximum(np.asarray(river.width, dtype=np.float64), 1.0)
        # hydraulic geometry: width ~ Q^0.5, depth ~ Q^0.4 (the same law the tier carver
        # uses, so shaped and unshaped reaches meet without a step)
        qn = np.maximum(q, min_discharge) / max(float(min_discharge), 1.0)
        chan_w = 0.14 * np.sqrt(qn) * np.sqrt(cs) * 1.6 * 2.0
        half = np.maximum((wid * 0.5 + chan_w) / cs, 0.6)                 # cells
        # width/depth ratio of natural channels is roughly 6-15; clamping keeps a
        # mountain brook at one block and a continental river at four or five
        dep = np.clip(wid / 7.0, 1.0, 5.0)                                # blocks
        cols = (pts[:, 0] - ox) / cs
        rows = (pts[:, 1] - oz) / cs
        for i in range(pts.shape[0]):
            r, c = int(round(rows[i])), int(round(cols[i]))
            rad = int(np.ceil(half[i]))
            if rad < 0:
                continue
            r0, r1 = max(0, r - rad), min(h, r + rad + 1)
            c0, c1 = max(0, c - rad), min(w, c + rad + 1)
            if r0 >= r1 or c0 >= c1:
                continue
            rr = np.arange(r0, r1)[:, None]
            cc = np.arange(c0, c1)[None, :]
            inside = (rr - r) ** 2 + (cc - c) ** 2 <= half[i] ** 2
            if not np.any(inside):
                continue
            sub = (slice(r0, r1), slice(c0, c1))
            mask[sub] |= inside
            half_width[sub] = np.where(inside, np.maximum(half_width[sub], half[i] * cs),
                                       half_width[sub])
            depth[sub] = np.where(inside, np.maximum(depth[sub], dep[i]), depth[sub])
            cur = level[sub]
            bed = np.where(inside, pts[i, 2], np.nan)
            level[sub] = np.where(np.isnan(cur), bed,
                                  np.where(np.isnan(bed), cur, np.minimum(cur, bed)))
    return mask, half_width, depth, level


# --------------------------------------------------------------------------------------
# 4. the shaper
# --------------------------------------------------------------------------------------


@dataclass
class RiverResult:
    dem: np.ndarray
    rivers: List[RiverPath]
    oxbows: List[Lake] = field(default_factory=list)
    waterfalls: List[Tuple[float, float, float]] = field(default_factory=list)
    channel_mask: Optional[np.ndarray] = None
    diagnostics: Dict[str, float] = field(default_factory=dict)


class RiverShaper:
    """Longitudinal-equilibrium river shaper (see module docstring)."""

    def __init__(self, cfg: Optional[Dict[str, float]] = None, *, seed: int = 0):
        self.cfg = {**DEFAULT_RIVER_LOGIC,
                    **{k: v for k, v in (cfg or {}).items() if k in DEFAULT_RIVER_LOGIC}}
        self.seed = int(seed)

    # ----------------------------------------------------------------------------------
    def shape(
        self,
        dem: np.ndarray,
        rivers: Sequence[RiverPath],
        *,
        cell_size: float,
        threshold: float,
        ocean: np.ndarray,
        lake_mask: np.ndarray,
        sea_level: float = 63.0,
        origin: Tuple[float, float] = (0.0, 0.0),
    ) -> RiverResult:
        c = self.cfg
        if not rivers or float(c["enabled"]) <= 0:
            return RiverResult(dem=np.asarray(dem, dtype=np.float64).copy(),
                               rivers=list(rivers))

        dem = np.asarray(dem, dtype=np.float64).copy()
        h, w = dem.shape
        cs = max(float(cell_size), 1e-6)
        ox0, oz0 = float(origin[0]), float(origin[1])
        out_rivers: List[RiverPath] = []
        oxbows: List[Lake] = []
        waterfalls: List[Tuple[float, float, float]] = []
        graded_cells = 0
        migrated = 0

        bed_target = np.full((h, w), np.nan, dtype=np.float64)
        influence = np.zeros((h, w), dtype=np.float64)

        for river_index, river in enumerate(rivers):
            pts = np.asarray(river.points, dtype=np.float64)
            if pts.shape[0] < 3:
                out_rivers.append(river)
                continue
            q = np.maximum(np.asarray(river.discharge, dtype=np.float64), 1.0)
            width = np.maximum(np.asarray(river.width, dtype=np.float64), 1.0)

            # --- 1. graded bed ---------------------------------------------------------
            seg = np.hypot(*np.diff(pts[:, :2], axis=0).T)
            s = np.concatenate([[0.0], np.cumsum(seg)])
            graded, eq_slope = plan_longitudinal_profile(
                pts[:, 2], q, s, theta=float(c["theta"]),
                strength=float(c["grade_strength"]),
                min_discharge=float(c["grade_min_q"]),
            )
            hot = find_knickpoints(graded, eq_slope, s,
                                   ratio=float(c["knickpoint_ratio"]),
                                   min_drop=float(c["knickpoint_min_drop"]))
            if hot.size:
                graded = migrate_knickpoints(graded, q, s, hot,
                                             amount=float(c["knickpoint_migrate"]))
                for k in hot[:4000]:
                    if k + 1 < pts.shape[0]:
                        waterfalls.append((float(pts[k, 0]), float(pts[k, 1]),
                                           float(graded[k])))
            pts[:, 2] = graded

            # --- 2. meander migration (lowland rivers only) ----------------------------
            slope = np.abs(np.gradient(graded, s)) if s[-1] > 0 else np.zeros_like(graded)
            if (q.min() >= float(c["meander_min_q"])
                    and float(np.median(slope)) <= float(c["meander_max_slope"])
                    and pts.shape[0] >= 8):
                new_pts, loops = migrate_meanders(
                    pts, width=width, discharge=q, slope=slope,
                    passes=int(c["meander_passes"]), erosion=float(c["meander_erosion"]),
                    smoothing=float(c["meander_smoothing"]),
                    belt_scale=float(c["belt_scale"]),
                    cutoff_width_factor=float(c["cutoff_width_factor"]),
                    turn_reference=float(c["turn_reference"]),
                    seed=self.seed + river_index * 7,
                )
                if new_pts.shape[0] >= 3:
                    pts = new_pts
                    width = _resample_attr(width, pts.shape[0])
                    q = _resample_attr(q, pts.shape[0])
                    migrated += 1
                    for loop in loops:
                        oxbows.append(self._oxbow_lake(loop, cell_size=cs,
                                                       origin=(ox0, oz0),
                                                       sea_level=sea_level))
                    pts[:, 2] = np.minimum.accumulate(pts[:, 2])

            # --- 3. delta / distributaries at the sea ----------------------------------
            if river.ends_in == "sea" and float(q[-1]) >= float(c["delta_min_q"]):
                pts, q, width = self._split_delta(
                    pts, q, width, cell_size=cs, sea_level=sea_level,
                    branches=int(c["delta_branches"]), reach=float(c["delta_reach"]),
                )

            # --- write the graded bed into the terrain field ---------------------------
            cx = np.clip(np.rint((pts[:, 0] - ox0) / cs), 0, w - 1).astype(np.int64)
            cz = np.clip(np.rint((pts[:, 1] - oz0) / cs), 0, h - 1).astype(np.int64)
            for i in range(pts.shape[0]):
                zi, xi = int(cz[i]), int(cx[i])
                if np.isnan(bed_target[zi, xi]) or pts[i, 2] < bed_target[zi, xi]:
                    bed_target[zi, xi] = pts[i, 2]
                influence[zi, xi] = max(
                    influence[zi, xi], min(float(q[i]) / max(float(threshold), 1.0), 1.0)
                )
            graded_cells += pts.shape[0]

            out_rivers.append(RiverPath(points=pts, discharge=q, width=width,
                                        order=river.order, name=river.name,
                                        ends_in=river.ends_in))

        if np.any(influence > 0) and float(c["grade_strength"]) > 0:
            dem = self._apply_bed(dem, bed_target, influence)

        diag = {
            "rivers_shaped": float(len(out_rivers)),
            "meandered": float(migrated),
            "oxbow_lakes": float(len(oxbows)),
            "knickpoints": float(len(waterfalls)),
            "graded_points": float(graded_cells),
        }
        return RiverResult(dem=dem, rivers=out_rivers, oxbows=oxbows,
                           waterfalls=waterfalls, diagnostics=diag)

    # ----------------------------------------------------------------------------------
    def _apply_bed(self, dem: np.ndarray, target: np.ndarray,
                   influence: np.ndarray) -> np.ndarray:
        """Pull the terrain down onto the graded bed, then feather the edges.

        Only lowering is allowed (a river erodes its floor), and the influence field is
        dilated outward so the valley walls come down with the bed instead of leaving a
        one-cell slot canyon.
        """
        from scipy import ndimage

        known = ~np.isnan(target)
        if not np.any(known):
            return dem
        idx = ndimage.distance_transform_edt(~known, return_distances=False,
                                             return_indices=True)
        filled = target[tuple(idx)]
        weight = ndimage.maximum_filter(influence, size=3, mode="nearest")
        weight = ndimage.gaussian_filter(weight, sigma=1.0, mode="nearest")
        drop = np.maximum(dem - filled, 0.0)
        return dem - drop * np.clip(weight, 0.0, 1.0)

    # ----------------------------------------------------------------------------------
    def _split_delta(self, pts: np.ndarray, q: np.ndarray, width: np.ndarray, *,
                     cell_size: float, sea_level: float, branches: int,
                     reach: float) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Fan the last stretch of a big river into distributaries.

        A delta is what a river builds when it can no longer carry its load: the channel
        splits, each arm drops its sediment, and the shoreline advances.  The arms are
        appended to the same path so the carver, the water mask and the preview all pick
        them up without a second code path.
        """
        if pts.shape[0] < 4 or branches < 2:
            return pts, q, width
        mouth = pts[-1]
        back = pts[-min(6, pts.shape[0]):]
        dx = float(np.mean(np.diff(back[:, 0])))
        dz = float(np.mean(np.diff(back[:, 1])))
        base = math.atan2(dz, dx)
        extra: List[np.ndarray] = []
        extra_q: List[np.ndarray] = []
        extra_w: List[np.ndarray] = []
        for b in range(branches):
            frac = (b - (branches - 1) / 2.0) / max((branches - 1) / 2.0, 1.0)
            ang = base + frac * math.radians(34.0)
            length = float(reach) * (0.75 + 0.25 * (1.0 - abs(frac)))
            t = np.linspace(0.0, 1.0, max(4, int(length / max(cell_size, 1.0))))
            xs = mouth[0] + np.cos(ang) * length * t
            zs = mouth[1] + np.sin(ang) * length * t
            ys = np.linspace(mouth[2], sea_level - 1.0, t.size)
            extra.append(np.stack([xs, zs, ys], axis=1))
            # each distributary carries a share of the flow and narrows downstream, which
            # is what makes a delta read as a fan rather than as three equal channels
            share = 1.0 / max(branches, 1)
            extra_q.append(np.linspace(q[-1] * share, q[-1] * share * 0.6, t.size))
            extra_w.append(np.linspace(width[-1] * 0.75, width[-1] * 0.45, t.size))
        pts = np.concatenate([pts, *extra], axis=0)
        q = np.concatenate([np.asarray(q, dtype=np.float64), *extra_q], axis=0)
        width = np.concatenate([np.asarray(width, dtype=np.float64), *extra_w], axis=0)
        return pts, q, width

    # ----------------------------------------------------------------------------------
    def _oxbow_lake(self, loop: np.ndarray, *, cell_size: float,
                    origin: Tuple[float, float], sea_level: float) -> Lake:
        """Turn an abandoned meander loop into a lake record."""
        cs = max(float(cell_size), 1e-6)
        cx = np.clip(np.rint((loop[:, 0] - origin[0]) / cs), 0, 10 ** 9).astype(np.int64)
        cz = np.clip(np.rint((loop[:, 1] - origin[1]) / cs), 0, 10 ** 9).astype(np.int64)
        cells = np.unique(np.stack([cz, cx], axis=1), axis=0)
        surface = float(np.mean(loop[:, 2])) if loop.shape[1] > 2 else sea_level
        return Lake(cells=cells, surface=surface,
                    volume=float(cells.shape[0]) * 2.0, depth_max=2.5)
