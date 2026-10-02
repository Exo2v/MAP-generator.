package io.github.exo2v.vantraya.engine;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.concurrent.TimeUnit;
import java.util.function.Predicate;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;

import com.google.gson.JsonElement;
import com.mojang.serialization.DataResult;
import com.mojang.serialization.JsonOps;

import io.github.exo2v.vantraya.VantrayaBuilder;
import io.github.exo2v.vantraya.core.BiomeLogic;
import io.github.exo2v.vantraya.core.BiomeRole;
import io.github.exo2v.vantraya.core.Spec;
import io.github.exo2v.vantraya.core.SpecVerifier;
import io.github.exo2v.vantraya.core.VantrayaModel;
import io.github.exo2v.vantraya.mc.CalderaFluids;
import io.github.exo2v.vantraya.mc.SurfacePainter;
import io.github.exo2v.vantraya.mc.VantrayaBiomeSource;
import io.github.exo2v.vantraya.mc.VantrayaChunkGenerator;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Holder;
import net.minecraft.core.HolderSet;
import net.minecraft.core.QuartPos;
import net.minecraft.core.Registry;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.RegistryOps;
import net.minecraft.resources.ResourceKey;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.MinecraftServer;
import net.minecraft.tags.BlockTags;
import net.minecraft.tags.WorldPresetTags;
import net.minecraft.world.level.ChunkPos;
import net.minecraft.world.level.LevelHeightAccessor;
import net.minecraft.world.level.StructureManager;
import net.minecraft.world.level.biome.Biome;
import net.minecraft.world.level.biome.BiomeSource;
import net.minecraft.world.level.biome.Climate;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.chunk.ProtoChunk;
import net.minecraft.world.level.chunk.UpgradeData;
import net.minecraft.world.level.dimension.DimensionType;
import net.minecraft.world.level.dimension.LevelStem;
import net.minecraft.world.level.levelgen.Heightmap;
import net.minecraft.world.level.levelgen.NoiseBasedChunkGenerator;
import net.minecraft.world.level.levelgen.RandomState;
import net.minecraft.world.level.levelgen.blending.Blender;
import net.minecraft.world.level.levelgen.presets.WorldPreset;
import net.minecraft.world.level.levelgen.structure.Structure;
import net.minecraft.world.level.levelgen.structure.StructureStart;
import net.neoforged.testframework.junit.EphemeralTestServerProvider;

/**
 * The Vantraya world type inside the real game engine - no dedicated server, no EULA, no world on disk.
 *
 * <p>{@link EphemeralTestServerProvider} starts an in-memory {@link MinecraftServer} whose data is loaded by
 * Minecraft's own {@code WorldLoader} from vanilla's data pack plus every mod's, so a JSON mistake in the
 * preset, dimension type, noise settings, density functions, noise or biome tags fails here exactly as it
 * would when a player opens the world creation screen. The tests then take the generator out of the preset and
 * drive it directly: the HANDOFF section 9.4 verification on the real density-function engine, real chunks
 * through {@code fillFromNoise}, the biome source against the real {@code Climate.Sampler}, the codec round
 * trip that saving {@code level.dat} performs.
 *
 * <p>The ephemeral server's own overworld is an empty void world that exists only to load data; it is not
 * touched. What this class cannot reach is a player in a world: the vanilla surface rule pass
 * ({@code buildSurface} needs a {@code WorldGenRegion}), feature decoration, spawning.
 */
@ExtendWith(EphemeralTestServerProvider.class)
class InEngineWorldgenTest {

    private static final int MIN_Y = -64;
    private static final int HEIGHT = 384;
    private static final LevelHeightAccessor LEVEL = LevelHeightAccessor.create(MIN_Y, HEIGHT);

    // ---- helpers ----------------------------------------------------------------------------------

    private static WorldPreset preset(MinecraftServer server) {
        Registry<WorldPreset> presets = server.registryAccess().registryOrThrow(Registries.WORLD_PRESET);
        return presets.getHolderOrThrow(ResourceKey.create(Registries.WORLD_PRESET, VantrayaBuilder.id("vantraya"))).value();
    }

    private static LevelStem overworld(MinecraftServer server) {
        LevelStem stem = preset(server).dimensions().get(LevelStem.OVERWORLD);
        assertNotNull(stem, "the preset defines an overworld");
        return stem;
    }

    private static VantrayaChunkGenerator generator(MinecraftServer server) {
        Object g = overworld(server).generator();
        assertTrue(g instanceof VantrayaChunkGenerator, "the overworld's generator is Vantraya's, not " + g.getClass());
        return (VantrayaChunkGenerator) g;
    }

