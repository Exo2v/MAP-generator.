package io.github.exo2v.vantraya.core;

import java.util.List;
import java.util.function.Function;

import io.github.exo2v.vantraya.core.Spec.Kind;
import io.github.exo2v.vantraya.core.Spec.Landmark;
import io.github.exo2v.vantraya.core.Spec.Window;

/**
 * The continent of Vantyra as six noise maps - the specification's "noise maps that give these specific
 * land features" - evaluated per column.
 *
 * <p>0.3.0 reworked the whole generator onto vanilla's Perlin-noise spline pipeline (the play-test
 * document "Rivers aren't spawning only these puddles", 3 October 2026). The model no longer computes a
 * height field at all: it produces the same six <em>parameter maps</em> vanilla's own overworld runs on -
 * {@code continents}, {@code erosion}, {@code ridges}, {@code temperature}, {@code humidity} - and the
 * vanilla offset / factor / jaggedness / depth / sloped-cheese splines (kept as data under
 * {@code worldgen/density_function/}) turn those maps into terrain. Rivers, valleys, peaks and plains are
 * therefore <em>level sets of the same fields that made the ground</em>, exactly as in vanilla and in the
 * studied mods (Tectonic's lesson): no post-pass can desync from the terrain.
 *
 * <p>How each map is built:
 * <ul>
 *   <li><b>continents</b> - the island: a warped radial coast (specification shape), a shelf spline, an
 *       inland bump, and the landmark {@code cont} windows. In vanilla's bands the shelf is ocean, the
 *       shore band is coast, and the interior runs up the near/mid/far-inland bands; the Sunken Reach's
 *       window drops to the mushroom-fields band where the spec wants its reef.</li>
 *   <li><b>erosion</b> - regional relief class (low = dramatic, high = flat), windowed per landmark so
 *       the Spine is a mountain range and the Fen a plain.</li>
 *   <li><b>ridges</b> - the peaks-and-valleys map. Its <em>zero contour is the river network</em>:
 *       vanilla's offset spline dips where {@code ridges ~ 0} ("valleys" band) and the biome builder
 *       calls that band {@code river}. Zero contours of smooth noise meander, join and fork like real
 *       drainage and always run from the interior toward the falling continentalness, i.e. downhill to
 *       the sea; aquifers fill the carved valleys, at sea level near the coast and <em>at altitude in the
 *       uplands</em> - the upland waterways the spec asked for.</li>
 *   <li><b>temperature / humidity</b> - latitude + regional noise + moisture advection, windowed per
 *       landmark (the Glacial Spine frozen, the Dunes hot and dry, the Fen lush).</li>
 * </ul>
 *
 * <p>Biomes are selected by vanilla's multi-noise biome builder from these same maps
 * ({@code multi_noise} + {@code preset: minecraft:overworld}), so biome borders are vanilla's nearest-
 * parameter curves and TerraBlender-based biome mods work in a Vantraya world.
 *
 * <p>Instances are immutable and thread-safe; sampling reuses a small per-thread cache so that the
 * several density-function channels evaluated at one column cost one evaluation.
 */
public final class VantrayaModel {

    /** Tunables that change how faithfully (or how practically) the specification's climate is mirrored. */
    public record Config(Calibration calibration, double instanceNoiseOffset, boolean seamlessCoast,
                         boolean borealBuffer) {

        /** What the Minecraft world type uses for an arbitrary world seed. */
        public static Config production() {
            return new Config(Calibration.DEFAULT, 3998.0, true, true);
        }

        /** The canonical build (seed 20250929): the reference build's own percentile bounds. */
        public static Config canonical() {
            return new Config(Calibration.SPEC_SEED_REFERENCE, 3998.0, true, true);
        }

        /**
         * Legacy shape kept for the rendering tools: fixed calibration, no boreal buffer, the table's
         * west-axis seam kept.
         */
        public static Config parity(int cellSize, Calibration calibration) {
            return new Config(calibration, 4000.0 - 0.5 * cellSize, false, false);
        }
    }

    /**
     * The parameter maps at one column. {@code cont}, {@code erosion} and {@code ridges} are vanilla
     * continentalness / erosion / weirdness units (roughly -1..1); {@code temperature} and
     * {@code humidity} are vanilla climate units (roughly -1..1); {@code protect} is 1 within
     * {@link Spec#PROTECT_RADIUS} of a landmark centre.
     */
    public record Fields(
            double x, double z,
            double cont, double erosion, double ridges,
            double temperature, double humidity,
            int landmark,
            double protect) {

        /** {@code ridges ~ 0} is the valley band: the river network. */
        public boolean valley() {
            return Math.abs(ridges) < VALLEY_BAND;
        }

        public int tier() {
            return Spec.climateTier(temperature);
        }
    }

