package io.github.exo2v.vantraya;

import org.slf4j.Logger;

import com.mojang.logging.LogUtils;

import io.github.exo2v.vantraya.mc.ModCompat;
import io.github.exo2v.vantraya.mc.ModRegistries;
import io.github.exo2v.vantraya.mc.SpawnAndBorderHandler;
import io.github.exo2v.vantraya.mc.VantrayaCommand;
import io.github.exo2v.vantraya.mc.VantrayaConfig;
import net.minecraft.resources.ResourceLocation;
import net.neoforged.bus.api.IEventBus;
import net.neoforged.fml.ModContainer;
import net.neoforged.fml.common.Mod;
import net.neoforged.fml.config.ModConfig;
import net.neoforged.neoforge.common.NeoForge;

/**
 * Vantraya Builder: the Ashenfall specification, built live.
 *
 * <p>Nothing is exported. Choosing the <em>Vantraya</em> world type on the world creation screen creates a
 * normal Overworld whose terrain, biomes and surface are generated chunk by chunk from the specification
 * tables (see {@code docs/DESIGN.md}). The world type is plain data ({@code worldgen/world_preset}); this
 * class only registers the building blocks it refers to - a density function, a biome source and a chunk
 * generator - and the small amount of server glue (spawn, optional world border, commands).
 */
@Mod(VantrayaBuilder.MOD_ID)
public final class VantrayaBuilder {
    public static final String MOD_ID = "vantraya_builder";
    public static final Logger LOGGER = LogUtils.getLogger();

    public VantrayaBuilder(IEventBus modEventBus, ModContainer modContainer) {
        ModRegistries.register(modEventBus);
        modContainer.registerConfig(ModConfig.Type.COMMON, VantrayaConfig.SPEC);

        NeoForge.EVENT_BUS.addListener(SpawnAndBorderHandler::onCreateSpawnPosition);
        NeoForge.EVENT_BUS.addListener(SpawnAndBorderHandler::onServerStarted);
        NeoForge.EVENT_BUS.addListener(VantrayaCommand::onRegisterCommands);
        NeoForge.EVENT_BUS.addListener(ModCompat::onServerStarting);
    }

    public static ResourceLocation id(String path) {
        return ResourceLocation.fromNamespaceAndPath(MOD_ID, path);
    }
}