    private static RandomState randomState(MinecraftServer server, VantrayaChunkGenerator gen, long seed) {
        return RandomState.create(gen.generatorSettings().value(), server.registryAccess().lookupOrThrow(Registries.NOISE), seed);
    }

    private static BiomeRole roleAt(RandomState rs, int x, int y, int z) {
        Climate.TargetPoint tp = rs.sampler().sample(QuartPos.fromBlock(x), QuartPos.fromBlock(y), QuartPos.fromBlock(z));
        return BiomeLogic.classify(x, y, z,
                Climate.unquantizeCoord(tp.temperature()), Climate.unquantizeCoord(tp.humidity()),
                Climate.unquantizeCoord(tp.continentalness()), Climate.unquantizeCoord(tp.erosion()),
                Climate.unquantizeCoord(tp.depth()));
    }

    /** What {@code /vantraya verify} reads: the live generator's height, and the climate the router produces. */
    private static SpecVerifier.Probe engineProbe(VantrayaChunkGenerator gen, RandomState rs) {
        return new SpecVerifier.Probe() {
            @Override
            public int groundHeight(int x, int z) {
                return gen.getBaseHeight(x, z, Heightmap.Types.OCEAN_FLOOR_WG, LEVEL, rs) - 1;
            }

            @Override
            public BiomeRole role(int x, int y, int z) {
                return roleAt(rs, x, y, z);
            }
        };
    }

    /** A structure manager that finds no structures, so chunks can be generated without touching any world. */
    private static StructureManager noStructures() {
        return new StructureManager(null, null, null) {
            @Override
            public List<StructureStart> startsForStructure(ChunkPos pos, Predicate<Structure> predicate) {
                return List.of();
            }
        };
    }

    private static ProtoChunk fill(NoiseBasedChunkGenerator gen, RandomState rs, Registry<Biome> biomes, int chunkX, int chunkZ)
            throws Exception {
        ProtoChunk chunk = new ProtoChunk(new ChunkPos(chunkX, chunkZ), UpgradeData.EMPTY, LEVEL, biomes, null);
        gen.fillFromNoise(Blender.empty(), rs, noStructures(), chunk).get(120, TimeUnit.SECONDS);
        return chunk;
    }

    private static int ground(ProtoChunk chunk, int x, int z) {
        return chunk.getHeight(Heightmap.Types.OCEAN_FLOOR_WG, x & 15, z & 15) - 1;
    }

    private static int count(ProtoChunk chunk, Predicate<BlockState> what) {
        int n = 0;
        BlockPos.MutableBlockPos pos = new BlockPos.MutableBlockPos();
        for (int dz = 0; dz < 16; dz++) {
            for (int dx = 0; dx < 16; dx++) {
                for (int y = MIN_Y; y < MIN_Y + HEIGHT; y++) {
                    pos.set(chunk.getPos().getMinBlockX() + dx, y, chunk.getPos().getMinBlockZ() + dz);
                    if (what.test(chunk.getBlockState(pos))) {
                        n++;
                    }
                }
            }
        }
        return n;
    }

    private static String failures(List<SpecVerifier.Check> checks) {
        List<String> failed = new ArrayList<>();
        for (SpecVerifier.Check c : checks) {
            if (!c.pass()) {
                failed.add(c.toString());
            }
        }
        return failed.toString();
    }

    // ---- registration ------------------------------------------------------------------------------

    @Test
    void theCodecsAreRegistered() {
        assertTrue(BuiltInRegistries.DENSITY_FUNCTION_TYPE.containsKey(VantrayaBuilder.id("field")), "density function type");
        assertTrue(BuiltInRegistries.CHUNK_GENERATOR.containsKey(VantrayaBuilder.id("vantraya")), "chunk generator");
        assertTrue(BuiltInRegistries.BIOME_SOURCE.containsKey(VantrayaBuilder.id("vantraya")), "biome source");
    }

    // ---- the data, loaded by Minecraft's own loader ---------------------------------------------------

