package io.github.exo2v.vantraya.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import io.github.exo2v.vantraya.core.Spec.Kind;
import io.github.exo2v.vantraya.core.Spec.Landmark;

/**
 * Pins every number of the specification tables. The expected values below are typed in independently
 * from {@code ASHENFALL_WORLD_MAP_MASTER_SPECIFICATION.pdf} (sections 1-4) and {@code HANDOFF.md}
 * section 3, not read back from {@link Spec}, so a typo in either place fails the build.
 */
public class SpecTest {

    @Test
    public void canvasAndVerticalRange() {
        assertEquals(8000, Spec.CANVAS);
        assertEquals(-4000, -Spec.HALF);
        assertEquals(-64, Spec.MIN_Y);
        assertEquals(320, Spec.MAX_Y);
        assertEquals(384, Spec.MAX_Y - Spec.MIN_Y);
        assertEquals(62.0, Spec.SEA_LEVEL, 0.0);
        assertEquals(20250929L, Spec.SPEC_SEED);
    }

    @Test
    public void uint16HeightEncoding() {
        // h_norm = (Y + 64) / 384, uint16 = round(h_norm * 65535)
        assertEquals(0, Spec.yToU16(-64));
        assertEquals(5461, Spec.yToU16(-32));
        assertEquals(12629, Spec.yToU16(10));
        assertEquals(21504, Spec.yToU16(62));
        assertEquals(65535, Spec.yToU16(320));
        assertEquals(62.0, Spec.u16ToY(21504), 0.01);
        assertEquals(-64.0, Spec.u16ToY(0), 1e-9);
        assertEquals(320.0, Spec.u16ToY(65535), 1e-9);
    }

    @Test
    public void namedElevations() {
        assertEquals(-32.0, Spec.ABYSS_FLOOR, 0.0);
        assertEquals(10.0, Spec.TRENCH_TOP, 0.0);
        assertEquals(40.0, Spec.CALDERA_FLOOR, 0.0);
        assertEquals(92.0, Spec.CALDERA_THRONE, 0.0);
        assertEquals(146.0, Spec.CALDERA_RIM, 0.0);
        assertEquals(56.0, Spec.SHELF_TOP, 0.0);
        assertEquals(225.0, Spec.TREELINE, 0.0);
        assertEquals(279.2, Spec.SPINE_HIGH, 0.0);
    }

    @Test
    public void shelfDropoffFollowsTheHermiteCurve() {
        assertEquals(3300.0, Spec.SHELF_INNER, 0.0);
        assertEquals(3800.0, Spec.SHELF_OUTER, 0.0);
        assertEquals(0.0, Spec.shelfDrop(3300.0), 1e-12);
        assertEquals(0.0, Spec.shelfDrop(100.0), 1e-12);
        // t = 0.5 -> S = 0.5 -> -300 (above the clamp at the abyss floor, -94)
        assertEquals(-94.0, Spec.shelfDrop(3550.0), 1e-9);
        // t = 0.1 -> S = 0.028 -> -16.8
        assertEquals(-600.0 * (3 * 0.01 - 2 * 0.001), Spec.shelfDrop(3350.0), 1e-9);
        // beyond the outer radius the drop is the clamped abyss depth: Y = -32
        assertEquals(-94.0, Spec.shelfDrop(3800.0), 1e-9);
        assertEquals(Spec.ABYSS_FLOOR, Spec.SEA_LEVEL + Spec.shelfDrop(5000.0), 1e-9);
        // monotone descent
        double prev = 1.0;
        for (double r = 3200; r <= 3900; r += 10) {
            double d = Spec.shelfDrop(r);
            assertTrue(d <= prev + 1e-12);
            prev = d;
        }
    }

