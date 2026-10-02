"""Export tests: NBT encoding, bit packing, Anvil round-trip, schematics, bundles."""

from __future__ import annotations

import gzip
import os
import shutil
import struct
import sys
import tempfile
import unittest
import zlib

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mg.config import RegionInfo, default_config  # noqa: E402
from mg.core.materials import BLOCK_ALIASES, BLOCK_ID, ID_BLOCK, namespaced  # noqa: E402
from mg.core.types import BIOMES, CHUNK, MAX_Y, MIN_Y, SEA_LEVEL  # noqa: E402
from mg.export.anvil import RegionFileWriter, bits_for_palette, pack_bits  # noqa: E402
from mg.export.nbt import (  # noqa: E402
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
    compound_of,
    read_nbt,
    write_nbt,
)
from mg.export.schematic import build_schematic, export_schematic, export_worldpainter_bundle  # noqa: E402
from mg.export.world import export_world  # noqa: E402
from mg.generation.surface import TerrainSampler  # noqa: E402
from mg.pipeline import run_pipeline  # noqa: E402


def _palette_names(palette_entries):
    """Palette entries come back either as raw strings (List of String) or compounds."""
    names = []
    for entry in palette_entries:
        if isinstance(entry, str):
            names.append(entry)
        elif isinstance(entry, tuple):
            names.append(entry[1])
        elif isinstance(entry, dict):
            value = entry.get("Name")
            names.append(value[1] if isinstance(value, tuple) else value)
        else:  # pragma: no cover
            names.append(str(entry))
    return names


def unpack_bits(words, bits: int, count: int) -> np.ndarray:
    """Reference decoder for the Minecraft long-array packing."""
    if bits == 0:
        return np.zeros(count, dtype=np.int64)
    per_word = 64 // bits
    mask = (1 << bits) - 1
    out = np.zeros(len(words) * per_word, dtype=np.int64)
    for i, word in enumerate(np.asarray(words, dtype=np.uint64)):
        v = int(word)
        for k in range(per_word):
            out[i * per_word + k] = (v >> (bits * k)) & mask
    return out[:count]


def _stub_chunk():
    """A minimal ChunkBlocks-like object for writer-level tests."""
    class _Stub:
        blocks = np.full((384, 16, 16), 0, dtype=np.uint16)
        blocks[:64] = 1
        biomes = np.zeros((16, 16), dtype=np.uint8)
        surface_y = np.full((16, 16), 63, dtype=np.int16)
        water_y = np.full((16, 16), -1, dtype=np.int16)
    return _Stub()


class TestNbt(unittest.TestCase):
    def test_every_type_round_trips(self):
        root = compound_of("Root", [
            Byte("b", 7),
            Int("i", -12345),
            Long("l", 2 ** 40),
            Float("f", 1.5),
            Double("d", -3.25),
            String("s", "hello ✓"),
            ByteArray("ba", np.array([1, 2, 3, -4], dtype=np.int8)),
            IntArray("ia", np.array([9, 8, 7], dtype=np.int32)),
            LongArray("la", np.array([2 ** 60, -5], dtype=np.int64)),
            List("li", [Int("", 1), Int("", 2)]),
            compound_of("c", [Int("x", 4)]),
        ])
        parsed = read_nbt(write_nbt(root))
        data = parsed["value"]
        self.assertEqual(data["b"][1], 7)
        self.assertEqual(data["i"][1], -12345)
        self.assertEqual(data["l"][1], 2 ** 40)
        self.assertAlmostEqual(data["f"][1], 1.5, places=6)
        self.assertAlmostEqual(data["d"][1], -3.25)
        self.assertEqual(data["s"][1], "hello ✓")
        np.testing.assert_array_equal(data["ba"][1], [1, 2, 3, -4])
        np.testing.assert_array_equal(data["ia"][1], [9, 8, 7])
        np.testing.assert_array_equal(np.asarray(data["la"][1]).astype(np.int64),
                                      [2 ** 60, -5])
        self.assertEqual(len(data["li"][1]), 2)
        self.assertEqual(data["c"][1]["x"][1], 4)

    def test_large_negative_long_survives(self):
        root = compound_of("R", [LongArray("a", np.array([-1, -(2 ** 62)], dtype=np.int64))])
        parsed = read_nbt(write_nbt(root))
        np.testing.assert_array_equal(np.asarray(parsed["value"]["a"][1]).astype(np.int64),
                                      [-1, -(2 ** 62)])

    def test_gzip_header_is_deterministic(self):
        root = compound_of("R", [Int("a", 1)])
        self.assertEqual(write_nbt(root), write_nbt(root))


