package io.github.exo2v.vantraya.mc;

import java.util.Map;

import io.github.exo2v.vantraya.VantrayaBuilder;
import net.minecraft.core.MappedRegistry;
import net.minecraft.core.Registry;
import net.minecraft.core.RegistrationInfo;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.ResourceKey;
import net.minecraft.world.level.dimension.LevelStem;

/**
 * Keeps the Vantraya world type's overworld when a data pack ships a {@code minecraft:overworld} dimension of its own.
 *
 * <p>Minecraft builds the dimensions of a world from two sources: the world type the player picked, and the
 * {@code data/<ns>/dimension/*.json} files of the enabled data packs and mods. Where both define the same
 * dimension, <em>the data pack wins</em> and the world type "then doesn't have an impact on the given dimension"
 * (Minecraft Wiki, "Dimension definition"). Packs that replace the whole overworld - Still Life and Terralith are
 * examples, and TerraFirmaCraft documents exactly this - therefore switch every other world type off silently:
 * the player picks <em>Vantraya</em>, and gets that pack's overworld.
 *
 * <p>This is the one rule of the merge that Vantraya Builder changes, and only for its own overworld
 * ({@code WorldDimensionsMixin} calls it): when the overworld the player chose is Vantraya's and the data packs
 * define a different one, the packs' overworld is left out of the merge. Every other world type, the Nether and
 * the End are untouched, so Lithosphere, Still Life and the rest keep generating the worlds that were not created
 * with the Vantraya world type. A pack that ships a <em>Vantraya</em> overworld of its own (a deliberate
 * customisation) is left to win, as before.
 *
 * <p>The merge runs for a new world and again every time the world is loaded, with the dimensions saved in
 * {@code level.dat} as "the chosen ones", so the guard holds for the life of the world.
 */
public final class WorldTypePriority {
    private WorldTypePriority() {
    }

    /**
     * @param chosen        the dimensions of the world: from the world type, or decoded from {@code level.dat}
     * @param fromDataPacks the level stems that data packs define ({@code Registries.LEVEL_STEM})
     * @return {@code fromDataPacks} itself, or a copy of it without the overworld
     */
    public static Registry<LevelStem> keepChosenOverworld(Map<ResourceKey<LevelStem>, LevelStem> chosen, Registry<LevelStem> fromDataPacks) {
        LevelStem mine = chosen.get(LevelStem.OVERWORLD);
        if (mine == null || !(mine.generator() instanceof VantrayaChunkGenerator)) {
            return fromDataPacks; // not a Vantraya world: vanilla's rule, whatever it gives
        }
        LevelStem theirs = fromDataPacks.get(LevelStem.OVERWORLD);
        if (theirs == null || theirs.generator() instanceof VantrayaChunkGenerator) {
            return fromDataPacks; // nothing to override, or a pack that deliberately configures Vantraya itself
        }
        VantrayaBuilder.LOGGER.info("Vantraya: a data pack defines its own minecraft:overworld ({} generator) which would replace "
                + "the Vantraya world type; keeping the Vantraya overworld", theirs.generator().getClass().getSimpleName());
        MappedRegistry<LevelStem> without = new MappedRegistry<>(Registries.LEVEL_STEM, fromDataPacks.registryLifecycle());
        for (Map.Entry<ResourceKey<LevelStem>, LevelStem> entry : fromDataPacks.entrySet()) {
            if (!entry.getKey().equals(LevelStem.OVERWORLD)) {
                without.register(entry.getKey(), entry.getValue(),
                        fromDataPacks.registrationInfo(entry.getKey()).orElse(RegistrationInfo.BUILT_IN));
            }
        }
        return without.freeze();
    }
}