    /** Vanilla's "valleys" weirdness slice is [-0.05, 0.05]; the river band is a hair wider here. */
    public static final double VALLEY_BAND = 0.06;

    // ---------------------------------------------------------------------------------------

    private final long seed;
    private final Config cfg;
    private final Calibration cal;
    private final int calderaIdx;

    public VantrayaModel(long seed, Config cfg) {
        this.seed = seed;
        this.cfg = cfg;
        this.cal = cfg.calibration();
        int ci = -1;
        for (int i = 0; i < Spec.LANDMARKS.size(); i++) {
            if (Spec.LANDMARKS.get(i).kind() == Kind.CALDERA) {
                ci = i;
                break;
            }
        }
        this.calderaIdx = ci;
    }

    /** The production model for one world seed. */
    public static VantrayaModel forSeed(long seed) {
        return new VantrayaModel(seed, Config.production());
    }

    // ---------------------------------------------------------------------------------------
    // sampling cache
    // ---------------------------------------------------------------------------------------

    private static final int CACHE_SIZE = 1 << 8;

    private static final class SampleCache {
        final long[] kx = new long[CACHE_SIZE];
        final long[] kz = new long[CACHE_SIZE];
        final Fields[] val = new Fields[CACHE_SIZE];
    }

    private static final ThreadLocal<SampleCache> local = ThreadLocal.withInitial(SampleCache::new);

    public Fields sample(double x, double z) {
        long bx = Double.doubleToLongBits(x);
        long bz = Double.doubleToLongBits(z);
        long h = bx * 0x9E3779B97F4A7C15L ^ (bz + 0x632BE59BD9B4E019L) * 0xC2B2AE3D27D4EB4FL;
        int slot = (int) (h ^ (h >>> 29) ^ (h >>> 47)) & (CACHE_SIZE - 1);
        SampleCache c = local.get();
        Fields f = c.val[slot];
        if (f != null && c.kx[slot] == bx && c.kz[slot] == bz) {
            return f;
        }
        f = compute(x, z);
        c.kx[slot] = bx;
        c.kz[slot] = bz;
        c.val[slot] = f;
        return f;
    }

    // ---------------------------------------------------------------------------------------
    // 1. continentalness
    // ---------------------------------------------------------------------------------------

    /** Continentalness before the landmark windows: warped radial coast + spline + inland bonus + detail. */
    public double rawContinentalness(double x, double z) {
        double[] w = new double[2];
        Noise.domainWarp(x, z, 2100.0, 900.0, seed + 17, 4, w);
        return rawContinentalness(x, z, w[0], w[1]);
    }

    private double rawContinentalness(double x, double z, double wx, double wz) {
        double rc = Tables.coastRadius(wx, wz, cfg.seamlessCoast())
                + Noise.fbm(x, z, 3, 2600.0, seed + 19) * 620.0
                + Noise.fbm(x, z, 2, 820.0, seed + 29) * 190.0;
        double delta = Math.hypot(wx, wz) - rc;
        double cont = Tables.COAST_SPLINE.eval(delta);
        double sx = x / 2700.0;
        double sz = (z + 1500.0) / 2300.0;
        cont += 0.12 * Math.exp(-(sx * sx + sz * sz));
        return Mathx.clamp(cont + Noise.fbm(x, z, 3, 900.0, seed + 23) * 0.05, -1.20, 1.20);
    }

    /**
     * {@code windows()}: remap {@code field} into each landmark's window. The field keeps its internal
     * structure; only its amplitude is confined to the range the specification allows for the region.
     */
    private double windows(double field, Function<Landmark, Window> window,
                           double weight, double fieldLo, double fieldHi, double x, double z) {
        double unit = Mathx.saturate((field - fieldLo) / Math.max(fieldHi - fieldLo, 1e-9));
        double out = field;
        List<Landmark> lms = Spec.LANDMARKS;
        for (int i = 0; i < lms.size(); i++) {
            double taper = Regions.boxFeather(i, x, z, Regions.WINDOW_FEATHER);
            if (taper <= 0.0) {
                continue;
            }
            Landmark lm = lms.get(i);
            Window win = window.apply(lm);
            double target = win.lo() + (win.hi() - win.lo()) * unit;
            double k = Mathx.saturate(taper * weight);
            out = out * (1.0 - k) + target * k;
        }
        return out;
    }

