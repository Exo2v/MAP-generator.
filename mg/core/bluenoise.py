"""Continuous-to-discrete placement without clumping (specification section 3.3).

A suitability field says *how likely* a feature is at a cell - ``P = 0.35`` - but the game
needs a yes/no per block.  Rounding at a threshold produces giant blobs and dead voids;
uniform white noise produces visible clumps and single-pixel salt-and-pepper.  The fix is
a **blue-noise** threshold matrix: a tileable array whose values are spread so that any
thresholded subset is evenly distributed.

Two placement methods, matching the specification:

* :func:`void_and_cluster` / :func:`continuous_to_discrete` - Method A, for surface covers
  (grass, flowers, snow crust, scree).  Cheap, tileable, no grid repetition thanks to a
  per-tile hash rotation.
* :func:`variable_radius_poisson` - Method B, Bridson-style scattering with a radius that
  varies with local density, for objects with real bounding boxes (custom trees, boulders).

The void-and-cluster matrix is generated with Ulichney's algorithm: start from a sparse
random binary pattern, repeatedly move the point in the tightest *cluster* into the
largest *void* until it is stable, then rank the points by the order in which they were
removed.  Gaussian filtering finds clusters and voids the same way the eye does.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

import numpy as np
from scipy.ndimage import gaussian_filter

# --------------------------------------------------------------------------------------
# Method A - void-and-cluster blue noise
# --------------------------------------------------------------------------------------


def void_and_cluster(size: int = 64, *, seed: int = 0, sigma: float = 1.5,
                     initial_fraction: float = 0.1, max_iterations: int = 64,
                     cache: Optional[Dict[Tuple[int, int], np.ndarray]] = None
                     ) -> np.ndarray:
    """Deterministic ``size x size`` blue-noise threshold matrix in ``[0, 1)``.

    Ullichney's void-and-cluster: relax a sparse binary pattern by moving the point in
    the tightest cluster into the largest void, then rank points by removal order.
    Returns float32 values so it can be compared directly against a suitability field.
    """
    if cache is not None and (size, seed) in cache:
        return cache[(size, seed)]
    rng = np.random.default_rng(int(seed) & 0xFFFFFFFF)
    n = int(size) * int(size)
    n_ones = max(1, int(round(n * float(np.clip(initial_fraction, 0.01, 0.5)))))
    pattern = np.zeros(n, dtype=bool)
    pattern[rng.choice(n, size=n_ones, replace=False)] = True

    def density(binary: np.ndarray) -> np.ndarray:
        field = binary.reshape(size, size).astype(np.float64)
        out = gaussian_filter(field, sigma=sigma, mode="wrap")
        return out.ravel()

    # relax: move the tightest cluster into the largest void
    for _ in range(max_iterations):
        field = density(pattern)
        ones = np.flatnonzero(pattern)
        zeros = np.flatnonzero(~pattern)
        if ones.size == 0 or zeros.size == 0:
            break
        tightest = ones[int(np.argmax(field[ones]))]
        largest_void = zeros[int(np.argmin(field[zeros]))]
        if field[tightest] <= field[largest_void]:
            break
        pattern[tightest] = False
        pattern[largest_void] = True

    # rank by removal order: take the tightest cluster out repeatedly, and rank those
    # points above half; then fill voids and rank those below half
    rank = np.zeros(n, dtype=np.float64)
    working = pattern.copy()
    count = int(working.sum())
    for r in range(count - 1, -1, -1):
        field = density(working)
        ones = np.flatnonzero(working)
        if ones.size == 0:
            break
        tightest = ones[int(np.argmax(field[ones]))]
        rank[tightest] = r
        working[tightest] = False

    working = pattern.copy()
    count_ones = int(pattern.sum())
    total = n
    for r in range(count_ones, total):
        field = density(working)
        zeros = np.flatnonzero(~working)
        if zeros.size == 0:
            break
        void = zeros[int(np.argmin(field[zeros]))]
        rank[void] = r
        working[void] = True

    matrix = (rank / max(total - 1, 1)).reshape(size, size).astype(np.float32)
    if cache is not None:
        cache[(size, seed)] = matrix
    return matrix


class BlueNoiseMatrix:
    """Tileable blue-noise thresholds with a per-tile rotation.

    A plain ``np.tile`` of one matrix would repeat visibly across a continent, so each
    tile is rolled by a deterministic offset derived from the tile coordinates.  Rolling
    preserves the blue-noise spectrum (it is a cyclic shift), but the seams between tiles
    no longer line up, so the eye cannot find the period.
    """

    def __init__(self, size: int = 64, seed: int = 0):
        self.size = int(size)
        self.seed = int(seed)
        self._cache: Dict[Tuple[int, int], np.ndarray] = {}
        self.matrix = void_and_cluster(self.size, seed=self.seed, cache=self._cache)

    def tiled(self, shape: Tuple[int, int], *, offset: Tuple[int, int] = (0, 0)) -> np.ndarray:
        """A ``shape``-sized threshold field, seam-jittered per tile.

        ``offset`` is the world cell coordinate of the top-left sample, so a mask can be
        generated for a sub-region (or a world centred on the origin, where coordinates
        are negative) and still line up with the masks generated for its neighbours.  The
        per-tile roll is hashed from the *absolute* tile index, so it is stable regardless
        of where the window starts.
        """
        h, w = int(shape[0]), int(shape[1])
        s = self.size
        oy, ox = int(offset[0]), int(offset[1])
        out = np.empty((h, w), dtype=np.float32)
        ty0, tx0 = oy // s, ox // s
        ty1 = (oy + h - 1) // s
        tx1 = (ox + w - 1) // s
        for ty in range(ty0, ty1 + 1):
            for tx in range(tx0, tx1 + 1):
                hsh = (ty * 73856093) ^ (tx * 19349663) ^ (self.seed * 83492791)
                ry = (hsh >> 8) % s
                rx = (hsh >> 16) % s
                tile = np.roll(np.roll(self.matrix, ry, axis=0), rx, axis=1)
                # overlap of this tile with the requested window
                y0 = ty * s - oy
                x0 = tx * s - ox
                sy0, sx0 = max(0, -y0), max(0, -x0)
                dy0, dx0 = max(0, y0), max(0, x0)
                dy1 = min(h, y0 + s)
                dx1 = min(w, x0 + s)
                if dy0 >= dy1 or dx0 >= dx1:
                    continue
                out[dy0:dy1, dx0:dx1] = tile[sy0:sy0 + (dy1 - dy0), sx0:sx0 + (dx1 - dx0)]
        return out

    def threshold(self, probability: np.ndarray, *, offset: Tuple[int, int] = (0, 0),
                  ) -> np.ndarray:
        """Binary mask ``probability >= threshold`` - the placement decision."""
        p = np.clip(np.asarray(probability, dtype=np.float64), 0.0, 1.0)
        thr = self.tiled(p.shape, offset=offset)
        return (p >= thr).astype(np.uint8)

    def mask8(self, probability: np.ndarray, *, offset: Tuple[int, int] = (0, 0),
              scale: float = 255.0) -> np.ndarray:
        """The same decision scaled to ``0 / 255`` - the 8-bit mask WorldPainter wants."""
        return (self.threshold(probability, offset=offset).astype(np.uint8) * int(scale))


def continuous_to_discrete(probability_field: np.ndarray, threshold_matrix: np.ndarray,
                           *, offset: Tuple[int, int] = (0, 0)) -> np.ndarray:
    """Module-level form of the specification's function, for callers with their own matrix."""
    p = np.clip(np.asarray(probability_field, dtype=np.float64), 0.0, 1.0)
    ty, tx = threshold_matrix.shape
    ny, nx = p.shape
    if ty != ny or tx != nx:
        reps_y = ny // ty + 1
        reps_x = nx // tx + 1
        tiled = np.tile(threshold_matrix, (reps_y, reps_x))[:ny, :nx]
    else:
        tiled = threshold_matrix
    return np.where(p >= tiled, 255, 0).astype(np.uint8)


