package io.github.exo2v.vantraya.core;

import java.util.List;
import java.util.concurrent.ConcurrentHashMap;
import java.util.function.Function;

import io.github.exo2v.vantraya.core.Instances.Instance;
import io.github.exo2v.vantraya.core.Spec.Kind;
import io.github.exo2v.vantraya.core.Spec.Landmark;
import io.github.exo2v.vantraya.core.Spec.Window;

/**
 * The continent of Vantyra as a pure function of a world seed and a block coordinate.
 *
 * <p>This is the offline engine's {@code AshenfallBuilder.build} + {@code apply_ashenfall} chain
 * (HANDOFF sections 5.2 - 5.7, 5.11, 5.12) re-expressed so that any single column can be evaluated on
 * its own, which is what a live chunk generator needs. The specification's tables stay the authority
 * and noise only supplies texture: every landmark is shaped to land on its stated elevation.
 *
 * <p>Stages that are global in the offline pipeline are replaced by local equivalents:
 * <ul>
 *   <li>{@code windows()} percentile bounds -> {@link Calibration} constants;</li>
 *   <li>distance-transform feathers -> closed-form signed distances ({@link Regions});</li>
 *   <li>priority-flood / flow-accumulation rivers and lakes -> a river-valley and basin field that
 *       honours the same rules (valleys where {@code R ~ 0}, no standing water on dry landforms,
 *       absolutely no water in the caldera);</li>
 *   <li>post-hydrology landmark re-pinning -> the same radius-220 pull, applied pointwise;</li>
 *   <li>moisture advection -> a closed-form moisture budget.</li>
 * </ul>
 *
 * <p>Instances are immutable and thread-safe; sampling reuses a small per-thread cache so that the
 * several density-function channels evaluated at one column cost one evaluation.
 */
public final class VantrayaModel {

    /** Tunables that change how faithfully (or how practically) the offline engine is mirrored. */
    public record Config(Calibration calibration, double instanceNoiseOffset, boolean seamlessCoast,
                         boolean carveWater, boolean repin, boolean borealBuffer) {

        /** What the Minecraft world type uses for an arbitrary world seed. */
        public static Config production() {
            return new Config(Calibration.DEFAULT, 3998.0, true, true, true, true);
        }

        /** The canonical build (seed 20250929): the reference build's own percentile bounds. */
        public static Config canonical() {
            return new Config(Calibration.SPEC_SEED_REFERENCE, 3998.0, true, true, true, true);
        }

        /**
         * Mirror of the offline engine's analytic builder (no hydrology, no re-pin, no boreal buffer, the
         * table's west-axis seam kept) at the given raster cell size - used to cross-check the two
         * implementations.
         */
        public static Config parity(int cellSize, Calibration calibration) {
            return new Config(calibration, 4000.0 - 0.5 * cellSize, false, false, false, false);
        }
    }

    /** Everything the generator needs to know about one column. Heights are in blocks, climate in spec units. */
    public record Fields(
            double x, double z,
            double cont, double erosion, double ridges,
            double temperature, double humidity,
            double demMacro, double height,
            int landmark, boolean lava, boolean noWater, boolean noLake,
            double river, double lake, double waterLine,
            double rough3d, double pin,
            double tempAsh, double humAsh) {

        public int tier() {
            return Spec.climateTier(temperature);
        }
    }

    // ---------------------------------------------------------------------------------------

    private final long seed;
    private final Config cfg;
    private final Calibration cal;
    private final List<Instance> spine;
    private final List<Instance> needles;
    private final int cordilleraIdx;
    private final int spiresIdx;

    public VantrayaModel(long seed, Config cfg) {
        this(seed, cfg, null, null);
    }

    /**
     * Test hook: use explicit instance lists instead of the seeded ones (for example the stations the
     * offline engine stamped), so the rendering and composition maths can be compared like for like.
     */
    public VantrayaModel(long seed, Config cfg, List<Instance> spineOverride, List<Instance> needlesOverride) {
        this.seed = seed;
        this.cfg = cfg;
        this.cal = cfg.calibration();
        int ci = -1;
        int si = -1;
        for (int i = 0; i < Spec.LANDMARKS.size(); i++) {
            Kind k = Spec.LANDMARKS.get(i).kind();
            if (k == Kind.CORDILLERA) {
                ci = i;
            } else if (k == Kind.SPIRES) {
                si = i;
            }
        }
        this.cordilleraIdx = ci;
        this.spiresIdx = si;
        this.spine = spineOverride != null ? spineOverride
                : (ci < 0 ? List.of() : Instances.buildCordillera(Spec.LANDMARKS.get(ci), shaperSeed(ci)));
        this.needles = needlesOverride != null ? needlesOverride
                : (si < 0 ? List.of() : Instances.buildNeedles(Spec.LANDMARKS.get(si), shaperSeed(si)));
    }

