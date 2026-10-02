package io.github.exo2v.vantraya.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertTrue;

import java.util.Random;
import java.util.stream.IntStream;

import org.junit.Test;

import io.github.exo2v.vantraya.core.Spec.Kind;
import io.github.exo2v.vantraya.core.Spec.Landmark;
import io.github.exo2v.vantraya.core.VantrayaModel.Fields;

/** Invariants of the production model: the specification's hard numbers must hold for every seed. */
public class ModelTest {

    private static final long[] SEEDS = {Spec.SPEC_SEED, 1L, 42L, -7L, 123456789012345L, 987654321L, 31337L};

    @Test
    public void deterministicForASeedAndDifferentAcrossSeeds() {
        VantrayaModel a = new VantrayaModel(5, VantrayaModel.Config.production());
        VantrayaModel b = new VantrayaModel(5, VantrayaModel.Config.production());
        VantrayaModel c = new VantrayaModel(6, VantrayaModel.Config.production());
        boolean differs = false;
        Random rnd = new Random(1);
        for (int i = 0; i < 300; i++) {
            double x = rnd.nextDouble() * 8000 - 4000;
            double z = rnd.nextDouble() * 8000 - 4000;
            assertEquals(a.sample(x, z), b.sample(x, z));
            differs |= Math.abs(a.sample(x, z).height() - c.sample(x, z).height()) > 0.5;
        }
        assertTrue("two different seeds produced the same terrain", differs);
    }

    @Test
    public void everyLandmarkCentreLandsOnItsSpecifiedElevation() {
        for (long seed : SEEDS) {
            VantrayaModel m = VantrayaModel.forSeed(seed);
            for (Landmark lm : Spec.LANDMARKS) {
                double h = m.height(lm.x(), lm.z());
                double want = lm.kind() == Kind.CALDERA ? Spec.CALDERA_THRONE : lm.y(); // the centre is the Obsidian Throne
                assertEquals(lm.key() + " seed " + seed, want, h, 0.5);
            }
        }
    }

    @Test
    public void landmarkOwnershipFollowsTheBoxesAndTheVeil() {
        assertEquals(0, Regions.idAt(0, 2500));
        assertEquals(1, Regions.idAt(-2100, 0));
        assertEquals(2, Regions.idAt(0, 0));
        assertEquals(3, Regions.idAt(0, -2500));
        assertEquals(4, Regions.idAt(2300, 0));
        assertEquals(5, Regions.idAt(2000, 2000));
        assertEquals(6, Regions.idAt(-2400, 1600));
        assertEquals(7, Regions.idAt(-1800, -1800));
        assertEquals(8, Regions.idAt(1800, -1800));
        assertEquals(-1, Regions.idAt(0, 1000));
        assertEquals(Spec.VEIL_ID, Regions.idAt(-3000, -3000)); // r = 4243: the Veil owns the corners of the canvas
        // the smaller box wins where the Hermit's Spire and the Choir sit inside the cordillera's box
        assertEquals(7, Regions.idAt(-1500, -2000));
        assertEquals(8, Regions.idAt(1500, -2000));
        assertEquals(3, Regions.idAt(-1000, -2000));
        // the Veil overrides everything beyond r > 3550
        assertEquals(Spec.VEIL_ID, Regions.idAt(3600, 0));
        assertEquals(Spec.VEIL_ID, Regions.idAt(-3200, 2300)); // inside the Reach's box but r = 3941
        assertEquals(Spec.VEIL_ID, Regions.idAt(0, -3540 - 20));
    }

    @Test
    public void veilOfSaltIsAbyssal() {
        for (long seed : new long[] {Spec.SPEC_SEED, 99L}) {
            VantrayaModel m = VantrayaModel.forSeed(seed);
            Random rnd = new Random(seed);
            for (int i = 0; i < 400; i++) {
                double a = rnd.nextDouble() * Math.PI * 2;
                double r = 3620 + rnd.nextDouble() * 1800;
                double h = m.height(r * Math.cos(a), r * Math.sin(a));
                assertTrue("abyss floor at r=" + r + " was " + h, h >= Spec.ABYSS_FLOOR - 1e-9 && h <= Spec.ABYSS_FLOOR + 9.0);
            }
        }
    }

