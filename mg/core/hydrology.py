"""Hydrology: fill -> route -> accumulate -> carve.

This module is the backbone of the "Streams Reflowing" style water logic:

1.  ``priority_flood``      - removes every closed depression (Barnes et al. 2014),
                              remembering how deep each basin had to be filled so we
                              can turn those basins back into *lakes*.
2.  ``flow_directions``     - D8 plus a smoothed D-infinity style vector field, so the
                              exported flow map is continuous across cell borders
                              (the same idea as Streams Reflowing imposing a server
                              computed direction and smoothing channel seams).
3.  ``flow_accumulation``   - drainage area per cell, computed by processing cells in
                              descending elevation order (no recursion, O(n log n)).
4.  ``watershed_labels``    - which outlet each cell drains to.
5.  ``build_river_network`` - vector polylines from the accumulation raster, complete
                              with Strahler stream order.
6.  ``carve_channels``      - hydraulic geometry: width ~ Q^0.5, depth ~ Q^0.4, with
                              bank flare so the channel meets the terrain smoothly.
7.  ``detect_waterfalls``   - steep knickpoints along a channel profile.

Every function is pure numpy + stdlib and safe to call on a few hundred thousand
cells.
"""

from __future__ import annotations

import heapq
import math
from typing import Dict, List, Optional, Tuple

import numpy as np

from .noise import fbm
from .types import Lake, RiverPath

# D8 neighbourhood, ordered so that index 0..7 is NW, N, NE, E, SE, S, SW, W
_D8 = np.array(
    [[-1, -1], [-1, 0], [-1, 1], [0, 1], [1, 1], [1, 0], [1, -1], [0, -1]], dtype=np.int64
)
_D8_DIST = np.array([np.hypot(dx, dy) for dx, dy in _D8], dtype=np.float64)


# --------------------------------------------------------------------------------------
# 1. Depression handling
# --------------------------------------------------------------------------------------


def priority_flood(dem: np.ndarray, epsilon: float = 1e-4, nodata: Optional[np.ndarray] = None
                   ) -> Tuple[np.ndarray, np.ndarray]:
    """Fill depressions so every cell has a downhill path to the grid border.

    Returns ``(filled, fill_depth)`` where ``fill_depth = filled - dem`` reveals the
    basins that would impound water (i.e. future lakes).
    """
    dem = np.asarray(dem, dtype=np.float64)
    h, w = dem.shape
    filled = dem.copy()
    closed = np.zeros((h, w), dtype=bool)
    if nodata is not None:
        closed |= nodata

    heap: List[Tuple[float, int]] = []
    # Seed with the border ring (and any nodata edges) - water must exit there.
    border = np.zeros((h, w), dtype=bool)
    border[0, :] = border[-1, :] = True
    border[:, 0] = border[:, -1] = True
    for z, x in zip(*np.nonzero(border & ~closed)):
        closed[z, x] = True
        heapq.heappush(heap, (float(dem[z, x]), int(z * w + x)))

    while heap:
        zc, idx = heapq.heappop(heap)
        cz, cx = divmod(idx, w)
        for dz, dx in _D8:
            nz, nx = cz + dz, cx + dx
            if nz < 0 or nx < 0 or nz >= h or nx >= w:
                continue
            if closed[nz, nx]:
                continue
            closed[nz, nx] = True
            nh = dem[nz, nx]
            filled_v = zc + epsilon if nh <= zc else nh
            filled[nz, nx] = filled_v
            heapq.heappush(heap, (float(filled_v), int(nz * w + nx)))

    # Any nodata cells were never visited: give them their own height so routing works.
    unvisited = ~closed
    if np.any(unvisited):
        filled[unvisited] = dem[unvisited]
    return filled, np.maximum(filled - dem, 0.0)


# --------------------------------------------------------------------------------------
# 2. Flow routing
# --------------------------------------------------------------------------------------


def flow_directions(dem: np.ndarray, *, nodata: Optional[np.ndarray] = None,
                    flat_epsilon: float = 1e-6) -> np.ndarray:
    """D8 steepest-descent direction per cell.

    Returns an int8 array of D8 indices, ``-1`` where the cell is a pit/outlet.
    Flat filled areas get seeded with a tiny slope so they still route.
    """
    dem = np.asarray(dem, dtype=np.float64)
    h, w = dem.shape
    best_slope = np.full((h, w), -np.inf)
    best_dir = np.full((h, w), -1, dtype=np.int8)
    zz = np.arange(h)[:, None]
    xx = np.arange(w)[None, :]

    for k, (dz, dx) in enumerate(_D8):
        nz_raw = zz + dz
        nx_raw = xx + dx
        valid = np.broadcast_to(
            (nz_raw >= 0) & (nz_raw < h) & (nx_raw >= 0) & (nx_raw < w), (h, w)
        )
        nz = np.broadcast_to(np.clip(nz_raw, 0, h - 1), (h, w))
        nx = np.broadcast_to(np.clip(nx_raw, 0, w - 1), (h, w))
        neighbour = dem[nz, nx]
        slope = (dem - neighbour) / _D8_DIST[k]
        slope = np.where(valid, slope, np.inf)  # off-grid drains away
        better = slope > best_slope
        best_slope = np.where(better, slope, best_slope)
        best_dir = np.where(better, np.int8(k), best_dir)

    if nodata is not None:
        best_dir = np.where(nodata, np.int8(-1), best_dir)
    best_dir = np.where(best_slope <= flat_epsilon, np.int8(-1), best_dir)
    return best_dir