    private static final ConcurrentHashMap<Long, VantrayaModel> CACHE = new ConcurrentHashMap<>();

    /**
     * The production model of a world seed (cached; at most a handful of seeds are ever live). The
     * canonical seed {@link Spec#SPEC_SEED} reproduces the shipped build's mountain layout exactly.
     */
    public static VantrayaModel forSeed(long seed) {
        VantrayaModel m = CACHE.get(seed);
        if (m == null) {
            if (CACHE.size() > 8) {
                CACHE.clear();
            }
            m = CACHE.computeIfAbsent(seed, VantrayaModel::create);
        }
        return m;
    }

    private static VantrayaModel create(long seed) {
        if (seed == Spec.SPEC_SEED) {
            return new VantrayaModel(seed, Config.canonical(), SpecSeedInstances.SPINE, SpecSeedInstances.NEEDLES);
        }
        return new VantrayaModel(seed, Config.production());
    }

    public long seed() {
        return seed;
    }

    public Config config() {
        return cfg;
    }

    public List<Instance> spine() {
        return spine;
    }

    public List<Instance> needles() {
        return needles;
    }

    /** {@code seed + idx * 137}, the per-landmark seed the offline engine hands each shaper. */
    private long shaperSeed(int idx) {
        return seed + idx * 137L;
    }

    // ---------------------------------------------------------------------------------------
    // per-thread sample cache
    // ---------------------------------------------------------------------------------------

    private static final int CACHE_SIZE = 1024;

    private static final class SampleCache {
        final long[] kx = new long[CACHE_SIZE];
        final long[] kz = new long[CACHE_SIZE];
        final Fields[] val = new Fields[CACHE_SIZE];
    }

    private final ThreadLocal<SampleCache> local = ThreadLocal.withInitial(SampleCache::new);

    /** All macro fields of the column at {@code (x, z)}. */
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

