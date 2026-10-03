package io.github.exo2v.vantraya.router;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import java.util.ArrayList;
import java.util.List;

import org.junit.Test;

import com.google.gson.JsonElement;
import com.google.gson.JsonParser;

import io.github.exo2v.vantraya.core.Spec;
import io.github.exo2v.vantraya.core.SpecVerifier;
import io.github.exo2v.vantraya.core.VantrayaModel;

/**
 * The offline acceptance of the 0.3.0 terrain: vanilla's offset / factor / jaggedness / depth /
 * sloped-cheese spline stack (kept as data) driven by the specification's parameter maps, evaluated here
 * without Minecraft. It asks the questions the play-test document asked - is there a continent, do the
 * landmarks sit in their zones, does the river band carve a valley that runs downhill to the sea - not
 * "does the code run".
 */
public class RouterTest {

    private static final String SETTINGS = "/data/vantraya_builder/worldgen/noise_settings/vantraya.json";

    private final VantrayaModel model = VantrayaModel.forSeed(7L);
    private final DensityInterpreter interp = new DensityInterpreter(model);

    private JsonElement router(String slot) {
        JsonElement root = DensityInterpreter.parseResource(SETTINGS);
        return root.getAsJsonObject().get("noise_router").getAsJsonObject().get(slot);
    }

    /** The Y where {@code final_density} crosses zero - the terrain surface, by bisection. */
    private double surfaceY(double x, double z) {
        JsonElement fd = router("final_density");
        double lo = -64.0;
        double hi = 320.0;
        // density falls as y rises: solid below the surface, air above
        double atLo = interp.eval(fd, x, lo, z);
        double atHi = interp.eval(fd, x, hi, z);
        assertTrue("density must be solid at min_y (" + atLo + ")", atLo > 0.0);
        assertTrue("density must be air at max_y (" + atHi + ")", atHi < 0.0);
        for (int i = 0; i < 40; i++) {
            double mid = 0.5 * (lo + hi);
            if (interp.eval(fd, x, mid, z) > 0.0) {
                lo = mid;
            } else {
                hi = mid;
            }
        }
        return 0.5 * (lo + hi);
    }

    @Test
    public void everyReferenceInTheStackResolves() {
        for (String slot : new String[] {"continents", "depth", "erosion", "ridges", "temperature",
                "vegetation", "initial_density_without_jaggedness", "final_density"}) {
            interp.resolveAll(router(slot));
        }
    }

    @Test
    public void theOceanIsOceanAndTheInteriorIsLand() {
        for (long seed : new long[] {7L, 42L, 20250929L}) {
            VantrayaModel m = new VantrayaModel(seed, VantrayaModel.Config.production());
            DensityInterpreter ip = new DensityInterpreter(m);
            // out past the Veil the ground must be sea floor; the interior must be land
            double deep = 0;
            double land = 0;
            int deepN = 0;
            int landN = 0;
            for (int k = 0; k < 60; k++) {
                double a = k * Math.PI / 30.0;
                double ox = 4200 * Math.cos(a);
                double oz = 4200 * Math.sin(a);
                VantrayaModel.Fields f = m.sample(ox, oz);
                if (f.cont() < -0.6) {
                    deep += surfaceYWith(ip, ox, oz);
                    deepN++;
                }
                // the interior: within r=1200 of the caldera, high continentalness
                double ia = k * Math.PI / 30.0;
                double ix = 900 * Math.cos(ia);
                double iz = 900 * Math.sin(ia);
                VantrayaModel.Fields fi = m.sample(ix, iz);
                if (fi.cont() > 0.3) {
                    land += surfaceYWith(ip, ix, iz);
                    landN++;
                }
            }
            assertTrue("deep-ocean columns must exist", deepN >= 10);
            assertTrue("interior columns must exist", landN >= 10);
            assertTrue("deep ocean is under water: " + (deep / deepN), deep / deepN < Spec.SEA_LEVEL - 5);
            assertTrue("interior is dry land: " + (land / landN), land / landN > Spec.SEA_LEVEL + 3);
        }
    }

    // The stack is seed-independent JSON but the fields are not: one interpreter, many models.
    private double surfaceYWith(DensityInterpreter ip, double x, double z) {
        JsonElement fd = router("final_density");
        double lo = -64.0;
        double hi = 320.0;
        for (int i = 0; i < 40; i++) {
            double mid = 0.5 * (lo + hi);
            if (ip.eval(fd, x, mid, z) > 0.0) {
                lo = mid;
            } else {
                hi = mid;
            }
        }
        return 0.5 * (lo + hi);
    }

    @Test
    public void theLandmarksHoldTheirElevationZones() {
        // one probe per landmark: the median of the columns of a small disc about the centre
        for (long seed : new long[] {7L, 42L}) {
            VantrayaModel m = new VantrayaModel(seed, VantrayaModel.Config.production());
            DensityInterpreter ip = new DensityInterpreter(m);
            for (Spec.Landmark lm : Spec.LANDMARKS) {
                List<Double> ys = new ArrayList<>();
                for (int dx = -48; dx <= 48; dx += 24) {
                    for (int dz = -48; dz <= 48; dz += 24) {
                        ys.add(surfaceYWith(ip, lm.x() + dx, lm.z() + dz));
                    }
                }
                ys.sort(Double::compare);
                double median = ys.get(ys.size() / 2);
                double[] zone = SpecVerifier.zoneRange(lm);
                double slack = 15.0; // zones, not pins: the noise is allowed its texture
                assertTrue(lm.name() + " median Y=" + median + " outside " + zone[0] + ".." + zone[1],
                        median >= zone[0] - slack && median <= zone[1] + slack);
            }
        }
    }

