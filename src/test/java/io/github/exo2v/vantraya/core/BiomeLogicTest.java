package io.github.exo2v.vantraya.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertTrue;

import java.util.ArrayList;
import java.util.EnumSet;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.Set;

import org.junit.Test;

import io.github.exo2v.vantraya.core.Spec.Kind;
import io.github.exo2v.vantraya.core.Spec.Landmark;
import io.github.exo2v.vantraya.core.VantrayaModel.Fields;

public class BiomeLogicTest {

    private static BiomeRole surface(VantrayaModel m, double x, double z) {
        Fields f = m.sample(x, z);
        return BiomeLogic.surfaceRole(x, z, f.height(), f.temperature(), f.humidity(), f.cont(), f.erosion());
    }

    @Test
    public void surfaceHeightIsRecoveredFromTheRouterDepth() {
        for (double h : new double[] {-30, 20, 62.4, 68, 140, 279}) {
            for (double y : new double[] {-60, 0, 62, 100, 300}) {
                double depth = (h + 0.5 - y) / 128.0;
                assertEquals(h, BiomeLogic.surfaceHeight(depth, y), 1e-9);
            }
        }
    }

    @Test
    public void everyLandmarkCentreHasItsSpecifiedBiome() {
        for (long seed : new long[] {Spec.SPEC_SEED, 3L, 777L}) {
            VantrayaModel m = VantrayaModel.forSeed(seed);
            for (Landmark lm : Spec.LANDMARKS) {
                BiomeRole role = surface(m, lm.x(), lm.z());
                boolean ok = false;
                for (String b : lm.biomes()) {
                    ok |= role.vanilla().equals(b);
                }
                assertTrue(lm.key() + " seed " + seed + " got " + role.vanilla() + " expected " + lm.biomes(), ok);
            }
        }
    }

    @Test
    public void landmarkPalettesSplitByHeightLikeTheOfflineEngine() {
        assertEquals(BiomeRole.PLAINS, BiomeLogic.landmarkRole(Kind.COAST, 70));
        assertEquals(BiomeRole.MEADOW, BiomeLogic.landmarkRole(Kind.COAST, 72));
        assertEquals(BiomeRole.WOODED_BADLANDS, BiomeLogic.landmarkRole(Kind.QUARRY, 100));
        assertEquals(BiomeRole.WINDSWEPT_HILLS, BiomeLogic.landmarkRole(Kind.QUARRY, 105));
        assertEquals(BiomeRole.BASALT_DELTAS, BiomeLogic.landmarkRole(Kind.CALDERA, 80));
        assertEquals(BiomeRole.ERODED_BADLANDS, BiomeLogic.landmarkRole(Kind.CALDERA, 130));
        assertEquals(BiomeRole.GROVE, BiomeLogic.landmarkRole(Kind.CORDILLERA, 190));
        assertEquals(BiomeRole.JAGGED_PEAKS, BiomeLogic.landmarkRole(Kind.CORDILLERA, 220));
        assertEquals(BiomeRole.FROZEN_PEAKS, BiomeLogic.landmarkRole(Kind.CORDILLERA, 260));
        assertEquals(BiomeRole.DESERT, BiomeLogic.landmarkRole(Kind.DUNES, 80));
        assertEquals(BiomeRole.BADLANDS, BiomeLogic.landmarkRole(Kind.DUNES, 92));
        assertEquals(BiomeRole.MANGROVE_SWAMP, BiomeLogic.landmarkRole(Kind.FEN, 63.0));
        assertEquals(BiomeRole.SWAMP, BiomeLogic.landmarkRole(Kind.FEN, 64.0));
        assertEquals(BiomeRole.LUKEWARM_OCEAN, BiomeLogic.landmarkRole(Kind.DROWNED_SHELF, 49));
        assertEquals(BiomeRole.WARM_OCEAN, BiomeLogic.landmarkRole(Kind.DROWNED_SHELF, 55));
        assertEquals(BiomeRole.MEADOW, BiomeLogic.landmarkRole(Kind.SPIRES, 140));
        assertEquals(BiomeRole.WINDSWEPT_HILLS, BiomeLogic.landmarkRole(Kind.SPIRES, 170));
        assertEquals(BiomeRole.MEADOW, BiomeLogic.landmarkRole(Kind.TERRACES, 120));
        assertEquals(BiomeRole.CHERRY_GROVE, BiomeLogic.landmarkRole(Kind.TERRACES, 140));
    }

