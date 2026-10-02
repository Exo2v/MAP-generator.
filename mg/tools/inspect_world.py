"""Read an exported world back and render a top-down map of the actual blocks.

Useful as a smoke test (does the region file really contain the terrain we meant to
write?) and as a way to eyeball a world without launching Minecraft.

    python -m mg.tools.inspect_world "output/My World" --out preview.png
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
import zlib
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np

from ..core.materials import ID_BLOCK, block_name
from ..core.types import MIN_Y
from ..export.nbt import read_nbt

# Rough in-game colours for the blocks we emit, so the render looks like the game.
BLOCK_COLORS: Dict[str, Tuple[int, int, int]] = {
    "grass_block": (124, 168, 88), "dirt": (134, 96, 67), "coarse_dirt": (122, 88, 61),
    "rooted_dirt": (144, 104, 76), "podzol": (91, 68, 40), "mud": (61, 57, 65),
    "moss_block": (89, 109, 45), "mycelium": (111, 100, 111), "clay": (160, 166, 179),
    "gravel": (131, 127, 126), "sand": (219, 207, 163), "red_sand": (190, 102, 33),
    "sandstone": (216, 203, 155), "red_sandstone": (181, 97, 31),
    "terracotta": (152, 94, 67), "white_terracotta": (209, 178, 161),
    "stone": (125, 125, 125), "andesite": (136, 136, 136), "granite": (149, 103, 85),
    "diorite": (188, 188, 190), "deepslate": (80, 80, 84), "tuff": (108, 109, 102),
    "bedrock": (85, 85, 85), "basalt": (72, 72, 78), "blackstone": (44, 38, 43),
    "snow_block": (249, 254, 254), "snow": (249, 254, 254), "powder_snow": (244, 251, 251),
    "ice": (145, 183, 253), "packed_ice": (141, 180, 250), "blue_ice": (116, 167, 253),
    "water": (63, 118, 228), "lava": (207, 92, 23),
    "oak_log": (102, 81, 49), "birch_log": (216, 215, 210), "spruce_log": (58, 37, 16),
    "jungle_log": (86, 67, 27), "acacia_log": (103, 96, 86), "dark_oak_log": (60, 46, 26),
    "mangrove_log": (119, 54, 45), "muddy_mangrove_roots": (70, 62, 50),
    "oak_leaves": (60, 118, 42), "birch_leaves": (120, 165, 78), "spruce_leaves": (58, 94, 60),
    "jungle_leaves": (48, 110, 30), "acacia_leaves": (86, 130, 42),
    "dark_oak_leaves": (45, 89, 35), "mangrove_leaves": (68, 122, 44),
    "grass": (110, 160, 74), "tall_grass": (110, 160, 74), "fern": (95, 150, 70),
    "dead_bush": (148, 109, 60), "cactus": (85, 130, 55), "bamboo": (105, 150, 60),
    "dandelion": (200, 200, 60), "poppy": (190, 60, 60), "cornflower": (90, 110, 200),
    "oxeye_daisy": (230, 230, 230), "azure_bluet": (220, 220, 220), "allium": (170, 120, 200),
    "blue_orchid": (60, 180, 200), "lily_of_the_valley": (240, 240, 240),
    "sweet_berry_bush": (80, 120, 60), "brown_mushroom": (150, 120, 90),
    "red_mushroom": (200, 60, 50), "lily_pad": (60, 130, 60),
    "seagrass": (60, 150, 90), "sugar_cane": (150, 200, 120),
    "oak_planks": (162, 130, 79), "spruce_planks": (114, 84, 48),
    "dark_oak_planks": (66, 43, 20), "cobblestone": (127, 127, 127),
    "mossy_cobblestone": (110, 127, 100), "stone_bricks": (122, 121, 122),
    "mossy_stone_bricks": (105, 120, 95), "bricks": (150, 97, 83),
    "torch": (200, 170, 90), "lantern": (190, 160, 80), "bell": (200, 170, 70),
    "coal_ore": (110, 110, 110), "iron_ore": (170, 140, 120), "copper_ore": (150, 130, 110),
    "gold_ore": (200, 180, 110), "redstone_ore": (150, 90, 90),
    "lapis_ore": (90, 110, 160), "diamond_ore": (130, 190, 190),
    "vine": (60, 110, 50), "glow_lichen": (120, 140, 130),
    "oak_door": (140, 110, 66), "oak_fence": (150, 120, 72), "glass": (200, 220, 230),
    "glass_pane": (200, 220, 230), "hay_block": (170, 150, 50), "bookshelf": (140, 110, 70),
    "barrel": (120, 90, 55), "crafting_table": (130, 100, 60), "chest": (120, 90, 50),
    "campfire": (140, 100, 60), "white_terracotta": (209, 178, 161),
}

#: blocks that count as "vegetation" when picking which block to display
AIR = 0


def read_region(path: str) -> Dict[Tuple[int, int], np.ndarray]:
    """Decode a region file into ``{(cx, cz): (384, 16, 16) uint16 block array}``."""
    with open(path, "rb") as fh:
        data = fh.read()
    if len(data) < 8192:
        return {}
    locations = struct.unpack(">1024I", data[:4096])
    out: Dict[Tuple[int, int], np.ndarray] = {}
    for index, loc in enumerate(locations):
        if loc == 0:
            continue
        offset = (loc >> 8) * 4096
        if offset + 5 > len(data):
            continue
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        comp = data[offset + 4]
        payload = data[offset + 5 : offset + 4 + length]
        if comp == 1:
            raw = zlib.decompress(payload)
        elif comp == 2:
            raw = zlib.decompress(payload)
        elif comp == 3:
            raw = payload
        else:
            continue
        root = read_nbt(raw)["value"]
        cx, cz = root["xPos"][1], root["zPos"][1]
        blocks = decode_chunk_blocks(root)
        out[(cx, cz)] = blocks
    return out


def decode_chunk_blocks(root: dict) -> np.ndarray:
    """Turn one chunk's sections back into a (384, 16, 16) array of block names."""
    blocks = np.zeros((384, 16, 16), dtype=np.uint16)
    for sec in root.get("sections", (9, []))[1]:
        y = sec["Y"][1]
        bs = sec["block_states"][1]
        palette = [entry["Name"][1] for entry in bs["palette"][1]]
        words = np.asarray(bs["data"][1], dtype=np.uint64) if "data" in bs else np.zeros(0, dtype=np.uint64)
        count = len(palette)
        bits = max(4, int(np.ceil(np.log2(count)))) if count > 1 else 0
        if bits == 0:
            idx = np.zeros(4096, dtype=np.int64)
        else:
            epw = 64 // bits
            mask = (1 << bits) - 1
            flat = np.zeros(len(words) * epw, dtype=np.int64)
            for i, w in enumerate(words):
                v = int(w)
                for k in range(epw):
                    flat[i * epw + k] = (v >> (bits * k)) & mask
            idx = flat[:4096]
        names = np.array([_block_id(name) for name in palette], dtype=np.uint16)
        values = names[idx].reshape(16, 16, 16)
        yi = y * 16 - MIN_Y
        if 0 <= yi and yi + 16 <= 384:
            blocks[yi : yi + 16] = values
    return blocks