    @Test
    public void riverValleysCarveBelowTheirRidges() {
        // the ridges map's valley band is where the offset spline dips: columns there must sit lower
        // than ridge-band columns of the same inland climate, and the deepest must reach the water
        long seed = 7L;
        VantrayaModel m = new VantrayaModel(seed, VantrayaModel.Config.production());
        DensityInterpreter ip = new DensityInterpreter(m);
        double valleySum = 0;
        double ridgeSum = 0;
        int valleyN = 0;
        int ridgeN = 0;
        double lowestValley = Double.MAX_VALUE;
        for (int k = 0; k < 3000; k++) {
            double x = ((k * 7919) % 5200) - 2600;
            double z = ((k * 6151) % 5200) - 2600;
            VantrayaModel.Fields f = m.sample(x, z);
            if (f.cont() < -0.11 || f.cont() > 0.55) {
                continue; // inland low/mid country only
            }
            double y = surfaceYWith(ip, x, z);
            if (Math.abs(f.ridges()) < 0.05) {
                valleySum += y;
                valleyN++;
                lowestValley = Math.min(lowestValley, y);
            } else if (Math.abs(f.ridges()) > 0.45) {
                ridgeSum += y;
                ridgeN++;
            }
        }
        assertTrue("valley columns found: " + valleyN, valleyN >= 15);
        assertTrue("ridge columns found: " + ridgeN, ridgeN >= 15);
        double valleyMean = valleySum / valleyN;
        double ridgeMean = ridgeSum / ridgeN;
        assertTrue("valley mean " + valleyMean + " must sit below ridge mean " + ridgeMean,
                valleyMean < ridgeMean - 8.0);
        assertTrue("some river bed reaches the water (lowest valley Y=" + lowestValley + ")",
                lowestValley < Spec.SEA_LEVEL);
    }

    @Test
    public void theParameterMapsStayWithinVanillaRanges() {
        for (int k = 0; k < 500; k++) {
            double x = ((k * 3571) % 8000) - 4000;
            double z = ((k * 2477) % 8000) - 4000;
            VantrayaModel.Fields f = model.sample(x, z);
            assertTrue("cont " + f.cont(), f.cont() >= -1.2 && f.cont() <= 1.2);
            assertTrue("ero " + f.erosion(), f.erosion() >= -1.0 && f.erosion() <= 1.0);
            assertTrue("ridges " + f.ridges(), f.ridges() >= -1.0 && f.ridges() <= 1.0);
            assertTrue("temp " + f.temperature(), f.temperature() >= -1.2 && f.temperature() <= 1.2);
            assertTrue("hum " + f.humidity(), f.humidity() >= -1.2 && f.humidity() <= 1.2);
        }
    }

    @Test
    public void theSurfaceIsContinuousNotTerraced() {
        // one-block steps between neighbouring columns, averaged over a hillside: big flat terraces with
        // vertical risers were the 0.2.0 artifact; the spline stack must move smoothly
        double maxJump = 0;
        for (int k = 0; k < 60; k++) {
            double x = 300 + k * 37.0;
            double z = -2500 + k * 13.0;
            double prev = surfaceY(x, z);
            for (int j = 1; j <= 30; j++) {
                double y = surfaceY(x + j, z);
                maxJump = Math.max(maxJump, Math.abs(y - prev));
                prev = y;
            }
        }
        assertTrue("steepest one-block step " + maxJump, maxJump <= 6.0);
    }

    @Test
    public void splineMathMatchesCubicSpline() {
        // the interpreter's Hermite, against the formula from net.minecraft.util.CubicSpline by hand.
        // coordinate = y_clamped_gradient(-64..320 -> -1..1), so the spline input tracks y linearly.
        JsonElement s = JsonParser.parseString(
                "{\"coordinate\": {\"type\": \"minecraft:y_clamped_gradient\", "
                        + "\"from_y\": -64.0, \"to_y\": 320.0, \"from_value\": -2.0, \"to_value\": 2.0}, "
                        + "\"points\": ["
                        + "{\"location\": -1.0, \"value\": 0.0, \"derivative\": 1.0},"
                        + "{\"location\": 1.0, \"value\": 2.0, \"derivative\": 1.0}]}");
        assertEquals(1.0, interp.evalSplinePublic(s, 0, 128, 0), 1e-9);  // f = 0: the Hermite midpoint
        assertEquals(0.0, interp.evalSplinePublic(s, 0, 32, 0), 1e-9);    // f = -1: the first point
        assertEquals(2.0, interp.evalSplinePublic(s, 0, 224, 0), 1e-9);   // f = 1: the last point
        // linear extensions at the clamped ends: 2 + 1*(f-1) and 0 + 1*(f+1) with f = 2 and f = -2
        assertEquals(3.0, interp.evalSplinePublic(s, 0, 320, 0), 1e-4);
        assertEquals(-1.0, interp.evalSplinePublic(s, 0, -64, 0), 1e-4);
    }
}