    @Test
    public void shelfDropsAwayBeyondTheContinent() {
        VantrayaModel m = VantrayaModel.forSeed(Spec.SPEC_SEED);
        for (double deg = 0; deg < 360; deg += 15) {
            double a = Math.toRadians(deg);
            double inner = m.height(2000 * Math.cos(a), 2000 * Math.sin(a));
            double outer = m.height(3750 * Math.cos(a), 3750 * Math.sin(a));
            assertTrue("continent interior is above the sea at bearing " + deg + ": " + inner, inner > 40);
            assertTrue("abyss at bearing " + deg + ": " + outer, outer < 0);
        }
    }

    @Test
    public void calderaIsNeverWetAndLavaStaysInsideIt() {
        for (long seed : new long[] {Spec.SPEC_SEED, 8L, 2024L}) {
            VantrayaModel m = VantrayaModel.forSeed(seed);
            int dry = 0;
            for (double x = -800; x <= 800; x += 8) {
                for (double z = -800; z <= 800; z += 8) {
                    Fields f = m.sample(x, z);
                    if (f.noWater()) {
                        dry++;
                        assertEquals("river in the caldera", 0.0, f.river(), 0.0);
                        assertEquals("lake in the caldera", 0.0, f.lake(), 0.0);
                        assertEquals("the no-water mask only covers the caldera", 2, f.landmark());
                    }
                    if (f.lava()) {
                        assertTrue(Math.hypot(x, z) < 330);
                    }
                }
            }
            assertTrue("no dry crater cells for seed " + seed, dry > 1000);
        }
    }

    @Test
    public void closedBasinLandformsKeepTheirRiversButNeverPond() {
        for (long seed : new long[] {Spec.SPEC_SEED, 17L}) {
            VantrayaModel m = VantrayaModel.forSeed(seed);
            int dryCells = 0;
            for (double x = -3300; x <= 3300; x += 24) {
                for (double z = -3300; z <= 3300; z += 24) {
                    Fields f = m.sample(x, z);
                    if (f.noLake()) {
                        dryCells++;
                        assertEquals("standing water on a dry landform", 0.0, f.lake(), 0.0);
                    }
                }
            }
            assertTrue(dryCells > 2000);
        }
    }

    @Test
    public void riversAreLowlandFeatures() {
        VantrayaModel m = VantrayaModel.forSeed(Spec.SPEC_SEED);
        int rivers = 0;
        for (double x = -3300; x <= 3300; x += 10) {
            for (double z = -3300; z <= 3300; z += 10) {
                Fields f = m.sample(x, z);
                if (f.river() > 0.05) {
                    rivers++;
                    assertTrue("river on high ground (" + f.height() + ") at " + x + "," + z, f.height() < 104.0);
                }
            }
        }
        assertTrue("no rivers generated", rivers > 500);
    }

    @Test
    public void inlandWaterCoverIsModerate() {
        for (long seed : new long[] {Spec.SPEC_SEED, 424242L, 7L}) {
            VantrayaModel m = VantrayaModel.forSeed(seed);
            int inland = 0;
            int water = 0;
            for (double x = -3300; x <= 3300; x += 16) {
                for (double z = -3300; z <= 3300; z += 16) {
                    if (Math.hypot(x, z) > 3300) {
                        continue;
                    }
                    Fields f = m.sample(x, z);
                    if (f.cont() > 0.05 && f.landmark() != 6) {
                        inland++;
                        if (f.height() < Spec.SEA_LEVEL) {
                            water++;
                        }
                    }
                }
            }
            double share = (double) water / inland;
            assertTrue("seed " + seed + ": inland water share " + share, share > 0.015 && share < 0.12);
        }
    }

    @Test
    public void heightsStayInsideTheWorld() {
        Random rnd = new Random(3);
        for (long seed : SEEDS) {
            VantrayaModel m = VantrayaModel.forSeed(seed);
            for (int i = 0; i < 3000; i++) {
                double h = m.height(rnd.nextDouble() * 9000 - 4500, rnd.nextDouble() * 9000 - 4500);
                assertTrue(h >= Spec.ABYSS_FLOOR - 1e-9 && h <= Spec.SPINE_HIGH + 4.0 + 1e-9);
            }
        }
    }

