package io.github.exo2v.vantraya.mc;

import com.mojang.serialization.MapCodec;

import io.github.exo2v.vantraya.VantrayaBuilder;
import net.minecraft.core.registries.Registries;
import net.minecraft.world.level.chunk.ChunkGenerator;
import net.minecraft.world.level.levelgen.DensityFunction;
import net.neoforged.bus.api.IEventBus;
import net.neoforged.neoforge.registries.DeferredRegister;

/** The codecs the Vantraya world type's JSON refers to (the field density function and the generator). */
public final class ModRegistries {
    private ModRegistries() {
    }

    public static final DeferredRegister<MapCodec<? extends DensityFunction>> DENSITY_FUNCTION_TYPES =
            DeferredRegister.create(Registries.DENSITY_FUNCTION_TYPE, VantrayaBuilder.MOD_ID);
    public static final DeferredRegister<MapCodec<? extends ChunkGenerator>> CHUNK_GENERATORS =
            DeferredRegister.create(Registries.CHUNK_GENERATOR, VantrayaBuilder.MOD_ID);
    static {
        DENSITY_FUNCTION_TYPES.register("field", VantrayaField.CODEC::codec);
        CHUNK_GENERATORS.register("vantraya", () -> VantrayaChunkGenerator.CODEC);
    }

    public static void register(IEventBus modEventBus) {
        DENSITY_FUNCTION_TYPES.register(modEventBus);
        CHUNK_GENERATORS.register(modEventBus);
    }
}