    private static void landmark(int idx, String key, double x, double y, double z, double x1, double x2,
                                 double z1, double z2, double yLo, double yHi, Kind kind) {
        Landmark lm = Spec.LANDMARKS.get(idx);
        assertEquals(key, lm.key());
        assertEquals(key + " x", x, lm.x(), 0.0);
        assertEquals(key + " y", y, lm.y(), 0.0);
        assertEquals(key + " z", z, lm.z(), 0.0);
        assertEquals(key + " x1", x1, lm.x1(), 0.0);
        assertEquals(key + " x2", x2, lm.x2(), 0.0);
        assertEquals(key + " z1", z1, lm.z1(), 0.0);
        assertEquals(key + " z2", z2, lm.z2(), 0.0);
        assertEquals(key + " yLo", yLo, lm.yLo(), 0.0);
        assertEquals(key + " yHi", yHi, lm.yHi(), 0.0);
        assertEquals(kind, lm.kind());
        // the centre lies inside the box
        assertTrue(lm.boxContains(x, z));
    }

    @Test
    public void nineLandmarksWithExactCentresBoxesAndBands() {
        assertEquals(9, Spec.LANDMARKS.size());
        landmark(0, "forgotten_coast", 0, 68, 2500, -600, 600, 2000, 3200, 68, 74, Kind.COAST);
        landmark(1, "cogwork_march", -2100, 85, 0, -2800, -1400, -700, 700, 85, 110, Kind.QUARRY);
        landmark(2, "ashen_caldera", 0, 80, 0, -750, 750, -750, 750, 38, 150, Kind.CALDERA);
        landmark(3, "glacial_spine", 0, 220, -2500, -1800, 1800, -3500, -1500, 180, 279, Kind.CORDILLERA);
        landmark(4, "gilded_dunes", 2300, 75, 0, 1600, 3100, -800, 800, 75, 94, Kind.DUNES);
        landmark(5, "whispering_fen", 2000, 63, 2000, 1300, 2700, 1300, 2700, 62, 66, Kind.FEN);
        landmark(6, "sunken_reach", -2400, 54, 1600, -3200, -1700, 1000, 2300, 50, 62, Kind.DROWNED_SHELF);
        landmark(7, "hermits_spire", -1800, 140, -1800, -2300, -1300, -2300, -1300, 140, 185, Kind.SPIRES);
        landmark(8, "byzantine_choir", 1800, 120, -1800, 1300, 2300, -2300, -1300, 110, 145, Kind.TERRACES);
    }

    @Test
    public void veilOfSaltRim() {
        assertEquals(3550.0, Spec.VEIL_RADIUS, 0.0);
        assertEquals(-32.0, Spec.VEIL.yLo(), 0.0);
        assertEquals(62.0, Spec.VEIL.yHi(), 0.0);
        assertEquals(java.util.List.of("deep_cold_ocean", "deep_ocean"), Spec.VEIL.biomes());
        assertEquals(9, Spec.VEIL_ID);
    }

    private static void windows(int idx, double c0, double c1, double e0, double e1, double r0, double r1,
                                double t0, double t1, double h0, double h1) {
        Landmark lm = Spec.LANDMARKS.get(idx);
        assertEquals(lm.key() + " cont lo", c0, lm.cont().lo(), 0.0);
        assertEquals(lm.key() + " cont hi", c1, lm.cont().hi(), 0.0);
        assertEquals(lm.key() + " erosion lo", e0, lm.erosion().lo(), 0.0);
        assertEquals(lm.key() + " erosion hi", e1, lm.erosion().hi(), 0.0);
        assertEquals(lm.key() + " ridges lo", r0, lm.ridges().lo(), 0.0);
        assertEquals(lm.key() + " ridges hi", r1, lm.ridges().hi(), 0.0);
        assertEquals(lm.key() + " temp lo", t0, lm.temp().lo(), 0.0);
        assertEquals(lm.key() + " temp hi", t1, lm.temp().hi(), 0.0);
        assertEquals(lm.key() + " hum lo", h0, lm.humidity().lo(), 0.0);
        assertEquals(lm.key() + " hum hi", h1, lm.humidity().hi(), 0.0);
    }

