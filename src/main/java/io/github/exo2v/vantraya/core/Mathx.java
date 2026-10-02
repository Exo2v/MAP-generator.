package io.github.exo2v.vantraya.core;

/**
 * Small scalar helpers shared by the specification model.
 *
 * <p>Everything in {@code io.github.exo2v.vantraya.core} is plain Java with no Minecraft
 * dependency, so the whole specification can be unit-tested (and compared against the
 * offline Python engine) without launching the game.
 */
public final class Mathx {
    private Mathx() {
    }

    public static double clamp(double v, double lo, double hi) {
        return v < lo ? lo : (v > hi ? hi : v);
    }

    public static int clamp(int v, int lo, int hi) {
        return v < lo ? lo : (v > hi ? hi : v);
    }

    public static double saturate(double v) {
        return v < 0.0 ? 0.0 : (v > 1.0 ? 1.0 : v);
    }

    public static double lerp(double a, double b, double t) {
        return a + (b - a) * t;
    }

    /**
     * {@code smoothstep(a, b, t) = u^2 (3 - 2u)}, {@code u = clip((t - a) / (b - a), 0, 1)} - the
     * primitive used throughout the specification (HANDOFF section 5.6). The tiny epsilon in the
     * denominator mirrors the offline engine.
     */
    public static double smoothstep(double a, double b, double t) {
        double u = saturate((t - a) / (b - a + 1e-12));
        return u * u * (3.0 - 2.0 * u);
    }

    /** {@code (t - lo) / (hi - lo)} clipped to {@code [0, 1]} (a percentile stretch with fixed bounds). */
    public static double stretch01(double v, double lo, double hi) {
        return saturate((v - lo) / Math.max(hi - lo, 1e-9));
    }

    /** Hermite S-curve {@code 3t^2 - 2t^3}. */
    public static double hermite(double t) {
        return 3.0 * t * t - 2.0 * t * t * t;
    }

    /** Polynomial smooth maximum {@code (a + b + sqrt((a-b)^2 + k)) / 2} (HANDOFF failure mode 7). */
    public static double smoothMax(double a, double b, double k) {
        return 0.5 * (a + b + Math.sqrt((a - b) * (a - b) + Math.max(0.0, k)));
    }

    public static double degrees(double radians) {
        return Math.toDegrees(radians);
    }
}