def flow_vectors(dirs: np.ndarray, *, smooth: int = 1) -> np.ndarray:
    """Unit surface-flow vectors, smoothed so neighbouring channels agree.

    This is what the preview shader and the exported ``flow`` map consume; it mirrors
    the "flow direction computed once per chunk and imposed at render time" trick.
    """
    h, w = dirs.shape
    vx = np.zeros((h, w), dtype=np.float64)
    vy = np.zeros((h, w), dtype=np.float64)
    for k, (dz, dx) in enumerate(_D8):
        m = dirs == k
        if not np.any(m):
            continue
        # world axes: +z is south (down the raster), +x is east (right)
        vy = np.where(m, float(dz) / _D8_DIST[k], vy)
        vx = np.where(m, float(dx) / _D8_DIST[k], vx)
    for _ in range(max(0, int(smooth))):
        pad = np.pad(vx, 1, mode="edge")
        sx = (pad[:-2, 1:-1] + pad[2:, 1:-1] + pad[1:-1, :-2] + pad[1:-1, 2:]) * 0.25
        pad = np.pad(vy, 1, mode="edge")
        sy = (pad[:-2, 1:-1] + pad[2:, 1:-1] + pad[1:-1, :-2] + pad[1:-1, 2:]) * 0.25
        has = (dirs >= 0).astype(np.float64)
        has_pad = np.pad(has, 1, mode="edge")
        wsum = (has_pad[:-2, 1:-1] + has_pad[2:, 1:-1] + has_pad[1:-1, :-2] + has_pad[1:-1, 2:]) * 0.25
        vx = np.where(has > 0, (vx + sx * wsum) / (1.0 + wsum), vx)
        vy = np.where(has > 0, (vy + sy * wsum) / (1.0 + wsum), vy)
    mag = np.hypot(vx, vy)
    mag = np.where(mag < 1e-9, 1.0, mag)
    return np.stack([vx / mag, vy / mag], axis=-1).astype(np.float32)


def flow_accumulation(dem: np.ndarray, dirs: np.ndarray, weights: Optional[np.ndarray] = None
                      ) -> np.ndarray:
    """Drainage area (in cells) for every cell, including itself."""
    dem = np.asarray(dem, dtype=np.float64)
    h, w = dem.shape
    acc = np.ones((h, w), dtype=np.float64) if weights is None else np.array(weights, dtype=np.float64)
    order = np.argsort(dem, axis=None)[::-1]  # high -> low
    flat_dirs = dirs.ravel()
    acc_flat = acc.ravel()
    ys, xs = np.divmod(np.arange(h * w), w)
    for idx in order:
        d = flat_dirs[idx]
        if d < 0:
            continue
        dz, dx = _D8[d]
        nz = ys[idx] + dz
        nx = xs[idx] + dx
        if nz < 0 or nx < 0 or nz >= h or nx >= w:
            continue
        acc_flat[nz * w + nx] += acc_flat[idx]
    return acc


def downstream_order(dem: np.ndarray, dirs: np.ndarray) -> np.ndarray:
    """Processing order from high to low elevation (index into flattened arrays)."""
    return np.argsort(dem, axis=None)[::-1]


# --------------------------------------------------------------------------------------
# 3. Basins / lakes / oceans
# --------------------------------------------------------------------------------------


def watershed_labels(dirs: np.ndarray, *, max_iter: int = 64) -> np.ndarray:
    """Label every cell with the id of the cell it eventually drains out of.

    Uses pointer jumping: each cell inherits its neighbour's terminal id, doubling the
    distance covered per pass, so it converges in ~log(n) iterations.
    """
    h, w = dirs.shape
    ys, xs = np.divmod(np.arange(h * w), w)
    label = np.arange(h * w, dtype=np.int64)
    flat_dirs = dirs.ravel()
    nz = ys.copy()
    nx = xs.copy()
    for k, (dz, dx) in enumerate(_D8):
        m = flat_dirs == k
        ny = np.where(m, ys + dz, ys)
        nxx = np.where(m, xs + dx, xs)
        inside = m & (ny >= 0) & (nxx >= 0) & (ny < h) & (nxx < w)
        nz = np.where(inside, ny, nz)
        nx = np.where(inside, nxx, nx)
    nxt = np.where(flat_dirs >= 0, nz * w + nx, np.arange(h * w))
    for _ in range(max_iter):
        old = label
        label = label[nxt]
        if np.array_equal(old, label):
            break
    return label.reshape(h, w) // w  # keep it readable: row index of the outlet


