package io.github.exo2v.vantraya.mc;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

import io.github.exo2v.vantraya.VantrayaBuilder;
import io.github.exo2v.vantraya.core.BiomeRole;
import net.minecraft.core.Holder;
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
 * <p>How Vantraya works alongside Lithosphere, Tectonic, Still Life and friends is by construction rather
 * than by code: see {@code docs/COMPATIBILITY.md}. In short, the Vantraya world type owns its own dimension
 * type, noise settings and generator (so nothing that rewrites {@code minecraft:overworld} can clash with
 * it), uses vanilla-namespace biomes that any biome-feature mod already decorates, and exposes one biome
 * tag per role for other mods' biomes to join.
 */
public final class ModCompat {
    private ModCompat() {
    }

    private static final String[][] KNOWN = {
            {"lithostitched", "Lithostitched (worldgen library; its overworld modifiers apply to Vantraya's overworld)"},
            {"tectonic", "Tectonic (replaces minecraft:overworld terrain; Vantraya has its own noise settings)"},
            {"lithosphere", "Lithosphere (replaces minecraft:overworld terrain; Vantraya has its own noise settings)"},
            {"still_life", "Still Life (its biomes can join Vantraya's biome role tags)"},
            {"stilllife", "Still Life (its biomes can join Vantraya's biome role tags)"},
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
        try {
            Registry<Biome> registry = server.registryAccess().registryOrThrow(Registries.BIOME);
            Map<String, Integer> namespaces = new TreeMap<>();
            for (ResourceLocation id : registry.keySet()) {
                namespaces.merge(id.getNamespace(), 1, Integer::sum);
            }
            VantrayaBuilder.LOGGER.info("Vantraya: biome namespaces available: {}", namespaces);
            for (BiomeRole role : BiomeRole.values()) {
                registry.getTag(VantrayaBiomeSource.tagOf(role)).ifPresent(tag -> {
                    List<String> members = new ArrayList<>();
                    for (Holder<Biome> h : tag) {
                        h.unwrapKey().ifPresent(key -> members.add(key.location().toString()));
                    }
                    if (members.size() > 1) {
                        VantrayaBuilder.LOGGER.info("Vantraya: role '{}' spreads {} biomes: {}", role.id(), members.size(), members);
                    }
                });
            }
        } catch (RuntimeException e) {
            VantrayaBuilder.LOGGER.debug("Vantraya: could not read the biome registry for the compatibility report", e);
        }
    }
}
