package io.github.exo2v.vantraya.core;

/**
 * Coherent noise used by every landform. This is a line-for-line port of the offline engine's
 * {@code mg/core/noise.py}: the same integer hash, the same eight gradients, the same quintic
 * fade, the same octave seeding ({@code seed + octave * 1013}). Keeping the arithmetic identical
 * is what lets the in-game generator be cross-checked against the exported reference world.
 *
 * <p>The functions are stateless and allocation-free, so they are safe to call from the many
 * worker threads that generate chunks concurrently.
 */
public final class Noise {
    private Noise() {
    }

    private static final double R = 0.70710678;
    private static final double[] GX = {1.0, -1.0, 0.0, 0.0, R, -R, R, -R};
    private static final double[] GZ = {0.0, 0.0, 1.0, -1.0, R, R, -R, -R};

    private static final double INV_2_32 = 1.0 / 4294967296.0;

    /** Integer hash of a 2D lattice point to {@code [0, 1)} (unsigned 32-bit wrap-around arithmetic). */
    public static double hash2(long ix, long iy, int seed) {
        int x = (int) ix;
        int y = (int) iy;
        int h = x * 0x27D4EB2D;
        h ^= y * 0x165667B1;
        h ^= h >>> 15;
        h *= 0x2545F491;
        h ^= h >>> 13;
        h ^= seed;
        h *= 0x9E3779B1;
        h ^= h >>> 16;
        return (h & 0xFFFFFFFFL) * INV_2_32;
    }

    private static double quintic(double t) {
        return t * t * t * (t * (t * 6.0 - 15.0) + 10.0);
    }

    private static double grad(long ix, long iy, int seed, double ux, double uy) {
        double g = hash2(ix, iy, seed);
        int idx = Math.min((int) (g * 8.0), 7);
        return GX[idx] * ux + GZ[idx] * uy;
    }

    /** Perlin-style gradient noise in roughly {@code [-1, 1]}. */
    public static double gradient(double x, double y, int seed) {
        double x0 = Math.floor(x);
        double y0 = Math.floor(y);
        double dx = x - x0;
        double dy = y - y0;
        long ix = (long) x0;
        long iy = (long) y0;
        double n00 = grad(ix, iy, seed, dx, dy);
        double n10 = grad(ix + 1, iy, seed, dx - 1.0, dy);
        double n01 = grad(ix, iy + 1, seed, dx, dy - 1.0);
        double n11 = grad(ix + 1, iy + 1, seed, dx - 1.0, dy - 1.0);
        double ux = quintic(dx);
        double uy = quintic(dy);
        double nx0 = n00 + (n10 - n00) * ux;
        double nx1 = n01 + (n11 - n01) * ux;
        return (nx0 + (nx1 - nx0) * uy) * 1.4;
    }

    /** Smooth value noise in {@code [-1, 1]} (used for the sub-cell block detail). */
    public static double value(double x, double y, int seed) {
        double x0 = Math.floor(x);
        double y0 = Math.floor(y);
        double fx = quintic(x - x0);
        double fy = quintic(y - y0);
        long ix = (long) x0;
        long iy = (long) y0;
        double n00 = hash2(ix, iy, seed);
        double n10 = hash2(ix + 1, iy, seed);
        double n01 = hash2(ix, iy + 1, seed);
        double n11 = hash2(ix + 1, iy + 1, seed);
        double nx0 = n00 + (n10 - n00) * fx;
        double nx1 = n01 + (n11 - n01) * fx;
        return (nx0 + (nx1 - nx0) * fy) * 2.0 - 1.0;
    }

    /**
     * Fractal Brownian motion: gain 0.5, lacunarity 2, normalised by the sum of amplitudes so the
     * result stays in roughly {@code [-1, 1]}. {@code scale} is the wavelength of the first octave
     * in blocks.
     */
    public static double fbm(double x, double z, int octaves, double scale, long seed) {
        double xs = x / scale;
        double zs = z / scale;
        double amp = 1.0;
        double freq = 1.0;
        double total = 0.0;
        double norm = 0.0;
        for (int o = 0; o < octaves; o++) {
            double n = gradient(xs * freq, zs * freq, (int) (seed + o * 1013L));
            total += n * amp;
            norm += amp;
            amp *= 0.5;
            freq *= 2.0;
        }
        return total / norm;
    }

    /** Ridged fBm: each octave is {@code (1 - |n|)^2}, so the result lies in {@code [0, ~1]}. */
    public static double ridgedFbm(double x, double z, int octaves, double scale, long seed) {
        double xs = x / scale;
        double zs = z / scale;
        double amp = 1.0;
        double freq = 1.0;
        double total = 0.0;
        double norm = 0.0;
        for (int o = 0; o < octaves; o++) {
            double n = gradient(xs * freq, zs * freq, (int) (seed + o * 1013L));
            n = 1.0 - Math.abs(n);
            n = n * n;
            total += n * amp;
            norm += amp;
            amp *= 0.5;
            freq *= 2.0;
        }
        return total / norm;
    }

    /**
     * Quilez-style domain warp: returns {@code (x + wx * strength, z + wz * strength)} in
     * {@code out[0..1]}. The two warp fields use seeds {@code seed + 51} and {@code seed + 923}.
     */
    public static void domainWarp(double x, double z, double scale, double strength, long seed,
                                  int octaves, double[] out) {
        double wx = fbm(x, z, octaves, scale, seed + 51);
        double wz = fbm(x, z, octaves, scale, seed + 923);
        out[0] = x + wx * strength;
        out[1] = z + wz * strength;
    }
}
