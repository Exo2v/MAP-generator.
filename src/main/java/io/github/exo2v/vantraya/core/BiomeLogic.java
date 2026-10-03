package io.github.exo2v.vantraya.core;

import io.github.exo2v.vantraya.core.Spec.Kind;
import io.github.exo2v.vantraya.core.Spec.Landmark;

/**
 * Which biome role a point of the continent belongs to.
 *
 * <p>The specification names the biomes of every landmark and "spec landmarks override the classifier
 * outright" (HANDOFF section 5.14); everywhere else a Whittaker classifier decides, from temperature,
 * humidity, elevation and proximity to water (the offline engine's {@code BiomeClassifier}).
 *
 * <p>The inputs are exactly what a Minecraft {@code BiomeSource} is handed by the noise router - the
 * climate parameters and the column's {@code depth} - so no seed is needed here:
 * <ul>
 *   <li>the surface height of the column is recovered from {@code depth} ({@code depth = (H + 0.5 - y) / 128});</li>
 *   <li>water that is connected to the sea has {@code continentalness <= 0}; water inland of the
 *       shoreline is a river or lake;</li>
 *   <li>erosion stands in for the slope the offline engine measures on its raster.</li>
 * </ul>
 *
 * <h2>How borders are made to look like borders (the third attempt)</h2>
 * The play test of 0.1.2 called the interfaces "very stark borders ... terrible and boxy", and it was
 * right: a threshold is a wall, landmark regions are rectangles, and the fix of the day stamped 64-block
 * <em>squares</em> of one biome onto another. Vanilla's borders look the way they do because they are
 * level sets of smooth multi-octave noise in a continuous climate space: wandering curves at every scale,
 * never a line or a square. So this version:
 * <ul>
 *   <li><b>warps the classifier's inputs</b> with three octaves of coherent noise before any threshold is
 *       read, so every border wanders at every scale and follows fractal detail;</li>
 *   <li><b>has no patches</b> - the squares are gone;</li>
 *   <li><b>feathers region seams</b>: within about 80 blocks of a landmark's edge the two sides interleave
 *       in fractal blobs, and where the two sides are climatic extremes (cherry grove against frozen peaks)
 *       the band goes through the climate-<em>intermediate</em> role, so two extremes are never
 *       side by side;</li>
 *   <li><b>keeps landmark centres exact</b>: {@link Spec#protection} stills the warp there.</li>
 * </ul>
 */
public final class BiomeLogic {
    private BiomeLogic() {
    }

    /** Block height of the surface of a column: {@code depth = (H + 0.5 - y) / 128}. */
    public static double surfaceHeight(double depth, double y) {
        return depth * 128.0 + y - 0.5;
    }

    /** Distance below the surface, in blocks, at which cave biomes (and only they) take over. */
    public static final double CAVE_DEPTH = 0.2;

    private static final BiomeRole[][] LAND_MATRIX = {
            // band 0: frozen
            {BiomeRole.SNOWY_PLAINS, BiomeRole.SNOWY_TAIGA, BiomeRole.SNOWY_TAIGA, BiomeRole.GROVE, BiomeRole.GROVE},
            // band 1: cold
            {BiomeRole.COLD_SHRUBLAND, BiomeRole.TAIGA, BiomeRole.TAIGA, BiomeRole.OLD_GROWTH_TAIGA,
                    BiomeRole.OLD_GROWTH_TAIGA},
            // band 2: cool temperate
            {BiomeRole.HIGHLAND_STEPPE, BiomeRole.PLAINS, BiomeRole.TEMPERATE_FOREST,
                    BiomeRole.OLD_GROWTH_TEMPERATE_FOREST, BiomeRole.WINDSWEPT_HILLS},
            // band 3: warm temperate
            {BiomeRole.XERIC_SHRUBLAND, BiomeRole.MEADOW, BiomeRole.FERTILE_VALLEY, BiomeRole.TEMPERATE_FOREST,
                    BiomeRole.SWAMP},
            // band 4: subtropical
            {BiomeRole.DESERT, BiomeRole.SAVANNA, BiomeRole.HUMID_SAVANNA, BiomeRole.SPARSE_JUNGLE, BiomeRole.JUNGLE},
            // band 5: torrid
            {BiomeRole.DESERT, BiomeRole.SAVANNA, BiomeRole.HUMID_SAVANNA, BiomeRole.SPARSE_JUNGLE,
                    BiomeRole.TROPICAL_RAINFOREST}};