def fill_lakes(dem: np.ndarray, filled: np.ndarray, fill_depth: np.ndarray, *,
               min_depth: float = 0.6, min_cells: int = 2, sea_level: float = 63.0
               ) -> Tuple[np.ndarray, List[Lake]]:
    """Turn filled basins into lakes.

    Returns ``(lake_mask, lakes)``; ``lake_mask`` is True where water stands above the
    terrain.  Basins shallower than ``min_depth`` are left drained so we do not litter
    the map with one-block puddles.
    """
    from scipy import ndimage  # local import; scipy is a soft dependency

    h, w = dem.shape
    basin = fill_depth > min_depth
    lake_mask = np.zeros((h, w), dtype=bool)
    lakes: List[Lake] = []
    labels, count = ndimage.label(basin, structure=np.ones((3, 3), dtype=int))
    for i in range(1, count + 1):
        sel = labels == i
        if int(sel.sum()) < min_cells:
            continue
        surface = float(np.max(filled[sel]))
        if surface < sea_level:
            continue  # already below the sea, treat as ocean not lake
        lake_mask |= sel
        cells = np.argwhere(sel)
        depth = float(np.max(filled[sel] - dem[sel]))
        volume = float(np.sum(filled[sel] - dem[sel]))
        lakes.append(
            Lake(cells=cells.astype(np.int32), surface=surface, volume=volume, depth_max=depth)
        )
    return lake_mask, lakes


def ocean_mask(dem: np.ndarray, sea_level: float,
               exclude: Optional[np.ndarray] = None) -> np.ndarray:
    """Cells below sea level connected to the border (i.e. the actual sea).

    ``exclude`` marks cells that are below sea level but are not sea - a lava-filled
    crater basin, for instance.
    """
    from scipy import ndimage

    below = dem < sea_level
    if exclude is not None:
        below = below & ~np.asarray(exclude, dtype=bool)
    labels, count = ndimage.label(below, structure=np.ones((3, 3), dtype=int))
    if count == 0:
        return np.zeros_like(below)
    border_labels = set()
    for row in (0, dem.shape[0] - 1):
        border_labels.update(np.unique(labels[row, :]).tolist())
    for col in (0, dem.shape[1] - 1):
        border_labels.update(np.unique(labels[:, col]).tolist())
    border_labels.discard(0)
    out = np.zeros_like(below)
    for lb in border_labels:
        out |= labels == lb
    return out


# --------------------------------------------------------------------------------------
# 4. River network extraction
# --------------------------------------------------------------------------------------


def downstream_index(dirs: np.ndarray) -> np.ndarray:
    """Flat index of the cell each cell flows into (``-1`` for pits / off-grid)."""
    h, w = dirs.shape
    ys, xs = np.divmod(np.arange(h * w), w)
    flat_dirs = dirs.ravel().astype(np.int64)
    ok = flat_dirs >= 0
    safe = np.maximum(flat_dirs, 0)
    nz = ys + np.where(ok, _D8[:, 0][safe], 0)
    nx = xs + np.where(ok, _D8[:, 1][safe], 0)
    inside = ok & (nz >= 0) & (nx >= 0) & (nz < h) & (nx < w)
    nxt = np.full(h * w, -1, dtype=np.int64)
    nxt[inside] = nz[inside] * w + nx[inside]
    return nxt


def upstream_counts(flat_channel: np.ndarray, nxt: np.ndarray) -> np.ndarray:
    """How many *channel* cells drain directly into each cell."""
    hw = flat_channel.size
    counts = np.zeros(hw, dtype=np.int64)
    idx = np.nonzero(flat_channel)[0]
    if idx.size == 0:
        return counts
    targets = nxt[idx]
    valid = (targets >= 0) & (targets < hw)
    targets = targets[valid]
    valid2 = flat_channel[targets]
    np.add.at(counts, targets[valid2], 1)
    return counts


def build_river_network(
    dem: np.ndarray,
    dirs: np.ndarray,
    accum: np.ndarray,
    *,
    cell_size: float,
    origin: Tuple[float, float],
    min_discharge,
    mouth_is_water: Optional[np.ndarray] = None,
    max_rivers: int = 2048,
    min_length: int = 3,
) -> List[RiverPath]:
    """Vectorise the accumulation raster into ordered river *segments*.

    Segments run from a headwater (or a confluence) down to the next confluence or to
    the mouth, which is how river networks are normally represented and keeps each
    polyline's discharge monotonic.  ``min_discharge`` may be a scalar or a per-cell
    raster (climate-aware thresholds).
    """
    h, w = dem.shape
    channel = accum >= min_discharge
    if not np.any(channel):
        return []

    flat_channel = channel.ravel()
    flat_accum = accum.ravel()
    flat_dem = dem.ravel()
    nxt = downstream_index(dirs)
    up = upstream_counts(flat_channel, nxt)

    channel_idx = np.nonzero(flat_channel)[0]
    starts = channel_idx[(up[channel_idx] == 0) | (up[channel_idx] >= 2)]
    junction_set = set(channel_idx[up[channel_idx] >= 2].tolist())

    rivers: List[RiverPath] = []
    visited_pairs = set()
    # Walk the longest segments first so downstream reaches win ties.
    starts = starts[np.argsort(-flat_accum[starts])]

    for s in starts.tolist():
        path: List[int] = [s]
        cur = s
        overflow = 0
        while True:
            nxt_i = int(nxt[cur])
            if nxt_i < 0 or not flat_channel[nxt_i]:
                break
            path.append(nxt_i)
            cur = nxt_i
            if cur != s and cur in junction_set:
                break
            overflow += 1
            if overflow > 60000:
                break
        if len(path) < min_length:
            continue
        key = (path[0], path[-1], len(path))
        if key in visited_pairs:
            continue
        visited_pairs.add(key)

        idx = np.asarray(path, dtype=np.int64)
        zz, xx = np.divmod(idx, w)
        px = origin[0] + (xx + 0.5) * cell_size
        pz = origin[1] + (zz + 0.5) * cell_size
        py = flat_dem[idx]
        dis = flat_accum[idx]
        pts = np.stack([px, pz, py], axis=1).astype(np.float64)

        # hydraulic geometry: width grows as the square root of discharge
        widths = np.clip(0.55 * np.sqrt(np.maximum(dis, 1.0)), 1.2, 28.0)
        order = strahler_order(pts, dis)
        ends_in = "sink"
        last = int(idx[-1])
        lz, lx = divmod(last, w)
        if lz in (0, h - 1) or lx in (0, w - 1):
            ends_in = "sea"
        elif mouth_is_water is not None and mouth_is_water[lz, lx]:
            ends_in = "lake"
        rivers.append(
            RiverPath(points=pts, discharge=dis.astype(np.float64), width=widths,
                      order=order, ends_in=ends_in)
        )
        if len(rivers) >= max_rivers:
            break

    rivers.sort(key=lambda r: -float(np.max(r.discharge)))
    return rivers


