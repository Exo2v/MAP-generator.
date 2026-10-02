"""WorldEdit schematic export (Sponge Schematic v2) and a WorldPainter map bundle.

Two extra ways to move a generated chunk of world into other tools:

* :func:`export_schematic` writes a ``.schem`` that WorldEdit / FAWE can paste anywhere
  (``//schem load`` then ``//paste``), with the correct palette and var-int block stream.
* :func:`export_worldpainter_bundle` writes 16-bit ``heightmap.png`` plus a biome colour
  map and a water mask, which is what WorldPainter's heightmap importer wants.

Both are optional extras in the export step; the Anvil world is always the primary output.
"""

from __future__ import annotations

import gzip
import io
import os
from typing import Dict, List, Optional, Tuple

import numpy as np

from ..core.materials import ID_BLOCK, namespaced
from ..core.types import BIOMES, BIOME_COLORS, CHUNK, MAX_Y, MIN_Y, SEA_LEVEL
from .anvil import BLOCK_PROPERTIES
from .nbt import ByteArray, Compound, Int, IntArray, List as NbtList, Short, String, Tag, compound_of, write_nbt


def _varint_stream(ids: np.ndarray) -> bytes:
    """Var-int encode a flat array of palette indices (Sponge schematic BlockData)."""
    out = bytearray()
    for value in ids.astype(np.int64).tolist():
        v = value
        while True:
            b = v & 0x7F
            v >>= 7
            if v:
                out.append(b | 0x80)
            else:
                out.append(b)
                break
    return bytes(out)


def build_schematic(
    *,
    blocks: np.ndarray,  # (H, Z, X) uint16 block ids, y index 0 = the bottom of the box
    biomes: Optional[np.ndarray] = None,  # (Z, X) biome indices
    name: str = "mapgen selection",
    author: str = "mapgen",
    offset: Tuple[int, int, int] = (0, 0, 0),
    data_version: int = 3953,
    version: str = "1.21",
    include_air: bool = False,
) -> Tag:
    """Build a Sponge Schematic v2 root tag from a block box."""
    blocks = np.asarray(blocks, dtype=np.uint16)
    height, length, width = blocks.shape  # (y, z, x)

    used = np.unique(blocks)
    palette: List[str] = []
    remap = {}
    for bid in used.tolist():
        if bid == 0 and not include_air:
            remap[bid] = -1
            continue
        name_full = namespaced(int(bid), version)
        remap[bid] = len(palette)
        palette.append(name_full)
    # index 0 is always the first palette entry; empty boxes get an air entry so the
    # stream stays valid
    if not palette:
        palette = ["minecraft:air"]
    if remap.get(0, -1) < 0:
        # put air at the end so every index in the stream is still meaningful
        remap[0] = len(palette)
        palette.append("minecraft:air")

    flat = np.zeros(height * length * width, dtype=np.uint16)
    for bid, idx in remap.items():
        if idx < 0:
            continue
        flat[(blocks == bid).reshape(-1)] = idx

    palette_tag = compound_of("Palette", [Int(n, i) for i, n in enumerate(palette)])
    root = compound_of("Schematic", [
        Int("Version", 2),
        Int("DataVersion", int(data_version)),
        Short("Width", width),
        Short("Height", height),
        Short("Length", length),
        IntArray("Offset", np.array(offset, dtype=np.int32)),
        compound_of("Metadata", [
            String("Name", name),
            String("Author", author),
            String("Tool", "mapgen"),
        ]),
        Int("PaletteMax", len(palette)),
        palette_tag,
        ByteArray("BlockData", np.frombuffer(_varint_stream(flat), dtype=np.uint8).view(np.int8)),
    ])
    return root


