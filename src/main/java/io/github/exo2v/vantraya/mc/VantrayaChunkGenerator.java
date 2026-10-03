package io.github.exo2v.vantraya.mc;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Map;

import com.mojang.serialization.MapCodec;
import com.mojang.serialization.codecs.RecordCodecBuilder;

import io.github.exo2v.vantraya.core.Spec;
import io.github.exo2v.vantraya.core.VantrayaModel;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Holder;
import net.minecraft.core.HolderLookup;
import net.minecraft.core.RegistryAccess;
import net.minecraft.world.level.LevelHeightAccessor;
import net.minecraft.world.level.StructureManager;
import net.minecraft.world.level.biome.BiomeSource;
import net.minecraft.world.level.chunk.ChunkAccess;
import net.minecraft.world.level.chunk.ChunkGenerator;
import net.minecraft.world.level.chunk.ChunkGeneratorStructureState;
import net.minecraft.world.level.levelgen.Heightmap;
import net.minecraft.world.level.levelgen.NoiseBasedChunkGenerator;
import net.minecraft.world.level.levelgen.NoiseGeneratorSettings;
import net.minecraft.world.level.levelgen.RandomState;
import net.minecraft.world.level.levelgen.structure.BoundingBox;
import net.minecraft.world.level.levelgen.structure.Structure;
import net.minecraft.world.level.levelgen.structure.StructureSet;
import net.minecraft.world.level.levelgen.structure.StructureStart;
import net.minecraft.world.level.levelgen.structure.templatesystem.StructureTemplateManager;
import net.minecraft.world.level.levelgen.synth.NormalNoise;

/**
 * The Vantraya chunk generator: vanilla's noise generator - splines, caves, aquifers, ore veins, rivers,
 * structures, features all as usual - driven by the specification's parameter maps through the noise
 * settings' density functions. Since 0.3.0 there are no post-passes at all: terrain, river valleys and
 * aquifer water all come out of the same fields through vanilla's offset / factor / jaggedness splines, so
 * nothing can desync (the play-test document "Rivers aren't spawning only these puddles" asked for exactly
 * this rework). Two small touches remain: {@link UplandWater} gives the carved valleys their sheet of
 * water (aquifers only fill below sea level), and at the structure step {@link StructurePolicy} throws
 * out the structures that do not belong here.
 *
 * <p>Because it is still a {@code NoiseBasedChunkGenerator} wired through normal noise settings and a
 * vanilla multi-noise biome source, other mods' hooks (Lithostitched noise-router wrappers, surface-rule
 * injection, TerraBlender regions, biome modifiers that add features to biomes, structure sets ...) keep
 * working.
 */
public class VantrayaChunkGenerator extends NoiseBasedChunkGenerator {

    public static final MapCodec<VantrayaChunkGenerator> CODEC = RecordCodecBuilder.mapCodec(instance -> instance.group(
            BiomeSource.CODEC.fieldOf("biome_source").forGetter(ChunkGenerator::getBiomeSource),
            NoiseGeneratorSettings.CODEC.fieldOf("settings").forGetter(NoiseBasedChunkGenerator::generatorSettings)
    ).apply(instance, instance.stable(VantrayaChunkGenerator::new)));

    private record Cached(RandomState state, VantrayaModel model) {
    }

    /** The model a {@link ChunkGeneratorStructureState} belongs to (structures run before the noise fill). */
    private record StateModel(ChunkGeneratorStructureState state, VantrayaModel model) {
    }

    private volatile Cached cached;
    private volatile StateModel stateModel;

    public VantrayaChunkGenerator(BiomeSource biomeSource, Holder<NoiseGeneratorSettings> settings) {
        super(biomeSource, settings);
    }

    @Override
    protected MapCodec<? extends ChunkGenerator> codec() {
        return CODEC;
    }