    private static final double[] TEMP_EDGES = {0.16, 0.33, 0.50, 0.67, 0.83};
    private static final double[] HUM_EDGES = {0.24, 0.42, 0.58, 0.76};

    /** Half-width of the seam band around a landmark region, in blocks. */
    public static final double SEAM_BAND = 80.0;

    /** {@code np.digitize}: the number of edges that are {@code <= v}. */
    private static int digitize(double v, double[] edges) {
        int n = 0;
        for (double e : edges) {
            if (v >= e) {
                n++;
            }
        }
        return n;
    }

    /**
     * Classify one cell of the biome grid.
     *
     * @param x     block x
     * @param y     block y of the cell
     * @param z     block z
     * @param t     temperature, spec units ({@code -1..1}), lapse already applied
     * @param h     humidity, spec units
     * @param c     continentalness
     * @param e     erosion
     * @param depth the router's depth: {@code (H + 0.5 - y) / 128}
     */
    public static BiomeRole classify(double x, double y, double z, double t, double h, double c, double e,
                                     double depth) {
        if (depth > CAVE_DEPTH) {
            BiomeRole cave = caveRole(t, h, c, e, depth);
            if (cave != null) {
                return cave;
            }
        }
        return surfaceRole(x, z, surfaceHeight(depth, y), t, h, c, e);
    }

    /** The three vanilla cave biomes, placed with the thresholds the Overworld biome builder uses. */
    static BiomeRole caveRole(double t, double h, double c, double e, double depth) {
        if (depth > 0.9 && e < -0.225) {
            return BiomeRole.DEEP_DARK;
        }
        if (depth <= 0.9 && h > 0.7) {
            return BiomeRole.LUSH_CAVES;
        }
        if (depth <= 0.9 && c > 0.8) {
            return BiomeRole.DRIPSTONE_CAVES;
        }
        return null;
    }

    /** Surface biome of the column whose ground is at {@code hs} blocks. */
    public static BiomeRole surfaceRole(double x, double z, double hs, double t, double h, double c, double e) {
        return surfaceRole(x, z, hs, t, h, c, e, true);
    }

