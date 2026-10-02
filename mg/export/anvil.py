"""Minecraft Java (Anvil) chunk + region writers.

Layout of a chunk (1.18+ data version):
    DataVersion, xPos, zPos, Status="minecraft:full", LastUpdate, InhabitedTime,
    sections[] -> { Y, block_states {palette, data}, biomes {palette, data},
                    BlockLight, SkyLight (omitted - the game relights) },
    block_entities[], Heightmaps {MOTION_BLOCKING, WORLD_SURFACE}, structures, ...

Everything here is written against the documented format; no external libraries.
"""

from __future__ import annotations

import math
import os
import struct
import zlib
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from ..core.materials import ID_BLOCK, namespaced
from ..core.types import CHUNK, MAX_Y, MIN_Y, MIN_Y as WORLD_MIN_Y, SEA_LEVEL
from .nbt import (
    Byte,
    ByteArray,
    Compound,
    Double,
    Float,
    Int,
    IntArray,
    List,
    Long,
    LongArray,
    String,
    Tag,
    compound_of,
    write_nbt,
)

AIR = 0

# Block ids that need block-state properties to look right in game
BLOCK_PROPERTIES: Dict[str, Dict[str, str]] = {
    "water": {"level": "0"},
    "lava": {"level": "0"},
    "snow": {"layers": "1"},
    "snow_block": {},
    "grass": {},
    "tall_grass": {"half": "lower"},
    "large_fern": {"half": "lower"},
    "lilac": {"half": "lower"},
    "rose_bush": {"half": "lower"},
    "peony": {"half": "lower"},
    "sunflower": {"half": "lower"},
    "bamboo": {"age": "0", "leaves": "none", "stage": "0"},
    "cactus": {"age": "0"},
    "kelp": {"age": "0"},
    "seagrass": {},
    "sugar_cane": {"age": "0"},
    "oak_door": {"facing": "north", "half": "lower", "hinge": "left", "open": "false",
                 "powered": "false"},
    "oak_stairs": {"facing": "north", "half": "bottom", "shape": "straight",
                   "waterlogged": "false"},
    "spruce_stairs": {"facing": "north", "half": "bottom", "shape": "straight",
                      "waterlogged": "false"},
    "oak_slab": {"type": "bottom", "waterlogged": "false"},
    "oak_fence": {"east": "false", "north": "false", "south": "false", "waterlogged": "false",
                  "west": "false"},
    "glass_pane": {"east": "false", "north": "false", "south": "false", "waterlogged": "false",
                   "west": "false"},
    "vine": {"east": "false", "north": "false", "south": "false", "up": "false",
             "west": "false"},
    "lily_pad": {},
    "campfire": {"facing": "north", "lit": "true", "signal_fire": "false",
                 "waterlogged": "false"},
    "lantern": {"hanging": "false", "waterlogged": "false"},
    "torch": {},
    "bell": {"attachment": "floor", "facing": "north", "powered": "false"},
    "sweet_berry_bush": {"age": "3"},
    "mushroom_stem": {"down": "true", "east": "true", "north": "true", "south": "true",
                      "up": "true", "west": "true"},
    "brown_mushroom_block": {"down": "true", "east": "true", "north": "true", "south": "true",
                             "up": "true", "west": "true"},
    "red_mushroom_block": {"down": "true", "east": "true", "north": "true", "south": "true",
                           "up": "true", "west": "true"},
    "muddy_mangrove_roots": {"axis": "y"},
    "oak_log": {"axis": "y"},
    "birch_log": {"axis": "y"},
    "spruce_log": {"axis": "y"},
    "jungle_log": {"axis": "y"},
    "acacia_log": {"axis": "y"},
    "dark_oak_log": {"axis": "y"},
    "mangrove_log": {"axis": "y"},
    "oak_leaves": {"distance": "7", "persistent": "true", "waterlogged": "false"},
    "birch_leaves": {"distance": "7", "persistent": "true", "waterlogged": "false"},
    "spruce_leaves": {"distance": "7", "persistent": "true", "waterlogged": "false"},
    "jungle_leaves": {"distance": "7", "persistent": "true", "waterlogged": "false"},
    "acacia_leaves": {"distance": "7", "persistent": "true", "waterlogged": "false"},
    "dark_oak_leaves": {"distance": "7", "persistent": "true", "waterlogged": "false"},
    "mangrove_leaves": {"distance": "7", "persistent": "true", "waterlogged": "false"},
}

