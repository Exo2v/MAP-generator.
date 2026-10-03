package io.github.exo2v.vantraya.mc;

import net.neoforged.neoforge.common.ModConfigSpec;

/** {@code config/vantraya_builder-common.toml}. Every option has a sensible default; none is required. */
public final class VantrayaConfig {
    private VantrayaConfig() {
    }

    private static final ModConfigSpec.Builder BUILDER = new ModConfigSpec.Builder();

    public static final ModConfigSpec.BooleanValue CANONICAL_WORLD = BUILDER
            .comment("false: the world seed drives the texture of the continent (coastline wiggle, relief, where the",
                    "      Glacial Spine's peaks stand) while every landmark stays exactly where the specification puts it.",
                    "true : ignore the world seed and always build the shipped Ashenfall continent (seed 20250929) -",
                    "       the same Vantyra in every world.")
            .define("canonicalWorld", false);

    public static final ModConfigSpec.BooleanValue SPAWN_AT_FORGOTTEN_COAST = BUILDER
            .comment("Start new Vantraya worlds at the Forgotten Coast (0, 68, 2500), the specification's spawn.")
            .define("spawnAtForgottenCoast", true);

    public static final ModConfigSpec.BooleanValue PAINT_SURFACE = BUILDER
            .comment("Apply the specification's slope-aware surface table (soil / scree / bare rock / treeline /",
                    "caldera / Veil of Salt crust) after the biome surface rules. Turn off to keep pure biome surfaces.")
            .define("paintSurface", true);

    public static final ModConfigSpec.BooleanValue KEEP_CALDERA_DRY = BUILDER
            .comment("No water inside the Ashen Caldera: open water in the crater is removed and the crater floor",
                    "gets its lava basins (specification: \"absolutely no water\").")
            .define("keepCalderaDry", true);

    public static final ModConfigSpec.BooleanValue ENFORCE_WORLD_BORDER = BUILDER
            .comment("Set an 8,000-block world border centred on the origin when a Vantraya world is first started,",
                    "matching the specification's finite canvas. Beyond it the Veil of Salt abyss continues.")
            .define("enforceWorldBorder", false);

    public static final ModConfigSpec.BooleanValue LOG_COMPAT_REPORT = BUILDER
            .comment("Log which worldgen mods and biome contributions Vantraya found when a server starts.")
            .define("logCompatReport", true);

    public static final ModConfigSpec.BooleanValue FILL_RIVERS = BUILDER
            .comment("Fill the specification's river and lake channels with water (and ice where it is cold) so the",
                    "continent can be travelled by water. Turn off for dry valleys.")
            .define("fillRivers", true);

    public static final ModConfigSpec.BooleanValue STRUCTURE_POLICY = BUILDER
            .comment("Keep structures where they belong in a Vantraya world: no Nether structures (the Ashen Caldera",
                    "is basalt deltas, and vanilla's fortress tag includes it), and no structures standing in the",
                    "middle of a river or lake unless vanilla puts them in water (ruined portals, shipwrecks).")
            .define("structurePolicy", true);

    public static final ModConfigSpec.BooleanValue PRESELECT_WORLD_TYPE = BUILDER
            .comment("Client side: open the Create New World screen with the Vantraya world type already selected,",
                    "instead of \"Default\". It can still be switched back there. Meant for a modpack built around this world.")
            .define("preselectWorldType", false);

    public static final ModConfigSpec SPEC = BUILDER.build();

    private static boolean read(ModConfigSpec.BooleanValue v, boolean fallback) {
        try {
            return v.get();
        } catch (RuntimeException e) { // config not loaded yet (datagen, early world creation preview)
            return fallback;
        }
    }

    public static boolean canonicalWorld() {
        return read(CANONICAL_WORLD, false);
    }

    public static boolean spawnAtForgottenCoast() {
        return read(SPAWN_AT_FORGOTTEN_COAST, true);
    }

    public static boolean paintSurface() {
        return read(PAINT_SURFACE, true);
    }

    public static boolean keepCalderaDry() {
        return read(KEEP_CALDERA_DRY, true);
    }

    public static boolean enforceWorldBorder() {
        return read(ENFORCE_WORLD_BORDER, false);
    }

    public static boolean logCompatReport() {
        return read(LOG_COMPAT_REPORT, true);
    }

    public static boolean preselectWorldType() {
        return read(PRESELECT_WORLD_TYPE, false);
    }

    public static boolean fillRivers() {
        return read(FILL_RIVERS, true);
    }

    public static boolean structurePolicy() {
        return read(STRUCTURE_POLICY, true);
    }
}