def strahler_order(points: np.ndarray, discharge: np.ndarray) -> int:
    """Cheap Strahler estimate from the discharge profile of a segment."""
    if len(points) < 3:
        return 1
    ratio = float(discharge[-1]) / max(float(discharge[0]), 1.0)
    if ratio > 512:
        return 4
    if ratio > 128:
        return 3
    if ratio > 24:
        return 2
    return 1


# --------------------------------------------------------------------------------------
# 5. Channel carving
# --------------------------------------------------------------------------------------


def channel_geometry(discharge: np.ndarray, *, cell_size: float, width_coeff: float = 0.55,
                     width_exp: float = 0.5, depth_coeff: float = 0.30,
                     depth_exp: float = 0.40, max_width: float = 40.0,
                     max_depth: float = 12.0) -> Tuple[np.ndarray, np.ndarray]:
    """Hydraulic geometry relations: W ~ a*Q^b, D ~ c*Q^f (Leopold & Maddock)."""
    q = np.maximum(np.asarray(discharge, dtype=np.float64), 1.0)
    width = np.clip(width_coeff * np.power(q, width_exp) * (cell_size / 4.0) ** 0.0, 0.8, max_width)
    depth = np.clip(depth_coeff * np.power(q, depth_exp), 0.35, max_depth)
    return width, depth


def carve_channels(
    dem: np.ndarray,
    dirs: np.ndarray,
    accum: np.ndarray,
    *,
    cell_size: float,
    min_discharge: float,
    strength: float = 1.0,
    bank_flare: float = 2.4,
    valley_depth_scale: float = 1.0,
    sea_level: float = 63.0,
    lake_mask: Optional[np.ndarray] = None,
    passes: int = 2,
    meander_strength: float = 1.4,
    seed: int = 0,
) -> np.ndarray:
    """Widen and deepen terrain along the flow network.

    Carving is driven by the *distance to the nearest channel* combined with that
    channel's own hydraulic geometry, so the incision stays connected and continuous -
    the same reason Streams Reflowing builds streams from watershed paths instead of
    stamping shapes per chunk.  ``bank_flare`` controls how far the incision bleeds into
    the surrounding slope: large values give soft grassy banks, small values give sharp
    canyon walls.
    """
    from scipy import ndimage

    dem = np.asarray(dem, dtype=np.float64).copy()
    if np.size(accum) == 0:
        return dem
    if not np.any(accum >= min_discharge):
        return dem

    warped_accum = warp_field(accum, cell_size=cell_size, seed=seed,
                              strength=meander_strength, scale=14.0)
    if np.ndim(min_discharge) == 0:
        if not np.any(warped_accum >= min_discharge) and not np.any(accum >= min_discharge):
            return dem

    for _ in range(max(1, int(passes))):
        river = warped_accum >= min_discharge
        width, depth = channel_geometry(warped_accum, cell_size=cell_size)
        target_depth = depth * float(strength) * float(valley_depth_scale)
        if np.ndim(target_depth) == 0:
            target_depth = np.full(dem.shape, float(target_depth))

        # distance (in blocks) to the nearest channel + which channel cell that is
        dist, (iz, ix) = ndimage.distance_transform_edt(~river, return_indices=True)
        dist = dist * cell_size

        half_width = np.maximum(width * 0.5, 0.5)
        local_half_width = np.where(river, half_width, half_width[iz, ix])
        local_depth = np.where(river, target_depth, target_depth[iz, ix])
        bank = np.where(river, dem, dem[iz, ix])

        t = np.clip(dist / np.maximum(local_half_width * float(bank_flare), 1e-6), 0.0, 1.0)
        profile = (1.0 - t) ** 1.6  # smooth falloff from channel floor up to the bank

        # A channel can only cut so far below its own bank, and never below the sea
        # floor unless the ground was already down there.  Without this guard a high
        # discharge cell next to a low coastal bank would gouge a canyon far past the
        # waterline.
        sea_guard = sea_level - 6.0
        floor_target = np.maximum(bank - local_depth, np.minimum(bank, sea_guard))
        carved = dem + np.minimum(profile * (floor_target - dem), 0.0)
        carved = np.maximum(carved, np.minimum(dem, sea_guard))

        if lake_mask is not None:
            carved = np.where(lake_mask, dem, carved)
        dem = carved
    return dem



