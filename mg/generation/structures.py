"""Plants and buildings: turning population data into actual blocks.

Two stampers:

* :func:`stamp_vegetation` - vectorised.  Every tree/shrub/flower species has a voxel
  template built once; all instances of a species inside a chunk are stamped in a
  single numpy scatter.  That is what keeps a 4 km² export with a few hundred thousand
  plants in the minutes range instead of hours.
* :func:`stamp_structure` - plain Python, called a handful of times per region, builds
  villages, hamlets, outposts, ruins, ports and landmarks out of ordinary vanilla
  blocks (no mod dependency).
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from ..core.materials import AIR, BLOCK_ID
from ..core.types import MAX_Y, MIN_Y

WORLD_HEIGHT = MAX_Y - MIN_Y

# --------------------------------------------------------------------------------------
# Species tables
# --------------------------------------------------------------------------------------

#: Every plant the population layer can emit, in template order.
SPECIES: List[str] = [
    "oak", "birch", "spruce", "pine", "jungle", "acacia", "dark_oak", "mangrove", "palm",
    "shrub", "cactus", "dead_bush", "grass", "dry_grass", "fern", "flowers", "lily",
    "bamboo", "seagrass", "sweet_berry", "mushroom_brown", "mushroom_red",
]
SPECIES_ID: Dict[str, int] = {s: i for i, s in enumerate(SPECIES)}

#: Template recipes: (log, leaves, trunk_min, trunk_max, canopy_radius, style)
TREE_RECIPES: Dict[str, dict] = {
    "oak": dict(log="oak_log", leaves="oak_leaves", trunk=(4, 6), radius=2, style="blob"),
    "birch": dict(log="birch_log", leaves="birch_leaves", trunk=(5, 7), radius=2, style="blob"),
    "spruce": dict(log="spruce_log", leaves="spruce_leaves", trunk=(7, 13), radius=2,
                   style="conifer"),
    "pine": dict(log="spruce_log", leaves="spruce_leaves", trunk=(9, 16), radius=2,
                 style="conifer"),
    "jungle": dict(log="jungle_log", leaves="jungle_leaves", trunk=(10, 20), radius=3,
                   style="jungle"),
    "acacia": dict(log="acacia_log", leaves="acacia_leaves", trunk=(4, 6), radius=3,
                   style="acacia"),
    "dark_oak": dict(log="dark_oak_log", leaves="dark_oak_leaves", trunk=(5, 8), radius=3,
                     style="blob"),
    "mangrove": dict(log="mangrove_log", leaves="mangrove_leaves", trunk=(4, 7), radius=2,
                     style="mangrove"),
    "palm": dict(log="jungle_log", leaves="jungle_leaves", trunk=(5, 9), radius=2,
                 style="palm"),
}

SIMPLE_RECIPES: Dict[str, dict] = {
    "shrub": dict(blocks=["oak_leaves"], height=(1, 2)),
    "cactus": dict(blocks=["cactus"], height=(1, 3)),
    "dead_bush": dict(blocks=["dead_bush"], height=(1, 1)),
    "grass": dict(blocks=["grass"], height=(1, 1)),
    "dry_grass": dict(blocks=["dead_bush"], height=(1, 1)),
    "fern": dict(blocks=["fern", "large_fern"], height=(1, 1)),
    "flowers": dict(blocks=["dandelion", "poppy", "cornflower", "oxeye_daisy", "azure_bluet",
                            "allium", "blue_orchid", "lily_of_the_valley"], height=(1, 1)),
    "lily": dict(blocks=["lily_pad"], height=(1, 1)),
    "bamboo": dict(blocks=["bamboo"], height=(3, 7)),
    "seagrass": dict(blocks=["seagrass"], height=(1, 1)),
    "sweet_berry": dict(blocks=["sweet_berry_bush"], height=(1, 1)),
    "mushroom_brown": dict(blocks=["brown_mushroom"], height=(1, 1)),
    "mushroom_red": dict(blocks=["red_mushroom"], height=(1, 1)),
}


def _species_index(name: str) -> int:
    if name in SPECIES_ID:
        return SPECIES_ID[name]
    aliases = {
        "dark_oak_tree": "dark_oak", "jungle_tree": "jungle", "oak_tree": "oak",
        "spruce_tree": "spruce", "palm_tree": "palm", "bush": "shrub", "flower": "flowers",
    }
    return SPECIES_ID.get(aliases.get(name, ""), SPECIES_ID["oak"])


# --------------------------------------------------------------------------------------
# Voxel templates
# --------------------------------------------------------------------------------------


class TemplateCache:
    """Builds and caches (offsets, block_ids) voxel templates per species and size."""

    def __init__(self):
        self._cache: Dict[Tuple[str, int, int], Tuple[np.ndarray, np.ndarray]] = {}

    def get(self, species: str, seed_a: int, seed_b: int
            ) -> Tuple[np.ndarray, np.ndarray]:
        key = (species, seed_a % 4, seed_b % 4)
        if key in self._cache:
            return self._cache[key]
        template = self._build(species, seed_a, seed_b)
        self._cache[key] = template
        return template

    # ----------------------------------------------------------------------------------
    def _build(self, species: str, seed_a: int, seed_b: int) -> Tuple[np.ndarray, np.ndarray]:
        rng = np.random.default_rng((abs(hash(species)) + seed_a * 7919 + seed_b * 104729) % 2**31)
        offsets: List[Tuple[int, int, int]] = []
        ids: List[int] = []

        if species in TREE_RECIPES:
            r = TREE_RECIPES[species]
            trunk_h = int(rng.integers(r["trunk"][0], r["trunk"][1] + 1))
            log = BLOCK_ID[r["log"]]
            leaf = BLOCK_ID[r["leaves"]]
            style = r["style"]
            radius = r["radius"]

            if style == "conifer":
                for y in range(trunk_h):
                    offsets.append((0, y, 0))
                    ids.append(log)
                layer = 0
                for y in range(2, trunk_h + 1, 1):
                    layer_r = max(0, radius - (y * 2 // (trunk_h + 1)))
                    if (y - 2) % 2 == 0:
                        layer_r = min(radius, layer_r + 1)
                    for dx in range(-layer_r, layer_r + 1):
                        for dz in range(-layer_r, layer_r + 1):
                            if abs(dx) + abs(dz) > layer_r + 1:
                                continue
                            if dx == 0 and dz == 0 and y < trunk_h:
                                continue
                            offsets.append((dx, y, dz))
                            ids.append(leaf)
                    layer += 1
                # tip
                for dx, dz in ((0, 0), (1, 0), (0, 1), (-1, 0), (0, -1)):
                    offsets.append((dx, trunk_h, dz))
                    ids.append(leaf)
            elif style == "blob":
                for y in range(trunk_h):
                    offsets.append((0, y, 0))
                    ids.append(log)
                cy = trunk_h + 1
                for dy in range(-1, radius + 2):
                    rr = radius - abs(dy - 1) * 0.7
                    rr = max(0.6, rr)
                    for dx in range(-radius, radius + 1):
                        for dz in range(-radius, radius + 1):
                            if dx * dx + dz * dz <= rr * rr:
                                offsets.append((dx, cy + dy, dz))
                                ids.append(leaf)
            elif style == "jungle":
                for y in range(trunk_h):
                    offsets.append((0, y, 0))
                    ids.append(log)
                for dy in (0, 1, 2):
                    rr = max(1.0, radius - dy * 0.8)
                    for dx in range(-radius, radius + 1):
                        for dz in range(-radius, radius + 1):
                            if dx * dx + dz * dz <= rr * rr:
                                offsets.append((dx, trunk_h + dy, dz))
                                ids.append(leaf)
                # vines hanging from the canopy edge
                vine = BLOCK_ID["vine"]
                for _ in range(int(rng.integers(0, 6))):
                    vx = int(rng.integers(-radius, radius + 1))
                    vz = int(rng.integers(-radius, radius + 1))
                    for vy in range(1, int(rng.integers(2, 6))):
                        offsets.append((vx, trunk_h - vy, vz))
                        ids.append(vine)
            elif style == "acacia":
                bend = 2
                for y in range(trunk_h):
                    offsets.append((0 if y < trunk_h - 2 else bend, y, 0))
                    ids.append(log)
                cy = trunk_h
                for dx in range(-radius, radius + 1):
                    for dz in range(-radius, radius + 1):
                        if abs(dx) + abs(dz) <= radius + 1:
                            offsets.append((bend + dx, cy, dz))
                            ids.append(leaf)
                for dx, dz in ((0, 0), (2, 1), (-2, 1), (1, -2)):
                    offsets.append((bend + dx, cy + 1, dz))
                    ids.append(leaf)
            elif style == "mangrove":
                # stilt roots
                root = BLOCK_ID["mangrove_log"]
                for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    offsets.append((dx, -1, dz))
                    ids.append(root)
                for y in range(trunk_h):
                    offsets.append((0, y, 0))
                    ids.append(log)
                for dy in range(0, radius + 1):
                    rr = radius - dy * 0.6
                    for dx in range(-radius, radius + 1):
                        for dz in range(-radius, radius + 1):
                            if dx * dx + dz * dz <= max(rr, 0.8) ** 2:
                                offsets.append((dx, trunk_h + dy, dz))
                                ids.append(leaf)
            elif style == "palm":
                for y in range(trunk_h):
                    offsets.append((0, y, 0))
                    ids.append(log)
                for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    offsets.append((dx, trunk_h, dz))
                    ids.append(leaf)
                    offsets.append((dx * 2, trunk_h - 1, dz * 2))
                    ids.append(leaf)
                offsets.append((0, trunk_h + 1, 0))
                ids.append(leaf)
        else:
            r = SIMPLE_RECIPES.get(species, SIMPLE_RECIPES["grass"])
            h = int(rng.integers(r["height"][0], r["height"][1] + 1))
            blocks = [BLOCK_ID[b] for b in r["blocks"]]
            chosen = blocks[int(rng.integers(0, len(blocks)))]
            for y in range(h):
                offsets.append((0, y, 0))
                ids.append(chosen)

        off = np.asarray(offsets, dtype=np.int32)
        bid = np.asarray(ids, dtype=np.uint16)
        return off, bid


_TEMPLATES = TemplateCache()


# --------------------------------------------------------------------------------------
# Vectorised vegetation stamping
# --------------------------------------------------------------------------------------


def stamp_vegetation(
    blocks: np.ndarray,
    *,
    vegetation: np.ndarray,  # (n, 5) int32: cell_x, cell_z, species_id, height, kind
    cell_size: float,
    region_x0: int,
    region_z0: int,
    chunk_x0: int,
    chunk_z0: int,
    ground: np.ndarray,
    seed: int,
) -> np.ndarray:
    """Stamp every plant whose position falls inside this chunk.

    ``vegetation`` is sorted by ``(cell_z, cell_x)`` so the lookup is a slice plus a
    filter rather than a full scan - that is what keeps decoration cheap across
    thousands of chunks.
    """
    if vegetation is None or len(vegetation) == 0:
        return blocks
    cs = float(cell_size)
    n = len(vegetation)
    # slice out the cells that could touch this chunk (one cell of margin)
    z_lo_cell = int(np.floor((chunk_z0 - region_z0) / cs)) - 1
    z_hi_cell = int(np.ceil((chunk_z0 + 16 - region_z0) / cs)) + 1
    lo = int(np.searchsorted(vegetation[:, 1], z_lo_cell, side="left"))
    hi = int(np.searchsorted(vegetation[:, 1], z_hi_cell, side="right"))
    if hi <= lo:
        return blocks
    sub = vegetation[lo:hi]
    x_lo_cell = int(np.floor((chunk_x0 - region_x0) / cs)) - 1
    x_hi_cell = int(np.ceil((chunk_x0 + 16 - region_x0) / cs)) + 1
    keep = (sub[:, 0] >= x_lo_cell) & (sub[:, 0] < x_hi_cell)
    if not np.any(keep):
        return blocks
    sub = sub[keep]

    vx = region_x0 + (sub[:, 0].astype(np.float64) + 0.5) * cs
    vz = region_z0 + (sub[:, 1].astype(np.float64) + 0.5) * cs
    lx = np.floor(vx - chunk_x0).astype(np.int64)
    lz = np.floor(vz - chunk_z0).astype(np.int64)
    inside = (lx >= 0) & (lx < 16) & (lz >= 0) & (lz < 16)
    if not np.any(inside):
        return blocks
    lx, lz = lx[inside], lz[inside]
    species_ids = sub[inside, 2]
    heights = sub[inside, 3]
    gy = ground[lz, lx].astype(np.int64)

    flat = blocks.reshape(-1)
    replaceable = {
        AIR, BLOCK_ID["snow"], BLOCK_ID["grass"], BLOCK_ID["fern"], BLOCK_ID["dead_bush"],
        BLOCK_ID["sand"], BLOCK_ID["water"], BLOCK_ID["ice"],
    }

    for sid in np.unique(species_ids):
        mask = species_ids == sid
        name = SPECIES[int(sid)] if 0 <= int(sid) < len(SPECIES) else "shrub"
        ts = np.nonzero(mask)[0]
        # two small hash buckets keep templates varied without exploding the cache
        a = ((ts * 2654435761) >> 13) & 3
        b = ((ts * 40503) >> 7) & 3
        for ka in np.unique(a):
            for kb in np.unique(b):
                m = (a == ka) & (b == kb)
                if not np.any(m):
                    continue
                sel = ts[m]
                offs, bids = _TEMPLATES.get(name, int(ka), int(kb))
                for k in range(len(bids)):
                    dx = int(offs[k, 0])
                    dy = int(offs[k, 1])
                    dz = int(offs[k, 2])
                    x = lx[sel] + dx
                    z = lz[sel] + dz
                    y = gy[sel] + dy
                    ok = (x >= 0) & (x < 16) & (z >= 0) & (z < 16) & (y >= MIN_Y) & (y < MAX_Y)
                    if not np.any(ok):
                        continue
                    xi, yi, zi = x[ok], y[ok], z[ok]
                    fidx = yi * 256 + zi * 16 + xi
                    cur = flat[fidx]
                    writable = np.fromiter(
                        (int(c) in replaceable for c in cur), dtype=bool, count=len(cur)
                    )
                    if np.any(writable):
                        flat[fidx[writable]] = bids[k]
    return blocks


# --------------------------------------------------------------------------------------
# Structures
# --------------------------------------------------------------------------------------


def _place(blocks: np.ndarray, lx: int, lz: int, y: int, block: str, *, overwrite=True):
    if not (0 <= lx < 16 and 0 <= lz < 16 and MIN_Y <= y < MAX_Y):
        return
    yi = y - MIN_Y
    if not overwrite and blocks[yi, lz, lx] != AIR:
        return
    blocks[yi, lz, lx] = BLOCK_ID.get(block, BLOCK_ID["stone"])


def _box(blocks, x0, z0, y, w, d, block, *, hollow=False, overwrite=True):
    for dx in range(w):
        for dz in range(d):
            edge = dx in (0, w - 1) or dz in (0, d - 1)
            if hollow and not edge:
                continue
            _place(blocks, x0 + dx, z0 + dz, y, block, overwrite=overwrite)


def _foundation(blocks, ground, x0, z0, w, d, mat="cobblestone"):
    """Level a footprint: fill dips with foundation, carve the peaks away."""
    ys = [
        int(ground[z, x])
        for z in range(max(0, z0), min(16, z0 + d))
        for x in range(max(0, x0), min(16, x0 + w))
    ]
    if not ys:
        return None
    base = int(np.median(ys))
    for z in range(max(0, z0), min(16, z0 + d)):
        for x in range(max(0, x0), min(16, x0 + w)):
            gy = int(ground[z, x])
            for y in range(base, gy):
                _place(blocks, x, z, y, AIR)
            for y in range(gy + 1, base + 1):
                _place(blocks, x, z, y, mat)
    return base


def _house(blocks, ground, rng, x0, z0, *, w=None, d=None):
    w = w or int(rng.integers(5, 8))
    d = d or int(rng.integers(5, 8))
    base = _foundation(blocks, ground, x0, z0, w, d)
    if base is None:
        return
    wall = str(rng.choice(["oak_planks", "spruce_planks", "dark_oak_planks", "cobblestone"]))
    floor = str(rng.choice(["oak_planks", "spruce_planks", "cobblestone"]))
    height = int(rng.integers(3, 5))
    _box(blocks, x0, z0, base, w, d, floor)
    for h in range(1, height + 1):
        _box(blocks, x0, z0, base + h, w, d, wall, hollow=True)
    # door on a random side
    side = int(rng.integers(0, 4))
    door = "oak_door"
    if side == 0:
        _place(blocks, x0 + w // 2, z0, base + 1, door)
        _place(blocks, x0 + w // 2, z0, base + 2, door)
    elif side == 1:
        _place(blocks, x0 + w // 2, z0 + d - 1, base + 1, door)
        _place(blocks, x0 + w // 2, z0 + d - 1, base + 2, door)
    elif side == 2:
        _place(blocks, x0, z0 + d // 2, base + 1, door)
        _place(blocks, x0, z0 + d // 2, base + 2, door)
    else:
        _place(blocks, x0 + w - 1, z0 + d // 2, base + 1, door)
        _place(blocks, x0 + w - 1, z0 + d // 2, base + 2, door)
    # windows
    for dx in range(1, w - 1):
        if dx != w // 2:
            _place(blocks, x0 + dx, z0, base + 2, "glass_pane")
            _place(blocks, x0 + dx, z0 + d - 1, base + 2, "glass_pane")
    # roof
    for i in range(max(w, d) // 2 + 1):
        _box(blocks, x0 + i, z0 + i, base + height + i, max(1, w - 2 * i),
             max(1, d - 2 * i), "spruce_stairs" if i % 2 == 0 else "dark_oak_planks")
    if rng.random() < 0.7:
        _place(blocks, x0 + 1, z0 + 1, base + height, "campfire")
    if rng.random() < 0.5:
        _place(blocks, x0 + w - 2, z0 + d - 2, base + 1, "barrel")
    if rng.random() < 0.4:
        _place(blocks, x0 + 1, z0 + d - 2, base + 1, "crafting_table")


def _path(blocks, ground, x0, z0, x1, z1):
    """A trodden dirt path between two points inside the chunk."""
    steps = max(abs(x1 - x0), abs(z1 - z0), 1)
    for i in range(steps + 1):
        x = int(round(x0 + (x1 - x0) * i / steps))
        z = int(round(z0 + (z1 - z0) * i / steps))
        if not (0 <= x < 16 and 0 <= z < 16):
            continue
        gy = int(ground[z, x])
        if blocks[gy - MIN_Y + 1, z, x] != AIR:
            continue
        _place(blocks, x, z, gy, "coarse_dirt")


def stamp_structure(
    blocks: np.ndarray,
    ground: np.ndarray,
    *,
    kind: str,
    local_x: int,
    local_z: int,
    seed: int,
    biome: str = "",
    near_water: bool = False,
) -> None:
    """Build one settlement / ruin / landmark at a local chunk position."""
    rng = np.random.default_rng(seed)
    lx = int(np.clip(local_x, 2, 13))
    lz = int(np.clip(local_z, 2, 13))

    if kind in ("village", "hamlet"):
        houses = 3 if kind == "village" else 2
        positions = []
        for _ in range(houses):
            ox = int(np.clip(lx + int(rng.integers(-4, 5)), 1, 10))
            oz = int(np.clip(lz + int(rng.integers(-4, 5)), 1, 10))
            positions.append((ox, oz))
        for ox, oz in positions:
            _house(blocks, ground, rng, ox, oz, w=int(rng.integers(5, 8)),
                   d=int(rng.integers(5, 8)))
        for i in range(len(positions) - 1):
            _path(blocks, ground, positions[i][0] + 2, positions[i][1] + 2,
                  positions[i + 1][0] + 2, positions[i + 1][1] + 2)
        if kind == "village" and rng.random() < 0.6:
            _place(blocks, lx, lz, int(ground[lz, lx]) + 1, "bell")
        if rng.random() < 0.5:
            # a small farm plot
            fx = int(np.clip(lx + int(rng.integers(-5, 6)), 1, 11))
            fz = int(np.clip(lz + int(rng.integers(-5, 6)), 1, 11))
            for dx in range(3):
                for dz in range(3):
                    gy = int(ground[min(15, fz + dz), min(15, fx + dx)])
                    _place(blocks, fx + dx, fz + dz, gy, "coarse_dirt")
                    if (dx + dz) % 2 == 0:
                        _place(blocks, fx + dx, fz + dz, gy + 1, "grass")
    elif kind == "outpost":
        base = _foundation(blocks, ground, lx, lz, 5, 5)
        if base is not None:
            for y in range(1, 9):
                _box(blocks, lx, lz, base + y, 5, 5, "cobblestone", hollow=True)
                if y % 3 == 0:
                    _box(blocks, lx, lz, base + y, 5, 5, "oak_planks")
            for dx, dz in ((0, 0), (4, 0), (0, 4), (4, 4)):
                for y in range(9, 11):
                    _place(blocks, lx + dx, lz + dz, base + y, "oak_fence")
            _place(blocks, lx + 2, lz + 2, base + 10, "lantern")
            _place(blocks, lx + 1, lz + 1, base + 1, "chest")
    elif kind == "ruin":
        base = _foundation(blocks, ground, lx, lz, 7, 7)
        if base is not None:
            for dx in range(7):
                for dz in range(7):
                    edge = dx in (0, 6) or dz in (0, 6)
                    if not edge:
                        continue
                    h = int(rng.integers(0, 4))
                    for y in range(1, h + 1):
                        mat = "mossy_cobblestone" if rng.random() < 0.5 else "cobblestone"
                        _place(blocks, lx + dx, lz + dz, base + y, mat)
            _place(blocks, lx + 3, lz + 3, base + 1, "cobweb")
    elif kind == "landmark":
        base = _foundation(blocks, ground, lx, lz, 3, 3)
        if base is not None:
            height = int(rng.integers(6, 12))
            for y in range(1, height):
                _place(blocks, lx + 1, lz + 1, base + y, "stone_bricks")
                if y % 3 == 1:
                    for dx, dz in ((0, 0), (2, 0), (0, 2), (2, 2)):
                        _place(blocks, lx + dx, lz + dz, base + y, "mossy_stone_bricks")
            _place(blocks, lx + 1, lz + 1, base + height, "lantern")
    elif kind == "port":
        # pier running toward the water plus a storehouse
        _house(blocks, ground, rng, lx, lz, w=6, d=6)
        dz = 1 if near_water else -1
        for i in range(1, 9):
            z = int(np.clip(lz + 3 + dz * i, 0, 15))
            x = lx + 2
            gy = int(ground[z, x])
            for dx in range(3):
                _place(blocks, x + dx, z, gy + 1, "oak_planks")
                _place(blocks, x + dx, z, gy, "oak_fence")
            if i % 3 == 0:
                _place(blocks, x + 1, z, gy + 2, "lantern")
    elif kind == "camp":
        base = _foundation(blocks, ground, lx, lz, 4, 4)
        if base is not None:
            _place(blocks, lx + 2, lz + 2, base + 1, "campfire")
            _place(blocks, lx, lz, base + 1, "barrel")


def structure_offsets(poi_x: float, poi_z: float, chunk_x0: int, chunk_z0: int) -> Tuple[int, int]:
    """Local chunk coordinates of a POI (may fall outside the 16x16 chunk)."""
    return int(round(poi_x - chunk_x0)), int(round(poi_z - chunk_z0))
