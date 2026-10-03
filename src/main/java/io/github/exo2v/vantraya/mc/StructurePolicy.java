package io.github.exo2v.vantraya.mc;

import io.github.exo2v.vantraya.core.Spec;
import io.github.exo2v.vantraya.core.VantrayaModel;
import net.minecraft.core.Holder;
import net.minecraft.core.HolderSet;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.tags.TagKey;
import net.minecraft.world.level.biome.Biome;
import net.minecraft.world.level.levelgen.structure.Structure;

/**
 * Which structures may generate in a Vantraya world, and which may not.
 *
 * <p>Vanilla gates each structure on a biome tag ({@code #minecraft:has_structure/...}), and those tags are
 * global: {@code nether_fortress} is {@code #minecraft:is_nether}, and the specification's Ashen Caldera is
 * <em>basalt deltas</em> - an {@code is_nether} biome - so the first play test found a nether fortress standing
 * in a river at the volcano's feet. The same rule put ocean structures into inland water wherever the classifier
 * called a flooded valley an ocean.
 *
 * <p>Two rules fix that for a Vantraya world only (the real Nether, and every other world type, are untouched;
 * this policy runs from {@link VantrayaChunkGenerator} and nowhere else):
 * <ol>
 *   <li>a structure whose biomes are all Nether biomes never generates here - the Nether is the place for it;</li>
 *   <li>a structure centred in the model's river, lake or inland water does not generate there, unless vanilla
 *       puts it in or on water (ruined portals in rivers, shipwrecks, ocean ruins, monuments, buried treasure).</li>
 * </ol>
 */
public final class StructurePolicy {
    private StructurePolicy() {
    }

    private static final TagKey<Biome> IS_NETHER = tag("is_nether");
    private static final TagKey<Biome> IS_RIVER = tag("is_river");
    private static final TagKey<Biome> IS_OCEAN = tag("is_ocean");
    private static final TagKey<Biome> IS_DEEP_OCEAN = tag("is_deep_ocean");
    private static final TagKey<Biome> IS_BEACH = tag("is_beach");

    private static TagKey<Biome> tag(String path) {
        return TagKey.create(Registries.BIOME, ResourceLocation.withDefaultNamespace(path));
    }

    /** True when every biome of the structure is a Nether biome (fortresses, nether portals, nether fossils...). */
    public static boolean isNetherOnly(HolderSet<Biome> biomes) {
        boolean any = false;
        for (Holder<Biome> b : biomes) {
            any = true;
            if (!b.is(IS_NETHER)) {
                return false;
            }
        }
        return any;
    }

    /** True when vanilla itself places this structure in or on water. */
    public static boolean isWaterLegal(HolderSet<Biome> biomes) {
        for (Holder<Biome> b : biomes) {
            if (b.is(IS_RIVER) || b.is(IS_OCEAN) || b.is(IS_DEEP_OCEAN) || b.is(IS_BEACH)) {
                return true;
            }
        }
        return false;
    }

    /**
     * True when this column is probably water in the finished world: the ocean bands of the continents
     * map, or a river valley (the ridges map's valley band, where the offset spline carves a channel and
     * the aquifer fills it) low enough that the fill reaches it.
     */
    public static boolean isWetSite(VantrayaModel.Fields f) {
        return f.cont() < -0.19                      // ocean, coast shallows, the Sunken Reach's shelf
                || (f.valley() && f.cont() < 0.30); // a carved river valley in the low or mid country
    }

    /** May this structure not generate at this site? */
    public static boolean drop(Structure structure, VantrayaModel.Fields site) {
        HolderSet<Biome> biomes = structure.biomes();
        return isNetherOnly(biomes) || (isWetSite(site) && !isWaterLegal(biomes));
    }
}
