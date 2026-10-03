package io.github.exo2v.vantraya.mc;

import io.github.exo2v.vantraya.core.VantrayaModel;
import net.minecraft.core.BlockPos;
import net.minecraft.world.level.ChunkPos;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.chunk.ChunkAccess;

/**
 * Rivers and lakes carry water.
 *
 * <p>Minecraft's noise fill only puts water below the world's fixed sea level, so a river carved through the
 * hills - where the specification's network runs, hundreds of blocks above it - would come out as a dry ravine.
 * The first play test called that "a critical lack of waterways": the continent had valleys but no water in
 * them, and travel by river was impossible.
 *
 * <p>This pass fills every channel the model carves up to the water surface the model chose
 * ({@link VantrayaModel.Fields#waterLine}), two blocks under the natural ground of the bed, with a floor a few
 * blocks under that. Cold channels freeze over. Like the caldera's fluids it runs right after the noise fill,
 * so surface rules, the slope-aware surface painter and decoration all see the finished water; aquifers and
 * caves underground are left alone, and the caldera and the dry basins stay dry.
 */
public final class RiverWater {
    private RiverWater() {
    }

    /** Below this channel strength the banks taper out and no water is placed. */
    private static final double RIVER_TAPERS = 0.3;
    private static final double LAKE_TAPER = 0.4;

    /** The frozen_river line of the climate matrix (temperature unit 0.16). */
    private static final double FREEZE_TEMPERATURE = -0.68;

    public static void fill(ChunkAccess chunk, VantrayaModel model) {
        ChunkPos cp = chunk.getPos();
        BlockState water = Blocks.WATER.defaultBlockState();
        BlockState ice = Blocks.ICE.defaultBlockState();
        BlockPos.MutableBlockPos pos = new BlockPos.MutableBlockPos();
        for (int dz = 0; dz < 16; dz++) {
            for (int dx = 0; dx < 16; dx++) {
                int x = cp.getMinBlockX() + dx;
                int z = cp.getMinBlockZ() + dz;
                VantrayaModel.Fields f = model.sample(x + 0.5, z + 0.5);
                if (f.waterLine() <= VantrayaModel.NO_WATER_LINE + 1.0 || f.noWater()) {
                    continue;
                }
                if (f.river() <= RIVER_TAPERS && f.lake() <= LAKE_TAPER) {
                    continue;
                }
                int ground = SurfacePainter.groundY(chunk, dx, dz);
                int top = (int) Math.floor(f.waterLine());
                if (top <= ground) {
                    continue; // not cut deep enough here: this column is a bank
                }
                boolean frozen = f.temperature() < FREEZE_TEMPERATURE;
                for (int y = ground + 1; y <= top; y++) {
                    pos.set(x, y, z);
                    if (chunk.getBlockState(pos).isAir()) {
                        chunk.setBlockState(pos, y == top && frozen ? ice : water, false);
                    }
                }
            }
        }
    }
}