    // ---------------------------------------------------------------------------------------
    // 2. generic climate (HANDOFF 5.1) - closed-form stand-ins for the raster statistics
    // ---------------------------------------------------------------------------------------

    private static final double MOIST_STEPS_DECAY = Math.pow(0.94, 26);

    /** Generic temperature in 0..1: latitude band, regional noise, no lapse yet. */
    private double genericTempUnit(double x, double z) {
        double bandWarp = Noise.fbm(x, z, 3, 3200.0 * 1.6, seed + 11) * 0.4 * 0.45;
        double lat = Mathx.clamp(z / 8000.0 + bandWarp, -0.30, 1.30);
        double polar = Math.pow(Mathx.saturate(lat), 2.0);
        double core = 1.0 - polar;
        double regional = Noise.fbm(x, z, 4, 3200.0, seed + 101);
        double t = core * 0.70 + Mathx.stretch01(regional, cal.regionalTempLo(), cal.regionalTempHi()) * 0.30;
        return Mathx.saturate(t);
    }

    /** Continentality (0 coast .. 1 deep interior): the min-max normalised low-frequency noise. */
    private double continentality(double x, double z) {
        double n = Noise.fbm(x, z, 3, 3200.0 * 0.6, seed + 303);
        return Mathx.saturate((n - cal.continentalityLo()) / Math.max(cal.continentalityHi() - cal.continentalityLo(), 1e-9));
    }

    private double humidityPatch(double x, double z) {
        double n = Noise.fbm(x, z, 3, 3200.0 * 0.5, seed + 505);
        return Mathx.saturate((n - cal.humPatchLo()) / Math.max(cal.humPatchHi() - cal.humPatchLo(), 1e-9));
    }

    /**
     * The moisture budget after 26 advection steps, in closed form. Every step is
     * {@code m <- 0.94 m + 0.015 (1 - c)}, started from {@code max(0.35, 0.9 (1 - c))}.
     */
    private static double moisture(double c) {
        double shore = Mathx.saturate(1.0 - c);
        double m0 = Math.max(0.35, shore * 0.9);
        double evap = 0.015 * shore;
        return MOIST_STEPS_DECAY * m0 + evap * (1.0 - MOIST_STEPS_DECAY) / 0.06;
    }

    private static final double MOIST_MIN = moisture(1.0);
    private static final double MOIST_MAX = moisture(0.0);

    /** Generic humidity in 0..1 (the offline {@code ClimateModel.compute} output). */
    private double genericHumUnit(double x, double z, double c) {
        double norm = (moisture(c) - MOIST_MIN) / Math.max(MOIST_MAX - MOIST_MIN, 1e-9);
        double h = norm * 0.85 + humidityPatch(x, z) * 0.15;
        return Mathx.saturate(h - c * 0.12);
    }

    // ---------------------------------------------------------------------------------------
    // compute
    // ---------------------------------------------------------------------------------------

