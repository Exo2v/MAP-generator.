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
import net.minecraft.world.level.chunk.Heightmap;
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
 *   <li><b>One water surface per reach, not per column.</b> The surface is a min-filter of the ground
 *       over a 64-block window - a continuous function of position along the valley. It follows the bed
 *       downhill with no staircases (a min-filter cannot step), and where the bed drops suddenly it
 *       spills as a waterfall.</li>
 *   <li><b>The channel is the ridges band - the same map that carved it.</b> Water is placed only where
 *       {@code |ridges| < 0.06} and the column is inland, so the fill and the carve cannot disagree
 *       (M3's flood rule), and nothing is ever wet outside the valley.</li>
 *   <li><b>The sheet is a few blocks deep.</b> Water fills from the bed up to {@code min-filter + 3.5},
 *       so the river reads as a river of the carve's width, and a column far below its neighbours (a
 *       pit, a cave mouth) gets at most a shallow pool, never a flood.</li>
 * </ol>
 *
 * Everything below sea level is left to the aquifers and the sea. Lakes form wherever a closed valley's
 * bed is locally flat; there is no separate lake pass and no pit-filling ("puddles") rule.
 */
public final class UplandWater {
    private UplandWater() {
    }

    /** The valley band: wider than vanilla's river slice so the sheet always covers the carved bed. */
    public static final double VALLEY_BAND = 0.06;

    /** The reach whose bed the water surface follows. */
    public static final int MIN_FILTER_RADIUS = 64;

    /** How deep the sheet is at its deepest normal point. */
    public static final double SHEET_DEPTH = 3.5;

    /** A column never holds more than this much of the sheet above its own bed. */
    public static final int MAX_COLUMN_FILL = 6;

    /** Fill the river valleys of one chunk. Runs after the noise fill; the block palette is plain water. */
    public static void fill(ChunkAccess chunk, NoiseBasedChunkGenerator generator, RandomState randomState,
                            VantrayaModel model, LevelHeightAccessor level) {
        ChunkPos pos = chunk.getPos();
        int x0 = pos.getMinBlockX();
        int z0 = pos.getMinBlockZ();
        int minY = level.getMinBuildHeight();
        int maxY = level.getMaxBuildHeight() - 1;

        // 1. this chunk's bed per column, straight from the filled blocks (caves only ever lower it)
        int[][] bed = new int[16][16];
        BlockPos.MutableBlockPos cursor = new BlockPos.MutableBlockPos();
        for (int dx = 0; dx < 16; dx++) {
            for (int dz = 0; dz < 16; dz++) {
                int y = maxY;
                for (; y >= minY; y--) {
                    if (!chunk.getBlockState(cursor.set(x0 + dx, y, z0 + dz)).isAir()) {
                        break;
                    }
                }
                bed[dx][dz] = y;
            }
        }

        // 2. the min-filter over the reach: this chunk's bed plus a ring of surrounding columns
        int[][] waterY = new int[16][16];
        for (int dx = 0; dx < 16; dx++) {
            for (int dz = 0; dz < 16; dz++) {
                int x = x0 + dx;
                int z = z0 + dz;
                int min = bed[dx][dz];
                for (int r = MIN_FILTER_RADIUS; r >= 16; r -= 16) {
                    for (int k = 0; k < 8; k++) {
                        double a = k * Math.PI / 4.0;
                        int sx = x + (int) Math.round(r * Math.cos(a));
                        int sz = z + (int) Math.round(r * Math.sin(a));
                        if (sx >= x0 && sx < x0 + 16 && sz >= z0 && sz < z0 + 16) {
                            continue; // this chunk's own bed was already scanned
                        }
                        int ground = generator.getBaseHeight(sx, sz, Heightmap.Types.OCEAN_FLOOR_WG, level, randomState) - 1;
                        min = Math.min(min, ground);
                    }
                }
                waterY[dx][dz] = (int) Math.floor(min + SHEET_DEPTH);
            }
        }

        // 3. place the sheet: only in the valley band, only inland, only shallow columns
        BlockState water = Blocks.WATER.defaultBlockState();
        for (int dx = 0; dx < 16; dx++) {
            for (int dz = 0; dz < 16; dz++) {
                int x = x0 + dx;
                int z = z0 + dz;
                VantrayaModel.Fields f = model.sample(x + 0.5, z + 0.5);
                if (Math.abs(f.ridges()) >= VALLEY_BAND || f.cont() < -0.19) {
                    continue;
                }
                int bedY = bed[dx][dz];
                if (bedY < minY || bedY >= maxY) {
                    continue;
                }
                int top = Math.min(waterY[dx][dz], bedY + MAX_COLUMN_FILL);
                for (int y = bedY + 1; y <= top; y++) {
                    BlockState here = chunk.getBlockState(cursor.set(x, y, z));
                    if (here.isAir() || here.canBeReplaced()) {
                        chunk.setBlockState(cursor.set(x, y, z), water);
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
