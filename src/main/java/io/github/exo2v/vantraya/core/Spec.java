package io.github.exo2v.vantraya.core;

import java.util.List;

/**
 * The Ashenfall master specification, transcribed. Nothing here is invented: the canvas, the
 * elevation table, the nine landmarks (centre, box, elevation band, biomes, density windows and
 * climate windows), the Veil of Salt, the shelf dropoff, the climate tiers and the slope table all
 * come from {@code ASHENFALL_WORLD_MAP_MASTER_SPECIFICATION.pdf} as restated in {@code HANDOFF.md}
 * section 3 and implemented by the offline engine's {@code mg/generation/landmarks.py}.
 *
 * <p>A build that disagrees with these tables is wrong, not creative - the unit tests in
 * {@code SpecTest} pin every number.
 */
public final class Spec {
    private Spec() {
    }

    // ---------------------------------------------------------------------------------------
    // 1. Canvas and elevations (spec section 1)
    // ---------------------------------------------------------------------------------------

    /** The canvas is 8,000 x 8,000 blocks centred on the origin: -4000 .. +4000. */
    public static final int CANVAS = 8000;
    public static final int HALF = CANVAS / 2;
    public static final int MIN_Y = -64;
    public static final int MAX_Y = 320;
    /**
     * "Sea level is anchored at Y = 62". This is the height-field (DEM) constant: ground below it is
     * drowned, ground at or above it is dry. The exported reference world floods air blocks up to and
     * including Y = 62, which is what the Overworld generator does with {@code sea_level = 63}, so the
     * Vantraya noise settings use 63 and the two worlds share one waterline.
     */
    public static final double SEA_LEVEL = 62.0;
    /** World seed of the shipped, exported build of Vantyra. */
    public static final long SPEC_SEED = 20250929L;

    public static final double ABYSS_FLOOR = -32.0;
    public static final double TRENCH_TOP = 10.0;
    public static final double CALDERA_FLOOR = 40.0;
    public static final double CALDERA_THRONE = 92.0;
    public static final double CALDERA_RIM = 146.0;
    public static final double SHELF_TOP = 56.0;
    public static final double COAST_LOW = 68.0;
    public static final double COAST_HIGH = 74.0;
    public static final double DUNES_LOW = 82.0;
    public static final double DUNES_HIGH = 96.0;
    public static final double QUARRY_LOW = 85.0;
    public static final double QUARRY_HIGH = 110.0;
    public static final double TAIGA_LOW = 150.0;
    public static final double TAIGA_HIGH = 190.0;
    public static final double SPINE_LOW = 220.0;
    public static final double SPINE_HIGH = 279.2;
    /** Still Life's climatic treeline: nothing grows above this. */
    public static final double TREELINE = 225.0;

    /** 16-bit height normalisation: {@code h = (Y + 64) / 384}. */
    public static double yToNorm(double y) {
        return (y + 64.0) / 384.0;
    }

    public static int yToU16(double y) {
        return (int) Math.rint(Mathx.clamp(yToNorm(y), 0.0, 1.0) * 65535.0);
    }

    public static double u16ToY(int u16) {
        return -64.0 + (u16 / 65535.0) * 384.0;
    }

    // ---------------------------------------------------------------------------------------
    // 2. Continental shelf dropoff and the Veil of Salt (spec section 1 / 2)
    // ---------------------------------------------------------------------------------------

    public static final double SHELF_INNER = 3300.0;
    public static final double SHELF_OUTER = 3800.0;
    public static final double SHELF_DEPTH_SPEC = -600.0;
    /** The Veil of Salt: everything beyond this radius is the brine abyss. */
    public static final double VEIL_RADIUS = 3550.0;

    /**
     * Hermite S-curve dropoff beyond the continental shelf: {@code t = clamp((r - 3300) / 500)},
     * {@code S(t) = 3t^2 - 2t^3}, {@code H_drop = -600 * S(t)}.
     *
     * <p>The literal -600 would fall through the world floor (Y = -64) since the same table puts the
     * abyssal trench at Y = -32, so the curve is the <em>shape</em> and its depth is clamped to the
     * abyss floor (HANDOFF section 5.7).
     */
    public static double shelfDrop(double radius) {
        double t = Mathx.saturate((radius - SHELF_INNER) / Math.max(SHELF_OUTER - SHELF_INNER, 1e-6));
        double s = Mathx.hermite(t);
        return Math.max(SHELF_DEPTH_SPEC * s, ABYSS_FLOOR - SEA_LEVEL);
    }

