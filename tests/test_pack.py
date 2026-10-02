"""Tests for the world download packer.

These guard the two mistakes that would silently corrupt a release download: publishing a
region file that is still being written, and reusing a shard name so that an already
published shard is overwritten.
"""

from __future__ import annotations

import os
import shutil
import struct
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.pack_world_download import (  # noqa: E402
    _is_complete,
    complete_regions,
    next_free_index,
    occupied_chunks,
    packaged_regions,
    write_shards,
)


def make_region(path: str, chunks: int, payload: int = 64) -> str:
    """A region file with ``chunks`` chunk slots filled, one sector each."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    header = bytearray(4096 * 2)
    body = bytearray()
    for i in range(chunks):
        offset_sector = 2 + i
        struct.pack_into(">I", header, i * 4, (offset_sector << 8) | 1)
        body += struct.pack(">I", payload + 1) + bytes([2]) + b"x" * payload
        body += b"\x00" * (4096 - (payload + 5))
    with open(path, "wb") as fh:
        fh.write(bytes(header))
        fh.write(bytes(body))
    return path


class TestRegionState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mapgen-pack-")
        self.world = os.path.join(self.tmp, "Ashenfall")
        self.region = os.path.join(self.world, "region")
        os.makedirs(self.region, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_occupied_chunks_counts_written_slots(self):
        path = make_region(os.path.join(self.region, "r.0.0.mca"), 5)
        self.assertEqual(occupied_chunks(path), 5)

    def test_only_full_regions_count_as_complete(self):
        make_region(os.path.join(self.region, "r.0.0.mca"), 1024)
        make_region(os.path.join(self.region, "r.0.1.mca"), 1023)
        done = [os.path.basename(p) for p in complete_regions(self.region)]
        self.assertEqual(done, ["r.0.0.mca"],
                         "a half-written region must never be published")

    def test_export_finished_needs_level_dat_and_full_regions(self):
        make_region(os.path.join(self.region, "r.0.0.mca"), 1024)
        self.assertFalse(_is_complete(self.world, self.region, 1),
                         "no level.dat yet, the export is still running")
        open(os.path.join(self.world, "level.dat"), "wb").close()
        self.assertTrue(_is_complete(self.world, self.region, 1))
        make_region(os.path.join(self.region, "r.0.1.mca"), 10)
        self.assertFalse(_is_complete(self.world, self.region, 1),
                         "a partially written region means the export is not done")


class TestShards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mapgen-shard-")
        self.world = os.path.join(self.tmp, "Ashenfall")
        self.region = os.path.join(self.world, "region")
        self.out = os.path.join(self.tmp, "out")
        os.makedirs(self.out, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _region_files(self, count: int, size: int = 1 << 20):
        files = []
        for i in range(count):
            path = os.path.join(self.region, f"r.{i}.0.mca")
            os.makedirs(self.region, exist_ok=True)
            with open(path, "wb") as fh:      # incompressible, so sizes are predictable
                fh.write(os.urandom(size))
            files.append(path)
        return files

    def test_shards_cover_every_region_once_and_respect_the_budget(self):
        files = self._region_files(8)
        written = write_shards(self.out, self.world, "W", files, start_index=1,
                               budget=3 << 20)
        self.assertGreater(len(written), 1, "8 MB of regions must not fit one 3 MB shard")
        seen = []
        for path, group in written:
            self.assertLessEqual(os.path.getsize(path), 3 << 20,
                                 f"{os.path.basename(path)} broke the size budget")
            with zipfile.ZipFile(path) as z:
                seen += [os.path.basename(n) for n in z.namelist()]
        self.assertEqual(sorted(seen), sorted(os.path.basename(f) for f in files),
                         "shards must partition the regions: no gaps, no duplicates")

    def test_next_free_index_skips_published_shards(self):
        for name in ("W_regions01.zip", "W_regions02.zip"):
            open(os.path.join(self.out, name), "wb").close()
        self.assertEqual(next_free_index(self.out, "W", 1), 3)

    def test_write_shards_never_overwrites_an_existing_shard(self):
        files = self._region_files(2, size=1 << 16)
        first = write_shards(self.out, self.world, "W", files, start_index=1,
                             budget=10 << 20)
        again = write_shards(self.out, self.world, "W", files[:1], start_index=1,
                             budget=10 << 20)
        self.assertNotEqual(first[0][0], again[0][0],
                            "a second run must add a shard, not replace one")

    def test_packaged_regions_reads_back_the_shards(self):
        files = self._region_files(3, size=1 << 14)
        write_shards(self.out, self.world, "W", files, start_index=1, budget=10 << 20)
        done = packaged_regions(self.out, "W")
        self.assertEqual(done, {os.path.basename(f) for f in files})


if __name__ == "__main__":
    unittest.main()