    @Test
    public void lithosphereDensityAndClimateWindows() {
        // 0.3.0: the parameter windows recalibrated against vanilla's offset/factor spline response
        // (see docs/RIVERS_AND_BIOME_BORDERS.md section 6), keeping every landmark's character and sign.
        windows(0, 0.00, 0.35, -0.25, 0.25, 0.05, 0.50, -0.20, 0.15, -0.35, 0.30);
        windows(1, 0.10, 0.40, -0.35, 0.05, -0.55, -0.20, 0.00, 0.50, -0.4, 0.3);
        windows(2, 0.25, 0.55, -0.70, -0.35, 0.02, 0.35, 0.70, 1.00, -0.8, -0.2);
        windows(3, 0.75, 0.95, -0.85, -0.55, 0.60, 0.95, -1.00, -0.75, -0.4, 0.4);
        windows(4, 0.20, 0.45, -0.50, -0.05, -0.50, 0.50, 0.70, 1.00, -0.8, -0.2);
        windows(5, 0.05, 0.25, 0.40, 0.85, -0.20, 0.20, 0.35, 0.60, 0.5, 1.0);
        windows(6, -0.25, -0.18, 0.10, 0.50, -0.50, 0.50, 0.35, 0.60, 0.4, 1.0);
        windows(7, 0.45, 0.75, -0.80, -0.40, 0.45, 0.85, -0.60, -0.25, -0.2, 0.6);
        windows(8, 0.40, 0.70, -0.70, -0.35, 0.15, 0.55, -0.35, 0.15, -0.45, 0.35);
        Landmark veil = Spec.VEIL;
        assertEquals(-0.90, veil.cont().lo(), 0.0);
        assertEquals(-0.45, veil.cont().hi(), 0.0);
        assertEquals(-0.60, veil.erosion().lo(), 0.0);
        assertEquals(0.40, veil.erosion().hi(), 0.0);
        assertEquals(-1.00, veil.ridges().lo(), 0.0);
        assertEquals(1.00, veil.ridges().hi(), 0.0);
    }

    @Test
    public void landmarkBiomesAreTheSpecifiedVanillaIds() {
        String[][] expected = {
                {"plains", "meadow"},
                {"windswept_hills", "wooded_badlands"},
                {"basalt_deltas", "eroded_badlands"},
                {"frozen_peaks", "jagged_peaks", "grove"},
                {"desert", "badlands"},
                {"swamp", "mangrove_swamp"},
                {"warm_ocean", "lukewarm_ocean"},
                {"windswept_hills", "meadow"},
                {"meadow", "cherry_grove"}};
        for (int i = 0; i < expected.length; i++) {
            assertEquals(java.util.List.of(expected[i]), Spec.LANDMARKS.get(i).biomes());
        }
    }

    @Test
    public void everyLandmarkBiomeIsAPlainBiomeId() {
        for (Landmark lm : Spec.LANDMARKS) {
            for (String b : lm.biomes()) {
                assertTrue("biome id " + b, b.matches("[a-z_]+"));
            }
        }
    }

    @Test
    public void climateTiersAndLapse() {
        // spec section 4 / HANDOFF 3.3
        assertEquals(0, Spec.climateTier(-0.75));
        assertEquals(0, Spec.climateTier(-1.0));
        assertEquals(1, Spec.climateTier(-0.74));
        assertEquals(1, Spec.climateTier(-0.25));
        assertEquals(2, Spec.climateTier(-0.24));
        assertEquals(2, Spec.climateTier(0.40));
        assertEquals(3, Spec.climateTier(0.41));
        assertEquals(3, Spec.climateTier(0.69));
        assertEquals(4, Spec.climateTier(0.70));
        // T_eff = T_base - 0.0055 * max(0, y - 62); "summits at Y=220 experience a -0.869 reduction"
        assertEquals(0.3 - 0.869, Spec.effectiveTemperature(0.3, 220.0), 1e-9);
        assertEquals(0.3, Spec.effectiveTemperature(0.3, 62.0), 0.0);
        assertEquals(0.3, Spec.effectiveTemperature(0.3, -20.0), 0.0);
    }

    @Test
    public void slopeTableThresholds() {
        assertEquals(25.0, Spec.SLOPE_SOIL_MAX, 0.0);
        assertEquals(35.0, Spec.SLOPE_DIRT_MAX, 0.0);
        assertEquals(45.0, Spec.SLOPE_SCREE_MAX, 0.0);
    }
}