    @Test
    void theWorldTypeLoadsAndIsListedOnTheCreationScreen(MinecraftServer server) {
        Registry<WorldPreset> presets = server.registryAccess().registryOrThrow(Registries.WORLD_PRESET);
        Holder<WorldPreset> holder = presets.getHolderOrThrow(ResourceKey.create(Registries.WORLD_PRESET, VantrayaBuilder.id("vantraya")));
        HolderSet.Named<WorldPreset> normal = presets.getTag(WorldPresetTags.NORMAL)
                .orElseThrow(() -> new AssertionError("the tag #minecraft:normal is not bound"));
        assertTrue(normal.contains(holder), "the preset is in #minecraft:normal, which is what the creation screen lists");

        LevelStem overworld = overworld(server);
        assertTrue(overworld.generator() instanceof VantrayaChunkGenerator);
        assertTrue(overworld.generator().getBiomeSource() instanceof VantrayaBiomeSource);
        DimensionType type = overworld.type().value();
        assertEquals(MIN_Y, type.minY());
        assertEquals(HEIGHT, type.height());
        assertEquals(HEIGHT, type.logicalHeight());
        assertTrue(preset(server).dimensions().containsKey(LevelStem.NETHER), "the Nether is still there");
        assertTrue(preset(server).dimensions().containsKey(LevelStem.END), "the End is still there");
        assertEquals(63, generator(server).generatorSettings().value().seaLevel(), "water fills through Y=62");
    }

    @Test
    void everyRoleTagIsBoundAndHoldsItsVanillaDefault(MinecraftServer server) {
        Registry<Biome> biomes = server.registryAccess().registryOrThrow(Registries.BIOME);
        for (BiomeRole role : BiomeRole.values()) {
            HolderSet.Named<Biome> tag = biomes.getTag(VantrayaBiomeSource.tagOf(role))
                    .orElseThrow(() -> new AssertionError("role tag not bound: " + VantrayaBiomeSource.tagOf(role)));
            ResourceLocation vanilla = ResourceLocation.withDefaultNamespace(role.vanilla());
            assertTrue(tag.stream().anyMatch(h -> h.is(vanilla)), "#" + VantrayaBiomeSource.tagOf(role).location() + " lacks " + vanilla);
        }
    }

    @Test
    void theDimensionSurvivesTheEncodeDecodeRoundTripThatSavingAWorldPerforms(MinecraftServer server) {
        RegistryOps<JsonElement> ops = RegistryOps.create(JsonOps.INSTANCE, server.registryAccess());
        DataResult<JsonElement> encoded = LevelStem.CODEC.encodeStart(ops, overworld(server));
        assertTrue(encoded.result().isPresent(), () -> "encoding failed: " + encoded.error().map(e -> e.message()).orElse("?"));
        String text = encoded.result().get().toString();
        assertTrue(text.contains("vantraya_builder:vantraya"), text);
        DataResult<LevelStem> decoded = LevelStem.CODEC.parse(ops, encoded.result().get());
        assertTrue(decoded.result().isPresent(), () -> "decoding failed: " + decoded.error().map(e -> e.message()).orElse("?"));
        assertTrue(decoded.result().get().generator() instanceof VantrayaChunkGenerator);
        assertTrue(decoded.result().get().generator().getBiomeSource() instanceof VantrayaBiomeSource);
    }

    @Test
    void theCommandIsRegistered(MinecraftServer server) {
        var root = server.getCommands().getDispatcher().getRoot().getChild("vantraya");
        assertNotNull(root, "/vantraya");
        for (String sub : new String[] {"info", "where", "locate", "tp", "verify", "biomes"}) {
            assertNotNull(root.getChild(sub), "/vantraya " + sub);
        }
    }

    // ---- the terrain on the real density-function engine -----------------------------------------------

    @Test
    void theSpecificationVerifiesOnTheRealEngineForSeveralWorldSeeds(MinecraftServer server) {
        VantrayaChunkGenerator gen = generator(server);
        for (long seed : new long[] {0L, 20250929L, -987654321L, 4242L}) {
            List<SpecVerifier.Check> checks = SpecVerifier.run(engineProbe(gen, randomState(server, gen, seed)));
            assertTrue(checks.size() >= 30, "the verifier ran " + checks.size() + " checks");
            assertTrue(SpecVerifier.allPass(checks), "world seed " + seed + ": " + failures(checks));
        }
    }

    @Test
    void theWorldSeedReachesTheDensityFunctions(MinecraftServer server) {
        VantrayaChunkGenerator gen = generator(server);
        RandomState a = randomState(server, gen, 111L);
        RandomState b = randomState(server, gen, 222L);
        VantrayaModel ma = gen.model(a);
        VantrayaModel mb = gen.model(b);
        assertNotEquals(ma.seed(), mb.seed(), "two world seeds must give two models");

        // columns where the two worlds differ a lot: the density functions must follow their own world's model
        List<int[]> columns = new ArrayList<>();
        for (int x = -3600; x <= 3600; x += 150) {
            for (int z = -3600; z <= 3600; z += 150) {
                if (Math.abs(ma.height(x + 0.5, z + 0.5) - mb.height(x + 0.5, z + 0.5)) > 15.0 && columns.size() < 30) {
                    columns.add(new int[] {x, z});
                }
            }
        }
        assertTrue(columns.size() >= 8, "only " + columns.size() + " columns tell the two worlds apart");
        for (int[] c : columns) {
            double engine = gen.getBaseHeight(c[0], c[1], Heightmap.Types.OCEAN_FLOOR_WG, LEVEL, a) - 1;
            double own = Math.abs(engine - ma.height(c[0] + 0.5, c[1] + 0.5));
            double other = Math.abs(engine - mb.height(c[0] + 0.5, c[1] + 0.5));
            assertTrue(own < other, "column " + c[0] + "," + c[1] + ": engine " + engine
                    + " is not closer to its own world's height (off by " + own + ") than to the other's (" + other + ")");
        }
    }

