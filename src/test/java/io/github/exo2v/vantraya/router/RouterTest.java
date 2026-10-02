package io.github.exo2v.vantraya.router;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;

import java.io.InputStream;
import java.net.URL;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.stream.Collectors;
import java.util.stream.Stream;

import org.junit.BeforeClass;
import org.junit.Test;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import io.github.exo2v.vantraya.core.BiomeLogic;
import io.github.exo2v.vantraya.core.BiomeRole;
import io.github.exo2v.vantraya.core.Spec;
import io.github.exo2v.vantraya.core.VantrayaModel;

/**
 * Runs the actual noise-router JSON that ships in the mod against the specification model, with noise at its
 * mean, and checks the properties the world depends on: the terrain surface sits exactly on the model's height
 * field (so the nine landmark pins are exact), the bedrock is solid, the sky is air, and {@code depth} has
 * vanilla's scale so biome sources, aquifers and cave thresholds behave.
 */
public class RouterTest {
    private static JsonObject noiseSettings;
    private static JsonObject router;

    @BeforeClass
    public static void load() {
        noiseSettings = DensityInterpreter.parseResource("/data/vantraya_builder/worldgen/noise_settings/vantraya.json").getAsJsonObject();
        router = noiseSettings.getAsJsonObject("noise_router");
    }

    private static int topSolid(DensityInterpreter in, JsonElement finalDensity, double x, double z) {
        for (int y = 319; y >= -64; y--) {
            if (in.eval(finalDensity, x, y, z) > 0.0) {
                return y;
            }
        }
        return -65;
    }

    @Test
    public void noiseSettingsHaveTheWorldGeometry() {
        assertEquals(63, noiseSettings.get("sea_level").getAsInt()); // water fills up to Y=62: the specified waterline
        JsonObject noise = noiseSettings.getAsJsonObject("noise");
        assertEquals(-64, noise.get("min_y").getAsInt());
        assertEquals(384, noise.get("height").getAsInt());
        assertTrue(noiseSettings.get("aquifers_enabled").getAsBoolean());
        assertTrue(noiseSettings.get("ore_veins_enabled").getAsBoolean());
        assertNotNull(noiseSettings.get("surface_rule"));
        assertNotNull(noiseSettings.get("spawn_target"));
    }

    @Test
    public void everyReferenceInTheRouterResolves() {
        DensityInterpreter in = new DensityInterpreter(VantrayaModel.forSeed(1));
        for (String key : new String[] {"final_density", "initial_density_without_jaggedness", "depth", "continents", "erosion",
                "ridges", "temperature", "vegetation"}) {
            in.resolveAll(router.get(key));
        }
    }

    @Test
    public void depthHasVanillasScaleAndPutsZeroAtTheSurface() {
        for (long seed : new long[] {Spec.SPEC_SEED, 5L}) {
            VantrayaModel m = VantrayaModel.forSeed(seed);
            DensityInterpreter in = new DensityInterpreter(m);
            JsonElement depth = router.get("depth");
            for (int x = -3000; x <= 3000; x += 500) {
                for (int z = -3000; z <= 3000; z += 500) {
                    double h = m.height(x, z);
                    for (int y : new int[] {-60, 0, 62, 100, 250}) {
                        assertEquals((h + 0.5 - y) / 128.0, in.eval(depth, x, y, z), 1e-9);
                    }
                    // one block of height = 1/128 of depth, as in vanilla; the surface block sits at depth ~0
                    assertEquals(0.0, in.eval(depth, x, Math.rint(h), z), 1.0 / 128.0 + 1e-9);
                }
            }
        }
    }

    @Test
    public void theTerrainSurfaceSitsOnTheHeightField() {
        for (long seed : new long[] {Spec.SPEC_SEED, 2024L}) {
            VantrayaModel m = VantrayaModel.forSeed(seed);
            DensityInterpreter in = new DensityInterpreter(m);
            JsonElement fd = router.get("final_density");
            int checked = 0;
            for (int x = -3600; x <= 3600; x += 400) {
                for (int z = -3600; z <= 3600; z += 400) {
                    double h = m.height(x, z);
                    int want = (int) Math.floor(h + 0.5); // ground = round(H)
                    int got = topSolid(in, fd, x, z);
                    assertEquals("seed " + seed + " column " + x + "," + z + " (H=" + h + ")", want, got, 1);
                    checked++;
                }
            }
            assertTrue(checked > 300);
        }
    }

