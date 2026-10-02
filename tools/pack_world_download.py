#!/usr/bin/env python3
"""Package an exported Minecraft save for release on GitHub.

GitHub refuses files larger than 100 MB and a full 8,000 x 8,000 continent is far bigger
than that, so the save is published as a set of ZIP shards:

* ``…_regions01.zip``, ``…_regions02.zip`` … hold whole region files (``region/r.x.z.mca``),
  so extracting all of them into one folder reconstructs the world exactly,
* ``…_meta.zip`` holds ``level.dat``, ``mapgen.json``, ``maps/``, ``worldpainter/`` and
  ``README.txt``; it is written once the export has finished,
* ``session.lock`` and ``level.dat_old`` are dropped - the game recreates them and a stale
  lock only confuses the launcher.

Two modes:

``--once`` (default)
    Split a finished save into ``--parts`` shards of roughly equal size.

``--watch``
    Follow a running export.  A region file is finished as soon as all 1,024 of its chunk
    slots are filled, which happens while the export sweeps past it, so this can publish
    shard after shard *during* a two-hour build instead of holding everything until the
    end.  With ``--push`` every shard is committed and pushed to the current branch as soon
    as it is written, so an interrupted build keeps everything it has published so far.

    python3 tools/pack_world_download.py out/ashenfall/Ashenfall \
        --out release/ashenfall-world --watch --push --interval 60
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from typing import Dict, Iterable, List, Sequence, Tuple

REGION_RE = re.compile(r"^r\.(-?\d+)\.(-?\d+)\.mca$")
SKIP = {"session.lock", "level.dat_old"}
CHUNKS_PER_REGION = 1024


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------
def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def region_sort_key(name: str) -> Tuple[int, int]:
    match = REGION_RE.match(os.path.basename(name))
    if not match:
        return (1 << 30, 1 << 30)
    return (int(match.group(1)), int(match.group(2)))


def occupied_chunks(path: str) -> int:
    """How many of the 1,024 chunk slots a region file actually holds."""
    try:
        with open(path, "rb") as fh:
            header = fh.read(4096)
    except OSError:
        return 0
    if len(header) < 4096:
        return 0
    return sum(1 for i in range(CHUNKS_PER_REGION)
               if header[i * 4: i * 4 + 3] != b"\x00\x00\x00")


def complete_regions(region_dir: str) -> List[str]:
    out = []
    for name in os.listdir(region_dir):
        path = os.path.join(region_dir, name)
        if REGION_RE.match(name) and occupied_chunks(path) == CHUNKS_PER_REGION:
            out.append(path)
    return sorted(out, key=region_sort_key)


def metadata_files(world_dir: str) -> List[str]:
    out: List[str] = []
    for root, _dirs, names in os.walk(world_dir):
        for name in sorted(names):
            if name in SKIP:
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, world_dir)
            if rel.startswith("region" + os.sep) or rel.startswith("schematic" + os.sep):
                continue
            if os.path.getsize(path) > 32 << 20:
                continue
            out.append(path)
    return out


def packaged_regions(out_dir: str, prefix: str) -> set:
    """Which region files are already inside a published shard.

    The shards themselves are the state - nothing else has to survive an interruption.
    """
    done = set()
    for path in glob.glob(os.path.join(out_dir, f"{prefix}*.zip")):
        if path.endswith("_meta.zip"):
            continue
        try:
            with zipfile.ZipFile(path) as z:
                for name in z.namelist():
                    base = os.path.basename(name)
                    if REGION_RE.match(base):
                        done.add(base)
        except zipfile.BadZipFile:  # pragma: no cover - a half-written shard
            continue
    return done


def write_zip(path: str, world_dir: str, files: Sequence[str], *, level: int = 6) -> int:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=level) as z:
        for src in files:
            z.write(src, os.path.relpath(src, world_dir))
    return os.path.getsize(path)


def next_free_index(out_dir: str, prefix: str, start: int = 1) -> int:
    """Continue the numbering after the shards that are already published."""
    index = max(1, int(start))
    while os.path.exists(os.path.join(out_dir, f"{prefix}_regions{index:02d}.zip")):
        index += 1
    return index


def write_shards(out_dir: str, world_dir: str, prefix: str, region_files: Sequence[str], *,
                 start_index: int, budget: float, level: int = 6,
                 extra: Sequence[str] = ()) -> List[Tuple[str, List[str]]]:
    """Write shards of whole region files, none bigger than ``budget`` bytes."""
    written: List[Tuple[str, List[str]]] = []
    pending = list(region_files)
    index = next_free_index(out_dir, prefix, start_index)
    while pending:
        # start optimistically with everything that is left, then halve until it fits
        take = len(pending)
        while True:
            name = f"{prefix}_regions{index:02d}.zip"
            path = os.path.join(out_dir, name)
            if os.path.exists(path):          # a shard from an earlier run lives here
                index += 1
                continue
            size = write_zip(path, world_dir, pending[:take] + list(extra), level=level)
            if size <= budget or take == 1:
                break
            os.remove(path)
            take = max(1, take // 2)
        written.append((path, pending[:take]))
        pending = pending[take:]
        index += 1
    return written


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(("git",) + args, capture_output=True, text=True, check=check)


def commit_and_push(paths: Iterable[str], message: str, *, attempts: int = 3) -> bool:
    for attempt in range(1, attempts + 1):
        try:
            git("add", *paths)
            if git("diff", "--cached", "--quiet", check=False).returncode != 0:
                git("commit", "-q", "-m", message)
            # Always push, even when there was nothing to commit: after a failed push the
            # earlier attempt's commit is already in place, and "nothing to commit" must
            # not be reported as "published".
            git("push", "-q", "origin", "HEAD")
            return True
        except subprocess.CalledProcessError as exc:  # pragma: no cover - network/git race
            print(f"  git attempt {attempt} failed: {exc.stderr.strip()[:200]}")
            time.sleep(5 * attempt)
    return False


# --------------------------------------------------------------------------------------
# modes
# --------------------------------------------------------------------------------------
def run_once(args) -> int:
    world = os.path.abspath(args.world)
    region_dir = os.path.join(world, "region")
    if not os.path.isdir(region_dir):
        print(f"error: {region_dir} is not a save directory", file=sys.stderr)
        return 2
    regions = sorted([os.path.join(region_dir, n) for n in os.listdir(region_dir)
                      if REGION_RE.match(n)], key=region_sort_key)
    if not regions:
        print(f"error: no region files in {region_dir}", file=sys.stderr)
        return 2

    os.makedirs(args.out, exist_ok=True)
    meta = metadata_files(world)
    raw = sum(os.path.getsize(p) for p in regions)
    print(f"world    {world}")
    print(f"regions  {len(regions):,} files, {raw / 1e6:.1f} MB on disk")
    print(f"metadata {len(meta)} files (level.dat, maps, mapgen.json, README ...)")

    suffix = f"_{args.version}" if args.version else ""
    prefix = args.prefix + suffix
    # every shard gets the metadata: extracting any one of them is already a valid save
    keep_meta_in = max(1, len(regions) // max(args.parts, 1))
    groups: List[List[str]] = [regions[i:i + keep_meta_in]
                               for i in range(0, len(regions), keep_meta_in)]
    written: List[Tuple[str, List[str]]] = []
    for i, group in enumerate(groups, start=1):
        path = os.path.join(args.out, f"{prefix}_regions{i:02d}.zip")
        size = write_zip(path, world, group + (meta if i == 1 else []), level=args.level)
        mb = size / 1e6
        flag = "  <-- OVER LIMIT" if mb > args.max_mb else ""
        print(f"  {os.path.basename(path)}  {mb:6.1f} MB  ({len(group)} regions){flag}")
        if mb > args.max_mb:
            print(f"error: shard exceeds {args.max_mb} MB; raise --parts", file=sys.stderr)
            return 3
        written.append((path, group))
    return _write_sums(args.out) and 0


def run_watch(args) -> int:
    world = os.path.abspath(args.world)
    region_dir = os.path.join(world, "region")
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    suffix = f"_{args.version}" if args.version else ""
    prefix = args.prefix + suffix
    print(f"watching {region_dir}")
    print(f"publishing into {out_dir} as {prefix}_regionsNN.zip")
    index = next_free_index(out_dir, prefix)
    print(f"continuing at shard {index:02d}")
    empty_rounds = 0
    while True:
        try:
            done = packaged_regions(out_dir, prefix)
            regions = [p for p in complete_regions(region_dir)
                       if os.path.basename(p) not in done]
            raw_pending = sum(os.path.getsize(p) for p in regions)
            if regions and raw_pending < args.min_raw_mb * 1e6 and not _is_complete(
                    world, region_dir, args.expect_regions):
                regions = []          # wait for a worthwhile shard instead of spamming
            if regions:
                budget = args.max_mb * 1e6
                extra = list(args.flush_extra)
                written = write_shards(out_dir, world, prefix, regions,
                                       start_index=index, budget=budget, level=args.level)
                index += len(written)
                names = []
                for path, group in written:
                    names.append(os.path.basename(path))
                    print(f"  {time.strftime('%H:%M:%S')}  {os.path.basename(path)}  "
                          f"{os.path.getsize(path) / 1e6:.1f} MB  ({len(group)} regions)",
                          flush=True)
                _write_sums(out_dir)
                if args.push:
                    ok = commit_and_push(
                        [os.path.join(out_dir, n) for n in names]
                        + [os.path.join(out_dir, "SHA256SUMS.txt")],
                        f"Publish Ashenfall world shards {names[0]} .. {names[-1]}")
                    print(f"  {'pushed' if ok else 'PUSH FAILED'}: "
                          f"{len(names)} shard(s)", flush=True)
                empty_rounds = 0
            else:
                empty_rounds += 1

            # the export is over when the save carries a level.dat and no region is half
            # written any more
            if args.expect_regions > 0 and _is_complete(world, region_dir, args.expect_regions):
                if not regions:      # anything still unpublished goes out now
                    regions = [p for p in complete_regions(region_dir)
                               if os.path.basename(p) not in packaged_regions(out_dir, prefix)]
                    if regions:
                        written = write_shards(out_dir, world, prefix, regions,
                                               start_index=index, budget=args.max_mb * 1e6,
                                               level=args.level)
                        index += len(written)
                        names = [os.path.basename(p) for p, _g in written]
                        for path, group in written:
                            print(f"  {time.strftime('%H:%M:%S')}  {os.path.basename(path)}  "
                                  f"{os.path.getsize(path) / 1e6:.1f} MB  ({len(group)} regions)",
                                  flush=True)
                        _write_sums(out_dir)
                        if args.push:
                            commit_and_push(
                                [os.path.join(out_dir, n) for n in names]
                                + [os.path.join(out_dir, "SHA256SUMS.txt")],
                                f"Publish Ashenfall world shards {names[0]} .. {names[-1]}")
                total = len([n for n in os.listdir(region_dir) if REGION_RE.match(n)])
                print(f"  {time.strftime('%H:%M:%S')}  world complete: {total} regions",
                      flush=True)
                return finalize_meta(args, prefix, index)

            if args.idle_exit and empty_rounds >= args.idle_exit:
                print(f"  nothing new for {args.idle_exit} rounds, stopping")
                return 0
            time.sleep(max(5.0, args.interval))
        except KeyboardInterrupt:  # pragma: no cover
            return 0


def _is_complete(world: str, region_dir: str, expect_regions: int) -> bool:
    """``level.dat`` written and every region file full: the export is finished."""
    if expect_regions <= 0 or not os.path.isfile(os.path.join(world, "level.dat")):
        return False
    total = len([n for n in os.listdir(region_dir) if REGION_RE.match(n)])
    if total < expect_regions:
        return False
    return len(complete_regions(region_dir)) == total


def finalize_meta(args, prefix: str, index: int) -> int:
    """Write the metadata shard for a finished world and publish it."""
    world = os.path.abspath(args.world)
    out_dir = os.path.abspath(args.out)
    meta = metadata_files(world)
    path = os.path.join(out_dir, f"{prefix}_meta.zip")
    size = write_zip(path, world, meta, level=args.level)
    print(f"  {os.path.basename(path)}  {size / 1e6:.1f} MB  ({len(meta)} files)")
    _write_sums(out_dir)
    if args.push:
        ok = commit_and_push([path, os.path.join(out_dir, "SHA256SUMS.txt")],
                             f"Publish Ashenfall world metadata ({len(meta)} files)")
        print(f"  {'pushed' if ok else 'PUSH FAILED'}: metadata")
    return 0


def _write_sums(out_dir: str) -> bool:
    shards = sorted(glob.glob(os.path.join(out_dir, "*.zip")))
    lines = [f"{sha256(p)}  {os.path.basename(p)}" for p in shards]
    with open(os.path.join(out_dir, "SHA256SUMS.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"  SHA256SUMS.txt  {len(shards)} shard(s)")
    return True


def run_restore(args) -> int:
    """Extract published shards back into the save folder.

    This is what makes a two-hour export survive losing the sandbox: after a reset the
    region files are gone, but the shards that were pushed to the repository are not, so
    they are unpacked again and the export resumes from the chunks they carry.
    """
    world = os.path.abspath(args.world)
    out_dir = os.path.abspath(args.out)
    os.makedirs(world, exist_ok=True)
    shards = sorted(glob.glob(os.path.join(out_dir, "*.zip")))
    if not shards:
        print(f"nothing to restore: no shards in {out_dir}")
        return 0
    files = 0
    for path in shards:
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if name.endswith("/"):
                    continue
                target = os.path.join(world, name)
                if os.path.exists(target):
                    continue                     # never clobber newer local work
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with z.open(name) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                files += 1
        print(f"  {os.path.basename(path)}  -> {world}")
    print(f"restored {files} file(s) from {len(shards)} shard(s)")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pack_world_download.py",
                                 description="Split or stream-publish a save as ZIP shards.")
    ap.add_argument("world", help="the save directory (holds level.dat and region/)")
    ap.add_argument("--out", required=True, help="where the shards are written")
    ap.add_argument("--parts", type=int, default=4, help="--once: shards to aim for")
    ap.add_argument("--max-mb", type=float, default=90.0,
                    help="never emit a shard above this size (GitHub caps at 100 MB)")
    ap.add_argument("--prefix", default="ASHENFALL_MinecraftWorld")
    ap.add_argument("--version", default="", help="version tag used in the file names")
    ap.add_argument("--level", type=int, default=6, help="zip deflate level")
    ap.add_argument("--watch", action="store_true", help="follow a running export")
    ap.add_argument("--restore", action="store_true",
                    help="extract the shards in --out back into the save folder (used after "
                         "an interrupted build, so the resumable export can continue)")
    ap.add_argument("--interval", type=float, default=60.0, help="--watch poll seconds")
    ap.add_argument("--min-raw-mb", type=float, default=200.0,
                    help="--watch: hold newly finished regions until this much raw data has "
                         "accumulated, so a build publishes a few big shards, not many small")
    ap.add_argument("--push", action="store_true", help="commit and push each shard")
    ap.add_argument("--expect-regions", type=int, default=0,
                    help="--watch: finish once this many regions are complete")
    ap.add_argument("--idle-exit", type=int, default=0,
                    help="--watch: stop after this many rounds with nothing new (0 = never)")
    ap.add_argument("--flush-extra", action="append", default=[],
                    help="--watch: extra files to add to every shard (repeatable)")
    args = ap.parse_args(argv)
    if args.restore:
        return run_restore(args)
    return run_watch(args) if args.watch else run_once(args)


if __name__ == "__main__":
    raise SystemExit(main())