    @Test
    public void landmarkBiomesStayInsideTheirBox() {
        VantrayaModel m = VantrayaModel.forSeed(Spec.SPEC_SEED);
        Random rnd = new Random(11);
        for (Landmark lm : Spec.LANDMARKS) {
            Set<String> allowed = new java.util.HashSet<>(lm.biomes());
            int land = 0;
            int core = 0;
            int band = 0;
            int bandOk = 0;
            for (int i = 0; i < 400; i++) {
                double x = lm.x1() + rnd.nextDouble() * (lm.x2() - lm.x1());
                double z = lm.z1() + rnd.nextDouble() * (lm.z2() - lm.z1());
                if (Regions.idAt(x, z) != Spec.LANDMARKS.indexOf(lm)) {
                    continue;
                }
                Fields f = m.sample(x, z);
                if (f.height() < Spec.SEA_LEVEL + 4 || f.cont() < 0.12) {
                    continue; // shorelines and inland water are classified as such
                }
                land++;
                boolean specified = allowed.contains(surface(m, x, z).vanilla());
                double sd = Regions.sdOwned(Spec.LANDMARKS.indexOf(lm), x, z);
                if (sd > BiomeLogic.SEAM_BAND) {
                    core++;
                    assertTrue(lm.key() + " deep inside its region got a foreign biome at " + (int) x + "," + (int) z,
                            specified);
                } else {
                    band++;
                    if (specified) {
                        bandOk++;
                    }
                }
                land++;
            }
            if (lm.kind() != Kind.DROWNED_SHELF && band > 10) {
                // The seam band is a deliberate ecotone (the play test asked for borders that are not walls):
                // the two sides interleave there in fractal blobs, but the landmark side stays the majority.
                assertTrue(lm.key() + " seam band columns with the specified biome: " + bandOk + "/" + band,
                        bandOk >= 0.5 * band);
            }
            assertTrue(lm.key() + " core sampled", core == 0 || land > 20);
        }
    }

    @Test
    public void bordersWanderInsteadOfRunningStraight() {
        // The Byzantine Choir's meadow gives way to cherry grove at Y = 126. Before blending that line was a
        // wall; the squares of the second attempt were worse. Now the threshold itself is read through
        // warped height, so the line wanders in a smooth, fractal way - vanilla's look. Measure the line:
        // for each point across the region, the height at which the role flips.
        VantrayaModel m = VantrayaModel.forSeed(Spec.SPEC_SEED);
        int choir = 8; // the Byzantine Choir (Spec.LANDMARKS order)
        assertEquals("byzantine_choir", Spec.LANDMARKS.get(choir).key());
        List<Double> flips = new ArrayList<>();
        double stepSum = 0;
        int steps = 0;
        for (double z = -2150.0; z <= -1450.0; z += 116.0) {
            double prev = Double.NaN;
            for (double x = 1450.0; x <= 2150.0; x += 58.0) {
                Fields f = m.sample(x, z);
                double flip = Double.NaN;
                for (double hs = 110.0; hs <= 146.0; hs += 0.25) {
                    BiomeRole role = BiomeLogic.surfaceRole(x, z, hs, f.temperature(), f.humidity(),
                            f.cont(), f.erosion());
                    if (role == BiomeRole.CHERRY_GROVE) {
                        flip = hs;
                        break;
                    }
                }
                if (!Double.isNaN(flip)) {
                    flips.add(flip);
                    if (!Double.isNaN(prev)) {
                        stepSum += Math.abs(flip - prev);
                        steps++;
                    }
                    prev = flip;
                }
            }
        }
        int n = flips.size();
        assertTrue("found " + n + " border columns", n >= 40);
        double mean = 0;
        for (double v : flips) {
            mean += v;
        }
        mean /= n;
        double var = 0;
        for (double v : flips) {
            var += (v - mean) * (v - mean);
        }
        double std = Math.sqrt(var / n);
        // one warp amplitude of regional shift is allowed; over the whole region the warp averages to zero
        assertTrue("the border sits at " + mean + " instead of near 126", Math.abs(mean - 126.0) < 4.5);
        assertTrue("the border is flat (std " + std + " blocks) - still a wall", std > 1.0);
        assertTrue("the border jitters per point (mean step " + stepSum / steps + " blocks over 58-block spacing)",
                steps == 0 || stepSum / steps < 8.0);
    }

