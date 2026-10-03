package io.github.exo2v.vantraya.mc;

import io.github.exo2v.vantraya.core.VantrayaModel;
import net.minecraft.core.BlockPos;
import net.minecraft.core.QuartPos;
import net.minecraft.world.level.ChunkPos;
import net.minecraft.world.level.LevelHeightAccessor;
import net.minecraft.world.level.biome.Biome;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.chunk.ChunkAccess;
import net.minecraft.world.level.levelgen.Heightmap;
import net.minecraft.world.level.levelgen.NoiseBasedChunkGenerator;
import net.minecraft.world.level.levelgen.RandomState;
import net.minecraft.core.Holder;

/**
 * Water for the carved river valleys - the one thing a density function cannot express.
 *
 * <p>The terrain comes entirely from vanilla's spline stack: where the ridges map is in its valley band
 * ({@code |ridges| < 0.06}) the offset spline carves a channel, and the biome builder calls the band
 * {@code river}. Minecraft's aquifers only fill below sea level, so upland waterways need their water
 * placed - but the 0.2.0 play test showed what happens when that fill is designed wrong (per-column
 * water lines, pit-lakes everywhere). The rules here are the ones {@code docs/RIVERS_AND_BIOME_BORDERS.md}
 * derives from the study, kept minimal:
 *
 * <ol>
 *   <li><b>The sheet follows the bed.</b> The spline terrain is smooth (continuous derivatives), so a
 *       water surface two blocks above the bed is smooth with it: it runs downhill with no staircases
 *       and steps only where the bed itself steps - a waterfall, not a staircase. (The 0.2.0 staircase
 *       came from this rule applied to a quantised analytic dem, whose terraces the water copied.)</li>
 *   <li><b>The channel is the ridges band - the same map that carved it.</b> Water is placed only where
 *       {@code |ridges| < 0.06} and the column is inland, so the fill and the carve cannot disagree
 *       (M3's flood rule), and nothing is ever wet outside a valley (the "puddles" cannot return: there
 *       is no pit-filling rule at all).</li>
 *   <li><b>One reach, one surface.</b> Every column of a reach holds the same depth of sheet on its own
 *       bed, so the visible surface is the bed's own smooth profile - the river's surface, not the
 *       column's.</li>
 * </ol>
 *
 * Everything below sea level is left to the aquifers and the sea. Lakes form wherever a closed valley
 * widens; there is no separate lake pass.
 */
public final class UplandWater {
    private UplandWater() {
    }

    /** The valley band: wider than vanilla's river slice so the sheet always covers the carved bed. */
    public static final double VALLEY_BAND = 0.06;

    /** How deep the sheet lies on the bed. */
    public static final int SHEET_DEPTH = 2;

    /** Fill the river valleys of one chunk. Runs after the noise fill; the block palette is plain water. */
    public static void fill(ChunkAccess chunk, NoiseBasedChunkGenerator generator, RandomState randomState,
                            VantrayaModel model, LevelHeightAccessor level) {
        ChunkPos pos = chunk.getPos();
        int x0 = pos.getMinBlockX();
        int z0 = pos.getMinBlockZ();
        int minY = level.getMinBuildHeight();
        int maxY = level.getMaxBuildHeight() - 1;

        BlockState water = Blocks.WATER.defaultBlockState();
        BlockPos.MutableBlockPos cursor = new BlockPos.MutableBlockPos();
        for (int dx = 0; dx < 16; dx++) {
            for (int dz = 0; dz < 16; dz++) {
                int x = x0 + dx;
                int z = z0 + dz;
                VantrayaModel.Fields f = model.sample(x + 0.5, z + 0.5);
                if (Math.abs(f.ridges()) >= VALLEY_BAND || f.cont() < -0.19) {
                    continue;
                }
                // the bed of this column: the top solid block (a cave mouth only lowers the reading)
                int bed = maxY;
                for (; bed >= minY; bed--) {
                    if (!chunk.getBlockState(cursor.set(x, bed, z)).isAir()) {
                        break;
                    }
                }
                if (bed < minY || bed >= maxY) {
                    continue;
                }
                for (int y = bed + 1; y <= bed + SHEET_DEPTH; y++) {
                    BlockState here = chunk.getBlockState(cursor.set(x, y, z));
                    if (here.isAir() || here.canBeReplaced()) {
                        chunk.setBlockState(cursor.set(x, y, z), water, true);
                    }
                }
            }
        }
    }

    /** True when the column sits on the river network's valley band (used by the structure policy). */
    public static boolean isValley(double ridges) {
        return Math.abs(ridges) < VALLEY_BAND;
    }

    /** The biome at a quart position, kept here so callers do not need the biome source. */
    public static Holder<Biome> biomeAt(NoiseBasedChunkGenerator generator, RandomState rs, int x, int y, int z) {
        return generator.getBiomeSource().getNoiseBiome(QuartPos.fromBlock(x), QuartPos.fromBlock(y),
                QuartPos.fromBlock(z), rs.sampler());
    }
}