# Biome ids (Minecraft 1.20+/1.21 resource names).  Our internal biome names are mapped
# onto the closest vanilla biome so vanilla clients show the right colours and mobs.
BIOME_TO_VANILLA: Dict[str, str] = {
    "deep_ocean": "deep_ocean",
    "ocean": "ocean",
    "shallow_coast": "warm_ocean",
    "beach": "beach",
    "stony_shore": "stony_shore",
    "river": "river",
    "lake": "river",
    "frozen_river": "frozen_river",
    "snowy_beach": "snowy_beach",
    "snowy_plains": "snowy_plains",
    "snowy_taiga": "snowy_taiga",
    "grove": "grove",
    "taiga": "taiga",
    "old_growth_taiga": "old_growth_pine_taiga",
    "cold_mountains": "snowy_slopes",
    "cold_shrubland": "windswept_gravelly_hills",
    "plains": "plains",
    "meadow": "meadow",
    "temperate_forest": "forest",
    "old_growth_temperate_forest": "dark_forest",
    "temperate_mountains": "windswept_hills",
    "warm_temperate_mountains": "windswept_savanna",
    "swamp": "swamp",
    "mangrove_swamp": "mangrove_swamp",
    "humid_savanna": "savanna",
    "savanna": "savanna",
    "xeric_shrubland": "savanna_plateau",
    "desert": "desert",
    "arid_mountains": "badlands",
    "badlands_mesa": "badlands",
    "jungle": "jungle",
    "tropical_rainforest": "sparse_jungle",
    "sparse_jungle": "sparse_jungle",
    "highland_steppe": "windswept_hills",
    "alpine_peaks": "frozen_peaks",
    "glacier": "snowy_plains",
    "volcanic_highland": "basalt_deltas",
    "salt_flats": "desert",
    "windswept_hills": "windswept_hills",
    "fertile_valley": "sunflower_plains",
    "deep_ocean_floor": "deep_ocean",
}

VANILLA_BIOME_IDS = [
    "the_void", "plains", "sunflower_plains", "snowy_plains", "ice_spikes", "desert",
    "swamp", "mangrove_swamp", "forest", "flower_forest", "birch_forest",
    "dark_forest", "old_growth_birch_forest", "old_growth_pine_taiga",
    "old_growth_spruce_taiga", "taiga", "snowy_taiga", "savanna", "savanna_plateau",
    "windswept_hills", "windswept_gravelly_hills", "windswept_forest",
    "windswept_savanna", "jungle", "sparse_jungle", "bamboo_jungle", "badlands",
    "eroded_badlands", "wooded_badlands", "meadow", "grove", "snowy_slopes",
    "frozen_peaks", "jagged_peaks", "stony_peaks", "river", "frozen_river", "beach",
    "snowy_beach", "stony_shore", "warm_ocean", "lukewarm_ocean", "deep_lukewarm_ocean",
    "ocean", "deep_ocean", "cold_ocean", "deep_cold_ocean", "frozen_ocean",
    "deep_frozen_ocean", "mushroom_fields", "dripstone_caves", "lush_caves",
    "deep_dark", "nether_wastes", "basalt_deltas", "crimson_forest", "warped_forest",
    "soul_sand_valley", "the_end", "end_highlands", "end_midlands", "small_end_islands",
    "end_barrens",
]
_VANILLA_INDEX = {name: i for i, name in enumerate(VANILLA_BIOME_IDS)}