    @Test
    public void everyLandmarkCentreIsExactlyItsSpecifiedElevation() {
        for (long seed : new long[] {Spec.SPEC_SEED, 99L, 31337L}) {
            VantrayaModel m = VantrayaModel.forSeed(seed);
            DensityInterpreter in = new DensityInterpreter(m);
            JsonElement fd = router.get("final_density");
            for (Spec.Landmark lm : Spec.LANDMARKS) {
                double want = lm.kind() == Spec.Kind.CALDERA ? Spec.CALDERA_THRONE : lm.y();
                int got = topSolid(in, fd, lm.x(), lm.z());
                assertEquals(lm.key() + " seed " + seed, want, got, 0.0);
            }
        }
    }

    /**
     * Vanilla's cave entrances and noodle tunnels carve from the surface down; one entrance opened a 17-block pit in the
     * pinned top of the Hermit's Spire (found by the in-engine run). With either forced to its strongest, a landmark
     * centre must still stand at its specified elevation - and, as a control for each, ground away from every centre
     * must be carved, or the test proves nothing.
     */
    @Test
    public void caveEntrancesAndNoodlesCannotOpenTheSurfaceAtALandmarkCentre() {
        VantrayaModel m = VantrayaModel.forSeed(Spec.SPEC_SEED);
        JsonElement fd = router.get("final_density");
        for (String function : new String[] {"minecraft:overworld/caves/entrances", "minecraft:overworld/caves/noodle"}) {
            DensityInterpreter in = new DensityInterpreter(m);
            in.override(function, -100.0);
            for (Spec.Landmark lm : Spec.LANDMARKS) {
                double want = lm.kind() == Spec.Kind.CALDERA ? Spec.CALDERA_THRONE : lm.y();
                assertEquals(function + ": " + lm.key() + " centre", want, topSolid(in, fd, lm.x(), lm.z()), 0.0);
                for (double[] d : new double[][] {{40, 0}, {0, -40}, {-30, 30}}) {
                    double x = lm.x() + d[0];
                    double z = lm.z() + d[1];
                    double ground = Math.floor(m.height(x, z) + 0.5);
                    assertEquals(function + ": " + lm.key() + " at " + x + "," + z, ground, topSolid(in, fd, x, z), 1.0);
                }
            }
            int land = 0;
            int carved = 0;
            for (int x = -3400; x <= 3400; x += 400) {
                for (int z = -3400; z <= 3400; z += 400) {
                    if (Spec.protection(x, z) == 0.0 && m.height(x, z) > Spec.SEA_LEVEL + 5) {
                        land++;
                        if (topSolid(in, fd, x, z) < Math.floor(m.height(x, z) + 0.5) - 10) {
                            carved++;
                        }
                    }
                }
            }
            assertTrue("control for " + function + ": " + land + " land columns away from the centres, " + carved + " carved",
                    land > 30 && carved == land);
        }
    }

    @Test
    public void spineSummitsClearTheOldTerrainCeiling() {
        // vanilla fades terrain out between Y=240 and 256; the Glacial Spine is specified up to 279
        VantrayaModel m = VantrayaModel.forSeed(Spec.SPEC_SEED);
        DensityInterpreter in = new DensityInterpreter(m);
        JsonElement fd = router.get("final_density");
        double best = 0;
        int bestX = 0;
        int bestZ = 0;
        for (int x = -1800; x <= 1800; x += 20) {
            for (int z = -3500; z <= -1500; z += 20) {
                double h = m.height(x, z);
                if (h > best) {
                    best = h;
                    bestX = x;
                    bestZ = z;
                }
            }
        }
        assertTrue("highest spine point " + best, best > 255 && best <= 284);
        assertEquals((int) Math.floor(best + 0.5), topSolid(in, fd, bestX, bestZ), 1);
    }

    @Test
    public void bedrockIsSolidAndTheSkyIsAir() {
        VantrayaModel m = VantrayaModel.forSeed(Spec.SPEC_SEED);
        DensityInterpreter in = new DensityInterpreter(m);
        JsonElement fd = router.get("final_density");
        for (int x = -3000; x <= 3000; x += 600) {
            for (int z = -3000; z <= 3000; z += 600) {
                for (int y = -64; y <= -48; y += 4) {
                    assertTrue("floor must be solid at " + x + "," + y + "," + z, in.eval(fd, x, y, z) > 0.0);
                }
                for (int y = 314; y < 320; y++) {
                    assertTrue("sky must be air at " + x + "," + y + "," + z, in.eval(fd, x, y, z) < 0.0);
                }
            }
        }
    }

