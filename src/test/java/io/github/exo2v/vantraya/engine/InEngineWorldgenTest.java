package io.github.exo2v.vantraya.engine;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNotSame;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.function.Predicate;

import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.Timeout;
import org.junit.jupiter.api.extension.ExtendWith;

import com.google.gson.JsonElement;
import com.mojang.serialization.DataResult;
import com.mojang.serialization.JsonOps;
import com.mojang.serialization.Lifecycle;

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
import io.github.exo2v.vantraya.mc.WorldTypePriority;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Holder;
import net.minecraft.core.HolderSet;
import net.minecraft.core.MappedRegistry;
import net.minecraft.core.QuartPos;
import net.minecraft.core.RegistrationInfo;
import net.minecraft.core.Registry;
import net.minecraft.core.RegistryAccess;
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
import net.minecraft.world.level.biome.Biomes;
import net.minecraft.world.level.biome.Climate;
import net.minecraft.world.level.biome.FixedBiomeSource;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.chunk.ChunkGenerator;
import net.minecraft.world.level.chunk.ProtoChunk;
import net.minecraft.world.level.chunk.UpgradeData;
import net.minecraft.world.level.dimension.BuiltinDimensionTypes;
import net.minecraft.world.level.dimension.DimensionType;
import net.minecraft.world.level.dimension.LevelStem;
import net.minecraft.world.level.levelgen.Heightmap;
import net.minecraft.world.level.levelgen.NoiseBasedChunkGenerator;
import net.minecraft.world.level.levelgen.NoiseGeneratorSettings;
import net.minecraft.world.level.levelgen.RandomState;
import net.minecraft.world.level.levelgen.WorldDimensions;
import net.minecraft.world.level.levelgen.blending.Blender;
import net.minecraft.world.level.levelgen.presets.WorldPreset;
import net.minecraft.world.level.levelgen.presets.WorldPresets;
import net.minecraft.world.level.levelgen.structure.Structure;
import net.minecraft.world.level.levelgen.structure.StructureStart;
import net.neoforged.neoforge.server.ServerLifecycleHooks;
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
 * <p>The ephemeral server loads data but no levels at all ({@code server.overworld()} is null), so nothing here
 * touches a world. What this class cannot reach is a player in a world: the vanilla surface rule pass
 * ({@code buildSurface} needs a {@code WorldGenRegion}), feature decoration, structures, spawning.
 */
@ExtendWith(EphemeralTestServerProvider.class)
@Timeout(value = 4, unit = TimeUnit.MINUTES) // a safety net per test; start-up has its own, shorter deadline below
class InEngineWorldgenTest {

    private static final int MIN_Y = -64;
    private static final int HEIGHT = 384;
    private static final LevelHeightAccessor LEVEL = LevelHeightAccessor.create(MIN_Y, HEIGHT);

    // ---- starting the server -------------------------------------------------------------------------

    private static final int START_DEADLINE_SECONDS = 150;

    /**
     * The provider marks the server as started when its thread completes a first tick, and polls for that
     * forever: if anything throws while the server thread is starting - a mod's ServerStarting or ServerStarted
     * listener, say - the thread ends, nothing is reported, and the test thread sleeps for good. So start it on a
     * helper thread with a deadline, and say what became of the server thread when the deadline passes.
     */
    @BeforeAll
    static void theEphemeralServerComesUp() throws Exception {
        ExecutorService starter = Executors.newSingleThreadExecutor(r -> {
            Thread t = new Thread(r, "vantraya-test-server-starter");
            t.setDaemon(true);
            return t;
        });
        try {
            Callable<MinecraftServer> start = EphemeralTestServerProvider::grabServer;
            starter.submit(start).get(START_DEADLINE_SECONDS, TimeUnit.SECONDS);
        } catch (TimeoutException e) {
            throw new AssertionError(describeStalledStart(), e);
        } catch (ExecutionException e) {
            throw new AssertionError("starting the ephemeral server threw " + e.getCause(), e.getCause());
        } finally {
            starter.shutdownNow();
        }
    }

