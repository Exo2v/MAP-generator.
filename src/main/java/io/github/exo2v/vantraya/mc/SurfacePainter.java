package io.github.exo2v.vantraya.mc;

import io.github.exo2v.vantraya.core.BiomeLogic;
import io.github.exo2v.vantraya.core.BiomeRole;
import io.github.exo2v.vantraya.core.BlueNoise;
import io.github.exo2v.vantraya.core.Noise;
import io.github.exo2v.vantraya.core.Spec;
import io.github.exo2v.vantraya.core.SurfaceLogic;
import io.github.exo2v.vantraya.core.SurfaceLogic.Mat;
import io.github.exo2v.vantraya.core.VantrayaModel;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.world.level.ChunkPos;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.chunk.ChunkAccess;
import net.minecraft.world.level.levelgen.Heightmap;

/**
 * Applies the specification's slope-aware surface table to a freshly built chunk. The biome's own surface
 * rule has already run; this pass changes only what the specification dictates (steep faces, the treeline,
 * the caldera, the Veil of Salt crust ...) and leaves everything else as the biome made it.
 *
 * <p>The decision itself is {@link SurfaceLogic}; this class only gathers its inputs for each of the 256
 * columns and writes the result. Soil is what trees, logs and shrubs root in, so stripping it from cliffs,
 * summits and the volcano keeps vegetation (vanilla's or any mod's) off them without teaching a single
 * decorator about slopes.
 */
public final class SurfacePainter {
    private SurfacePainter() {
    }

    private static volatile BlockState[] states;

    private static BlockState state(Mat m) {
        BlockState[] s = states;
        if (s == null) {
            s = new BlockState[Mat.values().length];
            for (Mat mat : Mat.values()) {
                s[mat.ordinal()] = BuiltInRegistries.BLOCK
                        .get(ResourceLocation.withDefaultNamespace(mat.path())).defaultBlockState();
            }
            states = s;
        }
        return s[m.ordinal()];
    }

    /** Y of the top solid block of a column (the ground, ignoring any water above it). */
    static int groundY(ChunkAccess chunk, int dx, int dz) {
        return chunk.getHeight(Heightmap.Types.OCEAN_FLOOR_WG, dx, dz) - 1;
    }

    public static void paint(ChunkAccess chunk, VantrayaModel model) {
        ChunkPos cp = chunk.getPos();
        int minX = cp.getMinBlockX();
        int minZ = cp.getMinBlockZ();
        BlueNoise blue = BlueNoise.forSeed(model.seed());
        int hashSeedA = (int) (model.seed() * 31 + 7);
        int hashSeedB = (int) (model.seed() + 13);
        BlockPos.MutableBlockPos pos = new BlockPos.MutableBlockPos();
        for (int dz = 0; dz < 16; dz++) {
            for (int dx = 0; dx < 16; dx++) {
                int x = minX + dx;
                int z = minZ + dz;
                int ground = groundY(chunk, dx, dz);
                if (ground <= chunk.getMinBuildHeight() + 5) {
                    continue;
                }
                VantrayaModel.Fields f = model.sample(x + 0.5, z + 0.5);
                boolean wet = ground < Spec.SEA_LEVEL;
                double deg = wet ? 0.0 : model.slopeDegrees(x + 0.5, z + 0.5);
                BiomeRole role = BiomeLogic.surfaceRole(x + 0.5, z + 0.5, ground,
                        f.temperature(), f.humidity(), f.cont(), f.erosion());
                SurfaceLogic.Choice c = SurfaceLogic.choose(new SurfaceLogic.Column(
                        deg, ground, wet, f.landmark(), role, f.lava(),
                        blue.threshold(x, z),
                        Noise.hash2(x, z, hashSeedA),
                        Noise.hash2(x + 7919L, z - 104729L, hashSeedB)));
                if (c.isKeep()) {
                    continue;
                }
                for (int i = 0; i < c.soilDepth(); i++) {
                    pos.set(x, ground - i, z);
                    BlockState current = chunk.getBlockState(pos);
                    if (current.isAir() || !current.getFluidState().isEmpty() || current.is(Blocks.BEDROCK)) {
                        break;
                    }
                    Mat m = i == 0 ? c.top() : c.filler();
                    if (m != null) {
                        chunk.setBlockState(pos, state(m), false);
                    }
                }
            }
        }
    }
}
