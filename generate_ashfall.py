#!/usr/bin/env python3
"""Generate the continent of Vantyra - the Ashenfall world map.

The turnkey command from the specification::

    python generate_ashfall.py --res 2048 --out worldpainter

writes, into ``out/`` (or the path given with ``--dir``):

* ``ASHFALL_HEIGHTMAP_16BIT.png`` - 16-bit uint16 heightfield, ``h = (Y + 64) / 384``
* ``ASHFALL_POPULATE_MASK.png``   - Still Life populate mask (spec §5, method 1)
* ``ashenfall_worldpainter_setup.js`` - JSR-223 script that builds the world

Other outputs:

    python generate_ashfall.py --res 2048 --out worldpainter --dir world/out
    python generate_ashfall.py --out anvil --dir saves --size 2048     # Minecraft world
    python generate_ashfall.py --out both --res 2048 --dir out

``--out anvil`` writes a real Minecraft Java 1.21.1 save with the chunks left at
``Status: minecraft:features`` so the mods (Lithosphere / Still Life) decorate them on
first load; that path costs one chunk of work per 16 x 16 blocks, so it is much heavier
than the WorldPainter path.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mg.config import apply_preset, default_config, save_config            # noqa: E402
from mg.generation.landmarks import SEA_LEVEL                               # noqa: E402
from mg.pipeline import run_pipeline                                        # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="generate_ashfall.py",
        description="Generate the 8,000 x 8,000 continent of Vantyra (Ashenfall).",
    )
    ap.add_argument("--res", type=int, default=2048,
                    help="heightmap resolution in pixels (default 2048, spec turnkey command)")
    ap.add_argument("--out", choices=("worldpainter", "anvil", "both"), default="worldpainter",
                    help="what to write: the WorldPainter asset set, a Minecraft world, or both")
    ap.add_argument("--dir", default="out", help="output folder (default: out)")
    ap.add_argument("--size", type=int, default=None,
                    help="override the canvas size in blocks (default 8000, spec canvas)")
    ap.add_argument("--cell", type=int, default=4,
                    help="simulation cell size in blocks (default 4; larger is faster)")
    ap.add_argument("--seed", type=int, default=20250929, help="world seed")
    ap.add_argument("--name", default="Ashenfall", help="world name")
    ap.add_argument("--config", default="", help="write the resolved mapgen.json here")
    ap.add_argument("--compression", type=int, default=0,
                    help="zlib level for the Minecraft region files (0 = keep the preset's "
                         "value, 9 = smallest download; only used by --out anvil)")
    ap.add_argument("--progress-log", type=float, default=0.0, metavar="SECONDS",
                    help="append a timestamped progress line to stdout at most every SECONDS, "
                         "which keeps a redirected build log readable")
    ap.add_argument("--quiet", action="store_true", help="no progress output")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    cfg = apply_preset(default_config(), "ashenfall")
    cfg.seed = int(args.seed)
    cfg.name = args.name
    cfg.region.cell_size = int(args.cell)
    if args.size:
        size = int(args.size)
        cfg.region.blocks_x = cfg.region.blocks_z = size
        cfg.region.x0 = cfg.region.z0 = -(size // 2)
    cfg.export["world_name"] = args.name
    if args.compression:
        cfg.export["compression"] = int(args.compression)

    blocks = cfg.region.blocks_x
    cells = cfg.region.cells_x
    print(f"Ashenfall: {args.name}")
    print(f"  canvas   {blocks:,} x {blocks:,} blocks, Y -64..320, sea level Y={SEA_LEVEL:.0f}")
    print(f"  cells    {cells:,} x {cells:,} at {cfg.region.cell_size} blocks/cell "
          f"({cells * cells:,} samples)")
    print(f"  output   {args.out} -> {os.path.abspath(args.dir)}")

    last = [0.0]
    log_every = float(args.progress_log or 0.0)

    def progress(frac: float, label: str) -> None:
        if args.quiet or log_every:
            if log_every:
                now = time.time()
                if now - last[0] < log_every and frac < 1.0:
                    return
                last[0] = now
                print(f"  {time.strftime('%H:%M:%S')}  {frac * 100:5.1f}%  {label}", flush=True)
            return
        now = time.time()
        if now - last[0] < 0.4 and frac < 1.0:
            return
        last[0] = now
        bar = "#" * int(frac * 28)
        print(f"\r  [{bar:<28}] {frac * 100:5.1f}%  {label[:44]:<44}", end="", flush=True)

    t0 = time.time()
    result = run_pipeline(cfg, progress=progress)
    if not args.quiet:
        print()
    stats = result.terrain.stats()
    print(f"  generated in {time.time() - t0:.1f}s   "
          f"Y {stats['min_height']:.0f}..{stats['max_height']:.0f}   "
          f"rivers {stats.get('river_count', 0)}   lakes {stats.get('lake_count', 0)}")

    if args.config:
        save_config(cfg, args.config)
        print(f"  config   {args.config}")

    if args.out in ("worldpainter", "both"):
        from mg.export.worldpainter import export_worldpainter

        wp_dir = os.path.join(args.dir, "worldpainter") if args.out == "both" else args.dir
        wp = export_worldpainter(result.terrain, cfg.to_dict(), wp_dir, res=int(args.res),
                                 seed=cfg.seed, progress=progress)
        if not args.quiet:
            print()
        print(f"  worldpainter: {wp_dir}  ({wp.seconds:.1f}s, "
              f"{wp.diagnostics['populate_fraction'] * 100:.1f}% populate)")
        for path in sorted(wp.files):
            print(f"    {os.path.basename(path)}")

    if args.out in ("anvil", "both"):
        from mg.export.world import export_world

        anvil_dir = os.path.join(args.dir, "minecraft") if args.out == "both" else args.dir
        print("  exporting Minecraft world (this is the heavy path) ...")
        res = export_world(result.terrain, cfg.to_dict(), anvil_dir, seed=cfg.seed,
                           progress=progress)
        if not args.quiet:
            print()
        print(f"  minecraft: {res.world_dir}  {res.chunks:,} chunks  "
              f"({res.seconds:.1f}s)")

    print("  done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