# --------------------------------------------------------------------------------------
# D-infinity routing, stream-power incision and tiered channel cross-sections
# (specification sections 2.3 A.2 - A.4)
# --------------------------------------------------------------------------------------


def dinf_flow(dem: np.ndarray, *, nodata: Optional[np.ndarray] = None,
              flat_epsilon: float = 1e-6) -> Tuple[np.ndarray, np.ndarray]:
    """D-infinity flow distribution - aspect-driven routing with continuous splitting.

    Water leaving a cell is split between the two neighbours that straddle the steepest
    descent *facet*, weighted by the exact angular position inside that facet (Tarboton
    1997).  This is the continuous, aspect-driven alternative to D8's 45-degree
    staircase that the specification calls for before stream-power incision (section
    2.3 A.2): drainage organizes along the true fall line rather than snapping to eight
    compass directions.

    One well-known failure mode is handled explicitly: a facet plane fitted through a
    narrow V-shaped valley floor and its two uphill neighbours points *back up the
    walls*, so a pure facet router strands water exactly where rivers run.  Cells with no
    usable facet therefore fall back to the D8 steepest neighbour, which is precisely the
    case D8 resolves correctly.

    Returns ``(fracs, vector)``: ``fracs`` is ``(h, w, 8)`` summing to 1 wherever the cell
    drains somewhere, ``vector`` is the unit flow direction in world axes (``+x`` east,
    ``+z`` south).
    """
    dem = np.asarray(dem, dtype=np.float64)
    h, w = dem.shape
    fracs = np.zeros((h, w, 8), dtype=np.float32)
    vec = np.zeros((h, w, 2), dtype=np.float32)
    if h < 3 or w < 3:
        return fracs, vec

    right = np.array([1, 2, 3, 4, 5, 6, 7, 0], dtype=np.int64)
    cos45 = math.cos(math.pi / 4.0)
    sin45 = math.sin(math.pi / 4.0)
    step = math.pi / 4.0
    best_slope = np.zeros((h, w), dtype=np.float64)
    best_facet = np.full((h, w), -1, dtype=np.int8)
    best_w1 = np.zeros((h, w), dtype=np.float64)   # weight on the facet's second edge

    zz = np.arange(1, h - 1)[:, None]
    xx = np.arange(1, w - 1)[None, :]
    centre = dem[zz, xx]
    for k in range(8):
        k1 = int(right[k])
        dz0, dx0 = _D8[k]
        dz1, dx1 = _D8[k1]
        s0 = (centre - dem[zz + dz0, xx + dx0]) / _D8_DIST[k]
        s1 = (centre - dem[zz + dz1, xx + dx1]) / _D8_DIST[k1]
        gx = s0
        gy = (s1 - cos45 * s0) / sin45
        r = np.arctan2(gy, gx)
        inside = (r >= -1e-12) & (r <= step + 1e-12) & (gx > 0.0)
        s_best = np.hypot(gx, gy)
        w1 = np.clip(r / step, 0.0, 1.0)
        better = inside & (s_best > best_slope[1:-1, 1:-1])
        best_slope[1:-1, 1:-1] = np.where(better, s_best, best_slope[1:-1, 1:-1])
        best_facet[1:-1, 1:-1] = np.where(better, np.int8(k), best_facet[1:-1, 1:-1])
        best_w1[1:-1, 1:-1] = np.where(better, w1, best_w1[1:-1, 1:-1])

    active = best_slope > max(flat_epsilon, 1e-9)
    active[:1, :] = active[-1:, :] = False
    active[:, :1] = active[:, -1:] = False
    for k in range(8):
        sel = active & (best_facet == k)
        if not np.any(sel):
            continue
        k1 = int(right[k])
        w1 = best_w1
        fracs[..., k] = np.where(sel, 1.0 - w1, fracs[..., k]).astype(np.float32)
        fracs[..., k1] = np.where(sel, w1, fracs[..., k1]).astype(np.float32)
        dz0, dx0 = _D8[k]
        dz1, dx1 = _D8[k1]
        vx = (dx0 / _D8_DIST[k]) * (1.0 - w1) + (dx1 / _D8_DIST[k1]) * w1
        vy = (dz0 / _D8_DIST[k]) * (1.0 - w1) + (dz1 / _D8_DIST[k1]) * w1
        mag = np.hypot(vx, vy)
        mag = np.where(mag < 1e-9, 1.0, mag)
        vec[..., 0] = np.where(sel, vx / mag, vec[..., 0]).astype(np.float32)
        vec[..., 1] = np.where(sel, vy / mag, vec[..., 1]).astype(np.float32)

    # flats and V-notch floors: route with D8 so nothing strands (see docstring)
    fallback = flow_directions(dem)
    need = (~active) & (fallback >= 0)
    if np.any(need):
        for k in range(8):
            sel = need & (fallback == k)
            if not np.any(sel):
                continue
            fracs[..., k] = np.where(sel, 1.0, fracs[..., k]).astype(np.float32)
            dz0, dx0 = _D8[k]
            vec[..., 0] = np.where(sel, dx0 / _D8_DIST[k], vec[..., 0]).astype(np.float32)
            vec[..., 1] = np.where(sel, dz0 / _D8_DIST[k], vec[..., 1]).astype(np.float32)
        active = active | need

    vec = np.where(active[..., None], vec, 0.0).astype(np.float32)
    total = fracs.sum(axis=2, keepdims=True)
    fracs = np.divide(fracs, total, out=np.zeros_like(fracs), where=total > 0)
    if nodata is not None:
        fracs[nodata] = 0.0
        vec[nodata] = 0.0
    return fracs, vec


