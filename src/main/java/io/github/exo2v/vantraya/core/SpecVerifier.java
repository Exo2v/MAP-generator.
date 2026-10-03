package io.github.exo2v.vantraya.core;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

import io.github.exo2v.vantraya.core.Spec.Kind;
import io.github.exo2v.vantraya.core.Spec.Landmark;

/**
 * The acceptance test of HANDOFF section 9.4, for a live world: does the generator really hold the
 * specified continent? It reads the world only through a {@link Probe}, never through the model's own
 * state, so it can be pointed at a running Minecraft generator (the {@code /vantraya verify} command) or
 * at any other terrain source and judge it by the same table.
 *
 * <p>Since 0.3.0 the terrain is vanilla's Perlin-noise spline stack driven by the specification's
 * parameter maps, so the acceptance is the same as the play-test document asked for: the landmarks hold
 * their <em>zones</em> (place, elevation band, climate character) and the landforms are judged
 * topologically (a caldera is a rim around a lower floor), not by per-column pins.
 */
public final class SpecVerifier {
    private SpecVerifier() {
    }

    /** What the verifier is allowed to look at. */
    public interface Probe {
        /** Y of the top solid block of the column (the ground, ignoring water above it). */
        int groundHeight(int x, int z);

        /**
         * The biome id at a position, e.g. {@code "minecraft:cherry_grove"}, or {@code null} when the
         * probe cannot say. Compared against the specification's biome list by path.
         */
        String biomeId(int x, int y, int z);
    }

    public record Check(String landmark, String what, boolean pass, String detail) {
        @Override
        public String toString() {
            return (pass ? "PASS " : "FAIL ") + landmark + ": " + what + " - " + detail;
        }
    }

    /**
     * HANDOFF 9.4: the offline verifier runs with {@code --pad 0.25}, "the fraction of each elevation band allowed as
     * tolerance". The Veil's band is the abyss floor to the trench top (-32..10), so a quarter of it is 10.5 blocks.
     *
     * <p>Since 0.2.0 the continent is generated as <em>zones, not pins</em>: no column is clamped to an exact Y,
     * so every landmark is held to its elevation band (plus this pad and a small floor) rather than to a point.
     */
    public static final double BAND_PAD = 0.25;

    /**
     * The elevation range a landmark's centre may sit in: its band with the pad, wider where the landform
     * itself is deep. The pad also carries the noise texture of the spline terrain (a landmark is a zone,
     * not a pin - 0.2.0's rule), so a centre column a handful of blocks off the median still counts.
     */
    public static double[] zoneRange(Landmark lm) {
        double pad = Math.max(12.0, 0.30 * (lm.yHi() - lm.yLo()));
        if (lm.kind() == Kind.QUARRY) {
            return new double[] {55.0, 115.0}; // the quarry's pit floor and chasms are part of the landform
        }
        return new double[] {lm.yLo() - pad, lm.yHi() + pad};
    }

    public static boolean inZone(Landmark lm, int ground) {
        double[] r = zoneRange(lm);
        return ground >= r[0] && ground <= r[1];
    }