# --------------------------------------------------------------------------------------
# Method B - variable-radius Poisson disk
# --------------------------------------------------------------------------------------


def variable_radius_poisson(probability: np.ndarray, *, cell_size: float = 4.0,
                            max_density: float = 1.0, base_radius_cells: float = 2.0,
                            seed: int = 0, max_points: int = 200000,
                            ) -> np.ndarray:
    """Bridson-style dart throwing with a density-driven radius (spec 3.3 Method B).

    ``r(x, z) = r_min / sqrt(rho(x, z))`` with ``rho`` the local target density, so open
    meadow gets dense placement and a lone tree on a ridge stays lonely.  Returns an
    ``(n, 3)`` array of ``cell_x, cell_z, local_probability`` - the caller decides what to
    put at each accepted point.
    """
    p = np.clip(np.asarray(probability, dtype=np.float64), 0.0, 1.0)
    h, w = p.shape
    rng = np.random.default_rng(int(seed) & 0xFFFFFFFF)
    rmin = max(float(base_radius_cells), 0.5)
    # spatial hash: bucket -> points, so radius tests only look at nearby buckets
    cell = rmin
    gw = max(1, int(w / cell) + 1)
    gh = max(1, int(h / cell) + 1)
    grid: Dict[Tuple[int, int], list] = {}
    accepted: list = []

    def ok(x: float, z: float, r: float) -> bool:
        gx, gz = int(x / cell), int(z / cell)
        span = int(np.ceil(r / cell))
        for jz in range(gz - span, gz + span + 1):
            for jx in range(gx - span, gx + span + 1):
                for (px, pz) in grid.get((jx, jz), ()):  # noqa: E501
                    if (px - x) ** 2 + (pz - z) ** 2 < r * r:
                        return False
        return True

    active: list = []

    def spawn(x: float, z: float) -> None:
        gx, gz = int(x / cell), int(z / cell)
        grid.setdefault((gx, gz), []).append((x, z))
        accepted.append((int(x), int(z), float(p[int(z), int(x)])))
        active.append((x, z))

    # seed with the strongest candidate in a few random cells
    for _ in range(min(64, max(1, h * w // 400))):
        z = int(rng.integers(0, h))
        x = int(rng.integers(0, w))
        if p[z, x] > 1e-6 and ok(x, z, 0.0):
            spawn(x, z)

    while active and len(accepted) < max_points:
        idx = int(rng.integers(0, len(active)))
        x, z = active[idx]
        rho = float(p[int(z), int(x)]) * float(max_density)
        r = rmin / max(np.sqrt(max(rho, 1e-6)), 1e-6)
        r = float(np.clip(r, rmin, rmin * 40.0))
        placed = False
        for _ in range(12):
            ang = rng.random() * 2.0 * np.pi
            rad = r * (1.0 + rng.random())
            nx = x + np.cos(ang) * rad
            nz = z + np.sin(ang) * rad
            if not (0 <= nx < w and 0 <= nz < h):
                continue
            if p[int(nz), int(nx)] <= 1e-6:
                continue
            if ok(nx, nz, r):
                spawn(nx, nz)
                placed = True
                break
        if not placed:
            active.pop(idx)

    if not accepted:
        return np.zeros((0, 3), dtype=np.int64)
    arr = np.asarray(accepted, dtype=np.float64)
    arr[:, 0] = arr[:, 0] // max(cell_size, 1e-6)   # cell coordinates -> block offset
    arr[:, 1] = arr[:, 1] // max(cell_size, 1e-6)
    return arr.astype(np.int64)
