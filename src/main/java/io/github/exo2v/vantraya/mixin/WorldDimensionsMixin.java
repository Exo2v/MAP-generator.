package io.github.exo2v.vantraya.mixin;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.ModifyVariable;

import io.github.exo2v.vantraya.mc.WorldTypePriority;
import net.minecraft.core.Registry;
import net.minecraft.world.level.dimension.LevelStem;
import net.minecraft.world.level.levelgen.WorldDimensions;

/**
 * The only hook into Minecraft that Vantraya Builder needs, and the smallest one that does the job.
 *
 * <p>{@code WorldDimensions.bake} merges a world's dimensions with those that data packs define, and a data pack's
 * dimension wins over the world type the player picked. {@link WorldTypePriority} decides, from the chosen
 * dimensions, whether the packs' {@code minecraft:overworld} must stay out of the merge. This mixin only hands it
 * the argument; the rest of {@code bake} is vanilla's. {@code bake} runs when a world is created and again whenever
 * it is loaded, so one injection covers both, and the dedicated server's {@code level-type} as well.
 */
@Mixin(WorldDimensions.class)
public abstract class WorldDimensionsMixin {

    @ModifyVariable(method = "bake", at = @At("HEAD"), argsOnly = true)
    private Registry<LevelStem> vantraya$keepTheChosenOverworld(Registry<LevelStem> fromDataPacks) {
        return WorldTypePriority.keepChosenOverworld(((WorldDimensions) (Object) this).dimensions(), fromDataPacks);
    }
}