    @Test
    public void climateBandBordersAreMottledToo() {
        // The same for the Whittaker matrix: wherever open land sits near the coldest temperature edge, the two
        // bands' biomes interleave instead of meeting along a line. The edge is found by scanning, so the test
        // says nothing about where on the continent the climate happens to cross it.
        VantrayaModel m = VantrayaModel.forSeed(Spec.SPEC_SEED);
        Map<String, Integer> seen = new java.util.TreeMap<>();
        for (double x = -3200; x <= 3200; x += 16) {
            for (double z = -3200; z <= 3200; z += 16) {
                if (Regions.idAt(x, z) != -1) {
                    continue;
                }
                Fields f = m.sample(x, z);
                if (f.height() < Spec.SEA_LEVEL + 6 || f.cont() < 0.2 || Math.abs(f.temperature() + 0.68) > 0.10) {
                    continue;
                }
                BiomeRole role = BiomeLogic.surfaceRole(x, z, f.height(), f.temperature(), f.humidity(),
                        f.cont(), f.erosion());
                seen.merge(role.name(), 1, Integer::sum);
            }
        }
        int total = seen.values().stream().mapToInt(Integer::intValue).sum();
        assertTrue("found " + total + " columns near the snow band edge", total > 100);
        assertTrue("roles at the snow band edge: " + seen, seen.size() >= 2);
        int biggest = seen.values().stream().mapToInt(Integer::intValue).max().getAsInt();
        assertTrue("one role takes " + biggest + "/" + total + " of the border band: " + seen, biggest < 0.9 * total);
    }

    @Test
    public void theSunkenCraterIsVolcanicNotWater() {
        // the crater floor (Y 38-42) is below sea level, yet the caldera is dry: basalt deltas, never river or ocean
        for (long seed : new long[] {Spec.SPEC_SEED, 12L}) {
            VantrayaModel m = VantrayaModel.forSeed(seed);
            int basin = 0;
            for (double x = -560; x <= 560; x += 20) {
                for (double z = -560; z <= 560; z += 20) {
                    Fields f = m.sample(x, z);
                    if (!f.noWater()) {
                        continue;
                    }
                    basin++;
                    BiomeRole role = surface(m, x, z);
                    assertTrue("caldera column " + x + "," + z + " (H=" + f.height() + ") got " + role,
                            role == BiomeRole.BASALT_DELTAS || role == BiomeRole.ERODED_BADLANDS);
                }
            }
            assertTrue(basin > 300);
        }
    }

    @Test
    public void theVeilIsDeepColdOrDeepOcean() {
        VantrayaModel m = VantrayaModel.forSeed(Spec.SPEC_SEED);
        Random rnd = new Random(2);
        int cold = 0;
        int deep = 0;
        for (int i = 0; i < 500; i++) {
            double a = rnd.nextDouble() * 2 * Math.PI;
            double r = 3600 + rnd.nextDouble() * 1500;
            BiomeRole role = surface(m, r * Math.cos(a), r * Math.sin(a));
            assertTrue("veil biome " + role, role == BiomeRole.DEEP_COLD_OCEAN || role == BiomeRole.DEEP_OCEAN);
            if (role == BiomeRole.DEEP_COLD_OCEAN) {
                cold++;
            } else {
                deep++;
            }
        }
        assertTrue("both spec biomes should occur: " + cold + "/" + deep, cold > 20 && deep > 20);
    }

