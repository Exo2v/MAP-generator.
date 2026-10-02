package io.github.exo2v.vantraya.mc;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Optional;
import java.util.stream.Stream;

import com.mojang.serialization.MapCodec;
import com.mojang.serialization.codecs.RecordCodecBuilder;

import io.github.exo2v.vantraya.VantrayaBuilder;
import io.github.exo2v.vantraya.core.BiomeLogic;
import io.github.exo2v.vantraya.core.BiomeRole;
import io.github.exo2v.vantraya.core.Noise;
import net.minecraft.core.Holder;
import net.minecraft.core.HolderGetter;
import net.minecraft.core.HolderSet;
import net.minecraft.core.QuartPos;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.RegistryOps;
import net.minecraft.resources.ResourceKey;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.tags.TagKey;
import net.minecraft.world.level.biome.Biome;
import net.minecraft.world.level.biome.BiomeSource;
import net.minecraft.world.level.biome.Climate;

/**
 * Places biomes from the specification: every landmark has its named biomes ("spec landmarks override the
 * classifier outright"), everywhere else a Whittaker classifier decides from temperature, humidity and
 * elevation. See {@link BiomeLogic}.
 *
 * <p>The decision uses only what the noise router hands a biome source - the climate parameters, which the
 * {@code vantraya_builder:field} density functions produce from the specification model, and the column's
 * {@code depth}, from which the surface height is recovered - so it needs no seed of its own.
 *
 * <h2>Working alongside other biome mods</h2>
 * Every {@link BiomeRole} resolves through the biome tag {@code #vantraya_builder:biome/<role>}. Out of the
 * box a tag holds only the vanilla default (so a Vantraya world uses vanilla biomes, like the exported
 * Ashenfall world). A datapack or compatibility pack may <em>add</em> biomes to a tag - for example
 * Still Life's - and they are spread, in patches of about 320 blocks, over exactly the places that role
 * covers; the specification's landmarks and climate tiers keep deciding <em>where</em>, the added biomes
 * decide <em>what flavour</em>. Entries may be marked {@code "required": false} so a pack works with or
 * without the other mod.
 */
public final class VantrayaBiomeSource extends BiomeSource {

    public static final MapCodec<VantrayaBiomeSource> CODEC = RecordCodecBuilder.mapCodec(instance -> instance.group(
            RegistryOps.retrieveGetter(Registries.BIOME)
    ).apply(instance, VantrayaBiomeSource::new));

    /** Size of the patches in which the biomes of one role alternate when a role has several. */
    private static final int PATCH = 320;

    private final HolderGetter<Biome> biomes;
    private volatile List<List<Holder<Biome>>> palettes;

    public VantrayaBiomeSource(HolderGetter<Biome> biomes) {
        this.biomes = biomes;
    }

    public static TagKey<Biome> tagOf(BiomeRole role) {
        return TagKey.create(Registries.BIOME, VantrayaBuilder.id("biome/" + role.id()));
    }

    /** The biomes of every role, resolved from the role tags on first use (tags are bound by then). */
    private List<List<Holder<Biome>>> palettes() {
        List<List<Holder<Biome>>> p = this.palettes;
        if (p == null) {
            p = new ArrayList<>(BiomeRole.values().length);
            for (BiomeRole role : BiomeRole.values()) {
                p.add(resolve(role));
            }
            this.palettes = p;
        }
        return p;
    }

    private List<Holder<Biome>> resolve(BiomeRole role) {
        List<Holder<Biome>> out = new ArrayList<>();
        try {
            Optional<HolderSet.Named<Biome>> tag = biomes.get(tagOf(role));
            if (tag.isPresent()) {
                tag.get().stream()
                        .sorted(Comparator.comparing(h -> h.unwrapKey().map(k -> k.location().toString()).orElse("")))
                        .forEach(out::add);
            }
        } catch (IllegalStateException unbound) {
            out.clear(); // tags not bound yet: use the default below
        }
        if (out.isEmpty()) {
            ResourceKey<Biome> key = ResourceKey.create(Registries.BIOME, ResourceLocation.withDefaultNamespace(role.vanilla()));
            out.add(biomes.getOrThrow(key));
        }
        return List.copyOf(out);
    }

    @Override
    protected MapCodec<? extends BiomeSource> codec() {
        return CODEC;
    }

    @Override
    protected Stream<Holder<Biome>> collectPossibleBiomes() {
        return palettes().stream().flatMap(List::stream);
    }

    @Override
    public Holder<Biome> getNoiseBiome(int quartX, int quartY, int quartZ, Climate.Sampler sampler) {
        Climate.TargetPoint tp = sampler.sample(quartX, quartY, quartZ);
        int x = QuartPos.toBlock(quartX);
        int y = QuartPos.toBlock(quartY);
        int z = QuartPos.toBlock(quartZ);
        BiomeRole role = BiomeLogic.classify(x, y, z,
                Climate.unquantizeCoord(tp.temperature()),
                Climate.unquantizeCoord(tp.humidity()),
                Climate.unquantizeCoord(tp.continentalness()),
                Climate.unquantizeCoord(tp.erosion()),
                Climate.unquantizeCoord(tp.depth()));
        return pick(role, x, z);
    }

    /** The biome of a role at a position: the only one, or one of several alternating in patches. */
    public Holder<Biome> pick(BiomeRole role, int x, int z) {
        List<Holder<Biome>> palette = palettes().get(role.ordinal());
        if (palette.size() == 1) {
            return palette.get(0);
        }
        double r = Noise.hash2(Math.floorDiv(x, PATCH), Math.floorDiv(z, PATCH), 0x5EED0000 + role.ordinal());
        return palette.get(Math.min((int) (r * palette.size()), palette.size() - 1));
    }
}