def dinf_accumulation(dem: np.ndarray, fracs: np.ndarray,
                      weights: Optional[np.ndarray] = None) -> np.ndarray:
    """Drainage area (in cells) using the D-infinity fractions from :func:`dinf_flow`."""
    dem = np.asarray(dem, dtype=np.float64)
    h, w = dem.shape
    acc = np.ones((h, w), dtype=np.float64) if weights is None else np.array(weights, float).copy()
    order = np.argsort(dem, axis=None)[::-1]
    ys, xs = np.divmod(np.arange(h * w), w)
    acc_flat = acc.ravel()
    frac_flat = fracs.reshape(h * w, 8)
    for idx in order:
        share = acc_flat[idx]
        if share <= 0:
            continue
        y, x = ys[idx], xs[idx]
        for k in range(8):
            f = frac_flat[idx, k]
            if f <= 0:
                continue
            dz, dx = _D8[k]
            ny, nx = y + dz, x + dx
            if 0 <= ny < h and 0 <= nx < w:
                acc_flat[ny * w + nx] += share * f
    return acc


def match_accumulation_scale(reference: np.ndarray, routed: np.ndarray,
                             *, mask: Optional[np.ndarray] = None,
                             quantile: float = 0.99, floor: float = 1.0) -> float:
    """Scale factor that puts a D-infinity accumulation onto a D8 accumulation's scale.

    Splitting flow between two neighbours - the whole point of D-infinity - lowers the
    absolute drainage-area values a channel accumulates, because water is shared with the
    parallel neighbour instead of being funnelled into one cell.  The network structure
    is right but the *numbers* no longer line up with thresholds that were written for
    D8 terrain (the specification's 500 / 5000 tier cut-offs, or a user's
    ``water.river_threshold``).

    This returns the ratio of high quantiles of the two fields, so callers can rescale
    their thresholds and keep the cut-offs meaning "headwater", "mid-tier" and
    "continental" rather than silently dropping every river.
    """
    ref = np.asarray(reference, dtype=np.float64)
    new = np.asarray(routed, dtype=np.float64)
    if mask is not None:
        m = np.asarray(mask, dtype=bool)
        ref, new = ref[m], new[m]
    if ref.size == 0 or new.size == 0:
        return max(float(floor), 1.0)
    q_ref = float(np.quantile(ref, quantile))
    q_new = float(np.quantile(new, quantile))
    if q_new <= 1e-9 or q_ref <= 1e-9:
        return max(float(floor), 1.0)
    return max(float(floor), q_ref / q_new)


def stream_power_incision(
    dem: np.ndarray,
    accum: np.ndarray,
    *,
    cell_size: float = 4.0,
    k: float = 4.0e-3,
    m: float = 0.5,
    n: float = 1.0,
    max_incision: float = 1.5,
    min_discharge: float = 24.0,
) -> np.ndarray:
    """Stream-power law incision: ``dH = -K * A^m * |grad H|^n`` (spec 2.3 A.3).

    Uses m = 0.5 / n = 1.0 by default, the conventional drainage-area and slope
    exponents, and only touches cells carrying real discharge so hillslopes keep the
    thermal-weathering signature instead of being uniformly etched.
    """
    dem = np.asarray(dem, dtype=np.float64)
    dzdy, dzdx = np.gradient(dem, cell_size)
    slope = np.hypot(dzdx, dzdy)
    relative = np.maximum(accum, 1.0)
    channel = accum >= float(min_discharge)
    drop = k * np.power(relative, m) * np.power(np.maximum(slope, 1e-5), n)
    drop = np.minimum(drop, float(max_incision))
    return dem - np.where(channel, drop, 0.0)


# channel tiers from the specification: headwater torrents, mid-tier rivers and
# continental lowland rivers, each with its own cross-section shape.
CHANNEL_TIERS = (
    # (name, discharge_ceiling, width_coeff, depth_coeff, profile_exponent, alluvial)
    ("torrent", 500.0, 0.085, 0.075, 1.10, 0.0),     # narrow V-shaped ravines
    ("river", 5000.0, 0.135, 0.055, 1.60, 0.35),     # U-shaped braided channels
    ("lowland", float("inf"), 0.190, 0.040, 2.10, 1.0),  # wide alluvial meander belt
)