    // ---------------------------------------------------------------------------------------
    // 3. Landmarks (spec sections 2-4)
    // ---------------------------------------------------------------------------------------

    /** The kind of landform; drives the shaper, the water rules and the surface palette. */
    public enum Kind {
        COAST, QUARRY, CALDERA, CORDILLERA, DUNES, FEN, DROWNED_SHELF, SPIRES, TERRACES, VEIL
    }

    /** A {@code [lo, hi]} window in the specification's {@code -1..+1} units. */
    public record Window(double lo, double hi) {
        public boolean contains(double v) {
            return v >= lo && v <= hi;
        }
    }

    /** One landmark: exact centre, bounding box, elevation band, biomes and density/climate windows. */
    public record Landmark(
            String key, String name,
            double x, double y, double z,
            double x1, double x2, double z1, double z2,
            double yLo, double yHi,
            Kind kind, List<String> biomes,
            Window cont, Window erosion, Window ridges,
            Window temp, Window humidity,
            String surface) {

        public boolean boxContains(double px, double pz) {
            return px >= x1 && px <= x2 && pz >= z1 && pz <= z2;
        }

        public double area() {
            return (x2 - x1) * (z2 - z1);
        }
    }

    private static Window w(double lo, double hi) {
        return new Window(lo, hi);
    }

    /** The nine landmarks in the order of the specification (landmark 0 is the spawn). */
    public static final List<Landmark> LANDMARKS = List.of(
            new Landmark("forgotten_coast", "Forgotten Coast",
                    0, 68, 2500, -600, 600, 2000, 3200, 68, 74,
                    Kind.COAST, List.of("plains", "meadow"),
                    w(0.00, 0.35), w(-0.25, 0.25), w(0.05, 0.50), w(-0.20, 0.15), w(-0.35, 0.30),
                    "grass_block, podzol, stony_shore"),
            new Landmark("cogwork_march", "Cogwork March",
                    -2100, 85, 0, -2800, -1400, -700, 700, 85, 110,
                    Kind.QUARRY, List.of("windswept_hills", "wooded_badlands"),
                    w(0.10, 0.40), w(-0.35, 0.05), w(-0.55, -0.20), w(0.0, 0.5), w(-0.4, 0.3),
                    "orange/yellow terracotta, stone, andesite"),
            new Landmark("ashen_caldera", "The Ashen Caldera",
                    0, 80, 0, -750, 750, -750, 750, 38, 150,
                    Kind.CALDERA, List.of("basalt_deltas", "eroded_badlands"),
                    w(0.25, 0.55), w(-0.70, -0.35), w(0.02, 0.35), w(0.70, 1.0), w(-0.8, -0.2),
                    "basalt, blackstone, magma_block, obsidian"),
            new Landmark("glacial_spine", "Solitary Glacial Spine",
                    0, 220, -2500, -1800, 1800, -3500, -1500, 180, 279,
                    Kind.CORDILLERA, List.of("frozen_peaks", "jagged_peaks", "grove"),
                    w(0.75, 0.95), w(-0.85, -0.55), w(0.60, 0.95), w(-1.0, -0.75), w(-0.4, 0.4),
                    "snow_block, packed_ice, calcite, stone"),
            new Landmark("gilded_dunes", "The Gilded Dunes",
                    2300, 75, 0, 1600, 3100, -800, 800, 75, 94,
                    Kind.DUNES, List.of("desert", "badlands"),
                    w(0.20, 0.45), w(-0.20, 0.02), w(-0.45, -0.15), w(0.70, 1.0), w(-0.8, -0.2),
                    "red_sand, sandstone, terracotta (table) / black glass crests (map)"),
            new Landmark("whispering_fen", "The Whispering Fen",
                    2000, 63, 2000, 1300, 2700, 1300, 2700, 62, 66,
                    Kind.FEN, List.of("swamp", "mangrove_swamp"),
                    w(0.05, 0.25), w(0.40, 0.85), w(-0.20, 0.20), w(0.35, 0.60), w(0.5, 1.0),
                    "mud, peat, moss, coarse_dirt"),
            new Landmark("sunken_reach", "The Sunken Reach",
                    -2400, 54, 1600, -3200, -1700, 1000, 2300, 50, 62,
                    Kind.DROWNED_SHELF, List.of("warm_ocean", "lukewarm_ocean"),
                    w(-0.25, -0.18), w(0.10, 0.50), w(-0.50, 0.50), w(0.35, 0.60), w(0.4, 1.0),
                    "sand, coral reefs, prismarine gravel"),
            new Landmark("hermits_spire", "The Hermit's Spire",
                    -1800, 140, -1800, -2300, -1300, -2300, -1300, 140, 185,
                    Kind.SPIRES, List.of("windswept_hills", "meadow"),
                    w(0.45, 0.75), w(-0.72, -0.42), w(0.42, 0.56), w(-0.40, -0.05), w(-0.45, 0.05),
                    "granite, stone, cobblestone"),
            new Landmark("byzantine_choir", "The Byzantine Choir",
                    1800, 120, -1800, 1300, 2300, -2300, -1300, 110, 145,
                    Kind.TERRACES, List.of("meadow", "cherry_grove"),
                    w(0.40, 0.70), w(-0.72, -0.45), w(0.38, 0.50), w(-0.15, 0.15), w(-0.45, 0.05),
                    "cherry terraces, stone, gilded ruins"));

