package io.github.exo2v.vantraya.core.tools;

import java.awt.image.BufferedImage;
import java.io.File;
import java.io.IOException;
import java.util.EnumMap;
import java.util.Map;

import javax.imageio.ImageIO;

import io.github.exo2v.vantraya.core.BiomeLogic;
import io.github.exo2v.vantraya.core.BiomeRole;
import io.github.exo2v.vantraya.core.Spec;
import io.github.exo2v.vantraya.core.VantrayaModel;

/**
 * Renders the continent from the model as plain PNGs: {@code biome} (biome colours with hill shading),
 * {@code height} (hypsometric tint) and {@code tier} (the five climate tiers).
 *
 * <pre>
 *   java -cp ... io.github.exo2v.vantraya.core.tools.RenderMaps &lt;seed&gt; &lt;blocksPerPixel&gt; &lt;outDir&gt; [prefix]
 * </pre>
 * The images carry no labels; the repository's {@code docs/img} versions were annotated afterwards.
 */
public final class RenderMaps {
    private RenderMaps() {
    }

    private static final Map<BiomeRole, Integer> COLOURS = new EnumMap<>(BiomeRole.class);

    private static void c(BiomeRole r, int red, int green, int blue) {
        COLOURS.put(r, (red << 16) | (green << 8) | blue);
    }

    static {
        // the offline engine's biome palette
        c(BiomeRole.DEEP_OCEAN, 12, 40, 82);
        c(BiomeRole.OCEAN, 24, 68, 128);
        c(BiomeRole.SHALLOW_COAST, 56, 122, 168);
        c(BiomeRole.BEACH, 222, 208, 158);
        c(BiomeRole.STONY_SHORE, 128, 126, 120);
        c(BiomeRole.RIVER, 56, 132, 190);
        c(BiomeRole.LAKE, 48, 118, 176);
        c(BiomeRole.FROZEN_RIVER, 150, 196, 220);
        c(BiomeRole.SNOWY_BEACH, 232, 236, 240);
        c(BiomeRole.SNOWY_PLAINS, 238, 242, 246);
        c(BiomeRole.SNOWY_TAIGA, 156, 176, 168);
        c(BiomeRole.GROVE, 176, 196, 190);
        c(BiomeRole.TAIGA, 94, 122, 88);
        c(BiomeRole.OLD_GROWTH_TAIGA, 72, 100, 76);
        c(BiomeRole.COLD_MOUNTAINS, 168, 176, 180);
        c(BiomeRole.COLD_SHRUBLAND, 150, 158, 130);
        c(BiomeRole.PLAINS, 134, 172, 92);
        c(BiomeRole.MEADOW, 146, 186, 108);
        c(BiomeRole.TEMPERATE_FOREST, 78, 128, 66);
        c(BiomeRole.OLD_GROWTH_TEMPERATE_FOREST, 58, 104, 58);
        c(BiomeRole.TEMPERATE_MOUNTAINS, 140, 142, 134);
        c(BiomeRole.WARM_TEMPERATE_MOUNTAINS, 146, 140, 122);
        c(BiomeRole.SWAMP, 72, 96, 62);
        c(BiomeRole.MANGROVE_SWAMP, 60, 96, 72);
        c(BiomeRole.HUMID_SAVANNA, 168, 178, 96);
        c(BiomeRole.SAVANNA, 186, 176, 96);
        c(BiomeRole.XERIC_SHRUBLAND, 166, 158, 108);
        c(BiomeRole.DESERT, 222, 206, 150);
        c(BiomeRole.ARID_MOUNTAINS, 176, 156, 122);
        c(BiomeRole.BADLANDS_MESA, 176, 108, 72);
        c(BiomeRole.JUNGLE, 48, 122, 56);
        c(BiomeRole.TROPICAL_RAINFOREST, 36, 104, 48);
        c(BiomeRole.SPARSE_JUNGLE, 96, 142, 70);
        c(BiomeRole.HIGHLAND_STEPPE, 162, 168, 128);
        c(BiomeRole.ALPINE_PEAKS, 206, 210, 214);
        c(BiomeRole.GLACIER, 238, 246, 252);
        c(BiomeRole.VOLCANIC_HIGHLAND, 104, 92, 88);
        c(BiomeRole.SALT_FLATS, 232, 230, 220);
        c(BiomeRole.WINDSWEPT_HILLS, 132, 156, 110);
        c(BiomeRole.FERTILE_VALLEY, 118, 164, 86);
        c(BiomeRole.WOODED_BADLANDS, 140, 88, 58);
        c(BiomeRole.ERODED_BADLANDS, 168, 96, 56);
        c(BiomeRole.BADLANDS, 186, 108, 62);
        c(BiomeRole.BASALT_DELTAS, 44, 42, 46);
        c(BiomeRole.FROZEN_PEAKS, 226, 236, 244);
        c(BiomeRole.JAGGED_PEAKS, 198, 210, 220);
        c(BiomeRole.CHERRY_GROVE, 232, 174, 196);
        c(BiomeRole.WARM_OCEAN, 54, 140, 158);
        c(BiomeRole.LUKEWARM_OCEAN, 62, 152, 166);
        c(BiomeRole.DEEP_COLD_OCEAN, 12, 40, 74);
        c(BiomeRole.COLD_OCEAN, 30, 76, 140);
        c(BiomeRole.FROZEN_OCEAN, 150, 196, 220);
        c(BiomeRole.DEEP_LUKEWARM_OCEAN, 14, 52, 92);
        c(BiomeRole.DEEP_FROZEN_OCEAN, 100, 140, 170);
    }

