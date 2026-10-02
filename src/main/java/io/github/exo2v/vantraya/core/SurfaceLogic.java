package io.github.exo2v.vantraya.core;

import io.github.exo2v.vantraya.core.Spec.Kind;

/**
 * The specification's slope-aware surface rule (spec section 5, method 3; HANDOFF section 3.4) as a pure
 * decision: given what is known about a column it says which material tops it, which fills the layers
 * below, and how thick that soil is.
 *
 * <pre>
 *   slope &lt; 25 deg    biome topsoil (grass, podzol, sand ...)              100 % density
 *   25 - 35 deg       coarse dirt / podzol                                 40 % density
 *   35 - 45 deg       cobblestone, stone scree, gravel                      5 % density, no trees
 *   &gt; 45 deg          granite / basalt bare rock                            0 %
 *   Y &gt; 225          snow block (frozen peaks) / calcite, packed-ice fill  treeline, 0 %
 *   caldera           blackstone, basalt, magma block, obsidian             barren, 0 %
 * </pre>
 *
 * Trees, logs and shrubs need a dirt-like block under them, so stripping the soil is exactly how the
 * specification keeps vegetation (from vanilla, Still Life or any other mod) off cliffs, summits and the
 * volcano: no tree decorator has to be taught about slopes. The density columns are realised with a
 * blue-noise dither, so a "40 %" band keeps soil on an evenly spread 40 % of its columns rather than in
 * clumps.
 *
 * <p>A {@code null} material means "leave what the biome's own surface rule produced".
 */
public final class SurfaceLogic {
    private SurfaceLogic() {
    }

    /** Blocks the surface pass can place; ids are in the {@code minecraft} namespace. */
    public enum Mat {
        GRASS_BLOCK("grass_block"), DIRT("dirt"), COARSE_DIRT("coarse_dirt"), PODZOL("podzol"),
        MUD("mud"), MOSS_BLOCK("moss_block"), STONE("stone"), COBBLESTONE("cobblestone"),
        MOSSY_COBBLESTONE("mossy_cobblestone"), GRAVEL("gravel"), GRANITE("granite"), ANDESITE("andesite"),
        BASALT("basalt"), BLACKSTONE("blackstone"), MAGMA_BLOCK("magma_block"), OBSIDIAN("obsidian"),
        CALCITE("calcite"), PACKED_ICE("packed_ice"), SNOW_BLOCK("snow_block"),
        RED_SAND("red_sand"), RED_SANDSTONE("red_sandstone"), TERRACOTTA("terracotta"),
        ORANGE_TERRACOTTA("orange_terracotta"), YELLOW_TERRACOTTA("yellow_terracotta"),
        BLACK_GLAZED_TERRACOTTA("black_glazed_terracotta");

        private final String path;

        Mat(String path) {
            this.path = path;
        }

        public String path() {
            return path;
        }

        public String id() {
            return "minecraft:" + path;
        }
    }

    /** Everything the decision depends on. {@code dither} and {@code rnd} are uniform in {@code [0, 1)}. */
    public record Column(
            double slopeDeg, int groundY, boolean wet,
            int landmarkId, BiomeRole biome, boolean lava,
            double dither, double rnd, double rnd2) {
    }

    /** The outcome; {@code top}/{@code filler} may be null (keep the biome's own surface). */
    public record Choice(Mat top, Mat filler, int soilDepth) {
        public static final Choice KEEP = new Choice(null, null, 0);

        public boolean isKeep() {
            return top == null && filler == null;
        }
    }

    /** True for blocks a tree, log or shrub can root in (the vanilla {@code #dirt} family). */
    public static boolean isDirtLike(Mat m) {
        return m == Mat.GRASS_BLOCK || m == Mat.DIRT || m == Mat.COARSE_DIRT || m == Mat.PODZOL
                || m == Mat.MUD || m == Mat.MOSS_BLOCK;
    }

    /** Share of 25-35 degree columns that keep soil (the spec's "40 % density"). */
    public static final double DIRT_BAND_DENSITY = 0.40;
    /** Share of 35-45 degree columns that keep a mossy boulder/lichen patch ("5 % density"). */
    public static final double SCREE_BAND_DENSITY = 0.05;

    /** Slope in degrees from the L1 gradient of the height field, as the offline engine measures it. */
    public static double slopeDegrees(double dhdx, double dhdz) {
        return Math.toDegrees(Math.atan(Math.abs(dhdx) + Math.abs(dhdz)));
    }

