package io.github.exo2v.vantraya.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import io.github.exo2v.vantraya.core.SurfaceLogic.Choice;
import io.github.exo2v.vantraya.core.SurfaceLogic.Column;
import io.github.exo2v.vantraya.core.SurfaceLogic.Mat;

/** The spec's slope table, row by row. */
public class SurfaceLogicTest {
    private static final int FREE = -1;
    private static final int CALDERA = 2;
    private static final int DUNES = 4;
    private static final int GLACIAL = 3;

    private static Column col(double deg, int ground, int landmark, BiomeRole biome, double dither, double rnd) {
        return new Column(deg, ground, false, landmark, biome, false, dither, rnd, 0.5);
    }

    private static boolean isDirtLike(Mat m) {
        return m == Mat.GRASS_BLOCK || m == Mat.DIRT || m == Mat.COARSE_DIRT || m == Mat.PODZOL
                || m == Mat.MUD || m == Mat.MOSS_BLOCK;
    }

    @Test
    public void gentleSlopesKeepTheBiomeTopsoil() {
        Choice c = SurfaceLogic.choose(col(10, 90, FREE, BiomeRole.PLAINS, 0.5, 0.5));
        assertTrue("free gentle ground is left to the biome's own surface rule", c.isKeep());
    }

    @Test
    public void dirtBandKeepsFortyPercentSoil() {
        int soil = 0;
        int n = 4000;
        BlueNoise bn = BlueNoise.forSeed(1);
        for (int i = 0; i < n; i++) {
            Choice c = SurfaceLogic.choose(col(30, 90, FREE, BiomeRole.TEMPERATE_FOREST,
                    bn.threshold(i % 200, i / 200), (i * 0.618) % 1.0));
            assertNotNull(c.top());
            if (isDirtLike(c.top())) {
                soil++;
                assertTrue(c.top() == Mat.COARSE_DIRT || c.top() == Mat.PODZOL);
            } else {
                assertTrue(c.top() == Mat.STONE || c.top() == Mat.GRAVEL || c.top() == Mat.ANDESITE);
            }
        }
        assertEquals(0.40, (double) soil / n, 0.03);
    }

    @Test
    public void screeBandExcludesTreesAndKeepsAFivePercentMossyPatch() {
        int mossy = 0;
        int n = 4000;
        BlueNoise bn = BlueNoise.forSeed(1);
        for (int i = 0; i < n; i++) {
            Choice c = SurfaceLogic.choose(col(41, 90, FREE, BiomeRole.TEMPERATE_MOUNTAINS,
                    bn.threshold(i % 200, i / 200), (i * 0.618) % 1.0));
            assertTrue("no dirt-like block (trees strictly excluded) on a 35-45 degree face: " + c.top(), !isDirtLike(c.top()));
            if (c.top() == Mat.MOSSY_COBBLESTONE) {
                mossy++;
            } else {
                assertTrue(c.top() == Mat.STONE || c.top() == Mat.GRAVEL || c.top() == Mat.COBBLESTONE);
            }
        }
        assertEquals(0.05, (double) mossy / n, 0.015);
        // the scree fraction rises with the angle: (deg - 35) / 12
        int gravelLow = 0;
        int gravelHigh = 0;
        for (int i = 0; i < 2000; i++) {
            double rnd = (i * 0.754877666) % 1.0;
            Mat lo = SurfaceLogic.choose(col(36, 90, FREE, BiomeRole.TAIGA, 0.9, rnd)).top();
            Mat hi = SurfaceLogic.choose(col(44, 90, FREE, BiomeRole.TAIGA, 0.9, rnd)).top();
            if (lo != Mat.STONE) {
                gravelLow++;
            }
            if (hi != Mat.STONE) {
                gravelHigh++;
            }
        }
        assertTrue(gravelHigh > 3 * gravelLow);
    }

    @Test
    public void sheerCliffsAreBareRock() {
        Choice c = SurfaceLogic.choose(col(60, 90, FREE, BiomeRole.TEMPERATE_MOUNTAINS, 0.0, 0.5));
        assertEquals(Mat.GRANITE, c.top());
        assertEquals(Mat.STONE, c.filler());
        Choice basalt = SurfaceLogic.choose(col(60, 90, FREE, BiomeRole.BASALT_DELTAS, 0.0, 0.5));
        assertEquals(Mat.BASALT, basalt.top());
        for (double deg = 45.5; deg < 90; deg += 2) {
            assertTrue(!isDirtLike(SurfaceLogic.choose(col(deg, 90, FREE, BiomeRole.PLAINS, 0.0, 0.1)).top()));
        }
    }