    @Override
    public ChunkGeneratorStructureState createState(HolderLookup<StructureSet> structureSets,
                                                    RandomState randomState, long seed) {
        ChunkGeneratorStructureState state = super.createState(structureSets, randomState, seed);
        this.stateModel = new StateModel(state,
                WorldSeeds.modelFor(randomState.getOrCreateNoise(VantrayaField.SEED_PROBE)::getValue));
        return state;
    }

    /**
     * Vanilla generates every structure whose biome tag matches the site; for a Vantraya world
     * {@link StructurePolicy} says which ones are thrown out again - Nether structures (the caldera is basalt
     * deltas, and vanilla's fortress tag is {@code #minecraft:is_nether}), and structures that would stand in
     * the middle of a river or lake. This runs at the structure step, before the terrain exists, so the site is
     * read from the specification model; the real Nether and other world types never pass through here.
     */
    @Override
    public void createStructures(RegistryAccess registryAccess, ChunkGeneratorStructureState structureState,
                                 StructureManager structureManager, ChunkAccess chunk,
                                 StructureTemplateManager templateManager) {
        super.createStructures(registryAccess, structureState, structureManager, chunk, templateManager);
        if (!VantrayaConfig.structurePolicy()) {
            return;
        }
        VantrayaModel model = modelOf(structureState);
        List<Structure> dropped = new ArrayList<>();
        for (Map.Entry<Structure, StructureStart> entry : chunk.getAllStarts().entrySet()) {
            StructureStart start = entry.getValue();
            if (start == null || !start.isValid()) {
                continue;
            }
            BoundingBox box = start.getBoundingBox();
            int cx = (box.minX() + box.maxX()) / 2;
            int cz = (box.minZ() + box.maxZ()) / 2;
            if (StructurePolicy.drop(entry.getKey(), model.sample(cx + 0.5, cz + 0.5))) {
                dropped.add(entry.getKey());
            }
        }
        for (Structure structure : dropped) {
            chunk.setStartForStructure(structure, StructureStart.INVALID_START);
        }
    }

    private VantrayaModel modelOf(ChunkGeneratorStructureState structureState) {
        StateModel sm = this.stateModel;
        if (sm != null && sm.state() == structureState) {
            return sm.model();
        }
        // a state this generator did not make (a test driving the chunks directly): the canonical model
        return VantrayaModel.forSeed(Spec.SPEC_SEED);
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
            {0, 0}, {3, 0}, {-3, 0}, {0, 3}, {0, -3}};

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
    public java.util.concurrent.CompletableFuture<net.minecraft.world.level.chunk.ChunkAccess> fillFromNoise(
            net.minecraft.world.level.levelgen.blending.Blender blender, RandomState randomState,
            StructureManager structureManager, net.minecraft.world.level.chunk.ChunkAccess chunk) {
        return super.fillFromNoise(blender, randomState, structureManager, chunk).thenApply(filled -> {
            if (VantrayaConfig.fillRivers()) {
                UplandWater.fill(filled, this, randomState, model(randomState), filled.getHeightAccessorForGeneration());
            }
            return filled;
        });
    }

    @Override
    public void addDebugScreenInfo(List<String> info, RandomState randomState, BlockPos pos) {
        super.addDebugScreenInfo(info, randomState, pos);
        VantrayaModel.Fields f = model(randomState).sample(pos.getX() + 0.5, pos.getZ() + 0.5);
        String where = f.landmark() >= 0 && f.landmark() < Spec.LANDMARKS.size()
                ? Spec.LANDMARKS.get(f.landmark()).name()
                : (f.landmark() == Spec.VEIL_ID ? Spec.VEIL.name() : "open land");
        info.add(String.format(Locale.ROOT, "Vantraya: %s  tier %d  T=%.2f H=%.2f  C=%.2f E=%.2f R=%.2f%s",
                where, f.tier(), f.temperature(), f.humidity(), f.cont(), f.erosion(), f.ridges(),
                f.valley() ? "  [river valley]" : ""));
    }
}