    public static Choice choose(Column c) {
        Kind kind = c.landmarkId() >= 0 && c.landmarkId() < Spec.LANDMARKS.size()
                ? Spec.LANDMARKS.get(c.landmarkId()).kind() : null;
        boolean veil = c.landmarkId() == Spec.VEIL_ID;
        int soil = 2 + (int) (c.rnd2() * 3.0);

        if (c.wet()) {
            if (veil) { // white salt crust over abyssal gravel
                return new Choice(c.rnd() > 0.55 ? Mat.CALCITE : Mat.GRAVEL, Mat.GRAVEL, soil);
            }
            return Choice.KEEP;
        }

        Mat top = null;
        Mat filler = null;
        double deg = c.slopeDeg();

        // ---- landmark palettes on gentle ground (spec section 3.2, "Specified surfaces") ----
        if (deg < Spec.SLOPE_SOIL_MAX && kind != null) {
            switch (kind) {
                case CALDERA:
                    top = c.rnd() < 0.35 ? Mat.BLACKSTONE : Mat.BASALT;
                    filler = Mat.BLACKSTONE;
                    break;
                case QUARRY: { // concentric 9 m benches, one band of rock per tread
                    int bench = Math.floorMod((int) Math.floor(c.groundY() / 9.0), 4);
                    Mat[] bands = {Mat.ORANGE_TERRACOTTA, Mat.YELLOW_TERRACOTTA, Mat.ANDESITE, Mat.STONE};
                    top = bands[bench];
                    filler = Mat.STONE;
                    break;
                }
                case DUNES:
                    top = c.groundY() > 88 ? (c.rnd() < 0.5 ? Mat.TERRACOTTA : Mat.RED_SAND) : Mat.RED_SAND;
                    filler = Mat.RED_SANDSTONE;
                    break;
                case FEN:
                    if (c.rnd() < 0.30) {
                        top = Mat.MUD;
                        filler = Mat.MUD;
                    } else if (c.rnd() < 0.50) {
                        top = Mat.COARSE_DIRT;
                        filler = Mat.DIRT;
                    } else if (c.rnd() < 0.62) {
                        top = Mat.MOSS_BLOCK;
                        filler = Mat.DIRT;
                    }
                    break;
                case SPIRES:
                    if (c.groundY() > 150) {
                        double r = c.rnd();
                        top = r < 0.40 ? Mat.GRANITE : (r < 0.80 ? Mat.STONE : Mat.COBBLESTONE);
                        filler = Mat.STONE;
                    }
                    break;
                case COAST:
                    if (c.rnd() < 0.12) {
                        top = Mat.PODZOL;
                        filler = Mat.DIRT;
                    }
                    break;
                default:
                    break;
            }
        }

        // ---- the slope table ----------------------------------------------------------------
        if (deg >= Spec.SLOPE_SOIL_MAX && deg < Spec.SLOPE_DIRT_MAX) {
            if (c.dither() < DIRT_BAND_DENSITY) {
                top = c.rnd() < 0.35 ? Mat.PODZOL : Mat.COARSE_DIRT;
                filler = Mat.COARSE_DIRT;
            } else {
                top = c.rnd() < 0.5 ? Mat.STONE : (c.rnd2() < 0.5 ? Mat.GRAVEL : Mat.ANDESITE);
                filler = Mat.STONE;
            }
        } else if (deg >= Spec.SLOPE_DIRT_MAX && deg <= Spec.SLOPE_SCREE_MAX) {
            double scree = Mathx.saturate((deg - Spec.SLOPE_DIRT_MAX) / 12.0);
            if (c.dither() < SCREE_BAND_DENSITY) {
                top = Mat.MOSSY_COBBLESTONE; // mossy boulders, clinging lichen (not dirt: no trees)
            } else if (c.rnd() < scree) {
                top = c.rnd2() < 0.5 ? Mat.GRAVEL : Mat.COBBLESTONE;
            } else {
                top = Mat.STONE;
            }
            filler = Mat.STONE;
        } else if (deg > Spec.SLOPE_SCREE_MAX) {
            top = c.biome() == BiomeRole.BASALT_DELTAS ? Mat.BASALT : Mat.GRANITE; // sheer cliffs: bare rock
            filler = Mat.STONE;
        }

        // ---- treeline: climatic cutoff -------------------------------------------------------
        if (c.groundY() > Spec.TREELINE) {
            top = c.biome() == BiomeRole.FROZEN_PEAKS ? Mat.SNOW_BLOCK : Mat.CALCITE;
            filler = Mat.PACKED_ICE;
        }

        // ---- volcanic barrenness overrides every foliage rule --------------------------------
        if (c.lava()) {
            top = Mat.MAGMA_BLOCK;
            filler = Mat.MAGMA_BLOCK;
        }
        if (kind == Kind.CALDERA) {
            if (top == null || isDirtLike(top)) { // volcanic barrenness: not a block of soil anywhere in the caldera
                top = c.rnd() < 0.35 ? Mat.BLACKSTONE : Mat.BASALT;
            }
            if (filler == null || isDirtLike(filler)) {
                filler = Mat.BLACKSTONE;
            }
            if (c.groundY() > Spec.CALDERA_FLOOR + 22.0) {
                top = deg > 30.0 ? Mat.BLACKSTONE : Mat.BASALT;
                filler = Mat.BLACKSTONE;
            }
            if (c.groundY() > Spec.CALDERA_THRONE - 6.0 && c.groundY() < Spec.CALDERA_THRONE + 30.0) {
                top = Mat.OBSIDIAN; // the Obsidian Throne and the glassy shoulders below the rim
                filler = Mat.OBSIDIAN;
            }
            if (c.lava()) {
                top = Mat.MAGMA_BLOCK;
                filler = Mat.MAGMA_BLOCK;
            }
        }
        // volcanic glass on the dune crests ("vitrified black-glass dunes")
        if (kind == Kind.DUNES && c.groundY() > 89) {
            top = Mat.BLACK_GLAZED_TERRACOTTA;
        }
        if (top == null && filler == null) {
            return Choice.KEEP;
        }
        return new Choice(top, filler, soil);
    }
}