def _block_id(namespaced: str) -> int:
    name = namespaced.split(":")[-1]
    if name == "air":
        return 0
    key = {"short_grass": "grass"}.get(name, name)
    from ..core.materials import BLOCK_ID

    return BLOCK_ID.get(key, 1)


def render_top_down(region_dir: str, *, max_cells: int = 1200, show_vegetation: bool = True):
    """Render every region file in ``region_dir`` into one RGB image array."""
    files = sorted(f for f in os.listdir(region_dir) if f.endswith(".mca"))
    if not files:
        raise FileNotFoundError(f"no .mca files in {region_dir}")
    all_chunks: Dict[Tuple[int, int], np.ndarray] = {}
    for f in files:
        all_chunks.update(read_region(os.path.join(region_dir, f)))
    if not all_chunks:
        raise ValueError("region files contained no chunks")

    xs = [c[0] for c in all_chunks]
    zs = [c[1] for c in all_chunks]
    x0, x1 = min(xs), max(xs)
    z0, z1 = min(zs), max(zs)
    w = (x1 - x0 + 1) * 16
    h = (z1 - z0 + 1) * 16
    img = np.zeros((h, w, 3), dtype=np.uint8)

    for (cx, cz), blocks in all_chunks.items():
        surface = _surface_colors(blocks, show_vegetation=show_vegetation)
        ox = (cx - x0) * 16
        oz = (cz - z0) * 16
        img[oz : oz + 16, ox : ox + 16] = surface

    if max(img.shape[:2], default=0) > max_cells:
        from PIL import Image

        scale = max_cells / max(img.shape[:2])
        new = (max(1, int(img.shape[1] * scale)), max(1, int(img.shape[0] * scale)))
        img = np.asarray(Image.fromarray(img).resize(new, Image.LANCZOS))
    return img


