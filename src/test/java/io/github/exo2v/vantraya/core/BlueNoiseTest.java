package io.github.exo2v.vantraya.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class BlueNoiseTest {

    @Test
    public void thresholdIsUniformSoDensitiesAreExact() {
        BlueNoise bn = BlueNoise.forSeed(Spec.SPEC_SEED);
        int n = 0;
        int below40 = 0;
        int below5 = 0;
        for (int z = -320; z < 320; z++) {
            for (int x = -320; x < 320; x++) {
                double t = bn.threshold(x, z);
                assertTrue(t >= 0.0 && t < 1.0);
                n++;
                if (t < 0.40) {
                    below40++;
                }
                if (t < 0.05) {
                    below5++;
                }
            }
        }
        assertEquals("40 % density", 0.40, (double) below40 / n, 0.005);
        assertEquals("5 % density", 0.05, (double) below5 / n, 0.003);
    }

    @Test
    public void thresholdedSubsetsAreDispersedNotClumped() {
        BlueNoise bn = BlueNoise.forSeed(7);
        int agree = 0;
        int pairs = 0;
        for (int z = 0; z < 256; z++) {
            for (int x = 0; x < 255; x++) {
                boolean p = bn.threshold(x, z) < 0.4;
                boolean q = bn.threshold(x + 1, z) < 0.4;
                pairs++;
                if (p == q) {
                    agree++;
                }
            }
        }
        // white noise at 40 % agrees with its neighbour 0.4^2 + 0.6^2 = 52 % of the time; blue noise much less
        assertTrue("neighbour agreement " + (double) agree / pairs, (double) agree / pairs < 0.46);
    }

    @Test
    public void deterministicAndTileHashHidesTheRepeat() {
        BlueNoise a = BlueNoise.forSeed(3);
        BlueNoise b = BlueNoise.forSeed(3);
        for (int i = 0; i < 100; i++) {
            assertEquals(a.threshold(i * 13, i * -7), b.threshold(i * 13, i * -7), 0.0);
        }
        // adjacent tiles are rolled differently, so the 64-block period does not show
        int same = 0;
        for (int k = 0; k < 64; k++) {
            if (a.threshold(k, 5) == a.threshold(k + 64, 5)) {
                same++;
            }
        }
        assertTrue(same < 40);
    }
}