    private static String describeStalledStart() {
        StringBuilder sb = new StringBuilder("the ephemeral server had not completed a tick after ")
                .append(START_DEADLINE_SECONDS).append(" s.");
        Thread server = null;
        for (Thread t : Thread.getAllStackTraces().keySet()) {
            if (t.getName().equals("Server thread")) {
                server = t;
            }
        }
        if (server == null) {
            sb.append(" There is no 'Server thread': it ended while starting. The log has the reason under")
                    .append(" 'Encountered an unexpected exception' - usually a mod's listener for a server lifecycle event threw.");
        } else {
            sb.append(" 'Server thread' is ").append(server.getState()).append(", at:");
            StackTraceElement[] frames = server.getStackTrace();
            for (int i = 0; i < Math.min(frames.length, 25); i++) {
                sb.append("\n    at ").append(frames[i]);
            }
        }
        return sb.append("\nServerLifecycleHooks.getCurrentServer() is ")
                .append(ServerLifecycleHooks.getCurrentServer() == null ? "null" : "set").toString();
    }

    // ---- helpers ----------------------------------------------------------------------------------

    private static WorldPreset preset(MinecraftServer server) {
        Registry<WorldPreset> presets = server.registryAccess().registryOrThrow(Registries.WORLD_PRESET);
        return presets.getHolderOrThrow(ResourceKey.create(Registries.WORLD_PRESET, VantrayaBuilder.id("vantraya"))).value();
    }

    private static Map<ResourceKey<LevelStem>, LevelStem> dimensions(MinecraftServer server) {
        return preset(server).createWorldDimensions().dimensions();
    }