    /**
     * The surface decision, optionally blended (the seam probes below ask their neighbours without
     * blending, so a seam always compares two unblended roles).
     */
    private static BiomeRole surfaceRole(double x, double z, double hs, double t, double h, double c, double e,
                                         boolean blend) {
        // every threshold below reads warped inputs, so every border is a wandering curve of the noise
        double jitter = 1.0 - Spec.protection(x, z);
        double w1 = wobble(x, z, 0xB1);
        double w2 = wobble(x, z, 0xB2);
        double w3 = wobble(x, z, 0xB3);
        double tU = Mathx.saturate((t + 1.0) * 0.5) + 0.07 * w1 * jitter;
        double hU = Mathx.saturate((h + 1.0) * 0.5) + 0.07 * w2 * jitter;
        double hsJ = hs + 8.5 * w3 * jitter;

        int id = Regions.idAt(x, z);
        boolean veil = id == Spec.VEIL_ID;
        Landmark lm = id >= 0 && id < Spec.LANDMARKS.size() ? Spec.LANDMARKS.get(id) : null;
        Kind kind = lm == null ? null : lm.kind();
        double sea = Spec.SEA_LEVEL;
        // The crater floor lies below the waterline but the caldera is dry by specification (lava basins, never
        // water): it is always the landmark's own biome, whatever the height.
        boolean water = hs < sea && kind != Kind.CALDERA;

        // ---- the Veil of Salt: cold, wet, featureless abyss ----------------------------------
        if (veil) {
            // both spec biomes occur, in broad patches (the Veil's temperature is a constant cold clamp)
            if (e < -0.08) {
                return BiomeRole.DEEP_COLD_OCEAN;
            }
            if (e > 0.08) {
                return BiomeRole.DEEP_OCEAN;
            }
            return Noise.value(x / 240.0, z / 240.0, 0x5EA1) < 0.5 ? BiomeRole.DEEP_OCEAN : BiomeRole.DEEP_COLD_OCEAN;
        }

        // ---- water ---------------------------------------------------------------------------
        if (water) {
            if (kind == Kind.DROWNED_SHELF) {
                return hs > 50.0 ? BiomeRole.WARM_OCEAN : BiomeRole.LUKEWARM_OCEAN;
            }
            if (kind == Kind.FEN && sea - hs < 7.0) {
                return hs < 63.2 ? BiomeRole.MANGROVE_SWAMP : BiomeRole.SWAMP; // bayou water stays swamp
            }
            if (c > 0.02) { // inland water: a river or a lake
                return tU < TEMP_EDGES[0] ? BiomeRole.FROZEN_RIVER : BiomeRole.RIVER;
            }
            return oceanRole(sea - hs, t);
        }

        // ---- the caldera and the rest of the specification's landmark palettes -------------
        double elev = hsJ - sea;
        boolean nearCoast = c < 0.12 && elev < 4.0;
        if (nearCoast && kind != Kind.CALDERA && kind != Kind.FEN) {
            int band = digitize(tU, TEMP_EDGES);
            int hum = digitize(hU, HUM_EDGES);
            if (kind == Kind.COAST) {
                return tU < TEMP_EDGES[0] ? BiomeRole.SNOWY_BEACH : BiomeRole.STONY_SHORE; // pebble beaches
            }
            if (e < -0.15) {
                return BiomeRole.STONY_SHORE;
            }
            if (band == 0) {
                return BiomeRole.SNOWY_BEACH;
            }
            if (band >= 4 && hum <= 2) {
                return BiomeRole.STONY_SHORE;
            }
            return BiomeRole.BEACH;
        }
        BiomeRole role = decide(hsJ, tU, hU, e, kind);
        if (!blend) {
            return role;
        }
        return seamBlend(x, z, hs, t, h, c, e, id, role);
    }

    /**
     * Across a region seam the two sides interleave in fractal blobs (never squares), and where the two
     * sides are climatic extremes the middle of the band is the climate-{@linkplain #intermediate
     * intermediate} role, so no two extremes are ever side by side.
     */
    private static BiomeRole seamBlend(double x, double z, double hs, double t, double h, double c, double e,
                                       int id, BiomeRole role) {
        double sd = seamDistance(x, z, id);
        if (Math.abs(sd) > SEAM_BAND) {
            return role;
        }
        BiomeRole other = nearbyOtherRole(x, z, hs, t, h, c, e, id);
        if (other == null || other == role) {
            return role;
        }
        double u = Mathx.smoothstep(-SEAM_BAND, SEAM_BAND, sd);
        double n = fractal(x, z, 0xC0);
        double uw = Mathx.saturate(u + 0.20 * (n - 0.5));
        if (contrast(role, other) > 0.85) {
            BiomeRole mid = intermediate(role, other);
            if (uw > 0.36 && uw < 0.64 && mid != null && mid != role && mid != other) {
                return mid;
            }
        }
        return uw >= 0.5 ? role : other;
    }

    /**
     * Signed distance to the seam the point is nearest: positive inside the owning region (a landmark box
     * or the Veil), negative outside it. Large in magnitude away from any seam.
     */
    private static double seamDistance(double x, double z, int id) {
        if (id == Spec.VEIL_ID) {
            return Math.hypot(x, z) - Spec.VEIL_RADIUS;
        }
        if (id >= 0 && id < Spec.LANDMARKS.size()) {
            return Regions.sdOwned(id, x, z);
        }
        double best = -1.0E9;
        for (int i = 0; i < Spec.LANDMARKS.size(); i++) {
            best = Math.max(best, Regions.sdOwned(i, x, z));
        }
        best = Math.max(best, Spec.VEIL_RADIUS - Math.hypot(x, z));
        return best;
    }

    /** The role of the region on the other side of a nearby seam, or null when there is no seam nearby. */
    private static BiomeRole nearbyOtherRole(double x, double z, double hs, double t, double h, double c, double e,
                                             int id) {
        double reach = Mathx.clamp(Math.abs(seamDistance(x, z, id)) + 14.0, 34.0, 120.0);
        double[][] probes = {{reach, 0}, {-reach, 0}, {0, reach}, {0, -reach}};
        for (double[] p : probes) {
            if (Regions.idAt(x + p[0], z + p[1]) != id) {
                return surfaceRole(x + p[0], z + p[1], hs, t, h, c, e, false);
            }
        }
        return null;
    }