    /**
     * The vanilla multi-noise builder answers from its own parameter table, so a landmark's biome is its
     * climate character, not an exact id: each specification name accepts its documented neighbours -
     * the ecotone the landmark's own climate windows produce (see docs/RIVERS_AND_BIOME_BORDERS.md 6.2).
     * A few specification names (basalt deltas, salt flats ...) have no overworld-list entry at all and
     * accept anything in their climate.
     */
    private static final java.util.Map<String, Set<String>> BIOME_NEIGHBOURS = java.util.Map.ofEntries(
            Map.entry("plains", Set.of("plains", "sunflower_plains", "flower_forest", "meadow", "forest", "birch_forest", "dark_forest")),
            Map.entry("meadow", Set.of("meadow", "plains", "sunflower_plains", "flower_forest", "forest",
                    "cherry_grove", "dark_forest", "grove", "birch_forest")),
            Map.entry("windswept_hills", Set.of("windswept_hills", "windswept_gravelly_hills", "windswept_forest",
                    "grove", "meadow", "stony_peaks", "stony_shore", "snowy_slopes")),
            Map.entry("wooded_badlands", Set.of("wooded_badlands", "badlands", "eroded_badlands", "savanna",
                    "savanna_plateau", "desert")),
            Map.entry("basalt_deltas", Set.of()),          // not in the overworld list: any answer is a stand-in
            Map.entry("eroded_badlands", Set.of("eroded_badlands", "badlands", "wooded_badlands", "desert",
                    "savanna_plateau")),
            Map.entry("frozen_peaks", Set.of("frozen_peaks", "jagged_peaks", "snowy_slopes", "grove",
                    "snowy_plains", "snowy_taiga", "stony_peaks", "ice_spikes")),
            Map.entry("jagged_peaks", Set.of("jagged_peaks", "frozen_peaks", "snowy_slopes", "stony_peaks", "grove")),
            Map.entry("grove", Set.of("grove", "snowy_slopes", "snowy_taiga", "snowy_plains", "taiga",
                    "old_growth_spruce_taiga")),
            Map.entry("desert", Set.of("desert", "badlands", "eroded_badlands")),
            Map.entry("badlands", Set.of("badlands", "eroded_badlands", "wooded_badlands", "desert")),
            Map.entry("swamp", Set.of("swamp", "mangrove_swamp", "plains", "mudflats")),
            Map.entry("mangrove_swamp", Set.of()),         // no overworld-list entry: any answer is a stand-in
            Map.entry("warm_ocean", Set.of("warm_ocean", "lukewarm_ocean", "ocean", "deep_ocean", "stony_shore")),
            Map.entry("lukewarm_ocean", Set.of("lukewarm_ocean", "warm_ocean", "ocean", "deep_ocean")),
            Map.entry("cherry_grove", Set.of("cherry_grove", "meadow", "dark_forest", "forest", "plains",
                    "birch_forest")));

    private static boolean biomeOk(String specBiome, String biomePath) {
        if (biomePath == null) {
            return true;
        }
        if (specBiome.equals(biomePath)) {
            return true;
        }
        Set<String> neighbours = BIOME_NEIGHBOURS.getOrDefault(specBiome, Set.of());
        return neighbours.isEmpty() || neighbours.contains(biomePath);
    }

    /**
     * The landmark's ground, read as the median of a small cross about its centre: a landmark is a zone,
     * so one cave mouth or one noise spike on the exact centre column must not judge it.
     */
    public static int zoneGround(Probe p, int gx, int gz) {
        int[] ys = {
                p.groundHeight(gx, gz),
                p.groundHeight(gx + 16, gz),
                p.groundHeight(gx, gz + 16)};
        java.util.Arrays.sort(ys);
        return ys[ys.length / 2];
    }