    private static LevelStem overworld(MinecraftServer server) {
        LevelStem stem = dimensions(server).get(LevelStem.OVERWORLD);
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
                return gen.terrainTopY(x, z, LEVEL, rs);
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

    /** The Y of the top solid block: {@code ChunkAccess.getHeight} already returns that (the heightmap's first free Y, minus one). */
    private static int ground(ProtoChunk chunk, int x, int z) {
        return chunk.getHeight(Heightmap.Types.OCEAN_FLOOR_WG, x & 15, z & 15);
    }

    /**
     * The terrain's top Y near a column of the chunk: the highest top block among the column and the columns three
     * blocks around it that lie inside the chunk. Cave mouths only remove ground, so this sees through one that
     * happens to open exactly on the column.
     */
    private static int groundNear(ProtoChunk chunk, int x, int z) {
        int best = ground(chunk, x, z);
        for (int[] d : new int[][] {{3, 0}, {-3, 0}, {0, 3}, {0, -3}, {2, 2}, {2, -2}, {-2, 2}, {-2, -2}}) {
            int lx = (x & 15) + d[0];
            int lz = (z & 15) + d[1];
            if (lx >= 0 && lx < 16 && lz >= 0 && lz < 16) {
                best = Math.max(best, ground(chunk, x + d[0], z + d[1]));
            }
        }
        return best;
    }

    /** Water lying open above the top solid block of a column: the sea or a lake on the ground. Not aquifers underground. */
    private static int openWater(ProtoChunk chunk, int x, int z) {
        int n = 0;
        BlockPos.MutableBlockPos pos = new BlockPos.MutableBlockPos();
        for (int y = ground(chunk, x, z) + 1; y < MIN_Y + HEIGHT; y++) {
            if (chunk.getBlockState(pos.set(x, y, z)).is(Blocks.WATER)) {
                n++;
            }
        }
        return n;
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

    @Test
    void everySurfaceMaterialIsARealBlock() {
        for (io.github.exo2v.vantraya.core.SurfaceLogic.Mat m : io.github.exo2v.vantraya.core.SurfaceLogic.Mat.values()) {
            ResourceLocation id = ResourceLocation.withDefaultNamespace(m.path());
            assertTrue(BuiltInRegistries.BLOCK.containsKey(id), "the surface table names a block that does not exist: " + id);
            assertFalse(BuiltInRegistries.BLOCK.get(id).defaultBlockState().isAir(), id + " is air");
        }
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
        assertTrue(dimensions(server).containsKey(LevelStem.NETHER), "the Nether is still there");
        assertTrue(dimensions(server).containsKey(LevelStem.END), "the End is still there");
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

    // ---- a data pack that ships its own overworld ---------------------------------------------------------
    //
    // The first real game showed it: the player picked Vantraya and got the overworld of Lithosphere and Still Life.
    // A data pack's data/minecraft/dimension/overworld.json beats the world type in WorldDimensions.bake, and a mod
    // that replaces the overworld ships exactly that file. WorldTypePriority (through WorldDimensionsMixin) is the fix.

    private static ChunkGenerator somePacksOverworld(MinecraftServer server) {
        RegistryAccess access = server.registryAccess();
        return new NoiseBasedChunkGenerator(
                new FixedBiomeSource(access.registryOrThrow(Registries.BIOME).getHolderOrThrow(Biomes.PLAINS)),
                access.registryOrThrow(Registries.NOISE_SETTINGS).getHolderOrThrow(NoiseGeneratorSettings.OVERWORLD));
    }

    /** What the dimension JSON files of the data packs become: a registry of level stems. */
    private static Registry<LevelStem> dataPackStems(MinecraftServer server, Map<ResourceKey<LevelStem>, ChunkGenerator> generators) {
        Registry<DimensionType> types = server.registryAccess().registryOrThrow(Registries.DIMENSION_TYPE);
        MappedRegistry<LevelStem> stems = new MappedRegistry<>(Registries.LEVEL_STEM, Lifecycle.stable());
        generators.forEach((key, generator) -> {
            ResourceKey<DimensionType> type = key.equals(LevelStem.NETHER) ? BuiltinDimensionTypes.NETHER : BuiltinDimensionTypes.OVERWORLD;
            stems.register(key, new LevelStem(types.getHolderOrThrow(type), generator), RegistrationInfo.BUILT_IN);
        });
        return stems.freeze();
    }

    private static WorldDimensions defaultWorldType(MinecraftServer server) {
        return server.registryAccess().registryOrThrow(Registries.WORLD_PRESET).getHolderOrThrow(WorldPresets.NORMAL).value().createWorldDimensions();
    }

    @Test
    void inVanillaADataPacksOverworldBeatsTheWorldTypeThePlayerPicked(MinecraftServer server) {
        // The control, with no Vantraya in it: the player picks "Default" and a pack ships its own overworld. This is
        // the rule that switched Vantraya off in the first real game.
        ChunkGenerator pack = somePacksOverworld(server);
        WorldDimensions chosen = defaultWorldType(server);
        assertNotSame(pack, chosen.dimensions().get(LevelStem.OVERWORLD).generator(), "test set-up");

        WorldDimensions.Complete baked = chosen.bake(dataPackStems(server, Map.of(LevelStem.OVERWORLD, pack)));

        assertSame(pack, baked.dimensions().get(LevelStem.OVERWORLD).generator(),
                "vanilla: the pack's overworld replaces that of the world type that was picked");
    }

    @Test
    void theVantrayaWorldTypeKeepsItsOverworldWhenADataPackShipsAnother(MinecraftServer server) {
        ChunkGenerator packOverworld = somePacksOverworld(server);
        ChunkGenerator packNether = somePacksOverworld(server);
        WorldDimensions chosen = preset(server).createWorldDimensions();

        WorldDimensions.Complete baked = chosen.bake(
                dataPackStems(server, Map.of(LevelStem.OVERWORLD, packOverworld, LevelStem.NETHER, packNether)));

        LevelStem overworld = baked.dimensions().get(LevelStem.OVERWORLD);
        assertTrue(overworld.generator() instanceof VantrayaChunkGenerator,
                "the overworld is Vantraya's, not " + overworld.generator().getClass().getName()
                        + " - is WorldDimensionsMixin applied in this environment?");
        assertTrue(overworld.generator().getBiomeSource() instanceof VantrayaBiomeSource);
        assertEquals(HEIGHT, overworld.type().value().height(), "and so is its dimension type");
        assertSame(packNether, baked.dimensions().get(LevelStem.NETHER).generator(),
                "only the Vantraya overworld is protected: a pack's Nether still wins, as in vanilla");
        assertNotNull(baked.dimensions().get(LevelStem.END), "the End is still there");
    }

    @Test
    void theGuardHoldsWhenTheWorldIsLoadedAgainFromLevelDat(MinecraftServer server) {
        // Loading a saved world merges the dimensions decoded from level.dat with the data packs' the same way.
        RegistryOps<JsonElement> ops = RegistryOps.create(JsonOps.INSTANCE, server.registryAccess());
        JsonElement saved = LevelStem.CODEC.encodeStart(ops, overworld(server)).result().orElseThrow();
        LevelStem loaded = LevelStem.CODEC.parse(ops, saved).result().orElseThrow();
        WorldDimensions fromLevelDat = new WorldDimensions(Map.of(LevelStem.OVERWORLD, loaded));

        WorldDimensions.Complete baked = fromLevelDat.bake(dataPackStems(server, Map.of(LevelStem.OVERWORLD, somePacksOverworld(server))));

        assertTrue(baked.dimensions().get(LevelStem.OVERWORLD).generator() instanceof VantrayaChunkGenerator,
                "a reloaded Vantraya world must stay a Vantraya world");
    }

    @Test
    void aPackThatConfiguresVantrayaItselfStillWins(MinecraftServer server) {
        VantrayaChunkGenerator base = generator(server);
        VantrayaChunkGenerator configured = new VantrayaChunkGenerator(base.getBiomeSource(), base.generatorSettings());

        WorldDimensions.Complete baked = preset(server).createWorldDimensions()
                .bake(dataPackStems(server, Map.of(LevelStem.OVERWORLD, configured)));

        assertSame(configured, baked.dimensions().get(LevelStem.OVERWORLD).generator(),
                "a data pack that deliberately defines a Vantraya overworld is left to win");
    }

    @Test
    void theGuardLeavesEverythingElseAlone(MinecraftServer server) {
        // Called directly, so this holds even where the mixin is not applied.
        Registry<LevelStem> packs = dataPackStems(server, Map.of(LevelStem.OVERWORLD, somePacksOverworld(server)));
        Registry<LevelStem> noPackOverworld = dataPackStems(server, Map.of(LevelStem.NETHER, somePacksOverworld(server)));

        assertNull(WorldTypePriority.keepChosenOverworld(dimensions(server), packs).get(LevelStem.OVERWORLD),
                "Vantraya chosen: the pack's overworld is left out of the merge");
        assertSame(packs, WorldTypePriority.keepChosenOverworld(defaultWorldType(server).dimensions(), packs),
                "another world type chosen: vanilla's rule, untouched");
        assertSame(noPackOverworld, WorldTypePriority.keepChosenOverworld(dimensions(server), noPackOverworld),
                "no pack overworld: nothing to do");
        assertNotNull(WorldTypePriority.keepChosenOverworld(dimensions(server),
                dataPackStems(server, Map.of(LevelStem.OVERWORLD, somePacksOverworld(server), LevelStem.NETHER, somePacksOverworld(server))))
                .get(LevelStem.NETHER), "the pack's Nether is kept");
    }

    // ---- the terrain on the real density-function engine -----------------------------------------------

    @Test
    void theSpecificationVerifiesOnTheRealEngineForSeveralWorldSeeds(MinecraftServer server) {
        VantrayaChunkGenerator gen = generator(server);
        List<String> problems = new ArrayList<>(); // all seeds, so one run tells everything
        for (long seed : new long[] {0L, 20250929L, -987654321L, 4242L, 1L, 2L, 3L, 31337L}) {
            RandomState rs = randomState(server, gen, seed);
            List<SpecVerifier.Check> checks = SpecVerifier.run(engineProbe(gen, rs));
            assertTrue(checks.size() >= 20, "the verifier ran only " + checks.size() + " checks");
            for (SpecVerifier.Check c : checks) {
                if (!c.pass()) {
                    problems.add("seed " + seed + ": " + c + diagnosis(gen, rs, c));
                }
            }
        }
        assertTrue(problems.isEmpty(), "\n" + String.join("\n", problems));
    }

    /**
     * For a failed landmark check: what the engine and the model each say at the landmark's centre column and the ring
     * around it (engine top Y / model height, the model sampled at the integer column as the density function does).
     */
    private static String diagnosis(VantrayaChunkGenerator gen, RandomState rs, SpecVerifier.Check c) {
        for (Spec.Landmark lm : Spec.LANDMARKS) {
            if (!lm.name().equals(c.landmark()) || !c.what().contains("centre")) {
                continue;
            }
            VantrayaModel model = gen.model(rs);
            VantrayaModel.Fields f = model.sample(lm.x(), lm.z());
            StringBuilder sb = new StringBuilder(String.format(java.util.Locale.ROOT,
                    "\n    centre (%d,%d): model H=%.2f (at +0.5: %.2f) rough3d=%.2f pin=%.2f; engine/model per column:",
                    (int) lm.x(), (int) lm.z(), f.height(), model.height(lm.x() + 0.5, lm.z() + 0.5), f.rough3d(), f.pin()));
            for (int[] d : new int[][] {{0, 0}, {3, 0}, {-3, 0}, {0, 3}, {0, -3}, {2, 2}, {2, -2}, {-2, 2}, {-2, -2}}) {
                int x = (int) lm.x() + d[0];
                int z = (int) lm.z() + d[1];
                sb.append(String.format(java.util.Locale.ROOT, " (%d,%d)=%d/%.1f", d[0], d[1],
                        gen.getBaseHeight(x, z, Heightmap.Types.OCEAN_FLOOR_WG, LEVEL, rs) - 1, model.sample(x, z).height()));
            }
            return sb.toString();
        }
        return "";
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
            int centre = ground(chunk, x, z);
            int viaBase = gen.getBaseHeight(x, z, Heightmap.Types.OCEAN_FLOOR_WG, LEVEL, rs) - 1;
            int ground = groundNear(chunk, x, z); // the terrain, seen through a cave mouth opening on the pin
            double want = lm.kind() == Spec.Kind.CALDERA ? Spec.CALDERA_THRONE : lm.y();
            if (centre != viaBase) {
                problems.add(lm.name() + ": the chunk's centre column ends at " + centre + " but getBaseHeight says " + viaBase);
            }
            if (Math.abs(ground - want) > SpecVerifier.CENTRE_TOLERANCE) {
                problems.add(lm.name() + ": ground Y " + ground + " (centre column " + centre + "), specified " + want);
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

        int maskColumns = 0;
        int plainOpenWater = 0;
        int dryOpenWater = 0;
        int lavaColumns = 0;
        int lavaPoured = 0;
        for (int cz : new int[] {0, -6}) {
            for (int cx = 0; cx <= 24; cx += 4) { // the crater's east radius, x = 0..399
                ProtoChunk with = fill(gen, rs, biomes, cx, cz);
                ProtoChunk without = fill(plain, rs, biomes, cx, cz);
                CalderaFluids.pourLava(with, model);
                for (int dz = 0; dz < 16; dz++) {
                    for (int dx = 0; dx < 16; dx++) {
                        int x = cx * 16 + dx;
                        int z = cz * 16 + dz;
                        VantrayaModel.Fields f = model.sample(x + 0.5, z + 0.5);
                        if (f.noWater()) {
                            maskColumns++;
                            plainOpenWater += openWater(without, x, z);
                            dryOpenWater += openWater(with, x, z);
                        }
                        if (f.lava()) {
                            lavaColumns++;
                            if (with.getBlockState(new BlockPos(x, ground(with, x, z) + 1, z)).is(Blocks.LAVA)) {
                                lavaPoured++;
                            }
                        }
                    }
                }
            }
        }
        assertTrue(maskColumns > 0, "the sampled chunks contain columns of the caldera's no-water mask");
        assertTrue(plainOpenWater > 0, "control: without the drain the crater holds open sea water (" + maskColumns + " columns)");
        assertEquals(0, dryOpenWater, "the drained crater holds no open water (undrained: " + plainOpenWater + " blocks)");
        assertEquals(lavaColumns, lavaPoured, "lava lies on the ground of every column the model's lava mask names");
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
        int g = groundNear(chunk, 3750, 0);
        double pad = SpecVerifier.BAND_PAD * (Spec.TRENCH_TOP - Spec.ABYSS_FLOOR);
        assertTrue(g >= Spec.ABYSS_FLOOR - pad && g <= Spec.TRENCH_TOP + pad, "abyss floor Y " + g);
        assertTrue(chunk.getBlockState(new BlockPos(3750, 50, 0)).is(Blocks.WATER), "water above the abyss floor");
    }
}
