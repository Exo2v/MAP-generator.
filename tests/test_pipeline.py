"""End-to-end pipeline tests on small regions (the ones that catch real regressions)."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mg.config import GenerationConfig, RegionInfo, apply_preset, default_config, validate  # noqa: E402
from mg.core.types import BIOMES, MAX_Y, MIN_Y, SEA_LEVEL  # noqa: E402
from mg.generation.biomes import BiomeClassifier, biome_histogram  # noqa: E402
from mg.generation.population import PopulationModel  # noqa: E402
from mg.pipeline import run_pipeline  # noqa: E402


def small_config(**overrides) -> GenerationConfig:
    cfg = default_config()
    cfg.region = RegionInfo(blocks_x=256, blocks_z=256, cell_size=4)
    cfg.seed = 4242
    cfg.erosion["thermal_iterations"] = 6
    cfg.erosion["fluvial_iterations"] = 5
    cfg.erosion["droplets"] = 0
    for key, value in overrides.items():
        section, _, name = key.partition("__")
        getattr(cfg, section)[name] = value
    return cfg


class TestPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = small_config()
        cls.result = run_pipeline(cls.cfg)
        cls.t = cls.result.terrain

    def test_shapes_agree(self):
        t = self.t
        shape = (t.cells_z, t.cells_x)
        for field in (t.heights, t.water_level, t.temperature, t.humidity, t.fertility,
                      t.population, t.soil_depth, t.discharge):
            self.assertEqual(field.shape, shape, "grid fields must share a shape")
        self.assertEqual(t.biome.shape, shape)
        self.assertEqual(t.flow_dir.shape, shape + (2,))
        self.assertEqual(t.water_mask.shape, shape)

    def test_heights_are_plausible(self):
        h = np.asarray(self.t.heights)
        self.assertTrue(np.all(np.isfinite(h)), "no NaNs in the heightfield")
        self.assertGreater(float(h.min()), MIN_Y)
        self.assertLess(float(h.max()), MAX_Y)
        self.assertGreater(float(h.max() - h.min()), 20.0, "terrain should have relief")

    def test_water_is_consistent(self):
        t = self.t
        water = t.water_mask > 0
        if np.any(water):
            wl = np.asarray(t.water_level)[water]
            self.assertTrue(np.all(np.isfinite(wl)), "water surfaces must be numbers")
            self.assertTrue(np.all(wl[wl > -1e8] <= MAX_Y))
            # no water above the terrain it sits on, ignoring the ocean floor margin
            ground = np.asarray(t.heights)[water]
            self.assertTrue(np.all(wl >= ground - 8.0))

    def test_rivers_flow_downhill(self):
        violations = 0
        for river in self.t.rivers:
            d = np.diff(np.asarray(river.points)[:, 2])
            violations += int(np.count_nonzero(d > 0.5))
        self.assertEqual(violations, 0, "river polylines must be monotonically downhill")

    def test_river_widths_grow_downstream(self):
        big = [r for r in self.t.rivers if len(r.points) > 6]
        if not big:
            self.skipTest("no long rivers in the test region")
        river = max(big, key=lambda r: len(r.points))
        widths = np.asarray(river.width)
        self.assertGreaterEqual(widths[-1], widths[0] * 0.99)

    def test_biomes_are_valid_indices(self):
        b = self.t.biome
        self.assertTrue(np.all(b >= 0))
        self.assertTrue(np.all(b < len(BIOMES)))
        hist = biome_histogram(b)
        self.assertGreater(len(hist), 1, "a 256x256 region should have several biomes")
        self.assertAlmostEqual(sum(hist.values()), 1.0, places=5)

    def test_population_fields_in_range(self):
        self.assertTrue(np.all(np.asarray(self.t.population) >= -1e-6))
        self.assertTrue(np.all(np.asarray(self.t.population) <= 1.0 + 1e-6))
        self.assertTrue(np.all(np.asarray(self.t.fertility) >= -1e-6))
        self.assertTrue(np.all(np.asarray(self.t.fertility) <= 1.0 + 1e-6))

    def test_flow_vectors_are_unit_or_zero(self):
        flow = np.asarray(self.t.flow_dir)
        mag = np.hypot(flow[..., 0], flow[..., 1])
        wet = self.t.water_mask > 0
        if np.any(wet):
            self.assertLess(float(np.abs(mag[wet] - 1.0).max()), 1e-3)
        dry = ~wet
        if np.any(dry):
            self.assertLess(float(mag[dry].max()), 1e-6)

    def test_vegetation_records_are_sane(self):
        veg = self.t.vegetation
        if veg is None or len(veg) == 0:
            self.skipTest("no vegetation placed")
        self.assertEqual(veg.shape[1], 5)
        self.assertTrue(np.all(veg[:, 0] >= 0))
        self.assertTrue(np.all(veg[:, 0] < self.t.cells_x))
        self.assertTrue(np.all(veg[:, 1] >= 0))
        self.assertTrue(np.all(veg[:, 1] < self.t.cells_z))
        # sorted by (cell_z, cell_x) - the surface generator relies on this for lookups
        order = np.lexsort((veg[:, 0], veg[:, 1]))
        np.testing.assert_array_equal(order, np.arange(len(veg)))

    def test_pois_are_on_land(self):
        for poi in self.t.pois:
            cx = int(np.clip((poi.x - self.t.region.x0) / self.t.region.cell_size, 0,
                             self.t.cells_x - 1))
            cz = int(np.clip((poi.z - self.t.region.z0) / self.t.region.cell_size, 0,
                             self.t.cells_z - 1))
            self.assertNotEqual(int(self.t.water_mask[cz, cx]), 3,
                                f"{poi.name} was placed in the ocean")

    def test_reproducible_from_seed(self):
        again = run_pipeline(small_config())
        np.testing.assert_allclose(np.asarray(self.t.heights), np.asarray(again.terrain.heights),
                                   rtol=1e-6, atol=1e-6)

    def test_different_seeds_differ(self):
        other = run_pipeline(small_config(seed=99) if False else self._with_seed(999))
        self.assertFalse(np.allclose(np.asarray(self.t.heights),
                                     np.asarray(other.terrain.heights)))

    @staticmethod
    def _with_seed(seed: int) -> GenerationConfig:
        cfg = small_config()
        cfg.seed = seed
        return cfg

    def test_presets_all_run(self):
        for preset in ("archipelago", "desert", "floating_plateau"):
            cfg = small_config()
            apply_preset(cfg, preset)
            cfg.region = RegionInfo(blocks_x=192, blocks_z=192, cell_size=6)
            cfg.erosion["thermal_iterations"] = 4
            cfg.erosion["fluvial_iterations"] = 3
            res = run_pipeline(cfg, include_population=False)
            h = np.asarray(res.terrain.heights)
            self.assertTrue(np.all(np.isfinite(h)), f"{preset} produced NaNs")
            self.assertGreater(float(h.max() - h.min()), 5.0, f"{preset} is flat")


class TestConfig(unittest.TestCase):
    def test_round_trip(self):
        cfg = default_config()
        cfg.seed = 7
        cfg.region = RegionInfo(blocks_x=512, blocks_z=768, cell_size=8)
        cfg.terrain["mountain_height"] = 199.0
        restored = GenerationConfig.from_dict(cfg.to_dict())
        self.assertEqual(restored.seed, 7)
        self.assertEqual(restored.region.blocks_x, 512)
        self.assertEqual(restored.region.cells_x, 64)
        self.assertAlmostEqual(restored.terrain["mountain_height"], 199.0)

    def test_validation_catches_nonsense(self):
        cfg = default_config()
        cfg.region = RegionInfo(blocks_x=32, blocks_z=32, cell_size=4)
        self.assertTrue(validate(cfg))
        cfg = default_config()
        cfg.region = RegionInfo(blocks_x=256, blocks_z=256, cell_size=99)
        self.assertTrue(any("cell size" in w.lower() for w in validate(cfg)))

    def test_apply_preset_changes_values(self):
        cfg = default_config()
        before = cfg.terrain["land_fraction"]
        apply_preset(cfg, "archipelago")
        self.assertNotEqual(cfg.terrain["land_fraction"], before)
        self.assertEqual(cfg.preset, "archipelago")


class TestBiomeClassifier(unittest.TestCase):
    def test_water_biomes_win(self):
        cfg = default_config()
        region = RegionInfo(blocks_x=128, blocks_z=128, cell_size=4)
        h, w = region.cells_z, region.cells_x
        heights = np.full((h, w), 70.0)
        heights[:, :] = 70.0
        water_mask = np.zeros((h, w), dtype=np.uint8)
        water_mask[:, : w // 2] = 3  # left half ocean
        heights[:, : w // 2] = 40.0
        water_level = np.where(water_mask > 0, SEA_LEVEL, -1e9)
        fields = BiomeClassifier(cfg.to_dict(), region, 1).compute(
            heights=heights, water_mask=water_mask, water_level=water_level,
            temperature=np.full((h, w), 0.5), humidity=np.full((h, w), 0.5),
            continentality=np.full((h, w), 0.1), cell_size=4.0,
        )
        names = {BIOMES[int(i)] for i in np.unique(fields.biome)}
        self.assertTrue(names & {"ocean", "deep_ocean", "shallow_coast"},
                        "underwater cells must classify as ocean biomes")
        self.assertFalse(any("ocean" in n for n in names if n not in
                             ("ocean", "deep_ocean", "shallow_coast")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
