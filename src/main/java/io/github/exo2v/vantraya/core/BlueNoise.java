package io.github.exo2v.vantraya.core;

import java.util.SplittableRandom;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Tileable blue-noise threshold matrix (Ulichney's void-and-cluster), the specification's answer to
 * "turn a continuous probability into a yes/no per block without clumps" (population method A).
 *
 * <p>A probability {@code P} placed against the matrix as {@code P >= threshold} marks exactly a fraction
 * {@code P} of the blocks, spread so evenly that no thresholded subset clumps or leaves a void - which is
 * how the "40 %" and "5 %" density columns of the slope table become evenly scattered soil patches.
 * Each 64 x 64 tile is cyclically shifted by a hash of its tile index so the repetition never shows.
 */
public final class BlueNoise {
    public static final int SIZE = 64;

    private final float[] matrix; // rank / N in [0, 1), row-major
    private final long seed;

    private BlueNoise(long seed) {
        this.seed = seed;
        this.matrix = voidAndCluster(SIZE, seed);
    }

    private static final ConcurrentHashMap<Long, BlueNoise> CACHE = new ConcurrentHashMap<>();

    public static BlueNoise forSeed(long seed) {
        return CACHE.computeIfAbsent(seed, BlueNoise::new);
    }

    /** Threshold in {@code [0, 1)} for the block column {@code (x, z)}. */
    public double threshold(int x, int z) {
        int s = SIZE;
        int tx = Math.floorDiv(x, s);
        int ty = Math.floorDiv(z, s);
        long hsh = ((long) ty * 73856093L) ^ ((long) tx * 19349663L) ^ (seed * 83492791L);
        int ry = (int) Math.floorMod(hsh >> 8, (long) s);
        int rx = (int) Math.floorMod(hsh >> 16, (long) s);
        int lx = Math.floorMod(x, s);
        int lz = Math.floorMod(z, s);
        int my = Math.floorMod(lz - ry, s);
        int mx = Math.floorMod(lx - rx, s);
        return matrix[my * s + mx];
    }

    /** {@code P >= threshold}: true for exactly a fraction {@code p} of the blocks, evenly dispersed. */
    public boolean passes(double p, int x, int z) {
        return p >= threshold(x, z);
    }

    // ---------------------------------------------------------------------------------------

    private static float[] voidAndCluster(int size, long seed) {
        final int n = size * size;
        final double sigma = 1.5;
        final int radius = 6;
        final double[] kernel = new double[(2 * radius + 1) * (2 * radius + 1)];
        for (int dy = -radius; dy <= radius; dy++) {
            for (int dx = -radius; dx <= radius; dx++) {
                kernel[(dy + radius) * (2 * radius + 1) + (dx + radius)] =
                        Math.exp(-0.5 * (dx * dx + dy * dy) / (sigma * sigma));
            }
        }
        SplittableRandom rng = new SplittableRandom(seed & 0xFFFFFFFFL);
        boolean[] pattern = new boolean[n];
        int ones = Math.max(1, (int) Math.round(n * 0.1));
        int placed = 0;
        while (placed < ones) {
            int i = rng.nextInt(n);
            if (!pattern[i]) {
                pattern[i] = true;
                placed++;
            }
        }
        double[] energy = new double[n];
        for (int i = 0; i < n; i++) {
            if (pattern[i]) {
                splat(energy, i, size, radius, kernel, 1.0);
            }
        }
        // relax: move the tightest cluster into the largest void until it is stable
        for (int iter = 0; iter < 64; iter++) {
            int tight = argExtreme(energy, pattern, true, true);
            int voidIdx = argExtreme(energy, pattern, false, false);
            if (tight < 0 || voidIdx < 0 || energy[tight] <= energy[voidIdx]) {
                break;
            }
            pattern[tight] = false;
            splat(energy, tight, size, radius, kernel, -1.0);
            pattern[voidIdx] = true;
            splat(energy, voidIdx, size, radius, kernel, 1.0);
        }
        double[] rank = new double[n];
        // phase 1: remove clusters, ranking them from the top down
        boolean[] work = pattern.clone();
        double[] e1 = energy.clone();
        int count = 0;
        for (boolean b : pattern) {
            if (b) {
                count++;
            }
        }
        for (int r = count - 1; r >= 0; r--) {
            int tight = argExtreme(e1, work, true, true);
            if (tight < 0) {
                break;
            }
            rank[tight] = r;
            work[tight] = false;
            splat(e1, tight, size, radius, kernel, -1.0);
        }
        // phase 2: fill voids, ranking them upward
        work = pattern.clone();
        double[] e2 = energy.clone();
        for (int r = count; r < n; r++) {
            int voidIdx = argExtreme(e2, work, false, false);
            if (voidIdx < 0) {
                break;
            }
            rank[voidIdx] = r;
            work[voidIdx] = true;
            splat(e2, voidIdx, size, radius, kernel, 1.0);
        }
        float[] out = new float[n];
        for (int i = 0; i < n; i++) {
            out[i] = (float) (rank[i] / n); // [0, 1): rank n-1 maps to just below one
        }
        return out;
    }

    /** Index of the largest energy among set cells ({@code wantSet}) or the smallest among clear cells. */
    private static int argExtreme(double[] energy, boolean[] pattern, boolean wantSet, boolean max) {
        int best = -1;
        double bestV = max ? Double.NEGATIVE_INFINITY : Double.POSITIVE_INFINITY;
        for (int i = 0; i < energy.length; i++) {
            if (pattern[i] != wantSet) {
                continue;
            }
            double v = energy[i];
            if (max ? v > bestV : v < bestV) {
                bestV = v;
                best = i;
            }
        }
        return best;
    }

    private static void splat(double[] energy, int idx, int size, int radius, double[] kernel, double sign) {
        int cy = idx / size;
        int cx = idx % size;
        int w = 2 * radius + 1;
        for (int dy = -radius; dy <= radius; dy++) {
            int y = Math.floorMod(cy + dy, size);
            for (int dx = -radius; dx <= radius; dx++) {
                int x = Math.floorMod(cx + dx, size);
                energy[y * size + x] += sign * kernel[(dy + radius) * w + (dx + radius)];
            }
        }
    }
}