def export_schematic(
    sampler,
    out_path: str,
    *,
    chunk_x0: int,
    chunk_z0: int,
    chunks_x: int = 4,
    chunks_z: int = 4,
    version: str = "1.21",
    data_version: int = 3953,
    name: Optional[str] = None,
) -> str:
    """Paste-ready schematic of a chunk-aligned box taken from a generated region."""
    region = sampler.region
    bx0 = region.x0 + chunk_x0 * CHUNK
    bz0 = region.z0 + chunk_z0 * CHUNK
    width = chunks_x * CHUNK
    length = chunks_z * CHUNK

    box = np.zeros((MAX_Y - MIN_Y, length, width), dtype=np.uint16)
    biomes = np.zeros((length, width), dtype=np.uint8)
    for dz in range(chunks_z):
        for dx in range(chunks_x):
            chunk = sampler.generate_chunk(chunk_x0 + dx, chunk_z0 + dz)
            box[:, dz * CHUNK : (dz + 1) * CHUNK, dx * CHUNK : (dx + 1) * CHUNK] = chunk.blocks
            biomes[dz * CHUNK : (dz + 1) * CHUNK, dx * CHUNK : (dx + 1) * CHUNK] = chunk.biomes

    # trim empty space so the schematic is only as tall as it needs to be
    occupied = np.any(box != 0, axis=(1, 2))
    if np.any(occupied):
        lo = int(np.argmax(occupied))
        hi = int(len(occupied) - np.argmax(occupied[::-1]))
        box = box[lo:hi]
        y_offset = MIN_Y + lo
    else:
        y_offset = MIN_Y

    root = build_schematic(
        blocks=box,
        biomes=biomes,
        name=name or os.path.basename(out_path),
        offset=(bx0, y_offset, bz0),
        data_version=data_version,
        version=version,
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as fh:
        fh.write(write_nbt(root, gzipped=True, compression_level=6))
    return out_path


# --------------------------------------------------------------------------------------
# WorldPainter bundle
# --------------------------------------------------------------------------------------


def _axis_picks(n: int, k: int) -> List[int]:
    """``k`` evenly spaced indices over ``range(n)``, ends included."""
    if k <= 1 or n <= 1:
        return [0]
    if k >= n:
        return list(range(n))
    picks = [int(round(i * (n - 1) / (k - 1))) for i in range(k)]
    out, seen = [], set()
    for v in picks:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _spread_tiles(nx: int, nz: int, limit: int) -> List[Tuple[int, int]]:
    """Pick at most ``limit`` tiles spread over an ``nx`` by ``nz`` grid of tiles.

    The picks form a coarse grid covering the whole region (corners included) rather
    than a single strip, so the exported schematics actually sample different biomes.
    """
    total = max(1, nx * nz)
    if limit <= 0 or total <= limit:
        return [(i % nx, i // nx) for i in range(total)]
    best = (0, 0.0, 1, 1)  # (picks, aspect penalty, kx, kz)
    target = nx / max(1, nz)
    for kx in range(1, min(nx, limit) + 1):
        kz = max(1, min(nz, limit // kx))
        picks = kx * kz
        if picks > limit:
            continue
        penalty = abs(kx / kz - target)
        if (picks, -penalty) > (best[0], -best[1]):
            best = (picks, penalty, kx, kz)
    _, _, kx, kz = best
    cols = _axis_picks(nx, kx)
    rows = _axis_picks(nz, kz)
    picks = [(x, z) for z in rows for x in cols][:limit]
    if len(picks) < min(limit, total):  # top up when the grid cannot reach ``limit``
        seen = set(picks)
        for i in range(total):
            cand = (i % nx, i // nx)
            if cand not in seen:
                seen.add(cand)
                picks.append(cand)
                if len(picks) >= limit:
                    break
    return picks


def export_schematic_tiles(
    sampler,
    out_dir: str,
    *,
    tile: int = 4,
    limit: int = 12,
    version: str = "1.21",
    data_version: int = 3953,
    name_prefix: str = "slice",
) -> List[str]:
    """Export up to ``limit`` chunk-aligned ``tile`` x ``tile`` boxes spread over the region.

    A 2048-block world is 128 chunks wide; one schematic of the whole thing would be
    unusable in WorldEdit, so we hand out a sample of tiles instead - near, far, coast
    and inland - and let the user paste whichever they want.
    """
    region = sampler.region
    cx_total = max(1, region.blocks_x // CHUNK)
    cz_total = max(1, region.blocks_z // CHUNK)
    tile = max(1, int(tile))
    nx = max(1, -(-cx_total // tile))
    nz = max(1, -(-cz_total // tile))
    os.makedirs(out_dir, exist_ok=True)
    written: List[str] = []
    for i, (tx, tz) in enumerate(_spread_tiles(nx, nz, int(limit))):
        cxx = min(tile, cx_total - tx * tile)
        czz = min(tile, cz_total - tz * tile)
        if cxx <= 0 or czz <= 0:
            continue
        path = os.path.join(out_dir, f"{name_prefix}_{i:02d}_{tx:03d}_{tz:03d}.schem")
        written.append(export_schematic(
            sampler, path,
            chunk_x0=tx * tile, chunk_z0=tz * tile,
            chunks_x=cxx, chunks_z=czz,
            version=version, data_version=data_version,
            name=f"{name_prefix} {tx},{tz}",
        ))
    return written


def export_worldpainter_bundle(terrain, world_dir: str, *, name: str = "worldpainter") -> List[str]:
    """Heightmap + biome + water images for WorldPainter's heightmap importer."""
    from PIL import Image

    out_dir = os.path.join(world_dir, name)
    os.makedirs(out_dir, exist_ok=True)
    written: List[str] = []

    heights = np.asarray(terrain.heights, dtype=np.float64)
    lo = float(heights.min())
    hi = float(heights.max())
    sea = float(terrain.meta.get("sea_level", SEA_LEVEL))

    # 16-bit heightmap: black = lowest, white = highest (WorldPainter convention)
    if hi - lo < 1e-6:
        hi = lo + 1.0
    norm = np.clip((heights - lo) / (hi - lo), 0.0, 1.0)
    hm = (norm * 65535.0).astype(np.uint16)
    # WorldPainter reads PNG rows top-to-bottom; our raster is +z downward already
    depth_img = Image.fromarray(hm)  # uint16 -> 16-bit grayscale PNG
    path = os.path.join(out_dir, "heightmap.png")
    depth_img.save(path)
    written.append(path)

    # water mask: white where water stands, so lakes/oceans import as water
    water = (np.asarray(terrain.water_mask) > 0).astype(np.uint8) * 255
    path = os.path.join(out_dir, "water.png")
    Image.fromarray(water, mode="L").save(path)
    written.append(path)

    # biome colour map (approximate; WorldPainter's own palette differs per version, so
    # this is meant as a visual reference / mixed-paint guide)
    palette = np.zeros((len(BIOMES), 3), dtype=np.uint8)
    for i, biome in enumerate(BIOMES):
        palette[i] = BIOME_COLORS.get(biome, (128, 128, 128))
    biome_img = palette[np.clip(terrain.biome, 0, len(BIOMES) - 1)]
    path = os.path.join(out_dir, "biomes.png")
    Image.fromarray(biome_img).save(path)
    written.append(path)

    # flow map: direction encoded as colour, discharge in blue
    flow = np.asarray(terrain.flow_dir, dtype=np.float64)
    angle = np.arctan2(flow[..., 1], flow[..., 0])
    strength = np.clip(np.log1p(np.asarray(terrain.discharge)) / 12.0, 0, 1)
    flow_img = np.zeros((*angle.shape, 3), dtype=np.uint8)
    flow_img[..., 0] = ((np.sin(angle) * 0.5 + 0.5) * 255).astype(np.uint8)
    flow_img[..., 1] = ((np.cos(angle) * 0.5 + 0.5) * 255).astype(np.uint8)
    flow_img[..., 2] = (strength * 255).astype(np.uint8)
    path = os.path.join(out_dir, "flow.png")
    Image.fromarray(flow_img).save(path)
    written.append(path)

    readme = os.path.join(out_dir, "README.txt")
    with open(readme, "w", encoding="utf-8") as fh:
        fh.write(
            "WorldPainter import bundle\n"
            "==========================\n\n"
            "In WorldPainter: File > Import > Height map...\n"
            f"  height map : heightmap.png   (16-bit, black = {lo:.0f}, white = {hi:.0f} block Y)\n"
            "  water level: " + f"{sea:.0f} blocks\n"
            "\n"
            "Then use water.png as a mask/overlay layer for lakes and oceans, and\n"
            "biomes.png as a reference when painting biomes (the colours are mapgen's own\n"
            "biome palette - see maps/legend.json).\n"
            "\n"
            "flow.png encodes surface flow direction as hue and stream size as blue:\n"
            "useful for painting rivers that run the right way.\n"
        )
    written.append(readme)
    return written