    @Test
    public void treelineStripsEverythingAboveY225() {
        Choice frozen = SurfaceLogic.choose(col(5, 226, GLACIAL, BiomeRole.FROZEN_PEAKS, 0.5, 0.5));
        assertEquals(Mat.SNOW_BLOCK, frozen.top());
        assertEquals(Mat.PACKED_ICE, frozen.filler());
        Choice other = SurfaceLogic.choose(col(5, 240, GLACIAL, BiomeRole.JAGGED_PEAKS, 0.5, 0.5));
        assertEquals(Mat.CALCITE, other.top());
        assertEquals(Mat.PACKED_ICE, other.filler());
        // even a gentle slope just below the treeline keeps its soil rules
        Choice below = SurfaceLogic.choose(col(5, 224, FREE, BiomeRole.TAIGA, 0.5, 0.5));
        assertTrue(below.top() == null || below.top() != Mat.CALCITE);
        // and cliffs above it are covered too
        assertEquals(Mat.CALCITE, SurfaceLogic.choose(col(70, 230, FREE, BiomeRole.TEMPERATE_MOUNTAINS, 0.5, 0.5)).top());
    }

    @Test
    public void calderaIsBarrenVolcanicRock() {
        // crater floor, rim, throne
        Choice floor = SurfaceLogic.choose(col(2, 41, CALDERA, BiomeRole.BASALT_DELTAS, 0.5, 0.5));
        assertTrue(floor.top() == Mat.BASALT || floor.top() == Mat.BLACKSTONE);
        Choice rimFlat = SurfaceLogic.choose(col(10, 146, CALDERA, BiomeRole.ERODED_BADLANDS, 0.5, 0.5));
        assertEquals(Mat.BASALT, rimFlat.top());
        Choice rimSteep = SurfaceLogic.choose(col(32, 130, CALDERA, BiomeRole.ERODED_BADLANDS, 0.5, 0.5));
        assertEquals(Mat.BLACKSTONE, rimSteep.top());
        Choice throne = SurfaceLogic.choose(col(40, 92, CALDERA, BiomeRole.BASALT_DELTAS, 0.5, 0.5));
        assertEquals(Mat.OBSIDIAN, throne.top());
        assertEquals(Mat.OBSIDIAN, throne.filler());
        Choice lava = SurfaceLogic.choose(new Column(1, 41, false, CALDERA, BiomeRole.BASALT_DELTAS, true, 0.5, 0.5, 0.5));
        assertEquals(Mat.MAGMA_BLOCK, lava.top());
        assertEquals(Mat.MAGMA_BLOCK, lava.filler());
        // nothing in the caldera is ever a dirt-like block, whatever the slope
        for (double deg = 0; deg < 90; deg += 5) {
            for (int y : new int[] {41, 70, 92, 120, 146}) {
                assertTrue(!isDirtLike(SurfaceLogic.choose(col(deg, y, CALDERA, BiomeRole.BASALT_DELTAS, 0.0, 0.0)).top()));
                assertTrue(!isDirtLike(SurfaceLogic.choose(col(deg, y, CALDERA, BiomeRole.BASALT_DELTAS, 0.99, 0.99)).top()));
            }
        }
    }

    @Test
    public void duneCrestsAreVitrified() {
        assertEquals(Mat.BLACK_GLAZED_TERRACOTTA, SurfaceLogic.choose(col(5, 92, DUNES, BiomeRole.BADLANDS, 0.5, 0.5)).top());
        assertTrue(SurfaceLogic.choose(col(5, 80, DUNES, BiomeRole.DESERT, 0.5, 0.5)).top() == Mat.RED_SAND);
    }

    @Test
    public void theVeilSeabedIsSaltCrustOverGravel() {
        int calcite = 0;
        int n = 2000;
        for (int i = 0; i < n; i++) {
            Choice c = SurfaceLogic.choose(new Column(3, -28, true, Spec.VEIL_ID, BiomeRole.DEEP_COLD_OCEAN, false,
                    0.5, (i * 0.618) % 1.0, 0.5));
            assertEquals(Mat.GRAVEL, c.filler());
            assertTrue(c.top() == Mat.CALCITE || c.top() == Mat.GRAVEL);
            if (c.top() == Mat.CALCITE) {
                calcite++;
            }
        }
        assertEquals(0.45, (double) calcite / n, 0.03);
    }

    @Test
    public void underwaterGroundOutsideTheVeilIsLeftAlone() {
        Choice c = SurfaceLogic.choose(new Column(50, 40, true, FREE, BiomeRole.OCEAN, false, 0.1, 0.1, 0.1));
        assertTrue(c.isKeep());
        assertNull(c.top());
    }

    @Test
    public void soilDepthIsTwoToFour() {
        for (double r2 : new double[] {0.0, 0.34, 0.67, 0.999}) {
            Choice c = SurfaceLogic.choose(new Column(30, 90, false, FREE, BiomeRole.PLAINS, false, 0.1, 0.1, r2));
            assertTrue(c.soilDepth() >= 2 && c.soilDepth() <= 4);
        }
    }

    @Test
    public void slopeIsMeasuredWithTheL1GradientLikeTheOfflineEngine() {
        assertEquals(0.0, SurfaceLogic.slopeDegrees(0, 0), 0.0);
        assertEquals(45.0, SurfaceLogic.slopeDegrees(1, 0), 1e-9);
        assertEquals(45.0, SurfaceLogic.slopeDegrees(0.5, 0.5), 1e-9);
        assertEquals(Math.toDegrees(Math.atan(0.7)), SurfaceLogic.slopeDegrees(0.4, -0.3), 1e-9);
    }
}