    // ---- biomes -------------------------------------------------------------------------------------

    @Test
    void theBiomeSourceResolvesEveryRoleAndPlacesBiomesFromTheRealClimate(MinecraftServer server) {
        VantrayaChunkGenerator gen = generator(server);
        BiomeSource source = gen.getBiomeSource();
        Set<Holder<Biome>> possible = source.possibleBiomes();
        assertFalse(possible.isEmpty(), "possible biomes");
        for (BiomeRole role : BiomeRole.values()) {
            ResourceLocation vanilla = ResourceLocation.withDefaultNamespace(role.vanilla());
            assertTrue(possible.stream().anyMatch(h -> h.is(vanilla)), "role " + role.id() + " resolves to " + vanilla);
        }

        RandomState rs = randomState(server, gen, 31337L);
        for (Spec.Landmark lm : Spec.LANDMARKS) {
            int x = (int) Math.floor(lm.x());
            int z = (int) Math.floor(lm.z());
            int y = Math.max(gen.getBaseHeight(x, z, Heightmap.Types.OCEAN_FLOOR_WG, LEVEL, rs), (int) Spec.SEA_LEVEL + 1);
            Holder<Biome> biome = source.getNoiseBiome(QuartPos.fromBlock(x), QuartPos.fromBlock(y), QuartPos.fromBlock(z), rs.sampler());
            assertNotNull(biome, lm.name());
            assertTrue(possible.contains(biome), lm.name() + ": a biome outside the source's own set");
            boolean specified = false;
            for (String name : lm.biomes()) {
                BiomeRole role = BiomeRole.ofSpecName(name);
                specified |= biome.is(ResourceLocation.withDefaultNamespace(name))
                        || (role != null && biome.is(ResourceLocation.withDefaultNamespace(role.vanilla())));
            }
            assertTrue(specified, lm.name() + ": centre biome " + biome.unwrapKey().map(k -> k.location().toString()).orElse("?")
                    + " is none of " + lm.biomes());
        }
    }

    // ---- real chunks ----------------------------------------------------------------------------------

    @Test
    void landmarkChunksGenerateOnTheRealEngineAtTheirSpecifiedElevations(MinecraftServer server) throws Exception {
        VantrayaChunkGenerator gen = generator(server);
        Registry<Biome> biomes = server.registryAccess().registryOrThrow(Registries.BIOME);
        RandomState rs = randomState(server, gen, 777L);
        VantrayaModel model = gen.model(rs);
        List<String> problems = new ArrayList<>();

        for (Spec.Landmark lm : Spec.LANDMARKS) {
            int x = (int) Math.floor(lm.x());
            int z = (int) Math.floor(lm.z());
            ProtoChunk chunk = fill(gen, rs, biomes, x >> 4, z >> 4);
            int ground = ground(chunk, x, z);
            int viaBase = gen.getBaseHeight(x, z, Heightmap.Types.OCEAN_FLOOR_WG, LEVEL, rs) - 1;
            double want = lm.kind() == Spec.Kind.CALDERA ? Spec.CALDERA_THRONE : lm.y();
            if (ground != viaBase) {
                problems.add(lm.name() + ": chunk ground " + ground + " but getBaseHeight says " + viaBase);
            }
            if (Math.abs(ground - want) > SpecVerifier.CENTRE_TOLERANCE) {
                problems.add(lm.name() + ": ground Y " + ground + ", specified " + want);
            }
            if (chunk.getBlockState(new BlockPos(x, MIN_Y, z)).isAir()) {
                problems.add(lm.name() + ": no rock at the bottom of the world");
            }
            if (!chunk.getBlockState(new BlockPos(x, MIN_Y + HEIGHT - 1, z)).isAir()) {
                problems.add(lm.name() + ": something solid at the build limit");
            }
        }
        assertTrue(problems.isEmpty(), problems.toString());

        // the Obsidian Throne is obsidian once the surface pass has run
        ProtoChunk centre = fill(gen, rs, biomes, 0, 0);
        SurfacePainter.paint(centre, model);
        int throne = ground(centre, 0, 0);
        assertTrue(centre.getBlockState(new BlockPos(0, throne, 0)).is(Blocks.OBSIDIAN),
                "the Throne's top block is " + centre.getBlockState(new BlockPos(0, throne, 0)));
    }