class TestPackBits(unittest.TestCase):
    def test_round_trip_sizes(self):
        for bits in (4, 5, 6, 8, 9, 12):
            count = 4096
            values = (np.arange(count) % (1 << bits)).astype(np.uint32)
            packed = pack_bits(values, bits)
            self.assertEqual(len(packed), (count * bits + 63) // 64 // 1 * 1 if False else
                             int(np.ceil(count / (64 // bits))))
            back = unpack_bits(packed, bits, count)
            np.testing.assert_array_equal(back, values.astype(np.int64))

    def test_zero_bits(self):
        self.assertEqual(len(pack_bits(np.zeros(4096, dtype=np.uint32), 0)), 0)

    def test_bits_for_palette(self):
        self.assertEqual(bits_for_palette(1), 0)
        self.assertEqual(bits_for_palette(2), 4)
        self.assertEqual(bits_for_palette(16), 4)
        self.assertEqual(bits_for_palette(17), 5)
        self.assertEqual(bits_for_palette(300), 9)


class TestChunkExport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cfg = default_config()
        cfg.region = RegionInfo(blocks_x=128, blocks_z=128, cell_size=4)
        cfg.seed = 31337
        cfg.erosion["thermal_iterations"] = 4
        cfg.erosion["fluvial_iterations"] = 4
        cls.cfg = cfg
        cls.result = run_pipeline(cfg)
        cls.sampler = TerrainSampler(cls.result.terrain, seed=cfg.seed, cfg=cfg.to_dict())

    def _decode_region(self, path):
        with open(path, "rb") as fh:
            data = fh.read()
        locations = struct.unpack(">1024I", data[:4096])
        chunks = {}
        for loc in locations:
            if not loc:
                continue
            offset = (loc >> 8) * 4096
            (length,) = struct.unpack(">I", data[offset:offset + 4])
            payload = data[offset + 5: offset + 4 + length]
            root = read_nbt(zlib.decompress(payload))["value"]
            chunks[(root["xPos"][1], root["zPos"][1])] = root
        return chunks

    def test_chunk_round_trip_is_lossless(self):
        chunk = self.sampler.generate_chunk(0, 0)
        writer = RegionFileWriter(0, 0, version="1.21")
        writer.add_chunk(0, 0, chunk)
        with tempfile.TemporaryDirectory() as tmp:
            path = writer.write(os.path.join(tmp, "region", "r.0.0.mca"))
            decoded = self._decode_region(path)
        self.assertIn((0, 0), decoded)
        root = decoded[(0, 0)]

        rebuilt = np.zeros_like(chunk.blocks)
        for sec in root["sections"][1]:
            y = sec["Y"][1]
            bs = sec["block_states"][1]
            palette = [entry["Name"][1].split(":")[-1] for entry in bs["palette"][1]]
            words = np.asarray(bs["data"][1], dtype=np.uint64) if "data" in bs else np.zeros(0, np.uint64)
            bits = max(4, int(np.ceil(np.log2(len(palette))))) if len(palette) > 1 else 0
            idx = unpack_bits(words, bits, 4096)
            ids = np.array([
                (0 if n == "air" else BLOCK_ID.get({"short_grass": "grass"}.get(n, n), 1))
                for n in palette
            ], dtype=np.uint16)
            self.assertLess(int(idx.max(initial=0)), len(palette))
            rebuilt[y * 16 - MIN_Y: y * 16 - MIN_Y + 16] = ids[idx].reshape(16, 16, 16)
        np.testing.assert_array_equal(rebuilt, chunk.blocks)

    def test_heightmaps_match_terrain(self):
        chunk = self.sampler.generate_chunk(1, 1)
        writer = RegionFileWriter(0, 0)
        writer.add_chunk(1, 1, chunk)
        with tempfile.TemporaryDirectory() as tmp:
            path = writer.write(os.path.join(tmp, "r.0.0.mca"))
            root = self._decode_region(path)[(1, 1)]
        hm = root["Heightmaps"][1]["WORLD_SURFACE"][1]
        values = unpack_bits(hm, 9, 256).reshape(16, 16)
        expected = (chunk.surface_y.astype(np.int64) - MIN_Y) + 1
        np.testing.assert_array_equal(values.astype(np.int64), np.clip(expected, 1, MAX_Y - MIN_Y))

    def test_biomes_are_vanilla_ids(self):
        chunk = self.sampler.generate_chunk(2, 2)
        writer = RegionFileWriter(0, 0)
        writer.add_chunk(2, 2, chunk)
        with tempfile.TemporaryDirectory() as tmp:
            root = self._decode_region(writer.write(os.path.join(tmp, "r.0.0.mca")))[(2, 2)]
        section = root["sections"][1][-1]
        palette = _palette_names(section["biomes"][1]["palette"][1])
        self.assertTrue(all(p.startswith("minecraft:") for p in palette), palette)

    def test_sections_never_contain_unknown_blocks(self):
        chunk = self.sampler.generate_chunk(3, 3)
        writer = RegionFileWriter(0, 0)
        writer.add_chunk(3, 3, chunk)
        with tempfile.TemporaryDirectory() as tmp:
            root = self._decode_region(writer.write(os.path.join(tmp, "r.0.0.mca")))[(3, 3)]
        known = set(ID_BLOCK.values())
        # the exporter renames a few blocks for newer versions - those are still valid
        aliased = {v for table in BLOCK_ALIASES.values() for v in table.values()}
        aliased |= {v for table in BLOCK_ALIASES.values() for v in table.keys()}
        for sec in root["sections"][1]:
            for entry in sec["block_states"][1]["palette"][1]:
                name = entry["Name"][1].split(":")[-1]
                self.assertTrue(name in known or name in aliased, f"unknown block {name}")

    def test_region_batches_do_not_lose_chunks(self):
        writer = RegionFileWriter(0, 0)
        produced = []
        for i in range(24):
            cx, cz = i % 8, i // 8
            chunk = self.sampler.generate_chunk(cx, cz)
            writer.add_chunk(cx, cz, chunk)
            produced.append((cx, cz))
        with tempfile.TemporaryDirectory() as tmp:
            path = writer.write(os.path.join(tmp, "r.0.0.mca"))
            first = set(self._decode_region(path))
            # reopen, adopt and add more - simulating a second export batch
            writer2 = RegionFileWriter(0, 0)
            adopted = writer2.adopt_existing(path)
            self.assertEqual(adopted, len(first))
            for i in range(24, 40):
                cx, cz = i % 8, i // 8
                writer2.add_chunk(cx, cz, self.sampler.generate_chunk(cx, cz))
            writer2.write(path)
            merged = set(self._decode_region(path))
        self.assertEqual(merged, {((cx), (cz)) for cx, cz in
                                  [(i % 8, i // 8) for i in range(40)]})


class TestResumeExport(unittest.TestCase):
    """The exporter must be able to pick up an interrupted world without losing chunks.

    This is the one that matters for the Ashenfall build: a quarter of a million chunks
    takes tens of minutes, so an interruption is normal and resuming has to be safe.  The
    trap is that a resumed run skips chunks it has *not* regenerated, and the region writer
    for that file starts empty - if it does not adopt what is on disk it will rewrite the
    file down to only the chunks it did regenerate, silently destroying the rest.
    """

    @classmethod
    def setUpClass(cls):
        cfg = default_config()
        # 256 x 256 blocks at cell 16 -> 16 x 16 chunks, all inside one region file
        cfg.region = RegionInfo(blocks_x=256, blocks_z=256, cell_size=16)
        cfg.seed = 4242
        cfg.name = "Resume Test"
        cls.cfg = cfg
        cls.result = run_pipeline(cfg)
        cls.tmp = tempfile.mkdtemp(prefix="mapgen-resume-")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _region_path(self, world_dir):
        region_dir = os.path.join(world_dir, "region")
        files = [f for f in os.listdir(region_dir) if f.endswith(".mca")]
        self.assertEqual(len(files), 1, "expected everything in one region file")
        return os.path.join(region_dir, files[0])

    @staticmethod
    def _occupied(path):
        with open(path, "rb") as fh:
            header = fh.read(4096)
        return {i for i in range(1024)
                if header[i * 4: i * 4 + 3] != b"\x00\x00\x00"}

    @staticmethod
    def _clear_entries(path, indices):
        """Zero the location entries, i.e. pretend those chunks were never written."""
        with open(path, "rb") as fh:
            data = bytearray(fh.read())
        for i in indices:
            data[i * 4: i * 4 + 4] = b"\x00\x00\x00\x00"
        with open(path, "wb") as fh:
            fh.write(data)

    def test_resume_keeps_every_chunk(self):
        cfg = type(self.cfg).from_dict(self.cfg.to_dict())
        cfg.export["resume"] = True
        cfg.export["world_name"] = "Resume Test"
        cfg.export["generate_png_maps"] = False
        cfg.export["write_bundle"] = False
        cfg.export["write_level_dat"] = False

        first = export_world(self.result.terrain, cfg.to_dict(), self.tmp, seed=cfg.seed)
        path = self._region_path(first.world_dir)
        written = self._occupied(path)
        self.assertEqual(len(written), 256, "the first export wrote all 256 chunks")

        # Simulate an interruption: the last-written chunks are missing, the rest are on
        # disk.  A resumed run therefore skips most chunks and regenerates a few.
        missing = sorted(written)[-56:]
        self._clear_entries(path, missing)
        self.assertEqual(len(self._occupied(path)), 200)

        second = export_world(self.result.terrain, cfg.to_dict(), self.tmp, seed=cfg.seed)
        path = self._region_path(second.world_dir)
        after = self._occupied(path)
        self.assertEqual(
            after, written,
            "resuming must restore the missing chunks without discarding the chunks it "
            "skipped - otherwise the export can never finish",
        )


class TestRegionFlushBatching(unittest.TestCase):
    """A region file must be written in batches, not once per chunk.

    The writer flushes when it holds ``chunk_batch`` chunks that are not on disk yet.
    Counting the chunks it *adopted* from disk as pending as well would make every
    later chunk rewrite the whole file - on an 8,000 x 8,000 continent that is 512
    extra megabyte-sized writes per region and roughly doubles the export time.
    """

    @classmethod
    def setUpClass(cls):
        cfg = default_config()
        # 512 x 512 blocks at cell 16 -> 32 x 32 chunks: exactly one region file
        cfg.region = RegionInfo(blocks_x=512, blocks_z=512, cell_size=16)
        cfg.seed = 99
        cfg.name = "Flush Test"
        cfg.export["chunk_batch"] = 512
        cfg.export["generate_png_maps"] = False
        cfg.export["write_bundle"] = False
        cfg.export["write_level_dat"] = False
        cfg.export["worldpainter_bundle"] = False
        cls.cfg = cfg
        cls.result = run_pipeline(cfg)
        cls.tmp = tempfile.mkdtemp(prefix="mapgen-flush-")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_one_region_is_written_twice(self):
        from mg.export import anvil

        calls = []
        original = anvil.RegionFileWriter.write

        def counting(self, path):
            calls.append(len(self.chunks))
            return original(self, path)

        anvil.RegionFileWriter.write = counting
        try:
            export_world(self.result.terrain, self.cfg.to_dict(), self.tmp, seed=self.cfg.seed)
        finally:
            anvil.RegionFileWriter.write = original

        self.assertEqual(len(calls), 2,
                         f"1,024 chunks with a batch of 512 must flush twice, got {calls}")
        self.assertEqual(sorted(calls), [512, 1024])

    def test_pending_drops_to_zero_after_a_flush(self):
        writer = RegionFileWriter(0, 0)
        for i in range(600):
            writer.chunks[i] = b"x"
        self.assertEqual(writer.pending, 600)
        writer.write(os.path.join(self.tmp, "r.0.0.mca"))
        self.assertEqual(writer.pending, 0, "a flushed region has nothing pending")

    def test_adopting_counts_as_written(self):
        path = os.path.join(self.tmp, "r.1.1.mca")
        first = RegionFileWriter(1, 1)
        for i in range(8):
            first.add_chunk(i, 0, _stub_chunk())
        first.write(path)

        second = RegionFileWriter(1, 1)
        self.assertEqual(second.adopt_existing(path), 8)
        self.assertEqual(second.pending, 0,
                         "chunks read back from disk must not be queued for a rewrite")


class TestWorldExport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cfg = default_config()
        cfg.region = RegionInfo(blocks_x=128, blocks_z=128, cell_size=4)
        cfg.seed = 777
        cfg.erosion["thermal_iterations"] = 4
        cfg.erosion["fluvial_iterations"] = 4
        cfg.name = "Unit Test World"
        cls.cfg = cfg
        cls.result = run_pipeline(cfg)
        cls.tmp = tempfile.mkdtemp(prefix="mapgen-test-")
        cls.export = export_world(cls.result.terrain, cfg.to_dict(), cls.tmp, seed=cfg.seed)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_world_layout(self):
        root = self.export.world_dir
        self.assertTrue(os.path.isdir(root))
        self.assertTrue(os.path.isfile(os.path.join(root, "level.dat")))
        self.assertTrue(os.path.isfile(os.path.join(root, "session.lock")))
        self.assertTrue(os.path.isfile(os.path.join(root, "mapgen.json")))
        self.assertTrue(os.path.isfile(os.path.join(root, "README.txt")))
        self.assertTrue(os.path.isdir(os.path.join(root, "region")))
        region_files = [f for f in os.listdir(os.path.join(root, "region")) if f.endswith(".mca")]
        self.assertTrue(region_files)

    def test_level_dat_is_readable(self):
        with open(os.path.join(self.export.world_dir, "level.dat"), "rb") as fh:
            raw = fh.read()
        self.assertEqual(raw[:2], b"\x1f\x8b", "level.dat must be gzipped")
        parsed = read_nbt(raw)
        data = parsed["value"]["Data"][1]
        self.assertEqual(data["LevelName"][1], "Unit Test World")
        self.assertEqual(data["RandomSeed"][1], 777)
        self.assertGreater(data["SpawnY"][1], MIN_Y)
        self.assertLess(data["SpawnY"][1], MAX_Y)

    def test_config_is_reproducible(self):
        import json

        with open(os.path.join(self.export.world_dir, "mapgen.json"), encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual(saved["seed"], 777)
        self.assertEqual(saved["region"]["blocks_x"], 128)
        regen = run_pipeline(type(self.cfg).from_dict(saved))
        np.testing.assert_allclose(np.asarray(self.result.terrain.heights),
                                   np.asarray(regen.terrain.heights), rtol=1e-6, atol=1e-6)

    def test_maps_bundle_written(self):
        maps_dir = os.path.join(self.export.world_dir, "maps")
        self.assertTrue(os.path.isdir(maps_dir))
        pngs = [f for f in os.listdir(maps_dir) if f.endswith(".png")]
        self.assertGreaterEqual(len(pngs), 3)
        self.assertIn("legend.json", os.listdir(maps_dir))

    def test_worldpainter_bundle(self):
        base = os.path.join(self.export.world_dir, "worldpainter")
        self.assertTrue(os.path.isfile(os.path.join(base, "heightmap.png")))
        self.assertTrue(os.path.isfile(os.path.join(base, "water.png")))
        from PIL import Image

        img = Image.open(os.path.join(base, "heightmap.png"))
        self.assertEqual(img.mode, "I;16")

    def test_schematic_round_trip(self):
        sampler = TerrainSampler(self.result.terrain, seed=777, cfg=self.cfg.to_dict())
        with tempfile.TemporaryDirectory() as tmp:
            path = export_schematic(sampler, os.path.join(tmp, "sel.schem"),
                                    chunk_x0=0, chunk_z0=0, chunks_x=2, chunks_z=2)
            with open(path, "rb") as fh:
                root = read_nbt(fh.read())["value"]
        self.assertEqual(root["Version"][1], 2)
        self.assertEqual(root["Width"][1], 32)
        self.assertEqual(root["Length"][1], 32)
        self.assertEqual(len(root["BlockData"][1]),
                         root["Width"][1] * root["Height"][1] * root["Length"][1])
        self.assertGreater(len(root["Palette"][1]), 1)

    def test_schematic_varint_stream_decodes(self):
        blocks = np.zeros((4, 3, 3), dtype=np.uint16)
        blocks[0, :, :] = BLOCK_ID["stone"]
        blocks[1, :, :] = BLOCK_ID["dirt"]
        blocks[2, 0, 0] = BLOCK_ID["oak_log"]
        root = build_schematic(blocks=blocks, name="t")
        data = root.value["BlockData"].value.view(np.uint8).astype(np.int64)
        # decode var-ints back to palette indices
        values, shift, current = [], 0, 0
        for byte in data.tolist():
            b = byte & 0xFF
            current |= (b & 0x7F) << shift
            if b & 0x80:
                shift += 7
            else:
                values.append(current)
                shift, current = 0, 0
        self.assertEqual(len(values), 4 * 3 * 3)
        self.assertEqual(values[0], values[1], "the whole stone layer should share an index")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestSchematicTiles(unittest.TestCase):
    """The schematic bundle hands out spread-out tiles, not one corner strip."""

    def test_spread_covers_region(self):
        from mg.export.schematic import _spread_tiles

        for nx, nz, limit in ((8, 8, 12), (8, 8, 4), (8, 8, 3), (1, 1, 6),
                              (16, 8, 12), (4, 4, 5), (24, 24, 12)):
            picks = _spread_tiles(nx, nz, limit)
            self.assertEqual(len(picks), min(limit, nx * nz))
            self.assertEqual(len(set(picks)), len(picks), picks)
            for x, z in picks:
                self.assertTrue(0 <= x < nx and 0 <= z < nz)

    def test_tiles_are_written(self):
        from mg.export.schematic import export_schematic_tiles

        cfg = default_config()
        cfg.region = RegionInfo(blocks_x=256, blocks_z=256, cell_size=4)
        cfg.seed = 909
        cfg.erosion["thermal_iterations"] = 4
        cfg.erosion["fluvial_iterations"] = 4
        terrain = run_pipeline(cfg, include_population=False).terrain
        sampler = TerrainSampler(terrain, seed=cfg.seed, cfg=cfg.to_dict())

        with tempfile.TemporaryDirectory() as tmp:
            paths = export_schematic_tiles(sampler, os.path.join(tmp, "s"),
                                           tile=2, limit=6)
            self.assertTrue(paths)
            self.assertLessEqual(len(paths), 6)
            for path in paths:
                self.assertTrue(os.path.isfile(path), path)
                with open(path, "rb") as fh:
                    root = read_nbt(fh.read())["value"]
                width, height, length = (root["Width"][1], root["Height"][1],
                                         root["Length"][1])
                self.assertLessEqual(width, 32)
                self.assertLessEqual(length, 32)
                self.assertGreater(height, 0)