    private Fields compute(double x, double z) {
        // ---- 1. continentalness -------------------------------------------------------------
        double[] warp = new double[2];
        Noise.domainWarp(x, z, 2100.0, 900.0, seed + 17, 4, warp);
        double contRaw = rawContinentalness(x, z, warp[0], warp[1]);
        double cont = windows(contRaw, Landmark::cont, 0.60, cal.contLo(), cal.contHi(), x, z);

        // ---- 2. erosion / ridges ------------------------------------------------------------
        double eroUnit = Mathx.stretch01(Noise.fbm(x, z, 4, 1500.0, seed + 31), cal.eroLo(), cal.eroHi());
        double ero = windows(eroUnit * 2.0 - 1.0, Landmark::erosion, 0.70, -1.0, 1.0, x, z);
        double ridUnit = Mathx.stretch01(Noise.fbm(x, z, 5, 1250.0, seed + 41), cal.ridLo(), cal.ridHi());
        double rid = windows(ridUnit * 2.0 - 1.0, Landmark::ridges, 0.70, -1.0, 1.0, x, z);

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
        double tAshUnit = Mathx.saturate((t + 1.0) * 0.5);
        double hAshUnit = Mathx.saturate((h + 1.0) * 0.5);

        // ---- 4. base height and relief ------------------------------------------------------
        double land = Mathx.smoothstep(-0.12, 0.28, cont);
        double base = Tables.BASE_SPLINE.eval(cont);
        double rid01 = Mathx.saturate(rid * 0.5 + 0.5);
        double belt = Mathx.smoothstep(0.48, 0.95, rid01) * Mathx.smoothstep(0.20, 0.62, cont);
        double ridgeAmp = 18.0 + 145.0 * belt;
        double ridgeShape = Noise.ridgedFbm(x, z, 6, 1050.0, seed + 51);
        double relief = ridgeAmp * ridgeShape * land;
        double detailScale = Mathx.clamp(1.1 - 0.7 * (ero * 0.5 + 0.5), 0.2, 1.2);
        double rolling = Noise.fbm(x, z, 4, 1150.0, seed + 61) * (4.0 + 9.0 * land);
        double hills = Noise.fbm(x, z, 4, 420.0, seed + 71) * (3.0 + 6.0 * land);
        double detail = Noise.fbm(x, z, 4, 120.0, seed + 81) * 2.6;
        double demMacro = base + (relief + rolling + hills) * detailScale + detail * land;

        // ---- 5. shelf, Veil of Salt and the nine landmarks ----------------------------------
        double dem = demMacro;
        double innerW = Mathx.smoothstep(Spec.SHELF_INNER - 900.0, Spec.SHELF_INNER, r);
        double outerW = Mathx.smoothstep(Spec.SHELF_INNER, Spec.SHELF_OUTER, r);
        double abyssW = Mathx.smoothstep(Spec.VEIL_RADIUS, Spec.VEIL_RADIUS + 220.0, r);
        double drop = Spec.shelfDrop(r);
        if (innerW > 0.0) {
            dem = dem * (1.0 - innerW * 0.7) + (Spec.SEA_LEVEL + drop) * (innerW * 0.7);
            double floor = Math.max(Spec.SEA_LEVEL + drop, Spec.ABYSS_FLOOR);
            dem = dem * (1.0 - outerW) + floor * outerW;
        }
        if (abyssW > 0.0) {
            double veilFloor = Spec.ABYSS_FLOOR + 3.0 + 5.0 * Mathx.stretch01(
                    Noise.fbm(x, z, 3, 1400.0, seed + 1301), cal.veilLo(), cal.veilHi());
            dem = dem * (1.0 - abyssW) + veilFloor * abyssW;
        }

        int owner = Regions.idAt(x, z);
        boolean lava = false;
        boolean basinFlag = false;
        double pinMask = 0.0;
        for (int idx = 0; idx < lms.size(); idx++) {
            double sd = Regions.sdOwned(idx, x, z);
            if (sd < -160.0) {
                continue; // outside the feather's reach (it extends at most ~110 blocks past the border)
            }
            Landmark lm = lms.get(idx);
            double width = 200.0;
            double edge = Noise.fbm(x, z, 3, Math.max(260.0, width * 1.6), seed + 3000 + idx * 13L);
            double wgt = Regions.noisyFeather(sd, edge, width, 0.55);
            if (lm.kind() == Kind.CALDERA) {
                if (wgt > 0.0) {
                    Landforms.Caldera cd = Landforms.caldera(x, z, dem, seed);
                    double blend = Mathx.saturate(wgt * 1.8);
                    dem = dem * (1.0 - blend) + cd.target() * blend;
                    boolean inside = sd > 0.0;
                    if (inside) {
                        lava |= cd.lava();
                        basinFlag |= cd.basin();
                    }
                }
            } else if (wgt > 0.0) {
                double target = shape(idx, lm, x, z, dem);
                boolean soft = lm.kind() == Kind.FEN || lm.kind() == Kind.DROWNED_SHELF || lm.kind() == Kind.COAST;
                boolean hard = lm.kind() == Kind.QUARRY || lm.kind() == Kind.DUNES
                        || lm.kind() == Kind.TERRACES || lm.kind() == Kind.SPIRES;
                double strong = Mathx.saturate(wgt * (soft ? 1.25 : (hard ? 2.0 : 0.9)));
                dem = dem * (1.0 - strong) + target * strong;
            }
            if (lm.kind() != Kind.CALDERA) {
                double pinR = (lm.kind() == Kind.DUNES || lm.kind() == Kind.CORDILLERA) ? 260.0 : 150.0;
                double d = Math.hypot(x - lm.x(), z - lm.z());
                double pin = Math.pow(Mathx.saturate(1.0 - d / pinR), 2.0);
                if (pin > 0.0) {
                    dem = dem * (1.0 - pin) + lm.y() * pin;
                    pinMask = Math.max(pinMask, pin);
                }
            }
        }
        dem = Mathx.clamp(dem, Spec.ABYSS_FLOOR, Spec.SPINE_HIGH + 4.0);

        // ---- 6. dry masks (HANDOFF 5.11) ----------------------------------------------------
        boolean noWater = basinFlag || lava;
        boolean noLake = owner >= 0 && owner < lms.size() && isDryKind(lms.get(owner).kind());

        // ---- 7. rivers, lakes (runtime replacement for hydrology) ---------------------------
        double river = 0.0;
        double lake = 0.0;
        double waterLine = NO_WATER_LINE;
        if (cfg.carveWater() && !noWater && r < Spec.SHELF_INNER) {
            double[] wr = water(x, z, dem, rid, cont, noLake);
            // The specification gives every landmark centre an exact elevation, so water stays out of the pin
            // zone around it (the offline engine re-pins after hydrology; here the water simply never gets there).
            double keep = 1.0 - Mathx.smoothstep(0.0, 0.35, pinMask);
            dem = dem + (wr[0] - dem) * keep;
            river = wr[1] * keep;
            lake = wr[2] * keep;
            waterLine = wr[3];
        }

        // ---- 8. landmark re-pinning (HANDOFF 5.12) ------------------------------------------
        if (cfg.repin() && dem >= Spec.SEA_LEVEL) {
            for (Landmark lm : lms) {
                if (lm.kind() == Kind.CALDERA) {
                    continue;
                }
                double d = Math.hypot(x - lm.x(), z - lm.z());
                double wRepin = Math.pow(Mathx.saturate(1.0 - d / 220.0), 2.0);
                if (wRepin > 0.0) {
                    dem += Mathx.clamp(lm.y() - dem, -6.0, 6.0) * wRepin;
                }
            }
        }

        // ---- 9. altitude lapse on the finished surface (ClimateModel.finalize) --------------
        double above = Math.max(dem - Spec.SEA_LEVEL, 0.0);
        double tFinalUnit = Mathx.saturate(tAshUnit - Math.min(above * 0.00275, 0.60));
        if (cfg.borealBuffer()) {
            // the belt must stay non-snowy on the *effective* temperature too, foothills included
            tFinalUnit = Mathx.saturate((borealFloor((tFinalUnit * 2.0 - 1.0), x, z) + 1.0) * 0.5);
        }
        double hFinalUnit = Mathx.saturate(0.45 * hAshUnit
                + 0.55 * (adv(c) * 0.85 + humidityPatch(x, z) * 0.15) - c * 0.10);

        // ---- 10. how much 3D noise the density function may add -----------------------------
        double rugged = Mathx.saturate((0.30 - ero) / 0.9);
        if (owner == cordilleraIdx || owner == spiresIdx) {
            rugged = Math.max(rugged, 0.9);
        }
        double rough3d = (0.12 + 0.88 * rugged) * (1.0 - pinMask);

        // A water surface at or below the finished ground (re-pinning lifts it near a landmark) means the
        // channel is not cut here: this column is a bank and gets no water.
        if (waterLine <= dem) {
            waterLine = NO_WATER_LINE;
        }
        return new Fields(x, z, cont, ero, rid,
                tFinalUnit * 2.0 - 1.0, hFinalUnit * 2.0 - 1.0,
                demMacro, dem, owner, lava, noWater, noLake, river, lake, waterLine, rough3d, pinMask,
                tAshUnit * 2.0 - 1.0, hAshUnit * 2.0 - 1.0);
    }