    @Test
    void theCalderaIsDrainedAndPouredWithLava(MinecraftServer server) throws Exception {
        VantrayaChunkGenerator gen = generator(server);
        Registry<Biome> biomes = server.registryAccess().registryOrThrow(Registries.BIOME);
        RandomState rs = randomState(server, gen, 777L);
        VantrayaModel model = gen.model(rs);
        // the same noise settings without the Vantraya post-passes: the crater floor (Y~40) is under the sea there
        NoiseBasedChunkGenerator plain = new NoiseBasedChunkGenerator(gen.getBiomeSource(), gen.generatorSettings());

        int plainWater = 0;
        int dryWater = 0;
        int lavaColumns = 0;
        int lava = 0;
        for (int cx = 8; cx <= 24; cx += 4) { // x = 128..400 on the crater floor, z = 0..15
            ProtoChunk with = fill(gen, rs, biomes, cx, 0);
            ProtoChunk without = fill(plain, rs, biomes, cx, 0);
            plainWater += count(without, s -> s.is(Blocks.WATER));
            dryWater += count(with, s -> s.is(Blocks.WATER));
            for (int dz = 0; dz < 16; dz++) {
                for (int dx = 0; dx < 16; dx++) {
                    if (model.sample(cx * 16 + dx + 0.5, dz + 0.5).lava()) {
                        lavaColumns++;
                    }
                }
            }
            CalderaFluids.pourLava(with, model);
            lava += count(with, s -> s.is(Blocks.LAVA));
        }
        assertTrue(plainWater > 0, "control: without the drain the crater holds sea water");
        assertEquals(0, dryWater, "the drained crater holds no open water");
        assertEquals(lavaColumns > 0, lava > 0, "lava is poured exactly where the model's lava mask says (" + lavaColumns + " columns, " + lava + " blocks)");
    }

    @Test
    void steepGroundKeepsNoSoilForTreesToRootIn(MinecraftServer server) throws Exception {
        VantrayaChunkGenerator gen = generator(server);
        Registry<Biome> biomes = server.registryAccess().registryOrThrow(Registries.BIOME);
        RandomState rs = randomState(server, gen, 777L);
        VantrayaModel model = gen.model(rs);
        int steep = 0;
        List<String> soil = new ArrayList<>();
        for (int cx = -3; cx <= 3; cx++) { // around the Glacial Spine's centre (0, -2500)
            for (int cz = -158; cz <= -154; cz++) {
                ProtoChunk chunk = fill(gen, rs, biomes, cx, cz);
                SurfacePainter.paint(chunk, model);
                for (int dz = 0; dz < 16; dz++) {
                    for (int dx = 0; dx < 16; dx++) {
                        int x = cx * 16 + dx;
                        int z = cz * 16 + dz;
                        int g = ground(chunk, x, z);
                        if (g > Spec.SEA_LEVEL && model.slopeDegrees(x + 0.5, z + 0.5) > 45.0) {
                            steep++;
                            if (chunk.getBlockState(new BlockPos(x, g, z)).is(BlockTags.DIRT) && soil.size() < 5) {
                                soil.add(x + "," + g + "," + z + " " + chunk.getBlockState(new BlockPos(x, g, z)));
                            }
                        }
                    }
                }
            }
        }
        assertTrue(steep > 0, "the Spine should have faces steeper than 45 degrees (found " + steep + ")");
        assertTrue(soil.isEmpty(), "soil on a face steeper than 45 degrees: " + soil);
    }

    @Test
    void theAbyssBeyondTheVeilIsDeepSeaWater(MinecraftServer server) throws Exception {
        VantrayaChunkGenerator gen = generator(server);
        Registry<Biome> biomes = server.registryAccess().registryOrThrow(Registries.BIOME);
        RandomState rs = randomState(server, gen, 777L);
        ProtoChunk chunk = fill(gen, rs, biomes, 3750 >> 4, 0);
        int g = ground(chunk, 3750, 0);
        assertTrue(g >= Spec.ABYSS_FLOOR - 2 && g <= Spec.TRENCH_TOP + 2, "abyss floor Y " + g);
        assertTrue(chunk.getBlockState(new BlockPos(3750, 50, 0)).is(Blocks.WATER), "water above the abyss floor");
    }
}
