"""Export a generated region as a playable Minecraft Java world.

What lands on disk:

    <output>/<World Name>/
        level.dat                 - world settings, spawn point, seed, generator info
        session.lock
        region/r.X.Z.mca          - the actual terrain (Anvil, 1.18+ format)
        mapgen.json               - the exact config, so the world can be regenerated
        maps/*.png                - height / biome / climate / flow / population rasters
        schematic/*.schem         - optional WorldEdit selections
        README.txt                - how to install and what is in the world

The exporter streams one chunk at a time, so memory stays flat whether the region is
1 km or 16 km across.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from PIL import Image

from ..core.erosion import slope_map
from ..core.types import CHUNK, MIN_Y, SEA_LEVEL, TerrainGrid
from ..generation.surface import TerrainSampler
from .anvil import RegionFileWriter
from .nbt import Byte, Compound, Double, Float, Int, List as NbtList, Long, String, Tag, compound_of, write_nbt

ProgressFn = Callable[[float, str], None]


@dataclass
class ExportResult:
    world_dir: str
    region_files: List[str]
    chunks: int
    blocks_written: int
    seconds: float
    extras: List[str]


def _level_dat(
    *,
    name: str,
    seed: int,
    spawn: Tuple[int, int, int],
    data_version: int,
    version: str,
    game_type: int = 1,
    hardcore: bool = False,
    allow_commands: bool = True,
) -> Tag:
    now = int(time.time() * 1000)
    version_tag = compound_of("Version", [
        Int("Id", int(data_version)),
        String("Name", version),
        Byte("Snapshot", 0),
    ])
    # The file is a nameless root compound holding one compound called "Data".
    level = compound_of("", [compound_of("Data", [
        Int("DataVersion", int(data_version)),
        String("LevelName", name),
        String("generatorName", "minecraft:normal"),
        Int("GameType", game_type),
        Byte("hardcore", 1 if hardcore else 0),
        Byte("MapFeatures", 1),
        Byte("allowCommands", 1 if allow_commands else 0),
        Byte("initialized", 1),
        Byte("Difficulty", 2),
        Byte("DifficultyLocked", 0),
        Byte("raining", 0),
        Byte("thundering", 0),
        Int("rainTime", 0),
        Int("thunderTime", 0),
        Long("RandomSeed", int(seed)),
        Long("LastPlayed", now),
        Long("Time", 6000),
        Long("DayTime", 6000),
        Int("clearWeatherTime", 0),
        Int("GameTypeKeep", 0),
        Int("SpawnX", int(spawn[0])),
        Int("SpawnY", int(spawn[1])),
        Int("SpawnZ", int(spawn[2])),
        Int("version", 19133),
        Int("clearWeatherTime", 0),
        compound_of("GameRules", [String("doDaylightCycle", "false"),
                                  String("doWeatherCycle", "false"),
                                  String("keepInventory", "true")]),
        compound_of("WorldGenSettings", [
            Long("seed", int(seed)),
            Byte("generate_features", 1),
            Byte("bonus_chest", 0),
            compound_of("dimensions", [
                compound_of("minecraft:overworld", [
                    compound_of("generator", [
                        String("type", "minecraft:noise"),
                        String("settings", "minecraft:overworld"),
                        compound_of("biome_source", [String("type", "minecraft:multi_noise")]),
                    ]),
                    String("type", "minecraft:overworld"),
                ]),
            ]),
            Int("DataVersion", int(data_version)),
        ]),
        version_tag,
        NbtList("ServerBrands", [String("", "mapgen")]),
        compound_of("DataPacks", [
            NbtList("Enabled", [String("", "vanilla")], item_type=8),
            NbtList("Disabled", [], item_type=8),
        ]),
        Int("WanderingTraderSpawnDelay", 24000),
        Int("WanderingTraderSpawnChance", 25),
        Int("DataVersion", int(data_version)),
    ])])
    return level


def _world_readme(cfg: Dict, stats: Dict, world_name: str) -> str:
    lines = [
        f"{world_name}",
        "=" * len(world_name),
        "",
        "Generated with mapgen - a World Machine / World Painter / Streams-style",
        "terrain generator for Minecraft Java Edition.",
        "",
        f"Seed          : {cfg.get('seed')}",
        f"Preset        : {cfg.get('preset')}",
        f"Region        : {cfg.get('region', {}).get('blocks_x')} x {cfg.get('region', {}).get('blocks_z')} blocks",
        f"Origin        : x={cfg.get('region', {}).get('x0')}, z={cfg.get('region', {}).get('z0')}",
        "",
        "Install:",
        "  1. Copy this whole folder into your '.minecraft/saves' directory.",
        "  2. Launch Minecraft (matching version) and open the world.",
        "  3. Fly to the coordinates above - the terrain lives there.",
        "",
        "Contents",
        "--------",
        f"  height range   : {stats.get('min_height')} .. {stats.get('max_height')}",
        f"  water coverage : {100 * stats.get('water_fraction', 0):.1f}%",
        f"  rivers         : {stats.get('river_count')}",
        f"  lakes          : {stats.get('lake_count')}",
        f"  settlements    : {stats.get('poi_count')}",
        f"  roads          : {stats.get('road_count')}",
        f"  plants         : {stats.get('tree_count')}",
        "",
        "Also included:",
        "  mapgen.json    - regenerate this exact world",
        "  maps/          - height, biome, climate, population, flow and water rasters",
        "  schematic/     - WorldEdit selections (if enabled)",
    ]
    return "\n".join(lines) + "\n"


def export_world(
    terrain: TerrainGrid,
    config: Dict,
    output_dir: str,
    *,
    seed: int,
    progress: Optional[ProgressFn] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    sampler: Optional[TerrainSampler] = None,
) -> ExportResult:
    """Write a full world folder for ``terrain`` into ``output_dir``."""
    t0 = time.time()
    export_cfg = (config or {}).get("export") or {}
    world_name = str(export_cfg.get("world_name") or config.get("name") or "Generated World")
    world_name = _safe_name(world_name)
    world_dir = os.path.join(output_dir, world_name)
    region_dir = os.path.join(world_dir, "region")
    os.makedirs(region_dir, exist_ok=True)

    version = str(export_cfg.get("version", "1.21"))
    data_version = int(export_cfg.get("data_version", 3953))
    min_y = int(export_cfg.get("min_y", MIN_Y))
    max_y = int(export_cfg.get("max_y", 320))
    sea_level = float((config or {}).get("climate", {}).get("sea_level", SEA_LEVEL))
    compression = int(export_cfg.get("compression", 2))

    region = terrain.region
    sampler = sampler or TerrainSampler(terrain, seed=seed, cfg=config)
    total_chunks = (region.blocks_x // CHUNK) * (region.blocks_z // CHUNK)
    done = 0
    blocks_written = 0

    # region files are keyed by their 512-block grid position
    writers: Dict[Tuple[int, int], RegionFileWriter] = {}
    region_files: List[str] = []

    flushed: set = set()

    def get_writer(cx: int, cz: int) -> RegionFileWriter:
        rx = (region.x0 + cx * CHUNK) >> 9
        rz = (region.z0 + cz * CHUNK) >> 9
        key = (rx, rz)
        if key not in writers:
            writer = RegionFileWriter(
                rx, rz, compression=compression, version=version, data_version=data_version,
                min_y=min_y, max_y=max_y, chunk_status=chunk_status,
            )
            path = _region_path(region_dir, writer)
            if key in flushed:  # a previous batch already wrote part of this region
                writer.adopt_existing(path)
            writers[key] = writer
        return writers[key]

    # ---- who decorates the world? -------------------------------------------------------
    # "engine" : bake vegetation/structures here and mark chunks full - playable vanilla.
    # "mods"   : leave the surface bare, mark chunks as an earlier generation stage and
    #            ship the populate mask, so the game's own decorators (Still Life,
    #            Lithosphere) place the features on first load.  That is what the Ashenfall
    #            specification asks for in its population section.
    decorate_in_engine = str(export_cfg.get("decoration", "engine")).lower() != "mods"
    chunk_status = str(export_cfg.get("chunk_status")
                       or ("minecraft:full" if decorate_in_engine else "minecraft:features"))

    chunks_x = region.blocks_x // CHUNK
    chunks_z = region.blocks_z // CHUNK
    for cz in range(chunks_z):
        for cx in range(chunks_x):
            if should_cancel and should_cancel():
                raise RuntimeError("export cancelled")
            chunk = sampler.generate_chunk(cx, cz, decorate=decorate_in_engine)
            blocks_written += int(np.count_nonzero(chunk.blocks))
            writer = get_writer(cx, cz)
            world_cx = (region.x0 // CHUNK) + cx
            world_cz = (region.z0 // CHUNK) + cz
            writer.add_chunk(world_cx, world_cz, chunk)
            done += 1
            if progress and (done % 8 == 0 or done == total_chunks):
                progress(done / max(total_chunks, 1),
                         f"exporting chunk {done}/{total_chunks}")
            if writer.chunk_count >= int(export_cfg.get("chunk_batch", 512)):
                path = _region_path(region_dir, writer)
                region_files.append(writer.write(path))
                flushed.add((writer.rx, writer.rz))
                writers.pop((writer.rx, writer.rz), None)

    for key in list(writers):
        writer = writers.pop(key)
        region_files.append(writer.write(_region_path(region_dir, writer)))

    # de-duplicate: a region flushed in batches reports the same file every time
    region_files = sorted(set(region_files))

    # ---- level.dat + session.lock ---------------------------------------------------------
    spawn = _pick_spawn(terrain, export_cfg)
    if bool(export_cfg.get("write_level_dat", True)):
        level = _level_dat(name=world_name, seed=seed, spawn=spawn, data_version=data_version,
                           version=version)
        with open(os.path.join(world_dir, "level.dat"), "wb") as fh:
            fh.write(_gzip(level, compression_level=6))
        with open(os.path.join(world_dir, "session.lock"), "wb") as fh:
            fh.write((0).to_bytes(8, "big"))
        # the old-format marker file keeps some tools happy
        with open(os.path.join(world_dir, "level.dat_old"), "wb") as fh:
            fh.write(_gzip(level, compression_level=6))

    extras: List[str] = []
    # ---- optional map / WorldPainter bundles ------------------------------------------------
    if bool(export_cfg.get("generate_png_maps", True)) and bool(export_cfg.get("write_bundle", True)):
        try:
            from ..server.views import write_map_bundle

            extras.extend(write_map_bundle(terrain, world_dir, config))
        except Exception as exc:  # pragma: no cover - bundles are a nice-to-have
            print(f"warning: map bundle skipped ({exc})")
    if bool(export_cfg.get("worldpainter_bundle", True)):
        try:
            from .schematic import export_worldpainter_bundle

            extras.extend(export_worldpainter_bundle(terrain, world_dir))
        except Exception as exc:  # pragma: no cover
            print(f"warning: WorldPainter bundle skipped ({exc})")
    if bool(export_cfg.get("write_schematic", False)):
        try:
            from .schematic import export_schematic_tiles

            extras.extend(export_schematic_tiles(
                sampler, os.path.join(world_dir, "schematic"),
                tile=int(export_cfg.get("schematic_chunks", 4)),
                limit=int(export_cfg.get("schematic_files", 12)),
                version=version, data_version=data_version,
            ))
        except Exception as exc:  # pragma: no cover
            print(f"warning: schematic export skipped ({exc})")

    # ---- populate mask (spec population method 1) ---------------------------------------
    # A binary mask marking where the game's decorators are allowed to plant things:
    # everything that is not water, not a cliff, below the treeline and below the snow
    # line.  WorldPainter consumes it as its native Populate layer, and it doubles as a
    # human-readable record of where Still Life will place canopies.
    if bool(export_cfg.get("write_populate_mask", False)) or not decorate_in_engine:
        try:
            from ..core.bluenoise import BlueNoiseMatrix

            heights = np.asarray(terrain.heights, dtype=np.float64)
            slope = np.degrees(np.arctan(slope_map(heights, float(region.cell_size))))
            water = np.asarray(terrain.water_mask) > 0
            canopy = (slope < 35.0) & ~water & (heights > float(sea_level) + 1.0)
            canopy &= heights <= float(export_cfg.get("treeline", 225.0))
            # dither so the mask edge is a natural scatter rather than a contour line
            bn = BlueNoiseMatrix(int(export_cfg.get("populate_noise_size", 64)),
                                 seed=int(seed))
            mask = bn.mask8(canopy.astype(np.float64), offset=(region.z0, region.x0))
            out = os.path.join(world_dir, "POPULATE_MASK.png")
            Image.fromarray(mask).save(out)
            extras.append(out)
            scree = np.clip((slope - 25.0) / 20.0, 0.0, 1.0) * (~water)
            out2 = os.path.join(world_dir, "SCREE_MASK.png")
            Image.fromarray((scree * 255).astype(np.uint8)).save(out2)
            extras.append(out2)
            frost = np.clip((heights - float(export_cfg.get("snowline", 200.0)) ) / 40.0,
                            0.0, 1.0) * (~water)
            out3 = os.path.join(world_dir, "FROST_MASK.png")
            Image.fromarray((frost * 255).astype(np.uint8)).save(out3)
            extras.append(out3)
        except Exception as exc:  # pragma: no cover
            print(f"warning: populate mask skipped ({exc})")

    # ---- config + readme -------------------------------------------------------------------
    with open(os.path.join(world_dir, "mapgen.json"), "w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2)
    extras.append("mapgen.json")
    with open(os.path.join(world_dir, "README.txt"), "w", encoding="utf-8") as fh:
        fh.write(_world_readme(config, terrain.stats(), world_name))
    extras.append("README.txt")

    return ExportResult(
        world_dir=world_dir,
        region_files=region_files,
        chunks=done,
        blocks_written=blocks_written,
        seconds=time.time() - t0,
        extras=extras,
    )


def _gzip(tag: Tag, compression_level: int = 6) -> bytes:
    from .nbt import write_nbt

    return write_nbt(tag, gzipped=True, compression_level=compression_level)


def _region_path(region_dir: str, writer: RegionFileWriter) -> str:
    return os.path.join(region_dir, f"r.{writer.rx}.{writer.rz}.mca")


def _safe_name(name: str) -> str:
    keep = "".join(c if (c.isalnum() or c in " -_.") else "_" for c in name).strip()
    return keep or "Generated World"


def _pick_spawn(terrain: TerrainGrid, export_cfg: Dict) -> Tuple[int, int, int]:
    """A spawn point that is on dry, gentle land near the middle of the region."""
    heights = terrain.heights
    wet = terrain.water_mask > 0
    from ..core.erosion import slope_map

    slope = slope_map(heights.astype(np.float64), float(terrain.region.cell_size))
    score = -slope
    score = np.where(wet, -1e9, score)
    score = np.where(heights < SEA_LEVEL + 1.5, -1e9, score)
    cz, cx = np.unravel_index(int(np.argmax(score)), score.shape)
    if not np.isfinite(score[cz, cx]) or score[cz, cx] < -1e8:
        cz, cx = heights.shape[0] // 2, heights.shape[1] // 2
    cs = terrain.region.cell_size
    x = int(terrain.region.x0 + (cx + 0.5) * cs)
    z = int(terrain.region.z0 + (cz + 0.5) * cs)
    y = int(np.clip(heights[cz, cx] + 2, MIN_Y + 2, 318))
    if not bool(export_cfg.get("spawn_in_center", True)):
        return x, y, z
    return x, y, z