    /** The role of a place before blending: the landmark's palette or the Whittaker grid. */
    private static BiomeRole decide(double hs, double tU, double hU, double e, Kind kind) {
        return kind != null ? landmarkRole(kind, hs) : generic(hs, tU, hU, e);
    }

    /** Three-octave value noise in {@code -1..1} (mean zero): the warp that makes every border wander. */
    private static double wobble(double x, double z, int salt) {
        return 0.5 * Noise.value(x / 165.0, z / 165.0, salt)
                + 0.32 * Noise.value(x / 62.0, z / 62.0, salt * 31)
                + 0.18 * Noise.value(x / 23.0, z / 23.0, salt * 57);
    }

    /** Value noise in {@code 0..1} (mean 0.5): fractal blobs for the seam band. */
    private static double fractal(double x, double z, int salt) {
        return 0.5 + 0.5 * (0.6 * Noise.value(x / 46.0, z / 46.0, salt)
                + 0.4 * Noise.value(x / 17.0, z / 17.0, salt * 13));
    }

    /**
     * The biome of a landmark at ground height {@code hs}: within a region the first-listed biome covers
     * the low ground and the second takes the high or deep ground (the offline engine's
     * {@code landmark_biome_field}).
     */
    public static BiomeRole landmarkRole(Kind kind, double hs) {
        switch (kind) {
            case CALDERA:
                return hs > 120.0 ? BiomeRole.ERODED_BADLANDS : BiomeRole.BASALT_DELTAS;
            case CORDILLERA:
                if (hs > 240.0) {
                    return BiomeRole.FROZEN_PEAKS;
                }
                return hs > 196.0 ? BiomeRole.JAGGED_PEAKS : BiomeRole.GROVE;
            case DUNES:
                return hs > 88.0 ? BiomeRole.BADLANDS : BiomeRole.DESERT;
            case FEN:
                return hs < 63.2 ? BiomeRole.MANGROVE_SWAMP : BiomeRole.SWAMP;
            case DROWNED_SHELF:
                return hs > 50.0 ? BiomeRole.WARM_OCEAN : BiomeRole.LUKEWARM_OCEAN;
            case SPIRES:
                return hs > 150.0 ? BiomeRole.WINDSWEPT_HILLS : BiomeRole.MEADOW;
            case TERRACES:
                return hs > 126.0 ? BiomeRole.CHERRY_GROVE : BiomeRole.MEADOW;
            case COAST:
                return hs > 71.0 ? BiomeRole.MEADOW : BiomeRole.PLAINS;
            case QUARRY:
                return hs > 104.0 ? BiomeRole.WINDSWEPT_HILLS : BiomeRole.WOODED_BADLANDS;
            default:
                return BiomeRole.DEEP_COLD_OCEAN;
        }
    }

    /** Ocean family by depth below the waterline and temperature (vanilla has one per climate band). */
    static BiomeRole oceanRole(double depthBelowSea, double t) {
        if (depthBelowSea < 14.0) {
            if (t >= 0.45) {
                return BiomeRole.WARM_OCEAN;
            }
            if (t >= 0.10) {
                return BiomeRole.LUKEWARM_OCEAN;
            }
            if (t >= -0.55) {
                return BiomeRole.OCEAN;
            }
            return t >= -0.80 ? BiomeRole.COLD_OCEAN : BiomeRole.FROZEN_OCEAN;
        }
        if (depthBelowSea <= 50.0) {
            if (t >= -0.55) {
                return BiomeRole.OCEAN;
            }
            return t >= -0.80 ? BiomeRole.COLD_OCEAN : BiomeRole.FROZEN_OCEAN;
        }
        if (t >= 0.45) {
            return BiomeRole.DEEP_LUKEWARM_OCEAN;
        }
        if (t >= -0.30) {
            return BiomeRole.DEEP_OCEAN;
        }
        return t >= -0.80 ? BiomeRole.DEEP_COLD_OCEAN : BiomeRole.DEEP_FROZEN_OCEAN;
    }

