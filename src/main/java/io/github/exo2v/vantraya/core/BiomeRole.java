package io.github.exo2v.vantraya.core;

/**
 * A biome <em>role</em>: the offline engine's internal biome vocabulary (a Still-Life-flavoured list
 * such as {@code temperate_forest} or {@code xeric_shrubland}) together with the vanilla biome each
 * role resolves to by default - the same mapping the exported world used ({@code BIOME_TO_VANILLA}).
 *
 * <p>At runtime the Minecraft layer looks every role up through the biome tag
 * {@code #vantraya_builder:biome/<id>}. Out of the box a tag holds only the vanilla default, so a Vantraya
 * world uses vanilla biomes exactly like the exported world. Any datapack or compatibility pack can
 * <em>add</em> biomes from other mods (Still Life, Biomes O' Plenty, ...) to a tag, and Vantraya will
 * spread them over the places that role covers.
 */
public enum BiomeRole {
    DEEP_OCEAN("deep_ocean", "deep_ocean"),
    OCEAN("ocean", "ocean"),
    SHALLOW_COAST("shallow_coast", "warm_ocean"),
    BEACH("beach", "beach"),
    STONY_SHORE("stony_shore", "stony_shore"),
    RIVER("river", "river"),
    LAKE("lake", "river"),
    FROZEN_RIVER("frozen_river", "frozen_river"),
    SNOWY_BEACH("snowy_beach", "snowy_beach"),
    SNOWY_PLAINS("snowy_plains", "snowy_plains"),
    SNOWY_TAIGA("snowy_taiga", "snowy_taiga"),
    GROVE("grove", "grove"),
    TAIGA("taiga", "taiga"),
    OLD_GROWTH_TAIGA("old_growth_taiga", "old_growth_pine_taiga"),
    COLD_MOUNTAINS("cold_mountains", "snowy_slopes"),
    COLD_SHRUBLAND("cold_shrubland", "windswept_gravelly_hills"),
    PLAINS("plains", "plains"),
    MEADOW("meadow", "meadow"),
    TEMPERATE_FOREST("temperate_forest", "forest"),
    OLD_GROWTH_TEMPERATE_FOREST("old_growth_temperate_forest", "dark_forest"),
    TEMPERATE_MOUNTAINS("temperate_mountains", "windswept_hills"),
    WARM_TEMPERATE_MOUNTAINS("warm_temperate_mountains", "windswept_savanna"),
    SWAMP("swamp", "swamp"),
    MANGROVE_SWAMP("mangrove_swamp", "mangrove_swamp"),
    HUMID_SAVANNA("humid_savanna", "savanna"),
    SAVANNA("savanna", "savanna"),
    XERIC_SHRUBLAND("xeric_shrubland", "savanna_plateau"),
    DESERT("desert", "desert"),
    ARID_MOUNTAINS("arid_mountains", "badlands"),
    BADLANDS_MESA("badlands_mesa", "badlands"),
    JUNGLE("jungle", "jungle"),
    TROPICAL_RAINFOREST("tropical_rainforest", "sparse_jungle"),
    SPARSE_JUNGLE("sparse_jungle", "sparse_jungle"),
    HIGHLAND_STEPPE("highland_steppe", "windswept_hills"),
    ALPINE_PEAKS("alpine_peaks", "frozen_peaks"),
    GLACIER("glacier", "snowy_plains"),
    VOLCANIC_HIGHLAND("volcanic_highland", "basalt_deltas"),
    SALT_FLATS("salt_flats", "desert"),
    WINDSWEPT_HILLS("windswept_hills", "windswept_hills"),
    FERTILE_VALLEY("fertile_valley", "sunflower_plains"),
    // Vanilla biomes the specification names directly for its regions
    WOODED_BADLANDS("wooded_badlands", "wooded_badlands"),
    ERODED_BADLANDS("eroded_badlands", "eroded_badlands"),
    BADLANDS("badlands", "badlands"),
    BASALT_DELTAS("basalt_deltas", "basalt_deltas"),
    FROZEN_PEAKS("frozen_peaks", "frozen_peaks"),
    JAGGED_PEAKS("jagged_peaks", "jagged_peaks"),
    CHERRY_GROVE("cherry_grove", "cherry_grove"),
    WARM_OCEAN("warm_ocean", "warm_ocean"),
    LUKEWARM_OCEAN("lukewarm_ocean", "lukewarm_ocean"),
    DEEP_COLD_OCEAN("deep_cold_ocean", "deep_cold_ocean"),
    // Runtime additions: ocean variants by temperature and the three vanilla cave biomes
    COLD_OCEAN("cold_ocean", "cold_ocean"),
    FROZEN_OCEAN("frozen_ocean", "frozen_ocean"),
    DEEP_LUKEWARM_OCEAN("deep_lukewarm_ocean", "deep_lukewarm_ocean"),
    DEEP_FROZEN_OCEAN("deep_frozen_ocean", "deep_frozen_ocean"),
    LUSH_CAVES("lush_caves", "lush_caves"),
    DRIPSTONE_CAVES("dripstone_caves", "dripstone_caves"),
    DEEP_DARK("deep_dark", "deep_dark");

    private final String id;
    private final String vanilla;

    BiomeRole(String id, String vanilla) {
        this.id = id;
        this.vanilla = vanilla;
    }

    /** The role's name; also the path of its tag, {@code vantraya_builder:biome/<id>}. */
    public String id() {
        return id;
    }

    /** Path (in the {@code minecraft} namespace) of the vanilla biome this role defaults to. */
    public String vanilla() {
        return vanilla;
    }

    /** Resolve a specification biome name ({@code "plains"}, {@code "frozen_peaks"} ...) to its role. */
    public static BiomeRole ofSpecName(String name) {
        for (BiomeRole r : values()) {
            if (r.id.equals(name)) {
                return r;
            }
        }
        throw new IllegalArgumentException("unknown biome " + name);
    }
}
