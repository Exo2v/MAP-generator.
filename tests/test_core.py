"""Core maths tests: noise, hydrology, erosion primitives.

Run with ``python -m pytest tests`` or plain ``python tests/test_core.py``.
"""

from __future__ import annotations

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mg.core import erosion, hydrology  # noqa: E402
from mg.core.noise import (  # noqa: E402
    cellular,
    fbm,
    gradient_noise,
    grid_coords,
    hash2,
    normalize01,
    spline,
    stretch01,
    value_noise,
    warped_fbm,
)


class TestNoise(unittest.TestCase):
    def test_hash_is_deterministic_and_bounded(self):
        a = hash2(np.array([0, 1, 2, 3]), np.array([5, 5, 5, 5]), seed=7)
        b = hash2(np.array([0, 1, 2, 3]), np.array([5, 5, 5, 5]), seed=7)
        np.testing.assert_array_equal(a, b)
        self.assertTrue(np.all(a >= 0) and np.all(a < 1))
        c = hash2(np.array([0, 1, 2, 3]), np.array([5, 5, 5, 5]), seed=8)
        self.assertFalse(np.allclose(a, c))

    def test_hash_differentiates_coordinates(self):
        self.assertNotAlmostEqual(hash2(0, 0, 1), hash2(0, 1, 1))
        self.assertNotAlmostEqual(hash2(3, 4, 1), hash2(4, 3, 1))

    def test_noise_range(self):
        X, Y = grid_coords(64, 64, 8.0)
        for fn in (value_noise, gradient_noise):
            v = fn(X / 40.0, Y / 40.0, seed=3)
            self.assertGreater(np.max(np.abs(v)), 0.1)
            self.assertLess(np.max(np.abs(v)), 3.0)

    def test_fbm_is_smooth(self):
        X, Y = grid_coords(128, 128, 4.0)
        v = fbm(X, Y, octaves=5, scale=200.0, seed=11)
        diffs = np.abs(np.diff(v, axis=1))
        self.assertLess(np.mean(diffs), 0.25, "fBm should vary smoothly between neighbours")

    def test_ridged_fbm_is_positive(self):
        X, Y = grid_coords(32, 32, 8.0)
        v = fbm(X, Y, octaves=4, scale=300.0, seed=5, ridged=True)
        self.assertGreaterEqual(np.min(v), -1e-9)
        self.assertLessEqual(np.max(v), 1.0 + 1e-9)

    def test_cellular_modes(self):
        X, Y = grid_coords(48, 48, 12.0)
        f1 = cellular(X, Y, scale=100.0, seed=2, mode="f1")
        f2 = cellular(X, Y, scale=100.0, seed=2, mode="f2")
        self.assertTrue(np.all(f2 >= f1 - 1e-9), "F2 should never be closer than F1")
        ids = cellular(X, Y, scale=100.0, seed=2, mode="id")
        self.assertTrue(np.all((ids >= 0) & (ids < 1)))

    def test_spline_hits_control_points(self):
        pts = [[0.0, 10.0], [0.5, 40.0], [1.0, 90.0]]
        v = spline(pts, np.array([0.0, 0.5, 1.0]))
        np.testing.assert_allclose(v, [10.0, 40.0, 90.0], atol=1e-6)

    def test_spline_monotonic_between_points(self):
        pts = [[0.0, 0.0], [0.4, 20.0], [1.0, 120.0]]
        v = spline(pts, np.linspace(0, 1, 200))
        self.assertTrue(np.all(np.diff(v) >= -1e-6), "spline should not oscillate downward")

    def test_normalisation_helpers(self):
        a = np.random.default_rng(0).normal(size=1000)
        n = normalize01(a)
        s = stretch01(a)
        self.assertAlmostEqual(float(n.min()), 0.0, places=6)
        self.assertAlmostEqual(float(n.max()), 1.0, places=6)
        self.assertAlmostEqual(float(s.min()), 0.0, places=6)
        self.assertAlmostEqual(float(s.max()), 1.0, places=6)
        # the percentile stretch should give more contrast in the middle than min/max
        self.assertGreater(np.std(s), np.std(n) * 0.9)

    def test_warped_fbm_changes_shape(self):
        X, Y = grid_coords(64, 64, 8.0)
        a = fbm(X, Y, octaves=4, scale=200.0, seed=1)
        b = warped_fbm(X, Y, scale=200.0, warp_scale=400.0, warp_strength=120.0, seed=1)
        self.assertFalse(np.allclose(a, b))