    /** Width of the mandatory non-snowy belt around the Glacial Spine (spec section 4, tier 1). */
    public static final double BOREAL_BUFFER_WIDTH = 600.0;

    /**
     * The spec's tier-1 rule: a mandatory 600-block non-snowy boreal (pine taiga) belt separates the glacial
     * tier from the temperate lowlands, so that snow never touches temperate plains. The offline engine
     * only applies the 220-block landmark windows, which leaves temperate terrain touching the Spine's
     * edge; here the temperature around the Spine's box is eased to the top of the tier-1 band
     * ({@code -0.58}) at the edge and to {@code -0.40} at 600 blocks (both inside the spec's tier-1 range and
     * the engine's boreal biome band), then released back to the regional climate over the next 300 blocks.
     * Inside the box the ease fades out exactly as the Spine's own window ramps in, so the profile is
     * continuous across the boundary; landmarks with their own windows (Hermit's Spire, Byzantine Choir)
     * are applied afterwards and win.
     */
    private double borealBuffer(double t, double x, double z) {
        if (cordilleraIdx < 0) {
            return t;
        }
        double sd = Regions.sdBox(Spec.LANDMARKS.get(cordilleraIdx), x, z); // > 0 inside the box
        double w = BOREAL_BUFFER_WIDTH;
        double k;
        if (sd >= 0.0) {
            k = 1.0 - Mathx.smoothstep(0.0, Regions.WINDOW_FEATHER, sd);
        } else {
            double outside = -sd;
            if (outside >= w + 300.0) {
                return t;
            }
            k = 1.0 - Mathx.smoothstep(w, w + 300.0, outside);
        }
        k = Mathx.saturate(k * 0.95);
        double target = -0.58 + 0.18 * Mathx.smoothstep(0.0, w, Math.max(-sd, 0.0));
        return t * (1.0 - k) + target * k;
    }