    /**
     * Radius, in blocks, around every landmark centre within which vanilla's cave entrances and noodle tunnels may
     * not open the surface. The first in-engine run found a cave entrance 17 blocks deep in the pinned top of the
     * Hermit's Spire, which contradicts "exact centre elevation" - and the Forgotten Coast's centre is where a new
     * world spawns. Caves underground are untouched.
     */
    public static final double PROTECT_RADIUS = 64.0;

    /** 1 within {@link #PROTECT_RADIUS} of a landmark centre, else 0 (the {@code protect} channel). */
    public static double protection(double x, double z) {
        for (Landmark lm : LANDMARKS) {
            if (Math.hypot(x - lm.x(), z - lm.z()) < PROTECT_RADIUS) {
                return 1.0;
            }
        }
        return 0.0;
    }

    /** The Veil of Salt "landmark" (rim): whole canvas, radius &gt; 3550. */
    public static final Landmark VEIL = new Landmark("veil_of_salt", "The Veil of Salt",
            0, 0, 0, -HALF, HALF, -HALF, HALF, ABYSS_FLOOR, SEA_LEVEL,
            Kind.VEIL, List.of("deep_cold_ocean", "deep_ocean"),
            w(-0.90, -0.45), w(-0.60, 0.40), w(-1.00, 1.00), w(-1.0, 1.0), w(-1.0, 1.0),
            "gravel, deepslate, calcite salt crust");

    /** Index used for "the Veil of Salt" in landmark-id arrays ({@code LANDMARKS.size()}). */
    public static final int VEIL_ID = LANDMARKS.size();

    public static Landmark byKey(String key) {
        for (Landmark lm : LANDMARKS) {
            if (lm.key().equals(key)) {
                return lm;
            }
        }
        if (VEIL.key().equals(key)) {
            return VEIL;
        }
        throw new IllegalArgumentException("unknown landmark " + key);
    }

    public static Landmark spawnLandmark() {
        return LANDMARKS.get(0);
    }

    // ---------------------------------------------------------------------------------------
    // 4. Climate tiers (spec section 4)
    // ---------------------------------------------------------------------------------------

    /** Temperature drop per block above sea level, in the specification's {@code -1..+1} units. */
    public static final double LAPSE_PER_BLOCK = 0.0055;

    /**
     * The 5-tier climate index of a temperature in spec units (HANDOFF section 3.3):
     * 0 glacial arctic, 1 boreal buffer belt, 2 temperate lowlands, 3 subtropical bayou,
     * 4 arid &amp; volcanic (default).
     */
    public static int climateTier(double t) {
        if (t <= -0.75) {
            return 0;
        }
        if (t <= -0.25) {
            return 1;
        }
        if (t <= 0.40) {
            return 2;
        }
        if (t < 0.70) {
            return 3;
        }
        return 4;
    }

    /** Effective temperature at altitude: {@code T_eff = T_base - 0.0055 * max(0, y - 62)}. */
    public static double effectiveTemperature(double tBase, double y) {
        return tBase - LAPSE_PER_BLOCK * Math.max(0.0, y - SEA_LEVEL);
    }

    // ---------------------------------------------------------------------------------------
    // 5. Slope-aware surface table (spec section 5, method 3)
    // ---------------------------------------------------------------------------------------

    public static final double SLOPE_SOIL_MAX = 25.0;
    public static final double SLOPE_DIRT_MAX = 35.0;
    public static final double SLOPE_SCREE_MAX = 45.0;
}
