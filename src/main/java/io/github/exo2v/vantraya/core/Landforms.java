package io.github.exo2v.vantraya.core;

import java.util.List;

import io.github.exo2v.vantraya.core.Instances.Instance;
import io.github.exo2v.vantraya.core.Spec.Landmark;

/**
 * The nine landmark shapers (HANDOFF section 5.7): each turns the specification's row for a landmark
 * into a <em>target elevation field</em> that the model blends into the continent with a feathered
 * edge. Ported from the offline engine's {@code mg/generation/landform.py}; every function is local
 * (a pure function of the block coordinate, the world seed and the instances built from it).
 */
public final class Landforms {
    private Landforms() {
    }

    private static double sq(double v) {
        return v * v;
    }

    /** Result of the caldera shaper: the target height plus the two "keep it dry" flags. */
    public record Caldera(double target, boolean lava, boolean basin, double spire) {
    }

    /**
     * Spec landmark 3: volcanic ring wall at Y = 146, a sunken crater basin at Y = 40 and the Obsidian
     * Throne spire at Y = 92. The crater is a <em>lava</em> basin - nothing inside the ring may be
     * flooded, or the sea would fill the bowl to the rim and turn the caldera into a crater lake.
     */
    public static Caldera caldera(double x, double z, double base, long seed) {
        double r0 = Math.hypot(x, z);
        double r = Math.max(r0 + Noise.fbm(x, z, 3, 380.0, seed + 397) * 95.0, 0.0);
        double ringR = 470.0;
        double ringW = 150.0;
        double wobble = 1.0 + 0.20 * Noise.fbm(x, z, 3, 300.0, seed + 401);
        double wall = Spec.CALDERA_RIM * Math.exp(-sq((r - ringR) / ringW)) * wobble;

        double basinR = 300.0;
        double basinFloor = Spec.CALDERA_FLOOR;
        double basin = basinFloor + 2.5 * Mathx.saturate(r / basinR);
        double spire = (Spec.CALDERA_THRONE - basinFloor) * Math.exp(-sq(r / 60.0));

        double caldera = Math.max(basin + spire, wall);
        double inner = Mathx.smoothstep(basinR, basinR * 1.30, r);
        caldera = caldera * (1.0 - inner) + Math.max(basin + spire, base * 0.35) * inner;
        caldera = Math.max(caldera, wall);

        double outer = Mathx.smoothstep(ringR + ringW * 0.8, ringR + ringW * 3.2, r);
        double target = caldera * (1.0 - outer) + base * outer;
        boolean lava = (r < basinR * 0.88) && (target < basinFloor + 7.0) && (spire < 8.0);
        boolean basinFlag = r < ringR * 1.18;
        return new Caldera(target, lava, basinFlag, spire);
    }

    /** Spec landmark 2: concentric 9 m quarry benches, brass river chasms. */
    public static double quarry(double x, double z, Landmark lm, long seed) {
        double r = Math.hypot(x - lm.x(), z - lm.z());
        double step = 9.0;
        double cone = Mathx.saturate(1.0 - r / 950.0);
        double benches = Math.floor((lm.y() + 34.0 * (1.0 - cone)) / step) * step;
        double grit = Noise.fbm(x, z, 3, 110.0, seed + 511) * step * 0.4;
        return Math.max(benches + grit, Spec.QUARRY_LOW - 22.0);
    }

    /** Spec landmark 4: alpine cordillera - crests, needle ridges and Matterhorn peaks. */
    public static double cordillera(double x, double z, double base, List<Instance> instances,
                                    double noiseOffset) {
        return Math.max(base, Instances.relief(instances, x, z, noiseOffset));
    }

    /** Spec landmark 5: barchan dunes with vitrified crests and a few terracotta mesas. */
    public static double dunes(double x, double z, long seed, Calibration cal) {
        double warp = Noise.fbm(x, z, 3, 900.0, seed + 601);
        double phase = (x + z) / 230.0 * 2.0 * Math.PI + warp * 9.0;
        double cross = (x - z * 0.9) / 1900.0 * 2.0 * Math.PI;
        double s = Math.sin(phase) * (0.75 + 0.25 * Math.sin(cross));
        double asym = s >= 0.0 ? Math.pow(Math.abs(s), 0.65) : -Math.pow(Math.abs(s), 1.7);
        double envelope = Mathx.stretch01(Noise.fbm(x, z, 3, 1500.0, seed + 605), cal.dunesEnvLo(), cal.dunesEnvHi());
        double crest = asym * (0.35 + 0.65 * envelope);
        double dunes = 76.0 + 16.0 * (crest * 0.5 + 0.5);
        double grit = Noise.fbm(x, z, 4, 110.0, seed + 607) * 2.0;
        double plateau = Mathx.stretch01(Noise.fbm(x, z, 3, 760.0, seed + 611), cal.dunesMesaLo(), cal.dunesMesaHi());
        double mesa = Mathx.smoothstep(0.62, 0.82, plateau) * 24.0;
        return Math.max(dunes + grit + mesa, Spec.SEA_LEVEL + 4.0);
    }

