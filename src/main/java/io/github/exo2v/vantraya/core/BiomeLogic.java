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

    /** Size of the patches in which the two sides of a decision edge alternate. */
    private static final int BLEND_PATCH = 64;

    /** How far around a point the region seams are probed: the width of the blended border band. */
    private static final double[][] SEAM_PROBES = {{12, 0}, {-12, 0}, {0, 12}, {0, -12}};

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
     * The surface decision, optionally blended (the outer {@link #surfaceRole} blends; the seam probes below
     * ask their neighbours without blending).
     *
     * <p>Blending: the specification's classifier is a set of thresholds, and a threshold is a wall - the
     * first play test found "very stark borders" where two contrasting biomes met. So the thresholds are
     * wobbled by a little coherent noise (the border wanders) and within reach of a border the two sides
     * alternate in patches (the border is mottled). Landmark centres are exempt: {@link Spec#protection}
     * stills the wobble there, so every pinned elevation and biome stays exactly as specified.
     */
    private static BiomeRole surfaceRole(double x, double z, double hs, double t, double h, double c, double e,
                                         boolean blend) {
        double jitter = 1.0 - Spec.protection(x, z);
        double tU = Mathx.saturate((t + 1.0) * 0.5) + 0.07 * wobble(x, z, 0xB1) * jitter;
        double hU = Mathx.saturate((h + 1.0) * 0.5) + 0.07 * wobble(x, z, 0xB2) * jitter;
        double hsJ = hs + 5.0 * wobble(x, z, 0xB3) * jitter;
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
            return patchTakes(x, z, 7) ? BiomeRole.DEEP_OCEAN : BiomeRole.DEEP_COLD_OCEAN;
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
        // near a decision edge, the role of the other side alternates in patches (mottled borders)
        BiomeRole[] near = {
                decide(hsJ + 8.0, tU, hU, e, kind),
                decide(hsJ - 8.0, tU, hU, e, kind),
                decide(hsJ, tU + 0.07, hU, e, kind),
                decide(hsJ, tU - 0.07, hU, e, kind),
                decide(hsJ, tU, hU + 0.07, e, kind),
                decide(hsJ, tU, hU - 0.07, e, kind)};
        for (int i = 0; i < near.length; i++) {
            if (near[i] != role && patchTakes(x, z, i)) {
                role = near[i];
                break;
            }
        }
        // at a landmark's border, the two sides alternate in patches too (no more walls between regions)
        for (int i = 0; i < SEAM_PROBES.length; i++) {
            double px = x + SEAM_PROBES[i][0];
            double pz = z + SEAM_PROBES[i][1];
            if (Regions.idAt(px, pz) != id) {
                BiomeRole other = surfaceRole(px, pz, hs, t, h, c, e, false);
                if (other != role && patchTakes(x, z, 20 + i)) {
                    role = other;
                    break;
                }
            }
        }
        return role;
    }

    /** The role of a place before blending: the landmark's palette or the Whittaker grid. */
    private static BiomeRole decide(double hs, double tU, double hU, double e, Kind kind) {
        return kind != null ? landmarkRole(kind, hs) : generic(hs, tU, hU, e);
    }

    /** Two-octave value noise in {@code -1..1}: coherent wobble for the decision thresholds. */
    private static double wobble(double x, double z, int salt) {
        return 2.0 * (0.65 * Noise.value(x / 70.0, z / 70.0, salt)
                + 0.35 * Noise.value(x / 23.0, z / 23.0, salt * 31)) - 1.0;
    }

    /** Patches of {@link #BLEND_PATCH} blocks: true when this patch takes the neighbouring role. */
    private static boolean patchTakes(double x, double z, int salt) {
        long px = Math.floorDiv((long) Math.floor(x), BLEND_PATCH);
        long pz = Math.floorDiv((long) Math.floor(z), BLEND_PATCH);
        return Noise.hash2(px, pz, 0x51EED000 + salt) < 0.5;
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
}
