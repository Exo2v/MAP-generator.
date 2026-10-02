"""Command line interface — generate and export without opening the UI.

Examples
--------
    # generate a 2 km world with the cinematic preset and export it to a saves folder
    python -m mg.cli generate --preset cinematic --size 2048 --seed 42 \\
        --out "C:/Users/me/.minecraft/saves"

    # full-resolution render of one raster from an existing config
    python -m mg.cli map --config world/mapgen.json --kind biome --out biome.png

    # inspect a single column
    python -m mg.cli inspect --config world/mapgen.json --x 512 --z 700
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Optional

import numpy as np

from .config import (
    GenerationConfig,
    apply_preset,
    default_config,
    load_config,
    preset_list,
    resolve_derived,
    save_config,
    validate,
)
from .core.types import RegionInfo


def _progress_printer():
    last = [0.0]

    def report(frac: float, message: str) -> None:
        pct = int(frac * 100)
        if pct != last[0]:
            last[0] = pct
            bar = "█" * (pct // 3) + "·" * (33 - pct // 3)
            sys.stdout.write(f"\r  [{bar}] {pct:3d}%  {message[:44]:<44}")
            sys.stdout.flush()

    return report


def cmd_generate(args: argparse.Namespace) -> int:
    cfg = load_config(args.config) if args.config else default_config()
    if args.preset:
        apply_preset(cfg, args.preset)
    if args.size:
        cfg.region.blocks_x = int(args.size)
        cfg.region.blocks_z = int(args.size)
    if args.width:
        cfg.region.blocks_x = int(args.width)
    if args.depth:
        cfg.region.blocks_z = int(args.depth)
    if args.cell_size:
        cfg.region.cell_size = int(args.cell_size)
    if args.seed is not None:
        cfg.seed = int(args.seed)
    if args.name:
        cfg.name = args.name
    for override in args.set or []:
        key, _, value = override.partition("=")
        parts = key.split(".")
        target = cfg
        for p in parts[:-1]:
            target = getattr(target, p)
        try:
            parsed = json.loads(value)
        except Exception:
            parsed = value
        target[parts[-1]] = parsed
    resolve_derived(cfg)

    problems = validate(cfg)
    for p in problems:
        print(f"warning: {p}", file=sys.stderr)

    print(f"generating '{cfg.name}'  seed={cfg.seed}  preset={cfg.preset}")
    print(f"  region {cfg.region.blocks_x}x{cfg.region.blocks_z} blocks, "
          f"cell {cfg.region.cell_size} -> {cfg.region.cells_x}x{cfg.region.cells_z} cells")

    from .pipeline import run_pipeline

    t0 = time.time()
    result = run_pipeline(cfg, progress=_progress_printer())
    print()
    stats = result.terrain.stats()
    print(f"  done in {time.time() - t0:.1f}s")
    for key in ("min_height", "max_height", "relief", "water_fraction", "river_count",
                "lake_count", "poi_count", "tree_count"):
        value = stats.get(key)
        if isinstance(value, float):
            print(f"    {key:16} {value:,.2f}")
        else:
            print(f"    {key:16} {value:,}")

    if args.save_config:
        save_config(cfg, args.save_config)
        print(f"  config written to {args.save_config}")

    if args.out:
        from .export.world import export_world

        print(f"exporting to {args.out} ...")
        res = export_world(result.terrain, cfg.to_dict(), args.out, seed=cfg.seed,
                           progress=_progress_printer())
        print()
        print(f"  world: {res.world_dir}")
        print(f"  {res.chunks:,} chunks, {res.blocks_written:,} blocks in {res.seconds:.1f}s")
        for f in res.region_files:
            print(f"    {f}")
    return 0


def cmd_map(args: argparse.Namespace) -> int:
    from PIL import Image

    from .pipeline import run_pipeline
    from .server.views import map_raster

    cfg = load_config(args.config) if args.config else default_config()
    if args.preset:
        apply_preset(cfg, args.preset)
    if args.size:
        cfg.region.blocks_x = int(args.size)
        cfg.region.blocks_z = int(args.size)
    if getattr(args, "cell_size", None):
        cfg.region.cell_size = int(args.cell_size)
    if args.seed is not None:
        cfg.seed = int(args.seed)
    resolve_derived(cfg)
    print(f"generating for map '{args.kind}' ...")
    result = run_pipeline(cfg, progress=_progress_printer())
    print()
    img = map_raster(result.terrain, args.kind, size=int(args.resolution))
    if args.resolution and max(img.shape[:2]) < int(args.resolution):
        # the raster is produced at simulation-cell resolution; scale the PNG up so the
        # requested --resolution is what actually lands on disk
        scale = max(1, int(args.resolution) // max(img.shape[:2]))
        img = np.repeat(np.repeat(img, scale, axis=0), scale, axis=1)
    Image.fromarray(img).save(args.out)
    print(f"wrote {args.out} ({img.shape[1]}x{img.shape[0]})")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    from .pipeline import run_pipeline
    from .server.views import column_info

    cfg = load_config(args.config) if args.config else default_config()
    if args.preset:
        apply_preset(cfg, args.preset)
    if args.size:
        cfg.region.blocks_x = int(args.size)
        cfg.region.blocks_z = int(args.size)
    if args.seed is not None:
        cfg.seed = int(args.seed)
    resolve_derived(cfg)
    result = run_pipeline(cfg)
    info = column_info(result.terrain, float(args.x), float(args.z))
    width = max(len(k) for k in info)
    for key, value in info.items():
        print(f"  {key:<{width}} : {value}")
    return 0


def cmd_presets(args: argparse.Namespace) -> int:
    for p in preset_list():
        print(f"{p['id']:<20} {p['label']}")
        print(f"{'':<20} {p['description']}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .desktop import main as desktop_main

    argv = ["--port", str(args.port)]
    if args.browser:
        argv.append("--browser")
    if args.no_window:
        argv.append("--no-window")
    return desktop_main(argv)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="mg", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    g = sub.add_parser("generate", help="generate a world (optionally export it)")
    g.add_argument("--config", help="mapgen.json to start from")
    g.add_argument("--preset", choices=[p["id"] for p in preset_list()])
    g.add_argument("--size", type=int, help="square region size in blocks")
    g.add_argument("--width", type=int)
    g.add_argument("--depth", type=int)
    g.add_argument("--cell-size", type=int, dest="cell_size")
    g.add_argument("--seed", type=int)
    g.add_argument("--name")
    g.add_argument("--out", help="output folder for the exported world")
    g.add_argument("--save-config", dest="save_config")
    g.add_argument("--set", action="append", metavar="key.path=value",
                   help="override any config value, e.g. water.river_threshold=500")
    g.set_defaults(func=cmd_generate)

    m = sub.add_parser("map", help="render a single map raster")
    m.add_argument("--config")
    m.add_argument("--preset")
    m.add_argument("--size", type=int)
    m.add_argument("--cell-size", type=int, dest="cell_size")
    m.add_argument("--seed", type=int)
    m.add_argument("--kind", default="biome",
                   choices=["height", "elevation", "hillshade", "slope", "biome",
                            "water", "hydrology", "flow", "discharge", "population",
                            "vegetation", "fertility", "temperature", "humidity",
                            "climate", "soil", "continentality"])
    m.add_argument("--resolution", type=int, default=2048)
    m.add_argument("--out", default="map.png")
    m.set_defaults(func=cmd_map)

    i = sub.add_parser("inspect", help="print one column's data")
    i.add_argument("--config")
    i.add_argument("--preset")
    i.add_argument("--size", type=int)
    i.add_argument("--cell-size", type=int, dest="cell_size")
    i.add_argument("--seed", type=int)
    i.add_argument("--x", type=float, required=True)
    i.add_argument("--z", type=float, required=True)
    i.set_defaults(func=cmd_inspect)

    p = sub.add_parser("presets", help="list the built-in presets")
    p.set_defaults(func=cmd_presets)

    s = sub.add_parser("serve", help="run the desktop app / preview server")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--browser", action="store_true")
    s.add_argument("--no-window", action="store_true", dest="no_window")
    s.set_defaults(func=cmd_serve)
    return ap


def main(argv: Optional[list] = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