    @Test
    public void groundIsSolidWithoutPocketsBelowTheSurface() {
        // with noise at its mean there must be no hollow just under the surface (caves come from the noise)
        VantrayaModel m = VantrayaModel.forSeed(Spec.SPEC_SEED);
        DensityInterpreter in = new DensityInterpreter(m);
        JsonElement fd = router.get("final_density");
        int solid = 0;
        int total = 0;
        for (int x = -3000; x <= 3000; x += 300) {
            for (int z = -3000; z <= 3000; z += 300) {
                int top = (int) Math.floor(m.height(x, z) + 0.5);
                for (int y = top - 1; y >= Math.max(top - 40, -60); y -= 3) {
                    total++;
                    if (in.eval(fd, x, y, z) > 0.0) {
                        solid++;
                    }
                }
            }
        }
        assertTrue("solid share below the surface " + (double) solid / total, (double) solid / total > 0.98);
    }

    @Test
    public void biomeInputsRecoveredFromTheRouterGiveTheSpecifiedBiomes() {
        // what the BiomeSource sees: climate parameters from the field channels, depth from the depth function
        VantrayaModel m = VantrayaModel.forSeed(Spec.SPEC_SEED);
        DensityInterpreter in = new DensityInterpreter(m);
        for (Spec.Landmark lm : Spec.LANDMARKS) {
            int x = (int) lm.x();
            int z = (int) lm.z();
            int y = Math.max((int) lm.y() + 3, 66);
            BiomeRole role = BiomeLogic.classify(x, y, z,
                    in.eval(router.get("temperature"), x, y, z), in.eval(router.get("vegetation"), x, y, z),
                    in.eval(router.get("continents"), x, y, z), in.eval(router.get("erosion"), x, y, z),
                    in.eval(router.get("depth"), x, y, z));
            boolean ok = false;
            for (String b : lm.biomes()) {
                ok |= role.vanilla().equals(b);
            }
            assertTrue(lm.key() + " -> " + role.vanilla(), ok);
        }
    }

    @Test
    public void worldPresetAndTagsAreWellFormed() {
        JsonObject preset = DensityInterpreter.parseResource("/data/vantraya_builder/worldgen/world_preset/vantraya.json").getAsJsonObject();
        JsonObject dims = preset.getAsJsonObject("dimensions");
        assertEquals(3, dims.size());
        JsonObject overworld = dims.getAsJsonObject("minecraft:overworld");
        assertEquals("vantraya_builder:vantraya", overworld.get("type").getAsString());
        JsonObject gen = overworld.getAsJsonObject("generator");
        assertEquals("vantraya_builder:vantraya", gen.get("type").getAsString());
        assertEquals("vantraya_builder:vantraya", gen.get("settings").getAsString());
        assertEquals("vantraya_builder:vantraya", gen.getAsJsonObject("biome_source").get("type").getAsString());
        assertEquals("minecraft:noise", dims.getAsJsonObject("minecraft:the_nether").getAsJsonObject("generator").get("type").getAsString());
        assertEquals("minecraft:the_end", dims.getAsJsonObject("minecraft:the_end").get("type").getAsString());
        // listed on the world creation screen
        JsonObject normal = DensityInterpreter.parseResource("/data/minecraft/tags/worldgen/world_preset/normal.json").getAsJsonObject();
        assertEquals("vantraya_builder:vantraya", normal.getAsJsonArray("values").get(0).getAsString());
        // dimension type and the seed probe noise exist
        JsonObject type = DensityInterpreter.parseResource("/data/vantraya_builder/dimension_type/vantraya.json").getAsJsonObject();
        assertEquals(-64, type.get("min_y").getAsInt());
        assertEquals(384, type.get("height").getAsInt());
        assertNotNull(DensityInterpreter.parseResource("/data/vantraya_builder/worldgen/noise/seed_probe.json"));
    }

    @Test
    public void everyBiomeRoleHasATagHoldingItsVanillaDefault() throws Exception {
        for (BiomeRole role : BiomeRole.values()) {
            String path = "/data/vantraya_builder/tags/worldgen/biome/biome/" + role.id() + ".json";
            try (InputStream in = RouterTest.class.getResourceAsStream(path)) {
                assertNotNull("missing biome role tag " + path, in);
            }
            JsonObject tag = DensityInterpreter.parseResource(path).getAsJsonObject();
            assertEquals(1, tag.getAsJsonArray("values").size());
            assertEquals("minecraft:" + role.vanilla(), tag.getAsJsonArray("values").get(0).getAsString());
        }
    }