def vanilla_biome_id(name: str) -> int:
    v = BIOME_TO_VANILLA.get(name, name)
    return _VANILLA_INDEX.get(v, _VANILLA_INDEX["plains"])


# --------------------------------------------------------------------------------------
# Palette helpers
# --------------------------------------------------------------------------------------


def pack_bits(indices: np.ndarray, bits: int) -> np.ndarray:  # noqa: D401
    """Pack a flat array of small ints into long-array words (LSB first).

    This is the packing the modern Minecraft chunk format uses: consecutive entries
    share words and never straddle a word boundary.
    """
    if bits <= 0:
        return np.zeros(0, dtype=np.int64)
    entries_per_word = 64 // bits
    n = indices.size
    n_words = math.ceil(n / entries_per_word)
    idx = indices.astype(np.uint64)
    padded = np.zeros(n_words * entries_per_word, dtype=np.uint64)
    padded[:n] = idx
    padded = padded.reshape(n_words, entries_per_word)
    shifts = (np.arange(entries_per_word, dtype=np.uint64) * np.uint64(bits))
    out = np.bitwise_or.reduce(padded << shifts, axis=1)
    # keep the bit pattern intact while making it a signed big-endian long array
    return out.view(np.int64)


def bits_for_palette(size: int) -> int:
    if size <= 1:
        return 0
    return max(4, int(math.ceil(math.log2(size))))


# --------------------------------------------------------------------------------------
# Chunk serialisation
# --------------------------------------------------------------------------------------


