package io.github.exo2v.vantraya.core.tools;

import java.util.Arrays;
import java.util.Locale;

import io.github.exo2v.vantraya.core.Noise;
import io.github.exo2v.vantraya.core.VantrayaModel;

/**
 * Derives the seed-independent percentile bounds in {@code Calibration.DEFAULT}.
 *
 * <p>The offline engine normalises each noise field against the percentiles of its own 8,000 x 8,000
 * raster. A chunk generator cannot, so every such percentile is replaced by a constant: the median,
 * over many world seeds, of the percentile measured on a regular sample of the same canvas.
 *
 * <pre>
 *   java -cp ... io.github.exo2v.vantraya.core.tools.Calibrate [seedCount] [spacingBlocks]
 * </pre>
 */
public final class Calibrate {
    private Calibrate() {
    }

    private static double pct(double[] sorted, double q) {
        double rank = q / 100.0 * (sorted.length - 1);
        int lo = (int) Math.floor(rank);
        int hi = Math.min(lo + 1, sorted.length - 1);
        return sorted[lo] + (sorted[hi] - sorted[lo]) * (rank - lo);
    }

    private static double median(double[] v) {
        double[] s = v.clone();
        Arrays.sort(s);
        return pct(s, 50.0);
    }

    public static void main(String[] args) {
        int seeds = args.length > 0 ? Integer.parseInt(args[0]) : 48;
        int spacing = args.length > 1 ? Integer.parseInt(args[1]) : 40;
        int n = 8000 / spacing;
        String[] names = {"cont", "ero", "rid", "veil", "dunesEnv", "dunesMesa", "reachBar", "reachAtoll",
                "regionalTemp", "continentality", "humPatch"};
        double[] qlo = {1, 1, 1, 3, 3, 4, 6, 8, 2, -1, -1};
        double[] qhi = {99, 99, 99, 97, 97, 96, 94, 92, 98, -1, -1};
        double[][] lo = new double[names.length][seeds];
        double[][] hi = new double[names.length][seeds];
        double[] buf = new double[n * n];
        for (int s = 0; s < seeds; s++) {
            long seed = 1_000_003L * (s + 1) + 17L;
            VantrayaModel m = new VantrayaModel(seed, VantrayaModel.Config.production());
            for (int f = 0; f < names.length; f++) {
                int k = 0;
                for (int j = 0; j < n; j++) {
                    for (int i = 0; i < n; i++) {
                        double x = -4000 + (i + 0.5) * spacing;
                        double z = -4000 + (j + 0.5) * spacing;
                        buf[k++] = field(f, m, seed, x, z);
                    }
                }
                double[] sorted = buf.clone();
                Arrays.sort(sorted);
                if (qlo[f] < 0) {
                    lo[f][s] = sorted[0];
                    hi[f][s] = sorted[sorted.length - 1];
                } else {
                    lo[f][s] = pct(sorted, qlo[f]);
                    hi[f][s] = pct(sorted, qhi[f]);
                }
            }
        }
        System.out.println("// seeds=" + seeds + " spacing=" + spacing);
        for (int f = 0; f < names.length; f++) {
            double[] l = lo[f].clone();
            double[] h = hi[f].clone();
            Arrays.sort(l);
            Arrays.sort(h);
            System.out.println(String.format(Locale.ROOT,
                    "%-15s lo median %9.5f (min %9.5f max %9.5f)   hi median %9.5f (min %9.5f max %9.5f)",
                    names[f], median(lo[f]), l[0], l[l.length - 1], median(hi[f]), h[0], h[h.length - 1]));
        }
        StringBuilder sb = new StringBuilder("new Calibration(\n");
        for (int f = 0; f < names.length; f++) {
            sb.append(String.format(Locale.ROOT, "        %.6f, %.6f%s%n", median(lo[f]), median(hi[f]),
                    f == names.length - 1 ? ");" : ","));
        }
        System.out.println(sb);
    }

    private static double field(int f, VantrayaModel m, long seed, double x, double z) {
        switch (f) {
            case 0:
                return m.rawContinentalness(x, z);
            case 1:
                return Noise.fbm(x, z, 4, 1500.0, seed + 31);
            case 2:
                return Noise.fbm(x, z, 5, 1250.0, seed + 41);
            case 3:
                return Noise.fbm(x, z, 3, 1400.0, seed + 1301);
            case 4:
                return Noise.fbm(x, z, 3, 1500.0, seed + 4 * 137L + 605);
            case 5:
                return Noise.fbm(x, z, 3, 760.0, seed + 4 * 137L + 611);
            case 6:
                return Noise.fbm(x, z, 2, 320.0, seed + 6 * 137L + 807);
            case 7:
                return Noise.fbm(x, z, 3, 280.0, seed + 6 * 137L + 811);
            case 8:
                return Noise.fbm(x, z, 4, 3200.0, seed + 101);
            case 9:
                return Noise.fbm(x, z, 3, 1920.0, seed + 303);
            default:
                return Noise.fbm(x, z, 3, 1600.0, seed + 505);
        }
    }
}