    /** Spec landmark 6: a flat sunken bayou at Y = 62-66 cut by braided delta channels. */
    public static double fen(double x, double z, Landmark lm, long seed) {
        double r = Math.hypot(x - lm.x(), z - lm.z());
        double target = 63.5 + 26.0 * Math.pow(Mathx.saturate((r - 420.0) / 620.0), 1.6);
        target += Noise.fbm(x, z, 3, 440.0, seed + 701) * 1.8;
        double braid = 1.0;
        for (int k = 0; k < 5; k++) {
            double off = (k - 2) * 150.0;
            double wob = Noise.fbm(x, z, 2, 380.0, seed + 710 + k) * 200.0;
            double d = Math.abs((x + z) * 0.5 - off + wob);
            braid = Math.min(braid, Mathx.saturate(d / 30.0));
        }
        return target - (1.0 - braid) * 3.4;
    }

    /** Spec landmark 7: a drowned caldera shelf with barrier sandbars and coral atolls. */
    public static double drownedShelf(double x, double z, Landmark lm, long seed, Calibration cal) {
        double shelf = 53.0 + Noise.fbm(x, z, 4, 560.0, seed + 801) * 3.5;
        double bars = Mathx.stretch01(Noise.fbm(x, z, 2, 320.0, seed + 807), cal.reachBarLo(), cal.reachBarHi());
        shelf += Mathx.smoothstep(0.70, 0.86, bars) * 8.0;
        double atoll = Mathx.stretch01(Noise.fbm(x, z, 3, 280.0, seed + 811), cal.reachAtollLo(), cal.reachAtollHi());
        shelf += Mathx.smoothstep(0.80, 0.94, atoll) * 12.0;
        // The centre of the reach is open water. The biome pipeline recovers the surface height from the
        // router's quantised depth (±0.05 ≈ ±6 blocks), so ground anywhere near the waterline flips between
        // "ocean" and "shore"; the old exact pin sat at 54 and hid that. The centre is therefore held well
        // under the waterline and the bars and atolls rise around it, not under it.
        double r = Math.hypot(x - lm.x(), z - lm.z());
        double ceil = Spec.SEA_LEVEL + 1.5 - (Spec.SEA_LEVEL + 1.5 - 55.0) * (1.0 - Mathx.smoothstep(120.0, 260.0, r));
        return Mathx.clamp(shelf, lm.yLo() - 3.0, ceil);
    }

    /** Spec landmark 8: solitary granite needles on a bench at the landmark's elevation. */
    public static double spires(double x, double z, double base, Landmark lm, List<Instance> needles,
                                double noiseOffset) {
        double r = Math.hypot(x - lm.x(), z - lm.z());
        double bench = lm.y() * Mathx.saturate(1.0 - sq(r / 620.0));
        return Math.max(base, bench + Instances.relief(needles, x, z, noiseOffset));
    }

    /** Spec landmark 9: stepped highland terraces (6 m treads), the rim being the high ground. */
    public static double terraces(double x, double z, Landmark lm, long seed) {
        double r = Math.hypot(x - lm.x(), z - lm.z());
        double climb = Mathx.saturate(1.0 - r / 660.0);
        double target = lm.yHi() - climb * (lm.yHi() - lm.yLo());
        double step = 6.0;
        target = Math.floor(target / step) * step;
        return target + Noise.fbm(x, z, 3, 320.0, seed + 1001) * step * 0.7;
    }

    /** Spec landmark 1: cold pebble bluffs and rolling wildflower bluffs, clamped into the 68-74 band. */
    public static double coast(double x, double z, double base, long seed) {
        double rolling = Noise.fbm(x, z, 4, 360.0, seed + 1101) * 3.0;
        double bluffs = Noise.fbm(x, z, 3, 95.0, seed + 1107) * 1.4;
        double inland = Mathx.clamp(base, Spec.COAST_LOW - 10.0, Spec.COAST_HIGH + 1.0);
        return Math.min(inland + rolling + bluffs, Spec.COAST_HIGH + 4.0);
    }
}