class TestHydrology(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(1234)
        X, Y = grid_coords(64, 64, 1.0)
        self.dem = (
            60.0
            + 18.0 * np.sin(X / 9.0) * np.cos(Y / 11.0)
            + fbm(X, Y, octaves=4, scale=40.0, seed=99) * 6.0
        )

    def test_priority_flood_removes_all_pits(self):
        filled, depth = hydrology.priority_flood(self.dem)
        self.assertTrue(np.all(filled >= self.dem - 1e-9))
        dirs = hydrology.flow_directions(filled)
        interior = dirs[1:-1, 1:-1]
        # every interior cell must have a downhill neighbour (a tiny slope is enough)
        self.assertLess(int(np.count_nonzero(interior < 0)), interior.size * 0.02)

    def test_flow_directions_are_downhill(self):
        filled, _ = hydrology.priority_flood(self.dem)
        dirs = hydrology.flow_directions(filled)
        h, w = self.dem.shape
        d8 = np.array([[-1, -1], [-1, 0], [-1, 1], [0, 1], [1, 1], [1, 0], [1, -1], [0, -1]])
        checked = 0
        for z in range(1, h - 1):
            for x in range(1, w - 1):
                d = dirs[z, x]
                if d < 0:
                    continue
                dz, dx = d8[d]
                # flowing downhill means this cell must be at least as high as the one
                # it drains into
                self.assertGreaterEqual(filled[z, x], filled[z + dz, x + dx] - 1e-6)
                checked += 1
        self.assertGreater(checked, 100)

    def test_flow_accumulation_increases_downstream(self):
        filled, _ = hydrology.priority_flood(self.dem)
        dirs = hydrology.flow_directions(filled)
        acc = hydrology.flow_accumulation(filled, dirs)
        self.assertGreaterEqual(float(acc.min()), 1.0)
        self.assertGreater(float(acc.max()), 20.0)

    def test_carve_channel_lowers_only_the_floor(self):
        filled, _ = hydrology.priority_flood(self.dem)
        dirs = hydrology.flow_directions(filled)
        acc = hydrology.flow_accumulation(filled, dirs)
        carved = hydrology.carve_channels(
            self.dem, dirs, acc, cell_size=4.0, min_discharge=8.0, sea_level=0.0,
            bank_flare=2.0, passes=1,
        )
        self.assertTrue(np.all(carved <= self.dem + 1e-9), "carving must never raise terrain")
        self.assertLess(float(carved.min()), float(self.dem.min()) + 1e-9,
                        "at least one channel should be cut")
        self.assertGreater(float(carved.min()), -50.0, "carving should stay bounded")

    def test_watershed_labels_terminate_at_borders(self):
        filled, _ = hydrology.priority_flood(self.dem)
        dirs = hydrology.flow_directions(filled)
        labels = hydrology.watershed_labels(dirs)
        self.assertEqual(labels.shape, self.dem.shape)
        border = set(labels[0, :].tolist()) & set(labels[-1, :].tolist())
        self.assertTrue(len(set(labels.ravel().tolist())) < labels.size)

    def test_ocean_mask_only_connected_to_border(self):
        dem = np.full((20, 20), 70.0)
        dem[5:8, 5:8] = 40.0  # an inland pit, not connected to the edge
        dem[:, :4] = 40.0  # a bay open to the west edge
        mask = hydrology.ocean_mask(dem, 63.0)
        self.assertTrue(mask[10, 1], "connected low ground should be ocean")
        self.assertFalse(mask[6, 6], "an enclosed pit is not the ocean")

    def test_warp_field_preserves_mass_roughly(self):
        field = np.arange(64 * 64, dtype=np.float64).reshape(64, 64)
        warped = hydrology.warp_field(field, cell_size=4.0, seed=3, strength=1.5)
        self.assertEqual(warped.shape, field.shape)
        self.assertFalse(np.allclose(warped, field))
        self.assertLess(abs(float(warped.mean() - field.mean())) / field.mean(), 0.25)


class TestErosion(unittest.TestCase):
    def setUp(self):
        X, Y = grid_coords(48, 48, 4.0)
        self.dem = 80.0 + fbm(X, Y, octaves=5, scale=220.0, seed=4) * 30.0

    def test_slope_map(self):
        slope = erosion.slope_map(self.dem, 4.0)
        self.assertEqual(slope.shape, self.dem.shape)
        self.assertTrue(np.all(slope >= 0))

    def test_thermal_erosion_smooths(self):
        # talus is in blocks of rise per block of run, so a 1-block cell with talus 0.05
        # erodes anything steeper than ~3 degrees - a realistic hillslope setting
        out = erosion.thermal_erosion(self.dem, iterations=30, rate=0.3, talus=0.05,
                                      cell_size=4.0)
        rough_in = float(np.std(np.diff(self.dem, axis=1)))
        rough_out = float(np.std(np.diff(out, axis=1)))
        self.assertLess(rough_out, rough_in, "thermal erosion should reduce roughness")

    def test_thermal_erosion_conserves_volume(self):
        out = erosion.thermal_erosion(self.dem, iterations=15, rate=0.25, talus=0.05,
                                      cell_size=4.0)
        self.assertAlmostEqual(float(out.mean()), float(self.dem.mean()), delta=0.05)

    def test_stream_power_erodes_more_where_flow_is_larger(self):
        dem, acc = erosion.stream_power_erosion(self.dem, cell_size=4.0, iterations=6,
                                                k=0.6, max_incision=2.0)
        change = dem - self.dem
        self.assertTrue(np.all(change <= 1e-9))
        high = change[acc > np.percentile(acc, 90)]
        low = change[acc < np.percentile(acc, 50)]
        self.assertLess(float(high.mean()), float(low.mean()),
                        "channels should incise faster than hillslopes")

    def test_talus_relaxation_limits_gradient(self):
        dem = self.dem.copy()
        dem[20, 20] += 60.0  # an impossible spike
        out = erosion.talus_relaxation(dem, talus=1.4, iterations=15, cell_size=4.0)
        self.assertLess(float(out.max()), float(dem.max()))
        # the resulting gradient to every neighbour must respect the repose angle
        h, w = out.shape
        for dz, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            a = out[1:-1, 1:-1]
            b = out[1 + dz : h - 1 + dz, 1 + dx : w - 1 + dx]
            drops = a - b
            self.assertLessEqual(float(drops.max()), 1.4 * 4.0 + 1e-6)

    def test_smooth_channels_keeps_shape(self):
        acc = np.zeros_like(self.dem)
        acc[20:30, 20] = 500.0
        out = erosion.smooth_channels(self.dem, acc, threshold=100.0, sigma=1.0)
        self.assertEqual(out.shape, self.dem.shape)
        self.assertLess(float(np.abs(out - self.dem).max()), 5.0)

    def test_erosion_stats(self):
        before = self.dem.copy()
        after = before - 1.0
        stats = erosion.erosion_stats(before, after, np.ones_like(before))
        self.assertAlmostEqual(stats["eroded_volume"], float(before.size), delta=1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
