package io.github.exo2v.vantraya.mc;

import java.util.OptionalLong;

import io.github.exo2v.vantraya.core.Spec;
import io.github.exo2v.vantraya.core.VantrayaModel;

/**
 * Finds the specification model that belongs to a world.
 *
 * <p>A density function never learns the world seed - Minecraft only hands it noise instances that were
 * seeded from it. So the world is identified by <em>fingerprinting</em> one such noise
 * ({@code vantraya_builder:seed_probe}): its values at eight fixed points are hashed to 64 bits. The
 * density functions (through their seeded {@code NoiseHolder}) and the chunk generator (through
 * {@code RandomState.getOrCreateNoise}) read the very same noise instance, so both arrive at the same
 * fingerprint, hence the same model, without any global state - several worlds can coexist in one JVM.
 */
public final class WorldSeeds {
    private WorldSeeds() {
    }

    /** Anything that can be sampled in 3D: a {@code NormalNoise}, a seeded {@code NoiseHolder}. */
    @FunctionalInterface
    public interface NoiseSource {
        double value(double x, double y, double z);
    }

    private static final double[][] PROBE = {
            {0.37, 0.11, 0.73}, {13.1, -5.7, 2.2}, {-71.3, 40.9, -9.9}, {255.5, -190.25, 31.0},
            {1024.25, 333.75, -512.5}, {-3333.3, 2222.2, 1111.1}, {87.0, -1.5, -87.0}, {0.013, 0.027, 0.041}};

    private static long mix(long z) {
        z = (z ^ (z >>> 30)) * 0xBF58476D1CE4E5B9L;
        z = (z ^ (z >>> 27)) * 0x94D049BB133111EBL;
        return z ^ (z >>> 31);
    }

    /**
     * A 64-bit fingerprint of a seeded noise, or empty when the noise is not seeded (an unseeded
     * {@code NoiseHolder} returns 0 everywhere, which a real {@code NormalNoise} never does).
     */
    public static OptionalLong fingerprint(NoiseSource noise) {
        long h = 0x9E3779B97F4A7C15L;
        boolean seeded = false;
        for (double[] p : PROBE) {
            double v = noise.value(p[0], p[1], p[2]);
            if (v != 0.0) {
                seeded = true;
            }
            h = mix(h ^ Double.doubleToLongBits(v));
        }
        return seeded ? OptionalLong.of(h) : OptionalLong.empty();
    }

    /**
     * The model for the world the probe noise belongs to. An unseeded probe (a density function that was never
     * wired into a {@code RandomState}) falls back to the canonical seed, as does the {@code canonicalWorld}
     * option.
     */
    public static VantrayaModel modelFor(NoiseSource probe) {
        if (VantrayaConfig.canonicalWorld()) {
            return VantrayaModel.forSeed(Spec.SPEC_SEED);
        }
        OptionalLong fp = fingerprint(probe);
        return VantrayaModel.forSeed(fp.isPresent() ? fp.getAsLong() : Spec.SPEC_SEED);
    }
}
