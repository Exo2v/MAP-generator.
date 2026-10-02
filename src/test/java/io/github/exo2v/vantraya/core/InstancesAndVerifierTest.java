package io.github.exo2v.vantraya.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import java.util.List;
import java.util.SplittableRandom;

import org.junit.Test;

import io.github.exo2v.vantraya.core.Instances.Instance;
import io.github.exo2v.vantraya.core.Instances.Type;

public class InstancesAndVerifierTest {

    @Test
    public void catmullRomPassesThroughItsWaypoints() {
        double[][] wp = {{0, 0}, {100, 50}, {200, -30}, {300, 10}};
        double[][] s = Instances.catmullRom(wp, 301);
        assertEquals(0.0, s[0][0], 1e-9);
        assertEquals(0.0, s[0][1], 1e-9);
        assertEquals(100.0, s[100][0], 1e-9);
        assertEquals(50.0, s[100][1], 1e-9);
        assertEquals(200.0, s[200][0], 1e-9);
        assertEquals(300.0, s[300][0], 1e-9);
        assertEquals(10.0, s[300][1], 1e-9);
    }

    @Test
    public void stationsRespectTheSpacingAndCount() {
        double[][] wp = {{-1800, -2740}, {-900, -2360}, {0, -2560}, {900, -2780}, {1800, -2520}};
        double[][] spline = Instances.catmullRom(wp, 768);
        List<double[]> st = Instances.poissonStations(spline, 30, 200, 0, new SplittableRandom(4));
        assertEquals(30, st.size());
        for (int i = 0; i < st.size(); i++) {
            for (int j = i + 1; j < st.size(); j++) {
                double d = Math.hypot(st.get(i)[0] - st.get(j)[0], st.get(i)[1] - st.get(j)[1]);
                assertTrue("stations too close: " + d, d >= 200 * Math.pow(0.7, 3) - 1e-6);
            }
        }
    }

    @Test
    public void cordilleraAndNeedlesFollowTheSpecifiedDistributions() {
        for (long seed : new long[] {1, 2, 3, 4, 5}) {
            List<Instance> spine = Instances.buildCordillera(Spec.byKey("glacial_spine"), seed);
            assertEquals(31, spine.size());
            assertEquals(0.0, spine.get(0).cx(), 0.0);
            assertEquals(-2500.0, spine.get(0).cz(), 0.0);
            int peaks = 0;
            for (Instance in : spine) {
                if (in.type() == Type.PEAK) {
                    peaks++;
                    assertTrue(in.radius() >= 150 && in.radius() <= 210 && in.arms() >= 3 && in.arms() <= 5);
                    assertEquals(Spec.SPINE_HIGH - 10.0, in.height(), 1e-9);
                } else {
                    assertTrue(in.length() >= 320 && in.length() <= 620);
                    assertTrue("crest width sigma in [38, 62]", in.sigma() >= 38 && in.sigma() <= 62);
                    assertEquals(Spec.SPINE_HIGH - 24.0, in.height(), 1e-9);
                }
            }
            assertTrue("peak share " + peaks, peaks >= 4 && peaks <= 24);
            List<Instance> needles = Instances.buildNeedles(Spec.byKey("hermits_spire"), seed);
            assertTrue(needles.size() >= 6 && needles.size() <= 9);
            for (Instance in : needles) {
                assertTrue(Math.abs(in.cx() + 1800) <= 420 && Math.abs(in.cz() + 1800) <= 420);
                assertTrue(in.height() >= 38 && in.height() <= 46 && in.radius() >= 46 && in.radius() <= 84);
            }
        }
    }

    @Test
    public void aSummitInstanceRisesToItsHeightAndFallsToZero() {
        Instance peak = new Instance(Type.PEAK, 0, 0, 100, 0, 0, 0, 200, 4, 1.9, 0.0, 0.0, 1);
        double top = Instances.relief(List.of(peak), 0, 0, 0);
        assertTrue("summit " + top, top > 80 && top < 105);
        assertEquals(0.0, Instances.relief(List.of(peak), 250, 0, 0), 0.0);
        assertEquals(0.0, Instances.relief(List.of(), 10, 10, 0), 0.0);
    }

    @Test
    public void theVerifierPassesForEverySeed() {
        for (long seed : new long[] {Spec.SPEC_SEED, 1L, 2L, 3L, 99L, 123456789L, -5L}) {
            List<SpecVerifier.Check> checks = SpecVerifier.run(SpecVerifier.modelProbe(VantrayaModel.forSeed(seed)));
            for (SpecVerifier.Check c : checks) {
                assertTrue("seed " + seed + ": " + c, c.pass());
            }
            assertTrue(checks.size() >= 20);
        }
    }

    @Test
    public void theVerifierFailsAWorldThatIsNotTheSpecification() {
        SpecVerifier.Probe flat = new SpecVerifier.Probe() {
            @Override
            public int groundHeight(int x, int z) {
                return 70;
            }

            @Override
            public BiomeRole role(int x, int y, int z) {
                return BiomeRole.PLAINS;
            }
        };
        List<SpecVerifier.Check> checks = SpecVerifier.run(flat);
        assertTrue(!SpecVerifier.allPass(checks));
        long failed = checks.stream().filter(c -> !c.pass()).count();
        assertTrue("a flat world should fail many checks: " + failed, failed >= 10);
    }
}