    @Test
    public void concurrentSamplingGivesTheSameAnswer() {
        VantrayaModel m = VantrayaModel.forSeed(77L);
        int n = 20000;
        double[] seq = new double[n];
        Random rnd = new Random(5);
        double[] xs = new double[n];
        double[] zs = new double[n];
        for (int i = 0; i < n; i++) {
            xs[i] = Math.rint(rnd.nextDouble() * 8000 - 4000);
            zs[i] = Math.rint(rnd.nextDouble() * 8000 - 4000);
            seq[i] = m.height(xs[i], zs[i]);
        }
        double[] par = new double[n];
        IntStream.range(0, n).parallel().forEach(i -> par[i] = m.height(xs[i], zs[i]));
        for (int i = 0; i < n; i++) {
            assertEquals(seq[i], par[i], 0.0);
        }
    }

    @Test
    public void canonicalSeedUsesTheShippedInstanceTables() {
        VantrayaModel canon = VantrayaModel.forSeed(Spec.SPEC_SEED);
        assertEquals(31, canon.spine().size());
        assertEquals(6, canon.needles().size());
        VantrayaModel other = VantrayaModel.forSeed(11L);
        assertEquals(31, other.spine().size());
        assertTrue(other.needles().size() >= 6 && other.needles().size() <= 9);
        assertNotEquals(canon.spine().get(1).cx(), other.spine().get(1).cx(), 0.0);
        // the first station is the summit pinned at the landmark's exact centre
        assertEquals(0.0, other.spine().get(0).cx(), 0.0);
        assertEquals(-2500.0, other.spine().get(0).cz(), 0.0);
    }

    @Test
    public void climateTiersFollowTheLandmarks() {
        VantrayaModel m = VantrayaModel.forSeed(Spec.SPEC_SEED);
        // effective temperature at the centre (lapse included)
        assertEquals("glacial spine", 0, m.sample(0, -2500).tier());
        // the Hermit's Spire is the boreal tier by its window (T -0.60..-0.25); its summit is colder still once the lapse is applied
        assertTrue("hermit's spire base temperature", Spec.climateTier(m.sample(-1800, -1800).tempAsh()) <= 2);
        assertTrue("hermit's spire summits are glacial or boreal", m.sample(-1800, -1800).tier() <= 1);
        assertEquals("forgotten coast (temperate)", 2, m.sample(0, 2500).tier());
        // base temperature, before the altitude lapse, is what the volcanic and arid tiers refer to
        assertEquals("ashen caldera", 4, Spec.climateTier(m.sample(0, 0).tempAsh()));
        assertEquals("gilded dunes", 4, Spec.climateTier(m.sample(2300, 0).tempAsh()));
        assertEquals("whispering fen", 3, Spec.climateTier(m.sample(2000, 2000).tempAsh()));
    }

    @Test
    public void snowNeverTouchesTemperateTerrainWithoutABorealBelt() {
        // spec section 4, tier 1: a mandatory 600-block non-snowy buffer around the glacial tier
        for (long seed : new long[] {Spec.SPEC_SEED, 5L, 99999L}) {
            VantrayaModel m = VantrayaModel.forSeed(seed);
            Landmark spine = Spec.byKey("glacial_spine");
            int checked = 0;
            for (double x = spine.x1(); x <= spine.x2(); x += 60) {
                for (double outside = 5; outside <= 600; outside += 25) {
                    double z = spine.z2() + outside; // south edge of the cordillera, going away from it
                    if (Regions.idAt(x, z) != -1) {
                        continue; // landmarks with their own windows (Hermit, Choir, Caldera ...) take precedence
                    }
                    Fields f = m.sample(x, z);
                    if (f.height() < Spec.SEA_LEVEL) {
                        continue;
                    }
                    checked++;
                    assertTrue("snow temperature inside the boreal belt: T=" + f.temperature() + " at " + x + "," + z,
                            f.temperature() > -0.68);
                }
            }
            assertTrue(checked > 200);
        }
    }
}