    /** The six parameter maps at one column. */
    private Fields compute(double x, double z) {
        // ---- 1. continentalness -------------------------------------------------------------
        double[] warp = new double[2];
        Noise.domainWarp(x, z, 2100.0, 900.0, seed + 17, 4, warp);
        double contRaw = rawContinentalness(x, z, warp[0], warp[1]);
        double cont = windows(contRaw, Landmark::cont, 1.0, cal.contLo(), cal.contHi(), x, z);

        // ---- 2. erosion / ridges ------------------------------------------------------------
        double eroUnit = Mathx.stretch01(Noise.fbm(x, z, 4, 1500.0, seed + 31), cal.eroLo(), cal.eroHi());
        double ero = windows(eroUnit * 2.0 - 1.0, Landmark::erosion, 1.0, -1.0, 1.0, x, z);

        // The peaks-and-valleys map. The zero contour of this smooth field is the river network the
        // offset spline carves and the biome builder calls "river"; warp it first so the network meanders.
        double[] rwp = new double[2];
        Noise.domainWarp(x, z, 900.0, 340.0, seed + 137, 3, rwp);
        double ridUnit = Mathx.stretch01(Noise.fbm(rwp[0], rwp[1], 5, 1250.0, seed + 41), cal.ridLo(), cal.ridHi());
        double rid = windows(ridUnit * 2.0 - 1.0, Landmark::ridges, 1.0, -1.0, 1.0, x, z);
        rid = calderaRing(x, z, rid);

        // ---- 3. climate in spec units (the Ashenfall engine's _climate) ---------------------
        double genT = genericTempUnit(x, z);
        double c = continentality(x, z);
        double genH = genericHumUnit(x, z, c);
        double t = Mathx.saturate(genT) * 2.0 - 1.0;
        double h = Mathx.saturate(genH) * 2.0 - 1.0;
        double reg = Noise.fbm(x, z, 3, 2400.0, seed + 91);
        t = Mathx.clamp(t * 0.45 + reg * 0.85 + 0.10, -1.15, 1.15);
        h = Mathx.clamp(h * 0.55 + Noise.fbm(x, z, 3, 2000.0, seed + 97) * 0.7, -1.15, 1.15);
        double unitT = Mathx.saturate((t + 1.0) * 0.5);
        double unitH = Mathx.saturate((h + 1.0) * 0.5);
        if (cfg.borealBuffer()) {
            // eased first, so every landmark's own temperature window still takes precedence over it
            t = borealBuffer(t, x, z);
        }
        List<Landmark> lms = Spec.LANDMARKS;
        for (int i = 0; i < lms.size(); i++) {
            double taper = Regions.boxFeather(i, x, z, Regions.WINDOW_FEATHER);
            if (taper <= 0.0) {
                continue;
            }
            Landmark lm = lms.get(i);
            double k = Mathx.saturate(taper * 0.85);
            double tTarget = lm.temp().lo() + (lm.temp().hi() - lm.temp().lo()) * unitT;
            double hTarget = lm.humidity().lo() + (lm.humidity().hi() - lm.humidity().lo()) * unitH;
            t = t * (1.0 - k) + tTarget * k;
            h = h * (1.0 - k) + hTarget * k;
        }
        double r = Math.hypot(x, z);
        if (r > Spec.VEIL_RADIUS) {
            t = Math.min(t, -0.55);
            h = Mathx.clamp(h, -0.4, 0.6);
        }
        t = Mathx.clamp(t, -1.2, 1.2);
        h = Mathx.clamp(h, -1.2, 1.2);

        // ---- 4. protection and landmark ownership ------------------------------------------
        double protect = Spec.protection(x, z);
        int owner = Regions.idAt(x, z);

        return new Fields(x, z, cont, ero, rid, t, h, owner, protect);
    }

    // ---------------------------------------------------------------------------------------
    // the Ashen Caldera: a ring of peaks around a sunken floor, as an anomaly of the ridges map
    // ---------------------------------------------------------------------------------------

    /**
     * The caldera is the one landmark with a shape rather than a window: a ring valley-field around a
     * flat centre, so the offset spline carves a crater - peaks on the rim, the throne's plateau in the
     * middle. All of it is smooth Gaussian blending in the ridges map: no pins, no clamps.
     */
    private double calderaRing(double x, double z, double rid) {
        if (calderaIdx < 0) {
            return rid;
        }
        Landmark lm = Spec.LANDMARKS.get(calderaIdx);
        double taper = Regions.boxFeather(calderaIdx, x, z, 260.0);
        if (taper <= 0.0) {
            return rid;
        }
        double d = Math.hypot(x - lm.x(), z - lm.z());
        double ring = Math.exp(-sq((d - CALDERA_RING_R) / CALDERA_RING_W));
        double floor = Math.exp(-sq(d / CALDERA_FLOOR_R));
        double t = rid * (1.0 - ring) + CALDERA_RING_RIDGES * ring;
        t = t * (1.0 - floor) + CALDERA_FLOOR_RIDGES * floor;
        return rid * (1.0 - taper) + t * taper;
    }

    private static double sq(double v) {
        return v * v;
    }

    private static final double CALDERA_RING_R = 430.0;
    private static final double CALDERA_RING_W = 190.0;
    private static final double CALDERA_FLOOR_R = 270.0;
    private static final double CALDERA_RING_RIDGES = 0.78;
    private static final double CALDERA_FLOOR_RIDGES = 0.06;

    // ---------------------------------------------------------------------------------------
    // climate helpers
    // ---------------------------------------------------------------------------------------

    private double borealBuffer(double t, double x, double z) {
        double south = borealFloor(t, x, z);
        return Math.min(t, south);
    }

    /** The cold floor south of the Veil, feathered with noise so its border is never a straight line. */
    private double borealFloor(double t, double x, double z) {
        double edge = Noise.fbm(x, z, 3, 2200.0, seed + 811) * 700.0;
        double band = Mathx.smoothstep(Spec.VEIL_RADIUS - 900.0 + edge, Spec.VEIL_RADIUS + 400.0 + edge,
                Math.hypot(x, z));
        return Mathx.lerp(t, Math.min(t, -0.55), band);
    }

    /** Landmark ownership fraction for one column (kept for the tools). */
    public int landmarkAt(double x, double z) {
        return Regions.idAt(x, z);
    }
}
