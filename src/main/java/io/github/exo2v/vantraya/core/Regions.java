package io.github.exo2v.vantraya.core;

import java.util.List;

import io.github.exo2v.vantraya.core.Spec.Landmark;

/**
 * Landmark membership and the feathered masks built on it.
 *
 * <p>The offline engine computes these on a raster with Euclidean distance transforms. At runtime
 * every column is generated on its own, so the transforms are replaced by their closed forms: the
 * signed distance to an axis-aligned box is exact, and the distance to a box with other boxes
 * (and the Veil disc) cut out of it is the minimum of the individual signed distances.
 */
public final class Regions {
    private Regions() {
    }

    private static final List<Landmark> LMS = Spec.LANDMARKS;
    private static final int N = LMS.size();

    /** Taper width of the {@code windows()} blend (HANDOFF 5.3: {@code max(220, 12 * cell)}, cell = 4 -> 220). */
    public static final double WINDOW_FEATHER = 220.0;

    /**
     * Assignment order of the offline engine: large boxes first, small boxes overwrite, equal areas by
     * descending index so the lower index ends up on top.
     */
    private static final int[] ORDER = buildOrder();

    private static int[] buildOrder() {
        Integer[] idx = new Integer[N];
        for (int i = 0; i < N; i++) {
            idx[i] = i;
        }
        java.util.Arrays.sort(idx, (a, b) -> {
            int c = Double.compare(LMS.get(b).area(), LMS.get(a).area()); // area descending
            return c != 0 ? c : Integer.compare(b, a);                    // idx descending
        });
        int[] out = new int[N];
        for (int i = 0; i < N; i++) {
            out[i] = idx[i];
        }
        return out;
    }

    /** True when landmark {@code j} overrides landmark {@code i} wherever both boxes contain a point. */
    private static boolean overrides(int j, int i) {
        double aj = LMS.get(j).area();
        double ai = LMS.get(i).area();
        return aj < ai || (aj == ai && j < i);
    }

    /**
     * Landmark id of a point: the index into {@link Spec#LANDMARKS}, {@link Spec#VEIL_ID} beyond the
     * Veil radius, or {@code -1} for terrain governed only by the base noise. Where boxes overlap the
     * smaller box wins, so a compact landmark is never swallowed by a large neighbour.
     */
    public static int idAt(double x, double z) {
        int id = -1;
        for (int k = 0; k < N; k++) {
            int idx = ORDER[k];
            if (LMS.get(idx).boxContains(x, z)) {
                id = idx;
            }
        }
        if (Math.hypot(x, z) > Spec.VEIL_RADIUS) {
            id = Spec.VEIL_ID;
        }
        return id;
    }

    /** Signed distance to a landmark's bounding box: positive inside, negative outside (Euclidean). */
    public static double sdBox(Landmark lm, double x, double z) {
        double dx = Math.min(x - lm.x1(), lm.x2() - x);
        double dz = Math.min(z - lm.z1(), lm.z2() - z);
        if (dx >= 0.0 && dz >= 0.0) {
            return Math.min(dx, dz);
        }
        double ox = Math.max(Math.max(lm.x1() - x, x - lm.x2()), 0.0);
        double oz = Math.max(Math.max(lm.z1() - z, z - lm.z2()), 0.0);
        return -Math.hypot(ox, oz);
    }

    /**
     * Signed distance to the cells {@code ids == idx} - the landmark's own box minus every box that
     * overrides it and minus the Veil disc. Positive inside.
     */
    public static double sdOwned(int idx, double x, double z) {
        double sd = sdBox(LMS.get(idx), x, z);
        for (int j = 0; j < N; j++) {
            if (j != idx && overrides(j, idx)) {
                sd = Math.min(sd, -sdBox(LMS.get(j), x, z));
            }
        }
        return Math.min(sd, Spec.VEIL_RADIUS - Math.hypot(x, z));
    }

    /** Plain box feather used by {@code windows()}: {@code clip(d / width, 0, 1) ^ 0.8}, zero outside. */
    public static double boxFeather(int idx, double x, double z, double width) {
        double d = sdBox(LMS.get(idx), x, z);
        if (d <= 0.0) {
            return 0.0;
        }
        return Math.pow(Mathx.saturate(d / Math.max(width, 1e-6)), 0.8);
    }

    /**
     * Feather whose front is domain-warped by {@code edge} (a noise value in about {@code [-1, 1]}):
     * {@code clip((sd + edge * amplitude * width) / width, 0, 1) ^ 0.85}.
     */
    public static double noisyFeather(double sd, double edge, double width, double amplitude) {
        double signed = sd + edge * amplitude * width;
        return Math.pow(Mathx.saturate(signed / Math.max(width, 1e-6)), 0.85);
    }
}
