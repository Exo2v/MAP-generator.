"""Read an exported Ashenfall world back and check it against the specification.

This never consults the generator's own state: it opens the ``.mca`` region files, decodes
the chunks, and compares what is actually on disk with the specification's landmark table.
A pass therefore means the world in the save folder really holds the specified continent.

    python3 -m mg.tools.verify_ashenfall_world ~/.minecraft/saves/Ashenfall

Exit code 0 when every landmark centre lands inside its specified elevation band (with a
generous pad, because the spec's bands are regional targets, not per-block constants).
"""

from __future__ import annotations

import argparse
import gzip
import os
import struct
import sys
import zlib
from collections import Counter
from typing import Dict, Optional, Tuple

import numpy as np

from ..core.materials import block_name
from ..export.nbt import read_nbt
from ..generation.landmarks import CANVAS, HALF, LANDMARKS, SEA_LEVEL
from .inspect_world import decode_chunk_blocks

SECTOR = 4096


def read_chunk(region_dir: str, wx: int, wz: int) -> Tuple[Optional[str], Optional[np.ndarray]]:
    """``(status, blocks)`` for the chunk holding block ``(wx, wz)``, straight from disk."""
    cx, cz = wx >> 4, wz >> 4
    rx, rz = wx >> 9, wz >> 9
    path = os.path.join(region_dir, f"r.{rx}.{rz}.mca")
    if not os.path.isfile(path):
        return None, None
    with open(path, "rb") as fh:
        data = fh.read()
    if len(data) < 8192:
        return None, None
    locations = struct.unpack(">1024I", data[:SECTOR])
    loc = locations[(cx & 31) + (cz & 31) * 32]
    if loc == 0:
        return None, None
    offset = (loc >> 8) * SECTOR
    if offset + 5 > len(data):
        return None, None
    (length,) = struct.unpack(">I", data[offset: offset + 4])
    comp = data[offset + 4]
    payload = data[offset + 5: offset + 4 + length]
    raw = zlib.decompress(payload) if comp in (1, 2) else payload
    root = read_nbt(raw)["value"]
    return root.get("Status", (8, "?"))[1], decode_chunk_blocks(root)


def top_block(blocks: np.ndarray, lx: int, lz: int) -> Tuple[Optional[int], Optional[str]]:
    """Highest non-air block in a column, as ``(world_y, block_name)``."""
    col = blocks[:, lz, lx]
    nz = np.nonzero(col)[0]
    if not nz.size:
        return None, None
    i = int(nz[-1])
    return i - 64, block_name(int(col[i]))


def _level_dat(world_dir: str) -> Dict[str, object]:
    path = os.path.join(world_dir, "level.dat")
    if not os.path.isfile(path):
        return {}
    try:
        with gzip.open(path, "rb") as fh:
            root = read_nbt(fh.read())["value"]
        return root.get("Data", (10, {}))[1]
    except Exception:
        return {}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Verify an exported Ashenfall world.")
    ap.add_argument("world", help="world folder (containing region/)")
    ap.add_argument("--pad", type=float, default=0.25,
                    help="fraction of each landmark's band allowed as tolerance (default 0.25)")
    args = ap.parse_args(argv)

    region_dir = os.path.join(args.world, "region")
    if not os.path.isdir(region_dir):
        print(f"not a world folder: {args.world}", file=sys.stderr)
        return 2

    files = [f for f in os.listdir(region_dir) if f.endswith(".mca")]
    total = sum(os.path.getsize(os.path.join(region_dir, f)) for f in files)
    print(f"world: {args.world}")
    print(f"  region files: {len(files)}  ({total / 1e9:.2f} GB)")

    data = _level_dat(args.world)
    for key in ("SpawnX", "SpawnY", "SpawnZ", "LevelName", "DataVersion"):
        if key in data:
            print(f"  level.dat {key} = {data[key]}")

    print(f"\n{'#':>2}  {'landmark':<30} {'top block':>16} {'Y':>5} {'spec Y':>10} {'':>4}")
    ok_all = True
    statuses: Counter = Counter()
    for lm in LANDMARKS:
        x, z = int(lm.center[0]), int(lm.center[1])
        status, blocks = read_chunk(region_dir, x, z)
        spec = f"{lm.y_min:.0f}-{lm.y_max:.0f}"
        if blocks is None:
            print(f"{lm.index:>2}  {lm.name:<30} {'-':>16} {'-':>5} {spec:>10} "
                  f"{'MISSING':>8}")
            ok_all = False
            continue
        statuses[status] += 1
        y, name = top_block(blocks, x & 15, z & 15)
        pad = (lm.y_max - lm.y_min) * args.pad + 8.0
        good = y is not None and (lm.y_min - pad) <= y <= (lm.y_max + pad)
        ok_all &= bool(good)
        print(f"{lm.index:>2}  {lm.name:<30} {str(name):>16} {y if y is not None else '?':>5} "
              f"{spec:>10} {'ok' if good else 'OFF':>8}")

    if statuses:
        print(f"\n  chunk Status at landmark centres: {dict(statuses)}")
    print(f"  spec canvas {CANVAS:.0f} blocks, half {HALF:.0f}, sea level Y={SEA_LEVEL:.0f}")
    print("ALL LANDMARKS OK" if ok_all else "SOME LANDMARKS OFF - see the table")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
