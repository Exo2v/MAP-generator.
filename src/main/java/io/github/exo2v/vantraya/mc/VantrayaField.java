package io.github.exo2v.vantraya.mc;

import com.mojang.serialization.Codec;
import com.mojang.serialization.DataResult;
import com.mojang.serialization.MapCodec;
import com.mojang.serialization.codecs.RecordCodecBuilder;

import io.github.exo2v.vantraya.VantrayaBuilder;
import io.github.exo2v.vantraya.core.Spec;
import io.github.exo2v.vantraya.core.VantrayaModel;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.ResourceKey;
import net.minecraft.util.KeyDispatchDataCodec;
import net.minecraft.world.level.levelgen.DensityFunction;
import net.minecraft.world.level.levelgen.synth.NormalNoise;

/**
 * {@code vantraya_builder:field}: one column-wise channel of the specification model as a density function.
 *
 * <pre>
 * { "type": "vantraya_builder:field", "channel": "height", "seed_noise": "vantraya_builder:seed_probe" }
 * </pre>
 *
 * The noise router wires the channels the way vanilla wires its own terrain splines: {@code continents},
 * {@code erosion}, {@code ridges}, {@code temperature} and {@code humidity} feed biome selection;
 * {@code height} (blocks) and {@code rough3d} (how much 3D noise may blur the surface) shape the terrain
 * through {@code vantraya_builder:depth} and {@code vantraya_builder:sloped_cheese}; {@code protect} (1 within
 * {@link Spec#PROTECT_RADIUS} of a landmark centre, else 0) keeps cave entrances and noodle tunnels off those centres.
 *
 * <p>The function depends only on {@code x} and {@code z}, so it is wrapped in {@code flat_cache} by the
 * data: it is evaluated once per 4 x 4 block quart and Minecraft interpolates between the samples.
 */
public final class VantrayaField implements DensityFunction {

    /** Noise that stands in for the world seed (see {@link WorldSeeds}). */
    public static final ResourceKey<NormalNoise.NoiseParameters> SEED_PROBE =
            ResourceKey.create(Registries.NOISE, VantrayaBuilder.id("seed_probe"));

    public enum Channel {
        CONTINENTS("continents", -1.2, 1.2),
        EROSION("erosion", -1.0, 1.0),
        RIDGES("ridges", -1.0, 1.0),
        TEMPERATURE("temperature", -1.2, 1.2),
        HUMIDITY("humidity", -1.2, 1.2),
        HEIGHT("height", -64.0, 320.0),
        ROUGH3D("rough3d", 0.0, 1.0),
        PROTECT("protect", 0.0, 1.0);

        private final String id;
        private final double min;
        private final double max;

        Channel(String id, double min, double max) {
            this.id = id;
            this.min = min;
            this.max = max;
        }

        public String id() {
            return id;
        }

        public static Channel byId(String id) {
            for (Channel c : values()) {
                if (c.id.equals(id)) {
                    return c;
                }
            }
            return null;
        }

        double read(VantrayaModel.Fields f) {
            switch (this) {
                case CONTINENTS:
                    return f.cont();
                case EROSION:
                    return f.erosion();
                case RIDGES:
                    return f.ridges();
                case TEMPERATURE:
                    return f.temperature();
                case HUMIDITY:
                    return f.humidity();
                case HEIGHT:
                    return f.height();
                case PROTECT:
                    return Spec.protection(f.x(), f.z());
                default:
                    return f.rough3d();
            }
        }
    }

    private static final Codec<Channel> CHANNEL_CODEC = Codec.STRING.comapFlatMap(
            s -> {
                Channel c = Channel.byId(s);
                return c == null
                        ? DataResult.<Channel>error(() -> "Unknown Vantraya channel '" + s + "'")
                        : DataResult.success(c);
            },
            Channel::id);

    public static final MapCodec<VantrayaField> MAP_CODEC = RecordCodecBuilder.mapCodec(instance -> instance.group(
            CHANNEL_CODEC.fieldOf("channel").forGetter(f -> f.channel),
            DensityFunction.NoiseHolder.CODEC.fieldOf("seed_noise").forGetter(f -> f.seedNoise)
    ).apply(instance, VantrayaField::new));

    public static final KeyDispatchDataCodec<VantrayaField> CODEC = KeyDispatchDataCodec.of(MAP_CODEC);

    private final Channel channel;
    private final DensityFunction.NoiseHolder seedNoise;
    private volatile VantrayaModel model;

    public VantrayaField(Channel channel, DensityFunction.NoiseHolder seedNoise) {
        this.channel = channel;
        this.seedNoise = seedNoise;
    }

    private VantrayaModel model() {
        VantrayaModel m = this.model;
        if (m == null) {
            m = WorldSeeds.modelFor(seedNoise::getValue);
            this.model = m;
        }
        return m;
    }

    @Override
    public double compute(DensityFunction.FunctionContext context) {
        return channel.read(model().sample(context.blockX(), context.blockZ()));
    }

    @Override
    public void fillArray(double[] array, DensityFunction.ContextProvider contextProvider) {
        contextProvider.fillAllDirectly(array, this);
    }

    @Override
    public DensityFunction mapAll(DensityFunction.Visitor visitor) {
        return visitor.apply(new VantrayaField(channel, visitor.visitNoise(seedNoise)));
    }

    @Override
    public double minValue() {
        return channel.min;
    }

    @Override
    public double maxValue() {
        return channel.max;
    }

    @Override
    public KeyDispatchDataCodec<? extends DensityFunction> codec() {
        return CODEC;
    }
}