    /** The Whittaker classifier for terrain outside every landmark (and the high ground inside none). */
    static BiomeRole generic(double hs, double tU, double hU, double e) {
        double sea = Spec.SEA_LEVEL;
        int band = digitize(tU, TEMP_EDGES);
        int hum = digitize(hU, HUM_EDGES);
        BiomeRole b = LAND_MATRIX[band][hum];
        double elev = hs - sea;

        // swamps: flat (high erosion), very wet, low ground; mangroves when it is also hot
        boolean swampy = hum >= 3 && e > 0.25 && elev < 12.0;
        if (swampy && band >= 4) {
            b = BiomeRole.MANGROVE_SWAMP;
        }
        if (swampy && band <= 2) {
            b = BiomeRole.SWAMP;
        }
        if (swampy && band == 3) {
            b = BiomeRole.SWAMP;
        }
        // mesas / badlands: hot + arid + terraced high ground
        if (band >= 4 && hum <= 1 && elev > 24.0) {
            b = BiomeRole.BADLANDS_MESA;
        }
        // high ground wins over climate below the snowline
        if (Mathx.smoothstep(40.0, 90.0, elev) > 0.5) {
            b = BiomeRole.TEMPERATE_MOUNTAINS;
            if (band >= 4) {
                b = BiomeRole.WARM_TEMPERATE_MOUNTAINS;
            }
            if (band == 0) {
                b = BiomeRole.COLD_MOUNTAINS;
            }
            if ((band == 1 || band == 2) && hum <= 2) {
                b = BiomeRole.ARID_MOUNTAINS;
            }
            if (band == 3) {
                b = BiomeRole.TEMPERATE_MOUNTAINS;
            }
        }
        // snowline and ice: an altitude-driven cold cap independent of latitude
        double snowline = sea + 190.0 - tU * 60.0;
        if (hs > snowline) {
            b = band <= 1 ? BiomeRole.GLACIER : BiomeRole.ALPINE_PEAKS;
        }
        if (Mathx.smoothstep(90.0, 160.0, elev) > 0.5 && hs > snowline * 1.06) {
            b = BiomeRole.ALPINE_PEAKS;
        }
        return b;
    }

    // ---------------------------------------------------------------------------------------
    // climate space: every role's warmth and moisture, for the seam buffers
    // ---------------------------------------------------------------------------------------

    /** How far two roles are apart in climate: 0 is the same place, about 1.2 is the full span. */
    static double contrast(BiomeRole a, BiomeRole b) {
        return Math.abs(warmth(a) - warmth(b)) + Math.abs(moisture(a) - moisture(b));
    }

    /** The role nearest the climate midpoint of two roles: the buffer between two extremes. */
    static BiomeRole intermediate(BiomeRole a, BiomeRole b) {
        double tw = 0.5 * (warmth(a) + warmth(b));
        double tm = 0.5 * (moisture(a) + moisture(b));
        BiomeRole best = null;
        double bestD = 1.0E9;
        for (BiomeRole r : BiomeRole.values()) {
            if (r == a || r == b) {
                continue;
            }
            double d = Math.abs(warmth(r) - tw) + Math.abs(moisture(r) - tm);
            if (d < bestD) {
                bestD = d;
                best = r;
            }
        }
        return best;
    }

