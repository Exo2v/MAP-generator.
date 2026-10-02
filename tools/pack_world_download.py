#!/usr/bin/env python3
"""Package an exported Minecraft save for release on GitHub.

GitHub refuses files larger than 100 MB, and a full 8,000 x 8,000 continent is far
bigger than that.  This tool splits the save into shards that every host accepts:

* ``region/*.mca`` files are grouped by coordinate, so each shard holds whole region
  files and the shards can simply be extracted on top of each other,
* the first shard also carries the save's metadata (``level.dat``, ``mapgen.json``,
  ``maps/``, ``worldpainter/``, ``README.txt``),
* ``session.lock`` and ``level.dat_old`` are dropped - the game recreates them and a
  stale lock only confuses the launcher.

The shards are ordinary ZIP files: downloading all of them and extracting them into
one folder (``.minecraft/saves/Ashenfall``) reconstructs the world exactly.

    python tools/pack_world_download.py out/ashenfall/Ashenfall \
        --out release/v1.1.0 --parts 4 --version v1.1.0
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import zipfile
from typing import Dict, List, Sequence, Tuple

REGION_RE = re.compile(r"^r\.(-?\d+)\.(-?\d+)\.mca$")
SKIP = {"session.lock", "level.dat_old"}


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def region_sort_key(path: str) -> Tuple[int, int]:
    match = REGION_RE.match(os.path.basename(path))
    if not match:
        return (1 << 30, 1 << 30)
    return (int(match.group(1)), int(match.group(2)))


def split_regions(region_files: Sequence[str], parts: int) -> List[List[str]]:
    """Balance whole region files across shards, keeping the coordinate order."""
    files = sorted(region_files, key=region_sort_key)
    total = sum(os.path.getsize(p) for p in files)
    target = total / max(parts, 1)
    groups: List[List[str]] = [[] for _ in range(parts)]
    running = [0] * parts
    index = 0
    for path in files:
        # fill shard n until it reaches its share, then move on
        while index < parts - 1 and running[index] >= target:
            index += 1
        groups[index].append(path)
        running[index] += os.path.getsize(path)
    return [g for g in groups if g]


def metadata_files(world_dir: str) -> List[str]:
    """Everything that is not a region file, minus the throwaway ones."""
    out: List[str] = []
    for root, _dirs, names in os.walk(world_dir):
        for name in sorted(names):
            if name in SKIP:
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, world_dir)
            if rel.startswith("region" + os.sep):
                continue
            if rel.startswith("schematic" + os.sep):          # optional heavy extras
                continue
            if os.path.getsize(path) > 32 << 20:
                continue
            out.append(path)
    return out


def write_part(path: str, world_dir: str, files: Sequence[str], *,
               level: int = 6) -> int:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=level) as z:
        for src in files:
            z.write(src, os.path.relpath(src, world_dir))
    return os.path.getsize(path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pack_world_download.py",
                                 description="Split an exported save into GitHub-safe shards.")
    ap.add_argument("world", help="the save directory (holds level.dat and region/)")
    ap.add_argument("--out", required=True, help="where the shards are written")
    ap.add_argument("--parts", type=int, default=4, help="how many shards (default 4)")
    ap.add_argument("--max-mb", type=float, default=95.0,
                    help="fail if a shard exceeds this size (default 95 MB, GitHub caps at 100)")
    ap.add_argument("--prefix", default="ASHENFALL_MinecraftWorld",
                    help="shard file name prefix")
    ap.add_argument("--version", default="", help="version tag used in the file names")
    ap.add_argument("--level", type=int, default=6, help="zip deflate level (default 6)")
    args = ap.parse_args(argv)

    world = os.path.abspath(args.world)
    region_dir = os.path.join(world, "region")
    if not os.path.isdir(region_dir):
        print(f"error: {region_dir} is not a save directory", file=sys.stderr)
        return 2

    region_files = [os.path.join(region_dir, n) for n in os.listdir(region_dir)
                    if REGION_RE.match(n)]
    if not region_files:
        print(f"error: no region files in {region_dir}", file=sys.stderr)
        return 2

    groups = split_regions(region_files, max(1, int(args.parts)))
    os.makedirs(args.out, exist_ok=True)
    meta = metadata_files(world)
    raw = sum(os.path.getsize(p) for p in region_files)
    print(f"world    {world}")
    print(f"regions  {len(region_files):,} files, {raw / 1e6:.1f} MB on disk")
    print(f"metadata {len(meta)} files (level.dat, maps, mapgen.json, README ...)")
    print(f"shards   {len(groups)}  -> {args.out}")

    suffix = f"_{args.version}" if args.version else ""
    written: List[str] = []
    sum_lines: List[str] = []
    for i, group in enumerate(groups, start=1):
        name = f"{args.prefix}{suffix}_part{i:02d}_of_{len(groups):02d}.zip"
        path = os.path.join(args.out, name)
        files = list(group) + (meta if i == 1 else [])
        size = write_part(path, world, files, level=args.level)
        mb = size / 1e6
        flag = "  <-- OVER LIMIT" if mb > args.max_mb else ""
        print(f"  {name}  {mb:6.1f} MB  ({len(group)} regions, "
              f"{len(files) - len(group)} metadata){flag}")
        if mb > args.max_mb:
            print(f"error: shard exceeds {args.max_mb} MB; raise --parts", file=sys.stderr)
            return 3
        written.append(path)
        sum_lines.append(f"{sha256(path)}  {name}")

    sums = os.path.join(args.out, "SHA256SUMS.txt")
    with open(sums, "w", encoding="utf-8") as fh:
        fh.write("\n".join(sum_lines) + "\n")
    print(f"\n  {os.path.basename(sums)}")
    print(f"  total {sum(os.path.getsize(p) for p in written) / 1e6:.1f} MB "
          f"in {len(written)} shards")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