def _surface_colors(blocks: np.ndarray, *, show_vegetation: bool = True) -> np.ndarray:
    """Colour each column by the highest non-air block (or the water surface)."""
    out = np.zeros((16, 16, 3), dtype=np.uint8)
    column = blocks[:, :, :]  # (y, z, x)
    height = np.zeros((16, 16), dtype=np.int64)
    top_id = np.zeros((16, 16), dtype=np.uint16)
    water_y = np.full((16, 16), -1, dtype=np.int64)
    water_id = np.zeros((16, 16), dtype=np.uint16)

    non_air = column != 0
    # top solid block (ignore 1-block plants for the landscape colour, remember them)
    solid = non_air & (column != _block_id("minecraft:water"))
    any_idx = np.argmax(non_air[::-1], axis=0)
    top_y = 383 - any_idx
    for z in range(16):
        for x in range(16):
            yi = top_y[z, x]
            top_id[z, x] = column[yi, z, x]
            height[z, x] = yi
            # find water surface (highest water block)
            col = column[:, z, x]
            wmask = col == _block_id("minecraft:water")
            if wmask.any():
                wy = 383 - int(np.argmax(wmask[::-1]))
                if wy >= yi:
                    water_y[z, x] = wy
                    water_id[z, x] = col[wy]

    # shading from the height gradient so relief reads
    gy, gx = np.gradient(height.astype(np.float64))
    shade = np.clip(0.72 + (0.60 * (-gx) + 0.45 * (-gy)) * 0.10, 0.35, 1.5)

    for z in range(16):
        for x in range(16):
            if water_y[z, x] >= 0:
                base = BLOCK_COLORS.get("water", (63, 118, 228))
                depth = max(0, water_y[z, x] - height[z, x])
                f = float(np.clip(1.0 - depth * 0.045, 0.45, 1.0))
                color = (int(base[0] * f), int(base[1] * f), int(base[2] * f + 20))
            else:
                name = block_name(int(top_id[z, x]))
                color = BLOCK_COLORS.get(name, (150, 150, 150))
            s = shade[z, x]
            out[z, x] = (
                int(np.clip(color[0] * s, 0, 255)),
                int(np.clip(color[1] * s, 0, 255)),
                int(np.clip(color[2] * s, 0, 255)),
            )
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Render a top-down map of an exported world.")
    parser.add_argument("world", help="world folder (containing region/)")
    parser.add_argument("--out", default="world_preview.png", help="output PNG path")
    parser.add_argument("--size", type=int, default=1200, help="max output edge in pixels")
    args = parser.parse_args(argv)

    region_dir = os.path.join(args.world, "region")
    if not os.path.isdir(region_dir):
        print(f"no region folder in {args.world}", file=sys.stderr)
        return 2
    img = render_top_down(region_dir, max_cells=args.size)
    from PIL import Image

    Image.fromarray(img).save(args.out)
    print(f"wrote {args.out} ({img.shape[1]}x{img.shape[0]})")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