    @Test
    public void fieldFunctionJsonUsesTheKeysTheCodecReads() {
        for (String ch : new String[] {"continents", "erosion", "ridges", "temperature", "humidity", "height", "rough3d", "protect"}) {
            JsonObject flat = DensityInterpreter.parseResource("/data/vantraya_builder/worldgen/density_function/field/" + ch + ".json").getAsJsonObject();
            assertEquals("minecraft:flat_cache", flat.get("type").getAsString());
            JsonObject field = flat.getAsJsonObject("argument");
            assertEquals("vantraya_builder:field", field.get("type").getAsString());
            assertEquals(ch, field.get("channel").getAsString());
            assertEquals("vantraya_builder:seed_probe", field.get("seed_noise").getAsString());
        }
    }

    /** The vanilla functions the noise router may still reference: the Y coordinate and the cave set. */
    private static final Set<String> SHARED_WITH_VANILLA = Set.of("minecraft:y",
            "minecraft:overworld/caves/entrances", "minecraft:overworld/caves/noodle",
            "minecraft:overworld/caves/pillars", "minecraft:overworld/caves/spaghetti_2d",
            "minecraft:overworld/caves/spaghetti_roughness_function");

    /** Every string under a key other than "type"/"noise" that points into the minecraft namespace. */
    private static void collectVanillaReferences(JsonElement e, String key, Set<String> out) {
        if (e.isJsonObject()) {
            for (Map.Entry<String, JsonElement> entry : e.getAsJsonObject().entrySet()) {
                collectVanillaReferences(entry.getValue(), entry.getKey(), out);
            }
        } else if (e.isJsonArray()) {
            e.getAsJsonArray().forEach(item -> collectVanillaReferences(item, key, out));
        } else if (e.isJsonPrimitive() && e.getAsJsonPrimitive().isString()) {
            String v = e.getAsString();
            if (v.startsWith("minecraft:") && !"type".equals(key) && !"noise".equals(key)
                    && (v.equals("minecraft:y") || v.contains("/"))) {
                out.add(v);
            }
        }
    }

    /**
     * Where the shipped density functions are on disk. Normally the class path says so; under NeoForge's unit-test
     * launcher the resources are not served from a plain {@code file:} directory, so fall back to the source tree
     * above the working directory. If neither is found the guard fails: a guard that silently skips guards nothing
     * (it did exactly that for one CI run).
     */
    private static Path densityFunctionDirectory() throws Exception {
        URL dir = RouterTest.class.getResource("/data/vantraya_builder/worldgen/density_function");
        if (dir != null && "file".equals(dir.getProtocol())) {
            return Paths.get(dir.toURI());
        }
        for (Path p = Paths.get("").toAbsolutePath(); p != null; p = p.getParent()) {
            Path candidate = p.resolve("src/main/resources/data/vantraya_builder/worldgen/density_function");
            if (Files.isDirectory(candidate)) {
                return candidate;
            }
        }
        throw new AssertionError("cannot find the shipped density functions (class path says " + dir
                + ", working directory " + Paths.get("").toAbsolutePath() + ")");
    }

    /**
     * Terrain overhauls (Lithosphere, Tectonic, ...) override vanilla density functions. The shape of this world's
     * terrain must not depend on any function they might replace - that would move the landmark pins - so the
     * terrain's own functions reference nothing in the minecraft namespace but the Y coordinate, and the router
     * itself only the cave functions (which a cave overhaul is welcome to change).
     */
    @Test
    public void terrainDependsOnNoVanillaFunctionAnotherPackCouldOverride() throws Exception {
        Path root = densityFunctionDirectory();
        List<Path> files;
        try (Stream<Path> walk = Files.walk(root)) {
            files = walk.filter(f -> f.toString().endsWith(".json")).collect(Collectors.toList());
        }
        assertTrue("the terrain functions are shipped", files.size() >= 10);
        for (Path f : files) {
            String rel = root.relativize(f).toString().replace('\\', '/');
            Set<String> refs = new TreeSet<>();
            collectVanillaReferences(DensityInterpreter.parseResource("/data/vantraya_builder/worldgen/density_function/" + rel), "", refs);
            refs.remove("minecraft:y");
            assertTrue(rel + " must not reference vanilla density functions, found " + refs, refs.isEmpty());
        }
        Set<String> routerRefs = new TreeSet<>();
        collectVanillaReferences(router, "", routerRefs);
        for (String ref : routerRefs) {
            assertTrue("the noise router references a vanilla function that is not a cave function: " + ref,
                    SHARED_WITH_VANILLA.contains(ref));
        }
        assertTrue("the caves stay shared with vanilla", routerRefs.contains("minecraft:overworld/caves/noodle"));
    }
}
