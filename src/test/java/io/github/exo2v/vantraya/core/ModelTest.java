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
    public void everyLandmarkCentreLandsInItsElevationZone() {
        // zones, not pins (0.2.0): no column is clamped to an exact Y any more - each centre must still land
        // inside the landmark's specified elevation band (with the verifier's pad)
        for (long seed : SEEDS) {
            VantrayaModel m = VantrayaModel.forSeed(seed);
            for (Landmark lm : Spec.LANDMARKS) {
                int h = (int) Math.rint(m.height(lm.x(), lm.z()));
                assertTrue(lm.key() + " seed " + seed + ": ground " + h + " is outside "
                                + java.util.Arrays.toString(SpecVerifier.zoneRange(lm)),
                        SpecVerifier.inZone(lm, h));
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
    public void riversReachTheMidlandsButNotThePeaks() {
        VantrayaModel m = VantrayaModel.forSeed(Spec.SPEC_SEED);
        int rivers = 0;
        for (double x = -3300; x <= 3300; x += 10) {
            for (double z = -3300; z <= 3300; z += 10) {
                Fields f = m.sample(x, z);
                if (f.river() > 0.05) {
                    rivers++;
                    // the network reaches the midlands (the first play test wanted waterways to travel by)
                    // and fades out on the high ground where the peaks are
                    assertTrue("river on the peaks (" + f.height() + ") at " + x + "," + z, f.height() < 190.0);
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
    public void uplandRiversHaveAWaterSurfaceToFill() {
        VantrayaModel m = VantrayaModel.forSeed(Spec.SPEC_SEED);
        int checked = 0;
        for (double x = -3000; x <= 3000; x += 12) {
            for (double z = -3000; z <= 3000; z += 12) {
                Fields f = m.sample(x, z);
                if (f.river() > 0.5 && f.height() > 75.0) {
                    if (f.waterLine() <= VantrayaModel.NO_WATER_LINE + 1.0) {
                        continue; // the bank of a channel that is not cut deep enough here
                    }
                    assertTrue("water below its bed at " + x + "," + z, f.waterLine() > f.height());
                    assertTrue("flooded channel (+" + (f.waterLine() - f.height()) + ") at " + x + "," + z,
                            f.waterLine() - f.height() <= 11.0);
                    checked++;
                }
            }
        }
        assertTrue("no upland channels found", checked > 100);
    }

    @Test
    public void waterwayCoverageReachesTheHighlands() {
        for (long seed : new long[] {Spec.SPEC_SEED, 424242L, 7L}) {
            VantrayaModel m = VantrayaModel.forSeed(seed);
            int cols = 0;
            int channel = 0;
            int upland = 0;
            for (double x = -3300; x <= 3300; x += 16) {
                for (double z = -3300; z <= 3300; z += 16) {
                    if (Math.hypot(x, z) > 3300) {
                        continue;
                    }
                    Fields f = m.sample(x, z);
                    if (f.cont() <= 0.05 || f.landmark() == 6) {
                        continue;
                    }
                    cols++;
                    if (f.river() > 0.3 || f.lake() > 0.4) {
                        channel++;
                        if (f.height() > 70.0) {
                            upland++;
                        }
                    }
                }
            }
            double cover = (double) channel / cols;
            double high = (double) upland / cols;
            assertTrue("seed " + seed + ": waterway cover " + cover, cover > 0.05 && cover < 0.20);
            assertTrue("seed " + seed + ": upland waterway cover " + high, high > 0.03 && high < 0.20);
        }
    }

    @Test
    public void theWaterLineIsGoneWhereThereIsNoChannel() {
        VantrayaModel m = VantrayaModel.forSeed(Spec.SPEC_SEED);
        for (double x = -2000; x <= 2000; x += 37) {
            for (double z = -2000; z <= 2000; z += 37) {
                Fields f = m.sample(x, z);
                if (f.river() <= 0.02 && f.lake() <= 0.02) {
                    assertEquals("water line on dry ground at " + x + "," + z,
                            VantrayaModel.NO_WATER_LINE, f.waterLine(), 0.0);
                }
            }
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

    // ---------------------------------------------------------------------------------------
    // the drainage network (v3): the questions the second play test showed were not being asked
    // ---------------------------------------------------------------------------------------

    /** The engine reads the height field on a 4-block grid and interpolates. */
    private static double gridSurface(VantrayaModel m, double x, double z) {
        double x0 = Math.floor(x / 4.0) * 4.0;
        double z0 = Math.floor(z / 4.0) * 4.0;
        double fx = (x - x0) / 4.0;
        double fz = (z - z0) / 4.0;
        double h00 = m.sample(x0 + 0.5, z0 + 0.5).height();
        double h10 = m.sample(x0 + 4.5, z0 + 0.5).height();
        double h01 = m.sample(x0 + 0.5, z0 + 4.5).height();
        double h11 = m.sample(x0 + 4.5, z0 + 4.5).height();
        return (h00 * (1 - fx) + h10 * fx) * (1 - fz) + (h01 * (1 - fx) + h11 * fx) * fz;
    }

    @Test
    public void waterSurfacesRunDownhillWithAtMostARapid() {
        for (long seed : new long[] {Spec.SPEC_SEED, 424242L, 7L, 17L}) {
            VantrayaModel m = VantrayaModel.forSeed(seed);
            Drainage d = m.drainage();
            int links = 0;
            for (int i = 0; i < d.cells(); i++) {
                if (!d.isChannel(i)) {
                    continue;
                }
                int to = d.drainsTo(i);
                if (to < 0 || !(d.isChannel(to) || d.isLake(to))) {
                    continue;
                }
                double up = d.waterSurface(i);
                double down = d.waterSurface(to);
                assertTrue("upstream surface " + up + " is below its downstream " + down + " at "
                        + d.cellX(i) + "," + d.cellZ(i) + " (seed " + seed + ")", up >= down - 0.55);
                assertTrue("more than one rapid between " + d.cellX(i) + "," + d.cellZ(i) + " and its downstream: "
                        + up + " -> " + down, up <= down + Drainage.MAX_RISE + 0.01);
                links++;
            }
            assertTrue("seed " + seed + ": only " + links + " channel links", links > 300);
        }
    }

    @Test
    public void everyChannelEndsAtAMouthAndStartsInACatchment() {
        for (long seed : new long[] {Spec.SPEC_SEED, 424242L, 7L, 17L}) {
            VantrayaModel m = VantrayaModel.forSeed(seed);
            Drainage d = m.drainage();
            int mouths = 0;
            int sinks = 0;
            int heads = 0;
            for (int i = 0; i < d.cells(); i++) {
                if (!d.isChannel(i)) {
                    continue;
                }
                assertTrue("a channel above the head limit at " + d.cellX(i) + "," + d.cellZ(i),
                        d.naturalH(i) < Drainage.CHANNEL_MAX_H);
                assertTrue("a head without a catchment at " + d.cellX(i) + "," + d.cellZ(i),
                        d.catchment(i) >= Drainage.HEAD_CATCHMENT);
                if (d.catchment(i) < 2 * Drainage.HEAD_CATCHMENT) {
                    heads++;
                }
                int p = i;
                for (int steps = 0; steps < 4000; steps++) {
                    int q = d.drainsTo(p);
                    if (q < 0 || d.isLake(q)) {
                        break;
                    }
                    p = q;
                }
                int q = d.drainsTo(p);
                if (q >= 0 && d.isLake(p)) {
                    mouths++; // into a lake
                } else if (q < 0) {
                    if (d.naturalH(p) <= Spec.SEA_LEVEL + 1.0) {
                        mouths++; // into the sea
                    } else if (d.cellIndex(d.cellX(p), d.cellZ(p)) < 0
                            || d.naturalH(p) < Drainage.CHANNEL_MAX_H) {
                        sinks++; // a playa in a dry basin, or off the map: a declared end, not a random stop
                    } else {
                        assertTrue("a channel that just stops at " + d.cellX(p) + "," + d.cellZ(p), false);
                    }
                }
            }
            assertTrue("seed " + seed + ": only " + mouths + " mouths and " + sinks + " sinks",
                    mouths > 200 && mouths + sinks > 0);
            assertTrue("seed " + seed + ": only " + heads + " heads", heads > 50);
        }
    }

    @Test
    public void waterNeverStandsOutsideItsChannel() {
        for (long seed : new long[] {Spec.SPEC_SEED, 31337L}) {
            VantrayaModel m = VantrayaModel.forSeed(seed);
            for (double x = -3000; x <= 3000; x += 31) {
                for (double z = -3000; z <= 3000; z += 31) {
                    Fields f = m.sample(x, z);
                    boolean dry = f.river() <= 0.02 && f.lake() <= 0.02;
                    if (dry) {
                        assertEquals("a water line with no channel at " + x + "," + z,
                                VantrayaModel.NO_WATER_LINE, f.waterLine(), 0.0);
                    } else if (f.waterLine() > VantrayaModel.NO_WATER_LINE + 1.0) {
                        assertTrue("water claimed above its bed at " + x + "," + z
                                        + " (line " + f.waterLine() + ", ground " + f.height() + ")",
                                f.height() < f.waterLine());
                    }
                }
            }
        }
    }

    @Test
    public void channelCoresCarryWaterThroughTheEngineGrid() {
        for (long seed : new long[] {Spec.SPEC_SEED, 0L, 4242L, 31337L, 7L}) {
            VantrayaModel m = VantrayaModel.forSeed(seed);
            int cores = 0;
            int dry = 0;
            for (double x = -3300; x <= 3300; x += 12) {
                for (double z = -3300; z <= 3300; z += 12) {
                    Fields f = m.sample(x, z);
                    if (f.river() > 0.85 && f.waterLine() > VantrayaModel.NO_WATER_LINE + 1.0) {
                        cores++;
                        double margin = f.waterLine() - Math.floor(gridSurface(m, x, z) + 0.5);
                        if (margin <= 0) {
                            dry++;
                        }
                    }
                }
            }
            assertTrue("seed " + seed + ": only " + cores + " channel cores", cores > 150);
            assertTrue("seed " + seed + ": " + dry + " of " + cores + " channel cores come out dry after the "
                    + "engine's grid smoothing", dry <= 0.02 * cores);
        }
    }
}