    private static int colour(BiomeRole r) {
        Integer v = COLOURS.get(r);
        return v == null ? 0x808080 : v;
    }

    private static int shade(int rgb, double k) {
        int r = (int) Math.max(0, Math.min(255, ((rgb >> 16) & 255) * k));
        int g = (int) Math.max(0, Math.min(255, ((rgb >> 8) & 255) * k));
        int b = (int) Math.max(0, Math.min(255, (rgb & 255) * k));
        return (r << 16) | (g << 8) | b;
    }

    private static int lerp(int a, int b, double t) {
        int r = (int) (((a >> 16) & 255) * (1 - t) + ((b >> 16) & 255) * t);
        int g = (int) (((a >> 8) & 255) * (1 - t) + ((b >> 8) & 255) * t);
        int bl = (int) ((a & 255) * (1 - t) + (b & 255) * t);
        return (r << 16) | (g << 8) | bl;
    }

    private static int hypsometric(double h) {
        if (h < Spec.SEA_LEVEL) {
            double t = Math.max(0, Math.min(1, (Spec.SEA_LEVEL - h) / 100.0));
            return lerp(0x4aa3d8, 0x08306b, t);
        }
        double t = (h - Spec.SEA_LEVEL) / (Spec.SPINE_HIGH - Spec.SEA_LEVEL);
        if (t < 0.15) {
            return lerp(0x7fb069, 0xc9d27a, t / 0.15);
        }
        if (t < 0.4) {
            return lerp(0xc9d27a, 0xb08850, (t - 0.15) / 0.25);
        }
        if (t < 0.75) {
            return lerp(0xb08850, 0x8a8a8a, (t - 0.4) / 0.35);
        }
        return lerp(0x8a8a8a, 0xffffff, (t - 0.75) / 0.25);
    }

    private static final int[] TIER = {0xe8f3ff, 0x4f9a94, 0x7cb342, 0xf0b429, 0xc4412b};

    public static void main(String[] args) throws IOException {
        long seed = Long.parseLong(args[0]);
        int bpp = Integer.parseInt(args[1]);
        File out = new File(args[2]);
        String prefix = args.length > 3 ? args[3] : "";
        out.mkdirs();
        int n = Spec.CANVAS / bpp;
        VantrayaModel m = VantrayaModel.forSeed(seed);
        BufferedImage biome = new BufferedImage(n, n, BufferedImage.TYPE_INT_RGB);
        BufferedImage height = new BufferedImage(n, n, BufferedImage.TYPE_INT_RGB);
        BufferedImage tier = new BufferedImage(n, n, BufferedImage.TYPE_INT_RGB);
        double[][] hs = new double[n][n];
        for (int j = 0; j < n; j++) {
            for (int i = 0; i < n; i++) {
                hs[j][i] = m.height(-Spec.HALF + (i + 0.5) * bpp, -Spec.HALF + (j + 0.5) * bpp);
            }
        }
        for (int j = 0; j < n; j++) {
            for (int i = 0; i < n; i++) {
                double x = -Spec.HALF + (i + 0.5) * bpp;
                double z = -Spec.HALF + (j + 0.5) * bpp;
                VantrayaModel.Fields f = m.sample(x, z);
                BiomeRole role = BiomeLogic.surfaceRole(x, z, f.height(), f.temperature(), f.humidity(), f.cont(), f.erosion());
                // hill shading: light from the north-west
                double hx = (hs[j][Math.min(i + 1, n - 1)] - hs[j][Math.max(i - 1, 0)]) / (2.0 * bpp);
                double hz = (hs[Math.min(j + 1, n - 1)][i] - hs[Math.max(j - 1, 0)][i]) / (2.0 * bpp);
                double k = 0.80 + 2.2 * (-0.7071 * hx - 0.7071 * hz) * 0.5;
                boolean water = f.height() < Spec.SEA_LEVEL;
                biome.setRGB(i, j, shade(colour(role), water ? 1.0 : Math.max(0.55, Math.min(1.45, k))));
                height.setRGB(i, j, shade(hypsometric(f.height()), water ? 1.0 : Math.max(0.6, Math.min(1.4, k))));
                int tr = Math.min(Spec.climateTier(f.temperature()), 4);
                tier.setRGB(i, j, f.height() < Spec.SEA_LEVEL ? shade(TIER[tr], 0.6) : TIER[tr]);
            }
        }
        ImageIO.write(biome, "png", new File(out, prefix + "biome.png"));
        ImageIO.write(height, "png", new File(out, prefix + "height.png"));
        ImageIO.write(tier, "png", new File(out, prefix + "tier.png"));
        System.out.println("wrote " + n + "x" + n + " maps for seed " + seed + " to " + out);
    }
}