    /** Effective temperature below which the belt may not fall (the boreal band's snow-free lower edge). */
    private static final double BOREAL_FLOOR = -0.62;

    /**
     * Outside the Spine's box, keep the lapse-adjusted temperature from dropping below the boreal band,
     * so foothills inside the 600-block belt are taiga rather than snowy taiga (the specification's
     * "non-snowy pine taiga buffer"). The ease fades out over the 300 blocks beyond the belt.
     */
    private double borealFloor(double t, double x, double z) {
        if (cordilleraIdx < 0) {
            return t;
        }
        double sd = Regions.sdBox(Spec.LANDMARKS.get(cordilleraIdx), x, z);
        if (sd >= 0.0) {
            return t;
        }
        double outside = -sd;
        double w = BOREAL_BUFFER_WIDTH;
        if (outside >= w + 300.0) {
            return t;
        }
        double k = Mathx.saturate((1.0 - Mathx.smoothstep(w, w + 300.0, outside)) * 0.95);
        return t + k * Math.max(0.0, BOREAL_FLOOR - t);
    }

    /** Land-side moisture after the advection steps, normalised against the wettest (ocean) cell. */
    private static double adv(double c) {
        return Mathx.saturate((moisture(c) - MOIST_MIN) / Math.max(1.4 - MOIST_MIN, 1e-9));
    }

    private static boolean isDryKind(Kind k) {
        return k == Kind.CALDERA || k == Kind.DUNES || k == Kind.QUARRY || k == Kind.TERRACES || k == Kind.COAST;
    }

    private double shape(int idx, Landmark lm, double x, double z, double dem) {
        long s = shaperSeed(idx);
        switch (lm.kind()) {
            case QUARRY:
                return Landforms.quarry(x, z, lm, s);
            case CORDILLERA:
                return Landforms.cordillera(x, z, dem, spine, cfg.instanceNoiseOffset());
            case DUNES:
                return Landforms.dunes(x, z, s, cal);
            case FEN:
                return Landforms.fen(x, z, lm, s);
            case DROWNED_SHELF:
                return Landforms.drownedShelf(x, z, lm, s, cal);
            case SPIRES:
                return Landforms.spires(x, z, dem, lm, needles, cfg.instanceNoiseOffset());
            case TERRACES:
                return Landforms.terraces(x, z, lm, s);
            case COAST:
                return Landforms.coast(x, z, dem, s);
            default:
                return dem;
        }
    }

    // ---------------------------------------------------------------------------------------
    // rivers and lakes
    // ---------------------------------------------------------------------------------------

    /** The water surface of a channel sits this far below the natural ground of its bed. */
    private static final double RIVER_INCISE = 2.0;
    /** How far below the water surface the channel floors are cut. */
    private static final double TRUNK_DEPTH = 4.0;
    private static final double TRIB_DEPTH = 3.0;
    private static final double LAKE_DEPTH = 3.5;

    /** {@link Fields#waterLine} of a column with no channel in it. */
    public static final double NO_WATER_LINE = -1.0E9;