def build_chunk_nbt(
    *,
    cx: int,
    cz: int,
    blocks: np.ndarray,
    biomes: np.ndarray,
    surface_y: np.ndarray,
    water_y: np.ndarray,
    data_version: int = 3953,
    version: str = "1.21",
    min_y: int = MIN_Y,
    max_y: int = MAX_Y,
    status: str = "minecraft:full",
    heightmaps: bool = True,
) -> Tag:
    """Build the chunk root compound tag from a ``ChunkBlocks`` block array.

    ``status`` is the chunk generation stage recorded in the save.  Writing
    ``minecraft:full`` means "this chunk is finished, place no features"; writing an
    earlier stage (``minecraft:features``) leaves decoration to the game, which is how a
    modpack's own placed features - Still Life canopies, fallen logs, boulders - get
    applied on load.  That is the mechanism the Ashenfall specification relies on in its
    population section.
    """
    status = status if status.startswith("minecraft:") else f"minecraft:{status}"
    n_sections = (max_y - min_y) // 16
    sections: List[Tag] = []
    block_flat = blocks.reshape(n_sections, 16, 256)  # (sections, y, xz)

    for si in range(n_sections):
        section = block_flat[si]
        if not np.any(section):
            continue
        y_sec = (min_y // 16) + si

        # ---- block palette -----------------------------------------------------------
        uniq, inverse = np.unique(section.reshape(-1), return_inverse=True)
        palette_tags = []
        palette_names: List[str] = []
        for bid in uniq.tolist():
            name = ID_BLOCK.get(int(bid), "stone")
            if int(bid) == AIR:
                palette_names.append("minecraft:air")
                palette_tags.append(compound_of("", [String("Name", "minecraft:air")]))
                continue
            full = namespaced(int(bid), version)
            palette_names.append(full)
            props = BLOCK_PROPERTIES.get(name)
            if props:
                prop_tags = [String(k, v) for k, v in sorted(props.items())]
                palette_tags.append(
                    compound_of("", [String("Name", full),
                                     compound_of("Properties", prop_tags)])
                )
            else:
                palette_tags.append(compound_of("", [String("Name", full)]))

        bits = bits_for_palette(len(uniq))
        # Minecraft expects at least 4 bits per entry in practice; 0 bits only when a
        # single block type fills the section.
        data_words = pack_bits(inverse.astype(np.uint32), bits) if bits else np.zeros(0, dtype=np.int64)
        block_states = compound_of("block_states", [
            List("palette", palette_tags),
            LongArray("data", data_words),
        ])

        # ---- biomes ------------------------------------------------------------------
        # one biome per 4x4x4 cell: take the biome of the topmost block in the column
        bio = biomes.reshape(16, 16) if biomes.ndim == 2 else biomes
        vals = np.zeros(4 * 4 * 4, dtype=np.int32)
        from ..core.types import BIOMES

        for by in range(4):
            for bz in range(4):
                for bx in range(4):
                    idx = (by * 16) + (bz * 4) + bx
                    name = BIOMES[int(bio[bz * 4, bx * 4])] if bio.size else "plains"
                    vals[idx] = vanilla_biome_id(name)
        bu, binv = np.unique(vals, return_inverse=True)
        bb = bits_for_palette(len(bu))
        biomes_tag = compound_of("biomes", [
            List("palette", [String("", f"minecraft:{VANILLA_BIOME_IDS[int(b)]}")
                             for b in bu.tolist()]),
            LongArray("data", pack_bits(binv.astype(np.uint32), bb) if bb else np.zeros(0, dtype=np.int64)),
        ])

        sections.append(compound_of("", [
            Byte("Y", y_sec),
            block_states,
            biomes_tag,
        ]))

    # ---- heightmaps ------------------------------------------------------------------
    hm = build_heightmaps(surface_y, water_y, min_y)

    root = compound_of("", [
        Int("DataVersion", int(data_version)),
        Int("xPos", int(cx)),
        Int("zPos", int(cz)),
        String("Status", status),
        Long("LastUpdate", 0),
        Long("InhabitedTime", 0),
        List("sections", sections),
        List("block_entities", []),
        compound_of("Heightmaps", [LongArray(k, v) for k, v in hm.items()] if heightmaps else []),
        List("PostProcessing", []),
        Compound("structures"),
        List("entities", []),
        Byte("isLightOn", 0),
        List("block_ticks", []),
        List("fluid_ticks", []),
    ])
    return root


def build_heightmaps(surface_y: np.ndarray, water_y: np.ndarray, min_y: int = MIN_Y
                     ) -> Dict[str, np.ndarray]:
    """9-bit heightmaps, packed the same way as block data."""
    surface = np.clip(np.asarray(surface_y, dtype=np.int64), min_y, MAX_Y - 1)
    water = np.asarray(water_y, dtype=np.int64)
    motion = np.where(water > 0, np.maximum(surface, water), surface)
    out: Dict[str, np.ndarray] = {}
    for name, values in (("MOTION_BLOCKING", motion), ("WORLD_SURFACE", surface)):
        v = (values - min_y) + 1  # heightmap values are 1-based
        v = np.clip(v, 1, MAX_Y - min_y)
        idx = v.reshape(-1).astype(np.uint32)
        out[name] = pack_bits(idx, 9)
    return out


# --------------------------------------------------------------------------------------
# Region file (.mca)
# --------------------------------------------------------------------------------------

SECTOR = 4096


class RegionFileWriter:
    """Accumulates chunks and writes a single ``r.X.Z.mca`` region file."""

    def __init__(self, rx: int, rz: int, *, compression: int = 2, version: str = "1.21",
                 data_version: int = 3953, min_y: int = MIN_Y, max_y: int = MAX_Y,
                 chunk_status: str = "minecraft:full"):
        self.rx = rx
        self.rz = rz
        self.compression = compression
        self.version = version
        self.data_version = data_version
        self.min_y = min_y
        self.max_y = max_y
        # "minecraft:full" = finished chunk, no further decoration; an earlier stage
        # (e.g. "minecraft:features") leaves decoration to the game, so a modpack's own
        # placed features run when the chunk loads.
        self.chunk_status = chunk_status
        self.chunks: Dict[int, bytes] = {}
        self.sizes: Dict[int, int] = {}
        # How many of ``chunks`` are already on disk.  A region is flushed when the
        # number of *unwritten* chunks reaches the batch size - counting the adopted
        # ones again would rewrite the whole 1 MB file once per chunk.
        self.written = 0

    def adopt_existing(self, path: str) -> int:
        """Pull the chunks already present in ``path`` into this writer.

        A large region is flushed in batches to keep memory flat, which means the same
        ``.mca`` can be opened more than once during one export.  Re-reading the chunks
        that are already on disk (rather than truncating the file) is what stops the
        later batch from silently erasing the earlier one.
        """
        if not os.path.isfile(path):
            return 0
        with open(path, "rb") as fh:
            data = fh.read()
        if len(data) < SECTOR * 2:
            return 0
        locations = struct.unpack(">1024I", data[: SECTOR])
        adopted = 0
        for index, loc in enumerate(locations):
            if loc == 0 or (loc >> 8) == 0:
                continue
            if loc & 0x80000000:  # chunk lives in the .mcc overflow file
                continue
            offset = (loc >> 8) * SECTOR
            if offset + 5 > len(data):
                continue
            (length,) = struct.unpack(">I", data[offset : offset + 4])
            if length <= 1 or offset + 4 + length > len(data):
                continue
            self.chunks[index] = data[offset + 5 : offset + 4 + length]
            self.sizes[index] = len(self.chunks[index])
            adopted += 1
        self.written = len(self.chunks)   # what we just read back is on disk already
        return adopted

    def add_chunk(self, cx: int, cz: int, chunk_blocks) -> None:
        nbt = build_chunk_nbt(
            cx=cx,
            cz=cz,
            blocks=chunk_blocks.blocks,
            biomes=chunk_blocks.biomes,
            surface_y=chunk_blocks.surface_y,
            water_y=chunk_blocks.water_y,
            data_version=self.data_version,
            version=self.version,
            min_y=self.min_y,
            max_y=self.max_y,
            status=self.chunk_status,
        )
        import io

        raw = write_nbt(nbt, gzipped=False)
        payload = zlib.compress(raw, self.compression)
        index = (cx & 31) + (cz & 31) * 32
        self.chunks[index] = payload
        self.sizes[index] = len(payload)

    @property
    def chunk_count(self) -> int:
        return len(self.chunks)

    @property
    def pending(self) -> int:
        """Chunks held in memory that are not in the file yet."""
        return len(self.chunks) - self.written

    def write(self, path: str) -> str:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        header = bytearray(SECTOR * 2)
        body = bytearray()
        offset_sector = 2
        for index in sorted(self.chunks):
            payload = self.chunks[index]
            total = len(payload) + 5
            sectors = (total + SECTOR - 1) // SECTOR
            if sectors > 255:
                # oversized chunk -> external .mcc file
                ext_name = f"c.{self.rx}.{self.rz}.mcc"
                ext_path = os.path.join(os.path.dirname(path), ext_name)
                with open(ext_path, "ab") as fh:
                    fh.write(struct.pack(">i", len(payload) + 5))
                    fh.write(bytes([2]))
                    fh.write(payload)
                struct.pack_into(">I", header, index * 4, 0x80000000 | 0)
                continue
            struct.pack_into(">I", header, index * 4, (offset_sector << 8) | sectors)
            struct.pack_into(">I", header, SECTOR + index * 4, 0)
            body += struct.pack(">I", len(payload) + 1) + bytes([2]) + payload
            pad = sectors * SECTOR - (len(payload) + 5)
            if pad:
                body += b"\x00" * pad
            offset_sector += sectors
        with open(path, "wb") as fh:
            fh.write(bytes(header))
            fh.write(bytes(body))
        self.written = len(self.chunks)
        return path
