package io.github.exo2v.vantraya.mc;

import io.github.exo2v.vantraya.core.Spec;
import io.github.exo2v.vantraya.core.VantrayaModel;
import net.minecraft.core.BlockPos;
import net.minecraft.world.level.ChunkPos;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.chunk.ChunkAccess;

/**
 * The Ashen Caldera is a volcano: "nothing inside the ring may be flooded" (HANDOFF 5.11). The crater floor
 * lies at Y = 40, below the waterline, and the Overworld generator fills every open block under sea level with
 * water - so after the terrain is built the water inside the crater is removed again, and the lava basins
 * the specification describes ("Basalt / Lava Basins") are poured on the floor.
 *
 * <p>Only open-air surface water is touched: water above the top solid block of a column inside the
 * caldera's no-water mask. Aquifers and caves underground are left alone.
 */
public final class CalderaFluids {
    private CalderaFluids() {
    }

    /** The crater is only near the origin; chunks farther than this from it cannot contain any of it. */
    private static final double REACH = 800.0;

    private static boolean nearCaldera(ChunkPos cp) {
        return Math.hypot(cp.getMinBlockX() + 8.0, cp.getMinBlockZ() + 8.0) <= REACH;
    }

    /** Remove open surface water from every column of the chunk inside the caldera's no-water mask. */
    public static void drain(ChunkAccess chunk, VantrayaModel model) {
        ChunkPos cp = chunk.getPos();
        if (!nearCaldera(cp)) {
            return;
        }
        BlockState air = Blocks.AIR.defaultBlockState();
        BlockPos.MutableBlockPos pos = new BlockPos.MutableBlockPos();
        int seaTop = (int) Spec.SEA_LEVEL; // the Overworld fills open air up to this block (sea_level 63)
        for (int dz = 0; dz < 16; dz++) {
            for (int dx = 0; dx < 16; dx++) {
                int x = cp.getMinBlockX() + dx;
                int z = cp.getMinBlockZ() + dz;
                if (!model.sample(x + 0.5, z + 0.5).noWater()) {
                    continue;
                }
                int ground = SurfacePainter.groundY(chunk, dx, dz);
                for (int y = ground + 1; y <= seaTop; y++) {
                    pos.set(x, y, z);
                    if (chunk.getBlockState(pos).is(Blocks.WATER)) {
                        chunk.setBlockState(pos, air, false);
                    }
                }
            }
        }
    }

    /** Pour a sheet of lava on the crater floor where the specification's lava mask says so. */
    public static void pourLava(ChunkAccess chunk, VantrayaModel model) {
        ChunkPos cp = chunk.getPos();
        if (!nearCaldera(cp)) {
            return;
        }
        BlockState lava = Blocks.LAVA.defaultBlockState();
        BlockPos.MutableBlockPos pos = new BlockPos.MutableBlockPos();
        for (int dz = 0; dz < 16; dz++) {
            for (int dx = 0; dx < 16; dx++) {
                int x = cp.getMinBlockX() + dx;
                int z = cp.getMinBlockZ() + dz;
                if (!model.sample(x + 0.5, z + 0.5).lava()) {
                    continue;
                }
                int ground = SurfacePainter.groundY(chunk, dx, dz);
                pos.set(x, ground + 1, z);
                if (chunk.getBlockState(pos).isAir()) {
                    chunk.setBlockState(pos, lava, false);
                }
            }
        }
    }
}
