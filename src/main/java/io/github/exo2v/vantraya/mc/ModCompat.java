package io.github.exo2v.vantraya.mc;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.TreeMap;

import io.github.exo2v.vantraya.VantrayaBuilder;
import net.minecraft.core.Registry;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.MinecraftServer;
import net.minecraft.world.level.biome.Biome;
import net.neoforged.fml.ModList;
import net.neoforged.neoforge.event.server.ServerStartingEvent;

/**
 * Reports, when a server starts, which companion worldgen mods are present and which biomes they have
 * contributed to Vantraya's biome roles - so a pack author can see at a glance what is active.
 *
 * <p>How Vantraya works alongside Lithosphere, Tectonic, Still Life and friends: see
 * {@code docs/COMPATIBILITY.md}. In short, the Vantraya world type owns its own dimension type, noise
 * settings and generator, and runs vanilla's multi-noise biome builder on the specification's parameter
 * maps - so TerraBlender regions, biome-feature mods and structure mods all apply as they would to any
 * vanilla-shaped world. A pack's own {@code minecraft:overworld} <em>dimension</em> is a different matter
 * - vanilla lets it beat the world type - and is handled by {@link WorldTypePriority}
 * (COMPATIBILITY.md section 0).
 */
public final class ModCompat {
    private ModCompat() {
    }

    private static final String[][] KNOWN = {
            {"lithostitched", "Lithostitched (worldgen library; its overworld modifiers apply to Vantraya's overworld)"},
            {"tectonic", "Tectonic (replaces minecraft:overworld terrain; Vantraya has its own noise settings)"},
            {"lithosphere", "Lithosphere (replaces minecraft:overworld terrain; Vantraya has its own noise settings)"},
            {"terrablender", "TerraBlender (its regions apply: Vantraya uses the vanilla multi-noise overworld builder)"},
            {"still_life", "Still Life (biome mod; TerraBlender regions of biome mods apply to Vantraya)"},
            {"stilllife", "Still Life (biome mod; TerraBlender regions of biome mods apply to Vantraya)"},
    };

    public static void onServerStarting(ServerStartingEvent event) {
        if (!VantrayaConfig.logCompatReport()) {
            return;
        }
        MinecraftServer server = event.getServer();
        List<String> found = new ArrayList<>();
        for (String[] k : KNOWN) {
            if (ModList.get().isLoaded(k[0])) {
                found.add(k[1]);
            }
        }
        VantrayaBuilder.LOGGER.info("Vantraya: companion mods detected: {}", found.isEmpty() ? "none" : found);
        // Lithosphere and Still Life are published both as mods and as plain data packs; a data pack has no mod id,
        // so also look at the data packs selected for this world.
        try {
            List<String> packs = new ArrayList<>();
            for (String id : server.getPackRepository().getSelectedIds()) {
                String s = id.toLowerCase(Locale.ROOT);
                if (s.contains("lithosphere") || s.contains("tectonic") || s.contains("lithostitched")
                        || (s.contains("still") && s.contains("life"))) {
                    packs.add(id);
                }
            }
            VantrayaBuilder.LOGGER.info("Vantraya: data packs that look like worldgen companions: {}",
                    packs.isEmpty() ? "none" : packs);
        } catch (RuntimeException e) {
            VantrayaBuilder.LOGGER.debug("Vantraya: could not read the selected data packs", e);
        }
        try {
            Registry<Biome> registry = server.registryAccess().registryOrThrow(Registries.BIOME);
            Map<String, Integer> namespaces = new TreeMap<>();
            for (ResourceLocation id : registry.keySet()) {
                namespaces.merge(id.getNamespace(), 1, Integer::sum);
            }
            VantrayaBuilder.LOGGER.info("Vantraya: biome namespaces available: {}", namespaces);
        } catch (RuntimeException e) {
            VantrayaBuilder.LOGGER.debug("Vantraya: could not read the biome registry for the compatibility report", e);
        }
    }
}
