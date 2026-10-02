package io.github.exo2v.vantraya.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

import org.junit.BeforeClass;
import org.junit.Test;

/**
 * Cross-checks the Java model against the offline Python engine it was ported from.
 *
 * <p>{@code ashenfall_reference_s20250929.csv} holds 560 columns read from the Python
 * {@code AshenfallBuilder.build()} output at the specification's 4-block resolution (seed 20250929),
 * stratified over every landmark, the free terrain, the lava floor and the dry masks. The model is built
 * in "parity" configuration - no hydrology, no re-pin, no boreal buffer, the offline engine's own
 * percentile bounds and the very mountain instances it stamped - so what remains is a like-for-like
 * comparison of the maths: warped coast radius, splines, windows, relief, shelf, Veil, the nine shapers.
 *
 * <p>The only differences left are the raster-versus-analytic feather (a cell-size offset in a 220-block
 * taper) and rounding in the CSV; the tolerances below are set from the measured spread.
 */
public class ParityTest {

    private record Row(double x, double z, int id, double cont, double ero, double rid, double dem,
                       double temp, double hum, boolean lava, boolean dry) {
    }

    private static final List<Row> ROWS = new ArrayList<>();
    private static VantrayaModel model;

    @BeforeClass
    public static void load() throws IOException {
        try (InputStream in = ParityTest.class.getResourceAsStream("/parity/ashenfall_reference_s20250929.csv")) {
            assertTrue("reference CSV missing", in != null);
            BufferedReader r = new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8));
            String line;
            while ((line = r.readLine()) != null) {
                if (line.isBlank() || line.startsWith("#")) {
                    continue;
                }
                String[] p = line.trim().split("\\s+");
                ROWS.add(new Row(Double.parseDouble(p[0]), Double.parseDouble(p[1]), Integer.parseInt(p[2]),
                        Double.parseDouble(p[3]), Double.parseDouble(p[4]), Double.parseDouble(p[5]),
                        Double.parseDouble(p[6]), Double.parseDouble(p[7]), Double.parseDouble(p[8]),
                        Integer.parseInt(p[9]) == 1, Integer.parseInt(p[10]) == 1));
            }
        }
        model = new VantrayaModel(Spec.SPEC_SEED,
                VantrayaModel.Config.parity(4, Calibration.SPEC_SEED_REFERENCE),
                SpecSeedInstances.SPINE, SpecSeedInstances.NEEDLES);
    }

    @Test
    public void referenceHasEveryStratum() {
        assertTrue(ROWS.size() >= 500);
        boolean[] seen = new boolean[11];
        for (Row r : ROWS) {
            seen[r.id() + 1] = true;
        }
        for (int i = 0; i < seen.length; i++) {
            assertTrue("no reference rows for landmark id " + (i - 1), seen[i]);
        }
    }

    @Test
    public void landmarkOwnershipLavaAndDryMasksAreIdentical() {
        for (Row r : ROWS) {
            VantrayaModel.Fields f = model.sample(r.x(), r.z());
            assertEquals("landmark id at " + r.x() + "," + r.z(), r.id(), f.landmark());
            assertEquals("lava mask at " + r.x() + "," + r.z(), r.lava(), f.lava());
            assertEquals("no-water mask at " + r.x() + "," + r.z(), r.dry(), f.noWater());
        }
    }

    @Test
    public void lithosphereFieldsMatch() {
        double sumC = 0;
        double sumE = 0;
        double sumR = 0;
        double maxC = 0;
        double maxE = 0;
        double maxR = 0;
        for (Row r : ROWS) {
            VantrayaModel.Fields f = model.sample(r.x(), r.z());
            double dc = Math.abs(f.cont() - r.cont());
            double de = Math.abs(f.erosion() - r.ero());
            double dr = Math.abs(f.ridges() - r.rid());
            sumC += dc;
            sumE += de;
            sumR += dr;
            maxC = Math.max(maxC, dc);
            maxE = Math.max(maxE, de);
            maxR = Math.max(maxR, dr);
            if (r.id() == -1) {
                // outside every landmark the fields are not windowed at all: exact up to CSV rounding
                assertEquals("free-terrain continentalness", r.cont(), f.cont(), 2e-6);
            }
        }
        int n = ROWS.size();
        assertTrue("mean |cont diff| " + sumC / n, sumC / n < 0.002);
        assertTrue("mean |erosion diff| " + sumE / n, sumE / n < 0.002);
        assertTrue("mean |ridges diff| " + sumR / n, sumR / n < 0.002);
        assertTrue("max |cont diff| " + maxC, maxC < 0.03);
        assertTrue("max |erosion diff| " + maxE, maxE < 0.03);
        assertTrue("max |ridges diff| " + maxR, maxR < 0.03);
    }

    @Test
    public void heightFieldMatchesTheOfflineDem() {
        double sum = 0;
        double max = 0;
        int over1 = 0;
        for (Row r : ROWS) {
            double d = Math.abs(model.sample(r.x(), r.z()).height() - r.dem());
            sum += d;
            max = Math.max(max, d);
            if (d > 1.0) {
                over1++;
            }
        }
        int n = ROWS.size();
        assertTrue("mean |dem diff| = " + sum / n, sum / n < 0.15);
        assertTrue("max |dem diff| = " + max, max < 5.0);
        assertTrue("share of columns off by more than one block: " + over1 + "/" + n, over1 < 0.06 * n);
    }

    @Test
    public void climateMatches() {
        double sumT = 0;
        double sumH = 0;
        double maxT = 0;
        double maxH = 0;
        for (Row r : ROWS) {
            VantrayaModel.Fields f = model.sample(r.x(), r.z());
            double dt = Math.abs((f.tempAsh() + 1) * 0.5 - r.temp());
            double dh = Math.abs((f.humAsh() + 1) * 0.5 - r.hum());
            sumT += dt;
            sumH += dh;
            maxT = Math.max(maxT, dt);
            maxH = Math.max(maxH, dh);
        }
        int n = ROWS.size();
        assertTrue("mean |temperature diff| " + sumT / n, sumT / n < 0.002);
        assertTrue("max |temperature diff| " + maxT, maxT < 0.03);
        // humidity uses a closed form of the offline engine's 26-step moisture advection
        assertTrue("mean |humidity diff| " + sumH / n, sumH / n < 0.01);
        assertTrue("max |humidity diff| " + maxH, maxH < 0.05);
    }
}