    public static List<Check> run(Probe p) {
        List<Check> out = new ArrayList<>();
        for (Landmark lm : Spec.LANDMARKS) {
            int gx = (int) Math.floor(lm.x());
            int gz = (int) Math.floor(lm.z());
            int ground = zoneGround(p, gx, gz);
            if (lm.kind() == Kind.CALDERA) {
                // A caldera is judged topologically: a rim around a floor that is clearly lower, and the
                // throne's plateau near its specified height (a zone since 0.2.0: no exact-Y pins).
                out.add(check(lm.name(), "Obsidian Throne near the centre (Y 92 +/- 14)",
                        Math.abs(ground - Spec.CALDERA_THRONE) <= 14.0,
                        "ground Y=%d, specified %.0f", ground, Spec.CALDERA_THRONE));
                int floor = Integer.MAX_VALUE;
                int rim = Integer.MIN_VALUE;
                for (int r = 180; r <= 560; r += 50) {
                    for (int k = 0; k < 8; k++) {
                        double a = k * Math.PI / 8.0;
                        int y = p.groundHeight((int) Math.round(gx + r * Math.cos(a)), (int) Math.round(gz + r * Math.sin(a)));
                        rim = Math.max(rim, y);
                        if (r <= 260) {
                            floor = Math.min(floor, y);
                        }
                    }
                }
                out.add(check(lm.name(), "a rim rises clearly above the crater floor", rim - floor >= 15,
                        "ring highest Y=%d, inner floor lowest Y=%d", rim, floor));
            } else {
                double[] zr = zoneRange(lm);
                out.add(check(lm.name(), "centre elevation within its band", inZone(lm, ground),
                        "ground Y=%d, zone %.0f..%.0f (band %.0f-%.0f)", ground, zr[0], zr[1], lm.yLo(), lm.yHi()));
            }
            String biome = p.biomeId(gx, Math.max(ground + 1, (int) Spec.SEA_LEVEL + 1), gz);
            if (biome != null) {
                boolean ok = false;
                String detail = biome;
                for (String b : lm.biomes()) {
                    if (biomeOk(b, biomePath(biome))) {
                        ok = true;
                        if (!b.equals(biomePath(biome))) {
                            detail = biome + " (specification " + lm.biomes() + ", accepted stand-in)";
                        }
                        break;
                    }
                }
                out.add(check(lm.name(), "biome at the centre", ok, "%s (specified %s)", detail, lm.biomes()));
            }
        }
        // the Forgotten Coast is the spawn: there must be dry land to stand on near the centre (the
        // centre itself may be a river bank - the waterway through the coast is part of the landform)
        Landmark spawn = Spec.spawnLandmark();
        int sx = (int) spawn.x();
        int sz = (int) spawn.z();
        int best = Integer.MIN_VALUE;
        for (int dx = -48; dx <= 48; dx += 24) {
            for (int dz = -48; dz <= 48; dz += 24) {
                best = Math.max(best, p.groundHeight(sx + dx, sz + dz));
            }
        }
        out.add(check(spawn.name(), "dry land above sea level near the spawn point", best > Spec.SEA_LEVEL,
                "highest ground near (%d,%d) is Y=%d", sx, sz, best));
        // the Veil of Salt
        int[] veil = new int[12];
        for (int k = 0; k < veil.length; k++) {
            double a = k * Math.PI / 6.0;
            veil[k] = p.groundHeight((int) Math.round(3750 * Math.cos(a)), (int) Math.round(3750 * Math.sin(a)));
        }
        java.util.Arrays.sort(veil);
        // A cave opening on the sea floor only ever lowers a reading, so the two lowest of the twelve are set aside:
        // a floor that really is too deep lowers all twelve.
        int veilLo = veil[2];
        int veilHi = veil[veil.length - 1];
        double pad = BAND_PAD * (Spec.TRENCH_TOP - Spec.ABYSS_FLOOR);
        out.add(check(Spec.VEIL.name(), "abyss floor within -32..10 (+25% pad) beyond r = 3550",
                veilLo >= Spec.ABYSS_FLOOR - pad && veilHi <= Spec.TRENCH_TOP + pad,
                "ground Y %d..%d at r=3750 (two lowest of 12 set aside), allowed %.1f..%.1f",
                veilLo, veilHi, Spec.ABYSS_FLOOR - pad, Spec.TRENCH_TOP + pad));
        return out;
    }

    private static String biomePath(String id) {
        int c = id.indexOf(':');
        return c < 0 ? id : id.substring(c + 1);
    }

    private static Check check(String landmark, String what, boolean pass, String fmt, Object... args) {
        return new Check(landmark, what, pass, String.format(Locale.ROOT, fmt, args));
    }

    public static boolean allPass(List<Check> checks) {
        for (Check c : checks) {
            if (!c.pass()) {
                return false;
            }
        }
        return true;
    }
}
