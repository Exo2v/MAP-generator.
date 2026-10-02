package io.github.exo2v.vantraya.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

/**
 * The noise port is bit-exact with the offline engine ({@code mg/core/noise.py}); the golden values below
 * were produced by that Python code, so any drift in the hash, the gradients or the octave seeding fails.
 */
public class NoiseAndSplineTest {
    private static final long SEED = 20250929L;

    @Test
    public void matchesTheOfflineEngineBitForBit() {
        // hash2(floor(123.4), floor(-987.6), seed + 17)
        assertEquals(0.41101636784151196, Noise.hash2(123, -988, (int) (SEED + 17)), 1e-15);
        assertEquals(0.89228754071518780, Noise.hash2(0, 0, (int) (SEED + 17)), 1e-15);
        // gradient noise and fBm at (123.4, -987.6)
        assertEquals(0.22853871260377406, Noise.gradient(123.4 / 100.0, -987.6 / 100.0, (int) (SEED + 31)), 1e-14);
        assertEquals(-0.32678577249125750, Noise.fbm(123.4, -987.6, 4, 1500.0, SEED + 31), 1e-14);
        assertEquals(-0.058754993696222516, Noise.fbm(123.4, -987.6, 5, 1250.0, SEED + 41), 1e-14);
        assertEquals(0.67622767490081410, Noise.ridgedFbm(123.4, -987.6, 6, 1050.0, SEED + 51), 1e-14);
        assertEquals(0.52343655860566570, Noise.value(123.4 / 42.0, -987.6 / 42.0, (int) (SEED + 5501)), 1e-14);
        double[] w = new double[2];
        Noise.domainWarp(123.4, -987.6, 2100.0, 900.0, SEED + 17, 4, w);
        assertEquals(426.42364753949800, w[0], 1e-9);
        assertEquals(-1307.4304732641635, w[1], 1e-9);
    }

    @Test
    public void gradientNoiseVanishesOnTheLattice() {
        // so fBm is exactly zero at the origin for every seed: the Obsidian Throne's wobble never moves it
        for (long s = 0; s < 50; s++) {
            assertEquals(0.0, Noise.fbm(0, 0, 3, 380.0, s * 7919 + 397), 0.0);
        }
    }

    @Test
    public void fbmStaysInRange() {
        double lo = 9;
        double hi = -9;
        for (int i = 0; i < 20000; i++) {
            double v = Noise.fbm(i * 37.3, i * -11.9, 5, 800.0, 3);
            lo = Math.min(lo, v);
            hi = Math.max(hi, v);
        }
        assertTrue(lo > -1.0 && hi < 1.0 && hi - lo > 0.6);
    }

    @Test
    public void splineHitsItsControlPointsAndIsFlatOutside() {
        Spline s = Tables.COAST_SPLINE;
        assertEquals(0.60, s.eval(-5000), 0.0);
        assertEquals(-1.05, s.eval(9000), 0.0);
        assertEquals(-0.02, s.eval(0.0), 1e-12);
        assertEquals(0.31, s.eval(-400.0), 1e-12);
        assertEquals(-0.45, s.eval(450.0), 1e-12);
        // the waterline sits where Delta = 0, i.e. C ~ 0 (HANDOFF 5.2)
        assertTrue(Math.abs(s.eval(0.0)) < 0.05);
        Spline b = Tables.BASE_SPLINE;
        assertEquals(63.0, b.eval(0.05), 1e-12);
        assertEquals(68.0, b.eval(0.25), 1e-12);
        assertEquals(122.0, b.eval(1.15), 1e-12);
        // monotone: more continental -> higher base
        double prev = -1e9;
        for (double c = -1.1; c <= 1.2; c += 0.01) {
            double v = b.eval(c);
            assertTrue(v >= prev - 1e-9);
            prev = v;
        }
    }

    @Test
    public void coastRadiusIsPeriodicWithTheSeamFix() {
        // bearing table: north (-90 deg) reaches furthest (3480), the south-west (146 deg) is closest (2700)
        assertEquals(3480.0, Tables.coastRadius(0, -1000, true), 1e-9);
        assertEquals(3200.0, Tables.coastRadius(0, 1000, true), 1e-9);
        assertEquals(2700.0, Tables.coastRadius(-Math.cos(Math.toRadians(34)), Math.sin(Math.toRadians(34)), true), 1e-6);
        // across the west axis the legacy table jumps ~200 blocks; the seamless one does not
        double above = Tables.coastRadius(-1000, 0.001, false);
        double below = Tables.coastRadius(-1000, -0.001, false);
        assertTrue("legacy seam " + Math.abs(above - below), Math.abs(above - below) > 150);
        double a2 = Tables.coastRadius(-1000, 0.001, true);
        double b2 = Tables.coastRadius(-1000, -0.001, true);
        assertEquals(a2, b2, 1.0);
    }

    @Test
    public void smoothstepAndSmoothMax() {
        assertEquals(0.0, Mathx.smoothstep(0, 1, -1), 0.0);
        assertEquals(1.0, Mathx.smoothstep(0, 1, 2), 0.0);
        assertEquals(0.5, Mathx.smoothstep(0, 1, 0.5), 1e-9);
        // HANDOFF failure mode 7: smooth_max(a, 0, k) = sqrt(k)/2, not 0
        assertEquals(Math.sqrt(26.0) / 2.0, Mathx.smoothMax(0.0, 0.0, 26.0), 1e-12);
    }
}