    /** Warmth of a role's home climate, 0 = frozen, 1 = torrid. */
    static double warmth(BiomeRole r) {
        switch (r) {
            case GLACIER:
                return 0.02;
            case FROZEN_PEAKS:
                return 0.05;
            case DEEP_FROZEN_OCEAN:
            case FROZEN_OCEAN:
            case SNOWY_PLAINS:
                return 0.08;
            case JAGGED_PEAKS:
            case SNOWY_BEACH:
            case FROZEN_RIVER:
            case SNOWY_TAIGA:
                return 0.13;
            case DEEP_COLD_OCEAN:
            case COLD_OCEAN:
            case ALPINE_PEAKS:
                return 0.17;
            case GROVE:
            case COLD_SHRUBLAND:
                return 0.24;
            case DEEP_OCEAN:
            case OCEAN:
            case COLD_MOUNTAINS:
                return 0.28;
            case TAIGA:
                return 0.32;
            case OLD_GROWTH_TAIGA:
                return 0.38;
            case HIGHLAND_STEPPE:
            case WINDSWEPT_HILLS:
            case STONY_SHORE:
            case TEMPERATE_MOUNTAINS:
                return 0.44;
            case PLAINS:
            case MEADOW:
            case RIVER:
            case LAKE:
            case TEMPERATE_FOREST:
            case CHERRY_GROVE:
            case BEACH:
            case DEEP_LUKEWARM_OCEAN:
            case LUKEWARM_OCEAN:
                return 0.52;
            case OLD_GROWTH_TEMPERATE_FOREST:
            case FERTILE_VALLEY:
            case SHALLOW_COAST:
                return 0.58;
            case SWAMP:
            case XERIC_SHRUBLAND:
            case WARM_TEMPERATE_MOUNTAINS:
                return 0.64;
            case MANGROVE_SWAMP:
            case ARID_MOUNTAINS:
            case SALT_FLATS:
                return 0.72;
            case BASALT_DELTAS:
            case VOLCANIC_HIGHLAND:
            case BADLANDS:
            case ERODED_BADLANDS:
            case WOODED_BADLANDS:
            case BADLANDS_MESA:
            case SAVANNA:
            case HUMID_SAVANNA:
            case WARM_OCEAN:
                return 0.80;
            case DESERT:
            case SPARSE_JUNGLE:
                return 0.86;
            case JUNGLE:
                return 0.91;
            case TROPICAL_RAINFOREST:
                return 0.95;
            case LUSH_CAVES:
                return 0.55;
            case DRIPSTONE_CAVES:
                return 0.50;
            case DEEP_DARK:
            default:
                return 0.45;
        }
    }

    /** Moisture of a role's home climate, 0 = arid, 1 = soaked. */
    static double moisture(BiomeRole r) {
        switch (r) {
            case DESERT:
            case SALT_FLATS:
                return 0.06;
            case BADLANDS:
            case ERODED_BADLANDS:
            case BADLANDS_MESA:
                return 0.12;
            case XERIC_SHRUBLAND:
            case WOODED_BADLANDS:
            case ARID_MOUNTAINS:
                return 0.22;
            case SAVANNA:
            case BASALT_DELTAS:
            case VOLCANIC_HIGHLAND:
            case HIGHLAND_STEPPE:
                return 0.32;
            case STONY_SHORE:
            case HUMID_SAVANNA:
            case WARM_TEMPERATE_MOUNTAINS:
            case TEMPERATE_MOUNTAINS:
                return 0.42;
            case PLAINS:
            case MEADOW:
            case BEACH:
            case SNOWY_BEACH:
            case WINDSWEPT_HILLS:
            case COLD_SHRUBLAND:
            case SPARSE_JUNGLE:
                return 0.48;
            case SNOWY_PLAINS:
            case GROVE:
            case CHERRY_GROVE:
            case FERTILE_VALLEY:
            case TEMPERATE_FOREST:
            case RIVER:
            case LAKE:
            case SHALLOW_COAST:
            case GLACIER:
                return 0.56;
            case TAIGA:
            case SNOWY_TAIGA:
            case OLD_GROWTH_TEMPERATE_FOREST:
            case JUNGLE:
            case COLD_MOUNTAINS:
            case JAGGED_PEAKS:
            case FROZEN_PEAKS:
            case ALPINE_PEAKS:
                return 0.64;
            case SWAMP:
            case MANGROVE_SWAMP:
            case OLD_GROWTH_TAIGA:
            case TROPICAL_RAINFOREST:
                return 0.82;
            case OCEAN:
            case DEEP_OCEAN:
            case COLD_OCEAN:
            case DEEP_COLD_OCEAN:
            case WARM_OCEAN:
            case LUKEWARM_OCEAN:
            case DEEP_LUKEWARM_OCEAN:
            case FROZEN_OCEAN:
            case DEEP_FROZEN_OCEAN:
            case FROZEN_RIVER:
                return 0.62;
            case LUSH_CAVES:
                return 0.85;
            case DRIPSTONE_CAVES:
                return 0.35;
            case DEEP_DARK:
            default:
                return 0.45;
        }
    }
}