    @Test
    public void oceansFollowDepthAndTemperature() {
        assertEquals(BiomeRole.WARM_OCEAN, BiomeLogic.oceanRole(5, 0.7));
        assertEquals(BiomeRole.LUKEWARM_OCEAN, BiomeLogic.oceanRole(5, 0.2));
        assertEquals(BiomeRole.OCEAN, BiomeLogic.oceanRole(5, 0.0));
        assertEquals(BiomeRole.COLD_OCEAN, BiomeLogic.oceanRole(5, -0.7));
        assertEquals(BiomeRole.FROZEN_OCEAN, BiomeLogic.oceanRole(5, -0.9));
        assertEquals(BiomeRole.OCEAN, BiomeLogic.oceanRole(30, 0.5));
        assertEquals(BiomeRole.DEEP_OCEAN, BiomeLogic.oceanRole(80, 0.0));
        assertEquals(BiomeRole.DEEP_COLD_OCEAN, BiomeLogic.oceanRole(80, -0.5));
        assertEquals(BiomeRole.DEEP_FROZEN_OCEAN, BiomeLogic.oceanRole(80, -0.95));
        assertEquals(BiomeRole.DEEP_LUKEWARM_OCEAN, BiomeLogic.oceanRole(80, 0.6));
    }

    @Test
    public void inlandWaterIsARiverAndSeaWaterIsAnOcean() {
        // a column drowned below the waterline, inland (continentalness > 0): a river; at the coast: an ocean
        BiomeRole river = BiomeLogic.surfaceRole(900, -200, 58, 0.2, -0.2, 0.40, 0.0);
        assertEquals(BiomeRole.RIVER, river);
        BiomeRole frozen = BiomeLogic.surfaceRole(900, -200, 58, -0.9, -0.2, 0.40, 0.0);
        assertEquals(BiomeRole.FROZEN_RIVER, frozen);
        BiomeRole sea = BiomeLogic.surfaceRole(900, -200, 58, 0.0, -0.2, -0.30, 0.0);
        assertEquals(BiomeRole.OCEAN, sea);
    }

    @Test
    public void caveBiomesUseTheVanillaThresholds() {
        // depth 0.2 and below is still the surface layer
        BiomeRole surface = BiomeLogic.classify(900, 40, -200, 0.2, 0.9, 0.5, -0.5, 0.15);
        assertNotEquals(BiomeRole.LUSH_CAVES, surface);
        assertEquals(BiomeRole.LUSH_CAVES, BiomeLogic.classify(900, 10, -200, 0.2, 0.9, 0.5, 0.0, 0.5));
        assertEquals(BiomeRole.DRIPSTONE_CAVES, BiomeLogic.classify(900, 10, -200, 0.2, 0.0, 0.9, 0.0, 0.5));
        assertEquals(BiomeRole.DEEP_DARK, BiomeLogic.classify(900, -50, -200, 0.2, 0.0, 0.5, -0.5, 1.05));
        // below the deep-dark depth but without low erosion: no cave biome
        BiomeRole plain = BiomeLogic.classify(900, 10, -200, 0.2, 0.0, 0.3, 0.0, 0.5);
        assertTrue(plain != BiomeRole.LUSH_CAVES && plain != BiomeRole.DRIPSTONE_CAVES && plain != BiomeRole.DEEP_DARK);
    }

    @Test
    public void genericClassifierCoversTheWholeClimateMatrix() {
        Set<BiomeRole> seen = EnumSet.noneOf(BiomeRole.class);
        for (double t = -1; t <= 1; t += 0.05) {
            for (double h = -1; h <= 1; h += 0.05) {
                for (double elev : new double[] {3, 20, 60, 140, 190, 300}) {
                    seen.add(BiomeLogic.generic(Spec.SEA_LEVEL + elev, (t + 1) / 2, (h + 1) / 2, 0.0));
                }
            }
        }
        for (BiomeRole r : new BiomeRole[] {BiomeRole.SNOWY_PLAINS, BiomeRole.TAIGA, BiomeRole.PLAINS,
                BiomeRole.TEMPERATE_FOREST, BiomeRole.DESERT, BiomeRole.SAVANNA, BiomeRole.JUNGLE,
                BiomeRole.TEMPERATE_MOUNTAINS, BiomeRole.GLACIER, BiomeRole.ALPINE_PEAKS, BiomeRole.BADLANDS_MESA}) {
            assertTrue("never produced " + r, seen.contains(r));
        }
    }

    @Test
    public void everyRoleMapsToAVanillaBiomeAndHasAUniquePath() {
        Set<String> ids = new java.util.HashSet<>();
        for (BiomeRole r : BiomeRole.values()) {
            assertTrue(r.vanilla().matches("[a-z_]+"));
            assertTrue("duplicate role id " + r.id(), ids.add(r.id()));
        }
    }
}