def classify_tiers(accum: np.ndarray, *, threshold: float = 320.0) -> np.ndarray:
    """0 = dry, 1 = headwater torrent, 2 = mid-tier river, 3 = lowland river."""
    a = np.asarray(accum, dtype=np.float64)
    tiers = np.zeros(a.shape, dtype=np.uint8)
    cut = float(threshold)
    tiers[a >= cut] = 1
    tiers[a >= max(500.0, cut * 1.6)] = 2
    tiers[a >= max(5000.0, cut * 8.0)] = 3
    return tiers


def tier_channel_geometry(accum: np.ndarray, *, cell_size: float, threshold: float = 320.0,
                          ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-cell channel half-width (blocks), depth (blocks) and tier id.

    Width follows ``W ~ Q^0.5`` and depth ``D ~ Q^0.4`` with per-tier coefficients, so
    torrents are narrow and deep while lowland rivers are wide and shallow - the
    hydraulic-geometry distinction the specification asks for.
    """
    a = np.maximum(np.asarray(accum, dtype=np.float64), 1.0)
    tiers = classify_tiers(a, threshold=threshold)
    width = np.zeros(a.shape, dtype=np.float64)
    depth = np.zeros(a.shape, dtype=np.float64)
    norm = a / max(float(threshold), 1.0)
    for tid, (_name, _ceil, wc, dc, _exp, _allu) in enumerate(CHANNEL_TIERS, start=1):
        sel = tiers == tid
        if not np.any(sel):
            continue
        width = np.where(sel, wc * np.sqrt(norm) * np.sqrt(cell_size) * 1.6, width)
        depth = np.where(sel, dc * np.power(norm, 0.4) * np.sqrt(cell_size) * 2.2, depth)
    return width * 0.5, depth, tiers


def carve_tiered_channels(
    dem: np.ndarray,
    accum: np.ndarray,
    *,
    cell_size: float,
    threshold: float = 320.0,
    bank_flare: float = 2.6,
    sea_level: float = 63.0,
    valley_depth_scale: float = 1.0,
    passes: int = 2,
    lake_mask: Optional[np.ndarray] = None,
    alluvial_spread: float = 3.0,
) -> Tuple[np.ndarray, np.ndarray]:
    """Carve each channel tier with its own cross-section, plus lowland levees.

    * torrents get a narrow, steep V (exponent ~1.1) that follows the slope;
    * mid-tier rivers get a rounded U with braided gravel banks;
    * lowland rivers get a wide, flat alluvial floor, and the ground beside them is
      raised slightly into natural levees the way a flooding river builds them.

    Returns ``(dem, tiers)``.
    """
    from scipy import ndimage

    dem = np.asarray(dem, dtype=np.float64).copy()
    half_width, depth, tiers = tier_channel_geometry(
        accum, cell_size=cell_size, threshold=threshold
    )
    channel = tiers > 0
    if not np.any(channel):
        return dem, tiers

    for _ in range(max(1, int(passes))):
        dist, (iz, ix) = ndimage.distance_transform_edt(~channel, return_indices=True)
        dist = dist * cell_size
        near_w = np.where(channel, half_width, half_width[iz, ix])
        near_d = np.where(channel, depth, depth[iz, ix])
        near_t = np.where(channel, tiers, tiers[iz, ix])
        bank = np.where(channel, dem, dem[iz, ix])

        # per-cell profile exponent: sharp V for torrents, flat alluvial floor for lowland
        expo = np.select([near_t <= 1, near_t == 2, near_t >= 3], [1.10, 1.60, 2.10],
                         default=1.60)
        # lowland channels spread their influence much wider than their water surface
        reach = np.where(near_t >= 3, near_w * float(alluvial_spread), near_w)
        t = np.clip(dist / np.maximum(reach * float(bank_flare), 1e-6), 0.0, 1.0)
        profile = (1.0 - t) ** expo

        sea_guard = sea_level - 6.0
        target_depth = near_d * float(valley_depth_scale)
        floor = np.maximum(bank - target_depth, np.minimum(bank, sea_guard))
        carved = dem + np.minimum(profile * (floor - dem), 0.0)

        # natural levees: a subtle ridge just outside the alluvial floor
        if float(alluvial_spread) > 0:
            levee_band = (near_t >= 3) & (dist > reach) & (dist < reach * 2.1)
            levee = np.where(levee_band, np.minimum(target_depth * 0.16, 1.6), 0.0)
            carved = carved + levee

        carved = np.maximum(carved, np.minimum(dem, sea_guard))
        if lake_mask is not None:
            carved = np.where(lake_mask, dem, carved)
        dem = carved
    return dem, tiers



def carve_footprint(
    dem: np.ndarray,
    mask: np.ndarray,
    half_width: np.ndarray,
    depth: np.ndarray,
    *,
    cell_size: float,
    sea_level: float = 63.0,
    bank_flare: float = 2.4,
    level: Optional[np.ndarray] = None,
    passes: int = 2,
    lake_mask: Optional[np.ndarray] = None,
) -> np.ndarray:
    """Carve an explicit channel footprint into the terrain.

    The accumulation-threshold carver infers its channel from discharge and then hopes
    the flow raster agrees with the geometry.  Once rivers are *shaped* - graded beds and
    migrated meanders - the channel is known exactly, so this carves the rasterised
    footprint directly: every cell in ``mask`` gets a smooth V/U profile of the supplied
    half width down to the supplied depth, and ``level`` (where present) pins the bed to
    the river's own graded elevation so the long profile stays continuous.
    """
    from scipy import ndimage

    dem = np.asarray(dem, dtype=np.float64).copy()
    mask = np.asarray(mask, dtype=bool)
    if not np.any(mask):
        return dem
    half = np.maximum(np.asarray(half_width, dtype=np.float64), 0.5)
    dep = np.maximum(np.asarray(depth, dtype=np.float64), 0.25)

    # One smooth profile carve: repeating it with the same mask is idempotent, and the
    # graded bed is already continuous, so an extra pass would only soften the banks.
    dist, (iz, ix) = ndimage.distance_transform_edt(~mask, return_indices=True)
    dist = dist * float(cell_size)
    local_w = np.where(mask, half, half[iz, ix])
    local_d = np.where(mask, dep, dep[iz, ix])
    bank = np.where(mask, dem, dem[iz, ix])
    t = np.clip(dist / np.maximum(local_w * float(bank_flare), 1e-6), 0.0, 1.0)
    profile = (1.0 - t) ** 1.7
    sea_guard = sea_level - 6.0
    floor = np.maximum(bank - local_d, np.minimum(bank, sea_guard))
    if level is not None:
        pin = np.asarray(level, dtype=np.float64)
        has = np.isfinite(pin)
        floor = np.where(has & mask, np.minimum(floor, pin), floor)
    carved = dem + np.minimum(profile * (floor - dem), 0.0)
    carved = np.maximum(carved, np.minimum(dem, sea_guard))
    if lake_mask is not None:
        carved = np.where(lake_mask, dem, carved)
    _ = passes
    return carved


def dinf_flow_vectors(vec: np.ndarray, *, smooth: int = 1) -> np.ndarray:
    """Smoothed unit vectors from :func:`dinf_flow`, matching ``flow_vectors`` output."""
    vx = np.asarray(vec[..., 0], dtype=np.float64)
    vy = np.asarray(vec[..., 1], dtype=np.float64)
    for _ in range(max(0, int(smooth))):
        for _pass in range(2):
            arr = vx if _pass == 0 else vy
            pad = np.pad(arr, 1, mode="edge")
            blurred = (pad[:-2, 1:-1] + pad[2:, 1:-1] + pad[1:-1, :-2] + pad[1:-1, 2:]) * 0.25
            if _pass == 0:
                vx = 0.5 * vx + 0.5 * blurred
            else:
                vy = 0.5 * vy + 0.5 * blurred
    mag = np.hypot(vx, vy)
    mag = np.where(mag < 1e-9, 1.0, mag)
    return np.stack([vx / mag, vy / mag], axis=-1).astype(np.float32)

def warp_field(field: np.ndarray, *, cell_size: float, seed: int, strength: float = 1.4,
               scale: float = 14.0, octaves: int = 2) -> np.ndarray:
    """Displace a raster along a smooth curl-like field (bilinear resample).

    Rivers routed on a grid are geometrically straight because the router only has eight
    options.  Warping the *discharge field* before it is thresholded into a channel
    footprint bends the resulting channels into smooth curves that follow the terrain
    contours - the same trick that makes procedural rivers look hand drawn instead of
    rasterised.  ``strength`` and ``scale`` are in cells.
    """
    from scipy.ndimage import map_coordinates

    field = np.asarray(field, dtype=np.float64)
    h, w = field.shape
    rows, cols = np.mgrid[0:h, 0:w].astype(np.float64)
    # two decorrelated displacement fields (curl-ish: one shifted from the other)
    warp_x = fbm(cols * cell_size, rows * cell_size, octaves=octaves,
                 scale=scale * cell_size, seed=seed + 61) * strength
    warp_y = fbm(cols * cell_size, rows * cell_size, octaves=octaves,
                 scale=scale * cell_size, seed=seed + 62) * strength
    out = map_coordinates(field, [rows + warp_y, cols + warp_x], order=1, mode="nearest")
    return out


def detect_waterfalls(dem: np.ndarray, rivers: List[RiverPath], *, min_drop: float = 3.0
                      ) -> List[Tuple[float, float, float]]:
    """Return ``(x, z, height)`` knickpoints where a channel drops sharply."""
    out: List[Tuple[float, float, float]] = []
    for river in rivers:
        if len(river.points) < 4:
            continue
        p = river.points
        d = np.diff(p[:, 2])
        seg = np.hypot(np.diff(p[:, 0]), np.diff(p[:, 1]))
        drop = -d / np.maximum(seg, 1e-6)
        for i in np.nonzero(drop > min_drop / 4.0)[0]:
            if -d[i] >= min_drop:
                out.append((float(p[i, 0]), float(p[i, 1]), float(p[i, 2])))
    return out


def flow_summary(dirs: np.ndarray, accum: np.ndarray, cell_size: float, sea_level: float,
                 dem: np.ndarray) -> Dict[str, float]:
    """Quick numbers for the UI diagnostics panel."""
    return {
        "max_discharge_blocks2": float(np.max(accum) * cell_size * cell_size) if accum.size else 0.0,
        "routed_fraction": float(np.count_nonzero(dirs >= 0) / dirs.size) if dirs.size else 0.0,
        "mean_slope": float(np.mean(np.abs(np.diff(dem, axis=0)))) if dem.size else 0.0,
    }
