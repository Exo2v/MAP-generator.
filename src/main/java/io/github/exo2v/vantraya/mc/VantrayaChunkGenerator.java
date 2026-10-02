package io.github.exo2v.vantraya.mc;

import java.util.List;
import java.util.Locale;
import java.util.concurrent.CompletableFuture;

import com.mojang.serialization.MapCodec;
import com.mojang.serialization.codecs.RecordCodecBuilder;

import io.github.exo2v.vantraya.core.Spec;
import io.github.exo2v.vantraya.core.VantrayaModel;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Holder;
import net.minecraft.server.level.WorldGenRegion;
import net.minecraft.world.level.LevelHeightAccessor;
import net.minecraft.world.level.StructureManager;
import net.minecraft.world.level.biome.BiomeSource;
import net.minecraft.world.level.chunk.ChunkAccess;
import net.minecraft.world.level.chunk.ChunkGenerator;
import net.minecraft.world.level.levelgen.Heightmap;
import net.minecraft.world.level.levelgen.NoiseBasedChunkGenerator;
import net.minecraft.world.level.levelgen.NoiseGeneratorSettings;
import net.minecraft.world.level.levelgen.RandomState;
import net.minecraft.world.level.levelgen.blending.Blender;
import net.minecraft.world.level.levelgen.synth.NormalNoise;

/**
 * The Vantraya chunk generator: vanilla's noise generator - caves, aquifers, ore veins, structures, features
 * all as usual - fed by the specification's height field (through the noise settings' density functions) plus
 * three small post-passes the specification calls for and a density function cannot express:
 * <ol>
 *   <li>after the noise fill, open water is drained from the caldera (and lava is poured on its floor);</li>
 *   <li>after the biome surface rules, the slope-aware surface table is applied.</li>
 * </ol>
 * Because it is still a {@code NoiseBasedChunkGenerator} wired through normal noise settings, other mods'
 * hooks (Lithostitched noise-router wrappers, surface-rule injection, biome modifiers that add features to
 * biomes, structure sets ...) keep working.
 */
public class VantrayaChunkGenerator extends NoiseBasedChunkGenerator {

    public static final MapCodec<VantrayaChunkGenerator> CODEC = RecordCodecBuilder.mapCodec(instance -> instance.group(
            BiomeSource.CODEC.fieldOf("biome_source").forGetter(ChunkGenerator::getBiomeSource),
            NoiseGeneratorSettings.CODEC.fieldOf("settings").forGetter(NoiseBasedChunkGenerator::generatorSettings)
    ).apply(instance, instance.stable(VantrayaChunkGenerator::new)));

    private record Cached(RandomState state, VantrayaModel model) {
    }

    private volatile Cached cached;

    public VantrayaChunkGenerator(BiomeSource biomeSource, Holder<NoiseGeneratorSettings> settings) {
        super(biomeSource, settings);
    }

    @Override
    protected MapCodec<? extends ChunkGenerator> codec() {
        return CODEC;
    }

    /** The specification model of the world this generator is running in. */
    public VantrayaModel model(RandomState randomState) {
        Cached c = this.cached;
        if (c != null && c.state() == randomState) {
            return c.model();
        }
        NormalNoise probe = randomState.getOrCreateNoise(VantrayaField.SEED_PROBE);
        VantrayaModel m = WorldSeeds.modelFor(probe::getValue);
        this.cached = new Cached(randomState, m);
        return m;
    }

    /** The columns {@link #terrainTopY} reads: the one asked for and a ring of eight about three blocks around it. */
    private static final int[][] LOOK_AROUND = {
            {0, 0}, {3, 0}, {-3, 0}, {0, 3}, {0, -3}, {2, 2}, {2, -2}, {-2, 2}, {-2, -2}};

    /**
     * The Y of the top solid block of the <em>terrain</em> at (x, z): the highest top block among the column and a
     * ring of eight columns around it. Caves and noodle tunnels only ever remove ground, so a tunnel that happens to
     * break the surface exactly at the column asked for lowers that column's top block by several blocks while the
     * ground around it is intact; the ring still sees the surface the height field asked for. This is what
     * {@code /vantraya verify} and the in-engine tests ask - does the terrain match the specification - rather than
     * whether a cave happens to open on a pin.
     */
    public int terrainTopY(int x, int z, LevelHeightAccessor level, RandomState randomState) {
        int top = Integer.MIN_VALUE;
        for (int[] d : LOOK_AROUND) {
            top = Math.max(top, getBaseHeight(x + d[0], z + d[1], Heightmap.Types.OCEAN_FLOOR_WG, level, randomState) - 1);
        }
        return top;
    }

    @Override
    public CompletableFuture<ChunkAccess> fillFromNoise(Blender blender, RandomState randomState,
                                                        StructureManager structureManager, ChunkAccess chunk) {
        return super.fillFromNoise(blender, randomState, structureManager, chunk).thenApply(filled -> {
            if (VantrayaConfig.keepCalderaDry()) {
                CalderaFluids.drain(filled, model(randomState));
            }
            return filled;
        });
    }

    @Override
    public void buildSurface(WorldGenRegion level, StructureManager structureManager, RandomState randomState,
                             ChunkAccess chunk) {
        super.buildSurface(level, structureManager, randomState, chunk);
        VantrayaModel model = model(randomState);
        if (VantrayaConfig.paintSurface()) {
            SurfacePainter.paint(chunk, model);
        }
        if (VantrayaConfig.keepCalderaDry()) {
            CalderaFluids.pourLava(chunk, model);
        }
    }

    @Override
    public void addDebugScreenInfo(List<String> info, RandomState randomState, BlockPos pos) {
        super.addDebugScreenInfo(info, randomState, pos);
        VantrayaModel.Fields f = model(randomState).sample(pos.getX() + 0.5, pos.getZ() + 0.5);
        String where = f.landmark() >= 0 && f.landmark() < Spec.LANDMARKS.size()
                ? Spec.LANDMARKS.get(f.landmark()).name()
                : (f.landmark() == Spec.VEIL_ID ? Spec.VEIL.name() : "open land");
        info.add(String.format(Locale.ROOT, "Vantraya: %s  H=%.1f  tier %d  T=%.2f H=%.2f  C=%.2f E=%.2f R=%.2f",
                where, f.height(), f.tier(), f.temperature(), f.humidity(), f.cont(), f.erosion(), f.ridges()));
    }
}