    /**
     * Local stand-in for the offline hydrology: carve river valleys through the land and open lake basins in
     * them. Rivers are the zero set of a domain-warped noise (they meander, join and end at the sea), strongest
     * where the ridges field is near zero - the specification's "wide U-shaped river valleys at R ~ 0" - and
     * absent on ridge crests.
     *
     * <p>A channel follows the terrain: its water surface sits {@link #RIVER_INCISE} blocks below the natural
     * ground of the bed and the floor a few blocks below that, so a river runs downhill across the continent
     * and carries real water (the {@code RiverWater} pass fills it to {@link Fields#waterLine}) instead of
     * being a dry notch. Rivers reach up to about Y = 150 and lakes to about Y = 110 and fade out above
     * (the first play test found the old lowland-only network a "critical lack of waterways"), and a
     * closed-basin landform keeps its rivers but never its lakes.
     *
     * @return {@code {dem, riverStrength, lakeStrength, waterSurfaceY}}
     */
    private double[] water(double x, double z, double dem, double rid, double cont, boolean noLake) {
        double low = 1.0 - Mathx.smoothstep(150.0, 190.0, dem);
        if (low <= 0.0) {
            return new double[] {dem, 0.0, 0.0, NO_WATER_LINE};
        }
        double line = dem - RIVER_INCISE;
        double trunkBed = line - TRUNK_DEPTH;
        double tribBed = line - TRIB_DEPTH;
        double[] w = new double[2];
        Noise.domainWarp(x, z, 900.0, 300.0, seed + 6001, 3, w);
        double wx = w[0];
        double wz = w[1];
        double ridgeFactor = 1.0 - Mathx.smoothstep(0.30, 0.90, Math.abs(rid));
        double seaward = Mathx.smoothstep(0.35, -0.05, cont);

        // trunk rivers
        double trunkDist = zeroSetDistance(wx, wz, 1500.0, seed + 6011);
        double halfTrunk = 6.0 + 9.0 * seaward;
        double s1 = ridgeFactor * low;
        double t1 = trunkDist / halfTrunk;
        double chan1 = (1.0 - Mathx.smoothstep(0.55, 1.0, t1)) * s1;
        double val1 = (1.0 - Mathx.smoothstep(1.0, 3.4, t1)) * s1;

        // tributaries (narrower)
        double tribDist = zeroSetDistance(wx, wz, 650.0, seed + 6013);
        double halfTrib = 3.5 + 2.0 * seaward;
        double s2 = ridgeFactor * low * 0.85;
        double t2 = tribDist / halfTrib;
        double chan2 = (1.0 - Mathx.smoothstep(0.55, 1.0, t2)) * s2;
        double val2 = (1.0 - Mathx.smoothstep(1.0, 3.0, t2)) * s2;

        double out = dem;
        double valley = Math.max(val1, val2);
        double bed = chan1 >= chan2 ? trunkBed : tribBed;
        out -= valley * Math.max(out - bed, 0.0) * 0.45; // the wide U: ground slopes towards the channel floor
        out = out * (1.0 - chan2) + Math.min(out, tribBed) * chan2;
        out = out * (1.0 - chan1) + Math.min(out, trunkBed) * chan1;
        double river = Math.max(chan1, chan2);

        double lakeStrength = 0.0;
        if (!noLake) {
            double ln = Noise.fbm(x, z, 3, 1300.0, seed + 6021);
            double low2 = 1.0 - Mathx.smoothstep(110.0, 150.0, dem);
            lakeStrength = Mathx.smoothstep(0.22, 0.34, ln) * low2;
            out = out * (1.0 - lakeStrength) + Math.min(out, line - LAKE_DEPTH) * lakeStrength;
        }
        double waterLine = river + lakeStrength > 0.02 ? line : NO_WATER_LINE;
        return new double[] {out, river, lakeStrength, waterLine};
    }

    /** Approximate distance (blocks) to the zero contour of an fBm: {@code |n| / |grad n|}. */
    private static double zeroSetDistance(double wx, double wz, double scale, long seed) {
        double n = Noise.fbm(wx, wz, 3, scale, seed);
        double h = 6.0;
        double gx = (Noise.fbm(wx + h, wz, 3, scale, seed) - Noise.fbm(wx - h, wz, 3, scale, seed)) / (2 * h);
        double gz = (Noise.fbm(wx, wz + h, 3, scale, seed) - Noise.fbm(wx, wz - h, 3, scale, seed)) / (2 * h);
        double mag = Math.max(Math.hypot(gx, gz), 1e-6);
        return Math.abs(n) / mag;
    }

    // ---------------------------------------------------------------------------------------
    // convenience
    // ---------------------------------------------------------------------------------------

    /** Final surface height (blocks) of the column. */
    public double height(double x, double z) {
        return sample(x, z).height();
    }

    /**
     * Surface slope in degrees at a block, the way the offline engine measures it: the L1 norm of the
     * height-field gradient ({@code |dh/dx| + |dh/dz|}, central differences over one block), as an angle.
     */
    public double slopeDegrees(double x, double z) {
        double dx = (height(x + 1, z) - height(x - 1, z)) * 0.5;
        double dz = (height(x, z + 1) - height(x, z - 1)) * 0.5;
        return Math.toDegrees(Math.atan(Math.abs(dx) + Math.abs(dz)));
    }

    /** True when {@code window} contains {@code v} (small readability helper for tests and tools). */
    public static boolean inWindow(Window window, double v) {
        return window.contains(v);
    }
}
